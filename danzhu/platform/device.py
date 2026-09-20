"""平台胶水: 震动 / 守卫 / 沉浸与方向锁 / 设定落盘 / CPU 频率 / 高刷 / JNI 计时。

⚠️ 所有 pyjnius / android 的 import 一律在函数体内或 try 里 —— **桌面 import 期必须成功**;
   `platform != "android"` 的分支就是降级路径, 照抄老版, 别另发明一套。
⚠️ `platform` / `_FPS_*` / `_VIB_*` / `_GUARD_Q` / `_CFG_*` / `_JNI_STAT` / `_SYSUI_MODE` /
   `_WAKE_MODE` 是**跨模块共享状态**: 调用方一律 import 这里的同一份, 另建一份不会报错,
   只会静默漂移(标志设了不生效 / 计数永远 0)。
⚠️ 帧剖析那一组(`_FRAME_BRK` / `_FRAME_CALLS` / `_brk_add` / `_brk_wrap`) **不在本模块**,
   在 `danzhu/ui/text.py` —— 本模块原来也有一份, 是拆分造出来的重复(见 `_cpu_split` 下方那段说明)。
"""

import json
import os
import sys
import threading
import time

from ..config import (FPS_CAP_DEFAULT, FPS_CAP_FALLBACK,
                      FPS_CAP_OPTIONS, FPS_CAP_PC, _FPS_INFO, _FPS_USER_CAP)

# 老版用的是 `from kivy.utils import platform`; 桌面没有 kivy 也必须能 import ⇒ 取不到就
# 用等价判定(与 audio/backend 的 `_detect_platform` 同口径)。
try:
    from kivy.utils import platform
