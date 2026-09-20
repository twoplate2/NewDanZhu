# -*- coding: utf-8 -*-
"""把**帧间隔**按日志的列拆开, 逐轮配对比 —— 回答「组间差的那 0.3ms 落在哪一列」。

## 为什么不能只看慢帧那几列

`ab_compare.py` 印的 `慢帧自算 / 慢帧主线程 / 慢帧等屏幕` 只统计**最慢那一批帧**
(中位数意义上的), 而我们要解释的是**帧间隔 p99 的差**。两者不是一回事:
一批帧慢可能是"它自己算得久", 也可能是"它等得久", 还可能是"它前面的帧算得久,
把相位推歪了"。

## 列的含义(`danzhu/ui/bench.py` 日志末行有自述)

    dt    帧间隔(ms)          = 这一帧距离上一帧

    stg   阶段
    tex   当帧有没有文字重建
    self_  _frame 自算(ms)     = 纯 Python 自己花掉的
    thr   主线程(ms)          = 整帧在主线程上的时间(含 Kivy 内部)
    snd   当帧发声次数
    vib   当帧震动次数
    sub   最大单个子步骤名
    sw    等屏幕(ms)          = swap 阻塞。**dt ≈ thr + sw 的大头**

## 判据

对每一列分别算「组间配对中位差」。谁把 0.3ms 吃掉了, 一眼能看出来:
· `thr` 抬了 ⇒ 我们的活变多了(改 Python 有用)
· `thr` 平、`sw` 抬了 ⇒ 等得久了(GPU/合成器/相位, 改 Python 没用)
· 两列都平而 `dt` 抬了 ⇒ 缺口在 `dt − thr − sw`, 那是 **Kivy 主动睡**(差额律那行)

跑法:  python tools/emu/frame_decomp.py v69_r v122_r
"""
import glob
import io
import os
import re
import statistics as st
import sys

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "temp")
# 日志列号 -> 名字。⚠️ 与 `bench.py` 末行那句自述**必须一致**, 改一处要改两处。
COLS = {0: "dt", 3: "self_", 4: "thr", 8: "sw"}


def load(prefix):
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "_emu_%s*.txt" % prefix)),
                    key=lambda p: int(re.search(r"_r(\d+)\.txt$", p).group(1))):
        rows = []
        for l in io.open(f, encoding="utf-8", errors="replace"):
            if l.startswith("#") or not l.strip():
                continue
            r = l.rstrip("\n").split(",")
            try:
                rows.append({n: float(r[i]) for i, n in COLS.items()})
            except Exception:
                pass
        if rows:
            out.append((os.path.basename(f), rows))
    return out


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * q))]


def summarise(rows):
    d = {}
    for n in COLS.values():
        v = [x[n] for x in rows]
        d[n] = (st.median(v), pct(v, 0.95), pct(v, 0.99), sum(v) / len(v))
    # 缺口 = dt − thr − sw。中位与 p99 各算一次(中位上它是 Kivy 主动睡的量)
    gap = [x["dt"] - x["thr"] - x["sw"] for x in rows]
    d["缺口"] = (st.median(gap), pct(gap, 0.95), pct(gap, 0.99), sum(gap) / len(gap))
    return d


VSYNC_MS = 1000.0 / 165.0          # 面板一格(这台模拟器实测恒为 165.0Hz)


def late_frames(rows):
    """错过 vsync 的帧: 统计**条数**(低方差) + 迟到的时间都花在哪一列。

    ⚠️ 判据用「≥1.5 格」而不是「≥中位×2」: 中位本身就是一格, 1.5 格 = 9.09ms,
       与 `★口径A` 那行(≥2×中位 = 12.12ms)是两个不同的问题 —— 前者问"迟到了多少帧",
       后者问"有没有长停顿"。迟到 1.5 格就已经丢了一格 vsync, 玩家看得见。
    """
    thr = [x["thr"] for x in rows if x["dt"] >= 1.5 * VSYNC_MS]
    sw = [x["sw"] for x in rows if x["dt"] >= 1.5 * VSYNC_MS]
    gp = [x["dt"] - x["thr"] - x["sw"] for x in rows if x["dt"] >= 1.5 * VSYNC_MS]
    late = [x["dt"] - VSYNC_MS for x in rows if x["dt"] >= 1.5 * VSYNC_MS]
    m = (lambda v: st.median(v) if v else 0.0)
    return dict(n=len(thr), thr=m(thr), sw=m(sw), gap=m(gp), late=m(late))


