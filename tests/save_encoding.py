"""小存档一次编码与逐块编码内容相同，同时不产生编码器闭包循环。"""
import gc
import io
import json


def main():
    values=[{'bet':100,'balance':4321,'rtp_target':3.6,'max_plays':50},
            [{'plays':50,'balance':0,'time':12345.25}],{'文字':'保存测试','flag':True,'none':None}]
    for value in values:
        target=io.StringIO();json.dump(value,target)
        assert target.getvalue()==json.dumps(value)
    enabled=gc.isenabled();gc.disable()
    counts=[]
    try:
        for streaming in (True,False):
            gc.collect(2)
            for _ in range(100):
                target=io.StringIO()
                if streaming:json.dump(values[0],target)
                else:target.write(json.dumps(values[0]))
            del target
            counts.append(gc.collect(2))
        assert counts[1]<counts[0]/10,counts
    finally:
        if enabled:gc.enable()
    print('PASS: identical JSON; streaming/one-shot unreachable objects',counts)


if __name__=='__main__':main()
