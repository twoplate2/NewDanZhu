"""按原画家序一次提交球/阴影矩形；保留两层混合，不重新计算材质。

每球固定两个四边形：球、该球的局部阴影。两个纹理单元固定绑定，
顶点中的kind选择采样器。隐藏项退化为零面积，整局不增删指令。
"""
from array import array
import os

from kivy import kivy_shader_dir
from kivy.core.image import Image
from kivy.graphics import BindTexture, Fbo, Mesh, RenderContext

MAX_BALLS = 100
STRIDE = 9  # xy, uv, rgba, kind
QUAD_FLOATS = 4 * STRIDE
VS = '''$HEADER$
attribute vec4 vColor;
attribute float vKind;
varying float texture_kind;
void main(void) {
    frag_color = vColor * vec4(1.0, 1.0, 1.0, opacity);
    texture_kind = vKind;
    tex_coord0 = vTexCoords0;
    gl_Position = projection_mat * modelview_mat * vec4(vPosition, 0.0, 1.0);
}
'''
FS = '''$HEADER$
uniform sampler2D texture1;
varying float texture_kind;
void main(void) {
    if (texture_kind < 0.5)
        gl_FragColor = frag_color * texture2D(texture0, tex_coord0);
    else
        gl_FragColor = frag_color * texture2D(texture1, tex_coord0);
}
'''
FMT = [(b'vPosition', 2, 'float'), (b'vTexCoords0', 2, 'float'),
       (b'vColor', 4, 'float'), (b'vKind', 1, 'float')]


class CupBatch:
    def __init__(self):
        self.context = RenderContext(vs=VS, fs=FS, use_parent_projection=True,
                                     use_parent_modelview=True)
        if not self.context.shader.success:
            raise RuntimeError('cup batch shader did not compile')
        self.context['texture1'] = 1
        placeholder = Image(os.path.join(kivy_shader_dir, 'default.png')).texture
        self.vertices = array('f', [0.]) * (MAX_BALLS * 2 * QUAD_FLOATS)
        self.indices = array('H')
        for slot in range(MAX_BALLS * 2):
            v = slot * 4
            self.indices.extend((v, v+1, v+2, v+2, v+3, v))
        with self.context:
            self.shadow_binding = BindTexture(texture=placeholder, index=1)
            self.mesh = Mesh(fmt=FMT, mode='triangles', vertices=self.vertices,
                             indices=self.indices, texture=placeholder)
        self.states = [None] * (MAX_BALLS * 2)
        self.count = 0
        self.dirty = False
        self.uploads = 0
        # 在启动空闲阶段真正执行一次，提前创建VBO并验证着色器使用路径。
        self.configure([{'value': 0, 'shadow_texture': placeholder}], placeholder)
        self.quad(0, (0., 0.), (1., 1.), (1., 1., 1., 1.))
        self.quad(1, (0., 0.), (1., 1.), (1., 1., 1., .5))
        self.flush()
        target = Fbo(size=(1, 1))
        target.add(self.context)
        try:
            target.draw()
        finally:
            target.remove(self.context)
        self.clear()
        self.flush()

    def configure(self, balls, texture):
        if not 0 < len(balls) <= MAX_BALLS:
            return False
        if any(b['value'] != balls[0]['value'] for b in balls):
            return False
        shadow = next((b.get('shadow_texture') for b in balls
                       if b.get('shadow_texture') is not None), None)
        # TextureRegion与所属图集共用GL纹理id；UV已是图集绝对坐标。
        atlas = shadow if shadow is not None else texture
        if any(b.get('shadow_texture') is not None and b['shadow_texture'].id != atlas.id
               for b in balls):
            return False
        self.clear()
        self.count = len(balls)
        self.mesh.texture = texture
        self.shadow_binding.texture = atlas
        for i, ball in enumerate(balls):
            for kind, tex in ((0, texture), (1, ball.get('shadow_texture'))):
                if tex is None:
                    continue
                uv = tex.tex_coords
                for corner in range(4):
                    offset = (i*2+kind)*QUAD_FLOATS + corner*STRIDE
                    self.vertices[offset+2] = uv[corner*2]
                    self.vertices[offset+3] = uv[corner*2+1]
                    self.vertices[offset+8] = kind
        self.dirty = True
        return True

    def quad(self, slot, pos, size, rgba):
        state = (pos, size, rgba)
        if self.states[slot] == state:
            return
        self.states[slot] = state
        x, y = pos
        w, h = size
        vertices = self.vertices
        for corner, (px, py) in enumerate(((x,y), (x+w,y), (x+w,y+h), (x,y+h))):
            offset = slot*QUAD_FLOATS + corner*STRIDE
            vertices[offset] = px
            vertices[offset+1] = py
            vertices[offset+4] = rgba[0]
            vertices[offset+5] = rgba[1]
            vertices[offset+6] = rgba[2]
            vertices[offset+7] = rgba[3]
        self.dirty = True

    def hide(self, slot):
        if self.states[slot] is not None:
            self.quad(slot, (0., 0.), (0., 0.), (0., 0., 0., 0.))
            self.states[slot] = None

    def clear(self):
        for slot in range(self.count*2):
            self.hide(slot)
        self.count = 0

    def flush(self):
        if self.dirty:
            # 同一块连续float数组复用；只在内容变化后通知Mesh上传。
            self.mesh.vertices = self.vertices
            self.dirty = False
            self.uploads += 1
