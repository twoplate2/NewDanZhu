"""RootWidget —— 界面壳（老版 `android/main.py` 13263-21526 那一个 8263 行的单类）。

## 与老版最大的结构差别

老版把「游戏逻辑」和「Kivy 界面」焊在同一个类里。新版劈成两半：

    danzhu/game.py   Game        —— 无头状态机：六态迁移 / 蓄力发射 / 落袋结算 /
                                    余额·投注·轮次 / 存档 / 隐藏返还率。产出事件流。
    本模块           RootWidget  —— 只剩壳：建控件树、把 Game 的事件画出来、
                                    把输入转发给 Game。

所以老版 `_frame` 里那几百行状态机在本模块里**不存在**，只有一句 `game.step(dt)`
加一段事件派发。行为逐位一致，只是换了地方。

按职责再拆成三个 Mixin（都**不定义 `__init__`**，`__init__` 只在本文件）：

    class RootWidget(BenchMixin, PlayMixin, UiMixin, BoxLayout)
                    └ 隐藏跑分工程   └ Game↔UI 适配   └ 布局/字号/控件构建

⚠️ MRO 顺序不是随便排的：Kivy 的 `Widget` 自己也定义 `on_touch_down` 等事件方法，
   谁排在前面谁生效。输入锁 `on_touch_down` 在 `PlayMixin`，而 `BenchMixin` 在它之前
   —— 所以 `BenchMixin` 里**不许**出现同名的事件方法，否则会静默盖掉输入锁。

## 装配顺序（三处不能换）

1. 先建 `Game`（`fx=None`）—— 因为 `_build_ui` 要读 `game.max_plays` / `game.balance`；
2. 再 `_build_ui()` —— `GameArea` 在这里建出来，`win_fx` 才有；
3. `game.set_fx(game_area.win_fx)` 晚绑，然后**排空一次初始事件**。

老版那段 `set_bet/set_rtp/park_ball` 在 `Game.__init__` 里就跑完了，而它们的**界面效果**
必须等控件树存在之后才画得出来 —— 所以第 3 步那次排空不是可选的，漏了界面就会停在
「还没恢复上次设定」的样子。
"""

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.core.text import Label as CoreLabel
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout

from ..audio.bus import Sfx
from .. import config as _cfg
from ..config import FRAME_TICK_DT, _GLYPH_FONT
from ..game import Game
from ..platform import device as _dev
from .ui_base import UiMixin
from .play import PlayMixin
from .bench import BenchMixin


