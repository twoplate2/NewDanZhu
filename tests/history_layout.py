"""仅用模拟历史记录验证两个窗口；不运行跑分或 CPU 高压。"""

from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import main
from kivy.clock import Clock
from kivy.core.window import Window
from danzhu import config as C
from danzhu.ui.app import PlinkoApp
from danzhu.ui.root import RootWidget

C.SOUND_ENABLED=False


class Probe(PlinkoApp):
    def on_start(self):
        self.popup=None
        self.previous=None
        self.cases=[("bench",540,850,5),("hp",540,850,1),
                    ("bench",360,640,5),("hp",360,640,100),
                    ("bench",960,600,100),("hp",960,600,0),
                    ("resize",540,850,5),("resize",800,950,5)]
        self.failures=[]
        Clock.schedule_once(self.next_case,.4)

    def next_case(self,_dt):
        if not self.cases:
            if self.popup:self.popup.dismiss(animation=False)
            self.stop()
            return
        kind,w,h,n=self.cases.pop(0)
        self.case=(kind,w,h,n)
        if self.popup is not None and not (kind=="resize" and self.previous=="resize"):
            self.popup.dismiss(animation=False);self.popup=None
        Window.size=(w,h)
        self.layer.apply_orientation()
        records=[dict(time="2026-11-05 01:%02d" % (i%60),render_fps=60.1,
                      render_1low=56.2,phys_fps=28625+i,phys_mad=2.6,
                      mean=26703+i,mad=12.72) for i in range(n)]
        self.rootw.bench_history=[dict(r) for r in records]
        self.rootw.hp_history=[dict(r) for r in records]
        Clock.schedule_once(self.open_case,.15)

    def open_case(self,_dt):
        kind,w,h,n=self.case
        if self.popup is None:
            self.popup=(self.rootw._show_hp_history() if kind=="hp" else self.rootw._show_bench_history())
        Clock.schedule_once(self.check_case,.3)

    def check_case(self,_dt):
        kind,w,h,n=self.case
        p=self.popup
        try:
            assert p.width<=Window.width+1 and p.height<=Window.height+1,(p.size,Window.size)
            if n:
                row,labels,button=p._history_rows[0]
                for head,cell in zip(p._history_headers,[*labels,button]):
                    head_x=head.to_window(head.x,head.y)[0]
                    cell_x=cell.to_window(cell.x,cell.y)[0]
                    assert abs(head_x-cell_x)<1 and abs(head.width-cell.width)<1,(head_x,cell_x,head.width,cell.width)
                    assert abs(head.font_size-cell.font_size)<.01
                    head.texture_update()
                    assert head.texture_size[1]<=head.height+1,(head.text,head.texture_size,head.size)
                for _,cells,btn in p._history_rows:
                    for cell in cells:
                        cell.texture_update()
                        assert cell.texture_size[0]<=cell.width+1,(cell.text,cell.texture_size,cell.size)
                        assert cell.texture_size[1]<=cell.height+1,(cell.text,cell.texture_size,cell.size)
                    assert btn.height>=32
                if n==100:assert p._history_scroll.height<p._history_scroll.children[0].height
                if kind=="resize":
                    if self.previous=="resize":
                        assert labels[0].font_size>self.previous_font
                        assert button.height>self.previous_button_height
                    self.previous_font=labels[0].font_size
                    self.previous_button_height=button.height
            assert p._history_actions.y>=p.y-1
            path=Path(tempfile.gettempdir())/("danzhu_history_%s_%dx%d_%d.png" % self.case)
            print("LAYOUT",self.case,"popup",tuple(round(v,1) for v in p.size),
                  "font",round(p._history_rows[0][1][0].font_size,1) if n else "empty",
                  "screenshot",Window.screenshot(str(path)),flush=True)
        except AssertionError as exc:
            self.failures.append((self.case,str(exc)));print("FAIL",self.case,exc,flush=True)
        self.previous=kind
        Clock.schedule_once(self.next_case,.05)


def forbidden(*_a,**_kw):
    raise AssertionError("历史排版测试不允许启动跑分或高压测试")


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="danzhu_history_layout_") as folder:
        for name,file in (("_config_path","cfg.json"),("_history_path","rounds.json"),
                          ("_bench_history_path","bench.json"),("_hp_history_path","hp.json")):
            setattr(RootWidget,name,staticmethod(lambda p=str(Path(folder)/file):p))
        RootWidget._start_hp_test=forbidden
        RootWidget._start_bench_test=forbidden
        app=Probe();app.run()
        if app.failures:raise SystemExit(str(app.failures))
