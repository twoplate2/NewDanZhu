"""固定种子验证发射手感：按力度记录顶部耗时、回流与落格，不重写冻结基线。"""
import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu import config as C, geo, physics as P


def measure(samples):
    result = {}
    for power in C.KNOB_POWERS:
        times, slow, slots, returned, unfinished = [], [], [0]*9, 0, 0
        for seed in range(samples):
            rng = random.Random(47000+seed)
            scene = geo.build_geo()
            dy = rng.uniform(-6, 6)
            scene['deflectors'] = [(a,b+dy,c,d+dy) for a,b,c,d in scene['deflectors']]
            ball = P.launch_ball(power, rng)
            top = low = 0
            entered = back = False
            for _ in range(1800):
                slot = P.advance_flight(ball, scene)
                entered |= ball.x < C.LANE_L-C.BALL_R
                back |= entered and ball.x > C.LANE_L and ball.y > C.LANE_WALL_TOP
                if ball.y < C.PEG_TOP:
                    top += C.FIXED_DT
                    low += C.FIXED_DT if math.hypot(ball.vx,ball.vy) < 180 else 0
                if slot is not None:
                    slots[slot] += 1
                    break
            else:
                unfinished += 1
            returned += back
            times.append(top); slow.append(low)
        result[str(power)] = dict(top_median_s=statistics.median(times),
            top_max_s=max(times), slow_top_mean_s=statistics.mean(slow),
            returned=returned, unfinished=unfinished, slots=slots, samples=samples)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=100)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    data = measure(args.samples)
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(json.dumps(data, indent=2))
