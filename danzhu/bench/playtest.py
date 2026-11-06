"""帧率测试数据：逐帧紧凑记录，实时显示最近十秒；停止后统计全程。"""
from array import array
from collections import deque
from bisect import insort
import math
import heapq
import time

from .low_metrics import compute_metrics
from .playtest_probe import DiagnosticFrames


class PlaytestRecord:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.started = clock()
        self.previous = None
        self.frames = array('d')
        self.recent = deque()
        self.events, self.sensors, self.gaps = [], [], []
        self.display_events=[]
        self.wall_ms = 0.
        self.active = True
        self.reason = None
        self.duration_s = 0.
        self.tail = []
        self.total_ms = 0.
        self.minimum_fps = self.minimum_low = None
        self.live = {}
        self.settings = {}
        self.diagnostics=DiagnosticFrames()
        self._stats_cursor=0
        self._stats_window=deque()
        self._next_stats_at=.5
        self.low01_groups=[]
        self._low01_pending=[]
        self._low01_end=10.

    def frame(self, ms):
        self.wall_ms += ms

    def flip(self, phase, diagnostic=None):
        now = self.clock()
        if not self.active:
            return False
        if self.previous is not None:
            interval = (now-self.previous)*1000
            if interval > 0:
                self.frames.extend((now-self.started, interval, self.wall_ms, phase))
                self.diagnostics.append(len(self.frames)//4-1,now-self.started,interval,phase,
                                        dict(diagnostic or {},frame_wall_ms=self.wall_ms))
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

    def _window_values(self, at, track=True):
        while self._stats_window and self._stats_window[0][0]<=at-10:
            self._stats_window.popleft()
        one=[v for t,v in self._stats_window if t>at-1]
        ten=[v for _,v in self._stats_window]
        one_fps=1000*len(one)/math.fsum(one) if one and at>=1 else None
        fps=1000*len(ten)/math.fsum(ten) if ten and at>=10 else None
        k=max(1,len(ten)//100)
        low=1000*k/math.fsum(sorted(ten)[-k:]) if ten and at>=10 else None
        if one_fps is not None and track:
            self.minimum_fps = min(self.minimum_fps if self.minimum_fps is not None else one_fps, one_fps)
        if low is not None and track:
            self.minimum_low = min(self.minimum_low if self.minimum_low is not None else low, low)
        return fps,low

    def snapshot_live(self):
        """主线程只复制新增帧和小窗口；后台不读写仍在采集的容器。"""
        work=PlaytestRecord.__new__(PlaytestRecord)
        work.started=0.
        at=self.clock()-self.started
        work.clock=lambda:at
        work._full_count=len(self.frames)//4
        work._commit_cursor=work._full_count
        work.frames=self.frames[self._stats_cursor*4:]
        work._stats_cursor=0
        work._stats_window=deque(self._stats_window)
        work._next_stats_at=self._next_stats_at
        work.minimum_fps=self.minimum_fps
        work.minimum_low=self.minimum_low
        work.tail=self.tail[-max(1,work._full_count//100):]
        work.total_ms=self.total_ms
        work.low01_groups=list(self.low01_groups)
        work._low01_pending=list(self._low01_pending)
        work._low01_end=self._low01_end
        return work

    def apply_live(self,work):
        self._stats_cursor=work._commit_cursor
        for name in ('_stats_window','_next_stats_at','minimum_fps','minimum_low','live',
                     'low01_groups','_low01_pending','_low01_end'):
            setattr(self,name,getattr(work,name))
        return self.live

    def update_live(self):
        work=self.snapshot_live();work.compute_live()
        return self.apply_live(work)

    def _finish_low01_group(self):
        values=self._low01_pending
        if values:
            k=max(1,(len(values)+999)//1000)
            self.low01_groups.append(dict(start_s=self._low01_end-10,end_s=self._low01_end,
                frame_count=len(values),selected_frames=k,
                fps=1000*k/math.fsum(sorted(values)[-k:]),worst_ms=max(values)))
        self._low01_pending=[];self._low01_end+=10

    def compute_live(self, yielding=False):
        # 只在发射间隔/结束时做统计；按历史0.5秒窗口补算，不能因少刷新而漏掉最低值。
        now=self.clock()-self.started
        n=len(self.frames)//4
        for index in range(self._stats_cursor,n):
            elapsed,interval=self.frames[index*4:index*4+2]
            while self._next_stats_at<elapsed:
                self._window_values(self._next_stats_at)
                self._next_stats_at+=.5
                if yielding:time.sleep(0)
            while self._low01_end<elapsed:self._finish_low01_group()
            self._low01_pending.append(interval)
            self._stats_window.append((elapsed,interval))
            while self._stats_window and self._stats_window[0][0]<=elapsed-10:
                self._stats_window.popleft()
        self._stats_cursor=n
        while self._next_stats_at<=now:
            self._window_values(self._next_stats_at)
            self._next_stats_at+=.5
            if yielding:time.sleep(0)
        while self._low01_end<=now:self._finish_low01_group()
        fps,low=self._window_values(now,track=False)
        n = self._full_count
        k = max(1,n//100)
        average_low = 1000*k/math.fsum(self.tail[-k:]) if n and k<=len(self.tail) else None
        self.live = dict(current_fps=fps, minimum_fps=self.minimum_fps,
                         sample_elapsed_s=now,
                         current_low=low, minimum_low=self.minimum_low, average_low=average_low,
                         average_low_0_1pct=(math.fsum(g['fps'] for g in self.low01_groups)/len(self.low01_groups)
                                            if self.low01_groups else None),
                         minimum_low_0_1pct=min((g['fps'] for g in self.low01_groups),default=None),
                         average_fps=1000*n/self.total_ms if n else None)
        return self.live

    def sensor(self, sample):
        self.sensors.append(dict(elapsed_s=self.clock()-self.started, **sample))

    def metrics(self, full=False):
        if full:
            n=len(self.frames)//4
            if not n: return None
            k=max(1,n//100)
            k01=max(1,(n+999)//1000)
            return dict(frame_count=n,duration_ms=self.total_ms,average_fps=1000*n/self.total_ms,
                        low_1pct_fps=1000*k/math.fsum(self.tail[-k:]),max_ms=self.tail[-1],
                        low_0_1pct_fps=1000*k01/math.fsum(self.tail[-k01:]),low_0_1pct_count=k01,
                        worst_frame_fps=1000/self.tail[-1])
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
        low=heapq.nlargest(max(1,n//100),range(n),key=lambda i:self.frames[i*4+1])
        low_set=set(low)
        low01=low[:max(1,(n+999)//1000)]
        low01_set=set(low01)
        phases={}
        for i in range(n):
            phase=int(self.frames[i*4+3])
            group=phases.setdefault(phase,dict(frames=0,slowest_1pct_frames=0,slowest_0_1pct_frames=0))
            group['frames']+=1;group['slowest_1pct_frames']+=int(i in low_set)
            group['slowest_0_1pct_frames']+=int(i in low01_set)
        worst=heapq.nlargest(min(200,n),range(n),key=lambda i:self.frames[i*4+1])
        selected=sorted({j for i in worst for j in range(max(0,i-2),min(n,i+3))})
        extremes={}
        for label,is_gap in (('gameplay',False),('test_gap',True)):
            index=max((i for i in range(n) if (self.frames[i*4+3]==7)==is_gap),
                      key=lambda i:self.frames[i*4+1],default=None)
            extremes[label]=(dict(frame_index=index,elapsed_s=self.frames[index*4],
                interval_ms=self.frames[index*4+1],equivalent_fps=1000/self.frames[index*4+1],
                phase=int(self.frames[index*4+3])) if index is not None else None)
        return dict(schema='danzhu.random_play.v2', mode='random_real_play',
                    timing_source='on_flip_application_pre_submit', present_source='unavailable',
                    reason=self.reason, metrics=self.metrics(full=True),
                    duration_s=self.duration_s, settings=dict(self.settings), live=dict(self.live),
                    columns=['elapsed_s', 'interval_ms', 'frame_wall_ms', 'phase'],
                    phases=['ready', 'charging', 'flying', 'misfire', 'landing', 'landed', 'cup','test_gap'],
                    frame_scope='worst_200_intervals_with_2_neighbors',
                    low_1pct_by_phase=phases,diagnostics=self.diagnostics.document(low,low01),
                    low_0_1pct_groups=list(self.low01_groups),
                    worst_frame_by_scope=extremes,
                    low_0_1pct_partial=(dict(start_s=self._low01_end-10,end_s=self.duration_s,
                        frame_count=len(self._low01_pending),complete=False) if self._low01_pending else None),
                    low_0_1pct_definition='10-second disjoint groups; k=max(1,ceil(n/1000)); average of group FPS; incomplete final group excluded',
                    frame_indices=selected,
                    frames=[list(self.frames[i*4:i*4+4]) for i in selected],
                    events=list(self.events), sensors=list(self.sensors),test_gaps=list(self.gaps),
                    display_events=list(self.display_events),
                    sensor_interval_s=self.settings.get('sensor_interval_s',.5), live_metrics_window_s=10)


def format_report(document):
    """TXT 前半为人读摘要，JSON 部分保留完整可复算记录。仅停止后调用。"""
    import json
    def number(value):return '未采到' if value is None else '%.3f'%value
    phases=('待机','蓄力','飞行','哑火','落袋','结算','装杯','测试间隔')
    m=document.get('metrics') or {}; settings=document.get('settings',{})
    diag=document.get('diagnostics',{})
    lines=['帧率测试诊断报告',
           '版本：%s；时长：%.2f 秒；结束原因：%s'%(document.get('version','未知'),document.get('duration_s',0),document.get('reason')),
           '投入：%s；返还率：%.0f%%；屏幕：%s Hz；Kivy 上限：%s'%(
               settings.get('bet'),settings.get('rtp',0)*100,settings.get('display_hz'),settings.get('kivy_cap')),
           '全程：%d 帧；平均 %s FPS；1%% Low %s FPS；最长 %s ms'%(
               m.get('frame_count',0),number(m.get('average_fps')),number(m.get('low_1pct_fps')),number(m.get('max_ms'))),
           '0.1%% Low：10秒分组平均 %s FPS；分组最低 %s FPS；全程 %s FPS'%(
               number(document.get('live',{}).get('average_low_0_1pct')),
               number(document.get('live',{}).get('minimum_low_0_1pct')),number(m.get('low_0_1pct_fps'))),
           '最差单帧：%s ms，等效 %s FPS'%(number(m.get('max_ms')),number(m.get('worst_frame_fps'))),
           '屏幕信息：默认%s；开关操作%d次（逐帧统计始终继续）'%(
               '显示' if settings.get('overlay_visible_default',True) else '隐藏',
               sum(e.get('event')=='request' for e in document.get('display_events',[]))),
           'GC设置：'+('关闭（自动循环GC及主动回收均禁用；引用计数照常）' if settings.get('gc_policy')=='disabled' else '手动（落杯关闭后0.3秒、间隔至少60秒、画面稳定后回收）' if settings.get('gc_policy')=='manual_close_after_interval' else '开启（画面稳定后按代回收）'),
           '', '一、最慢 1% 帧分布（与全程 1% Low 使用同一批帧）']
    memory=[s['memory_rss_bytes'] for s in document.get('sensors',[]) if s.get('memory_rss_bytes') is not None]
    memory_line=('占用内存（RSS）：首次 %.1fMB；末次 %.1fMB；采样最高 %.1fMB；变化 %+.1fMB'%
                 (memory[0]/1e6,memory[-1]/1e6,max(memory)/1e6,(memory[-1]-memory[0])/1e6)) if memory else '占用内存（RSS）：未采到'
    renderer = settings.get('cup_renderer_builds')
    if renderer is not None:
        lines.insert(-2,'落杯绘制：批绘建表%d次 / 矩形回退%d次；预热就绪=%s；回退原因=%s' % (
            renderer['mesh'],renderer['rectangles'],renderer['ready'],renderer.get('fallback_error') or '无'))
    lines.insert(-2,memory_line)
    lines.insert(-2,'内存口径：进程驻留内存，含共享页；非独占PSS或完整GPU用量。1MB=1000000字节，仅发射间隔采样，最高值非瞬时峰值。')
    for phase,row in sorted(document.get('low_1pct_by_phase',{}).items(),key=lambda item:int(item[0])):
        lines.append('%s：最慢1%% %d帧；最慢0.1%% %d帧；共%d帧'%(phases[int(phase)],row['slowest_1pct_frames'],row.get('slowest_0_1pct_frames',0),row['frames']))
    for key,label in (('gameplay','游戏阶段最差单帧'),('test_gap','测试间隔最差单帧')):
        row=document.get('worst_frame_by_scope',{}).get(key)
        if row:lines.append('%s：#%d @%.3fs，%.3fms，约%.2f FPS，%s'%(
            label,row['frame_index'],row['elapsed_s'],row['interval_ms'],row['equivalent_fps'],phases[row['phase']]))
    lines.extend(['','二、慢帧与其余帧的核心计时均值（ms）',
                  '主线程 CPU / 游戏帧 CPU / 前次 swap 等待 / 画布提交 / 面板自身'])
    for key,label in (('slowest_1pct','最慢1%'),('slowest_0_1pct','最慢0.1%'),('other_frames','其余帧')):
        row=diag.get('group_mean_ms',{}).get(key,{})
        lines.append(label+'：'+' / '.join(number(row.get(k)) for k in diag.get('core_columns',())))
    lines.extend(['','三、最慢十帧与最大子步骤（全部详细案例在后面的 JSON 中）'])
    for case in diag.get('cases',[])[:10]:
        row=next(r for r in case['frames'] if r['frame_index']==case['trigger_index'])
        steps=sorted(row.get('steps',{}).items(),key=lambda item:item[1][1],reverse=True)[:5]
        lines.append('#%d @%.3fs %s 间隔 %.3fms；主线程CPU %sms；游戏帧 %sms；swap %sms'%(
            row['frame_index'],row['elapsed_s'],phases[int(row['phase'])],row['interval_ms'],
            number(row.get('thread_cpu_ms')),number(row.get('frame_wall_ms')),number(row.get('swap_ms'))))
        lines.append('  '+', '.join('%s %d次/%.3fms'%(tag,values[0],values[1]) for tag,values in steps))
    lines.extend(['','四、计时口径和限制',
                  '记录应用 on_flip 提交间隔，未获得显示合成器实际呈现时间。',
                  '子步骤含嵌套，不能直接相加；面板刷新、面板文字、记录器已单列。',
                  'swap 记录前一次提交，属于当前帧间隔；interval_id 与 swap_source_interval 可核对。',
                  'CPU 较低只说明可能存在等待，不能直接判定为 GPU、系统调度或温控。',
                  '全程最慢案例最多128组，另保留游戏阶段最慢64组；每组前8帧后4帧。结束处 following_left 标明缺少后续帧。',
                  '原始帧片段仅含最慢200帧及邻帧；全程指标、阶段分布和核心计时均使用全部帧。',
                  '温度与电池功率读取策略：%s；缺失读数不编造，不提供平均耗电。'%settings.get('sensor_policy','periodic_0.5s'),
                  '测试间隔单独标记；全程指标仍包含间隔帧，不删除刷新产生的慢帧。',
                  '0.1% Low：固定10秒分组，取最慢 max(1,ceil(N/1000)) 帧；1200帧取2帧。不完整尾组单列，不计入分组平均/最低。',
                  '', '=== DATA JSON ===',json.dumps(document,ensure_ascii=False,allow_nan=False)])
    return '\n'.join(lines)
