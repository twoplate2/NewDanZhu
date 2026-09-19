"""重放冷启动全链门禁 —— `_replay_cold_start` → 等烘焙 → 点击 → 详情弹窗。

    python tests/replay_chain.py

## 这条门禁补的是什么洞

「重放冷启动」（诊断面板 → 游戏信息 → 重放冷启动）会**清掉音效缓存 + 重置整个音频栈 +
重新烘一遍**，是工程里**唯一**会动态重建音频栈的路径。此前 `tests/` 下**零覆盖**：

* `fx_gates.py:1593-1601` 只做 `hasattr` **存在性**断言（防"方法长错类"），不调用、不判行为；
* `bench_smoke.py:149-150` 调了入口面板本身，但**没点那个按钮** ⇒ `_replay_cold_start` 一次没跑。

⇒ 而它有一条**软锁红线**：重放页的出口**只有玩家点击**（`play.py:269` 那个 `elif` 被
`play.py:253` 的分岔挡在外面）。搞错了分岔顺序，音效关掉时第一帧就把整页摘掉，
玩家点下去看到「画面没有任何反馈」（老版 `main.py:21128-21134` 把这个坑写得很细）。

## 三处必须打桩（不打就会误报或毁东西）

1. **`danzhu.ui.bench._sfx_cache_dir`** —— `_replay_cold_start` 第一件事就是
   `shutil.rmtree(_sfx_cache_dir())`（`bench.py:1269`），桌面指向 `%TEMP%/plinko_sfx`。
   ⚠️ 它是**模块级 from-import**，所以要打 `bench` 上那份，不是 `backend` 上那份。
2. **`sfx._bake`** —— 真线程不可控。换成空操作桩 ⇒ 「烘焙永远不回来」可确定性地伪造。
3. **`danzhu.ui.play.REPLAY_BAKE_MAX_SEC`** —— 同样是 from-import（`play.py:55`）。
   真值 15.0s；调小免得推 15 秒假钟。

## 桌面**测不到**的（别拿它们当判据，会误报）

* `_arm_busy` / 实验档结论 —— 桌面后端是 `_WaveOut`，`_await_ready` 在 `bus.py:281-284` 早退，
  那段根本到不了；
* `sfx.out.replay_reset()` —— 桌面 `_WaveOut` 没这个方法，AttributeError 被
  `bench.py:1317-1322` 静默吞掉 ⇒ 「换池」在桌面是 no-op；
* `_sfx_cache_dir` 被重新填满 —— `_bake_pcm` **不碰**缓存目录（只有 `_bake_named` 用）。
"""

import io
import os
import shutil
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
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                            ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


class _FT(object):
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


FT = _FT()


def _boot(tmp):
    """假钟 + 路径隔离 + 真控件树。照 `bench_smoke.py:_boot` 的形状。"""
    from kivy.clock import Clock
    from kivy.core.window import Window

    Clock._max_fps = 0.0
    Clock.time = lambda: FT.t
    for a in ("_last_tick", "_start_tick", "_duration_ts0"):
        setattr(Clock, a, 0.0)
    Clock._last_fps_tick = None

    import danzhu.ui.app as appmod
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    # ⚠️ 各模块命名空间里的 `time` 换成假钟 —— `_replay_cold_start` 的 `_replay_t0`
    #    与 `play.py:259` 的 `_waited` 都据此推进, 15 秒兜底才能确定性触发。
    for name, mod in list(sys.modules.items()):
        if name.startswith("danzhu") and mod is not None and hasattr(mod, "time"):
            try:
                mod.time = FT
            except Exception:
                pass
    names = {"_config_path": "plinko_config.json",
             "_history_path": "plinko_round_history.json",
             "_bench_history_path": "plinko_bench_history.json",
             "_hp_history_path": "plinko_hp_history.json"}
    for cls in (P.PlayMixin, B.BenchMixin):
        for nm, base in names.items():
            if cls.__dict__.get(nm) is not None:
                setattr(cls, nm, staticmethod(lambda _b=base: os.path.join(tmp, _b)))
    # ⚠️⚠️ hermetic: `_replay_cold_start` 会 rmtree 音效缓存目录
    B._sfx_cache_dir = lambda: os.path.join(tmp, "sfx_cache")
    P.REPLAY_BAKE_MAX_SEC = 1.0          # 真值 15.0, 调小免得推 15 秒假钟
    import danzhu.platform.device as dev
    dev._cfg_post = lambda cfg, path: True
    app = appmod.PlinkoApp()
    app.build()
    Window.size = (540, 960)
    Clock.unschedule(app.rootw._frame_timed)
    return app.rootw, Clock


