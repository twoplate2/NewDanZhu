"""存档往返门禁 —— 落盘 / 读回 / **白名单**。

    python tests/persist_check.py

## 为什么需要它

「余额 / 投注 / 返还率 / 轮次 跨启动恢复」是玩家可见的功能，但它在**任何门禁里都没跑过**：

* `tests/trace.py` 刻意把它**打桩关掉**（`_cfg_post` 恒真 ⇒ `_save_config` 直接 return），
  为的是"启动恒为出厂设定"，好让轨迹可复现；
* `tests/fx_gates.py` 只做静态源码比对；
* `tests/bench_smoke.py` 跑的是入口。

⇒ 于是「写下去的是不是那 9 个键」「读回来时**白名单**拦不拦得住坏值」这两件事
**一条断言都没有**。而白名单正是老版踩过的坑：读档不设白名单 ⇒ 一个坏 config
就能把 `rtp_target` 写成隐藏档、把 `round_plays` 写得比 `max_plays` 还大。

## 判据

写 → 读 → 逐字段比；然后**逐字段喂坏值**，验它被白名单挡住而不是照单全收。
另外验两条结构性事实：**隐藏档读不回来**（`_regular_rtp()` 派生，不靠手抄）、
坏文件**静默回出厂设定**而不是抛。
"""

import io
import json
import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ROWS = []


def check(ok, name, detail=""):
    ROWS.append(ok)
    print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                           ("  " + detail) if detail else ""))


CFG_KEYS = ("max_plays", "rtp_target", "bet", "balance", "round_plays",
            "plays", "hits", "fps_cap_setting", "power_grain")


def _game(path, **kw):
    from danzhu.game import Game
    return Game(config_path=(lambda: path), history_path=None, **kw)


