#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate.py —— 测试报告回归门（chiptest gate，纯标准库）。

比较两份 parse_test_log.py 产出的 JSON 报告，按测试项聚合后检查指标漂移：
  * pass_rate            通过率，单位百分点（pp），默认阈值 5 表示最多允许 ±5pp；
  * ranging_error_mean_mm 测距误差均值，单位相对变化百分比（%）；
  * rssi_mean_dbm        RSSI 均值，单位 dB（绝对漂移）。

漂移绝对值**严格大于**阈值才判定失败（恰好等于阈值视为通过），便于把
"阈值 5" 理解成包含边界的 5pp 容差。

缺失处理：
  * 某个测试项只出现在一边、或某个指标在任一侧为 null/缺字段，记为 SKIP 并
    打印原因，默认**不让 gate 失败**（可用 --fail-on-missing 改为失败），
    避免报告格式演进时误伤 CI。

退出码：0 = 通过（允许存在 SKIP），1 = 有指标超阈值，2 = 输入/格式错误。

用法：
    python scripts/gate.py --baseline old_report.json --candidate new_report.json --threshold 5
    python scripts/gate.py --baseline old.json --candidate new.json --threshold 5 --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# mode: pp = 百分点绝对漂移；relative_pct = 相对变化百分比；absolute = 原单位绝对漂移
METRIC_SPECS = [
    {"key": "pass_rate", "label": "pass_rate", "unit": "pp", "mode": "pp", "digits": 2},
    {
        "key": "ranging_error_mean_mm",
        "label": "ranging_error_mean_mm",
        "unit": "%",
        "mode": "relative_pct",
        "digits": 2,
    },
    {"key": "rssi_mean_dbm", "label": "rssi_mean_dbm", "unit": "dB", "mode": "absolute", "digits": 2},
]

_EPS = 1e-9


def _as_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def aggregate_by_test(report: dict) -> dict[str, dict]:
    """把 DUT × FW × TEST 的行按 TEST 汇总，得到每个测试项的样本与指标。"""
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("报告缺少 results 数组，无法作为 parse_test_log 报告读取")

    agg: dict[str, dict] = {}
    for row in report["results"]:
        if not isinstance(row, dict):
            continue
        test = row.get("test")
        if not test:
            continue
        bucket = agg.setdefault(
            test,
            {
                "total": 0,
                "passed": 0,
                "err_sum": 0.0,
                "err_n": 0,
                "rssi_sum": 0.0,
                "rssi_n": 0,
            },
        )
        total = _as_number(row.get("total"))
        passed = _as_number(row.get("passed"))
        if total is not None:
            bucket["total"] += int(total)
        if passed is not None:
            bucket["passed"] += int(passed)

        err = row.get("ranging_error_mm") or {}
        if isinstance(err, dict):
            mean = _as_number(err.get("mean"))
            n = _as_number(err.get("n"))
            if mean is not None and n:
                bucket["err_sum"] += mean * int(n)
                bucket["err_n"] += int(n)

        rssi = row.get("rssi_dbm") or {}
        if isinstance(rssi, dict):
            mean = _as_number(rssi.get("mean"))
            n = _as_number(rssi.get("n"))
            if mean is not None and n:
                bucket["rssi_sum"] += mean * int(n)
                bucket["rssi_n"] += int(n)

    for bucket in agg.values():
        total = bucket["total"]
        bucket["pass_rate"] = (bucket["passed"] / total) if total else None
        bucket["ranging_error_mean_mm"] = (
            bucket["err_sum"] / bucket["err_n"] if bucket["err_n"] else None
        )
        bucket["rssi_mean_dbm"] = (
            bucket["rssi_sum"] / bucket["rssi_n"] if bucket["rssi_n"] else None
        )
    return agg


def compute_drift(spec: dict, old: float, new: float) -> float | None:
    """按指标口径算有符号漂移；无法计算时返回 None。"""
    if spec["mode"] == "pp":
        return (new - old) * 100.0
    if spec["mode"] == "relative_pct":
        if abs(old) < _EPS:
            return 0.0 if abs(new) < _EPS else None
        return (new - old) / abs(old) * 100.0
    return new - old


def _format_value(spec: dict, value: float | None) -> str:
    if value is None:
        return "null"
    if spec["key"] == "pass_rate":
        return f"{value * 100:.2f}%"
    if spec["key"] == "ranging_error_mean_mm":
        return f"{value:.2f}mm"
    return f"{value:.2f}dBm"


def _format_drift(spec: dict, value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value:+.{spec['digits']}f}{spec['unit']}"


