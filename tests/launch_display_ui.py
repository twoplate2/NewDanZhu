"""真实设置控件：默认模式、取消/确认、保存、窗口尺寸与文字边界。"""
from pathlib import Path
import tempfile

from bench_smoke import _boot


def main():
    root, clock, Clock = _boot()
    from kivy.core.window import Window
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.togglebutton import ToggleButton
    from danzhu.game import Game

    def pump():
        for _ in range(20):
            clock.t += 1/60
            Clock.tick()

    with tempfile.TemporaryDirectory(prefix='danzhu_launch_ui_') as directory:
        path = Path(directory)/'config.json'
        root.game._config_path = lambda: str(path)
        root.game._save_cfg = None
        assert root.game.launch_extrapolation is False
        for width, height in ((360,640),(540,960)):
            Window.size = (width,height)
            for text, confirm, expected in (('发射段外推',False,False),
                                            ('发射段外推',True,True),
                                            ('原始显示',True,False)):
                before = root.game.launch_extrapolation
                root._show_fps_cap_settings()
                popup = Window.children[0]
                pump()
                toggles = [w for w in popup.walk() if isinstance(w,ToggleButton)]
                assert len(toggles) == 2
                chosen = next(w for w in toggles if w.text == text)
                chosen.trigger_action(duration=0)
                pump()
                assert chosen.state == 'down'
                assert sum(w.state == 'down' for w in toggles) == 1
                assert root.game.launch_extrapolation == before
                assert popup.y >= -1 and popup.top <= Window.height + 1
                for widget in popup.content.walk():
                    if isinstance(widget, Button):
                        assert widget.y >= popup.y-1 and widget.top <= popup.top+1,(widget.text,widget.pos)
                        widget.texture_update()
                        assert widget.texture_size[0] <= widget.width+1,(widget.text,widget.width)
                    if isinstance(widget, Label) and widget.text.startswith('仅调整'):
                        widget.texture_update()
                        assert widget.texture_size[1] <= widget.height+1
                next(w for w in popup.walk() if isinstance(w,Button) and w.text == ('确定' if confirm else '取消')).dispatch('on_release')
                pump()
                assert root.game.launch_extrapolation == expected
                assert Game(config_path=lambda: str(path)).launch_extrapolation == expected
                popup.dismiss(animation=False)
        print('PASS: original default, confirm/cancel, persistence and 360/540-width layout')


if __name__ == '__main__':
    main()
