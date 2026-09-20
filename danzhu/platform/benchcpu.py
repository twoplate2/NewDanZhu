"""跑分与电池: CPU 提频/亲和性/线程优先级 / 波1(峰值) / 波2(高压) / 探针 / 电池温度与功率 / 热限制。

⚠️ 所有 pyjnius / android 的 import 一律在函数体内或 try 里 —— **桌面 import 期必须成功**;
   非安卓一律安全降级并**如实记因**: 拿不到就留空 / 返回 None / 0.0, **绝不印假数**。
⚠️ `_FREQ_GATE` / `_PHYS_*` / `_BENCH_*` / `_SUST_SPEED` 是**跨模块共享状态**: 写的一侧是跑分
   线程、读的一侧是主线程 ⇒ 只能走模块级。调用方一律 import 同一份, 另建一份必然静默漂移。
   `_FRAME_CALLS` 的家在 `danzhu/ui/text.py`(见 `benchmark_trajectories` 里的调用点导入)。
⚠️ 老版把这些常量叫 `SOC_*`(**历史名**: 屏幕上早已全叫 CPU): **标识符一行没改**, 别照这个前缀起新名字。
"""

import os
import random
import threading
import time

from ..config import COL_BALL, MISFIRE_POWER
from ..geo import build_geo
from ..physics import advance_flight, launch_ball
from .device import _cpufreq_cores, _cpufreq_mhz, platform

# ⚠️ `SOC_` 是**历史名**(见模块头): 这些标识符一行没改, 看到就当「CPU 高压」读。
SOC_WARMUP_CPU_SEC = 1.5

# ⚠️ **样本时长 / 轮数 / 间隔**:
#    · `SOC_SAMPLE_GAP_SEC = 5.0` **就是 Geekbench 6.1 的口径**(6.0 是 2 秒, 官方为了"降低逐次
#      波动"加长的)—— 间隔够长, SoC 才凉得下来, 测到的才是**峰值**而不是"半烤机"。
#      ⚠️ **别把它调小**(中间试过 3 秒, 那是往反方向走)。⚠️ 它是**墙钟 sleep**, 要的就是真实散热时间。
#    · ⚠️ **轮数取奇数 5**: `_bench_done` 取中位数用的是 `sorted(v)[len(v)//2]` —— n=8 时那是
#      `sorted[4]`, 下面 4 个上面 3 个, 是**上中位数**(报出来偏悲观); 奇数才是真正居中的那个。
#      ⚠️ 顺带: **5 轮 = 4 个间隔**(不是 5 个) —— 第一轮前不休息, 因为预热刚做完。
#    · ⚠️ 轮数改了, 状态栏 `物理跑分 d/N` 的分母会**自动跟着变**(它读的就是本常量)。
#    · ⚠️ **样本时长 4.0**(3.0 → 4.0): 桌面公平赛马显示**掐头能降平均差 6~19%, 而得分 μ 几乎
#      不动(±0.23%)** ⇒ 这一项买到的是**稳定性**, 不是更高的分。
SOC_SAMPLE_CPU_SEC = 4.0
SOC_SAMPLE_RUNS = 5
SOC_SAMPLE_GAP_SEC = 5.0

# ⚠️ **高压测试(独立的「高压测试」按钮, 从主测试里拆出来)**: 与波 1 相反, **窗口之间一秒都不停**,
#    专门看衰减。⚠️ 主测试不再跑高压段 —— 它的时长/参数一个字不动。
#
# ⚠️⚠️ **2026-09-15 改口径: 从"CPU 秒"改成"真实时间(墙钟)秒"**。病根: 面板报的是工作线程跑掉的
#    **CPU 秒**, 而主线程每帧要抢 GIL 渲染 ⇒ 旧口径"压 300 秒"实际要玩家等约 393 秒, 面板却在
#    300/300 就顶格 = **面板在骗人**。而"猜一个比值去凑"只在测过的机器上成立(换台机器同一个 bug
#    原样回来)。解法: **让循环条件本身就是墙钟**, 数学上必然在到点那一帧结束, 换任何设备都对得齐。
#    ⚠️ 代价(已告知玩家): 工作量随设备浮动(步/秒是速率, 仍可比); 旧记录 `sec`=300 是旧口径,
#       新老**不直接可比**。
#    ⚠️ 两个名字必须分得清:
#      `..._WALL_SEC` = 真实时间, 决定**跑多久**; `..._WINDOW_CPU_SEC` = 采样窗口, 仍是 **CPU 秒**,
#      决定**每份样本多长**("步/秒"的分母)。
SOC_SUSTAIN_WALL_SEC = 360.0
SOC_SUSTAIN_WINDOW_CPU_SEC = 1.0

# 面板上"连续采样成绩"那一行的**抽点间隔(秒)**。⚠️ 文案里的数字与抽点步长共用这一个常量
#    (两边各写一个 10 迟早脱钩)。360 秒的局 ⇒ 36 个采样点。
HP_SAMPLE_SEC = 10.0

# 功率曲线**前 5 秒不要**(玩家 2026-09-17): 那一段是 CPU 从 idle 冲到满载的**过渡期**(实测从
#   11.5W 一路掉到 7.5W), 算进平均/最高会把整场读数带偏 —— 尤其"最高"那个 11.5 不是稳态。
# ⚠️ **只跳功率**, 不跳温度/频率(玩家点名的是功率; 而且温度那条是**变长**序列, 按下标切会切错位)。
# ⚠️ 切片只做**一处**(`_run_hp_test` 里存 `_hp_power_*` 时)—— 统计 / 曲线 / 每瓦跑分 / 导出全都吃那一条。
HP_PWR_SKIP_SEC = 5.0

# ⚠️ **高压测试的 CPU 频率/电池温度采样窗口**: 360 秒的局只统计 **[1, 359] 秒**(玩家 2026-09-16
#    要的"掐头去尾")。两端确实不该算: **头**那一秒 CPU 才刚被拉起来(还在爬频), **尾**那一秒测试
#    已经收尾、下一轮排队的调度动作也在动。
#    ⚠️ 窗口用**绝对时刻**表达(见 `_freq_sampler_start` 的 `win`), 与 `_hp_wall0` 同源;
#       不要写成"线程跑起来之后 N 秒", 那会差出锁核/探测拓扑那几十毫秒。
HP_FREQ_TRIM_SEC = 1.0
# 渲染采样窗口自动发几颗球(原来是 `self._target_launches = 5` 写死在跑分函数里, 提成常量)。
BENCH_TARGET_LAUNCHES = 5

# 「频率采样门」: **唯一的写者是 `benchmark_trajectories`**(样本之间的 sleep 期间关门),
# 唯一的读者是 `_freq_sampler_start` 起的那个采样线程。默认 True = 一直采 —— 所以波 2(没有间隙)不受影响。
# ⚠️ 为什么必须是**进程级**而不是参数: 采样线程与 benchmark 是两个线程、两个调用栈,
#    它们之间只有模块级状态这一条路。
_FREQ_GATE = [True]

# ---- 波 1 跑分的**按秒进度** ----
# ⚠️ 走**模块级**: 发布者是 `benchmark_trajectories`(工作线程里的纯函数), 它够不着 RootWidget。
# ⚠️ 分子/分母的尺子必须是**同一根**: 波 1 的循环条件是 **CPU 秒** ⇒ 进度也用 CPU 秒
#    (高压那边用墙钟是因为它的循环条件就是墙钟)。拿墙钟当分子会重演"面板顶到顶了活儿还没干完"。
# ⚠️ 样本之间有 `gap_sec`(5 秒 × 4 次)不烧 CPU ⇒ 那几秒数字**不动**, 那是事实不是卡住。
_PHYS_PROG = [0.0]        # 已跑掉的 CPU 秒(工作线程写, 主线程读)
_PHYS_TOTAL_SEC = [0.0]   # 这次一共要跑多少 CPU 秒(每次开跑时按实参算好)

# ⚠️ 模拟盖子里还有"等待"(样本之间的 `gap_sec` 睡眠): 那几秒**不烧 CPU** ⇒ 光看 CPU 秒数字会
#    **冻住**(玩家 2026-09-16: 「经常出现每秒不更新, 空闲的时候也要更新」)。
#    ⇒ 分母加上等待总量, 分子加上**实际睡掉的秒数**。两边都是真实时间, 分子不再冻住也不会提前顶头。
_PHYS_SLEPT = [0.0]       # 已结束的那几段等待的总秒数

# ⚠️ **正在等待的起点**(0 = 没在等)。它必须单独发: 只把睡眠时长在**睡完之后**累加是不够的 ——
#    睡的那 5 秒里 `_PHYS_SLEPT` 一动不动 ⇒ 数字照样冻着(探针实测冻 2.80 秒)。
#    ⇒ 把起点也发出去, 由**读的一侧**按墙钟实时累加; 结束时归零并并入 `_PHYS_SLEPT`,
#      不会重复计一段。
_PHYS_WAIT_T0 = [0.0]

# ⚠️ `thread_time` 当分母已经排除了 GIL 等待, 但**排不掉"OS 把这条线程调度到哪几个核"** ——
#    真机实测(同机同设置, 只改帧率设定)物理跑分 12617/19316/21340(**差 69%**)而最大频率只差 9%。
#    真正的原因**待测**: 逐核频率 + 亲和性就是为此加的。
_BENCH_AFF = []          # 波 1 每一轮采一次 `os.sched_getaffinity(0)`; 拿不到填 None

# 跑分线程的锁核结果。只用于日志: `sched_getaffinity` 只能说明"允许跑在哪些核", 不能证明实际
# 没有在快/慢簇之间迁移; 这里把可控的迁移变量去掉, 并把成败如实记下。
_BENCH_CPU_PIN = {}

# ⚠️ 只影响跑分/高压/渲染测量那**三条工作线程**; 正常游戏线程不改亲和性。
#    (旧注释写的是"高压测试也不改" —— 那是**错的**: `_run_hp_test` / 渲染采样 / `_run_benchmark`
#     三处都调 `_bench_pin_fast_cpus()`。别照旧注释做判断。)
_BENCH_PIN_FAST_CORES = True

