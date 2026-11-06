"""小档铺底：独立支撑、固定半径、变体、回退及真实落珠时间线。"""
from pathlib import Path
import math
import random
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.fx.floor_pile_data import DATA,RADIUS
from danzhu.fx.pile import halfwidth
from danzhu.ui import winfx as W


class FloorPileTests(unittest.TestCase):
    def test_floor_support_geometry_and_variants(self):
        self.assertEqual(set(DATA),{(n,v) for n in (10,20) for v in range(12)})
        for (n,v),points in DATA.items():
            self.assertEqual(len(points),n)
            self.assertEqual(len(set(points)),n)
            for i,(x,z,h) in enumerate(points):
                self.assertEqual(h,RADIUS)  # 每颗球单独受到底面支撑，不依赖其它球。
                self.assertLess(math.hypot(x,z)+RADIUS,halfwidth(h))
                for q in points[i+1:]:self.assertGreaterEqual(math.dist((x,z,h),q),2*RADIUS)
            self.assertGreater(max(x for x,z,h in points)-min(x for x,z,h in points),7*RADIUS)
            self.assertGreater(max(z for x,z,h in points)-min(z for x,z,h in points),7*RADIUS)
            beads=W._beads_from_baked(n,v)
            self.assertEqual({b['r'] for b in beads},{RADIUS})
            self.assertEqual([(b['x'],b['z'],b['h']) for b in beads],list(points))
            proj=W._pile_projected(n,v)
            self.assertTrue(all(not p.get('sup') and not p.get('side') for p in proj))
        for v in range(12):self.assertEqual(DATA[10,v],DATA[20,v][:10])
        for n in (10,20):self.assertEqual(len(set(DATA[n,v] for v in range(12))),12)

    def test_invalid_table_keeps_safe_legacy_radius(self):
        with patch.dict(DATA,{(20,0):((float('nan'),0,RADIUS),)*20}):
            beads=W._beads_from_baked(20,0)
        self.assertEqual(len(beads),20)
        self.assertEqual({b['r'] for b in beads},{W._PILE_BAKED_R[20]})
        self.assertTrue(all(math.isfinite(b[k]) for b in beads for k in ('x','z','h')))

    def test_entry_spacing_and_deadlines_for_all_small_layouts(self):
        longest=0
        for n in (10,20):
            for v in range(12):
                for seed in range(16):
                    fx=W.WinPileFX(None);fx._rng=random.Random(seed)
                    fx._make_balls(n,100,v)
                    self.assertEqual(len(fx._balls),n)
                    entries=sorted(b['t0']+b['f_enter'] for b in fx._balls)
                    self.assertTrue(all(b-a>=W.ENTRY_GAP-1e-8 for a,b in zip(entries,entries[1:])))
                    self.assertLess(W.WINDUP+fx._last_settle+W.HOLD_BASE,W.FX_MAX_SEC)
                    longest=max(longest,fx._last_settle)
        print('FLOOR_TIMING 384 cases longest',longest)


if __name__=='__main__':unittest.main()
