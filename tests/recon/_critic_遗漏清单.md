Reading complete. Below is what I verified directly in `E:\AI_Tools\other\DanZhu\android\main.py` (22,347 lines) and could **not** find in any of the 13 scout summaries.

# 遗漏清单

## A. 玩家可见行为（13 条）

**1. 中文字体是「子集」，新加汉字会变豆腐块（真机可见，桌面看不见）**
为什么算遗漏：全部摘要里只有 scout 9 一句 `font-register:中文字体同名覆盖注册`，没人指出字体是**裁剪过的子集**。它是一条**约束型**功能——重写时任何人新写一句中文 UI 文案，真机上就是一个方框，而桌面截图验证不出来。代码里有**三处**为它让路：
- 2250-2261：`_live_power_temp_line` 里「摄氏度」的「摄/氏」不在字库 ⇒ 写成「度」；「瓦」不在字库 ⇒ 写成 `W`
- 14860-14864：`ppw` 面板「每瓦跑分」的「瓦」渲染成方块 ⇒ 改成「**每W跑分**」（注释写明：实测字库只有 **1602** 个字形）
- 18963-18965：「平均差系数」脚注里那个「**群**」字不在子集 ⇒ 删掉整段
- 18411-18415：**全角 `＝`** 真机是方块而桌面正常 ⇒ 结论「UI 文案别用全角冷门符号」
- 注册点在 487-493：`fonts/NotoSansSC-Medium.otf` 以 `name="Roboto"` 全局覆盖，**文件不存在就静默跳过 ⇒ 全屏汉字变方块**（`fonts/` 里 417KB 子集 vs `temp/NotoSansSC-Medium.ORIG.otf` 8.3MB）
类别：平台适配 + 玩家可见 + silent-invariant

**2. 结算结果窗口 `_result_until`：中奖演出期间切档/切投注**不播**语音**
20300-20302 设 `_result_until = now + 2.5 + win_fx.expected_sec(m)`；19800（`set_bet`）与 20025（`set_rtp`）的语音分支都带 `and time.time() >= self._result_until`。这是「结果音优先、UI 语音让位」的规则，摘要里只写了语音映射，没写这条抑制窗。
类别：玩家可见（听觉）+ silent-invariant

**3. `voice_lose` 已制作但**从不接入**（且仍被全量加载）**
20921：`self.sfx.play("lose", 0.9)    # "好遗憾"语音已制作(voice_lose), 暂不接入`。`voice/voice_lose.wav` 实际存在（61 条清单里），而 `_voice_files()` 按目录全量加载 ⇒ 它占一次加载、永不发声。重写时「未中」只有一声琶音、没有人声。
类别：玩家可见（听觉）

**4. `reset_balance` 会去重/清理飘字控件（防控件泄漏）**
20048-20056：先把 `kind == "toast"` 的 widget 逐个 `remove_widget`，再从 `_effects` 列表过滤 —— 注释写明「先移除 widget 再从列表过滤, 防控件泄漏」。摘要只写了 `reset-balance` 的语义，没这条。
类别：silent-invariant（漏了会泄漏 widget 且飘字叠在一起）

**5. 按钮变灰是「身份色朝底色混一档」，不是涂成 `COL_BTN_OFF`；「重置」独有描边**
- 14060-14090 `_restyle_buttons`：注释明写它是「**按钮底色的唯一取值口**」，输入锁时走 `dim_rgb(h, BTN_OFF_DIM=0.55)`（827 行，实测取值区间 [0.45, 0.72]）；还规定 `state=="charging"` 时**跳开发射键**（否则抢 `_frame` 的力度色，闪一帧亮红）
- 14018-14048 `_outline_btn` + 14050 `_sync_outline`：「重置」用描边与「未选中档」区分（两轮 A/B 截图试过换底色都不行），描边色**每次按当前底色重算**、跟着输入锁一起 dim
- 14094-14138 `_set_controls_enabled`：三标签染色走 `_set_lbl_tint`/`_tint_from`（12742-12783，逐通道 ratio，因为 Kivy 的着色是 RGB 相乘），**拿不到画布 Color 必须退回写 `.color`**
摘要里只有 `input-lock:输入锁(吞触摸)与置灰`一句，这三条规则全无。
类别：玩家可见（一眼看出押了哪一档）+ silent-invariant

**6. 「按下发射键的那根手指」用 uid 认（多指抬手不许把球打出去）**
14140-14168（`on_touch_down` 里 `self._charge_uid = touch.uid`，用**显式命中判定**而非 `touch.grab_current`——实测 grab_current 恒为 None）+ 14247-14257（`_on_title_touch_up` 只认同一 uid）。这是 2026-09-18 才修的玩家 bug，摘要里的 `title-touch-up` 没有它。
类别：玩家可见（bug 回归）+ silent-invariant

