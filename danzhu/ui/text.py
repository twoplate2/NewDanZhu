"""文字度量 / 纹理缓存 / 预热 / 帧探针包装。

端口自老版 `android/main.py` 4593-5560 + 10190-10557 + 10637-10853 + 12708-12784。
数值与求值顺序逐位照抄, 一个数都没改。

⚠️⚠️ **跨模块共享的探针账本 / 缓存定义在这里**(老版是 main.py 的一个命名空间):
       `_FRAME_BRK` / `_FRAME_END` / `_FRAME_SWAP` / `_FRAME_CALLS` / `_FRAME_SELF` /
       `_SINCE_LAUNCH` / `_FRAME_FIT` / `_TEXUPD*` / `_TEXUPD_ACTIVE` / `_CLOCK_END` /
       `_COLD_FS*` / `_FONT_WARM_ALL` / `_WARM_DID` / `_PIN_*` / `_POPUP_N` / `_FS_*` /
       `_FIT_HIST` / `_TEXEX_*` / `_TEXTEX_PER_LABEL` / `_GLYPH_*` / `_SLOT_TXT_TEX` /
       `_BALL_TEX` / `_TINT_*` / `_BRK_KEYS` ...
   ⚠️ 上面这份清单**天然会漏**(漏了不报错, 只让那一格恒为 0) —— 请当
   "**本文件全部模块级名字**(含公开的 `text_px` / `fit_font_size` / `slot_txt` /
   `slot_text_tex` / `FIT_SCALES` / `COLD_FS_MIN_MS` …)都属于同一份"来用:
   `root.py` / `play.py` / `bench.py` / `widgets.py` / `ui_base.py` / `winfx.py` /
   `game_area.py` **必须 `from .text import ...`**; 包装器往里写、别处另建一份的话账目
   恒为零 —— **而且不报错**(`_SINCE_LAUNCH` 就是这样漏到 `play.py` 的 ImportError 才暴露,
   而那次 ImportError 直接让整棵 UI 起不来)。`tests/global_dedup.py` 钉这条。
"""

import math
import os
import time

from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.graphics.texture import Texture
from kivy.metrics import sp
from kivy.uix.label import Label

from ..config import (COL_FIRE, COL_GRAY, COL_GREEN, COL_TEXT, DEFAULT_BET,
                      PRESETS, _BALL_TEX_PX, _GLYPH_FONT, hex_rgb, slot_color)
from ..rules import VALUE_SHAPE


# 中文字体: 用 `name="Roboto"` 覆盖 Kivy 默认字体, 所有控件全局生效(老版 main.py:486-493)。
# ⚠️ **这是本模块一切度量的前提**: 不注册就用 Kivy 自带那个 —— 真机上汉字整体变豆腐块,
#    而 `text_px` / `fit_font_size` / 字形图集的**每一个数都静默变掉**(桌面实测 "未中"@48
#    宽 96→42、`_glyph_bake(48)["h"]` 70→57、`slot_text_tex(50,14)` [23,21]→[23,17]),
#    没有一块红会亮。端口自述里点过它, 但当时没有任何模块落地 ⇒ 度量模块自己保证它。
# ⚠️ 路径按**包根**算(与 `winfx._ASSET_GLASS` 同法): 老版那个 `__file__` 是 main.py, 所以
#    是 `dirname(__file__) + "fonts"`; 模块化之后得往上退三层才是同一个目录。
# ⚠️ 必须钉在 **import 期**: 要早于任何一次度量(`Label.texture_update` 也一样吃它), 而本模块
#    正是度量入口。`LabelBase.register` 只是登记一个类, 不读文件、不开窗口(实测)。
_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fonts", "NotoSansSC-Medium.otf")
try:
    if os.path.exists(_FONT_PATH):
        LabelBase.register(name="Roboto", fn_regular=_FONT_PATH)
except Exception:
    pass


# ===========================================================================
# 帧探针账本(老版 4495-4590 + 5957)
# ===========================================================================
_FRAME_BRK = {}                  # 本帧累计(会被 _on_flip 读走并清空)
_BRK_KEYS = ("板面", "重掷", "字号", "装杯")
_FRAME_CALLS = [0]               # `_frame` 被调了几次(与"采样了多少帧"比, 见面板"节拍"行)
_FRAME_SELF = [0.0]              # 本线程上这一帧我们自己花了多少毫秒(perf_counter 量的)
_SINCE_LAUNCH = [0]              # 距上一次 `launch()` 过了多少帧(判"最差帧是不是紧跟在发射后")
_FRAME_FIT = [0, 0]              # [本帧 _fit1 调用次数, 本帧 text_px 冷测量次数]
_COLD_FS = []                    # 最慢的 5 次冷字号测量
_COLD_FS_TAG = ["?"]
_COLD_FS_BASE = [0.0]
COLD_FS_MIN_MS = 3.0             # 多慢才算「冷」; 热字号一次只要 0.02~1.5ms
_FONT_WARM_ALL = []              # 预热表里**真正传给 text_px 的原值** [(字号, bold)]
_WARM_DID = set()                # 预热**真的调过 text_px** 的 (字号, bold)
_FRAME_END = [0.0]               # `_frame` 算完那一刻; 0 = 本帧还没算完
_FRAME_SWAP = [0.0]              # 上一帧 Window.flip() 阻塞了多少毫秒
_TEXUPD = [0]
_TEXUPD_BY = {}
_TEXUPD_ACTIVE = [False]         # 采样闸门: 避免启动/成绩弹窗的重排污染游戏采样
_CLOCK_END = [0.0]               # Clock.tick 结束那一刻(「尾」三分段的中间那一刀)
# 本帧 `Window.on_draw`(画布遍历 + GL 提交)花了多少**毫秒**。
# ⚠️ 它同时进 `_FRAME_BRK["尾·on_draw"]`, 而 `_swap_wrap` 算「尾·空档」时会**把它减掉**
#    —— 两格不重叠, 加得起来。见 `_ondraw_wrap`。
_ON_DRAW_MS = [0.0]


def _brk_add(tag, t0):
    """记一笔子步骤耗时(秒)。只在跑分采样期真读, 平时只是一次字典读改写。"""
    d = _FRAME_BRK.get(tag)
    _FRAME_BRK[tag] = (d or 0.0) + (time.perf_counter() - t0)