def main():
    from danzhu.config import FPS_CAP_OPTIONS, POWER_GRAINS, PRESETS

    tmp = tempfile.mkdtemp(prefix="danzhu_persist_")
    path = os.path.join(tmp, "plinko_config.json")

    print("== 一、写下去的是不是那 9 个键 ==")
    g = _game(path)
    g.max_plays = 100
    g.rtp_target = 2.0
    g.bet = 50
    g.balance = 4321
    g.round_plays = 7
    g.plays = 21
    g.hits = 9
    g.fps_cap_setting = 90
    g.power_grain = "每5秒"
    g._save_config()
    check(os.path.exists(path), "落盘文件已生成", path)
    with io.open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    check(set(cfg) == set(CFG_KEYS),
          "键集合**恰好**是那 9 个(多一个少一个都算脱钩)",
          "实得 %s" % sorted(cfg))
    check(cfg.get("balance") == 4321 and cfg.get("bet") == 50
          and cfg.get("power_grain") == "每5秒",
          "落盘的值与内存里一致", "%r" % {k: cfg.get(k) for k in
                                          ("balance", "bet", "power_grain")})

    print("\n== 二、读回来逐字段复原 ==")
    g2 = _game(path)
    got = {k: getattr(g2, k) for k in CFG_KEYS}
    want = dict(zip(CFG_KEYS, (100, 2.0, 50, 4321, 7, 21, 9, 90, "每5秒")))
    bad = {k: (want[k], got[k]) for k in CFG_KEYS if got[k] != want[k]}
    check(not bad, "9 个字段逐个复原", "" if not bad else repr(bad))
    check(g2.display_balance == 4321.0 and g2._anim_target_balance == 4321.0,
          "余额复原时 **display/_anim 三个影子字段一起同步**",
          "display=%s target=%s" % (g2.display_balance, g2._anim_target_balance))
    # ⚠️⚠️ `fps_cap_setting` 有**两份**: `game.fps_cap_setting`(设置弹窗高亮用的那份) 与
    #    模块级 `config._FPS_USER_CAP[0]`(**真正决定帧率**的那份 —— `device.py` 的
    #    `_fps_user_cap()` 读它, `_apply_fps_cap()` 按它设 `Clock._max_fps` 与安卓高刷请求)。
    #    老版读档后有一行同步(`android/main.py:13324`), 拆成模块时那个同步点**没人接手**
    #    ⇒ 「把上限设成 60 并落盘, 下次启动 UI 显示 60、实际跑 120」, 每次重启复发。
    #    ⇒ 只断言 `game.fps_cap_setting` 复原是**测不到**的(两份可以各说各话) ——
    #      这正是它能一路漂到第二轮完工审计的原因。判据必须查**实际生效**的那一份。
    from danzhu.platform.device import _fps_user_cap
    check(_fps_user_cap() == g2.fps_cap_setting == 90,
          "帧率上限**实际生效**的那份也复原（模块级 `_FPS_USER_CAP[0]`，不只是 game 属性）",
          "game=%s / _fps_user_cap()=%s" % (g2.fps_cap_setting, _fps_user_cap()))

    print("\n== 三、白名单逐字段挡坏值（老版踩过的坑） ==")
    cases = [
        ("max_plays", 999, 50, "只认 (20, 50, 100)"),
        ("bet", 7, 10, "只认 PRESETS=%s" % (PRESETS,)),
        ("balance", -5, 1000, "余额不许为负"),
        ("round_plays", 999, 0, "round_plays 不得 > max_plays"),
        ("plays", -1, 0, "次数不许为负"),
        ("hits", -1, 0, "中奖数不许为负"),
        ("fps_cap_setting", 61, 120, "只认 FPS_CAP_OPTIONS"),
        ("power_grain", "每3秒", "每帧", "只认 POWER_GRAINS=%s" % (POWER_GRAINS,)),
    ]
    for key, badval, default, why in cases:
        p2 = os.path.join(tmp, "bad_%s.json" % key)
        with io.open(p2, "w", encoding="utf-8") as f:
            json.dump({key: badval}, f)
        gg = _game(p2)
        got_v = getattr(gg, key)
        check(got_v == default, "坏 %s=%r 被挡回默认 %r" % (key, badval, default),
              "%s；实得 %r" % (why, got_v))

    print("\n== 四、两条结构性事实 ==")
    # 隐藏档必须**读不回来** —— 靠 `_regular_rtp()` 派生, 不靠谁记得改那行字面量
    from danzhu.game import Game
    hidden = Game.RTP_HIDDEN[0][1]
    p3 = os.path.join(tmp, "hidden.json")
    with io.open(p3, "w", encoding="utf-8") as f:
        json.dump({"rtp_target": hidden}, f)
    g3 = _game(p3)
    check(g3.rtp_target != hidden,
          "**隐藏档读不回来**(rtp_target=%r 被忽略, 停在 %r)" % (hidden, g3.rtp_target),
          "白名单走 _regular_rtp() 派生 ⇒ 这是**结构性**事实")
    # 常驻档必须读得回来(否则上面那条就是"什么都不读"的假绿)
    p4 = os.path.join(tmp, "regular.json")
    with io.open(p4, "w", encoding="utf-8") as f:
        json.dump({"rtp_target": 1.2}, f)
    check(_game(p4).rtp_target == 1.2, "常驻档 1.2 照常读得回来（阴性对照）")

    # 坏文件 → 静默回出厂设定, 不抛
    for name, content in (("半截 JSON", '{"bet": 50'), ("不是 JSON", "hello")):
        p5 = os.path.join(tmp, "corrupt.json")
        with io.open(p5, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            g5 = _game(p5)
            check(g5.bet == 10, "%s ⇒ 静默回出厂设定(不抛)" % name,
                  "bet=%s" % g5.bet)
        except Exception as e:
            check(False, "%s ⇒ 静默回出厂设定(不抛)" % name, "抛了 %r" % (e,))
    # 文件不存在(首启)也不能抛
    try:
        g6 = _game(os.path.join(tmp, "不存在.json"))
        check(g6.bet == 10, "文件不存在(首启) ⇒ 出厂设定, 不抛")
    except Exception as e:
        check(False, "文件不存在(首启) ⇒ 出厂设定, 不抛", "抛了 %r" % (e,))

    # ⚠️⚠️ **"坏 JSON" ≠ "坏 JSON 值"** —— 上面那两个样本都在 `json.load` 那一步就失败,
    #    因而被读文件那道 try 接住;**喂不到解析块**。而解析块在 2026-09-19 之前是**裸奔**的
    #    (重构时 try 被收窄成"只罩 open+json.load"; 老版 `android/main.py:20566` 的 try
    #    一直包到 `:20600`)。`1e400` / `Infinity` 是**合法 JSON**, `json.load` 读出
    #    `float('inf')`, 而 `inf >= 0` 为真 ⇒ `int(cfg["balance"])` 抛 OverflowError ⇒
    #    异常冒出 `App.build()`(那里没有 try) ⇒ **启动即崩、进不去游戏**; 老版同一份存档
    #    只是静默丢掉 `balance` 之后的几个字段。⇒ 加这一节, 判据同时钉住
    #    「不抛」与「**半途生效**」(抛之前赋过的字段保持生效, 不是整体回滚)。
    print("\n== 四之二、**合法 JSON 的坏值**: 溢出数不许把启动打崩 ==")
    po = os.path.join(tmp, "overflow.json")
    for nm, txt, want in (
            ("balance=1e400 (json 读出 inf)",
             '{"max_plays":20,"rtp_target":1.2,"bet":100,"balance":1e400,"plays":7,"hits":3}',
             (20, 1.2, 100, 1000, 0, 0)),
            ("balance=Infinity (裸字面量)",
             '{"max_plays":20,"bet":100,"balance":Infinity,"plays":7}',
             (20, 0.8, 100, 1000, 0, 0)),
            ("balance=-1e400 (-inf)",
             '{"max_plays":20,"bet":100,"balance":-1e400}',
             (20, 0.8, 100, 1000, 0, 0))):
        with io.open(po, "w", encoding="utf-8") as f:
            f.write(txt)
        try:
            go = _game(po)
            got = (go.max_plays, go.rtp_target, go.bet, go.balance, go.plays, go.hits)
            check(got == want,
                  "%s ⇒ **不抛**，且「半途生效」与老版逐字段相同" % nm,
                  "实得 %r（期望 %r）" % (got, want))
        except Exception as e:
            check(False, "%s ⇒ **不许抛**（老版静默吞掉照常启动）" % nm,
                  "抛了 %r: %s" % (type(e).__name__, e))

    print("\n== 五、落盘口是注入的（异步优先 / 同步兜底） ==")
    p6 = os.path.join(tmp, "inject.json")
    seen = []

    def fake_post(cfg, p):
        seen.append((cfg.get("bet"), p))
        return True
    gi = _game(p6, save_config=fake_post)
    gi.bet = 100
    gi._save_config()
    check(seen and seen[-1][0] == 100 and seen[-1][1] == p6,
          "注入的落盘口收到 (cfg, path)", "%r" % (seen[-1:] or None,))
    check(not os.path.exists(p6), "落盘口返 True ⇒ **主线程不写盘**")
    # 返 False ⇒ 同步兜底(与老版逐字相同的那一支)
    p7 = os.path.join(tmp, "fallback.json")
    gf = _game(p7, save_config=lambda cfg, p: False)
    gf.bet = 100
    gf._save_config()
    check(os.path.exists(p7), "落盘口返 False ⇒ 同步兜底真的写了")
    with io.open(p7, encoding="utf-8") as f:
        check(json.load(f).get("bet") == 100, "兜底写的内容对")

    print()
    bad_n = ROWS.count(False)
    if bad_n:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (bad_n, len(ROWS)))
        return 1
    print("门禁结果: 全绿 —— %d 项(落盘/读回/白名单/注入)" % len(ROWS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
