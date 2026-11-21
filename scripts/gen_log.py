#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_log.py —— 合成芯片测试日志生成器（纯标准库，无第三方依赖）。

输出格式与 examples/sample_run.log 一致，可直接交给 parse_test_log.py 解析：

    [2026-11-21 08:00:00] DUT=UWB001 FW=1.2.3 TEST=ranging_ss_twr RESULT=PASS dist_m=1.0 err_mm=8 rssi_dbm=-68

造数原则（不是纯均匀随机）：
  * 多测试项，按真实现场的大致占比抽样（测距为主，rx/tx/功耗/温度/SPI 为辅）；
  * 每个测试项有各自的通过率，失败带 err_code，FAIL 与测量值大致自洽；
  * 少量超差样本（远距离测距误差可达数百毫米）与弱信号样本；
  * 少量格式异常行（缺字段 / 坏时间戳 / 乱码），用于验证解析器跳过计数；
  * 固定 --seed 后输出逐字节可复现（Python 标准库 random，同一版本内稳定）。

用法：
    python scripts/gen_log.py --lines 2000 --out D:/deepwork/tools/data/smoke.log
    python scripts/gen_log.py --lines 1000000 --out D:/deepwork/tools/data/synthetic-1m.log --seed 20261121
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# 测试项画像：权重（现场占比）、标称通过率、测量值类型。
# 通过率会在 FW 质量因子基础上再做上下浮动，避免所有 DUT 一模一样。
TEST_PROFILES = [
    {"name": "ranging_ss_twr", "weight": 0.34, "pass_rate": 0.964, "kind": "ranging"},
    {"name": "ranging_ds_twr", "weight": 0.12, "pass_rate": 0.951, "kind": "ranging"},
    {"name": "rx_sensitivity", "weight": 0.16, "pass_rate": 0.908, "kind": "rx"},
    {"name": "tx_power", "weight": 0.10, "pass_rate": 0.962, "kind": "tx"},
    {"name": "sleep_current", "weight": 0.08, "pass_rate": 0.931, "kind": "sleep"},
    {"name": "temp_sensor", "weight": 0.08, "pass_rate": 0.972, "kind": "temp"},
    {"name": "spi_loopback", "weight": 0.12, "pass_rate": 0.986, "kind": "spi"},
]

# 固件版本：老版本通过率略低，新版本略优；混跑比例模拟现场升级过渡期。
FIRMWARE_PROFILES = [
    {"name": "1.2.2", "weight": 0.30, "quality": 0.982},
    {"name": "1.2.3", "weight": 0.52, "quality": 1.000},
    {"name": "1.3.0", "weight": 0.18, "quality": 0.994},
]

FAIL_CODES = {
    "ranging": [("E_TIMEOUT", 0.48), ("E_RANGE", 0.34), ("E_SYNC", 0.18)],
    "rx": [("E_CRC", 0.40), ("E_TIMEOUT", 0.32), ("E_SENS", 0.28)],
    "tx": [("E_POWER", 0.56), ("E_CAL", 0.44)],
    "sleep": [("E_CURRENT", 0.70), ("E_TIMEOUT", 0.30)],
    "temp": [("E_TEMP", 0.62), ("E_CRC", 0.38)],
    "spi": [("E_IO", 0.55), ("E_CRC", 0.45)],
}

DISTANCES = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0]

# 异常行模板：每一类都必然无法被 parse_test_log 解析（缺必需字段或时间戳不合法）。
MALFORMED_TEMPLATES = [
    "[{ts}] DUT={dut} FW={fw} RESULT=PASS err_mm=12",          # 缺 TEST
    "!!! corrupted record at offset {offset} !!!",             # 完全不是日志
    "[{ts}] DUT={dut} TEST={test} RESULT=FAIL err_code=E_TIMEOUT",  # 缺 FW
    "[{ts} DUT={dut} FW={fw} TEST={test} RESULT=PASS",         # 方括号不闭合
    "[{ts}] FW={fw} TEST={test} RESULT=PASS",                  # 缺 DUT
    "[not-a-timestamp] DUT={dut} FW={fw} TEST={test} RESULT=FAIL",  # 坏时间戳
    "LOG TRUNCATED mid-record: DUT={dut} FW={fw} TEST={test}", # 截断行
]