except Exception:
    platform = ("android" if (os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_ROOT"))
                else ("win" if os.name == "nt" else sys.platform))


# ======================== CPU 频率(跑分期的调频状态) ========================
# ⚠️ 采样必须放**工作线程**: 读 sysfs 每次几十微秒, 而这一格恰恰是用来解释 `主线程ms` 的
#    —— 埋点自己抬高被解释的那个数就自相矛盾了。
# ⚠️ 拿不到(桌面 / 被 SELinux 挡住)一律当"读不到": 日志里那一段**不印**, 不印一行 0 假装量到了。
_CPUFRQ = {"freqs": [], "cap": 0.0, "stop": True, "thr": None}

# `PythonActivity` 的**类对象**缓存 —— 见 `_activity_cls()`。
# ⚠️ 存在的唯一理由: `autoclass()` 每次调用都走一趟 JNI `FindClass`, 而本工程**实测它是
#    毫秒级的**(老版 `android/temp/adv_out.md` C7)。它热的路径 `_bench_hz_tick` →
#    `_screen_hz()` 在采样窗口里每 0.5 秒调一次 ⇒ 25 秒约 50 次**主线程**卡顿。
#    缓存类对象把那 50 次查表砍掉, 只留 `getRefreshRate()` 那一下。
# ⚠️ **只缓存类, 不缓存 `mActivity`**: activity 重建后旧引用会失效, 那个必须每次现取。
_ACT_CLS = [None]


def _cpufreq_cores():
    """逐核当前频率 `[(核号, MHz请求值, MHz实际值), ...]`, 只列**读得到**的核。

    ⚠️ 必须逐核: `_cpufreq_mhz()` 报的是各核**最大值**, 而"跑分那条线程跑在哪个核"是 OS
       调度决定的 —— 真机实测最大频率只差 9% 而物理跑分差 69%, "最大值"在这一问上没有分辨力。
    ⚠️ 第三个字段是 `cpuinfo_cur_freq`(实际值), **不是** `scaling_cur_freq`(请求值): 高通平台
       上后者读的是调频器的目标值, 实际时钟可以被温控按下去而不回写。读不到就是 0, **不编数**。
    ⚠️ 这里**不做** `_cpufreq_mhz()` 那个"连续两核读不到就停"的提前退出 —— 逐核要的就是完整
       的一张表, 中间缺一个核会让人以为是"那个核不存在"。
    """
    out = []
    for i in range(12):                     # 8 核封顶, 留点余量给 12 核的机器
        _dir = "/sys/devices/system/cpu/cpu%d/cpufreq/" % i
        try:
            with open(_dir + "scaling_cur_freq") as fh:
                v = int(fh.read().strip() or 0)
        except Exception:
            continue
        _a = 0
        try:
            with open(_dir + "cpuinfo_cur_freq") as fh:
                _a = int(fh.read().strip() or 0)
        except Exception:
            _a = 0                            # 读不到就是 0, **不编数**
        if v > 0:
            out.append((i, v / 1000.0, _a / 1000.0))
    return out


def _cpufreq_mhz():
    """当前 CPU 频率(MHz): 取各核**最大值**(调度会把跑得最多的核拉到最高)。

    ⚠️ 逐核试到连续两核读不到就停 —— 真机核数不一(4/8), 没必要每次开 8 个文件。
    """
    best = 0
    miss = 0
    for i in range(8):
        try:
            with open("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq" % i) as fh:
                v = int(fh.read().strip() or 0)
            if v > best:
                best = v
            miss = 0
        except Exception:
            miss += 1
            if miss >= 2:
                break
    return best / 1000.0


def _cpufreq_cap_mhz():
    """这台机器的 CPU 最高频(MHz)。读不到返回 0。"""
    for _p in ("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq",
               "/sys/devices/system/cpu/cpu7/cpufreq/cpuinfo_max_freq"):
        try:
            with open(_p) as fh:
                v = int(fh.read().strip() or 0)
            if v > 0:
                return v / 1000.0
        except Exception:
            pass
    return 0.0


def _cpufreq_worker():
    """后台每 0.5 秒采一次主频, 直到 `stop`。开销 ≈ 每 0.5 秒两次小文件读。"""
    while not _CPUFRQ["stop"]:
        _v = _cpufreq_mhz()
        if _v > 0:
            _CPUFRQ["freqs"].append(_v)
        time.sleep(0.5)


def _cpufreq_start():
    _CPUFRQ["freqs"] = []
    _CPUFRQ["cap"] = _cpufreq_cap_mhz()
    _CPUFRQ["stop"] = False
    try:
        _CPUFRQ["thr"] = threading.Thread(target=_cpufreq_worker, daemon=True)
        _CPUFRQ["thr"].start()
    except Exception:
        _CPUFRQ["thr"] = None
        _CPUFRQ["stop"] = True
    # 起步立刻采一次: 窗口只有 25 秒、0.5 秒一次, 早采一点才看得出"前期就低"。
    _v = _cpufreq_mhz()
    if _v > 0:
        _CPUFRQ["freqs"].append(_v)


def _cpufreq_stop():
    _CPUFRQ["stop"] = True
    _CPUFRQ["thr"] = None


def _fps_user_cap():
    """当前用户选择的上限；异常值/坏存档一律回到 `FPS_CAP_DEFAULT`，避免把循环设成 0。

    ⚠️ 文档串原来写的是"回到 120" —— 那是 `FPS_CAP_DEFAULT` 还等于 120 时的旧文案，
       现在回的是 `FPS_CAP_DEFAULT`(跟面板最高档)，**别照那句话去改代码**。
    """
    try:
        cap = int(_FPS_USER_CAP[0])
        return cap if cap in FPS_CAP_OPTIONS else FPS_CAP_DEFAULT
    except Exception:
        return FPS_CAP_DEFAULT


def _android_system_fps_cap(activity):
    """读取 Android 用户/系统设置的峰值刷新率上限；读不到返回 0 表示未知。"""
    try:
        from jnius import autoclass
        settings = autoclass("android.provider.Settings$System")
        key = settings.PEAK_REFRESH_RATE
        cap = float(settings.getFloat(activity.getContentResolver(), key, 0.0))
        return cap if cap > 1.0 else 0.0
    except Exception:
        return 0.0


def _activity_cls():
    """缓存 `PythonActivity` 的**类对象**(不是 activity 本身)。拿不到返回 None。

    ⚠️⚠️ **为什么必须缓存**: `autoclass()` 每次调用都要走一趟 JNI `FindClass`, 本工程
       **实测过它是毫秒级的** —— 老版对抗审查的原话:
         「简报引的 `ui_worst 3.8~7.1ms` 不能当 `setSystemUiVisibility` 的代价:
           **计时块里含两次 `autoclass(...)`, 那才是毫秒级的东西** —— 探针在测自己的查表开销。」
       (`android/temp/adv_out.md` C7; `autoclass` 内部**没有**对本工程可依赖的缓存。)
    ⚠️ 它热的路径是**采样窗口内每 0.5 秒一次**(`_bench_hz_tick` → `_screen_hz`): 25 秒的
       窗口 ≈ **50 次**。这正是"方向守卫/沉浸重申每 0.7 秒一记"那条被搬走之前的形状 ——
       那两条当时就是「**周期性停顿里唯一的常驻项**」。⇒ 把每次 ms 级的查表砍掉,
       只留 `getRefreshRate()` 那一下(廉价), 是这一刀的全部目的。
    ⚠️ 只缓存**类对象**: `mActivity` 必须每次现取(进程重建 / activity 重建后旧引用会失效)。
    """
    c = _ACT_CLS[0]
    if c is not None:
        return c
    try:
        from jnius import autoclass
        c = autoclass("org.kivy.android.PythonActivity")
        _ACT_CLS[0] = c
    except Exception:
        return None
    return c


def _screen_hz():
    """当前屏幕刷新率(Hz)。拿不到返回 None。

    ⚠️ `getRefreshRate()` 给的是**当前模式**的刷新率, 会随智能刷新率/外接屏变 ——
       所以切回前台时会再算一次(见 `_apply_fps_cap` 的调用点)。
    ⚠️ `autoclass` 走 `_activity_cls()` 的**缓存**(毫秒级查表, 见那里的说明) ——
       这一句省下来的正是采样窗口里那约 50 次主线程卡顿。
    """
    if platform != "android":
        return None
    try:
        _c = _activity_cls()
        if _c is None:
            return None
        act = _c.mActivity
        disp = act.getWindowManager().getDefaultDisplay()
        try:
            return float(disp.getMode().getRefreshRate())     # API 23+
        except Exception:
            return float(disp.getRefreshRate())
    except Exception:
        return None


def _request_android_high_hz():
    """按"屏幕支持、Android 系统上限、用户档位"三者最小值请求显示模式。

    `Window.setFrameRate(target)`(API 30+)告诉系统此窗口的期望节拍; `preferredDisplayModeId`
    (API 23+)兼容旧系统, 并在自适应刷新率机器上给出明确的高刷模式偏好。两者都是**系统可以
    拒绝**的请求: 省电模式、温控或用户强制 60Hz 时绕不过去。
    """
    if platform != "android":
        return (0.0, float(min(FPS_CAP_PC, _fps_user_cap())))
    try:
        from jnius import autoclass
        # ⚠️ 走 `_activity_cls()` 的缓存 —— 与 `_screen_hz` 同一个理由(autoclass 是毫秒级)。
        #    本函数在**生命周期切换**时被调(`_apply_fps_cap`: 回前台 / 弹窗关闭 / 切上限),
        #    而那些时刻正落在采样窗口里。
        _ac = _activity_cls()
        if _ac is None:
            return (0.0, float(_fps_user_cap()))
        activity = _ac.mActivity
        window = activity.getWindow()
        display = window.getWindowManager().getDefaultDisplay()
        current = display.getMode()
        cw, ch = int(current.getPhysicalWidth()), int(current.getPhysicalHeight())
        modes = list(display.getSupportedModes())
        same_size = [m for m in modes
                     if int(m.getPhysicalWidth()) == cw and int(m.getPhysicalHeight()) == ch]
        candidates = same_size or modes

        def hz(mode):
            return float(mode.getRefreshRate())

        screen_cap = max((hz(m) for m in candidates), default=0.0)
        system_cap = _android_system_fps_cap(activity)
        # 读不到系统设置时不能凭空把它当 60Hz；让系统最终拒绝/降档，并由“屏幕 Hz”回读真值。
        if system_cap <= 0.0:
            system_cap = screen_cap
        target = min(screen_cap, system_cap, float(_fps_user_cap()))
        # ⚠️⚠️ **跑分/高压期间也要把"对系统的请求"按下去**(2026-09-15 真机抓到的 bug)。
        #    只压 `Clock._max_fps` 而**不压这里**是**错的**: app 会一边告诉系统"我要 120Hz"、
        #    一边只画 60 —— 那正是 ② 那次"**设备整个塌一次**"的同款不一致状态。
        #    真机实测(游戏120+OS120, 只压 Kivy): 实际渲染 **70.5fps, 没按住**;
        #    而同一版里游戏60+OS60(请求本来就是 60)就按住了(57.0)。
        #    ⚠️ `setFrameRate` 是**请求**, 系统可以拒绝(省电/温控/用户强制) —— 所以日志里
        #    那一行「实际渲染」永远是最终判据, 不能拿这一行当"已经按住了"。
        if _BENCH_FPS_LOCK[0] > 0:
            target = min(target, float(_bench_fps_force_now()))
        at_or_below = [m for m in candidates if hz(m) <= target + 0.5]
        # 优先不超过三者共同上限的最高同分辨率模式；没有精确档位时宁可保守降档，
        # 不绕过 Android 系统的峰值刷新率设定。
        chosen = (max(at_or_below, key=hz) if at_or_below else
                  min(candidates, key=hz) if candidates else None)
        mode_id = int(chosen.getModeId()) if chosen is not None else 0
        mode_hz = hz(chosen) if chosen is not None else 0.0
        target = min(target, mode_hz) if mode_hz > 0.0 else target
        sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)

        from android.runnable import run_on_ui_thread

        @run_on_ui_thread
        def apply_request(win, requested_mode_id, requested_hz, api_level):
            # Window API 从 Android 11 起可用；即使没有列出 mode，也保留高刷窗口请求。
            if api_level >= 30:
                try:
                    win.setFrameRate(float(requested_hz))
                except Exception:
                    pass
            if requested_mode_id:
                try:
                    attrs = win.getAttributes()
                    attrs.preferredDisplayModeId = requested_mode_id
                    win.setAttributes(attrs)
                except Exception:
                    pass

        apply_request(window, mode_id, mode_hz, sdk)
        return (mode_hz, target)
    except Exception:
        return (0.0, float(_fps_user_cap()))


