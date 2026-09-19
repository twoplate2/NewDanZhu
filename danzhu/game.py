"""一局玩法状态机 + 经济系统(无头, 不依赖 Kivy / tkinter)。

老版这些逻辑长在 `RootWidget` 里(与 8300 行 UI 混在一起), 这里把它整块抽出来,
让「同一串输入 → 同一串可观测轨迹」可测。**行为逐位一致, 只是换了地方** ——
数值一律从 `android/main.py` 的赋值行读, 注释多处与代码不符(见 config.py 抬头)。

## 与 UI 的缝只有三条

1. **音效**: 本模块**不播**, 只按调用现场的顺序产出 `Ev(kind="sound")`, 带
   `(name, gain, throttle, t, frame)`。老版那次 `sfx.play(name, gain, throttle)` 的**决策**
   (播哪一声/多响/节流窗)全在这里 —— 因为轨迹台账要逐条比名字。
   音效层只负责五道闸(enabled / 已加载 / 增益量化 / 节流 / 语音互斥), 不再重复这套映射。
   ⚠️ 碰撞音的名字解析(老版 `Sfx.impact`)也搬到了 `impact_sound()`: 它是事件流的一部分。
   变体用 `audio.synth.pick_peg_variant`(与烘焙共用一条 `_ARNG`, 另起一条流会整体漂移)。
2. **装杯演出**: 注入 `fx`(老版 `GameArea.win_fx`)。协议 = `mode` / `busy()` /
   `play_win(m, bet, on_done, auto_close)` / `reveal_at()` / `expected_sec(m)`。
   不注入则用 `NullFX`(永不排上 ⇒ 揭晓立刻走兜底, 见 `play_win` 返回 False 那条路)。
   ⚠️ **`tick()` 不在这里调** —— 老版唯一的调用点是 `GameArea.tick_draw()` 里那句
      `self.win_fx.tick()`(无参)。本模块若也调一次, 演出每帧走两步 = **双倍速**;
      而且传 `now` 进去还会 TypeError(`tick(self)` 是无参的)。本模块只发 `ui_tick`。
3. **落盘 / 语音时长 / 弹窗 / 墙钟**: 构造参数就是**全部**注入点, 一个都不能漏 ——
   漏掉哪个, 那条路就偷偷接上真机环境(轨迹重放因此不可复现):

   | 注入点 | 老版对应 | 不注入会怎样 |
   |---|---|---|
   | `fx` | `GameArea.win_fx`(装杯演出) | 用 `NullFX` ⇒ 永不排上, 揭晓立刻走兜底 |
   | `config_path` | `RootWidget._config_path` | None = **不读不写**(等价读档恒失败 = 出厂设定, 也是 `tests/trace.py` 钉住的口径) |
   | `history_path` | `RootWidget._history_path` | 同上, 轮次历史不落盘 |
   | `on_easter_popup` | 彩蛋弹窗(模态) | None = 无 UI 可等, 直接放行(`easter_finish()`) |
   | `on_rtp_popup` | 隐藏档弹窗(模态) | None = 不占闸, 长按不会永久锁死 |
   | `voice_duration` | `Sfx.voice_duration` | 缺省走 `default_voice_duration()`(真 WAV 字节数算时长) — 见下 |
   | `wall_clock` | `time.time` | ⚠️ **必须注入**: 不注入则 `round_history` 的 `time` 是真墙钟, 同种子两遍录出来不一样 |

   `voice_duration` 的缺省**不再是常数 0.15**: 老版在 named 后端下按 WAV 文件字节数算真时长
   (`default_voice_duration`), 而轮次结束那条语音序列的**片段间隔全靠它** —— 落成常数的话
   真机上整段播报的节奏与老版不同(对账能过只是因为夹具双方都取缺省值)。
   彩蛋弹窗是模态, 有 UI 则等 UI 调 `easter_finish()`。

## 事件出口

所有公开方法把事件追加进内部缓冲, `step(dt)` 返回并**清空**它(`take_events()` 同)。
另有两条**环状**流水: `sounds`(全程音效) / `settle_log`(全程结算), 给轨迹台账直接用。
⚠️ 它们是 `deque(maxlen=LOG_CAP)` —— 老版这两个结构在**外部 tap** 里(于是一局结束就没了),
   这里为了让台账能读全, 只能自己留着; 而长局不封顶就是内存泄漏(一局 2.9 声/100 帧,
   挂机几小时能到十万条)。满了丢最老的。
`frame` 从 -1 起, `step()` 开头 +1(与 `tests/trace.py` 的 `frame_no` 同口径)。
"""

import json
import os
import random
import time
from collections import deque

from .audio.synth import SR, pick_peg_variant
# ⚠️ 中文数字朗读分词**只有一份**, 住在 voice.py —— 这里原来抄了一遍完全相同的实现。
#    两份纯逻辑各自演化, 表现是「同一个数字在两处读法不同」, 而对账夹具两边都取缺省值时看不出来。
from .audio.voice import _voice_files, number_voice_names
from .config import (
    ALIGN_VX_MAX, BALL_R, BENCH_SEED, CHARGE_RATE, DEFAULT_BET, EV_ARC, EV_CEIL,
    EV_DIV, EV_PEG, EV_WALL, FIELD_L, FIELD_R, FIXED_DT, FLOOR, G, LAND_BOUNCE_DECAY_JITTER,
    LAND_BOUNCE_JITTER, LAND_BOUNCE_MAX_VY, LAND_BOUNCE_MIN_VY, LAND_DAMP, LAND_E,
    LAND_HOLD, LAND_K, LANE_WALL_TOP, MAX_FALL_SEC, MISFIRE_MAX_FRAMES, MISFIRE_POWER,
    NUM_SLOTS, PEG_TOP, PLUNGER_X, PLUNGER_Y, PRESETS, RISER_Y, SLOT_TOP, SLOT_W,
    STALL_MAX_RETRY, STALL_RETRY_SEC, START_BEADS, _clamp_accum,
    SFX_MIN_SP, SFX_REF_SP,
)
from .config import COL_FIRE, COL_FIRE_HOT, COL_GREEN
from .geo import build_geo
from .physics import (
    Ball, advance_flight, advance_misfire, clamp, launch_ball, launch_misfire, power_u,
)
from .rules import roll_multipliers

# ---------------------------------------------------------------------------
# 音效增益常量(老版 main.py:2884-2912 的 Sfx 段)
# ---------------------------------------------------------------------------
# ⚠️ 这几个数原本住在 `Sfx` 里, 但**消费者只有 RootWidget 的调用点**(gain 是调用点决定的,
#    `Sfx.play` 只拿它乘 SFX_MASTER 再量化成 10 档)。音效层落地后它们可以直接搬去那边 ——
#    现在放这里是因为本模块是唯一的消费者, 而契约(config.py)不能改。
SFX_MASTER = 1.0             # 总音量(0~1), 手机喇叭需要满幅
SFX_LAUNCH_GAIN = 0.60       # 发射音基准
SFX_LAUNCH_GAIN_MAX = 0.80   # 满蓄力上限
SFX_MISFIRE_GAIN = 0.35      # 哑火发射音: 弱蓄力
SFX_MISFIRE_GAIN_MAX = 0.50  # 哑火发射音: 贴着阈值
CHARGE_HOLD_SEC = 0.60       # 满蓄力后"还顶着"提示音的重复间隔
CHARGE_HOLD_GAIN = 0.40      # 该提示音的音量
SFX_TOP_Y = 30.0             # 低于此高度的撞墙事件不发闷咚: 同帧的 top 音已代表这一下
SFX_APEX_Y_LO = 58.0         # 最弱有效蓄力的转向高度(球心 y), 实测
SFX_APEX_Y_HI = 22.0         # 满蓄力的转向高度, 实测
# ⚠️ `SFX_MIN_SP` / `SFX_REF_SP` 的**唯一真源在 `config.py`** —— 这里原来自建了一份,
#    而它漏了 `EV_ARC`(bus.py 那份是全的)。两份内容不同却都不报错。

# 老版 main.py:5683 / 1374 —— 与玩法无关, 但它们是 config 存档的字段, 读/写两侧都要同一份。
# ⚠️ `FPS_CAP_DEFAULT` 必须与 `config.py` 那份**同步** —— 两处都是存档字段的真源, 只改一处
#    就是"读写两侧不同一份"(本工程栽过的形状)。`max(...)` 而不是写死 185, 是为了让
#    `FPS_CAP_OPTIONS` 变动时两边自动跟。理由("默认 = 面板最高档")写在 `config.py` 那一段。
FPS_CAP_OPTIONS = (60, 90, 120, 144, 165, 185)
FPS_CAP_DEFAULT = max(FPS_CAP_OPTIONS)
POWER_GRAINS = ("每帧", "每5秒")
POWER_GRAIN_DEFAULT = "每帧"

CUP_TRIGGER_DELAY = 0.15     # 结算 -> "弹珠落容器"事件的延迟(账务/音效仍在 t=0)
STATS_REFRESH_DELAY = 0.20   # 统计写文字的延后(躲"落袋那一拍"的字形重排)
TOAST_RESET_DELAY = 0.05     # reset 的 toast 延后
ROUND_END_VOICE_GAP = 0.005  # 语音序列的片段间隔

# `sounds` / `settle_log` 的环状上限。老版没有这两个结构(台账是**外部 tap**, 一局结束就
# 丢), 这里为了让轨迹台账能读全只能自己留 —— 但长局不封顶就是内存泄漏。
# ⚠️⚠️ **2000 是实测定的, 别再往回调**(2026-09-19, 见 `temp/probe_mem.py`):
#    两条 deque 填满时常驻 **15.76 MB(@20000) → 1.57 MB(@2000)**, 而这里的读者只有
#    ① `tests/trace.py:754` 读 `settle_log[-1]` **一条** ② `tests/round_end.py:222` 读
#    `len(sounds)`。全库对账全程也只有 **251 声** ⇒ 2000 有 8 倍余量。
#    ⛔ "能读全"这个目的**本来就不靠它**: `tests/trace.py` 的 `sound_log` / `settle_log`
#       是**驱动器自己那份 list**(trace.py:263), 不读这里。
# ⇒ 任何需要**全量**台账的地方, 一律在外部 tap, **不要**靠把这个数调大。
LOG_CAP = 2000

# 定时器到期的浮点容差(秒)。见 `_pump_timers` 的说明 —— 只为对齐
# "排的时候相加" 与 "逐帧累加" 这两条求和路径的末位噪声。
_TIMER_EPS = 1e-9


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------
class Ev(object):
    """一条可观测事件。

    `kind`:
      · "sound"        —— name/gain/throttle, 要播的那一声(未过音效层的五道闸)
      · "state"        —— 状态机迁移, data={"from","to"}
      · "settle"       —— 一次结算, data= 老版 trace 的 settle_log 同形
      · "collision"    —— 逐物理帧汇总的碰撞事件位(给 UI 的特效, 音效已单独出)
      · 其余 UI 反馈   —— status/lamp/pulse_slot/big_result/toast/lamps_off/update_slots/
                         fire_color/power_label/stats/controls/vibrate/ui_tick/...
                         见各调用点; data 里有 UI 需要的全部参数
    """

    __slots__ = ("kind", "t", "frame", "name", "gain", "throttle", "data")

    def __init__(self, kind, t, frame, name=None, gain=0.0, throttle=0.0, data=None):
        self.kind = kind
        self.t = t
        self.frame = frame
        self.name = name
        self.gain = gain
        self.throttle = throttle
        self.data = data

    def __repr__(self):
        if self.kind == "sound":
            return "<Ev sound %s gain=%.3f thr=%.3f t=%.4f f=%d>" % (
                self.name, self.gain, self.throttle, self.t, self.frame)
        return "<Ev %s t=%.4f f=%d %r>" % (self.kind, self.t, self.frame, self.data)


