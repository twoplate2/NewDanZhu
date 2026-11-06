"""I方案球面：512px预烘，玻璃/尺寸不参与；缺图分块兜底共用此实现。"""
import math

SIZE = 512
STYLE = 'I_pure_palette'
FIT = (151.02585878858142,48.79968670781391,185.08592279953984,
       .3843241004422038,.6658372946588886,-.5771526968752386,-.7897676582919446,
       -.27909784902370754,-.3681171124349268,48.87672002557607,
       .2990645942174523,.04948918025876803,.08491338394998486)
# 只用于落杯球，不改HUD、倍率槽和投注按钮的配色。
CUP_PALETTE = {'#39c98a':'#2ac453', '#4da8ff':'#3d6fec', '#e0533b':'#dc3742'}


def pigment(color):
    color=color.lower()
    if color=='#a335ee':return FIT[:3]
    color=CUP_PALETTE.get(color,color)
    return tuple(int(color[i:i+2],16) for i in (1,3,5))


def render_rows(base,start,end,size=SIZE,bottom_up=False):
    if not 0<=start<=end<=size:raise ValueError('invalid row range')
    out=bytearray((end-start)*size*4)
    radius=size*127/256
    norm=math.sqrt(FIT[5]**2+FIT[6]**2+1)
    hz=math.sqrt(1-FIT[7]**2-FIT[8]**2)
    for row in range(start,end):
        y=size-1-row if bottom_up else row
        ny=(y+.5-size/2)/radius
        for x in range(size):
            nx=(x+.5-size/2)/radius;rr=nx*nx+ny*ny
            if rr>=1.008:continue
            nz=math.sqrt(max(0.,1-rr))
            nd=(nx*FIT[5]+ny*FIT[6]+nz)/norm
            diffuse=.5*(nd+math.sqrt(nd*nd+.018))
            q=max(0.,nx*FIT[7]+ny*FIT[8]+nz*hz)
            reflection=FIT[10]*q**FIT[9]+FIT[11]*q**14
            body=FIT[3]+FIT[4]*diffuse+FIT[12]*nz
            i=((row-start)*size+x)*4
            for k,light in enumerate((252,230,248)):
                out[i+k]=round(max(0.,min(255.,base[k]*body*(1-reflection)+light*reflection)))
            out[i+3]=round(max(0.,min(255.,(1-math.sqrt(rr))*128*255)))
    return bytes(out)
