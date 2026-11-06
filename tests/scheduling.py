"""线程调度偏好、失败恢复、后台继承和benchmark嵌套范围。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.platform import scheduling as S


class Backend:
    def __init__(self):
        self.current=42
        self.masks={42:set(range(8))}
        self.calls=[]
        self.reject=False
    def sched_getaffinity(self,tid):
        return set(self.masks[tid or self.current])
    def sched_setaffinity(self,tid,mask):
        tid=tid or self.current
        self.calls.append((tid,set(mask)))
        if self.reject and set(mask)=={6,7}:raise PermissionError('denied')
        self.masks[tid]=set(mask)


def topology(cpu,name):
    if name=='cpu_capacity':return 1024 if cpu in (6,7) else 762
    return 4608000 if cpu in (6,7) else 3628800


class PolicyTests(unittest.TestCase):
    def make(self):
        backend=Backend()
        return backend,S.GameCpuPolicy(android=True,backend=backend,read_cpu=topology,tid=42)

    def test_default_and_toggle_restore_only_owner(self):
        b,p=self.make();p.apply()
        self.assertEqual(b.calls,[])
        b.masks[43]=set(range(8))
        p.set_preference(True)
        self.assertEqual(b.masks[42],{6,7})
        self.assertEqual(b.masks[43],set(range(8)))
        p.set_preference(False)
        self.assertEqual(b.masks[42],set(range(8)))
        self.assertEqual(p.snapshot()['reason'],'system_default')

    def test_pause_resume_and_same_preference_no_extra_write(self):
        b,p=self.make();p.set_preference(True)
        calls=len(b.calls);p.apply();self.assertEqual(len(b.calls),calls)
        p.suspend();self.assertEqual(b.masks[42],set(range(8)))
        self.assertTrue(p.prefer_fast)
        p.resume();self.assertEqual(b.masks[42],{6,7})
        p.suspend();self.assertFalse(p.snapshot()['active'])

    def test_permission_failure_reports_and_restores(self):
        b,p=self.make();b.reject=True;p.set_preference(True)
        self.assertEqual(b.masks[42],set(range(8)))
        self.assertEqual(p.snapshot()['reason'],'request_failed')
        self.assertIn('denied',p.snapshot()['error'])

    def test_topology_not_hardcoded_and_frequency_fallback(self):
        b=Backend();b.masks[42]={1,3,5}
        p=S.GameCpuPolicy(android=True,backend=b,read_cpu=lambda c,n:0 if n=='cpu_capacity' else 4000 if c==3 else 2000,tid=42)
        p.set_preference(True);self.assertEqual(b.masks[42],{3})
        b=Backend();p=S.GameCpuPolicy(android=True,backend=b,read_cpu=lambda c,n:1000,tid=42)
        p.set_preference(True)
        self.assertEqual(b.calls,[])
        self.assertEqual(p.reason,'no_distinct_fast_cluster')

    def test_background_worker_resets_inherited_affinity(self):
        b=Backend();b.current=43;b.masks[43]={6,7}
        with patch.object(S,'os',b),patch.object(S,'_WORKER_BASELINE',set(range(8))),patch.object(S,'_OWNER_TID',42),patch.object(S.threading,'get_native_id',return_value=43):
            S.reset_worker_affinity()
        self.assertEqual(b.masks[43],set(range(8)))
        seen=[]
        with patch.object(S,'reset_worker_affinity',side_effect=lambda:seen.append('reset')):
            t=S.BackgroundThread(target=lambda:seen.append('target'));t.start();t.join()
        self.assertEqual(seen,['reset','target'])

    def test_benchmark_scope_restores_user_selection(self):
        from danzhu.platform import benchcpu as B
        for enabled in (False,True):
            b,p=self.make();p.set_preference(enabled)
            expected=b.sched_getaffinity(42)
            with patch.object(B,'platform','android'),patch.object(B,'os',b),patch.object(B,'_BENCH_PIN_FAST_CORES',True),patch.object(B,'_bench_cpu_max_khz',side_effect=lambda c:topology(c,'freq')):
                before=B._bench_pin_fast_cpus()
                self.assertEqual(b.sched_getaffinity(42),{6,7})
                B._bench_restore_cpu_affinity(before)
                self.assertEqual(b.sched_getaffinity(42),expected)
            p.suspend();self.assertEqual(b.sched_getaffinity(42),set(range(8)))


if __name__=='__main__':unittest.main()
