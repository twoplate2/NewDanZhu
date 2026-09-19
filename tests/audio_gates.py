# -*- coding: utf-8 -*-
"""常设门禁: 音频播放层 (bus / backend / voice 三段)。

`tests/parity.py` 只覆盖 synth(合成); bus / backend / voice 在常设门禁里**零覆盖**,
本文件补上 —— 覆盖的是"没声音"这条路里最难查的那些分岔: 闸门/探针的就绪判据、
B1 补试、6 秒硬超时、语音互斥、play 的受理语义、节流键、语音列举失败的可诊断性。

    python tests/audio_gates.py            # 全部
    python tests/audio_gates.py G1 G7      # 只跑某几条

不依赖真机、不依赖真声音: 安卓 SoundPool 用假池/假闸门替身, 替身**继承真的
`_SoundPoolOut`**, 只换掉三条碰 Java 的线(建池 / 注册广播 / 挂闸门) —— 所以
`probe_all` / `_probe_serial` / `_probe_parallel` / `prime` / `play_named` / `stop_voice` /
`_reset_pools` / `gate_*` 走的都是**出货那份代码**。

⚠️ 每条检查都配**阴性对照**: 把坏行为改回去(monkeypatch / 子类替身), 同一条检查必须
   **变红**。对照做了什么、为什么它等价于那个坏行为, 逐条写在 `_neg_*` / 子类替身的
   docstring 里。阴性对照没红 = 这条检查是假绿(恒真), 一样判红。
"""

import contextlib
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:                                    # Windows 控制台默认 GBK, 直印中文会炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_LOG_LEVEL", "warning")
# ⚠️ 语音目录必须显式指到工程根: `voice._voice_dir()` 的兜底是 app_root()/voice, 而
#    app_root() 取的是**主脚本**目录 —— 跑 `python tests/audio_gates.py` 时它是 tests/,
#    那儿没有 voice/。这正是模块化之后与老版 `dirname(main.py)/voice` 的差异所在。
VOICE_DIR = os.path.join(ROOT, "voice")
os.environ["DANZHU_VOICE_DIR"] = VOICE_DIR

from danzhu.audio import backend as B          # noqa: E402
from danzhu.audio import bus as BUS            # noqa: E402
from danzhu.audio import synth as S            # noqa: E402
from danzhu.audio import voice as V            # noqa: E402
from danzhu.config import EV_PEG               # noqa: E402


# ============================== 小工具 =====================================
class R:
    """一条检查的账本。`n` = 断言总数, `bad` = 不符的那些的说明。"""

    def __init__(self):
        self.n = 0
        self.bad = []

    def eq(self, ok, msg):
        self.n += 1
        if not ok:
            self.bad.append(msg)
        return bool(ok)


@contextlib.contextmanager
def patched(obj, name, val):
    """临时换掉 `obj.name`(模块全局 / 类属性都行), 退出时还原。"""
    _had = hasattr(obj, name)
    _old = getattr(obj, name, None)
    setattr(obj, name, val)
    try:
        yield
    finally:
        if _had:
            setattr(obj, name, _old)
        else:
            delattr(obj, name)


@contextlib.contextmanager
def fake_backend(out):
    """让 `B.open_output()` 返回替身(只有构造 Sfx 时需要)。"""
    _orig = B.open_output
    B.open_output = lambda: out
    try:
        yield out
    finally:
        B.open_output = _orig


# ============================== 替身 =======================================
class FakePool:
    """一个 SoundPool 的替身。模型: 第 N 个 load 进来的样本还要 plan[N] 次 play 才"解码完"。

    真机上 `load()` 返回了 sampleId **不代表**解码完; 没解码完 `play()` 返回 0 且静默
    什么都不做 —— 探针就是拿这个 0 当"还没好"的判据。
    """

    def __init__(self, plan=None, default_pending=0):
        self.plan = dict(plan or {})      # load 序号(1 起) -> 还需要几次 play
        self.default_pending = default_pending
        self.nload = 0
        self.names = {}                   # sid -> basename
        self.pending = {}                 # sid -> 还要几次 play
        self._sid = 0
        self._st = 0
        self.plays = 0                    # play 调用总次数(含 0 增益探针)
        self.audible = []                 # 真发声的 (name, gain)
        self.stopped = []
        self.released = False
        self.auto_paused = 0
        self.auto_resumed = 0

    def load(self, path, prio=1):
        self.nload += 1
        self._sid += 1
        self.names[self._sid] = os.path.basename(path)
        self.pending[self._sid] = self.plan.get(self.nload, self.default_pending)
        return self._sid

    def play(self, sid, gl, gr, prio, loop, rate):
        self.plays += 1
        if sid not in self.names:
            return 0
        _left = self.pending.get(sid, 0)
        if _left > 0:                     # 还没解码完: 真机上返回 0 且什么都不做
            self.pending[sid] = _left - 1
            return 0
        self._st += 1
        if gl > 0.0:                      # 0 增益 = 探针, 不算发声
            self.audible.append((self.names[sid], gl))
        return self._st

    def stop(self, st):
        self.stopped.append(st)

    def release(self):
        self.released = True

    def autoPause(self):
        self.auto_paused += 1

    def autoResume(self):
        self.auto_resumed += 1


