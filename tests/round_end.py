"""轮次结束链门禁 —— 触发 / 弹窗 / 历史落盘 / 语音排队 / 确定 / 去重闸。

    python tests/round_end.py

## 这条门禁补的是什么洞

「打满一轮 → 弹窗 + 写历史 + 念语音 → 点确定重置」是**每局游戏都会走到**的玩家可见链，
但它在 `tests/` 下**零覆盖**：

* 最接近的 `tests/bench_smoke.py:153` 探的是 `_show_round_settings`（轮次**设定**弹窗），
  **不是** `_show_round_end`，名字像而已；
* `tests/persist_check.py:57` 构造时 `history_path=None` ⇒ 历史落盘那一路**结构性关闭**
  （`game.py:1547` 的早退闸）；
* `tests/trace.py` 的脚本是 12 发球而 `max_plays` 默认 50 ⇒
  `round_plays >= max_plays` **永不成立**，这条链一次都没被调过。

⇒ 所以它不是"被打桩跳过"，是**压根没跑过**。而链上任何一环断掉，玩家被卡在
"无法继续玩"的弹窗里 —— 比崩溃还难受。

## 顺带钉住 2026-09-19 修的一个真分歧

老版的"确定"去重闸是 `_show_round_end` 里**每次新建**的 `_done = [False]` 闭包
（`android/main.py:20782`），置 True 后永不复位。新版做成成员 `_round_end_ack`，
却在 `reset_balance` 里清回 `False`（原 `game.py:1318`）—— 而 `round_end_confirm`
恰恰是"置 True → `reset_balance()`"一口气调完的 ⇒ **那道闸永远放行，是死代码**，
第二次 confirm 会二次重置 + 多发一条 `round_end_close`。已改成在 `show_round_end`
里清（等价于老版"每个弹窗一份新闭包"）。第五节就是这条的判据。
"""

import json
import os
import sys
import tempfile
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


TMP = tempfile.mkdtemp(prefix="danzhu_roundend_")
_N = [0]


def _new_hist():
    """每节一个**全新**的历史文件 —— 共用一个会跨节累加(第一版就是这么错的)。"""
    _N[0] += 1
    return os.path.join(TMP, "hist%d.json" % _N[0])


def _game(path=None, **kw):
    from danzhu.game import Game
    p = path or _new_hist()
    return Game(config_path=None, history_path=lambda: p,
                wall_clock=lambda: 12345.0, **kw)


def _step(g, n=1):
    """推进 n 帧，返回这一路攒下的**全部**事件。"""
    out = []
    for _ in range(n):
        out.extend(g.step(DT))
    return out


def _names(evs):
    return [e.name for e in evs if e.kind == "sound"]


def _kinds(evs, kind):
    return [e for e in evs if e.kind == kind]


