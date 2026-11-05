"""同种子比较 0.8.137 与当前顶部轨迹；两版使用相同发射初速和几何。"""
import argparse
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from danzhu import config as C, geo


def implementation(kind):
    source=(subprocess.check_output(['git','show','c7484f7:danzhu/physics.py'],cwd=ROOT,
                                   text=True,encoding='utf-8') if kind=='before' else
            (ROOT/'danzhu/physics.py').read_text(encoding='utf-8'))
    module=types.ModuleType('danzhu.top_probe');module.__package__='danzhu'
    exec(compile(source,'<top_probe>','exec'),module.__dict__)
    return module


def measure(kind,samples):
    physics=implementation(kind)
    output={}
    for power in C.KNOB_POWERS:
        rows=[]
        for seed in range(samples):
            rng=random.Random(47000+seed);scene=geo.build_geo();dy=rng.uniform(-6,6)
            scene['deflectors']=[(a,b+dy,c,d+dy) for a,b,c,d in scene['deflectors']]
            ball=physics.launch_ball(power,rng)
            first=None;top=slow=0;minimum=9999;entered=returned=peg=False;slot=None
            for step in range(1800):
                ball.events=0
                slot=physics.advance_flight(ball,scene)
                if first is None and ball.events&C.EV_ARC:
                    first=dict(vx=ball.vx,vy=ball.vy,speed=math.hypot(ball.vx,ball.vy),time=(step+1)/60)
                if ball.events&C.EV_PEG:peg=True
                if not peg and ball.y<C.PEG_TOP:
                    speed=math.hypot(ball.vx,ball.vy)
                    top+=1/60;slow+=int(speed<180)/60;minimum=min(minimum,speed)
                entered |= ball.x < C.LANE_L-C.BALL_R
                returned |= entered and ball.x>C.LANE_L and ball.y>C.LANE_WALL_TOP
                if slot is not None:break
            rows.append(dict(seed=seed,arc=first,top=top,slow=slow,minimum=minimum,
                             returned=returned,slot=slot,duration=(step+1)/60))
        output[str(power)]=dict(top_median=statistics.median(r['top'] for r in rows),
            low_speed_mean=statistics.mean(r['slow'] for r in rows),
            low_speed_cases=sum(r['slow']>0 for r in rows),
            min_speed_median=statistics.median(r['minimum'] for r in rows),
            right_arc=sum(r['arc']['vx']>0 for r in rows if r['arc']),
            returns=sum(r['returned'] for r in rows),unfinished=sum(r['slot'] is None for r in rows),
            samples=rows)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--samples',type=int,default=100)
    args=parser.parse_args()
    folder=ROOT/'tools/output/top_launch';folder.mkdir(parents=True,exist_ok=True)
    for kind in ('before','after'):
        output=measure(kind,args.samples)
        (folder/(kind+'.json')).write_text(json.dumps(output,indent=2),encoding='utf-8')
        print(kind,json.dumps({k:{n:v for n,v in row.items() if n!='samples'} for k,row in output.items()}),flush=True)
