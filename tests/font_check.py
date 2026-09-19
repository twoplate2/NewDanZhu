"""字体子集门禁：扫新工程里的中文字面量，报出字库没有的字符。

⚠️ 为什么需要它：`fonts/NotoSansSC-Medium.otf` 是**裁剪过的子集**（1602 码位 / 1444 汉字），
不是完整中文字体。新写一句 UI 文案，只要里面有字库外的字，**真机上就是一个方框**，
而桌面（有系统字体兜底）截图验证不出来 —— 老版为此让过 4 次路：
「摄氏度」写成「度」、「每瓦跑分」写成「每W跑分」、删掉带「群」的脚注、
结论「UI 文案别用全角冷门符号」（实测全角 ＝ 真机是方块而桌面正常）。

跑法：python tests/font_check.py            # 全工程
      python tests/font_check.py danzhu     # 只看某个目录
"""

import os
import sys

# ⚠️ 必须在任何 print 之前: 下面的诊断带 ⚠️/中文, 而 Windows 控制台默认 GBK
#    —— 不带这句, **门禁自己会在报红的时候崩掉**(实测), 那比不报还糟。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHARS = os.path.join(HERE, "golden", "font_chars.txt")

# 只扫**会上设备屏幕**的源码。
# ⚠️ `tests/` `temp/` `tools/` 都是**在 PC 上跑**的：测试夹具、过程稿、
#    开发工具(语音再生成 / 余额模拟器出 HTML 报告)。它们的字串永不上设备,
#    算进来就是假阳性 —— 假阳性多了这条门禁就会被无视, 等于没有。
SKIP_DIRS = {"tests", "temp", "tools", ".git", "__pycache__", "assets", "fonts"}


def load_font_chars():
    with open(CHARS, encoding="utf-8") as f:
        return set(f.read())


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def needs_font(ch):
    """要字体才能显示、且不是 ASCII 控制/空白的那种字符。"""
    o = ord(ch)
    if o < 0x80:
        return False
    # 中日韩统一表意 + 全角/中文标点 + CJK 符号
    return (0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F
            or 0xFF00 <= o <= 0xFFEF or 0x2000 <= o <= 0x206F)


def iter_ui_strings(path):
    """只取**会显示的字面量**：AST 里的字符串常量，但排除 docstring。

    ⚠️ 不能用纯文本扫 —— 注释和 docstring 里的中文不上屏，全算进来就是一堆假阳性，
    假阳性多了这条门禁就会被无视，等于没有。docstring 与注释一起排除是刻意的：
    代码里写「摄氏度」没关系，只有把它塞进 Label 才有关系。
    """
    import ast
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_nodes:
            yield node.lineno, node.value


def check_registration():
    """子集字体必须在 `danzhu/ui/text.py` 的**导入期**被 register 成 "Roboto"。

    ⚠️ 为什么单列一条(实测踩过): 老版 main.py:486-493 有
       `LabelBase.register(name="Roboto", fn_regular=.../NotoSansSC-Medium.otf)`,
       而新版端口时**整条没落地** —— 全树一次 register 都没有。
       后果**全程静默**: Kivy 退回自带的 Roboto(不含汉字), 中文变豆腐块, 且
       `text_px` 的宽度全变(实测 `text_px("未中",48,True)` 96 -> 42,
       `"中奖! +100"` 231 -> 177) ⇒ 自适应字号、弹窗排版、槽位文字全线跟着错。

    ⚠️ 为什么是**静态**检查而不是量一句宽度: `text_px` 要 GL 上下文, 无窗口直接段错误
       (实测)。而带窗跑属于 `tests/trace_parity.py` 的活 —— **它的布局段(28 控件 x 2)
       就是靠 `text_px`/`fit_font_size` 算出来的, 字体一掉那一整段立刻变红**。
       所以这条只负责"那行源码还在不在", 端到端由对账台负责。

    ⚠️ 必须是**模块级**语句: 塞进某个永远不调的函数里等于没注册, 而静态看"文件里有
       register"会假绿。
    """
    import ast
    path = os.path.join(ROOT, "danzhu", "ui", "text.py")
    with open(path, encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError as e:
            print("[红] 字体注册: %s 解析失败 %s" % (path, e))
            return 1
    hits = []
    for node in tree.body:                       # ⚠️ 只看**顶层**语句
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name != "register":
                continue
            kw = {k.arg: k.value for k in sub.keywords if k.arg}
            nm = kw.get("name")
            if isinstance(nm, ast.Constant) and nm.value == "Roboto":
                hits.append(sub.lineno)
    if not hits:
        print("[红] 字体注册: `danzhu/ui/text.py` 的**模块级**没有 "
              '`LabelBase.register(name="Roboto", ...)`')
        print("     ⚠️ 掉了它 = Kivy 退回不含汉字的默认字体: 真机中文全变方块,")
        print("        文字宽度也全变(实测 96->42), 而且不报任何错。")
        return 1
    # 登记的字体文件必须在
    fpath = os.path.join(ROOT, "fonts", "NotoSansSC-Medium.otf")
    if not os.path.exists(fpath):
        print("[红] 字体注册: 登记了但文件不在 %s" % fpath)
        return 1
    print("[绿] 字体注册: text.py:%d 模块级注册了 Roboto -> 子集字体" % hits[0])
    return 0


def main():
    rc = 0
    target = sys.argv[1] if len(sys.argv) > 1 else ROOT
    have = load_font_chars()
    missing = {}
    for path in iter_py(target):
        for ln, text in iter_ui_strings(path):
            for ch in text:
                if needs_font(ch) and ch not in have:
                    missing.setdefault(ch, []).append(
                        (os.path.relpath(path, ROOT), ln))
    if not missing:
        print("[绿] 字体子集: 源码里的中文都在字库内")
    else:
        rc = 1
        print(f"[红] 字体子集: {len(missing)} 个字库外的字符 —— 真机会显示成方块")
        for ch in sorted(missing):
            where = missing[ch]
            spot = ", ".join(f"{p}:{n}" for p, n in where[:4])
            more = f" (+{len(where) - 4})" if len(where) > 4 else ""
            print(f"      {ch!r} U+{ord(ch):04X}  ×{len(where)}  {spot}{more}")
    # 只在扫全工程时查注册(扫单个目录时那与它无关)
    if len(sys.argv) <= 1:
        rc = check_registration() or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