def _weighted_choice(rng: random.Random, items: list[dict], key: str = "weight") -> dict:
    total = sum(x[key] for x in items)
    r = rng.random() * total
    upto = 0.0
    for x in items:
        upto += x[key]
        if r <= upto:
            return x
    return items[-1]


def _fail_code(rng: random.Random, kind: str) -> str:
    pairs = FAIL_CODES[kind]
    r = rng.random()
    upto = 0.0
    for code, w in pairs:
        upto += w
        if r <= upto:
            return code
    return pairs[-1][0]


def _ranging_fields(rng: random.Random, passed: bool) -> tuple[list[str], bool]:
    """测距项：返回 (字段列表, 是否本次额外注入了大超差样本)。"""
    dist = rng.choice(DISTANCES)
    outlier = False
    if passed:
        # 误差随距离增大；多数在 ±30 mm 内。
        err = rng.gauss(0.0, 5.0 + 2.4 * dist)
        rssi = rng.randint(-84, -62)
        fields = [f"dist_m={dist}", f"err_mm={err:.1f}", f"rssi_dbm={rssi}"]
        # 极少量"超差但被判 PASS"的记录：模拟人工复测/判定口径差异。
        if rng.random() < 0.0006:
            err = rng.choice([-1, 1]) * rng.uniform(120.0, 700.0)
            fields[1] = f"err_mm={err:.1f}"
            outlier = True
        return fields, outlier

    code = _fail_code(rng, "ranging")
    if code == "E_TIMEOUT":
        fields = [f"dist_m={dist}", f"rssi_dbm={rng.randint(-96, -88)}"]
    elif code == "E_RANGE":
        err = rng.choice([-1, 1]) * rng.uniform(80.0, 900.0)
        fields = [f"dist_m={dist}", f"err_mm={err:.1f}", f"rssi_dbm={rng.randint(-92, -80)}"]
        outlier = abs(err) >= 300.0
    else:
        fields = [f"dist_m={dist}", f"rssi_dbm={rng.randint(-95, -85)}"]
    fields.append(f"err_code={code}")
    return fields, outlier


def _build_valid_line(
    rng: random.Random, ts: str, dut: str, fw: str, test: dict, fw_quality: float
) -> tuple[str, bool, bool]:
    """返回 (日志行, 是否 PASS, 是否大超差样本)。"""
    p_pass = min(0.999, max(0.80, test["pass_rate"] * fw_quality))
    passed = rng.random() < p_pass
    kind = test["kind"]
    outlier = False

    if kind == "ranging":
        fields, outlier = _ranging_fields(rng, passed)
    elif kind == "rx":
        if passed:
            rssi = rng.randint(-88, -76)
        else:
            rssi = rng.randint(-100, -89)
        fields = [f"rssi_dbm={rssi}"]
        if not passed:
            fields.append(f"err_code={_fail_code(rng, 'rx')}")
    elif kind == "tx":
        pwr = rng.uniform(8.6, 11.9)
        fields = [f"pwr_dbm={pwr:.2f}", "freq_mhz=6489.6"]
        if not passed:
            fields.append(f"err_code={_fail_code(rng, 'tx')}")
    elif kind == "sleep":
        if passed:
            current = rng.uniform(1.8, 6.8)
        else:
            current = rng.uniform(12.0, 48.0)
        fields = [f"current_ua={current:.1f}"]
        if not passed:
            fields.append(f"err_code={_fail_code(rng, 'sleep')}")
    elif kind == "temp":
        temp = rng.uniform(23.5, 43.0)
        fields = [f"temp_c={temp:.1f}"]
        if not passed:
            fields.append(f"err_code={_fail_code(rng, 'temp')}")
    else:  # spi
        crc_errors = 0 if passed else rng.randint(1, 8)
        fields = [f"bytes_ok={rng.choice([128, 256, 512])}", f"crc_errors={crc_errors}"]
        if not passed:
            fields.append(f"err_code={_fail_code(rng, 'spi')}")

    result = "PASS" if passed else "FAIL"
    parts = [f"DUT={dut}", f"FW={fw}", f"TEST={test['name']}", f"RESULT={result}", *fields]
    return f"[{ts}] " + " ".join(parts), passed, outlier


