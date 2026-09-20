#!/bin/bash
# 改模拟器上那份**存档里的帧率档**(`fps_cap_setting`) —— 不用改代码、不用重出 APK。
#
# ## 为什么这是个正经旋钮, 不是作弊
#
# Kivy 的帧率闸门(`kivy/clock.py:867-930`)读出来的规则是:
#
#     只在  body_elapsed < 0.4/fps  时才睡
#     睡则帧周期 = 1/fps − (4/5)·(1/(3·fps)) = **(11/15)/fps**
#     ⇒ 帧周期 ≈ max(body, (11/15)/cap), 再被 vsync 取整
#
# 所以 `cap` 不只是"上限", 它还是**帧周期的一个地板**:
#
#     cap=120 → 地板 6.111ms      cap=165 → 地板 4.444ms      cap=185 → 地板 3.964ms
#
# 真机面板是 165Hz(一格 6.06ms), 而工厂默认 `FPS_CAP_DEFAULT=185`; 但这台模拟器上
# 存档里是 **120** ⇒ 地板 6.111ms 与一格 vsync 6.06ms **几乎重合**, 这是个巧合位置,
# 不一定是最优的。⇒ 扫一遍档位, 看 1%Low 跟着谁走。
#
# ## 它改的是哪儿
#
# `/data/data/<pkg>/files/plinko_config.json` 的 `fps_cap_setting`(`game.py:1567-1569`
# 读它, 并校验必须 ∈ `FPS_CAP_OPTIONS`)。⚠️ **合法值从 `danzhu/config.py` 现读**(见下面
# `VALID=` 那一段) —— 这里**不许再写死一份档位表**, 它已经漂过一次:
# 脚本里留着旧的 6 档而 config 已经扩容 ⇒ 合法档被判成非法、脚本拒绝执行。
#
# ⚠️ **必须在应用停掉之后写** —— 应用退出时会回写整份 config, 会把改动盖掉。
#    (`multi_round.sh` 每轮 `am force-stop`, 是 SIGKILL, 不回写。)
#
# 跑法:  bash tools/emu/set_fps_cap.sh 165
#        bash tools/emu/set_fps_cap.sh          # 只看当前值
set -u
ADB="/c/Program Files/Netease/MuMu/nx_main/adb.exe"
PKG="org.danzhu.tiao"
CFG="/data/data/$PKG/files/plinko_config.json"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export MSYS_NO_PATHCONV=1

read_cfg() {
  "$ADB" shell "run-as $PKG cat $CFG" 2>/dev/null | tr -d '\r'
}

if [ $# -eq 0 ]; then
  echo "当前存档里的帧率档:"
  read_cfg | python -c "import sys,json;d=json.load(sys.stdin);print('  fps_cap_setting =',d.get('fps_cap_setting'))"
  exit 0
fi

CAP="$1"
# ⚠️⚠️ **合法档位从 `danzhu/config.py` 现读, 不写死。**
#    写死的版本 2026-09-20 漂过一次: `config.py` 已恢复成 `(…165, 185)`, 而脚本里还留着
#    `…165, 480` ⇒ 合法档 185 被判成非法、脚本拒绝执行。`config.py` 是纯 stdlib, 能直接 import。
VALID="$(PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0, r'$(cygpath -m "$ROOT")')
from danzhu.config import FPS_CAP_OPTIONS
print(' '.join(str(v) for v in FPS_CAP_OPTIONS))" 2>/dev/null)"
case " $VALID " in
  *" $CAP "*) ;;
  *) echo "!! $CAP 不是合法档位(FPS_CAP_OPTIONS = $VALID)"; exit 1;;
esac

echo "=== 1) 停应用(防止退出时回写覆盖) ==="
"$ADB" shell "am force-stop $PKG" >/dev/null 2>&1; sleep 2

echo "=== 2) 拉存档 ==="
D="$(cd "$(dirname "$0")/../.." && pwd)/temp"
read_cfg > "$D/_cfg_in.json"
wc -c < "$D/_cfg_in.json" | sed 's/^/  /'

echo "=== 3) 改 fps_cap_setting -> $CAP ==="
# ⚠️ **必须 cygpath 转成 Windows 路径** —— `python` 是 Windows 程序, 不认 Git Bash 的
#    `/e/...`(踩过: 报 `FileNotFoundError: '/e/.../_cfg_in.json'`, 而 `wc -c` 明明读到了 160 字节)。
PYTHONIOENCODING=utf-8 python - "$(cygpath -m "$D/_cfg_in.json")" \
                             "$(cygpath -m "$D/_cfg_out.json")" "$CAP" <<'PY'
import io, json, sys
src, dst, cap = sys.argv[1], sys.argv[2], int(sys.argv[3])
d = json.load(io.open(src, encoding="utf-8"))
old = d.get("fps_cap_setting")
d["fps_cap_setting"] = cap
io.open(dst, "w", encoding="utf-8").write(json.dumps(d))
print("  %s -> %s" % (old, cap))
PY

echo "=== 4) 推回去 ==="
"$ADB" push "$(cygpath -m "$D/_cfg_out.json")" /data/local/tmp/_cfg.json >/dev/null 2>&1
"$ADB" shell "run-as $PKG cp /data/local/tmp/_cfg.json $CFG" && echo "  已写入"

echo "=== 5) 读回确认 ==="
read_cfg | python -c "import sys,json;d=json.load(sys.stdin);print('  fps_cap_setting =',d.get('fps_cap_setting'))"