# 波 1 每轮的**窗口三段结构**: 起跑段(不计成绩) → 计分段(`SOC_SAMPLE_CPU_SEC` 秒) → 收尾段(不计成绩)。
# ⚠️⚠️ **两头不对称**, 是桌面 `temp/_window_fair.py` **实测**出来的(一次采集、离线切不同起点, 样本数
#    完全相同, 消掉系统漂移), 不是拍的 —— 改之前先跑它:
#   · **掐头 0.5 秒**: 轮间有 5 秒间隔, 大核在那几秒里已落到低频 OPP, 窗口开头在 DVFS 爬坡
#     (真机 7 份日志里 **5 份的最低轮都是第 2 轮**, 锁核后 3/3)⇒ 掐掉它实测**平均差降 6~19%**。
#   · **不掐尾(0.0)**: 越靠后取越稳(取 0~4s 平均差 1058 → 取 3~7s 降到 793), 掐尾是**净损失**。
#   · 得分 μ 不受影响(所有档位 μ 变化都在 ±0.23% 内, 噪声级)⇒ 这一整套买到的是**同设备更稳**,
#     不是"跑得更高"。⚠️ 只影响"统计哪一段", 不碰任何物理/弹珠逻辑。
_BENCH_HEAD_SEC = 0.5
_BENCH_TAIL_SEC = 0.0
# 波 1 每一轮的**纯算术探针**吞吐(见 `_speed_probe`)。⚠️ 它在 `_run_once` **之外**跑,
# 所以不占"步/秒"的分母; 它自己那份 CPU 时间由 `_speed_probe` 内部计量。
_BENCH_SPEED = []
# 波 1 每一轮的**对象分配探针**吞吐(见 `_alloc_probe`)。与 `_BENCH_SPEED` 配成一对:
# 两个一起掉 = 核心慢; 只有这个掉 = 内存/分配器那一侧被抢。
_BENCH_ALLOC = []
# 波 2(高压)每一窗的**纯算术探针**吞吐(见 `_speed_probe`)。⚠️ 和波 1 分开存 ——
# 两波是**不同的两段时间**(波 1 有间隔、波 2 一秒不停), 混在一起就分不出是哪一段的。
_SUST_SPEED = []
# 波 1 每一轮**实际渲染出来**多少帧/秒(用 `_FRAME_CALLS` 的增量 ÷ 墙钟)。
# ⚠️ **必须有这个数**: "请求 60" 不等于 "拿到 60" —— 真机实测过请求 60 而拿到 80.4fps。
_BENCH_RENDER_FPS = []


def _bench_cpu_max_khz(cpu):
    """读取一个 CPU 的静态最高频率(kHz)，读不到返回 0。

    `cpuinfo_max_freq` 是硬件/策略给出的能力上限，不能拿来推断某一刻的实际频率；
    这里只用它识别大小核簇，避免用会随负载抖动的 `scaling_cur_freq` 选核。
    """
    _base = "/sys/devices/system/cpu/cpu%d/cpufreq/" % int(cpu)
    for _name in ("cpuinfo_max_freq", "scaling_max_freq"):
        try:
            with open(_base + _name) as _fh:
                _v = int(_fh.read().strip() or 0)
            if _v > 0:
                return _v
        except Exception:
            pass
    return 0


def _bench_pin_fast_cpus():
    """将**当前跑分线程**限制在可用的最高性能 CPU 簇，返回供恢复的原集合。

    锁核不是给游戏提速，而是消除 benchmark 在性能核/中核之间迁移这一测量变量。
    Android/设备不允许、拓扑读不出或本来就是同构核时一律不锁，并在日志中留下原因。
    """
    _BENCH_CPU_PIN.clear()
    _status = _BENCH_CPU_PIN
    _status["enabled"] = bool(_BENCH_PIN_FAST_CORES)
    if not _BENCH_PIN_FAST_CORES:
        _status["reason"] = "已关闭"
        return None
    if platform != "android":
        _status["reason"] = "非安卓"
        return None
    try:
        _allowed = set(os.sched_getaffinity(0))
    except Exception:
        _status["reason"] = "sched_getaffinity 不可用"
        return None
    if not _allowed:
        _status["reason"] = "当前允许 CPU 集为空"
        return None
    _status["before"] = sorted(_allowed)
    _caps = {int(_cpu): _bench_cpu_max_khz(_cpu) for _cpu in _allowed}
    _caps = {int(_cpu): int(_khz) for _cpu, _khz in _caps.items() if _khz > 0}
    if len(_caps) < 2:
        _status["reason"] = "读不到足够的 CPU 最高频率"
        return None
    _top = max(_caps.values())
    _bottom = min(_caps.values())
    # 不把极小的固件/读数差误判成大小核；K90 的 2746/2880MHz 簇差约 4.9%，会被识别。
    if _top <= _bottom * 1.02:
        _status["reason"] = "可用 CPU 为同一性能核组"
        return None
    # 同一最高簇的核心通常共享最高频率；留 2% 容差兼容厂商公布频率的微小差异。
    _target = {int(_cpu) for _cpu, _khz in _caps.items() if _khz >= _top * 0.98}
    if not _target or _target == _allowed:
        _status["reason"] = "没有可收窄的性能核组"
        return None
    _status["caps_khz"] = dict(sorted(_caps.items()))
    _status["target"] = sorted(_target)
    try:
        os.sched_setaffinity(0, _target)
        _actual = set(os.sched_getaffinity(0))
        _status["actual"] = sorted(_actual)
        if not _actual.issubset(_target) or not _actual:
            _status["reason"] = "系统未接受核组绑定"
            try:
                os.sched_setaffinity(0, _allowed)
            except Exception:
                pass
            return None
        _status["pinned"] = True
        return _allowed
    except Exception as _exc:
        _status["reason"] = "sched_setaffinity 失败: %s" % type(_exc).__name__
        return None


def _bench_restore_cpu_affinity(previous):
    """恢复 `_bench_pin_fast_cpus` 改过的当前线程亲和性。"""
    if not previous:
        return
    try:
        os.sched_setaffinity(0, previous)
        _BENCH_CPU_PIN["restored"] = sorted(os.sched_getaffinity(0))
    except Exception as _exc:
        _BENCH_CPU_PIN["restore_error"] = type(_exc).__name__


# 跑分/高压**工作线程**的调度优先级状态。与 `_BENCH_CPU_PIN` 正交:
#   亲和性管「允许跑在哪些核」, 优先级管「抢不抢得到 CPU」。
# ⚠️ 只作用于跑分那颗线程, **不碰主线程、不碰物理/弹珠逻辑** —— 见 `_bench_raise_thread_priority`。
_BENCH_TID_PRIO = {}
_BENCH_RAISE_PRIO = True


def _bench_raise_thread_priority():
    """把**当前(跑分)线程**提到高调度优先级; 结果写进 `_BENCH_TID_PRIO` 供日志如实记录。

    为什么需要它: 玩家要的是「同一台设备重复跑要稳, 更好的 CPU 要跑得高」。跑分线程和 Kivy 主线程
    在**同一个进程**里抢 CPU, 而进程内的调度权重是自己能改的(改本进程线程的优先级不需要 root)。
    `_bench_pin_fast_cpus` 只说明"允许跑在 cpu6/7" —— **不保证抢得到**; 这两件事合起来才把
    "跑分线程被渲染挤走"这条变量按住。

    ⚠️ 它**不改变任何被测结果**, 只改变这颗线程多久拿到一次 CPU。弹珠的运行逻辑一行不碰。
    ⚠️ 非安卓 / 系统拒绝一律**安全降级并如实记因**, 不印假成功:
       `getThreadPriority(0)` 读回实际值, 没变成负数就说明系统没接受这次请求。
    返回供恢复的原优先级(None = 没改过)。
    """
    _BENCH_TID_PRIO.clear()
    if not _BENCH_RAISE_PRIO:
        _BENCH_TID_PRIO["reason"] = "已关闭"
        return None
    if platform != "android":
        _BENCH_TID_PRIO["reason"] = "非安卓"
        return None
    try:
        from jnius import autoclass
        _P = autoclass("android.os.Process")
        _before = int(_P.getThreadPriority(0))
        _P.setThreadPriority(_P.THREAD_PRIORITY_URGENT_DISPLAY)
        _after = int(_P.getThreadPriority(0))
        _BENCH_TID_PRIO["before"] = _before
        _BENCH_TID_PRIO["after"] = _after
        if _after < 0:
            _BENCH_TID_PRIO["raised"] = True
            return _before
        _BENCH_TID_PRIO["reason"] = "系统未接受(读回 %d)" % _after
        return None
    except Exception as _exc:
        _BENCH_TID_PRIO["reason"] = "调用失败: %s" % type(_exc).__name__
        return None


def _bench_restore_thread_priority(previous):
    """恢复跑分线程测试前的调度优先级(`None` = 没改过, 直接返回)。"""
    if previous is None:
        return
    try:
        from jnius import autoclass
        _P = autoclass("android.os.Process")
        _P.setThreadPriority(int(previous))
        _BENCH_TID_PRIO["restored"] = int(_P.getThreadPriority(0))
    except Exception as _exc:
        _BENCH_TID_PRIO["restore_error"] = type(_exc).__name__


def _mad_coef(vals):
    """**平均差系数** = 平均差 ÷ 均值(取代原来的「波动」)。

    ⚠️ 为什么换掉: 旧定义的 `(max-min)/中位` **只看两个极端点**, 一个离群值就能把它整个带飞。
    平均差用上**每一个样本**。⚠️ 与变异系数(CV = σ/μ)的区别: 平均差**不平方**, 对离群值的敏感度
    比 σ 还低一档。

    返回**百分数**(如 2.5 表示 2.5%); 样本少于 2 个或均值为 0 时返回 `None` —— **不编数**。
    """
    _v = []
    for _x in (vals or []):
        try:
            _v.append(float(_x))
        except Exception:
            pass
    if len(_v) < 2:
        return None
    _m = sum(_v) / len(_v)
    if _m == 0:
        return None
    return 100.0 * sum(abs(_x - _m) for _x in _v) / len(_v) / _m


