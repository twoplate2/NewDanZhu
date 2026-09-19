"""中奖玻璃杯的球堆数学 —— 生成期一次算完, 运行期零物理。

四层数据流: PileSpec(参数) -> build_pile(3D 终态) -> project_pile(斜投影+画家序)
-> 演出层(只画不模拟)。纯 stdlib, 零 Kivy / tkinter, headless 可单测。

坐标系: design px, 画布 800x460(与 assets/glass_tumbler.png 同源); 本模块用
h = 离地高度(y 向下是屏幕语义, h 向上为正)。

⚠️ _WALL_BEZ 与玻璃杯 PNG 的贝塞尔壁**强耦合** —— 球堆"贴"的壁就是 PNG"画"出来的壁。
   改壁形必须同时重出 glass_tumbler*.png, 否则球会穿出画出来的杯壁。

⚠️ build_pile 的容量兜底会**原地缩小 spec.r**(最多 4 次 x0.94), 同一个 PileSpec
   复用第二次会更小 —— 每局新建 spec, 不要跨局复用实例。

⚠️ 老版在本段之后还有一层**离线烘焙坐标表**(_PILE_BAKED / _PILE_BAKED_R, 定点 x10,
   存球心坐标不存投影) + 运行期"查表失败就静默回退到 build_pile"。它存在的唯一理由是
   **省启动/首帧耗时**: 实测 x100 程序化生成 ~19ms(安卓按 3~8 倍估 60~150ms), 而查表
   是纯 int 解析 + 一次 project_pile; 回退判据是 `(count, v)` 缺项 / 表串解不开 /
   解出的球数 != count 三者任一 -> 返回 None -> 现算。本次只端口程序化生成那一段
   (build_pile 本身), 烘焙表保留与否待对账跑通后单独决定。
"""

import math
import random
import time

DESIGN_W = 800.0
DESIGN_H = 460.0
CX = 400.0
FLOOR_Y = 404.0            # 碗反射椭圆中心: 地板平面 h=0
RIM_Y = 80.0               # 壁顶
RIM_H = FLOOR_Y - RIM_Y    # 杯口在 h 坐标里的高度(=324)

# 堆顶超杯口多少 design px。0 = 不溢出(老行为)。**真正的"溢出"做不到**, 原因是几何上的,
# 别再试: 层距是密排 1.633r, N=100 时球径被容量卡在 r≈46.5 ⇒ 层距≈76px, 而杯口(y=80)到
# 画布顶只有 ~80px —— "多铺一层"直接冲出画布(实测堆顶 y=-51)。中间没有过渡态。
OVERFLOW_MAX = 0.0
K2 = 0.20                  # 斜投影纵剪: 与碗/杯口椭圆 b/a≈0.20 同源(俯视约 12 度)
PILE_SHADE_RANGE = 0.22    # 明暗的**绝对刻度**幅度, 见 project_pile
PACK_PHI = 0.907           # 三角格盘面密度(pi/2sqrt3): 体积方程与格点枚举自洽
TAPER = 1.43               # 圆肩: 底半径/堆高(对应休止角 ~35 度)

# 杯底加宽并放缓收口: 底/口宽约 0.80, 避免旧版漏斗感; 必须与玻璃杯生成器同源。
_WALL_BEZ = ((39.0, 80.0), (65.0, 262.0), (110.0, 404.0))  # 左壁 bezier


def _bez_at(t):
    u = 1.0 - t
    x = u * u * _WALL_BEZ[0][0] + 2 * u * t * _WALL_BEZ[1][0] + t * t * _WALL_BEZ[2][0]
    y = u * u * _WALL_BEZ[0][1] + 2 * u * t * _WALL_BEZ[1][1] + t * t * _WALL_BEZ[2][1]
    return x, y


_HW_TABLE = None


