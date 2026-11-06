"""合并弹窗切换产生的高刷重申；只由主线程调用。"""


class RefreshBurst:
    def __init__(self, apply, schedule, clock, minimum_interval=.1, settle_delay=.35):
        self.apply = apply
        self.schedule = schedule
        self.clock = clock
        self.minimum_interval = minimum_interval
        self.settle_delay = settle_delay
        self.last_immediate = float('-inf')
        self.pending = None

    def request(self):
        # 每次切换都保留最后一次动画结束后的补请求，取消被它覆盖的旧回调。
        if self.pending is not None:
            self.pending.cancel()
        self.pending = self.schedule(self._settled, self.settle_delay)
        now = self.clock()
        if now - self.last_immediate >= self.minimum_interval:
            self.last_immediate = now
            self.apply(False)

    def _settled(self, _dt):
        self.pending = None
        # 补请求不延长立即请求的合并窗口；玩家随后关闭弹窗仍应马上重申。
        self.apply(True)
