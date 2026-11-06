"""轮次弹窗复用：首次/重复显示不重建文字、事件快照和确认幂等。"""
from unittest.mock import patch
from bench_smoke import _boot


def main():
    root,t,Clock=_boot()
    from kivy.core.text import Label as CoreLabel
    from kivy.core.window import Window
    from danzhu.ui.text import _POPUP_N, _glyph_finish_warm
    Clock.unschedule(root.game_area.win_fx.prebake_step)
    root._round_end_prepare_event.cancel()
    root._load_veil=None
    _glyph_finish_warm()
    def pump(n=50):
        for _ in range(n):t.t+=.01;Clock.tick()
    initial_gate=_POPUP_N[0]
    view=root._prepare_round_end();pump();view.resize();pump()
    assert not view.popup._is_open and _POPUP_N[0]==initial_gate
    widgets={id(w) for w in view.popup.walk()}
    counter=[0]
    original=CoreLabel.refresh
    def refresh(self,*args,**kw):counter[0]+=1;return original(self,*args,**kw)
    calls=[]
    confirm=root.game.round_end_confirm
    def once():calls.append(1);return confirm()
    root.game.round_end_confirm=once
    for plays,balance in ((50,1234),(100,987654321),(20,123456789012345678)):
        root.game._round_end_shown=True;root.game._round_end_ack=False
        root.game.balance=777
        with patch.object(CoreLabel,'refresh',refresh):
            root._show_round_end(dict(plays=plays,balance=balance))
            pump()
        assert root._round_end_popup is view.popup
        assert view.plays.number.text==str(plays) and view.balance.number.text==str(balance)
        assert root.game.balance==777,'display must not change ledger'
        assert {id(w) for w in view.popup.walk()}==widgets
        assert counter[0]==0,('round-end CoreLabel refresh',counter[0])
        assert view.balance.number._quads is not None
        before=len(calls);view.button.dispatch('on_release');pump()
        assert len(calls)==before+1,'button binding accumulated'
        assert root.game.balance==1000 and not view.popup._is_open
    Window.size=(360,640);pump();view.resize();pump()
    with patch.object(CoreLabel,'refresh',refresh):
        root._show_round_end(dict(plays=50,balance=123456));pump()
    assert counter[0]==0,('resize warmup incomplete',counter[0])
    view.popup.dismiss(animation=False);view.dispose()
    print('PASS: reused popup, first/repeated show 0 CoreLabel refresh, 18-digit balance fits, one callback, resize prewarmed')


if __name__=='__main__':main()