class RootWidget(BenchMixin, PlayMixin, UiMixin, BoxLayout):
    """界面壳：控件树 + 事件派发 + 输入转发。游戏逻辑全在 `self.game`。"""

    def __init__(self, sfx=None, **kw):
        super().__init__(orientation="vertical", spacing=dp(10), **kw)
        # ⚠️ 走 `_cfg.SOUND_ENABLED`(运行期读模块属性), **不 from-import** ——
        #    from-import 绑的是拷贝, 运行期改 `config.SOUND_ENABLED`(录制台就靠这个把
        #    烘焙关掉)换不动它。`--nosound` 与 `app.py` 那两处都是同一条规矩。
        self.sfx = sfx if sfx is not None else Sfx(_cfg.SOUND_ENABLED)
        # 建 Game。⚠️ 路径/回调/时长/墙钟这五样是 Game 的**全部**注入点, 一个都不能漏 ——
        # 漏掉哪个, 那条路就偷偷接上真机环境。`wall_clock` 不给 = `time.time`, 录制台
        # 靠改 `danzhu.game` 模块命名空间里的 `time` 来接管(与老版同一条路)。
        self.game = Game(
            fx=None,                                  # 晚绑, 见 set_fx
            # ⚠️⚠️ 传的是**方法本身**, 不是 `self._config_path()` 的返回值。`Game` 里存下来
            #    之后是 `self._config_path()`, 传字符串进去会 TypeError —— 而读档/落盘两侧
            #    都有 try/except 兜着, 于是**静默变成"设置永不落盘、档位余额不恢复"**,
            #    与老版直接是两种行为, 且不报错。录制台也是往这个方法上打补丁的。
            config_path=self._config_path,
            history_path=self._history_path,
            on_easter_popup=self._show_easter_popup,
            on_rtp_popup=self._ask_unlock_rtp,
            voice_duration=self.sfx.voice_duration,
            # ⚠️ 落盘走**平台的工作线程**(老版就是 `_cfg_post`)。不给这个口的话
            #    `Game` 会在主线程同步写盘 —— 切投注/切档/结算那一帧各一次,
            #    实测桌面 `build()` 里两次就 114ms(老版 8ms), 真机上更贵。
            # ⚠️ 走 **lambda 转发**, 不 `from ... import _cfg_post`: from-import 绑的是
            #    一份拷贝, 运行期换掉 `device._cfg_post`(测试桩/诊断)就换不动它 ——
            #    这正是 `config.SOUND_ENABLED` 那里踩过的同一个坑。
            save_config=lambda _c, _p: _dev._cfg_post(_c, _p),
            # ⚠️ 撞钉音的第一道闸读**音效层本体**(老版 `Sfx.impact` 首句, main.py:7044),
            #    不是 `Game.sfx_enabled` —— 两者在"三级音频后端全建不起来"的设备上会分叉
            #    (`Sfx.__init__` 把 `enabled` 置 False 而 `Game` 那份仍是 True)。
            #    不分叉时(常态)两条路返回同一个值, 所以对账逐位不变。
            sound_gate=lambda: self.sfx.enabled,
        )
        self._build_ui()
        # ⚠️ 窗口尺寸轮询的快照。`bind(size)` 对**程序启动期**的 resize 不可靠, 所以老版
        #    靠每帧比对这个值来发现窗口变化。初值必须是 `None`: 首帧 `ws != None` 恒真 ⇒
        #    一定会跑一次尺寸重排(那时窗口未必是最终尺寸)。
        self._last_win_size = None
        self.game.set_fx(self.game_area.win_fx)
        # ⚠️ 排空初始事件: `Game.__init__` 里的 set_bet / set_rtp / park_ball 已经把界面效果
        #    发出来了, 而那一刻控件树还不存在。不排空的话界面会停在"出厂样子"。
        self._dispatch(self.game.take_events())
        Window.bind(on_key_down=self._on_key_down, on_key_up=self._on_key_up)
        Window.bind(on_touch_down=self._on_title_touch_down,
                    on_touch_up=self._on_title_touch_up)
        # ⚠️ **不在这里另建 `self._bench_running`** —— `Game` 已经有一份, 而老版是**同一个**
        #    字段(`Game` 的 launch / park_ball / settle / show_easter_popup / play_win 五处
        #    读它)。各建一份的后果是: 只置 UI 那份 ⇒ `Game.park_ball` 照旧重掷盘面,
        #    跑分的"钉盘面"与"跑完还原"全线失效。一律走 `self.game._bench_running`。
        # ⚠️⚠️ 跑分历史的两条列表**必须在这里建**(老版 13314/13318 就在 `__init__` 里):
        #    三个 Mixin 都不定义 `__init__`, 而 `_bench_done` 的
        #    `self.bench_history.append(...)` **不在任何 try 里** —— 少这两行, 跑完一次
        #    性能测试就在 append 处 AttributeError, 把后面整段收尾(停进度 / 解输入锁 /
        #    还原盘面 / 还随机流 / 弹结果窗 / 清 `game._bench_running`)**全部跳过**,
        #    游戏永久停在跑分模式(盘面钉死、发射走确定流、不装杯不中奖), 而四个历史入口
        #    (查看/清空 x 2)一按就炸。`_hp_done` 那条有 try 兜着, 症状轻(静默丢记录)但同源。
        # ⚠️ `_load_*` 紧跟其后(老版 13320/13321 同址): 只建列表不读盘 = 历史**只写不读**,
        #    重启后表恒空。
        self.bench_history = []
        self.hp_history = []
        self._load_bench_history()
        self._load_hp_history()
        # 跑分置灰层: 第2轮物理benchmark时全屏置灰(半透明深色矩形盖住整个界面含游戏区)。
        # ⚠️ 它是**第三套**压暗常数, 与 DIM_ALPHA/HUD_ALPHA 不是一套。两者不会同屏:
        #    跑分要长按标题 3 秒才起, 而装杯期输入是锁的(`_bench_running` 还会直接拦住
        #    play_win)。真要改其中一个, 记得它们互不影响。
        # ⚠️ 它挂在 `RootWidget.canvas.after` = 盖住**整棵子树**(含 GameArea 里的中奖大字),
        #    所以不能拿它做装杯期的 HUD 压暗 —— 那会把大字一起压掉。
        self._bench_dim_shown = False
        with self.canvas.after:
            self._bench_dim_col = Color(0.05, 0.06, 0.09, 0.0)
            self._bench_dim_rect = Rectangle(pos=(0, 0), size=(0, 0))
            # ⚠️⚠️ **黑屏上的白字层必须画在矩形之后** —— 同一个 `canvas.after` 里,
            #    **顺序即层序**。不能用盘面里现成的 `_bench_badge`: `RootWidget.canvas.after`
            #    盖住整棵子树(含 GameArea 里的 badge), 黑屏会把那行红字一起盖掉。
            #    ⚠️ 重烘只在**文字真变了**时做(见 `_set_bench_msg`): 一次 refresh 要重排文字,
            #       而跑分进度一共才变 5 次。
            self._bench_msg_col = Color(1, 1, 1, 0.0)
            self._bench_msg_lbl = CoreLabel(text="", font_size=sp(26), bold=True,
                                            font_name=_GLYPH_FONT)
            self._bench_msg_lbl.refresh()
            self._bench_msg_rect = Rectangle(texture=self._bench_msg_lbl.texture,
                                             pos=(0, 0), size=(0, 0))
        self.bind(size=self._relayout_bench_dim, pos=self._relayout_bench_dim)
        # ⚠️⚠️ **试过 `Clock.interupt_next_only = True`, 实测无效, 已撤 —— 别再试一遍。**
        #
        # 它管的是 `_check_ready` 里两个"吞掉睡眠"的地方 (`kivy/clock.py:860-868/920-933`):
        # 打开后 `sleeptime` 不再被「下一个事件的到期时间」封顶, 且普通定时事件不再取消睡眠。
        # **动机是对的** —— 165Hz 面板 + cap=120 下应用会从"Kivy 自己睡"(周期 ≈6.1ms)
        # 掉进"被 vsync 钉住"(≈6.06ms), 两个模式的 `1%Low` 差 6%。
        #
        # **但 2026-09-20 实测它不解决问题, 而且有代价**(同台子同档位各 7~8 轮):
        #     · 「模式 A 的比例」看着从 4/8 升到 5/7 —— 那是**中位数造成的假象**:
        #       翻转的轮次(B→A 或 A→B)都被算成 A, 真正**稳定**待在 A 的只有 2/7
        #     · **A 组的实际分数反而从 119.3 掉到 114.9(−4%)** —— 普通 Clock 事件要等
        #       睡眠结束才跑, 代价落在这里
        #     · A→B 翻转**照旧发生**(3/7)
        # ⇒ 判据要取「**稳定 A 的比例**」和「A 组的分数」, **不能取"按中位分类的比例"**。
        Clock.schedule_interval(self._frame_timed, FRAME_TICK_DT)
        # 中奖杯的球纹理/球堆预热: 分帧摊在启动后做, 别等中奖那一刻现算(低端机单档
        # d=128 纯 Python 合成要 100~200ms, 一次做完就是几个长帧, 而这动画的全部意义
        # 就是丝滑)。排在 _frame 之后, 不影响冷启动的建界面/烘音效。
        Clock.schedule_once(self.game_area.win_fx.prebake_step, 0.05)

    # ---------------------------------------------------------------- 转发属性
    # ⚠️ `_bench_running` **只有一份, 住在 `Game` 上**。老版它是 RootWidget 的字段, 而它的
    #    五个读者(launch / park_ball / settle / show_easter_popup / play_win)全在游戏逻辑里
    #    —— 拆出来之后天然属于 Game。UI 侧(置灰层、`_sync_hud_dim`、跑分链)以前读写
    #    `self._bench_running`, 若各建一份: 只置 UI 那份 ⇒ `Game.park_ball` 照旧重掷盘面,
    #    跑分的"钉盘面 / 跑完还原"全线失效, 而**不报错**。
    #    用属性转发, 是为了让"再写一份"在结构上不可能 —— 读和写都落到 Game 那一份。
    @property
    def _bench_running(self):
        return self.game._bench_running

    @_bench_running.setter
    def _bench_running(self, v):
        self.game._bench_running = bool(v)

    # ⚠️ `rtp_btns` **不是**第二份字典 —— 它就是 `Game.rtp_btns`: 键 = "有哪些档"的真源
    #    (Game 维护), 值 = 控件句柄(UI 填)。转发而不是另建一份, 是为了让"两份键漂移"
    #    在结构上不可能(只删 children 不删键 ⇒ 按钮关不掉; 只删键不删 children ⇒ 按钮
    #    加不回来)。老版这是 RootWidget 自己的一个字典, 新版把它挂在 Game 上是因为
    #    "能不能切这一档"是**游戏规则**, 与控件无关。
    @property
    def rtp_btns(self):
        return self.game.rtp_btns

    # ⚠️ `bet` **只有一份, 住在 `Game` 上**(`Game.bet`), 老版它是 RootWidget 的字段
    #    (main.py:13283 `self.bet = DEFAULT_BET`)。
    #    外部读者读的是 `GameArea.game.bet` —— 而 `area.game` 是**宿主**(本类, 见
    #    `GameArea.__init__` 的说明), 所以这条转发必须存在, 否则那些读者 `getattr(...,'bet',
    #    None)` 会**静默**拿到 `None`: ① `winfx.prebake_step` 的 `_build_texwarm(rw, bet)`
    #    按 `PRESETS[0]`(=1) 烘「中奖! +N (xM)」那 7 条, 而不是玩家当前档位;
    #    ② 装杯球纹理的预热顺序从"当前档"变成 `DEFAULT_BET`(=10) —— 两处都**不报错**,
    #    只是"预热白做一半"(玩家第一次按自己档位中奖时现烘一段大字, 真机 4~10.7ms)。
    #    ⚠️ 只读: 写 `bet` 是 `Game.set_bet` 的事 —— 给 setter 就等于允许在 UI 侧再写一份,
    #    而那正是这条转发要消灭的东西(写它会 AttributeError, 响亮)。
    @property
    def bet(self):
        return self.game.bet
