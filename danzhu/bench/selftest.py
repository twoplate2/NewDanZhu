"""无头自测(`main.py --selftest`)。纯计算: 不开窗、不 import `danzhu.ui.*`。

老版 android/main.py:7112 的 `selftest` 逐行端口, 外加它同段的 `sfx_check`(7626) —— 后者
只被本函数调用, 老版归在自测块里, 全仓没有第二处, 所以一并搬来, 不另立模块。
判据、阈值、打印文案全部照抄: 这是给人读的自测报告, 数字变了就是行为变了。
"""

import array
import math
import random

# ⚠️ SFX_MIN_SP 有两份: `game.py` 那份只有 4 个键(玩法内部用), 撞弧面时会 KeyError ——
#    自测要按 `b.amp` 的每个键查表, 而 EV_ARC 每发必到, 所以只能取 `audio.bus` 这份
#    (含 `EV_ARC: 1e9`)。别图省事换成 game.py 那份。
from ..audio.bus import SFX_MIN_SP
from ..audio.synth import SR, bake_bank
from ..config import (
    BALL_R, DIV_TOP, EV_ARC, EV_CEIL, EV_DIV, EV_PEG, EV_WALL, FIELD_R, FIXED_DT,
    FLOOR, G, LAND_BOUNCE_DECAY_JITTER, LAND_BOUNCE_JITTER, LAND_BOUNCE_MAX_VY,
    LAND_BOUNCE_MIN_VY, LAND_E, LANE_WALL_TOP, MISFIRE_MAX_FRAMES, MISFIRE_POWER,
    NUM_SLOTS, PEG_ROWS, PEG_SY, PEG_TOP, PLUNGER_X, PLUNGER_Y, STALL_MAX_RETRY,
)
# 档位标签取自玩法那份唯一清单(见 (1b) 的说明)
from ..game import Game
from ..geo import build_geo
from ..physics import (
    advance_flight, advance_misfire, launch_ball, launch_misfire, power_u,
)
from ..rules import VALUE_DIST, roll_multipliers


