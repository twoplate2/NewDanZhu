"""固定关闭循环GC：旧存档迁移、设置窗口、退场及生命周期回归。"""
import gc
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bench_smoke import _boot


def main():
    root, clock, Clock = _boot()
    with patch.object(gc, 'collect', side_effect=AssertionError('unexpected collect')), \
            patch.object(gc, 'enable', side_effect=AssertionError('unexpected enable')), \
            patch.object(gc, 'freeze', side_effect=AssertionError('unexpected freeze')):
        from danzhu.game import Game
        from danzhu.ui.app import PlinkoApp
        from danzhu.ui.winfx import HOLD_BASE, RESULT_FADE
        from kivy.core.window import Window
        from kivy.uix.slider import Slider
        from kivy.uix.switch import Switch

        assert not gc.isenabled()
        assert not hasattr(root, '_idle_gc')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(dict(gc_enabled=True, gc_manual_only=True,
                                           fps_cap_setting=120, balance=12345)), encoding='utf-8')
            loaded = Game(config_path=lambda: str(path))
            assert loaded.fps_cap_setting == 120 and loaded.balance == 12345
            assert not hasattr(loaded, 'gc_enabled') and not hasattr(loaded, 'gc_manual_only')
            assert not gc.isenabled()
            loaded.set_fps_cap_setting(60)
            saved = json.loads(path.read_text(encoding='utf-8'))
            assert 'gc_enabled' not in saved and 'gc_manual_only' not in saved
            assert saved['fps_cap_setting'] == 60 and saved['balance'] == 12345

            root.game._config_path = lambda: str(path)
            root.game._save_cfg = None
            original = root.game.fps_cap_setting
            for confirm in (False, True):
                root._show_fps_cap_settings()
                popup = Window.children[0]
                assert len([w for w in popup.walk() if isinstance(w, Switch)]) == 1
                assert not any('GC' in getattr(w, 'text', '') for w in popup.walk())
                slider = next(w for w in popup.walk() if isinstance(w, Slider))
                slider.value = 0
                label = '确定' if confirm else '取消'
                next(w for w in popup.walk() if getattr(w, 'text', '') == label).dispatch('on_release')
                popup.dismiss(animation=False)
                if not confirm:
                    assert root.game.fps_cap_setting == original
            from danzhu.config import FPS_CAP_OPTIONS
            assert root.game.fps_cap_setting == FPS_CAP_OPTIONS[0]
            assert Game(config_path=lambda: str(path)).fps_cap_setting == FPS_CAP_OPTIONS[0]

            # 玩家关闭和测试/benchmark自动关闭均不能触发回收。
            clock.t = 100.0
            fx = root.game_area.win_fx
            for automatic in (False, True):
                fx.play_win(2, 100)
                fx.mode = 'result'
                fx._balls = []
                fx._settled_at = clock.t - HOLD_BASE - 1
                fx._closing_at = 0
                fx._auto_close = automatic
                if not automatic:
                    assert fx.request_close()
                fx.tick()
                clock.t += RESULT_FADE + .01
                fx.tick()
                assert fx.mode == 'idle' and not gc.isenabled()

            host = SimpleNamespace(rootw=root)
            assert PlinkoApp.on_pause(host)
            assert PlinkoApp.on_resume(host)
            assert PlinkoApp.on_stop(host)
            assert not gc.isenabled()
    print('PASS: GC disabled; legacy config ignored; FPS confirm/cancel, cup close and lifecycle intact')


if __name__ == '__main__':
    main()
