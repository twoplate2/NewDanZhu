"""实玩诊断：仅测试时启用；全帧紧凑核心数据 + 有界最慢帧上下文。"""
from array import array
from collections import deque
import heapq
import math
import time

ACTIVE_PLAY = [None]
CORE_COLUMNS = ('thread_cpu_ms','frame_cpu_ms','swap_ms','draw_ms','monitor_ms')


class PlayProbe:
    def __init__(self, clock=time.perf_counter, cpu=time.thread_time):
        self.clock,self.cpu=clock,cpu
        self.previous_cpu=cpu()
        self.steps={}
        self.frame_cpu=0.
        self.frame_calls=0
        self.swap_ms=0.
        self.swap_source=None
        self.interval_id=0
        self.active=True

    def step(self, tag, seconds):
        if not self.active:return
        entry=self.steps.get(tag)
        if entry is None:self.steps[tag]=[1,seconds*1000]
        else:entry[0]+=1;entry[1]+=seconds*1000

    def frame(self,cpu_ms):
        self.frame_cpu+=cpu_ms;self.frame_calls+=1

    def submit(self,start,end):
        self.swap_ms+=(end-start)*1000
        self.swap_source=self.interval_id

    def take(self):
        cpu=self.cpu()
        sample=dict(interval_id=self.interval_id+1,thread_cpu_ms=max(0,(cpu-self.previous_cpu)*1000),frame_cpu_ms=self.frame_cpu,
                    frame_calls=self.frame_calls,swap_ms=self.swap_ms,
                    swap_source_interval=self.swap_source,steps=self.steps)
        self.previous_cpu=cpu;self.frame_cpu=0.;self.frame_calls=0
        self.swap_ms=0.;self.swap_source=None;self.steps={};self.interval_id+=1
        return sample


    def start(self):
        ACTIVE_PLAY[0]=self

    def stop(self):
        self.active=False
        if ACTIVE_PLAY[0] is self:ACTIVE_PLAY[0]=None


class DiagnosticFrames:
    def __init__(self, limit=128):
        self.core=array('f')
        self.recent=deque(maxlen=8)
        self.cases=[]
        self.gameplay_cases=[]
        self.pending=[]
        self.limit=limit

    def append(self,index,elapsed,interval,phase,sample):
        sample=sample or {}
        steps=sample.get('steps',{})
        draw=steps.get('画布提交',[0,0])[1] if 'steps' in sample else math.nan
        monitor=sum(steps.get(k,[0,0])[1] for k in ('面板刷新','面板文字','记录器')) if 'steps' in sample else math.nan
        self.core.extend((sample.get('thread_cpu_ms',math.nan),sample.get('frame_cpu_ms',math.nan),
                          sample.get('swap_ms',math.nan),draw,monitor))
        row=dict(frame_index=index,elapsed_s=elapsed,interval_ms=interval,phase=phase,**sample)
        for case in self.pending:
            case['frames'].append(row);case['following_left']-=1
        self.pending=[c for c in self.pending if c['following_left']>0]
        overall=len(self.cases)<self.limit or interval>self.cases[0][0]
        game_limit=min(64,self.limit)
        gameplay=phase!=7 and (len(self.gameplay_cases)<game_limit or interval>self.gameplay_cases[0][0])
        if overall or gameplay:
            case=dict(trigger_index=index,frames=list(self.recent)+[row],following_left=4)
            entry=(interval,index,case)
            if overall:
                if len(self.cases)<self.limit:heapq.heappush(self.cases,entry)
                else:heapq.heapreplace(self.cases,entry)
            if gameplay:
                if len(self.gameplay_cases)<game_limit:heapq.heappush(self.gameplay_cases,entry)
                else:heapq.heapreplace(self.gameplay_cases,entry)
            self.pending.append(case)
        self.recent.append(row)

    def document(self, low_indices,low01_indices=()):
        n=len(self.core)//len(CORE_COLUMNS)
        low=set(low_indices)
        groups={}
        for name,indices in (('slowest_1pct',low),('slowest_0_1pct',low01_indices),('other_frames',(i for i in range(n) if i not in low))):
            sums=[0.]*len(CORE_COLUMNS);counts=[0]*len(CORE_COLUMNS)
            for index in indices:
                for column in range(len(CORE_COLUMNS)):
                    value=self.core[index*len(CORE_COLUMNS)+column]
                    if math.isfinite(value):sums[column]+=value;counts[column]+=1
            groups[name]={key:(sums[i]/counts[i] if counts[i] else None) for i,key in enumerate(CORE_COLUMNS)}
        return dict(core_columns=CORE_COLUMNS,core_frame_count=n,group_mean_ms=groups,
                    cases=[entry[2] for entry in sorted(self.cases,reverse=True)],
                    gameplay_cases=[entry[2] for entry in sorted(self.gameplay_cases,reverse=True)],
                    gameplay_case_limit=min(64,self.limit),
                    case_limit=self.limit,context_before=8,context_after=4,
                    core_memory_bytes=len(self.core)*self.core.itemsize,
                    timing_note='子步骤含嵌套，不能相加；swap 是前一次提交，归属当前帧间隔。CPU 低不等于已证明 GPU 瓶颈。')
