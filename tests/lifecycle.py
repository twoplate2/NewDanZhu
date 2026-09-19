"""生命周期回调门禁 —— `on_pause` / `on_resume` / `on_stop` 端到端。

    python tests/lifecycle.py

## 这条门禁补的是什么洞

三个回调是**安卓上切后台/回前台/退出**的唯一入口（`app.py:273/287/301` ↔ 老版
`main.py:22108/22121/22135`），而 `tests/` 下**零覆盖** —— 全仓 grep
`.on_pause()` / `.on_resume()` / `.on_stop()` 的**驱动调用点 = 0 命中**
（`cfg_thread.py:226` 只是注释）。改动这三处（少调一次 `_cfg_flush`、把
`resume_out` 挪错位）不会让任何门禁变红。

## 两条本工程踩过的坑，这里都当成判据

⚠️ **1. `_cfg_flush` 能打桩、`_apply_fps_cap` 打不动 —— 这个不对称是真的**
  `on_pause` 里是 `from ..platform.device import _cfg_flush`（**函数内延迟 import**，
  调用时才取模块属性）⇒ 打桩 `device._cfg_flush` **生效**；
  而 `app.py:24` 的 `_apply_fps_cap` 是**模块级 from-import**（绑的是拷贝）⇒
  打桩 `device._apply_fps_cap` **无效**，必须打 `appmod._apply_fps_cap`。
  这正是 `config.SOUND_ENABLED` / `device._cfg_post` 那个坑的同款形状 ——
  本工程已经在它上面栽过两次。两条方向都断言。

⚠️ **2. 假钟会让 `_cfg_flush` 变成死循环**
  `bench_smoke.py:97-102` 那种"把 `danzhu.*` 各模块的 `time` 全换成假钟"的装法，
  会让 `device.py:777` 的循环条件 `perf_counter() - _t0 < timeout` 恒为 `0 < 1.0`
  ⇒ 只要槽位非空就**永不退出**。所以本脚本**不换 `device.time`**（与
  `tests/cfg_thread.py:_boot_app` 同口径），且 `_cfg_flush` 一律打桩。

## 判据

不打桩源码、不查字符串 —— 全取**跨层副作用**：方法被调了几次、`sfx` 的真状态、
`Clock` 的待触发集合、返回值。
"""

import os
import sys
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

FAIL = []
N_OK = [0]
CALLS = {}


def check(ok, name, detail=""):
    if ok:
        N_OK[0] += 1
    else:
        FAIL.append(name)
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                            ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def _rec(name):
    def _f(*a, **kw):
        CALLS.setdefault(name, []).append((a, kw))
        return True
    return _f


