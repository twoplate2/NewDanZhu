"""离线烘焙每颗球的局部遮蔽；固定几何，运行时仅读取图集和渐入alpha。"""
import ast,json,math,sys,time
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from danzhu.fx.pile import project_pile
from danzhu.fx.natural_pile_data import DATA as NATURAL,RADII
from danzhu.fx.floor_pile_data import DATA as FLOOR,RADIUS

TILE=80;PAD=2;CELL=TILE+2*PAD


def layouts():
    tree=ast.parse((ROOT/'danzhu/ui/winfx.py').read_text(encoding='utf-8'));old={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('_PILE_BAKED','_PILE_BAKED_R'):
            old[node.targets[0].id]=ast.literal_eval(node.value)
    for n in (2,3,5,10,20,50,100):
        for v in range(12):
            if n in (10,20):points=FLOOR[n,v];r=RADIUS
            elif n in (50,100):points=NATURAL[n,v];r=RADII[n]
            else:
                points=[tuple(int(a)/10 for a in p.split(',')) for p in old['_PILE_BAKED'][n,v].split(';')]
                r=old['_PILE_BAKED_R'][n]
            beads=[dict(x=x,z=z,h=h,r=r,layer=0) for x,z,h in points]
            projected=project_pile(beads)
            ordered=np.array([[beads[p['i']][k] for k in ('x','z','h')] for p in projected])
            yield n,v,r,ordered


def mask(center,r,points,index):
    yy,xx=np.mgrid[:TILE,:TILE];x=(xx+.5-TILE/2)/(TILE/2);y=(yy+.5-TILE/2)/(TILE/2)
    rr=x*x+y*y;inside=rr<1;z=np.sqrt(np.maximum(0,1-rr));co=math.sqrt(.96)
    normals=np.stack([x,-y*.2-z*co,-y*co+z*.2],axis=-1)[inside].astype(np.float32)
    p=center+normals*(r+.3)
    distances=np.linalg.norm(points-center,axis=1)
    peers=[j for j in np.argsort(distances) if j!=index and distances[j]<4*r][:12]
    directions=[]
    for i in range(18):
        h=1-2*(i+.5)/18;a=i*math.pi*(3-math.sqrt(5));rad=math.sqrt(1-h*h)
        directions.append(np.array([rad*math.cos(a),rad*math.sin(a),h]))
    weights=[np.maximum(0,normals@d) for d in directions];hits=[np.zeros(len(p)) for _ in directions]
    light=np.array([-.43,.108-.724*co,.54*co+.724*.2]);light/=np.linalg.norm(light)
    lights=[light]+[light+np.array([.20*math.cos(i*math.pi/4),.12*math.sin(i*math.pi/4),.08*math.sin(i*math.pi/4)]) for i in range(8)]
    lights=[d/np.linalg.norm(d) for d in lights];shadows=[np.zeros(len(p)) for d in lights]
    contributors=[]
    for j in peers:
        delta=points[j]-p;c=np.sum(delta*delta,axis=1)-r*r;contributes=False
        for k,d in enumerate(directions):
            along=delta@d;disc=along*along-c;distance=along-np.sqrt(np.maximum(0,disc))
            good=(disc>0)&(distance>0)&(distance<3*r)
            amount=np.where(good,np.exp(-np.maximum(0,distance)/(1.5*r)),0)
            if np.any(amount*weights[k]>.015):contributes=True
            hits[k]=np.maximum(hits[k],amount)
        for k,d in enumerate(lights):
            along=delta@d;disc=along*along-c;distance=along-np.sqrt(np.maximum(0,disc))
            good=(disc>0)&(distance>0)
            if np.any(good):contributes=True
            shadows[k][good]=1
        if contributes:contributors.append(int(j))
    ao=sum(w*h for w,h in zip(weights,hits))/np.maximum(.01,sum(weights))
    alpha=1-(1-.55*ao)*(1-.24*sum(shadows)/len(shadows))
    field=np.zeros((TILE,TILE));field[inside]=alpha
    field=gaussian_filter(field,1.2)*np.clip((1-np.sqrt(rr))*TILE/2,0,1)
    rgba=np.zeros((TILE,TILE,4),np.uint8);rgba[:,:,:3]=[9,7,16];rgba[:,:,3]=np.rint(np.clip(field,0,.60)*255)
    return rgba,contributors


def main():
    target=ROOT/'assets/cup_shadows';target.mkdir(exist_ok=True)
    manifest={'tile':TILE,'cell':CELL,'padding':PAD,'strength':.68,'layouts':{}}
    started=time.perf_counter()
    for n,v,r,points in layouts():
        cols=math.ceil(math.sqrt(n));rows=math.ceil(n/cols);atlas=np.zeros((rows*CELL,cols*CELL,4),np.uint8);links=[]
        for i,center in enumerate(points):
            rgba,peers=mask(center,r,points,i);x=(i%cols)*CELL+PAD;y=(i//cols)*CELL+PAD
            atlas[y:y+TILE,x:x+TILE]=rgba;links.append(peers)
        name=f'pile_{n}_{v}.png';Image.fromarray(atlas).save(target/name)
        manifest['layouts'][f'{n}:{v}']={'file':name,'columns':cols,'rows':rows,'points':np.round(points,4).tolist(),'radius':r,'peers':links}
        if v==11:print('BAKED',n,'elapsed',round(time.perf_counter()-started,1),flush=True)
    (ROOT/'tools/art/piles/shadows_manifest.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf-8')
    data={(int(k.split(':')[0]),int(k.split(':')[1])):v for k,v in manifest['layouts'].items()}
    (ROOT/'danzhu/fx/cup_shadow_data.py').write_text(
        '"""离线局部遮蔽元数据，由tools/bake_cup_shadows.py生成。"""\n'
        +f'TILE={TILE}\nCELL={CELL}\nPAD={PAD}\nSTRENGTH=.68\nDATA={{\n'
        +''.join('    '+repr(k)+': '+repr(v)+',\n' for k,v in data.items())+'}\n',encoding='utf-8')
    print('ATLAS_TOTAL_BYTES',sum(p.stat().st_size for p in target.iterdir()),flush=True)


if __name__=='__main__':main()
