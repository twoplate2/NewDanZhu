"""函数签名门禁 —— `ast_parity.py` **看不见的那一维**。

    python tests/sig_parity.py

## 这条门禁补的是什么洞

`tests/ast_parity.py` 的 `_norm_dump` 里有这么一句：

    node.args = ast.arguments(posonlyargs=[], args=[], ...)   # 把**参数表整个抹掉**

⇒ 它比的是**函数体**，**签名一个字都不比**。
而"参数个数 / 默认值被改了一个数"正是本工程反复栽的那一类：
根目录 `CLAUDE.md` 记着实测 4 处注释与代码不符（`MISFIRE_V_MAX` 注释 980 实际 935、
`PEG_BOUNCE_VY_MAX` 注释 300 实际 280…）—— 全是**数值**，而默认参数值是同一类东西。

## 判据

逐个同名函数比**签名的规范化拼写**（参数名 + 默认值 `ast.unparse`），
差异集合必须与 `tests/golden/sig_diff.txt` **逐条相同**（与 `ast_parity.py` 同套路）。

⚠️ 首采基线时逐条核过 5 条差异，都是**良性**：
  · `_collide_arc`：老版 `frame=_ARC_FRAME` 是**冻死的默认值**（`def` 那行就求值，
    把逐帧递增的计数冻成 import 时刻的 0）—— 新版**删掉它是修 bug**，
    且唯一调用点两版都显式传实参 ⇒ 行为等价（已记在 `physics.py:176` 的注释里）。
  · `_play_voice_sequence`：`gap=0.005` → `gap=ROUND_END_VOICE_GAP`（同值）。
  · `_ask_unlock_rtp` / `_show_easter_popup` / `_show_round_end`：UI 与 `Game` 的**同名两份**。
"""

import ast
import io
import os
import sys
import collections

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OLD_MAIN = os.path.join(os.path.dirname(ROOT), "android", "main.py")
NEW_PKG = os.path.join(ROOT, "danzhu")
GOLDEN = os.path.join(HERE, "golden", "sig_diff.txt")
GOLDEN_C = os.path.join(HERE, "golden", "const_diff.txt")

FAIL = []
N_OK = [0]


def check(ok, name, detail=""):
    if ok:
        N_OK[0] += 1
    else:
        FAIL.append(name)
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                            ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def _sigs(path):
    out = collections.defaultdict(set)

    def w(body):
        for n in body:
            if isinstance(n, ast.ClassDef):
                w(n.body)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = n.args
                parts = [x.arg for x in list(a.posonlyargs) + list(a.args)
                         + list(a.kwonlyargs)]
                for d in list(a.defaults) + [x for x in a.kw_defaults if x is not None]:
                    try:
                        parts.append("=" + ast.unparse(d))
                    except Exception:
                        parts.append("=?")
                if a.vararg:
                    parts.append("*" + a.vararg.arg)
                if a.kwarg:
                    parts.append("**" + a.kwarg.arg)
                out[n.name].add(",".join(parts))
    w(ast.parse(io.open(path, encoding="utf-8", errors="replace").read()).body)
    return out


def _consts(path):
    """模块级(含类级)大写常量的 `名字 -> {右值拼写}`。"""
    out = collections.defaultdict(set)
    t = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())

    def w(body):
        for n in body:
            if isinstance(n, ast.ClassDef):
                w(n.body)
            elif isinstance(n, ast.Assign):
                for x in n.targets:
                    if isinstance(x, ast.Name) and x.id.isupper():
                        try:
                            out[x.id].add(ast.unparse(n.value))
                        except Exception:
                            pass
    w(t.body)
    return out


def _diff():
    old = _sigs(OLD_MAIN)
    new = collections.defaultdict(set)
    for base, _d, files in os.walk(NEW_PKG):
        if "__pycache__" in base:
            continue
        for f in sorted(files):
            if f.endswith(".py"):
                for k, v in _sigs(os.path.join(base, f)).items():
                    new[k] |= v
    lines = []
    for k in sorted(set(old) & set(new)):
        # ⚠️ **交集非空就算过** —— 同名两份(UI/Game)只要有一份对得上即可,
        #    与 `ast_parity._diff` 的"子集语义"同一个道理。
        if not (old[k] & new[k]):
            lines.append("SIG  %-30s 老 %s | 新 %s"
                         % (k, ",".join(sorted(old[k]))[:46],
                            ",".join(sorted(new[k]))[:46]))
    return lines


