#!/bin/bash
# 在模拟器上**连跑 N 轮跑分**, 每轮把逐帧日志拖下来 —— 拿真实的**轮间波动**。
#
# 为什么: 决策树第 ① 层要的是「这个分数可不可重复」。
# 2026-09-20 已经用**同一份日志做块自举**给了个下界(带宽 4%~168%), 但那是**重采样**。
# 这里要的是**真实重复实验**: 同一份 APK、同一台机器、什么都不改, 跑 N 遍。
#
# ⚠️⚠️ **每轮之间用 `am force-stop` + 重启应用复位状态。**
#   第一版靠点「返回」「关闭」两个坐标关弹窗 —— **实测关不掉**
#   (截图显示「帧率曲线」弹窗还盖着, 而且是上一轮的内容), 于是第 2 轮的长按
#   按在弹窗上, 后面全废。**重启慢 30 秒, 但状态是确定的。**
#
# ⚠️⚠️ **所有按钮一律"按颜色找", 不写死坐标**(`tools/emu/find_btn.py`)。
#   结果弹窗是**内容撑高**的 —— 「卡顿帧分布」那一行有/没有, 整张弹窗差一行,
#   按钮跟着上下移 **~57px**。2026-09-20 实测: 同一个 `input tap 310 1300`,
#   一轮点中、下一轮点空, 而**点空是静默失败**(日志没保存, 整轮作废)。
#
# ⚠️ 用 `plinko_fps_2*` 而不是 `plinko_fps_*` —— 后者会匹配到「连现有记录一起」存的
#    `plinko_fps_all_<时间戳>.txt`, 那样脚本会拉到**别人**。
#
# 跑法:  bash tools/emu/multi_round.sh [轮数, 默认 4] [文件名前缀, 默认 r]
set -u
ADB="/c/Program Files/Netease/MuMu/nx_main/adb.exe"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# ⚠️ 输出固定到**仓库根下的 temp/**(不是脚本自己所在的 tools/emu/) —— 脚本搬家之后
#    这里曾经把日志写进 tools/emu/, 而 ab_compare 是在 temp/ 找的。
D="$ROOT/temp"
cd "$D"
export MSYS_NO_PATHCONV=1
N="${1:-4}"
PREFIX="${2:-r}"          # 输出文件名前缀(A/B 两组分开)

# ⚠️⚠️⚠️ **互斥锁 —— 两个实例一起跑会把结果全废掉, 而且是静默的。**
#
# 2026-09-20 踩过: `TaskStop`(以及任何"杀掉外层 bash -c")**杀不掉脚本本体** ——
# 实测有三个 `multi_round.sh` 同时活着(11:28 / 11:37 / 11:38 启动)。它们**都在**
# `am force-stop` 应用、都在点标题、都在往同一个前缀里拉文件 ⇒
#   · 一个刚点完「开始模拟测试」, 另一个就把应用 force-stop 了, 跑分半途夭折;
#   · 结果页迟迟不出来, 脚本报"没有新日志", 而**屏幕上明明有**;
#   · 拉回来的几份文件**内容一模一样**(都是同一份设备文件的拷贝)。
# 症状看起来像"点击坐标不对", 实际是**抢机器**。
#
# ⇒ 判据不靠"我记得杀干净了", 靠锁文件。跑之前先看有没有锁。
LOCK="$D/_emu_round.lock"
if [ -f "$LOCK" ]; then
  echo "!! 已经有一个 multi_round.sh 在跑(PID $(cat "$LOCK" 2>/dev/null)) —— 拒绝启动。"
  echo "   两个实例会互相 force-stop 被测应用, 结果全废(而且是静默的)。"
  echo "   确认没有别的实例后: rm -f $LOCK"
  exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

tap()  { "$ADB" shell "input tap $1 $2" >/dev/null 2>&1; sleep "${3:-3}"; }
hold() { "$ADB" shell "input swipe 540 78 540 78 3200" >/dev/null 2>&1; sleep 4; }
newest() { "$ADB" shell "ls -t /sdcard/Download/plinko_fps_2*.txt 2>/dev/null | head -1" | tr -d '\r'; }

