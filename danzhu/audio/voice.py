"""预录语音(61 条 wav, edge-tts 生成, 落在 <app_root>/voice/)。

⚠️ 语音**不是** bake_bank 的合成品, 不参与 iter_bank 的"顺序即色"体系 —— 它是数据源。
   名字与音色对账无关, 但**名字集合**是闸门期望数(`_expected`)的一部分。
"""

import os
import sys

_VOICE_FILES_CACHE = [None]          # None = 还没列过; 列到东西了才缓存(见 _voice_files)
# 上次列举失败的原因(空串=没失败)。**静默为空是这里最大的风险**: 目录没打进包时
# 语音会全灭而玩家只看到"没声音", 没有任何线索。启动信息面板读它 —— 别删。
_VOICE_MISS = [""]

# 满编条数 = 打包进包里的语音条数(老工程 android/voice/ 与本工程 voice/ **逐名相同**,
# 61 个)。只给"实际列到几条 vs 应有几条"这个校验用(见 tests/audio_gates.py)。
# ⚠️ **不能**写成 `len(_voice_files())` —— 那个数正是要被校验的对象: 目录没打进包时它
#    列举出来就是 0, 拿它当满编数, 校验就退化成一句废话(报「0/0 全绿」)。语音集变了
#    必须手动改这里 —— 改不动正是它该红的时候。
N_VOICE = 61


def voice_miss():
    """最近一次语音目录列举的失败原因; 空串 = 正常。"""
    return _VOICE_MISS[0]


def app_root():
    """应用根目录 = 与主脚本同级(打包后 main.py 就在那儿)。

    ⚠️ 老版用 `dirname(abspath(__file__))`, 那个 __file__ 是 main.py; 模块化之后
       `__file__` 指向本包, 所以改成"取主脚本的目录"。
    """
    _e = os.environ.get("DANZHU_APP_ROOT")
    if _e:
        return _e
    _m = sys.modules.get("__main__")
    _f = getattr(_m, "__file__", None)
    if _f:
        return os.path.dirname(os.path.abspath(_f))
    return os.getcwd()


def _voice_dir():
    """预录语音目录; 目录不存在时静默为空 —— 语音是安卓版附加功能。"""
    _e = os.environ.get("DANZHU_VOICE_DIR")
    if _e:
        return _e
    return os.path.join(app_root(), "voice")


def _voice_files():
    """{语音名: wav 路径}。

    ⚠️ **加缓存**。原来每次调用都是一次 `os.listdir(voice/)`(**61 项**) —— 安卓上走 FUSE,
       目录列举不是免费的; 而且老写法还把 `_voice_dir()` 重复算 63 次, 它挂在**主线程**上
       (`voice_duration` 由 `Sfx.play` 调用), 一轮要查 4~8 段 = 同一帧里 4~8 次目录列举。
       目录内容运行期不会变(语音是打包进来的), 所以缓存是安全的。
    ⚠️ **只缓存非空结果**: 首次调用若目录还没解包出来(listdir 抛异常或空), 不能把空字典
       缓存住 —— 那会让语音**永久静默失效**。拿不到就下次再列。
    """
    got = _VOICE_FILES_CACHE[0]
    if got is not None:
        return got
    out = {}
    try:
        d = _voice_dir()
        for fn in os.listdir(d):
            if fn.endswith(".wav"):
                out[fn[:-4]] = os.path.join(d, fn)
        _VOICE_MISS[0] = "" if out else "目录是空的: %s" % d
    except Exception as _e:
        # 不往外抛(语音是附加功能, 缺了不该拖垮启动), 但**必须留下线索** ——
        # 老版这块是裸 pass, 于是"语音全灭"在诊断里查无实据。
        _VOICE_MISS[0] = "%s: %s" % (type(_e).__name__, _e)
    if out:
        _VOICE_FILES_CACHE[0] = out
    return out


def reset_cache():
    """丢掉目录缓存(测试/诊断用)。⚠️ 出货路径**没有**调用点 —— 见 _voice_files 的说明。

    ⚠️ 失败原因(`_VOICE_MISS`)必须**一起**清: 它是同一次列举的另一个产物, 只清一个会让
       `voice_miss()` 继续报**上一次目录**的旧原因 —— 诊断时最容易被那条旧原因带偏
       (换过目录之后, 你会以为新的那个也失败, 或者以为它没事)。
    """
    _VOICE_FILES_CACHE[0] = None
    _VOICE_MISS[0] = ""


def number_voice_names(n):
    """整数 → 中文朗读的语音名列表(队列拼接用)。

    1250 → ['voice_d_1','voice_u_1000','voice_d_2','voice_u_100','voice_d_5','voice_u_10']
    2000 → ['voice_liang','voice_u_1000']
    """
    if n == 0:
        return ["voice_d_0"]
    names = []
    wan = n // 10000
    rest = n % 10000
    if wan > 0:
        if wan == 2:
            names.append("voice_liang")   # 万位的 2 读"两"(两万)
        else:
            names.extend(_read_4digits(wan, is_highest=True))
        names.append("voice_u_10000")
    names.extend(_read_4digits(rest, is_highest=(wan == 0)))
    return names or ["voice_d_0"]


def _read_4digits(n, is_highest=True):
    """朗读 0~9999, 返回语音名列表。二/两规则: 千位的 2 读"两"。"""
    if n == 0:
        return []
    qian, rem = divmod(n, 1000)
    bai, rem = divmod(rem, 100)
    shi, ge = divmod(rem, 10)
    parts = []
    need_zero = False
    if qian > 0:
        parts.append("voice_liang" if qian == 2 else "voice_d_%d" % qian)
        parts.append("voice_u_1000")
    else:
        need_zero = is_highest is False   # 低位组(前面有万位)且无千位: 组前要"零"
    if bai > 0:
        if need_zero:
            parts.append("voice_d_0")
            need_zero = False
        parts.append("voice_d_%d" % bai)
        parts.append("voice_u_100")
    elif qian > 0:
        need_zero = True                  # 千位后百位空: 十位/个位前要"零"
    if shi > 0:
        if need_zero:
            parts.append("voice_d_0")
            need_zero = False
        if shi == 1 and not parts:
            parts.append("voice_u_10")          # 10~19: "十" 不读 "一十"
        else:
            parts.append("voice_d_%d" % shi)
            parts.append("voice_u_10")
    if ge > 0:
        if shi == 0 and (need_zero or qian > 0 or bai > 0):
            parts.append("voice_d_0")
        parts.append("voice_d_%d" % ge)
    return parts
