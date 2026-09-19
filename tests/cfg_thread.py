"""异步落盘线程门禁 —— `device.py` 那整套机构 + `root.py` 的接线。

    python tests/cfg_thread.py

## 这条门禁补的是什么洞

`tests/persist_check.py` 覆盖的是 **`Game._save_config` 的一侧**（键集合 / 读回 /
白名单 / 注入契约 / 同步兜底）。而 `danzhu/platform/device.py:704-778` 那一整套
**异步机构**——工作线程 / 单格合并 / 原子替换 / 写失败静默 / `_cfg_flush` / `_CFG_STAT`
——以及 `danzhu/ui/root.py:81` 那条把它接进 `Game` 的 **lambda 转发**，
在**所有门禁里跑过零次**：

* `tests/trace.py:380`、`tests/trace.py:603`、`tests/mute_chain.py:129` 全都
  `_cfg_post = lambda cfg, path: True` 把它**整链打桩关掉**（为的是轨迹可复现）；
* `tests/persist_check.py:158-160` 的「异步」那一半是个**假函数**，只记调用、不落盘；
* 真跑过一次的只有 `temp/selfcheck_platform.py`，那是临时脚本不是门禁。

⇒ 而这条链正是**"进度清零"事故的第一现场**：写坏了没人红，玩家下次启动发现
余额回到出厂。

⚠️ 本脚本**不碰真实存档**：`device._cfg_post(cfg, path)` 的 path 全靠调用方传，
所以只测 device 层时随手给个临时目录即可。⛔ **不要**照抄
`temp/selfcheck_platform.py:110` 的 `tempfile.gettempdir()+"plinko_config.json"`
—— 那**就是桌面版的真实存档路径**，实测里面躺着真进度。

## 两条踩过的坑（写这条门禁时真踩的）

1. **装可控 Event 必须赶在 `_cfg_warm` 第一次执行之前**，且**先置 `_CFG_OK[0] = True`**。
   `_cfg_warm`（`device.py:749-750`）见到 `_CFG_OK[0]` 非 None 就早退，
   顺序反了它会把 `_CFG_EVT` 覆盖回真 Event。
2. **`_cfg_flush` 不是完成信号**（见第五节）—— 等它等于没等。
   唯一在**写之后**发生的可观测事件是 `_CFG_STAT[2]`（`device.py:742`）。
"""

import json
import os
import sys
import tempfile
import threading
import time
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


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _reset(dev, ok=None):
    """把 4 个模块全局拨回初始态。"""
    dev._CFG_PENDING[0] = None
    dev._CFG_OK[0] = ok
    dev._CFG_STAT[0] = 0.0
    dev._CFG_STAT[1] = 0.0
    dev._CFG_STAT[2] = 0


class _StubEvt(object):
    """替掉真 Event：`set()` 只记账**不**唤醒谁 —— 用来把工作线程挡在门外。"""

    def __init__(self):
        self.sets = 0
        self.clears = 0

    def set(self):
        self.sets += 1

    def clear(self):
        self.clears += 1

    def wait(self, *_a):
        raise AssertionError("本门禁**不该**有线程 wait 到这个桩上")


def _block_worker(dev):
    """挡死工作线程：先 `_CFG_OK[0]=True`（让 `_cfg_warm` 早退，不覆盖 `_CFG_EVT`），

    再装桩 Event。返回桩，供断言"投递确实 set 过"。
    ⚠️ 顺序不能反 —— 反了 `_cfg_warm` 会把 `_CFG_EVT` 覆盖回真 Event。
    """
    _reset(dev, ok=True)
    ev = _StubEvt()
    dev._CFG_EVT = ev
    return ev


def _release(dev):
    """放行：起一条真 worker 线程，把当前槽位落盘。返回该线程。"""
    ev = threading.Event()
    dev._CFG_EVT = ev
    th = threading.Thread(target=dev._cfg_worker, daemon=True)
    th.start()
    ev.set()
    return th


def _wait_writes(dev, n0, timeout=3.0):
    """等 `_CFG_STAT[2]` 涨过 n0 —— 唯一在**写之后**发生的可观测事件。"""
    t0 = time.perf_counter()
    while dev._CFG_STAT[2] <= n0 and time.perf_counter() - t0 < timeout:
        time.sleep(0.005)
    return dev._CFG_STAT[2] - n0


