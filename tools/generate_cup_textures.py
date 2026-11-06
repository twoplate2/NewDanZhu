"""规范化球材质，柔化杯壁反光，并把杯沿与杯底厚度烘入现有玻璃层。

python tools/generate_cup_textures.py
源图和提示词见 tools/art/cup_materials/manifest.json；只输出游戏使用的固定尺寸素材。
"""
from pathlib import Path
import json
import sys
import statistics
import argparse
import os
from PIL import Image
from cup_glass_edges import GLASS_EDGES
from cup_satin_material import fragment as satin_fragment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from danzhu.fx.porcelain import pigment, render_rows
from danzhu.fx import porcelain_i
os.environ.setdefault("KIVY_NO_ARGS", "1")
from kivy.core.window import Window
from kivy.core.image import Image as CoreImage
from kivy.graphics import Fbo, ClearColor, ClearBuffers, Rectangle, Callback
from kivy.graphics.opengl import glDisable, glEnable, GL_BLEND
from kivy.graphics.texture import Texture

HIGHLIGHT_COMPRESSION = 1.5


def ball_fragment(base_color, compression=HIGHLIGHT_COMPRESSION, midtone_lift=0.0, target_color=None, body_fill=0.0):
    """离线校准反光、中间调与历史配色，保留球面光照形状及圆形 alpha。"""
    palette=""
    if target_color is not None:
        peak=max(target_color)
        normalized=tuple(c/peak for c in target_color)
        palette="""float v = max(max(pixel.r,pixel.g),pixel.b);
    // 只补正面受光的主体色；边缘与背光区不做全局曝光抬升。
    vec2 p = (gl_FragCoord.xy-vec2(128.0))/127.0;
    float nz = sqrt(max(0.0,1.0-dot(p,p)));
    float diffuse = max(0.0,dot(vec3(p,nz),normalize(vec3(-0.4,0.5,0.8))));
    float bodyTarget = %f * (0.22+0.70*diffuse);
    v += %f * nz*nz * max(0.0,bodyTarget-v);
    // 弱白反光收敛，保留原高光位置、形状与柔和边缘。
    float reflection = white*white/(white+0.10);
    pixel.rgb = v * mix(vec3(%f,%f,%f),vec3(1.0),reflection);""" % (peak,body_fill,*normalized)
    return """$HEADER$
void main(void) {
    vec4 pixel = texture2D(texture0,tex_coord0);
    vec3 base = vec3(%f,%f,%f);
    base /= max(max(base.r,base.g),base.b);
    float peak = max(max(pixel.r,pixel.g),pixel.b);
    float minimum = min(min(pixel.r,pixel.g),pixel.b)/max(peak,0.001);
    float baseMinimum = min(min(base.r,base.g),base.b);
    float white = clamp((minimum-baseMinimum)/max(0.05,1.0-baseMinimum),0.0,1.0);
    float excess = max(0.0,white-0.18);
    float removed = excess-excess/(1.0+%f);
    pixel.rgb = max(vec3(0.0),pixel.rgb-removed*peak*(vec3(1.0)-base));
    float body = max(max(pixel.r,pixel.g),pixel.b);
    pixel.rgb *= 1.0 + %f * (1.0-smoothstep(0.45,0.92,body));
    %s
    pixel.a = clamp(127.0-length(gl_FragCoord.xy-vec2(128.0)),0.0,1.0);
    gl_FragColor = frag_color*pixel;
}
""" % (*base_color,compression,midtone_lift,palette)


