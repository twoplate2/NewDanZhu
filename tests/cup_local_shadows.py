"""局部遮蔽图集方向、布局匹配、渐入连续性和绘制复用。"""
from pathlib import Path
import random,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kivy.core.window import Window
from kivy.graphics import Fbo,ClearColor,ClearBuffers,Color,Rectangle,Callback
from kivy.graphics.opengl import glDisable,glEnable,GL_BLEND
from PIL import Image
from danzhu.ui import winfx as W
from danzhu.ui import cup_shadows as S
from danzhu.fx.cup_shadow_data import DATA,TILE,CELL,PAD


class LocalShadowTests(unittest.TestCase):
    def scene(self,count=100):
        S.warm(count,0)
        fx=W.WinPileFX(None,size=(520,660));fx._rng=random.Random(42);fx._make_balls(count,100,0)
        fx._tbl_build(*W._glass_textures())
        return fx

    def test_all_layouts_match_geometry(self):
        for n,v in DATA:
            S.warm(n,v);proj=W._pile_projected(n,v)
            entry=S.lookup(n,v,proj);self.assertIsNotNone(entry,(n,v))
            regions,peers,strength=entry
            self.assertEqual(len(regions),n);self.assertEqual(strength,.68)
            self.assertTrue(all(len(p)<=12 and all(0<=j<n and j!=i for j in p) for i,p in enumerate(peers)))
        changed=[dict(p) for p in W._pile_projected(100,0)];changed[0]['sx']+=1
        self.assertIsNone(S.lookup(100,0,changed))

    def test_region_orientation_matches_offline_pixels(self):
        S.warm(100,0);tex,regions,entry,_=S._CACHE[100,0]
        source=Image.open(Path(S._ROOT)/entry['file']).convert('RGBA')
        with tempfile.TemporaryDirectory() as folder:
            for i in (0,24,99):
                fbo=Fbo(size=(TILE,TILE))
                with fbo:
                    ClearColor(0,0,0,0);ClearBuffers();Color(1,1,1,1)
                    Callback(lambda *_:glDisable(GL_BLEND))
                    Rectangle(texture=regions[i],pos=(0,0),size=(TILE,TILE))
                    Callback(lambda *_:glEnable(GL_BLEND))
                fbo.draw();path=Path(folder)/f'{i}.png';fbo.texture.save(str(path))
                actual=Image.open(path).convert('RGBA')
                x=i%entry['columns']*CELL+PAD;y=i//entry['columns']*CELL+PAD
                expected=source.crop((x,y,x+TILE,y+TILE))
                self.assertEqual(actual.tobytes(),expected.tobytes(),i)

    def test_neighbour_presence_and_settle_do_not_jump(self):
        fx=self.scene();ball=next(b for b in fx._balls if b.get('shadow_peers'))
        for b in fx._balls:b['settled']=False;b['tt']=b['f']-W.LIGHT_APPROACH_SEC-1
        self.assertEqual(fx._shadow_alpha(ball),0)
        values=[]
        for offset in (-W.LIGHT_APPROACH_SEC,-.1,-.01,0,.1):
            for b in fx._balls:b['tt']=b['f']+offset
            values.append(fx._shadow_alpha(ball))
        self.assertEqual(values,sorted(values));self.assertAlmostEqual(values[-1],.68)
        before=fx._shadow_alpha(ball)
        for b in fx._balls:b['settled']=True
        self.assertEqual(fx._shadow_alpha(ball),before)
        fx._balls[ball['shadow_peers'][0]]['settled']=False
        fx._balls[ball['shadow_peers'][0]]['tt']=-1
        self.assertEqual(fx._shadow_alpha(ball),0)

    def test_drawing_reuses_instructions_and_no_io(self):
        fx=self.scene();instructions=tuple(fx.canvas.children)
        with patch.object(S,'CoreImage',side_effect=AssertionError('render-time I/O')):
            for t in (.1,1,2,4,8):
                fx._advance_balls(t);fx._tbl_apply_balls(1.)
                self.assertEqual(tuple(fx.canvas.children),instructions)
        for i,entry in enumerate(fx._sd):
            if entry is not None:
                self.assertEqual(entry[1].pos,fx._bd[i][2].pos)
                self.assertEqual(entry[1].size,fx._bd[i][2].size)
        fx._tbl_apply_balls(0)
        self.assertTrue(all(entry is None or entry[0].a==0 for entry in fx._sd))
        fx._tbl_drop();self.assertFalse(fx._sd)

    def test_missing_cache_is_fixed_for_current_round(self):
        saved=S._CACHE.pop((20,0),None)
        try:
            fx=W.WinPileFX(None);fx._rng=random.Random(1);fx._make_balls(20,100,0)
            self.assertFalse(any('shadow_texture' in b for b in fx._balls))
            S.warm(20,0)
            self.assertFalse(any('shadow_texture' in b for b in fx._balls))
        finally:
            if saved is not None:S._CACHE[20,0]=saved

    def test_shadow_is_between_receiver_and_next_ball_before_glass(self):
        fx=self.scene();children=list(fx.canvas.children)
        front=children.index(fx._fx_post[0][2])
        for i,entry in enumerate(fx._sd):
            if entry is None:continue
            shadow=children.index(entry[1]);base=children.index(fx._bd[i][2])
            self.assertLess(base,shadow);self.assertLess(shadow,front)
            if i+1<len(fx._bd):self.assertLess(shadow,children.index(fx._bd[i+1][2]))


if __name__=='__main__':unittest.main()
