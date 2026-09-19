"""纯物理层: 积分 / 碰撞 / 发射 / 哑火。不依赖 GUI, 不依赖 Kivy。

数值一律从 .config 读 —— 本模块不重新定义任何常量。
顺序即语义: 碰钉七道限幅、缓动带球的每帧角度改写、物理步内 墙→弧→钉→隔板→落袋
的次序, 动一个落格分布就变。
"""

import math
import random
import time

from . import config as C
# ⚠️ 这一串 from-import 是 **import 时刻的快照**: 之后改 config.G 对已加载的 physics 零影响
#    (老版是单文件全局, 运行期改 G 立刻生效)。所以**凡会被改写的物理量都不能进这张快照表** ——
#    目前只有 `_ARC_FRAME` 一个, 它一律走 `C._ARC_FRAME` 现读(physics_step / advance_flight)。
#    调试期调参改 config 后必须重新 import 本模块, 否则静默无效果。
from .config import (
    _ARC_REACH, ARC_EASE_FRAMES, ARC_OUT_ANGLE, ARC_VISUAL, BALL_R,
    E, E_FAST, E_SIDE, E_SLOW, E_VREF, EV_ARC, EV_CEIL, EV_DIV, EV_PEG, EV_WALL,
    FIELD_L, FIXED_DT, FLOOR, G, KNOB_ARC_ANGLE, KNOB_ARC_BOOST, KNOB_CEIL_ANGLE,
    KNOB_CEIL_BOOST, LAUNCH_MAX, LAUNCH_MIN, MISFIRE_BOUNCE_VY, MISFIRE_E,
    MISFIRE_POWER, MISFIRE_V_MAX, MISFIRE_V_MIN, NUM_SLOTS, PEG_BOUNCE_VY_MAX,
    PEG_BOUNCE_VY_MIN, PEG_CROWN_ESCAPE, PEG_FRICTION, PEG_FRICTION_VY, PEG_KEEP_VY,
    PEG_MIN_ESCAPE, PEG_R, PEG_REFLECT_VX_MAX, PEG_SPRINT, PLUNGER_X, PLUNGER_Y,
    SLOT_W, SUBSTEPS, VMAX, WALL, WALL_E, _power_band, _sample_table,
)


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _reflect(b, nx, ny, e):
    """沿法线反弹, 返回撞击前的法向接近速率(>0 表示真的撞上了, 供音效定音量)。"""
    vn = b.vx * nx + b.vy * ny
    if vn < 0:
        b.vx -= (1 + e) * vn * nx
        b.vy -= (1 + e) * vn * ny
        return -vn
    return 0.0


def _mark(b, bit, sp):
    """记录碰撞事件位 + 该类碰撞本帧的最大撞击速率(GUI 读后清零)。"""
    b.events = b.events | bit
    amp = b.amp
    if amp is None:
        amp = {}
        b.amp = amp
    if sp > amp.get(bit, 0.0):
        amp[bit] = sp


