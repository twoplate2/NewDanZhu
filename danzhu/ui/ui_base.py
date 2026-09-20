"""UiMixin —— 布局 / 字号 / 控件构建(老版 `android/main.py` 13398-14138, 19815, 20939-21075)。

`RootWidget` 三块里的**壳的一部分**: 建控件树、尺寸与字号自适应、按钮底色、HUD 压暗。
游戏逻辑一行都没有 —— 全在 `danzhu/game.py`。所以本模块里只有两类东西:

  ① **纯 UI 状态**: `_ui_scale` / `_font_scale` / `bet_btns` / `_row_*` / 压暗矩形几何。
     只有这里写。
  ② **读 `Game` 的位**: 输入锁 / 选中档 / 注额 / 音效档 / 每轮次数 —— 一律 `getattr(self.game, ...)`,
     **不在这里镜像一份**(镜像 = 第二个状态源, 正是这个重构要消灭的)。

⚠️ `rtp_btns` 是 **`Game` 的字典**: 键(有哪些档)是 `Game` 维护的真源, 值(控件句柄)由
   `PlayMixin._add_rtp_button` 填进去。所以本文件一律走 `self.game.rtp_btns`,
   **不许再建一个同名的 UI 字典**(那正是"两处各存一份必然漂移"的形状)。

调用关系(都在别的 Mixin 里, 本文件不定义):
  · `PlayMixin` 的事件派发调 `_restyle_buttons()`(restyle_buttons) / `_set_controls_enabled()`
    (controls) / `_apply_sizes()` / `_sync_hud_dim()`; `_show_round_settings()` /
    `_refresh_mute_btn()` / `_refresh_stats()` / `_add_rtp_button` 也在那边。
"""

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ..config import (BTN_OFF_DIM, CH, COL_BALL, COL_BTN, COL_BTN_LOCK,
                      COL_BTN_OFF, COL_FIRE, COL_GRAY, COL_GREEN, COL_METER,
                      COL_MUTE_OFF, COL_PANEL, COL_SUB, COL_TEXT, CW, DIM_RGB,
                      H_BETS, H_BOTTOM, H_INFO, H_RTP, H_TOP, HUD_ALPHA,
                      PRESETS, _TW_GROW_MAX, dim_rgb, hex_rgb)
from ..rules import _line_color
from .game_area import GameArea
from .text import (_COLD_FS_BASE, _COLD_FS_TAG, _FRAME_FIT, _TINT_BRIGHT,
                   _TINT_DIM, _set_lbl_tint, _tag_texupd, fit_font_size,
                   text_px)
from .widgets import RotPopup


