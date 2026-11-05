"""离线重排大球堆：固定半径、球数、杯壁，通过接触约束与重力势能求终态。

numpy/scipy 仅开发工具使用，不进入游戏运行依赖。
"""
import argparse
import ast
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import minimize,linprog

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from danzhu.fx.pile import FLOOR_Y,RIM_H,halfwidth


def legacy():
    tree=ast.parse((ROOT/"danzhu/ui/winfx.py").read_text(encoding="utf-8"))
    values={}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ("_PILE_BAKED","_PILE_BAKED_R"):
                    values[target.id]=ast.literal_eval(node.value)
    return values


def wall(h,r):
    y=np.clip(FLOOR_Y-h*r,80.0,404.0)
    t=(364.0-np.sqrt(132496.0-160.0*(y-80.0)))/80.0
    x=39.0+52.0*t+19.0*t*t
    radius=(400.0-x)/r-1.04
    slope=(52.0+38.0*t)/(364.0-80.0*t)
    return radius,slope


def settle(count,variant,trial=0):
    data=legacy();r=data['_PILE_BAKED_R'][count]
    points=np.array([[float(v)/10 for v in item.split(',')]
                     for item in data['_PILE_BAKED'][(count,variant)].split(';')])/r
    rng=np.random.default_rng(9173+count*131+variant*71+trial*1999)
    points[:,:2]+=rng.normal(0,.38,(count,2))
    points[:,2]+=rng.normal(0,.16,count)
    ii,jj=np.triu_indices(count,1)
    def energy(flat,g):
        p=flat.reshape(count,3);grad=np.zeros_like(p)
        delta=p[ii]-p[jj];dist=np.sqrt(np.sum(delta*delta,axis=1)+1e-18)
        overlap=np.maximum(0,2-dist)
        forces=-overlap[:,None]*delta/dist[:,None]
        np.add.at(grad,ii,forces);np.add.at(grad,jj,-forces)
        floor=np.minimum(0,p[:,2]-1)
        grad[:,2]+=floor+g
        radial=np.sqrt(np.sum(p[:,:2]**2,axis=1)+1e-18)
        limit,slope=wall(p[:,2],r)
        penetration=np.maximum(0,radial-limit)
        grad[:,:2]+=penetration[:,None]*p[:,:2]/radial[:,None]
        grad[:,2]-=penetration*slope
        value=.5*(np.dot(overlap,overlap)+np.dot(floor,floor)+np.dot(penetration,penetration))+g*np.sum(p[:,2])
        return value,grad.ravel()
    started=time.perf_counter()
    for gravity,steps in ((.015,1400),(.001,1200),(.000025,1800)):
        result=minimize(energy,points.ravel(),args=(gravity,),jac=True,method='L-BFGS-B',
                        options={'maxiter':steps,'ftol':1e-14,'gtol':1e-9,'maxls':35})
        points=result.x.reshape(count,3)
    points[np.abs(points[:,2]-1)<.002,2]=1
    points=np.round(points*r,5)
    report=validate(points,r)
    report.update(count=count,variant=variant,trial=trial,seconds=round(time.perf_counter()-started,3))
    return points,r,report


