"""本轮继承与清理的行为回归：长按边界。"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu.game import Game


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


if __name__ == "__main__":
    unittest.main()
