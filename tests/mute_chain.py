"""音效按钮链**端到端**门禁 —— 防「按钮写着『音效已关』，声音却照响」。

    python tests/mute_chain.py

## 这条门禁补的是什么洞

老版 `toggle_mute` 是**同步**的（`android/main.py:19761-19763` 当场
`self.sfx.set_enabled(...)`）。新版拆成三层，中间隔着一个事件：

    Game.toggle_mute()            danzhu/game.py:1408   逻辑层 —— 只翻 `sound_mode` + 发事件
      └─ _emit("sfx_enabled")     danzhu/game.py:1435
    PlayMixin._dispatch()         danzhu/ui/play.py:435  界面层 —— 认这个事件
      └─ self.sfx.set_enabled()   danzhu/ui/play.py:436  音效层 —— 真的关掉

**三环任一断掉都是同一个病**：按钮文字跟着 `sound_mode` 变了（写着「音效已关」），
而 `Sfx.enabled` 没动 ⇒ **声音照响**。三个断点都只是"删一行"级的改动，而现有门禁
一条都碰不到这条链（`fx_gates` 对它只有一条「函数体里不许出现 `background_color=`」
的静态扫描）。

⇒ 所以这条**不查源码、只查结果**：真的点一下，看音效层变没变。

⚠️ 用**假钟 + 真控件树**（与 `tests/bench_smoke.py` 同款 `_boot`）—— 判据是
`root.sfx.enabled` 这个**跨层副作用**，不是我们自己转述的事件名。
"""

import os
import sys
import tempfile
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
FAIL = []
N_OK = [0]


def check(ok, name, detail=""):
    if ok:
        N_OK[0] += 1
    else:
        FAIL.append(name)
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name, ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


class _FakeTime(object):
    """与 `tests/trace.py` / `bench_smoke.py` 同款的假钟。"""

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
    # ⚠️ 顺序不能反: `schedule_once` 当场把 timeout 折进 `_last_tick`。
    for a in ("_last_tick", "_start_tick", "_duration_ts0"):
        setattr(Clock, a, 0.0)
    Clock._last_fps_tick = None

    import danzhu.ui.app as appmod
    for name, mod in list(sys.modules.items()):
        if name.startswith("danzhu") and mod is not None and hasattr(mod, "time"):
            try:
                mod.time = ft
            except Exception:
                pass
    # 持久化引到一次性临时目录 —— 否则会读写真实存档(这条链的末尾有 _save_config)
    tmp = tempfile.mkdtemp(prefix="danzhu_mutechain_")
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    import danzhu.platform.device as _dev
    for cls in (P.PlayMixin, B.BenchMixin):
        for nm in ("_config_path", "_history_path", "_bench_history_path",
                   "_hp_history_path"):
            if hasattr(cls, nm):
                setattr(cls, nm,
                        staticmethod(lambda _n=nm: os.path.join(tmp, _n + ".json")))
    _dev._cfg_post = lambda cfg, path: True

    app = appmod.PlinkoApp()
    app.build()
    root = app.rootw
    Window.size = (540, 960)
    Clock.unschedule(root._frame_timed)
    return root, ft, Clock


def _tick(root, ft, Clock, n):
    for _ in range(n):
        ft.t += DT
        Clock.tick()
        root._frame(DT)


def main():
    root, ft, Clock = _boot()
    # ❗ 这里**不能**像 bench_smoke 那样把音效关掉 —— 要测的就是"关"这个动作本身。
    root.sfx.enabled = True
    _tick(root, ft, Clock, 3)

    print("== 音效按钮链: 点一下 -> 音效层真的变了吗 ==")

    # ---- 第 1 次点: 开 -> 关 ----
    before = root.sfx.enabled
    root.toggle_mute()                 # PlayMixin 转发 -> Game.toggle_mute -> 事件
    _tick(root, ft, Clock, 6)
    after = root.sfx.enabled
    check(before is True and after is False,
          "点一下: **音效层真的关了**(不是只改了按钮文字)",
          "sfx.enabled %s -> %s" % (before, after))
    check(getattr(root.game, "sound_mode", None) == "off",
          "sound_mode 同时翻到 off(按钮文字那一半)",
          "sound_mode=%r" % getattr(root.game, "sound_mode", None))
    check(getattr(root.game, "sfx_enabled", None) is False,
          "逻辑层**自己那份** sfx_enabled 也翻了(它挡在抽撞钉变体之前, 不翻会让 _ARNG 漂)")
    btn = getattr(root, "mute_btn", None) or getattr(root, "sound_btn", None)
    if btn is not None:
        check("已关" in (getattr(btn, "text", "") or ""),
              "按钮文字写着「音效已关」", "text=%r" % getattr(btn, "text", None))

    # ---- 第 2 次点: 关 -> 开 ----
    root.toggle_mute()
    _tick(root, ft, Clock, 6)
    check(root.sfx.enabled is True,
          "再点一下: **音效层真的开了**",
          "sfx.enabled %s -> %s" % (after, root.sfx.enabled))
    check(getattr(root.game, "sound_mode", None) == "on",
          "sound_mode 翻回 on", "sound_mode=%r" % getattr(root.game, "sound_mode", None))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— 音效按钮的三个断点都活着(发事件 / 认事件 / 真的关掉)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
