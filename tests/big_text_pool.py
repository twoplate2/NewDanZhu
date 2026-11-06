"""中奖大字控件复用、颜色/淡出复位、重叠保护与循环垃圾对照。"""
import ast
import gc
import subprocess
from unittest.mock import patch
from pathlib import Path
from bench_smoke import _boot


def main():
    root,clock,Clock=_boot()
    from kivy.metrics import sp
    from danzhu.ui import game_area as G,text as T
    area=root.game_area
    Clock.unschedule(area.win_fx.prebake_step)
    area.size=(520,660);area._redraw()
    rec=T._glyph_bake(sp(48),True);assert rec is not None;T._glyph_put(sp(48),True,rec)
    def expire():
        clock.t+=G.BIG_TEXT_LIFE+.1
        area.tick_draw(1/60)
        assert not area._effects
    source=subprocess.check_output(['git','show','b327fd1:danzhu/ui/game_area.py'],cwd=Path(__file__).resolve().parents[1],text=True,encoding='utf-8')
    tree=ast.parse(source);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='GameArea')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='big_result_text')
    ns=dict(G.__dict__);exec(compile(ast.Module(body=[fn],type_ignores=[]),'<old-big-text>','exec'),ns)
    gc.collect(2)
    for _ in range(10):ns['big_result_text'](area,2,200);expire()
    before=gc.collect(2)
    area.big_result_text(2,200);pair=tuple(area._effects[-1]['ws']);expire()
    gc.collect(2)
    for i in range(10):
        area.big_result_text(10 if i%2 else 2,1000 if i%2 else 200)
        assert tuple(area._effects[-1]['ws'])==pair
        main,shadow=pair
        assert abs(main._gcol.a-1)<1e-6 and abs(shadow._gcol.a-.6)<1e-6
        expected=G.hex_rgb(G.slot_color(10 if i%2 else 2))
        assert all(abs(a-b)<1e-6 for a,b in zip(main._gcol.rgb,expected))
        assert main._pop_sc.x==main._pop_sc.y==1
        assert main.center==[area._effects[-1]['cx'],area._effects[-1]['cy']]
        expire()
    after=gc.collect(2)
    assert after<before/2,(before,after)
    for _ in range(3):area.big_result_text(2,200)
    widgets=[w for e in area._effects for w in e['ws']]
    assert len({id(w) for w in widgets})==6
    assert len(area._big_text_pool['glyph'])==2
    expire();area.big_result_text(0,0);miss=tuple(area._effects[-1]['ws']);expire()
    area.big_result_text(0,0);assert tuple(area._effects[-1]['ws'])==miss;expire()
    with patch.object(G,'_glyph_rec',return_value=None):
        area.big_result_text(2,200)
        assert tuple(area._effects[-1]['ws'])==miss
        assert miss[0].text=='+200'
        assert T._lbl_canvas_color(miss[0]).a==1
        expire()
    print('PASS: pooled widgets reset correctly, active overlap protected; old/new unreachable objects',before,after)


if __name__=='__main__':main()