def _build_hw_table(n=160):
    pts = []
    for i in range(n + 1):
        x, y = _bez_at(i / float(n))
        pts.append((FLOOR_Y - y, CX - x))        # (h, halfwidth)
    pts.sort()
    return pts


def halfwidth(h):
    """离地 h 高度处"画出来的"杯内壁半宽(design px), 表外钳制。"""
    global _HW_TABLE
    if _HW_TABLE is None:
        _HW_TABLE = _build_hw_table()
    tab = _HW_TABLE
    if h <= tab[0][0]:
        return tab[0][1]
    if h >= tab[-1][0]:
        return tab[-1][1]
    lo, hi = 0, len(tab) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if tab[mid][0] <= h:
            lo = mid
        else:
            hi = mid
    h0, w0 = tab[lo]
    h1, w1 = tab[hi]
    return w0 + (w1 - w0) * (h - h0) / (h1 - h0)


def floor_radius():
    """碗底平面可用半径(壁内)。"""
    return halfwidth(0.0)


class PileSpec(object):
    """一局球堆的全部参数。默认值 = 老版基线口径(不传 sites/quota/scatter/rot_deg 时
    走"奇偶交替 ABAB + 格点由内而外"的老行为)。"""

    def __init__(self, count, r_dp=11.5, seed=0, dp2px=DESIGN_W / 430.0,
                 jitter=0.10, polish=30, over_f=1.0, wall_f=1.0, rot_deg=0.0,
                 sites=None, scatter=None, quota=None):
        self.count = max(0, int(count))
        self.r = r_dp * dp2px
        self.seed = int(seed)
        self.jitter = float(jitter)
        self.polish = int(polish)
        # 杯口**之上**那一层的收窄系数(1.0=不收窄)。只作用于 h > RIM_H 的层: 杯口以下
        # 必须贴满杯壁(整堆收窄会让球悬在杯子中间 —— 用户实拍反馈过), 杯口以上才是自由堆。
        self.over_f = float(over_f)
        # 杯口**以下**的可用半径系数。1.0=严格贴壁。略小于 1 会让每层少装几颗、把多出来的球
        # 挤到杯口之上变"冒尖"; 代价是球与杯壁之间留缝, 缝宽 = 壁半宽 x (1-wall_f)。
        self.wall_f = float(wall_f)
        # 整堆**绕杯轴(竖直轴)的旋转角**(度)。唯一"零容量"的堆形自由度(刚体: 球心距不变,
        # 重叠断言天然满足; 俯视截面是圆, 格点判据不变)。⚠️ 它改的是**相位不是形状** ——
        # h 谱 / 离轴 ρ 谱 / 最近邻距离谱逐位不变。
        self.rot_deg = float(rot_deg)
        # 层间注册位序列: 0=A 1=B 2=C(见 _enumerate 的 ox/oz)。None = 老行为(奇偶交替 ABAB)。
        # 每层仍是密排三角格、层距仍是 1.633r(球照样坐在三个下层球之间的谷上), 只换落位。
        # 相邻两层不能同位 ⇒ 4 层共 3*2^3 = 24 个合法序列。
        self.sites = tuple(sites) if sites else None
        # 单层小档(x2/x3, 占中奖场次 ~81%)的**自由摆放**变体号, 见 _scatter_floor。
        # ⚠️ 只对 count <= _SCATTER_MAX 生效(那几档实测都是单层, 没有跨层落谷问题)。
        self.scatter = None if scatter is None else int(scatter)
        # 每层的**颗数配额**(纯大档用)。大档是把杯子填满的堆, 层间横移几乎不动轮廓; 配额改的
        # 是"哪层多、哪层少" ⇒ 直接改轮廓。⚠️ 配额只是上限, 被卡住的球**流到下一层**(不丢),
        # 所以总颗数仍 = count —— 但也因此**必须验证装得下**, 流上去的球可能顶到高度上限。
        self.quota = tuple(quota) if quota else None


