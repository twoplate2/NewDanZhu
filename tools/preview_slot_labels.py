"""隔离存档对照倍率槽文字，覆盖手机与桌面尺寸。"""
from pathlib import Path
import sys
import tempfile
import argparse
import ast
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from kivy.clock import Clock
from kivy.core.window import Window
from danzhu import config as C
from danzhu.ui.app import PlinkoApp
from danzhu.ui.root import RootWidget
from danzhu.ui import game_area as G
C.SOUND_ENABLED=False
parser=argparse.ArgumentParser();parser.add_argument('--baseline',action='store_true');args=parser.parse_args()
if args.baseline:
    source=subprocess.check_output(['git','show','e8db64c:danzhu/ui/game_area.py'],encoding='utf-8')
    cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='GameArea')
    nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_redraw','_update_slots')]
    ns=dict(vars(G));exec(compile(ast.Module(body=nodes,type_ignores=[]),'old_slots','exec'),ns)
    for n in nodes:setattr(G.GameArea,n.name,ns[n.name])
OUT=Path(__file__).resolve().parent/'output/slot_labels'/('before' if args.baseline else 'after')
OUT.mkdir(parents=True,exist_ok=True)
class Preview(PlinkoApp):
    def on_start(self):
        self.cases=iter([(360,640),(540,960),(720,1280)])
        Clock.schedule_once(self.next_case,2)
    def next_case(self,dt):
        try:self.case=next(self.cases)
        except StopIteration:self.stop();return
        Window.size=self.case
        Clock.schedule_once(self.draw,.3)
    def draw(self,dt):
        self.rootw.game.multipliers=[2,3,5,10,20,50,100,5,100]
        self.rootw.game_area._update_slots()
        Clock.schedule_once(self.capture,.2)
    def capture(self,dt):
        print(Window.screenshot(str(OUT/('slots_%dx%d.png'%self.case))),flush=True)
        Clock.schedule_once(self.next_case,.1)
if __name__=='__main__':
    with tempfile.TemporaryDirectory() as folder:
        for name,file in (('_config_path','cfg.json'),('_history_path','rounds.json')):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        app=Preview();app._user_data_dir=folder;app.run()
