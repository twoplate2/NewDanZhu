"""装杯视觉回归：抗锯齿采样、固定光源、回弹、落点与画布复用。"""

from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kivy.core.window import Window  # 建立纹理测试所需 GL 上下文。
from danzhu.ui import winfx as W


def scene(count=2):
    fx = W.WinPileFX(None, size=(520, 660))
    fx._rng = random.Random(1234)
    fx._make_balls(count, 100, 1)
    return fx


class CupVisualTests(unittest.TestCase):
    def test_mipmap_and_texture_reuse(self):
        back, front, fallback = W._glass_textures()
        self.assertIsNone(fallback)
        textures = [back, front, W._glass_over(), W._ball_texture(100)]
        textures.extend(t[0] for t in W._rim_back_strips(back))
        for tex in textures:
            self.assertTrue(tex.mipmap)
            self.assertEqual(tex.min_filter, "linear_mipmap_linear")
        self.assertEqual(W._ball_texture(100).size, (256, 256))
        self.assertIs(W._ball_texture(100), W._ball_texture(100))

    def test_fallback_chunked_bake_matches_full_bake(self):
        full = W._bake_begin(100)
        W._bake_advance(full, 2*full["d"])
        chunked = W._bake_begin(100)
        while chunked["done"] < 2*chunked["d"]:
            W._bake_advance(chunked, 7)
        self.assertEqual(full["buf"], chunked["buf"])

    def test_assets_are_round_transparent_and_skip_runtime_bake(self):
        import colorsys
        from PIL import Image
        from unittest.mock import patch
        for bet in W.BET_COLORS:
            with Image.open(Path(W._ASSET_BALLS)/("cup_%d.png" % bet)) as source:
                image=source.convert("RGBA")
                self.assertEqual(image.size,(256,256))
                alpha=image.getchannel("A")
                self.assertEqual(alpha.getpixel((0,0)),0)
                self.assertGreater(alpha.getpixel((128,128)),240)
                for y in range(16,241,8):
                    for x in range(16,241,8):
                        if (x-127.5)**2+(y-127.5)**2 < 118**2:
                            self.assertEqual(alpha.getpixel((x,y)),255)
                self.assertGreater(sum(0<a<255 for a in alpha.getdata()),200)
                x0,y0,x1,y1=alpha.point(lambda x:255 if x>=128 else 0).getbbox()
                self.assertLess(abs((x1-x0)-(y1-y0)),4)
                self.assertGreater(x1-x0,248)
                r,g,b,_=image.getpixel((128,128))
                if bet==1:self.assertGreater(g,r)
                elif bet==10:self.assertGreater(b,r)
                elif bet==50:self.assertGreater(r,b)
                else:self.assertGreater(min(r,b),g)
                # 仅判断 r>b 会漏过酒红/紫红；必须与历史投注色板的色相一致。
                target=W.hex_rgb(W.BET_COLORS[bet])
                target_hue=colorsys.rgb_to_hsv(*target)[0]
                target_sat=colorsys.rgb_to_hsv(*target)[1]
                center_sat=colorsys.rgb_to_hsv(r/255,g/255,b/255)[1]
                self.assertLess(abs(center_sat-target_sat),.06)
                for xy in ((128,128),(64,70),(179,160)):
                    rgb=image.getpixel(xy)[:3]
                    hue=colorsys.rgb_to_hsv(*(c/255 for c in rgb))[0]
                    delta=abs(hue-target_hue)
                    self.assertLess(min(delta,1-delta),.015)
            W._CUP_BALL_TEX.pop(bet,None)
            with patch.object(W,"_bake_begin",side_effect=AssertionError("runtime bake")):
                self.assertIsNotNone(W._ball_texture(bet))

    def test_glass_fill_seam_softened_without_removing_rim(self):
        from PIL import Image
        with Image.open(Path(W._ASSET_GLASS)/"glass_tumbler_front.png") as a, Image.open(Path(W._ASSET_GLASS)/"glass_tumbler_front_soft.png") as b:
            old,new=a.convert("RGBA"),b.convert("RGBA")
            self.assertEqual(new.size,old.size)
            self.assertLess(new.getpixel((800,816))[3],3)
            self.assertGreater(old.getpixel((800,816))[3],15)
            # 中央杯沿减弱、侧边局部反光保留，不再叠加均匀双描边。
            self.assertGreater(new.getpixel((800,305))[3],5)
            self.assertLess(new.getpixel((800,305))[3],old.getpixel((800,305))[3])
            self.assertGreater(new.getpixel((320,274))[3],new.getpixel((800,305))[3]*2)
            self.assertLessEqual(abs(new.getpixel((240,780))[3]-old.getpixel((240,780))[3]),1)
            self.assertGreater(new.getpixel((800,900))[3],old.getpixel((800,900))[3])
            for xy in ((600,460),(800,460),(1000,460)):
                self.assertEqual(new.getpixel(xy)[3],old.getpixel(xy)[3])

    def test_two_visible_bounces_and_consistent_final_floor(self):
        fx = scene()
        for b in fx._balls:
            self.assertGreaterEqual(b["t1"], .20)
            self.assertLessEqual(b["t1"]+b["t2"], W.REVEAL_DELAY)
            b["settled"] = True
            rest = fx._ball_screen(b)
            b["settled"] = False
            b["tt"] = b["f"] + b["t1"]*.5
            first = fx._ball_screen(b)
            b["tt"] = b["f"] + b["t1"] + b["t2"]*.5
            second = fx._ball_screen(b)
            self.assertGreater(first[1]-rest[1], .55*rest[3])
            self.assertGreater(second[1], rest[1])
            self.assertLess(second[1], first[1])
            self.assertEqual(first[4], 0.0)
            self.assertEqual(second[4], 0.0)
            self.assertEqual(rest[4], 0.0)
            floor_y = fx._map(b["sxf"], W.FLOOR_Y-W.K2*b["z"])[1]
            self.assertAlmostEqual(rest[1]-rest[3], floor_y, delta=.1)

    def test_canvas_instructions_reused_for_small_and_large_scenes(self):
        for count in (2, 100):
            fx = scene(count)
            fx._tbl_build(*W._glass_textures())
            instructions = tuple(fx.canvas.children)
            for t in (.7, 1.1, 1.4, 2.5, 4.0):
                fx._advance_balls(t)
                fx._tbl_apply_balls(1.)
                self.assertEqual(tuple(fx.canvas.children), instructions)
            fx._tbl_drop()

    def test_ball_material_not_dimmed_again_by_depth(self):
        fx = scene()
        fx._tbl_build(*W._glass_textures())
        for b in fx._balls:
            b["settled"] = True
            b["shade"] = .5  # 即使旧投影仍给出深度亮度，也不应用到球材质。
        for alpha in (1.0, .4):
            fx._tbl_apply_balls(alpha)
            for color, _, _, _ in fx._bd:
                self.assertEqual(tuple(color.rgb), (1., 1., 1.))
                self.assertAlmostEqual(color.a, alpha, places=6)



    def test_occlusion_depends_on_arrived_neighbours(self):
        small=scene(2)
        for b in small._balls:b["settled"]=True
        self.assertEqual([small._ball_exposure(b) for b in small._balls],[1.,1.])
        fx=scene(100)
        for b in fx._balls:
            b["settled"]=False
            b["tt"]=b["f"]-W.LIGHT_APPROACH_SEC-.01
        self.assertTrue(all(fx._ball_exposure(b)==1 for b in fx._balls))
        for b in fx._balls:b["settled"]=True
        values=[fx._ball_exposure(b) for b in fx._balls]
        self.assertGreaterEqual(min(values),.68-1e-9)
        self.assertEqual(max(values),1.)
        self.assertGreater(sum(v<.95 for v in values),20)

    def test_lighting_finishes_before_contact_without_settle_jump(self):
        fx=scene(100)
        lower=next(b for b in fx._balls if b["light_links"])
        lower["settled"]=True
        neighbours=[fx._balls[index] for index,_ in lower["light_links"]]
        exposures=[]
        for offset in (-W.LIGHT_APPROACH_SEC, -.3, -.1, 0., .1, .4):
            for b in neighbours:
                b["settled"]=False
                b["tt"]=b["f"]+offset
            exposures.append(fx._ball_exposure(lower))
        self.assertEqual(exposures[0],1.)
        self.assertGreater(exposures[1],exposures[2])
        self.assertGreater(exposures[2],exposures[3])
        self.assertEqual(exposures[3:], [exposures[3]]*3)
        for b in neighbours:b["settled"]=True
        self.assertEqual(fx._ball_exposure(lower),exposures[3])

    def test_upper_ball_arrives_after_its_support_settles(self):
        for count in (2,3,5,10,20,50,100):
            for seed in range(W._PILE_VARIANTS):
                fx=W.WinPileFX(None)
                fx._rng=random.Random(1234+seed)
                fx._make_balls(count,50,seed)
                by={b["pile_index"]:b for b in fx._balls}
                projection=W._pile_projected(count,seed)
                for index,ball in by.items():
                    # 时间安排不改变终态位置与半径。
                    for key,pkey in (("sxf","sx"),("syf","sy"),("r_d","r")):
                        self.assertEqual(ball[key],projection[index][pkey])
                    for lower_index in projection[index].get("sup", ()):
                        lower=by[lower_index]
                        self.assertGreaterEqual(ball["t0"]+ball["f"],lower["end"]+.02-1e-9)
                        self.assertGreaterEqual(ball["t0"],lower["t0"]+.012-1e-9)
                self.assertLess(W.WINDUP+fx._last_settle+W.HOLD_BASE,W.FX_MAX_SEC)

    def test_entries_and_same_layer_touches_are_staggered(self):
        import math
        for count in (10,20,50,100):
            for seed in range(W._PILE_VARIANTS):
                fx=W.WinPileFX(None)
                fx._rng=random.Random(1234+seed)
                fx._make_balls(count,100,seed)
                entries=sorted(b["t0"]+b["f_enter"] for b in fx._balls)
                self.assertTrue(all(b-a>=W.ENTRY_GAP-1e-9 for a,b in zip(entries,entries[1:])))
                layers={}
                for b in fx._balls:
                    h=W.FLOOR_Y-b["syf"]-W.K2*b["z"]
                    layer=round((h-b["r_d"])/(b["r_d"]*math.sqrt(8/3)))
                    layers.setdefault(layer,[]).append(b["t0"]+b["f"])
                for times in layers.values():
                    times.sort()
                    self.assertTrue(all(b-a>=W.LAYER_TOUCH_GAP-1e-9 for a,b in zip(times,times[1:])))

    def test_pile_prewarm_yields_and_finishes_all_variants(self):
        cache,order=dict(W._PILE_CACHE),list(W._PILE_ORDER)
        W._PILE_CACHE.clear();W._PILE_ORDER.clear()
        try:
            fx=W.WinPileFX(None)
            calls=0
            while True:
                calls+=1
                if fx._prebake_piles(budget=0.0):break
                self.assertLess(calls,100)
            self.assertGreater(calls,1)
            self.assertEqual(len(W._PILE_CACHE),7*W._PILE_VARIANTS)
            self.assertEqual(fx._pile_warm_new,7*W._PILE_VARIANTS)
            self.assertTrue(all("light_links" in p for data in W._PILE_CACHE.values()
                                for p in data if not p.get("meta")))
        finally:
            W._PILE_CACHE.clear();W._PILE_CACHE.update(cache)
            W._PILE_ORDER[:]=order


if __name__ == "__main__":
    unittest.main()
