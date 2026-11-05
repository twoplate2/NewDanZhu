"""与0.8.147逐帧对照画布属性，统计重复写入并检查重建失效。"""
import ast
import json
import subprocess
import time
from bench_smoke import _boot


def baseline_functions(module):
    source=subprocess.check_output(['git','show','e7e65c2:danzhu/ui/winfx.py'],encoding='utf-8')
    tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='WinPileFX')
    nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_tbl_apply_fixed','_tbl_apply_balls')]
    namespace=dict(vars(module))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'baseline_winfx','exec'),namespace)
    return namespace['_tbl_apply_fixed'],namespace['_tbl_apply_balls']


class Counter:
    def __init__(self,target,counts):
        object.__setattr__(self,'target',target);object.__setattr__(self,'counts',counts)
    def __getattr__(self,key):return getattr(self.target,key)
    def __setattr__(self,key,value):
        if key in ('rgba','pos','size'):self.counts[0]+=1
        setattr(self.target,key,value)


def state(fx):
    rows=[]
    for _,c,r,*_ in fx._fx_pre+fx._fx_post:
        rows.append((tuple(c.rgba),tuple(r.pos),tuple(r.size)))
    for c,ro,r,b in fx._bd:
        rows.append((tuple(c.rgba),tuple(r.pos),tuple(r.size)))
    return rows


def main():
    root,clock,Clock=_boot()
    from danzhu.ui import winfx as W
    old_fixed,old_balls=baseline_functions(W)
    fx=root.game_area.win_fx
    totals=[0,0];frames=0
    for count in (2,50,100):
        fx.play_win(count,100)
        tex=W._glass_textures();fx._tbl_build(*tex)
        calls=[0]
        fx._fx_pre=[(k,Counter(c,calls),Counter(r,calls),a,b,d) for k,c,r,a,b,d in fx._fx_pre]
        fx._fx_post=[(k,Counter(c,calls),Counter(r,calls),a,b,d) for k,c,r,a,b,d in fx._fx_post]
        fx._bd=[(Counter(c,calls),ro,Counter(r,calls),b) for c,ro,r,b in fx._bd]
        for i in range(420):
            moment=i*(fx._last_settle+.6)/419
            fx._advance_balls(moment)
            a_cup=1. if i<360 else max(0.,(419-i)/59)
            k=1. if i<360 else 1.-(i-360)*.0003
            fx._apply_anim_rect(k,0.)
            args=(a_cup,1.,fx._abx,fx._aby,fx._abw,fx._abh)
            calls[0]=0;fx._tbl_apply_fixed(*args);fx._tbl_apply_balls(a_cup)
            totals[1]+=calls[0];new=state(fx)
            calls[0]=0;old_fixed(fx,*args);old_balls(fx,a_cup)
            totals[0]+=calls[0]
            assert new==state(fx),(count,i)
            frames+=1
        # 同颗数新一局也必须初始化缓存；隐藏->出现、尺寸变化重建。
        fx.play_win(count,100);fx._tbl_build(*tex)
        assert fx._fixed_state is None and fx._bd_state==[None]*count
        fx._tbl_drop();assert not fx._bd_state and fx._fixed_state is None
    # 原生指令计时，不用Counter代理；固定同一混合落定状态反复更新。
    fx.play_win(100,100);fx._tbl_build(*W._glass_textures())
    fx._advance_balls(fx._last_settle*.7);fx._apply_anim_rect(1.,0.)
    args=(1.,1.,fx._abx,fx._aby,fx._abw,fx._abh)
    timings={}
    for label,fixed,balls in [('old',old_fixed,old_balls),('new',W.WinPileFX._tbl_apply_fixed,W.WinPileFX._tbl_apply_balls)]:
        start=time.perf_counter()
        for _ in range(1000):fixed(fx,*args);balls(fx,1.)
        timings[label]=(time.perf_counter()-start)*1000
    assert totals[1]<totals[0]*.7,totals
    print(json.dumps(dict(frames_equal=frames,writes_old=totals[0],writes_new=totals[1],repeat_1000_ms=timings)))

if __name__=='__main__':main()
