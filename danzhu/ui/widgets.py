"""五个自绘控件 + 横屏反旋转层。

LandLayer / RotPopup / FpsCurve / GlyphLabel / SpeedCurve，以及曲线轴用的
`_axis_nice_step` / `_med5` / `_curve_axis_range`。老版 `android/main.py`
11394-12099、12846-13127。

⚠️ LandLayer 是横屏反旋转的坐标变换层：`to_local` / `to_parent` / `_to_eq` / `_to_win`
   的语义**不许改方向** —— 全树只有它带旋转，覆写反了就是"横屏所有按钮点不中"。
"""

import math
import sys

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import (Color, Line, PopMatrix, PushMatrix, Rectangle, Rotate,
                           RoundedRectangle)
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.utils import platform

from ..config import (COL_BALL, COL_BTN_OFF, COL_FIRE, COL_GRAY, COL_SUB,
                      hex_rgb)
from ..platform.device import _device_is_wide
from .text import (_GLYPH_ATLAS, _GLYPH_HIT, _GLYPH_MAX_CHARS, _GLYPH_MISS,
                   _POPUP_N, _glyph_key, _glyph_quads, _glyph_rec)


def _land_angle():
    """横屏渲染旋转角(度, Kivy Rotate 逆时针为正): 1(ROTATION_90)->+90, 3->-90,
    其余/读不到(桌面)固定 +90。"""
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        wm = act.getWindowManager()
        disp = wm.getDefaultDisplay()
        return -90 if disp.getRotation() == 3 else 90
    except Exception:
        return 90


def _land_layer():
    app = App.get_running_app()
    return getattr(app, "layer", None)


class LandLayer(FloatLayout):
    """横屏反旋转层: 把整棵 UI 树按"等效竖屏窗口"(短边x长边)布局后整体旋转 90 度铺满
    横屏。竖屏时 angle=0 且层尺寸=窗口, 行为与没有本层完全一致。仅 16:9 及更宽的设备启用。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.angle = 0
        self._anchor = None          # 等效竖屏窗口容器
        with self.canvas.before:
            PushMatrix()
            self._rot = Rotate(angle=0, axis=(0, 0, 1), origin=(0, 0))
        with self.canvas.after:
            PopMatrix()

    def apply_orientation(self):
        """窗口尺寸变化时重算层尺寸/旋转角, 返回是否处于横屏反旋转模式。"""
        w, h = Window.width, Window.height
        land = (w > h and (platform == "android" or "--landscape" in sys.argv)
                and _device_is_wide())
        self.pos = (0, 0)
        self.size = (w, h)
        self.angle = _land_angle() if land else 0
        self._rot.angle = self.angle
        self._rot.origin = (w / 2.0, h / 2.0)
        if self._anchor is not None:
            self._anchor.size = (h, w) if land else (w, h)
            self._anchor.center = self.center
        return land

    def _to_eq(self, x, y):
        """物理窗口坐标 -> 等效竖屏坐标(渲染旋转的逆变换)。

        ⚠️ 只做逆旋转, **不能**再减 `self._anchor.pos`: anchor 是普通 AnchorLayout,
           不是 RelativeLayout —— Kivy 单一 window 坐标系下整棵树的 pos 数值本来就是
           "等效竖屏物理坐标", anchor 没有开新坐标系。多减一次会把所有控件的点击判定区
           平移出屏幕(横屏 1740x1000 时偏移 (370,-370))。
        """
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:                      # 逆变换 = 顺时针 90
            px, py = cx + dy, cy - dx
        else:                                     # angle == -90: 逆时针 90
            px, py = cx - dy, cy + dx
        return (px, py)

    def _to_win(self, x, y):
        """等效竖屏坐标 -> 物理窗口坐标(渲染旋转的正变换, to_parent 用)。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:                      # 正变换 = 逆时针 90
            px, py = cx - dy, cy + dx
        else:                                     # angle == -90: 顺时针 90
            px, py = cx + dy, cy - dx
        return (px, py)

    def to_local(self, x, y, **k):
        """覆写(EventLoop 的 grab 派发依赖): 按钮 down 时 `touch.grab(self)`, 之后 move/up
        由 EventLoop **直接派发给按钮本体**(不经过本层的 on_touch_*), 派发前 Window 沿
        祖先链逐层调 to_local 换算坐标。整棵树只有本层带旋转 —— 不覆写则 collide_point
        判不中 + always_release 默认 False 直接吞掉 on_release(症状: 按下有反馈、抬起无动作)。"""
        return self._to_eq(x, y)

    def to_parent(self, x, y, **k):
        """覆写(与 to_local 对称): 局部(等效竖屏)坐标 -> 物理窗口坐标。"""
        return self._to_win(x, y)

    def _pass_touch(self, method, touch):
        if self.angle == 0:
            return method(touch)
        touch.push()
        # ⚠️ 必须用 apply_transform_2d 而不是 `touch.pos = ...`: pos 只是普通元组属性,
        #    直接赋值不改 x/y —— 而 ButtonBehavior.on_touch_down 判点击用的是
        #    collide_point(touch.x, touch.y), 拿到的还是物理坐标, 横屏所有按钮点不中。
        touch.apply_transform_2d(self._to_eq)
        ret = method(touch)
        touch.pop()
        return ret

    def on_touch_down(self, touch):
        return self._pass_touch(super().on_touch_down, touch)

    def on_touch_move(self, touch):
        return self._pass_touch(super().on_touch_move, touch)

    def on_touch_up(self, touch):
        return self._pass_touch(super().on_touch_up, touch)