**7. 钉子受击高亮 / 球受击压扁 / 球自转 / 弹簧阻尼振荡**（渲染层手感）
- 1054-1057：碰钉写 `b.peg_flash`、`b.squash = 1-0.05*vn/E_VREF`、`b.spin += (vx*ny - vy*nx)*0.02`
- 12536-12560：60ms 电光金 `#ffe500` + 240ms 渐回 + 半径 1.2x
- 12588-12605：弹簧 `k=120, damp=3.2`，过冲到 `-0.25`（回弹约 11px）；弹簧色 10 档金蓝渐变、哑火变红
scout 1 明确把「渲染层几何」列为未覆盖，但**这三条是物理量驱动的动画**，落在谁都没盘的那条缝里。
类别：玩家可见（手感/反馈）

**8. 窗口尺寸轮询驱动的横竖屏切换（不靠 bind(size)）**
21207-21216：`ws != self._last_win_size` 时 `layer.apply_orientation()` + `app._apply_orientation()`。13349 注释写明「bind(size) 对程序启动期的 resize 不可靠」。摘要里有 `land-layer`/`land-toeq`，没有这个轮询。
类别：平台适配 + 玩家可见（转屏是否跟手）

**9. 竖井（发射槽）也是「球要落在上面」的承托面，绘制线一律用 `BALL_VIS_R`**
10183-10186：`BALL_VIS_R = BALL_R * BALL_VIEW = 12.6`，井区顶边 / 弹簧上横线 / 底墙绘制上沿都从它派生；写死 `BALL_R` 会让球陷进地面 3.6px（玩家截图报过）。
类别：玩家可见（像素级穿帮）+ silent-invariant

**10. 彩蛋那发的落定目标必须是 `PLUNGER_X`，不能按槽中心算**
21284-21299：竖井在场区右边、隔着一道墙，按 x 算出的槽中心在墙左边 ⇒ landing 弹簧会把球**拽穿隔墙**并嵌在墙里定格（玩家 2026-09-18 报的「球压在隔墙上」）。摘要 `easter-flow` 没写这条。
类别：玩家可见（bug 回归）+ silent-invariant

**11. 卡片式「未中/中奖」大字的上浮 + GPU Scale + PopMatrix 夹住**
12351-12435：`+payout`/`未中` 两态、`cy_logical = CH/2-80`（未中）vs `TEXT_CY_WIN`（中奖），白字图集路线 vs Label 回退。摘要 `big-result-text` 只提了 life 与字号规则。
类别：玩家可见

**12. 投注档配色 / 槽位文字字色规则**
10190-10226：`slot_color`（m=0 空槽 `#2a3550`，否则 WoW 品质色）、`slot_txt`（**x100 用黑字**，对比度 2.3 太低会糊）、`_SLOT_TXT_TEX` 缓存（键含字号，超 64 条 clear）。摘要 `slot-tex-cache` 只写了「贴图缓存与配色」。
类别：玩家可见（可读性）

**13. `--landscape` 桌面模拟横屏 + 桌面窗口尺寸 `(1740,1000)`**
11460（`land = (w > h and (platform == "android" or "--landscape" in sys.argv))`）、21863（`Window.size = (1740,1000) if "--landscape" in sys.argv else (540,960)`）。摘要只有一句「cli:命令行开关(桌面/调试)」。
类别：平台适配 / dev-tool

## B. 静默不变量（8 条）

**14. 主频采样线程**必须**在 `sched_setaffinity` 之前起**
2016-2035 的 docstring：`os.sched_setaffinity` 只改调用线程、**新线程继承创建者亲和性** ⇒ 采样线程若在锁核之后起，会跟被测线程抢核。所以用「先起线程、后填窗口」的可变容器 `win`。摘要 `bench-cpu-pin` / `freq-pipeline` 都没有这条顺序。
类别：silent-invariant（删了分数静默变差，不报错）

**15. `_FREQ_GATE` 频率采样门（样本间隔的睡眠段不进平均）**
1400 定义、1732/1774-1780（`benchmark_trajectories` 里 `finally` 必须把门开回来）、2050-2052（采样线程关门期间直接 return）。摘要提过 `benchmark_trajectories`，没提这个**跨线程的进程级闸**。
类别：silent-invariant

