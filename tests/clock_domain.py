"""时钟域门禁 —— 防「两把钟混着比」这类**结构性**缺陷。

    python tests/clock_domain.py

## 这条门禁补的是什么洞

工程里有**两把钟**:

* `Game.now` —— `step(dt)` 里 `self.now += dt` 从 **0 累加**的**模拟钟**
  (`game.py:263` 初值 / `:464` 唯一推进点);
* `time.time()` —— **墙钟绝对值**(~1.7e9), `WinPileFX` 整个模块用的是它
  (`winfx.py:774-775` 盖章 `_t0 = now + WINDUP`, 模块 docstring 明写"时基一律 time.time()")。

一个"到点没做就兜底"的判据如果**写侧取墙钟、比侧取模拟钟**, 就是
「~20 秒 vs ~1.7e9 秒」⇒ **恒假、兜底变死代码**, 而且**真机上才发作**。

⚠️⚠️ **这类缺陷在门禁下结构性不可见**: 轨迹台(`tests/trace.py`)的假钟让**两边都从 0 起、
加同一串 dt** ⇒ 两个浮点数**逐位相等**, `trace_parity` 全绿。这是本工程的母题
「**夹具自己制造绿**」的又一个实例 —— 2026-09-19 就是这么漏掉 `_reveal_deadline` 的
(写侧 `self._fx.reveal_at()` 是墙钟, 比侧 `self.now` 是模拟钟)。

## 判据

用**真墙钟的假 FX**(照 `winfx.py` 的形状, `reveal_at()` 返回 `time.time()+N`)
驱动真 `Game`, 然后:

1. **夹具自检**: `reveal_at()` 必须 > 1e6 —— 否则两把钟没分开, 下面全是空转;
2. **域不变式**: 驱动一整局(中奖/未中/彩蛋/哑火/蓄力), 每一步之后
   `Game` 上**所有跟模拟钟比的时刻字段**都必须落在模拟钟域(< 1e6);
3. **行为后果**: 装杯 tick 停摆时, 揭晓兜底**真的会触发**(`reveal_fallback` 事件)。
"""

import os
import sys
import time
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

DT = 1.0 / 60.0
FAIL = []
N_OK = [0]

# `Game` 上**参与 `now` 比较**的时刻字段(逐个在 game.py 里核过赋值与比较两侧)。
# ⚠️ 加新字段时这里也要加 —— 漏加 = 那条字段没人守。
SIM_FIELDS = ("now", "_coin_start", "_coin_until", "_reveal_deadline",
              "_result_until", "_charge_start", "_last_charge_sound",
              "_anim_start_time", "landed_at", "_anim_start_balance")

# 模拟钟从 0 累加(跑一小时也才 3600); 墙钟是 1.7e9 量级。这条线把两者劈开。
WALL_LIMIT = 1e6


def check(ok, name, detail=""):
    if ok:
        N_OK[0] += 1
    else:
        FAIL.append(name)
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                            ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


class WallFX(object):
    """真机形态的装杯演出桩: **时基是 `time.time()` 绝对值**(照 `winfx.py:774-775`)。

    `mode` 恒为 `"busy"` ⇒ 正常揭晓路径永远不走 ⇒ 只有兜底能救。
    """

    mode = "busy"

    def __init__(self, delay=0.8):
        self._t0 = time.time() + 0.5
        self._delay = delay
        self.done = None

    def play_win(self, m, bet, on_done=None, auto_close=False):
        self.done = on_done
        return True                      # 排上了 ⇒ settle 不立刻走兜底

    def busy(self):
        return True

    def reveal_at(self):
        return self._t0 + self._delay

    def expected_sec(self, m):
        return 3.0

    def tick(self, now=None):
        pass

    def request_close(self):
        return False


def _drive(g, frames):
    out = []
    for _ in range(frames):
        out.extend(g.step(DT))
    return out


def _bad_domain(g):
    """返回落在**墙钟域**的字段名 —— 空 = 全在模拟钟域。"""
    bad = []
    for f in SIM_FIELDS:
        try:
            v = getattr(g, f)
        except AttributeError:
            continue
        if isinstance(v, float) and v >= WALL_LIMIT:
            bad.append((f, v))
    return bad


def main():
    from danzhu.game import Game, START_BEADS

    print("== 一、夹具自检: 两把钟真的分开了吗 ==")
    fx = WallFX()
    check(fx.reveal_at() >= WALL_LIMIT,
          "假 FX 的 `reveal_at()` 是**墙钟绝对值**(否则下面全是空转)",
          "%.1f" % fx.reveal_at())

    print("\n== 二、域不变式: 驱动一整局, 时刻字段不许跑到墙钟域 ==")
    g = Game(fx=fx, config_path=None, history_path=None)
    g.multipliers = [10] * len(g.multipliers)
    g.balance = START_BEADS
    g.bet = 10

    worst = []
    # (动作, 帧数, 说明) —— 覆盖所有给时刻字段赋值的路径
    STEPS = (
        ("蓄力发射(中奖)", lambda: (g.start_charge(), setattr(g, "power", 0.8), g.launch()),
         12, "settle 中奖支"),
        ("推进到装杯/揭晓", None, 400, "reveal_deadline / _anim_start_time / coin"),
        ("强制结算未中", lambda: g.settle(1), 60, "未中支"),
        ("彩蛋结算", lambda: (setattr(g, "_easter_egg", True), g.settle(0)), 60, "彩蛋支"),
        ("哑火", lambda: (g.start_charge(), setattr(g, "power", 0.01),
                          g.launch(), _drive(g, 60)), 60, "哑火支"),
        ("再发一发", lambda: (g.start_charge(), setattr(g, "power", 0.8), g.launch()),
         600, "整轮"),
    )
    for name, act, n, why in STEPS:
        try:
            if act is not None:
                act()
            _drive(g, n)
            b = _bad_domain(g)
            if b:
                worst.append((name, b))
            check(not b, "%-16s 后所有时刻字段都在**模拟钟域**(< %.0e)" % (name, WALL_LIMIT),
                  "" if not b else "跑到墙钟域了: %r（%s）" % (b, why))
        except Exception as e:
            check(False, "%s 不炸" % name, "%s: %s" % (type(e).__name__, e))

    print("\n== 三、行为后果: 装杯 tick 停摆时, 揭晓兜底**真的会触发** ==")
    fx2 = WallFX(delay=0.4)
    g2 = Game(fx=fx2, config_path=None, history_path=None)
    g2.multipliers = [10] * len(g2.multipliers)
    g2.balance = START_BEADS
    g2.bet = 10
    g2.start_charge()
    g2.power = 0.8
    g2.launch()
    g2.take_events()
    g2.settle(0)
    evs = _drive(g2, 1800)               # 推 30 秒
    hits = [e for e in evs if e.kind == "reveal_fallback"]
    check(len(hits) >= 1,
          "**兜底触发**（`_pending_win` 的揭晓被补出来了 —— 老版这条是真活的）",
          "触发 %d 次；now=%.3f deadline=%.3f"
          % (len(hits), g2.now, float(g2._reveal_deadline or 0.0)))
    check(not _bad_domain(g2),
          "触发时 `_reveal_deadline` 在模拟钟域（可比）",
          "%r" % (_bad_domain(g2),))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（夹具自检 / 域不变式 / 兜底真的活）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
