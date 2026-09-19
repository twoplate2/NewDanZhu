"""音频输出后端: SoundPool(安卓, 双池) / winmm(Windows) / Kivy SoundLoader / 静音。

三种接口模式:
  "pcm"   —— winmm: Sfx 把缩放后的裸 PCM 直接送声卡 (play_pcm)
  "named" —— SoundPool/SoundLoader: 音效先落盘成 WAV, 按名字播, gain 就是音量

⚠️ 降级链每一级都要把**异常原文**记进 `_BACKEND_ERRORS`。这块面板存在的全部理由就是
   抓「静默降级」, 所以它自己不许再做静默降级 —— 否则玩家的安卓机上 SoundPool 一直
   建不起来、静默跑在 Kivy 后端上, 面板只能说"后端是 Kivy"而说不出**为什么**。
"""

import array
import os
import struct
import sys
import tempfile
import threading
import time

from .synth import SFX_SEED, SR, pcm_to_wav


# ============================== 平台 ======================================
def _detect_platform():
    """kivy.utils.platform, 取不到时退回等价判定(桌面自测要能不开 Kivy 跑起来)。"""
    try:
        from kivy.utils import platform as _p
        return _p
    except Exception:
        pass
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_ROOT"):
        return "android"
    return "win" if os.name == "nt" else sys.platform


platform = _detect_platform()


# ============================== 启动日志 ==================================
# ⚠️⚠️ **必须 import, 不许在这里自己建一份**。`_BOOT_LOG` / `_PROBE_TRACE` / `_PROBE_COST`
#    是跨模块共享状态(bus.py 通过 `B._boot_log` 往这里写), 各建一份**不会报错**, 只会让
#    导出的启动日志**静默少掉一半行** —— 而读它的只有"保存加载日志"那一处, 没人对账。
#    唯一定义点在 `danzhu/platform/boot.py`。
from ..platform.boot import (          # noqa: E402  (放在此处是为了贴近原来的阅读顺序)
    _BOOT_LOG, _BOOT_T0, _PROBE_COST, _PROBE_TRACE, _boot_log,
)

# 两段各自提交的字节数(合成音 / 语音), 只给启动日志算"每 MB 多少 ms"。
_LOAD_BYTES = {"bank": 0, "voice": 0}


def _safe_size(path):
    """文件字节数; 拿不到就 0 —— 只给启动日志用, 不因为一次 stat 失败把加载搞崩。"""
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _cpu_groups():
    """按 max_freq 分组的 CPU: `[(频率kHz, [下标...]), ...]`, 从快到慢。读不到返回 []。

    ⚠️ _cpu_shape() 与探针的"钉大核"共用这一份读数, 不许各读一遍。
    """
    try:
        _n = os.cpu_count() or 0
        _byf = {}
        for _i in range(_n):
            try:
                with open("/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq" % _i, "r") as _f:
                    _v = int(_f.read().strip())
            except Exception:
                continue
            if _v > 0:
                _byf.setdefault(_v, []).append(_i)
        return [(int(_k), list(_v)) for _k, _v in sorted(_byf.items(), reverse=True)]
    except Exception:
        return []


def _cpu_shape():
    """CPU 簇结构 + 各簇频率, 给启动日志用: `("1+3+4", "4x2016M + 3x2745M + 1x3187M 当前 ...")`。

    ⚠️ 光写"8 核"区分不了老机(1+3+4, 最慢 2.0GHz)和新机(2+6, 最慢 3.62GHz), 而同一个
       play() 的单价在那两台机器上差 26 倍 —— 这一行就是为这件事单独写的。
    读不到(PC / 权限 / 核离线)返回空串, 调用方整行不出现 —— 绝不编数。
    """
    try:
        _grp = _cpu_groups()
        if not _grp:
            return "", ""
        _shape = "+".join(str(len(_v)) for _k, _v in _grp)
        _mhz = " + ".join("%dx%dM" % (len(_v), int(_k) // 1000) for _k, _v in _grp)
        _cur = []
        for _k, _v in _grp:                       # 每簇抽一个核看**当前**频率
            try:
                with open("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq" % _v[0], "r") as _f:
                    _cur.append(int(_f.read().strip()) // 1000)
            except Exception:
                pass
        if len(_cur) == len(_grp):
            _mhz += "　当前 " + " / ".join("%dM" % _c for _c in _cur)
        return _shape, _mhz
    except Exception:
        return "", ""


def _probe_boost_thread():
    """把**当前这条线程**钉到大核簇 + 提到 THREAD_PRIORITY_AUDIO(探针实验档, 失败静默)。

    ⚠️ 只为把"CPU 状态"这个变量钉死, 别指望收益; 只动当前线程, 主线程/渲染照旧。
    """
    try:
        _g = _cpu_groups()
        if _g:
            _mask = set(_g[0][1]) | (set(_g[1][1]) if len(_g) > 1 else set())
            if _mask:
                os.sched_setaffinity(0, _mask)
    except Exception:
        pass
    try:
        from jnius import autoclass
        # AOSP: THREAD_PRIORITY_AUDIO = -16(URGENT_AUDIO 才是 -19)
        autoclass("android.os.Process").setThreadPriority(-16)
    except Exception:
        pass


# ============================== 常量 ======================================
SFX_VOICES = 8               # 并发声道数 = **每个池**的上限(SoundPool maxStreams)
# ⚠️ 第二个池只装语音。AOSP 每个 SoundPool 的解码线程数硬编码 2
#    (kDecoderThreads = hw_concurrency>=4 ? 2 : 1); 再开一个池就是 4 条。
#    置 False 即退回单池, 行为与加这个开关之前逐字相同(出问题时的回退路径)。
SFX_POOL_SPLIT = True
# 闸门总开关。置 False ⇒ _gate_err 记原因, 全程走老探针(绝不降级后端)。
# ⚠️ **打包时必须把 java/ 打进 APK(老工程 buildozer.spec 的 `android.add_src = java`)**,
#    否则 `com.plinko.SoundGate` 取不到 ⇒ 闸门**永远不可用**、静默回退慢探针: 功能不坏,
#    但冷启动要多等探针那 3~5 秒, 而且启动日志里只有一行「闸门不可用」。
#    本版没有 buildozer.spec(不建), 这条约束只写在这里 —— 打包脚本一定要带上 java/。
SFX_GATE = True

GATE_MODE_OFF = "off"        # 只跑老探针(v0.8.60 那条路)
GATE_MODE_ONLY = "only"      # 只等闸门, 老探针一次都不跑(已退休, 保留可复活)
GATE_MODE_FIRST = "first"    # 闸门优先, 到点不开才转老探针 = 出货默认
ARM_LADDER = (
    (GATE_MODE_FIRST, ""),        # A 出货形态
    (GATE_MODE_OFF, ""),          # B 老探针对照
    (GATE_MODE_FIRST, "pools"),   # C 池数对拍(临时池, 不碰出货路径)
)
# ⚠️ 实现体在本文件末尾(`_arm48_bench` / `_arm_pools_bench`, 连同 `_warm_all` / `_med` /
#    `_resample16` / `_wav_pcm_read` / `_wav_write_sr`)。**退休档**(从元组里摘掉就再也选不到,
#    代码整段留着可复活)与它们的结论 —— 别再为了"确认一下"重跑:
#    · `("first", "48k")`: 22050 中位 56.3ms vs 48000 55.7ms、字节比 2.16x ⇒ **采样率不是
#      杠杆**, 定价按样本数(`Sfx._await_ready` 里那个 `"48k"` 分支保留可复活)。
#    · pools 档: 2 池 930/984ms vs 4 池 741/747ms(语音拆半 691) ⇒ **只快 21%, 不是腰斩**;
#      瓶颈在每个音效都要跨进程找**共用的那个解码服务**要资源 ⇒ 多开池这条路关闭
#      (档位保留: 换机器顺带量, 零风险)。
#    · `(GATE_MODE_ONLY, "")`: 只等闸门、老探针一次都不跑。它与 FIRST 只差"闸门不开时"那一段,
#      而那正是 FIRST 的兜底该管的 ⇒ 已无信息量(`_g_fallback` 那一支保留)。

# 发声"慢调用"判据(毫秒)。20 已远超一帧余量(帧间隔 12.5ms), 落在这儿就一定有问题。
SND_SLOW_MS = 20.0
_SND_STAT = [0.0, 0.0, "", 0.0]      # [累计秒, 单次最慢秒, 最慢的名字, 超阈次数]
# 逐帧计数 [本帧发声次数, 本帧震动次数, 本帧是否跑了预热]; 只在跑分采样期读。
_FRAME_PROBE = [0, 0, 0]


# ============================== winmm (pcm) ===============================
_WAVE_MAPPER = 0xFFFFFFFF
_WHDR_DONE = 1

try:                                        # winmm: 唯一能做多声道叠加的 stdlib 路径
    import ctypes

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
                    ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                    ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
                    ("cbSize", ctypes.c_uint16)]

    class _WAVEHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
                    ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
                    ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_void_p)]

    _winmm = ctypes.WinDLL("winmm")
    _winmm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                   ctypes.POINTER(_WAVEFORMATEX), ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_uint32]
    for _fn in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
        getattr(_winmm, _fn).argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(_WAVEHDR), ctypes.c_uint]
    _winmm.waveOutReset.argtypes = [ctypes.c_void_p]
    _winmm.waveOutClose.argtypes = [ctypes.c_void_p]
    if _winmm.waveOutGetNumDevs() <= 0:     # 无声卡: 别白费力气开设备
        _winmm = None
