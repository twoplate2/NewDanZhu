"""按正常游戏输入驱动帧率测试，不设置 benchmark 标志，不固定盘面或锁核。"""
import os
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ..bench.playtest import PlaytestRecord, format_report
from ..bench.playtest_probe import PlayProbe
from ..bench.low_runtime import ACTIVE
from ..game import Game
from ..config import MISFIRE_POWER, COL_BALL
from ..platform.benchcpu import _live_power_snapshot
from ..platform.memory import process_memory_snapshot
from ..platform.device import _set_keep_awake, _screen_hz
from .text import _set_label_text, _hist_stamp, text_px
from .widgets import _app_version
from .number_cell import NumberCell


def _compute_playtest_snapshot(work):
    start=time.perf_counter()
    work.compute_live(yielding=True)
    computed=time.perf_counter()
    sample=_live_power_snapshot()
    sampled=time.perf_counter()
    sample.update(process_memory_snapshot())
    sample["worker_memory_ms"]=(time.perf_counter()-sampled)*1000
    sample.update(worker_stats_ms=(computed-start)*1000,worker_sensor_ms=(sampled-computed)*1000)
    return work,sample


def _duration(seconds):
    seconds=max(0,int(seconds))
    return '%02d:%02d' % (seconds//60,seconds%60)


def _number(value, digits=1):
    return '未采到' if value is None else ('%.*f'%(digits,value))


class PlaytestMixin:
    def _start_playtest(self):
        if getattr(self, '_playtest', None) is not None:
            return
        if self.game._bench_running or getattr(self, '_hp_running', False) or ACTIVE[0] is not None:
            return
        fx = self.game_area.win_fx
        if self.game.state != 'ready' or fx.mode != 'idle':
            self._status_message('请在本次落珠结束后开始帧率测试')
            return
        directory = os.path.join(App.get_running_app().user_data_dir, 'random_play_sandbox')
        os.makedirs(directory, exist_ok=True)
        self._playtest_original = self.game
        self._playtest_status_before = self.status_lbl.text
        self._playtest_rng_state = random.getstate()
        self._playtest_fx_state = (fx._seq, fx._rng.getstate())
        self._playtest_big_text_start=dict(self.game_area._big_text_stats)
        old = self.game
        simulation = Game(fx=fx, voice_duration=old._voice_duration, save_config=old._save_cfg,
                          sound_gate=old._sound_gate, on_easter_popup=old._on_easter_popup,
                          on_rtp_popup=old._on_rtp_popup, wall_clock=old._wall)
        simulation.take_events()
        simulation._config_path = lambda: os.path.join(directory, 'config.json')
        simulation._history_path = lambda: os.path.join(directory, 'history.json')
        for field in ('bet','rtp_target','max_plays','sound_mode','sfx_enabled','fps_cap_setting','power_grain','gc_enabled','gc_manual_only'):
            setattr(simulation, field, getattr(old, field))
        simulation.rtp_btns = dict(old.rtp_btns)
        simulation.multipliers = simulation._boards[simulation.rtp_target]
        self.game = simulation
        self.game_area.model = simulation
        self._act(simulation._refresh_stats)
        self.game_area._update_slots()
        self._restyle_buttons()
        self._playtest = PlaytestRecord(clock=time.perf_counter)
        self._playtest_probe=PlayProbe()
        self._playtest.settings = dict(bet=simulation.bet, rtp=simulation.rtp_target,
                                      fps_cap=simulation.fps_cap_setting, sensor_interval_s=None,
                                      sensor_policy='between_shots',ui_refresh_policy='between_shots',
                                      memory_metric='process_rss',memory_unit='MB_decimal',
                                      memory_policy='between_shots_worker',
                                      overlay_visible_default=False,overlay_visible=False,overlay_requested=False,
                                      big_result_widget_pool='two_pairs_per_renderer',
                                      current_fps_window_s=10,minimum_fps_window_s=1,
                                      gap_before_refresh_s=.3,gap_after_refresh_s=.3,
                                      stats_execution='worker',number_rendering='glyph_atlas',
                                      round_end_popup='cached_prewarmed_static_and_atlas_numbers',
                                      text_measurement='single_line_extents_without_texture',
                                      display_request='cached_classes_and_argument_queue',
                                      gc_enabled=simulation.gc_enabled,gc_manual_only=simulation.gc_manual_only,
                                      gc_manual_interval_s=60,
                                      gc_policy=('disabled' if not simulation.gc_enabled else
                                                 'manual_close_after_interval' if simulation.gc_manual_only else
                                                 'generational_collect_when_visually_quiet'),
                                      low_0_1pct_window_s=10,low_0_1pct_rounding='ceil',
                                      low_0_1pct_aggregation='mean_and_minimum_of_complete_10s_groups',
                                      display_hz=_screen_hz(),kivy_cap=getattr(Clock,'_max_fps',None),
                                      random_power_range=[MISFIRE_POWER,1.0],
                                      diagnostic_probe='scoped_steps_gc_thread_cpu_draw_swap_v1')
        self._playtest_rng = random.Random()
        self._playtest_next = time.perf_counter()+.7
        self._playtest_target = None
        self._playtest_gap = None
        self._playtest_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='playtest-stats')
        self._playtest_job=None
        self._playtest_result = None
        self._playtest_make_panel()
        Window.bind(on_flip=self._playtest_flip)
        self._playtest_driver = Clock.schedule_interval(self._playtest_tick, .05)
        self._playtest_display = None
        self._playtest_probe.start()
        _set_keep_awake(True)

    def _playtest_make_panel(self):
        panel = BoxLayout(orientation='vertical', size_hint=(None,None), spacing=dp(2), padding=dp(4))
        with panel.canvas.before:
            Color(.055,.065,.085,.98)
            bg = Rectangle()
        panel.bind(pos=lambda w,v:setattr(bg,'pos',v), size=lambda w,v:setattr(bg,'size',v))
        self._playtest_show_info=False
        self._playtest_info_built=False
        self._playtest_info_box=None
        self._playtest_label=self._playtest_sensor=None
        self._playtest_stat_cells=[]
        self._playtest_low01_cells=[]
        row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        stop = Button(text='停止', font_size=sp(14))
        save = Button(text='停止并保存', font_size=sp(14))
        stop.bind(on_release=lambda *_: self._stop_playtest() if self._playtest.active else self._close_playtest())
        save.bind(on_release=lambda *_: self._save_playtest())
        toggle=Button(text='显示信息',font_size=sp(14))
        toggle.bind(on_release=self._toggle_playtest_info)
        self._playtest_toggle_btn=toggle
        row.add_widget(toggle);row.add_widget(stop); row.add_widget(save); panel.add_widget(row)
        self._playtest_panel, self._playtest_stop_btn, self._playtest_save_btn = panel, stop, save
        panel.bind(minimum_height=lambda w,v:setattr(w,'height',v),height=self._playtest_layout)
        Window.add_widget(panel)
        Window.bind(size=self._playtest_layout)
        self._playtest_layout()

    def _playtest_build_info(self):
        if self._playtest_info_built:return
        panel=BoxLayout(orientation='vertical',size_hint_y=None,spacing=dp(2))
        panel.bind(minimum_height=panel.setter('height'))
        self._playtest_label = Label(text='帧率测试 · 正在开始', halign='left', valign='middle',markup=True,
                                    size_hint_y=None,height=dp(82))
        self._playtest_sensor = Label(text='', markup=True, halign='left', valign='middle',
                                     size_hint_y=None,height=dp(24))
        for label in (self._playtest_label,self._playtest_sensor):
            label._playtest_probe_tag='面板文字'
            label.bind(texture_size=lambda w,v:setattr(w,'height',v[1] if w.text else 0))
        panel.add_widget(self._playtest_label)
        stats=BoxLayout(orientation='vertical',size_hint_y=None,spacing=0)
        stats.bind(minimum_height=stats.setter('height'))
        self._playtest_stat_cells=[]
        for caption in ('帧率：','1%low：'):
            stat_row=BoxLayout(size_hint_y=None,height=dp(22),spacing=0)
            cells=[]
            for index,text in enumerate((caption,'当前','最低','平均')):
                cell=(Label(text=text,halign='left',valign='middle',size_hint_x=None) if index==0 else
                      NumberCell(caption=text,text='采样中',font_size=sp(max(13,min(17,Window.width/dp(27)))),size_hint_x=None))
                cell._playtest_probe_tag='面板文字'
                cells.append(cell);stat_row.add_widget(cell)
            for cell in cells:
                cell.bind(texture_size=lambda _w,_v,row=stat_row,cells=cells:
                          setattr(row,'height',max(c.texture_size[1] for c in cells)))
            self._playtest_stat_cells.append(cells);stats.add_widget(stat_row)
        self._playtest_stats=stats
        panel.add_widget(stats)
        low_row=BoxLayout(size_hint_y=None,height=dp(22))
        self._playtest_sensor.size_hint_x=None
        self._playtest_sensor_plain=''
        low_row.add_widget(self._playtest_sensor)
        self._playtest_low01_title=Label(text='0.1%Low：',size_hint_x=None,halign='left',valign='middle')
        low_row.add_widget(self._playtest_low01_title)
        self._playtest_low01_cells=[NumberCell(caption=caption,text='采样中',
            size_hint_x=None,font_size=sp(max(13,min(17,Window.width/dp(27))))) for caption in ('平均','/最低')]
        for cell in self._playtest_low01_cells:
            low_row.add_widget(cell)
            cell.bind(texture_size=lambda _w,_v,row=low_row:setattr(row,'height',max(c.texture_size[1] for c in row.children)))
        self._playtest_low01_row=low_row;panel.add_widget(low_row)
        self._playtest_info_box=panel
        self._playtest_info_built=True

    def _toggle_playtest_info(self, *_):
        if self._playtest is None or not self._playtest.active:return
        self._playtest_show_info=not self._playtest_show_info
        self._playtest.settings['overlay_requested']=self._playtest_show_info
        self._playtest.display_events.append(dict(elapsed_s=self._playtest.clock()-self._playtest.started,
                                                 event='request',visible=self._playtest_show_info))
        self._playtest_toggle_btn.text='隐藏信息' if self._playtest_show_info else '显示信息'
        if not self._playtest_show_info and self._playtest_info_box is not None:
            self._playtest_panel.remove_widget(self._playtest_info_box)
            self._playtest.settings['overlay_visible']=False
            self._playtest.display_events.append(dict(elapsed_s=self._playtest.clock()-self._playtest.started,
                                                     event='display',visible=False))
        # 显示请求在下个发射间隔兑现；飞行期间不新建控件或重建统计文字。

    def _playtest_layout(self, *_):
        panel = self._playtest_panel
        panel.width = min(Window.width, dp(620))
        panel.pos = ((Window.width-panel.width)/2, Window.height-panel.height)
        if not self._playtest_info_built or not self._playtest_show_info:return
        font=sp(max(13,min(17,Window.width/dp(27))))
        header_width=panel.width-dp(10)
        self._playtest_label.font_size=font*min(1.,header_width/max(1,text_px(self._playtest_label.text,font)))
        self._playtest_label.text_size=(header_width,None)
        prefix=max(text_px('1%low：',font),text_px('帧率：',font))+dp(3)
        widths=[prefix]+[(panel.width-dp(8)-prefix)/3]*3
        for cells in self._playtest_stat_cells:
            for cell,width in zip(cells,widths):
                cell.width=width;cell.font_size=font;cell.text_size=(width,None)
        plain=self._playtest_sensor_plain
        texts=(plain,'0.1%Low：','平均000.00','/最低000.00')
        total=sum(text_px(t,font) for t in texts if t)+(dp(6) if plain else 0)
        line_font=font*min(1.,(panel.width-dp(8))/max(1,total))
        widgets=(self._playtest_sensor,self._playtest_low01_title,*self._playtest_low01_cells)
        for index,(widget,text) in enumerate(zip(widgets,texts)):
            widget.font_size=line_font
            widget.width=(text_px(text,line_font) if text else 0)+(dp(6) if index==0 and plain else 0)
            widget.text_size=(widget.width,None)

    def _playtest_flip(self, *_):
        started=time.perf_counter()
        record = self._playtest
        if record is None or not record.active:
            return
        phase = 6 if self.game_area.win_fx.mode != 'idle' else {
            'ready':0,'charging':1,'flying':2,'misfire':3,'landing':4,'landed':5}.get(self.game.state,0)
        if self._playtest_gap is not None:phase=7
        diagnostic=self._playtest_probe.take()
        ball=self.game.ball
        diagnostic['context']=dict(shot_id=len(record.events),state=self.game.state,
            cup_mode=self.game_area.win_fx.mode,
            ball=(ball.x,ball.y,ball.vx,ball.vy) if ball is not None else None)
        if not record.flip(phase,diagnostic):
            self._stop_playtest('100_minutes')
        self._playtest_probe.step('记录器',time.perf_counter()-started)

    def _playtest_tick(self, _dt):
        if self._playtest is None or not self._playtest.active:
            return False
        now = time.perf_counter()
        if now-self._playtest.started>=6000:
            self._stop_playtest('100_minutes')
            return False
        if now < self._playtest_next:
            return True
        fx, game = self.game_area.win_fx, self.game
        if game._round_end_shown:
            self._act(game.round_end_confirm)
            self._playtest_next = now+1
        elif game._easter_hold and getattr(self, '_easter_popup_w', None) is not None:
            self._easter_popup_w.dismiss()
            self._playtest_next = now+1
        elif fx.mode == 'result' and not fx._closing_at:
            fx.request_close()
            self._playtest_next = now+self._playtest_rng.uniform(.5,1.5)
        elif game.state == 'ready' and fx.mode == 'idle':
            quiet=(not game._anim_pending and game.now>=game._anim_start_time+game._anim_dur and
                   not any(e.get('kind')=='big' for e in self.game_area._effects))
            if not quiet:return True
            gap=self._playtest_gap
            if gap is None:
                if game.balance < game.bet:self._act(game.reset_balance)
                gap=dict(begin_s=now-self._playtest.started,stage='before_refresh')
                self._playtest.gaps.append(gap);self._playtest_gap=gap
                self._playtest_next=now+.3
            elif gap['stage']=='before_refresh':
                gap['refresh_begin_s']=now-self._playtest.started
                self._playtest_job=self._playtest_worker.submit(_compute_playtest_snapshot,self._playtest.snapshot_live())
                gap['stage']='computing'
                self._playtest_next=now
            elif gap['stage']=='computing':
                if not self._playtest_job.done():return True
                try:work,sample=self._playtest_job.result()
                except Exception as exc:
                    self._stop_playtest('statistics_failed')
                    self._status_message('统计失败：'+str(exc),important=True)
                    return False
                values=self._playtest.apply_live(work)
                self._playtest_live(0,values=values,sample=sample)
                finished=time.perf_counter()
                gap['refresh_end_s']=finished-self._playtest.started
                gap['stage']='after_refresh'
                self._playtest_next=finished+.3
            else:
                gap.update(end_s=now-self._playtest.started,stage='complete')
                self._playtest_gap=None
                self._playtest_target = self._playtest_rng.uniform(MISFIRE_POWER,1.0)
                self._act(game.start_charge)
        elif game.state == 'charging' and self._playtest_target is not None and game.power >= self._playtest_target:
            power = game.power
            self._act(game.launch)
            self._playtest.events.append(dict(elapsed_s=now-self._playtest.started, event='launch',
                power=power, bet=game.bet, board=list(game.multipliers)))
            self._playtest_target = None
            self._playtest_next = now+self._playtest_rng.uniform(.5,1.5)
        return True

    def _playtest_live(self, _dt, values=None, sample=None):
        record = self._playtest
        if record is None or not record.active:
            return False
        values = record.update_live() if values is None else values
        sample = _live_power_snapshot() if sample is None else sample
        sample['fps'] = dict(values)
        record.sensor(sample)
        if not self._playtest_show_info:return True
        self._playtest_build_info()
        if self._playtest_info_box.parent is None:
            self._playtest_panel.add_widget(self._playtest_info_box,index=1)
            record.settings['overlay_visible']=True
            record.display_events.append(dict(elapsed_s=record.clock()-record.started,event='display',visible=True))
        self._playtest_layout()
        def fmt(value): return '采样中' if value is None else '%.2f'%value
        self._playtest_label.text = '更新 %s · 投%d · 返还%.0f%%' % (
            _duration(values.get('sample_elapsed_s',time.perf_counter()-record.started)),record.settings['bet'],record.settings['rtp']*100)
        rss=sample.get('memory_rss_bytes')
        self._playtest_label.text+=' · 占用内存：'+('未采到' if rss is None else '%.1fMB'%(rss/1000000))
        for cells,keys in zip(self._playtest_stat_cells,(('current_fps','minimum_fps','average_fps'),
                                                       ('current_low','minimum_low','average_low'))):
            for cell,key in zip(cells[1:],keys):
                cell.text=fmt(values[key])
        for cell,key in zip(self._playtest_low01_cells,('average_low_0_1pct','minimum_low_0_1pct')):
            cell.text=fmt(values[key])
        fields=[];plain=[]
        for value,unit in ((sample.get('temp'),'度'),(sample.get('watts'),'W')):
            if value is not None:
                prefix='温度：' if unit=='度' else '功耗：'
                number=('%.1f' if unit=='度' else '%.2f')%value
                plain.append('%s%s%s'%(prefix,number,unit))
                fields.append('%s[color=%s]%s[/color]%s'%(prefix,COL_BALL.lstrip('#'),number,unit))
        self._playtest_sensor_plain=' '.join(plain)
        self._playtest_sensor.text=' '.join(fields)
        self._playtest_layout()
        if hasattr(self,'_idle_gc'):self._idle_gc.quiet_since=None
        return True

    def _stop_playtest(self, reason='user', show_summary=True):
        record = getattr(self, '_playtest', None)
        if record is None or not record.active:
            return
        _set_keep_awake(False)
        self._playtest_probe.stop()
        self._playtest_worker.shutdown(wait=False,cancel_futures=True)
        record.stop(reason)
        if self._playtest_gap is not None:
            self._playtest_gap.update(end_s=record.duration_s,stage='interrupted')
            self._playtest_gap=None
        Window.unbind(on_flip=self._playtest_flip)
        self._playtest_driver.cancel()
        if self._playtest_display is not None:self._playtest_display.cancel()
        fx = self.game_area.win_fx
        fx._on_done = None
        fx._abort()
        self.game._timers.clear()
        for attr in ('_round_end_popup','_easter_popup_w'):
            popup = getattr(self, attr, None)
            if popup is not None:
                popup.dismiss()
                setattr(self, attr, None)
        self.game = self._playtest_original
        self.game_area.model = self.game
        random.setstate(self._playtest_rng_state)
        fx._seq = self._playtest_fx_state[0]
        fx._rng.setstate(self._playtest_fx_state[1])
        for effect in list(self.game_area._effects):
            for widget in effect['ws']:
                self.game_area.remove_widget(widget)
        self.game_area._effects.clear()
        self._act(self.game._refresh_stats)
        self._act(self.game._controls,self.game._controls_enabled)
        self._status_message(self._playtest_status_before)
        self._restyle_buttons()
        self.game_area._update_slots()
        self.game_area.tick_draw()
        metrics = record.metrics(full=True)
        self._playtest_stop_btn.text = '关闭'
        self._playtest_save_btn.text = '保存结果'
        self._playtest_toggle_btn.disabled=True
        if self._playtest_info_built:
            if self._playtest_stats.parent is not None:self._playtest_stats.parent.remove_widget(self._playtest_stats)
            for cell,key in zip(self._playtest_low01_cells,('average_low_0_1pct','minimum_low_0_1pct')):
                cell.text='采样中' if record.live.get(key) is None else '%.2f'%record.live[key]
            self._playtest_label.text = ('测试已停止 · 全程 %d 帧\n平均 %.2f  |  1%% Low %.2f  |  最长 %.1fms' %
                (metrics['frame_count'],metrics['average_fps'],metrics['low_1pct_fps'],metrics['max_ms'])) if metrics else '测试已停止 · 暂无帧数据'
        self._playtest_result = self._playtest_summary(record)
        self._append_playtest_history(self._playtest_result)
        if show_summary and reason not in ('paused','stopped'):
            self._show_playtest_detail(self._playtest_result)

    def _save_playtest(self):
        self._stop_playtest()
        record = self._playtest
        result = self._playtest_result
        self._playtest_save_btn.disabled = True
        def save():
            try:
                document = record.document()
                document['version'] = _app_version()
                document['summary'] = result
                _, message = self._bench_save_log(text=format_report(document),
                                                  prefix='plinko_random_play')
            except Exception as exc:
                message = '保存失败：'+str(exc)
            def done(_dt):
                if self._playtest is record:
                    if self._playtest_info_built:
                        self._playtest_sensor.text = ''
                        self._playtest_label.text = message
                    status=getattr(self,'_playtest_detail_status',None)
                    if status is not None: status.text=message
                    self._playtest_save_btn.disabled = False
            Clock.schedule_once(done,0)
        threading.Thread(target=save,daemon=True).start()

    def _close_playtest(self):
        self._stop_playtest('stopped',show_summary=False)
        if getattr(self, '_playtest', None) is not None:
            Window.unbind(size=self._playtest_layout)
            Window.remove_widget(self._playtest_panel)
            self._playtest = None

    def _playtest_summary(self, record):
        temps=[s['temp'] for s in record.sensors if s.get('temp') is not None]
        memory=[s['memory_rss_bytes'] for s in record.sensors if s.get('memory_rss_bytes') is not None]
        return dict(id=str(time.time_ns()),time=time.strftime('%Y-%m-%d %H:%M:%S'),version=_app_version(),duration_s=record.duration_s,
                    reason=record.reason, settings=dict(record.settings), shots=len(record.events),
                    metrics=record.metrics(full=True),live=dict(record.live),
                    temperature_start=temps[0] if temps else None,
                    memory_rss_first_bytes=memory[0] if memory else None,
                    memory_rss_last_bytes=memory[-1] if memory else None,
                    memory_rss_max_bytes=max(memory) if memory else None,
                    memory_rss_delta_bytes=memory[-1]-memory[0] if memory else None,
                    big_text_pool={key:value-self._playtest_big_text_start.get(key,0)
                                   for key,value in self.game_area._big_text_stats.items()},
                    temperature_end=temps[-1] if temps else None,
                    temperature_max=max(temps) if temps else None)

    def _playtest_history_path(self):
        return os.path.join(App.get_running_app().user_data_dir,'random_play_history.json')

    def _load_playtest_history(self):
        try:
            with open(self._playtest_history_path(),encoding='utf-8') as stream:
                data=json.load(stream)
            return [r for r in data if isinstance(r,dict)][-100:] if isinstance(data,list) else []
        except (OSError,ValueError):
            return []

    def _append_playtest_history(self, result):
        data=self._load_playtest_history()
        data.append(result)
        self._write_playtest_history(data[-100:])

    def _write_playtest_history(self, data):
        path=self._playtest_history_path()
        try:
            with open(path+'.tmp','w',encoding='utf-8') as stream:
                json.dump(data,stream,ensure_ascii=False)
            os.replace(path+'.tmp',path)
        except OSError as exc:
            self._status_message('帧率测试历史保存失败：'+str(exc),important=True)

    def _show_playtest_history(self):
        def cells(r):
            settings=r.get('settings',{}); m=r.get('metrics') or {}
            return (_hist_stamp(r.get('time')), '%s\n%d / %.0f%%' %
                    (_duration(r.get('duration_s',0)),settings.get('bet',0),settings.get('rtp',0)*100),
                    '%s / %s' % (_number(m.get('average_fps'),2),_number(m.get('low_1pct_fps'),2)))
        return self._show_history_table('帧率测试历史',('时间','时长/投入·返还','平均帧/1%Low'),
            list(reversed(self._load_playtest_history())),cells,self._show_playtest_detail,
            self._clear_playtest_history,'时长：分:秒；档位：每发投入 / 期望返还\n1% Low：全程最慢 1% 帧计算',
            '暂无帧率测试记录')

    def _clear_playtest_history(self):
        content=BoxLayout(orientation='vertical',spacing=dp(8),padding=dp(12))
        content.add_widget(Label(text='清空全部帧率测试历史？\n不可恢复。'))
        row=BoxLayout(size_hint_y=None,height=dp(46),spacing=dp(8))
        cancel=Button(text='取消'); confirm=Button(text='确定清空')
        row.add_widget(cancel);row.add_widget(confirm);content.add_widget(row)
        popup=self._popup(.9,220,title='清空帧率测试历史',content=content)
        cancel.bind(on_release=popup.dismiss)
        confirm.bind(on_release=lambda *_:(self._write_playtest_history([]),popup.dismiss(),self._show_playtest_history()))
        popup.open()

    def _show_playtest_detail(self, result):
        settings=result.get('settings',{}); m=result.get('metrics') or {}; live=result.get('live',{})
        reason={'user':'手动结束','100_minutes':'已达100分钟','paused':'切到后台','stopped':'已停止'}.get(result.get('reason'),'已结束')
        text=('时长：%s　发射：%d 次　%s\n投入：%d 个　期望返还：%.0f%%\n\n'
              '当前帧率（1秒）：%s\n最低帧率（1秒）：%s\n全程平均帧率：%s\n\n'
              '当前 1%% Low（10秒）：%s\n最低 1%% Low（10秒）：%s\n平均 1%% Low（全程）：%s\n\n'
              '平均 0.1%% Low（10秒分组）：%s\n最低 0.1%% Low（10秒分组）：%s\n\n'
              '温度：%s → %s 度；最高 %s 度\n\n'
              '记录：%d 帧；最长一帧：%s ms\n最差单帧约：%s FPS\n'
              '帧率为应用提交间隔统计；温度与电池功率同游戏信息面板。') % (
              _duration(result.get('duration_s',0)),result.get('shots',0),reason,
              settings.get('bet',0),settings.get('rtp',0)*100,
              _number(live.get('current_fps'),2),_number(live.get('minimum_fps'),2),_number(m.get('average_fps'),2),
              _number(live.get('current_low'),2),_number(live.get('minimum_low'),2),_number(m.get('low_1pct_fps'),2),
              _number(live.get('average_low_0_1pct'),2),_number(live.get('minimum_low_0_1pct'),2),
              _number(result.get('temperature_start')),_number(result.get('temperature_end')),_number(result.get('temperature_max')),
              m.get('frame_count',0),_number(m.get('max_ms'),2),_number(m.get('worst_frame_fps'),2))
        if 'memory_rss_first_bytes' in result:
            vals=[result.get('memory_rss_'+key+'_bytes') for key in ('first','last','max','delta')]
            text+='\n\n占用内存：首次 %sMB → 末次 %sMB\n采样最高 %sMB；变化 %sMB\n进程驻留内存（RSS，含共享页），1MB=1000000字节。'%tuple(
                _number(v/1000000) if v is not None else '未采到' for v in vals)
        text=text.replace('当前帧率（1秒）','当前帧率（%d秒）'%settings.get('current_fps_window_s',1))
        content=BoxLayout(orientation='vertical',padding=dp(10),spacing=dp(8))
        scroll=ScrollView(do_scroll_x=False)
        label=Label(text=text,size_hint_y=None,halign='left',valign='top',font_size=sp(15 if Window.width<dp(430) else 17))
        label.bind(width=lambda w,v:setattr(w,'text_size',(v,None)),
                   texture_size=lambda w,v:setattr(w,'height',v[1]+dp(12)))
        scroll.add_widget(label);content.add_widget(scroll)
        status=Label(text='',size_hint_y=None,height=dp(36),font_size=sp(12),halign='left')
        status.bind(size=lambda w,v:setattr(w,'text_size',(v[0],None)))
        content.add_widget(status);self._playtest_detail_status=status
        row=BoxLayout(size_hint_y=None,height=dp(48),spacing=dp(8))
        save=Button(text='保存报告');close=Button(text='关闭')
        row.add_widget(save);row.add_widget(close);content.add_widget(row)
        popup=self._popup(.94,min(dp(720),Window.height*.9),title='帧率测试总结',content=content,
                          title_align='center',title_size=sp(21),separator_height=0)
        def save_report(*_):
            if result is getattr(self,'_playtest_result',None) and getattr(self,'_playtest',None) is not None:
                self._save_playtest()
            else:
                _,message=self._bench_save_log(text=json.dumps(result,ensure_ascii=False,indent=2),prefix='plinko_random_summary')
                status.text=message
        save.bind(on_release=save_report)
        close.bind(on_release=popup.dismiss)
        popup.bind(on_dismiss=lambda *_:self._close_playtest() if result is getattr(self,'_playtest_result',None) else None)
        popup.open()
        self._playtest_summary_popup=popup
        return popup