def generate(lines: int, out_path: Path, seed: int, malformed_rate: float, start: str) -> dict:
    if lines < 0:
        raise ValueError("--lines 必须 >= 0")
    if not 0.0 <= malformed_rate < 0.5:
        raise ValueError("--malformed-rate 必须在 [0, 0.5) 内")

    rng = random.Random(seed)
    base = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")

    n_duts = 32
    duts = [f"UWB{i:03d}" for i in range(1, n_duts + 1)]
    # 每台设备固定一个固件版本，符合"一次产线批次用同一 FW"的现场习惯。
    dut_fw: dict[str, str] = {}
    for i, dut in enumerate(duts):
        prof = _weighted_choice(rng, FIRMWARE_PROFILES)
        dut_fw[dut] = prof["name"]
    fw_quality = {p["name"]: p["quality"] for p in FIRMWARE_PROFILES}

    n_malformed = int(round(lines * malformed_rate))
    malformed_idx = set(rng.sample(range(lines), n_malformed)) if n_malformed else set()

    stats = {
        "tests": Counter(),
        "firmware": Counter(),
        "pass": 0,
        "fail": 0,
        "outliers": 0,
        "malformed": 0,
        "parsed_expected": 0,
    }

    t0 = time.perf_counter()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunk: list[str] = []
    ts = base
    last_second = -1

    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for i in range(lines):
            if i in malformed_idx:
                dut = rng.choice(duts)
                fw = dut_fw[dut]
                tmpl = rng.choice(MALFORMED_TEMPLATES)
                line = tmpl.format(
                    ts=ts.strftime("%Y-%m-%d %H:%M:%S"),
                    offset=rng.randint(1 << 20, 1 << 30),
                    dut=dut,
                    fw=fw,
                    test=rng.choice(TEST_PROFILES)["name"],
                )
                stats["malformed"] += 1
            else:
                # 25 行/秒的采集节奏，约 11 小时跑完 100 万行。
                second = i // 25
                if second != last_second:
                    ts = base + timedelta(seconds=second)
                    last_second = second
                dut = rng.choice(duts)
                fw = dut_fw[dut]
                test = _weighted_choice(rng, TEST_PROFILES)
                line, passed, outlier = _build_valid_line(rng, ts.strftime("%Y-%m-%d %H:%M:%S"), dut, fw, test, fw_quality[fw])
                stats["tests"][test["name"]] += 1
                stats["firmware"][fw] += 1
                if passed:
                    stats["pass"] += 1
                else:
                    stats["fail"] += 1
                if outlier:
                    stats["outliers"] += 1
                stats["parsed_expected"] += 1

            chunk.append(line)
            if len(chunk) >= 8192:
                fh.write("\n".join(chunk))
                fh.write("\n")
                chunk.clear()

        if chunk:
            fh.write("\n".join(chunk))
            fh.write("\n")

    elapsed = time.perf_counter() - t0
    size = out_path.stat().st_size
    summary = {
        "out": str(out_path),
        "seed": seed,
        "lines": lines,
        "malformed_rate": malformed_rate,
        "malformed_lines": stats["malformed"],
        "parsed_expected": stats["parsed_expected"],
        "pass": stats["pass"],
        "fail": stats["fail"],
        "super_outliers": stats["outliers"],
        "tests": dict(sorted(stats["tests"].items())),
        "firmware": dict(sorted(stats["firmware"].items())),
        "bytes": size,
        "elapsed_s": round(elapsed, 3),
        "lines_per_s": round(lines / elapsed, 1) if elapsed > 0 else None,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lines", type=int, default=1_000_000, help="物理行数（默认 1000000）")
    ap.add_argument("--out", type=Path, required=True, help="输出日志路径（建议 D 盘）")
    ap.add_argument("--seed", type=int, default=20261121, help="随机种子（默认 20261121）")
    ap.add_argument("--malformed-rate", type=float, default=0.0004, help="格式异常行比例（默认 0.0004）")
    ap.add_argument("--start", default="2026-11-21 08:00:00", help="起始时间戳")
    ap.add_argument("--stats-out", type=Path, help="可选：把生成摘要写成 JSON")
    args = ap.parse_args(argv)

    if args.lines == 0:
        print("警告：--lines 0，将生成空文件", file=sys.stderr)
    summary = generate(args.lines, args.out, args.seed, args.malformed_rate, args.start)
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.stats_out:
        args.stats_out.parent.mkdir(parents=True, exist_ok=True)
        args.stats_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