class FakeGate:
    """冒充 `com.plinko.SoundGate`: 只数谁登记了、谁就绪了。

    `open_after`       = 登记到第 N 个样本后就绪(0 / 极大值 = 永远开不了)。
    `open_after_checks` = **查询**到第 N 次才就绪 —— 用来模拟"Java 回调是在 Python 轮询
        **期间**才陆续到齐"(而不是 `Sfx` 开始等之前就已经闩上了)。区别很关键:
        前者才让「等闸门 / 转探针」这个时间窗有意义。
    """

    def __init__(self, open_after=1, open_after_checks=0):
        self.registered = set()
        self.ready = set()
        self.open_after = open_after
        self.open_after_checks = int(open_after_checks or 0)
        self.checks = 0
        self._all_ready = False

    def expect(self, sid):
        self.registered.add(sid)
        if not self._all_ready and len(self.registered) >= max(1, self.open_after):
            self.ready = set(self.registered)
            self._all_ready = True       # 闩锁: 开过一次就永远是开(与 Java 侧一致)

    def allReady(self):
        self.checks += 1
        if (not self._all_ready) and self.open_after_checks \
                and self.checks >= self.open_after_checks:
            self.ready = set(self.registered)
            self._all_ready = True
        return self._all_ready

    def readyCount(self):
        return len(self.ready)

    def expectCount(self):
        return len(self.registered)

    def readyMs(self):
        return 210.0 if self._all_ready else -1.0

    def firstMissing(self):
        _miss = self.registered - self.ready
        return min(_miss) if _miss else 0

    def failedCount(self):
        return 0


class FakeOut(B._SoundPoolOut):
    """`_SoundPoolOut` 的桌面临时替身(真逻辑 + 假池/假闸门)。"""

    def __init__(self, gate_open_after=1, gate_open_checks=0, plan=None, plan2=None,
                 default_pending=0, worker=False):
        self._gate_open_after = gate_open_after
        self._gate_open_checks = gate_open_checks
        self._plans = [dict(plan or {}), dict(plan2 or {})]
        self._default_pending = default_pending
        self.pools = []
        self.probe_calls = 0
        # ⚠️ 真类声明 needs_worker=True(Sfx 会起发声线程) —— 上面那个开关是它的替身。
        self.needs_worker = bool(worker)
        B._SoundPoolOut.__init__(self)

    def _build_sp(self):
        _i = len(self.pools)
        _p = FakePool(self._plans[_i] if _i < len(self._plans) else {}, self._default_pending)
        self.pools.append(_p)
        return _p

    def _register_noisy_receiver(self):
        self._receiver = None

    def _attach_gates(self):
        self._gate = FakeGate(self._gate_open_after, self._gate_open_checks)
        self._gate2 = (FakeGate(self._gate_open_after, self._gate_open_checks)
                       if self._sp2 is not None else None)

    def probe_all(self):
        self.probe_calls += 1
        return B._SoundPoolOut.probe_all(self)

    def gate_info(self):
        # ⚠️ _g_desc 只进日志、没有任何判据读它 ⇒ 替身化是安全的; 真版要静态调 Java。
        return "闸门[假] 就绪 %d/%d" % (self.gate_ready_count(), self.gate_expected())

    def no_gate(self):
        """把闸门判成"不可用"(一个样本都没登记) —— 桌上取不到 java 类时就是这个形状。"""
        self.gate_expected = lambda: 0


def _prime(out, n, tag="s", pool=1):
    """直接往池里塞 n 个样本。

    ⚠️ 不建 Sfx(不烘焙)—— 探针/K5/B1 这几条检查的对象是**探针本身**, 烘焙只是噪音。
    分池规则与出货一致: 名字以 `voice_` 开头走第二个池。
    """
    _p = tag if pool == 1 else "voice_" + tag
    for i in range(n):
        out.prime("%s%d" % (_p, i), "/fake/%s%d.wav" % (_p, i))


# ====================== 阴性对照用的"坏实现"替身 ============================
class _SidOnlySet(set):
    """阴性对照: 冒充"K5 缓存只用**纯 sid** 当键(没带池序号)"的实现。

    add/contains 都只取元组的第二项 ⇒ 两个池各自从 1 编号的 sampleId 会互相撞号:
    池 2 的前几个从没被 play 验过, 就因为"池 1 同号验过了"被放行 = **假绿**。
    """

    def add(self, key):
        set.add(self, key[1] if isinstance(key, tuple) else key)

    def __contains__(self, key):
        return set.__contains__(self, key[1] if isinstance(key, tuple) else key)


