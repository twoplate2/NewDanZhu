"""离线生成10/20颗铺底布局；点全部独立接触底面，不依赖被移除球的支撑。"""
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from danzhu.fx.natural_pile_data import DATA,RADII
from danzhu.fx.pile import halfwidth


def build():
    radius=RADII[100];layouts={};checks={}
    for variant in range(12):
        # 大堆求解允许约0.014px接触容差；单层轻微放松0.02%，避免恢复高度的
        # 浮点误差把这点相交识别成承托链。最大移动约0.05设计像素，不改球径。
        floor=[(round(x*1.0002,5),round(z*1.0002,5),h)
               for x,z,h in DATA[100,variant] if abs(h-radius)<.001]
        selected=[min(floor,key=lambda p:math.hypot(p[0],p[1]))]
        while len(selected)<20:
            selected.append(max((p for p in floor if p not in selected),
                                key=lambda p:min(math.dist(p,q) for q in selected)))
        for count in (10,20):
            points=tuple(selected[:count])
            separation=min(math.dist(p,q) for i,p in enumerate(points) for q in points[i+1:])
            clearance=min(halfwidth(h)-math.hypot(x,z)-radius for x,z,h in points)
            assert separation>=2*radius and clearance>0
            assert all(abs(p[2]-radius)<.001 for p in points)
            layouts[count,variant]=points
            checks[f'{count}:{variant}']=dict(count=count,radius=radius,min_separation=separation,
                                             wall_clearance=clearance,floor_supported=count)
    target=ROOT/'danzhu/fx/floor_pile_data.py'
    target.write_text('"""由tools/bake_floor_piles.py生成；固定球径的独立底面支撑布局。"""\n'
                      +'RADIUS = '+repr(radius)+'\nDATA = {\n'
                      +''.join('    '+repr(key)+': '+repr(points)+',\n' for key,points in layouts.items())+'}\n',encoding='utf-8')
    (ROOT/'tools/art/piles/floor_manifest.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print('Validated',len(layouts),'floor layouts; radius',radius)


if __name__=='__main__':build()
