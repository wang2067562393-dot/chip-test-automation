#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare_fw.py —— 跨固件版本的测试结果配对对比。

把同一测试项在不同固件版本（如 1.2.2 vs 1.2.3）下的日志聚合结果放在一起，
输出通过率差值与样本量，避免"看了几眼感觉差不多"式的结论。

用法：
    python compare_fw.py examples/sample_run.log --md
    python compare_fw.py a.log b.log --json-out cmp.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# 复用同目录解析器
sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_test_log import parse_log  # noqa: E402


def merge_reports(log_paths: list[Path]) -> tuple[dict, dict]:
    """逐文件解析后合并，保留 (fw, test) 维度。"""
    merged: dict[tuple[str, str], dict] = defaultdict(lambda: {"total": 0, "passed": 0})
    parsed_total = 0
    skipped_total = 0
    fw_per_dut: dict[str, set] = defaultdict(set)
    for lp in log_paths:
        report, _ = parse_log(lp.read_text(encoding="utf-8", errors="replace"))
        parsed_total += report["summary"]["parsed_lines"]
        skipped_total += report["summary"]["skipped_lines"]
        for dut, fws in report["firmware_by_dut"].items():
            fw_per_dut[dut].update(fws)
        for row in report["results"]:
            key = (row.get("firmware", "?"), row["test"])
            m = merged[key]
            m["total"] += row["total"]
            m["passed"] += row["passed"]
            for fld in ("errors", "rssis"):
                pass  # 误差/RSSI 的跨文件合并在需要时再扩展，保持本工具聚焦通过率对比
    return merged, {"parsed": parsed_total, "skipped": skipped_total, "fw_per_dut": fw_per_dut}


def compare(merged: dict) -> list[dict]:
    """每个测试项：不同固件的通过率与差值（后一个固件减前一个）。"""
    by_test: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for (fw, test), row in merged.items():
        by_test[test].append((fw, row))
    out = []
    for test, items in sorted(by_test.items()):
        items.sort(key=lambda x: x[0])
        rates = []
        for fw, row in items:
            rate = row["passed"] / row["total"] if row["total"] else None
            rates.append({"firmware": fw, "total": row["total"], "passed": row["passed"], "pass_rate": rate})
        delta = None
        if len(rates) >= 2 and rates[0]["pass_rate"] is not None and rates[-1]["pass_rate"] is not None:
            delta = round(rates[-1]["pass_rate"] - rates[0]["pass_rate"], 4)
        out.append({"test": test, "groups": rates, "pass_rate_delta_new_minus_old": delta})
    return out


def to_markdown(cmp_rows: list[dict], meta: dict) -> str:
    lines = ["# 固件版本对比（自动生成）", ""]
    lines.append(
        f"- 共解析 {meta['parsed']} 条记录，跳过 {meta['skipped']} 条；"
        f"DUT-固件覆盖 {len(meta['fw_per_dut'])} 台设备"
    )
    lines.append("")
    lines.append("| 测试项 | 固件 | 总数 | 通过 | 通过率 | 新版本−旧版本 |")
    lines.append("|---|---|---|---|---|---|")
    for row in cmp_rows:
        for i, g in enumerate(row["groups"]):
            delta = ""
            if i == len(row["groups"]) - 1 and row["pass_rate_delta_new_minus_old"] is not None:
                d = row["pass_rate_delta_new_minus_old"]
                delta = f"{d:+.1%}"
            rate = f"{g['pass_rate']:.1%}" if g["pass_rate"] is not None else "-"
            lines.append(f"| {row['test'] if i == 0 else ''} | {g['firmware']} | {g['total']} | {g['passed']} | {rate} | {delta} |")
    lines.append("")
    lines.append("> 注意：通过率差值只有在同协议、同环境、样本量足够时才有参考意义；小样本请标注不确定性。")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--md-out", type=Path)
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()

    merged, meta = merge_reports(args.logs)
    rows = compare(merged)
    payload = {"summary": meta, "comparisons": rows}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(rows, meta)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.md or not args.json_out:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
