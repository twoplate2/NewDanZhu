"""音效合成: 程序化烘出全部 41 个音效的 16bit 单声道 PCM。

纯 stdlib (math/random/array/struct), 无平台依赖, 可无窗口烘。
音频是**逐位确定**的: 固定种子 + 共用一条随机流 ⇒ 同版本每次烘出的字节相同。
"""

import array
import math
import random
import struct

SR = 22050                   # 采样率
SFX_SEED = 20260727          # 合成固定种子: 每次启动音色一致
SFX_RESULT_LEAD = 0.18       # 结果音(中奖/未中)前置静音: 让入袋声先落地

# ⚠️ 与盘面/落点的随机流隔离(共用会让烘音效扰乱物理序列), 但它**不是合成私有**:
#    播放层选撞钉音色变体抽的也是这条流 ⇒ 烘焙后它的状态参与玩法表现。运行期一律走
#    shared_rng() / pick_peg_variant(), 别 new Random、别 import random —— 另起一条流
#    不报错, 只会让变体序列整体漂移, 而 bank 只存 PCM 的 sha1, 对账**看不见**。
#    别在 bake 之后重 seed, 也别让烘焙与发声并发抽。
_ARNG = random.Random(SFX_SEED)


# ------------------------- 共用流 (合成 + 播放) ---------------------------
def shared_rng():
    """烘焙与播放共用的**唯一**随机流。

    ⚠️ 它会一路活到运行期: 撞钉变体就是从它抽的。播放层另起一条同种子流不会报错,
       只会让变体整体漂移, 而对账只比 PCM 的 sha1, 抓不到 —— 所以出口只有这一个。
    """
    return _ARNG


def pick_peg_variant(t):
    """撞钉音色变体下标 0..5 (老版 impact: clamp(int(t*5.99)+randint(-1,1),0,5))。

    放在这里而不是播放层, 是因为它抽的是 shared_rng(): 与烘焙共用一条流, 分家就会漂移。
    """
    v = int(t * 5.99) + _ARNG.randint(-1, 1)
    return 0 if v < 0 else (5 if v > 5 else v)

NOTE = {"C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23, "G4": 392.00,
        "A4": 440.00, "C5": 523.25, "D5": 587.33, "E5": 659.25, "F5": 698.46,
        "G5": 783.99, "A5": 880.00, "C6": 1046.50, "D6": 1174.66, "E6": 1318.51,
        "G6": 1567.98, "C7": 2093.00}


# ----------------------------- 合成基元 -----------------------------------
def _buf(dur):
    return [0.0] * int(SR * dur)


def _noise(n, lp=0.5):
    """单极点低通白噪声: lp 越小越闷(1.0=白噪, 0.1=低频轰隆)。"""
    out = [0.0] * n
    z = 0.0
    comp = 1.0 / math.sqrt(max(0.02, lp))   # 补偿低通造成的能量损失
    for i in range(n):
        z += lp * (_ARNG.uniform(-1.0, 1.0) - z)
        out[i] = z * comp
    return out


def _add_partials(buf, t0, f0, parts, gain=1.0):
    """叠加指数衰减正弦分音。parts = [(频率倍数, 幅度, 衰减时间常数s)]。"""
    n = len(buf)
    i0 = int(t0 * SR)
    if i0 >= n:
        return
    for mul, amp, tau in parts:
        w = math.tau * f0 * mul / SR
        dec = math.exp(-1.0 / (tau * SR))
        a = amp * gain
        e = 1.0
        for i in range(i0, n):
            buf[i] += a * e * math.sin(w * (i - i0))
            e *= dec
            if e < 1e-4:                     # 衰减到 -80dB 以下, 提前收尾
                break


