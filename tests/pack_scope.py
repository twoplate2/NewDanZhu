"""打包边界门禁 —— 会被 buildozer 打进 APK 的 .py，必须**恰好**是「该进包的那几个」。

⚠️ 规则出处（**别自己猜，照抄**）: `buildozer 1.5.0` 的 `_copy_application_sources`。
   本工程早先算过一次（`temp/_shipcheck.py`），规则是：
     ① `os.walk(source.dir)` **全树**；
     ② **跳过任何隐藏路径段**（`.` 开头的目录/文件 —— 所以 `.github/` 天然不进包）；
     ③ 按 `source.include_exts` 过扩展名；
     ④ ⚠️ **没有扩展名的文件也会被收**（源码里是 `if ext:` 才判扩展名）。
   本文件原来把机制写成「buildozer 不过滤、p4a 收全树」—— **那是错的**，已按上表更正。

⚠️ 为什么需要它（这不是洁癖）:
   规则 ③ 是全树按扩展名收 ⇒ 仓库根下**任何** `.py` 都进 APK。
   这不是推测。老版 APK 的 `assets/private.tar` 里躺着 `sim_balance.pyc`(29.3 KB)
   与 `bench_no_preflight.pyc`(45.8 KB) —— 两个跟游戏运行毫无关系、纯 PC 工具脚本。

   本工程实测白进包的字节码（`temp/probe_bytes.py` 真编译量的）:
     `tests/` 781.6 KB + `tools/` 46.0 KB + `changelog/_mksum.py` 6.2 KB = **833.7 KB**
   —— 出货代码本身才 1252.5 KB，白扔的是它的 67%。

   ⇒ CI 里加了一步 `rm -rf` 把它们剥掉。**本门禁守的就是那一步**:
     ① 那一步被删了 ⇒ 红
     ② 新加了一个含 .py 的目录、却没列进剥离清单 ⇒ 红
     ③ 剥离清单误伤了出货代码(`danzhu` / `p4a` / `main.py`) ⇒ 红
     ④ **根目录多了个没有扩展名的文件** ⇒ 红（按规则 ④，那个也进包 —— `x` 就是这种东西）

⚠️⚠️ **本门禁只做本机静态判定** —— 它比的是「CI 的剥离清单」与「工作区实际的目录清单」。
   **不等于**真机构建验证过。那一半交给 `tools/check_apk_scope.py`（解 APK 实物来看）。
   别把这条绿当成"包真的小了"。

跑法: python tests/pack_scope.py
"""

import glob
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ✅ 允许进包的（其余一律必须出现在 CI 的剥离清单里）
ALLOW_DIRS = {"danzhu", "p4a"}      # p4a/hook.py 是 buildozer.spec 的 p4a.hook 指定的
ALLOW_ROOT_PY = {"main.py"}         # 入口
# 根目录允许存在的**无扩展名**文件（按规则 ④，其余无扩展名的都会被收进包）
ALLOW_ROOT_NOEXT = set()

SKIP_DIRS = {"__pycache__", ".git", ".buildozer", "bin"}

# 出货文件里**.py 之外**的扩展名（按 include_exts）—— 判「无扩展名」时用得上
INCLUDE_EXTS = {"py", "png", "jpg", "kv", "atlas", "ttf", "otf", "wav", "mp3"}


def is_hidden(rel):
    """规则 ②：路径里任何一段以 `.` 开头 ⇒ buildozer 跳过它。"""
    return any(seg.startswith(".") for seg in rel.split("/") if seg)


def source_dir():
    """从 buildozer.spec 读 `source.dir`（缺省 '.'）。"""
    path = os.path.join(ROOT, "buildozer.spec")
    if not os.path.exists(path):
        return None, "读不到 buildozer.spec"
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = re.match(r"\s*source\.dir\s*=\s*(\S+)", ln)
            if m:
                return m.group(1), None
    return ".", None


def candidates(sd):
    """按 buildozer 规则算出「会被收进包」的条目 → (顶层目录集, 根下 .py 集, 根下无扩展名集)。"""
    base = os.path.normpath(os.path.join(ROOT, sd))
    dirs, rootpy, rootnoext = set(), set(), set()
    if not os.path.isdir(base):
        return None, None, None, "source.dir 不存在: %s" % base
    for name in sorted(os.listdir(base)):
        rel = name
        if is_hidden(rel):                      # 规则 ②：隐藏的一律不收（.github/.gitignore 都是）
            continue
        p = os.path.join(base, name)
        if os.path.isdir(p):
            if name in SKIP_DIRS:
                continue
            for dp, dn, fs in os.walk(p):
                dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
                if any(f.endswith(".py") for f in fs):
                    dirs.add(name)
                    break
        else:
            ext = os.path.splitext(name)[1]
            if ext:
                if ext[1:].lower() == "py":
                    rootpy.add(name)
            else:
                rootnoext.add(name)             # 规则 ④：没有扩展名 ⇒ 也会进包
    return dirs, rootpy, rootnoext, None


