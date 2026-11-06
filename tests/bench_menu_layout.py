"""用实际 Kivy 排版验证菜单换行，不运行游戏或改写存档。"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kivy.core.text.markup import MarkupLabel
from danzhu.ui.bench import _bench_menu_desc


def plain(text):
    return re.sub(r'\[/?(?:color[^\]]*|b)\]', '', text)


class MenuLayoutTests(unittest.TestCase):
    def test_native_lines_fit_and_preserve_all_text(self):
        expected = re.sub(r'\s+', '', plain(_bench_menu_desc()))
        for width in (220, 275, 440, 700):
            for size in (15, 19.5, 30):
                with self.subTest(width=width, size=size):
                    text = _bench_menu_desc(width, size)
                    label = MarkupLabel(text=text, font_size=size,
                                        text_size=(width, None), halign='left')
                    label.refresh()
                    lines = label._cached_lines
                    # 原生控件不应再次提前折行；所有字都必须仍然存在。
                    self.assertEqual(len(lines), len(text.splitlines()))
                    rendered = [''.join(word.text for word in line.words) for line in lines]
                    self.assertEqual(re.sub(r'\s+', '', ''.join(rendered)), expected)
                    for line, row in zip(lines, rendered):
                        self.assertLessEqual(line.w, width)
                        self.assertNotRegex(row.lstrip(), r'^[，。、：；！？]')

    def test_titles_continue_into_body_and_terms_stay_together(self):
        for width in (275, 440, 700):
            text = plain(_bench_menu_desc(width, 15))
            self.assertIn('CPU 高压测试：持续', text)
            self.assertIn('快速测试：在100秒', text)
            self.assertIn('帧率测试：查看', text)
            self.assertIn('1% Low 帧', text)
            self.assertNotIn('C\nPU', text)
            self.assertNotIn('功\n耗', text)
            self.assertNotIn('测\n试', text)
            self.assertNotIn('信\n息', text)


if __name__ == '__main__':
    unittest.main()
