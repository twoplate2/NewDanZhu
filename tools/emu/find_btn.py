# -*- coding: utf-8 -*-
"""在模拟器截图里**按颜色**找出弹窗按钮, 打印它的中心坐标 —— 给 `multi_round.sh` 用。

## 为什么不能硬编码坐标

「画面帧率和性能测试」那张结果弹窗是**内容撑高**的: 「卡顿帧分布」那一行有/没有,
整张弹窗就差一行, 两个按钮跟着上下移 **~57px**。
2026-09-20 实测过: 同一个 `input tap 310 1300`, 一轮点中、下一轮点空 ——
**点空是静默失败**(弹窗没开 → 后面两下点到空处 → 日志没保存 → 整轮作废,
只在脚本输出留一行"没有新日志")。

⇒ 判据换成"**弹窗里那块蓝色的位置**", 而不是"y 等于多少"。

## 判据

按钮的蓝是 `(53, 99, 209)`(实测采样)。在 `y ∈ [Y0, Y1]` 的带状区域里找
**连续 ≥ MIN_RUN 像素**的蓝行, 取**最上面那一条带**的中心。

⚠️ 为什么要限制 y 区间: 背景里 RTP 档位那排按钮也是蓝的(y≈150),
   不限的话会点错。

跑法:  python tools/emu/find_btn.py <截图.png> [--y0 500] [--y1 1550]
输出:  中心坐标一行 `X Y`; 找不到时打印 `NONE` 并以 1 退出。
"""
import sys

BLUE = (53, 99, 209)
TOL = 26.0            # 颜色容差(每通道)
MIN_RUN = 240         # 一行里至少连续这么多像素才算"按钮", 不是图标/文字
MIN_BAND = 14         # 至少这么多行才算一条带
# ⚠️ **文字会把按钮切成上下两条带**: 一行穿过白字时, 蓝色被字切开, 最长的连续段
#    只有按钮两边剩下的那点宽度 ⇒ 过不了 MIN_RUN, 那一行整行被丢。
#    实测: 「保存日志(txt)」按钮本身 y 1256~1335, 但只看 gap≤2 的分带会得到
#    1256~1283 与 1312~1335 两条 —— 取上一条的中心就落在按钮**上沿之外**。
#    ⇒ 相邻两条带之间只要不超过这个高度, 就当成同一个按钮的文字缝, 合并。
TEXT_GAP = 70


def load(path):
    from PIL import Image
    return Image.open(path).convert("RGB")


def main():
    args = sys.argv[1:]
    if not args:
        print("NONE")
        return 1
    path = args[0]
    y0, y1 = 500, 1550
    for i, a in enumerate(args):
        if a == "--y0":
            y0 = int(args[i + 1])
        if a == "--y1":
            y1 = int(args[i + 1])

    im = load(path)
    W, H = im.size
    y1 = min(y1, H)
    px = im.load()

    # 逐行找最长蓝色连段
    rows = []
    for y in range(y0, y1):
        best = cur = 0
        bs = cs = 0
        for x in range(W):
            r, g, b = px[x, y]
            if (abs(r - BLUE[0]) <= TOL and abs(g - BLUE[1]) <= TOL
                    and abs(b - BLUE[2]) <= TOL):
                if cur == 0:
                    cs = x
                cur += 1
                if cur > best:
                    best, bs = cur, cs
            else:
                cur = 0
        if best >= MIN_RUN:
            rows.append((y, bs, best))

    if not rows:
        print("NONE")
        return 1

    # 切成粗带(行号不连续就断开), 再按 TEXT_GAP 把"被文字切开的同一个按钮"并回去
    bands = []
    cur = [rows[0]]
    for r in rows[1:]:
        if r[0] - cur[-1][0] <= 2:
            cur.append(r)
        else:
            bands.append(cur)
            cur = [r]
    bands.append(cur)

    merged = [bands[0]]
    for b in bands[1:]:
        gap = b[0][0] - merged[-1][-1][0]
        # 横向也要对得上 —— 免得把两个上下排列的不同按钮并成一个
        ox = min(merged[-1][-1][1] + merged[-1][-1][2], b[0][1] + b[0][2]) - max(
            merged[-1][-1][1], b[0][1])
        if gap <= TEXT_GAP and ox > MIN_RUN:
            merged[-1] = merged[-1] + b
        else:
            merged.append(b)

    band = merged[0]                      # 最上面那条
    if len(band) < MIN_BAND:
        print("NONE")
        return 1
    ymid = (band[0][0] + band[-1][0]) // 2
    xs = [b[1] for b in band]
    xe = [b[1] + b[2] for b in band]
    xmid = (min(xs) + max(xe)) // 2
    print("%d %d" % (xmid, ymid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
