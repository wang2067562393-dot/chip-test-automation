# -*- coding: utf-8 -*-
"""parse_test_log 的单元测试：用构造的小日志验证聚合逻辑。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_test_log import parse_log, to_markdown  # noqa: E402

LOG_A = """\
[2026-01-01 10:00:00] DUT=U1 FW=1.0.0 TEST=t1 RESULT=PASS err_mm=10 rssi_dbm=-70
[2026-01-01 10:00:01] DUT=U1 FW=1.0.0 TEST=t1 RESULT=PASS err_mm=20 rssi_dbm=-80
[2026-01-01 10:00:02] DUT=U1 FW=1.0.0 TEST=t1 RESULT=FAIL err_code=E_TIMEOUT rssi_dbm=-95
not a log line
[2026-01-01 10:00:03] DUT=U1 FW=1.0.0 RESULT=PASS
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.report, _ = parse_log(LOG_A)

    def test_counts_and_skip(self):
        self.assertEqual(self.report["summary"]["parsed_lines"], 3)
        self.assertEqual(self.report["summary"]["skipped_lines"], 2)

    def test_pass_rate(self):
        row = self.report["results"][0]
        self.assertEqual(row["dut"], "U1")
        self.assertEqual(row["firmware"], "1.0.0")
        self.assertEqual(row["test"], "t1")
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["passed"], 2)
        self.assertAlmostEqual(row["pass_rate"], 2 / 3, places=4)
        self.assertEqual(row["failure_codes"], {"E_TIMEOUT": 1})

    def test_error_stats(self):
        st = self.report["results"][0]["ranging_error_mm"]
        self.assertEqual(st["mean"], 15.0)
        # 样本标准差（n-1）：[10,20] -> sqrt(50)=7.07
        self.assertAlmostEqual(st["sample_std"], 7.07, places=2)
        self.assertEqual(st["max"], 20.0)

    def test_rssi(self):
        rs = self.report["results"][0]["rssi_dbm"]
        self.assertEqual(rs["min"], -95.0)
        self.assertAlmostEqual(rs["mean"], -81.67, places=1)

    def test_markdown_renders(self):
        md = to_markdown(self.report)
        self.assertIn("U1", md)
        self.assertIn("1.0.0", md)


if __name__ == "__main__":
    unittest.main()
