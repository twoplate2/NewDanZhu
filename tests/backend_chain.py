"""音频后端降级链门禁 —— `open_output()` 本身。

    python tests/backend_chain.py

## 为什么需要它

`tests/audio_gates.py` 为了测闸门逻辑，**把 `open_output` 整个打桩换掉了**
（`B.open_output = lambda: out`）。于是「按优先级选后端 / 逐级降级 / 把每级失败原因
记进 `_BACKEND_ERRORS`」这条链**从来没有被跑过** —— 而它是"一开窗就没声音"这类
事故的第一现场。

⚠️ `tests/fx_gates.py:1339` 的注释里记着 2026-09-11 挖出来的**真 bug**：
`_KivySoundOut` 少了 `loaded_count`，而 `Sfx` 在诊断那条路上会 `getattr` 它。
"少一个方法"在桌面上不一定立刻炸，但诊断面板会静默少一栏。

## 老版留的测试缝

`PLINKO_SFX_BACKEND=kivy|winmm|none` —— 老版自己就写了"不强制就没法在开发机上验它"。
本脚本就用它逐级验。
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ⚠️ 用 os.path 拼, **不写反斜杠字面量** —— `r"...\\android\\..."` 里的 `\\a` 是 BEL,
#    会被悄悄吃掉(实测: 文件里变成 `DanZhu` 后面跟一个响铃符再加 `ndroid`)。
OLD_MAIN = os.path.join(os.path.dirname(ROOT), "android", "main.py")

ROWS = []


def check(ok, name, detail=""):
    ROWS.append(ok)
    print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                           ("  " + detail) if detail else ""))


def _fresh():
    """重新 import 模块, 好让 `_BACKEND_ERRORS` 与 env 从头生效。"""
    for m in [k for k in list(sys.modules) if k.startswith("danzhu.audio.backend")]:
        del sys.modules[m]
    import danzhu.audio.backend as B
    return B


def _methods(path, cls):
    """源码里那个类的方法名集合。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        t = ast.parse(f.read())
    for n in ast.walk(t):
        if isinstance(n, ast.ClassDef) and n.name == cls:
            return set(m.name for m in n.body if isinstance(m, ast.FunctionDef))
    return None


