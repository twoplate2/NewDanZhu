"""启动分帧加载局部遮蔽图集；游玩绘制只查缓存，不同步读盘或烘焙。"""
import os
from kivy.core.image import Image as CoreImage
from ..fx.pile import CX,FLOOR_Y,K2

_CACHE={}
_FAILED=set()
_ROOT=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'assets','cup_shadows')


def attempted(count,variant):
    return (count,variant) in _CACHE or (count,variant) in _FAILED


def warm(count,variant):
    key=(count,variant)
    if key in _CACHE or key in _FAILED:return
    try:
        from ..fx.cup_shadow_data import DATA,TILE,CELL,PAD,STRENGTH
        entry=DATA[key]
        tex=CoreImage(os.path.join(_ROOT,entry['file']),mipmap=True).texture
        tex.mag_filter='linear';tex.min_filter='linear_mipmap_linear';tex.bind()
        regions=[]
        for i in range(count):
            x=i%entry['columns']*CELL+PAD
            y=tex.height-(i//entry['columns']*CELL+PAD+TILE)
            region=tex.get_region(x,y,TILE,TILE)
            region.mag_filter='linear';region.min_filter='linear_mipmap_linear'
            regions.append(region)
        _CACHE[key]=(tex,tuple(regions),entry,STRENGTH)
    except Exception:
        _FAILED.add(key)


def lookup(count,variant,proj):
    cached=_CACHE.get((count,variant))
    if cached is None:return None
    _,regions,entry,strength=cached
    if len(proj)!=count:return None
    for p,point in zip(proj,entry['points']):
        actual=(p['sx']-CX,p['z'],FLOOR_Y-p['sy']-K2*p['z'])
        if abs(p['r']-entry['radius'])>.0001 or any(abs(a-b)>.0002 for a,b in zip(actual,point)):
            return None  # 表损坏/回退布局不能套用原布局的阴影。
    return regions,entry['peers'],strength
