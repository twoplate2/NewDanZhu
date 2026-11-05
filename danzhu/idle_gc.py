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
        counts=self.backend.get_count()
        thresholds=self.backend.get_threshold()
        if thresholds[0]<=0 or counts[0]<thresholds[0]:return None
        # 年轻代达到阈值不等于要全堆扫描。显式collect会推进代计数，按配置逐代升级。
        generation=0
        if counts[1]>=max(1,thresholds[1]):generation=1
        if counts[2]>=max(1,thresholds[2]):generation=2
        start=self.clock()
        collected=self.backend.collect(generation)
        end=self.clock()
        self.last_collect=end
        event=dict(monotonic_s=start,duration_ms=(end-start)*1000,collected=collected,generation=generation,
                   counts_before=list(counts),thresholds=list(thresholds),counts_after=list(self.backend.get_count()))
        self.events.append(event)
        return event