def benchmark_trajectories(warmup_cpu_sec=SOC_WARMUP_CPU_SEC,
                           sample_cpu_sec=SOC_SAMPLE_CPU_SEC,
                           runs=SOC_SAMPLE_RUNS,
                           gap_sec=SOC_SAMPLE_GAP_SEC,
                           on_sample=None):
    """**波 1 —— 测性能(峰值)**: `warmup_cpu_sec` 秒 CPU 预热 + `runs` 轮 x `sample_cpu_sec` 秒样本, 取中位数。

    用工作线程自身的 ``thread_time`` 作时间基准, 排除等待 GIL、渲染和系统调度的墙钟空档;
    每轮用实际消耗的 CPU 时间作分母(避免旧版「实际跑过 0.7 秒但固定除以 0.7」的偏差)。
    返回 ``(总发数, 总步数, 各轮步/秒, 各轮实际CPU秒)``。

    ⚠️ 样本之间留 `gap_sec` 秒(**5 秒就是 Geekbench 6.1 的口径**): 没有间隔就是"背靠背烤机",
       后几轮被自己烤热 —— 那测到的**不是峰值**。要测持续性能走 `benchmark_sustained`(波 2)。
    ⚠️ **顺序必须是先本函数、再 `benchmark_sustained`** —— 反过来波 2 先把机器烤热, 这里就不是峰值了。
    ⚠️ 间隔期间**主线程照常渲染**, 采样线程也照常采频率 —— 那正是观察降频的窗口。
    """
    # 渲染帧率计数器(`_FRAME_CALLS`)的家在 `danzhu/ui/text.py`(写入口全在那边, 见 device.py
    # 顶部那段说明); 这里是**调用点导入** —— 平台层在 import 期不许拖 kivy, 也不许反向依赖 ui。
    from ..ui.text import _FRAME_CALLS
    geo = build_geo()
    cpu_clock = getattr(time, "thread_time", None) or time.process_time

    def _run_once(cpu_seconds, seed):
        # 每个样本从同一条确定性输入序列起跑，波动反映 SoC 状态，而不是抽到不同球路。
        rng = random.Random(seed)
        # ⚠️⚠️ **窗口三段结构: 起跑段(不计) → 计分段 → 收尾段(不计)**(玩家 2026-09-15 建议)。
        #    头: 轮间有 `gap_sec` 秒间隔, 大核在那几秒里已落到低频 OPP, 窗口开头那几百毫秒在
        #        DVFS 爬坡 —— 真机 7 份日志里 **5 份的最低轮都是第 2 轮**(锁核后 3/3), 正是这个形状。
        #    尾: 窗口末尾会被"最后一颗球跑不完"截断, 而且紧挨着下一轮的调度动作。
        #    `flights` / `frames` / 分母 `used` **同源**, 都只覆盖中间那一段。
        # ⚠️ 不计分段用**独立的随机流** —— 否则会消耗主序列的随机数、让"跑哪些球"变掉, 历史就不可比了。
        # ⚠️ 这里只移动"统计哪一段", 跑的仍是原样的 `launch_ball` + `advance_flight`;
        #    **弹珠逻辑一行不碰**。
        _warm_rng = random.Random(seed ^ 0x5A5A5A)

        def _burst(deadline, _rng, _count):
            """跑到本线程 CPU 时间到 `deadline` 为止; `_count` 为假时只跑、不计数。

            ⚠️ 顺手把**按秒进度**发出去(`_PHYS_PROG`)。发布点选在这里是**刻意的** —— 这个 `while`
               是**每颗球**转一圈(内层那个 `range(4000)` 才是每步), 而一颗球要飞几百步 ⇒ 每次多
               "取一次时间 + 存一次列表"相对一颗球的成本可以忽略。
               ⚠️ **绝不能挪进内层 `for`**: v0.7.74 的教训就是"热循环里每步多加一点"直接把跑分拖下去。
            ⚠️ 循环形状是 `while True` + 一次取值(**不是** `while cpu_clock() < deadline`):
               **调用次数与判据完全相同** ⇒ 发球的序列一个数都不变(物理逐位不变)。
            """
            _fl = _fr = 0
            while True:
                _now = cpu_clock()
                if _now >= deadline:
                    break
                _PHYS_PROG[0] = _now - _t0[0]
                _b = launch_ball(_rng.uniform(MISFIRE_POWER, 1.0), rng=_rng)
                for _ in range(4000):
                    _landed = advance_flight(_b, geo)
                    if _count:
                        _fr += 1
                    if _landed is not None:
                        if _count:
                            _fl += 1
                        break
            return _fl, _fr

        _burst(cpu_clock() + _BENCH_HEAD_SEC, _warm_rng, False)   # 起跑段: 只顶频率
        _m0 = cpu_clock()
        flights, frames = _burst(_m0 + cpu_seconds, rng, True)     # 计分段
        used = max(0.000001, cpu_clock() - _m0)
        _burst(cpu_clock() + _BENCH_TAIL_SEC, _warm_rng, False)   # 收尾段: 只顶频率
        return flights, frames, used

    # ⚠️⚠️ **门: 采样间隙里频率不算数**(玩家 2026-09-15: 「必须用跑分时的平均频率(空闲的时候可以
    #    不计)」)。采样线程是每 0.5 秒无脑采一次的, 而样本之间有 `gap_sec` 秒的 sleep(默认 5 秒 ×
    #    4 次 = **20 秒**), 那几段应用基本闲着、频率是低频 ⇒ 不关门的话"平均频率"报出来的是
    #    "跑分 + 休息"的平均, 不是"跑分时"的。
    # ⚠️ 进门先开门: 预热那一段也算"在跑"(它同样满载)。波 2 **没有间隙**, 门对它恒真 ——
    #    默认就是 True, 只有本函数会去翻, 互不干扰。
    _FREQ_GATE[0] = True
    # ---- 按秒进度的基准 ----
    # `_t0` 是"整场跑分开始时的线程 CPU 时间"; 分子 = 当前 CPU 时间 - `_t0`。分母 = **一共要烧多少
    # CPU 秒** —— 每一段 `_burst` 都是 `_BENCH_HEAD_SEC` 秒的起跑段 + 计分段, 而 `_run_once` 被
    # 调用 `1(预热) + runs` 次。
    # ⚠️ 起跑段(每轮 0.5 秒 × (runs+1))**必须算进分母**: 它同样在烧 CPU、同样是玩家在等。
    # ⚠️ 探针会把参数改小(`temp/_bench_*`), 所以按**实参**现算, 不写死常量。
    _t0 = [cpu_clock()]
    _PHYS_PROG[0] = 0.0
    _PHYS_SLEPT[0] = 0.0
    _PHYS_WAIT_T0[0] = 0.0
    # ⚠️ 分母 = CPU 秒总量 + **等待总量**(`runs-1` 次样本间睡眠)。
    #    两者都是玩家真在等的时间 ⇒ 不会冻住也不会提前顶头。
    _PHYS_TOTAL_SEC[0] = ((_BENCH_HEAD_SEC + warmup_cpu_sec)
                          + runs * (_BENCH_HEAD_SEC + sample_cpu_sec)
                          + max(0, runs - 1) * float(gap_sec or 0.0))
    _run_once(warmup_cpu_sec, 98765)   # 升频/Python 热身，不计成绩
    # ⚠️⚠️ **把这条线程的 CPU 亲和性记下来**。真机实测(红米 K90 Pro Max): 同一台机器、同样开"均衡",
    #    只改"手机帧率上限 / 游戏帧率上限"这两个设定, 物理跑分是 **12617 / 19316 / 21340**(差 69%),
    #    而"最大 CPU 频率"只差 9%。按构造这条线程与刷新率无关(独立线程 + `thread_time` 当分母),
    #    所以差异只能来自 **OS 把它调度到哪几个核 / 那个核跑多少频率** —— 而那是我们看不见的。
    #    `sched_getaffinity` 是**唯一**能从应用侧读到"它被限在哪几个核"的口子。
    #    ⚠️ 非安卓/不支持时拿不到, **如实留空**, 调用方印"没采到", 不印假数。
    #    ⚠️ **每轮都采**: 亲和性是可能中途变的(系统按负载收窄 cpuset), 只看开头会漏掉。
    _BENCH_AFF.clear()
    _BENCH_SPEED.clear()
    _BENCH_ALLOC.clear()
    _BENCH_RENDER_FPS.clear()
    fps_list = []
    cpu_seconds_list = []
    total_flights = 0
    total_frames = 0
    for _i in range(runs):
        try:
            _BENCH_AFF.append(sorted(os.sched_getaffinity(0)))
        except Exception:
            _BENCH_AFF.append(None)
        if _i == 0:
            _prev_fc, _prev_wt = _FRAME_CALLS[0], time.time()
        if _i > 0 and gap_sec > 0:
            _FREQ_GATE[0] = False      # 关门 → 这几秒的频率不进平均
            _g0 = time.time()
            _PHYS_WAIT_T0[0] = _g0     # 告诉读的一侧"从这一刻开始在等"
            try:
                time.sleep(gap_sec)    # ⚠️ 只在样本之间停, 第一轮前不停(预热刚做完)
            finally:
                _FREQ_GATE[0] = True   # ⚠️ finally: 中断了也必须把门开回来
                _PHYS_WAIT_T0[0] = 0.0     # ⚠️ **先清起点再累加**, 否则这一段被算两次
                # ⚠️ 用**实测**睡眠时长(不是 `gap_sec` 常量): 系统调度
                #    可能让 `sleep` 多睡一点, 而进度里那一秒就该是真实的。
                _PHYS_SLEPT[0] += max(0.0, time.time() - _g0)
        flights, frames, used = _run_once(sample_cpu_sec, 12345)
        fps_list.append(frames / used)
        cpu_seconds_list.append(used)
        # ⚠️ **这一轮实际渲染了多少帧/秒**(见 `_BENCH_RENDER_FPS`): `_FRAME_CALLS` 是
        #    `_frame` 的调用计数, 增量 ÷ 墙钟就是**真实渲染帧率**。用来回答"帧率到底按住了没有"
        #    —— 真机实测过"请求 60 却拿到 80.4"。
        try:
            _BENCH_RENDER_FPS.append((_FRAME_CALLS[0] - _prev_fc) / max(1e-6, time.time() - _prev_wt))
        except Exception:
            _BENCH_RENDER_FPS.append(0.0)
        _prev_fc, _prev_wt = _FRAME_CALLS[0], time.time()
        # ⚠️ **纯算术探针紧跟在这一轮后面**(见 `_speed_probe`): 同样的热状态、同样的
        #    调度处境。它**不占**上面那个分母(自己计时), 只是给"这一轮的机器到底有多快"
        #    留一个**与内存无关**的参照。
        try:
            _BENCH_SPEED.append(_speed_probe())
        except Exception:
            _BENCH_SPEED.append(0.0)
        # ⚠️ 分配探针紧跟其后 —— 和纯算术探针**同一处境**, 两个相减才是"内存那一侧"。
        try:
            _BENCH_ALLOC.append(_alloc_probe())
        except Exception:
            _BENCH_ALLOC.append(0.0)
        total_flights += flights
        total_frames += frames
        # ⚠️ 回调跑在**工作线程**上: 只能写普通属性, **绝不许碰界面**
        #    (界面只能在主线程的 Clock 回调里动)。进度条由主线程按 0.25 秒轮询这个值。
        if on_sample is not None:
            try:
                on_sample(len(fps_list), runs)
            except Exception:
                pass
    return total_flights, total_frames, fps_list, cpu_seconds_list


