"""真实窗口检查缓存轮次弹窗：六种尺寸/数据，记录显示期间的文字光栅化次数。"""
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import main
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.core.text import Label as CoreLabel
from kivy.uix.label import Label
from kivy.graphics.opengl import glPixelStorei,GL_PACK_ALIGNMENT
from danzhu import config as C
from danzhu.ui.app import PlinkoApp
from danzhu.ui.root import RootWidget

C.SOUND_ENABLED=False
OUT=ROOT/'tools/output/round_end_preview';OUT.mkdir(parents=True,exist_ok=True)


class Probe(PlinkoApp):
    def on_start(self):
        self.cases=iter([(360,640,50,1234),(360,640,100,123456789012345678),
                         (540,960,50,98765),(720,1280,20,0),(960,600,100,9999999),
                         (360,640,20,500)])
        self.original_refresh=CoreLabel.refresh
        self.count=0;self.watched=set();self.measuring=False;self.failures=[]
        def refresh(label,*args,**kw):
            if self.measuring and label.text in self.watched:self.count+=1
            return self.original_refresh(label,*args,**kw)
        CoreLabel.refresh=refresh
        Clock.schedule_once(self.next_case,2.5)

    def next_case(self,_dt):
        self.measuring=False
        view=self.rootw._prepare_round_end();view.popup.dismiss(animation=False)
        try:self.case=next(self.cases)
        except StopIteration:
            CoreLabel.refresh=self.original_refresh
            self.stop();return
        Window.size=self.case[:2]
        Clock.schedule_once(self.show_case,.8)

    def show_case(self,_dt):
        view=self.rootw._prepare_round_end()
        self.watched={w.text for w in view.popup.walk() if isinstance(w,Label) and w.text}
        self.count=0;self.measuring=True
        self.rootw._show_round_end(dict(plays=self.case[2],balance=self.case[3]))
        Clock.schedule_once(self.capture,.4)

    def capture(self,_dt):
        self.measuring=False
        view=self.rootw._round_end_view;p=view.popup
        try:
            assert self.count==0,('popup text rasterization',self.count)
            vw,vh=self.rootw._veq()
            assert p.width<=vw and p.height<=vh
            for row in (view.plays,view.balance):
                assert row.prefix.x>=row.x-1
                assert row.suffix.right<=row.right+1
                for rect in row.number._rects:
                    if rect.size[0]:assert rect.pos[0]+rect.size[0]<=row.number.right+1
            assert view.footer.texture_size[0]<=view.footer.width+1
            assert view.button.y>=view.content.y
            assert view.balance.number.text==str(self.case[3])
        except AssertionError as exc:self.failures.append((self.case,str(exc)))
        glPixelStorei(GL_PACK_ALIGNMENT,1)
        try:path=Window.screenshot(str(OUT/('round_%dx%d_%d_%d.png'%self.case)))
        finally:glPixelStorei(GL_PACK_ALIGNMENT,4)
        print('ROUND',self.case,'rasterizations',self.count,'screenshot',path,'failures',self.failures,flush=True)
        Clock.schedule_once(self.next_case,.1)


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='round_end_preview_') as folder:
        for name,file in (('_config_path','cfg.json'),('_history_path','rounds.json'),
                          ('_bench_history_path','bench.json'),('_hp_history_path','hp.json')):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        app=Probe();app._user_data_dir=folder;app.run()
        if app.failures:raise SystemExit(str(app.failures))
