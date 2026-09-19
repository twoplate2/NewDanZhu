"""安卓 JNI 路径门禁 —— 让「桌面永远是死代码」的那几条分支真的跑起来。

    python tests/android_jni.py

## 为什么需要它

`danzhu/audio/backend.py` 的音频就绪闸门族（`_attach_gates` / `gate_expected` /
`gate_all_ready` / `gate_ready_ms` / `gate_first_missing` / `gate_info`）以及
`danzhu/platform/device.py` 的整套 JNI 胶水，**在桌面上一条都不会执行** ——
取不到 `jnius` / 取不到 Java 类 ⇒ 全走"不可用"分支。

`tests/fx_gates.py` 那 380 条里对它们的覆盖也**只是读源码逐行比对**（"静态确认"）。
⇒ 改错一个聚合规则（例如把"两池都就绪才为真"写成"任一个就绪"）在桌面**全绿**，
到真机上才发现冷启动行为不对。

## 本脚本做两件事（都是可证伪的）

**一、Java 签名交叉核对**：解析 `java/com/plinko/SoundGate.java` 的公开方法
（名字 + 形参个数），再扫 `danzhu/` 里所有闸门方法的调用点，逐个核对
「Python 调的这个名字在 Java 里存在、且形参个数对得上」。
⚠️ 这条钉的是**接口**：Java 改了方法名/arity 而 Python 没跟，桌面测不出、真机静默失效。

**二、假 jnius 跑聚合语义**：塞一个假 `jnius` 模块，让 `gate_*` 真的执行，
逐条验判据（"两池都就绪才算"、`readyMs` 取最晚、异常一律退 0/False/-1 而不是抛）。
"""

import ast
import io
import os
import sys
import types

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

JAVA = os.path.join(ROOT, "java", "com", "plinko", "SoundGate.java")
PKG = os.path.join(ROOT, "danzhu")

# Python 侧会调的闸门方法（Java 那份必须都有，且形参个数一致）
GATE_METHODS = {
    "expect": 1, "expectCount": 0, "readyCount": 0, "allReady": 0, "readyMs": 0,
    "failedCount": 0, "firstMissing": 0, "initCount": 0, "callbackTotal": 0,
    "callbackThreadName": 0,
}

ROWS = []


def check(ok, name, detail=""):
    ROWS.append(ok)
    print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                           ("  " + detail) if detail else ""))


def java_methods():
    """{名字: 形参个数} —— 只看 `public` 方法。"""
    with io.open(JAVA, encoding="utf-8", errors="replace") as f:
        tree = ast.parse(f.read())          # Java 不是 Python —— 这条走不通, 见下
    return tree


def java_methods_regex():
    """用正则抽 `public ... 名字(形参)` —— Java 不能用 ast 解。"""
    import re
    out = {}
    with io.open(JAVA, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r"\bpublic\s+(?:static\s+)?[\w<>\[\],\s\.]+?\s+(\w+)\s*\(([^)]*)\)", line)
            if not m:
                continue
            nm, params = m.group(1), m.group(2).strip()
            if nm in ("class", "if", "for", "while", "return"):
                continue
            n = 0 if not params else len([p for p in params.split(",") if p.strip()])
            out[nm] = n
    return out


def py_gate_calls():
    """[(文件:行, 方法名, 实参个数)] —— 所有闸门方法的调用点。"""
    out = []
    for dp, dns, fns in os.walk(PKG):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            try:
                tree = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                nm = getattr(f, "attr", None)
                if nm in GATE_METHODS:
                    out.append(("%s:%d" % (os.path.relpath(p, ROOT), n.lineno),
                                nm, len(n.args) + len(n.keywords)))
    return out


