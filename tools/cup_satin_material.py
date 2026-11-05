"""统一的实心球离线光照；四色只换颜料，不使用四套不一致的源图亮度。"""


def fragment(color,specular=.18):
    return """$HEADER$
vec3 toLinear(vec3 c) {
    return mix(c/12.92,pow((c+0.055)/1.055,vec3(2.4)),step(vec3(0.04045),c));
}
vec3 toSRGB(vec3 c) {
    return mix(c*12.92,1.055*pow(max(c,vec3(0.0)),vec3(1.0/2.4))-0.055,
               step(vec3(0.0031308),c));
}
void main(void) {
    vec2 p=(gl_FragCoord.xy-vec2(128.0))/127.0;
    float r2=dot(p,p);
    float nz=sqrt(max(0.0,1.0-r2));
    vec3 normal=vec3(p,nz);
    vec3 pigment=toLinear(vec3(%f,%f,%f));
    float diffuse=max(0.0,dot(normal,normalize(vec3(-0.40,0.55,0.73))));
    float illumination=0.12+0.12*nz+0.72*diffuse;
    float facing=max(0.0,dot(normal,normalize(vec3(-0.35,0.45,0.82))));
    // 紧凑柔和高光；能量混合避免蓝色高光通道截平。
    float reflection=%f*pow(facing,44.0)+0.025*pow(facing,12.0);
    vec3 rgb=mix(pigment*illumination,vec3(0.98,0.99,1.0),reflection);
    float alpha=clamp(127.0-length(gl_FragCoord.xy-vec2(128.0)),0.0,1.0);
    gl_FragColor=frag_color*vec4(toSRGB(rgb),alpha);
}
""" % (*color,specular)