def _refresh_screen_hz(*_):
    """显示模式请求异步生效后，刷新诊断面板中的实际 Hz。"""
    hz = _screen_hz()
    if hz:
        _FPS_INFO[0] = float(hz)

# ⚠️⚠️ **跑分/高压期间把帧率按到 10fps**(玩家 2026-09-15 定案)。
#    **为什么需要**: 物理跑分跑在工作线程、分母是它自己的 `thread_time`, 按构造与刷新率无关,
#    但真机实测同一台 K90: 渲染 120.3fps 时 18943 步/秒, 渲染 80.4fps 时 21580(**差 13.9%**),
#    而两次的纯算术探针只差 1.1% ⇒ 那 14% 是渲染在抢内存/缓存, **归一化也抓不干净**
#    (归一化值 535 vs 603 仍差 12.7%) ⇒ 只能把渲染本身压住。
#    ⚠️ 两波**同档**(10fps 是玩家定的: 波 2 也一样), 而**渲染窗口那一档不受影响**
#       (它测的就是渲染帧率, 压它就没意义了) —— 闸门只在波 1 / 波 2 期间开。
#    ⚠️ "请求 60" **不等于** "拿到 60"(真机请求 60 而系统 120, 实测渲染 80.4fps) ⇒
#       日志里必须**同时印出实际渲染了多少帧/秒**, 否则没法知道按没按住。
_BENCH_FPS_FORCE = 10        # 波 2(高压)上限
_BENCH_FPS_FORCE_PHYS = 10   # 波 1(物理跑分)上限 —— 见上面那段
_BENCH_FPS_LOCK = [0]        # >0 = 正在跑分/高压
_BENCH_FPS_PHYS = [0]        # >0 = **波 1** 正在跑(分档留着, 两档同值的理由见上)


def _bench_fps_force_now():
    """当前该按到多少: 波 1 用 `_BENCH_FPS_FORCE_PHYS`, 其余用 `_BENCH_FPS_FORCE`。"""
    return _BENCH_FPS_FORCE_PHYS if _BENCH_FPS_PHYS[0] > 0 else _BENCH_FPS_FORCE


def _bench_fps_lock_on(phys=False):
    """按住帧率上限(可重入)。跑分/高压开始时调。`phys=True` 表示这是**波 1**。"""
    _BENCH_FPS_LOCK[0] += 1
    if phys:
        _BENCH_FPS_PHYS[0] += 1
    try:
        _apply_fps_cap()
    except Exception:
        pass


def _bench_fps_lock_off(phys=False):
    """松手(计数归零才真的恢复)。跑分/高压结束**必须**在 `finally` 里调。"""
    _BENCH_FPS_LOCK[0] = max(0, _BENCH_FPS_LOCK[0] - 1)
    if phys:
        _BENCH_FPS_PHYS[0] = max(0, _BENCH_FPS_PHYS[0] - 1)
    if _BENCH_FPS_LOCK[0] == 0:
        try:
            _apply_fps_cap()
        except Exception:
            pass