except Exception:
    _winmm = None


class _WaveOut:
    """winmm 多声道输出: 多个音效真正同时响, 且写入不阻塞 GUI。

    两条慢路径必须避开(实测): waveOutOpen 8.8ms/次 -> 后台 warm() 预开;
    waveOutReset 抢占 10ms/次 -> 声道全忙时直接丢弃(丢一声听不出, 卡一帧看得出)。"""

    mode = "pcm"

    def __init__(self, voices=SFX_VOICES):
        if _winmm is None:
            raise OSError("winmm unavailable")
        self._fmt = _WAVEFORMATEX(1, 1, SR, SR * 2, 2, 16, 0)
        self._hsz = ctypes.sizeof(_WAVEHDR)
        self._n = voices
        self._h = [None] * voices
        self._hdr = [None] * voices
        self._buf = [None] * voices
        self._lock = threading.Lock()
        self.drops = 0
        h = self._open()
        if h is None:
            raise OSError("waveOutOpen failed")
        self._h[0] = h

    def _open(self):
        h = ctypes.c_void_p()
        if _winmm.waveOutOpen(ctypes.byref(h), _WAVE_MAPPER,
                              ctypes.byref(self._fmt), None, None, 0) != 0:
            return None
        return h

    def warm(self):
        """预开所有声道(后台线程调用): 避开游戏中途 8.8ms 的开设备卡顿。"""
        with self._lock:
            for i in range(self._n):
                if self._h[i] is None:
                    h = self._open()
                    if h is None:
                        break
                    self._h[i] = h

    def _alloc(self):
        for i in range(self._n):
            if self._h[i] is not None and (self._hdr[i] is None or
                                           (self._hdr[i].dwFlags & _WHDR_DONE)):
                return i
        return None                         # 全忙: 丢弃(不抢占, 抢占要 10ms)

    def play_pcm(self, pcm):
        with self._lock:
            i = self._alloc()
            if i is None:
                self.drops += 1
                return
            h = self._h[i]
            if self._hdr[i] is not None:
                _winmm.waveOutUnprepareHeader(h, ctypes.byref(self._hdr[i]), self._hsz)
                self._hdr[i] = None
            buf = ctypes.create_string_buffer(pcm, len(pcm))
            hdr = _WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(pcm)
            if _winmm.waveOutPrepareHeader(h, ctypes.byref(hdr), self._hsz) != 0:
                return
            if _winmm.waveOutWrite(h, ctypes.byref(hdr), self._hsz) != 0:
                _winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr), self._hsz)
                return
            self._hdr[i] = hdr              # 保活: header 与 buffer 必须活到播完
            self._buf[i] = buf

    def close(self):
        with self._lock:
            for i in range(self._n):
                h = self._h[i]
                if h is None:
                    continue
                try:
                    _winmm.waveOutReset(h)
                    if self._hdr[i] is not None:
                        _winmm.waveOutUnprepareHeader(h, ctypes.byref(self._hdr[i]), self._hsz)
                    _winmm.waveOutClose(h)
                except Exception:
                    pass
                self._h[i] = self._hdr[i] = self._buf[i] = None

    @property
    def name(self):
        return "winmm(%d声道)" % self._n


def _scale_pcm(pcm, g):
    """PCM 整体缩放(增益量化缓存用)。"""
    a = array.array("h")
    a.frombytes(pcm)
    for i in range(len(a)):
        a[i] = int(a[i] * g)
    return a.tobytes()


# ============================== 落盘缓存 ==================================
def _sfx_cache_dir():
    """音效 WAV 落盘目录(named 后端要文件路径; winmm 直接播 PCM 用不到)。"""
    if platform == "android":
        try:
            from kivy.app import App
            base = App.get_running_app().user_data_dir
        except Exception:
            base = tempfile.gettempdir()
    else:
        base = tempfile.gettempdir()
    d = os.path.join(base, "plinko_sfx")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _audio_src_files():
    """本包的源码文件(缓存指纹的覆盖面)。"""
    d = os.path.dirname(os.path.abspath(__file__))
    try:
        return [os.path.join(d, fn) for fn in os.listdir(d) if fn.endswith(".py")]
    except Exception:
        return [os.path.abspath(__file__)]


def _sfx_code_tag():
    """缓存指纹: 配方文件的 mtime+size + 合成种子 + 采样率。

    配方改了 -> 音效包变了 -> 指纹变 -> 旧 WAV 整目录作废, 不会拿旧配方冒充新的。
    ⚠️ 覆盖面是**整个 audio 包**: 只盯 synth.py 会漏掉播放层的改动。
    """
    try:
        _mt, _sz = 0, 0
        for _f in _audio_src_files():
            st = os.stat(_f)
            _mt = max(_mt, int(st.st_mtime))
            _sz += st.st_size
        return "%d.%d.%d.%d" % (SFX_SEED, SR, _mt, _sz)
    except Exception:
        return "%d.%d.nofile" % (SFX_SEED, SR)


