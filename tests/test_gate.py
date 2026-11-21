# -*- coding: utf-8 -*-
"""gate 回归门的单元测试：通过、拦截、缺失、阈值边界。"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gate import evaluate, main  # noqa: E402


def _row(test="t1", total=100, passed=90, err=10.0, rssi=-70.0, include_metrics=True):
    row = {
        "dut": "U1",
        "firmware": "1.0.0",
        "test": test,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else None,
        "failure_codes": {},
    }
    if include_metrics:
        row["ranging_error_mm"] = {
            "mean": err,
            "sample_std": 1.0,
            "max": err + 2,
            "n": total,
        }
        row["rssi_dbm"] = {"min": rssi - 5, "mean": rssi, "n": total}
    else:
        row["ranging_error_mm"] = {"mean": None, "sample_std": None, "max": None, "n": 0}
        row["rssi_dbm"] = {"min": None, "mean": None, "n": 0}
    return row


def _report(*rows):
    return {
        "summary": {"parsed_lines": sum(r["total"] for r in rows), "skipped_lines": 0},
        "firmware_by_dut": {"U1": ["1.0.0"]},
        "results": list(rows),
    }


class TestGate(unittest.TestCase):
    def _run(self, baseline, candidate, threshold=5.0, fail_on_missing=False):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "baseline.json"
            c = Path(td) / "candidate.json"
            b.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
            c.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(
                    [
                        "--baseline",
                        str(b),
                        "--candidate",
                        str(c),
                        "--threshold",
                        str(threshold),
                        *(["--fail-on-missing"] if fail_on_missing else []),
                    ]
                )
        return rc, buf.getvalue()

    def test_below_threshold_passes(self):
        # 通过率 90% -> 88%，只降 2pp，应放行
        rc, out = self._run(_report(_row(passed=90)), _report(_row(passed=88)))
        self.assertEqual(rc, 0)
        self.assertIn("结论: PASS", out)
        self.assertNotIn("[FAIL]", out)

    def test_above_threshold_blocked_and_reports_metric(self):
        # 通过率 90% -> 80%，降 10pp，应拦截并指出具体指标/旧值/新值/超出量
        rc, out = self._run(_report(_row(passed=90)), _report(_row(passed=80)))
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL]", out)
        self.assertIn("test=t1", out)
        self.assertIn("metric=pass_rate", out)
        self.assertIn("old=90.00%", out)
        self.assertIn("new=80.00%", out)
        self.assertIn("drift=-10.00pp", out)
        self.assertIn("exceeded_by=5.00pp", out)

    def test_missing_metric_is_skipped_not_crash(self):
        # 候选报告里该测试项缺少误差/RSSI 均值：应 SKIP，默认不失败
        candidate = _report(_row(passed=90, include_metrics=False))
        rc, out = self._run(_report(_row(passed=90)), candidate)
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP]", out)
        self.assertIn("ranging_error_mean_mm", out)
        self.assertIn("结论: PASS", out)

    def test_missing_test_item_is_skipped(self):
        rc, out = self._run(
            _report(_row(test="t1", passed=90), _row(test="t2", passed=90)),
            _report(_row(test="t1", passed=90)),
        )
        self.assertEqual(rc, 0)
        self.assertIn("test=t2", out)
        self.assertIn("候选报告缺少该测试项", out)

    def test_threshold_boundary_is_inclusive(self):
        # 恰好 -5pp / +5pp：严格大于才失败，边界应放行
        rc_down, _ = self._run(_report(_row(passed=85)), _report(_row(passed=80)))
        rc_up, _ = self._run(_report(_row(passed=80)), _report(_row(passed=85)))
        self.assertEqual(rc_down, 0)
        self.assertEqual(rc_up, 0)

    def test_evaluate_returns_structured_failure(self):
        result = evaluate(_report(_row(passed=90)), _report(_row(passed=80)), 5.0)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(len(result["failures"]), 1)
        failure = result["failures"][0]
        self.assertEqual(failure["metric"], "pass_rate")
        self.assertAlmostEqual(failure["drift"], -10.0, places=6)
        self.assertAlmostEqual(failure["exceeded_by"], 5.0, places=6)


if __name__ == "__main__":
    unittest.main()