def _volume_H(spec):
    """体积守恒初值: N 球体积 / 格盘密度 = 半椭球堆体积 (2/3)pi R^2 H, R=TAPER*H。"""
    v_total = spec.count * (4.0 / 3.0) * math.pi * spec.r ** 3 / PACK_PHI
    H = (v_total / ((2.0 / 3.0) * math.pi * TAPER * TAPER)) ** (1.0 / 3.0)
    cap = (FLOOR_Y - RIM_Y) * 0.78        # 大珠档允许堆到内壁 78%
    return max(spec.r * 2.0, min(H, cap))


_SCATTER_MAX = 10       # 自由摆放生效的档位上限。x2/x3/x5/x10 合计占中奖场次 ~90%,
# 而地板层可用半径 245 —— 平铺 10 颗只需半径 sqrt(10*r^2/0.9) ≈ 167。x20 不在此列: 平铺
# 需要 ~237(顶到 245 的边), 且"贴壁一圈"周长只够 15 颗, 放 20 颗必然重叠。


def _scatter_floor(spec, a):
    """单层小档的自由摆放: 返回 [(x, z), ...] 或 None(表示"走老格点")。

    spec.scatter 的语义: 0 -> None(老行为); 其余按 3 取模: 1=贴壁一圈 / 2=偏心一侧 /
    0=随机散开。三种摆法都带上 raw 自己的相位/朝向/种子, 否则 1/4/7/10 会给出相同结果。
    ⚠️ 返回值必须**逐颗**满足 |(x,z)| <= a 且两两球心距 >= 2r, 否则 _assert_pile 抛。
    """
    n, raw, r = spec.count, spec.scatter, spec.r
    if raw == 0 or n < 1:
        return None
    if n == 1:
        return [(0.0, 0.0)]
    mode = (raw - 1) % 3 + 1
    # 相位必须走黄金角, 不能用固定步长: 0.7 弧度是 3 颗球间隔(120 度)的整数倍, 会让 n=3
    # 的变体 7 和 10 完全重合。
    _NSTEP = 6
    if raw <= _NSTEP:
        rho_min = r / math.sin(math.pi / n) if n > 1 else 0.0
        rho = rho_min + (float(raw - 1) / max(1, _NSTEP - 1)) * (a - rho_min)
        rho = max(0.0, min(rho, a))
        ph = math.radians((raw * 137.508) % 360.0)
        return [(rho * math.cos(2.0 * math.pi * i / n + ph),
                 rho * math.sin(2.0 * math.pi * i / n + ph)) for i in range(n)]
    rg = random.Random(raw * 7919 + spec.seed * 131 + n)    # 随机散开, 每 raw 一种
    pts = []
    for _ in range(n):
        for _try in range(400):
            t = rg.uniform(0.0, 2.0 * math.pi)
            rr = a * math.sqrt(rg.uniform(0.05, 1.0))
            x, z = rr * math.cos(t), rr * math.sin(t)
            if all((x - p[0]) ** 2 + (z - p[1]) ** 2 >= (2.0 * r) ** 2 for p in pts):
                pts.append((x, z))
                break
    return pts if len(pts) == n else None


