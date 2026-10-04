"""应用侧 Low 的权威统计及可审计离线读取；仅依赖标准库。"""
import csv
import json
import math
import random
from pathlib import Path

SCHEMA = "danzhu.low.v1"


class DataError(ValueError):
    pass


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataError(label + ": expected numeric milliseconds")
    if not math.isfinite(value) or value <= 0:
        raise DataError(label + ": must be finite and positive")
    return float(value)


def quantile(values, q):
    """线性插值 (N-1)*q，与 Python inclusive/R type 7 一致。"""
    if not values or not 0 <= q <= 1:
        raise DataError("quantile requires samples and q in [0,1]")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def compute_metrics(intervals_ms, target_hz=None):
    values = [_positive(v, "interval_ms[%d]" % i)
              for i, v in enumerate(intervals_ms)]
    if not values:
        raise DataError("empty frame intervals")
    n = len(values)
    total = math.fsum(values)
    k = max(1, math.floor(n * .01))
    out = dict(frame_count=n, duration_ms=total,
               average_fps=1000 * n / total,
               low_1pct_fps=1000 * k / math.fsum(sorted(values)[-k:]),
               low_1pct_count=k, p95_ms=quantile(values, .95),
               p99_ms=quantile(values, .99), max_ms=max(values),
               quantile_method="linear_(n-1)*q", interval_unit="ms")
    out["long_stalls"] = {
        str(t): {"count": sum(v > t for v in values),
                 "per_minute": sum(v > t for v in values) * 60000 / total}
        for t in (16.7, 25, 33.3)}
    if target_hz is not None:
        hz = _positive(target_hz, "target_hz")
        out.update(target_hz=hz, budget_ms=1000 / hz,
                   over_budget_fraction=sum(v > 1000 / hz for v in values) / n)
    return out


def _new_session():
    return dict(schema=SCHEMA, meta={}, events=[], frames=[], summary={},
                parse_errors=[])


def load_sessions(path):
    """坏行保留为 parse_errors；从不悄悄跳过后当完整样本。"""
    path = Path(path)
    source = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".txt":
        return [_load_legacy(source, path)]
    if path.suffix.lower() == ".json":
        try:
            raw = json.loads(source)
        except (ValueError, TypeError) as exc:
            s = _new_session()
            s["parse_errors"].append("invalid JSON: " + str(exc))
            return [s]
        sessions = raw if isinstance(raw, list) else [raw]
        return [s if isinstance(s, dict) else
                dict(_new_session(), parse_errors=["session must be object"])
                for s in sessions]
    sessions, current = [], None
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record must be object")
        except ValueError as exc:
            if current is None:
                current = _new_session()
                sessions.append(current)
            current["parse_errors"].append("line %d: %s" % (number, exc))
            continue
        kind = record.get("type")
        if kind == "meta":
            if current is None or current["meta"]:
                current = _new_session()
                sessions.append(current)
            current["schema"] = record.get("schema")
            current["meta"] = record.get("meta", {k: v for k, v in record.items()
                                                    if k not in ("type", "schema")})
        else:
            if current is None:
                current = _new_session()
                sessions.append(current)
                current["parse_errors"].append("record before meta")
            if current["summary"]:
                current["parse_errors"].append("record_after_summary")
            if kind in ("frame", "event"):
                current[kind + "s"].append({k: v for k, v in record.items() if k != "type"})
            elif kind == "summary":
                if current["summary"]:
                    current["parse_errors"].append("duplicate summary")
                current["summary"] = record.get("summary", {k: v for k, v in record.items() if k != "type"})
            else:
                current["parse_errors"].append("line %d: unknown record type" % number)
    return sessions or [dict(_new_session(), parse_errors=["empty file"])]


def _load_legacy(source, path):
    s = _new_session()
    s["schema"] = "legacy.txt"
    s["meta"] = dict(session_id="legacy:" + str(path.resolve()), interval_unit="ms")
    s["summary"] = dict(complete=False, valid=False,
                         invalid_reasons=["legacy_uncalibrated", "legacy_completeness_unknown"])
    explicit_ms = any(line.startswith("# 每行:") and "帧间隔毫秒" in line
                      for line in source.splitlines())
    if not explicit_ms:
        s["meta"]["interval_unit"] = "unavailable"
    for number, line in enumerate(source.splitlines(), 1):
        # 历史导出尾部是另一个具名汇总表，不是逐帧记录。
        if line.startswith("# 序号,时间,") or (line.startswith("#") and "===== 跑分记录" in line):
            break
        if not line.strip() or line.startswith("#"):
            continue
        try:
            row = next(csv.reader([line]))
            if len(row) < 2:
                raise ValueError("expected interval,phase")
            s["frames"].append(dict(interval_ms=float(row[0]), phase=row[1]))
        except (ValueError, csv.Error) as exc:
            s["parse_errors"].append("line %d: %s" % (number, exc))
    return s


