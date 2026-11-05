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
    root._load_veil=None
    old=root.game
    root._act(old.set_bet,100);root._act(old.set_rtp,3.6)
    initial=(old.balance,old.plays,old.hits,old.bet,old.rtp_target,list(old.multipliers))
    rng=random.getstate()
    writes=[]
    def write(cfg,path):
        writes.append(path)
        Path(path).write_text(json.dumps(cfg),encoding='utf-8')
        return True
    old._save_cfg=write
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(App,'get_running_app',return_value=SimpleNamespace(user_data_dir=directory)):
            root._start_playtest()
            record=root._playtest
            assert root.game is not old and root.game_area.model is root.game
            assert not root.game._bench_running and not getattr(root.game,'_low_no_save',False)
            root.set_bet(1);root.set_rtp(.8)
            assert root.game.bet==100 and root.game.rtp_target==3.6
            for _ in range(4800):
                clock.t+=1/120
                Clock.tick()
                root._frame_timed(1/120)
                root._playtest_flip(Window)
            assert len(record.events)>=2,len(record.events)
            assert len({round(e['power'],2) for e in record.events})>1
            assert all(e['bet']==100 for e in record.events)
            assert writes and all('random_play_sandbox' in p for p in writes)
            root._stop_playtest()
            assert root.game is old and root.game_area.model is old
            assert initial==(old.balance,old.plays,old.hits,old.bet,old.rtp_target,list(old.multipliers))
            assert random.getstate()==rng
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
            report=json.loads(Path(directory,'report.json').read_text(encoding='utf-8'))
            assert report['settings']['bet']==100 and report['settings']['rtp']==3.6
            assert report['metrics']['frame_count']==len(record.frames)//4
            assert report['summary']['duration_s']==record.duration_s
            assert len(root._load_playtest_history())==1,'save must not duplicate history'
            root._playtest_summary_popup.dismiss(animation=False)
            root._close_playtest()
            root._start_playtest()
            record=root._playtest
            clock.t=record.started+6000
            root._playtest_tick(.05)
            assert not record.active and record.reason=='100_minutes'
            assert len(root._load_playtest_history())==2
            root._playtest_summary_popup.dismiss(animation=False)
            root._close_playtest()
    print('PASS: random launches, fixed settings, ordinary save/reroll path, player restoration, history, 100-minute stop')


if __name__=='__main__':main()
