# 应用间隔离线分析

本工具复算应用 `on_flip` 间隔，不把它称为屏幕实际呈现。仅依赖 Python 标准库。

```powershell
python tools/low_analyze.py temp/low/capture.json --split
python tools/low_analyze.py temp/low/A1.json temp/low/B1.json temp/low/B2.json temp/low/A2.json --paired --seed 20260927
python tools/low_analyze.py log/plinko_fps_all_20260920_203351.txt --split
python tests/low_metrics.py
```

输出为具名 JSON；只要存在无效会话，CLI 返回码为 2。无效样本及其原因仍保留。配对排除单独写在 `comparison.excluded`。历史 TXT 可复算描述数字，但永久标记 `legacy_uncalibrated`、`legacy_completeness_unknown`，不能默认加入正式比较；旧记录没有显式“帧间隔毫秒”表头时单位也无效。

## 权威统计

`danzhu.bench.low_metrics.compute_metrics(intervals_ms, target_hz=None)` 只接收明确毫秒间隔。空数组、布尔、非数字、非正数、NaN/inf 抛 `DataError`。平均 FPS 是 `1000*N/sum(ms)`。1% Low 是 `1000/mean(最大的 max(1,floor(.01*N)) 个 ms)`。p95/p99 使用排序后 `(N-1)*q` 的线性插值，单位为 ms。每分钟长停顿按严格 `>16.7/25/33.3ms` 计数，除以总观察时长；有目标刷新率时报告预算及超预算比例。

`--split` 分别按 `phase` 与 `shot_id` 输出样本量和累计间隔时长。每个间隔归属于帧记录上的阶段/发射号，边界跨越的间隔因此由采集器的记录标签决定。分段不代表重建了连续时间线，静止等待应由采集模式/阶段明确区分。

## 新格式

聚合 JSON 是 `{schema, meta, events, frames, summary}`。schema 固定 `danzhu.low.v1`。也可输入此对象的数组。JSONL 使用 `type=meta/event/frame/summary`，meta 行含 `schema` 与 `meta` 对象，其余 frame/event 行把字段直接展开，summary 行包含 `summary` 对象；每个新 meta 开始新会话。

```json
{
  "schema": "danzhu.low.v1",
  "meta": {
    "session_id": "experiment01-A1",
    "interval_unit": "ms",
    "identity": {"app_id": "org.example.game", "version": "actual-version", "source_hash": "actual-source", "config_hash": "actual-config", "resource_hash": "actual-resource"},
    "device": {"model": "TB323FU", "os": "Android16", "abi": "arm64-v8a"},
    "display": {"actual_hz": 165, "mode_id": 1, "resolution": [2560, 1600]},
    "settings": {"brightness": 80, "sound": true, "vibration": true, "vsync": true, "cap": 0, "affinity": "original"},
    "scenario": {"version": 1, "signature": "actual-fixed-five-signature"},
    "mode": "fixed_five", "probe": "light", "protocol": "warm-v1",
    "pair_id": "pair01", "condition": "A", "run_order": 1
  },
  "events": [],
  "frames": [{"interval_ms": 6.06, "phase": "flying", "shot_id": 1, "flip_id": 2}],
  "summary": {"complete": true, "valid": true, "invalid_reasons": [], "frame_count": 1, "lost_records": 0}
}
```

这是字段示例，不是完整五发的有效实验。采集器负责完整事件链与终态提交证明，将超时、暂停、漏发、退场未完成等写入 `invalid_reasons`。离线工具再检查单位、记录容器、坏帧、重复 flip/session ID、声明帧数及丢记录。含坏帧的会话不输出部分剩余帧统计；其他无效会话的数字只作描述。不能根据数字高低删除样本。

## 轮级配对比较

`--paired` 用 `pair_id` 匹配恰好一轮 A、一轮 B；输入文件顺序不影响匹配。`run_order` 是不同整数，记录实际 AB/BA 顺序。配对要求实际构建 app/version/source/config/resource 身份全部明确，A/B 可来自不同构建；设备、显示、设置、场景版本/签名、模式、探针档位及协议必须一致。`scene`/`sampling_mode` 也接受，分别与 runtime 的 `scenario`/`mode` 等价。缺字段或 unavailable 不参与正式比较。采集器无法获取的元数据应写 unavailable，实验人员核实真实包和条件后补充实验清单，不能填猜测值。

每轮先独立计算主指标，再按配对计算 B-A 均值，按整个配对有放回重采样求 percentile 95% CI。默认主指标为绝对 `low_1pct_fps`；支持 average_fps、p95_ms、p99_ms、max_ms。CI 只描述该批独立轮的差值不确定性，至少两对才输出，少量配对精度有限；完全相同轮差值的区间可能退化。工具同时输出 AB/BA 均值及顺序不平衡警告，不以组内极差判显著，也不计算不显著即等价的结论。

本工具不能验证身份字段是否来自真实安装包，也不能证明真机性能收益、采集扰动、视觉或输入无回归。正式实验仍须保存实际构建身份、完整采集链、预注册轮数和冷却/温度规则；显示侧校准另行完成。

新schema的 flip_id 必须为连续递增整数，时间戳提供时须递增且与相邻帧 interval_ms 一致。JSONL summary 后的记录使该会话无效。历史TXT末尾具名“跑分记录/序号,时间”汇总段不当帧读取。整批配对的 A 身份、B 身份及共同环境/协议必须固定；混批结果标记 `mixed_batch_not_comparable` 并不输出正式CI。配对不足两对、有排除项或混批时CLI也返回2；错误迭代次数由参数解析直接报告。
