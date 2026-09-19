"""静态检查门禁 —— 只报**真问题**：未定义名 / 语法错 / 重复定义 / 未用的 global。

⚠️ 为什么需要它：这是一个 ~20,000 行的端口工程，跨模块 import 的接缝是**最容易错、
   又最难测**的地方 —— 我把 `_brk_add` 从 `platform/device.py` 归一出去之后，
   `ui/game_area.py` 与 `ui/winfx.py` 还指着旧地方 import，两个模块直接 ImportError，
   而**已有的五道门禁一条都没红**（它们不导入 UI 层）。这类错必须由静态检查兜住。

⚠️ 故意**不报未用导入**：本工程有一批"导入即再导出"的用法（`audio/backend.py` 把
   `_PROBE_TRACE` / `_PROBE_COST` / `_BOOT_LOG` 从 `platform/boot.py` 引进来，
   好让 `bus.py` 能写 `B._PROBE_TRACE`）。那是有意的，报了就是假阳性。

⚠️ pyflakes 没装时**明确报"未运行"并返回非零** —— 一条静默跳过的门禁等于一条假绿，
   而假绿正是本工程最防的东西。

跑法: python tests/lint_gate.py
"""

import os
import re
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 只报这四类。**不要**把 "imported but unused" 加进来(见抬头说明)。
KEEP = re.compile(r"undefined name|syntax error|redefinition|unused: name is never assigned")

# ⚠️ `tools/` 也扫: 它是 PC 上的开发工具(语音再生成 / 余额模拟器), **不进设备**,
#    但同样是本工程的代码 —— 未定义名一样要在桌面就红, 别等到用它那天才发现。
TARGETS = ["danzhu", "main.py", "tests", "tools"]


def import_check():
    """**运行期导入检查** —— 逐个 import `danzhu.*`，报出失败的。

    ⚠️ 为什么 pyflakes 不够: 它**不检查被导入的名字在目标模块里是否真的存在**。
       实测踩到过两次 —— `winfx.py` 从 `platform/device.py` 导入已经搬走的 `_brk_add`
       (ImportError), `bench.py` 导入根本没端口的 `_bench_menu_desc`(ImportError)。
       两处 pyflakes 都是绿的, 而**整个 UI 层根本 import 不进去**。
    ⚠️ 会有 Kivy 窗口起来的副作用, 所以放在最后跑, 且只跑一次。
    """
    import importlib
    import pkgutil

    if ROOT not in sys.path:          # 本脚本以 `python tests/lint_gate.py` 跑, sys.path[0] 是 tests/
        sys.path.insert(0, ROOT)
    import danzhu
    mods = ["danzhu"]
    for _f, name, _p in pkgutil.walk_packages(danzhu.__path__, "danzhu."):
        mods.append(name)
    bad = []
    for name in mods:
        try:
            importlib.import_module(name)
        except Exception as e:
            bad.append("%s -> %s: %s" % (name, type(e).__name__, e))
    if not bad:
        print("[绿] 运行期导入: %d 个模块全部导入成功" % len(mods))
        return 0
    print("[红] 运行期导入: %d 个模块导入失败" % len(bad))
    for b in bad:
        print("      " + b)
    return 1


def main():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        print("[红] 静态检查**未运行**: 没装 pyflakes")
        print("     安装: pip install pyflakes")
        print("     ⚠️ 静默跳过 = 假绿, 所以这里当失败处理。")
        return 2
    proc = subprocess.run([sys.executable, "-m", "pyflakes"] + TARGETS,
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    hits = [l for l in (proc.stdout or "").splitlines() if KEEP.search(l)]
    if hits:
        print("[红] 静态检查: %d 条 —— 断言前先确认它们不是刻意的" % len(hits))
        for l in hits:
            print("      " + l)
        return 1
    print("[绿] 静态检查: 无未定义名 / 无重复定义 / 无未用 global")
    return import_check()


if __name__ == "__main__":
    sys.exit(main())