def _apply_fps_cap():
    """重申实际帧率上限 = min(屏幕支持, Android系统上限, 用户设定)。"""
    hz = _screen_hz()               # 只用于面板显示
    requested_hz, cap = _request_android_high_hz()
    try:
        cap = float(cap)
    except Exception:
        cap = float(FPS_CAP_FALLBACK)
    _FPS_INFO[0] = float(hz or 0.0)
    _FPS_INFO[1] = cap
    _FPS_INFO[2] = requested_hz
    # ⚠️⚠️ **这里原来有一段「165Hz 面板专用: 把生效档降到 118」的特判, 2026-09-20 已删。**
    #    它确实有效(实测 `1%Low` 122 vs 112), 但三条理由让它不值 —— **别再捡回来**:
    #      ① **帮不到最需要它的人**: 判据是"玩家从没手动选过档"(`_fps_user_cap() ==
    #         FPS_CAP_DEFAULT`), 而 0.8.81 之前的默认档就是 **120** ⇒ 老存档里存着 120 的
    #         玩家判据不成立、**特判不生效**, 他会一直停在 120。**恰好漏掉最需要它的那批人。**
    #      ② **判据本身分不清两种情况**: "玩家明确把滑条拖到顶格"与"从没选过"在存档里
    #         长得一模一样(初值就是 `FPS_CAP_DEFAULT`)。
    #      ③ 一个"只对某个面板、只对某种存档状态生效"的例外, 维护成本大于它买到的那点分数
    #         —— 撤的时候已经发现有两处注释/一处 changelog 还在论证旧的 120, 两条测试基线
    #         (`ast_parity` / `sig_parity`)也忘了重采。
    #    ⇒ 要试这条路的玩家**自己在设置里选 120 档**(`FPS_CAP_OPTIONS` 里有)。
    #    ⇒ 完整实测账(含 118 是最优档的证据)见 `changelog/2026-09-20.md` 第 30/40/41 条。
    # ⚠️⚠️ **跑分/高压期间按到 `_BENCH_FPS_FORCE`**(见那段说明)。闸门**必须挂在这儿**
    #    —— 这是"帧率上限"唯一的生效点。散在各调用点的话, 跑分途中任何一次
    #    `_apply_fps_cap`(弹窗关闭 / 切回前台)都会把上限**悄悄恢复**, 而日志上看不出来。
    if _BENCH_FPS_LOCK[0] > 0:
        try:
            cap = min(float(cap), float(_bench_fps_force_now()))
        except Exception:
            pass
        _FPS_INFO[1] = cap
    try:
        from kivy.config import Config    # 老版在模块顶部 import(桌面 import 期不许拖 kivy)
        Config.set("graphics", "maxfps", str(int(cap)))
        # 窗口创建前已设过；这里保留运行期状态供诊断，并防止配置被其他代码改回去。
        Config.set("graphics", "vsync", "1")
    except Exception:
        pass
    try:
        from kivy.clock import Clock
        Clock._max_fps = cap          # 真正生效的那个(见本段顶部说明)
    except Exception:
        pass
    if platform == "android":
        try:
            Clock.schedule_once(_refresh_screen_hz, 0.6)
        except Exception:
            pass
    return cap


def _cpu_split():
    """读 `/proc/self/stat` 的 utime(14)/stime(15), 返回 (用户态秒, 内核态秒) 或 None。

    ⚠️ **只适合整窗口统计, 不能逐帧**: 安卓/Linux 的 USER_HZ 是 100 ⇒ 一个 tick = 10 毫秒,
       而一帧只有 12 毫秒 —— 逐帧读等于全是量化台阶。整窗口的占比足够把候选池切两半:
         内核态占比高 ⇒ JNI/Binder/logd socket 写/文件写回 这一族;
         用户态占比高 ⇒ 纯 Python 的 CPU 竞争(GIL)这一族。
    ⚠️ 解析必须**从最后一个 ')' 之后切**: 进程名那段带括号且可能含空格。
    """
    try:
        with open("/proc/self/stat", "r") as f:
            raw = f.read()
        rest = raw[raw.rfind(")") + 2:].split()
        hz = 100.0
        try:
            import os as _os
            hz = float(_os.sysconf("SC_CLK_TCK")) or 100.0
        except Exception:
            pass
        return (int(rest[11]) / hz, int(rest[12]) / hz)     # 14-3=11, 15-3=12
    except Exception:
        return None


# 帧剖析的共享计数器**不在这里** —— 它们住在 `danzhu/ui/text.py`(`_FRAME_BRK` / `_FRAME_CALLS`
# / `_brk_add` / `_brk_wrap` / `_FRAME_END` / `_FRAME_SWAP` / `_FRAME_FIT` / `_COLD_FS*`)。
# ⚠️ 为什么归一到一个模块: 老版这些全在同一个文件里, 拆分时两边各建了一份 —— 两份**不报错**,
#    只让分段耗时永远为空、面板那一栏静默失效(读它的只有跑分面板一处, 没有任何测试对账它)。
#    写入口(`_clock_wrap` / `_swap_wrap`)在 text.py, 所以家在那边。`tests/global_dedup.py` 钉这条。
# "慢帧"的判定门槛(毫秒)。只此一处 —— 判据与面板文案都读它, 免得两处各写一个数漂掉。
BENCH_SLOW_MS = 90.0

# ---- 震动: 工作线程 + 系统服务代理缓存 + 单次计时 ----
# ⚠️ 和发声同一个理由, 而且它还多一处浪费: `Vibrator.vibrate` 是**双程 Binder**(主线程不该
#    等 IPC); 而 `activity.getSystemService(VIBRATOR_SERVICE)` **本身就是一次 Binder 往返**,
#    取到的代理长期有效 ⇒ 缓存它等于每次震动白省一次 IPC。
_VIB_Q = None                    # 震动工作队列(None = 还没建)
_VIB_LOCK = threading.Lock()
_VIB_PROXY = [None]              # [缓存的 Vibrator 代理]
_VIB_STAT = [0.0, 0.0, ""]       # [累计秒, 单次最慢秒, 最慢那次的描述]

