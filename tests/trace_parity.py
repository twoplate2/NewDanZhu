"""老版 ⇄ 重构版 **行为轨迹逐字段对账** —— UI 端口的验收闸门。

    python tests/trace_parity.py

读 `tests/golden/trace_old.json`（老版真值）与 `tests/golden/trace_new.json`（重构版），
逐段比：帧状态 / 状态迁移 / 结算 / **真发出去的音效** / 布局几何。

## 为什么这条门禁比 `parity.py` 更硬

`parity.py` 比的是**库**（物理、盘面、球堆、音频合成）。这一条比的是**整局游戏的对外行为**：
同一串脚本化输入进去，同一串可观测轨迹出来 —— 覆盖 UI 派发、帧循环、状态机接线、
音效五道闸、装杯演出的时间轴。任何一处接错，这里都会分叉。

## 口径

* **帧**：逐帧比 8 个字段（state/power/ball/balance/round_plays/plays/hits/fx）。
  第一处不同就停（后面的必然跟着漂），但**把两边各自的上下文打出来**。
* **迁移 / 结算 / 音效 / 布局**：逐项比，报了还能继续看别的段。
* 帧数不同不算错本身（可能是一边多推了几帧才落定），但会**明确报出来**。

⚠️ 这条门禁**没有**"重刷基线"这条路 —— 老版那份是从另一个 git 仓库的既存产物里采的，
   改它就是改判据。红了就去改新版。
"""

import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")

FRAME_FIELDS = ("state", "power", "ball", "balance", "round_plays", "plays", "hits", "fx")


def load(name):
    p = os.path.join(GOLDEN, name)
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


class Report(object):
    def __init__(self):
        self.fail = 0
        self.notes = []

    def ok(self, msg):
        print("[绿] " + msg)

    def bad(self, msg):
        self.fail += 1
        print("[红] " + msg)

    def note(self, msg):
        self.notes.append(msg)
        print("[注] " + msg)


def cmp_frames(old, new, rep):
    """逐帧比 8 个字段；报第一处分叉 + 两边上下文。"""
    n = min(len(old), len(new))
    for i in range(n):
        a, b = old[i], new[i]
        for k in FRAME_FIELDS:
            if a.get(k) != b.get(k):
                rep.bad("帧 %d 字段 `%s` 不同:" % (i, k))
                print("       老版 %r" % (a,))
                print("       新版 %r" % (b,))
                if i:
                    print("       上一帧老版 %r" % (old[i - 1],))
                    print("       上一帧新版 %r" % (new[i - 1],))
                return i
    if len(old) != len(new):
        rep.note("帧数不同: 老版 %d / 新版 %d —— 逐帧比只覆盖了前 %d 帧"
                 % (len(old), len(new), n))
    else:
        rep.ok("帧: %d 帧 x %d 字段逐位相符" % (len(old), len(FRAME_FIELDS)))
    return None


def cmp_list(name, old, new, rep, keyfn=None):
    """逐项比一个列表（迁移 / 结算）。"""
    if len(old) != len(new):
        rep.bad("%s 条数不同: 老版 %d / 新版 %d" % (name, len(old), len(new)))
    n = min(len(old), len(new))
    for i in range(n):
        a, b = old[i], new[i]
        if a != b:
            rep.bad("%s 第 %d 项不同:" % (name, i))
            print("       老版 %r" % (a,))
            print("       新版 %r" % (b,))
            return
    if len(old) == len(new):
        rep.ok("%s: %d 项逐位相符" % (name, len(old)))


