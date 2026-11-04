"""固定随机种子、布局和时刻，对照装杯材质；只使用临时存档。

python tools/preview_cup_materials.py --assets tools/output/cup_material_review/baseline --label baseline
"""
import argparse
import hashlib
import json
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--assets",type=Path,default=ROOT/"assets/balls")
    parser.add_argument("--label",default="current")
    parser.add_argument("--counts",default="1,2,10,100")
    parser.add_argument("--bets",default="100")
    parser.add_argument("--phases",default="rest")
    parser.add_argument("--background",choices=("board","light","checker"),default="board")
    parser.add_argument("--output",type=Path,default=ROOT/"tools/output/cup_material_review")
    args=parser.parse_args()
    import main as entry
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.core.image import Image as CoreImage
    from kivy.graphics import Color,Rectangle
    from kivy.graphics.opengl import glPixelStorei,GL_PACK_ALIGNMENT
    from danzhu import config as C
    from danzhu.ui import winfx as W
    from danzhu.ui.app import PlinkoApp
    from danzhu.ui.root import RootWidget
    C.SOUND_ENABLED=False
    output=args.output/args.label
    output.mkdir(parents=True,exist_ok=True)
    textures={}
    for bet in map(int,args.bets.split(",")):
        tex=CoreImage(str(args.assets/("cup_%d.png" % bet)),mipmap=True).texture
        tex.mag_filter="linear";tex.min_filter="linear_mipmap_linear"
        textures[bet]=tex
    original_texture=W._ball_texture
    W._ball_texture=lambda bet,rows=None: textures[bet] if bet in textures else original_texture(bet,rows)
    cases=[(bet,count,phase) for bet in textures for count in map(int,args.counts.split(","))
           for phase in args.phases.split(",")]
    records=[]

    class Preview(PlinkoApp):
        def on_start(self):
            Clock.schedule_once(self.prepare,1.0)

        def prepare(self,_dt):
            Clock.unschedule(self.rootw._frame_timed)
            self.rootw.game.multipliers=[5,2,2,3,2,20,2,2,5]
            self.next_case(0)

        def next_case(self,_dt):
            if not cases:
                self.stop();return
            bet,count,phase=cases.pop(0)
            area=self.rootw.game_area
            area._redraw()
            if args.background!="board":
                with area.canvas:
                    cell=32
                    for y in range(0,int(area.height)+cell,cell):
                        for x in range(0,int(area.width)+cell,cell):
                            value=.92 if args.background=="light" or (x//cell+y//cell)%2 else .12
                            Color(value,value,value,1)
                            Rectangle(pos=(area.x+x,area.y+y),size=(cell,cell))
                area._restack_overlays()
            fx=area.win_fx
            fx._seq=0;fx._rng=random.Random(1234)
            if not fx.play_win(count,bet):raise RuntimeError("preview scene failed")
            first=min(fx._balls,key=lambda b:b["i"])
            moments={"fall":first["t0"]+first["f_enter"]+.6*(first["f"]-first["f_enter"]),
                     "touch":first["t0"]+first["f"]+.005,
                     "bounce":first["t0"]+first["f"]+first["t1"]*.5,
                     "rest":fx._last_settle+.1}
            moment=moments[phase]
            fx._t0=time.time()-moment;fx.mode="win"
            fx._advance_balls(moment);fx._redraw();self.rootw._sync_hud_dim()
            layout=[(b["i"],b["sxf"],b["syf"],b["z"],b["r_d"],b["t0"],b["f"],b["end"]) for b in fx._balls]
            nearest=fx._ball_screen(fx._balls[-1])
            self.record=dict(bet=bet,count=count,phase=phase,background=args.background,
                             scene_sha256=hashlib.sha256(json.dumps(layout).encode()).hexdigest(),
                             nearest_ball=nearest,window=list(Window.size))
            Clock.schedule_once(self.capture,.08)

        def capture(self,_dt):
            r=self.record
            path=output/("bet%d_n%d_%s_%s.png" % (r["bet"],r["count"],r["phase"],r["background"]))
            # RGB 截图每行字节数在 125% DPI 下不一定是 4 的倍数。
            glPixelStorei(GL_PACK_ALIGNMENT,1)
            try:
                r["screenshot"]=Window.screenshot(str(path))
            finally:
                glPixelStorei(GL_PACK_ALIGNMENT,4)
            records.append(r)
            print("CAPTURE",r["screenshot"],flush=True)
            Clock.schedule_once(self.next_case,.03)

    with tempfile.TemporaryDirectory(prefix="danzhu_material_preview_") as folder:
        for name,file in (("_config_path","config.json"),("_history_path","rounds.json"),
                          ("_bench_history_path","bench.json"),("_hp_history_path","hp.json")):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        Preview().run()
    (output/("captures_%s.json" % args.background)).write_text(
        json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