# 方向守卫 / 沉浸重申的耗时统计:
#   [0]主线程投递累计秒 [1]主线程单次最慢秒 [2]发起次数
#   [3]工作线程累计秒   [4]工作线程单次最慢秒 [5]工作线程失败次数
#   [6]UI线程累计秒     [7]UI线程次数        [8]UI线程单次最慢秒
# ⚠️ **必须定义在模块级**(不是某个类的类属性): 用它的人写的都是**裸名**, 裸名找的是模块全局,
#    写成类属性会 NameError(本文件踩过一次, 探针逮住的)。
# ⚠️ 这两条链**每 0.7 秒**各跑一次且跑分期间照跑 —— 它们是全 app 唯一的常驻周期性主线程 JNI,
#    而 1%Low 只看最差的 1%(约十几帧)。搬出主线程后的判据是:
#    **主线程那一档要接近 0, 而工作线程那一档接手**(两边都看得见, 才知道是真搬走了还是没跑)。
_JNI_STAT = [0.0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0, 0.0]

# ⚠️⚠️ **系统栏模式** —— [False] = **非沉浸**(状态栏可见, 就是一个标志都不设);
#    [True] = **真全屏**(状态栏 + 导航栏都藏, 边缘滑入可临时呼出)。
#    平时非沉浸、跑分黑屏期间(波 1 + 波 2)真全屏, 开关挂在 `_show_bench_dim` / `_hide_bench_dim`。
#    ⚠️ **别再把它写成常量**: 两档都是玩家点名的需求。
_SYSUI_MODE = [False]

# ⚠️⚠️ **防息屏标志** —— [False] = 正常(允许系统按超时息屏); [True] = **屏幕常亮**。
#    实现用**窗口标志**(`FLAG_KEEP_SCREEN_ON`)而不是 `PowerManager.WakeLock`: 前者**不需要
#    权限**(本工程 `android.permissions` 只声明了 VIBRATE), 且**随窗口可见性自动失效**。
#    ⚠️ 这**不是体验优化, 是正确性**: 屏幕一灭安卓会暂停游戏、冻结 Clock ⇒ 6 分钟的高压
#       测试会被打断, 跑出来的成绩是假的。
# ⚠️ 与 `_SYSUI_MODE` 两个标志**共用同一个 UI 线程任务**, 所以切一次只投一趟 JNI。
_WAKE_MODE = [False]


def _vib_get():
    """取(并缓存)Vibrator 系统服务代理。

    ⚠️ 必须用 `Context.VIBRATOR_SERVICE` **字符串** —— 传 `autoclass("android.os.Vibrator")`
       那个 Class 对象在 pyjnius 下匹配不到 `getSystemService(Class<T>)` 重载, 会**静默失败**
       (整段被 try 吞掉, 表现为"权限也给了、代码也跑了, 就是不震")。
    ⚠️ 代理失效(Binder 断了)时 `vibrate` 会抛 —— 那时把缓存清掉, 下次重新取。
    """
    v = _VIB_PROXY[0]
    if v is not None:
        return v
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        _VIB_PROXY[0] = v = activity.getSystemService(Context.VIBRATOR_SERVICE)
    except Exception:
        v = None
    return v


def _vibrate_now(ms, amp):
    """真正那一次震动调用(只在工作线程上跑)。带单次计时, 供跑分面板归因。"""
    _t0 = time.perf_counter()
    try:
        vib = _vib_get()
        if vib is None:
            return
        try:
            from jnius import autoclass
            VibrationEffect = autoclass("android.os.VibrationEffect")
            vib.vibrate(VibrationEffect.createOneShot(
                ms, amp))     # 满振幅 255; DEFAULT_AMPLITUDE(-1) 约 50%, 太弱
        except Exception:
            vib.vibrate(ms)                  # API < 26: 没有 VibrationEffect
    except Exception:
        _VIB_PROXY[0] = None                 # 代理失效: 清缓存, 下次重取
    finally:
        _dt = time.perf_counter() - _t0
        _VIB_STAT[0] += _dt
        if _dt > _VIB_STAT[1]:
            _VIB_STAT[1] = _dt
            _VIB_STAT[2] = "%dms" % ms


def _vib_worker():
    """震动工作线程。队列满就**丢这一次**(与发声同策) —— 绝不阻塞主线程。"""
    while True:
        try:
            item = _VIB_Q.get()
        except Exception:
            return
        if item is None:
            return
        # 两种形状: ("d", ms, gap, amp) = 双震; (ms, amp) = 单次(老形状保留, 防漏改)。
        if len(item) == 4 and item[0] == "d":
            _vibrate_double_now(item[1], item[2], item[3])
        else:
            _vibrate_now(item[0], item[1])


def _vib_warm():
    """启动期把震动这条路**焐热**: 建工作线程 + 预取 Vibrator 代理。

    ⚠️ **必须预热**: 工作线程原本是**第一次震动时才建**的, 而第一次震动正好落在**第一次发射**
       那一刻 —— 也就是跑分采样窗口**里面**。于是那一帧要现付: 建线程 + 首次 JNI 的
       `AttachCurrentThread`(安卓上可能几十毫秒) + 首次 `getSystemService`。1%Low 只统计最慢
       的 1%(约 12 帧), **一帧 200ms 就能把那一档的均值明显拉下去**。
       与其它预热(玻璃贴图/球纹理/字形表)是同一条规矩: **别在采样/中奖那帧现做**。
    """
    if platform != "android":
        return
    global _VIB_Q
    if _VIB_Q is None:
        with _VIB_LOCK:
            if _VIB_Q is None:
                try:
                    import queue as _queue
                    _VIB_Q = _queue.Queue(maxsize=32)
                    threading.Thread(target=_vib_worker, daemon=True).start()
                except Exception:
                    _VIB_Q = False
    _vib_get()                            # 顺手把系统服务代理也取回来


def _vibrate(ms, amp=255):
    """单次震动(仅 Android; 其它平台静默)。需要 buildozer.spec 的 VIBRATE 权限。

    ⚠️ **投递即返回, 不等 IPC**。`amp` 只在 API 26+ 生效; 老机器退回 `vibrate(ms)`, 振幅由系统定。
    """
    if platform != "android":
        return
    global _VIB_Q
    from ..audio.backend import _FRAME_PROBE   # 调用点导入: 它的家在 audio(见顶部说明)
    _FRAME_PROBE[1] += 1                     # 逐帧计数记在**发起**这一刻(工作线程不再属于某一帧)
    if _VIB_Q is None:
        with _VIB_LOCK:
            if _VIB_Q is None:
                try:
                    import queue as _queue
                    _VIB_Q = _queue.Queue(maxsize=32)
                    threading.Thread(target=_vib_worker, daemon=True).start()
                except Exception:
                    _VIB_Q = False       # 建不起来: 退回同步调用, 绝不静默失震
    if _VIB_Q is False:
        _vibrate_now(ms, amp)
        return
    try:
        _VIB_Q.put_nowait((ms, amp))
    except Exception:
        pass

