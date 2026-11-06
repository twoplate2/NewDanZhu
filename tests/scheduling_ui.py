"""CPU偏好确认/取消、设备存档和真实游戏/测试共享策略。"""
import json
from pathlib import Path
import tempfile
from bench_smoke import _boot


def main():
    root,_,_=_boot()
    from kivy.core.window import Window
    from kivy.uix.switch import Switch
    from danzhu.game import Game
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'config.json'
        root.game._config_path=lambda:str(path)
        root.game._save_cfg=None
        assert root.game.prefer_fast_cores is False
        for confirm in (False,True):
            root._show_fps_cap_settings()
            popup=Window.children[0]
            switches=[w for w in popup.walk() if isinstance(w,Switch)]
            assert len(switches)==1 and not switches[0].active
            switches[0].active=True
            label='确定' if confirm else '取消'
            next(w for w in popup.walk() if getattr(w,'text','')==label).dispatch('on_release')
            popup.dismiss(animation=False)
            assert root.game.prefer_fast_cores==confirm
            assert root._cpu_policy.prefer_fast==confirm
        assert Game(config_path=lambda:str(path)).prefer_fast_cores
        root._act(root.game.set_prefer_fast_cores,False)
        assert not Game(config_path=lambda:str(path)).prefer_fast_cores
        for value in ('true',1,None):
            path.write_text(json.dumps({'prefer_fast_cores':value}),encoding='utf-8')
            assert not Game(config_path=lambda:str(path)).prefer_fast_cores
        assert not root.game.set_prefer_fast_cores('true')
        assert root._cpu_policy.snapshot()['reason']=='not_android'
    print('PASS: CPU preference default, confirm/cancel, persistence, strict boolean and desktop fallback')


if __name__=='__main__':main()
