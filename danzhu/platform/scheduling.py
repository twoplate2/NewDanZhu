"""可恢复的游戏线程CPU亲和性；默认交给系统调度，不改频率或优先级。"""
import os
import threading

_WORKER_BASELINE = None
_OWNER_TID = None


def reset_worker_affinity():
    """自建后台线程不继承游戏线程的实验绑核；benchmark可随后设置自己的策略。"""
    if _WORKER_BASELINE is None or threading.get_native_id() == _OWNER_TID:
        return
    try:
        if set(os.sched_getaffinity(0)) != _WORKER_BASELINE:
            os.sched_setaffinity(0, _WORKER_BASELINE)
    except (OSError, AttributeError):
        pass  # 系统限制不能阻止音效、存档或诊断任务执行。


class BackgroundThread(threading.Thread):
    def run(self):
        reset_worker_affinity()
        super().run()


def _cpu_value(cpu, name):
    try:
        with open('/sys/devices/system/cpu/cpu%d/%s' % (cpu, name)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def android_game_category():
    """启动时读回安装包分类；不在逐帧路径访问Java。"""
    try:
        from .device import _activity_cls
        activity = _activity_cls().mActivity
        return int(activity.getApplicationInfo().category)
    except Exception:
        return None


def describe_policy(data):
    requested = '优先性能核' if data.get('prefer_fast_cores') else '系统默认'
    actual = ','.join(map(str,data.get('actual',()))) or '未读取'
    category = data.get('app_category')
    game = '游戏' if category == 0 else '未读取' if category is None else str(category)
    return 'CPU调度：%s；主线程允许核组=%s；应用分类=%s；状态=%s；错误=%s' % (
        requested,actual,game,data.get('reason','unknown'),data.get('error') or '无')


class GameCpuPolicy:
    def __init__(self, android=False, backend=os, read_cpu=_cpu_value, tid=None):
        self.backend = backend
        self.read_cpu = read_cpu
        self.tid = threading.get_native_id() if tid is None else tid
        self.android = android
        self.prefer_fast = False
        self.active = True
        self.baseline = set()
        self.fast = set()
        self.capacity = {}
        self.max_khz = {}
        self.error = None
        self.reason = 'system_default'
        self.app_category = android_game_category() if android and backend is os else None
        if not android:
            self.reason = 'not_android'
            return
        try:
            self.baseline = set(backend.sched_getaffinity(self.tid))
            if not self.baseline:
                raise RuntimeError('empty CPU affinity')
            self.capacity = {c:read_cpu(c,'cpu_capacity') for c in self.baseline}
            self.max_khz = {c:read_cpu(c,'cpufreq/cpuinfo_max_freq') for c in self.baseline}
            values = self.capacity if all(v > 0 for v in self.capacity.values()) else self.max_khz
            if values and all(v > 0 for v in values.values()) and max(values.values()) > min(values.values())*1.02:
                top = max(values.values())
                self.fast = {c for c,v in values.items() if v >= top*.98}
            # 实际应用只创建一个策略实例，供其自建Python工作线程恢复允许集合。
            if backend is os:
                global _WORKER_BASELINE, _OWNER_TID
                _WORKER_BASELINE = set(self.baseline)
                _OWNER_TID = self.tid
        except Exception as exc:
            self.error = repr(exc)
            self.reason = 'unavailable'

    def set_preference(self, enabled):
        self.prefer_fast = bool(enabled)
        self.apply()

    def apply(self):
        if not self.android or not self.baseline:
            return
        target = self.fast if self.active and self.prefer_fast and self.fast else self.baseline
        self.reason = ('paused' if not self.active else 'system_default' if not self.prefer_fast
                       else 'fast_cores' if self.fast else 'no_distinct_fast_cluster')
        self.error = None
        try:
            if set(self.backend.sched_getaffinity(self.tid)) != target:
                self.backend.sched_setaffinity(self.tid, target)
            actual = set(self.backend.sched_getaffinity(self.tid))
            if not actual or not actual.issubset(target):
                raise RuntimeError('CPU affinity readback differs from request')
        except Exception as exc:
            self.error = repr(exc)
            self.reason = 'request_failed'
            try:
                self.backend.sched_setaffinity(self.tid, self.baseline)
            except Exception as restore_error:
                self.error += '; restore: ' + repr(restore_error)

    def suspend(self):
        self.active = False
        self.apply()

    def resume(self):
        self.active = True
        self.apply()

    def snapshot(self):
        actual = []
        if self.android and self.baseline:
            try:
                actual = sorted(self.backend.sched_getaffinity(self.tid))
            except (OSError, AttributeError):
                pass
        return dict(prefer_fast_cores=self.prefer_fast,active=self.active,tid=self.tid,
                    baseline=sorted(self.baseline),fast_cores=sorted(self.fast),actual=actual,
                    capacity=dict(self.capacity),max_khz=dict(self.max_khz),
                    app_category=self.app_category,reason=self.reason,error=self.error)
