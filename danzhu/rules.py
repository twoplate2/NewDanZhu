"""盘面配平: 每盘掷 k 个奖格填倍率, 坏盘重抽, x2 权重闭式反解。

⚠️ 本模块**只有盘面配平**。一局的经济/状态机(余额/投注/轮次/切档守卫/存档/事件位→音效)
   是另一块, 不在这里 —— `roll_multipliers` 之外没有 `Game`, 那不是漏项。

RTP = 盘面数学期望, 而坏盘重抽会改变它(保底超发 / 封顶少发), 所以 x2 的权重
**不能写死** —— 它由 `_solve_p2` 反解出来, 把重抽的影响一并配平。
改 VALUE_SHAPE / K_DIST / MAX_REROLL / CEIL_THRESHOLD 任何一个数, x2 权重自动跟上;
换成手写权重表就丢了这条性质。

纯 stdlib, 无 Kivy / tkinter。
"""

import colorsys
import math
import random

from .config import BTN_LINE_DOWN, BTN_LINE_UP, NUM_SLOTS, hex_rgb


def _pick(dist):
    """按 {取值: 概率} 的累计阈值掷一个取值(概率和须=1)。"""
    r = random.random()
    acc = 0.0
    for value, prob in sorted(dist.items()):
        acc += prob
        if r < acc:
            return value
    return max(dist)          # 浮点误差兜底: 落在最后一段


MAX_REROLL = 2            # 坏盘最多重抽次数(共生成 MAX_REROLL+1 盘, 最后一盘无论好坏都收)
CEIL_THRESHOLD = 5        # 高倍率起点: >=x5 即 x5/x10/x20/x50/x100 都算"高"

# 非 x2 部分的形状(**条件**分布)。低档无 x50/x100, 高档含。
# ⚠️ 这里写的是**权重**不是概率 —— 和不必为 1, 归一化交给 `_shape_of`。
#    (隐藏档的权重是玩家直接给的整数, 凑成小数会被顶层 assert 卡在小数点后几位上。)
VALUE_SHAPE = {
    0.80: {3: 0.556, 5: 0.289, 10: 0.122, 20: 0.033},
    1.20: {3: 0.556, 5: 0.289, 10: 0.122, 20: 0.033},
    2.00: {3: 0.50, 5: 0.27, 10: 0.12, 20: 0.06, 50: 0.033, 100: 0.017},
    3.60: {3: 0.50, 5: 0.27, 10: 0.12, 20: 0.06, 50: 0.033, 100: 0.017},
    10.0: {5: 17, 10: 12, 20: 8, 50: 5, 100: 3},
    20.0: {5: 10, 10: 20, 20: 32, 50: 26, 100: 12},
    # ⚠️ 5000% **故意不放 x5/x10**: 每格均值被固定成 50, 掺小倍率反而要更多 x2 去平衡.
    50.0: {20: 20, 50: 40, 100: 40},
}

# 每盘有奖格数。三个隐藏档 9 格全有奖 = 必中(玩家定稿「都没有空军的」);
# 空军是**这张表**管的, 不是 RTP 管的。
K_DIST = {
    0.80: {2: 0.8507, 3: 0.1493},
    1.20: {3: 0.7761, 4: 0.2239},
    2.00: {3: 0.25, 4: 0.50, 5: 0.25},
    3.60: {6: 0.10, 7: 0.70, 8: 0.20},
    10.0: {9: 1.0},
    20.0: {9: 1.0},
    50.0: {9: 1.0},
}


def _shape_of(rtp):
    """取**归一化后**的形状 —— 表里允许写整数权重(和不必为 1)。"""
    sh = VALUE_SHAPE[rtp]
    tot = float(sum(sh.values()))
    return {v: w / tot for v, w in sh.items()} if tot else sh


def _line_color(fill_hex):
    """「重置」的描边色 = 从它自己的底色推出来: 同色相, 暗底提亮 / 亮底压暗。

    ⚠️ 必须分两路: 只提亮的话亮底会变成白边, 跟底色混成浑灰。
    """
    _r, _g, _b = hex_rgb(fill_hex)
    _h, _sat, _v = colorsys.rgb_to_hsv(_r, _g, _b)
    _v2 = _v * (BTN_LINE_UP if _v < 0.5 else BTN_LINE_DOWN)
    _o = colorsys.hsv_to_rgb(_h, _sat, min(1.0, max(0.0, _v2)))
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, x)) * 255)) for x in _o)


