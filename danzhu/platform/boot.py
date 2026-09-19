"""启动日志 + 探针记账(平台层最底层: 只 import time, 不碰 kivy / 本包其它模块)。

⚠️ 这里几个模块级对象是**跨模块共享状态**: audio / ui 都必须 import 同一份, 另建一份
   不会报错, 只会让导出的启动日志**静默少掉一部分行**(读它的只有导出那一处)。
⚠️ `_boot_log` 自己吞掉一切异常且必须极便宜: 调用点不许写 try, 也绝不许它拖慢启动。
"""

import time

# ⚠️ t0 取在**包内其它模块被导入之前** —— 后面的导入(几百毫秒)也是启动时间的一部分。
_BOOT_T0 = time.perf_counter()
_BOOT_LOG = []          # [(t_ms, tag, msg)]


def _boot_log(tag, msg):
    """记一条启动日志。tag 是分组(boot/sfx/bake/load/probe/prebake), 导出时按时间排序。"""
    try:
        _BOOT_LOG.append(((time.perf_counter() - _BOOT_T0) * 1000.0, tag, str(msg)))
    except Exception:
        pass


# 探针逐轮的进度采样: [(t_ms, 连续就绪个数, 本轮是否全过)] —— 只给导出时的自动判定用。
# ⚠️ 它**不参与任何判据**(没有任何逻辑读它做判断), 只被 `_startup_log_text` 读一次。
_PROBE_TRACE = []
# 点名的**自身成本**累计: [探针自己花掉的 ms, 真扫次数] —— 只给启动日志算「单价」用。
# ⚠️ 与 `_PROBE_TRACE` 一样, **不参与任何判据**(没有任何逻辑读它做判断)。
_PROBE_COST = [0.0, 0]
