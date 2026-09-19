"""游戏盘面自绘控件 GameArea: 520x660 逻辑场景(y 向下), 绘制时等比缩放居中。

⚠️ `_redraw` 里的 canvas 指令**书写顺序即层序**, 一行都不许挪。
⚠️ 子控件 canvas 是**绝对(窗口)坐标**, 父级不做平移 —— 见 `big_result_text` 处
   "横向不要再减 self.x" 的说明。
"""

import math
import time

from kivy.graphics import (Color, Ellipse, Line, PopMatrix, PushMatrix,
                           Rectangle, Rotate, RoundedRectangle, Scale)
from kivy.metrics import sp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label

from ..config import (BALL_R, BALL_VIEW, BALL_VIS_R, CH, COL_BUMPER, COL_CANVAS,
                      COL_FIRE, COL_LAMP_OFF, COL_LANE, COL_METER, COL_PEG,
                      COL_WALL, CW, FIELD_L, FIXED_DT, FLOOR, LANE_L,
                      MISFIRE_POWER, NUM_SLOTS, PEG_R, PLUNGER_Y, RIGHT_INNER,
                      SLOT_TOP, SLOT_W, hex_rgb)
from ..physics import power_u
# ⚠️ `_brk_add` 的家在 `ui/text.py`(帧剖析的写入口都在那边) —— 从 platform 拿会 ImportError,
#    因为那边已经把它让出去了(拆分时两边各建了一份, 是静默分叉)。
from .text import _brk_add
from .text import (_fade_set, _fx_fade_set, _glyph_quads, _glyph_rec,
                   _lbl_canvas_color, _set_lbl_tint, _tag_texupd, ball_texture,
                   fit_font_size, slot_color, slot_text_tex, slot_txt, text_px)
from .widgets import GlyphLabel
from .winfx import (BIG_TEXT_ALPHA_STEPS, BIG_TEXT_LIFE, TEXT_CY_WIN,
                    WinPileFX)


