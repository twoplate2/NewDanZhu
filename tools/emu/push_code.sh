#!/bin/bash
# 把**当前工作区的代码**编译成 .pyc 推进模拟器上那个已装的应用 —— **不用重出 APK**。
#
# ## 为什么能这么干
#
# `buildozer android debug` 出的 APK 是 **debuggable** 的 ⇒ `adb shell run-as <pkg>` 能进
# 应用私有目录。而 p4a 把 Python 代码解压在 `/data/data/<pkg>/files/app/`, 并且
# **只在首次启动解压**（`libpybundle.version` 只判 `_python_bundle` 要不要重解压）——
# 实测: push 一个标记文件进去, 重启两次它还在, `danzhu/` 的时间戳始终是首次那个。
#
# ⚠️ 两个前提, 都实测过:
#   ① `.pyc` 的 magic 必须一致 —— 本机 Python **3.11** 与 APK 里的完全一样 (`a70d0d0a`)。
#      不一致的话这条路直接堵死(要装对应版本的 Python)。
#   ② 模块树必须一致 —— 实测设备 30 个 `.pyc` ↔ 本工程 30 个 `.py`, **差集为空**。
#
# ## 它不做什么
#
# · **不换资源**(`assets/` `fonts/` `voice/`)—— 那些没变就别动。
# · **不换 `_python_bundle`**(Python 本体)。
# · **不改 `buildozer.spec`** ⇒ 应用内版本号还是旧的那个(它不在 `app/` 里)。
#   ⇒ **日志头里的 `v0.8.xx` 不代表你推的代码版本**, 别拿它当验证依据。
#
# ## 跑法
#
#     bash tools/emu/push_code.sh            # 编译 + 推 + 重启
#     bash tools/emu/push_code.sh --restore  # 回滚到推送前的备份
set -u
ADB="/c/Program Files/Netease/MuMu/nx_main/adb.exe"
PKG="org.danzhu.tiao"
APP="/data/data/$PKG/files/app"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="$ROOT/temp/_pyc_build"
export MSYS_NO_PATHCONV=1

if [ "${1:-}" = "--restore" ]; then
  echo "=== 回滚 danzhu/ ==="
  "$ADB" shell "run-as $PKG rm -rf $APP/danzhu" >/dev/null 2>&1
  "$ADB" shell "run-as $PKG cp -r $APP/danzhu.bak074 $APP/danzhu" && echo "  已回滚到 danzhu.bak074"
  echo "  ⚠️ main.pyc / p4a/ 的回滚要手工做(备份时没存它们)"
  exit 0
fi

echo "=== 1) 复制源树到临时目录(不污染仓库) ==="
rm -rf "$BUILD"; mkdir -p "$BUILD"
cp -r "$ROOT/danzhu" "$BUILD/"; cp "$ROOT/main.py" "$BUILD/"; cp -r "$ROOT/p4a" "$BUILD/"
find "$BUILD" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
echo "  $BUILD"

echo "=== 2) 编译成 .pyc(legacy 布局, 与 p4a 一致) ==="
( cd "$BUILD" && python -m compileall -b -q danzhu main.py p4a ) || { echo "!! 编译失败"; exit 1; }
find "$BUILD" -name '*.py' -delete
echo "  $(find "$BUILD" -name '*.pyc' | wc -l) 个 .pyc"

echo "=== 3) 校验 magic(不一致就别推) ==="
# ⚠️ 必须把 Git Bash 路径转成 Windows 路径 —— `python` 是 Windows 程序, 不认 `/e/...`。
_BUILD_W="$(cygpath -m "$BUILD")"
# ⚠️ `PYTHONIOENCODING=utf-8` —— 不然中文/箭头会在 GBK 控制台上炸掉。
PYTHONIOENCODING=utf-8 python - <<PY || exit 1
import io, importlib.util, sys
b = io.open(r"$_BUILD_W/danzhu/config.pyc", "rb").read(4)
ok = b == importlib.util.MAGIC_NUMBER
print("  编出来 %s · 本机 %s ⇒ %s" % (b.hex(), importlib.util.MAGIC_NUMBER.hex(),
                                      "一致" if ok else "**不一致, 中止**"))
sys.exit(0 if ok else 1)
PY

echo "=== 4) 打包 + 停应用 + 备份 + 覆盖 ==="
( cd "$BUILD" && tar -cf "$ROOT/temp/_push.tar" danzhu main.pyc p4a )
"$ADB" shell "am force-stop $PKG" >/dev/null 2>&1; sleep 2
"$ADB" push "E:/AI_Tools/other/DanZhu/new_danzhu/temp/_push.tar" /data/local/tmp/_push.tar >/dev/null 2>&1
# ⚠️ 备份只在**第一次**做 —— 别把已推的新代码覆盖成"备份"
if ! "$ADB" shell "run-as $PKG ls $APP/danzhu.bak074" >/dev/null 2>&1; then
  "$ADB" shell "run-as $PKG cp -r $APP/danzhu $APP/danzhu.bak074" && echo "  已备份到 danzhu.bak074"
else
  echo "  备份已存在(不覆盖)"
fi
"$ADB" shell "run-as $PKG tar -xf /data/local/tmp/_push.tar -C $APP/" && echo "  已覆盖"

echo "=== 5) 重启 ==="
"$ADB" shell "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
sleep 30
"$ADB" shell "ps -A | grep $PKG" | head -1
echo
echo "⚠️ 验证代码版本**别看日志头的 v0.8.xx**(那读的是 buildozer.spec, 不在 app/ 里)"
echo "   ⇒ 看行为: 比如跑一轮后日志里有没有新加的字段"
