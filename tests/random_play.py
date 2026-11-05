"""随机实玩统计：一秒帧率、十秒 Low、全程 Low、功耗和百分钟边界。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.bench.playtest import PlaytestRecord
from danzhu.bench.low_metrics import compute_metrics


class PlaytestTests(unittest.TestCase):
    def setUp(self):
        self.t=0.
        self.r=PlaytestRecord(clock=lambda:self.t)
        self.r.flip(0)

    def tick(self, ms):
        self.t+=ms/1000
        self.r.frame(.2)
        return self.r.flip(2)

    def test_windows_minima_and_full_low(self):
        for i in range(1300):
            self.tick(50 if i==1050 else 10)
            if i%50==0:self.r.update_live()
            if i==500:self.assertIsNone(self.r.live['current_low'])
        live=self.r.update_live()
        reference=compute_metrics(list(self.r.frames[1::4]))
        ten=[self.r.frames[i+1] for i in range(0,len(self.r.frames),4) if self.r.frames[i]>self.t-10]
        self.assertAlmostEqual(live['current_fps'],compute_metrics(ten)['average_fps'])
        self.assertAlmostEqual(live['current_low'],compute_metrics(ten)['low_1pct_fps'])
        self.assertLess(live['minimum_fps'],100)
        self.assertLess(live['minimum_low'],100)
        self.assertAlmostEqual(live['average_low'],reference['low_1pct_fps'])
        self.assertAlmostEqual(self.r.metrics(full=True)['average_fps'],reference['average_fps'])

    def test_current_power_readings_kept_without_average(self):
        for p in (2,4,None):self.r.sensor(dict(watts=p,plugged=0))
        self.r.sensor(dict(watts=9,plugged=1))
        self.assertEqual([s['watts'] for s in self.r.sensors],[2,4,None,9])
        self.r.stop()
        self.assertNotIn('power',self.r.document())

    def test_stop_and_hundred_minutes(self):
        self.t=5999.99
        self.assertTrue(self.r.flip(0))
        self.t=6000
        self.assertFalse(self.r.flip(0))
        self.r.stop('100_minutes')
        n=len(self.r.frames)
        self.t=7000;self.r.flip(0)
        self.assertEqual(len(self.r.frames),n)
        self.assertEqual(self.r.duration_s,6000)

    def test_report_keeps_slow_frames_and_settings(self):
        self.r.settings=dict(bet=100,rtp=3.6)
        for i in range(1200):self.tick(80 if i==600 else 10)
        self.r.stop()
        doc=self.r.document()
        self.assertEqual(doc['settings'],dict(bet=100,rtp=3.6))
        self.assertIn(600,doc['frame_indices'])
        self.assertEqual(doc['metrics']['frame_count'],1200)
        self.assertLessEqual(len(doc['frames']),1000)

    def test_infrequent_display_does_not_lose_historical_low(self):
        t=[0.]
        frequent=PlaytestRecord(clock=lambda:t[0]);delayed=PlaytestRecord(clock=lambda:t[0])
        frequent.flip(0);delayed.flip(0)
        for i in range(1,4001):
            t[0]=i*.01+(.1 if i>=1200 else 0)
            frequent.flip(2);delayed.flip(2)
            if i%50==0:frequent.update_live()
        a=frequent.update_live();b=delayed.update_live()
        for key in ('current_fps','minimum_fps','current_low','minimum_low','average_low'):
            self.assertAlmostEqual(a[key],b[key],msg=key)
        self.assertLess(b['minimum_low'],b['current_low'])

    def test_point_one_percent_uses_ceiling_and_ten_second_groups(self):
        for i in range(1,1201):
            self.t=i*(10-.053)/1198 if i<=1198 else (9.967 if i==1199 else 10)
            self.r.flip(2)
        live=self.r.update_live()
        self.assertEqual(self.r.low01_groups[0]['frame_count'],1200)
        self.assertEqual(self.r.low01_groups[0]['selected_frames'],2)
        expected=2000/53
        self.assertAlmostEqual(live['average_low_0_1pct'],expected)
        for i in range(1,601):
            self.t=10+i/60;self.r.flip(2)
        live=self.r.update_live()
        self.assertEqual(self.r.low01_groups[1]['selected_frames'],1)
        self.assertAlmostEqual(live['minimum_low_0_1pct'],expected)
        self.assertAlmostEqual(live['average_low_0_1pct'],(expected+60)/2)
        self.assertEqual(self.r.metrics(full=True)['low_0_1pct_count'],2)

    def test_worker_snapshot_isolated_from_new_frames(self):
        for _ in range(1200):self.tick(10)
        work=self.r.snapshot_live()
        for _ in range(50):self.tick(30)
        work.compute_live();self.r.apply_live(work)
        self.assertAlmostEqual(self.r.live['average_fps'],100)
        self.assertEqual(self.r._stats_cursor,1200)
        self.r.update_live()
        self.assertEqual(self.r._stats_cursor,1250)


if __name__=='__main__':unittest.main()
