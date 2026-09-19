"""差分对账：把重构版逐位比到 tests/golden/ 里的老版基线。

口径是**同种子逐位相等**，不是统计近似 —— 手感变了必须红，不能"看起来差不多"。
老版可无窗口 import 且同种子逐帧确定（见 golden_extract.py 顶部），所以这条能成立。

跑法：python tests/parity.py            # 全部
      python tests/parity.py geo        # 只跑某一段
"""

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, "golden")
sys.path.insert(0, ROOT)

# ⚠️ 必须在任何 print 之前: 报红时打的诊断带 ⚠️/中文, 而 Windows 控制台默认 GBK
#    —— 不带这句, **门禁自己会在报红的时候崩掉**(本工程今天踩过三次: font_check /
#    fx_probe / trace), 那比不报还糟。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOL = 1e-9


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as f:
        return json.load(f)


class Report:
    def __init__(self, section):
        self.section = section
        self.bad = []
        self.checks = 0

    def eq(self, ok, msg):
        self.checks += 1
        if not ok:
            self.bad.append(msg)
        return ok

    def close(self, a, b, msg):
        return self.eq(abs(a - b) <= TOL, f"{msg}: {a!r} != {b!r}")

    def done(self):
        if self.bad:
            print(f"[红] {self.section}: {len(self.bad)}/{self.checks} 处不符")
            for m in self.bad[:12]:
                print(f"      {m}")
            if len(self.bad) > 12:
                print(f"      ... 另有 {len(self.bad) - 12} 处")
            return False
        print(f"[绿] {self.section}: {self.checks} 项逐位相符")
        return True


def check_geo():
    r = Report("geo")
    from danzhu import geo as G

    want = load("geo.json")
    got = G.build_geo()
    for k in ("pegs", "dividers", "walls", "deflectors"):
        w, g = want[k], got[k]
        if not r.eq(len(w) == len(g), f"{k} 条数 {len(g)} != {len(w)}"):
            continue
        for i, (a, b) in enumerate(zip(w, g)):
            for j in range(len(a)):
                r.close(b[j], a[j], f"{k}[{i}][{j}]")
    for ri, row in enumerate(want["peg_rows"]):
        for i, p in enumerate(row):
            r.close(got["peg_rows"][ri][i][0], p[0], f"peg_rows[{ri}][{i}].x")
            r.close(got["peg_rows"][ri][i][1], p[1], f"peg_rows[{ri}][{i}].y")
    return r.done()


def check_trajectories():
    r = Report("轨迹")
    from danzhu import geo as G
    from danzhu import physics as P

    g = G.build_geo()
    for case in load("trajectories.json"):
        b = P.launch_ball(case["power"], random.Random(case["seed"]))
        frames = []
        slot = None
        for _ in range(900):
            slot = P.advance_flight(b, g)
            frames.append((round(b.x, 9), round(b.y, 9), round(b.vx, 9),
                           round(b.vy, 9), int(b.events)))
            if slot is not None:
                break
        tag = f"power={case['power']} seed={case['seed']}"
        if not r.eq(len(frames) == case["n"], f"{tag} 帧数 {len(frames)} != {case['n']}"):
            continue
        if not r.eq(slot == case["slot"], f"{tag} 槽 {slot} != {case['slot']}"):
            continue
        for i, (wf, gf) in enumerate(zip(case["frames"], frames)):
            for j in range(5):
                if abs(gf[j] - wf[j]) > TOL:
                    r.eq(False, f"{tag} 第{i}帧[{['x','y','vx','vy','ev'][j]}] "
                                f"{gf[j]} != {wf[j]}")
                    break
            else:
                r.checks += 1
                continue
            break
    return r.done()