class _DropNewSfx(BUS.Sfx):
    """阴性对照: **老行为** —— 撞上语音互斥时 `return False`, 把新来的那句丢掉。"""

    def play(self, name, gain=1.0, throttle=0.0):
        if name.startswith(("voice_rtp_", "voice_bet_", "voice_mode_")):
            if time.time() - self._last_voice < self._last_voice_len:
                return False
        return BUS.Sfx.play(self, name, gain, throttle)


class _NoCutSfx(BUS.Sfx):
    """阴性对照: 把"掐掉旧的"那一半摘掉(新的照念, 旧的不打断)。"""

    def _voice_cut(self):
        pass


class _VerdictSfx(BUS.Sfx):
    """阴性对照: 把返回值改成"**后端说了算**"(老版 `play_named` 的返回语义)。

    后端这一次说没播成(池里 play 返 0) ⇒ `play()` 返回 False ⇒ 牵着它的震动会整段消失。
    """

    def play(self, name, gain=1.0, throttle=0.0):
        if self.out is None or getattr(self.out, "mode", "pcm") != "named":
            return BUS.Sfx.play(self, name, gain, throttle)
        _m0 = int(getattr(self.out, "_play_missed", 0) or 0)
        _r = BUS.Sfx.play(self, name, gain, throttle)
        if int(getattr(self.out, "_play_missed", 0) or 0) > _m0:
            return False
        return _r


# ============================== G1 闸门优先 =================================
def _g1(slow_gate=False):
    """闸门就绪 ⇒ 老探针一次都不跑。

    ⚠️ 这里的闸门做成"**查询到第 2 次**才就绪"(不是一开始就闩上), 否则负对照无论怎么
       调都改不动结果 —— 闸门在 `Sfx` 开始等之前就开着, 时间窗根本没被用到。
    """
    r = R()
    fake = FakeOut(gate_open_after=10 ** 9, gate_open_checks=2)
    _ctx = patched(BUS.Sfx, "SFX_GATE_FIRST_SEC", -1.0) if slow_gate else contextlib.nullcontext()
    with _ctx, fake_backend(fake):
        s = BUS.Sfx(sync=True)
    r.eq(fake.probe_calls == 0,
         "闸门就绪后探针仍跑了 %d 次(老版语义: 0 次)" % fake.probe_calls)
    r.eq(fake._sp.plays == 0 and fake._sp2.plays == 0,
         "闸门就绪后池里仍被 play %d 次(探针没跑 ⇒ 应为 0)"
         % (fake._sp.plays + fake._sp2.plays))
    r.eq(s._ready_winner == "闸门", "先到者 winner=%r(应为「闸门」)" % s._ready_winner)
    r.eq(s.audio_ready(), "audio_ready() 仍为假")
    r.eq(fake._gate.checks >= 1, "闸门一次都没被查询过(allReady 调用 0 次)")
    r.eq(s.ready_ms < 500.0,
         "闸门路径的等待 %.1f ms —— 闸门就绪却还在按 15ms 档轮询" % s.ready_ms)
    s.close()
    return r


def _g1_neg_deadline():
    """阴性对照: 把闸门窗口压成 0(`SFX_GATE_FIRST_SEC = -1`) —— 等价于"先探针、后闸门"
    那个顺序错误(闸门明明会开, 却永远等不到它的窗口就落回探针, 收益归零)。

    断言 `probe_calls == 0` / `winner == 闸门` 必须变红。"""
    return _g1(slow_gate=True)


# ==================== G2 闸门不可用 ⇒ 串行探针兜底 ==========================
def _g2(parallel=False):
    r = R()
    calls = {"serial": 0, "parallel": 0}
    _o_s = B._SoundPoolOut._probe_serial
    _o_p = B._SoundPoolOut._probe_parallel

    def _cs(self):
        calls["serial"] += 1
        return _o_s(self)

    def _cp(self, mode):
        calls["parallel"] += 1
        return _o_p(self, mode)

    fake = FakeOut(gate_open_after=0)
    fake.no_gate()
    if parallel:
        fake._probe_mode = (2, 2)         # 阴性对照: 逼它走并行档
    with patched(B._SoundPoolOut, "_probe_serial", _cs), \
            patched(B._SoundPoolOut, "_probe_parallel", _cp), \
            fake_backend(fake):
        s = BUS.Sfx(sync=True)
    _total = fake.loaded_count()
    r.eq(s._ready_winner == "探针",
         "winner=%r(闸门不可用 ⇒ 必须落回老探针)" % s._ready_winner)
    r.eq(fake.probe_calls > 0, "probe_all 一次都没被调用")
    r.eq(calls["serial"] > 0, "_probe_serial 一次都没跑(闸门不可用时它就是兜底)")
    r.eq(calls["parallel"] == 0,
         "走到了并行档(默认 _probe_cfg=(1,1) ⇒ 只许走串行的那条老路)")
    r.eq(fake._probe_played == _total,
         "首轮真扫 %s 次 != 已加载 %d 个" % (fake._probe_played, _total))
    r.eq(fake._probe_stuck == "", "stuck=%r(全体都解码得出来)" % fake._probe_stuck)
    r.eq(s.audio_ready(), "audio_ready() 仍为假")
    s.close()
    return r


