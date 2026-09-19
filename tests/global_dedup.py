"""跨模块重复定义门禁 —— 同一个私有名字在多个模块被**各自定义**了一遍。

⚠️ 为什么需要它（这不是洁癖）: `_boot_log` / `_BOOT_LOG` / `_PROBE_TRACE` / `_PROBE_COST`
   这一组是**跨模块共享状态**，端口时被 audio 与 platform 各建了一份。两份**不报错**，
   只让导出的启动日志静默少掉一半行 —— 而读它的只有"保存加载日志"那一处导出，
   没有任何测试会对账它。同类灾难还有 `_FRAME_CALLS` / `_JIT` 式的计数器、
   任何 `[0]` 单元素列表当可变全局用的东西。

判据刻意做窄（宁可漏，不可吵）: 只报
  · 在 ≥2 个模块里是**定义**（不是 import 进来的）；
  · 值是**可变字面量**（`[]` / `{}` / `[0]` / `set()` 之类）或 `def` 出来的函数。

后一条是关键: 不可变的私有常量（`_TW_GROW_MAX = 1.6`）各写一份只是重复，不会分叉；
可变对象各写一份才是**静默分叉**。

⚠️⚠️ **判据里曾经只看 `_` 开头的名字, 那漏掉了 `SFX_MIN_SP`** —— 它是全大写, 在
   `game.py` 与 `audio/bus.py` 各有一份, 而**两份内容不同**(game 那份漏了 `EV_ARC: 1e9`)。
   对账当时是绿的(EV_ARC 从不查那张表), 所以能一直漂下去。⇒ 现在**不限前缀**,
   只要"可变字面量 + 多模块各自定义"就报 —— 名字大小写与风险无关。

跑法: python tests/global_dedup.py
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
PKG = os.path.join(ROOT, "danzhu")

# 允许各写一份的白名单: (名字, 为什么)
ALLOW = {
    # 这两个是"每个模块各自的 import", 不是共享状态(backend 那份已归一, 见 audio/backend.py)
    "_detect_platform",
}


def is_mutable_literal(node):
    """`[]` / `{}` / `[0.0, 0]` / `set()` / `dict()` —— 可变字面量与它的显式构造。"""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("set", "dict", "list", "bytearray"):
        return True
    return False


def module_defs(path):
    """{名字: 是否可变/函数} —— 只看**模块级定义**, 不看 import 进来的名字。"""
    with io.open(path, encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError as e:
            print("[红] 解析失败 %s: %s" % (os.path.relpath(path, ROOT), e))
            return None
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            val = node.value
            flag = is_mutable_literal(val) or (
                isinstance(val, ast.Call) and not is_mutable_literal(val))
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if names and flag:
                for nm in names:
                    out[nm] = "赋值"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = "函数"
        elif isinstance(node, ast.ClassDef):
            out[node.name] = "类"
    return out


def main():
    seen = {}          # 名字 -> [(相对路径, 种类)]
    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            defs = module_defs(p)
            if defs is None:
                return 1
            rel = os.path.relpath(p, ROOT)
            for nm, kind in defs.items():
                # ⚠️ **不限前缀** —— 见抬头: 只看下划线漏掉过 `SFX_MIN_SP`。
                if nm in ALLOW:
                    continue
                seen.setdefault(nm, []).append((rel, kind))

    dupes = {nm: v for nm, v in seen.items() if len(v) >= 2}
    if not dupes:
        print("[绿] 跨模块重复定义: 没有共享状态被各建一份")
        return 0
    print("[红] 跨模块重复定义: %d 个名字在多个模块各定义了一份" % len(dupes))
    print("     ⚠️ 可变对象各一份 = 静默分叉(写进这份, 读那份) —— 归一到一个模块再 import。")
    for nm in sorted(dupes):
        where = ", ".join("%s(%s)" % (p, k) for p, k in dupes[nm])
        print("      %-24s %s" % (nm, where))
    return 1


if __name__ == "__main__":
    sys.exit(main())
