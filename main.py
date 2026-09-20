"""跳跳的弹珠机 —— 安卓版入口（老版 `android/main.py` 22400+ 的 `_smoke` / `main`）。

    python main.py              # 开窗玩
    python main.py --nosound    # 静音启动
    python main.py --selftest   # 无界面自测（必跑！）
    python main.py --smoke      # 桌面冒烟：建窗 -> 发射 -> 截图 -> 必中盘 -> 哑火
    python main.py --landscape  # 横屏反旋转模拟（1740x1000）

⚠️ 新版把游戏逻辑抽进了 `danzhu/game.py :: Game`，所以界面壳上的东西要先过 `.game`：
   老版的 `rootw.state` / `rootw.balance` / `rootw.settle(i)` 现在是
   `rootw.game.state` / `rootw.game.balance` / `rootw.game.settle(i)`。
   控件与演出仍在 `rootw` 本身上（`rootw.game_area` 等）—— 那是**壳**，不是逻辑。
"""

import os
import sys

# ⚠️⚠️ 必须在**导入 kivy 之前**引入本包的 boot 模块 —— `danzhu/platform/boot.py` 的 `_BOOT_T0`
#    就是启动日志的 t 轴**原点**, 老版取在 main.py 模块加载的最开头(`android/main.py:42`,
#    比 `from kivy.app import App` 还早 400 多行)。若等到 `danzhu.game` 那条链才把 boot 拖
#    进来, 原点会落到 **kivy 导入之后**(桌面实测晚 642~727 ms) ⇒ 导出日志的 t 值整体偏小,
#    而正文里那句「t 起算于 main.py 模块加载的第一行(比 Kivy 导入还早)」就变成了假话。
#    ⚠️ 这一句安全: `boot` 只 import time, 且 danzhu / danzhu.platform 两个 `__init__.py`
#       都刻意不 re-export 任何子模块。
from danzhu.platform.boot import _boot_log   # noqa: E402

os.environ.setdefault("KIVY_NO_ARGS", "1")   # 自定义参数自己解析, 别让 Kivy 抢
_boot_log("boot", "模块开始加载")              # 与老版同名标记(android/main.py:460)
# 四方向重力感应: 与 buildozer.spec orientation / manifest fullSensor 同值(p4a 会写进
# p4a_env_vars.txt, 这里运行时再设一次防 bootstrap 未带上)。
# ⚠️ 只写竖屏两方向会把 SDL 也锁竖屏, 横拿时系统只能给 letterbox 兼容盒, 而
#    buildozer.spec 放开四方向就会被这里覆盖废掉。
os.environ["KIVY_ORIENTATION"] = "Portrait PortraitUpsideDown Landscape LandscapeUpsideDown"

# 必须在导入 Window 之前配置。所有平台都保留 SDL 的 swap interval: Windows 上关闭它不会
# 绕过桌面合成器, 实测反而把 60Hz 显示器从稳定 60fps 拖成约 50fps。高刷显示器开启 vsync
# 会自然按其刷新率呈现; Android 则由 Window/显示模式请求优先提升到 165Hz。
from kivy.config import Config        # noqa: E402
Config.set("graphics", "maxfps", "120")
Config.set("graphics", "vsync", "-1")   # ⚠️ 实验态(B 组): 自适应 vsync, 测完改回 "1"

import tempfile                       # noqa: E402

from kivy.app import App              # noqa: E402
from kivy.clock import Clock          # noqa: E402
from kivy.core.window import Window   # noqa: E402

_boot_log("boot", "Kivy 导入完成")     # 与老版同名标记(android/main.py:472)

# ⚠️ `CUP_TRIGGER_DELAY` 住在 `danzhu/game.py`(它是**玩法时序**, 不是界面常量)。
from danzhu.game import CUP_TRIGGER_DELAY   # noqa: E402