def main():
    groups = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(groups) != 2:
        print("用法: python tools/emu/frame_decomp.py <前缀A> <前缀B>")
        return 1
    ga, gb = groups
    ra, rb = load(ga), load(gb)
    if not ra or not rb:
        print("!! 两组都要有数据")
        return 1
    sa = {n: summarise(r) for n, r in ra}
    sb = {n: summarise(r) for n, r in rb}
    names = list(COLS.values()) + ["缺口"]

    print("逐轮「中位」明细（组 %s）" % ga)
    print("%-12s %8s %8s %8s %8s %8s" % ("轮", *names))
    for nm, r in ra:
        s = summarise(r)
        print("%-12s %8.2f %8.2f %8.2f %8.2f %8.2f"
              % (nm.replace("_emu_", "").replace(".txt", ""), *[s[n][0] for n in names]))
    print("逐轮「中位」明细（组 %s）" % gb)
    print("%-12s %8s %8s %8s %8s %8s" % ("轮", *names))
    for nm, r in rb:
        s = summarise(r)
        print("%-12s %8.2f %8.2f %8.2f %8.2f %8.2f"
              % (nm.replace("_emu_", "").replace(".txt", ""), *[s[n][0] for n in names]))
    print()
    print("========== 组间差（%s − %s，组内轮间中位再相减）==========" % (ga, gb))
    print("%-8s %10s %10s %10s %10s" % ("列", "中位差", "均差", "p95差", "p99差"))
    for n in names:
        # ⚠️ 这里**不做逐轮配对**(两组轮号集合可能不齐), 用"组内轮间中位"再相减。
        #    逐轮配对版在 `ab_compare.py` 里 —— 这里要回答的是"**哪一列**"。
        a_med = st.median([sa[k][n][0] for k, _ in ra])
        b_med = st.median([sb[k][n][0] for k, _ in rb])
        a_avg = st.median([sa[k][n][3] for k, _ in ra])
        b_avg = st.median([sb[k][n][3] for k, _ in rb])
        a_p95 = st.median([sa[k][n][1] for k, _ in ra])
        b_p95 = st.median([sb[k][n][1] for k, _ in rb])
        a_p99 = st.median([sa[k][n][2] for k, _ in ra])
        b_p99 = st.median([sb[k][n][2] for k, _ in rb])
        print("%-8s %+10.3f %+10.3f %+10.3f %+10.3f"
              % (n, b_med - a_med, b_avg - a_avg, b_p95 - a_p95, b_p99 - a_p99))
    print()
    print("========== 迟到帧（dt ≥ 1.5 格 = %.2fms）逐轮 ==========" % (1.5 * VSYNC_MS))
    print("%-12s %6s %8s %8s %8s %8s" % ("轮", "条数", "迟到ms", "thr", "sw", "缺口"))
    for gp, rs in ((ga, ra), (gb, rb)):
        print("-- 组 %s --" % gp)
        ks = []
        for nm, r in rs:
            k = late_frames(r)
            ks.append(k)
            print("%-12s %6d %8.2f %8.2f %8.2f %8.2f"
                  % (nm.replace("_emu_", "").replace(".txt", ""),
                     k["n"], k["late"], k["thr"], k["sw"], k["gap"]))
        print("   %s 轮间中位: 条数 %d · 迟到 %.2fms · thr %.2f · sw %.2f · 缺口 %.2f"
              % (gp, st.median([k["n"] for k in ks]), st.median([k["late"] for k in ks]),
                 st.median([k["thr"] for k in ks]), st.median([k["sw"] for k in ks]),
                 st.median([k["gap"] for k in ks])))
    print()
    print("读法: dt 是总账。谁( thr / sw / 缺口 )在 p99 那列跟 dt 一起抬, 谁就是那 0.3ms。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