# 截图 -> 找蓝按钮 -> 点它。找不到返回 1(**不点**, 免得点到别的地方)。
# ⚠️⚠️ **`adb pull` 的目标必须是相对路径。** 本脚本 `export MSYS_NO_PATHCONV=1`
#    (为了保住设备上的 `/sdcard/...` 不被 Git Bash 改写), 但 adb 是 **Windows 程序**,
#    它在本地那一侧要的是 `E:/...` —— 绝对路径 `$D/x.png`(=`/e/...`)被原样交给它会
#    **静默失败**, 于是每次都读到**上一次**那张旧图。实测: `_emu_shot.png` 的时间戳
#    停在 01:50, 而 27 次轮询全报"没找到按钮", 屏幕上结果页明明在。
# ⚠️ `python` 那一侧相反, **必须**是 cygpath 转过的 Windows 绝对路径。
# ⇒ **先删再拉**: 文件在不在就是"这一步有没有落地"的判据, 不靠"命令没报错"。
shot() {
  rm -f "$D/_emu_shot.png"
  "$ADB" shell "screencap -p /sdcard/_emu_shot.png" >/dev/null 2>&1
  "$ADB" pull /sdcard/_emu_shot.png "_emu_shot.png" >/dev/null 2>&1
  [ -f "$D/_emu_shot.png" ] || { echo "    ⚠️ 截图没落地"; return 1; }
}
find_btn() {
  PYTHONIOENCODING=utf-8 python "$(cygpath -m "$ROOT/tools/emu/find_btn.py")" \
      "$(cygpath -m "$D/_emu_shot.png")" 2>/dev/null | head -1
}
# 一路点到"设备上**真的**出现了新日志"为止。
#
# ⚠️⚠️ **不写死"第 1 下点 A、第 2 下点 B"。** 弹窗是**一层套一层**的
#    (结果页 → 帧率曲线 → 保存范围), 而"保存范围"那一层**有时有有时没有**;
#    每层的按钮 y 也不一样(结果页那层还会随结果行数上下移)。
#    ⇒ 写成固定步序, 只要有一层没按预期出现, 后面的每一下都点空 ——
#      而**点空是静默失败**: 不报错、不崩, 只是那一轮日志没保存。
#    ⇒ 所以: **每一步都重新找按钮**, 出口只有一个 —— `newest()` 真的变了。
do_save() {
  local k=0 xy
  while [ "$k" -lt 8 ]; do
    k=$((k + 1))
    if ! shot; then sleep 2; continue; fi
    # ⚠️ **每一步都留一张截图**(`_emu_stepN.png`)。"点空"的失败是**静默**的,
    #    而且**点错会把模态弹窗直接关掉**(Kivy 的 ModalView 点空白处自动 dismiss)——
    #    事后只有截图能告诉你当时屏幕上到底是什么。
    cp -f "$D/_emu_shot.png" "$D/_emu_step${k}.png" 2>/dev/null
    xy="$(find_btn)"
    if [ "$xy" = "NONE" ] || [ -z "$xy" ]; then
      # ⚠️ **不 break** —— 弹窗是**渐入**的, 也可能刚被上一次点击关掉、下一层还没出来。
      #    早退等于把"等 3 秒"当成"到此为止"。
      echo "    第 $k 下: 暂时没有按钮, 等 2 秒再看"
      sleep 2
      continue
    fi
    echo "    第 $k 下 -> ($xy)"
    # shellcheck disable=SC2086
    "$ADB" shell "input tap $xy" >/dev/null 2>&1
    sleep 3
    [ "$(newest)" != "$BEFORE" ] && { echo "    ✓ 日志已落盘"; return 0; }
  done
  [ "$(newest)" != "$BEFORE" ]
}
# 等结果页出现: 判据是**弹窗里那块蓝按钮出现**, 不是"过了多少秒"。
# ⚠️ 跑分时长会飘(实测 108 秒不够, 165 秒也失手过)。而"蓝按钮出现"是结果页的**直接**
#    判据 —— 跑分进行中那一带(y 500~1550)是黑的, 找得到东西就一定是弹窗。
wait_result() {
  local w=0
  while [ "$w" -lt 330 ]; do
    sleep 12; w=$((w+12))
    shot
    case "$(find_btn)" in NONE|"") ;; *) return 0;; esac
  done
  echo "    ⚠️ 等了 330 秒结果页还没出现"
  return 1
}

for i in $(seq 1 "$N"); do
  echo "=========== 第 $i 轮 / 共 $N 轮 ==========="
  BEFORE="$(newest)"
  "$ADB" shell "am force-stop org.danzhu.tiao" >/dev/null 2>&1; sleep 3
  "$ADB" shell "monkey -p org.danzhu.tiao -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
  echo "  启动中, 等 32 秒…"; sleep 32
  hold                       # 长按标题 → 跑分菜单
  tap 310 1044 2             # 「开始模拟测试」
  # ⭐ **跑分结束由应用自己落盘**(bench.py 里的 `[BENCH AUTOSAVE]` 实验钩子) ——
  #    夹具只等"设备上出现新文件"。这样就不碰被测对象的 UI 了。
  # ⭐ **主路 = 点 UI 保存**(结果页 → 帧率曲线 → 保存范围), 出口只有一个:
  #    "设备上真的出现了新日志"。所有按钮都**按颜色找**(`find_btn.py`), 不写死坐标。
  echo "  跑分中, 等结果页…"
  if wait_result; then
    do_save
  fi
  NEW="$(newest)"
  if [ "$NEW" = "$BEFORE" ] || [ -z "$NEW" ]; then
    # ⚠️ 这里**只给 20 秒**去等"自动落盘", 不再给 330 秒。
    #    原因: 出过一版 `bench.py` 带 `[BENCH AUTOSAVE]` 实验钩子(采样结束自己写盘, 免点弹窗),
    #    那时这里等 330 秒是对的。**钩子按计划删掉之后**, 再等 330 秒就是每轮白等 5.5 分钟
    #    —— 而它永远不会来。(2026-09-20 实测踩到: 8 轮要白等 44 分钟。)
    echo "  ⚠️ 第一次没落地 —— 再试一次…"
    sleep 20
    if wait_result; then
      do_save
    fi
    NEW="$(newest)"
  fi
  if [ "$NEW" = "$BEFORE" ] || [ -z "$NEW" ]; then
    echo "  ⚠️ 没有新日志(最新还是 $NEW) —— 这一轮作废"
  else
    "$ADB" pull "$NEW" "_emu_${PREFIX}$i.txt" 2>&1 | tail -1
  fi
done

echo "=== 全部完成, 汇总 ==="
for i in $(seq 1 "$N"); do
  [ -f "_emu_${PREFIX}$i.txt" ] && grep -m1 "^# 窗口" "_emu_${PREFIX}$i.txt" | sed "s/^/  ${PREFIX}$i: /"
done
