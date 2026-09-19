"""启动补位门禁 —— 「上轮打满被 kill ⇒ 启动即静默重置」（`_auto_reset_on_start`）。

    python tests/auto_reset.py

## 这条门禁补的是什么洞

玩家玩到 `round_plays == max_plays` 那一刻被系统杀掉（来电/清后台/崩溃），下次启动时：
存档里 `round_plays` 还是满的，而 `Game.__init__` **末尾**那句 `park_ball`
（`game.py:1027-1028` ↔ 老版 `main.py:20369-20370`）判据正是
`round_plays >= max_plays and not _round_end_shown` —— 不补位就会**一启动就弹轮次结束窗**。

老版靠 `_load_config` 置位 + `RootWidget.__init__` 里 `_build_ui()` 之后
`reset_balance(notify=False)` 补位（`main.py:20604-20605` / `:13354-13355`）。
新版**逐字同构**（`game.py:1528-1529` / `:373-374`），但 `tests/` 下**零覆盖**：

* `grep -rn "_auto_reset_on_start" new_danzhu/tests/` → **零命中**；
* `tests/persist_check.py` 写的是 `max_plays=100, round_plays=7` ⇒ 碰不到；
* `tests/round_end.py:80` 构造时 `config_path=None` ⇒ `_load_config` 短路，标志永不置位。

⚠️ 所以「**自动重置是启动时那扇轮次结束窗的唯一闸门**」这件事**此前没人守**。

## 判据（全取跨层副作用，不查源码字符串）

| 断言 | 依据 |
|---|---|
| 余额回 `START_BEADS`、三个影子字段同步 | `game.py:1319-1323` |
| `plays/hits/round_plays` 清零、`_round_end_shown` 复位 | `:1324, :1328, :1329` |
| **`_ev` 里没有 `round_end_popup`** | 补位真的在 `park_ball` 之前跑完了 |
| 盘上 JSON 被**改写**（`round_plays=0`、9 个键） | `:1340` → `_save_config` |
| 事件序列 = `stats → status("已重置") → sound(cash) → controls → restyle_buttons`，且 `notify=False` ⇒ **没有** `toast`/`toast_clear`/`voice_reset_progress` | `:1326→1327→1330→1331`，对比 `:1332-1339` |
| `round_history` **未新增**、历史文件未被写 | 自动重置**不过** `show_round_end` |

⚠️ 「无头下这条链不出声」是**空断言** —— `Game` 没有 `Sfx`，`_snd` 只记事件。
   要断言就断**事件**，不是"没响"。
"""

import io
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

TMP = tempfile.mkdtemp(prefix="danzhu_autoreset_")
_N = [0]
FAIL = []
N_OK = [0]
CFG_KEYS = ("max_plays", "rtp_target", "bet", "balance", "round_plays",
            "plays", "hits", "fps_cap_setting", "power_grain")


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


def _write(cfg):
    _N[0] += 1
    p = os.path.join(TMP, "cfg%d.json" % _N[0])
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return p


def _game(path):
    from danzhu.game import Game
    return Game(config_path=(lambda: path),
                history_path=(lambda: os.path.join(TMP, "hist%d.json" % _N[0])))


def _kinds(evs, kind):
    return [e for e in evs if e.kind == kind]