# ⚠️ `_line_color` 是**唯一没有任何对账段覆盖**的函数(纯 UI 色, 物理/账务/音效段都碰不到它),
#    而它依赖 config 的 `hex_rgb` / `BTN_LINE_UP` / `BTN_LINE_DOWN` —— 改这三个数, 现有对账
#    一声不吭。所以把老版实测输出钉在这里, 动一个当场炸。
assert _line_color("#26324f") == "#42588a", "描边色漂移: 查 hex_rgb / BTN_LINE_UP(暗底提亮支)"
assert _line_color("#3563d1") == "#1b3168", "描边色漂移: 查 hex_rgb / BTN_LINE_DOWN(亮底压暗支)"


def _is_bad_board(k, n2, nh):
    """坏盘判据 —— `_effective_rtp`(解析期望) 与 `roll_multipliers`(实际重抽) **共用这一个**。

    坏盘 = x2 占比 >= 80%(几乎全 x2) 或 高倍率(>=x5) >= 2 个。
    ⚠️ 两处各写一遍的互补写法会让解析值和实测值分家, 而 RTP 正是靠两者对齐才成立的。
    ⚠️ 阈值 0.8 只以整数形式 `* 4 / 5` 出现(不引入浮点比较)。
    """
    return n2 * 5 >= k * 4 or nh >= 2


def _effective_rtp(p2, rtp):
    """给定 x2 权重 p2, 闭式算出含坏盘重抽后的 RTP(= E[盘面倍率和]/9)。

    E[盘和'] = E + (P + P^2 + ... + P^R) * (E - E[坏盘]), P = 坏盘概率, R = MAX_REROLL。
    """
    shape, kd = _shape_of(rtp), K_DIST[rtp]
    w3 = shape.get(3, 0.0)
    high = {v: w for v, w in shape.items() if v >= CEIL_THRESHOLD}
    w_high = sum(high.values())
    eh = sum(v * w for v, w in high.items()) / w_high if w_high else 0.0
    p3 = (1 - p2) * w3                      # 单格 x3 概率
    ph = (1 - p2) * w_high                  # 单格高倍率(>=x5)概率

    e_sum = 0.0                             # 无条件 E[盘和]
    p_bad = 0.0                             # 坏盘概率
    e_bad = 0.0                             # E[盘和] * P(坏盘) 的加权和
    for k, pk in kd.items():
        for c2 in range(k + 1):             # c2 个 x2
            for ch in range(k + 1 - c2):    # ch 个高倍率(>=x5), 其余 c3 个 x3
                c3 = k - c2 - ch
                prob = (math.comb(k, c2) * math.comb(k - c2, ch)) * (p2 ** c2) * (ph ** ch) * (p3 ** c3)
                s = 2 * c2 + 3 * c3 + ch * eh
                e_sum += pk * prob * s
                if _is_bad_board(k, c2, ch):
                    p_bad += pk * prob
                    e_bad += pk * prob * s
    e_bad_cond = e_bad / p_bad if p_bad > 0 else e_sum
    factor = sum(p_bad ** i for i in range(1, MAX_REROLL + 1))   # P + P^2
    return (e_sum + factor * (e_sum - e_bad_cond)) / NUM_SLOTS


def _solve_p2(rtp):
    """二分反解 x2 权重, 使含坏盘重抽后的 RTP 精确等于档位。"""
    inc = _effective_rtp(0.9, rtp) > _effective_rtp(0.1, rtp)   # 先探单调方向
    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(200):
        mid = (lo + hi) / 2
        if (_effective_rtp(mid, rtp) < rtp) == inc:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# 每档完整倍率分布(x2 权重解出后拼成), 并断言 RTP 精确=档位。
