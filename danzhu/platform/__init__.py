"""平台层: 启动日志(boot) / 设备胶水(device) / 跑分与电池(benchcpu)。

⚠️ 这里**故意不 re-export 子模块**: `device` 走 pyjnius、`boot` 只碰 time, 混在一个包级
   import 里会让"只想记一条启动日志"的调用方被平台依赖拖住。
"""