def render(texture, size, pos, draw_size, path, fragment=None):
    fbo = Fbo(size=size)
    if fragment is not None:
        fbo.shader.fs = fragment
        if not fbo.shader.success:
            raise RuntimeError("asset shader failed")
    with fbo:
        ClearColor(0,0,0,0)
        ClearBuffers()
        Callback(lambda *_: glDisable(GL_BLEND))
        Rectangle(texture=texture,pos=pos,size=draw_size)
        Callback(lambda *_: glEnable(GL_BLEND))
    fbo.draw()
    fbo.texture.save(str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT/"tools/art/cup_materials/manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT/"assets/balls")
    parser.add_argument("--balls-only", action="store_true")
    parser.add_argument("--glass-output", type=Path, default=ROOT/"assets/glass")
    parser.add_argument("--midtone-lift", type=float, default=None)
    parser.add_argument("--body-fill", type=float, default=None)
    parser.add_argument("--material-model", choices=("source","satin","porcelain_06_02","porcelain_I"),default=None)
    parser.add_argument("--specular",type=float,default=None)
    args = parser.parse_args()
    source_dir = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    preparation = manifest.get("sprite_preparation", {})
    material=args.material_model or preparation.get("material_model","source")
    if material=="source":
        preparation={**preparation,**manifest.get("legacy_source_preparation",{})}
    specular=preparation.get("specular",.18) if args.specular is None else args.specular
    if not 0<=specular<=.3:raise ValueError("specular outside 0..0.3")
    inset = preparation.get("source_rim_inset_per_side", .015)
    compression = preparation.get("highlight_compression", HIGHLIGHT_COMPRESSION)
    lift = preparation.get("midtone_lift", 0.0) if args.midtone_lift is None else args.midtone_lift
    body_fill = preparation.get("body_fill",0.0) if args.body_fill is None else args.body_fill
    if not 0.0 <= body_fill <= 1.0:
        raise ValueError("body fill must be between 0 and 1")
    if not 0.0 <= lift <= .25:
        raise ValueError("midtone lift must be between 0 and .25")
    target = args.output.resolve()
    target.mkdir(parents=True,exist_ok=True)
    palette=manifest.get("palette_reference",{}).get("colors",{})
    for bet,name in manifest["sources"].items():
        color=palette.get(bet)
        target_color=tuple(int(color[i:i+2],16)/255.0 for i in (1,3,5)) if color else None
        if material=="porcelain_I":
            if not color:raise ValueError("porcelain I material needs a palette color")
            path=target/("cup_%s.png" % bet)
            size=porcelain_i.SIZE
            Image.frombytes('RGBA',(size,size),porcelain_i.render_rows(porcelain_i.pigment(color),0,size)).save(path)
            print(path,path.stat().st_size,"bytes")
            continue
        if material=="porcelain_06_02":
            if not color:raise ValueError("porcelain material needs a palette color")
            path=target/("cup_%s.png" % bet)
            Image.frombytes('RGBA',(256,256),render_rows(pigment(color),0,256)).save(path)
            print(path,path.stat().st_size,"bytes")
            continue
        if material=="satin":
            if target_color is None:raise ValueError("satin material needs a palette color")
            blank=Texture.create(size=(1,1),colorfmt="rgba")
            blank.blit_buffer(bytes((255,255,255,255)),colorfmt="rgba",bufferfmt="ubyte")
            path=target/("cup_%s.png" % bet)
            render(blank,(256,256),(0,0),(256,256),path,satin_fragment(target_color,specular))
            print(path,path.stat().st_size,"bytes")
            continue
        source = source_dir/name
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            w,h = image.size
            xs = [x for x in range(w) if alpha.getpixel((x,h//2))>=128]
            ys = [y for y in range(h) if alpha.getpixel((w//2,y))>=128]
        x0,x1,y0,y1 = min(xs),max(xs)+1,min(ys),max(ys)+1
        if not .95 < (x1-x0)/(y1-y0) < 1.05:
            raise ValueError("source sphere silhouette is not round")
        cx,cy=(x0+x1)//2,(y0+y1)//2
        # 中心小块不含主反射斑，取中位数避免源图细纹影响底色估计。
        base_color=tuple(statistics.median(rgba.getpixel((x,y))[channel]
                          for x in range(cx-5,cx+6) for y in range(cy-5,cy+6))/255.0
                         for channel in range(3))
        # 源图最外圈的浅亮杂边会在游戏里重复成一层“塑料膜”。
        # 去掉这圈边缘并重建一像素抗锯齿轮廓，球身保持实心，显示直径不变。
        cut_x,cut_y=(x1-x0)*inset,(y1-y0)*inset
        x0+=cut_x; x1-=cut_x
        y0+=cut_y; y1-=cut_y
        sx,sy = 254/(x1-x0),254/(y1-y0)
        path = target/("cup_%s.png" % bet)
        render(CoreImage(str(source)).texture,(256,256),
               (1-x0*sx,1-(h-y1)*sy),(w*sx,h*sy),path,ball_fragment(base_color,compression,lift,target_color,body_fill))
        print(path,path.stat().st_size,"bytes")
    if args.balls_only:
        return
    # 保留原玻璃的完整杯壁/边缘，仅让内部填充在原来的硬切口前渐隐。
    source = ROOT / "assets/glass/glass_tumbler_front.png"
    texture = CoreImage(str(source)).texture
    fragment = "$HEADER$\n" + GLASS_EDGES + """
void main(void) {
    vec4 pixel = texture2D(texture0, tex_coord0);
    float x = tex_coord0.x * 800.0;
    float y = tex_coord0.y * 460.0;
    float interior = smoothstep(125.0,190.0,x) * (1.0-smoothstep(610.0,675.0,x));
    if (y < 420.0) {
        float fade = 1.0-smoothstep(360.0,410.0,y);
        pixel.a *= mix(1.0,fade,interior);
    }
    // 旧前壁左反光横跨球面，像一道划痕；只削弱超出邻近杯壁的反光 alpha。
    float stripeX = 48.5 + 0.315 * y;
    float dx = (x-stripeX)/10.0;
    float stripe = exp(-0.5*dx*dx) * smoothstep(145.0,175.0,y)
                   * (1.0-smoothstep(335.0,375.0,y));
    float nearby = min(texture2D(texture0,tex_coord0-vec2(22.0/800.0,0.0)).a,
                       texture2D(texture0,tex_coord0+vec2(22.0/800.0,0.0)).a);
    pixel.a = mix(pixel.a,min(pixel.a,nearby),0.65*stripe);
    pixel = frontEdges(pixel,vec2(x,y));
    gl_FragColor = frag_color * pixel;
}
"""
    args.glass_output.mkdir(parents=True,exist_ok=True)
    path = args.glass_output.resolve() / "glass_tumbler_front_soft.png"
    render(texture,texture.size,(0,0),texture.size,path,fragment)
    print(path,path.stat().st_size,"bytes")
    texture = CoreImage(str(ROOT/"assets/glass/glass_tumbler_back.png")).texture
    fragment = "$HEADER$\n" + GLASS_EDGES + """
void main(void) {
    vec2 p = tex_coord0*vec2(800.0,460.0);
    gl_FragColor = frag_color*backEdges(texture2D(texture0,tex_coord0),p);
}
"""
    path=args.glass_output.resolve()/"glass_tumbler_back_detail.png"
    render(texture,texture.size,(0,0),texture.size,path,fragment)
    print(path,path.stat().st_size,"bytes")
    texture = CoreImage(str(ROOT/"assets/glass/glass_tumbler_over.png")).texture
    fragment = "$HEADER$\n" + GLASS_EDGES + """
void main(void) {
    vec2 p = tex_coord0*vec2(800.0,460.0);
    vec4 pixel = texture2D(texture0,tex_coord0);
    gl_FragColor = frag_color*mouthEdges(pixel,p,1.0-smoothstep(79.0,81.0,p.y));
}
"""
    path=args.glass_output.resolve()/"glass_tumbler_over_detail.png"
    render(texture,texture.size,(0,0),texture.size,path,fragment)
    print(path,path.stat().st_size,"bytes")


if __name__ == "__main__":
    main()
