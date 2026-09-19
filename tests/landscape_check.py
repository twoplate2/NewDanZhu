"""横屏反旋转层门禁 —— `LandLayer` 的**旋转分支**。

    python tests/landscape_check.py

## 为什么需要它

`tests/trace.py` 把窗口钉死在 540x960（竖屏），所以整条轨迹对账里
`LandLayer.angle` **恒为 0** —— 那条分支（横拿时把画面整体转 90 度、坐标逆变换）
**一次都没被跑过**。而它一旦错，症状是「横屏所有按钮点不中」（坐标没逆变换）
或「画面倒置」（90/-90 反了）。

⚠️ 桌面要进横屏分支得带 `--landscape`（`apply_orientation` 里判的就是它），
   所以本脚本自己往 `sys.argv` 里塞一个。
"""

import os
import sys
import warnings

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("KIVY_NO_ARGS", "1")
warnings.filterwarnings("ignore")

ROWS = []


def check(ok, name, detail=""):
    ROWS.append(ok)
    print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                           ("  " + detail) if detail else ""))


def main():
    from kivy.clock import Clock
    from kivy.core.window import Window
    from danzhu.ui.widgets import LandLayer

    def resize(w, h):
        """改窗口尺寸并**泵几帧**让它真的落地。

        ⚠️ 光 `Window.size = ...` 不够: 没有事件循环时 SDL 的 resize 不会立刻反映到
           `Window.width/height` —— 实测不泵的话 `apply_orientation` 读到的还是默认
           800x600, 于是整个测试量的是错的窗口(第一版就栽在这里: 竖屏那条也判成
           「进了反旋转」)。
        """
        Window.size = (w, h)
        for _ in range(3):
            Clock.tick()
        return (Window.width, Window.height)


    # 桌面要进横屏分支得带 --landscape(见 apply_orientation)
    if "--landscape" not in sys.argv:
        sys.argv.append("--landscape")

    print("== 横屏反旋转层 ==")

    # ---- 竖屏: 零回归 ----
    got = resize(540, 960)
    check(tuple(got) == (540.0, 960.0), "窗口尺寸 540x960 已落地", "%r" % (got,))
    lay = LandLayer()
    land = lay.apply_orientation()
    check(land is False, "竖屏不进反旋转", "land=%s" % land)
    check(lay.angle == 0, "竖屏 angle=0", "angle=%s" % lay.angle)
    check(lay._to_eq(123.0, 456.0) == (123.0, 456.0),
          "竖屏 _to_eq 是恒等", "%r" % (lay._to_eq(123.0, 456.0),))

    # ---- 横屏: 进反旋转 ----
    got = resize(1740, 1000)
    check(tuple(got) == (1740.0, 1000.0), "窗口尺寸 1740x1000 已落地", "%r" % (got,))
    land = lay.apply_orientation()
    check(land is True, "横屏(1740x1000)进反旋转", "land=%s" % land)
    check(lay.angle in (90, -90), "角是 ±90", "angle=%s" % lay.angle)
    check(tuple(lay.size) == (1740.0, 1000.0), "层尺寸=窗口", "%r" % (tuple(lay.size),))
    # 等效盒 = 短边 x 长边(保持竖拿构图)
    if lay._anchor is not None:
        check(tuple(lay._anchor.size) == (1000.0, 1740.0),
              "等效盒 = (短边, 长边)", "%r" % (tuple(lay._anchor.size),))

    # ---- 坐标变换: 互逆 ----
    pts = [(0.0, 0.0), (1740.0, 1000.0), (870.0, 500.0), (100.0, 900.0), (1700.0, 40.0)]
    worst = 0.0
    for (x, y) in pts:
        ex, ey = lay._to_eq(x, y)
        bx, by = lay._to_win(ex, ey)
        worst = max(worst, abs(bx - x), abs(by - y))
    check(worst < 1e-9, "_to_eq / _to_win 互逆(5 个点)",
          "最大往返误差 %.3g px" % worst)

    # ---- 中心点不动(旋转原点 = 窗口中心) ----
    cx, cy = 1740.0 / 2.0, 1000.0 / 2.0
    ex, ey = lay._to_eq(cx, cy)
    check(abs(ex - cx) < 1e-9 and abs(ey - cy) < 1e-9,
          "窗口中心是旋转不动点", "_to_eq(%.0f,%.0f)=(%.3f,%.3f)" % (cx, cy, ex, ey))

    # ---- 90 度旋转的**方向**必须对 ----
    # ⚠️ 这是最容易反的一处: 正变换逆时针、逆变换顺时针。把 _to_eq 与 _to_win
    #    的 90 / -90 两对分支写反, 互逆性**照样成立**(互逆是自洽的), 只有方向错。
    #    ⇒ 必须单独钉一条"方向"判据: 窗口右上角 (W, 0) 经逆变换应落在
    #    等效竖屏的**右下角**附近(横拿时屏幕右侧 = 竖构图的下方)。
    ex, ey = lay._to_eq(1740.0, 0.0)
    if lay.angle == 90:
        want = (1740.0, 0.0)      # 逆变换 = 顺时针 90: (cx+dy, cy-dx)
        want = (cx + (0.0 - cy), cy - (1740.0 - cx))
    else:
        want = (cx - (0.0 - cy), cy + (1740.0 - cx))
    check(abs(ex - want[0]) < 1e-9 and abs(ey - want[1]) < 1e-9,
          "90 度旋转的**方向**正确(不只是互逆)",
          "angle=%s  _to_eq(1740,0)=(%.1f,%.1f) 期望 (%.1f,%.1f)"
          % (lay.angle, ex, ey, want[0], want[1]))

    # ---- to_local / to_parent 与 _to_eq / _to_win 同源 ----
    check(lay.to_local(700.0, 300.0) == lay._to_eq(700.0, 300.0),
          "to_local 走 _to_eq(横屏时)", "%r" % (lay.to_local(700.0, 300.0),))
    check(lay.to_parent(700.0, 300.0) == lay._to_win(700.0, 300.0),
          "to_parent 走 _to_win(横屏时)", "%r" % (lay.to_parent(700.0, 300.0),))

    # ---- 转回竖屏: 角度与位置必须精确复原 ----
    resize(540, 960)
    lay.apply_orientation()
    check(lay.angle == 0, "转回竖屏 angle 归零", "angle=%s" % lay.angle)
    check(lay._to_eq(200.0, 800.0) == (200.0, 800.0),
          "转回竖屏后 _to_eq 恢复恒等")

    print()
    bad = ROWS.count(False)
    if bad:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (bad, len(ROWS)))
        return 1
    print("门禁结果: 全绿 —— %d 项(含 90 度方向判据)" % len(ROWS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