def validate(points,r):
    p=points/r;n=len(p);ii,jj=np.triu_indices(n,1)
    distance=np.linalg.norm(p[ii]-p[jj],axis=1)
    wall_limit,slopes=wall(p[:,2],r)
    radial=np.linalg.norm(p[:,:2],axis=1)
    unsupported=[];max_residual=0.0
    columns=[];contact_keys=[]
    for i,j,distance_ij in zip(ii,jj,distance):
        if distance_ij<=2.004:
            col=np.zeros((n,3));normal=(p[i]-p[j])/distance_ij
            col[i]=normal;col[j]=-normal;columns.append(col.ravel());contact_keys.append((int(i),int(j)))
    for i,b in enumerate(p):
        if b[2]<=1.002:
            col=np.zeros((n,3));col[i,2]=1;columns.append(col.ravel());contact_keys.append((i,-1))
        if radial[i]>=wall_limit[i]-.004:
            col=np.zeros((n,3))
            col[i]=[-b[0]/radial[i],-b[1]/radial[i],slopes[i]]
            columns.append(col.ravel())
            contact_keys.append((i,-2))
    matrix_all=np.array(columns).T
    gravity=np.tile([0.,0.,1.],n)
    equilibrium=linprog(np.ones(len(columns)),A_eq=matrix_all,b_eq=gravity,bounds=(0,None),method='highs')
    for i,b in enumerate(p):
        if b[2]<=1.002:continue
        normals=[]
        for j,q in enumerate(p):
            if i==j or q[2]>=b[2]:continue
            d=b-q;length=np.linalg.norm(d)
            if length<=2.004:normals.append(d/length)
        if radial[i]>=wall_limit[i]-.004:
            normals.append(np.array([-b[0]/radial[i],-b[1]/radial[i],slopes[i]]))
        if not normals:
            unsupported.append(i);continue
        matrix=np.array(normals).T
        result=linprog(np.ones(len(normals)),A_eq=matrix,b_eq=[0,0,1],bounds=(0,None),method='highs')
        if not result.success:unsupported.append(i)
        else:max_residual=max(max_residual,float(np.max(np.abs(matrix@result.x-[0,0,1]))))
    return {'minimum_separation':float(distance.min()*r),
            'max_overlap':float(max(0,2-distance.min())*r),
            'max_wall_penetration':float(max(0,(radial-wall_limit).max())*r),
            'min_height':float(points[:,2].min()),'max_height':float(points[:,2].max()),
            'needs_lateral_support':unsupported,'support_residual':max_residual,
            'global_force_equilibrium':bool(equilibrium.success),
            'force_certificate':[(i,j,float(force)) for (i,j),force in zip(contact_keys,equilibrium.x) if force>1e-9] if equilibrium.success else [],
            'valid':bool(distance.min()>=1.998 and (radial-wall_limit).max()<.002
                         and p[:,2].min()>=.999 and points[:,2].max()<=RIM_H+4 and equilibrium.success)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--counts',default='50,100');parser.add_argument('--variants',default='1')
    parser.add_argument('--trial',type=int,default=0)
    parser.add_argument('--output',type=Path,default=ROOT/'tools/output/cup_material_review/settled_trials.json')
    parser.add_argument('--export-module',type=Path)
    args=parser.parse_args();out={}
    for n in map(int,args.counts.split(',')):
        for v in map(int,args.variants.split(',')):
            points,r,report=settle(n,v,args.trial)
            out[f'{n}:{v}']={'radius':r,'points':points.tolist(),'validation':report}
            print(json.dumps(report),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,separators=(',',':')),encoding='utf-8')
    if args.export_module:
        data={};radii={};validation={}
        for n in map(int,args.counts.split(',')):
            entry=out[f'{n}:1']
            if not entry['validation']['valid']:raise ValueError('cannot export an invalid pile')
            r=entry['radius'];radii[n]=r
            for variant in range(12):
                angle=math.radians((variant-1)*137.508)
                c,s=math.cos(angle),math.sin(angle)
                points=np.array(entry['points'])
                x,z=points[:,0].copy(),points[:,1].copy()
                points[:,0]=x*c-z*s;points[:,1]=x*s+z*c
                points=np.round(points,5)
                check=validate(points,r)
                if not check['valid']:raise ValueError(f'rotated pile invalid: {n}/{variant}')
                data[n,variant]=tuple(tuple(float(x) for x in p) for p in points)
                validation[f'{n}:{variant}']=check
        content='"""离线生成的自然球堆。使用 tools/bake_settled_piles.py 重建，不手改坐标。"""\n'
        content+='RADII = '+repr(radii)+'\nDATA = {\n'
        for key,points in data.items():content+='    '+repr(key)+': '+repr(points)+',\n'
        content+='}\n'
        args.export_module.write_text(content,encoding='utf-8')
        audit=ROOT/'tools/art/piles/natural_manifest.json';audit.parent.mkdir(parents=True,exist_ok=True)
        audit.write_text(json.dumps({'generator':'tools/bake_settled_piles.py',
                        'method':'gravity/contact constrained optimization; 12 rigid rotations of one validated base per count',
                        'trial':args.trial,'radii':radii,'base':{k:v['validation'] for k,v in out.items()},
                        'variants':validation},indent=2),encoding='utf-8')


if __name__=='__main__':main()