def _collide_pegs(b, rows):
    rr = BALL_R + PEG_R
    rng = getattr(b, "_rng", None) or random    # 确定性: 预演/真发共享同一 rng
    for _row in rows:
        # y 带粗筛: 整行 y 离球超过 rr ⇒ 一颗都碰不到。⚠️ 每次重读 b.y ——
        # 上面某颗钉会改 b.y, 缓存住就是静默漏碰。
        if _row[0][1] < b.y - rr or _row[0][1] > b.y + rr:
            continue
        for px, py in _row:
            dx = b.x - px
            dy = b.y - py
            d2 = dx * dx + dy * dy
            if d2 < rr * rr:
                d = math.sqrt(d2)
                if d > 1e-9:
                    nx, ny = dx / d, dy / d
                else:
                    a = rng.uniform(0, math.tau)
                    nx, ny = math.cos(a), math.sin(a)
                b.x = px + nx * rr
                b.y = py + ny * rr
                vn = -(b.vx * nx + b.vy * ny)           # 法向接近速率
                if vn > 0:                               # 真反弹才处理
                    vy_pre = b.vy                        # 碰前 vy(比例保底用)
                    # e(v): 低速弹得高(逃逸卡死), 高速粘(保持节奏); 侧碰恒低弹(治横滑)
                    if abs(nx) > abs(ny):
                        E_eff = E_SIDE
                    else:
                        E_eff = E_SLOW - (E_SLOW - E_FAST) * clamp(vn / E_VREF, 0.0, 1.0)
                    E_eff *= rng.uniform(0.92, 1.08)     # 反弹高度 ±8% 随机
                    # 法线扰动幅度别加大: 侧碰的横向分量会被一起放大 = "凭空横向移动"
                    g = rng.gauss(0, 0.04)
                    g = clamp(g, -0.15, 0.15)
                    tx_, ty_ = -ny, nx                   # 切向
                    njx = nx + tx_ * g
                    njy = ny + ty_ * g
                    nrm = math.hypot(njx, njy)
                    njx /= nrm; njy /= nrm
                    hit = _reflect(b, njx, njy, E_eff)   # 用扰动后法线 + e(v) 反射
                    # 直接属性访问(与老版一致): 会进 _collide_pegs 的球只有 launch_ball 造的,
                    # 恒有这两字段; 套 getattr 兜底 = 把"漏赋字段"静默吞成默认值, 反而藏错。
                    rehit = (b.hit_peg == (px, py))
                    # crown: 顶冠再访强制分离(治"球冻在钉顶原地微弹"); vx==0 不硬给
                    if rehit and abs(ny) >= abs(nx):
                        if b.vy < PEG_BOUNCE_VY_MIN:
                            b.vy = PEG_BOUNCE_VY_MIN
                        if b.vx != 0:
                            b.vx = math.copysign(max(abs(b.vx), PEG_CROWN_ESCAPE), b.vx)
                    if b.vy < -PEG_BOUNCE_VY_MAX:
                        b.vy = -PEG_BOUNCE_VY_MAX
                    if b.vy >= 0 and b.vy < max(PEG_BOUNCE_VY_MIN, vy_pre * PEG_KEEP_VY):
                        b.vy = max(PEG_BOUNCE_VY_MIN, vy_pre * PEG_KEEP_VY)
                    if abs(b.vx) > PEG_REFLECT_VX_MAX:
                        b.vx = PEG_REFLECT_VX_MAX * (1.0 if b.vx > 0 else -1.0)
                    if abs(nx) > abs(ny) and b.vy >= 0 and b.vy < PEG_MIN_ESCAPE:
                        b.vy = PEG_MIN_ESCAPE            # 逃逸顺导: 沿重力补速, 不沿法线横推
                    if PEG_SPRINT and py == 570:         # 判据是 570 字面量, 与 build_pegs 硬耦合
                        b.vx *= 0.7
                        if 0.0 <= b.vy < 160.0:          # 只兜底"仍向下且不够快", 不抹掉向上分量
                            b.vy = 160.0
                    b.vx *= PEG_FRICTION                 # 摩擦必须在七道限幅之后
                    b.vy *= PEG_FRICTION_VY
                    _mark(b, EV_PEG, hit)
                    b.last_nx = njx; b.last_ny = njy      # 兜底滚落用
                    b.hit_peg = (px, py)
                    b.peg_flash = (px, py)                # 渲染高亮用
                    b.squash = 1.0 - 0.05 * clamp(vn / E_VREF, 0.0, 1.0)
                    b.squash_nx = njx; b.squash_ny = njy
                    b.spin += (b.vx * njy - b.vy * njx) * 0.02


def _collide_rect(b, rx1, ry1, rx2, ry2, e, ev=0):
    cx = max(rx1, min(b.x, rx2))
    cy = max(ry1, min(b.y, ry2))
    dx = b.x - cx
    dy = b.y - cy
    d2 = dx * dx + dy * dy
    if d2 < BALL_R * BALL_R:
        d = math.sqrt(d2)
        if d > 1e-9:
            nx, ny = dx / d, dy / d
        else:                                   # 球心在矩形内: 朝最近边推出
            left, right = b.x - rx1, rx2 - b.x
            top, bot = b.y - ry1, ry2 - b.y
            m = min(left, right, top, bot)
            if m == left:
                nx, ny = -1.0, 0.0
            elif m == right:
                nx, ny = 1.0, 0.0
            elif m == top:
                nx, ny = 0.0, -1.0
            else:
                nx, ny = 0.0, 1.0
        b.x = cx + nx * BALL_R
        b.y = cy + ny * BALL_R
        hit = _reflect(b, nx, ny, e)
        is_ceil = (ev == EV_WALL and ry1 == 0 and ry2 == WALL)
        if is_ceil and hit > 0.0:
            # 天花板弹射器: 角度+力度每发**只抽一次**并存球上, 之后复用(不能每撞一次重抽)
            rng = getattr(b, "_rng", None) or random
            knob = getattr(b, "ceil_knob", None)
            if knob is None:
                band = _power_band(getattr(b, "launch_power", 0.5))
                knob = (_sample_table(KNOB_CEIL_ANGLE[band], rng),
                        _sample_table(KNOB_CEIL_BOOST[band], rng))
                b.ceil_knob = knob
            tilt, scale = knob
            if scale != 1.0:
                b.vx *= scale
                b.vy *= scale
            if tilt:
                sp = math.hypot(b.vx, b.vy)
                a = math.atan2(b.vy, b.vx) - math.radians(tilt)
                b.vx = sp * math.cos(a)
                b.vy = sp * math.sin(a)
            if b.vy < 180.0:            # 保底: 否则减速/旋转把 vy 压太小 → 球贴顶"吸住"
                b.vy = 180.0
        if ev and hit > 0.0:
            _mark(b, EV_CEIL if is_ceil else ev, hit)