def main():
    import danzhu.platform.device as dev

    tmp = tempfile.mkdtemp(prefix="danzhu_cfgthread_")

    # ⚠️ 进来先看一眼：万一有别的东西已经把真线程起起来了，本脚本的"挡门"就不成立。
    live = [t for t in threading.enumerate()
            if getattr(t, "_target", None) is dev._cfg_worker]
    check(not live, "起点干净: 还没有 worker 线程在跑（否则下面的`挡门`是假的）",
          "已有 %d 条" % len(live))

    # ---------------------------------------------------------------- 一
    print("== 一、单格合并: 连投两次, 前一份必须被丢掉 ==")
    ev = _block_worker(dev)
    p = os.path.join(tmp, "merge.json")
    okA = dev._cfg_post({"bet": 10}, p)
    okB = dev._cfg_post({"bet": 100}, p)
    check(okA and okB, "两次投递都返 True（契约: True = **已投递**, 不是已落盘）",
          "%s / %s" % (okA, okB))
    check(dev._CFG_PENDING[0] is not None and dev._CFG_PENDING[0][0] == {"bet": 100},
          "槽位里是**后一份**(前一份被覆盖丢弃 —— 没有队列, 见 device.py:708-709)",
          "%r" % (dev._CFG_PENDING[0],))
    check(ev.sets == 2, "两次都 set 了 Event", "sets=%d" % ev.sets)
    # ⚠️ 关键阴性对照: 上面两个 True **什么都没保证**
    check(not os.path.exists(p),
          "**返 True ≠ 已写盘**（工作线程被挡着时, 盘上一个字节都没有）")
    check(dev._CFG_STAT[2] == 0, "worker 没跑过 ⇒ 计数仍为 0")

    # ---------------------------------------------------------------- 二
    print("\n== 二、放行: 落盘的是后一份, 且 `.tmp` 不残留 ==")
    _release(dev)
    n = _wait_writes(dev, 0)
    check(n == 1, "worker 落了一次盘", "_CFG_STAT[2]=%d" % dev._CFG_STAT[2])
    check(os.path.exists(p) and _read(p) == {"bet": 100},
          "盘上内容是**后一份**(前一份从未落盘)", "%r" % (_read(p) if os.path.exists(p) else None))
    check(not os.path.exists(p + ".tmp"), "`os.replace` 收走了临时文件, `.tmp` 不残留")
    check(dev._CFG_STAT[0] > 0.0 and dev._CFG_STAT[1] > 0.0,
          "`_CFG_STAT` 三个格子都填了(累计秒/单次最慢/次数)",
          "%.4f / %.4f / %d" % (dev._CFG_STAT[0], dev._CFG_STAT[1], dev._CFG_STAT[2]))

    # ---------------------------------------------------------------- 三
    print("\n== 三、原子写: 写失败时**原档一字不能变**（正向证据） ==")
    # 在 `path + ".tmp"` 位置**预建一个同名目录** ⇒ `open(tmp,"w")` 必抛 ⇒ 被静默吞掉。
    # 这比 monkeypatch `device.json` 安全 —— 那会污染 stdlib 全局。
    _block_worker(dev)
    p3 = os.path.join(tmp, "atomic.json")
    with open(p3, "w", encoding="utf-8") as f:
        json.dump({"bet": 10, "balance": 4321}, f)
    os.mkdir(p3 + ".tmp")            # 占住 tmp 这个位置
    try:
        dev._cfg_post({"bet": 100, "balance": 0}, p3)
        _release(dev)
        n3 = _wait_writes(dev, 0)
        threw = None
    except Exception as e:
        n3, threw = -1, e
    check(threw is None, "写盘失败**不抛**（异常被 device.py:738-739 吞掉）",
          "" if threw is None else repr(threw))
    check(os.path.isdir(p3 + ".tmp"), "tmp 那个目录还占着(说明确实没写成功)")
    check(_read(p3) == {"bet": 10, "balance": 4321},
          "**原档一字未变** —— 这正是老写法 `open(path,'w')` 做不到的(它先截断)",
          "%r" % (_read(p3),))
    check(n3 == 1, "写**失败**也计一次数（`_CFG_STAT[2]` 在 try 之外, device.py:742）",
          "涨了 %d" % n3)
    os.rmdir(p3 + ".tmp")

    # ---------------------------------------------------------------- 四
    print("\n== 四、降级支: `_CFG_OK[0]=False` ⇒ 主线程同步兜底 ==")
    _reset(dev, ok=None)
    dev._CFG_OK[0] = False
    p4 = os.path.join(tmp, "degrade.json")
    check(dev._cfg_post({"bet": 100}, p4) is False,
          "`_CFG_OK[0]=False` ⇒ `_cfg_post` 返 False（device.py:762-763）")
    check(not os.path.exists(p4), "`_cfg_post` 自己**不写盘**(它只负责投递)")
    # 用**真** `_cfg_post`（不是 lambda）驱动真 `Game._save_config` 走兜底
    from danzhu.game import Game
    import danzhu.platform.device as _d
    g = Game(config_path=(lambda: p4), history_path=None,
             save_config=lambda c, pp: _d._cfg_post(c, pp))
    g.bet = 100
    g.balance = 4321
    g._save_config()
    check(os.path.exists(p4) and _read(p4).get("bet") == 100,
          "返 False ⇒ `Game._save_config` 真的落了同步写（game.py:1529-1534）",
          "%r" % (_read(p4).get("bet") if os.path.exists(p4) else None))

    # ---------------------------------------------------------------- 五
    print("\n== 五、`_cfg_flush` 的**已知口径**：它等的不是写完 ==")
    # ⚠️⚠️ 这是**记录现状**，不是"应该如此"。两版逐行同构(device.py:772-778 ↔
    #    android/main.py:11235-11241)，所以**不能改** —— 改了 `on_pause` 的等待时长
    #    就变了，偏离 1:1。但它与 `device.py:713` 那句注释「保证切走时一定落了盘」
    #    **不符**，写在这里免得下次有人拿 flush 当完成信号。
    _block_worker(dev)
    p5 = os.path.join(tmp, "flush.json")
    dev._cfg_post({"bet": 10}, p5)
    dev._CFG_PENDING[0] = None        # 模拟 worker 在 device.py:728 取走槽位
    t0 = time.perf_counter()
    dev._cfg_flush(timeout=1.0)
    dt = time.perf_counter() - t0
    check(dt < 0.2, "取走槽位后 flush **立即返回**(远早于 timeout=1.0s)",
          "%.3fs" % dt)
    check(not os.path.exists(p5) and dev._CFG_STAT[2] == 0,
          "**flush 返回了, 盘上什么都没有** —— 它的循环条件是「槽位非空」"
          "(device.py:777), 而 worker 在**写之前**就先清空了槽位(device.py:728)",
          "文件在=%s 写入计数=%d" % (os.path.exists(p5), dev._CFG_STAT[2]))

    # ---------------------------------------------------------------- 六
    print("\n== 六、`_cfg_warm` 幂等 + 给 `_CFG_OK` 定态 ==")
    _reset(dev, ok=None)
    dev._CFG_EVT = None

    def _workers():
        return [t for t in threading.enumerate()
                if getattr(t, "_target", None) is dev._cfg_worker]
    # ⚠️ 取**基线**再比增量 —— 上面几节 `_release()` 起的线程是 daemon、放着不管的,
    #    直接数绝对值会把它们算进来(第一版就是这么写的, 报了个假红 3 -> 3)。
    base_n = len(_workers())
    dev._cfg_warm()
    after1 = len(_workers())
    dev._cfg_warm()
    dev._cfg_warm()
    check(after1 - base_n == 1 and len(_workers()) - base_n == 1,
          "连调三次 `_cfg_warm` 只起**一条**线程（device.py:749-750 幂等早退）",
          "基线 %d, 一次后 %d, 三次后 %d" % (base_n, after1, len(_workers())))
    check(dev._CFG_OK[0] is True and dev._CFG_EVT is not None,
          "预热后 `_CFG_OK[0] is True` 且 `_CFG_EVT` 非 None",
          "OK=%r" % (dev._CFG_OK[0],))
    th = _workers()[0]
    check(getattr(th, "daemon", False),
          "worker 是 **daemon**（不挡进程退出）", "daemon=%r" % getattr(th, "daemon", None))

    # ---------------------------------------------------------------- 七
    print("\n== 七、端到端: `root.py:81` 那条 lambda 转发真的通 ==")
    # 这是**唯一**能抓住接线断掉的测试 —— persist_check 直接构造 `Game`, 绕开了 RootWidget。
    # ⚠️ 这里**故意不打 `_dev._cfg_post` 的桩**(别的门禁都打), 让落盘真走工作线程。
    _reset(dev, ok=None)
    dev._CFG_EVT = None
    _boot_app(tmp)
    # 先验：四个路径函数的**真文件名**（这条关的是"写错一个字母全绿"）
    badnames = {k: (v, _REAL_NAMES.get(k)) for k, v in _EXPECT_NAMES.items()
                if _REAL_NAMES.get(k) != v}
    check(not badnames,
          "四个存档文件名**逐字**对得上（先问真函数再改目录，不是拿桩名糊弄）",
          "" if not badnames else "期望/实得 %r" % (badnames,))
    import danzhu.platform.device as d2
    n0 = d2._CFG_STAT[2]              # ⚠️ 必须先取 n0 —— `build()` 期间已经写过几次
    root = _APP[0].rootw
    root.game.set_bet(50)
    grew = _wait_writes(d2, n0)
    cfgp = os.path.join(tmp, _EXPECT_NAMES["_config_path"])
    check(grew >= 1,
          "切投注后 `_CFG_STAT[2]` 涨了 ⇒ **真的走了工作线程**"
          "(同步兜底**不碰**这个计数, 所以它是接线通的铁证)",
          "%d -> %d" % (n0, d2._CFG_STAT[2]))
    got = _read(cfgp).get("bet") if os.path.exists(cfgp) else None
    check(got == 50, "**临时目录里**的存档落到了 bet=50",
          "%s ⇒ bet=%r" % (cfgp, got))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（合并 / 原子写 / 降级 / flush口径 / 幂等 / 端到端接线）"
          % (len(FAIL) + N_OK[0]))
    return 0