class RotPopup(Popup):
    """挂 LandLayer 的 Popup: 横屏时随层旋转, 坐标系统一为等效竖屏窗口。
    Kivy 2.3 `ModalView.open()` 硬编码挂 Window, 这里照抄其 open/_real_remove_widget
    把宿主换成旋转层(层无 on_resize/on_keyboard 事件, bind 静默无害)。竖屏回落原生行为。"""

    def _reassert_high_refresh(self, *_):
        """弹窗切换后重申高刷新率: 自适应刷新率设备会把静态 Modal 降回 60Hz。"""
        if platform != "android":
            return
        try:
            # ⚠️ 归属表没把 platform/device.py 列为本模块的依赖, 故延迟到老版求值的那一点
            #    再取(`platform != "android"` 时老版根本不会查这个名字)。
            from ..platform.device import _apply_fps_cap
            _apply_fps_cap()
        except Exception:
            return
        # Popup 的淡入/淡出会在下一小段时间才真正提交到 Surface; 稳定后再请求一次,
        # 防止系统在布局切换时覆盖 Window 的 frame-rate / display-mode 偏好。
        try:
            Clock.schedule_once(lambda *_: _apply_fps_cap(), 0.35)
        except Exception:
            pass

    def _arm_high_refresh(self):
        self._reassert_high_refresh()
        if not getattr(self, "_high_refresh_bound", False):
            self._high_refresh_bound = True
            self.bind(on_dismiss=self._reassert_high_refresh)

    def _popup_gate(self, delta):
        """维护「现在有几个弹窗开着」(`_POPUP_N`) —— 给字体钉子用。

        ⚠️ 用**每个实例一个开关**去重, 而不是直接加减: `open()` 有几条早退分支(已经开着 /
           竖屏回落), 直接加减会重复计数, 而计数偏了钉子就会**静默**地要么全禁要么全放。
           dismiss 也不是每次都配得上一次 open(转屏、被动移除)。
        """
        try:
            _on = delta > 0
            if bool(getattr(self, "_gate_on", False)) == _on:
                return
            self._gate_on = _on
            _POPUP_N[0] = max(0, _POPUP_N[0] + (1 if _on else -1))
        except Exception:
            pass

    def open(self, *_args, **kwargs):
        self._popup_gate(1)
        self._arm_high_refresh()
        layer = _land_layer()
        if layer is None or layer.angle == 0:
            return super().open(*_args, **kwargs)
        if self._is_open:
            return
        self._window = layer
        self._is_open = True
        self.dispatch('on_pre_open')
        if not self.pos_hint:
            self.pos_hint = {"center_x": 0.5, "center_y": 0.5}
        layer.add_widget(self)
        layer.bind(on_resize=self._align_center, on_keyboard=self._handle_keyboard)
        self.center = layer.center
        self.fbind('center', self._align_center)
        self.fbind('size', self._align_center)
        if kwargs.get('animation', True):
            ani = Animation(_anim_alpha=1., d=self._anim_duration)
            ani.bind(on_complete=lambda *_a: self.dispatch('on_open'))
            ani.start(self)
        else:
            self._anim_alpha = 1.
            self.dispatch('on_open')

    def _real_remove_widget(self):
        if not self._is_open:
            return
        self._window.remove_widget(self)
        self._window.unbind(on_resize=self._align_center,
                            on_keyboard=self._handle_keyboard)
        self._is_open = False
        self._window = None

    def dismiss(self, *_args, **kwargs):
        # ⚠️ 闸门计数必须在这里落回: `ModalView.dismiss` 不是每次都走 `_real_remove_widget`
        #    (转屏/被动移除那条路绕开它), 挂在那上面计数会永远不归零, 钉子被**永久静默禁用**。
        self._popup_gate(-1)
        return super().dismiss(*_args, **kwargs)


# 阶段条: 每一帧"游戏在干什么"的取色与图例顺序。
# ⚠️ 名字必须与 `RootWidget._bench_tag()` 的返回值**逐字一致** —— 那张表是唯一的真源,
#    这里只是给它配颜色(对不上会退化成灰色)。
STAGE_ORDER = ("飞行", "装杯", "落袋", "蓄力", "哑火", "待机")
STAGE_COLORS = {
    "飞行": "#3563d1",
    "装杯": "#f0b000",
    "落袋": "#39d98a",
    "蓄力": "#e0533b",
    "哑火": "#5a6a8c",
    "待机": "#b9c3d6",
}