def _enumerate(spec, H, wall_mode=False):
    """给定堆高 H 做确定性格点枚举: 返回 (beads, H, R)。放不满则调用方增大 H。

    wall_mode(N>=35): "一坛子"语义 —— 逐层铺满杯壁圆盘直到堆顶(平截口外圆内收, 天然
    微微圆顶); 土丘 dome 剖面只用于小奖档(否则大珠 x50 圆顶剖面离散层装不下)。
    """
    rng = random.Random(spec.seed)
    r = spec.r
    _rot = math.radians(spec.rot_deg)
    _rc, _rs = math.cos(_rot), math.sin(_rot)
    # ---- 格子间距系数 k_p = 球心间距 / (2r): 1.0 = 球紧挨着 ----
    # ⚠️ 壁模式 1.02(原来 1.12 是"给抛光留的松量", 代价是每层只填 89% 的位置, x100 装不下
    #    ⇒ 缩球兜底把 r 从 52 一路缩到 46, 就是玩家报的「珠子不够大 / 100 个不够满」)。
    #    **不能再往下压到 1.00**: 跨层三维距离 = sqrt((1.1547*k_p*r)^2 + (1.633r)^2), k_p=1.02
    #    时 2.0138r(只剩 0.7px 余量), 1.00 时正好 2.000r —— 抖动一上来 _assert_pile 就报
    #    "unresolved overlap"(实测 k_p=1.00 直接抛)。
    # ⚠️ 土丘档(count<35) 从 1.0 改成 1.25(玩家诉求「球少的时候 球和球有一定距离」)。
    k_p = 1.02 if wall_mode else 1.25
    # ⚠️ wall 模式抖动系数**上限 0.09, 不能到 0.10**: 0.10(±10px) 会让 x100 触发缩球兜底
    #    (r 50.23 -> 47.22), 而缩球破坏玩家定稿的"球一样大"。抖动从可用半径里扣
    #    (a = wall - r - jit), 所以这个系数同时是"逼真度"和"容量"的交换 —— 别再往上加。
    jit = (0.09 if wall_mode else spec.jitter) * 2.0 * r
    R = min(TAPER * H, floor_radius() - r)
    h_cap = RIM_H * 0.95 + (OVERFLOW_MAX if wall_mode else 0.0)
    hv = math.sqrt(8.0 / 3.0) * r            # 密排面间距 1.633r: 层 k+1 坐在层 k 三角谷上
    pitch = math.sqrt(3.0) * r * k_p
    beads = []
    k = 0
    while True:
        h = r + k * hv
        if wall_mode:
            if h > h_cap:
                break
        elif h > H + r * 0.5:
            break
        dome = R * math.sqrt(max(0.0, 1.0 - (h / H) ** 2))
        wall = halfwidth(h)
        if wall_mode:
            # 杯口以下贴壁(可轻微收窄以挤出"冒尖"的球); 杯口以上自由堆, 收窄成墩
            wall *= spec.wall_f if h <= RIM_H else spec.over_f
        # 俯视是圆(深度短缩全交给投影 K2, 与碗椭圆 b=26~K2*257 自洽): 两轴同限。
        # 留出抖动余量, 抖后仍在壁内(贴壁大层边缘本无余量)。
        a = max(0.5, (wall if wall_mode else min(wall, dome)) - r - jit)
        if a <= 0.5 and not wall_mode:
            if k == 0:
                a = max(0.6 * r, floor_radius() - r)   # 极小堆也要坐得进
            else:
                break
        # 单层小档(x2/x3): 走自由摆放, 就地返回。
        # ⚠️ 半径必须用**杯壁**允许的(floor_radius - r - jit), 不能用上面那个 a —— dome 模式下
        #    a 取 min(杯壁, 土丘半径), 而 _polish 只保证"在杯壁内", 实测球本来就会跑到 ρ=88
        #    (超出土丘半径 64)。用土丘半径会把自由摆放白白锁死在中心 —— 那正是要解决的问题。
        if (not wall_mode) and spec.scatter is not None and spec.count <= _SCATTER_MAX:
            _sp = _scatter_floor(spec, floor_radius() - r - jit)
            if _sp is not None:
                return ([{"x": _x, "z": _z, "h": h, "layer": k, "r": r}
                         for _x, _z in _sp], H, R)
        b = a
        # 层间注册位(0=A 1=B 2=C): 默认奇偶交替(A/B) = 老行为; 给了 sites 就按序列走
        _st = spec.sites[k] if (spec.sites and k < len(spec.sites)) else (1 if k % 2 else 0)
        ox = 0.0 if _st == 0 else r * k_p
        oz = 0.0 if _st == 0 else (0.577 * r * k_p if _st == 1 else -0.577 * r * k_p)
        pts = []
        rows = int(2.0 * b / pitch) + 1
        for j in range(rows):
            z = (j - (rows - 1) / 2.0) * pitch + oz
            if b <= 0 or (z / b) ** 2 > 0.94:
                continue
            cols = int(2.0 * a / (2.0 * r * k_p)) + 1
            row_shift = (j % 2) * r * k_p        # 真三角格: 奇行错开半格(漏了它=方格错位挤压)
            for i in range(cols):
                x = (i - (cols - 1) / 2.0) * 2.0 * r * k_p + ox + row_shift
                if (x / a) ** 2 + (z / b) ** 2 > 0.94:
                    continue
                pts.append((x, z))
        pts.sort(key=lambda p: p[0] * p[0] + p[1] * p[1])   # 由内而外 -> 圆顶自然收肩
        _nl = 0                                  # 本层已填几颗(用于配额)
        for x, z in pts:
            if len(beads) >= spec.count:
                break
            # 层颗数配额(见 PileSpec.quota): 本层填够就收手, 剩下的球流到上一层。
            # 只限上限、不丢球 —— 总颗数仍 = count。
            if spec.quota and _nl >= spec.quota[k]:
                break
            _nl += 1
            jx = rng.uniform(-1.0, 1.0) * jit
            jz = rng.uniform(-1.0, 1.0) * jit
            bx, bz = x + jx, z + jz
            if spec.rot_deg:                     # 绕杯轴旋转(见 PileSpec.rot_deg): 刚性, 不改距离
                bx, bz = bx * _rc - bz * _rs, bx * _rs + bz * _rc
            beads.append({"x": bx, "z": bz, "h": h, "layer": k, "r": r})
        k += 1
    return beads, H, R