def _g2_neg_parallel():
    """阴性对照: 把 `_probe_mode` 改成 (2,2) ⇒ 分派到并行档。

    两档的**判据**相同, 所以这条对照不是"行为坏了", 而是校验"默认档真的只走串行"
    这个断言不是恒真: 改一档它就红。"""
    return _g2(parallel=True)


# ============================ G3 K5 增量 ====================================
def _g3(no_cache=False):
    r = R()
    fake = FakeOut(gate_open_after=0)
    _prime(fake, 5, "a")
    _ok1 = fake._probe_serial()
    r.eq(fake._probe_played == 5, "第 1 轮真扫 %s 次 != 5" % fake._probe_played)
    r.eq(fake._probe_scan == 5, "第 1 轮连续就绪 %s != 5" % fake._probe_scan)
    r.eq(_ok1 is True, "第 1 轮 _probe_serial() 返回 %r" % _ok1)
    r.eq(len(fake._ok_sids) == 5, "已确认集合 %d 个 != 5" % len(fake._ok_sids))
    if no_cache:
        fake._ok_sids.clear()             # 阴性对照: 删掉 K5 缓存 = "没有 K5"
    _ok2 = fake._probe_serial()
    r.eq(fake._probe_played == 0,
         "第 2 轮仍真扫 %s 次 —— 已确认的 (池,sid) 不该重扫(每次 JNI 往返都算钱)"
         % fake._probe_played)
    r.eq(fake._probe_scan == 5,
         "第 2 轮连续就绪 %s != 5(被跳过的样本仍要算进连续就绪)" % fake._probe_scan)
    r.eq(_ok2 is True, "第 2 轮 _probe_serial() 返回 %r" % _ok2)
    return r


def _g3_neg_nocache():
    """阴性对照: 第 2 轮之前把 `_ok_sids` 清空(等价于"K5 没生效")。

    它证明上面那个 `真扫 == 0` 是**缓存**做出来的, 而不是计数器坏了恒为 0。"""
    return _g3(no_cache=True)


# ==================== G4 K5 的键必须含池序号 ===============================
def _g4(sid_only=False):
    r = R()
    # 池 2 的第 1 个样本永不解码(play 恒返 0) —— 它和池 1 的第 1 个样本**同号**。
    fake = FakeOut(gate_open_after=0, plan2={1: 10 ** 9})
    if sid_only:
        fake._ok_sids = _SidOnlySet()     # 阴性对照: 键只用纯 sid
    _prime(fake, 3, "a")                  # 池 1: sid 1,2,3 —— 全都立刻可播
    _prime(fake, 3, "b", pool=2)          # 池 2: sid 1,2,3 —— 第 1 个永不解码
    _ok = fake._probe_serial()
    r.eq(fake._probe_stuck == "voice_b0",
         "卡在 = %r(应为池 2 那个永不解码的样本 —— 池 1 同号不许把它顶掉)"
         % fake._probe_stuck)
    r.eq(_ok is False, "两池撞号时 _probe_serial() 返回 %r(必须判未就绪)" % _ok)
    r.eq(fake._sp2.plays >= 2,
         "池 2 只被 play 了 %d 次(那个样本从没被验证过就被放行了)" % fake._sp2.plays)
    r.eq(fake._ok_sids == {(0, 1), (0, 2), (0, 3)},
         "已确认集合 = %r(只有池 1 的三个, 池 2 一个都不许进)" % (set(fake._ok_sids),))
    return r


def _g4_neg_sidonly():
    """阴性对照: 把 K5 缓存换成"只用纯 sid 当键"的替身(`_SidOnlySet`)。

    这正是 `_reset_pools` 那条注释警告的形态: 两池 sampleId 各自从 1 编号 ⇒ 池 2 前几个
    没验过就被当成"池 1 同号已确认" ⇒ `_probe_stuck` 空、探针报全就绪 = 假绿。
    断言必须变红。"""
    return _g4(sid_only=True)