# ⚠️ 下面三个 assert 只保证「表自洽 + RTP 可解且精确 = 档位」—— **形状它管不了**: 表内权重
#    只要还在可解区间, 改一格(5: 0.289→0.290)就会让 _solve_p2 解出新的 p2, RTP 照样精确 =
#    档位, 这里一声不吭。而 boards 对账每档只 60 个种子, 实测 0.289→0.2905(单格概率 +1.06e-3)
#    时 420 个盘面**仍是 0 处不符**。形状的钉子在下面 _P2_NAIL, 不在这个循环里。
VALUE_DIST = {}
for _rtp in (0.80, 1.20, 2.00, 3.60, 10.0, 20.0, 50.0):
    # ⚠️ VALUE_SHAPE 允许整数权重(和不必为 1), 所以只校验"权重为正"; K_DIST 是概率分布, 和须=1。
    assert all(w > 0 for w in VALUE_SHAPE[_rtp].values()) and \
           abs(sum(K_DIST[_rtp].values()) - 1) < 1e-9, "权重/K 分布不合法: %.2f" % _rtp
    _p2 = _solve_p2(_rtp)
    VALUE_DIST[_rtp] = {2: _p2}
    VALUE_DIST[_rtp].update({v: (1 - _p2) * w for v, w in _shape_of(_rtp).items()})
    assert abs(sum(VALUE_DIST[_rtp].values()) - 1) < 1e-12, "配平失败: %.2f" % _rtp
    assert abs(_effective_rtp(_p2, _rtp) - _rtp) < 1e-9, "RTP 漂移: %.4f" % _rtp


# ⚠️ x2 权重是**解**出来的, 只由 VALUE_SHAPE / K_DIST / MAX_REROLL / CEIL_THRESHOLD 决定 ⇒
#    拿它当形状的指纹: 表里任何一格被动过, p2 必动(实测相对 1e-9 的改动仍推得动 1e-12)。
#    容差 1e-12(≈4500 ulp)只留给浮点复现; 整表等比缩放是语义 no-op, 不动 p2 是对的。
_P2_NAIL = {
    0.80: 0.7257776148604471,
    1.20: 0.4432560827541726,
    2.00: 0.43055583414228293,
    3.60: 0.439121573450311,
    10.0: 0.5636363636363635,
    20.0: 0.4357366771159873,
    50.0: 0.2258064516129033,
}
for _rtp, _want in _P2_NAIL.items():
    assert abs(VALUE_DIST[_rtp][2] - _want) < 1e-12, "x2 权重漂移(查 VALUE_SHAPE/K_DIST): %.2f" % _rtp


def _reroll_dead(rtp):
    """这个档的坏盘判据是不是**恒真**(⇒ 重抽纯属空转)。

    ⚠️ 三个隐藏档就是: K_DIST = {9: 1.0} 且非 x2 部分全部 >= x5 ⇒ 每盘都判坏 ⇒
       永远走到最后一盘。三次独立抽样取最后一个与只掷一次分布逐位相同, 等于白掷两遍。
    ⚠️ RTP 一分钱不变 —— 顶层 assert 会兜底验这件事。
    """
    _shape = _shape_of(rtp)
    _w3 = _shape.get(3, 0.0)
    for _k in K_DIST[rtp]:
        for _c2 in range(_k + 1):
            for _ch in range(_k + 1 - _c2):
                if (_k - _c2 - _ch) > 0 and _w3 <= 0:
                    continue                  # x3 抽不出来, 这种组合根本不存在
                if not _is_bad_board(_k, _c2, _ch):
                    return False
    return True


REROLL_DEAD = set(_r for _r in VALUE_DIST if _reroll_dead(_r))


def roll_multipliers(rtp=0.80):
    """掷 k 格填倍率; 坏盘必重抽, 最多 MAX_REROLL 次。有效 RTP 精确 = 档位。

    ⚠️ 必须用**全局 random** —— 调用方靠 `random.seed(seed)` 复现整盘面;
       模块内自建 Random 会让对账基线(和玩家录像回放)全部失效。
    """
    kd = K_DIST.get(rtp, K_DIST[0.80])
    dist = VALUE_DIST.get(rtp, VALUE_DIST[0.80])
    # 判据恒真的档(三个隐藏档)只掷一遍, 见 `_reroll_dead`。
    _n = 1 if rtp in REROLL_DEAD else MAX_REROLL + 1
    for _ in range(_n):                            # 一般最多 3 盘
        k = _pick(kd)
        vals = [_pick(dist) for _ in range(k)]
        n2 = sum(1 for v in vals if v == 2)
        nh = sum(1 for v in vals if v >= CEIL_THRESHOLD)
        if not _is_bad_board(k, n2, nh):           # 不是坏盘: 收下
            break
    mult = [0] * NUM_SLOTS
    for i, v in zip(random.sample(range(NUM_SLOTS), k), vals):
        mult[i] = v
    return mult