# ⚠️ frame 必须是**必填位置参数**, 两种"补默认值"的写法都是坑:
#    写成 `frame=C._ARC_FRAME` —— 在 def 那行就求值, 把逐帧递增的计数冻成 import 时刻的 0,
#      缓动步数 n 从此永远停在 1(缓动只走一帧), 不报错、只是手感变了。
#    写成 `frame=None` 再回读 —— 省略时它每帧都读到新值, 与老版冻结的 0 行为分叉。
#    必填 ⇒ 漏传当场 TypeError, 不存在静默分叉的可能。
def _collide_arc(b, x1, y1, x2, y2, frame):
    """弧面"接触帧缓动带球": 首次接触把出口方向缓动到 ARC_OUT_ANGLE, 速率不变。

    n 只在**新的一帧**上 +1 ⇒ 同一物理步的 6 个子步只算一次缓动。
    """
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else ((b.x - x1) * dx + (b.y - y1) * dy) / L2
    t = max(0.0, min(1.0, t))
    cx, cy = x1 + t * dx, y1 + t * dy
    ox, oy = b.x - cx, b.y - cy
    r = BALL_R * ARC_VISUAL
    if ox * ox + oy * oy >= r * r:
        return
    d = math.sqrt(ox * ox + oy * oy)
    nx, ny = (ox / d, oy / d) if d > 1e-9 else (0.0, -1.0)
    vn = b.vx * nx + b.vy * ny
    if vn >= 0:
        return
    b.x = cx + nx * r
    b.y = cy + ny * r
    st = getattr(b, "arc_ease", None)     # park_ball 造的球无此字段
    if st is None:
        rng = getattr(b, "_rng", None) or random
        band = _power_band(getattr(b, "launch_power", 0.5))    # 力度档 → 4 旋钮离散表
        st = [0, -1,                      # [缓动步数, 上次接触帧, 出口角抖动°, 出口力度系数]
              _sample_table(KNOB_ARC_ANGLE[band], rng),
              _sample_table(KNOB_ARC_BOOST[band], rng)]
        b.arc_ease = st
    n, lf = st[0], st[1]
    if lf != frame:
        n += 1
        lf = frame
        if n == 1 and st[3] != 1.0:       # 力度系数只在首接触帧乘一次(不每帧连乘)
            b.vx *= st[3]
            b.vy *= st[3]
            st[3] = 1.0
    th = ARC_OUT_ANGLE * min(1.0, n / ARC_EASE_FRAMES) + st[2]
    a = math.radians(th)
    sp = math.hypot(b.vx, b.vy)
    b.vx = sp * (-math.sin(a))
    b.vy = sp * (-math.cos(a))
    st[0], st[1] = n, lf
    _mark(b, EV_ARC, -vn)