def main():
    print("== 一、按环境变量强制选后端（老版留的测试缝） ==")
    os.environ["PLINKO_SFX_BACKEND"] = "none"
    B = _fresh()
    check(B.open_output() is None, "PLINKO_SFX_BACKEND=none ⇒ 返回 None(静音)")

    os.environ["PLINKO_SFX_BACKEND"] = "kivy"
    B = _fresh()
    o = B.open_output()
    check(isinstance(o, B._KivySoundOut), "=kivy ⇒ _KivySoundOut",
          "%s" % type(o).__name__)
    check(o is not None and getattr(o, "mode", None) == "named",
          "Kivy 后端声明 mode='named'", "mode=%r" % getattr(o, "mode", None))
    check(hasattr(o, "loaded_count"),
          "Kivy 后端有 loaded_count（2026-09-11 那个真 bug 就是少了它）")
    try:
        o.close()
    except Exception:
        pass

    os.environ["PLINKO_SFX_BACKEND"] = "winmm"
    B = _fresh()
    o = B.open_output()
    # ⚠️ 这里**有三种结局, 不是两种**: ① 建起 `_WaveOut`; ② 本机没有 winmm 输出设备
    #    (`_winmm is None` —— 声音设备被禁用 / 远程会话 / 无声卡时 `waveOutGetNumDevs()==0`,
    #    与老版 `main.py:3384` 同一行判据) ⇒ **降级到 Kivy 是正确行为**, 不是缺陷;
    #    ③ 什么都没起来 ⇒ None。
    #    ⛔ **绝不静默跳过**: 无设备时明说"本机没设备、winmm 本身没验到", 免得把"没验"
    #    读成"验过了"(实测踩过: 有设备时绿、设备消失后变红, 而红的是环境不是代码)。
    if getattr(B, "_winmm", None) is None:
        check(isinstance(o, B._KivySoundOut) or o is None,
              "=winmm ⇒ 本机无 winmm 输出设备 ⇒ 正确地降级(**winmm 本身没验到**)",
              "后端 %s / waveOutGetNumDevs()=0" % type(o).__name__)
        try:
            o.close()
        except Exception:
            pass
    elif o is None:
        check(True, "=winmm ⇒ 本机建不起来(退回 None)", "跳过后面的 winmm 检查")
    else:
        check(isinstance(o, B._WaveOut), "=winmm ⇒ _WaveOut", "%s" % type(o).__name__)
        check(getattr(o, "mode", None) == "pcm", "winmm 后端声明 mode='pcm'",
              "mode=%r" % getattr(o, "mode", None))
        check(not getattr(o, "needs_worker", False),
              "winmm **不**声明 needs_worker(桌面实测 0.9ms/次, 不值得起线程)")
        try:
            o.close()
        except Exception:
            pass

    print("\n== 二、三个后端的**方法集与老版逐名相同** ==")
    # ⚠️⚠️ **不要自己列"每个后端该有哪些方法"** —— 第一版就是这么写的, 于是把
    #    「pcm 后端也得有 play_named」当成判据, 报了 6 条**假 FAIL**。
    #    真正的契约是「**与老版逐名相同**」: 各后端只带自己那条路要用的方法。
    #    ⇒ 直接去老版源码抽集合来比, 不手抄。
    if not os.path.exists(OLD_MAIN):
        check(False, "读老版真值做方法集对账", "找不到 %s" % OLD_MAIN)
    else:
        NEWBK = os.path.join(ROOT, "danzhu", "audio", "backend.py")
        for cls in ("_WaveOut", "_SoundPoolOut", "_KivySoundOut"):
            a, b = _methods(OLD_MAIN, cls), _methods(NEWBK, cls)
            if a is None or b is None:
                check(False, "%s 方法集对账" % cls,
                      "抽不到: 老=%s 新=%s" % (a is None, b is None))
                continue
            miss, extra = sorted(a - b), sorted(b - a)
            check(not miss and not extra,
                  "%s 方法集与老版逐名相同（%d 个）" % (cls, len(a)),
                  "" if not miss and not extra else "少 %r / 多 %r" % (miss, extra))

    print("\n== 三、逐级降级 + `_BACKEND_ERRORS` 记原文 ==")
    os.environ.pop("PLINKO_SFX_BACKEND", None)
    B = _fresh()
    check(B.platform != "android", "本机不是 android（降级链的桌面分支才走得到）",
          "platform=%r" % B.platform)

    _ok = B._KivySoundOut

    class _Boom(object):
        def __init__(self, *a, **k):
            raise RuntimeError("boom-%s" % self.__class__.__name__)

    # 两级都炸 ⇒ 必须一路退到 None，且两级原因都记下来
    B._WaveOut = type("_WaveOut", (_Boom,), {})
    B._KivySoundOut = type("_KivySoundOut", (_Boom,), {})
    B._BACKEND_ERRORS[:] = []
    check(B.open_output() is None, "两级都建不起来 ⇒ 返回 None(绝不抛)")
    names = [n for n, _e in B._BACKEND_ERRORS]
    check("winmm" in names and "Kivy-SoundLoader" in names,
          "两级失败**都**记进了 _BACKEND_ERRORS", "%r" % (names,))
    check(all("RuntimeError: boom" in e for _n, e in B._BACKEND_ERRORS),
          "记的是**异常原文**（不是一句「失败了」）",
          "%r" % ([e for _n, e in B._BACKEND_ERRORS][:2],))

    # 只有 winmm 炸 ⇒ 落到 Kivy，而不是直接静音
    B._WaveOut = type("_WaveOut", (_Boom,), {})
    B._KivySoundOut = _ok
    B._BACKEND_ERRORS[:] = []
    got2 = B.open_output()
    check(isinstance(got2, _ok), "只有 winmm 炸 ⇒ **落到 Kivy**, 不是直接静音",
          "%s" % (type(got2).__name__ if got2 is not None else "None"))
    check([n for n, _e in B._BACKEND_ERRORS] == ["winmm"],
          "只记了失败的那一级", "%r" % ([n for n, _e in B._BACKEND_ERRORS],))
    try:
        got2.close()
    except Exception:
        pass

    print("\n== 四、`_backend_error()` 把账本拼成一行 ==")
    B._BACKEND_ERRORS[:] = [("winmm", "OSError: no device"),
                            ("Kivy-SoundLoader", "boom")]
    line = B._backend_error("winmm")
    check("no device" in line, "_backend_error('winmm') 带那级的原文", line)

    os.environ.pop("PLINKO_SFX_BACKEND", None)
    print()
    bad = ROWS.count(False)
    if bad:
        print("门禁结果: 红 %d 项 / 共 %d 项" % (bad, len(ROWS)))
        return 1
    print("门禁结果: 全绿 —— %d 项（选路 / 方法集 / 降级 / 错误账本）" % len(ROWS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
