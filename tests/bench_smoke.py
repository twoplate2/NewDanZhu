"""跑分链冒烟 —— 把 `danzhu/ui/bench.py`（5100+ 行）的每个入口挨个打开，只求"不炸"。

    python tests/bench_smoke.py

## 这条门禁补的是什么洞

`bench.py` 是**隐藏开发工程**（长按标题 3 秒进）：跑分 / CPU 高压 / 诊断面板 / 启动加载 /
曲线与历史。它占 `danzhu/` 的 **26%**，而：

* `tests/trace.py` 的脚本**从不进跑分菜单** ⇒ 轨迹对账一点都覆盖不到它；
* `tests/fx_gates.py`（端口自老版 `fx_probe.py`）管的是演出/UI 的**判据**，
  也只碰到其中一部分；
* `--smoke` 只把 `_bench_running` 置真、验两条守卫。

⇒ 于是这里补一条**最粗但覆盖最广**的：把每个入口打开一次、推几帧，看有没有
AttributeError / NameError / TypeError。**它不做任何判据** —— 判据归 `fx_gates.py`。

⚠️ 它**不是**"跑分功能正确"的证据，只是"这些路都还能走通"。
   真正的功能对等要靠 `tests/fx_gates.py` 的 380 条。
"""

import os
import sys
import tempfile
import traceback
import warnings

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("KIVY_NO_ARGS", "1")
warnings.filterwarnings("ignore")

DT = 1.0 / 60.0


class _FakeTime(object):
    """与 `tests/trace.py` 同款的假钟 —— 跑分链里有大量 `time.time()` 判据。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def perf_counter(self):
        return self.t

    def monotonic(self):
        return self.t

    def thread_time(self):
        return 0.0

    def process_time(self):
        return 0.0

    def sleep(self, *_a):
        pass

    def strftime(self, *a):
        import time as _t
        return _t.strftime(*a)

    def strptime(self, *a):
        import time as _t
        return _t.strptime(*a)

    def localtime(self, *a):
        import time as _t
        return _t.localtime(*a)


def _boot():
    from kivy.clock import Clock
    from kivy.core.window import Window

    ft = _FakeTime()
    Clock._max_fps = 0.0
    Clock.time = lambda: ft.t
    # ⚠️ 顺序不能反: `schedule_once` 当场把 timeout 折进 `_last_tick`, 先排事件再改钟
    #    的话那些事件永远不到期。
    for a in ("_last_tick", "_start_tick", "_duration_ts0"):
        setattr(Clock, a, 0.0)
    Clock._last_fps_tick = None

    import danzhu.ui.app as appmod
    # 各模块命名空间里的 `time` 换成假钟(与 trace.py 同一条路)
    for name, mod in list(sys.modules.items()):
        if name.startswith("danzhu") and mod is not None and hasattr(mod, "time"):
            try:
                mod.time = ft
            except Exception:
                pass
    # 持久化引到一次性临时目录 —— 否则这次探针会读写真机存档
    tmp = tempfile.mkdtemp(prefix="danzhu_benchsmoke_")
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    for cls in (P.PlayMixin, B.BenchMixin):
        for nm in ("_config_path", "_history_path", "_bench_history_path",
                   "_hp_history_path"):
            if hasattr(cls, nm):
                setattr(cls, nm,
                        staticmethod(lambda _n=nm: os.path.join(tmp, _n + ".json")))

    app = appmod.PlinkoApp()
    app.build()
    root = app.rootw
    Window.size = (540, 960)
    Clock.unschedule(root._frame_timed)
    # 音效关掉: 这条探的是 UI 链, 不是音频(音频有 `audio_gates.py` 那 11 条)
    root.sfx.enabled = False
    return root, ft, Clock


def main():
    root, ft, Clock = _boot()

    rows = []

    def pump(n=8):
        for _ in range(n):
            ft.t += DT
            Clock.tick()
            root._frame(DT)

    def probe(name, fn, pump_n=3):
        try:
            fn()
            rows.append(("OK", name, ""))
        except Exception as e:
            rows.append(("FAIL", name, "%s: %s" % (type(e).__name__, e)))
            rows.append(("", "", traceback.format_exc().splitlines()[-3].strip()))
        pump(pump_n)

    # 先把界面推到稳态
    pump(5)

    import danzhu.ui.bench as B

    probe("弹性能测试菜单 _show_bench_menu", root._show_bench_menu)
    probe("启动信息 _show_startup_info", root._show_startup_info)
    probe("跑分历史 _show_bench_history", root._show_bench_history)
    probe("CPU高压历史 _show_hp_history", root._show_hp_history)
    probe("轮次设置 _show_round_settings", root._show_round_settings)
    probe("帧率上限设置 _show_fps_cap_settings", root._show_fps_cap_settings)
    probe("黑屏开 _show_bench_dim", root._show_bench_dim)
    probe("黑屏白字 _set_bench_msg", lambda: root._set_bench_msg("测试中…"))
    probe("黑屏关 _hide_bench_dim", root._hide_bench_dim)
    probe("跑分启动 _start_bench_test", root._start_bench_test, pump_n=30)
    probe("设备信息 _device_info", root._device_info)
    probe("构建信息 _build_info", root._build_info)
    probe("诊断正文 _bench_diag_text", lambda: root._bench_diag_text())
    probe("启动日志正文 _startup_log_text", lambda: root._startup_log_text())
    probe("电源日志正文 _power_log_text", lambda: root._power_log_text())
    probe("帧记录 _bench_frame_log", lambda: root._bench_frame_log())
    probe("成绩正文 _bench_low_summary_text", lambda: root._bench_low_summary_text())
    probe("跑分菜单简介 _bench_menu_desc", lambda: B._bench_menu_desc())

    bad = [r for r in rows if r[0] == "FAIL"]
    print("== 跑分链冒烟(18 个入口) ==")
    for tag, name, det in rows:
        if tag == "FAIL":
            print("  [FAIL] %s  %s" % (name, det))
        elif tag == "OK":
            print("  [OK  ] %s" % name)
        else:
            print("         %s" % det)
    n_ok = sum(1 for r in rows if r[0] == "OK")
    if bad:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(bad), n_ok))
        return 1
    print("门禁结果: 全绿 —— %d 个入口全部打开无异常" % n_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
