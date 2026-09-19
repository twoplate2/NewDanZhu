"""脚本化输入 -> 行为轨迹录制台(老版 / 重构版通用)。

给定同一串脚本化输入(按住 N 帧 -> 松手 / 切档 / 点击), 产出一串逐帧可观测轨迹;
老版那一条冻结成 `tests/golden/trace_old.json`, 新版同一个脚本再跑一遍, **逐项对比**。

    python tests/trace.py --record                    # 录老版, 写 golden, 打印 sha1
    python tests/trace.py --which new --record        # 录新版(写 trace_new.json)
    python tests/trace.py --verify                    # 老版: 与 golden 逐字节比
    python tests/trace.py --which new --verify        # 新版: **跨版本对账(门禁)** + 可复现

## `--verify` 比的是什么(2026-09-19 修, 别改回去)

两种对账, 都是门禁, 但证明力完全不同:

  ① **跨版本对账**: 本次跑出来的轨迹 vs `trace_old.json`(老驱动录的冻结基线)。
     比法 = 逐字节, **只忽略 `meta.driver`**(它按定义就是 "old"/"new")。
     ⚠️⚠️ 这是**唯一**能证明"重构版与老版逐位一致"的口径, 也正是本文件抬头承诺的那一条。
  ② **可复现对账**: vs 本驱动上一次录下来的那份(`trace_new.json`)。只证明"同种子两遍
     一样"(确定性), **不证明与老版一致**。

  ⚠️⚠️ 曾经只有 ② 而没有 ①: `--which new --verify` 比的是**它自己上一次的录制** ⇒
     同一份 golden 重录一遍就能"制造绿", 而真正的 old→new 漂移从这个口径里漏过去 ——
     实测漏掉过一次 5159 帧里 **1320 帧不同 / 少 15 声落球音**的运行, 还被当成
     `All verification is green` 报出去。这是本工程最忌的「假绿」, 所以 ① 必须在,
     且**红就返回非零**。`--record --which new` 也会顺手打一遍 ① 的结论(只出声, 不改
     返回码 —— 录文件是它的职责), 免得"重录一遍"这条路悄悄把漂移固化下来。

## 老版为什么能被"真窗口 + 不跑事件循环"驱动

老版 `PlinkoApp.build()` 只搭控件树, 不进入 Kivy 主循环; `RootWidget.__init__` 会把
`_frame_timed` 挂到 `Clock.schedule_interval`。所以这里把那一挂**撤掉**, 自己按固定 dt 调
`root._frame(dt)`, 同时每帧手动 `Clock.tick()` 让 `schedule_once` 排的那些游戏内回调
(装杯起播 `_start_cup` 等)按虚拟时间落地。

## 两处不确定性怎么消掉的

* **墙钟**: 老模块顶部 `import time` 把 stdlib time 绑进了它自己的全局命名空间, 于是
  把 `m.time` 换成假钟就够了 —— stdlib 与 Kivy 内部都不受影响。Kivy 的 `Clock` 另外
  单点改写(`Clock.time` + `_last_tick` 归零 + `_max_fps=0` 防真睡)。
  ⚠️ `Clock.time` 必须在**任何 schedule 之前**改 —— `schedule_once` 那一刻就把
  `timeout` 折成了 `_last_tick` 的绝对值, 改晚了排进去的事件永远不到期(实测)。
* **全局 random**: 建界面前 `random.seed(SEED)`。老版盘面/物理都走全局流, 所以这一条
  就把它钉死了。
  ⚠️ **光 seed 全局流不够** —— 老版另有一处 `self._rng = random.Random()`
  (`WinPileFX.__init__`, 装杯每颗球的下落/回弹/自转参数), 无参构造取的是 **OS 熵**,
  与全局流无关。不处理的话两遍轨迹在"装杯"那一段必然分叉(实测首处分叉在第 846 帧的
  `fx` 字段上)。这里把老模块命名空间里的 `random` 换成 `_RandomProxy`, 只改 `Random`
  一个属性(见 `DetRandom`)。

## 已知不做的事(如实记)

* 音效录的是**发出**的那一声(过了 enabled/已加载/增益/节流/语音互斥五道闸之后),
  不是"调用点"。所以关掉音效的路径、被节流吃掉的连击都不在录 —— 那正是要验的东西。
* 持久化(config / history / bench history)被引到一次性临时目录, 启动恒为出厂设定。
  不这么做的话, 上一次跑留下的 `plinko_config.json` 会把余额/投注档/返还率带进下一遍。
* `inputs` 里每条的时间戳是"**上一步走完那一帧**"的帧号/虚拟时刻 —— 帧间发生的动作
  (切档紧跟着起手蓄力)会共用同一个帧号。这是刻意的: 同一份输入在两边落到同一个帧上,
  才不会因为记录口径不同而对不齐。
* 录制要**真窗口**(这里用 win32 + SDL2 + 真 OpenGL)。`KIVY_WINDOW=mock` 那个 provider
  不存在, 拿不到布局几何。所以本录制台跑不了无头环境。
* `layout` 是 Kivy 文本度量算出来的, 换字号/换字体/换 Kivy 版本都会动 —— 换机器时
  先看 `meta.env` 而不是直接判定新版布局错。
"""

import hashlib
import importlib.util
import json
import os
import random
import sys
import tempfile

# ⚠️ 必须在 print(__doc__) 之前: 本文件 docstring 与运行时告警带 ⚠️/中文, 而 Windows
#    控制台默认 GBK —— 不带这句, `python tests/trace.py` 直接 UnicodeEncodeError 退出,
#    而崩掉的恰好是"窗口没钉住"那条最该发出来的告警。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")
GOLDEN_OLD = os.path.join(GOLDEN, "trace_old.json")
OLD_MAIN = r"E:\AI_Tools\other\DanZhu\android\main.py"

# 每帧恰好推一个物理步: 老版 `_frame` 用累加器攒 `FIXED_DT=1/60`, dt 给 1/60 就一帧一步,
# 而且永远碰不到"一帧里跑两个物理步"那条会掀翻落袋分支的路子。
DT = 1.0 / 60.0
SEED = 20260918
# 浮点落成 JSON 的小数位(与 tests/golden_extract.py 同一口径, 免得两套基线对不上账)。
QD = 9


def q(v):
    return round(float(v), QD)


