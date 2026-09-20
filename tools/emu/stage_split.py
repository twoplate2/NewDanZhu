# -*- coding: utf-8 -*-
"""按**阶段**拆开看 1%Low: 慢帧到底落在 飞行 / 装杯 / 落袋 / 蓄力 / 待机 的哪一段。

## 为什么需要它(而不是只看一个 1%Low 数字)

`ab_compare.py` 告诉我们"两组差多少", **不告诉我们差在哪**。
2026-09-20 定位装杯那条支线时就吃过亏: 我先用"超标率"当判据得出"病灶在落袋",
而**真正占掉最慢 59 帧里 36~41 帧的是装杯**(装杯占 46% 的帧)。
⇒ 阶段拆分是把"版本差"翻译成"哪段代码"的那一步。

## 判据怎么读

`p99ms` = 该阶段帧间隔的 99 分位(**同阶段内部比**, 不受该阶段占比影响)。
· 两组同一阶段的 `p99ms` 差得多 ⇒ 差异**就在这段代码**里
· 各阶段都差不多、只有占比不同 ⇒ 差在"哪段出现得多", 不在"哪段慢"

跑法:  python tools/emu/stage_split.py v69_r v122_r
"""
import glob
import io
import os
import re
import statistics as st

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "temp")


def load(prefix):
    files = sorted(glob.glob(os.path.join(HERE, "_emu_%s*.txt" % prefix)),
                   key=lambda p: int(re.search(r"(\d+)\.txt$", p).group(1)))
    out = []
    for f in files:
        fr = []
        for l in io.open(f, encoding="utf-8", errors="replace"):
            if not l.strip() or l.startswith("#"):
                continue
            r = l.rstrip("\n").split(",")
            try:
                fr.append((float(r[0]), r[1]))
            except Exception:
                pass
        if fr:
            out.append((os.path.basename(f), fr))
    return out


def pct(v, q):
    if not v:
        return 0.0
    s = sorted(v)
    i = min(len(s) - 1, int(len(s) * q))
    return s[i]


def main():
    import sys
    groups = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not groups:
        print("用法: python tools/emu/stage_split.py <前缀> [<前缀2> ...]")
        return 1

    STAGES = ["飞行", "装杯", "落袋", "蓄力", "待机"]
    res = {}
    for gp in groups:
        rs = load(gp)
        if not rs:
            print("!! 没找到 _emu_%s*.txt" % gp)
            continue
        print("========== 组 %s（%d 轮）==========" % (gp, len(rs)))
        print("%-6s %8s %8s %8s %8s %8s" % ("阶段", "帧数占比", "中位ms", "p95ms", "p99ms", "最慢ms"))
        # 每轮每阶段算一次, 再取**轮间中位**(不让帧数多的轮压过帧数少的轮)
        agg = {}
        for stg in STAGES:
            meds, p95s, p99s, maxs, shares = [], [], [], [], []
            for _nm, fr in rs:
                g = [d for d, s in fr if s == stg]
                if not g:
                    continue
                meds.append(st.median(g))
                p95s.append(pct(g, 0.95))
                p99s.append(pct(g, 0.99))
                maxs.append(max(g))
                shares.append(100.0 * len(g) / len(fr))
            if meds:
                agg[stg] = (st.median(meds), st.median(p95s), st.median(p99s),
                            st.median(maxs), st.median(shares))
                print("%-6s %7.1f%% %8.2f %8.2f %8.2f %8.2f"
                      % (stg, agg[stg][4], agg[stg][0], agg[stg][1], agg[stg][2], agg[stg][3]))
        # 最慢的 1% 落在哪个阶段(每轮各算, 再汇总)
        drop = {}
        for _nm, fr in rs:
            fr2 = sorted(fr, key=lambda x: -x[0])
            n1 = max(1, int(len(fr2) * 0.01))
            for _d, s in fr2[:n1]:
                drop[s] = drop.get(s, 0) + 1
        tot = sum(drop.values()) or 1
        print("   最慢 1%% 帧的阶段归属: " +
              " · ".join("%s %.0f%%" % (s, 100.0 * drop.get(s, 0) / tot) for s in STAGES if drop.get(s)))
        res[gp] = agg
        print()

    if len(res) == 2:
        (ga, aa), (gb, ab) = list(res.items())
        print("========== 逐阶段 A/B（p99ms）==========")
        print("%-6s %10s %10s %10s" % ("阶段", ga, gb, "差"))
        for stg in STAGES:
            if stg in aa and stg in ab:
                d = ab[stg][2] - aa[stg][2]
                flag = "  ←← 新版更高" if d > 0.3 else ("  ← 老版更高" if d < -0.3 else "  差不多")
                print("%-6s %10.2f %10.2f %+10.2f%s" % (stg, aa[stg][2], ab[stg][2], d, flag))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
