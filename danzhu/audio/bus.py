"""音效总线 Sfx: 一次合成 + 按名字播 + 就绪护栏 + 语音互斥。

pcm 后端(winmm): gain 量化 10 档缓存缩放后的 PCM。
named 后端(SoundPool/SoundLoader): WAV 落盘 + 按名字播, gain 直接给后端当音量。

⚠️ `play()` 的返回值 = **「请求已受理」**, 不是"后端真的播成了"。它牵着震动
   (`if sfx.play(...): _vibrate_tick()`), 改成"后端说了算"会让流一忙就整段没震。
"""

import os
import threading
import time

from ..config import (EV_ARC, EV_CEIL, EV_DIV, EV_PEG, EV_WALL,
                      SFX_MIN_SP, SFX_REF_SP)
from ..physics import clamp          # clamp 只此一份(见 physics)
from . import backend as B
from .synth import SR, bake_bank, iter_bank, pick_peg_variant
from .voice import N_VOICE, _voice_files, voice_miss

SFX_MASTER = 1.0             # 总音量 (0~1), 手机喇叭需要满幅

# ---------------------------------- 事件位 → 音效 ---------------------------
# 撞击音下限(法向速率 px/s): 低于此值视为轻微擦碰, 不发声。
# ⚠️ EV_ARC(弧面接触)的下限是 1e9 ⇒ **永不发声** —— 弧面那一声由 GUI 另播
#    `play("rail", 0.18, throttle=0.05)`。别为了"让弧面也响"把它调下来(会叠音)。
# ⚠️ 这两张表的**唯一真源在 `config.py`** —— 本模块原来自建了一份(那份是全的),
#    而 `game.py` 也自建了一份(漏了 EV_ARC)。归一之后两边取同一份。
# 顶部碰撞音: 球冲到最高点"转向"时发声, 不等真撞墙 —— 实测只有满蓄力(apex y=22)才真撞到
# 顶墙, 且撞点就在 apex 上速率仅 77px/s, 按速率定音量必然听不见。
SFX_APEX_Y_LO = 58.0         # 最弱有效蓄力的转向高度(球心 y), 实测
SFX_APEX_Y_HI = 22.0         # 满蓄力的转向高度(球顶几乎贴上顶墙), 实测

_VARIANT_FAMILIES = ("peg", "wall", "div", "top")


def _throttle_key(name):
    """节流键: 同一族的随机变体**共用一个闸门**(peg0..5 / wall0..1 / div0..1 / top0..1)。

    ⚠️ 不能按变体名各自计时 —— 那等于把聚合速率乘以变体数。实测 `peg` 是 6 倍:
       `impact()` 里写的是 `throttle=0.08`(意图"机关枪连珠只响第一声"), 但 idx 是按撞击
       强度 + randint 选出来的, 6 个变体各有一个计时器轮流放行 ⇒ 上限 75 次/秒。
       每一次通过闸门的播放都是一次 JNI 往返, 足以把帧时间顶过 16.67ms 的预算 ——
       表现就是"弹珠在飞的时候一卡一卡"。
    """
    for _p in _VARIANT_FAMILIES:
        if name.startswith(_p) and name[len(_p):].isdigit():
            return _p
    return name


def peg_variant(t, gate=None):
    """撞钉音色变体 —— **先判闸, 再抽变体**(老版 `Sfx.impact` 的语句顺序)。

    ⚠️ 顺序是行为的一部分, 不是风格问题: 老版 `Sfx.impact` 的第一句就是
       `if not self.enabled or sp < SFX_MIN_SP.get(bit, 0.0): return False`,
       **变体是在过了那道闸之后**才抽的 ⇒ **静音时 `_ARNG` 一个数都不消耗**。
       播放层若先抽变体、后判闸(或不判), 静音前后切回来 `_ARNG` 的序列就与老版错位 ——
       听感无损, 但"同输入 ⇒ 同轨迹"这条确定性破了, 而且对账**看不见**
       (bank 只比 PCM 的 sha1, 变体序列不进任何对账)。
    ⚠️ 另一道闸(`sp < SFX_MIN_SP.get(bit, 0.0)`)不在本函数里 —— 它在调用方, 是
       老版同一条语句的后半句, 必须**在调本函数之前**判掉, 否则一样会多抽一个数。
    `gate`: 任何带 `.enabled` 的对象(传 `Sfx` 实例即可); None = 没有音效层 = 放行。
    返回 None = 这一下**不该发声** —— 调用方不许凑一个默认变体糊过去(那正是本函数防的错)。
    """
    if gate is not None and not getattr(gate, "enabled", True):
        return None
    return pick_peg_variant(t)


