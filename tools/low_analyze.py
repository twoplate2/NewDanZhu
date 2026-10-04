"""python tools/low_analyze.py capture.json [capture.jsonl ...] --paired --split"""
import argparse
import json
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from danzhu.bench.low_metrics import DataError, analyze_sessions, load_sessions, paired_compare


def main(argv=None):
    parser = argparse.ArgumentParser(description="应用侧 Low 离线复算；无效记录保留原因，正式比较仅配对完整新schema。")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--split", action="store_true", help="按phase与shot_id拆分")
    parser.add_argument("--paired", action="store_true", help="按pair_id执行轮级B-A bootstrap")
    parser.add_argument("--metric", choices=("low_1pct_fps", "average_fps", "p95_ms", "p99_ms", "max_ms"), default="low_1pct_fps")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args(argv)
    if args.iterations < 100:
        parser.error("--iterations 必须至少为100")
    sessions = []
    for path in args.paths:
        try:
            loaded = load_sessions(path)
        except (OSError, UnicodeError) as exc:
            loaded = [dict(parse_errors=["read error: " + str(exc)])]
        for session in loaded:
            session["source_path"] = str(Path(path).resolve())
        sessions.extend(loaded)
    results = analyze_sessions(sessions, args.split)
    for result, session in zip(results, sessions):
        result["source_path"] = session["source_path"]
    report = dict(measurement="application_on_flip_interval_not_display_present", sessions=results)
    if args.paired:
        report["comparison"] = paired_compare(results, args.metric, args.iterations, args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    pair_ok = not args.paired or (report["comparison"]["ci95"] is not None and not report["comparison"]["excluded"])
    return 0 if pair_ok and all(r["valid"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