# ============ 方向守卫 / 沉浸重申: 搬出主线程 ============
# 病根: 这两条链原来**每 0.7 秒各在主线程跑一次**, 而且是全 app 唯一的**常驻周期性主线程
# JNI**。JNI/Binder 在本工程已实测过是灾难级的慢(`SoundPool.play()` 单次 143.6ms)。
# 改法与发声/震动同策: 主线程只投递, 工作线程去付 IPC 的钱; 建不起队列就退回同步(= 今天
# 的行为), **绝不静默失效**。
_GUARD_Q = None                  # 守卫工作队列(None = 还没建, False = 建不起来)
_GUARD_LOCK = threading.Lock()


_BENCH_ORIENT_LOCK = [False]
"""跑分黑屏期间是否**锁死屏幕方向**(玩家 2026-09-17: 黑屏跑分时要禁止屏幕旋转)。

⚠️ 用**单元素 list** 而不是裸 bool —— 守卫(`_guard_orient_now`)跑在**工作线程**上, 而这个
   文件里 `_SYSUI_MODE` / `_FPS_INFO` 那一批跨线程标志**全是单元素 list** 的写法。统一口径,
   免得以后有人改成裸变量 + `global` 语句时漏掉一处(那会变成"设了没生效"这种最难查的毛病)。
"""


def _guard_orient_now():
    """方向守卫的**真身**(只在工作线程上跑)。

    ⚠️ 病根: 原来宽屏设备**只在横置(rot 1/3)时才重申**, **竖置时什么都不设** ⇒ 平板竖着跑分
       时方向完全敞开; 而跑分是 **360 秒**的长过程, 中途一转: `LandLayer` 要重排整棵树、
       `_veq()` 跟着变、黑屏矩形/白字的位置全要重算 —— 玩家看到的"横屏转竖屏"那类诡异现象
       就是这么来的。
    ⚠️ `LOCKED`(14, API 18+) = 锁定**当前**方向、不随传感器变 ⇒ 跑分期间画面钉死。
       **别用 `NOSENSOR`(5)**: 那个的语义是"用 manifest 声明的方向"(平板横竖都可能)。
    ⚠️ 锁只覆盖**黑屏那一段**(开关在 `_show_bench_dim` / `_hide_bench_dim`), 平时照旧分流。
    """
    from jnius import autoclass
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    if _BENCH_ORIENT_LOCK[0]:
        act.setRequestedOrientation(14)
        return
    if _device_is_wide():
        rot = act.getWindowManager().getDefaultDisplay().getRotation()
        if rot in (1, 3):
            act.setRequestedOrientation(10)
    else:
        act.setRequestedOrientation(7)


def _set_system_ui(immersive):
    """**切系统栏档位**(玩家 2026-09-16 要的两档)。

    `immersive=True`  ⇒ **真全屏**(状态栏 + 导航栏都藏) —— 跑分黑屏期间用;
    `immersive=False` ⇒ **非沉浸**(两栏都在) —— 平时用, 就是一个标志都不设。

    ⚠️ **切档必须"立刻生效一次"**, 不能只改标志位等下一个 2.5 秒的周期重申 —— 否则从真全屏
       切回非沉浸时, 系统栏会**继续藏着**最多 2.5 秒(黑屏都撤了还全屏着)。
    ⚠️ 走 `_guard_post` 那条**工作线程队列**而不是自己起线程: 队列满就丢这一次 —— 幂等,
       丢一次毫无影响(真要紧了 2.5 秒后还有一次)。
    ⚠️ 非安卓直接只改标志位(桌面没有系统栏可切), 与 `_enter_immersive` 的分支一致。
    """
    _SYSUI_MODE[0] = bool(immersive)
    if platform != "android":
        return
    try:
        ok = _guard_post("immerse")
        if not ok:
            _guard_immersive_now()
    except Exception:
        pass


def _set_orient_lock(on):
    """**跑分黑屏期间锁死屏幕方向**(玩家 2026-09-17)。

    ⚠️ 与 `_set_system_ui` **逐字同款**: 改标志 + **立刻投一次**。不能只改标志、等下一个 2.5 秒
       的周期重申 —— 那 2.5 秒里玩家转一下屏幕就出事: 黑屏矩形是 `_relayout_bench_dim` 算好的,
       旋转会让 GameArea 重排而矩形不一定跟上, 玩家报的「会出现非黑屏画面」就是这么来的。
    ⚠️ 非安卓只改标志位(桌面没有方向可锁), 与那两处的分支一致。
    """
    _BENCH_ORIENT_LOCK[0] = bool(on)
    if platform != "android":
        return
    try:
        ok = _guard_post("orient")
        if not ok:
            _guard_orient_now()
    except Exception:
        pass


def _set_keep_awake(on):
    """**跑分期间不让屏幕息屏**(玩家 2026-09-16 报的 bug)。

    `on=True` ⇒ 屏幕常亮(跑分期间); `on=False` ⇒ 交还给系统按超时息屏(跑完就还回去)。

    ⚠️ 与 `_set_system_ui` **同一套机制**: 改标志位 + **立即投一次任务** —— 否则跑分开始后最多
       有 2.5 秒仍可能息屏, 而**屏幕一灭测试就废了**。
    ⚠️ 两者**共用同一个 Runnable**: 那个任务会把**两个标志位一起**应用, 所以这里投的那一趟
       顺带也把系统栏状态重申了一次(幂等, 无副作用)。
    """
    _WAKE_MODE[0] = bool(on)
    if platform != "android":
        return
    try:
        ok = _guard_post("immerse")
        if not ok:
            _guard_immersive_now()
    except Exception:
        pass


