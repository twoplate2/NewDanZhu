"""设备GC开关默认值、存档、UI确认/取消和策略同步。"""
import json
import tempfile
from pathlib import Path
from bench_smoke import _boot


def main():
    root,clock,Clock=_boot()
    from danzhu.game import Game
    from kivy.core.window import Window
    from kivy.uix.switch import Switch
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'config.json'
        root.game._config_path=lambda:str(path)
        root.game._save_cfg=None
        assert not root.game.gc_enabled and not root._idle_gc.collection_enabled
        def dialog():
            root._show_fps_cap_settings()
            popup=Window.children[0]
            switches=[w for w in popup.walk() if isinstance(w,Switch)]
            switch=switches[0]
            buttons={w.text:w for w in popup.walk() if getattr(w,'text',None) in ('确定','取消')}
            return popup,switch,buttons
        popup,switch,buttons=dialog()
        assert not switch.active
        switch.active=True;buttons['取消'].dispatch('on_release')
        assert not root.game.gc_enabled
        popup.dismiss(animation=False)
        popup,switch,buttons=dialog()
        switch.active=True;buttons['确定'].dispatch('on_release')
        assert root.game.gc_enabled and root._idle_gc.collection_enabled
        assert Game(config_path=lambda:str(path)).gc_enabled
        popup.dismiss(animation=False)
        popup,switch,buttons=dialog()
        assert switch.active
        switch.active=False;buttons['确定'].dispatch('on_release')
        assert not root.game.gc_enabled and not root._idle_gc.collection_enabled
        assert not Game(config_path=lambda:str(path)).gc_enabled
        popup.dismiss(animation=False)
        popup,switch,buttons=dialog()
        manual=[w for w in popup.walk() if isinstance(w,Switch)][1]
        assert manual.disabled
        switch.active=True;assert not manual.disabled
        manual.active=True;buttons['确定'].dispatch('on_release')
        popup.dismiss(animation=False)
        assert root._idle_gc.manual_only and root._idle_gc.collection_enabled
        loaded=Game(config_path=lambda:str(path))
        assert loaded.gc_enabled and loaded.gc_manual_only
        root._act(root.game.set_gc_enabled,False,True)
        assert not root._idle_gc.collection_enabled and root._idle_gc.manual_only
        loaded=Game(config_path=lambda:str(path))
        assert not loaded.gc_enabled and loaded.gc_manual_only
        # 同一个退场完成点覆盖玩家点击、测试request_close和benchmark自动退场。
        policy=root._idle_gc;policy.clock=lambda:clock.t
        fx=root.game_area.win_fx
        from danzhu.ui.winfx import HOLD_BASE,RESULT_FADE
        for auto_close in (False,True):
            policy.set_enabled(True,True);clock.t+=61
            fx.play_win(2,100)
            fx.mode='result';fx._balls=[];fx._settled_at=clock.t-HOLD_BASE-1
            fx._closing_at=0;fx._auto_close=auto_close
            if not auto_close:assert fx.request_close()
            fx.tick();assert not policy.manual_pending
            clock.t+=RESULT_FADE+.01;fx.tick()
            assert fx.mode=='idle' and policy.manual_pending
            fx.tick();assert policy.manual_pending
        policy.set_enabled(False,True)
        fx.play_win(2,100);fx.mode='result';fx._balls=[]
        fx._settled_at=clock.t-HOLD_BASE-1;fx._closing_at=0;fx._auto_close=True
        fx.tick();clock.t+=RESULT_FADE+.01;fx.tick()
        assert not policy.manual_pending
        for value in ('false',1,None):
            path.write_text(json.dumps({'gc_enabled':value}),encoding='utf-8')
            assert not Game(config_path=lambda:str(path)).gc_enabled
        path.write_text('{}',encoding='utf-8')
        assert not Game(config_path=lambda:str(path)).gc_enabled
    print('PASS: GC setting default, cancel, confirm, policy, persistence and invalid configs')

if __name__=='__main__':main()
