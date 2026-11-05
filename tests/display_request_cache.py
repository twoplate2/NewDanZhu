"""缓存不可变JNI类/投递器，同时保持系统上限、Activity和显示模式实时读取。"""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from danzhu.platform import device as D


class Mode:
    def __init__(self,number,hz):self.number,self.hz=number,hz
    def getPhysicalWidth(self):return 1600
    def getPhysicalHeight(self):return 2560
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
    display=SimpleNamespace(getMode=lambda:modes[-1],getSupportedModes=lambda:modes)
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
    saved=(D.platform,D._ACT_CLS[0],D._SETTINGS_CLS[0],D._SDK_LEVEL[0],D._DISPLAY_APPLIER[0],D._FPS_USER_CAP[0],D._BENCH_FPS_LOCK[0])
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
    finally:
        D.platform,D._ACT_CLS[0],D._SETTINGS_CLS[0],D._SDK_LEVEL[0],D._DISPLAY_APPLIER[0],D._FPS_USER_CAP[0],D._BENCH_FPS_LOCK[0]=saved
    print('PASS: JNI classes/decorator reused; system cap, user cap and new Activity still honored')


if __name__=='__main__':main()
