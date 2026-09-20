#!/bin/bash
# 老版/新版 **交替**跑 N 轮 —— 每轮换一个包, 拿两组的"真实重复实验"分布。
#
# ## 为什么要交替(而不是"先跑完老版再跑完新版")
#
# `ab_compare.py` 的判据是**组间中位差 vs 组内极差**。顺序分组有个**静默前提**:
# 两组的测量条件在这一个多小时里是同一个。而主机负载 / 模拟器自身 GC 都可能**随时间漂移**
# —— 那样"组间差"里就混进了"前半段 vs 后半段"的差, 而且**看不出来**。
# 交替跑把这个漂移摊到两组里(每对相邻), 代价只是每轮多一次冷启动(本来也要冷启动)。
#
# ## 为什么用 `multi_round.sh` 的第 4 个参数(起始轮号)
#
# 它每轮的文件名是 `_emu_<前缀><轮号>.txt`。交替时每轮只跑 1 遍, 轮号恒为 1 ⇒
# **每轮覆盖上一轮**。所以把轮号从外面递进去(`START=i`), 文件名才是 `_emu_v69_r3.txt`。
#
# ## 钉住帧率档
#
# ⚠️ **两版的出厂档不一样**(老版 `FPS_CAP_DEFAULT=120` / 新版 `240`), 而两版共同的合法档
#    是 `(60,90,120,144,165)`, 面板本身也是 165Hz ⇒ **统一钉 165**。
#    不钉的话, 量到的是"帧率档的差", 不是"代码的差"。脚本开头重钉一次, 防中途被改。
#
# 跑法:  bash tools/emu/ab_rounds.sh [每组轮数, 默认 6] [起始对号, 默认 1]
#        # 想把 6 对加到 12 对, 就再来一次(文件名按对号走, 不会覆盖):
#        bash tools/emu/ab_rounds.sh 6 7
#
# ⚠️⚠️ **跑起来之后不许再改这个文件(或它调用的那两个)** ——
#    `bash` 是**按字节偏移**边读边执行的。2026-09-20 实测: 跑到一半改了
#    `multi_round.sh`, 结尾就报 `ho: command not found` + `syntax error near 'else'`
#    (那一轮的数据没丢, 但尾部整段被读错)。改脚本请**等它跑完**。
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
D="$ROOT/temp"
N="${1:-6}"
START="${2:-1}"
OLD_PKG="org.danzhu.plinko"    # v0.8.69
NEW_PKG="org.danzhu.tiao"      # v0.8.122
CAP=165

LOCK="$D/_emu_ab.lock"
if [ -f "$LOCK" ]; then
  echo "!! 已经有一个 A/B 在跑(PID $(cat "$LOCK" 2>/dev/null)) —— 拒绝启动。"
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
  echo "################## 第 $i / $LAST 对 ##################"
  echo "---------- 老版 v0.8.69 ----------"
  bash "$ROOT/tools/emu/multi_round.sh" 1 "v69_r"  "$OLD_PKG" "$i"
  echo "---------- 新版 v0.8.122 ----------"
  bash "$ROOT/tools/emu/multi_round.sh" 1 "v122_r" "$NEW_PKG" "$i"
done

echo ""
echo "=== 全部完成, 汇总 ==="
for i in $(seq "$START" "$LAST"); do
  for p in v69_r v122_r; do
    f="$D/_emu_${p}${i}.txt"
    [ -f "$f" ] && grep -m1 "^# 窗口" "$f" | sed "s|^|  ${p}${i}: |"
  done
done
echo ""
echo "对比:  python tools/emu/ab_compare.py v69_r v122_r"
