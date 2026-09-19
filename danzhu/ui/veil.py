"""冷启动加载页 —— 盖住整屏 + 吞掉所有触摸 + 开场那六个字的渐变动画。

老版 `android/main.py` 21526-21856。

为什么要有这一页: 音效库首装要用纯 Python 现场合成一遍(好几秒)。老版是 `Sfx(sync=True)`,
在建 UI **之前**于主线程烘完 —— 玩家看到的是几秒钟的**黑屏**(窗口已经在, 控件一个都没有),
而那几秒 Kivy 主循环被阻塞, 什么都画不出来。现在改成后台烘焙 + 这一页盖住。

⚠️ 触摸必须吞掉: 不吞的话 state 还是 ready, 玩家能按发射 —— 球飞出去了音效却没就绪,
   又是一次"没声音", 正是换掉 sync=True 想避免的那件事。
⚠️ 必须是 `Widget` 而不是"只画个矩形": 矩形不吞触摸, 挡不住下面那层。
⚠️ 它是整个 App 最早出现的东西, **只依赖 sp()/hex_rgb()/COL_BG**, 不碰任何游戏状态。
"""

import time

from kivy.graphics import Color, Rectangle, StencilPop, StencilPush, StencilUnUse, StencilUse
from kivy.metrics import sp
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ..config import (COL_BG, COL_SUB, REPLAY_BAKE_MAX_SEC, VEIL_BG, VEIL_TITLE,
                      VEIL_TITLE_COLORS, VEIL_TITLE_IN_SEC, VEIL_TITLE_LEAD,
                      VEIL_TITLE_MAX_SEC, VEIL_TITLE_MIN_SEC, VEIL_TITLE_ROUND_GAP,
                      VEIL_TITLE_SWEEP, hex_rgb)
from ..platform.boot import _BOOT_T0, _boot_log


def _veil_title_round_len():
    """一轮(**先停一下** -> 填充线从左扫到右 -> 再停一下)有多长。"""
    return VEIL_TITLE_LEAD + VEIL_TITLE_SWEEP + VEIL_TITLE_ROUND_GAP


def _veil_title_state(t):
    """开场动画在"这一页开了 t 秒"时, 六个字各自的状态 —— **纯函数**(探针直接钉它)。

    效果是 **KTV 歌词**那一种: 六个字一上来就都在(暗色), 然后一个**圆点**从左往右走,
    **圆点左边的字是这一轮的新颜色、右边还是旧的**; 走到头再从左边重来一轮,
    而**每一轮的新颜色比上一轮更亮更冷**(= "从高级渲染变成超高级渲染")。

    返回 `(lit, fill, grade, rl)`:
      · `lit`   = 整行**一起**淡入的进度(0->1, 只走一次);
      · `fill`  = 填充线扫到**整行的百分之几**(本轮内 0->1 单调)。是**行级**而不是逐字级,
                  所以它能停在某个字的中间 —— 那个字左半边新色、右半边旧色;
      · `grade` = 第几轮(0 起), 调用方拿它去 `VEIL_TITLE_COLORS` 取这一轮的颜色;
      · `rl`    = 一轮多长(秒), 顺手带出来给调用方/探针用。
    """
    rl = _veil_title_round_len()
    try:
        k = int(t / rl) if t > 0.0 else 0
    except Exception:
        k = 0
    u = t / VEIL_TITLE_IN_SEC
    lit = 0.0 if u <= 0.0 else (1.0 if u >= 1.0 else u)
    lit = lit * lit * (3.0 - 2.0 * lit)                  # smoothstep
    p = (t - k * rl - VEIL_TITLE_LEAD) / VEIL_TITLE_SWEEP
    p = 0.0 if p <= 0.0 else (1.0 if p >= 1.0 else p)
    p = p * p * (3.0 - 2.0 * p)                          # smoothstep
    return lit, p, k, rl