def _brk_wrap(cls, attr, tag):
    """给一个方法包一层计时。⚠️ 包装函数的 `__name__` 必须**与原方法同名** ——
    Kivy 的 WeakMethod 存的是 `__name__`, 名字对不上会在下次调度时 AttributeError
    (老版的探针踩过一次)。"""
    orig = getattr(cls, attr, None)
    if orig is None:
        return

    def f(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            _brk_add(tag, t0)
    f.__name__ = attr
    setattr(cls, attr, f)


# ===========================================================================
# 字体"钉子": 把复冷过的 fontid 从 Kivy 的淘汰队列里摘掉(老版 4706-4793)
# ===========================================================================
# ⚠️ Kivy 的 `_text_sdl2` 里 `sdl2_cache_order` 是插入序, 命中只读 dict、淘汰只从队头取。
#    把某个 fontid 从 order 里 `remove` 掉且**不 append** ⇒ 它排不进淘汰队列,
#    dict 里那份永远不会被 `del`, 那个 fontid 再也不会被冷开。
# ⚠️⚠️ **绝不 append**: order 里出现重复项时, 淘汰那句 `del sdl2_cache[popid]` 会 KeyError
#    崩在渲染路径上。所以只准 `remove`, 且钉之前断言 order 无重复。
# ⚠️ `_PIN_MAX` 是**按 RSS 预算倒推**的上限(每个 fontid 实测约 0.88MB), 不是"能钉多少钉多少"。
_PIN_ORDER = [None]              # [那个 list]; None = 还没找过
_PIN_DONE = [None]               # set(); None = 还没建(省一个模块级 set)
_PIN_MAX = 24
# ⚠️ **第几次冷开才钉**。别往下调回 2: 名额不够时会分给一大堆只复冷 2 次的字号
#    (复冷 2 次 = 只省下 1 次冷开), 而真正的慢性病排不上。
_PIN_AFTER = 3
# ⚠️⚠️ **弹窗开着的时候不钉**: 设置界面那 13 个弹窗会新开出游戏里根本不会出现的字号,
#    点一遍就废掉 79% 的钉子预算 —— 钉子名额是**游戏 HUD 的救命资源**。
_POPUP_N = [0]                   # 现在开着几个弹窗(RotPopup.open/dismiss 维护)
_PIN_POPUP_SKIP = [0]            # 因为"弹窗开着"而跳过的钉子次数(**必须印进日志, 不许静默**)
_PIN_STAT = [0, 0, 0]            # [钉成功, 钉失败, 复冷次数] —— **必须印进日志, 不许静默**
_PIN_WHERE = ["还没找"]           # 定位结果的一句话说明(日志里印)
_PIN_CAND = [0]


def _find_sdl2_order():
    """在 CPython 对象图里找出 Kivy 的 `sdl2_cache_order`。找不到返回 None。

    ⚠️ `getattr(_text_sdl2, "sdl2_cache_order")` **拿不到** —— 它是 `.pyx` 里的 `cdef`,
       不进模块 dict。但它是货真价实的 list, `gc.get_objects()` 能扫到。
    ⚠️ 判据的形状在本进程里是**唯一**的, 所以命中数要记进 `_PIN_CAND` —— 命中 2 个以上时
       这里返回的是先扫到的那个, 可能是错的, 而错的后果同样是**静默不生效**。
    ⚠️ 认不出来就**如实返回 None**, 绝不猜。
    """
    _n = 0
    _first = None
    try:
        import gc as _gc
        for o in _gc.get_objects():
            if type(o) is list and 8 <= len(o) <= 64:
                for x in o:
                    if not (isinstance(x, str) and x.count("|") == 5):
                        break
                else:
                    _n += 1
                    if _first is None:
                        _first = o
    except Exception:
        pass
    _PIN_CAND[0] = _n
    return _first


def _pin_fontid(fid):
    """把一个 fontid 从淘汰队列里摘掉(只 remove, 绝不 append)。返回是否成功。"""
    if _PIN_ORDER[0] is None:
        return False
    if _PIN_DONE[0] is None:
        _PIN_DONE[0] = set()
    if fid in _PIN_DONE[0] or len(_PIN_DONE[0]) >= _PIN_MAX:
        return False
    _o = _PIN_ORDER[0]
    try:
        if len(_o) != len(set(_o)):        # 约束②: order 有重复项就整条停用
            _PIN_STAT[1] += 1
            return False
        if fid not in _o:
            return False
        _o.remove(fid)                     # 约束①(只删不加)
        _PIN_DONE[0].add(fid)
        _PIN_STAT[0] += 1
        return True
    except Exception:
        _PIN_STAT[1] += 1
        return False


# ===========================================================================
# 文字纹理扩展缓存(老版 4862-5052)
# ===========================================================================
def _tag_texupd(label, tag):
    """给会动态变化的 Label 标记来源，供跑分定位文字纹理重建。"""
    try:
        label._texupd_tag = str(tag)
    except Exception:
        pass
    return label


def _set_label_text(label, text):
    """只有文本真的变化才触发 Kivy Label 的属性更新与后续纹理检查。"""
    try:
        if label.text == text:
            return False
        label.text = text
        return True
    except Exception:
        return False


def _hp_score(r):
    """一条 CPU 高压记录该显示的**跑分** = **平均数**(不是中位数)。

    ⚠️ 与 `_hp_freq_line` 里那段注释同源: 频率/每秒窗口都是**双峰**样本(大部分时间在等
       vsync, 满窗口才冲睿频), 「中位数必然落在其中一个峰上」—— 报出来要么像全程低频、
       要么像全程满血。所以这里取平均。
    ⚠️ **三处口径必须同步**(列头 / 脚注 / 详情正文), 只改一处就会出现"表头写平均数、
       正文写中位"这种自相矛盾。
    ⚠️ **新记录读 `mean`; 旧记录没有这个字段就拿 `windows` 现算** —— 两者本来就是同一次
       测试的同一批逐秒样本, 算得出来就该算, **不要印「—」**(那不是"没有数据", 是我们当年
       没存, 而原始样本还在)。真的一条窗口都没有才返回 None。
    """
    _m = r.get('mean')
    if _m is not None:
        try:
            return int(_m)
        except Exception:
            pass
    _w = [x for x in (r.get('windows') or []) if x > 0]
    if not _w:
        return None
    return int(round(sum(_w) / float(len(_w))))


def _hist_stamp(raw):
    """把记录里的时间戳**归一化**成 `09-15 14:07`(月日 + 时间, **不带年份**)。

    ⚠️ 玩家在这一个格子上**来回改过四轮**, 结论是**不带年份** —— 别再从头试。
       这次不是重犯老路: 之前反复是被**宽度**逼出来的(三列固定栅格, 时间列只有 110px
       而完整格式要 113px), 这次是玩家的显示偏好, 且去掉年份正好把宽度账彻底松开 ——
       `09-15 14:07` 只要 76px ⇒ 三列数据全都放得下 ⇒ 全表字号回到 `sp(14)` 上限
       (**一个字列宽都没改, 字号自己就上去了**)。
    ⚠️ **只改显示** —— 盘上存的一直是 `%Y-%m-%d %H:%M`(完整), 所以旧记录一条都不用动。
    ⚠️ 认不出来**原样返回**, 绝不吞信息、也不抛。
    """
    _s = str(raw if raw is not None else '')
    try:
        _d = time.strptime(_s[:16], "%Y-%m-%d %H:%M")
        return time.strftime("%m-%d %H:%M", _d)
    except Exception:
        return _s or '--'


# ⚠️ **必须是 64 而不是 12**: 状态栏现在要存 10 条固定文案 + 35 条带数字的 = 45 项。
#    上限留在 12 的话它会**自己把自己挤掉** —— 烘一个丢一个, 命中率反而跌回去, 且不报错。
_TEXTEX_PER_LABEL = 64
_TEXEX_HIT = [0]            # 跑分采样期命中次数(进日志, 用来验证这层到底有没有生效)
_TEXEX_MISS = [0]

# ⚠️⚠️ **别改成"直接缓存 Kivy 给标签建的那张 Texture"**: `CoreLabel._texture_fill(self,
#    texture)` **忽略传进来的 texture 参数**, 方法体只有一句 `self.render(real=True)` ——
#    它画进的是那一刻 `self.texture` 那张, 而填充推迟到 Texture 第一次 `bind()` 才发生,
#    这段延迟里谁先被 bind 内容就写进谁 ⇒ 缓存下来的对象**张冠李戴**(逐像素比对过:
#    4 句里 3 句不一致, 而尺寸全对, 只看尺寸永远发现不了)。
#    现在的做法: 每个指纹配一个**专用的 CoreLabel**, 它的 `text`/`options` 从此**永不改变**
#    ⇒ 延迟填充只发生一次, 填的必然是它自己的内容。**前提是"永不改动"这条不破。**
_TEXEX_ON = True

# ⚠️ 字段表**必须覆盖影响纹理的一切**(按 Kivy `Label._font_properties` 抄)。
#    漏一项 = 那个属性改了但缓存命中 ⇒ 显示旧内容, 而且**只在命中时犯**, 极难复现。
_TEXEX_FIELDS = ("text", "font_size", "bold", "color", "text_size", "halign", "valign",
                 "padding", "mipmap", "outline_width", "disabled", "font_name",
                 "underline", "strikethrough", "font_kerning")


def _texex_norm(v):
    """把属性值变成可哈希的。⚠️ 浮点**原样保留精度** —— 字号差 0.01 就是另一张纹理。"""
    if isinstance(v, (list, tuple)):
        return tuple(_texex_norm(x) for x in v)
    return v


def _texex_key(lbl):
    """一张文字纹理的完整指纹。

    ⚠️ `color` 必须在内: Kivy 把颜色烘进纹理, 换个色就是另一张图。
    ⚠️ `text_size` 必须在内: 它绑在 `size` 上, 布局一变它就变。
    """
    _out = []
    for _n in _TEXEX_FIELDS:
        try:
            _out.append(_texex_norm(getattr(lbl, _n)))
        except Exception:
            _out.append(None)
    return tuple(_out)


# ⚠️⚠️ **全局(跨标签)指纹缓存**。结算大字/阴影是**每次中奖现新建的 Label**,
#    而 `_texex` 存在标签自己身上 ⇒ 每个新标签的缓存都是空的 ⇒ 每局中奖都稳定产生
#    2 次重建, 真机上各 5~10 毫秒, 落在同一帧 ⇒ 那一帧必然越过 11.11 毫秒。
# ⚠️ **全局共享在这里安全的前提是"专用 CoreLabel 永不改动"**(见 `_TEXEX_ON` 处):
#    按标签存本来是为了防"把 Kivy 给标签建的纹理交出去会被原地重画", 而 `_texex_bake`
#    烘的那张**再也不会被重画** ⇒ 多给几个标签用完全没问题。
_TEXEX_G = {}
_TEXEX_G_ORDER = []
_TEXEX_G_MAX = 256          # 一张纹理几十 KB, 256 张约十几 MB


def _texex_key_of(lbl):
    """按标签当前属性算指纹(抽出来是因为全局缓存那条路也要用)。"""
    return _texex_key(lbl)


def _texex_get_g(key):
    return _TEXEX_G.get(key)


def _texex_put_g(key, cl):
    if key in _TEXEX_G:
        return
    _TEXEX_G[key] = cl
    _TEXEX_G_ORDER.append(key)
    while len(_TEXEX_G_ORDER) > _TEXEX_G_MAX:
        _TEXEX_G.pop(_TEXEX_G_ORDER.pop(0), None)


def _texex_get(lbl, key):
    _d = getattr(lbl, "_texex", None)
    return _d.get(key) if _d else None


def _texex_put(lbl, key, tex):
    _d = getattr(lbl, "_texex", None)
    if _d is None:
        _d = {}
        lbl._texex = _d
        lbl._texex_order = []
    if key in _d:
        return
    _d[key] = tex
    lbl._texex_order.append(key)
    while len(lbl._texex_order) > _TEXTEX_PER_LABEL:
        _d.pop(lbl._texex_order.pop(0), None)


def _texex_bake(lbl):
    """按 `lbl` **当前**的属性烘一个**专用的 `CoreLabel`**, 返回它。

    ⚠️ `Label` 与 `CoreLabel` 的属性名不完全一样:
       · **`disabled` 会把颜色换成 `disabled_color`** —— 不照做的话, 灰化状态会烘出
         **亮色**的纹理, 而且是"看着正常但颜色不对"那种错。`outline_color` 同理。
    ⚠️ 属性**逐个 `getattr` 且缺失就跳过**, 不写死一份"以为一定有"的清单 —— Kivy 版本
       一变就会静默少传一个参数, 而少传的表现是"烘出来的字和显示的不一样", 极难查。
    """
    _o = {}
    for _n in ("text", "font_size", "font_name", "bold", "italic", "underline",
               "strikethrough", "font_family", "halign", "valign", "shorten",
               "mipmap", "line_height", "strip", "unicode_errors", "font_hinting",
               "font_kerning", "font_blended", "outline_width", "font_features",
               "font_context", "base_direction", "text_language",
               "limit_render_to_text_bbox", "padding"):
        try:
            _o[_n] = getattr(lbl, _n)
        except Exception:
            pass
    try:
        _ts = list(lbl.text_size)
        _o["text_size"] = _ts
    except Exception:
        pass
    # 颜色: **必须按 disabled 走 Kivy 那套映射**, 否则灰化态烘成亮色。
    try:
        _o["color"] = tuple(lbl.disabled_color if lbl.disabled else lbl.color)
    except Exception:
        pass
    try:
        _oc = lbl.disabled_outline_color if lbl.disabled else lbl.outline_color
        if _oc is not None:
            _o["outline_color"] = tuple(_oc)
    except Exception:
        pass
    _cl = CoreLabel(**_o)
    # ⚠️ **`refresh()` 绝不能漏**: 漏了它 `_cl.texture` 恒为 `None` ⇒ `_texex_apply`
    #    返回 False ⇒ 永远存不进缓存、每次都退回 Kivy 老路。症状特别阴:
    #    **画面完全正常**, 但命中率恒为 0。
    _cl.refresh()
    # ⚠️⚠️ **必须在这里就把第二趟(真光栅化 + 纹理上传)做掉, 否则预热等于没烘**:
    #    `refresh()` 之后填纹 +0, 真正的光栅化要等这张纹理**第一次被绑上去画**才发生。
    #    触发方式选 `bind()` 不选读 `.pixels`: 两者都能触发, 但读 `.pixels` 要把整张纹理
    #    拷进 Python 侧(45KB/项), `bind()` 只是把它设为当前 GL 纹理, 不留副作用。
    #    触发**只做一次**: 再 `bind()` 一次是 +0(回调已被摘掉)。
    try:
        _cl.texture.bind()
    except Exception:
        pass
    return _cl


def _texex_apply(lbl, cl):
    """把专用 CoreLabel 的纹理交给标签显示。**这是标签唯一的出图路径**。"""
    _t = cl.texture
    if _t is None:
        return False
    lbl.texture = _t
    lbl.texture_size = list(_t.size)
    return True


# ===========================================================================
# 启动预热表(老版 5055-5309)
# ===========================================================================
def _build_texwarm(rw, bet=None):
    """列出"启动期该提前烘好的 (标签, 文案, 颜色)"。

    ⚠️ 缓存把**重复**变免费了, 但每句的**第一次**仍要付全价 —— 预热 = 把"必然出现"的
       首次挪到启动期。
    ⚠️ **只列固定文案**: 带数字的(`累计%d投%d中`、余额、`中奖! +%d (x%d)`)值域无限,
       列不进来 —— 那部分只能靠缓存命中重复值。
    ⚠️ **颜色必须一起列**: Kivy 把颜色烘进纹理, 同一个字符串换个色就是另一张图。
       `_set_controls_enabled` 每发球都会改那三个标签的 `.color`。
    ⚠️ `bet` = **当前投注档**。带数字的"中奖! +N (xM)"里 `payout = bet x m`, 四个投注档
       全铺是 28 条(1.4 秒启动), 而**一局里投注档通常不变** ⇒ 只烘当前档那 7 条,
       别的档第一次用到时走懒缓存。
    """
    out = []
    if rw is None:
        return out
    # ---- 状态栏: 10 句固定文案(颜色恒定, 只有 `_fit1` 可能改字号) ----
    _st = getattr(rw, "status_lbl", None)
    if _st is not None:
        try:
            _c = tuple(_st.color)
        except Exception:
            _c = None
        for _t in ("按住蓄力发射", "已重置", "蓄力中", "力度不足,未扣弹珠", "发射!", "未中",
                   "即将入袋…", "弹跳中…", "入场中…", "性能测试中…", str(_st.text)):
            out.append((_st, _t, _c))
        # ---- 带数字的那两句: **取值域有限, 所以能枚举** ----
        # ⚠️ 倍率与投注档**从代码里派生, 不许手抄一份** —— 抄的那份迟早和真值脱钩,
        #    而脱钩的表现是"预热白做、还不报错"。倍率真源 = `VALUE_SHAPE` 的键(再并上 x2)。
        try:
            _mults = sorted({2} | {int(_k) for _d in VALUE_SHAPE.values() for _k in _d})
            for _m in _mults:
                out.append((_st, "命中 x%d · 结算中" % _m, _c))
            _my_bet = bet if bet in PRESETS else (PRESETS[0] if PRESETS else 1)
            for _m in _mults:
                out.append((_st, "中奖! +%d (x%d)" % (_my_bet * _m, _m), _c))
        except Exception:
            pass
    # ---- 音效按钮: 两句话 x 两种颜色(`_refresh_mute_btn` 里那两个) ----
    _mb = getattr(rw, "mute_btn", None)
    if _mb is not None:
        try:
            _on = hex_rgb("#0e1524") + (1,)
            _off = hex_rgb("#c0c8e4") + (1,)
        except Exception:
            _on = _off = None
        for _t, _c in (("音效已开", _on), ("音效已关", _off)):
            out.append((_mb, _t, _c))
    # ---- 两个标题标签: 文字永不变, 但 `.color` 每发球被改两次 —— 只烘一个色 ----
    for _n in ("_rtp_title_lbl", "_bet_title_lbl"):
        _lb = getattr(rw, _n, None)
        if _lb is None:
            continue
        try:
            out.append((_lb, str(_lb.text), tuple(_lb.color)))
        except Exception:
            pass
    # ---- 中奖大字/阴影(每次中奖现建新标签, 只有全局缓存能救它) ----
    try:
        out.extend(_bigtext_warm_items(rw))
    except Exception:
        pass
    # ⚠️ **进度串不预烘**: 进度只在物理段/CPU 高压段显示, 那时屏幕采样**已经停了**
    #    ⇒ 写标签不进成绩。
    # ---- 飘字(`center_toast`): 同样是每次现建 Label, 同一个病 ----
    try:
        out.extend(_toast_warm_items(rw))
    except Exception:
        pass
    return out


def _warm_one(item):
    """把 (标签, 文案, 颜色[, 字号]) 走一遍 —— 走的是 `Label.texture_update`, 自动进缓存。

    ⚠️ 走完**必须还原**。还原那一下也会触发一次重建, 但还原回去的正是标签原本那句,
       而它也在预热表里 ⇒ 那一次是**命中**。
    ⚠️ 这里**只调 `texture_update()`**, 不碰 `_orig`: 命中/未命中由包装器自己决定。
    ⚠️ 第 4 项 `font_size` 只给**中奖大字**那批用: 它的字号由 `fit_font_size` 现算,
       **必须一起设**, 否则烘出来的是"模板那个字号"的纹理, 而缓存指纹里含 `font_size`
       ⇒ **一条都命中不了**。
    """
    lbl, text, color = item[0], item[1], item[2]
    _fs = item[3] if len(item) > 3 else None
    _save_t = lbl.text
    try:
        _save_c = tuple(lbl.color)
    except Exception:
        _save_c = None
    _save_f = None
    if _fs is not None:
        try:
            _save_f = float(lbl.font_size)
        except Exception:
            _save_f = None
    try:
        if color is not None:
            lbl.color = color
        if _fs is not None:
            lbl.font_size = float(_fs)
        lbl.text = text
        lbl.texture_update()
    finally:
        try:
            lbl.text = _save_t
            if _save_c is not None:
                lbl.color = _save_c
            if _save_f is not None:
                lbl.font_size = _save_f
        except Exception:
            pass


# ⚠️⚠️ **预热模板必须打 `_texupd_tag`**, 否则整条预热是空转: `_texupd_wrap` 的入口判据是
#    `self._texupd_tag is not None and self.text`, 没打标的标签**直接走 Kivy 老路、
#    压根不进缓存** ⇒ 烘了等于没烘, 而且**不报错**。
# ⚠️ tag 本身**不进** `_texex_key` 的字段表, 所以一个模板就够。
_BIGTEXT_TMPL = {}


def _warm_tmpl(key, factory):
    """按 `key` 缓存一个**预热模板标签**(已打 `_texupd_tag`)。

    ⚠️ **每类标签要各自的模板**: `_texex_key` 含 `halign`/`valign`/`bold`/`font_name` 等,
       飘字是 `halign="center"`, 拿大字的模板去烘 ⇒ 指纹对不上 ⇒ **一条都命中不了**,
       而且是**静默**的。所以工厂由调用方给。
    """
    lb = _BIGTEXT_TMPL.get(key)
    if lb is None:
        try:
            lb = factory()
            _tag_texupd(lb, key)
            _BIGTEXT_TMPL[key] = lb
        except Exception:
            lb = None
    return lb


def _bigtext_tmpl():
    return _warm_tmpl("结算大字",
                      lambda: Label(text="", bold=True, size_hint=(None, None)))


def _toast_tmpl():
    # ⚠️ 与 `center_toast` 里的建法**逐字一致**
    return _warm_tmpl("飘字",
                      lambda: Label(text="", bold=True, halign="center",
                                    size_hint=(None, None)))


def _toast_warm_items(rw):
    """列出**飘字**(`center_toast`)**能枚举**的那几条, 让它的第一次也是缓存命中。

    ⚠️ 文案/颜色/字号**全部从 `center_toast` 的调用点抄下来** —— 这几条是散在调用点上的
       字面量, 不是从代码派生的常量; 改了调用点这里不会自动跟, 所以每条都标了出处行号。
    ⚠️ 字号必须和 `center_toast` 里那句 `fit_font_size(text, sp(size), seen, True)`
       **同口径**(`seen = max(80, GameArea.width * 0.94)`), 否则指纹里的 `font_size` 对不上。
    """
    out = []
    _lb = _toast_tmpl()
    if _lb is None or rw is None:
        return out
    try:
        _ga = getattr(rw, "game_area", None)
        _seen = max(80.0, float(getattr(_ga, "width", 0.0) or 540.0) * 0.94)
        _nl = chr(10)          # 有一条文案自带换行
        _tasks = [
            ("先等这一发落定", COL_FIRE, 26),
            ("重放失败：找不到挂载点", COL_FIRE, 26),
            ("弹珠数量不足" + _nl + "请重置或降低投入", COL_FIRE, 26),
            ("弹珠数量已调整到1000个", COL_GREEN, 28),
        ]
        for _v in (20, 50, 100):                    # 轮次档位, 与 `_set_max_plays` 一致
            _tasks.append(("每轮已设定为%d次" % _v, COL_GREEN, 20))
        # ⚠️⚠️ 去重键**必须含文案**: 缓存指纹里有 `text`, 文案不同就是不同条目。
        #    只按 (字号, 颜色) 去重会把全是 COL_FIRE + 同一个字号的那三条合并成一条
        #    ⇒ 只烘第一条, 其余**照旧冷开且不报错**(实测 7 条只烘进 3 条)。
        _seen_fs = set()
        for _t, _c, _sz in _tasks:
            _fs = fit_font_size(_t, sp(_sz), _seen, True)
            if (_t, _fs, _c) in _seen_fs:
                continue
            _seen_fs.add((_t, _fs, _c))
            out.append((_lb, _t, hex_rgb(_c) + (1,), _fs))
    except Exception:
        pass
    return out


def _bigtext_warm_items(rw):
    """列出**中奖大字/阴影**该预烘的那些整串, 让"第一次中奖"也是缓存命中。

    ⚠️ 每次中奖都是**现建 Label**, `_TEXEX_G` 能救它, 但每种 (文案, 颜色) 第一次出现
       仍要付一次真光栅化(真机实测 8~11 毫秒), 而一帧预算只有 6 毫秒 ⇒ 那一帧必卡。
    ⚠️ 枚举范围**从代码派生**(`VALUE_SHAPE` / `PRESETS` / `slot_color`), 不许手抄一份 ——
       抄的那份迟早和真值脱钩, 而脱钩的表现是"预热白做、还不报错"。
    ⚠️ 枚举不全**不会出错**: 没预到的组合第一次照旧走 Kivy 老路, 之后命中。
    ⚠️ **字号必须和 `big_result_text` 用同一句算**(同一个 `avail`、同一个 `base`)——
       指纹里含 `font_size`, 差一点就是一条都命中不了。
    """
    out = []
    _lb = _bigtext_tmpl()
    if _lb is None or rw is None:
        return out
    try:
        _ga = getattr(rw, "game_area", None)
        _avail = max(80.0, float(getattr(_ga, "width", 0.0) or 540.0) * 0.94)
        _bet = getattr(rw, "bet", DEFAULT_BET)
        if _bet not in PRESETS:
            _bet = PRESETS[0] if PRESETS else 1
        _mults = sorted({2} | {int(_k) for _d in VALUE_SHAPE.values() for _k in _d})
        _tasks = [(0, "未中", COL_FIRE, sp(36))]          # m=0 那条, 与 `big_result_text` 一致
        for _m in _mults:
            _tasks.append((_m, "+%d" % (_bet * _m), slot_color(_m), sp(48)))
        for _m, _txt, _col, _base in _tasks:
            _sz = fit_font_size(_txt, _base, _avail, True)
            _c = hex_rgb(_col) + (1,)
            out.append((_lb, _txt, _c, _sz))               # 大字
            out.append((_lb, _txt, (0, 0, 0, 0.6), _sz))   # 黑影(同字同号, 只差颜色)
    except Exception:
        pass
    return out


# ===========================================================================
# 帧探针包装器(老版 5312-5557)
# ===========================================================================
def _texupd_wrap():
    """给 `Label.texture_update` 挂计数器 + **一层按标签自己的文字纹理缓存**。

    ⚠️ Kivy 的文字**两趟画**: 第一趟只量宽高, 第二趟才是真光栅化(4~10.7 毫秒, 由 Texture
       回调在"纹理下次被用到时"触发)。**命中缓存时直接换纹理 ⇒ 三样全跳过。**
    ⚠️ **为什么按标签自己存、不做全局共享**: `CoreLabel.refresh()` 在宽高不变时会
       `texture.ask_update(...)` **原地重画进同一张纹理** ⇒ 跨标签共享会让 A 改文字时
       把 B 正在显示的字悄悄改掉(只在同尺寸时发生)。按标签存零风险。
    ⚠️ **只对打了 `_texupd_tag` 的标签生效** —— 普通按钮/弹窗/档位按钮一律走原路,
       把影响面压到最小。
    ⚠️ **必须由启动路径调一次**(老版在模块导入时调)。没调 = 整层缓存不存在, 且不报错。
    """
    try:
        from kivy.uix.label import Label as _L
    except Exception:
        return
    _orig = getattr(_L, "texture_update", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    def texture_update(self, *a, **k):
        _key = None
        if _TEXEX_ON and getattr(self, "_texupd_tag", None) is not None and self.text:
            # ⚠️ 空文字不进缓存: Kivy 那条路会把 `texture` 置 None、`texture_size` 置 (0,0),
            #    而 `CoreLabel.refresh()` 空文字给的是一张 1x1 占位图 —— 语义不同, 混了会出鬼。
            try:
                _key = _texex_key(self)
                _cl = _texex_get(self, _key)
                if _cl is None:
                    # 本标签没烘过 ⇒ 查**全局**那一份(结算大字/阴影靠这条接上第二次之后)
                    _cl = _texex_get_g(_key)
            except Exception:
                _key, _cl = None, None
            if _cl is not None:
                if _TEXUPD_ACTIVE[0]:
                    _TEXEX_HIT[0] += 1
                if _texex_apply(self, _cl):
                    return
                _key = None          # 纹理没了(被回收/上下文丢失) ⇒ 退回老路, 绝不让画面空着
            else:
                # ⚠️ 未命中时**不让标签自己渲染**, 而是烘一个专用 CoreLabel 并把它的纹理
                #    交给标签 —— 标签显示的东西 100% 来自那张稳定的纹理。
                #    代价: 未命中时多一个 CoreLabel 对象; 烘的工时和原来一模一样。
                try:
                    _cl = _texex_bake(self)
                    if _TEXUPD_ACTIVE[0]:
                        _TEXUPD[0] += 1
                        _TEXUPD_BY[getattr(self, "_texupd_tag", "其他文字")] = \
                            _TEXUPD_BY.get(getattr(self, "_texupd_tag", "其他文字"), 0) + 1
                        _TEXEX_MISS[0] += 1
                    if _texex_apply(self, _cl):
                        _texex_put(self, _key, _cl)
                        _texex_put_g(_key, _cl)      # 两边都存: 下一个新标签才接得上
                        return
                except Exception:
                    _key = None      # 烘不出来就退回 Kivy 老路, 绝不抛
        _t0 = time.perf_counter()
        try:
            if _TEXUPD_ACTIVE[0]:
                _TEXUPD[0] += 1
                _tag = getattr(self, "_texupd_tag", "其他文字")
                _TEXUPD_BY[_tag] = _TEXUPD_BY.get(_tag, 0) + 1
            _orig(self, *a, **k)
        finally:
            # 只在跑分采样期记 —— 平时 `_FRAME_BRK` 没人读, 记了也是白记。
            if _TEXUPD_ACTIVE[0]:
                _brk_add("文字", _t0)
    texture_update._probe_wrapped = True
    texture_update.__name__ = "texture_update"
    _L.texture_update = texture_update


def _texfill_wrap():
    """把**第二趟**文字渲染(`CoreLabel._texture_fill`)也接进子步骤计时。

    ⚠️ 第二趟是**纹理下一次被用到时**由 Texture 回调触发的, 也就是**跑在
       `Label.texture_update` 外面** —— 只包 `texture_update` 的话, 真正贵的那一趟没人认领。
    ⚠️ 包装函数**必须保住 `__name__`**: Kivy 有按方法名找的地方, 改名会静默失联。
    """
    try:
        from kivy.core.text import LabelBase as _LB
    except Exception:
        return
    _orig = getattr(_LB, "_texture_fill", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    def _texture_fill(self, texture, *a, **k):
        if not _TEXUPD_ACTIVE[0]:
            return _orig(self, texture, *a, **k)
        _t0 = time.perf_counter()
        try:
            return _orig(self, texture, *a, **k)
        finally:
            _brk_add("填纹", _t0)

    _texture_fill._probe_wrapped = True
    try:
        _texture_fill.__name__ = "_texture_fill"
        _LB._texture_fill = _texture_fill
    except Exception:
        pass


def _swap_wrap():
    """把 `Window.flip()`(真正 swap 那一步)包起来计时, 写进 `_FRAME_SWAP`。

    ⚠️ **包的是 `type(Window).flip`, 不是 `Window.flip`** —— 后者是绑定方法, 赋值上去
       只给实例加一个属性, 而真正被调的是 `WindowBase.on_flip` 里的 `self.flip()`
       (走类) ⇒ 撞不到实例属性上。
    ⚠️ 必须 try/except 兜住: `self._win` 在无窗口/自测路径上可能是 `None`。埋点绝不能让
       主循环抛。
    ⚠️ `Window` 在这里**惰性 import**: 老版在模块顶部 import 它, 而那会在 import 期就建出
       真窗口 —— 本模块的字宽/字号那半边是纯计算, 不许被窗口拖住。
    ⚠️ **必须由启动路径调一次**(老版在模块导入时调)。
    """
    try:
        from kivy.core.window import Window
    except Exception:
        return
    _cls = type(Window)
    _orig = getattr(_cls, "flip", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    def flip(self, *a, **k):
        if not _TEXUPD_ACTIVE[0]:
            return _orig(self, *a, **k)
        # ⚠️ **必须在 `_orig` 之前记** —— `_on_flip` 是在 `_orig` 里面被派发的,
        #    它读完 `_FRAME_BRK` 就清空; 记晚了这一帧就白记。
        if _FRAME_END[0] > 0.0:
            # 两段之和 == 老版那一格「尾」的总和, 语义不丢; 拿不到 Clock 标记就原样退回。
            _ce = _CLOCK_END[0]
            if _ce > _FRAME_END[0]:
                _FRAME_BRK["尾·回调"] = (_FRAME_BRK.get("尾·回调", 0.0)
                                        + (_ce - _FRAME_END[0]))
                # ⚠️⚠️ **减掉 on_draw** —— 这一格的定义是「Clock.tick 结束 → flip 开始」,
                #    而 `Window.on_draw` 正落在里面。不减的话 `尾·空档` 和 `尾·on_draw`
                #    **重叠**, 读数的人一相加就得到比整帧还长的"总耗时"。
                _FRAME_BRK["尾·空档"] = (_FRAME_BRK.get("尾·空档", 0.0)
                                        + max(0.0, (time.perf_counter() - _ce)
                                              - _ON_DRAW_MS[0] / 1000.0))
            else:
                _brk_add("尾", _FRAME_END[0])
                _FRAME_BRK["尾"] = max(0.0, _FRAME_BRK.get("尾", 0.0)
                                       - _ON_DRAW_MS[0] / 1000.0)
            _ON_DRAW_MS[0] = 0.0
            _FRAME_END[0] = 0.0
        _t0 = time.perf_counter()
        try:
            return _orig(self, *a, **k)
        finally:
            _FRAME_SWAP[0] = (time.perf_counter() - _t0) * 1000.0

    flip._probe_wrapped = True
    try:
        _cls.flip = flip
    except Exception:
        pass


def _ondraw_wrap():
    """把 `Window.on_draw()`(**画布遍历 + GL 提交**)单独计时, 写进 `_FRAME_BRK["尾·on_draw"]`。

    ⚠️ **为什么非要单独这一刀** —— 原来的「尾·空档」= `Clock.tick` 结束 → `flip` 开始,
       里面**混着三件事**: Kivy 的 `Clock.tick_draw()`(派发子控件画布)、`Window.on_draw()`
       (`self.clear()` + `self.render_context.draw()`, 即遍历全部画布指令并逐条发 GL 调用)、
       以及输入派发。真机上那种「**主线程 15.9ms 而 `_frame` 自算只有 0.07ms**」的慢帧
       (见 `danzhu/ui/bench.py` 的慢帧归因与老版 `android/jiaojie.md` 方案六), 就卡在
       这三件的其中一件上 —— **混在一起就没法决策**:
         · 拆开后 **on_draw 大** ⇒ 是 GL 提交 / 驱动 / 合成器那一侧贵的, **改 Python 没用**;
         · 拆开后 **空档大而 on_draw 小** ⇒ 是 Kivy 的 Python 画布派发贵的, 该去减控件/指令。
       老版交接文档对这一步的要求原话: 「定位是否为系统栏、功耗策略或合成器节拍,
       **而不是猜测游戏代码**」。

    ⚠️ 三条与 `_swap_wrap` 相同的规矩:
       ① 包的是 **`type(Window).on_draw`**(类上的), 不是实例属性;
       ② 埋点**绝不能让主循环抛** —— 全程 try/except;
       ③ **必须由启动路径调一次**(`app.py`, 挨着 `_swap_wrap()`)。漏了是**静默**的:
          那一格恒为 0, 看着像"on_draw 根本不花时间"。
    ⚠️ 只有 `_TEXUPD_ACTIVE`(采样期)才记 —— 平时 `_FRAME_BRK` 没人读, 记了白记。
    """
    try:
        from kivy.core.window import Window
    except Exception:
        return
    _cls = type(Window)
    _orig = getattr(_cls, "on_draw", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    def on_draw(self, *a, **k):
        if not _TEXUPD_ACTIVE[0]:
            return _orig(self, *a, **k)
        _t0 = time.perf_counter()
        try:
            return _orig(self, *a, **k)
        finally:
            _ON_DRAW_MS[0] = (time.perf_counter() - _t0) * 1000.0
            _brk_add("尾·on_draw", _t0)

    on_draw._probe_wrapped = True
    on_draw.__name__ = "on_draw"
    try:
        _cls.on_draw = on_draw
    except Exception:
        pass


def _clock_wrap():
    """给 `Clock.tick` 的**结束**盖一个时间戳 —— 「尾」三分段的中间那一刀。

    ⚠️ 必须在 `_orig` **之后**记(`finally`), 而且只在采样期记(`_TEXUPD_ACTIVE`)。
    ⚠️ 拿不到这一刀时 `flip` 那边会**退回原来那一格「尾」** —— 绝不因为少一个标记就把账丢了。
    """
    try:
        from kivy.clock import Clock as _KC
        _ccls = type(_KC)
        _corig = getattr(_ccls, "tick", None)
        if _corig is None or getattr(_corig, "_probe_wrapped", False):
            return

        def tick(self, *a, **k):
            try:
                return _corig(self, *a, **k)
            finally:
                if _TEXUPD_ACTIVE[0]:
                    _CLOCK_END[0] = time.perf_counter()

        tick._probe_wrapped = True
        try:
            _ccls.tick = tick
        except Exception:
            pass
    except Exception:
        pass


# ⚠️ 老版这三条是在模块导入时调掉的(5411 / 5458 / 5556); `_swap_wrap()` 会建真窗口,
#    不进 import 期, 由启动路径显式调一次。
_texupd_wrap()
_texfill_wrap()
_clock_wrap()


# ===========================================================================
# 槽位倍率贴图(老版 10190-10229)
# ===========================================================================
# ⚠️ `slot_color`(老版 10190-10194)的唯一真源在 config.py(已端口), 上面 import 进来
#    即完成转出 —— **不要在这里再抄一份**: 两份迟早会各改各的。

# 槽倍率文字的**贴图缓存**: (倍率, 字号) -> Texture。
# ⚠️ 为什么必须缓存: `_redraw` 每次重掷盘面都会把整块画布重建一遍, 里面每个非空槽都要
#    新建一个 `CoreLabel` 并 `refresh()` = 一次完整光栅化(一次重掷最多 9 次),
#    而它落在**球落定后那一帧**(待机)。倍率只有 2/3/5/10/20/50/100 这么几种。
# ⚠️ 缓存键**必须带字号** `fs`: 它跟着画布缩放走, 转屏/改窗口时字号会变, 那时要重建。
_SLOT_TXT_TEX = {}


def slot_text_tex(m, fs):
    """倍率文字的贴图(带缓存)。见 `_SLOT_TXT_TEX` 处的说明。"""
    k = (int(m), int(fs))
    t = _SLOT_TXT_TEX.get(k)
    if t is None:
        try:
            cl = CoreLabel(text="x%d" % m, font_size=fs, font_name="Roboto", bold=True)
            cl.refresh()
            t = cl.texture
        except Exception:
            return None
        if len(_SLOT_TXT_TEX) > 64:
            _SLOT_TXT_TEX.clear()
        _SLOT_TXT_TEX[k] = t
    return t


def slot_txt(m):
    """槽位数字字色: ×100 深橙白字对比 2.3 太低(大奖会糊), 故黑字(8.0)最跳;
    其余档白字(低档绿蓝红干净醒目, 深红/紫暗底白字最亮)。"""
    return "#0b1220" if m >= 100 else "#ffffff"


# ===========================================================================
# 单行自适应字号(老版 10231-10478)
# ===========================================================================
# 病根: Kivy 的 Label 只有**两种**行为 —— 设了 `text_size` 就折行, 没设就溢出(不裁剪)。
# 没有"缩到放得下"这一档, 而本作 HUD 上几乎每个位置都是**定宽 + 定高**的。
# ⚠️ 只在**单行**标签上用。多行正文不能缩 —— 那会把整段字一起缩小。
FIT_SCALES = (1.0, 0.94, 0.88, 0.82, 0.76, 0.70)
# ⚠️⚠️ **2026-09-15: 原来是"在 [0.42, 0.70] 之间二分 6 次", 改成固定细分档。别再改回二分。**
#    病根: 二分每次都落在一个没人见过的字号上, 而"开一个没开过的字号"在真机上是一次
#    40 毫秒级的字体表打开(`帧31 54.6ms[字号48.6(2次/1测)]` —— **只量了 1 回就烧掉 42 毫秒**,
#    所以那笔钱不在"量了几回", 在"量的那个字号是冷的")。固定档之后全 app 的字号集合
#    塌缩成"每个基准 x 这 13 个固定倍率", 预热表能一次全盖上。
#    ⚠️ **二分在这个问题上是结构性错误**: 它保证产出"这辈子只用一次"的字号。
FIT_FINE = (0.66, 0.62, 0.58, 0.54, 0.50, 0.46, 0.42)
FIT_HARD_FLOOR = FIT_FINE[-1]   # 硬下限; **只防"小到看不见", 不参与塞不塞得下的判断**
_FIT_PX = {}

_FS_OPEN = []            # [(序号, 字号, bold, 调用方/文案前 10 字)] —— 按开的先后
_FS_OPEN_SET = set()     # {(字号, bold)} 去重后 = 开过的**不同** fontid 数
_FS_OPEN_MAX = 300       # 只留最近的, 别让它无限长
# ⚠️ **为什么要静音**: 玩家导出日志要打开「帧率曲线」弹窗, 而那个弹窗**自己的标题**
#    走 `_fit_line` → 一次走满 13 档阶梯 → **一口气开 12~13 个 fontid**
#    ⇒ "测量动作污染被测对象"。所以面板构建期间(含布局收敛, 异步跨几帧)暂停记账。
# ⚠️ **不许静默**: 静音期间被跳过的次数照记并必须印进日志。
_FS_MUTE_UNTIL = [0.0]   # 静音到这个墙钟时刻; 0.0 = 不静音
_FS_MUTED_N = [0]
_FS_MUTE_SEC = 1.5
_FS_COLD_CNT = {}        # fontid -> [冷开次数, 最后一次的调用方] —— 把 P 从"采样"变成"普查"
_FIT_HIST = {}           # 「本帧冷开了几次」→「这种帧出现了多少次」


def text_px(text, fs, bold=False, base=None, ctx=None, force=False):
    """一段文字在字号 fs 下的**单行宽度**(px)。结果缓存。"""
    if not text:
        return 0.0
    key = (text, round(fs, 2), bool(bold))
    # ⚠️ `force=True` **跳过缓存读** —— 只给启动预热用。预热那句一旦命中本函数的缓存,
    #    就**不建 CoreLabel、不开字体**, 预热白做且**不报错**。
    got = None if force else _FIT_PX.get(key)
    if got is None:
        # 这一格就是"冷测量"的定义: 缓存没命中 ⇒ 现建 CoreLabel 在这个精确字号上量一次。
        _FRAME_FIT[1] += 1
        _ck = (round(float(fs), 4), bool(bold))
        _ce = _FS_COLD_CNT.get(_ck)
        _prev_n = _ce[0] if _ce else 0      # 本次**之前**已经冷开过几次(下面才 +1)
        # ⚠️ 必须取记账**之前**的状态 —— 记账之后它当然就在集合里了。
        if time.time() < _FS_MUTE_UNTIL[0]:
            _FS_MUTED_N[0] += 1
        else:
            try:
                _FS_OPEN.append((len(_FS_OPEN), float(fs), bool(bold),
                                 ctx or _COLD_FS_TAG[0] or "?"))
                _FS_OPEN_SET.add((round(float(fs), 4), bool(bold)))
                if len(_FS_OPEN) > _FS_OPEN_MAX:
                    del _FS_OPEN[0]
                if _ce is None:
                    _FS_COLD_CNT[_ck] = [1, ctx or _COLD_FS_TAG[0] or "?"]
                else:
                    _ce[0] += 1
            except Exception:
                pass
        _t_cold = time.perf_counter()
        try:
            _cl = CoreLabel(text=text, font_size=fs, bold=bold, text_size=(None, None))
            _cl.refresh()
            got = _cl.texture.size[0]
            # ⚠️ **冷开够多次 ⇒ 钉住**(见 `_pin_fontid`): 此刻 `refresh()` 刚把它重新插回
            #    order 尾部, 正是摘掉它的最佳时机。`_get_font_id()` 是 Kivy 自己拼的
            #    6 段键 —— **必须用它**, 手拼会错(字体路径是运行期解析出来的)。
            if _prev_n >= _PIN_AFTER - 1:
                if _POPUP_N[0] > 0:
                    _PIN_POPUP_SKIP[0] += 1
                else:
                    _PIN_STAT[2] += 1
                    try:
                        _pin_fontid(_cl._get_font_id())
                    except Exception:
                        pass
        except Exception:
            got = len(text) * fs * 0.55          # 量不出来按汉字宽粗估, 绝不抛
        _cold_ms = (time.perf_counter() - _t_cold) * 1000.0
        if _cold_ms >= COLD_FS_MIN_MS:
            # 找最近的**同 bold** 预热档: 差值小 = 四舍五入对不上, 差值大 = 基准压根
            # 不是一个数。两种病的修法完全不同。
            _nb, _nd = 0.0, -1.0
            for _wv, _wb in _FONT_WARM_ALL:
                if _wb != bool(bold):
                    continue
                _d = abs(_wv - float(fs))
                if _nd < 0.0 or _d < _nd:
                    _nd, _nb = _d, _wv
            # ⚠️ `base` / `ctx` 由调用方传**真值**; 拿不到才退回模块级残留值
            #    (它们只在 `_fit1` 里写, 直接调 `fit_font_size` 的路径上是假数)。
            _base = float(base) if base else float(_COLD_FS_BASE[0])
            _ctx = ctx or _COLD_FS_TAG[0]
            _COLD_FS.append((_cold_ms, float(fs), bool(bold), _ctx, _nb, _nd,
                             (float(fs), bool(bold)) in _WARM_DID, _base,
                             len(_FS_OPEN) - 1))
            _COLD_FS.sort(key=lambda _x: -_x[0])
            del _COLD_FS[5:]
        if len(_FIT_PX) > 512:                   # 余额那类数字会一直变, 别让缓存无限长
            _FIT_PX.clear()
        _FIT_PX[key] = got
    return got


# `fit_font_size` 的**结果缓存**。键 = (文字, 基准字号, 可用宽, 粗体) —— 它是纯函数。
# ⚠️ 为什么值得缓存: `_install_fit` 把 `_fit1` 绑在 **`text` 和 `width` 两条路**上,
#    宽度变化那条**文字根本没变**, 却在用同一份输入把整个阶梯从头再算一遍。
# ⚠️ 缓存**不设超大**: 上限到了整体清空(与 `_FIT_PX` 同策)。
_FIT_SIZE_PX = {}

# 字号**量化网格**(px)。
# ⚠️ 为什么必须量化: "碰一个新字号"在 Kivy 里 = **重新打开一次 TTF 字形表**(桌面实测
#    **26 毫秒**), 而 `fit_font_size` 的二分支会返回 `10.65` 这种任意值。
# ⚠️ 网格别调粗: 1px 网格在 360dp + 小字号(10~12px)上会有约 8% 的字号跳变, 就开始看得出了。
_FIT_GRID = 0.5


def _qfs(x):
    """把一个字号落到量化网格上。

    ⚠️ **2026-09-14: 已停止使用(保留函数只为别处引用不报错)。**
    为什么回退: 真机上 `sp(N)` 是 `N x 密度` 的**非整数**, 落网格后挪了最多 0.25px ——
    而 Kivy 的字体缓存**按精确字号索引**, 于是和启动期预热/布局烘出来的字号**对不上**,
    每次挑字号都变成一次冷开字形表 ⇒ 主线程变忙 ⇒ 物理 benchmark 拿到的 GIL 变少 ⇒
    **纯 CPU 吞吐掉约 6%**。⚠️ 桌面当时测出来是**变快**, 因为桌面上 `sp(48)` 正好 = 48.0,
    量化是空操作 —— **桌面测不到这个副作用**。
    """
    return round(x / _FIT_GRID) * _FIT_GRID


def fit_font_size(text, base_fs, avail_w, bold=False):
    """挑一个"单行塞得进 avail_w"的最大字号档;**返回绝对字号(px)**。

    塞不下就给最小档(FIT_SCALES[-1] = 0.7 倍)—— 宁可小一点, 也不折行/不溢出。
    """
    if not text or avail_w <= 1.0 or base_fs <= 0:
        return base_fs
    _key = (text, round(base_fs, 2), round(avail_w, 1), bool(bold))
    _got = _FIT_SIZE_PX.get(_key)
    if _got is not None:
        return _got
    _res = _fit_font_size_slow(text, base_fs, avail_w, bold)
    if len(_FIT_SIZE_PX) > 256:
        _FIT_SIZE_PX.clear()
    _FIT_SIZE_PX[_key] = _res
    return _res


def _fit_font_size_slow(text, base_fs, avail_w, bold=False):
    """真正干活的那一半。**别直接调它**, 走带缓存的入口。

    ⚠️ 两段都是**固定阶梯**: `FIT_SCALES`(1.0~0.70) 然后 `FIT_FINE`(0.66~0.42)。
    为什么第二段**绝不能改回二分** —— 见 `FIT_FINE` 上面那一段。
    """
    # ⚠️ 把**基准**与**文本**一起传下去 —— 冷字号榜要靠它们指名道姓。
    #    这里不许退回模块级残留值: 本函数是**直接调用入口**(不走 `_fit1`)。
    _ctx = (text or "")[:10]
    for _k in FIT_SCALES + FIT_FINE:
        _fs = base_fs * _k
        if text_px(text, _fs, bold, base=base_fs, ctx=_ctx) <= avail_w:
            return _fs
    # 连硬下限都放不下 —— 给地板档, 而不是折行/盖邻居。
    # ⚠️ 别再试"按比例估一次": 实测字宽**不随字号线性变**(同一串在 14.95 和 14.05 下量出来
    #    都是 139px, 字形步进被取整), 估出来的值量出来仍然放不下。**估计这条路是死的。**
    return base_fs * FIT_HARD_FLOOR


# ===========================================================================
# 飞行球贴图(老版 10480-10557)
# ===========================================================================
# ⚠️ `_BALL_RIM_K` / `_BALL_BAND_*` 是弹珠贴图的"型"参数, 老版在 8472-8475 定义,
#    与杯中球 `_ball_texture`(fx 层)**共用同一组数**。归属表没给它们定位, 这里先落地;
#    fx 层要用请 `from .text import ...`, **不要另抄一份**。
_BALL_RIM_K = 0.74       # 边缘暗环: p>0.40 的色标往外挤成 1-(1-p)*K ⇒ 亮球身更大、过渡更短
_BALL_BAND_W = 0.66      # 猫眼带宽度倍率
_BALL_BAND_S = 0.76      # 猫眼带强度倍率
_BALL_BAND_SPAN = 0.92   # 猫眼带长度倍率
_BALL_TEX = None


def ball_texture():
    """程序化径向渐变小球贴图(对应 tkinter 版 PIL 渐变, 纯 Python 生成, 零依赖)。"""
    global _BALL_TEX
    if _BALL_TEX is not None:
        return _BALL_TEX
    d = _BALL_TEX_PX
    r = d / 2.0
    # 外圈是金色向深金/褐金的细暗边, 而不是纯黑描边;
    # 在深色盘面、弹簧槽与钉阵前能更清楚地分离出来。
    stops = [
        (0.00, (254, 240, 138)), (0.20, (250, 220, 80)), (0.40, (234, 179, 8)),
        (0.65, (202, 138, 4)), (0.82, (172, 108, 9)), (0.92, (128, 69, 8)),
        (0.965, (84, 42, 5)), (0.985, (46, 22, 3)), (0.99, (36, 16, 2)),
    ]
    # 收窄边缘暗环那段, 与杯中球同一套参数。
    stops = [(p if p <= 0.40 else 1.0 - (1.0 - p) * _BALL_RIM_K, c) for (p, c) in stops]
    buf = bytearray(d * d * 4)
    for y in range(d):
        for x in range(d):
            dx = x - r + 0.5
            dy = y - r + 0.5
            dist = math.hypot(dx, dy) / (r - 0.5)
            if dist >= 1.0:
                continue
            rr, gg, bb = stops[-1][1]
            for j in range(len(stops) - 1):
                if stops[j][0] <= dist <= stops[j + 1][0]:
                    s0, c0 = stops[j]
                    s1, c1 = stops[j + 1]
                    f = (dist - s0) / (s1 - s0) if s1 > s0 else 0
                    rr = int(c0[0] + (c1[0] - c0[0]) * f)
                    gg = int(c0[1] + (c1[1] - c0[1]) * f)
                    bb = int(c0[2] + (c1[2] - c0[2]) * f)
                    break
            alpha = 255
            if dist > 0.97:                      # 边缘抗锯齿(羽化带 = 3% 半径)
                alpha = int(255 * (1.0 - dist) / 0.03)
            i = (y * d + x) * 4
            buf[i] = rr
            buf[i + 1] = gg
            buf[i + 2] = bb
            buf[i + 3] = alpha
    ba = math.radians(-32.0)
    off = 0.08 * d                # 中心线偏离圆心(偏右下, 与左上高光错开)
    band_w = 0.09 * d * _BALL_BAND_W
    band_c = (178, 108, 22)       # 焦糖色
    strength = 0.44 * _BALL_BAND_S
    cos_a, sin_a = math.cos(ba), math.sin(ba)
    for y in range(d):
        for x in range(d):
            i = (y * d + x) * 4
            if buf[i + 3] == 0:   # 跳过透明像素, 防边缘渗色
                continue
            dx = x - r
            dy = y - r
            s = dx * cos_a + dy * sin_a          # 沿带方向(-r..r)
            v = -dx * sin_a + dy * cos_a         # 垂直带方向
            if abs(s) < r * _BALL_BAND_SPAN:
                wmax = band_w * math.sqrt(max(0.0, 1.0 - (s / (r * _BALL_BAND_SPAN)) ** 2))
                dv = abs(v - off)
                if dv < wmax:
                    t = dv / wmax
                    w = (1.0 - t * t) ** 2 * strength
                    buf[i] = int(buf[i] + (band_c[0] - buf[i]) * w)
                    buf[i + 1] = int(buf[i + 1] + (band_c[1] - buf[i + 1]) * w)
                    buf[i + 2] = int(buf[i + 2] + (band_c[2] - buf[i + 2]) * w)
    # [高光已删] 只保留焦糖色带(球身径向渐变已够立体)
    tex = Texture.create(size=(d, d), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _BALL_TEX = tex
    return tex


# ===========================================================================
# 预烘字形图集: **白色字形 + 画布染色**, 把 Kivy 那句文字光栅化整条掐掉(老版 10637-10838)
# ===========================================================================
# 为什么"逐字贴图"能成立(全部实测): 项目字体上 `0123456789+` 这 11 个字的 advance
# **逐字相同**、纹理高全部相同; `sum(逐字宽) == 整串宽` 在 8 个字号 x 8 个串上**精确相等**;
# `.pixels` 的 RGB 恒为 255、A 是覆盖率(**不预乘**)⇒ 白字图集 x 画布色 == 原来烘色。
# ⚠️ "彩色大字"和"黑影子"本来在 Kivy 里是**两张图、两次光栅化**且落在同一帧;
#    烘成白色之后两者共用同一批贴图, 只差画布上那一条 `Color`。
# ⚠️ **不进 `_TEXEX_G`**: 那条路缓存的是"整段文字一张纹理", 而这里的目标恰恰是
#    "一张整段纹理都不产生"。两条路各自独立。
# ⚠️ **一个 fontid 都不多占**: 这些标签只落到 2 个字号, 两个都本来就在字号预热表里 ——
#    刚因为往预热表多塞 11 项把游玩路径的字号挤出缓存, **别再往 Kivy 的 64 个 fontid 上添东西**。
_GLYPH_CHARS = "0123456789+"
_GLYPH_ON = False                # 总开关。⚠️ 暂时关着: 打开前必须先解释掉"未解释的 1 像素"
_GLYPH_ATLAS = {}                # {(fs, bold, font): rec}
_GLYPH_ORDER = []
_GLYPH_MAX_KEYS = 8              # 只 2 档, 给足余量; 上限是"别再撑爆字形表"的兜底
_GLYPH_MAX_CHARS = 12            # 最长串 `+10000`(投注 100 x x100)
_GLYPH_HIT = [0]
_GLYPH_MISS = [0]                # 退化次数 —— **收益归零是静默的, 所以必须记账**


def _glyph_key(fs, bold):
    # ⚠️ **不 round**: 运行期算的是 `base * k` 的原值, round 过就是**另一个 fontid**,
    #    预热/图集全白做。
    return (float(fs), bool(bold), _GLYPH_FONT)


def _glyph_bake(fs, bold):
    """烘一档(11 个字形)。任何一步不对**返回 None** ⇒ 这一档永不启用, 退回普通 Label。

    ⚠️ 三道门禁都是**真的会失败**的, 不是"看起来对":
      ① 每个字形 `bind()` 之后**必须**数得出 alpha>0 的像素 —— 这就是"`refresh()` 只量
         宽高, `bind()` 才触发第二趟真光栅化"那条账。漏了它的症状是**图集全空、画面没字,
         而且不报错**。
      ② 11 个字的高度必须**完全一致** —— 否则"同一条基线"这条假设不成立, 拼出来会歪。
      ③ 逐字宽相加 == `text_px` 整串量(**`force=True`**)。`force` 不能省: `_FIT_PX` 的
         key 含 text, 命中的话这一句什么都不做。
    """
    try:
        rec = {"fs": float(fs), "bold": bool(bold), "adv": {}, "tex": {}, "keep": [],
               "h": 0, "ok": False}
        # ⚠️⚠️ **必须把 11 个字当一整串烘一次, 再按 advance 切** ——
        #    **绝不可以一个字一个 CoreLabel 地烘**: 逐像素对照实测, 单独烘出来的字形
        #    竖直落点比"在整串里"的**高半个像素**, 整块字差 704 个像素(平移 ±2 行扫过
        #    一遍, 0 偏移最优 ⇒ 不是位移, 是 SDL 的次像素定位)。
        _cl = CoreLabel(text=_GLYPH_CHARS, font_size=float(fs), bold=bool(bold),
                        font_name=_GLYPH_FONT, text_size=(None, None))
        _cl.refresh()
        _t = _cl.texture
        if _t is None:
            return None
        _t.bind()                                       # 门禁①的前提: 第二趟必须在这里付掉
        _W, _H = _t.size
        if _H <= 0:
            return None
        _adv = [int(text_px(c, float(fs), bool(bold), force=True)) for c in _GLYPH_CHARS]
        # 门禁③: 逐字宽相加 == 整串纹理宽。不过就整档作废(宁可退回 Label, 不许拼歪)。
        if sum(_adv) != _W:
            return None
        # 门禁①: 每个字都真的画出了东西(空字形 = 这档废掉, 别静默拼出一串空白)
        _px = _t.pixels
        _x = 0
        for _c, _w in zip(_GLYPH_CHARS, _adv):
            if _w <= 0:
                return None
            _n = 0
            for _xx in range(_x, min(_x + _w, _W)):
                for _yy in range(_H):
                    if _px[(_yy * _W + _xx) * 4 + 3] > 8:
                        _n += 1
            if _n == 0:
                return None                             # 空字形 ⇒ 整档作废
            _st = _t.get_region(_x, 0, _w, _H)
            _st.mag_filter = "linear"
            _st.min_filter = "linear"
            rec["adv"][_c] = _w
            rec["tex"][_c] = _st
            _x += _w
        rec["h"] = _H
        rec["keep"].append(_cl)                         # ⚠️ 强引用: 子纹理靠父纹理活着
        # 门禁②: 逐字宽相加 == `text_px` 整串量(拿几个真串验, 不只验字符集本身)
        for s in ("0", "8", "+2", "+10000", "273300", "1234567890"):
            if sum(rec["adv"][c] for c in s) != text_px(s, float(fs), bool(bold), force=True):
                return None
        rec["ok"] = True
        return rec
    except Exception:
        return None


def _glyph_put(fs, bold, rec):
    k = _glyph_key(fs, bold)
    if k in _GLYPH_ATLAS:
        return
    _GLYPH_ATLAS[k] = rec
    _GLYPH_ORDER.append(k)
    while len(_GLYPH_ORDER) > _GLYPH_MAX_KEYS:
        _GLYPH_ATLAS.pop(_GLYPH_ORDER.pop(0), None)


def _glyph_rec(fs, bold):
    if not _GLYPH_ON or not fs:
        return None
    _r = _GLYPH_ATLAS.get(_glyph_key(fs, bold))
    return _r if (_r and _r.get("ok")) else None


def _glyph_quads(rec, text):
    """-> `([(子纹理, x偏移, 宽)...], 总宽)`; 出现字符集外的字符**立刻返回 None**。

    ⚠️ **这条 `None` 是硬边界, 不许绕过**: `未中` 这两个汉字、任何含空格或斜杠的串
       (实测那种串"逐字和"与"整串宽"差 1~8px)**必须**退回普通 `Label`。
       静默拼出来 = 字距错、宽度错, 而画面"看着差不多"。
    """
    out, x = [], 0
    for ch in text:
        t = rec["tex"].get(ch)
        if t is None:
            return None
        w = rec["adv"][ch]
        out.append((t, x, w))
        x += w
    return out, x


def _glyph_keys_reachable(rw):
    """枚举**运行期真的会用到**的档 —— 不许手抄阶梯, 一律让 `fit_font_size` 自己算。

    ⚠️ 每开一个新字号在真机上是一次字形表的打开, 而 Kivy 的字形缓存只有 64 项。
    ⚠️ `未中`(sp(36))**故意不烘** —— 它不在 `_GLYPH_CHARS` 里, 必退 Label。
    """
    out = []

    def _add(fs, bd):
        k = (float(fs), bool(bd))
        if k[0] > 0 and k not in out:
            out.append(k)

    try:      # 结算大字: 文案 = "+N", N = 投注档 x 倍率(与 `_build_texwarm` 同一个值域)
        mults = sorted({2} | {int(_k) for _d in VALUE_SHAPE.values() for _k in _d})
        bets = list(PRESETS) + [int(getattr(rw, "bet", DEFAULT_BET) or DEFAULT_BET)]
        _aw = max(80.0, float(getattr(rw, "width", 540) or 540) * 0.94)   # 与 big_result_text 同一句
        for _b in bets:
            for _m in mults:
                _add(fit_font_size("+%d" % (_b * _m), sp(48), _aw, True), True)
    except Exception:
        pass
    try:      # 余额: 数字等宽 ⇒ 位数就决定档, 用全 8 代表
        _lb = getattr(rw, "balance_lbl", None)
        _base = float(getattr(_lb, "_fit_base", 0.0) or 0.0)
        if _base > 0:
            _av = max(1.0, float(_lb.width))
            for _n in ("8", "88", "888", "8888", "88888", "888888", "8888888"):
                _add(fit_font_size(_n, _base, _av, True), True)
    except Exception:
        pass
    return out[:_GLYPH_MAX_KEYS]


def _glyph_warm_step(area):
    """`prebake_step` 里一次烘一档(自链式, 别一帧烘完 —— 一帧 11 次字形表 = 一记长帧)。"""
    if not _GLYPH_ON:
        return False
    _rw = getattr(area, "game", None)
    if getattr(_rw, "_glyph_keys", None) is None:
        _rw._glyph_keys = _glyph_keys_reachable(_rw)
        _rw._glyph_i = 0
    _keys = _rw._glyph_keys
    _i = int(getattr(_rw, "_glyph_i", 0))
    if _i < len(_keys):
        _rw._glyph_i = _i + 1
        _fs, _bd = _keys[_i]
        _r = _glyph_bake(_fs, _bd)
        if _r is not None:
            _glyph_put(_fs, _bd, _r)
        return True
    return False


# ===========================================================================
# 画布染色(老版 10841-10850 + 12708-12784)
# ===========================================================================
def _fx_fade_set(w, col, alpha):
    """特效淡出。⚠️ 比 `_fade_set` 多乘一个**基色 alpha**。

    为什么必须有这一条: `_fade_set` 只写 alpha、保留 rgb —— 老路上没问题, 因为那时
    **颜色(含阴影的 0.6)是烘在纹理里的**, 画布那条 Color 只管透明度。
    换成白字图集之后**基色搬到了画布上**, 于是 `_fade_set(col, 0.5)` 会把阴影的 `0.6`
    **覆盖成 0.5** ⇒ 阴影在淡出段比基线**更黑**。
    普通 `Label` 没有 `_alpha0` ⇒ 默认 1.0 ⇒ 与改动前**完全一致**。
    """
    return _fade_set(col, alpha * float(getattr(w, "_glyph_alpha0", 1.0) or 1.0))


def _lbl_canvas_color(lbl):
    """取 `Label` 画布里那条 `Color` 指令 —— 用来做**不重建纹理**的淡出。

    ⚠️ Kivy 把颜色**烘进字形纹理**(画布里那条 `Color` 实测恒为 (1,1,1,1)), 所以写
       `label.color` 就等于**重测字形 + 重光栅化 + 重建纹理 + 上传**。而淡出期 alpha
       一直在变 ⇒ 中奖大字的 20 档量化 x 2 个 Label = **每次中奖 40 次重建**。
    改写成那条 `Color` 的 rgba ⇒ 给**已烘好的纹理乘一个 alpha**, 零重建。
    ⚠️ 拿不到就返回 None, 调用方**退回老路**, 绝不静默不淡出。
    """
    try:
        from kivy.graphics import Color as _KC
        for ch in lbl.canvas.children:
            if isinstance(ch, _KC):
                return ch
    except Exception:
        pass
    return None


def _fade_set(col, alpha):
    """把那条画布 Color 的 alpha 设掉(颜色分量保持原样)。成功返回 True。"""
    if col is None:
        return False
    try:
        r, g, b, _a = col.rgba
        col.rgba = (r, g, b, alpha)
        return True
    except Exception:
        return False


def _tint_from(baked_hex, target_hex, alpha=1.0):
    """算出"画布染色"的系数: 把烘成 `baked_hex` 的纹理, 乘多少能得到 `target_hex`。

    ⚠️ **必须逐通道算, 不能拍一个 0.5 这种数**: Kivy 的着色是 **RGB 相乘**,
       最终 = 纹理RGB x 画布Color ⇒ 系数只能是 target/baked(逐通道)。
       三个通道的比例不一样, 用同一个数乘会偏色。
    ⚠️ 这个公式**实测钉过**(直接烘 `CoreLabel` 读 `texture.pixels` 最亮像素):
         color=GRAY@0.6  ⇒ rgba (90, 106, 140, 153)   ← RGB 仍是 GRAY, 只有 A = 0.6 x 255
       ⇒ **Kivy 不预乘 RGB**, 所以"烘 TEXT + 乘 GRAY/TEXT + alpha 0.6"**精确等于**老做法。
    """
    _b = hex_rgb(baked_hex)
    _t = hex_rgb(target_hex)
    return (_t[0] / max(1e-6, _b[0]), _t[1] / max(1e-6, _b[1]),
            _t[2] / max(1e-6, _b[2]), alpha)


# `_set_controls_enabled` 用的染色系数。⚠️ **必须排在 `_tint_from` 之后** ——
# 它是模块级调用, 放前面会 NameError。
_TINT_BRIGHT = (1.0, 1.0, 1.0, 1.0)
_TINT_DIM = {"sub": _tint_from(COL_TEXT, COL_GRAY, 0.6),
             "text": _tint_from(COL_TEXT, COL_GRAY, 0.6)}


def _set_lbl_tint(lbl, rgba):
    """给标签**染色**而不重建纹理。成功返回 True; 拿不到画布那条 Color 就返回 False。

    ⚠️ 为什么不用 `lbl.color = ...`: Kivy 把颜色**烘进字形纹理**, 写一次就是一次
       "重测字形 + 重光栅化 + 重建纹理 + 上传"(真机实测 4~7 毫秒)。而
       `_set_controls_enabled` 每发球要改 3 个标签的颜色、而且**正好落在"回 ready"那一拍**。
    ⚠️ 拿不到就返回 False, 调用方**必须退回写 `lbl.color`** —— 绝不静默不染色
       (那会变成"状态卡住时看不出按钮是暗的")。
    """
    _c = _lbl_canvas_color(lbl)
    if _c is None:
        return False
    try:
        _c.rgba = tuple(rgba)
        return True
    except Exception:
        return False