def _guard_immersive_now():
    """沉浸重申的**真身**(只在工作线程上跑)。

    ⚠️ 真正的 View 操作本来就已在 UI 线程上(`runOnUiThread` 投递), 这里搬走的只是
       **那一次 `runOnUiThread` 的 JNI 调用**。
    ⚠️ `_immersive_task()` 是类级缓存 + 启动期预热过(见 `_guard_warm`), 所以这里只是一次
       属性读 —— **绝不在工作线程上现造 PythonJavaClass**(那一步注册 Java 代理, 留在主线程)。
    """
    from jnius import autoclass
    from ..ui.app import PlinkoApp         # 调用点延迟导入(老版同模块, 新版跨层)
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    act.runOnUiThread(PlinkoApp._immersive_task())


def _guard_worker():
    """守卫工作线程。队列满就**丢这一次** —— 两条链都是幂等的, 丢一次毫无影响。"""
    while True:
        try:
            item = _GUARD_Q.get()
        except Exception:
            return
        if item is None:
            return
        _t0 = time.perf_counter()
        try:
            if item == "orient":
                _guard_orient_now()
            else:
                _guard_immersive_now()
        except Exception:
            _JNI_STAT[5] += 1                 # 失败次数要看得见(原来被 except 静默吞掉)
        _dt = time.perf_counter() - _t0
        _JNI_STAT[3] += _dt
        if _dt > _JNI_STAT[4]:
            _JNI_STAT[4] = _dt


def _guard_warm():
    """启动期焐热: 建工作线程 + 预热沉浸 Runnable。

    ⚠️ **预热 Runnable 必须在主线程做**(它要注册 Java 代理)。顺手也把
       `org.kivy.android.PythonActivity` 的 autoclass 查表热一次 —— 工作线程第一次用
       不必现付 JNI 的 `AttachCurrentThread`。
    """
    if platform != "android":
        return
    global _GUARD_Q
    if _GUARD_Q is None:
        with _GUARD_LOCK:
            if _GUARD_Q is None:
                try:
                    import queue as _queue
                    _GUARD_Q = _queue.Queue(maxsize=16)
                    threading.Thread(target=_guard_worker, daemon=True).start()
                except Exception:
                    _GUARD_Q = False
    try:
        from jnius import autoclass
        from ..ui.app import PlinkoApp         # 调用点延迟导入(见 _guard_immersive_now)
        autoclass("org.kivy.android.PythonActivity")
        PlinkoApp._immersive_task()           # 主线程上把代理建好
    except Exception:
        pass


def _guard_post(tag):
    """把一次守卫投给工作线程。返回 True = 已投递(主线程没有付 IPC 的钱)。"""
    global _GUARD_Q
    _JNI_STAT[2] += 1
    if _GUARD_Q is None:
        with _GUARD_LOCK:
            if _GUARD_Q is None:
                try:
                    import queue as _queue
                    _GUARD_Q = _queue.Queue(maxsize=16)
                    threading.Thread(target=_guard_worker, daemon=True).start()
                except Exception:
                    _GUARD_Q = False
    if _GUARD_Q is False:
        return False                          # 建不起来: 调用方退回同步
    try:
        _GUARD_Q.put_nowait(tag)
        return True
    except Exception:
        return False                          # 队列满: 丢这一次(幂等), 不回主线程补

# ---- 设定落盘: 搬出主线程 ----
# 病根: `_save_config()` 是 open + json.dump + close, **每球一次**(settle 里调), 跑在主线程。
# 安卓上这是一次**阻塞的文件写**(走 FUSE), 而它落在"落袋"那一帧 —— 同一帧还要做结算、
# 排揭晓、槽位白闪。这一类"事件路径上的阻塞调用"本工程已栽过两次。
# ⚠️ **只保留最新一份**(后写覆盖先写): 配置是"当前状态"不是流水账, 中间态没有保留价值,
#    所以不需要队列、不需要去重, 一个格子 + 一个 Event 就够。
# ⚠️ 落盘走 **临时文件 + `os.replace`** 原子替换 —— 写一半被杀不会留下半个 JSON。老写法
#    `open(path, "w")` 是先截断再写, 那种时刻被杀就是文件损坏(下次启动读不出来, 白名单一挡 =
#    进度清零)。
# ⚠️ 切后台(`on_pause`)会 flush 一次, 保证切走时一定落了盘。
_CFG_EVT = None
_CFG_PENDING = [None]        # (cfg, path) 或 None
_CFG_OK = [None]             # None=还没建, True=工作线程可用, False=建不起来
_CFG_STAT = [0.0, 0.0, 0]    # [累计秒, 单次最慢秒, 次数]


def _cfg_worker():
    while True:
        try:
            _CFG_EVT.wait()
        except Exception:
            return
        _CFG_EVT.clear()
        item = _CFG_PENDING[0]
        _CFG_PENDING[0] = None
        if item is None:
            continue
        cfg, path = item
        _t0 = time.perf_counter()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f)
            os.replace(tmp, path)
        except Exception:
            pass
        _d = time.perf_counter() - _t0
        _CFG_STAT[0] += _d
        _CFG_STAT[2] += 1
        if _d > _CFG_STAT[1]:
            _CFG_STAT[1] = _d


def _cfg_warm():
    global _CFG_EVT
    if _CFG_OK[0] is not None:
        return
    try:
        _CFG_EVT = threading.Event()
        threading.Thread(target=_cfg_worker, daemon=True).start()
        _CFG_OK[0] = True
    except Exception:
        _CFG_OK[0] = False


def _cfg_post(cfg, path):
    """把一份设定交给工作线程落盘。返回 True = 已投递(主线程不付那次文件写)。"""
    _cfg_warm()
    if _CFG_OK[0] is not True:
        return False
    _CFG_PENDING[0] = (cfg, path)
    try:
        _CFG_EVT.set()
        return True
    except Exception:
        return False


