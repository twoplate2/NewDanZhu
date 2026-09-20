# -*- coding: utf-8 -*-
"""字形图集 `wait_bake` 的三条分岔 —— 余额行「零填纹」这条路的**唯一**判据。

跑法:  python tests/glyph_gate.py      (在 new_danzhu/ 下)
⚠️ **必须真 Kivy 窗口**(`_glyph_bake` 走 `CoreLabel`, 无窗口下段错误) ⇒ 不能并进 fx_gates。

背景: `_degrade()` 是**回不去**的, 而余额行在布局期建、图集是启动后分帧预热的
⇒ 建标签那一刻必然还没烘出来。没有 `wait_bake`, 当场永久退化 ⇒ 收益**静默归零**。
"""
import os
import sys

sys.path.insert(0, os.getcwd())

# ⚠️ 与其它门禁同款: Windows 控制台默认 GBK, 而本文件的判据文本含 `⇒` 等字符 ——
#    不重设编码就会 `UnicodeEncodeError` **在打印判据的那一刻崩掉**(而不是判红),
#    看起来像门禁坏了, 其实是编码。**门禁不许依赖调用者先 export 环境变量。**
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from kivy.app import App                  # noqa: E402
from kivy.uix.widget import Widget        # noqa: E402

import danzhu.ui.text as T                # noqa: E402
from danzhu.ui import widgets as W        # noqa: E402

FAIL = []


def ck(cond, msg, got=""):
    print("  [%s] %s%s" % ("OK  " if cond else "FAIL", msg,
                           ("   " + str(got)) if got else ""))
    if not cond:
        FAIL.append(msg)


class Probe(App):
    def build(self):
        return Widget()

    def on_start(self):
        T._GLYPH_ON = True
        FS = 42.0
        _keep_bake = T._glyph_bake
        try:
            print("== wait_bake: 图集还没烘好时**等**, 不退化 ==")
            T._GLYPH_WARM_DONE[0] = False
            del T._GLYPH_WAITERS[:]
            a = W.GlyphLabel(text="12345", font_size=FS, bold=True, wait_bake=True)
            ck(a._quads is None and not a._degraded and a._fb is None,
               "图集未就绪 ⇒ 保持等待(_quads 空 / **没退化** / 没挂 fb)",
               "quads=%r degraded=%r fb=%r" % (a._quads, a._degraded, a._fb))
            ck(a in T._GLYPH_WAITERS, "把自己登记进 `_GLYPH_WAITERS`(预热跑完要靠它叫醒)",
               "waiters=%d" % len(T._GLYPH_WAITERS))

            print("== 对照: wait_bake=False(中奖大字) 同样条件 ⇒ **立即永久退化** ==")
            b = W.GlyphLabel(text="12345", font_size=FS, bold=True)
            ck(b._degraded and b._fb is not None,
               "非 wait_bake 保持原语义: 一局里要么图集要么老路(**不撤回**)",
               "degraded=%r" % (b._degraded,))

            print("== 预热跑完 ⇒ flush 叫醒, 升级到图集 ==")
            rec = T._glyph_bake(FS, True)
            ck(rec is not None, "先确认这一档真能烘出来", "rec=%s" % (rec is not None,))
            T._glyph_put(FS, True, rec)
            T._GLYPH_WARM_DONE[0] = True
            T._glyph_flush_waiters()
            ck(a._quads is not None and not a._degraded,
               "**升级成功** —— 余额行真的走字形图集了(零纹理重建)",
               "quads=%d 段" % (len(a._quads) if a._quads else 0))
            ck(not T._GLYPH_WAITERS, "等待队列已清空(否则每帧都要遍历它)")

            print("== 分岔③: 预热已跑完、这一档**真的没有** ⇒ 必须退化(不许永久空白) ==")
            del T._GLYPH_WAITERS[:]
            c = W.GlyphLabel(text="12345", font_size=FS + 7.0, bold=True, wait_bake=True)
            ck(c._degraded and c._fb is not None,
               "已跑完仍拿不到 ⇒ 退化(不是干等) —— 这条防的是**余额永久空白**",
               "degraded=%r" % (c._degraded,))

            print("== 分岔②b: 字符集外(汉字) ⇒ 无论 wait_bake 一律退化 ==")
            T._GLYPH_WARM_DONE[0] = False        # 就算"还在烘"也不许拿汉字去等
            d = W.GlyphLabel(text="累计", font_size=FS, bold=True, wait_bake=True)
            ck(d._degraded, "「累计」不在 `_GLYPH_CHARS` 里 ⇒ 立即退化(汉字没有字形)",
               "degraded=%r" % (d._degraded,))
            d2 = W.GlyphLabel(text="789", font_size=FS, bold=True, wait_bake=True)
            ck(not d2._degraded, "同一个标签换成纯数字就又能等(判据是**串本身**, 不比对一个)",
               "degraded=%r" % (d2._degraded,))

            print("== 退出 ==")
            del T._GLYPH_WAITERS[:]
            T._GLYPH_WARM_DONE[0] = True
        finally:
            T._glyph_bake = _keep_bake
        print("\n结果: %s" % ("全绿" if not FAIL else ("红 —— %d 条: %s" % (len(FAIL), FAIL))))
        self.stop()


Probe().run()