# ============================= G5 B1 补试 ===================================
def _g5(need_two=False):
    r = R()
    # 池 1 第 1 个样本要 plan[1] 次 play 才算解码完。
    fake = FakeOut(gate_open_after=0, plan={1: (2 if need_two else 1)})
    _prime(fake, 3, "a")
    _ok = fake._probe_serial()
    r.eq(fake._probe_stuck == "",
         "第一个样本第一次 play 返 0、补试接住了, 却被判未就绪(stuck=%r)"
         % fake._probe_stuck)
    r.eq(_ok is True, "_probe_serial() 返回 %r(补试接住 ⇒ 必须判就绪)" % _ok)
    r.eq(fake._probe_played == 3, "真扫 %s 次 != 3(补试不算「多一遍样本」)" % fake._probe_played)
    r.eq(fake._probe_scan == 3, "连续就绪 %s != 3(补试接住的不许断前缀)" % fake._probe_scan)
    return r


def _g5_neg_needs2():
    """阴性对照: 让那个样本要**两次**补试才解码完 ⇒ 只补一次接不住, 必须判未就绪。

    它同时钉死"补试**只做一次**"这条口径(老版就是只补一次)。"""
    return _g5(need_two=True)


# ========================= G6 6 秒硬超时 ====================================
def _g6():
    r = R()
    fake = FakeOut(gate_open_after=0, default_pending=10 ** 9)   # 全体永不解码
    fake.no_gate()
    _t0 = time.time()
    with fake_backend(fake):
        s = BUS.Sfx(sync=True)
    _dt = time.time() - _t0
    r.eq(s.audio_ready(), "硬超时后 audio_ready() 仍为假 —— **软锁**了(项目红线)")
    r.eq(s._ready_winner == "超时", "先到者 winner=%r(应为「超时」)" % s._ready_winner)
    r.eq(_dt >= BUS.Sfx.SFX_READY_TIMEOUT - 0.5,
         "只等了 %.2fs —— 还没到硬上限就放行了(那是另一条路的 bug, 不是这条)" % _dt)
    r.eq(_dt < BUS.Sfx.SFX_READY_TIMEOUT + 6.0, "等了 %.2fs" % _dt)
    s.close()
    return r


def _g6_neg_nolock():
    """阴性对照: 把硬上限改成 1e9(= **没有**硬超时) ⇒ 循环永不退出 = 软锁。

    跑在后台线程里, 2.5 秒后它**必须还没构造完**(构造没返回就是 `_await_ready` 还在
    转) —— 这一条红了, 才说明上面那条"6 秒必须放行"不是碰巧过的。
    ⚠️ 之后把上限改小(循环条件每轮重读 `self.SFX_READY_TIMEOUT`)让线程自己出来, 不留残线程。
    """
    r = R()
    fake = FakeOut(gate_open_after=0, default_pending=10 ** 9)
    fake.no_gate()
    box = {}

    def _boot():
        with fake_backend(fake):
            box["s"] = BUS.Sfx(sync=True)

    with patched(BUS.Sfx, "SFX_READY_TIMEOUT", 10 ** 9):
        th = threading.Thread(target=_boot, daemon=True)
        th.start()
        time.sleep(2.5)
        r.eq("s" in box,
             "没有硬超时时启动居然返回了 —— 那说明 6 秒上限不是唯一出口, 这条检查要重写")
        BUS.Sfx.SFX_READY_TIMEOUT = 0.02      # 放它出来(循环条件每轮重读)
        th.join(6.0)
    r.eq("s" in box, "兜底上限降下来之后线程仍没出来(线程泄漏)")
    if "s" in box:
        box["s"].close()
    return r


# ========================== G7 语音互斥 ====================================
def _g7(mode="ok"):
    r = R()
    _cls = {"ok": BUS.Sfx, "drop": _DropNewSfx, "nocut": _NoCutSfx}[mode]
    fake = FakeOut(gate_open_after=1, worker=False)
    with fake_backend(fake):
        s = _cls(sync=True)
    _a, _b = "voice_rtp_120", "voice_rtp_200"
    r.eq(s.play(_a, 1.0) is True, "第一句没受理")
    _s1 = fake._voice_stream
    r.eq(_s1 > 0, "第一句没记下流号(%r) —— stop_voice 会停错/停不到" % _s1)
    r.eq(list(fake._sp2.stopped) == [], "第一句之前就掐了别人: %r" % (fake._sp2.stopped,))
    # 非互斥族(音效)不许动语音那条流
    r.eq(s.play("win6", 1.0) is True, "非互斥族的音效没受理")
    r.eq(list(fake._sp2.stopped) == [], "播了个音效却把语音掐了: %r" % (fake._sp2.stopped,))
    r.eq(list(fake._sp.stopped) == [], "音效池被动了(不该有人 stop 它): %r" % (fake._sp.stopped,))
    # 撞上互斥: 掐掉旧的、念新的
    r.eq(s.play(_b, 1.0) is True,
         "撞上互斥时把**新来的那句丢掉了**(play 返回 False) —— 玩家会以为新选择没生效")
    r.eq(list(fake._sp2.stopped) == [_s1],
         "旧的没被掐掉: stopped=%r(应为 [%d])" % (fake._sp2.stopped, _s1))
    r.eq(fake._voice_stream not in (0, _s1),
         "新流号 %r 没记对(旧的是 %d)" % (fake._voice_stream, _s1))
    r.eq(any(nm == _b + ".wav" for nm, _g in fake._sp2.audible),
         "新的那句没真发声: audible=%r" % (fake._sp2.audible,))
    s.close()
    return r


