#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_test_log.py —— UWB 测试日志自动解析与标准化报告。

纯标准库实现。输入：上位机导出的文本日志，每行一条测试结果。
输出：按 DUT × 测试项聚合的 JSON / Markdown 报告。

用法：
    python parse_test_log.py examples/sample_run.log --md
    python parse_test_log.py run.log --json-out report.json --md-out report.md
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# 形如：[2026-01-01 10:00:01] DUT=UWB001 FW=1.2.3 TEST=ranging RESULT=PASS ...
LINE_RE = re.compile(r"^\[(?P<ts>[\d\-: ]+)\]\s*(?P<body>.+)$")
KV_RE = re.compile(r"(\w+)=([^\s]+)")

REQUIRED = ("DUT", "FW", "TEST", "RESULT")


@dataclass
class Group:
    """一个 DUT × 测试项维度的聚合桶。"""

    total: int = 0
    passed: int = 0
    errors: list[float] = field(default_factory=list)  # err_mm
    rssis: list[float] = field(default_factory=list)
    err_codes: Counter = field(default_factory=Counter)

    def summarize(self) -> dict:
        n = self.total
        mean_err = sum(self.errors) / len(self.errors) if self.errors else None
        if len(self.errors) >= 2:
            var = sum((x - mean_err) ** 2 for x in self.errors) / (len(self.errors) - 1)
            std_err = math.sqrt(var)
        else:
            std_err = None
        return {
            "total": n,
            "passed": self.passed,
            "failed": n - self.passed,
            "pass_rate": round(self.passed / n, 4) if n else None,
            "ranging_error_mm": {
                "mean": round(mean_err, 2) if mean_err is not None else None,
                "sample_std": round(std_err, 2) if std_err is not None else None,
                "max": round(max(self.errors), 2) if self.errors else None,
                "n": len(self.errors),
            },
            "rssi_dbm": {
                "min": min(self.rssis) if self.rssis else None,
                "mean": round(sum(self.rssis) / len(self.rssis), 1) if self.rssis else None,
                "n": len(self.rssis),
            },
            "failure_codes": dict(self.err_codes),
        }


def parse_log(text: str) -> tuple[dict, dict]:
    groups: dict[tuple[str, str, str], Group] = defaultdict(Group)
    firmware: dict[str, set] = defaultdict(set)
    skipped = 0

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            skipped += 1
            continue
        fields = dict(KV_RE.findall(m.group("body")))
        if not all(k in fields for k in REQUIRED):
            skipped += 1
            continue

        dut, fw, test, result = (fields["DUT"], fields["FW"], fields["TEST"], fields["RESULT"].upper())
        firmware[dut].add(fw)
        g = groups[(dut, fw, test)]
        g.total += 1
        if result == "PASS":
            g.passed += 1
        else:
            g.err_codes[fields.get("err_code", "UNKNOWN")] += 1
        if "err_mm" in fields:
            try:
                g.errors.append(abs(float(fields["err_mm"])))
            except ValueError:
                pass
        if "rssi_dbm" in fields:
            try:
                g.rssis.append(float(fields["rssi_dbm"]))
            except ValueError:
                pass

    report = {
        "summary": {"parsed_lines": sum(g.total for g in groups.values()), "skipped_lines": skipped},
        "firmware_by_dut": {dut: sorted(fws) for dut, fws in firmware.items()},
        "results": [],
    }
    for (dut, fw, test), g in sorted(groups.items()):
        row = {"dut": dut, "firmware": fw, "test": test, **g.summarize()}
        report["results"].append(row)
    return report, {}


def to_markdown(report: dict) -> str:
    lines = ["# 测试报告（自动生成）", ""]
    s = report["summary"]
    lines.append(f"- 解析记录：**{s['parsed_lines']}** 条；跳过格式不符：{s['skipped_lines']} 条")
    lines.append("- 固件版本覆盖：")
    for dut, fws in report["firmware_by_dut"].items():
        lines.append(f"  - {dut}: {', '.join(fws)}")
    lines.append("")
    lines.append("| DUT | 固件 | 测试项 | 总数 | 通过 | 失败 | 通过率 | 误差均值(mm) | 误差σ | 最大误差 | RSSI min/均值 | 主要失败码 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in report["results"]:
        re_ = r["ranging_error_mm"]
        rs = r["rssi_dbm"]
        codes = ", ".join(f"{k}:{v}" for k, v in sorted(r["failure_codes"].items(), key=lambda x: -x[1])) or "-"
        lines.append(
            f"| {r['dut']} | {r['firmware']} | {r['test']} | {r['total']} | {r['passed']} | {r['failed']} | "
            f"{r['pass_rate']:.1%} | {re_['mean'] if re_['mean'] is not None else '-'} | "
            f"{re_['sample_std'] if re_['sample_std'] is not None else '-'} | "
            f"{re_['max'] if re_['max'] is not None else '-'} | "
            f"{rs['min'] if rs['min'] is not None else '-'}/{rs['mean'] if rs['mean'] is not None else '-'} | {codes} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path, help="测试日志文件")
    ap.add_argument("--json-out", type=Path, help="JSON 报告输出路径")
    ap.add_argument("--md-out", type=Path, help="Markdown 报告输出路径")
    ap.add_argument("--md", action="store_true", help="把 Markdown 报告打印到 stdout")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"日志不存在: {args.log}", file=sys.stderr)
        return 2

    report, _ = parse_log(args.log.read_text(encoding="utf-8", errors="replace"))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] JSON -> {args.json_out}")
    md = to_markdown(report)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
        print(f"[ok] Markdown -> {args.md_out}")
    if args.md or (not args.json_out and not args.md_out):
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
