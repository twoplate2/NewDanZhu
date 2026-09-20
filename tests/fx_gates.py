# -*- coding: utf-8 -*-
"""中奖玻璃杯表现层的桌面自检(无窗口, 不弹 GUI) —— 老版 `tools/fx_probe.py` 的端口。

跑法:  python tests/fx_gates.py            (在 new_danzhu/ 下)

这是老版那份 3240 行的 `tools/fx_probe.py` 逐条搬到重构版上。**判据一个字没改** ——
阈值、比较符、期望值、`check(...)` 的语义、打印文案全部照旧。改的只有三样:

  ① **取值路径**: 老版是单文件平铺命名空间(`import main as M`), 新版是分包。
     每个 `M.X` 按「它现在住哪个模块」重指(见下面 import 块)。
  ② **源码断言的文件**: 老版读生成物 `android/main.py`(出货的那个文件); 新版读
     `danzhu/**/*.py` + `main.py` 里**对应的那个模块**。整文件级别的扫描用 `SHIPPED`
     (全部出货源码拼起来) —— 它就是老版那个 `main_src` 在新架构下的对应物。
  ③ **少数「名字换了、性质没换」的锚点**(如 `_frame` 里推进演出的 `tick_draw()` 在新版
     是 `_dispatch(_events)`)。每一条都写在该段代码旁, 并汇总在交付报告里。

⚠️⚠️ **老版自己有 18 条已知 FAIL**(mipmap 没开、「中位跑分」文案已改…), 那是老版
    **已知且接受**的状态。⇒ **新版的验收靶子是「失败集合与老版完全相同」**, 不是「零失败」:
    多一条 FAIL = 新版真错了; 少一条 FAIL = 门禁被削弱了(同样算错)。
    老版存档: `tests/golden/fx_probe_old.txt`(481 行, 369 OK / 18 FAIL)。
    门禁清单: `tests/golden/fx_gates.txt`(380 条)。

无窗口: 只 `import`, 不 `run()`、不建真窗口(与老版一致)。
"""
import ast
import io
import math
import os
import random
import re
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # new_danzhu/
# 老工程根 —— 只剩**开发工具**还得从那儿取(`tools/generate_voice.py` 等, 见下方使用点)。
OLDROOT = os.path.dirname(ROOT)
# ⚠️ **打包根** —— 安卓打包那三份(buildozer.spec / p4a/hook.py / presplash.png)
#    **2026-09-19 已搬进本工程**, 所以这个根现在就是 `ROOT` —— 门禁验的是**自己的**打包一致性,
#    不再是"拿老工程的文件去比"。**这个根全文件只有一份**, 两处都指它
#    (以前是两份口径, 那正是当年 multi-FAIL 的来源):
#      · `[13] 底色三处同值`/presplash —— 直接读那三个文件;
#      · `_app_version()` 的桌面分支要读 `app_root()/buildozer.spec`。
#    ⚠️ 若这两个文件将来又被挪走, 这里会**如实变红**(而不是静默退回老工程)。
PACK_ROOT = ROOT
sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ⚠️ `app_root()` 取的是**主脚本**目录 —— 跑 `python tests/fx_gates.py` 时它是 tests/,
#    而工程根(出货 `main.py` 所在、`voice/` 与 `buildozer.spec` 所在)是上一层。
#    与 `tests/audio_gates.py` 设 `DANZHU_VOICE_DIR` 同一个理由。
os.environ.setdefault("DANZHU_APP_ROOT", ROOT)
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_LOG_LEVEL", "warning")

from kivy.core.image import Image as _CoreImage                      # noqa: E402
from kivy.graphics import (Color, PopMatrix, PushMatrix, Rectangle,  # noqa: E402
                           Rotate)
from kivy.metrics import sp                                          # noqa: E402
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout                             # noqa: E402
from kivy.uix.button import Button                                   # noqa: E402
from kivy.uix.widget import Widget                                   # noqa: E402

from danzhu import game as GAME                                      # noqa: E402
from danzhu import geo                                               # noqa: E402
from danzhu import physics as PHYS                                   # noqa: E402
from danzhu import rules as RULES                                    # noqa: E402
from danzhu.audio import backend as BACKEND                          # noqa: E402
from danzhu.audio import bus as BUS                                  # noqa: E402
from danzhu import config as CFG                                     # noqa: E402
from danzhu.fx import pile                                           # noqa: E402
from danzhu.platform import benchcpu as BC                           # noqa: E402
from danzhu.platform import device as DEV                            # noqa: E402
from danzhu.ui import bench as BENCH                                 # noqa: E402
from danzhu.ui import game_area as GAREA                             # noqa: E402
from danzhu.ui import play as PLAY                                   # noqa: E402
from danzhu.ui import root as ROOTMOD                                # noqa: E402
from danzhu.ui import text as TX                                     # noqa: E402
from danzhu.ui import ui_base as UIB                                 # noqa: E402
from danzhu.ui import veil as VEIL                                   # noqa: E402
from danzhu.ui import widgets as WID                                 # noqa: E402
from danzhu.ui import winfx as WFX                                   # noqa: E402

WinPileFX = WFX.WinPileFX
RootWidget = ROOTMOD.RootWidget
Sfx = BUS.Sfx
Game = GAME.Game
LoadVeil = VEIL._LoadVeil
GameArea = GAREA.GameArea
FpsCurve = WID.FpsCurve


# ============================== 出货源码 ===================================
# 老版整个探针只有**一个** `main_src`(生成物 `android/main.py`)。新版那份文件被劈成了
# 这些模块 ⇒ 老版的 `main_src` 在这里对应**两个东西**:
#   · 段内的 `_fn_body` / 精确串断言 -> 指到**定义它的那个模块**(逐条按 grep 确认过);
#   · 整文件级别的扫描(禁某串、数某串出现几次) -> `SHIPPED`(全部拼起来)。
_SRCFILES = {
    "config": "danzhu/config.py",
    "rules": "danzhu/rules.py",
    "geo": "danzhu/geo.py",
    "physics": "danzhu/physics.py",
    "game": "danzhu/game.py",
    "pile": "danzhu/fx/pile.py",
    "synth": "danzhu/audio/synth.py",
    "bus": "danzhu/audio/bus.py",
    "backend": "danzhu/audio/backend.py",
    "voice": "danzhu/audio/voice.py",
    "text": "danzhu/ui/text.py",
    "widgets": "danzhu/ui/widgets.py",
    "game_area": "danzhu/ui/game_area.py",
    "winfx": "danzhu/ui/winfx.py",
    "ui_base": "danzhu/ui/ui_base.py",
    "play": "danzhu/ui/play.py",
    "bench": "danzhu/ui/bench.py",
    "veil": "danzhu/ui/veil.py",
    "root": "danzhu/ui/root.py",
    "app": "danzhu/ui/app.py",
    "device": "danzhu/platform/device.py",
    "boot": "danzhu/platform/boot.py",
    "benchcpu": "danzhu/platform/benchcpu.py",
    "entry": "main.py",
    # ---- 2026-09-19 补漏: 这几份也是**出货源码**, 原来漏在 `_SRCFILES` 外面 ----
    # `main.py --selftest` 会 `from danzhu.bench.selftest import selftest`, 而老版的
    # `main_src`(= 生成物 `android/main.py`)**是含 selftest 代码的**(`def selftest(n=40000)`)。
    # 漏了它, 那 6 条整文件级扫描(`_android_output_rate` 缺席 / `_all_k = FIT_SCALES` 缺席 +
    # 出现 1 次 / `1000.0 / 90.0` 缺席 / `_restyle_selects` 缺席 / `.disabled =` 全文件扫 /
    # voice 名抽取)就**看不见**这个文件里的东西 —— 今天逐串 grep 过它是干净的(判定不变),
    # 但这是个**静默洞**: 哪天把 `.disabled =` 之类写进 selftest.py, 门禁会放过。
    "selftest": "danzhu/bench/selftest.py",
    "init_root": "danzhu/__init__.py",
    "init_audio": "danzhu/audio/__init__.py",
    "init_bench": "danzhu/bench/__init__.py",
    "init_fx": "danzhu/fx/__init__.py",
    "init_platform": "danzhu/platform/__init__.py",
    "init_ui": "danzhu/ui/__init__.py",
}

SRC = {}
for _k, _rel in _SRCFILES.items():
    try:
        SRC[_k] = io.open(os.path.join(ROOT, _rel), encoding="utf-8").read()
    except Exception:                                  # noqa: BLE001
        SRC[_k] = ""
# 老版 `main_src` 的对应物: 全部出货源码。⚠️ 顺序固定 —— 有几条断言靠 `find()` 的先后。
SHIPPED = "\n".join(SRC[_k] for _k in _SRCFILES)

cfg_src = SRC["config"]
game_src = SRC["game"]
text_src = SRC["text"]
winfx_src = SRC["winfx"]
ui_base_src = SRC["ui_base"]
play_src = SRC["play"]
bench_src = SRC["bench"]
veil_src = SRC["veil"]
app_src = SRC["app"]
bus_src = SRC["bus"]
backend_src = SRC["backend"]
root_src = SRC["root"]
main_src = SHIPPED                                     # 整文件级扫描走这份

FAIL = []


def check(ok, name, detail=""):
    # ⚠️⚠️ **先记账、再打印**。原来 `print` 在前、`FAIL.append` 在后, 而 Windows 控制台默认
    #    GBK —— 详情里带 `⚠`/`⇒` 这类字符时 `print` 抛 UnicodeEncodeError ⇒ **后面几百条
    #    断言一条都不跑**(静默少跑)。判据: 一条断言失败**绝不许影响后面的断言**。
    if not ok:
        FAIL.append(name)
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name, ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))