def main():
    from danzhu.game import Game, START_BEADS, ROUND_END_VOICE_GAP
    from danzhu.audio.voice import number_voice_names

    print("== 一、触发条件：只有 `round_plays >= max_plays` 一条 ==")
    # 阴性对照: 没打满时**不许**弹窗（否则每发都弹）
    g = _game()
    g.max_plays = 20
    g.round_plays = 19
    g.balance = 1234
    g.start_charge()
    evs = _step(g, 3)
    check(not _kinds(evs, "round_end_popup"),
          "round_plays=19 < max_plays=20 ⇒ **不弹窗**（阴性对照）",
          "state=%r" % g.state)

    # 打满 ⇒ 弹
    g = _game()
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.start_charge()
    evs = _step(g, 3)
    pops = _kinds(evs, "round_end_popup")
    check(len(pops) == 1, "`start_charge` 打满 ⇒ 弹出一次（game.py:718-720）",
          "实得 %d 次" % len(pops))
    if pops:
        d = pops[0].data
        check(d.get("plays") == 20 and d.get("balance") == 1234,
              "弹窗载荷的 plays/balance = 触发那一刻的快照",
              "%r" % {k: d.get(k) for k in ("plays", "balance", "max_plays")})

    # 第二条触发点: park_ball
    g = _game()
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.park_ball(reroll=False, silent=True)
    evs = _step(g, 3)
    check(len(_kinds(evs, "round_end_popup")) == 1,
          "`park_ball` 打满 ⇒ 也弹（game.py:1015-1016，第二条触发点）")

    print("\n== 二、弹窗那一刻做齐了三件事（锁输入 / 写历史 / 发事件） ==")
    g = _game()
    hp = g._history_path()
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.start_charge()
    evs = _step(g, 3)
    check(g._round_end_shown is True, "`_round_end_shown` 置位（幂等闸）")
    check(g._controls_enabled is False, "**输入被锁住**（顶栏/HUD 全灰）")
    check(os.path.exists(hp), "轮次历史**立刻落盘**（不等退出）", hp)
    with open(hp, encoding="utf-8") as f:
        hist = json.load(f)
    check(hist == [{"plays": 20, "balance": 1234, "time": 12345.0}],
          "历史**恰好**一条三字段：plays / balance / time（`wall_clock` 可断言）",
          "%r" % (hist,))

    print("\n== 三、语音序列：模板 + 逐字念余额 + 后缀，**排队不叠** ==")
    # ⚠️ 必须**从触发那一刻**开始收事件: 第一条(delay=0)在触发后的**第一帧**就到期,
    #    先 `_step` 再看的话它已经落在上一批事件里了(第一版就是这么漏掉模板那条的)。
    g = _game()
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.start_charge()
    evs = _step(g, 600)                    # 泵够长, 让整条序列都到期
    got = _names(evs)
    want = (["voice_round_end_20"]
            + number_voice_names(1234)
            + ["voice_round_suffix"])
    check(got == want, "**恰好**这几条、次序一致（多一条少一条都算脱钩）",
          "实得 %r" % (got,))
    check(number_voice_names(1234) == [
        "voice_d_1", "voice_u_1000", "voice_d_2", "voice_u_100",
        "voice_d_3", "voice_u_10", "voice_d_4"],
        "`number_voice_names(1234)` 逐条钉死（不靠上面那条自己派生自己）",
        "%r" % (number_voice_names(1234),))
    times = [e.t for e in evs if e.kind == "sound"]
    durs = [g._voice_duration(n) for n in want]
    overlaps = [(want[i], times[i] - times[i - 1], durs[i - 1] + ROUND_END_VOICE_GAP)
                for i in range(1, len(times))
                if times[i] - times[i - 1] < durs[i - 1] + ROUND_END_VOICE_GAP - 2 * DT]
    check(not overlaps,
          "**上一句念完才起下一句**（间隔 = 上一条时长 + %.3fs，容差 2 帧）"
          % ROUND_END_VOICE_GAP,
          "" if not overlaps else "有重叠: %r" % (overlaps,))

    print("\n== 四、幂等：`_round_end_shown` 挡住第二次弹窗 ==")
    g = _game()
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.show_round_end()
    n_hist = len(g.round_history)
    evs = _step(g, 3)
    g.show_round_end()                      # 再调一次
    evs2 = _step(g, 3)
    check(not _kinds(evs2, "round_end_popup"),
          "已弹过再调 `show_round_end()` ⇒ **无新弹窗**")
    check(len(g.round_history) == n_hist,
          "历史**也不许多写一条**", "%d -> %d" % (n_hist, len(g.round_history)))

    print("\n== 五、确定 + **去重闸**（2026-09-19 修的那个比死代码还死的闸） ==")
    g = _game()
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.show_round_end()
    _step(g, 600)
    g.round_end_confirm()
    evs = _step(g, 3)
    check(g.balance == START_BEADS and g.round_plays == 0,
          "点确定 ⇒ 余额回 %d、round_plays 清零" % START_BEADS,
          "balance=%s round_plays=%s" % (g.balance, g.round_plays))
    check(g._round_end_shown is False, "`_round_end_shown` 复位（下一轮还能弹）")
    check(len(_kinds(evs, "round_end_close")) == 1,
          "发出 `round_end_close`（关窗的唯一出口）")
    # ⚠️ 这条就是那个真分歧的判据: 老版 `_done` 闭包置 True 后永不复位 ⇒ 硬 no-op
    # ⚠️ 判据**不能是"零事件"** —— `step()` 每帧都发 `ui_tick`, 那是帧噪声不是副作用。
    #    要查的是"有没有**重新做了一遍重置**": 新 close / 新 sound / 新状态迁移。
    n_before = len(g.sounds)
    g.round_end_confirm()                   # 第二次
    evs3 = _step(g, 3)
    noise = ("ui_tick",)
    real3 = [e for e in evs3 if e.kind not in noise]
    check(not real3 and len(g.sounds) == n_before,
          "**第二次点确定是硬 no-op**（不许二次重置 / 不许再发 close / 不许再响 cash）",
          "非帧噪声的新事件 %r, sounds %d -> %d"
          % ([e.kind for e in real3], n_before, len(g.sounds)))
    # ⚠️ 反面: 别为了修上面那条把玩家**永久卡在弹窗里** —— 下一轮必须能再确认
    g.round_plays = 20
    g.balance = 1234
    g.show_round_end()
    _step(g, 3)
    g.round_end_confirm()
    evs4 = _step(g, 3)
    check(g.balance == START_BEADS and len(_kinds(evs4, "round_end_close")) == 1,
          "**下一轮的确定照常有效**（去重闸是「每个弹窗一份」, 不是「一辈子一次」）",
          "balance=%s" % g.balance)

    print("\n== 六、轮次历史：读回 / 100 条上限 / 倒序渲染 ==")
    p = os.path.join(TMP, "cap.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump([{"plays": i, "balance": 100 + i, "time": float(i)}
                   for i in range(120)], f)
    g = _game(p)
    check(len(g.round_history) == 100,
          "读回时 `data[-100:]` 截断（120 条进去, 100 条出来）",
          "实得 %d" % len(g.round_history))
    check(g.round_history[0]["plays"] == 20 and g.round_history[-1]["plays"] == 119,
          "保留的是**最后** 100 条（不是最前 100 条）",
          "首 %r 末 %r" % (g.round_history[0]["plays"], g.round_history[-1]["plays"]))
    g._history_path = lambda: p
    g.round_history = [{"plays": i, "balance": i, "time": float(i)}
                       for i in range(100)]
    g._round_end_shown = False
    g.max_plays = 20
    g.round_plays = 20
    g.balance = 1234
    g.show_round_end()
    check(len(g.round_history) == 100,
          "append 后超 100 条 ⇒ `pop(0)` 丢掉最老的", "实得 %d" % len(g.round_history))
    with open(p, encoding="utf-8") as f:
        on_disk = json.load(f)
    check(len(on_disk) == 100 and on_disk[-1]["balance"] == 1234,
          "**盘上**也是 100 条，最后一条是刚写的", "实得 %d 条" % len(on_disk))

    # 坏文件 / 非 list ⇒ 静默忽略, 不抛
    for nm, content in (("半截 JSON", '[{"plays": 1'), ("不是 list", '{"a": 1}')):
        pb = os.path.join(TMP, "bad.json")
        with open(pb, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            gb = _game(pb)
            check(gb.round_history == [], "%s ⇒ 静默忽略(回空表, 不抛)" % nm,
                  "%r" % (gb.round_history,))
        except Exception as e:
            check(False, "%s ⇒ 静默忽略(不抛)" % nm, "抛了 %r" % (e,))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（触发 / 弹窗 / 历史 / 语音排队 / 幂等 / 去重闸 / 上限）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