def default_voice_duration(name):
    """语音片段时长(秒) —— 老版 `Sfx.voice_duration` 的命名为 named 后端那一路, 逐行端口。

    查 `voice/` 目录里那个 WAV 的**文件大小**(44 = WAV 头)除以 SR×2(16bit 单声道)。
    老版还有一条"bank 里有 PCM 就按字节数算"的分支 —— 那条只对**合成音效**生效, 而
    本函数的调用方(`_play_voice_sequence`)只拿语音名, 全在 voice 目录里, 走不到它。
    ⚠️ 不能写死 0.15: 轮次结束语音序列的片段间隔全靠它, 时序错了整段播报的节奏就与老版不同。
    """
    path = _voice_files().get(name)
    if path:
        try:
            return (os.path.getsize(path) - 44) / (SR * 2.0)
        except OSError:
            pass
    return 0.15  # 回落值(典型单字约 200ms)


def impact_sound(bit, sp, enabled=True):
    """碰撞音的名字/增益/节流 —— 老版 `Sfx.impact` 的决策部分, 逐行端口。

    ⚠️ **闸在抽变体之前**(与老版同形状: `if not self.enabled or sp < SFX_MIN_SP...: return`)。
       这不是为了少算一次 —— 撞钉变体抽的是 `audio.synth` 那条**与烘焙共用**的 `_ARNG`,
       静音/低于阈值时也抽一次, 会让运行期的变体序列整体漂移(而且对账看不见: bank 只比
       PCM 的 sha1)。所以静音时必须**一格不消耗**。
    ⚠️ 低于 `SFX_MIN_SP` 不发声: 返回 None(调用方不要凑一个默认值糊过去)。
    """
    if not enabled or sp < SFX_MIN_SP.get(bit, 0.0):
        return None
    t = clamp(sp / SFX_REF_SP.get(bit, 900.0), 0.0, 1.0)
    if bit == EV_PEG:
        # throttle 0.038→0.08: 机关枪连珠(间隔<0.08s)只响第一声, 与 PC plinko.py 一致
        return ("peg%d" % pick_peg_variant(t), 0.30 + 0.70 * t, 0.08)
    if bit == EV_CEIL:
        return ("rail", 0.45 + 0.55 * t, 0.22)
    if bit == EV_WALL:
        return ("wall%d" % (1 if t > 0.5 else 0), 0.35 + 0.65 * t, 0.055)
    if bit == EV_DIV:
        return ("div%d" % (1 if t > 0.5 else 0), 0.35 + 0.65 * t, 0.055)
    return None


def top_sound(y):
    """顶部碰撞音 —— 老版 `Sfx.top(y)`。不走 impact 是因为 apex 处法向速率≈0。"""
    t = clamp((SFX_APEX_Y_LO - y) / (SFX_APEX_Y_LO - SFX_APEX_Y_HI), 0.0, 1.0)
    return ("top%d" % (1 if t > 0.5 else 0), 0.62 + 0.38 * t, 0.0)


# ---------------------------------------------------------------------------
# 语音名拼装(老版模块级 number_voice_names / _read_4digits, 逐行端口)
# ---------------------------------------------------------------------------
# ⚠️ 它属于语音层, 但**唯一消费者是 `_play_round_end_voice`**(状态机的一部分: 决定
#    什么时候播哪一段)。语音层落地后可以整段搬走, 这里只保留引用。
# ---------------------------------------------------------------------------
# 装杯演出的空实现
# ---------------------------------------------------------------------------
class NullFX(object):
    """没有装杯演出时用的替身: 永不排上 ⇒ `play_win` 返回 False ⇒ 揭晓立刻走兜底。

    与老版"球堆排不上(build_pile 断言)"那条路的可观测行为一致: 演出没有, 但
    账务/揭晓/语音一个都不吞。
    """
    mode = "idle"

    def busy(self):
        return False

    def play_win(self, m, bet, on_done=None, auto_close=False):
        return False

    def reveal_at(self):
        return 0.0

    def expected_sec(self, m):
        return 0.0

    def tick(self, now):
        pass

    def request_close(self):
        return False


class _PopupOpen(object):
    """`_rtp_popup` 的占位(无 UI 句柄时也要能占住防重入闸)。"""
    __slots__ = ()


