"""同拍派发门禁 —— UI 回调直调 `Game` 的输入口时, **必须走 `_act`**。

    python tests/act_gate.py

## 这条门禁补的是什么洞

`Game` 是**无头状态机**, 它只把界面效果**排进事件队列**(`_ev`), 要等下一次
`game.step(dt)` 的派发才落地。而老版是"置位与染色**同体同拍**"的同步方法。

⇒ UI 回调里直调 `self.game.X(...)` 而不走 `self._act(...)`, 界面那一半就**晚一帧**
(≈16.7ms)。工程自己记着同一形状的账(`ui/play.py:73-78`)：

    从 UI 回调直调 `game.launch()` 会让那一声晚一帧: 对账台实测第 1 条音效
    就是 **frame 34 vs frame 35**

⚠️⚠️ **这个病已经犯过两轮**(`ui/ui_base.py` 的注释里有完整记录):
  · 跑分链那 4 处**最初**直接调 `_set_controls_enabled` ⇒ **状态位整个丢了**
    (高压/模拟测试那几十秒 HUD 全程不上锁, 玩家能点重置/投注档/发射);
  · 第一轮改成裸调 `self.game._controls(...)` ⇒ 状态位补回来了, 但**染色晚一帧**;
  · 正解 = `self._act(self.game._controls, ...)` —— 既写位又当拍派发。

⚠️ 它是**幂等/一帧之差** ⇒ 画面看着一样、`trace_parity` 逐位相同、**别的门禁全绿**
—— 与 `Game._controls` 多发一条 `restyle_buttons`(第三轮)和 `_frame` 白付最贵单块
(第四轮)同型: **只差一点的静默缺陷**。

## 判据

用 `ast` 扫 `danzhu/ui/**`：凡是对 `self.game.<名字>(...)` 的调用
(名字取自 `Game` 的**输入口**白名单), **必须**出现在 `self._act(...)` 的第一个实参位置。
**不查源码字符串** —— 查的是 AST 里的调用父子关系。
"""

import ast
import io
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UI = os.path.join(ROOT, "danzhu", "ui")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# `Game` 的**输入口** —— 从 UI 调它们都会产生"界面那半边"的事件, 必须当拍派发。
GAME_INPUTS = (
    "start_charge", "launch", "settle", "park_ball", "set_bet", "set_rtp",
    "reset_balance", "toggle_mute", "set_max_plays", "set_sound_enabled",
    "set_fps_cap_setting", "round_end_confirm", "easter_finish",
    "ask_unlock_rtp", "close_rtp_hidden", "show_round_end", "set_power_grain",
    "_controls", "_status", "_snd", "show_easter_popup",
)
# ⚠️ **不发事件的 Game 口不该进上面的名单** —— `easter_finish` 只置 `_easter_hold = False`
#    (`game.py:1103`), 一个事件都不发 ⇒ 走不走 `_act` 毫无区别, 列进来只会制造假阳。
#    (第一版把它列进来, 门禁就报了 `play.py:717` 那条假阳。)
NO_EVENT_OK = ("easter_finish",)

# ⚠️ **跑分/高压的采样循环要豁免** —— 那里正在用 `_brk_add(...)` 逐段掐表,
#    在计时区间里派发 UI 事件会**污染被测量的东西**(`bench.py:1809/1814` 那两句
#    就在 `_brk_add("发·盘面", _tb)` 与 `_brk_add("发·起蓄", _tc)` 之间)。
#    老版那两处确实是同步调, 但老版的跑分块本身也在同一段里量 —— 保持"不在计时区间里
#    派发"是**更接近老版被测语义**的选择, 且这两句不产生玩家可见的界面效果(跑分期间 HUD 压暗)。
# ⚠️ **豁免只能给真正在掐表的那个函数** —— 第一版写成了前缀元组
#    (`_bench` / `_hp` / `_start_bench` / `_start_hp` …), 而那 4 处要修的
#    (`_start_hp_test` / `_hp_done` / `_start_bench_test` / `_bench_done`)
#    **恰恰全落在这些前缀里** ⇒ **门禁对它们完全空转**, 阴性对照判"无效"才发现。
#    `_auto_launch_tick` 是唯一真正在计时区间里发球的: 它在 `bench.py:1680` 被
#    `schedule_interval`, 体内用 `_brk_add("发·盘面", ...)` 逐段掐表, 而
#    `self.game.start_charge()` / `self.game.launch()` 就夹在两次掐表之间。
BENCH_LOOP_PREFIX = ("_auto_launch_tick",)
#     `_auto_launch_tick` 也归跑分: 它**只在** `bench.py:1680` 被 `schedule_interval`,

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


