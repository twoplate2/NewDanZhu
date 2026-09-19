"""跑分 / CPU高压 / 轮次 三份历史的**落盘 → 读回 → 渲染 → 清空**门禁。

    python tests/history_io.py

## 这条门禁补的是什么洞

三份历史（轮次 / 跑分 / CPU 高压）各有 100 条上限、各有独立文件，但此前：

* **没有任何测试写过一条历史** —— 全仓 `grep` 六个 `_save_*_history()` 的调用点，
  只有产物代码，测试侧**零调用**；
* **没有任何测试读过一条真记录** —— `tests/bench_smoke.py` / `mute_chain.py` 把路径
  引到**空**临时目录 ⇒ `_load_*` 恒走 `except` 分支，`isinstance(data, list)` 那个类型闸
  与 `data[-100:]` 截断**从没被验证过**；
* **渲染的非空分支从没执行过** —— 打开面板时列表都是空的，真正建表格那一大段是死路；
* **文件名三个字面量从没被跑过** —— 现有门禁把桩写成 `_n + ".json"`，产出的是
  `_bench_history_path.json` 这种**桩名**。⇒ **真文件名写错一个字母，门禁全绿**
  （玩家/开发者的历史记录静默丢光）。本脚本先问真函数拿真名字，再只换目录。

⚠️ 三条链的超限语义**与其他地方不同**：`_save` 落盘时切 `[-100:]`、`_load` 读回时也切
   —— 所以盘上恒 ≤100 条，**不是**"写 101 条就丢最老的"那种 append 语义。
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

os.environ.setdefault("KIVY_NO_ARGS", "1")
warnings.filterwarnings("ignore")

DT = 1.0 / 60.0
FAIL = []
N_OK = [0]

_EXPECT_NAMES = {
    "_config_path": "plinko_config.json",
    "_history_path": "plinko_round_history.json",
    "_bench_history_path": "plinko_bench_history.json",
    "_hp_history_path": "plinko_hp_history.json",
}
_REAL_NAMES = {}

# 一条"像真的"跑分记录(字段照 `bench.py:2412-2467` 取; 缺字段渲染侧一律 `r.get()` + 「—」)
BENCH_REC = {
    "time": "2026-09-19 12:00", "render_fps": 60.0, "render_median": 16.7,
    "render_1low": 48.0, "render_10low": 52.0, "render_p99": 18.0, "render_p90": 17.0,
    "phys_fps": 33000.0, "phys_mad": 1.5, "phys_min": 31000.0, "phys_max": 35000.0,
    "phys_spread": 4000.0, "phys_cpu_seconds": 12.0, "phys_freq_mean": 2400.0,
    "phys_freq_p50": 2400.0, "avg_frames": 3.0, "cost_ms": 16.7, "flight_ms": 220.0,
    "margin": 0.42, "battery_start_c": 30.0, "battery_end_c": 33.0,
    "sust_sec": 60.0, "version": "v0.6.7", "device": "desktop",
}
# 一条"像真的"高压记录(字段照 `bench.py:776-850` 取), 带上 `windows` —— `_hp_score` 要用
HP_REC = {
    "time": "2026-09-19 12:00", "mean": 30000.0, "median": 30500.0, "spread": 4000.0,
    "mad": 1.2, "first": 33000.0, "last": 27000.0, "min": 26000.0, "decay": 18.0,
    "freq_mean": 2200.0, "freq_p50": 2200.0, "freq_n": 100,
    "battery_mean": 32.0, "battery_min": 31.0, "battery_max": 34.0, "battery_n": 100,
    "power_mean": 4.2, "power_min": 3.0, "power_max": 6.0, "power_n": 100,
    "wh": 0.07, "ppw": 7142.0, "thermal": "正常", "sec": 60.0,
    "version": "v0.6.7", "device": "desktop",
    "windows": [1.0, 1.02, 0.99, 0.97, 0.95, 0.93],
}


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


class _FakeTime(object):
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def perf_counter(self):
        return self.t

    def monotonic(self):
        return self.t

    def thread_time(self):
        return 0.0

    def process_time(self):
        return 0.0

    def sleep(self, *_a):
        pass

    def strftime(self, *a):
        import time as _t
        return _t.strftime(*a)

    def strptime(self, *a):
        import time as _t
        return _t.strptime(*a)

    def localtime(self, *a):
        import time as _t
        return _t.localtime(*a)


TMP = tempfile.mkdtemp(prefix="danzhu_histio_")
_FT = _FakeTime()
_READY = [False]


def _boot():
    """起一个真 RootWidget。路径引到 TMP, 但**保留真文件名**。"""
    if _READY[0]:
        raise RuntimeError("Kivy 一个进程只能建一次窗口")
    _READY[0] = True
    from kivy.clock import Clock
    from kivy.core.window import Window

    Clock._max_fps = 0.0
    Clock.time = lambda: _FT.t
    for a in ("_last_tick", "_start_tick", "_duration_ts0"):
        setattr(Clock, a, 0.0)
    Clock._last_fps_tick = None

    import danzhu.ui.app as appmod
    import danzhu.ui.bench as B
    import danzhu.ui.play as P
    for name, mod in list(sys.modules.items()):
        if name.startswith("danzhu") and mod is not None and hasattr(mod, "time"):
            try:
                mod.time = _FT
            except Exception:
                pass
    for cls in (P.PlayMixin, B.BenchMixin):
        for nm in _EXPECT_NAMES:
            raw = cls.__dict__.get(nm)
            if raw is None:
                continue
            fn = raw.__func__ if isinstance(raw, staticmethod) else raw
            real = fn() if isinstance(raw, staticmethod) else fn(None)
            _REAL_NAMES[nm] = os.path.basename(real)
            setattr(cls, nm, staticmethod(lambda _b=os.path.basename(real):
                                          os.path.join(TMP, _b)))
    import danzhu.platform.device as dev
    dev._cfg_post = lambda cfg, path: True      # 设定落盘另有 `cfg_thread.py` 管
    app = appmod.PlinkoApp()
    app.build()
    Window.size = (540, 960)
    Clock.unschedule(app.rootw._frame_timed)
    app.rootw.sfx.enabled = False
    return app.rootw, Clock


def _pump(root, n=6):
    for _ in range(n):
        _FT.t += DT
        _FT_clock().tick()
        root._frame(DT)


def _FT_clock():
    from kivy.clock import Clock
    return Clock


def _popups():
    """窗口里所有带 `content` 的弹窗, **最新的在前**。

    ⚠️ `dismiss()` 是**动画** —— 退场中的旧弹窗还挂在 `Window.children` 里, 所以同一时刻
       可能有多个。`Window.children` 是「后加的在前」, 于是 newest-first。
       （第一版取 `found[-1]` = 最老的, 于是拿到的是上一个还没退完场的弹窗, 按钮自然找不到。）
    """
    from kivy.core.window import Window

    found = []

    def walk(w, depth=0):
        if depth > 6:
            return
        for ch in list(getattr(w, "children", []) or []):
            if hasattr(ch, "content") and hasattr(ch, "title"):
                found.append(ch)
            walk(ch, depth + 1)

    walk(Window)
    return found


def _popup_rows(root):
    """数最新那个弹窗 content 里的行控件。"""
    ps = _popups()
    if not ps:
        return None, None
    p = ps[0]
    kinds = []

    def count(w, depth=0):
        if depth > 8:
            return
        for ch in list(getattr(w, "children", []) or []):
            kinds.append(type(ch).__name__)
            count(ch, depth + 1)

    count(p.content)
    return p, kinds


def _find_btn(text, popups=None):
    """在**所有**弹窗里按文字找一个 Button（最新的优先）。"""
    for p in (popups if popups is not None else _popups()):
        out = []

        def walk(w, d=0):
            if d > 8:
                return
            for ch in list(getattr(w, "children", []) or []):
                if type(ch).__name__ == "Button" and getattr(ch, "text", "") == text:
                    out.append(ch)
                walk(ch, d + 1)

        walk(p.content)
        if out:
            return out[0]
    return None


def _dismiss_all(root):
    for p in _popups():
        try:
            p.dismiss()
        except Exception:
            pass
    _pump(root, 8)          # 等退场动画走完, 别让旧弹窗留在下一个判据里


def main():
    root, Clock = _boot()

    print("== 一、四个存档文件名逐字对得上（真字面量, 不是桩名） ==")
    bad = {k: (v, _REAL_NAMES.get(k)) for k, v in _EXPECT_NAMES.items()
           if _REAL_NAMES.get(k) != v}
    check(not bad, "四个路径函数返回的**真文件名**与期望逐字相同",
          "" if not bad else "期望/实得 %r" % (bad,))
    _pump(root, 4)

    bench_p = os.path.join(TMP, _EXPECT_NAMES["_bench_history_path"])
    hp_p = os.path.join(TMP, _EXPECT_NAMES["_hp_history_path"])

    print("\n== 二、**写**：跑分 / 高压各落一条（此前从没写过） ==")
    root.bench_history = [dict(BENCH_REC)]
    root._save_bench_history()
    check(os.path.exists(bench_p), "跑分历史落盘", bench_p)
    with io.open(bench_p, encoding="utf-8") as f:
        got = json.load(f)
    check(got == [BENCH_REC], "盘上就是刚写的那一条（逐字段）",
          "实得 %d 条, 首条 time=%r" % (len(got), got[0].get("time") if got else None))

    root.hp_history = [dict(HP_REC)]
    root._save_hp_history()
    check(os.path.exists(hp_p), "高压历史落盘", hp_p)
    with io.open(hp_p, encoding="utf-8") as f:
        got2 = json.load(f)
    check(got2 == [HP_REC], "盘上就是刚写的那一条（逐字段）", "实得 %d 条" % len(got2))

    print("\n== 三、100 条上限（写 / 读两侧各切一次 `[-100:]`） ==")
    with io.open(bench_p, "w", encoding="utf-8") as f:
        json.dump([dict(BENCH_REC, time="t%d" % i) for i in range(120)], f)
    root.bench_history = [dict(BENCH_REC, time="t%d" % i) for i in range(120)]
    root._save_bench_history()
    with io.open(bench_p, encoding="utf-8") as f:
        capped = json.load(f)
    check(len(capped) == 100 and capped[-1]["time"] == "t119",
          "`_save_*` 落盘时切 `[-100:]`（**保留最后 100 条**）",
          "实得 %d 条, 末条 %r" % (len(capped), capped[-1]["time"] if capped else None))
    check(root.bench_history and len(root.bench_history) == 120,
          "`_save_*` **不**动内存里的列表（只切落盘那一份）",
          "内存 %d 条" % len(root.bench_history))

    print("\n== 四、**读**：新进程/新实例冷启动能读回来（此前恒走 except） ==")
    import danzhu.ui.bench as B
    fresh = B.BenchMixin.__new__(B.BenchMixin)
    fresh.bench_history = []
    fresh._load_bench_history()
    check(len(fresh.bench_history) == 100 and fresh.bench_history[-1]["time"] == "t119",
          "`_load_bench_history` 把 100 条读回来、末条对得上",
          "实得 %d 条" % len(fresh.bench_history))
    # 类型闸: 不是 list 就不收（防坏文件把列表写成 dict）
    for nm, content in (("不是 list", '{"a": 1}'), ("半截 JSON", '[{"time": "x"')):
        with io.open(bench_p, "w", encoding="utf-8") as f:
            f.write(content)
        f2 = B.BenchMixin.__new__(B.BenchMixin)
        f2.bench_history = ["哨兵"]
        f2._load_bench_history()
        check(f2.bench_history == ["哨兵"],
              "%s ⇒ 静默不动(保留原值, 不抛)" % nm, "%r" % (f2.bench_history,))

    print("\n== 五、**渲染非空分支**（这一段从没被执行过） ==")
    with io.open(bench_p, "w", encoding="utf-8") as f:
        json.dump([dict(BENCH_REC, time="t%d" % i) for i in range(3)], f)
    with io.open(hp_p, "w", encoding="utf-8") as f:
        json.dump([dict(HP_REC, time="t%d" % i) for i in range(3)], f)
    root.bench_history = [dict(BENCH_REC, time="t%d" % i) for i in range(3)]
    root.hp_history = [dict(HP_REC, time="t%d" % i) for i in range(3)]
    root.game.round_history = [{"plays": 20, "balance": 1234, "time": 1.0},
                               {"plays": 50, "balance": 999, "time": 2.0}]
    for label, fn in (("跑分历史 _show_bench_history", root._show_bench_history),
                      ("CPU高压历史 _show_hp_history", root._show_hp_history),
                      ("轮次设置 _show_round_settings", root._show_round_settings)):
        try:
            fn()
            _pump(root, 3)
            p, kinds = _popup_rows(root)
            n_lbl = kinds.count("Label") if kinds else 0
            check(p is not None and n_lbl > 0,
                  "%s：**非空列表**下真的建出了控件（%d 个 Label）" % (label, n_lbl),
                  "" if p is not None else "窗口里没找到弹窗")
        except Exception as e:
            check(False, "%s：非空列表下不炸" % label, "%s: %s" % (type(e).__name__, e))
        _dismiss_all(root)      # 关掉, 免得叠窗影响下一轮

    print("\n== 六、**清空**：不可逆 ⇒ 必须先过确认框（从没被调过） ==")
    for label, meth, attr, path in (
            ("跑分", root._clear_bench_history, "bench_history", bench_p),
            ("CPU高压", root._clear_hp_history, "hp_history", hp_p)):
        # ⚠️ `_clear_*_history` **本身不清** —— 它只弹确认框, 真清在 "确定清空" 的回调里
        #    (`bench.py:5047-5055`)。直接调它就以为"清过了"是个假判据。
        try:
            n_before = len(getattr(root, attr))
            meth()
            _pump(root, 3)
            p, _k = _popup_rows(root)
            check(p is not None, "%s 清空 ⇒ 先弹**确认框**（不可逆操作）" % label)
            if p is None:
                continue
            cancel = _find_btn("取消")
            confirm = _find_btn("确定清空")
            check(cancel is not None and confirm is not None,
                  "%s 确认框有「取消」和「确定清空」两个按钮" % label)
            if cancel is None or confirm is None:
                continue
            # ---- 负面路径: 点「取消」一个字都不许删 ----
            cancel.dispatch("on_release")
            _pump(root, 3)
            check(len(getattr(root, attr)) == n_before,
                  "%s 点「取消」⇒ **一条都没删**（内存）" % label,
                  "%d -> %d" % (n_before, len(getattr(root, attr))))
            with io.open(path, encoding="utf-8") as f:
                check(len(json.load(f)) == n_before,
                      "%s 点「取消」⇒ **盘上也没动**" % label)
            # ---- 正面路径: 点「确定清空」 ----
            meth()
            _pump(root, 3)
            p2, _k = _popup_rows(root)
            btn = _find_btn("确定清空")
            check(btn is not None, "%s 再开确认框并找到「确定清空」" % label)
            if btn is None:
                continue
            btn.dispatch("on_release")
            _pump(root, 4)
            with io.open(path, encoding="utf-8") as f:
                on_disk = json.load(f)
            check(getattr(root, attr) == [] and on_disk == [],
                  "%s 点「确定清空」⇒ 内存与**盘上**都空了" % label,
                  "内存 %d 条 / 盘上 %d 条"
                  % (len(getattr(root, attr)), len(on_disk)))
            # 只清自己那张表（两张是分开的案）
            other = hp_p if attr == "bench_history" else bench_p
            other_attr = "hp_history" if attr == "bench_history" else "bench_history"
            with io.open(other, encoding="utf-8") as f:
                check(len(json.load(f)) > 0 or getattr(root, other_attr) == [],
                      "%s 清空**不碰另一张表**" % label)
            # `_confirm` 末尾会当场重开历史面板, 关掉免得叠窗
            _dismiss_all(root)
        except Exception as e:
            check(False, "%s 清空链不炸" % label, "%s: %s" % (type(e).__name__, e))

    print()
    if FAIL:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (len(FAIL), len(FAIL) + N_OK[0]))
        for n in FAIL:
            print("   - %s" % n)
        return 1
    print("门禁结果: 全绿 —— %d 项（文件名 / 写 / 上限 / 读 / 渲染非空 / 清空）"
          % (len(FAIL) + N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
