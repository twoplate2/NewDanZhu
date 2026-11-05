"""截图逐像素对照新建与复用的大字；不同历史颜色/透明度不应污染下一次。"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import main
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics.opengl import glPixelStorei,GL_PACK_ALIGNMENT
from kivy.metrics import sp
from PIL import Image
from danzhu import config as C
from danzhu.ui import game_area as G,text as T
from danzhu.ui.app import PlinkoApp
from danzhu.ui.root import RootWidget

C.SOUND_ENABLED=False
OUT=ROOT/'tools/output/big_text_pool_preview';OUT.mkdir(parents=True,exist_ok=True)


class Probe(PlinkoApp):
    def on_start(self):
        self.cases=iter((m,phase,reuse) for m in (2,0) for phase in (0.,.4,.85) for reuse in (False,True))
        self.before=None;self.failures=[];self.fake=SimpleNamespace(t=0.)
        self.real_time=G.time
        Clock.schedule_once(self.prepare,2.5)

    def prepare(self,_dt):
        Clock.unschedule(self.rootw._frame_timed)
        T._glyph_put(sp(48),True,T._glyph_bake(sp(48),True))
        G.time=SimpleNamespace(time=lambda:self.fake.t,perf_counter=self.real_time.perf_counter)
        self.next_case(0)

    def expire(self):
        self.fake.t+=G.BIG_TEXT_LIFE+1
        self.rootw.game_area.tick_draw(1/60)

    def next_case(self,_dt):
        self.expire()
        try:self.case=next(self.cases)
        except StopIteration:
            G.time=self.real_time;self.stop();return
        m,phase,reuse=self.case;area=self.rootw.game_area
        if not reuse:area._big_text_pool={'glyph':[],'label':[]}
        else:
            area.big_result_text(10,1000);self.fake.t+=G.BIG_TEXT_LIFE*.9;area.tick_draw(1/60);self.expire()
        area.big_result_text(m,200 if m else 0)
        self.fake.t+=G.BIG_TEXT_LIFE*phase;area.tick_draw(1/60)
        Clock.schedule_once(self.capture,.15)

    def capture(self,_dt):
        m,phase,reuse=self.case
        glPixelStorei(GL_PACK_ALIGNMENT,1)
        try:path=Window.screenshot(str(OUT/('m%d_p%d_%s.png'%(m,round(phase*100),'reuse' if reuse else 'fresh'))))
        finally:glPixelStorei(GL_PACK_ALIGNMENT,4)
        with Image.open(path) as im:pixels=im.convert('RGBA').tobytes()
        if not reuse:self.before=pixels
        elif pixels!=self.before:self.failures.append(self.case)
        print('BIG',self.case,path,'matches',not reuse or pixels==self.before,flush=True)
        Clock.schedule_once(self.next_case,.05)


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='pool_preview_') as folder:
        for name,file in (('_config_path','cfg.json'),('_history_path','rounds.json'),('_bench_history_path','bench.json'),('_hp_history_path','hp.json')):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        app=Probe();app._user_data_dir=folder;app.run()
        if app.failures:raise SystemExit(str(app.failures))