**16. `_save_config` 的落盘降级 + 切后台必须 `_cfg_flush(timeout=1.0)`**
11178-11245（`_CFG_PENDING` 单槽最新赢 / `_CFG_WORKER` / `_cfg_flush` 「绝不长等，超时就放弃」）、20607-20626（`_cfg_post` 失败则**同步写**兜底）、22108-22118（`on_pause` 里 flush）。scout 7 自己在 uncovered 里承认「`_cfg_post` 工作线程实现未读」。
类别：silent-invariant

**17. 装杯震动的时长/振幅映射（10~18ms / 80~220）与「节流只有一个真源」**
11244-11262：`g = (gain-0.20)/0.55`，`_vibrate(10+8g, 80+140g)`；注释明写**不在此判时间**，节流真源是 `WinPileFX._bounce` 里 `sfx.play` 的返回值（两边各判一次必然漂移 = 「听到响但手上没感觉」）。摘要 `win-pile-bounce-vib` 只说「同一次节流判定」。
类别：silent-invariant + 玩家可见

**18. `_result_until` 之外，`_charge_topped` / `_last_charge_sound` 的满蓄力节流**
20903-20914：满蓄力后每 `CHARGE_HOLD_SEC=0.60s` 一声 `charge_full`，且棘轮音间隔 `0.25 - 0.18*power`。摘要 `sfx-charge-full` 有，节流参数与递进规则没有。
类别：玩家可见（听觉）

**19. 齿轮状隐藏规则：`_TINT_BRIGHT` / `_TINT_DIM` 必须排在 `_tint_from` 之后**
12761-12763 注释：「**必须排在 `_tint_from` 之后** —— 它是模块级调用, 放前面会 NameError(第一版就踩了)」。同类还有 864 行 `dim_rgb` 的位置注释。这类**模块级求值顺序**是典型删了才炸的静默不变量。
类别：silent-invariant

**20. `_SINCE_LAUNCH` 的归零点与自增点在两个不同函数里**
4587 定义；20092 在 `launch()` 里归零；21091 在 `_frame_timed` 里 +1；16191-16192 注释把它列为一次真机误判的根因（判 C6「最差帧是不是紧跟在发射后」）。纯开发用，但删了/挪了会让诊断面板静默说谎。
类别：dev-tool + silent-invariant

**21. `_update_slots` 与 `_redraw` 的「同义动作」必须一起搬**
12211-12226：增量更新路径里**必须**同时做两件事——`self._pulse = None`（作废槽位白闪）与 `lamps_off()`（熄灭投中灯）。漏掉灯的后果摘要写过（`lamps-pulse`），但「两处同义、改一处必须改另一处」这条结构约束没写全。
类别：silent-invariant

## C. 平台适配（5 条）

**22. `PLINKO_SFX_BACKEND` 环境变量覆盖后端选择**
4459-4474：`none` ⇒ 完全无后端；`kivy`/`winmm` ⇒ 跳过 SoundPool。这是**出货包里就带着的**运行时开关（非 buildozer 开关）。13 份摘要里一个字都没有。
类别：平台适配 / dev-tool

**23. 桌面 `--smoke` 夹具本身（含 `Window.screenshot`）**
22147-22331：建窗 → 蓄力 → **截图 8 张**到 `tempfile.gettempdir()/plinko_smoke`（22155）→ 必中盘 → 跑分中中奖/彩蛋断言 → `when_ready`/`when_revealed` 轮询夹具（含「等超时也要打日志，别给会骗人的绿」）。摘要只有「--smoke 的断言只读了部分」。
类别：dev-tool

**24. `_device_info()` 的三级降级 + `_build_info()` 取 `main.py` mtime**
16695-16745：安卓走 `Build.MODEL/VERSION.RELEASE` → 失败退 `subprocess getprop` → 再失败退 `'Android 设备'`；桌面走 `platform.node()/system()/python_version()`。`_build_info` 用文件 mtime 反推制作时刻，并显式拒绝未来时间戳。scout 7 自分承认这两个未读。
类别：平台适配 + dev-tool

**25. 高刷请求的三重上限与 `_FPS_INFO` 回读**
5692-5701 / 5713 / 5733-5800 / 5894-5930：`min(屏幕支持, Android 系统设定, 用户档位)`；读不到系统设置时**不当作 60Hz**，让系统拒绝后由「屏幕 Hz」回读真值。摘要 `fps-request` 只提了「高刷请求与帧率上限」这个名。
类别：平台适配

