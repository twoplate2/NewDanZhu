"""发射体验新契约；旧轨迹 golden 保留，不把有意变化伪装成逐位相等。"""
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu import config as C, physics as P, geo


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

    def test_guide_reflects_from_surface_not_random_angle_or_frame(self):
        # 45 度直线导轨：竖直上行应转为水平向左；次数/随机种子不改变接触几何。
        for frame in (1,2,30):
            for seed in range(20):
                b=P.launch_ball(1,random.Random(seed))
                b.x,b.y,b.vx,b.vy=45,55,0,-500
                P._collide_arc(b,0,0,100,100,frame)
                self.assertAlmostEqual(b.vx,-500)
                self.assertAlmostEqual(b.vy,0)
                self.assertLess(b.x-b.y,0)  # 球仍处于导轨左下侧

    def test_first_guide_contact_always_enters_left_for_real_geometry(self):
        for power in C.KNOB_POWERS:
            for dy in (-6,-3,0,3,6):
                scene=geo.build_geo()
                scene['deflectors']=[(a,b+dy,c,d+dy) for a,b,c,d in scene['deflectors']]
                ball=P.launch_ball(power,random.Random(21))
                for _ in range(120):
                    P.advance_flight(ball,scene)
                    if ball.events&C.EV_ARC:break
                else:self.fail('valid launch missed guide')
                self.assertLess(ball.vx,-250,(power,dy,ball.vx))


if __name__ == '__main__': unittest.main()
