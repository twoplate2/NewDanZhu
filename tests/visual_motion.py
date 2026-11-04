"""弹簧动画和弹珠显示回归；不改弹珠碰撞尺寸。"""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu import config as C
from danzhu.ui.game_area import GameArea
from danzhu.ui import text as T
from danzhu.game import Game
from kivy.graphics import Rectangle, RoundedRectangle


class Counted:
    def __init__(self):
        self.writes = 0

    def __setattr__(self, name, value):
        if name in ("points", "rgb"):
            self.writes += 1
        object.__setattr__(self, name, value)


class MotionTests(unittest.TestCase):
    def test_spring_30hz_rebounds_stable_color_and_no_idle_writes(self):
        for hz in (60, 120, 185):
            area = SimpleNamespace(model=SimpleNamespace(state="charging", power=1.0),
                _spring_power=0., _spring_vel=0., _spring_accum=0.,
                _spring_draw_key=None, _spring_color_key=None,
                _s=1., _ox=0., _oyt=660., _px=lambda x: x, _py=lambda y: 660-y,
                _spring_bars=[Counted() for _ in range(3)], _spring_bar_col=Counted())
            GameArea._tick_spring(area, 1/hz)
            area.model.state = "flying"
            colors = set()
            minimum = 0.
            previous = area._spring_power
            crossings = 0
            for frame in range(hz*3):
                GameArea._tick_spring(area, 1/hz)
                if previous * area._spring_power < 0:
                    crossings += 1
                previous = area._spring_power
                minimum = min(minimum, area._spring_power)
                colors.add(area._spring_bar_col.rgb)
                if not area._spring_power and not area._spring_vel:
                    break
            duration = (frame+1)/hz
            self.assertGreater(duration, 1.5)
            self.assertLess(duration, 2.2)
            self.assertGreaterEqual(crossings, 5)
            self.assertLessEqual(crossings, 6)
            self.assertLess(minimum, 0.0)
            self.assertEqual(len(colors), 1)
            self.assertLessEqual(area._spring_bars[0].writes, int(duration * 30) + 2)
            writes = [x.writes for x in area._spring_bars]+[area._spring_bar_col.writes]
            for _ in range(hz):
                GameArea._tick_spring(area, 1/hz)
            self.assertEqual(writes, [x.writes for x in area._spring_bars]+[area._spring_bar_col.writes])

    def test_size_is_visual_only_and_texture_is_cached_128(self):
        self.assertEqual(C.BALL_R, 9)
        self.assertEqual(C.ARC_VISUAL, 1.4)
        self.assertEqual(C.BALL_VIEW, 1.65)
        texture = T.ball_texture()
        self.assertEqual(texture.size, (128, 128))
        self.assertIs(T.ball_texture(), texture)

    def test_settled_ball_slots_and_dividers_share_visible_floor(self):
        game = Game()
        game.ball.y = C.FLOOR - C.BALL_R
        game.ball.squash = 1.0
        game.state = "landed"
        area = GameArea(SimpleNamespace(game=game))
        for scale in (0.7, 1.0, 2.0):
            area.size = (C.CW * scale, C.CH * scale)
            area._redraw()
            floor = area._ball_e.pos[1]
            slots = [i for i in area.canvas.children if isinstance(i, RoundedRectangle)]
            self.assertEqual(len(slots), C.NUM_SLOTS)
            for slot in slots:
                self.assertAlmostEqual(slot.pos[1], floor, places=4)
            dividers = [i for i in area.canvas.children if isinstance(i, Rectangle)
                        and abs(i.size[0] - C.DIV_W * scale) < 1e-4
                        and abs(i.size[1] - (C.FLOOR_VIEW_Y - C.DIV_TOP) * scale) < 1e-4]
            self.assertEqual(len(dividers), C.NUM_SLOTS - 1)
            for divider in dividers:
                self.assertAlmostEqual(divider.pos[1], floor, places=4)
        self.assertEqual(game.ball.y, C.FLOOR - C.BALL_R)

if __name__ == "__main__":
    unittest.main()
