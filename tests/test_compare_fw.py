# -*- coding: utf-8 -*-
"""compare_fw 的单元测试：跨固件通过率差值与多文件合并。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from compare_fw import compare, merge_reports  # noqa: E402

OLD = """\
[2026-01-01 10:00:00] DUT=U1 FW=1.0.0 TEST=t1 RESULT=PASS
[2026-01-01 10:00:01] DUT=U1 FW=1.0.0 TEST=t1 RESULT=FAIL err_code=E_TIMEOUT
"""
NEW = """\
[2026-01-02 10:00:00] DUT=U1 FW=1.0.1 TEST=t1 RESULT=PASS
[2026-01-02 10:00:01] DUT=U1 FW=1.0.1 TEST=t1 RESULT=PASS
"""


class TestCompare(unittest.TestCase):
    def test_merge_and_delta(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "old.log"
            b = Path(td) / "new.log"
            a.write_text(OLD, encoding="utf-8")
            b.write_text(NEW, encoding="utf-8")
            merged, meta = merge_reports([a, b])
        self.assertEqual(meta["parsed"], 4)
        rows = compare(merged)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["test"], "t1")
        fws = {g["firmware"] for g in row["groups"]}
        self.assertEqual(fws, {"1.0.0", "1.0.1"})
        # 1.0.0: 0.5, 1.0.1: 1.0 -> delta +0.5
        self.assertAlmostEqual(row["pass_rate_delta_new_minus_old"], 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