def _add_chirp(buf, t0, f1, f2, dur, amp, tau, curve=1.0):
    """扫频(弹簧/滑音): f1 -> f2。curve 是幂指数, >1 时前段慢、后段快。"""
    n = len(buf)
    i0 = int(t0 * SR)
    nn = max(2, int(dur * SR))
    dec = math.exp(-1.0 / (tau * SR))
    ph = 0.0
    e = 1.0
    for j in range(nn):
        i = i0 + j
        if i >= n:
            break
        f = f1 + (f2 - f1) * ((j / (nn - 1.0)) ** curve)
        ph += math.tau * f / SR
        buf[i] += amp * e * math.sin(ph)
        e *= dec


def _add_noise(buf, t0, dur, amp, tau, lp=0.5):
    """噪声瞬态(撞击的"咔"/"沙")。"""
    n = len(buf)
    i0 = int(t0 * SR)
    ns = _noise(max(1, int(dur * SR)), lp)
    dec = math.exp(-1.0 / (max(1e-4, tau) * SR))
    e = 1.0
    for j, v in enumerate(ns):
        i = i0 + j
        if i >= n:
            break
        buf[i] += amp * e * v
        e *= dec


def _add_bell(buf, t0, f0, amp=1.0, tau=0.35, bright=1.0):
    """钟/马林巴音色: 谐波分音, 高次衰减更快 -> 温暖不刺耳。"""
    _add_partials(buf, t0, f0, [
        (1.00, 1.00 * amp, tau),
        (2.00, 0.42 * amp * bright, tau * 0.55),
        (3.00, 0.17 * amp * bright, tau * 0.34),
        (4.02, 0.08 * amp * bright, tau * 0.22),
    ])
    _add_noise(buf, t0, 0.003, 0.09 * amp, 0.0012, 0.85)   # 琴槌敲击感


def _reverb(buf, mix=0.20, rt=0.45):
    """极简梳状混响: 给铃声/中奖音一点空间感。"""
    if mix <= 0.0:
        return
    n = len(buf)
    wet = [0.0] * n
    for dl in (0.0231, 0.0297, 0.0371, 0.0411):
        d = int(SR * dl)
        if d >= n:
            continue
        fb = 10.0 ** (-3.0 * d / (SR * rt))
        tmp = [0.0] * n
        for i in range(n):
            v = buf[i]
            if i >= d:
                v += fb * tmp[i - d]
            tmp[i] = v
            wet[i] += v * 0.25
    for i in range(n):
        buf[i] += mix * wet[i]


def _pack(buf, peak=0.6, fi=0.0006, fo=0.005):
    """归一化到 peak + 首尾淡入淡出(防爆音) -> 16bit 单声道 PCM 字节。
    淡入极短(0.6ms)以保住打击瞬态, 淡出较长(5ms)避免尾部断音"哒"。"""
    n = len(buf)
    pk = 0.0
    for v in buf:
        av = -v if v < 0.0 else v
        if av > pk:
            pk = av
    if pk < 1e-9:
        return b"\x00" * (2 * n)
    g = peak / pk
    ni = max(1, int(SR * fi))
    no = max(1, int(SR * fo))
    out = array.array("h", bytes(2 * n))
    for i in range(n):
        v = buf[i] * g
        if i < ni:
            v *= i / ni
        r = n - 1 - i
        if r < no:
            v *= r / no
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        out[i] = int(v * 32767.0)
    return out.tobytes()


def pcm_to_wav(pcm):
    """裸 PCM -> 标准 WAV 容器字节(供 winsound.SND_MEMORY / --dumpwav)。"""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


# ----------------------------- 音效配方 -----------------------------------
def _sfx_tink(f0):
    """撞钉: 明亮金属"叮", 非谐分音 + 极短噪声瞬态。"""
    b = _buf(0.085)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.020),
                               (2.01, 0.45, 0.012),
                               (3.42, 0.20, 0.007)])
    _add_noise(b, 0.0, 0.004, 0.28, 0.0015, 0.60)
    return _pack(b, 0.72)


