"""显示错峰不影响账务：大字、余额、累计各自只在规定时刻更新。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.game import Game


class DisplayTests(unittest.TestCase):
    def game(self):
        g=Game();g.state='landed';g._land_hold=100;g.take_events()
        return g

    def advance(self,g,until):
        events=[]
        while g.now<until-1e-8:events.extend(g.step(.01))
        return events

    def test_win_reveal_stagger(self):
        g=self.game();g.balance=1200;g.display_balance=1000;g._anim_pending=True
        g._reveal_win(2,200)
        self.assertTrue(any(e.kind=='big_result' for e in g.take_events()))
        self.assertEqual(g.balance,1200)
        self.assertFalse(any(e.kind=='stats' for e in self.advance(g,.11)))
        self.assertEqual(g.display_balance,1000)
        self.advance(g,.13);self.assertEqual(g.display_balance,1200)
        self.assertFalse(any(e.kind=='stats' for e in self.advance(g,.23)))
        self.assertEqual(sum(e.kind=='stats' for e in self.advance(g,.25)),1)

    def test_loss_waits_for_bounce_then_delay(self):
        g=self.game();g.multipliers[0]=0;g.state='landing';before=g.balance
        g.settle(0);self.assertEqual(g.balance,before)
        self.assertTrue(g._lose_stats_pending)
        self.assertFalse(any(e.kind=='stats' for e in g.take_events()))
        g.state='landed'
        self.assertFalse(any(e.kind=='stats' for e in self.advance(g,.12)))
        self.assertEqual(sum(e.kind=='stats' for e in self.advance(g,.14)),1)

    def test_reset_and_spurious_launch_do_not_leave_stale_display(self):
        g=self.game();g.balance=1200;g._reveal_win(2,200)
        at=g._balance_release_at;g.launch();self.assertEqual(g._balance_release_at,at)
        g.reset_balance(False);g.take_events();self.advance(g,.5)
        self.assertEqual(g.balance,1000);self.assertEqual(g.display_balance,1000)
        self.assertIsNone(g._stats_release_at)

    def test_routine_status_does_not_emit(self):
        g=self.game();g._status('中奖! +200 (x2)');self.assertFalse(g.take_events())
        g._status('异常',important=True)
        self.assertTrue(g.take_events()[0].data['important'])


if __name__=='__main__':unittest.main()