def _g7_neg_drop():
    """阴性对照: `_DropNewSfx` = 老行为(`return False` 把新的丢掉) ⇒ "受理"那条断言变红。"""
    return _g7("drop")


def _g7_neg_nocut():
    """阴性对照: `_NoCutSfx` = 把"掐掉旧的"摘掉 ⇒ "旧流被 stop"那条断言变红。"""
    return _g7("nocut")


# ====================== G8 play 的返回值 = 请求已受理 =======================
def _g8(mode="ok"):
    r = R()
    _cls = _VerdictSfx if mode == "verdict" else BUS.Sfx
    fake = FakeOut(gate_open_after=1, worker=False)
    with fake_backend(fake):
        s = _cls(sync=True)
    _sid = fake._ids.get("click")
    r.eq(_sid is not None, "click 没进池 ⇒ 这条检查验不到受理语义")
    fake._sp.pending[_sid] = 10 ** 9          # 后端这一次一定"没播成"
    _ret = s.play("click", 1.0)
    r.eq(_ret is True,
         "后端 play() 返 0 时 play() 返回了 %r —— 它必须是「请求已受理」"
         "(返回值牵着震动: 改成「后端说了算」会让流一忙就整段没震)" % _ret)
    r.eq(fake._play_missed == 1,
         "「没播成」没被记下来: _play_missed=%d(它是这件事唯一的正面计数)"
         % fake._play_missed)
    r.eq(fake._play_missed_last == "click", "_play_missed_last=%r" % fake._play_missed_last)
    r.eq(s.play("no_such_sfx", 1.0) is False, "池里没有的名字应返回 False")
    s.close()
    return r


def _g8_neg_verdict():
    """阴性对照: `_VerdictSfx` 把返回值换成"后端说了算"(老版 `play_named` 的语义)
    ⇒ "受理仍返真"那条断言变红。"""
    return _g8("verdict")


# ============================ G9 节流键 =====================================
_THROTTLE_WANT = {
    "peg0": "peg", "peg3": "peg", "peg5": "peg",       # 族: peg0..5 共用一道闸
    "wall1": "wall", "div0": "div", "top1": "top",
    "peg": "peg", "pegx": "pegx", "peg-1": "peg-1",    # 不是"前缀+数字"的一律保持原名
    "win6": "win6",
    # ⚠️ 语音这几条是**反例**: 名字里有数字, 但**不是**撞钉/撞墙那套变体族 ——
    #    剥成 voice_rtp_ 会让所有档次/下注的语音共用一道闸, 互相顶掉。
    "voice_rtp_200": "voice_rtp_200", "voice_rtp_": "voice_rtp_",
    "voice_rtp_2x": "voice_rtp_2x", "voice_bet_1000": "voice_bet_1000",
    "voice_mode_off": "voice_mode_off", "": "",
}


def _g9(mode="ok"):
    r = R()
    if mode == "pervariant":
        _fn = lambda n: n                                  # noqa: E731 每个变体各自计时
    elif mode == "strip":
        _fn = lambda n: n.rstrip("0123456789")             # noqa: E731 无脑剥尾巴数字
    else:
        _fn = None
    _ctx = patched(BUS, "_throttle_key", _fn) if _fn is not None else contextlib.nullcontext()
    with _ctx:
        _tk = BUS._throttle_key
        for _k, _v in _THROTTLE_WANT.items():
            r.eq(_tk(_k) == _v, "_throttle_key(%r) = %r, 应为 %r" % (_k, _tk(_k), _v))
        fake = FakeOut(gate_open_after=1, worker=False)
        with fake_backend(fake):
            s = BUS.Sfx(sync=True)
        r.eq(s.play("peg0", 1.0, 0.08) is True, "第一声 peg0 被挡了")
        _second = s.play("peg3", 1.0, 0.08)
        r.eq(_second is False,
             "同族的不同变体各有一个计时器(第二声 peg3 放行了) "
             "⇒ 机关枪连珠时 6 倍速率打 JNI, 每响一声主线程卡几十毫秒")
        r.eq(s.play("click", 1.0, 0.08) is True, "不同族的音效被连带挡了")
        s.close()
    return r


