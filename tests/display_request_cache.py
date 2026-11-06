"""缓存不可变JNI类/投递器，同时保持系统上限、Activity和显示模式实时读取。"""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.platform import device as D


class Mode:
    def __init__(self,number,hz,width=1600,height=2560):
        self.number,self.hz,self.width,self.height=number,hz,width,height
    def getPhysicalWidth(self):return self.width
    def getPhysicalHeight(self):return self.height
    def getRefreshRate(self):return self.hz
    def getModeId(self):return self.number


class Window:
    def __init__(self,display):self.display=display;self.requests=[];self.modes=[]
    def getWindowManager(self):return SimpleNamespace(getDefaultDisplay=lambda:self.display)
    def setFrameRate(self,hz):self.requests.append(hz)
    def getAttributes(self):return SimpleNamespace(preferredDisplayModeId=0)
    def setAttributes(self,attrs):self.modes.append(attrs.preferredDisplayModeId)


def main():
    modes=[Mode(1,60),Mode(2,120),Mode(3,165)]
    reads=[];display_id=[0]
    def supported():reads.append(1);return modes
    display=SimpleNamespace(getMode=lambda:modes[-1],getSupportedModes=supported,
                            getDisplayId=lambda:display_id[0])
    first=Window(display);second=Window(display)
    def activity(window):return SimpleNamespace(getWindow=lambda:window,getContentResolver=lambda:None)
    cls=SimpleNamespace(mActivity=activity(first))
    state={'system_cap':165,'lookups':[],'decorators':0,'settings_reads':0}
    class Settings:
        PEAK_REFRESH_RATE='peak'
        @staticmethod
        def getFloat(*_):state['settings_reads']+=1;return state['system_cap']
    def autoclass(name):
        state['lookups'].append(name)
        return Settings if name=='android.provider.Settings$System' else SimpleNamespace(SDK_INT=35)
    pending=[]
    def decorator(fn):
        state['decorators']+=1
        shared={}
        def call(*args,**kwargs):
            shared.update(args=args,kwargs=kwargs)
            pending.append(lambda:fn(*shared['args'],**shared['kwargs']))
        return call
    jnius=ModuleType('jnius');jnius.autoclass=autoclass
    android=ModuleType('android');runnable=ModuleType('android.runnable');runnable.run_on_ui_thread=decorator
    modules={'jnius':jnius,'android':android,'android.runnable':runnable}
    saved=(D.platform,D._ACT_CLS[0],D._SETTINGS_CLS[0],D._SDK_LEVEL[0],D._DISPLAY_APPLIER[0],D._FPS_USER_CAP[0],D._BENCH_FPS_LOCK[0],D._DISPLAY_MODE_CACHE[0])
    try:
        D.platform='android';D._ACT_CLS[0]=cls;D._SETTINGS_CLS[0]=D._SDK_LEVEL[0]=D._DISPLAY_APPLIER[0]=None
        D._FPS_USER_CAP[0]=240;D._BENCH_FPS_LOCK[0]=0
        with patch.dict(sys.modules,modules):
            assert D._request_android_high_hz()==(165.,165.)
            state['system_cap']=60
            assert D._request_android_high_hz()==(60.,60.)
            cls.mActivity=activity(second);state['system_cap']=165;D._FPS_USER_CAP[0]=120
            assert D._request_android_high_hz()==(120.,120.)
            modes[:]=[Mode(1,60),Mode(4,90)];D._FPS_USER_CAP[0]=240
            assert D._request_android_high_hz()==(90.,90.)
            assert not first.requests and not second.requests
            for callback in pending:callback()
        assert first.requests==[165.,60.] and second.requests==[120.,90.]
        assert state['decorators']==1 and len(state['lookups'])==2,state
        assert state['settings_reads']==4
        assert len(reads)==4,'默认调用必须完整重查'
        pending.clear();reads.clear();D._DISPLAY_MODE_CACHE[0]=None
        now=[100.]
        with patch.dict(sys.modules,modules),patch.object(D.time,'monotonic',side_effect=lambda:now[0]):
            assert D._request_android_high_hz(reuse_modes=True)==(90.,90.)
            assert len(reads)==1
            assert all(isinstance(v,(int,float)) for m in D._DISPLAY_MODE_CACHE[0][2] for v in m)
            now[0]+=.1;state['system_cap']=60
            assert D._request_android_high_hz(reuse_modes=True)==(60.,60.)
            state['system_cap']=165;D._FPS_USER_CAP[0]=60
            assert D._request_android_high_hz(reuse_modes=True)==(60.,60.)
            D._FPS_USER_CAP[0]=240;D._BENCH_FPS_LOCK[0]=1
            assert D._request_android_high_hz(reuse_modes=True)==(60.,10.)
            D._BENCH_FPS_LOCK[0]=0;cls.mActivity=activity(first)
            assert D._request_android_high_hz(reuse_modes=True)==(90.,90.)
            assert len(reads)==1,'仅模式枚举复用，系统/用户/跑分限制和Window仍现取'
            modes.append(Mode(3,165));now[0]=101.01
            assert D._request_android_high_hz(reuse_modes=True)==(165.,165.)
            assert len(reads)==2,'缓存超过1秒必须重新读取'
            display_id[0]=1
            assert D._request_android_high_hz(reuse_modes=True)==(165.,165.)
            assert len(reads)==3,'更换屏幕不能复用旧模式表'
            modes.append(Mode(5,120,1080,1920))
            assert D._request_android_high_hz(reuse_modes=True)==(120.,120.)
            assert len(reads)==4,'分辨率变化必须重查，只选同分辨率模式'
            modes[-1]=Mode(6,90,1080,1920)
            assert D._request_android_high_hz()==(90.,90.)
            assert len(reads)==5,'生命周期/设置默认路径绕过短缓存'
            with patch.object(display,'getSupportedModes',side_effect=RuntimeError('JNI unavailable')):
                assert D._request_android_high_hz()==(0.,240.)
            assert D._DISPLAY_MODE_CACHE[0] is None,'失败不能留下可复用的旧表'
            assert D._request_android_high_hz(reuse_modes=True)==(90.,90.)
            assert len(reads)==6
            for callback in pending:callback()
        assert first.requests[-6:]==[90.,165.,165.,120.,90.,90.]
        assert state['settings_reads']==14,'命中模式缓存时仍逐次读取系统上限'
    finally:
        D.platform,D._ACT_CLS[0],D._SETTINGS_CLS[0],D._SDK_LEVEL[0],D._DISPLAY_APPLIER[0],D._FPS_USER_CAP[0],D._BENCH_FPS_LOCK[0],D._DISPLAY_MODE_CACHE[0]=saved
    print('PASS: JNI queue, 1s numeric mode cache, live caps/Window, expiry/display/size/full-refresh/error invalidation')


if __name__=='__main__':main()