class FpsCurve(Widget):
    """跑分结束后的逐帧提交曲线; 曲线下方另有一条**阶段条**, 与曲线同一根时间轴对齐。"""

    # 放大图里**每帧占多少像素**。⚠️⚠️ 玩家 2026-09-17 第三次定案的值就是 **2**, 且
    #    数据口径**不写死秒数** —— 小图每一组含多少帧由横轴像素数反推(`ceil(帧数/像素)`),
    #    写死 100 毫秒 = 16.5 帧/组, 一组宽 2.8 倍 ⇒"最慢"更容易抓到慢帧, 曲线被压低压抖。
    ZOOM_PER_FRAME_PX = 2.0

    def __init__(self, gaps_ms, cap_fps=120.0, tags=None, on_zoom=None,
                 frame_spaced=False, line_w=1.0, **kw):
        super().__init__(**kw)
        self._gaps = [max(0.01, float(x)) for x in (gaps_ms or [])]
        # 每帧的场景标签, 与 `_gaps` 同序等长; 长度对不上就当没有: 曲线照画, 只是不画阶段条。
        _tg = list(tags or [])
        self._tags = _tg if len(_tg) == len(self._gaps) else []
        self._cap = max(1.0, float(cap_fps or 120.0))
        # x 定位两种口径: 默认按**累计真实时间**(长卡顿占真实宽度);
        # `frame_spaced=True`(放大图)按**帧序号均分**, "每帧占 N 像素"才真的成立。
        self._frame_spaced = bool(frame_spaced)
        # ⚠️⚠️ 默认 1.0 = 真 1 像素。Kivy 的 `Line(width=W)` **画出来是 2W 宽**
        #    (`vertex_instructions_line.pxi` 两侧各按 w 偏移), 只有 `width == 1.0` 才走
        #    `build_legacy()` 的 GL_LINES。**别按直觉写 1.15 去要"1.15 像素"** —— 那是 2.3 像素;
        #    且 `width != 1.0` 时每点生成 14 个顶点, 上千点会超 `unsigned short` 索引的
        #    65535 上限被**静默回绕**(没有异常也没有守卫)。
        self._line_w = float(line_w)
        self._on_zoom = on_zoom
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def on_touch_down(self, touch):
        """点本体 = 放大。⚠️ **必须在 `collide_point` 之后再吃下事件** ——
        否则会连弹窗里别处的触摸一起吞掉(它是 Window 级观察者链上的一环)。"""
        if self._on_zoom is not None and self.collide_point(*touch.pos):
            self._on_zoom(self)
            return True
        return super().on_touch_down(touch)

    def _legend_rows(self, pw):
        """图例按可用宽度折行(360dp 上六个阶段一行放不下)。只列**这一轮真出现过的**阶段。"""
        _names = [n for n in STAGE_ORDER if n in set(self._tags)]
        if not _names:
            return []
        _sw, _pad_name, _pad_item = dp(6.0), dp(6.0), dp(7.0)
        _rows, _cur, _cur_w = [], [], 0.0
        for _n in _names:
            _w = _sw + dp(3.0) + len(_n) * dp(10.0) + _pad_name + _pad_item
            if _cur and _cur_w + _w > pw:
                _rows.append(_cur)
                _cur, _cur_w = [], 0.0
            _cur.append((_n, _cur_w))
            _cur_w += _w
        if _cur:
            _rows.append(_cur)
        return _rows

    @staticmethod
    def _label(canvas, text, x, y, anchor="left"):
        lb = CoreLabel(text=text, font_size=sp(10), color=(0.37, 0.40, 0.45, 1))
        lb.refresh()
        tw, th = lb.texture.size
        if anchor == "right":
            x -= tw
        elif anchor == "center":
            x -= tw / 2.0
        with canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=lb.texture, pos=(x, y), size=(tw, th))

    def _draw(self, *_):
        if self.width < 40 or self.height < 40:
            return
        pad_l, pad_r, pad_t = dp(32), dp(8), dp(18)
        pw = max(1.0, self.width - pad_l - pad_r)
        # ⚠️ 底部留白**按内容算**: 阶段条下面是图例, 而图例在 360dp 上会折成两行 ——
        #    写死 pad_b 的话, 折行的第二行会盖到时间轴上(或掉出控件外面)。
        _strip = dp(7.0)
        _rows = self._legend_rows(pw)
        pad_b = dp(20) + dp(4) + _strip + ((dp(4) + dp(13) * len(_rows)) if _rows else 0.0)
        x0, y0 = self.x + pad_l, self.y + pad_b
        ph = max(1.0, self.height - pad_b - pad_t)
        # 固定 0~120（或设备请求的更高档）坐标，跨轮次的曲线才可直接比较。
        lo, hi = 0.0, max(120.0, self._cap)

        self.canvas.clear()
        with self.canvas:
            Color(0.97, 0.975, 0.985, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(7)])
            for value in range(0, int(hi) + 1, 30):
                y = y0 + value / hi * ph
                Color(0.72, 0.74, 0.77, 0.85)
                Line(points=[x0, y, x0 + pw, y], width=1)
            Color(0.50, 0.52, 0.56, 0.85)
            Line(points=[x0, y0, x0 + pw, y0], width=1)
            Line(points=[x0, y0, x0, y0 + ph], width=1)

            if self._gaps:
                total_ms = sum(self._gaps)
                # ⚠️⚠️ **必须降采样, 不能"一帧一个点"**: 165Hz 设备一轮 5263 帧铺在 785 像素上
                #    = 每列塞 7 帧, 逐帧折线在同一列里上下横跳、自己跟自己交叉, **糊成一块实心色块**
                #    (图在骗人 —— 那一轮实测平均 164.9 / 中位 165.1, 基本满帧)。
                #    每 `_grp` 帧合成一点、取这组里**最慢**的一帧(取最慢而非平均/中位是刻意保守:
                #    平均会被十几帧好帧稀释掉长卡顿)。`_grp` 由横轴像素数反推, 不是写死的。
                _nb = max(1, int(pw))                              # 目标点数 = 横轴像素数
                _grp = max(1, int(math.ceil(len(self._gaps) / float(_nb))))
                _pts = []
                _el = 0.0
                _worst = 0.0
                _n = 0
                for _i, _gap in enumerate(self._gaps):
                    if _gap > _worst:
                        _worst = _gap
                    _el += _gap
                    _n += 1
                    if _n >= _grp:
                        _v = max(lo, min(hi, 1000.0 / _worst))
                        _px = (x0 + (_i + 1) / float(len(self._gaps)) * pw
                               if self._frame_spaced else
                               x0 + _el / max(0.01, total_ms) * pw)
                        _pts.extend([_px,
                                     y0 + _v / hi * ph])
                        _worst = 0.0
                        _n = 0
                if _n > 0:                  # 尾巴: 不满一组也算一个点, 别把最后几帧丢了
                    _v = max(lo, min(hi, 1000.0 / _worst))
                    _px = (x0 + pw if self._frame_spaced else
                           x0 + _el / max(0.01, total_ms) * pw)
                    _pts.extend([_px,
                                 y0 + _v / hi * ph])
                # 浅底上使用深灰蓝：尖峰足够清楚，又不抢走坐标与统计信息。
                Color(0.24, 0.29, 0.35, 1)
                if len(_pts) >= 4:
                    # ⚠️ 线宽走 `self._line_w`, 默认 1.0(真 1 像素, 见 `__init__` 那段)。
                    Line(points=_pts, width=self._line_w, joint="round")

                # ---- 阶段条: 每一帧"游戏在演什么", 与曲线同一根时间轴 ----
                # ⚠️ 按**连续同标签合并成段**再画(逐帧画会画出一大堆亚像素矩形, 又慢又糊)。
                # ⚠️⚠️ 这道 `if self._tags:` 闸**不能删**: 裸着写 `self._tags[_i]` 时, 没传标签
                #    (或长度不匹配)会直接 IndexError, **整张曲线一起没** —— 与 `__init__` 里
                #    "长度对不上就当没有, 绝不让诊断把曲线本身搞没"的承诺正好相反。
                if self._tags:
                    _runs, _t_run, _start, _acc = [], None, 0.0, 0.0
                    for _i, _gap in enumerate(self._gaps):
                        _tg = self._tags[_i]
                        if _tg != _t_run:
                            if _t_run is not None:
                                _runs.append((_t_run, _start, _acc - _start))
                            _t_run, _start = _tg, _acc
                        _acc += _gap
                    if _t_run is not None:
                        _runs.append((_t_run, _start, _acc - _start))
                    _sy = y0 - dp(4.0) - _strip
                    if self._frame_spaced:
                        # 放大图的阶段条也按帧序号定位，避免与曲线横向错位。
                        _runs_px, _run_tag, _run_start = [], None, 0
                        for _i, _tg in enumerate(self._tags):
                            if _tg != _run_tag:
                                if _run_tag is not None:
                                    _runs_px.append((_run_tag, _run_start, _i))
                                _run_tag, _run_start = _tg, _i
                        if _run_tag is not None:
                            _runs_px.append((_run_tag, _run_start, len(self._tags)))
                        _draw_runs = [(_tg, _st / float(len(self._gaps)),
                                       _en / float(len(self._gaps)))
                                      for _tg, _st, _en in _runs_px]
                    else:
                        _draw_runs = [(_tg,
                                       _st / max(0.01, total_ms),
                                       (_st + _dur) / max(0.01, total_ms))
                                      for _tg, _st, _dur in _runs]
                    for _tg, _rx1p, _rx2p in _draw_runs:
                        _rx1 = x0 + _rx1p * pw
                        _rx2 = x0 + _rx2p * pw
                        Color(*hex_rgb(STAGE_COLORS.get(_tg, COL_GRAY)) + (1,))
                        Rectangle(pos=(_rx1, _sy), size=(max(0.6, _rx2 - _rx1), _strip))

        self._label(self.canvas, "%.0f" % hi, x0 - dp(5), y0 + ph - dp(5), "right")
        for value in range(0, int(hi), 30):
            self._label(self.canvas, "%.0f" % value, x0 - dp(5),
                        y0 + value / hi * ph - dp(5), "right")
        duration = sum(self._gaps) / 1000.0
        self._label(self.canvas, "0s", x0, self.y + dp(2))
        self._label(self.canvas, "%.1fs" % duration, x0 + pw, self.y + dp(2), "right")

        # ---- 阶段条的图例(只列这一轮真出现过的阶段) ----
        # ⚠️ 画在 `with self.canvas` 块**之后**: 这样它在最上层, 且与上面那批坐标标签同一种写法。
        if _rows and self._tags:
            for _ri, _row in enumerate(_rows):
                _ly = y0 - dp(4.0) - _strip - dp(3.0) - dp(13.0) * (_ri + 1)
                for _n, _ox in _row:
                    with self.canvas:
                        Color(*hex_rgb(STAGE_COLORS.get(_n, COL_GRAY)) + (1,))
                        Rectangle(pos=(x0 + _ox, _ly + dp(2.0)),
                                  size=(dp(6.0), dp(6.0)))
                    self._label(self.canvas, _n, x0 + _ox + dp(9.0), _ly - dp(1.0))