class _LoadVeil(Widget):
    """冷启动加载页。`tick()` 返回 True = 这一页可以摘了。

    两条路径共用这一个类:
      · **普通启动** —— 印游戏名那六个大字, 走 KTV 动画; 到点整页消失(**零淡出**)。
      · **重放冷启动** —— 带 `text` 进来(只当标记用, 一个字都不显示), 同样演那一行字,
        但不自动摘: 演完之后摆着结果等玩家点一下(靠 `_hold`)。
    """

    def __init__(self, text="", **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*hex_rgb(VEIL_BG) + (1,))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        # 贴底那行状态字: **只服务「重放冷启动」**(`set_done` 往里写「测试已经完成」)。
        # ⚠️ 普通启动路径**绝不写它** —— 那一页只有游戏名那六个大字, 底下什么都没有。
        #    这里原来印的是实时诊断(「已加载 42 / 97 · 已用 1.0 秒」), 那种数在「启动信息」里
        #    全都有而且更全, 不需要在启动页上再占一行。
        self._sub = Label(text="", font_size=sp(17),
                          color=hex_rgb(COL_SUB) + (1,),
                          halign="center", valign="middle")
        # ⚠️ `_hold`: 「重放冷启动」完成时置真 —— 那一屏**不自动摘**, 摆出结果等玩家点一下。
        #    玩家反馈: 「成功之后没有暂停, 直接回去了, 我啥都没有看清」。PC 上烘焙只要 1.2 秒、
        #    探针一过就摘页, 那几行数字等于闪一下。普通冷启动**不用**它(那里玩家要的是赶紧进游戏)。
        self._hold = False
        self._on_tap = None
        # ---- 开场那六个字(只有**普通启动页**有; 「重放冷启动」那页带文字进来, 不参与动画) ----
        self._title_on = False
        self._title_box = (0.0, 0.0, 0.0, 0.0)   # 那一行字的外接框(x, y, w, h), 裁剪按它算
        self._title_fs = 0.0                     # 这一页当前的字号(圆点大小按它算)
        # 第一帧的**启动时钟**读数(秒, 相对 `_BOOT_T0`)—— 面板那行「无音频加载累计耗时」
        # 要拿它 + `VEIL_TITLE_MIN_SEC` 算"加载页自己的地板"。
        # ⚠️ `_t0` 用的是 `time.time()`(给动画算 t), 与启动时钟**不是同一个源** ⇒ 必须另盖一个。
        self._t0_boot = 0.0
        # **加载页"演完了"的那一刻**(启动时钟) —— 第一个满足 `t >= VEIL_TITLE_MIN_SEC` 的 tick
        # 上盖章。⚠️ 为什么不直接拿常量算: 那是"220ms 向上取整到下一帧", 而帧长随设备/负载变
        # (冷启动时主线程还在建界面+跑预热) ⇒ 实测值每局不同。
        self._done_at_boot = 0.0
        # 动画起点 —— ⚠️ **第一帧才盖章**: 构造时刻这一页还没上屏(安卓要等 presplash 撤掉),
        # 从那里算会让动画"没开始就过半"。
        self._t0 = 0.0
        self.add_widget(self._sub)
        self._is_replay = bool(text)
        self._build_title()
        self.bind(pos=self._sync, size=self._sync)
        self._sub.bind(texture_size=self._sync)
        self._sync()

    def _build_title(self):
        """建那一行字 —— **两个字叠着 + 一条裁剪**。

        底下那层是这一轮的"暗色"(整行都在), 上面那层是"亮色", 但只**裁剪出圆点左边那一段**
        露出来。圆点走到哪儿, 哪儿就换成新颜色 —— 粒度是**像素级**的, 可以切在某个字的中间。

        ⚠️ 字号**只在 `_sync` 里改**(窗口尺寸变了才动): 逐帧改字号会让 Kivy 每帧重烘文字纹理。
           逐帧动的只有 `color` 和那条裁剪矩形的宽度。
        """
        try:
            self._dim_lb = Label(text=VEIL_TITLE, color=(0.0, 0.0, 0.0, 0.0),
                                 size_hint=(None, None))
            self._hi_lb = Label(text=VEIL_TITLE, color=(0.0, 0.0, 0.0, 0.0),
                                size_hint=(None, None))
            self.add_widget(self._dim_lb)
            self.add_widget(self._hi_lb)          # 亮色那层压在上面
            # 裁剪 = 从整行左边缘到圆点的一条矩形。圆点右边的亮色被裁掉, 露出底下的暗色。
            with self._hi_lb.canvas.before:
                StencilPush()
                self._clip_a = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
                StencilUse()
            with self._hi_lb.canvas.after:
                StencilUnUse()
                self._clip_b = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
                StencilPop()
            self._dim_lb.bind(texture_size=self._sync)
            self._title_on = True
        except Exception:
            self._title_on = False    # 纯装饰: 建不出来就退化成"只有底色", 绝不把启动带崩

    def set_done(self, extra=""):
        """「重放冷启动」跑完了: 只在**最下面**亮出「测试已经完成」(+ 可选的第二行)。

        ⚠️ 玩家定稿: 「重放冷启动界面不应该显示各种文字, 只显示 跳跳的弹珠机 和 最下面的
           测试已经完成(**如果没有完成, 就不显示**)」—— 所以这一行只在完成时才出现,
           那些统计数挪去了点击之后的弹窗(`BenchMixin._show_replay_detail`)。
        ⚠️ `extra`(耗时那行)是**回车拼上去**的, 不另开一行控件。
        """
        try:
            self._sub.text = "测试已经完成" + (("\n" + extra) if extra else "")
            self._sub.font_size = sp(22)
        except Exception:
            pass

    def drop(self):
        """把这一页摘掉(幂等)。"""
        try:
            if self.parent is not None:
                self.parent.remove_widget(self)
        except Exception:
            pass

    def _apply(self, t):
        """把这一帧该有的颜色写下去(唯一的写入口)。

        ⚠️ 这一页**没有淡出、也不该有**: 到点就整页摘掉, 底下直接是游戏。玩家报的 bug:
           「游戏界面有一个发光效果(原来的启动界面 loading 界面的, 跳跳的弹珠机的文字的
           **覆盖发光**), 没有立即消失, 持续了0.x秒。**这里应该是啥都看不见, 消失得干干净净**」
           —— 字压在游戏画面上这件事, 一帧都不能有。
        ⚠️ **只改 `color`**, 绝不碰 `font_size`(那会每帧重烘文字纹理, 手机上掉帧),
           也不加描边/阴影/辉光。
        """
        if not self._title_on:
            return
        lit, fill, grade, _rl = _veil_title_state(t)
        # 底下那层 = **上一轮**的颜色(起点是**银色**), 上面那层 = 这一轮要扫成的颜色。
        # 圈次**取模**不是夹断 —— 三种颜色循环, 玩家要的就是"无限演"。
        _n_col = len(VEIL_TITLE_COLORS)
        gi = grade % _n_col
        dim_rgb = VEIL_TITLE_COLORS[gi]
        hi_rgb = VEIL_TITLE_COLORS[(gi + 1) % _n_col]
        a = lit
        # ⚠️ **只在颜色真的变了才赋值**: Kivy 的 `Label.color` 一变就要重烘文字纹理(6 个汉字在
        #    手机上不便宜)。这里一轮才换一次色, 每帧赋值是白烧。alpha 只在开头那 0.22s 的
        #    淡入里逐帧变, 很短, 无所谓。
        _want = (dim_rgb, hi_rgb, round(a, 3))
        if _want != getattr(self, "_last_col", None):
            self._last_col = _want
            self._dim_lb.color = (dim_rgb[0], dim_rgb[1], dim_rgb[2], a)
            self._hi_lb.color = (hi_rgb[0], hi_rgb[1], hi_rgb[2], a)
        x0, y0, w, h = self._title_box
        cut = max(x0, min(x0 + w, x0 + w * fill))
        sw = cut - x0
        for _r in (self._clip_a, self._clip_b):
            _r.pos = (x0, y0)
            _r.size = (sw, h)

    def tick(self, audio_ready=False):
        """每帧推进。由 `RootWidget._frame` 调 —— 自己不持有 Clock(切后台回来直接跳终态)。

        返回 **True = 这一页可以摘了**:
          · 普通启动页: 那行字**完整淡入**(`VEIL_TITLE_MIN_SEC`) + 音效就绪 -> **立刻摘**(零淡出);
          · 「重放冷启动」页: 音效就绪即 True; 摘不摘由 `_frame` 按 `_hold` 决定。

        ⚠️ **到点就整页消失, 没有任何淡出**。
        ⚠️ 出任何意外一律返回 True 放行: 绝不能因为动画把玩家卡在启动页(项目红线: 绝不软锁)。
        """
        try:
            now = time.time()
            if self._t0 == 0.0:
                self._t0 = now
                try:
                    self._t0_boot = time.perf_counter() - _BOOT_T0
                except Exception:
                    self._t0_boot = 0.0
            t = now - self._t0
            self._apply(t)
            # "这一页演完了" = 第一次 `t >= VEIL_TITLE_MIN_SEC` 的那一帧(**实测**)。
            # 它是"无音频加载累计耗时"里的那个地板 —— 不能拿常量去算。
            if self._done_at_boot == 0.0 and t >= VEIL_TITLE_MIN_SEC:
                try:
                    self._done_at_boot = time.perf_counter() - _BOOT_T0
                except Exception:
                    self._done_at_boot = 0.0
            if self._is_replay:
                return bool(audio_ready)
            return bool(audio_ready) and t >= VEIL_TITLE_MIN_SEC
        except Exception:
            return True

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        # 底部那行状态字(只有「重放冷启动」用)。
        self._sub.text_size = (self.width, None)
        sh = max(sp(24), self._sub.texture_size[1] + sp(8))
        self._sub.size = (self.width, sh)
        self._sub.pos = (self.x, self.y + sp(30))        # **贴最下面**
        # 那一行字的排版。字号跟着**这一页的宽度**走, 所以是裸数值(不是 sp()) ——
        # 标题要占满宽度, 与屏幕密度无关; 横向 n 个字 ≈ n x 字号宽, 所以取 0.86 / n
        # 再拿高度 0.17 兜一道(别撑出屏)。
        # ⚠️ 标题**永远在正中**: 两块一上一下, 不需要"有文字就把标题顶上去"那套算术。
        if self._title_on:
            n = max(1, len(VEIL_TITLE))
            fs = min(self.width * 0.86 / n, self.height * 0.17)
            self._title_fs = fs
            for lb in (self._dim_lb, self._hi_lb):
                lb.font_size = fs
                lb.text_size = (None, None)           # 单行, 不折行
            # ⚠️ 两层必须**逐像素同尺寸同位置**, 否则裁剪出来的边会和字错位。
            #    尺寸取两层的最大值(理论上一样, 取 max 是防字体回退那类意外)。
            w = max(self._dim_lb.texture_size[0], self._hi_lb.texture_size[0], fs)
            h = max(self._dim_lb.texture_size[1], self._hi_lb.texture_size[1], fs)
            x0 = self.center_x - w / 2.0
            y0 = self.center_y - h / 2.0
            for lb in (self._dim_lb, self._hi_lb):
                lb.size = (w, h)
                lb.pos = (x0, y0)
            self._title_box = (x0, y0, w, h)
        # ⚠️ 这里**绝不能调 `tick()`**: 它会用 `time.time()` 给动画盖章 `_t0`, 而 `_sync` 在
        #    `__init__` 结尾就会跑一次(那时这一页还没上屏) ⇒ 动画"没开始就过半"。

    def on_touch_down(self, touch):
        if not getattr(self, "_first_touch_logged", False):
            self._first_touch_logged = True
            _boot_log("frame", "玩家首次触摸")
        # 普通加载页: 吞掉所有触摸(不让玩家在音效没就绪时按发射)。
        # ⚠️ 但「重放冷启动完成」那一屏**必须能点掉** —— 它是个结果页, 不点掉就永远停在那儿,
        #    而"永远停着"正是项目红线(绝不软锁)最怕的形状。所以只在这一屏放行。
        if self._hold and self._on_tap is not None:
            try:
                self._on_tap()
            except Exception:
                pass
        return True

    def on_touch_move(self, touch):
        return True

    def on_touch_up(self, touch):
        return True
