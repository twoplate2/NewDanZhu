"""PlayMixin —— `Game`(无头状态机) 与控件之间的**唯一适配层**。

职责只有三件, 一行游戏逻辑都不许有:

  1. **输入转发**: Kivy 送来的触摸/按键 -> `Game` 的输入口(命中判定用 UI 几何, 是 UI 的事)。
  2. **帧循环**: `_frame_timed` / `_frame`(加载页收尾、窗口尺寸轮询、`game.step(dt)`)。
  3. **事件派发**: `game.step(dt)` 的返回值逐条映射到控件与音效, 外加老版那些**纯界面**的
     弹窗与刷新(`Game` 没有、也不该有的那一半)。

⚠️⚠️ **凡是 `Game` 里已经有的, 这里绝不许再实现一遍** —— 那正是这次重构要消灭的东西。
   判定方法: `grep -n "def <名字>" danzhu/game.py`。已经搬过去、**不要**在这里重实现逻辑的:
   `start_charge / launch / settle / park_ball / set_bet / set_rtp / reset_balance /
    toggle_mute / set_max_plays / show_round_end / round_end_confirm / _refresh_stats /
    _voice_duration / _play_voice_sequence / _play_round_end_voice / _play_charge_sound /
    _play_pocket_sound / _play_win_voice / _play_events / easter_finish / unlock_rtp /
    close_rtp_hidden`, 以及 `show_easter_popup` / `ask_unlock_rtp` / `_add_rtp_button` /
   `_remove_rtp_button` 的**状态那一半**。

⚠️ **唯一的例外 = "同拍转发"**(见下面「〇、同步入口」)。上面那份清单里有 6 个名字
   在本模块里**又出现了一次**(`start_charge / launch / set_bet / set_rtp /
   reset_balance / toggle_mute`, 外加 `_refresh_stats`), 它们的方法体只有一行
   `return self._act(self.game.X, ...)`, **不含一个字的游戏逻辑** —— 存在的唯一理由是
   `ui/ui_base.py` 建控件时按**老版写法**回调这些旧名字(`self.set_bet(x)` 等), 留着它们
   老版到新版的 diff 才读得出来。除这 7 个之外, `Game` 里已有的名字**一个都不许**在这里
   再出现(多一个就是第二个状态源); 其余 UI 入口一律在**调用点**直接写
   `self._act(self.game.X, ...)` —— 本模块里不许再添新的同名转发。

同一条规矩也管**布局那一半**: `_restyle_buttons` / `_set_controls_enabled` /
   `_reflow_row_budget` / `_sync_hud_dim` / `_apply_row_budget` / `_fit*` 都在
   `ui/ui_base.py`(UiMixin)。本模块**只调不写** —— 写了会因 MRO 靠前而**静默遮蔽**它。

⚠️ 两个"半个方法"最容易写重, 单独点名:
   · `_set_controls_enabled`(输入锁的界面那一半)在 **UiMixin** —— 本模块只从 `controls`
     事件转发给它(标志本身在 `Game._controls_enabled`, 底色在 `restyle_buttons` 事件);
   · `_ask_unlock_rtp` / `_show_easter_popup`: `Game` 那边是"占闸 + 发事件 + 调注入回调",
     这边只画弹窗。`root.py` 已经把这两个方法当**构造注入**传给了 `Game`
     (`on_rtp_popup=` / `on_easter_popup=`), 所以那两条事件**不许**再开一次弹窗。
"""

import os
import tempfile
import time

from kivy.app import App
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.utils import platform

from ..config import (COL_BALL, COL_BTN, COL_BTN_OFF, COL_DARKRED, COL_DIV,
                      COL_FIRE, COL_GREEN, COL_METER, COL_SUB, COL_TEXT,
                      FPS_CAP_DEFAULT, FPS_CAP_OPTIONS, REPLAY_BAKE_MAX_SEC,
                      _FPS_USER_CAP, hex_rgb)
from ..platform.boot import _BOOT_T0, _boot_log
from ..platform.device import _apply_fps_cap, _vibrate, _vibrate_double
# ⚠️ 帧计时账本(`_FRAME_SELF` / `_FRAME_CALLS` / `_SINCE_LAUNCH` / `_FRAME_END` /
#    `_TEXUPD_ACTIVE`)的唯一定义点在 `ui/text.py` —— 另建一份不会报错, 只会让跑分面板
#    那几栏**静默失效**。`_restyle_buttons` / `_set_controls_enabled` / `_reflow_row_budget`
#    / `_sync_hud_dim` / `_apply_row_budget` 等**布局那一半在 `ui/ui_base.py`(UiMixin)** ——
#    本模块只调它们, 绝不重定义(重定义会因 MRO 靠前而**静默遮蔽**掉 UiMixin 的版本)。
from .text import (_FRAME_CALLS, _FRAME_END, _FRAME_SELF, _SINCE_LAUNCH,
                   _TEXUPD_ACTIVE, _set_label_text)
from .widgets import _land_layer


