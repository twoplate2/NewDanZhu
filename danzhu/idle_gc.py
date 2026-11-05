"""前台禁用自动循环GC，只在画面稳定的空闲窗口按需回收；引用计数照常工作。"""
from collections import deque
import gc
import time


class IdleGC:
    def __init__(self, backend=gc, clock=time.perf_counter, quiet_delay=.3):
        self.backend,self.clock,self.quiet_delay=backend,clock,quiet_delay
        self.was_enabled=backend.isenabled()
        self.active=False
        self.quiet_since=None
        self.last_collect=float('-inf')
        self.events=deque(maxlen=64)

    def start(self):
        if not self.active:
            self.backend.disable();self.active=True;self.quiet_since=None

    def stop(self):
        if self.active:
            self.active=False;self.quiet_since=None
            if self.was_enabled:self.backend.enable()

    def tick(self, quiet):
        if not self.active:return None
        now=self.clock()
        if not quiet:
            self.quiet_since=None
            return None
        if self.quiet_since is None:self.quiet_since=now
        if now-self.quiet_since<self.quiet_delay or now-self.last_collect<1.:
            return None
        threshold=self.backend.get_threshold()[0]
        if self.backend.get_count()[0]<max(1,threshold):return None
        start=self.clock()
        collected=self.backend.collect(2)
        end=self.clock()
        self.last_collect=end
        event=dict(monotonic_s=start,duration_ms=(end-start)*1000,collected=collected,generation=2)
        self.events.append(event)
        return event
