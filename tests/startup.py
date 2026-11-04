"""真实 Kivy 时钟下的启动回归；子进程隔离音频、图集缓存和临时存档。

    python tests/startup.py

覆盖首屏文字、预热异常、预热链停摆，以及启动诊断的耗时归属。
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def child(scenario):
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    os.environ["KIVY_NO_ARGS"] = "1"
    started = time.perf_counter()
    import main  # 启用与真实入口相同的窗口/时钟配置
    from kivy.clock import Clock
    from danzhu.ui.app import PlinkoApp
    from danzhu.ui import text as T, widgets as W, winfx as FX
    from danzhu.ui.bench import BenchMixin
    from danzhu.platform.boot import _BOOT_T0

    if scenario == "failed":
        def fail(_area):
            raise RuntimeError("injected glyph warm failure")
        FX._glyph_warm_step = fail
    if scenario == "stalled":
        W._GLYPH_WAIT_MAX = 0.25

    class Probe(PlinkoApp):
        @property
        def user_data_dir(self):
            return data

        def on_start(self):
            self.audio_at = None
            self.saved_veil = self.rootw._load_veil
            if scenario == "stalled":
                # 不再改余额或字号；超时必须由 Clock 主动唤醒标签。
                Clock.unschedule(self.rootw.game_area.win_fx.prebake_step)
            Clock.schedule_interval(self.check, 0.01)

        def check(self, _dt):
            elapsed = time.perf_counter() - started
            assert elapsed < 15, "启动未放行"
            r = self.rootw
            if r.sfx.audio_ready() and self.audio_at is None:
                self.audio_at = elapsed
            if r._load_veil is not None:
                return
            label = r.balance_lbl
            assert label._quads or label._fb is not None, "摘页后余额仍为空白"
            assert label not in T._GLYPH_WAITERS, "完成后仍持有等待标签"
            assert getattr(label, "_wait_event", None) is None, "完成后仍有超时回调"
            if scenario == "normal":
                assert not label._degraded, "正常启动应保留字形图集"
                assert not getattr(r.game_area.win_fx, "_texwarm_done", False), "首屏仍在等待非必需特效"
            else:
                assert label._fb is not None, "预热失败后没有普通文字兜底"
                assert tuple(label._fb.pos) == tuple(label.pos), "兜底文字偏离余额位置"
                label.pos = (123, 234)
                assert tuple(label._fb.pos) == (123, 234), "布局变动后兜底文字未跟随"
                label.size = (140, 40)
                assert tuple(label._fb.size) == (140, 40)

            # 音频一秒完成、首屏文字六秒完成：额外五秒不能归咎于音频。
            owner = SimpleNamespace(sfx=SimpleNamespace(_baked_at=_BOOT_T0 + 1))
            veil = SimpleNamespace(_done_at_boot=0.5, _glyph_ready_at_boot=6)
            measured = BenchMixin._calc_no_audio_ms(owner, veil, 6100)
            assert abs(measured - 6000) < 0.01, measured
            print("STARTUP " + json.dumps(dict(
                scenario=scenario, playable=round(elapsed, 3),
                audio=round(self.audio_at, 3),
                fallback=label._degraded)))
            self.stop()

    with tempfile.TemporaryDirectory(prefix="danzhu_startup_test_") as data:
        # 桌面产品存档固定写 TEMP，覆盖 user_data_dir 不足以隔离。
        from danzhu.ui.root import RootWidget
        for name, filename in (("_config_path", "config.json"), ("_history_path", "rounds.json"),
                               ("_bench_history_path", "bench.json"), ("_hp_history_path", "hp.json")):
            setattr(RootWidget, name, staticmethod(lambda p=str(Path(data) / filename): p))
        Probe().run()


def main_test():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for scenario in ("normal", "failed", "stalled"):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--case", scenario],
            cwd=ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=25)
        results = [line for line in proc.stdout.splitlines() if line.startswith("STARTUP ")]
        if proc.returncode or not results:
            print(proc.stdout)
            print(proc.stderr)
            raise SystemExit("启动测试失败: " + scenario)
        print(results[-1])


if __name__ == "__main__":
    if "--case" in sys.argv:
        child(sys.argv[sys.argv.index("--case") + 1])
    else:
        main_test()
