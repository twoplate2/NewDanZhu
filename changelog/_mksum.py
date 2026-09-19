"""给「出货文件」拍一张 sha256 指纹表 —— 没有 git 时，用它回答「哪些文件被改过」。

    python changelog/_mksum.py            # 重新生成 baseline.sha256
    python changelog/_mksum.py --check    # 与基线比对，列出 变动/新增/缺失

⚠️ 这不是版本控制，是**探针**：它只能告诉你"变了"，不能告诉你"变成什么"、更不能回退。
   真正的回退见 `changelog/README.md` 里那节「和 git 的分工」。
"""
import hashlib
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "baseline.sha256")

# 只覆盖**出货**文件；tests/ 与 temp/ 不在此列（它们是验收台，不随构建发出去）
GLOBS = ("danzhu", "main.py")


def walk():
    for g in GLOBS:
        p = os.path.join(ROOT, g)
        if os.path.isfile(p):
            yield p
            continue
        for base, dirs, fs in os.walk(p):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(fs):
                if f.endswith(".py"):
                    yield os.path.join(base, f)


def h(p):
    d = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            d.update(chunk)
    return d.hexdigest()


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


cur = {rel(p): h(p) for p in sorted(walk())}

if "--check" in sys.argv:
    if not os.path.exists(OUT):
        print("没有基线：先跑 `python changelog/_mksum.py`")
        sys.exit(1)
    old = {}
    for l in io.open(OUT, encoding="utf-8"):
        l = l.strip()
        if l and not l.startswith("#"):
            a, b = l.split(None, 1)
            old[b.strip()] = a
    chg = [k for k in cur if k in old and old[k] != cur[k]]
    new = [k for k in cur if k not in old]
    gone = [k for k in old if k not in cur]
    print("比对 %d 个文件：" % len(cur))
    for k in chg:
        print("  [改了] %s" % k)
    for k in new:
        print("  [新增] %s" % k)
    for k in gone:
        print("  [没了] %s" % k)
    if not (chg or new or gone):
        print("  与基线**逐文件相同**")
    sys.exit(1 if (chg or gone) else 0)

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("# 出货文件的 sha256 指纹（`python changelog/_mksum.py` 生成；`--check` 比对）\n")
    for k, v in cur.items():
        f.write("%s  %s\n" % (v, k))
print("已写 %s：%d 个文件" % (rel(OUT), len(cur)))
