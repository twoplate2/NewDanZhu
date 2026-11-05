"""GC只能在画面稳定后执行，生命周期恢复原始状态。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.idle_gc import IdleGC


class FakeGC:
    def __init__(self,enabled=True):
        self.enabled=enabled;self.count=1000;self.collections=0
        self.older=[0,0];self.generations=[]
    def isenabled(self):return self.enabled
    def enable(self):self.enabled=True
    def disable(self):self.enabled=False
    def get_count(self):return (self.count,*self.older)
    def get_threshold(self):return (700,10,10)
    def collect(self,generation):
        self.generations.append(generation)
        self.collections+=1;self.count=0
        if generation==0:self.older[0]+=1
        elif generation==1:self.older=[0,self.older[1]+1]
        else:self.older=[0,0]
        return 7


class GCTests(unittest.TestCase):
    def test_busy_and_quiet_window(self):
        t=[0.];backend=FakeGC();policy=IdleGC(backend,lambda:t[0]);policy.start()
        self.assertFalse(backend.enabled)
        for i in range(100):t[0]=i;self.assertIsNone(policy.tick(False))
        policy.tick(True);t[0]+=.2;self.assertIsNone(policy.tick(True))
        t[0]+=.11;self.assertEqual(policy.tick(True)['collected'],7)
        self.assertEqual(backend.collections,1)
        t[0]+=10;self.assertIsNone(policy.tick(True))
        policy.stop();self.assertTrue(backend.enabled)

    def test_motion_restarts_wait_and_disabled_state_is_preserved(self):
        t=[0.];backend=FakeGC(False);p=IdleGC(backend,lambda:t[0]);p.start();p.tick(True)
        t[0]=.25;p.tick(False);t[0]=.5;p.tick(True)
        t[0]=.7;self.assertIsNone(p.tick(True))
        t[0]=.81;self.assertIsNotNone(p.tick(True))
        p.stop();self.assertFalse(backend.enabled)

    def test_sixty_six_quiet_windows_do_not_force_full_heap_sweeps(self):
        t=[0.];backend=FakeGC();p=IdleGC(backend,lambda:t[0]);p.start();p.tick(True)
        for i in range(66):
            backend.count=1000;t[0]=i*2+1;p.tick(True)
        self.assertEqual(backend.generations.count(0),60)
        self.assertEqual(backend.generations.count(1),6)
        self.assertEqual(backend.generations.count(2),0)

    def test_old_generation_requires_its_own_threshold(self):
        t=[0.];backend=FakeGC();p=IdleGC(backend,lambda:t[0]);p.start();p.tick(True)
        for i in range(111):
            backend.count=1000;t[0]=i*2+1;event=p.tick(True)
        self.assertEqual(backend.generations.count(2),1)
        self.assertEqual(event['generation'],2)
        self.assertEqual(event['counts_before'][2],10)
        self.assertEqual(event['counts_after'],[0,0,0])


if __name__=='__main__':unittest.main()