class UiMixin(object):

    # ------------------------------ 尺寸 ------------------------------
    def _veq(self):
        """等效竖屏窗口尺寸: 横屏反旋转时 = (短边, 长边), 画面构图与竖拿时一致。
        竖屏时 = 物理窗口尺寸, 行为不变。所有布局计算一律吃等效值,
        物理窗口只用来算旋转(LandLayer)。"""
        w, h = Window.width, Window.height
        return (w, h) if w <= h else (h, w)

    def _fit_width(self):
        """内容最大宽度 = 让 520:660 场景恰好填满可用高度。
        窄屏(手机竖屏)直接铺满宽度; 宽屏(16:10 桌面)内容列居中、两侧留深色边。
        尺寸取"等效竖屏窗口"(横屏反旋转时短边x长边), 横竖屏布局同一套。"""
        vw, vh = self._veq()
        self._ui_scale = min(1.0, vh / dp(680))
        us = self._ui_scale
        # 缩放后的固定高度(行高+间距), 与 _apply_sizes() 一致
        scaled_fixed = (dp(H_TOP + H_RTP + H_BETS + H_INFO + H_BOTTOM) * us
                        + dp(10) * 5 * us * us + dp(12) * us)  # +底部留白
        avail_h = max(100.0, vh - scaled_fixed)
        want = avail_h * (CW / CH) + dp(8)
        self.width = min(vw, want)
        self._font_scale = min(1.0, self.width / dp(360)) # 宽度缩放因子: 窄屏时字体等比缩小

    # ------------------------------ 控件工厂 ------------------------------
    def _mk_label(self, text, font_size, hexcolor, halign="left", bold=False, **kw):
        lbl = Label(text=text, font_size=font_size, bold=bold,
                    color=hex_rgb(hexcolor) + (1,), halign=halign, valign="middle", **kw)
        lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        return lbl

    def _mk_balance_lbl(self, text):
        """余额行 —— 走**字形图集**(`GlyphLabel`)⇒ 零 `填纹`; 建不起来就逐字退回普通 `Label`。

        ⚠️ 为什么**只有这一行**能走: `_GLYPH_CHARS = "0123456789+"` 只有数字和加号, 而余额是
           纯数字。`stats_lbl`(「累计N投N中」)含汉字 ⇒ `_glyph_quads` 必返回 `None` ⇒ 走不了。
           日志里「重建来源」是 **余额 9 · 统计 5** ⇒ 这一行就是那 9 次。
        ⚠️⚠️ **绝不在这里预检图集**(`if _glyph_rec(...) is not None`) —— 本行在**布局期**就建好,
           而图集是启动后**分帧**预热的 ⇒ 那一刻必然还没烘出来, 预检等于**永远退回 Label**,
           而且不报错(收益静默归零)。
        ⚠️ `wait_bake=True` 是这条路的**前提**: 让 `GlyphLabel` 在图集没就绪时**等**而不是
           **永久退化**(`_degrade` 回不去)。预热跑完会 `_glyph_flush_waiters()` 叫醒它。
           三条分岔(还没烘完 ⇒ 等 / 已烘完仍没有 ⇒ 退化 / 等超时 ⇒ 兜底退化)全在
           `GlyphLabel._glyph_sync` 里。
        ⚠️ `fit_box=True` = 排在"控件矩形"里, 与老路 `halign='left'` + `text_size=size` 同位 ——
           就是 `GlyphLabel.fit_box` 注释里点名"余额用"的那一档。
        """
        try:
            from .widgets import GlyphLabel
            return GlyphLabel(text=text, font_size=sp(19), bold=True,
                              color=hex_rgb(COL_BALL) + (1,), fit_box=True,
                              wait_bake=True, size_hint_x=None, width=dp(80))
        except Exception:
            return self._mk_label(text, "19sp", COL_BALL, "left", True,
                                  size_hint_x=None, width=dp(80))

    def _fit1(self, w, base=None, inset=0.0):
        """把控件 w 的字号调到"当前文字**恰好一行**放得下"的最大档, 返回实际字号。

        `base` = 这一档布局的**基准字号**(由 `_apply_sizes` 按 `sp(N)*_font_scale*_ui_scale`
        算出来传进来); 不传就用上次记下的 / 当前字号 —— 于是文字一变(状态栏、余额、
        统计)也会自动重挑, 不需要每个赋值点都记得调。

        ⚠️ 重入闸是必须的: 这个函数会写 `font_size`, 而 `font_size` 变 → `texture_size` 变,
        绑了 texture_size 的地方(弹窗里那些自动撑高的行)会再回调回来。没有闸就是死循环。
        ⚠️ 只改字号、**不改宽度** —— 宽度归 `_apply_sizes` / BoxLayout 管, 两边都改就会打架。
        """
        if getattr(w, "_fit_busy", False):
            return float(w.font_size)
        # 记一笔"真的叫了一次"(闸门之上早退的不算 —— 那不是活, 是防重入)。
        _FRAME_FIT[0] += 1
        # 给"冷字号"归因用: 谁在挑字号。
        _COLD_FS_TAG[0] = getattr(w, "_texupd_tag", None) or type(w).__name__
        # 记下这次用的基准 —— 冷字号的比值(fs/基准)靠它才算得出来。
        try:
            _COLD_FS_BASE[0] = float(getattr(w, "_fit_base", 0.0) or 0.0)
        except Exception:
            _COLD_FS_BASE[0] = 0.0
        if base is not None:
            w._fit_base = float(base)
        # inset 记在控件上: 挂在 width 上的自动重挑(`_install_fit`)也要用同一个内缩量,
        # 否则"布局重排"和"文字变化"两条路会算出**两个**字号, 同一个按钮一会儿大一会儿小。
        if inset:
            w._fit_inset = float(inset)
        else:
            inset = float(getattr(w, "_fit_inset", 0.0))
        b = getattr(w, "_fit_base", None)
        if b is None:
            b = float(w.font_size)
            w._fit_base = b
        w._fit_busy = True
        try:
            pad = getattr(w, "padding", [0, 0, 0, 0])
            avail = max(1.0, w.width - pad[0] - pad[2] - inset)
            fs = fit_font_size(w.text or "", b, avail,
                               bool(getattr(w, "bold", False)))
            if abs(float(w.font_size) - fs) > 0.01:
                w.font_size = fs
        finally:
            w._fit_busy = False
        return float(w.font_size)

    def _fit_uniform(self, rows, base):
        """一组**同格式**的行共用一个字号: 按最宽那条算档, 全体照用。

        ⚠️ 逐行各自 `_fit1` 会得出**好几个**字号 —— 实测 12 行历史里出现了
        34.0 / 31.96 / 30.0 三种, 玩家一眼就发现「同一个 log 为什么不一样」。
        ⚠️⚠️ 度量用的 bold 取"组里只要有一个粗体就按粗体量", **不许**按"最宽那条自己的
           bold": 预热表是按每个标签自己的 bold 烘字号的, 而 `_longest` 随文字换人 ⇒
           同一组今天用粗体量、明天用细体量, 撞上没烘过的组合就是一次 **21.7 毫秒的冷
           字体表打开**(实测真机两帧因此越过 11.11 毫秒那条线)。按粗体量则只会用到
           bold=True 那套(一定烘过) —— **零预热成本**, 且粗体更宽 ⇒ 也顺带防了细体定档时
           粗体那行溢出。
        """
        rows = [w for w in rows if w is not None]
        if not rows:
            return rows

        def _go(*_a):
            avail = min(float(getattr(w, "width", 0.0) or 0.0) for w in rows)
            if avail <= 1.0:
                return
            _bd = any(bool(getattr(w, "bold", False)) for w in rows)
            _COLD_FS_TAG[0] = "同类行 x%d" % len(rows)
            _longest = max(rows, key=lambda w: text_px(w.text or "", base, _bd))
            fs = fit_font_size(_longest.text or "", base, avail, _bd)
            for w in rows:
                if abs(float(w.font_size) - fs) > 0.01:
                    w.font_size = fs

        for w in rows:
            w._fit_base = float(base)
            w.bind(width=lambda *_a, _g=_go: _g())
        _go()
        return rows

    def _fit_buttons_uniform(self, btns, base, inset=0.0):
        """一排按钮**共用一个字号**: 按最宽那个标签定档, 全体照用。

        ⚠️ 逐个小 `_fit1` 会让长标签缩得更多 —— 实测 360dp 解锁隐藏档后, 返还率那一排
        出现 **24 / 19 / 16px 三种墨迹高**("80%"最大、"5000%"最小), 玩家一眼就看出来不齐。
        与列表行是同一个道理(见 `_fit_uniform`), 只是按钮还要扣掉左右内缩 `inset`。
        ⚠️ 度量 bold 与 `_fit_uniform` **同一条规矩**: 组里只要有一个粗体就按粗体量。
        """
        btns = [b for b in btns if b is not None]
        if not btns:
            return
        avail = min(max(1.0, float(b.width) - inset) for b in btns)
        _bd = any(bool(getattr(b, "bold", False)) for b in btns)
        _COLD_FS_TAG[0] = "同类钮 x%d" % len(btns)
        longest = max(btns, key=lambda b: text_px(b.text or "", base, _bd))
        fs = fit_font_size(longest.text or "", base, avail, _bd)
        for b in btns:
            b._fit_base = float(base)
            b._fit_inset = float(inset)
            if abs(float(b.font_size) - fs) > 0.01:
                b.font_size = fs

    def _install_fit(self, *ws):
        """给单行控件挂上"文字一变就重挑字号"的钩子(宽度归布局管, 也一起绑)。

        挂在 `text` 上而不是在 20 个赋值点各调一次 —— 那样迟早漏掉一个, 而漏掉的表现
        就是"某个状态又折行了", 只有截图才看得见。"""
        for w in ws:
            if w is None or getattr(w, "_fit_installed", False):
                continue
            w._fit_installed = True
            w._fit_base = float(w.font_size)

            def _go(_w, *_a):
                self._fit1(_w)
            w.bind(text=_go, width=_go)
        return ws

    def _mk_button(self, text, cb, bg=COL_BTN_OFF):
        b = Button(text=text, background_normal="", background_down="",
                   background_color=hex_rgb(bg) + (1,), color=(1, 1, 1, 1),
                   font_size="16sp", bold=True)
        if cb is not None:
            b.bind(on_release=cb)
        return b

    def _fit_w(self, base_w):
        """宽屏(平板)上把弹窗里的固定宽块按可用宽度等比放大, 返回 `(可用宽度, 缩放系数)`。

        ⚠️⚠️ **只在宽屏放大** —— 手机上可用宽度(约 388dp)本来就大于表格(288dp), 无脑
           按比例放大会把手机侧本来就不错的观感一起改掉。门槛用 Android 的 `sw600dp`
           惯例: 等效竖屏窗口宽 > 600dp 才算平板, 否则只 `min(1.0, ...)` **收缩** ——
           360dp 那档小屏靠它防表格溢出弹窗(`size_hint_x=None` 的子控件宽度不够时
           **不会自己缩**, 会直接飘到弹窗外)。
        ⚠️ 两处调用点必须**共用这一个口径**, 否则两台设备上会算出不同的留白。
        """
        _vw = self._veq()[0]
        _avail = _vw * 0.96 - dp(38)          # 弹窗宽 0.96*vw - content padding 16*2 - 余量 6
        if base_w <= 0:
            return _avail, 1.0
        if _vw <= dp(600):
            return _avail, min(1.0, _avail / base_w)
        return _avail, min(_TW_GROW_MAX, _avail / base_w)

    def _popup(self, hint_w, h_dp, **kw):
        """统一建 Popup(RotPopup: 横屏挂旋转层随画面转, 坐标系统一为等效竖屏窗口)。

        ⚠️ 宽高都必须按**等效竖屏窗口**(`_veq()` = 短边x长边)算成绝对值, 不能用
        size_hint —— size_hint 取的是父容器的**原始**宽高, 而设备横拿时窗口是横的
        (实测 960x540 下界面宽 540 / 弹窗 806), 这就是"横屏启动、一开设置窗口宽度
        就是错的"的根因。`_veq()` 会排序, 所以方向尚未落定时也拿得到竖构图那一边。
        ⚠️ `title_align` 必须默认 "center": Kivy 的 `Popup.title_align` **默认是 'left'**,
        不覆盖的话标题贴左边缘, 而主界面标题栏是严格居中的(调用方显式传值仍可覆盖)。
        """
        vw, vh = self._veq()
        kw["size_hint"] = (None, None)
        kw["width"] = hint_w * vw
        kw["height"] = min(dp(h_dp), vh * 0.92)
        kw.setdefault("title_align", "center")
        return RotPopup(**kw)

    def _fit_line(self, lb, base_sp=None):
        """弹窗/正文里的**单行**标签: 自动挑字号保证一行(装不下就缩, 绝不折行)。

        与 `_mk_label` 那条路分开写: 那条绑的是 `size→text_size`(两维都给), 这里必须只给
        宽度、高度留 None, 否则 Kivy 会把文字排版进一个**固定高度**里 —— 折行后的第二行
        就画不出来了(定高裁切, v0.6.12/v0.6.20 栽过两次)。"""
        if base_sp is not None:
            lb.font_size = sp(base_sp)
        lb._fit_base = float(lb.font_size)
        lb.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        self._install_fit(lb)
        return lb

    def _auto_h(self, lb, h0=0.0, extra=0.0):
        """多行正文标签: 高度跟着**真实排版**走 —— 折行只会让弹窗长高, 不会把字裁掉。

        `h0` 是单行时的最小高度。绑 `width→text_size`(**不能绑 size**: 绑 size 会死循环),
        再把 `texture_size[1]`(排版后的真实高度)写回 height。"""
        lb.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))

        def _sync(*_a):
            # `_auto_cap` 由 `_popup_fit_content` 在"内容装不下"时写进来(见那里的说明):
            # 有它就把高度**压到它以下**(代价是少显示几行), 没有就按真实排版撑开。
            _cap = float(getattr(lb, "_auto_cap", 0.0) or 0.0)
            _h = max(h0, lb.texture_size[1] + extra)
            lb.height = min(_h, _cap) if _cap > 0 else _h
        lb._auto_sync = _sync
        lb._auto_min_h = h0
        lb._auto_base = float(lb.font_size)      # 装不下时按这个基准缩字号(见 _popup_fit_content)
        lb.bind(texture_size=_sync)
        _sync()
        return lb

    def _popup_fit_content(self, popup, content, tries=2):
        """开完之后把弹窗高度改成**内容真实需要的高度**(只长不缩到 96% 视口以内)。

        ⚠️ 不能靠写死的 `h_dp` 估: 同一个弹窗在 360dp 机器上、在系统字体 1.3 倍下, 排版
        高度差 40%~100%(实测跑分说明 170 → 420), 估小了内容就被顶出弹窗外面。
        ⚠️ 非内容区(标题栏 + 分隔条 + 外壳内边距)不写死常数, 而是**量**出来, 与 Kivy
        版本的 kv 结构无关。
        ⚠️ 必须等一帧: `content.minimum_height` 要等子控件宽度落定(自动撑高的那些标签
        是先知道宽度、再算出高度的)。"""
        def _refit(*_):
            try:
                _vw, _vh = self._veq()
                # ⚠️ 非内容区**绝不能**用 `popup.height - content.height` 反推: 在**同一个
                #    tick 里** content.height 还是上一轮布局的旧值 —— 实测 chrome 因此被算成
                #    14 而不是 44(彩蛋弹窗高度 220px 而内容要 206px, 分隔线**压在标题字上**)。
                #    改成**按结构直接算**(GridLayout 内边距 + 除 container 外的孩子高度):
                #    只取决于 kv 结构 ⇒ 幂等, 调一次和调十次结果相同。
                chrome = getattr(popup, "_fit_chrome", None)
                if chrome is None:
                    _cont = content.parent
                    _grid = _cont.parent if _cont is not None else None
                    if _grid is not None and hasattr(_grid, "padding"):
                        _pad = _grid.padding
                        chrome = (_pad[1] + _pad[3]
                                  + sum(c.height for c in _grid.children if c is not _cont))
                    else:
                        chrome = max(0.0, popup.height - content.height)   # 兜底
                    popup._fit_chrome = chrome
                # ⚠️ 上限 0.96(不是 0.92): 真正的要求只是"弹窗要放得上屏幕", 0.92 是拍的余量
                #    —— 代价实测: 360dp + 1.3 倍字体下说明高出 11px 被压掉一行, 而缩字号
                #    救不了(行高按行算, 缩 2.6% 高度一像素不变)。
                _cap = _vh * 0.96
                if content.minimum_height + chrome > _cap:
                    # ⚠️ 装不下的时候**不能就这么封顶**: Kivy 不裁剪控件, 多出来的那截会被
                    #    竖排 BoxLayout 摆到**弹窗外面**(实测弹窗标题整行飘到屏幕外) ——
                    #    做法是把"自动撑高"的那几个标签按比例压低, 让总高塞进上限: 宁可
                    #    少显示两行(裁在面板里), 也不飘出去。
                    _autos = [c for c in content.children if hasattr(c, "_auto_sync")]
                    _tot = sum(float(c.height) for c in _autos)
                    _over = (content.minimum_height + chrome) - _cap
                    if _autos and _tot > 0:
                        # 先**缩字号**(字全都在, 只是小一点) —— 比"裁掉几行"对玩家友好。
                        # 字号变了纹理要下一帧才重排, 所以这一帧先按比例把高度压住当保险;
                        # 下一帧 `_refit` 再跑时 `minimum_height` 已经变小, 若够用就不再压。
                        _k = max(0.55, min(1.0, (_tot - _over) / _tot))
                        for _a in _autos:
                            _b = float(getattr(_a, "_auto_base", 0.0) or 0.0)
                            if _b > 0 and _k < 0.999:
                                _a.font_size = max(6.0, _b * _k)
                        _room = max(0.0, _tot * _k)
                        for _a in _autos:
                            _a._auto_cap = max(
                                float(getattr(_a, "_auto_min_h", 0.0)),
                                _room * (float(_a.height) / _tot))
                            _a._auto_sync()
                else:
                    # ⚠️ 钳位**只设不清**是个陷阱: 第一帧压过之后, 即使下一帧字号已经缩小、
                    #    内容真的够了, 旧钳位还留着继续裁(实测 1.3 倍字体下跑分菜单的说明
                    #    被裁掉 11px 一直不恢复)。够用就释放。
                    for _c in content.children:
                        if getattr(_c, "_auto_cap", 0.0):
                            _c._auto_cap = 0.0
                            _sync = getattr(_c, "_auto_sync", None)
                            if _sync is not None:
                                _sync()
                popup.height = min(content.minimum_height + chrome, _cap)
            except Exception:
                pass
        for _i in range(max(1, tries)):
            Clock.schedule_once(_refit, 0.0 if _i == 0 else 0.06)

    def _row_bg(self, row, hexcolor):
        with row.canvas.before:
            Color(*hex_rgb(hexcolor))
            row._bg_rect = Rectangle(pos=row.pos, size=row.size)
        row.bind(pos=lambda w, *_: setattr(w._bg_rect, "pos", w.pos),
                 size=lambda w, *_: setattr(w._bg_rect, "size", w.size))

    # ------------------------------ HUD 压暗 ------------------------------
    def _build_hud_dim(self):
        """建"装杯期压暗"的两块矩形 —— **整块界面减去游戏区**, 挂在 `RootWidget.canvas.after`。

        上块 = 游戏区上沿→本控件上沿(顶栏/返奖档/投注档 + 它们之间与上方的空白);
        下块 = 本控件下沿→游戏区下沿(信息行/底行 + 它们之间与底部的留白)。两块在游戏区
        处严丝合缝地断开, 所以**碰不到 GameArea 里的中奖大字**("大字留上方"是用户定案的)。

        ⚠️ 为什么是"整块减去游戏区", 而不是"每行各盖一块"或"每行一块、相邻相接":
        五行之间有 `spacing=dp(10)`、底部有 `padding` 12dp, 那些地方是**窗口背景**,
        没有任何控件去盖 —— 只盖行本身会让那几条缝成为全屏最亮的东西(实测缝 20.6 vs
        相邻被压暗的行 16.8~19.6; 横屏反旋转时是 **6 条贯通全高的竖亮线**)。而块与块
        一旦重叠, 0.68 叠 0.68 会变成 0.90, 缝就从"更亮"翻成"更暗"。按区域盖则行高/
        spacing/padding 怎么改都不会漏。

        **横向要一直铺到视口边**: `_fit_width` 让 `self.width = min(vw, want)`, 宽窗口下
        RootWidget 比视口窄(左右留深色边), 只盖 `self.width` 会在演出期两侧留两条亮带。
        """
        with self.canvas.after:
            self._hud_dim_col_top = Color(DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], 0.0)
            self._hud_dim_top = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
            self._hud_dim_col_bot = Color(DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], 0.0)
            self._hud_dim_bot = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
            # Kivy 的 Color 是**全局状态**: 块里的近黑+alpha0 会泄漏给之后画的兄弟/子控件。
            # 本作目前每条绘制路径都自己先写 Color, 所以没发作; 这里补一刀白色打底,
            # 以后谁新增一条"不自己设色"的绘制也不会静默变成全黑。
            Color(1.0, 1.0, 1.0, 1.0)
        self._hud_dim_cols = [self._hud_dim_col_top, self._hud_dim_col_bot]
        self._hud_dim_last = -1.0
        self.bind(pos=self._relayout_hud_dim, size=self._relayout_hud_dim)
        self.game_area.bind(pos=self._relayout_hud_dim, size=self._relayout_hud_dim)
        self._relayout_hud_dim()

    def _relayout_hud_dim(self, *_):
        """把两块压暗矩形摆到"整块**屏幕**减去游戏区"的位置(尺寸一变就跟着走)。

        ⚠️ 视口取 **`self.parent`(App.build 里那个 AnchorLayout)**, 不按 self 的 pos/width 反推。
        Kivy 是**单一 window 坐标系**, 那个 AnchorLayout 的矩形**恰好就是等效视口**(横屏
        反旋转时它铺满整块物理屏)。实测按 `x0=-self.x, w=self.width+2*self.x` 反推时:
        1400x1000 横屏窗上压暗只盖到物理 x∈[0,1277], **右边 122px 和顶边 56px 原样亮着**;
        且 `game_area.y = -78 < 0` 时下块高度被 `max(0.0, ga.y)` 算成 **0**。读父容器矩形
        之后两个毛病一起消失(下块的纵向范围也跟着视口走, 不再被 0 截断)。
        """
        vp = self.parent
        if vp is None or vp.width <= 1.0 or vp.height <= 1.0:
            vp = self                       # 未挂父/尺寸未定: 退回自身(探针夹具走这条)
        ga = self.game_area
        x0, y0, w, h = vp.x, vp.y, vp.width, vp.height
        ga_lo = min(max(ga.y, y0), y0 + h)
        ga_hi = min(max(ga.y + ga.height, y0), y0 + h)
        self._hud_dim_geo = ((x0, ga_hi, w, max(0.0, y0 + h - ga_hi)),
                             (x0, y0, w, max(0.0, ga_lo - y0)))
        # ⚠️ **构造期必须无条件写完整几何**: `fx_probe [11]` 就是在"刚建完、还没跑过一帧"
        #    这个状态下读这四块矩形的 pos/size 来断言"恰好盖满整屏减游戏区"的。
        #    跑起来之后才交给 `_paint_hud_dim` 按当前 alpha 决定"铺满"还是"收到 0"。
        self._paint_hud_dim(max(0.0, self._hud_dim_last),
                            force_geo=(self._hud_dim_last < 0))

    def _paint_hud_dim(self, a, force_geo=False):
        """按当前压暗强度摆这两块矩形。

        ⚠️ **a <= 0 时把矩形收到 0 尺寸, 而不是只把 alpha 写成 0**: alpha=0 的矩形仍然
        要走一遍混合填充, 而它盖的是"整块屏幕减去游戏区"(竖屏下约 35% 屏面积) ——
        真机 2560x1600 每帧白烧约 140 万像素混合、~115 Mpix/s(约占 Adreno 730 填充预算
        的 6~11%)。这条是"去掉确凿的白工", 不是"实测更快的优化"(桌面测不出收益)。
        """
        for col, geo, rect in ((self._hud_dim_cols[0], self._hud_dim_geo[0],
                                self._hud_dim_top),
                               (self._hud_dim_cols[1], self._hud_dim_geo[1],
                                self._hud_dim_bot)):
            col.rgba = (DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], a)
            rect.pos = (geo[0], geo[1])
            rect.size = (geo[2], geo[3]) if (force_geo or a > 0.0) else (0.0, 0.0)

    def _sync_hud_dim(self):
        """每帧把压暗块对齐到装杯演出的生灭曲线。

        真源只有一个: `WinPileFX.dim_alpha()`(板面本帧真正画上去的那个数)。这里**不重算
        任何曲线** —— 两边各写一份必然脱钩(本仓库踩过一次: 兜底时刻硬编码 0.45, 尾巴从
        0.45 改到 0.60 时静默脱钩, 兜底提前触发把数字剧透了)。
        `_hud_dim_last` 短路是性能考虑: 演出之外这一层恒为 0, 不必每帧去动那两个 Color。
        """
        # 跑分时信息栏承担进度提示, 不能再被中奖装杯的 HUD 压暗层盖灰。
        if getattr(self, "_bench_running", False):
            if self._hud_dim_last != 0.0:
                self._hud_dim_last = 0.0
                self._paint_hud_dim(0.0)
            return
        a = self.game_area.win_fx.dim_alpha(HUD_ALPHA)
        if a == self._hud_dim_last:
            return
        self._hud_dim_last = a
        self._paint_hud_dim(a)

    # ------------------------------ 控件树 ------------------------------
    def _build_ui(self):
        # 顶栏: [左边两个按钮(定宽)] [标题(弹性·居中)] [状态(弹性·右对齐)]
        # ⚠️ 左右两块**强制等宽**, 标题才落在屏幕正中 —— 两边不等时窗口越宽标题越偏:
        #    实测 540dp 的桌面窗**偏 38.5px**。代价: 左边两个按钮的宽得由这份份额反算
        #    (见 `_apply_row_budget`), 上限是设计值。
        # ⚠️ 左边**必须显式定宽**, 不能让它做弹性块: 两个弹性块按 Kivy 的规矩**平分**剩余宽,
        #    而 left_box 的内容要 142dp —— 实测 400dp 机器只分到 107(右溢 35dp)、360dp 只
        #    分到 87(右溢 55dp), 溢出的按钮去盖标题。
        top = BoxLayout(size_hint_y=None, height=dp(H_TOP),
                        padding=[dp(10), dp(4), dp(10), dp(4)], spacing=dp(6))
        self._row_top = top
        self._row_bg(top, COL_PANEL)
        left_box = BoxLayout(spacing=dp(6))
        self._top_left = left_box
        self.mute_btn = self._mk_button("", lambda _b: self.toggle_mute())
        self.mute_btn.size_hint_x = None
        self.mute_btn.width = dp(58)
        self.mute_btn.font_size = "13sp"
        # ⚠️ 打标才进文字纹理缓存(不打标 = 每次开关音效都重建一次纹理)
        _tag_texupd(self.mute_btn, "音效按钮")
        self._refresh_mute_btn()
        left_box.add_widget(self.mute_btn)
        self.round_btn = self._mk_button("每轮%d次" % self.game.max_plays,
            lambda _b: self._show_round_settings(), bg=COL_GREEN)
        self.round_btn.size_hint_x = None
        self.round_btn.width = dp(62)
        self.round_btn.font_size = "13sp"
        _tag_texupd(self.round_btn, "轮次按钮")
        self.round_btn.color = (0, 0, 0, 1)          # 黑字配绿底
        left_box.add_widget(self.round_btn)
        top.add_widget(left_box)
        self.title_lbl = self._mk_label("跳跳的弹珠机", "18sp", COL_TEXT, "center", True,
                                        size_hint_x=None, width=dp(120))
        top.add_widget(self.title_lbl)
        # 状态装在**弹性容器**里(与左边等宽), "右对齐"才是相对那一半而言。
        right_box = BoxLayout()
        self._top_right = right_box
        self.status_lbl = self._mk_label("按住蓄力发射", "13sp", COL_SUB, "right", False)
        _tag_texupd(self.status_lbl, "状态栏")
        right_box.add_widget(self.status_lbl)
        top.add_widget(right_box)
        # 文字一变就重挑字号(状态栏在整局里会换成十来种文案, 逐个赋值点去调必漏)
        self._install_fit(self.title_lbl, self.status_lbl, self.mute_btn, self.round_btn)
        self.add_widget(top)
        # ---- 设定区(左对齐, 不撑满) ----
        # 返还率行: 返还率 + 三档(固定宽)
        # ⚠️ 左右空白是**宽度预算的一部分**: padding 收到 14/10(右边那 24dp 纯粹白扔,
        #    内容本来左对齐)之后, 360dp 机器上每个档位按钮从 45.3dp 变成 52.3dp。
        rtp = BoxLayout(size_hint_y=None, height=dp(H_RTP),
                        padding=[dp(14), dp(4), dp(10), dp(4)], spacing=dp(5))
        self._row_rtp = rtp
        self._rtp_title_lbl = self._mk_label("期望返还比例：", "14sp", COL_TEXT, "left", False,
                                      size_hint_x=None, width=dp(115))
        # ⚠️ 打标才进缓存: 这个标签**文字永不变**、变的只是 `.color`, 而 Kivy 把颜色
        #    烘进纹理 —— 不打标就是每发球禁用/启用各改一次 = 每发球重建。
        _tag_texupd(self._rtp_title_lbl, "返还率标题")
        # 染色基色: 纹理烘成 COL_TEXT(最亮), 亮/暗两态靠画布乘系数 —— 见 `_tint_from`。
        self._rtp_title_lbl._tint_base_rgba = hex_rgb(COL_TEXT) + (1,)
        self._rtp_title_lbl._tint_key = "sub"
        rtp.add_widget(self._rtp_title_lbl)
        # ⚠️ **这个"右侧留空"的 Widget 必须先加**(在按钮之前加进 `rtp`) —— `_add_rtp_button`
        #    靠它定位: 把新按钮插到它**左边**(Kivy 的 `children[0]` 在最右, "更大 index = 更左")。
        #    先建按钮的话它找不到 spacer, 会退到 idx=1, 按钮全跑到标题**左边**(实测踩过:
        #    界面变成「80% 120% 200% 360%   期望返还比例：」, 截图才发现, 单测测不出)。
        self._rtp_spacer = Widget()
        rtp.add_widget(self._rtp_spacer)
        # ⚠️ **不在这里建 `self.rtp_btns`** —— 那个字典是 `Game` 的(键 = 有哪些档的真源,
        #    值 = 控件句柄, 由 `_add_rtp_button` 填)。建第二份就是第二个状态源。
        self._rtp_row = rtp
        for label, val in self.game.RTP_TIERS:   # 常驻档; 隐藏档靠长按解锁, 见 unlock_rtp
            self._add_rtp_button(label, val)
        self.add_widget(rtp)
        # 投入行: 投入弹珠单位 + 1/10/50/100(固定宽)
        bets = BoxLayout(size_hint_y=None, height=dp(H_BETS),
                         padding=[dp(14), dp(4), dp(10), dp(4)], spacing=dp(5))
        self._row_bets = bets
        self._bet_title_lbl = self._mk_label("每次投入弹珠：", "14sp", COL_TEXT, "left", False,
                                       size_hint_x=None, width=dp(115))
        # ⚠️ 打标才进缓存。这个标签**文字永不变**, 变的是 `.color` —— 理由同上。
        _tag_texupd(self._bet_title_lbl, "投注标题")
        self._bet_title_lbl._tint_base_rgba = hex_rgb(COL_TEXT) + (1,)
        self._bet_title_lbl._tint_key = "sub"
        bets.add_widget(self._bet_title_lbl)
        self.bet_btns = {}
        for v in PRESETS:
            b = self._mk_button(str(v) + "个", lambda _b, x=v: self.set_bet(x))
            b.size_hint_x = None
            b.width = dp(56)
            self.bet_btns[v] = b
            bets.add_widget(b)
        bets.add_widget(Widget())   # 右侧留空
        self.add_widget(bets)
        # 游戏区(全宽)
        # ⚠️ 传的是 `self`(RootWidget, 界面壳), **不是** `self.game` —— 老版就是 `GameArea(self)`。
        #    `area.game` 有 6 处外部读者(winfx 取 sfx/bet、text._glyph_warm_step 走标签树),
        #    它们要的是**宿主**; 传 Game 进去会让 `getattr(g,'sfx',None)` 静默返回 None ——
        #    实测 `WinPileFX._bounce` 因此丢掉了 15 声落球音(装杯期间一个都不响)。
        self.game_area = GameArea(self)
        self.add_widget(self.game_area)
        # ---- 信息区 ----
        # 信息行: 弹珠 + 累计x投x中(x%)
        info = BoxLayout(size_hint_y=None, height=dp(H_INFO))
        self._row_info = info
        # 与上面两行同一条左竖线(14dp) —— 写别的值就与那两行的左沿对不齐(旧值 dp(24)
        # 号称"对齐重置按钮", 而重置按钮实际起于 6+12=18, 从来没对齐过任何东西)。
        info.add_widget(Widget(size_hint_x=None, width=dp(14)))
        self._bead_lbl = self._mk_label("弹珠：", "15sp", COL_TEXT, "left", True,
                                       size_hint_x=None, width=dp(48))
        info.add_widget(self._bead_lbl)
        self.balance_lbl = self._mk_balance_lbl(str(self.game.balance))
        _tag_texupd(self.balance_lbl, "余额")
        info.add_widget(self.balance_lbl)
        self.stats_lbl = self._mk_label("", "15sp", COL_TEXT, "center", True,
                                        size_hint_x=0.70)
        _tag_texupd(self.stats_lbl, "统计")
        self.stats_lbl._tint_base_rgba = hex_rgb(COL_TEXT) + (1,)
        self.stats_lbl._tint_key = "text"
        info.add_widget(self.stats_lbl)
        # 余额/统计/"弹珠："都是单行; 余额涨到 8 位数以上时**缩字号**而不是折行
        self._install_fit(self._bead_lbl, self.balance_lbl, self.stats_lbl)
        self.add_widget(info)
        # 底行: [重置 96] —长距离— [力度 100] [蓄力发射 弹性]
        fire = BoxLayout(size_hint_y=None, height=dp(H_BOTTOM),
                         padding=[dp(6), dp(4), dp(12), dp(4)], spacing=dp(6))
        self._row_bottom = fire
        self.reset_btn = self._mk_button("重置", lambda _b: self.reset_balance(),
                                         bg=COL_BTN_OFF)
        self._outline_btn(self.reset_btn)   # 加描边(底色不动) —— 见 `_outline_btn`
        self.reset_btn.size_hint_x = None
        self.reset_btn.width = dp(96)
        # ⚠️ 按下态是"这个键被按住了", 与输入锁无关; 松手**必须回到取值口**而不是写死一个色 ——
        #    否则"按住重置不放 → 3s 兜底自动发射(上锁) → 松手"会把锁着的键涂回亮的。
        self.reset_btn.bind(on_press=lambda _b: setattr(self.reset_btn, "background_color",
                                                        hex_rgb(COL_BTN_LOCK) + (1,)),
                            on_release=lambda _b: self._restyle_buttons())
        fire.add_widget(Widget(size_hint_x=None, width=dp(12)))     # 重置按钮右移
        fire.add_widget(self.reset_btn)
        fire.add_widget(Widget(size_hint_x=0.95))                 # 弹簧(让出少量给右侧)
        self.power_lbl = self._mk_label("", "14sp", COL_METER, "center", True,
                                        size_hint_x=None, width=dp(100))
        fire.add_widget(self.power_lbl)
        self._install_fit(self.power_lbl)
        # ⚠️ 回调走 `self.`(UiMixin 不定义, 由 PlayMixin 提供)**同步入口** —— 直接调
        #    `self.game.launch()` 会让发射音晚一帧发出来(见 `PlayMixin._act`)。
        self.fire_btn = self._mk_button("蓄力发射", None, bg=COL_FIRE)
        self.fire_btn.size_hint_x = None
        self.fire_btn.width = dp(110)
        self.fire_btn.bind(on_press=lambda _b: self.start_charge(),
                           on_release=lambda _b: self.launch())
        fire.add_widget(self.fire_btn)
        fire.add_widget(Widget(size_hint_x=0.05))                 # 右侧弹簧(蓄力左移≈2dp)
        self.add_widget(fire)
        self.padding = [0, 0, 0, dp(12)]  # 底部留白
        self._restyle_buttons()           # 首帧之前把底色定死(不靠别处的调用顺带纠正)
        # 压暗块放在最后建: 它要读 game_area 的 pos/size, 且 canvas.after 必须排在
        # 全部子控件之后(见 _build_hud_dim 的说明)。
        self._build_hud_dim()
        self._refresh_stats()
        # ⚠️ 这三样**只在老版的 `_build_ui` 里初始化过**, 而它们的读者在 `_frame` /
        #    `_replay_cost_text`(PlayMixin / BenchMixin), 不归 `Game`(Game 里没有它们)。
        #    删掉的话 `_frame` 的心跳那句 `if self._hb_t <= 0.0` 会 AttributeError。
        self._replay_t0 = 0.0        # 「重放冷启动」的计时起点
        self._hb_t = 0.0             # 主线程心跳(只在启动窗口内记)
        self._hb_n = 0
        # ⚠️ 老版这里还有 `_rtp_popup` / `_rtp_hold_start` / `_rtp_hold_fired` / `_charge_uid`
        #    四行 —— **已删**: 它们现在是 `Game` 的状态(`game.py` 的 __init__ 里初始化),
        #    在这里再写一遍就是第二个状态源, 而且会把 Game 已经推进过的值清回去。

    # ------------------------------ 控件状态 ------------------------------
    def _outline_btn(self, btn):
        """给按钮**加一圈描边**。**底色不动**。

        「重置」原来只用 `COL_BTN_OFF` —— 那**同时也是**「没选中的档位/投注」的色, 于是
        "没选中"这个状态和"点我"这个动作长得一样。⚠️ **换底色解决不了**(两轮 A/B 截图
        都试过: 暖色更跳、暗色糊进背景、"空框"玩家当场回「底色也需要改改」) —— 靠描边
        做区分: 未选中的档是纯实心无描边, 两者形状语言不同。

        ⚠️ 底色(`background_color`)**不在这里写** —— 它归 `_restyle_buttons`(唯一取值口);
           这里只把描边挂上去, 描边色同样由 `_restyle_buttons` 跟着输入锁一起 dim。
        ⚠️ Kivy 的 `Line` **不能写 `.color`** —— 颜色要一条独立的 `Color` 指令, 且必须
           排在 `Line` **前面**。
        ⚠️ `width` 是**半宽**: 实测 1.0 与 0.5 都画出 2 个像素行(逐行采样验过), 取 0.5。
        """
        with btn.canvas.after:
            gl_c = Color(*(hex_rgb(COL_BTN_OFF) + (1,)))   # 占位, 真值由 _restyle_buttons 写
            gl_l = Line(rectangle=(btn.x, btn.y, btn.width, btn.height), width=0.5)
        btn._line_col = gl_c
        btn._line_ln = gl_l

        def _sync(_b, _v):
            gl_l.rectangle = (_b.x, _b.y, _b.width, _b.height)
        btn.bind(pos=_sync, size=_sync)

    def _sync_outline(self, btn, fill_hex, keep):
        """按**这个按钮当前的底色**重算描边, 并按输入锁调暗。

        ⚠️ 描边色**每次都要重算**(`_line_color(底色)`), 不能在这里定死 ——
           底色的唯一取值口是 `_restyle_buttons` 自己, 两处各存一份必然漂移。
        """
        _gc = getattr(btn, "_line_col", None)
        if _gc is not None:
            _gc.rgba = dim_rgb(_line_color(fill_hex), keep) + (1,)

    def _restyle_buttons(self):
        """**按钮底色的唯一取值口**。三个输入: 输入锁 / 选中档 / 音效档。

        ⚠️ "唯一"的意思是**别在别处再写 `btn.background_color`** —— 写两处必然漂移,
           表现就是"某个状态下看不出自己押的是哪一档"(本函数出生的原因)。
        ⚠️ 输入锁**不是换一个颜色**, 而是把身份色朝 `COL_BG` 混一档(`dim_rgb`):
           换色会让"锁着且选中"和"锁着且没选中"变成同一个色 ⇒ "我押的是哪一档"
           这条信息在飞行/中奖期间从画面上消失。
        ⚠️ `state == "charging"` 时**跳开发射键** —— 蓄力期它的底色归 `_frame`
           按力度独占写(`COL_FIRE` / 金色), 这里再写就是抢帧(会闪一帧亮红)。
        ⚠️ 三个输入全部从 `self.game` 取 —— **不许在 UI 侧存镜像**(那是第二个状态源)。
           一律 `getattr`: 探针夹具是半套控件, 硬取属性会把探针整个打红。
        """
        _keep = (BTN_OFF_DIM
                 if not getattr(self.game, "_controls_enabled", True) else 1.0)
        _bg = lambda _h: dim_rgb(_h, _keep) + (1,)
        _fb = getattr(self, "fire_btn", None)
        if _fb is not None and getattr(self.game, "state", "") != "charging":
            _fb.background_color = _bg(COL_FIRE)
        _rb = getattr(self, "reset_btn", None)
        if _rb is not None:
            _rb.background_color = _bg(COL_BTN_OFF)
            self._sync_outline(_rb, COL_BTN_OFF, _keep)
        _ob = getattr(self, "round_btn", None)
        if _ob is not None:
            _ob.background_color = _bg(COL_GREEN)
        _mb = getattr(self, "mute_btn", None)
        if _mb is not None:
            _mb.background_color = _bg(COL_GREEN if self.game.sound_mode == "on"
                                       else COL_MUTE_OFF)
        for _pv, _btn in self.bet_btns.items():
            _btn.background_color = _bg(COL_BTN if _pv == self.game.bet else COL_BTN_OFF)
        for _tv, _btn in self.game.rtp_btns.items():
            # ⚠️ 值**可能是 `None`**: `Game.__init__` 就把 4 个常驻档的键预注册好了(值 = None,
            #    等 UI 的 `_add_rtp_button` 填句柄), 而隐藏档在 `_add_rtp_button` 里也是先
            #    `rtp_btns[val] = None` 再发事件。事件是**攒着**下一帧才派发的, 所以这一轮
            #    完全可能撞上"键在、句柄还没填"的窗口 —— 不跳过去就是 AttributeError
            #    (`_reflow_row_budget` 有 try 兜着, 但这里是**裸的**, 会直接打断整帧派发)。
            if _btn is None:
                continue
            _btn.background_color = _bg(COL_BTN if abs(_tv - self.game.rtp_target) < 1e-6
                                        else COL_BTN_OFF)

    def _set_controls_enabled(self, enabled):
        """锁/解锁 HUD 输入 —— **只有界面那一半**。

        ⚠️⚠️ **状态位在 `Game`**(`Game._controls()` 持有并发 `controls` 事件), 本方法
           **绝不写 `self._controls_enabled`** —— 写它就是第二个状态源。要读就
           `self.game._controls_enabled`(见 `_restyle_buttons`)。
        ⚠️ 因此本方法的**唯一调用方必须是 `controls` 事件**(它带的就是 `Game` 刚写下的那个
           值) —— 两者必然同值。拿别的值直接调它, 会让"置灰"(读 `Game` 的位)与"染色"(用
           参数)两半打架。
        ⚠️⚠️ 与之配套的规矩(2026-09-19 修, **两轮才修对**): **别的模块要上锁/解锁, 一律走
           `self._act(self.game._controls, ...)`, 不许直接调本方法, 也不许裸调 `game._controls`**。
           · 那 4 处(`bench.py` 的 `_start_hp_test` / `_hp_done` / `_start_bench_test` /
             `_bench_done`)**最初**是直接调本方法 ⇒ **状态位那一半整个丢了**: 高压/模拟测试
             那几十秒 HUD **全程不上锁、不变暗**, 玩家可以直接点重置/投注档/发射
             (实测 `game._controls_enabled` 恒 True、发射键底色一个字节都没变)。
           · 第一轮改成裸调 `self.game._controls(...)` ⇒ 状态位补回来了, 但**界面那一半晚了
             一帧** —— `Game._controls` 只把 `controls` 事件排进 `_ev`, 染色要等下一次
             `game.step(dt)` 的派发才落地, 而老版那 4 处调的是**同一个方法**(置位与染色同体同拍)。
           · 正解 = `self._act(self.game._controls, ...)`: 既写 `Game` 的位, 又**当拍**派发
             (与 `play.py` 那 7 个"同拍转发"同一个机制)。本工程已为同一形状栽过一次:
             `play.py:73-78` 记着"从 UI 回调直调 `game.launch()` 会让那一声晚一帧 ——
             对账台实测第 1 条音效 frame 34 vs frame 35"。

        ⚠️ **绝不再逐个按钮改 `btn.disabled`**(1% Low 的头号来源): Kivy `uix/label.py`
        的 `_trigger_texture_update` 里 `disabled` 分支会把 `disabled_color` 写回
        `_label.options['color']`, 而 `Button` 就是 `Label` —— **颜色是烘进纹理的**,
        改一次就是一次文字重排; 更致命的是 Kivy 用 Clock 把这些重排**攒到同一帧**一起做
        ⇒ 12 个按钮的那一帧 12~21 毫秒(占该帧 95%+)。地面二分: `disabled=True` → 12 次
        重建 / `disabled_color` 对齐后仍 12 次(无条件重排) / `background_color` → 0 次。
        真机对应每一发两次爆发(发射时禁用 16 次 / 回 ready 时启用 15 次, 间隔正好一个
        蓄力时长 0.1s)。

        ⚠️ 变灰必须保留 —— 万一状态卡住, 玩家**看得见**按钮是暗的, 比"看起来正常却点不动"强。
        ⚠️ 变灰**不是"把按钮全涂成 `COL_BTN_OFF`"**: 那个色同时也是"没选中"的色, 两者撞车
           ⇒ 飞行/中奖期间选中的档位和旁边没选的长得一模一样(玩家报的"置灰逻辑混乱")。
           ⇒ 变暗走 `dim_rgb`(见 `_restyle_buttons`)。
        """
        # 底色: **唯一取值口**。输入锁在这里变成"身份色朝 COL_BG 混一档" ——
        # 不是"全涂成 COL_BTN_OFF", 那样会把"选中了哪一档"一起抹掉。
        self._restyle_buttons()
        if enabled:
            self._refresh_mute_btn()      # 只负责"文字 + .color"; 底色上面已经给过
            # ⚠️ **不写 `.color`** —— 写它就是一次字形纹理重排(4~7 毫秒), 而这一拍正是"回 ready"。
            #    染色走画布那条 Color; 拿不到才退回写 `.color`(绝不静默不染色)。
            for _lbl in (self._rtp_title_lbl, self._bet_title_lbl, self.stats_lbl):
                if not _set_lbl_tint(_lbl, _TINT_BRIGHT):
                    _lbl.color = _lbl._tint_base_rgba
        else:
            # 同上一支: 变暗走染色, 不重建纹理。系数 = 目标色 / 烘的那个色(逐通道)。
            for _lbl in (self._rtp_title_lbl, self._bet_title_lbl, self.stats_lbl):
                if not _set_lbl_tint(_lbl, _TINT_DIM[_lbl._tint_key]):
                    _lbl.color = hex_rgb(COL_GRAY) + (0.6,)

    # ------------------------------ 尺寸自适应 ------------------------------
    def _apply_sizes(self):
        """将 _ui_scale / _font_scale 写到所有固定 UI 元素的尺寸和字号上。
        横屏时缩小所有固定行高/按钮宽/字号/边距, 把垂直空间还给游戏区。
        纵向边距用平方衰减(us²), 横屏时更激进地挤掉空白。"""
        us = self._ui_scale
        uv = us * us                                    # 纵向: 平方衰减, 激进挤空白
        fs = self._font_scale * us

        self._row_top.height    = dp(H_TOP)    * us
        self._row_rtp.height    = dp(H_RTP)    * us
        self._row_bets.height   = dp(H_BETS)   * us
        self._row_info.height   = dp(H_INFO)   * us
        self._row_bottom.height = dp(H_BOTTOM) * us
        self.spacing = dp(10) * uv                      # 行间距: 激进衰减

        self._row_top.padding    = [dp(10), dp(4) * uv, dp(10), dp(4) * uv]
        self._row_rtp.padding    = [dp(14), dp(4) * uv, dp(10), dp(4) * uv]
        self._row_bets.padding   = [dp(14), dp(4) * uv, dp(10), dp(4) * uv]
        self._row_bottom.padding = [dp(6), dp(4) * uv, dp(12), dp(4) * uv]
        self.padding = [0, 0, 0, dp(12)]  # 底部留白

        # ---- 基准字号: 与原来逐条一致(sp(N) * fs), 紧接着全部交给 `_fit1` 定档 ----
        # `_fit1` 只会在**装不下**时往下调(最低 0.7 倍), 装得下就原样保持 —— 所以宽屏/
        # 正常字体下与改动前逐像素相同, 只有"会折行/会溢出"的那些才会变。
        for _w, _base in ((self.title_lbl, sp(18) * fs),
                          (self.status_lbl, sp(13) * fs),
                          (self.mute_btn, sp(13) * fs),
                          (self.round_btn, sp(13) * fs),
                          (self._rtp_title_lbl, sp(14) * fs),
                          (self._bet_title_lbl, sp(14) * fs),
                          (self._bead_lbl, sp(15) * fs),
                          (self.balance_lbl, sp(19) * fs),
                          (self.stats_lbl, sp(15) * fs),
                          (self.power_lbl, sp(14) * fs),
                          (self.reset_btn, sp(16) * fs),
                          (self.fire_btn, sp(16) * fs)):
            _w.font_size = _base
            _w._fit_base = _base
        for b in list(self.game.rtp_btns.values()) + list(self.bet_btns.values()):
            # ⚠️ `rtp_btns` 的值可能是 `None`(键先注册、句柄后填) —— 理由见 `_restyle_buttons`
            if b is None:
                continue
            b.font_size = sp(16) * fs
            b._fit_base = b.font_size

        self._apply_row_budget(us, fs)

    def _apply_row_budget(self, us, fs):
        '''重算"顶栏 / 两条档位行 / 信息行 / 底行"的**宽度预算**, 再定一遍字号。

        ⚠️ 单独成方法是为了让**档位按钮增删之后能再跑一次** —— 解锁隐藏档会在返还率那行
           多插一个按钮, 而 `_add_rtp_button` 把新按钮宽写死 `dp(56)`、常驻档的宽却是这里
           反算的(360dp 上 ≈45dp): 一解锁那一行就从"恰好铺满"变成**右溢 61dp**, 最右那个
           按钮只剩一小半在屏内(玩家报的"隐藏返还率几乎看不到"是被推出屏幕, 不是看不清)。
        '''
        # 顶栏宽度预算: 标题**按自己的字量**定宽(不折行), 左右两块各拿 (行内宽 - 标题 - 2间距)/2
        # —— 两边**强制等宽** ⇒ 标题居中。左边两个按钮的宽由这份份额反算(上限是设计值 58/62),
        # 于是窄屏上收的是按钮、不是标题的字号。
        _tp = self._row_top.padding                    # Kivy 已展开成 [l, t, r, b]
        _top_inner = (self._row_top.width or self.width) - _tp[0] - _tp[2]
        _gap = self._row_top.spacing
        _title_w = text_px(self.title_lbl.text, sp(18) * fs, True) + dp(6)
        self.title_lbl.width = _title_w
        _share = max(dp(56) * us, (_top_inner - _title_w - _gap * 2) / 2.0)
        self.mute_btn.width    = dp(58) * us
        self.round_btn.width   = dp(62) * us
        self._top_left.spacing = _gap
        _need = self.mute_btn.width + _gap + self.round_btn.width
        if _need > _share:                             # 窄屏: 两个按钮等比收, 保住标题字号
            _k = max(0.62, (_share - _gap) / max(1.0, _need - _gap))
            self.mute_btn.width  = dp(58) * us * _k
            self.round_btn.width = dp(62) * us * _k

        # 「返还比例」「投入」两行: 标签宽**按它自己的字量**, 档位按钮宽**由行宽反算**
        # —— 一律 dp(56) 的话整行最小要 364dp, 而 360dp 机器上只有 312, 最右那个按钮
        # (360% / 100个)被推出屏幕 47dp。全角"："的字身自带宽空白, 宽度量到字宽再垫 4dp
        # 就"贴上去"了(写死 dp(115) 而字只要 98, 那 17dp 白占)。
        _lbl_w = max(text_px(self._rtp_title_lbl.text, sp(14) * fs),
                     text_px(self._bet_title_lbl.text, sp(14) * fs)) + dp(4)
        self._rtp_title_lbl.width = _lbl_w
        self._bet_title_lbl.width = _lbl_w
        for _rw in (self._row_rtp, self._row_bets):
            # ⚠️ 预算吃的是**行的真实宽度**, 而 `_apply_sizes` 是在"窗口尺寸变了"那一帧跑的
            #    —— 那一刻 `_row.width` 还是**上一次布局**的值(实测 360dp 机器上会按 540 算,
            #    按钮宽 66 而不是 52, 最右那个"100个"被推出屏幕)。绑到行宽上就自洽了:
            #    布局一落定就重算一次, 与"窗口变化"这个触发时机彻底解耦。
            # ⚠️⚠️ **只能绑一次**(`_budget_bound` 守着)。Kivy 的 `bind` 只追加、不去重,
            #    而这条回调会调回 `_apply_row_budget`, 于是每跑一次就再挂一条新的 ——
            #    实测 observer 数 21 -> 85 -> 341 -> 1365(**每变一次宽就 ×4**),
            #    一次宽度变化要跑几千遍(单遍 0.046ms) => 转屏 / 分屏拖拽 / 折叠屏开合时
            #    整帧卡 1~4 秒, 而且**只增不减、越玩越糟**。
            if not getattr(_rw, "_budget_bound", False):
                _rw._budget_bound = True
                _rw.bind(width=lambda *_a: self._reflow_row_budget())
        for _row, _btns in ((self._row_rtp, self.game.rtp_btns),
                            (self._row_bets, self.bet_btns)):
            # ⚠️ 数的是**句柄已填好的**按钮, 不是键的个数: `rtp_btns` 的值可能是 `None`
            #    (键先注册、句柄后填, 见 `_restyle_buttons`) —— 按 `len(keys)` 算会把这格
            #    算进宽度预算, 然后把整排压窄; 而那一格此刻**根本不在屏幕上**。
            _bs = [b for b in _btns.values() if b is not None]
            _n = len(_bs)
            if not _n:
                continue
            _pad = _row.padding                     # Kivy 已展开成 [l, t, r, b]
            _avail = (max(_row.width, self.width) if self.width else _row.width)                 - _pad[0] - _pad[2]
            _gap = _row.spacing
            # 两行的孩子都是 n+2 个(标签 + 一个吃余量的空白 + N 个档位按钮)
            # ⇒ 间距有 n+1 段; 按钮把余量吃干净, 那个空白弹簧自然收到 0。
            _bw = (_avail - _lbl_w - _gap * (_n + 1)) / float(_n)
            # ⚠️ **上下都要夹**: 下界防窄屏把按钮压没, 上界防宽屏把它撑爆(Y700 内容列
            #    792dp, 只按下界时实测返还率按钮 127dp/个、投入按钮 160dp/个, 设计值 56)。
            #    dp(72) 之后: 400dp 上仍是算出来的 62.3、457dp 上 76.5 -> 72, 平板的富余
            #    交回"吃余量的空白弹簧", 回到原本的左对齐构图。
            _bw = min(max(dp(34) * us, _bw), dp(72) * us)
            for b in _bs:
                b.width = _bw
            # 一排按钮**共用一个字号**(见 `_fit_buttons_uniform` 的说明)
            self._fit_buttons_uniform(_bs, sp(16) * fs, inset=dp(6) * us)

        # 信息行: "弹珠："按字量, 余额/统计靠 `_fit1`(余额 8 位数以上时会缩, 而不是折行)
        self._bead_lbl.width = text_px(self._bead_lbl.text, sp(15) * fs) + dp(4)

        # 底行: 定宽件按原尺寸, 只把按钮文字也接进自适应(系统大字体下不溢出)
        self.reset_btn.width = dp(96)  * us
        self.fire_btn.width  = dp(110) * us
        # 力度标签**按它自己的字量**给宽 —— 它现在是空的, 而白占 dp(100) 会把底行撑到
        # 366dp, 340dp 以下的机器上"蓄力发射"直接被推出屏幕(实测 320dp 右溢 28dp)。
        # 空串给 0 宽, 以后真往里写力度也就自动有位置了。
        self.power_lbl.width = (text_px(self.power_lbl.text, sp(14) * fs) + dp(6)
                                if self.power_lbl.text else 0.0)

        # ---- 定档: 上面所有宽度都落定之后, 再算字号(顺序不能反) ----
        for _w in (self.title_lbl, self.status_lbl, self.mute_btn, self.round_btn,
                   self._rtp_title_lbl, self._bet_title_lbl, self._bead_lbl,
                   self.balance_lbl, self.stats_lbl, self.power_lbl,
                   self.reset_btn, self.fire_btn):
            self._fit1(_w)
        # (两行的按钮在各自那一轮里已经按**整排统一**定过字号了, 见上)

    def _reflow_row_budget(self):
        '''档位按钮增删之后重跑一次宽度预算(包在 try 里: 探针夹具只搭了半套控件)。'''
        try:
            self._apply_row_budget(self._ui_scale, self._font_scale * self._ui_scale)
        except Exception:
            pass
