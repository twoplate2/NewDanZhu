"""从老版 android/main.py 抽取确定性行为，落成重构版的对账基线。

老版可无窗口 import（KIVY_WINDOW=mock），且**同种子逐帧确定**（实测两遍哈希相同）。
所以基线口径是「同种子逐位相等」，不是「统计近似」—— 后者会把"手感变了"放过去。

跑法（必须先跑一次，产物进 tests/golden/）：
    python tests/golden_extract.py

⚠️ 只读老工程，不写它。老工程路径写死在 OLD_MAIN，换机器要改。
"""

import hashlib
import importlib.util
import json
import os
import random
import sys

# ⚠️ 必须在任何 print 之前: 报红时打的诊断带 ⚠️/中文, 而 Windows 控制台默认 GBK
#    —— 不带这句, **门禁自己会在报红的时候崩掉**(本工程今天踩过三次: font_check /
#    fx_probe / trace), 那比不报还糟。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "golden")
OLD_MAIN = r"E:\AI_Tools\other\DanZhu\android\main.py"

# 覆盖弱蓄/中蓄/满蓄三档 + 弧面抖动最敏感的边界力度
POWERS = [0.05, 0.15, 0.30, 0.50, 0.70, 0.85, 1.00]
SEEDS = list(range(40))
MAX_FRAMES = 900  # 实测 p99 落袋步数 < 520，留一倍余量


def load_old():
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_WINDOW"] = "mock"
    sys.argv = ["main", "--nosound"]
    spec = importlib.util.spec_from_file_location("oldmain", OLD_MAIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def q(v):
    """量化到 1e-9，避免 JSON 浮点表示差异导致假红。"""
    return round(float(v), 9)


def extract_geo(m, raw=None):
    geo = m.build_geo() if raw is None else raw
    return {
        "pegs": [[q(p[0]), q(p[1])] for p in geo["pegs"]],
        "dividers": [[q(v) for v in d] for d in geo["dividers"]],
        "walls": [[q(v) for v in w] for w in geo["walls"]],
        "deflectors": [[q(v) for v in d] for d in geo["deflectors"]],
        "peg_rows": [[[q(p[0]), q(p[1])] for p in r] for r in geo["peg_rows"]],
    }


def extract_trajectories(m, geo):
    out = []
    for power in POWERS:
        for seed in SEEDS:
            b = m.launch_ball(power, random.Random(seed))
            frames = []
            slot = None
            for _ in range(MAX_FRAMES):
                slot = m.advance_flight(b, geo)
                frames.append([q(b.x), q(b.y), q(b.vx), q(b.vy), int(b.events)])
                if slot is not None:
                    break
            out.append({
                "power": power, "seed": seed, "n": len(frames),
                "slot": slot, "frames": frames,
            })
    return out


def extract_misfire(m, geo):
    out = []
    for power in [0.0, 0.05, 0.10, 0.14]:
        for seed in SEEDS[:20]:
            b = m.launch_misfire(power)
            b._rng = random.Random(seed)
            frames = []
            for _ in range(240):
                m.advance_misfire(b)
                frames.append([q(b.x), q(b.y), q(b.vx), q(b.vy)])
            out.append({"power": power, "seed": seed, "frames": frames})
    return out


def extract_boards(m):
    """盘面配平是全工程最值钱的闭式解，必须逐档逐种子对上。"""
    out = {}
    for rtp in [0.80, 1.20, 2.00, 3.60, 10.0, 20.0, 50.0]:
        rolls = []
        for seed in range(60):
            random.seed(seed)
            rolls.append([q(v) for v in m.roll_multipliers(rtp)])
        out[repr(rtp)] = rolls
    return out


def extract_pile(m):
    """中奖装杯的球堆：颗数 = 倍率，9 档盘面上会出现的 7 种。"""
    out = []
    for count in [2, 3, 5, 10, 20, 50, 100]:
        for seed in range(6):
            spec = m.PileSpec(count, seed=seed)
            beads, meta = m.build_pile(spec)
            proj = m.project_pile(beads)
            out.append({
                "count": count, "seed": seed,
                "beads": [[q(b["x"]), q(b["z"]), q(b["h"]), int(b["layer"])] for b in beads],
                "meta": {k: q(v) for k, v in meta.items()},
                "proj": [[int(p["i"]), q(p["sx"]), q(p["sy"]), q(p["r"]), q(p["shade"])]
                         for p in proj],
            })
    return out


def extract_bank(m):
    """音效是确定性合成（固定种子）。存 sha1 而非波形 —— 几 MB 变几 KB，
    而"逐位相同"这件事由 sha1 同样能钉死。"""
    out = {}
    for item in m.iter_bank():
        name, pcm = item[0], item[1]
        raw = pcm.tobytes() if hasattr(pcm, "tobytes") else bytes(pcm)
        out[name] = {
            "len": len(raw),
            "sha1": hashlib.sha1(raw).hexdigest(),
        }
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    print("import 老工程 ...")
    m = load_old()
    print("OK")

    # ⚠️ 轨迹必须用**原始** geo（未 round 的浮点坐标）跑。用 extract_geo 的序列化版本会
    # 把钉坐标截到 9 位，物理差 1e-8 且逐帧累积 —— 会让正确的端口在对账里看起来是红的。
    raw_geo = m.build_geo()
    geo = extract_geo(m, raw_geo)
    print(f"geo: {len(geo['pegs'])} 钉 / {len(geo['dividers'])} 隔板 / "
          f"{len(geo['walls'])} 墙 / {len(geo['deflectors'])} 弧")

    traj = extract_trajectories(m, raw_geo)
    settled = sum(1 for t in traj if t["slot"] is not None)
    print(f"轨迹: {len(traj)} 条, 落袋 {settled}, 未落袋 {len(traj) - settled}")

    misfire = extract_misfire(m, raw_geo)
    print(f"哑火: {len(misfire)} 条")

    boards = extract_boards(m)
    print(f"盘面: {len(boards)} 档 × {len(next(iter(boards.values())))} 种子")

    bank = extract_bank(m)
    print(f"音效: {len(bank)} 条")

    pile = extract_pile(m)
    print(f"球堆: {len(pile)} 组（7 颗数档 × 6 种子）")

    for name, data in [
        ("geo.json", geo), ("trajectories.json", traj), ("misfire.json", misfire),
        ("boards.json", boards), ("bank.json", bank), ("pile.json", pile),
    ]:
        p = os.path.join(OUT, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  -> {name}  {os.path.getsize(p) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
