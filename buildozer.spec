# 安卓打包配置(云构建: .github/workflows/build-apk.yml)。
#
# ⚠️ 历史与踩坑的**全文**在老工程 `DanZhu/android/buildozer.spec`(6668 行, 真配置只有 29 行,
#    其余全是注释)。这份按本工程口径压过 —— 只留「删掉/改错会**静默**出错」的, 复述历史的全删。
#
# ⚠️⚠️ 三条**影响真机功能、错一点不报错只静默失效**的配置:
#   · `android.add_src = java` —— 掉了 ⇒ `java/com/plinko/SoundGate.java` 不进包
#     ⇒ 真机音频就绪闸门**整条静默失效**, 只剩老探针兜底(日志印「闸门 无(类取不到: …)」)。
#   · `version` —— 桌面端 `danzhu/ui/widgets.py::_app_version()` 读的就是本文件这一行
#     (安卓端走 PackageManager 的 versionName)。改这里 = 同时改两个平台显示的版本号。
#   · `p4a.hook` —— 掉了 ⇒ manifest 不被注入, 横屏四方向转不动。
#   · `android.presplash_color` —— 与 `danzhu/config.py::VEIL_BG`、`p4a/hook.py` 注入的
#     `windowSplashScreenBackground` **必须三处同值**; `tests/fx_gates.py` 有一条门禁逐字比
#     这三个值(改一处忘一处, 玩家就会在一次启动里看到两种颜色)。

[app]

title = 跳跳的弹珠机
# ⚠️ 2026-09-19: `plinko` → `tiao`, 目的是**与老版 APK 共存**（用户要求 `org.danzhu.tiao`）。
#    老版是 `org.danzhu.plinko`, 新版若沿用同名, 安卓会当成**同一个应用**:
#    包名相同 + versionCode 相同 + **签名不同**(两台机器各自的 debug 证书, 互不兼容)
#    ⇒ 装新版报签名冲突, 只能先卸载老版 —— 而卸载 = **存档全清**(余额/轮次/跑分历史)。
#    改成 `org.danzhu.tiao` 之后两者是**独立应用**: 可同时装, 存档各存各的
#    (全部走 `user_data_dir` = `/data/data/<包名>/files`; 诊断日志那条降级链的第②③级
#     也含包名, 只有第①级公共 Download 是共享的, 而它带时间戳不会撞)。
#    ⚠️ `java/com/plinko/SoundGate.java` **不受影响** —— 它的 `package com.plinko;` 是
#       独立的 Java 包名, 与这个应用 ID 无关, 而且它没引用 p4a 生成的 PythonActivity。
#    ⚠️ 代价: 新版**看不到老版的存档**(这正是共存的意义); 老版那份原样留着。
package.name = tiao
package.domain = org.danzhu

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
# ⚠️ 这行是**双保险**, 不是主力收集机制 —— 真正干活的是上面 `include_exts`(按扩展名收全树,
#    `danzhu/**/*.py` 就是靠它进去的)。留着是因为"子目录资源必须显式列"这条老经验。
#    注: p4a 这边用的是 `fnmatch`, 其 `*` **跨 `/`** ⇒ `assets/*.png` 收得到
#    `assets/glass/*.png`(资源挪深一层不必改这行)。
source.include_patterns = fonts/*.otf,voice/*.wav,assets/*.png

# ⚠️ 只决定 versionName(显示的那个字符串)。真正管"能不能装上去"的是 versionCode, 而它由
#    下面那行显式钉死 —— **别删, 也别改成跟 version 联动的写法**: 版本号字符串退回去而
#    versionCode 跟着退, Android 会**拒绝安装**(INSTALL_FAILED_VERSION_DOWNGRADE),
#    玩家得先卸载 ⇒ 攒的历史(球数 / 性能测试历史)一起清掉。
# ⚠️ **每次提交都要 +1**（玩家 2026-09-19 定的规矩）—— 一次提交一个号，不跳号、不连跳。
#    上面这段"versionCode 不许联动"说的是 **android.numeric_version 不许动**，
#    跟这里的字符串递增是两码事：version 纯显示，随便加。
version = 0.8.90
android.numeric_version = 1000000

requirements = python3,kivy==2.3.0,pyjnius

# 锁 p4a 到 2024 年的 tag: 不锁的话新版默认去下 Python 3.14 alpha, 编不过。
p4a.branch = v2024.01.21
# ⚠️ 必须在 gradle assemble **之前**改 manifest 才影响最终 APK。
#    (`p4a/hook.py` 的 before/after 都挂了这一手, 幂等。)
p4a.hook = p4a/hook.py

# 四方向随重力。⚠️ **不许锁竖屏**: 12L+ 大屏(sw>=600dp)会把锁定方向的 app 关进固定比例
#    letterbox 兼容盒 —— 那正是当初"画面缩小的病根"。
orientation = portrait, portrait-reverse, landscape, landscape-reverse
# 与 `p4a/hook.py` 的注入是**同一件事**, 显式写一遍双保险。
android.manifest.orientation = fullSensor

# 沉浸式(状态栏/导航栏)由 `main.py` 运行时自己藏, 不靠这里。保持 0。
fullscreen = 0

android.permissions = VIBRATE

# ⚠️⚠️ 删掉这行**不会报错**: `java/` 不进包, SoundGate 取不到类, 音频就绪闸门静默退回老探针。
#     ⚠️ 那个 .java 通篇只写 ASCII —— CI 上 javac 的默认源编码不保证是 UTF-8,
#        注释里一个中文字节就能把整个 APK 编崩。
android.add_src = java

# targetSdk=33: 30 的兼容模式在 12L+ 大屏会被塞 letterbox 盒(声明 fullSensor 也躲不开,
# 系统只认 targetSdk)。别降回去。
android.api = 33
android.minapi = 21
android.ndk = 25b

android.archs = arm64-v8a,armeabi-v7a

android.allow_backup = True

icon.filename = %(source.dir)s/icon.png

# ⚠️ 必须和 presplash.png 的边缘同色(#0b1220), 否则冷启动会闪一下别的颜色。
android.presplash_color = #0b1220
presplash.filename = %(source.dir)s/presplash.png


[buildozer]

log_level = 2
warn_on_root = 1
