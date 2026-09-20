#!/bin/bash
# 扫**帧率档**: 每档设一次存档 + 跑 N 轮跑分, 输出 `_emu_k<cap><i>.txt`。
#
# ## 为什么需要它
#
# 2026-09-20 实测定的靶子: 应用能不能**稳住**「Kivy 自己睡」(模式 A) 取决于
# 睡眠目标 `(11/15)/cap` 与一格 vsync 之间的**余量** ——
#
#     cap=120 → 目标 6.111ms vs 一格 6.061ms ⇒ 余量 **+0.83%** ⇒ 8 轮里只有 1~2 轮稳住
#     cap= 90 → 目标 8.148ms vs 6.061ms      ⇒ 余量 **+34%**  ⇒ 4 轮 **4/4** 稳住
#
# ⇒ 真正的最优档 = 「**还能稳住 A 的最大 cap**」, 要在 90 和 120 之间二分找。
#   一次设一档、连跑几轮, 就是想在这个区间里定位。
#
# ⚠️ `set_fps_cap.sh` 每次都会 `am force-stop` 被测应用 —— 这是必须的,
#    因为档位是**读存档**生效的, 不重启不生效。
#
# 跑法:  bash tools/emu/cap_sweep.sh "115 110 105" [每档轮数, 默认 3]
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
D="$ROOT/temp"
CAPS="${1:?用法: cap_sweep.sh \"115 110\" [每档轮数]}"
N="${2:-3}"

LOCK="$D/_emu_capsweep.lock"
if [ -f "$LOCK" ]; then
  echo "!! 已经有一个扫描在跑(PID $(cat "$LOCK" 2>/dev/null)) —— 拒绝启动。"
  exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

cd "$D"
for CAP in $CAPS; do
  echo "########## 帧率档 $CAP ##########"
  bash "$ROOT/tools/emu/set_fps_cap.sh" "$CAP" 2>&1 | tail -2
  bash "$ROOT/tools/emu/multi_round.sh" "$N" "k${CAP}"
done

echo "=== 扫描完成, 汇总 ==="
for CAP in $CAPS; do
  for i in $(seq 1 "$N"); do
    f="_emu_k${CAP}${i}.txt"
    [ -f "$f" ] && grep -m1 "^# 窗口" "$f" | sed "s|^|  ${CAP} #$i: |"
  done
done
