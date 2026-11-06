"""随机实玩真实 UI 驱动、档位/存档隔离、停止恢复及百分钟边界。"""
import json
from pathlib import Path
import random
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from bench_smoke import _boot


def main():
    root, clock, Clock = _boot()
    from kivy.app import App
    from kivy.core.window import Window
    import danzhu.ui.playtest as module
    clock.time_ns=lambda:int(clock.t*1e9)
    module.time=clock
    awake=[]
    module._set_keep_awake=awake.append
    root._load_veil=None
    old=root.game
    root._act(old.set_bet,100);root._act(old.set_rtp,3.6)
    initial=(old.balance,old.plays,old.hits,old.bet,old.rtp_target,list(old.multipliers))
    rng=random.getstate()
    fx=root.game_area.win_fx
    fx._variant_bags={50:[3,8,1]};fx._last_variant={50:2}
    original_bags={n:list(bag) for n,bag in fx._variant_bags.items()}
    original_last=dict(fx._last_variant)
    writes=[]
    def write(cfg,path):
        writes.append(path)
        Path(path).write_text(json.dumps(cfg),encoding='utf-8')
        return True
    old._save_cfg=write
    read_sensor=module._live_power_snapshot
    sensor_calls=[]
    main_thread=threading.get_ident()
    def checked_sensor():
        assert threading.get_ident()!=main_thread,'sensor read must not block UI thread'
        assert root.game.state=='ready' and root.game_area.win_fx.mode=='idle'
        assert root._playtest_gap is not None
        sensor_calls.append(clock.t)
        return read_sensor()
    module._live_power_snapshot=checked_sensor
    read_memory=module.process_memory_snapshot
    def checked_memory():
        assert threading.get_ident()!=main_thread,'memory read must not block UI thread'
        assert root._playtest_gap is not None
        return read_memory()
    module.process_memory_snapshot=checked_memory
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(App,'get_running_app',return_value=SimpleNamespace(user_data_dir=directory)):
            root._start_playtest()
            import gc
            assert not gc.isenabled()
            assert root._playtest.settings["gc_policy"]=="disabled"
            assert awake==[True]
            assert root._playtest_display is None
            assert not root._playtest_show_info and not root._playtest_info_built
            assert root._playtest_label is None
            record=root._playtest
            assert root.game is not old and root.game_area.model is root.game
            assert not root.game._bench_running and not getattr(root.game,'_low_no_save',False)
            root.set_bet(1);root.set_rtp(.8)
            assert root.game.bet==100 and root.game.rtp_target==3.6
            flight_text=None
            for _ in range(4800):
                clock.t+=1/120
                Clock.tick()
                root._frame_timed(1/120)
                root._playtest_flip(Window)
                texts=(getattr(root._playtest_label,'text',None),getattr(root._playtest_sensor,'text',None),
                       tuple(c.text for row in root._playtest_stat_cells for c in row))
                if root.game.state=='flying':
                    if flight_text is not None:assert texts==flight_text
                    flight_text=texts
                else:flight_text=None
            assert len(record.events)>=2,len(record.events)
            assert len({round(e['power'],2) for e in record.events})>1
            assert all(e['bet']==100 for e in record.events)
            assert all(e['power']>=.15 for e in record.events)
            assert record.settings['random_power_range']==[.15,1.0]
            assert record.settings['current_fps_window_s']==10
            assert record.sensors and len(record.sensors)<=len(record.gaps)
            assert len(sensor_calls)==len(record.sensors)
            assert all(s.get('memory_rss_bytes',0)>0 for s in record.sensors)
            assert all(s.get('worker_memory_ms',-1)>=0 for s in record.sensors)
            for sample in record.sensors:
                assert any(g['begin_s']<=sample['elapsed_s']<=g.get('end_s',float('inf')) for g in record.gaps)
            for gap in record.gaps:
                if gap['stage']=='complete':
                    assert gap['refresh_begin_s']-gap['begin_s']>=.3-1e-6
                    assert gap['end_s']-gap['refresh_end_s']>=.3-1e-6
            assert 7 in record.frames[3::4]
            assert not root._playtest_info_built,'hidden mode must not construct metric widgets'
            root._toggle_playtest_info()
            assert not root._playtest_info_built,'show request waits for a shot gap'
            for _ in range(2400):
                clock.t+=1/120;Clock.tick();root._frame_timed(1/120);root._playtest_flip(Window)
                if root._playtest_info_built:break
            assert root._playtest_info_built and root._playtest_info_box.parent is root._playtest_panel
            assert '占用内存：' in root._playtest_label.text and 'MB' in root._playtest_label.text
            root._toggle_playtest_info()
            assert root._playtest_info_box.parent is None
            frozen=(root._playtest_label.text,root._playtest_sensor.text)
            for _ in range(240):
                clock.t+=1/120;Clock.tick();root._frame_timed(1/120);root._playtest_flip(Window)
            assert frozen==(root._playtest_label.text,root._playtest_sensor.text)
            assert writes and all('random_play_sandbox' in p for p in writes)
            root._stop_playtest()
            renderers=record.settings['cup_renderer_builds']
            assert renderers['mesh']>0 and renderers['ready'],renderers
            assert renderers['fallback_error'] is None,renderers
            assert awake==[True,False]
            assert root.game is old and root.game_area.model is old
            assert old._bench_running is False
            assert root.stats_lbl._rec is not None
            assert initial==(old.balance,old.plays,old.hits,old.bet,old.rtp_target,list(old.multipliers))
            assert random.getstate()==rng
            assert fx._variant_bags==original_bags and fx._last_variant==original_last
            rows=root._load_playtest_history()
            assert len(rows)==1 and rows[0]['settings']['bet']==100 and rows[0]['settings']['rtp']==3.6
            assert rows[0]['duration_s']>=39.9
            saved=threading.Event()
            def export(text,prefix):
                Path(directory,'report.json').write_text(text,encoding='utf-8')
                saved.set()
                return True,'saved'
            root._bench_save_log=export
            root._save_playtest()
            assert saved.wait(5),'report export did not finish'
            saved_text=Path(directory,'report.json').read_text(encoding='utf-8')
            assert '最慢 1% 帧分布' in saved_text
            report=json.loads(saved_text.split('=== DATA JSON ===\n',1)[1])
            assert report['settings']['bet']==100 and report['settings']['rtp']==3.6
            assert report['metrics']['frame_count']==len(record.frames)//4
            assert report['summary']['duration_s']==record.duration_s
            assert report['summary']['memory_rss_first_bytes']==record.sensors[0]['memory_rss_bytes']
            assert report['summary']['memory_rss_max_bytes']==max(s['memory_rss_bytes'] for s in record.sensors)
            assert '占用内存（RSS）' in saved_text
            assert report['diagnostics']['core_frame_count']==report['metrics']['frame_count']
            assert report['diagnostics']['cases']
            assert report['settings']['overlay_visible_default'] is False
            assert report['settings']['popup_refresh_policy']=='shared_100ms_leading_350ms_trailing'
            assert report['settings']['display_request']=='cached_classes_argument_queue_and_1s_mode_catalog'
            assert [e['visible'] for e in report['display_events'] if e['event']=='request']==[True,False]
            assert [e['visible'] for e in report['display_events'] if e['event']=='display']==[True,False]
            assert report['low_0_1pct_groups']
            assert report['metrics']['low_0_1pct_count']==max(1,(report['metrics']['frame_count']+999)//1000)
            assert len(root._load_playtest_history())==1,'save must not duplicate history'
            root._playtest_summary_popup.dismiss(animation=False)
            root._close_playtest()
            root._start_playtest()
            record=root._playtest
            clock.t=record.started+6000
            root._playtest_tick(.05)
            assert not record.active and record.reason=='100_minutes'
            assert awake==[True,False,True,False]
            assert len(root._load_playtest_history())==2
            root._playtest_summary_popup.dismiss(animation=False)
            root._close_playtest()
    print('PASS: random launches, fixed settings, ordinary save/reroll path, player restoration, history, 100-minute stop')


if __name__=='__main__':main()
