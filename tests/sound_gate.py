"""撞钉音的**第一道闸**门禁 —— `_ARNG` 在静音时一格都不许消耗。

    python tests/sound_gate.py

## 这条门禁补的是什么洞

老版 `Sfx.impact` 的**第一句**就是闸（`android/main.py:7044`）：

    if not self.enabled or sp < SFX_MIN_SP.get(bit, 0.0):
        return False

而抽撞钉音色变体的那一句在**它之后**（`:7048` 的 `_ARNG.randint(-1, 1)`）。
⇒ **静音时 `_ARNG` 一个数都不消耗**。这条规矩工程自己白纸黑字写了两处
（`bus.py:55-60`「静音时 `_ARNG` 一个数都不消耗…而且对账看不见」、
`game.py:169-172`「静音时必须一格不消耗」），`audio_gates.py` 的 G11 也在守。

⚠️⚠️ **但 G11 守的不是出货那条路。** 新版把决策抽成了裸函数
`game.impact_sound(bit, sp, enabled)`（`game.py:167-184`），而它内部调的
`pick_peg_variant(t)`（`game.py:180`）**不带闸**；真正带闸的 `Sfx.impact`
（`bus.py:926-945`）在出货流程里**已经不走了**（`_play_events` 改调裸函数）。

⇒ 于是「有没有闸」这件事，G11 测的是**没人调的那条路**。

## 真正的分歧（2026-09-19 第三轮 AST 审计挖到并已修）

`_play_events` 原来传的是 `Game.sfx_enabled` —— **Game 自己记的一份副本**，
而老版那道闸读的是**音效层本体** `Sfx.enabled`。两者在
**三级音频后端全建不起来的设备**上分叉：`Sfx.__init__` 见 `open_output()` 返回 None
就把 `enabled` 置 False，而 `Game.sfx_enabled` 仍是 True。
⇒ 老版一次 `_ARNG` 都不消耗，新版**每次撞钉抽一个** ⇒ 变体流整体错位。

修法：`Game(sound_gate=...)` 可注入，出货路径由 `root.py` 注入 `lambda: self.sfx.enabled`。

## 判据

**不看源码字符串、不看调用次数** —— 直接量 `_ARNG` 的**状态**：
跑一发撞钉，`_ARNG.getstate()` 前后必须**逐位相同**（闸为假时）。

⚠️ 夹具自检：闸为真时**必须**消耗 `> 0` 个数，否则这条判据是**空转**。
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
    line = "  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                            ("  " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def _peg_hit(gate):
    """跑一发撞钉, 返回 `_ARNG` 在那前后的 state（逐位可比）。"""
    from danzhu.game import Game
    from danzhu.audio.synth import _ARNG
    from danzhu.physics import Ball
    import danzhu.config as C

    g = Game(config_path=None, history_path=None,
             sound_gate=(None if gate is None else (lambda: gate)))
    b = Ball(x=C.FIELD_L + 120.0, y=400.0, vx=0.0, vy=300.0, item=None, born=0.0,
             events=0, amp={}, misfire=False, launch_power=0.8, _stall_retry=0,
             _rng=None, last_nx=0.0, last_ny=-1.0, hit_peg=None, squash=1.0,
             squash_nx=0.0, squash_ny=-1.0, spin=0.0)
    _ARNG.seed(12345)
    s0 = _ARNG.getstate()
    g._play_events(int(C.EV_PEG), {int(C.EV_PEG): 600.0}, b)
    s1 = _ARNG.getstate()
    _ARNG.setstate(s0)
    return s0, s1, [e.name for e in g.take_events() if e.kind == "sound"]


def main():
    print("== 一、夹具自检：闸为真时**必须**消耗 `_ARNG` ==")
    s0, s1, names = _peg_hit(True)
    check(s0 != s1,
          "闸=真 ⇒ `_ARNG` 状态**变了**（否则下面的判据是空转）",
          "发声音效 %r" % (names,))

    print("\n== 二、闸为假 ⇒ `_ARNG` **一格都不许消耗**（老版 `Sfx.impact` 首句的语义） ==")
    s0, s1, names = _peg_hit(False)
    check(s0 == s1,
          "闸=假 ⇒ `_ARNG.getstate()` 前后**逐位相同**"
          "（静音时抽了变体 = 变体流与老版整体错位）",
          "消耗了若干个数" if s0 != s1 else "一格未动 ✓")
    check(not names,
          "闸=假 ⇒ 一条 `sound` 事件都不发",
          "实得 %r" % (names,))

    print("\n== 三、不注入 `sound_gate` 时退回 `Game.sfx_enabled`（无头/轨迹台的老口径） ==")
    s0, s1, _n = _peg_hit(None)
    check(s0 != s1,
          "未注入 ⇒ 按 `Game.sfx_enabled`(默认 True) ⇒ 照常消耗"
          "（所以 `trace_parity` 逐位不变 —— 那里 `sfx.enabled` 恒真）",
          "")
    from danzhu.game import Game
    import danzhu.audio.synth as _syn
    _syn._ARNG.seed(12345)
    _a = _syn._ARNG.getstate()
    g = Game(config_path=None, history_path=None)
    g.set_sound_enabled(False)
    from danzhu.physics import Ball
    import danzhu.config as C
    b = Ball(x=C.FIELD_L + 120.0, y=400.0, vx=0.0, vy=300.0, item=None, born=0.0,
             events=0, amp={}, misfire=False, launch_power=0.8, _stall_retry=0,
             _rng=None, last_nx=0.0, last_ny=-1.0, hit_peg=None, squash=1.0,
             squash_nx=0.0, squash_ny=-1.0, spin=0.0)
    g._play_events(int(C.EV_PEG), {int(C.EV_PEG): 600.0}, b)
    check(_syn._ARNG.getstate() == _a,
          "玩家按了「音效已关」(`set_sound_enabled(False)`) ⇒ 退回支也**一格不消耗**",
          "")
    _syn._ARNG.setstate(_a)

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（夹具自检 / 静音零消耗 / 不注入退回支）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