def _spell(n):
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        b = _spell(n.value)
        return (b + "." + n.attr) if b else n.attr
    return None


def _scan(path):
    """返回 [(行号, 拼写)] —— 直调 `Game` 输入口而**没**走 `_act` 的地方。"""
    tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    ok_children = set()          # `self._act(...)` 的第一个实参节点 id

    good = []          # ⚠️ `self._act(self.game.X, ...)` 传的是**引用不是调用**,
                       #    所以它在 AST 里**没有 Call 节点** —— 必须在这里单独数,
                       #    否则夹具自检永远数到 0(第一版就是这样, 自检恒红)。
    class Mark(ast.NodeVisitor):
        def visit_Call(self, n):
            if _spell(n.func) == "self._act" and n.args:
                ok_children.add(id(n.args[0]))
                sp = _spell(n.args[0])
                if sp and sp.startswith("self.game.") and sp[len("self.game."):] in GAME_INPUTS:
                    good.append((n.lineno, sp))
            self.generic_visit(n)
    Mark().visit(tree)

    bad = []
    seen = []          # ⚠️ **所有**调用, 不论走没走 `_act` —— 供夹具自检用

    class Scan(ast.NodeVisitor):
        def __init__(self):
            self.enclosing = ""

        def visit_FunctionDef(self, n):
            prev, self.enclosing = self.enclosing, n.name
            self.generic_visit(n)
            self.enclosing = prev
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, n):
            sp = _spell(n.func)
            nm = sp[len("self.game."):] if (sp and sp.startswith("self.game.")) else ""
            if nm and nm in GAME_INPUTS and nm not in NO_EVENT_OK and not self.enclosing.startswith(BENCH_LOOP_PREFIX):
                seen.append((n.lineno, sp, id(n) in ok_children))
                if id(n) not in ok_children:
                    bad.append((n.lineno, sp))
            self.generic_visit(n)
    Scan().visit(tree)
    return bad, seen + good


def main():
    total = 0
    bad_all = []
    for base, _d, files in os.walk(UI):
        if "__pycache__" in base:
            continue
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            p = os.path.join(base, f)
            bad, seen = _scan(p)
            total += len(seen)
            for ln, sp in bad:
                bad_all.append("%s:%d  %s(...)" % (os.path.relpath(p, ROOT), ln, sp))

    print("== UI 里对 `Game` 输入口的调用 ==")
    check(True, "扫了 %d 处对输入口的调用（白名单 %d 个；跑分采样循环已豁免）"
          % (total, len(GAME_INPUTS)))
    check(not bad_all,
          "**每一处都走了 `self._act(...)`**（直调 = 界面那半边晚一帧, "
          "而老版是置位与染色同体同拍）",
          "" if not bad_all else "\n        " + "\n        ".join(bad_all))
    check(total > 0,
          "（夹具自检）确实扫到了调用 —— 否则上面那条是**空转**"
          "（⚠️ 这里必须数**所有**调用, 不是只数「坏的」 —— 第一版数错了, 于是夹具自检"
          "只有判红时才通过, 自相矛盾）",
          "0 处" if not total else "")

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（调用形状对 / 夹具非空转）" % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
