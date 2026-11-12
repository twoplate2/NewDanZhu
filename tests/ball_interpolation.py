"""弹珠显示插值：实际帧间隔、碰撞边界、换球和只读约束。"""
import math
from copy import deepcopy
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu import config as C
from danzhu.game import Game
from danzhu.physics import advance_flight, launch_ball


def launch(power=1.0):
    random.seed(91)
    game = Game(sound_gate=lambda: False, voice_duration=lambda _: 0.1)
    game.start_charge()
    game.power = power
    game.launch()
    game.take_events()
    return game


def pose(ball):
    return ball.x, ball.y, getattr(ball, "spin", 0.0)


class InterpolationTests(unittest.TestCase):
    def test_every_presented_frame_moves_after_initial_step(self):
        for hz in (60, 90, 120, 144, 165, 185, 240):
            with self.subTest(hz=hz):
                game = launch()
                displayed, physical = [], []
                for _ in range(int(hz * 0.2)):
                    game.step(1.0 / hz)
                    displayed.append(game.ball_draw_pose()[1])
                    physical.append(game.ball.y)
                start = math.ceil(hz / 60)
                moves = [a - b for a, b in zip(displayed[start:], displayed[start + 1:])]
                self.assertTrue(all(d > 0 for d in moves), (hz, moves))
                self.assertLess(max(moves) / min(moves), 1.3)
                if hz > 60:
                    self.assertTrue(any(a == b for a, b in zip(physical, physical[1:])))

    def test_variable_intervals_follow_actual_time_not_fps_setting(self):
        game = launch()
        reference = launch_ball(1.0)
        samples = [reference.y]
        for _ in range(20):
            advance_flight(reference, game.geo)
            samples.append(reference.y)
        elapsed = 0.0
        for i, dt in enumerate((1/240, 1/120, 1/90, 1/165, 1/60, 1/144, 1/30) * 2):
            game.set_fps_cap_setting((60, 120, 240)[i % 3])
            game.step(dt)
            elapsed += dt
            render_step = max(0.0, elapsed / C.FIXED_DT - 1.0)
            index = int(render_step)
            fraction = render_step - index
            expected = samples[index] + (samples[index + 1] - samples[index]) * fraction
            self.assertAlmostEqual(game.ball_draw_pose()[1], expected, places=8)

    def test_catchup_uses_last_physics_step_and_long_pause_does_not_extrapolate(self):
        for dt in (3.4 * C.FIXED_DT, 2.0):
            game = launch()
            positions = [game.ball.y]
            original = game._remember_ball_pose
            def remember():
                positions.append(game.ball.y)
                original()
            game._remember_ball_pose = remember
            game.step(dt)
            self.assertLessEqual(len(positions) - 1, C.MAX_STEPS_PER_FRAME)
            x, y, _ = game.ball_draw_pose()
            self.assertEqual(x, game.ball.x)
            self.assertGreaterEqual(y, game.ball.y)
            self.assertLessEqual(y, positions[-1])
            alpha = game._accumulator / C.FIXED_DT
            self.assertAlmostEqual(y, positions[-1] + (game.ball.y - positions[-1]) * alpha)

    def test_drawing_is_read_only_and_redraw_does_not_advance_time(self):
        game = launch()
        game.step(1/40)
        before = deepcopy({key: getattr(game.ball, key, None) for key in game.ball.__slots__})
        rng = random.getstate()
        events = list(game._ev)
        clocks = game.now, game.frame, game._accumulator
        first = game.ball_draw_pose()
        for _ in range(20):
            self.assertEqual(game.ball_draw_pose(), first)
        self.assertEqual(before, {key: getattr(game.ball, key, None) for key in game.ball.__slots__})
        self.assertEqual(random.getstate(), rng)
        self.assertEqual(game._ev, events)
        self.assertEqual((game.now, game.frame, game._accumulator), clocks)

    def test_reset_relaunch_and_replacement_never_use_old_ball(self):
        game = launch()
        game.step(1/40)
        self.assertNotEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])
        game.reset_balance(False)
        self.assertEqual(game.ball_draw_pose(), pose(game.ball))
        game.start_charge()
        game.power = 0.8
        game.launch()
        self.assertEqual(game.ball_draw_pose(), pose(game.ball))
        game.step(1/40)
        game.ball = launch_ball(0.9)
        self.assertEqual(game.ball_draw_pose(), pose(game.ball))
        game.park_ball(reroll=False)
        self.assertEqual(game.ball_draw_pose(), pose(game.ball))
        game.ball = None
        self.assertIsNone(game.ball_draw_pose())

    def test_peg_collision_uses_corrected_position_even_after_event_consumed(self):
        game = launch()
        px, py = game.geo["pegs"][4]
        game.ball.x, game.ball.y = px, py - C.BALL_R - C.PEG_R - 1
        game.ball.vx, game.ball.vy = 0.0, 500.0
        events = game.step(1/40)
        self.assertTrue(any(e.kind == "collision" for e in events))
        self.assertEqual(game.ball.events, 0)
        self.assertEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])
        game.step(1/1000)  # 没有新物理步时也不能退回碰撞前坐标。
        self.assertEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])

    def test_real_guide_and_wall_contacts_do_not_cut_across_turn(self):
        game = launch()
        found = 0
        for _ in range(500):
            events = game.step(1/120)
            for event in events:
                if event.kind == "collision" and event.data["ev"] & (C.EV_ARC | C.EV_CEIL | C.EV_WALL):
                    found += 1
                    self.assertEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])
            if game.state != "flying":
                break
        self.assertGreater(found, 0)

    def test_landing_transition_bounce_and_settled_position(self):
        game = launch()
        game.multipliers = [0] * C.NUM_SLOTS
        game.ball.x = C.FIELD_L + 4.5 * C.SLOT_W
        game.ball.y = C.FLOOR - C.BALL_R - 1
        game.ball.vx, game.ball.vy = 0.0, 400.0
        game.step(1/40)
        self.assertEqual(game.state, "landing")
        self.assertTrue(game._settled)
        self.assertEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])
        game.step(C.FIXED_DT)
        self.assertTrue(game._ball_draw_contact)
        self.assertEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])
        for _ in range(120):
            game.step(1/120)
            if game.state == "landed":
                break
        self.assertEqual(game.state, "landed")
        self.assertEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])

    def test_misfire_smooths_then_parks_without_old_history(self):
        game = launch(0.05)
        game.step(1/40)
        self.assertEqual(game.state, "misfire")
        self.assertNotEqual(game.ball_draw_pose()[:2], pose(game.ball)[:2])
        for _ in range(600):
            game.step(1/120)
            if game.state == "ready":
                break
        self.assertEqual(game.state, "ready")
        self.assertEqual(game.ball_draw_pose(), pose(game.ball))

    def test_rotation_takes_short_path_across_full_turn(self):
        game = launch()
        game.ball.spin = math.radians(359)
        game._remember_ball_pose()
        game.ball.spin = math.radians(1)
        game._accumulator = C.FIXED_DT / 2
        self.assertAlmostEqual(game.ball_draw_pose()[2], 2 * math.pi)
        self.assertAlmostEqual(game.ball.spin, math.radians(1))


if __name__ == "__main__":
    unittest.main()
