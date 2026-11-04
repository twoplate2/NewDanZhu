"""本轮继承与清理的行为回归：长按边界、GC 回调去重、结束后解除监听。"""

import gc
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu.game import Game
from danzhu.ui.bench import BenchMixin


class InheritanceTests(unittest.TestCase):
    def test_hidden_tier_hold_is_one_second_and_fires_once(self):
        opened = []
        game = Game(on_rtp_popup=lambda *args: opened.append(args))
        game.now = 10.0
        game.begin_rtp_hold()
        game.now = 10.999
        game.check_title_hold()
        self.assertEqual(opened, [])
        game.now = 11.0
        game.check_title_hold()
        self.assertEqual(len(opened), 1)
        game.now = 12.0
        game.check_title_hold()
        self.assertEqual(len(opened), 1)

    def test_benchmark_menu_still_requires_three_seconds(self):
        game = Game()
        game.take_events()
        game.now = 10.0
        game.begin_bench_hold()
        game.now = 12.999
        game.check_title_hold()
        self.assertNotIn("bench_menu", [e.kind for e in game.take_events()])
        game.now = 13.0
        game.check_title_hold()
        self.assertIn("bench_menu", [e.kind for e in game.take_events()])

    def test_gc_registration_and_cleanup_are_idempotent(self):
        host = BenchMixin()
        host._bench_gc = {}
        host._bench_gc_t0 = 0.0
        before = list(gc.callbacks)
        enabled = gc.isenabled()
        gc.disable()
        try:
            # 模拟异常退出遗留多份同一绑定方法。
            gc.callbacks.extend([host._bench_gc_cb] * 3)
            host._bench_gc_listen(True)
            host._bench_gc_listen(True)
            self.assertEqual(gc.callbacks.count(host._bench_gc_cb), 1)
            gc.collect(0)
            self.assertEqual(host._bench_gc[0][0], 1)
            host._bench_gc_listen(False)
            host._bench_gc_listen(False)
            self.assertEqual(gc.callbacks, before)
            gc.callbacks.extend([host._bench_gc_cb] * 2)
            self.assertEqual(host._bench_collect_diag(), {})
            self.assertEqual(gc.callbacks, before)
        finally:
            gc.callbacks[:] = before
            if enabled:
                gc.enable()


if __name__ == "__main__":
    unittest.main()