class GameArea(FloatLayout):
    """520x660 逻辑场景(y 向下), 绘制时等比缩放居中。

    静态元素(墙/钉/槽/弧)只在尺寸变化或换盘面时重绘; 球/力度条/弹簧每帧只改 pos;
    特效(浮字/中奖大字)是 FloatLayout 子 Label, 每帧在 `tick_draw` 里驱动。
    """

    def __init__(self, game, **kw):
        super().__init__(**kw)
        # ⚠️⚠️ `game` 是 **RootWidget(界面壳)**, 与老版同形 —— `area.game` 的 6 处外部读者
        #    (`winfx._bounce` 取 sfx / `_build_texwarm` 取 bet / `text._glyph_warm_step` 走标签树)
        #    要的是**宿主**, 不是状态机。
        #    ⚠️ 本文件绘制时读的 `ball`/`multipliers`/`state` 在**状态机**上, 所以另给 `model`。
        #    老版这两者是同一个对象(RootWidget 既装状态又装控件); 拆开之后**必须两个名字**,
        #    合成一个一定有一边取错 —— 实测就是这里静默丢了 15 声音效。
        self.game = game                       # RootWidget(界面壳)
        self.model = getattr(game, "game", None)   # Game(无头状态机)
        self._s = 1.0
        self._ox = 0.0
        self._oyt = 0.0
        self._slot_cols = []
        self._slot_txt_cols = []
        self._slot_txt_rects = []     # 空槽的矩形 size=(0,0)
        self._lamp_cols = []
        self._peg_cols = {}
        self._peg_ellipses = {}
        self._peg_flash = {}
        self._ball_e = None
        self._meter_fill = None
        self._meter_col = None
        self._spring_bars = []
        self._spring_power = 0.0
        self._spring_vel = 0.0
        self._spring_bar_col = None
        self._pulse = None
        self._effects = []
        self._bench_badge = None      # 跑分提示牌, 不走 `_effects` 的飘字动画
        self._last_size = None        # 尺寸变了才清特效
        # ⚠️ WinPileFX **必须最先 `add_widget`**: Kivy 按 children 逆序绘制(后加的画在上面),
        #    而中奖大字是 settle 时才 add 的 Label ⇒ 大字天然盖在杯层之上, 杯子压暗盖不住它。
        self.win_fx = WinPileFX(self, size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self.add_widget(self.win_fx)
        self.bind(size=self._redraw, pos=self._redraw)

    def win_fx_busy(self):
        """中奖演出是否还在放(锁输入的判据)。"""
        return self.win_fx.busy()

    def _restack_overlays(self):
        """把覆盖层重新挂回画布末尾, 顺序固定为「板面 < 中奖杯 < 中奖大字」。

        ⚠️ 不能省: `self.canvas.clear()` 会把子控件的 canvas **一起摘掉**(子控件的
           canvas 是 `add_widget` 时挂进父 canvas 的), 不重挂的话每次换盘面或尺寸变化
           杯子和中奖大字就整个消失, 而它们的 `canvas.children` 看起来完全正常。
        ⚠️ 顺序靠**追加次序**保证 —— 后 add 的后画, 所以杯层必须排在大字之前。
        """
        chain = [self.win_fx]
        for e in self._effects:
            chain.extend(e["ws"])
        if self._bench_badge is not None:
            chain.append(self._bench_badge)
        for w in chain:
            try:
                self.canvas.remove(w.canvas)
            except Exception:
                pass
            self.canvas.add(w.canvas)

    # ---- 坐标换算: 逻辑(x, y向下) -> 控件像素(Kivy y向上); 返回 kwargs 便于 ** 展开 ----
    def _rect(self, x1, y1, x2, y2):
        w = (x2 - x1) * self._s
        h = (y2 - y1) * self._s
        return {"pos": (self._ox + x1 * self._s, self._oyt - y2 * self._s),
                "size": (w, h)}

    def _circle(self, cx, cy, r):
        return {"pos": (self._ox + (cx - r) * self._s,
                        self._oyt - (cy + r) * self._s),
                "size": (2 * r * self._s, 2 * r * self._s)}

    def _px(self, x):
        return self._ox + x * self._s

    def _py(self, y):
        return self._oyt - y * self._s

    def _update_slots(self):
        """只更新倍率槽的颜色与文字, 不清画布、不重建静态指令。

        ⚠️ 只有在盘面**几何没变**、仅倍率变了时才能走这条路。结构对不上(首帧 / 刚改过
           尺寸 / 槽数变了)就**退回完整 `_redraw()`** —— 绝不半更新。
        """
        g = self.model
        try:
            ok = (len(self._slot_cols) == NUM_SLOTS
                  and len(self._slot_txt_cols) == NUM_SLOTS
                  and len(self._slot_txt_rects) == NUM_SLOTS)
        except Exception:
            ok = False
        if not ok:
            self._redraw()
            return
        fs = max(12, int(20 * self._s))
        cy = (SLOT_TOP + FLOOR) / 2.0
        for i in range(NUM_SLOTS):
            m = g.multipliers[i]
            try:
                self._slot_cols[i].rgb = hex_rgb(slot_color(m))
                self._slot_txt_cols[i].rgb = hex_rgb(slot_txt(m))
                _r = self._slot_txt_rects[i]
                if m > 0:
                    _tex = slot_text_tex(m, fs)
                    if _tex is not None:
                        _r.texture = _tex
                        _r.size = _tex.size
                        _r.pos = (self._px(FIELD_L + (i + 0.5) * SLOT_W) - _tex.width / 2.0,
                                  self._py(cy) - _tex.height / 2.0)
                else:
                    _r.size = (0, 0)          # 与 `_redraw` 的 size 0 一致
            except Exception:
                pass
        # ⚠️ 与 `_redraw` 同义: 重掷后槽位白闪作废(`tick_draw` 读 `_pulse`)。
        self._pulse = None
        # ⚠️ **与 `_redraw` 同义的第二件事: 换盘面必须熄灭全部投中指示灯**。灯本来就只在
        #    `_redraw` 重建 `_lamp_cols` 时被顺手重置成 `COL_LAMP_OFF`; 走增量更新这条路
        #    就没这层遮盖了 ⇒ 漏掉这步会变成"灯每局点亮一格、从不熄灭", 几局下来槽上
        #    攒出一片红绿点。⚠️ 若不做, 没有任何报错, 只是画面上慢慢脏掉。
        # ⚠️ `_lamp_cols` 的条数由 `_redraw` 建, 这里只按**现有条数**熄灭(与 `lamps_off()`
        #    同一套写法), 所以它不参与上面那个结构守卫。
        self.lamps_off()

    def _redraw(self, *_):
        if self.width < 20 or self.height < 20:
            return
        s = min(self.width / CW, self.height / CH)
        size_changed = (self.width, self.height) != self._last_size
        self._last_size = (self.width, self.height)
        self._s = s
        self._ox = self.x + (self.width - CW * s) / 2.0
        self._oyt = self.y + (self.height + CH * s) / 2.0
        g = self.model
        self._pulse = None
        # ⚠️ 仅尺寸真变了才清特效 —— 否则 `park_ball` 重掷盘面会把中奖大字一起杀了。
        if size_changed:
            for e in self._effects:
                for w in e["ws"]:
                    self.remove_widget(w)
            self._effects = []
        self.canvas.clear()
        with self.canvas:
            Color(*hex_rgb(COL_CANVAS))
            Rectangle(pos=self.pos, size=self.size)
            Color(*hex_rgb(COL_LANE))
            Rectangle(**self._rect(LANE_L, 0, RIGHT_INNER, FLOOR))
            Color(*hex_rgb(COL_WALL))
            for w in g.geo["walls"]:
                if w[1] == FLOOR:
                    # ⚠️ 底墙**绘制**上沿下移到球的新下沿, 球才"坐在地面上"而非陷进去。
                    #    只改绘制: 物理层的底墙在 `physics_step` 里被显式跳过(地板不是墙),
                    #    这里的 y 不参与碰撞/落格/门禁, `build_walls()` 的几何一个字不动。
                    Rectangle(**self._rect(w[0], FLOOR + BALL_R * (BALL_VIEW - 1), w[2], w[3]))
                    continue
                Rectangle(**self._rect(*w))
            # 导流弧(右壁口部弧形导轨)
            if g.geo["deflectors"]:
                pts = []
                for (x1, y1, x2, y2) in g.geo["deflectors"]:
                    pts.extend([self._px(x1), self._py(y1)])
                x1, y1, x2, y2 = g.geo["deflectors"][-1]
                pts.extend([self._px(x2), self._py(y2)])
                Color(*hex_rgb(COL_WALL))
                Line(points=pts, width=max(1.0, 3.5 * s), cap="round", joint="round")
            # 钉阵(每颗独立 Color+Ellipse, 支持单颗受击高亮/形变)
            self._peg_cols.clear()
            self._peg_ellipses.clear()
            for px, py in g.geo["pegs"]:
                col = Color(*hex_rgb(COL_PEG))
                e = Ellipse(**self._circle(px, py, PEG_R))
                self._peg_cols[(px, py)] = col
                self._peg_ellipses[(px, py)] = e
            # 槽隔板
            Color(*hex_rgb(COL_BUMPER))
            for d in g.geo["dividers"]:
                Rectangle(**self._rect(*d))
            # 倍率槽(圆角, 颜色随盘面)
            self._slot_cols = []
            for i in range(NUM_SLOTS):
                col = Color(*hex_rgb(slot_color(g.multipliers[i])))
                self._slot_cols.append(col)
                RoundedRectangle(radius=[max(1.0, 6 * s)],
                                 **self._rect(FIELD_L + i * SLOT_W + 2, SLOT_TOP + 3,
                                              FIELD_L + (i + 1) * SLOT_W - 2, FLOOR - 3))
            # 槽倍率文字(走 `slot_text_tex` 的缓存; 逻辑 20px 跟盘面缩放, 手机上≈11sp)
            # ⚠️ **9 个槽全都建**(空槽用 size=(0,0) 藏起来, 视觉完全等价) —— 否则倍率的
            #    "哪几个槽有奖"每次重掷都会变, 条数跟着变就没法在原地更新了(见 `_update_slots`)。
            self._slot_txt_cols = []
            self._slot_txt_rects = []
            fs = max(12, int(20 * s))
            cx = FIELD_L + 0.5 * SLOT_W
            cy = (SLOT_TOP + FLOOR) / 2.0
            for i in range(NUM_SLOTS):
                m = g.multipliers[i]
                self._slot_txt_cols.append(Color(*hex_rgb(slot_txt(m))))
                if m > 0:
                    tex = slot_text_tex(m, fs)
                    _r = Rectangle(texture=tex,
                                   pos=(self._px(FIELD_L + (i + 0.5) * SLOT_W) - tex.width / 2.0,
                                        self._py(cy) - tex.height / 2.0),
                                   size=tex.size)
                else:
                    _r = Rectangle(texture=None, pos=(0, 0), size=(0, 0))
                self._slot_txt_rects.append(_r)
            # 投中指示灯(中奖绿/未中红, 结算时变色, 换盘面熄灭)
            self._lamp_cols = []
            ly = SLOT_TOP - 9
            for i in range(NUM_SLOTS):
                col = Color(*hex_rgb(COL_LAMP_OFF))
                self._lamp_cols.append(col)
                cx = FIELD_L + (i + 0.5) * SLOT_W
                Ellipse(**self._circle(cx, ly, 5))
            # 力度条底槽 + 哑火红线(玩家必须看得见阈值在哪)
            Color(*hex_rgb("#1b2b4a"))
            Rectangle(**self._rect(RIGHT_INNER - 9, SLOT_TOP - 210,
                                   RIGHT_INNER - 4, SLOT_TOP - 6))
            Color(*hex_rgb(COL_FIRE))
            ty = (SLOT_TOP - 8) - MISFIRE_POWER * 200
            Rectangle(**self._rect(RIGHT_INNER - 12, ty - 1, RIGHT_INNER - 1, ty + 1))
            # 力度填充(动态)
            self._meter_col = Color(*hex_rgb(COL_METER))
            self._meter_fill = Rectangle(pos=(0, 0), size=(0, 0))
            # 弹簧凹槽(跟随 PLUNGER_Y; 从球底延伸到画布底)
            Color(*hex_rgb("#060e18"))
            Rectangle(**self._rect(LANE_L, PLUNGER_Y + BALL_VIS_R, RIGHT_INNER, CH))
            # 弹簧: 2 条横线(在凹槽内, 间距=松弛, 贴紧=压缩)
            self._spring_bar_col = Color(*hex_rgb("#8fa0c4"))
            self._spring_bars = []
            for _ in range(3):
                self._spring_bars.append(
                    Line(points=[0, 0, 0, 0], width=max(0.8, 1.5 * s),
                         cap="round"))
            # 球(动态, 程序化渐变贴图; 视觉 BALL_VIEW 倍放大, 碰撞半径不变)
            Color(1, 1, 1)
            self._ball_push = PushMatrix()
            self._ball_rot = Rotate(angle=0.0, origin=(0, 0))
            self._ball_e = Rectangle(texture=ball_texture(), pos=(0, 0),
                                     size=(2 * BALL_R * BALL_VIEW * s,
                                           2 * BALL_R * BALL_VIEW * s))
            self._ball_pop = PopMatrix()
        self._restack_overlays()
        self._place_bench_badge()
        self.tick_draw()

    # ------------------------------ 特效 ------------------------------
    def big_result_text(self, m, payout):
        """画布中央中奖大字: 缩放+淡出+上浮。

        ⚠️ 字号**必须过 `sp()`**: `Label(font_size=48)` 是裸物理像素 —— 桌面 density=1
           时正好, 手机 density 2.5~3 时只剩 16~19sp, 比旁边 18sp 的余额数字还小。
        """
        if self._ball_e is None:
            return
        if m > 0:
            text = "+%d" % payout
            hexcolor = slot_color(m)
            size = sp(48)
        else:
            text = "未中"
            hexcolor = COL_FIRE
            size = sp(36)
        # 手绘文字, 按可见宽缩字号(隐藏档 5000% 时 "+500000" 会比屏幕还宽)
        size = fit_font_size(text, size, max(80.0, float(self.width) * 0.94), True)
        # ⚠️ 走不了图集(未中 / 没烘到这一档)就**原样退回 Label**, 一个字都不改 ——
        #    `_glyph_quads` 对 `未中` / 含空格或斜杠的串返回 None 是**硬边界**(静默拼出来
        #    会字距错、宽度错, 而画面"看着差不多")。
        _grec = _glyph_rec(size, True)
        if _grec is not None and _glyph_quads(_grec, text) is not None:
            main = GlyphLabel(text=text, font_size=size, bold=True,
                              color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
            shadow = GlyphLabel(text=text, font_size=size, bold=True,
                                color=(0, 0, 0, 0.6), size_hint=(None, None))
        else:
            main = Label(text=text, font_size=size, bold=True,
                         color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
            main.bind(size=lambda w, _: setattr(w, "text_size", w.size))
            shadow = Label(text=text, font_size=size, bold=True,
                           color=(0, 0, 0, 0.6), size_hint=(None, None))
            shadow.bind(size=lambda w, _: setattr(w, "text_size", w.size))
            _tag_texupd(main, "结算大字")
            _tag_texupd(shadow, "结算阴影")
        # ⚠️ **缩放入场走 GPU `Scale`, 绝不再逐帧写 `font_size`**: `font_size` 属于 Kivy 的
        #    `_font_properties`, 每次赋值都会重新测量字形 + 重新光栅化 + 重建并上传纹理。
        #    `Scale` 只是乘进 modelview 矩阵, 纹理一次生成。基准纹理仍按 `size` 光栅化,
        #    不做"按峰值预放大"(放大 1.2 倍线性插值只是极轻微发虚, 缩小没 mipmap 会闪)。
        for _lb in (main, shadow):
            with _lb.canvas.before:
                PushMatrix()
                _lb._pop_sc = Scale(origin=_lb.center, x=1.0, y=1.0)
            # ⚠️⚠️ **PopMatrix 绝不能省**: Kivy 的 `Scale`/`Rotate` 是**持久变换**, 一进画布
            #    指令流就管到"下一次被改"为止, 不会自动弹栈。而本项目的子控件画布是**绝对
            #    (窗口)坐标、父级不做平移** ⇒ 挂在 `canvas.before` 上的 Scale 会**泄漏给它
            #    之后画的每一个控件**(也就是 HUD 五行), 大字活着的那 1.8s 里底部两行被按
            #    sc 缩放位移(峰值 1.2 时下推 ~80px), **整块推出窗口**。
            #    Label 没有子控件, 所以 `canvas.after` 正好落在它画完的那一刻。
            with _lb.canvas.after:
                PopMatrix()
        self.add_widget(shadow)
        self.add_widget(main)
        # 中奖时大字上移到杯顶之上(逻辑 cy = TEXT_CY_WIN; 杯子占逻辑 y ≈264.6~578,
        # 且大字要上浮 38px/s, 起跳点留足余量才不会被杯口压住)。未中不播杯子, 保持中央。
        cy_logical = (CH / 2.0 - 80.0) if m <= 0 else TEXT_CY_WIN
        # ⚠️ **横向不要再减 `self.x`**: 这里是 `_px()` 的返回值, 已经是 Kivy 窗口绝对坐标;
        #    再减一次等于把大字左移整整一个 `GameArea.x`。竖屏 `GameArea.x` 恒为 0 所以一直
        #    没人发现; **横屏反旋转时 x=370**(1740x1000), 大字就偏左 370px。
        #    纵向的 `- self.y` 是历史遗留(竖屏 y=122, 被眼睛调过的既成观感), 本轮不动。
        _cc = [_lbl_canvas_color(main), _lbl_canvas_color(shadow)]
        self._effects.append({"kind": "big", "ws": [main, shadow], "cols": _cc,
                              "born": time.time(),
                              "life": BIG_TEXT_LIFE, "size": size, "rgb": hex_rgb(hexcolor),
                              "cx": self._px(CW / 2.0),
                              "cy": self._py(cy_logical) - self.y})

    def pulse_slot(self, i):
        if 0 <= i < len(self._slot_cols):
            self._pulse = (i, time.time() + 0.30)

    def center_toast(self, text, hexcolor=COL_FIRE, size=26, life=2.0):
        """画布中央两行警示飘字(如余额不足): 上浮+淡出。size 单位是 sp(见 `big_result_text`)。

        ⚠️ 老 toast 未消失前不再弹新的(连点发射会瞬间叠一排); **创建即摆位到中央** ——
           默认 pos=(0,0) 是 GameArea 左下角, 下一帧才被 `tick_draw` 摆位, 连点时会在
           那里闪出"第二个提示"。
        """
        if self._ball_e is None:
            return
        for e in self._effects:
            if e["kind"] == "toast":
                return                            # 已有 toast 存活, 不重复弹
        # ⚠️ 这是**手绘**文字(自己 `size = texture_size`), 屏幕装不下就左右被切 ——
        #    实测 360dp + 1.3 倍系统字体: "弹珠数量已调整到1000个" 量出 408px 而屏只有 360px,
        #    左右各切掉 24px。所以按**可见宽**定字号。
        _seen = max(80.0, float(self.width) * 0.94)
        _fs = fit_font_size(text, sp(size), _seen, True)
        lbl = Label(text=text, font_size=_fs, bold=True, halign="center",
                    color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
        _tag_texupd(lbl, "飘字")
        lbl.texture_update()                      # 立刻出纹理, 尺寸跟文字(center 才摆得准)
        lbl.size = lbl.texture_size
        # ⚠️ 横向同样**不减 `self.x`** —— 理由见 `big_result_text` 处。
        cx = self._px(CW / 2.0)
        cy = self._py(CH / 2.0 - 40) - self.y
        lbl.center = (cx, cy)
        self.add_widget(lbl)
        self._effects.append({"kind": "toast", "ws": [lbl],
                              "cols": [_lbl_canvas_color(lbl)], "born": time.time(),
                              "life": life, "rgb": hex_rgb(hexcolor),
                              "cx": cx, "cy": cy})

    def _place_bench_badge(self):
        badge = self._bench_badge
        if badge is not None:
            badge.center = (self._px(CW / 2.0), self._py(CH / 2.0) - self.y)

    def show_bench_badge(self, text):
        """跑分状态固定在盘面中心: 大号红字, 不置灰、不加遮挡底板。"""
        badge = self._bench_badge
        display = text.replace("\n", "　·　")
        if badge is None:
            badge = Label(text=display, font_size=sp(20), bold=True, halign="center",
                          valign="middle", color=hex_rgb(COL_FIRE) + (1,),
                          size_hint=(None, None))
            _tag_texupd(badge, "跑分提示")
            badge.texture_update()
            badge.size = badge.texture_size
            self._bench_badge = badge
            self.add_widget(badge)
        elif badge.text != display:
            badge.text = display
            badge.texture_update()
            badge.size = badge.texture_size
        self._place_bench_badge()
        self._restack_overlays()

    def hide_bench_badge(self):
        badge = self._bench_badge
        self._bench_badge = None
        if badge is not None:
            try:
                self.remove_widget(badge)
            except Exception:
                pass

    def set_lamp(self, i, hex_color):
        if 0 <= i < len(self._lamp_cols):
            self._lamp_cols[i].rgb = hex_rgb(hex_color)

    def lamps_off(self):
        for col in self._lamp_cols:
            col.rgb = hex_rgb(COL_LAMP_OFF)

    # ------------------------------ 帧驱动 ------------------------------
    def tick_draw(self):
        """每帧只更新动态元素(ball/meter/plunger) + 特效, 不重排 canvas。"""
        # ⚠️ 必须放在下面的 `_ball_e is None` 早退**之前**: 否则画布还没建好的那几帧
        #    (启动/尺寸未定)中奖演出不推进, 起播时间会被白白拖后。
        self.win_fx.tick()
        if self._ball_e is None:
            return
        g = self.model
        now = time.time()
        _t_ball = time.perf_counter()
        b = g.ball
        if b is not None:
            br = BALL_R * BALL_VIEW
            bs = 2 * br * self._s
            # 受击压扁: 沿法线缩、切向胀, 渐回正圆
            sq = getattr(b, "squash", 1.0)
            if sq < 0.99:
                b.squash += (1.0 - sq) * 0.5
                if b.squash > 0.99:
                    b.squash = 1.0
                sq = b.squash
            self._ball_e.pos = (self._ox + (b.x - br) * self._s + bs * (1 - sq) * 0.5,
                                self._oyt - (b.y + br) * self._s + bs * (1 - sq) * 0.5)
            self._ball_e.size = (bs * (2 - sq), bs * sq)
            # 球自转(spin 是碰钉切向速度积分)
            self._ball_rot.angle = math.degrees(getattr(b, "spin", 0.0) % (2.0 * math.pi))
            self._ball_rot.origin = (self._ox + b.x * self._s, self._oyt - b.y * self._s)
            # ⚠️ 消费物理层的 peg_flash 信号后**必须清空**, 否则同一颗钉会反复重新点亮。
            pf = getattr(b, "peg_flash", None)
            if pf is not None and pf in self._peg_cols:
                self._peg_flash[pf] = now
                b.peg_flash = None
        _brk_add("板·球", _t_ball)
        _t_peg = time.perf_counter()
        # 钉子高亮动画: 60ms 电光金 + 240ms 渐回原色 + 半径微扩 1.2x
        for (px, py), t0 in list(self._peg_flash.items()):
            col = self._peg_cols.get((px, py))
            e = self._peg_ellipses.get((px, py))
            if col is None:
                del self._peg_flash[(px, py)]
                continue
            elapsed = now - t0
            if elapsed > 0.30:
                col.rgb = hex_rgb(COL_PEG)
                if e is not None:
                    r = PEG_R * self._s
                    e.size = (2 * r, 2 * r)
                del self._peg_flash[(px, py)]
            else:
                flash = (1.0, 0.898, 0.0)
                base = hex_rgb(COL_PEG)
                if elapsed < 0.06:
                    col.rgb = flash
                else:
                    f = (elapsed - 0.06) / 0.24
                    col.rgb = (flash[0] + (base[0] - flash[0]) * f,
                               flash[1] + (base[1] - flash[1]) * f,
                               flash[2] + (base[2] - flash[2]) * f)
                scale = 1.0 if elapsed < 0.06 else (1.2 - 0.2 * (elapsed - 0.06) / 0.24)
                if e is not None:
                    r = PEG_R * self._s * scale
                    e.size = (2 * r, 2 * r)
        _brk_add("板·钉闪", _t_peg)
        if g.power > 0.01:
            top = (SLOT_TOP - 8) - g.power * 200
            kw = self._rect(RIGHT_INNER - 9, top, RIGHT_INNER - 4, SLOT_TOP - 8)
            self._meter_fill.pos = kw["pos"]
            self._meter_fill.size = kw["size"]
            weak = g.power < MISFIRE_POWER
            self._meter_col.rgb = hex_rgb(COL_FIRE if weak else COL_METER)
        else:
            self._meter_fill.size = (0, 0)
        # 弹簧: 蓄力时跟随, 释放后阻尼振荡回弹(过冲→往复→停止)
        if g.state == "charging":
            self._spring_power = g.power
            self._spring_vel = 0.0
        elif abs(self._spring_power) > 0.0005 or abs(self._spring_vel) > 0.005:
            k, damp = 120.0, 3.2
            self._spring_vel += (-k * self._spring_power - damp * self._spring_vel) * FIXED_DT
            self._spring_power += self._spring_vel * FIXED_DT
        else:
            self._spring_power = 0.0
            self._spring_vel = 0.0
        sp = max(-0.25, self._spring_power)  # 过冲到 -0.25(回弹约 11px), 视觉明显
        # 弹簧 Z 字形: 上横线→斜线→下横线
        bar_top = PLUNGER_Y + BALL_VIS_R      # 球的下沿: 球"坐在弹簧上"而非陷进去
        bar_bot = bar_top + 9 + sp * 45
        lx = self._px(LANE_L + 5)
        rx = self._px(RIGHT_INNER - 5)
        y0 = self._py(bar_top)
        y1 = self._py(bar_bot)
        bars = self._spring_bars
        bars[0].points = [lx, y0, rx, y0]
        bars[1].points = [rx, y0, lx, y1]
        bars[2].points = [lx, y1, rx, y1]
        # 弹簧颜色: 哑火→红, 正常蓄力→灰蓝渐变至金黄
        weak = sp < MISFIRE_POWER
        if sp < 0.01:
            self._spring_bar_col.rgb = hex_rgb("#8fa0c4")
        elif weak:
            self._spring_bar_col.rgb = hex_rgb("#c45a4a")
        else:
            u = power_u(sp)
            r0, g0, b0 = 0x8f, 0xa0, 0xc4
            r1, g1, b1 = 0xff, 0xd7, 0x00
            r = int(r0 + (r1 - r0) * u)
            g = int(g0 + (g1 - g0) * u)
            b = int(b0 + (b1 - b0) * u)
            self._spring_bar_col.rgb = (r / 255.0, g / 255.0, b / 255.0)
        # 槽位白闪
        if self._pulse is not None:
            i, end = self._pulse
            if now >= end or i >= len(self._slot_cols):
                if i < len(self._slot_cols):
                    self._slot_cols[i].rgb = hex_rgb(slot_color(g.multipliers[i]))
                self._pulse = None
            else:
                self._slot_cols[i].rgb = (1, 1, 1)
        # 特效推进
        for e in list(self._effects):
            p = (now - e["born"]) / e["life"]
            if p >= 1.0:
                for w in e["ws"]:
                    self.remove_widget(w)
                self._effects.remove(e)
                continue
            if e["kind"] == "toast":
                alpha = max(0.0, 1.0 - max(0.0, p - 0.65) / 0.35)   # 前 65% 实色, 后 35% 淡出
                w = e["ws"][0]
                # ⚠️ **必须和"中奖大字"用同一套 alpha 量化**: `color` 是 Kivy
                #    `Label._font_properties` 之一,**赋一次值就重测字形 + 重光栅化 + 重建纹理**。
                #    优先走画布那条 `Color`(零重建, 每帧都写); 拿不到才退回写 `label.color` + 量化。
                if _fade_set((e.get("cols") or [None])[0], alpha):
                    pass
                else:
                    _q = int(alpha * BIG_TEXT_ALPHA_STEPS + 0.5) / float(BIG_TEXT_ALPHA_STEPS)
                    if _q != e.get("_qa"):
                        e["_qa"] = _q
                        w.color = e["rgb"] + (_q,)
                w.center = (e["cx"], e["cy"] + 20 * (now - e["born"]))
            else:
                if p < 0.5:
                    sc = 1.0 + (p / 0.5) * 0.2       # 前50%生命: 1.0→1.2 弹入
                else:
                    sc = 1.2 - ((p - 0.5) / 0.5) * 0.2  # 后50%: 1.2→1.0 慢收
                alpha = max(0.0, 1.0 - max(0.0, p - 0.55) / 0.45)
                rise = 38 * (now - e["born"])
                main, shadow = e["ws"]
                # ⚠️ 只在**量化后的 alpha 真的变了**时才写 `color` —— 见 BIG_TEXT_ALPHA_STEPS。
                # ⚠️ 优先走画布那条 `Color`(零重建, 见 `_lbl_canvas_color`); 两个 Label 的
                #    贴图颜色在**创建时**就已烘好(main 是倍率色、shadow 是半透明黑), 这里
                #    只需给它们乘一个 alpha。
                _cols = e.get("cols") or [None, None]
                # ⚠️ 走 `_fx_fade_set` 而**不是** `_fade_set`: 图集把阴影的基色 alpha(0.6)
                #    搬到了画布上, `_fade_set` 只写 alpha 会把 0.6 覆盖掉 ⇒ 阴影在淡出段比
                #    基线更黑。普通 `Label` 没有 `_glyph_alpha0`, 那一支与改动前完全一致。
                _ok = (_fx_fade_set(main, _cols[0], alpha) and _fx_fade_set(shadow, _cols[1], alpha))
                if not _ok:
                    # 兜底: 拿不到画布 Color 就退回老路, 绝不静默不淡出
                    _q = int(alpha * BIG_TEXT_ALPHA_STEPS + 0.5) / float(BIG_TEXT_ALPHA_STEPS)
                    if _q != e.get("_qa"):
                        e["_qa"] = _q
                        main.color = e["rgb"] + (_q,)
                        shadow.color = (0, 0, 0, _q * 0.6)
                main.center = (e["cx"], e["cy"] + rise)
                shadow.center = (e["cx"] + 2, e["cy"] + rise - 2)
                # 缩放 = 纯 GPU 变换(见 `big_result_text` 里 `_pop_sc` 处的说明)。
                # ⚠️ `origin` 必须是**写完 center 之后**的当前中心 —— 大字一边缩放一边上浮,
                #    锚点不跟就会看到"绕着一个飘走的点放大"。shadow 多偏 (2,-2), 各自锚自己。
                for _lb in (main, shadow):
                    _ps = _lb._pop_sc
                    _ps.origin = _lb.center
                    _ps.x = _ps.y = sc