def cmp_sounds(old, new, rep):
    """音效**逐条按序**比 (name, gain, throttle, frame, t)。"""
    if len(old) != len(new):
        rep.bad("音效条数不同: 老版 %d / 新版 %d" % (len(old), len(new)))
    n = min(len(old), len(new))
    for i in range(n):
        a, b = old[i], new[i]
        if a != b:
            rep.bad("音效第 %d 条不同:" % i)
            print("       老版 %r" % (a,))
            print("       新版 %r" % (b,))
            lo = max(0, i - 3)
            print("       前三条老版 %r" % (old[lo:i],))
            print("       前三条新版 %r" % (new[lo:i],))
            # 名字集合的差异能直接指出"少了哪一声"
            rem_a = [x["name"] for x in old[i:]]
            rem_b = [x["name"] for x in new[i:]]
            miss = [x for x in rem_a if x not in rem_b]
            if miss:
                rep.note("从这里往后, 老版有而新版**没发**的音效: %r" % (sorted(set(miss)),))
            extra = [x for x in rem_b if x not in rem_a]
            if extra:
                rep.note("新版多发的: %r" % (sorted(set(extra)),))
            return
    if len(old) == len(new):
        rep.ok("音效: %d 条逐位相符(名字/增益/节流/帧/时刻)" % len(old))


def cmp_layout(old, new, rep):
    for seg in ("warmup", "after_win"):
        a = (old or {}).get(seg) or {}
        b = (new or {}).get(seg) or {}
        if not a or not b:
            rep.bad("布局段 `%s` 缺: 老版 %d 项 / 新版 %d 项" % (seg, len(a), len(b)))
            continue
        bad = []
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                bad.append((k, a.get(k), b.get(k)))
        if bad:
            rep.bad("布局 `%s`: %d/%d 个控件几何不同" % (seg, len(bad), len(a)))
            for k, x, y in bad[:8]:
                print("       %-18s 老版 %-28r 新版 %r" % (k, x, y))
            if len(bad) > 8:
                print("       …还有 %d 个" % (len(bad) - 8))
        else:
            rep.ok("布局 `%s`: %d 个控件几何逐位相符" % (seg, len(a)))


def main():
    old, new = load("trace_old.json"), load("trace_new.json")
    rep = Report()

    mo, mn = old.get("meta", {}), new.get("meta", {})
    for k in ("dt", "seed"):
        if mo.get(k) != mn.get(k):
            rep.bad("meta.%s 不同: 老版 %r / 新版 %r" % (k, mo.get(k), mn.get(k)))
    print("[trace] 老版 %.0f 字节 / 新版 %.0f 字节"
          % (os.path.getsize(os.path.join(GOLDEN, "trace_old.json")),
             os.path.getsize(os.path.join(GOLDEN, "trace_new.json"))))
    print()

    cmp_layout(old.get("layout"), new.get("layout"), rep)
    cmp_frames(old.get("frames") or [], new.get("frames") or [], rep)
    cmp_list("状态迁移", old.get("transitions") or [], new.get("transitions") or [], rep)
    cmp_list("结算", old.get("settles") or [], new.get("settles") or [], rep)
    cmp_sounds(old.get("sounds") or [], new.get("sounds") or [], rep)

    so, sn = old.get("summary", {}), new.get("summary", {})
    diff = {k: (so.get(k), sn.get(k)) for k in set(so) | set(sn) if so.get(k) != sn.get(k)}
    if diff:
        rep.bad("summary 有 %d 项不同: %r" % (len(diff), diff))
    else:
        rep.ok("summary: %d 项逐位相符" % len(so))

    # 总账: 除了 `meta.driver`(那是驱动器自己的名字, 本来就该不同), **逐字节相同**。
    import hashlib
    import json
    a = {k: v for k, v in old.items() if k != "meta"}
    b = {k: v for k, v in new.items() if k != "meta"}
    sa = hashlib.sha1(json.dumps(a, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")).hexdigest()
    sb = hashlib.sha1(json.dumps(b, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")).hexdigest()
    if sa == sb:
        rep.ok("除 meta.driver 外**逐字节相同**  sha1 %s" % sa)
    else:
        rep.bad("除 meta.driver 外仍有差异  sha1 %s / %s" % (sa, sb))

    print()
    if rep.fail:
        print("对账结果: **红 %d 处**" % rep.fail)
        return 1
    print("对账结果: 全绿 —— 同一串脚本化输入, 两边行为逐位一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
