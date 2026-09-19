# -*- coding: utf-8 -*-
"""APK 打包边界复核 —— 拿**构建出来的 APK 实物**验证「不该进包的东西真的没进」。

⚠️ 为什么需要它（它补的是 `tests/pack_scope.py` 够不着的那一半）:

   `tests/pack_scope.py` 只在**本机做静态判定** —— 它比的是「CI 的剥离清单」与「目录清单」，
   证明的是「CI 里确实写了那一步 `rm -rf`」。
   它**证明不了**「p4a 真的没把那 833.7 KB 收进去」—— 那要**真的构建一次**才看得见。

   ⇒ 本脚本就是那一步的另一半：**解 APK 的 `assets/private.tar`，看里面到底有什么。**

   背景见 `changelog/2026-09-19-体积拆解.md` 与 `changelog/2026-09-19.md` 第 20 条。

跑法:
    python tools/check_apk_scope.py                  # 自动找 bin/*.apk（取最新）
    python tools/check_apk_scope.py 某个.apk          # 指定文件
    python tools/check_apk_scope.py --list           # 顺带列出 private.tar 的全部条目

判据（两条，缺一不可）:
    R1. **不许出现** `tests/` `tools/` `changelog/` 下的任何东西 —— 它们运行时不参与，
        但会被 `source.include_exts = py` 按扩展名收全树（这是实测过的，见下）。
    R2. **必须出现** `danzhu/` 下的 .pyc —— 否则说明剥离清单误伤了出货代码，
        那个 APK 是**起不来**的。
"""

import io
import os
import sys
import tarfile
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# R1：这些前缀下的**任何**条目都不该在包里
FORBIDDEN = ("tests/", "tools/", "changelog/", "temp/", "book/")
# R2：出货代码的落点（p4a 会把 app 源码编译成 .pyc 打进 private.tar）
REQUIRED_PREFIX = "danzhu/"


def pick_apk(argv):
    for a in argv:
        if a.endswith(".apk"):
            return a
    cands = []
    for d in ("bin", ".", "output"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            cands += [os.path.join(p, f) for f in os.listdir(p) if f.endswith(".apk")]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def main(argv):
    show_list = "--list" in argv
    apk = pick_apk([a for a in argv if not a.startswith("--")])
    if apk is None:
        print("[红] 找不到 .apk —— 先构建一次（或把路径当参数传进来）")
        print("     本机 CI 流程: `buildozer android debug` 的产物在 `bin/*.apk`")
        return 1
    if not os.path.isabs(apk):
        apk = os.path.join(os.getcwd(), apk)
    if not os.path.exists(apk):
        print("[红] 文件不存在: %s" % apk)
        return 1

    print("APK: %s（%.2f MB）" % (apk, os.path.getsize(apk) / 1048576))
    try:
        zf = zipfile.ZipFile(apk)
    except Exception as e:
        print("[红] 打不开（不是 zip？）: %r" % (e,))
        return 1

    names = zf.namelist()
    if "assets/private.tar" not in names:
        print("[红] APK 里没有 `assets/private.tar` —— 这不像 p4a 打出来的包")
        print("     （前 12 个条目: %s）" % ", ".join(names[:12]))
        return 1

    blob = zf.read("assets/private.tar")
    print("`assets/private.tar`: %.2f MB（未压缩）" % (len(blob) / 1048576))

    try:
        tf = tarfile.open(fileobj=io.BytesIO(blob))
        members = tf.getmembers()
    except Exception as e:
        print("[红] private.tar 解不开: %r" % (e,))
        return 1

    files = [m for m in members if m.isfile()]
    paths = [m.name.lstrip("./") for m in files]

    # ── 构成汇总（按顶层目录）────────────────────────────────────────────
    agg = {}
    for m in files:
        top = m.name.lstrip("./").split("/")[0] or "(根)"
        a = agg.setdefault(top, [0, 0])
        a[0] += 1
        a[1] += m.size
    print("\n== private.tar 构成（按顶层）==")
    for top, (n, sz) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print("   %-28s %5d 个  %10.1f KB" % (top, n, sz / 1024))

    pyc = [m for m in files if m.name.endswith(".pyc")]
    app_pyc = [m for m in pyc if m.name.lstrip("./").startswith(REQUIRED_PREFIX)]
    print("\n== Python 字节码 ==")
    print("   .pyc 总数 %d 个 / %.1f KB" % (len(pyc), sum(m.size for m in pyc) / 1024))
    print("   其中出货代码(`%s`) %d 个 / %.1f KB"
          % (REQUIRED_PREFIX, len(app_pyc), sum(m.size for m in app_pyc) / 1024))
    if app_pyc:
        for m in sorted(app_pyc, key=lambda m: -m.size)[:8]:
            print("      %9.1f KB  %s" % (m.size / 1024, m.name.lstrip("./")))

    # ── 判据 ────────────────────────────────────────────────────────────
    leaked = sorted(p for p in paths if p.startswith(FORBIDDEN))
    print("\n== 判据 ==")
    ok = True

    if leaked:
        ok = False
        print("   [红] R1 失败：不该进包的东西**在包里**，共 %d 个：" % len(leaked))
        for p in leaked[:15]:
            print("        %s" % p)
        if len(leaked) > 15:
            print("        …（还有 %d 个）" % (len(leaked) - 15))
        print("        ⇒ 检查 CI 的 `run: rm -rf tests temp tools book changelog` 那一步还在不在")
    else:
        print("   [绿] R1：`%s` 下的东西**一个都没进包**"
              % "` `".join(x.rstrip("/") for x in FORBIDDEN))

    if not app_pyc:
        # ⚠️ 老版（`android/`）打出来的包里是**单个 `main.pyc`**（22,347 行全在那一个文件里），
        #    新版才是 `danzhu/` 包结构。拿老版 APK 来跑本脚本会撞上这条 —— 那不是缺陷，
        #    是**用错了对象**。所以这里先认一下，别误报成红。
        legacy = any(p.endswith("main.pyc") for p in paths)
        if legacy:
            print("   [—] R2 **不适用**：这个包里是单个 `main.pyc` ⇒ 它是**老版**（`android/`）的包")
            print("       新版是 `danzhu/` 包结构，出货代码会落成 `danzhu/**.pyc`。")
            print("       ⚠️ 本脚本判的是**新版**的包；老版那个不用它判（老版的边界问题见"
                  " `changelog/2026-09-19.md` 第 20 条：`sim_balance.pyc` 就是那么进去的）。")
        else:
            ok = False
            print("   [红] R2 失败：包里**没有** `%s` 下的 .pyc ⇒ 出货代码没进去，这个 APK 起不来"
                  % REQUIRED_PREFIX)
            print("        ⇒ 检查剥离清单是不是误伤了 `danzhu/`（`tests/pack_scope.py` 会先拦住这种）")
    else:
        print("   [绿] R2：出货代码进去了（`%s` 下 %d 个 .pyc）" % (REQUIRED_PREFIX, len(app_pyc)))

    if show_list:
        print("\n== private.tar 全部条目（%d）==" % len(paths))
        for p in sorted(paths):
            print("   %s" % p)

    print()
    if ok:
        print("✅ 打包边界复核通过 —— 这个 APK 里只有该有的东西")
        print("   （把上一行连同 APK 文件名记进 `changelog/2026-09-19.md` 第 20 条的「待云构建复核」）")
        return 0
    print("❌ 打包边界复核**未通过** —— 见上面的 [红] 行")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