def _cfg_flush(timeout=1.0):
    """等挂着的设定落完盘(切后台/退出前)。**绝不长等** —— 超时就放弃。"""
    if _CFG_OK[0] is not True:
        return
    _t0 = time.perf_counter()
    while _CFG_PENDING[0] is not None and time.perf_counter() - _t0 < timeout:
        time.sleep(0.005)


def _vibrate_tick(gain):
    """装杯的**单次轻震**(只有 Android 有; 其它平台静默)。

    ⚠️ 这个函数**不判时间、不计数** —— 节流只能有一处真源, 就是调用点
       (`WinPileFX._bounce` 里那次 `sfx.play(...)` 的返回值)。两边各判一次必然漂移, 玩家就会
       出现"听到响但手上没感觉"(或反过来), 那正是"同步"要消灭的东西。

    时长/振幅都跟着撞击强弱走(gain 与落地音用的是同一个 0.25~0.72): 10~18ms / 振幅 80~220。
    **刻意做得很短、也不算满**: 节流后是 10 次/秒, 脉冲一长就糊成"持续嗡嗡"而不是"珠子一颗
    一颗落进杯子里"; 振幅压在 220 不满格, 因为这是"雨点"不是"大奖震一下"。手机马达的启动时间
    约 10~20ms, 所以 10ms 以下没意义 —— 下限就取 10ms。
    """
    if platform != "android":
        return
    g = max(0.0, min(1.0, (gain - 0.20) / 0.55))
    _vibrate(int(round(10 + 8 * g)), int(round(80 + 140 * g)))

# 隐藏弹窗里"制作时刻"的格式。
# ⚠️ 时间**必须是 24 小时制(%H)**, 玩家定稿: 用 %I 会变成"下午 1:49"那种 12 小时制, 与其余
#    界面的时间口径不一致(历史记录里也是 `%Y-%m-%d %H:%M`)。
# ⚠️ 年月日写成**汉字**: `2026-09-11` 这种全数字写法在中文语境下容易被读反(有人按 日/月 读)。
BUILD_TIME_FMT = '%Y年%m月%d日 %H:%M'

def _vibrate_double_now(ms=35, gap=40, amp=255):
    """短促双震的**真身**(只在工作线程上跑)。

    ⚠️ 它原来是**同步 JNI**: 在调用线程上直接 `autoclass` +
       `activity.getSystemService(VIBRATOR_SERVICE)`(**这本身就是一次 Binder 往返**) +
       `vibrate`, 而唯一的调用点是**彩蛋路径的 `settle` 那一帧**。
    ⚠️ 顺带复用 `_vib_get()` 的缓存代理 —— 原来每次都要现取一次系统服务。
    """
    t0 = time.perf_counter()
    try:
        vib = _vib_get()
        if vib is None:
            return
        try:
            from jnius import autoclass
            VibrationEffect = autoclass("android.os.VibrationEffect")
            v = VibrationEffect.createWaveform([0, ms, gap, ms], [0, amp, 0, amp], -1)
            vib.vibrate(v)
        except Exception:
            vib.vibrate(ms * 2 + gap)          # 退回单次(近似时长)
    except Exception:
        _VIB_PROXY[0] = None
    finally:
        _dt = time.perf_counter() - t0
        _VIB_STAT[0] += _dt
        if _dt > _VIB_STAT[1]:
            _VIB_STAT[1] = _dt
            _VIB_STAT[2] = "双震%dms" % ms


def _vibrate_double(ms=35, gap=40, amp=255):
    """短促双震(彩蛋用): 两下短脉冲, 手机读作"发现惊喜"; 区别于单次长震的大奖之感。
    仅 Android; **投递即返回**(见 `_vibrate_double_now` 处说明)。
    ⚠️ 队列建不起来就退回同步调用 —— 与改之前逐字相同的行为, 绝不静默失震。
    """
    if platform != "android":
        return
    # ⚠️ 老版这里有一句 `global _VIB_Q`, 但**本函数从不给它赋值**(建队列在
    #    `_vib_warm` 里) —— 空操作, 已删。留着会让 `tests/lint_gate.py` 常红,
    #    而常红的门禁等于没有。读模块全局本来就不需要 `global`。
    if _VIB_Q is None:
        _vib_warm()
    if _VIB_Q is False:
        _vibrate_double_now(ms, gap, amp)
        return
    try:
        _VIB_Q.put_nowait(("d", ms, gap, amp))
    except Exception:
        pass

# =============================================================================
# 屏幕比例分流: 仅 16:9 及更宽的屏(平板)才四方向旋转; 更瘦长的手机(18:9/20.5:9 等)锁竖屏
# (正竖+倒竖), 不进横屏 —— 瘦长机横拿时系统会多出一条横向状态栏压在旋转画面上, 显示直接
# 坏掉, 且反旋转构图在小屏上本就不适合阅读。
# =============================================================================
_DEVICE_WIDE_MIN = 9.0 / 16.0    # 短边/长边 ≥ 9:16 = 宽屏(16:9 及更宽/更方)
_device_wide_cache = None        # 开机量一次物理屏比例, 之后不再变

def _device_is_wide():
    """本机物理屏是否 16:9 及更宽(平板类, 允许横屏旋转)。
    用 Display.getRealSize() 的物理分辨率(含系统栏, 不随旋转变), 不受当前
    窗口尺寸/状态栏影响。读不到(桌面/异常)时按宽屏处理 = 保持原有行为
    (桌面 LandLayer 本就只认 --landscape 模拟, 不受影响)。"""
    global _device_wide_cache
    if _device_wide_cache is None:
        aspect = 1.0
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            disp = act.getWindowManager().getDefaultDisplay()
            pt = autoclass("android.graphics.Point")()
            disp.getRealSize(pt)
            s, l = min(pt.x, pt.y), max(pt.x, pt.y)
            aspect = s / float(l)
        except Exception:
            aspect = 1.0
        _device_wide_cache = aspect >= _DEVICE_WIDE_MIN
    return _device_wide_cache