class Sfx:
    """合成一次(后台线程), 之后每次发声只做取样+送声卡。"""

    def __init__(self, enabled=True, sync=False, start_now=True):
        self.enabled = bool(enabled)
        self.out = None
        self.bank = {}
        self.named = set()          # named 后端里已经可播的音效名
        self.baked = False          # 整库烘焙是否收工(UI 的冷启动加载页按它收尾)
        self._baked_at = 0.0        # 收工时刻(启动时钟, 秒)
        self._no_audio_ms = 0.0     # "无音频加载累计耗时"(ms, 摘页那刻算出来)
        # 启动总耗时(ms) = 摘页那一刻的启动时钟读数, 与上面那个**同一次测量**里落盘。
        # ⚠️ 老算法 `bake_ms + ready_ms` 只数了烘焙线程自己那两段, 漏掉"进程启动 ->
        #    烘焙开工"那 ~1 秒 ⇒ 会出现「总耗时 < 无音频加载累计耗时」的荒谬读数。
        self._total_ms = 0.0
        self._audio_ready = False   # 烘完 + **探到真的能播** 才为真(见 _await_ready)。
                                    # 但 !enabled / 后端探测不可用时一律放行 —— 绝不软锁。
        self.ready_ms = 0.0         # 等"真的能播"花了多久(0 = 不适用/没探针)
        # 「谁先到」的诊断真源 —— 只给启动日志看, **不参与任何判据**
        self._ready_winner = ""     # "闸门" / "探针" / "超时" / ""(还没等过)
        self._arm_rows = []         # 实验档的结论行(只给重放详情弹窗看)
        self._arm_busy = False      # 实验档正在跑 ⇒ 重放那一页先别急着报"完成"
        self._gate_missing_name = ""      # 点名结果(哪个样本卡住了) —— 只给日志/面板看
        self._gate_rebuilt_once = False   # "重建池"每进程只许一次(重放会复位)
        self._gate_open_ms = 0.0    # 闸门打开的时刻(相对这一次等待的 t0)
        self._probe_open_ms = 0.0   # 老探针说"全部就绪"的时刻
        self.n_attempt = 0          # 过了全部闸门、真的向后端要过声音的次数
        self.n_missed = 0           # 上面那些里**后端仍说没播成**的次数(静默的正面计数)
        self._expected = 0          # 满编音效数(烘焙时顺手记, 不能现算)
        self._n_bank = 0            # 满编的合成音数(不含语音)
        self._failed = []           # 加载失败的 (name, path): 后台重试, 见 _retry_failed
        self._retry_wait = 0.6      # 重试间隔(秒)
        self._retry_rounds = 12     # 重试轮数(≈7s), 还不成功就认命
        self.bake_ms = 0.0
        self.cached = False         # 本次启动是否命中磁盘缓存(没现场合成)
        self._scaled = {}
        self._last = {}
        self._last_voice = 0.0       # 全局语音间隔: 防重叠
        self._last_voice_len = 0.0   # 上一句的时长(互斥按它判, 不写死 3 秒)
        self._thread = None
        if not self.enabled:
            return
        self.out = B.open_output()
        if self.out is None:
            self.enabled = False
            return
        # 发声工作线程: 把后端调用搬出主线程。见 `_drain` 的说明。
        self._q = None
        self._qthread = None
        if getattr(self.out, "needs_worker", False):
            try:
                import queue as _queue
                self._q = _queue.Queue(maxsize=64)
                self._qthread = threading.Thread(target=self._drain, daemon=True)
                self._qthread.start()
            except Exception:
                self._q = None
        if sync:
            self._bake()
        elif start_now:
            self.start_bake()
        # else: 由调用方在**第一帧**再调 `start_bake()` —— 见 `app.py` 的说明

    def start_bake(self):
        """起烘焙线程。⚠️ 只有**没起过**才起(幂等)。"""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._bake, daemon=True)
        self._thread.start()

    def _backend_call(self, item):
        """真正那一次后端调用 + 计时(同步路径与工作线程共用, 保证两边统计口径一致)。"""
        _t0 = time.perf_counter()
        try:
            if item[0] == "pcm":
                self.out.play_pcm(item[1])
            else:
                self.out.play_named(item[2], item[1])     # (增益, 名字) —— 名字放最后
        finally:
            _dt = time.perf_counter() - _t0
            B._SND_STAT[0] += _dt
            if _dt * 1000.0 > B.SND_SLOW_MS:
                B._SND_STAT[3] += 1
            if _dt > B._SND_STAT[1]:
                B._SND_STAT[1] = _dt
                # ⚠️ 取**最后一项**当名字 —— 两个分支的载荷不同(pcm 是字节, named 是名字+增益),
                #    所以名字一律放最后。写成 `item[1]` 会把整段 PCM 字节当名字塞进面板。
                B._SND_STAT[2] = str(item[-1])[:24]

    def _drain(self):
        """发声工作线程(只在后端声明 `needs_worker` 时才起)。

        ⚠️ **必须把发声搬出主线程**: 真机实测 `SoundPool.play()` 单次最慢 143.6ms、50 次
           累计 2674ms(平均 53.5ms), 而帧间隔才 12.5ms —— 主线程每响一声就被卡几十毫秒。
       SoundPool 为什么慢是设备/HAL 的事, 这里不赌它变快 —— 只赌"主线程不必等它"。
        ⚠️ 线程安全: SoundPool 本身线程安全, 而且烘焙线程早就在后台调它的 JNI。
        ⚠️ 队列满就**丢这一声**(和 winmm 声道全忙时一致) —— 绝不阻塞主线程。
        """
        q = self._q
        if q is None:
            return
        while True:
            try:
                item = q.get()
            except Exception:
                return
            if item is None:
                return
            try:
                self._backend_call(item)
            except Exception:
                pass

    def _bake(self):
        t0 = time.perf_counter()
        B._boot_log("bake", "烘焙线程开始(后端 %s)" % getattr(self.out, "mode", "?"))
        try:
            if getattr(self.out, "mode", "pcm") == "pcm":
                self._bake_pcm()
            else:
                self._bake_named()
        except Exception:
            pass
        self.bake_ms = (time.perf_counter() - t0) * 1000.0
        B._boot_log("bake", "烘焙线程结束: %.0f ms" % self.bake_ms)
        # ⚠️ `baked` 放在 except 之后: 烘失败了也要放行, 否则加载页永不消失(软锁)。
        self.baked = True
        self._baked_at = time.perf_counter()
        self._await_ready()             # 再等"真的能播"(带硬超时)

    # ---- 冷启动"真的能播了吗"的护栏(首次安装必然没声音的正面修复) -------------
    # ⚠️ 到点无条件放行(绝不软锁 —— 项目红线), 只是**放行前**尽量修一次。
    SFX_READY_TIMEOUT = 6.0
    # 闸门**先跑**, 最多等它这么久; 到点还没开就转老探针兜底。
    # ⚠️ 不能"两个一起跑": 老探针一次 probe_all() 是**阻塞 3 秒**的整轮扫描,
    #    "先探针再问闸门"等于让闸门白等那 3 秒 —— 收益归零。
    # ⚠️ 这个值只在"闸门**存在但打不开**"时才被付掉(类取不到时压根不走这条路)。
    SFX_GATE_FIRST_SEC = 1.5
    # 探测间隔(秒) —— **起手值**, 之后由速率自适应接管。
    # ⚠️ 真正的最优间隔是 sqrt(8×解码耗时), 它随设备变 ⇒ 别再定死一个数。
    #    起手用大间隔是因为第一次探测时没有任何速率信息, 能少付"失败 play"。
    SFX_READY_POLL = 0.15

    def audio_ready(self):
        """加载页什么时候可以摘: 音效关了(没什么可等) 或 烘完且探到真能播。"""
        return (not self.enabled) or self._audio_ready

    def _gate_repair(self, _desc=""):
        """闸门到点没开: **点名 → 重 load → (必要时)重建池**。只做一轮, 绝不循环。

        ⚠️ 在这之前这条路**只会等**(等到 6 秒硬上限 → 照样放行 → 那个音照样不响),
           既不修、也不说是哪一个。
        ⚠️ 每项都有上限: 点名/重 load 都是"扔给后台"(`_retry_failed` 自己重试 N 轮),
           不阻塞放行; 重建池**只在"一个都没就绪"时**做(那说明回调那条路像死了),
           且每进程只允许一次。
        ⚠️ **绝不重新合成资源**: 冷路径的合成**正是**"load 返回 0"的原因(2.2 秒纯 Python
           抢解码线程), 失败后再合成 = 再制造一次争抢。
        """
        try:
            _nm = ""
            _got = None
            _fn = getattr(self.out, "gate_first_missing", None)
            if _fn is not None:
                _got = _fn()
            if _got:
                _nm = str(_got[1])
                try:
                    self._gate_missing_name = _nm
                except Exception:
                    pass
                B._boot_log("probe", "闸门差件: %s(池%d) 还没就绪 —— 以前只知道「差 1 个」"
                           % (_nm, int(_got[0])))
            # 把它丢进重试队列(后台反复重 load, 成功就进 named)
            try:
                _paths = getattr(self.out, "_paths", {}) or {}
                if _nm and _nm in _paths:
                    self._failed.append((_nm, _paths[_nm]))
                    self._retry_failed()
                    B._boot_log("probe", "已把 %s 丢进重试队列(%d 轮 × %.1f 秒)"
                               % (_nm, int(getattr(self, "_retry_rounds", 0) or 0),
                                  float(getattr(self, "_retry_wait", 0.0) or 0.0)))
            except Exception as _e:
                B._boot_log("probe", "重试入队失败(不影响放行): %r" % (_e,))
            # 一个都没就绪 ⇒ 回调那条路像死的 ⇒ 重建两个池 + 全部重新 load(每进程一次)
            _rdy = -1
            try:
                _rf = getattr(self.out, "gate_ready_count", None)
                _rdy = int(_rf()) if _rf is not None else -1
            except Exception:
                _rdy = -1
            if _rdy == 0 and not getattr(self, "_gate_rebuilt_once", False):
                self._gate_rebuilt_once = True
                _snap = dict(getattr(self.out, "_paths", {}) or {})
                _t0 = time.perf_counter()
                self.out.replay_reset()          # 换新池 + 挂新闸门 + 清应到清单
                _n = 0
                for _n2, _p2 in _snap.items():
                    try:
                        self.out.prime(_n2, _p2)
                        _n += 1
                    except Exception:
                        pass
                B._boot_log("probe", "闸门 0 就绪 ⇒ 重建两个池并重新 load %d/%d 个 (%.0f ms)"
                           % (_n, len(_snap), (time.perf_counter() - _t0) * 1000.0))
        except Exception as _e:
            B._boot_log("probe", "修复阶梯自己出错(不影响放行): %r" % (_e,))

    def _await_ready(self):
        """等"真的能播"再放行 —— 这是冷启动加载页的摘页判据。

        ⚠️ 不能"烘完就放行": `SoundPool.load()` 是异步的, 它**返回了 sampleId 不代表解码
        完了**; 没解码完 `play()` 返回 0(静默, 不抛异常不留痕), 而 `named` 闸门早就放行了
        —— 于是"所有音效都在、就是不响"。这就是「**首次安装打开 App 必然全静音**, 第二次
        打开走缓存、速度快所以能响」的根因。
        ⚠️ 硬超时: 探不到也放行。**这是这条路的唯一出口, 不许去掉**(探针本身也可能失灵)。
        """
        try:
            probe = getattr(self.out, "probe_all", None)
            if probe is None:                 # 后端没有探针(桌面/静音) → 不等
                self._audio_ready = True
                return
            t0 = time.time()
            # ---- 闸门(纯 Java 的加载完成回调)优先 ----
            #   `_g_use` 为假时, 下面整个循环与"没有闸门那一版"逐字相同。
            _g_use = False
            _g_ok = getattr(self.out, "gate_all_ready", None)
            _g_deadline = 0.0
            _g_desc = ""
            _g_repaired = False        # 修复阶梯**每个进程只跑一轮**
            _g_last_rdy = -2           # 上一次看到的就绪数(用来判"还在不在进展")
            _g_fallback = True          # 闸门到点没开时, 要不要落回老探针
            self._ready_winner = ""
            self._gate_open_ms = 0.0
            self._probe_open_ms = 0.0
            # 档位由「重放冷启动」写在后端上(空 = 出货默认 "first")
            _g_mode = str(getattr(self.out, "_gate_mode", "") or B.GATE_MODE_FIRST)
            try:
                _g_info = getattr(self.out, "gate_info", None)
                _g_desc = str(_g_info()) if _g_info is not None else ""
                _g_exp = int(getattr(self.out, "gate_expected", lambda: 0)() or 0)
                if _g_ok is not None and _g_exp > 0 and _g_mode != B.GATE_MODE_OFF:
                    _g_use = True
                    _g_fallback = (_g_mode != B.GATE_MODE_ONLY)
                    # "only" 档: 一直等它到 6 秒硬上限, 中间不跑探针(量的是闸门的纯读数)
                    _g_deadline = (t0 + self.SFX_GATE_FIRST_SEC) if _g_fallback \
                        else (t0 + self.SFX_READY_TIMEOUT + 1.0)
                    B._boot_log("probe", "实验档=%s: 闸门可用(%s)%s"
                               % (_g_mode, _g_desc,
                                  " ⇒ 先等它 %.1f 秒, 不开就走老探针" % self.SFX_GATE_FIRST_SEC
                                  if _g_fallback else " ⇒ 只等闸门, 老探针一次都不跑"))
                elif _g_mode == B.GATE_MODE_OFF:
                    # ⚠️ 文案与老版不同(老版是"没登记到样本"): 老版那个「登」**不在字体子集里**
                    #    (FontSubset 只有 1602 码位), 真机上这行诊断会显示成方块。老版自己为豆腐块
                    #    让过 4 次路(摄氏度/每瓦跑分/群/全角＝), 这句是漏网的那个。含义不变。
                    # ⚠️ 这一档要跟"闸门不可用"分开印 —— 否则看日志的人会以为闸门挂了。
                    B._boot_log("probe", "实验档=off: 闸门在位但**本档不使用**(%s) ⇒ 走老探针"
                               % (_g_desc or "没有样本",))
                else:
                    B._boot_log("probe", "实验档=%s: 闸门不可用(%s) ⇒ 走老探针"
                               % (_g_mode, _g_desc or "没有样本"))
            except Exception as _e:
                B._boot_log("probe", "闸门初始化判断异常 ⇒ 走老探针: %r" % (_e,))
            # ⚠️ **先把这一次走的是哪一档写进日志** —— 没有这一行, 对照数据就不知道哪组是哪组。
            # ⚠️ **有意的行为改动(不与老版逐位一致, 已在交付说明的 not_fixed 里申报)**:
            #    老版这行是 `self._probe_cfg()`(老 main.py:6306), 而 `_probe_cfg` **只定义在
            #    `_SoundPoolOut` 上**(老 4037), `Sfx` 没有 ⇒ 老版这行**恒抛 AttributeError**,
            #    被紧邻的 `except Exception: pass` 吞掉(老 6307-6308) ⇒ 那条日志**从来没印出来过**。
            #    这里保留新版写法(`self.out._probe_cfg()`), 因为这条日志是"这一轮走的哪一档
            #    探针"的唯一痕迹, 有诊断价值。
            #    ⚠️ 老版那个 except 的语义**一条没丢**: 它管的是"这行日志是**尽力而为**的 ——
            #       后端没有 `_probe_cfg`(winmm / Kivy-SoundLoader 就没有)时同样抛、同样被吞,
            #       且**绝不许**因为一条日志把启动搞崩"。语义与老版完全相同。
            try:
                _m0, _b0 = self.out._probe_cfg()
                B._boot_log("probe", "探针模式: 每池线程 %s%s"
                           % (_m0, " + 钉核提优先" if _b0 else ""))
            except Exception:
                pass
            # 探针**起手**时各簇的当前频率(日志头部那个"当前"是导出那一刻读的, 对判断
            # "探针期间有没有被降频/丢小核"没用)。
            try:
                _sh, _fr = B._cpu_shape()
                if _sh:
                    B._boot_log("probe", "探针起手时 CPU: %s %s" % (_sh, _fr))
            except Exception:
                pass
            _rnd = 0
            # ⚠️ 自适应轮询: 起手用 SFX_READY_POLL, 之后按**实测速率**算"还要等多久"。
            #    信号必须用**速率**而不是"本轮扫了几个" —— 后者与时间无关, 慢解码到中期
            #    也会变大, 会误切小间隔。
            _poll = self.SFX_READY_POLL
            _last_scan = 0
            _last_t = 0.0
            _total_n = int(getattr(self, "_expected", 0) or 0)
            while time.time() - t0 < self.SFX_READY_TIMEOUT:
                # ---- ① 闸门优先 ----
                # ⚠️ 顺序就是**收益的全部**: 老探针一次 probe_all() 是阻塞 3 秒的整轮扫描,
                #    所以"先探针、后看闸门"等于让闸门白等那 3 秒 —— 收益归零。
                #    (⚠️ `_rnd` 只在探针那一支里加: 它是"探针跑了几轮", 不是"循环转了几圈"。)
                if _g_use:
                    try:
                        _g_now = bool(_g_ok())
                    except Exception as _ge:
                        _g_now = False
                        _g_use = False
                        B._boot_log("probe", "闸门查询异常 ⇒ 退回老探针: %r" % (_ge,))
                    if _g_now:
                        self._gate_open_ms = (time.time() - t0) * 1000.0
                        self._ready_winner = "闸门"
                        B._boot_log("probe", "闸门打开: t+%.0f ms(两池全部就绪) ⇒ 放行; "
                                            "这一次探针一次都没跑" % self._gate_open_ms)
                        break
                    # ---- **有进展就给闸门续命**, 别急着转老探针 ----
                    #   ⚠️ 定时触发(到 1.5 秒就转)会让"解码就是慢"的机器**既等满 1.5 秒、
                    #      又再付一遍老探针的 3~5 秒**, 而闸门明明还在干活。
                    #   ⚠️ 判据只看"就绪数有没有涨": 涨 ⇒ 续命; **卡住不动** ⇒ 才转兜底。
                    #      6 秒硬上限仍在循环条件上 ⇒ 绝不软锁。
                    try:
                        _g_rdy = int(getattr(self.out, "gate_ready_count", lambda: -1)() or -1)
                    except Exception:
                        _g_rdy = -1
                    if _g_rdy != _g_last_rdy:
                        _g_last_rdy = _g_rdy
                        _g_deadline = time.time() + self.SFX_GATE_FIRST_SEC
                    if time.time() < _g_deadline:
                        time.sleep(0.02)     # 闸门查询是纯内存(下行 JNI), 密一点无妨
                        continue
                    if not _g_fallback:
                        time.sleep(0.02)     # "only" 档: 等到 6 秒硬上限为止
                        continue
                    # ---- 先"修", 再谈兜底(只跑一轮: 点名/重 load 扔后台, 重建池自带一次性) ----
                    if not _g_repaired:
                        _g_repaired = True
                        self._gate_repair(_g_desc)
                    _g_use = False
                    B._boot_log("probe", "闸门停住不动(%.0f ms 没进展, 就绪 %s) ⇒ 起走老探针兜底"
                               % (self.SFX_GATE_FIRST_SEC * 1000.0, _g_last_rdy))
                _rnd += 1
                _p0 = time.perf_counter()
                _ok = probe()
                _pdt = (time.perf_counter() - _p0) * 1000.0
                # ⚠️ **这一行是整份启动日志的核心**: 它把「探针自己多贵」与「真的在等解码」
                #    当场分开 —— 在此之前没人能分辨这两者。
                try:
                    B._PROBE_TRACE.append(((time.time() - t0) * 1000.0,
                                          int(getattr(self.out, "_probe_scan", 0) or 0),
                                          bool(_ok)))
                except Exception:
                    pass
                try:
                    B._PROBE_COST[0] += _pdt
                    B._PROBE_COST[1] += int(getattr(self.out, "_probe_played", 0) or 0)
                except Exception:
                    pass
                B._boot_log("probe", "第 %d 轮(t+%.0f ms): 探针自身 %.1f ms, 连续就绪 %s 个, "
                                    "真扫 %s 个, 本轮间隔 %.0f ms, %s"
                           % (_rnd, (time.time() - t0) * 1000.0, _pdt,
                              getattr(self.out, "_probe_scan", "?"),
                              getattr(self.out, "_probe_played", "?"),
                              _poll * 1000.0,
                              ("全部就绪" if _ok else
                               ("卡在 " + (getattr(self.out, "_probe_stuck", "") or "?")))))
                if _ok:
                    self._probe_open_ms = (time.time() - t0) * 1000.0
                    self._ready_winner = "探针"
                    break
                # ---- 按速率重算下一次的间隔 ----
                try:
                    _now_ms = (time.time() - t0) * 1000.0
                    _scan = int(getattr(self.out, "_probe_scan", 0) or 0)
                    _dt = _now_ms - _last_t
                    if _total_n > 0 and _dt > 0 and _scan > _last_scan:
                        _rate = (_scan - _last_scan) / _dt          # 个/ms
                        _eta = (_total_n - _scan) / _rate           # 预计还要多少 ms
                        _poll = max(0.03, min(0.20, _eta * 0.3 / 1000.0))
                    _last_scan = _scan
                    _last_t = _now_ms
                except Exception:
                    pass
                time.sleep(_poll)
            if not self._ready_winner:
                self._ready_winner = "超时"
            # 闸门**自己掐的表**(Java 侧, 从挂闸门到最后一个样本就绪), 不含 Python 的轮询间隔。
            try:
                _gm = getattr(self.out, "gate_ready_ms", None)
                if _gm is not None:
                    _gmv = float(_gm())
                    if _gmv >= 0:
                        B._boot_log("probe", "闸门自记(Java 侧, 不含轮询间隔): %.0f ms" % _gmv)
            except Exception:
                pass
            # 实验档用完就复位(下一次启动回到出货默认) —— 复位即"空串"
            try:
                if getattr(self.out, "_gate_mode", ""):
                    self.out._gate_mode = ""
            except Exception:
                pass
            self.ready_ms = (time.time() - t0) * 1000.0
            try:
                _sh2, _fr2 = B._cpu_shape()
                if _sh2:
                    B._boot_log("probe", "探针收工时 CPU: %s %s" % (_sh2, _fr2))
            except Exception:
                pass
            B._boot_log("probe", "音效加载结束: %.0f ms, 共 %d 轮" % (self.ready_ms, _rnd))
            # ---- 实验档: 跑在"等待结束之后", 不污染上面那几个数 ----
            #   ⚠️ 48k 那一档必须等冷烘焙把 22050 的 wav 写回缓存之后才跑 —— 重放会先清掉整个
            #      缓存目录, 所以它只能挂在这里(不能在 `_replay_cold_start` 里起)。
            #   ⚠️ `_arm_busy` 是给「重放冷启动」那一页看的: 实验跑在 `_audio_ready = True`
            #      之前, 而 veil 的"完成"判据看的是更早的 `sfx.baked` ⇒ 不挂这个标志的话,
            #      玩家手快就会点开一个**还没有实验结果**的详情弹窗。
            _arm_x = str(getattr(self.out, "_arm_extra", "") or "")
            if _arm_x:
                try:
                    self.out._arm_extra = ""          # 用完复位(下一条命回到出货默认)
                except Exception:
                    pass
                self._arm_busy = True
                try:
                    if _arm_x == "48k":
                        # 退休档(见 backend.ARM_LADDER 下方注释): 保留可复活
                        _paths48 = [(n, p) for n, p in list(getattr(self.out, "_paths", {}).items())
                                    if not n.startswith("voice_")]
                        self._arm_rows = B._arm48_bench(_paths48)
                        for _r in self._arm_rows:
                            B._boot_log("probe", _r)
                    elif _arm_x == "pools":
                        self._arm_rows = B._arm_pools_bench(
                            list(getattr(self.out, "_paths", {}).items()))
                        for _r in self._arm_rows:
                            B._boot_log("probe", _r)
                except Exception as _e:
                    self._arm_rows = ["%s 实验　出错: %r" % (_arm_x, _e)]
                finally:
                    self._arm_busy = False
        except Exception as _e:
            # ⚠️ 这里**只加记录, 不改行为**: 循环里任何异常若裸 pass, ready_ms 会保持 0、
            #    启动日志一行不留, 然后照常硬放行 —— 症状就是「初次安装必然没声音」原样回来,
            #    而且查不到任何痕迹。仍然硬放行 ——「绝不软锁」是项目红线。
            B._boot_log("probe", "探针异常 ⇒ 直接放行: %r" % (_e,))
        self._audio_ready = True

    def backend_count(self):
        """后端**真的**握着几个可播对象; None = 这个后端不报数(或压根没有后端)。

        ⚠️ 拿不到时**绝不能**退回闸门值(`len(self.named)`) —— 那正是这块面板存在的理由被
           反噬: 安卓上 SoundPool 构造失败、静默降级到 `_KivySoundOut` 时, 面板会报
           `97 / 97` **全绿**。兜底只能是"不知道" —— 用一个类型上分得开的返回值(None)。
        """
        fn = getattr(self.out, "loaded_count", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    def audio_detail(self):
        """「启动信息」里那几行(每行一个字段)。

        玩家报的「初次安装必然没声音」—— 它的**所有候选原因在产物里长得一模一样**
        (静默 / 不抛异常 / 不留痕), 没有 adb 就只能把真值按字段摆开, 一次截图定位到哪一支。

        设计规则(三条都是踩过坑才有的):
        1. **顺序 = 排查优先级**, 不是"变不变"。
        2. **有唯一预期值的, 只在偏离时才有信息**; 没有唯一预期值的(冷/热、等待时长)才常显。
        3. **恒定值降权、不删除** —— 出事那张截图里它们就是基线。
        ⚠️ 行数有硬预算; 合并行几乎免费 —— 所以"后端名"并进了"音效就绪"那一行。
        ⚠️ 整段 try/except: 这只是隐藏菜单里的几行, 绝不把弹窗带崩。
        """
        try:
            out = self.out
            n_gate = len(self.named)
            n_back = self.backend_count()
            named_mode = getattr(out, "mode", "") == "named"
            bname = getattr(out, "name", "静音")
            deviated = B.platform == "android" and named_mode and bname != "SoundPool"
            # ⚠️ 「音效开关」那一行是玩家点名删掉的: 有唯一预期值的行, 常态印它只是噪音。
            #    信息没丢 —— 真出问题时「音频后端」那行会变。
            # ⚠️ 唯一的代价: 玩家自己点了静音时面板看起来是正常的。
            # ⚠️ PCM 后端不写磁盘缓存 ⇒ `cached` 恒 False ⇒ PC 上永远显示「冷启动」。
            #    那是**事实**, 不是标签错(玩家定稿要 PC 与安卓同一个口径)。
            _tot = float(getattr(self, "_total_ms", 0.0) or 0.0)
            if _tot <= 0.0:
                _tot = self.bake_ms + self.ready_ms
            mode_row = "%s启动总耗时%.0fms" % ("热" if self.cached else "冷", _tot)
            n_rc = getattr(out, "rebuild_count", 0)
            # 「无音频加载累计耗时」= **不必等音频就绪的话, 最早什么时候能进游戏**(ms)
            #   = max(烘焙收工, 加载页"演完"的实测时刻) ← 摘页那刻算好存进 `_no_audio_ms`
            # ⚠️ 印 `bake_ms` 是错的: 加载页那 220ms 的最短停留是与音频无关的地板。
            _noa = float(getattr(self, "_no_audio_ms", 0.0) or 0.0)
            if _noa <= 0.0:
                _noa = float(getattr(self, "bake_ms", 0.0) or 0.0)
            # 括号里那个「音效加载 X ms」的三种形态(有探针且等了 / 本后端无探针 / 没等)
            if self.ready_ms > 0:
                _wait = "音效加载%.0fms" % self.ready_ms
            elif getattr(out, "probe_all", None) is None:
                _wait = ("音效加载 无法确认能播(本后端无探针)"
                         if B.platform == "android" else "音效加载 0ms")
            else:
                _wait = "音效加载0ms(未等待)"

            # ⚠️ 语音目录**列举失败**时必须让诊断看得出为什么: 目录没打进包时语音会全灭,
            #    而看面板的人只会看到「语音就绪 0/0」—— 与"语音集本来就是空的"长得一模一样。
            #    老版这块是裸 pass, 于是"语音全灭"在诊断里查无实据(见 voice._VOICE_MISS)。
            #    有唯一预期值 ⇒ **只在异常时出现**, 常态一行都不印。
            _vmiss_row = ""
            try:
                _vm = str(voice_miss() or "")
                if _vm:
                    _vmiss_row = "语音目录　%s" % _vm[:64]
            except Exception:
                _vmiss_row = ""

            # ⚠️ PCM 后端: 下面三项对它**结构上就不适用**(全建立在"按名字加载、有 sampleId"
            #    之上)。不适用的行直接不出现, 而不是印成"不适用" —— 三行"不适用"既是噪音,
            #    又把真正有内容的两行淹掉。
            if not named_mode:
                _loads = ""
                # ⚠️ 合成音 = bank 总数 - 语音数, **不重新合成、不碰 bake_bank**。
                #    ⚠️ bank 为空时一行都不印 —— 否则会印出「音效加载 0 个」, 读起来像全军覆没。
                if self.bank:
                    _vf = _voice_files()
                    _nv_load = sum(1 for _n in _vf if _n in self.bank)
                    _nb_load = max(0, len(self.bank) - _nv_load)
                    _loads = "音效就绪 %d/%d，语音就绪 %d/%d" % (
                        _nb_load, self._n_bank or _nb_load, _nv_load, len(_vf))
                rows = [mode_row + "(" + _wait + ")",
                        "无音频加载累计耗时：%.0fms" % _noa]
                if _loads:
                    rows.append(_loads)
                if _vmiss_row:
                    rows.append(_vmiss_row)
                rows.append("音频后端　%s" % bname)
                if n_rc:
                    rows.append("后端重建　%d 次" % n_rc)
                return rows

            # ---- 以下都是 named 后端(安卓的 SoundPool, 或降级到 Kivy-SoundLoader) ----
            if n_back is None:
                ready = "未知（该后端不报数）"
            else:
                ready = "%d / %d" % (n_back, n_gate)
                if n_back < n_gate:
                    ready += "　后端缺 %d" % (n_gate - n_back)
                # ⚠️「满编」只在**闸门自己就短了**的时候才印(常态印它只是噪音)。
                if self._expected and n_gate < self._expected:
                    ready += "（满编 %d）" % self._expected
            # 「语音就绪」紧跟「音效就绪」: 那个数含语音, 单看它"音效响、语音不响"是隐形的。
            _n_voice = sum(1 for _n in self.named if _n.startswith("voice_"))
            _n_voice_all = max(0, self._expected - self._n_bank)
            rows = [mode_row + "(" + _wait + ")",
                    "无音频加载累计耗时：%.0fms" % _noa,
                    "音效就绪：%s，语音就绪：%d/%d" % (ready, _n_voice, _n_voice_all)]
            if _vmiss_row:
                # ⚠️ 紧跟「语音就绪」: 那个数是 0/0 时, 下一行直接给出**为什么**。
                rows.append(_vmiss_row)
            rows.append("音频后端　%s" % bname)
            # ⚠️ 「先到者」与「就绪闸门」两行**已从面板撤下**: 它们是实验包的输出, 实验已结束。
            #    信息没丢 —— 「保存加载日志」里各有一份。实现仍在, 只是不往 rows 里塞。
            try:
                for _r in list(getattr(self, "_arm_rows", []) or []):
                    rows.append(str(_r))
            except Exception:
                pass
            # ⚠️ 期望值**单独占一行**, 不并进上一行: 并进去会让那行超宽折行, 而折行是这里
            #    最容易出事的地方(被定高标签裁掉尾巴)。
            if deviated:
                rows.append("　　　　　安卓上应为 SoundPool，静默降级了")
                _err = B._backend_error("SoundPool")
                if _err:
                    rows.append("　　　　　%s" % _err[:64])
            if n_rc:
                rows.append("后端重建　%d 次" % n_rc)
            return rows
        except Exception:
            return []

    def audio_status(self):
        """上面那几行的一行版(日志与门禁用; 界面上显示的是分栏)。"""
        return "  |  ".join(self.audio_detail())

    def _bake_pcm(self):
        self.bank = bake_bank()             # 整体赋值(引用切换), 读侧只会看到空或全量
        # ⚠️ **必须在语音并入之前**取 `len(self.bank)` —— 那才是"合成音满编"。
        #    口径与 `_bake_named` 对齐: 满编 = 合成音 + 语音(两个都是数据源)。
        _vf = _voice_files()
        self._n_bank = len(self.bank)
        self._expected = self._n_bank + len(_vf)
        for name, path in _vf.items():      # 预录语音并入 bank, winmm 同路径可播
            try:
                self.bank[name] = B._read_wav_pcm(path)
            except Exception:
                continue
        for name in ("win0", "win1", "win2", "win3", "win4", "win5", "win6", "lose",
                     "launch", "riser", "top0", "top1"):
            for g in (1.0, 0.9, 0.85):     # 预热长音效的音量缓存
                self.play_prepare(name, g)
        warm = getattr(self.out, "warm", None)
        if warm is not None:
            warm()                          # 预开所有声道(每个 8.8ms, 放后台)

    def _bake_named(self):
        """命中缓存就直接加载 WAV; 否则边合成边落盘边加载 —— 每烘好一个立刻能播。

        手机上整库合成要好几秒(纯 Python 浮点循环), 若等整库烘完才 prime, 开局第一次
        蓄力必然一声不响; 而 iter_bank 的顺序里 ratchet 排在前 15%, 边烘边用就赶得上。
        stamp 记的是 "指纹 / 名字:字节数", 逐个核对大小 —— 只查存在性的话, 一个被截断的
        WAV 会被当成有效缓存永久加载失败, 而这正是最难发现的一类静默故障。
        """
        d = B._sfx_cache_dir()
        tag = B._sfx_code_tag()
        stamp = os.path.join(d, "stamp")
        if self._load_cached(d, stamp, tag):
            self.cached = True
            B._boot_log("bake", "缓存命中 = 热启动")
            n_voice = self._prime_voice()
            self._expected = self._n_bank + n_voice     # 满编 = 合成音 + 语音
            self._retry_failed()          # 缓存命中也可能有个别没加载上(语音是每次重载的)
            return
        B._boot_log("bake", "缓存未命中 = 冷启动(现场合成)")
        B._wav_wipe(d)
        lines = []
        n_bank = 0
        for name, pcm in iter_bank():
            if name == "flight":           # flight 音效已移除, 但 iter_bank 必须保留(顺序即音色)
                continue
            n_bank += 1                    # 满编数从**数据源**数, 与"加载成功几个"无关
            path = os.path.join(d, name + ".wav")
            try:
                B._wav_write(path, pcm)
                self.out.prime(name, path)
            except Exception:
                self._failed.append((name, path))
                continue
            self.named.add(name)
            lines.append("%s:%d" % (name, os.path.getsize(path)))
        self._n_bank = n_bank
        self._expected = n_bank + self._prime_voice()
        try:
            with open(stamp, "w") as f:
                f.write(tag + "\n" + "\n".join(lines))
        except Exception:
            pass
        # ⚠️ 先给解码线程一点时间。但**不要退回"把这个数调大"**: 既拖慢启动, 又救不了
        #    已经返回 0 的那批(那是**永久**失败, 归 `_retry_failed` 管)。
        time.sleep(0.5)
        self._retry_failed()

    def _retry_failed(self):
        """把加载失败的音效丢到后台重试几轮。

        首装走的是**冷路径**: 先把整库合成一遍(纯 Python 浮点循环), SoundPool 的解码线程
        被抢 CPU, `load()` 很容易返回 0; 而 `named` 是 play 的闸门(不在里面就直接跳过)
        —— 于是**整局一声不响**, 只有重启(走缓存、解码快)才恢复。
        ⚠️ 这条与闸门**不冲突**: 闸门只管"已经交给池的那批好了没有", 这里管的是
           "`load()` 当场返回 0、那个样本**从来没进过池**" —— 闸门永远等不到它。
        ⚠️ 每重试成功一次就多登记一个 sid ⇒ 闸门的"期望数"会后涨; 闸门在 Java 侧是**闩锁**的
           (开过一次就永远是开) —— 别改成实时重算, 否则就变成"闸门开了又关"。
        """
        todo, self._failed = self._failed, []
        if not todo:
            return

        def _worker(items):
            for _ in range(self._retry_rounds):
                time.sleep(self._retry_wait)
                left = []
                for name, path in items:
                    try:
                        self.out.prime(name, path)
                        self.named.add(name)
                    except Exception:
                        left.append((name, path))
                items = left
                if not items:
                    return

        try:
            threading.Thread(target=_worker, args=(todo,), daemon=True).start()
        except Exception:
            pass

    def _prime_voice(self):
        """预录语音直接 prime APK 内原文件(voice/*.wav), 不落缓存不进 stamp 指纹:
        每次启动都重新加载, 语音文件更新即生效。

        返回语音条数(满编数要用它 —— 它是数据源, 不是"加载成功几个")。"""
        n = 0
        B._boot_log("load", "开始 prime 语音 %d 条 (下方每条 ms 只量 load() 提交, "
                            "load 是异步的、不代表解码完)" % len(_voice_files()))
        for name, path in _voice_files().items():
            n += 1
            _l0 = time.perf_counter()
            try:
                self.out.prime(name, path)
            except Exception:
                self._failed.append((name, path))
                continue
            self.named.add(name)
            _sz = B._safe_size(path)
            B._LOAD_BYTES["voice"] += _sz
            B._boot_log("load", "  语音   %-12s %6.1f ms %7d B"
                       % (name, (time.perf_counter() - _l0) * 1000.0, _sz))
        return n

    def _load_cached(self, d, stamp, tag):
        """缓存有效(指纹一致 + 每个 WAV 大小对得上)则全部加载并返回 True。"""
        try:
            with open(stamp, "r") as f:
                head, _, body = f.read().partition("\n")
        except Exception:
            return False
        if head != tag:
            return False
        want = []
        for line in body.split("\n"):
            name, _, size = line.partition(":")
            if not name or not size.isdigit():
                return False
            if name == "flight":               # flight 已移除, 跳过缓存加载
                continue
            path = os.path.join(d, name + ".wav")
            try:
                if os.path.getsize(path) != int(size):
                    return False
            except OSError:
                return False
            want.append((name, path))
        if not want:
            return False
        self._n_bank = len(want)      # 满编的合成音数(面板的"满编"靠它)
        B._boot_log("load", "开始 prime 合成音 %d 个 (下方每条 ms 只量 load() 提交, "
                           "load 是异步的、不代表解码完)" % len(want))
        for name, path in want:
            _l0 = time.perf_counter()
            try:
                self.out.prime(name, path)
            except Exception:
                # ⚠️ 原来这里直接 `return False`, 上层见到 False 就走未命中分支 `wav_wipe(d)`
                #    —— **把整个缓存目录的 WAV 全删掉**再整库重合成。一次**偶发**的 load 失败
                #    代价被放大成"这一局从热启动变成冷启动"。
                #    ⇒ 改成"**先原地重试一次**": 偶发的一下就过了、缓存保住;
                #      两次都失败才判缓存真坏了 —— 那正是原来那条自愈路径的职责。
                B._boot_log("bake", "缓存 prime 失败, 原地重试: %s" % name)
                try:
                    self.out.prime(name, path)
                except Exception:
                    B._boot_log("bake", "重试仍失败 ⇒ 判缓存无效, 走重烘自愈: %s" % name)
                    return False      # 两次都失败: 缓存视为无效, 触发重新烘焙自愈
            _sz = B._safe_size(path)
            B._LOAD_BYTES["bank"] += _sz
            B._boot_log("load", "  合成音 %-12s %6.1f ms %7d B"
                       % (name, (time.perf_counter() - _l0) * 1000.0, _sz))
            self.named.add(name)
        return True

    def play_prepare(self, name, gain):
        pcm = self.bank.get(name)
        if pcm is None:
            return
        lvl = int(round(clamp(SFX_MASTER * gain, 0.0, 1.0) * 10.0))
        key = (name, lvl)
        if lvl > 0 and key not in self._scaled:
            self._scaled[key] = pcm if lvl >= 10 else B._scale_pcm(pcm, lvl / 10.0)

    def _voice_cut(self):
        """把还在念的那一句掐掉(新的语音要立刻念)。**后端不支持时静默跳过。**

        ⚠️ 单独抽出来是为了把"后端可能没有这个方法"(桌面 / 别的后端)收在一处 ——
           拿不到就什么都不做, 绝不抛。
        """
        try:
            _fn = getattr(self.out, "stop_voice", None)
            if _fn is not None:
                _fn()
        except Exception:
            pass

    def play(self, name, gain=1.0, throttle=0.0):
        if not self.enabled:
            return False
        now = time.time()
        # UI 交互语音互斥: 同族语音**不许叠着念**(叠着谁都听不清); 结果/轮次语音不在此限。
        # ⚠️ 用上一句的**真实时长**判, 不能写死 3 秒 —— 写死的话连按音效开关时第一句之后
        #    3 秒内全被挡, 按钮颜色在切而完全没声音, 音画脱节(实测连按 12 次只听到第 1 句,
        #    而"关闭声音"本身只有 0.9 秒)。
        # ⚠️ 撞上互斥时**掐掉旧的、念新的**(以前是 `return False`, **把新来的那句丢掉**):
        #    旧那句已经是**被玩家取消掉的旧信息**, 听完它反而让人以为新选择没生效。
        if name.startswith(("voice_rtp_", "voice_bet_", "voice_mode_")):
            if now - self._last_voice < self._last_voice_len:
                self._voice_cut()
            self._last_voice = now
            self._last_voice_len = self.voice_duration(name)
        pcm_mode = getattr(self.out, "mode", "pcm") == "pcm"
        if pcm_mode:
            pcm = self.bank.get(name)
            if pcm is None:                  # 还没烘焙好(启动后 ~350ms 内)
                return False
        elif name not in self.named:          # 还没落盘/加载好
            return False
        lvl = int(round(clamp(SFX_MASTER * gain, 0.0, 1.0) * 10.0))
        if lvl <= 0:
            return False
        if throttle > 0.0:
            _tk = _throttle_key(name)      # 按族计时
            if now - self._last.get(_tk, 0.0) < throttle:
                return False
            self._last[_tk] = now
        if pcm_mode:
            key = (name, lvl)
            data = self._scaled.get(key)
            if data is None:
                data = pcm if lvl >= 10 else B._scale_pcm(pcm, lvl / 10.0)
                self._scaled[key] = data
            B._FRAME_PROBE[0] += 1
            if self._q is not None:
                self._enqueue(("pcm", data, name))
            else:
                self._backend_call(("pcm", data, name))
            return True
        # ⚠️ 必须把这个返回值存下来再返回: 它和上面那几个"设计内静默"的 return False 长得
        #    一模一样, 但语义完全不同 —— 这一条是"过了 enabled/互斥/已加载/增益/节流五道
        #    闸门之后, 后端仍然说没播成", 也就是「首次安装必然没声音」的**正面计数**。
        #    节流那一支在它上面提前 return, 天然被排除。
        B._FRAME_PROBE[0] += 1
        self.n_attempt += 1
        if self._q is not None:
            # ⚠️ 投递就返回 True —— 与同步路径"后端受理了"同义。返回值还牵着**震动**
            #    (`_vibrate_tick` 挂在它上面), 所以这里绝不能因为"异步还不知道成败"就返回
            #    False, 那会让装杯的震动整段消失。
            self._enqueue(("named", lvl / 10.0, name))
            return True
        _t0 = time.perf_counter()
        ok = self.out.play_named(name, lvl / 10.0)
        _dt = time.perf_counter() - _t0
        B._SND_STAT[0] += _dt
        if _dt * 1000.0 > B.SND_SLOW_MS:
            B._SND_STAT[3] += 1
        if _dt > B._SND_STAT[1]:
            B._SND_STAT[1] = _dt
            B._SND_STAT[2] = name
        if not ok:
            self.n_missed += 1
        return ok

    def _enqueue(self, item):
        """投递给发声工作线程。**队列满就丢这一声** —— 绝不阻塞主线程(见 `_drain`)。"""
        try:
            self._q.put_nowait(item)
        except Exception:
            pass

    def peg_variant(self, t):
        """`peg_variant(t, self)` 的实例版(调用方拿得到 `Sfx` 时用这个)。

        ⚠️ 抽变体的入口**只此一处**(加上模块级那个同名函数 —— 它俩是同一个实现)。
           `game.py` 的 `impact_sound()` 是模块级函数、拿不到 Sfx, 它要调模块级那个并把
           `gate` 传成 UI 持有的 Sfx 实例 —— 两边都在**过了闸之后**才抽, 不许各抽各的。
        """
        return peg_variant(t, self)

    def impact(self, bit, sp):
        """碰撞音: 撞得越猛越响越亮; 低于阈值不发声。"""
        if not self.enabled or sp < SFX_MIN_SP.get(bit, 0.0):
            return False
        t = clamp(sp / SFX_REF_SP.get(bit, 900.0), 0.0, 1.0)
        if bit == EV_PEG:
            # ⚠️ 变体必须走 `self.peg_variant`(它抽的是与烘焙**共用**的那条 `_ARNG`)。
            #    在播放层另起一条同种子流不会报错, 只会让变体序列整体漂移。
            idx = self.peg_variant(t)
            if idx is None:
                return False          # 上面那道闸已放行 ⇒ 只有 enabled 被并发关掉才到这儿
            return self.play("peg%d" % idx, 0.30 + 0.70 * t, 0.08)
            # throttle 0.038→0.08: 机关枪连珠(间隔<0.08s)只响第一声
        if bit == EV_CEIL:
            return self.play("rail", 0.45 + 0.55 * t, 0.22)
        if bit == EV_WALL:
            return self.play("wall%d" % (1 if t > 0.5 else 0), 0.35 + 0.65 * t, 0.055)
        if bit == EV_DIV:
            return self.play("div%d" % (1 if t > 0.5 else 0), 0.35 + 0.65 * t, 0.055)
        return False

    def top(self, y):
        """顶部碰撞: 球冲到最高点转向时发声(y = 转向高度, 越小 = 蓄力越足 = 撞得越实)。

        不走 impact 是因为 apex 处法向速率≈0, 按速率定音量就等于不发声。
        """
        t = clamp((SFX_APEX_Y_LO - y) / (SFX_APEX_Y_LO - SFX_APEX_Y_HI), 0.0, 1.0)
        return self.play("top%d" % (1 if t > 0.5 else 0), 0.62 + 0.38 * t)

    def voice_duration(self, name):
        """语音片段时长(秒), 用于队列播放的调度间隔。

        pcm 后端: bank 中有 PCM → 按字节数算; named 后端: 从 voice 目录的 WAV 文件大小推算。
        ⚠️ 这是语音**时长**的唯一真源 —— 互斥精度与队列调度都靠它。
        """
        pcm = self.bank.get(name)
        if pcm:
            return len(pcm) / (SR * 2.0)
        # named 后端: bank 里没有 PCM, 查 voice 目录文件大小(44 = WAV 头)
        path = _voice_files().get(name)
        if path:
            try:
                return (os.path.getsize(path) - 44) / (SR * 2.0)
            except OSError:
                pass
        return 0.15  # 回落值(典型单字约 200ms)

    def close(self):
        if self.out is not None:
            self.out.close()
            self.out = None
        self.enabled = False

    def pause_out(self):
        """切后台: 暂停输出(winmm 无暂停概念, 跳过)。"""
        m = getattr(self.out, "pause", None)
        if m is not None:
            m()

    def resume_out(self):
        m = getattr(self.out, "resume", None)
        if m is not None:
            m()

    def set_enabled(self, on):
        """静音开关: 关时暂停输出, 开时**只放开 enabled**, 不 resume。

        ⚠️ 开的时候**不能** autoResume: 静音那一刻 autoPause() 会把当时正在播的流掐在半路,
           一 resume 就从半路接着播出来 —— 症状是"连按音效开关会听到半句上一句提示音",
           而且连按越频繁越明显。
        autoResume 只该服务于"切后台回来"(on_resume → resume_out)。
        (SoundPool.autoPause 只遍历已分配的 channel 去暂停, 不会挡住后续 play() 新建
         channel, 所以这里不 resume 不影响"取消静音后能不能出声"。)
        """
        self.enabled = bool(on)
        if not on:
            self.pause_out()


# ⚠️ N_VOICE 只做满编数校验用(见 voice.N_VOICE), 这里 re-export 是为了让调用方
#    不必同时 import 两个模块。
__all__ = ["Sfx", "_throttle_key", "peg_variant", "N_VOICE", "SFX_MASTER", "SFX_MIN_SP",
           "SFX_REF_SP", "SFX_APEX_Y_LO", "SFX_APEX_Y_HI"]
