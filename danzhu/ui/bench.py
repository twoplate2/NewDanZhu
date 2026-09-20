"""BenchMixin —— RootWidget 的隐藏跑分工程(老版 `android/main.py` 14170-19742 那一整段)。

长按标题 3 秒进。四块: 性能测试(渲染采样 + 波 1 物理演算) / CPU 高压 / 启动信息与
重放冷启动 / 逐帧诊断与历史。

## 与老版最大的两条结构差别

1. **游戏状态住在 `self.game` 上**, 不在 `self` 上。老版的 `self.balance` / `self.state` /
   `self.multipliers` / `self._bench_running` … 现在一律 `self.game.xxx`。跑分**驱动游戏**
   时走 `Game` 的公开口(`start_charge` / `launch` / `park_ball` / `_refresh_stats` /
   `_save_config`), **不许在本文件另起一套状态** —— 那是这个重构要消灭的东西。
   `Game` 里与跑分相关的位: `_bench_running` / `_bench_ball_i` / `_bench_status_active` /
   `_bench_start` / `_bench_triggered`(见 `danzhu/game.py` 的 `launch` / `park_ball` /
   `settle` / `_refresh_stats` / `_check_title_hold` 各处)。
   本 Mixin 只留**跑分自己的**状态(采样数组、黑屏开关、弹窗引用、`hp_history` 等)。

2. **长按计时不在本文件** —— `_bench_start` / `_bench_triggered` / 3 秒判据整个住在
   `Game._check_title_hold`(每帧由 `Game._frame` 调), 到点产一条 `bench_menu` 事件。
   那条事件的**真正落点是 `PlayMixin._dispatch`**(`kind == "bench_menu"` 那一支直接调
   `_show_bench_menu()`); 本文件那个同名方法**一个调用者都没有**(见它的 docstring)。

⚠️ **本文件不许出现 `on_touch_down` / `on_flip` 之类 Kivy 自己也有的事件方法** ——
   `BenchMixin` 排在 `PlayMixin` 之前(MRO), 同名方法会**静默**盖掉输入锁(见 root.py 抬头)。
   `_on_flip` 不在禁止之列: 它是 `Window.bind(on_flip=...)` 的回调, 不是控件事件。

⚠️ **格式化文本是判据, 不是文案**: `_prog_text` / `_hp_result_text` / `_hp_freq_line` /
   `_bench_score_text` / `_power_log_text` / `_bench_frame_log` / `_startup_log_text` /
   `_bench_low_summary_text` 里一个空格、一个单位、一个格式符都别动 —— 玩家是按这些字
   读数的, 改一个字符就是换了一次口径。
"""

import json
import math
import os
import random
import sys
import tempfile
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.utils import platform

# ⚠️ 帧计数器/字体账本那几个(**名字里的下划线是老的**, 新架构沿用同一批名字, 见各模块抬头):
#    `_FRAME_BRK` / `_FRAME_CALLS` / `_FRAME_FIT` / `_FRAME_SWAP` / `_TEXUPD*` / `_TEXEX_*` /
#    `_COLD_FS*` / `_FIT_HIST` / `_FS_*` / `_PIN_*` 都在 text.py(帧仪表那一桶)。
from .text import (_COLD_FS, _FIT_HIST, _FRAME_BRK, _FRAME_CALLS, _FRAME_FIT, _FRAME_SWAP,
                   _FS_COLD_CNT, _brk_add,
                   _FS_MUTED_N, _FS_MUTE_SEC, _FS_MUTE_UNTIL, _FS_OPEN, _FS_OPEN_SET,
                   _PIN_AFTER, _PIN_CAND, _PIN_MAX, _PIN_ORDER, _PIN_POPUP_SKIP, _PIN_STAT,
                   _PIN_WHERE, _TEXEX_HIT, _TEXEX_MISS, _TEXEX_ON, _TEXUPD, _TEXUPD_ACTIVE,
                   _SINCE_LAUNCH, _TEXUPD_BY, _FRAME_SELF, _hist_stamp,
                   _hp_score, _set_label_text,
                   COLD_FS_MIN_MS, fit_font_size, text_px)
from .veil import _LoadVeil
from .widgets import (FpsCurve, RotPopup, SpeedCurve, STAGE_ORDER, _app_version,
                      _bench_result_title, _bench_score_text, _med5, _soc_result_title,
                      _startup_title)
# ⚠️⚠️ `_FONT_WARM_HUD` / `_FONT_WARM_SIZES` **必须走模块属性读**(`winfx.xxx`), 不能
#     `from .winfx import`: 这两个表在 `prebake_step` 里是**整个重绑**(`globals()[...] = tuple(...)`),
#     不是原地改 —— from-import 拿到的是导入那一刻的 `()` / `None`, 之后永不更新,
#     下面「字号预热表 N 项」会**恒印 0**(而真值是 48+4=52), 正是本工程反复防的自证失效。
#     `_GC_FROZEN` / `_PREBAKE_DONE` 是下标原地改的 list, from-import 没有这个问题。
from . import winfx
from .winfx import _GC_FROZEN, _PREBAKE_DONE

from ..audio.backend import (ARM_LADDER, SND_SLOW_MS, _FRAME_PROBE, _LOAD_BYTES, _SND_STAT,
                             _cpu_shape, _sfx_cache_dir)
from ..config import (BENCH_BOARD, BENCH_SEED, COL_BALL, COL_BTN, COL_BTN_OFF, COL_DARKRED,
                      COL_DIV, COL_FIRE, COL_SOC, COL_SUB, COL_TEXT, NUM_SLOTS, POWER_GRAIN_K,
                      START_BEADS, VEIL_TITLE_MIN_SEC, _FPS_INFO, hex_rgb)
from ..platform.benchcpu import (BENCH_TARGET_LAUNCHES, HP_FREQ_TRIM_SEC, HP_PWR_SKIP_SEC,
                                 HP_SAMPLE_SEC, SOC_SUSTAIN_WALL_SEC, _BENCH_AFF, _BENCH_ALLOC,
                                 _BENCH_CPU_PIN, _BENCH_RENDER_FPS, _BENCH_SPEED, _BENCH_TID_PRIO,
                                 _PHYS_PROG, _PHYS_SLEPT, _PHYS_TOTAL_SEC, _PHYS_WAIT_T0,
                                 _SUST_SPEED, _battery_sampler_start, _battery_temp_c,
                                 _bench_pin_fast_cpus, _bench_raise_thread_priority,
                                 _bench_restore_cpu_affinity, _bench_restore_thread_priority,
                                 _freq_core_stats, _freq_sampler_start, _live_cpu_freq_line,
                                 _live_power_temp_line, _mad_coef, _thermal_probe, _thermal_status,
                                 benchmark_sustained, benchmark_trajectories)
from ..platform.boot import _BOOT_LOG, _BOOT_T0, _PROBE_COST, _PROBE_TRACE, _boot_log
from ..platform.device import (BUILD_TIME_FMT, _BENCH_FPS_FORCE, _CFG_STAT, _CPUFRQ, _JNI_STAT,
                               _VIB_STAT, _bench_fps_force_now, _bench_fps_lock_off,
                               _bench_fps_lock_on, _cpu_split, _cpufreq_start, _cpufreq_stop,
                               _fps_user_cap, _screen_hz, _set_keep_awake, _set_orient_lock,
                               _set_system_ui)

# ⚠️ 下面这几个是**本文件自己的**跑分标定/账本 —— 老版在 main.py 模块级, 但读者只有跑分:
#    `JANK_RATE` / `SLOW_RATE` 只被 `_bench_collect_diag` / `_bench_frame_log` /
#    `_bench_low_summary_text` 读(全工程 grep 过, 别处没有);
#    `_BENCH_HZ` / `_HIST_COLS` / `_FRAME_THR` 同理(写的人也只有本文件)。
# ⚠️ 语义: 帧率 < 中位 x JANK_RATE => 卡顿帧; < 中位 x SLOW_RATE => 慢帧。
#    等价于**帧间隔** > 中位间隔 / 该比例(所以下面写的是 `_med / JANK_RATE`)。
JANK_RATE = 0.55
SLOW_RATE = 0.75
# 采样期屏幕刷新率采样: {(场景标签, Hz): 次数}(见 `_bench_hz_tick`)。
_BENCH_HZ = {}
# 「测试历史（渲染 / CPU）」的表头。⚠️ 与数据行**分开排版**(见 `_show_bench_history`)。
_HIST_COLS = ('时间', '平均/1%Low帧', '平均分/平均差系数')
# 本帧**主线程**烧了多少毫秒 CPU(线程级时钟)。写的人只有 `_on_flip`, 读的人只有跑分面板。
_FRAME_THR = [0.0]
# 线程级时钟。⚠️ Windows 没有 `thread_time`, 退回 `process_time` —— 桌面精度只有 15.6ms,
# 那一列在 PC 上基本是台阶, 分流**只能在真机上看**。
_THREAD_TIME = getattr(time, "thread_time", None) or time.process_time


def _bench_menu_desc():
    """跑分菜单里那段说明的正文。

    ⚠️ **末句那个"约 67 秒"是手写的粗估**, 不是算出来的: 它 = 渲染窗口(约 25 秒, 由
       `_target_launches` 发 5 颗球决定, 不是常量) + 波 1(预热 + `SOC_SAMPLE_RUNS` x
       `SOC_SAMPLE_CPU_SEC` + 间隔)。改那几个参数要回来改这个数。
    ⚠️ 三行是**逐字照抄玩家给的文本**(含「1、」这个顿号编号、小写 `python`、数字两侧不加空格)
       —— 他要的就是这三行, 别自作主张改标点。三行都放得下, 不折行。
    """
    return ('模拟测试约 67 秒（含装杯动画）\n测试三项设备性能：\n'
            '1、累计发射5颗弹珠，测屏幕渲染帧率\n'
            '2、用python物理引擎，测CPU单核浮点\n'
            '3、用高压测试，考验CPU调度和散热')


class BenchMixin(object):

    # ---------------------------------------------------------------- 状态栏
    def _show_bench_status(self, text):
        """把跑分状态放在底部操作区正上方，复用统计栏而不遮住盘面。"""
        self.game._bench_status_active = True
        _set_label_text(self.stats_lbl, text.replace("\n", "　·　"))
        self.stats_lbl.color = hex_rgb(COL_FIRE) + (1,)
        fs = self._font_scale * self._ui_scale
        self.stats_lbl.font_size = sp(16) * fs
        self.stats_lbl._fit_base = self.stats_lbl.font_size
        self._fit1(self.stats_lbl)

    def _hide_bench_status(self):
        self.game._bench_status_active = False
        fs = self._font_scale * self._ui_scale
        self.stats_lbl.font_size = sp(15) * fs
        self.stats_lbl._fit_base = self.stats_lbl.font_size
        # ⚠️ 走 `Game._refresh_stats()`(它产一条 `stats` 事件给界面) —— 统计口径的唯一真源
        #    在 Game(plays/hits 都在那边), 本文件不许自己拼那行字。
        self.game._refresh_stats()

    # ---------------------------------------------------------------- 黑屏
    def _show_bench_dim(self):
        self._bench_dim_shown = True
        # ⚠️ **黑屏期间锁死屏幕方向**: 否则很容易出问题 —— 会出现非黑屏画面, 甚至有原有的
        #    弹珠屏幕。放在最前面: 后面那几步(重排矩形/切系统栏)都依赖"方向不会在它们之间变"。
        _set_orient_lock(True)
        # ⚠️ 0.72 → **1.0**(玩家定稿): 原来 0.72 是为了"盖住但还看得见板面", 现在要的是
        #    **纯黑** —— 跑分期间没什么可看的, 少画就是少抢 CPU。
        self._bench_dim_col.rgba = (0.05, 0.06, 0.09, 1.0)
        self._relayout_bench_dim()
        # ⚠️⚠️ **黑屏期间切"真全屏"**(波 1 物理演算与波 2 CPU高压**共用这一对** —— 两波都是
        #    先 `_show_bench_dim()` 再跑, 所以"都算"是自动满足的, 不用各写一份)。
        #    ⚠️ 放在 `_relayout_bench_dim()` **之后**: 切档会让窗口 inset 变一次(重新布局),
        #       先让黑屏铺满再切, 那一瞬间的重排就落在黑屏底下、看不见。
        _set_system_ui(True)
        # ⚠️ 同一对开关的**另一半**: 跑分期间**不让屏幕息屏**。屏幕一灭会让安卓暂停游戏、
        #    冻结 Clock, 6 分钟的高压测试会被打断、成绩直接失真 ⇒ 这是**正确性**问题。
        _set_keep_awake(True)

    def _set_bench_msg(self, text):
        """黑屏上的白字(进度)。文字没变就整个跳过 —— 一次 refresh 要重排文字。"""
        _t = str(text or "")
        if getattr(self, "_bench_msg_last", None) == _t:
            return
        self._bench_msg_last = _t
        try:
            self._bench_msg_lbl.text = _t
            self._bench_msg_lbl.refresh()
            self._bench_msg_rect.texture = self._bench_msg_lbl.texture
            self._bench_msg_col.a = 1.0 if _t else 0.0
        except Exception:
            pass
        self._relayout_bench_dim()

    def _relayout_bench_dim(self, *_):
        if getattr(self, "_bench_dim_shown", False):
            # ⚠️⚠️ **视口必须读 `self.parent`(App.build 里那个 AnchorLayout), 不能按 `self.x`
            #    反推**: 病根是上一版写的 `pos = (-self.x, -self.y)` + `size = self._veq()` ——
            #    作者的意图是"把原点挪到视口原点", **但 Kivy 的 canvas 指令本来就是绝对(窗口)
            #    坐标, 父级不做平移** ⇒ 那个 `-self.x` 反而把矩形推到**负坐标**, 右边露出一条
            #    `self.x` 宽的缝。**只有 `self.x != 0` 的机器才看得见**(手机上内容列正好铺满
            #    `self.x = 0` 永远正常; 平板居中后 `self.x ≈ 6dp` ⇒ 右边露约 16px)。
            #    ⇒ 与 `_relayout_hud_dim` 逐字同款: 直接用父容器那个矩形 —— 它就是"等效视口",
            #      横屏反旋转时铺满整块物理屏。
            vp = self.parent
            if vp is None or vp.width <= 1.0 or vp.height <= 1.0:
                vp = self                       # 未挂父/尺寸未定: 退回自身(探针夹具走这条)
            self._bench_dim_rect.pos = (vp.x, vp.y)
            self._bench_dim_rect.size = (vp.width, vp.height)
            # ⚠️ 居中靠 `texture_size / 2`, 不能用固定尺寸: 文字长度会变(`物理演算第3/45秒`)。
            try:
                _ts = self._bench_msg_lbl.texture.size
                self._bench_msg_rect.pos = (vp.x + (vp.width - _ts[0]) / 2.0,
                                            vp.y + (vp.height - _ts[1]) / 2.0)
                self._bench_msg_rect.size = _ts
            except Exception:
                pass

    def _hide_bench_dim(self):
        self._bench_dim_shown = False
        _set_orient_lock(False)      # 与 `_show_bench_dim` 那一对, 见那边的说明
        self._bench_dim_col.rgba = (0, 0, 0, 0)
        self._bench_dim_rect.size = (0, 0)
        # ⚠️ **黑屏撤掉 ⇒ 系统栏切回"非沉浸"**。必须**主动切**: 这一档是"系统默认值",
        #    不主动清标志就会一直全屏着(黑屏没了、屏还是全屏的)。
        _set_system_ui(False)
        # ⚠️ 跑分一结束就把"常亮"还回去 —— 别忘了这一半, 否则跑完一次之后**屏幕永远不灭**。
        _set_keep_awake(False)
        # ⚠️ 白字必须一起撤 —— 否则跑分结束后那行字会**留在黑屏位置**(黑屏没了、字还在)。
        try:
            self._bench_msg_col.a = 0.0
            self._bench_msg_rect.size = (0, 0)
            self._bench_msg_last = None
        except Exception:
            pass

    def _check_title_hold(self):
        """长按标题 3 秒 → 性能测试菜单。**本方法没有任何调用者, 计时也不在这里。**

        ⚠️⚠️ 老版这一段的判据(`_bench_start` / `_bench_triggered` / 3 秒 / `_rtp_hold_*`)
           **整个搬进了 `Game._check_title_hold`**(每帧由 `Game._frame` 调), 它到点产一条
           `bench_menu` 事件。再在这里抄一份计时就是**两份状态抢同一个 `_bench_triggered`**
           —— 谁先跑到谁置真, 菜单会随机少弹或多弹。
        ⚠️ 那条事件的落点是**事件派发**: `PlayMixin._dispatch` 里 `kind == "bench_menu"`
           那一支直接调 `_show_bench_menu()` —— 它**不经过本方法**。本方法保留老名字
           (与老版那个方法同名)、体只一句转发, 作为"哪天有人按老名字接回来"的入口;
           它不参与任何现行路径, 别照它去推理弹窗是谁弹的(全工程 grep 只有这一处 def)。
        """
        self._show_bench_menu()

    # ---------------------------------------------------------------- 进度显示
    # ⚠️⚠️ **渲染窗口那 25 秒一个字都不显示**: 那段正在测 1%Low, 在里面写标签就是往被测帧
    #    上加活儿。**只有后面全力跑 CPU 高压 / 物理演算时才显示** —— 那两个阶段
    #    `_finish_render_sample` 已经跑过(屏幕采样停了) ⇒ 写标签不进成绩。
    #    两种文案都由 `_prog_text` 出: `CPU高压测试第d/d秒`(分子从 **1** 起) 与
    #    `物理演算第d/d秒`(分子从 **1** 起、分母 `ceil(总秒数)`, 实测 **45**)。
    def _prog_text(self):
        """当前该显示的进度文案; 没有测试在跑就返回 None。"""
        if getattr(self, "_hp_running", False):
            # ⚠️ **高压的进度分子分母同源 = 墙钟**(`benchmark_sustained` 的循环条件就是
            #    `total_wall_sec`)。旧版报 CPU 秒、循环也按 CPU 秒跑 ⇒ 面板顶到 300/300 时
            #    活儿还剩两成没干完。**关键不在用不用墙钟, 在"和循环条件是不是同一根尺子"。**
            #    ⚠️ 超了就**封顶**, 不造第二句会跳动的文案(「成绩核算中：x秒」已整个删掉 ——
            #    核算实测只要 51 毫秒, 而轮询 0.25 秒一次 ⇒ 那句多数情况下连一帧都画不出来,
            #    删掉零损失)。
            _w0 = getattr(self, "_hp_wall0", 0.0)
            _el = (time.time() - _w0) if _w0 else 0.0
            _cap = int(SOC_SUSTAIN_WALL_SEC)
            return "CPU高压测试第%d/%d秒" % (min(int(_el) + 1, _cap), _cap)
        if self.game._bench_running:
            # ⚠️⚠️ **渲染窗口那 25 秒一个字都不显示**: 不写标签 ⇒ 不会在测 1%Low 的窗口里
            #    造出一次文字重建, 也就**不需要为它预烘**。
            if not getattr(self, "_phys_started", False):
                return None
            # ⚠️ 物理段的尺子是 **CPU 秒**(波 1 的循环条件 `warmup_cpu_sec` / `sample_cpu_sec`
            #    就是 CPU 秒) —— 拿墙钟当分子会重演"面板顶到顶了、活儿还剩两成"那个 bug。
            #    ⚠️ 代价: 样本之间的 `gap_sec`(5 秒 × 4 次)不烧 CPU ⇒ 那几秒数字不动。
            #       那是**事实**(那几秒确实没在算), 不是卡住。
            _dt = float(_PHYS_TOTAL_SEC[0]) or 1.0
            # ⚠️ 分子 = CPU 秒 + **实际睡掉的秒** + **正在进行的那段等待**(按墙钟实时算) ——
            #    等待那几秒也是玩家在等, 不算就会"每秒不更新"。
            _w0 = float(_PHYS_WAIT_T0[0])
            _wait = max(0.0, time.time() - _w0) if _w0 > 0 else 0.0
            _dd = float(_PHYS_PROG[0]) + float(_PHYS_SLEPT[0]) + _wait
            # ⚠️⚠️ 分母取 `ceil(总秒数)`: 总秒数是 **44.5** ⇒ `ceil` = **45**(玩家要的数),
            #    `int()` 会给 44 —— 那正是"显示 44/44 却还剩半秒"的由来。
            #    ⚠️ 变量名用 `_pcap`/`_pv`(不叫 `_cap`): 上面高压分支里已有一个 `_cap`,
            #       同名会在读代码时误导 —— 两处是**两把不同的尺子**。
            _pcap = max(1, int(math.ceil(_dt)))
            _pv = min(int(_dd) + 1, _pcap)
            # ⚠️ 文案是玩家逐字定稿的: `物理演算第x/45秒` —— **不带空格**, 与黑屏上另一行
            #    `CPU高压测试 d/360秒` 对齐。
            return "物理演算第%d/%d秒" % (_pv, _pcap)
        return None

    def _prog_tick(self, dt=0):
        """主线程轮询进度。**只在文案真变了才写标签** —— 每写一次都是一次文字重排。"""
        try:
            _t = self._prog_text()
        except Exception:
            _t = None
        if not _t or _t == getattr(self, "_prog_last", None):
            return
        self._prog_last = _t
        try:
            _set_label_text(self.status_lbl, _t)
        except Exception:
            pass
        # ⚠️ **同步刷黑屏上的白字**: 它是画在 `RootWidget.canvas.after` 里的 **CoreLabel 纹理,
        #    不是控件** —— 黑屏挂在同一层且盖住整棵子树, 所以白字只能自己画一层。
        #    ⚠️ 波 1 与波 2 **共用这一个 tick**, 别在各自的分支里再抄一份。
        try:
            self._set_bench_msg(_t)
        except Exception:
            pass

    def _prog_start(self):
        self._prog_last = None
        try:
            self._prog_ev = Clock.schedule_interval(self._prog_tick, 0.25)
        except Exception:
            self._prog_ev = None

    def _prog_stop(self):
        try:
            if getattr(self, "_prog_ev", None) is not None:
                self._prog_ev.cancel()
        except Exception:
            pass
        self._prog_ev = None

    # ---------------------------------------------------------------- 高压测试
    # ⚠️ **与"性能测试"分开的独立入口**。两者问的问题不同, 时长也差一个量级:
    #      · 性能测试 = 短、**带间隔**、测**峰值**(可比);  约 34 秒
    #      · 高压测试 = 长、**一秒不停**、测**衰减**;      `SOC_SUSTAIN_WALL_SEC` 秒
    #    ⚠️ 两者互斥: 一个在跑的时候另一个不许进。
    def _start_hp_test(self):
        if getattr(self, "_hp_running", False) or self.game._bench_running:
            return
        self._hp_running = True
        # ⚠️ 状态栏要先存一份再改, 跑完还回去 —— 与 `_bench_saved_status` 同一个写法。
        self._hp_saved_status = self.status_lbl.text
        # ⚠️ **墙钟起点先归零, 由工作线程真正开跑那一刻再盖章**(见 `_run_hp_test`):
        #    中间还隔着 `_wait_idle_then_hp` 等球落地那一段, 盖在这儿会把等待也算成测试时间。
        self._hp_wall0 = 0.0
        self._prog_start()
        # ⚠️ **黑屏与白字不在这里建** —— 本函数可能跑在**工作线程**上, 而它们要碰 Kivy 的
        #    canvas / CoreLabel。**在调用方 `_wait_idle_then_hp`(主线程)那里建。**
        _set_label_text(self.status_lbl, "CPU高压测试第1/%d秒" % int(SOC_SUSTAIN_WALL_SEC))
        # ⚠️ 走 `game._controls`, **不是** `self._set_controls_enabled` —— 后者只是这条链的
        #    **界面那一半**(染色/标签), 状态位在 `Game`。老版那一下是同一个方法(置位 + 染色一起),
        #    而新版的状态位由 `on_touch_down_allowed()`(`_restyle_buttons` 的置灰)读
        #    ⇒ 只调 UI 那一半的话, 高压测试那几十秒 HUD **不上锁也不变暗**, 玩家能直接点
        #    重置/投注档/发射(实测: 位恒为 True、发射键一个字节都没变)。
        #    ⚠️ 老版对应点: main.py 的 `_start_hp_test` 里那句 `_set_controls_enabled(False)`。
        self._act(self.game._controls, False)
        self._wait_idle_then_hp()

    def _wait_idle_then_hp(self, dt=0):
        """等球落地(主线程空闲)再起 —— 与 `_wait_idle_then_bench` 同一个理由: 别抢 CPU。"""
        if self.game.state == "ready":
            # ⚠️⚠️ **黑屏与白字在主线程建**(与波 1 同一条规矩, 见 `_wait_idle_then_bench`):
            #    `_run_hp_test` 是工作线程, 而这两个碰 Kivy 的 canvas / CoreLabel
            #    —— 跨线程做会卡死。
            self._show_bench_dim()
            self._set_bench_msg("CPU高压测试第1/%d秒" % int(SOC_SUSTAIN_WALL_SEC))
            threading.Thread(target=self._run_hp_test, daemon=True).start()
        else:
            Clock.schedule_once(self._wait_idle_then_hp, 0.5)

    def _run_hp_test(self):
        # ⚠️ 工作线程: 只写属性, **不碰界面**(界面只能在 Clock 回调里动)。
        # ⚠️ 频率采样线程**在锁核之前就起**(见 `_freq_sampler_start` 的 `win` 那段:
        #    新线程继承创建者的亲和性, 晚起会跟着被钉到性能核上、跟被测线程抢核)。
        #    ⇒ 这里先交出一个**空窗口**, 等墙钟 `_hp_wall0` 起来之后再填进去。
        _freq_win = [0.0, 0.0]
        _frq, _stop = _freq_sampler_start(_freq_win)
        # 电池温度与 CPU 频率用同一个 `[1,359]` 墙钟窗口, 但独立按 1Hz 绝对时刻采集;
        # 不与 sysfs 读取耗时绑在一起。同样在锁核前起线程, 避免新线程继承跑分核亲和性。
        _battery_win = [0.0, 0.0]
        # ⚠️ 返回**三**个值 —— 第三个是功率收集器(温度 1Hz + 功率 5Hz 在同一个线程、
        #    同一个绝对网格上采)。
        _battery_values, _battery_stop, _pwr = _battery_sampler_start(_battery_win)
        # ⚠️⚠️ **波 2 全程把帧率按到 `_BENCH_FPS_FORCE`**(与波 1 同一条规矩)。
        _hp_render_fps = 0.0          # ⚠️ 先给初值: 下面抛异常时 finally 之后那一行不能 NameError
        _bench_fps_lock_on()
        # 高压测试也是独立 CPU 工作线程；锁在性能簇，才不会把温控衰减和迁到慢核混为一谈。
        _hp_aff_before = _bench_pin_fast_cpus()
        # 调度优先级: 亲和性管"允许跑哪些核", 这里管"抢不抢得到"。**只改这颗线程**, 不碰物理。
        _hp_prio_before = _bench_raise_thread_priority()
        try:
            # 锁核完成后才起墙钟；拓扑探测不计入高压测试时长。
            self._hp_wall0 = time.time()
            # ⚠️ **填"掐头去尾"的窗口**(`HP_FREQ_TRIM_SEC`)。必须在**这里**填、不能在线程起来
            #    时填: 这一段墙钟是**锁核/提权之后**才起的, 而这个窗口必须与它同源。
            #    ⚠️ 填之前 `win[0] == 0` ⇒ 采样线程**一个都不收** —— 上面那几十毫秒的
            #       锁核/提权阶段本来就不该算进"跑分时的频率"。
            _freq_win[0] = self._hp_wall0 + HP_FREQ_TRIM_SEC
            _freq_win[1] = self._hp_wall0 + SOC_SUSTAIN_WALL_SEC - HP_FREQ_TRIM_SEC
            _battery_win[0] = _freq_win[0]
            _battery_win[1] = _freq_win[1]
            _fc0, _wt0 = _FRAME_CALLS[0], time.time()
            _fl, _fr, _fps, _cpu = benchmark_sustained()
            _hp_render_fps = (_FRAME_CALLS[0] - _fc0) / max(1e-6, time.time() - _wt0)
        finally:
            # ⚠️ 门禁 L6 是照"finally **首行**"查 `_bench_fps_lock_off(` 配对的 ——
            #    `_hide_bench_dim` 那句不能插在它前面。
            _bench_fps_lock_off()
            _stop[0] = True
            _battery_stop[0] = True
            # ⚠️ **兜底撤黑屏**: 上面任何一步抛异常, `_hp_done` 就**永远不会被调度**
            #    (那行的 `Clock.schedule_once` 在异常路径上根本走不到) ⇒ 黑屏会永久留在屏幕上。
            #    `_hide_bench_dim` 幂等, 正常路径下 `_hp_done` 再调一次无害。
            Clock.schedule_once(lambda dt: self._hide_bench_dim(), 0)
            _bench_restore_thread_priority(_hp_prio_before)
            _bench_restore_cpu_affinity(_hp_aff_before)
        self._hp_cpu_pin = dict(_BENCH_CPU_PIN)
        self._hp_tid_prio = dict(_BENCH_TID_PRIO)
        self._hp_render_fps = _hp_render_fps
        # ⚠️⚠️ `_frq.sort()` 会**毁掉时间轴**, 而频率曲线要的正是时间轴
        #    ⇒ 排序**之前**先留一份原序副本。面板那三个数(平均/最低/最高)**仍然用排序后的**。
        self._hp_freq_series = [float(x) for x in _frq]
        self._hp_battery_series = [round(float(x), 1) for x in _battery_values]
        # ---- 功率 ----
        # ⚠️ 统计(`_pwr["stats"]`)是**采样线程退出前**算好的; 主线程这里只搬运,
        #    别再自己遍历一遍序列去算, 否则"谁在算什么"就有两份。
        # ⚠️⚠️ 真机实测 t=3.0~10.8s 的电流全是 `nan`(CPU 刚冲满载那几秒 Binder 被抢占,
        #    一格都没读到) ⇒ "固定剔前 N 秒"根本不管用: N 得随设备变。
        #    ⇒ 两步: ① 跳过**开头那段读不到的空段**; ② 再从第一个有效读数往后延
        #      `HP_PWR_SKIP_SEC` 秒(那段是 CPU 从 idle 冲到满载的过渡期)。
        #    ⚠️ 只处理**开头**: 中途偶发的 nan 是采样抖动, 该留着。
        _pw_all = list(_pwr["w"])
        _i0 = next((i for i, x in enumerate(_pw_all) if x is not None), 0)
        _sk = _i0 + int(round(HP_PWR_SKIP_SEC / max(1e-6, _pwr["dt"])))
        self._hp_pwr_skipped = _sk       # 导出的 txt 要写明剔了多少(玩家要看得见)
        self._hp_power_series = _pw_all[_sk:]                # 含 None, 定长 5Hz 网格
        self._hp_power_times = list(_pwr["t"])[_sk:]         # 与之一一对应(秒, 相对窗口起点)
        self._hp_battery_times = list(_pwr["bat_t"])   # 与 `_hp_battery_series` 一一对应
        self._hp_power_meta = {"src": _pwr["src"], "unit": _pwr["unit"],
                               "dt": _pwr["dt"], "stats": _pwr["stats"]}
        # ⚠️ **原始电流整数与电压序列也要留在内存里** —— 导出的 txt 要用它们,
        #    而这两条**没有落盘**(落盘的 `power_series` 已经是换算成 W 且 round 到 2 位)。
        self._hp_power_raw = list(_pwr["raw"])[_sk:]
        self._hp_power_mv = list(_pwr["mv"])[_sk:]
        # ⚠️ 面板那三个数**跟着当前粒度走** —— 否则会出现"曲线最高 3.81、面板写着 7.60"的
        #    自相矛盾。⚠️ 但 `wh` **不跟着**(见它自己那行的注释): 能量守恒要的是"均值 × 时长"。
        _gk = POWER_GRAIN_K.get(self.game.power_grain, 1)
        _gseq = (_med5(self._hp_power_series, _gk) if _gk > 1 else self._hp_power_series)
        _pwr_ok = [x for x in _gseq if x is not None]
        _raw_ok = [x for x in self._hp_power_series if x is not None]
        _mv_ok = [x for x in _pwr["mv"] if x is not None]
        _i_ok = [abs(float(x)) * (0.001 if _pwr["unit"] == "ma" else 1e-6)
                 for x in _pwr["raw"] if x]
        # 每瓦跑分的分子(平均步/秒) —— ⚠️ 口径必须与 `_hp_result_text` 里那个 `_avg`
        # **一致**(都只取 >0 的窗口), 否则同一次测试的面板上两行会自相矛盾。
        _w_ok = [int(x) for x in (_fps or []) if x > 0]
        _avg_sp = (sum(_w_ok) / float(len(_w_ok))) if _w_ok else None
        _frq.sort()
        _bat_sorted = sorted(self._hp_battery_series)
        self._hp_fps = list(_fps or [])
        self._hp_cpu = list(_cpu or [])
        # ⚠️ **高压的纯算术探针 + 归一化**(与波 1 同一条规矩): 高压跑分的绝对值同样会被
        #    "渲染抢内存"污染, 所以也要能归一化。
        #    归一化 = 首窗步/秒 ÷ 首窗探针 ×1e6 —— ⚠️ **取首窗**, 不是中位:
        #    高压要回答的是"**一上来能跑多快**"(峰值), 中位会被后面的温控衰减拉低。
        self._hp_speed = [round(float(x), 1) for x in _SUST_SPEED]
        _hv = [x for x in self._hp_fps if x > 0]
        _hs = [x for x in self._hp_speed if x > 0]
        self._hp_norm = (int(round(1000000.0 * _hv[0] / _hs[0]))
                         if (_hv and _hs and _hs[0] > 0) else 0)
        self._hp_freq = {"p50": _frq[len(_frq) // 2] if _frq else 0,
                         "min": _frq[0] if _frq else 0,
                         "max": _frq[-1] if _frq else 0, "n": len(_frq),
                         # 平均频率 —— 历史里那一列要的就是它(玩家:「CPU 平均频率」)。
                         "mean": int(sum(_frq) / len(_frq)) if _frq else 0}
        self._hp_battery = {
            "mean": (round(sum(_bat_sorted) / len(_bat_sorted), 1) if _bat_sorted else None),
            "min": (_bat_sorted[0] if _bat_sorted else None),
            "max": (_bat_sorted[-1] if _bat_sorted else None),
            "n": len(_bat_sorted),
            # ---- 功率 / 电压 / 电流 / 耗电 ----------------------
            # ⚠️ 功率序列里有 None(那一格没读到) ⇒ 统计一律**先过滤 None**;
            #    一个有效值都没有时全部是 None —— 渲染端据此**整行不印**,
            #    而不是印成"没采到"(那是另一件事)。
            "power_mean": (round(sum(_pwr_ok) / len(_pwr_ok), 2) if _pwr_ok else None),
            "power_min": (min(_pwr_ok) if _pwr_ok else None),
            "power_max": (max(_pwr_ok) if _pwr_ok else None),
            "power_n": len(_pwr_ok),
            "power_dt": _pwr["dt"],
            "power_src": _pwr["src"],
            "power_unit": _pwr["unit"],
            "power_stats": _pwr["stats"],
            # 测试期间**多数格**在充电 ⇒ 上面那些功率读数是"充入"而不是"耗电",
            # 面板要据此加一句说明(否则"满载 6 分钟才 2 瓦"会被读成省电)。
            "power_plugged": (sum(1 for x in _pwr["plugged"] if x)
                              > len(_pwr["plugged"]) * 0.5),
            # 电压来自 sticky 广播(mV), 电流原始值按 `power_unit` 折算成安培。
            "volt_mean": (round(sum(_mv_ok) / len(_mv_ok) / 1000.0, 2) if _mv_ok else None),
            "volt_min": (round(min(_mv_ok) / 1000.0, 2) if _mv_ok else None),
            "volt_max": (round(max(_mv_ok) / 1000.0, 2) if _mv_ok else None),
            "amp_mean": (round(sum(_i_ok) / len(_i_ok), 2) if _i_ok else None),
            "amp_min": (round(min(_i_ok), 2) if _i_ok else None),
            "amp_max": (round(max(_i_ok), 2) if _i_ok else None),
            # 总耗电 = Σ(P × dt) —— 功率是 5Hz **定长网格**, 所以 dt 就是格宽,
            # 缺失格(None)已经在 `_pwr_ok` 里滤掉, 不会把缺口算成 0 瓦。
            # ⚠️ `wh` **始终用原始序列 + 按有效格外推**: ① 能量守恒要的是"**均值** × 时长",
            #    换粒度会让它偏(中位数不是均值); ② 顺带补上原来的**缺格偏差** ——
            #    旧式 `sum(有效格) × dt` 等价于"把缺格记成 0 瓦", 实测一台机器缺 40/1775
            #    ⇒ wh 偏低 **2.25%**, 比整根尖峰的影响(0.15%)大 **6.6 倍**。
            "wh": (round((sum(_raw_ok) / len(_raw_ok))
                         * (len(_pwr["w"]) * _pwr["dt"]) / 3600.0, 3)
                   if _raw_ok else None),
            # ---- 每瓦跑分 ----
            # 口径 = **全程平均步/秒 ÷ 全程平均功率**。
            # ⚠️ 用**全程平均**而不是首窗峰值 —— 压力测试关心的是"持续能效", 首窗那几秒
            #    还没热起来, 拿它比会把每台机器都高估。
            # ⚠️ 存进记录才有意义: 这个指标是拿来**跨设备对比**的。
            "ppw": (int(round(_avg_sp / (sum(_pwr_ok) / float(len(_pwr_ok)))))
                    if (_avg_sp and _pwr_ok) else None),
            # 热限制等级(Android 10+; 不需要权限)。⚠️ 它是**收尾时刻的一个快照**, 不是全程
            #    曲线; 不支持的设备恒为 0, 和"真没热限制"分不出来 ⇒ 只作参考, 不能单独当结论。
            "thermal": _thermal_status(),
            # 这台设备上**读得到的 thermal zone**(大概率是空表, 见 `_thermal_probe`)。
            # 存它是为了下次翻日志时能回答"当初到底是没权限还是没这个 zone"。
            "zones": _thermal_probe(),
        }
        Clock.schedule_once(lambda dt: self._hp_done(), 0)

    def _hp_stats(self):
        """-> (首, 末, 最低, 中位, 降幅%); 没数据返回 None。"""
        v = [x for x in (getattr(self, "_hp_fps", None) or []) if x > 0]
        if not v:
            return None
        sv = sorted(v)
        _d = 100.0 * (v[0] - v[-1]) / v[0] if v[0] > 0 else 0.0
        return v[0], v[-1], sv[0], sv[len(sv) // 2], _d

    def _hp_freq_line(self):
        """结果弹窗里那行 CPU 频率。

        ⚠️ **口径取「平均」, 不取「中位」**: 频率采样是**双峰**的 —— 应用大部分时间在等
           vsync, 调频器把核压在最低档, 满载窗口才冲到睿频。**中位数必然落在其中一个峰上**,
           报出来要么像"全程低频"要么像"全程满血", 两个都不代表这段测试; 而**平均值**对应的
           是平均功耗/发热 —— 正是这个高压测试想回答的问题。
        ⚠️ 中位照旧印在**详情**的「频率分布」那一行, 也照旧存在记录里(`freq_p50`), 没有删。
        ⚠️ 文案长度刻意与改前**一样**(「中位」→「平均」, 都是两字), 不动弹窗排版。
        """
        _f = getattr(self, "_hp_freq", None) or {}
        if _f.get("mean"):
            return ("平均 %dMHz（%d到%d）"
                    % (_f["mean"], _f["min"], _f["max"]))
        return "没采到（非安卓 / 读不到 sysfs）"

    def _hp_cpu_pin_line(self):
        """高压结果中如实显示**跑分只在哪几个核上跑**。

        ⚠️ 面板上**先说人话**; 原始数字/核号留在括号里 —— 它们是"系统到底答没答应"的
           唯一硬证据, 删了就没法诊断。
        ⚠️ **非安卓返回空串**, 由 `_hp_summary_text` 过滤掉: PC 上永远是「未锁定（非安卓）」
           —— 那是**恒定值**, 而这块面板的成文规则是"有唯一预期值的, 只在偏离时才有信息"。
        """
        if platform != "android":
            return ""
        _pin = getattr(self, "_hp_cpu_pin", None) or {}
        if _pin.get("pinned"):
            _cpus = _pin.get("actual", _pin.get("target", [])) or []
            # 先说人话, 核号跟括号里(诊断要看的就是它)。
            return ("跑分只跑这几个核：" + "、".join("cpu%d" % int(_cpu) for _cpu in _cpus))
        return "跑分没有固定用哪几个核（%s）" % (_pin.get("reason", "未执行") or "未知原因")

    def _hp_tid_prio_line(self):
        """如实显示跑分线程的**调度优先级**是否真的被系统接受（读回验证，不印假成功）。

        `_BENCH_TID_PRIO` 由 `_bench_raise_thread_priority` 填: 只有当
        `getThreadPriority(0)` 读回**负数**时才算生效 —— 系统可以静默拒绝。
        ⚠️ **只说人话, 不印数字**: 原样印 `-4 → -8` 只有懂安卓线程优先级的人看得懂
           (那是 `Process.setThreadPriority` 的编号: -4 = 跟画面渲染同级, -8 = 比它更高)。
           真要看这个数, 日志里照旧有(`_bench_log` 印的「物理跑分工作线程优先级」)。
        ⚠️ 同一批要求还点名: 「已提权」/「没提权」后面那个**全角冒号换成全角逗号**
           (两行**一起**改 —— 同一种句式在一屏里留两种标点, 正是被打回的那种"排版送把柄")。
        """
        if platform != "android":
            return ""
        _pr = getattr(self, "_hp_tid_prio", None) or {}
        if _pr.get("raised"):
            return "跑分线程已提权，比画面渲染更优先"
        return "跑分线程没提权，可能被画面抢 CPU（%s）" % (
            _pr.get("reason", "未执行") or "未知原因")

    def _hp_result_text(self, d, opt_lines=()):
        """CPU 高压的**成绩正文** —— 结果弹窗与历史「详情」**共用这一份**。

        ⚠️ 详情原来有自己的「成绩 / 频率 / 过程」三段版式, 与结果弹窗**两套说法**(同一个数
           一个写「平均 A / 最低 B」、另一个写「最低 B，平均 A」)。两处各写一份**迟早脱钩**
           ⇒ 合成这一份, 两边都调它。

        ⚠️ 入参 `d` 用**记录里的字段名**(`hp_history` 那套)。现场那条路先用同样的键组一个
           dict 再传进来 ⇒ 「刚跑完」与「翻历史」走**同一条渲染路径**。
        ⚠️ `opt_lines` 是**只在现场才有**的两行(`_hp_cpu_pin_line` / `_hp_tid_prio_line`
           —— 那是 android 运行时状态, 记录里没存) ⇒ 详情传空。
        ⚠️ 缺字段一律印「—」, **绝不回填**(老记录没有 `mad` / `windows` 之类)。
        """
        _w = [int(x) for x in (d.get('windows') or []) if x > 0]
        _sec = float(d.get('sec') or SOC_SUSTAIN_WALL_SEC)
        _f, _l, _mn = d.get('first'), d.get('last'), d.get('min')
        _decay = float(d.get('decay') or 0.0)
        _avg = int(round(sum(_w) / float(len(_w)))) if _w else None
        _mc = _mad_coef(_w)
        # ⚠️ 抽点间隔 = **每 `HP_SAMPLE_SEC` 秒一个**(由"固定抽 20 个"改成**按时间**抽:
        #    一局 360 秒 ⇒ 36 个点)。
        # ⚠️⚠️ 取的是每段的**中点**, 不是段首: 10 秒一段、共 36 段, 每段取它的正中那一刻
        #    (5, 15, …, 355)。段首取值会让第一个数落在 t=0(实测偏 +1.6%), 中点是无偏的。
        #    ⚠️ 窗口**不是**整 1 秒一个(实测 321~331 个窗口摊在 360 秒上 ≈ 1.1 秒/窗) ⇒
        #       必须按 `秒数 × 窗口数 / 总秒数` 折算, 不能拿"每 10 个窗口"当 10 秒。
        #    ⚠️ `_sec` 取记录里的(老记录可能是 300 秒口径), 别拿当前常量硬套。
        # ⚠️⚠️ `_w` 可能是**空的**(老记录根本没存 `windows`) ⇒ 那种情况必须让 `_idx` 保持空、
        #    由下面印「—」。**别把 `_nseg` 写成 `max(1, ...)` 了事** —— 段数封底 1 之后,
        #    循环仍会跑一次, 而 `min(len(_w)-1, ...) = -1`、`max(0, -1) = 0` ⇒ `_w[0]`
        #    直接 IndexError。
        _idx = []
        if _w:
            _nseg = max(1, int(_sec // HP_SAMPLE_SEC))
            for _k in range(_nseg):
                _tk = (_k + 0.5) * HP_SAMPLE_SEC             # 段中点: 5, 15, …, 355
                _i = int(round(_tk * len(_w) / max(1.0, _sec)))
                _idx.append(max(0, min(len(_w) - 1, _i)))
        _samples = " / ".join("%d" % _w[_i] for _i in _idx) if _idx else '—'
        # 频率行: 口径取**平均**(与 `_hp_freq_line` 一致)。
        _fm = int(d.get('freq_mean', 0) or 0)
        if _fm > 0:
            _freq = ("平均 %dMHz（%d到%d）"
                     % (_fm, int(d.get('freq_min', 0) or 0),
                        int(d.get('freq_max', 0) or 0)))
        else:
            _freq = "没采到（非安卓 / 读不到 sysfs）"
        _bm = d.get('battery_mean')
        if _bm is not None and int(d.get('battery_n', 0) or 0) > 0:
            # ⚠️ 尾巴上「，N 个采样」删掉了(采样数在记录 JSON 里照旧存着)。
            # ⚠️ 「最低xx/最高xx」简化为「（x到y）」: 原写法在 360dp 上会**折行**。
            #    ⚠️ 用的是**「到」**(U+5230) 不是连字符 —— 玩家点名的写法。
            _battery = ("平均 %.1f度（%.1f到%.1f）"
                        % (float(_bm), float(d.get('battery_min', _bm)),
                           float(d.get('battery_max', _bm))))
        else:
            _battery = "没采到（非安卓 / 系统未提供）"
        # ---- 电池功率 / 电压·电流 / 能效跑分 ----
        # ⚠️⚠️ **老记录的判据是「键在不在」, 不是「值是不是 None」** —— 老存档里根本没有
        #    `power_*`, 那种情况**整块不印**; 印成"没采到"就是把「当时没采集」和
        #    「这台采不到」混成同一个词。
        _plines = []
        _pm = d.get('power_mean')
        if _pm is not None:
            _pt = ("电池功率：平均 %.2fW（%.2f到%.2f）"
                   % (float(_pm), float(d.get('power_min', _pm)),
                      float(d.get('power_max', _pm))))
            # ⚠️ 测试期间插着电 ⇒ 采到的是**充入**功率, 不是耗电。必须说清, 否则
            #    "满载跑 6 分钟才 2 瓦"会被读成这台设备很省电 —— 那是反的。
            if d.get('power_plugged'):
                _pt += "　⚠ 测试期间在充电，这是充入功率"
            _plines.append(_pt)
        elif d.get('power_src') == 'zero':
            # 与"读不到"**必须分开说**: 这条 API 在、只是恒返回 0 ⇒ 这台大概率没有 fuel gauge。
            _plines.append("电池功率：接口在、但恒为 0（这台大概率没有 fuel gauge）")
        elif 'power_mean' in d:
            _plines.append("电池功率：没采到（非安卓 / 系统未提供电流）")
        # ⚠️ **电压/电流那一行从面板撤掉了**(采集与落盘照旧: `volt_*` / `amp_*` 还在记录 JSON
        #    里, 只是不显示)。
        # 能效跑分 = 平均步/秒 ÷ 平均功率(全程)。
        # ⚠️ 取**全程平均**而不是首窗峰值: 压力测试关心的是**持续能效**, 首窗那几秒还没热起来,
        #    拿它比会把所有机器都高估。
        # ⚠️ 优先读记录里的 `ppw`(口径在 `_run_hp_test` 里算好存下的), 老记录没有才现算
        #    —— 两处都算会在"存的"和"印的"之间留一个静默分叉。
        # ⚠️⚠️ **面板上不能出现「瓦」字** —— 项目字体 `fonts/NotoSansSC-Medium.otf` 是**子集**,
        #    `瓦`(U+74E6) **不在里面**, 渲染成豆腐块。⇒ 写法是「**每W跑分**」(W 是 ASCII)。
        #    加新文案前先查字形表(`tests/font_check.py`)。
        _ppw = d.get('ppw')
        if _ppw is None and _pm and _avg:
            _ppw = int(round(_avg / float(_pm)))
        if _ppw is not None:
            # ⚠️ 标签里的「W」已经说明了单位 ⇒ 数值后面**不再重复写「·W」**。
            _plines.append("每W跑分：%d 步/秒" % int(_ppw))
        # ⚠️ 「总耗电」与「电流刷新」两行**从面板撤掉**(采集与落盘照旧: `wh` / `power_stats`
        #    还在记录 JSON 里) —— 撤的只是显示。
        _pwr_txt = "".join(chr(10) + x for x in _plines)
        # ⚠️⚠️ **"热限制等级 / 本机 thermal zone"那两行从界面上删掉了**: 用词对看结果的人
        #    就是噪音, 而它回答的问题**已经有结论了**(真机实测 32 个 thermal zone 里一个
        #    电池的都没有 ⇒ "更细的温度源"这条走不通)。
        #    ⚠️ **采集本身留着**(`_hp_battery` 的 `thermal` / `zones`) —— 只是不显示。
        # ⚠️⚠️ **老记录根本没有这两个键** ⇒ **不能印成**"读不到(设备不支持 Thermal HAL)":
        #    "当时没采集"和"这台读不到"是**两件不同的事**, 印同一个词就是假结论。
        #    ⇒ 老记录**整段不印**。判据用"**键在不在**", 不用"值是不是 None"。
        _o = [x for x in (opt_lines or ()) if x]
        _n = chr(10)
        return (str(d.get('head', '') or '') + _n
                # ⚠️ 与历史详情、空态那句**用同一个说法**: 「连续高压测试 N 秒」, 全工程只此一种。
                + "连续高压测试 %d 秒" % int(_sec) + _n
                + "".join(x + _n for x in _o)
                # ⚠️ 末尾更高时 `decay` 是**负数**, 直接印就成了"降 -2%"(读起来像降了负的)
                #    ⇒ 按符号换词、数字取绝对值。
                + "首 %s → 末 %s 步/秒（%s %.0f%%）" % (
                    '—' if _f is None else int(_f), '—' if _l is None else int(_l),
                    "增加" if _decay < 0 else "降", abs(_decay)) + _n
                + "最低 %s，平均 %s 步/秒" % (
                    '—' if _mn is None else int(_mn), '—' if _avg is None else _avg) + _n
                + ("平均差系数 %.2f%%" % _mc if _mc is not None else "平均差系数 无数据")
                # ⚠️ 这里**只留一个换行**(原来 `_n + _n` 那版是"成绩 / 频率 / 过程"三块版式
                #    带过来的空行, 合并成一份之后就是多余的)。
                + _n
                # ⚠️ 标签跟着抽点间隔走(同一个常量, 别再手抄一个 10 进来)。
                + "每%d秒连续采样成绩：" % int(HP_SAMPLE_SEC) + _samples + _n
                # ⚠️⚠️ 标签跟着**口径**走: 跑分线程是**锁核**的 ⇒ 这条频率现在只统计**跑分核
                #    那几个**; 没锁上(锁核失败/非安卓能读到 sysfs)才退回老口径「CPU 频率」
                #    = 全体取最大。**别把这两个标签合并** —— 它们是两个不同的数。
                + ("跑分核频率：" if d.get('freq_pinned') else "CPU 频率：") + _freq + _n
                + "电池温度：" + _battery + _pwr_txt)

    def _hp_summary_text(self):
        """结果弹窗的正文 —— 与历史「详情」**共用** `_hp_result_text`。

        ⚠️ 现场这条路把运行时状态**按记录的字段名**组一个 dict 再传 —— 这样"刚跑完"与
           "翻历史"就是同一条渲染路径, 不会再出现"同一个数两种说法"。
        """
        st = self._hp_stats()
        if st is None:
            return ""
        _f, _l, _lo, _mid, _d = st
        _fr = getattr(self, "_hp_freq", None) or {}
        _bt = getattr(self, "_hp_battery", None) or {}
        _opt = [x for x in (self._hp_cpu_pin_line(), self._hp_tid_prio_line()) if x]
        return self._hp_result_text({
            'head': self._device_info(),
            'sec': SOC_SUSTAIN_WALL_SEC,
            'first': _f, 'last': _l, 'min': _lo, 'decay': _d,
            'windows': list(getattr(self, "_hp_fps", None) or []),
            'freq_mean': int(_fr.get('mean', 0) or 0),
            'freq_p50': int(_fr.get('p50', 0) or 0),
            'freq_min': int(_fr.get('min', 0) or 0),
            'freq_max': int(_fr.get('max', 0) or 0),
            'freq_n': int(_fr.get('n', 0) or 0),
            'battery_mean': _bt.get('mean'), 'battery_min': _bt.get('min'),
            'battery_max': _bt.get('max'), 'battery_n': int(_bt.get('n', 0) or 0),
            # ---- 功率那一族 -----------------------------------------------------
            # ⚠️⚠️ **键名必须与记录 JSON 里的逐字一致** —— 渲染端 `_hp_result_text`
            #    只认这些名字。这里漏搬任何一个, 真机上那一行就恒不显示(或恒印"没采到"),
            #    而且**跑分本身是成功的**, 不报错。
            'power_mean': _bt.get('power_mean'), 'power_min': _bt.get('power_min'),
            'power_max': _bt.get('power_max'), 'power_n': int(_bt.get('power_n', 0) or 0),
            'power_dt': _bt.get('power_dt'), 'power_src': _bt.get('power_src'),
            'power_unit': _bt.get('power_unit'), 'power_stats': _bt.get('power_stats'),
            'power_plugged': _bt.get('power_plugged'),
            'volt_mean': _bt.get('volt_mean'), 'volt_min': _bt.get('volt_min'),
            'volt_max': _bt.get('volt_max'),
            'amp_mean': _bt.get('amp_mean'), 'amp_min': _bt.get('amp_min'),
            'amp_max': _bt.get('amp_max'), 'wh': _bt.get('wh'),
            'ppw': _bt.get('ppw'),
            # ⚠️⚠️ **这两个键必须跟着搬**: 只把 `_hp_battery` 填好是没用的 —— 渲染端走的是
            #    **这个 dict**, 漏搬的后果是那两行**恒印"读不到"**, 在读数成功的真机上也一样
            #    ⇒ 功能等于没上线, 而且印出来的正是"设备不支持 Thermal HAL"这种**假结论**。
            'thermal': _bt.get('thermal'), 'zones': _bt.get('zones'),
        }, _opt)

    def _hp_done(self):
        """高压测试结束: 弹结果弹窗。"""
        # ⚠️⚠️ **必须撤黑屏**: 黑屏是后来才启用的, 而 `_hide_bench_dim()` 当初**只加在了
        #    `_bench_done`(波 1)里**, 波 2 这条路径整个漏了 ⇒ 高压跑完黑屏留在屏幕上、
        #    那行白字停在最后一次进度, 看起来就是卡死。⚠️ 它是**幂等**的。
        self._hide_bench_dim()
        self._prog_stop()
        self._act(self.game._controls, True)          # 理由同 `_start_hp_test`: 状态位在 Game, 老版同一个方法
        _set_label_text(self.status_lbl,
                        getattr(self, "_hp_saved_status", "按住蓄力发射"))
        self._hp_running = False
        # ⚠️ **落一条历史**。存的东西要够"详细成绩 + CPU 平均频率"看 —— 逐窗曲线也存下
        #    (详情弹窗要画它)。存不成也不能影响结果弹窗, 所以整段 try 包着。
        try:
            _st2 = self._hp_stats()
            if _st2 is not None:
                _f2, _l2, _lo2, _mid2, _d2 = _st2
                _fr2 = getattr(self, "_hp_freq", None) or {}
                _bt2 = getattr(self, "_hp_battery", None) or {}
                _w2 = sorted(x for x in (getattr(self, "_hp_fps", None) or []) if x > 0)
                self.hp_history.append({
                    "time": time.strftime("%Y-%m-%d %H:%M"),
                    # ⚠️ **跑分口径: 平均数**(原来是中位数)。显示一律走 `_hp_score()` ——
                    #    新记录读这里, 旧记录拿 `windows` 现算。
                    #    ⚠️ `median` **照旧存着**(老记录要能读、以后要复盘), 只是**不再显示**。
                    #        **别顺手把它从记录里删掉。**
                    "mean": (int(round(sum(_w2) / float(len(_w2)))) if _w2 else None),
                    "median": int(_mid2),
                    "spread": (round(100.0 * (_w2[-1] - _w2[0]) / _mid2, 1)
                               if (_w2 and _mid2 > 0) else None),
                    # ⚠️ **平均差系数**: 高压评分的新口径, 取代「波动」= 平均差 ÷ 均值。
                    #    ⚠️ 旧字段 `spread` 与 `norm` **照旧存着**(老记录要能读、以后要复盘),
                    #        只是**不再显示**。**别顺手把它们从记录里删掉。**
                    "mad": (round(_mad_coef(_w2), 2) if _mad_coef(_w2) is not None else None),
                    "first": int(_f2), "last": int(_l2), "min": int(_lo2),
                    "decay": round(_d2, 1),
                    "freq_mean": int(_fr2.get("mean", 0) or 0),
                    "freq_p50": int(_fr2.get("p50", 0) or 0),
                    "freq_min": int(_fr2.get("min", 0) or 0),
                    "freq_max": int(_fr2.get("max", 0) or 0),
                    "freq_n": int(_fr2.get("n", 0) or 0),
                    "battery_mean": _bt2.get("mean"),
                    "battery_min": _bt2.get("min"),
                    "battery_max": _bt2.get("max"),
                    "battery_n": int(_bt2.get("n", 0) or 0),
                    # ---- 功率那一族 -------------------------------------------------
                    # ⚠️ 与 `_hp_summary_text`(现场那条路)**必须成对** —— 只搬一半的后果是
                    #    "现场对、翻历史错"(或反过来), 而且两边都不报错。
                    "power_mean": _bt2.get("power_mean"),
                    "power_min": _bt2.get("power_min"),
                    "power_max": _bt2.get("power_max"),
                    "power_n": int(_bt2.get("power_n", 0) or 0),
                    "power_dt": _bt2.get("power_dt"),
                    "power_src": _bt2.get("power_src"),
                    "power_unit": _bt2.get("power_unit"),
                    "power_stats": _bt2.get("power_stats"),
                    "power_plugged": _bt2.get("power_plugged"),
                    "volt_mean": _bt2.get("volt_mean"),
                    "volt_min": _bt2.get("volt_min"),
                    "volt_max": _bt2.get("volt_max"),
                    "amp_mean": _bt2.get("amp_mean"),
                    "amp_min": _bt2.get("amp_min"),
                    "amp_max": _bt2.get("amp_max"),
                    "wh": _bt2.get("wh"),
                    "ppw": _bt2.get("ppw"),
                    # ⚠️ 和现场那条路**必须成对**: 存了才能让"历史详情"印出当时的真实情况;
                    #    不存的话翻历史永远显示"读不到"(假结论)。
                    "thermal": _bt2.get("thermal"),
                    "zones": _bt2.get("zones"),
                    "sec": int(SOC_SUSTAIN_WALL_SEC),
                    "version": _app_version(),
                    "device": self._device_info(),
                    "windows": [int(x) for x in self._hp_fps],
                    # ⚠️ 频率曲线要的那条**按时间顺序**的序列(排序前的原序)。
                    "freq_series": [int(x) for x in (getattr(self, "_hp_freq_series", None) or [])],
                    "battery_series": [round(float(x), 1) for x in
                                       (getattr(self, "_hp_battery_series", None) or [])],
                    # ---- 曲线用的两条序列 -------------------------------------------
                    # `power_series` 是**定长 5Hz 网格**, 时刻可以由 `power_dt` 推出来
                    #   (t_i = i × dt), 所以不必另存时刻表; `None` = 那一格没读到。
                    # `battery_t` 则是**非等距**的(广播限流会让某些秒整点读失败),
                    #   所以温度那条必须连**时刻**一起存, 否则双轴图的横轴会对不上。
                    "power_series": [None if x is None else round(float(x), 2) for x in
                                     (getattr(self, "_hp_power_series", None) or [])],
                    "battery_t": [round(float(x), 1) for x in
                                  (getattr(self, "_hp_battery_times", None) or [])],
                    # ⚠️ 这条频率是"**只算了跑分核**"还是"全体取最大" ⇒ 面板换标签用。
                    "freq_pinned": bool((getattr(self, "_hp_cpu_pin", None) or {}).get("actual")),
                    # ⚠️ **归一化步/秒 + 逐窗探针**: 高压的绝对值同样会被"渲染抢内存"污染,
                    #    所以它也得能归一化 —— **跨设置/跨设备比高压成绩只能用这个数**。
                    #    `speed_runs` 逐窗存下来, 是为了事后判断"衰减是机器掉了还是测量抖了"。
                    #    ⚠️ **旧记录没有这两个字段** ⇒ 面板印「无数据」, 绝不回填。
                    "norm": int(getattr(self, "_hp_norm", 0) or 0),
                    "speed_runs": list(getattr(self, "_hp_speed", None) or []),
                })
                if len(self.hp_history) > 100:
                    self.hp_history.pop(0)
                self._save_hp_history()
        except Exception:
            pass
        try:
            _txt = self._hp_summary_text()
        except Exception:
            _txt = ""
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        # ⚠️ 标题改成 `CPU高压测试 v0.x.x`, 正文那行不再带版本。
        title_lbl = self._fit_line(Label(text=_soc_result_title(), bold=True, halign="center",
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 20)
        content.add_widget(title_lbl)
        body = Label(text=_txt or "没有采到数据", font_size="15sp", halign="left",
                     valign="top", color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None)
        self._auto_h(body, dp(160), dp(6))
        content.add_widget(body)
        # ⚠️ **没有「保存日志」按钮**(「删掉高压测试的写入文件功能」)。
        #    历史记录照旧落盘(JSON), 只是不再导出 txt。
        # ⚠️ 数据取**内存里这一轮**的 `self._hp_fps`(与下面那串采样同源)。
        _btnrow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6),
                            orientation="horizontal")
        _cw = [x for x in (getattr(self, "_hp_fps", None) or []) if x > 0]
        if len(_cw) >= 2:
            curve_btn = Button(text="成绩曲线", font_size="14sp", bold=True,
                               background_normal="", background_color=hex_rgb(COL_BTN) + (1,))
            curve_btn.bind(on_release=lambda *_: self._show_hp_curve(_cw))
            _btnrow.add_widget(curve_btn)
        # ⚠️ 数据取**内存里这一轮**的频率序列(`_hp_freq_series`, 是排序前的原序)。
        #    ⚠️ 位置就是**加进 BoxLayout 的先后**(横向 BoxLayout 按 add 顺序从左到右)
        #       ⇒ 这一段必须夹在成绩曲线与关闭**之间**, 挪前挪后都会改版面。
        _fw = [x for x in (getattr(self, "_hp_freq_series", None) or []) if x > 0]
        if len(_fw) >= 2:
            freq_btn = Button(text="频率曲线", font_size="14sp", bold=True,
                              background_normal="", background_color=hex_rgb(COL_BTN) + (1,))
            freq_btn.bind(on_release=lambda *_: self._show_hp_curve(
                _fw, title="高压CPU测试的频率曲线", unit="MHz", unit_name="采样"))
            _btnrow.add_widget(freq_btn)
        # ⚠️ **只画功率**(单轴): 温度仍然报, 但**只在面板文字**里。
        #    ⇒ **不新增按钮**: 这一行本来就有 4 个控件, 360dp 上每个只剩 ~68dp,
        #       第 5 个会直接溢出弹窗。没功率就**不建按钮**(没有可降级的东西)。
        # ⚠️ 闭包晚绑定: 这几份局部量**各起唯一名字**(`1` 后缀) —— 同一个函数里若有两个
        #    lambda 共用同名变量, 它们会看到对方最后一次赋的值。
        _pw1 = list(getattr(self, "_hp_power_series", None) or [])
        if len([x for x in _pw1 if x is not None]) >= 2:
            # ⚠️⚠️ 传下去的是**原始序列(含 None)**, 不能先过滤 —— 功率的时刻按**原下标 × dt**
            #    推, 过滤掉一个点会让它**后面所有点左移一格**(实测: 末点从 357.8s 变成 357.2s)。
            #    跳点由 `SpeedCurve` 自己负责。
            _dt1 = ((getattr(self, "_hp_power_meta", None) or {}).get("dt") or 0.2)
            power_btn = Button(text="功率曲线", font_size="14sp", bold=True,
                               background_normal="", background_color=hex_rgb(COL_SOC) + (1,))
            # 两个粒度: 「每帧」= 原样(保住每一根真实的爆发); 「每5秒」= 每 25 格取中位数
            # ⇒ 那些 2 秒宽的峰被平台拉回来(max 7.60→3.81)。
            # ⚠️ 实测「每3秒」**去不掉**: 两根挨着的峰合起来 10 格, 3 秒一组占 2/3。
            # ⚠️ 导出那份**始终是原始值**(`log_extra` 不动), 与看哪一档无关。
            _v1 = {"每帧": (list(_pw1), "采样"),
                   "每5秒": (_med5(_pw1, POWER_GRAIN_K["每5秒"]), "5秒段")}
            _ck1 = self.game.power_grain          # 打开时**用上次选的那一档**(粒度是保存的)
            power_btn.bind(on_release=lambda *_: self._show_hp_curve(
                _v1[_ck1][0], dt=_dt1, title="CPU高压测试的功率曲线",
                unit="W", unit_name="采样", value_decimals=2, flat_min_range=1.0,
                axis_unit="W", save_log=True, variants=_v1, cur_key=_ck1,
                log_extra={"pt": list(getattr(self, "_hp_power_times", None) or []),
                           "raw": list(getattr(self, "_hp_power_raw", None) or []),
                           "mv": list(getattr(self, "_hp_power_mv", None) or []),
                           "meta": getattr(self, "_hp_power_meta", None) or {},
                           "panel": getattr(self, "_hp_battery", None) or {}}))
            _btnrow.add_widget(power_btn)
        close_btn = Button(text="关闭", font_size="14sp", bold=True,
                           background_normal="", background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        _btnrow.add_widget(close_btn)
        content.add_widget(_btnrow)
        popup = self._popup(0.90, 460, title="", content=content,
                            auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_bench_menu(self):
        """弹珠发射模拟测试菜单：三行两列，测试、历史、信息与帧率设定各自成对。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        _ver = _app_version()
        _title = ('弹珠发射模拟测试 ' + _ver) if _ver else '弹珠发射模拟测试'
        title_lbl = self._fit_line(Label(text=_title, bold=True, halign='center',
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title_lbl)
        # ⚠️ 文案在 `_bench_menu_desc()` 里(抽出去是为了让测量脚本能量到**出货这一份**)。
        # ⚠️ 末句的「连压 N 分钟」走常量, 别再写成硬编码字面量。
        desc_lbl = Label(text=_bench_menu_desc(),
                         font_size='15sp', halign='left', valign='middle',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(170))
        # 说明是**多行正文** —— 只能用"高度跟着排版走"(缩字号会把整段一起缩小)。
        # 它原来定高 dp(170), 而排版后要 198px(400dp) / 242px(360dp) ⇒ 折出来的行被裁掉。
        # ⚠️ 末段那两行是**手写 \n 断的**, 别删 —— 交给 Kivy 自动折的话断点会落在"纯 Python"
        #    两边那个**空格**上, 于是断成两行(玩家报的"这个纯python执行的换行也很奇怪"就是它)。
        self._auto_h(desc_lbl, dp(120), dp(8))
        content.add_widget(desc_lbl)
        # ⚠️ **按系配色 + 按系排序**(**3 系, 同类同色系**):
        #      模拟(性能测试): 红系 `COL_FIRE` / CPU 高压: 琥珀系 `COL_SOC` / 信息: `COL_BTN`
        #    ⚠️⚠️ **一行一个颜色 —— 右边的按钮用左边那个的颜色**: 改之前是 6 个按钮 6 种颜色,
        #       玩家读成"颜色好乱"。代价是**同一行里分不出"行动"和"历史"** —— 玩家明确选的取舍。
        #    ⚠️ `COL_DARKRED` / `COL_SOC_DIM` / `COL_BTN_OFF` 在本文件别处仍在用(其它弹窗的
        #       取消/返回/关闭), **不要因为它们在这里不用了就删掉**。
        start_btn = Button(text='开始模拟测试', font_size='17sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_FIRE) + (1,),
                           size_hint_y=None, height=dp(52))
        hist_btn = Button(text='查看模拟历史', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_FIRE) + (1,),
                          size_hint_y=None, height=dp(52))
        hp_btn = Button(text='CPU高压测试', font_size='17sp', bold=True,
                        background_normal='', background_color=hex_rgb(COL_SOC) + (1,),
                        size_hint_y=None, height=dp(52))
        hph_btn = Button(text='高压测试历史', font_size='17sp', bold=True,
                         background_normal='', background_color=hex_rgb(COL_SOC) + (1,),
                         size_hint_y=None, height=dp(52))
        info_btn = Button(text='游戏信息', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                          size_hint_y=None, height=dp(52))
        cap_btn = Button(text='帧率上限设定', font_size='17sp', bold=True,
                         background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                         size_hint_y=None, height=dp(52))
        # ⚠️⚠️ **宽度 0.92 是算出来的, 不是拍的**: 说明那三条要用全角逗号, 第 2 条要 269px,
        #    而弹窗宽度按 `宽 × 屏宽dp − 56`(外壳 24 + 正文内边距 32)算可用宽 —— 360dp 上
        #    0.92 给 275px(余 6px)。本文件里另外两张历史面板早就用 0.92, 这一处改完就统一了。
        #    ⚠️ **改这一行必须同步改测量脚本里的可用宽** —— 那个式子是**手抄**的,
        #       不改的话测量还会按旧宽度算, 给出假绿。
        popup = self._popup(0.92, 520, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        start_btn.bind(on_release=lambda *_: (popup.dismiss(), self._start_bench_test()))
        hist_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_bench_history()))
        info_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_startup_info()))
        hp_btn.bind(on_release=lambda *_: (popup.dismiss(), self._start_hp_test()))
        hph_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_hp_history()))
        cap_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_fps_cap_settings()))
        for left, right in ((start_btn, hist_btn), (hp_btn, hph_btn), (info_btn, cap_btn)):
            row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
            row.add_widget(left)
            row.add_widget(right)
            content.add_widget(row)
        popup.open()
        self._popup_fit_content(popup, content)

    def _calc_no_audio_ms(self, _veil, _tot_ms=0.0):
        """「无音频加载累计耗时」= **不必等音频就绪的话, 最早什么时候能进游戏**(ms)。

        = `min(实际摘页时刻, max(加载页"演完"的实测时刻, 烘焙收工时刻))`
          `_tot_ms` = 实际摘页时刻(启动时钟, 调用方在同一次测量里传进来; 0 = 不夹)

        ⚠️ **两边的每一项都是实测的, 不是拿常量算的**: 加载页那一项 =
           `_LoadVeil._done_at_boot`(第一次 `t >= VEIL_TITLE_MIN_SEC` 的那一帧盖的章)
           —— 那一刻实际落在什么时候是"220ms **向上取整到下一帧**", 而帧长随设备/负载变
           ⇒ **每局都不一样**, 拿常量算会系统性偏小。
        ⚠️ 两项都躲不掉, 所以取 max: ① **加载页地板**要"完整淡入", 与音频无关;
           ② **烘焙收工** —— 冷启动必须先把音效现场合成出来(热启动则是读缓存, 很早)。
        ⚠️ **别**跟"总耗时 − 音效加载"混: 那个是烘焙线程自己的时长, 不含地板。
        ⚠️ 本函数算出来的东西**必须与"总耗时"同一个时钟**(都是启动时钟的绝对时刻),
           所以调用方把**实际摘页时刻**传进来, 这里 `min` 一手 ⇒ 恒有 总 ≥ 本值。
           为什么非要夹: 音频若很快就绪, 加载页会在"演完"之前就被摘掉, `_done_at_boot`
           压根没机会盖章 ⇒ 这里退回"首帧 + 常量", 而那个值可能**比真正摘页还晚**。
        出任何意外返回 0 —— 调用方会退回 `bake_ms`。
        """
        try:
            # ⚠️ **优先用实测**(加载页自己说的"我演完了"); 拿不到才退回"首帧 + 常量"。
            _vf = float(getattr(_veil, "_done_at_boot", 0.0) or 0.0)
            if _vf <= 0.0:
                _t0b = float(getattr(_veil, "_t0_boot", 0.0) or 0.0)
                if _t0b <= 0.0:
                    # ⚠️ 连"首帧"都没有 ⇒ **老实说不知道**(返回 0, 调用方会退回 `bake_ms`),
                    #    不许拿常量凑一个数出来 —— 那是编数。
                    return 0.0
                _vf = _t0b + VEIL_TITLE_MIN_SEC
            _bk = float(getattr(self.sfx, "_baked_at", 0.0) or 0.0) - _BOOT_T0
            _v = max(_vf, _bk) * 1000.0
            if _tot_ms > 0.0:
                _v = min(_v, _tot_ms)
            _boot_log("frame", "无音频加载累计耗时 %.0f ms（加载页演完 %.0f / 烘焙收工 %.0f%s）"
                      % (_v, _vf * 1000.0, _bk * 1000.0,
                         ("，夹在摘页 %.0f 之下" % _tot_ms) if _tot_ms > 0.0 else ""))
            return _v
        except Exception:
            return 0.0

    def _show_startup_info(self):
        """「启动信息」弹窗: 版本/制作日期 + 音频体检。

        音频体检是给「初次安装必然没声音」那个 bug 用的: 它的**所有候选原因在产物里长得
        一模一样**(静默 / 不抛异常 / 不留痕), 没有 adb 就只能靠这几行把真值摆出来 ——
        尤其"音效就绪"报的是**后端真的握着几个 sampleId**, 不是闸门放行了几个(病灶正是
        两者不等; 只报闸门会显示全绿, 那比不显示更有害)。
        ⚠️ 逐行分栏, **绝不并成一行**: 实测合并后 658px > 内容区 422px, 会折行而被定高标签
        裁掉, 玩家看到的是一句缺尾巴的话。
        ⚠️ 纯只读 —— 这个弹窗**不许**放任何会动音频栈或游戏状态的按钮(那是"绝不软锁"的前提)。"""
        # ⚠️ 页边距收一档: `padding 16->12` / `spacing 12->8`。
        #    ⚠⚠ **高度公式(`need`)必须同步改** —— 那里的 `dp(32)` 是 `2×padding`、
        #       `dp(12)` 是 `spacing`。只改布局不改公式, 弹窗会短一截、把内容裁掉尾巴。
        #    ⚠️ 只改**这一个**弹窗: 同样两个参数在跑分菜单 / 高压历史里也有, 那两处不动。
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        # 标题是 `跳跳的弹珠机v0.x.x` —— 版本号全工程只在这里出现一次, 正文那行只剩制作时刻。
        title_lbl = self._fit_line(Label(text=_startup_title(), bold=True, halign='center',
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title_lbl)
        rows = []
        # ⚠️ 这里原来印一行「游戏版本　v0.x.x」, 而上面那个标题就是同一个数 —— 同一屏同一个数
        #    印两遍。删掉它同时回收一行的高度预算(这块面板行数是有硬预算的)。
        #    ⚠️ 别再以"诊断时想要一个纯版本字段"为由加回来。
        try:
            _info = self._build_info()
        except Exception:
            _info = ""
        def _mk_lbl(_text, _align, _size='15sp', _h0=26, _markup=False):
            # ⚠️ 字号与另外两个列表弹窗(跑分历史/每轮次数)统一到 15sp —— 这三个弹窗长得
            #    几乎一样, 不该用三种字号。
            """自动撑高的行标签 —— **折行不再等于裁切**。

            ⚠️ 为什么必须是这个形状: 这块面板栽在"文字被裁掉"上两次了, 根因是**可用宽度取决于
            设备密度**: 桌面等效宽 540(可用 421px), 而手机密度下等效宽可能只有 360(可用 ~270px),
            同一条字符串在桌面上不折、在手机上折。所以不能靠"把字符串写短"来躲, 只能让行高跟着
            实际排版走。绑 width → 先让 Kivy 按可用宽度算出真正的 text_size(text_size 第二位给
            None 才自动换行), 再把 texture_size[1](排版后的真实高度)写回 height。"""
            lb = Label(text=_text, font_size=_size, halign=_align, valign='middle',
                       markup=_markup,          # ⚠️ 温度/功耗 那行要用 markup 给数字上金色
                       color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(_h0))
            lb.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
            lb.bind(texture_size=lambda w, ts: setattr(w, 'height', max(dp(_h0), ts[1] + dp(4))))
            return lb

        try:
            rows.extend(self.sfx.audio_detail())
        except Exception:
            pass
        if _info:                       # 版本/日期居中
            content.add_widget(_mk_lbl(_info, 'center'))
        for _ln in rows:                # 字段行一律左对齐(标签等宽 4 个汉字, 左对齐才排得成一列)
            content.add_widget(_mk_lbl(_ln, 'left'))
        # ⚠️ 实时「温度/功耗」行(每 0.5 秒刷新)。
        #    **读不到就整行不出现** —— 与 `audio_detail()` 里"不适用的行直接不出现"同一条规矩;
        #    PC 上 `_battery_snapshot()` 恒返回 None ⇒ 自然满足。
        _live = None
        _n_extra = 0
        try:
            _lt = _live_power_temp_line()
        except Exception:
            _lt = ""
        if _lt:
            _live = _mk_lbl(_lt, 'left', _markup=True)   # 数字是金色的
            content.add_widget(_live)
            _n_extra += 1
        # ⚠️ 实时「CPU 频率」块 —— **新版独有**(老版没有), 见 `_live_cpu_freq_line` 与
        #    `changelog/2026-09-19.md` 第 23 条。与上面那行**共用同一个 0.5 秒定时器**,
        #    不各开一个: 少一个 Clock 事件, 也让"关窗 unschedule"只有一处。
        #    ⚠️ 它是**多行** Label(每簇一行), 但 `_n_extra` 只当 1 个控件算 ——
        #       多出来的高度由开窗后的 `_popup_fit_content` 按真实排版兜住(见 1180 行那段注释)。
        #    ⚠️ 簇结构是静态的(cpuinfo_max_freq 不变) ⇒ **行数开窗后就固定**, 不会越长越高。
        _live_cpu = None
        try:
            _ct = _live_cpu_freq_line()
        except Exception:
            _ct = ""
        if _ct:
            _live_cpu = _mk_lbl(_ct, 'left', _markup=True)
            content.add_widget(_live_cpu)
            _n_extra += 1
        ok_btn = Button(text='确定', font_size='17sp', bold=True,
                        background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                        size_hint_y=None, height=dp(52))
        # 「重放冷启动」: 不丢存档地按需复现"初次安装那种局"(见 `_replay_cold_start`)。
        # 玩家提的 —— 那个 bug 一年犯一次、"关掉重开"就自愈, 想抓现场只能卸载重装,
        # 而卸载会清掉余额/轮次。它不新建 Sfx 对象, 所以不碰任何接线, 也不动游戏状态。
        # 「保存加载日志」: **不上屏**。
        #    ⚠️ 代码全留着 —— 它是"导出启动日志"的唯一入口, 藏了之后所有诊断都得靠别的路子。
        #    放回来: 在 `_btn_row` 那两个 add_widget 里加一句 `_btn_row.add_widget(log_btn)`,
        #    并把上面的 `n_btn` 改成 2(它会单独占一行)。
        # ⚠️ 它**不违反**这个弹窗的铁律(不许放会动音频栈或游戏状态的按钮):
        #    这个按钮**只写文件** —— 不碰 Sfx、不碰音频栈、不碰游戏状态, 连读都只读一次快照。
        log_btn = Button(text='保存加载日志', font_size='17sp', bold=True,
                         background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                         size_hint_y=None, height=dp(52))
        replay_btn = Button(text='重放冷启动', font_size='17sp', bold=True,
                            background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                            size_hint_y=None, height=dp(52))
        # ⚠️ 按钮区是**并排一行**, 左右顺序由 `_btn_row` 里的 add 顺序决定(重放左、确定右)。
        #    ⚠️ 并排之后每个按钮只剩约半宽 ⇒ 接进单行自适应(窄屏/大字号下自动缩字,
        #       而不是"盖到隔壁按钮上" —— Kivy 的 Button 不换行也不缩)。
        _btn_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height=dp(52), spacing=dp(8))
        self._install_fit(replay_btn, ok_btn)
        _btn_row.add_widget(replay_btn)
        _btn_row.add_widget(ok_btn)
        content.add_widget(_btn_row)
        # ⚠️ 高度必须**按内容算**: 实测弹窗内容区 = 弹窗高 − 44px(Kivy 标题栏, 即使 title=''
        #    也吃), 每行 38px(行高 26 + spacing 12)。写死高度的话加一行就会被裁掉尾巴。
        n_lbl = 1 + (1 if _info else 0) + len(rows) + _n_extra    # ⚠️ 那块面板有硬预算
        # ⚠️ `n_btn` 数的是**按钮行数**(不是按钮个数)。两个按钮现在并排一行 ⇒ 一行。
        #    放回「保存加载日志」(它单独占一行)时它得跟着 +1。
        n_btn = 1
        need = (dp(30) + dp(26) * (n_lbl - 1) + dp(52) * n_btn + dp(24)
                + dp(8) * (n_lbl + n_btn - 1))      # ⚠️ dp(24)=2×padding、dp(8)=spacing,
                                                    #    与上面那行 BoxLayout **必须成对改**
        popup = self._popup(0.84, need + dp(64), title='', content=content,
                            auto_dismiss=True, separator_height=0)
        # ⚠️ 实时行的定时器: **弹窗一关就必须 unschedule** —— 否则它会一直跑下去
        #    (每开一次面板再攒一个), 而它每 0.5 秒要 `registerReceiver` 一次(还要读 3 个
        #    sysfs 取 CPU 频率)。两条实时行**共用这一个 tick**, 所以这里只有一处 unschedule。
        if _live is not None or _live_cpu is not None:
            def _tick_live(_dt):
                if _live is not None:
                    try:
                        _t2 = _live_power_temp_line()
                        if _t2:
                            _live.text = _t2
                    except Exception:
                        pass
                if _live_cpu is not None:
                    try:
                        _c2 = _live_cpu_freq_line()
                        if _c2:
                            _live_cpu.text = _c2
                    except Exception:
                        pass
            _live_ev = Clock.schedule_interval(_tick_live, 0.5)

            def _stop_live(*_a):
                try:
                    Clock.unschedule(_live_ev)
                except Exception:
                    pass
            popup.bind(on_dismiss=_stop_live)
        ok_btn.bind(on_release=lambda *_: popup.dismiss())
        replay_btn.bind(on_release=lambda *_: (popup.dismiss(), self._replay_cold_start()))
        # ⚠️ 提示**不弹新弹窗**(这里已经在弹窗里了, 叠一个必然出岔子): 照抄 `_save_power_log`
        #    调用方那套 —— 把结果写进按钮文字, 3 秒后复原。
        def _on_save_log(*_a):
            try:
                _ok, _msg = self._save_startup_log()
            except Exception as _e:
                _ok, _msg = False, "保存失败: %r" % (_e,)
            try:
                log_btn.text = str(_msg)[:30]
                Clock.schedule_once(lambda _d: setattr(log_btn, 'text', '保存加载日志'), 3.0)
            except Exception:
                pass
            return _ok
        log_btn.bind(on_release=_on_save_log)
        popup.open()

        # 上面那个高度是**按单行估的**; 一旦有行折了(设备越窄越容易折), 内容就比弹窗高。
        # 所以开完再按**真实排版高度**对一次 —— 这样"折行"永远只让弹窗长高, 不会把内容顶出去。
        self._popup_fit_content(popup, content)

    def _show_replay_detail(self):
        """「重放冷启动」完成后点屏幕: 把**详细统计**摆出来(带确认按钮的独立窗口)。

        ⚠️ 加载页上只剩「跳跳的弹珠机」+ 底部一行「测试已经完成」, 那些数搬到这里。
        ⚠️ 内容仍然复用 `_replay_summary()`(`audio_detail()` 那一个真源), 不另写一套格式化。
        ⚠️ 整段 try/except + 给默认高度: 弹窗起不来也绝不能让玩家卡在加载页(项目红线)。"""
        try:
            content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
            head = self._fit_line(Label(text="重放冷启动 · 详细统计", bold=True,
                                        color=hex_rgb(COL_TEXT) + (1,),
                                        halign="center", valign="middle",
                                        size_hint_y=None, height=dp(34)), 19)
            content.add_widget(head)
            # ⚠️ 字号与「启动信息」面板统一(那边是 `_mk_lbl` 的 15sp), 顺带腾出 ~19px 宽度
            #    —— 那一行在窄机器上会换行。
            body = Label(text=self._replay_summary(), font_size="15sp",
                         color=hex_rgb(COL_SUB) + (1,),
                         halign="left", valign="top", size_hint_y=None, height=dp(26))
            # ⚠️ 行高按**真实排版**撑开: 手机上可用宽度更窄, 同一串字会折行 ——
            #    写死高度就会把折出来的第二行裁掉。
            body.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
            body.bind(texture_size=lambda w, ts: setattr(w, "height", max(dp(26), ts[1] + dp(4))))
            content.add_widget(body)
            ok_btn = Button(text='确定', font_size='17sp', bold=True,
                            background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                            size_hint_y=None, height=dp(52))
            content.add_widget(ok_btn)
            # ⚠️ 0.94: 该弹窗正文那行在窄机器上会换行(实测可用 245px 而整行要 289px),
            #    更宽 + 更小的字号一起把它压回一行。0.98 在隐藏返还率弹窗里已经在用。
            popup = self._popup(0.94, 420, title='', content=content,
                                auto_dismiss=True, separator_height=0)
            ok_btn.bind(on_release=lambda *_: popup.dismiss())
            popup.open()
            self._popup_fit_content(popup, content)

            # 开完再按真实排版高度对一次(与「启动信息」那块面板的做法一致: 先估高, 再校正)。
            def _refit(*_):
                try:
                    _vw, _vh = self._veq()
                    # ⚠️ 上限必须与 `_popup_fit_content` 的 0.96 **一致**: 这里写 0.92 会
                    #    **覆盖**它的结果 ⇒ 同一块弹窗被更矮的封顶管着。
                    popup.height = min(content.minimum_height + dp(64), _vh * 0.96)
                except Exception:
                    pass
            Clock.schedule_once(_refit, 0.06)
        except Exception:
            pass

    def _replay_cold_start(self):
        """**不丢存档**地重放一次冷启动 —— 按需复现「初次安装」那种局, 用来抓现场。

        那个 bug 一年犯一次、而且"关掉 app 再打开"就自愈, 想抓现场只能卸载重装 ——
        而卸载会清掉余额/轮次, 没人愿意为调试反复清自己的存档。
        这里做的是**原地重放**: 清掉音效缓存目录 + 把同一个 Sfx 对象的状态按回启动前, 再烘一次。
        ⚠️ 绝不新建 Sfx 对象: 新建会让 GameArea / WinPileFX 里持有的旧引用全部接错。
        ⚠️ 绝不软锁: 烘焙全程在后台线程; 失败也会置 baked, 而探针本身有 6 秒硬超时
           (见 Sfx._await_ready) —— 所以加载页一定会被 _frame 摘掉。
        ⚠️ 只在 state == "ready" 时允许: 球在飞的时候重烘 = 那一刻所有音效都播不出来。
        ⚠️ 它只是"近似"初装: 真初装还多两件事 —— app 目录刚解包(文件缓存是冷的)、进程刚启动
           (模块导入是冷的)。所以重放会比真初装快一些, 数字对上了不等于 bug 一定复现。"""
        try:
            if self.game.state != "ready":
                self.game_area.center_toast("先等这一发落定")
                return
            # ⚠️ **in-flight 闸门**: 重放全程不碰 `state`(所以上面那道拦不住第二次点击),
            #    而两次重放叠着跑会让两轮 probe_all 互相覆盖 ⇒ 日志与单价读到中间值。
            if getattr(self, "_replay_busy", False):
                self.game_area.center_toast("正在重放中")
                return
            sfx = self.sfx
            # ① **先把结果页建起来再动音频状态**。顺序是要紧的: 建页失败就直接返回, 绝不能先
            #    把 named 清掉 —— 那会留下一个"闸门是空的、也不会重烘"的**静默**状态,
            #    而这正是这块面板要抓的那种故障。
            host = getattr(self, "_load_veil_host", None) or self.parent
            if host is None:
                self.game_area.center_toast("重放失败：找不到挂载点")
                return
            # ⚠️ 耗时**起点取在这里**(玩家点下去那一刻), 不是线程启动那一刻 ——
            #    玩家感知的是"我点完到看见结果", 建页/清缓存/起线程都算在内。
            self._replay_busy = True
            self._replay_t0 = time.perf_counter()
            veil = _LoadVeil(text="正在重放冷启动…", size_hint=(1, 1))
            host.add_widget(veil)
            self._load_veil = veil
            self._replay_veil = veil          # _frame 靠它把这一页改成"停住等点击"
            # ② 再动音频状态
            import shutil
            shutil.rmtree(_sfx_cache_dir(), ignore_errors=True)   # 缓存整目录清掉 = 真·冷路径
            sfx.baked = False
            sfx._audio_ready = False
            sfx.cached = False
            sfx.ready_ms = 0.0
            # ⚠️ `bake_ms` **也要清** —— 它是"上次烘焙花了多久"的残值, 不清的话万一在烘焙
            #    完成前读到它, 报出来的就是上一次的数。
            sfx.bake_ms = 0.0
            # ⚠️ 这两个也是"上一局"的残值 —— 面板的总耗时读 `_total_ms`(摘页那刻的启动时钟),
            #    不清的话重放后会印上一次的数; 而重放这一页走的是 `_hold` 分支,
            #    **不会**经过正常摘页那一段(所以没人会替它更新)。`_no_audio_ms` 同理。
            sfx._total_ms = 0.0
            sfx._no_audio_ms = 0.0
            sfx.named.clear()
            sfx._failed = []
            try:
                sfx._last.clear()
            except Exception:
                pass
            # ③ 后台重烘(先把「实验档」换到下一档, 并把两个池整个换新)
            try:
                _li = int(getattr(self, "_probe_ladder_i", 0))
                _mode, _extra = ARM_LADDER[_li % len(ARM_LADDER)]
                # ⚠️⚠️ 档位曾经写在 **RootWidget** 上, 而读它们的是 **`_SoundPoolOut`**
                #    —— 两个对象! ⇒ 四档全走同一档, 日志却写着「档位 4/4」
                #    ⇒ 四组一模一样的数据会被当成结论。
                #    ⇒ 档位一律写到**后端**, 并在 `_await_ready` 里从后端读。
                _bo = getattr(getattr(self, "sfx", None), "out", None)
                if _bo is not None:
                    _bo._gate_mode = _mode
                    _bo._arm_extra = str(_extra)
                self._probe_ladder_i = _li + 1
                _boot_log("probe", "重放实验档 %d/%d: %s%s"
                          % (_li % len(ARM_LADDER) + 1, len(ARM_LADDER), _mode,
                             (" + " + _extra) if _extra else ""))
            except Exception:
                pass
            # ⚠️ 诊断累加器**每一档都从头起**: `_PROBE_TRACE`/`_PROBE_COST` 是模块级、
            #    只增不减的列表 ⇒ 不清的话后一档的启动日志会混进前一档的数
            #    (而"四档对照"的全部意义就是它们互不污染)。
            try:
                _PROBE_TRACE[:] = []
                _PROBE_COST[0] = 0.0
                _PROBE_COST[1] = 0
            except Exception:
                pass
            # ⚠️ 把两个池整个换新 + 清空应到清单。必须在 `sfx._bake` **之前**:
            #    冷烘焙会 prime 全部 101 个样本, 那一步才是"重建"。
            try:
                sfx._arm_rows = []
                sfx._gate_missing_name = ""       # 点名结果每一档重来
                sfx._gate_rebuilt_once = False    # "重建池"的一次性闸门: 重放本来就是重来一遍
                sfx.out.replay_reset()
            except Exception:
                pass
            import threading
            threading.Thread(target=sfx._bake, daemon=True).start()
        except Exception as exc:
            # ⚠️ **绝不静默**: 上一次这里一句都不说, 玩家点下去"完全没有反馈", 而音频状态其实
            #    已经被清掉了。现在起不来就立刻放行 + 把原因说出来(toast)。红线是绝不软锁。
            try:
                sfx.baked = True
                sfx._audio_ready = True
            except Exception:
                pass
            self._finish_replay_veil()
            try:
                self.game_area.center_toast("重放失败：%s" % exc)
            except Exception:
                pass

    def _probe_verdict(self):
        """从探针逐轮的进度采样里**自动判定**那个关键未知(每样本定价 or 按字节定价)。

        为什么不用额外做对照实验: 探针按 `_ids` 的**插入顺序**扫(先合成音、再语音),
        所以每轮那个「扫 N 个」**就是"从队首起连续就绪的个数"** —— 配上轮次时刻,
        这就是一条现成的到达曲线。两段的字节数比是 7.8 倍、样本数比只有 1.5 倍, 两种定价
        给出的预测拉得很开, 判据干净。

        ⚠️ 数据不足(<2 个可切分的点)**明说"不足以判定"** —— 绝不给假结论。
        ⚠️ 纯读数: 不参与任何判据, 算不出来就返回空表。
        """
        out = []
        try:
            tr = list(_PROBE_TRACE)
            if len(tr) < 2:
                return ["探针只跑了 %d 轮, 不足以切段判定到达形状。" % len(tr)]
            out.append("探针进度(轮→连续就绪个数@t): "
                       + "  ".join("%d个@%.0fms" % (n, t) for t, n, _o in tr))
            nb = int(getattr(self.sfx, "_n_bank", 0) or 0)
            na = int(getattr(self.sfx, "_expected", 0) or 0)
            if nb <= 0 or na <= nb:
                out.append("满编数未知(合成音 %d / 总 %d), 不做判定。" % (nb, na))
                return out
            t_b = None
            for t, n, _o in tr:
                if n >= nb:
                    t_b = t
                    break
            t_e, n_e = tr[-1][0], tr[-1][1]
            if t_b is None:
                out.append("合成音(%d 个)在本次采样窗内**从未**全部就绪 ⇒ 数据不够。" % nb)
                return out
            if t_b <= 0.0 or t_e <= t_b or n_e <= nb:
                out.append("切分点退化(t_合成音段=%.0fms, t_末=%.0fms, 末轮就绪=%d) ⇒ 不足以判定。"
                           % (t_b, t_e, n_e))
                return out
            bb = _LOAD_BYTES.get("bank", 0) / 1048576.0      # MB
            bv = _LOAD_BYTES.get("voice", 0) / 1048576.0
            nb_v = na - nb
            per_s_bank = t_b / float(nb)                      # 合成音: ms/个
            per_s_voice = (t_e - t_b) / float(nb_v)           # 语音:   ms/个
            per_b_bank = (t_b / bb) if bb > 0 else 0.0        # 合成音: ms/MB
            per_b_voice = ((t_e - t_b) / bv) if bv > 0 else 0.0
            r_s = (per_s_voice / per_s_bank) if per_s_bank > 0 else 0.0
            r_b = (per_b_voice / per_b_bank) if per_b_bank > 0 else 0.0
            out.append("")
            out.append("合成音段: %d 个 / %.2f MB / %.0f ms  ⇒ %.2f ms/个, %.0f ms/MB"
                       % (nb, bb, t_b, per_s_bank, per_b_bank))
            out.append("语音段:   %d 个 / %.2f MB / %.0f ms  ⇒ %.2f ms/个, %.0f ms/MB"
                       % (nb_v, bv, t_e - t_b, per_s_voice, per_b_voice))
            out.append("两段比值: 每样本 %.2fx   每MB %.2fx   (越接近 1 就越像那种定价)"
                       % (r_s, r_b))
            if not bb or not bv:
                out.append("⇒ 缺字节数(缓存可能没走 prime), 只给数字不做判定。")
            elif r_s <= 0 or r_b <= 0:
                out.append("⇒ 数值退化, 不做判定。")
            elif abs(r_b - 1.0) < abs(r_s - 1.0):
                out.append("⇒ **更像「按字节」定价**(每MB 的比值更靠近 1) —— "
                           "那么把数据做小(降采样/裁静音/换格式)是有意义的。")
            else:
                # ⚠️ 文案与老版不同(老版是"杠杆在**样本数**上"): 老版那个「杆」**不在
                #    字体子集里**(FontSubset 只有 1602 码位), 真机上这行会显示成方块。
                #    这是老版为豆腐块让过 4 次路之后**又漏掉的第 6 处**(另外几处:
                #    「摄氏度」「每瓦跑分」「群」, 以及启动诊断里的「没登记到样本」)。
                #    含义不变。
                out.append("⇒ **更像「每样本」定价**(每样本耗时的比值更靠近 1) —— "
                           "那么把数据做小基本没用, 关键在**样本数**上。")
        except Exception as _e:
            out.append("(自动判定失败: %r)" % (_e,))
        return out

    def _replay_cost_text(self):
        """重放冷启动的耗时, 一行文本。

        ⚠️ 原来算的是 `_replay_t0`(点下去那一刻)到"音效真的能播"那一刻的差值 —— 真机上它
           显示成 **41ms**, 而面板上明明写着「烘焙 2587ms + 音效等待 617ms」, 两者自相矛盾。
           那个差值为什么变成 41ms 这一轮没查到根因。
        ⚠️ 但**没必要依赖那个时序**: `bake_ms`(这次烘焙花了多久) 与 `ready_ms`(等"真的能播"
           花了多久) 本来就是"这次冷启动花了多久"的两个组成部分, 而且它们与「启动信息」
           面板读的是**同一份真源** ⇒ 直接相加, **结构上不可能再和面板打架**。
        ⚠️ `_replay_t0` 仍然记进启动日志(仅供以后追那个 41ms), 但**不参与显示**。
        ⚠️ 拿不到数就返回空串, 调用方按"没有第二行"处理 —— 绝不因为一个提示崩掉。
        """
        _t0 = getattr(self, "_replay_t0", 0.0)
        if _t0:
            try:
                _boot_log("frame", "重放: 从点下去到此刻 %.0f ms (仅诊断, 不参与显示)"
                          % ((time.perf_counter() - _t0) * 1000.0))
            except Exception:
                pass
        try:
            _ms = (float(getattr(self.sfx, "bake_ms", 0.0) or 0.0)
                   + float(getattr(self.sfx, "ready_ms", 0.0) or 0.0))
        except Exception:
            return ""
        if _ms <= 0:
            return ""
        return "耗时 %.0f 毫秒" % _ms

    def _replay_summary(self):
        """重放结束后摆在加载页上的结论。

        ⚠️ 直接**复用 `audio_detail()`** —— 不许另写一套格式化: 那样 PC 上又会冒出
        `音效就绪 0 / 0`(PCM 后端压根不用 sampleId), 而这个数在那边是**没有意义的**,
        看着却像全军覆没。复用同一处真源, 两个地方才不会各说各话。"""
        try:
            rows = []
            ver = _app_version()
            if ver:
                rows.append("游戏版本　%s" % ver)
            rows.extend(self.sfx.audio_detail())
            return "\n".join(rows)
        except Exception:
            return ""

    def _finish_replay_veil(self):
        """玩家点掉了「重放冷启动」那一屏: **摘页 + 弹出详细统计**。幂等; 绝不在这里动音频栈。

        摘页和弹窗是一件事, 顺序是先摘页(别让弹窗盖在加载页上, 那样关掉弹窗会露出一个
        已经没用的加载页)。"""
        v = getattr(self, "_replay_veil", None)
        self._replay_veil = None
        self._replay_busy = False
        self._load_veil = None
        if v is not None:
            v.drop()
        self._show_replay_detail()

    def _bench_set_balance(self, v):
        """把余额**直接设成 v** 并同步显示/动画基准 —— 不碰输入、不播音、不写状态栏。

        ⚠️ 三个 `_anim_*` **必须一起同步** —— 不同步的话余额会**从旧值开始滚动**。
        ⚠️ 时钟用 `self.game.now`(**不是 `time.time()`**): 余额动画那段比较的是
           `now - _anim_start_time`, 而 `Game.now` 是从 0 起累加的模拟时钟 —— 塞一个墙钟
           绝对值进去会让差值恒为大负数, 动画直接冻住(Game 里其余每一处 `_anim_start_time`
           写的也都是 `self.now`)。
        """
        try:
            self.game.balance = int(v)
            self.game.display_balance = float(v)
            self.game._anim_target_balance = float(v)
            self.game._anim_start_balance = float(v)
            self.game._anim_start_time = self.game.now
            self.game._refresh_stats()
        except Exception:
            pass

    def _bench_counters_begin(self):
        """跑分**开始前**: 弹珠数**重置成起始值**; 投中数先记下, 跑完再放回。

        玩家要的是**开始就归零到起始值**(`START_BEADS`) —— 不是"快照 + 原样放回":
        那样跑的时候弹珠数照样从当前值往上滚, 只是跑完还回来。

        ⚠️ **投中数不同处理**: `plays/hits` 是**真实的累计投中数**(而且写进配置)
           ⇒ 那两项仍然"记下再放回", **不归零**(=不抹他的记录)。
        ⚠️ 依然**不走 `reset_balance()`**: 那个会解锁输入、播 `cash` 音效、写状态栏。
        """
        try:
            self._bench_snap = (int(self.game.plays), int(self.game.hits),
                                int(getattr(self.game, "round_plays", 0) or 0))
        except Exception:
            self._bench_snap = None
        self._bench_set_balance(START_BEADS)

    def _bench_counters_end(self):
        """跑分**结束后**: 弹珠数**再重置一次**; 投中数放回跑前的值(不让测试那几发把它撑大)。

        ⚠️ 幂等: 快照只能用一次(用完置 None) ⇒ 重复调无害。
        """
        _s = getattr(self, "_bench_snap", None)
        self._bench_snap = None
        if _s:
            try:
                _pl, _hi, _rp = _s
                self.game.plays = int(_pl)
                self.game.hits = int(_hi)
                self.game.round_plays = int(_rp)
            except Exception:
                pass
        self._bench_set_balance(START_BEADS)

    def _start_bench_test(self):
        """开始性能测试(菜单点"开始测试"后)。"""
        self.game._bench_running = True
        # 普通测试的电池温度只要首尾两个点; 这里是整场测试真正的起点。
        self._bench_battery_start_c = _battery_temp_c()
        self._bench_battery_end_c = None
        # 常亮要覆盖**整场**模拟测试: 从预热等待、屏幕渲染采样开始,
        # 而不是等到后半段物理演算的黑屏 `_show_bench_dim()` 才开。这里只开常亮,
        # 不改系统栏/黑屏; 结束和异常路径仍由 `_hide_bench_dim()` 统一关闭。
        _set_keep_awake(True)
        # ⚠️ **快照弹珠数/投中数** —— 下面的渲染采样会发球、每发都中奖
        #    ⇒ 不快照的话玩家的弹珠数会被这次测试撑大。
        #    还原在 `_run_benchmark` 的 `finally` 里(异常路径也罩得住)。
        self._bench_counters_begin()
        # ⚠️⚠️ **把随机钉死 —— 跑分必须是"放录像", 不是"再抽一次"。**
        #   病根: 每轮球的落格是随机的 ⇒ 中奖次数不同 ⇒ **装杯时长不同** ⇒ 内容配比每轮都不一样。
        #   实测连续三轮的装杯占比是 **25.2% / 30.6% / 38.2%**, 而 1%Low 是 92.4 / 92.2 / 88.4 ——
        #   差的 3.9 **全来自内容配比**, 不是代码变差。**追了三轮噪声。**
        #   随机源(查实的): 发球时 `arc_dy = random.uniform(...)` 用全局 `random`;
        #   撞钉扰动走 `rng = getattr(b, "_rng", None) or random` —— 正常发射 `_rng` 是 None
        #   ⇒ 也走全局。另外 `WinPileFX._rng` 是**无种子的独立实例**, 它决定装杯那几颗球的
        #   下落时长 ⇒ 直接影响装杯多久。**两处都要钉。**
        #   ⚠️ `getstate/setstate` 而不是"跑完再 seed()" —— 后者会把正常游戏的球路也弄得每局一样。
        try:
            self._bench_rng_state = random.getstate()
            random.seed(BENCH_SEED)
            _wf = getattr(self.game_area, "win_fx", None)
            self._bench_pile_rng = getattr(_wf, "_rng", None)
            if _wf is not None:
                _wf._rng = random.Random(BENCH_SEED + 1)
        except Exception:
            self._bench_rng_state = None
            self._bench_pile_rng = None
        self._bench_saved_status = self.status_lbl.text
        self._phys_started = False
        self._phys_done = 0
        self._prog_start()
        # ⚠️ **黑屏与白字不在这里建** —— 本函数跑在**工作线程**上, 而它们要碰 Kivy 的
        #    canvas / CoreLabel。**在调用方 `_wait_idle_then_bench`(主线程)那里建。**
        # ⚠️⚠️ **盘面也要存**。跑分期间 `_auto_launch_tick` 每一发都把 9 个槽**全钉成
        #    `BENCH_BOARD[i]` 那一个值**, 而且**写回了缓存** `_boards[rtp_target]`。
        #    跑分结束时只还了随机数、没还盘面 ⇒ **跑分一完, 下面 9 个倍率槽全是 `x100`**
        #    (玩家原话:「都是*100 这个明显不合理」), 一直挂到下一次发射才自愈。
        self._bench_save_board()
        ver = _app_version()
        _set_label_text(self.status_lbl, ("模拟测试中 " + ver) if ver else "模拟测试中…")
        # ⚠️ 走 `game._controls`(状态位 + 染色一起), 理由见 `_start_hp_test`。
        #    老版对应点: main.py 的 `_start_bench_test` 里那句 `_set_controls_enabled(False)`。
        self._act(self.game._controls, False)
        # 渲染跑分不额外叠加中央红字或飘字：被测画面只保留正常游戏 HUD，
        # 这样 1% Low 与玩家实际发射时看到的负载完全一致。
        self.game_area.hide_bench_badge()
        # ⚠️ **等启动预热跑完再采样**。采样窗口只有 7~12 秒, 而玩家是启动后 3 秒就长按标题
        #    开跑的 —— 真机上一个预热单步要 100~200 毫秒, 常常还没跑完。混进采样里会把
        #    1%Low 压下去, 而且量到的是**启动期**的数, 不是玩家平时玩的数。
        #    ⚠️ 有上限(最多等 20 秒): 预热万一卡住也不能把跑分永远挂在这儿 —— 唯一红线是"绝不软锁"。
        self._bench_wait_bake = 0.0
        # ⚠️⚠️ **让出 0.1 秒**(约 3 帧) 再开始采样: 重置珠子会刷新 HUD 上几个标签 ⇒
        #    **文字纹理重建是下一帧才发生的**, 而 `_await_prebake` 在预热早就完成时
        #    (玩过一会儿再跑分, **最常见**)会**当场**调 `_start_benchmark()` ⇒
        #    重置与"开始收样本"落在**同一帧** ⇒ 那次重建**正好落进采样窗口**。
        Clock.schedule_once(lambda _dt: self._await_prebake(), 0.1)

    def _await_prebake(self, dt=0):
        """预热没跑完就先等着(最多 20 秒), 跑完再开采样。见 `_start_bench_test` 处说明。"""
        if _PREBAKE_DONE[0] or self._bench_wait_bake >= 20.0:
            self._start_benchmark()
            return
        self._bench_wait_bake += 0.25
        Clock.schedule_once(self._await_prebake, 0.25)

    def _start_benchmark(self):
        """阶段1: 真实屏幕采样(on_flip, 自动发球5发), 发满后停止采样，再测阶段2物理吞吐。"""
        self.game_area.hide_bench_badge()
        self._flip_times = []
        # ---- 诊断: 光有"平均帧率/1%Low"没法定位卡在哪 ----
        self._bench_frames = []          # [(帧间隔ms, 场景标签, ...)]
        self._bench_gc = {}              # gen -> [次数, 总秒, 最坏秒]
        self._bench_gc_t0 = 0.0
        self._bench_cpu0 = time.process_time()
        self._bench_cpu_prev = self._bench_cpu0
        self._bench_thr_prev = _THREAD_TIME()
        _FRAME_CALLS[0] = 0
        _TEXUPD[0] = 0
        _TEXUPD_BY.clear()
        _TEXUPD_ACTIVE[0] = True
        # ⚠️ 缓存命中/未命中计数**必须跟着归零**: 它是"这一轮缓存有没有生效"的读数,
        #    带着上一轮的残值就等于在骗自己。
        _TEXEX_HIT[0] = 0
        _TEXEX_MISS[0] = 0
        # ⚠️ 必须在这里归零: 上一轮跑分残留的 swap 值会被当成"第一帧等屏幕的时间"记进新日志的
        #    第一行(那种"凭空冒出来的 40 毫秒"没人解释得了)。
        _FRAME_SWAP[0] = 0.0
        _FRAME_BRK.clear()
        # 屏幕刷新率采样: 清零 + 起一个 0.5 秒的 tick。
        # ⚠️ tick 自己会在采样结束时返回 False 摘掉自己, 不用另找地方 unschedule。
        _BENCH_HZ.clear()
        try:
            Clock.schedule_interval(self._bench_hz_tick, 0.5)
        except Exception:
            pass
        # 同上, 「字号」的分解计数必须跟着归零 —— 不归零的话采样窗口第一帧会背着
        # "上次采样结束以来"的全部累计, 印出一个没人解释得了的"叫了 300 次"。
        _FRAME_FIT[0] = 0
        _FRAME_FIT[1] = 0
        # 冷字号榜也要清 —— 不清的话第一份日志里会混着上一轮的残值(那是"没发生的事")。
        del _COLD_FS[:]
        # ⚠️ **同理, 而且这三个以前一直没清**: 发声/震动计数和预热位都是**裸模块级计数,
        #    采样期之外照常累加**, 而 `_on_flip` 只在 `if prev is not None:` 里复位它们
        #    ⇒ **日志第一行背的是"上次采样结束以来"的全部累计**。
        #    真机铁证: 一帧只有 0.74 毫秒却背着 `发声6`(全窗口次大才 3)、`字号1010.0` ——
        #    那两个数物理上装不进 0.74 毫秒。
        #    ⚠️ **别图省事把 `_on_flip` 里那三行挪出来** —— 那里必须**每帧清**
        #    (它清的是"这一帧记了多少"), 而这里要的是**开跑前清一次**。两处都要。
        _FRAME_PROBE[0] = 0
        _FRAME_PROBE[1] = 0
        _FRAME_PROBE[2] = 0
        # CPU 调频状态(工作线程里采, 不占主线程): 玩家的第三方工具在跑分期看到"前期只有
        # 1.1GHz、后期才 4.5GHz", 而 `主线程ms` 是**真实 CPU 秒**, 主频差 4 倍会让同一个
        # 函数量出来差 4 倍。不采这一格, 上面所有 CPU 数字都缺前提。
        _cpufreq_start()
        # 逐帧文字纹理重建计数(与 `_bench_frames` 同序等长的平行表)
        self._bench_tex = []
        self._bench_tex_prev = _TEXUPD[0]
        self._bench_cpusplit0 = _cpu_split()
        _SND_STAT[0] = 0.0
        _SND_STAT[1] = 0.0
        _SND_STAT[2] = ""
        _SND_STAT[3] = 0.0
        _VIB_STAT[0] = 0.0
        _VIB_STAT[1] = 0.0
        _VIB_STAT[2] = ""
        _JNI_STAT[0] = 0.0
        _JNI_STAT[1] = 0.0
        _JNI_STAT[2] = 0
        _JNI_STAT[3] = 0.0
        _JNI_STAT[4] = 0.0
        _JNI_STAT[5] = 0
        _JNI_STAT[6] = 0.0
        _JNI_STAT[7] = 0
        _JNI_STAT[8] = 0.0
        _CFG_STAT[0] = 0.0
        _CFG_STAT[1] = 0.0
        _CFG_STAT[2] = 0
        self._bench_wall0 = time.time()
        import gc
        try:
            gc.callbacks.append(self._bench_gc_cb)
        except Exception:
            pass
        # 阶段 1 测的是 Kivy 主渲染线程，不能像物理跑分那样在工作线程里锁。
        # 这里运行在主线程，且只覆盖 on_flip 采样窗口；采样结束会先恢复，再启动物理工作线程。
        self._render_aff_before = _bench_pin_fast_cpus()
        self._render_cpu_pin = dict(_BENCH_CPU_PIN)
        Window.bind(on_flip=self._on_flip)
        self._launch_count = 0
        self._target_launches = BENCH_TARGET_LAUNCHES
        # 只在跑分期间轮询；0.1 秒把每局结束到下一发的空档从最多 0.5 秒缩到最多 0.1 秒。
        # 回调只读状态，发射后立即离开 ready，不会重复触发或改变游戏物理。
        self._auto_evt = Clock.schedule_interval(self._auto_launch_tick, 0.1)

    def _bench_gc_cb(self, phase, info):
        """量每一次 GC 的耗时。安卓上 GC 停顿直接表现为掉帧, 而本工程从来没调过 gc。"""
        try:
            if phase == "start":
                self._bench_gc_t0 = time.perf_counter()
                return
            d = time.perf_counter() - self._bench_gc_t0
            e = self._bench_gc.setdefault(info.get("generation", -1), [0, 0.0, 0.0])
            e[0] += 1
            e[1] += d
            if d > e[2]:
                e[2] = d
        except Exception:
            pass

    def _bench_tag(self):
        """这一帧"屏幕上在演什么"。跑分现在会把装杯演出一起采样(25s 窗口被拉到 ~15s),
        所以只看总平均分不清"飞行卡"还是"装杯卡" —— 分场景统计才能回答玩家问的那句
        「球在飞的时候一卡一卡的」。"""
        fx = getattr(self.game_area, "win_fx", None)
        md = getattr(fx, "mode", "idle") if fx is not None else "idle"
        if md in ("pending", "win", "result"):
            return "装杯"
        st = getattr(self.game, "state", "ready")
        if st == "flying":
            return "飞行"
        if st == "landing":
            return "落袋"
        if st == "misfire":
            return "哑火"
        if st == "charging":
            return "蓄力"
        return "待机"

    def _on_flip(self, win):
        # ⚠️ **量帧间隔必须用单调钟, 不能用 `time.time()`**。
        #    `time.time()` 是 **CLOCK_REALTIME** —— 会被 NTP 校时、用户改时间、时区/夏令时
        #    调整**跳变**。而这里记下来的差值, 就是面板上「平均帧率 / 1%Low / 10%Low / p99 /
        #    p90」的**全部输入**。一次 +30ms 的校时跳变会被原样记成"一帧 30 毫秒",
        #    直接落进 1%Low 那一档(那档只有 6~10 帧)。
        #    ⚠️ 别顺手把**别处**的 `time.time()` 也换掉 —— 动画时间轴要的就是墙钟绝对值
        #    (切后台回来"直接跳终态"依赖它), 那个语义是对的。
        now = time.perf_counter()
        cpu = time.process_time()
        # ⚠️ **这一格必须真的写**: `_FRAME_THR[0]` 曾经只有"读进帧记录"和"面板打印",
        #    **没有任何一处赋值** ⇒ 面板那格"主线程"永远是**硬编码 0.0**, 而格式串照常打印
        #    "主线程0.0"、还会因为别的数 >=1.0 打开长格式分支, 字符串看起来完全健康。
        #    最坏的一种: 一个专抓静默归因的面板, 用一个常量冒充测量值。
        _tt = _THREAD_TIME()
        _FRAME_THR[0] = (_tt - getattr(self, "_bench_thr_prev", _tt)) * 1000.0
        self._bench_thr_prev = _tt
        prev = self._flip_times[-1] if self._flip_times else None
        pcpu = self._bench_cpu_prev
        self._bench_cpu_prev = cpu
        self._flip_times.append(now)
        if prev is not None:
            # ⚠️ 第三个字段是**这一帧真的烧了多少 CPU**(process_time 差)。
            #    光知道"当时在飞行"不够, 必须能分清它是**算出来的**(实算接近帧间隔 ⇒ 处理器
            #    瓶颈) 还是**等出来的**(实算很小 ⇒ GC/IO/显卡/驱动在阻塞)。这是分流的那一刀。
            #    Linux/安卓 上 process_time 纳秒级; Windows 上精度只有 15.6ms, 桌面看不出
            #    分辨力, 真机才有效。
            self._bench_frames.append(((now - prev) * 1000.0, self._bench_tag(),
                                       (cpu - pcpu) * 1000.0,
                                       _FRAME_PROBE[0], _FRAME_PROBE[1],
                                       _FRAME_SELF[0], _FRAME_PROBE[2],
                                       _FRAME_THR[0], _SINCE_LAUNCH[0],
                                       tuple(sorted(
                                           ((v * 1000.0, k) for k, v in _FRAME_BRK.items()
                                            if v > 0.0002), reverse=True)[:4]),
                                       # [10] = **上一次** `Window.flip()` 阻塞了多少毫秒。
                                       # ⚠️ 是"上一次"不是"这一次": 绑定回调 `on_flip` 跑在默认
                                       #    处理器(真正 swap)之**前**(桌面实测序列恒为 CB SWAP),
                                       #    所以读到的必然是上一帧那一笔。而这正好与这一行记的
                                       #    帧间隔配对 —— 那段间隔里含的就是那一次 swap。
                                       # ⚠️ **只能追加在末尾**: 前面 [0]~[9] 的下标被
                                       #    `_bench_collect_diag` 按号取, 插在中间会让旧解析器静默错位。
                                       _FRAME_SWAP[0],
                                       # [11] = 本帧「字号」的分解: (`_fit1` 调用次数,
                                       # `text_px` 冷测量次数)。光有 `字号50.7` 这一个数, 分不出
                                       # "一次走完阶梯+二分" 还是"一帧里十个标签同时换字",
                                       # 而那两件事的修法完全相反。
                                       (_FRAME_FIT[0], _FRAME_FIT[1])))
            # ⚠️ 本帧冷开次数的**普查**: `(N次/M测)` 只在「字号」挤进该帧最大两个子步骤时才印
            #    —— 采样当普查是错的。**必须在下面清零之前记。**
            _FIT_HIST[_FRAME_FIT[1]] = _FIT_HIST.get(_FRAME_FIT[1], 0) + 1
            _FRAME_BRK.clear()
            _FRAME_FIT[0] = 0
            _FRAME_FIT[1] = 0
            _FRAME_PROBE[0] = 0
            _FRAME_PROBE[1] = 0
            _FRAME_PROBE[2] = 0
            _FRAME_SWAP[0] = 0.0
            # 这一帧发生了几次**文字纹理重建** —— 与 `_bench_frames` **同序等长的平行表**。
            # ⚠️ 用平行表, 不往 `_bench_frames` 的元组里加字段: 那个元组的**下标被
            #    `_bench_collect_diag` 到处按号取**, 平行的另一张表不加新下标, 零风险。
            # ⚠️ `_TEXUPD[0]` 数的是**真的 `Label.texture_update`**(由 Kivy 用 Clock 延后
            #    执行, 跑在 `_frame` 外面) —— 所以它落在"那一笔账被还上"的那一帧。
            self._bench_tex.append(_TEXUPD[0] - self._bench_tex_prev)
            self._bench_tex_prev = _TEXUPD[0]

    def _auto_launch_tick(self, dt):
        # ⚠️ 这里逐句埋点(`_brk_add`): 真机抓到过一个 55.92 毫秒的帧 —— **整个
        #    `_auto_launch_tick` 51.4 毫秒, 而它调的两个子函数都没进前二**(各自 ≤0.2 毫秒)
        #    ⇒ 那 51 毫秒花在这个方法**自己身上**。所以逐句量开, 别删这几笔。
        # ⚠️ `_brk_add` 只在跑分采样期有意义(平时 `_FRAME_BRK` 没人读), 但这几句本身就是一次
        #    `perf_counter` + 一次字典累加, 比它包住的赋值贵不了多少, 不另加开关。
        _ta = time.perf_counter()
        if self._launch_count >= self._target_launches:
            self._finish_render_sample(0)
            return
        if self.game.state == "ready":
            # ⚠️ **跑分: 把这一发的盘面钉死**(见 `BENCH_BOARD`)。必须放在 `start_charge()`
            #    (也就是发射)**之前** —— 结算读的是 `multipliers[i]`, 盘面得在球飞出去之前定下来。
            #    ⚠️ 只改**值**、不改结构 ⇒ 用 `_update_slots()` 增量更新就够
            #    (它结构对不上时会自己退回完整 `_redraw()`, 不会半更新)。
            _tb = time.perf_counter()
            try:
                _bi = self._launch_count
                self.game._bench_ball_i = _bi      # 给 `launch()` 派生碰撞随机流用
                if 0 <= _bi < len(BENCH_BOARD):
                    self.game.multipliers = [BENCH_BOARD[_bi]] * NUM_SLOTS
                    self.game._boards[self.game.rtp_target] = self.game.multipliers
                    self.game_area._update_slots()
            except Exception:
                pass
            _brk_add("发·盘面", _tb)
            _tc = time.perf_counter()
            self.game.start_charge()
            _brk_add("发·起蓄", _tc)
            self._launch_count += 1
            _td = time.perf_counter()
            Clock.schedule_once(lambda _: (setattr(self.game, "power", 0.8),
                                           self.game.launch()), 0.1)
            _brk_add("发·排程", _td)
        _brk_add("发·整段", _ta)

    def _finish_render_sample(self, dt):
        """停止屏幕采样, 统计真实 FPS/掉帧, 等球落地后启动物理 benchmark。"""
        if getattr(self, "_auto_evt", None):
            self._auto_evt.cancel()
            self._auto_evt = None
        Window.unbind(on_flip=self._on_flip)
        # 主渲染线程的锁核只属于帧率采样窗口；后续物理跑分会在它自己的工作线程上单独锁核。
        _render_aff_before = getattr(self, "_render_aff_before", None)
        self._render_aff_before = None
        _bench_restore_cpu_affinity(_render_aff_before)
        self._render_cpu_pin = dict(_BENCH_CPU_PIN)
        flips = self._flip_times or []
        self._render_lows = {}
        self._render_pct = {}
        if len(flips) >= 2:
            gaps = [flips[i + 1] - flips[i] for i in range(len(flips) - 1)]
            self._render_gaps_ms = [gap * 1000.0 for gap in gaps]
            # ⚠️ **每发球的实测飞行时长**。**不新埋点** —— 用 `_bench_frames` 里那对现成的
            #    逐帧数据切段: [0] = 本帧间隔(ms), [8] = `_SINCE_LAUNCH`(该帧距本发发射过了
            #    几帧, **发射那帧为 0**)。
            #    ⚠️ 不能拿"两次 launch 的间隔"代替: 那个里面还包含下一发的蓄力延时(0.1 秒)
            #       与 tick 节拍(0.1 秒), 会系统性地多算 ~0.15 秒。
            # ⚠⚠ 判据是"计数器**回落**"而**不是**"等于 0"。
            #    病根: `_SINCE_LAUNCH[0] = 0` 写在 `launch()` 里, 而 `+= 1` 在**另一处**,
            #    两者与 `_bench_frames.append` 的先后不保证 ⇒ 发射那帧被记下来时计数器
            #    **很可能已经是 1 而不是 0** ⇒ `== 0` 一次都不成立 ⇒ **整场只切出一段**
            #    (那一段 = 整个采样窗口)。改成"当前值 <= 上一个值"就开新段: 0 跟 1 都能认出来,
            #    而飞行中计数器只增不减 ⇒ 不会误切。
            _fl_ms = []
            _seg = None
            _prev_sl = None
            for _f in (getattr(self, "_bench_frames", None) or []):
                if len(_f) < 9:
                    continue
                _sl = _f[8]
                if _prev_sl is None or _sl <= _prev_sl:   # 回落 = 新的一发
                    if _seg is not None:
                        _fl_ms.append(_seg)
                    _seg = 0.0
                # ⚠️⚠️ **只累加"球真的在动"的帧**(飞行 + 落袋), **把装杯演出排除掉**:
                #    跑分用的固定盘面每一发都中奖 ⇒ **每一发都会演装杯**, 而下一发要等
                #    `state == ready` 才发 ⇒ 按"两次发射的间隔"切出来的段**里面全是装杯时间**
                #    (实测能把每发时长从 ~0.5 秒撑到 6 秒以上)。
                #    ⚠️ 标签取 `_f[1]`: 「落袋」是飞行的尾巴(球还在动), 「装杯」才是那个演出。
                _tag = _f[1] if len(_f) > 1 else ''
                if _tag in ('飞行', '落袋'):
                    _seg += float(_f[0] or 0.0)
                _prev_sl = _sl
            if _seg is not None:
                _fl_ms.append(_seg)
            self._render_flight_ms = [x for x in _fl_ms if x > 0]

            s = sorted(gaps)
            # 真平均 = 总帧数 / 总耗时；旧版误把中位数标成“平均”，外部工具无法对照。
            self._render_fps = len(gaps) / sum(gaps) if sum(gaps) > 0 else 0.0
            self._render_median_fps = 1.0 / s[len(s) // 2] if s[len(s) // 2] > 0 else 0.0
            # 低帧率(1%/10%): "最慢 N% 的帧"**平均下来**是多少 FPS。
            # ⚠️ 它和下面的 p99/p90 **不是一回事**: 这里是"最慢那批的均值", 那里是"分位上那一帧"。
            #    偶发几个巨大尖峰时, 均值会被拉得比阈值狠得多 —— 两个一起看才分得清
            #    "偶发几次长停顿"和"整体都慢"。
            for _pct in (1, 10):
                n = max(1, int(len(s) * _pct / 100.0))
                self._render_lows[_pct] = 1.0 / (sum(s[-n:]) / n) if sum(s[-n:]) > 0 else 0.0
            self._render_1low = self._render_lows[1]
            # p99/p90 帧率: **99% / 90% 的帧都比它快**。取"慢侧分位上那一帧"的帧率(不是均值)
            # —— 它是**门槛**, 不是平均。
            # ⚠️ `gaps` 是秒, 所以这里除的是 1.0 不是 1000.0(写成 1000.0 会虚高一千倍 ——
            #    面板上会看到 "p99帧率: 66438" 这种鬼数)。
            self._render_pct = {}
            for _q in (99, 90):
                _sec = s[min(len(s) - 1, int(len(s) * _q / 100.0))]
                self._render_pct[_q] = (1.0 / _sec) if _sec > 0 else 0.0
        else:
            self._render_fps = 0.0
            self._render_median_fps = 0.0
            self._render_gaps_ms = []
            self._render_1low = 0.0
        _cpufreq_stop()
        self._bench_diag = self._bench_collect_diag()
        _TEXUPD_ACTIVE[0] = False
        self._wait_idle_then_bench()

    def _bench_hz_tick(self, _dt=0.0):
        """采样期每 0.5 秒记一次「当时在演什么 + 屏幕刷新率」。

        ⚠️ `_screen_hz()` 在安卓上走 JNI, **绝不能每帧调** —— 0.5 秒一次可以忽略。
        ⚠️ 采样期一结束就**自己停**(返回 False 让 Clock 摘掉它); 不然跑完分还在后台
           每 0.5 秒戳一次 JNI。
        """
        if not _TEXUPD_ACTIVE[0]:
            return False
        try:
            _hz = round(float(_screen_hz() or 0.0), 1)
            if _hz > 0.0:
                _k = (self._bench_tag(), _hz)
                _BENCH_HZ[_k] = _BENCH_HZ.get(_k, 0) + 1
        except Exception:
            pass
        return True

    def _bench_collect_diag(self):
        """把采样到的原始帧信息整理成可读诊断。**只读采样结果, 不改任何行为。**"""
        import gc
        try:
            gc.callbacks.remove(self._bench_gc_cb)
        except Exception:
            pass
        out = {}
        fr = list(getattr(self, "_bench_frames", []) or [])
        if not fr:
            return out
        gaps = sorted(x[0] for x in fr)
        nn = len(gaps)
        pk = lambda q: gaps[min(nn - 1, int(nn * q))]
        out["n"] = nn
        # 采样窗口的**时长**(毫秒) = 帧间隔之和。口径必须与 `_bench_frame_log()` 的日志头
        # 一致(那边也是 `sum(gaps)`), 否则面板与日志会各印一个窗口, 读者对不上账。
        # ⚠️ **不要**用 `self._flip_times[-1] - self._flip_times[0]`: 那个含首帧之前的一段,
        #    会比这里多一帧, 两边差一个帧间隔。
        out["win_ms"] = sum(float(x[0]) for x in fr)
        out["p50"] = pk(0.50)
        out["p99"] = pk(0.99)
        out["max"] = gaps[-1]
        # 与成绩页 1% Low 使用同一批最慢帧，专门保存成可读的归因摘要。
        # 这样结果页不必再堆满与优化无关的累计计数。
        _low1_n = max(1, int(nn * 0.01))
        _low1 = sorted(fr, key=lambda x: x[0], reverse=True)[:_low1_n]
        out["low1_n"] = _low1_n
        out["low1_ms"] = sum(x[0] for x in _low1) / _low1_n
        _low1_groups = {}
        for _x in _low1:
            _low1_groups[_x[1]] = _low1_groups.get(_x[1], 0) + 1
        out["low1_groups"] = sorted(_low1_groups.items(), key=lambda x: -x[1])
        # 16.7ms 是 60 FPS 的帧预算；120Hz 的平均值高并不代表没有越过这条线。
        out["over60_n"] = sum(1 for x in fr if x[0] > (1000.0 / 60.0))
        # ---- **卡顿帧** = 帧率低于「本机中位帧率」一半的帧(帧间隔 > 中位 x 2) ----
        # ⚠️ **必须用相对本机中位的判据, 不能写死绝对帧率**: 165Hz 与 60Hz 的机器上同一个
        #    绝对门槛含义完全不同(原来硬编码 1000/90, 结果 1%Low 贴到 89.7 时同一份数据
        #    反而报"变差")。
        # ⚠️ 两档: **<50% 要尽量消除; 50%~70% 只作参考**。
        #    所以这里只收 <50% 的那一档进 `jank_*`, 50%~70% 留在日志里当趋势看, **不上面板**。
        _med = float(out.get("p50") or 0.0)
        _jank = [x for x in fr if _med > 0.0 and x[0] > _med / JANK_RATE]
        out["jank_n"] = len(_jank)
        # **慢帧** = 帧率低于「中位帧率 75%」—— 比卡顿帧宽一档, **包含**卡顿帧。
        # ⚠️ **先取出这一个 list, 再拿它同时算"有几帧"和"分布"** —— 写成两趟就是
        #    "两处各算一遍", 迟早印出「慢帧 168 帧」而分布只有 9。
        _slow75 = [x for x in fr if _med > 0.0 and x[0] > _med / SLOW_RATE]
        out["slow_n"] = len(_slow75)
        _sg = {}
        for _x in _slow75:
            _sg[_x[1]] = _sg.get(_x[1], 0) + 1
        out["slow_groups"] = sorted(_sg.items(), key=lambda x: -x[1])
        _jg = {}
        for _x in _jank:
            _jg[_x[1]] = _jg.get(_x[1], 0) + 1
        out["jank_groups"] = sorted(_jg.items(), key=lambda x: -x[1])
        # 最慢的 3 帧 + 当时在演什么 + **那一帧真烧了多少 CPU**。
        # 后两个数一起看才分流: 实算 ≈ 帧间隔 ⇒ 算出来的(处理器瓶颈);
        # 实算很小 ⇒ 等出来的(GC/IO/显卡/驱动阻塞) —— 真机上这是唯一能分清的地方。
        # 带上它在 `_bench_frames` 里的**下标** —— 三个最慢帧是不是**同一段忙碌里的连续帧**,
        # 看下标就知道。
        by_worst = [x for _i, x in sorted(enumerate(fr), key=lambda p: -p[1][0])[:3]]
        # [帧间隔, 场景, 全进程实算, 主线程(x[7]), 自算(x[5]), 是否预热(x[6]), 子步骤(x[9]), 下标]
        _idx_of = {}
        for _i, _x in enumerate(fr):
            _idx_of[id(_x)] = _i
        out["worst"] = [[x[0], x[1], x[2], (x[7] if len(x) > 7 else 0.0),
                         (x[5] if len(x) > 5 else 0.0),
                         (x[6] if len(x) > 6 else 0),
                         (x[9] if len(x) > 9 else ()),      # breaks(子步骤) —— 加字段时别忘同步
                         _idx_of.get(id(x), -1)] for x in by_worst]
        # 全程自算的中位数 —— 与"实算"并排看: 两者都小 ⇒ 主线程既没算也没被我们的代码占,
        # 帧时间就是框架/出图的开销(优化我们的代码没用)。
        _self_sorted = sorted((x[5] if len(x) > 5 else 0.0) for x in fr)
        out["self_p50"] = _self_sorted[len(_self_sorted) // 2] if _self_sorted else 0.0
        out["self_max"] = _self_sorted[-1] if _self_sorted else 0.0
        # 主线程 CPU(线程级时钟) —— 它与"自算"的**差额就是 `_frame` 外面那一大块**
        # (Kivy 渲染 / 延迟的文字重排 / 其它 Clock 回调)。
        # ⚠️ 索引别记错: 帧记录尾部依次是 `..., _FRAME_SELF, _FRAME_PROBE[2], _FRAME_THR, breaks`
        #    ⇒ **主线程在 [7]、子步骤在 [8]**。曾经把这两个写反, 于是 `thr_p50` 拿到的是
        #    **元组**, 面板那行 `%.1f` 直接 TypeError, 而它外面那个 except 把整块诊断**静默**
        #    返回成了空串 —— 一个专抓静默的面板自己静默了。
        _thr_sorted = sorted((x[7] if len(x) > 7 else 0.0) for x in fr)
        out["thr_p50"] = _thr_sorted[len(_thr_sorted) // 2] if _thr_sorted else 0.0
        out["thr_max"] = _thr_sorted[-1] if _thr_sorted else 0.0
        out["texupd"] = _TEXUPD[0]
        # 文字纹理缓存: 命中 = 两趟全跳过(省掉 4~10.7ms 的 `填纹`)。
        # ⚠️ 这两个数**必须印进日志** —— 否则没法判断这层到底有没有生效(命中率 0 时
        #    必须能一眼看出来, 而不是"看着像好了"却什么都没省)。
        out["texex_hit"] = _TEXEX_HIT[0]
        out["texex_miss"] = _TEXEX_MISS[0]
        # ⚠️ 只留前 4 名 —— 但**必须把"其余还有多少"一并记下来**: 真机日志里"全程文字重建
        #    76 次"与"重建来源四项相加 68"差了 8 次(约 10%), 而**日志里没有任何地方提示这里
        #    截断了** —— 看日志的人只会以为那 8 次凭空消失。
        _by_all = sorted(_TEXUPD_BY.items(), key=lambda it: -it[1])
        out["texupd_by"] = _by_all[:4]
        out["texupd_rest"] = sum(int(c) for _, c in _by_all[4:])
        out["texupd_tags"] = len(_by_all)
        # ---- 等屏幕(swap 阻塞)的分布 ----
        # 为什么要它: 真机同一天两份日志(屏幕 120Hz / 60Hz)显示"慢帧"在两种刷新率下**都**落在
        # 1.35~1.45 个刷新周期上, 比例几乎一样 ⇒ 只降帧率救不了。而那批慢帧里有一大半
        # **主线程根本没烧 CPU**(<30% 帧长) —— 光看 CPU 分不出它们是"我们交晚了"还是"屏幕不让交"。
        _sw = sorted(float(x[10]) if len(x) > 10 else 0.0 for x in fr)
        out["swap_p50"] = _sw[len(_sw) // 2] if _sw else 0.0
        out["swap_max"] = _sw[-1] if _sw else 0.0
        out["swap_sum"] = sum(_sw)
        # ---- 节拍真值: 屏幕多少 Hz / 请求的模式 / Kivy 实际生效的上限 / vsync ----
        # ⚠️ 这三样 `_FPS_INFO` 里一直有, 但**从没进过日志文件** —— 而它们是"这份日志能不能
        #    和上一份比"的唯一前提。实测教训: 120Hz 那轮平均 120.3fps、60Hz 那轮 60.1fps,
        #    同一份代码同一个版本, 1%Low 一个是 83.3 一个是 42.2, 差别全部来自屏幕档位。
        try:
            out["screen_hz"] = float(_FPS_INFO[0] or 0.0)
            out["req_hz"] = float(_FPS_INFO[2] or 0.0)
        except Exception:
            out["screen_hz"], out["req_hz"] = 0.0, 0.0
        try:
            out["clock_maxfps"] = float(getattr(Clock, "_max_fps", 0.0) or 0.0)
        except Exception:
            out["clock_maxfps"] = 0.0
        # ---- CPU 调频状态 ----
        # ⚠️ 单位是 MHz; 采不到就留空列表, 打印端据此**整段不印**(不印 0 冒充量到)。
        try:
            _fr_ = list(_CPUFRQ.get("freqs") or [])
            out["cpufreq_n"] = len(_fr_)
            out["cpufreq_cap"] = float(_CPUFRQ.get("cap", 0.0) or 0.0)
            out["cpufreq_p50"] = _fr_[len(_fr_) // 2] if _fr_ else 0.0
            # 平均 —— 「所有"代表值"都改平均」。⚠️ 这一段是**渲染窗口**采的, 应用大部分时间
            #    在等 vsync ⇒ 均值天生偏低, 打印端**必须注明它是渲染窗口**。
            out["cpufreq_mean"] = (sum(_fr_) / len(_fr_)) if _fr_ else 0.0
            out["cpufreq_min"] = min(_fr_) if _fr_ else 0.0
            out["cpufreq_max"] = max(_fr_) if _fr_ else 0.0
            # 低于"上限 50%"的采样占比 —— 直接回答"是不是全程在低频跑"。
            _cap = out["cpufreq_cap"]
            out["cpufreq_low_pct"] = (100.0 * sum(1 for _v in _fr_ if _cap and _v < 0.5 * _cap)
                                      / len(_fr_)) if (_fr_ and _cap) else -1.0
        except Exception:
            out["cpufreq_n"] = 0
        # 节拍事实: Kivy 的限速旋钮实际是什么值, 以及"逻辑更新几次 vs 呈现了几帧"。
        try:
            from kivy.config import Config as _Cfg
            out["maxfps"] = str(_Cfg.get("graphics", "maxfps"))
            out["vsync"] = str(_Cfg.get("graphics", "vsync")) or "(空=不改)"
        except Exception:
            out["maxfps"], out["vsync"] = "?", "?"
        out["frame_calls"] = _FRAME_CALLS[0]
        try:
            from kivy.config import Config as _Cfg2
            out["multisamples"] = str(_Cfg2.get("graphics", "multisamples"))
        except Exception:
            out["multisamples"] = "?"
        # ---- C6 判据: 最差 20 帧距上一次发射各过了几帧 ----
        try:
            _w20 = sorted(fr, key=lambda x: -x[0])[:20]
            _pos = [(x[8] if len(x) > 8 else -1) for x in _w20]   # [8] = 距上次发射的帧数
            _pos = [p for p in _pos if p is not None]
            _pos.sort()
            out["w20_pos"] = _pos
            out["w20_near"] = sum(1 for p in _pos if p <= 20)   # 发射后 20 帧内(约 0.33 秒)
        except Exception:
            out["w20_pos"] = []
            out["w20_near"] = -1
        _cs1 = _cpu_split()
        _cs0 = getattr(self, "_bench_cpusplit0", None)
        if _cs1 and _cs0:
            out["utime_ms"] = (_cs1[0] - _cs0[0]) * 1000.0
            out["stime_ms"] = (_cs1[1] - _cs0[1]) * 1000.0
        else:
            out["utime_ms"] = out["stime_ms"] = -1.0
        # 慢帧的"节拍"与"这一帧在发声/震动吗" —— 定位偶发长停顿的两把刀:
        #   · 节拍规则 ⇒ 时钟驱动(某个 0.5s 定时器); 不规则 ⇒ 事件驱动。
        #   · 慢帧里绝大多数在发声/震动 ⇒ 就是那条路(玩家"关音效就变好"的因果线索)。
        # ⚠️ 门槛按**中位帧的相对比例**定, 不用任何绝对值。来由: 原来两处各写一套绝对值
        #    (`BENCH_SLOW_MS=90` 与 `1000/90`), 而本机最慢帧只有 40ms ⇒ `_slow` **恒为空集**,
        #    "慢帧的节拍/在发声/在震动"几行永远不打印。
        # ⚠️⚠️ **这两个键必须叫 `slow2_*`, 不能叫 `slow_n`/`slow_ms`**: 上面已经把
        #    `out["slow_n"]` 定义成「中位帧率 <75%」的帧数 —— 那是**成绩面板**读的数;
        #    而这里这一批是**归因分析集**(≥2 倍中位帧时间, 即 <50%)。原来这里写的是
        #    `out["slow_n"] = len(_slow)`, **把面板那个数原地覆盖成了 5**, 面板再套一层
        #    `max(slow_n, jank_n)` 兜底 ⇒ 印出来**恒等于卡顿帧**(真机实测 <75% 真值 168 帧,
        #    被覆盖成 5, 面板印的却是 9)。
        _slow_ms = 2.0 * out["p50"]
        out["slow2_ms"] = _slow_ms
        _slow = [x for x in fr if x[0] >= _slow_ms]
        out["slow2_n"] = len(_slow)
        out["slow_beat"] = (sum(g for g, *_r in fr) / len(_slow)) if _slow else 0.0
        out["slow_snd"] = sum(1 for x in _slow if x[3] > 0)
        out["slow_vib"] = sum(1 for x in _slow if x[4] > 0)
        # 全程总数 —— 用来**自证计数器在工作**: 面板上看到"全程发声 N 次"就知道探针没坏,
        # 否则"慢帧里 0 帧在发声"既可能是真的、也可能是计数器根本没跑(这个仓库栽过这种静默)。
        out["snd_n"] = sum(x[3] for x in fr)
        out["vib_n"] = sum(x[4] for x in fr)
        # 慢帧里有多少帧是**启动预热**在跑(第 7 个字段)。与"在发声/震动"同一把刀。
        out["slow_bake"] = sum(1 for x in _slow if (x[6] if len(x) > 6 else 0) > 0)
        # 发声耗时: 单次最慢 + 全程累计。**这是"那一声到底卡了多久"的直接证据** ——
        # 真机实测慢帧是纯等(119ms 只烧 10.4ms CPU), 所以要看的就是这个数。
        out["snd_worst"] = _SND_STAT[1] * 1000.0        # 全程单次最慢(毫秒)
        out["snd_sum"] = _SND_STAT[0] * 1000.0          # 全程累计(毫秒)
        out["snd_worst_name"] = _SND_STAT[2]
        out["snd_slow_n"] = int(_SND_STAT[3])
        out["vib_worst"] = _VIB_STAT[1] * 1000.0
        out["vib_sum"] = _VIB_STAT[0] * 1000.0
        out["vib_worst_name"] = _VIB_STAT[2]
        # 方向守卫 / 沉浸重申: 每 0.7 秒各一次, **跑分期间照跑**(已搬出主线程)。
        # 它是"周期性停顿"里唯一的常驻项, 而 1%Low 只看最差的那十几帧 —— 每 0.7 秒来一记
        # 正好能把那一档占满。搬走之后要看的是**两档的对比**:
        #   主线程档 ≈ 0 且 工作线程档 接手  ⇒ 真搬走了(慢帧该跟着消失);
        #   主线程档仍然大                ⇒ 没投出去(队列建不起来 / 满了), 等于没改;
        #   失败次数 > 0                  ⇒ 守卫在真机上抛异常了(原来被 except 静默吞掉)。
        out["jni_n"] = _JNI_STAT[2]
        out["jni_main_worst"] = _JNI_STAT[1] * 1000.0
        out["jni_main_sum"] = _JNI_STAT[0] * 1000.0
        out["jni_bg_sum"] = _JNI_STAT[3] * 1000.0
        out["jni_bg_worst"] = _JNI_STAT[4] * 1000.0
        out["jni_err"] = _JNI_STAT[5]
        # UI 线程那档(沉浸重申本身)。**这是本面板唯一能看到"不在我们线程上"的开销的地方。**
        out["ui_sum"] = _JNI_STAT[6] * 1000.0
        out["ui_n"] = _JNI_STAT[7]
        out["ui_worst"] = _JNI_STAT[8] * 1000.0
        # 音频后端名 —— 这个仓库栽过一次: SoundPool 构造失败会**静默降级**到 Kivy-SoundLoader,
        # 而后者走 SDL_mixer, 阻塞行为完全不同。不知道后端就分不清是哪一个在卡。
        try:
            out["backend"] = str(getattr(self.sfx.out, "name", "?"))
        except Exception:
            out["backend"] = "?"
        # 分场景: 飞行 vs 装杯(跑分把装杯也采样进去了, 不分就分不清是谁在拖)
        grp = {}
        for _x in fr:
            grp.setdefault(_x[1], []).append(_x[0])
        out["groups"] = {}
        for t, xs in grp.items():
            xs.sort()
            out["groups"][t] = (len(xs), xs[len(xs) // 2])
        # 每帧真正花在计算上的时间(全进程 CPU / 帧数)。面板的"瓶颈"判断读它:
        # 它离"帧间隔"越近, 越是处理器顶不住; 差得远就是大头在等画面。
        _cpu_tot = time.process_time() - getattr(self, "_bench_cpu0", 0.0)
        out["cpu_per_frame"] = _cpu_tot * 1000.0 / max(1, out["n"])
        # GC 停顿: 次数 / 总时长 / 最坏一次
        gcs = getattr(self, "_bench_gc", {}) or {}
        out["gc_n"] = sum(v[0] for v in gcs.values())
        out["gc_total"] = sum(v[1] for v in gcs.values())
        out["gc_worst"] = max((v[2] for v in gcs.values()), default=0.0)
        # 最坏那一次是**哪一代**的回收 —— gen-2(全量)在扫整个对象图, 量级与 gen-0 完全不同。
        # 这一栏是"GC 冻结有没有生效"的判据: 冻结之后 gen-2 该变成扫不到东西。
        out["gc_worst_gen"] = (max(gcs, key=lambda g: gcs[g][2]) if gcs else -1)
        out["gc_frozen"] = _GC_FROZEN[0]
        out["gc_frz_before"] = _GC_FROZEN[1]
        out["gc_frz_after"] = _GC_FROZEN[2]
        out["cfg_sum"] = _CFG_STAT[0] * 1000.0
        out["cfg_worst"] = _CFG_STAT[1] * 1000.0
        out["cfg_n"] = _CFG_STAT[2]
        return out

    def _wait_idle_then_bench(self, dt=0):
        """等球落地(主线程空闲)再启动物理 benchmark, 避免抢 CPU 干扰结果。"""
        if self.game.state == "ready":
            # ⚠️⚠️ **黑屏与白字必须在主线程建**(真机卡死事故)。它们是 Kivy 的 canvas 指令与
            #    **CoreLabel 的文字光栅化** —— 而 `_run_benchmark` 跑在**工作线程**上。
            #    这两句写进线程体里的真机表现: **黑屏出来了、白字一动不动, 整轮卡死**。
            #    ⇒ 建在**主线程这一侧**(本函数是 Clock 回调), 别搬回线程体里。
            self._show_bench_dim()
            # ⚠️ 这句只在黑屏刚上来时亮一下 —— 0.25 秒后 `_prog_tick` 就会用真进度盖掉,
            #    所以**不拼数字**(总秒数到那时还没算出来), 只写名字。
            self._set_bench_msg("物理演算")
            threading.Thread(target=self._run_benchmark, daemon=True).start()
        else:
            Clock.schedule_once(self._wait_idle_then_bench, 0.5)

    def _run_benchmark(self):
        """**两波**: 波 1 测性能(峰值, 带间隔) -> 波 2 测高压(衰减, 一秒不停)。

        ⚠️⚠️ **顺序不能反**: 波 2 会把这台机器烤热, 反过来的话波 1 就不再是"峰值"了 ——
           那是白测。
        ⚠️ **两波各自采一遍 CPU 频率**(`_freq_sampler_start`), 分开存。
           为什么必须单独采: 日志里那行「CPU 频率(采样期)」采的是**渲染窗口**那二十几秒,
           与物理跑分**不是同一段时间** —— 拿它解释跑分的差异是**张冠李戴**。
        ⚠️ 采样线程每 0.5 秒只读几次小文件, 对跑分的 GIL 干扰可忽略。
        """
        # ---- 波 1: 测性能(峰值) ----
        _f1, _s1 = _freq_sampler_start()
        self._phys_started = True

        def _on_sample(_i, _n):
            self._phys_done = _i

        # ⚠️⚠️ **波 1 全程把帧率按到 `_BENCH_FPS_FORCE_PHYS`**(比高压那档更低,
        #    因为波 1 期间屏幕已经换成黑屏+白字, 20fps 刷新一行进度绰绰有余)。
        #    `finally` 是硬的: 跑分中途抛异常也必须把帧率还回去, 否则玩家会一直卡在低帧率。
        _bench_fps_lock_on(phys=True)
        # 只收窄当前跑分工作线程；正常游戏和渲染线程的调度不受影响。
        _bench_aff_before = _bench_pin_fast_cpus()
        # 调度优先级: 亲和性管"允许跑哪些核", 这里管"抢不抢得到"。**只改这颗线程**, 不碰物理。
        _bench_prio_before = _bench_raise_thread_priority()
        try:
            flights, frames, fps_list, cpu_secs = benchmark_trajectories(on_sample=_on_sample)
        # ⚠️ **`finally` 的第一行必须是还原动作** —— 门禁 L6 是照"finally 首行"查配对的。
        #    ⚠️ `_bench_fps_lock_off` **必须带 `phys=True`**, 与上面的 `lock_on(phys=True)`
        #       配对 —— 少一个就只增不减 ⇒ 跑完一次跑分后帧率**再也回不去**。
        finally:
            _bench_fps_lock_off(phys=True)
            # ⚠️ **把弹珠数/投中数还回去** —— 放在 `finally` 里是刻意的: **异常路径也得还**。
            #    ⚠️ 必须在 `_bench_fps_lock_off` **之后** —— 门禁 L6 查的是 `finally:` 的**首行**。
            self._bench_counters_end()
            # ⚠️ **兜底撤黑屏**(异常路径) —— 正常路径由 `_bench_done` 撤, 这里管"跑分中途抛
            #    异常"那条, 没有它黑屏会永久留在屏幕上。`_hide_bench_dim` 幂等。
            Clock.schedule_once(lambda dt: self._hide_bench_dim(), 0)
            _bench_restore_thread_priority(_bench_prio_before)
            _bench_restore_cpu_affinity(_bench_aff_before)
        _s1[0] = True
        _f1.sort()
        self._phys_freq_p50 = _f1[len(_f1) // 2] if _f1 else 0
        self._phys_freq_n = len(_f1)
        self._phys_freq_min = _f1[0] if _f1 else 0
        self._phys_freq_max = _f1[-1] if _f1 else 0
        # ⚠️ **平均频率** —— 玩家定案:「cpu频率不能用中位数」「必须用**跑分时**的平均频率」。
        #    所以: ①这一格是**均值不是中位**; ②采到的样本已经由 `_FREQ_GATE` 把**样本之间的
        #    sleep 段**滤掉了(那几秒 CPU 闲, 会把均值拖低)。
        # ⚠️ `_phys_freq_p50/min/max/n` **一个都不删** —— JSON 里留着, 只是不再当"代表值"显示。
        self._phys_freq_mean = int(sum(_f1) / len(_f1)) if _f1 else 0
        # ⚠️ **逐轮步/秒 + 逐核频率 + 亲和性** —— 三样都是为"同一台机器只改帧率上限,
        #    跑分差 69% 而最大频率只差 9%"这件事加的仪表。
        #    最有用的是**逐轮**: 中位数把 5 轮压成一个数, 而"一直低"和"中途掉一轮"是两种病。
        self._phys_fps_runs = [round(float(x), 1) for x in (fps_list or [])]
        self._phys_cores = _freq_core_stats()
        self._phys_aff = list(_BENCH_AFF)
        self._phys_cpu_pin = dict(_BENCH_CPU_PIN)
        self._phys_tid_prio = dict(_BENCH_TID_PRIO)
        self._phys_speed = [round(float(x), 1) for x in _BENCH_SPEED]
        self._phys_render_fps = [round(float(x), 2) for x in _BENCH_RENDER_FPS]
        self._phys_alloc = [round(float(x), 1) for x in _BENCH_ALLOC]
        # ⚠️ **归一化跑分** = 中位步/秒 ÷ 中位纯算术探针 × 1e6。
        #    为什么要有它(真机三轮定案): "中位步/秒"**会被渲染帧率污染** —— 实测同一台机器
        #    ①(渲染120.3fps) 18943 vs ③(渲染80.4fps) 21580, 差 13.9%, 而两次的**纯算术探针
        #    只差 1.1%**(核心速度一样)。把核心速度因子除掉之后, 剩下的才是"这台机器跑物理"
        #    的相对好坏 ⇒ **跨帧率设定比较只能用这个数**。
        #    ⚠️ 它**不是**"机器快不快"的绝对指标(那要看步/秒本身) —— 两个一起看。
        _sp_med = 0.0
        if self._phys_speed:
            _ss = sorted(x for x in self._phys_speed if x > 0)
            _sp_med = _ss[len(_ss) // 2] if _ss else 0.0
        _fp = sorted(float(x) for x in (fps_list or []) if x > 0)
        _fp_med = _fp[len(_fp) // 2] if _fp else 0.0
        self._phys_norm = int(round(1000000.0 * _fp_med / _sp_med)) if _sp_med > 0 else 0
        # ⚠️ **主测试不再跑高压段**(高压拆成独立按钮)。这里必须**主动清空**, 否则上一次高压
        #    测试的 `_sust_fps` 会残留在内存里, 面板和日志就会把**旧的高压结果**当成这一次的
        #    印出来 —— 那是"印假数", 比没有更糟。
        self._sust_fps = None
        self._sust_freq = None
        Clock.schedule_once(lambda dt: self._bench_done(flights, frames, fps_list, cpu_secs), 0)

    def _bench_save_board(self):
        """跑分开始前把盘面存一份(与"还"配对的另一半, 见 `_bench_restore_board`)。

        ⚠️ 存的是**每个列表的副本**(`list(v)`) —— `multipliers` 与 `_boards[rtp]`
           是**同一个 list 对象**, 只存引用的话原地改动会连带污染存下来的那份。
        ⚠️ 抽成独立方法是为了**能被探针直接调** —— 跑分那整条链太长, 端到端测不起来。
        """
        try:
            self._bench_saved_boards = {r: list(v) for r, v in self.game._boards.items()}
        except Exception:
            self._bench_saved_boards = None

    def _bench_restore_board(self):
        """把跑分钉死的盘面还回去(存盘在 `_start_bench_test`)。

        ⚠️ 为什么必须还: `_auto_launch_tick` 每一发都把 9 个槽**全钉成 `BENCH_BOARD[i]`**
           那一个值, 第 5 发是 `100`, 而且它**写回了缓存** ⇒ 跑分结束时只还了随机数、没还
           盘面 ⇒ **跑分一完, 下面 9 个倍率槽全是 `x100`**(玩家原话:「都是*100 这个明显
           不合理」), 一直挂到下一次发射(`park_ball` 会重掷)才自愈 —— 中间那段时间看着就是坏的。
        ⚠️ 抽成独立方法是为了**能被探针直接调**。
        返回是否真的还了(没存盘 = 跑分没起来过 = 不用还)。
        """
        _sb = getattr(self, "_bench_saved_boards", None)
        if not _sb:
            return False
        self._bench_saved_boards = None
        self.game._boards = _sb
        _m = self.game._boards.get(self.game.rtp_target)
        if _m:
            self.game.multipliers = _m
        try:
            self.game_area._update_slots()      # 只刷 9 个槽, 不整块重画
        except Exception:
            pass
        return True

    def _device_info(self):
        if platform == 'android':
            try:
                from jnius import autoclass
                b = autoclass('android.os.Build')
                return '%s / Android %s' % (b.MODEL, b.VERSION.RELEASE)
            except Exception:
                try:
                    import subprocess
                    p = subprocess.run(['getprop', 'ro.product.model'],
                                       capture_output=True, text=True)
                    model = p.stdout.strip() or 'Android'
                    p2 = subprocess.run(['getprop', 'ro.build.version.release'],
                                        capture_output=True, text=True)
                    ver = p2.stdout.strip() or '?'
                    return '%s / Android %s' % (model, ver)
                except Exception:
                    return 'Android 设备'
        import platform as pf
        return '%s / %s / Python %s' % (pf.node(), pf.system(), pf.python_version())

    def _build_info(self):
        """这个包是**什么时候做出来的** —— 长按标题那两个弹窗里显示一行。

        ⚠️ **版本号不在这儿**: 它被挪进了弹窗标题(见 `_startup_title`), 这里只剩制作时刻。
        日期取 `main.py` 的文件 mtime。

        ⚠️ **为什么不把日期烘成源码常量**: 安卓版的构建脚本是纯字符串拼接、不做任何改写 ——
        源码里写死日期的话, `--check` 每次都报"生成物与源不同步"。运行时取 mtime 就与生成
        过程完全解耦。mtime 为什么约等于构建日: 打包时 p4a 把整个 app 目录塞进 APK 里的
        `private.tar`, 条目时间戳 = 构建机上那些文件的写入时间(CI 是新拉代码后立刻构建),
        首次运行时 bootstrap 解包, `tarfile` 默认保留 mtime。

        ⚠️ 整段 try/except: 拿不到就少显示一段, 绝不把弹窗带崩。
        """
        parts = []
        try:
            t = os.path.getmtime(os.path.abspath(__file__))
            # 1600000000 = 2020-09; 再过掉"未来时间"(设备时钟不对时会取到), 免得显示怪日期
            if 1600000000 < t < time.time() + 86400:
                # ⚠️ 文案: 不用「构建」(行话); 用「于 … 制作」; 年月日写成**汉字** ——
                #    `2026-09-11` 这种全数字写法在中文语境下容易被读反(有人按 日/月 读)。
                parts.append('于 %s 制作'
                             % time.strftime(BUILD_TIME_FMT, time.localtime(t)))
        except Exception:
            pass
        return ' · '.join(parts)

    def _bench_done(self, flights, frames, fps_list, cpu_secs=None):
        # ⚠️ 兜底再还一次(幂等, 重复调无害): `_run_benchmark` 的 `finally` 正常路径已经还过了,
        #    这里只是防那条路被绕过。
        self._bench_counters_end()
        self.game_area.hide_bench_badge()
        # ⚠️ 撤黑屏。少了它, 波 1 跑完黑屏会一直挂在屏幕上。
        self._hide_bench_dim()
        # 结果计算已经开始, 此处读整场普通测试的终点温度;
        # 不放到弹窗建好之后, 避免把结果界面停留时间算进去。
        self._bench_battery_end_c = _battery_temp_c()
        _phys_sorted = sorted(fps_list)
        # ⚠️⚠️ **运算速度取平均值**(原来是中位数), 与 CPU 高压那边**同一条口径**。
        #    ⚠️ 这**一个数牵连很广** —— 成绩面板那行 / 历史表第三列 / `cost_ms`(每发计算用时)
        #       / `phys_norm`(归一化) **全从它来**。改它是**换口径**, 不只是换显示:
        #       老记录里存的是当年的中位数 ⇒ 跨版本比成绩时**别把两者混着看**。
        #    `_phys_sorted` 仍要留着 —— 下面 `phys_min` / `phys_max` 用它。
        phys_fps = (sum(fps_list) / len(fps_list)) if fps_list else 0.0
        phys_min = _phys_sorted[0] if _phys_sorted else 0.0
        phys_max = _phys_sorted[-1] if _phys_sorted else 0.0
        phys_spread = 100.0 * (phys_max - phys_min) / phys_fps if phys_fps > 0 else 0.0
        # ⚠️ **平均差系数**: 显示改用这个, 取代旧「波动」。
        #    旧口径 `(max-min)/中位` 只看两个极端点 —— 一个离群值就能把它整个带飞;
        #    平均差用上**每一个**样本(见 `_mad_coef`)。
        #    ⚠️ `phys_spread` **照旧算、照旧存**(老记录要能读、以后要复盘), 只是**不再显示**。
        #       **别顺手把它从记录里删掉。**
        phys_mad = _mad_coef(fps_list)
        phys_runs = len(fps_list)
        # ---- 波 2(高压)的统计 ----
        # ⚠️ 口径: 首窗 vs **末窗**(不是 vs 最低)—— 问的是"一直压着跑**最后**掉到哪",
        #    最低值单独印一格。两波各自的频率也分开存。
        _sv = [x for x in (getattr(self, "_sust_fps", None) or []) if x > 0]
        if _sv:
            _s_first, _s_last, _s_min = _sv[0], _sv[-1], min(_sv)
            _s_decay = 100.0 * (_s_first - _s_last) / _s_first if _s_first > 0 else 0.0
        else:
            _s_first = _s_last = _s_min = _s_decay = 0.0
        avg_frames = frames / max(1, flights)
        cost_ms = avg_frames / phys_fps * 1000.0 if phys_fps > 0 else 0.0  # 每发纯物理耗时
        # ⚠️ 「每次发射的后续文字调整 计算用时 x.x ms, **飞行用时** x.x」+「倍率是**飞行除以
        #    计算**」+「飞行那个是**五次平均用时**」。飞行用时取**渲染采样那几发**的实测均值。
        #    富余 = 飞行 ÷ 计算: >1 是有余量, <1 是算不过来。
        _fl = [x for x in (getattr(self, "_render_flight_ms", None) or []) if x > 0]
        flight_ms = round(sum(_fl) / len(_fl), 1) if _fl else None
        margin = (round(flight_ms / cost_ms, 1)
                  if (flight_ms is not None and cost_ms > 0) else None)
        render_fps = getattr(self, "_render_fps", 0.0)
        render_median = getattr(self, "_render_median_fps", 0.0)
        render_1low = getattr(self, "_render_1low", 0.0)
        dev = self._device_info()
        # 跑分**那一段**的 CPU 频率(与日志里那行"渲染采样期"的频率不是同一段时间, 不可混用)。
        self._bench_phys_freq = {
            "p50": int(getattr(self, "_phys_freq_p50", 0) or 0),
            "min": int(getattr(self, "_phys_freq_min", 0) or 0),
            "max": int(getattr(self, "_phys_freq_max", 0) or 0),
            "n": int(getattr(self, "_phys_freq_n", 0) or 0),
            # 平均 —— 「cpu频率不能用中位数」; 上面那个 p50 留着做分布。
            "mean": int(getattr(self, "_phys_freq_mean", 0) or 0),
        }
        self._bench_phys_now = int(phys_fps)   # 给 `_bench_frame_log` 印那一行用
        # 存历史(最近100次)
        _rec = {
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "phys_fps": int(phys_fps),
            # ⚠️ `int()` -> **一位小数**: 「每次飞行平均需 x 步运算」要**一位小数**, 取整就
            #    永远印 `.0` 了。⚠️ 老记录里存的是整数 ⇒ 读出来补 `.0`, 不影响。
            "avg_frames": round(avg_frames, 1),
            "cost_ms": round(cost_ms, 1),
            # 每发的**飞行用时**(ms, 渲染采样那几发的实测均值) 与**富余倍数**(飞行÷计算)。
            # ⚠️ 老记录没有 ⇒ 详情那行印「—」, **不回填**。
            "flight_ms": flight_ms,
            "margin": margin,
            "battery_start_c": getattr(self, "_bench_battery_start_c", None),
            "battery_end_c": getattr(self, "_bench_battery_end_c", None),
            "render_fps": round(render_fps, 1),
            "render_median": round(render_median, 1),
            "render_1low": round(render_1low, 1),
            # ⚠️ 补三格 —— 它们本来只印在**跑完那一刻**的面板上, 历史里没有 ⇒ "从历史里看详情"
            #    就少三行。三个数, 体积忽略。
            #    ⚠️ 老记录没有它们 ⇒ 详情自动退回三值版, **绝不回填**。
            "render_10low": round(float((getattr(self, "_render_lows", {}) or {}).get(10, 0.0)), 1),
            "render_p99": round(float((getattr(self, "_render_pct", {}) or {}).get(99, 0.0)), 1),
            "render_p90": round(float((getattr(self, "_render_pct", {}) or {}).get(90, 0.0)), 1),
            "phys_runs": phys_runs,
            # ⚠️ **逐轮步/秒**: 中位数会把"五轮一直低"和"中途掉一轮"压成同一个数 ——
            #    而那是两种病(前者是频率/核心, 后者是温控/系统干预)。面板不印它, 留在 JSON 里。
            "phys_fps_runs": list(getattr(self, "_phys_fps_runs", None) or []),
            # ⚠️ **归一化跑分**: 中位步/秒 ÷ 中位纯算术探针 ×1e6。
            #    ⚠️ **旧记录没有它** ⇒ 面板印「无数据」, **绝不拿步/秒回填**(那是印假数)。
            "phys_norm": int(getattr(self, "_phys_norm", 0) or 0),
            "phys_speed_runs": list(getattr(self, "_phys_speed", None) or []),
            "phys_alloc_runs": list(getattr(self, "_phys_alloc", None) or []),
            "phys_min": int(phys_min),
            "phys_max": int(phys_max),
            "phys_spread": round(phys_spread, 1),
            # ⚠️ **平均差系数**: 取代「波动」作为稳定性读数。旧字段 `phys_spread` 照旧存,
            #    只是不再显示 —— 别删。
            "phys_mad": (round(phys_mad, 2) if phys_mad is not None else None),
            "phys_cpu_seconds": round(sum(cpu_secs or []), 3),
            # ⚠️ 跑分**那一段**自己的 CPU 频率: 留着它才能事后回答"两次跑分差这么多, 是不是
            #    频率不同"。`phys_freq_mean` 是**代表值**(而且采样间隙已滤掉);
            #    `phys_freq_p50` 一并留着 —— 它只是**频率分布的一项**, 不再当代表值印。
            "phys_freq_mean": int(getattr(self, "_phys_freq_mean", 0) or 0),
            "phys_freq_p50": int(getattr(self, "_phys_freq_p50", 0) or 0),
            # 波 2(高压) —— 面板只印一行, JSON 里把逐窗值和它那段的频率全留着。
            "sust_sec": int(SOC_SUSTAIN_WALL_SEC),
            "sust_first": int(_s_first), "sust_last": int(_s_last),
            "sust_min": int(_s_min), "sust_decay_pct": round(_s_decay, 1),
            "sust_freq_p50": int(getattr(self, "_sust_freq_p50", 0) or 0),
            "sust_fps_windows": [int(x) for x in _sv],
            # ⚠️ 存下**诊断块**的原始数据。体积约 1~3KB/条, 而且它本来就是 JSON 友好的。
            #    ⚠️ 老记录没有 ⇒ 详情里那一块**整块不出现**(不印空壳)。
            "diag": (getattr(self, "_bench_diag", None) or None),
            "version": _app_version(),
            "device": dev,
        }
        self.bench_history.append(_rec)
        if len(self.bench_history) > 100:
            self.bench_history.pop(0)
        self._save_bench_history()
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        # ⚠️ 标题改成 `性能测试 v0.x.x`(见 `_bench_result_title`), 正文那行不再带版本。
        title_lbl = self._fit_line(Label(text=_bench_result_title(), bold=True,
                                         halign='center', color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 20)
        content.add_widget(title_lbl)
        _lows = getattr(self, "_render_lows", {}) or {}
        _pct = getattr(self, "_render_pct", {}) or {}
        if _lows:
            # 排版: 平均帧率**独占一行**, 其余四个两两一行。
            _low_txt = ('平均帧率： %.1f    中位帧率：%.1f\n'
                        '1%%Low：%.1f    10%%Low：%.1f\n'
                        'p99帧率：%.1f    p90帧率：%.1f') % (
                            render_fps, render_median, _lows.get(1, 0.0), _lows.get(10, 0.0),
                            _pct.get(99, 0.0), _pct.get(90, 0.0))
        else:
            _low_txt = '平均帧率： %.1f　中位帧率：%.1f\n1%%Low帧率：%.1f' % (
                render_fps, render_median, render_1low)
        # ⚠️ 成绩面板**不放**版本/制作日期 —— 版本加在 **"安卓版本后面"**(不是标题后面),
        #    和机器名排在一起: 读成绩的人先看"哪台机器", 紧接着就是"哪个包"。
        # ⚠️ 版本号拿不到时**不留空尾巴**(`_app_version()` 可能返回空串)。
        _ver = ""
        try:
            _ver = str(_app_version() or "")
        except Exception:
            _ver = ""
        # ⚠️ 三段并列用 `/` 分隔(原来是两个空格, 印出来版本号看着像系统版本的一部分):
        #    `机器 / 安卓 / 游戏版本`。
        _dev_ver = (dev + " / " + _ver) if _ver and _ver not in dev else dev
        # ⚠️ 高压那行**没有数据就整行不印**(不印假数)、口径统一成「连续高压测试 N 秒」。
        _s_txt = (('连续高压测试 %d 秒：首 %d → 末 %d 步/秒（降 %.0f%%）· 最低 %d\n'
                   % (int(SOC_SUSTAIN_WALL_SEC), int(_s_first), int(_s_last),
                      int(_s_decay), int(_s_min)))
                  if _sv else '')
        # ⚠️ 改成调**共用渲染**函数 —— 现场这个弹窗与历史「详情」重开的那个**必须长一模一样**。
        #    ⚠️ 传进去的就是**刚刚存进历史的那个 dict** ⇒ 两边同源, 连"老记录缺字段"的处理
        #       都只有一份。
        score = _bench_score_text(_rec)
        # ⚠️⚠️ **字号必须与历史「详情」一致**(15sp → 双方一起落到 **14sp**): 同一个
        #    `_bench_score_text` 渲染出来, 一个不怎么折行、一个"各种乱换行"。
        #    ⚠️ 改这里要连带看 `_auto_h` 的**基准高度**: 字号小了内容也矮, 基准还留着老的大数
        #       就会多出一块空白。
        score_lbl = Label(text=score, markup=True, font_size='14sp', halign='left', valign='top',
                          color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(130))
        self._auto_h(score_lbl, dp(130), dp(6))
        content.add_widget(score_lbl)

        # ⚠️⚠️ **两个按钮都挪到面板底部, 左边「帧率曲线」右边「关闭」**, 并且**这个面板只能靠
        #    「关闭」关掉**(`auto_dismiss=False`) —— 点面板外面不再关它: 原来「帧率曲线」夹在
        #    成绩块和诊断块中间(整条通栏), 而**根本没有关闭按钮**, 关闭全靠点外面 —— 玩家点
        #    空白处想滚动/误触就把成绩面板关掉了。
        # ⚠️ 横向 `BoxLayout` 里**先 add 的在左边**(Kivy 按 `reversed(children)` 摆位)。
        _btnrow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10),
                            orientation='horizontal')
        curve_btn = Button(text='帧率曲线', font_size='16sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
        curve_btn.bind(on_release=lambda *_: self._show_fps_curve())
        close_btn = Button(text='关闭', font_size='16sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_BTN_OFF) + (1,))
        _btnrow.add_widget(curve_btn)
        _btnrow.add_widget(close_btn)

        _sep = Widget(size_hint_y=None, height=dp(1))
        with _sep.canvas.before:                      # 同 `_row_bg` 的写法
            Color(*hex_rgb(COL_DIV))
            _sep._line = Rectangle(pos=_sep.pos, size=_sep.size)
        _sep.bind(pos=lambda w, *_: setattr(w._line, "pos", w.pos),
                  size=lambda w, *_: setattr(w._line, "size", w.size))
        content.add_widget(_sep)

        diag_lbl = Label(text=self._bench_low_summary_text(), font_size='14sp',
                         halign='left', valign='top',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(110))
        self._auto_h(diag_lbl, dp(0), dp(0))
        content.add_widget(diag_lbl)
        # ⚠️ **必须最后 add** —— 竖向 `BoxLayout` 按 add 的先后从上往下排, 先加的那批在上。
        #    第一版把这一行加在 `score_lbl` 后面, 结果两个按钮卡在成绩块和诊断块**中间**。
        content.add_widget(_btnrow)

        popup = self._popup(0.90, 460, title='', content=content,
                            auto_dismiss=False, separator_height=0)
        # ⚠️ 绑定必须在 `popup` 建出来之后(和隐藏档弹窗同一个写法)。
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)
        self._prog_stop()
        _set_label_text(self.status_lbl, getattr(self, '_bench_saved_status', '按住蓄力发射'))
        self._act(self.game._controls, True)           # 理由同 `_start_bench_test`: 状态位在 Game
        # ⚠️ **盘面必须和随机数一起还**。放在 `_bench_running = False` **之前** ——
        #    之后 `park_ball` 就会重掷盘面, 这里要还的是"跑分之前那份", 不是"新掷的一份"。
        self._bench_restore_board()
        self.game._bench_running = False
        self.game._bench_start = 0.0
        # 把随机**原样还回去**: 不还的话正常游戏的球路会被钉死, 每局一模一样 ——
        # 那是比"跑分不可比"严重得多的事故。
        try:
            if getattr(self, "_bench_rng_state", None) is not None:
                random.setstate(self._bench_rng_state)
                self._bench_rng_state = None
            _wf = getattr(self.game_area, "win_fx", None)
            if _wf is not None and getattr(self, "_bench_pile_rng", None) is not None:
                _wf._rng = self._bench_pile_rng
            self._bench_pile_rng = None
        except Exception:
            pass

    def _bench_frame_log(self):
        """把这一轮的**逐帧原始采样**拼成可复制的文本(帧率曲线弹窗的"保存"按钮用)。

        形状: 头部(机器/版本/关键数 + **各阶段统计表**) → 之后每行一帧 `帧间隔毫秒,阶段,...`。
        ⚠️ 头部刻意放最前面、且自带**各阶段慢帧率** —— 那是面板上原来印不出来、而定位瓶颈
        最需要的那个数(要除以各阶段自己的总帧数, 不能拿"最慢 1% 的总帧数"当分母)。
        这样**即使粘贴被截断**, 我要的结论仍然在开头, 不会因为尾巴丢了就白跑一趟。
        ⚠️ 阶段名取 `_bench_tag()` 的原值(中文), 不做缩写 —— 缩写表本身又是一份要维护的清单。
        ⚠️ 本函数**整段不许包 try**: 外层 `_copy_bench_log` / `_bench_save_log` 会把异常吞成
        空串, 玩家看到的是"没有可复制的数据"。所以每一处取值都要自己兜底。
        """
        gaps = list(getattr(self, "_render_gaps_ms", []) or [])
        if not gaps:
            return ""
        tags = [x[1] for x in (getattr(self, "_bench_frames", []) or []) if len(x) > 1]
        if len(tags) != len(gaps):
            tags = ["?"] * len(gaps)          # 对不上就照发原始帧间隔, 不猜阶段
        tex = list(getattr(self, "_bench_tex", []) or [])

        if len(tex) != len(gaps):
            tex = [0] * len(gaps)
        _n1 = max(1, int(len(gaps) * 0.01))   # 最慢 1% 的帧数(只给 `_order[:3]` 取最慢三帧用)
        _order = sorted(range(len(gaps)), key=lambda _i: -gaps[_i])
        # 慢帧门槛 = 中位帧时间的 2 倍(= 中位帧率的 50%), 与 `_bench_diag` 的 `_slow_ms` **同口径**。
        # ⚠️ 这里原来写的是"最慢 1% 的分位临界值", 与 `_bench_diag` 的 `_slow_ms` 是**两套算法**,
        #    同一份日志里两处"慢帧"能差好几倍 ⇒ 统一成"中位帧 50%", 一律不写死绝对值。
        _p50 = sorted(gaps)[len(gaps) // 2]
        _thr = 2.0 * _p50
        # ⚠️ "慢帧"的判据**必须与下面各阶段表用同一条**: 都是 `>= 门槛`。
        #    写成 `set(_order[:_n1])` 的话, 正好卡在门槛上的并列帧会让两边算出**不同的集合**
        #    (实测 100 帧里 20 帧同值: 一边 1 帧、一边 20 帧), 于是"慢帧当帧有没有文字重建"
        #    和各阶段慢帧率互相打架。
        _slow_idx = set(_i for _i in range(len(gaps)) if gaps[_i] >= _thr)
        d = getattr(self, "_bench_diag", None) or {}
        _lines = ["# 跳跳的弹珠机 逐帧帧率日志"]
        try:
            _lines.append("# %s  %s" % (self._device_info(), str(_app_version() or "")))
        except Exception:
            pass
        try:
            _lines.append("# 窗口 %.2fs / %d 帧 / 平均 %.1f / 中位 %.1f / 1%%Low %.1f"
                          % (sum(gaps) / 1000.0, len(gaps),
                             float(getattr(self, "_render_fps", 0.0)),
                             float(getattr(self, "_render_median_fps", 0.0)),
                             float(getattr(self, "_render_1low", 0.0))))
            # ⚠️⚠️ **两套口径必须各自带名印出来**。这一行是【口径 A】: 帧间隔 ≥ 中位 × 2
            #    (等价于"中位帧率的 50%")。它与下面【口径 B】(中位帧率的 55%)**是两个不同的量**,
            #    却**都叫 ★** —— 实测第 2 轮 A=3 帧而 B=8 帧, 差 167%; 而**第 1 轮两者都给 1**,
            #    所以只看一轮永远发现不了"它们不是一个东西"。
            _lines.append("# ★口径A 长停顿「帧间隔 ≥ 中位×2」= 门槛 %.2f 毫秒(中位帧 %.2f 毫秒);"
                          " 命中 **%d** 帧   ← 这是「长停顿」口径"
                          % (_thr, _p50, len(_slow_idx)))
            # ⚠️ **物理跑分 + 它那一段自己的 CPU 频率**。起因: 同一台设备在 60Hz 与 120Hz 下
            #    跑分差**接近 2 倍**, 而跑分本该只反映 CPU ⇒ 两行相邻印, 一眼看出是不是频率不同。
            #    ⚠️ 这行里的频率是**跑分那几秒 CPU 时间**内的, 与下面「CPU 频率(采样期)」
            #       (渲染窗口那二十几秒)**不是同一段时间**, 别混用。
            _pf = getattr(self, "_bench_phys_freq", None)
            _pm = int(getattr(self, "_bench_phys_now", 0) or 0)
            if _pm > 0:
                # ⚠️ **代表值用平均, 不用中位**; 而且这个平均是**跑分时**的
                #    —— 样本之间的 sleep 段已被 `_FREQ_GATE` 滤掉。
                _pfm = int((_pf or {}).get("mean", 0) or 0) or int((_pf or {}).get("p50", 0) or 0)
                if _pf and _pfm:
                    _lines.append("# 物理跑分(中位 %d 步/秒) · **跑分那段的 CPU 平均频率**: %dMHz "
                                  "(最低 %d / 最高 %d, %d 个采样; 采样间隙已剔除)"
                                  % (_pm, _pfm, _pf["min"], _pf["max"], _pf["n"]))
                else:
                    _lines.append("# 物理跑分(中位 %d 步/秒) · 跑分那段的 CPU 平均频率: **没采到**"
                                  "(非安卓 / 读不到 sysfs)" % _pm)
            # ⚠️⚠️ **逐轮步/秒 + 逐核频率 + 亲和性**。这三行是为这个问题加的: 同一台机器只改
            #    "帧率上限", 跑分 12617/19316/21340(**差 69%**), 而"最大 CPU 频率"只差 **9%**。
            #    · **逐轮**: 中位数把 5 轮压成一个数, 而"五轮一直低"和"中途掉一轮"是两种病。
            #    · **逐核**: `平均频率` 那一格是**各核最大值** —— 大核 2.8G 的时候, 跑分线程
            #      完全可能正在一个 1.5G 的中核上。逐核才看得出这种"大核空转"。
            #    · **亲和性**: 唯一能从应用侧读到"这条线程被限在哪几个核"的口子。
            # ⚠️⚠️ **"按住了没有"的读数**: 跑分期间帧率被强制按到低值, 但**"请求"不等于"拿到"**
            #    —— 真机实测过请求 60 却渲染出 80.4fps。所以必须印**实际**渲染帧率。
            _rf = getattr(self, "_phys_render_fps", None) or []
            _rf = [x for x in _rf if x > 0]
            # ⚠️ 这里印的必须是**波 1 实际按到的那个值**(`_bench_fps_force_now()`), 不是
            #    `_BENCH_FPS_FORCE` —— 波 1 用的是更低的 `_BENCH_FPS_FORCE_PHYS`, 印常量会在
            #    日志里写"上限 60"而实际渲染 12.9fps, 自相矛盾。
            _fcap = int(_bench_fps_force_now())
            if _rf:
                _mid = sorted(_rf)[len(_rf) // 2]
                _lines.append("# 物理跑分**强制帧率上限 %d** · 那一段**实际渲染**: %s fps (中位 %.1f)%s"
                              % (_fcap, " · ".join("%.1f" % x for x in _rf), _mid,
                                 ("   ← 实际**高于** %d ⇒ **没按住**, 环境里还混着玩家的设定"
                                  % _fcap) if _mid > _fcap * 1.15
                                 else "   ← 按住了(实际 ≤ 强制值)"))
            else:
                _lines.append("# 物理跑分**强制帧率上限 %d** · 实际渲染: **没采到**" % _fcap)
            _pr = getattr(self, "_phys_fps_runs", None) or []
            if _pr:
                # ⚠️ 印的是**平均差系数 = 平均差 ÷ 均值**(见 `_mad_coef`), 不是旧口径
                #    「波动 = (max-min)/中位」—— 后者只看两个极端点, 一个离群值就能把它带飞。
                _mc = _mad_coef(_pr)
                _lines.append("# 物理跑分**逐轮**步/秒: %s  (共 %d 轮, 中位 %d, 平均差系数 %s)"
                              % (" · ".join("%.0f" % x for x in _pr), len(_pr), _pm,
                                 ("%.2f%%" % _mc) if _mc is not None else "无数据"))
            _pc = getattr(self, "_phys_cores", None) or {}
            if _pc:
                _lines.append("# 物理跑分那段的 CPU **逐核**平均频率: %s"
                              % " · ".join("cpu%d %.0f(%.0f~%.0f)"
                                           % (_k, _v["mean"], _v["min"], _v["max"])
                                           for _k, _v in sorted(_pc.items())))
                _lines.append("#   ⚠️ 上面那格「平均频率」是**各核最大值**; 逐核才看得出"
                              "**跑分线程到底跑在快核还是慢核上**(大核 2.8G 时线程可能在 1.5G 的中核)")
                # ⚠️ **实际值 vs 请求值** —— 高通平台上 `scaling_cur_freq` 读的是调频器的
                #    **目标值**, 实际时钟可以被 EPSS/温控按下去而不回写。两者对不上就说明
                #    "频率这一列本身在骗人", 前面所有"频率差不多"的结论都要作废。
                _ac = {_k: _v["act"] for _k, _v in _pc.items() if _v.get("act")}
                if _ac:
                    _rd = [_v["act"] / _v["mean"] for _k, _v in _pc.items()
                           if _v.get("act") and _v["mean"] > 0]
                    _lines.append("# 物理跑分那段的 CPU **实际**频率(`cpuinfo_cur_freq`): %s"
                                  % " · ".join("cpu%d %.0f" % (_k, _v) for _k, _v in sorted(_ac.items())))
                    _lines.append("#   ⚠️ 实际/请求 = **%.2f 倍**(1.00 = 两者一致)。**明显小于 1 "
                                  "⇒ `scaling_cur_freq` 报的不是实际值, 前面所有「频率差不多」"
                                  "的结论都得作废**。" % (sum(_rd) / len(_rd)))
                else:
                    _lines.append("# 物理跑分那段的 CPU **实际**频率(`cpuinfo_cur_freq`): "
                                  "**没读到**(这台机器不给 / 非安卓) —— 那就分不出"
                                  "「频率掉下去了」还是「缓存被抢了」, 只能看下面那行探针")
            else:
                _lines.append("# 物理跑分那段的 CPU **逐核**平均频率: **没采到**(非安卓 / 读不到 sysfs)")
            # ⚠️⚠️ **纯算术探针** —— 把"核心真的慢了"和"缓存被抢了"分开的那把尺子。
            #    判读写在行里, 因为这一行是给"下一次有人来查这个问题"看的。
            _ps2 = getattr(self, "_phys_speed", None) or []
            _ps2 = [x for x in _ps2 if x > 0]
            if _ps2 and len(_ps2) == len(_pr) and _pr:
                _lines.append("# 物理跑分**逐轮**纯算术探针(不碰内存): %s  (每秒轮数)"
                              % " · ".join("%.0f" % x for x in _ps2))
                # ⚠️ 两个量的**量纲不同**(一个是"步"、一个是"轮"), 直接相除得到的数没有意义。
                #    要的是**它们各自相对第 1 轮的倍率** —— 两个倍率一比, 才回答得了
                #    "是核心慢了还是缓存被抢了"。
                _lines.append("#   相对第 1 轮: **步/秒** %s   |   **探针** %s"
                              % (" · ".join("%.3f" % (x / _pr[0]) for x in _pr),
                                 " · ".join("%.3f" % (x / _ps2[0]) for x in _ps2)))
                _lines.append("#   ⚠️ 上面两串**比一比**: 步/秒掉得**明显比探针多** ⇒ 缓存/内存被抢;"
                              " 两个**掉得差不多** ⇒ 核心真的慢了")
                _spr, _sps = max(_ps2) / min(_ps2), max(_pr) / min(_pr)
                _lines.append("#   本轮内 探针最大/最小 = %.2f 倍 · 步/秒最大/最小 = %.2f 倍  ⇒ %s"
                              % (_spr, _sps,
                                 "**步/秒抖得比探针明显 ⇒ 不是核心速度的问题**"
                                 if _sps > _spr * 1.3 else
                                 ("**两者抖得差不多 ⇒ 就是核心速度在变**"
                                  if _spr > 1.3 else "两者都稳(本轮没有可解释的波动)")))
                # ⚠️ **归一化跑分** —— 跨帧率设定比较**只能**用这个数。
                _nz = int(getattr(self, "_phys_norm", 0) or 0)
                if _nz:
                    _lines.append("#   **归一化跑分** = 中位步/秒 ÷ 中位纯算术探针 ×1e6 = **%d**"
                                  "   ← **跨帧率设定比跑分只能用这个数**: 步/秒本身会被渲染帧率"
                                  "污染(实测 120fps 与 80fps 差 13.9%%, 而探针只差 1.1%%)" % _nz)
                _pa3 = [x for x in (getattr(self, "_phys_alloc", None) or []) if x > 0]
                if _pa3 and len(_pa3) == len(_ps2):
                    _lines.append("# 物理跑分**逐轮**对象分配探针(碰分配器+内存): %s  (每秒轮数)"
                                  % " · ".join("%.0f" % x for x in _pa3))
                    _lines.append("#   相对第 1 轮: **分配探针** %s   ← 与上面**纯算术探针**那一串比:"
                                  " 两个**一起掉** = 核心慢; **只有分配探针掉** = **内存/分配器被抢**"
                                  % " · ".join("%.3f" % (x / _pa3[0]) for x in _pa3))
                    _lines.append("#   分配/算术 = %s   (这个比值**掉了**就说明内存那一侧变慢,"
                                  "与核心速度无关)"
                                  % " · ".join("%.3f" % (_pa3[_i] / _ps2[_i])
                                               for _i in range(len(_pa3))))
                else:
                    _lines.append("# 物理跑分**逐轮**对象分配探针: **没采到**")
            elif not _ps2:
                _lines.append("# 物理跑分**逐轮**纯算术探针: **没采到**")
            _pa = getattr(self, "_phys_aff", None) or []
            if _pa and any(x for x in _pa):
                _lines.append("# 物理跑分线程的 **CPU 绑定核**(每轮采一次, `sched_getaffinity`): %s"
                              % " | ".join(("?" if x is None else ",".join(str(i) for i in x))
                                           for x in _pa))
            else:
                _lines.append("# 物理跑分线程的 CPU 绑定核: **没采到**(非安卓 / 不支持)")
            _rpin = getattr(self, "_render_cpu_pin", None) or {}
            if _rpin.get("pinned"):
                _rtarget = _rpin.get("actual", _rpin.get("target", [])) or []
                _rrestored = ",".join(str(_cpu) for _cpu in (_rpin.get("restored", []) or []))
                _lines.append("# 帧率采样主线程性能核锁定: **已锁定** %s · 恢复 %s"
                              % (",".join("cpu%d" % int(_cpu) for _cpu in _rtarget),
                                 _rrestored or "失败"))
            else:
                _lines.append("# 帧率采样主线程性能核锁定: **未锁定**(%s)"
                              % (_rpin.get("reason", "未执行") or "未知原因"))
            # `sched_getaffinity` 的全核集合只表示"允许调度"。这里额外写明本轮是否真的把
            # benchmark 工作线程锁在最高性能簇，避免把优化请求误读成已经生效。
            _pin = getattr(self, "_phys_cpu_pin", None) or {}
            if _pin.get("pinned"):
                _pcaps = _pin.get("caps_khz", {}) or {}
                _ptarget = _pin.get("actual", _pin.get("target", [])) or []
                _pfreq = ",".join("cpu%d=%dMHz" % (int(_cpu),
                                   int(_pcaps.get(_cpu, _pcaps.get(str(_cpu), 0))) // 1000)
                                  for _cpu in _ptarget)
                _before = ",".join(str(_cpu) for _cpu in (_pin.get("before", []) or []))
                _restored = ",".join(str(_cpu) for _cpu in (_pin.get("restored", []) or []))
                _lines.append("# 物理跑分工作线程性能核锁定: **已锁定** %s (%s) · 原允许 %s · 恢复 %s"
                              % (",".join("cpu%d" % int(_cpu) for _cpu in _ptarget), _pfreq,
                                 _before or "?", _restored or "失败"))
            else:
                _lines.append("# 物理跑分工作线程性能核锁定: **未锁定**(%s)"
                              % (_pin.get("reason", "未执行") or "未知原因"))
            # ⚠️ **跑分线程的调度优先级**: 与锁核正交的另一半 —— 锁核管"允许跑哪些核",
            #    优先级管"抢不抢得到 CPU"。系统可以静默拒绝, 所以这里印的是
            #    `getThreadPriority(0)` 的**读回值**, 不是我们的请求值。没变负就是没生效。
            _pr = getattr(self, "_phys_tid_prio", None) or {}
            if _pr.get("raised"):
                _lines.append("# 物理跑分工作线程优先级: **已提升** %d → %d · 恢复 %s"
                              % (int(_pr.get("before", 0)), int(_pr.get("after", 0)),
                                 _pr.get("restored", "?")))
            else:
                _lines.append("# 物理跑分工作线程优先级: **未提升**(%s)"
                              % (_pr.get("reason", "未执行") or "未知原因"))
            # ⚠️ **波 2(高压)那两行** —— 与波 1 相邻印, 两波的频率**必须分开看**。
            _sv2 = [x for x in (getattr(self, "_sust_fps", None) or []) if x > 0]
            if _sv2:
                _d2 = 100.0 * (_sv2[0] - _sv2[-1]) / _sv2[0] if _sv2[0] > 0 else 0.0
                _sf2 = getattr(self, "_sust_freq", None) or {}
                if _sf2.get("p50"):
                    _f2t = ("高压那段的 CPU 频率: 中位 %dMHz "
                            "(最低 %d / 最高 %d, %d 个采样)"
                            % (_sf2["p50"], _sf2["min"], _sf2["max"], _sf2["n"]))
                else:
                    _f2t = "高压那段的 CPU 频率: **没采到**"
                _lines.append("# 高压 %d 秒(背靠背不停): 首 %d → 末 %d 步/秒"
                              "(降 %.0f%%) · 最低 %d"
                              % (int(SOC_SUSTAIN_WALL_SEC), int(_sv2[0]), int(_sv2[-1]),
                                 _d2, int(min(_sv2))))
                _lines.append("#   逐窗: " + ", ".join("%d" % x for x in _sv2))
                _lines.append("#   " + _f2t)
                # ⚠️ **高压的纯算术探针 + 归一化**: 高压的绝对值同样被"渲染抢内存"污染
                #    ⇒ **跨设置/跨设备比高压成绩只能用归一化那个数**。
                #    ⚠️ 归一化取的是**首窗**(峰值), 不是中位 —— 中位会被后面的温控衰减拉低。
                _hs2 = [x for x in (getattr(self, "_hp_speed", None) or []) if x > 0]
                if _hs2 and _sv2:
                    _lines.append("#   高压**逐窗**纯算术探针: "
                                  + ", ".join("%.0f" % x for x in _hs2))
                    _lines.append("#   高压**归一化** = 首窗步/秒 ÷ 首窗探针 ×1e6 = **%d**"
                                  "   ← **跨设置/跨设备比高压成绩只能用这个数**"
                                  % int(getattr(self, "_hp_norm", 0) or 0))
                    _lines.append("#   高压逐窗 步/秒÷探针(×1e6): "
                                  + " · ".join("%d" % int(1000000.0 * _sv2[_i] / _hs2[_i])
                                               for _i in range(min(len(_sv2), len(_hs2)))))
                    _lines.append("#     上面**这一串稳不稳**才是真衰减: 它平而步/秒在掉 ⇒ 机器整体"
                                  "慢了; 它跟着掉 ⇒ 渲染/内存那一侧变了")
                else:
                    _lines.append("#   高压纯算术探针: **没采到**")
                # ⚠️ **"按住了没有"的读数**: 与波 1 同一条规矩 —— "请求"不等于"拿到"。
                #    整段一个数就够(高压期间渲染负载基本恒定)。
                _lines.append("# 高压**强制帧率上限 %d** · 整段**实际渲染**: %.1f fps   ← 明显高于 %d "
                              "⇒ **没按住**, 环境仍受玩家设定影响"
                              % (_BENCH_FPS_FORCE,
                                 float(getattr(self, "_hp_render_fps", 0.0) or 0.0),
                                 _BENCH_FPS_FORCE))
        except Exception:
            pass
        # ---- ★ 这一轮到底能不能和上一轮比 ----
        # ⚠️ 加这一段的理由: 连续三轮的「低于 90fps 的帧数」是 10 / 10 / 9, 而
        #    **1%Low 是 92.4 / 92.2 / 88.4** —— 差的那 3.9 全来自"这轮抽到了大杯局"
        #    (装杯占比 25.2% → 30.6% → 38.2%), 不是代码变差。
        #    **装杯占比不印出来, 每一轮都不可比。**
        # ⚠️ 判据用「低于 90fps 的帧数」而不是 1%Low: 后者被配比带得晃。
        # ⚠️ **这一段不包在 `try` 里**(它自己在下面逐项兜底) —— 包了的话一旦出错就被静默吞掉,
        #    而玩家看到的是"日志里少了这两行", 不报错也不提示。
        # 判据 = **中位帧率的 50%**(帧间隔 = 中位的 2 倍)。与 70% 同源, 只是更严。
        # ⚠️ 两档口径: **<50% 要尽量消除; 50%~70% 只作参考**。
        #    所以下面印**两行** —— 一行硬判据、一行参考带, 别把参考带的帧混进硬指标里。
        _p50b = sorted(gaps)[len(gaps) // 2]
        _th90 = _p50b / JANK_RATE
        _below90 = [_i for _i in range(len(gaps)) if gaps[_i] > _th90]
        _th_ref = _p50b / SLOW_RATE
        _refband = [_i for _i in range(len(gaps)) if _th_ref < gaps[_i] <= _th90]
        # ⚠️ **就地取, 不引用上面的 `tex`/`tags`** —— 那两个列表在本函数里定义得**很晚**,
        #    而这一段的插入点在头部 ⇒ 直接引用会 NameError。
        _tex_all = list(getattr(self, "_bench_tex", []) or [])
        _fr_all = list(getattr(self, "_bench_frames", []) or [])
        _tags_all = [x[1] for x in _fr_all if len(x) > 1]
        if len(_tags_all) != len(gaps):
            _tags_all = ["?"] * len(gaps)
        _b90_tex = sum(1 for _i in _below90 if _i < len(_tex_all) and _tex_all[_i] > 0)
        _b90_face = 0
        _b90_fit = 0
        for _i in _below90:
            try:
                _b = _fr_all[_i][9] if (0 <= _i < len(_fr_all) and len(_fr_all[_i]) > 9) else ()
                if any(_k == "板面" for _v, _k in (_b or ())):
                    _b90_face += 1
            except Exception:
                pass
            # 「带字号」的判据是**分解计数**不是子步骤名: `_fit1` 可能跑在**别的回调**里,
            # 最大子步骤那一栏未必写着"字号" —— 但 `_FRAME_FIT[0] > 0` 一定为真。
            try:
                _r = _fr_all[_i]
                if 0 <= _i < len(_fr_all) and len(_r) > 11 and _r[11][0] > 0:
                    _b90_fit += 1
            except Exception:
                pass
        # ⚠️ `1%%Low` 的双百分号**不能省**: 这一行是 `%` 格式化的, 写成 `1%Low` 会被当成
        #    格式符(`%L`), 报的是 "not enough arguments for format string" —— 报错信息
        #    指向 `%d` 的个数, 而**真正的原因在后面那个 `%`**。
        _lines.append("# ★口径B 低于「中位帧率 55%%」(%.1f fps = 帧间隔 ≥ %.3f 毫秒 = 中位×%.3f)"
                      " 的帧数: %d  ·  其中带文字重建 %d / 带板面 %d / 带字号 %d"
                      "   ← **跨版本比较用这一条**(口径A 是另一回事, 见上)"
                      % (1000.0 / _th90, _th90, (_th90 / _p50) if _p50 > 0 else 0.0,
                         len(_below90), _b90_tex, _b90_face, _b90_fit))
        # 参考带单独一行(50%~70% 只作参考, 不进硬指标)。
        _lines.append("# ★ 参考带「中位帧率 55%%~75%%」(%.1f~%.1f fps): %d 帧  ·  中位帧 %.2f 毫秒"
                      "   ← 这**不是**指标, 只用来看趋势(跟着上面的硬指标一起降才对)"
                      % (1000.0 / _th90, 1000.0 / _th_ref, len(_refband), _p50b))
        # ★ 成绩面板「慢帧（<75%）」那一档的**自证行**。
        # ⚠️ 为什么必须单独印: 面板读的是 `_bench_diag["slow_n"]`, 而这个数在收尾统计里
        #    算一遍、又有可能被后面的段落覆盖一次(真踩过, 印出来恒等于卡顿帧)。
        #    印成 `N = 低于55% + 参考带` 之后, **下次读日志当场就能验** —— 对不上就是又脱钩了。
        _lines.append("# ★ 慢帧「中位帧率 <75%%」(%.1f fps): %d 帧 = 低于55%% %d + 参考带 %d"
                      "   ← 成绩面板那一档读的就是这个数(面板把门槛印成帧/秒)"
                      % (1000.0 / _th_ref, len(_below90) + len(_refband),
                         len(_below90), len(_refband)))
        # ★ **面板窗口 vs 日志窗口** 的自证行。
        # ⚠️ 为什么必须有: 面板读 `_bench_diag` 的 `n` / `win_ms`, 而日志头这两个数是从
        #    `gaps` 现算的 —— **两边各算一遍**, 正是本工程反复栽的脱钩形状。印在同一行上,
        #    下次读日志当场就能验。
        _dn = int(d.get("n", -1) or -1)
        _dw = float(d.get("win_ms", -1.0) or -1.0)
        _lw = sum(gaps)
        _lines.append("# ★ 面板窗口 vs 日志窗口: %d 帧 / %.2fs  vs  %d 帧 / %.2fs  ==> %s"
                      % (_dn, _dw / 1000.0, len(gaps), _lw / 1000.0,
                         "一致" if (_dn == len(gaps) and abs(_dw - _lw) < 0.5)
                         else "**不一致, 面板与日志脱钩了**"))
        _cnt = {}
        for _t in _tags_all:
            _cnt[_t] = _cnt.get(_t, 0) + 1
        _lines.append("# ★ 内容配比(判断这轮和上一轮能不能比): "
                      + " · ".join("%s %.1f%%" % (_k, 100.0 * _v / max(1, len(_tags_all)))
                                   for _k, _v in sorted(_cnt.items(), key=lambda kv: -kv[1])))
        # ---- 最慢的几次「冷字号测量」----
        # ⚠️ 逐帧那行只印得出「字号21.7(1次/1测)」—— 知道"一次冷测量烧了 21.7 毫秒",
        #    但**不知道是哪个字号、哪个标签**。修法完全取决于这个(补预热表? 还是从标签那边修?),
        #    所以把 (毫秒, 字号, bold, 标签) 原样印出来, 让下一份日志直接给答案。
        # ⚠️ 同样**不包在 try 里**: 包了出错就被静默吞掉, 玩家只看到"少了一行"。
        if _COLD_FS:
            # ⚠️ 字号印**原值 6 位**、并带上"离最近的同 bold 预热档差多少":
            #    差值 ~0 ⇒ 只是四舍五入对不上(预热 round 过); 差值大 ⇒ 基准不是同一个数。
            #    只印 4 位小数的话这两种情况长得一模一样。
            _lines.append("# 最慢的冷字号测量(>%.0f 毫秒才算, 含「离最近预热档的差」): %s"
                          % (COLD_FS_MIN_MS,
                             " · ".join("%.1fms fs=%r bold=%d [%s] 基准%.4f 倍率%.4f "
                                        "最近预热档%r 差%.4f 预热时量过=%s"
                                        % (_c[0], _c[1], int(_c[2]), _c[3],
                                           (_c[7] if len(_c) > 7 else 0.0),
                                           ((_c[1] / _c[7]) if len(_c) > 7 and _c[7] > 0 else 0.0),
                                           _c[4], _c[5], "是" if _c[6] else "**否**")
                                        for _c in _COLD_FS)))
        else:
            _lines.append("# 冷字号测量: **一次都没有**(全部命中了预热表)")
        # ⚠️ 预热项数必须**明着印出来**: Kivy 的 SDL2 字体缓存上限是 **64**(桌面实测),
        #    超了就是"预热自己把自己挤掉"—— 表里有、也量过, 运行期还是冷, 而且**不报错**。
        # ⚠️ `or ()` 不能省: 这两个表的初始值是 `None`(预热链还没跑到就算不出来),
        #    直接 `len()` 会 TypeError —— 而外层 `_copy_bench_log` 会把异常吞成**空串**,
        #    玩家看到的是"没有可复制的数据"。
        # ---- 采样期屏幕刷新率**变过没有** ----
        # ⚠️ 这一段存在的理由: 玩家报「**发射的时候必然会降低帧率上限**」。
        #    屏幕上真降频的话, 那一段的帧间隔会**自然变长** —— 既可能被误判成"卡顿",
        #    又会把"中位帧间隔"(新判据的分母)往大推。**先把事实印出来再谈归因。**
        if _BENCH_HZ:
            _hz_vals = sorted({_k[1] for _k in _BENCH_HZ})
            _by = {}
            for (_st, _h), _c in _BENCH_HZ.items():
                _by.setdefault(_h, {})
                _by[_h][_st] = _by[_h].get(_st, 0) + _c
            if len(_hz_vals) <= 1:
                _lines.append("# 屏幕刷新率(采样期, 每 0.5 秒记一次): 恒为 %.1f Hz —— "
                              "**没变过**, 长帧与降频无关" % _hz_vals[0])
            else:
                _lines.append("# ⚠️ 屏幕刷新率(采样期)**变过**: %s"
                              % " · ".join(
                                  "%.1fHz{%s}" % (_h, " ".join(
                                      "%s %d" % (_st, _c) for _st, _c in
                                      sorted(_by[_h].items(), key=lambda kv: -kv[1])))
                                  for _h in _hz_vals))
                _lines.append("#   ← 降频那一段的帧间隔会**自然变长**(60Hz=16.7ms / 120Hz=8.3ms): "
                              "既可能被误判成卡顿, 也会把「中位帧间隔」(判据的分母)往大推")
        else:
            # ⚠️ 采不到也要**明说**。本工程反复踩过"没印"被读成"没发生" —— 屏幕上没有这一行,
            #    读数的人分不清是"刷新率没变"还是"这一栏根本没在跑"。
            _lines.append("# 屏幕刷新率(采样期): **采不到**(桌面预览 / 无权限 / `_screen_hz()` 返回 0)")
        _wc = len(winfx._FONT_WARM_HUD or ()) + len(winfx._FONT_WARM_SIZES or ())
        # ---- 字体缓存账本: 回答"冷字号到底是被谁挤掉的" ----
        # ⚠️ 判据不靠猜: **一次冷测量 = 真的开了一个 fontid**。
        #    不同字号数 > 64 ⇒ 淘汰是铁的 —— "预热时量过=是 却仍冷" 就是这么来的。
        try:
            _nopen = len(_FS_OPEN_SET)
            # ⚠️⚠️ **判据看的是"实际开过几个", 不是"我烘了几项"**: 原来这里拿 `_wc`
            #    (=预热表项数) 去比 64 ⇒ 恒印「有余量」, 而**紧跟着的下一行**印的是
            #    "开过 86 个 ⇒ 必然发生过淘汰" —— **同一份日志里两行结论相反**, 而且乐观的
            #    那一行在读者的视线更前面。占住 64 个槽位的是**实际开过的 fontid 数**。
            # ⚠️ **判据那行必须排在预热项数那一行之前**。
            _lines.append("# 字号缓存判据: 实际开过 **%d** 个不同字号"
                          " (Kivy 字体缓存上限约 64)  %s"
                          % (_nopen,
                             "**超了 —— 必然发生淘汰, 必须减总数**" if _nopen > 60
                             else ("贴着上限, 别再往上加" if _nopen >= 56 else "有余量")))
            _lines.append("# 字号预热表: %d 项 —— ⚠️ **这一行只说明「我烘了几项」, 不是判据**;"
                          " 挤爆缓存的是上面「实际开过」那个数" % _wc)
            if _FS_MUTED_N[0]:
                # ⚠️ 静音了多少次**必须印** —— 否则"账本变小了"会被读成"真的少开了"。
                _lines.append("#   （其中**报告 UI 期间静音跳过 %d 次** —— 打开「帧率曲线」弹窗"
                              "本身会开 12~13 个 fontid, 那是测量动作污染被测对象, 已剔除）"
                              % _FS_MUTED_N[0])
            _lines.append("# 字体缓存账本: 到现在一共开过 **%d** 个不同字号 (上限约 64) %s"
                          % (_nopen,
                             "**必然发生过 LRU 淘汰**" if _nopen > 60
                             else "还没撑爆"))
            # ---- 两个**普查**计数器 ----
            # 「每个 fontid 冷开了几次」—— 定"该钉几个"的唯一依据(原来只有最慢 5 条, 那是采样)。
            if _FS_COLD_CNT:
                _top = sorted(_FS_COLD_CNT.items(), key=lambda kv: -kv[1][0])[:12]
                _lines.append("# 冷开次数普查: 共 %d 个不同字号被冷开过, 总计 %d 次 ·"
                              " **复冷过的 %d 个**"
                              % (len(_FS_COLD_CNT),
                                 sum(v[0] for v in _FS_COLD_CNT.values()),
                                 sum(1 for v in _FS_COLD_CNT.values() if v[0] > 1)))
                _lines.append("#   前 12 名(字号/bold → 次数[最后调用方]): "
                              + " · ".join("%.2f%s x%d[%s]"
                                           % (k[0], "B" if k[1] else "", v[0], str(v[1])[:8])
                                           for k, v in _top))
            else:
                _lines.append("# 冷开次数普查: **一次冷开都没有**(全部命中预热表)")
            # ---- 字体"钉子" ----
            # ⚠️ **必须印** —— 钉子是"静默失效"的重灾区: 定位失败 / 断言不通过 / 超上限,
            #    三种情况下它都只是**不生效**, 而日志一片安静, 看起来和"钉住了所以没问题"
            #    一模一样。这里把"定没定到、钉了几个、失败几次、复冷几次"全摊开。
            _ps = _PIN_STAT
            _lines.append("# 字体钉子: 队列定位=%s · 钉住 **%d** 个(上限 %d) · "
                          "失败 %d 次 · 达到门槛(冷开满 %d 次)的有 %d 个 · "
                          "**因弹窗开着而跳过 %d 次**"
                          % (_PIN_WHERE[0], _ps[0], _PIN_MAX, _ps[1], _PIN_AFTER, _ps[2],
                             _PIN_POPUP_SKIP[0]))
            if _PIN_ORDER[0] is None:
                _lines.append("#   ⚠️ **钉子没生效** —— 没在对象图里认出 Kivy 的淘汰队列"
                              "(`sdl2_cache_order`)。冷字号会照旧被驱逐, 这是**已知的失效**,"
                              " 不是「没问题」。")
            elif _PIN_CAND[0] > 1:
                _lines.append("#   ⚠️ 形状命中的 list 有 **%d 个** —— 认的是先扫到的那个, **可能认错**。"
                              " 若同时看到「钉住 0 个」, 那就是认错了(不是「没有字号冷开这么多次」)。"
                              % _PIN_CAND[0])
            elif _ps[0] == 0 and _ps[2] == 0:
                _lines.append("#   本轮**没有任何字号冷开到 %d 次** ⇒ 没有可钉的对象"
                              "(这是好事, 不是失效)。" % _PIN_AFTER)
            # 「本帧冷开几次」的分布 —— 回答"一帧到底会不会开十几个"。
            # ⚠️ 这是 `(N次/M测)` 的**普查版**: 那一栏只在「字号」挤进该帧最大两个子步骤时
            #    才印(21059 帧里只印过 2 帧), 拿它当普查是采样当普查。
            if _FIT_HIST:
                _tot = sum(_FIT_HIST.values())
                _lines.append("# 帧内冷开分布: " + " · ".join(
                    "%d次 %d帧(%.2f%%)" % (_k, _v, 100.0 * _v / max(1, _tot))
                    for _k, _v in sorted(_FIT_HIST.items())))
            if _FS_OPEN:
                _lines.append("#   最近开过的 12 个(字号[调用方]): "
                              + " · ".join("%.2f[%s]" % (_x[1], str(_x[3])[:10])
                                             for _x in _FS_OPEN[-12:]))
            for _c in _COLD_FS[:2]:
                try:
                    _li = int(_c[8])
                    _before = [x for x in _FS_OPEN if x[0] < _li][-8:]
                    _n_at = len(set((round(x[1], 4), x[2]) for x in _FS_OPEN[:_li + 1]))
                    _lines.append("#   冷字号 fs=%.4f[%s]: 发生时已开过 %d 个不同字号"
                                  % (_c[1], str(_c[3])[:10], _n_at))
                    _lines.append("#     它之前最近开的 8 个: "
                                  + (" · ".join("%.2f[%s]" % (_x[1], str(_x[3])[:10])
                                                   for _x in _before) or "无"))
                except Exception:
                    pass
        except Exception:
            pass
        # ⚠️ 顺带把这两句读法写进日志 —— 下一份日志不用再回来翻源码就知道怎么读。
        _lines.append("#   读法2: 「倍率」若落在 FIT_SCALES(1.0/0.94/0.88/0.82/0.76/0.70) "
                      "之外, 那就是 v0.7.32 为了压到 64 上限以内**故意不烘**的 FIT_FINE 档 —— "
                      "**这是「要么超上限自己挤自己、要么多付一次冷开」的硬取舍, 不是漏烘。**")
        # ⚠️ 「基准 / 倍率」两栏以前只在 `_fit1` 路径上有值, **直接调 `fit_font_size` 的路径
        #    记的是上一次 `_fit1` 的残留** ⇒ 假数。现在由 `_fit_font_size_slow` 现传真值, 并加了
        #    **文本前 10 个字**。**看这一行时以文本为准。**
        _lines.append("#   读法: 「预热时量过=是」而仍然是冷 ⇒ **被 Kivy 的字体缓存挤掉了**"
                      "(warm 再多也没用, 要减字号总数); 「=否」⇒ 预热那一句被 `text_px` "
                      "自己的缓存挡掉了, 等于没烘。最近预热档的**差**若为 0 就排除"
                      "「四舍五入/基准不同」这两种解释。")

        # ⚠️ **节拍真值必须印在最前面**。理由见 `_bench_collect_diag` 里"节拍真值"那段:
        #    屏幕档位一变, 同一份代码的 1%Low 能从 83.3 掉到 42.2。这一行是"这份日志能不能
        #    和上一份比"的唯一前提 —— 放在头部第一眼就能看到。
        try:
            _lines.append("# 节拍: 屏幕 %.1fHz · 请求高刷模式 %.1fHz · Kivy上限 maxfps=%s"
                          " (Clock._max_fps=%.1f) · vsync=%s · multisamples=%s"
                          % (float(d.get("screen_hz", 0.0)), float(d.get("req_hz", 0.0)),
                             str(d.get("maxfps", "?")), float(d.get("clock_maxfps", 0.0)),
                             str(d.get("vsync", "?")), str(d.get("multisamples", "?"))))
        except Exception:
            pass
        # ---- 等屏幕(swap 阻塞) ----
        # ⚠️ **慢帧 × 文字纹理重建的交叉表** —— 这一行是"文字重排到底是不是元凶"的直接读数。
        #    `Label.texture_update` 由 Kivy 用 Clock **延后**执行(重测字形+重光栅化+重建纹理+
        #    上传), 跑在 `_frame` 外面; 所以这笔账落在"被还上"的那一帧, 而不是"改 text"的那一帧。
        #    两边比例一摆就见分晓: 慢帧里占一大半、其余帧里几乎为零 ⇒ 就是它。
        _s_n = len(_slow_idx)
        _o_n = len(gaps) - _s_n
        _s_t = sum(1 for _i in _slow_idx if tex[_i] > 0)
        _o_t = sum(1 for _i in range(len(gaps)) if _i not in _slow_idx and tex[_i] > 0)
        # ⚠️ 分母必须是**慢帧集合的真实大小**(`_s_n`), 不是 `_n1` —— 门槛上并列时
        #    `>= 门槛` 选出来的帧会比 `_n1` 多(夹具实测 20 vs 1, 印出 `20/1 (2000%)`)。
        _lines.append("# 慢帧当帧发生文字重建: %d/%d (%.0f%%)  ·  其余帧: %d/%d (%.0f%%)"
                      % (_s_t, _s_n, 100.0 * _s_t / max(1, _s_n),
                         _o_t, _o_n, 100.0 * _o_t / max(1, _o_n)))
        _lines.append("# 全程文字重建 %d 次(其中 %d 次落在慢帧上)"
                      % (sum(tex), sum(tex[_i] for _i in _slow_idx)))
        # ---- 慢帧的**相邻间隔** ----
        # ⚠️ 这一格回答的是老版交接文档 `android/jiaojie.md` **方案六**点名的问题:
        #    「若长帧呈**严格周期性**, 定位是否为**系统栏、功耗策略或合成器节拍**,
        #      而不是猜测游戏代码」。
        #    判读: 间隔的**中位 ≈ 最大**(离散小)⇒ **固定节拍** —— 改游戏代码没用;
        #          间隔**忽大忽小** ⇒ **事件驱动**(落袋/发射/结算/中奖), 该去查那些时刻。
        #    ⚠️ 用**帧号差**不用毫秒差: 帧号差直接读成"大约每 N 帧来一记", 不受帧率漂移影响。
        #    ⚠️ 慢帧少于 3 个时**不印** —— 两点之间的一个间隔说明不了周期性, 印出来是误导。
        _gap_si = sorted(_slow_idx)
        if len(_gap_si) >= 3:
            _gap_iv = sorted(_gap_si[_k + 1] - _gap_si[_k]
                             for _k in range(len(_gap_si) - 1))
            _gap_med = _gap_iv[len(_gap_iv) // 2]
            _gap_max = _gap_iv[-1]
            _gap_contig = sum(1 for _d in _gap_iv if _d <= 1)
            # ⚠️⚠️ **"连片"必须单独判**, 不能并进"整齐"里: 慢帧**连成一片**(帧号差全是 1)
            #    说明那是**一次**长停顿, 不是"每隔 N 帧来一记"的节拍 —— 两者要查的东西
            #    完全相反(一个是"那一段在干什么", 一个是"哪个系统节拍")。
            #    第一版没这一条, 夹具那 20 个连片慢帧当场被判成"固定节拍", 是错的。
            if _gap_contig * 2 >= len(_gap_iv):
                _gap_v = ("**多数慢帧连成一片** ⇒ 那是**一次**长停顿(不是周期性): "
                          "去看那一段在干什么")
            elif _gap_max <= max(2, int(_gap_med * 1.5)):
                _gap_v = ("间隔整齐 ⇒ **固定节拍**(系统栏/功耗策略/合成器), "
                          "改游戏代码没用")
            else:
                _gap_v = "间隔忽大忽小 ⇒ **事件驱动**(落袋/发射/结算/中奖), 去查那些时刻"
            _lines.append(
                "# 慢帧相邻间隔(帧号差): 中位 %d · 最小 %d · 最大 %d · 连片 %d/%d"
                "(共 %d 个慢帧)  ← %s"
                % (_gap_med, _gap_iv[0], _gap_max, _gap_contig, len(_gap_iv),
                   len(_gap_si), _gap_v))
        # ⚠️ **文字纹理缓存**的命中率。这一行是"缓存到底有没有生效"的唯一判据:
        #    命中 = 第一趟(量宽高)、第二趟(`填纹` 真光栅化)和纹理上传**三样全跳过**。
        #    ⚠️ 命中率若是 0, 说明指纹每次都不一样(最常见的原因是 `text_size` 在变) ——
        #       那就要回去看 `_texex_key` 的字段表, 别以为"加了缓存"就万事大吉。
        try:
            _th = int(d.get("texex_hit", 0) or 0)
            _tm = int(d.get("texex_miss", 0) or 0)
            if not _TEXEX_ON:
                # ⚠️ 必须明说"本版关着" —— 否则那行会印成"命中率 0%", 看着像缓存失效,
                #    而实际是根本没开。**一个会骗人的面板比没有面板更坏。**
                _lines.append("# 文字纹理缓存: **本版关闭**(见 `_TEXEX_ON` 处的证据: 逐像素比对"
                              "验出命中内容张冠李戴, 已停用)")
            else:
                _lines.append("# 文字纹理缓存: 命中 %d 次 · 未命中 %d 次(命中率 %.0f%%)"
                              "  ← 命中 = 两趟渲染 + 纹理上传全部跳过"
                              % (_th, _tm, 100.0 * _th / max(1, _th + _tm)))
        except Exception:
            pass
        try:
            _by = d.get("texupd_by") or []
            if _by:
                # ⚠️ 采集端只留前 4 名, 所以这里**必须把"其余"和"合计"一起印出来** ——
                #    否则读者拿这行去对上面的"全程文字重建 N 次"会永远差一截, 而不知道该信谁。
                _rest = int(d.get("texupd_rest", 0) or 0)
                _bits = ["%s %d" % (n, c) for n, c in _by]
                if _rest > 0:
                    _bits.append("其余%d类合计 %d" % (max(1, int(d.get("texupd_tags", 0) or 0)
                                                          - len(_by)), _rest))
                _lines.append("# 重建来源: " + " · ".join(_bits)
                              + "  [合计 %d]" % (sum(int(c) for _, c in _by) + _rest))
            # 发声 / 震动单次最慢 —— 覆盖另一条假设("关掉音效 1%low 就回升", 而关音效会
            # 连震动一起关掉, 两条分不开)。这两组埋点一直在跑, 只是很多年没显示了。
            _lines.append("# 发声 %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒"
                          % (int(d.get("snd_n", 0)), float(d.get("snd_worst", 0.0)),
                             float(d.get("snd_sum", 0.0))))
            _lines.append("# 震动 %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒"
                          % (int(d.get("vib_n", 0)), float(d.get("vib_worst", 0.0)),
                             float(d.get("vib_sum", 0.0))))
        except Exception:
            pass
        _lines.append("# 各阶段: 帧数 / 占总帧 / 中位ms / p99ms / 长停顿数 / 该阶段长停顿率")
        for _s in STAGE_ORDER:
            _v = [gaps[_i] for _i in range(len(gaps)) if tags[_i] == _s]
            if not _v:
                continue
            _vs = sorted(_v)
            _slow = sum(1 for _x in _v if _x >= _thr)
            _lines.append("#   %s %d / %.1f%% / %.2f / %.2f / %d / %.2f%%"
                          % (_s, len(_v), 100.0 * len(_v) / len(gaps),
                             _vs[len(_vs) // 2],
                             _vs[min(len(_vs) - 1, int(len(_vs) * 0.99))],
                             _slow, 100.0 * _slow / len(_v)))
        # 最慢三帧的 `_frame` **子步骤** —— 用来回答"这 20 毫秒里我们自己的代码占多少"。
        # ⚠️ 只统计 `_frame` **内部**被 `_brk_wrap` 包过的那几处(板面/重掷/字号/装杯/发射)。
        #    这一格很小而整帧很大 ⇒ 钱花在 `_frame` 外面(Kivy 延后的文字重排 / 渲染 / 其它
        #    Clock 回调), 不是我们的代码 —— 这是分流的那一刀。
        _fr = list(getattr(self, "_bench_frames", []) or [])
        # ---- 等屏幕(swap 阻塞) ----
        # ⚠️ 这一格回答的是"主线程没烧 CPU 的那些慢帧, 到底在等谁"。Kivy 的绑定回调 `on_flip`
        #    跑在真正 swap **之前**, 所以帧间隔里那一段"等屏幕"以前只混在总数里, 从没被单独量过。
        #    `time.thread_time()` 也看不见它 —— 阻塞在驱动 fence 上**不算 CPU 时间**。
        # ⚠️ 必须排在 `_fr` 之后(下面要用它)。
        _sw = [float(_r[10]) if len(_r) > 10 else 0.0 for _r in _fr] if _fr else []
        if len(_sw) == len(gaps) and any(_sw):
            _sw_s = sorted(_sw)
            _s_sw = sorted(_sw[_i] for _i in _slow_idx)
            _o_sw = sorted(_sw[_i] for _i in range(len(gaps)) if _i not in _slow_idx)
            _lines.append("# 等屏幕(swap 阻塞): 中位 %.2f 毫秒 · 最慢 %.2f 毫秒 · 累计 %.0f 毫秒"
                          " (占窗口 %.1f%%)"
                          % (_sw_s[len(_sw_s) // 2], _sw_s[-1], sum(_sw),
                             100.0 * sum(_sw) / max(1.0, sum(gaps))))
            # ⚠️ 判据**两个方向都要写**, 不能只往"在等屏幕"引 —— 桌面实测就撞到过反例:
            #    桌面 vsync 下大部分帧 swap 只要 0.3 毫秒, 但**排到队的那一帧要等 15.9 毫秒**;
            #    那一帧"等屏幕高"是因为它**本来就来晚了、撞上了队列**, 不是被屏幕拖慢的。
            #    所以这一格必须和下面那句「慢帧 CPU 账」**一起读**:
            #      慢帧 swap 高 **且** 主线程 CPU 也高  ⇒ 我们自己交晚了, 该去砍 CPU;
            #      慢帧 swap 高 **但** 主线程 CPU 很低  ⇒ 在等屏幕, 优化代码没用。
            _lines.append("#   慢帧当帧 等屏幕 中位 %.2f 毫秒  ·  其余帧 中位 %.2f 毫秒"
                          "  ← 必须配合上面的「慢帧 CPU 账」读: CPU 也高=我们交晚了;"
                          " CPU 低=真在等屏幕"
                          % (_s_sw[len(_s_sw) // 2] if _s_sw else 0.0,
                             _o_sw[len(_o_sw) // 2] if _o_sw else 0.0))
        if len(_fr) == len(gaps):
            _bits = []
            for _i in _order[:3]:
                # ⚠️ 必须兜底: `[9]` 是 `_on_flip` 存的 `((毫秒, 标签), ...)`, 但**形状意外时
                #    绝不能让整份日志消失**(外层会把异常吞成空串, 玩家只看到"没有可复制的数据")。
                _b = _fr[_i][9] if len(_fr[_i]) > 9 else ()
                # 「字号」那一格**必须带上分解**, 否则只看到一个 50.7 毫秒的数字,
                # 分不出"一次走完阶梯+二分"还是"一帧里十个标签同时换字"。
                _fn = _fr[_i][11] if len(_fr[_i]) > 11 else (0, 0)
                try:
                    _parts = []
                    for _v, _k in (_b or ()):
                        if _k == "字号":
                            _parts.append("字号%.1f(%d次/%d测)" % (_v, _fn[0], _fn[1]))
                        else:
                            _parts.append("%s%.1f" % (_k, _v))
                    _s = " / ".join(_parts) or "无"
                except Exception:
                    _s = "无"
                _bits.append("帧%d %.1fms[%s]" % (_i, gaps[_i], _s))
            _lines.append("# 最慢三帧的子步骤(仅 _frame 内部): " + " · ".join(_bits))
        # ---- 逐帧 CPU 账: 把"慢"分流成 我们的代码 / Kivy / 在等 ----
        # 这三样**每一帧本来就在采**, 只是以前没印。一批 11~12ms 的慢帧**文字重建为 0**,
        # 靠"有没有重建"解释不了它们, 必须靠这三个数分流: 自算大=我们的代码;
        # 主线程大而自算小=Kivy 渲染/延后重排; 两个都小=在等(GC/显卡/驱动)。
        # ⚠️ 桌面 `thread_time` 精度只有 15.6ms, 这台机器上主线程那一列基本是台阶
        #    —— 分流**只能在真机上看**。
        _self_ms, _thr_ms, _top1, _snd_n, _vib_n = [], [], [], [], []
        for _i in range(len(gaps)):
            _rec = _fr[_i] if _i < len(_fr) else ()
            _self_ms.append(float(_rec[5]) if len(_rec) > 5 else 0.0)
            _thr_ms.append(float(_rec[7]) if len(_rec) > 7 else 0.0)
            # 发声/震动是**逐帧**计的, 而且发声那个计数在**节流闸门之后** —— 数的是"真的播了",
            # 不是"想播"。所以它能直接回答玩家问的那句「卡的那一下是不是正在响/正在震」。
            _snd_n.append(int(_rec[3]) if len(_rec) > 3 else 0)
            _vib_n.append(int(_rec[4]) if len(_rec) > 4 else 0)
            _t1 = ""
            try:
                _b = _rec[9] if len(_rec) > 9 else ()
                if _b:
                    _v, _k = max((v, k) for v, k in _b)
                    _t1 = "%s%.1f" % (_k, _v)
            except Exception:
                _t1 = ""
            _top1.append(_t1)
        # 发声/震动与慢帧的相关性 —— 与上面"文字重建"那一条同一个形状, 便于横向比。
        # ⚠️ 判据要**两边都印**(慢帧 vs 其余帧): 只印"慢帧里 40% 在发声"读不出结论,
        #    因为发声本来就密(4 次/秒), 得看它是不是**超配**。
        _s_n = len(_slow_idx)
        _o_n2 = len(gaps) - _s_n
        for _nm, _arr in (("发声", _snd_n), ("震动", _vib_n)):
            _s_hit = sum(1 for _i in _slow_idx if _arr[_i] > 0)
            _o_hit = sum(1 for _i in range(len(gaps))
                         if _i not in _slow_idx and _arr[_i] > 0)
            _lines.append("# 慢帧当帧在%s: %d/%d (%.0f%%)  ·  其余帧: %d/%d (%.0f%%)"
                          % (_nm, _s_hit, _s_n, 100.0 * _s_hit / max(1, _s_n),
                             _o_hit, _o_n2, 100.0 * _o_hit / max(1, _o_n2)))
            # ⚠️ **滞后窗口**那一版也必须印: 发声/震动的计数记在**投递那一刻**, 真正的 JNI 调用
            #    发生在那之后 —— 而 JNI 会在 JVM 里分配对象, JVM 的 GC 是**停全世界**的
            #    (把主线程一起按住)。若卡是这么来的, 它会出现在发声的**下一两帧**, 而不是当帧
            #    —— 只看当帧必然漏掉。
            _lag = 3
            _s_lag = sum(1 for _i in _slow_idx
                         if any(_arr[_j] > 0 for _j in range(max(0, _i - _lag), _i)))
            _o_lag = sum(1 for _i in range(len(gaps))
                         if _i not in _slow_idx and _arr[_i] == 0
                         and any(_arr[_j] > 0 for _j in range(max(0, _i - _lag), _i)))
            _o_lag_n = sum(1 for _i in range(len(gaps)) if _i not in _slow_idx and _arr[_i] == 0)
            _lines.append("# 慢帧**前%d帧内**有过%s: %d/%d (%.0f%%)  ·  其余帧: %d/%d (%.0f%%)"
                          % (_lag, _nm, _s_lag, _s_n, 100.0 * _s_lag / max(1, _s_n),
                             _o_lag, _o_lag_n, 100.0 * _o_lag / max(1, _o_lag_n)))
        # ⚠️ 必须带 `_slow_idx` 非空判断: 门槛改成"中位帧×2"之后, 帧时间分布集中时
        #    **可能一帧都不命中** —— 旧门槛"最慢1%"天然保证至少命中 1 帧, 这个隐含保障被改掉了
        #    ⇒ `_sg` 为空 ⇒ 下面 `_sg[len(_sg)//2]` IndexError。
        if _slow_idx and (any(_self_ms) or any(_thr_ms)):
            _sg = sorted(gaps[_i] for _i in _slow_idx)
            _ss = sorted(_self_ms[_i] for _i in _slow_idx)
            _st = sorted(_thr_ms[_i] for _i in _slow_idx)
            _gm = _sg[len(_sg) // 2]
            _sm = _ss[len(_ss) // 2]
            _tm = _st[len(_st) // 2]
            _lines.append("# 慢帧 CPU 账(中位): 帧间隔 %.2f / `_frame` 自算 %.2f / 主线程 %.2f 毫秒"
                          % (_gm, _sm, _tm))
            # ⚠️ 判据按**主线程占帧长的比例**分, 不是"自算是否 >=1ms"。
            #    第一版用的就是后者, 结果把 17 个"主线程只跑了 1.9 毫秒、帧却走了 11 毫秒"的帧
            #    也算进了"`_frame` 外面", 读起来像"钱花在 Kivy 上" —— 而那些帧**主线程
            #    大部分时间根本没在跑**(在等节拍/合成器/调度)。两者要修的东西完全不同。
            _n_wait = sum(1 for _i in _slow_idx if _thr_ms[_i] < 0.30 * gaps[_i])
            _n_half = sum(1 for _i in _slow_idx
                          if 0.30 * gaps[_i] <= _thr_ms[_i] < 0.70 * gaps[_i])
            _n_busy = len(_slow_idx) - _n_wait - _n_half
            _lines.append("#   主线程没在跑(<30%%帧长) %d 帧 = 帧在等, 不是算出来的 · 半跑半等 %d 帧"
                          " · 一直在算(>=70%%) %d 帧" % (_n_wait, _n_half, _n_busy))
            _all_ratio = sorted(_thr_ms[_i] / max(0.01, gaps[_i]) for _i in range(len(gaps)))
            _br_ = _all_ratio[len(_all_ratio) // 2] if _all_ratio else 0.0
            _lines.append("#   常态帧主线程只占帧长 %.0f%%(拿它当尺子): 慢帧明显低于这个 = 在等;"
                          " 明显高于 = 真在算" % (100.0 * _br_))
        # ---- CPU 调频状态 ----
        # ⚠️ 这一行是上面**所有** CPU 数字的前提。玩家在跑分时用第三方工具看到: 采样窗口前期
        #    CPU 只跑 1.1GHz, 到后期纯 CPU 的物理跑分才升到 4.5GHz。而"主线程ms"量的是
        #    `time.thread_time()` = **真实 CPU 秒数**, 主频差 4 倍 ⇒ 同一个函数量出来差 4 倍。
        try:
            _fn = int(d.get("cpufreq_n", 0) or 0)
            if _fn > 0:
                _fm = float(d.get("cpufreq_min", 0.0) or 0.0)
                _fp = float(d.get("cpufreq_p50", 0.0) or 0.0)
                _fx = float(d.get("cpufreq_max", 0.0) or 0.0)
                _fc = float(d.get("cpufreq_cap", 0.0) or 0.0)
                _lp = float(d.get("cpufreq_low_pct", -1.0))
                # ⚠️ **代表值用平均**。中位仍印在括号里 —— 它是**分布的一项**, 与最低/最高并列,
                #    不再冒充代表值。
                # ⚠️ **平均取不到就印「没采到」, 绝不拿中位数顶**(那正是"印假数")。
                _fmn = float(d.get("cpufreq_mean", 0.0) or 0.0)
                _fm_txt = ("**平均 %.0fMHz**" % _fmn) if _fmn > 0 else "平均 **没采到**"
                _lines.append("# CPU 频率(**渲染窗口**那一段, 不是跑分段): %s · "
                              "最低 %.0f · 最高 %.0f · 上限 %.0fMHz · (中位 %.0f)%s"
                              % (_fm_txt, _fm, _fx, _fc, _fp,
                                 ("  ·  **低于上限一半的采样占 %.0f%%**" % _lp) if _lp >= 0 else ""))
                _lines.append("#   ⚠️ 这一段天生偏低: 渲染窗口里应用大部分时间在**等 vsync**,"
                              " 调频器据此判它很闲。要看「跑分时跑到多少」请看上面那行"
                              "「跑分那段的 CPU 平均频率」。")
                # ⚠️ 判据: 中位远低于上限 ⇒ 采样期全程低频, 那 `主线程ms` 是**被主频放大过的**,
                #    不能拿去和其他跑分比; 反过来, 砍掉同样的工作量在低频下**省下的墙钟更多**
                #    —— 所以低频并不是"优化没用", 是"优化更值"。
                _lines.append("#   读法: 中位远低于上限 = 采样期一直低频跑(调频器按负载升频, 而本应用"
                              "大部分时间在等 vsync ⇒ 它看着很闲)。此时 `主线程ms` 被主频放大,"
                              " 同一份代码在不同跑分里会差好几倍。")
        except Exception:
            pass
        # ---- 已采未印的那几格 ----
        # ⚠️ 为什么补: 真机日志里有两段 0.8 秒的低谷; 而低谷里飞行帧的**主线程 CPU 占比与
        #    全局一模一样(34%)**, 只是**绝对 CPU 涨了 20%**(2.05→2.49ms)、帧长同步涨 20%。
        #    也就是说那段**不是被外面挡住, 是我们自己每帧多干了 20% 的活**。可"多干的是什么"
        #    这份日志一个字都没有 —— 而 GC 次数/耗时、进程态 utime/stime、后端是谁、这几个数
        #    `_bench_collect_diag` **早就采好了, 只是从来没引用过它们**。纯打印, 零行为改变。
        try:
            _gc_n = int(d.get("gc_n", 0) or 0)
            _u = float(d.get("utime_ms", -1.0))
            _s = float(d.get("stime_ms", -1.0))
            _cpf = float(d.get("cpu_per_frame", 0.0) or 0.0)
            if _u >= 0.0 and _s >= 0.0:
                _lines.append("# 进程态: 用户态 %.0f 毫秒 · 内核态 %.0f 毫秒(内核占 %.0f%%)"
                              "  ·  每帧全进程 CPU 平均 %.2f 毫秒"
                              % (_u, _s, 100.0 * _s / max(1.0, _u + _s), _cpf))
                # ⚠️ 读法: 内核占比高 ⇒ JNI/Binder/socket 写/文件写回这一族;
                #    用户态占绝对多数 ⇒ 纯 Python 的 CPU 竞争(GIL)。
            _lines.append("# GC: %d 次 · 累计 %.1f 毫秒 · 单次最慢 %.1f 毫秒(gen%s)  ·  后端 %s"
                          % (_gc_n, float(d.get("gc_total", 0.0) or 0.0) * 1000.0,
                             float(d.get("gc_worst", 0.0) or 0.0) * 1000.0,
                             str(d.get("gc_worst_gen", -1)), str(d.get("backend", "?"))))
            # ⚠️ **内存冻结必须落进日志**。这一行原来**只在成绩面板上显示**, txt 里没有
            #    ⇒ **离线看日志时无从判断**。实测代价: 四轮日志里有一轮撞到 **21.2 毫秒的 gen-2**
            #    (占那一轮最慢帧的全部), 而账面上它与另外三轮看不出任何差别。
            # ⚠️ **`gc_frozen == 0` 也要印**(印成"没冻结"), 不能 `if frozen:` 就跳过 ——
            #    那正是本仓库栽过的"静默": 冻结链没跑到/抛了异常时, 日志一片安静, 看起来
            #    和"冻结成功所以没问题"一模一样。
            _fz = int(d.get("gc_frozen", 0) or 0)
            if _fz:
                _lines.append("# 内存冻结: 已冻结 %d 个常驻对象 · 强制全量回收 冻结前 %.1f → 冻结后 %.1f 毫秒"
                              % (_fz, float(d.get("gc_frz_before", 0.0) or 0.0),
                                 float(d.get("gc_frz_after", 0.0) or 0.0)))
            else:
                _lines.append("# 内存冻结: **没冻结**(gc_frozen=0) —— 冻结链没跑到或抛了异常,"
                              " 常驻对象仍被 gen-2 全量回收反复扫")
            # JNI: 主线程那一笔才是"卡我们"的; 后台那一笔只说明工作线程在忙。
            _jn = int(d.get("jni_n", 0) or 0)
            if _jn or float(d.get("ui_n", 0) or 0):
                _lines.append("# JNI: 发声 %d 次 · 主线程累计 %.1f / 最慢 %.1f 毫秒"
                              "  ·  后台累计 %.1f / 最慢 %.1f 毫秒"
                              "  ·  界面调用 %d 次 / 最慢 %.1f 毫秒  ·  失败 %d 次"
                              % (_jn, float(d.get("jni_main_sum", 0.0) or 0.0),
                                 float(d.get("jni_main_worst", 0.0) or 0.0),
                                 float(d.get("jni_bg_sum", 0.0) or 0.0),
                                 float(d.get("jni_bg_worst", 0.0) or 0.0),
                                 int(d.get("ui_n", 0) or 0),
                                 float(d.get("ui_worst", 0.0) or 0.0),
                                 int(d.get("jni_err", 0) or 0)))
            _w20 = d.get("w20_pos") or []
            if _w20:
                _lines.append("# 最差20帧距上次发射的帧数(<=20 帧 = 发射后 0.33 秒内): %d 个"
                              "  ·  全部位次 %s"
                              % (int(d.get("w20_near", -1)), str([int(x) for x in _w20])[:110]))
            _cfg_n = int(d.get("cfg_n", 0) or 0)
            if _cfg_n:
                _lines.append("# 配置写盘: %d 次 · 累计 %.1f / 最慢 %.1f 毫秒"
                              % (_cfg_n, float(d.get("cfg_sum", 0.0) or 0.0),
                                 float(d.get("cfg_worst", 0.0) or 0.0)))
        except Exception:
            pass
        # ⚠️ 新列**只能加在末尾**: 前面几列的位置被 `_bench_frames` 的下标和外部脚本按号取,
        #    插在中间会让旧解析器静默错位(比报错更难发现)。
        _adv = [float(_r[10]) if len(_r) > 10 else 0.0 for _r in _fr] if _fr else []
        if len(_adv) != len(gaps):
            _adv = [0.0] * len(gaps)
        # ---- 「差额律」三列(2026-09-20) ----
        # 病根: 真机 14 条慢帧里有 **8 条对不平** —— `dt − 主线程 − 等屏幕` 差出稳定的
        # 4.1~5.5ms, 而这笔账**一直没进过仪表**(上一轮是拿纸笔在
        # `android/temp/_real_dev_slow.txt` 上手算出来的, 手算完才发现是什么)。
        # 手算结论: `差_i + (主线程+等屏幕)_{i−1} ≈ 常数`, 而 Kivy `clock.py::_check_ready`
        # 的睡眠律给出这个常数 = `1/cap − (4/5)·res = (11/15)/cap`
        # (`res = Clock.get_resolution() = 1/(3·cap)`)。
        # ⇒ 那一大块缺口**很可能就是 Kivy 自己按公式睡掉的**, 不是未知开销。
        # ⇒ 这三列就是用来**判死/判活**这条律的(判据写在下面那条 `★差额律` 里):
        #     ① `差`            = dt − 主线程 − 等屏幕
        #     ② `上一帧body`     = (主线程 + 等屏幕)_{i−1}  ← 用来验 ①+② ≈ T
        #     ③ `别的线程CPU`    = 全进程 CPU − 本线程 CPU  ← **独立**的第二假说(GIL 争用):
        #        大 ⇒ 后台 Python 线程在抢 GIL; ≈0 ⇒ 那条假说当场出局。
        # ⚠️ 三列都是**已有采样的算术**, 不新增任何逐帧开销。
        _pc_ms = [float(_r[2]) if len(_r) > 2 else 0.0 for _r in _fr] if _fr else []
        if len(_pc_ms) != len(gaps):
            _pc_ms = [0.0] * len(gaps)
        _body = [_thr_ms[_i] + _adv[_i] for _i in range(len(gaps))]
        _gap_ms = [gaps[_i] - _body[_i] for _i in range(len(gaps))]
        _othr = [_pc_ms[_i] - _thr_ms[_i] for _i in range(len(gaps))]
        _prev_body = [_body[_i - 1] if _i else 0.0 for _i in range(len(gaps))]
        try:
            _cap = float(getattr(Clock, "_max_fps", 0.0) or 0.0)
        except Exception:
            _cap = 0.0
        _T_ms = ((11.0 / 15.0) / _cap * 1000.0) if _cap > 0 else 0.0
        if _T_ms > 0 and len(gaps) >= 8:
            _pair = sorted(_gap_ms[_i] + _body[_i - 1] for _i in range(1, len(gaps)))
            _pm = _pair[len(_pair) // 2]
            _lines.append("# ★差额律: Kivy上限 %.1f ⇒ 睡眠常数 T=(11/15)/cap = %.3f 毫秒 · "
                          "实测「差_i + 上一帧body」中位 %.3f 毫秒(样本 %d)"
                          % (_cap, _T_ms, _pm, len(_pair)))
            _lines.append("#   ← 两者接近 ⇒ 那笔 4~5 毫秒的缺口就是 **Kivy 主动睡掉的**"
                          "(不是未知开销, 改 Python 没用); 明显不接近 ⇒ 缺口另有其人, "
                          "去看「别的线程CPU」与「等屏幕」两列")
        if gaps:
            _oth = sorted(_othr)
            _lines.append("# 别的线程烧的 CPU(全进程 − 本线程, 逐帧): 中位 %.2f · 最大 %.2f 毫秒"
                          "  ← 明显 >0 ⇒ 后台 Python 线程在抢 GIL(发声 drain / 烘焙 / "
                          "主频采样 / 落盘); ≈0 ⇒ 这条假说出局"
                          % (_oth[len(_oth) // 2], _oth[-1]))
        # ---- ★1%Low 的**可重复性**(2026-09-20) ----
        # 病根: `1%Low = 最慢 1% 帧间隔的算术平均`, 而 1900 帧的窗口里 1% 桶**只有 19 帧**
        # ⇒ **单帧离群就能支配它**。模拟器实测(`temp/_emu_analyze.py`, 1901 帧):
        #     剔掉最慢 **1** 帧(占 0.05%) ⇒ 1%Low **24.0 → 51.0**, 而平均帧只动 0.8
        #     5 个子窗口各自的 1%Low = 50.1 / 51.8 / 51.1 / **6.2** / 54.2  (极差 200%)
        # ⇒ **单点值不可比**。所以这里必须**同时报区间**, 否则读日志的人会把噪声当信号。
        # ⚠️ 用**块自举**不用普通自举: 帧间隔有 lag-1 自相关(上一帧慢 ⇒ 这一帧睡得少),
        #    普通自举会把区间算**窄**(2026-09-19 那轮发散的 W1 也点名过这一条)。
        # ⚠️ 固定种子 ⇒ 同一份日志每次算出来**一样**, 不然它自己又变成一个噪声源。
        # ⚠️ 迭代压到 200 次: 这段跑在**主线程**(玩家点「保存」那一刻), 不能吃掉一秒以上。
        try:
            # ⚠️ 门槛 100 而不是 200: 夹具只有 100 帧, 门槛设高了这段在门禁里**永远不跑**
            #    —— 那就是一条**空转的闸**(本工程栽过)。门槛只需保证"块数 ≥ 2"。
            if len(gaps) >= 100:
                import random as _rnd
                _r = _rnd.Random(20260920)
                _B = 50
                _nb = len(gaps) // _B
                _bs = []
                for _ in range(200):
                    _s = []
                    for _ in range(_nb):
                        _s0 = _r.randrange(0, len(gaps) - _B)
                        _s.extend(gaps[_s0:_s0 + _B])
                    _n1b = max(1, int(len(_s) * 0.01))
                    _sb = sorted(_s)[-_n1b:]
                    _sm = sum(_sb)
                    _bs.append(1.0 / (_sm / _n1b) if _sm > 0 else 0.0)
                _bs.sort()
                # ⚠️ **长停顿帧数必须跟分数印在一起**(方案处方 c): 模拟器实测 ——
                #    两轮同一份 APK, 平均帧只差 1.2%, 而 1%%Low 差 2.16 倍(24.0 vs 51.9),
                #    差别全在"这一轮有没有出现那一帧离群"。分开印的话读的人只会看分数。
                _md = sorted(gaps)[len(gaps) // 2]
                _nlong = sum(1 for _x in gaps if _x >= 2.0 * _md)
                _lines.append("# ★1%%Low 的可重复性(块自举 200 次, 块长 50): 90%% 区间 = "
                              "%.1f ~ %.1f 帧/秒 · **本轮长停顿(>=2x中位) %d 帧**"
                              "  ← **区间宽 或 长停顿>0 = 这一轮的单点值不可比**"
                              % (_bs[int(len(_bs) * 0.05)], _bs[int(len(_bs) * 0.95)],
                                 _nlong))
                # 子窗口四等分 —— 同一轮**内部**稳不稳, 一眼能看出来
                _q = len(gaps) // 4
                _qs = []
                for _i4 in range(4):
                    _seg = gaps[_i4 * _q:(_i4 + 1) * _q]
                    if len(_seg) < 20:
                        continue
                    _n4 = max(1, int(len(_seg) * 0.01))
                    _s4 = sorted(_seg)[-_n4:]
                    _sv = sum(_s4)
                    _qs.append(1.0 / (_sv / _n4) if _sv > 0 else 0.0)
                if _qs:
                    _lines.append("#   子窗口(四等分)各自的 1%%Low: %s  ← 差得多 = 这一轮"
                                  "**内部就不稳**, 更别拿去跟别的轮比"
                                  % " · ".join("%.1f" % _x for _x in _qs))
                _lines.append("#   ⚠️ 处方: **A/B 一律 >=3 轮取中位**, 或把采样窗口拉长"
                              "(最慢 1%% 的帧数从 ~19 变成上百帧); 别拿单轮涨跌当结论")
        except Exception:
            pass
        # ⚠️ 新列**只能加在末尾**: 前面几列的位置被 `_bench_frames` 的下标和外部脚本按号取,
        #    插在中间会让旧解析器静默错位(比报错更难发现)。
        _lines.append("# 每行: 帧间隔毫秒,阶段,文字重建,_frame自算ms,主线程ms,发声,震动,最大子步骤,"
                      "等屏幕ms,差ms,上一帧body_ms,别的线程CPUms")
        _lines.extend("%.2f,%s,%d,%.2f,%.2f,%d,%d,%s,%.2f,%.2f,%.2f,%.2f"
                      % (g, t, x, s, m, sn, vb, b, w, gp, pb, ot)
                      for g, t, x, s, m, sn, vb, b, w, gp, pb, ot
                      in zip(gaps, tags, tex, _self_ms, _thr_ms, _snd_n, _vib_n, _top1, _adv,
                             _gap_ms, _prev_body, _othr))
        return "\n".join(_lines) + "\n"

    def _copy_bench_log(self, btn=None):
        """把逐帧日志复制到剪贴板。失败**必须说出来** —— 静默失败等于让玩家白等一次出包。"""
        txt = ""
        try:
            txt = self._bench_frame_log()
        except Exception:
            txt = ""
        if not txt:
            if btn is not None:
                btn.text = '没有可复制的数据'
            return False
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(txt)
            _ok = True
        except Exception:
            _ok = False
        # ⚠️ `return False` 必须在 `btn is not None` **外面** —— 否则无参调用(保存那条路
        #    就是这么调的)会一路落到 `return True`: 玩家被告知"已复制到剪贴板",
        #    而剪贴板其实是空的。
        if not _ok:
            if btn is not None:
                btn.text = '复制失败(剪贴板不可用)'
            return False
        if btn is not None:
            _n = txt.count("\n") + 1
            btn.text = '已复制 %d 行' % _n
            Clock.schedule_once(lambda _d: setattr(btn, 'text', '复制逐帧日志'), 2.0)
        return True

    def _power_log_text(self, pw=None, extra=None):
        """把功率的**原始采样**拼成一份可导出的 txt。

        ⚠️ 为什么要导**原始值**: 落盘的 `power_series` 已经是**换算成 W 且 round 到 2 位**
           的数 —— 而"那个尖峰是真读数还是采样毛刺"这类问题, 只能拿**原始整数电流 +
           当时的电压**去回答。这份 txt 就是那批原始值, 一行一格。

        ⚠️⚠️ **表头里那两行"极值点游程"是这份文件的重点**: 底层电量计约 0.96 秒才刷新
           一次, 而我们是 5Hz 采样 ⇒ **一个真实读数应当连续占约 5 格**。所以"最高值
           连续占了几格"直接回答了"它像不像真的"。而且它**只用 W 序列就能算** ——
           翻历史导出的老记录(没有原始电流)也能得到这个数。

        ⚠️ `pw` / `extra` 供**翻历史**那条路用(那时的数据来自记录, 不在 `self._hp_*` 里);
           都不传就退回现场状态。
        返回字符串; 没有任何功率数据时返回 ""。
        """
        _e = extra or {}
        _pw = list(pw if pw is not None
                   else (getattr(self, "_hp_power_series", None) or []))
        if not _pw:
            return ""
        # ⚠️ 取值必须**按"键在不在"判**, 不能用 `or` —— 翻历史那条路会显式传空列表
        #    表示"这条记录里没有这个数据"; 用 `or` 的话空列表会被当成"没传",
        #    于是**退回 `self._hp_*`(上一局的残值)**, 导出一份张冠李戴的 txt。
        def _pick(_k, _attr):
            if _k in _e:
                return list(_e[_k] or [])
            return list(getattr(self, _attr, None) or [])

        _pt = _pick("pt", "_hp_power_times")
        _raw = _pick("raw", "_hp_power_raw")
        _mv = _pick("mv", "_hp_power_mv")
        _m = _e["meta"] if "meta" in _e else (getattr(self, "_hp_power_meta", None) or {})
        _st = _m.get("stats") or {}
        _bt = _e["panel"] if "panel" in _e else (getattr(self, "_hp_battery", None) or {})
        _head = _e.get("head") or None

        def _hold(_arr, _i):
            """一个下标所在的值连续出现多少格(往两边数相等的)。判游程宽度用。"""
            if _i < 0 or _i >= len(_arr) or _arr[_i] is None:
                return 0
            _v = _arr[_i]
            _a, _b = _i, _i
            while _a > 0 and _arr[_a - 1] == _v:
                _a -= 1
            while _b + 1 < len(_arr) and _arr[_b + 1] == _v:
                _b += 1
            return _b - _a + 1

        def _g(_k, _d="-"):
            _v = _bt.get(_k)
            return _d if _v is None else ("%s" % (_v,))

        _L = []
        _L.append("# 跳跳的弹珠机 · CPU高压测试 · 电池功率原始记录")
        try:
            _L.append("# 时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        try:
            _L.append("# 版本: %s   设备: %s"
                      % (_app_version(), _head or self._device_info()))
        except Exception:
            pass
        _L.append("# 窗口: 第 1 ~ 359 秒(连续高压测试 %d 秒)" % int(SOC_SUSTAIN_WALL_SEC))
        _L.append("# 采样: 5Hz 定长网格, dt=%ss, 共 %d 格(nan = 该格没读到)"
                  % (_m.get("dt", 0.2), len(_pw)))
        _L.append("# ⚠️ 开头已整段剔除 = **读不到的空段**(CPU 刚冲满载时 Binder 被抢占)"
                  " + 其后再延 %s 秒(CPU 从 idle 冲到满载的过渡期), 都不是稳态。"
                  % HP_PWR_SKIP_SEC)
        _L.append("#    实际剔掉 %s 格(t 列起点 %.1f 秒) —— 下面 t 是**相对测试起点**的秒数"
                  % (getattr(self, "_hp_pwr_skipped", "?"),
                     (_pt[0] if _pt else 0.0)))
        _L.append("# 电流源: %s   单位: %s   (BatteryManager.getIntProperty)"
                  % (_m.get("src"), _m.get("unit")))
        _L.append("# 电压源: ACTION_BATTERY_CHANGED / EXTRA_VOLTAGE   单位: mV"
                  "   (每秒读一次, 被 5 个功率格复用 —— 所以 V 是阶梯)")
        _L.append("# 变化点: n=%s n_chg=%s frac=%s gap_p50=%ss n_uniq=%s step_med=%s"
                  % (_st.get("n"), _st.get("n_chg"), _st.get("frac"),
                     _st.get("gap_p50"), _st.get("n_uniq"), _st.get("step_med")))
        _L.append("# 功率: 平均 %sW  最低 %s  最高 %s"
                  % (_g("power_mean"), _g("power_min"), _g("power_max")))
        _ok = [(i, x) for i, x in enumerate(_pw) if x is not None]
        if _ok:
            _hi_i, _hi_v = max(_ok, key=lambda p: p[1])
            _lo_i, _lo_v = min(_ok, key=lambda p: p[1])
            _L.append("#")
            _L.append("# 极值点游程(判毛刺用: 底层约 0.96s 刷新一次 ⇒ 真读数应连续占约 5 格)")
            for _tag, _i, _v in (("最高", _hi_i, _hi_v), ("最低", _lo_i, _lo_v)):
                _L.append("#   %s %.2fW @ t=%ss (idx=%d), 连续 %d 格"
                          % (_tag, _v, _pt[_i] if _i < len(_pt) else "?",
                             _i, _hold(_pw, _i)))
        _L.append("#")
        _L.append("# idx\tt(s)\tI_raw(uA)\tV(mV)\tP(W)")
        for _i in range(len(_pw)):
            _r = _raw[_i] if _i < len(_raw) else None
            _v = _mv[_i] if _i < len(_mv) else None
            _p = _pw[_i]
            _t = _pt[_i] if _i < len(_pt) else ""
            _L.append("%d\t%s\t%s\t%s\t%s"
                      % (_i, _t,
                         "nan" if _r is None else _r,
                         "nan" if _v is None else _v,
                         "nan" if _p is None else ("%.2f" % _p)))
        return "\n".join(_L) + "\n"

    def _startup_log_text(self):
        """把**启动加载日志**拼成一份可导出的 txt。

        它回答的是**「启动信息」面板回答不了的问题**: 面板只报结果(音效等待 N ms),
        而"这 N ms 花在哪"要看过程 —— 每轮探针自己多贵、扫了几个、卡在谁, 每条音效的
        load 各花了多久, 启动预热在哪些时刻抢了 CPU。

        ⚠️ **只读**: 不碰 Sfx 的任何状态、不碰音频栈、不碰游戏状态(这个弹窗的铁律)。
        ⚠️ 整段 try/except: 拼不出来就给空串, 绝不把按钮带崩。
        """
        L = []
        try:
            sfx = self.sfx
            L.append("=== 跳跳的弹珠机 · 启动加载日志 ===")
            try:
                L.append("版本      %s" % _app_version())
            except Exception:
                pass
            try:
                _bi = self._build_info()
                if _bi:
                    L.append("制作      %s" % _bi)
            except Exception:
                pass
            # 设备/系统: 只在安卓上取得到; 桌面照实写, 不报错
            try:
                from jnius import autoclass
                _B = autoclass("android.os.Build")
                _V = autoclass("android.os.Build$VERSION")
                L.append("设备      %s %s" % (_B.MANUFACTURER, _B.MODEL))
                L.append("系统      Android %s (SDK %s)" % (_V.RELEASE, _V.SDK_INT))
            except Exception:
                L.append("设备      (非安卓)")
            try:
                _n_cpu = os.cpu_count()
                _shape, _freq = _cpu_shape()     # (簇结构, 各簇频率); 读不到就是两个空串
                L.append("CPU       %s 核%s%s"
                         % (_n_cpu, ("  " + _shape) if _shape else "",
                            ("  " + _freq) if _freq else ""))
            except Exception:
                pass
            # ⚠️ **SoC 型号** —— "几核"看不出 1+3+4 与 2+6 的区别, 而那正是单价差的来源。
            #    Android 12+ 直接给 `Build.SOC_MODEL` / `SOC_MANUFACTURER`, 老版本退回
            #    `Build.HARDWARE`(厂商串); 取不到就整行不出现 —— 不编数。
            try:
                from jnius import autoclass
                _B3 = autoclass("android.os.Build")
                _soc = []
                for _a in ("SOC_MANUFACTURER", "SOC_MODEL"):
                    try:
                        _v = getattr(_B3, _a)
                        if _v:
                            _soc.append(str(_v))
                    except Exception:
                        pass
                if not _soc:
                    try:
                        _soc.append(str(_B3.HARDWARE))
                    except Exception:
                        pass
                if _soc:
                    L.append("SoC       %s" % " ".join(_soc))
            except Exception:
                pass
            L.append("音频后端  %s" % getattr(sfx.out, "name", "静音"))
            L.append("启动方式  %s启动   烘焙 %.0f ms"
                     % ("热" if sfx.cached else "冷", sfx.bake_ms))
            L.append("音效加载  %.0f ms（上限 %.0f）"
                     % (sfx.ready_ms, sfx.SFX_READY_TIMEOUT * 1000.0))
            # 闸门 vs 老探针**谁先到** —— 这一包的全部结论都从这一行读。
            #   ⚠️ 只在**真等过**的时候印(桌面上没有探针, `_await_ready` 直接放行 ⇒ 这一行会是
            #      "问号", 那是噪音)。
            if getattr(sfx, "_ready_winner", ""):
                L.append("先到者    %s（闸门 %.0f ms / 探针 %.0f ms）"
                         % (sfx._ready_winner,
                            float(getattr(sfx, "_gate_open_ms", 0.0) or 0.0),
                            float(getattr(sfx, "_probe_open_ms", 0.0) or 0.0)))
            L.append("轮询间隔  %.3f s" % sfx.SFX_READY_POLL)
            # 修复阶梯的点名结果(只在真的卡住时才是非空)
            try:
                _gmiss = str(getattr(sfx, "_gate_missing_name", "") or "")
                if _gmiss:
                    L.append("闸门差件  %s（已点名 + 已丢进重试队列）" % _gmiss)
            except Exception:
                pass
            # ⚠️ **点名单价** —— 它就是"这台机器一次 play() 有多贵", 以前只能从逐轮日志里手算。
            #    现在 `Σ探针自身 ÷ Σ真扫` 直接印 —— 换任何设备一眼就能分档。
            try:
                if _PROBE_COST[1] > 0:
                    L.append("点名单价  %.2f ms/次（探针自身共 %.0f ms ÷ 真扫 %d 次）"
                             % (_PROBE_COST[0] / _PROBE_COST[1], _PROBE_COST[0], _PROBE_COST[1]))
                else:
                    L.append("点名单价  无（这一次探针一次都没跑 —— 先到的是闸门）")
            except Exception:
                pass
            try:
                L.append("满编      合成音 %d + 语音 %d = %d"
                         % (sfx._n_bank, max(0, sfx._expected - sfx._n_bank), sfx._expected))
            except Exception:
                pass
            try:
                L.append("就绪      闸门 %d / 后端 %s"
                         % (len(sfx.named), sfx.backend_count()))
            except Exception:
                pass
            try:
                _gi = getattr(sfx.out, "gate_info", None)
                if _gi is not None:
                    L.append("就绪闸门  %s" % _gi())
            except Exception:
                pass
            # 「后端没播成」也进日志 —— 运行期静默不响**唯一的探照灯**, 以前只在面板上
            #   (而面板要玩家主动点开)。**0 也要印**: 它是"全程一声没漏"的正面证据。
            try:
                _ms = int(getattr(sfx.out, "_play_missed", 0) or 0)
                L.append("后端没播成  %d 次%s"
                         % (_ms, ("（最后一次 %s）"
                                  % (getattr(sfx.out, "_play_missed_last", "") or "?")) if _ms else ""))
            except Exception:
                pass
            try:
                if sfx._failed:
                    L.append("加载失败  %d 个: %s"
                             % (len(sfx._failed), ", ".join(n for n, _p in sfx._failed[:8])))
            except Exception:
                pass
            L.append("")
            L.append("---- 自动判定(到达形状: 每样本定价 or 按字节定价) ----")
            try:
                L.extend(self._probe_verdict())
            except Exception:
                pass
            L.append("")
            L.append("     t(ms)  事件")
            L.append("----------  ------------------------------------------------------------")
            for _t, _tag, _msg in sorted(_BOOT_LOG):
                L.append("%10.1f  [%s] %s" % (_t, _tag, _msg))
            L.append("")
            L.append("t 起算于 main.py 模块加载的第一行(比 Kivy 导入还早)。")
            L.append("「探针自身」那一项 = 这一轮 probe_all 里所有 play() 的累计耗时;")
            L.append("「扫 N 个」= 这一轮真的调用了几次 play();「卡在 X」= 第一个没就绪的。")
        except Exception:
            pass
        return "\n".join(L)

    def _save_startup_log(self):
        """「保存加载日志」按钮的动作 —— 复用 `_bench_save_log` 那整条降级链
        (MediaStore → 公共 Download / 外部私有 / 内部 / 剪贴板), 只换内容与文件名前缀。"""
        try:
            _txt = self._startup_log_text()
        except Exception:
            _txt = ""
        return self._bench_save_log(text=_txt, prefix="plinko_startup")

    def _save_power_log(self, pw=None, extra=None):
        """「保存记录」按钮的动作 —— 复用 `_bench_save_log` 那整条降级链, 只换内容与文件名前缀。"""
        try:
            _txt = self._power_log_text(pw, extra)
        except Exception:
            _txt = ""
        return self._bench_save_log(text=_txt, prefix="plinko_power")

    def _bench_history_text(self):
        """把**跑分历史的全部记录**拼成一段可保存的文本(2026-09-20, 玩家要的)。

        ⚠️ **是「能保存的所有」不是「历史全部」** —— 老记录会被「清空历史」删掉。
        ⚠️ 为什么要有它: 「保存日志(txt)」原来存的**只有当前这一轮的逐帧数据**
        (`_render_gaps_ms` 只保留最新一轮) —— 而「两次跑分差这么多是不是频率不同」
        这类问题**要看历史**。玩家原话:「保存的时候需要问我：保存最近一次，还是所有」。

        形状(**人读 + 机器读各一段**):
          ① 一张人读表: 表头一行 + 每条记录一行 CSV(**最新在前**, 与历史面板同序);
          ② 一段 JSON Lines: 每条记录一行完整 JSON ——
             ⚠️ **去掉 `diag`**(它是"当时那一刻"的逐帧诊断块, 1~3KB/条 × 100 条 = 上百 KB,
             而它要说的东西已经提到 ① 的列里了)。
        空历史返回 `""` —— 调用方据此**不拼这一段**, 而不是拼一个空壳。
        """
        _h = list(getattr(self, "bench_history", None) or [])
        if not _h:
            return ""
        _lines = ["#",
                  "# ===== 跑分记录（**现在存着的** %d 条，最新在前）=====" % len(_h),
                  "# ⚠️ 是「**能保存的所有**」不是「历史全部」—— 老记录会被「清空历史」删掉。",
                  "# 这一段的字段与上面的逐帧表无关 —— 它是**历次跑分**的汇总。",
                  "# 序号,时间,版本,机型,平均帧,中位帧,1%Low,10%Low,p99,p90,"
                  "物理中位步每秒,平均差系数,归一化,飞行ms,富余倍数,电池起,电池止,"
                  "屏幕Hz,Kivy上限,vsync"]
        _rev = list(reversed(_h[-100:]))

        def _g(_r, _k):
            _v = _r.get(_k)
            return "" if _v is None else _v

        for _i, _r in enumerate(_rev, 1):
            _d = _r.get("diag") or {}
            _lines.append(",".join(str(_x) for _x in (
                _i, _g(_r, "time"), _g(_r, "version"), _g(_r, "device"),
                _g(_r, "render_fps"), _g(_r, "render_median"), _g(_r, "render_1low"),
                _g(_r, "render_10low"), _g(_r, "render_p99"), _g(_r, "render_p90"),
                _g(_r, "phys_fps"), _g(_r, "phys_mad"), _g(_r, "phys_norm"),
                _g(_r, "flight_ms"), _g(_r, "margin"),
                _g(_r, "battery_start_c"), _g(_r, "battery_end_c"),
                _d.get("screen_hz", ""), _d.get("clock_maxfps", ""), _d.get("vsync", ""))))
        _lines.append("# ----- 以下每行是一条记录的完整 JSON（已去掉 diag）-----")
        for _i, _r in enumerate(_rev, 1):
            try:
                _lines.append(json.dumps({_k: _v for _k, _v in _r.items() if _k != "diag"},
                                         ensure_ascii=False))
            except Exception as _e:
                # ⚠️ 一条序列化不了**不许把整段带崩** —— 占位一行, 其余的照发。
                _lines.append('{"_err": "第 %d 条序列化失败: %s"}' % (_i, type(_e).__name__))
        return "\n".join(_lines) + "\n"

    def _bench_save_log(self, text=None, prefix="plinko_fps"):
        """把日志**存成 txt 文件**。

        ⚠️ 为什么不能只存 `user_data_dir`: 那是**应用内部目录**(`/data/data/<pkg>/files`),
        文件管理器看不见 —— 存那儿等于没存。剪贴板那条路在真机实测**会被截断**
        (3778 帧的日志只贴出 425 行, 安卓剪贴板走 Binder 有大小上限), 所以必须落盘。

        逐级降级, **每一级都把结果说出来**(绝不静默失败):
          ① 安卓 10+(API 29+) → MediaStore 写进**公共 Download 目录**, 不需要任何权限;
          ② `getExternalFilesDir` → `/sdcard/Android/data/<pkg>/files/`, 老系统也能写、不用权限;
          ③ `user_data_dir`(最后手段, 要 adb 才能取);
          ④ 全失败 → 退回**复制到剪贴板**, 并在提示里写明"剪贴板可能被截断"。
        返回 (成功?, 给玩家看的说明)。

        ⚠️ `text` / `prefix` 是为了让**功率原始记录 / 启动加载日志**复用这整条降级链 ——
           **默认值与原行为逐字相同**(不传就是逐帧日志 + `plinko_fps` 前缀), 零回归。
        """
        try:
            txt = text if text is not None else self._bench_frame_log()
        except Exception:
            txt = ""
        if not txt:
            return False, "没有可保存的数据"
        try:
            name = "%s_%s.txt" % (prefix, time.strftime("%Y%m%d_%H%M%S"))
        except Exception:
            name = prefix + ".txt"

        if platform != "android":
            # 桌面: 写到一个明确的地方(这样桌面也能验证"文件真的写出来了、内容完整")
            try:
                d = os.path.join(tempfile.gettempdir(), prefix)
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, name)
                with open(p, "wb") as f:
                    f.write(txt.encode("utf-8"))
                return True, "已保存: " + p
            except Exception as e:
                return False, "保存失败: %r" % (e,)

        # ---- ① MediaStore → 公共 Download(API 29+) ----
        try:
            from jnius import autoclass
            _sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
        except Exception:
            _sdk = 0
        if _sdk >= 29:
            _uri1 = None
            try:
                from jnius import autoclass
                act = autoclass("org.kivy.android.PythonActivity").mActivity
                resolver = act.getContentResolver()
                cv = autoclass("android.content.ContentValues")()
                cv.put("_display_name", name)
                cv.put("mime_type", "text/plain")
                _uri1 = resolver.insert(
                    autoclass("android.provider.MediaStore$Downloads").EXTERNAL_CONTENT_URI, cv)
                if _uri1 is not None:
                    os_ = resolver.openOutputStream(_uri1)
                    # ⚠️ 必须走 `java.lang.String.getBytes("UTF-8")` 拿到**真正的 byte[]** ——
                    #    直接把 Python `bytes` 交给 `OutputStream.write` 时, pyjnius 可能挑中
                    #    `write(int)` 重载, 于是只写进去一个字节(静默截断成一个字符)。
                    os_.write(autoclass("java.lang.String")(txt).getBytes("UTF-8"))
                    os_.flush()
                    os_.close()
                    return True, "已保存到 Download/" + name
                _err1 = "insert 返回空"
            except Exception as e:
                _err1 = repr(e)
            # ⚠️ **insert 一返回, 那一行就已经落库、文件当场对玩家可见**(没设 is_pending)。
            #    此后任何一步抛(openOutputStream 返回 None / 写到一半 / close 的 flush 失败)
            #    都会走到这里 —— 不清掉的话, 公共 Download 里留下一个**0 字节或半截的同名 txt**,
            #    而这个功能的全部承诺就是"去 Download 拿", 玩家会抓到那个坏文件发出去。
            #    (API 29+ 删自己插的行不需要权限。)
            #    ⚠️ 别把 `os_.close()` 挪进 `finally` —— 那样"没写全"会被吞成"已保存",
            #       正好制造这个功能要消灭的静默截断。
            if _uri1 is not None:
                try:
                    resolver.delete(_uri1, None, None)
                except Exception:
                    pass
        else:
            _err1 = "API %d < 29" % _sdk

        # ---- ② 外部私有目录 / ③ 内部目录 ----
        _tries = []
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            _d = act.getExternalFilesDir(None)
            if _d is not None:
                _tries.append(_d.getAbsolutePath())
        except Exception:
            pass
        try:
            _tries.append(App.get_running_app().user_data_dir)
        except Exception:
            pass
        # ⚠️ 这一行**必须在 try 里** —— `tempfile.gettempdir()` 在候选目录都不存在时会抛,
        #    裸着写会让整个函数冲出异常, ①②③ 白跑、连 ④ 都到不了。
        try:
            _tries.append(tempfile.gettempdir())
        except Exception:
            pass
        for _base in _tries:
            try:
                if not os.path.isdir(_base):
                    os.makedirs(_base, exist_ok=True)
                p = os.path.join(_base, name)
                with open(p, "wb") as f:
                    f.write(txt.encode("utf-8"))
                # ⚠️ 这一级**必须把 ① 为什么没成一起说出来**: 否则玩家看到
                #    "需用文件管理器/adb 取"会先去文件管理器白找一轮 —— 而这个目录在
                #    Android 11+ 上**系统文件管理器根本进不去**, 只有 adb 能取。
                return True, ("已保存: %s（Android 11+ 的文件管理器看不到这个目录, "
                              "要用电脑 adb pull 取）· 直接存 Download 失败原因: %s"
                              % (p, _err1))
            except Exception:
                continue

        # ---- ④ 全失败: 退回剪贴板, 并**明说可能被截断** ----
        try:
            if self._copy_bench_log():
                return True, "没法落盘(%s); 已复制到剪贴板 —— 安卓剪贴板可能截断" % _err1
        except Exception:
            pass
        return False, "保存失败(落盘与剪贴板都不可用): %s" % _err1

    def _fps_curve_zoom(self, curve):
        """点一下小图 = 放大成一张**可以左右拖的长条图**。

        ⚠️ **不用超采样、不建离屏纹理** —— 本控件的"每个点合并几帧"本来就是
           `ceil(总帧数 ÷ 横轴像素数)` 算出来的 ⇒ **把控件拉宽, 合并的帧数自动变小**,
           同一套绘制代码一行没改。
        ⚠️ 放大图 = **1:1 的原始图(每帧一个点)** + 横向拖拉 ⇒ 宽度给到
           "每帧 `ZOOM_PER_FRAME_PX` 像素", 于是 `_grp` 落到 1。
        ⚠️ **代价是实测过的, 别当它是 bug**: 1:1 时相邻两点的 y 差**就是数据本身的逐帧
           抖动量**, 画出来是**顶部一条厚带**, 读不出"稳定在某一帧率"这个事实。
           换粒度只改 `ZOOM_PER_FRAME_PX`: 2.0 = 每帧一个点; 0.5 = 每 2 帧 / 0.34 = 每 3 帧。
        ⚠️ 宽度按**帧数**算, 不按屏幕定死 —— 跑得久的一轮就是滚得久一点, **每一轮都是同一档
           细节**, 不会因为跑得久就被压扁。
        ⚠️ 放大图**不带 `on_zoom`** —— 免得点一下又套一层弹窗。阶段条照画。
        """
        gaps = list(getattr(curve, "_gaps", None) or [])
        if len(gaps) < 2:
            return
        # ⚠️ 常量长在 `FpsCurve` 上(它是绘制侧的旋钮), 这里必须写 `FpsCurve.` 前缀 ——
        #    本方法是 `RootWidget` 的, 写成 `self.` 会 AttributeError。
        _w = max(dp(600), len(gaps) * FpsCurve.ZOOM_PER_FRAME_PX + dp(40))
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        title = self._fit_line(Label(text='帧率曲线 · 放大', bold=True, halign='center',
                                     color=hex_rgb(COL_TEXT) + (1,),
                                     size_hint_y=None, height=dp(26)), 19)
        content.add_widget(title)
        sv = ScrollView(size_hint=(1, None), height=dp(292), do_scroll_y=False,
                        bar_width=dp(5))
        sv.add_widget(FpsCurve(gaps, cap_fps=getattr(curve, "_cap", 120.0),
                               tags=list(getattr(curve, "_tags", []) or []),
                               frame_spaced=True,
                               # ⚠️ 这里**不再显式传 `line_w`** —— `FpsCurve` 的默认值就是
                               #    1.0(真 1 像素)。一处真源, 别写第二遍。
                               size_hint=(None, 1), width=_w))
        content.add_widget(sv)
        # ⚠️ 文案**不写"1:1"也别写"每 N 帧一个点"**:
        #    ① "1:1" 是**错的**: 现在是每帧占 `ZOOM_PER_FRAME_PX`(=2) 像素, 不是"1 像素 1 帧"
        #       —— 多出来的那 1 像素是**给两条线之间留的空隙**。"1:1" 说的是**数据口径**
        #       (每帧一个点), 不是像素比, 混在一起就会误导。
        #    ② "每 N 帧一个点"这种**算出来的数**也不能写死 —— 它和实际绘制的 `_grp` 可能差 1。
        #    ⇒ 只说**数据口径**, 不碰像素比、不写算出来的数。
        note = Label(text='左右拖动查看 · 逐帧原始值', font_size='12sp',
                     halign='center', valign='middle',
                     color=hex_rgb(COL_SUB) + (1,),
                     size_hint_y=None, height=dp(20))
        note.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
        content.add_widget(note)
        close = Button(text='返回', font_size='16sp', bold=True, background_normal='',
                       background_color=hex_rgb(COL_BTN_OFF) + (1,),
                       size_hint_y=None, height=dp(46))
        content.add_widget(close)
        popup = self._popup(0.96, 430, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        close.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_fps_curve(self, gaps=None, tags=None, cap=None,
                        avg=None, med=None, low=None, allow_save=True):
        """展示帧率趋势；曲线与 1% Low 共用同一份 on_flip 原始采样。

        ⚠️ 加了**可选入参** —— 历史「详情」里点曲线时把**记录里**那份(降采样后的)传进来;
           而现场那个按钮**一个字都不用改**(缺省仍取 self.*)。
        ⚠️ **绝不能用"临时把 self._render_gaps_ms 改掉再调"的做法** —— 那会覆盖掉最新一轮的
           逐帧数据(而它还要给「保存日志」用)。
        ⚠️ `allow_save=False` 时**不建**「保存日志」按钮: 保存走的是 `_bench_save_log`,
           它读的是**内存里这一轮**的数据 —— 拿着一张历史记录的曲线去按保存, 存下来的是
           **另一轮**的日志。那是假数据, 比没有按钮更糟。
        """
        gaps = list(gaps) if gaps is not None else list(getattr(self, "_render_gaps_ms", []) or [])
        # ⚠️⚠️ **本面板自己的标签不计入字体账本**: 下面那个标题走 `_fit_line` →
        #    `_fit_font_size_slow` **一次走满 13 档阶梯** ⇒ 一口气开 12~13 个 fontid。
        #    而玩家**必须**打开这个面板才能导出日志 ⇒ **保存日志这个动作, 把日志里印的那个
        #    计数撑大 12** —— 测量动作污染被测对象。
        #    ⚠️ 窗口是**时间**不是"函数返回": 布局收敛是异步的, 跨好几帧才定稿。
        #    ⚠️ 跳过几次**照记**(`_FS_MUTED_N`)并印进日志 —— 不许静默。
        _FS_MUTE_UNTIL[0] = time.time() + _FS_MUTE_SEC
        # 每一帧"当时在演什么", 与 `gaps` **同序等长** —— 两者来自同一批 `on_flip` 采样:
        # `_on_flip` 一直在往 `_bench_frames` 里存 `(帧间隔, 场景标签)`, 曲线原来只取了前半截。
        # 接到曲线上之后, "哪几帧在掉" 和 "那几帧在演什么" 就是上下对齐看的。
        # ⚠️ 取不到(长度对不上/老记录)时 `FpsCurve` 会自己退化成不画阶段条, 曲线照旧。
        if tags is None:
            tags = [x[1] for x in (getattr(self, "_bench_frames", []) or []) if len(x) > 1]
        else:
            tags = list(tags)
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        title = self._fit_line(Label(text='帧率曲线', bold=True, halign='center',
                                     color=hex_rgb(COL_TEXT) + (1,),
                                     size_hint_y=None, height=dp(26)), 19)
        content.add_widget(title)
        # ⚠️ 历史那条路把**当时**的帧率档位传进来 —— 纵轴上限必须是当时的,
        #    拿现在的档位去画历史那张会把曲线压扁或拉长。
        if cap is None:
            cap = float(_FPS_INFO[1] or _fps_user_cap())
        curve = FpsCurve(gaps, cap_fps=cap, tags=tags,
                         on_zoom=self._fps_curve_zoom,
                         size_hint_y=None, height=dp(276))
        content.add_widget(curve)
        if avg is None:
            avg = float(getattr(self, "_render_fps", 0.0))
        if med is None:
            med = float(getattr(self, "_render_median_fps", 0.0))
        if low is None:
            low = float(getattr(self, "_render_1low", 0.0))
        note = Label(text='逐帧 FPS　平均 %.1f　中位 %.1f　1%%Low %.1f'
                          % (avg, med, low),
                     font_size='13sp', halign='center', valign='middle',
                     color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(22))
        note.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
        content.add_widget(note)
        # ⚠️ 这行**说明两张图各是什么口径**: 让看图的人知道"上面那条线不是平均值,
        #    是每几帧里最慢的那帧"(所以看着比实际差), 以及"点一下能看到每一帧的原始值"。
        #    **措辞是逐字定的, 别改顺口** —— "组"是造词, 玩家从没说过。
        # ⚠️⚠️ **这里用半角 `=` 不是全角 `＝`**(真机截图报过「无法显示的文字」:
        #    那个位置是个方块)。**桌面渲染全角等号是正常的**, 只有真机把它画成豆腐块 ——
        #    所以别拿桌面截图当"没问题"的证据。**UI 文案里别用全角标点里的冷门符号。**
        _tip = Label(text='本图每若干帧合一个点、取最慢的那帧（看着比实际差）'
                          '· 点一下看每一帧的原始值',
                     font_size='12sp', halign='center', valign='middle',
                     color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(20))
        _tip.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
        self._auto_h(_tip, dp(20), dp(4))
        content.add_widget(_tip)
        # 两个按钮一行: 保存日志(txt) + 返回。
        # ⚠️ 保存是**唯一**能把"逐帧数据"**完整**带出这台设备的出口 —— 曲线只能看, 带不走;
        #    而剪贴板在真机上**会被截断**(实测 3778 帧的日志只贴出 425 行)。
        _btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        # ⚠️ `save` **可能不建**(历史那条路) ⇒ 下面的 bind 要过一道。
        save = None
        if allow_save:
            save = Button(text='保存日志(txt)', font_size='16sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
            _btns.add_widget(save)
        close = Button(text='返回', font_size='16sp', bold=True,
                       background_normal='', background_color=hex_rgb(COL_BTN_OFF) + (1,))
        _btns.add_widget(close)
        content.add_widget(_btns)
        # 保存结果**单独一行常驻显示** —— 按钮上的字两秒就变回去了, 而"存到哪了"是要照着去找的。
        # ⚠️ 高度**必须走 `_auto_h`**: ② 那条提示里带完整路径 + 失败原因, 12sp 下要折 2~4 行,
        #    写死 `height=dp(30)` 会把尾巴裁掉 —— 而尾巴里正是"① 为什么没成"。
        _note = Label(text='', font_size='12sp', halign='center', valign='middle',
                      color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(30))
        _note.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
        self._auto_h(_note, dp(30), dp(4))
        content.add_widget(_note)
        popup = self._popup(0.92, 390, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _do_save(_scope="last"):
            """⚠️ `_scope` 由下面的选择框给: `"last"`(默认) / `"all"`。

            `"last"` 那条路**与改动前逐字相同**(不传 `text`/`prefix`), 零回归 —— 有门禁钉住。
            """
            try:
                if _scope == "all":
                    _txt = self._bench_frame_log() + "\n" + self._bench_history_text()
                    _ok, _msg = self._bench_save_log(text=_txt, prefix="plinko_fps_all")
                else:
                    _ok, _msg = self._bench_save_log()
            except Exception as _e:
                _ok, _msg = False, "保存失败: %r" % (_e,)
            _set_label_text(_note, _msg)
            save.text = '已保存' if _ok else '保存失败'
            Clock.schedule_once(lambda _d: setattr(save, 'text', '保存日志(txt)'), 2.5)

        def _ask_scope(*_a):
            """玩家 2026-09-20 要的: 保存前**先问一句**「最近一次」还是「所有」。

            玩家原话:「保存的时候需要问我：保存最近一次，还是所有」。
            ⚠️ 历史为空时**照样给两个选项**, 只把「连历史一起存」标成「(历史为空)」并禁用 ——
               藏掉它的话玩家会以为功能没做。
            ⚠️ Label **不认 markdown**: 正文里**绝不能出现星号**(会原样显示成星号)。
            """
            _n = len(getattr(self, "bench_history", None) or [])
            _c = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(10))
            _ttl = self._fit_line(Label(text='保存日志', bold=True, halign='center',
                                        color=hex_rgb(COL_TEXT) + (1,),
                                        size_hint_y=None, height=dp(28)), 19)
            _c.add_widget(_ttl)
            # ⚠️ 措辞是「**现有的**」而不是「全部历史」—— 玩家 2026-09-20 明确更正:
            #    「因为清空的时候会清 log，所以保存的也不是历史所有，而是**能保存的所有**」。
            _m = Label(text=('只存最近一次：当前这一轮的逐帧日志\n\n'
                             '连现有记录一起：上面那份，再加上现在存着的 %d 条跑分记录\n'
                             '（逐帧数据只有最近这一轮；更早的记录只剩汇总）' % _n),
                       font_size='14sp', halign='left', valign='top',
                       color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
            self._auto_h(_m, dp(96), dp(6))
            _c.add_widget(_m)
            _r1 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            _b_last = Button(text='只存最近一次', font_size='15sp', bold=True,
                             background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
            _b_all = Button(text='连现有记录一起', font_size='15sp', bold=True,
                            background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
            if _n <= 0:
                # ⚠️⚠️ **不许用 `.disabled =`** —— 出货文件里**一处都不许有**
                #    (`fx_gates` 有闸钉住)。老版「输入锁改走触摸层」那条就是因为它
                #    (逐个按钮 `disabled` 是 1%Low 的头号来源)。
                #    ⇒ 空历史时改成「**灰底 + 文字说明 + 不 bind**」(点了没反应),
                #      而不是禁用它 —— 既不违反那条闸, 也不会让玩家以为功能没做。
                _b_all.text = '连现有记录一起（没有）'
                _b_all.background_color = hex_rgb(COL_BTN_OFF) + (1,)
            _r1.add_widget(_b_last)
            _r1.add_widget(_b_all)
            _c.add_widget(_r1)
            # 取消**单独一行、占满宽**: 它是无害的那一个(最坏就是不保存) ⇒ 给它最大的靶子。
            _cancel = Button(text='取消', font_size='16sp', bold=True, background_normal='',
                             background_color=hex_rgb(COL_BTN_OFF) + (1,),
                             size_hint_y=None, height=dp(46))
            _c.add_widget(_cancel)
            _p = self._popup(0.86, 300, title='', content=_c,
                             auto_dismiss=True, separator_height=0)

            def _pick(_s):
                def _go(*_a2):
                    _p.dismiss()
                    _do_save(_s)
                return _go

            _b_last.bind(on_release=_pick("last"))
            if _n > 0:
                _b_all.bind(on_release=_pick("all"))
            _cancel.bind(on_release=_p.dismiss)
            _p.open()
            self._popup_fit_content(_p, _c)

        if save is not None:
            save.bind(on_release=_ask_scope)
        close.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _bench_diag_text(self):
        """性能测试成绩面板的**诊断追加行**。

        为什么要加: 原来的成绩只有"平均帧率 / 1%Low", 而玩家报的是"球在飞行的时候一卡一卡的"
        —— 光看两个数**分不清卡在哪儿**: 是在飞行, 还是在装杯演出? 是处理器顶不住, 还是画面在拖?
        ⚠️ 措辞一律用白话: 不用"p50/p99/墙钟/GC"这些行话, 直接说"一半的帧""内存回收停顿"
           "在等画面 / 算不过来"。
        任何一项取不到都返回空串, 面板与旧版逐字相同 —— 绝不让诊断把成绩挤没。"""
        d = getattr(self, "_bench_diag", None)
        if not d:
            return ''
        try:
            parts = []
            # "实算"= 那一帧真的烧掉的 CPU。它接近帧间隔就是**算出来的**(处理器瓶颈);
            # 很小就是**等出来的**(GC/IO/显卡/驱动)。
            # "实算"只在 >=1 毫秒时才带出来: 安卓是 Linux, process_time 有纳秒精度;
            # 而 Windows 只有 15.6ms 精度, 桌面跑分里恒为 0.0, 纯噪声。
            # ⚠️ `_FRAME_SELF`(自算) **只包了 `_frame`** —— 启动预热、方向守卫、进度提示
            #    这些都跑在别的 Clock 回调里, 在它外面。所以"自算很小"**不等于**"我们没干活":
            #    桌面实测那几帧 实算 62.5 而自算 0.1, 正是预热(球纹理烘焙)干的。
            #    所以**预热帧必须直接标出来**, 否则会被误读成"主线程在等"。
            def _frame_cell(_g, _t, _c, _h, _s, _b, _k=(), _i=-1):
                # ⚠️ 占位符个数不同的分支**必须分开格式化** —— 写成
                #    `('A' if c else 'B') % (g,t,c)` 会在一个分支抛 TypeError, 而外层
                #    那个 except 会把它静默吞掉(面板诊断整块消失)。踩过一次了。
                # **实算 / 主线程 / 自算** 三个数并排 —— "主线程"才是能看见 Kivy 渲染那一块的那一格。
                if _c >= 1.0 or _s >= 1.0:
                    _x = '%.0f毫秒(%s·实算%.1f·主线程%.1f·自算%.1f)' % (_g, _t, _c, _h, _s)
                else:
                    _x = '%.0f毫秒(%s)' % (_g, _t)
                if _b:
                    _x += '·预热'
                # 本帧最大的两笔子步骤 —— **这是"那 9 毫秒到底花在哪"的直接答案**。
                # ⚠️ 标签之间用 '/' 分隔而不是空格: 面板那一行本来就长, 空格会让它读不清哪里断开。
                for _ms, _nm in (_k or ()):
                    _x += '|%s%.1f' % (_nm, _ms)
                if _i >= 0:
                    _x += '@%d' % _i          # 在采样序列里的下标: 相邻=同一段忙碌
                return _x
            _w = []
            for _it in d["worst"]:
                _w.append(_frame_cell(_it[0], _it[1], _it[2],
                                      (_it[3] if len(_it) > 3 else 0.0),
                                      (_it[4] if len(_it) > 4 else 0.0),
                                      (_it[5] if len(_it) > 5 else 0),
                                      (_it[6] if len(_it) > 6 else ()),
                                      (_it[7] if len(_it) > 7 else -1)))
            w = '  '.join(_w)
            parts.append('最慢三帧： ' + w)
            _ord = ("飞行", "装杯", "落袋", "蓄力", "哑火", "待机")
            gs = d["groups"]
            # 带上帧数 —— 只印中位数的话, "待机 8 帧"的中位和"飞行 300 帧"的中位不可比,
            # 而三个最慢帧里两个是待机。
            seg = ['%s %.1f(%d帧)' % (t, gs[t][1], gs[t][0]) for t in _ord if t in gs]
            if seg:
                parts.append('各阶段每帧： ' + ' · '.join(seg) + ' 毫秒')
            # 慢帧的节拍 + 当时在不在发声/震动。这一行是给"偶发长停顿"定位用的 ——
            # 规则节拍指向时钟驱动, 不规则指向事件驱动; 而慢帧里发声/震动的占比
            # 直接回答玩家那句"关掉音效就好了"到底是声音还是震动造成的。
            # 发声/震动计数**独立一行、永远显示**。它有两个用途: ① 真机上把"偶发长停顿"归因到
            # 声音还是震动(玩家唯一有因果力的线索是"关掉音效 1% low 就回升", 而那会**连震动
            # 一起关掉**, 混在一起分不开); ② **自证探针在工作** —— 看到"全程发声 N 次"就知道
            # 计数器真跑了, 否则"慢帧里 0 帧在发声"既可能是真的、也可能是计数器根本没跑。
            # 后端名并到发声那行(它只跟发声有关)。
            _wn = d.get("snd_worst_name", "")
            parts.append('发声： %d 次 · 单次最慢 %.1f 毫秒%s · 累计 %.0f 毫秒'
                         ' · 超%.0f毫秒 %d 次（后端 %s）'
                         % (d.get("snd_n", 0), d.get("snd_worst", 0.0),
                            ('（%s）' % _wn) if _wn else '',
                            d.get("snd_sum", 0.0), SND_SLOW_MS,
                            d.get("snd_slow_n", 0), d.get("backend", "?")))
            _vn = d.get("vib_worst_name", "")
            parts.append('震动： %d 次 · 单次最慢 %.1f 毫秒%s · 累计 %.0f 毫秒'
                         % (d.get("vib_n", 0), d.get("vib_worst", 0.0),
                            ('（%s）' % _vn) if _vn else '',
                            d.get("vib_sum", 0.0)))
            # 方向守卫 / 沉浸重申那一行 —— 只在这两条链**真的跑过**时出(桌面是 0 次)。
            # 它们**已搬到工作线程**, 所以这里报的是两档对比: 主线程那档该接近 0(证明真搬走了),
            # 工作线程那档接手。
            if d.get("jni_n"):
                parts.append('方向守卫： %d 次（每0.7秒）· 主线程单次最慢 %.2f 毫秒 · 累计 %.1f 毫秒'
                             '　工作线程累计 %.0f 毫秒（最慢 %.1f）· 失败 %d 次'
                             % (d.get("jni_n", 0), d.get("jni_main_worst", 0.0),
                                d.get("jni_main_sum", 0.0), d.get("jni_bg_sum", 0.0),
                                d.get("jni_bg_worst", 0.0), d.get("jni_err", 0)))
            # 系统栏那一次重申的**真实代价** —— 跑在 Java UI 线程上, 只有这里看得见。
            # 判据: 单次 ≥5 毫秒 ⇒ 每 0.7 秒重申一次是在拿 UI 线程换一个系统本来就会自动隐藏
            # 的东西(IMMERSIVE_STICKY 自己会收回), 那就该把周期拉长。
            if d.get("ui_n"):
                parts.append('系统栏重申： %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒（UI线程，不在主线程实算里）'
                             % (d.get("ui_n", 0), d.get("ui_worst", 0.0),
                                d.get("ui_sum", 0.0)))
            # 慢帧那一行只在真有慢帧时出(它回答的是"停顿长什么样", 没停顿就没什么可说的)。
            if d.get("slow2_n"):
                # ⚠️ "几帧在预热"必须单独报 —— 那几帧是**启动期**的账, 与稳态卡顿不是一回事
                #    (桌面实测: 最慢的 11 帧全在启动 0.6 秒内, 而那些帧我们自己的代码只花了
                #    0.03~0.10 毫秒)。混在一起看会把"启动慢"误读成"玩起来卡"。
                # ⚠️ 这里叫「长停顿」不叫「慢帧」: 「慢帧」已经被成绩面板占成「中位帧率 <75%」
                #    (那一档有一百多帧), 而这一行说的是「≥2 倍中位帧时间」(<50%)。
                #    **同一个词两个含义**正是本工程栽过的那类静默脱钩。
                parts.append('长停顿： %d 帧≥%.0f毫秒（平均每 %.2f 秒一次）· 其中 %d 帧在发声'
                             ' / %d 帧在震动 / %d 帧在启动预热'
                             % (d["slow2_n"], d.get("slow2_ms", 25.0), d["slow_beat"] / 1000.0,
                                d.get("slow_snd", 0), d.get("slow_vib", 0),
                                d.get("slow_bake", 0)))
            # 瓶颈判断: 每帧真正花在计算上的时间 vs 帧间隔。差得远 = 大头在等画面
            #   (GPU 出图 / 垂直同步); 快追平 = 处理器就是瓶颈。
            _cpu = float(d.get("cpu_per_frame", 0.0))
            _p50 = float(d["p50"])
            # ⚠️ **Windows 上这一整行不成立, 必须说清楚**: 本文件上面自己写着"Windows 的
            #    `process_time` 精度只有 15.6ms, 桌面跑分里恒为 0.0, 纯噪声" —— 可"瓶颈"判断
            #    仍然拿它去除帧间隔, 于是 PC 上会打出「处理器算不过来 · 每帧实算 18.4 / 帧间隔
            #    16.5」这种**自相矛盾**的结论(同一份面板里"最慢三帧 20毫秒(待机·实算31.2)" ——
            #    20 毫秒的帧不可能烧 31 毫秒, 那就是量化台阶本身)。
            #    这正是本仓库警告过的那个形状: **一个专抓静默归因的面板, 自己在做静默归因**。
            _win = (sys.platform == "win32")
            if _win:
                _v = "本机测不准"
            elif _p50 > 0 and _cpu > 0:
                _r = _cpu / _p50
                # ⚠️ 第三档**故意不说"画面是瓶颈"**: 这一项只量"本进程烧了多少 CPU"。
                #    "算得少、等得多"既可能是等显卡出图/等垂直同步, 也可能是**阻塞在系统调用上**
                #    (Binder/JNI/文件IO) —— 本项**分不出这两者**。写死"画面"会把归因带偏。
                #    要分清得靠"慢帧里在发声/震动吗"那一行。
                _v = ("处理器算不过来" if _r >= 0.8
                      else ("处理器比较吃紧" if _r >= 0.4 else "大头在等"))
            else:
                _v = "—"
            # GC 那一栏后面挂上"最坏那次是哪一代" —— gen-2 全量回收与 gen-0 差一个量级,
            # 不写清是哪一代, 读者没法判断这是"轻微抖动"还是"扫了整个对象图"。
            _gn = {0: "轻度", 1: "中度", 2: "全量"}.get(d.get("gc_worst_gen", -1), "—")
            # 三个数并排才能分流"谁在吃时间":
            #   实算(全进程)大 · 主线程小 => 工作线程在忙;
            #   主线程大 · _frame 小      => **Kivy 渲染 / 文字重排**在吃;
            #   _frame 大                => 就是我们自己的代码。
            _win_s = max(0.001, d.get("p50", 12.5) * max(1, d.get("n", 0)) / 1000.0)
            # 节拍那一行 —— 直接回答"帧循环是不是自由跑": 比值 < 1 说明**呈现比逻辑更新更频繁**
            # (有一批帧在白白占用呈现机会)。也顺手把 Kivy 的限速旋钮值打出来。
            _fc = int(d.get("frame_calls", 0))
            _hz, _cap, _mode_hz = _FPS_INFO[0], _FPS_INFO[1], _FPS_INFO[2]
            _platform_rate = (' · Android模式请求 %s' %
                              (('%.0fHz' % _mode_hz) if _mode_hz else
                               ('%.0fHz（模式未知）' % _fps_user_cap()))
                              if platform == "android" else ' · 桌面vsync跟随屏幕刷新率')
            parts.append('节拍： 屏幕 %s · 帧率上限 %s · vsync=%s%s'
                         ' · `_frame` %d 次 / 采样 %d 帧（比值 %.2f）'
                         % (('%.0fHz' % _hz) if _hz else '没读到',
                            ('%.0f' % _cap) if _cap else '不设(0)', d.get("vsync", "?"), _platform_rate, _fc,
                            d.get("n", 0), _fc / float(max(1, d.get("n", 1)))))
            # 内核态占比 —— 把这一个数当**分流器**: 高则查 JNI/Binder/文件写, 低则查 GIL。
            # C6: 最差 20 帧是不是**紧跟在一次发射之后**? 是 ⇒ "每发才做一次的活"有罪;
            # 铺开 ⇒ 无罪, 该往别处找。
            _p = d.get("w20_pos") or []
            if _p:
                parts.append('最差20帧： 距上次发射 %d~%d 帧（中位 %d）· 其中 %d 帧在发射后 20 帧内'
                             '　（铺开=与发射无关）' % (_p[0], _p[-1], _p[len(_p) // 2],
                                                    d.get("w20_near", -1)))
            parts.append('渲染设置： multisamples=%s（Kivy 默认 2 = 2x MSAA 全屏，每帧固定带宽成本）'
                         % d.get("multisamples", "?"))
            _u, _k = float(d.get("utime_ms", -1)), float(d.get("stime_ms", -1))
            if _u >= 0 and (_u + _k) > 0:
                parts.append('CPU 构成： 用户态 %.0f 毫秒 · 内核态 %.0f 毫秒（内核占 %.0f%%）'
                             % (_u, _k, 100.0 * _k / (_u + _k)))
            parts.append('瓶颈： %s · 每帧实算 %.1f / 主线程 %.1f（其中 _frame %.1f，最坏 %.1f）'
                         '/ 帧间隔 %.1f 毫秒'
                         ' · 内存回收 %.1f 毫秒（最坏一次 %.1f·%s）· 文字重排 %.1f 次/秒'
                         % (_v, _cpu, d.get("thr_p50", 0.0), d.get("self_p50", 0.0),
                            d.get("self_max", 0.0), _p50, d["gc_total"] * 1000.0,
                            d["gc_worst"] * 1000.0, _gn, d.get("texupd", 0) / _win_s))
            _tex_by = d.get("texupd_by") or []
            if _tex_by:
                parts.append('文字重排来源：' + ' · '.join('%s %d次' % (name, count)
                                                       for name, count in _tex_by))
            if d.get("cfg_n"):
                parts.append('　存档落盘： %d 次（工作线程）· 单次最慢 %.1f 毫秒'
                             % (d["cfg_n"], d.get("cfg_worst", 0.0)))
            # 冻结生效与否**必须显示** —— 否则"GC 没再拖后腿"既可能是真冻结了, 也可能是根本
            # 没跑到(这个仓库专门栽过这种静默)。
            if d.get("gc_frozen"):
                # 冻结**前后各强制做一次全量回收**的实测读数 —— 这是"冻结到底买到了什么"
                # 在真机上的直接答案, 不用靠推断(桌面量不出来)。
                parts.append('内存冻结： 已冻结 %d 个常驻对象 · 全量回收 %.1f -> %.1f 毫秒'
                             % (d.get("gc_frozen", 0), d.get("gc_frz_before", 0.0),
                                d.get("gc_frz_after", 0.0)))
            if _win:
                parts.append('⚠️ 每帧实算这一项在 Windows 上测不准(系统计时粒度 15.6 毫秒), '
                             '「瓶颈」不判 —— 要看它请用安卓机的成绩')
            return '\n'.join(parts)
        except Exception:
            return ''

    def _bench_low_summary_text(self, d=None):
        """成绩页默认只显示能指导 1% Low 优化的简短证据。

        ⚠️ 加了可选入参 `d` —— 玩家报「普通测试的详情里**漏了一块下面的灰色字**」:
        那块就是本函数的输出, 而它原来**只读 `self._bench_diag`** ⇒ 历史记录里没存就印不出来。
        ⚠️ 不传就读 `self._bench_diag`(现场那条路**一个字不用改**)。
        """
        d = d if d else (getattr(self, "_bench_diag", None) or {})
        if not d:
            return ''
        try:
            parts = []
            _grp = d.get("groups") or {}
            # ⚠️ 这一段的口径统一到**卡顿帧**(帧率 < 中位帧率 50%), 不再是"最慢 1%" ——
            #    否则上面写"卡顿帧共 N 帧"、下面写"各阶段比例"用的却是另一批帧, 两行对不上。
            # ⚠️ 卡顿帧为 0 时**不要退回 `low1_groups`** —— 那会印出"分布"却写着"共 0 帧"。
            def _dist_line_of(groups, n_key, label):
                """把 {阶段: 帧数} 排成一行分布文案; 没有该档时返回空串。

                ⚠️ 卡顿帧与慢帧**共用这一个函数** —— 两处各写一份格式化迟早会漂成两种排版。
                ⚠️ `x/y` 的分母是**该阶段的帧数**("占这一阶段多少"), 与两档那行的分母
                   (**窗口总帧数**)不是一回事, 两处不可互比。
                ⚠️ **只印 `x/y`, 不印百分比**; 但**排序仍按比率** —— 分数大小一样时, 比率才是
                   "哪个阶段更容易卡"的正确次序; 按绝对帧数排会把长阶段顶到前面。
                ⚠️ 该档为 0 帧时**整行不印**, 别印一个空分布。
                """
                _st = (groups or []) if d.get(n_key) else []
                _rw = []
                for _name, _cnt in _st[:3]:
                    _info = _grp.get(_name)
                    _tot = int(_info[0]) if isinstance(_info, (tuple, list)) and _info else 0
                    _rw.append(((100.0 * _cnt / _tot) if _tot > 0 else -1.0,
                                _name, int(_cnt), _tot))
                _rw.sort(key=lambda r: -r[0])
                _rt = [('%s %d/%d' % (r[1], r[2], r[3])) if r[3] > 0
                       else ('%s %d帧' % (r[1], r[2])) for r in _rw]
                # ⚠️ 中文圆点用**两个半角空格**代替(与上面「采样窗口」那行**同一处改动**)。
                return (label + '：' + '  '.join(_rt)) if _rt else ''

            _dist_line = _dist_line_of(d.get("jank_groups"), "jank_n", '卡顿帧分布')
            # 慢帧分布。⚠️ 判据是**中位帧率的 75%**, 与 `slow_n` **同一个集合** ——
            #    这一档**包含**卡顿帧, 所以它的分布天然比上一行"大一圈"(每一格都 ≥)。
            _slow_dist_line = _dist_line_of(d.get("slow_groups"), "slow_n", '慢帧分布')
            _worst = (d.get("worst") or [None])[0]
            if _worst:
                _gap, _stage = float(_worst[0]), _worst[1]
                # ⚠️ 分隔符统一用**全角冒号** —— 三行里两行全角一行半角, 截图上一眼看出来不齐。
                # ⚠️ 单位跟本面板其余几行走**中文「帧/秒」, 不用 `fps`** —— 上面两档门槛印的
                #    就是「低于 91 帧/秒」, 同一块面板混两种单位会被打回。
                #    `_gap` 是帧间隔毫秒, 所以帧率 = `1000 / _gap`。
                _worst_line = ('最慢一帧：%.1f 帧/秒（%s）' % (1000.0 / _gap, _stage)
                               if _gap > 0 else '')
            else:
                _worst_line = ''
            # ⚠️ **绝对帧率**的门槛在高刷机上没有意义(165Hz 的机器上"低于 60fps"是地板级要求,
            #    而且它的分子分母都是全窗口, 跟上面"卡顿帧"那批根本不是同一批帧)
            #    ⇒ 改成"卡顿帧(帧率 < 中位帧率的 50%), 一共有 X 帧", 门槛跟着本机中位走。
            # ⚠️ **两档都要印**: `卡顿帧（<55%）` 与 `慢帧（<75%）`, 阈值都是**相对本机中位帧率**。
            #    ⚠️ 慢帧**包含**卡顿帧(同一个分母的两档), 所以它的数一定 ≥ 卡顿帧。
            # ⚠️ **不变量: 慢帧(75%) 一定 ⊇ 卡顿帧(55%)** —— 老 diag 字典/异常兜底里可能没有
            #    `slow_n`, 直接印 0 就会出现"卡顿帧 6 / 慢帧 0"这种**自相矛盾**的两行。
            # ⚠️⚠️ 但这个 `max` 是**兜底, 不是定义**: 它曾经把「`slow_n` 被覆盖成 5」这件事
            #    **盖住了** —— `max(5, 9) = 9`, 于是面板印的慢帧**恒等于卡顿帧**, 看起来"合理"
            #    却完全失真。真正的数来自 `_bench_collect_diag` 里那句 `<75%` 的统计, 对应的
            #    自证行是日志头的 `# ★ 慢帧「中位帧率 <75%」`。**改这里之前先去看那一行对不对得上。**
            _jn = int(d.get("jank_n", 0))
            _sn = max(int(d.get("slow_n", 0)), _jn)
            # ---- 采样窗口 + 两档 ----
            # ⚠️ **三个数必须同源**: 中位帧率、两个门槛**全从 `d["p50"]` 派生**, 而
            #    `_bench_collect_diag` 判 `jank_n`/`slow_n` 用的也是同一个 p50 ⇒
            #    "印出来的门槛"与"数出来的帧数"严格一致。**不要**改用日志头那个
            #    `_render_median_fps`(它是从 `_flip_times` 另算的) —— 两处各算必然脱钩。
            # ⚠️ 窗口时长取 `win_ms`(帧间隔之和), 与日志头 `sum(gaps)` **同一口径**。
            # ⚠️ 拿不到窗口就**整行不印、门槛与比例也不印**: 「0.0 秒 · 0 帧」「0.00%」
            #    是**假数**, 比没有更糟。帧数照常印。
            _n = int(d.get("n", 0) or 0)
            _win_ms = float(d.get("win_ms", 0.0) or 0.0)
            _p50 = float(d.get("p50", 0.0) or 0.0)
            if _n > 0 and _win_ms > 0.0 and _p50 > 0.0:
                # ⚠️ 中文圆点用**两个半角空格**代替。用两个而不是一个: 一个的话「秒 帧」会读成
                #    同一个词的一部分, 分不出这是分隔符。
                #    ⚠️ 句子那一串 `' · '.join(...)` 见 `_dist_line_of`, **两处一起改**。
                parts.append('采样窗口：%.1f 秒  %d 帧  中位 %.0f 帧/秒'
                             % (_win_ms / 1000.0, _n, 1000.0 / _p50))
                parts.append('卡顿帧（低于 %.0f 帧/秒）：%d 帧（%.2f%%）'
                             % (1000.0 * JANK_RATE / _p50, _jn, 100.0 * _jn / _n))
                parts.append('慢帧（低于 %.0f 帧/秒）：%d 帧（%.2f%%）'
                             % (1000.0 * SLOW_RATE / _p50, _sn, 100.0 * _sn / _n))
            else:
                parts.append('卡顿帧（<55%%）：共 %d 帧' % _jn)
                parts.append('慢帧（<75%%）：共 %d 帧' % _sn)
            if _dist_line:
                parts.append(_dist_line)
            if _slow_dist_line:
                parts.append(_slow_dist_line)
            if _worst_line:
                parts.append(_worst_line)
            return '\n'.join(parts)
        except Exception:
            return ''

    def _show_hp_history(self):
        """CPU 高压测试历史: **4 列**(时间 / 平均值 / 平均差系数 / 详情按钮)。

        ⚠️ 与「测试历史」是**两张表**: 那一张是性能测试(峰值/帧率),
           这一张是 CPU 高压(衰减/频率)。挤一张只会互相污染。

        ⚠️⚠️ 本函数整体按 `_show_bench_history`**逐项照搬**(否则"两张表看着不像一套东西"):
             ① **不做行数推算, 也不调 `_popup_fit_content`** —— 列表用
                `ScrollView(size_hint=(1, 1))` 吃掉剩余高度, 面板高度由弹窗固定给(`0.7 * _vh`)。
                记录少就下面留白、多了才滚动 ⇒ 高度与记录数**无关**。
                ⚠️ 末尾那次 `_popup_fit_content` **必须没有**: 它把弹窗收到"内容最小高度",
                   而 `ScrollView` 对 `minimum_height` 的贡献是 **0** ⇒ 列表会被压成 0 行。
             ② **表头独立排版**(按自己文字宽度分列、整行居中) + **全表共用一个字号**。
                表头因此**不调** `_fit_line`/`_fit1`, 字号由统一那段给。
             ③ 行高 `dp(26)`、脚注 `_auto_h(foot, dp(44))`、底部**两个按钮**(清空历史 + 关闭)。
        """
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        title_lbl = self._fit_line(Label(text='CPU高压测试历史', bold=True,
                                         halign='center', color=hex_rgb(COL_BALL) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
        content.add_widget(title_lbl)
        # ⚠️ 跑分**从中位数改为平均数** ⇒ 列头跟着改。三处口径必须同步: 这里 / 脚注 / 详情正文。
        _HP_COLS = ('时间', '平均值', '平均差系数')
        if not self.hp_history:
            # ⚠️ 空态**靠两根弹簧竖向居中**, 面板高度**不变**(与隔壁那张表逐字同款):
            #    不能因为"有没有记录"让面板忽大忽小。真有问题也只是"空的时候字堆在底下",
            #    而那该用**居中去解决, 不是改高度**。
            content.add_widget(Widget(size_hint_y=1))          # 上弹簧
            empty = Label(text='暂无 CPU 高压测试记录\n\n性能测试菜单里选「CPU高压测试」\n连续高压测试 %d 秒即可产生一条'
                               % int(SOC_SUSTAIN_WALL_SEC),
                          font_size='16sp', halign='center',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(110))
            empty.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
            content.add_widget(empty)
            content.add_widget(Widget(size_hint_y=1))          # 下弹簧
        else:
            # 列宽与隔壁**同一套量法**(`text_px(文本, sp(14))`, 桌面密度 1 ⇒ px == dp):
            #   时间 **113** · 平均值 `34652` **40** · 平均差系数 `2.14%` **41** · 「详情」**28**。
            #   留出呼吸量 ⇒ 110/58/62/58 = **288dp**(隔壁三列是 110/92/88 = 290dp —— 同一张脸)。
            # ⚠️ 上一版是 116/46/76/46: 中位数那格只剩 3px 边距, 真机上时间戳与它**糊成一片**
            #    (玩家截图为证: `2026-09-15 23:06:34652`)。这次三格都留出 9px 以上。
            # ⚠️ 窄屏按比例收(`_k`): `size_hint_x=None` 的子控件宽度不够时**不会自己缩**,
            #    会直接**溢出弹窗**(就是"字飘在游戏画面上"那一类)。
            _HW = (dp(110), dp(58), dp(62), dp(58))
            # ⚠️⚠️ **宽度适配**: 原来 `_k = min(1.0, _tw_max / sum(_HW))` **只缩不放**, 于是平板
            #    上表格还是 288dp、缩在中间两边各空一大截。⇒ 统一交给 `_fit_w()`: 宽屏按可用
            #    宽度等比放大(夹 `_TW_GROW_MAX`)、窄屏收缩、**手机上保持 1.0**。
            #    ⚠️ 分母是**基准** `sum(_HW)` —— 别拿缩放后的 `_HW` 再算一次(会自己乘自己)。
            _tw_max, _k = self._fit_w(sum(_HW))
            _HW = tuple(_w * _k for _w in _HW)
            _table_w = sum(_HW)
            # 表头**独立排版**: 各按自己文字的宽度分列, 整行居中(与隔壁同款)。
            # ⚠️ 宽度按 `sp(14)` 量(不是裸 14.0 —— 那是**绝对 px**, density=2 的机器上只有一半大)。
            _head_w = [min(_table_w / 4.0 * 1.6, max(dp(30), text_px(_t, sp(14) * _k) + dp(6)))
                       for _t in _HP_COLS]
            _hw_sum = sum(_head_w)
            if _hw_sum > _table_w:
                _head_w = [_w * _table_w / _hw_sum for _w in _head_w]
                _hw_sum = _table_w
            columns = BoxLayout(size_hint_x=None, size_hint_y=None, width=_hw_sum,
                                height=dp(22) * _k, pos_hint={'center_x': 0.5})
            _heads = []
            for _t, _w in zip(_HP_COLS, _head_w):
                h = Label(text=_t, halign='center', valign='middle',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_x=None)
                h.width = _w
                # ⚠️ 绑 `(w.width, None)` 而不是 `w.size`: 两维都给 ⇒ 宽度不够就**折行**,
                #    而这一排只有 dp(22) 高, 第二行直接被顶出格子。
                h.bind(size=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
                # ⚠️ **这里不调 `_fit_line`/`_fit1`** —— 表头要与数据行**同一个字号**,
                #    统一由下面"全表共用一个字号"那段来定(单独缩表头就会出现两种字号)。
                columns.add_widget(h)
                _heads.append(h)
            content.add_widget(columns)
            # ⚠️ **`size_hint=(1, 1)`, 不是写死高度** —— 它吃掉面板的剩余高度, 面板高度因此
            #    与记录数**无关**。
            scroll = ScrollView(size_hint=(1, 1))
            inner = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2) * _k)
            inner.bind(minimum_height=inner.setter('height'))
            rows = [[], [], []]          # 逐列一组, 只为下面"全表统一字号"取最长内容
            for r in reversed(self.hp_history[-100:]):
                stamp = _hist_stamp(r.get('time'))
                # ⚠️ 跑分 = **平均数**(见 `_hp_score`), 旧记录拿 windows 现算, 不印「—」。
                _avg = _hp_score(r)
                avg_text = '%d' % _avg if _avg is not None else '—'
                # ⚠️ **平均差系数**: 取代原来的「波动」与「归一化」两列。= 平均差 ÷ 均值;
                #    旧「波动」是 (max-min)/中位, 只看两端点。
                #    ⚠️ **旧记录没有这个字段** ⇒ 印「—」, **绝不拿别的字段回填**(印假数)。
                _mad = r.get('mad')
                mad_text = ('%.2f%%' % float(_mad)) if _mad is not None else '—'
                row = BoxLayout(size_hint_x=None, size_hint_y=None, width=_table_w,
                                height=dp(26) * _k, pos_hint={'center_x': 0.5})
                for _i, _t in enumerate((stamp, avg_text, mad_text)):
                    lbl = Label(text=_t, halign='center', valign='middle',
                                color=hex_rgb(COL_TEXT) + (1,), size_hint_x=None)
                    lbl.width = _HW[_i]
                    lbl.bind(size=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
                    row.add_widget(lbl)
                    rows[_i].append(lbl)
                # ⚠️ 最后一列是**按钮**(唯一一个), 不进"全表统一字号" —— 它不是数据格。
                btn = Button(text='详情', font_size=sp(14) * _k, bold=True,
                             background_normal='', size_hint_x=None, width=_HW[3],
                             background_color=hex_rgb(COL_BTN) + (1,))
                btn.bind(on_release=lambda _b, rr=r: self._show_hp_detail(rr))
                row.add_widget(btn)
                inner.add_widget(row)
            # ⚠️⚠️ **全表共用一个字号**: 逐**数据列**量出"这列最多能放多大"
            #    (`fit_font_size` 走同一套阶梯), 再取**最小**的那个发给**所有**格子。
            #    ⚠️ **不能逐列各缩** —— 表头(「平均差系数」5 字比数据长)会比数据行小一档,
            #       同一张表里出两种字号, 那正是玩家截图指出过的问题。
            #    ⚠️ **不能把所有格子塞进一个 `_fit_uniform`** —— 它取"组里最窄那列的宽度",
            #       会被最窄的列拖死、整表缩到 ~11sp。
            #    ⚠️ 必须按**数据列宽 `_HW`** 算, 不能按表头列宽 `_head_w`(踩过: 表头「时间」
            #       只有 2 个字, 拿它当可用宽度会把字号压到 **5.88sp**)。
            #    ⚠️ 最长内容**只从数据行取**, 表头不参与。
            #    ⚠️ 必须传 `sp(14)`, 不是裸 `14.0`(那是**绝对 px**, density=2 的机器上只有一半大)。
            _groups = [[_heads[_i]] + rows[_i] for _i in range(len(_HP_COLS))]
            _fs_all = None
            for _i, _g in enumerate(_groups):
                _long = max(rows[_i], key=lambda x: text_px(x.text or '', sp(14)))
                _f = fit_font_size(_long.text or '', sp(14) * _k, float(_HW[_i]))
                _fs_all = _f if _fs_all is None else min(_fs_all, _f)
            for _g in _groups:
                for _c in _g:
                    _c.font_size = _fs_all
            scroll.add_widget(inner)
            content.add_widget(scroll)
            # 脚注: 只回答"这一列是什么"。口径沿革属于代码注释, 面板上不写。
            # ⚠️ 括号里那个「群」字**不在字体子集里**(真机上显示成方块) ⇒ 那段整个删了即根治。
            # ⚠️ `h0` 与隔壁那张表**同一个值**(两张表脚注都是两行 12sp, 实测约 34px)。
            foot = Label(
                text=('  平均值：物理引擎每秒模拟步数的平均值\n'
                      '  平均差系数：平均差 ÷ 平均值，越小越稳'),
                font_size='12sp', halign='left', valign='top',
                color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
            self._auto_h(foot, dp(44))
            content.add_widget(foot)
        # ⚠️ 「清空历史」只在**有记录**时才放出来 —— 空列表上摆一个"清空"是没意义的热区,
        #    而且它离「关闭」只有 dp(8), 误触代价是**不可逆**的。(与隔壁同款)
        if self.hp_history:
            _acts = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            clear_btn = Button(text='清空历史', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_DARKRED) + (1,))
            close_btn = Button(text='关闭', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_BTN_OFF) + (1,))
            _acts.add_widget(clear_btn)
            _acts.add_widget(close_btn)
            content.add_widget(_acts)
        else:
            close_btn = Button(text='关闭', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_BTN_OFF) + (1,),
                               size_hint_y=None, height=dp(46))
            content.add_widget(close_btn)
        _vw, _vh = self._veq()
        # ⚠️ 宽 **0.96**(照搬隔壁): 四列固定宽度要 288dp, 而 0.92 在 360dp 机上只有 299dp 的
        #    内容区; 0.96 拿回 14.4px。
        # ⚠️ 高 `0.7 * _vh` 是**固定**的 —— 面板高度不随记录数变。
        # ⚠️ **末尾不调 `_popup_fit_content`** —— 理由见上面 docstring 第 ① 条。
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.96 * _vw, height=0.7 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        if self.hp_history:
            # ⚠️ 清空之后**当场把面板重开一次** —— 玩家要立刻看到空态, 而不是盯着
            #    一份已经删掉的旧表格。(旧面板先 dismiss, 否则会叠两层。)
            def _ask_clear(*_):
                popup.dismiss()
                self._clear_hp_history()
            clear_btn.bind(on_release=_ask_clear)
        popup.open()

    def _clear_hp_history(self):
        """清空「CPU 高压测试历史」——**不可逆, 所以必须先过一道确认**。

        ⚠️ 与 `_clear_bench_history` **同款**。
        ⚠️ **只清这一张表**(`plinko_hp_history.json`)。「模拟测试历史」是**另一张**
           (`plinko_bench_history.json`) —— 两张表分开是定过的案, 别一起清。
        ⚠️ 确认框里**必须写清条数**(「不可恢复」四个字不能省)。
        """
        _n = len(self.hp_history)
        if _n <= 0:
            return
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(10))
        ttl = self._fit_line(Label(text='清空 CPU 高压测试历史', bold=True, halign='center',
                                   color=hex_rgb(COL_TEXT) + (1,),
                                   size_hint_y=None, height=dp(28)), 19)
        content.add_widget(ttl)
        # ⚠️⚠️ **正文里绝不能出现 markdown 星号** —— Kivy 的 Label 不认 markdown,
        #    `**3**` 会在屏幕上**原样显示成 `**3**`**。
        msg = Label(text='将删除全部 %d 条 CPU 高压测试历史，\n不可恢复。\n\n'
                         '（模拟测试历史不受影响）' % _n,
                    font_size='15sp', halign='center', valign='middle',
                    color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
        self._auto_h(msg, dp(90), dp(6))
        content.add_widget(msg)
        acts = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        cancel = Button(text='取消', font_size='16sp', bold=True, background_normal='',
                        background_color=hex_rgb(COL_BTN_OFF) + (1,))
        ok = Button(text='确定清空', font_size='16sp', bold=True, background_normal='',
                    background_color=hex_rgb(COL_DARKRED) + (1,))
        acts.add_widget(cancel)
        acts.add_widget(ok)
        content.add_widget(acts)
        popup = self._popup(0.86, 260, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _confirm(*_):
            self.hp_history = []
            self._save_hp_history()          # 盘上也要清, 否则重启又回来了
            popup.dismiss()
            _set_label_text(self.status_lbl, 'CPU 高压测试历史已清空')
            self._show_hp_history()          # 当场重开 → 看到空态

        cancel.bind(on_release=popup.dismiss)
        ok.bind(on_release=_confirm)
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_hp_curve(self, windows, sec=None, title=None, unit='步/秒', unit_name='窗口',
                       value_decimals=0, flat_min_range=None,
                       windows2=None, times2=None, dt=None, unit2='',
                       value_decimals2=1, flat_min_range2=None, axis_unit='',
                       save_log=False, log_extra=None,
                       variants=None, cur_key=None):
        """CPU 高压的**一条曲线**(结果弹窗 / 历史详情上的按钮)。

        ⚠️⚠️ **三个调用点共用这一个**:
             · 成绩曲线 —— 逐秒的**步/秒**(数据 = `windows`);
             · 频率曲线 —— 的 **MHz**(数据 = `freq_series`);
             · 功率曲线 —— 的**电池功率(W)**(数据 = `power_series`, 5Hz 定长网格, 含 `None`
               = 那一格没读到; 时刻由 `dt` 推出来)。温度**不进图**(只在面板文字里), 所以
               这条曲线**是单轴**。`SpeedCurve` 的双轴能力留着没删, 但目前**没有调用点**。
           纵轴那套规则(上下留白 + 向外取整到友好刻度 + 保底离底 5%)整个在 `SpeedCurve` 里
           ⇒ 这里**只换标题、单位、和"一个点代表什么"**, 不碰轴。
        ⚠️ 没数据(或只有 1 个点)就**不开弹窗** —— 一条直线没信息, 不如不给。
        ⚠️ 双轴的两条序列**长度本来就不同**(功率 ~1790 / 温度 ~359), 所以 `SpeedCurve` 内部
           一律**按秒**画横轴 —— 这里只负责把时刻表传对。
        """
        # ⚠️⚠️ **不能在这里过滤 `None`** —— 功率那条的时刻是按**原下标 × dt** 推出来的,
        #    过滤掉一个点会让它**后面所有点左移一格**(实测: 末点从 357.8s 变成 357.2s)。
        #    ⇒ 原样传下去, 由 `SpeedCurve` 自己跳过 None 点; 这里只数"够不够两个有效点"。
        #    (`None > 0` 在 Python 3 里会抛 TypeError, 老写法就是 `if x > 0`。)
        _w = list(windows or [])
        if len([x for x in _w if x is not None and x > 0]) < 2:
            return
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        # ⚠️⚠️ **不能叫 `title`** —— 那会遮蔽入参 `title`(字符串)。而下面给粒度切换拼的
        #    `_kw` 要拿**原始的字符串**去重开弹窗; 一旦这里覆盖成 Label 控件, `_kw["title"]`
        #    抓到的就是控件 ⇒ 切粒度时 `Label(text=<Label>)` ⇒
        #    `ValueError: Label.text accept only str`。
        _title_lbl = self._fit_line(Label(text=(title or '高压CPU测试的成绩曲线'),
                                          bold=True, halign='center',
                                          color=hex_rgb(COL_TEXT) + (1,),
                                          size_hint_y=None, height=dp(26)), 19)
        content.add_widget(_title_lbl)
        curve = SpeedCurve(_w, value_decimals=value_decimals, flat_min_range=flat_min_range,
                           vals2=windows2, times2=times2, dt=dt, unit2=unit2,
                           value_decimals2=value_decimals2, flat_min_range2=flat_min_range2,
                           unit=axis_unit,
                           size_hint_y=None, height=dp(240))
        content.add_widget(curve)
        # ⚠️⚠️ 那一行「逐秒步/秒 首 x 末 x 中位 x 最低 x 最高 x」**整个删掉了** ——
        #    那几个数在**详情面板**里照旧有(首/末/最低/平均, 逐轮分数也在), 所以删它不丢信息。
        #    (「（横线 = 纵轴刻度）」那个括号也一起删了。)
        if curve._dual:
            # 双轴必须**说清哪条是哪条**。颜色串从画线用的**同一批常量**拼 ——
            # 哪天调色板改了, 图例不会说谎。
            _n2 = Label(text=('[color=%s]%s（左轴）[/color]　[color=%s]%s（右轴）[/color]'
                              % (COL_BALL, unit or '功率', COL_FIRE, unit2 or '温度')),
                        markup=True,
                        font_size='12sp', halign='center', valign='middle',
                        color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(20))
        else:
            # `variants` 的一项 = `(序列, 一个点代表什么)` —— 粒度不同, 说明行也得跟着变
            if variants and cur_key and cur_key in variants:
                try:
                    unit_name = variants[cur_key][1]
                except Exception:
                    pass
            _n2 = Label(text='横轴 = 按时间顺序的 %d 个%s　竖轴单位是%s'
                             % (len(_w), unit_name, unit),
                        font_size='12sp', halign='center', valign='middle',
                        color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(20))
        _n2.bind(width=lambda _w2, *_: setattr(_w2, 'text_size', (_w2.width, None)))
        content.add_widget(_n2)
        # ---- 粒度切换 ----
        # ⚠️ 实测「每 3 秒」**去不掉那些峰**: 两根挨着的峰合起来 = 10 格连续高值(2 秒),
        #    3 秒一组它占 2/3 ⇒ 均值 5.71 / 中位 5.94, 峰还在。
        #    **5 秒一组(25 格)它只占 40%** ⇒ 中位数被平台拉回来, max 7.60 → 3.81, 一个不剩。
        if variants:
            _kw = dict(sec=sec, title=title, unit=unit, unit_name=unit_name,
                       value_decimals=value_decimals, flat_min_range=flat_min_range,
                       dt=dt, axis_unit=axis_unit, save_log=save_log, log_extra=log_extra,
                       variants=variants, value_decimals2=value_decimals2,
                       flat_min_range2=flat_min_range2, unit2=unit2,
                       windows2=windows2, times2=times2)
            _vrow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            for _k in variants:
                _vb = Button(text=_k, font_size="16sp", bold=True, background_normal="",
                             background_color=hex_rgb(
                                 COL_BTN if _k == cur_key else COL_BTN_OFF) + (1,))
                _vb.bind(on_release=lambda *_a, _kk=_k: self._reopen_curve(_kk, variants, _kw))
                _vrow.add_widget(_vb)
            content.add_widget(_vrow)
        close_btn = Button(text='返回', font_size='16sp', bold=True, background_normal='',
                           background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        if save_log:
            # ⚠️ **只给功率曲线传 `save_log=True`** —— 那份 txt 里是功率的**原始采样**
            #    (整数电流 + 当时的电压), 落盘的历史记录里没有这两条。
            _srow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            _save_btn = Button(text='保存记录', font_size='16sp', bold=True,
                               background_normal='', background_color=hex_rgb(COL_SOC) + (1,))
            _srow.add_widget(_save_btn)
            _srow.add_widget(close_btn)
            content.add_widget(_srow)
            # ⚠️ 数据**在闭包里捕获**, 不能等点击时再读 `self._hp_*` —— 那时可能已经被
            #    下一局覆盖, 而翻历史那条路的数据根本不在 `self` 里。
            _logp = list(_w)
            _loge = dict(log_extra or {})
            _save_btn.bind(on_release=lambda *_: self._on_save_power_click(
                _save_btn, _logp, _loge))
        else:
            content.add_widget(close_btn)
        popup = self._popup(0.92, 400, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _reopen_curve(self, key, variants, kw):
        """切曲线粒度: 关掉当前弹窗, 用 `key` 那套数据重开一个。

        ⚠️ **参数在闭包里整个捕获**(`kw`), 不能等点击时再去读 `self._hp_*` —— 那时可能
           已经被下一局覆盖, 而翻历史那条路的数据根本不在 `self` 里。
        """
        for _w in list(Window.children):
            if isinstance(_w, Popup):
                try:
                    _w.dismiss()
                except Exception:
                    pass
        try:
            _w2 = variants[key][0]
        except Exception:
            return
        # ⚠️ 粒度**是保存的**, 而且要**当场**生效 —— 写进状态 + 存盘 +
        #    **立刻重算面板那三个数**, 否则要等下一次跑完测试才看到新口径。
        self.game.power_grain = key
        try:
            self.game._save_config()
        except Exception:
            pass
        self._recalc_power_stats()
        self._show_hp_curve(_w2, cur_key=key, **kw)

    def _recalc_power_stats(self):
        """按**当前粒度**重算面板那三个数(`_hp_battery` 的 power_*)。

        ⚠️ 只动**现场**那一份(`_hp_battery`) —— 历史记录是只读的, 翻历史切粒度只影响曲线,
           面板(历史详情)仍按记录里的原始值显示。
        ⚠️ `power_n` 也跟着变(平滑后点数少了) —— 它现在表示"这一档有多少个点", 与曲线一致。
        """
        _s = list(getattr(self, "_hp_power_series", None) or [])
        _b = getattr(self, "_hp_battery", None)
        if not _s or not isinstance(_b, dict):
            return
        _gk = POWER_GRAIN_K.get(self.game.power_grain, 1)
        _g = (_med5(_s, _gk) if _gk > 1 else _s)
        _o = [x for x in _g if x is not None]
        if not _o:
            return
        _b["power_mean"] = round(sum(_o) / len(_o), 2)
        _b["power_min"] = min(_o)
        _b["power_max"] = max(_o)
        _b["power_n"] = len(_o)

    def _on_save_power_click(self, btn, pw=None, extra=None):
        """点「保存记录」: 落盘, 并把结果**当场写在按钮上**(绝不静默失败)。

        ⚠️ 与逐帧日志那条同一个规矩: 把**真实结果**说出来(成功给到哪儿了 / 失败为什么),
           而不是只说一句"已保存" —— 玩家下一步要拿着这个文件去找它。
        """
        try:
            _ok, _msg = self._save_power_log(pw, extra)
        except Exception as _e:
            _ok, _msg = False, "保存失败: %r" % (_e,)
        try:
            btn.text = _msg[:30]
            Clock.schedule_once(lambda _d: setattr(btn, 'text', '保存记录'), 3.0)
        except Exception:
            pass
        return _ok

    def _show_hp_detail(self, r):
        """某一条 CPU 高压记录的**详细成绩 + CPU 平均频率**。"""
        # ⚠⚠ 正文改走**与结果弹窗共用的** `_hp_result_text` —— 原来那套「成绩/频率/过程」
        #    三段版式**已删**(它与结果弹窗是**两套说法**: 同一个数一个写「平均 A / 最低 B」、
        #    另一个写「最低 B，平均 A」)。
        #    ⚠️ `opt_lines` 传空: 锁核/提优先级那两行是 **android 运行时状态**,
        #       记录里没存 ⇒ **不印假值**。
        #    ⚠️ 头一行只有 **设备 + 时间**; 记录里的 `version` 字段**照旧存着**(数据不动),
        #       只是不上屏 —— 哪天想印回来不用改存档格式。
        _txt = self._hp_result_text({
            'head': str(r.get('device', '?')).strip() + chr(10) + str(r.get('time', '--')),
            'sec': r.get('sec', 0),
            'first': r.get('first'), 'last': r.get('last'),
            'min': r.get('min'), 'decay': r.get('decay', 0.0),
            'windows': list(r.get('windows') or []),
            'freq_mean': r.get('freq_mean', 0), 'freq_p50': r.get('freq_p50', 0),
            'freq_min': r.get('freq_min', 0), 'freq_max': r.get('freq_max', 0),
            'freq_n': r.get('freq_n', 0),
            'thermal': r.get('thermal'), 'zones': r.get('zones'),
            'battery_mean': r.get('battery_mean'), 'battery_min': r.get('battery_min'),
            'battery_max': r.get('battery_max'), 'battery_n': r.get('battery_n', 0),
            # ---- 功率那一族 --------------------------------------------------------
            # ⚠️⚠️ **只在记录里真有这些键时才搬** —— 渲染端 `_hp_result_text` 的判据是
            #    「键在不在」, 不是「值是不是 None」。老记录根本没有 `power_*`, 若这里一律搬成
            #    None, 翻历史就会把"当时没采集"印成"这台采不到"(假结论)。
            #    ⇒ 展开时过滤掉记录里没有的键。
            **{_k: r[_k] for _k in (
                'power_mean', 'power_min', 'power_max', 'power_n', 'power_dt',
                'power_src', 'power_unit', 'power_stats', 'power_plugged',
                'volt_mean', 'volt_min', 'volt_max',
                'amp_mean', 'amp_min', 'amp_max', 'wh', 'ppw') if _k in r},
        })
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        title_lbl = self._fit_line(Label(text='CPU高压测试详情', bold=True,
                                         halign='center', color=hex_rgb(COL_BALL) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
        content.add_widget(title_lbl)
        body = Label(text=_txt, font_size='14sp', halign='left', valign='top',
                     color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None)
        self._auto_h(body, dp(220), dp(6))
        content.add_widget(body)
        # ⚠️ 数据用**记录里那份** `windows`(逐秒一个)。不够 2 个点就**不建**按钮
        #    (`_show_hp_curve` 自己也会拒)。
        _btnrow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6),
                            orientation='horizontal')
        _cw = [x for x in (r.get('windows') or []) if x > 0]
        if len(_cw) >= 2:
            curve_btn = Button(text='成绩曲线', font_size='14sp', bold=True,
                               background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
            curve_btn.bind(on_release=lambda *_: self._show_hp_curve(_cw))
            _btnrow.add_widget(curve_btn)
        # ⚠️ 「**频率曲线**」夹在成绩曲线与关闭之间。
        #    数据用**记录里那份** `freq_series`(老记录没这个字段 ⇒ 自然不建按钮, 不印假图)。
        _fw = [x for x in (r.get('freq_series') or []) if x > 0]
        if len(_fw) >= 2:
            freq_btn = Button(text='频率曲线', font_size='14sp', bold=True,
                              background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
            freq_btn.bind(on_release=lambda *_: self._show_hp_curve(
                _fw, title='高压CPU测试的频率曲线', unit='MHz', unit_name='采样'))
            _btnrow.add_widget(freq_btn)
        # ⚠️ 与现场那个按钮**同一套逻辑**(只画功率), 只是数据从**记录**里取:
        #    `power_series`(定长 5Hz 网格) + `power_dt`(格宽)。
        #    老记录 / 读不到功率的记录没有这些键 ⇒ **不建按钮**(温度不进图了, 没有可降级的东西)。
        # ⚠️ 变量名带 `2` 后缀: 别和 `_hp_done` 那个按钮的闭包变量撞车。
        _pw2 = list(r.get('power_series') or [])
        if len([x for x in _pw2 if x is not None]) >= 2:
            _dt2 = (r.get('power_dt') or 0.2)
            power_btn = Button(text='功率曲线', font_size='14sp', bold=True,
                               background_normal='', background_color=hex_rgb(COL_SOC) + (1,))
            # ⚠️ 与现场那条**同一套**(两个粒度 + 导出原始值), 数据从记录里取。
            _v2 = {"每帧": (list(_pw2), "采样"),
                   "每5秒": (_med5(_pw2, POWER_GRAIN_K["每5秒"]), "5秒段")}
            _ck2 = self.game.power_grain
            power_btn.bind(on_release=lambda *_: self._show_hp_curve(
                _v2[_ck2][0], dt=_dt2, title='CPU高压测试的功率曲线',
                unit='W', unit_name='采样', value_decimals=2, flat_min_range=1.0,
                axis_unit='W', save_log=True, variants=_v2, cur_key=_ck2,
                # ⚠️ 老记录里**没有**原始电流/电压序列, 所以这三项**显式传空** ——
                #    让 txt 里那几列印 nan, 而不是退回 `self._hp_*`(上一局的残值)。
                #    游程那两行只用 W 序列, 照样算得出来。
                log_extra={"pt": [], "raw": [], "mv": [],
                           "meta": {"dt": r.get('power_dt') or 0.2,
                                    "src": r.get('power_src'), "unit": r.get('power_unit'),
                                    "stats": r.get('power_stats')},
                           "panel": r, "head": r.get('device')}))
            _btnrow.add_widget(power_btn)
        close_btn = Button(text='关闭', font_size='14sp', bold=True,
                           background_normal='',
                           background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        _btnrow.add_widget(close_btn)
        content.add_widget(_btnrow)
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.88 * _vw, height=0.62 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_bench_detail(self, r):
        """「测试历史（渲染 / CPU）」某一条的**详情** —— 点开的就是跑完那一刻的成绩面板。

        ⚠️ 正文走 `_bench_score_text(r)` —— **与现场那个成绩面板共用同一份渲染**,
           所以两边永远长一样(两处各写一份迟早脱钩)。
        ⚠️ **不存逐帧数据**: 帧率曲线**只有刚跑完那一次**能看(原始采样还在内存里);
           历史行**能找到数据就给按钮, 找不到就不给**。
        """
        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(8))
        # 历史详情标题不带版本号; 版本仍保留在记录字段中供追溯。
        title_lbl = self._fit_line(Label(text='画面帧率和性能测试', bold=True, halign='center',
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 20)
        content.add_widget(title_lbl)
        body = Label(text=_bench_score_text(r), markup=True, font_size='14sp', halign='left',
                     valign='top', color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None)
        self._auto_h(body, dp(190), dp(6))
        content.add_widget(body)
        # ⚠️⚠️ 下面那块**灰色字**就是**诊断块**(采样窗口 / 卡顿帧 / 慢帧 / 慢帧分布 / 最慢一帧)
        #    —— 它原来**只在跑完那一刻的面板上**(现场是**独立的一个灰标签**), 而记录里没存
        #    ⇒ 详情弹窗就缺了。现在记录里存了 `diag`, 这里**照现场同样的样式**再加一个灰标签。
        #    ⚠️ 老记录没有 `diag` ⇒ 这段是空串 ⇒ **那个标签整个不 add**(不印空壳, 不留空白)。
        _diag_txt = self._bench_low_summary_text(r.get('diag'))
        if _diag_txt:
            diag_lbl = Label(text=_diag_txt, font_size='14sp', halign='left', valign='top',
                             color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(110))
            self._auto_h(diag_lbl, dp(0), dp(0))
            content.add_widget(diag_lbl)
        # ⚠️ **不存任何逐帧数据**(省掉 100 条 × 数千个数): 原始采样 `_render_gaps_ms` 就在
        #    内存里, 只有它还在、而且这条记录就是**刚跑的那一次**时才给按钮。
        #    ⚠️ 判据是 `r is self.bench_history[-1]` **且** `_render_gaps_ms` 非空:
        #       重启后最新那条是上一局的记录, 而内存里已经没有逐帧数据
        #       ⇒ **不给按钮**(给一个点开是**空图**的按钮, 比没按钮更糟)。
        #    ⚠️ 这条路上 `_show_fps_curve()` **带默认参数**调——它读的就是内存里那一轮,
        #       而那一轮正好就是这条记录 ⇒ 「保存日志」也是**对的**那一轮, 不用屏。
        _btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        _live_gaps = list(getattr(self, '_render_gaps_ms', None) or [])
        if _live_gaps and self.bench_history and (r is self.bench_history[-1]):
            curve_btn = Button(text='帧率曲线', font_size='16sp', bold=True,
                               background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
            curve_btn.bind(on_release=lambda *_: self._show_fps_curve())
            _btns.add_widget(curve_btn)
        close_btn = Button(text='关闭', font_size='16sp', bold=True, background_normal='',
                           background_color=hex_rgb(COL_BTN_OFF) + (1,))
        _btns.add_widget(close_btn)
        content.add_widget(_btns)
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.90 * _vw, height=0.66 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_bench_history(self):
        """性能测试历史：每次完整测试严格一行，保留时间、帧率与 CPU 波动。

        ⚠️ 第三列表头是「**平均分 / 平均差系数**」: 那一格是 `phys_fps`, 来源是
           `_bench_done` 里的 **平均值**(从中位数改的; 老记录里存的是当年的中位数
           ⇒ 跨版本比成绩别混着看)。表头写「步数」会让人以为是总步数或均值。
        """
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        # 这是“数据表”而不是一段左对齐正文：三列标题与数值均居中，扫视同一行时能更快对应；
        # 时间列仍固定足够宽，完整年份不会被压缩。
        # ⚠️⚠️ **标题金色、备注灰色 —— 与更早那版正好对调**: 整块面板的层次是
        #    **标题金(主) > 数据近白 > 表头/备注灰(次)**。这是**有意反转**旧决定, 不是漂移。
        title_lbl = self._fit_line(Label(text='测试历史（渲染 / CPU）', bold=True,
                                         halign='center', color=hex_rgb(COL_BALL) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
        content.add_widget(title_lbl)
        if not self.bench_history:
            # ⚠️⚠️ **弹窗高度固定不变, 空态靠两根"弹簧"竖向居中。**
            #    这个面板以后是要装数据的, 高度必须**始终一样**, 否则"有没有记录"会让面板
            #    忽大忽小、读数时跳来跳去; 真有问题也只是"空的时候字堆在底下",
            #    而那该用**居中去解决, 不是改高度**。
            #    ⚠️ 弹簧只加在**空态**: 有记录时那块是 `size_hint=(1, 1)` 的 ScrollView,
            #       它自己就会把剩余空间吃掉, 再加弹簧反而挤掉列表。
            content.add_widget(Widget(size_hint_y=1))          # 上弹簧
            empty = Label(text='暂无测试记录\n\n长按标题 3 秒即可测试', font_size='16sp', halign='center',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(90))
            empty.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
            content.add_widget(empty)
            content.add_widget(Widget(size_hint_y=1))          # 下弹簧
        else:
            # ⚠️⚠️ **固定列宽对齐(准表格)**: 前一版是"整行一串空格分隔、上下不对齐",
            #    起因是四列按各自最宽算要 333px 而内容区只有 299.2px —— 对齐是硬要求,
            #    所以**删掉了「CPU平均频率」那一列**(它已被实测证伪)来腾宽度。
            # ⚠️ 三列列宽**量出来的**(`text_px` 非粗体 14sp): 时间 **76** · `平均/1%Low帧`
            #    (**表头比数据长**) **61** · `20300/1.23%` **86** ⇒ 配成 80/66/92/50 = **288**
            #    (**+50 是「详情」按钮列**), 与原来 290 同量级, 弹窗宽度一个字不用改。
            #    ⚠️ 字号账: 三列全都放得下 ⇒ **全表字号回到 sp(14)**。
            #    ⚠️ 加列最容易挤坏的**不是文字, 是那个按钮**(旧事故: 「详情」被挤成 25px)。
            _HW = (dp(80), dp(66), dp(92), dp(50))
            # ⚠️⚠️ **宽度适配**: 这一处原来**连缩放都没有**(`_table_w = sum(_HW)` 写死), 于是
            #    平板上表格缩在中间、两边各空一大截。⇒ 和 `_show_hp_history` 那处**共用同一个
            #    `_fit_w()`**: 同一个门槛、同一个口径。
            #    ⚠️ 手上这两台设备里**只有平板会被放大**(手机上 `_fit_w` 返回 1.0) ——
            #       所以这一处虽然原来"从没缩放过", 手机侧的观感也不会变。
            _tw_max, _k = self._fit_w(sum(_HW))
            _HW = tuple(_w * _k for _w in _HW)
            _table_w = sum(_HW)
            # ⚠️⚠️ **表头独立排版**: 表头**不再**和数据行共用 `_HW` —— 表头「时间」只要 ~26px,
            #    而数据要 **113px**, 本来就不该共用一套宽度。共用的时候, 表头里最长的那一列会把
            #    **全表字号**往下压(实测把 13.16sp 压到 **11.48sp**, 整张表的字都变小 ——
            #    那才是真正的回归, 比"列没对齐"严重得多)。
            #    ⇒ 表头按**自己文字的宽度**分列、整行居中; 数据行照旧用 `_HW`。
            #    代价: 表头与数据**不逐列对齐** —— 已确认接受这一条。
            #    ⚠️ 宽度按基准 `sp(14)` 量(不是裸 14.0 —— 那是**绝对 px**, density=2 的机器上
            #       只有一半大), 再夹到不超过数据行总宽。
            # ⚠️⚠️ 分母是 **3**(= 表头**自己**有几格), **不是 4**(数据行的列数):
            #    改成 4.0 会把上限压到 115px, 而表头「平均分/平均差系数」要 **117px**
            #    ⇒ **折成两行**, 而这一排只有 dp(22) 高, 第二行被顶出格子。
            _head_w = [min(_table_w / 3.0 * 1.6, max(dp(30), text_px(_t, sp(14) * _k) + dp(6)))
                       for _t in _HIST_COLS]
            _hw_sum = sum(_head_w)
            if _hw_sum > _table_w:
                _head_w = [_w * _table_w / _hw_sum for _w in _head_w]
                _hw_sum = _table_w
            # 固定列宽表格不能默认贴在父容器左边；宽屏设备上这会明显偏左，
            # 窄屏设备上也会造成标题/数据与面板中心不一致。表头和每一行各自整体居中。
            columns = BoxLayout(size_hint_x=None, size_hint_y=None, width=_hw_sum,
                                height=dp(22) * _k, pos_hint={'center_x': 0.5})
            _heads = []
            for _t, _w in zip(_HIST_COLS, _head_w):
                h = Label(text=_t, halign='center', valign='middle',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_x=None)
                h.width = _w
                # ⚠️ 绑 `(w.width, None)` 而不是 `w.size`: 两维都给 ⇒ 宽度不够就**折行**,
                #    而这一排只有 dp(22) 高, 第二行直接被顶出格子。
                h.bind(size=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
                # ⚠️ **这里不调 `_fit_line`/`_fit1`** —— 表头要和数据行**同一个字号**,
                #    统一由下面那段"全表共用一个字号"来定。单独缩表头就会出现两种字号。
                columns.add_widget(h)
                _heads.append(h)
            content.add_widget(columns)
            scroll = ScrollView(size_hint=(1, 1))
            inner = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2) * _k)
            inner.bind(minimum_height=inner.setter('height'))
            # ⚠️ **逐列一组**, 但字号**不逐列定** —— 见下面"全表共用一个字号"那一段。
            rows = [[], [], []]
            for r in reversed(self.bench_history[-100:]):
                # ⚠️ 字号/行高与另外两个列表弹窗**对齐**: 同一个滚动框里能多放约两行
                #    (这个设计的目的是放更多内容的)。
                render, low = r.get('render_fps'), r.get('render_1low')
                stamp = _hist_stamp(r.get('time'))
                if render is None or low is None:
                    fps_text = '—'
                else:
                    fps_text = '%.1f/%.1f' % (float(render), float(low))
                # ⚠️⚠️ **「CPU平均频率」那一格删掉了**: 它**已被实测证伪** —— 真机上大核报
                #    2712MHz 而同一轮的纯算术探针低了 16%; 高通 LMH 平台上 `scaling_cur_freq`
                #    报的是**调频器的目标值**, 实际时钟被硬件按下去**不回写**。
                #    **留一个会误导的列, 还挤掉对齐要用的宽度。**
                #    ⚠️ `phys_freq_mean` **照旧存在 JSON 里**, 只是**不再显示**。
                # ⚠️ 这里原来印的是「波动」(= (max-min)/中位), 换成**平均差系数**。
                #    ⚠️ 旧记录没有 `phys_mad` ⇒ 印「—」, **不回填、不印假数**。
                mad = r.get('phys_mad')
                if mad is None:
                    soc_text = '%d/—' % r.get('phys_fps', 0)
                else:
                    soc_text = '%d/%.1f%%' % (
                        r.get('phys_fps', 0), float(mad))
                # ⚠️⚠️ **「归一化」那一列移出面板了**: 实测它在跨设置之间**并不稳定**,
                #    还不配当"公平秤"; 而它占的宽度正好把**完整时间戳**挤掉了。
                #    ⚠️ `phys_norm` **照旧存在 JSON 里**, 只是**不再显示**。
                # ⚠️ **整行改成一排固定列宽的 Label**: 列宽与表头**共用 `_HW`**。
                row = BoxLayout(size_hint_x=None, size_hint_y=None, width=_table_w,
                                height=dp(26) * _k, pos_hint={'center_x': 0.5})
                for _i, (_t, _w) in enumerate(zip((stamp, fps_text, soc_text), _HW)):
                    lbl = Label(text=_t, halign='center', valign='middle',
                                color=hex_rgb(COL_TEXT) + (1,), size_hint_x=None)
                    lbl.width = _w
                    # ⚠️ 绑 `(w.width, None)` 而**不是** `w.size`: 两维都给 ⇒ 宽度不够就折行。
                    lbl.bind(size=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
                    row.add_widget(lbl)
                    rows[_i].append(lbl)
                # ⚠️ 第四列是**按钮**(唯一一个), **不进"全表统一字号"** —— 它不是数据格。
                btn = Button(text='详情', font_size=sp(14) * _k, bold=True,
                             background_normal='', size_hint_x=None, width=_HW[3],
                             background_color=hex_rgb(COL_BTN) + (1,))
                btn.bind(on_release=lambda _b, rr=r: self._show_bench_detail(rr))
                row.add_widget(btn)
                inner.add_widget(row)
            # ⚠️⚠️ **全表共用一个字号**: **逐列**量出"这一列最多能放多大"
            #    (`fit_font_size` 走的是同一套阶梯), 然后取**最小的那个**发给**所有**格子。
            #    ⚠️ **不能逐列各缩** —— 表头(比数据长)会比数据行小一档, 同一张表里出两种字号。
            #    ⚠️ **不能把所有格子塞进一个 `_fit_uniform`** —— 它取的是"组里最窄那列的宽度",
            #       会被最窄列(88px)拖死, 整表缩到 ~11sp。
            #    ⚠️ 必须 `sp(14)` 而不是 `14.0`: 这个形参是**绝对字号(px)**, 传裸 14.0 在
            #       density=2 的机器上只有一半大(实测被探针逮住过)。
            # ⚠️ **按数据列数**(`_HIST_COLS`, 3), 不是 `len(_HW)`(4) —— `_HW` 多出来的那一项
            #    是按钮列, 而 `rows` 只有 3 组 ⇒ 按 4 会 IndexError。
            _groups = [[_heads[_i]] + rows[_i] for _i in range(len(_HIST_COLS))]
            _fs_all = None
            for _i, _g in enumerate(_groups):
                # ⚠️⚠️ **字号必须按「数据列宽」`_HW` 算, 不能按表头列宽 `_head_w`**(踩过):
                #    表头独立排版之后, `_g[0]` 是**表头** Label, 拿 `_g[0].width` 当可用宽度
                #    ⇒ 第一列的表头「时间」只有 2 个字(30dp), 却要塞下该列最长的内容(113px)
                #    ⇒ 字号被压到 **5.88sp**。表头只是个标签, 它的宽度不该约束数据的字号。
                _w = float(_HW[_i])
                _bd = bool(getattr(_g[0], "bold", False))
                # ⚠️ **最长内容只从「数据行」里取, 表头不参与** —— 表头有自己的宽度, 拿它来
                #    约束字号会把整张表拖小(实测: 表头 9 个字把全表从 13.16sp 拖到 **10.64sp**)。
                _long = max(rows[_i], key=lambda x: text_px(x.text or "", sp(14), _bd))
                _f = fit_font_size(_long.text or "", sp(14) * _k, _w, _bd)
                _fs_all = _f if _fs_all is None else min(_fs_all, _f)
            for _g in _groups:
                for _c in _g:
                    _c.font_size = _fs_all
            scroll.add_widget(inner)
            content.add_widget(scroll)
            # 底部口径说明。⚠️ **两个「中位」不是同一个东西**:
            #   左列那个中位是**渲染帧率**(每秒画了多少帧),
            #   右列那个中位是**物理吞吐**(每秒模拟多少步) —— 两列各自取自己的中位数。
            # ⚠️⚠️ 三条规矩: ① **平均/1%Low帧 不解释**(常识, 占了小半屏还折行), 只留这个面板
            #    **特有**的两条; ② **手工折行 + 全角空格做悬挂缩进**(自动折出来的续行没有缩进,
            #    几条会糊成一片); ③ 高度走 `_auto_h`(写死高度会让最后一行被「关闭」按钮压掉一半)。
            #    `**中位数**` 那种星号是 markdown 残留, Kivy 不认, 会在屏幕上原样显示 ——
            #    强调一律用「」。
            foot = Label(
                text=('  平均分：物理引擎每秒模拟步数的平均值\n'
                      '  平均差系数：平均差 ÷ 平均值，越小越稳'),
                font_size='12sp', halign='left', valign='top',
                color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
            self._auto_h(foot, dp(44))
            content.add_widget(foot)
        # ⚠️ 「清空历史」只在**有记录**时才放出来 —— 空列表上摆一个"清空"是没意义的热区,
        #    而且它离「关闭」只有 dp(8), 误触代价是**不可逆**的。
        if self.bench_history:
            _acts = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            clear_btn = Button(text='清空历史', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_DARKRED) + (1,))
            close_btn = Button(text='关闭', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_BTN_OFF) + (1,))
            _acts.add_widget(clear_btn)
            _acts.add_widget(close_btn)
            content.add_widget(_acts)
        else:
            close_btn = Button(text='关闭', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_BTN_OFF) + (1,),
                               size_hint_y=None, height=dp(46))
            content.add_widget(close_btn)
        # 宽高同 _popup(): 必须吃等效竖屏窗口, 不能用 size_hint(见 _popup 的说明)
        # ⚠️ 宽 **0.96**: 固定列宽要 340px 而 0.92 只有 275px。
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.96 * _vw, height=0.7 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        if self.bench_history:
            # ⚠️ 清空之后**当场把面板重开一次** —— 玩家要立刻看到空态, 而不是盯着
            #    一份已经删掉的旧表格。(旧面板先 dismiss, 否则会叠两层。)
            def _ask_clear(*_):
                popup.dismiss()
                self._clear_bench_history()
            clear_btn.bind(on_release=_ask_clear)
        popup.open()

    def _clear_bench_history(self):
        """清空「模拟测试历史」——**不可逆, 所以必须先过一道确认**。

        ⚠️ **只清这一张表**(`plinko_bench_history.json`)。CPU 高压历史是**另一张**
           (`plinko_hp_history.json`) —— 两张表分开是定过的案, 别一起清。
        ⚠️ 确认框里**必须写清条数**(「不可恢复」这四个字不能省)。
        """
        _n = len(self.bench_history)
        if _n <= 0:
            return
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(10))
        ttl = self._fit_line(Label(text='清空模拟测试历史', bold=True, halign='center',
                                   color=hex_rgb(COL_TEXT) + (1,),
                                   size_hint_y=None, height=dp(28)), 19)
        content.add_widget(ttl)
        # ⚠️⚠️ **正文里绝不能出现 markdown 星号** —— Kivy 的 Label 不认 markdown,
        #    `**3**` 会在屏幕上**原样显示成 `**3**`**。
        msg = Label(text='将删除全部 %d 条模拟测试历史，\n不可恢复。\n\n'
                         '（CPU 高压测试历史不受影响）' % _n,
                    font_size='15sp', halign='center', valign='middle',
                    color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
        self._auto_h(msg, dp(90), dp(6))
        content.add_widget(msg)
        acts = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        cancel = Button(text='取消', font_size='16sp', bold=True, background_normal='',
                        background_color=hex_rgb(COL_BTN_OFF) + (1,))
        ok = Button(text='确定清空', font_size='16sp', bold=True, background_normal='',
                    background_color=hex_rgb(COL_DARKRED) + (1,))
        acts.add_widget(cancel)
        acts.add_widget(ok)
        content.add_widget(acts)
        popup = self._popup(0.86, 260, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _confirm(*_):
            self.bench_history = []
            self._save_bench_history()          # 盘上也要清, 否则重启又回来了
            popup.dismiss()
            _set_label_text(self.status_lbl, '模拟测试历史已清空')
            self._show_bench_history()          # 当场重开 → 看到空态

        cancel.bind(on_release=popup.dismiss)
        ok.bind(on_release=_confirm)
        popup.open()
        self._popup_fit_content(popup, content)

    # ---------------------------------------------------------------- 历史存取
    @staticmethod
    def _bench_history_path():
        """性能测试历史 JSON 路径(与轮次历史同目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_bench_history.json")

    def _load_bench_history(self):
        try:
            with open(self._bench_history_path(), "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.bench_history = data[-100:]
        except Exception:
            pass

    def _save_bench_history(self):
        try:
            with open(self._bench_history_path(), "w") as f:
                json.dump(self.bench_history[-100:], f)
        except Exception:
            pass

    # ---- CPU 高压测试的独立历史 ----
    # ⚠️ 与性能测试历史**同一套存取路子**(同目录、同 JSON 套路), 只是另一个文件。
    #    分开存是因为量纲不同(峰值 vs 衰减), 挤一张表只会互相污染。
    def _hp_history_path(self):
        """CPU 高压测试历史 JSON 路径(与性能测试历史同目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_hp_history.json")

    def _load_hp_history(self):
        try:
            with open(self._hp_history_path(), "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.hp_history = data[-100:]
        except Exception:
            pass

    def _save_hp_history(self):
        try:
            with open(self._hp_history_path(), "w") as f:
                json.dump(self.hp_history[-100:], f)
        except Exception:
            pass