def _g9_neg_pervariant():
    """阴性对照: 节流键改成**名字本身**(= 每个变体各一个计时器) ⇒ 表里 peg3→peg 与
    "第二声被挡"两条都变红。"""
    return _g9("pervariant")


def _g9_neg_strip():
    """阴性对照: 节流键改成"无脑剥掉尾巴数字" ⇒ `voice_rtp_200` 被剥成 `voice_rtp_`,
    与 `voice_rtp_100`/`voice_rtp_50` 撞成同一道闸(选档后只响第一句)。表里必须变红。"""
    return _g9("strip")


# ================== G10 语音列举失败必须可诊断 ==============================
def _g10(hide=False):
    r = R()
    _ctx = patched(BUS, "voice_miss", lambda: "") if hide else contextlib.nullcontext()
    with _ctx:
        # (a) 常态: 语音目录好好的 ⇒ **不**许印那行(有唯一预期值的行只在偏离时才有信息)
        fake = FakeOut(gate_open_after=1, worker=False)
        with fake_backend(fake):
            s = BUS.Sfx(sync=True)
        _rows = s.audio_detail()
        r.eq(not any(x.startswith("语音目录") for x in _rows),
             "语音好好的却印了「语音目录」行: %r" % (_rows,))
        r.eq(any("语音就绪：%d/%d" % (V.N_VOICE, V.N_VOICE) in x for x in _rows),
             "常态的「语音就绪」不是 %d/%d: %r" % (V.N_VOICE, V.N_VOICE, _rows))
        s.close()

        # (b) 目录没打进包(listdir 抛) ⇒ 必须能看到**为什么**
        _old_env = os.environ.get("DANZHU_VOICE_DIR")
        _missing = os.path.join(ROOT, "voice__not_packed")
        r.eq(not os.path.exists(_missing), "测试用的「不存在目录」居然存在: %s" % _missing)
        try:
            os.environ["DANZHU_VOICE_DIR"] = _missing
            V.reset_cache()
            # `reset_cache` 只丢缓存, 不替我们列举 ⇒ 显式列一次(顺带钉住"列举失败会被记下来")
            r.eq(V._voice_files() == {}, "那个目录明明是空的, 却列到了语音")
            r.eq(V.voice_miss() != "", "列举失败却没被 voice_miss() 记下来")
            fake2 = FakeOut(gate_open_after=1, worker=False)
            with fake_backend(fake2):
                s2 = BUS.Sfx(sync=True)
            _rows2 = s2.audio_detail()
            s2.close()
        finally:
            if _old_env is None:
                os.environ.pop("DANZHU_VOICE_DIR", None)
            else:
                os.environ["DANZHU_VOICE_DIR"] = _old_env
            V.reset_cache()
        _row = [x for x in _rows2 if x.startswith("语音目录")]
        r.eq(len(_row) == 1,
             "语音列举失败, 面板里却看不到原因(只有「语音就绪 0/0」): %r" % (_rows2,))
        if _row:
            r.eq(("Error" in _row[0]) or ("空" in _row[0]),
                 "印了行但看不出原因: %r" % _row[0])
        r.eq(any("语音就绪：0/0" in x for x in _rows2),
             "语音全灭时「语音就绪」不是 0/0: %r" % (_rows2,))
    return r


def _g10_neg_hidden():
    """阴性对照: `voice_miss()` 恒返回空串(= "采集了但没接进面板"这个状态)
    ⇒ "面板里看得出为什么"那条断言变红。"""
    return _g10(hide=True)


# ============== G11 撞钉变体: 先判闸、再抽变体 ==============================
class _GateOff(object):
    enabled = False


class _GateOn(object):
    enabled = True


