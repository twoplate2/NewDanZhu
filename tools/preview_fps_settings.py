"""隔离存档检查帧率设置布局。"""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.switch import Switch
from danzhu import config as C
from danzhu.ui.app import PlinkoApp
from danzhu.ui.root import RootWidget
C.SOUND_ENABLED=False
OUT=Path(__file__).resolve().parent/'output/fps_settings_preview'
OUT.mkdir(parents=True,exist_ok=True)
class Preview(PlinkoApp):
    def on_start(self):
        self.cases=iter([(360,640),(540,960)])
        self.popup=None
        Clock.schedule_once(self.next_case,2)
    def next_case(self,dt):
        if self.popup:self.popup.dismiss(animation=False)
        try:self.case=next(self.cases)
        except StopIteration:self.stop();return
        Window.size=self.case
        Clock.schedule_once(self.show,.3)
    def show(self,dt):
        self.rootw._show_fps_cap_settings()
        self.popup=Window.children[0]
        Clock.schedule_once(self.capture,.5)
    def capture(self,dt):
        assert not any(isinstance(w,Switch) for w in self.popup.walk())
        assert self.popup.y>=0 and self.popup.top<=Window.height+1
        print(Window.screenshot(str(OUT/('fps_%dx%d.png'%self.case))),flush=True)
        Clock.schedule_once(self.next_case,.1)
if __name__=='__main__':
    with tempfile.TemporaryDirectory() as folder:
        for name,file in (('_config_path','cfg.json'),('_history_path','rounds.json')):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        app=Preview();app._user_data_dir=folder;app.run()
