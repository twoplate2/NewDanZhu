"""用户选定的06-02彩瓷球：与HTML选型的256px RGB/alpha算法一致。

正常运行直接加载离线PNG。本模块供离线生成与缺失素材时分块兜底，不能逐帧调用。
"""
import math

STYLE = '06-02'
SIZE = 256


def pigment(color):
    """历史投注色 -> 选型页的1.08饱和度颜料；输入为#RRGGBB。"""
    rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    luma = sum(c*w for c,w in zip(rgb, (.2126, .7152, .0722)))
    return tuple(max(0., min(255., luma+(c-luma)*1.08)) for c in rgb)


def render_rows(base, start, end, size=SIZE, bottom_up=False):
    """生成[start,end)行；PNG为顶向下，Kivy blit缓冲为底向上。"""
    if not 0 <= start <= end <= size:
        raise ValueError('invalid row range')
    out = bytearray((end-start)*size*4)
    radius = size/2-1
    tint = tuple(255*.65+c*.35 for c in base)
    for row in range(start, end):
        y = size-1-row if bottom_up else row
        ny = (y+.5-size/2)/radius
        for x in range(size):
            nx = (x+.5-size/2)/radius
            rr = nx*nx+ny*ny
            if rr >= 1.008:
                continue
            nz = math.sqrt(max(0., 1-rr))
            nd = -nx*.43-ny*.54+nz*.724
            diffuse = max(0., (nd+.04)/1.04)
            reflection = max(0., -nx*.27-ny*.34+nz*.90)**22*.19
            fill = max(0., nx*.15+ny*.60+nz*.45)*.05
            edge = 1-.035*(1-nz)**2
            i = ((row-start)*size+x)*4
            for k in range(3):
                value = base[k]*(.29+.82*diffuse+fill)*edge+tint[k]*reflection
                out[i+k] = round(max(0., min(255., value)))
            out[i+3] = round(max(0., min(255., (1-math.sqrt(rr))*128*255)))
    return bytes(out)
