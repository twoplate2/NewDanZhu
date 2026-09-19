"""震动档位表门禁 —— 防"手感分档改了没人会红"。

    python tests/vib_gates.py

## 这条门禁补的是什么洞

震动时长是**玩家手感**（2026-09-14 定稿分档），但它散在 `danzhu/game.py` 的四处
`_emit("vibrate", ...)` 里，而此前**没有任何门禁碰得到**（`fx_gates` 只钉了装杯落珠
那条 `_vibrate_tick`）。改错一个数字、或者把"彩蛋双震"的 `double` 写成 `False`，
**所有门禁照绿**，只有玩家按下去才发现手感变了。

## 判据

不扫源码（源码字符串能被注释喂绿，本工程踩过），**真的驱动 `Game` 到那几个场景**，
读事件流里的 `vibrate`：

| 场景 | 期望 | 老版出处 |
|---|---|---|
| 哑火（power < `MISFIRE_POWER`） | `ms=8, double=False` | `android/main.py:20103 _vibrate(8)` |
| 发射 | `ms=14, double=False` | `:20146 _vibrate(14)` |
| 彩蛋（球跳回发射槽，×2 结算） | `ms=35, double=True` | `:11312 _vibrate_double(ms=35)` |
| 中奖按倍率 | 300/220/150/110/75/45 | `:20287` 的嵌套表达式 |
| 未中（m=0） | **不震** | 同上（`if m > 0`） |

⚠️ 中奖那一档用 `game.multipliers = [m] * 9` 直接控盘 —— 判据取的是**分档边界**
（99→220、49→150、19→110、9→75、4→45），**边界才是容易写错的地方**，档中心不是。
"""

import os
import sys
import warnings

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

warnings.filterwarnings("ignore")

FAIL = []
N_OK = [0]


def check(ok, name, detail=""):
    if ok:
        N_OK[0] += 1
    else:
        FAIL.append(name)
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name, ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def _vibs(g):
    """取走事件流里全部的 vibrate 载荷。"""
    return [e.data for e in g.take_events() if e.kind == "vibrate"]


def main():
    from danzhu.game import Game, MISFIRE_POWER

    print("== 一、三个固定档位(哑火 / 发射 / 彩蛋) ==")

    # ---- 哑火: power < MISFIRE_POWER ----
    g = Game()
    g.start_charge()
    g.power = MISFIRE_POWER * 0.3
    g.launch()
    v = _vibs(g)
    check(v == [{"ms": 8, "double": False}],
          "哑火: 8ms 短震不双震(老版 `_vibrate(8)`)", str(v))

    # ---- 发射 ----
    g = Game()
    g.start_charge()
    g.power = 0.8
    g.launch()
    v = _vibs(g)
    check(v == [{"ms": 14, "double": False}],
          "发射: 14ms 不双震(老版 `_vibrate(14)`)", str(v))

    # ---- 彩蛋: 球跳回发射槽, ×2 结算 ----
    g = Game()
    g._easter_egg = True
    g.settle(0)
    v = _vibs(g)
    check(v == [{"ms": 35, "double": True}],
          "彩蛋: 35ms **双震**(老版 `_vibrate_double(ms=35)`; double 写成 False 就是手感错)",
          str(v))

    print("\n== 二、中奖按倍率分档(取边界, 不取档中心) ==")
    # (倍率 m, 期望 ms) —— 边界两边都要, 那是嵌套三元最容易写错的地方
    CASES = (
        (0, None), (1, 45), (4, 45), (5, 75), (9, 75), (10, 110), (19, 110),
        (20, 150), (49, 150), (50, 220), (99, 220), (100, 300), (5000, 300),
    )
    for m, want in CASES:
        g = Game()
        g.multipliers = [m] * len(g.multipliers)   # 直接控盘(普通属性, 见 game.py:370)
        g.settle(0)
        v = _vibs(g)
        if want is None:
            check(v == [], "m=%d(未中): **不震**" % m, str(v))
        else:
            check(v == [{"ms": want, "double": False}],
                  "m=%-5d ⇒ %dms" % (m, want), str(v))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— 震动四个档位与老版 `_vibrate*` 调用逐档相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