class _FakeGate(object):
    """假闸门: 值由构造参数给, 可选抛异常。"""

    def __init__(self, expect=0, ready=0, allready=False, ms=-1.0, missing=0,
                 failed=0, boom=False):
        self._v = dict(expect=expect, ready=ready, allready=allready, ms=ms,
                       missing=missing, failed=failed)
        self._boom = boom

    def _g(self, k):
        if self._boom:
            raise RuntimeError("boom")
        return self._v[k]

    def expectCount(self):
        return self._g("expect")

    def readyCount(self):
        return self._g("ready")

    def allReady(self):
        return self._g("allready")

    def readyMs(self):
        return self._g("ms")

    def firstMissing(self):
        return self._g("missing")

    def failedCount(self):
        return self._g("failed")


def _mk(g1=None, g2=None, ids=None, ids2=None):
    from danzhu.audio.backend import _SoundPoolOut
    o = _SoundPoolOut.__new__(_SoundPoolOut)     # 跳过 __init__(它要真 SoundPool)
    o._gate, o._gate2 = g1, g2
    o._gate_err = ""
    if ids is not None:
        o._ids = ids
    if ids2 is not None:
        o._ids2 = ids2
    return o


def part1():
    print("== 一、Java 签名交叉核对 ==")
    jm = java_methods_regex()
    check(len(jm) >= 10, "从 SoundGate.java 抽到公开方法", "%d 个: %s"
          % (len(jm), ", ".join(sorted(jm))))
    calls = py_gate_calls()
    check(len(calls) >= 10, "Python 侧找到闸门方法调用点", "%d 处" % len(calls))
    missing, arity = [], []
    for where, nm, n in calls:
        if nm not in jm:
            missing.append("%s 调了 %s() —— Java 里没有" % (where, nm))
        elif jm[nm] != n:
            arity.append("%s 调 %s(%d 参) —— Java 是 %d 参"
                         % (where, nm, n, jm[nm]))
    check(not missing, "每个 Python 调用的方法在 Java 里都存在",
          "" if not missing else "; ".join(missing[:4]))
    check(not arity, "形参个数逐个对得上",
          "" if not arity else "; ".join(arity[:4]))


def part_contract():
    """钉住 Python 侧赖以区分「没满」与「满了」的那条**跨语言契约**。

    `_SoundPoolOut.gate_ready_ms()` 是这么判的: `any(_x < 0) ⇒ 回 -1`。
    它成立的前提是 **Java 的 `readyMs()` 在未就绪时返 -1** —— 这条契约只活在
    SoundGate.java 的一行里, 而 Java 改了它 Python 侧**不会报错**, 只会把
    「还没满」读成「满编 X ms」(冷启动诊断直接失真)。
    """
    src = io.open(JAVA, encoding="utf-8", errors="replace").read()
    import re
    m = re.search(r"public\s+double\s+readyMs\s*\(\)\s*\{(.*?)\n\s*\}", src, re.S)
    body = m.group(1) if m else ""
    check("return -1.0" in body and "allReadyAt == 0L" in body,
          "Java readyMs() 未就绪时返 -1(Python 侧靠它区分「没满/满了」)",
          "SoundGate.java:157")