def _wav_write(path, pcm):
    """原子写: 先写 .tmp 再 replace。半截文件绝不能留在缓存里被下次启动当成有效音效。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(pcm_to_wav(pcm))
    os.replace(tmp, path)


def _wav_wipe(d):
    for fn in os.listdir(d):
        if fn.endswith(".wav") or fn.endswith(".tmp") or fn == "stamp":
            try:
                os.remove(os.path.join(d, fn))
            except Exception:
                pass


def _read_wav_pcm(path):
    """读 22050Hz 16bit mono wav -> 裸 PCM 字节(winmm pcm 模式用; 格式不符直接拒)。"""
    import wave
    with wave.open(path, "rb") as wf:
        if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (1, 2, SR):
            raise ValueError("voice wav 不是 %dHz 16bit mono: %s" % (SR, path))
        return wf.readframes(wf.getnframes())


# ============================== SoundPool (named) =========================
def _mk_soundpool(voices):
    """建一个 SoundPool(jnius)。**唯一**的构造点 —— 两个池与实验临时池都走它。

    ⚠️ 只在安卓上有意义(桌面 import jnius 就抛)。
    """
    from jnius import autoclass

    def _inner(outer_name, inner):
        """取 Java **内部类**, 优先 `Outer$Inner` 这种规范写法。

        ⚠️ pyjnius 对 `Outer.Inner` 属性访问的解析不可靠, 而它一旦抛异常就会被
           open_output 的 except 吞掉 ⇒ 整台设备静默降级到 Kivy 后端。
           真机实测过: 面板显示 `Kivy-SoundLoader`, 即 SoundPool 从来没建起来过。
        """
        try:
            return autoclass("%s$%s" % (outer_name, inner))
        except Exception:
            return getattr(autoclass(outer_name), inner)

    AudioAttributes = autoclass("android.media.AudioAttributes")
    attrs = (_inner("android.media.AudioAttributes", "Builder")()
             .setUsage(AudioAttributes.USAGE_GAME)
             .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
             .build())
    return (_inner("android.media.SoundPool", "Builder")()
            .setMaxStreams(voices)
            .setAudioAttributes(attrs)
            .build())


class _SoundPoolOut:
    """Android SoundPool: 短音效全部解压进内存, 并发交给硬件 mixer。

    就绪判据 = 纯 Java 的 OnLoadCompleteListener 闸门(`com.plinko.SoundGate`),
    Python 只做下行查询 —— 见 `_attach_gates`。那条老否决理由("PythonJavaClass 代理
    失灵/被 GC ⇒ 全库永久静音")否的是桥, 不是这个接口; 闸门是 Java 对象、由 Handler
    强引用, 没有 GC 风险。

    ⚠️ `needs_worker = True`: 真机实测 `play()` 会阻塞调用线程几十到一百多毫秒
       (Y700 二代: 50 次累计 2674ms, 单次最慢 143.6ms, 而帧间隔只有 12.5ms)
       ⇒ Sfx 起工作线程, 主线程只投递不等待。只给这一个后端开。
    """
    mode = "named"
    name = "SoundPool"
    needs_worker = True

    def __init__(self, voices=SFX_VOICES):
        self._voices = voices
        # ⚠️ 两张 id 表都从这里建 —— **唯一的一处**(见 `_reset_pools`)。
        self._reset_pools()
        # 以下三个只给启动日志用, 没有任何逻辑读它们做判断。
        self._probe_scan = 0        # 从队首起**连续**就绪的个数
        self._probe_played = 0
        self._probe_stuck = ""
        # 池的**代次**: `_reset_pools` 每重建一次 +1 —— 并行探针的 worker 回写
        # `_ok_sids` 前对一下它, 免得把旧池(sampleId 也从 1 编号)的结果当成新池的 ⇒ 假绿。
        self._probe_gen = 0
        # ⚠️ **只增不减**(连 _rebuild 都不清): 启动日志用它判定「闸门活口」在真机上发生过几次。
        #    不能用 Sfx._failed —— 那个在 _retry_failed 第一行就被搬空。
        self._load_failed_total = 0
        # "**后端没播成**"的正面计数: play() 返回 0。运行期发声全走工作线程的异步路径,
        # 那一声没响**没人知道** —— 面板只在非 0 时印它。
        self._play_missed = 0
        self._play_missed_last = ""
        # 本次会话一共试了几次 play(只给"没播成"那条日志用)。
        self._play_total = 0
        # "还在念的那一条流"的流号。⚠️ 流号是**池内**编号 ⇒ 换池必须清零(见 _reset_pools/close)。
        self._voice_stream = 0
        self._paths = {}              # name -> wav 路径(重建时重新 load 用)
        self.rebuild_count = 0        # 正常局应恒为 0
        _t0 = time.perf_counter()
        self._sp = self._build_sp()
        # ⚠️ 第二个池: 语音走它 ⇒ 两条池各有 2 条解码线程, 并行度 2 -> 4。
        self._sp2 = self._build_sp() if SFX_POOL_SPLIT else None
        # ⚠️ 闸门必须**在建完池、任何 load() 之前**挂上 —— 回调是一次性的, 漏掉的永远不来。
        self._gate = None
        self._gate2 = None
        self._gate_err = ""
        self._attach_gates()
        try:
            _boot_log("sfx", self.gate_info())
        except Exception:
            pass
        _boot_log("sfx", "SoundPool 构造 %.1f ms (maxStreams=%d × %d 池)"
                 % ((time.perf_counter() - _t0) * 1000.0, self._voices,
                    2 if self._sp2 is not None else 1))
        self._receiver = None
        self._register_noisy_receiver()

    def _build_sp(self):
        return _mk_soundpool(self._voices)

    def _register_noisy_receiver(self):
        """路由一变就重建 SoundPool。

        - **拔**耳机: 底层音频流被系统断开, 不重建就永久无声, 只能重启。
        - **插**耳机: SoundPool 的输出路由是**建流那一刻**定下的, 系统插耳机的事件不会把
          已有流搬过去 ⇒ 玩家报的「玩的时候插耳机, 耳机里没声音; 重启 app 就有了」。
        ⚠️ ACTION_HEADSET_PLUG 是 **sticky** 广播 —— registerReceiver 会立刻回放"当前状态",
          所以必须记下初值、只在**状态真的变了**时才重建; 否则每次启动都白重建一次
          (那时 sample 还没 load 完, 会丢音)。
        receiver 存到 self._receiver 保活, 防被 GC。
        """
        try:
            from jnius import autoclass, PythonJavaClass, java_method
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            AudioManager = autoclass('android.media.AudioManager')
            IntentFilter = autoclass('android.content.IntentFilter')
            _plug_action = str(AudioManager.ACTION_HEADSET_PLUG)

            class _RouteReceiver(PythonJavaClass):
                __javainterfaces__ = ['android/content/BroadcastReceiver']
                __javacontext__ = 'app'

                def __init__(self, cb):
                    super().__init__()
                    self.cb = cb
                    self._plugged = None          # None = 还没收到过插拔状态

                @java_method('(Landroid/content/Context;Landroid/content/Intent;)V')
                def onReceive(self, context, intent):
                    try:
                        if str(intent.getAction()) == _plug_action:
                            st = intent.getIntExtra("state", -1)
                            if self._plugged is None:
                                # sticky 回放: **只记初值、绝不重建**(初值是 None 时
                                # `if st == self._plugged: return` 恒假 ⇒ 每次启动白重建)。
                                self._plugged = st
                                return
                            if st == self._plugged:
                                return            # 真·状态没变: 不动
                            self._plugged = st
                    except Exception:
                        pass
                    self.cb()

            self._receiver = _RouteReceiver(self._rebuild)
            flt = IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY)
            flt.addAction(AudioManager.ACTION_HEADSET_PLUG)
            PythonActivity.mActivity.registerReceiver(self._receiver, flt)
        except Exception:
            self._receiver = None

    def prime(self, name, path):
        self._paths[name] = path             # ⚠️ 先记"应到", 再 load: load 失败的名字也要进
        # ⚠️ **分池规则只有这一处**: 语音走第二个池, 其余走第一个。按名字前缀判, 不引第二份清单。
        _use2 = (self._sp2 is not None) and name.startswith("voice_")
        _pool = self._sp2 if _use2 else self._sp
        sid = _pool.load(path, 1)
        if not sid:
            self._load_failed_total += 1
            raise RuntimeError("SoundPool.load failed: " + path)
        if _use2:
            self._ids2[name] = sid
            _g = getattr(self, "_gate2", None)
        else:
            self._ids[name] = sid
            _g = getattr(self, "_gate", None)
        if _g is not None:
            # ⚠️ 登记"我要等这个 sid", **必须按池分开** —— 两个池的 sampleId 各自从 1 编号,
            #    混着数会撞号假绿。
            try:
                _g.expect(sid)
            except Exception:
                pass

    def _reset_pools(self):
        """清空**两张** id 表 —— **唯一的一处**。

        ⚠️ `_ids` 与 `_ids2` 必须在同一处一起清。本项目头号杀手就是「同一份清单出现在
           两处, 改一处忘另一处 ⇒ 静默脱钩」。**任何地方都不许单独写 `self._ids = {}`**。
        """
        self._ids = {}
        self._ids2 = {}
        # K5 增量探针的缓存: 已亲眼确认"能播"的 sid 集合。⚠️ 必须与两张 id 表同生命周期。
        # ⚠️ 键是 **(池序号, sid)**, 必须带池序号 —— 两池的 sampleId 各自从 1 编号,
        #    只用纯 sid 当键会让池 2 前 40 个撞上池 1 已确认过的 ⇒ 从没验证过就被放行。
        self._ok_sids = set()
        self._probe_gen = getattr(self, "_probe_gen", 0) + 1
        # ⚠️ 流号是池内编号 ⇒ 换池必须清零, 否则会拿旧池的流号去 stop 新池的一条流(静默停错)。
        self._voice_stream = 0

    def replay_reset(self):
        """「重放冷启动」专用: 把两个池**整个换新**(连同闸门), 并清空应到清单。

        ⚠️ 与 `_rebuild` 的区别: 这里**不**按 `_paths` 重新 load —— 调用方紧接着会跑一次
           真正的冷烘焙, 那一步会把全部样本重新 prime 进来。
        ⚠️ 不换池的话 `_ok_sids` 已记满(探针空转、量不出单价), 闸门也早闩上了。
        """
        try:
            _old = (self._sp, getattr(self, "_sp2", None))
            self._reset_pools()
            self._paths = {}
            self._sp = self._build_sp()
            self._sp2 = self._build_sp() if SFX_POOL_SPLIT else None
            self._attach_gates()
            for _p in _old:
                try:
                    if _p is not None:
                        _p.release()
                except Exception:
                    pass
        except Exception:
            pass

    def _rebuild(self):
        """路由变了(插/拔耳机)后重建: release 旧的, 建新池, 把**应到清单**全部重新 load。

        ⚠️ 遍历必须用**快照**: 冷路径下烘焙线程正在往 `_paths` 里塞东西, 直接迭代它会在
           "字典在迭代中被改"时抛 RuntimeError 并被外层吞掉 → 循环当场中断、`_ids` 停在半路
           —— 而 Sfx.named 早已被烘焙线程填满 ⇒ **闸门放行、后端没有 sid ⇒ 整局静默**。
        ⚠️ 跑在 Android **主线程**(onReceive 默认线程): 持锁/长循环会卡 UI, 所以只做
           "重建 + 一轮对账", 不做等待。
        """
        try:
            self.rebuild_count += 1
            _old_sp, _old_sp2 = self._sp, self._sp2
            self._reset_pools()
            self._sp = self._build_sp()
            self._sp2 = self._build_sp() if SFX_POOL_SPLIT else None
            # ⚠️ 新池要**先挂闸门再 prime** —— 闸门装晚了, 已解完的样本回调永远不来。
            self._attach_gates()
            for _p in (_old_sp, _old_sp2):
                try:
                    if _p is not None:
                        _p.release()
                except Exception:
                    pass
            _reload_failed = 0
            for name, path in list(self._paths.items()):
                try:
                    self.prime(name, path)
                except Exception:
                    _reload_failed += 1
            if _reload_failed:
                # ⚠️ 只留痕, 不假装有人会重试: Sfx._failed 只由冷路径合成循环与语音循环 append。
                _boot_log("sfx", "重建后重新 load 失败 %d 个 —— 没有任何人会重试它们"
                         % _reload_failed)
        except Exception:
            pass

    def loaded_count(self):
        """后端**真的**握着几个能播的 sampleId。

        ⚠️ 不能拿 Sfx.named 冒充它 —— 病灶正是"闸门满、后端缺"。
        """
        return len(self._ids) + len(self._ids2)

    def probe_all(self):
        """按**实验开关**分派: 默认走 `_probe_serial`, 开关打开才走并行。

        ⚠️ 分派这一层是唯一的新增行为; 关掉开关时走的是原样搬过去的老函数。
        ⚠️ 两条路的对外读数同义: `_probe_scan` 从队首起连续就绪数 / `_probe_played`
           这一轮真 play 次数 / `_probe_stuck` 声明顺序里第一个没就绪的。
        """
        _mode, _boost = self._probe_cfg()
        if _boost:
            # ⚠️ 必须在分派之前设 —— 否则 (1,1)+boost 会因为"有 boost 就不走串行"
            #    而变成"两池各一条线程 + boost"(同时改两个变量), 那一档就隔离不出 CPU 那一份。
            _probe_boost_thread()
        if _mode[0] <= 1 and _mode[1] <= 1:
            return self._probe_serial()
        return self._probe_parallel(_mode)

    # ---- 纯 Java 的「样本就绪闸门」(见 java/com/plinko/SoundGate.java) ----
    #   ⚠️ 这一整块在桌面上**永远**是"不可用"路径: 取不到 jnius/Java 类 ⇒ 三个查询
    #      返回 0/False ⇒ `_await_ready` 走老探针。
    def _attach_gates(self):
        """给两个池各挂一个闸门。**必须在任何 load() 之前调**(回调是一次性的)。

        ⚠️ 拿不到类一律静默置 None —— **绝不抛、绝不降级后端**
           (项目有过"SoundPool 没建起来、静默降级到 Kivy 后端"的真机事故)。
        ⚠️ 拿不到类的**最常见原因**是打包漏了 `java/`(老工程 buildozer.spec 的
           `android.add_src = java`)—— 那种包功能照跑(静默回退慢探针), 只是冷启动多等
           3~5 秒。看到 `_gate_err` 里是「类取不到」就先查这一条。
        """
        self._gate = None
        self._gate2 = None
        self._gate_err = ""
        if not SFX_GATE:
            self._gate_err = "总开关 SFX_GATE = False"
            return
        try:
            from jnius import autoclass
            _G = autoclass("com.plinko.SoundGate")
        except Exception as _e:
            self._gate_err = "类取不到: %s" % (repr(_e)[:60],)
            return
        for _pi, _p in ((0, self._sp), (1, getattr(self, "_sp2", None))):
            if _p is None:
                continue
            try:
                _g = _G(_p)          # 构造时自己 setOnLoadCompleteListener
            except Exception as _e:
                self._gate_err = "构造失败: %s" % (repr(_e)[:60],)
                continue
            if _pi == 0:
                self._gate = _g
            else:
                self._gate2 = _g

    def _gates(self):
        """两个池的闸门对象(可能只有一个, 也可能一个都没有)。"""
        return [_g for _g in (getattr(self, "_gate", None), getattr(self, "_gate2", None))
                if _g is not None]

    def gate_expected(self):
        """两池一共登记了多少个"我要等"的样本。0 ⇒ 闸门不可用。"""
        try:
            return sum(int(_g.expectCount()) for _g in self._gates())
        except Exception:
            return 0

    def gate_ready_count(self):
        try:
            return sum(int(_g.readyCount()) for _g in self._gates())
        except Exception:
            return 0

    def gate_all_ready(self):
        """**两池都**全部解码完才为真(任一个池差一个都不算)。"""
        _gs = self._gates()
        if not _gs:
            return False
        try:
            return all(bool(_g.allReady()) for _g in _gs)
        except Exception:
            return False

    def gate_ready_ms(self):
        """两池都满时 = 两池里**最晚**那个的 readyMs; 否则 -1。

        ⚠️ 这个数是闸门自己在 Java 里掐的表(从**第一个 load** 到最后一个样本就绪),
           不含 Python 的轮询间隔 —— 比"轮询发现它的时刻"准。
        """
        _gs = self._gates()
        if not _gs:
            return -1.0
        try:
            _v = [float(_g.readyMs()) for _g in _gs]
        except Exception:
            return -1.0
        if any(_x < 0 for _x in _v):
            return -1.0
        return max(_v)

    def gate_first_missing(self):
        """差的是哪一个 —— 返回 `(池序号, 名字)`, 全就绪或查不到时返回 None。

        ⚠️ 闸门是"差一个就打不开", 而在此之前 Python 只知道「100/101」—— 不知道是谁。
        ⚠️ 必须**带池序号**反查: 两个池的 sampleId 各自从 1 编号。
        ⚠️ 只报第一个(Java 侧是 HashSet 迭代序 ⇒ 缺多个时给的是其中之一) —— 诊断够用。
        """
        for _pi, _g, _tab in ((0, getattr(self, "_gate", None), getattr(self, "_ids", None)),
                              (1, getattr(self, "_gate2", None), getattr(self, "_ids2", None))):
            if _g is None or not _tab:
                continue
            try:
                _sid = int(_g.firstMissing())
            except Exception:
                continue
            if _sid <= 0:
                continue
            for _nm, _v in _tab.items():
                try:
                    if int(_v) == _sid:
                        return (_pi, _nm)
                except Exception:
                    continue
            return (_pi, "sid=%d" % _sid)
        return None

    def gate_info(self):
        """一行诊断: 类在不在 / 本进程第几次建闸门 / 回调从哪个线程来 / 两池就绪 a/b。

        ⚠️ 「回调线程」是这套方案**唯一的实测确认点** —— 空字符串表示一条回调都没到,
           那就是"闸门打不开", 兜底会转老探针(不会退步, 也拿不到收益)。
        """
        try:
            _gs = self._gates()
            if not _gs:
                return "闸门 无(%s)" % (getattr(self, "_gate_err", "") or "未挂载",)
            from jnius import autoclass
            _G = autoclass("com.plinko.SoundGate")
            _th = str(_G.callbackThreadName() or "") or "还没回调"
            _ms = self.gate_ready_ms()
            _bad = 0
            try:
                _bad = sum(int(_g.failedCount()) for _g in _gs)
            except Exception:
                pass
            # ⚠️ `满编` 那个数从**第一个 load** 起算: 冷启动时池子早就建好了、样本还在合成,
            #    从建闸门起算会被烘焙时间淹没。
            return ("闸门 本进程第 %d 次 / 回调 %d 条 线程=%s / 就绪 %d/%d%s%s"
                    % (int(_G.initCount()), int(_G.callbackTotal()), _th,
                       self.gate_ready_count(), self.gate_expected(),
                       ("" if _ms < 0 else " 满编 %.0fms(自首个load)" % _ms),
                       (" 坏 %d" % _bad) if _bad else ""))
        except Exception as _e:
            return "闸门 查询失败 %r" % (_e,)

    def _probe_cfg(self):
        """这一轮怎么探: `((池 0 线程数, 池 1 线程数), 钉核提优先)`。

        默认 `((1, 1), False)` = 串行路(零风险); 「重放冷启动」按 PROBE_LADDER 一档一档往上换。
        """
        try:
            _m = getattr(self, "_probe_mode", None) or (1, 1)
            # ⚠️ 上界 4: 线程数超过池的 maxStreams 就拿不到流, 补试也拿不到
            #    ⇒ 全员被判未就绪直到 6 秒, 会被读成"解码慢"的假阴性。
            return ((min(4, max(1, int(_m[0]))), min(4, max(1, int(_m[1])))),
                    bool(getattr(self, "_probe_boost", False)))
        except Exception:
            return ((1, 1), False)

    def _probe_serial(self):
        """所有已加载的 sample 是不是**真的能播**了(0 增益试播当探针)。

        `SoundPool.load()` 返回了 sampleId **不等于**解码完了; 没解码完 `play()` 返回 0、
        静默什么都不做 —— 这就是"音效都在、就是不响"。
        ⚠️ 探针自己出错一律当"能播": 它只是护栏, 绝不允许反过来把玩家锁在加载页。
        """
        _pairs = [(0, self._sp, list(self._ids.items()))]
        if self._sp2 is not None:
            _pairs.append((1, self._sp2, list(self._ids2.items())))
        if not _pairs[0][2] and (len(_pairs) < 2 or not _pairs[1][2]):
            self._probe_scan = 0
            self._probe_played = 0
            self._probe_stuck = ""
            return True
        _n = 0          # 这一轮**真的**调用了几次 play
        _seq = 0        # 从队首起**连续就绪**的个数
        _seq_open = True   # 前缀还没断
        _stuck = ""
        try:
            for _pi, _pool, _tab in _pairs:
              for name, sid in _tab:
                # ⚠️ K5: **已经亲眼确认过能播的, 不再重扫** —— 每轮从头重扫已确认的那批,
                #    对结果零信息、对成本满贡献(真机实测探针自己花掉 660ms)。
                #    依据: `_ids` 只增不减(唯一写入点是 prime), 启动窗口内没有任何地方
                #    release 单个 sample ⇒ "刚才能播" ⇒ "现在还能播"成立。
                if (_pi, sid) in self._ok_sids:
                    if _seq_open:
                        _seq += 1
                    continue
                _n += 1
                st = _pool.play(sid, 0.0, 0.0, 1, 0, 1.0)   # 0 增益 → 听不见
                if not st:
                    # ⚠️ `play()` 返回 0 **不只有"没解码完"一个含义**: 官方 javadoc 写明另一个
                    #    独立原因是「新流优先级低于所有在播流 / 当前没有空闲流」。**单独补试一次**:
                    #    此刻前面所有流都已经 stop 掉、池是空的, 再返回 0 就真的是"还没解码完"。
                    #    放行判据一个字没变: 仍然是"每个 id 都得有一次 play 返回非 0"。
                    try:
                        st = _pool.play(sid, 0.0, 0.0, 1, 0, 1.0)
                    except Exception:
                        st = 0
                    if not st:
                        if not _stuck:
                            _stuck = name
                        # ⚠️ 前缀到此为止, 但**只停本池** —— 两池各自串行、"每池同时最多
                        #    一条流"这个前提仍然成立(B1 补试靠它)。
                        _seq_open = False
                        break
                try:
                    _pool.stop(st)           # 立刻收流, 别占满 maxStreams
                except Exception:
                    pass
                self._ok_sids.add((_pi, sid))
                if _seq_open:
                    _seq += 1
        except Exception:
            self._probe_scan = _seq
            self._probe_played = _n
            self._probe_stuck = ""
            return True
        self._probe_scan = _seq
        self._probe_played = _n
        self._probe_stuck = _stuck
        return not _stuck

    def _probe_parallel(self, _mode):
        """并行探针(**已退休的档, 默认不会被选到**): 每个池派 `_mode[池]` 条线程,
        各扫自己池里**下标连续**的一段。

        ⚠️ 判据一个字没变: 仍然是"每个 (池, sid) 都得有一次 play 返回非 0", 每项仍补试一次。
        ⚠️ 分块按声明顺序**连续**切 ⇒ 「池里第一个没就绪的」与「连续就绪前缀」两个对外读数
           与串行版**同义**(合并时按下标排序回放)。
        """
        _pairs = [(0, self._sp, list(self._ids.items()))]
        if self._sp2 is not None:
            _pairs.append((1, self._sp2, list(self._ids2.items())))
        if not _pairs[0][2] and (len(_pairs) < 2 or not _pairs[1][2]):
            self._probe_scan = 0
            self._probe_played = 0
            self._probe_stuck = ""
            return True
        _res = {}                       # 池序号 -> {分块号: [(下标,名字,就绪?,真play过?)] | None}
        _lk = threading.Lock()
        _threads = []
        _err = []                       # 分块里抛异常 ⇒ 记一笔(合并层按串行约定放行)
        _gen = getattr(self, "_probe_gen", 0)   # 拔耳机重建过 ⇒ 这批结果作废
        try:
            for _pi, _pool, _tab in _pairs:
                _k = max(1, int(_mode[_pi])) if _pi < len(_mode) else 1
                _todo = [(idx, nm, sd) for idx, (nm, sd) in enumerate(_tab)]
                _step = max(1, (len(_todo) + _k - 1) // _k)
                # ⚠️ 槽位字典**必须先注册再起线程**: chunk 是 `_res.setdefault(_pi, {})[_t] = ...`
                #    写回来的, 若主线程在起完线程之后才 `_res[_pi] = _slots`, 那个赋值会把
                #    chunk 已写好的结果**整个覆盖掉**(实测非确定性)。
                _slots = {}
                _res[_pi] = _slots
                for _t in range(_k):
                    _chunk = _todo[_t * _step:(_t + 1) * _step]
                    if not _chunk:
                        continue
                    # ⚠️ 先登记槽位(None = "这条线程没起来"): 合并层靠它认出
                    #    「这一段**从没验过**」—— 否则起不来的那段会静默地不进任何判据,
                    #    而 `return not _stuck` 就会**声称全部就绪**。
                    _slots[_t] = None
                    _th = threading.Thread(target=self._probe_chunk,
                                           args=(_pi, _t, _pool, _chunk, _res, _lk, _err, _gen),
                                           daemon=True)
                    try:
                        _th.start()      # ⚠️ try 必须在**内层**: 一条起不来不许带崩其余
                    except Exception:
                        continue
                    _threads.append(_th)
        except Exception:
            pass
        # ⚠️ join **必须有上限**: 一旦某条 worker 卡在原生 play() 里, 无限等就等于拖页不摘。
        _join_by = time.time() + 3.0
        for _th in _threads:
            try:
                _th.join(max(0.05, _join_by - time.time()))
            except Exception:
                pass
        # ---- 合并: 完全按串行版的口径, 按下标顺序回放 ----
        _n = 0
        _seq = 0
        _seq_open = True
        _stuck = ""
        _unverified = False
        for _pi, _pool, _tab in _pairs:
            _sl = _res.get(_pi) or {}
            for _t in sorted(_sl):
                _rows = _sl[_t]
                if _rows is None:
                    # ⚠️ 这条线程没起来 ⇒ 它负责的那一段**一个样本都没验过**, 绝不能当
                    #    "没问题"放行 —— 那正好把这个探针存在的理由反过来打穿。
                    _unverified = True
                    _seq_open = False
                    continue
                for _idx, _nm, _ok, _played in _rows:
                    if _played:
                        _n += 1
                    if _ok:
                        if _seq_open:
                            _seq += 1
                    else:
                        if not _stuck:
                            _stuck = _nm
                        _seq_open = False
        if _err:
            # ⚠️ 与串行版**同一条约定**: 探针自己出错一律当"能播"(它只是护栏)。
            self._probe_scan = _n
            self._probe_played = _n
            self._probe_stuck = ""
            return True
        self._probe_scan = _seq
        self._probe_played = _n
        self._probe_stuck = _stuck
        return (not _stuck) and (not _unverified)

    def _probe_chunk(self, _pi, _t, _pool, _chunk, _res, _lk, _err, _gen):
        """一条线程负责一段(**下标连续**)的样本; 撞到第一个未就绪的**只停自己这一段**。"""
        _out = []
        try:
            for _idx, _nm, _sd in _chunk:
                if (_pi, _sd) in self._ok_sids:      # K5: 已确认的不重扫
                    _out.append((_idx, _nm, True, False))
                    continue
                # ⚠️ `play()` **抛异常**与**返回 0** 必须分开: 返回 0 = "还没解码完";
                #    抛异常 = "探针自己出事", 串行版对后者是**整轮放行**。折成 0 会一路
                #    重试到 6 秒硬超时。
                _st = _pool.play(_sd, 0.0, 0.0, 1, 0, 1.0)
                if not _st:
                    _st = _pool.play(_sd, 0.0, 0.0, 1, 0, 1.0)   # B1: 补试一次
                if not _st:
                    _out.append((_idx, _nm, False, True))
                    break
                try:
                    _pool.stop(_st)
                except Exception:
                    pass
                # ⚠️ 代次守卫: 拔耳机会 `_reset_pools()`, 而新池的 sampleId 又从 1 重新编号
                #    ⇒ 旧池的 (池,sid) 回写会碰上"新池同号但没验过"的样本 = 假绿。
                if _gen == getattr(self, "_probe_gen", 0):
                    self._ok_sids.add((_pi, _sd))
                _out.append((_idx, _nm, True, True))
        except Exception:
            try:
                _err.append(1)           # 与串行版同一条约定: 探针出错 ⇒ 放行
            except Exception:
                pass
        try:
            with _lk:
                _res.setdefault(_pi, {})[_t] = _out
        except Exception:
            pass

    def _miss_play(self, name):
        """后端说"这一声没播成" ⇒ 记一笔。

        ⚠️ **只计数, 不改任何返回值** —— 见 `play_named`。
        ⚠️ 带时间戳与序号: "发生在启动后多久 + 是第几次 play 尝试"是定位它的钥匙
           (play() 返回 0 有好几种原因, 分不开 —— 那就至少把时刻记下来)。
        """
        self._play_missed += 1
        self._play_missed_last = name
        try:
            _boot_log("sfx", "没播成: %s（本次会话第 %d 次 play 尝试, t+%.0f ms）"
                     % (name, int(getattr(self, "_play_total", 0) or 0),
                        (time.perf_counter() - _BOOT_T0) * 1000.0))
        except Exception:
            pass

    def play_named(self, name, gain01):
        """⚠️ 返回值 = **"请求已受理"**, 不是"后端真的播成了" —— 这是有意的:

        它牵着装杯震动(`if sfx.play(...)` → `_vibrate_tick`), 改成"后端说了算"
        会让流一忙就**整段没震**。
        "没播成"从此被记下来(`_play_missed`), 它以前彻底不可见。
        """
        self._play_total += 1
        sid = self._ids.get(name)
        if sid is not None:
            if not self._sp.play(sid, gain01, gain01, 1, 0, 1.0):
                self._miss_play(name)
            return True
        if self._sp2 is not None:            # 语音在第二个池里(见 prime 的分池规则)
            sid2 = self._ids2.get(name)
            if sid2 is not None:
                _st2 = self._sp2.play(sid2, gain01, gain01, 1, 0, 1.0)
                if not _st2:
                    self._miss_play(name)
                elif name.startswith("voice_"):
                    # ⚠️ 记下"还在念的那一条流"供 stop_voice 用。**只在播成功时记**:
                    #    返回 0 说明那条流根本不存在。
                    self._voice_stream = _st2
                return True
        return False

    def stop_voice(self):
        """掐掉"还在念的那一句语音"。⚠️ **只动语音池里记下来的那一条流**。

        ⚠️ 只掐语音那一条: 撞钉 / 装杯 / 中奖那些流还在响, 一个都不许动。
        ⚠️ 拿不到流号(没播过 / 刚换过池)时**什么都不做** —— 不是错误。
        """
        _st = int(getattr(self, "_voice_stream", 0) or 0)
        if not _st:
            return
        self._voice_stream = 0
        try:
            self._sp2.stop(_st)
        except Exception:
            pass

    def _each_pool(self, _fn):
        """对**每一个**池做同一件事(切后台/静音/退出都要管全)。

        ⚠️ 池可能只有一个(SFX_POOL_SPLIT 关了 / 第二个池构造失败) ⇒ None 要跳过;
           每个池各自 try —— 一个池出事不能拖累另一个。
        """
        for _p in (self._sp, getattr(self, "_sp2", None)):
            if _p is None:
                continue
            try:
                _fn(_p)
            except Exception:
                pass

    def pause(self):
        """切后台 / 静音: 暂停**所有池**的流。

        ⚠️ 必须管到 `_sp2`: 只暂停池 1 的话, 正在念的那句语音会继续念完(别的音都停了),
           而语音恰恰是最长的那批(1~3 秒)。
        """
        self._each_pool(lambda _p: _p.autoPause())

    def resume(self):
        """切后台回来: 恢复**所有池**(与 pause 对称 —— 只暂停不恢复会留下一半没声的池)。"""
        self._each_pool(lambda _p: _p.autoResume())

    def close(self):
        try:
            if self._receiver is not None:
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                PythonActivity.mActivity.unregisterReceiver(self._receiver)
        except Exception:
            pass
        # ⚠️ **不把 _sp/_sp2 字段置 None** —— play_named / probe_all 都在判 `is not None`,
        #    置 None 会把"释放后的旧引用"变成"路径分岔"。
        self._voice_stream = 0      # 池要没了 ⇒ 流号必须作废(不然会去 stop 野句柄)
        self._each_pool(lambda _p: _p.release())


# ============================== 实验档(退休) ==============================
# 见 `ARM_LADDER`。
# ⚠️ **绝不在出货路径上**: 只由「重放冷启动」的实验阶梯按 `_arm_extra` 调
#    (档位写在**后端对象**上, 由 `Sfx._await_ready` 读出来); A 档(出货形态)的
#    `_arm_extra` 是空串 ⇒ 下面一行都不跑。
# ⚠️ 两支都用**临时池**(见各函数自己的说明): 样本不许进主池, 用完立刻 release。


def _wav_pcm_read(path):
    """从 WAV 里取裸 PCM 字节。⚠️ **不假设 44 字节头** —— 按 RIFF 块遍历找 `data`。"""
    try:
        with open(path, "rb") as f:
            d = f.read()
        if d[:4] != b"RIFF":
            return b""
        i = 12
        while i + 8 <= len(d):
            _cid = d[i:i + 4]
            _sz = struct.unpack("<I", d[i + 4:i + 8])[0]
            if _cid == b"data":
                return d[i + 8:i + 8 + _sz]
            i += 8 + _sz + (_sz & 1)
    except Exception:
        pass
    return b""


def _resample16(pcm, sr_in, sr_out):
    """16bit 单声道线性插值重采样(纯 Python, 不引 numpy)。

    ⚠️ 只服务 48k **对拍**档 —— 唯一目的是"换个采样率再量一次单价",
       音质不进任何判据, 所以线性插值够(真要出货再谈窗口 sinc)。
    """
    try:
        n = len(pcm) // 2
        if n <= 1 or sr_in <= 0 or sr_out <= 0 or sr_in == sr_out:
            return pcm
        src = struct.unpack("<%dh" % n, pcm[:n * 2])
        m = int(n * float(sr_out) / float(sr_in))
        _r = float(sr_in) / float(sr_out)
        out = []
        for i in range(m):
            _x = i * _r
            _i0 = int(_x)
            if _i0 >= n - 1:
                out.append(src[n - 1])
                continue
            _f = _x - _i0
            _v = src[_i0] * (1.0 - _f) + src[_i0 + 1] * _f
            out.append(-32768 if _v < -32768.0 else (32767 if _v > 32767.0 else int(_v)))
        return struct.pack("<%dh" % m, *out)
    except Exception:
        return b""


def _wav_write_sr(path, pcm, sr):
    """写标准 WAV(采样率可给)。与 `pcm_to_wav` 同形, 只是它的采样率写死 `SR`。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) +
                b"data" + struct.pack("<I", len(pcm)) + pcm)
    os.replace(tmp, path)