def benchmark_sustained(total_wall_sec=SOC_SUSTAIN_WALL_SEC,
                        window_cpu_sec=SOC_SUSTAIN_WINDOW_CPU_SEC):
    """**波 2 —— 测高压(衰减)**: 窗口之间**一秒都不停**, 整整压 `total_wall_sec` 秒**真实时间**。

    与波 1 的区别就一个 —— **没有间隔**。问的问题也不同:
      波 1 问"这台机器**最好**能跑多快"(峰值, 跨次/跨设置可比);
      波 2 问"**一直压着跑会掉多少**"(温控衰减, 以及抖成什么样)。

    返回 ``(总发数, 总步数, 各窗口步/秒, 各窗口实际CPU秒)``。
    ⚠️ 口径与波 1 **逐字相同**(同一条确定性输入序列、`thread_time` 当分母、用实际消耗的 CPU 秒做除法)
       —— 否则两波的数没法放在一起比。
    ⚠️⚠️ **外循环按 `time.time()`(真实时间)退出, 不是 CPU 时间**。**窗口本身仍是 1 CPU 秒** ——
       它决定"步/秒"的分母, 不许跟着改。
       副作用: 外循环在**窗口开始前**判条件, 所以最后一个窗口会**多做 ≤ 一个窗口**的活
       ⇒ 总墙钟落在 `[total_wall_sec, total_wall_sec + 约1.4秒]`。
       ⚠️ **别为了"精确到点"去截断最后一个窗口** —— 那样它的分母会变得很小、样本变噪,
          而**末窗口正是衰减曲线的关键读数**(`_hp_stats` 的"末")。
    """
    geo = build_geo()
    cpu_clock = getattr(time, "thread_time", None) or time.process_time

    def _run_once(cpu_seconds, seed):
        rng = random.Random(seed)
        flights = 0
        frames = 0
        t0 = cpu_clock()
        while cpu_clock() - t0 < cpu_seconds:
            power = rng.uniform(MISFIRE_POWER, 1.0)
            b = launch_ball(power, rng=rng)
            for _ in range(4000):
                landed = advance_flight(b, geo)
                frames += 1
                if landed is not None:
                    flights += 1
                    break
        used = max(0.000001, cpu_clock() - t0)
        return flights, frames, used

    fps_list = []
    cpu_seconds_list = []
    total_flights = 0
    total_frames = 0
    # ⚠️ **波 2 也要纯算术探针**(玩家 2026-09-15: 「高压测试的跑分也应该同步修订」)。
    #    理由和波 1 完全一样: "步/秒"会被**渲染抢内存**污染(波 1 实测 120fps 与 80fps 差 13.9%,
    #    而探针只差 1.1%)。高压跑 6 分钟, **衰减曲线的绝对值**同样被污染。
    #    ⚠️ 只在**每个窗口后**跑一次(1 CPU 秒的窗口, 探针 0.05 秒) —— 6 分钟里约 360 次, 累计 18 秒
    #       的额外 CPU, 占比 5%。**这是有意的**: 探针必须**全程陪着**, 才追得上温控造成的漂移。
    _SUST_SPEED.clear()
    # ⚠️ **这里是墙钟**(2026-09-15 改): 与外循环判据同一根尺子。
    #    旧版用 `cpu_clock()` —— 那是"工作线程自己跑了多久", 与玩家等的真实时间差 1.31 倍。
    t_all = time.time()
    while time.time() - t_all < total_wall_sec:
        flights, frames, used = _run_once(window_cpu_sec, 12345)
        fps_list.append(frames / used)
        cpu_seconds_list.append(used)
        total_flights += flights
        total_frames += frames
        try:
            _SUST_SPEED.append(_speed_probe())
        except Exception:
            _SUST_SPEED.append(0.0)
    return total_flights, total_frames, fps_list, cpu_seconds_list


# 逐核频率的累加器(只在采样窗口里填): {核号: [和, 次数, 最低, 最高]}。
# ⚠️ 由 `_freq_sampler_start` 清空、`_freq_core_stats()` 读走 —— 和 `_CPUFRQ` 同一条命。
_FREQ_CORE_STATS = {}


def _freq_core_stats():
    """折成 `{核号: {'mean','min','max','n','act'}}`(没采到返回 {})。

    `act` = `cpuinfo_cur_freq`(实际值)的均值; **一个都没读到就是 0**, 不编数。
    """
    out = {}
    for _k, _v in _FREQ_CORE_STATS.items():
        if _v[1] > 0:
            out[_k] = {"mean": _v[0] / _v[1], "min": _v[2], "max": _v[3], "n": _v[1],
                       "act": (_v[4] / _v[5]) if _v[5] > 0 else 0.0}
    return out

# 探针的**分块数**。⚠️ 取值靠**机制约束**, 不是"哪个块数最稳"的排名 —— 实测过两轮(每档 30 次),
# **两轮的排名完全相反**(第一次 6 块最好/8 块最差, 第二次 8 块最好/6 块最差), 说明桌面环境根本
# 分辨不出块数优劣 ⇒ **那条路是死路, 别再照着排名调**。
# 两次都复现的只有一件事: **中位随块数单调上升**(58.7M → 63.9M, +6~9%) —— 分块取最大确实抓到了
# 更高的峰值, **机制生效**。于是改按机制取值:
#   · **下界 2**: 一块脏了还有别的块 ⇒ 抗瞬时干扰的前提(真机 221654 那次探针崩 34% 而同期步/秒
#     只崩 2.3%, 正说明干扰没持续满整个计分段)。
#   · **上界 ~5**: 内循环一轮约 18us, 每块至少要 ~500 轮计数才稳 ⇒ 每块 >= 10ms ⇒ 50ms 最多切 5 块。
#   · **取 4**(每块 12.5ms ≈ 700 轮) —— 落在区间内、且每块样本留有余量。
_PROBE_BLOCKS = 4


def _speed_probe(cpu_seconds=0.05, blocks=_PROBE_BLOCKS):
    """**纯算术**负载的吞吐(每秒做了几轮) —— 只碰寄存器, 不碰内存。

    ⚠️ 为什么要它: 物理步/秒**不是纯核心速度** —— `advance_flight` 每一步都在碰对象头 / 属性字典 /
       小对象分配, 对**缓存与内存子系统**同样敏感。一个只碰寄存器的定工作量循环能把这两件事分开:
         · **两个数一起掉** ⇒ 核心真的慢了 —— 那 `scaling_cur_freq` 报的就不是实际值
         · **只有物理步/秒掉** ⇒ 是缓存/内存被渲染抢了, 与核心速度无关
       真机三次(同一台 K90, 只改帧率设定)跑分 18450 / 13596 / 21585(**差 59%**), 而逐核频率只差
       6%、亲和性三次相同、`thread_time` 实测**确实**排除了 GIL 等待 ⇒ 三个假设都排除了。

    ⚠️⚠️ **分块取最大**(不是"跑一整段 50ms 取一个数"): 血证 —— 某轮探针报 **-34%**, 而**同一轮**的
       实际步/秒只比中位低 **2.3%**。同样一次瞬时干扰, 摊进 **50ms** 里占 100%、摊进计分段的
       **4000ms** 里只占 1.25% ⇒ 那是**尺子自己被干扰了, 不是核心真的慢**。
       做法: 切成 `blocks` 段, 每段 `cpu_seconds/blocks`, **返回最快那一段**。
       ⚠️ **取最大不是取平均** —— "这颗 CPU 最快能到多少"本来就是个**上界量**, 而干扰**只会让测量
          变慢、不会让它变快**(单向) ⇒ 上界量该用最大值估计(Cinebench 的 best-of-N 同款道理)。
       ⚠️ 总成本不变, 只是切细了。
       ⚠️ 副作用: 报出来的值会**系统性偏高** ⇒ **归一化跑分会跟着变小**; 跨版本比归一化时要记得
          这件事(它是一次口径切换, 不是设备变快了)。
    ⚠️ 工作量固定, 用**本线程 CPU 时间**计时, 所以"每秒几轮"就是有效核心速度。
    """
    _nb = max(1, int(blocks))
    _per = max(0.005, float(cpu_seconds) / _nb)
    _best = 0.0
    for _b in range(_nb):
        _t0 = time.thread_time()
        _n = 0
        while time.thread_time() - _t0 < _per:
            _x = 0
            for _i in range(600):
                _x += _i
            _n += 600
        _u = max(1e-6, time.thread_time() - _t0)
        _v = _n / _u
        if _v > _best:
            _best = _v
    return _best


class _ProbeObj(object):
    """分配探针用的小对象 —— 刻意长得像 `Ball`(多个槽位 + 浮点属性)。"""
    __slots__ = ("a", "b", "c", "d")


def _alloc_probe(cpu_seconds=0.05, blocks=_PROBE_BLOCKS):
    """**对象分配**负载的吞吐(每秒几轮) —— 走 CPython 分配器 + 属性写读, **碰内存**。

    ⚠️ 它与 `_speed_probe`(纯寄存器)**配成一对**, 才分得开"核心慢"和"内存被抢":
         · **两个一起掉** ⇒ 核心速度整体慢了(那 `scaling_cur_freq` 就在骗人)
         · **只有这个掉** ⇒ **内存/分配器那一侧被抢了**
       真机实测(红米 K90, 只改帧率设定): 两次的**纯算术探针只差 1.1%**、大核频率也一样钉在 2880,
       而**物理步差 13.9%** ⇒ 那 14% **与核心速度无关**, 只能是内存那一侧。
    ⚠️ 和 `_speed_probe` 一样: 工作量固定、用**本线程 CPU 时间**计时, 在 `_run_once` **之外**跑
       (不占"步/秒"的分母)。
    ⚠️ 与 `_speed_probe` **同构**(分块取最大): 两个探针必须用同一套采样口径, 否则"两个一起掉 /
       只有一个掉"这个判读就不成立 ⇒ 别只改一个。
    """
    # ⚠️ 与 `_speed_probe` **同构**: 分块取最大(理由与血证见那边, 别只改一个)。
    #    两个探针必须用同一套采样口径, 否则"两个一起掉 / 只有一个掉"这个判读就不成立。
    _nb = max(1, int(blocks))
    _per = max(0.005, float(cpu_seconds) / _nb)
    _best = 0.0
    for _b in range(_nb):
        _t0 = time.thread_time()
        _n = 0
        while time.thread_time() - _t0 < _per:
            _keep = []
            for _i in range(200):
                _o = _ProbeObj()             # 分配(走分配器)
                _o.a = _i * 0.5
                _o.b = _o.a + 1.0
                _o.c = _o.b * 0.25
                _o.d = _o.c - _o.a
                _keep.append(_o.d)
            _n += 200
            del _keep                        # 再回收(和游戏里一样是"分配-丢弃"的节奏)
        _u = max(1e-6, time.thread_time() - _t0)
        _v = _n / _u
        if _v > _best:
            _best = _v
    return _best