def main():

    print("== 中奖玻璃杯表现层自检 ==")

    # ---- 1. Clock 回调签名(BUILD_APK.md §3.23 红线) ----
    # ⚠️ 老版读的是生成物 `tools/android_part_pile.py`(它被拼进出货文件) ——
    #    新版那一块住在 `danzhu/ui/winfx.py`, 判据不变, 只是文件换了。
    print("\n[1] Clock 回调签名")
    src = winfx_src
    pat = re.compile(r"Clock\.schedule_(?:once|interval)\(\s*([^,()]+?)\s*,")
    bad = []
    seen = set()
    for name in pat.findall(src):
        name = name.strip()
        if name.startswith("lambda"):
            continue
        if name in seen:
            continue
        seen.add(name)
        fn = None
        if name.startswith("self."):
            fn = getattr(WinPileFX, name[5:], None)
        else:
            fn = getattr(WFX, name, None)
        if fn is None:
            bad.append("%s(找不到定义)" % name)
            continue
        try:
            import inspect
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        required_ok = any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                          for p in sig.parameters.values())
        print("      %-24s %s" % (name, sig))
        if not required_ok:
            bad.append("%s 收不下位置参数" % name)
    check(not bad, "所有 schedule_* 回调都能收 1 个位置参数(否则真机启动即闪退)",
          "; ".join(bad))

    # ---- 2. 玻璃纹理 ----
    print("\n[2] 玻璃纹理(assets/glass_tumbler*.png)")
    # ⚠️ 老版: 出货版 `_glass_textures()` 返回 **3 元组**(back, front, fallback)。
    #    新版同形(贴图从 assets/ 挪进了 assets/glass/, 由模块自己按包根定位)。
    back, front, fb = WFX._glass_textures()
    check(back is not None and front is not None,
          "分层玻璃图可加载", "back=%s front=%s fallback=%s"
          % (back is not None, front is not None, fb is not None))
    if back is not None:
        # ⚠️ 改这张断言前先读老工程 generate_glass_tumbler.py 的 OUT 说明。
        # 它**不是** design 坐标(design 是 800x460, 几何全在 pile3d/_map 里, 与贴图边长无关)。
        # 2x(1600x920) 是 2026-09-11 定的, 当时是为关掉真机上的放大采样。
        # 逐像素实测: 4x 收益为零(玻璃杯是**纯低频渐变图**), 而解码耗时涨 3.8 倍。
        check(tuple(back.size) == (1600, 920), "玻璃图画布 1600x920(2x)",
              str(tuple(back.size)))
    if front is not None:
        # 三层必须同尺寸: 只有 back 被换回 1x 的话, 杯子前后壁会差一倍(错位/糊边),
        # 而"能加载、尺寸不报错" —— 只钉 back 是防不住这个的。
        check(tuple(front.size) == (1600, 920), "玻璃前层同尺寸", str(tuple(front.size)))
    for _t, _nm in ((back, "back"), (front, "front")):
        if _t is None:
            continue
        # ⚠️ **必须是 mipmap 系**(`*_mipmap_linear`), 不是"带 linear 就行" —— 字符串
        #    `"linear"` **本身就带 `"linear"`** ⇒ 丢了 mipmap 照样绿(老版真因此漏过一次回归)。
        check(_t.mag_filter == "linear" and "_mipmap_" in _t.min_filter,
              "玻璃图 %s 必须开 mipmap(mag=linear; min 含 _mipmap_, 禁 nearest)" % _nm,
              "mag=%s min=%s" % (_t.mag_filter, _t.min_filter))

    # ---- 2b. 球贴图也必须开 mipmap ----
    # ⚠️ 这几张是**程序合成**的, 没有资产文件可查 —— 所以"静默丢掉 mipmap"在这条路上
    #    没有任何外部痕迹能看出来, 只能靠这条断言。
    print("\n[2b] 球贴图(程序合成)")
    # ⚠️ `ball_texture`(主球)住在 `danzhu/ui/text.py`; `_ball_texture`(杯中球)住在 winfx。
    for _mk, _nm in ((lambda: TX.ball_texture(), "主球"),
                     (lambda: WFX._ball_texture(10), "杯中球")):
        try:
            _t = _mk()
        except Exception as exc:                      # noqa: BLE001
            check(False, "球贴图 %s 可生成" % _nm, repr(exc))
            continue
        check(tuple(_t.size) == (128, 128), "球贴图 %s 画布 128x128" % _nm, str(tuple(_t.size)))
        check(_t.mag_filter == "linear" and "_mipmap_" in _t.min_filter,
              "球贴图 %s 必须开 mipmap" % _nm,
              "mag=%s min=%s" % (_t.mag_filter, _t.min_filter))

    # 分层加载失败时的**整图回退**也要钉尺寸: 它会顶替 back/front 上场, 尺寸不对就是
    # 整只杯子错位。这条路径平时不走, 所以更容易悄悄坏掉。
    _glass_dir = os.path.join(ROOT, "assets", "glass")
    try:
        fb_img = _CoreImage(os.path.join(_glass_dir, "glass_tumbler.png")).texture
        check(tuple(fb_img.size) == (1600, 920), "整图回退资产同尺寸", str(tuple(fb_img.size)))
    except Exception as exc:
        check(False, "整图回退资产 glass_tumbler.png 可加载", str(exc))

    # 远侧环补画层(杯口环后半 + 杯底环后半)也要钉尺寸 —— 它和 back/front 画在**同一个矩形**里。
    try:
        over_img = _CoreImage(os.path.join(_glass_dir, "glass_tumbler_over.png")).texture
        check(tuple(over_img.size) == (1600, 920), "远侧环补画层同尺寸", str(tuple(over_img.size)))
    except Exception as exc:
        check(False, "远侧环补画层 glass_tumbler_over.png 可加载", str(exc))

    # 前后两层是同一只杯子: 非透明范围的**左右边距**必须一致。
    # 采样步长 16(够快), 所以容差给 2*16=32 —— 抓的是"某一层被重新生成成不同几何"。
    def _xspan(tex):
        px = tex.pixels
        step = 16
        w, h = tex.size
        x0, x1 = w, -1
        for y in range(0, h, step):
            row = y * w * 4
            for x in range(0, w, step):
                if px[row + x * 4 + 3] > 8:
                    if x < x0:
                        x0 = x
                    if x > x1:
                        x1 = x
        return x0, x1

    # ---- 3. 球纹理 / 球堆 ----
    print("\n[3] 球纹理与球堆")
    for bet in (1, 10, 50, 100):
        WFX._ball_texture(bet)
    check(sorted(WFX._CUP_BALL_TEX) == [1, 10, 50, 100], "4 档投注色球纹理齐全",
          str(sorted(WFX._CUP_BALL_TEX)))
    ok_cnt, ok_det = True, []
    for n in (2, 3, 5, 10, 20, 50, 100):
        beads, meta = pile.build_pile(pile.PileSpec(n, r_dp=WFX._r_dp_for(n), seed=1))
        proj = pile.project_pile(beads)
        if len(proj) != n:
            ok_cnt = False
            ok_det.append("x%d 只堆出 %d 颗" % (n, len(proj)))
    check(ok_cnt, "7 档堆形颗数 = 倍率", "; ".join(ok_det))
    a = WFX._pile_projected(20, 1)
    b = WFX._pile_projected(20, 1)
    fa = [x for x in a if not x.get("meta")]
    fb2 = [x for x in b if not x.get("meta")]
    check(fa == fb2, "同 (颗数,种子) 逐球心可复现")

    # ⚠️ 这条门禁的判据试错了五版(逐球屏幕位移 / 3D 结构指纹 / 屏幕 Chamfer / 方位扇区直方图 /
    #    Procrustes 残差)。正解 = **双向 Hausdorff 距离**: 它不需要任何对齐, 所以
    #    · 整堆转 60 度 -> 点集几乎重合 -> 小;  · 偏心换朝向 -> 点集完全不重合 -> 大。
    def _hausdorff(pa, pb):
        def one(ps, qs):
            return max(min(((p["sx"] - q["sx"]) ** 2 + (p["sy"] - q["sy"]) ** 2) ** 0.5
                           for q in qs) for p in ps)
        return max(one(pa, pb), one(pb, pa))

    worst_ratio, worst_det, small_bad, big_worst = None, "(没有可比的变体对)", [], None
    for n in (2, 3, 5, 10, 20, 50, 100):
        prs = [[p for p in WFX._pile_projected(n, sd) if not p.get("meta")]
               for sd in range(WFX._PILE_VARIANTS)]
        dia = 2.0 * prs[0][0]["r"]
        for ai in range(WFX._PILE_VARIANTS):
            for bi in range(ai + 1, WFX._PILE_VARIANTS):
                if len(prs[ai]) != len(prs[bi]):
                    continue
                d = _hausdorff(prs[ai], prs[bi])
                ratio = d / dia
                if ratio < 0.30:
                    small_bad.append("x%d v%d-%d %.0f%%" % (n, ai, bi, 100.0 * ratio))
                if n >= 50 and (big_worst is None or ratio < big_worst):
                    big_worst = ratio
                if worst_ratio is None or ratio < worst_ratio:
                    worst_ratio = ratio
                    worst_det = "最差 x%d 变体%d-%d: 双向 Hausdorff = %.0f%% 球径" % (
                        n, ai, bi, 100.0 * ratio)
    # 阈值按实测标定(2026-09-12): 小档 x2~x20 中位 0.59~1.05 球径; 大档 x50/x100 中位
    # 0.47~0.55 球径 —— **明显偏弱**(层间注册只改"整层横移", 大档是填满杯子的堆)。
    check(len(small_bad) <= 12 and worst_ratio is not None and worst_ratio >= 0.20,
          "%d 个堆形变体两两可辨(双向 Hausdorff: 最差 >= 0.20 球径, 且多数对 >= 0.30)"
          % WFX._PILE_VARIANTS,
          "%s | 低于 0.30 的对: %d 个%s%s" % (
              worst_det, len(small_bad),
              (" [" + ", ".join(small_bad[:6]) + "]") if small_bad else "",
              "" if big_worst is None else " | 大档最差 %.0f%%" % (100.0 * big_worst)))

    # 预烘必须铺满全部 (档, 变体) —— 玩期的 key 是 (倍率, 变体号), 只烘一部分的话
    # 同一档别的变体全是冷建(x100 桌面 18.7ms / 安卓估 60~150ms, 卡在 settle 那一帧)。
    miss_key = [(n, sd) for n in (2, 3, 5, 10, 20, 50, 100)
                for sd in range(WFX._PILE_VARIANTS) if (n, sd) not in WFX._PILE_CACHE]
    check(not miss_key, "预烘覆盖 7 档 x %d 变体的全部 %d 个堆形 key" % (
              WFX._PILE_VARIANTS, 7 * WFX._PILE_VARIANTS),
          "缺 %d 个: %s" % (len(miss_key), miss_key[:6]))

    # ---- 3c. 离线坐标表 vs 现算 逐位对拍(红线) ----
    # 球堆坐标是**离线烘好的表**, 运行期只解析 —— 于是多了一种静默事故: 改了球堆参数却忘了
    # 重跑烘焙。那时表和实际行为脱钩, 玩家看到的是**另一个球堆**, 而全链路没有任何东西会报错。
    # 容差 0.06 = 表的定点精度(0.1px 取整)留一点浮点余量。
    drift = []
    for n in (2, 3, 5, 10, 20, 50, 100):
        for v in range(WFX._PILE_VARIANTS):
            baked = WFX._beads_from_baked(n, v)
            if baked is None:
                drift.append("x%d v%d 表里没有" % (n, v))
                continue
            try:
                spec = pile.PileSpec(n, r_dp=WFX._r_dp_for(n), seed=v,
                                     rot_deg=(v * WFX._PILE_ROT_STEP) % 360.0,
                                     sites=WFX._PILE_SITES[v % len(WFX._PILE_SITES)],
                                     scatter=v,
                                     quota=WFX._PILE_QUOTA[v % len(WFX._PILE_QUOTA)])
                live, _meta = pile.build_pile(spec)
            except Exception as exc:
                drift.append("x%d v%d 现算抛异常 %s" % (n, v, str(exc)[:30]))
                continue
            if len(live) != len(baked):
                drift.append("x%d v%d 颗数 %d vs %d" % (n, v, len(baked), len(live)))
                continue
            for bi, li in zip(baked, live):
                if (abs(bi["x"] - li["x"]) > 0.06 or abs(bi["z"] - li["z"]) > 0.06
                        or abs(bi["h"] - li["h"]) > 0.06):
                    drift.append("x%d v%d 漂移 (%.2f,%.2f,%.2f) vs (%.2f,%.2f,%.2f)" % (
                        n, v, bi["x"], bi["z"], bi["h"], li["x"], li["z"], li["h"]))
                    break
    check(not drift, "离线坐标表与现算逐位一致(改了球堆参数必须重跑 bake_pile_data.py)",
          "漂移 %d 处: %s" % (len(drift), drift[:4]))

    # 回退路径必须真的能走 —— 表损坏时不能表现为"那一局没球"
    _bak = WFX._PILE_BAKED
    try:
        WFX._PILE_BAKED = {}
        WFX._PILE_CACHE.clear()
        WFX._PILE_ORDER[:] = []
        fb = [p for p in WFX._pile_projected(100, 0) if not p.get("meta")]
        WFX._PILE_CACHE.clear()
        WFX._PILE_ORDER[:] = []
    finally:
        WFX._PILE_BAKED = _bak
    check(len(fb) == 100, "表被清空时回退到程序化生成仍能堆出 100 颗",
          "回退产出 %d 颗" % len(fb))

    # ---- 3d. 彩蛋档: 必中 + 每格均值 == 档位 ----
    # 三档隐藏档全部"必中"(K_DIST = {9: 1.0})。玩家 2026-09-11 报的「5000% 还tmd空军」就是
    # 漏了这一半: RTP 配得再准, 9 格里照样能有 3 格是空的。
    # ⚠️ `RTP_HIDDEN` 在新版住在 `Game`(老版是 RootWidget 的类属性)。
    hidden = getattr(Game, "RTP_HIDDEN", ())
    hbad, hdet = [], []
    for _lab, _rtp in hidden:
        kd = RULES.K_DIST.get(_rtp) or {}
        dist = RULES.VALUE_DIST.get(_rtp) or {}
        allwin = kd.get(9, 0.0)                     # "9 格全有奖"的概率
        mean = sum(v * w for v, w in dist.items())
        random.seed(20260912)
        empty = 0
        N = 5000
        for _ in range(N):
            if any(v == 0 for v in RULES.roll_multipliers(_rtp)):
                empty += 1
        hdet.append("%s 必中%.0f%% 均值%.3f 空军%d/%d" % (_lab, allwin * 100, mean, empty, N))
        if allwin < 1.0 - 1e-9 or abs(mean - _rtp) > 1e-6 or empty:
            hbad.append(_lab)
    check(hidden and not hbad,
          "彩蛋档全部必中(K_DIST=9格)且每格均值==档位(%d 档)" % len(hidden),
          "; ".join(hdet))

    # ---- 3e. 档位清单只能有一份(唯一真源) ----
    # 2026-09-12 线上闪退: `_boards` 的**初始化**和 `park_ball` 里的**重刷**各写了一份手抄
    # 档位元组, 加彩蛋档时只改到其中一处 —— 切到 2000%/5000% 之后**发射一次**,
    # `self._boards[self.rtp_target]` 就抛 KeyError 闪退。所以这条门禁扫出货文件。
    # ⚠️ 那条手抄本 / 白名单在新版都住在 `danzhu/game.py`。
    _src = game_src
    # ⚠️ 这条只管 `_boards` 的两处。**不要**把正则放宽成通用的 `(0.80, 1.20`。
    _hand = re.findall(r"for r in \(0\.80,\s*1\.20", _src)
    check(not _hand, "档位清单没有第二份手抄本(必须走 _all_rtp())",
          "发现 %d 处手写档位元组" % len(_hand))
    # 读档白名单是同一份清单的**第三处**副本。判据用**正面形态**(必须写出派生调用),
    # 而不是"禁止某个字面量" —— 后者会被注释里的引用误伤。
    _src_code = "\n".join(_l.split("#")[0] for _l in _src.splitlines())
    check('cfg["rtp_target"] in self._regular_rtp()' in _src_code,
          "读档白名单走 `_regular_rtp()` 派生 —— 隐藏档'不保存'由此成为**结构性**事实, "
          "不靠谁记得改那行字面量(那行曾经是第三份手抄清单)",
          "出货文件里%s" % ("找到派生调用" if "_regular_rtp()" in _src_code else "没找到"))

    # ---- 4. 缓存上限 ----
    print("\n[4] 缓存上限")
    for s in range(20):
        WFX._pile_projected(20, s)
    check(len(WFX._PILE_CACHE) <= WFX._PILE_CACHE_MAX,
          "球堆缓存不超过上限(原型无上限, 长局会一直涨)",
          "%d <= %d" % (len(WFX._PILE_CACHE), WFX._PILE_CACHE_MAX))


    # ---- 5. 时长自洽 ----
    print("\n[5] 时长自洽(expected_sec 要盖得住真实播放长度)")
    fx = WinPileFX.__new__(WinPileFX)
    under, slack = 0, 0.0
    leads = []
    holds = []
    revs, touches, margins = [], [], []
    snds = []                                    # 最后一颗球的**最后一次落地音**时刻
    rows = []
    for n in (2, 3, 5, 10, 20, 50, 100):
        WinPileFX.__init__(fx, None)
        # 固定种子: _make_balls 内部用的是无种子 Random, 不钉住的话每次抽到的下落/弹跳
        # 时长都不同, 这条门禁会随机红。
        fx._rng = random.Random(12345)
        WinPileFX._make_balls(fx, n, 10, 1)
        # 退场计时基准 = 最后一颗球**回弹停住**(_last_settle), 揭晓那个 _last_touch
        # (第一次触地)比它早 —— 两者分开, 见 hold_for 处的说明。
        real = WFX.WINDUP + fx._last_settle + WFX.hold_for(n) + WFX.RESULT_FADE
        est = fx.expected_sec(n)
        if est < real:
            under += 1
        slack = max(slack, (est - real) / real)
        # 揭晓基准 = 最后一颗球**第一次触地** + REVEAL_DELAY。
        lead = fx._last_settle - fx._last_touch      # 触地 -> 停住 差多久
        rev = fx.reveal_sec()
        revs.append(rev)
        touches.append(fx._last_touch)
        # 揭晓必须落在"杯子装满静止"这段里(否则数字还没看清就开始淡出)
        fade_at = fx._last_settle + WFX.hold_for(n)
        margins.append(fade_at - rev)
        # 最后一颗球的**最后一次**落地音(_advance_balls 里两处 _bounce: 首次触地 + 二跳)
        snds.append(max(b["t0"] + b["f"] + b["t1"] for b in fx._balls))
        leads.append(lead)
        holds.append(WFX.hold_for(n))
        rows.append("x%-4d 实%.2fs/估%.2fs  触地->停住%.2fs  揭晓%.2fs(余%.2fs)  停留%.1fs"
                    % (n, real, est, lead, rev, fade_at - rev, WFX.hold_for(n)))
    check(under == 0, "7 档 expected_sec 都 >= 真实播放时长(兜底 deadline 的基准, 不能偏低)",
          "偏低 %d 档" % under)
    check(slack < 0.15, "预计时长比实测高出不超过 15%(高太多会让解锁拖沓)",
          "最多 +%.1f%%" % (slack * 100))
    # 两个时刻必须分开: 合在一起的话退场会从"最后一颗刚触地"就计时, 球还在弹杯子就淡出
    # (玩家: "落进容器后消失得太快, 没有回味")
    check(min(leads) > 0.05, "最后一颗球触地后还要弹一会儿(触地->停住 > 0.05s), 两时刻确实分开",
          "最短的一档也差 %.2fs" % min(leads))
    # 揭晓延后(用户定案): 必须是"触地 + REVEAL_DELAY", 不是触地当场
    check(all(abs(r - (t + WFX.REVEAL_DELAY)) < 1e-9 for r, t in zip(revs, touches)),
          "揭晓时刻 = 最后一颗球第一次触地 + REVEAL_DELAY(%.1fs, 让最后几下落地的声音先播完)"
          % WFX.REVEAL_DELAY, "各档 %.2f~%.2fs" % (min(revs), max(revs)))
    # 揭晓不能晚到"已经要淡出了"才报 —— 那样数字还没看清就被收走。
    # ⚠️ 阈值必须**跟着 REVEAL_DELAY 一起看**, 它是解析下界推出来的、不是拍的。
    # 又因为这条只跑 seed=12345 一局/档, 阈值留到解析下界之下才不会被单一种子的运气判红。
    check(min(margins) > 0.30,
          "揭晓落在'杯子装满静止'里, 离淡出起点至少还有 0.30s(玩家来得及看清数字)",
          "最紧的一档只剩 %.3fs" % min(margins))
    # REVEAL_DELAY 的语义("让最后几下落地的声音先播完")必须钉成不变量。
    check(all(r >= s for r, s in zip(revs, snds)),
          "揭晓(触地+REVEAL_DELAY)晚于最后一颗球的最后一次落地音(用户要的语义)",
          "最紧一档 揭晓%.3f / 末声%.3f" % (min(r - s for r, s in zip(revs, snds)) + max(snds),
                                          max(snds)) if revs else "")
    # 停留按倍率分档(hold_for): 地板要够高、要严格递增、要在 9s 硬兜底内
    check(min(holds) >= 0.50, "最小档(x2)的停留够长(>=0.50s; x2 占中奖 55%%, 低于可辨差等于没改)",
          "x2 停留 %.2fs" % holds[0])
    check(all(abs(h - WFX.HOLD_BASE) < 1e-9 for h in holds),
          "七档停留**一致** = HOLD_BASE(2026-09-12 起不再按倍率分档: 装满后由**玩家点击**"
          "才退场, HOLD_BASE 只当'最短停留'用)。改回递增 = 把分档加回来了, 那会和点击逻辑打架",
          str(["%.1f" % h for h in holds]))
    check(WFX.FX_MAX_SEC - (WFX.hold_for(100) + WFX.RESULT_FADE) > 4.0,
          "最高档的尾巴离 FX_MAX_SEC=9s 硬兜底还有充裕余量",
          "x100 尾巴 %.2fs" % (WFX.hold_for(100) + WFX.RESULT_FADE))
    for r in rows:
        print("      " + r)

    # ---- 6. 进场/退场曲线 ----
    # _layers() 在这之前**零自动覆盖** —— selftest 根本不建 GameArea, 上面五项也没有一项
    # 碰它。曲线改坏了是完全静默的, 只有真机上肉眼能发现。
    print("\n[6] 进场/退场曲线(_layers)")
    fx.mode = "result"
    tail = WFX.RESULT_FADE                       # 窗口就是淡出时长(不再有 hold_for 平移)
    # 还没收到关闭请求时必须**停在装满静止态**: dy=0 才算"没退场"。
    # ⚠️ 两条采样都必须在 `_closing_at = 0` 状态下取 —— 先置 1000.0 再采会拿到退场曲线的
    #    起点, 那是"已点击"的样子, 测不出"没点击时会不会自己动"。
    fx._closing_at = 0.0
    _a0 = fx._layers(1000.0)
    _a1 = fx._layers(1000.0 + 99.0)            # 再晚也不许自己动
    check(_a1 == (1.0, 1.0, 1.0, 0.0) and _a0 == (1.0, 1.0, 1.0, 0.0),
          "没收到关闭请求时 _layers() 恒返回装满静止态(1,1,1,0) —— 画面纹丝不动地停着, "
          "**不许**按退场曲线自己往前走(那会在等待期里把杯子淡掉)",
          "t+0 -> %s ; t+99 -> %s" % (_a0, _a1))
    fx._closing_at = 1000.0                    # 下面按"已点击"采退场曲线
    mono, prev, t = True, None, 0.0
    while t <= tail + 0.15:
        a_dim, a_cup, k, dy = fx._layers(1000.0 + t)
        if prev is not None and (a_cup > prev[1] + 1e-9 or a_dim > prev[0] + 1e-9
                                 or k > prev[2] + 1e-9 or dy < prev[3] - 1e-9):
            mono = False
        prev = (a_dim, a_cup, k, dy)
        t += 0.01
    check(mono, "退场四分量全程单调(alpha/k 只降, dy 只升)")
    a_dim_end, a_cup_end, _, _ = fx._layers(1000.0 + tail)
    check(a_cup_end == 0.0 and a_dim_end == 0.0,
          "解锁那一刻道具与压暗都已归零(不留给 canvas.clear() 擦成硬切)",
          "a_cup=%.3f a_dim=%.3f" % (a_cup_end, a_dim_end))
    _, a_tail, _, _ = fx._layers(1000.0 + tail - WFX.EXIT_ALPHA_TAIL)
    check(a_tail == 0.0, "解锁前 EXIT_ALPHA_TAIL 处道具已全透明", "a_cup=%.3f" % a_tail)

    fx2 = WinPileFX.__new__(WinPileFX)
    WinPileFX.__init__(fx2, None)
    fx2.mode = "pending"
    fx2._t0 = 1000.0 + WFX.WINDUP
    a_dim, a_cup, k, dy = fx2._layers(1000.0 + WFX.WINDUP)
    check(abs(a_cup - 1.0) < 1e-9 and abs(k - 1.0) < 1e-9 and abs(dy) < 1e-9,
          "WINDUP 那一刻进场曲线正好收在 (1,1,1,0)(和 win 分支严丝合缝, 不会被截断成瞬移)",
          "a=%.3f k=%.3f dy=%.1f" % (a_cup, k, dy))
    check(WFX.ENTER_CUP_AT + WFX.ENTER_CUP_DUR <= WFX.WINDUP,
          "ENTER_CUP_AT + ENTER_CUP_DUR <= WINDUP(否则进场被 tick() 当场截断)",
          "%.2f <= %.2f" % (WFX.ENTER_CUP_AT + WFX.ENTER_CUP_DUR, WFX.WINDUP))
    # 位移只能朝上: 起手(进场最高处)与退场终态都不许压到槽区隔板顶
    hi = WFX.CUP_BOTTOM - max(WFX.ENTER_RISE, WFX.EXIT_LIFT)
    check(hi < 606.0, "进场起手/退场终态的杯底都在槽区隔板顶(606)之上",
          "最高处杯底 %.1f" % hi)
    check(WFX.CUP_T - WFX.ENTER_RISE > WFX.TEXT_CY_WIN,
          "进场起手杯顶没够到大字 cy", "%.1f > %.1f" % (WFX.CUP_T - WFX.ENTER_RISE, WFX.TEXT_CY_WIN))

    # ---- 7. 空档: 杯子就位到首球入画 ----
    # 这段以前有 0.44~0.55s 屏幕上一个像素都不动(玩家读成"演出结束了"), 靠整场雨前移消掉。
    print("\n[7] 空档(杯子就位 -> 首球入画)")
    gaps = []
    for n in (2, 3, 5, 10, 20, 50, 100):
        WinPileFX.__init__(fx, None)
        fx._rng = random.Random(12345)
        WinPileFX._make_balls(fx, n, 10, 1)
        g = min(b["t0"] + b["f_enter"] for b in fx._balls)
        gaps.append(g)
        # 安全性: 前移量不许大到让球"还没开局就已经落袋/已经画出来"
        for b in fx._balls:
            if b["t0"] < 0 and (-b["t0"] >= b["f_enter"]):
                check(False, "x%d 有球开局就已画在画面上(凭空出现在半空)" % n)
                break
    check(max(gaps) <= 0.20, "七档首球入画都 <= 0.20s(锚点 RAIN_ANCHOR=0.12 + 前移封顶的余量)",
          "%.2f ~ %.2f" % (min(gaps), max(gaps)))

    # ---- 8. 落定阶段: 纯竖直回弹, 球不许横向离开本槽 ----
    # 病根(实踩): landing 循环里只有重力/横向弹簧/地板, **没有隔板碰撞**, 而球带着飞行
    # 末段的横速入槽 -> 回弹期能横着滑过隔板停到隔壁槽(结算槽仍是本槽 = "钱对、球停错")。
    # 现在的解法是入槽瞬间把横速清零(原地竖弹), 横向只剩 LAND_K 弹簧。
    print("\n[8] 落定阶段横向不越界(纯竖直回弹)")
    FLOOR_Y = CFG.FLOOR - CFG.BALL_R
    worst_out = -1e9                      # 必须是 -inf 起步: 越界量全是负数, max(0.0, ...) 会把它吃掉
    for slot_i in range(CFG.NUM_SLOTS):
        tgt = CFG.FIELD_L + (slot_i + 0.5) * CFG.SLOT_W
        for off in (-0.48, -0.3, -0.1, 0.1, 0.3, 0.48):     # 入槽点: 槽内各处(贴边界)
            x = tgt + off * CFG.SLOT_W
            vx, vy = 0.0, CFG.LAND_BOUNCE_MIN_VY
            for step in range(int(0.5 / CFG.FIXED_DT)):
                vx += (tgt - x) * CFG.LAND_K * CFG.FIXED_DT
                vx *= CFG.LAND_DAMP
                vx = PHYS.clamp(vx, -CFG.ALIGN_VX_MAX, CFG.ALIGN_VX_MAX)
                vy += CFG.G * CFG.FIXED_DT
                x += vx * CFG.FIXED_DT
            out = abs(x - tgt) - CFG.SLOT_W / 2.0
            worst_out = max(worst_out, out)
    check(worst_out < 0.0,
          "横速清零后, 落定阶段从任意入槽点出发都不会滑出本槽(弹簧只会拉向槽心)",
          "最坏离边界 %.2f px(负=在槽内)" % worst_out)

    # ---- 10. 揭晓的**投递语义**(不是数值!) ----
    # 为什么要单独有这一段: 上面九节全在测**值域**(公式、区间、时长上界), 没有一节在测
    # **一次性事件被投递了几次**。2026-09-10 实踩: 揭晓判定被关在 `if mode == "win"` 里,
    # 而揭晓时刻通常**晚于** win->result 的切换点 —— 于是 mode 先跳走, 回调一路挂到
    # **下一局**才被放出来。测法: 受控时钟 + 真实的 `WinPileFX.tick()`, 逐帧推进到演出结束。
    print("\n[10] 揭晓的投递语义(恰好一次 + 就在揭晓时刻)")

    class _FakeTime(object):
        t = 1.0e6
        def time(self):
            return self.t
    # ⚠️ 打桩得**contained**: 只换 `winfx` 命名空间里的 `time`(老版换的是 `main.time` ——
    #    那是同一个东西: 老版 `WinPileFX.tick()` 用的就是那个模块级 `time`)。
    _real_time = WFX.time
    _ft = _FakeTime()
    WFX.time = _ft
    try:
        zero = multi = leak = 0
        offs = []
        rounds = 0
        for n in (2, 3, 5, 10, 20, 50, 100):
            for seed in range(40):
                fx4 = WinPileFX.__new__(WinPileFX)
                WinPileFX.__init__(fx4, None)
                fx4._redraw = lambda *a, **k: None
                fx4._rng = random.Random(seed + 1)
                calls = []
                _ft.t = 1.0e6 + seed * 0.0137     # 错开帧相位, 别让所有局同相位
                if not fx4.play_win(n, 10, on_done=lambda: calls.append(_ft.t)):
                    continue
                rounds += 1
                due = fx4._t0 + fx4._last_touch + WFX.REVEAL_DELAY
                # ⚠️ 上界必须盖住**整场**装杯(最后落定 ~2.8s) + 点击等待(HOLD_BASE) + 淡出,
                #    再留余量。按 FX_MAX_SEC 算跑不完; 按 HOLD_BASE+FADE 算又**太短**。
                _cap = int((6.0 + WFX.HOLD_BASE + WFX.RESULT_FADE) * 60)
                for _ in range(_cap):
                    if (fx4.mode == "result" and not fx4._closing_at
                            and _ft.t >= fx4._settled_at + WFX.HOLD_BASE):
                        fx4.request_close()        # 模拟玩家点了一下(受理条件在方法里)
                    fx4.tick()
                    _ft.t += 1.0 / 60.0
                    if fx4.mode == "idle":
                        break
                for _ in range(5):                # 再走几帧: 漏账会在收尾暴露
                    fx4.tick()
                    _ft.t += 1.0 / 60.0
                if not calls:
                    zero += 1
                elif len(calls) == 1:
                    offs.append(calls[0] - due)
                else:
                    multi += 1
                if fx4._on_done is not None:      # mode 已 idle 却还挂着回调 = 会串到下一局
                    leak += 1
    finally:
        WFX.time = _real_time
    check(zero == 0, "每个中奖局的揭晓回调都会被放出去(不许有局丢账)",
          "丢账 %d/%d 局" % (zero, rounds))
    check(multi == 0, "揭晓回调不会放两次", "放两次 %d 局" % multi)
    check(leak == 0, "一局收尾时回调必须已被消费(挂账=下一局会被重入保护提前执行)",
          "残留 %d/%d 局" % (leak, rounds))
    check(not offs or max(offs) <= 3.0 / 60.0 + 1e-9,
          "揭晓就发生在 reveal_sec() 的 3 帧内(不是拖到下一局开头)",
          "最晚 %.1f 帧" % (max(offs) * 60.0) if offs else "")

    # ---- 11. 压暗的覆盖与绘制层级 ----
    # [6] 只测 `_layers()` 的**数值曲线**; 2026-09-11 修的两件事全在曲线之外:
    #   (a) HUD 五行不在 GameArea 的矩形里 —— 板面那块压暗永远盖不到它们;
    #   (b) 压暗矩形原本画在**后层玻璃之前**, 后层玻璃把它提亮回去。
    # 所以这里测**接线**与**绘制次序**, 不重复测曲线([6] 已经测了)。
    print("\n[11] 压暗的覆盖与绘制层级")

    # ⚠️ 文本断言读**出货源码**。老版它读的是 `android/main.py`(那一个文件 = 全部出货代码);
    #    新版对应物是整份 `danzhu/**` + `main.py`, 而每条的切片指到**定义它的那个模块**。
    def _fn_body(src, name):
        # ⚠️ 必须**先定义**: 第一版写在下面某一段判据旁边, 而比它更靠上的几条先引用了它 ⇒
        #    UnboundLocalError, 门禁在中间炸掉、**它后面的 20 条一条都没跑**(实测: 总检查数
        #    从 205 悄悄掉到 185)。**门禁自己静默少跑, 比门禁报红危险得多。**
        _i = src.find("def %s(" % name)
        if _i < 0:
            return ""
        _j = src.find("\n    def ", _i + 1)
        return src[_i:(_j if _j > 0 else len(src))]

    def _code_only(body):
        return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))

    # ⚠️ 锚点重映射: 老版 `_frame` 是"状态机 + 界面"一个函数, 里面直接调 `tick_draw()`;
    #    新版 `_frame` 只剩壳, 推进演出的那一步是 `self._dispatch(_events)`(派发 `ui_tick`
    #    时驱动 `game_area.tick_draw()`)。**判据的关系没变**(压暗要排在推进之后),
    #    只是那一行的名字跟着架构换了。
    m_frame = re.search(r"def _frame\(self, dt\):(.*?)\n    def ", play_src, re.S)
    body = m_frame.group(1) if m_frame else ""
    check("_sync_hud_dim()" in body,
          "_frame 每帧调 _sync_hud_dim(不调 = 压暗层永远停在 0, 静默失效)")
    check(body.find("_dispatch(_events)") >= 0
          and body.find("_dispatch(_events)") < body.find("_sync_hud_dim()"),
          "_sync_hud_dim 在 tick_draw **之后**(演出是靠 tick_draw 推进的; 放前面就晚一帧, "
          "退场结束那帧 HUD 会比板面多黑一整帧)")

    # ---- 文字纹理缓存(2026-09-14) ------------------------------------------------
    # ⚠️ 这套门禁钉的是**三个已经踩过的坑**, 每一个都做过阴性对照:
    #   ① 直接缓存 Kivy 给标签建的 Texture 会**张冠李戴**(尺寸全对, 只有逐像素能抓);
    #   ② `_texex_bake` 漏掉 `cl.refresh()` ⇒ `cl.texture` 恒为 None ⇒ 缓存永远存不进去;
    #   ③ 缓存只对**打了 `_texupd_tag`** 的标签生效; 漏打标 = 那个标签白预热。
    check("_TEXEX_ON = True" in text_src,
          "文字纹理缓存是开着的(`_TEXEX_ON`; 关掉时日志会明说「本版关闭」而不是印 0%)")
    _bk = _fn_body(text_src, "_texex_bake")
    check("_cl.refresh()" in _bk,
          "`_texex_bake` 里调了 `refresh()` —— 漏了它 `texture` 恒为 None, 缓存静默永远存不进")
    check("CoreLabel(**_o)" in _bk and "getattr(lbl, _n)" in _bk,
          "专用 CoreLabel 的属性是**逐个 getattr 抄标签当前的**, 不是写死一份清单")
    check('_o["color"] = tuple(lbl.disabled_color if lbl.disabled else lbl.color)' in _bk,
          "颜色按 Kivy 的 `disabled -> disabled_color` 映射抄(不照做的话灰化态会烘成亮色)")
    _tu = _fn_body(text_src, "texture_update")
    check('_TEXEX_ON and getattr(self, "_texupd_tag", None) is not None' in _tu,
          "缓存只对打了 `_texupd_tag` 的标签生效(普通按钮/弹窗不受影响)")
    check("_texex_apply(self, _cl)" in _tu and "return" in _tu.split("_texex_apply(self, _cl)")[1][:60],
          "命中时直接交纹理并 return —— 第一趟/第二趟/上传三样全跳过")
    check("_texex_put(self, _key, _cl)" in _tu,
          "未命中时烘一个专用 CoreLabel 并存进缓存")
    _ap = _fn_body(text_src, "_texex_apply")
    check("lbl.texture = _t" in _ap and "lbl.texture_size = list(_t.size)" in _ap,
          "交纹理时 `texture` 与 `texture_size` **两个都写**(漏后者会整行文字错位)")
    check("self.text" in _tu and "空文字不进缓存" in _tu,
          "空文字不进缓存(Kivy 那条路给 texture=None/(0,0), CoreLabel 给的是 1x1 占位图, 混了会出鬼)")

    # ---- 预热 ----
    # ⚠️ `prebake_step` 是 `WinPileFX` 的方法(老版也是), 其余预热函数在 `ui/text.py`。
    _ps = _fn_body(winfx_src, "prebake_step")
    check("_build_texwarm" in _ps and "Clock.schedule_once(self.prebake_step, 0.05)" in _ps,
          "预热链里加了文字纹理预热, 且**一帧只走一个组合**(挤一帧就是自己造一记长帧)")
    check("_texwarm_done" in _ps,
          "预热有一次性闸门, 不会每帧重跑")
    _bw = _fn_body(text_src, "_build_texwarm")
    for _s in ("按住蓄力发射", "发射!", "未中", "即将入袋…", "弹跳中…", "入场中…"):
        if _s not in _bw:
            check(False, "预热表里少了状态栏文案 %s" % _s)
            break
    else:
        check(True, "预热表覆盖了状态栏的固定文案")
    check("_lb.color" in _bw and "hex_rgb(COL_GRAY) + (0.6,)" not in _bw,
          "两个标题标签只烘**一个色**(它们的 .color 已恒定, 亮暗走画布染色)")
    _wo = _fn_body(text_src, "_warm_one")
    check("finally" in _wo and "lbl.text = _save_t" in _wo,
          "`_warm_one` 走完**必须还原**文案与颜色(finally)")

    # 四个"每发球被改"的标签必须打标, 否则预热白做
    for _attr, _tag in (("mute_btn", "音效按钮"), ("round_btn", "轮次按钮"),
                        ("_rtp_title_lbl", "返还率标题"), ("_bet_title_lbl", "投注标题")):
        if ('_tag_texupd(self.%s, "%s")' % (_attr, _tag)) not in ui_base_src:
            check(False, "`%s` 没打 tag(不打标 = 不进缓存 = 预热白做)" % _attr)
            break
    else:
        check(True, "mute_btn / round_btn / 两个标题标签都打了 tag(不打标就不进缓存)")

    # ---- 画布染色替代写 `.color`(2026-09-14) ----
    # 病根: Kivy 把颜色烘进字形纹理 ⇒ 写一次 `label.color` 就是一次 4~7 毫秒的重排。
    # ⚠️ Kivy **不预乘 RGB**, 所以"烘 COL_TEXT + 画布乘 COL_GRAY/COL_TEXT + alpha 0.6"
    #    **精确等于**老做法(逐像素对比 25200 点最大差 1/255)。
    check("def _tint_from(" in text_src and "def _set_lbl_tint(" in text_src,
          "有 `_tint_from`(逐通道算系数)与 `_set_lbl_tint`(写画布那条 Color)")
    check('_TINT_DIM = {"sub": _tint_from(COL_TEXT, COL_GRAY, 0.6)' in text_src,
          "`_TINT_DIM` 是 `_tint_from` 算出来的, 不是拍脑袋的常数")
    _sce = _fn_body(ui_base_src, "_set_controls_enabled")
    check("_set_lbl_tint(_lbl, _TINT_BRIGHT)" in _sce and "_set_lbl_tint(_lbl, _TINT_DIM[_lbl._tint_key])" in _sce,
          "`_set_controls_enabled` 两态都改走染色")
    check(".color = _lbl._tint_base_rgba" in _sce and "hex_rgb(COL_GRAY) + (0.6,)" in _sce,
          "⚠️ 拿不到画布 Color 时**退回写 `.color`** —— 绝不静默不染色")
    check("_TINT_BRIGHT" in text_src
          and text_src.find("_TINT_BRIGHT = (1.0") > text_src.find("def _tint_from("),
          "`_TINT_BRIGHT`/`_TINT_DIM` **排在 `_tint_from` 之后**(它是模块级调用, 放前面会 NameError)")
    # ---- 状态栏带数字那两句的枚举预热(2026-09-14) ----
    check("命中 x%d · 结算中" in _bw and "中奖! +%d (x%d)" in _bw,
          "预热表里列了状态栏带数字的那两句(`命中 x%d · 结算中` / `中奖! +%d (x%d)`)")
    check("VALUE_SHAPE.values()" in _bw and "PRESETS" in _bw,
          "⚠️ 倍率与投注档是**从代码里派生**的(VALUE_SHAPE / PRESETS), 不是手抄一份")
    check("_TEXTEX_PER_LABEL = 64" in text_src,
          "`_TEXTEX_PER_LABEL` 调大到 64(状态栏要存 10 固定 + 35 带数字 = 45 项; "
          "留在 12 会自己把自己挤掉, 且不报错)")
    check("文字纹理缓存: 命中" in bench_src and "texex_hit" in bench_src,
          "日志头部会印缓存命中率(不然没法判断这层到底有没有生效)")
    # ---- 跑分 = 放录像(2026-09-14, 玩家提的) ----
    # 为什么钉: 每轮球的落格是随机的 ⇒ 中奖次数不同 ⇒ 装杯时长不同 ⇒ **内容配比每轮都不一样**。
    # 实测连续三轮装杯占比 25.2% / 30.6% / 38.2%, 而 1%Low 是 92.4 / 92.2 / 88.4 ——
    # 差的 3.9 全来自配比, 不是代码变差。追了三轮噪声。
    check("BENCH_SEED" in cfg_src and "BENCH_BOARD" in cfg_src,
          "有跑分专用的固定种子与固定盘面")
    check("BENCH_BOARD = (5, 10, 20, 50, 100)" in cfg_src,
          "盘面是 5/10/20/50/100(相邻约 2 倍, 且 100 保证最重那档每轮都被量到)")
    _sb = _fn_body(bench_src, "_start_bench_test")
    check("random.getstate()" in _sb and "random.seed(BENCH_SEED)" in _sb,
          "开跑时存下随机状态并种入固定种子")
    check("random.setstate(" in bench_src and "_bench_rng_state" in bench_src,
          "跑完把随机状态**原样还回去**(不还的话正常游戏每局球路一样, 比跑分不可比严重得多)")
    _lb = _fn_body(game_src, "launch")
    check("launch_ball(frozen_power, rng=_brng)" in _lb and "random.Random(BENCH_SEED + 1000" in _lb,
          "跑分时给球一条**按球号派生的碰撞随机流**(撞钉/墙/隔板/弧面全走 `b._rng`)")
    check("BENCH_SEED + 1000 + int(self._bench_ball_i)" in _lb,
          "碰撞流的种子按**球号**派生(不是每发同一个, 否则 5 发走同一条轨迹)")
    _pb = _fn_body(game_src, "park_ball")
    check("if reroll and not self._bench_running:" in _pb,
          "跑分期间**不重掷盘面**(否则会把 `_auto_launch_tick` 钉好的覆盖掉)")
    check("random.Random(BENCH_SEED + 2000 + int(multiplier))" in winfx_src,
          "装杯内部的下落时序也按倍率派生(球路钉死后 装杯 571/545 还差 26 帧, 就是它)")
    check("if auto_close:" in winfx_src,
          "⚠️ 装杯那条**只对跑分生效**(`auto_close` 是跑分的标志) —— 正常游戏的杯子必须随机")
    # ---- 历史面板口径(2026-09-14, 玩家要的) ----
    # ⚠️ 2026-09-15: 表头文案现在住在 `_HIST_COLS` 这个常量里(表头与数据行共用), 用单引号。
    check("'中位跑分 / 波动'" in bench_src,
          "历史面板表头是「中位跑分 / 波动」(那一格是 `_phys_sorted[len//2]`, **中位数**不是均值)")
    check("'平均/1%Low帧'" in bench_src,
          "表头是「平均/1%Low帧」")
    # ⚠️⚠️ 2026-09-15 **玩家把「CPU平均频率」这一列删掉了**。它已被实测证伪: 高通 LMH 平台上
    #    `scaling_cur_freq` 报的是**调频器的目标值**, 实际时钟被硬件按下去不回写。
    #    ⇒ 这条门禁**反过来钉**: 表头里**不许**再出现它。
    check("'CPU平均频率'" not in bench_src and "_HIST_COLS" in bench_src,
          "「CPU平均频率」那一列已删(它已被实测证伪), 且表头仍走 `_HIST_COLS`")
    # ⚠️⚠️ 2026-09-15 **第五次定稿**: 玩家「去掉归一化列」—— 面板只剩三列。
    #    **反过来钉**: 面板列里**不许**再出现「归一化」(但记录 JSON 里仍留着 `phys_norm`)。
    check("归一化" not in bench_src.split("_HIST_COLS = ")[1].split("\n")[0]
          and "'中位跑分 / 波动')" in bench_src,
          "面板只剩三列、以「中位跑分 / 波动」收尾(`归一化` 只留在记录 JSON 里)")
    # ⚠️ 屏幕上印的是 `15422/1.9%`(`'%d/%.1f%%'`), 1.9 那个数**已经带 %** —— 所以 ×100
    #    不能删, 删了这条说明就和屏幕对不上了。
    check("物理引擎每秒模拟步数的中位数" in bench_src,
          "面板底部留着「中位跑分」的口径(它是**物理步数**的中位数, 不是渲染帧数)")
    check("(最大跑分-最小跑分)/中位数跑分" in bench_src,
          "「波动」写清了算式(玩家 2026-09-15 定稿的写法)"
          "(⚠️ 别看它没写 ×100 就以为和代码对不上: main.py:9600 是 `100.0 * (max-min)/fps`, "
          "屏幕上印的是 `1.9%` —— 那个 %% 就是 ×100 的结果, 只是落在单位里)")
    # ⚠️ 2026-09-15 玩家: 「大家都知道什么是平均帧和1%low帧就不用你教学了」+「难点是中位跑分和
    #    波动是什么」⇒ 那两条**常识不解释**, 只留这个面板特有的两条。门禁跟着改, 并加一条**反向**的。
    # ⚠️ 这里**只能用裸字符串**, 不能用 `_code_only`。
    # ⚠️ 必须**把源码里的字符串拼回来**, 不能直接对源码切一段 —— 切出来的是带引号/带 `\n`
    #    的**源码行**, 量宽度量的是源码不是文字。
    _i = bench_src.find("text=('  中位跑分：")
    _j = bench_src.find("'),", _i)
    # ⚠️ `_j + 1` 不能省: `'),` 的**那个引号是最后一行字符串的收尾引号**。
    _blk = bench_src[_i:_j + 1] if (_i >= 0 and _j > _i) else ""
    _foot = "".join(
        _ln[_ln.find("'") + 1:_ln.rfind("'")]
        for _ln in _blk.splitlines() if _ln.count("'") >= 2
    ).replace("\\n", "\n")
    check(bool(_foot) and "1%Low帧" not in _foot,
          "底部口径**不解释**平均帧/1%Low帧(常识, 占了小半屏还折行 —— 玩家点名不要)")
    # ⚠️ 2026-09-15 玩家: 「这个排版废话很多啊, 你看看每个项目能不能 1 行说完」。
    #    这条门禁**真的去量宽度**, 而且按**最窄的手机**(400px 窗口, 正文可用宽 288px)算。
    # ⚠️ 必须排在 `_foot` 定义**之后**。
    try:
        _sp12 = sp(12)
        _avail400 = 0.86 * 400.0 - 24 - 32
        _items = [l for l in _foot.splitlines()
                  if l.strip() and not l.strip().startswith("口径")]
        _wide = [(l.strip(), round(TX.text_px(l, _sp12))) for l in _items
                 if TX.text_px(l, _sp12) > _avail400]
        check(len(_items) == 2 and not _wide,
              "底部口径**每项一行**(最窄的手机上也放得下: 可用 %.0f px)" % _avail400,
              "过宽的项: %s" % (_wide,) if _wide else "")
    except Exception as _e:
        check(False, "口径每项一行的宽度门禁跑不起来", repr(_e))
    # ---- 日志头部: 这一轮能不能和上一轮比 ----
    check("低于「中位帧率 55%%」" in bench_src and "内容配比" in bench_src,
          "日志头部印「低于中位帧率 55% 的帧数」+「内容配比」—— 判断这一轮和上一轮能不能比"
          "(门槛是**相对中位帧**的; 原来写死 90fps, 在 1%Low 贴到 89.7 时反而报'变差', 已改)")
    check("参考带「中位帧率 55%%~75%%」" in bench_src,
          "55%~75% 的参考带**单独一行**印出(玩家定稿: 只作参考, 不混进硬指标)")
    # ---- 字号预热扩到 HUD 基准 ----
    check("_FONT_WARM_HUD" in winfx_src and "_fit_base" in _fn_body(winfx_src, "prebake_step"),
          "字号预热扩到 HUD 基准, 且基准值**从标签自己的 `_fit_base` 读**(不手抄 sp(N))")
    check("Clock.schedule_once(self.prebake_step, 0.02)" in _fn_body(winfx_src, "prebake_step"),
          "字号预热那一步用 0.02 秒(真机实测冷开一次约 1.4ms, 0.05 白白多花 3.6 秒启动)")
    check("**本版关闭**" in bench_src,
          "缓存若被关掉, 日志**明说「本版关闭」**而不是印成 0% 命中率冒充失败")

    # ---- 落袋那一拍不许重建文字纹理(2026-09-14, v0.7.26) ----
    # 真机日志里 10 个低于 90fps 的帧有 7 个落在"落袋那一拍"上, 而 `settle()` 里就有一句
    # 裸的 `self._refresh_stats()` —— 写文字 = 重排一次字形纹理(真机 4~7 毫秒)。
    # 判据是**它不在落袋当帧写、而是延后**: 既要看到 `schedule_once`, 也要看不到裸调用。
    _st = _fn_body(game_src, "settle")
    # 锚点重映射: 延后走的是 `Game._schedule(..., STATS_REFRESH_DELAY)`(新版无头状态机
    # 不直接持 Clock)。判据("落袋当帧不写文字、延后 0.20 秒")没变。
    check("self._schedule(self._refresh_stats, STATS_REFRESH_DELAY)" in _st,
          "`settle()` 里的统计刷新**延后**了, 不在落袋当帧重建文字纹理")
    check("\n        self._refresh_stats()\n" not in _st,
          "阴性对照: 裸的 `self._refresh_stats()` 已经不在 `settle()` 里了(留着就等于没挪)")
    # ⚠️ 挪的秒数**不能是 0**: 下一帧仍在落袋窗口里, 白挪。
    check("STATS_REFRESH_DELAY" in _st and GAME.STATS_REFRESH_DELAY == 0.20
          and "_refresh_stats(), 0)" not in _st,
          "延后用的是 0.20 秒而不是 0(0 = 下一帧, 仍在落袋窗口里)")
    check(0.0 < CFG.HUD_ALPHA <= 1.0 and 0.0 < CFG.DIM_ALPHA <= 1.0,
          "两个压暗强度都在 (0,1]",
          "HUD=%.2f DIM=%.2f" % (CFG.HUD_ALPHA, CFG.DIM_ALPHA))

    # 接线: **整块界面减去游戏区**必须被两块矩形恰好盖满, 一块不漏、也不许压到游戏区。
    # ⚠️ 不用"数 `_row_dim(` 出现几次"那种文本断言 —— 它测不出"该盖的地方盖全了没有"。
    # ⚠️ 挂具必须把 `_relayout_hud_dim` / `_sync_hud_dim` / `_paint_hud_dim` 三个都绑上,
    #    `_build_hud_dim` 一进去就要用到(`_paint_hud_dim` 是 2026-09-14 拆出来的新方法)。
    class _FakeGA(object):
        def __init__(self, y, h):
            self.y, self.height = y, h
        def bind(self, **kw):
            pass

    def _dim_geom(x, width, height, ga_y, ga_h, vp=None, vp_pos=(0.0, 0.0)):
        # 挂一个真的 AnchorLayout 当父容器 —— 那份矩形就是"等效视口"。
        vpbox = AnchorLayout(size_hint=(None, None))
        vpbox.pos = vp_pos
        vpbox.size = vp if vp else (width, height)
        holder = BoxLayout(size_hint=(None, None))
        holder.pos = (x, 0.0)
        holder.size = (width, height)
        holder.game_area = _FakeGA(ga_y, ga_h)
        vpbox.add_widget(holder)
        holder._relayout_hud_dim = UIB.UiMixin._relayout_hud_dim.__get__(holder)
        holder._sync_hud_dim = UIB.UiMixin._sync_hud_dim.__get__(holder)
        holder._paint_hud_dim = UIB.UiMixin._paint_hud_dim.__get__(holder)
        UIB.UiMixin._build_hud_dim(holder)
        return vpbox, holder

    _vp1, h1 = _dim_geom(0.0, 540.0, 960.0, 122.0, 676.0)
    tp, ts = tuple(h1._hud_dim_top.pos), tuple(h1._hud_dim_top.size)
    bp, bs = tuple(h1._hud_dim_bot.pos), tuple(h1._hud_dim_bot.size)
    # 上块 = [游戏区上沿, 界面顶]; 下块 = [界面底, 游戏区下沿]
    check(tp[1] >= 122.0 + 676.0 and bp[1] + bs[1] <= 122.0 + 1e-9,
          "两块压暗矩形都不与游戏区重叠(重叠 = 压暗叠成 0.90, 大字也会被压)",
          "top.y=%.1f  bottom=%.1f  游戏区=[122.0, 798.0]" % (tp[1], bp[1] + bs[1]))
    covered = ts[1] + bs[1]
    check(abs(covered - (960.0 - 676.0)) < 1e-9,
          "界面里除了游戏区**一块不漏**(含五行之间的间距与底部留白)",
          "盖住 %.0f / 应为 %.0f" % (covered, 960.0 - 676.0))
    check(abs((tp[1] - (122.0 + 676.0)) + bs[1] - 122.0) < 1e-9,
          "两块严丝合缝相接(中间不留缝, 也不重叠)",
          "上块下沿 %.1f == 游戏区上沿 %.1f" % (tp[1], 122.0 + 676.0))

    # 宽窗口: RootWidget 比视口窄(两侧留深色边)。压暗必须铺满**视口**。
    vp2, h2 = _dim_geom(52.0, 596.0, 1100.0, 120.0, 800.0, vp=(700.0, 1100.0))
    w2 = tuple(h2._hud_dim_top.size)[0]
    check(abs(w2 - 700.0) < 1e-9 and abs(h2._hud_dim_top.pos[0]) < 1e-9,
          "宽窗口下压暗铺满**视口**而不是 RootWidget(否则两侧留白原样亮着)",
          "宽 %.0f 起点 %.0f (视口 700, 起点 0)" % (w2, h2._hud_dim_top.pos[0]))
    # 游戏区**下方**那两行: 横屏时 game_area.y 可能是负的, 老写法 max(0.0, ga.y) 会算成 0
    _vp3, h3 = _dim_geom(256.4, 887.3, 1400.0, -78.0, 1116.0,
                         vp=(1000.0, 1400.0), vp_pos=(200.0, -200.0))
    check(tuple(h3._hud_dim_bot.size)[1] > 1.0
          and abs(tuple(h3._hud_dim_bot.size)[1] - 122.0) < 1e-9,
          "游戏区下方那两块(信息行/底行)在 game_area.y<0 时也要盖上(老写法算成高度 0)",
          "下块高 %.1f" % tuple(h3._hud_dim_bot.size)[1])

    vp2.size = (700.0, 1200.0)
    h2.size = (596.0, 1200.0)
    UIB.UiMixin._relayout_hud_dim(h2)
    check(abs(tuple(h2._hud_dim_top.size)[1] - (1200.0 - 920.0)) < 1e-9,
          "视口尺寸一变, 压暗矩形跟着重算(bind 生效 —— 横竖屏切换/resize 全靠它)",
          "上块高 %.1f / 应为 %.1f" % (tuple(h2._hud_dim_top.size)[1], 1200.0 - 920.0))

    # 真夹具: 确认两块 Color 真的挂在 canvas.after 上(挂在 canvas 上就画在子控件**下面**, 等于没盖)
    in_after = all(any(c is col for c in h1.canvas.after.children) for col in h1._hud_dim_cols)
    check(in_after and len(h1._hud_dim_cols) == 2,
          "两块 Color 都在 canvas.after 里(挂在 canvas 上会被五行盖住, 等于没压)",
          "cols=%d" % len(h1._hud_dim_cols))

    class _FakeWinFx(object):
        def dim_alpha(self, peak=CFG.DIM_ALPHA):
            return peak

    h1.game_area.win_fx = _FakeWinFx()
    UIB.UiMixin._sync_hud_dim(h1)
    col = h1._hud_dim_cols[0]
    check(abs(col.rgba[3] - CFG.HUD_ALPHA) < 1e-9
          and tuple(round(v, 4) for v in col.rgba[:3]) == tuple(round(v, 4) for v in CFG.DIM_RGB),
          "_sync_hud_dim 写成 DIM_RGB + HUD_ALPHA(与板面同一个色、同一个强度)",
          "rgba=%s" % (tuple(round(v, 3) for v in col.rgba),))

    # dim_alpha(): HUD 与板面**共用同一个数** —— 它读的是 `_redraw` 本帧写入的 `_a_dim_now`。
    fxd = WinPileFX.__new__(WinPileFX)
    WinPileFX.__init__(fxd, None)
    fxd.pos = (0.0, 0.0)
    fxd.size = (400.0, 520.0)
    fxd._balls = []
    fxd.mode = "idle"
    # ⚠️ `_settled_at` 必须给一个**刚过去**的时刻。给 0.0 的话退场曲线算出天文数字 ->
    #    clamp 成 u=1 -> a_dim 正好也是 0, 于是"漏判 idle"这个 bug 会被这条门禁放过去。
    fxd._settled_at = time.time()
    check(fxd.dim_alpha() == 0.0 and fxd.dim_alpha(CFG.HUD_ALPHA) == 0.0,
          "idle 时 dim_alpha() 归零(_settled_at 是上一局残值, 不判 idle 会算成永远全黑)")
    fxd.mode = "win"
    fxd._redraw()                       # 板面画一帧 -> 写入本帧的 _a_dim_now
    check(abs(fxd.dim_alpha(CFG.DIM_ALPHA) - CFG.DIM_ALPHA) < 1e-9
          and abs(fxd.dim_alpha(CFG.HUD_ALPHA) - CFG.HUD_ALPHA) < 1e-9,
          "板面画过一帧之后, dim_alpha(peak) == peak(HUD 与板面同值、只有峰值不同)")
    # 退场结束那一帧: mode 已切 idle, _redraw 走早退 -> 同一帧 HUD 必须已经是 0。
    fxd.mode = "idle"
    fxd._redraw()
    check(fxd.dim_alpha(CFG.HUD_ALPHA) == 0.0 and fxd._a_dim_now == 0.0,
          "演出结束那一帧 _redraw 已把 a_dim 归零(HUD 不会比板面多黑一帧)",
          "a_dim=%.3f" % fxd._a_dim_now)

    # 绘制次序: 后层玻璃 < 压暗 < 弹珠 < 前层玻璃。测的是**真 _redraw 产出的指令表**。
    # ⚠️ 夹具里**必须放球**(专家用变异体打出来的盲区): 只比对 back/dim/front 三个下标时,
    #    把压暗挪到"已落定球之后"照样全绿。
    fxo = WinPileFX.__new__(WinPileFX)
    WinPileFX.__init__(fxo, None)
    fxo.pos = (0.0, 0.0)
    fxo.size = (400.0, 520.0)
    fxo.mode = "win"                       # a_dim = a_cup = 1, 两个分支都会跑
    fxo._balls = [
        # _ball_screen/_draw_bead 对已落定球只用这几个字段
        {"settled": True, "r_d": 20.0, "sxf": pile.CX, "syf": pile.FLOOR_Y,
         "ring_angle": 0.0, "shade": 1.0, "value": 10},
        {"settled": True, "r_d": 18.0, "sxf": pile.CX + 30.0, "syf": pile.FLOOR_Y - 10.0,
         "ring_angle": 0.0, "shade": 1.0, "value": 10},
    ]
    fxo._redraw()
    back_tex, front_tex, _fb = WFX._glass_textures()
    ball_tex = WFX._ball_texture(10)
    i_back = i_dim = i_front = None
    i_balls = []
    for i, ins in enumerate(fxo.canvas.children):
        if not isinstance(ins, Rectangle):
            continue
        t = getattr(ins, "texture", None)
        if t is not None and t is back_tex and i_back is None:
            i_back = i
        elif t is not None and t is front_tex and i_front is None:
            i_front = i
        elif t is not None and t is ball_tex:
            i_balls.append(i)
        # 压暗矩形 = 唯一一块**盖满整个覆盖层**的矩形。别用 `texture is None` 去认:
        # Kivy 的 Rectangle.texture 在没给贴图时返回默认白贴图, 拿到的是个 Texture 不是 None。
        elif tuple(ins.size) == tuple(fxo.size) and i_dim is None:
            i_dim = i
    check(None not in (i_back, i_dim, i_front) and i_back < i_dim < i_front,
          "绘制次序 后层玻璃 < 压暗 < 前层玻璃(压暗画在后层玻璃之前 = 被玻璃提亮回去)",
          "back=%s dim=%s front=%s" % (i_back, i_dim, i_front))
    check(len(i_balls) == 2 and i_dim is not None and all(i > i_dim for i in i_balls),
          "压暗画在**弹珠之前**(放到球之后 = 球被压暗, 整堆沉进背景里看不见)",
          "dim=%s 球=%s" % (i_dim, i_balls))

    # 收尾必归零: 拿受控时钟把几十局真 `tick()` 跑完, 每局结束 dim_alpha 都必须回到 0。
    # HUD 卡在暗色是**永久性观感损坏**(整屏一直灰着), 而它只在真机上肉眼可见。
    class _FT(object):
        t = 2.0e6

        def time(self):
            return self.t

    _real2 = WFX.time
    _f2 = _FT()
    WFX.time = _f2
    stuck = 0
    try:
        for n in (2, 5, 20, 100):
            for seed in range(6):
                fxs = WinPileFX.__new__(WinPileFX)
                WinPileFX.__init__(fxs, None)
                fxs._redraw = lambda *a, **k: None
                fxs._rng = random.Random(seed + 7)
                _f2.t = 2.0e6 + seed * 0.011        # 错开帧相位
                if not fxs.play_win(n, 10, on_done=lambda: None):
                    continue
                # 退场改成"等玩家点击"之后必须模拟点击; 上界要盖住整场装杯 + 点击等待 + 淡出。
                for _ in range(int((6.0 + WFX.HOLD_BASE + WFX.RESULT_FADE) * 60)):
                    if (fxs.mode == "result" and not fxs._closing_at
                            and _f2.t >= fxs._settled_at + WFX.HOLD_BASE):
                        fxs.request_close()        # 模拟玩家点了一下
                    fxs.tick()
                    _f2.t += 1.0 / 60.0
                    if fxs.mode == "idle":
                        break
                if fxs.dim_alpha(CFG.HUD_ALPHA) != 0.0:
                    stuck += 1
    finally:
        WFX.time = _real2
    check(stuck == 0, "每局演完 dim_alpha 必归零(卡住 = HUD 永久停在暗的)",
          "卡住 %d 局" % stuck)

    print("\n[12] 落珠震动 = 落地音(同一次节流判定)")

    class _FakeSfx(object):
        """复刻 `Sfx.play` 的节流语义(按音效名记 last), 但用受控时钟。"""

        def __init__(self, clock):
            self._c = clock
            self.hits = []
            self._last = {}

        def play(self, name, gain=1.0, throttle=0.0):
            if throttle > 0.0 and self._c.t - self._last.get(name, -1e9) < throttle:
                return False
            self._last[name] = self._c.t
            self.hits.append(self._c.t)
            return True

    class _FakeGame(object):
        def __init__(self, sfx):
            self.sfx = sfx

    class _FakeArea(object):
        def __init__(self, sfx):
            self.game = _FakeGame(sfx)

    class _FT3(object):
        t = 3.0e6

        def time(self):
            return self.t

    _real3 = WFX.time
    _real_vib = WFX._vibrate_tick
    _f3 = _FT3()
    WFX.time = _f3
    vibs = []
    WFX._vibrate_tick = lambda gain: vibs.append(_f3.t)
    mismatch = 0
    tooclose = 0
    rounds = 0
    total_vib = 0
    try:
        for n in (2, 5, 20, 100):
            for seed in range(4):
                fxv = WinPileFX.__new__(WinPileFX)
                sfx = _FakeSfx(_f3)
                WinPileFX.__init__(fxv, _FakeArea(sfx))
                fxv._redraw = lambda *a, **k: None
                fxv._rng = random.Random(seed + 11)
                _f3.t = 3.0e6 + seed * 0.017
                vibs[:] = []
                if not fxv.play_win(n, 10, on_done=lambda: None):
                    continue
                rounds += 1
                for _ in range(int((WFX.FX_MAX_SEC + 3.0) * 60)):
                    fxv.tick()
                    _f3.t += 1.0 / 60.0
                    if fxv.mode == "idle":
                        break
                if vibs != sfx.hits:            # 逐次比对时刻: 响一次必须震一次, 且同时
                    mismatch += 1
                total_vib += len(vibs)
                for a, b in zip(vibs, vibs[1:]):
                    if b - a < WFX.BOUNCE_THROTTLE - 1e-9:
                        tooclose += 1
    finally:
        WFX.time = _real3
        WFX._vibrate_tick = _real_vib
    check(rounds > 0 and mismatch == 0 and total_vib > 0,
          "每一次落地音都对应恰好一次震动, 且时刻逐个相同(不自己另判一次节流)",
          "不一致 %d/%d 局, 共震 %d 次" % (mismatch, rounds, total_vib))
    check(tooclose == 0,
          "两次震动间隔不小于 BOUNCE_THROTTLE(否则会糊成持续嗡嗡)",
          "过密 %d 次" % tooclose)

    # ---- 13. 隐藏弹窗的"版本 / 构建日期"行 ----
    # 这一行取的是**出货文件**的 mtime(打包时 p4a 塞进 private.tar 的时间戳),
    # 不是烘进源码的常量 —— 生成器是纯字符串拼接, 烘日期进去会让 --check 每次都报不同步。
    # 门禁只钉两件事: 不抛异常、格式对(拿不到就返回空串, 弹窗少一行, 绝不能带崩)。
    print("\n[13] 隐藏弹窗的版本/构建日期行(_build_info)")
    _bh = RootWidget.__new__(RootWidget)
    info = RootWidget._build_info(_bh)
    check(isinstance(info, str) and (" 制作" in info or info == ""),
          "_build_info() 不抛异常(拿不到就返回空串)", repr(info))
    check(info == "" or re.match(r"^于 \d{4}年\d{2}月\d{2}日 \d{2}:\d{2} 制作$", info) is not None,
          "制作时刻格式 = 于 YYYY年MM月DD日 HH:MM 制作(年月日写汉字, 全数字容易读反)", repr(info))
    # ⚠️ 玩家 2026-09-11 定稿: 版本号**挪进弹窗标题**, 并且「去掉其他地方的版本号」。
    check("v0" not in info and " · " not in info,
          "版本号**不再出现在正文行里**(玩家: 「去掉其他地方的版本号」) —— 正文只剩制作时刻",
          repr(info))
    # ⚠️⚠️ 路径重映射(**与上面「底色三处同值」同一个根**, 2026-09-19 修): 老版探针是先
    #    `os.chdir(ROOT/android)` 再 `import main` ⇒ `_app_version()` 桌面分支那句
    #    `dirname(abspath(__file__))` 求出来就是 `android/`, 而 `buildozer.spec` 正躺在旁边。
    #    新版不 chdir, `app_root()` 取的是主脚本目录(本文件的 `DANZHU_APP_ROOT=ROOT`), 而
    #    `new_danzhu/` 下**还没有 buildozer.spec**(README「还没做的」: 用户定的是"先只保证
    #    代码跑通") ⇒ 不指过来的话 `_app_version()` 返回空串、`_startup_title()` 退化成纯游戏名,
    #    报的是**工程缺件**, 不是标题判据错了(实测指过来之后两个值逐字相同)。
    #    ⇒ 临时把 app 根指到 `PACK_ROOT`, 判据**一个字不改**: 标题仍必须是「跳跳的弹珠机 v0.x.x」。
    _ar_keep = os.environ.get("DANZHU_APP_ROOT")
    os.environ["DANZHU_APP_ROOT"] = PACK_ROOT
    try:
        _st = WID._startup_title()
    finally:
        if _ar_keep is None:
            os.environ.pop("DANZHU_APP_ROOT", None)
        else:
            os.environ["DANZHU_APP_ROOT"] = _ar_keep
    check(_st.startswith("跳跳的弹珠机") and re.match(r"^跳跳的弹珠机 v\d", _st) is not None,
          "弹窗标题 = **跳跳的弹珠机 v0.x.x**(游戏名与版本号之间**有一个空格** —— 玩家 2026-09-11 "
          "「加一个空格」); 版本号全工程只在这里出现一次", repr(_st))
    # 24 小时制**功能性**断言: 拿两个会暴露 12/24 制差异的时刻直接格式化, 而不是去 grep 源码。
    _mid = time.struct_time((2026, 9, 11, 23, 5, 0, 0, 0, 0))
    _midn = time.struct_time((2026, 9, 11, 0, 5, 0, 0, 0, 0))
    check(time.strftime(DEV.BUILD_TIME_FMT, _mid) == "2026年09月11日 23:05"
          and time.strftime(DEV.BUILD_TIME_FMT, _midn) == "2026年09月11日 00:05",
          "时刻是 24 小时制(%H, 不是 %I): 23:05 要显示 23:05, 午夜要显示 00:05",
          "23:05 -> %s / 00:05 -> %s" % (time.strftime(DEV.BUILD_TIME_FMT, _mid),
                                         time.strftime(DEV.BUILD_TIME_FMT, _midn)))

    # ---- 13b. 「游戏信息」面板的两条实时行(温度/功率 + **CPU 频率**) ----
    # ⚠️ **CPU 频率那一块是新版独有的, 老版完全没有** —— 见 `changelog/2026-09-19.md` 第 23 条。
    #    ⇒ 这一组**不与老版 FAIL 集合对账**(老版没有可比的东西), 它钉的是**新版自己**的纪律。
    # ⚠️ 判据分两层: **运行期**(真的调一次, 看降级与排版) + **静态**(去注释后 grep, 防注释喂绿)。
    _keep_g = BACKEND._cpu_groups
    _keep_r = BC._read_int_file
    _keep_a = getattr(os, "sched_getaffinity", None)
    # 一台典型的 1+3+4(降序: 最高频簇在前, 与 `_cpu_groups` 的 `sorted(reverse=True)` 一致)
    _FAKE_GRP = [(3187000, [7]), (2745000, [4, 5, 6]), (2016000, [0, 1, 2, 3])]
    try:
        BACKEND._cpu_groups = lambda: []
        check(BC._live_cpu_freq_line() == "",
              "阴性对照: 没有 cpufreq(PC / 权限 / 核离线) ⇒ `_live_cpu_freq_line()` 返回**空串** —— "
              "调用方据此整块不出现, **绝不印一排 0 假装量到了**")
        BACKEND._cpu_groups = lambda: _FAKE_GRP
        # ⚠️ 路径里 `cpu` 出现**三次**(`/cpu/`、`cpuN`、`cpufreq`) ⇒ 只能正则取核号, 不能 split。
        BC._read_int_file = lambda _p: {7: 1804000, 4: 2400000, 0: 2800000}.get(
            int(re.search(r"/cpu(\d+)/", _p).group(1)))
        os.sched_getaffinity = lambda _m: set((4, 5, 6, 7))
        _got = BC._live_cpu_freq_line()
        check("1+3+4" in _got and "核 4-6" in _got and "核 0-3" in _got,
              "有 cpufreq ⇒ 印**簇结构**(1+3+4) + 每簇**核号范围**与当前频率(核号不逐个列, 用范围)",
              repr(_got.split("\n")[0]) if _got else repr(_got))
        # ⚠️ 判据必须**先去 markup**: 输出是 `8[/color] 核`, 裸文本 `"8 核"` 根本不在里面 ——
        #    我第一版就那么写的, 当场判红。(这正是"写完先跑一遍看它是否真通过"的用处:
        #    写「期望通过」的判据而不先验, 等于给自己埋一条永远红的闸。)
        _plain = re.sub(r"\[/?color[^\]]*\]", "", _got)
        _n_want = sum(len(_v) for _k, _v in _FAKE_GRP)
        check(("%d 核" % _n_want) in _plain,
              "核数(%d)取自**分组里数出来的**那个, 不是 `os.cpu_count()` —— "
              "两者不等时(核 offline)会和 1+3+4 那个 shape 自相矛盾" % _n_want,
              repr(_plain.split("\n")[0]))
        check("可跑核" in _got and "4-7" in _got and "可跑核" in _got.split("\n")[0],
              "锁核状态按 `os.sched_getaffinity` 印**可跑核**(per-thread; 文案不许写成「已锁核」), "
              "且**并进第一行**(玩家 2026-09-19 定:「放在 CPU:X核 x+Y 后面, 多个空格即可」—— "
              "它和「几个核」是同一件事的两面, 单独占一行白吃一块高度)",
              repr(_got.split("\n")[0]) if _got else repr(_got))
        # ---- 玩家 2026-09-19 的第二轮两条要求(背后是同一条原则: 金色只给会变的) ----
        # ① `可跑核` 的值**不许可金色** —— 它在一局里不会变(锁核只发生在跑分/高压测试期间)。
        #    ⚠️ 判据用**带 markup 的原文**: 若还套着 `[color=…]`, 原文里就没有连续的
        #       「可跑核 4-7」这三个字符 —— 这比去数 `[color=` 出现几次更准, 也更短。
        check("可跑核 4-7" in _got,
              "「可跑核」的值**不着色**(玩家:「可跑核不是变量, 改为通用颜色」)—— 本块的金色留给"
              "**会变的那个数**; 还套着 markup 的话原文里不会有连续的「可跑核 4-7」",
              repr(_got.split("\n")[0]) if _got else repr(_got))
        # ② 频率格式 = **当前/上限**, 用全进程**实测值**钉(夹具给的是 核7: 当前1804000 / 上限3187000)。
        #    ⚠️ 单位必须是 `M`, **不是** `Mhz` —— 玩家第一遍笔误成 `Mhz`、第二遍更正为 `M`,
        #       这条就是防止以后有人"顺手把单位补全"。(`M` 不在 markup 里, 所以判 `_plain`。)
        check("1804M/3187M" in _plain and "2400M/2745M" in _plain,
              "频率 = **当前/上限**(玩家:「格式改为 xxxM/yyyM, 那个 yy 肯定是上限」)—— "
              "上限是 `cpuinfo_max_freq`(常量, 不上金色), 当前是 `scaling_cur_freq`(活变量, 上金色)",
              " ".join(_plain.split("\n")[1:]))
        check("Mhz" not in _got and "MHz" not in _got,
              "频率单位保持 **`M`**(`4608M` 那种), **不许**自作主张补成 `Mhz`/`MHz` —— "
              "玩家第一遍笔误、第二遍明确更正为 `M`",
              " ".join(_plain.split("\n")[1:]))
        BC._read_int_file = lambda _p: None
        check(BC._live_cpu_freq_line() == "",
              "阴性对照: 簇结构读到了、但**当前频率一个都读不到** ⇒ 仍返回空串(不印半张表充数)")
    finally:
        BACKEND._cpu_groups = _keep_g
        BC._read_int_file = _keep_r
        if _keep_a is None:
            try:
                del os.sched_getaffinity          # Windows 上本来就没有, 别留个假的在那
            except Exception:
                pass
        else:
            os.sched_getaffinity = _keep_a
    _bc = _code_only(bench_src)
    check("Clock.schedule_interval(_tick_live, 0.5)" in _bc,
          "两条实时行**共用同一个** 0.5 秒 tick(不是各开一个 Clock 事件)")
    check("_live.text = _t2" in _bc and "_live_cpu.text = _c2" in _bc,
          "同一个 tick 里**两行都刷** —— 加了行却忘了刷它 = 那一行是死的, 玩家会当它坏了")
    check("popup.bind(on_dismiss=_stop_live)" in _bc and "Clock.unschedule(_live_ev)" in _bc
          and "_live is not None or _live_cpu is not None" in _bc,
          "⚠️ 关窗必须 `unschedule`, 且判据是**两条行任一存在**就挂上 —— 写死 "
          "`if _live is not None:` 的话, PC 上没有温度行 ⇒ 定时器**永远不摘**(每开一次面板攒一个)")
    # ⚠️⚠️ 判据**不许**写成 `"_n_extra += 1" in _bc` —— 那样是**空转的**, 阴性对照当场抓到:
    #     把**第一处**改成 `_n_extra = 1`(第二处仍是 `+= 1`), 裸 `in` 照样为真 ⇒ 照样绿。
    #     现在钉「初始化 `= 0`」+「累加**至少两处**」+「**一处裸赋值都不许有**」 ——
    #     加第三行实时行也不用改判据, 而任何一处写死 `= 1` 都会红。
    check("_n_extra = 0" in _bc and _bc.count("_n_extra += 1") >= 2
          and "_n_extra = 1" not in _bc,
          "实时行的高度预算: 初始化 `= 0` 之后**只准 `+= 1` 累加** —— "
          "写死 `= 1` 时加第二行不会撑高弹窗, 尾巴被裁")

    # ---- 13c. 帧率默认档 = 「跟随面板最高档」, 且两处真源不许分裂(玩家 2026-09-20 定) ----
    # ⚠️ **新版独有的一条纪律**(老版的默认档是 120), 所以**不与老版 FAIL 集合对账**。
    # 为什么要单独立一组: 这个常量在 `config.py` 与 `game.py` 里**各有一份**(后者是存档字段
    # 的读/写侧), 只改一处就是"读写两侧不同一份" —— 本工程栽过的形状。
    # 而它的值直接决定**生效刷新率**(`platform/device.py _request_android_high_hz()` 里
    # `target = min(屏幕最高档, Android系统峰值, 用户档位)`), 工程自己的真机账又是
    # `1%Low ≈ 0.70 × 生效刷新率`(120Hz→83.3 / 60Hz→42.2)。**改错了没有任何别的东西会报错。**
    check(CFG.FPS_CAP_DEFAULT == CFG.FPS_CAP_MAX,
          "帧率默认档 == `FPS_CAP_MAX`(=「跟随面板最高档」)—— 用户档位必须设到 ≥ 任何面板, "
          "上面那句 `min(屏幕, 系统, 用户档位)` 才会落到面板真实最高档",
          "FPS_CAP_DEFAULT=%r 而 FPS_CAP_MAX=%r ⇒ 默认档把面板压低了"
          % (CFG.FPS_CAP_DEFAULT, CFG.FPS_CAP_MAX))
    check(GAME.FPS_CAP_DEFAULT == CFG.FPS_CAP_DEFAULT,
          "`game.py` 与 `config.py` 的 `FPS_CAP_DEFAULT` **是同一份**(存档读/写两侧不许分裂)",
          "game.py=%r 而 config.py=%r" % (GAME.FPS_CAP_DEFAULT, CFG.FPS_CAP_DEFAULT))
    check(GAME.FPS_CAP_OPTIONS == CFG.FPS_CAP_OPTIONS,
          "`game.py` 与 `config.py` 的 `FPS_CAP_OPTIONS` **是同一份**(白名单与滑条同源)",
          "game.py=%r 而 config.py=%r" % (GAME.FPS_CAP_OPTIONS, CFG.FPS_CAP_OPTIONS))
    check(CFG.FPS_CAP_DEFAULT in CFG.FPS_CAP_OPTIONS,
          "默认档必须在选项表里 —— `_fps_user_cap()` 与 `set_fps_cap_setting()` 都拿 "
          "`cap in FPS_CAP_OPTIONS` 当白名单, 不在表里的默认值会被**静默丢掉**换回自己",
          "FPS_CAP_DEFAULT=%r 不在 %r 里" % (CFG.FPS_CAP_DEFAULT, CFG.FPS_CAP_OPTIONS))

    # ---- 14. 装杯落珠的音量 ----
    # 玩家 2026-09-11: "弹珠掉落容器的声音, 音量太小了"。同一个 bounce 波形主游戏给到 1.0
    # (不削波), 装杯原来只有 0.25~0.72 —— 这条钉住"两跳都不低于主游戏那个 0.55 的落槽音量"。
    print("\n[14] 装杯落珠的音量")
    _a1_lo = WFX.BOUNCE_A1_BASE * WFX.BOUNCE_GAIN_BOOST
    _a2_lo = WFX.BOUNCE_A2_BASE * WFX.BOUNCE_GAIN_BOOST
    check(_a1_lo >= 0.55 and _a2_lo >= 0.35,
          "装杯落珠的音量不低于主游戏落槽(玩家报过'音量太小'; 主游戏是 clamp(vy/500, 0.3, 1.0))",
          "第一跳最低 %.2f / 第二跳最低 %.2f" % (_a1_lo, _a2_lo))
    check(WFX.BOUNCE_GAIN_HI <= 1.0 and WFX.BOUNCE_GAIN_BOOST * WFX.BOUNCE_A1_BASE <= 1.0 + 1e-9,
          "夹完不超 1.0(Sfx.play 的 lvl 上限就是原始 PCM, 超了等于没加)",
          "上限 %.2f" % (WFX.BOUNCE_GAIN_HI,))

    # ---- 17. 落袋即终态 + 落地必弹 ----
    # 玩家 2026-09-11 第三次报「弹珠落入1个倍率槽之后跑到其他槽位去了 —— 也不是横着走, 而是
    # 弹跳的高度比较高, 而且是斜着的」。根因**不在物理而在出货路径的循环形状**:
    # GUI 的飞行循环是"累加器 + 每轮覆盖 landed", 落袋那一帧只要还有第二个物理步, 那一步返回
    # None 就把 landed 冲成 None ⇒ 整个落袋分支被跳过(横速没清零 / 没切 landing / 没结算)。
    # ⚠️ 这个 bug 能活到今天, 是因为 --selftest / benchmark_trajectories 都是"逐物理步 break",
    #    **永远构造不出"一帧 2 步"**。所以这里不复制循环, 改为断言三件与循环形状无关的事。
    print("\n[17] 落袋即终态 + 落地必弹")

    _geo = geo.build_geo()
    _pin_bad = _down_bad = 0
    _N17 = 200
    for _k in range(_N17):
        _b = PHYS.launch_ball(CFG.MISFIRE_POWER + (1.0 - CFG.MISFIRE_POWER) * (_k / (_N17 - 1.0)),
                              rng=random.Random(20260911 + _k))
        _r0 = None
        for _ in range(4000):
            _r0 = PHYS.advance_flight(_b, _geo)
            if _r0 is not None:
                break
        if _r0 is None:
            _pin_bad += 1
            continue
        if _b.vy <= 0.0:
            _down_bad += 1
        _x0 = _b.x
        for _ in range(30):        # 再推进 30 步(远超"同帧多跑几步")
            _r1 = PHYS.advance_flight(_b, _geo)
            if _r1 is not None and (_r1 != _r0 or abs(_b.x - _x0) > 0.5):
                _pin_bad += 1
                break
    check(_pin_bad == 0,
          "落袋后槽号/球心 x 不再变(落袋=终态)。这是让「结算槽==落格槽」与调用方循环形状"
          "无关的那条不变量; 少了它, 一帧 2 步就换槽",
          "变了 %d/%d" % (_pin_bad, _N17))
    check(_down_bad <= _N17 * 0.05,
          "落袋时球仍在下落(地板矩形不再被当弹性墙撞, 真实下落速度活到落袋那一刻)。"
          "以前 WALL_E=0.5 会在同一子步把球弹成向上, 实测 61% 的落袋 vy<0 —— 既让结算槽"
          "受偶然影响, 又把落地回弹抹掉四分之三",
          "不在下落 %d/%d" % (_down_bad, _N17))

    # 落地必弹(用户 2026-09-11 定稿: "落地的时候必须弹跳下, 真跳和假跳都可以, 高度别太固定")
    _apex_lo = (CFG.LAND_BOUNCE_MIN_VY * CFG.LAND_BOUNCE_JITTER[0] * CFG.LAND_E
                * CFG.LAND_BOUNCE_DECAY_JITTER[0]) ** 2 / (2.0 * CFG.G)
    _apex_hi = CFG.LAND_BOUNCE_MAX_VY ** 2 / (2.0 * CFG.G)
    _clear = (CFG.FLOOR - CFG.BALL_R) - CFG.DIV_TOP + CFG.BALL_R
    check(_apex_lo >= 10.0,
          "落地回弹的最低 apex >= 10px(肉眼可见口径, 与碰钉弹高门禁同尺)。"
          "旧值 LAND_BOUNCE_MIN_VY=220 是把它当**回弹**速度写的, 实际只弹 ~4px, 74% 的落袋"
          "肉眼看不见",
          "最低 apex %.1fpx" % _apex_lo)
    check(_apex_hi <= 45.0 and _apex_hi <= _clear,
          "落地回弹的最高 apex <= 45px, 且球顶不越隔板顶(%d)" % CFG.DIV_TOP,
          "最高 apex %.1fpx / 可用 %.1fpx" % (_apex_hi, _clear))
    check(CFG.LAND_BOUNCE_JITTER[0] < 1.0 and CFG.LAND_BOUNCE_JITTER[1] > 1.0,
          "落地高度带随机(用户定稿: 不要固定高度) —— 撞击速度乘随机系数, 落得越快弹得越高",
          "系数 %.2f~%.2f" % CFG.LAND_BOUNCE_JITTER)

    # 出货循环的接线(唯一必须文本断言的一条: 循环在 Kivy widget 的 _frame 里)。
    # ⚠️ 锚点重映射: 新版那次飞行循环住在 `Game.step`(老版在 `_frame` 里)。判据的关系没变。
    check(re.search(r"if landed is not None:(?:\s*#[^\n]*)*\s*break",
                    _fn_body(game_src, "_frame")) is not None,
          "_frame 的飞行循环落袋即 break(少了它, 球落袋后还会被继续模拟, 结算时刻/槽位白闪/"
          "装杯/揭晓随帧率漂; 实测越槽局多飞 0.68~1.27s, 状态栏那段时间还打着「即将入袋…」)")

    # ---- 18. 冷启动音频就绪(「首次安装必然没声音」的正面修复) ----
    # SoundPool.load() 是异步的, **返回 sampleId ≠ 解码完**, 没解码完 play() 返回 0(静默跳过);
    # 冷启动一口气灌 40 个合成音 + 61 条语音, 解码器来不及 ⇒ 整局没声。以前只有一行
    # time.sleep(0.5) 兜着, 而加载页一烘完就摘等于把那点缓冲也拿掉了。
    # 现在改成"探到真的能播再摘页"(0 增益试播当探针 + 硬超时)。
    print("\n[18] 冷启动音频就绪(首次安装没声音)")
    _off = Sfx(False)                 # enabled=False: __init__ 早退, 不起线程不碰后端
    check(_off.audio_ready() is True,
          "音效关掉时 audio_ready() 直接放行 —— 不这么做加载页永不摘 = 软锁(项目红线)")
    _st_off = _off.audio_status()
    check(isinstance(_st_off, str),
          "audio_status() 在没有后端时也不抛异常(它只是隐藏菜单里的一行, 不许把弹窗带崩)",
          repr(_st_off))
    _det_off = _off.audio_detail()
    # ⚠️ 玩家要求把三行并成两行(时间一行、加载进度一行), 而音效关掉时加载那行不出现 ⇒ 只剩 2 行。
    #    与其把阈值降到 2(那是削弱断言), 不如**直接查内容**。
    check(isinstance(_det_off, list) and len(_det_off) >= 2
          and any("音频后端" in r for r in _det_off)
          and any("总耗时" in r for r in _det_off)
          and any("音效加载" in r for r in _det_off)
          and any("无音频加载累计耗时" in r for r in _det_off)
          and not any("音效等待" in r for r in _det_off),
          "audio_detail() 返回字段行(「X启动总耗时Nms(音效加载Mms)」/「无音频加载累计耗时：Nms」"
          " / 加载进度 / 音频后端 —— 两支同一形状; 后端重建只在非 0 时出现, "
          "「音效开关」已整段删除。v0.8.67 起「音效等待」一律叫「音效加载」; "
          "v0.8.69 起 PC 那支也补齐这一行、并按同一行序摆)",
          repr(_det_off))
    # ⚠️ 这是**行为断言, 不是文本断言**(源码里那句"删除说明"的注释也会进出货文件)。
    check(all("音效开关" not in r for r in _det_off),
          "「音效开关」那一行**已按玩家要求删除**(玩家原话:「去掉音效开关： xx 这个行 没有意义」)"
          " —— 它永远显示「已开」, 属于这块面板自己那条成文规则「有唯一预期值的, 只在偏离时"
          "才有信息」的反面", repr(_det_off))
    # ⚠️ 这是 2026-09-11 实测挖出来的**真 bug**: `_KivySoundOut` 没有 loaded_count 而 mode 是
    #    "named", 而代码用 getattr 拿不到就**退回闸门值** ⇒ 安卓上 SoundPool 构造失败、静默降级到
    #    它时, 面板会报 `97 / 97` **全绿**。兜底只能是"不知道"。
    class _NamedNoCount:
        name, mode = "假命名后端", "named"
        rebuild_count = 0
        probe_all = lambda self: True          # noqa: E731
    _f2 = Sfx.__new__(Sfx)
    _f2.enabled, _f2.out, _f2.named = True, _NamedNoCount(), set("v%d" % i for i in range(97))
    _f2.cached, _f2.bake_ms, _f2.ready_ms = True, 10.0, 0.0
    _f2._expected, _f2._n_bank, _f2._failed = 97, 40, []
    _f2.n_attempt, _f2.n_missed = 0, 0
    _f2._total_ms, _f2._no_audio_ms = 0.0, 0.0
    _blob = " ".join(_f2.audio_detail())
    check("97 / 97" not in _blob and "未知" in _blob,
          "后端不报数时显示「未知」,**绝不退回闸门值**报成 97 / 97 全绿"
          "(那个分支里四行有三行假绿, 只有「音频后端」说真话)", _blob)
    # ⚠️ 玩家 2026-09-11 定稿: 最后那行「加载失败　N 个　·　语音 XX / YY」整行拆掉, 语音那半
    #    独立成「语音就绪：XX / YY」并**挪到「音效就绪」紧后面**。
    _rows_named = _f2.audio_detail()
    _blob_named = " ".join(_rows_named)
    check("语音就绪：" in _blob_named and "加载失败" not in _blob_named,
          "「加载失败 …」整行拆掉, 语音那半成了「语音就绪：XX / YY」", _blob_named)
    # ⚠️ 玩家要求把「音效就绪 / 语音就绪 / 音效等待」**三行并成一行**, 所以判据从「下一行」
    #    改成「**同一行内、且在「音效就绪」之后**」。
    _i_ready = next((i for i, r in enumerate(_rows_named) if "音效就绪" in r), -99)
    _row_ready = _rows_named[_i_ready] if _i_ready >= 0 else ""
    check(_i_ready >= 0 and "语音就绪" in _row_ready
          and _row_ready.index("语音就绪") > _row_ready.index("音效就绪"),
          "「语音就绪」紧跟在「音效就绪」后面(玩家: 「放在音效就绪的后面」; "
          "2026-09-18 起两者并进同一行)",
          " | ".join(_rows_named))
    _f3 = Sfx.__new__(Sfx)
    # out=None 时真实代码里 enabled 一定是 False(见 Sfx.__init__ 早退), 夹具必须造一致,
    # 否则测的是"不可能出现的状态"。
    _f3.enabled, _f3.out, _f3.named = False, None, set()
    _f3.cached, _f3.bake_ms, _f3.ready_ms = False, 0.0, 0.0
    _f3._expected, _f3._n_bank, _f3._failed = 0, 0, []
    _f3.n_attempt, _f3.n_missed = 0, 0
    _f3._total_ms, _f3._no_audio_ms = 0.0, 0.0
    _blob3 = " ".join(_f3.audio_detail())
    check("静音" in _blob3 and all("音效开关" not in r for r in _f3.audio_detail()),
          "彻底没有后端时「音频后端」那行印「静音」—— 和 PC 上正常态(winmm(8声道))不会长得"
          "一样(否则「全没了」和「本来就该这样」就分不出来了); 而「音效开关」那行已删除, "
          "这条**替代**了原来靠它区分的那条断言", _blob3)
    _fake = Sfx.__new__(Sfx)        # PCM 后端(PC 的 winmm/SoundLoader)
    # ⚠️ cached 给 False: PCM 后端不写磁盘缓存, 真实 PC 上它**恒为 False**。
    _fake.enabled, _fake.named, _fake.cached = True, set("abcdefghij"), False
    # ⚠️ v0.8.69 修的**空转门禁**: 这个夹具以前没给 `bank` ⇒ `AttributeError` 被
    #    `audio_detail` 最外层吞掉 ⇒ **返回空列表** ⇒ 下面几条 PC 断言全部恒假。
    _fake.bank = {}
    _fake._total_ms, _fake._no_audio_ms = 0.0, 0.0
    _fake.bake_ms, _fake.out = 553.0, type("Out", (), {"name": "winmm", "mode": "pcm"})()
    _fake.ready_ms, _fake._expected, _fake._n_bank, _fake._failed = 0.0, 0, 0, []
    _fake.n_attempt, _fake.n_missed = 0, 0
    _pcm_rows = _fake.audio_detail()
    _pcm_blob = " ".join(_pcm_rows)
    check("不适用" not in _pcm_blob and "0 / 0" not in _pcm_blob,
          "PCM 后端(PC)下那三项**结构上不适用**的行直接不出现 —— 而不是印成三行「不适用」"
          "(玩家 2026-09-11: 「这几个不适用听起来有点奇怪」: 既是噪音, 又把有内容的两行淹了)",
          _pcm_blob)
    # ⚠️ 玩家 2026-09-11 定稿: 「我在pc上也需要知道版本号 也需要知道那几个时间 别tmd自作主张」
    #    ⇒ PC 上必须有那几个时间, **与安卓同一形状**。
    check("冷启动总耗时553ms" in _pcm_blob and "音效加载" in _pcm_blob
          and "无音频加载累计耗时" in _pcm_blob and "音效等待" not in _pcm_blob,
          "PC 上也要报那几个时间, 且与安卓**同一形状**: 「X启动总耗时Nms(音效加载Mms)」"
          " + 「无音频加载累计耗时：Nms」—— v0.8.69 起 PC 那支补齐(以前它缺那一行、"
          "名字还是老的「音效等待」、并且「音频后端」排在第一个)", _pcm_blob)
    # ⚠️ 「总耗时」的**唯一取值口** = `_total_ms`(摘页那一刻的启动时钟, 与另一行同一次测量)
    #    —— 读到 0 才退回老的 `bake_ms + ready_ms`。下面两发钉住这两个分支。
    _fake._total_ms, _fake._no_audio_ms = 1328.0, 1328.0
    _blob_t = " ".join(_fake.audio_detail())
    check("冷启动总耗时1328ms" in _blob_t and "无音频加载累计耗时：1328ms" in _blob_t,
          "有实测值时「总耗时」读 `_total_ms`(摘页时钟) —— 不是 `bake_ms + ready_ms`"
          "(553) ; 玩家截图那个矛盾(463 < 1328)就是老算法漏掉"
          "「进程启动 -> 烘焙开工」那一段造成的", _blob_t)
    _fake._total_ms, _fake._no_audio_ms = 0.0, 0.0
    _blob_t0 = " ".join(_fake.audio_detail())
    check("冷启动总耗时553ms" in _blob_t0,
          "取不到实测值(没建加载页 / 重放刚清过)时退回老算法, 而不是印 0", _blob_t0)
    _sfx_min = Sfx.__new__(Sfx)     # 最小状态, 不走 __init__
    _sfx_min.enabled, _sfx_min._audio_ready = True, False
    _sfx_min.named, _sfx_min.cached, _sfx_min.bake_ms, _sfx_min.out = set(), False, 0.0, None
    check(_sfx_min.audio_ready() is False,
          "音效开着且**还没就绪**时不放行(不然加载页形同虚设, 等于退回改动前)")
    check(play_src.count("audio_ready()") >= 2,
          "出货 main.py 里加载页的创建与摘除都按 audio_ready() 判(不是「烘完就摘」)")
    check("if self._plugged is None:" in backend_src,
          "sticky 广播首帧只记初值、不重建 —— 旧代码拿 0/1 去比 None 恒假, "
          "return 永不生效, 于是每次启动都白重建一次 SoundPool(冷路径下还会把 _ids 清在半路)")
    check("probe_all" in backend_src and "def loaded_count" in backend_src,
          "后端提供 probe_all(能播探针) 与 loaded_count(后端真相, 诊断行要读它而不是闸门)")
    # 玩家 2026-09-11 定稿**删除**「输出采样率」那一行: 真机与模拟器实测**都是 48000** ⇒
    # 它成了"永远不变的那一行", 而这块面板的成文规则是"有唯一预期值的, 只在偏离时才有信息"。
    check("_android_output_rate" not in SHIPPED and "_out_rate" not in SHIPPED,
          "「输出采样率」那一行连同它的取值口(_android_output_rate / _out_rate)已整段删除")

    # ⚠️ 玩家 2026-09-11 定稿(原话): 「启动的时候逐渐渲染五个比较大的汉字: 跳跳的弹珠机。
    #    渲染完成后, 再次重复渲染 或从高级渲染变成超高级渲染, 反正最多 6 秒钟」,
    #    外加一句总纲「**总之这个东西不能是突变**」。
    #    ⚠️ 这套逻辑老版**内联在 `_frame` 里、零自动覆盖**; 新版核心是**纯函数**
    #    `_veil_title_state()` —— 这里直接钉死, 否则改坏了完全静默。
    # ⚠️ `_LoadVeil` / `_veil_title_state` / `VEIL_*` 在新版住在 `danzhu/ui/veil.py` 与
    #    `danzhu/config.py`(老版全在 main 的平铺命名空间里)。
    _veil_ns = (VEIL, CFG)                    # 老版 `M` 的替身: 探针只做 hasattr 负断言
    check(not hasattr(VEIL, "_veil_status_line") and not hasattr(LoadVeil, "set_status"),
          "启动页**没有实时诊断行**: 那条路(_veil_status_line / set_status)已整段删除"
          "(那些数「启动信息」里全都有, 而且更全)")
    check(LoadVeil.__init__.__defaults__[0] == "",
          "启动页默认文案是**空的** —— 那五个大字是 `_build_title` 单独建的; 只有「重放冷启动」"
          "那条路会带文字进来(那是作者主动点开、专门停下来读的一屏)")
    check(CFG.VEIL_TITLE == "跳跳的弹珠机",
          "开场那行字**就是游戏名**(六个字 —— 玩家口述的\"五个\"不影响, 长短由 `len()` 自适应)",
          repr(CFG.VEIL_TITLE))

    # ---- 开场动画的**形状**(纯函数, 逐条钉死) ----
    # ⚠️ 三条不变量, 少一条就是一次"突变":
    #    ① t=0 那帧棋盘必须**满亮**; ② 六个字的 alpha **单调不回落**; ③ 迟早全亮 + 圈次只增不减。
    check(not any(hasattr(_ns, _n) for _ns in _veil_ns
                  for _n in ("_veil_img_alpha", "_veil_fit", "_loadveil_src", "_pick_box",
                             "LOADVEIL_ASPECT", "VEIL_TITLE_IMG_ALPHA")),
          "启动页那条**图片路径已整段删除**(不再挂 presplash 那张图, 也没有盒子/夹断/占位图那些事)",
          "还留着的: %s" % [n for _ns in _veil_ns
                            for n in ("_veil_img_alpha", "_veil_fit", "_loadveil_src",
                                      "_pick_box", "LOADVEIL_ASPECT",
                                      "VEIL_TITLE_IMG_ALPHA") if hasattr(_ns, n)])
    _lit0, _fill0, _g0, _rl = VEIL._veil_title_state(0.0)
    check(_lit0 < 1e-9 and _fill0 < 1e-9,
          "**t=0 那一帧**: 六个字还没淡入、填充线也还没起步(出来的时候才慢慢到齐, 不是一上来就满亮)",
          "lit=%.3f fill=%.3f" % (_lit0, _fill0))
    # ⚠️ 三条不变量: ① 整行**一起**淡入且单调; ② 填充线**一轮之内只增不减**; ③ 圈次只增不减。
    _lits, _fills, _grades = [], [], []
    for _i in range(600):
        _l, _f, _g, _ = VEIL._veil_title_state(_i / 60.0)
        _lits.append(_l); _fills.append(_f); _grades.append(_g)
    check(all(_lits[i] <= _lits[i + 1] + 1e-9 for i in range(len(_lits) - 1))
          and _lits[-1] >= 1.0 - 1e-9,
          "整行**一起**淡入且**单调**, 最后到满(不回落)", "%.3f -> %.3f" % (_lits[0], _lits[-1]))
    _fill_bad = []
    for _kk in range(3):
        _t0k = _kk * _rl
        _f_s = VEIL._veil_title_state(_t0k)[1]
        _f_e = VEIL._veil_title_state(_t0k + _rl - 1e-6)[1]
        if _f_s > 1e-6 or _f_e < 1.0 - 1e-6:
            _fill_bad.append("第%d轮 起%.3f 终%.3f" % (_kk, _f_s, _f_e))
    _mono_f = True
    for _kk in range(3):
        _prev = -1.0
        for _i in range(int(_rl * 60)):
            _f = VEIL._veil_title_state(_kk * _rl + _i / 60.0)[1]
            if _f < _prev - 1e-9:
                _mono_f = False
            _prev = _f
    check(not _fill_bad and _mono_f,
          "**KTV 那条填充线**: 一轮之内只增不减、这轮扫完到 1(整行都换过色)、下一轮从 0 重新扫",
          ("; ".join(_fill_bad) if _fill_bad else "") + ("(单调性破了)" if not _mono_f else ""))
    check(_grades[-1] > _grades[0]
          and all(_grades[i] <= _grades[i + 1] for i in range(len(_grades) - 1))
          and "grade % _n_col" in veil_src,
          "圈次**只增不减**, 且**演完最后一种颜色回到第一种**(取模, 不是夹断) —— "
          "玩家: 「无限演 …… 颜色1结束播放颜色2 颜色2结束播放颜色3 结束播放颜色1」",
          "10 秒内到第 %d 圈, 共 %d 种颜色" % (_grades[-1], len(CFG.VEIL_TITLE_COLORS)))
    check(all(len(c) == 3 for c in CFG.VEIL_TITLE_COLORS) and len(CFG.VEIL_TITLE_COLORS) >= 2,
          "色表是**一串 RGB**, 至少两种颜色; 顺序就是变色的顺序(银 -> 黄 -> 红 -> 银)",
          "%d 种" % len(CFG.VEIL_TITLE_COLORS))
    # ⚠️ 老版读的是 `tools/android_part_ui.py` 里 `class _LoadVeil` 之后那一段 ——
    #    新版那一块整个住在 `danzhu/ui/veil.py`。
    _ui_src = veil_src
    check(not hasattr(VEIL, "VEIL_TITLE_HALO_SCALE") and "PushMatrix" not in _ui_src,
          "那一行字**没有描边/阴影/辉光/缩放** —— 只有颜色(玩家: 「高级不是土味审美」"
          "「别tmd加奇怪的描边了 阴影了」)")
    check("StencilPush" in _ui_src and "_clip_a" in _ui_src,
          "填充线的粒度是**像素级**的(两层字叠着 + 一条裁剪), 所以能切在某个字的中间 —— "
          "玩家: 「圆点左边的字体是新颜色 甚至颗粒度可以很细什么的」")
    # ⚠️ 2026-09-11 改口径: 原来要求 `MIN_SEC >= 一整轮` —— 那条地板的立论是"动画没铺满
    #    就被摘 = 突变", 但**它是实现时自己加的, 玩家从来没要过**。玩家报的正是「加载完成就进入
    #    游戏界面, 实际上是播放了跳跳的弹珠机这几个字才进去」。现在的口径是**"够那行字完整淡入"**。
    check(CFG.VEIL_TITLE_MIN_SEC >= CFG.VEIL_TITLE_IN_SEC - 1e-9,
          "**最短停留**够那行字**完整淡入**(否则字亮到一半就被摘 —— 半截动画比没有动画更像出 bug);"
          " **不要求**走完一整轮(那会逼加载快的机器白等一轮多)",
          "淡入=%.2fs  最短停留=%.2fs  一轮=%.2fs"
          % (CFG.VEIL_TITLE_IN_SEC, CFG.VEIL_TITLE_MIN_SEC, _rl))
    # ⚠️ **前摇不得短于整行淡入** —— 否则那六个字会"一边淡入一边变色"。
    _spin = (CFG.VEIL_TITLE_LEAD + CFG.VEIL_TITLE_SWEEP + CFG.VEIL_TITLE_ROUND_GAP)
    check(CFG.VEIL_TITLE_LEAD >= CFG.VEIL_TITLE_IN_SEC - 1e-9,
          "**前摇不短于整行淡入**(否则字会一边淡入一边变色 —— 玩家要的是"
          "「刚开始是都能看到的, 然后逐渐改变颜色」)",
          "淡入=%.2fs  前摇=%.2fs  扫=%.2fs  后停=%.2fs  一轮=%.2fs"
          % (CFG.VEIL_TITLE_IN_SEC, CFG.VEIL_TITLE_LEAD, CFG.VEIL_TITLE_SWEEP,
             CFG.VEIL_TITLE_ROUND_GAP, _spin))
    check(abs(_spin - _rl) < 1e-9,
          "一轮的时长 == 前摇+扫+后停(`_veil_title_round_len()` 与三个常量必须自洽)",
          "round_len=%.2fs  三个常量相加=%.2fs" % (_rl, _spin))
    check(abs(CFG.VEIL_TITLE_MAX_SEC - Sfx.SFX_READY_TIMEOUT) < 1e-9,
          "**最长停留 = 音效就绪的硬超时**(两个 6 秒同值): 一个管\"别等太久\", 一个管\"最多演 6 秒\","
          "改一个必须看另一个", "%.1f / %.1f" % (CFG.VEIL_TITLE_MAX_SEC, Sfx.SFX_READY_TIMEOUT))
    check(not hasattr(VEIL, "VEIL_TITLE_FADE_OUT") and "_fade_t0" not in veil_src,
          "**没有淡出** —— 到点整页直接消失(玩家 2026-09-11: 「这里应该是啥都看不见, "
          "消失得干干净净, **而不是有淡出**」): 那行字一帧都不许压在游戏画面上")
    check(not any(hasattr(_ns, _n) for _ns in _veil_ns
                  for _n in ("LOADVEIL_GROW_SEC", "LOADVEIL_GROW_FROM",
                             "LOADVEIL_BREATH_SEC", "LOADVEIL_BREATH_AMP", "_veil_scale")),
          "历代两代动效(进场从小到大 v0.6.26~28 / 呼吸 v0.6.29~35)的常量与函数都已删除, "
          "**没有复活**")

    # 底色**三处同值** —— 这一版"没有突变"的**唯一保证**。
    # ⚠️ 2026-09-11 起启动页不再挂 presplash 那张图了(纯色): 系统那张图本身也是纯色, 于是从
    #    "点图标"到"进游戏"整条链路上一直是**同一个颜色**。**但代价是这个值散在三个文件里** ——
    #    改一个忘一个, 用户就会在一次启动里看到两种颜色, 那正是玩家说的"突变"。
    # ⚠️⚠️ 路径重映射: 安卓打包那三个文件(buildozer.spec / p4a/hook.py / presplash.png)
    #    **还没搬进 new_danzhu**(README「还没做的」里点着这一条), 目前唯一的真源仍是老工程
    #    `android/` 下的那三份 —— 所以这条门禁读的是老工程那三个文件, 跟的还是"三处必须同值"。
    #    ⚠️ 这个根 = 模块级的 `PACK_ROOT`(`_startup_title` 那条也用同一个根, 不再各指各的)。
    _pack = PACK_ROOT
    _spec_txt = io.open(os.path.join(_pack, "buildozer.spec"),
                        encoding="utf-8").read()
    _m_pc = re.search(r"^android\.presplash_color\s*=\s*(#[0-9a-fA-F]{6})", _spec_txt, re.M)
    _hook_txt = io.open(os.path.join(_pack, "p4a", "hook.py"),
                        encoding="utf-8").read()
    _m_th = re.search(r"windowSplashScreenBackground[^>]*?>\s*(#[0-9a-fA-F]{6})", _hook_txt)
    check(_m_pc is not None and _m_th is not None
          and _m_pc.group(1).lower() == CFG.VEIL_BG.lower() == _m_th.group(1).lower(),
          "**底色三处同值** = 没有突变的唯一保证: `VEIL_BG`(这一页) == "
          "`buildozer.spec` 的 `android.presplash_color`(系统那张图) == `p4a/hook.py` 注入的 "
          "`windowSplashScreenBackground`(应用启动窗口)",
          "VEIL_BG=%s  presplash_color=%s  主题=%s"
          % (CFG.VEIL_BG, _m_pc and _m_pc.group(1), _m_th and _m_th.group(1)))
    # `presplash.png` 必须**还是那张纯色图**(不能被人换回带图案的)。
    #    ⚠️ 这里**读不了像素**: 无窗口进程里碰 `texture.pixels` 会 **segfault**。所以用两个
    #    够硬的旁证: 尺寸还是 1080x1920; **文件很小**(纯色 PNG 几 KB, 带图案的几百 KB)。
    _ps = os.path.join(_pack, "presplash.png")
    try:
        _psz = os.path.getsize(_ps)
        _pt = _CoreImage(_ps).texture
        check(tuple(_pt.size) == (1080, 1920) and _psz <= 32 * 1024,
              "`presplash.png` 还是那张**纯色图**(1080x1920 且体积很小) —— 系统那层和这一页之间"
              "才会是「同一个颜色接同一个颜色」",
              "%s, %.1f KB" % (tuple(_pt.size), _psz / 1024.0))
    except Exception as _e:
        check(False, "presplash.png 可读", repr(_e))

    check("hex_rgb(VEIL_BG)" in veil_src and CFG.VEIL_BG != CFG.COL_BG,
          "启动页底色用 `VEIL_BG`(#0b1220), **不是**游戏的 `COL_BG` —— 两者不同是有意的:"
          "前者要和系统那两层对齐, 后者是游戏自己的背景")
    check("_veil.tick(self.sfx.audio_ready())" in play_src,
          "_frame 每帧驱动启动页(它自己不持有 Clock: 切后台回来直接跳终态, 不会冻在半路); "
          "**就绪判据由 `tick()` 给** —— 它还要管开场动画的最短停留与整页淡出, 所以 `_frame` "
          "不再自己看 `audio_ready()`")

    # ⚠️ 2026-09-11 一天踩了两次的坑: 改方法时**插进了隔壁 class 的中间** —— 第一次把 _frame
    #    拦腰截断, 第二次把两个方法塞进了 _LoadVeil。**两次都能编译, --check 也全绿**。
    #    `hasattr` 是这里唯一有分辨力的判据: 它问的是"这个方法**长在哪个类上**"。
    # ⚠️ 老版这个清单里有 4 个方法属于跑分链(新版在 `BenchMixin`), 它们在 `RootWidget` 上
    #    **仍然找得到**(继承) —— 判据不变。
    _must_on_rootwidget = ("_frame", "_replay_cold_start", "_replay_summary",
                           "_finish_replay_veil", "_show_startup_info", "_show_bench_menu",
                           "_check_title_hold", "_build_info")
    _missing = [n for n in _must_on_rootwidget if not hasattr(RootWidget, n)]
    check(not _missing,
          "关键方法都长在 RootWidget 上(不是掉进了隔壁 class —— 生成物里方法边界肉眼看不出来, "
          "而掉错类**能编译**, 只有 hasattr 能问出'它长在哪')", "; ".join(_missing))
    _leaked = [n for n in _must_on_rootwidget if hasattr(LoadVeil, n)]
    check(not _leaked, "没有方法漏进 _LoadVeil(它只有一个 __init__ 和几个触摸/文案方法)",
          "; ".join(_leaked))

    # ---- 18. 装杯发牌顺序: 随机 + 拓扑合法, 且绘制按深度(含飞行球) ----
    # 起因(玩家 2026-09-12 原话):「弹珠落入杯子的顺序好像不随机, **总是先内后外**。」
    # 根因: `_make_balls` 原来是 `for p in proj`, 而 proj 是按 z 降序的画家序。
    # 现在换成随机拓扑序(见 `_topo_deal`): 每次从"正下方的球都已发出"的那批里随机挑一颗。
    print("\n[18] 装杯发牌顺序: 随机 + 拓扑合法")

    def _deal_balls(n, v, sd):
        """跑一局 _make_balls, 返回**按发牌序**排好的球表。
        ⚠️ `_balls` 的列表序是画家序(-z), 发牌序在 "i" 字段里 —— 别拿列表序当发牌序验。"""
        WinPileFX.__init__(fx, None)
        fx._rng = random.Random(sd)
        WinPileFX._make_balls(fx, n, 10, v)
        return sorted(fx._balls, key=lambda b: b["i"])

    # 18a 拓扑合法: 后发的球不许压在先发的球正下方
    _viol = 0
    _pairs = 0
    for _n in (20, 100):
        for _v in (0, 7):
            _bs = _deal_balls(_n, _v, 12345)
            _rr = _bs[0]["r_d"]
            _thr2 = (2.0 * _rr) ** 2
            _H = [pile.FLOOR_Y - b["syf"] - pile.K2 * b["z"] for b in _bs]
            for _t in range(len(_bs)):
                for _u in range(_t + 1, len(_bs)):
                    if _H[_u] >= _H[_t]:
                        continue                      # u 更高 -> 不是 t 的前驱
                    _dx = _bs[_u]["sxf"] - _bs[_t]["sxf"]
                    _dz = _bs[_u]["z"] - _bs[_t]["z"]
                    if _dx * _dx + _dz * _dz < _thr2:
                        _viol += 1
                    _pairs += 1
    check(_viol == 0,
          "拓扑合法: 每颗球发牌时, 它**正下方**(水平距 < 一个球直径且更低)的球都已发牌 "
          "—— 判据用水平距而不是 3D 距: 球是竖着落的, 水平距小于球直径就必然相交",
          "违规 %d 对(共查 %d 对)" % (_viol, _pairs))

    # 18b 顺序随机: 同倍率、同变体、不同种子两局的发牌序必须不一样
    _same_worst = 0.0
    for _n in (20, 100):
        _runs = [_deal_balls(_n, 0, s) for s in (1, 2, 3)]
        for _a in range(len(_runs)):
            for _b in range(_a + 1, len(_runs)):
                _same = sum(1 for i in range(_n)
                            if abs(_runs[_a][i]["sxf"] - _runs[_b][i]["sxf"]) < 1e-6
                            and abs(_runs[_a][i]["syf"] - _runs[_b][i]["syf"]) < 1e-6)
                _same_worst = max(_same_worst, _same / float(_n))
    check(_same_worst < 0.20,
          "发牌顺序**每局不同**(不再是「12 个变体共享同一个顺序」): 逐位比对两局第 i 颗球的"
          "落点, 相同的比例必须很低(随机下期望约 1/n)。照旧实现下这里恒 = 1.000",
          "两局最大重合 %.3f" % _same_worst)

    # 18c 绘制按深度 —— **含飞行球**。发牌随机化之后, "飞行球一律置顶"就不再成立。
    #     判据是**真 _redraw 产出的指令表里两颗球谁先画**。
    fxq = WinPileFX.__new__(WinPileFX)
    WinPileFX.__init__(fxq, None)
    fxq.pos = (0.0, 0.0)
    fxq.size = (400.0, 520.0)
    fxq.mode = "win"
    fxq._balls = [
        # 列表序 = 画家序(z 降序) -> 第一颗更远。**它故意是飞行球**: 照旧把飞行球一律
        # 置顶, 它就会被排到最后 -> "远的画在近的上面" = 玩家眼里的穿帮。
        {"settled": False, "r_d": 20.0, "sxf": pile.CX, "syf": pile.FLOOR_Y - 40.0,
         "sx0": pile.CX, "sy0": pile.FLOOR_Y - 420.0, "tt": 0.6, "f": 1.0,
         "t1": 0.15, "t2": 0.08, "ring_angle": 0.0, "spin_deg": 0.0,
         "shade": 1.0, "value": 50, "f_enter": 0.0, "z": 120.0},
        {"settled": True, "r_d": 18.0, "sxf": pile.CX + 30.0, "syf": pile.FLOOR_Y - 10.0,
         "ring_angle": 0.0, "shade": 1.0, "value": 10, "z": -120.0},
    ]
    fxq._redraw()
    _t_fly, _t_set = WFX._ball_texture(50), WFX._ball_texture(10)
    _o_fly = _o_set = None
    for _i, _ins in enumerate(fxq.canvas.children):
        if not isinstance(_ins, Rectangle):
            continue
        _t = getattr(_ins, "texture", None)
        if _t is _t_fly and _o_fly is None:
            _o_fly = _i
        elif _t is _t_set and _o_set is None:
            _o_set = _i
    check(_o_fly is not None and _o_set is not None and _o_fly < _o_set,
          "飞行球也按深度画(远的先画、近的后画): 发牌随机化之后飞行球不再恒为最近的球, "
          "照旧把它一律置顶 = 更远的球盖住更近的已落定球",
          "飞行球(z=+120) @%s  已落定球(z=-120) @%s" % (_o_fly, _o_set))

    # ---- 19. 隐藏档弹窗: 每次都弹 + 四选一 + 关闭隐藏档 ----
    # 玩家 2026-09-12 定稿: 长按"期望返还比例"3 秒 -> 四选一 + 确认; 每次长按都弹;
    # 选「关闭隐藏」能把隐藏档按钮**摘掉**。这四条各自都踩得到, 所以各钉一条。
    # ⚠️⚠️ **结构重映射**: 老版这一块整个长在 `RootWidget` 上(状态与控件同体); 新版劈成两半
    #    —— **状态**(防重入闸 / 档位真源 / 摘按钮)在 `danzhu/game.py :: Game`,
    #    **控件**在 `danzhu/ui/play.py :: PlayMixin`, 中间由 `RootWidget._dispatch` 按事件搬。
    #    夹具因此装成"一个 Game + 一个最小 UI 宿主", 用的都是**出货那份方法体**。
    print("\n[19] 隐藏档弹窗")

    class _RecSfx(object):
        """假音效: 只记名字 —— 与**老版夹具逐字同一形状**(老版 `sfx.play` 是直调, 新版由
        `Game` 发 `sound` 事件、经出货的 `PlayMixin._dispatch_one` 落到这里)。"""
        def __init__(self):
            self.played = []
        def play(self, name, gain=1.0, throttle=0.0):
            self.played.append(name)
            return True

    class _RtpHost(object):
        """`PlayMixin` 里"返还率那排按钮 / 隐藏档弹窗"那一半的最小宿主。

        ⚠️ 方法一律绑**出货那份**(`PlayMixin` / `UiMixin`), 一个字都没复制。
        ⚠️⚠️ **那座桥也要走出货的**(2026-09-19 修): 老版这一块方法体与状态同体, 夹具直调
           `RootWidget._unlock_rtp` 就是端到端; 新版劈成 `Game`(状态/发事件) + `PlayMixin`
           (控件), 中间靠 `PlayMixin._dispatch_one` 里 `rtp_btn_add` / `rtp_btn_remove` 两条
           分支搬。原来 `pump()` **把这两条分支手抄了一份** ⇒ ①出货逻辑被复制进测试,
           ②没有任何一条 check 覆盖那座桥(`_dispatch_one` 那两行删掉, 门禁照样全绿)。
           现在 `pump()` 直接调**出货的** `_dispatch`, 两条分支与整个事件循环都在覆盖里。
        """
        _mk_button = UIB.UiMixin._mk_button
        _install_fit = UIB.UiMixin._install_fit
        _auto_h = UIB.UiMixin._auto_h
        _popup_fit_content = UIB.UiMixin._popup_fit_content
        _add_rtp_button = PLAY.PlayMixin._add_rtp_button
        _remove_rtp_button = PLAY.PlayMixin._remove_rtp_button
        _ask_unlock_rtp = PLAY.PlayMixin._ask_unlock_rtp
        # `pump()` 现在走整条派发, 于是 `redraw` / `reflow_row_budget` / `restyle_buttons`
        # 那几条也会到 (老版夹具同样有 `game_area` 桩与 `bet_btns`)。`_reflow_row_budget`
        # 内部自带 try(半套控件), 与老版一样静默跳过。
        _dispatch = PLAY.PlayMixin._dispatch
        _dispatch_one = PLAY.PlayMixin._dispatch_one
        _reflow_row_budget = UIB.UiMixin._reflow_row_budget
        _restyle_buttons = UIB.UiMixin._restyle_buttons

        def __init__(self, game):
            self.game = game
            self._rtp_row = BoxLayout(size_hint_y=None, height=100)
            self._rtp_spacer = Widget()
            self._rtp_row.add_widget(self._rtp_spacer)
            self._font_scale = 1.0
            self._ui_scale = 1.0
            self._popup = None                  # 夹具会覆盖它
            self.bet_btns = {}                  # `_restyle_buttons` 会遍历它(老版夹具同款)
            self.sfx = _RecSfx()

            class _GA(object):
                """盘面事件(`redraw` / `update_slots`)会调 `game_area` 的方法 —— 给计数桩就够
                (老版夹具同款)。

                ⚠️ **两个方法都要留**: 2026-09-19 起 `set_rtp` 切档**不再整块重画**, 改走
                   `_update_slots()`(切档时几何一点没变, 只换 9 个倍率槽 —— 见 `Game.set_rtp`
                   的注释与老版交接文档 `android/jiaojie.md` 方案四)。当时正是这里抛
                   `AttributeError: '_GA' object has no attribute '_update_slots'` 把门禁打红的。
                   ⇒ **桩件必须跟着"出货代码真实会调的方法"走**, 少一个就是假红/假绿。
                """
                def __init__(self):
                    self.redraws = 0
                    self.slot_updates = 0
                def _redraw(self):
                    self.redraws += 1
                def _update_slots(self):
                    self.slot_updates += 1

            self.game_area = _GA()
            # 老版 `_ask_unlock_rtp` 画完弹窗后不再自己搬事件; 新版把两半接起来的那一步
            # 就是 `root.py` 的构造注入(`on_rtp_popup=self._ask_unlock_rtp`) —— 这里照抄。
            game._on_rtp_popup = lambda opts, cur: PLAY.PlayMixin._ask_unlock_rtp(self, opts, cur)

        def pump(self):
            """把 `Game` 的事件搬进控件 —— **走出货的 `_dispatch` 本体**(见类 docstring)。"""
            self._dispatch(self.game.take_events())

    class _FakePopup(object):
        def __init__(self, **kw):
            self.kw = kw
            self.dismissed = False
            self._dismiss_cbs = []

        def bind(self, **kw):
            if "on_dismiss" in kw:
                self._dismiss_cbs.append(kw["on_dismiss"])

        def open(self):
            pass

        def dismiss(self):
            self.dismissed = True

        def _fire_dismiss(self):
            for cb in self._dismiss_cbs:
                cb(self)

    def _rtp_fixture(target):
        """只装"返还率那排"的夹具 —— `Game` 走 `__init__` 的真路径(老版夹具是 `__new__` 手搓,
        因为那时状态和控件同体、只能用 `__new__` 绕开建控件树)。

        ⚠️⚠️ **夹具语义变了(2026-09-19 定性纠正)**: 这不是"详情值噪声", 是**夹具换了个东西**。
           老版: `RootWidget.__new__` + 手写 `w._boards = dict((_v, [0] * NUM_SLOTS) ...)`
                 ⇒ 盘面是**常量 0**;
           新版: 真 `Game(config_path=None, history_path=None)`, `_boards` 是 `Game.__init__`
                 按种子真掷出来的 ⇒ 盘面是**真值**。
           ⇒ 直接后果: 【阴性对照】那条的详情 `盘面首格` 老版 `0` / 新版 `2`;
             同源的还有 `实际开过=797868→148710`、保存 txt `8354→8347 字节`。
           **新版这条判据其实更硬**(验的是真盘面能换得动, 而不是"0 换成了别的")。
           归类: 「夹具语义变更」—— 既不是判据放宽, 也不是单纯的详情值差异。"""
        _game = Game(config_path=None, history_path=None)
        # ⚠️ `Game.__init__` 里已经跑过 set_bet / set_rtp / park_ball 并攒了一串初始事件;
        #    夹具不建控件树, 所以先把它们排掉, 免得跟下面那句 `rtp_target` 打架。
        _game.take_events()
        _game.rtp_target = target
        _game.state = "ready"
        _game.sound_mode = "on"
        _game._result_until = 0.0
        w = _RtpHost(_game)
        for lab, val in Game.RTP_TIERS:
            PLAY.PlayMixin._add_rtp_button(w, lab, val)
        _game.take_events()
        return w

    def _open_rtp(host):
        """走完整那条链: `Game.ask_unlock_rtp()`(占闸 + 调注入回调) -> `PlayMixin._ask_unlock_rtp`。"""
        cap = {}
        host._popup = lambda hint_w, h_dp, **kw: (
            cap.update(kw), _FakePopup(**kw))[1]
        host.game.ask_unlock_rtp()
        return cap

    # 19a 每次都弹: 那个"解锁过就不再弹"的守卫标志必须**整个消失**
    # ⚠️ 判据必须走 **AST**, 不能扫文本 —— 解释这段历史的注释和 docstring 里就写着这个名字,
    #    按行剥 `#` 也剥不掉 docstring。AST 只认真正的属性访问。
    _flag_hits = [n for _v in SRC.values() for n in ast.walk(ast.parse(_v))
                  if isinstance(n, ast.Attribute) and n.attr == "_rtp_unlocked"]
    check(not _flag_hits and Game.RTP_UNLOCK_HOLD == 3.0,
          "长按入口**每次都弹**: `_rtp_unlocked`(只置真、从不置假, 一局只能解锁一次)"
          "已整个删除, 长按时长 5s -> 3s。留着它 = 第二个状态源(真状态在那排按钮里)",
          "代码里引用 %d 处; HOLD=%.1fs"
          % (len(_flag_hits), Game.RTP_UNLOCK_HOLD))

    # 19b 防重入: 长按入口是 Window 级观察者, 模态弹窗拦不住它
    _f = _rtp_fixture(0.80)
    _calls = []
    _captured = {}
    _f.game._on_rtp_popup = lambda opts, cur: (
        _calls.append(1), _captured.update({"opts": opts, "sel": cur}))
    _f.game.ask_unlock_rtp()
    _f.game.ask_unlock_rtp()                         # 弹窗还开着 -> 应被闸门吞掉
    _n_first = len(_calls)
    _f.game.rtp_popup_dismissed()                    # 关掉(含点外部/系统关) -> 放行
    _f.game.ask_unlock_rtp()
    check(_n_first == 1 and len(_calls) == 2,
          "不叠弹窗: 弹窗开着时再长按被闸门吞掉; 关掉之后能再弹"
          "(Window 级触摸观察者不受模态弹窗拦, 闸门必须开在建弹窗那一端)",
          "开着的两次里建了 %d 个; 关掉后再来 = 共 %d 个" % (_n_first, len(_calls)))

    # 19c 选项清单跟着 RTP_HIDDEN 走(不手抄档位文字)
    _f2 = _rtp_fixture(0.80)
    _cap2 = _open_rtp(_f2)
    _texts = [c.text for c in _cap2["content"].walk() if isinstance(c, Button)]
    _want = {"关闭隐藏"} | set(lab for lab, _v in Game.RTP_HIDDEN)
    check(set(_texts) == _want | {"确定"} and _texts.count("确定") == 1,
          "弹窗选项文字 == {关闭隐藏} ∪ RTP_HIDDEN 的档位文字 + 一个「确定」"
          "(手抄过一次档位文字, 隐藏档改定稿时漏改 -> 玩家 2026-09-11 截图报的就是这个)",
          "实际 %s" % _texts)

    # 19d 默认选中 = 当前档位(隐藏档->那一档; 常驻档->关闭隐藏)
    def _sel_of(target):
        _w = _rtp_fixture(target)
        _cap = _open_rtp(_w)
        # ⚠️ 必须排掉「确定」: 它也是 `COL_BTN` 的兄弟色(不排的话判据会失去分辨力)。
        _hot = [c.text for c in _cap["content"].walk()
                if isinstance(c, Button) and c.text != "确定"
                and list(c.background_color[:3]) == list(CFG.hex_rgb(CFG.COL_BTN))]
        return _hot

    _h1, _h2 = _sel_of(20.0), _sel_of(1.20)
    check(_h1 == ["2000%"] and _h2 == ["关闭隐藏"],
          "默认选中当前档位: 开着 2000% -> 高亮 2000%; 常驻档 -> 高亮「关闭隐藏」"
          "(恰好一个高亮)",
          "20.0 -> %s ; 1.20 -> %s" % (_h1, _h2))

    # 19d-2 「确定」不能和选中态同色 —— 玩家 2026-09-12 实测报的:"确定按钮的颜色和
    #   选择按钮的颜色一致"。选中态必须是 COL_BTN, 所以该换色的是「确定」。
    _ok_col = None
    for _c in _cap2["content"].walk():
        if isinstance(_c, Button) and _c.text == "确定":
            _ok_col = list(_c.background_color[:3])
    check(_ok_col is not None and _ok_col != list(CFG.hex_rgb(CFG.COL_BTN)),
          "「确定」不能和选中态同色(同色时玩家分不清哪个是「当前选中」)",
          "确定 = %s ; 选中态 = %s" % (_ok_col, list(CFG.hex_rgb(CFG.COL_BTN))))

    # 19e 关闭隐藏档(最值钱的一条)
    _g = _rtp_fixture(0.80)
    _g.game.unlock_rtp(10.0)
    _g.pump()
    _g.game.unlock_rtp(20.0)
    _g.pump()
    _n_open = len(_g._rtp_row.children)                # 19e-2 用: 隐藏档开着时那排有几行孩子
    _only_one = sorted(_g.game.rtp_btns) == [0.80, 1.20, 2.00, 3.60, 20.0]
    _g.sfx.played = []
    _g.game.close_rtp_hidden()
    _g.pump()
    _g2 = _rtp_fixture(1.20)                          # 本来就是常驻档
    _g2.game.close_rtp_hidden(silent=True)
    _g2.pump()
    check(sorted(_g.game.rtp_btns) == [0.80, 1.20, 2.00, 3.60]
          and abs(_g.game.rtp_target - 3.60) < 1e-9
          and len(_g._rtp_row.children) == 5          # 1 spacer + 4 常驻
          and _g2.game.rtp_target == 1.20,
          "关闭隐藏档: ① 按钮全部摘掉(那排回到 4 个常驻) ② 档位回到最高常驻档 "
          "③ 布局 children 同步 ④ **本来就是常驻档时什么都不做**(不跳 360%)",
          "关闭后 %s / %.2f / children=%d ; 常驻档关闭后仍 %.2f"
          % (sorted(_g.game.rtp_btns), _g.game.rtp_target, len(_g._rtp_row.children),
             _g2.game.rtp_target))
    check(_only_one, "切到第二个隐藏档时**只留当前一个**(开过 1000% 又选 2000% 不该挂两个按钮)",
          "换档后 %s" % sorted(_g.game.rtp_btns))

    # 19e-2 结构重映射**那座桥**自己也要被钉住(2026-09-19 补, **老版没有这一条**)
    # ⚠️⚠️ 老版不存在"桥": `RootWidget._unlock_rtp` 里状态与控件同体, 端到端跑到就是覆盖。
    #    新版劈成 `Game`(发 `rtp_btn_add`/`rtp_btn_remove` 事件) + `PlayMixin`(控件),
    #    中间靠 `_dispatch_one` 那两条分支搬。而上面 19e/19f 那几条**都不认那座桥** ——
    #    实测: 把 `_dispatch_one` 里两条 rtp 分支摘掉, 19e 照样全绿(因为 `children==5` 在
    #    "从来没挂上去"和"挂上去又摘掉"两种情况下同值), 只有 19f 的语音会红。
    #    ⇒ 这一条**新增**(`golden/fx_gates.txt` 那 380 条是老版的清单, 新建构多出来的桥得自己钉)。
    #    两半都钉: ① 出货 `_dispatch_one` 里确实有这两条分支(静态); ② 事件真把控件搬过去了(行为)。
    _d1 = _fn_body(play_src, "_dispatch_one")
    check('elif kind == "rtp_btn_add"' in _d1
          and 'self._add_rtp_button(ev.data["label"], ev.data["val"])' in _d1
          and 'elif kind == "rtp_btn_remove"' in _d1
          and 'self._remove_rtp_button(ev.data["val"], ev.data.get("btn"))' in _d1
          and _n_open == 6,                       # 1 spacer + 4 常驻 + 1 隐藏(真挂上去了)
          "隐藏档那座桥必须通: `PlayMixin._dispatch_one` 把 `rtp_btn_add`/`rtp_btn_remove` "
          "两条事件真接到 `_add_rtp_button`/`_remove_rtp_button` 上, 且事件真的搬动了控件"
          "(摘掉分支 = 隐藏档按钮永不出现/不消失, 而 UI 一声不吭)",
          "分支齐全=%s ; 隐藏档开着时那排 children=%d(期望 6)"
          % ('elif kind == "rtp_btn_add"' in _d1 and 'elif kind == "rtp_btn_remove"' in _d1,
             _n_open))

    # 19f 语音: 只播新的那条, 不许和"切回 360%"撞车
    check(_g.sfx.played == ["click", "voice_rtp_hide_2000"],
          "关闭隐藏档只播**一条**语音 voice_rtp_hide_2000, 且没有 voice_rtp_360"
          "(两条同走普通路径时 360 那句会抢掉 hide 那句(v0.8.65 起「打断旧的、念新的」), "
          "所以 set_rtp 那边必须 silent=True)",
          "%s ; 常驻档关闭时 = %s" % (_g.sfx.played, _g2.sfx.played))

    # 19h 非 ready 时**必须完全切不动档**(2026-09-18, 玩家报的「切档只切一半」)
    #     ⚠️ 病根: `set_rtp` 里的盘面交换被 `if self.state == "ready"` 挡着, 而入口在
    #        非 ready 够得着 ⇒ 高亮跳到新档、9 个倍率槽还是旧档的, 这一发按**旧档**赔付。
    #     ⚠️ 判据必须"**档位与盘面都不许变**"两条一起。
    _nr = _rtp_fixture(0.80)
    _nr.game.state = "flying"                # 夹具建的是 ready 夹具, 这里只改状态
    _nr.game.rtp_target = 0.80
    _nr.game.multipliers = [7] * CFG.NUM_SLOTS   # 打个记号, 好看出盘面有没有被动过
    _nr.game.set_rtp(3.60, silent=True)
    check(_nr.game.rtp_target == 0.80
          and list(_nr.game.multipliers) == [7] * CFG.NUM_SLOTS,
          "非 ready 时 set_rtp **完全不动**(档位高亮与盘面**两个都不许变**)",
          "调完 rtp_target=%.2f 盘面首格=%s (期望 0.80 / 7)"
          % (_nr.game.rtp_target, _nr.game.multipliers[0]))
    #     阴性对照: 同一夹具切成 ready, 同一个调用**必须生效** —— 否则上一条是恒真的
    _nr.game.state = "ready"
    _nr.game.set_rtp(3.60, silent=True)
    check(abs(_nr.game.rtp_target - 3.60) < 1e-9
          and list(_nr.game.multipliers) != [7] * CFG.NUM_SLOTS,
          "【阴性对照】ready 时 set_rtp **照常生效**(证明上一条有分辨力)",
          "rtp_target=%.2f 盘面首格=%s" % (_nr.game.rtp_target, _nr.game.multipliers[0]))
    #     非 ready 时长按"期望返还比例"**不该弹窗** —— 弹了也改不动, 是个死对话框
    _calls_nr = []
    _nr2 = _rtp_fixture(0.80)
    _nr2.game.state = "flying"
    _nr2.game._on_rtp_popup = lambda *a, **kw: _calls_nr.append(1)
    _nr2.game.ask_unlock_rtp()
    check(not _calls_nr and _nr2.game._rtp_popup is None,
          "非 ready 时长按「期望返还比例」**不弹窗**(弹窗是 Window 子控件, 输入锁拦不住它)",
          "建了 %d 个弹窗" % len(_calls_nr))

    # 19g 语音清单一致(双向) —— 防"代码里播了一个不存在的 wav"
    # ⚠️ 路径重映射: 语音生成器(`tools/generate_voice.py`)还没搬进 new_danzhu(它仍是
    #    `voice/*.wav` 的生成者, 在老工程的 tools/ 下); wav 目录已搬进本工程的 `voice/`。
    try:
        sys.path.insert(0, os.path.join(OLDROOT, "tools"))
        import generate_voice as _gv
        _keys = set(_gv.PHRASES)
        _wavs = set(os.path.splitext(f)[0] for f in
                    os.listdir(os.path.join(ROOT, "voice"))
                    if f.endswith(".wav"))
        # ⚠️ 排掉以 `_` 结尾的: `"voice_rtp_hide_%d"` 这种**模板**会被正则截成前缀。
        _lit = set(n for n in re.findall(r'"(voice_[a-z0-9_]+)"', SHIPPED)
                   if not n.endswith("_"))
        _tmpl = set(re.findall(r'"(voice_[a-z0-9_]+_)%d"', SHIPPED))
        check(_keys == _wavs and _lit <= _keys
              and all(any(k.startswith(t) for k in _keys) for t in _tmpl),
              "语音清单三方一致: generate_voice.PHRASES == voice/*.wav == "
              "出货文件里播的字面名(双向; 少一个 wav = 真机上那条播不出来); "
              "模板名(如 voice_rtp_hide_%d)另有前缀判据",
              "PHRASES %d / wav %d / 差异 %s / 模板 %s"
              % (len(_keys), len(_wavs), sorted(_keys ^ _wavs)[:4], sorted(_tmpl)))
    except Exception as _e:
        check(False, "语音清单可读(generate_voice.PHRASES 与 voice/ 目录)", repr(_e))

    # 19h 新方法必须长在正确的类上(掉进隔壁 class 能编译、--check 也绿)
    # ⚠️ 老版这一组**全在 `RootWidget` 上**; 新版按"状态 / 控件"分家 —— 判据不变,
    #    只是每个名字的**归属类**跟着架构换了。
    _rtp_methods_ui = ("_ask_unlock_rtp", "_remove_rtp_button")
    _rtp_methods_state = ("close_rtp_hidden", "rtp_is_hidden", "_regular_rtp")
    _miss = ([n for n in _rtp_methods_ui if not hasattr(RootWidget, n)]
             + [n for n in _rtp_methods_state if not hasattr(Game, n)])
    _leak = ([n for n in _rtp_methods_ui + _rtp_methods_state if hasattr(LoadVeil, n)])
    check(not _miss and not _leak,
          "隐藏档那套方法都长在 RootWidget 上(生成物里方法边界肉眼看不出来, 掉错类"
          "**能编译**、--check 也全绿, 只有 hasattr 问得出'它长在哪')",
          "缺 %s ; 漏进 _LoadVeil %s" % (_miss, _leak))

    # ---- 20. 装杯退场改成"等玩家点击" ----
    # 玩家 2026-09-12 定稿: 装满后**不再自动消失**, 等玩家点屏幕任意位置才退场; 但
    # "最后一颗球落定 + 0.6s"之前点击无效。**跑分期间例外** —— 那一路仍到点自己走。
    print("\n[20] 装杯退场: 等玩家点击")

    class _FT3(object):
        t = 3.0e6

        def time(self):
            return self.t

    _real3 = WFX.time
    _f3 = _FT3()
    WFX.time = _f3
    try:
        def _fx20(auto=False):
            w = WinPileFX.__new__(WinPileFX)
            WinPileFX.__init__(w, None)
            w._redraw = lambda *a, **k: None
            w._rng = random.Random(999)
            _f3.t = 3.0e6
            w.play_win(10, 10, auto_close=auto)
            return w

        def _to_result(w, cap=20.0):
            """推进到"最后一颗球落定"那一刻(装杯装满)。返回是否走到了。"""
            for _ in range(int(cap * 60)):
                if w.mode == "result":
                    return True
                w.tick()
                _f3.t += 1.0 / 60.0
            return False

        def _run(w, secs):
            """推进 secs 秒(60fps)。返回**是否在中途**回到过 idle。"""
            for _ in range(int(secs * 60)):
                w.tick()
                _f3.t += 1.0 / 60.0
                if w.mode == "idle":
                    return True
            return False

        # 20a 装满后**不自动退场**
        _a = _fx20()
        _run(_a, 12.0)          # 12s: 既超过旧的"0.6 停留 + 0.25 淡出", 也超过 FX_MAX_SEC(9s)
        check(_a.mode == "result" and _a._closing_at == 0.0 and _a.busy(),
              "装满后**不自动退场**: 干等 12 秒(既超过旧实现的 0.85s, 也超过 FX_MAX_SEC=9s), "
              "mode 仍是 result、_closing_at 仍是 0、busy() 仍锁着 —— 画面停在装满状态等玩家"
              "点击。改回「到点自动 idle」/ 给 result 段加时间兜底, 这里立刻红",
              "12s 后 mode=%s _closing_at=%.1f busy=%s"
              % (_a.mode, _a._closing_at, _a.busy()))

        # 20b 最短停留: `_settled_at + HOLD_BASE` 之前点击无效
        _b = _fx20()
        _ok_b = _to_result(_b)
        _t_settled = _b._settled_at
        _f3.t = _t_settled + WFX.HOLD_BASE - 0.05
        _early = _b.request_close()
        _f3.t = _t_settled + WFX.HOLD_BASE + 0.01
        _late = _b.request_close()
        check(_ok_b and (not _early) and _late,
              "最短停留: 最后一颗球落定 + %.2fs **之前**点击无效, 过了才受理"
              "(球还在往下落时 mode != result, 同样不受理 —— 那条由 20a 的 _to_result 覆盖)"
              % WFX.HOLD_BASE,
              "早 0.05s -> %s ; 过线 -> %s" % (_early, _late))

        # 20c 点击后走现成的淡出曲线, RESULT_FADE 内回到 idle
        _c = _fx20()
        _to_result(_c)
        _f3.t = _c._settled_at + WFX.HOLD_BASE + 0.01
        _c.request_close()
        _mid = _run(_c, WFX.RESULT_FADE * 0.5)
        _done = _run(_c, WFX.RESULT_FADE * 0.5 + 3.0 / 60)   # +3 帧: 抵掉浮点累加的尾差
        check((not _mid) and _done and _c.mode == "idle" and not _c._balls,
              "点击之后走**现成的** %.2fs 淡出曲线(不是瞬间消失): 半程时还没回到 idle, "
              "过完才 idle 且球清空" % WFX.RESULT_FADE,
              "半程就 idle=%s ; 走完 mode=%s" % (_mid, _c.mode))

        # 20d 跑分例外: auto_close 时不点击也会自己走
        _d = _fx20(auto=True)
        _to_result(_d)                                   # 先等它装满(否则还在下球阶段)
        _ran = _run(_d, WFX.HOLD_BASE + WFX.RESULT_FADE + 0.5)
        check(_ran and _d.mode == "idle",
              "**跑分例外**: auto_close=True 时不点击也到点自己走(跑分是自动连续发射的, "
              "等人点击会把整轮跑分卡死)",
              "auto_close 装满后跑 %.2fs -> mode=%s" % (WFX.HOLD_BASE + WFX.RESULT_FADE, _d.mode))
    finally:
        WFX.time = _real3

    # ---- 22. 受击压扁: 压扁轴沿碰撞法线, 自转不丢 ----
    # 修法: quad 转到法线方向(压扁轴 = 法线), 自转交给 tex_coords, 两者解耦。
    #   ⚠️ 这条只覆盖**辅助函数的数学**(角度可逆 + 保长), **覆盖不到"接线"** —— 要验证
    #      "渲染里确实把法线算进去了"得建真实 GameArea, 成本不划算。**接线目前靠目视**
    #      (smoke 截图里球撞钉那一帧), 已在文档里如实记明。
    print("\n[22] 受击压扁的方向解耦")
    # ⚠️ 这一段测的是"压扁轴与自转解耦"的产物(`_tex_coords_rot`)。当前出货基线没有这个
    #    函数, 所以用 hasattr 守卫跳过 —— **不删**, 哪天真把那次改动做回来, 这段自动恢复生效。
    if not hasattr(WFX, "_tex_coords_rot"):
        print("  [SKIP] 当前基线没有 _tex_coords_rot(9/13 的解耦改动未在出货版里), 本段跳过")
    else:
        bad = []
        for a_deg in (-170.0, -90.0, -35.0, 0.0, 47.0, 140.0):
            a = math.radians(a_deg)
            tc = WFX._tex_coords_rot(a)
            if len(tc) != 8:
                bad.append("len=%d@%.0f" % (len(tc), a_deg))
                continue
            # 反解: 第一角原本在 (-0.5,-0.5)(即 225°), 纯旋转后应落在 225°+a
            du, dv = tc[0] - 0.5, tc[1] - 0.5
            back = (math.degrees(math.atan2(dv, du)) - 225.0 + 180.0) % 360.0 - 180.0
            if abs(((back - a_deg) + 180.0) % 360.0 - 180.0) > 0.5:
                bad.append("%.0f°->%.1f°" % (a_deg, back))
            for i in range(0, 8, 2):        # 正交旋转: 四角到中心的距离必须不变
                if abs(math.hypot(tc[i] - 0.5, tc[i + 1] - 0.5) - math.sqrt(0.5)) > 1e-9:
                    bad.append("非正交@%.0f" % a_deg)
                    break
        check(not bad, "_tex_coords_rot 是绕中心的纯旋转(角度可逆 + 保长)",
              "异常: %s" % (bad or "无"))
        tc0 = [round(v, 9) for v in WFX._tex_coords_rot(0.0)]
        check(tc0 == [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
              "_tex_coords_rot(0) 等于 Rectangle 的默认 tex_coords(零旋转不改变外观)",
              str(tc0))

    # ---- 21. 换盘面熄灭投中指示灯 ----
    # 玩家报「进入倍率槽的时候, 有一个红点和绿点代表有没有命中, 这个点应该在结算之后就消失,
    # 实际并没有消失」。病根不是"灭灯的代码写错了", 而是 `park_ball(reroll=True)` 从整块
    # `_redraw()` 换成增量 `_update_slots()` 之后, **原先顺手灭灯的那层遮盖没了**。
    print("\n[21] 换盘面熄灭投中指示灯")

    # ⚠️ 找之前**必须剥掉整行注释**: 把 `self.lamps_off()` 改成 `# self.lamps_off()` 时,
    #    字符串仍留在函数体里, 不剥注释这条断言照样报绿(假绿灯)。
    # ⚠️ 锚点重映射: `_update_slots` 在新版住 `danzhu/ui/game_area.py`(老版 main.py 同址);
    #    `park_ball` 搬进了 `danzhu/game.py`。
    _m_us = re.search(r"def _update_slots\(self\):(.*?)\n    def ", SRC["game_area"], re.S)
    _us_body = _code_only(_m_us.group(1) if _m_us else "")
    check("lamps_off()" in _us_body,
          "_update_slots 里调 lamps_off(增量重掷 = 整块重画, 少了它灯就永不熄灭)")
    _m_pb = re.search(r"def park_ball\(self.*?\n    def ", game_src, re.S)
    _pb_body = _code_only(_m_pb.group(0) if _m_pb else "")
    # 锚点重映射: 新版 `park_ball` 发的是 `update_slots` **事件**, 真正调
    # `GameArea._update_slots()` 的是 `_dispatch` —— 判据("走增量、不整块重画")没变。
    check('_emit("update_slots")' in _pb_body,
          "park_ball(reroll=True) 走的是增量 _update_slots(整块 _redraw 已被 0.6.91 换成它)")

    # 行为: 把**真的** GameArea 方法绑到一个最小宿主上(不复制方法体、不复制 GUI 循环)。
    _OFF = tuple(CFG.hex_rgb(CFG.COL_LAMP_OFF))

    class _SlotHost(object):
        def __init__(self, multipliers):
            self.game = types.SimpleNamespace(multipliers=list(multipliers))
            # 新版 `GameArea` 把无头状态机存在 `self.model` 上(老版是 `self.game`) ——
            # 夹具两个名字都给, 免得 `_update_slots` 里那句 `g = self.model` AttributeError。
            self.model = self.game
            self._s = 1.0
            self._ox = 0.0
            self._oyt = 0.0
            self._pulse = ("sentinel", 0.0)
            self._slot_cols = [Color(*CFG.hex_rgb(CFG.COL_CANVAS) + (1,)) for _ in range(CFG.NUM_SLOTS)]
            self._slot_txt_cols = [Color(*CFG.hex_rgb(CFG.COL_CANVAS) + (1,)) for _ in range(CFG.NUM_SLOTS)]
            self._slot_txt_rects = [Rectangle(pos=(0, 0), size=(0, 0)) for _ in range(CFG.NUM_SLOTS)]
            self._lamp_cols = [Color(*CFG.hex_rgb(CFG.COL_LAMP_OFF) + (1,)) for _ in range(CFG.NUM_SLOTS)]

    for _n in ("_update_slots", "lamps_off", "_px", "_py"):
        setattr(_SlotHost, _n, GameArea.__dict__[_n])

    host = _SlotHost([0, 2, 0, 5, 0, 10, 0, 3, 0])
    # 模拟两局结算后残留: 第 3 格绿(中) / 第 0 格红(未中)
    GameArea.set_lamp(host, 3, CFG.COL_GREEN)
    GameArea.set_lamp(host, 0, CFG.COL_FIRE)
    _lit = sum(1 for c in host._lamp_cols if tuple(c.rgb) != _OFF)
    check(_lit == 2, "夹具本身就位: 结算两局后恰好 2 格灯亮着", "亮 %d 格" % _lit)

    host.game.multipliers = [4, 0, 0, 0, 20, 0, 0, 0, 2]
    host._update_slots()
    _lit2 = sum(1 for c in host._lamp_cols if tuple(c.rgb) != _OFF)
    check(_lit2 == 0, "换盘面后全部指示灯熄灭(玩家报的『点了不消失』)",
          "残留 %d 格亮" % _lit2)

    # 防"为了灭灯把增量更新弄坏": 槽色必须真的跟着新盘面变, 白闪作废也必须还在。
    _want = CFG.hex_rgb(CFG.slot_color(host.game.multipliers[0]))
    _got = tuple(host._slot_cols[0].rgb)
    check(max(abs(_got[i] - _want[i]) for i in range(3)) < 1e-6,
          "灭灯的同时槽色照常更新(没有为了灭灯牺牲增量语义)",
          "%s vs %s" % (tuple(round(v, 3) for v in _got), tuple(round(v, 3) for v in _want)))
    check(host._pulse is None, "重掷后槽位白闪照常作废(_pulse 仍被清)")

    # ---- 23. 成绩页的慢帧归因必须**按各阶段自己的总帧数归一化** ----
    # 玩家报"其他 AI 说这统计没用"。查下来那行 `1% Low 定位：最慢 56 帧平均 11.9 毫秒
    # （装杯 37帧 · 飞行 9帧 · 待机 5帧）` 确实**读不出结论** —— 分母是共用的 56。
    # 正确形状是**条件概率**: P(该帧进最慢 1% | 该阶段) = 该阶段进 1% 的帧数 / 该阶段总帧数。
    print("\n[23] 成绩页慢帧归因: 必须按各阶段总帧数归一化")

    # 接线: 分母必须取自 `groups`(全部帧按阶段分组), 不能拿 `low1_n` 当分母。
    _m_ls = re.search(r"def _bench_low_summary_text\(self\):(.*?)\n    def ", bench_src, re.S)
    _ls_body = _code_only(_m_ls.group(1) if _m_ls else "")
    check('d.get("groups")' in _ls_body,
          "成绩页取用了 groups 当分母(不取 = 又退回只印帧数, 那个数读不出结论)")
    check("_tot" in _ls_body and "/ _tot" in _ls_body,
          "比例 = 该阶段进 1% 的帧数 / 该阶段总帧数(分母是 _tot, 不是 low1_n)")

    # 行为: 把**真的** RootWidget 方法绑到最小宿主上(不复制方法体)。
    class _BenchHost(object):
        def __init__(self, diag):
            self._bench_diag = diag

    _BenchHost._bench_low_summary_text = BenchMixin_impl = BENCH.BenchMixin.__dict__["_bench_low_summary_text"]

    # ⚠️ 夹具刻意让"帧数排序"与"比例排序"**分岔**: 装杯帧数最多(37)但比例不是最高,
    #    待机帧数最少(5)却因为分母小反而比例最高。只按帧数排的实现会被这条抓住。
    _diag = {
        "n": 5602, "low1_n": 56, "low1_ms": 11.9, "over60_n": 11,
        "low1_groups": [("装杯", 37), ("飞行", 9), ("待机", 5)],
        "jank_n": 51, "jank_groups": [("装杯", 37), ("飞行", 9), ("待机", 5)],
        "groups": {"装杯": (1240, 18.0), "飞行": (2680, 6.0), "待机": (120, 6.2)},
        "worst": [[27.0, "飞行", 20.5, 20.5, 0.2, 0, (), 12]],
        "texupd": 178, "texupd_by": [("其他文字", 112)],
    }
    _txt = _BenchHost(dict(_diag))._bench_low_summary_text()
    _rate_line = ""
    for _ln in _txt.split("\n"):
        if "卡顿帧分布" in _ln:
            _rate_line = _ln
            break
    check(bool(_rate_line), "成绩页印出了「卡顿帧分布」那一行")

    # 分子分母都要原样印出来 —— 分母小的阶段会出现 100% 这种噪声读数。
    check("37/1240" in _rate_line and "9/2680" in _rate_line and "5/120" in _rate_line,
          "分子与分母都原样印出(只印百分比 = 把样本量这条信息丢掉)",
          _rate_line[:70])

    _order = (_rate_line.find("待机") < _rate_line.find("装杯") < _rate_line.find("飞行"))
    check(_order,
          "按**比例**降序排(待机 4.2% > 装杯 3.0% > 飞行 0.3%) —— 第一个就是最该查的阶段",
          "若按帧数排会是 装杯>飞行>待机")

    # 退化路径: 老 diag 字典里没有 groups(历史记录/异常兜底)时, 不许崩、不许整块消失。
    _legacy = {k: v for k, v in _diag.items() if k != "groups"}
    _txt2 = _BenchHost(_legacy)._bench_low_summary_text()
    check("装杯" in _txt2 and "卡顿帧分布" in _txt2,
          "缺 groups 时退化成只报帧数, 整块不消失(历史 diag 字典没有这个键)")
    check(_BenchHost({})._bench_low_summary_text() == "",
          "空 diag 仍返回空串(与原行为一致, 不让诊断把成绩挤没)")

    # ---- 24. 帧率曲线: 阶段条 + 逐帧日志复制 ----
    # 玩家 2026-09-14 的主意: "每 1 帧 1 个点, 你再额外记录一个东西 —— 这 1 帧游戏在做什么"。
    # 数据**本来就在记**(`_on_flip` 往 `_bench_frames` 里存的第 2 个字段就是 `_bench_tag()`)。
    #   ① 曲线真的拿到了标签, 且长度对不上时**退化而不是崩**;
    #   ② 复制出来的逐帧日志**头部自带各阶段分母与慢帧率**。
    print("\n[24] 帧率曲线: 阶段条 + 逐帧日志复制")

    _m_fc = re.search(r"def _show_fps_curve\(self\):(.*?)\n    def ", bench_src, re.S)
    _fc_body = _code_only(_m_fc.group(1) if _m_fc else "")
    check("tags=tags" in _fc_body and "FpsCurve(" in _fc_body,
          "曲线弹窗把逐帧阶段标签传给了 FpsCurve(不传 = 整个阶段条消失)")
    _m_flip = re.search(r"def _on_flip\(self.*?\n    def ", bench_src, re.S)
    check("_bench_tag()" in _code_only(_m_flip.group(0) if _m_flip else ""),
          "_on_flip 每帧把场景标签记进 _bench_frames(阶段条与日志的唯一数据源)")
    check("STAGE_ORDER" in bench_src and "_bench_tag" in bench_src,
          "STAGE_ORDER 与 _bench_tag 都在(名字对不上会退化成灰色, 见常量处注释)")

    # 行为①: FpsCurve 拿对标签 / 拿错长度时退化
    _c_ok = FpsCurve([6.1, 6.2, 30.0], tags=["飞行", "飞行", "装杯"], cap_fps=165)
    check(len(_c_ok._tags) == 3, "标签长度对得上时 FpsCurve 收下它")
    _c_bad = FpsCurve([6.1, 6.2, 30.0], tags=["飞行"], cap_fps=165)
    check(_c_bad._tags == [], "长度对不上时退化成不画阶段条(曲线照画, 绝不崩)")
    _c_none = FpsCurve([6.1, 6.2], cap_fps=165)
    check(_c_none._tags == [], "压根没标签时也退化(老记录路径)")

    # 行为②: 逐帧日志的头部与正文
    class _LogHost(object):
        def __init__(self, gaps, tags, tex=None, frames=None):
            self._render_gaps_ms = list(gaps)
            self._bench_frames = (list(frames) if frames is not None
                                  else list(zip(gaps, tags)))
            self._bench_tex = list(tex) if tex is not None else []
            self._render_fps = 1000.0 / (sum(gaps) / len(gaps))
            self._render_median_fps = 1000.0 / sorted(gaps)[len(gaps) // 2]
            self._render_1low = 1000.0 / sorted(gaps)[-1]
            self._bench_diag = {"texupd": 7, "texupd_by": [("状态栏", 7)],
                                "snd_n": 11, "snd_worst": 3.2, "snd_sum": 9.0,
                                "vib_n": 4, "vib_worst": 12.4, "vib_sum": 21.0,
                                # 节拍真值那一行要用的几个(见 `_bench_collect_diag` 的"节拍真值")
                                "screen_hz": 120.0, "req_hz": 165.0, "clock_maxfps": 165.0,
                                "maxfps": "165", "vsync": "1", "multisamples": "2",
                                # 「已采未印」那几格 —— ⚠️ 单位别搞错: `gc_total`/`gc_worst` 采集端
                                # 存的是**秒**(perf_counter 差), 打印端自己 ×1000; 其余都已经是毫秒。
                                "gc_n": 5, "gc_total": 0.012, "gc_worst": 0.008,
                                "gc_worst_gen": 1, "backend": "SoundPool",
                                "utime_ms": 900.0, "stime_ms": 100.0, "cpu_per_frame": 1.86,
                                "jni_n": 11, "jni_main_sum": 1.2, "jni_main_worst": 0.4,
                                "jni_bg_sum": 18.0, "jni_bg_worst": 6.0,
                                "ui_n": 3, "ui_worst": 1.0, "jni_err": 0,
                                "w20_pos": [0, 3, 8, 12], "w20_near": 2,
                                "cfg_n": 0, "cfg_sum": 0.0, "cfg_worst": 0.0}
        def _device_info(self):
            return "TESTDEV / Android 99"

    for _n in ("_bench_frame_log", "_copy_bench_log", "_bench_save_log"):
        setattr(_LogHost, _n, BENCH.BenchMixin.__dict__[_n])

    # 造一段"装杯明显更慢"的采样: 60 帧飞行(6ms) + 40 帧装杯(其中 20 帧 30ms)
    _g = [6.0] * 60 + [6.0] * 20 + [30.0] * 20
    _t = ["飞行"] * 60 + ["装杯"] * 40
    # 慢帧(那 20 帧 30ms)当帧都在重建文字, 其余帧一律不重建 —— 交叉表应当直接读出来
    _x = [0] * 80 + [1] * 20
    _log = _LogHost(_g, _t, _x)._bench_frame_log()
    # ⚠️ 列头是「长停顿率」**不是**「慢帧率」(2026-09-14 改名): 这张表的门槛是
    #    "≥2 倍中位帧时间", 而「慢帧」这个词已经被成绩面板占成「中位帧率 <75%」那一档了。
    check("各阶段" in _log and "长停顿率" in _log,
          "日志头部带各阶段统计表(列头叫「长停顿率」, 与面板的「慢帧」区分开)")
    check("慢帧当帧发生文字重建: 20/20 (100%)" in _log and "其余帧: 0/80 (0%)" in _log,
          "头部给出『慢帧 × 文字重建』交叉表 —— 判断文字重排是不是元凶的直接读数",
          " ".join(l for l in _log.split("\n") if "文字重建:" in l))
    check("全程文字重建 20 次" in _log,
          "头部报全程重建次数与落在慢帧上的次数")
    # ---- 慢帧相邻间隔(老版交接文档 `android/jiaojie.md` 方案六点名的那项诊断) ----
    # 本夹具的 20 个慢帧是**连成一片**的(下标 80~99) ⇒ 19 个帧号差**全是 1** ⇒
    # 它判的必须是「一次长停顿」, **不是**「固定节拍」。
    # ⚠️ 第一版判据只看「最大 ≤ 中位×1.5」, 当场把这个夹具判成"固定节拍" ——
    #    而连片和节拍要查的东西完全相反, 所以「连片」必须单独一条分支。
    _gapl = " ".join(l for l in _log.split("\n") if "相邻间隔" in l)
    check("慢帧相邻间隔" in _log and "连片 19/19" in _log,
          "头部给出慢帧的**相邻间隔**(帧号差)—— 判「是不是系统固定节拍」的唯一读数; "
          "本夹具 20 个慢帧连成一片 ⇒ 必须报连片", _gapl)
    check("一次" in _gapl and "固定节拍" not in _gapl,
          "连片的慢帧判成「**一次**长停顿」而**不是**「固定节拍」—— 两者要查的东西相反"
          "(一个是「那一段在干什么」, 一个是「哪个系统节拍」)", _gapl)
    check("发声" in _log and "震动" in _log,
          "头部带发声/震动单次最慢 —— 覆盖另一条假设(关音效会连震动一起关, 两条分不开)")

    # ---- 「差额律」三列(2026-09-20) ----
    # 病根: 真机 14 条慢帧里 **8 条** `dt − 主线程 − 等屏幕` 差出稳定的 4.1~5.5ms, 而这笔账
    # **从来没进过仪表** —— 上一轮是拿纸笔在手算出来的。手算结论: 差_i + 上一帧body ≈ 常数,
    # 而 Kivy `clock.py::_check_ready` 的睡眠律给出这个常数 = `(11/15)/cap`。
    # ⇒ 这三列存在的意义就是**把那条律变成每轮跑分自动判的**; 判据行必须**自带 T 的算法**,
    #    否则拿到日志的人还是得回去翻 Kivy 源码才知道"6.111 是哪来的"。
    _gcl = " ".join(l for l in _log.split("\n") if "差额律" in l or "别的线程" in l)
    check("差额律" in _log and "(11/15)/cap" in _log,
          "头部印「差额律」判据行, 且**自带 T 的算法** —— Kivy 睡眠常数 `T=(11/15)/cap` "
          "与实测「差_i + 上一帧body」并列", _gcl)
    check("别的线程烧的 CPU" in _log,
          "头部印「别的线程烧的 CPU」= 全进程 − 本线程 —— 它是 **GIL 争用**那条独立假说的"
          "判据(≈0 ⇒ 那条假说当场出局, 不用再猜)", _gcl)
    check("上一帧body_ms" in _log and "别的线程CPUms" in _log,
          "逐帧表头把三列都写上(差 / 上一帧body / 别的线程CPU)—— "
          "表头不写列名, 拿到日志的人只能靠数列数")
    check("%.2f,%.2f,%.2f" in bench_src and bench_src.index("上一帧body_ms")
          > bench_src.index("等屏幕ms"),
          "三列**只加在末尾**(格式串落在 `等屏幕ms` 之后)—— 前面几列被 `_bench_frames` "
          "下标与外部脚本按号取, 插在中间会让旧解析器**静默错位**")
    check("_gap_ms" in bench_src and "_prev_body" in bench_src and "_othr" in bench_src,
          "三列都是**已有采样的算术**(逐帧 0 新开销): 差 = dt−主线程−等屏幕 / "
          "上一帧body / 别的线程CPU = 进程−线程")

    # ---- ★1%Low 的可重复性(块自举) —— 2026-09-20 ----
    # 病根: `1%Low = 最慢 1% 帧间隔的算术平均`, 而 1900 帧的窗口里 1% 桶**只有 19 帧**
    # ⇒ **单帧离群就能支配它**。模拟器实测(`temp/_emu_analyze.py`, 1901 帧):
    #     剔掉最慢 **1** 帧(0.05%) ⇒ 1%Low **24.0 → 51.0**, 而平均帧只动 0.8;
    #     5 个子窗口各自的 1%Low = 50.1 / 51.8 / 51.1 / **6.2** / 54.2(极差 200%)
    # ⇒ **只报一个点值是误导**。这一组钉的是"区间必须跟着一起报"。
    check("块自举" in _log and "90% 区间" in _log,
          "头部印 1%Low 的**块自举 90% 区间** —— 单点值不可比, 必须同时报区间", _gcl)
    # ⚠️ 长停顿帧数**必须跟分数印在同一行**: 分开印的话读的人只会看分数。
    #    模拟器实测: 两轮同一份 APK, 平均帧只差 1.2%, 而 1%Low 差 2.16 倍(24.0 vs 51.9)
    #    —— 差别全在"这一轮有没有出现那一帧离群"。所以这个数不是装饰, 是**读分数的前提**。
    check("本轮长停顿" in _log and "单点值不可比" in _log,
          "**长停顿帧数印在 1%Low 同一行** —— 没有它, 分不清「真的变好了」和"
          "「这一轮运气好没撞上离群帧」", _gcl)
    check("子窗口" in _log and "内部就不稳" in _log,
          "头部印**子窗口四等分**各自的 1%Low —— 一轮**内部**稳不稳, 一眼看得出来", _gcl)
    check("A/B" in _log and "3 轮" in _log,
          "处方写进日志本身: **A/B 一律 >=3 轮取中位**(否则读的人还是会拿单轮涨跌当结论)", _gcl)
    check("Random(20260920)" in bench_src,
          "块自举用**固定种子** —— 不固定的话它自己又变成一个噪声源, 同一份日志两次算出不同区间")
    check("len(gaps) >= 100" in bench_src,
          "块自举的门槛是 100 帧(夹具正好 100) —— 门槛设高了这段在门禁里**永远不跑**, "
          "那就是一条空转的闸")

    # ---- 「保存」先问「最近一次 / 现有记录」(玩家 2026-09-20 定) ----
    # 玩家原话:「保存的时候需要问我：保存最近一次，还是所有」；
    # 随后**更正**：「因为清空的时候会清 log，所以保存的也不是历史所有，而是**能保存的所有**」。
    check("save.bind(on_release=_ask_scope)" in bench_src,
          "「保存日志(txt)」绑的是**选择框**而不是直接保存")
    check("_ok, _msg = self._bench_save_log()" in bench_src,
          "⚠️ 「只存最近一次」那条路**逐字不变**(不传 `text`/`prefix`) —— 零回归")
    check('prefix="plinko_fps_all"' in bench_src,
          "「连现有记录一起」走**独立前缀** —— 两份文件不会互相覆盖")
    check("_b_all.text = '连现有记录一起（没有）'" in bench_src
          and "_b_all.background_color = hex_rgb(COL_BTN_OFF)" in bench_src,
          "历史为空时「连现有记录一起」**灰底 + 标明原因**, 而不是禁用 —— "
          "⚠️ 出货文件里**一处 `.disabled =` 都不许有**(本文件上面那条老闸钉着; "
          "老版「输入锁改走触摸层」就是因为它)")
    check('if _k != "diag"' in bench_src,
          "历史那段 JSON **去掉 diag** —— 1~3KB/条 × 100 条 = 上百 KB, "
          "而它要说的东西已经提到人读表里了")
    check("能保存的所有" in bench_src,
          "文案写的是「**能保存的所有**」不是「历史全部」—— 老记录会被「清空历史」删掉")

    # `_frame` 内部的子步骤 —— 回答"这 20 毫秒里我们自己的代码占多少"。
    _fr_ok = [((g_, t_) + (0.0,) * 7 + ((((1.3, "发射"), (0.2, "板面")),) if g_ > 12 else ()))
              for g_, t_ in zip(_g, _t)]
    _log_fr = _LogHost(_g, _t, _x, frames=_fr_ok)._bench_frame_log()
    check("最慢三帧的子步骤" in _log_fr and "发射1.3" in _log_fr,
          "头部印出最慢三帧的 _frame 子步骤(发射那一刻的子步骤原来完全没有计时)",
          " ".join(l for l in _log_fr.split("\n") if "子步骤" in l))
    # ⚠️ 判据已从"源码字符串在不在"改成**运行期**: `import danzhu.ui.app` 会执行它的模块级
    #    `_brk_install()`, 之后看方法是不是**真的被包了**(`_brk_wrap` 的包装器 qualname 是
    #    `_brk_wrap.<locals>.f`)。旧写法有两个洞: ① 注释里出现同样字样就能喂绿它(真踩过 ——
    #    一条解释这个弱点的注释把另一条检查喂绿了); ② 老版 8 处 `_brk_wrap` 是模块级调用,
    #    新版集中进 `_brk_install()` 后**只要函数定义了却没被调用**, 字符串照样在、检查照样绿,
    #    而那 8 格永远是 0(2026-09-19 实测确认过这个状态)。运行期判据注释骗不过。
    import danzhu.ui.app as _appmod      # noqa: F401  —— 模块级 `_brk_install()` 在这一句里执行
    check("_brk_wrap" in getattr(GAME.Game.launch, "__qualname__", ""),
          "launch 真的被 _brk_wrap 包上了(不包 = 那一格永远是空的)")
    # ⚠️ 形状意外时**必须退化成"无"**, 绝不能让整份日志消失 —— 外层 `_copy_bench_log` 会把
    #    异常吞成空串, 玩家只看到"没有可复制的数据"(实测: [9] 是扁平元组时 TypeError)。
    _fr_bad = [((g_, t_) + (0.0,) * 7 + (((1.3, "发射"),) if g_ > 12 else ()))
               for g_, t_ in zip(_g, _t)]
    _log_bad = _LogHost(_g, _t, _x, frames=_fr_bad)._bench_frame_log()
    check(("子步骤" in _log_bad and "[无]" in _log_bad
           and len(_log_bad.split("\n")) > len(_g)),
          "子步骤形状意外时退化成『无』, 整份日志不消失(实测踩到过 TypeError)")
    # 分母必须是**该阶段自己的帧数**, 不是"最慢 1% 的总帧数"
    check("装杯 40 /" in _log and "飞行 60 /" in _log,
          "表里的分母是该阶段的总帧数(与成绩页同一条口径)",
          " ".join(l.strip() for l in _log.split("\n") if l.startswith("#   装杯")
                   or l.startswith("#   飞行")))
    # 正文行数 == 帧数(不许静默丢帧)
    _body = [l for l in _log.split("\n") if l and not l.startswith("#")]
    check(len(_body) == len(_g),
          "日志正文**每帧一行**, 一帧不少(静默丢帧 = 拿它分析会得出错的结论)",
          "%d 行 / %d 帧" % (len(_body), len(_g)))
    # ⚠️ 这条**逐字**钉住正文那一行的形状 ⇒ 任何加列都必须同步改这里。
    #    2026-09-20 末尾追加了「差额律」三列(差 / 上一帧body / 别的线程CPU),
    #    见下面那一组; 这里跟着补上 —— **不是门禁被削弱, 是形状真的变了**,
    #    而"新列只能追加在末尾"这条约束由下面那组单独钉。
    # ⚠️ 2026-09-20 又追加了第 13 列「距本发发射帧数」(`_SINCE_LAUNCH`) —— 它以前**只活在
    #    内存里**, 于是面板印的「飞行平均持续 X 秒」**没法从日志复核**(玩家报那个 bug 时
    #    手上只有均值, 只能靠反推)。取不到时印 **-1**(不能是 0 —— 0 是合法计数值)。
    check(_body[0] == "6.00,飞行,0,0.00,0.00,0,0,,0.00,6.00,0.00,0.00,-1",
          "正文格式是 `帧间隔,阶段,文字重建,_frame自算,主线程,发声,震动,最大子步骤,等屏幕`"
          "+ 末尾四列 `差,上一帧body,别的线程CPU,距本发发射帧数`"
          "(新列**只能追加在末尾** —— 插在中间会让旧解析器静默错位)", _body[0])

    # ---- 逐帧 CPU 账: 把"慢"分流成 在等 / 真在算 ----
    # 判据是**主线程占帧长的比例**(不是"自算是否>=1ms")。夹具刻意让两堆都出现:
    # 400 帧 -> 中位帧 6.0 -> 慢帧门槛 12.0 -> 2 个等待型 + 2 个计算型。
    # ⚠️ 循环变量千万别叫 `_x` —— 上面那个"逐帧重建次数"的列表就叫 `_x`, 会被覆盖掉。
    _g2 = [6.0] * 396 + [12.5] * 2 + [14.0] * 2
    _fr2 = []
    for _gv in _g2:
        _t2 = 12.0 if _gv >= 14.0 else (1.5 if _gv >= 11.0 else 1.7)
        _fr2.append((_gv, "飞行", 0, 0, 0, 0.3, 0, _t2, 0,
                     ((1.2, "板面"),) if _gv >= 14.0 else ()))
    _log_cpu = _LogHost(_g2, ["飞行"] * len(_g2), frames=_fr2)._bench_frame_log()
    check("慢帧 CPU 账" in _log_cpu,
          "头部印出慢帧的 CPU 账(自算 / 主线程 中位)—— 分流「慢」是谁造成的",
          " ".join(l.strip() for l in _log_cpu.split("\n") if "CPU 账" in l)[:90])
    check("主线程没在跑(<30%帧长) 2 帧" in _log_cpu,
          "主线程没在跑的慢帧被单独数出来(帧在等 ≠ 算不出来)",
          " ".join(l.strip() for l in _log_cpu.split("\n") if "主线程没在跑" in l)[:100])
    check("一直在算(>=70%) 2 帧" in _log_cpu,
          "主线程一直在算的慢帧也被单独数出来",
          " ".join(l.strip() for l in _log_cpu.split("\n") if "一直在算" in l)[:100])
    check("常态帧主线程只占帧长" in _log_cpu,
          "给了一把「常态帧占多少」的尺子(慢帧低于它 = 在等; 高于 = 真在算)")
    check("_frame自算" in _log_cpu and "主线程" in _log_cpu and "最大子步骤" in _log_cpu,
          "正文每帧带上自算/主线程/最大子步骤三列")
    # 文字重排也必须进子步骤计时 —— 否则"主线程烧了 13ms 而五个已计时的加起来只有 2ms"
    # 那种账会一直差着一大截没人认领。
    check('_brk_add("文字", _t0)' in text_src,
          "`Label.texture_update` 自己也被 `_brk_add` 计时(子步骤里会出现「文字X.X」)")
    # 发声/震动与慢帧的相关性 —— 玩家 2026-09-15 问的「卡的那一下是不是正在响/正在震」。
    check("慢帧当帧在发声" in _log_cpu and "慢帧当帧在震动" in _log_cpu,
          "头部印出「慢帧 × 发声/震动」的交叉表(与「慢帧 × 文字重建」同一个形状, 便于横向比)",
          " ".join(l.strip() for l in _log_cpu.split("\n") if "在发声" in l)[:80])
    # ⚠️ **滞后窗口**那一版必须有: 发声计数记在**投递那一刻**, 真正的 JNI 调用在其后 —— 而 JNI
    #    会在 JVM 里分配对象, GC 又是**停全世界**的。自证: 夹具把发声放在慢帧的**前一帧**时,
    #    「当帧」那行报 0/2、「前3帧内」那行报 2/2 —— 只有滞后窗口能看见它。
    check("前3帧内" in _log_cpu,
          "印出滞后窗口那一版(当帧视角会漏掉「发声后一两帧才卡」这种因果)",
          " ".join(l.strip() for l in _log_cpu.split("\n") if "前3帧内" in l)[:80])
    check(any(l.startswith("# 每行:") and "发声" in l and "震动" in l
              for l in _log_cpu.split("\n")),
          "正文每帧带上发声/震动两列(表头写明了)",
          " ".join(l.strip() for l in _log_cpu.split("\n") if l.startswith("# 每行:"))[:90])

    # ---- [24b] 节拍真值 + 等屏幕(swap 阻塞) ----
    # ① 同一份代码、同一个版本, 屏幕 120Hz 那轮 1%Low=83.3, 60Hz 那轮只有 42.2 ——
    #    **屏幕档位是跨日志比较的唯一前提, 而它在此之前从没进过日志文件。**
    # ② Kivy 的绑定回调 `on_flip` 跑在真正 swap **之前** —— 所以"帧间隔"里含着等屏幕的时间
    #    却分不出来。`time.thread_time()` 也看不见它。
    check(any(l.startswith("# 节拍:") and "屏幕" in l and "vsync" in l
              for l in _log_cpu.split("\n")),
          "头部印出节拍真值(屏幕Hz / 请求的高刷模式 / Kivy上限 / vsync / multisamples)"
          " —— 跨日志比较的前提",
          " ".join(l.strip() for l in _log_cpu.split("\n") if l.startswith("# 节拍:"))[:150])
    check("屏幕 120.0Hz" in _log_cpu and "vsync=1" in _log_cpu,
          "节拍那行的值取自诊断数据, 不是写死的占位",
          " ".join(l.strip() for l in _log_cpu.split("\n") if l.startswith("# 节拍:"))[:150])
    check(any(l.startswith("# 每行:") and "等屏幕" in l for l in _log_cpu.split("\n")),
          "正文表头写明最后一列是「等屏幕ms」",
          " ".join(l.strip() for l in _log_cpu.split("\n") if l.startswith("# 每行:"))[-40:])
    # ⚠️ 夹具必须**真的**把 swap 值分两堆, 否则这一格永远是"两边都是 0.00"的空断言。
    _fr3 = []
    for _i, _gv in enumerate(_g2):
        _t2 = 12.0 if _gv >= 14.0 else (1.5 if _gv >= 11.0 else 1.7)
        _fr3.append((_gv, "飞行", 0, 0, 0, 0.3, 0, _t2, 0,
                     ((1.2, "板面"),) if _gv >= 14.0 else (),
                     9.0 if _gv >= 11.0 else 0.3))
    _log_sw = _LogHost(_g2, ["飞行"] * len(_g2), frames=_fr3)._bench_frame_log()
    check("慢帧当帧 等屏幕 中位 9.00 毫秒" in _log_sw and "其余帧 中位 0.30 毫秒" in _log_sw,
          "头部给出「慢帧 × 等屏幕」交叉表 —— 那批主线程没烧 CPU 的慢帧到底在等谁, 看这一行",
          " ".join(l.strip() for l in _log_sw.split("\n") if "等屏幕 中位" in l)[:110])
    check("等屏幕(swap 阻塞)" in _log_sw and "累计" in _log_sw,
          "头部给出等屏幕的全程中位 / 最慢 / 累计(占窗口百分比)",
          " ".join(l.strip() for l in _log_sw.split("\n") if l.startswith("# 等屏幕"))[:110])
    # ⚠️ 这句提示**两个方向都要写**。桌面实测撞到过反例: vsync 下大部分帧 swap 只要 0.3ms,
    #    而排到队的那一帧要等 15.9ms —— 只写"高=在等屏幕"会把人引到错误结论上。
    check("CPU 也高=我们交晚了" in _log_sw and "CPU 低=真在等屏幕" in _log_sw,
          "等屏幕那一行的判据写明了**两个方向**(必须配合 CPU 账一起读, 不能只看 swap 高)",
          " ".join(l.strip() for l in _log_sw.split("\n") if "等屏幕 中位" in l)[-70:])
    # ---- 已采未印的几格(GC / 进程态 / JNI / 后端 / 越界位次) ----
    check("GC:" in _log_sw and "后端" in _log_sw,
          "头部印出 GC 次数/累计/单次最慢 + 实际生效的后端(降级到别的后端会改变所有音效读数的含义)",
          " ".join(l.strip() for l in _log_sw.split("\n") if l.startswith("# GC:"))[:110])
    check("进程态" in _log_sw and "内核" in _log_sw,
          "头部印出进程态 utime/stime(内核占比高 = JNI/Binder/写盘那一族; 用户态高 = 纯 Python 抢 GIL)")
    check("最差20帧距上次发射的帧数" in _log_sw,
          "头部印出「最差 20 帧距上次发射多少帧」(发射后 0.33 秒内 = 发射本身贵)")
    check("JNI:" in _log_sw or not _LogHost.__dict__,  # 夹具没填 JNI 时这一块按需跳过
          "有 JNI 数据时印出主线程/后台两笔(主线程那笔才是卡我们的)")
    check("配置写盘" not in _log_cpu,
          "阴性对照: 夹具里 cfg_n=0 时**不印**配置写盘那一行(不凭空造一行 0.0)")
    # ---- CPU 调频状态 ----
    # 玩家在跑分时用第三方工具看到**采样窗口前期 CPU 只跑 1.1GHz、后期物理跑分才 4.5GHz**。
    # 而"主线程ms"量的是 `time.thread_time()` = 真实 CPU 秒, 主频差 4 倍会让同一个函数量出来
    # 差 4 倍 —— 不印这一格, 上面所有 CPU 数字都缺前提。
    _h_cf = _LogHost(_g, _t, _x)
    _h_cf._bench_diag = dict(_h_cf._bench_diag)
    _h_cf._bench_diag.update({"cpufreq_n": 50, "cpufreq_cap": 4608.0, "cpufreq_p50": 1152.0,
                              "cpufreq_min": 1056.0, "cpufreq_max": 4608.0,
                              "cpufreq_low_pct": 88.0, "cpufreq_mean": 1250.0})
    _log_cf = _h_cf._bench_frame_log()
    check("CPU 频率(**渲染窗口**那一段" in _log_cf and "上限 4608MHz" in _log_cf
          and "低于上限一半的采样占 88%" in _log_cf,
          "头部印出**渲染窗口**那一段的 CPU 频率(最低/最高/上限 + 低频占比)"
          "—— 所有 CPU 数字的前提",
          " ".join(l.strip() for l in _log_cf.split("\n") if "CPU 频率" in l)[:130])
    # ⚠️ **代表值必须是平均, 不是中位**(玩家:「cpu频率不能用中位数」)。
    #    夹具里 mean=1250 / p50=1152 —— 两个数**故意不同**, 这样"印了哪个"才分辨得出来。
    check("**平均 1250MHz**" in _log_cf and "(中位 1152)" in _log_cf,
          "⚠️ 代表值印的是**平均**(1250) 而不是中位(1152); 中位降级到括号里, "
          "与最低/最高并列成「分布的一项」")
    check("等 vsync" in _log_cf,
          "写明了读法: 渲染窗口里应用大部分时间在等 vsync ⇒ 调频器判它很闲, "
          "**这一段天生偏低**, 要看跑分时跑到多少得另看那一行")
    # 阴性对照: 夹具里**不给** mean ⇒ 必须印「没采到」, **绝不拿中位数顶**(那正是印假数)
    _h_cf2 = _LogHost(_g, _t, _x)
    _h_cf2._bench_diag = dict(_h_cf._bench_diag)
    _h_cf2._bench_diag.pop("cpufreq_mean", None)
    _log_cf2 = _h_cf2._bench_frame_log()
    check("平均 **没采到**" in _log_cf2 and "平均 1152MHz" not in _log_cf2,
          "阴性对照: 没有 mean 时印「没采到」, **不许拿中位数冒充平均**")
    check("CPU 频率(**渲染窗口**那一段" not in _log_cpu,
          "阴性对照: 采不到频率(桌面/被 SELinux 挡住)**整段不印**, 不印一行 0 冒充量到了")
    check("_cpufreq_start()" in _fn_body(bench_src, "_start_benchmark")
          and "_cpufreq_stop()" in _fn_body(bench_src, "_finish_render_sample"),
          "频率采样在开跑时启动、停采样时停掉(不留后台线程)")
    # ⚠️ 采样**绝不能**塞进 `_on_flip`: 读 sysfs 每次几十微秒, 而这一格恰恰是用来解释
    #    `主线程ms` 的 —— 埋点自己抬高被解释的那个数就自相矛盾了。必须是工作线程。
    check("_cpufreq_mhz()" not in _fn_body(bench_src, "_on_flip"),
          "频率采样**不在** `_on_flip` 里(埋点不许抬高它要解释的那个数)")
    check("def _cpufreq_worker" in SRC["device"]
          and "threading.Thread(target=_cpufreq_worker" in SRC["device"],
          "频率采样跑在工作线程上")

    # ---- 第二趟文字渲染(真光栅化) ----
    # Kivy 的文字是两趟画的: `refresh()` 里 `render()` 只量宽高, 真光栅化在 `_texture_fill`
    # (由 Texture 回调在**纹理下次被用到时**触发, 跑在 `texture_update` 外面)。
    # `_texupd_wrap` 只包了后者 ⇒ 一直只在量第一趟。真机那 11 毫秒差额就在这一趟里。
    check('_brk_add("填纹", _t0)' in text_src,
          "第二趟文字渲染(`CoreLabel._texture_fill`)也进了子步骤计时(日志里会出现「填纹X.X」)")
    check("_LB._texture_fill = _texture_fill" in text_src
          and '_texture_fill.__name__ = "_texture_fill"' in text_src,
          "包的是 `LabelBase._texture_fill` 且**保住了 `__name__`**(Kivy 有按方法名找的地方, 改名会静默失联)")
    check("from kivy.core.text import LabelBase as _LB" in text_src,
          "取 `LabelBase` 走 try/except(换 Kivy 版本时不会把整个模块带崩)")
    # 正文第 9 列必须真的是那个值(不是恒 0 的占位)。
    # ⚠️ 判据从"**最后一列**"改成"**第 9 列**" —— 2026-09-20 末尾追加了「差额律」三列,
    #    而"最后一列"这个写法会让这条闸**在加列时悄悄失去意义**(它仍然绿, 但盯的已经不是
    #    等屏幕那个数了)。按**列号**钉才不会被后续加列带走。
    _rows_sw = [l for l in _log_sw.split("\n") if l and not l.startswith("#")]
    _f0 = _rows_sw[0].split(",")
    _fN = _rows_sw[-1].split(",")
    check(len(_f0) >= 9 and len(_fN) >= 9 and _f0[8] == "0.30" and _fN[8] == "9.00",
          "正文**第 9 列**是该帧真实的等屏幕毫秒(不是恒 0 的占位)",
          "%s ... %s" % (_rows_sw[0], _rows_sw[-1]))
    # 阴性对照: 全部帧 swap 都是 0 时, 不该凭空编出一张交叉表(两行都不该出现)
    _log_zero = _LogHost(_g, _t, _x)._bench_frame_log()
    check("等屏幕(swap 阻塞)" not in _log_zero and "等屏幕 中位" not in _log_zero,
          "阴性对照: 一个 swap 值都没有时**不印**这一块(而不是印一行 0.00 冒充测到了)")
    # 结构性: 埋点必须真的包在 swap 上, 且在采样闸门之内、在开跑时归零
    check("_cls.flip = flip" in text_src and "_cls = type(Window)" in text_src,
          "`_swap_wrap` 包的是 **类** 上的 `flip`(包实例属性撞不到 `self.flip()`)")
    check("_FRAME_SWAP[0] = (time.perf_counter() - _t0) * 1000.0" in text_src,
          "`Window.flip()` 的耗时真的被写进 `_FRAME_SWAP`")
    check("if not _TEXUPD_ACTIVE[0]:" in text_src,
          "埋点只在跑分采样期生效(平时不改变被测量的东西)")
    check("_FRAME_SWAP[0] = 0.0" in _fn_body(bench_src, "_on_flip"),
          "`_on_flip` 读完就把 `_FRAME_SWAP` 归零(否则同一笔会被记进后面每一帧)")
    check("_FRAME_SWAP[0] = 0.0" in _fn_body(bench_src, "_start_benchmark"),
          "`_start_benchmark` 里把 `_FRAME_SWAP` 归零(上一轮残值会变成第一帧的假读数)")
    # ⚠️ 同一处漏下的三个计数: 发声/震动/预热位在采样期外照常累加, 而 `_on_flip` 只在
    #    `if prev is not None` 里复位 ⇒ **日志第一行背的是"上次采样以来"的全部累计**。
    _sb = _fn_body(bench_src, "_start_benchmark")
    check(all(("_FRAME_PROBE[%d] = 0" % _k) in _sb for _k in (0, 1, 2)),
          "`_start_benchmark` 把发声/震动/预热三个逐帧计数也归零(第 0 行不再是跨窗口残值)")
    check("_on_flip" in bench_src
          and all(("_FRAME_PROBE[%d] = 0" % _k) in _fn_body(bench_src, "_on_flip")
                  for _k in (0, 1, 2)),
          "`_on_flip` 里那三个「每帧清」仍然在(两处都要 —— 一处开跑清一次, 一处每帧清)")

    # ---- 预热必须**真的烘到纹理**(2026-09-15) --------------------------------------
    # 起因: 真机有三帧 **`文字重建 = 0` 却各付 3.1~4.4 毫秒填纹**。
    #   `CoreLabel.refresh()` 之后填纹 **+0** —— 它只量宽高、挂了个回调; 真正的光栅化要等
    #   **这张纹理第一次被 bind**。所以预热链跑完, 烘出来的纹理**一张都没上过光栅**。
    # ⚠️ 用 `_code_only` 而不是裸 `in` —— 这一段自己就带着讲这件事的注释。
    _bk_c = _code_only(_fn_body(text_src, "_texex_bake"))
    check("_cl.texture.bind()" in _bk_c,
          "`_texex_bake` 主动 bind 一次 —— 不 bind 的话预热一张纹理都没烘, 账全拖到运行期")
    check(_bk_c.find("_cl.texture.bind()") > _bk_c.find("_cl.refresh()"),
          "bind 排在 `refresh()` **之后**(refresh 之前连纹理都还没有)")
    check(".pixels" not in _bk_c,
          "用 `bind()` 而不是读 `.pixels`(后者要把整张纹理拷进 Python 侧: 45KB/项 x 上百项)")
    _bk_tail = _bk_c.split("_cl.texture.bind()")
    # ⚠️ **绝不能写成 `_bk_c.split(...)[1]`** —— 阴性对照(把 bind 摘掉)时那个 split 只有一段,
    #    `[1]` 当场 IndexError ⇒ 门禁**在中间炸掉, 它后面的 22 条一条都没跑**。
    check(len(_bk_tail) > 1 and "except" in _bk_tail[1][:80],
          "bind 有兜底 —— 它是要往 GL 里塞东西的, 失败绝不能把烘焙整条路带崩")

    # ---- 「字号」的分解计数 + ★ 的「带字号」列(2026-09-15) --------------------------
    # 同一个名字下藏着两种**修法相反**的病:
    #   「叫 1 次 / 量 12 回」= 一次 `_fit1` 走完阶梯+二分, 每回都是冷字号 ⇒ 该收敛字号集合;
    #   「叫 20 次 / 量 20 回」= 一帧里好几个标签同时换字 ⇒ 该错峰合并。
    _fb_c = _code_only(_fn_body(ui_base_src, "_fit1"))
    check("_FRAME_FIT[0] += 1" in _fb_c,
          "`_fit1` 记「叫了几次」")
    check(_fb_c.find("_FRAME_FIT[0] += 1") > _fb_c.find("_fit_busy"),
          "计数排在重入闸**之后**(闸门里早退的不是活, 数进去会虚高)")
    _tp_c = _code_only(_fn_body(text_src, "text_px"))
    check("_FRAME_FIT[1] += 1" in _tp_c
          and _tp_c.find("_FRAME_FIT[1] += 1") > _tp_c.find("if got is None:"),
          "`text_px` 只数**缓存未命中**(= 真的现量了一回; 命中不算 —— 冷测量才是那笔钱)")
    check("_FRAME_FIT[0] = 0" in _sb and "_FRAME_FIT[1] = 0" in _sb,
          "`_start_benchmark` 把分解计数归零(否则第一帧背着跨窗口残值, 印出个没人解释得了的数)")
    _of_c = _code_only(_fn_body(bench_src, "_on_flip"))
    check("(_FRAME_FIT[0], _FRAME_FIT[1])" in _of_c
          and "_FRAME_FIT[0] = 0" in _of_c and "_FRAME_FIT[1] = 0" in _of_c,
          "`_on_flip` 把它存进 `_bench_frames` 的 [11] 并每帧归零")
    # ⚠️ 这两条**必须用 `_code_only`**: 裸 `"带字号" in main_src` 是**恒真的假绿灯**。
    _bl_c = _code_only(_fn_body(bench_src, "_bench_frame_log"))
    check("带板面 %d / 带字号 %d" in _bl_c,
          "★ 那行印「其中带字号 N」—— 用**分解计数**判, 不用子步骤名(那两帧的 `_fit1` "
          "跑在别的回调里, 最大子步骤那栏未必写着「字号」)")
    check("(%d次/%d测)" in _bl_c,
          "最慢三帧那行的「字号」带上 (次数/测量数)")

    # ---- 字号挑选不再用二分(2026-09-15) -------------------------------------------
    # 运行期冷量到的字号里, **不在预热表的那些全是二分产出的任意值**, 而"开一个没开过的字号"
    # 真机约 **40 毫秒**。改法: 二分换成 **固定细分档** `FIT_FINE`。
    _ff = _code_only(_fn_body(text_src, "_fit_font_size_slow"))
    check("FIT_SCALES + FIT_FINE" in _ff,
          "字号挑选走 `FIT_SCALES + FIT_FINE` 两段**固定阶梯**")
    check("_mid" not in _ff and "range(6)" not in _ff,
          "⚠️ 二分**已经删干净** —— 它在这个引擎里是结构性错误: 保证产出"
          "「这辈子只用一次」的字号, 而每个新字号都是一次 40 毫秒的冷字体表打开")
    check("FIT_FINE" in text_src and "FIT_HARD_FLOOR = FIT_FINE[-1]" in text_src,
          "`FIT_HARD_FLOOR` 是 `FIT_FINE[-1]`(**只此一处真源**, 免得两处各写一个数漂掉)")
    # ⚠️⚠️ **反转了这一条**: 上一版要求"把 FIT_FINE 也烘上", 实测**害了事** —— 桌面实测
    #    Kivy 的 SDL2 字体缓存**上限是 64**: 开过 64 个不同字号后最早那个被挤掉;
    #    而 12 基准 + 4 大字基准 x 13 档 = 155 项 ⇒ **预热自己把自己挤掉**。
    _pb_c = _code_only(_fn_body(winfx_src, "prebake_step"))
    check("_all_k = FIT_SCALES + FIT_FINE" not in SHIPPED
          and SHIPPED.count("_all_k = FIT_SCALES") == 1,
          "预热**只烘基础 6 档**(不烘 FIT_FINE) —— 烘满 13 档会超过字体缓存上限 64, "
          "把前面烘好的全挤掉, 而且**不报错**")
    # ⚠️⚠️ **真机打脸, 两次**: ① 预热里 `round(_b * _k, 4)` 是错的 —— 运行期算的是**原值**,
    #    差 1e-7 就是两个 fontid。② 大字那批**不能一个都不烘** —— 改成**只烘第一档**(4 项)。
    check("round(_b * _k" not in _pb_c,
          "⚠️ 预热**不许 round** 字号 —— 运行期用的是原值, round 过就是两个 fontid, 等于没烘")
    check('globals()["_FONT_WARM_SIZES"] = tuple(' in _pb_c
          and "FIT_SCALES[0]" in _pb_c,
          "大字/飘字**只烘第一档**(k=1.0) —— 一个都不烘会让 sp(48) 冷开 49 毫秒, "
          "全烘又超上限; 第一档多半就放得下")
    check("_b * FIT_SCALES[0] for _b in (sp(36), sp(48), sp(26), sp(30))" in _pb_c,
          "那 4 个基准与 `big_result_text` 里的 `sp(48)` / `sp(36)` 对齐(手抄的, 注释已标明)")
    check("_FONT_WARM_ALL" in _pb_c and 'for _v in globals()["_FONT_WARM_SIZES"]' in _pb_c,
          "「最近预热档」那份清单**只收真的进了预热链的** —— 表里列着、链子却没碰它, "
          "会印出两条自相矛盾的读法(实踩过)")
    check("_WARM_DID" in _pb_c,
          "另记一份「预热**真的量过**哪些字号」(和上一条不同: 上一条是「表里列了什么」) —— "
          "冷测量时两相对照才分得清「被挤掉」和「压根没烘」")
    # 预热项数必须明着印, 而且要能一眼看出有没有超上限
    try:
        _n = len(WFX._FONT_WARM_HUD or ()) + len(WFX._FONT_WARM_SIZES or ())
        check(_n == 0 or _n <= 60,
              "预热项数 ≤ 60(Kivy 字体缓存上限实测 64; 超了就是**预热自己把自己挤掉**)",
              "现在 %d 项" % _n)
    except Exception as _e:
        check(False, "预热项数门禁跑不起来", repr(_e))
    check("字号预热表: %d 项" in bench_src and "字体缓存上限约 64" in bench_src,
          "日志头部印预热项数 + 上限, 超了能一眼看出来(不然又得靠桌面探针回头查)")
    # ⚠️ **判据必须比"实际开过几个", 不能比"我烘了几项"**。
    #    原来那句拿 `_wc`(=预热表项数) 去比 64 ⇒ 恒印「有余量」, 而**紧跟着的下一行**印的是
    #    "开过 86 个 ⇒ 必然发生过淘汰" —— **同一份日志两行结论相反**, 且乐观的那行在视线更前面。
    _nopen_eq = "_nopen = len(_FS_OPEN_SET)"
    _i_up = bench_src.find(_nopen_eq)
    _i_warm = bench_src.find('_lines.append("# 字号预热表: %d 项')
    check(_i_up > 0 and ("_nopen > 60" in bench_src or "_nopen >= 56" in bench_src),
          "字体缓存判据比的是**实际开过的 fontid 数**(`len(_FS_OPEN_SET)`), 不是预热表项数")
    check(0 < _i_up < _i_warm,
          "而且那个判据**排在预热项数那一行之前** —— 先看到的是真正会溢出缓存的那个数",
          "实际开过=%d, 预热表那一行=%d" % (_i_up, _i_warm))
    check("_FS_MUTE_UNTIL" in bench_src and "_FS_MUTED_N" in bench_src,
          "报告 UI(「帧率曲线」弹窗)自己的字号**不计入账本** —— 它是测量动作, 不是被测对象;"
          " 而跳过几次**必须印出来**(不许静默)")
    check("_FS_COLD_CNT" in bench_src and "_FIT_HIST" in bench_src,
          "两个**普查**计数器: 每个 fontid 冷开过几次(定钉几个)、本帧冷开几次的分布"
          "(回答「一帧会不会开十几个」—— `(N次/M测)` 是采样, 只有 2/21059 帧印过)")
    check("# ★口径A " in bench_src and "# ★口径B " in bench_src,
          "两套 ★ 口径**各自带名印全** —— 它们都叫 ★ 却是两个量(实测第 2 轮 A=3 而 B=8, "
          "差 167%; 而第 1 轮两者都给 1, 只看一轮永远发现不了)")
    check('尾·回调' in text_src and '尾·空档' in text_src and "_clock_wrap" in text_src,
          "「尾」拆成两段(尾·回调 / 尾·空档)+ flip 自身 = 三段, 不再是一个 lump")

    # ---- 同类行/同类钮的度量 bold 必须与预热表口径一致(2026-09-15) -------------------
    # 病根: 两处 uniform 原来用"**最长那一行自己**的 bold"去度量, 而 `_longest` 是按"谁最宽"
    #   选出来的 ⇒ 同一组换一段文字就可能换人, 于是同一个基准会**今天按粗体量、明天按细体量**。
    _fu = _code_only(_fn_body(ui_base_src, "_fit_uniform"))
    _fb2 = _code_only(_fn_body(ui_base_src, "_fit_buttons_uniform"))
    check('any(bool(getattr(w, "bold", False)) for w in rows)' in _fu
          and 'text_px(w.text or "", base, _bd)' in _fu
          and 'fit_font_size(_longest.text or "", base, avail, _bd)' in _fu,
          "同类行: 度量 bold = 「组里只要有一个粗体就按粗体」, 且**度量与定档用同一个** `_bd`")
    check('any(bool(getattr(b, "bold", False)) for b in btns)' in _fb2
          and 'text_px(b.text or "", base, _bd)' in _fb2
          and 'fit_font_size(longest.text or "", base, avail, _bd)' in _fb2,
          "同类钮: 同上(逐个小 `_fit1` 会让长标签缩得更多, 所以这一排必须共用一个档)")
    check('bool(getattr(_longest, "bold", False))' not in _fu
          and 'bool(getattr(longest, "bold", False))' not in _fb2,
          "⚠️ 「最宽那一行自己的 bold」这个写法**已经彻底删掉** —— 它是预热烘不到的那个源头")

    # ---- 给「待机 -> 蓄力那一拍的怪帧」补的四个埋点(2026-09-15) ---------------------
    # 三份真机日志里都有一个同形状的怪帧: `_frame` 自算 0.03~0.25 毫秒、子步骤栏空着,
    # 主线程却烧 15~47 毫秒。那一拍会跑的、**不在 `_frame` 里**的东西:
    # `_auto_launch_tick` -> `_update_slots` / `start_charge`, 外加"自算结束后到交画面"那一段。
    # ⚠️ 同上改运行期判据。(`start_charge` 在新版住在 `Game` —— 老版住在 RootWidget,
    #    拆分后 `PlayMixin.start_charge` 只是一行转发壳, 包它量不到逻辑耗时。)
    check(all("_brk_wrap" in getattr(getattr(_c, _a, None), "__qualname__", "")
              for _c, _a in ((ROOTMOD.RootWidget, "_auto_launch_tick"),
                             (GAME.Game, "start_charge"),
                             (GAREA.GameArea, "_update_slots"))),
          "三个 Clock 回调都挂上了子步骤计时(自动发 / 起蓄 / 槽面)")
    # ⚠️ `_fn_body` 在这里**不能用**: 它按"下一个 `\n    def `"切, 而 `_swap_wrap` 是**顶格**
    #    函数、里面那个 `def flip` 正好是 4 空格缩进 ⇒ 切出来的只有文档字符串那一段。
    def _mod_fn(src, name):
        _i = src.find("\ndef %s(" % name)
        if _i < 0:
            return ""
        for _m in re.finditer(r"\n(?=(?:def |class )\S)", src[_i + 1:]):
            return src[_i:_i + 1 + _m.start()]
        return src[_i:]
    _fw = _code_only(_mod_fn(text_src, "_swap_wrap"))
    check('_brk_add("尾", _FRAME_END[0])' in _fw,
          "`尾` = 从 `_frame` 算完到真正交画面之间那一段(渲染 + 其余回调); 没有它分不出"
          "「主线程 − 自算」那 15 毫秒是渲染还是别的回调")
    # ⚠️ 用 `rfind` 不用 `find`: 函数开头那条"不在采样期就直接转发"的分支里也有一次
    #    `_orig(self, ...)`, 它是**第一次**出现 —— 拿 `find` 比会永远比不过(假红, 实测踩到)。
    check(0 <= _fw.find('_brk_add("尾"') < _fw.rfind("_orig(self"),
          "⚠️ `尾` 必须在**主路径那次** `_orig` 之前记 —— `_on_flip` 是在 `_orig` 里面被派发的, "
          "它读完 `_FRAME_BRK` 就清空, 记晚了这一帧白记")
    _src_lines = play_src.splitlines()
    _idx = [_n for _n, _l in enumerate(_src_lines)
            if _l.strip() == "_FRAME_END[0] = time.perf_counter()"]
    check(len(_idx) == 1,
          "`_frame` 里把「这一刻」写进 `_FRAME_END`(全文件**只此一处** —— 两处会互相盖掉)")
    # ⚠️ 锚点重映射: 老版判据是"它后面第一个非空行必须顶格(= 已出类)" —— 那在**单文件**里
    #    成立, 而新版 `_frame` 后面紧跟的是**同类的方法**(`_dispatch`), 永远不可能顶格。
    #    判据的本体("那一句是 `_frame` 的最后一条语句")一字未改, 只是改成直接量它。
    _fe_tail = _code_only(_fn_body(play_src, "_frame")).split(
        "_FRAME_END[0] = time.perf_counter()")
    check(len(_fe_tail) > 1 and not _fe_tail[1].strip(),
          "⚠️ 那一句必须是 `_frame` 的**最后一行** —— 放中间的话 `尾` 会含住后面还没跑的"
          "部分, 读出来是假账(而且不报错)。判据: 它后面第一个非空行必须顶格(缩进 0 = 已出类)")

    # ---- 冷字号榜(2026-09-15) -----------------------------------------------------
    # 逐帧那行只印得出「字号21.7(1次/1测)」—— 知道"一次冷测量烧了 21.7 毫秒", 却**不知道是
    # 哪个字号、哪个标签**。修法完全取决于这个, 而这一项已经猜错过两次 ⇒ 把三元组原样印出来。
    check("_COLD_FS" in bench_src and "COLD_FS_MIN_MS" in bench_src,
          "冷字号测量带 (毫秒, 字号, bold, 标签) 四元组, 且有低于门槛就不记的下限")
    _bl_c2 = _code_only(_fn_body(bench_src, "_bench_frame_log"))
    check("最慢的冷字号测量" in _bl_c2 and "冷字号测量: **一次都没有**" in _bl_c2,
          "日志头部两个分支都印(有冷字号就排名, 没有就明说「一次都没有」 —— 别让「没印」和「没发生」混在一起)")
    check("del _COLD_FS[:]" in _sb,
          "`_start_benchmark` 清空冷字号榜(不清的话第一份日志里混着上一轮的残值)")
    check("_COLD_FS_TAG[0]" in _code_only(_fn_body(ui_base_src, "_fit1")),
          "`_fit1` 记下「谁在挑字号」(给冷字号归因)")
    # ⚠️ 只印一个 `fs` 分不出两种可能: (a) 它是 `基准 x FIT_FINE`(故意不烘的那 7 档);
    #    (b) 它是"某个没进表的基准"。记下基准之后 `fs / 基准` 就是那个倍率, 一眼看出来是哪种。
    # ⚠️ `_COLD_FS_BASE` 的**定义**在 ui/text.py, 而**赋值**在 UiMixin._fit1(ui_base) ——
    #    两个模块都要看, 不然这条会假红。
    check("_COLD_FS_BASE" in text_src
          and "_COLD_FS_BASE[0] = float(getattr(w, " in ui_base_src,
          "冷字号测量记下**基准字号**(倍率 = fs/基准, 用来区分 FIT_SCALES 与 FIT_FINE)")
    check("基准%.4f 倍率%.4f" in bench_src,
          "日志印出「基准 / 倍率」两栏")
    check("读法2" in bench_src and "故意不烘" in bench_src,
          "日志里写明读法2: 倍率落在 FIT_SCALES 之外 ⇒ 那是**硬取舍**, 不是漏烘")
    # ---- `_auto_launch_tick` 逐句埋点(2026-09-15) ----------------------------------
    # 真机: `帧1201 55.92ms [自动发51.4]` —— 整个方法 51.4 毫秒, 而它调的两个子函数
    # (`槽面`/`起蓄`)**都没进前二** ⇒ 钱花在这个方法**自己身上**。逐句量开。
    _alt = _code_only(_fn_body(bench_src, "_auto_launch_tick"))
    for _seg in ("发·盘面", "发·起蓄", "发·排程", "发·整段"):
        check('_brk_add("%s"' % _seg in _alt, "`_auto_launch_tick` 分段计时: %s" % _seg)
    # ---- `_redraw` 拆成 杯层/球层 两段(2026-09-15) --------------------------------
    # 桌面基线(拆完立刻量到): 杯层 max 0.71 / 中位 0.32; 球层 max 1.64 / 中位 0.36。
    _rd = _code_only(_fn_body(winfx_src, "_redraw"))
    check('_brk_add("杯层", _t)' in _rd and '_brk_add("球层", _t)' in _rd,
          "`_redraw` 仍分「杯层 / 球层」两段计时(持久指令表把重建摊平之后, 这两格就是读每帧真花了多少的唯一窗口)")
    check(_rd.find("time.perf_counter()") < _rd.find('_brk_add("杯层"') < _rd.find('_brk_add("球层"'),
          "先取时再记账, 且杯层在球层之前(次序错了会量成负数或重叠)")
    # ---- 装杯持久指令表(2026-09-14) ------------------------------------------------
    # ⚠️ 命令流验收: 新旧各 164 条指令**逐条相同**, 只有一颗飞行中的球因两次进程 tt 不同而差一点。
    _tbc = _code_only(_fn_body(winfx_src, "_tbl_build"))
    check("def _tbl_build" in winfx_src and "def _tbl_apply_fixed" in winfx_src
          and "def _tbl_apply_balls" in winfx_src and "def _tbl_drop" in winfx_src,
          "持久指令表四个函数都在")
    check("for _b in self._balls:" in _tbc and "sorted" not in _tbc,
          "⚠️ 球层按 `self._balls` **原始顺序单趟建**(红线 1) —— 历史账: 改成"
          "「落定球 + 飞行球两趟」后 x100 一局 25.5% 的重叠帧层次错乱")
    _order = [_tbc.find('"shadow"'), _tbc.find('"under"'), _tbc.find('"dim"'),
              _tbc.find('"rim"'), _tbc.find("for _b in self._balls:"), _tbc.find('"front"')]
    check(all(_order[_i] >= 0 and _order[_i] < _order[_i + 1] for _i in range(5)),
          "⚠️ 玻璃五层顺序不许动(红线 2): 阴影 < 后层 < 压暗 < 杯口环补画 < 弹珠 < 前层")
    check("_r.size = (0.0, 0.0)" in _code_only(_fn_body(winfx_src, "_tbl_apply_balls")),
          "不可见的球只把 size 置 0、**不增删指令** —— 条数在本局内必须恒定")
    check("self._tbl_balls is self._balls" in winfx_src,
          "⚠️ 重建判据里球列表比**对象身份** —— 新一局长度可能一样(8 换 8), 只比长度"
          "会拿上一局的球继续画")
    _rdc = _code_only(_fn_body(winfx_src, "_redraw"))
    check(_rdc.count("self._tbl_drop()") >= 2,
          "⚠️ `idle` 与「两边都透明」两种早退都要**显式作废表**(红线 3) —— 少了它退出装杯"
          "后杯子/压暗会永久留在画布上(canvas.clear 是唯一能擦掉它们的机制)")
    check("reverse=True)[:4])" in bench_src,
          "每帧存**前四**个子步骤(不是前二): `杯层/球层` 嵌在 `装杯` 里、"
          "`装杯` 又嵌在 `板面` 里, 只存前二时它们**永远进不了榜**(实测: 拆完一个都看不到)")
    check(_alt.find('_brk_add("发·整段"') > _alt.find('_brk_add("发·排程"'),
          "「发·整段」排在各分段**之后**(它是包住全体的总和, 放前面就只量到一半)")

    # ---- 测试历史面板的表头/口径排版(2026-09-15, 玩家截图) -------------------------
    # ① 表头折行: 「平均/1%Low帧」在 12sp 下**正好要 82.0 px**, 而列宽是 `dp(82)` = 82.0 px
    #    —— **零余量**。② 口径那一段: `**中位数**` 是照搬 markdown 的星号(Kivy 不认)。
    _bh = _code_only(_fn_body(bench_src, "_show_bench_history"))
    # ⚠️ 判据从"钉字号字面量 12"改成**钉行为**: 走自动缩字号这条路 + 基准 >= 14。
    #    钉字面量的写法还有个反向风险: 谁把字号改回更小时**判据会跟着一起绿**。
    check("_fs_all" in _bh and "fit_font_size(" in _bh and "sp(14)" in _bh
          and "_HIST_COLS" in _bh,
          "表头与数据行**共用同一个字号**(`_fs_all`, 逐列量完取最小), 基准 sp(14) "
          "(2026-09-14 玩家:「这个时间 平均/1%low帧什么的标题字体的大小增加」; "
          "2026-09-15 玩家:「项目内的字体大小改成相同」—— **别再退回逐列各缩**, "
          "那会让表头比数据行小一档)")
    check("text_size = w.size" not in _bh.replace(" ", ""),
          "⚠️ 表头**不再**把 `text_size` 两维都绑成 size —— 那正是折行的直接原因")
    check("_HIST_SEP" not in _bh,
          "**不再用空格分隔**(玩家 2026-09-15 定案) —— `_HIST_SEP` 应当已被整个删掉")
    check("_HW" in _bh and "_HIST_COLS" in _bh,
          "表头与数据行**共用同一套列宽**(`_HW`)与同一份列名(`_HIST_COLS`)")
    check("size_hint_x=None" in _bh,
          "数据行是**一排固定列宽的 Label**(不是整行一个 Label) —— 这才叫准表格")
    check("(w.width, None)" in _bh,
          "行是**单行 Label**, `text_size` 只绑宽度(绑成 `w.size` 就会折行 —— 表头就是这么折的)")
    check("freq_text" not in _bh,
          "频率那一格**整段删掉了**(连 `freq_text` 都不该再有); "
          "⚠️ `phys_freq_mean` 仍存在 JSON 里, 只是不再显示")
    # ⚠️ 判据只取**脚注那个字符串字面量**, 不再切 300 字符源码窗口 —— 旧写法会把脚注**之后**
    #    的代码一起吃进来。
    _fseg = bench_src.split("text=('  中位跑分：")
    _ftxt = _fseg[1].split("')", 1)[0] if len(_fseg) > 1 else ""
    check(bool(_ftxt) and "**" not in _ftxt,
          "脚注那段文字里**没有 markdown 星号**(Kivy 不认, 会在屏幕上原样显示两个星号)")
    check("口径：" not in _ftxt,
          "⚠️ 开头那个「口径：」**已经删掉**(玩家定稿: 那两行本身就是注释, 前面再挂标签是废话)")
    # ⚠️⚠️ **玩家定稿: 标题金色、备注灰色**。**判据改成两处分开切片**, 理由: 旧那条只钉了
    #    **一侧**, 要是有人把标题改成 `COL_SUB`、备注留着 `COL_BALL`, 它照样绿。
    #    现在两侧各钉各的, 并且**互斥**, 往哪个方向换都会红。
    _ttl = (_bh.split("测试历史（渲染 / SoC）")[1][:200]
            if "测试历史（渲染 / SoC）" in _bh else "")
    check(bool(_ttl) and "hex_rgb(COL_BALL)" in _ttl and "hex_rgb(COL_SUB)" not in _ttl,
          "**标题**用金色 COL_BALL（2026-09-15 玩家:「标题用金色」）",
          "标题切片尾=%r" % _ttl[-72:])
    _fblk = _bh.split("中位跑分：")[1][:260] if "中位跑分：" in _bh else ""
    check(bool(_fblk) and "hex_rgb(COL_SUB)" in _fblk and "hex_rgb(COL_BALL)" not in _fblk,
          "**那段说明**用灰色 COL_SUB（2026-09-15 玩家:「备注用灰色」; 与 09-14 那版对调）",
          "备注切片尾=%r" % _fblk[-72:])
    check("self._auto_h(foot," in _bh,
          "口径标签走 `_auto_h`(高度跟真实排版走) —— 写死高度会让折行后的最后一行被裁掉")
    # ⚠️⚠️ 玩家截图 + 当场纠正: 一条记录都没有时, 那三行字堆在弹窗底下、上面一大片空的。
    #    **我第一版改错了方向** —— 把空态的弹窗收小, 玩家: 「高度不变, 因为以后要 tmd 放数据啊」。
    check("_popup_fit_content(popup, content)" not in _bh,
          "⚠️ **不许**对历史面板调 `_popup_fit_content` —— 那会把空态弹窗收小, 而这个面板"
          "以后要装数据, 高度必须恒定(玩家当场纠正过)")
    # ---- 成绩面板: 两个按钮在底部 + 只能靠关闭按钮关(2026-09-15 玩家定稿) ----------
    _bd_c = _code_only(_fn_body(bench_src, "_bench_done"))
    check("auto_dismiss=False" in _bd_c,
          "成绩面板 `auto_dismiss=False` —— 玩家定稿「这个界面只能通过关闭按钮关闭」"
          "(原来根本没有关闭按钮, 点面板外面就把它关了)")
    check("_btnrow.add_widget(curve_btn)" in _bd_c and "_btnrow.add_widget(close_btn)" in _bd_c,
          "「帧率曲线」和「关闭」在**同一行**(横向 BoxLayout; 先 add 的在左边)")
    check("curve_btn.bind(on_release=lambda *_: self._show_fps_curve())" in _bd_c
          and "close_btn.bind(on_release=popup.dismiss)" in _bd_c,
          "两个按钮各自接对了回调")
    check(_bd_c.rfind("content.add_widget(_btnrow)") > _bd_c.find("content.add_widget(diag_lbl)"),
          "⚠️ 按钮那行**必须最后 add** —— 竖向 BoxLayout 按 add 先后自上而下排, "
          "第一版加在成绩块后面, 结果两个按钮卡在面板**中间**(截图为证)")
    # ⚠️ 判据必须**先去掉注释**再查"某串在不在": 我要删的正是这三句, 而解释这件事的
    #    注释里**也写着这三句** ⇒ 裸文本断言会**假红**。
    _i0 = bench_src.find("def _bench_low_summary_text")
    _j0 = bench_src.find("return '", _i0)
    _bls = _code_only(bench_src[_i0:_j0]) if (0 <= _i0 < _j0) else ""
    check(bool(_bls) and "主线程" not in _bls and "游戏逻辑" not in _bls
          and "文字纹理：" not in _bls and "1%% Low 定位" not in _bls,
          "⚠️ 成绩面板底部的诊断块**只留玩家看得懂的**: 删掉「1% Low 定位」(教学)、"
          "「主线程/游戏逻辑」(开发者术语)、「文字纹理 N 次」(纯埋点) —— 玩家 2026-09-15 点检过")
    # ⚠️ 分布那行的**冒号现在由共用小函数 `_dist_line_of` 拼**, 所以这里查的是"标签"而不是
    #    "带冒号的整串" —— 否则改个拼接位置就假红。
    check("最慢一帧：" in bench_src and "卡顿帧分布" in bench_src and "慢帧分布" in bench_src,
          "面板底部留着「最慢一帧：N 毫秒（阶段）」与**两条**分布(卡顿帧 / 慢帧)")
    # ---- 判据改口径: 卡顿帧 = 帧率 < 中位帧率 50%(2026-09-14 玩家定稿) ----------------
    # ⚠️ 原来是硬编码 `1000/90`(绝对 90fps)。**绝对门槛在高刷机上会失效**。
    # ⚠️ 玩家定的两档: **<50% 要尽量消除; 50%~70% 只作参考** —— 所以日志印**两行**。
    # ⚠️⚠️ 原来是 `"_th90 = _p50b / 0.5" in main_src` 与 `"/ 0.7"` —— **那是子串幻觉型的
    #    假绿灯**: `0.55` 里含 `0.5`、`0.75` 里含 `0.7` ⇒ 阈值从 50%/70% 改成 55%/75% 时
    #    它们**照样绿**, 等于没钉住任何东西。现在改成**钉常量名 + 钉唯一真源**。
    check("JANK_RATE = 0.55" in bench_src and "SLOW_RATE = 0.75" in bench_src,
          "两档门槛比例**只在一处定义**(JANK_RATE=0.55 / SLOW_RATE=0.75)")
    check("_p50b / JANK_RATE" in bench_src and "_p50b / SLOW_RATE" in bench_src,
          "日志头两颗 ★ 的门槛从**常量**派生, 不是裸数字")
    check("1000.0 / 90.0" not in SHIPPED,
          "⚠️ 绝对帧率判据**已经删干净**(它在高刷机上没有意义, 且交接文档口径是相对的)")
    check("低于「中位帧率 55%%」" in bench_src and "参考带「中位帧率 55%%~75%%」" in bench_src,
          "日志印两行: 硬指标(中位帧率 55%) + 参考带(55%~75%), 且参考带那行**明说它不是指标**")
    # ---- 采样期屏幕刷新率: 变过没有(2026-09-14, 玩家报「发射时必然降低帧率上限」) ----
    check("_BENCH_HZ" in bench_src and "def _bench_hz_tick" in bench_src,
          "采样期每 0.5 秒记一次「阶段 + 屏幕刷新率」")
    check("Clock.schedule_interval(self._bench_hz_tick, 0.5)" in bench_src,
          "那个 tick 是 0.5 秒一次 —— `_screen_hz()` 在安卓上走 JNI, **绝不能每帧调**")
    check("if not _TEXUPD_ACTIVE[0]:" in bench_src and "return False" in bench_src,
          "采样一结束 tick 就**自己摘掉**(返回 False), 不然跑完分还在后台戳 JNI")
    check("屏幕刷新率(采样期)" in bench_src and "**采不到**" in bench_src,
          "日志印这一行; **采不到也明说** —— 本工程反复踩过「没印」被读成「没发生」")
    check("_refband = " in bench_src and "_th_ref = _p50b / SLOW_RATE" in bench_src
          and "_th90 = _p50b / JANK_RATE" in bench_src,
          "参考带 = (中位帧间隔/SLOW_RATE, 中位帧间隔/JANK_RATE] 这一段")
    # ⚠️ **必须先去掉注释**: 我把"为什么删掉这一行"的注释也写在了同一个函数里,
    #    裸文本断言会被自己的注释喂饱 —— 这是本文件第三次踩同一个坑。
    _bls2 = _code_only(_fn_body(bench_src, "_bench_low_summary_text"))
    check("卡顿帧（<55%%）：共" in _bls2 and "慢帧（<75%%）：共" in _bls2,
          "面板印**两档**(玩家定稿的名字: 「改名字即可」): 卡顿帧（<55%）与 慢帧（<75%）")
    check("max(int(d.get(" + chr(34) + "slow_n" + chr(34) + ", 0)), _jn)" in bench_src,
          "⚠️ 不变量: 慢帧(75%) 一定 ⊇ 卡顿帧(55%) —— 老 diag 字典没有 slow_n 时直接印 0 "
          "会出现「卡顿帧 6 / 慢帧 0」这种自相矛盾的两行(探针夹具上撞到过)")
    # ---- 采样窗口 + 两档比例(玩家 2026-09-14 追加) ------------------------------
    # 玩家:「让我知道帧率计算的表演是多少秒, 多少帧」+「卡顿帧的比例和慢帧的比例, 写入后面对应
    #       的项目」+「先写要求, 再写数量」。
    check("采样窗口：" in _bls2 and "win_ms" in _bls2,
          "面板加「采样窗口：X 秒 · N 帧 · 中位 N 帧/秒」(数据来自 diag 的 win_ms)")
    check('out["win_ms"] = sum(float(x[0]) for x in fr)' in bench_src,
          "⚠️ 窗口时长 = **帧间隔之和**, 与日志头 `sum(gaps)` 同一口径 —— "
          "别用 `_flip_times` 首尾相减(那个含首帧之前一段, 会比日志多一帧)")
    check("JANK_RATE / _p50" in _bls2 and "SLOW_RATE / _p50" in _bls2,
          "面板印的门槛是**算出来的**(中位 x 比例 -> 帧/秒), 不是写死的")
    # ⚠️ 结构不变量: 两档的"帧数"与"分布"必须来自**同一个 list**。
    check('out["slow_n"] = len(_slow75)' in bench_src
          and "for _x in _slow75:" in bench_src,
          "⚠️ 慢帧的**帧数与分布取自同一个 list**(不许两趟各算一遍)")
    # 面板与日志窗口的自证行
    check("★ 面板窗口 vs 日志窗口" in bench_src and "**不一致, 面板与日志脱钩了**" in bench_src,
          "日志印「面板窗口 vs 日志窗口」自证行, 不一致要显式喊出来")
    check("低于 60 FPS 的帧" not in _bls2,
          "⚠️ 面板上的「低于 60 FPS 的帧」已经删掉 —— 绝对门槛, 而且它和卡顿帧不是同一批帧")
    check("卡顿帧分布" in _bls2 and "jank_groups" in _bls2,
          "分布那一行的口径统一到卡顿帧(原来用的是最慢 1%, 两行会对不上)")
    # 慢帧分布(玩家 2026-09-14 追加「额外新增一个慢帧分布」)。
    # ⚠️ 它必须读 `slow_groups`(阈值 SLOW_RATE), **不能**读 `slow2_n` 那批。
    check("慢帧分布" in _bls2 and "slow_groups" in _bls2
          and "slow2_n" not in _bls2 and "slow2_ms" not in _bls2,
          "慢帧分布读的是 `slow_groups`(<75% 那一档), 不是 `slow2_*` 那批")
    check('_st = (groups or []) if d.get(n_key) else []' in bench_src
          and 'low1_groups' not in _bls2,
          "⚠️ 该档为 0 时不许退回 low1_groups —— 否则印着分布却写着共 0 帧"
          "(守卫现在在共用的 `_dist_line_of` 里, 两条分布一起受管)")
    check(_bh.count("Widget(size_hint_y=1)") >= 2,
          "空态用上下两根 `size_hint_y=1` 的弹簧把内容**竖向居中**(不是改高度)")
    check("content.add_widget(Widget(size_hint_y=1))" in _bh
          and _bh.find("Widget(size_hint_y=1)") > _bh.find("if not self.bench_history:"),
          "弹簧只加在**空态**分支里 —— 有记录时那块是 size_hint=(1,1) 的 ScrollView, "
          "它自己会吃掉剩余空间, 再加弹簧反而挤掉列表")

    # ---- 重建来源不许静默截断 ----
    # 采集端 `sorted(...)[:4]`, 打印端原来只印 4 项 ⇒ 真机 76 次 vs 四项相加 68, 差 8 次
    # 且**没有任何提示**。现在必须印出「其余N类合计」和「合计」。
    _h_rest = _LogHost(_g, _t, _x)
    _h_rest._bench_diag = dict(_h_rest._bench_diag)
    _h_rest._bench_diag.update({"texupd": 76, "texupd_by": [("状态栏", 35), ("其他文字", 16),
                                                           ("统计", 12), ("余额", 5)],
                                "texupd_rest": 8, "texupd_tags": 6})
    _log_rest = _h_rest._bench_frame_log()
    check("其余2类合计 8" in _log_rest and "[合计 76]" in _log_rest,
          "被截断的「重建来源」补上其余N类与合计 —— 76 次与四项相加 68 不再自相矛盾",
          " ".join(l.strip() for l in _log_rest.split("\n") if "重建来源" in l)[:130])
    check("[合计 7]" in _log_cpu and "其余" not in _log_cpu.split("重建来源")[1].split("\n")[0],
          "阴性对照: 没有截断(合计==列出的和)时**不印**「其余」那一段, 不凭空造一行")
    check("[:4]" in bench_src and "texupd_rest" in bench_src and "texupd_tags" in bench_src,
          "采集端把「前 4 名 + 其余合计 + 标签总数」一起存下来(不是只改打印端)")

    # 行为③: 真复制 + 真取回(桌面剪贴板)。失败**必须返回 False**, 不许静默成功。
    _h = _LogHost(_g, _t, _x)
    try:
        from kivy.core.clipboard import Clipboard
        _ok = _h._copy_bench_log()
        _back = Clipboard.paste()
        check(_ok and _back == _log,
              "复制走真剪贴板且往返逐字一致(复制按钮的全部意义就在这条路径上)")
    except Exception as _e:
        check(False, "复制路径抛异常", repr(_e))
    _m_cp = re.search(r"def _copy_bench_log\(self.*?\n    def ", bench_src, re.S)
    _cp_body = _code_only(_m_cp.group(0) if _m_cp else "")
    check("btn.text" in _cp_body and "except" in _cp_body,
          "复制失败会把原因写在按钮上(不静默失败 —— 静默失败等于让人白等一次出包)")

    # ---- 保存成 txt(玩家 2026-09-15: "复制改为下载 txt, 这样就不缺少东西") ----
    # 剪贴板在真机上会截断, 所以必须落盘。桌面能验的只有"文件真的写出来了、内容逐字一致"。
    _h2 = _LogHost(_g, _t, _x)
    try:
        _sok, _smsg = _h2._bench_save_log()
        _sp = _smsg.replace("已保存: ", "").split("（")[0].strip()
        _saved = io.open(_sp, encoding="utf-8").read() if _sok and os.path.exists(_sp) else ""
        check(_sok and _saved == _log,
              "保存出来的 txt 与内存里那份**逐字一致**(少一个字节都算白跑)",
              "%d 字节 -> %s" % (len(_saved.encode("utf-8")), _smsg[:70]))
        try:
            os.remove(_sp)
        except Exception:
            pass
    except Exception as _e:
        check(False, "保存路径抛异常", repr(_e))
    _m_sv = re.search(r"def _bench_save_log\(self.*?\n    def ", bench_src, re.S)
    _sv_body = _code_only(_m_sv.group(0) if _m_sv else "")
    check("MediaStore" in _sv_body,
          "安卓端走 MediaStore 写**公共 Download 目录**(写 user_data_dir 的话文件管理器看不见 = 等于没存)")
    check("getBytes" in _sv_body,
          "写流时用 `java.lang.String.getBytes` 拿到真 byte[] —— 直接给 Python bytes 可能被 pyjnius "
          "挑中 `write(int)` 重载, 于是只写进去一个字节(静默截断成一个字符)")
    check("_copy_bench_log()" in _sv_body,
          "逐级降级全部失败后退回剪贴板(复用已测过的那个方法, 不另写一份)")

    # ---- 25. 输入锁改走触摸层(不再逐个按钮 `disabled` —— 那是 1% Low 的头号来源) ----
    # 真机日志显示: 每一发两次"15~16 个文字纹理重建"的爆发, 间隔正好一个蓄力时长。
    # 桌面点名 + 二分定位到 **`_set_controls_enabled` 里逐个按钮改 `.disabled`**:
    # Kivy 的 `Label` 把文字颜色**烘进纹理**, 而 `label.py` 在 `disabled` 变化时会重排 ⇒
    # 12 个按钮 = 那一帧 12~21ms。改成触摸层吞掉之后, 同一次发射的重建从 **19 次降到 7 次**。
    print("\n[25] 输入锁: 触摸层吞掉, 不再动 Button.disabled")

    _m_ce = re.search(r"def _set_controls_enabled\(self.*?\n    def ", ui_base_src, re.S)
    _ce_body = _code_only(_m_ce.group(0) if _m_ce else "")
    # ⚠️ 断言只认**赋值语句**(`\.disabled\s*=`) —— 第一版写成 `".disabled" not in body`,
    #    结果被函数**说明文字**里那句"绝不再改 `btn.disabled`"当场骗红(自证踩坑)。
    _ce_dis = [ln for ln in _ce_body.splitlines() if re.search(r"\.disabled\s*=", ln)]
    check(not _ce_dis,
          "_set_controls_enabled 里**一处 `.disabled` 赋值都不许有**(每个 = 一次文字纹理重排)",
          "; ".join(ln.strip()[:60] for ln in _ce_dis[:3]))
    # ⚠️ 锚点重映射: 新版把"写标志"那一半搬进了 `Game._controls`(无头状态机),
    #    `UiMixin._set_controls_enabled` **故意不写它**(写了就是第二个状态源)。
    #    判据("输入锁 = 写标志, 不是逐个按钮 disabled")没变。
    check("self._controls_enabled = bool(enabled)" in _fn_body(game_src, "_controls"),
          "它改成写输入锁标志")
    # ⚠️⚠️ 上面那条的**背面**(2026-09-19 新增, **老版没有这一条** —— 老版 `_set_controls_enabled`
    #    一个函数里"置位 + 染色"同体, 那条断言落在它身上就完整了)。
    #    新版劈成 `Game._controls`(置位) + `UiMixin._set_controls_enabled`(染色) ⇒ 只查
    #    `Game._controls` 的话, "UI 那半边偷偷也写一份标志" —— 也就是老版那句"必须写标志"的
    #    **反面**, 第二个状态源 —— 就**没有任何断言覆盖**。等价形式: 全树只许有一个 `bool(...)`
    #    写入点(初始化那次 `= True` 不算, 它不是"输入锁"这条路)。
    _flag_writes = [ln for ln in SHIPPED.splitlines()
                    if "_controls_enabled = bool(" in ln and not ln.strip().startswith("#")]
    check(len(_flag_writes) == 1,
          "输入锁标志**全树只有一个写入点**(`Game._controls`) —— UI 那半边不许再写一份: "
          "写了就是第二个状态源, 而「置灰」读的是 `Game` 那个位, 两半各说各话且不报错",
          "写入点 %d 处: %s" % (len(_flag_writes),
                              "; ".join(ln.strip()[:60] for ln in _flag_writes[:3])))
    # 全文件级别: `.disabled` 的**赋值**必须绝迹(只允许出现在注释里)
    _dis_assign = [ln for ln in SHIPPED.splitlines()
                   if ".disabled" in ln and "=" in ln.split(".disabled")[-1][:3]
                   and not ln.strip().startswith("#")]
    check(not _dis_assign,
          "整个出货文件里没有一处 `.disabled =` 赋值(否则贵的写法又回来了)",
          "; ".join(ln.strip()[:60] for ln in _dis_assign[:3]))

    # 守卫的行为: 锁着时必须**在碰 touch 之前**就返回 True。
    # ⚠️ 拿 `None` 当 touch 直接调 —— 只有"提前 return"的实现才会返回 True 而不是抛异常。
    class _LockHost(object):
        """输入锁那半边的宿主。⚠️ 新版锁标志住在 `Game._controls_enabled`(老版在
        RootWidget 上), 所以夹具持一个真的 `Game` —— 问的仍是出货那个
        `on_touch_down_allowed()`。"""
        def __init__(self, locked):
            self.game = Game(config_path=None, history_path=None)
            self.game._controls_enabled = locked
            self.game.take_events()

    _LockHost.on_touch_down = PLAY.PlayMixin.__dict__["on_touch_down"]
    check(_LockHost(False).on_touch_down(None) is True,
          "锁着时 on_touch_down 直接吞掉(在碰 touch 之前就 return True)")
    _raised = False
    try:
        _LockHost(True).on_touch_down(None)
    except Exception:
        _raised = True
    check(_raised,
          "解锁时它**走正常派发**(拿 None 当 touch 会抛 —— 说明没有把所有人都吞掉)")
    # ⚠️ 这里**没有**验证"真手指按下去→松手→球发出去"这条端到端链路: 桌面构造 Kivy touch
    #    三个坑(profile / depack 吃序列 / pos 读 ox,oy)没趟平, 探针跑不通。

    # ======================================================================
    # [25b] 按钮底色: **唯一取值口** + "不可用"必须保留身份色(2026-09-17 玩家报"置灰逻辑混乱")
    #
    # 病根: 「不能点」和「没选中」原先共用 `COL_BTN_OFF` ⇒ 飞行中选中的档位被涂成
    #       和旁边没选的一模一样, "我押的是哪一档"从画面上消失。
    # 治法: 不可用 = **身份色朝 `COL_BG` 混一档**(`dim_rgb`), 所有底色只在
    #       `_restyle_buttons` 里算一次。
    # ⚠️ 下面那两条**行为**断言是本次改动的真门禁 —— 第一版写的是"逐通道乘一个系数",
    #    算术上 `COL_BTN_OFF`x0.55 对 `COL_BG` 只差 7/255, 按钮会从"凸起"翻成"凹陷"。
    # ======================================================================
    print("\n[25b] 按钮底色: 唯一取值口 + 不可用保留身份色")

    check("def _restyle_buttons(self)" in ui_base_src,
          "有 `_restyle_buttons`(按钮底色的唯一取值口)")
    check("_restyle_selects" not in SHIPPED,
          "旧的 `_restyle_selects` 已整个消失(它不感知输入锁, 禁用期会把选中项刷亮)")
    # ⚠️ `dim_rgb` 在新版住 `danzhu/config.py`(老版在 main.py 的配色块之后) —— 判据不变。
    check(cfg_src.find("def dim_rgb(") > cfg_src.find("COL_BG = "),
          "`dim_rgb` 定义在配色块**之后** —— `keep`/`base` 是默认参数, 在 `def` 那一刻求值, "
          "放前面 = import 期 NameError(同 `_TINT_BRIGHT` 的坑)")

    _leak = []
    for _fn in ("set_bet", "set_rtp", "toggle_mute", "_refresh_mute_btn",
                "_set_controls_enabled"):
        _m = None
        for _s in (PLAY.PlayMixin.__dict__, UIB.UiMixin.__dict__, GAME.Game.__dict__):
            if _fn in _s:
                _m = re.search(r"def %s\(self.*?\n    def " % re.escape(_fn),
                               SRC["play"] if _s is PLAY.PlayMixin.__dict__ else
                               (SRC["ui_base"] if _s is UIB.UiMixin.__dict__ else SRC["game"]),
                               re.S)
                break
        for _ln in _code_only(_m.group(0) if _m else "").splitlines():
            if re.search(r"background_color\s*=", _ln):
                _leak.append("%s: %s" % (_fn, _ln.strip()[:44]))
    check(not _leak,
          "底色只在 `_restyle_buttons` 里写 —— 这 5 个函数体里一处 `background_color` 赋值都没有",
          "; ".join(_leak[:3]))

    # ⚠️ 老版 `_frame` 是"状态机 + 界面"一个函数; 新版那一拍被拆成 `_frame`(壳)+ `_dispatch`
    #    (事件派发)。判据守的是"**每帧那条路上** background_color 的赋值只有 1 处(蓄力期发射键
    #    的力度色)" ⇒ 锚点取这两段合起来。
    _m_fr = re.search(r"def _frame\(self.*?\n    def ", play_src, re.S)
    _fr_body = _code_only(_m_fr.group(0) if _m_fr else "")
    # ⚠️ 新版把老版 `_frame` 里那一大坨拆成了「壳 + 两级派发」, 所以"每帧那条路"是
    #    这三段合起来(`_frame` / `_dispatch` / `_dispatch_one`)。
    _fr_path = (_fr_body + "\n" + _code_only(_fn_body(play_src, "_dispatch"))
                + "\n" + _code_only(_fn_body(play_src, "_dispatch_one")))
    _fr_bg = [ln.strip()[:56] for ln in _fr_path.splitlines()
              if re.search(r"background_color\s*=", ln)]
    check(len(_fr_bg) == 1,
          "`_frame` 里的 `background_color` 赋值**仍然只有 1 处**(蓄力期发射键的力度色)",
          "实测 %d 处: %s" % (len(_fr_bg), "; ".join(_fr_bg)))
    # ⚠️ 判据守的是"**每帧**不许调 `_restyle_buttons`(12 次赋值/帧)" —— 新版 `_frame` 自己
    #    一次都没调, 调用点挂在**事件**上(`_dispatch` 收到 `restyle_buttons` 才调)。
    check("_restyle_buttons" not in _fr_body,
          "`_frame` **不许**调 `_restyle_buttons`(12 次赋值/帧, 还会跟力度色抢帧)")

    # ---- 行为: 直接调出货的那个 `dim_rgb` 算, 不复制公式 ----
    _w = tuple(int(round(c * 255)) for c in CFG.hex_rgb(CFG.COL_BG))
    _off = tuple(int(round(c * 255)) for c in CFG.hex_rgb(CFG.COL_BTN_OFF))
    _dim_off = tuple(int(round(c * 255)) for c in CFG.dim_rgb(CFG.COL_BTN_OFF))
    _dim_sel = tuple(int(round(c * 255)) for c in CFG.dim_rgb(CFG.COL_BTN))
    check(all(_w[i] <= _dim_off[i] <= _dim_sel[i] for i in range(3)),
          "序不许反转: 窗口底色 <= 不可用(未选中) <= 不可用(选中), 逐通道成立",
          "底=%s 暗未选=%s 暗选中=%s" % (_w, _dim_off, _dim_sel))
    _gap = max(_dim_off[i] - _w[i] for i in range(3))
    check(_gap >= 15,
          "不可用的未选中档仍**亮于窗口底色** Δmax>=15(按钮不许被压到背景以下)",
          "实测 Δmax=%d (底=%s 暗=%s)" % (_gap, _w, _dim_off))
    _sep = max(_dim_sel[i] - _dim_off[i] for i in range(3))
    check(_sep >= 40,
          "不可用时「选中 vs 未选中」仍分得开(玩家要的: 看得出我押的是哪一档)",
          "实测 Δmax=%d" % _sep)
    # 阴性对照: 把当年的错写法(逐通道乘系数)在同一个颜色上算一遍, 它必须**更差** ——
    # 否则说明上面那条"不许压到背景以下"没有分辨力(恒真)。
    _mul = tuple(int(round(c * CFG.BTN_OFF_DIM)) for c in _off)
    _mul_gap = max(_mul[i] - _w[i] for i in range(3))
    check(_mul_gap < _gap,
          "阴性对照: 逐通道乘系数(old 写法)的可见度**确实更差** —— 判据有分辨力",
          "乘法 Δmax=%d < 混合 Δmax=%d" % (_mul_gap, _gap))

    # ======================================================================
    # [26] 装杯球层: 矩阵栈必须**配对**(2026-09-14 画面事故实修)
    # ======================================================================
    # 病根: `_redraw` 从"每帧 clear + 重建"改成**持久指令表**之后, 球层第一版把
    #       `_draw_bead` 里那对 `PushMatrix/PopMatrix` 漏掉了。而 Kivy 的 `Rotate` 是
    #       **上下文指令** —— 它作用于其后**所有**指令, 直到被 Pop 掉。更狠的是**球层后面
    #       紧跟着前层玻璃**, 于是整块玻璃被拧飞。玩家原话:「落杯动画一塌糊涂」。
    # ⚠️ 当时没抓到是因为验收探针把两版指令流 dump 出来逐条比、报"逐条一致" ——
    #    而它的 `SRC = sys.argv[1]` **赋了值却从没被用过**, 等于把同一份文件跟自己比了两遍。
    print("")
    print("[26] 装杯球层: PushMatrix/PopMatrix 必须配对, 且不许把前层玻璃卷进去")
    _mx = WinPileFX.__new__(WinPileFX)
    WinPileFX.__init__(_mx, None)
    _mx.pos = (0.0, 0.0)
    _mx.size = (400.0, 520.0)
    _mx._rng = random.Random(12345)
    WinPileFX._make_balls(_mx, 6, 10, 1)     # 用**真球**(手搓球字典太脆, 本工程反复警告)
    _mx.mode = "win"                            # `_layers` 在 win 分支返回常量, 必定画得出来
    _mx._redraw()
    _ch = list(_mx.canvas.children)
    _nb = len(_mx._balls)
    _push = [i for i, o in enumerate(_ch) if isinstance(o, PushMatrix)]
    _pop = [i for i, o in enumerate(_ch) if isinstance(o, PopMatrix)]
    _rot = [i for i, o in enumerate(_ch) if isinstance(o, Rotate)]
    check(len(_push) == _nb and len(_pop) == _nb and len(_rot) == _nb,
          "每颗球各一对 PushMatrix/PopMatrix + 一条 Rotate(丢一对 = 该球的旋转叠到后面所有指令上)",
          "球 %d / Push %d / Rotate %d / Pop %d" % (_nb, len(_push), len(_rot), len(_pop)))
    _d = 0
    _neg = 0
    _depth_at = []
    for _o in _ch:
        if isinstance(_o, PushMatrix):
            _d += 1
        elif isinstance(_o, PopMatrix):
            _d -= 1
            if _d < 0:
                _neg += 1
        _depth_at.append(_d)
    check(_neg == 0 and _d == 0,
          "矩阵栈不倒挂、且结束时归零", "越界 %d 次, 结束深度 %d" % (_neg, _d))
    # ⚠️ **咬人的是这一条**: 只数个数、只验归零都不够 —— 把 6 个 Push 全堆在球层开头、
    #    6 个 Pop 全堆在结尾, 个数对、深度也归零, 而中间每颗球照样互相污染。
    _bad = ["Rotate@%d" % _i for _i in _rot if not isinstance(_ch[_i - 1], PushMatrix)]
    check(not _bad, "每条 Rotate 都紧跟在一条 PushMatrix 之后(而不是被漏在外面)",
          "; ".join(_bad[:3]))
    # 最后一条: **前层玻璃必须画在深度 0 处** —— 这正是玩家看到的那一幕。
    _bt, _ft, _fb2 = WFX._glass_textures()
    _i_front = None
    if _ft is not None:
        for _i, _o in enumerate(_ch):
            if isinstance(_o, Rectangle) and getattr(_o, "texture", None) is _ft:
                _i_front = _i
    if _i_front is None:
        check(False, "夹具定位不到前层玻璃矩形(门禁自身失效, 不是通过)")
    else:
        check(_depth_at[_i_front] == 0,
              "前层玻璃画在矩阵栈归零之后(否则杯子被球层残留的旋转拧走)",
              "深度 %d @%d" % (_depth_at[_i_front], _i_front))

    # ======================================================================
    # [28] 远侧环独立层 `over` 必须真的被画出来(2026-09-14)
    # ======================================================================
    # ⚠️ 这一层曾经**代码与门禁一起消失**(9/14「适配当前基线」), 于是设计稿里的硬切
    #    又露出来 —— 玩家报的「还是有接缝」。**静默回归的代价由玩家付**, 所以这条必须有。
    print("")
    print("[28] 远侧环独立层 over: 必须加载 + 必须画在压暗之后、补画之前")
    _ovt = WFX._glass_over() if hasattr(WFX, "_glass_over") else None
    check(_ovt is not None and tuple(_ovt.size) == (1600, 920),
          "`glass_tumbler_over.png` 能加载且是 1600x920",
          "无" if _ovt is None else str(tuple(_ovt.size)))
    _bt3, _ft3, _fb4 = WFX._glass_textures()
    if _bt3 is not None:
        _fx3 = WinPileFX.__new__(WinPileFX)
        WinPileFX.__init__(_fx3, None)
        _fx3.pos = (0.0, 0.0)
        _fx3.size = (400.0, 520.0)
        _fx3._rng = random.Random(12345)
        WinPileFX._make_balls(_fx3, 20, 100, 1)
        _fx3.mode = "win"
        WFX._GLASS_RIM_TEX.clear()
        _fx3._tbl_ok = False
        _fx3._redraw()
        _ks = [e[0] for e in _fx3._fx_pre]
        check("oseam" in _ks,
              "表里有 `oseam` 这一条 —— 摘掉它 = 接缝回来",
              "次序: %s" % ",".join(_ks[:6]))
        if "oseam" in _ks and "dim" in _ks and "rim" in _ks:
            # ⚠️ 位置: 压暗之后(否则被压暗盖掉)、补画之前。
            check(_ks.index("dim") < _ks.index("oseam") < _ks.index("rim"),
                  "`oseam` 排在 **压暗之后、补画之前**(次序红线不动)",
                  "dim=%d oseam=%d rim=%d" % (_ks.index("dim"), _ks.index("oseam"),
                                              _ks.index("rim")))
            # ⚠️ **有 over 时杯口那一段补画要让位**(否则同一圈环画两遍 ⇒ 亮过头)。
            _n_rim = _ks.count("rim")
            check(_ovt is None or _n_rim == WFX.RIM_BAND_STRIPS,
                  "有 over 时 **杯口那 12 段补画让位**(只留杯底那段), 不会把环画两遍",
                  "rim=%d 期望=%d" % (_n_rim, WFX.RIM_BAND_STRIPS))
    # ⚠️ 阴性对照: 把 `_glass_over` 替成 None, `oseam` 应当消失, 且补画回到 24 段。
    if _bt3 is not None and _ovt is not None:
        _save = WFX._glass_over
        WFX._glass_over = lambda: None
        _fx3._tbl_ok = False
        _fx3._redraw()
        _ks2 = [e[0] for e in _fx3._fx_pre]
        check("oseam" not in _ks2 and _ks2.count("rim") == 2 * WFX.RIM_BAND_STRIPS,
              "阴性对照: 关掉 over ⇒ oseam 消失、补画回到 24 段(判据有分辨力)",
              "oseam=%s rim=%d" % ("oseam" in _ks2, _ks2.count("rim")))
        WFX._glass_over = _save

    # =====================================================================
    # [29] 飞行分段 `_flight_segments`: 判据 + 窗口两头
    # ⚠️ 为什么非要有这一条: 2026-09-20 修那个 bug 时, **两份判据各写一遍**(面板一份、
    #    诊断行一份), 改完只有真正推上模拟器跑一轮才知道对不对 —— 而那一轮**直接把应用
    #    跑崩了**(`None + float`), 因为窗口从**半空中**开始时累加器还是 `None`。
    #    现有门禁一条都没覆盖它(它只在跑分路径上活)。⇒ 做成纯函数 + 合成序列, 桌面就能测。
    # =====================================================================
    print("\n[29] 飞行分段: 判据必须是「严格递减」+ 窗口两头的半截怎么算")

    def _fs(seq):
        """seq = [(阶段, 距本发帧数, 帧间隔ms)] ⇒ 喂给 `_flight_segments` 的帧记录形状。"""
        return [(float(dt), tag, 0, 0, 0, 0.0, 0, 0.0, int(sl), (), 0.0, (0, 0))
                for tag, sl, dt in seq]

    _SEG = BENCH._flight_segments

    # (a) ★ 半空中开头 —— 就是那次崩溃的形状。半截残缺飞行必须**丢掉**, 不是计进去、更不是抛。
    _half = ([('飞行', 500 + i, 6.0) for i in range(4)] + [('落袋', 504, 6.0)]
             + [('装杯', 505, 6.0)] * 2 + [('待机', 507, 6.0)] * 2
             + [('蓄力', 509, 6.0)] * 2 + [('飞行', i, 6.0) for i in range(9)]
             + [('落袋', 9, 6.0)])
    try:
        _r = _SEG(_fs(_half))
        _ok = (len(_r) == 1 and _r[0][0] == 60.0)
        _d = "段数 %d(期望 1) · 长度 %s ms(期望 60: 9 飞行 + 1 落袋) · 半截飞行已丢弃" % (
            len(_r), [v for v, _, _ in _r])
    except Exception as _e:
        _ok, _d = False, "**抛了** %s: %s   ← 窗口从半空中开始就崩" % (type(_e).__name__, _e)
    check(_ok, "窗口从**半空中**开始时: 半截飞行丢弃、且不抛(那次崩溃的回归闸)", _d)

    # (b) ★ 相邻两帧计数**相等** —— 这就是玩家报的那个 bug 的形状。
    #     `<=` 判据会在这里多切一段(段数 2), 严格 `<` 必须只切 1 段。
    _eq = ([('待机', 6134, 3.0), ('待机', 6134, 1.2)]
           + [('待机', 6135 + i, 6.0) for i in range(4)]
           + [('蓄力', 6140, 6.0)] * 2 + [('飞行', i, 6.0) for i in range(6)]
           + [('落袋', 6, 6.0)])
    _r = _SEG(_fs(_eq))
    check(len(_r) == 1 and _r[0][0] == 42.0,
          "相邻两帧计数**相等**时不许开新段(左端 6134/6134 —— 玩家报的 bug 的形状)",
          "段数 %d(期望 1; `<=` 判据会给 2) · 长度 %s ms(期望 42)" % (len(_r), [v for v, _, _ in _r]))

    # (c) 正常形状: 待机开头 + 两次发射 ⇒ 2 段, 且**装杯不算进段长**(阴性对照)
    _two = ([('待机', 6000 + i, 6.0) for i in range(5)]
            + [('蓄力', 6005 + i, 6.0) for i in range(3)]
            + [('飞行', i, 6.0) for i in range(10)] + [('落袋', 10, 6.0)]
            + [('装杯', 11, 6.0)] * 50                      # ← 大量装杯帧
            + [('待机', 61, 6.0)] * 2 + [('蓄力', 63, 6.0)] * 2
            + [('飞行', i, 6.0) for i in range(8)] + [('落袋', 8, 6.0)])
    _r = _SEG(_fs(_two))
    check(len(_r) == 2 and _r[0][0] == 66.0 and _r[1][0] == 54.0,
          "两次发射 ⇒ 2 段, 且**装杯帧不计入段长**(50 个装杯帧一个都没算进去)",
          "段数 %d(期望 2) · 长度 %s ms(期望 66 / 54)" % (len(_r), [v for v, _, _ in _r]))

    # (d) 真发射(计数从上千打到 1)必须认得出 —— 这条防的是有人把判据改回 `== 0`
    _one = ([('待机', 1400, 6.0)] + [('飞行', 1, 6.0)] + [('飞行', 2, 6.0)]
            + [('落袋', 3, 6.0)])
    _r = _SEG(_fs(_one))
    check(len(_r) == 1 and _r[0][0] == 18.0,
          "发射帧记下来的计数是 **1**(不是 0)时也必须切得出段 —— 防判据改回 `== 0`",
          "段数 %d(期望 1) · 长度 %s ms(期望 18)" % (len(_r), [v for v, _, _ in _r]))

    # =====================================================================
    # [30] 跑分采样窗口必须覆盖 **5 发完整飞行**
    # ⚠️ 2026-09-20 真机日志暴露: 原来一看到 `_launch_count >= 5` 就关窗, 而那个计数是
    #    `start_charge()` 那一刻 +1 的(球 0.1 秒后才发射) ⇒ 第 5 发**还在蓄力**窗口就关了
    #    ⇒ 窗口里只有 4 发的完整飞行, 而面板印的「每次飞行平均持续」就是拿这 4 发算的。
    #    真机铁证: `蓄力→飞行` 只切换 4 次, 且窗口最后 12 帧全是「蓄力」。
    # =====================================================================
    print("\n[30] 跑分采样窗口: 第 5 发的飞行必须被算进去（不能它一蓄力就关）")

    class _WinHost(object):
        def __init__(self):
            self._launch_count = 5
            self._target_launches = 5
            self._last_ball_flew = False
            self._last_wait_t0 = 0.0
            self._closed = False
            self.game = types.SimpleNamespace(state="charging")

        def _finish_render_sample(self, dt):
            self._closed = True

    _WinHost._auto_launch_tick = BENCH.BenchMixin.__dict__["_auto_launch_tick"]

    _h = _WinHost()
    _h._auto_launch_tick(0.1)
    _a = (not _h._closed) and (not _h._last_ball_flew)       # 蓄力期: 不关、也没"飞出过"
    _h.game.state = "flying"
    _h._auto_launch_tick(0.1)
    _b = (not _h._closed) and _h._last_ball_flew             # 起飞: 只记标记, 不关
    _h.game.state = "landing"
    _h._auto_launch_tick(0.1)
    _c = not _h._closed                                      # 落袋: 还不关
    _h.game.state = "ready"
    _h._auto_launch_tick(0.1)
    _d = _h._closed                                          # 飞完了: 关
    check(_a and _b and _c and _d,
          "第5发**蓄力期不关窗** → 起飞不关 → 落袋不关 → **飞完才关**"
          "（原来一进蓄力就关 ⇒ 只统计到 4 发）",
          "蓄力%s · 飞行%s · 落袋%s · 飞完关窗%s" % (_a, _b, _c, _d))

    _h2 = _WinHost()
    _h2.game.state = "misfire"
    _h2._auto_launch_tick(0.1)
    check(_h2._last_ball_flew and not _h2._closed,
          "哑火(`misfire`)也算「飞出去了」—— 它是真的发射过(球飞不出竖井), "
          "不认它就会一直等下去", "flew=%s closed=%s" % (_h2._last_ball_flew, _h2._closed))

    _h3 = _WinHost()
    _h3._last_wait_t0 = time.perf_counter() - 11.0           # 假造"已经等了 11 秒"
    _h3._auto_launch_tick(0.1)
    check(_h3._closed,
          "兜底(绝不软锁): 状态机不按预期走时最多等 10 秒也把窗口关掉 —— "
          "跑分链上卡死 = 玩家点完跑分再也回不来")

    print("\n== 结果: %s ==" % ("全部通过" if not FAIL else "失败 %d 项 -> %s" % (len(FAIL), FAIL)))
    return _parity_verdict(FAIL)


def _fail_names(path):
    """从一份探针输出里抽出 FAIL 的门禁名(按 `[FAIL] 名字  详情` 的**两个空格**切)。

    ⚠️ 老版的 `check()` 是 `"  [%s] %s%s" % (tag, name, ("  " + detail) if detail else "")`,
       所以名字与详情之间恒为**两个空格** —— 按它切名字是可靠的; 单空格不能切
       (名字里本身就有 ` —— ` / `: ` 这类单空格)。
    """
    out = []
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "[FAIL]" in line:
                    body = line.split("[FAIL]", 1)[1].strip()
                    out.append(body.split("  ")[0].strip())
    except Exception:
        return None
    return out


def _parity_verdict(fail):
    """★ 本探针的**验收判据**: FAIL 集合必须与老版**逐条相同**。

    ⚠️⚠️ 判据是「**集合相同**」, **不是**「零失败」。老版 `tools/fx_probe.py` 自己就有
       **18 条 FAIL**（mipmap 没开、「中位跑分」文案已改…）—— 那是老版已知且**接受**的状态。
       ⇒ 多一条 FAIL = 新版真错了; 少一条 = **门禁被削弱了**。两者都算不合格。
    ⚠️ 基线是 `tests/golden/fx_probe_old.txt`（采自老工程，**只读冻结**）。
       读不到就**如实说"没比"**并返回非零 —— 静默跳过就是假绿。
    """
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "golden", "fx_probe_old.txt")
    want = _fail_names(base)
    if want is None:
        print("\n[门禁] ⚠️ 读不到老版基线 %s —— **没有比对**, 上面那份结果不算验收通过" % base)
        return 1
    got = list(fail)
    sw, sg = set(want), set(got)
    extra = sorted(sg - sw)
    missing = sorted(sw - sg)
    print("\n== 与老版基线对账 ==")
    print("   老版 %d 条 FAIL / 新版 %d 条" % (len(want), len(got)))
    if not extra and not missing:
        print("   [绿] FAIL 集合**逐条相同** —— 与老版行为对等")
        return 0
    if extra:
        print("   [红] 新版**多** FAIL %d 条(新版真错了):" % len(extra))
        for n in extra:
            print("        + %s" % n)
    if missing:
        print("   [红] 新版**少** FAIL %d 条(门禁被削弱了):" % len(missing))
        for n in missing:
            print("        - %s" % n)
    return 1


if __name__ == "__main__":
    sys.exit(main())
