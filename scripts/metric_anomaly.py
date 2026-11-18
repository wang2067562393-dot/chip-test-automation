#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metric_anomaly.py —— 测试指标的统计异常检测（AI4Test 第一步，纯标准库）。

输入 parse_test_log.py 产出的逐次测量 JSONL（每行含 err_mm / rssi_dbm 等数值），
用两类无需训练的基线找异常点：

  1) 3-sigma：   |x - mean| > k * std      （经典，但怕异常值污染均值/方差）
  2) MAD 稳健法： |x - median| > k * 1.4826 * MAD（中位数/MAD 不被离群点带偏，工程更常用）

为什么从统计基线开始而不是直接上深度学习：
  - 可解释：每条告警能写出"偏离中位数多少倍"，测试报告里站得住；
  - 无需标注数据和训练，当天就能接入真实流程；
  - 它是后续 IsolationForest / 自编码器的对照组（baseline of baselines）。

用法：
  python metric_anomaly.py examples/metrics.jsonl --metric err_mm --method both --md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def load_series(path: Path, metric: str) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rec = json.loads(line)
        if metric in rec and isinstance(rec[metric], (int, float)):
            rec["_idx"] = i
            rows.append(rec)
    if not rows:
        raise SystemExit(f"没有找到含字段 {metric!r} 的数值记录")
    return rows


def three_sigma(xs: list[float], k: float = 3.0) -> tuple[float, float, list[int]]:
    mean = statistics.fmean(xs)
    std = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    if std == 0:
        return mean, std, []
    return mean, std, [i for i, x in enumerate(xs) if abs(x - mean) > k * std]


def mad_flags(xs: list[float], k: float = 3.0) -> tuple[float, float, list[int]]:
    """返回 (中位数, 阈值, 异常下标)。MAD=0 时退化为"与中位数不同即异常"由上层决定。"""
    med = statistics.median(xs)
    mad = statistics.median([abs(x - med) for x in xs])
    if mad == 0:
        # 超过一半点完全相同：任何偏离中位数的点都报出（用 0 阈值）
        return med, 0.0, [i for i, x in enumerate(xs) if x != med]
    scale = 1.4826 * mad  # 使 MAD 在正态分布下与标准差同尺度
    return med, scale, [i for i, x in enumerate(xs) if abs(x - med) > k * scale]


def detect(rows: list[dict], metric: str, method: str = "both", k: float = 3.0) -> dict:
    xs = [float(r[metric]) for r in rows]
    result: dict = {"metric": metric, "n": len(xs), "k": k, "methods": {}}
    if method in ("sigma", "both"):
        mean, std, idx = three_sigma(xs, k)
        result["methods"]["three_sigma"] = {
            "mean": round(mean, 4), "std": round(std, 4),
            "anomaly_count": len(idx),
            "anomalies": [{"idx": rows[i]["_idx"], "value": xs[i],
                           **{kk: rows[i][kk] for kk in ("DUT", "FW", "TEST") if kk in rows[i]}}
                          for i in idx],
        }
    if method in ("mad", "both"):
        med, scale, idx = mad_flags(xs, k)
        result["methods"]["mad"] = {
            "median": round(med, 4), "robust_scale": round(scale, 4),
            "anomaly_count": len(idx),
            "anomalies": [{"idx": rows[i]["_idx"], "value": xs[i],
                           "deviation_in_scale": round(abs(xs[i] - med) / scale, 1) if scale else None,
                           **{kk: rows[i][kk] for kk in ("DUT", "FW", "TEST") if kk in rows[i]}}
                          for i in idx],
        }
    return result


def to_markdown(res: dict) -> str:
    lines = [f"# 指标异常检测：{res['metric']}", "", f"- 样本量 n={res['n']}，阈值 k={res['k']}", ""]
    for name, m in res["methods"].items():
        center = m.get("mean", m.get("median"))
        spread = m.get("std", m.get("robust_scale"))
        lines.append(f"## {name}（中心={center}，散布={spread}）")
        lines.append(f"异常点：**{m['anomaly_count']}** 个")
        if m["anomalies"]:
            lines.append("")
            lines.append("| 行号 | 值 | 偏离(倍尺度) | DUT | 固件 | 测试项 |")
            lines.append("|---|---|---|---|---|---|")
            for a in m["anomalies"]:
                dev = a.get("deviation_in_scale", "-")
                lines.append(f"| {a['idx']} | {a['value']} | {dev} | {a.get('DUT','-')} | {a.get('FW','-')} | {a.get('TEST','-')} |")
        lines.append("")
    lines.append("> 统计告警是「值得人工复查」的信号，不等于产品不合格；阈值应结合现场误报率回调。")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("--metric", required=True, help="数值字段名，如 err_mm / rssi_dbm")
    ap.add_argument("--method", choices=["sigma", "mad", "both"], default="both")
    ap.add_argument("--k", type=float, default=3.0)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()

    rows = load_series(args.input, args.metric)
    res = detect(rows, args.metric, args.method, args.k)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        print(to_markdown(res))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