class PlayMixin(object):
    """`Game` ↔ 控件的适配层。**不定义 `__init__`**(`__init__` 只在 root.py)。"""

    # =====================================================================
    # 〇、同步入口 —— **从 UI 侧调 Game 一律走 `_act`, 不要直接 `self.game.xxx()`**
    # =====================================================================
    # ⚠️⚠️ 为什么: `Game` 只把界面效果**排进事件缓冲**, 真正落地要等 `_frame` 里那次派发。
    #    而老版是**当场同步**的 —— `root.launch()` 那一刻就 `sfx.play("launch")`。
    #    于是从 UI 回调(按钮/按键/触摸)调 `game.launch()` 会让那一声**晚一帧**发出来:
    #    对账台实测第 1 条音效就是 `frame 34` vs `frame 35`, 整条轨迹跟着漂。
    #    `game.step()` **内部**发出的那些不受影响 —— 它们本来就在同一帧的派发里落地。
    # ⚠️⚠️ **规则的适用范围 = UI 的每一个入口, 不是"那几个按钮"**: 六个同名转发只是
    #    `ui_base` 建控件时用的旧名字, 而**其余入口在调用点直接写 `self._act(...)`** —
    #    两处**都是当拍**, 行为必须一致。踩过的坑: 返还率按钮曾经写成
    #    `lambda: self.game.set_rtp(t)`(直调 ⇒ 晚一帧), 而**同一屏**的投注按钮走
    #    `self.set_bet(x)`(⇒ `_act`, 当拍) —— 同一屏两个行为不同, 且对账台看不到
    #    (录制驱动走的是包装口 `root.set_rtp`, 与真实点击不是同一条路)。
    # ⚠️ `Game` 里**不发事件**的那几个入口不加 `_act`(加了也什么都没派发): `click` /
    #    `mark_charge_uid` / `begin_bench_hold` / `begin_rtp_hold` / `rtp_popup_dismissed`
    #    / `easter_finish` / `on_touch_down_allowed`。判据是"这个方法有没有 `_emit`/`_snd`"。
    # ⚠️ `_refresh_stats` 这条转发**故意不加 `_act`**: 它在 `_build_ui` **中途**被调,
    #    那儿控件树还没搭完(`game_area` 可能还不存在), 当场派发会派到半个树上。
    def _act(self, fn, *a, **kw):
        """调一个 `Game` 的输入口, 并**立刻**把它的界面效果发出去(等价老版的同步调用)。"""
        r = fn(*a, **kw)
        self._dispatch(self.game.take_events())
        return r

    # ---- 6 个"同拍转发"(唯一允许在这里与 `Game` 同名的东西, 见抬头那条例外) ----
    # ⚠️ 方法体只有一行转发 + `_act`, **不许**在这里补任何判断/文案/副作用 ——
    #    补一行就是把逻辑搬回来了一半(而另一半还在 `Game`, 两份迟早漂)。
    #    它们存在的理由只有一个: `ui/ui_base.py` 建控件时按老版写法回调这些**旧名字**。
    def start_charge(self, *a):
        return self._act(self.game.start_charge, *a)

    def launch(self, *a):
        return self._act(self.game.launch, *a)

    def set_bet(self, v, **kw):
        return self._act(self.game.set_bet, v, **kw)

    def set_rtp(self, t, **kw):
        return self._act(self.game.set_rtp, t, **kw)

    def reset_balance(self, *a, **kw):
        return self._act(self.game.reset_balance, *a, **kw)

    def toggle_mute(self, *a):
        return self._act(self.game.toggle_mute, *a)

    # =====================================================================
    # 一、输入转发
    # =====================================================================
    def on_touch_down(self, touch):
        """锁输入期间把 HUD 的触摸统一吞掉。

        ⚠️ **只挡 `on_touch_down`, 不挡 `on_touch_up`** —— 发射键在**按下**时就 grab 了,
        松手那条路要照常送达, 否则球永远发不出去。而锁只发生在 `launch()` **之后**,
        那时手早就松开了, 两者不打架。
        ⚠️ 弹窗是 Window 的直接子控件、**不挂在 RootWidget 下**, 所以锁输入期间弹窗上的
        按钮照常可点(彩蛋/轮次结束/设置弹窗都靠这条)。
        ⚠️ 记的是 `uid` 不是 Touch 对象: Kivy 的 Touch 是**池化复用**的。
        ⚠️ 用**显式命中判定**(`_to_eq` + `collide_point`), 不要靠 `touch.grab_current` ——
        实测那条在这个 Kivy + 触摸来源上读不到(派发走完仍是 None), 照它判定会**静默失效**。
        """
        if not self.game.on_touch_down_allowed():
            return True
        _pos = touch.pos
        _lay = _land_layer()
        if _lay is not None:
            _pos = _lay._to_eq(*_pos)          # Window 级触摸是物理坐标, 先逆旋转
        _fb = getattr(self, "fire_btn", None)
        if (self.game.state == "ready" and _fb is not None
                and _fb.collide_point(*_pos)):
            self.game.mark_charge_uid(touch.uid)
        return super().on_touch_down(touch)

    def _on_key_down(self, win, key, *rest):
        if key == 32:                     # 空格: 按住蓄力
            # ⚠️ 当拍(`_act`): 老版这里是 `self.start_charge()` 内联在 RootWidget 上 ——
            #    蓄力那声棘轮/`蓄力中`文案必须是**按下这一拍**的, 晚一帧听得出来。
            self._act(self.game.space_down)
            return True
        if key == 27:                     # Android 返回键 / ESC: 吞掉, 防止误触退出
            return True
        return False

    def _on_key_up(self, win, key, *rest):
        if key == 32:
            self._act(self.game.space_up)  # 冻结力度 + 50ms 去抖(定时器在 Game 里)
            return True
        return False

    def _space_fire(self, dt):
        """老版那条去抖定时器的落点(`Clock.schedule_once(self._space_fire, 0.05)`)。

        定时器现在排在 `Game.space_up()` 里, 这里保留同名转发 —— 老版这是个可直调的口。
        ⚠️ 同样走 `_act`: 它的下游是 `launch()`, 那一串(音效/震动/文案)老版是当拍的。
        """
        self._act(self.game.space_fire)

    def _on_title_touch_down(self, win, touch):
        pos = touch.pos
        layer = _land_layer()
        if layer is not None:
            pos = layer._to_eq(*pos)      # Window 级触摸是物理坐标, 先逆旋转到等效坐标
        # 装杯装满后等玩家点击才退场。这一下**吞掉** —— 早退, 不触发下面"标题 /
        # 期望返还比例"的长按计时: 玩家点的是"我看够了", 不是 HUD 上的长按热区。
        # 受理条件(装满静止 + 过了最短停留)全在 `Game.click()` 里, 这里只管"点到了就试一下"。
        # ⚠️ 用裸 `return`(不是 `return True`): 观察者返回值只是 OR 聚合、**不短路**。
        if self.game.click():
            return
        if (self.title_lbl.collide_point(*pos)
                and not self.game._bench_running):
            self.game.begin_bench_hold()
        # 长按"期望返还比例"3 秒 -> 四选一档位弹窗。判据与上面同构: 按下记时刻、每帧查时长、
        # 抬手清零, 不在这儿做任何计时器(免得和跑分那套抢状态)。
        if self._rtp_title_lbl.collide_point(*pos):
            self.game.begin_rtp_hold()

    def _on_title_touch_up(self, win, touch):
        # 抬手即取消长按 + 发射保底, 两条判据都在 `Game.touch_up` 里 —— 尤其
        # "必须是按下发射键的那根手指"那一条: 这里是 **Window 级**观察者, 屏幕上每一次
        # 抬手都会进来, 裸判 `state == "charging"` 的话蓄力期另一根手指蹭一下球就飞了。
        # ⚠️ 当拍(`_act`): 老版这一支就是 `self.launch()` 内联在这里 —— 保底发射那条
        #    路径上的音效/震动/文案必须落在**抬手这一拍**, 否则"滑出按钮再松手"的玩家
        #    会比走 on_release 的玩家晚一帧听到发射音。
        self._act(self.game.touch_up, touch.uid)

    # =====================================================================
    # 二、帧循环
    # =====================================================================
    def _frame_timed(self, dt):
        """给 `_frame` 计一个**本线程**的耗时, 喂给跑分面板。

        ⚠️ 用 `perf_counter` 而不是 `process_time`: 要量的是"这一帧我们自己的代码在
        **这条线程**上花了多久", 不能把工作线程/Java 线程的 CPU 算进来。
        ⚠️ 包一层而不是改 `_frame` 内部: 那个函数里有提前 return, 内嵌计时容易漏。
        """
        _t0 = time.perf_counter()
        try:
            self._frame(dt)
        finally:
            _FRAME_SELF[0] = (time.perf_counter() - _t0) * 1000.0
            _FRAME_CALLS[0] += 1
            _SINCE_LAUNCH[0] += 1

    def _frame(self, dt):
        if not getattr(self, "_first_frame_logged", False):
            self._first_frame_logged = True
            _boot_log("frame", "第一帧(画面第一次真的画出来)")
        # 主线程心跳: 探针在另一条线程上, 只有心跳能把"主线程被卡住"和"预热自己停了"分开
        # (预热是主线程驱动的)。⚠️ 只在**启动窗口**内记 —— `_boot_log` 只增不减, 游戏期
        # 一直记会把它撑爆。
        try:
            _hb = time.perf_counter()
            if self._hb_t <= 0.0:
                self._hb_t = _hb
                self._hb_n = 0
            elif (self._load_veil is not None) or ((_hb - _BOOT_T0) < 12.0):
                self._hb_n += 1
                if _hb - self._hb_t >= 0.5:
                    _boot_log("frame", "心跳 %.1f 帧/秒(窗口 %.0f ms)"
                              % (self._hb_n / (_hb - self._hb_t), (_hb - self._hb_t) * 1000.0))
                    self._hb_t = _hb
                    self._hb_n = 0
        except Exception:
            pass
        # ⚠️ `_check_title_hold()` 不在这里调: 它现在在 `Game._frame` 的**第一行**(与老版
        #    同址), 这里再来一次就是同帧两次。老版这一行的位置在 `_frame` 开头, 而新版
        #    整个状态机都在 `game.step(dt)` 里 —— 位置差别只在这一个函数的范围内。
        # 冷启动加载页收尾: 音效库**真的能播**了就摘掉(见 `_LoadVeil` / `Sfx.audio_ready`)。
        # ⚠️ 判据是"烘完 **且** 探到真能播(或硬超时)", 不是"烘完就摘" —— `SoundPool.load()`
        #    返回 sampleId ≠ 解码完, 没解码完 play() 返回 0 静默跳过。
        # ⚠️ 绝不软锁: `audio_ready()` 里 `!enabled` 与"探针不可用"都直接放行, 且等待有硬超时。
        _veil = getattr(self, "_load_veil", None)
        if _veil is not None:
            # 就绪判据由 `_LoadVeil.tick()` 给 —— 它还要管开场动画、最短停留和整页淡出,
            # 所以这里只问"能摘了吗"。它自己不持有 Clock, 由这里驱动。
            if _veil.tick(self.sfx.audio_ready()):
                # ⚠️ **先按「这一页是不是重放结果页」分岔** —— 重放页**绝不落进下面那条
                #    自动摘页**: `audio_ready()` 在**音效关掉时恒为真**, 掉进 elif 就会
                #    在第一帧把整页摘掉, 玩家点下去看到的是"画面没有任何反馈"(而烘焙照样
                #    在后台跑几秒)。⇒ 形状定死: 重放页的出口**只有玩家点击**。
                if _veil is getattr(self, "_replay_veil", None):
                    # ⚠️ 必须等**这一次重放自己的烘焙收工**才报结果, 否则 `_replay_cost_text()`
                    #    会读到**上一次的** `bake_ms` 配上刚被清零的 `ready_ms` ⇒ 报假数。
                    # ⚠️ 下面时间那一道是**防软锁**兜底: 这一页吞掉所有触摸、出口只有
                    #    "玩家点击", 而能不能点取决于 `_hold`; 烘焙线程万一永远不回来,
                    #    玩家就被锁在启动页(项目红线: 绝不软锁)。正常路上它用不到。
                    _waited = time.perf_counter() - float(getattr(self, "_replay_t0", 0.0) or 0.0)
                    # 还要等**实验档**跑完(`_arm_busy`): 它挂在 `_await_ready` 里、在
                    # `_audio_ready = True` 之前, 而 `sfx.baked` 比那更早 ⇒ 少了这一条,
                    # 玩家手快就会点开一个还没有实验结果的详情弹窗。
                    if (not _veil._hold and not getattr(self.sfx, "_arm_busy", False)
                            and (self.sfx.baked or _waited > REPLAY_BAKE_MAX_SEC)):
                        # 重放完成: **不自动摘页** —— 摆出结果等玩家点一下。
                        _veil._hold = True
                        _veil.set_done(self._replay_cost_text())
                        _veil._on_tap = self._finish_replay_veil
                elif not _veil._hold:
                    self._load_veil = None
                    _veil.drop()
                    _boot_log("frame", "加载页摘除")
                    # ⚠️ 两个耗时数必须**同一个时钟**: 老"总耗时"曾是"烘焙线程那两段的时长"
                    #    (差出"进程启动->烘焙开工"那一段, 从来没人算过), 于是出现过
                    #    「总耗时 < 无音频加载累计耗时」。现在在摘页这一刻同时落两个盘。
                    try:
                        _tot_ms = (time.perf_counter() - _BOOT_T0) * 1000.0
                        self.sfx._total_ms = _tot_ms
                        _noa = self._calc_no_audio_ms(_veil, _tot_ms)
                        self.sfx._no_audio_ms = _noa
                        _boot_log("frame", "启动总耗时 %.0f ms（摘页那一刻, 与「无音频加载"
                                           "累计耗时」同一时钟）⇒ 音频让它多等 %.0f ms"
                                  % (_tot_ms, (_tot_ms - _noa) if _noa > 0.0 else 0.0))
                    except Exception:
                        pass
                    # 摘页那一刻的四元快照 —— 一次同时判两件事:
                    #   · rebuild_count: 预期恒为 0, 非 0 则"白重建"假设复活。
                    #   · 「闸门 vs 已到」: `named` 比 `_ids` 多 = **闸门已放行而后端没有**,
                    #     那才是真正的静默故障;「应到 − 已到」非空只是"有样本加载失败"。
                    try:
                        _o = self.sfx.out
                        _nm = len(self.sfx.named)
                        _ids = (len(getattr(_o, "_ids", {}))
                                + len(getattr(_o, "_ids2", {})))
                        _pth = len(getattr(_o, "_paths", {}))
                        _warn = ("  ⚠️闸门比后端多 %d" % (_nm - _ids)) if _nm > _ids else ""
                        _boot_log("frame", "摘页快照: 重建 %s 次 / 应到 %d / 已到 %d / 闸门 %d / "
                                           "累计加载失败 %s%s"
                                  % (getattr(_o, "rebuild_count", "?"), _pth, _ids, _nm,
                                     getattr(_o, "_load_failed_total", "?"), _warn))
                    except Exception:
                        pass
        # 窗口尺寸轮询(bind(size) 对程序启动期的 resize 不可靠)。
        # ⚠️ 初值 `self._last_win_size = None` 在 `root.py:__init__` 里(**有**, 与老版
        #    main.py:13349 同一行同一处) —— 这里用 `getattr(..., None)` 取只是**不依赖**
        #    谁记得补那一行, 取到的默认值与那个初值**逐位相同**(首帧 `ws != None` 恒真
        #    ⇒ 照跑一次重排, 并把这行写回去), 所以行为一字不差。
        #    ⚠️ 别把这条兜底读成"新版没人初始化它" —— 那是错的, 照那个说法去改会改错方向。
        ws = (Window.width, Window.height)
        if ws != getattr(self, "_last_win_size", None):
            self._last_win_size = ws
            layer = _land_layer()
            if layer is not None:
                layer.apply_orientation()   # 横竖切换: 重算旋转角/等效窗口(保持竖构图)
            app = App.get_running_app()
            force = getattr(app, "_apply_orientation", None)
            if force is not None:
                force()                     # 窗口一变立即重申方向策略, 不等守卫周期
            self._fit_width()
            self._apply_sizes()
        # ---- 状态机推进 + 事件派发 ----
        # ⚠️ 老版那几百行 state 分支全在 `Game.step` 里; 这里只把它的产出画出来。
        _events = self.game.step(dt)
        # ⚠️⚠️ **老版蓄力超 3 秒的兜底自动发射那一帧是 `launch(); return`**
        #    (`main.py:21221-21222`) —— `return` 把整条渲染尾巴都跳过了。
        #    状态机搬走之后这里必须照做, 否则那一帧白付一次
        #    `_set_label_text(balance_lbl, ...)` —— 老版自己算过那是 `_frame` 里
        #    **最大的单块**(45 秒 1.06 秒)。事件照派发(老版 `launch()` 的副作用在里面)。
        if self.game._render_skip:
            self._dispatch(_events)
            return
        # ⚠️ **余额标签在派发之前写** —— 老版这两行的顺序是
        #    `_set_label_text(balance_lbl, ...)` 然后 `game_area.tick_draw()`。而 `ui_tick`
        #    是 `Game._frame` 的**最后一条**事件、派发到它就会调 `tick_draw()` ⇒ 若把余额
        #    写在派发之后, 两者就调换了。数值上等价(`display_balance` 在 `step` 内已更新完),
        #    但"同一帧里谁先谁后"是本工程的对账口径, 不靠"反正互不依赖"来省。
        _set_label_text(self.balance_lbl, str(int(round(self.game.display_balance))))
        self._dispatch(_events)
        # 装杯期把整块界面(减去游戏区)压暗。⚠️ 位置: 必须在 `tick_draw()` **之后** ——
        # 演出由 tick_draw -> win_fx.tick() -> `_redraw()` 推进, 而 `_redraw` 会把**本帧
        # 真正画上去的** a_dim 存进 `_a_dim_now`, HUD 从这里取值 ⇒ 两边永远是同一个数。
        # 放在前面的话 HUD 读到的是上一帧(退场结束那一帧板面已经不画了, HUD 还会多黑一帧)。
        # ⚠️ 它后面**不能再有早退**。
        self._sync_hud_dim()
        # ⚠️ **本帧 `_frame` 算完的时刻**。这是 `_frame` 的**最后一行** —— 放在中间的话
        # "尾"会含住后面还没跑的部分; 它后面**不许再加任何语句**(加了也不会被算进去)。
        # 只在采样期记(别让埋点改变被测量的东西)。
        if _TEXUPD_ACTIVE[0]:
            _FRAME_END[0] = time.perf_counter()

    # =====================================================================
    # 三、事件派发
    # =====================================================================
    def _dispatch(self, events):
        """把一帧的事件**按产出顺序**逐条映射到控件与音效。

        ⚠️ 顺序 = 老版调用点顺序(`Game` 就是按老版的调用次序 `_emit` 的)。逐条处理、
           **不许按种类分组重排** —— 灯/槽闪/大字/音效之间谁先谁后是有意义的。
        ⚠️ 每一种 `kind` 都要有分支: 漏一种 = 那条事件被**静默吞掉**(丢一声/少一次刷新/
           弹窗不开), 而且不报错。
           `grep -o '_emit("[a-z_]*"' danzhu/game.py | sort -u` 是这份清单的唯一真源。

        ⚠️⚠️ **派发期间新产生的事件必须在本帧内派完**(下面那个 while)。
           原因: `ui_tick` 会驱动 `game_area.tick_draw()` -> `win_fx.tick()` ->
           `_pump_reveal()` -> `_fire_done()` -> `Game._on_settled` —— 一整套**游戏逻辑**
           在**派发过程中**跑, 而它发出的 `sound` 落进了 `_ev`。而本帧那次 `take_events()`
           **早就取完了**, 于是新事件要等到下一帧才被派发: 实测 `voice_win20` 老版第 866
           帧、新版第 867 帧(整局唯一一处音效差)。
           老版没有这个问题 —— 它是同步调用, `sfx.play` 当场就响。
        """
        for _round in range(8):        # 正常最多 2 轮; 8 是"事件自我派生"的失控兜底
            for ev in events:
                self._dispatch_one(ev)
            events = self.game.take_events()
            if not events:
                return
        print("[ui] ⚠️ 事件派发 8 轮还没排空 —— 有事件在自己派生事件, 请查 Game 的 _emit")

    def _dispatch_one(self, ev):
        """派发**一条**事件。清单见 `_dispatch` 的说明。"""
        if True:
            kind = ev.kind
            if kind == "launch":
                # 老版 `launch()` 里那句 `_SINCE_LAUNCH[0] = 0`(在"state 必须是 charging"
                # 守卫之后、**哑火分支之前** —— 所以哑火也清)。
                # ⚠️ 事件转发与直接写在 `Game.launch()` 里**位置等价**: `_SINCE_LAUNCH[0] += 1`
                #    在 `_frame_timed` 的 finally(即 `_frame` **之后**), 所以帧内清零与帧外清零
                #    收敛到同一个值。`Game` 不该 import UI 的账本, 所以走事件。
                _SINCE_LAUNCH[0] = 0
            elif kind == "sound":
                self.sfx.play(ev.name, ev.gain, ev.throttle)
            elif kind == "status":
                _set_label_text(self.status_lbl, ev.data["text"])
            elif kind == "stats":
                # ⚠️ **不额外调 `_fit1`**: `_install_fit` 已经把 `_fit1` 挂在 `text` 上了,
                #    再调一次就是多量一次字宽(而 `_FRAME_FIT[0]` 是跑分对账的口径)。
                _set_label_text(self.stats_lbl, ev.data["text"])
            elif kind == "power_label":
                self.power_lbl.text = ev.data["text"]
            elif kind == "fire_color":
                self.fire_btn.background_color = hex_rgb(ev.data["hex"]) + (1,)
            elif kind == "lamp":
                self.game_area.set_lamp(ev.data["slot"], ev.data["hex"])
            elif kind == "lamps_off":
                self.game_area.lamps_off()
            elif kind == "pulse_slot":
                # 老版脉宽写死在 `GameArea.pulse_slot` 里(0.30s) —— 事件带的 `life` 是同一份额度
                self.game_area.pulse_slot(ev.data["slot"])
            elif kind == "big_result":
                self.game_area.big_result_text(ev.data["m"], ev.data["payout"])
            elif kind == "toast":
                self.game_area.center_toast(ev.data["text"], hexcolor=ev.data["hex"],
                                            size=ev.data["size"], life=ev.data["life"])
            elif kind == "toast_clear":
                # 老版 `reset_balance` 里那段"先移除 widget 再从列表过滤"(防控件泄漏)
                for _e in self.game_area._effects:
                    if _e["kind"] == "toast":
                        for _w in _e["ws"]:
                            self.game_area.remove_widget(_w)
                self.game_area._effects = [e for e in self.game_area._effects
                                           if e["kind"] != "toast"]
            elif kind == "update_slots":
                self.game_area._update_slots()
            elif kind == "redraw":
                self.game_area._redraw()
            elif kind == "ui_tick":
                self.game_area.tick_draw()
            elif kind == "controls":
                # 界面那一半在 UiMixin(标签染色 + 拿 `_refresh_mute_btn`); 底色归
                # `restyle_buttons`(另一条事件), 标志归 `Game`。
                self._set_controls_enabled(ev.data["enabled"])
            elif kind == "restyle_buttons":
                self._restyle_buttons()
            elif kind == "reflow_row_budget":
                self._reflow_row_budget()
            elif kind == "rtp_btn_add":
                self._add_rtp_button(ev.data["label"], ev.data["val"])
            elif kind == "rtp_btn_remove":
                # 句柄跟着事件来(`Game` 已经把键与值一起摘走了, 这里只摘控件)
                self._remove_rtp_button(ev.data["val"], ev.data.get("btn"))
            elif kind == "sound_mode":
                self._refresh_mute_btn()
            elif kind == "sfx_enabled":
                self.sfx.set_enabled(ev.data["enabled"])
            elif kind == "fps_cap":
                _FPS_USER_CAP[0] = ev.data["cap"]
                try:
                    _apply_fps_cap()
                except Exception:
                    pass
            elif kind == "max_plays":
                self.round_btn.text = ev.data["text"]
                # 老版 `_set_max_plays(v, sel_btns=...)` 顺手刷了**还开着的**那个弹窗里的选中高亮
                _rs = getattr(self, "_round_sel_refresh", None)
                if _rs is not None:
                    _rs()
            elif kind == "round_end_popup":
                self._show_round_end(ev.data)
            elif kind == "round_end_close":
                _rp = getattr(self, "_round_end_popup", None)
                if _rp is not None:
                    _rp.dismiss()
            elif kind == "bench_menu":
                self._show_bench_menu()          # 长按标题 3 秒 -> 性能测试菜单(跑分 Mixin)
            elif kind == "vibrate":
                if ev.data.get("double"):
                    _vibrate_double(ev.data["ms"])
                else:
                    _vibrate(ev.data["ms"])
            elif kind == "reveal_fallback":
                print("REVEAL 兜底触发: %s" % (ev.data["win"],))
            elif kind == "reveal_fail":
                # 揭晓那一段的 try/except 是静默的, 不留痕的话"数字永不出现"会查无对证
                print("REVEAL FAIL: %s" % ev.data["err"])
            elif kind == "collision":
                # ⚠️ **不在这里播碰撞音**。老版 `_play_events(ev, amp, b)` 的那一声,
                #    `Game` 已经按同一次汇总发成了 `sound` 事件(`impact_sound` + `_snd`) ——
                #    这里再走一遍 `sfx.impact()` 就是**每一声双播**(`Sfx.impact` 与
                #    `game.impact_sound` 是同一决策的两份实现)。这条事件剩下的去处是
                #    "UI 特效", 而目前的特效(钉子电光、球受击压扁)读的是物理层写在
                #    `ball.peg_flash` / `ball.squash` 上的信号, 由 `tick_draw` 消费。
                pass
            elif kind == "settle":
                # 结算流水。老版 `settle()` 里的 UI 副作用(指示灯/槽位白闪/中奖大字/装杯)
                # 都已经是**独立事件**(lamp / pulse_slot / big_result / ...), 这里无事可做。
                pass
            elif kind == "state":
                # 状态迁移本身没有 UI 动作: 界面差异全走 controls / restyle_buttons /
                # fire_color 那几条(老版 `_frame` 的 state 分支里也没有"切状态就改控件")。
                pass
            elif kind == "easter_popup":
                # 弹窗由构造时注入的 `on_easter_popup`(=`self._show_easter_popup`)直接画出来
                # (见 `Game.show_easter_popup`)。这里再画一次 = 叠两个模态窗。
                pass
            elif kind == "rtp_unlock_ask":
                # 同上: 弹窗由注入的 `on_rtp_popup`(=`self._ask_unlock_rtp`)画出来
                # (见 `Game.ask_unlock_rtp`)。`Game` 那边已经占住了防重入闸。
                pass

    # =====================================================================
    # 四、控件刷新(老版有、`Game` 没有的那一半)
    # =====================================================================
    # ⚠️ 曾经在这里写过 `_set_controls_enabled` 与 `_reflow_row_budget` —— **已删**:
    #    两者都在 `ui/ui_base.py`(UiMixin)里有同名实现, 而 PlayMixin 排在它**前面**,
    #    留着就是**静默遮蔽**(编译过、跑得动、行为悄悄变成这一份)。
    #    派发里那两条事件现在直接落到 UiMixin 的实现上, 与老版 `_frame`/`_set_controls_enabled`
    #    的调用点一一对应。
    def _refresh_stats(self):
        """老版 `_refresh_stats` 的**转发口** —— UiMixin 的 `_build_ui` 末尾会调它。

        ⚠️ 文案与"跑分状态栏期间不刷"的判据都在 `Game._refresh_stats` 里(它发 `stats` 事件),
           这里不许自己拼那个 `累计%d投%d中(%.0f%%)` —— 两份文案迟早漂。
           文字落到标签上是**下一帧的派发**(与 `set_bet`/`reset_balance` 那几条同拍)。
        """
        self.game._refresh_stats()

    def _refresh_mute_btn(self):
        """只写"文字 + `.color`" —— 底色归 `_restyle_buttons`(可能是朝 `COL_BG` 混过一档的)。

        ⚠️ 那两个前景色都打过 tag + 预热过 ⇒ 这里是**命中**, 零重建。
           **不要**在这里多加第三种颜色(比如"禁用态的字色"): 那是新纹理, 而且正好落在
           "回 ready"那一拍(字形重排最贵的那一帧)。
        """
        if self.game.sound_mode == "on":
            self.mute_btn.text = "音效已开"
            self.mute_btn.color = hex_rgb("#0e1524") + (1,)
        else:
            self.mute_btn.text = "音效已关"
            self.mute_btn.color = hex_rgb("#c0c8e4") + (1,)

    def _add_rtp_button(self, label, val):
        """往返还率那一行插一个按钮 —— 插在"右侧留空"的 Widget 之前(接在最右那个后面)。

        ⚠️ `rtp_btns` 的**键**是"有没有这个按钮"的唯一真源(`Game` 维护), 这里只把控件
           句柄填进它的**值**。⚠️ 老版这里还调了一次 `_reflow_row_budget()` —— 那条现在由
           `Game` 的 `reflow_row_budget` 事件单独驱动, 这里再调一次就是同帧两次。
        ⚠️ 回调走 `self.set_rtp`(= `_act`) **不是** `self.game.set_rtp`: 直调会晚一帧,
           而**同一屏**的投注按钮是当拍(`self.set_bet`) —— 老版两个都是当拍的。
        """
        b = self._mk_button(label, lambda _b, t=val: self.set_rtp(t))
        b.size_hint_x = None
        b.width = dp(56)
        b.size_hint_y = 1.0
        # ⚠️ `_fit_base` 必须在这里就给对: `_apply_sizes` 只给**当时已存在**的按钮写过它,
        #    而隐藏档是**运行期**(长按解锁)新建的。不给的话 `_fit1` 会退回
        #    `float(b.font_size)` —— 那是裸的 16.0(不含 _font_scale*_ui_scale)且会被**缓存**
        #    成基准: 实测 320dp 上解锁后隐藏档按钮 font=11.20, 而同排常驻档是 11.95。
        b._fit_base = (sp(16) * float(getattr(self, "_font_scale", 1.0) or 1.0)
                       * float(getattr(self, "_ui_scale", 1.0) or 1.0))
        self.game.rtp_btns[val] = b
        try:
            idx = self._rtp_row.children.index(self._rtp_spacer) + 1
        except (ValueError, AttributeError):
            idx = 1
        self._rtp_row.add_widget(b, index=idx)
        return b

    def _remove_rtp_button(self, val, btn=None):
        """把一个档位的按钮从返还率那排摘掉 —— `_add_rtp_button` 的逆操作。

        ⚠️ 两处必须**一起**改: ① `rtp_btns` 的键(`Game` 侧) ② `_rtp_row` 的 children。
           只删键 -> 按钮还画在屏幕上、点下去照样切档(玩家看到"关了还在");
           只删 children -> 下次 `unlock_rtp` 走 `val in rtp_btns` 直接 return, 按钮加不回来。
        ⚠️⚠️ **句柄由事件带过来**(`btn`), 这里**不许再 pop 一次**(2026-09-19 修): `Game`
           那一侧的 `pop` 已经把句柄从字典里拿走了, 这里再 pop 恒得 `None` ⇒
           `remove_widget` 跑不到, 按钮留在屏幕上(实测 8 个孩子里留着旧的 1000%)。
           `val` 只用于可读性/日志, 不再参与查找。
        ⚠️ `_rtp_spacer` 绝不碰 —— 它是 `_add_rtp_button` 的定位锚点。
        `btn is None` 让"本来就没这个按钮"(冷启动直接点「关闭隐藏」, 或 `Game` 那边
        `pop` 到的就是 `None`)天然是空操作。
        ⚠️ **不返回任何东西**(老版 `_remove_rtp_button` 的返回值就是 `None`, main.py:19849)
           —— 旧签名里那句 `return btn` 是这次端口多出来的, 与老版接口不一致, 已删。
        """
        if btn is not None:
            self._rtp_row.remove_widget(btn)

    def _ask_unlock_rtp(self, opts, selected=None):
        """长按"期望返还比例"3 秒的**弹窗那一半**(状态/防重入/非 ready 守卫都在 `Game`)。

        选项与默认选中**由 `Game.rtp_unlock_options()` 给**(从 `RTP_HIDDEN` 派生, 加一档
        自动多一个按钮) —— 这里一个字都不许自己拼, 拼过一次就会漏改(弹窗显示上一版档位)。

        与旧版的四点差异(玩家定稿): ① 每次都弹; ② 点选项只**选中**, 点「确定」才生效;
        ③ 「关闭隐藏」能把隐藏档按钮**摘掉**; ④ 默认选中当前档位。
        """
        content = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(8))
        tip = Label(text='（重启游戏后隐藏返还率失效，需重新激活）',
                    font_size='14sp', halign='center', valign='middle',
                    color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(28))
        # 这行 22 个汉字要 320px, 而 360dp 机器上只有 312px(系统字体放大更宽) ⇒
        # 原来"定高 28 + text_size=w.size"会把折出来的第二行**裁掉**。折行就长高。
        self._auto_h(tip, dp(28), dp(4))

        sel = [selected]
        btns = {}

        def _restyle():
            for k, b in btns.items():
                b.background_color = hex_rgb(COL_BTN if k == sel[0] else COL_BTN_OFF) + (1,)

        def _pick(k):
            def _go(*_):
                sel[0] = k
                _restyle()
            return _go

        row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(4))
        for key, text in opts:
            b = Button(text=text, font_size='18sp', bold=True, background_normal='',
                       background_down='')
            # Kivy 的 Button **不换行也不缩**: 装不下就**盖到隔壁按钮上**。"关闭隐藏"
            # 4 个汉字在 360dp 机器上只有 75.2dp(18sp 要 72dp, 余量仅 3.2dp) ⇒ 接进单行自适应。
            b._fit_base = float(b.font_size)
            self._install_fit(b)
            b.bind(on_release=_pick(key))
            btns[key] = b
            row.add_widget(b)
        content.add_widget(row)
        # 说明行放在**选项和「确定」之间**(玩家定稿): 先选, 选完往下看到说明, 再看到确定 ——
        # 顺序跟玩家的动作顺序一致。放最上面时它挤在标题下面, 容易被当成"副标题"跳过。
        content.add_widget(tip)
        _restyle()                                   # 建完立刻上默认高亮

        ok_btn = Button(text='确定', font_size='18sp', bold=True, background_normal='',
                        background_down='', background_color=hex_rgb(COL_DARKRED) + (1,),
                        size_hint_y=None, height=dp(52))
        content.add_widget(ok_btn)
        # 尺寸 0.98 / h_dp=265 是**算出来再用截图量过的**(不是拍的)。字号从 16sp 提到 18sp
        # 之后, 四个汉字要 4x18 = 72dp, 而按钮宽 = `hint_w*vw - 24(外壳内边距) - 2*pad -
        # 3*spacing` 再 /4 —— 360dp 机器上 0.92/10/4 只给 68.8dp, 放大字号就会溢出
        # (Kivy 的 Button 没有 text_size, 不换行, 超了直接盖到隔壁按钮上)。改文案/加档位/
        # 改字号都要重算这个。
        popup = self._popup(0.98, 265, title='隐藏返还率', content=content,
                            auto_dismiss=True,          # 点外面关掉且不生效
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            title_size='19sp',
                            separator_color=hex_rgb(COL_DIV) + (1,))

        def _confirm(*_):
            popup.dismiss()
            # ⚠️ 当拍(`_act`): 「确定」这条链接着摘按钮 / 插按钮 / 切档三串界面反响
            #    (rtp_btn_remove / rtp_btn_add / restyle_buttons / redraw), 老版是一口气
            #    同步做完的; 直调会让那排按钮到下一帧才变。
            self._act(self.game.confirm_rtp_unlock, sel[0])

        ok_btn.bind(on_release=_confirm)
        # 绑 on_dismiss(而不是绑按钮): 点外部/系统关掉也能放行 —— 它是防重入闸门的**唯一**
        # 解锁点(闸门本身在 `Game._rtp_popup`)。
        popup.bind(on_dismiss=lambda *_: self.game.rtp_popup_dismissed())
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_easter_popup(self, *_a):
        """彩蛋弹窗: 球跳回发射槽, 按 ×2 结算(返还 2×投注), 点确定才关。

        ⚠️ 跑分期间不弹 —— 防线在 `Game.on_easter_settled`(那里必须走 `easter_finish`
           解锁, 不能只 return, 否则玩家被软锁死)。

        文案与语音用中性正式的措辞(与游戏其余部分一致), 量词统一用「个弹珠」;
        金额报的是**总返还** 2×bet(按确定后余额确实 +2×bet), 净赚仍是 bet。
        """
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        # 三个孩子**全部定高**(size_hint_y=None) ⇒ `content.minimum_height` 就是精确值,
        # 弹窗高度由 `_popup_fit_content` 按它反算。原来正文是**弹性**孩子: 400dp 机器上
        # 它只分到 248px 可用宽, 而最长那句要 253px ⇒ 折成 4 行塞在 56px 的格子里,
        # 末行直接压在「确定」上。
        title = self._fit_line(Label(text="弹珠返回发射槽", bold=True, halign="center",
                                     color=hex_rgb(COL_METER) + (1,),
                                     size_hint_y=None, height=dp(44)), 28)
        # 标点与全 app 一致: 夹在中文之间的逗号用全角「，」(实测 249->256px, 仍放得下)
        msg = Label(text="弹珠未落入倍率槽，已回到发射槽。" + chr(10)
                         + "本局按 ×2 结算，返还 %d 个弹珠。" % (2 * self.game.bet),
                    font_size="16sp", halign="center", valign="middle",
                    color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(48))
        self._auto_h(msg, dp(24), dp(6))
        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        content.add_widget(title)
        content.add_widget(msg)
        content.add_widget(ok_btn)
        # 宽度 0.78 -> 0.90, 内边距 20 -> 16: 正文那两句各 16 个汉字, 16sp 下要 253px。
        # 老参数在 400dp 机器上只剩 248px、在 360dp 上只剩 216px, **必然折行**。
        # ⚠️ `separator_height=0` 不能漏: 这个弹窗的标题在**内容里**, Popup 自带的标题栏
        #    是空的, 但不关分隔条的话那条线还画着 —— 于是它飘在标题上方。
        popup = self._popup(0.90, 280, title="", content=content,
                            auto_dismiss=False,
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            separator_color=hex_rgb(COL_DIV) + (1,),
                            separator_height=0)
        ok_btn.bind(on_release=lambda *_: popup.dismiss())
        # 绑 on_dismiss 而不是按钮: 将来多一条关闭路径(手势/系统)也不会漏掉解锁。
        popup.bind(on_dismiss=self._on_easter_closed)
        # ⚠️ 这一行是**接口兼容**: 老版把弹窗句柄存在 RootWidget 上(`_easter_popup`),
        #    冒烟夹具读的就是它(判"弹过窗没有")。真源(防重入/解锁)在 `Game._easter_popup`
        #    —— 那个**不许清**(见 `Game.easter_finish`), 这里同样不清。
        # ⚠️⚠️ 句柄存在 `_easter_popup_w`, **不叫 `_easter_popup`**(2026-09-19 修): 那个名字
        #    已经被 `Game._easter_popup`(防重入闸)占了, 而两者是**不同种东西**(一个控件
        #    句柄、一个布尔式的闸)。同名两份写在一个对象上迟早读错那一份, 所以这里只留
        #    一个不可赋值的**只读转发属性**(见下面的 `_easter_popup`), "再写一份"在结构上
        #    不可能 —— 与 `root.py` 的 `_bench_running` / `rtp_btns` 同法。
        self._easter_popup_w = popup
        self._popup_fit_content(popup, content)
        popup.open()

    @property
    def _easter_popup(self):
        """老版 `RootWidget._easter_popup` 那个**只读**名字(探针/冒烟夹具读它)。

        ⚠️ 没有 setter ⇒ 写它就是 AttributeError(响亮), 不会静默多出一个状态源。
        """
        return getattr(self, "_easter_popup_w", None)

    def _on_easter_closed(self, *_):
        """弹窗关掉 -> 整段彩蛋结束, 放行 `park_ball`。"""
        self.game.easter_finish()

    def _show_round_end(self, data):
        """本轮游戏结束弹窗: 恭喜文案 + 统计 + 语音播报(玩家点"确定"才重置并关闭)。

        ⚠️ `plays` / `balance` 取事件的**快照**(`Game.show_round_end` 发事件那一刻的值),
           不要去读 `game.round_plays` —— 那是"现在"的值, 而两者中间隔着一帧。
        语音播报在 `Game._play_round_end_voice` 里(它按老版次序发 `sound` 事件), 这里不播。
        """
        content = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(14))
        msg = "本轮游戏 %d 次已结束\n剩余 [color=%s]%d[/color] 个弹珠\n弹珠数量已调整到1000个\n欢迎你再次挑战" % (
            data["plays"], COL_BALL, data["balance"])
        lbl = Label(text=msg, font_size="18sp", halign="center", valign="middle",
                    markup=True, color=hex_rgb(COL_TEXT) + (1,),
                    size_hint_y=None, height=dp(96))
        self._auto_h(lbl, dp(72), dp(8))
        content.add_widget(lbl)
        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        content.add_widget(ok_btn)
        popup = self._popup(0.82, 320, title="本轮游戏结束", content=content,
                            auto_dismiss=False,
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            title_size="19sp",
                            separator_color=hex_rgb(COL_DIV) + (1,))
        popup.open()
        self._popup_fit_content(popup, content)
        # 点"确定"才重置并关闭; 去重闸(`_round_end_ack`)与重置都在 `Game` 里,
        # 关窗由 `round_end_close` 事件驱动(事件是**唯一**的出口, 不在这里 dismiss)。
        self._round_end_popup = popup
        # ⚠️ 当拍(`_act`): 老版 `_auto_reset()` 是 `reset_balance(notify=False)` +
        #    `popup.dismiss()` 一口气同步做完的 —— 直调晚一帧的话, "重置"那一串界面反响
        #    (余额/统计/输入解锁/按钮底色)会比关窗晚一拍落地。
        ok_btn.bind(on_release=lambda *_: self._act(self.game.round_end_confirm))

    def _show_round_settings(self):
        """轮次设定弹窗: 选择 20/50/100 + 最近完成的轮次历史。"""
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12))

        # 每轮次数选择(纵向: 标签一行, 按钮一行, 全自适应防溢出)
        lbl = self._fit_line(Label(text="每轮游戏次数：", halign="left", valign="middle",
                                   color=hex_rgb(COL_SUB) + (1,),
                                   size_hint_y=None, height=dp(28)), 16)
        content.add_widget(lbl)
        sel_box = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        sel_btns = {}
        for val in (20, 50, 100):
            b = Button(text="%d次" % val, font_size="16sp", bold=True,
                       background_normal="", background_down="",
                       color=(1, 1, 1, 1))
            b.bind(on_release=lambda _b, v=val: self._act(self.game.set_max_plays, v))
            sel_btns[val] = b
            sel_box.add_widget(b)
        content.add_widget(sel_box)

        def _refresh_sel():
            for v, b in sel_btns.items():
                b.background_color = hex_rgb(
                    COL_BTN if self.game.max_plays == v else COL_BTN_OFF) + (1,)
        _refresh_sel()
        # 换档的界面反响由 `max_plays` 事件驱动(它还负责"还开着的这个弹窗"的选中高亮)。
        # ⚠️⚠️ **关掉弹窗就把它清掉**(2026-09-19 修): 老版那个高亮刷新是**按次传参**的
        #    (`_set_max_plays(val, sel_btns)`, main.py:20859 —— 只有弹窗内那一支刷),
        #    而这里挂在 `self` 上 ⇒ 不清的话, 弹窗关掉之后**任何**一条 `max_plays` 事件
        #    都会去写**已经摘掉的控件**(目前不可达: 只有一个地方设档位; 但等哪天多一处,
        #    症状就是"改了一个根本不存在的弹窗", 而且不报错)。清了就与老版同寿命。
        self._round_sel_refresh = _refresh_sel

        # 历史记录(ScrollView 可滚动, 最多显示最近 100 条)
        hist_lbl = self._fit_line(Label(text="最近完成的轮次：", halign="left", valign="middle",
                                        color=hex_rgb(COL_SUB) + (1,),
                                        size_hint_y=None, height=dp(28)), 15)
        content.add_widget(hist_lbl)
        # 一行一条, 每条自己**单行自适应**。原来是"一个大 Label 用 \n 拼", 而
        # "最近第1轮  每轮50次  剩 17405 个弹珠" 要 255px、360dp 机器上只有 250px ⇒
        # 每条都折成两行(白占一倍高度, 看着像坏掉了)。
        inner = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        inner.bind(minimum_height=inner.setter("height"))
        _rows = []
        if self.game.round_history:
            for i, r in enumerate(reversed(self.game.round_history[-100:])):
                _rows.append(Label(
                    text="第%d轮  每轮%d次  剩 %d 个弹珠" % (i + 1, r["plays"], r["balance"]),
                    font_size="15sp", halign="left", valign="middle",
                    color=hex_rgb(COL_TEXT) + (0.7,), size_hint_y=None, height=dp(26)))
        else:
            _rows.append(Label(
                text="暂无完成的轮次记录", font_size="15sp", halign="left", valign="middle",
                color=hex_rgb(COL_TEXT) + (0.7,), size_hint_y=None, height=dp(26)))
        for _r in _rows:
            _r.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
            inner.add_widget(_r)
        self._fit_uniform(_rows, sp(15))     # 绝对字号, 必须过 sp()
        scroll = ScrollView(size_hint=(1, 1), bar_width=dp(6))
        scroll.add_widget(inner)
        content.add_widget(scroll)

        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        popup = self._popup(0.84, 500, title="每轮游戏次数设定", content=content,
                            auto_dismiss=True,
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            title_size="19sp",
                            separator_color=hex_rgb(COL_DIV) + (1,))
        ok_btn.bind(on_release=popup.dismiss)
        # ⚠️ 弹窗关掉就把"刷这个弹窗高亮"的口清掉(理由见上面 `_round_sel_refresh` 那段):
        #    不绑这一句, 那个闭包会一直挂在 `self` 上、指着已经摘掉的控件。
        popup.bind(on_dismiss=lambda *_: setattr(self, "_round_sel_refresh", None))
        content.add_widget(ok_btn)
        popup.open()

    def _show_fps_cap_settings(self):
        """帧率上限设定：拖动滑条选档，确认后持久化并立即重申 Android 高刷请求。"""
        from kivy.uix.slider import Slider

        values = FPS_CAP_OPTIONS
        current = (self.game.fps_cap_setting if self.game.fps_cap_setting in values
                   else FPS_CAP_DEFAULT)
        picked = [current]
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        title = self._fit_line(Label(text='帧率上限设定', bold=True, halign='center',
                                     color=hex_rgb(COL_TEXT) + (1,),
                                     size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title)
        value_lbl = Label(text='当前设定：%d Hz' % current, font_size='20sp', bold=True,
                          halign='center', valign='middle', color=hex_rgb(COL_FIRE) + (1,),
                          size_hint_y=None, height=dp(34))
        value_lbl.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
        content.add_widget(value_lbl)
        slider = Slider(min=0, max=len(values) - 1, step=1,
                        value=values.index(current), size_hint_y=None, height=dp(42))
        content.add_widget(slider)
        ticks = BoxLayout(size_hint_y=None, height=dp(20))
        for hz in values:
            tick = Label(text=str(hz), font_size='12sp', halign='center', valign='middle',
                         color=hex_rgb(COL_SUB) + (1,))
            tick.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
            ticks.add_widget(tick)
        content.add_widget(ticks)
        # ⚠️ 文案**保一行 + 字号尽量大**: 「系统全局设定」缩成「系统设定」省 2 字 = 22dp,
        #    腾出来的宽度全换成字号(11sp -> 12sp, 实测 272dp)。弹窗 0.88 -> 0.96:
        #    360dp 屏 290dp / 393dp 屏 321dp ⇒ 一行放得下。
        #    ⚠️ 标点统一半角(`(`, `,`): 全角版 304dp@12sp 会在 360dp 屏折行。
        #    ⚠️ 为什么不再大: 0.96 弹窗下 360dp 屏的字号上限是 12.9sp —— 13sp 要 296dp。
        hint = Label(text='实际帧率上限=min(屏幕支持,系统设定,本窗口设定)',
                     font_size='12sp', halign='center', valign='middle',
                     color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(24))
        hint.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
        content.add_widget(hint)
        actions = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        cancel = Button(text='取消', font_size='16sp', bold=True, background_normal='',
                        background_color=hex_rgb(COL_BTN_OFF) + (1,))
        confirm = Button(text='确定', font_size='16sp', bold=True, background_normal='',
                         background_color=hex_rgb(COL_BTN) + (1,))
        actions.add_widget(cancel)
        actions.add_widget(confirm)
        content.add_widget(actions)
        # ⚠️ 0.96 是配合上面那行文案算的: 11sp 要 271dp 而 0.88 的可用宽只有 261dp。
        popup = self._popup(0.96, 310, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _pick(_slider, value):
            picked[0] = values[int(round(value))]
            value_lbl.text = '当前设定：%d Hz' % picked[0]

        def _confirm(*_):
            # 白名单与非法值都在 `Game.set_fps_cap_setting` 里判(非法值返回 False);
            # `_FPS_USER_CAP[0]` + `_apply_fps_cap()` 由 `fps_cap` 事件做。
            # ⚠️ 当拍(`_act`): 老版 `_set_fps_cap_setting` 就是在这句里同步重申高刷请求的
            #    (main.py:20706), 直调会让新上限晚一帧才生效。
            if self._act(self.game.set_fps_cap_setting, picked[0]):
                self.game_area.center_toast('帧率上限已设为 %d Hz' % picked[0],
                                            hexcolor=COL_GREEN, size=20, life=1.3)
            popup.dismiss()

        slider.bind(value=_pick)
        cancel.bind(on_release=popup.dismiss)
        confirm.bind(on_release=_confirm)
        popup.open()
        self._popup_fit_content(popup, content)

    # ---- 落盘路径(老版是 RootWidget 的 staticmethod; `Game` 只收两个"取路径"的回调) ----
    @staticmethod
    def _history_path():
        """轮次历史 JSON 文件路径(持久化到 user_data_dir, Android 上为应用私有目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_round_history.json")

    @staticmethod
    def _config_path():
        """游戏设定 JSON 文件路径(与轮次历史同目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_config.json")