def _freq_sampler_start(win=None):
    """起一个主频采样线程, 返回 `(频率列表, 停止标志)`。

    高压测试传入 `win` 时按绝对墙钟 1Hz 采集: 目标时刻是 `win[0] + 0, 1, 2...`, 读 sysfs 的耗时
    不累加到下一个时刻。因此 `[1,359]` 包含两端时目标为 359 个点, 第 N 个点对应测试的第 N 秒;
    某次读取失败或耗时超过 1 秒就**如实少一点**, 不复制旧值凑数。

    波 1 传 `win=None` 时保持旧行为: 每轮读完后 `sleep(0.5)`, 并由 `_FREQ_GATE` 排除样本间的等待段。

    用 `_cpufreq_mhz()`(读 sysfs), 非安卓/读不到时**采不到任何值**, 列表保持为空 —— 调用方据此印
    「没采到」, **不印假数**。⚠️ 同一个循环里顺带累加**逐核**频率(`_FREQ_CORE_STATS`)。

    ⚠️⚠️ `win`(可选)是一个**可变容器** `[起, 止]`, 装的是**绝对时刻**(`time.time()` 口径);
       只有落在 `起 <= now <= 止` 里的样本才计数(玩家 2026-09-16 要的「**掐头去尾**」)。
       `win[0] <= 0` 表示"窗口还没填" ⇒ **一个都不收**(宁可空着, 也不收没定过窗口的样本)。
    ⚠️ 为什么用"可变容器"而**不是两个秒数参数**: 高压那段的墙钟是在**锁核/提权之后**才起的,
       而采样线程必须**在那之前**就起来 —— 因为 `os.sched_setaffinity` 只改**调用线程**、
       **新线程继承创建者的亲和性**: 把采样线程挪到锁核之后起, 它就会跟着被钉到同一批性能核上,
       反过来跟被测线程抢核 ⇒ 只能"先起线程、后填窗口"。
    ⚠️ 传 `None` = 全程计数(波 1 就是这条路, 它没有"掐头去尾"这回事)。
    """
    _frq = []
    _stop = [False]
    _FREQ_CORE_STATS.clear()
    _t0 = time.time()

    def _read_one():
        """读一个时刻; 任一 sysfs 失败都不让采样线程退出。"""
        try:
            # ⚠️ **关门期间不采**(见 `_FREQ_GATE`): 样本之间那几秒 sleep 里 CPU 是闲的,
            #    采进来会把"跑分时的平均频率"拖低。
            if not _FREQ_GATE[0]:
                return
            # 一次读全, 频率曲线与逐核统计共用, 不重复读 sysfs。
            _cores = _cpufreq_cores()
            # 只统计跑分核; 锁核失败或读不到时才退回全体取最大值。
            _pin = set(_BENCH_CPU_PIN.get("actual") or ())
            _sel = [_cm for _ci, _cm, _ca in _cores if (not _pin) or (int(_ci) in _pin)]
            if not _sel:
                _sel = [_cm for _ci, _cm, _ca in _cores]
            _v = max(_sel) if _sel else _cpufreq_mhz()
            if _v > 0:
                _frq.append(_v)
            for _ci, _cm, _ca in _cores:
                _e = _FREQ_CORE_STATS.get(_ci)
                if _e is None:
                    _e = [_cm, 1, _cm, _cm, 0.0, 0]
                    _FREQ_CORE_STATS[_ci] = _e
                else:
                    _e[0] += _cm
                    _e[1] += 1
                    if _cm < _e[2]:
                        _e[2] = _cm
                    if _cm > _e[3]:
                        _e[3] = _cm
                if _ca > 0:
                    _e[4] += _ca
                    _e[5] += 1
        except Exception:
            pass

    def _samp():
        if win is None:
            while not _stop[0]:
                _read_one()
                time.sleep(0.5)
            return

        _next = None
        while not _stop[0]:
            try:
                _start, _end = float(win[0]), float(win[1])
            except Exception:
                _start = _end = 0.0
            if _start <= 0.0 or _end < _start:
                time.sleep(0.05)
                continue
            if _next is None:
                _next = _start
            if _next > _end:
                return
            _now = time.time()
            if _now < _next:
                time.sleep(min(0.10, max(0.01, _next - _now)))
                continue
            _read_one()
            _next += 1.0
            _after = time.time()
            if _next <= _after:
                _next = _start + (int((_after - _start) // 1.0) + 1) * 1.0

    try:
        threading.Thread(target=_samp, daemon=True).start()
    except Exception:
        pass
    return _frq, _stop


def _battery_temp_c():
    """读当前电池温度(摄氏度), 读不到返回 `None`。

    ⚠️ 2026-09-17 起这里只是**委托** `_battery_snapshot()`(温度与电压共用同一次
       sticky 广播读取)。**对外的签名与语义一个字没变** —— 波 1 在
       `_start_bench_test` / 结果计算两处直接调它。

    Android `ACTION_BATTERY_CHANGED` 里的 `EXTRA_TEMPERATURE` 单位是 0.1℃。
    这是**电池**传感器, 不是 CPU/SoC 核心温度; 该 sticky broadcast 不需要
    `BATTERY_STATS` 或其它额外权限。`receiver=None` 只取当前快照,
    不会注册一个需要解绑的 Receiver。
    """
    _s = _battery_snapshot()
    return None if _s is None else _s.get("temp")


# ---- 电池功率采集 ----
# 高压测试原来只有电池温度, 看不到**功耗**。功率 = |电流| × 电压, 而这两个数来自**两条完全不同的路**:
#   · 电压 —— 就在 `ACTION_BATTERY_CHANGED` 里(`EXTRA_VOLTAGE`, mV), 和温度同一次读;
#   · 电流 —— 广播里**没有**, 只能走 `BatteryManager.getIntProperty(CURRENT_NOW)`(µA)。
#   ⚠️ AOSP 里**不存在** `BATTERY_PROPERTY_VOLTAGE` —— 别去 getIntProperty 找电压。
#
# ⚠️⚠️ `CURRENT_NOW` 的**底层更新周期由 fuel gauge 硬件决定, Android 不保证**: 官方记录过
#   Nexus 6/9 约 175.8ms, 而 Nexus 10 约 **3.5 秒**。⇒ 「轮询频率」≠「真实刷新频率」, 采得再密也
#   可能拿到重复值。本项目按 5Hz 采, 并把"实际变了多少次"记进 `power_stats`。
#   **5Hz 只能分辨到 200ms** —— gauge 更快时只能说「≤0.2 秒, 测不到更细」, 报成具体毫秒数就是编数。
#
# ⚠️ 权限: `getIntProperty` 是公开 API, **不需要 `BATTERY_STATS`**。
_BM_PROXY = [None]        # 缓存的 BatteryManager 代理(与 `_VIB_PROXY` 同款)
_PWR_SRC = [None]         # 已锁定的电流源: "now"/"avg"/"sysfs:<路径>"/"zero"; None = 还没探
_PWR_UNIT = ["ua"]        # 电流原始值的单位: "ua" 或 "ma"(厂商报 mA 的场合)
_PWR_ZERO_RUN = [0]       # 连续读到 0 的次数 —— 区分"待机电流"与"这台没这属性"
_INT_MIN = -2147483648
_PS_DIR = "/sys/class/power_supply"


def _read_int_file(_p):
    """读一个只含整数的 sysfs 文件; 失败返回 None(读不到是常态, 不是错误)。"""
    try:
        with open(_p, "r") as _f:
            return int(_f.read().strip())
    except Exception:
        return None


def _battery_manager():
    """取(并缓存) `BatteryManager` 系统服务代理; 取不到返回 None。

    ⚠️ **必须传 `Context.BATTERY_SERVICE` 那个字符串常量**, 不能传
       `autoclass("android.os.BatteryManager")` 的 Class 对象 —— 后者在 pyjnius 下匹配不到
       `getSystemService(Class<T>)` 重载、**静默失败**(同款踩坑见 `_vib_get`)。
    """
    if platform != "android":
        return None
    if _BM_PROXY[0] is not None:
        return _BM_PROXY[0]
    try:
        from jnius import autoclass
        _act = autoclass("org.kivy.android.PythonActivity").mActivity
        _Context = autoclass("android.content.Context")
        _bm = _act.getSystemService(_Context.BATTERY_SERVICE)
        if _bm is None:
            return None
        _BM_PROXY[0] = _bm
        return _bm
    except Exception:
        _BM_PROXY[0] = None
        return None


def _battery_snapshot():
    """读**一次** `ACTION_BATTERY_CHANGED`, 返回 `{"temp","mv","plugged"}`; 读不到返回 None。

    ⚠️ 温度与电压**共用这一次读** —— 该广播被 `BatteryService` 限流(温度变化 ≥1℃
       **或**电压/电量/充电状态等字段变化才发), 多读几次**不会更新**, 所以没有理由
       为电压再开一次往返。
    单位: `EXTRA_TEMPERATURE` = 0.1℃; `EXTRA_VOLTAGE` = mV(未实现时给 0);
       `EXTRA_PLUGGED` 非 0 = 正在充电 ⇒ 此时电流报的是**充入**方向。
    """
    if platform != "android":
        return None
    try:
        from jnius import autoclass
        _act = autoclass("org.kivy.android.PythonActivity").mActivity
        _Intent = autoclass("android.content.Intent")
        _IntentFilter = autoclass("android.content.IntentFilter")
        _BatteryManager = autoclass("android.os.BatteryManager")
        _st = _act.registerReceiver(None, _IntentFilter(_Intent.ACTION_BATTERY_CHANGED))
        if _st is None:
            return None
        _raw = int(_st.getIntExtra(_BatteryManager.EXTRA_TEMPERATURE, _INT_MIN))
        # 厂商未实现时可能返回默认值/异常值; 不让它污染平均值和曲线。
        _temp = (round(_raw / 10.0, 1) if (-500 <= _raw <= 1200) else None)
        _mv = int(_st.getIntExtra(_BatteryManager.EXTRA_VOLTAGE, 0))
        if not (2000 <= _mv <= 6000):
            _mv = None                       # 未实现时给 0 ⇒ 当"没有", 不编数
        _pl = int(_st.getIntExtra(_BatteryManager.EXTRA_PLUGGED, 0))
        return {"temp": _temp, "mv": _mv, "plugged": _pl}
    except Exception:
        return None


_GOLD_MK = COL_BALL.lstrip("#")      # markup 里的颜色写法不带 '#'


def _live_power_temp_line():
    """「启动信息」里那行实时「温度 / 充放电功率」; **读不到就返回空串**(调用方据此整行不出现)。

    玩家 2026-09-18: 「新增一行, 内容是 温度：xx摄氏度，功耗xx.xx瓦, 并且每0.5秒刷新一次」
                  + 「如果是PC读不到信息，就不显示这行」。

    ⚠️ 全部复用现成的读取器(`_battery_snapshot` / `_battery_current_raw` / `_power_from`),
       **不新写一份** —— 高压测试那块已经把这套调好了, 两处各写一份必然漂移。
    ⚠️ 单位判断沿用 `_pwr_guess_unit()` 的**同一个阈值**(原始绝对值 < 20000 只可能是 mA)。
    ⚠️ 只印**读得到**的那部分: 两个都有才完整; 只有温度就只印温度; 都没有返回空串。
       (不印「耗电：--」那种占位 —— 那既是噪音又会让宽度忽长忽短。)
    """
    try:
        _sn = _battery_snapshot()
        if not _sn:
            return ""                       # PC / 读不到 ⇒ 整行不出现
        _parts = []
        _t = _sn.get("temp")
        if _t is not None:
            # ⚠️ 2026-09-18(玩家报的**: 字库是子集体, 新加的中文会变豆腐块**):
            #    「摄氏度」的「摄」「氏」两个字不在字库里 ⇒ 屏幕上是两个方框。
            #    玩家定的解法: **避开新字**, 不动字库 —— 用「度」(常用字, 字库里有)。
            _parts.append("温度：[color=%s]%.1f[/color] 度" % (_GOLD_MK, _t))
        _mv = _sn.get("mv")
        if _mv:
            _raw, _src = _battery_current_raw()
            if _raw is not None and abs(_raw) > 0:
                _unit = "ma" if abs(_raw) < 20000 else "ua"
                _w = _power_from(_raw, _mv, _unit)
                if _w is not None:
                    # ⚠️ 充电时这行也叫「功耗」是错的(玩家: 「那个功耗改为耗电更好吧」)。
                    #    ⚠️ 判据**不能用电流符号** —— `_power_from` 正因为符号不可信才一律 `abs()`
                    #       (厂商两极分化)。用 `_battery_snapshot()` 的 `plugged`(EXTRA_PLUGGED,
                    #       非 0 = 接着电源): 这是与厂商无关的权威来源, 而且**高压测试那边判
                    #       "测试期间在充电"用的就是它**(`power_plugged`)。两处同口径。
                    #    ⚠️ 已知边界(不管): 接着纯数据线 / 供电不足时仍叫「充电」—— 与高压测试
                    #       同口径; 那种场景下电流本来就小, 而"真正的充放方向"需要厂商符号知识
                    #       (高压测试里的 `inverted` 就是干这个的), 不在这一行里猜。
                    _lb = "充电" if _sn.get("plugged") else "耗电"
                    _parts.append("%s：[color=%s]%.2f[/color] W" % (_lb, _GOLD_MK, _w))
        if not _parts:
            return ""
        return "　".join(_parts)
    except Exception:
        return ""


# `/proc/stat` 上一次采样: {核号: (累计 total jiffies, 累计 idle jiffies)}。
# ⚠️ **利用率必须靠两次采样的差值算** —— 单次读数里**没有"利用率"这个量**, 只有开机以来的
#    累计值 ⇒ 这个快照是**必需状态**, 不是缓存。本行每 0.5 秒刷一次, 差值窗口就是那 0.5 秒。
_CPU_STAT_PREV = {}


def _read_proc_stat():
    """读 `/proc/stat` 的**逐核**累计 jiffies ⇒ `{核号: (total, idle)}`; 读不到返回 `{}`。

    ⚠️ 抽成独立函数**只为让门禁能替身注入** —— PC(Windows)上根本没有 `/proc/stat`,
       而"无基线不印利用率""绝不编数"这两条纪律在 PC 上也要能被钉住。
    ⚠️ 口径与 `top` 一致: 空闲 = `idle + iowait`。
       `total` = **前 8 个字段**之和(user+nice+system+idle+iowait+irq+softirq+steal) ——
       后面的 `guest`/`guest_nice` **已经计在 user/nice 里**了, 再加一遍就是重复计。
    """
    _out = {}
    try:
        with open("/proc/stat", "r") as _f:
            for _ln in _f:
                _p = _ln.split()
                if not _p or not _p[0].startswith("cpu"):
                    continue
                _nr = _p[0][3:]
                if not _nr.isdigit():
                    continue            # `cpu `(全局汇总那行, 不是某个核)
                _v = [int(_x) for _x in _p[1:9]]
                while len(_v) < 8:      # 老内核字段少 ⇒ 缺的补 0, 不因为少一个字段整块没有
                    _v.append(0)
                _out[int(_nr)] = (sum(_v), _v[3] + _v[4])
    except Exception:
        return {}
    return _out


def _cpu_util_by_core():
    """逐核 CPU 利用率(0~100 的百分数)⇒ `{核号: 百分数}`; **没基线 / 读不到返回 `{}`**。

    ⚠️ **第一次调用必定返回 `{}`** —— 那一次只负责把基线立起来(见 `_CPU_STAT_PREV`)。
       调用方据此**不印利用率**, 与"频率读不到就不印半张表"是同一条规矩: **绝不编数**。
    ⚠️ 快照**先换再算**: 不管下面算不算得成, 基线都要往前走 —— 否则一旦某次算不成,
       "上一次"就变成一个几秒前的陈旧值, 那段时间的利用率会被整段平均掉。
    ⚠️ `Δtotal ≤ 0` 时**跳过这个核**(两次采样之间一个 jiffy 都没走: 核 offline / 时钟冻结 /
       两次调用挨得太近) —— 不能拿 `Δtotal = 0` 去做除数。
    """
    _cur = _read_proc_stat()
    if not _cur:
        return {}
    _prev = dict(_CPU_STAT_PREV)
    _CPU_STAT_PREV.clear()
    _CPU_STAT_PREV.update(_cur)
    _out = {}
    for _i, (_tot, _idl) in _cur.items():
        _pp = _prev.get(_i)
        if _pp is None:
            continue
        _dt = _tot - _pp[0]
        if _dt <= 0:
            continue
        _u = 100.0 * (1.0 - float(_idl - _pp[1]) / float(_dt))
        _out[_i] = 0.0 if _u < 0.0 else (100.0 if _u > 100.0 else _u)
    return _out


def _live_cpu_freq_line():
    """「启动信息」里那几行实时 CPU 频率 + 可跑核; **读不到就返回空串**(调用方据此整块不出现)。

    ⚠️ **这是新版独有的功能(老版没有)** —— 2026-09-19 用户要求, 见 `changelog/2026-09-19.md`
       第 23 条。产品行为超出 1:1 的**唯一**一处, 老版那半边仍守逐位一致。

    形态(每簇一行: 频率写**当前**、后面跟该簇**利用率**; 可跑核并进第一行):
        CPU：8 核　1+3+4　　可跑核 0-7
        　核 7　　1804M，利用率 45.2%
        　核 4-6　2400M，利用率 12.0%
        　核 0-3　1500M，利用率 8.3%

    ⚠️ **金色只给"会变的那个数"**(玩家 2026-09-19 定的两条要求, 背后是同一条原则):
        · 核数、**当前频率**、**利用率** —— 会变 ⇒ 金色;
        · **可跑核** —— 常量 ⇒ 通用色(不着色)。
       两条要求原话:「可跑核不是变量, 改为通用颜色」/
       「核心频率的格式改为 xxxM/yyyM, 那个 yy 肯定是上限」。
    ⚠️⚠️ **2026-09-20 玩家改了格式**(原话:「改为 `核x-y：XM，利用率xx.x%`」):
        去掉了 `/上限M` 那一半(`cpuinfo_max_freq` 是常量, 每簇就那么一个数, 占着半行却没信息量),
        换成**该簇的实时利用率** —— 它才是"这一簇现在到底在不在干活"的答案。
       ⇒ 底下 fx_gates 里原来钉 `1804M/3187M` 的那条判据**已按新要求改写**, 不是被删掉。

    ⚠️ 分组**复用 `audio.backend._cpu_groups()`**(它已经按 `cpuinfo_max_freq` 分好簇) ——
       本工程明令"不新写一份, 两处各写一份必然漂移"。函数体内 import 是为了守住模块头那条
       「平台层 import 期不许拖别的子系统」的规矩。
    ⚠️ 每簇只读**一个代表核**的 `scaling_cur_freq`(8 核 3 簇 ⇒ 3 次 open, 不是 32 次) ——
       这一行每 0.5 秒跑一次, 是**上屏**路径, 不是跑分那条可以慢慢来的路径。
    ⚠️ 「当前」读的是 `scaling_cur_freq`(**调频器请求值**)。这里**故意不读**
       `cpuinfo_cur_freq`(硬件实际值): 后者很多内核根本不实现(`device.py:42-43` 记着这条),
       而这一行要的是**每簇一个数**的实时观感, 不是跑分要的那种精确归因。真要实际值看跑分历史。
    ⚠️ 全部簇的当前值都读不到 ⇒ 返回空串(整块不出现), 与温度行同一条规矩: **绝不编数**。
    ⚠️ `os.sched_getaffinity(0)` 是 **per-thread** 的: 面板主线程读到的是**主线程**的亲和性。
       锁核只发生在跑分 / CPU 高压测试期间(`_bench_pin_fast_cpus`), **游戏主循环本身不锁**。
       ⇒ 文案写「可跑核」而不是「已锁核」, 免得让人以为平时锁着。
    """
    try:
        from ..audio.backend import _cpu_groups      # 函数体内 import: 见模块头那条规矩
        _grp = _cpu_groups()
        if not _grp:
            return ""                                # PC / 权限 / 无 cpufreq ⇒ 整块不出现
        # ⚠️ 核数用**分组里数出来的**那个, 不用 `os.cpu_count()` —— 后者是**逻辑核数**,
        #    而 `_cpu_groups()` 只统计"有 cpufreq 节点可读"的核。两者不等时(核 offline /
        #    部分核没注册调频节点)`os.cpu_count()` 会和后面 `1+3+4` 那个 shape **自相矛盾**
        #    (印出"8 核　1+3+4"但加起来只有 9 个以外的数)。
        _n = sum(len(_v) for _k, _v in _grp)
        _shape = "+".join(str(len(_v)) for _k, _v in _grp)
        # ⚠️ **可跑核并进第一行**(玩家 2026-09-19 定:「放在 CPU:X核 x+Y 后面, 多个空格即可」)。
        #    它和「几个核」是同一件事的两面, 单独占一行白吃一块高度。
        #    ⚠️ 分隔用**全角空格**、不用 ASCII 空格: 夹在汉字/全角括号之间时 ASCII 空格太窄,
        #       会挤成「1+3+4可跑核」; 本行其它分隔(`核　%s`)也都是全角。
        #    ⚠️ 折行**不会裁切**: `_mk_lbl` 自动撑高, 且开窗后 `_popup_fit_content` 按**真实
        #       排版**重算弹窗高度 —— 窄屏(等效 360 / 系统字体 1.3 倍)上这行折成两句是允许的。
        _aff = ""
        try:
            _a = sorted(os.sched_getaffinity(0))
        except Exception:
            _a = []
        if _a:
            _segs = []
            _s = _e = _a[0]
            for _x in _a[1:]:
                if _x == _e + 1:
                    _e = _x
                else:
                    _segs.append(str(_s) if _s == _e else "%d-%d" % (_s, _e))
                    _s = _e = _x
            _segs.append(str(_s) if _s == _e else "%d-%d" % (_s, _e))
            # ⚠️ **可跑核不上金色**(玩家 2026-09-19 定:「可跑核不是变量, 改为通用颜色」)。
            #    它在一局里**不会变**(锁核只发生在跑分/高压测试期间), 而本块的金色是留给
            #    **活变量**的 —— 这正是玩家那两条要求背后的同一条原则:
            #      **金色 = 会变的那个数; 通用色 = 常量。**
            #    (同一原则的另两次应用: 09-19「上限频率是常量 ⇒ 不上金色」;
            #     09-20「利用率是活变量 ⇒ 上金色」—— 后者顺手把上限那半截整个删了。)
            _aff = "　　可跑核 %s" % ",".join(_segs)
        _lines = ["CPU：[color=%s]%d[/color] 核　%s%s" % (_GOLD_MK, _n, _shape, _aff)]
        _any = False
        # 利用率**整块只采一次**(不是每簇一次): 三个簇共用同一次 `/proc/stat` 差分,
        # 各读各的会把采样窗口错开, 同一屏上三行其实是三个不同时刻。
        _util = _cpu_util_by_core()
        for _k, _v in _grp:
            _base = "/sys/devices/system/cpu/cpu%d/cpufreq/" % _v[0]
            _cur = _read_int_file(_base + "scaling_cur_freq")
            # 核号用**范围**而不是逐个列: 「核 4-6」比「核 4 5 6」短, 也不猜"大核/中核"那种语义。
            _rng = ("%d" % _v[0]) if len(_v) == 1 else ("%d-%d" % (min(_v), max(_v)))
            # ⚠️ 格式 = **当前频率，利用率**(玩家 2026-09-20 定:「改为 `核x-y：XM，利用率xx.x%`」)。
            #    原来那半截 `/上限M` 去掉了 —— `cpuinfo_max_freq` 是**常量**, 每簇就那么一个数,
            #    占掉半行却不提供任何"现在发生了什么"的信息。
            #    ⚠️ 单位保持 `M`(`4608M` 那种), **不是** `Mhz` —— 玩家第一次笔误成 `Mhz`,
            #       第二遍更正为 `M`。别自作主张加单位后缀。
            # ⚠️ 簇内**取有读数的核平均**(同簇频率一致, 各核差异很小); 一个读数都没有 ⇒ 整段不印,
            #    不编一个数出来(与本函数"绝不编数"同一条规矩)。
            _us = [_util[_c] for _c in _v if _c in _util]
            _ut = ("，利用率[color=%s]%.1f[/color]%%" % (_GOLD_MK, sum(_us) / len(_us))
                   if _us else "")
            if _cur:
                _any = True
                _lines.append("　核 %s　[color=%s]%d[/color]M%s"
                              % (_rng, _GOLD_MK, _cur // 1000, _ut))
            else:
                # 当前频率读不到 ⇒ 频率位写 `--`, 不编一个数出来(同一条规矩)。
                _lines.append("　核 %s　--%s" % (_rng, _ut))
        if not _any:
            return ""                                # 一个当前频率都没有 ⇒ 不印半张表充数
        return "\n".join(_lines)
    except Exception:
        return ""


def _pwr_sysfs_paths():
    """枚举 `/sys/class/power_supply/*/current_now` 候选路径(读不到就是空表)。"""
    _out = []
    try:
        for _n in sorted(os.listdir(_PS_DIR)):
            _p = _PS_DIR + "/" + _n + "/current_now"
            if os.path.exists(_p):
                _out.append(_p)
    except Exception:
        pass
    return _out


def _pwr_read_by(_src):
    """按**已锁定**的来源读一次电流原始值。

    返回整数(有效读数) / `None`(当次读失败) / `"RETRY"`(这条路废了, 让调用方重探)。
    """
    if _src.startswith("sysfs:"):
        _v = _read_int_file(_src[6:])
    else:
        _bm = _battery_manager()
        if _bm is None:
            return None
        try:
            # BATTERY_PROPERTY_CURRENT_NOW = 2, CURRENT_AVERAGE = 3
            _v = int(_bm.getIntProperty(2 if _src == "now" else 3))
        except Exception:
            _BM_PROXY[0] = None
            return None
        if _v == _INT_MIN:
            return "RETRY"                   # 属性消失/服务重启 ⇒ 重探
    if _v is None:
        return None
    if abs(_v) > 30000000:                   # >30A ⇒ 哨兵/垃圾, 不是真读数
        return None
    return _v


def _battery_current_raw():
    """读一次瞬时电流, 返回 `(原始整数, 来源)`; 读不到返回 `(None, None)`。

    三条路按序试, **第一条能用的锁定**(记进 `_PWR_SRC`), 之后不再重复试探 ——
    缓存的是"哪条路能用", **不是值**(值必须每次真读)。
      ① `CURRENT_NOW`     ← 官方 API(API 21+), 无需权限
      ② `CURRENT_AVERAGE` ← 有的 gauge 只给平均
      ③ sysfs `/sys/class/power_supply/*/current_now` ← 有的厂商只在这儿给

    判废: `MIN_VALUE` = 本机不支持(目标 SDK ≥P 时官方约定的"不支持"返回值);
       `0` = 可能真是待机电流, **连续 3 次**才判定"这台没这属性"并往下换路。
    """
    _src = _PWR_SRC[0]
    if _src == "zero":
        return None, "zero"                  # 已判定没有 fuel gauge, 不再重复试探
    if _src is not None:
        _v = _pwr_read_by(_src)
        if _v == "RETRY":
            _PWR_SRC[0] = None               # 掉下来重探
        elif _v is not None:
            return _v, _src
        else:
            return None, _src                # 当次失败 ⇒ 交给上层记 None
    # ---- 还没锁定: 按序探 ----
    _bm = _battery_manager()
    if _bm is not None:
        for _name in ("now", "avg"):
            try:
                _v = int(_bm.getIntProperty(2 if _name == "now" else 3))
            except Exception:
                _BM_PROXY[0] = None
                break
            if _v == _INT_MIN:
                continue                     # 本机不支持这个属性
            if _v == 0:
                _PWR_ZERO_RUN[0] += 1
                if _PWR_ZERO_RUN[0] < 3:
                    return 0, _name          # 先当"待机电流"用, 攒够 3 次再说
                continue
            _PWR_ZERO_RUN[0] = 0
            _PWR_SRC[0] = _name
            return _v, _name
    for _p in _pwr_sysfs_paths():
        _v = _read_int_file(_p)
        if _v:
            _PWR_SRC[0] = "sysfs:" + _p
            return _v, _PWR_SRC[0]
    if _PWR_ZERO_RUN[0] >= 3:
        _PWR_SRC[0] = "zero"                 # 三条路都试过: 这台**没有** fuel gauge
    return None, None


def _power_from(_i, _mv, _unit):
    """电流原始值 + 电压(mV) → 瓦(W); 任一项缺就返回 None。**取绝对值**。

    ⚠️ **符号不参与计算**: AOSP 定义"正 = 充入电池, 负 = 放电", 但厂商实现不统一
       (社区实测两极分化)。玩家要的是功率**大小** ⇒ 一律 `abs()`;
       符号只作诊断(见 `_pwr_finish` 的 `sign_inverted`)。
    """
    if _i is None or _mv is None:
        return None
    _a = abs(float(_i)) / (1000.0 if _unit == "ma" else 1000000.0)
    return abs((float(_mv) / 1000.0) * _a)


def _pwr_guess_unit(_raws):
    """从全程原始值猜**单位**, 返回 "ua" 或 "ma"。

    判据: 360 秒满负载 + 屏幕常亮下, 电流**必然** ≥200mA。
      ⇒ 原始值的中位绝对值 < 20000 时只可能是 **mA**(20000µA 才 20mA, 不可能);
      ⇒ 否则是 **µA**(常见 30 万~150 万)。
    ⚠️ 这是启发式, 结论写进记录的 `power_unit` 供追溯。
    """
    _v = sorted(abs(x) for x in _raws if x)
    if not _v:
        return "ua"
    return "ma" if _v[len(_v) // 2] < 20000 else "ua"


# ⚠️ 第 4 档那两个字的**头一个字不在字体子集里**(`tests/font_check.py` 会红, 真机上印方块),
#    所以换成字库内的同义词「临界」—— 含义不变; 改法照 README「字体是裁剪子集」那条。
_THERMAL_NAMES = ("无", "轻微", "中等", "严重", "临界", "紧急", "关机")
"""`PowerManager.getCurrentThermalStatus()` 的 0~6 对应的中文(与 `_thermal_status` 配对)。

⚠️ 界面上**已经不显示它了**(玩家把"热限制等级"那行删了)。留着是因为**采集还在**
   (`_hp_battery["thermal"]` 照旧写进历史 JSON) —— 将来想再看一眼, 把显示那几行加回来就能直接用。
"""


def _thermal_status():
    """当前**热限制等级**(Android 10+), 读不到返回 `None`。

    0=NONE / 1=LIGHT / 2=MODERATE / 3=SEVERE / 4=CRITICAL / 5=EMERGENCY / 6=SHUTDOWN

    这是和电池温度**互补**的一个维度: 温度说"多热", 它说"系统开始降频了没有" —— 高压测试真正
    关心的是后者。

    ⚠️ **不需要任何权限**(`PowerManager.getCurrentThermalStatus()` 是 API 29+ 的公开方法) ——
       和电池温度(被 `BatteryService` 的广播限流卡着)**不一样, 这条是直接可用的**。
    ⚠️ 但它**依赖设备的 Thermal HAL 2.0**: 不支持的设备会**一直返回 0(NONE)**, 而 0 同时也是
       "真没热限制" —— **两种分不出来** ⇒ 只能和温度一起看, 不能单独下结论。
    ⚠️ 拿不到就返回 `None`, **绝不编个 0 冒充**(那会变成"这台机器很凉快"的假结论)。
    """
    if platform != "android":
        return None
    try:
        from jnius import autoclass
        _act = autoclass("org.kivy.android.PythonActivity").mActivity
        # ⚠️ 常量从**类**上取 —— 与本文件 `_vib_get` 的 `Context.VIBRATOR_SERVICE` 逐字同款。
        #    别写 `_act.POWER_SERVICE`: 实例取法**大概率也能用**, **但失败是静默的** ——
        #    AttributeError 被下面的 except 吞掉 → 印成"设备不支持 Thermal HAL", 你根本查不出来。
        # ⚠️ 也**不需要 `cast`**: pyjnius 对声明返回 `java.lang.Object` 的方法会用**运行时类**建代理。
        _Context = autoclass("android.content.Context")
        _pm = _act.getSystemService(_Context.POWER_SERVICE)
        return int(_pm.getCurrentThermalStatus())
    except Exception:
        return None


def _thermal_probe():
    """探测**这台设备**上有没有普通 app 读得到的 thermal zone; 返回 `[(序号, 类型, 温度), ...]`。

    为什么要有这个: 电池温度只有 `ACTION_BATTERY_CHANGED` 一条路, 而它经过 `BatteryService` 的
    **广播限流**(温度变化 ≥1°C **或** 电量/电压/充电状态等字段变化才发), 所以曲线必然是**台阶**。
    更底层的源(Health HAL / thermal sysfs)在架构上**存在**, 但普通 app 的 `untrusted_app` 域
    **通常**读不到 —— ⚠️ 而 **"通常读不到" ≠ "这台读不到"**(厂商可以自己开口子), 这个探测就是去问一句。
    ⚠️ 读不到是**常态, 不是错误** —— 所有异常一律吞掉、跳过。
    ⚠️ 单位: Linux thermal sysfs 的 `temp` 是**毫摄氏度**(`31480` = 31.48°C)。
    ⚠️ **别只认 `type` 里带 "battery" 的**: 名字是厂商随手起的, 而且"名字叫 battery"也**不代表**它
       就是 BatteryService 用的那个温度源 ⇒ 这里把**所有**读得到的 zone 都记下来, 由人比对。
    """
    if platform != "android":
        return []                            # 桌面没有 /sys, 白开 24 次文件没意义(只是难看)
    out = []
    for i in range(32):                      # 实测一台联想读到 24 个(0~23 连续), 留点余量
        _d = "/sys/class/thermal/thermal_zone%d/" % i
        try:
            with open(_d + "type") as fh:
                _t = fh.read().strip()
        except Exception:
            continue                         # 没这个 zone(或没权限) ⇒ 跳过, 不当错误
        _v = None
        try:
            with open(_d + "temp") as fh:
                _s = fh.read().strip()
            # ⚠️ **空文件要记成 None, 不能记成 0** —— 写成 `int(_s or 0)` 的话空文件会变成
            #    `0.0度`, 一个看起来很真的假数, 与本函数"读不到就不编数"的初衷正好相反。
            if _s:
                _v = int(_s) / 1000.0
        except Exception:
            pass                             # 有类型没温度(少见) ⇒ 保持 None, **不编数**
        out.append((i, _t, _v))
    return out


def _pwr_finish(_raw, _dt, _plugs):
    """算功率序列的统计 —— **在采样线程退出前跑一次**, 整份结果交给主线程。

    ⚠️ 为什么在这里算: 主线程读 `_raw` 的时候采样线程可能还在 append。
       线程自己算完写进收集器, 主线程拿到的才是一份完整的。

    返回的 dict **只存标量**(序列另存), 键:
      n / n_chg / frac —— 有效读数个数 / "值发生变化"的次数 / n_chg ÷ 有效相邻对数
      gap_*    两次"变化"之间的间隔(秒), **按网格下标差 × dt 算** —— 不取墙钟差, 免掉读取耗时与
               调度抖动(这是绝对网格白送的好处)
      est/est_kind  刷新周期估计与种类
      n_uniq/step_med  唯一值个数 / 相邻非零差的中位台阶(诊断"这台报得多细")
      inverted 测试期间在充电、电流却多数为负 ⇒ 厂商符号约定与 AOSP 相反

    ⚠️⚠️ **5Hz 的分辨下限是 200ms**: `frac ≈ 1.0`(每次采样都变)时只能说「τ ≤ 0.2 秒」—— 那个 0.2
       是**采样周期本身**, 不是刷新周期, 报成 175ms 就是编数。只有底层明显比采样慢(像 Nexus 10 的
       3.5 秒)才给具体值, 此时 `est = dt / frac`(每次采样撞上更新的概率 ≈ dt/τ)。
    """
    _v = [(i, x) for i, x in enumerate(_raw) if x is not None]
    _n = len(_v)
    _chg = []
    _pairs = 0
    _d = []
    for _k in range(1, _n):
        _pairs += 1
        if _v[_k][1] != _v[_k - 1][1]:
            _chg.append(_v[_k][0])
            _d.append(abs(_v[_k][1] - _v[_k - 1][1]))
    _nch = len(_chg)
    _gaps = sorted((_chg[_k] - _chg[_k - 1]) * _dt for _k in range(1, _nch))
    _frac = (_nch / float(_pairs)) if _pairs else 0.0
    _neg = sum(1 for _, _x in _v if _x < 0)
    _d.sort()
    return {
        "n": _n, "n_chg": _nch, "frac": round(_frac, 3),
        "gap_min": (_gaps[0] if _gaps else None),
        "gap_p50": (_gaps[len(_gaps) // 2] if _gaps else None),
        "gap_p90": (_gaps[min(len(_gaps) - 1, int(len(_gaps) * 0.9))] if _gaps else None),
        "gap_max": (_gaps[-1] if _gaps else None),
        "est": ((None if _frac >= 0.9 else round(_dt / _frac, 2)) if _frac > 0 else None),
        "est_kind": ("le_dt" if _frac >= 0.9 else ("ratio" if _frac > 0 else "none")),
        "n_uniq": len(set(x for _, x in _v)),
        "step_med": (_d[len(_d) // 2] if _d else None),
        "inverted": bool(_n and _neg > _n * 0.5 and any(_plugs)),
    }


def _battery_sampler_start(win, interval_sec=1.0, power_hz=5.0):
    """同时采电池**温度**(`interval_sec`)与电池**功率**(`power_hz`)。返回 `(温度列表, 停止标志, 功率收集器)`。

    温度列表: 与旧版**逐字同语义** —— 只装读成功的值, **失败不留占位**, 保留时间序
      (`_bat_sorted` / `len>=2` 判据 / 历史 `battery_series` 的消费者全都不动)。
    功率收集器: dict, 由采样线程在**退出前**把序列与统计算好写进去 —— 主线程读的时候采样线程已经
      结束, 不存在"边读边 append"。

    ⚠️⚠️ 两条序列的**时基不同、长度不同, 这是刻意的**: 功率 `power_hz` Hz(默认 0.2s)窗口 [1,359]
       ⇒ 约 1790 点; 温度 `interval_sec`(1.0s)同一个窗口 ⇒ 约 359 点。双轴图按**秒**画横轴
       (见 `SpeedCurve`), 所以长度不同**不需要对齐**。
       功率那条走**构造网格** `t_i = i / power_hz`; 某次读超时就**逐格补 None**(不是跳格)
       ⇒ 下标 ↔ 时刻恒成立。温度那条沿用"不 append"的旧语义。

    ⚠️ 成本: 采样线程本来每秒最贵的是 `_cpufreq_cores()`(最多 32 次 sysfs open, 1Hz); 本次新增
       5 次/s 的 `getIntProperty`(一次 Binder 往返, 约 0.1~0.5ms); 温度/电压那条**一次都没多**。
    """
    _values = []
    _values_t = []
    _stop = [False]
    _step = max(0.1, float(interval_sec))
    _p_step = 1.0 / max(0.5, float(power_hz))
    _sink = {"raw": [], "t": [], "w": [], "mv": [], "plugged": [], "bat_t": _values_t,
             "dt": _p_step, "stats": None, "src": None, "unit": "ua"}

    def _samp():
        _np = None
        _nt = None
        _mv_last = None
        _pl_last = 0
        while not _stop[0]:
            _now = time.time()
            try:
                _start = float(win[0])
                _end = float(win[1])
            except Exception:
                _start = _end = 0.0
            if _start <= 0.0 or _end < _start:
                time.sleep(0.05)
                continue
            if _np is None:
                _np = _start
                _nt = _start
            if _np > _end:
                break
            if _now < _np:
                time.sleep(min(0.10, max(0.01, _np - _now)))
                continue
            # ---- 温度/电压: 每 `_step` 那一格顺带读(1Hz 整除 5Hz ⇒ 必落在功率格上) ----
            if _nt <= _now:
                _sn = _battery_snapshot()
                if _sn is not None:
                    if _sn.get("temp") is not None:
                        _values.append(float(_sn["temp"]))
                        _values_t.append(round(_nt - _start, 1))
                    if _sn.get("mv") is not None:
                        _mv_last = _sn["mv"]
                    _pl_last = int(_sn.get("plugged") or 0)
                _nt += _step
                if _nt <= _now:
                    _nt = _start + (int((_now - _start) // _step) + 1) * _step
            # ---- 功率: 每次到点都读, 失败记 None(采样期间**只存原始值**) ----
            _raw, _src = _battery_current_raw()
            if _src:
                _sink["src"] = _src
            _sink["raw"].append(_raw)
            _sink["t"].append(round(_np - _start, 1))
            _sink["mv"].append(_mv_last)
            _sink["plugged"].append(_pl_last)
            # 追下一格; 落后超过一格就**逐格补 None**(不跳格 ⇒ 下标↔时刻恒成立)
            _np += _p_step
            while _np <= _now and _np <= _end:
                _sink["raw"].append(None)
                _sink["t"].append(round(_np - _start, 1))
                _sink["mv"].append(_mv_last)
                _sink["plugged"].append(_pl_last)
                _np += _p_step
        # ⚠️ 单位只能**采完之后**猜(要看全程分布) ⇒ 功率序列也在这里统一算。
        #    采样期间若按错的单位算 W, 会得到一串差 1000 倍的值。
        _sink["unit"] = _pwr_guess_unit(_sink["raw"])
        _sink["w"] = [_power_from(_r, _m, _sink["unit"])
                      for _r, _m in zip(_sink["raw"], _sink["mv"])]
        _sink["stats"] = _pwr_finish(_sink["raw"], _p_step, _sink["plugged"])

    try:
        threading.Thread(target=_samp, daemon=True).start()
    except Exception:
        pass
    return _values, _stop, _sink

