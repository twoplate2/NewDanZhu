"""渲染尾巴跳过门禁 —— 蓄力超 3 秒的兜底自动发射那一帧, UI 尾巴**必须跳过**。

    python tests/render_skip.py

## 这条门禁补的是什么洞

老版 `_frame` 里蓄力超时那一支是（`android/main.py:21220-21222`）：

    if time.time() - self._charge_start > 3.0:
        self.launch()   # 兜底: 蓄力超3秒自动发射(防 on_release 丢失卡死)
        return          # ← 直接退出整个 _frame

`return` 把后面**整条渲染尾巴**都跳过了 —— 余额标签赋值、`_sync_hud_dim`、`_FRAME_END`。

状态机搬进 `Game` 之后，`PlayMixin._frame` 的尾巴变成**无条件**跑完 ⇒
那一帧新版比老版**多付一次 `_set_label_text(balance_lbl, ...)`**。
而老版自己的注释（`main.py:21398-21403`）算过：那次赋值 = 重测字形 + 重光栅化 + 重建纹理，
还连锁触发 `_fit1` —— **`_frame` 里最大的单块**（45 秒累计 1.06 秒 ≈ 24ms/s，是板面重画的 4 倍）。

⚠️ **它是幂等的**（余额没变，`_a_dim_now` 同值）⇒ 画面不变、`trace_parity` 逐位相同、
**23 条门禁一条都不会红** —— 与第三轮抓到的 `Game._controls` 同型：**只差一点的静默缺陷**。
这条由 `tests/ast_parity.py` 的 `STRUCT` 分类发现（第 2 例）。

## 判据

驱动 `Game` 越过 3 秒蓄力，数 `_render_skip` 在**哪一帧**为真：

* **恰好一次**（多一次 = 每局多付；一次都没有 = 没跳过，就是本条要防的）；
* 触发时 `state` 已离开 `charging`（证明就是自动发射那一帧，不是别处）；
* **下一帧复位**（否则后续每帧都跳，画面就停了 —— 那比多付更糟）。
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


def _run(hold_frames):
    from danzhu.game import Game
    g = Game(config_path=None, history_path=None)
    g.balance = 1000
    g.bet = 10
    g.start_charge()
    hits = []
    for i in range(hold_frames):
        g.step(DT)
        if getattr(g, "_render_skip", False):
            hits.append((i, round(g.now, 3), g.state))
    return g, hits


def main():
    print("== 一、蓄力超 3 秒 ⇒ `_render_skip` **恰好一次**, 且就在自动发射那一帧 ==")
    g, hits = _run(260)                       # 4.33 秒
    check(len(hits) == 1,
          "`_render_skip` 为真的帧**恰好 1 帧**"
          "（0 = 没跳过尾巴, 就是本条要防的；>1 = 每局多付几次）",
          "%r" % (hits,))
    if hits:
        i, t, st = hits[0]
        check(st != "charging",
              "触发时 state 已离开 `charging`（证明是自动发射那一帧）", "state=%r" % st)
        check(170 <= i <= 200,
              "落在 3 秒附近（60fps 下约第 180 帧）", "第 %d 帧 (t=%.3f)" % (i, t))

    print("\n== 二、下一帧**必须复位**（否则画面就停了，比多付更糟） ==")
    g.step(DT)
    check(getattr(g, "_render_skip", False) is False,
          "自动发射的下一帧 `_render_skip` 已复位")

    print("\n== 三、没超时的一帧**不许**跳过（阴性对照） ==")
    from danzhu.game import Game
    g2 = Game(config_path=None, history_path=None)
    g2.balance = 1000
    g2.bet = 10
    g2.start_charge()
    skips = 0
    for _ in range(120):                      # 2 秒, 不到 3 秒
        g2.step(DT)
        if getattr(g2, "_render_skip", False):
            skips += 1
    check(skips == 0,
          "蓄力 2 秒内**一次都没跳过**（否则等于每帧都在跳，画面会停）",
          "%d 次" % skips)

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（恰好一次 / 时机对 / 下一帧复位 / 未超时不跳）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