**26. 闸门/探针降级链每一级都要把异常原文记进 `_BACKEND_ERRORS`**
4441-4474 + 6494-6535：注释明写「一块专门用来抓静默的面板, 自己却在做静默降级」是**真实事故**；面板要能印出「安卓上应为 SoundPool，静默降级了」+ 构造失败原文。摘要 `backends-degrade-chain` 有链，没有这条「每级必须留证」的判据。
类别：silent-invariant + 平台适配

## D. 开发/验收工具链里被漏掉的（4 条）

**27. 逐帧日志「复制到剪贴板」这条路**
17848-17876 `_copy_bench_log`：`from kivy.core.clipboard import Clipboard`；注释记录了对抗性审查实测出的一个真 bug（`return False` 必须在 `btn is not None` **外面**，否则无参调用会谎报「已复制到剪贴板」而剪贴板是空的）。摘要 `log-export` 只写了「三类日志导出 txt + 降级链」。
类别：dev-tool（但`btn` 文案是玩家可见的反馈）

**28. `_bench_hz_tick`：采样期每 0.5s 记「当时在演什么 + 屏幕刷新率」**
4534（`_BENCH_HZ`）、15948-15952（起 tick）、16255-16269（每 0.5s 一次 JNI，采样结束**自己 return False 摘掉**）、17384-17390（面板判「刷新率变过没有」）。摘要 `render-metrics` / `jank-thresholds` 都没它。
类别：dev-tool

**29. 启动日志的「主线程心跳」（只写不改的 12 秒窗口）**
21097-21114：`_hb_t/_hb_n`，只在 `_load_veil is not None` 或启动 12 秒内记；目的是把「探针 3 秒里主线程被卡住」与「预热自己停了」分开。摘要 `boot-log` 只说「启动加载日志记录器」。
类别：dev-tool

**30. `selftest()` 的 docstring 是陈旧的（写「预定槽/引导飞行」）**
7113：「(2) 引导飞行落点=预定槽、不卡死」——彻底被动重构后**已无预定槽**，而下面 (2) 的实际判据是「升过通道顶→越顶入场→落袋」。重写时若照 docstring 抄会复活一个已被删除的机制。
类别：doc-vs-code drift（不是行为，但会误导重写）

**31. 归零计数的健壮性清单：`_PROBE_COST` / `_PIN_STAT` / `_FS_MUTED_N` / `_GLYPH_MISS` 等「必须印进日志、不许静默」的账本**
代表性位置：4726-4741（`_PIN_POPUP_SKIP` / `_PIN_STAT` / `_PIN_WHERE` / `_PIN_CAND`）、10287-10290（`_FS_MUTE_UNTIL` / `_FS_MUTED_N`）、10675（`_GLYPH_MISS`）、4457（`_BACKEND_ERRORS`）、6041（`n_missed`）。scout 6 提过 `_FS_MUTE_UNTIL`/`_POPUP_N` 两个，但**「每个静默路径都必须有一格计数并落到日志」是本工程的一条统一规格**，散落地图（谁归零、谁读、印在哪一行）没人盘。
类别：silent-invariant（诊断死了而没有任何报错）

---

## 我另外确认过的、**不算**遗漏的（避免你重复劳动）

- `_device_is_wide` / `_land_angle` / `_land_layer`（11397-11434）——scout 9 的 `orient-split`/`land-angle` 已覆盖。
- `RotPopup._align_center`（11598-11615）看着像未定义符号，实为 **Kivy `ModalView._align_center`** 的继承（本机 kivy 2.3.1 `modalview.py:253` 有定义），不是 bug；`layer.bind(on_keyboard=…)` 对不存在的 event 是 Kivy 的静默无害路径。
- `main()` 只解析 `--nosound/--selftest/--smoke`（22333-22347）、`KIVY_NO_ARGS` / `KIVY_ORIENTATION`（9、13）、`Config.set(graphics,maxfps/vsync)`（17-18）——scout 9 已覆盖。
- `_vibrate_double`、`_thermal_probe`、`_pwr_*`、`_cpufreq_*`、`_brk_wrap`、`_gc.freeze()`、`_PREBAKE_DONE` 预热链——scout 8/9 已覆盖（细节深浅不一）。
- `JITTER`(606) / `PEG_GLANCE_UP` / `ARC_E`(617) 确认为死常量（与 scout 1 的 grep 结论一致，我复核了引用行）。

**我对主代理的提醒**：本清单里最危险的两条是 **#1（字体子集）** 和 **#14（采样线程必须先于锁核起）** ——前者是「重写时任何新文案都可能真机变方块、而所有桌面验证手段都测不出来」，后者是「顺序一改，跑分数字静默变差且归因困难」。两条都只在注释里，且都不在任何摘要中。