def analyze_session(session, split=False):
    errors = session.get("parse_errors", [])
    reasons = list(errors) if isinstance(errors, list) and all(isinstance(e, str) for e in errors) else ["malformed_parse_errors"]
    meta = session.get("meta", {})
    summary = session.get("summary", {})
    frames = session.get("frames", [])
    if not isinstance(meta, dict) or not isinstance(summary, dict) or not isinstance(frames, list):
        return dict(valid=False, invalid_reasons=reasons + ["malformed session containers"], metrics=None)
    invalid = summary.get("invalid_reasons", [])
    if isinstance(invalid, list) and all(isinstance(e, str) for e in invalid):
        reasons.extend(invalid)
    else:
        reasons.append("malformed_invalid_reasons")
    if session.get("schema") != SCHEMA:
        reasons.append("unsupported_or_legacy_schema")
    if not isinstance(meta.get("session_id"), str) or not meta.get("session_id"):
        reasons.append("missing_session_id")
    if meta.get("interval_unit") != "ms":
        reasons.append("interval_unit must explicitly be ms")
    if summary.get("complete") is not True:
        reasons.append("incomplete")
    if summary.get("valid") is not True:
        reasons.append("invalid_or_unspecified_validity")
    if summary.get("dropped_records", 0) or summary.get("lost_records", 0):
        reasons.append("dropped_records")
    if summary.get("frame_count") is not None and summary["frame_count"] != len(frames):
        reasons.append("frame_count_mismatch")
    intervals = []
    ids = set()
    previous_id = None
    previous_time = None
    for i, frame in enumerate(frames):
        try:
            if not isinstance(frame, dict):
                raise DataError("frame must be object")
            if frame.get("interval_unit", "ms") != "ms" or any(k in frame for k in ("interval_s", "interval_us", "dt")):
                raise DataError("mixed_or_ambiguous_interval_unit")
            intervals.append(_positive(frame.get("interval_ms"), "frame %d" % i))
            frame_id = frame.get("flip_id")
            if session.get("schema") == SCHEMA:
                if isinstance(frame_id, bool) or not isinstance(frame_id, int):
                    raise DataError("missing_or_invalid_flip_id")
                if previous_id is not None and frame_id != previous_id + 1:
                    reasons.append("noncontiguous_flip_id")
                previous_id = frame_id
            if frame_id is not None:
                if frame_id in ids:
                    raise DataError("duplicate_flip_id")
                ids.add(frame_id)
            timestamp = frame.get("monotonic_s")
            if timestamp is not None:
                if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                    raise DataError("invalid_monotonic_s")
                if previous_time is not None:
                    elapsed_ms = (timestamp - previous_time) * 1000
                    if elapsed_ms <= 0 or not math.isclose(elapsed_ms, frame["interval_ms"], rel_tol=1e-5, abs_tol=.001):
                        reasons.append("timestamp_interval_mismatch")
                previous_time = timestamp
        except (DataError, TypeError) as exc:
            reasons.append(str(exc))
    metrics = None
    # 即使仅一坏帧也不以剩余帧输出有效数字，避免误读。
    if len(intervals) == len(frames):
        try:
            metrics = compute_metrics(intervals, meta.get("target_hz"))
        except DataError as exc:
            reasons.append(str(exc))
    result = dict(session_id=meta.get("session_id"), valid=not reasons,
                  invalid_reasons=list(dict.fromkeys(reasons)), metrics=metrics, meta=meta)
    if split and metrics:
        result["segments"] = {}
        for key in ("phase", "shot_id"):
            groups = {}
            for f in frames:
                groups.setdefault(str(f.get(key, "unavailable")), []).append(f["interval_ms"])
            result["segments"][key] = {k: compute_metrics(v) for k, v in groups.items()}
    return result


def analyze_sessions(sessions, split=False):
    results = [analyze_session(s, split) for s in sessions]
    seen = set()
    duplicate = set()
    for r in results:
        sid = r.get("session_id")
        if not isinstance(sid, str):
            continue
        if sid in seen:
            duplicate.add(sid)
        seen.add(sid)
    for r in results:
        if isinstance(r.get("session_id"), str) and r["session_id"] in duplicate:
            r["valid"] = False
            r["invalid_reasons"].append("duplicate_session_id")
    return results


