"""开发用隔离进程：截取首次第1代回收对象类型；不用于帧率成绩，不写玩家存档。"""
from collections import Counter
import gc
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from bench_smoke import _boot


class Captured(Exception):pass


def main():
    root,t,Clock=_boot()
    from kivy.app import App
    from kivy.core.window import Window
    import danzhu.ui.playtest as P
    t.time_ns=lambda:int(t.t*1e9)
    P.time=t
    root._load_veil=None;root._idle_gc.clock=t.perf_counter
    original=gc.collect
    mode=sys.argv[1] if len(sys.argv)>1 else 'simulation'
    result={}
    class Backend:
        def __getattr__(self,name):return getattr(gc,name)
        def collect(self,generation):
            if generation!=1:return original(generation)
            gc.set_debug(gc.DEBUG_SAVEALL)
            count=original(generation)
            objects=list(gc.garbage)
            result.update(mode=mode,collected=count,objects=len(objects),
                types=Counter(type(o).__module__+'.'+type(o).__name__ for o in objects).most_common(20),
                methods=Counter(getattr(getattr(o,'__func__',None),'__qualname__','') for o in objects if type(o).__name__=='method').most_common(12),
                functions=Counter(o.__qualname__ for o in objects if type(o).__name__=='function').most_common(12),
                core_texts=Counter(str(getattr(o,'text',''))[:100] for o in objects if type(o).__name__=='LabelSDL2').most_common(15))
            from kivy.uix.widget import Widget
            widgets=[]
            for obj in objects:
                try:
                    if isinstance(obj,Widget):widgets.append(type(obj).__name__)
                except ReferenceError:pass
            result['widgets']=Counter(widgets).most_common(15)
            raise Captured()
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(App,'get_running_app',return_value=SimpleNamespace(user_data_dir=directory)):
            for _ in range(1000):t.t+=1/120;Clock.tick();root._frame_timed(1/120)
            root._idle_gc.backend=Backend()
            try:
                if mode=='simulation':root._start_playtest()
                for i in range(60000):
                    t.t+=1/120;Clock.tick();root._frame_timed(1/120)
                    if mode=='simulation':root._playtest_flip(Window)
                    else:
                        g=root.game;fx=root.game_area.win_fx
                        if g._round_end_shown:root._act(g.round_end_confirm)
                        elif fx.mode=='result':fx.request_close()
                        elif g.state=='ready' and fx.mode=='idle':
                            if g.balance<g.bet:root._act(g.reset_balance)
                            root._act(g.start_charge)
                        elif g.state=='charging' and g.power>=.8:root._act(g.launch)
                    if i%60==0:Window.dispatch('on_draw')
            except Captured:pass
            finally:
                if not result:result.update(mode=mode,captured=False,reason='no generation-1 idle collection reached')
                gc.set_debug(0);gc.garbage.clear();root._idle_gc.stop()
                root._close_playtest()
    path=ROOT/('tools/output/gc_objects_'+mode+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