def _g11(ungated=False):
    """`_ARNG` 只在**闸门放行之后**才被消耗(老版 `Sfx.impact` 的语句顺序)。

    静音时多抽一个数不会有人听见, 但切回有声之后整条随机流就与老版错位了 ——
    "同输入 ⇒ 同轨迹"这条确定性破了, 而且 bank 只比 PCM sha1, 对账**看不见**。
    """
    r = R()
    _rng = S.shared_rng()
    _s0 = _rng.getstate()
    try:
        if ungated:
            S.pick_peg_variant(0.9)        # 阴性对照: 不判闸就抽(错位的那个做法)
            _got = 0
        else:
            _got = BUS.peg_variant(0.9, _GateOff())
            r.eq(_got is None, "静音时 peg_variant 返回了 %r(应为 None = 不该发声)" % _got)
        r.eq(_rng.getstate() == _s0,
             "静音时 _ARNG 被消耗了 —— 切回有声后变体序列就与老版错位")
        # 开声必须抽, 且抽的公式与老版**逐位相同**
        _rng.seed(1234)
        _got2 = BUS.peg_variant(0.9, _GateOn())
        _rng.seed(1234)
        _want = int(0.9 * 5.99) + _rng.randint(-1, 1)
        _want = 0 if _want < 0 else (5 if _want > 5 else _want)
        r.eq(_got2 == _want, "开声时抽到 %r, 老版公式给 %r" % (_got2, _want))
        # 同一个入口: 没有 gate(= 没有音效层)一律放行
        r.eq(BUS.peg_variant(0.9) is not None, "无闸时应放行(返回 None 了)")
        # Sfx.impact 自己也必须是"先判闸、再抽" —— 静音时同样一个数都不许消耗
        _rng.setstate(_s0)
        _s1 = _rng.getstate()
        _off = BUS.Sfx(enabled=False)      # 没有后端 ⇒ 不烘焙、不起线程, 空跑
        r.eq(_off.impact(EV_PEG, 900.0) is False, "静音时 impact(EV_PEG) 没返回 False")
        r.eq(_rng.getstate() == _s1,
             "静音时 Sfx.impact 消耗了 _ARNG(变体在闸门之前就被抽走了)")
    finally:
        _rng.setstate(_s0)
    return r


def _g11_neg_ungated():
    """阴性对照: 静音时**不判闸直接抽**(`S.synth.pick_peg_variant`) ⇒ "静音不消耗
    `_ARNG`"那条断言必须变红。这正是重构版此前在 game.py 的 `impact_sound()` 里的做法。"""
    return _g11(ungated=True)


# ============================== 门禁清单 ====================================
CASES = [
    ("G1", "闸门就绪 ⇒ 老探针一次都不跑", _g1, (_g1_neg_deadline,)),
    ("G2", "闸门不可用 ⇒ 真 _probe_serial 兜底", _g2, (_g2_neg_parallel,)),
    ("G3", "K5 增量: 已确认的 (池,sid) 不再重扫", _g3, (_g3_neg_nocache,)),
    ("G4", "K5 的键含池序号: 两池撞号不许假绿", _g4, (_g4_neg_sidonly,)),
    ("G5", "B1 补试: 第一次 play 返 0、补试接住", _g5, (_g5_neg_needs2,)),
    ("G6", "6 秒硬超时: 全体永不解码也必须放行", _g6, (_g6_neg_nolock,)),
    ("G7", "语音互斥: 掐掉旧的、念新的", _g7, (_g7_neg_drop, _g7_neg_nocut)),
    ("G8", "play() 返回值 =「请求已受理」", _g8, (_g8_neg_verdict,)),
    ("G9", "节流键 = 族名(peg3→peg)", _g9, (_g9_neg_pervariant, _g9_neg_strip)),
    ("G10", "语音列举失败 ⇒ 启动信息里看得出为什么", _g10, (_g10_neg_hidden,)),
    ("G11", "撞钉变体: 先判闸、再抽(静音不消耗 _ARNG)", _g11, (_g11_neg_ungated,)),
]


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")]
    n_ck = 0
    n_red = 0
    n_assert = 0
    print("=" * 72)
    print("音频层门禁 (bus / backend / voice) —— 正例全绿 + 阴性对照必须变红")
    print("=" * 72)
    for cid, title, pos_fn, neg_fns in CASES:
        if want and cid not in want:
            continue
        p = pos_fn()
        n_ck += 1
        n_assert += p.n
        bad = ["正例: " + m for m in p.bad]
        notes = []
        for nf in neg_fns:
            o = nf()
            if not o.bad:
                bad.append("阴性对照 %s 没红 —— 这条检查是假绿(恒真)" % nf.__name__)
            else:
                notes.append("      对照 %-18s 变红 %d 条(例: %s)"
                             % (nf.__name__, len(o.bad), o.bad[0][:70]))
        if bad:
            n_red += 1
            print("[红] %s %s: %d/%d 不符" % (cid, title, len(bad), p.n))
            for m in bad[:8]:
                print("      " + m)
            for m in notes:
                print(m)
        else:
            print("[绿] %s %s: %d 项相符" % (cid, title, p.n))
            for m in notes:
                print(m)
    print()
    if n_red:
        print("门禁结果: 有红 —— %d/%d 条检查未过, 共 %d 项断言" % (n_red, n_ck, n_assert))
        return 1
    print("门禁结果: 全绿 —— %d 条检查 / %d 项断言(每条都做过阴性对照)" % (n_ck, n_assert))
    return 0


if __name__ == "__main__":
    sys.exit(main())