def _const_diff_lines():
    oc = _consts(OLD_MAIN)
    nc_ = collections.defaultdict(set)
    for base, _d, files in os.walk(NEW_PKG):
        if "__pycache__" in base:
            continue
        for f in sorted(files):
            if f.endswith(".py"):
                for k, v in _consts(os.path.join(base, f)).items():
                    nc_[k] |= v
    out = []
    for k in sorted(set(oc) & set(nc_)):
        if not (oc[k] & nc_[k]):
            out.append("CONST  %-26s 老 %s | 新 %s"
                       % (k, ",".join(sorted(oc[k]))[:40], ",".join(sorted(nc_[k]))[:44]))
    return out


def main():
    if not os.path.exists(OLD_MAIN):
        print("门禁结果: 红 —— 读不到老版（\"没比\"不等于\"通过\"）")
        return 1
    got = _diff()
    if "--freeze" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        # ⚠️ `newline="\n"` 不能省 —— 同 `ast_parity.py:401` 那条注释:
        #    Windows text mode 会把 `\n` 写成 `\r\n`, 与 `.gitattributes` 的 `eol=lf` 打架。
        io.open(GOLDEN, "w", encoding="utf-8", newline="\n").write("\n".join(got) + "\n")
        print("已采基线: %s（%d 条）" % (GOLDEN, len(got)))
        for l in got:
            print("   " + l)
        _cf = _const_diff_lines()
        io.open(GOLDEN_C, "w", encoding="utf-8", newline="\n").write("\n".join(_cf) + "\n")
        print("已采常量基线: %s（%d 条）" % (GOLDEN_C, len(_cf)))
        for l in _cf:
            print("   " + l)
        return 0
    if not os.path.exists(GOLDEN):
        print("门禁结果: 红 —— 基线 %s 不存在" % GOLDEN)
        return 1
    want = [l for l in io.open(GOLDEN, encoding="utf-8").read().splitlines() if l.strip()]
    extra = [l for l in got if l not in want]
    missing = [l for l in want if l not in got]
    for l in got:
        print("  [%s] %s" % ("!!" if l in extra else "OK", l))
    # ---- 模块级常量值（`ast_parity` 只比 `def`, 这一维它看不见） ----
    oc, nc_ = _consts(OLD_MAIN), collections.defaultdict(set)
    for base, _d, files in os.walk(NEW_PKG):
        if "__pycache__" in base:
            continue
        for f in sorted(files):
            if f.endswith(".py"):
                for k, v in _consts(os.path.join(base, f)).items():
                    nc_[k] |= v
    cdiff = []
    for k in sorted(set(oc) & set(nc_)):
        if not (oc[k] & nc_[k]):
            cdiff.append("CONST  %-26s 老 %s | 新 %s"
                         % (k, ",".join(sorted(oc[k]))[:40], ",".join(sorted(nc_[k]))[:44]))
    print("\n== 模块级常量（两版都有 %d 个）==" % len(set(oc) & set(nc_)))
    for l in cdiff:
        print("  " + l)
    wantc = []
    if os.path.exists(GOLDEN_C):
        wantc = [l for l in io.open(GOLDEN_C, encoding="utf-8").read().splitlines() if l.strip()]
    cex = [l for l in cdiff if l not in wantc]
    cmi = [l for l in wantc if l not in cdiff]
    check(not cex and not cmi,
          "常量值差异 %d 条与基线**逐条相同**（两版都有 %d 个常量）"
          % (len(cdiff), len(set(oc) & set(nc_))),
          "" if not cex and not cmi else "多 %r / 少 %r" % (cex[:2], cmi[:2]))

    check(not extra and not missing,
          "签名差异 %d 条与基线**逐条相同**（多 = 真漂了；少 = 门禁被削弱）" % len(got),
          "" if not extra and not missing
          else "多 %r / 少 %r" % (extra[:3], missing[:3]))
    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        return 1
    print("门禁结果: 全绿 —— 同名函数的**签名**（参数名 + 默认值）没有基线之外的漂移")
    return 0


if __name__ == "__main__":
    sys.exit(main())
