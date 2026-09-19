"""AST 逐函数比对门禁 —— 老版每个 `def` ↔ 新版对应函数的**归一化函数体**对账。

    python tests/ast_parity.py            # 跑门禁（差异集合必须与基线逐条相同）
    python tests/ast_parity.py --freeze   # 重新采基线（**只在确认差异都合理时**）

## 这条门禁补的是什么洞

工程里有 21 条门禁，判据分别是「数值逐位相同」「事件序列」「跨层副作用」……
**但没有一条把老版每个 `def` 的函数体与新版对应函数做逐句比对。**

⇒ 于是「接线重复 / 漏接 / 调用次数不一致」这一类缺陷**全都溜得过去**：
2026-09-19 第三轮审计用这个透镜扫了两版 **120 处**函数体差异，抓到的**唯一一处真差异**是
`Game._controls` 多发一条 `restyle_buttons` ⇒ `_restyle_buttons()` 每帧跑 **2** 次
（老版 AST 计数 **1**）。它幂等、画面不变、`trace_parity` 逐位相同 ——
**21 条门禁一条都没红**。

## 判据

1. 用 `ast` 取老版 `android/main.py` 与新版 `danzhu/**/*.py` 的每个函数（含类方法），
   归一化（**去 docstring / 去注释 / 归一化已知改名**）后比对函数体；
2. 差异集合必须与 `tests/golden/ast_diff.txt` **逐条相同**（与 `fx_gates.py` 同一个套路）：
   * **多一条** = 新版真漂了 ⇒ 红；
   * **少一条** = 门禁被削弱了（或有人偷偷改了老版）⇒ 红；
   * 基线读不到 ⇒ 红（"没比"不等于"通过"）。

## ⚠️ 归一化只放**已知改名**，不放"看着差不多"

`ALIAS` 表里每一项都必须能说清"为什么这两个拼写在两版里是同一件事"。
往表里加东西 = 宣布"这类差异不算差异" —— 加之前先问：**它会不会把真差异一起盖掉？**
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OLD_MAIN = os.path.join(os.path.dirname(ROOT), "android", "main.py")
GOLDEN = os.path.join(HERE, "golden", "ast_diff.txt")
NEW_PKG = os.path.join(ROOT, "danzhu")

# ⚠️ **已知改名**: 左边是规范名, 右边是两版各自的拼写。加项前先想清楚会不会盖掉真差异。
ALIAS = (
    ("_boot_log",      ("_boot_log", "B._boot_log")),
    ("_snd",           ("self.sfx.play", "self._snd", "sfx.play", "self.sfx.play_named")),
    ("_schedule",      ("Clock.schedule_once", "self._schedule")),
    ("_status",        ("self._set_game_status", "self._status")),
    ("_controls",      ("self._set_controls_enabled", "self._controls")),
    ("_state",         ("self.state", "self.game.state")),
    ("_now",           ("time.time", "self.now", "self._wall")),
    ("_refresh",       ("self._refresh_stats", "self._refresh_stats")),
)
_SPELL2CANON = {}
for _c, _sp in ALIAS:
    for _s in _sp:
        _SPELL2CANON[_s] = _c

# ⚠️ **两条通用归一化规则** —— 代替 43 条手写别名（triage 提炼出来的）：
#   R1 模块限定前缀: `X` ↔ `B.X` / `_cfg.X` / `winfx.X` …（纯命名空间, 同一个对象）
#   R2 属性上移:    `self.X` ↔ `self.game.X`（老版全在一个 RootWidget 里, 拆出去后归了 Game）
MODULE_ALIASES = ("B", "_cfg", "winfx", "P", "_dev", "_syn", "C", "benchcpu",
                  "voice", "synth", "BOOT", "appmod", "_veilmod", "_rootmod")
ATTR_HOME = "game"          # `self.game.X` -> `self.X`


class _Norm(ast.NodeTransformer):
    """去 docstring + 归一化已知改名。"""

    def _strip_doc(self, body):
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)):
            return body[1:]
        return body

    def visit_FunctionDef(self, n):
        self.generic_visit(n)
        n.body = self._strip_doc(n.body) or [ast.Pass()]
        _alpha_locals(n)
        return n

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, n):
        self.generic_visit(n)
        n.body = self._strip_doc(n.body) or [ast.Pass()]
        return n

    def _canon_expr(self, n):
        """把 `_spell` 出来的拼写按 R1/R2 归一, 归一了就换成 Name 节点。

        ⚠️ R2 之后**必须再查一次别名表** —— 否则 `self.game.state` 被归成 `self.state`,
           而别名表把 `self.state` 映射到 `_state`, 两边拼写仍不同 ⇒ 假 DIFF
           (实测 `_replay_cold_start` / `_wait_idle_bench` / `_wait_idle_hp` 三条就是这么来的)。
        """
        sp = _spell(n)
        if not sp:
            return None
        head, _, rest = sp.partition(".")
        if rest and head in MODULE_ALIASES:          # R1
            sp = rest
        elif sp.startswith("self." + ATTR_HOME + "."):  # R2
            sp = "self." + sp[len("self." + ATTR_HOME + "."):]
        elif sp not in _SPELL2CANON:
            return None
        canon = _SPELL2CANON.get(sp, sp)
        return ast.Name(id=canon.replace(".", "$"), ctx=ast.Load())

    def visit_Call(self, n):
        self.generic_visit(n)
        r = self._canon_expr(n.func)
        if r is not None:
            n.func = r
        return n

    def visit_Attribute(self, n):
        self.generic_visit(n)
        r = self._canon_expr(n)
        return r if r is not None else n


def _alpha_locals(fn):
    """把函数内的**局部变量名按首现顺序重编号**(`L0, L1, ...`)。

    ⚠️ 为什么必须做: `ast.dump` **把局部变量名也算进指纹**。两版把同一个局部叫
       `lab` 和 `selected`, 指纹就不同 ⇒ 报一条**行为上毫无意义**的 DIFF。
       实测(2026-09-19): 89 条 DIFF 里一大批是这种 —— 机械抽差异拼写时看到的
       全是 `lab`↔`selected` / `list`↔空 / `print`↔`bool` 这类。
       ⇒ **不做 alpha 归一化, 这条闸的 DIFF 清单就淹在噪声里**。
    ⚠️ `global` / `nonlocal` 声明的名字**跳过** —— 它们指向模块级真源, 改名会掩盖差异。
    """
    skip = set()
    for x in ast.walk(fn):
        if isinstance(x, (ast.Global, ast.Nonlocal)):
            skip |= set(x.names)
    order = []

    def _walk(node):                    # 源码顺序(不是 ast.walk 的 BFS 顺序)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.id not in skip and node.id not in order:
                order.append(node.id)
        for ch in ast.iter_child_nodes(node):
            _walk(ch)
    for st in fn.body:
        _walk(st)
    ren = {}
    for i, nm in enumerate(order):
        ren[nm] = "L%d" % i

    class _R(ast.NodeTransformer):
        def visit_Name(self, x):
            if x.id in ren:
                x.id = ren[x.id]
            return x
    _R().visit(fn)


def _spell(n):
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        b = _spell(n.value)
        return (b + "." + n.attr) if b else n.attr
    if isinstance(n, ast.Call):
        return _spell(n.func)
    return None


def _norm_dump(fn):
    """函数体的归一化指纹（不含函数名/参数/装饰器 —— 只比**函数体**）。"""
    node = _Norm().visit(ast.parse(ast.unparse(fn)).body[0])
    node.name = "_"
    node.args = ast.arguments(posonlyargs=[], args=[], vararg=None,
                              kwonlyargs=[], kw_defaults=[], kwarg=None,
                              defaults=[])
    node.decorator_list = []
    node.returns = None
    for a in ast.walk(node):
        if isinstance(a, ast.FunctionDef):
            a.name = "_"
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _collect(path):
    """{可读名: 指纹}。类方法用 `Class.method`；重名的取第一个（见下）。"""
    src = io.open(path, encoding="utf-8", errors="replace").read()
    out = {}
    tree = ast.parse(src)

    def walk(body, prefix=""):
        for n in body:
            if isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + ".")
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = prefix + n.name
                if key not in out:
                    try:
                        out[key] = _norm_dump(n)
                    except Exception:
                        out[key] = "<解析失败>"
    walk(tree.body)
    return out


def _bare(name):
    return name.rsplit(".", 1)[-1]


def _by_bare(coll):
    out = {}
    for k, v in coll.items():
        out.setdefault(_bare(k), set()).add(v)
    return out


def _diff():
    """按**方法名**匹配（忽略类前缀）—— 见下方 ⚠️。"""
    old = _by_bare(_collect(OLD_MAIN))
    new = {}
    for base, _d, files in os.walk(NEW_PKG):
        for f in sorted(files):
            if f.endswith(".py"):
                for k, v in _by_bare(_collect(os.path.join(base, f))).items():
                    new.setdefault(k, set()).update(v)
    lines = []
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k), new.get(k)
        if o is not None and n is not None:
            # ⚠️ **子集语义, 不是集合相等**。同一个**方法名**在两版里可能对应多个函数:
            #    * 老版一个 `RootWidget` 里有 `__init__`, 新版 11 个类各有一个;
            #    * `_add_rtp_button` 新版有**两份**(占位 + 真身) —— 老版那份 14 行的真身
            #      必须能在新版里找到, 而新版多出的占位不该算差异。
            #    判据就是 1:1 的那个问题: **老版这份函数体, 新版里有没有?**
            #    `o <= n` 为假 ⇒ 老版那份在新版里**找不到** ⇒ 真漂移。
            #    (第一版用 `o != n`, 于是 `__init__`/`_add_rtp_button` 报**假阳**。)
            if not (o <= n):
                lines.append("DIFF       %-34s %s" % (k, _fp("|".join(sorted(n)))))
        elif o is not None:
            lines.append("ONLY_OLD   %-34s %s" % (k, _fp("|".join(sorted(o)))))
        else:
            # ⚠️⚠️ **新版独有的也必须冻指纹**。第一版这里只记名字, 于是
            #    `Game._controls`(老版叫 `RootWidget._set_controls_enabled`, **名字对不上**)
            #    整条落在这一类、**函数体从不比对** —— 而重构的主战场恰恰是这些"改了名的函数"。
            #    实测: 往里注入一条 `_emit("restyle_buttons")`, 第一版门禁**照样全绿**。
            lines.append("ONLY_NEW   %-34s %s" % (k, _fp("|".join(sorted(n)))))
    return lines


class _Blank(ast.NodeTransformer):
    """把所有**标识符**抹成 `_` —— 只留 AST 形状。

    ⚠️ 必须在 **AST 节点上**做, 不能对 `ast.dump` 的**文本**用正则: dump 是嵌套的
       (`Attribute(value=Attribute(value=Name('self'), attr='game'), attr='state')`),
       正则从左往右扫会先命中内层、抹不干净 —— 第一版就是这么写的, 结果
       NS 只报 3 条(且那 3 条的"老独有/新独有"都是空的)、STRUCT 高报到 86 条。
    """

    def visit_Name(self, n):
        n.id = "_"
        return n

    def visit_Attribute(self, n):
        self.generic_visit(n)
        n.attr = "_"
        return n

    def visit_arg(self, n):
        self.generic_visit(n)
        n.arg = "_"
        return n

    def visit_keyword(self, n):
        self.generic_visit(n)
        if n.arg:
            n.arg = "_"
        return n

    def visit_alias(self, n):
        self.generic_visit(n)
        n.name = n.asname = "_"
        return n

    def visit_Global(self, n):
        n.names = ["_"] * len(n.names)
        return n

    visit_Nonlocal = visit_Global

    def visit_FunctionDef(self, n):
        self.generic_visit(n)
        n.name = "_"
        return n

    visit_AsyncFunctionDef = visit_FunctionDef


def _shape_of(fn):
    """AST 级形状指纹 —— 把所有标识符抹平后 dump。"""
    node = _Norm().visit(ast.parse(ast.unparse(fn)).body[0])
    node.args = ast.arguments(posonlyargs=[], args=[], vararg=None,
                              kwonlyargs=[], kw_defaults=[], kwarg=None,
                              defaults=[])
    node.decorator_list = []
    node.returns = None
    return ast.dump(_Blank().visit(node), annotate_fields=False,
                    include_attributes=False)


def _collect_shapes(path):
    out = {}
    tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())

    def walk(body):
        for n in body:
            if isinstance(n, ast.ClassDef):
                walk(n.body)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    out.setdefault(n.name, set()).add(_shape_of(n))
                except Exception:
                    pass
    walk(tree.body)
    return out


def _names(dump):
    import re as _re
    return set(_re.findall(r"Name\('([^']+)'", dump))


def classify():
    """把 `DIFF` 分成两类, 并给出机器可读的别名候选。

    * **NS**   —— 抹平标识符后形状**完全相同** ⇒ 只差名字 ⇒ **自动产出别名对**;
    * **STRUCT** —— 形状就不同 ⇒ 真有增删改 ⇒ **只这一类需要人看**。

    ⚠️ 这是把这条闸从"冻结 158 条清单"改成"**白名单 + 分类**"的关键一步:
       人只需要审 STRUCT 那一小撮, 而 NS 的对子是机器给的, 不用从自然语言里猜。
    """
    oldB = _by_bare(_collect(OLD_MAIN))
    newB = {}
    for base, _d, files in os.walk(NEW_PKG):
        for f in sorted(files):
            if f.endswith(".py"):
                for k, v in _by_bare(_collect(os.path.join(base, f))).items():
                    newB.setdefault(k, set()).update(v)
    oldS = _collect_shapes(OLD_MAIN)
    newS = {}
    for base, _d, files in os.walk(NEW_PKG):
        for f in sorted(files):
            if f.endswith(".py"):
                for k, v in _collect_shapes(os.path.join(base, f)).items():
                    newS.setdefault(k, set()).update(v)

    ns, st = [], []
    for k in sorted(set(oldB) & set(newB)):
        if oldB[k] <= newB[k]:
            continue
        o = sorted(oldB[k])[0]
        # 形状(抹平标识符后)在新版里找得到 ⇒ **只差名字**
        if (oldS.get(k) or set()) & (newS.get(k) or set()):
            a, b = _names(o), set()
            for nd in sorted(newB[k]):
                b |= _names(nd)
            ns.append((k, sorted(a - b)[:4], sorted(b - a)[:4]))
        else:
            st.append(k)
    return ns, st


def _fp(dump):
    import hashlib
    return hashlib.sha1(dump.encode("utf-8")).hexdigest()[:10]


def main():
    if not os.path.exists(OLD_MAIN):
        print("门禁结果: 红 —— 读不到老版 %s（\"没比\"不等于\"通过\"）" % OLD_MAIN)
        return 1
    got = _diff()
    if "--classify" in sys.argv:
        ns, st = classify()
        print("== NS（只差名字, 机器给出别名候选）== %d 条" % len(ns))
        for k, a, b in ns[:40]:
            print("  %-28s 老独有 %-30s 新独有 %s" % (k, ",".join(a)[:30], ",".join(b)[:34]))
        print("\n== STRUCT（形状就不同, 要人看）== %d 条" % len(st))
        for k in st: print("  %s" % k)
        return 0
    if "--freeze" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        # ⚠️⚠️ `newline="\n"` **不能省**: Windows 上 text mode 会把 `\n` 转成 `\r\n`,
        #     而 `.gitattributes` 规定 `* text=auto eol=lf` ⇒ 下次 checkout 内容就变了。
        #     实测踩过: 2026-09-19 重采一次基线, 工作区是 161 个 CRLF 而索引是 LF
        #     (`git status` 当场报 "CRLF will be replaced by LF"), 那一版基线等于埋了个雷。
        #     `tests/trace.py:1084` 早就写了 `newline="\n"`, 这里是补上。
        io.open(GOLDEN, "w", encoding="utf-8", newline="\n").write("\n".join(got) + "\n")
        print("已采基线: %s（%d 条）" % (GOLDEN, len(got)))
        for l in got:
            print("   %s" % l)
        return 0
    if not os.path.exists(GOLDEN):
        print("门禁结果: 红 —— 基线 %s 不存在（\"没比\"不等于\"通过\"）" % GOLDEN)
        return 1
    want = [l for l in io.open(GOLDEN, encoding="utf-8").read().splitlines() if l.strip()]
    extra = [l for l in got if l not in want]
    missing = [l for l in want if l not in got]
    for l in got:
        print("  [%s] %s" % ("!!" if l in extra else "OK", l))
    print()
    print("函数体差异: 实得 %d 条 / 基线 %d 条" % (len(got), len(want)))
    if extra or missing:
        print("门禁结果: 红")
        for l in extra:
            print("   + 多出来(= 新版真漂了, 或基线该更新): %s" % l)
        for l in missing:
            print("   - 少掉了(= 门禁被削弱, 或有人改了老版): %s" % l)
        return 1
    print("门禁结果: 全绿 —— %d 处函数体差异与基线**逐条相同**（已知改名之外没有漂移）"
          % len(got))
    return 0


if __name__ == "__main__":
    sys.exit(main())