def quiesce_audio(sfx, arng, seed):
    """等烘焙**线程真的收工**, 并把撞钉变体那条随机流**钉回种子起点**。两个驱动都调。

    ⚠️⚠️ **2026-09-19 起, 它的根因已经先被修掉了** —— `NewDriver.boot` 现在与 `OldDriver`
       一样设 `config.SOUND_ENABLED = False`, 于是**两边都不起烘焙线程**, 本函数退化成
       只做"把流钉回种子起点"这件事。留着是因为: ① 钉起点本身仍是确定性的一道保险;
       ② 万一将来谁又把烘焙打开(比如为了测音频层), 这里能把那条竞态原地收住。

    原说明(保留, 因为它是"为什么会漂"的完整记录):

    ⚠️⚠️ 为什么必须有这一步(实测): 烘焙与运行期的**撞钉音色变体共用同一条 `_ARNG`**
       (`bake_bank` 开头 `_ARNG.seed(SFX_SEED)`, 然后 `iter_bank` 一路抽几百个;
        运行期 `pick_peg_variant` 接着抽)。而烘焙跑在**后台线程**上 —— 老版在这次录制里
       命中磁盘缓存、一个数都没抽; 新版没命中、真合成了 700ms, 于是它的抽取**洒在脚本
       前几百帧里**(实测 frame 2 还在 `_sfx_win`), 运行期的变体整体错位
       (首处分叉: frame 260 的 `peg3` vs `peg4`, 到帧尾滚成 15 条音效的差)。

    ⇒ 烘焙属于**音频层**, 不在"行为轨迹"这份对账的范围内。所以这里:
       ① `join` 烘焙线程(真睡, 不推进假钟), 免得它跑到脚本中途还在抽;
       ② 把流钉回种子起点, 两边同一起跑线。

    ⚠️⚠️ **不能用 `sfx.baked` 当收工判据** —— `_install_sound_tap` 会把它**立刻**置真
       (那是为了跳过"加载竞态"那一段, 与本函数无关)。拿它判会当场返回, 烘焙照样在跑。
       判据只能是**线程活着没有**。

    ⚠️ **只钉起点, 不钉消耗** —— 运行期多抽或少抽一次仍然会被对账抓到, 那不是被掩盖,
       而是被隔离出来了。**如果哪天这里又出现"运行期抽取次数两边不等", 那就是真的多抽了。**
    """
    th = getattr(sfx, "_thread", None)
    if th is not None and th.is_alive():
        th.join(timeout=60.0)
        if th.is_alive():
            print("[trace] ⚠️ 烘焙线程 60 秒没结束 —— 下面的音效对账会带上它的噪声")
    try:
        arng.seed(seed)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 脚本: 12 发 + 两次切档 + 一次"必中档中奖后不点击"
# ---------------------------------------------------------------------------
# 每发"按住多少帧": 5/30/60/80 轮换三遍。
#   5 帧  -> power = 0.9*5/60 = 0.075 < MISFIRE_POWER(0.15)  => 哑火
#   80 帧 -> 蓄满(power 封 1.0)
HOLDS = [5, 30, 60, 200] * 3
# ⚠️ 第 4 个改成 **200 帧(3.33 秒)** —— 越过蓄力 3 秒的**兜底自动发射**阈值。
#    原来最大 80 帧(1.33s)离阈值差得远 ⇒ `_frame` 的『那一帧跳过渲染尾巴』
#    (老版 `main.py:21221-21222` 的 `launch(); return`)**从来没被对账覆盖过**。
#    ⚠️⚠️ **但扩剧本抓不到那个缺陷本身** —— 实测: 把 `_render_skip` 修复回退掉,
#       本轨迹的 sha1 **一模一样**(那多出来的一次 `_set_label_text` 是**幂等的**:
#       值没变 ⇒ 可观测状态没变 ⇒ 轨迹没变)。**轨迹看得见的是"状态", 不是"调用了什么"**,
#       所以这一类"多做一次幂等调用"的缺陷**扩多大剧本都抓不到**, 得靠
#       `tests/render_skip.py`(行为) 与 `tests/act_gate.py`(调用形状)。
#    补这一发的**真实收益**: 「蓄力 > 3 秒」这条**路径**此前从没被驱动过 ⇒ 现在它
#       在老版/新版上都被走了一遍并对账(帧数 5159→5462, 音效 266→275)。
#    ⚠️ 仍保持 **12 发**: 第 12 发是「必中档中奖后不点击」的收尾发, 之后状态本来就不是
#       ready ⇒ **不能追加第 13 发**(实测断言 '第 13 发起手时不是 ready' 直接炸)。

# 切档时机(第几发落定之后)与目标值。
BET_AFTER, BET_TO = 3, 50
RTP_AFTER, RTP_TO = 6, 1.20

# 第 12 发换到"必中档"(K_DIST={9:1.0}, 9 格全有奖) => 中奖是结构性的, 不靠运气。
WIN_LAUNCH, WIN_RTP = 12, 10.0
# 中奖后不点击, 再推这么多帧, 看演出是否**原地不动**。
POST_WIN_FRAMES = 900
# 切档前最多等多少帧让"结算结果窗口"过去(见 ui_quiet_until)。
SWITCH_WAIT = 1200
# 单发最多推多少帧都没落定就算失败(老版实测 p99 < 520)。
MAX_FLIGHT_FRAMES = 900
WARMUP_FRAMES = 30
# 前面几发要是也中了奖, 点掉它让流程能继续(老版低档 2~4 格有奖, 11 发里几乎必中)。
MAX_DISMISS_FRAMES = 1200
# ⚠️ 第 6 发: 飞到一半按「重置」—— 补 `reset_balance` 的"强制中断"那条路。
#    选第 6 发是因为它按住 30 帧(power≈0.45), 是**真的在飞**而不是哑火。
RESET_DURING_FLIGHT, RESET_AFTER_FRAMES = 6, 25


# ---------------------------------------------------------------------------
# 假钟 / 假时间模块
# ---------------------------------------------------------------------------
class FakeTime(object):
    """替掉老模块命名空间里的 `time`。墙钟由脚本决定, 一帧不差。"""

    def __init__(self):
        self.t = 0.0

    # ---- 由脚本推进的那几个 ----
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

    # ---- 不参与判据的: 原样转发真 stdlib(格式化时间戳之类) ----
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


class DetRandom(random.Random):
    """无参 `Random()` 也确定的替身。

    ⚠️ 光 `random.seed(SEED)` **不够**: `WinPileFX.__init__` 里有一句
    `self._rng = random.Random()` —— 无参构造在 stdlib 里取的是 OS 熵, **不是**那条被
    seed 过的全局流。装杯每颗球的下落时长/两次回弹幅度/自转都从这个流里抽, 于是
    "第几个球什么时候落定"(`_last_settle`)每遍都不一样 ⇒ 两遍轨迹在装杯那一段必然分叉
    (实测: 第 846 帧 `fx` 一个还是 win、一个已经 result)。

    换成按调用序号派生的固定种子: 确定、且与全局流的消耗顺序无关。
    """

    _n = 0

    def __init__(self, x=None):
        if x is None:
            DetRandom._n += 1
            x = SEED * 4096 + DetRandom._n
        random.Random.__init__(self, x)


