"""发射体验新契约；旧轨迹 golden 保留，不把有意变化伪装成逐位相等。"""
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu import config as C, physics as P


class LaunchTests(unittest.TestCase):
    def test_power_monotonic_and_misfire_unchanged(self):
        speeds = [-P.launch_ball(p).vy for p in C.KNOB_POWERS]
        self.assertTrue(all(a < b for a,b in zip(speeds,speeds[1:])))
        self.assertGreater(speeds[-1]/speeds[0], 1.14)
        self.assertEqual(P.misfire_speed(0), 475)
        self.assertEqual(P.misfire_speed(C.MISFIRE_POWER), 935)

    def test_arc_preserves_energy_across_all_powers_and_seeds(self):
        for power in C.KNOB_POWERS:
            for seed in range(50):
                b = P.launch_ball(power, random.Random(seed))
                b.x, b.y, b.vx, b.vy = 50, 105, 0, -500
                P._collide_arc(b, 0, 100, 100, 100, 1)
                self.assertIsNotNone(b.arc_ease)
                self.assertAlmostEqual(math.hypot(b.vx,b.vy), 500)

    def test_ceiling_keeps_collision_loss_but_no_random_speed_scale(self):
        for power in C.KNOB_POWERS:
            for seed in range(50):
                b = P.launch_ball(power, random.Random(seed))
                b.x, b.y, b.vx, b.vy = 200, C.WALL+4, -400, -700
                P._collide_rect(b, 0, 0, C.CW, C.WALL, C.WALL_E, C.EV_WALL)
                self.assertEqual(b.ceil_knob[1], 1)
                # 某些出口角会触发既有 vy=180 防贴顶；其余严格遵循碰撞能量。
                if b.vy > 180:
                    self.assertAlmostEqual(math.hypot(b.vx,b.vy), math.hypot(400,350))


if __name__ == '__main__': unittest.main()
