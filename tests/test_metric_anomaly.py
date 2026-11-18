# -*- coding: utf-8 -*-
"""metric_anomaly 的单元测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from metric_anomaly import load_series, mad_flags, three_sigma, detect  # noqa: E402

EX = Path(__file__).resolve().parents[1] / "examples" / "metrics.jsonl"


class TestStatBaselines(unittest.TestCase):
    def test_three_sigma_flags_outlier(self):
        xs = [10.0] * 10 + [100.0]
        mean, std, idx = three_sigma(xs, k=3)
        self.assertIn(10, idx)

    def test_mad_robust_to_outliers(self):
        # 3-sigma 会被极端值带偏；MAD 应稳定报出离群点
        xs = [10.0] * 20 + [100.0, 200.0]
        _, _, idx = mad_flags(xs, k=3)
        self.assertEqual(set(idx), {20, 21})

    def test_mad_zero_when_majority_equal(self):
        xs = [5.0, 5.0, 5.0, 9.0]
        med, scale, idx = mad_flags(xs, k=3)
        self.assertEqual(med, 5.0)
        self.assertEqual(idx, [3])

    def test_constant_series_no_sigma_flag(self):
        _, std, idx = three_sigma([7.0] * 5)
        self.assertEqual(std, 0.0)
        self.assertEqual(idx, [])

    def test_detect_on_example(self):
        rows = load_series(EX, "rssi_dbm")
        res = detect(rows, "rssi_dbm", "both")
        # -95 / -93 两个弱信号点应至少被一种方法抓到
        mad_n = res["methods"]["mad"]["anomaly_count"]
        sig_n = res["methods"]["three_sigma"]["anomaly_count"]
        self.assertGreaterEqual(mad_n + sig_n, 1)
        # err_mm 里 UWB002 的 95mm 是明显离群
        rows2 = load_series(EX, "err_mm")
        res2 = detect(rows2, "err_mm", "mad")
        values = [a["value"] for a in res2["methods"]["mad"]["anomalies"]]
        self.assertIn(95.0, values)


if __name__ == "__main__":
    unittest.main()
