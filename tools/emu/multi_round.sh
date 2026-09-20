#!/bin/bash
# 在模拟器上**连跑 N 轮跑分**, 每轮把逐帧日志拖下来 —— 拿真实的**轮间波动**。
#
# 为什么: 决策树第 ① 层要的是「这个分数可不可重复」。
# 2026-09-20 已经用**同一份日志做块自举**给了个下界(带宽 4%~168%), 但那是**重采样**。
# 这里要的是**真实重复实验**: 同一份 APK、同一台机器、什么都不改, 跑 N 遍。
#
# ⚠️⚠️ **每轮之间用 `am force-stop` + 重启应用复位状态。**
#   第一版靠点「返回」「关闭」两个坐标关弹窗 —— **实测关不掉**
#   (截图显示「帧率曲线」弹窗还盖着, 而且内容是上一轮的), 于是第 2 轮的长按
#   按在弹窗上, 后面全废。**重启慢 30 秒, 但状态是确定的。**
#
# 跑法:  bash tools/emu/multi_round.sh [轮数, 默认 4]
set -u
ADB="/c/Program Files/Netease/MuMu/nx_main/adb.exe"
D="$(cd "$(dirname "$0")" && pwd)"
cd "$D"
export MSYS_NO_PATHCONV=1
N="${1:-4}"
PREFIX="${2:-r}"          # 输出文件名前缀(A/B 两组分开)

tap()  { "$ADB" shell "input tap $1 $2" >/dev/null 2>&1; sleep "${3:-3}"; }
hold() { "$ADB" shell "input swipe 540 78 540 78 3200" >/dev/null 2>&1; sleep 4; }
# ⚠️ 用 `plinko_fps_2*` 而不是 `plinko_fps_*` —— 后者会匹配到「连现有记录一起」存的
#    `plinko_fps_all_<时间戳>.txt`, 那样脚本会拉到**别人**。
newest() { "$ADB" shell "ls -t /sdcard/Download/plinko_fps_2*.txt 2>/dev/null | head -1" | tr -d '\r'; }

for i in $(seq 1 "$N"); do
  echo "=========== 第 $i 轮 / 共 $N 轮 ==========="
  BEFORE="$(newest)"
  "$ADB" shell "am force-stop org.danzhu.tiao" >/dev/null 2>&1; sleep 3
  "$ADB" shell "monkey -p org.danzhu.tiao -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
  echo "  启动中, 等 32 秒…"; sleep 32
  hold                       # 长按标题 → 跑分菜单
  tap 310 1044 2             # 「开始模拟测试」
  echo "  跑分中, 等 108 秒…"; sleep 108
  tap 310 1300 4             # 「帧率曲线」
  tap 310 1303 4             # 「保存日志(txt)」→ **0.8.101 起会弹选择框**
  tap 310 1049 6             # 「只存最近一次」  ← 不加这一步, 保存不会执行
  NEW="$(newest)"
  if [ "$NEW" = "$BEFORE" ] || [ -z "$NEW" ]; then
    echo "  ⚠️ 没有新日志(最新还是 $NEW) —— 这一轮作废"
  else
    "$ADB" pull "$NEW" "_emu_${PREFIX}$i.txt" 2>&1 | tail -1
  fi
done

echo "=== 全部完成, 汇总 ==="
for i in $(seq 1 "$N"); do
  [ -f "_emu_${PREFIX}$i.txt" ] && grep -m1 "^# 窗口" "_emu_${PREFIX}$i.txt" | sed "s/^/  r$i: /"
done