class GlyphLabel(Widget):
    """把"Kivy 文字光栅化"换成"预烘字形图集 + 画布染色"。**零 `texture_update`、零填纹。**

    ⚠️ 它不是 `Label` 子类, 是故意的: 不碰 `style.kv` 里那条 `Rectangle(texture=self.texture)`
       (那个在 `texture=None` 时会画一块**实心色块**), 也不碰全局的 `Label.texture_update` 补丁。
    ⚠️ `_glyph_alpha0` = 这个控件基色的 alpha。阴影基色是 `(0,0,0,0.6)` ⇒ 淡出必须乘 0.6,
       见 `text._fx_fade_set`(**老路上不存在的一步** —— 老路把 0.6 烘在纹理里)。
    ⚠️ 这条路当前由 `text._GLYPH_ON = False` 关着。
    """

    text = StringProperty("")
    font_size = NumericProperty(0.0)
    bold = BooleanProperty(True)
    fit_box = BooleanProperty(False)      # True: 像 HUD 那样排在"控件矩形"里(余额用)

    def __init__(self, color=(1, 1, 1, 1), **kw):
        self._rgba0 = tuple(color)
        self._glyph_alpha0 = float(self._rgba0[3])
        self._quads = None
        self._text_w = 0
        self._fb = None                   # 退化用的普通 Label(见 `_degrade`)
        self._degraded = False
        self.padding = [0, 0, 0, 0]       # `_fit1` 会读它
        super().__init__(**kw)
        with self.canvas:
            self._gcol = Color(rgba=self._rgba0)      # ⚠️ 唯一一条 Color ⇒ `_lbl_canvas_color` 必找到它
            self._grects = [Rectangle(size=(0.0, 0.0)) for _ in range(_GLYPH_MAX_CHARS)]
        self.bind(text=self._glyph_sync, font_size=self._glyph_sync, bold=self._glyph_sync,
                  pos=self._glyph_place, size=self._glyph_place)
        self._glyph_sync()

    def _degrade(self):
        """图集没这一档(或出现字符集外的字)时, 挂一个**普通 Label 当孩子** —— 行为与旧版一致。

        ⚠️ 只挂一次, 且**不撤**回到图集: 一个标签一局里要么一直走图集, 要么一直走老路。
           中途来回换会让"这一帧到底画了哪个"变成不确定的东西, 逐像素比对就没法做了。
        """
        if self._degraded:
            return
        self._degraded = True
        _GLYPH_MISS[0] += 1
        try:
            self._fb = Label(text=self.text, font_size=self.font_size, bold=self.bold,
                             color=self._rgba0,
                             halign=("left" if self.fit_box else "center"),
                             valign="middle", size_hint=(None, None))
            self._fb.bind(size=lambda w, _: setattr(w, "text_size", w.size))
            if self.fit_box:
                self._fb.size = self.size
                self._fb.pos = (0.0, 0.0)
                self.bind(size=lambda *a: setattr(self._fb, "size", self.size))
            else:
                self._fb.center = self.center
                self.bind(center=lambda *a: setattr(self._fb, "center", self.center))
            self.add_widget(self._fb)
        except Exception:
            pass

    def _glyph_sync(self, *_a):
        # ⚠️ **只在这里**还原画布颜色。淡出会改它的 alpha(`_fx_fade_set`), 放到 `_glyph_place`
        #    里重置会让大字**永不淡出**(而那是每帧都在摆位的路径)。
        self._usecol = self._rgba0
        try:
            self._gcol.rgba = self._rgba0
        except Exception:
            pass
        if self._fb is not None:
            self._fb.text = self.text
            self._fb.font_size = self.font_size
            return
        _rec = _glyph_rec(self.font_size, self.bold)
        _q = _glyph_quads(_rec, self.text) if _rec is not None else None
        if _q is None:
            self._quads = None
            for _r in self._grects:
                _r.size = (0.0, 0.0)
            self._degrade()
            return
        self._quads, self._text_w = _q
        _GLYPH_HIT[0] += 1
        self._glyph_place()

    def _glyph_place(self, *_a):
        """摆位。⚠️ **绝不碰 `_gcol`** —— 中奖大字每帧都在改 center(上浮), 在这里重置颜色 = 永不淡出。

        坐标照抄 `style.kv` 那条 `Rectangle` 的算法(`pos = int(center - texture_size/2)`),
        **用 `int()` 截断, 不是 `math.floor`**(负数方向两者不同)。

        ⚠️⚠️ **必须从 `x/y/width/height` 现算中心, 不能读 `self.center`**: `Widget.center`
           是 `ReferenceListProperty(center_x, center_y)`, 而两者是 **`cache=True` 的
           `AliasProperty`** —— 赋 `w.center = (a, b)` 会先设 `center_x`(派发 `pos`, 我们的
           回调此刻就跑了一次)、再设 `center_y`, 而别名缓存还没失效 ⇒ 回调读到旧值 ⇒
           只有 x 跟着动, y 永远停在初始化那一次的值。
        """
        if not self._quads:
            return
        _rec = _GLYPH_ATLAS.get(_glyph_key(self.font_size, self.bold))
        if _rec is None:
            return
        _th = _rec["h"]
        _cx = self.x + self.width / 2.0
        _cy = self.y + self.height / 2.0
        if self.fit_box:
            # 排进"控件矩形": 横向 `halign='left'` ⇒ x 从左边起; 纵向 `valign='middle'`.
            _x0 = int(_cx - self.width / 2.0) + int(self.padding[0])
            _y0 = int(_cy - self.height / 2.0) + int((self.height - _th) / 2.0)
        else:
            # 居中: 老路上的可见矩形就是 `texture_size`(= 逐字宽之和 x 字高)。
            _x0 = int(_cx - self._text_w / 2.0)
            _y0 = int(_cy - _th / 2.0)
        for _i, _r in enumerate(self._grects):
            if _i < len(self._quads):
                _t, _dx, _w = self._quads[_i]
                _r.texture = _t
                _r.size = (_w, _th)
                _r.pos = (_x0 + _dx, _y0)
            else:
                _r.size = (0.0, 0.0)


