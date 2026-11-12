"""发射显示外推：范围、动态时间、接触前截断及物理只读对账。"""
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu import config as C
from danzhu.audio.synth import shared_rng, SFX_SEED
from danzhu.fx.launch_motion import launch_draw_position
from danzhu.game import Game


def launch(power=1.0, enabled=True):
    game = Game(wall_clock=lambda: 0.0, voice_duration=lambda _: 0.1)
    game.launch_extrapolation = enabled
    game.start_charge()
    game.power = power
    game.launch()
    return game


class LaunchMotionTests(unittest.TestCase):
    def test_default_old_config_and_boolean_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for value in ({}, {'launch_extrapolation': 'true'}, {'launch_extrapolation': 1}):
                path.write_text(json.dumps(value), encoding='utf-8')
                self.assertFalse(Game(config_path=lambda: str(path)).launch_extrapolation)
            game = Game(config_path=lambda: str(path))
            for enabled in (True, False):
                self.assertTrue(game.set_launch_extrapolation(enabled))
                self.assertEqual(Game(config_path=lambda: str(path)).launch_extrapolation, enabled)
            self.assertFalse(game.set_launch_extrapolation('true'))
            self.assertFalse(game.launch_extrapolation)

    def test_actual_remainder_drives_position_independently_of_cap(self):
        game = launch()
        for cap in C.FPS_CAP_OPTIONS:
            game.fps_cap_setting = cap
            for alpha in (0, .2, .5, .9):
                game._accumulator = C.FIXED_DT * alpha
                self.assertEqual(launch_draw_position(game),
                                 (game.ball.x, game.ball.y + game.ball.vy * C.FIXED_DT * alpha))
        game._accumulator = 10.0
        self.assertEqual(launch_draw_position(game)[1], game.ball.y + game.ball.vy * C.FIXED_DT)

    def test_120hz_initial_flight_moves_every_frame_without_history_delay(self):
        game = launch()
        physical, displayed = [game.ball.y], [game.ball.y]
        for _ in range(24):
            game.step(1/120)
            physical.append(game.ball.y)
            displayed.append(launch_draw_position(game)[1])
        self.assertEqual(physical[0], physical[1])
        self.assertGreater(displayed[0], displayed[1])
        moves = [a-b for a,b in zip(displayed, displayed[1:])]
        self.assertTrue(all(d > 0 for d in moves))
        self.assertLess(max(moves) / min(moves), 1.3)

    def test_guard_uses_actual_arc_and_does_not_enter_contact_region(self):
        game = launch()
        radius = max(C.BALL_VIS_R, C.BALL_R * C.ARC_VISUAL)
        for shift in (-6, 0, 6, 40):
            game.geo['deflectors'] = [(x1,y1+shift,x2,y2+shift) for x1,y1,x2,y2 in game._base_deflectors]
            boundary = max(max(y1,y2) for _,y1,_,y2 in game.geo['deflectors']) + radius
            game.ball.y = boundary + 1
            game._accumulator = C.FIXED_DT / 2
            self.assertEqual(launch_draw_position(game)[1], boundary)
            game.ball.y = boundary - 1
            self.assertEqual(launch_draw_position(game)[1], game.ball.y)

    def test_no_upward_backstep_before_real_guide_contact_at_supported_cadences(self):
        for hz in (60, 90, 120, 144, 165, 185, 240):
            for power in (.2, .5, 1.0):
                random.seed(42)
                game = launch(power)
                last_y = game.ball.y
                for _ in range(hz):
                    game.step(1/hz)
                    if game.ball.arc_ease is not None:
                        self.assertEqual(launch_draw_position(game), (game.ball.x, game.ball.y))
                        break
                    x, y = launch_draw_position(game)
                    self.assertEqual(x, C.PLUNGER_X)
                    self.assertLessEqual(y, last_y + 1e-8, (hz,power,y,last_y))
                    last_y = y
                else:
                    self.fail('guide contact not reached')

    def test_other_phases_and_disabled_mode_are_exact_original_positions(self):
        for state in ('ready', 'charging', 'misfire', 'landing', 'landed'):
            game = launch()
            game._accumulator = C.FIXED_DT / 2
            game.state = state
            self.assertEqual(launch_draw_position(game), (game.ball.x,game.ball.y))
        for attribute, value in (('vx',1), ('vy',10), ('x',C.FIELD_R-50), ('arc_ease',[1,2,3,4]), ('_stall_retry',1)):
            game = launch()
            game._accumulator = C.FIXED_DT / 2
            setattr(game.ball, attribute, value)
            self.assertEqual(launch_draw_position(game), (game.ball.x,game.ball.y))
        game = launch()
        game.step(1/120)
        game.launch_extrapolation = False
        self.assertEqual(launch_draw_position(game), (game.ball.x,game.ball.y))
        game.launch_extrapolation = True
        game.reset_balance(False)
        self.assertEqual(launch_draw_position(game), (game.ball.x,game.ball.y))
        game.park_ball(reroll=False)
        self.assertEqual(launch_draw_position(game), (game.ball.x,game.ball.y))

    def test_drawing_does_not_mutate_ball_events_clock_or_random_state(self):
        game = launch()
        game.step(1/120)
        before = deepcopy({k:getattr(game.ball,k,None) for k in game.ball.__slots__})
        clocks = game.now, game.frame, game._accumulator
        rng = random.getstate(), shared_rng().getstate()
        events = list(game._ev)
        first = launch_draw_position(game)
        for _ in range(10):
            self.assertEqual(launch_draw_position(game), first)
        self.assertEqual(before, {k:getattr(game.ball,k,None) for k in game.ball.__slots__})
        self.assertEqual(clocks, (game.now,game.frame,game._accumulator))
        self.assertEqual(rng, (random.getstate(),shared_rng().getstate()))
        self.assertEqual(events, game._ev)

    def test_mode_comparison_keeps_whole_flight_events_and_settlement_identical(self):
        def trace(enabled, seed, power, intervals):
            random.seed(seed)
            shared_rng().seed(SFX_SEED)
            C._ARC_FRAME = 0
            game = launch(power, enabled)
            rows = []
            for i in range(4000):
                events = game.step(intervals[i % len(intervals)])
                launch_draw_position(game)
                ball = {k:getattr(game.ball,k,None) for k in game.ball.__slots__ if k not in ('born','item','_rng')}
                rows.append(deepcopy((game.state,ball,game.balance,game.plays,game.hits,game.round_plays,
                                     [tuple(getattr(e,k) for k in e.__slots__) for e in events])))
                if game.state == 'ready' and i > 25:
                    break
            return rows, random.getstate(), shared_rng().getstate()
        count = frames = 0
        with patch('time.time', return_value=0.0):
            for intervals in [(1/hz,) for hz in (30,60,90,120,144,165,240)] + [(1/120,1/90,1/165,1/60,.1,1/144)]:
                for seed in range(3):
                    for power in (.05,.2,.5,1.0):
                        original = trace(False,seed,power,intervals)
                        predicted = trace(True,seed,power,intervals)
                        self.assertEqual(original, predicted, (intervals,seed,power))
                        count += 1
                        frames += len(predicted[0])
        print('MATCH modes scenarios=%d frames=%d' % (count,frames))


if __name__ == '__main__':
    unittest.main()