def _med(v):
    """中位数。⚠️ 单价实测有 143.6ms 那种离群点, 只报均值会被它带偏。"""
    _s = sorted(v)
    return _s[len(_s) // 2] if _s else 0.0


def _warm_all(pool, sids, timeout=6.0):
    """等到这一批 sid **逐个**都能播(与探针同一个判据)。

    ⚠️ 必须**逐个** play+stop: `maxStreams` 只有 8, 一次全 play 的话第 9 个起必然返回 0,
       那样这个等待条件永远不成立(会白等到超时)。
    """
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        _all = True
        for _s in sids:
            try:
                _st = pool.play(_s, 0.0, 0.0, 1, 0, 1.0)
            except Exception:
                return False
            if not _st:
                _all = False
                break
            try:
                pool.stop(_st)
            except Exception:
                pass
        if _all:
            return True
        time.sleep(0.05)
    return False


def _arm48_bench(paths, n=10, rounds=5):
    """48k 对拍(退休档, 见 `ARM_LADDER` 下方注释): 同一批波形, 22050 与 48000 各要多久一次
    `play()`?

    返回若干行文本(给重放详情弹窗)。⚠️ 用**临时池**(这些样本不能进主池的
    `_paths/_ids` —— 那会让闸门的期望数与 `_ids` 脱钩)。用完立刻 release。
    """
    rows = []
    _d48 = os.path.join(os.path.dirname(_sfx_cache_dir()), "plinko_arm48")
    pool = None
    try:
        try:
            os.makedirs(_d48, exist_ok=True)
        except Exception:
            pass
        items = []
        _t0 = time.perf_counter()
        _new = 0
        for _nm, _p22 in list(paths)[:max(1, int(n))]:
            try:
                if not os.path.exists(_p22):
                    continue
                _p48 = os.path.join(_d48, "arm48_" + _nm + ".wav")
                if not os.path.exists(_p48):
                    _pcm = _wav_pcm_read(_p22)
                    if not _pcm:
                        continue
                    _wav_write_sr(_p48, _resample16(_pcm, SR, 48000), 48000)
                    _new += 1
                items.append((_nm, _p22, _p48))
            except Exception:
                continue
        _rs_ms = (time.perf_counter() - _t0) * 1000.0
        if not items:
            return ["48k 对拍　跳过：读不到源 wav"]
        rows.append("48k 对拍　%d 个样本(本次重采样 %d 个, %.0f ms)"
                    % (len(items), _new, _rs_ms))
        pool = _mk_soundpool(SFX_VOICES)
        ids22, ids48 = [], []
        for _nm, _p22, _p48 in items:
            try:
                ids22.append(pool.load(_p22, 1))
                ids48.append(pool.load(_p48, 1))
            except Exception:
                pass
        if not ids22 or len(ids22) != len(ids48):
            return rows + ["48k 对拍　跳过：load 失败"]
        _w0 = time.perf_counter()
        if not _warm_all(pool, ids22 + ids48):
            return rows + ["48k 对拍　跳过：等不到就绪(%.0f ms)" % ((time.perf_counter() - _w0) * 1000.0)]
        rows.append("48k 对拍　预热(等到能播) %.0f ms" % ((time.perf_counter() - _w0) * 1000.0))
        s22, s48 = [], []
        for _r in range(max(1, int(rounds))):
            for _i in range(len(ids22)):
                for _lst, _sid in ((s22, ids22[_i]), (s48, ids48[_i])):
                    _t = time.perf_counter()
                    _st = pool.play(_sid, 0.0, 0.0, 1, 0, 1.0)
                    _lst.append((time.perf_counter() - _t) * 1000.0)
                    if _st:
                        try:
                            pool.stop(_st)
                        except Exception:
                            pass
        rows.append("48k 对拍　22050 中位 %.1f ms / 最小 %.1f ms (n=%d)"
                    % (_med(s22), min(s22), len(s22)))
        rows.append("48k 对拍　48000 中位 %.1f ms / 最小 %.1f ms (n=%d)"
                    % (_med(s48), min(s48), len(s48)))
        _b22 = sum(os.path.getsize(_p) for _n, _p, _q in items)
        _b48 = sum(os.path.getsize(_q) for _n, _p, _q in items)
        rows.append("48k 对拍　字节比 %.2fx（%.2f MB → %.2f MB）"
                    % (_b48 / float(_b22 or 1), _b22 / 1e6, _b48 / 1e6))
    except Exception as _e:
        rows.append("48k 对拍　出错: %r" % (_e,))
    finally:
        try:
            if pool is not None:
                pool.release()
        except Exception:
            pass
    return rows


def _arm_pools_bench(paths, plan=((2, "生产分法"), (4, "两组各自对半"),
                                  (2, "生产分法"), (4, "两组各自对半"))):
    """池数对拍(档 C): 同一批样本装进 **2 个池 vs 4 个池**, 解码各要多久?

    ⚠️ 生产分池是"合成音一池 / 语音一池"(2 池 × 2 条解码线程 = 4 条)。4 池 = 这两组**各自对半**
       (= 8 条解码线程) —— 那才是"真改成 4 池"的样子, 不是随便轮转。
    ⚠️ **交替 2→4→2→4**: 跨时段不可比(同一台机器的冷启动一天内从 6461 变 8620)。
    ⚠️ 时钟用 **Python 侧墙钟**(第一个 load 之前 → 所有闸门都开): 跨池可比。
       每池闸门自记的 readyMs 只是"该池自己的纯解码", 各池起点不同, **不能横向比**。
    ⚠️ **全程不 play()** ⇒ 不建 AudioTrack ⇒ 不碰 `kMaxTracksPerUid`(那是 track 的上限)。
    ⚠️ 上一组的池必须**先 release 再建下一组**, 否则它们会抢解码线程/内存, 把读数搅浑。
    ⚠️ 拿不到闸门类时**不做假数**, 直接返回一行说明。
    """
    rows = []
    try:
        from jnius import autoclass
        _G = autoclass("com.plinko.SoundGate")
    except Exception as _e:
        return ["池数对拍　跳过: 拿不到闸门类(%s)" % (repr(_e)[:40],)]
    _bank = [(n, p) for n, p in paths if not n.startswith("voice_")]
    _voice = [(n, p) for n, p in paths if n.startswith("voice_")]
    if not _bank or not _voice:
        return ["池数对拍　跳过: 两类样本不齐(合成 %d / 语音 %d)" % (len(_bank), len(_voice))]

    def _buckets(npool):
        # ⚠️⚠️ 每一档都必须**显式列出**。这里曾经是"2 池 / 否则=4 池"两分支, 那样传 1 会返回
        #    4 个桶, 而下面 `for _gi, _items in enumerate(...)` 配着 `if _gi >= len(pools): break`
        #    ⇒ **只 load 了第一个桶(20 个), 其余 81 个样本一次都没提交**, 闸门只等那 20 个
        #    ⇒ 报出一个**漂亮得离谱的假数, 而且不报错**。
        if npool == 1:
            return [list(_bank) + list(_voice)]
        if npool == 2:
            return [_bank, _voice]
        _hb = max(1, len(_bank) // 2)
        _hv = max(1, len(_voice) // 2)
        return [_bank[:_hb], _bank[_hb:], _voice[:_hv], _voice[_hv:]]

    for npool, tag in plan:
        pools, gates = [], []
        try:
            _t0 = time.perf_counter()
            for _ in range(npool):
                pools.append(_mk_soundpool(SFX_VOICES))
            _build_ms = (time.perf_counter() - _t0) * 1000.0
            for _p in pools:
                try:
                    gates.append(_G(_p))       # 必须在任何 load 之前挂上
                except Exception:
                    gates.append(None)
            _t1 = time.perf_counter()
            _n = 0
            for _gi, _items in enumerate(_buckets(npool)):
                if _gi >= len(pools):
                    break
                for _nm, _pp in _items:
                    try:
                        _sid = pools[_gi].load(_pp, 1)
                    except Exception:
                        continue
                    if _sid and gates[_gi] is not None:
                        try:
                            gates[_gi].expect(_sid)
                        except Exception:
                            pass
                        _n += 1
            _load_ms = (time.perf_counter() - _t1) * 1000.0
            _ok = False
            while time.perf_counter() - _t1 < 15.0:
                _all = True
                for _g in gates:
                    if _g is None:
                        _all = False
                        break
                    try:
                        if not bool(_g.allReady()):
                            _all = False
                            break
                    except Exception:
                        _all = False
                        break
                if _all:
                    _ok = True
                    break
                time.sleep(0.02)
            _wall = (time.perf_counter() - _t1) * 1000.0
            _per = []
            for _g in gates:
                try:
                    _per.append(float(_g.readyMs()))
                except Exception:
                    _per.append(-1.0)
            rows.append("池数对拍　%d 池(%s): 全部解码完 %s(load 提交 %d 个 %.0f ms, 建池 %.0f ms)"
                        % (npool, tag, ("%.0f ms" % _wall) if _ok else "**没等到**(15 秒上限)",
                           _n, _load_ms, _build_ms))
            rows.append("池数对拍　%d 池逐池满编: %s"
                        % (npool, " / ".join(("%.0f" % _v) if _v >= 0 else "?" for _v in _per)))
        except Exception as _e:
            rows.append("池数对拍　%d 池出错: %r" % (npool, _e))
        finally:
            for _p in pools:
                try:
                    _p.release()
                except Exception:
                    pass
    return rows


class _KivySoundOut:
    """桌面后备: Kivy SoundLoader(SDL2)。能同时响, 但延迟/叠加不如 winmm/SoundPool。"""
    mode = "named"
    name = "Kivy-SoundLoader"

    def __init__(self):
        from kivy.core.audio import SoundLoader
        self._loader = SoundLoader
        self._sounds = {}

    def loaded_count(self):
        """后端**真的**握着几个可播对象。

        ⚠️ 这个方法必须有。缺了它 `Sfx.backend_count()` 返回 None → 面板显示「未知」;
           而缺它的后果曾经是**悄悄退回闸门值**(`len(named)`) ⇒ 面板报出一片 `97 / 97`
           全绿 —— 而这块面板存在的唯一理由就是抓这种静默。
        """
        return len(self._sounds)

    def prime(self, name, path):
        snd = self._loader.load(path)
        if snd is None:
            raise RuntimeError("SoundLoader.load failed: " + path)
        self._sounds[name] = snd

    def play_named(self, name, gain01):
        snd = self._sounds.get(name)
        if snd is None:
            return False
        try:
            if snd.state == "play":
                snd.stop()
            snd.volume = gain01
            snd.play()
            return True
        except Exception:
            return False

    def close(self):
        for snd in self._sounds.values():
            try:
                snd.stop()
            except Exception:
                pass
        self._sounds.clear()


# ============================== 降级链 ====================================
_BACKEND_ERRORS = []     # [(名字, 异常文本)] —— 降级链每一级失败都记一笔


def _backend_error(name):
    """取某个后端构造失败的原文(诊断用)。"""
    for _n, _e in _BACKEND_ERRORS:
        if _n == name:
            return _e
    return ""


def open_output():
    """按优先级选后端: Android SoundPool > winmm > Kivy SoundLoader > 静音。

    环境变量 PLINKO_SFX_BACKEND=kivy|winmm|none 可在桌面强制指定 —— 安卓走的是 named
    这条路径(缓存/落盘/按名播), 桌面默认走 winmm 的 pcm 路径, 不强制就没法在开发机上验它。

    ⚠️ 降级链每一级**都要把异常原文记进 _BACKEND_ERRORS**(见模块顶部)。
    """
    want = os.environ.get("PLINKO_SFX_BACKEND", "").lower()
    if want == "none":
        return None
    if platform == "android" and want not in ("kivy", "winmm"):
        try:
            return _SoundPoolOut()
        except Exception as exc:
            _BACKEND_ERRORS.append(("SoundPool", "%s: %s" % (type(exc).__name__, exc)))
    if want != "kivy":
        try:
            return _WaveOut()
        except Exception as exc:
            _BACKEND_ERRORS.append(("winmm", "%s: %s" % (type(exc).__name__, exc)))
    try:
        return _KivySoundOut()
    except Exception as exc:
        _BACKEND_ERRORS.append(("Kivy-SoundLoader", "%s: %s" % (type(exc).__name__, exc)))
    return None
