"""画布层序门禁 —— 「**顺序即层序**」这条红线。

    python tests/canvas_order.py

## 为什么需要它

`GameArea._redraw` 的注释写着「一行都不能挪，**顺序即层序**」，`WinPileFX._tbl_build`
那条更狠（「建表顺序一个字不许动」）—— 但这两条**在本工程里一条断言都没有**。
`tests/fx_gates.py` 管的是演出层的**数值**判据（压暗 alpha、rim 段数、时长…），
`tests/trace_parity.py` 管的是**行为**（帧/音效/布局），**都不看画布指令的先后**。

⇒ 把某个 `with canvas:` 块挪到前面（例如把「压暗」挪到「后层玻璃」之前），
行为对账**全绿**，而画面变成「杯子周围反而最亮」那种一眼可见的错。

## 判据

把两个类里**发出画布指令/几何助手**的调用按源码出现顺序抽出来（AST），逐条比。
比的是**调用名与顺序**，不比参数 —— 参数由 `fx_gates.py` 的数值门禁管。

⚠️ 老版真值在 `E:\\AI_Tools\\other\\DanZhu\\android\\main.py`（外部依赖，与
   `fx_gates.py` 同一条：那份不在就跑不了，脚本会**如实报"没比"**而不是假装通过）。
"""

import ast
import io
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OLD_MAIN = r"E:\AI_Tools\other\DanZhu\android\main.py"

# 发画布指令的画布类 + 把静态元素画出来的几何助手 + 会改画布的兄弟方法
CANVAS_NAMES = (
    "Rectangle", "Ellipse", "Line", "Color", "PushMatrix", "PopMatrix", "Rotate",
    "StencilPush", "StencilPop", "StencilUse", "StencilUnUse", "RoundedRectangle",
    "Matrix", "BindTexture",
    "_rect", "_circle", "_update_slots",
)

# (类名, 方法名, 老版文件, 新版文件, 这条管什么)
TARGETS = [
    ("GameArea", "_redraw",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "game_area.py"),
     "盘面整块: 底/墙/钉/弧/槽/隔板/弹簧/球 —— **顺序即层序**"),
    ("GameArea", "big_result_text",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "game_area.py"),
     "中奖大字的画布层"),
    ("WinPileFX", "_tbl_build",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "winfx.py"),
     "装杯持久指令表: 阴影<下玻璃<压暗<远侧环<补画<弹珠<前玻璃"),
    ("WinPileFX", "_draw_bead",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "winfx.py"),
     "单颗球: PushMatrix/Rotate/... 的配对与次序"),
    # ---- 自绘控件(核验时点名「未对画布指令逐条 diff」的那几个) ----
    ("SpeedCurve", "_draw",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "widgets.py"),
     "跑分曲线: 网格/折线/分位带的叠放次序"),
    ("SpeedCurve", "_txt",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "widgets.py"),
     "曲线坐标轴文字的叠放"),
    ("FpsCurve", "_draw",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "widgets.py"),
     "帧率曲线: 阶段色带/曲线/帽线"),
    ("FpsCurve", "_label",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "widgets.py"),
     "帧率曲线的轴标"),
    ("LandLayer", "__init__",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "widgets.py"),
     "横屏反旋转层: canvas.before=PushMatrix/Rotate, after=PopMatrix —— "
     "**配对错了整棵树跟着转**"),
    ("GlyphLabel", "__init__",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "widgets.py"),
     "字形标签的画布层"),
    ("_LoadVeil", "_build_title",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "veil.py"),
     "启动页六个字: 两层 Label + Stencil 裁剪三连(**顺序错了裁不出扫色**)"),
    ("_LoadVeil", "__init__",
     OLD_MAIN, os.path.join(ROOT, "danzhu", "ui", "veil.py"),
     "启动页底色"),
]


def canvas_seq(path, cls, fn):
    """按**源码出现顺序**抽出画布调用名。找不到那个方法返回 None。"""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except Exception:
        return None
    for n in ast.walk(tree):
        if not (isinstance(n, ast.ClassDef) and n.name == cls):
            continue
        for m in n.body:
            if not (isinstance(m, ast.FunctionDef) and m.name == fn):
                continue
            out = []
            for x in sorted((y for y in ast.walk(m) if isinstance(y, ast.Call)),
                            key=lambda y: (y.lineno, y.col_offset)):
                f2 = x.func
                nm = getattr(f2, "attr", None) or getattr(f2, "id", None)
                if nm in CANVAS_NAMES:
                    out.append(nm)
            return out
    return None


def main():
    if not os.path.exists(OLD_MAIN):
        print("[红] 找不到老版真值 %s —— **没有比对**, 不算通过" % OLD_MAIN)
        return 1
    bad = 0
    for cls, fn, oldp, newp, what in TARGETS:
        o = canvas_seq(oldp, cls, fn)
        n = canvas_seq(newp, cls, fn)
        name = "%s.%s" % (cls, fn)
        if o is None or n is None:
            print("  [FAIL] %-26s 抽不到: 老版=%s 新版=%s" % (name, o is None, n is None))
            bad += 1
            continue
        if o == n:
            print("  [OK  ] %-26s %3d 条逐条同序  %s" % (name, len(o), what))
            continue
        bad += 1
        print("  [FAIL] %-26s 层序不同(老 %d / 新 %d)" % (name, len(o), len(n)))
        for i in range(min(len(o), len(n))):
            if o[i] != n[i]:
                print("         首个不同 @%d: 老 %s / 新 %s" % (i, o[i], n[i]))
                print("         老版附近 %r" % (o[max(0, i - 3):i + 4],))
                print("         新版附近 %r" % (n[max(0, i - 3):i + 4],))
                break
        else:
            print("         前缀相同, 只是条数不同")

    print()
    if bad:
        print("门禁结果: 红 %d 项 / 共 %d 项 —— **顺序即层序, 挪一行画面就错**"
              % (bad, len(TARGETS)))
        return 1
    print("门禁结果: 全绿 —— %d 个方法的画布层序与老版逐条相同" % len(TARGETS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