def check_misfire():
    r = Report("哑火")
    from danzhu import physics as P

    for case in load("misfire.json"):
        b = P.launch_misfire(case["power"])
        b._rng = random.Random(case["seed"])
        frames = []
        for i in range(240):
            P.advance_misfire(b)
            frames.append((round(b.x, 9), round(b.y, 9), round(b.vx, 9), round(b.vy, 9)))
        tag = f"power={case['power']} seed={case['seed']}"
        for i, (wf, gf) in enumerate(zip(case["frames"], frames)):
            for j in range(4):
                if abs(gf[j] - wf[j]) > TOL:
                    r.eq(False, f"{tag} 第{i}帧[{['x','y','vx','vy'][j]}] {gf[j]} != {wf[j]}")
                    break
            else:
                r.checks += 1
                continue
            break
    return r.done()


def check_boards():
    r = Report("盘面配平")
    from danzhu import rules as R

    for rtp_repr, rolls in load("boards.json").items():
        rtp = float(rtp_repr)
        for seed, want in enumerate(rolls):
            random.seed(seed)
            got = R.roll_multipliers(rtp)
            if not r.eq(len(got) == len(want), f"rtp={rtp} seed={seed} 格数 {len(got)} != {len(want)}"):
                continue
            for i, (a, b) in enumerate(zip(want, got)):
                r.close(b, a, f"rtp={rtp} seed={seed} 第{i}格")
    return r.done()


def check_bank():
    r = Report("音效合成")
    import hashlib
    from danzhu.audio import synth as S

    want = load("bank.json")
    got = {}
    for name, pcm in S.iter_bank():
        raw = pcm.tobytes() if hasattr(pcm, "tobytes") else bytes(pcm)
        got[name] = {"len": len(raw), "sha1": hashlib.sha1(raw).hexdigest()}
    r.eq(set(got) == set(want), f"音效名集合不符: 多 {set(got) - set(want)}, 少 {set(want) - set(got)}")
    for name in want:
        if name not in got:
            continue
        r.eq(got[name]["len"] == want[name]["len"],
             f"{name} 长度 {got[name]['len']} != {want[name]['len']}")
        r.eq(got[name]["sha1"] == want[name]["sha1"], f"{name} 波形不一致")
    return r.done()


def _sections():
    return {
        "geo": check_geo,
        "traj": check_trajectories,
        "misfire": check_misfire,
        "boards": check_boards,
        "bank": check_bank,
        "live": check_live_traj,
        "liveboards": check_live_boards,
        "pile": check_pile,
        "livepile": check_live_pile,
    }