def _smoke():
    """桌面自动冒烟: 建窗 -> 蓄力发射 -> 截图 -> 必中盘(验证中奖特效) -> 哑火。"""
    from danzhu.ui.app import PlinkoApp

    outdir = os.path.join(tempfile.gettempdir(), "plinko_smoke")
    os.makedirs(outdir, exist_ok=True)
    app = PlinkoApp()

    def shot(name):
        try:
            Window.screenshot(os.path.join(outdir, name))
        except Exception as e:
            print("screenshot fail:", e)

    def s1(dt):
        shot("01_ready.png")
        app.rootw.game.start_charge()

    def s2(dt):
        app.rootw.game.power = 0.85
        shot("02_charging.png")
        app.rootw.game.launch()

    def s3(dt):
        shot("03_flying.png")

    def s4(dt):
        shot("04_after_settle.png")
        r = app.rootw
        if r.game.state == "ready":
            r.game.multipliers = [2, 3, 5, 10, 20, 2, 3, 5, 10]   # 必中盘: 验证中奖特效
            r.game_area._redraw()
            r.game.start_charge()

    def s5(dt):
        r = app.rootw
        r.game.power = 1.0
        r.game.launch()

    def s6(dt):
        shot("05_win_effect.png")

    def s7(dt):
        shot("06_win_done.png")
        r = app.rootw
        # 直接调 settle 定格特效: x20 大奖 -> 大字 + 槽闪 + 灯绿 + 滚分。
        # 注意大字不再立刻出现: 揭晓已挪到"最后一颗球落定"(见 Game._reveal_win)。
        r.game.multipliers = [0, 0, 0, 0, 20, 0, 0, 0, 0]
        r.game_area._redraw()
        r.game.settle(4)

    def s7b(dt):
        shot("06b_fx_bigtext.png")
        app.rootw.game.toggle_mute()         # 静音: 截一张"音效已关"看对比
        shot("06c_mute_off.png")
        app.rootw.game.toggle_mute()         # 恢复

    def s7c(dt):
        # 中奖玻璃杯演出途中(杯子 + 正在下落/堆叠的球)。settle 是 s7, 杯子在 +WINDUP(0.5s)
        # 后出现, 这里取到的是装杯中段。
        shot("06d_cup.png")

    def s8(dt):
        g = app.rootw.game
        print("SMOKE s8: state=%s" % g.state)
        if g.state == "ready":
            g.start_charge()
            g.power = 0.05                 # 哑火
            g.launch()
            print("SMOKE s8 after launch: state=%s" % g.state)

    def s9(dt):
        shot("07_misfire_done.png")
        g = app.rootw.game
        print("SMOKE s9: state=%s balance=%s bet=%s" % (g.state, g.balance, g.bet))
        if g.state == "ready":
            g.balance = 5                    # 余额 < 投注 -> 触发飘字
            g.start_charge()
            print("SMOKE s9 after start_charge: state=%s" % g.state)
        Clock.schedule_once(when_ready(s9b, "s9b"), 0.5)

    def s9b(dt):
        r = app.rootw
        g = r.game
        print("SMOKE s9b: state=%s balance=%s" % (g.state, g.balance))
        shot("08_no_beads.png")
        # 跑分期间不许弹窗/不许播装杯(用户报: 跑分时彩蛋窗打断灰屏)。
        # 顺带守住"绝不软锁"那条红线 —— 彩蛋被拦掉时必须**解锁**, 只 return 不
        # easter_finish 的话 `_easter_hold` 永远不放, 玩家只能杀进程。
        # ⚠️ 先清掉前面某一局**真彩蛋**可能留下的窗/锁: 不清的话下面那条断言测的是
        #    "残留"而不是"跑分拦没拦住"(实测偶发误报: hold=False 却报 FAIL)。
        #    这是冒烟夹具的清理, 不是产品逻辑。
        g._easter_popup = None
        g._easter_hold = False
        g._bench_running = True
        # ⚠️ 先清掉上一局残留的装杯: 装满后要玩家点击才退场, 而冒烟里没有人点 ——
        #    不清的话它停在 result, 下面那条"settle 那一刻 mode 本该是 idle"会被这个
        #    残值误判成 FAIL。
        r.game_area.win_fx._abort()
        g.multipliers = [0] * 9
        g.multipliers[4] = 50
        g.state = "landing"
        g._settled = False
        g.settle(4)                       # 跑分中中奖
        print("SMOKE bench-win: cup=%s reveal=%s busy=%s"
              % (r.game_area.win_fx.mode, g._reveal_done, r.game_area.win_fx.busy()))
        # ⚠️ "落容器"事件的**触发**延后了 CUP_TRIGGER_DELAY 秒, 所以 settle 刚返回这一刻
        #    mode 本来就该是 idle —— 断言必须等过了那一段再看(下面用 Clock 推迟)。
        if r.game_area.win_fx.mode != "idle":
            print("SMOKE-FAIL: 落容器事件不该在触地那一帧就触发(应延后 %.2fs)" % CUP_TRIGGER_DELAY)

        def _cup_then_easter(dt):
            try:
                # ⚠️ 用户定案: 跑分期间**应该**照常播装杯(玩家报"你丢掉了落袋动画",
                #    要求保留)。以前这条断言是"不该播"。
                if r.game_area.win_fx.mode == "idle":
                    print("SMOKE-FAIL: 跑分中应该照常播装杯(用户定案保留落袋动画), 实际已回 idle")
                g.state = "landing"
                g._settled = False
                g._easter_egg = True
                g.settle(4)                       # 跑分中彩蛋
                if g._easter_popup is not None or g._easter_hold:
                    print("SMOKE-FAIL: 跑分中弹了彩蛋窗或软锁住了 (hold=%s)" % g._easter_hold)
            finally:
                g._bench_running = False
                g._easter_hold = False
                g._easter_popup = None
            print("SMOKE-OK state=%s cup=%s -> %s"
                  % (g.state, r.game_area.win_fx.mode, outdir))
            App.get_running_app().stop()

        Clock.schedule_once(_cup_then_easter, CUP_TRIGGER_DELAY + 0.12)

    def when_ready(fn, name, tries=120):
        """等状态机真正回到 ready 再执行, 超时(默认 12s)则打日志后强制执行。

        ⚠️ 中奖玻璃杯演出会锁住输入(见 `_frame` 的 landed 分支), 固定时刻的 s8/s9 会被
        **静默跳过** —— 冒烟照样全绿, 覆盖却没了。所以改成轮询, 并把结果打进日志。
        """
        def poll(dt, left=tries):
            r = app.rootw
            # ⚠️ 装杯装满后要**玩家点击**才退场, 而冒烟里没有人点 —— 所以过半还没等到就
            #    **模拟一次玩家点击**, 走的是同一个 `request_close()`(别在冒烟里另写一份
            #    "点击该干什么")。不这么做的话 s8/s9 会静默走超时分支, 在"杯子还立着"的
            #    状态下截图, 日志里只有一行超时 = **会骗人的绿**。
            if left < tries // 2:
                try:
                    r.game_area.win_fx.request_close()
                except Exception:
                    pass
            if r.game.state == "ready" and not r.game_area.win_fx_busy():
                fn(dt)
                return
            if left <= 0:
                print("SMOKE %s 等 ready 超时: state=%s cup=%s"
                      % (name, r.game.state, r.game_area.win_fx.mode))
                fn(dt)
                return
            Clock.schedule_once(lambda d: poll(d, left - 1), 0.1)
        return poll

    def when_revealed(fn, name, tries=200):
        """等中奖大字真的立起来再截。

        ⚠️ 揭晓挂在"最后一颗球落定"那一刻(×20 约 settle+3.4s), 原来的固定时刻会拍在
        一只还没揭晓的杯子上 —— 文件在、名字在、覆盖没了, 属于**会骗人的绿**。
        """
        def poll(dt, left=tries):
            if app.rootw.game_area._effects or left <= 0:
                if left <= 0:
                    print("SMOKE %s 等大字超时" % name)
                fn(dt)
                return
            Clock.schedule_once(lambda d: poll(d, left - 1), 0.1)
        return poll

    Clock.schedule_once(s1, 1.5)
    Clock.schedule_once(s2, 2.5)
    Clock.schedule_once(s3, 4.0)
    Clock.schedule_once(s4, 8.5)
    Clock.schedule_once(s5, 9.3)
    Clock.schedule_once(s6, 13.6)
    Clock.schedule_once(s7, 15.0)
    Clock.schedule_once(when_revealed(s7b, "s7b"), 15.4)
    Clock.schedule_once(s7c, 16.8)
    Clock.schedule_once(when_ready(s8, "s8"), 17.0)
    Clock.schedule_once(when_ready(s9, "s9"), 20.0)
    app.run()


def main():
    if "--nosound" in sys.argv:
        # ⚠️ 改的是**模块属性**(`PlinkoApp.build` 读的也是它) —— from-import 的名字是拷贝。
        import danzhu.config as _cfg
        _cfg.SOUND_ENABLED = False
    if "--selftest" in sys.argv:
        from danzhu.bench.selftest import selftest
        if not selftest():
            sys.exit(1)
        return
    if "--smoke" in sys.argv:
        _smoke()
        return
    from danzhu.ui.app import PlinkoApp
    PlinkoApp().run()


if __name__ == "__main__":
    main()