def _sfx_wall(f0):
    """撞墙: 闷"咚"(塑料/木质), 低频为主。"""
    b = _buf(0.13)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.045),
                               (1.87, 0.30, 0.018),
                               (3.10, 0.12, 0.008)])
    _add_noise(b, 0.0, 0.008, 0.35, 0.004, 0.18)
    return _pack(b, 0.59)


def _sfx_div(f0):
    """撞隔板: 中频"嗒"。"""
    b = _buf(0.105)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.026),
                               (2.31, 0.40, 0.013),
                               (3.91, 0.16, 0.007)])
    _add_noise(b, 0.0, 0.005, 0.30, 0.002, 0.35)
    return _pack(b, 0.65)


def _sfx_rail():
    """天花板金属弧: 钟形"锵", 带一点混响余韵。"""
    b = _buf(0.34)
    _add_partials(b, 0.0, 760.0, [(1.00, 1.00, 0.110),
                                  (2.76, 0.55, 0.070),
                                  (5.40, 0.30, 0.040),
                                  (8.93, 0.15, 0.022)])
    _add_noise(b, 0.0, 0.006, 0.30, 0.003, 0.70)
    _reverb(b, 0.18, 0.35)
    return _pack(b, 0.65)


def _sfx_launch():
    """发射: 柱塞"咔" + 弹簧下滑 boing。"""
    b = _buf(0.28)
    _add_noise(b, 0.000, 0.010, 0.55, 0.005, 0.85)          # 释放咔哒
    _add_chirp(b, 0.005, 640.0, 150.0, 0.11, 0.55, 0.085, 1.4)
    _add_partials(b, 0.005, 152.0, [(1.00, 0.80, 0.16),
                                    (2.40, 0.25, 0.06)])    # 弹簧余振
    return _pack(b, 0.75)


# 飞行音包络: 实测中位速度曲线(归一化), 每 0.1s 一点。
FLIGHT_ENV = [1.00, 0.91, 0.82, 0.72, 0.63, 0.54, 0.45, 0.30,
              0.23, 0.18, 0.18, 0.21, 0.27, 0.32, 0.34, 0.25]
FLIGHT_DUR = 1.50
FLIGHT_GRAIN_END = 0.65      # 颗粒(滚动感)淡出时刻(球此时已碰弧面离开竖井钢轨)


def _sfx_flight():
    """连续飞行音: 窄带共振噪声(球压钢轨的沙沙) + 低频颗粒调制, 铺到首次撞钉。"""
    n = int(SR * FLIGHT_DUR)
    b = [0.0] * n
    seg = (len(FLIGHT_ENV) - 1) / FLIGHT_DUR
    blk = 128                                        # 参数按块更新: 省掉 33k 次 cos/插值, 听不出
    r = 0.93                                         # 谐振器极点半径(带宽≈420Hz)
    rr = r * r
    z1 = z2 = 0.0
    ph = 0.0
    dph = 0.0
    cosw = 1.0
    e = FLIGHT_ENV[0]
    grain = 0.0
    for i in range(n):
        if i % blk == 0:
            t = i / SR
            u = t * seg                              # 包络控制点插值
            k = int(u)
            if k >= len(FLIGHT_ENV) - 1:
                e = FLIGHT_ENV[-1]
            else:
                f = u - k
                e = FLIGHT_ENV[k] * (1.0 - f) + FLIGHT_ENV[k + 1] * f
            cosw = math.cos(math.tau * (330.0 + 560.0 * e) / SR)
            dph = math.tau * (46.0 + 88.0 * e) / SR
            grain = 0.34 * max(0.0, 1.0 - t / FLIGHT_GRAIN_END)
        ph += dph
        y = _ARNG.uniform(-1.0, 1.0) + 2.0 * r * cosw * z1 - rr * z2
        z2 = z1
        z1 = y
        b[i] = y * (1.0 - r) * (e ** 1.2) * (1.0 - grain + grain * math.sin(ph))
    _add_chirp(b, 0.00, 150.0, 96.0, 0.34, 0.10, 0.26, 1.0)  # 竖井内的低频管腔感
    return _pack(b, 0.57, fi=0.004, fo=0.110)


