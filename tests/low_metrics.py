"""stdlib离线统计门禁：python tests/low_metrics.py"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from danzhu.bench.low_metrics import (SCHEMA, DataError, compute_metrics,
    quantile, load_sessions, analyze_session, analyze_sessions, paired_compare)


def session(sid="s1", condition="A", pair="p1", order=1, dt=10):
    return dict(schema=SCHEMA, meta=dict(session_id=sid, interval_unit="ms",
        identity=dict(app_id="test", version=condition, source_hash=condition,
                      config_hash="cfg", resource_hash="resource"),
        device={"model": "phone", "os": "16", "abi": "arm64"}, display={"hz": 100},
        settings={"vsync": True}, scenario={"version": 1, "signature": "fixed5"},
        mode="fixed_five", probe="light", protocol="warm-v1", pair_id=pair,
        condition=condition, run_order=order), events=[],
        frames=[dict(interval_ms=dt, flip_id=i, phase="flying", shot_id=1) for i in range(100)],
        summary=dict(complete=True, valid=True, frame_count=100, invalid_reasons=[]))


class MetricsTests(unittest.TestCase):
    def test_known_intervals(self):
        m = compute_metrics([10] * 97 + [20, 30, 40], 100)
        self.assertAlmostEqual(m["average_fps"], 100000 / 1060)
        self.assertEqual(m["low_1pct_fps"], 25)
        self.assertEqual(m["low_1pct_count"], 1)
        self.assertEqual(m["long_stalls"]["16.7"]["count"], 3)
        self.assertEqual(m["long_stalls"]["25"]["count"], 2)
        self.assertEqual(m["long_stalls"]["33.3"]["count"], 1)
        self.assertAlmostEqual(m["long_stalls"]["25"]["per_minute"], 120000/1060)
        self.assertEqual(m["over_budget_fraction"], .03)
        self.assertEqual(quantile([10, 20], .95), 19.5)

    def test_invalid_and_units(self):
        for values in ([], [0], [-1], [float("nan")], [float("inf")], [True], ["10"]):
            with self.subTest(values=values), self.assertRaises(DataError):
                compute_metrics(values)
        for mutate in (lambda s: s["meta"].update(interval_unit="s"),
                       lambda s: s["frames"][0].update(interval_ms=0),
                       lambda s: s["frames"][0].update(interval_s=.01),
                       lambda s: s["summary"].update(complete=False),
                       lambda s: s["summary"].update(lost_records=1)):
            s = session()
            mutate(s)
            self.assertFalse(analyze_session(s)["valid"])
        s = session()
        s["frames"][0]["interval_ms"] = float("nan")
        self.assertIsNone(analyze_session(s)["metrics"])

    def test_duplicate_export(self):
        result = analyze_sessions([session(), session()])
        self.assertTrue(all("duplicate_session_id" in r["invalid_reasons"] for r in result))
        s = session()
        s["frames"][1]["flip_id"] = 0
        self.assertIn("duplicate_flip_id", analyze_session(s)["invalid_reasons"])

    def test_malformed_containers(self):
        for target, key, value in (("summary", "invalid_reasons", 3),
                                   ("meta", "session_id", []),
                                   ("frames", "flip_id", [])):
            s = session()
            if target == "frames":
                s["frames"][0][key] = value
            else:
                s[target][key] = value
            self.assertFalse(analyze_sessions([s])[0]["valid"])
        s = session()
        s["parse_errors"] = 8
        self.assertFalse(analyze_session(s)["valid"])

    def test_missing_frame_and_timestamp(self):
        s = session()
        del s["frames"][20]
        s["summary"]["frame_count"] = 99
        self.assertIn("noncontiguous_flip_id", analyze_session(s)["invalid_reasons"])
        s = session()
        for i, f in enumerate(s["frames"]):
            f["monotonic_s"] = 123 + i * .01
        self.assertTrue(analyze_session(s)["valid"])
        s["frames"][5]["monotonic_s"] += .004
        self.assertIn("timestamp_interval_mismatch", analyze_session(s)["invalid_reasons"])

    def test_paired_order_and_identity(self):
        data = [session("b2", "B", "p2", 1, 8), session("a1", "A", "p1", 1),
                session("a2", "A", "p2", 2), session("b1", "B", "p1", 2, 8)]
        comparison = paired_compare(analyze_sessions(data), iterations=100)
        self.assertEqual(comparison["pair_count"], 2)
        self.assertEqual(comparison["difference_b_minus_a"], 25)
        self.assertEqual(comparison["ci95"], [25, 25])
        self.assertEqual(comparison["warnings"], [])
        self.assertEqual({p["order"] for p in comparison["pairs"]}, {"AB", "BA"})
        mixed = copy.deepcopy(data)
        mixed[0]["meta"]["identity"]["version"] = "unexpected-third-build"
        rejected = paired_compare(analyze_sessions(mixed), iterations=100)
        self.assertIsNone(rejected["ci95"])
        self.assertEqual(rejected["conclusion"], "mixed_batch_not_comparable")
        # 合法不同构建允许；同设备配置不匹配必须拒绝。
        data[0]["meta"]["settings"]["vsync"] = False
        comparison = paired_compare(analyze_sessions(data), iterations=100)
        self.assertEqual(comparison["pair_count"], 1)
        self.assertIn("metadata_mismatch:settings", comparison["excluded"][0]["reasons"])
        data[0]["meta"]["identity"].pop("source_hash")
        comparison = paired_compare(analyze_sessions(data), iterations=100)
        self.assertTrue(any("unverified_build_identity" in e["reasons"] for e in comparison["excluded"]))
        data[1]["meta"]["display"] = {"actual_hz": "unavailable"}
        comparison = paired_compare(analyze_sessions(data), iterations=100)
        self.assertTrue(any("unavailable_metadata:display" in e["reasons"] for e in comparison["excluded"]))

    def test_files_bad_line_legacy_and_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"capture.jsonl"
            s = session()
            lines = [json.dumps(dict(type="meta", schema=SCHEMA, meta=s["meta"]))]
            lines += [json.dumps(dict(type="frame", **f)) for f in s["frames"]]
            lines += [json.dumps(dict(type="summary", summary=s["summary"]))]
            path.write_text("\n".join(lines), encoding="utf-8")
            self.assertTrue(analyze_session(load_sessions(path)[0], True)["valid"])
            proc = subprocess.run([sys.executable, str(ROOT/"tools/low_analyze.py"), str(path), "--split"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["sessions"][0]["metrics"]["average_fps"], 100)
            paired = subprocess.run([sys.executable, str(ROOT/"tools/low_analyze.py"), str(path), "--paired"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(paired.returncode, 2)
            low_iterations = subprocess.run([sys.executable, str(ROOT/"tools/low_analyze.py"), str(path), "--iterations", "1"], capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(low_iterations.returncode, 0)
            self.assertNotIn("Traceback", low_iterations.stderr)
            path.write_text("\n".join(lines+[json.dumps(dict(type="event", name="late"))]), encoding="utf-8")
            self.assertIn("record_after_summary", analyze_session(load_sessions(path)[0])["invalid_reasons"])
            path.write_text("\n".join(lines[:-1]+["{bad", lines[-1]]), encoding="utf-8")
            r = analyze_session(load_sessions(path)[0])
            self.assertFalse(r["valid"])
            self.assertTrue(any("line 102" in e for e in r["invalid_reasons"]))
            txt = Path(temp)/"legacy.txt"
            txt.write_text("# 每行: 帧间隔毫秒,阶段\n10,飞行\n20,装杯\n# 序号,时间,版本\n1,2026-09-20,v1\n{}", encoding="utf-8")
            r = analyze_session(load_sessions(txt)[0])
            self.assertFalse(r["valid"])
            self.assertEqual(r["metrics"]["max_ms"], 20)
            self.assertEqual(r["metrics"]["frame_count"], 2)
            self.assertIn("legacy_completeness_unknown", r["invalid_reasons"])


if __name__ == "__main__":
    unittest.main()
