"""使用合成统计检查实玩面板布局；不运行 CPU 高压，不写玩家文件。"""
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import main
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics.opengl import glPixelStorei, GL_PACK_ALIGNMENT
from danzhu import config as C
from danzhu.ui.app import PlinkoApp
from danzhu.ui.root import RootWidget
from danzhu.bench.playtest import PlaytestRecord
import danzhu.ui.playtest as UI

C.SOUND_ENABLED=False
OUT=ROOT/'tools/output/random_play_preview'
OUT.mkdir(parents=True,exist_ok=True)


class Probe(PlinkoApp):
    def on_start(self):
        self.cases=iter([(kind,w,h) for w,h in ((360,640),(540,960),(720,1280))
                         for kind in ('hidden','live','summary','history')])
        self.popup=None
        Clock.schedule_once(self.prepare,2.5)

    def prepare(self,_dt):
        self.rootw._act(self.rootw.game.set_bet,100)
        self.rootw._act(self.rootw.game.set_rtp,3.6)
        self.next_case(0)

    def next_case(self,_dt):
        if self.popup:self.popup.dismiss(animation=False);self.popup=None
        self.rootw._close_playtest()
        try:self.case=next(self.cases)
        except StopIteration:self.stop();return
        _,w,h=self.case
        Window.size=(w,h)
        Clock.schedule_once(self.open_case,.3)

    def open_case(self,_dt):
        root=self.rootw
        root._start_playtest()
        root._playtest_driver.cancel()
        if root._playtest_display is not None:root._playtest_display.cancel()
        t=[time.perf_counter()-13]
        r=PlaytestRecord(clock=lambda:t[0]);r.settings=dict(root._playtest.settings)
        r.flip(0)
        for i in range(2100):
            t[0]+=.018 if i%180==0 else .006
            r.frame(.3);r.flip(2)
            if i%80==0:r.update_live()
        r.clock=time.perf_counter;r.previous=time.perf_counter()
        root._playtest=r
        UI._live_power_snapshot=lambda:dict(temp=36.,mv=4000,current_raw=600000,current_source='preview',
                                          current_unit='ua',watts=2.4,plugged=0)
        kind,_,_=self.case
        if kind!='hidden':root._toggle_playtest_info()
        root._playtest_live(0)
        if kind not in ('live','hidden'):
            root._stop_playtest(show_summary=False)
            record=root._playtest_result
            if kind=='summary':self.popup=root._show_playtest_detail(record)
            else:
                root._write_playtest_history([dict(record,id=str(i),time=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(time.time()-i*3600)),
                                                  duration_s=6000 if i==0 else 1225) for i in range(5)])
                self.popup=root._show_playtest_history()
        Clock.schedule_once(self.capture,.5)

    def capture(self,_dt):
        kind,w,h=self.case
        if kind=='history':
            for _,labels,_ in self.popup._history_rows:
                for label in labels:
                    label.texture_update()
                    assert label.texture_size[0]<=label.width+1,(label.text,label.texture_size,label.size)
                    assert label.texture_size[1]<=label.height+1,(label.text,label.texture_size,label.size)
        elif kind=='hidden':
            assert not self.rootw._playtest_info_built
            assert len(self.rootw._playtest_panel.children)==1
        elif kind=='live':
            for label in (self.rootw._playtest_label,self.rootw._playtest_sensor):
                label.texture_update()
                assert label.texture_size[1]<=label.height+1,(label.text,label.texture_size,label.size)
            first,second=self.rootw._playtest_stat_cells
            for a,b in zip(first,second):
                assert abs(a.to_window(a.x,a.y)[0]-b.to_window(b.x,b.y)[0])<.1
                for label in (a,b):
                    label.texture_update()
                    assert label.texture_size[1]<=label.height+1
        glPixelStorei(GL_PACK_ALIGNMENT,1)
        try:path=Window.screenshot(str(OUT/('%s_%dx%d.png'%self.case)))
        finally:glPixelStorei(GL_PACK_ALIGNMENT,4)
        print('CAPTURE',self.case,path,flush=True)
        Clock.schedule_once(self.next_case,.1)


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='random_play_preview_') as folder:
        for name,file in (('_config_path','cfg.json'),('_history_path','rounds.json'),
                          ('_bench_history_path','bench.json'),('_hp_history_path','hp.json')):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        app=Probe();app._user_data_dir=folder;app.run()
