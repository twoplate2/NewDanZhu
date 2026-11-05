"""随机实玩数据：逐帧紧凑记录，实时显示最近十秒；停止后统计全程。"""
from array import array
from collections import deque
from bisect import insort
import math
import heapq
import time

from .low_metrics import compute_metrics


class PlaytestRecord:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.started = clock()
        self.previous = None
        self.frames = array('d')
        self.recent = deque()
        self.events, self.sensors = [], []
        self.wall_ms = 0.
        self.active = True
        self.reason = None
        self.duration_s = 0.
        self.tail = []
        self.total_ms = 0.
        self.minimum_fps = self.minimum_low = None
        self.live = {}
        self.power_sum = self.power_count = 0
        self.charge_sum = self.charge_count = 0
        self.settings = {}

    def frame(self, ms):
        self.wall_ms += ms

    def flip(self, phase):
        now = self.clock()
        if not self.active:
            return False
        if self.previous is not None:
            interval = (now-self.previous)*1000
            if interval > 0:
                self.frames.extend((now-self.started, interval, self.wall_ms, phase))
                self.total_ms += interval
                # 最慢 60000 帧足够覆盖 100 分钟、1000Hz 的最慢 1%。
                # 有序小表避免每 0.5 秒重排全程百万帧，且不是逐帧瞬时 FPS 的平均。
                if len(self.tail)<60000 or interval>self.tail[0]:
                    insort(self.tail, interval)
                    if len(self.tail)>60000:
                        self.tail.pop(0)
                self.recent.append((now, interval))
                while self.recent and self.recent[0][0] < now-10:
                    self.recent.popleft()
        self.previous, self.wall_ms = now, 0.
        self.duration_s = now-self.started
        return self.duration_s < 6000

    def update_live(self):
        now = self.clock()
        one = [v for t,v in self.recent if t>now-1]
        ten = [v for t,v in self.recent if t>now-10]
        fps = 1000*len(one)/math.fsum(one) if one and now-self.started>=1 else None
        low = compute_metrics(ten)['low_1pct_fps'] if ten and now-self.started>=10 else None
        if fps is not None:
            self.minimum_fps = min(self.minimum_fps if self.minimum_fps is not None else fps, fps)
        if low is not None:
            self.minimum_low = min(self.minimum_low if self.minimum_low is not None else low, low)
        n = len(self.frames)//4
        k = max(1,n//100)
        average_low = 1000*k/math.fsum(self.tail[-k:]) if n and k<=len(self.tail) else None
        self.live = dict(current_fps=fps, minimum_fps=self.minimum_fps,
                         current_low=low, minimum_low=self.minimum_low, average_low=average_low,
                         average_fps=1000*n/self.total_ms if n else None)
        return self.live

    def sensor(self, sample):
        self.sensors.append(dict(elapsed_s=self.clock()-self.started, **sample))
        watts = sample.get('watts')
        if watts is not None:
            if sample.get('plugged'):
                self.charge_sum += watts; self.charge_count += 1
            else:
                self.power_sum += watts; self.power_count += 1

    def power_summary(self):
        return dict(average_power_w=self.power_sum/self.power_count if self.power_count else None,
                    discharge_samples=self.power_count,
                    average_charge_w=self.charge_sum/self.charge_count if self.charge_count else None,
                    charge_samples=self.charge_count)

    def metrics(self, full=False):
        if full:
            n=len(self.frames)//4
            if not n: return None
            k=max(1,n//100)
            return dict(frame_count=n,duration_ms=self.total_ms,average_fps=1000*n/self.total_ms,
                        low_1pct_fps=1000*k/math.fsum(self.tail[-k:]),max_ms=self.tail[-1])
        values = list(self.frames[1::4]) if full else [v for _, v in self.recent]
        return compute_metrics(values) if values else None

    def stop(self, reason='user'):
        if not self.active:
            return
        self.duration_s = self.clock()-self.started
        self.update_live()
        self.active = False
        self.reason = reason

    def document(self):
        if self.active:
            raise RuntimeError('stop simulation before saving')
        n=len(self.frames)//4
        worst=heapq.nlargest(min(200,n),range(n),key=lambda i:self.frames[i*4+1])
        selected=sorted({j for i in worst for j in range(max(0,i-2),min(n,i+3))})
        return dict(schema='danzhu.random_play.v1', mode='random_real_play',
                    timing_source='on_flip_application_pre_submit', present_source='unavailable',
                    reason=self.reason, metrics=self.metrics(full=True),
                    duration_s=self.duration_s, settings=dict(self.settings), live=dict(self.live),
                    power=self.power_summary(),
                    columns=['elapsed_s', 'interval_ms', 'frame_wall_ms', 'phase'],
                    phases=['ready', 'charging', 'flying', 'misfire', 'landing', 'landed', 'cup'],
                    frame_scope='worst_200_intervals_with_2_neighbors',
                    frame_indices=selected,
                    frames=[list(self.frames[i*4:i*4+4]) for i in selected],
                    events=list(self.events), sensors=list(self.sensors),
                    sensor_interval_s=.5, live_metrics_window_s=10)