def _sfx_top(hard):
    """球冲到顶点转向: 顶部一声碰撞。hard=1 更亮、余韵更长(接近满蓄力那档)。"""
    b = _buf(0.17 if hard else 0.13)
    f0 = 610.0 if hard else 520.0
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.048 if hard else 0.036),
                               (2.13, 0.55, 0.026),
                               (3.79, 0.26, 0.013),
                               (6.11, 0.11, 0.007)])
    _add_noise(b, 0.0, 0.007, 0.36, 0.0028, 0.72)
    _reverb(b, 0.10, 0.22)
    return _pack(b, 0.78 if hard else 0.52)


def _sfx_ratchet(lev):
    """蓄力棘轮: lev 0..5, 越高越亮越响(配合间隔变密 = 越蓄越急)。"""
    b = _buf(0.05)
    _add_noise(b, 0.0, 0.003, 0.45, 0.0012, 0.70)
    _add_partials(b, 0.0, 380.0 + lev * 98.0, [(1.00, 1.00, 0.010),
                                               (2.70, 0.52, 0.005)])
    return _pack(b, 0.6 + lev * 0.032)


def _sfx_charge_full():
    """满蓄力"顶到底": 弹簧压实的闷响 + 一声高音扣锁。"""
    b = _buf(0.19)
    _add_partials(b, 0.000, 178.0, [(1.00, 1.00, 0.050), (2.05, 0.30, 0.018)])
    _add_noise(b, 0.000, 0.009, 0.40, 0.004, 0.30)
    _add_partials(b, 0.014, 1260.0, [(1.00, 0.45, 0.020), (2.02, 0.18, 0.010)])
    return _pack(b, 0.6)


def _sfx_pocket():
    """入袋确认: 深长闷响 + 金属锁扣"咔哒"。"""
    b = _buf(0.24)
    _add_partials(b, 0.0, 120.0, [(1.00, 1.00, 0.085), (2.03, 0.25, 0.028)])
    _add_noise(b, 0.0, 0.010, 0.30, 0.008, 0.15)            # 闷噪声: "沉进去"不是"撞"
    _add_partials(b, 0.006, 1480.0, [                        # 金属锁扣(非谐=tink 同款"叮")
        (1.00, 0.30, 0.010),
        (2.01, 0.18, 0.006),
        (3.42, 0.09, 0.004),
    ])
    _add_noise(b, 0.006, 0.003, 0.18, 0.0012, 0.75)         # 锁扣的清脆瞬态
    return _pack(b, 0.74)


def _sfx_bounce():
    """落地弹跳: 钢珠撞槽底, 短亮带金属"叮"瞬态。"""
    b = _buf(0.11)
    _add_partials(b, 0.0, 180.0, [(1.00, 1.00, 0.026), (2.05, 0.30, 0.011)])
    _add_partials(b, 0.0, 3200.0, [(1.00, 0.10, 0.008)])    # 金属"叮"(钢珠指纹)
    _add_noise(b, 0.0, 0.005, 0.30, 0.0022, 0.45)           # 亮噪声攻击瞬态
    return _pack(b, 0.50)


def _sfx_riser():
    """入袋前铺垫: 球穿出最后一排钉进无钉区时响。下行滑音, 尾音接进落地 thud。"""
    n = int(SR * 0.16)
    b = [0.0] * n
    ph = 0.0
    for i in range(n):
        t = i / (n - 1.0)
        f = 520.0 - 380.0 * (t ** 1.5)               # 520 -> 140Hz 下行
        ph += math.tau * f / SR
        env = t ** 1.5                                # 渐强
        b[i] = (math.sin(ph) + 0.15 * math.sin(2.03 * ph)) * env
    z = 0.0                                          # 一层很轻的气声托底
    for i in range(n):
        t = i / (n - 1.0)
        z += 0.5 * (_ARNG.uniform(-1.0, 1.0) - z)
        b[i] += 0.12 * z * (t ** 2.0)
    return _pack(b, 0.40, fi=0.003, fo=0.015)


