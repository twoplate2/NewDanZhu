"""Targeted capture state/accounting tests; no GUI or player configuration access."""
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from danzhu.bench.low_runtime import Capture


class Time:
    t = 0.0
    def __call__(self):
        return self.t


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.time = Time()
        self.c = Capture(clock=self.time)
        self.game = SimpleNamespace(state='ready', now=10, _anim_start_time=0,
                                    _anim_dur=0, _anim_pending=False)
        self.fx = SimpleNamespace(mode='idle', _dirty=False)
        self.area = SimpleNamespace(win_fx=self.fx, _effects=[])

    def shot(self, variant=1):
        self.c.plan(100, variant, 123)
        self.c.scheduled()
        self.c.launched()
        self.c.settled(100)

    def terminal(self):
        self.c.frame_end(self.game, self.area, 1, .5)
        self.c.draw()
        self.c.flip('待机')
        self.c.submit(self.time.t, self.time.t+.001)

    def test_complete_five_requires_committed_terminal(self):
        for i in range(5):
            self.shot(i+1)
            self.fx.mode = 'result'
            self.c.frame_end(self.game, self.area, 1, .5)
            self.assertNotEqual(self.c.shot['state'], 'visual_finished')
            self.fx.mode = 'idle'
            self.area._effects = [{'kind': 'big'}]
            self.c.frame_end(self.game, self.area, 1, .5)
            self.assertIsNone(self.c.shot['terminal_loop'])
            self.area._effects = []
            self.c.frame_end(self.game, self.area, 1, .5)
            self.time.t += .01
            self.c.flip('待机')
            self.assertNotEqual(self.c.shot['state'], 'visual_finished')
            self.c.submit(0, .001)  # no draw after terminal -> cannot finish
            self.assertNotEqual(self.c.shot['state'], 'visual_finished')
            self.time.t += .01
            self.terminal()
        self.assertTrue(self.c.complete())
        self.assertTrue(self.c.freeze()['valid'])
        self.assertEqual(len([e for e in self.c.events if e['event']=='visual_finished']), 5)

    def test_delayed_cup_and_stats_tasks_block_completion(self):
        self.shot()
        self.c.task('delayed_cup', True)
        self.terminal()
        self.assertEqual(self.c.shot['state'], 'settled')
        self.c.task('delayed_cup', False)
        self.terminal()
        self.assertEqual(self.c.shot['state'], 'visual_finished')

    def test_all_frame_calls_accumulate_and_empty_interval_is_zero(self):
        self.c.flip('待机')
        self.c.frame_end(self.game, self.area, 2, .3)
        self.c.frame_end(self.game, self.area, 3, .4)
        self.time.t += .01
        self.c.flip('飞行')
        self.assertEqual(self.c.frames[-1]['frame_calls'], 2)
        self.assertEqual(self.c.frames[-1]['frame_wall_ms'], 5)
        self.assertAlmostEqual(self.c.frames[-1]['frame_cpu_ms'], .7)
        self.time.t += .01
        self.c.flip('飞行')
        self.assertEqual(self.c.frames[-1]['frame_calls'], 0)
        self.assertEqual(self.c.frames[-1]['frame_wall_ms'], 0)

    def test_overflow_and_pause_never_valid(self):
        self.c.max_records = 1
        self.c.flip('飞行')
        for _ in range(3):
            self.time.t += .01
            self.c.flip('飞行')
        summary = self.c.freeze('paused')
        self.assertEqual(len(self.c.frames), 1)
        self.assertEqual(summary['lost_records'], 2)
        self.assertFalse(summary['valid'])
        self.assertIn('paused', summary['invalid_reasons'])

    def test_reentry_duplicate_and_incomplete(self):
        self.c.plan(100, 1, 2)
        self.c.scheduled()
        self.c.launched()
        self.c.launched()
        self.assertIn('duplicate_or_unplanned_launch', self.c.freeze()['invalid_reasons'])
        self.assertFalse(self.c.summary['complete'])

    def test_normal_overlap_allowed_and_preserves_tasks(self):
        self.c = Capture(mode='normal', clock=self.time)
        self.c.launched()
        self.c.settled(5)
        old = self.c.shot
        self.c.task('delayed_cup', True, old)
        self.c.launched()
        self.c.settled(20)
        self.c.task('delayed_cup', False, old)
        self.assertFalse(old['pending'])
        self.assertNotIn('overlapping_shot', self.c.reasons)
        self.time.t += .01
        self.terminal()
        self.assertTrue(self.c.complete())

    def test_export_only_after_freeze_and_scene_signature_stable(self):
        self.shot()
        self.terminal()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'session.json'
            with self.assertRaises(RuntimeError):
                self.c.export(path)
            self.c.freeze()
            self.c.export(path)
            doc = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(doc['schema'], 'danzhu.low.v1')
            self.assertEqual(doc['meta']['timing_source'], 'on_flip_application_pre_submit')
            signature = doc['meta']['scenario']['signature']
            other = Capture(clock=self.time)
            other.plan(100, 1, 123)
            other.freeze()
            self.assertEqual(signature, other.meta['scenario']['signature'])

    def test_real_driver_timeout_before_all_state_returns(self):
        tree = ast.parse((ROOT/'danzhu/ui/bench.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='BenchMixin')
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='_auto_launch_tick')
        namespace = {'time': SimpleNamespace(perf_counter=self.time)}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<driver>', 'exec'), namespace)
        for state in ('charging','flying','landing','misfire','ready','unknown'):
            c = Capture(clock=self.time, watchdog_sec=.1)
            host = SimpleNamespace(_low_capture=c, game=SimpleNamespace(state=state),
                                   _launch_count=5, _target_launches=5)
            reasons = []
            host._low_interrupt = reasons.append
            self.time.t += .2
            self.assertFalse(namespace['_auto_launch_tick'](host, .1))
            self.assertEqual(reasons, ['watchdog_timeout'])


if __name__ == '__main__':
    unittest.main()
