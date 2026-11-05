"""轻量进程驻留内存采样；Android/Linux使用VmRSS，Windows使用工作集。

含共享驻留页，不等于独占PSS、Python堆或完整GPU内存。仅在发射间隔的工作线程读取。
"""
import os

_WIN_API = None


def _windows_rss():
    global _WIN_API
    if _WIN_API is None:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)] + [
                (name,ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage',
                    'PagefileUsage','PeakPagefileUsage')]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        psapi=ctypes.WinDLL('psapi',use_last_error=True)
        current=kernel.GetCurrentProcess
        current.argtypes=[];current.restype=wintypes.HANDLE
        read=psapi.GetProcessMemoryInfo
        read.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        read.restype=wintypes.BOOL
        _WIN_API=(ctypes,Counters,current,read)
    ctypes,Counters,current,read=_WIN_API
    counters=Counters();counters.cb=ctypes.sizeof(counters)
    if not read(current(),ctypes.byref(counters),counters.cb):
        raise OSError(ctypes.get_last_error(),'GetProcessMemoryInfo')
    return int(counters.WorkingSetSize)


def _proc_rss():
    with open('/proc/self/status',encoding='ascii') as stream:
        for line in stream:
            if line.startswith('VmRSS:'):
                fields=line.split()
                if len(fields)!=3 or fields[2]!='kB':raise ValueError('invalid VmRSS unit')
                value=int(fields[1])*1024
                if value<0:raise ValueError('negative VmRSS')
                return value
    raise ValueError('VmRSS unavailable')


def process_memory_snapshot():
    source='windows_working_set' if os.name=='nt' else 'proc_self_status_vmrss'
    try:
        value=_windows_rss() if os.name=='nt' else _proc_rss()
        return dict(memory_rss_bytes=value,memory_source=source,memory_error=None)
    except (OSError,ValueError,AttributeError) as exc:
        return dict(memory_rss_bytes=None,memory_source=source,memory_error=str(exc))
