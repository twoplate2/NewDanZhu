"""规范化 imagegen 材质源图，并为旧玻璃前层柔化杯底填充接缝。

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault("KIVY_NO_ARGS", "1")
from kivy.core.window import Window
from kivy.core.image import Image as CoreImage
from kivy.graphics import Fbo, ClearColor, ClearBuffers, Rectangle, Callback
from kivy.graphics.opengl import glDisable, glEnable, GL_BLEND

HIGHLIGHT_COMPRESSION = 1.5


def ball_fragment(base_color, compression=HIGHLIGHT_COMPRESSION):
    """仅压缩超过底色的白色反光；中间调、暗部和球身轮廓保持不变。"""
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
    pixel.a = clamp(127.0-length(gl_FragCoord.xy-vec2(128.0)),0.0,1.0);
    gl_FragColor = frag_color*pixel;
}
""" % (*base_color,compression)


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
    args = parser.parse_args()
    source_dir = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    preparation = manifest.get("sprite_preparation", {})
    inset = preparation.get("source_rim_inset_per_side", .015)
    compression = preparation.get("highlight_compression", HIGHLIGHT_COMPRESSION)
    target = args.output.resolve()
    target.mkdir(parents=True,exist_ok=True)
    for bet,name in manifest["sources"].items():
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
               (1-x0*sx,1-(h-y1)*sy),(w*sx,h*sy),path,ball_fragment(base_color,compression))
        print(path,path.stat().st_size,"bytes")
    if args.balls_only:
        return
    # 保留原玻璃的完整杯壁/边缘，仅让内部填充在原来的硬切口前渐隐。
    source = ROOT / "assets/glass/glass_tumbler_front.png"
    texture = CoreImage(str(source)).texture
    fragment = """$HEADER$
void main(void) {
    vec4 pixel = texture2D(texture0, tex_coord0);
    float x = tex_coord0.x * 800.0;
    float y = tex_coord0.y * 460.0;
    float interior = smoothstep(125.0,190.0,x) * (1.0-smoothstep(610.0,675.0,x));
    if (y < 420.0) {
        float fade = 1.0-smoothstep(360.0,410.0,y);
        pixel.a *= mix(1.0,fade,interior);
    }
    gl_FragColor = frag_color * pixel;
}
"""
    path = ROOT / "assets/glass/glass_tumbler_front_soft.png"
    render(texture,texture.size,(0,0),texture.size,path,fragment)
    print(path.relative_to(ROOT),path.stat().st_size,"bytes")


if __name__ == "__main__":
    main()
