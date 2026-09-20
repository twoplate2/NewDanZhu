# -*- coding: utf-8 -*-
"""A/B 两组多轮日志的对比 —— 用「真实重复实验」判差异，而不是拿单轮涨跌当结论。

## 为什么要有它

2026-09-20 在模拟器上跑出了**第一份真实重复实验**：同设置 4 轮，`1%Low` = 87.7 / 89.6 /
91.4 / 78.6 ⇒ **极差 14%**，而平均帧极差只有 0.08%。
⇒ **同一设置下单轮波动就有 14%** ⇒ 判 A/B 必须拿"组间差"跟"组内波动"比。

## 跑法

    python tools/emu/ab_compare.py A B          # 比 _emu_A*.txt 与 _emu_B*.txt
    python tools/emu/ab_compare.py A            # 只看 A 组

## 判据（写在输出里）

  · 组间中位差 **< 组内极差** ⇒ **分不出来**（这一轮样本不够，加轮数或换更稳的指标）
  · 组间中位差 > 组内极差，且两组各自的块自举区间**不重叠** ⇒ 才算有差异
"""
import glob
import io
import os
import re
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))


def load(prefix):
    files = sorted(glob.glob(os.path.join(HERE, "_emu_%s*.txt" % prefix)),
                   key=lambda p: int(re.search(r"(\d+)\.txt$", p).group(1)))
    out = []
    for f in files:
        L = [l.rstrip("\n") for l in io.open(f, encoding="utf-8", errors="replace")]
        rows = [l.split(",") for l in L if l and not l.startswith("#")]
        fr = []
        for r in rows:
            try:
                fr.append(dict(dt=float(r[0]), stg=r[1], self_=float(r[3]),
                               thr=float(r[4]), snd=int(r[5]), vib=int(r[6]),
                               sw=float(r[8]) if len(r) > 8 else 0.0))
            except Exception:
                pass
        hdr = {}
        for l in L:
            if l.startswith("# 节拍:"):
                m = re.search(r"屏幕\s*([\d.]+)Hz.*?vsync=([-\w]+)", l)
                if m:
                    hdr["hz"], hdr["vsync"] = m.group(1), m.group(2)
        if fr:
            out.append((os.path.basename(f), hdr, fr))
    return out


def stats(fr):
    g = [x["dt"] for x in fr]
    med = st.median(g)
    n1 = max(1, int(len(g) * 0.01))
    low1 = 1000.0 / (sum(sorted(g)[-n1:]) / n1)
    slow = [x for x in fr if x["dt"] > med / 0.75]
    return dict(n=len(g), low1=low1, med=med, avg=len(g) / sum(g) * 1000,
                nslow=len(slow),
                self_m=st.median([x["self_"] for x in slow]) if slow else 0.0,
                thr_m=st.median([x["thr"] for x in slow]) if slow else 0.0,
                sw_m=st.median([x["sw"] for x in slow]) if slow else 0.0)


def block_ci(fr, B=50, iters=300, seed=20260920):
    import random
    r = random.Random(seed)
    g = [x["dt"] for x in fr]
    if len(g) < B * 3:
        return None
    nb = len(g) // B
    vals = []
    for _ in range(iters):
        s = []
        for _ in range(nb):
            s0 = r.randrange(0, len(g) - B)
            s.extend(g[s0:s0 + B])
        n1 = max(1, int(len(s) * 0.01))
        vals.append(1000.0 / (sum(sorted(s)[-n1:]) / n1))
    vals.sort()
    return vals[int(len(vals) * 0.05)], vals[int(len(vals) * 0.95)]


def main():
    import sys
    groups = [a for a in sys.argv[1:] if not a.startswith("-")] or ["r"]
    res = {}
    for gp in groups:
        rs = load(gp)
        if not rs:
            print("!! 没找到 _emu_%s*.txt" % gp)
            continue
        print("========== 组 %s（%d 轮）==========" % (gp, len(rs)))
        print("%-10s %7s %7s %7s %7s %8s %9s %9s %9s" %
              ("轮", "帧数", "1%Low", "平均帧", "中位ms", "慢帧数",
               "慢帧自算", "慢帧主线程", "慢帧等屏幕"))
        lows, ss = [], []
        for nm, hdr, fr in rs:
            s = stats(fr)
            ss.append(s)
            lows.append(s["low1"])
            print("%-10s %7d %7.1f %7.1f %7.2f %8d %9.2f %9.2f %9.2f" %
                  (nm.replace("_emu_", "").replace(".txt", ""), s["n"], s["low1"],
                   s["avg"], s["med"], s["nslow"], s["self_m"], s["thr_m"], s["sw_m"]))
        if rs and rs[0][1]:
            print("   节拍: %s" % rs[0][1])
        res[gp] = (lows, ss, rs)
        print("   1%%Low 中位 %.1f · 范围 %.1f~%.1f · **组内极差 %.1f (%.0f%%)**"
              % (st.median(lows), min(lows), max(lows), max(lows) - min(lows),
                 100 * (max(lows) - min(lows)) / st.median(lows)))
        for nm, hdr, fr in rs[:1]:
            ci = block_ci(fr)
            if ci:
                print("   块自举 90%% 区间(取第一轮): %.1f ~ %.1f" % ci)
        print()

    if len(res) == 2:
        (ga, (la, _, _)), (gb, (lb, _, _)) = list(res.items())
        ma, mb = st.median(la), st.median(lb)
        wa = max(la) - min(la)
        wb = max(lb) - min(lb)
        print("========== A/B 判定 ==========")
        print("  %s 中位 %.1f · 组内极差 %.1f" % (ga, ma, wa))
        print("  %s 中位 %.1f · 组内极差 %.1f" % (gb, mb, wb))
        print("  组间差 %.1f  vs  组内极差 max %.1f" % (abs(ma - mb), max(wa, wb)))
        if abs(ma - mb) <= max(wa, wb):
            print("  ⇒ **分不出来** —— 组间差没有超过组内噪声。加轮数, 或换更稳的指标(p99)。")
        else:
            print("  ⇒ 组间差**超过**了组内极差 —— 但样本仍小, 建议再看块自举区间是否不重叠。")


if __name__ == "__main__":
    main()
