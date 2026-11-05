"""右上角常规消息禁用；回收守卫覆盖真实UI动画状态。"""
from bench_smoke import _boot


def main():
    root,_,_= _boot()
    from danzhu.ui.text import _build_texwarm, _POPUP_N
    assert root.status_lbl.text==''
    root._status_message('中奖! +200 (x2)');assert root.status_lbl.text==''
    root._status_message('保存失败',important=True);assert root.status_lbl.text=='保存失败'
    root._status_message('发射!');assert root.status_lbl.text=='保存失败'
    root._status_message('');assert root.status_lbl.text==''
    assert all(item[0] is not root.status_lbl for item in _build_texwarm(root,100))
    root._load_veil=None;_POPUP_N[0]=0
    game,area=root.game,root.game_area
    game.now=100;game._anim_start_time=0;game._anim_pending=False
    game._balance_release_at=game._stats_release_at=None
    area._effects=[];area._pulse=None;area._spring_power=area._spring_vel=0
    area.win_fx.mode='idle';game.state='ready'
    assert root._idle_gc_quiet()
    for state in ('charging','flying','landing','misfire'):
        game.state=state;assert not root._idle_gc_quiet()
    game.state='landed';area.win_fx.mode='result';area.win_fx._dirty=False;area.win_fx._closing_at=0
    assert root._idle_gc_quiet()
    area._effects=[{'kind':'big'}];assert not root._idle_gc_quiet();area._effects=[]
    area.win_fx._closing_at=1;assert not root._idle_gc_quiet();area.win_fx._closing_at=0
    game._stats_release_at=101;assert not root._idle_gc_quiet();game._stats_release_at=None
    area._spring_power=.1;assert not root._idle_gc_quiet();area._spring_power=0
    _POPUP_N[0]=1;assert not root._idle_gc_quiet();_POPUP_N[0]=0
    root._idle_gc.stop()
    print('PASS: routine status suppressed, important errors retained, no status prewarm, motion blocks GC')


if __name__=='__main__':main()