def paired_compare(results, metric="low_1pct_fps", iterations=10000, seed=20260927):
    """以独立轮配对 B-A 自举；保留排除原因，不产生等价结论。"""
    if iterations < 100:
        raise DataError("bootstrap iterations must be >=100")
    pairs, excluded = {}, []
    def unavailable(value):
        if value is None or value == "unavailable" or value == "":
            return True
        if isinstance(value, dict):
            return not value or any(unavailable(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return not value or any(unavailable(v) for v in value)
        return isinstance(value, float) and not math.isfinite(value)
    for r in results:
        m = dict(r.get("meta", {}))
        m.setdefault("scene", m.get("scenario"))
        m.setdefault("sampling_mode", m.get("mode"))
        # 归一化仅用于配对核验；原始meta保持可审计。
        r = dict(r, meta=m)
        reason = list(r.get("invalid_reasons", []))
        for key in ("pair_id", "condition", "run_order", "identity", "device", "display", "settings", "scene", "sampling_mode", "probe", "protocol"):
            if key not in m or m[key] in (None, "", "unavailable", {}):
                reason.append("missing_metadata:" + key)
        for key in ("device", "display", "settings", "scene", "probe", "protocol"):
            if unavailable(m.get(key)):
                reason.append("unavailable_metadata:" + key)
        identity = m.get("identity", {})
        if not isinstance(identity, dict) or any(identity.get(k) in (None, "", "unavailable") for k in ("app_id", "version", "source_hash", "config_hash", "resource_hash")):
            reason.append("unverified_build_identity")
        scene = m.get("scene", {})
        if not isinstance(scene, dict) or not scene.get("version") or not scene.get("signature"):
            reason.append("unverified_scene_identity")
        if m.get("condition") not in ("A", "B"):
            reason.append("condition_must_be_A_or_B")
        if reason:
            excluded.append(dict(session_id=r.get("session_id"), reasons=reason))
            continue
        pairs.setdefault(str(m["pair_id"]), []).append(r)
    differences, orders, accepted = [], {}, []
    batch_signatures = set()
    for pid, members in sorted(pairs.items()):
        reasons = []
        if len(members) != 2 or {r["meta"]["condition"] for r in members} != {"A", "B"}:
            reasons.append("pair_requires_exactly_one_A_and_B")
        else:
            a, b = sorted(members, key=lambda r: r["meta"]["condition"])
            for key in ("device", "display", "settings", "scene", "sampling_mode", "probe", "protocol"):
                if a["meta"][key] != b["meta"][key]:
                    reasons.append("metadata_mismatch:" + key)
            oa, ob = a["meta"]["run_order"], b["meta"]["run_order"]
            if isinstance(oa, bool) or isinstance(ob, bool) or not isinstance(oa, int) or not isinstance(ob, int) or oa == ob:
                reasons.append("run_order_requires_distinct_integers")
            if not reasons:
                try:
                    diff = b["metrics"][metric] - a["metrics"][metric]
                except (TypeError, KeyError):
                    reasons.append("missing_metric:" + metric)
                else:
                    order = "AB" if oa < ob else "BA"
                    orders.setdefault(order, []).append(diff)
                    differences.append(diff)
                    batch_signatures.add(json.dumps({
                        "A": a["meta"]["identity"], "B": b["meta"]["identity"],
                        "common": {key: a["meta"][key] for key in
                                   ("device", "display", "settings", "scene", "sampling_mode", "probe", "protocol")}},
                        sort_keys=True, ensure_ascii=True))
                    accepted.append(dict(pair_id=pid, difference_b_minus_a=diff, order=order,
                                         identities={"A": a["meta"]["identity"], "B": b["meta"]["identity"]}))
        if reasons:
            excluded.append(dict(pair_id=pid, reasons=reasons))
    n = len(differences)
    out = dict(metric=metric, pair_count=n, pairs=accepted, excluded=excluded,
               difference_b_minus_a=None, ci95=None, bootstrap_seed=seed,
               bootstrap_iterations=iterations, unit="FPS" if metric.endswith("fps") else "ms",
               conclusion="insufficient_pairs", order_means={k: math.fsum(v)/len(v) for k,v in orders.items()})
    if n:
        out["difference_b_minus_a"] = math.fsum(differences) / n
    mixed_batch = len(batch_signatures) > 1
    if n >= 2 and not mixed_batch:
        rng = random.Random(seed)
        draws = [math.fsum(rng.choice(differences) for _ in range(n))/n for _ in range(iterations)]
        out["ci95"] = [quantile(draws, .025), quantile(draws, .975)]
        out["conclusion"] = "estimate_with_uncertainty_not_equivalence"
    out["warnings"] = (["unbalanced_or_single_order"] if len(orders) < 2 or
                       len(orders.get("AB", [])) != len(orders.get("BA", [])) else [])
    if mixed_batch:
        out["warnings"].append("mixed_batch_identity_or_protocol")
        out["conclusion"] = "mixed_batch_not_comparable"
    return out