def physics_step(b, geo, dt):
    """推进一帧(拆 SUBSTEPS 子步)。落袋返回槽序号, 否则 None。"""
    sub = dt / SUBSTEPS
    for _ in range(SUBSTEPS):
        b.vy += G * sub
        sp = math.hypot(b.vx, b.vy)
        if sp > VMAX:
            f = VMAX / sp
            b.vx *= f
            b.vy *= f
        b.x += b.vx * sub
        b.y += b.vy * sub
        for w in geo["walls"]:
            # ⚠️ 地板矩形必须跳过: 当弹性墙撞会在"判定落袋"的同一子步里把球弹成向上
            # (vy=-295~-312), 落袋那帧被弹飞 ⇒ 斜着弹过隔板落进隔壁槽。
            if w[1] == FLOOR:
                continue
            _ylo = w[1] if w[1] < w[3] else w[3]
            _yhi = w[3] if w[1] < w[3] else w[1]
            if _yhi < b.y - BALL_R or _ylo > b.y + BALL_R:
                continue
            _collide_rect(b, w[0], w[1], w[2], w[3], WALL_E, EV_WALL)
        for s in geo["deflectors"]:
            _ylo = s[1] if s[1] < s[3] else s[3]
            _yhi = s[3] if s[1] < s[3] else s[1]
            if _yhi < b.y - _ARC_REACH or _ylo > b.y + _ARC_REACH:
                continue
            _collide_arc(b, s[0], s[1], s[2], s[3], C._ARC_FRAME)
        _collide_pegs(b, geo["peg_rows"])
        for d in geo["dividers"]:
            _ylo = d[1] if d[1] < d[3] else d[3]
            _yhi = d[3] if d[1] < d[3] else d[1]
            if _yhi < b.y - BALL_R or _ylo > b.y + BALL_R:
                continue
            _collide_rect(b, d[0], d[1], d[2], d[3], E, EV_DIV)
        if b.y + BALL_R >= FLOOR - 0.5:
            # ⚠️ 只清 vx 不清 vy: 清 vy 会把落地弹跳抹平(回弹=撞击速度×LAND_E)。
            #    清 vx 让"结算槽 == 落格槽"成为结构性不变量(调用方再推进也不改槽号)。
            b.y = FLOOR - BALL_R
            b.vx = 0.0
            i = int((b.x - FIELD_L) / SLOT_W)
            return max(0, min(NUM_SLOTS - 1, i))
    return None


def power_u(power):
    """有效蓄力区间 [MISFIRE_POWER, 1.0] 归一化到 [0, 1]。低于阈值的是哑火, 不走这里。"""
    return clamp((power - MISFIRE_POWER) / (1.0 - MISFIRE_POWER), 0.0, 1.0)


class Ball:
    """弹珠物理状态。__slots__ 消除 dict 哈希开销。

    ⚠️ 字段名下游 GUI 按名读, 不能改。保留 __getitem__/__setitem__/get 兼容接口。
    """
    __slots__ = ('x', 'y', 'vx', 'vy', 'item', 'born', 'events', 'amp',
                 'misfire',
                 'launch_power', '_stall_retry', '_rng',
                 'last_nx', 'last_ny',
                 'hit_peg', 'squash', 'squash_nx', 'squash_ny', 'spin',
                 'arc_ease', 'peg_flash', 'ceil_knob')

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)


def launch_ball(power, rng=None):
    """按蓄力比例 power 生成一颗向上发射的球(位于弹簧柱塞处), 纯竖直、零横向引导。

    竖直速度只在 LAUNCH_MIN~LAUNCH_MAX 的窄带内变化, 越顶时刻才散得开。
    """
    u = power_u(power)
    speed = LAUNCH_MIN + (LAUNCH_MAX - LAUNCH_MIN) * u
    return Ball(x=PLUNGER_X, y=PLUNGER_Y, vx=0.0, vy=-speed,
                item=None, born=time.time(), events=0, amp={},
                misfire=False,
                launch_power=power, _stall_retry=0, _rng=rng,
                last_nx=0.0, last_ny=-1.0,
                hit_peg=None, squash=1.0, squash_nx=0.0, squash_ny=-1.0, spin=0.0,
                arc_ease=None, peg_flash=None, ceil_knob=None)


def misfire_speed(power):
    """哑火发射速度: 蓄力越小升得越低(线性)。"""
    u = clamp(power / MISFIRE_POWER, 0.0, 1.0)
    return MISFIRE_V_MIN + (MISFIRE_V_MAX - MISFIRE_V_MIN) * u


def launch_misfire(power):
    """力度不足: 球照样弹出去, 只是升不过隔墙顶, 会掉回柱塞。"""
    b = launch_ball(power)
    b.vy = -misfire_speed(power)
    b.misfire = True
    return b


def advance_misfire(b):
    """竖井内一维升降(全程零碰撞, x 恒 = PLUNGER_X)。归位返回 True。

    ⚠️ 绝不能走 physics_step: 它的落袋判定没有 x<FIELD_R 保护,
       会把落回柱塞的球报成 8 号槽。
    """
    b.vy += G * FIXED_DT
    b.y += b.vy * FIXED_DT
    if b.vy > 0 and b.y >= PLUNGER_Y:
        b.y = PLUNGER_Y
        if b.vy > MISFIRE_BOUNCE_VY:
            b.vy = -b.vy * MISFIRE_E
            return False
        b.vy = 0.0
        return True
    return False


def advance_flight(b, geo):
    """推进一帧(GUI/自测共用): 弧面缓动帧计数 + 物理。落袋返回槽号, 否则 None。"""
    C._ARC_FRAME += 1                # 弧面缓动帧计数; frame 存在 config 里, 增量必须写回那里
    return physics_step(b, geo, FIXED_DT)
