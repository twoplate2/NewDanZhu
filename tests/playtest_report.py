"""诊断报告的帧归属、慢帧上下文、有界缓存和未知数据口径。"""
import gc
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.bench.playtest_probe import PlayProbe, DiagnosticFrames, ACTIVE_PLAY
from danzhu.bench.playtest import PlaytestRecord, format_report


class ReportTests(unittest.TestCase):
    def test_swap_belongs_to_next_interval_and_accumulators_reset(self):
        clock=[0.];cpu=[0.]
        probe=PlayProbe(clock=lambda:clock[0],cpu=lambda:cpu[0])
        first=probe.take()
        probe.submit(.001,.004)
        probe.step('游戏逻辑',.002);probe.frame(1.5)
        cpu[0]=.002
        sample=probe.take()
        self.assertEqual(sample['swap_source_interval'],first['interval_id'])
        self.assertAlmostEqual(sample['swap_ms'],3)
        self.assertEqual(sample['thread_cpu_ms'],2)
        self.assertEqual(sample['frame_cpu_ms'],1.5)
        self.assertEqual(sample['steps']['游戏逻辑'],[1,2])
        after=probe.take()
        self.assertEqual(after['swap_ms'],0)
        self.assertEqual(after['steps'],{})

    def test_worst_case_keeps_before_and_after_with_bounded_memory(self):
        diag=DiagnosticFrames(limit=4)
        for i in range(1000):
            diag.append(i,i*.006,30 if i==777 else 6,2,dict(
                thread_cpu_ms=.2,frame_cpu_ms=.1,swap_ms=3,steps={'画布提交':[1,10 if i==777 else .1]}))
        doc=diag.document([777])
        self.assertEqual(len(doc['cases']),4)
        case=doc['cases'][0]
        self.assertEqual(case['trigger_index'],777)
        self.assertEqual([r['frame_index'] for r in case['frames']],list(range(769,782)))
        self.assertEqual(case['following_left'],0)
        self.assertEqual(len(diag.recent),8)
        self.assertEqual(doc['core_frame_count'],1000)
        self.assertEqual(doc['group_mean_ms']['slowest_1pct']['draw_ms'],10)
        self.assertLess(doc['group_mean_ms']['other_frames']['draw_ms'],.11)

    def test_gc_cleanup_and_unknown_not_zero(self):
        probe=PlayProbe();probe.start()
        self.assertIs(ACTIVE_PLAY[0],probe)
        self.assertIn(probe.gc_callback,gc.callbacks)
        probe.stop();probe.stop()
        self.assertIsNone(ACTIVE_PLAY[0]);self.assertNotIn(probe.gc_callback,gc.callbacks)
        diag=DiagnosticFrames();diag.append(0,1,6,0,None)
        self.assertIsNone(diag.document([0])['group_mean_ms']['slowest_1pct']['thread_cpu_ms'])

    def test_report_matches_full_slowest_percent_and_json_parses(self):
        t=[0.];record=PlaytestRecord(clock=lambda:t[0]);record.flip(0)
        for i in range(300):
            t[0]+=.03 if i in (55,155,255) else .006
            record.flip(2,dict(thread_cpu_ms=.2,steps={'面板文字':[1,.1]},swap_ms=1,frame_cpu_ms=.1))
        record.stop()
        doc=record.document()
        self.assertEqual(doc['low_1pct_by_phase'][2]['slowest_1pct_frames'],3)
        report=format_report(doc)
        parsed=json.loads(report.split('=== DATA JSON ===\n',1)[1])
        self.assertEqual(parsed['metrics']['frame_count'],300)
        self.assertEqual(parsed['diagnostics']['core_frame_count'],300)
        self.assertIn('不能直接判定',report)

    def test_memory_report_uses_samples_and_preserves_missing(self):
        document={'sensors':[{'memory_rss_bytes':150000000},
                             {'memory_rss_bytes':None,'memory_error':'unavailable'},
                             {'memory_rss_bytes':120000000}]}
        report=format_report(document)
        self.assertIn('首次 150.0MB；末次 120.0MB；采样最高 150.0MB；变化 -30.0MB',report)
        data=json.loads(report.split('=== DATA JSON ===\n',1)[1])
        self.assertIsNone(data['sensors'][1]['memory_rss_bytes'])
        self.assertIn('占用内存（RSS）：未采到',format_report({}))

    def test_gameplay_worst_survives_slower_test_gaps(self):
        diag=DiagnosticFrames(limit=4)
        for i in range(100):
            diag.append(i,i*.01,40 if i%2==0 else 12,7 if i%2==0 else 2,
                        dict(thread_cpu_ms=1,frame_cpu_ms=.3,swap_ms=1,steps={}))
        doc=diag.document([0])
        self.assertTrue(all(c['frames'][next(j for j,r in enumerate(c['frames']) if r['frame_index']==c['trigger_index'])]['phase']==7 for c in doc['cases']))
        self.assertEqual(len(doc['gameplay_cases']),4)
        self.assertTrue(all(next(r for r in c['frames'] if r['frame_index']==c['trigger_index'])['phase']==2 for c in doc['gameplay_cases']))


if __name__=='__main__':unittest.main()
