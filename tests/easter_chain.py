"""彩蛋链门禁 —— 触发判定 / ×2 结算 / 语音档位 / 震动 / 弹窗闸 / 锁 / 跑分拦截。

    python tests/easter_chain.py

## 这条门禁补的是什么洞

彩蛋（球跳回发射槽 ⇒ ×2 结算）此前**只有震动那一环**被钉住
（`tests/vib_gates.py:91-95` 直接 `g._easter_egg = True` 再 `settle`）——
**物理触发判据、×2 的金额、语音档位映射、弹窗注入回调、`_easter_hold` 的持有与释放
一个都没验过**。

⚠️ `_easter_hold` 是"软锁"红线：它挡的是 `park_ball`（`game.py:669-673`）。
   持有时**必须**有一条无条件的释放路径 —— 漏一条，玩家就永久卡在
   "球回来了但下一发按不出来"的状态里。老版为此专门在跑分分支写了
   "不能只 return，否则软锁死"（`android/main.py:20385-20386`）。

⚠️ 另一条容易写错的是**语音档位**：`voice_easter_%d % self.bet`，档位只有
   `{1,10,50,100}` 四个（`ui/winfx.py:41` 的 `BET_COLORS`）。多出一个第 5 档而
   `voice/` 里没有对应 wav ⇒ 静默无声，门禁全绿。
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

DT = 1.0 / 60.0
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


def _game(**kw):
    from danzhu.game import Game
    return Game(config_path=None, history_path=None, **kw)


def _launched(g, bet=10):
    """发一发（让 plays/round_plays 非零，才看得出彩蛋"不计一局"的 -1）。

    ⚠️ 末尾**必须排空事件** —— 蓄力/发射本身会发 `launch` 音、`vibrate 14`、
       `status`「蓄力中」「发射!」。不排空的话它们全混进下面"彩蛋那一发"的收集里,
       判据就变成"彩蛋 + 发射"而不是彩蛋（第一版就是这么报的 7 条假红）。
    """
    g.bet = bet
    g.balance = 1234
    g.start_charge()
    g.power = 0.8
    g.launch()
    g.take_events()
    return g.balance


def _settle_easter(g, slot=0):
    """走到彩蛋结算。返回 (结算前余额, 这一路的事件)。

    ⚠️ `launch()` 会把 `_easter_egg` 清掉（`game.py:769`，老版 `main.py:20129` 同），
       所以标记必须**在 launch 之后**才置。
    """
    before = g.balance
    g._easter_egg = True
    g.settle(slot)
    return before, g.take_events()


def _kinds(evs, kind):
    return [e for e in evs if e.kind == kind]


def main():
    from danzhu.game import START_BEADS, LAND_HOLD

    print("== 一、×2 结算 + **不计一局** ==")
    g = _game()
    b0 = _launched(g, bet=10)
    plays0, round0 = g.plays, g.round_plays
    before, evs = _settle_easter(g)
    check(before == b0,
          "结算前余额就是发射后的余额（先记账基线）", "before=%s b0=%s" % (before, b0))
    check(g.balance == before + 2 * 10,
          "**×2 是加在 balance 上的加法**(`+= 2*bet`), 不经过 `bet * m` 那条路",
          "balance %s -> %s（期望 +20）" % (before, g.balance))
    check(g.plays == plays0 - 1 and g.round_plays == round0 - 1,
          "**不计一局**：plays / round_plays 各退回 1（与哑火一致）",
          "plays %d->%d, round_plays %d->%d"
          % (plays0, g.plays, round0, g.round_plays))
    # 槽号对金额**没有**影响（彩蛋在 `m = multipliers[i]` 之前就 return 了）
    g2 = _game()
    _launched(g2, bet=10)
    b2, _e = _settle_easter(g2, slot=0)
    g3 = _game()
    _launched(g3, bet=10)
    b3, _e = _settle_easter(g3, slot=7)
    check(g2.balance - b2 == g3.balance - b3 == 20,
          "落哪个槽都是 +2×bet（彩蛋支路在取 `multipliers[i]` **之前**就 return）",
          "槽0 +%s / 槽7 +%s" % (g2.balance - b2, g3.balance - b3))

    print("\n== 二、语音档位：`voice_easter_{bet}`, 四档全在 ==")
    for bet in (1, 10, 50, 100):
        g = _game()
        _launched(g, bet=bet)
        _before, evs = _settle_easter(g)
        got = [(e.name, e.gain, e.throttle) for e in evs if e.kind == "sound"]
        want = [("voice_easter_%d" % bet, 1.0, 0.5)]
        check(got == want,
              "bet=%-4d ⇒ **恰好一条** `voice_easter_%d`（gain 1.0 / throttle 0.5）"
              % (bet, bet),
              "实得 %r" % (got,))
    # 静音档 ⇒ 换成 win1 0.6（老版 main.py:20455）
    g = _game()
    _launched(g, bet=10)
    g.sound_mode = "off"
    _before, evs = _settle_easter(g)
    got = [(e.name, e.gain) for e in evs if e.kind == "sound"]
    check(got == [("win1", 0.6)], "静音档 ⇒ 换播 `win1` 0.6", "实得 %r" % (got,))

    print("\n== 三、震动 + **不写状态栏** ==")
    g = _game()
    _launched(g)
    _before, evs = _settle_easter(g)
    vibs = [e.data for e in _kinds(evs, "vibrate")]
    check(vibs == [{"ms": 35, "double": True}],
          "**双震** 35ms（老版 `_vibrate_double(ms=35)`, main.py:11312）", "%r" % (vibs,))
    check(not _kinds(evs, "status"),
          "**不设状态栏文字** —— 彩蛋是唯一一条不写 status 的结算分支"
          "（老版 `main.py:20158` 那行注释）",
          "实得 %r" % ([e.data for e in _kinds(evs, "status")],))

    print("\n== 四、弹窗闸 + `_easter_hold` 的持有与释放（软锁红线） ==")
    # 无回调(无头): 没有模态可等 ⇒ 必须自己解锁, 否则永久软锁
    g = _game()
    _launched(g, bet=10)
    _before, evs = _settle_easter(g)
    pops = _kinds(evs, "easter_popup")
    check(len(pops) == 1 and pops[0].data == {"bet": 10, "payout": 20},
          "发 `easter_popup` 事件（载荷 bet / payout=2×bet）", "%r" % (pops[:1],))
    check(g._easter_popup is not None,
          "`_easter_popup` **占住了**（防重入闸；`easter_finish` 不许清它）")
    check(g._easter_hold is False,
          "**无回调 ⇒ 自动解锁**（无头没有模态可等，不解锁就是永久软锁）")
    # 有回调(真 UI): 等玩家点确定 ⇒ 锁**必须**还持着, 否则 park_ball 会在演出中途跑掉
    calls = []
    g = _game(on_easter_popup=lambda bet: calls.append(bet))
    _launched(g, bet=50)
    _before, evs = _settle_easter(g)
    check(calls == [50], "注入的弹窗回调**被调了一次**、带上 bet", "%r" % (calls,))
    check(g._easter_hold is True,
          "**有回调 ⇒ 锁还持着**（等玩家关窗；演出没完就放行 = 球堆没播完就重掷）")
    # 锁住的当口"落定放行"必须被挡住
    # ⚠️ 守卫在 **`_frame` 的 landed 分支**(game.py:667-673), **不在 `park_ball` 里** ——
    #    直接调 `park_ball` 是绕开守卫的(第一版就是这么写的, 报了个假红)。
    g.state = "landed"
    g.landed_at = g.now - 10.0
    g.step(DT)
    check(g._easter_hold is True and g.state == "landed",
          "**锁住期间 `_frame` 不放行 `park_ball`**（game.py:670-673）",
          "state=%r" % g.state)
    # 关窗 ⇒ 解锁 ⇒ 下一帧就该放行（阳性对照: 证明上面挡住的真是这把锁）
    g.easter_finish()
    g.step(DT)
    check(g._easter_hold is False and g.state == "ready",
          "关窗(`easter_finish`) ⇒ 解锁, **下一帧就放行**（阳性对照）",
          "state=%r" % g.state)
    check(g._easter_popup is not None,
          "解锁**不清** `_easter_popup`（老版 `main.py:20464` 只清 hold；"
          "冒烟夹具靠它判「跑分期间有没有弹窗」）")

    print("\n== 五、跑分期间的彩蛋：不表演 / 账务照走 / **演出中途开跑分必须解锁** ==")
    g = _game()
    _launched(g, bet=10)
    g._bench_running = True
    before, evs = _settle_easter(g)
    check(g.balance == before + 20, "跑分期间 ×2 **账务照走**", "balance=%s" % g.balance)
    check(not _kinds(evs, "easter_popup") and g._easter_popup is None,
          "跑分期间**不表演不弹窗**（`game.py:878-880` 提前 return）")
    check(g._easter_hold is False,
          "跑分分支提前 return ⇒ 没有上锁（它压根没走到 `play_win`）")
    check(g._land_hold == max(0.3, LAND_HOLD - 0.5),
          "跑分期间 `_land_hold` 缩短（不表演 ⇒ 不用等演出）",
          "%.2f" % g._land_hold)

    # ⚠️ 上面那几条**够不着**真正的软锁红线 —— 跑分时 `settle` 提前 return, 那条守卫
    #    (`game.py:1031-1033`) 根本不会被执行。它守的是**另一条路**:
    #    结算时跑分**还没开**(装杯正常排上) → 装杯播到一半玩家进了跑分 → 装杯播完
    #    `on_done` 回来才发现 `_bench_running` 是 True。这时**不能只 return** ——
    #    那样 `_easter_hold` 永远不放, 玩家被软锁死。老版 `main.py:20385-20386` 专门写了这句。
    class _FX(object):
        mode = "busy"                     # 非 idle ⇒ 不许现在弹窗

        def __init__(self):
            self.done = None

        def play_win(self, m, bet, on_done=None, auto_close=False):
            self.done = on_done           # 抓住 on_done, 稍后手动触发
            return True                   # 排上了 ⇒ settle 不会同步走兜底

        def busy(self):
            return True

        def tick(self, now):
            pass

    fx = _FX()
    g = _game(fx=fx)
    _launched(g, bet=10)
    _before, evs = _settle_easter(g)
    check(g._easter_hold is True and fx.done is not None,
          "装杯排上了 ⇒ 上锁 + 拿到 `on_done`（演出期间不许放行）",
          "hold=%r" % g._easter_hold)
    g._bench_running = True               # 演出播到一半, 玩家进了跑分
    fx.done()                             # 装杯播完, on_done 回来
    check(g._easter_hold is False,
          "**演出中途开跑分 ⇒ `on_done` 回来时必须解锁**"
          "（只 `return` = 永久软锁, 玩家再也发不出下一发）",
          "hold=%r" % g._easter_hold)
    check(g._easter_popup is None, "且**不弹窗**（跑分期间不该有模态）")

    print("\n== 六、物理触发判据：`b.x > FIELD_R` ⇒ 标记 + **落定目标是发射槽** ==")
    from danzhu.config import FIELD_R, PLUNGER_X, FLOOR, BALL_R
    from danzhu.game import NUM_SLOTS
    g = _game()
    _launched(g, bet=10)
    # 把球挪进右侧竖井（场区右边、隔着一道墙），让它在那儿落地
    g.ball.x = FIELD_R + 20.0
    g.ball.y = FLOOR - BALL_R - 3.0
    g.ball.vx = 0.0
    g.ball.vy = 150.0
    # ⚠️ 判据取 **`settle` 事件里的 `easter` 字段**, 不取 `g._easter_egg` ——
    #    `settle()` 开头就把它清成 False（game.py:863-864），而 `settle` 与置标记
    #    可能落在**同一帧**里, 循环退出后再读就是"已经被消费掉"的样子（第一版假红）。
    settled = []
    for _ in range(240):
        settled.extend(g.step(DT))
    se = [e for e in settled if e.kind == "settle"]
    check(len(se) == 1 and se[0].data.get("easter") is True,
          "球在心 x > FIELD_R 处落地 ⇒ 结算走**彩蛋支路**（不靠手工赋标记）",
          "settle 事件 %r" % ([e.data.get("easter") for e in se],))
    if se:
        check(se[0].data.get("balance_after") - se[0].data.get("balance_before") == 20,
              "那一发的到账正是 2×bet（bet=10 ⇒ +20）",
              "%s -> %s" % (se[0].data.get("balance_before"),
                            se[0].data.get("balance_after")))
        check(g.land_target_x == PLUNGER_X,
              "落定目标 = **发射槽本身**（不是按 x 算出的槽中心）——"
              "用槽中心会被 LAND_K 弹簧往左拽穿隔墙，球嵌在墙里定格",
              "land_target_x=%s PLUNGER_X=%s" % (g.land_target_x, PLUNGER_X))
        check(se[0].data.get("slot") == NUM_SLOTS - 1,
              "结算槽被 clamp 成最右（竖井在盘面右侧，与弹窗文案「已回到发射槽」一致）",
              "slot=%r" % (se[0].data.get("slot"),))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（触发 / ×2 / 语音档 / 震动 / 弹窗闸 / 锁 / 跑分拦截）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