class _FakeClockTime(object):
    """只给 `kivy.clock` 用的假钟 —— ⚠️ **绝不换 `device.time`**（见模块 docstring 坑 2）。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def perf_counter(self):
        return self.t

    def monotonic(self):
        return self.t

    def strftime(self, *a):
        import time as _t
        return _t.strftime(*a)

    def strptime(self, *a):
        import time as _t
        return _t.strptime(*a)

    def localtime(self, *a):
        import time as _t
        return _t.localtime(*a)


_APP = [None]


def _boot(tmp):
    from kivy.clock import Clock
    from kivy.core.window import Window

    Clock._max_fps = 0.0
    for a in ("_last_tick", "_start_tick", "_duration_ts0"):
        setattr(Clock, a, 0.0)
    Clock._last_fps_tick = None

    import danzhu.ui.app as appmod
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    # 四路径引到临时目录(**保留真文件名**) —— 否则 `on_pause` 那条路会读写真实存档
    names = {"_config_path": "plinko_config.json",
             "_history_path": "plinko_round_history.json",
             "_bench_history_path": "plinko_bench_history.json",
             "_hp_history_path": "plinko_hp_history.json"}
    for cls in (P.PlayMixin, B.BenchMixin):
        for nm, base in names.items():
            if cls.__dict__.get(nm) is not None:
                setattr(cls, nm,
                        staticmethod(lambda _b=base: os.path.join(tmp, _b)))
    app = appmod.PlinkoApp()
    app.build()
    Window.size = (540, 960)
    Clock.unschedule(app.rootw._frame_timed)
    _APP[0] = app
    return app, appmod, Clock


def main():
    import tempfile
    import danzhu.platform.device as dev

    tmp = tempfile.mkdtemp(prefix="danzhu_lifecycle_")
    app, appmod, Clock = _boot(tmp)
    root = app.rootw

    print("== 一、`on_pause`：暂停音频 + flush 落盘 + 返回 True ==")
    # 桌面上 `Sfx.pause_out()` 是**真 no-op**(桌面后端没有 `pause`), 不打桩就什么都看不见
    root.sfx.pause_out = _rec("pause_out")
    _real_flush = dev._cfg_flush
    dev._cfg_flush = _rec("cfg_flush")       # ✅ 函数内延迟 import ⇒ 打得动
    r = app.on_pause()
    check(CALLS.get("pause_out") == [((), {})],
          "`sfx.pause_out()` **被调了一次**（桌面上它自己无副作用，必须打桩才看得见）",
          "%r" % (CALLS.get("pause_out"),))
    check(CALLS.get("cfg_flush") == [((), {})],
          "`_cfg_flush()` **被调了一次、且零参**（延迟 import ⇒ 打桩生效）",
          "%r" % (CALLS.get("cfg_flush"),))
    check(r is True,
          "`on_pause` 返回 **True** —— kivy 收到假值会转 `on_stop`（保 GL 上下文）",
          "%r" % (r,))

    print("\n== 二、`on_resume`：桌面跳过 android 块 / android 上必须全调 ==")
    CALLS.clear()
    root.sfx.resume_out = _rec("resume_out")
    appmod._apply_fps_cap = _rec("apply_fps_cap")     # ⚠️ 必须打 appmod 上的那份
    app._apply_orientation = _rec("apply_orientation")
    app._enter_immersive = _rec("enter_immersive")
    r = app.on_resume()
    check(CALLS.get("resume_out") == [((), {})], "`sfx.resume_out()` **被调了一次**")
    check(not CALLS.get("apply_fps_cap") and not CALLS.get("apply_orientation")
          and not CALLS.get("enter_immersive"),
          "桌面（`platform != 'android'`）⇒ 三个安卓专属调用**一个都不许进**",
          "%r" % (sorted(CALLS),))
    check(r is True, "`on_resume` 返回 True")

    # ---- 把 platform 掰成 android, 三个调用必须全到位 ----
    CALLS.clear()
    _real_plat = appmod.platform
    appmod.platform = "android"
    try:
        app.on_resume()
    finally:
        appmod.platform = _real_plat
    got = sorted(k for k in CALLS if k != "resume_out")
    check(got == ["apply_fps_cap", "apply_orientation", "enter_immersive"],
          "掰成 android ⇒ 三个调用**全到位且次序对**"
          "（少一个 = 回前台后帧率/方向/沉浸静默失效）",
          "实得 %r" % (got,))

    print("\n== 三、`_apply_fps_cap` 的**打桩不对称**（本工程踩过两次的形状） ==")
    CALLS.clear()
    _real_dev_cap = dev._apply_fps_cap
    dev._apply_fps_cap = _rec("device_apply_fps_cap")
    appmod.platform = "android"
    try:
        app.on_resume()
    finally:
        appmod.platform = _real_plat
        dev._apply_fps_cap = _real_dev_cap
    check(not CALLS.get("device_apply_fps_cap"),
          "打桩 `device._apply_fps_cap` **不生效** —— `app.py` 是**模块级 from-import**"
          "（绑的是拷贝）",
          "被调的其实是 appmod 上那份")
    check("apply_fps_cap" in CALLS,
          "（对照）打桩 `appmod._apply_fps_cap` 才生效 —— 而 `_cfg_flush` 恰好相反"
          "（延迟 import）")

    print("\n== 四、`on_stop`：关音频 + 撤预烘定时器（**真副作用**，不用打桩） ==")
    # ⚠️ 判据不能去数 `Clock._events` —— 第一版就是这么写的, 而 Kivy 2.x 的事件不落在
    #    那个桶里(实测 `0 -> 0`), 于是 `pre not in after` 恒真 ⇒ **空转的假绿**。
    #    改成**盯 `Clock.unschedule` 真被喂了什么**, 那是跨层可观测的调用事实。
    pre = root.game_area.win_fx.prebake_step
    _real_unsched = Clock.unschedule
    seen = []

    def _spy_unsched(cb=None, *a):
        seen.append(cb)
        return _real_unsched(cb, *a)
    Clock.unschedule = _spy_unsched
    try:
        Clock.schedule_once(pre, 0.1)
        r = app.on_stop()
    finally:
        Clock.unschedule = _real_unsched
    check(root.sfx.out is None and root.sfx.enabled is False,
          "`sfx.close()` 真的执行了（`out=None` / `enabled=False`）",
          "out=%r enabled=%r" % (root.sfx.out, root.sfx.enabled))
    want = type(root.game_area.win_fx).prebake_step
    hit = [c for c in seen
           if getattr(c, "__func__", c) is want or c is pre]
    check(bool(hit),
          "`Clock.unschedule` 真被喂了 `win_fx.prebake_step`（不是靠数内部桶）",
          "撤了 %d 个回调; 命中的是同一个函数: %s" % (len(seen), bool(hit)))
    check(r is True, "`on_stop` 返回 True")

    dev._cfg_flush = _real_flush
    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（on_pause / on_resume / on_stop / 打桩不对称）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