def main():
    from danzhu.game import START_BEADS

    print("== 一、打满被 kill ⇒ 启动即静默重置，且**不弹轮次结束窗** ==")
    p = _write({"max_plays": 20, "rtp_target": 1.2, "bet": 10, "balance": 0,
                "round_plays": 20, "plays": 20, "hits": 0,
                "fps_cap_setting": 60, "power_grain": "每5秒"})
    g = _game(p)
    evs = g.take_events()
    check(g.balance == START_BEADS,
          "余额回到 %d（存档里是 0 ⇒ 这次是**真重置了**）" % START_BEADS,
          "balance=%s" % g.balance)
    check (g.display_balance == float(START_BEADS)
           and g._anim_target_balance == float(START_BEADS)
           and g._anim_start_balance == float(START_BEADS),
           "三个影子字段(`display`/`_anim_target`/`_anim_start`)一起同步",
           "%s/%s/%s" % (g.display_balance, g._anim_target_balance,
                         g._anim_start_balance))
    check(g.plays == 0 and g.hits == 0 and g.round_plays == 0,
          "plays / hits / round_plays 全部清零",
          "%s/%s/%s" % (g.plays, g.hits, g.round_plays))
    check(g._round_end_shown is False, "`_round_end_shown` 复位")

    pops = _kinds(evs, "round_end_popup")
    check(not pops,
          "**`_ev` 里没有 `round_end_popup`** —— 补位赶在 `Game.__init__` 末尾那句"
          " `park_ball` 之前跑完了（**它是启动时那扇窗的唯一闸门**）",
          "实得 %d 条" % len(pops))
    check(g.state == "ready",
          "状态停在 `ready`（没被顶进别的分支）", "state=%r" % g.state)

    print("\n== 二、静默的准确含义：只静 toast/语音，事件序列仍完整 ==")
    # ⚠️ 只看**前 5 条** —— `Game.__init__` 末尾还有 `set_bet` / `set_rtp` / `park_ball`
    #    会各自发事件(`set_bet` 自己就发 controls/restyle/stats 一串)。重置那 5 条
    #    **必须是缓冲区最前面的一批**, 这才证明"补位跑在 `park_ball` 之前"。
    order = [e.kind for e in evs]
    # ⚠️ 只有 4 条: `Game._controls` **只发 `controls`**(底色由它的处理函数重刷)。
    #    原来这里多一条 `restyle_buttons` —— 那正是第三轮 AST 审计抓到的"同帧多跑一次"
    #    (`_restyle_buttons` 老版 1 次 / 新版 2 次), 这条闸反而把它**钉死**了。
    want = ["stats", "status", "sound", "controls"]
    check(order[:4] == want,
          "缓冲区**最前面 4 条** = %r（补位跑在 `set_bet`/`set_rtp`/`park_ball` 之前）"
          % (want,),
          "实得全序 %r" % (order,))
    # ⚠️⚠️ **精确判据**: `Game._controls` **只许发 `controls` 一条**。
    #    第三轮审计的 AST 逐函数比对抓到: 新版原来还多发一条 `restyle_buttons`, 而
    #    `controls` 的处理函数 `UiMixin._set_controls_enabled` **首句就是**
    #    `self._restyle_buttons()`(`ui_base.py:721`) ⇒ 同帧跑 **2** 次; 老版
    #    `_set_controls_enabled` 体里 `_restyle_buttons(` 的 AST 计数**恰好 1**。
    #    那一拍正是工程标定的最贵帧("回 ready 那一拍")。
    #    ⚠️ 只看事件序列的**前 4 条**抓不住它 —— 多出来的那条落在索引 4, 而 `set_bet`
    #       自己也会发 `restyle_buttons`。必须**单独驱动 `_controls` 数一遍**。
    g.take_events()                      # 排空
    g._controls(True)
    _k = [e.kind for e in g.take_events()]
    check(_k == ["controls"],
          "`Game._controls` **恰好只发 `controls` 一条**"
          "（多发 `restyle_buttons` = 底色重刷同帧跑两次）",
          "实得 %r" % (_k,))
    st = _kinds(evs, "status")
    check(len(st) == 1 and st[0].data.get("text") == "已重置",
          "唯一那条 status 是「已重置」", "%r" % ([e.data for e in st],))
    snd = _kinds(evs, "sound")
    check([e.name for e in snd] == ["cash"],
          "`cash` 那一声**照发**（`notify=False` **不**管它 —— 两版都是无条件发）",
          "%r" % ([e.name for e in snd],))
    for k in ("toast", "toast_clear"):
        check(not _kinds(evs, k), "`notify=False` ⇒ **没有** `%s`" % k)
    check(not [e for e in snd if e.name == "voice_reset_progress"],
          "`notify=False` ⇒ **没有** `voice_reset_progress`")

    print("\n== 三、盘上被改写，且历史**一分钱没动** ==")
    with io.open(p, encoding="utf-8") as f:
        disk = json.load(f)
    check(set(disk) == set(CFG_KEYS),
          "落盘键集合**恰好** 9 个", "实得 %s" % sorted(disk))
    check(disk["round_plays"] == 0 and disk["balance"] == START_BEADS,
          "盘上的 round_plays / balance 也被改写（否则下次启动又打满）",
          "round_plays=%s balance=%s" % (disk["round_plays"], disk["balance"]))
    check(disk["max_plays"] == 20 and disk["fps_cap_setting"] == 60,
          "**非本轮相关的字段原样保留**（不是整份重写成出厂值）",
          "max_plays=%s fps_cap=%s" % (disk["max_plays"], disk["fps_cap_setting"]))
    check(g.round_history == [],
          "`round_history` **一条都没新增**（自动重置不过 `show_round_end`）",
          "%r" % (g.round_history,))
    check(not os.path.exists(os.path.join(TMP, "hist%d.json" % _N[0])),
          "轮次历史文件**没被写**")

    print("\n== 四、可达集：只有 `round_plays == max_plays` 那一个点 ==")
    # 差一发 ⇒ 不重置
    p2 = _write({"max_plays": 20, "balance": 0, "round_plays": 19, "plays": 19})
    g2 = _game(p2)
    check(g2.balance == 0 and g2.round_plays == 19,
          "`round_plays=19 < max_plays=20` ⇒ **不重置**（余额停在存档值）",
          "balance=%s round_plays=%s" % (g2.balance, g2.round_plays))
    check(not [e for e in g2.take_events() if e.kind == "round_end_popup"],
          "也不弹窗", "")
    # 超一发 ⇒ 被白名单挡回 0
    p3 = _write({"max_plays": 20, "balance": 0, "round_plays": 21, "plays": 21})
    g3 = _game(p3)
    check(g3.round_plays == 0,
          "`round_plays=21 > max_plays` ⇒ 被读档白名单**挡回 0**（`21 <= 20` 为假）",
          "round_plays=%s" % g3.round_plays)

    print("\n== 五、标志消费后**不清零**（两版共有的现状，钉住免得将来二次重置） ==")
    check(g._auto_reset_on_start is True,
          "`_auto_reset_on_start` 消费后**仍是 True** —— 老版 `main.py:13354` 同样只读不写。"
          "今天不可达（`_load_config` 只调一次），将来任何一次重读档都会**二次重置**",
          "%r" % g._auto_reset_on_start)

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（重置 / 不弹窗 / 静默口径 / 盘上改写 / 可达集）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