def build_pile(spec):
    """确定性 3D 球堆终态(生成期一次算完): 大 N 走壁填充"一坛子"; 小 N 体积初值 ->
    不足则长高 -> 截断到 N -> xz 重叠抛光 -> 断言(在壁内/不重叠/不沉底/颗数=倍率)。"""
    t0 = time.perf_counter()
    if spec.count == 0:
        return [], {"count": 0, "H": 0.0, "R": 0.0, "ms": 0.0}
    wall_mode = spec.count >= 35
    cap = RIM_H * 0.95 + (OVERFLOW_MAX if wall_mode else 0.0)
    H = cap if wall_mode else _volume_H(spec)
    beads, H, R = _enumerate(spec, H, wall_mode)
    if not wall_mode:
        tries = 0
        while len(beads) < spec.count and tries < 24 and H < (FLOOR_Y - RIM_Y) * 0.78:
            H *= 1.08
            beads, H, R = _enumerate(spec, H)
            tries += 1
    # 容量兜底: 缩 6% 半径重试, 颗数=倍率的硬承诺优先于目标粒径。
    shrink = 0
    while len(beads) < spec.count and shrink < 4:
        spec.r *= 0.94
        shrink += 1
        H = cap if wall_mode else _volume_H(spec)
        beads, H, R = _enumerate(spec, H, wall_mode)
        if not wall_mode:
            tries = 0
            while len(beads) < spec.count and tries < 24 and H < (FLOOR_Y - RIM_Y) * 0.78:
                H *= 1.08
                beads, H, R = _enumerate(spec, H)
                tries += 1
    assert len(beads) == spec.count, "pile count short"
    _polish(beads, spec)
    _assert_pile(beads, spec)
    cost_ms = (time.perf_counter() - t0) * 1000.0
    meta = {"count": len(beads), "H": H, "R": R, "ms": cost_ms}
    return beads, meta


