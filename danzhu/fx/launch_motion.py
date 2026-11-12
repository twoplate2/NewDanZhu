"""发射通道的只读显示外推，不推进物理或保存历史位姿。"""

from ..config import (ARC_VISUAL, BALL_R, BALL_VIS_R, FIXED_DT, LANE_L,
                      PLUNGER_X, PLUNGER_Y, RIGHT_INNER, WALL)


def launch_draw_position(game):
    ball = game.ball
    if ball is None:
        return None
    x, y = ball.x, ball.y
    if (not game.launch_extrapolation or game.state != "flying"
            or ball.vy >= 0.0 or abs(ball.vx) > 1e-6
            or abs(x - PLUNGER_X) > 1e-6 or y > PLUNGER_Y
            or game._crossed or getattr(ball, "arc_ease", None) is not None
            or getattr(ball, "_stall_retry", 0)):
        return x, y
    # 只处理左右墙之间的直线发射；保留整个可见球的边界余量。
    radius = max(BALL_VIS_R, BALL_R * ARC_VISUAL)
    if not LANE_L + radius <= x <= RIGHT_INNER - radius:
        return x, y
    arcs = game.geo["deflectors"]
    if not arcs:
        return x, y  # 没有已知导流几何时不猜安全范围。
    # 所有导流段最下端以下才允许外推，包含每次发射的实际弧面抖动。
    # 在此处截住显示位置，让物理自然追上，避免碰撞时从预测位置跳回。
    boundary = max(WALL, max(max(y1, y2) for _, y1, _, y2 in arcs)) + radius
    if y <= boundary:
        return x, y
    remaining = min(FIXED_DT, max(0.0, game._accumulator))
    return x, max(boundary, y + ball.vy * remaining)
