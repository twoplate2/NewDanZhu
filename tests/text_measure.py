"""单行宽度与旧纹理路径一致，测量本身不再创建纹理循环。"""
from pathlib import Path
import gc
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kivy.core.window import Window
from kivy.core.text import Label as CoreLabel
from danzhu.ui.text import text_px


def main():
    strings=['本轮游戏 50 次已结束','累计123投12中(10%)','0.1%Low：平均72.14/最低65.31',
             ' 温度：31.0度 功耗：1.70W ','0.00','0000000',' ','一\n二','a\tb']
    strings += ['%d x%d'%(i*137,i) for i in range(50)]
    checks=0
    for fs in (8,10,12,13,13.2,13.3333,15,17,18,19,24,32,48):
        for bold in (False,True):
            for text in strings:
                old=CoreLabel(text=text,font_size=fs,bold=bold,text_size=(None,None));old.refresh()
                assert text_px(text,fs,bold,force=True)==old.texture.size[0],(text,fs,bold)
                checks+=1
    original=CoreLabel.refresh;calls=[0]
    def counted(self,*a,**kw):calls[0]+=1;return original(self,*a,**kw)
    was_enabled=gc.isenabled();gc.disable()
    try:
        del old;gc.collect(2)
        with patch.object(CoreLabel,'refresh',counted):
            for i in range(200):text_px('宽度测量%d'%i,15,force=True)
        assert calls[0]==0,calls[0]
        unreachable=gc.collect(2)
        assert unreachable<100,unreachable
    finally:
        if was_enabled:gc.enable()
    print('PASS: %d exact width comparisons; 200 single-line measures / 0 refresh; unreachable=%d'%(checks,unreachable))


if __name__=='__main__':main()