def _polish(beads, spec):
    """仅消重叠的 xz 推开(3D 距离判定, 纵层距不动), <= spec.polish 轮;
    推开后把球钳回本层壁内圆(抛光不可把球挤出杯)。"""
    r2 = spec.r * 2.0
    for _ in range(spec.polish):
        moved = False
        for i in range(len(beads)):
            bi = beads[i]
            for j in range(i + 1, len(beads)):
                bj = beads[j]
                dx = bj["x"] - bi["x"]
                dz = bj["z"] - bi["z"]
                dh = bj["h"] - bi["h"]
                d2 = dx * dx + dz * dz + dh * dh
                if d2 >= r2 * r2:
                    continue
                d = math.sqrt(d2)
                flat = math.sqrt(dx * dx + dz * dz)
                if flat < 1e-6:
                    dx, dz, flat = r2, 0.0, r2          # 直接叠放罕见: 沿 x 分开
                push = (r2 - d) * 0.5
                bi["x"] -= dx / flat * push
                bi["z"] -= dz / flat * push
                bj["x"] += dx / flat * push
                bj["z"] += dz / flat * push
                moved = True
        for b in beads:
            lim = halfwidth(b["h"]) - spec.r + 0.5   # 只防越出断言边界, 不与抛光抢球
            rr = math.hypot(b["x"], b["z"])
            if rr > lim > 0:
                b["x"] *= lim / rr
                b["z"] *= lim / rr
        if not moved:
            break


def _assert_pile(beads, spec):
    r = spec.r
    r2 = 2.0 * r
    slop = 2.0          # 亚像素级链式约束残留(抛光阻尼收敛尾差)属观感无关
    n = len(beads)
    for i in range(n):
        b = beads[i]
        assert abs(b["x"]) <= halfwidth(b["h"]) - r + 1.0, "ball out of wall"
        assert b["h"] >= r - 0.5, "ball below floor"
    for i in range(n):
        bi = beads[i]
        for j in range(i + 1, n):
            bj = beads[j]
            dx = bi["x"] - bj["x"]
            dz = bi["z"] - bj["z"]
            dh = bi["h"] - bj["h"]
            assert dx * dx + dz * dz + dh * dh > (r2 - slop) ** 2, "unresolved overlap"


def project_pile(beads):
    """(x,h,z) -> 屏幕 design px 斜投影(k1=0 纯纵剪), 返回画家序(远先近后)绘制表。

    每项: i / sx / sy / r / shade / z —— `z` 是给下游排序用的, 不参与绘制。

    ⚠️ 明暗是**绝对刻度**而不是这一堆自己的 min-max: 原来用 (zmax-z)/span 做归一, 于是
       每一堆的亮度跨度恒为 1.61:1, 与真实深度差无关 —— x2 的两颗球深度只差 23.8px
       (屏幕 4.8px)却差 38% 亮度, 读起来像**两种材质**(玩家报的"明暗关系有问题")。
       现在按**离地高度 h** 的绝对刻度(杯高 RIM_H 为尺度): 同层亮度一致, 越高的越亮。
    ⚠️ 杯底只压到 1-PILE_SHADE_RANGE(=0.78)而不是 0.62: 55% 的中奖画面是 2 颗球(单层),
       压到 0.62 会把最常见的画面整体调暗 16%, 是净亏。
    """
    if not beads:
        return []
    zs = [b["z"] for b in beads]
    zmax = max(zs)
    span = (zmax - min(zs)) or 1.0
    out = []
    for idx, b in enumerate(beads):
        _k = (RIM_H - b["h"]) / RIM_H
        _k = 0.0 if _k < 0.0 else (1.0 if _k > 1.0 else _k)
        out.append({"i": idx,
                    "sx": CX + b["x"],
                    "sy": FLOOR_Y - b["h"] - K2 * b["z"],
                    "r": b["r"],
                    "shade": 1.0 - PILE_SHADE_RANGE * _k,
                    "z": b["z"]})
    out.sort(key=lambda p: -p["z"])                    # 远先画, 近后画 -> 画家算法遮挡
    return out
