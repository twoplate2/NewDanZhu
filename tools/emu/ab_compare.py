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

# ⚠️ 日志在 `temp/`(仓库根下), 不是脚本自己所在的 `tools/emu/`。
HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "temp")


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
        # ⚠️⚠️ **配对检验 —— 这是本夹具最灵敏的一条, 别跳过。**
        #    `ab_rounds.sh` 是**交替**跑的(v69_r3 与 v122_r3 相邻), 交替换包正是为了让
        #    每一对共享同一段主机状态 ⇒ **逐对求差**把"漂移"这一项直接消掉。
        #    而"两组各取中位再相减"是**非配对**统计, 漂移全留在噪声里(实测同一个 APK
        #    连跑 6 轮的极差就有 7.4%)。判据看**符号检验**: 差值符号一致才叫真差异。
        try:
            import math as _math

            def _ridx(nm):
                m = re.search(r"_r(\d+)\.txt$", nm)
                return int(m.group(1)) if m else None

            _ra, _rb = res[ga][2], res[gb][2]
            _ma = {_ridx(nm): v for (nm, _h, _f), v in zip(_ra, res[ga][0])}
            _mb = {_ridx(nm): v for (nm, _h, _f), v in zip(_rb, res[gb][0])}
            _common = sorted(set(_ma) & set(_mb) - {None})
            if _common:
                _d = [_ma[i] - _mb[i] for i in _common]      # 正 = ga 高
                _k = sum(1 for x in _d if x > 0)
                _n = len(_d)
                # 符号检验(双侧, 精确): H0 = 差值正负各半
                _tail = sum(_math.comb(_n, j) for j in range(max(_k, _n - _k), _n + 1))
                _p = min(1.0, 2.0 * _tail / (2 ** _n))
                print("========== 配对检验（%d 对，%s − %s）==========" % (_n, ga, gb))
                print("  逐对差: %s" % " · ".join("%+.1f" % x for x in _d))
                print("  中位差 %+.1f · 均差 %+.1f · ga 高的对 %d/%d · 符号检验 p=%.3f"
                      % (st.median(_d), sum(_d) / _n, _k, _n, _p))
                # 配对自举: 对"对的集合"重采样(不是对帧重采样)
                import random as _rnd
                _r = _rnd.Random(20260920)
                _bs = []
                for _ in range(4000):
                    _s = [_d[_r.randrange(_n)] for _ in range(_n)]
                    _bs.append(sum(_s) / _n)
                _bs.sort()
                print("  配对自举 90%% 区间: %+.1f ~ %+.1f  %s"
                      % (_bs[200], _bs[3799],
                         "← 不含 0 ⇒ 有差异" if (_bs[200] > 0 or _bs[3799] < 0)
                         else "← 含 0 ⇒ 分不出来"))
                if _n < 5:
                    print("  ⚠️ 只有 %d 对 —— 符号检验最小可能 p 就是 %.3f, 判不出显著性。加对。" % (_n, 2.0 / (2 ** _n)))
        except Exception as _e:      # 配对是**增益**, 坏了不许把主判据带下水
            print("  (配对检验跳过: %r)" % (_e,))
        # ⚠️⚠️ **先比两组离群帧的量级** —— 2026-09-20 的一次 A/B 就是被这个骗的:
        #    B 组看着 +18.8%, 实际是 A 组某一轮撞上一根 177ms 的离群帧。
        #    两组的"最慢一帧"差得多 ⇒ 1%Low 根本不可比, 先去看那一轮为什么有离群。
        tops = {}
        for gp, (_, _, rs) in res.items():
            tops[gp] = st.median([max(x["dt"] for x in fr) for _, _, fr in rs])
        print("  最慢一帧(组内中位): %s" % " · ".join("%s %.2fms" % (k, v) for k, v in tops.items()))
        _tv = list(tops.values())
        if len(_tv) == 2 and max(_tv) > 1.5 * min(_tv):
            print("  ⚠️⚠️ **两组的离群量级差 %.1f 倍 ⇒ 1%%Low 不可比** —— 先去看量级大的那一组"
                  "为什么有离群, 别急着下结论。" % (max(_tv) / min(_tv)))
            print("     更稳的口径: p99 帧率, 或「剔除最慢 1 帧后的 1%Low」。")
            return
        if abs(ma - mb) <= max(wa, wb):
            print("  ⇒ **分不出来** —— 组间差没有超过组内噪声。加轮数, 或换更稳的指标(p99)。")
        else:
            print("  ⇒ 组间差**超过**了组内极差 —— 但样本仍小, 建议再看块自举区间是否不重叠。")


if __name__ == "__main__":
    main()
