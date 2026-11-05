"""自然堆积验收：几何、独立受力证书复算、回退及落珠时序。纯 stdlib + Kivy。"""
import json
import math
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from danzhu.fx.pile import halfwidth,RIM_H
from danzhu.fx.natural_pile_data import DATA,RADII
from danzhu.ui import winfx as W


class NaturalPileTests(unittest.TestCase):
    def test_geometry_and_nonplanar_layout(self):
        self.assertEqual(set(DATA),{(n,v) for n in (50,100) for v in range(12)})
        for (n,v),points in DATA.items():
            r=RADII[n]
            self.assertEqual(r,W._PILE_BAKED_R[n]);self.assertEqual(len(points),n)
            for i,(x,z,h) in enumerate(points):
                self.assertGreaterEqual(h,r-.001)
                self.assertLessEqual(h,RIM_H+4)
                self.assertLessEqual(math.hypot(x,z),halfwidth(h)-1.04*r+.1)
                for q in points[i+1:]:
                    self.assertGreaterEqual(math.dist((x,z,h),q),2*r-.1)
            self.assertGreater(len({round(h,2) for _,_,h in points if h>r+.5}),n//3)

    def test_force_certificates_balance_every_ball(self):
        audit=json.loads((ROOT/'tools/art/piles/natural_manifest.json').read_text(encoding='utf-8'))
        for (n,v),points in DATA.items():
            result=[[0.,0.,0.] for _ in points];r=RADII[n]
            certificate=audit['variants'][f'{n}:{v}']['force_certificate']
            self.assertTrue(certificate)
            for i,j,force in certificate:
                self.assertGreater(force,0)
                p=points[i]
                if j>=0:
                    distance=math.dist(p,points[j])
                    self.assertLessEqual(distance,2*r+.21)
                    normal=[(p[k]-points[j][k])/distance for k in range(3)]
                    for k in range(3):result[j][k]-=force*normal[k]
                elif j==-1:
                    self.assertAlmostEqual(p[2],r,delta=.11)
                    normal=[0,0,1]
                else:
                    self.assertEqual(j,-2)
                    radial=math.hypot(p[0],p[1])
                    self.assertAlmostEqual(radial,halfwidth(p[2])-1.04*r,delta=.22)
                    t=(364-math.sqrt(132496-160*(324-p[2])))/80
                    slope=(52+38*t)/(364-80*t)
                    normal=[-p[0]/radial,-p[1]/radial,slope]
                for k in range(3):result[i][k]+=force*normal[k]
            for force in result:
                for actual,expected in zip(force,(0,0,1)):
                    self.assertAlmostEqual(actual,expected,delta=1e-5)

    def test_bad_new_table_falls_back_without_losing_balls(self):
        broken=list(DATA[50,1]);broken[0]=(math.inf,0,50.233)
        for value in (tuple(broken),tuple(broken[:20])):
            with patch.dict(DATA,{(50,1):value}):
                balls=W._beads_from_baked(50,1)
                self.assertEqual(len(balls),50)
                self.assertTrue(all(math.isfinite(b[k]) for b in balls for k in ('x','z','h')))
                self.assertTrue(all(not b.get('natural') for b in balls))

    def test_all_variants_and_random_orders_meet_timing_constraints(self):
        longest=0;cases=0
        for count in (50,100):
            for variant in range(12):
                proj=W._pile_projected(count,variant)
                heights=[W.FLOOR_Y-p['sy']-W.K2*p['z'] for p in proj]
                # 侧向与承托拆分不能漏掉原有的下方阻挡依赖。
                for i,p in enumerate(proj):
                    expected={j for j,q in enumerate(proj) if heights[j]<heights[i]
                              and (p['sx']-q['sx'])**2+(p['z']-q['z'])**2<(2*p['r'])**2}
                    self.assertEqual(set(p['sup'])|set(p['side']),expected)
                for seed in range(16):
                    fx=W.WinPileFX(None);fx._rng=random.Random(seed*7919+1234)
                    fx._make_balls(count,100,variant)
                    by={b['pile_index']:b for b in fx._balls}
                    for i,b in by.items():
                        for j in proj[i]['sup']:
                            self.assertGreaterEqual(b['t0']+b['f'],by[j]['end']+.02-1e-8)
                        for j in proj[i]['side']:
                            self.assertGreaterEqual(b['t0']+b['f'],by[j]['t0']+by[j]['f']+.02-1e-8)
                    entries=sorted(b['t0']+b['f_enter'] for b in fx._balls)
                    self.assertTrue(all(y-x>=W.ENTRY_GAP-1e-8 for x,y in zip(entries,entries[1:])))
                    longest=max(longest,fx._last_settle);cases+=1
                    self.assertLess(W.WINDUP+fx._last_settle+W.HOLD_BASE,W.FX_MAX_SEC,
                                    (count,variant,seed,fx._last_settle))
        print('NATURAL_TIMING',json.dumps({'cases':cases,'longest_settle_s':longest}))


if __name__=='__main__':unittest.main()
