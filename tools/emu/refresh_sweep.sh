#!/bin/bash
# 扫模拟器刷新率: 每个档位**重启模拟器** + 跑 N 轮跑分, 输出 `_emu_r<hz><i>.txt`。
#
# ## 为什么不用改代码
#
# 游戏的 `cap = min(屏幕支持, Android系统上限, 用户设定)`(`platform/device.py`)。
# 只要 `FPS_CAP_OPTIONS` 的顶格**大于等于**所有要扫的刷新率, 用户档位一直设顶格就行 ——
# 面板是 420Hz 就自动夹到 420, 是 450 就夹到 450。**⇒ 扫档只需要动模拟器。**
#
# ## 为什么要有这一层脚本(而不是手动逐个来)
#
# 每档都要 `MuMuManager restart` + 等启动 + 复核实际模式, 手动做两次就会漏掉复核那一步,
# 而**不复核就等于不知道量的是哪一档**(设置回显 `"max_frame_rate": "480"` 只是"我写了",
# 不是"系统真的用了"—— 实测设置回显成功、而 `supportedModes` 里没有那个值时也发生过)。
#
# ⚠️ `restart` 之后 adb 会先 `offline` 一阵, 所以 `wait-for-device` 必须**重试**,
#    一次调用会在设备还没起来时直接报错返回。
#
# 跑法:  bash tools/emu/refresh_sweep.sh "420 450 480" [每档轮数, 默认 3]
set -u
ADB="/c/Program Files/Netease/MuMu/nx_main/adb.exe"
MM="/c/Program Files/Netease/MuMu/nx_main/MuMuManager.exe"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
D="$ROOT/temp"
export MSYS_NO_PATHCONV=1
HZS="${1:?用法: refresh_sweep.sh \"420 450\" [每档轮数]}"
N="${2:-3}"
cd "$D"

LOCK="$D/_emu_sweep.lock"
if [ -f "$LOCK" ]; then
  echo "!! 已经有一个扫描在跑(PID $(cat "$LOCK" 2>/dev/null)) —— 拒绝启动。"
  echo "   两个实例会互相 force-stop 被测应用, 结果全废(而且是静默的)。"
  exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

for HZ in $HZS; do
  echo "########## 刷新率 $HZ ##########"
  "$MM" setting --vmindex 0 --key max_frame_rate --value "$HZ" 2>&1 | tr -d '\r' | tail -2
  "$MM" control -v 0 restart >/dev/null 2>&1
  B=""
  for _i in $(seq 1 60); do
    "$ADB" wait-for-device 2>/dev/null
    B="$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')"
    [ "$B" = "1" ] && break
    sleep 4
  done
  echo "  启动完成(boot=$B) —— 等 15 秒让系统稳下来"
  sleep 15
  "$ADB" shell "dumpsys display > /sdcard/_hz.txt 2>&1"
  "$ADB" pull /sdcard/_hz.txt "_hz.txt" >/dev/null 2>&1
  # ⚠️ 这一行是"我量的到底是哪一档"的唯一证据。**设置回显不算证据。**
  echo "  系统实际给的模式: $(grep -o 'fps=[0-9.]*' _hz.txt | sort -u | tr '\n' ' ')"
  bash "$ROOT/tools/emu/multi_round.sh" "$N" "r$HZ"
done

echo "=== 扫描完成, 汇总 ==="
for HZ in $HZS; do
  for i in $(seq 1 "$N"); do
    f="_emu_r${HZ}$i.txt"
    [ -f "$f" ] && grep -m1 "^# 窗口" "$f" | sed "s|^|  ${HZ}Hz #$i: |"
  done
done
