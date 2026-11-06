"""游戏分类注入幂等，保持方向/主题及其他应用属性。"""
import importlib.util
import contextlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('game_manifest_hook',ROOT/'p4a/hook.py')
hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
ANDROID='{http://schemas.android.com/apk/res/android}'


class ManifestTests(unittest.TestCase):
    def test_game_category_all_entries_and_idempotence(self):
        for category in ('','android:appCategory="productivity"',"android:appCategory='game'"):
            with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
                path=Path('src/main/AndroidManifest.xml');path.parent.mkdir(parents=True)
                path.write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.danzhu.tiao">
<application android:label="test" android:theme="@style/PlinkoTheme" %s>
<activity android:name="org.kivy.android.PythonActivity" android:screenOrientation="portrait"/>
<activity android:name="example.BenchmarkActivity"/>
</application></manifest>'''%category,encoding='utf-8')
                hook._inject_manifest(SimpleNamespace())
                hook._inject_game_category(SimpleNamespace())
                before=path.read_text(encoding='utf-8')
                self.assertEqual(hook._inject_game_category(SimpleNamespace()),0)
                self.assertEqual(path.read_text(encoding='utf-8'),before)
                app=ET.fromstring(before).find('application')
                self.assertEqual(app.get(ANDROID+'appCategory'),'game')
                self.assertEqual(app.get(ANDROID+'theme'),'@style/PlinkoTheme')
                self.assertEqual(len(app.findall('activity')),2)
                self.assertEqual(app.find('activity').get(ANDROID+'screenOrientation'),'fullSensor')



if __name__=='__main__':unittest.main()