def part2():
    print("\n== 二、假 jnius 跑聚合语义（这些分支在桌面上零执行） ==")
    # 两池都满
    a, b = _FakeGate(40, 40, True, 120.0), _FakeGate(61, 61, True, 340.0)
    o = _mk(a, b)
    check(o.gate_expected() == 101, "gate_expected = 两池之和", "%s" % o.gate_expected())
    check(o.gate_ready_count() == 101, "gate_ready_count = 两池之和",
          "%s" % o.gate_ready_count())
    check(o.gate_all_ready() is True, "两池都满 ⇒ all_ready 真")
    check(o.gate_ready_ms() == 340.0, "readyMs 取**最晚**那个池", "%s" % o.gate_ready_ms())

    # 一池差一个 —— 关键判据
    # ⚠️⚠️ 夹具必须**忠实于 Java 的契约**: `SoundGate.readyMs()` 在未就绪时返 **-1.0**
    #    (`if (allReadyAt == 0L || firstExpectAt == 0L) return -1.0;`, SoundGate.java:157)。
    #    第一版夹具给了「未就绪 + ms=120」这个真闸门**永远不会产出**的组合, 于是
    #    Python 侧的 `any(_x < 0)` 判据被绕过、测试报了一个假 FAIL。
    a2 = _FakeGate(40, 39, False, -1.0)
    o2 = _mk(a2, b)
    check(o2.gate_all_ready() is False,
          "**差一个就不算**(两池都要满)", "池1 40/39")
    check(o2.gate_ready_ms() == -1.0, "没全满 ⇒ readyMs 回 -1", "%s" % o2.gate_ready_ms())
    check(o2.gate_ready_count() == 100, "ready_count 照常给 100(诊断要用)",
          "%s" % o2.gate_ready_count())

    # 没有任何闸门
    o3 = _mk(None, None)
    check(o3.gate_all_ready() is False, "没有闸门 ⇒ all_ready 假(不是异常)")
    check(o3.gate_ready_ms() == -1.0, "没有闸门 ⇒ readyMs -1")
    check(o3.gate_expected() == 0 and o3.gate_ready_count() == 0, "没有闸门 ⇒ 计数 0")
    check(o3.gate_first_missing() is None, "没有闸门 ⇒ first_missing None")

    # 闸门抛异常 —— 一律退化成安全值, 绝不抛
    ob = _mk(_FakeGate(boom=True), _FakeGate(boom=True))
    check(ob.gate_expected() == 0, "闸门抛异常 ⇒ expected 退 0")
    check(ob.gate_ready_count() == 0, "闸门抛异常 ⇒ ready_count 退 0")
    check(ob.gate_all_ready() is False, "闸门抛异常 ⇒ all_ready 退假")
    check(ob.gate_ready_ms() == -1.0, "闸门抛异常 ⇒ readyMs 退 -1")

    # first_missing 必须带池序号反查（两池 sampleId 各自从 1 编号）
    o4 = _mk(_FakeGate(40, 39, False, missing=0), _FakeGate(61, 60, False, missing=7),
             ids={"peg0": 1, "peg1": 2}, ids2={"voice_a": 5, "voice_b": 7})
    got = o4.gate_first_missing()
    check(got == (1, "voice_b"),
          "first_missing 带**池序号**反查(两池 sid 各自从 1 编号)", "%r" % (got,))
    o5 = _mk(_FakeGate(40, 39, False, missing=2), _FakeGate(61, 61, True),
             ids={"peg0": 1, "peg1": 2})
    check(o5.gate_first_missing() == (0, "peg1"), "池 0 缺的那颗也认得出来",
          "%r" % (o5.gate_first_missing(),))
    # sid 不在表里 —— 兜底报 sid
    o6 = _mk(_FakeGate(40, 39, False, missing=99), None, ids={"peg0": 1})
    check(o6.gate_first_missing() == (0, "sid=99"),
          "sid 查不到名字时兜底报 sid(不静默吞)", "%r" % (o6.gate_first_missing(),))


def part3():
    print("\n== 三、gate_info 在假 jnius 下能跑出行 ==")
    fake = types.ModuleType("jnius")

    class _G(object):
        @staticmethod
        def initCount():
            return 3

        @staticmethod
        def callbackTotal():
            return 101

        @staticmethod
        def callbackThreadName():
            return "SoundPoolThread"

    fake.autoclass = lambda name: _G if name == "com.plinko.SoundGate" else _G
    sys.modules["jnius"] = fake
    try:
        o = _mk(_FakeGate(40, 40, True, 120.0), _FakeGate(61, 61, True, 340.0))
        line = o.gate_info()
        check("闸门 本进程第 3 次" in line and "回调 101 条" in line
              and "SoundPoolThread" in line and "就绪 101/101" in line
              and "满编 340ms" in line,
              "gate_info 一行含 次数/回调/线程/就绪/满编", line)
    finally:
        sys.modules.pop("jnius", None)


def main():
    part1()
    part_contract()
    part2()
    part3()
    print()
    bad = ROWS.count(False)
    if bad:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (bad, len(ROWS)))
        return 1
    print("门禁结果: 全绿 —— %d 项(JNI 接口 + 闸门聚合语义)" % len(ROWS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