def _load_old():
    """直连老版活体（无窗口 import）。比冻结的 golden 更强：没有分辨率下限，
    且不会有"重刷 golden 制造绿"这条后门。"""
    import importlib.util
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_WINDOW"] = "mock"
    sys.argv = ["main", "--nosound"]
    spec = importlib.util.spec_from_file_location(
        "oldmain", r"E:\AI_Tools\other\DanZhu\android\main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 10 个力度档中心 + 档边界。只用档中心会漏掉 _power_band 的边界归属，
# 而档位错一位 = 整张旋钮表取错行。
LIVE_POWERS = [0.05, 0.15, 0.1499, 0.1501, 0.25, 0.35, 0.45, 0.55,
               0.65, 0.75, 0.85, 0.95, 1.0]

# 除 x/y/vx/vy/events 外，Ball 上还有这些字段会被下游读（渲染/音效/卡死踢球）。
# 它们抄错时轨迹对账照样绿 —— 所以单独逐位比。
# ⚠️ born 不在此列：它是 time.time() 出生时间戳，两次运行必然不同，不是物理状态。
AUX_FIELDS = ["squash", "squash_nx", "squash_ny", "spin", "last_nx", "last_ny",
              "hit_peg", "peg_flash", "item", "launch_power", "_stall_retry",
              "misfire"]


def _aux(b):
    out = []
    for f in AUX_FIELDS:
        v = getattr(b, f, None)
        out.append(None if v is None else (round(float(v), 9) if isinstance(v, (int, float)) else str(v)))
    return out


def check_live_traj():
    r = Report("活体轨迹(全字段)")
    old = _load_old()
    from danzhu import geo as G
    from danzhu import physics as P

    og, ng = old.build_geo(), G.build_geo()
    for power in LIVE_POWERS:
        for seed in range(24):
            ob = old.launch_ball(power, random.Random(seed))
            nb = P.launch_ball(power, random.Random(seed))
            tag = f"power={power} seed={seed}"
            for i in range(900):
                os_ = old.advance_flight(ob, og)
                ns = P.advance_flight(nb, ng)
                bad = None
                for name, a, b in (("x", ob.x, nb.x), ("y", ob.y, nb.y),
                                   ("vx", ob.vx, nb.vx), ("vy", ob.vy, nb.vy)):
                    if abs(a - b) > TOL:
                        bad = f"{name} {a!r} != {b!r}"
                        break
                if bad is None and int(ob.events) != int(nb.events):
                    bad = f"events {ob.events} != {nb.events}"
                if bad is None:
                    oa, na = _aux(ob), _aux(nb)
                    if oa != na:
                        d = [f"{AUX_FIELDS[k]}:{oa[k]}!={na[k]}"
                             for k in range(len(oa)) if oa[k] != na[k]]
                        bad = "辅助字段 " + ", ".join(d[:4])
                if bad:
                    r.eq(False, f"{tag} 第{i}帧 {bad}")
                    break
                r.checks += 1
                if os_ is not None or ns is not None:
                    r.eq(os_ == ns, f"{tag} 槽 {ns} != {os_}")
                    break
    return r.done()


def check_live_boards():
    """活体盘面对账。冻结 golden 每档只有 60 种子 —— 对权重小数点后第三位的漂移
    （0.289→0.290）无感（验证员实测），这里把种子数拉高到有分辨力。"""
    r = Report("活体盘面配平(高分辨)")
    old = _load_old()
    from danzhu import rules as R

    for rtp in [0.80, 1.20, 2.00, 3.60, 10.0, 20.0, 50.0]:
        bad = 0
        for seed in range(1500):
            random.seed(seed)
            w = old.roll_multipliers(rtp)
            random.seed(seed)
            g = R.roll_multipliers(rtp)
            if len(w) != len(g) or any(abs(a - b) > TOL for a, b in zip(w, g)):
                bad += 1
                if bad <= 3:
                    r.eq(False, f"rtp={rtp} seed={seed} {g} != {w}")
        if bad == 0:
            r.checks += 1500
    return r.done()


def check_pile():
    r = Report("球堆(装杯)")
    from danzhu.fx import pile as PL

    for case in load("pile.json"):
        spec = PL.PileSpec(case["count"], seed=case["seed"])
        beads, meta = PL.build_pile(spec)
        proj = PL.project_pile(beads)
        tag = f"count={case['count']} seed={case['seed']}"
        if not r.eq(len(beads) == len(case["beads"]), f"{tag} 球数 {len(beads)} != {len(case['beads'])}"):
            continue
        for i, (wb, gb) in enumerate(zip(case["beads"], beads)):
            for j, k in enumerate(("x", "z", "h", "layer")):
                r.close(gb[k], wb[j], f"{tag} bead[{i}].{k}")
        for k, v in case["meta"].items():
            # ⚠️ ms 是**耗时**(time.perf_counter 差值), 不是物理状态: 老版记的是抽基线那次
            #    冷跑的时间, 与机器负载/预热状态绑定, 同一种子重跑必然不同(golden 中位 1.05ms,
            #    本版中位 0.49ms)。它进对账只会制造假红, 且"让它变绿"的唯一办法是伪造计时。
            #    count/H/R 三个才是要看住的产物, 它们已逐位相符(实测非 ms 差异 0 处)。
            if k == "ms":
                continue
            if isinstance(v, (int, float)):
                r.close(meta[k], v, f"{tag} meta.{k}")
        if not r.eq(len(proj) == len(case["proj"]), f"{tag} 投影数不符"):
            continue
        for i, (wp, gp) in enumerate(zip(case["proj"], proj)):
            for j, k in enumerate(("i", "sx", "sy", "r", "shade")):
                r.close(gp[k], wp[j], f"{tag} proj[{i}].{k}")
    return r.done()


def check_live_pile():
    """活体球堆(**生产参数**)。
    冻结的 golden 用的是默认 PileSpec —— scatter=None 让 _scatter_floor 这条支路
    一次都没走到，而生产路径恰好走的就是它（验证员实测指出）。
    这里从老版取生产参数（_r_dp_for / _PILE_SITES / _PILE_QUOTA / _PILE_ROT_STEP），
    同一份 spec 喂给新旧两侧。"""
    r = Report("活体球堆(生产参数)")
    old = _load_old()
    from danzhu.fx import pile as PL

    variants = old._PILE_VARIANTS
    for count in [2, 3, 5, 10, 20, 50, 100]:
        for key in range(36):
            # ⚠️ seed 用裸 key, scatter/rot/sites/quota 一律用 key % variants ——
            # 生产路径就是这么算的（main.py:8848-8851）。把 scatter 也用裸 key 会
            # 越过 _PILE_SITES 的长度, _assert_pile 当场抛 "unresolved overlap"。
            v = key % variants
            kw = dict(seed=key,
                      r_dp=old._r_dp_for(count),
                      rot_deg=(v * old._PILE_ROT_STEP) % 360.0,
                      sites=old._PILE_SITES[v % len(old._PILE_SITES)],
                      scatter=v,
                      quota=old._PILE_QUOTA[v % len(old._PILE_QUOTA)])
            # 老版在部分 (count,seed) 上会抛 _assert_pile 的 "unresolved overlap"
            # （装不下）。生产上前 12 组走烘焙表所以玩家遇不到。
            # 这里把"抛不抛、抛什么"也当行为对账 —— 新版必须在**同样的输入上以同样的方式失败**。
            try:
                ob, om = old.build_pile(old.PileSpec(count, **kw))
                oerr = None
            except Exception as e:
                ob, om, oerr = None, None, f"{type(e).__name__}: {e}"
            try:
                nb, nm = PL.build_pile(PL.PileSpec(count, **kw))
                nerr = None
            except Exception as e:
                nb, nm, nerr = None, None, f"{type(e).__name__}: {e}"
            tag = f"count={count} key={key}(v={v})"
            if not r.eq(nerr == oerr, f"{tag} 异常不一致: 新={nerr!r} 老={oerr!r}"):
                continue
            if oerr is not None:
                continue
            op = old.project_pile(ob)
            npj = PL.project_pile(nb)
            if not r.eq(len(ob) == len(nb), f"{tag} 球数 {len(nb)} != {len(ob)}"):
                continue
            for i, (a, b) in enumerate(zip(ob, nb)):
                for k in ("x", "z", "h", "layer", "r"):
                    r.close(b[k], a[k], f"{tag} bead[{i}].{k}")
            for k in ("count", "H", "R"):
                r.close(nm[k], om[k], f"{tag} meta.{k}")
            r.eq(len(op) == len(npj), f"{tag} 投影数 {len(npj)} != {len(op)}")
            for i, (a, b) in enumerate(zip(op, npj)):
                for k in ("i", "sx", "sy", "r", "shade", "z"):
                    if k in a and k in b:
                        r.close(b[k], a[k], f"{tag} proj[{i}].{k}")
    return r.done()


def main():
    names = sys.argv[1:] or list(_sections())
    ok = True
    pending = []
    for n in names:
        secs = _sections()
        fn = secs.get(n)
        if fn is None:
            print(f"未知段 {n}; 可选 {list(secs)}")
            return 2
        try:
            ok &= fn()
        except ImportError as e:
            # 模块还没端口 —— 明说"未对账"，不许静默当成绿
            print(f"[--] {n}: 未端口（{e}）")
            pending.append(n)
        except Exception as e:
            print(f"[红] {n}: 抛异常 {type(e).__name__}: {e}")
            ok = False
    print()
    if pending:
        print("尚未端口、未对账的段:", ", ".join(pending))
    print("对账结果:", "全绿" if ok else "有红")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