def strip_list():
    """从 CI workflow 解析出 `rm -rf` 的清单 → (集合, 出处) 或 (None, 原因)。"""
    pats = sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")))
    if not pats:
        return None, "找不到 .github/workflows/*.yml"
    found = None
    for wf in pats:
        for ln in open(wf, encoding="utf-8"):
            m = re.search(r"run:\s*rm\s+-rf\s+(.+?)\s*$", ln)
            if m:
                if found is not None:
                    return None, "有**多个** `run: rm -rf` 步骤（%s），判据不唯一" % os.path.basename(wf)
                names = {t for t in m.group(1).split() if not t.startswith("#") and not t.startswith("-")}
                found = (names, os.path.basename(wf))
    if found is None:
        return None, "workflow 里**没有** `run: rm -rf …` 这一步 —— 开发目录会被白打进 APK"
    return found


def main():
    sd, err = source_dir()
    if err:
        print("[红] " + err)
        return 1
    dirs, rootpy, rootnoext, err = candidates(sd)
    if err:
        print("[红] " + err)
        return 1
    strip, where = strip_list()
    if strip is None:
        print("[红] 打包边界: " + where)
        return 1

    problems = []

    # ① 该剥没剥：会进包的目录里，既不在剥离清单、也不是允许进包的
    leaked = sorted(dirs - strip - ALLOW_DIRS)
    if leaked:
        problems.append(
            "这些目录含 .py ⇒ 会被 p4a 收进 APK，却**不在剥离清单**里: %s\n"
            "     ⇒ 要么在 CI 的 `rm -rf` 里补上，要么确认它**确实该进包**（那就加进 ALLOW_DIRS）"
            % ", ".join(leaked))

    # ② 根下多出来的 .py（同样会被收走）
    leaked_py = sorted(rootpy - strip - ALLOW_ROOT_PY)
    if leaked_py:
        problems.append(
            "仓库根下这些 .py 会被收进 APK，却不在允许清单里: %s\n"
            "     ⇒ 删掉它，或加进剥离清单，或确认该进包（加进 ALLOW_ROOT_PY）"
            % ", ".join(leaked_py))

    # ③ 剥离清单误伤出货代码
    hurt = sorted(strip & (ALLOW_DIRS | ALLOW_ROOT_PY))
    if hurt:
        problems.append(
            "剥离清单(`%s`)**误伤了出货代码**: %s\n"
            "     ⇒ 这会把游戏本体从包里删掉，构建出来的 APK 起不来" % (where, ", ".join(hurt)))

    # ④ ⚠️ **没有扩展名的文件也会进包**（规则 ④：源码里是 `if ext:` 才判扩展名）
    #    —— 仓库根曾经就躺着一个 `x`（游戏跑测试时落的存档 JSON），正是这一类。
    leaked_noext = sorted(rootnoext - ALLOW_ROOT_NOEXT)
    if leaked_noext:
        problems.append(
            "仓库根下这些**没有扩展名**的文件会被收进 APK: %s\n"
            "     ⇒ 按 buildozer 规则，无扩展名的文件不判 include_exts、照收不误。\n"
            "     ⇒ 要么删掉/移走（收进 `temp/` 也算），要么加进 ALLOW_ROOT_NOEXT 并写清为什么"
            % ", ".join(leaked_noext))

    # ④ 剥离清单里写了不存在的目录（粗心/改名残留）—— 只警告，不判红（rm -rf 对不存在安全）
    warn_missing = sorted(t for t in strip if not os.path.exists(os.path.join(ROOT, t)))

    if problems:
        print("[红] 打包边界: %d 项" % len(problems))
        for i, p in enumerate(problems, 1):
            print("  %d. %s" % (i, p))
        return 1

    print("[绿] 打包边界: 会进包的只有 %s + %s" % (
        ", ".join(sorted(ALLOW_DIRS)), ", ".join(sorted(ALLOW_ROOT_PY))))
    print("     剥离清单(`%s`): %s" % (where, ", ".join(sorted(strip))))
    print("     剥掉的目录: %s" % ", ".join(sorted(strip & dirs)))
    if warn_missing:
        print("     ⚠️ 剥离清单里有本机不存在的条目(在 CI 上也不存在就无害): %s"
              % ", ".join(warn_missing))
    print("     ⚠️ 本门禁只做静态判定 —— 「包里真的没有它们」要跑一次云构建解 private.tar 才算数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