WIN_TIERS = [
    # (音符序列, 音间隔, 总长, 混响, 峰值, 低音支撑)
    (["C5", "E5", "G5"], 0.085, 0.72, 0.14, 0.50, None),
    (["C5", "E5", "G5", "C6"], 0.080, 0.85, 0.18, 0.55, None),
    (["C5", "E5", "G5", "C6", "E6"], 0.075, 1.00, 0.24, 0.60, None),
    (["C5", "E5", "G5", "C6", "E6", "G6"], 0.070, 1.20, 0.28, 0.65, 130.8),
    (["C5", "E5", "G5", "C6", "E6", "G6", "C7"], 0.068, 1.40, 0.32, 0.70, 82.0),
    (["C5", "E5", "G5", "C6", "E6", "G6", "C7", "E6"], 0.064, 1.60, 0.36, 0.78, 55.0),        # tier5 x50
    (["C5", "E5", "G5", "C6", "E6", "G6", "C7", "G6", "C7"], 0.062, 1.80, 0.42, 0.85, 41.2),  # tier6 x100
]


def _sfx_win(tier):
    """中奖琶音 7 档: 0=x2 1=x3 2=x5 3=x10 4=x20 5=x50 6=x100, 音数/混响/低音随档位递增。"""
    tier = max(0, min(len(WIN_TIERS) - 1, tier))
    seq, step, dur, rv, peak, bass = WIN_TIERS[tier]
    lead = SFX_RESULT_LEAD
    b = _buf(dur + lead)
    last = len(seq) - 1
    for k, nm in enumerate(seq):
        _add_bell(b, lead + k * step, NOTE[nm], 0.90 - 0.05 * k,
                  0.62 if k == last else 0.38)
    if bass is not None:                                    # 大奖档的低音支撑
        _add_partials(b, lead, bass, [(1.00, 0.90, 0.30), (2.00, 0.25, 0.13)])
    if tier >= 4:
        for nm in ("C6", "E6", "G6"):                       # 收尾大三和弦
            _add_bell(b, lead + 0.50, NOTE[nm], 0.45, 0.90)
        for k in range(6):                                  # 尾部碎星
            _add_partials(b, lead + 0.62 + k * 0.075, 1900.0 + k * 185.0,
                          [(1.00, 0.22, 0.030), (2.40, 0.10, 0.015)])
    _reverb(b, rv, 0.70 if tier >= 4 else 0.45)
    return _pack(b, peak)


def _sfx_lose():
    """未中: 柔和下行两音(F4 -> C4), 轻描淡写地过去。"""
    lead = SFX_RESULT_LEAD
    b = _buf(0.34 + lead)
    _add_bell(b, lead + 0.00, NOTE["F4"], 0.70, 0.18, 0.5)
    _add_bell(b, lead + 0.11, NOTE["C4"], 0.70, 0.24, 0.5)
    _reverb(b, 0.10, 0.26)
    return _pack(b, 0.44)


def _sfx_click():
    """UI 按键: 极短软咔。"""
    b = _buf(0.035)
    _add_noise(b, 0.0, 0.0025, 0.50, 0.0012, 0.75)
    _add_partials(b, 0.0, 940.0, [(1.00, 0.50, 0.006), (2.60, 0.20, 0.003)])
    return _pack(b, 0.52)


def _sfx_error():
    """珠子不足: 低频颤音"嗡"。"""
    b = _buf(0.28)
    w = math.tau * 155.0 / SR
    for i in range(len(b)):
        trem = 0.55 + 0.45 * math.sin(math.tau * 19.0 * i / SR)
        env = min(1.0, i / (SR * 0.006)) * math.exp(-i / (SR * 0.16))
        b[i] = (math.sin(w * i) + 0.34 * math.sin(3 * w * i) +
                0.16 * math.sin(5 * w * i)) * trem * env
    return _pack(b, 0.44)


