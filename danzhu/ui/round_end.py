"""复用的轮次结束窗口：静态内容预热，次数/余额仅更新字形位置。"""
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ..config import COL_BALL, COL_BTN, COL_DIV, COL_TEXT, hex_rgb
from .number_cell import NumberCell
from .text import text_px


class NumberSentence(Widget):
    def __init__(self, prefix, suffix, value, number_color, **kw):
        super().__init__(size_hint_y=None, height=dp(28), **kw)
        self.prefix=Label(text=prefix,font_size=sp(18),color=hex_rgb(COL_TEXT)+(1,),size_hint=(None,None))
        self.number=NumberCell(text=str(value),font_size=sp(18),color=number_color,size_hint=(None,None),capacity=32,scale_to_fit=True)
        self.suffix=Label(text=suffix,font_size=sp(18),color=hex_rgb(COL_TEXT)+(1,),size_hint=(None,None))
        for child in (self.prefix,self.number,self.suffix):
            self.add_widget(child)
            child.bind(texture_size=self._arrange)
        self.bind(size=self._arrange,pos=self._arrange)
        self._arrange()

    def _arrange(self,*_):
        children=(self.prefix,self.number,self.suffix)
        widths=[c.texture_size[0] for c in children]
        widths[1]=min(widths[1],max(1,self.width-widths[0]-widths[2]))
        height=max(c.texture_size[1] for c in children)
        self.height=height+dp(4)
        x=self.x+(self.width-sum(widths))/2
        for child,width in zip(children,widths):
            child.size=(width,height)
            child.pos=(x,self.y+(self.height-height)/2)
            x+=width

    def set_font(self, size):
        for child in (self.prefix,self.number,self.suffix):child.font_size=size

    def set_value(self,value):
        self.number.text=str(int(value))


class RoundEndView:
    def __init__(self, host):
        self.host=host
        self.disposed=False
        self.content=BoxLayout(orientation='vertical',padding=dp(20),spacing=dp(10))
        self.plays=NumberSentence('本轮游戏 ',' 次已结束',50,hex_rgb(COL_TEXT)+(1,))
        self.balance=NumberSentence('剩余 ',' 个弹珠',1000,hex_rgb(COL_BALL)+(1,))
        self.footer=Label(text='弹珠数量已调整到1000个\n欢迎你再次挑战',font_size=sp(18),
                          color=hex_rgb(COL_TEXT)+(1,),halign='center',valign='middle',size_hint_y=None)
        self.footer.bind(texture_size=lambda w,v:setattr(w,'height',v[1]+dp(8)))
        self.button=Button(text='确定',font_size=sp(16),bold=True,background_normal='',background_down='',
                           background_color=hex_rgb(COL_BTN)+(1,),size_hint_y=None,height=dp(48))
        for widget in (self.plays,self.balance,self.footer,self.button):self.content.add_widget(widget)
        self.popup=host._popup(.82,320,title='本轮游戏结束',content=self.content,auto_dismiss=False,
                              title_color=hex_rgb(COL_TEXT)+(1,),title_size=sp(19),
                              separator_color=hex_rgb(COL_DIV)+(1,))
        self.button.bind(on_release=lambda *_:host._act(host.game.round_end_confirm))
        self._layout_event=Clock.create_trigger(self._finish_layout,0)
        self._resize_event=Clock.create_trigger(self.resize,0)
        self._warm_event=None
        self._warm_labels=[]
        self._layout_key=None
        self.content.bind(minimum_height=self._request_layout)
        Window.bind(size=self._request_resize)
        self.resize()

    def _request_layout(self,*_):
        if not self.disposed:self._layout_event()

    def _request_resize(self,*_):
        # Window.size派发时width/height别名可能仍是上一拍，等属性更新完成再计算。
        if not self.disposed:
            if getattr(self.host,'_idle_gc',None) is not None:self.host._idle_gc.quiet_since=None
            self._resize_event()

    def resize(self,*_):
        if self.disposed:return
        vw,vh=self.host._veq()
        key=(vw,vh,dp(1),sp(1))
        if key==self._layout_key:return
        self._layout_key=key
        self.popup.width=.82*vw
        container=self.content.parent
        grid=container.parent if container is not None else None
        padding=getattr(grid,'padding',(dp(12),dp(12),dp(12),dp(12)))
        available=max(dp(80),self.popup.width-padding[0]-padding[2]-dp(40))
        font=sp(18)*min(1.,vh/dp(480),available/max(1,text_px('本轮游戏 100 次已结束',sp(18))))
        self.plays.set_font(font);self.balance.set_font(font)
        self.footer.font_size=font
        self.footer.text_size=(available,None)
        self._layout_event()
        self.warm()

    def _finish_layout(self,*_):
        if self.disposed:return
        container=self.content.parent
        grid=container.parent if container is not None else None
        chrome=dp(68)
        if grid is not None and hasattr(grid,'padding'):
            pad=grid.padding
            chrome=pad[1]+pad[3]+sum(c.height for c in grid.children if c is not container)
        self.popup.height=min(self.host._veq()[1]*.92,self.content.minimum_height+chrome)

    def warm(self):
        if self.disposed:return
        if self._warm_event is not None:self._warm_event.cancel()
        # 不开窗、不碰模态/输入状态；纹理上传在启动或窗口尺寸改变时分帧完成。
        self._warm_labels=[w for w in self.popup.walk() if isinstance(w,Label) and w.text]
        self._warm_event=Clock.schedule_once(self._warm_one,.05)

    def _warm_one(self,_dt):
        self._warm_event=None
        if self.disposed or not self._warm_labels:return
        label=self._warm_labels.pop(0)
        label.texture_update()
        if label.texture is not None:label.texture.bind()
        if self._warm_labels:self._warm_event=Clock.schedule_once(self._warm_one,0)

    def show(self,data):
        self.resize()
        self.plays.set_value(data['plays'])
        self.balance.set_value(data['balance'])
        self.popup.open()
        return self.popup

    def dispose(self):
        if self.disposed:return
        self.disposed=True
        Window.unbind(size=self._request_resize)
        self._layout_event.cancel()
        self._resize_event.cancel()
        if self._warm_event is not None:self._warm_event.cancel()
        self.popup.dismiss(animation=False)