class _RandomProxy(object):
    """把老模块命名空间里的 `random` 换掉: 只改 `Random`, 其余原样转发真 stdlib。"""

    Random = DetRandom

    def __init__(self, real):
        self._real = real

    def __getattr__(self, k):
        return getattr(self._real, k)


# ---------------------------------------------------------------------------
# 驱动器接口
# ---------------------------------------------------------------------------
class Driver(object):
    """录制台与具体实现之间唯一的缝。脚本只对这套接口说话。"""

    name = "?"

    def __init__(self):
        self.inputs = []       # 脚本喂进去的动作
        self.sound_log = []    # 真正发出去的声音
        self.settle_log = []   # 每次结算
        self.frame_no = -1
        self.t0 = 0.0

    # ---- 必须实现 ----
    def boot(self, seed):
        raise NotImplementedError

    def step(self, dt):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def layout(self):
        """`GEOM_WIDGETS` 那些控件的 (x,y,w,h) + 窗口尺寸。

        ⚠️ **实现在基类里** —— 老版读 `RootWidget` 的控件, 新版也读 `RootWidget` 的控件
           (界面壳那一半没搬走), 所以两边逐字相同。各写一份就是第二个真源, 迟早一边改了
           另一边没改, 而对账只会看到"布局不一样"却指不出是谁错。
        """
        r = self.root
        out = {"window": [q(self._win.width), q(self._win.height)],
               "root": [q(r.x), q(r.y), q(r.width), q(r.height)]}
        for nm in GEOM_WIDGETS:
            w = getattr(r, nm, None)
            if w is not None:
                out[nm] = [q(w.x), q(w.y), q(w.width), q(w.height)]
        for nm in ("rtp_btns", "bet_btns"):
            d = getattr(r, nm, None) or {}
            for k in sorted(d, key=lambda x: str(x)):
                w = d[k]
                out["%s[%s]" % (nm, k)] = [q(w.x), q(w.y), q(w.width), q(w.height)]
        return out

    def start_charge(self):
        raise NotImplementedError

    def launch(self):
        raise NotImplementedError

    def set_bet(self, v):
        raise NotImplementedError

    def set_rtp(self, v):
        raise NotImplementedError

    def reset_balance(self):
        raise NotImplementedError

    def title_hold(self):
        raise NotImplementedError

    def show_startup_info(self):
        raise NotImplementedError

    def click(self):
        raise NotImplementedError

    def result_ready(self):
        """演出是否已到"装满静止、过了最短停留、点一下就会退场"。

        与 `WinPileFX.request_close` 的受理条件同一口径; 录制台用它决定**什么时候值得点**
        —— 免得记一堆"点了没反应"的假输入。
        """
        raise NotImplementedError

    def ui_quiet_until(self):
        """老版"结算结果窗口"的截止时刻(`_result_until`): 窗口内**故意**抑制 UI 语音。

        录制台在切档前会等它过去 —— 否则 `voice_bet_*` / `voice_rtp_*` 这一路永远录不到。
        """
        raise NotImplementedError

    def close(self):
        pass

    # ---- 录制台用的记账 ----
    def note(self, kind, **kw):
        rec = {"frame": self.frame_no, "t": q(self.clock_now()), "op": kind}
        rec.update(kw)
        self.inputs.append(rec)

    def clock_now(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 老版驱动器
# ---------------------------------------------------------------------------
class OldDriver(Driver):
    name = "old"

    def __init__(self, main_path=OLD_MAIN):
        Driver.__init__(self)
        self.main_path = main_path
        self.mod = None
        self.root = None
        self.ft = FakeTime()
        self._win = None
        self._clock = None
        self._tmp = None
        self._frame_probe0 = 0

    # ---- 启动 ----
    def boot(self, seed):
        os.environ.setdefault("KIVY_NO_ARGS", "1")
        sys.argv = ["main", "--nosound"]
        from kivy.clock import Clock
        from kivy.core.window import Window

        self._clock = Clock
        self._win = Window
        # ⚠️ 顺序不能反: `schedule_once` 当场把 timeout 折进 `_last_tick`, 先排事件再改钟
        #    的话那些事件永远不到期(实测 silent)。
        Clock._max_fps = 0.0
        Clock.time = lambda: self.ft.t
        Clock._last_tick = 0.0
        Clock._start_tick = 0.0
        Clock._duration_ts0 = 0.0
        Clock._last_fps_tick = None

        spec = importlib.util.spec_from_file_location("oldmain", self.main_path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.mod = m
        # 老模块顶部 `import time` 拿到的是 stdlib 全局模块; 在**它的命名空间里**换掉即可,
        # stdlib / Kivy 内部不受影响。
        m.time = self.ft
        # 同上, 但换的是 `random`: 老模块里有一处无参 `random.Random()`(见 DetRandom)。
        m.random = _RandomProxy(m.random)
        # --nosound 本来由 main() 处理, 这里不跑 main(), 自己置。
        m.SOUND_ENABLED = False
        # 持久化整体挪到一次性临时目录: 读档恒失败 => 出厂设定, 且绝不碰老工程的盘。
        self._tmp = tempfile.mkdtemp(prefix="danzhu_trace_")
        for nm in ("_config_path", "_history_path", "_bench_history_path",
                   "_hp_history_path"):
            setattr(m.RootWidget, nm,
                    staticmethod(lambda _n=nm: os.path.join(self._tmp, _n + ".json")))
        # 落盘工作线程整条掐掉(它只写文件, 与轨迹无关; 留着会往临时目录写东西)。
        m._cfg_post = lambda cfg, path: True

        random.seed(seed)
        app = m.PlinkoApp()
        app.build()
        self.root = app.rootw
        # 窗口尺寸钉死: 布局几何是逐字节对账的一部分, 尺寸漂了整条轨迹跟着漂。
        self._win.size = (540, 960)
        # 老版自己把 _frame_timed 挂在 Clock 的 interval 上; 撤掉, 由本脚本按固定 dt 泵。
        Clock.unschedule(self.root._frame_timed)
        self._install_sound_tap()
        self._install_settle_tap()
        quiesce_audio(self.root.sfx, m._ARNG, m.SFX_SEED)
        return self

    # ---- 音效: 假输出后端 ----
    def _install_sound_tap(self):
        """把 `Sfx` 从"关着"拨到"开着但后端是假的"。

        只录**真发出去**的那一声 —— 五道闸(enabled / 名字已就绪 / 增益非零 / 节流 /
        语音互斥)全部照跑, 后端那一步落进 `_TraceOut.play_named`。
        ⚠️ 别改成"在 play() 入口记一笔": 那样节流吃掉的重击、互斥掐掉的旧句都会进录,
        录出来的就不是"听到什么"了。
        """
        sfx = self.root.sfx
        sfx.enabled = True
        sfx.baked = True
        sfx.out = _TraceOut(self)
        # `named` 是加载闸: 真机上它随 WAV 落盘慢慢长出来, 这段竞态不可复现。
        # 用"什么都有"的替身把闸门置成"已放行", 于是只测播放决策, 不测启动竞态。
        sfx.named = _AllNames()
        sfx._q = None          # 发声工作线程不建(假后端本来就是同步的)
        sfx._qthread = None

    def _install_settle_tap(self):
        m = self.mod
        orig = m.RootWidget.settle
        drv = self

        def settle(self, i):
            before = self.balance
            n0 = drv.frame_no
            # ⚠️ _easter_egg 必须在 orig() **之前**读: 真身 settle 开头就把它清成 False,
            #    放到后面读会让这个字段恒为 False(死字段, 永远测不到彩蛋那条路)。
            easter = bool(getattr(self, "_easter_egg", False))
            orig(self, i)
            drv.settle_log.append({
                "frame": n0, "t": q(drv.ft.t), "slot": int(i),
                "bet": self.bet, "mult": self.multipliers[i],
                "balance_before": q(before), "balance_after": q(self.balance),
                "easter": easter,
            })

        m.RootWidget.settle = settle

    # ---- 帧推进 ----
    def step(self, dt):
        self.frame_no += 1
        self.ft.t += dt
        # 先跑本帧到期的定时回调(装杯起播 / 统计刷新), 再跑一帧逻辑。
        self._clock.tick()
        self.root._frame(dt)

    def clock_now(self):
        return self.ft.t

    # ---- 输入 ----
    def start_charge(self):
        self.note("start_charge")
        self.root.start_charge()

    def launch(self):
        self.note("launch", power=q(self.root.power))
        self.root.launch()

    def set_bet(self, v):
        self.note("set_bet", value=int(v))
        self.root.set_bet(v)

    def set_rtp(self, v):
        self.note("set_rtp", value=float(v))
        self.root.set_rtp(v)

    def reset_balance(self):
        self.note("reset_balance")
        self.root.reset_balance()

    def title_hold(self):
        # ⚠️ 老版`_on_title_touch_down` 是**成对**置的(main.py:14236-14237):
        #    `_bench_start = time.time()` + `_bench_triggered = False`。
        #    而 `_bench_triggered` **老版从不在 __init__ 里建**(靠 `t > 0` 短路才没炸)
        #    ⇒ 只置 `_bench_start` 会在 `_check_title_hold` 里 AttributeError(实测踩过)。
        self.note("title_hold")
        self.root._bench_start = self.ft.t
        self.root._bench_triggered = False

    def show_startup_info(self):
        # 跑分菜单里的「游戏信息」—— 菜单项两版同名(`main.py:15264` / `bench.py:985`)。
        self.note("show_startup_info")
        self.root._show_startup_info()

    def click(self):
        """模拟玩家点一下(装杯装满后的唯一出口)。

        走的是真身 `WinPileFX.request_close` —— 只录结果, 不录那个返回值(它由
        `result` 模式是否已到最短停留决定, 本身就落在轨迹的状态里)。
        """
        ok = bool(self.root.game_area.win_fx.request_close())
        self.note("click", accepted=ok)
        return ok

    def result_ready(self):
        fx = self.root.game_area.win_fx
        return fx.mode == "result" and self.ft.t >= fx._settled_at + self.mod.HOLD_BASE

    def ui_quiet_until(self):
        return float(getattr(self.root, "_result_until", 0.0))

    # ---- 观测 ----
    def snapshot(self):
        r = self.root
        b = r.ball
        return {
            "state": r.state,
            "power": q(r.power),
            "ball": None if b is None else [q(b.x), q(b.y), q(b.vx), q(b.vy)],
            "balance": q(r.balance),
            "round_plays": int(r.round_plays),
            "plays": int(r.plays),
            "hits": int(r.hits),
            "fx": r.game_area.win_fx.mode,
        }


    def close(self):
        try:
            self._clock.unschedule(self.root._frame_timed)
        except Exception:
            pass


class _AllNames(set):
    """`name in sfx.named` 恒真 —— 把"启动期 WAV 还没加载完"那段竞态从录制里拿掉。"""

    def __contains__(self, _k):
        return True


class _TraceOut(object):
    """假发声后端: 只记账, 不出声(也不起线程)。"""

    mode = "named"
    needs_worker = False

    def __init__(self, drv):
        self.drv = drv

    def play_named(self, name, gain01):
        self.drv.sound_log.append({
            "frame": self.drv.frame_no, "t": q(self.drv.ft.t),
            "name": name, "gain": q(gain01),
        })
        return True

    def play_pcm(self, _data):
        return True

    def prime(self, *_a):
        pass

    def replay_reset(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 新版驱动器(接口留好, UI 未落地)
# ---------------------------------------------------------------------------
class NewDriver(Driver):
    """重构版驱动器。与 `OldDriver` 逐项对应，只是把观测点从 `RootWidget` 挪到 `Game`。

    新版把老版那个 8263 行的 `RootWidget` 劈成了两半：
      · `danzhu/game.py :: Game`      —— 状态机（`state/power/ball/balance/plays/hits`）
      · `danzhu/ui/root.py :: RootWidget` —— 界面壳（控件树，几何）
    所以 `snapshot` 读 `self.game`，`layout` 仍读 `self.root`。

    ⚠️ **只读 `Game` 自己的口径，不许在驱动器里"换算"**：`Game.settle_log` 与老版
       trace 的 tap 是**同一个形状**（frame/t/slot/bet/mult/balance_before/balance_after/
       easter），`Game.fx_mode()` 就是老版的 `game_area.win_fx.mode`。任何"我看它差一点，
       在这里补一下"都是把对账做绿，不是把端口做对。
    """

    name = "new"

    def __init__(self):
        Driver.__init__(self)
        self.app = None
        self.root = None
        self.game = None
        self.ft = FakeTime()
        self._win = None
        self._clock = None
        self._tmp = None

    # ---- 启动 ----
    def boot(self, seed):
        os.environ.setdefault("KIVY_NO_ARGS", "1")
        sys.argv = ["main", "--nosound"]
        # ⚠️ 老版那一路用 `spec_from_file_location` 直接加载文件, 不需要 sys.path; 新版是
        #    一个包, 必须让 `danzhu` 可导入。本脚本以 `python tests/trace.py` 跑,
        #    sys.path[0] 是 tests/。
        _root = os.path.dirname(HERE)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from kivy.clock import Clock
        from kivy.core.window import Window

        self._clock = Clock
        self._win = Window
        self._freeze_clock()
        self._tmp = tempfile.mkdtemp(prefix="danzhu_trace_new_")
        self._redirect_persistence()

        # ⚠️⚠️ **必须与 OldDriver 的 `m.SOUND_ENABLED = False` 对齐**。
        #    老版那一路: `Sfx(False)` 在 `__init__` 里直接 return ⇒ **不起烘焙线程**。
        #    新版若不同步这一条, 它会在 `build()` 里起后台线程用 ~700ms CPU 合成 41 条音效 ——
        #      · 和主线程**抢 GIL**, 把 `build()` 从 41ms 抬到 ~400ms(实测,
        #        与老版同口径对照才看得出来: 两边都烘 423/396ms, 都不烘 37/41ms);
        #      · 而且会一路抽 `_ARNG`, 把运行期的撞钉音色变体整体挪位。
        #    ⚠️ 改 `danzhu.config` 的模块属性(运行期读), 不是 from-import —— 那是拷贝。
        import danzhu.config as _cfg
        _cfg.SOUND_ENABLED = False
        # ⚠️ 与 `OldDriver` 的 `m._cfg_post = lambda cfg, path: True` 对齐:
        #    老版那一路**不写盘**(`_save_config` 在 `_cfg_post` 返回真时直接 return)。
        #    新版的落盘口是可注入的, 不打这个桩它就会真的往临时目录写 —— 行为上无害
        #    (轨迹逐字节相同, 那些写发生在工作线程上、不碰游戏状态), 但**两边喂的输入就不一样了**。
        #    今天刚被同类的不对称咬过一次(`SOUND_ENABLED` 只设了一边 ⇒ 量出 10 倍假回归),
        #    所以这里一律对齐。
        import danzhu.platform.device as _dev
        _dev._cfg_post = lambda cfg, path: True
        import danzhu.ui.app as appmod
        self._patch_module_time()
        random.seed(seed)
        self.app = appmod.PlinkoApp()
        self.app.build()
        self.root = self.app.rootw
        self.game = self.root.game
        # 窗口尺寸钉死: 布局几何是逐字节对账的一部分, 尺寸漂了整条轨迹跟着漂。
        self._win.size = (540, 960)
        # `RootWidget.__init__` 自己把 `_frame_timed` 挂在 Clock 的 interval 上; 撤掉,
        # 由本脚本按固定 dt 泵(与 OldDriver 同一条)。
        Clock.unschedule(self.root._frame_timed)
        self._install_sound_tap()
        self._install_settle_tap()
        import danzhu.audio.synth as _syn
        quiesce_audio(self.root.sfx, _syn._ARNG, _syn.SFX_SEED)
        return self

    def _freeze_clock(self):
        """与 OldDriver **逐行同一条**: 假钟必须在任何 schedule 之前装好。

        ⚠️ 顺序不能反: `schedule_once` 当场把 timeout 折进 `_last_tick`, 先排事件再改钟
           的话那些事件永远不到期(实测 silent)。
        """
        Clock = self._clock
        Clock._max_fps = 0.0
        Clock.time = lambda: self.ft.t
        Clock._last_tick = 0.0
        Clock._start_tick = 0.0
        Clock._duration_ts0 = 0.0
        Clock._last_fps_tick = None

    def _patch_module_time(self):
        """把 `danzhu.*` 每个模块命名空间里的 `time` 换成假钟。

        ⚠️ 为什么要遍历所有子模块而不是改一处: 新版与老版同一个约定 —— 各模块
           `import time` 之后直接 `time.time()`, 所以每个模块**各有一份** `time` 绑定。
           漏掉任何一个, 那条路就接上真墙钟(表现是"同种子两遍录不出同一份轨迹", 而且
           只在走到那个模块的那条分支时才分叉, 极难定位)。
        ⚠️ `random` 同样要换: `WinPileFX.__init__` 里那句无参 `random.Random()` 取的是
           **OS 熵**, 与全局流无关(见 `DetRandom` 的说明)。
        """
        for name, mod in list(sys.modules.items()):
            if not name.startswith("danzhu") or mod is None:
                continue
            if hasattr(mod, "time") and not isinstance(mod.time, FakeTime):
                mod.time = self.ft
            if hasattr(mod, "random") and not isinstance(mod.random, _RandomProxy):
                mod.random = _RandomProxy(mod.random)

    def _redirect_persistence(self):
        """持久化整体挪到一次性临时目录: 读档恒失败 => 出厂设定, 且绝不碰真机存档。

        ⚠️ 老版是往 `RootWidget` 的四个 staticmethod 上打补丁; 新版这四样由
           `PlayMixin`/`BenchMixin` 提供、在 `Game`/`RootWidget` 构造时被**读一次**,
           所以补丁必须在 `PlinkoApp()` **之前**打好。
        ⚠️ 一个都没打上说明名字变了 —— **报错, 不许静默放行**: 放行的后果是这次录制
           读到了上一次跑留下的存档, 而轨迹看起来完全正常。
        """
        import danzhu.ui.play as playmod
        try:
            import danzhu.ui.bench as benchmod
        except Exception:
            benchmod = None
        names = ("_config_path", "_history_path", "_bench_history_path", "_hp_history_path")
        patched = []
        for nm in names:
            for owner in (playmod.PlayMixin, getattr(benchmod, "BenchMixin", None)):
                if owner is not None and hasattr(owner, nm):
                    setattr(owner, nm,
                            staticmethod(lambda _n=nm: os.path.join(self._tmp, _n + ".json")))
                    patched.append(nm)
                    break
        missing = [nm for nm in names if nm not in patched]
        if missing:
            raise RuntimeError(
                "持久化注入点没找到: %s —— 名字变了或还没端口。"
                "静默放行会让这次录制读到上一遍留下的存档。" % (missing,))

    # ---- 音效: 假输出后端 ----
    def _install_sound_tap(self):
        """与 OldDriver 同一条: 只录**真发出去**的那一声。

        五道闸(enabled / 名字已就绪 / 增益非零 / 节流 / 语音互斥)全部照跑, 后端那一步
        落进 `_TraceOut.play_named`。新版音效层是同一个 `Sfx`, 所以这段可以直接照抄。
        """
        sfx = self.root.sfx
        sfx.enabled = True
        sfx.baked = True
        sfx.out = _TraceOut(self)
        sfx.named = _AllNames()
        sfx._q = None          # 发声工作线程不建(假后端本来就是同步的)
        sfx._qthread = None

    def _install_settle_tap(self):
        """把 `Game` 自己记的结算流水同步进驱动器那份 `settle_log`。

        ⚠️ **复用 `Game._log_settle` 的产物, 不在这里另算一遍** —— 老版那份是外部 tap
           自己拼的字段, 新版 `Game` 内部已经按**同一个形状**记了一份。在这里重新拼一遍
           `frame/t/slot/bet/mult/...` 就是第二个真源, 迟早和 Game 漂移(而漂移的方向
           恰好会让对账"看起来对")。
        """
        g = self.game
        orig = g._log_settle
        drv = self

        def _log_settle(i, before, easter):
            orig(i, before, easter)
            drv.settle_log.append(dict(g.settle_log[-1]))

        g._log_settle = _log_settle

    # ---- 帧推进 ----
    def step(self, dt):
        self.frame_no += 1
        self.ft.t += dt
        # 先跑本帧到期的定时回调(装杯起播 / 统计刷新), 再跑一帧逻辑。
        self._clock.tick()
        self.root._frame(dt)

    def clock_now(self):
        return self.ft.t

    # ---- 输入 ----
    def start_charge(self):
        self.note("start_charge")
        # ⚠️ 走 **UI 的同步入口**(老版调的也是 `root.start_charge`) —— 直接调 `game.*`
        #    会绕过事件派发, 音效晚一帧(实测第 1 条 launch 就是 frame 34 vs 35)。
        self.root.start_charge()

    def launch(self):
        self.note("launch", power=q(self.game.power))
        self.root.launch()

    def set_bet(self, v):
        self.note("set_bet", value=int(v))
        self.root.set_bet(v)

    def set_rtp(self, v):
        self.note("set_rtp", value=float(v))
        self.root.set_rtp(v)

    def reset_balance(self):
        self.note("reset_balance")
        self.root.reset_balance()

    def title_hold(self):
        self.note("title_hold")
        self.game._bench_start = self.game.now
        self.game._bench_triggered = False

    def show_startup_info(self):
        self.note("show_startup_info")
        self.root._show_startup_info()

    def click(self):
        """模拟玩家点一下(装杯装满后的唯一出口)。走 `Game.click()`(它转 `fx.request_close`)。"""
        ok = bool(self.game.click())
        self.note("click", accepted=ok)
        return ok

    def result_ready(self):
        """演出是否已到"装满静止、过了最短停留、点一下就会退场"。

        ⚠️ **照 OldDriver 的公式原样写, 不去问 Game** —— 「到没到最短停留」是**录制台**
           自己决定"什么时候值得点"的口径(它读 `fx.mode` / `fx._settled_at` / `HOLD_BASE`),
           老版不是产品逻辑。曾经在 `Game` 上挂过一个同名方法, 而 `WinPileFX` 没有
           `result_ready`, `getattr` 兜底成恒 False ⇒ 那个钩子是空转的, 对账会静默少掉
           一整段"点击退场"的覆盖。
        """
        fx = self.root.game_area.win_fx
        from danzhu.ui.winfx import HOLD_BASE
        return fx.mode == "result" and self.ft.t >= fx._settled_at + HOLD_BASE

    def ui_quiet_until(self):
        return float(self.game.ui_quiet_until())

    # ---- 观测 ----
    def snapshot(self):
        g = self.game
        b = g.ball
        return {
            "state": g.state,
            "power": q(g.power),
            "ball": None if b is None else [q(b.x), q(b.y), q(b.vx), q(b.vy)],
            "balance": q(g.balance),
            "round_plays": int(g.round_plays),
            "plays": int(g.plays),
            "hits": int(g.hits),
            "fx": g.fx_mode(),
        }

    def close(self):
        try:
            self._clock.unschedule(self.root._frame_timed)
        except Exception:
            pass


GEOM_WIDGETS = (
    "game_area", "title_lbl", "status_lbl", "balance_lbl", "stats_lbl",
    "_bead_lbl", "_rtp_title_lbl", "_bet_title_lbl", "power_lbl",
    "mute_btn", "round_btn", "reset_btn", "fire_btn",
    "_row_top", "_row_rtp", "_row_bets", "_row_info", "_row_bottom",
)


# ---------------------------------------------------------------------------
# 脚本本体(只对 Driver 接口说话)
# ---------------------------------------------------------------------------
def run_script(drv, log):
    frames = []
    transitions = []
    state = {"cur": None}

    def push():
        snap = drv.snapshot()
        st = snap["state"]
        if st != state["cur"]:
            transitions.append({
                "frame": drv.frame_no, "t": q(drv.clock_now()),
                "from": state["cur"] or "<boot>", "to": st,
                "cause": _infer_cause(state["cur"], st),
            })
            state["cur"] = st
        rec = {"f": drv.frame_no, "t": q(drv.clock_now())}
        rec.update(snap)
        frames.append(rec)
        return st

    def pump(n):
        for _ in range(n):
            drv.step(DT)
            push()

    def pump_until(pred, limit, what):
        for _ in range(limit):
            drv.step(DT)
            push()
            if pred():
                return True
        raise SystemExit("%s: %d 帧没到 (state=%s fx=%s)"
                         % (what, limit, drv.snapshot()["state"],
                            drv.snapshot()["fx"]))

    def switch(fn, label):
        """切档统一走这里: 先等过"结算结果窗口"。

        老版在 `_result_until` 窗口内**故意**抑制 UI 语音(`set_bet` / `set_rtp` 里那句
        `time.time() >= self._result_until`)。不等它过去, `voice_bet_*` / `voice_rtp_*`
        这两路永远录不到 —— 而它们正是"切档有没有反馈"的观测点。
        """
        pump_until(lambda: drv.clock_now() >= drv.ui_quiet_until(),
                   SWITCH_WAIT, "%s 前等结果窗口结束" % label)
        fn()

    # ---- 热身: 让 Kivy 的布局/尺寸收敛, 几何在稳定态上采样 ----
    pump(WARMUP_FRAMES)
    geom = {"warmup": drv.layout()}
    if list(geom["warmup"]["window"]) != [540.0, 960.0]:
        log("⚠️ 窗口不是钉死的 540x960, 而是 %s —— 布局/dpi 会因为窗口尺寸漂, "
            "这条 golden 换机器前先确认尺寸" % geom["warmup"]["window"])

    # ---- 12 发 ----
    dismissed = 0
    win_result_frame = None
    for i, hold in enumerate(HOLDS, 1):
        n = i
        if n == WIN_LAUNCH:
            # 必中档: K_DIST={9:1.0} ⇒ 9 格全有奖, 这一发的中奖是结构性的, 不靠运气
            switch(lambda: drv.set_rtp(WIN_RTP), "换必中档")
        log("第 %d 发: 按住 %d 帧" % (n, hold))
        assert drv.snapshot()["state"] == "ready", "第 %d 发起手时不是 ready" % n
        assert drv.snapshot()["fx"] == "idle", "第 %d 发起手时演出没收干净" % n
        drv.start_charge()
        pump(hold)
        drv.launch()
        if n == RESET_DURING_FLIGHT:
            # ⚠️ 飞行途中的「重置」—— `reset_balance` 里那句"强制中断当前操作
#            (charging/flying/misfire/landing)回 ready"压根没被对账走过。
            pump(RESET_AFTER_FRAMES)
            drv.reset_balance()
            log("第 %d 发**飞行中按重置**" % n)
            continue
        # 推到 ready(没中/哑火收球) 或 演出起来(中了)
        for _ in range(MAX_FLIGHT_FRAMES):
            drv.step(DT)
            push()
            snap = drv.snapshot()
            if snap["state"] == "ready" or snap["fx"] != "idle":
                break
        else:
            raise SystemExit("第 %d 发 %d 帧还没落定: %s"
                             % (n, MAX_FLIGHT_FRAMES, drv.snapshot()))

        if n == WIN_LAUNCH:
            if drv.snapshot()["fx"] == "idle":
                raise SystemExit("必中档那一发居然没中(盘面/档位没生效?)")
            # 不点击 —— 推到杯子装满静止, 再单独推 POST_WIN_FRAMES 看它退不退场
            pump_until(lambda: drv.snapshot()["fx"] == "result",
                       MAX_DISMISS_FRAMES, "第 %d 发演出到 result" % n)
            win_result_frame = drv.frame_no
            log("第 %d 发中奖后不点击, 从 result 再推 %d 帧"
                % (WIN_LAUNCH, POST_WIN_FRAMES))
            pump(POST_WIN_FRAMES)
            continue

        if drv.snapshot()["fx"] != "idle":
            # 提前中奖: 等装满 -> 点掉 -> 回 ready, 让脚本能继续往下发
            pump_until(drv.result_ready, MAX_DISMISS_FRAMES, "第 %d 发装杯装满" % n)
            drv.click()
            pump_until(lambda: drv.snapshot()["state"] == "ready",
                       MAX_DISMISS_FRAMES, "第 %d 发点完回 ready" % n)
            dismissed += 1
            log("   (第 %d 发命中, 已点掉演出)" % n)

        # 中途切档(在两个 ready 之间做, 老版 set_rtp 非 ready 直接不动)
        if n == BET_AFTER:
            switch(lambda: drv.set_bet(BET_TO), "换投注档")
        if n == RTP_AFTER:
            switch(lambda: drv.set_rtp(RTP_TO), "换返还率档")

    # ⚠️ **长按标题 3 秒 ⇒ 跑分菜单**(`_check_title_hold` 发 `bench_menu` ⇒ `_show_bench_menu`)。
    #    放**剧本最末尾**: 它会开一个模态弹窗, 放中间会干扰后面每一发。
    drv.title_hold()
    pump(200)
    log("长按标题 3 秒 ⇒ 已推 200 帧(菜单事件应已发)")
    drv.show_startup_info()
    pump(60)
    log("菜单里点「游戏信息」⇒ 面板已开")

    final = drv.snapshot()
    geom["after_win"] = drv.layout()

    # ---- 判据: 中奖后不点击, 演出必须一直停在 result、状态一直是 landed ----
    held = [r for r in frames if r["f"] >= win_result_frame]
    stuck_ok = bool(held) and all(r["fx"] == "result" and r["state"] == "landed"
                                  for r in held)
    checks = {
        "launches": len(HOLDS),
        "misfires": sum(1 for r in transitions if r["to"] == "misfire"),
        "wins_dismissed": dismissed,
        "win_hold_all_result": stuck_ok,
        "win_hold_frames": len(held),
        "lose_settles": sum(1 for r in drv.settle_log if r["mult"] <= 0),
        "win_settles": sum(1 for r in drv.settle_log if r["mult"] > 0),
    }

    meta = {
        "schema": 1,
        "driver": drv.name,
        "dt": q(DT),
        "seed": SEED,
        "script": {
            "holds": HOLDS,
            "bet_after": BET_AFTER, "bet_to": BET_TO,
            "rtp_after": RTP_AFTER, "rtp_to": RTP_TO,
            "win_launch": WIN_LAUNCH, "win_rtp": WIN_RTP,
            "post_win_frames": POST_WIN_FRAMES,
            "warmup_frames": WARMUP_FRAMES,
            "max_flight_frames": MAX_FLIGHT_FRAMES,
        },
        "source": {"old_main": OLD_MAIN, "sha1": _sha1_file(OLD_MAIN)},
        "env": {
            "python": sys.version.split()[0],
            "kivy": _kivy_version(),
            "platform": sys.platform,
            "window": geom["warmup"]["window"],
        },
    }

    trace = {
        "meta": meta,
        "checks": checks,
        "frames": frames,
        "transitions": transitions,
        "settles": drv.settle_log,
        "sounds": drv.sound_log,
        "inputs": drv.inputs,
        "layout": geom,
        "summary": {
            "frames": len(frames),
            "last_state": final["state"],
            "last_fx": final["fx"],
            "settles": len(drv.settle_log),
            "sounds": len(drv.sound_log),
            "final_balance": final["balance"],
            "plays": final["plays"],
            "hits": final["hits"],
        },
    }
    return trace


def _infer_cause(frm, to):
    if frm is None:
        return "boot"                       # 建界面那一刻的初值, 不是迁移
    if to == "landing":
        return "pocket"
    if to == "landed":
        return "bounce_settled"
    if to == "ready" and frm == "landed":
        return "park_ball"
    if to == "ready" and frm == "misfire":
        return "misfire_done"
    if to == "ready" and frm in ("charging", "flying", "landing"):
        return "reset_balance"
    if to == "charging":
        return "input:start_charge"
    if to == "flying":
        return "launch"
    if to == "misfire":
        return "launch(misfire)"
    return "?"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _sha1_file(path):
    if not os.path.exists(path):
        return ""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _kivy_version():
    try:
        import kivy
        return kivy.__version__
    except Exception:
        return "?"


def _dump(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm(obj, drop_driver=False):
    """对账用的视图。`drop_driver`: 去掉 `meta.driver`(它按定义就不同: "old" / "new")。

    ⚠️ 除这一处之外**一个字段都不许放宽** —— 本对账的全部意义就是逐字节; 任何"差不多
       就行"的容差都会让它退化成"看不出来"。
    """
    d = dict(obj)
    if drop_driver:
        m = dict(d.get("meta") or {})
        m.pop("driver", None)
        d["meta"] = m
    return d


def _blob(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _field_diffs(a, b):
    """哪几个顶层字段不同 + 列表字段的**首处下标**(比一坨字节偏移好定位得多)。"""
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        extra = ""
        if isinstance(va, list) and isinstance(vb, list):
            n = min(len(va), len(vb))
            i = next((j for j in range(n) if va[j] != vb[j]), None)
            if i is None:
                extra = "  条数 %d vs %d" % (len(va), len(vb))
            else:
                extra = "  首处 #%d" % i
                if isinstance(va[i], dict) and "f" in va[i]:
                    extra += "(frame %s vs %s)" % (va[i].get("f"), vb[i].get("f"))
        print("[trace]   字段 %s 不同%s" % (k, extra))


def _compare(want, got, label, drop_driver=False):
    """want/got 是**已解析**的两份 trace。相同->True; 不同->打印定位信息并 False。

    ⚠️ 比的是**规范序列化**(`ensure_ascii=False, separators=(",",":")`)之后的字节 ——
       与 `_dump` 写盘用的是同一种, 所以对**同一份文件**来说它逐字节等价于比原始字节
       (实测: 两份 golden 的 raw sha1 与再序列化 sha1 一位不差); 跨版本那一侧因为要去掉
       `meta.driver` 才必须走这一层。
    """
    wa, gb = _norm(want, drop_driver), _norm(got, drop_driver)
    a, b = _blob(wa), _blob(gb)
    sa, sb = hashlib.sha1(a).hexdigest(), hashlib.sha1(b).hexdigest()
    if sa == sb:
        print("[trace] [绿] %s: 逐字节相同  sha1 %s" % (label, sa))
        return True
    print("[trace] [红] %s: 不同" % label)
    print("[trace]   golden sha1 %s / 本次 sha1 %s" % (sa, sb))
    _field_diffs(wa, gb)
    _first_diff(a, b)
    return False


def make_driver(which):
    if which == "old":
        return OldDriver()
    if which == "new":
        return NewDriver()
    raise SystemExit("未知 --which: %r (只有 old / new)" % which)


def main(argv):
    which = "old"
    do_record = "--record" in argv
    do_verify = "--verify" in argv
    if "--which" in argv:
        which = argv[argv.index("--which") + 1]
    if not (do_record or do_verify):
        print(__doc__)
        return 0

    drv = make_driver(which)
    print("[trace] 驱动器 = %s" % drv.name)
    drv.boot(SEED)
    try:
        trace = run_script(drv, lambda s: print("[trace] " + s))
    finally:
        drv.close()

    s = trace["summary"]
    c = trace["checks"]
    print("[trace] 帧 %d / 迁移 %d / 结算 %d / 音效 %d / 末态 %s(%s) / 余额 %s"
          % (s["frames"], len(trace["transitions"]), s["settles"], s["sounds"],
             s["last_state"], s["last_fx"], s["final_balance"]))
    print("[trace] 发数 %d / 哑火 %d / 中奖结算 %d / 未中结算 %d / 中途点掉的中奖 %d"
          % (c["launches"], c["misfires"], c["win_settles"], c["lose_settles"],
             c["wins_dismissed"]))
    print("[trace] 中奖后不点击 %d 帧: %s"
          % (c["win_hold_frames"],
             "全程停在 result/landed" if c["win_hold_all_result"]
             else "⚠️ 中间退场了"))

    if do_record:
        path = GOLDEN_OLD if which == "old" else os.path.join(
            GOLDEN, "trace_%s.json" % which)
        os.makedirs(GOLDEN, exist_ok=True)
        sha = _dump(path, trace)
        print("[trace] 写出 %s" % path)
        print("[trace] sha1 %s  (%d 字节)"
              % (sha, os.path.getsize(path)))
        # ⚠️ 录完**当场**打一遍跨版本对账(只有新版需要, 老版自己就是基线)。
        #    只出声、不改返回码 —— record 的职责是写文件。这一句是为了让"重录一遍制造绿"
        #    那条路上的漂移**当场可见**, 而不是等下一次 `--verify` 才被人发现。
        if which != "old":
            if os.path.exists(GOLDEN_OLD):
                # ⚠️ `drop_driver=True` 不能漏: 刚录的这份 `meta.driver` 是 "new",
                #    不去掉就是**恒红**(那种"永远红"的检查等于没有检查)。
                _compare(_load(GOLDEN_OLD), trace, _cross_label(), drop_driver=True)
            else:
                print("[trace] ⚠️ 缺 %s —— 跨版本对账打不了(先 `--record` 录老版)"
                      % GOLDEN_OLD)
        return 0

    # --verify
    # ⚠️⚠️ 两种对账, 见文件抬头。① 跨版本是**门禁**(新版只有它有证明力);
    #    ② 可复现只是"确定性", 但它红了也说明有东西在漂 —— 两条都算失败。
    ok = True
    if which == "old":
        # 老版自己就是基线: 只有"与冻结基线逐字节相同"这一条口径。
        ok = _compare(_load(GOLDEN_OLD), trace, os.path.basename(GOLDEN_OLD))
    else:
        # ① 跨版本对账(门禁)
        if not os.path.exists(GOLDEN_OLD):
            print("[trace] [红] 缺 %s —— 跨版本对账无法进行(先 `--record` 录老版)。"
                  % GOLDEN_OLD)
            print("       ⚠️ 只比自己上一次的录制 = 假绿, 所以这里当失败处理。")
            return 1
        ok = _compare(_load(GOLDEN_OLD), trace, _cross_label(), drop_driver=True)
        # ② 可复现对账(与本驱动上一次录制)
        p2 = os.path.join(GOLDEN, "trace_%s.json" % which)
        if os.path.exists(p2):
            # ⚠️ 顺序不能反: `and` 会短路, 写成 `ok and _compare(...)` 就**跑不到**这一条了。
            ok = _compare(_load(p2), trace,
                          "%s(上一次录制, 只证可复现)" % os.path.basename(p2)) and ok
        else:
            print("[trace] (没有 %s, 跳过「可复现」那一半)" % p2)
    return 0 if ok else 1


def _cross_label():
    return "%s(老版冻结基线, 跨版本对账, 只忽略 meta.driver)" % os.path.basename(GOLDEN_OLD)


def _first_diff(a, b):
    """逐字节找第一处不同 —— 递归定位到字段级, 省得人肉二分。"""
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    print("[trace]   首个不同字节 @%d" % i)
    for name, blob in (("golden", a), ("本次", b)):
        lo = max(0, i - 90)
        print("[trace]   %s ...%s" % (name, blob[lo:i + 90].decode(
            "utf-8", "replace").replace("\n", "\\n")))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
