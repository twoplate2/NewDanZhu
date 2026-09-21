#!/bin/bash
# **反向顺序**的 A/B：新版**先**跑、老版**后**跑 —— 把「执行顺序/漂移」与「版本」分开。
#
# ## 为什么必须做这一轮
#
# `ab_rounds.sh` 的循环体写死「老版 → 新版」，于是**每一对里新版永远是第二个跑的**。
# 交替只摊掉了「对与对之间」的漂移，**摊不掉对内的单调漂移**（模拟器预热、宿主机
# 其他租户、温度爬升）。若存在「第二个跑的更快」的效应，它会**整体计入版本差**。
#
# 2026-09-20 的 50 对里有几条 50/50 或 0/50 的"完美一致"结果（归一化跑分的分母
# +6.56%、GC 次/秒 +2.71%、用户态 ms/秒 +2.68%），全部是**新版更高**——
# 而新版恰好永远是第二个跑的。**这正是一个顺序效应会造出的形状。**
#
# 判据（跑完用 tools/emu/ab_compare.py v69_rv v122_rv 比）：
#   · 反向也给出「新版快 6.56%」 ⇒ 是**版本**效应
#   · 反向给出「**老版**快 ~6.56%」 ⇒ 是**顺序**效应，前 50 对的那些 50/50 结论当场作废
#   · 两边都没有 6% ⇒ 前 50 对的效应另有来源
#
# 跑法:  bash tools/emu/ab_rounds_rev.sh [对数, 默认 10] [起始对号, 默认 1]
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
D="$ROOT/temp"
N="${1:-10}"
START="${2:-1}"
OLD_PKG="org.danzhu.plinko"    # v0.8.69
NEW_PKG="org.danzhu.tiao"      # v0.8.122
CAP=165

LOCK="$D/_emu_abrev.lock"
if [ -f "$LOCK" ]; then
  echo "!! 已经有一个反向 A/B 在跑(PID $(cat "$LOCK" 2>/dev/null)) —— 拒绝启动。"
  exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

echo "########## 钉帧率档 = $CAP ##########"
bash "$ROOT/tools/emu/set_fps_cap.sh" "$CAP" "$OLD_PKG" 2>&1 | tail -1
bash "$ROOT/tools/emu/set_fps_cap.sh" "$CAP" "$NEW_PKG" 2>&1 | tail -1

LAST=$((START + N - 1))
for i in $(seq "$START" "$LAST"); do
  echo ""
  echo "################## 反向第 $i / $LAST 对 ##################"
  echo "---------- 新版 v0.8.122（**先跑**） ----------"
  bash "$ROOT/tools/emu/multi_round.sh" 1 "v122_rv" "$NEW_PKG" "$i"
  echo "---------- 老版 v0.8.69（**后跑**） ----------"
  bash "$ROOT/tools/emu/multi_round.sh" 1 "v69_rv"  "$OLD_PKG" "$i"
done

echo ""
echo "=== 全部完成, 汇总 ==="
for i in $(seq "$START" "$LAST"); do
  for p in v122_rv v69_rv; do
    f="$D/_emu_${p}${i}.txt"
    [ -f "$f" ] && grep -m1 "^# 窗口" "$f" | sed "s|^|  ${p}${i}: |"
  done
done
echo ""
echo "对比:  python tools/emu/ab_compare.py v69_rv v122_rv"
echo "探针:  python temp/_probechk.py 用的是 v69_r/v122_r 前缀 —— 反向的要另跑一次"