def _axis_nice_step(x):
    """把"想要的步长"抬到**友好数**(1/2/5 × 10^k)。只服务于 `SpeedCurve` 的轴取整。"""
    if x <= 0:
        return 1.0
    _e = math.floor(math.log10(x))
    _f = x / (10.0 ** _e)
    for _m in (1.0, 2.0, 5.0):
        if _f <= _m:
            return _m * (10.0 ** _e)
    return 10.0 * (10.0 ** _e)


def _med5(_arr, _k=5):
    """每 `_k` 个点**一组(不重叠)**取中位数 —— 玩家定的功率曲线口径:「每 5 个点取中位数」。

    ⚠️ 用**中位数**而不是均值: 均值/截尾均值会**造出没测到过的值**, 中位数的输出必然是
       窗口里存在过的读数。组内 `None`(该格没读到)**先剔掉再取中位**; 整组都是 None 则返回 `None`。
    ⚠️⚠️ **只给"画曲线"用** —— 统计(平均/最低/最高)与导出的 txt 一律走**原始序列**:
       滤波会改 `max`, 而"最高功率"是机器的能力指标, 导出的 txt 更是专门拿原始值判尖峰真伪的。
    """
    _k = max(1, int(_k))
    _a = list(_arr or [])
    _out = []
    for _i in range(0, len(_a), _k):
        _w = sorted(x for x in _a[_i:_i + _k] if x is not None)
        _out.append(_w[len(_w) // 2] if _w else None)
    return _out


def _curve_axis_range(_vals, _flat_min_range):
    """`SpeedCurve` 的纵轴取整规则(从 `_draw` 里原样搬出来的, 一个数都没动)。

    搬出来是为了让**双轴图的右轴**也走同一套 —— 两边各写一份必然漂移。

    返回 `(lo, hi)`: 常规 = 最小值 − max(跨度×5%, |最小值|×2%), 上限同理向上留;
    常数(跨度为 0)= 上下各留 max(|值|×5%, 最小展示范围/2); 最后**向外**取整到友好刻度。
    """
    _lo_d, _hi_d = min(_vals), max(_vals)
    _span_d = _hi_d - _lo_d
    if _span_d > 0:
        _p_lo = max(_span_d * SpeedCurve.Y_PAD_FRAC, abs(_lo_d) * SpeedCurve.Y_PAD_VAL_MIN)
        _p_hi = max(_span_d * SpeedCurve.Y_PAD_FRAC, abs(_hi_d) * SpeedCurve.Y_PAD_VAL_MIN)
    else:
        _p_lo = _p_hi = max(abs(_lo_d) * SpeedCurve.Y_FLAT_FRAC,
                            _flat_min_range / 2.0)
    _lo_p, _hi_p = _lo_d - _p_lo, _hi_d + _p_hi
    _step = _axis_nice_step(max(abs(_lo_p), abs(_hi_p)) * SpeedCurve.Y_TICK_FRAC)
    _lo = math.floor(_lo_p / _step) * _step
    if _lo_d >= 0.0:
        _lo = max(0.0, _lo)   # 本指标不可能为负 ⇒ 轴不必画到 0 以下
    _hi = math.ceil(_hi_p / _step) * _step
    if _lo_d >= 0.0:
        _g = SpeedCurve.Y_MIN_GAP_FRAC
        _lo_max = (_lo_d - _g * _hi) / (1.0 - _g)
        if _lo > _lo_max:
            _lo = max(0.0, math.floor(_lo_max / _step) * _step)
    if _hi - _lo < 1.0:       # 兜底: 极端退化时别造出零高度(会除零)
        _hi = _lo + 1.0
    return _lo, _hi


class SpeedCurve(Widget):
    """CPU 高压那 **300 多个逐秒样本**的成绩曲线(纵轴 = 步/秒)。

    ⚠️ 与 `FpsCurve` **分开写是故意的**: 那张图纵轴是帧率, 语义完全不同 —— 硬套得把值
       取倒数, 纵轴就变成"越快越靠下", 读图的人会理解反。
    ⚠️ 纵轴不再是"数据最小~最大": 现在**上下各留白 + 向外取整到友好刻度** ⇒ 纵轴刻度数字
       = 轴的上界/中点/下界, 与图下那行「最低/最高」**不再是同一个数**(那行印的仍是真实数据)。
    """

    # 纵轴留白 / 取整的四条系数(玩家 2026-09-16 亲口给的规则, 逐字实现, **别随手调**)
    Y_PAD_FRAC = 0.05          # 上下各留 **跨度** 的 5%
    Y_PAD_VAL_MIN = 0.02       # ...且不少于该端**数值**的 2%(窄幅大值数据靠它)
    Y_FLAT_FRAC = 0.05         # 常数数据(跨度为 0): 上下各留 **值** 的 5%
    Y_FLAT_MIN_RANGE = 2000.0  # 常数数据的最小展示范围(步/秒) —— 值很小时兜底
    Y_TICK_FRAC = 0.01         # 友好刻度的粒度 ≈ **轴量级**的 1%
    Y_MIN_GAP_FRAC = 0.05      # 兜底: 最低点离底**至少**这么多(占轴高)

    def __init__(self, vals, value_decimals=0, flat_min_range=None,
                 vals2=None, times2=None, dt=None, value_decimals2=1,
                 flat_min_range2=None, unit2='', t_max=None, unit='', **kw):
        """`vals` 是**左轴**序列; 传了 `vals2` 就变成**双轴图**(右轴 = `vals2`)。

        ⚠️⚠️ **不传 `vals2` 时逐字走老路径** —— 成绩/频率两条曲线一个字都不受影响。
        ⚠️ 双轴时横轴一律按**秒**(两条序列长度不同, 按下标画必然对不齐): `vals` 的时刻由
           `dt` × 下标推出来; `vals2` 的时刻必须由 `times2` **显式给出**(温度那条是**非等距**的)。
        ⚠️ `vals` 里的 `None` = 那一格没读到 ⇒ **跳过该点**(线在缺口处直连), **不要插 0**。
        """
        super().__init__(**kw)
        self._v = [float(x) for x in (vals or []) if x]
        self._value_decimals = max(0, int(value_decimals))
        self._flat_min_range = (self.Y_FLAT_MIN_RANGE if flat_min_range is None
                                else max(0.1, float(flat_min_range)))
        _dt = max(1e-6, float(dt)) if dt else None
        _p2 = []
        if vals2 and times2:
            for _a, _b in zip(list(vals2), list(times2)):
                if _a is not None and _b is not None:
                    _p2.append((float(_a), float(_b)))
        self._v2 = [p[0] for p in _p2]
        self._t2 = [p[1] for p in _p2]
        self._px = ([(float(_a), _i * _dt) for _i, _a in enumerate(vals or [])
                     if _a is not None] if _dt else [])
        # 两条里任意一条不足两点 ⇒ 双轴没意义, 退回单轴(右边不画)
        self._dual = (len(self._v2) >= 2 and len(self._px) >= 2)
        self._value_decimals2 = max(0, int(value_decimals2))
        self._flat_min_range2 = (self.Y_FLAT_MIN_RANGE if flat_min_range2 is None
                                 else max(0.1, float(flat_min_range2)))
        self._unit2 = unit2 or ''
        # 纵轴数字后面拼的单位。
        # ⚠️⚠️ **只有功率曲线能传** —— 左留白是 `dp(46)`, 实测: `12.20W` = 36px(与现有最宽的
        #    `102000` 一样宽, 安全), 而 `2425MHz` 要 **44px**, 只剩 2px 余量。
        self._unit = unit or ''
        _all_t = [_t for _, _t in self._px] + list(self._t2)
        self._t_max = float(t_max) if t_max else (max(_all_t) if _all_t else None)
        self._ax2 = None
        self._series = None
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    @staticmethod
    def _txt(canvas, text, x, y, anchor="left"):
        """刻度文字。⚠️ 颜色**不能沿用 `FpsCurve._label`**(那里写死深灰, 是配它的浅底)——
        这张是**深底**, 必须用浅色, 否则刻度根本看不见。"""
        lb = CoreLabel(text=text, font_size=sp(10), color=hex_rgb(COL_SUB) + (1,))
        lb.refresh()
        tw, th = lb.texture.size
        if anchor == "right":
            x -= tw
        elif anchor == "center":
            x -= tw / 2.0
        with canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=lb.texture, pos=(x, y), size=(tw, th))

    def _draw(self, *_):
        self.canvas.clear()
        _dual = self._dual
        # ⚠️ 双轴时"够不够两点"看的是**成对序列**(`_px` / `_v2`), 不是老路径那个 `_v`
        if (_dual and (len(self._px) < 2 or len(self._v2) < 2)) or len(self._v) < 2:
            return
        if self.width < 40 or self.height < 40:
            return
        # 左侧留 46dp 给纵轴刻度、下方留 18dp 给横轴次数;
        # ⚠️ 双轴时**右侧加宽到 38dp** 放右轴那三个刻度数字 —— `pad_l` 一动不动(有探针钉着)。
        pad_l, pad_r, pad_t, pad_b = dp(46), (dp(38) if _dual else dp(8)), dp(8), dp(18)
        pw = max(1.0, self.width - pad_l - pad_r)
        ph = max(1.0, self.height - pad_b - pad_t)
        x0, y0 = self.x + pad_l, self.y + pad_b
        _lo, _hi = _curve_axis_range(([p[0] for p in self._px] if _dual else self._v),
                                     self._flat_min_range)
        # ⚠️ 把**最终真正画上去**的那组轴范围记在控件上 —— 探针要断言的是"真画出来的这一组",
        #    而不是在探针里把算法重抄一遍(复制品只会测它自己)。
        self._ax = (_lo, _hi)
        _mid = (_hi + _lo) / 2.0
        _sp = max(1.0, _hi - _lo)
        _n = len(self._v)
        # 右轴(双轴才有): 同一条规则, 但用**温度自己的**留白系数
        _lo2 = _hi2 = _mid2 = None
        _sp2 = 1.0
        if _dual:
            _lo2, _hi2 = _curve_axis_range(self._v2, self._flat_min_range2)
            _mid2 = (_hi2 + _lo2) / 2.0
            _sp2 = max(1e-6, _hi2 - _lo2)
        self._ax2 = ((_lo2, _hi2) if _dual else None)

        def _yy(_val):
            return y0 + ph * ((_val - _lo) / _sp)

        def _yy2(_val):
            return y0 + ph * ((_val - _lo2) / _sp2)

        _pts2 = []
        if _dual:
            # ⚠️ 双轴**必须按秒画横轴**: 两条序列长度不同(功率 ~1790 / 温度 ~359),
            #    按下标画会把它们错开一大截。`None` 点已经在 `_px`/`_v2` 里剔掉了。
            _tm = self._t_max or 1.0
            _pts = []
            for _val, _t in self._px:
                _pts.append(x0 + pw * (_t / _tm))
                _pts.append(_yy(_val))
            for _val, _t in zip(self._v2, self._t2):
                _pts2.append(x0 + pw * (_t / _tm))
                _pts2.append(_yy2(_val))
        else:
            _pts = []
            for _i, _y in enumerate(self._v):
                _pts.append(x0 + pw * (_i / float(_n - 1)))
                _pts.append(_yy(_y))
        with self.canvas:
            Color(*hex_rgb(COL_BTN_OFF), 0.55)
            Rectangle(pos=self.pos, size=self.size)
            # 横向网格线**只挂左轴**(双轴图的标准做法: 网格共享, 右轴只出自己的刻度)
            for _val in (_hi, _mid, _lo):
                Color(*hex_rgb(COL_SUB), 0.30)
                Line(points=[x0, _yy(_val), x0 + pw, _yy(_val)], width=1)
            Color(*hex_rgb(COL_SUB), 0.75)
            Line(points=[x0, y0, x0 + pw, y0], width=1)
            Line(points=[x0, y0, x0, y0 + ph], width=1)
            if _dual:
                Line(points=[x0 + pw, y0, x0 + pw, y0 + ph], width=1)
            Color(*hex_rgb(COL_BALL))
            # ⚠️ 线宽 **1.0 = 真 1 像素** —— Kivy 的 `Line(width=W)` 画出来是 **2W** 宽。
            #    这一行是**硬编码**, 与 `FpsCurve(line_w=...)` 是**两个旋钮**, 改一处不会连带另一处。
            Line(points=_pts, width=1.0, joint="round")
            if _dual:
                Color(*hex_rgb(COL_FIRE))
                Line(points=_pts2, width=1.0, joint="round")
        # ---- 刻度数字(画在 canvas 之外, 各开自己的上下文) ----
        _vf = "%%.%df" % self._value_decimals
        for _val in (_hi, _mid, _lo):
            self._txt(self.canvas, (_vf % _val) + self._unit,
                      x0 - dp(5), _yy(_val) - dp(5), "right")
        if _dual:
            # 右轴刻度: 贴在右边界**外侧**, 左对齐(否则会盖到曲线上)
            _vf2 = "%%.%df" % self._value_decimals2
            for _val in (_hi2, _mid2, _lo2):
                self._txt(self.canvas, _vf2 % _val,
                          x0 + pw + dp(4), _yy2(_val) - dp(5), "left")
        # 横轴: 单轴印**第几个样本**; 双轴印**秒**(两条长度不同, 只能按时间读)。
        if _dual:
            _tm = self._t_max or 1.0
            for _k, _frac in ((0, 0.0), (1, 0.5), (2, 1.0)):
                _an = "left" if _k == 0 else ("right" if _k == 2 else "center")
                self._txt(self.canvas, "%ds" % int(round(_tm * _frac)),
                          x0 + pw * _frac, self.y + dp(3), _an)
        else:
            for _k in (0, (_n - 1) // 2, _n - 1):
                _an = "left" if _k == 0 else ("right" if _k == _n - 1 else "center")
                self._txt(self.canvas, "%d" % (_k + 1),
                          x0 + pw * (_k / float(_n - 1)), self.y + dp(3), _an)
        # ⚠️ 与 `_ax` 同一条规矩: 记的是**真画出去的那两条线**的颜色/点数/小数位, 供探针断言。
        self._series = ([("left", COL_BALL, len(_pts) // 2, self._value_decimals),
                         ("right", COL_FIRE, len(_pts2) // 2, self._value_decimals2)]
                        if _dual else None)


# ===========================================================================
# 版本号 / 弹窗标题 / 成绩正文  (老版 12787-12845 + 13127-13260)
# ===========================================================================
def _app_version():
    """本包版本号(如 "v0.6.30"); 拿不到返回 ""。

    ⚠️ 两个来源, **都不是在这里另抄一份常数**:
      - 安卓: PackageManager 的 versionName(就是 buildozer.spec 的 version);
      - 桌面: 直接读主脚本旁边的 `buildozer.spec` —— 出货打包用的就是同一个文件。
    """
    try:
        if platform == 'android':
            from jnius import autoclass
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            pi = act.getPackageManager().getPackageInfo(act.getPackageName(), 0)
            if pi.versionName:
                return 'v%s' % pi.versionName
    except Exception:
        pass
    try:
        import os
        import re as _re
        from ..audio.voice import app_root
        _spec = os.path.join(app_root(), "buildozer.spec")
        with open(_spec, "r", encoding="utf-8", errors="ignore") as _f:
            for _line in _f:
                _m = _re.match(r"\s*version\s*=\s*(\S+)", _line)
                if _m:
                    return "v%s" % _m.group(1)
    except Exception:
        pass
    return ""


def _startup_title():
    """「启动信息」弹窗的标题 = 「跳跳的弹珠机 v0.x.x」(名字与版本号之间留一个空格)。

    ⚠️ 版本号**全工程只在这里出现一次** —— 正文里那行 `v0.6.30 · 于 … 制作` 的版本前缀
       已经删掉, 只留制作时刻。拿不到版本时退化成纯游戏名, 不留一个孤零零的 "v"。
    """
    try:
        v = _app_version()
    except Exception:
        v = ""
    return ("跳跳的弹珠机 %s" % v) if v else "跳跳的弹珠机"


def _soc_result_title():
    """CPU 高压**结果弹窗**的标题。与 `_startup_title` **同一套做法**: 版本号只出现在标题里。"""
    try:
        v = _app_version()
    except Exception:
        v = ""
    return ("CPU高压测试 %s" % v) if v else "CPU高压测试"


def _bench_result_title():
    """普通模拟测试的成绩窗口标题。跑完即时窗口与历史详情窗口共用。"""
    return "画面帧率和性能测试"


def _bench_score_text(d):
    """成绩块正文 —— **现场那个弹窗与历史「详情」共用这一份**。

    ⚠️ 只有一份是硬要求: 两处各写一份迟早脱钩(同一个数两种说法)。
    ⚠️ 入参 `d` 用**记录里的字段名**(`bench_history` 那套键); 现场那条路先用同样的键组一个
       dict 再传进来 —— 于是"刚跑完"和"翻历史"走的是**同一条渲染路径**。
    ⚠️ 缺字段一律印「—」, **绝不拿别的字段回填**(老记录没有 `render_10low`/`phys_mad` 之类)。
    """
    def _g(key, default=None):
        return d.get(key, default)

    _dev = str(_g('device', '') or '')
    # 帧率块: 六个值 vs 三个值 —— 老记录没有 10%Low / p99 / p90 ⇒ 退回三值版。
    _l10, _p99, _p90 = _g('render_10low'), _g('render_p99'), _g('render_p90')
    if _l10 is not None and _p99 is not None and _p90 is not None:
        _low_txt = ('平均帧率： %.1f    中位帧率：%.1f\n'
                    '1%%Low：%.1f    10%%Low：%.1f\n'
                    'p99帧率：%.1f    p90帧率：%.1f') % (
                        float(_g('render_fps', 0.0) or 0.0),
                        float(_g('render_median', 0.0) or 0.0),
                        float(_g('render_1low', 0.0) or 0.0),
                        float(_l10), float(_p99), float(_p90))
    else:
        _low_txt = '平均帧率： %.1f　中位帧率：%.1f\n1%%Low帧率：%.1f' % (
            float(_g('render_fps', 0.0) or 0.0),
            float(_g('render_median', 0.0) or 0.0),
            float(_g('render_1low', 0.0) or 0.0))
    # 高压那一行(常规跑分这条路波 2 是被清空的 ⇒ 不印, 与现场面板一致)。
    _sv = [x for x in (_g('sust_fps_windows') or []) if x > 0]
    _s_txt = (('连续高压测试 %d 秒：首 %d → 末 %d 步/秒（降 %.0f%%）· 最低 %d\n'
               % (int(_g('sust_sec', 0) or 0), int(_g('sust_first', 0) or 0),
                  int(_g('sust_last', 0) or 0), float(_g('sust_decay_pct', 0.0) or 0.0),
                  int(_g('sust_min', 0) or 0))) if _sv else '')
    _mad = _g('phys_mad')
    _runs = [int(x) for x in (_g('phys_fps_runs') or [])][:20]
    _samples = ','.join('%d' % x for x in _runs) if _runs else '—'
    # 普通测试只记整场开始/结束两个电池温度。老历史没字段时整行不显示, 不用 0 回填。
    # 颜色用弹珠金 `COL_BALL`; 现场和历史的 Label 都开启 markup, 因为两处共用本函数。
    _bt0, _bt1 = _g('battery_start_c'), _g('battery_end_c')
    _battery_txt = ''
    if _bt0 is not None and _bt1 is not None:
        _battery_txt = ('[color=%s]电池温度：从%.1f度到%.1f度[/color]\n'
                        % (COL_BALL, float(_bt0), float(_bt1)))
    return ('%s\n'
            '平均每轮 %d 步模拟，平均差系数 %s\n'
            '分数依次为：%s\n'
            '%s'
            '每次飞行平均 %s 步运算，'
            '平均持续 %s 秒' + chr(10) +
            '弹珠飞行期间可完成 %s 次飞行模拟\n'
            '%s'
            '%s') % (
        _dev, int(_g('phys_fps', 0) or 0),
        (('%.2f%%' % float(_mad)) if _mad is not None else '无数据'),
        # ⚠️ 格式串里**没有**「N 轮」前缀的 `%d` —— 实参也必须一起没有。
        #    格式串少一个 `%d` 而实参多一个, 后面的数会**整体前移一格且不报错**
        #    (`%s` 什么都吃得下)。改这一处时数一遍两边。
        _samples,
        _s_txt, float(_g('avg_frames', 0) or 0),
        ('%.2f' % (float(_g('flight_ms')) / 1000.0)) if _g('flight_ms') else '—',
        ('%.1f' % float(_g('margin'))) if _g('margin') else '—',
        _battery_txt, _low_txt)
