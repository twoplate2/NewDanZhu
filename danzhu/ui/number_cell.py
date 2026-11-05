"""统计用静态标题 + 数字字形图集；复用现有烘字与字符拼接实现。"""
from collections import OrderedDict
from kivy.graphics import Color, Rectangle
from kivy.metrics import sp
from kivy.properties import NumericProperty, StringProperty, ListProperty, BooleanProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ..config import COL_BALL, hex_rgb
from .text import _glyph_bake, _glyph_quads, text_px

_NUMBER_ATLASES=OrderedDict()


class NumberCell(Widget):
    text=StringProperty('')
    font_size=NumericProperty(sp(15))
    texture_size=ListProperty([0,0])
    text_size=ListProperty([None,None])
    bold=BooleanProperty(False)
    scale_to_fit=BooleanProperty(False)

    def __init__(self, caption='', chars='0123456789.', centered=False, color=None, capacity=12, **kw):
        self.caption=caption
        self.chars=chars
        self.centered=centered
        self._base_rgba=tuple(color or (*hex_rgb(COL_BALL),1))
        self._rec=None
        self._caption_width=0
        self._fallback=False
        super().__init__(**kw)
        self._label=Label(text=caption,halign='left',valign='middle',size_hint=(None,None))
        self._label._playtest_probe_tag='面板静态字'
        self.add_widget(self._label)
        with self.canvas:
            self._color=Color(*self._base_rgba)
            self._rects=[Rectangle(size=(0,0)) for _ in range(capacity)]
        self.bind(text=self._sync,font_size=self._font,bold=self._font,pos=self._place,size=self._place)
        self._font()

    def _font(self,*_):
        key=(float(self.font_size),self.bold,self.chars)
        if key not in _NUMBER_ATLASES:
            rec=_glyph_bake(key[0],self.bold,chars=self.chars)
            # 个别小字号的 SDL advance 舍入相差1px；用高分辨率图集缩放，避免静默退回整行光栅化。
            if rec is None:
                for scale in (2.,4.):
                    source=_glyph_bake(key[0]*scale,self.bold,chars=self.chars)
                    if source is not None:
                        rec=dict(source,h=source['h']/scale,
                                 adv={c:w/scale for c,w in source['adv'].items()},render_scale=scale)
                        break
            _NUMBER_ATLASES[key]=rec
            while len(_NUMBER_ATLASES)>4:_NUMBER_ATLASES.popitem(last=False)
        else:_NUMBER_ATLASES.move_to_end(key)
        self._rec=_NUMBER_ATLASES[key]
        self._fallback=self._rec is None
        self._label.font_size=self.font_size
        self._label.bold=self.bold
        self._caption_width=text_px(self.caption,self.font_size,self.bold) if self.caption else 0
        self._sync()

    def _sync(self,*_):
        numeric=len(self.text)<=len(self._rects) and all(c in self.chars for c in self.text)
        self._quads=_glyph_quads(self._rec,self.text) if numeric and self._rec is not None else None
        self._label.text=self.caption if self._quads is not None else self.caption+self.text
        if self._label.text and self._label.texture is None:self._label.texture_update()
        height=self._rec['h'] if self._rec is not None else self._label.texture_size[1]
        width=self._caption_width+(self._quads[1] if self._quads is not None else text_px(self.text,self.font_size,self.bold))
        self.texture_size=[width,height]
        self._place()

    def _place(self,*_):
        self._label.pos=self.pos
        self._label.halign='center' if self.centered else 'left'
        self._label.size=(self._caption_width if self._quads is not None else self.width,self.height)
        self._label.text_size=(self._label.width,None)
        quads=self._quads[0] if self._quads is not None else ()
        height=self._rec['h'] if self._rec is not None else 0
        numeric_width=self._quads[1] if self._quads is not None else 0
        scale=min(1.,max(0.,self.width-self._caption_width)/numeric_width) if self.scale_to_fit and numeric_width else 1.
        for index,rect in enumerate(self._rects):
            if index<len(quads):
                texture,x,width=quads[index]
                displayed_width=self._caption_width+numeric_width*scale
                left=self.x+(self.width-displayed_width)/2 if self.centered else self.x
                rect.texture=texture;rect.pos=(int(left+self._caption_width+x*scale),int(self.y+(self.height-height*scale)/2))
                rect.size=(width*scale,height*scale)
            else:rect.size=(0,0)

    def texture_update(self,*_):
        # 兼容布局检查；数字的实际更新只修改现有 Rectangle。
        self._sync()

    def set_atlas_tint(self,rgba):
        self._color.rgba=tuple(a*b for a,b in zip(self._base_rgba,rgba))
        if self._quads is None:self._label.color=self._color.rgba

    def atlas_width(self,fs):
        rec=self._rec if float(fs)==float(self.font_size) else _NUMBER_ATLASES.get((float(fs),self.bold,self.chars))
        if rec is None or any(ch not in rec['adv'] for ch in self.text):return None
        return sum(rec['adv'][ch] for ch in self.text)