def evaluate(baseline: dict, candidate: dict, threshold: float, fail_on_missing: bool = False) -> dict:
    """核心比较逻辑：返回 {comparisons, missing, failures, verdict}。"""
    if threshold < 0:
        raise ValueError("--threshold 必须 >= 0")

    base_agg = aggregate_by_test(baseline)
    cand_agg = aggregate_by_test(candidate)
    all_tests = sorted(set(base_agg) | set(cand_agg))

    comparisons: list[dict] = []
    missing: list[dict] = []
    failures: list[dict] = []

    for test in all_tests:
        if test not in cand_agg:
            item = {"test": test, "reason": "候选报告缺少该测试项", "severity": "missing"}
            missing.append(item)
            continue
        if test not in base_agg:
            item = {"test": test, "reason": "仅候选报告存在该测试项（新增项不判回归）", "severity": "info"}
            missing.append(item)
            continue

        for spec in METRIC_SPECS:
            old = base_agg[test].get(spec["key"])
            new = cand_agg[test].get(spec["key"])
            if old is None and new is None:
                # 两侧都没有该指标：该测试项结构上不产生这个指标（如 tx_power 无 err_mm），
                # 不算缺失，直接不适用。
                continue
            item = {
                "test": test,
                "metric": spec["label"],
                "unit": spec["unit"],
                "threshold": threshold,
                "old": old,
                "new": new,
            }
            if old is None or new is None:
                item.update(
                    {
                        "status": "missing",
                        "reason": "基线或候选缺少数值/null",
                        "severity": "missing",
                        "exceeded": False,
                    }
                )
                missing.append(item)
                continue
            drift = compute_drift(spec, old, new)
            if drift is None:
                item.update(
                    {
                        "status": "missing",
                        "reason": "基线均值为 0，无法计算相对漂移",
                        "severity": "missing",
                        "exceeded": False,
                    }
                )
                missing.append(item)
                continue
            drift = round(drift, 6)
            exceeded = abs(drift) > threshold + _EPS
            item.update(
                {
                    "status": "fail" if exceeded else "pass",
                    "drift": drift,
                    "exceeded": exceeded,
                    "exceeded_by": round(abs(drift) - threshold, 6) if exceeded else 0.0,
                }
            )
            comparisons.append(item)
            if exceeded:
                failures.append(item)

    has_missing = any(m.get("severity") == "missing" for m in missing)
    verdict = "fail" if failures or (fail_on_missing and has_missing) else "pass"
    return {
        "verdict": verdict,
        "threshold": threshold,
        "tests_compared": len(set(base_agg) & set(cand_agg)),
        "metrics_checked": len(comparisons),
        "comparisons": comparisons,
        "missing": missing,
        "failures": failures,
    }


def _print_result(result: dict, verbose: bool = False) -> None:
    print(f"阈值: {result['threshold']:g}（各指标绝对值严格大于阈值才失败）")
    print(
        f"测试项: 共同 {result['tests_compared']} 个；可比指标 {result['metrics_checked']} 个；"
        f"缺失/跳过 {len(result['missing'])} 项"
    )
    print("")
    for item in result["comparisons"]:
        if item["status"] == "pass" and not verbose:
            continue
        spec = _spec_of(item["metric"])
        old = _format_value(spec, item["old"])
        new = _format_value(spec, item["new"])
        if item["status"] == "fail":
            print(
                f"[FAIL] test={item['test']} metric={item['metric']} "
                f"old={old} new={new} drift={_format_drift(spec, item['drift'])} "
                f"threshold={result['threshold']:g}{item['unit']} "
                f"exceeded_by={item['exceeded_by']:.2f}{item['unit']}"
            )
        else:
            print(
                f"[PASS] test={item['test']} metric={item['metric']} "
                f"old={old} new={new} drift={_format_drift(spec, item['drift'])}"
            )
    for item in result["missing"]:
        if "metric" in item:
            print(
                f"[SKIP] test={item['test']} metric={item['metric']} "
                f"old={_format_value(_spec_of(item['metric']), item['old'])} "
                f"new={_format_value(_spec_of(item['metric']), item['new'])} "
                f"reason={item['reason']}"
            )
        else:
            print(f"[SKIP] test={item['test']} reason={item['reason']}")
    print("")
    if result["verdict"] == "fail":
        if result["failures"]:
            print(f"结论: FAIL —— {len(result['failures'])} 个指标漂移超阈值")
        else:
            print(f"结论: FAIL —— {len(result['missing'])} 项测试项/指标缺失（--fail-on-missing）")
    elif result["missing"]:
        print(f"结论: PASS（有 {len(result['missing'])} 项缺失/跳过，未计入失败）")
    else:
        print("结论: PASS —— 所有可比指标均在阈值内")


def _spec_of(label: str) -> dict:
    for spec in METRIC_SPECS:
        if spec["label"] == label:
            return spec
    return {"key": label, "label": label, "unit": "", "digits": 2}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", type=Path, required=True, help="旧报告 JSON（parse_test_log --json-out）")
    ap.add_argument("--candidate", type=Path, required=True, help="新报告 JSON")
    ap.add_argument("--threshold", type=float, default=5.0, help="漂移阈值（默认 5）")
    ap.add_argument("--fail-on-missing", action="store_true", help="把测试项/指标缺失也判为失败")
    ap.add_argument("--verbose", action="store_true", help="同时打印未超阈值的指标")
    ap.add_argument("--json-out", type=Path, help="可选：把 gate 结果写成 JSON")
    args = ap.parse_args(argv)

    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"读取报告失败: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"报告不是合法 JSON: {exc}", file=sys.stderr)
        return 2

    try:
        result = evaluate(baseline, candidate, args.threshold, args.fail_on_missing)
    except ValueError as exc:
        print(f"报告格式错误: {exc}", file=sys.stderr)
        return 2

    print(f"基线: {args.baseline}")
    print(f"候选: {args.candidate}")
    _print_result(result, args.verbose)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "baseline": str(args.baseline),
            "candidate": str(args.candidate),
            **result,
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[ok] JSON -> {args.json_out}")
    return 1 if result["verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