class Game(object):
    # ---- 档位表: 唯一真源, 别在任何地方再手写一份 ----
    RTP_TIERS = (("80%", 0.80), ("120%", 1.20), ("200%", 2.00), ("360%", 3.60))
    # 隐藏档(长按"期望返还比例"解锁, **不保存**)。三档全部"必中"(K_DIST={9:1.0})。
    RTP_HIDDEN = (("1000%", 10.0), ("2000%", 20.0), ("5000%", 50.0))
    RTP_UNLOCK_HOLD = 3.0        # 长按多久触发隐藏档弹窗
    SOUND_MODES = ("on", "off")

    def __init__(self, fx=None, config_path=None, history_path=None,
                 on_easter_popup=None, on_rtp_popup=None, voice_duration=None,
                 wall_clock=None, save_config=None, sound_gate=None):
        self._fx = fx if fx is not None else NullFX()
        self._config_path = config_path     # None = 不读不写(= 老版读档恒失败)
        self._history_path = history_path
        self._on_easter_popup = on_easter_popup
        self._on_rtp_popup = on_rtp_popup
        self._voice_duration = voice_duration or default_voice_duration
        self._wall = wall_clock or time.time
        # ⚠️ **落盘口必须可注入, 且出货路径走异步**: 老版 `_cfg_post` 把写盘 post 给
        #    工作线程, 主线程立刻返回(建不起线程才同步写兜底)。本模块原来写死了
        #    **同步原子写** —— 那在无头测试里语义等价, 但 `Game` 现在**就是出货逻辑**:
        #    每次切投注/切档/**结算那一帧**都要在**主线程**上等一次文件写。
        #    实测(桌面, 盘上 0.5KB 的配置): `build()` 里两次 `_save_config` 花 114ms,
        #    而老版同口径 8ms。**对账看不见这个 —— 它不量时间。**
        self._save_cfg = save_config
        # ⚠️⚠️ **撞钉音的第一道闸读的是音效层本体 `Sfx.enabled`, 不是 `sfx_enabled`**
        #    (老版 `Sfx.impact` 首句 `if not self.enabled ...: return False`, main.py:7044)。
        #    两者不等值: `Sfx.__init__` 在 `open_output()` 返回 None(三级后端全建不起来,
        #    **没有任何可用音频后端**的设备)时把 `enabled` 置 False, 而 `Game.sfx_enabled`
        #    仍是 True ⇒ 老版一次 `_ARNG` 都不消耗, 新版每次撞钉抽一个 ⇒ 变体流整体错位。
        #    所以这里**可注入**: 出货路径由 `root.py` 注入 `lambda: sfx.enabled`(真身)。
        self._sound_gate = sound_gate

        # ---- 外部时钟(轨迹录制的假钟从这里进来) ----
        self.now = 0.0               # 由 step(dt) 累加; 老版是 time.time()
        self.frame = -1

        # ---- 事件流水 ----
        self._ev = []
        self._timers = []
        self._timer_seq = 0
        self.sounds = deque(maxlen=LOG_CAP)      # 全程音效(环状, 给轨迹台账)
        self.settle_log = deque(maxlen=LOG_CAP)  # 全程结算(环状)

        # ---- 盘面 / 几何 ----
        self.geo = build_geo()
        self._base_deflectors = list(self.geo["deflectors"])
        self.multipliers = roll_multipliers()
        self._boards = {}
        self.rtp_target = 0.80

        # ---- 账务 ----
        self.balance = START_BEADS
        self.display_balance = float(START_BEADS)
        self._anim_target_balance = float(START_BEADS)
        self._anim_start_balance = float(START_BEADS)
        self._anim_start_time = 0.0
        self._anim_dur = 0.5
        self._coin_until = 0.0
        self._coin_start = 0.0       # 计分滚动音起播时刻(错峰: 晚于结算)
        self._land_hold = LAND_HOLD
        # ⚠️⚠️ **这一帧要不要跳过渲染尾巴**。老版蓄力超 3 秒的兜底自动发射那一帧是
        #    `self.launch(); return`(`main.py:21221-21222`) —— `return` 直接退出整个
        #    `_frame`, 把后面**整条渲染尾巴**(余额标签赋值 / HUD 压暗 / `_FRAME_END`)
        #    全跳过。状态机搬进本模块后, `PlayMixin._frame` 的尾巴变成**无条件**跑完 ⇒
        #    那一帧新版比老版多付一次 `_set_label_text(balance_lbl, ...)`, 而老版自己的
        #    注释算过: 那次赋值 = 重测字形 + 重光栅化 + 重建纹理, 还连锁触发 `_fit1`,
        #    是 `_frame` 里**最大的单块**(45 秒累计 1.06 秒)。幂等 ⇒ 画面不变、对账看不见。
        self._render_skip = False
        self._result_until = 0.0     # 结算结果窗口: 期内抑制 UI 语音, 让结果音优先
        self.bet = DEFAULT_BET
        self.plays = 0
        self.hits = 0
        self.max_plays = 50          # 每轮次数上限
        self.round_plays = 0         # 本轮已玩次数
        self.round_history = []      # 最近完成的轮次记录

        # ---- 状态机 ----
        self.state = "ready"         # ready | charging | flying | misfire | landing | landed
        self.power = 0.0
        self._last_charge_sound = 0.0
        self._charge_topped = False
        self._charge_start = 0.0
        self._charge_uid = None
        self._space_held = False
        self._release_power = None
        self._crossed = False
        self._risen = False
        self._topped = False         # 本次飞行是否已播顶部碰撞音
        self._misfire_frames = 0
        self._accumulator = 0.0      # 固定步长累加器(适配任意刷新率)
        self._landing_primed = False
        self._settle_slot = 0
        self._settled = False
        self._easter_hold = False
        self._easter_popup = None
        self._last_motion = 0.0      # flying 帧内刷新; 卡死兜底看"位置不动"而非发射时长
        self._last_ball_xy = (PLUNGER_X, PLUNGER_Y)
        self.landed_at = 0.0
        self.land_target_x = PLUNGER_X
        self.target_slot = 0
        self.target_x = PLUNGER_X
        self.ball = None
        self._controls_enabled = True

        # 揭晓合流(中奖): 大字/余额/语音 全部推迟到"最后一颗球落定"那一刻一起给。
        self._anim_pending = False   # 为真时余额冻结在扣注后的值, 一帧都不许追平
        self._reveal_done = True     # 本局是否已揭晓(幂等闸, 唯一真源在 settle 的 _on_settled)
        self._reveal_deadline = 0.0  # 兜底: 到点还没揭就自己揭(防 tick 停摆把数字吞了)
        self._pending_win = None     # (m, payout), 兜底揭晓时要用
        self._win_seq = 0            # 中奖轮次序号: 当幂等键用, 丢弃上一局的迟到回调
        self._settle_cb = None       # 本轮的揭晓闭包: 主路径与兜底路径**共用同一个**

        # ---- 声音 / 设置 ----
        self.sound_mode = "on"       # 不持久化, 每次启动都是 on
        # ⚠️ **与 `sound_mode` 是两件事**(老版 `sfx.enabled` vs `root.sound_mode`): 前者是音效层
        #    的开关, 后者是"播语音还是播琶音"的档。只有 `toggle_mute` 会一起翻; 探针/测试直接
        #    改 `sound_mode` 时它**不该**跟着动(老版也不动)。
        #    它单独存一份是为了 `impact_sound` 的第一道闸 —— 见那里的说明。
        self.sfx_enabled = True
        self.fps_cap_setting = FPS_CAP_DEFAULT
        self.power_grain = POWER_GRAIN_DEFAULT
        self._bench_running = False  # 跑分开关(普通玩法恒 False; 跑分链自己置位)
        self._bench_ball_i = 0
        self._bench_status_active = False
        self.power_label = ""        # 老版 power_lbl.text
        self._auto_reset_on_start = False
        self._round_end_shown = False
        self._round_end_ack = False
        self._rtp_popup = None       # 弹窗防重入闸(老版 _rtp_popup)
        self._rtp_hold_start = 0.0
        self._rtp_hold_fired = False
        self._bench_start = 0.0      # 长按标题的跑分入口(跑分链自己消费)
        self._bench_triggered = False
        # ⚠️ 常驻档的键**先注册、值留 `None`**, 句柄由 UI 的 `_add_rtp_button` 填 —— 所以
        #    读这份字典的人**必须容忍 `None`**(UiMixin 的三处遍历: `_restyle_buttons` /
        #    `_apply_sizes` / `_apply_row_budget`)。事件攒到下一帧才派发, 于是"键已加、
        #    句柄未填"的窗口真实存在, 光靠调用顺序守不住(2026-09-19 补的容忍)。
        self.rtp_btns = {}           # 有按钮的档位(键即真源; 值是 UI 的控件句柄, 可能还没填)
        for _lab, _v in self.RTP_TIERS:
            self.rtp_btns[_v] = None

        # ⚠️ 老版 `_easter_egg` **只在 launch() 里重置**, __init__ 不建它(settle 走 getattr
        #    兜底)。这里照抄, 免得"多出来的初值"掩盖某条没重置的路径。
        self._load_config()
        # 各档盘面一起生成, 切换不刷新。⚠️ 必须在 `_load_config` **之后** —— 读回来的
        # rtp_target 决定 `multipliers` 取哪一档; 但**抽取顺序**与 rtp_target 无关, 是
        # `_all_rtp()` 的固定顺序(7 次 roll_multipliers 都吃全局 random)。
        self._boards = {r: roll_multipliers(r) for r in self._all_rtp()}
        self.multipliers = self._boards[self.rtp_target]
        self._load_history()
        # 老版这里建 UI(_build_ui) —— 本模块没有 UI。`_auto_reset_on_start` 的补位对称保留。
        if self._auto_reset_on_start:
            self.reset_balance(notify=False)
        # ⚠️ 这两行是"恢复上次的状态", 玩家没碰任何东西 ⇒ 一声都不许出。
        self.set_bet(self.bet, silent=True, click=False)
        self.set_rtp(self.rtp_target, silent=True, click=False)
        self.park_ball(reroll=False, silent=True)

    # =====================================================================
    # 事件
    # =====================================================================
    def take_events(self):
        """取走并清空本帧事件缓冲。"""
        ev, self._ev = self._ev, []
        return ev

    def _emit(self, kind, data=None):
        e = Ev(kind, self.now, self.frame, data=data)
        self._ev.append(e)
        return e

    def _snd(self, name, gain=1.0, throttle=0.0):
        """记一声"要播的音"。老版此处就是 `self.sfx.play(name, gain, throttle)`。"""
        e = Ev("sound", self.now, self.frame, name=name, gain=float(gain),
               throttle=float(throttle))
        self._ev.append(e)
        self.sounds.append(e)
        return e

    def _status(self, text):
        """老版 `_set_game_status`(只写状态栏那一行)。"""
        self._emit("status", {"text": text})

    def _set_state(self, name):
        if name == self.state:
            return
        old, self.state = self.state, name
        self._emit("state", {"from": old, "to": name})

    def _controls(self, enabled):
        """老版 `_set_controls_enabled(enabled)` 的状态部分。

        ⚠️⚠️ **只发 `controls` 一条, 绝不在这里再发一条 `restyle_buttons`**(2026-09-19 修)。
           第三轮审计的 AST 逐函数比对抓到的: 新版原来两条都发, 而 `controls` 的处理函数
           `UiMixin._set_controls_enabled` **首句就是 `self._restyle_buttons()`**
           (`ui_base.py:721`, 与老版 `main.py:14126` 逐行相同) ⇒ 紧接着的 `restyle_buttons`
           分支(`play.py:425`)把它**又跑一遍** —— 老版每次翻转只跑 **1** 次
           (`_set_controls_enabled` 体里 `_restyle_buttons(` 恰好出现一次, AST 计数=1),
           新版跑 **2** 次。
           ⚠️ 今天它是幂等的(只写 `background_color`), 所以画面不变、`trace_parity` 逐位相同 ——
              但"按钮底色的唯一取值口"在同一帧被驱动两次, 且多出的那次落在**标签染色之后**;
              将来任何非幂等副作用都会立刻变成可见缺陷。而且这一拍正是工程反复标定的最贵帧
              ("回 ready 那一拍")。
           ⚠️ `set_bet` / `set_rtp` / `toggle_mute` 那三处**仍然单独发 `restyle_buttons`** ——
              它们不走 `_set_controls_enabled`, 底色的重刷只能靠那条事件。别顺手把它们删了。
        """
        self._controls_enabled = bool(enabled)
        self._emit("controls", {"enabled": bool(enabled)})

    # ---- 定时器(老版 Clock.schedule_once 的等价物) ----
    def _schedule(self, cb, delay):
        self._timer_seq += 1
        self._timers.append((self.now + delay, self._timer_seq, cb))

    def _pump_timers(self):
        """每步开头跑一次本帧到期的定时回调。

        ⚠️ **只在 step() 开头跑**: Kivy 的 `Clock.tick()` 也是这个位置(trace 驱动器
        `ft.t += dt` 之后、`_frame(dt)` 之前)。跑步中间新排的 delay=0 事件因此落到
        **下一帧**, 与老版同拍 —— 在帧内多泵一次会让语音序列整体早一帧。

        ⚠️⚠️ **必须带 `_TIMER_EPS` 容差**。到期判据两边算法相同
           (`排的时候的 now + delay` 对上 `累加出来的 now`), 但**浮点求和路径不同**:
           `40.11666666666567 + 0.15` 得 `40.266666666665670`, 而逐帧累加到第 9 帧是
           `40.266666666665664` —— **差 7.1e-15**, 于是 `<=` 判不中, 整条链晚一帧
           (实测: `_start_cup` 老版第 2415 帧起播、新版 2416; 之后 park_ball / settle /
           整条音效序列全部顺延, 对账在 frame 2415 首红)。
           Kivy 的 `Clock` 在这个边界上是**触发**的(实测), 所以老版没有这个偏差。
           1e-9 秒 = 1 纳秒, 比一帧(16.7ms)小七个数量级, 不可能改变任何真实时序决策 ——
           它只是把"同一次浮点求和的两条路径"对齐。
        """
        if not self._timers:
            return
        _lim = self.now + _TIMER_EPS
        due = [t for t in self._timers if t[0] <= _lim]
        if not due:
            return
        rest = [t for t in self._timers if t[0] > _lim]
        self._timers = rest
        due.sort(key=lambda t: (t[0], t[1]))
        for _due, _seq, cb in due:
            cb()

    # =====================================================================
    # 一帧
    # =====================================================================
    def step(self, dt):
        """推进一帧(内部走累加器 + physics_step)。返回本帧事件列表。"""
        self.frame += 1
        self.now += dt
        self._pump_timers()
        self._frame(dt)
        return self.take_events()

    # ---- 装杯演出 ----
    def set_fx(self, fx):
        """晚绑定装杯演出(`GameArea.win_fx`)。

        ⚠️ 为什么不是构造参数: `fx` 是 `GameArea` 建的, 而 `GameArea` 要拿 `RootWidget`
           当宿主 —— 也就是 UI 必须**先于** `fx` 存在, 但 `RootWidget` 建 UI 时又要读
           `game.max_plays` / `game.balance`。先有鸡还是先有蛋, 只能晚绑一次。
        ⚠️ 构造到 `set_fx` 之间 `_fx` 是 `NullFX`, 而这段窗口里 `__init__` 跑的那些路径
           (set_bet / set_rtp / park_ball) **一次都没碰过 `_fx`** —— 所以晚绑不改变任何行为。
           以后若往 `__init__` 里新加会碰 `_fx` 的调用, 这条就不成立了。
        """
        self._fx = fx

    def win_fx_busy(self):
        return self._fx.busy()

    def fx_mode(self):
        """演出当前模式(idle / pending / win / result)。轨迹台账的 `fx` 字段就是它。"""
        return self._fx.mode

    def click(self):
        """玩家点一下屏幕, 请求把装杯画面收走。返回 True = 这一下被消费掉了。

        老版 `_on_title_touch_down` 的第一块: 拿到 True 就该早退, 别再走"标题/返还比例"
        的长按计时 —— 玩家点的是"我看够了", 不是 HUD 上的长按热区。
        """
        try:
            return bool(self._fx.request_close())
        except Exception:
            return False

    def ui_quiet_until(self):
        """老版"结算结果窗口"的截止时刻(`_result_until`)。

        窗口内**故意**抑制 UI 语音(见 set_bet / set_rtp); 录制台切档前要等它过去,
        否则 `voice_bet_*` / `voice_rtp_*` 这一路永远录不到。
        """
        return float(self._result_until)

    def _frame(self, dt):
        self._render_skip = False          # 每帧先复位
        self._check_title_hold()
        if self.state == "charging":
            self.power = min(1.0, self.power + CHARGE_RATE * dt)  # 用真实 dt, 适配 30fps 设备
            if self.now - self._charge_start > 3.0:
                self.launch()          # 兜底: 蓄力超 3 秒自动发射(防 on_release 丢失卡死)
                self._render_skip = True   # 让 UI 也照老版跳过渲染尾巴(见 `_render_skip`)
                return
            self._play_charge_sound(self.power)
            weak = self.power < MISFIRE_POWER
            # UI: fire_btn 按力度变色; `_restyle_buttons` 在 charging 时跳开它
            self._emit("fire_color", {"weak": bool(weak),
                                      "hex": COL_FIRE if weak else COL_FIRE_HOT})
        elif self.state == "flying" and self.ball is not None:
            b = self.ball
            self._accumulator = _clamp_accum(self._accumulator + dt)
            landed = None
            tick_ev = 0
            tick_amp = {}
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                landed = advance_flight(b, self.geo)
                # 逐物理帧消费事件: 汇总给音效(残留位会让撞钉计数虚高, 实测 35% 偏差)
                if b.events:
                    tick_ev |= b.events
                    for bit, spd in (b.amp or {}).items():
                        if spd > tick_amp.get(bit, 0.0):
                            tick_amp[bit] = spd
                    b.events = 0
                    b.amp.clear()
                if landed is not None:
                    # 落袋即本例终态: 这一帧剩下的物理步不再推进这颗球。
                    # ⚠️ 与 PC 版 plinko.py 的 _frame 同因同解。第一道保险在 physics_step 的
                    # 落袋返回处(清 vx + 贴地), 已经让"结算槽==落格槽"成为结构性不变量; 这一道
                    # 管的是**别的事**: 少了它, 球落袋后还会被继续模拟(实测越槽局多飞
                    # 0.68~1.27s), 结算时刻/槽位白闪/装杯/揭晓全部随帧率漂。
                    break
            if not self._crossed and b.x < FIELD_R and b.y < LANE_WALL_TOP:
                self._crossed = True
            elif self._crossed and not self._risen and b.y > RISER_Y:
                self._risen = True
                self._snd("riser", 0.9)
            if self._crossed and not self._topped and b.vy >= 0.0:
                self._topped = True           # 冲到顶点转向(恒在 0.95~1.00s): 顶部碰撞声
                _n, _g, _thr = top_sound(b.y)
                self._snd(_n, _g, _thr)
            if b.y > SLOT_TOP - 40:
                self._status("即将入袋…")
            elif b.y > PEG_TOP:
                self._status("弹跳中…")
            else:
                self._status("入场中…")
            if landed is None:
                lx, ly = self._last_ball_xy
                if (b.x - lx) ** 2 + (b.y - ly) ** 2 > 1.0:
                    self._last_motion = self.now     # 位移>1px/帧: 还在动, 不是卡死
                elif (self.now - self._last_motion > STALL_RETRY_SEC
                      and getattr(b, "_stall_retry", 0) < STALL_MAX_RETRY):
                    # 踢球(不退回柱塞重发): 沿接触法线向下踢, 玩家看不到"发射失败重发"
                    nx, ny = getattr(b, "last_nx", 0.0), getattr(b, "last_ny", -1.0)
                    tx_, ty_ = -ny, nx
                    if ty_ > 0:
                        tx_, ty_ = -tx_, -ty_
                    b.vx += tx_ * 120.0
                    b.vy += ty_ * 120.0
                    b._stall_retry = getattr(b, "_stall_retry", 0) + 1
                    self._last_motion = self.now
                    self._last_ball_xy = (b.x, b.y)
                    self._crossed = self._risen = self._topped = False
                elif self.now - self._last_motion > MAX_FALL_SEC:
                    landed = max(0, min(NUM_SLOTS - 1,       # 真卡死兜底: 物理槽
                                        int((b.x - FIELD_L) / SLOT_W)))
                self._last_ball_xy = (b.x, b.y)
                # 判据必须用位移而非速度/碰撞事件: 卡死球的速度数值和微碰撞从未停过,
                # 但位置被碰撞钉死 —— 位置不说谎。
            if landed is not None:
                i = max(0, min(NUM_SLOTS - 1,               # 物理落格结算(球落到哪算哪)
                               int((b.x - FIELD_L) / SLOT_W)))
                if b.x > FIELD_R:                           # 球落回竖井(发射槽) — 罕见彩蛋
                    self._easter_egg = True
                    # ⚠️⚠️ 彩蛋那发的落定目标**不能**用槽中心: 竖井在场区**右边**、隔着一道墙,
                    #   而按 x 算出来的槽号其中心在墙的**左边** —— landing 循环里那句 LAND_K
                    #   弹簧会把球往左拽、拽穿隔墙(那个循环没有墙体碰撞), 球嵌在墙里定格。
                    #   ⇒ 彩蛋的目标是**发射槽本身**(与弹窗文案"已回到发射槽"一致)。
                    self.land_target_x = PLUNGER_X
                else:
                    self.land_target_x = FIELD_L + (i + 0.5) * SLOT_W
                self._settle_slot = i
                self.landed_at = self.now
                self._set_state("landing")
                self._accumulator = 0.0
                self._landing_primed = True   # 首帧补初速, 之后交给物理
                # 回弹改**纯竖直**(用户定稿): 第一次触地就把横向速度清零。
                # 病根: landing 循环里只有重力/横向弹簧/地板, **没有隔板碰撞**, 而球带着
                # 飞行末段的横速入槽 —— 回弹期它能横着滑过隔板停到隔壁槽里(结算槽仍是本槽,
                # 于是"钱算对了、球停错地方")。清零横速后球只在本槽原地上下弹。
                b.vx = 0.0
                # 结算提前到"第一次触地"这一刻: 以前要等回弹落定, 而实测 88.7% 的落定是走
                # 0.5s 超时兜底(平均比触地晚 0.3~0.5s)。回弹照播(landing 状态只是不再拦着
                # 结算), 但中奖演出/揭晓从触地就开始排。
                if not self._settled:
                    self._settled = True
                    self.settle(i)
            if tick_ev:
                self._emit("collision", {"ev": int(tick_ev), "amp": dict(tick_amp)})
                self._play_events(tick_ev, tick_amp, self.ball)
        elif self.state == "misfire":
            self._accumulator = _clamp_accum(self._accumulator + dt)
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                self._misfire_frames += 1     # 每物理步 +1(与飞行分支/selftest 同构, 防刷新率漂移)
                if advance_misfire(self.ball) or self._misfire_frames > MISFIRE_MAX_FRAMES:
                    self.ball.x = PLUNGER_X
                    self.ball.y = PLUNGER_Y
                    self.ball.vx = 0.0
                    self.ball.vy = 0.0
                    self._snd("bounce", 0.55)
                    self.park_ball(reroll=False)
                    break
        elif self.state == "landing":
            b = self.ball
            if self._landing_primed:
                self._landing_primed = False
                # 落地必须弹一下(用户定稿: "真跳和假跳都可以, 高度别太固定"):
                # 撞击速度 = max(球真实下落速度, 保底) × 随机系数。
                if b.vy < LAND_BOUNCE_MIN_VY:
                    b.vy = LAND_BOUNCE_MIN_VY
                b.vy *= random.uniform(*LAND_BOUNCE_JITTER)
            self._accumulator = _clamp_accum(self._accumulator + dt)
            floor_y = FLOOR - BALL_R
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                b.vx += (self.land_target_x - b.x) * LAND_K * FIXED_DT
                b.vx *= LAND_DAMP
                b.vx = clamp(b.vx, -ALIGN_VX_MAX, ALIGN_VX_MAX)
                b.vy += G * FIXED_DT
                b.x += b.vx * FIXED_DT
                b.y += b.vy * FIXED_DT
                if b.y >= floor_y:
                    b.y = floor_y
                    if b.vy > 0:
                        if b.vy > 60.0:
                            self._snd("bounce", clamp(b.vy / 500.0, 0.3, 1.0), 0.05)
                        b.vy = -b.vy * LAND_E * random.uniform(*LAND_BOUNCE_DECAY_JITTER)
                        if b.vy < -LAND_BOUNCE_MAX_VY:     # 回弹 vy 上限(删刹车后防弹越隔板)
                            # ⚠️ 顶到上限时**也要随机**(取 JITTER 的下半段): 否则所有"落得快"
                            #   的球都停在同一个 24.2px, 一眼看出是套路。
                            b.vy = -LAND_BOUNCE_MAX_VY * random.uniform(LAND_BOUNCE_JITTER[0], 1.0)
            if (abs(b.x - self.land_target_x) < SLOT_W * 0.45 and abs(b.vy) < 10.0
                    and b.y >= floor_y - 0.5):
                b.vx = 0.0
                b.vy = 0.0
                self._set_state("landed")
                self.landed_at = self.now
            elif self.now - self.landed_at >= 0.5:
                b.y = floor_y
                b.vx = 0.0
                b.vy = 0.0
                self._set_state("landed")
                self.landed_at = self.now
        elif self.state == "landed":
            # 中奖玻璃杯演出期间不放行: 否则 park_ball 会在杯子播到一半时重掷盘面、恢复按钮。
            # _easter_hold: 彩蛋的弹窗+装杯整段(弹窗是模态的, 玩家只有"确定"一条出口)。
            if (self.now - self.landed_at >= self._land_hold
                    and not self.win_fx_busy()
                    and not self._easter_hold):
                self.park_ball()

        # ⚠️ 老版这里读 `self.power_lbl.text` —— 而那个 Label 全工程**只有这一处写**,
        #    赋的还是空串 ⇒ 这个分支恒为假(死代码)。照抄形状, 不改判据。
        if self.state != "charging" and self.power <= 0.01 and self.power_label:
            self.power_label = ""
            self._emit("power_label", {"text": ""})

        now = self.now
        if self._coin_start <= now < self._coin_until:
            self._snd("coin", 0.8, 0.055)
        # ⚠️ 余额不再"滚"上去(玩家定案): 落杯子的动画已经承担了"钱到账"的演出。
        #    揭晓前仍然不许动(`_anim_pending`): 数字必须跟大字/语音在同一刻出来。
        if not self._anim_pending:
            self.display_balance = self.balance
        # 兜底揭晓: 正常路径下 FX 的 `_pump_reveal()` 会先放; 这条只在 tick 完全停摆
        # (切后台/卡死)时才用得上。**必须调同一个闭包** —— 共用 `_reveal_done`/seq 闸 ⇒
        # 主路径和兜底可以安全赛跑, 不会双响。
        if (not self._reveal_done and self._pending_win and self._settle_cb
                and self._reveal_deadline and now >= self._reveal_deadline):
            self._emit("reveal_fallback", {"win": self._pending_win})
            self._settle_cb()
        # UI: 余额标签 / 板面 tick_draw / HUD 压暗 / 帧尾计时。
        # ⚠️⚠️ **装杯演出的推进不在这里** —— 它由 `GameArea.tick_draw()` 里那句
        #    `self.win_fx.tick()` 驱动(老版唯一的调用点)。这里若再调一次, 演出每帧走两步
        #    = **双倍速**, 而且 `WinPileFX.tick(self)` 是无参的, 传 now 还会 TypeError。
        #    本模块只发 `ui_tick`, 由 UI 去调 tick_draw。
        # ⚠️ 位置必须在 `reveal_fallback` **之后**(老版 tick_draw 在这里, 且它后面不许再早退)。
        self._emit("ui_tick")

    # =====================================================================
    # 蓄力 / 发射
    # =====================================================================
    def start_charge(self):
        if self.state != "ready":
            return
        if self.balance < self.bet:
            if self.sound_mode == "on":
                # 语音档: 播报替换 error 嗡声; 语音全长 2.9s, 节流到播完才许重播
                self._snd("voice_nomoney", 1.0, 3.0)
            else:
                self._snd("error", 1.0, 0.4)
            self._emit("toast", {"text": "弹珠数量不足\n请重置或降低投入",
                                 "hex": COL_FIRE, "size": 26, "life": 2.0})
            return
        if self.round_plays >= self.max_plays:
            self.show_round_end()
            return
        self._set_state("charging")
        self.power = 0.0
        # ⚠️ **蓄力期立刻上输入锁**: 不上锁的话这一窗口里档位/投注按钮都能点 ——
        #    点档位 = "切一半"(高亮走了、盘面没走, 见 set_rtp 的说明),
        #    点投注 = 余额可能被扣成负数。上锁**不影响发射**(发射走 on_touch_up)。
        self._controls(False)
        self._charge_start = self.now     # 蓄力起始时刻(3 秒兜底自动发射)
        self._last_charge_sound = 0.0     # 立刻响第一声棘轮
        self._charge_topped = False
        self._status("蓄力中")

    def launch(self):
        if self.state != "charging":
            return
        # ⚠️ 老版在这里有 `_SINCE_LAUNCH[0] = 0`(帧探针账本, 判"最差帧是不是紧跟在发射后")。
        #    那是 UI 侧的账本, 本模块不该 import 它 —— 所以**只发事件**, 由 UI 的派发器清零。
        #    位置等价: 老版那句在状态守卫之后、哑火分支**之前**(哑火也清), 所以事件也发在这里。
        self._emit("launch")
        if self.power < MISFIRE_POWER:
            # 哑火: 球照样弹出去, 只是升不过隔墙顶 -> 掉回柱塞。不扣弹珠、不计一局、不换盘面
            frozen_power = self.power  # 在清零前保存, 用于音量/震动分级
            self.ball = launch_misfire(self.power)
            self._set_state("misfire")
            self._accumulator = 0.0
            self.power = 0.0                  # 哑火后清除蓄力显示
            self._misfire_frames = 0
            self._snd("launch", SFX_MISFIRE_GAIN + (SFX_MISFIRE_GAIN_MAX - SFX_MISFIRE_GAIN)
                      * clamp(frozen_power / MISFIRE_POWER, 0.0, 1.0))
            self._emit("vibrate", {"ms": 8, "double": False})
            self._controls(False)
            self._status("力度不足,未扣弹珠")
            return
        frozen_power = self.power  # 在清零前保存, 用于音量/震动分级
        self.balance -= self.bet
        # 发射: 弧面垂直抖动 ±6px(每发随机), 纯物理飞行(无预演/无渲染修正)。
        # ⚠️ 跑分: 连碰撞随机流也换成按球号派生的确定流。正常发射 `_rng=None` ⇒ 走全局
        #    `random`(每球天然不同)。种子按**球号**派生, 不是每发同一个 —— 否则 5 发会走出
        #    一模一样的轨迹、落在同一个格子里。
        _brng = None
        if self._bench_running:
            _brng = random.Random(BENCH_SEED + 1000 + int(self._bench_ball_i))
            arc_dy = _brng.uniform(-6.0, 6.0)
        else:
            arc_dy = random.uniform(-6.0, 6.0)
        self.geo["deflectors"] = [(x1, y1 + arc_dy, x2, y2 + arc_dy)
                                  for (x1, y1, x2, y2) in self._base_deflectors]
        self.ball = launch_ball(frozen_power, rng=_brng)
        self._settled = False                 # 新发射重置结算标记(结算延迟到回弹后)
        self._easter_egg = False              # 新发射重置彩蛋标记(球落回竖井才置 True)
        # 防御性重置: 正常路径下 launch() 只在 state=ready 时可达, 而 _easter_hold 期间
        # 状态是 landed, 照理进不来。但 reset_balance() 能强制中断回 ready —— 那之后
        # 这一发必须能把上一次的彩蛋锁清掉, 否则玩家永远发不出去。留作逃生口。
        self._easter_hold = False
        self._set_state("flying")
        self._accumulator = 0.0
        self.power = 0.0                      # 发射后清除蓄力显示
        self.plays += 1
        self.round_plays += 1
        self._crossed = False
        self._risen = False
        self._topped = False
        self._last_motion = self.now          # 卡死兜底的运动锚点
        self._last_ball_xy = (self.ball["x"], self.ball["y"])
        self._snd("launch", SFX_LAUNCH_GAIN + (SFX_LAUNCH_GAIN_MAX - SFX_LAUNCH_GAIN)
                  * power_u(frozen_power))
        self._emit("vibrate", {"ms": 14, "double": False})
        self._controls(False)
        self._status("发射!")

    def _play_charge_sound(self, power):
        if power >= 1.0:
            now = self.now
            if not self._charge_topped:
                self._charge_topped = True
                self._last_charge_sound = now
                self._snd("charge_full")
            elif now - self._last_charge_sound >= CHARGE_HOLD_SEC:
                self._last_charge_sound = now
                self._snd("charge_full", CHARGE_HOLD_GAIN)
            return
        now = self.now
        if now - self._last_charge_sound < 0.25 - 0.18 * clamp(power, 0.0, 1.0):
            return
        self._last_charge_sound = now
        self._snd("ratchet%d" % int(clamp(power, 0.0, 1.0) * 5.99))

    # ---- 键盘 / 触摸(输入口) ----
    def space_down(self):
        """空格按住蓄力(老版 `_on_key_down` 的 key==32 支)。"""
        if not self._space_held:
            self._space_held = True
            self.start_charge()

    def space_up(self):
        """空格松手: 冻结力度, 50ms 去抖后发射(与 tkinter 版一致)。"""
        self._space_held = False
        if self.state == "charging":
            self._release_power = self.power
            self._schedule(self.space_fire, 0.05)

    def space_fire(self):
        """老版 `_space_fire(dt)`: 用冻结的力度发射。"""
        if self._release_power is not None:
            self.power = self._release_power
            self._release_power = None
        self.launch()

    def mark_charge_uid(self, uid):
        """记下"按下发射键的那根手指"(老版 `on_touch_down` 的 grab 那一支)。

        ⚠️ 命中判定(collide_point)是 UI 的事, 这里只收结果 —— 判据是 UI 几何。
        """
        self._charge_uid = uid

    def touch_up(self, uid):
        """老版 `_on_title_touch_up`: 取消长按计时 + 发射保底。

        ⚠️⚠️ **必须限定是"按下发射键的那根手指"**: 这里是 Window 级观察者, 屏幕上
        **每一次**抬手都会进来 —— 裸判 `state == "charging"` 的话, 蓄力期间另一根手指
        蹭一下再抬起, 球就当场飞出去了(力度作废; 若还不到 MISFIRE_POWER 更是直接哑火)。
        """
        self._bench_start = 0.0
        self._rtp_hold_start = 0.0
        if self.state == "charging" and uid == self._charge_uid:
            self._charge_uid = None
            self.launch()

    def on_touch_down_allowed(self):
        """输入锁是否放行 HUD 触摸(老版 `RootWidget.on_touch_down` 的第一句)。

        ⚠️ **只挡 on_touch_down, 不挡 on_touch_up** —— 松手那条路走 Kivy 的 touch grab,
        `fire_btn.on_release -> launch()` 必须照常送达。
        """
        return bool(self._controls_enabled)

    # =====================================================================
    # 结算
    # =====================================================================
    def settle(self, i):
        """落袋结算。`_frame` 在**第一次触地**那一刻调, `_settled` 守重复。"""
        before = self.balance
        easter = bool(getattr(self, "_easter_egg", False))
        if easter:
            self._easter_egg = False
            self.balance += 2 * self.bet          # 彩蛋: 球跳回发射槽, 按 ×2 结算
            # 不计一局(与哑火一致): 总投/每轮投都退回
            self.plays -= 1
            self.round_plays -= 1
            self._refresh_stats()
            self._emit("vibrate", {"ms": 35, "double": True})   # 短促双震=惊喜
            self._result_until = self.now + 2.5
            self._anim_start_balance = self.display_balance
            self._anim_target_balance = float(self.balance)
            self._anim_start_time = self.now
            self._save_config()
            self._log_settle(i, before, easter)
            # ⚠️ 跑分期间不表演: 不播装杯、不弹彩蛋窗。账务照走, 单局照常结束。
            if self._bench_running:
                self._land_hold = max(0.3, LAND_HOLD - 0.5)
                return
            # 一路锁到"装杯播完 + 弹窗关掉": 彩蛋分支不设 _land_hold, 不锁的话 park_ball
            # 会在 landed_at+0.6s 就跑掉 —— 重掷盘面、state 回 ready、按钮恢复。
            self._easter_hold = True
            if not self._fx.play_win(2, self.bet, on_done=self.on_easter_settled):
                self.on_easter_settled()
            return
        m = self.multipliers[i]
        payout = self.bet * m
        self.balance += payout
        if m > 0:
            self.hits += 1
        # ⚠️ **统计不在这一帧写**: 这一帧是"落袋那一拍" —— 灯/槽闪/音效/余额/揭晓全挤在
        #    这里, 而写文字 = 重排一次字形纹理(真机 4~7 毫秒)。挪后 0.20 秒。
        self._schedule(self._refresh_stats, STATS_REFRESH_DELAY)
        # 指示灯回答的是"哪一格中了", 所以固定 绿=中 / 红=未中(与 PC 版同一套设计)
        self._emit("lamp", {"slot": int(i), "hex": COL_FIRE if m <= 0 else COL_GREEN})
        self._emit("pulse_slot", {"slot": int(i), "life": 0.30})
        self._play_pocket_sound(m)
        # 数字滚动节奏 + 大奖分档(x10 以上滚更久, 看得清中大奖); 实际起滚在 _reveal_win
        big = m >= 10
        self._land_hold = 0.7 if big else max(0.3, LAND_HOLD - 0.5)  # 提前 0.5s 可发射
        self._anim_dur = 1.2 if big else 0.5
        if m > 0:
            # 揭晓合流: 大字/余额/结果语音 全部等到"最后一颗球落定"那一刻。
            # 账务(上面的 balance/hits/_save_config)一秒都不挪: 中途被杀不能吞奖励。
            self._status("命中 x%d · 结算中" % m)   # 中间态: 第一秒不发空, 余额"冻结"不像 bug
            self._anim_pending = True
            # ---- 揭晓: 一次性事件, 用"本轮序号"当幂等键 ----
            # 闸收到唯一一处、同时盖住两个副作用, 并用序号丢弃迟到的旧回调。
            self._win_seq += 1
            seq = self._win_seq
            self._reveal_done = False
            self._pending_win = (m, payout)

            def _on_settled():
                # 揭晓 + 语音同拍: 数字和声音一起给。顺序不能反 —— 先立大字再响。
                # `seq != self._win_seq` 说明这是**上一局**的迟到回调, 直接丢弃。
                if self._reveal_done or seq != self._win_seq:
                    return
                self._reveal_done = True
                self._reveal_win(m, payout)      # 内部自带 try/except
                self._play_win_voice(m, payout)

            self._settle_cb = _on_settled
            # ⚠️⚠️ `_reveal_deadline` **必须先归零再调度**。兜底判据是
            #    `... and self._reveal_deadline and now >= self._reveal_deadline` —— 不归零的话,
            #    **上一轮残留的非零 deadline** 会在这 0.15s 窗口里让兜底提前开火(= 提前剧透)。
            self._reveal_deadline = 0.0

            def _start_cup():
                # auto_close: **跑分期间**不等玩家点击(跑分是自动连续发射的, 等人点击会把
                # 整轮跑分卡死); 正常玩则停在装满状态等点击。
                if not self._fx.play_win(m, self.bet, on_done=_on_settled,
                                         auto_close=self._bench_running):
                    # 排不上(球堆异常)也绝不能把数字和声音吞了
                    _on_settled()
                # 兜底: 到点还没揭就自己揭(防 tick 停摆)。基准必须和 FX 判揭晓用**同一个
                # 真源**(`reveal_at()`), 不能自己按 expected_sec 反推 —— 那会比真事件早。
                # ⚠️⚠️ **两个时基必须换算, 不能直接比**(2026-09-19 修)。`reveal_at()` 返回的是
                #    **FX 自己时基上的绝对时刻**(`winfx.py:774-775` 用 `time.time()` 盖章), 而
                #    下面 `_frame` 里比的是 `self.now` —— 那是 `step(dt)` 从 0 累加的**模拟钟**。
                #    直接比 = 「~20 秒 vs ~1.7e9 秒」⇒ `now >= _reveal_deadline` **恒假**,
                #    这条兜底成了**死代码**(老版两侧都是 `time.time()`, 是通的)。
                #    ⚠️ 轨迹台**看不见这个错**: 假钟让两边都从 0 起、加同一串 dt ⇒ 逐位相等,
                #       对账全绿而真机上兜底永不触发(典型的"夹具自己制造绿")。
                #    ⚠️ 只能取**差值**: 两个钟都按真实秒推进, 所以"距现在还有多久"是同一个数。
                #       换算完在假钟下与老版**逐位相同**, 在真机上才真的可比。
                _ra = self._fx.reveal_at()
                self._reveal_deadline = (
                    _ra + 0.05 if not _ra                      # 无球(老版同款: 0.05 立刻到期)
                    else self.now + (_ra - time.time()) + 0.05)

            self._schedule(_start_cup, CUP_TRIGGER_DELAY)
            # 只要中奖就震, 按倍率分档(x2/x3 轻点一下)
            self._emit("vibrate", {"ms": 300 if m >= 100 else (
                220 if m >= 50 else (150 if m >= 20 else (110 if m >= 10 else (
                    75 if m >= 5 else 45)))), "double": False})
        else:
            # 未中不播装杯, 保持原来的即时反馈(大字 + 余额滚动照旧)
            self._status("未中")
            self._emit("big_result", {"m": m, "payout": payout})
            self._anim_pending = False
            self._reveal_done = True
            self._pending_win = None
            self._anim_start_balance = self.display_balance
            self._anim_target_balance = float(self.balance)
            self._anim_start_time = self.now
            self._coin_until = 0.0
            self._coin_start = 0.0
        self._result_until = self.now + 2.5 + (
            self._fx.expected_sec(m) if m > 0 else 0.0)
        self._save_config()
        self._log_settle(i, before, easter)

    def _log_settle(self, i, before, easter):
        """轨迹台账用的结算流水(与老版 trace 的 settle_log 逐字段同形)。"""
        self.settle_log.append({
            "frame": self.frame, "t": round(self.now, 9), "slot": int(i),
            "bet": self.bet, "mult": self.multipliers[i],
            # ⚠️ 必须 `float(...)` 再 round: `balance` 是**整数**, `round(int, 9)` 还是 int,
            #    JSON 里落成 `1050` 而老版是 `1050.0` —— 对账台逐字节比, 这一处会红。
            "balance_before": round(float(before), 9),
            "balance_after": round(float(self.balance), 9),
            "easter": bool(easter),
        })
        self._emit("settle", {"slot": int(i), "bet": self.bet,
                              "mult": self.multipliers[i],
                              "balance_before": before, "balance_after": self.balance,
                              "easter": bool(easter)})

    def _reveal_win(self, m, payout):
        """揭晓: 装杯最后一颗球落定的那一刻, 大字 + 余额 + coin 一起给。

        语音的挂点不在这里 —— 它挂在 settle 的 `_on_settled` 里(和这里同拍)。
        ⚠️ **幂等闸不在这里**: 唯一真源是 settle() 的 `_on_settled`(它同时挡住语音)。
        """
        try:
            self._anim_pending = False
            self._status(("中奖! +%d (x%d)" % (payout, m)) if payout > 0 else "未中")
            self._emit("big_result", {"m": m, "payout": payout})
            now = self.now
            self._anim_start_balance = self.display_balance
            self._anim_target_balance = float(self.balance)
            self._anim_start_time = now
            # coin 是"计分滚动"的伴音, 必须跟着余额滚。起点让开语音起句。
            self._coin_start = now + 0.35
            self._coin_until = now + (1.2 if m >= 10 else 0.5)
        except Exception as exc:
            self._emit("reveal_fail", {"err": "%s: %s" % (type(exc).__name__, exc)})

    def park_ball(self, reroll=True, silent=False):
        """重掷盘面(reroll=True), 新球停到柱塞, 回 ready。哑火 reroll=False 防免费刷盘。"""
        # ⚠️ 跑分期间**不重掷**: 盘面由跑分链按 BENCH_BOARD 钉死, 这里再掷一次会覆盖掉。
        if reroll and not self._bench_running:
            self._boards = {r: roll_multipliers(r) for r in self._all_rtp()}  # 各档盘面一起刷新
            self.multipliers = self._boards[self.rtp_target]
            # ⚠️ UI 只更新倍率槽, 不整块重画(重掷时几何一点没变, 变的只有 9 个槽)。
            self._emit("update_slots")
        else:
            self._emit("lamps_off")
        self.ball = Ball(x=PLUNGER_X, y=PLUNGER_Y, vx=0.0, vy=0.0,
                         item=None, born=self.now, events=0, amp={},
                         misfire=False)
        self._set_state("ready")
        self.power = 0.0
        self._controls(True)
        if self.round_plays >= self.max_plays and not self._round_end_shown:
            self.show_round_end()
        if not silent:
            self._snd("ready", 0.8)

    # ---- 彩蛋 ----
    def on_easter_settled(self, *_a):
        """装杯播完 -> 弹对话框(用户定稿: 先看弹珠落进杯子, 再看说明)。

        守卫 `_easter_hold`: 玩家若在装杯期间按了重置并又发了一发(launch 会清这个锁),
        这时再弹窗就会盖在新一局上 —— 那就不弹了。
        """
        if not self._easter_hold:
            return
        # ⚠️ 跑分期间**不弹窗**。这里**不能只 return** —— 那样 _easter_hold 永远不放,
        #    玩家会被软锁死; 必须走 easter_finish 解锁。
        if self._bench_running:
            self.easter_finish()
            return
        # ⚠️ 出口是**玩家点击**(FX 的手动收尾)。轮询在 FX 未 idle 期间持续, 没有算术上界。
        # ⚠️ 别改成"到点自己弹": 那会让弹窗盖在**还立着的杯子**上。
        if self._fx.mode != "idle":
            self._schedule(self.on_easter_settled, 0.2)
            return
        self.show_easter_popup()

    def show_easter_popup(self):
        """彩蛋弹窗的**状态部分**: 播报 + 占住 `_easter_hold`。UI 画弹窗、点确定后回调。

        ⚠️ 直接 `_snd` —— `set_bet`/`set_rtp` 那类辅助会被 `_result_until` 挡掉
        (本分支刚把它设成 now+2.5), 走辅助函数会自己把自己抑制掉。
        """
        if self.sound_mode == "on":
            self._snd("voice_easter_%d" % self.bet, 1.0, 0.5)
        else:
            self._snd("win1", 0.6)
        self._easter_popup = _PopupOpen()
        self._emit("easter_popup", {"bet": self.bet, "payout": 2 * self.bet})
        if self._on_easter_popup is not None:
            self._on_easter_popup(self.bet)      # UI 画弹窗; 关掉后必须调 easter_finish()
        else:
            self.easter_finish()                 # 无头: 没有模态可等, 直接放行

    def easter_finish(self):
        """彩蛋流程收尾: 解锁。装杯已经把 busy() 走完了, 这里只松 `_easter_hold`。

        ⚠️ **不要顺手清 `_easter_popup`**(老版 `_easter_finish` 只清 hold, main.py:20462-20464)。
           那个引用是**活的查询口**: 冒烟夹具靠它判"跑分期间有没有弹窗"
           (main.py:22236 先手工置 None, 跑完再看 `_easter_popup is not None`) ——
           在这里清掉, 那个探针读到的就恒是"没弹过"(弹窗真弹了也看不出来)。
           顺带: 关弹窗走 `on_dismiss`, 而 `_easter_popup` 记的是"最后一次开过哪个"。
        """
        self._easter_hold = False

    # =====================================================================
    # 音效出口
    # =====================================================================
    def _play_events(self, ev, amp, b):
        """播放本渲染帧收集到的碰撞事件(ev/amp 由累加器循环内逐物理帧消费汇总)。

        ⚠️ 逐物理帧消费(读后清零), 不能攒到渲染帧 —— 残留位让撞钉计数虚高(实测 35% 偏差)。
        """
        if not ev:
            return
        amp = amp or {}
        for bit in (EV_PEG, EV_CEIL, EV_WALL, EV_DIV):
            if ev & bit:
                if bit in (EV_WALL, EV_CEIL) and b.y < SFX_TOP_Y:
                    continue          # 顶墙/天花板撞击与 apex 转向同帧, 交给 top 音, 不叠
                _on = (self._sound_gate() if self._sound_gate is not None
                       else self.sfx_enabled)
                s = impact_sound(bit, amp.get(bit, 0.0), _on)
                if s is not None:
                    self._snd(s[0], s[1], s[2])
        if ev & EV_ARC:
            self._snd("rail", 0.18, 0.05)   # 弧面接触: 轻金属"擦"声, 转向瞬间的听觉反馈

    def _play_pocket_sound(self, m):
        """入袋音 —— 结算瞬间就播(杯子还没出来), 所以和赢音拆开。"""
        self._snd("pocket")
        if m <= 0:
            self._snd("lose", 0.9)    # "好遗憾"语音已制作(voice_lose), 暂不接入

    def _play_win_voice(self, m, payout):
        """赢音/中奖语音 —— 由中奖玻璃杯**全部落定那一刻**回调触发。"""
        if m <= 0:
            return
        if self.sound_mode == "on":
            # 语音档: "弹珠加xx"替换 win 琶音(语音与琶音同播会互相盖)
            self._snd("voice_win%d" % payout)
            return
        tier = 0 if m <= 2 else (1 if m <= 3 else (2 if m <= 5 else (
            3 if m < 20 else (4 if m < 50 else (5 if m < 100 else 6)))))
        self._snd("win%d" % tier)

    # =====================================================================
    # 经济 / 配置
    # =====================================================================
    def _refresh_stats(self):
        if self._bench_status_active:
            return
        rate = 100.0 * self.hits / self.plays if self.plays > 0 else 0
        self._emit("stats", {"text": "累计%d投%d中(%.0f%%)" % (self.plays, self.hits, rate)})

    def set_bet(self, v, silent=False, click=True):
        """切投注档。`click` 与 `silent` 是**两件事**: 前者管按钮反馈那一声, 后者管语音。"""
        self.bet = v
        self._emit("restyle_buttons")
        self._refresh_stats()
        if click:
            self._snd("click", 1.0, 0.08)
        if not silent and self.sound_mode == "on" and self.now >= self._result_until:
            self._snd("voice_bet_%d" % v, 1.0, 0.6)
        self._save_config()

    def set_rtp(self, t, silent=False, click=True):
        """切档。**只有 `state == "ready"` 才真的切**(玩家 2026-09-18 修)。

        ⚠️⚠️ 非 ready 时**直接不动** —— 盘面(`self.multipliers`)只有在 ready 才换得动,
           而"换一半"是最坏的结果: 档位高亮跳到新档、9 个倍率槽还是旧档的, 这一发按
           **旧档**赔付, 要等下一发 park_ball 重掷才同步。
        ⚠️ 守卫必须放在**最前面**(连 `rtp_target` 都不能先写)。
        """
        if self.state != "ready":
            return
        self.rtp_target = t
        self._emit("restyle_buttons")
        if click:
            self._snd("click", 1.0, 0.08)
        if not silent and self.sound_mode == "on" and self.now >= self._result_until:
            self._snd("voice_rtp_%d" % int(t * 100), 1.0, 0.6)
        self.multipliers = self._boards[t]   # 切换: 直接取该档盘面, 不刷新(只有发射才刷新)
        # ⚠️ **只换 9 个倍率槽, 不整块重画**(2026-09-19)。切档时**几何一点没变** —— 变的只有
        #    `multipliers` 那 9 个值, 与 `park_ball` 重掷是**同一种改动**, 而那边早就走
        #    `update_slots` 了(见 `game.py::park_ball`)。
        #    这是老版交接文档 `android/jiaojie.md` 方案四明写的下一步:
        #    「现有代码已将"仅倍率槽变化"改为 `_update_slots()`。下一步只检查仍然触发整块
        #      `GameArea._redraw()` 的真实场景: 尺寸变化、首帧、重置、**切换档位**、异常兜底。」
        #    代价: `_redraw` 要 `canvas.clear()` + 重建全部指令(桌面实测 0.529ms, 真机约 9ms),
        #    而 165Hz 的帧预算只有 6.06ms ⇒ 那 9ms 正好是一记肉眼可见的卡顿。
        # ⚠️ `_update_slots` **自带结构守卫**(9 个槽的列表长度对不上就自己退回 `_redraw`),
        #    所以"尺寸刚变过 / 首帧"这些情形不会被它半更新。
        # ⚠️ 它顺手做的事与 `_redraw` **等价**: 作废槽位白闪(`_pulse = None`)+ 熄灭全部投中
        #    指示灯(`lamps_off()`) —— 后者本来就是 `_redraw` 重建 `_lamp_cols` 时"顺手"做的。
        self._emit("update_slots")
        self._save_config()

    def _regular_rtp(self):
        """常驻档(可持久化的那批) —— 从 RTP_TIERS 派生, **不手抄**。

        用途只有一个: `_load_config` 的读档白名单。派生而非手写 ⇒ 隐藏档**结构上**
        不可能被读回来, 这就是"隐藏档不保存"的实现点(保存侧照常写盘, 读侧丢弃)。
        """
        return tuple(v for _lab, v in self.RTP_TIERS)

    def _all_rtp(self):
        """所有档位(常驻 + 彩蛋) —— **唯一真源**, 别在各处再手写一份元组。

        ⚠️ 这里真的踩过坑(线上闪退): `_boards` 的初始化 和 `park_ball` 的重刷
        **各写了一份手抄档位列表**, 加彩蛋档时只改到其中一处 —— 另一处漏掉之后,
        切到 2000%/5000% 再发射一次, `self._boards[self.rtp_target]` 就抛 KeyError。
        """
        return (tuple(v for _l, v in self.RTP_TIERS)
                + tuple(v for _l, v in self.RTP_HIDDEN))

    # ---- 隐藏返还率(长按解锁那条链) ----
    def rtp_is_hidden(self, t):
        """t 是不是隐藏档 —— 判据走 RTP_HIDDEN, 不手抄数字。"""
        return any(abs(t - v) < 1e-6 for _lab, v in self.RTP_HIDDEN)

    def begin_rtp_hold(self):
        """按下"期望返还比例"标签(老版 `_on_title_touch_down` 的最后一段)。"""
        self._rtp_hold_start = self.now
        self._rtp_hold_fired = False

    def begin_bench_hold(self):
        """按下标题(老版 `_on_title_touch_down` 的跑分支)。跑分链自己消费这个事件。"""
        self._bench_start = self.now
        self._bench_triggered = False

    def check_title_hold(self):
        """老版 `_check_title_hold`: 长按到点就开弹窗。"""
        self._check_title_hold()

    def _check_title_hold(self):
        t = self._bench_start
        if t > 0 and not self._bench_triggered and self.now - t >= 3.0:
            self._bench_triggered = True
            self._emit("bench_menu")          # 长按 3 秒: 弹性能测试菜单
        t2 = self._rtp_hold_start
        if t2 > 0 and not self._rtp_hold_fired and self.now - t2 >= self.RTP_UNLOCK_HOLD:
            self._rtp_hold_fired = True
            self.ask_unlock_rtp()

    def rtp_unlock_options(self):
        """弹窗的选项与默认选中 —— 文字**从 RTP_HIDDEN 取**, 不写死。

        返回 `(opts, default)`; `opts` 第一项固定是"关闭隐藏"(键 None), 后三项从
        RTP_HIDDEN 生成(加一档自动多一个按钮)。
        """
        opts = [(None, "关闭隐藏")] + [(v, lab) for lab, v in self.RTP_HIDDEN]
        cur = self.rtp_target if self.rtp_is_hidden(self.rtp_target) else None
        return opts, cur

    def ask_unlock_rtp(self):
        """长按 3 秒: 四选一 + 确认。返回 True = 弹窗该开(UI 负责画, 关掉后回调)。

        ⚠️ **非 ready 不弹**: 弹窗是 Window 的直接子控件, 输入锁拦不住它上面的按钮
        ⇒ 装杯等待期(landed)长按也能开、也能点「确定」, 而那时盘面换不动 ⇒ 又是"切一半"。
        ⚠️ 防重入: `_on_title_touch_down` 是 **Window 级触摸观察者**, 模态弹窗拦不住它。
        """
        if self.state != "ready":
            return False
        if self._rtp_popup is not None:
            return False
        opts, cur = self.rtp_unlock_options()
        self._emit("rtp_unlock_ask", {"options": opts, "selected": cur})
        if self._on_rtp_popup is None:
            return False                 # 无头: 没有弹窗可开, 不占闸(否则永久锁死)
        self._rtp_popup = _PopupOpen()
        self._on_rtp_popup(opts, cur)
        return True

    def rtp_popup_dismissed(self):
        """弹窗被关掉(任何路径) —— 唯一解锁点。"""
        self._rtp_popup = None

    def confirm_rtp_unlock(self, val):
        """弹窗「确定」: val=None -> 关闭隐藏; 否则解锁到该档。

        ⚠️ 顺序与老版 `_confirm` 一致: 先 dismiss(清闸) 再动作。
        """
        self.rtp_popup_dismissed()
        if val is None:
            self.close_rtp_hidden()
        else:
            self.unlock_rtp(val)

    def unlock_rtp(self, val):
        """应用玩家选中的档位(幂等): 先摘掉**别的**隐藏档按钮(只留当前一个), 再切过去。

        ⚠️ "只留当前一个"是玩家定的: 开过 1000% 又改选 2000% 时, 那排上不该同时挂着两个。
        """
        for _lab, v in self.RTP_HIDDEN:
            if v != val:
                self._remove_rtp_button(v)
        if val not in self.rtp_btns:
            for label, v in self.RTP_HIDDEN:
                if v == val:
                    self._add_rtp_button(label, v)
        self.set_rtp(val)

    def close_rtp_hidden(self, silent=False):
        """关掉隐藏档: 摘掉**全部**隐藏档按钮, 返还比例回到最高常驻档。

        ⚠️ `was_hidden` 必须在**动任何东西之前**判掉 —— 先切 360% 的话再判就恒假,
           那个信息丢了, 症状是"选了关闭却什么都没发生"。
        ⚠️ 本来就是常驻档 -> **什么都不做**(不跳 360%)。
        ⚠️ 切回的是 `max(RTP_TIERS)`, **不写死 3.60**(同一份清单原则)。
        ⚠️ `_boards` 一个字都不动: 摘的是"按钮"不是"盘面", 删键 = 复刻 KeyError 闪退。
        """
        was_hidden = self.rtp_is_hidden(self.rtp_target)
        was_tier = self.rtp_target          # 语音要念"被关掉的是哪一档", 必须在 set_rtp 之前存
        for _lab, v in self.RTP_HIDDEN:     # 不手抄, 加一档自动跟着摘
            self._remove_rtp_button(v)
        if not was_hidden:
            return
        self.set_rtp(max(v for _lab, v in self.RTP_TIERS), silent=True)
        # ⚠️ `silent=True` 是必须的: 切回 360% 那句 `voice_rtp_360` 与新语音同族, 两条都走
        #    普通路径的话互斥会静默挤掉一条 —— 声明式地只留一个新语音出口。
        if not silent and self.sound_mode == "on":
            self._snd("voice_rtp_hide_%d" % int(was_tier * 100), 1.0, 0.6)

    def _add_rtp_button(self, label, val):
        """往返还率那排插一个按钮(状态: rtp_btns 的键就是"有没有隐藏档"的唯一真源)。

        ⚠️ UI 侧必须同时给出 `_fit_base`(隐藏档是运行期新建的, `_apply_sizes` 只给
           当时已存在的按钮写过它), 否则解锁后那个按钮的字号与同排四个常驻档参差。
        """
        self.rtp_btns[val] = None
        self._emit("rtp_btn_add", {"label": label, "val": val})
        self._emit("reflow_row_budget")

    def _remove_rtp_button(self, val):
        """把一个档位的按钮从返还率那排摘掉 —— `_add_rtp_button` 的逆操作。

        ⚠️ 两处必须**一起**改: ① `rtp_btns` 的键 ② UI 那排的 children。
           只删键 -> 按钮还画在屏幕上、点下去照样切档(玩家看到"关了还在");
           只删 children -> 下次 `unlock_rtp` 走 `val in self.rtp_btns` 直接 return,
           按钮永远加不回来。
        `pop(val, None)` 让"本来就没这个按钮"(冷启动直接点「关闭隐藏」)天然是空操作。
        ⚠️⚠️ **句柄必须跟着事件走**(2026-09-19 修): `rtp_btns` 的值就是 UI 的控件句柄,
           而 `pop` 这一下把它**从字典里拿掉**了 —— `rtp_btn_remove` 只带 `val` 的话,
           UI 侧再 `pop` 一次恒得 `None`, `remove_widget` 永远跑不到, 按钮留在屏幕上
           还能点(实测: 开过 1000% 再改选 2000%, 那一排挂着两个; 反复解/关无限堆积)。
           老版是一个方法(字典与 children 同体), 拆开之后**只有 `Game` 这一个 pop**。
        """
        b = self.rtp_btns.pop(val, None)
        self._emit("rtp_btn_remove", {"val": val, "btn": b})
        self._emit("reflow_row_budget")

    def reset_balance(self, notify=True):
        # 强制中断当前操作(充电/飞行/哑火/着陆), 回到 ready
        if self.state in ("charging", "flying", "misfire", "landing"):
            self._set_state("ready")
            self.power = 0.0
        self.balance = START_BEADS
        self.display_balance = float(START_BEADS)
        self._anim_target_balance = float(START_BEADS)
        self._anim_start_balance = float(START_BEADS)
        self._anim_start_time = self.now
        self.plays = 0
        self.hits = 0
        self._refresh_stats()
        self._status("已重置")
        self.round_plays = 0
        self._round_end_shown = False
        self._snd("cash")
        self._controls(True)
        if notify:
            # 清理旧 toast(老版先移除 widget 再从列表过滤, 防控件泄漏)
            self._emit("toast_clear")
            self._schedule(lambda: self._emit(
                "toast", {"text": "弹珠数量已调整到1000个", "hex": COL_GREEN,
                          "size": 28, "life": 1.5}), TOAST_RESET_DELAY)
            if self.sound_mode == "on":
                self._snd("voice_reset_progress", 1.0, 1.5)
        self._save_config()

    # ---- 每轮次数 / 轮次结束 ----
    def set_max_plays(self, val):
        """切换每轮次数上限: 更换即重置(弹珠/游玩次数/轮次全部清零, 从头开始)。"""
        if self.max_plays == val:
            return
        self.max_plays = val
        self.balance = START_BEADS
        self.display_balance = float(START_BEADS)
        self._anim_target_balance = float(START_BEADS)
        self._anim_start_balance = float(START_BEADS)
        self.plays = 0
        self.hits = 0
        self.round_plays = 0
        self._round_end_shown = False
        self._round_end_ack = False
        self._refresh_stats()
        # ⚠️ `text` 必须由这里给: 老版这行是 `self.round_btn.text = "每轮%d次" % val`
        #    (main.py:20864)。只发 value 的话 UI 得自己复刻那个格式串 —— 两份文案迟早漂。
        self._emit("max_plays", {"value": int(val), "text": "每轮%d次" % val})
        self._emit("toast", {"text": "每轮已设定为%d次" % val, "hex": COL_GREEN,
                             "size": 20, "life": 1.5})
        if self.sound_mode == "on":
            self._snd("voice_round_set_%d" % val)
        self._save_config()

    def show_round_end(self):
        """本轮游戏结束弹窗: 恭喜文案 + 统计 + 语音播报(玩家点"确定"才重置并关闭)。"""
        if self._round_end_shown:
            return
        self._round_end_shown = True
        # ⚠️⚠️ 去重闸在这里清, **不在 `reset_balance` 里清**(2026-09-19 修)。
        #    老版的闸是 `_show_round_end` 里**每次新建**的 `_done = [False]` 闭包
        #    (`android/main.py:20782`), 置 True 后永不复位 ⇒ 第二次点「确定」是硬 no-op。
        #    新版把它做成成员变量, 却在 `reset_balance` 里清回 False —— 而
        #    `round_end_confirm` 恰恰是 `置 True` → `reset_balance()` 一口气调完的,
        #    于是那句 `if self._round_end_ack: return` **永远不放行不了, 是死代码**,
        #    第二次 confirm 会二次 `reset_balance` + 多发一条 `round_end_close`
        #    (多一声 `cash`、多一次写盘)。清在这里 = 等价于老版"每个弹窗一份新闭包"。
        self._round_end_ack = False
        self._controls(False)
        self.round_history.append({
            "plays": self.round_plays,
            "balance": self.balance,
            "time": self._wall(),
        })
        if len(self.round_history) > 100:
            self.round_history.pop(0)
        self._save_history()
        self._emit("round_end_popup", {
            "plays": self.round_plays, "balance": self.balance, "max_plays": self.max_plays})
        self._play_round_end_voice()

    def round_end_confirm(self):
        """弹窗「确定」: 重置并关闭(幂等, 老版那个 `_done` 标志)。"""
        if self._round_end_ack:
            return
        self._round_end_ack = True
        self.reset_balance(notify=False)
        self._emit("round_end_close")

    def _play_round_end_voice(self, on_done=None):
        """组装并播放轮次结束语音: 模板 + 当前弹珠数 + 后缀。返回总时长(秒)。"""
        if self.sound_mode != "on":
            if on_done:
                self._schedule(lambda: on_done(), 3.0)   # sfx/off 档不念, 3s 后自动重置
            return 0.0
        prefix_key = "voice_round_end_%d" % self.max_plays
        voices = [prefix_key]
        voices.extend(number_voice_names(self.balance))
        voices.append("voice_round_suffix")
        return self._play_voice_sequence(voices, on_done=on_done)

    def _play_voice_sequence(self, names, gap=ROUND_END_VOICE_GAP, on_done=None):
        """依次播放语音片段列表。on_done 在整个序列播完后回调。返回总时长(秒)。"""
        delay = 0.0
        total = 0.0
        for name in names:
            dur = self._voice_duration(name)
            self._schedule(lambda n=name: self._snd(n), delay)
            delay += dur + gap
            total = delay
        if on_done:
            self._schedule(lambda: on_done(), total)
        return total

    # ---- 声音开关 ----
    def toggle_mute(self):
        """声音两态循环: 音效已开(含语音) -> 音效已关。**两个方向都不播任何提示音**。

        ⚠️ 玩家定稿: 一个"关声音"的按钮不该先制造声音; 开启同理(避免误触出声)。

        整段照老版 `main.py:19749-19767` 补齐 —— 只翻 `sound_mode` **不是**静音:
        `sfx.enabled` 独立于 `sound_mode`(一个是音效层的闸, 一个是"语音/琶音"的档),
        少了 `set_enabled(False)` 这一句, 按钮显示「音效已关」而声音照响。
        ⚠️ 老版这里还有 `_refresh_mute_btn()`(按钮文字+字色) 与 `_restyle_buttons()`
           (按钮**底色**, 它按 `sound_mode` 取色) —— 前者的对应事件是 `sound_mode`,
           后者是 `restyle_buttons`(唯一取值口, 见 `_controls` 的说明)。顺序也照老版。
        """
        i = self.SOUND_MODES.index(self.sound_mode)
        self.sound_mode = self.SOUND_MODES[(i + 1) % len(self.SOUND_MODES)]
        self.set_sound_enabled(self.sound_mode == "on")   # 老版 sfx.set_enabled(False/True)
        self._emit("sound_mode", {"mode": self.sound_mode})   # 老版 _refresh_mute_btn
        self._emit("restyle_buttons")                         # 老版 _restyle_buttons
        self._save_config()

    def set_sound_enabled(self, on):
        """音效层的 enabled 开关(老版 `sfx.set_enabled`) —— 由 UI 转发给音效层。

        ⚠️ 本模块**自己也要记一份**(`self.sfx_enabled`): 老版 `Sfx.impact` 的第一道闸就是
           它, 而那道闸在**抽撞钉变体之前**(见 `impact_sound`) —— 不记的话静音期照样消耗
           `_ARNG`, 变体序列与老版漂移。
        """
        self.sfx_enabled = bool(on)
        self._emit("sfx_enabled", {"enabled": bool(on)})

    # ---- 帧率上限设定 ----
    def set_fps_cap_setting(self, cap):
        """写入用户帧率档位, 并立即按三重上限重算实际目标。返回是否受理。

        ⚠️ 老版叫 `RootWidget._set_fps_cap_setting`(main.py:20629-20644), 这里去掉下划线
           当公开口 —— 它是 UI 唯一入口, 而返回值牵着"帧率上限已设为 N Hz"那条 toast
           (main.py:20706-20708), 所以**非法值必须返回 False**, 不能静默吞掉。
        ⚠️ `_FPS_USER_CAP[0] = cap` + `_apply_fps_cap()` 是平台层的事(重申 Android 高刷请求),
           在这里是**一条事件**; 缺了它, 持久化的上限就永远改不动。
        ⚠️ 白名单是 `FPS_CAP_OPTIONS`(从老版取值域抄的真值), `int()` 先转再判 ——
           `"120"` 收, `120.7` 收(截断成 120), `None`/`"abc"`/`119` 一律 False。
        """
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            return False
        if cap not in FPS_CAP_OPTIONS:
            return False
        self.fps_cap_setting = cap
        self._emit("fps_cap", {"cap": cap})   # 老版 _FPS_USER_CAP[0]=cap + _apply_fps_cap()
        self._save_config()
        return True

    # ---- 持久化 ----
    def _load_config(self):
        # ⚠️⚠️ **`try` 必须罩住整个解析块, 不能只罩 `open()+json.load`**。
        #    老版就是这样: `android/main.py:20566` 的 `try:` 一直包到 `:20600`,
        #    `except Exception: pass` 在 `:20601`。搬进本模块时被**收窄**成了"只罩读文件",
        #    后果是**启动即崩**: 存档里 `balance` 写成 `1e400` / `Infinity` 时
        #    `json.load` 读出 `float('inf')` —— 那是**合法 JSON**, 不走 json 那条 except ——
        #    而 `inf >= 0` 为真 ⇒ `int(cfg["balance"])` 抛 OverflowError ⇒ 异常一路冒出
        #    `RootWidget.__init__` / `App.build()`(那里没有 try) ⇒ 玩家进不去游戏。
        #    老版同一份存档只是"静默丢掉 `balance` 之后的几个字段", 照常启动。
        #    ⚠️ 语义是**半途生效**: 抛之前已经赋过的字段**保持生效**(实测老版
        #    max_plays / rtp_target / bet 三个已应用, balance 起全丢), 别改成"整体回滚"。
        if self._config_path is not None:
            try:
                with open(self._config_path(), "r") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    # sound_mode 不再读档(用户定稿: 声音不持久化, 每次启动都是"音效已开")。
                    if isinstance(cfg.get("max_plays"), int) and cfg["max_plays"] in (20, 50, 100):
                        self.max_plays = cfg["max_plays"]
                    # ⚠️ 白名单走 `_regular_rtp()`(从 RTP_TIERS 派生), **不手抄** —— 这里原本是
                    #    硬编码的常驻档白名单, 正是闪退事故的同款形状。派生之后"隐藏档读不回来"
                    #    是**结构性**的, 不靠谁记得改这一行。
                    if (isinstance(cfg.get("rtp_target"), (int, float))
                            and cfg["rtp_target"] in self._regular_rtp()):
                        self.rtp_target = float(cfg["rtp_target"])
                    if isinstance(cfg.get("bet"), int) and cfg["bet"] in PRESETS:
                        self.bet = cfg["bet"]
                    if isinstance(cfg.get("balance"), (int, float)) and cfg["balance"] >= 0:
                        self.balance = int(cfg["balance"])
                        self.display_balance = float(self.balance)
                        self._anim_target_balance = float(self.balance)
                        self._anim_start_balance = float(self.balance)
                    if (isinstance(cfg.get("round_plays"), int)
                            and 0 <= cfg["round_plays"] <= self.max_plays):
                        self.round_plays = cfg["round_plays"]
                    if isinstance(cfg.get("plays"), int) and cfg["plays"] >= 0:
                        self.plays = cfg["plays"]
                    if isinstance(cfg.get("hits"), int) and cfg["hits"] >= 0:
                        self.hits = cfg["hits"]
                    if (isinstance(cfg.get("fps_cap_setting"), int)
                            and cfg["fps_cap_setting"] in FPS_CAP_OPTIONS):
                        self.fps_cap_setting = cfg["fps_cap_setting"]
                    # ⚠️ 白名单走 `POWER_GRAINS`(**派生, 不手抄**) —— 与上面 rtp 那条同一个规矩。
                    if cfg.get("power_grain") in POWER_GRAINS:
                        self.power_grain = cfg["power_grain"]
            except Exception:
                pass
        if self.round_plays >= self.max_plays:
            self._auto_reset_on_start = True   # 老版 UI 还没建, 延后到 _build_ui 之后
        # ⚠️⚠️ 读档后**必须**把帧率上限同步给平台层那份模块级容器(2026-09-19 补)。
        #    老版这行在 `RootWidget.__init__` 里紧跟着 `_load_config()`
        #    (`android/main.py:13324` `_FPS_USER_CAP[0] = self.fps_cap_setting`) ——
        #    拆成模块之后**这个同步点没人接手**: `game.fps_cap_setting` 从存档读回来了,
        #    而真正决定帧率的 `_fps_user_cap()`(`platform/device.py:134`)读的是模块级
        #    `_FPS_USER_CAP[0]`, 全工程只有"玩家改档"那一条 `fps_cap` 事件会写它
        #    (`ui/play.py:438`)。
        #    ⇒ 后果: 玩家把上限设成 60Hz(省电)并落盘, **下次启动 UI 高亮显示 60、
        #       实际却按 120 跑**(耗电/发热与玩家的显式选择不符), 每次重启复发 ——
        #       再进设置弹窗点一次「确定」才会修好。
        #    放在 `_load_config` 里而不是 `root.py`: 这里是"把存档应用上去"的**唯一出口**,
        #    放这儿就不可能再被漏掉 —— 它恰恰就是这样被漏掉的。
        #    ⚠️ 走**模块限定访问**: from-import 绑的是拷贝, 虽然对 list 原地改仍生效,
        #       但本工程在 `config.SOUND_ENABLED` / `device._cfg_post` 上踩过同一个形状。
        from . import config as _cfgmod
        _cfgmod._FPS_USER_CAP[0] = self.fps_cap_setting

    def _save_config(self):
        """存设定：**优先交给注入的落盘口**，拿不到才同步写。

        ⚠️ 落盘口 `_save_cfg` 是注入的（`RootWidget` 注入平台的 `_cfg_post` = **工作线程**）。
           老版就是"post 给工作线程、主线程立刻返回"，建不起线程才同步写兜底。
           ⚠️ 曾经这里写死过**同步原子写**（注释还写着"这里无头, 直接同步原子写, 差别只是
           主线程付那次文件写"）—— 那在无头测试里语义等价，但 `Game` 现在**就是出货逻辑**：
           每次切投注/切档/**结算那一帧**都要在主线程上等一次文件写（实测 `build()` 里两次
           花 114ms，老版 8ms）。**对账看不见这个 —— 它不量时间。**
        ⚠️ 兜底支**与老版逐字相同**：非原子写。原子性由工作线程那一支负责
           （临时文件 + `os.replace`），兜底支不负责 —— 老版那条路就是 `open(path,"w")`。
        """
        if self._config_path is None:
            return
        try:
            cfg = {
                "max_plays": self.max_plays,
                "rtp_target": self.rtp_target,
                "bet": self.bet,
                "balance": self.balance,
                "round_plays": self.round_plays,
                "plays": self.plays,
                "hits": self.hits,
                "fps_cap_setting": int(self.fps_cap_setting),
                "power_grain": self.power_grain,
            }
            path = self._config_path()      # 路径在主线程算好(工作线程问不到 App)
            if self._save_cfg is not None and self._save_cfg(cfg, path):
                return                      # 已投递 ⇒ 主线程不付那次文件写
            # ⚠️ 兜底**与老版逐字相同**: 非原子写。原子性由工作线程那一支负责
            #    (临时文件 + os.replace), 兜底支不负责 —— 老版那条路就是 `open(path,"w")`。
            with open(path, "w") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    def _load_history(self):
        if self._history_path is None:
            return
        try:
            with open(self._history_path(), "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.round_history = data[-100:]
        except Exception:
            pass

    def _save_history(self):
        if self._history_path is None:
            return
        try:
            with open(self._history_path(), "w") as f:
                json.dump(self.round_history[-100:], f)
        except Exception:
            pass