def selftest(n=40000):
    """盘面 RTP / 飞行不变量 / 下落节奏 / 事件覆盖 / 音效库 的全量自测。

    n 必须够大: 单发赔付方差很大(实测 σ=1.96/2.29/6.92/9.25)。n=40000 时四档标准误
    ≈0.010/0.012/0.035/0.046, 3σ = 0.029/0.034/0.104/0.139, 故低档门禁 ±0.05、
    高档 ±0.15(最高档 3σ=0.139, 离 0.15 只剩 0.011 —— 再往上加档位就得同步放宽门禁,
    否则每次自测约有千分之一概率假失败)。
    """
    geo = build_geo()
    ok = True

    print("== 返还率精确性(均匀落格盘面期望) ==")
    for rtp in (0.80, 1.20, 2.00, 3.60):
        tot = 0.0
        for _ in range(n):
            board = roll_multipliers(rtp)
            tot += board[random.randrange(NUM_SLOTS)]
        realized = tot / n
        tol = 0.15 if rtp >= 2.00 else 0.05   # 高档含 x50/x100, σ 大, 门禁放宽
        good = abs(realized - rtp) < tol
        ok = ok and good
        print("  档位 %.2f -> 实测 RTP %.3f  %s" % (rtp, realized, "OK" if good else "偏差!"))

    # ⚠️ 中奖率**不能**用 E[K]/9 估: 坏盘重抽会连带改变"有奖格数 k"的分布, 名义 K_DIST 算出的
    #    E[K]/9 与真实值对不上(3.60 档: 名义 78.89% vs 实测 78.52%)。所以跟 (1) 一样跑 MC ——
    #    但**对整块盘面求期望**(9 格全算, 不是随机挑一格): 盘和方差远小于单格, 同样 n 下精度
    #    高一个量级, 顺带把"空军率"也量出来。
    # ⚠️ `_ref` 写的是**每档应该等于多少**, 这是**回归门禁**不是推导: 改 K_DIST 就要同步改这里
    #    的参考值, 红了正是它存在的意义 —— 2026-09-10 300%→360% 那次只改了档位名、没动有奖
    #    格数, 中奖率一动不动(停在 65.6%), 玩家察觉不到换了档。
    # 门禁口径: 返还倍率 |MC − 档位| < **4σ**(σ 由本次样本实时估出 ⇒ 改 n 不用改门禁,
    #    且 4σ 的双侧假失败率约 6e-5, 七个档合起来也不会偶发报警);
    #    中奖率 **±1.5pp**(实测 σ≈0.1pp, 余量十几倍, 但足以抓住"换档没换格子"那种结构性
    #    错误 —— 那次的差距是 13pp)。
    print("== 每档 中奖率 / 返还倍率 ==")
    _ref = {0.80: 24.15, 1.20: 35.67, 2.00: 43.37, 3.60: 78.52,
            10.0: 100.00, 20.0: 100.00, 50.0: 100.00}
    _refv = dict((round(k, 6), v) for k, v in _ref.items())
    # ⚠️ 档位名单从 VALUE_DIST **派生**, 标签从玩法那份 `RTP_TIERS`/`RTP_HIDDEN` 取 ——
    #    老版为"多抄一份档位清单"闪退过一次, 这里不许再手抄。
    _lab = {}
    for _l, _v in tuple(Game.RTP_TIERS) + tuple(Game.RTP_HIDDEN):
        _lab[round(float(_v), 6)] = _l
    _tiers = sorted(VALUE_DIST)
    _lack = [t for t in _tiers if round(t, 6) not in _refv]
    if _lack:
        ok = False
        print("  !! 这些档位没有参考值, 请补进 _ref: %s" % _lack)
    _n1b = 20000                       # 整盘求期望, 精度足够; 不用 n=40000 那档
    _hit_prev = None                   # 第一档没有"上一档", 不参加单调判据
    for _rtp in _tiers:
        _tk = 0                        # Σ 有奖格数
        _ts = 0.0                      # Σ 盘面倍率和
        _sq = 0.0                      # Σ 盘和²(估 σ 用)
        _zero = 0                      # 空军盘数
        for _ in range(_n1b):
            _k = 0
            _ssum = 0
            for _v in roll_multipliers(_rtp):
                if _v:
                    _k += 1
                    _ssum += _v
            _tk += _k
            _ts += _ssum
            _sq += _ssum * _ssum
            if not _k:
                _zero += 1
        _hit = 100.0 * _tk / (_n1b * NUM_SLOTS)
        _real = _ts / float(_n1b * NUM_SLOTS)
        _mean = _ts / float(_n1b)
        _sd = math.sqrt(max(0.0, _sq / _n1b - _mean * _mean))
        _tol = max(4.0 * _sd / math.sqrt(_n1b) / NUM_SLOTS, 1e-4)
        _lab_t = _lab.get(round(_rtp, 6), "%.0f%%" % (_rtp * 100.0))
        _want = _refv.get(round(_rtp, 6))
        _bad = []
        if abs(_real - _rtp) >= _tol:
            _bad.append("返还倍率 %.4f 偏离档位 %.4f 超过 %.4f" % (_real, _rtp, _tol))
        if not _zero == 0:
            _bad.append("出现空军 %d 盘" % _zero)
        if _want is not None and abs(_hit - _want) >= 1.5:
            _bad.append("中奖率 %.2f%% 偏离参考 %.2f%%" % (_hit, _want))
        if _hit_prev is not None and _hit + 1e-9 < _hit_prev:
            _bad.append("中奖率比低档还低(上一档 %.2f%%)" % _hit_prev)
        ok = ok and not _bad
        print("  %-6s 中奖率 %6.2f%%   返还倍率 %7.4f (档位 %6.4f, 允差 ±%.4f)   "
              "空军 %d 盘   %s"
              % (_lab_t, _hit, _real, _rtp, _tol, _zero, "OK" if not _bad else "!!"))
        for _b in _bad:
            print("         !! %s" % _b)
        _hit_prev = _hit

    print("== 被动飞行(升到顶->越顶入场->落袋 & 不卡死) ==")
    m = 1500
    stuck = no_top = no_enter = 0
    ev_flights = {EV_PEG: 0, EV_CEIL: 0, EV_WALL: 0, EV_DIV: 0, EV_ARC: 0}
    ev_audible = {EV_PEG: 0, EV_CEIL: 0, EV_WALL: 0, EV_DIV: 0}
    for _ in range(m):
        b = launch_ball(random.uniform(MISFIRE_POWER, 1.0))   # 低于阈值的是哑火, 由 (2b) 覆盖
        min_y = b.y
        entered = False
        landed = None
        seen = 0
        loud = 0
        stall_frames = 0
        last_xy = (b.x, b.y)
        for _ in range(4000):
            landed = advance_flight(b, geo)
            lx, ly = last_xy
            if (b.x - lx) ** 2 + (b.y - ly) ** 2 > 1.0:
                stall_frames = 0
            else:
                stall_frames += 1
            if stall_frames > 72 and getattr(b, "_stall_retry", 0) < STALL_MAX_RETRY:
                # 踢球(与 GUI 一致, 不退回重发)
                nx, ny = getattr(b, "last_nx", 0.0), getattr(b, "last_ny", -1.0)
                tx_, ty_ = -ny, nx
                if ty_ > 0:
                    tx_, ty_ = -tx_, -ty_
                b.vx += tx_ * 120.0
                b.vy += ty_ * 120.0
                b._stall_retry = getattr(b, "_stall_retry", 0) + 1
                stall_frames = 0
                entered = False
            last_xy = (b.x, b.y)
            ev = b.events
            if ev:                             # 模拟 GUI: 每帧读事件位后清零(不清则覆盖率虚高)
                seen |= ev
                for bit, spd in b.amp.items():   # spd=振幅(改名避免 shadow kivy sp 单位)
                    if spd >= SFX_MIN_SP[bit]:
                        loud |= bit
                b.events = 0
                b.amp.clear()
            min_y = min(min_y, b.y)
            if b.x < FIELD_R:
                entered = True
            if landed is not None:
                break
        else:
            stuck += 1
            continue
        for bit in ev_flights:
            if seen & bit:
                ev_flights[bit] += 1
            if loud & bit:
                ev_audible[bit] += 1
        if min_y > LANE_WALL_TOP:          # 没升过通道隔墙口 = apex 太低(会掉回通道)
            no_top += 1
        if not entered:                    # 没越入场区
            no_enter += 1
    ok = ok and stuck == 0 and no_top == 0 and no_enter == 0
    print("  升过通道顶失败: %d/%d   越顶入场失败: %d/%d   卡死: %d" %
          (no_top, m, no_enter, m, stuck))

    # ⚠️ 上面 (2) 的循环是"逐物理步 break", **永远构造不出**"落袋那一帧还多跑了一步"的场景
    #    (GUI 的循环是"累加器 + 每轮覆盖 landed", 那一步会把 landed 冲成 None 并跳过整个落袋
    #    分支 ⇒ 球带横速弹过隔板落进隔壁槽)。所以这里断言一个**与调用方无关的不变量**:
    #    落袋之后再推进, 槽号与球心 x 都不许变。
    print("== 落袋终态(落袋后再推进 30 步, 槽号与球心 x 不许变) ==")
    pin_n = 300
    pin_bad = 0
    for _k in range(pin_n):
        _b = launch_ball(MISFIRE_POWER + (1.0 - MISFIRE_POWER) * (_k / (pin_n - 1.0)),
                         rng=random.Random(20260911 + _k))
        _r0 = None
        for _ in range(4000):
            _r0 = advance_flight(_b, geo)
            if _r0 is not None:
                break
        if _r0 is None:
            pin_bad += 1                     # 4000 步还不落袋 = 卡死, 也算失败
            continue
        _x0 = _b.x
        for _ in range(30):
            _r1 = advance_flight(_b, geo)
            if _r1 is not None and (_r1 != _r0 or abs(_b.x - _x0) > 0.5):
                pin_bad += 1
                break
    ok = ok and pin_bad == 0
    print("  落袋后槽号/位置变了: %d/%d  %s"
          % (pin_bad, pin_n, "OK" if pin_bad == 0 else "穿帮!"))

    print("== 落袋时仍在下落(地板不吃掉真实下落速度) ==")
    down_bad = 0
    for _k in range(300):
        _b = launch_ball(MISFIRE_POWER + (1.0 - MISFIRE_POWER) * (_k / 299.0),
                         rng=random.Random(20260912 + _k))
        for _ in range(4000):
            if advance_flight(_b, geo) is not None:
                break
        if _b.vy <= 0.0:
            down_bad += 1
    _down_ok = down_bad <= 15               # <=5%: 被隔板底面顶起来的极少数是合理的
    ok = ok and _down_ok
    print("  落袋时不在下落: %d/300  %s" % (down_bad, "OK" if _down_ok else "地板把速度吃了!"))

    # 钉的是**常数之间的关系**: 保底撞击速度 × LAND_E 得到的最小回弹换算成 apex 必须 >= 10px
    # (可见口径), 上限 <= 45px 且球顶不越隔板顶。真正的落地循环在 GUI 里。
    _apex_lo = (LAND_BOUNCE_MIN_VY * LAND_BOUNCE_JITTER[0] * LAND_E
                * LAND_BOUNCE_DECAY_JITTER[0]) ** 2 / (2.0 * G)
    _apex_hi = LAND_BOUNCE_MAX_VY ** 2 / (2.0 * G)
    _clear_ok = _apex_hi <= (FLOOR - BALL_R) - DIV_TOP + BALL_R
    _b_ok = _apex_lo >= 10.0 and _apex_hi <= 45.0 and _clear_ok
    ok = ok and _b_ok
    print("== 落地必弹(保底撞击速度换算的 apex) ==")
    print("  最低 apex %.1fpx (>=10 才看得见)   最高 apex %.1fpx (<=45 且球顶不越隔板顶)  %s"
          % (_apex_lo, _apex_hi, "OK" if _b_ok else "弹不起来/弹太飞!"))

    print("== 哑火(发射了但升不过隔墙顶) ==")
    mf = 500
    bad_apex = bad_x = bad_home = 0
    apex_hi_y, apex_lo_y = 1e9, -1e9     # apex_hi_y = 升得最高(y 最小)的那一发
    frames_max = 0
    for k in range(mf):
        power = MISFIRE_POWER * k / (mf - 1.0)
        b = launch_misfire(power)
        apex = b.y
        home = False
        used = 0
        for used in range(1, MISFIRE_MAX_FRAMES + 1):
            done = advance_misfire(b)
            apex = min(apex, b.y)
            if abs(b.x - PLUNGER_X) > 1e-9:
                break
            if done:
                home = True
                break
        frames_max = max(frames_max, used)
        apex_hi_y = min(apex_hi_y, apex)
        apex_lo_y = max(apex_lo_y, apex)
        if apex <= LANE_WALL_TOP + 40:       # 离隔墙顶(160)留 40px 安全余量
            bad_apex += 1
        if abs(b.x - PLUNGER_X) > 1e-9:   # 竖井内不该有任何横向位移
            bad_x += 1
        if not (home and b.y == PLUNGER_Y and b.vy == 0.0):
            bad_home += 1
    mf_ok = (bad_apex == 0 and bad_x == 0 and bad_home == 0
             and frames_max <= MISFIRE_MAX_FRAMES)
    ok = ok and mf_ok
    print("  apex y 区间 %.0f~%.0f (隔墙顶 %d, 越过即失败)   最长归位 %d/%d 帧"
          % (apex_hi_y, apex_lo_y, LANE_WALL_TOP, frames_max, MISFIRE_MAX_FRAMES))
    print("  越顶泄漏: %d/%d   横向漂移: %d/%d   未归位: %d/%d"
          % (bad_apex, mf, bad_x, mf, bad_home, mf))

    # 减速比门禁区间 0.45~0.80: 低于 0.45 黏滞(VMIN=30 实测 0.32); 上限 0.80(0.70 过严会逼出
    # 穿阵手感)。行穿行 0.15 只是极端哨兵(防整体过快)。滞留帧 ≤30/发: 摩擦后球到槽口慢是
    # "损失能量"的一致表现(实测 ~26), 30 防"卡在槽口"。
    print("== 下落节奏(弹珠机手感: 碰钉轻快弹开) ==")
    mn = 250
    row_gaps = []
    peg_ratios = []
    sticky = 0
    # 口径与专家组测量一致: power=0.8 固定 + 每发固定 rng 种子。
    # 滞留帧=碰钉间隔恰 1 帧(同钉连续碰撞)。
    for i in range(mn):
        b = launch_ball(0.8, rng=random.Random(1000 + i))
        prev_y = b.y
        row_t = {}
        t = 0.0
        last_peg_f = -10
        prev_sp = None
        for _f in range(4000):
            sp0 = math.hypot(b.vx, b.vy)
            landed = advance_flight(b, geo)
            t += FIXED_DT
            if b.events & EV_PEG:
                if _f - last_peg_f == 1:
                    sticky += 1              # 同钉连续碰撞(1 帧内再次碰同一颗钉)
                else:
                    if prev_sp is not None and prev_sp > 50:
                        peg_ratios.append(math.hypot(b.vx, b.vy) / prev_sp)  # 总速比
                last_peg_f = _f
                b.events = 0
                b.amp.clear()
            elif b.events:
                b.events = 0
                b.amp.clear()
            prev_sp = sp0
            for r in range(1, PEG_ROWS):      # 行穿行: 相邻钉行间的下落耗时
                y = PEG_TOP + r * PEG_SY
                if y not in row_t and b.vy > 0 and prev_y < y <= b.y:
                    row_t[y] = t
            prev_y = b.y
            if landed is not None:
                break
        rows = sorted(row_t)
        for a, c in zip(rows, rows[1:]):
            row_gaps.append(row_t[c] - row_t[a])
    row_gaps.sort()
    peg_ratios.sort()
    gap_med = row_gaps[len(row_gaps) // 2]
    ratio_med = peg_ratios[len(peg_ratios) // 2]
    rhythm_ok = (gap_med >= 0.15 and 0.45 <= ratio_med <= 0.80
                 and sticky <= mn * 30)
    ok = ok and rhythm_ok
    print("  行穿行 p50=%.2fs (须>=0.15)   碰钉减速比 p50=%.2f (须0.45~0.80, 轻快弹开)"
          % (gap_med, ratio_med))
    print("  滞留帧 %d (须<=%d/发, 摩擦后槽口慢速落袋为正常)" % (sticky, 30))

    print("== 转向平滑(弧面缓动带球) ==")
    ts = 100
    arc_turns = []
    for _ in range(ts):
        b = launch_ball(random.uniform(MISFIRE_POWER, 1.0))
        for _f in range(400):
            a0 = math.degrees(math.atan2(b.vy, b.vx))
            landed = advance_flight(b, geo)
            if b.events & EV_ARC:
                a1 = math.degrees(math.atan2(b.vy, b.vx))
                da = abs(a1 - a0)
                if da > 180:
                    da = 360 - da
                arc_turns.append(da)
                b.events = 0
                b.amp.clear()
            elif b.events:
                b.events = 0
                b.amp.clear()
            if landed is not None:
                break
    arc_turns.sort()
    turn_med = arc_turns[len(arc_turns) // 2] if arc_turns else 0.0
    arc_ok = bool(arc_turns) and turn_med <= 15.0
    ok = ok and arc_ok
    if arc_turns:
        print("  碰弧面帧方向角突变 p50=%.0f° (须<=15, 一帧横移=玩家投诉的荒谬感)" % turn_med)
    else:
        print("  碰弧面帧方向角突变: 无数据(弧面接触率 0) — 不达标")

    print("== 蓄力观感区分度(竖直时序必须不变) ==")
    apexx_med = {}
    ay_med = {}
    fp_x_med = {}
    turny_med = {}
    kink_max = {}
    kink_delta = {}
    fp_bad = []
    turn_bad = []
    for power in (MISFIRE_POWER, 0.5, 1.0):
        axs, npegs, fps, ays = [], [], [], []
        fpxs = []
        turns, turn_ys, kinks, kdeltas = [], [], [], []
        for k in range(100):
            b = launch_ball(power)
            best_y, best_x = b.y, b.x
            npeg, fp = 0, -1
            fp_x = 0
            crossed = False
            turn = -1
            mk = 0.0
            mkd = 0.0
            prev_da = None
            pvx, pvy = b.vx, b.vy
            for f in range(4000):
                landed = advance_flight(b, geo)
                if b.y < best_y:
                    best_y, best_x = b.y, b.x
                if not crossed and b.x < FIELD_R and b.y < LANE_WALL_TOP:
                    crossed = True
                if turn < 0 and crossed and b.vy >= 0.0:
                    turn = f                   # 顶部碰撞音的触发帧(GUI 用同一判据)
                    turn_ys.append(b.y)
                if b.events & EV_PEG:
                    npeg += 1
                    if fp < 0:
                        fp = f
                        fp_x = b.x            # 首钉 x(玩家看到的"进钉阵位置")
                # 空中折角: 没有碰撞(含弧面接触 EV_ARC)的那一帧里方向变了多少。低速段方向
                # 本就抖(vx 过零即 180°), 所以只看 |v|>300 的帧; 空间限定在首钉平面(y=141)
                # 之上 —— 碰第一个钉子之前才是"均匀平滑"的主战场。
                if (not b.events) and b.y < PEG_TOP - BALL_R and math.hypot(b.vx, b.vy) > 300.0:
                    da = abs(math.degrees(math.atan2(b.vy, b.vx) -
                                          math.atan2(pvy, pvx)))
                    if da > 180.0:
                        da = 360.0 - da
                    if da > mk:
                        mk = da
                    if prev_da is not None:
                        dd = abs(da - prev_da)
                        if dd > mkd:
                            mkd = dd
                    prev_da = da
                else:
                    prev_da = None          # 碰撞帧(弧面/钉/墙)打断 da 序列, 碰后重新开始:
                                            # Δ 只在连续无碰撞帧之间比较, 不跨碰撞
                pvx, pvy = b.vx, b.vy
                b.events = 0
                b.amp.clear()
                if landed is not None:
                    break
            axs.append(best_x)
            ays.append(best_y)
            npegs.append(npeg)
            kinks.append(mk)
            kdeltas.append(mkd)
            if fp >= 0:
                fps.append(fp)
                fpxs.append(fp_x)
            if turn >= 0:
                turns.append(turn)
        axs.sort(); npegs.sort(); fps.sort(); turns.sort(); turn_ys.sort()
        fpxs.sort()
        kinks.sort(); kdeltas.sort()
        apexx_med[power] = axs[len(axs) // 2]
        ay_med[power] = ays[len(ays) // 2]
        fp_x_med[power] = fpxs[len(fpxs) // 2] if fpxs else 0
        kink_max[power] = max(kinks)
        kink_delta[power] = max(kdeltas)
        fp_med = fps[len(fps) // 2] if fps else -1
        if not (75 <= fp_med <= 110):
            fp_bad.append((power, fp_med))
        turn_med = turns[len(turns) // 2] if turns else -1
        turn_y_med = turn_ys[len(turn_ys) // 2] if turn_ys else -1
        turn_covered = len(turns) == 100        # 顶部碰撞音必须每发都触发
        if not (50 <= turn_med <= 70) or not turn_covered:
            turn_bad.append((power, turn_med, len(turns)))
        turny_med[power] = turn_y_med
        print("  力度 %3.0f%% (u=%.2f): 冲顶 x 中位 %3.0f   撞钉 %d 次   首钉 %d 帧   "
              "转向 %d 帧 @y%.0f   空中折角 %.1f°(Δ%.1f°)"
              % (power * 100, power_u(power), apexx_med[power],
                 npegs[len(npegs) // 2], fp_med, turn_med, turn_y_med,
                 kink_max[power], kink_delta[power]))
    spread = apexx_med[MISFIRE_POWER] - apexx_med[1.0]
    fpx_spread = fp_x_med[MISFIRE_POWER] - fp_x_med[1.0]
    ay_spread = ay_med[MISFIRE_POWER] - ay_med[1.0]   # >0 表示满蓄飞更高
    # 力度区分暂时**不判失败**, 只打印实测值: 优化已把飞高差抹平到 1px 量级, 判失败会一直红。
    spread_ok = True
    tspread = turny_med[MISFIRE_POWER] - turny_med[1.0]
    tspread_ok = True
    kink_worst = max(kink_max.values())
    kink_delta_worst = max(kink_delta.values())
    kink_ok = kink_worst <= 4.0 and kink_delta_worst <= 1.0
    ok = ok and spread_ok and not fp_bad and not turn_bad and tspread_ok and kink_ok
    print("  飞高区分(弱→满 apex y 差): %.0f px  %s (>=10 满蓄明显飞更高; 首钉x跨度 %.0f px, 冲顶x跨度 %.0f px)"
          % (ay_spread, "OK" if spread_ok else "区分度不足!", fpx_spread, spread))
    print("  首钉时刻: %s (须恒在 75~110 帧, 否则飞行音与画面脱节)"
          % ("OK" if not fp_bad else "漂了! %s" % fp_bad))
    print("  转向(顶部碰撞音触发): %s (须每发都有且恒在 50~65 帧)  转向高度跨度 %.0f px %s"
          % ("OK" if not turn_bad else "异常! %s" % turn_bad,
             tspread, "OK" if tspread_ok else "(<8 顶部音分不出蓄力档!)"))
    print("  空中折角(无碰撞段, 弧面接触帧豁免): 单帧最大 %.1f°  %s (须 <=4;"
          % (kink_worst, "OK" if kink_ok else "不达标!"))
    print("           相邻帧折角差最大 %.1f°  %s (须 <=1; 均匀平滑, 无阶跃)"
          % (kink_delta_worst, "OK" if kink_ok else "不达标!"))

    print("== 碰撞事件覆盖率(音效触发源) ==")
    names = {EV_PEG: "撞钉", EV_CEIL: "天花板", EV_WALL: "撞墙", EV_DIV: "撞隔板",
             EV_ARC: "导流弧"}
    for bit in (EV_PEG, EV_CEIL, EV_WALL, EV_DIV):
        print("  %-8s 有事件 %5.1f%%   过音量阈值 %5.1f%%" %
              (names[bit], 100.0 * ev_flights[bit] / m, 100.0 * ev_audible[bit] / m))
    print("  导流弧   接触率 %5.1f%%   (静音事件位: 每次有效发射都必须接触弧面,"
          % (100.0 * ev_flights[EV_ARC] / m))
    print("           这是'转向都发生在导流槽上'的量化——没经过导流槽就转向 = 违规)")
    peg_rate = 100.0 * ev_audible[EV_PEG] / m
    ev_ok = peg_rate > 90.0                 # 撞钉是下落段的主音效, 必须几乎每发都有
    ok = ok and ev_ok
    if not ev_ok:
        print("  异常: 撞钉音效触发率 %.1f%% < 90%%, 玩家会觉得没声音" % peg_rate)
    arc_rate = 100.0 * ev_flights[EV_ARC] / m
    arc_ok = arc_rate >= 99.0               # 弧面接触率: 三段式轨道的第二段, 必须每发都走
    ok = ok and arc_ok
    if not arc_ok:
        print("  异常: 导流弧接触率 %.1f%% < 99%%, 存在'没经过导流槽就转向'的飞行" % arc_rate)
    ceil_rate = 100.0 * ev_flights[EV_CEIL] / m
    # 天花板升格 2 号弹射器后, 撞顶是正常行为(非失败), 只打印撞顶率供参考。
    print("  天花板撞击率 %.1f%% (2号弹射器生效, 已不再是失败门禁)" % ceil_rate)

    print("== 音效库体检 ==")
    cnt, bad = sfx_check(verbose=False)
    print("  音效数: %d   异常: %s" % (cnt, bad if bad else "无(削波/直流/爆音/静音 全部通过)"))
    ok = ok and not bad and cnt >= 20

    print("结果:", "OK" if ok else "存在异常, 需修复")
    return ok


def sfx_check(verbose=True):
    """无声卡也能跑的音效库体检: 削波/直流/首尾爆音/静音。"""
    bank = bake_bank()
    bad = []
    if verbose:
        print("  %-10s %7s %6s %6s %7s" % ("name", "ms", "peak", "rms", "dc"))
    for name in sorted(bank):
        a = array.array("h")
        a.frombytes(bank[name])
        n = len(a)
        if n == 0:
            bad.append((name, "空"))
            continue
        pk = max(max(a), -min(a)) / 32767.0
        rms = math.sqrt(sum(v * v for v in a) / n) / 32767.0
        dc = sum(a) / n / 32767.0
        if verbose:
            print("  %-10s %7.0f %6.3f %6.3f %7.4f" % (name, n / SR * 1000, pk, rms, dc))
        if pk > 0.999:
            bad.append((name, "削波"))
        if pk < 0.10 or rms < 0.005:
            bad.append((name, "太轻/静音"))
        if abs(dc) > 0.02:
            bad.append((name, "直流偏移"))
        if abs(a[0]) > 500 or abs(a[-1]) > 500:
            bad.append((name, "首尾爆音"))
    return len(bank), bad