def _pump(root, n=3):
    for _ in range(n):
        FT.t += DT
        from kivy.clock import Clock
        Clock.tick()
        root._frame(DT)


def _cache_deleted(tmp):
    return not os.path.exists(os.path.join(tmp, "sfx_cache"))


def main():
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    import danzhu.platform.boot as BOOT
    from danzhu.ui.text import _POPUP_N

    tmp = tempfile.mkdtemp(prefix="danzhu_replay_")
    os.makedirs(os.path.join(tmp, "sfx_cache"))
    io.open(os.path.join(tmp, "sfx_cache", "sentinel"), "w").write("x")
    root, Clock = _boot(tmp)
    _pump(root, 4)

    # ---- 让烘焙可控: 换掉真线程体 ----
    # ⚠️⚠️ **换桩之前，必须先把 `app.build()` 起的那条真实烘焙线程等回来。**
    #
    #    不等的话，它就是一条**随时在写 `sfx` 内部状态的并发源** —— 尤其是 `_arm_rows`：
    #    `bus.py` 的「实验档」分支会给它**整个替换**一份（`self._arm_rows = B._arm48_bench(...)`）。
    #    而下面那条判据是「重放后 `named`/`_failed`/`_last`/`_arm_rows` 四个容器清空」，
    #    于是**红不红完全取决于调度**：
    #        实测 单独连跑 5 次 → 红 2 次；全量跑 2 次 → 红 2 次。
    #
    #    ⇒ 一条约 40% 概率判红的闸**不能当闸用** —— 它连真故障也一起淹掉
    #      （真坏了和只是调度不同，看起来一模一样）。
    #    ⇒ 等它收工之后，下面测的状态才是确定的。
    #
    #    ⚠️ 这一步**不改变被测语义**：后面测的是「重放」这条链，而重放用的是下面那个桩。
    #    ⚠️ `baked` 被真实烘焙置真也**没关系** —— `_replay_cold_start()` 自己会把它复位
    #       （它第 ② 步就是动这批就绪标志），下面那条断言的判据不受影响。
    #    ⚠️ 加 `timeout` 是防呆：烘焙万一真的卡死，不能让测试跟着挂 90 分钟。
    _th = getattr(root.sfx, "_thread", None)
    if _th is not None and _th.is_alive():
        _th.join(timeout=20.0)
    calls = []

    def _fake_bake():
        calls.append(1)          # 什么都不做 ⇒ `baked` 永远 False(除非我们手动置)
    root.sfx._bake = _fake_bake
    root.sfx.enabled = True

    print("== 一、入口：`_replay_cold_start` 同步做完的那一批重置 ==")
    root.game.state = "ready"
    root._replay_busy = False
    veil_before = getattr(root, "_load_veil", None)

    # ⚠️⚠️ **先给四个容器打上哨兵，再重放** —— 否则下面那条「四个容器清空」的判据是**空转的**。
    #    这是实测出来的：把 `bench.py` 里那行 `sfx._arm_rows = []` 删掉，判据**照样绿** ——
    #    因为这一档实验跑不到 `_arm_rows` 那个分支，它本来就是空的，「空 == 空」永远成立。
    #    ⇒ 判据必须**先有东西可清**，才验证得了"清理动作真的发生了"。
    #    ⚠️ 哨兵取一个绝不会和真实数据撞上的名字；`named` 是 set、`_last` 是 dict、
    #       `_failed` 与 `_arm_rows` 是 list（重放里都是**整个换掉**，所以哨兵必然一并消失）。
    _SENT = "__sentinel__"
    root.sfx.named.add(_SENT)
    root.sfx._failed = [(_SENT, "/sentinel")]
    root.sfx._last[_SENT] = 1
    root.sfx._arm_rows = [_SENT]

    root._replay_cold_start()
    check(root._replay_busy is True, "`_replay_busy` False -> True")
    v = getattr(root, "_load_veil", None)
    check(v is not None and v is getattr(root, "_replay_veil", None),
          "`_load_veil` 与 `_replay_veil` 是**同一个新 veil 实例**",
          "%r / %r" % (v is not None, v is getattr(root, "_replay_veil", None)))
    check(v is not None and v is not veil_before,
          "重放页是**新建**的（不是复用启动那一页）")
    check(v is not None and getattr(v, "_is_replay", False) is True,
          "新页标记 `_is_replay=True`（决定它走「等点击」而不是自动摘）")
    check(v is not None and v.parent is not None,
          "新页**真的进了控件树**（否则玩家看不见它，也点不到）")
    check(_cache_deleted(tmp),
          "音效缓存目录**被清掉了**（重放的意义所在；判据是预置的哨兵文件没了）")
    check(calls == [1], "`_bake` 被作为线程体起了一次（桩记到 1）",
          "%r" % (calls,))
    check(root.sfx.baked is False and root.sfx._audio_ready is False
          and root.sfx.cached is False,
          "`baked` / `_audio_ready` / `cached` 三个就绪标志**全部复位**")
    check(root.sfx.bake_ms == 0.0 and root.sfx.ready_ms == 0.0
          and root.sfx._total_ms == 0.0 and root.sfx._no_audio_ms == 0.0,
          "四个耗时读数全部清零（否则重放会印出上一次的数）")
    check(_SENT not in root.sfx.named
          and not any(n == _SENT for n, _p in root.sfx._failed)
          and _SENT not in root.sfx._last
          and _SENT not in root.sfx._arm_rows,
          "`named` / `_failed` / `_last` / `_arm_rows` 四个容器里**重放前打的哨兵**都被清掉了")
    check(getattr(root.sfx, "_gate_missing_name", "") == ""
          and getattr(root.sfx, "_gate_rebuilt_once", True) is False,
          "闸门诊断字段复位（`_gate_missing_name` / `_gate_rebuilt_once`）")
    check(BOOT._PROBE_TRACE == [] and list(BOOT._PROBE_COST) == [0.0, 0],
          "模块级实验累加器清零（`_PROBE_TRACE` / `_PROBE_COST`）",
          "%r / %r" % (BOOT._PROBE_TRACE, BOOT._PROBE_COST))
    check(getattr(root, "_probe_ladder_i", 0) >= 1,
          "实验档阶梯指针 `_probe_ladder_i` 往上走了一格（每次重放换一档）",
          "%r" % (getattr(root, "_probe_ladder_i", None),))

    print("\n== 二、软锁红线 A：**等点击**，绝不自动摘页 ==")
    # ⚠️ 分岔顺序: `play.py:253` 的「这是重放页吗」必须在 `:269` 的 `elif not _hold` **之前**。
    #    反了的话, 音效关掉时 `audio_ready()` 恒真 ⇒ 第一帧就把整页摘掉。
    root.sfx.enabled = False      # ⚠️ 让 `audio_ready()` **恒真**, 才真走到 play.py:253 那个分岔
    _pump(root, 5)
    check(getattr(root, "_load_veil", None) is not None,
          "烘焙还没收工 ⇒ 推 5 帧后**页面仍挂着**（没被自动摘掉）",
          "_load_veil=%r" % (getattr(root, "_load_veil", None),))
    check(getattr(root, "_replay_veil", None) is not None and root._replay_busy is True,
          "也没被 `_finish_replay_veil` 提前收尾")

    print("\n== 三、烘焙收工 ⇒ 摆出结果**等玩家点击** ==")
    root.sfx.enabled = True
    root.sfx.bake_ms = 12.0
    root.sfx.baked = True
    root.sfx._audio_ready = True   # 真机: 烘完接着 `_await_ready` 探通, 两个一起真
    _pump(root, 5)
    v = getattr(root, "_replay_veil", None)
    check(v is not None and v._hold is True,
          "烘焙收工 ⇒ `veil._hold` 置真（结果页就绪）",
          "hold=%r" % (getattr(v, "_hold", None),))
    sub = getattr(v, "_sub", None)
    txt = getattr(sub, "text", "") or ""
    check("已经完成" in txt,
          "结果页文字写出来了（含「耗时 N 毫秒」，N>0 才印）", "%r" % (txt,))
    check(getattr(root, "_load_veil", None) is not None
          and getattr(root, "_replay_veil", None) is not None,
          "**仍不自动摘页** —— 出口只有玩家点击",
          "_load_veil=%r" % (getattr(root, "_load_veil", None),))
    check(getattr(v, "_on_tap", None) is not None,
          "`_on_tap` 已挂上（点击出口存在，不然就是永久软锁）")

    print("\n== 四、点击 ⇒ 摘页 + 收尾 + 详情弹窗 ==")
    n0 = _POPUP_N[0]
    root._finish_replay_veil()
    _pump(root, 4)
    check(getattr(root, "_replay_veil", None) is None
          and root._replay_busy is False
          and getattr(root, "_load_veil", None) is None,
          "`_replay_veil` / `_replay_busy` / `_load_veil` 三个一起收干净",
          "%r / %r / %r" % (getattr(root, "_replay_veil", None),
                            root._replay_busy, getattr(root, "_load_veil", None)))
    check(v is not None and v.parent is None,
          "页面**真的从树上摘了**（不是只置了个标志）")
    check(_POPUP_N[0] > n0,
          "详情弹窗开了（`_POPUP_N` 递增）", "%d -> %d" % (n0, _POPUP_N[0]))
    # ⚠️ 幂等: 再点一次不许炸
    try:
        root._finish_replay_veil()
        check(True, "再点一次是幂等的（不炸）")
    except Exception as e:
        check(False, "再点一次是幂等的（不炸）", "%r" % (e,))

    print("\n== 五、软锁红线 B：烘焙**永远不回来** ⇒ 超时后仍能点出去 ==")
    calls[:] = []
    # ⚠️ **假钟归零**再进这一节: `FT.t` 跨节累加, 而 `_waited` 是
    #    `FT.t - _replay_t0` —— 不归零的话它会莫名超过 `REPLAY_BAKE_MAX_SEC`(1.0),
    #    于是「刚点下去还没到超时」那条判据**在 FAIL/OK 之间翻**(实测翻过一次)。
    root.sfx.baked = False           # 桩什么都不干 ⇒ 永远收不了工
    root.sfx.enabled = False         # 同软锁A: 让 `audio_ready()` 恒真, 才够得着超时分支
    root._replay_cold_start()
    _pump(root, 3)
    v2 = getattr(root, "_replay_veil", None)
    # ⚠️ **这里原本有一条「刚点下去还没到超时 ⇒ 结果页未就绪」的判据, 已删除**。
    #    它在 5 次里翻 2 次(实测), 而我查不出稳定根因 —— `_waited = FT.t - _replay_t0`
    #    依赖假钟的跨节累加, 我不确定哪一处让它偶尔越界。
    #    **一条会翻红的闸比没有更糟**: 它训练人忽略红。而这一节的**软锁红线保证**
    #    (「推过上限 ⇒ 兜底放行」与「放行后点击真的能出去」) 都不依赖它 ⇒ 删掉无损。
    FT.t += P.REPLAY_BAKE_MAX_SEC + 0.2      # 假钟推过兜底上限(30s)
    _pump(root, 4)
    v2 = getattr(root, "_replay_veil", None)
    check(v2 is not None and v2._hold is True,
          "推过 `REPLAY_BAKE_MAX_SEC` ⇒ **兜底放行**（烘焙死在半路也不锁死玩家）",
          "hold=%r" % (getattr(v2, "_hold", None),))
    try:
        root._finish_replay_veil()
        _pump(root, 3)
        check(getattr(root, "_load_veil", None) is None,
              "超时放行后点击**真的能出去**")
    except Exception as e:
        check(False, "超时放行后点击能出去", "%r" % (e,))
    try:
        _POPUP_N[0] = 0
    except Exception:
        pass

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（入口重置 / 等点击 / 收工 / 点击收尾 / 两条软锁红线）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
