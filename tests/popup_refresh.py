"""验证真实弹窗入口共用合并策略，立即/延迟请求均进入报告计时。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu.platform.refresh_burst import RefreshBurst


class Event:
    def __init__(self, at, callback):
        self.at, self.callback, self.cancelled = at, callback, False

    def cancel(self):
        self.cancelled = True


class Scheduler:
    def __init__(self):
        self.now = 0.
        self.events = []

    def schedule(self, callback, delay):
        event = Event(self.now + delay, callback)
        self.events.append(event)
        return event

    def advance(self, until):
        while True:
            ready = [e for e in self.events if not e.cancelled and e.at <= until]
            if not ready:
                break
            event = min(ready, key=lambda e:e.at)
            self.events.remove(event)
            self.now = event.at
            event.callback(0)
        self.now = until


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.clock = Scheduler()
        self.calls = []
        self.burst = RefreshBurst(lambda delayed:self.calls.append((self.clock.now,delayed)),
                                  self.clock.schedule,lambda:self.clock.now)

    def test_same_frame_open_confirm_reduces_four_requests_to_two(self):
        self.burst.request();self.burst.request()
        self.assertEqual(self.calls,[(0.,False)])
        self.clock.advance(.36)
        self.assertEqual(self.calls,[(0.,False),(.35,True)])
        self.assertIsNone(self.burst.pending)

    def test_close_after_animation_keeps_immediate_and_followup(self):
        self.burst.request();self.clock.advance(1.)
        self.burst.request();self.clock.advance(1.36)
        self.assertEqual(self.calls,[(0.,False),(.35,True),(1.,False),(1.35,True)])

    def test_many_switches_keep_only_last_settled_callback(self):
        self.burst.request()
        for at in (.02,.06,.12,.18,.24):
            self.clock.advance(at);self.burst.request()
        self.clock.advance(.58)
        self.assertFalse(any(delayed for _,delayed in self.calls))
        self.clock.advance(.60)
        self.assertEqual(sum(delayed for _,delayed in self.calls),1)
        self.assertAlmostEqual(self.calls[-1][0],.59)
        self.assertEqual(sum(not e.cancelled for e in self.clock.events),0)

    def test_close_just_after_followup_still_requests_immediately(self):
        self.burst.request();self.clock.advance(.36)
        self.burst.request()
        self.assertEqual(self.calls,[(0.,False),(.35,True),(.36,False)])
        self.clock.advance(.72)
        self.assertEqual(len(self.calls),4)
        self.assertTrue(self.calls[-1][1])

    def test_different_popup_instances_share_policy_and_timer_is_measured(self):
        from danzhu.ui import widgets as W
        from danzhu.platform import device as D
        calls=[];tags=[]
        burst=RefreshBurst(W._apply_popup_refresh,self.clock.schedule,lambda:self.clock.now)
        with patch.object(W,'platform','android'),patch.object(W,'_POPUP_REFRESH_BURST',burst),\
             patch.object(D,'_apply_fps_cap',side_effect=lambda **kw:calls.append(kw)),\
             patch.object(W,'_brk_add',side_effect=lambda tag,stamp:tags.append(tag)):
            W.RotPopup._reassert_high_refresh(object())
            W.RotPopup._reassert_high_refresh(object())
            self.clock.advance(.36)
        self.assertEqual(calls,[{'reuse_modes':True},{'reuse_modes':True}])
        self.assertEqual(tags,['弹窗高刷立即','弹窗高刷延迟'])

    def test_failure_measured_and_followup_can_retry(self):
        from danzhu.ui import widgets as W
        from danzhu.platform import device as D
        tags=[]
        burst=RefreshBurst(W._apply_popup_refresh,self.clock.schedule,lambda:self.clock.now)
        with patch.object(D,'_apply_fps_cap',side_effect=[RuntimeError('temporary'),120]) as apply,\
             patch.object(W,'_brk_add',side_effect=lambda tag,stamp:tags.append(tag)):
            burst.request();self.clock.advance(.36)
        self.assertEqual(apply.call_count,2)
        self.assertEqual(tags,['弹窗高刷立即','弹窗高刷延迟'])

    def test_desktop_does_not_request_android_refresh(self):
        from danzhu.ui import widgets as W
        with patch.object(W,'platform','win'),patch.object(W,'_POPUP_REFRESH_BURST') as burst:
            W.RotPopup._reassert_high_refresh(object())
            burst.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
