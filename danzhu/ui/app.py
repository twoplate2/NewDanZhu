"""Kivy App —— 建窗、装旋转层、挂加载页、申报方向与刷新率。老版 `android/main.py` 21856-22146。

装配形状（自下而上）:

    App -> LandLayer(横屏反旋转层) -> AnchorLayout(等效盒) -> RootWidget(界面壳)
                                                          -> _LoadVeil(冷启动加载页, 音效没就绪才挂)

⚠️ 横屏反旋转层: 内容在**等效竖屏窗口**里布局, 横拿时整体旋转 90 度铺满横屏, 画面构图与
   竖拿一致。竖屏时透明无感(零回归)。
"""

import sys
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.anchorlayout import AnchorLayout

from ..audio.bus import Sfx
from .. import config as _cfg
from ..config import COL_BG, hex_rgb
from ..platform.boot import _boot_log
from ..platform.device import (platform, _apply_fps_cap, _cfg_warm, _device_is_wide,
                               _guard_immersive_now, _guard_orient_now, _guard_post,
                               _guard_warm, _JNI_STAT, _SYSUI_MODE, _WAKE_MODE)
from .root import RootWidget
from .veil import _LoadVeil
from .widgets import LandLayer


class PlinkoApp(App):

    def build(self):
        _boot_log("boot", "App.build 进入")
        Window.clearcolor = hex_rgb(COL_BG) + (1,)
        # ⚠️ **探针里唯一一条没有 import 期自调的包装**(见 `text._swap_wrap` 的说明): 它会
        #    `import kivy.core.window` = **建真窗口**, 所以不能放在 text.py 的 import 期 ——
        #    老版是在 main.py 模块导入时调的(5557)。必须**晚于窗口建立、早于任何一次 flip**
        #    (Kivy 主循环起来才会 flip), 这里正好。漏了它的后果是**静默**的:
        #    `_FRAME_SWAP` 恒为 0.0, 面板「flip 阻塞了多少毫秒」和「尾·回调 / 尾·空档」恒为空。
        try:
            from .text import _ondraw_wrap, _swap_wrap
            _swap_wrap()
            # ⚠️ `_ondraw_wrap` 与 `_swap_wrap` **同一条规矩**(必须晚于窗口建立、
            #    早于任何一次 flip), 所以挂在同一处。它把「尾·空档」里的
            #    `Window.on_draw`(画布遍历 + GL 提交)单独切出来 —— 见那个函数的说明。
            _ondraw_wrap()
        except Exception:
            pass
        if platform != "android":
            # 桌面预览 9:16; 宽屏可最大化, 内容自适应居中。
            # --landscape: 模拟横屏反旋转(Y700 横窗比例), 验证"画面保持竖拿构图"用。
            Window.size = (1740, 1000) if "--landscape" in sys.argv else (540, 960)
        self.title = "跳跳的弹珠机"
        # 音效库**放后台烘**: 首装要把整库用纯 Python 合成一遍(好几秒)。以前这里是 sync=True,
        # 那是在建 UI **之前**、在主线程上烘完 ⇒ 几秒钟黑屏, 而且 Kivy 主循环被阻塞、
        # 什么都画不出来。现在: 窗口立刻有内容, 由 _LoadVeil 盖住整屏挡输入, 烘完由 _frame 收掉。
        # ⚠️ 读**模块属性**而不是 from-import 的名字: `--nosound` 由 main.py 在
        #    运行期改 `config.SOUND_ENABLED`, 而 `from ... import SOUND_ENABLED`
        #    绑的是一份拷贝 —— 改了不生效, 且静默(玩家只会觉得"--nosound 没用")。
        sfx = Sfx(_cfg.SOUND_ENABLED, start_now=False)
        self.layer = LandLayer()
        anchor = AnchorLayout(anchor_x="center", anchor_y="center")
        anchor.size_hint = (None, None)   # 等效盒尺寸由 LandLayer 全权控制
        self.layer._anchor = anchor
        self.layer.add_widget(anchor)
        self.rootw = RootWidget(sfx=sfx, size_hint_x=None)
        anchor.add_widget(self.rootw)
        # 冷启动加载页: 音效库没烘完就盖住整屏(不盖的话首装前几秒是黑的, 而且能按发射出哑球)。
        # 后 add 的在上层, 所以它就是最上面那层。烘完由 RootWidget._frame 摘掉。
        self.veil = None
        # ⚠️ 这一行是**总闸门**的判据。`Sfx(...)` 是 build 的**第一句**, 它后面隔着整棵界面树的
        #    构建; 若走到这里音频**已经**就绪, 加载页根本不建 —— 那么「启动信息」里那个
        #    「音效等待 1000ms」就跟玩家感知的等待**没有关系**。有了这一行 + 「第一帧 / 摘页」,
        #    一次真机导出就能判死或放行整份优化清单。
        _say = "音效已就绪 ⇒ **不建**加载页" if sfx.audio_ready() else "音效未就绪 ⇒ 建加载页"
        _boot_log("boot", "建 UI 完成: " + _say)
        if not sfx.audio_ready():
            self.veil = _LoadVeil(size_hint=(1, 1))
            anchor.add_widget(self.veil)
            self.rootw._load_veil = self.veil      # 交给 _frame 收尾
            self.rootw._load_veil_host = anchor    # 重放冷启动时要往这里再挂一页
        # 帧率上限 / Android 高刷模式: 必须在起循环前设好。
        try:
            _apply_fps_cap()
        except Exception:
            pass
        self.layer.apply_orientation()
        self.rootw._fit_width()
        self.rootw._apply_sizes()
        if platform == "android":
            # 方向守卫: 启动 1s(SDL 启动序列完成后)按设备分流抢一次话语权, 之后常驻。
            # 0.7s 周期幂等: 宽屏横拿时持续顶掉 SDL 的竖屏自报, 转屏跟随基本即时
            # (窗口变化分支里还有一记立即重申, 双保险)。
            Clock.schedule_once(lambda *_: self._apply_orientation(), 1.0)
            Clock.schedule_interval(self._orient_guard, 0.7)
            # 全屏沉浸: buildozer fullscreen=1 之外的运行时双保险。弹窗/切后台回前台后系统栏
            # 会复活, 与方向守卫同节奏持续重申(幂等)。
            Clock.schedule_once(lambda *_: self._enter_immersive(), 1.0)
            # ⚠️ **周期 2.5s, 不是 0.7s**: 这条的 `setSystemUiVisibility` 跑在 **Java UI 线程**,
            #    每 0.7 秒让窗口重算一次 inset + 走一趟 SurfaceFlinger。敢拉长是因为:
            #    ① 回前台有 `on_resume()` 兜; ② 转屏有 `_frame` 的窗口尺寸轮询立即重申;
            #    ③ IMMERSIVE_STICKY 本身就是"玩家从边缘划出来、几秒后系统自动收回" ——
            #    我们重申的是系统自己就会收的东西, 2.5s 只是"万一没收干净"的保险。
            #    ⚠️ 真机上若发现系统栏赖着不走, 调回 0.7 即可, 代价就是那条尾巴。
            Clock.schedule_interval(self._enter_immersive, 2.5)
            # ⚠️ 守卫与落盘线程**在这里就焐热**, 不能等 prebake_step: 守卫第一次触发在 0.7s,
            #    而预热链第一步在 ~1.6s —— 等它等于让第一次守卫现建线程 + 现
            #    AttachCurrentThread(震动那边踩过同一个坑)。
            try:
                _guard_warm()
            except Exception:
                pass
            try:
                _cfg_warm()
            except Exception:
                pass
        # ⚠️ 烘焙在**这里**起(树建完了、首帧之前) —— 见 `Sfx(start_now=...)` 的说明。
        sfx.start_bake()
        return self.layer

    # ---- 方向策略(按屏幕比例分流) ----
    #   宽屏(16:9 及更宽, 平板): fullSensor 四方向, 横拿时系统给全屏横窗, LandLayer 把画面
    #     反转回竖拿构图铺满(锁竖屏会被 12L+/ZUI 塞 letterbox 半屏盒, app 改不了盒子宽高, 不对抗)。
    #   瘦长手机(<16:9, 如 20.5:9): 锁竖屏 SENSOR_PORTRAIT(7) —— 横拿时系统根本不进横屏,
    #     反旋转层在瘦长机上会撞上横向多出的状态栏而显示坏掉。
    #   横竖切换由 RootWidget._frame 的窗口尺寸轮询驱动(layer.apply_orientation)。
    def _apply_orientation(self):
        """以毒攻毒: SDL 启动/onResume 会按自身 hint 调 setRequestedOrientation, 可能把
        manifest 的 fullSensor 在运行时覆盖成竖屏 -> ZUI 判定"竖屏app"塞半屏盒。这里按设备
        分流重申一次抢回话语权: 宽屏设备抢 FULL_SENSOR(10), 瘦长手机抢 SENSOR_PORTRAIT(7)。"""
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.setRequestedOrientation(10 if _device_is_wide() else 7)
        except Exception:
            pass

    def _orient_guard(self, dt):
        """常驻方向守卫: 按设备分流持续重申方向请求(幂等, 系统无感)。

        ⚠️ 真身已搬到工作线程(见 `_guard_orient_now` / `_guard_post`), 这里只剩一次
           `put_nowait`。逻辑与频率一个字没改。搬走的原因是它是全 app 唯一的**常驻周期性
           主线程 JNI**(每 0.7 秒一次), 而真机跑分里"每帧实算只有 4.7ms、却有一批
           **不分阶段**的慢帧" —— 周期性、跨阶段、纯 CPU, 只有它和沉浸重申两条。
        """
        if platform != "android":
            return
        import time as _t
        _t0 = _t.perf_counter()
        ok = _guard_post("orient")
        if not ok:                       # 队列建不起来 / 满: 退回同步 = 今天的行为
            try:
                _guard_orient_now()
            except Exception:
                _JNI_STAT[5] += 1
        _d = _t.perf_counter() - _t0
        _JNI_STAT[0] += _d
        if _d > _JNI_STAT[1]:
            _JNI_STAT[1] = _d

    # ⚠️ 老版是 `PlinkoApp` 的类属性 + `@classmethod`(见 android/main.py 21983-22074)。
    #    `platform/device.py` 的 `_guard_immersive_now` / `_guard_warm` 两处**只投递不现造**
    #    (工作线程上现造 PythonJavaClass 要注册 Java 代理), 所以它必须住在这里、由启动期预热
    #    先建好 —— 漏了它那两处会 AttributeError, 被各自的 except 吞成"系统栏/常亮静默失效"。
    _immersive_task_inst = None

    @classmethod
    def _immersive_task(cls):
        """构造(并缓存)沉浸 Runnable, 单实例反复投递(防 pyjnius 代理被 GC)。

        ⚠️ `setSystemUiVisibility` 必须在 UI 线程执行: 从 SDL(Python)线程直调, 视图已 attach
           时会被 ViewRootImpl 的线程检查拦下(CalledFromWrongThread), 异常被 except 吞掉后
           无声无息 —— ZUI 真机实测: 竖屏启动期沉浸一直不生效, 转一次屏才"自愈"。
        ⚠️ 构造这一步会注册 Java 代理 ⇒ **只在主线程现造**(见 `_guard_warm` 的启动期预热)。
        """
        if cls._immersive_task_inst is None:
            from jnius import PythonJavaClass, java_method

            class ImmersiveTask(PythonJavaClass):
                __javainterfaces__ = ['java/lang/Runnable']

                @java_method('()V')
                def run(self):
                    # ⚠️ 这一段跑在 **Java UI 线程**上, 不是我们的 Python 线程 —— 它根本不出现在
                    #    `_frame` 的剖析里。而 `setSystemUiVisibility` 会让窗口重算 inset + 走一趟
                    #    SurfaceFlinger, 代价**每台机器差很多** ⇒ 单独计时写进 `_JNI_STAT[6..8]`。
                    try:
                        from jnius import autoclass
                        act = autoclass("org.kivy.android.PythonActivity").mActivity
                        View = autoclass("android.view.View")
                        # ⚠️ `_t0` **必须打在两次 `autoclass` 之后**: 打在它们之前, 量到的就是
                        #    pyjnius 自己的查表开销(毫秒级), 不是 `setSystemUiVisibility` 的代价。
                        _t0 = time.perf_counter()
                        # ⚠️⚠️ **按当前档位选标志位**(玩家 2026-09-16 要的"两档"):
                        #    非沉浸 ⇒ **0**(一个标志都不设 = 系统栏全在);
                        #    真全屏 ⇒ 那 6 位。两档只在这一处区分, 别在别处再抄一份。
                        #    `FULLSCREEN`/`LAYOUT_FULLSCREEN` 管顶部状态栏,
                        #    `HIDE_NAVIGATION`/`LAYOUT_HIDE_NAVIGATION` 管底部导航栏。
                        if _SYSUI_MODE[0]:
                            act.getWindow().getDecorView().setSystemUiVisibility(
                                View.SYSTEM_UI_FLAG_FULLSCREEN
                                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
                        else:
                            # 0 = 清掉全部标志 ⇒ 状态栏与导航栏都恢复默认(可见)。
                            # ⚠️ 这一句是**必须的**: 从真全屏切回来时不主动清就会一直全屏。
                            act.getWindow().getDecorView().setSystemUiVisibility(0)
                        # ---- 防息屏(跑分期间) ------------------------------------------
                        # ⚠️ 用**窗口标志 `FLAG_KEEP_SCREEN_ON`**(不是 WakeLock):
                        #    · **不需要任何权限**(WakeLock 要 `WAKE_LOCK`, 本工程没声明);
                        #    · **随窗口可见性自动失效** ⇒ 不会泄漏成"一直亮着把电吃光"。
                        # ⚠️ 这**不是体验优化, 是正确性**: 屏幕一灭, 安卓会把游戏暂停、Clock
                        #    冻结(`BUILD_APK.md` §3.22)⇒ **6 分钟的高压测试会被打断**, 成绩是假的。
                        # ⚠️ `FLAG_KEEP_SCREEN_ON` 定义在 `WindowManager$LayoutParams`(Java
                        #    嵌套类要用 `$`); 写成 `android.view.WindowManager.FLAG_...` 会抛异常
                        #    并被本 Runnable 的保护层吞掉 ⇒ 表现为"全屏正常但常亮失效"。
                        _w = act.getWindow()
                        _LP = autoclass('android.view.WindowManager$LayoutParams')
                        if _WAKE_MODE[0]:
                            _w.addFlags(_LP.FLAG_KEEP_SCREEN_ON)
                        else:
                            _w.clearFlags(_LP.FLAG_KEEP_SCREEN_ON)
                    except Exception as _exc:
                        # 这里一旦失败, 可能"全屏仍正常而常亮已失效" —— 不能再静默吞掉,
                        # 否则真机上只表现为"偶发息屏"。
                        _JNI_STAT[5] += 1
                        try:
                            print("[system-ui] apply failed: %r" % (_exc,))
                        except Exception:
                            pass
                    try:
                        _d = time.perf_counter() - _t0
                        _JNI_STAT[6] += _d
                        _JNI_STAT[7] += 1
                        if _d > _JNI_STAT[8]:
                            _JNI_STAT[8] = _d
                    except Exception:
                        pass

            cls._immersive_task_inst = ImmersiveTask()
        return cls._immersive_task_inst

    @staticmethod
    def _enter_immersive(*_):
        """**按当前档位重申系统栏**(每 2.5 秒的周期重申走这条路)。

        两档由 `_SYSUI_MODE` 决定: 非沉浸(默认, 玩游戏时) —— 状态栏 + 导航栏**都在**;
        真全屏(跑分黑屏期间) —— 两栏都藏。
        ⚠️ 名字里的 "immersive" 是**历史名**, 现在它只是"重申一次当前档位", 两档共用。
           **要改档位请用 `_set_system_ui()`**, 别在这里改常量。
        ⚠️ **非沉浸档直接 return**: 那一档"该有的样子"就是系统默认值, 没有任何东西需要重申。
        ⚠️ 形参 `*_` 必须保留: schedule_interval 回调会塞 dt 进来, 零参签名在真机上
           启动 0.7s 即 TypeError 闪退(桌面测试测不出)。
        ⚠️ 必须 runOnUiThread: 线程不对时静默失败。
        """
        if platform != "android":
            return
        if not _SYSUI_MODE[0]:
            return
        import time as _t
        _t0 = _t.perf_counter()
        ok = _guard_post("immerse")
        if not ok:
            try:
                _guard_immersive_now()
            except Exception:
                _JNI_STAT[5] += 1
        _d = _t.perf_counter() - _t0
        _JNI_STAT[0] += _d
        if _d > _JNI_STAT[1]:
            _JNI_STAT[1] = _d

    # ---- Android 生命周期 ----
    def on_pause(self):
        try:
            self.rootw.sfx.pause_out()       # 切后台静音(SoundPool.autoPause)
        except Exception:
            pass
        # 设定是**异步落盘**的: 切后台前必须把挂着的那一份等完, 否则切走那一刻的余额/次数
        # 可能还没写下去(下次启动读到旧值)。超时就放弃, 绝不卡住系统回调。
        try:
            from ..platform.device import _cfg_flush
            _cfg_flush()
        except Exception:
            pass
        return True

    def on_resume(self):
        if platform == "android":
            try:
                _apply_fps_cap()        # 屏幕刷新率会随智能刷新率/外接屏变, 回来重算一次
            except Exception:
                pass
            self._apply_orientation()   # 回前台 SDL 会重报方向, 抢回话语权
            self._enter_immersive()     # 回前台系统栏复活, 重新隐藏
        try:
            self.rootw.sfx.resume_out()
        except Exception:
            pass
        return True

    def on_stop(self):
        try:
            self.rootw.sfx.close()
        except Exception:
            pass
        try:
            # 预热是自链式 schedule_once, 退出时可能还挂在 Clock 上(幂等, 没链就无操作)
            Clock.unschedule(self.rootw.game_area.win_fx.prebake_step)
        except Exception:
            pass
        return True


def _brk_install():
    """给几个"大头"挂上子步骤计时 —— 必须在**类都定义完之后**执行。

    只包四类: 板面动态重画 / 重掷盘面 / 自适应字号 / 装杯重画。
    ⚠️ "装杯"是**嵌套在"板面"里面**的(tick_draw -> win_fx.tick -> _redraw), 所以面板上
       这几个数**不能相加**, 只按"谁最大"读。
    ⚠️ 另外四个(`_auto_launch_tick` / `start_charge` / `launch` / `_update_slots`)是补的:
       真机日志里有一个**同形状的怪帧**出现在"待机 -> 蓄力"那一拍, 而 `_frame` 自算只有
       0.03~0.25 毫秒 —— 那一拍会跑的、在 `_frame` **之外**(Clock 回调)的就是它们,
       外加"自算之后到交画面"那一段(记成 `尾`)。
    ⚠️⚠️ **一律包在具体类 `RootWidget` 上, 不包 Mixin**: `_brk_wrap` 在取不到属性时是
       **静默 return**(老版就这样), 而 `_fit1` 住在 `UiMixin`、`park_ball` 住在 `PlayMixin`
       —— 包错 Mixin 不会报错, 只会让那一格恒为 0。所以下面先断言方法确实在类上。
    """
    from ..game import Game
    from .bench import BenchMixin  # noqa: F401  (确保 Mixin 已加载, 方法解析完整)
    from .game_area import GameArea
    from .root import RootWidget
    from .text import _brk_wrap
    from .winfx import WinPileFX

    # ⚠️ 三个 target 的宿主类与老版**不同** —— 老版 `park_ball` / `launch` / `start_charge`
    #    都住在 RootWidget 里(游戏逻辑与界面焊在一起), 拆包后它们是 `Game` 的方法。
    #    新版 `PlayMixin.launch` / `start_charge` 只是**一行转发壳**(`return self._act(...)`),
    #    包在 RootWidget 上只会量到转发那点开销 ⇒ 那一格会恒接近 0, 与老版"量逻辑本体"不等价。
    #    ⇒ 必须包 `Game`。(`_fit1` 在 UiMixin、`_auto_launch_tick` 在 BenchMixin, 但
    #      `getattr(RootWidget, ...)` 走 MRO 取得到, `setattr` 也只落在组合类上 —— 保持原样。)
    targets = (
        (GameArea, "tick_draw", "板面"),
        (Game, "park_ball", "重掷"),
        (RootWidget, "_fit1", "字号"),
        (WinPileFX, "_redraw", "装杯"),
        (Game, "launch", "发射"),
        (RootWidget, "_auto_launch_tick", "自动发"),
        (Game, "start_charge", "起蓄"),
        (GameArea, "_update_slots", "槽面"),
    )
    missing = ["%s.%s" % (c.__name__, a) for c, a, _t in targets
               if getattr(c, a, None) is None]
    if missing:
        raise RuntimeError(
            "子步骤计时挂不上: %s —— 方法名变了或还没端口。"
            "_brk_wrap 取不到属性是**静默 return**, 不报就会让那一格恒为 0。" % (missing,))
    for cls, attr, tag in targets:
        _brk_wrap(cls, attr, tag)


# ⚠️⚠️ **必须模块级调用** —— 漏掉这一行, 上面那 8 格子步骤计时(板面/重掷/字号/装杯/发射/
#     自动发/起蓄/槽面)就**永远恒为 0**, 跑分日志头部的"最慢三帧的子步骤"只剩尾部两格。
#     老版是 8 处 `_brk_wrap(...)` **散在本文件模块级**直接执行的(在 class PlinkoApp 之前);
#     拆包时把它们集中进 `_brk_install()` 却漏了调用点。当时没被发现, 是因为 fx_gates 那条
#     门禁只比对**源码字符串在不在** —— 那个形状有两个洞: ① 注释里出现同样字样就能喂绿它
#     (实测: 一条解释这个弱点的注释把另一条检查喂绿了); ② 函数**定义了没调用**时字符串
#     照样在。⇒ 已把那条门禁改成**运行期**检查(import 本模块后看那 8 个方法是否真被包上)。
_brk_install()