def _sfx_coin():
    """计分滚动的细碎"叮"(数字翻滚时连播)。"""
    b = _buf(0.035)
    _add_partials(b, 0.0, 2280.0, [(1.00, 1.00, 0.007), (2.02, 0.40, 0.004)])
    _add_noise(b, 0.0, 0.002, 0.18, 0.001, 0.90)
    return _pack(b, 0.47)


def _sfx_ready():
    """新球滚进柱塞就位。"""
    b = _buf(0.16)
    _add_partials(b, 0.000, 255.0, [(1.00, 1.00, 0.022), (2.30, 0.30, 0.010)])
    _add_noise(b, 0.000, 0.050, 0.14, 0.030, 0.25)
    _add_partials(b, 0.075, 300.0, [(1.00, 0.50, 0.016)])
    return _pack(b, 0.49)


def _sfx_cash():
    """重置珠子: 一串硬币落盘。"""
    b = _buf(0.55)
    for k in range(7):
        t = 0.02 + k * 0.06 + _ARNG.uniform(-0.012, 0.012)
        _add_partials(b, t, 1900.0 + _ARNG.uniform(-260.0, 520.0),
                      [(1.00, 0.80, 0.010), (2.03, 0.30, 0.005)])
        _add_noise(b, t, 0.002, 0.12, 0.001, 0.90)
    _reverb(b, 0.14, 0.25)
    return _pack(b, 0.59)


def _sfx_bead(f0):
    """中奖金雨珠子到账: 玻璃珠轻"叮"。三个音高变体轮播成"叮叮叮"的计数感。"""
    b = _buf(0.05)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.012),
                               (2.42, 0.35, 0.007)])
    _add_noise(b, 0.0, 0.002, 0.22, 0.001, 0.85)
    return _pack(b, 0.50)


def iter_bank():
    """按固定顺序逐个合成 (名字, PCM字节)。

    ⚠️ **顺序即音色**: 全部配方共用 _ARNG 一条随机流, 换顺序噪声实例就变,
       波形逐位全错。新增音效一律追加在末尾, 绝不许插在中间。
    """
    _ARNG.seed(SFX_SEED)                    # 每次烘焙音色完全一致
    for i, f0 in enumerate((1040.0, 1180.0, 1330.0, 1500.0, 1680.0, 1880.0)):
        yield "peg%d" % i, _sfx_tink(f0)
    for i, f0 in enumerate((185.0, 225.0)):
        yield "wall%d" % i, _sfx_wall(f0)
    for i, f0 in enumerate((430.0, 505.0)):
        yield "div%d" % i, _sfx_div(f0)
    for lev in range(6):
        yield "ratchet%d" % lev, _sfx_ratchet(lev)
    yield "charge_full", _sfx_charge_full()
    yield "rail", _sfx_rail()
    yield "launch", _sfx_launch()
    yield "flight", _sfx_flight()
    yield "riser", _sfx_riser()
    yield "pocket", _sfx_pocket()
    yield "bounce", _sfx_bounce()
    for tier in range(len(WIN_TIERS)):
        yield "win%d" % tier, _sfx_win(tier)
    yield "lose", _sfx_lose()
    yield "click", _sfx_click()
    yield "error", _sfx_error()
    yield "coin", _sfx_coin()
    yield "ready", _sfx_ready()
    yield "cash", _sfx_cash()
    for hard in (0, 1):
        yield "top%d" % hard, _sfx_top(hard)
    for i, f0 in enumerate((1960.0, 2320.0, 2760.0)):   # 金雨珠子到账(追加末尾, 不扰既有音色)
        yield "bead%d" % i, _sfx_bead(f0)


def bake_bank():
    """合成全部音效 -> {名字: PCM字节}。素材共 ≈16.0s(41 条时长之和), 烘焙实测 ~0.64s
    (纯 Python 浮点循环, 机器相关; 后台线程跑)。"""
    return dict(iter_bank())
