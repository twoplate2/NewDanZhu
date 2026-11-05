"""固定杯形的离线玻璃剖面；替换旧描边，不叠加平行亮线。"""

GLASS_EDGES = r"""
vec4 glassOver(vec4 dst, vec3 rgb, float a) {
    a=clamp(a,0.0,1.0);
    float total=a+dst.a*(1.0-a);
    return vec4((rgb*a+dst.rgb*dst.a*(1.0-a))/max(total,0.00001),total);
}
float ellipseDistance(vec2 p,vec2 center,vec2 radii) {
    vec2 q=(p-center)/radii;
    float len=length(q);
    return (len-1.0)*len/max(length(q/radii),0.00001);
}
float fineEdge(float d,float width) {
    return exp(-0.5*d*d/(width*width));
}
vec4 clearOldRim(vec4 pixel,vec2 p,float halfMask) {
    float d=ellipseDistance(p,vec2(400.0,80.0),vec2(374.0,74.0));
    pixel.a*=1.0-0.97*(1.0-smoothstep(3.0,6.0,abs(d)))*halfMask;
    return pixel;
}
vec4 mouthEdges(vec4 pixel,vec2 p,float halfMask) {
    pixel=clearOldRim(pixel,p,halfMask);
    float d=ellipseDistance(p,vec2(400.0,80.0),vec2(374.0,74.0));
    float s=-d;
    float band=smoothstep(-0.8,0.6,s)*(1.0-smoothstep(2.5,4.2,s));
    float left=exp(-pow((p.x-128.0)/105.0,2.0));
    float right=exp(-pow((p.x-710.0)/65.0,2.0));
    float light=0.10+0.53*left+0.34*right;
    // 同一窄带内连续过渡；中央前沿较淡，转折处反光更强。
    float profile=0.36+0.64*exp(-pow((s-0.65)/1.3,2.0));
    pixel=glassOver(pixel,mix(vec3(0.30,0.39,0.49),vec3(0.85,0.91,0.98),profile),
                    band*light*halfMask);
    pixel=glassOver(pixel,vec3(0.91,0.95,1.0),fineEdge(d+0.25,0.48)*
                    (0.06+0.42*left+0.25*right)*halfMask);
    return pixel;
}
vec4 frontEdges(vec4 pixel,vec2 p) {
    pixel=mouthEdges(pixel,p,smoothstep(79.0,81.0,p.y));
    float outer=ellipseDistance(p,vec2(400.0,404.0),vec2(290.0,54.0));
    float front=smoothstep(404.0,414.0,p.y);
    pixel.a*=1.0-0.90*(1.0-smoothstep(3.0,6.0,abs(outer)))*front;
    float u=clamp((p.x-400.0)/290.0,-1.0,1.0);
    float curve=sqrt(max(0.0,1.0-u*u));
    float outerY=404.0+54.0*curve;
    float innerY=404.0+46.0*curve;
    float t=clamp((p.y-innerY)/max(outerY-innerY,1.0),0.0,1.0);
    float band=smoothstep(innerY-0.8,innerY+1.0,p.y)*
               (1.0-smoothstep(outerY-0.8,outerY+0.8,p.y))*front;
    float light=0.08+0.27*exp(-pow((p.x-260.0)/125.0,2.0))+
                         0.25*exp(-pow((p.x-655.0)/65.0,2.0));
    vec3 tone=mix(vec3(0.045,0.065,0.095),vec3(0.56,0.68,0.81),t*t);
    pixel=glassOver(pixel,tone,band*(0.15+light));
    pixel=glassOver(pixel,vec3(0.83,0.90,0.97),fineEdge(outer+0.6,0.75)*light*front);
    // 窄反光沿杯壁几何走，不横跨球面。
    float y=clamp(p.y,80.0,404.0);
    float wallT=(364.0-sqrt(132496.0-160.0*(y-80.0)))/80.0;
    float wallX=39.0+52.0*wallT+19.0*wallT*wallT;
    float wall=smoothstep(95.0,125.0,p.y)*(1.0-smoothstep(385.0,405.0,p.y));
    float wallLight=0.10+0.20*exp(-pow((p.y-180.0)/100.0,2.0));
    pixel=glassOver(pixel,vec3(0.73,0.84,0.95),wall*wallLight*
                    (fineEdge(p.x-wallX,0.9)+0.6*fineEdge(800.0-p.x-wallX,0.9)));
    return pixel;
}
vec4 backEdges(vec4 pixel,vec2 p) {
    pixel=clearOldRim(pixel,p,1.0-smoothstep(79.0,81.0,p.y));
    float bottom=ellipseDistance(p,vec2(400.0,404.0),vec2(290.0,54.0));
    float rear=1.0-smoothstep(402.0,406.0,p.y);
    pixel.a*=1.0-0.75*(1.0-smoothstep(3.0,6.0,abs(bottom)))*rear;
    return pixel;
}
"""
