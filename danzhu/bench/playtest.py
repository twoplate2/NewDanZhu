"""随机实玩数据：逐帧紧凑记录，实时显示最近十秒；停止后统计全程。"""
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
        self.events, self.sensors = [], []
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
        low=heapq.nlargest(max(1,n//100),range(n),key=lambda i:self.frames[i*4+1])
        low_set=set(low)
        phases={}
        for i in range(n):
            phase=int(self.frames[i*4+3])
            group=phases.setdefault(phase,dict(frames=0,slowest_1pct_frames=0))
            group['frames']+=1;group['slowest_1pct_frames']+=int(i in low_set)
        worst=heapq.nlargest(min(200,n),range(n),key=lambda i:self.frames[i*4+1])
        selected=sorted({j for i in worst for j in range(max(0,i-2),min(n,i+3))})
        return dict(schema='danzhu.random_play.v2', mode='random_real_play',
                    timing_source='on_flip_application_pre_submit', present_source='unavailable',
                    reason=self.reason, metrics=self.metrics(full=True),
                    duration_s=self.duration_s, settings=dict(self.settings), live=dict(self.live),
                    columns=['elapsed_s', 'interval_ms', 'frame_wall_ms', 'phase'],
                    phases=['ready', 'charging', 'flying', 'misfire', 'landing', 'landed', 'cup'],
                    frame_scope='worst_200_intervals_with_2_neighbors',
                    low_1pct_by_phase=phases,diagnostics=self.diagnostics.document(low),
                    frame_indices=selected,
                    frames=[list(self.frames[i*4:i*4+4]) for i in selected],
                    events=list(self.events), sensors=list(self.sensors),
                    sensor_interval_s=.5, live_metrics_window_s=10)


def format_report(document):
    """TXT 前半为人读摘要，JSON 部分保留完整可复算记录。仅停止后调用。"""
    import json
    def number(value):return '未采到' if value is None else '%.3f'%value
    phases=('待机','蓄力','飞行','哑火','落袋','结算','装杯')
    m=document.get('metrics') or {}; settings=document.get('settings',{})
    diag=document.get('diagnostics',{})
    lines=['随机实玩诊断报告',
           '版本：%s；时长：%.2f 秒；结束原因：%s'%(document.get('version','未知'),document.get('duration_s',0),document.get('reason')),
           '投入：%s；返还率：%.0f%%；屏幕：%s Hz；Kivy 上限：%s'%(
               settings.get('bet'),settings.get('rtp',0)*100,settings.get('display_hz'),settings.get('kivy_cap')),
           '全程：%d 帧；平均 %s FPS；1%% Low %s FPS；最长 %s ms'%(
               m.get('frame_count',0),number(m.get('average_fps')),number(m.get('low_1pct_fps')),number(m.get('max_ms'))),
           '', '一、最慢 1% 帧分布（与全程 1% Low 使用同一批帧）']
    for phase,row in sorted(document.get('low_1pct_by_phase',{}).items(),key=lambda item:int(item[0])):
        lines.append('%s：%d / %d 帧'%(phases[int(phase)],row['slowest_1pct_frames'],row['frames']))
    lines.extend(['','二、慢帧与其余帧的核心计时均值（ms）',
                  '主线程 CPU / 游戏帧 CPU / 前次 swap 等待 / 画布提交 / 面板自身'])
    for key,label in (('slowest_1pct','最慢1%'),('other_frames','其余帧')):
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
                  '最慢案例最多128组，每组前8帧、后4帧；结束附近可能缺少后续帧，following_left 明示缺口。',
                  '原始帧片段仅含最慢200帧及邻帧；全程指标、阶段分布和核心计时均使用全部帧。',
                  '温度与电池功率每0.5秒读取；缺失读数不编造，不提供平均耗电。',
                  '', '=== DATA JSON ===',json.dumps(document,ensure_ascii=False,allow_nan=False)])
    return '\n'.join(lines)
