"""批绘与0.9.0矩形路径的真实GPU像素对照及生命周期验证。"""
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kivy.core.window import Window
from kivy.graphics import Fbo, ClearColor, ClearBuffers, PushMatrix, PopMatrix, Rotate
from kivy.graphics.context import get_context
from danzhu.ui import winfx as W
from danzhu.ui.cup_batch import QUAD_FLOATS


class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy = W.WinPileFX(None, size=(520, 660))
        cls.legacy._batch_disabled = True
        cls.batch = W.WinPileFX(None, size=(520, 660))
        cls.batch._prepare_batch()
        assert cls.batch._batch_renderer is not None, cls.batch._batch_error
        cls.texture = W._glass_textures()
        cls.checks = 0
        cls.max_delta = 0

    def scene(self, count, bet=100, variant=0, shadows=True):
        if shadows:
            W.cup_shadows.warm(count, variant)
        for fx in (self.legacy, self.batch):
            fx._rng = random.Random(41)
            fx._make_balls(count, bet, variant)
            if not shadows:
                for b in fx._balls:
                    b.pop('shadow_texture', None)
            fx._tbl_build(*self.texture)
        self.assertTrue(self.batch._batch_active, self.batch._batch_error)

    @staticmethod
    def render(fx, angle=0):
        target = Fbo(size=tuple(map(int, fx.size)))
        with target:
            ClearColor(.071, .093, .12, 1)
            ClearBuffers()
            PushMatrix()
            Rotate(angle=angle, origin=(fx.width/2, fx.height/2))
        target.add(fx.canvas)
        with target:
            PopMatrix()
        target.draw()
        pixels = np.frombuffer(target.pixels, np.uint8).copy()
        target.remove(fx.canvas)
        return pixels

    def compare(self, moment, alpha=1., scale=1., rise=0., angle=0):
        for fx in (self.legacy, self.batch):
            fx._advance_balls(moment)
            fx._apply_anim_rect(scale, rise)
            fx._tbl_apply_fixed(alpha, alpha, fx._abx, fx._aby, fx._abw, fx._abh)
            fx._tbl_apply_balls(alpha)
        old, new = self.render(self.legacy, angle), self.render(self.batch, angle)
        delta = np.abs(old.astype(np.int16)-new.astype(np.int16))
        type(self).checks += 1
        type(self).max_delta = max(type(self).max_delta, int(delta.max()))
        # 顶点以float32存储，边缘插值允许极小舍入；不能接受材质/透明度整体漂移。
        self.assertLess(float(delta.mean()), .01)
        self.assertLess(float(np.mean(delta > 2)), .0001)

    def test_four_colors_counts_and_motion(self):
        for count in (2, 10, 20, 50, 100):
            for bet in (1, 10, 50, 100):
                self.scene(count, bet, variant=3)
                last=max(self.batch._balls,key=lambda b:b['end'])
                moments=(.01, self.batch._last_settle*.45, last['t0']+last['f'],
                         last['t0']+last['f']+last['t1']*.5, self.batch._last_settle+.1)
                for moment in moments:
                    self.compare(moment)
                self.compare(moments[-1], alpha=.43, scale=.97, rise=9)

    def test_native_size_and_parent_transform(self):
        for fx in (self.legacy,self.batch):
            fx.size=(1904,2417)
        try:
            for bet in (1,10,50,100):
                self.scene(100,bet,variant=7)
                for fraction in (.55,1.02):
                    self.compare(self.batch._last_settle*fraction)
            self.compare(self.batch._last_settle+.1, alpha=.29, scale=.95, rise=5, angle=90)
        finally:
            for fx in (self.legacy,self.batch):fx.size=(520,660)

    def test_reuse_one_mesh_static_no_upload_and_clear(self):
        self.scene(100)
        batch=self.batch._batch_renderer
        self.compare(self.batch._last_settle+.1)
        identity=(id(batch.mesh),id(batch.vertices),id(batch.indices))
        uploads=batch.uploads
        for _ in range(20):self.batch._tbl_apply_balls(1.)
        self.assertEqual(batch.uploads,uploads)
        self.assertEqual(identity,(id(batch.mesh),id(batch.vertices),id(batch.indices)))
        self.assertEqual(len(self.batch._bd),0)
        self.assertEqual(len(batch.indices),1200)
        children=list(self.batch.canvas.children)
        self.assertLess(children.index(batch.context),children.index(self.batch._fx_post[0][2]))
        self.assertGreater(children.index(batch.context),children.index(self.batch._fx_pre[-1][2]))
        # 缩小球数不会残留前一局的顶点，仍复用同一Mesh。
        self.scene(2,bet=1)
        self.compare(self.batch._last_settle+.1)
        self.assertEqual(identity,(id(batch.mesh),id(batch.vertices),id(batch.indices)))
        for slot in range(4,200):
            self.assertIsNone(batch.states[slot])
            self.assertEqual(batch.vertices[slot*QUAD_FLOATS+7],0)
        self.batch._tbl_drop()
        self.assertNotIn(batch.context,self.batch.canvas.children)

    def test_no_shadow_cache_and_no_render_time_io(self):
        self.scene(20,shadows=False)
        with patch.object(W.cup_shadows,'CoreImage',side_effect=AssertionError('render I/O')):
            self.compare(self.batch._last_settle*.5)
            self.compare(self.batch._last_settle+.1)

    def test_context_reload_restores_textures_and_mesh(self):
        self.scene(50,bet=10)
        moment=self.batch._last_settle+.1
        self.compare(moment)
        before=self.render(self.batch)
        legacy_before=self.render(self.legacy)
        get_context().reload()
        after=self.render(self.batch)
        legacy_after=self.render(self.legacy)
        # 驱动重载会令旧路径的极少边缘采样产生舍入差；新旧须保持同一变化。
        self.assertTrue(np.array_equal(before.astype(int)-after,legacy_before.astype(int)-legacy_after))
        self.compare(moment,alpha=.4,scale=.98)

    def test_canvas_opacity_and_rotation(self):
        self.scene(20)
        try:
            for fx in (self.legacy,self.batch):fx.opacity=.55
            self.compare(self.batch._last_settle+.1,alpha=.4,angle=180)
        finally:
            for fx in (self.legacy,self.batch):fx.opacity=1.

    def test_shader_failure_falls_back_without_retrying_in_motion(self):
        fx=W.WinPileFX(None)
        with patch('danzhu.ui.cup_batch.CupBatch',side_effect=RuntimeError('test shader failure')) as create:
            fx._prepare_batch();fx._prepare_batch()
            self.assertEqual(create.call_count,1)
            fx._rng=random.Random(0);fx._make_balls(20,100,0)
            fx._tbl_build(*self.texture)
            fx._advance_balls(fx._last_settle+.1);fx._tbl_apply_balls(1.)
            self.assertFalse(fx._batch_active)
            self.assertEqual(len(fx._bd),20)
            self.assertEqual(create.call_count,1)

    @classmethod
    def tearDownClass(cls):
        print('GPU image comparisons:',cls.checks,'max channel delta:',cls.max_delta)


if __name__=='__main__':unittest.main()
