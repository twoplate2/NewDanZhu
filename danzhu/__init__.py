"""跳跳的弹珠机 —— 安卓版重构工程。

⚠️ 这里**故意不 re-export 子模块**: 各模块(physics / rules / audio / ui ...)独立
   可分, 包级 import 会把一个模块的缺失变成整个包的 ImportError。
   下游一律 `from danzhu import geo` / `from danzhu.config import BALL_R`。
"""