_APP = [None]
_REAL_NAMES = {}

# 四个路径函数的**真文件名**字面量。⚠️ 写门禁时最容易漏的就是这个 —— 现有门禁
# (`tests/mute_chain.py:128`、`tests/bench_smoke.py:112`)把桩写成 `_n + ".json"`,
# 产出的是 `_config_path.json` 这种**桩名**, 于是真字面量 `plinko_config.json`
# **在所有测试里从没被跑过** —— 写错一个字母, 门禁全绿而玩家进度读不回来。
_EXPECT_NAMES = {
    "_config_path": "plinko_config.json",
    "_history_path": "plinko_round_history.json",
    "_bench_history_path": "plinko_bench_history.json",
    "_hp_history_path": "plinko_hp_history.json",
}


class _FakeTime(object):
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


def _redirect_paths(tmp):
    """把 4 个路径函数重定向到 tmp，但**保留真文件名**。

    先问一次**还没被打桩的**原函数拿到真路径，再只把目录换成 tmp ——
    这样真文件名字面量就真的被跑到了（上面 `_EXPECT_NAMES` 就是判据）。
    """
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    for cls in (P.PlayMixin, B.BenchMixin):
        for nm in _EXPECT_NAMES:
            raw = cls.__dict__.get(nm)
            if raw is None:
                continue
            # `_hp_history_path` 是**普通方法**(bench.py:5091), 另外三个是 staticmethod
            fn = raw.__func__ if isinstance(raw, staticmethod) else raw
            real = fn() if isinstance(raw, staticmethod) else fn(None)
            base = os.path.basename(real)
            _REAL_NAMES[nm] = base
            setattr(cls, nm, staticmethod(lambda _b=base: os.path.join(tmp, _b)))


def _boot_app(tmp):
    """起一个真 `PlinkoApp`，**不打 `_cfg_post` 桩**，只把 4 个路径引到临时目录。"""
    from kivy.clock import Clock
    from kivy.core.window import Window

    ft = _FakeTime()
    Clock._max_fps = 0.0
    Clock.time = lambda: ft.t
    for a in ("_last_tick", "_start_tick", "_duration_ts0"):
        setattr(Clock, a, 0.0)
    Clock._last_fps_tick = None

    import danzhu.ui.app as appmod
    _redirect_paths(tmp)
    app = appmod.PlinkoApp()
    app.build()
    Window.size = (540, 960)
    Clock.unschedule(app.rootw._frame_timed)
    _APP[0] = app


if __name__ == "__main__":
    sys.exit(main())
