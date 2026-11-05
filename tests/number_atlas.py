"""实际游戏 HUD 计数更新不再重建文字；核对图集、色调和无图集回退。"""
from unittest.mock import patch
from bench_smoke import _boot


def main():
    root,_,_= _boot()
    from kivy.core.text import Label as CoreLabel
    from kivy.uix.label import Label
    from danzhu.ui.number_cell import NumberCell
    from danzhu.ui.text import _set_lbl_tint
    stats=root.stats_lbl
    stats.width=500;stats.font_size=15;stats._fit_base=15
    assert stats._rec is not None and not stats._fallback
    counter=[0]
    original=CoreLabel.refresh
    def counted(self,*args,**kwargs):
        counter[0]+=1
        return original(self,*args,**kwargs)
    with patch.object(CoreLabel,'refresh',counted):
        for i in range(100,200):
            root.game.plays=i;root.game.hits=i//3
            root._act(root.game._refresh_stats)
        assert counter[0]==0,('normal HUD rasterizations',counter[0])
    old=Label(font_size=15,bold=True,size=(500,30))
    with patch.object(CoreLabel,'refresh',counted):
        for i in range(100,200):
            old.text='累计%d投%d中(33%%)'%(i,i//3)
            old.texture_update()
        assert counter[0]>=100
    _set_lbl_tint(stats,(.5,.5,.5,.6));rgba=tuple(stats._color.rgba)
    stats.text='累计200投70中(35%)'
    assert tuple(stats._color.rgba)==rgba
    for size in (13.2,13.3333333333,15,17,26):
        number=NumberCell(caption='当前',font_size=size,text='120.00')
        assert number._rec is not None,(size,'atlas unavailable')
        assert len(number._quads[0])==6
    print('PASS: normal HUD 100 updates / 0 CoreLabel refresh; old Label >=100; decimal atlas and tint preserved')


if __name__=='__main__':main()
