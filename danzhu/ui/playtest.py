"""按正常游戏输入驱动随机实玩，不设置 benchmark 标志，不固定盘面或锁核。"""
import os
import json
import random
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ..bench.playtest import PlaytestRecord
from ..bench.low_runtime import ACTIVE
from ..game import Game
from ..platform.benchcpu import _live_power_temp_line, _live_power_snapshot
from .text import _set_label_text, _hist_stamp
from .widgets import _app_version


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
            _set_label_text(self.status_lbl, '请在本次落珠结束后开始随机实玩模拟')
            return
        directory = os.path.join(App.get_running_app().user_data_dir, 'random_play_sandbox')
        os.makedirs(directory, exist_ok=True)
        self._playtest_original = self.game
        self._playtest_status_before = self.status_lbl.text
        self._playtest_rng_state = random.getstate()
        self._playtest_fx_state = (fx._seq, fx._rng.getstate())
        old = self.game
        simulation = Game(fx=fx, voice_duration=old._voice_duration, save_config=old._save_cfg,
                          sound_gate=old._sound_gate, on_easter_popup=old._on_easter_popup,
                          on_rtp_popup=old._on_rtp_popup, wall_clock=old._wall)
        simulation.take_events()
        simulation._config_path = lambda: os.path.join(directory, 'config.json')
        simulation._history_path = lambda: os.path.join(directory, 'history.json')
        for field in ('bet','rtp_target','max_plays','sound_mode','sfx_enabled','fps_cap_setting','power_grain'):
            setattr(simulation, field, getattr(old, field))
        simulation.rtp_btns = dict(old.rtp_btns)
        simulation.multipliers = simulation._boards[simulation.rtp_target]
        self.game = simulation
        self.game_area.model = simulation
        self._act(simulation._refresh_stats)
        self.game_area._update_slots()
        self._restyle_buttons()
        self._playtest = PlaytestRecord(clock=time.perf_counter)
        self._playtest.settings = dict(bet=simulation.bet, rtp=simulation.rtp_target,
                                      fps_cap=simulation.fps_cap_setting, sensor_interval_s=.5)
        self._playtest_rng = random.Random()
        self._playtest_next = time.perf_counter()+.7
        self._playtest_target = None
        self._playtest_result = None
        self._playtest_make_panel()
        Window.bind(on_flip=self._playtest_flip)
        self._playtest_driver = Clock.schedule_interval(self._playtest_tick, .05)
        self._playtest_display = Clock.schedule_interval(self._playtest_live, .5)

    def _playtest_make_panel(self):
        panel = BoxLayout(orientation='vertical', size_hint=(None,None), spacing=dp(3), padding=dp(5))
        with panel.canvas.before:
            Color(.055,.065,.085,.98)
            bg = Rectangle()
        panel.bind(pos=lambda w,v:setattr(bg,'pos',v), size=lambda w,v:setattr(bg,'size',v))
        self._playtest_label = Label(text='随机实玩模拟 · 正在开始', halign='left', valign='middle',
                                    size_hint_y=None,height=dp(82))
        self._playtest_sensor = Label(text='', markup=True, halign='left', valign='middle',
                                     size_hint_y=None,height=dp(50))
        panel.add_widget(self._playtest_label); panel.add_widget(self._playtest_sensor)
        row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        stop = Button(text='停止', font_size=sp(14))
        save = Button(text='停止并保存', font_size=sp(14))
        stop.bind(on_release=lambda *_: self._stop_playtest() if self._playtest.active else self._close_playtest())
        save.bind(on_release=lambda *_: self._save_playtest())
        row.add_widget(stop); row.add_widget(save); panel.add_widget(row)
        self._playtest_panel, self._playtest_stop_btn, self._playtest_save_btn = panel, stop, save
        Window.add_widget(panel)
        Window.bind(size=self._playtest_layout)
        self._playtest_layout()

    def _playtest_layout(self, *_):
        panel = self._playtest_panel
        panel.size = (min(Window.width, dp(620)), dp(184))
        panel.pos = ((Window.width-panel.width)/2, Window.height-panel.height)
        for label in (self._playtest_label, self._playtest_sensor):
            label.font_size = sp(max(13,min(17,Window.width/dp(27))))
            label.text_size = (panel.width-dp(10), None)

    def _playtest_flip(self, *_):
        record = self._playtest
        if record is None or not record.active:
            return
        phase = 6 if self.game_area.win_fx.mode != 'idle' else {
            'ready':0,'charging':1,'flying':2,'misfire':3,'landing':4,'landed':5}.get(self.game.state,0)
        if not record.flip(phase):
            self._stop_playtest('100_minutes')

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
            if game.balance < game.bet:
                self._act(game.reset_balance)
            self._playtest_target = self._playtest_rng.uniform(.05,1.0)
            self._act(game.start_charge)
        elif game.state == 'charging' and self._playtest_target is not None and game.power >= self._playtest_target:
            power = game.power
            self._act(game.launch)
            self._playtest.events.append(dict(elapsed_s=now-self._playtest.started, event='launch',
                power=power, bet=game.bet, board=list(game.multipliers)))
            self._playtest_target = None
            self._playtest_next = now+self._playtest_rng.uniform(.5,1.5)
        return True

    def _playtest_live(self, _dt):
        record = self._playtest
        if record is None or not record.active:
            return False
        values = record.update_live()
        def fmt(value): return '采样中' if value is None else '%.2f'%value
        self._playtest_label.text = ('实玩 %s · 投%d · 返还%.0f%%\n帧率 当前%s / 最低%s / 平均%s\n1%% Low 当前%s / 最低%s / 平均%s' %
            (_duration(time.perf_counter()-record.started),record.settings['bet'],record.settings['rtp']*100,
             fmt(values['current_fps']),fmt(values['minimum_fps']),fmt(values['average_fps']),fmt(values['current_low']),
             fmt(values['minimum_low']),fmt(values['average_low'])))
        # 与游戏信息面板完全相同的读取器、0.5 秒频率和充电/耗电口径。
        sample = _live_power_snapshot()
        sample['fps'] = dict(values)
        record.sensor(sample)
        sensor = _live_power_temp_line(sample)
        mean = record.power_summary()['average_power_w']
        self._playtest_sensor.text = sensor+ ('\n平均耗电：%.2f W'%mean if mean is not None else '')
        return True

    def _stop_playtest(self, reason='user', show_summary=True):
        record = getattr(self, '_playtest', None)
        if record is None or not record.active:
            return
        record.stop(reason)
        Window.unbind(on_flip=self._playtest_flip)
        self._playtest_driver.cancel(); self._playtest_display.cancel()
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
        _set_label_text(self.status_lbl,self._playtest_status_before)
        self._restyle_buttons()
        self.game_area._update_slots()
        self.game_area.tick_draw()
        metrics = record.metrics(full=True)
        self._playtest_stop_btn.text = '关闭'
        self._playtest_save_btn.text = '保存结果'
        self._playtest_label.text = ('模拟已停止 · 全程 %d 帧\n平均 %.2f  |  1%% Low %.2f  |  最长 %.1fms' %
            (metrics['frame_count'],metrics['average_fps'],metrics['low_1pct_fps'],metrics['max_ms'])) if metrics else '模拟已停止 · 暂无帧数据'
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
                _, message = self._bench_save_log(text=json.dumps(document,ensure_ascii=False),
                                                  prefix='plinko_random_play')
            except Exception as exc:
                message = '保存失败：'+str(exc)
            def done(_dt):
                if self._playtest is record:
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
        return dict(id=str(time.time_ns()),time=time.strftime('%Y-%m-%d %H:%M:%S'),version=_app_version(),duration_s=record.duration_s,
                    reason=record.reason, settings=dict(record.settings), shots=len(record.events),
                    metrics=record.metrics(full=True),live=dict(record.live),power=record.power_summary(),
                    temperature_start=temps[0] if temps else None,
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
            _set_label_text(self.status_lbl,'模拟历史保存失败：'+str(exc))

    def _show_playtest_history(self):
        def cells(r):
            settings=r.get('settings',{}); m=r.get('metrics') or {}
            return (_hist_stamp(r.get('time')), '%s\n%d / %.0f%%' %
                    (_duration(r.get('duration_s',0)),settings.get('bet',0),settings.get('rtp',0)*100),
                    '%s / %s\n%s W' % (_number(m.get('average_fps'),2),_number(m.get('low_1pct_fps'),2),
                                      _number(r.get('power',{}).get('average_power_w'),2)))
        return self._show_history_table('随机实玩模拟历史',('时间','时长/投入·返还','平均帧·Low/平均耗电'),
            list(reversed(self._load_playtest_history())),cells,self._show_playtest_detail,
            self._clear_playtest_history,'时长：分:秒；档位：每发投入 / 期望返还\n1% Low：全程最慢 1% 帧计算',
            '暂无随机实玩模拟记录')

    def _clear_playtest_history(self):
        content=BoxLayout(orientation='vertical',spacing=dp(8),padding=dp(12))
        content.add_widget(Label(text='清空全部随机实玩模拟历史？\n不可恢复。'))
        row=BoxLayout(size_hint_y=None,height=dp(46),spacing=dp(8))
        cancel=Button(text='取消'); confirm=Button(text='确定清空')
        row.add_widget(cancel);row.add_widget(confirm);content.add_widget(row)
        popup=self._popup(.9,220,title='清空模拟历史',content=content)
        cancel.bind(on_release=popup.dismiss)
        confirm.bind(on_release=lambda *_:(self._write_playtest_history([]),popup.dismiss(),self._show_playtest_history()))
        popup.open()

    def _show_playtest_detail(self, result):
        settings=result.get('settings',{}); m=result.get('metrics') or {}; live=result.get('live',{})
        power=result.get('power',{})
        reason={'user':'手动结束','100_minutes':'已达100分钟','paused':'切到后台','stopped':'已停止'}.get(result.get('reason'),'已结束')
        text=('时长：%s　发射：%d 次　%s\n投入：%d 个　期望返还：%.0f%%\n\n'
              '当前帧率（1秒）：%s\n最低帧率（1秒）：%s\n全程平均帧率：%s\n\n'
              '当前 1%% Low（10秒）：%s\n最低 1%% Low（10秒）：%s\n平均 1%% Low（全程）：%s\n\n'
              '平均耗电：%s W\n平均充电：%s W\n温度：%s → %s 度；最高 %s 度\n\n'
              '记录：%d 帧；最长一帧：%s ms\n'
              '帧率为应用提交间隔统计；温度与电池功率同游戏信息面板。') % (
              _duration(result.get('duration_s',0)),result.get('shots',0),reason,
              settings.get('bet',0),settings.get('rtp',0)*100,
              _number(live.get('current_fps'),2),_number(live.get('minimum_fps'),2),_number(m.get('average_fps'),2),
              _number(live.get('current_low'),2),_number(live.get('minimum_low'),2),_number(m.get('low_1pct_fps'),2),
              _number(power.get('average_power_w'),2),_number(power.get('average_charge_w'),2),
              _number(result.get('temperature_start')),_number(result.get('temperature_end')),_number(result.get('temperature_max')),
              m.get('frame_count',0),_number(m.get('max_ms')))
        content=BoxLayout(orientation='vertical',padding=dp(10),spacing=dp(8))
        scroll=ScrollView(do_scroll_x=False)
        if power.get('average_charge_w') is None:
            text=text.replace('平均充电：未采到 W\n','')
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
        popup=self._popup(.94,min(dp(720),Window.height*.9),title='随机实玩模拟总结',content=content,
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
