"""盘面几何: 钉阵 / 槽隔板 / 外墙 / 导流弧。

每次调用都重新构造 list —— 老版发射要重建 deflectors 做 arc_dy 抖动, 共享引用会互相污染。
"""

from .config import (
    BALL_R, CH, CW, DIV_TOP, DIV_W, FIELD_L, FIELD_R, FLOOR, LANE_L, LANE_WALL_TOP,
    NUM_SLOTS, PEG_DEEP_MIN_ROW, PEG_DEEP_SHIFT, PEG_R, PEG_ROWS, PEG_SX, PEG_SY,
    PEG_TOP, RIGHT_INNER, SLOT_W, WALL,
)


def build_pegs():
    """偶数行钉在槽中心, 奇数行钉在槽边界 + 两端贴墙钉; 末行 y=570 每个隔板正上方一颗。"""
    rows = []
    for r in range(PEG_ROWS):
        y = PEG_TOP + r * PEG_SY
        if r % 2 == 0:
            xs = [FIELD_L + (i + 0.5) * PEG_SX for i in range(NUM_SLOTS)]
        else:
            off = PEG_DEEP_SHIFT * PEG_SX if r >= PEG_DEEP_MIN_ROW else 0.0
            xs = [FIELD_L + i * PEG_SX + off for i in range(1, NUM_SLOTS - 1)]
            # 最右钉不随深部右移: 右移会与右墙钉夹出 <30px 夹缝卡球。
            xs.append(FIELD_L + (NUM_SLOTS - 1) * PEG_SX)
            # 贴墙钉固定, 不随深部偏移(圆心移进场区, 钉缘距墙 3px)。
            xs.insert(0, FIELD_L + PEG_R + 3)
            xs.append(FIELD_R - PEG_R - 3)
        rows.append([(x, y) for x in xs])
    # 隔板正上方钉(y=570): 把悬念推到最后一刻
    div_pegs = []
    for k in range(1, NUM_SLOTS):
        x = FIELD_L + k * SLOT_W
        div_pegs.append((x, 570))
    rows.append(div_pegs)
    return rows


def build_dividers():
    """底部矮槽之间的竖直隔板。"""
    divs = []
    for k in range(1, NUM_SLOTS):
        x = FIELD_L + k * SLOT_W
        divs.append((x - DIV_W / 2.0, DIV_TOP, x + DIV_W / 2.0, FLOOR))
    return divs


def build_walls():
    """轴对齐矩形墙: 上/左/右/下外墙 + 通道隔墙(部分高度, 顶部留开口)。"""
    return [
        (0, 0, CW, WALL),                       # 顶
        (0, 0, WALL, CH),                       # 左
        (RIGHT_INNER, 0, CW, CH),               # 右
        (0, FLOOR, CW, CH),                     # 底
        (FIELD_R, LANE_WALL_TOP, LANE_L, FLOOR),  # 通道隔墙
    ]


def build_deflectors():
    """发射区导流弧: 球纯竖直上升时以 ~20° 入射角碰到接触段, 被反射向左上抛体进钉阵。

    整条弧是 11 段折线, 切线连续无转折: 根部 R8 弧 + 25° 接触段(15px) + R400 微弯弧延长
    (切线 25°→19°, 末端 (482.1, 94.5))。延长段是"护送感"来源, 且保证每发只有一次 EV_ARC。
    ⚠️ 改形状必须重跑迭代验证(接触率 100% / 首钉包络 [250,450] / 无二次接触 / 卡死 0)。
    """
    cpts = [(508.0, 148.9), (506.8, 148.3), (505.6, 147.3),
            (504.7, 146.3), (504.0, 145.0), (497.7, 131.4), (495.5, 126.7),
            (492.5, 120.4), (489.8, 114.0), (487.2, 107.5),
            (484.6, 100.9), (482.1, 94.5)]
    return [(cpts[i][0], cpts[i][1], cpts[i + 1][0], cpts[i + 1][1])
            for i in range(len(cpts) - 1)]


def build_geo():
    peg_rows = build_pegs()
    return {
        "pegs": [p for row in peg_rows for p in row],  # 渲染用(平铺)
        "peg_rows": peg_rows,                            # 物理用(按行)
        "dividers": build_dividers(),
        "walls": build_walls(),
        "deflectors": build_deflectors(),
    }
