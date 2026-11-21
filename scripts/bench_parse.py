#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_parse.py —— parse_test_log.py 的吞吐与进程峰值内存基准（纯标准库）。

测量口径：
  * 吞吐：worker 进程内用 time.perf_counter() 包裹"读文件 + parse_log()"，
    行/秒 = (parsed_lines + skipped_lines) / elapsed，MB/秒 = 文件字节 / elapsed；
  * 内存：ctypes 调 Windows psapi.GetProcessMemoryInfo，读取
    PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize（进程生命周期内 RSS 高水位）。
    不用 psutil（第三方依赖），也不只依赖 tracemalloc：tracemalloc 只跟踪
    Python 分配器记录的堆分配，测不到解释器本身、C 扩展、内存映射与
    allocator 预留的 RSS，而 PeakWorkingSetSize 是操作系统口径的真实峰值。

为什么每个重复都起独立子进程：
  * 峰值工作集是"高水位"，同进程重复跑无法归零，第 2/3 次会继承第 1 次的峰值；
  * 独立子进程还能避免上一次解析残留的大对象干扰下一次计时。
  * 子进程启动开销不计入解析计时（计时器在 worker 内部、读文件前才启动）。

冷/热缓存：
  第一次运行往往是冷缓存（文件还没进 OS page cache）。本工具默认先跑
  --warmup 次（不计入统计），再跑 --repeat 次测量，因此报告的是**热缓存**
  吞吐；Windows 下没有管理员权限无法可靠清空 standby list，冷盘最坏值
  需另用工具（如 RAMMap）测量。

用法：
    python scripts/bench_parse.py D:/deepwork/tools/data/synthetic-1m.log --repeat 3 --warmup 1
    python scripts/bench_parse.py <log> --repeat 5 --json-out bench.json --md-out bench.md
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_test_log import parse_log  # noqa: E402


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    """与 Windows PROCESS_MEMORY_COUNTERS 布局一致（x64 下 72 字节）。"""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def read_process_memory() -> tuple[int, int]:
    """返回 (PeakWorkingSetSize, WorkingSetSize)，单位字节。仅 Windows 可用。"""
    if sys.platform != "win32":
        raise RuntimeError("本基准依赖 Windows psapi.GetProcessMemoryInfo")

    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ok = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.PeakWorkingSetSize), int(counters.WorkingSetSize)


def peak_working_set_bytes() -> int:
    """返回当前进程的 PeakWorkingSetSize（字节）。"""
    return read_process_memory()[0]


def run_worker(log_path: Path, index: int) -> dict:
    """解析一次日志，返回本次测量的原始数据（供父进程汇总）。"""
    size = log_path.stat().st_size
    t0 = time.perf_counter()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    t_read = time.perf_counter()
    report, _ = parse_log(text)
    t_end = time.perf_counter()

    lines = report["summary"]["parsed_lines"] + report["summary"]["skipped_lines"]
    elapsed = t_end - t0
    read_s = t_read - t0
    peak, current = read_process_memory()

    result = {
        "index": index,
        "elapsed_s": round(elapsed, 6),
        "read_s": round(read_s, 6),
        "lines": lines,
        "parsed_lines": report["summary"]["parsed_lines"],
        "skipped_lines": report["summary"]["skipped_lines"],
        "groups": len(report["results"]),
        "bytes": size,
        "lines_per_s": round(lines / elapsed, 1) if elapsed > 0 else None,
        "mb_per_s": round((size / (1024 * 1024)) / elapsed, 2) if elapsed > 0 else None,
        "peak_rss_bytes": peak,
        "current_rss_bytes": current,
    }
    del text
    return result


def _spawn(log_path: Path, index: int) -> dict:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--index",
        str(index),
        str(log_path),
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"worker #{index} 失败 (exit {proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def _summary(values: list[float]) -> dict:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _fmt_summary(values: list[float], unit: str = "", digits: int = 2) -> str:
    s = _summary(values)
    return (
        f"median={s['median']:.{digits}f}{unit}  "
        f"min={s['min']:.{digits}f}{unit}  max={s['max']:.{digits}f}{unit}"
    )


def to_markdown(payload: dict) -> str:
    agg = payload["aggregate"]
    lines = ["# 解析吞吐与峰值内存基准", ""]
    lines.append(f"- 日志：`{payload['log']}`")
    lines.append(f"- 文件大小：{payload['size_mb']:.2f} MB；总行数：{payload['lines']:,}")
    lines.append(f"- 预热：{payload['warmup_runs']} 次（不计入）；测量：{payload['repeats']} 次")
    lines.append(f"- 环境：Python {payload['environment']['python']} / {payload['environment']['platform']}，"
                 f"逻辑核 {payload['environment']['cpu_count']}")
    lines.append("")
    lines.append("## 每次测量")
    lines.append("")
    lines.append("| run | 耗时(s) | 行/秒 | MB/秒 | 峰值 RSS(MB) |")
    lines.append("|---|---|---|---|---|")
    for r in payload["runs"]:
        lines.append(
            f"| {r['index']} | {r['elapsed_s']:.3f} | {r['lines_per_s']:,.0f} | "
            f"{r['mb_per_s']:.2f} | {r['peak_rss_bytes'] / 1048576:.1f} |"
        )
    lines.append("")
    lines.append("## 汇总（median / min / max）")
    lines.append("")
    lines.append("| 指标 | median | min | max |")
    lines.append("|---|---|---|---|")
    for key, label, digits in (
        ("lines_per_s", "行/秒", 0),
        ("mb_per_s", "MB/秒", 2),
        ("elapsed_s", "耗时(s)", 3),
        ("peak_rss_mb", "峰值 RSS(MB)", 1),
        ("current_rss_mb", "结束时 RSS(MB)", 1),
    ):
        s = agg[key]
        lines.append(f"| {label} | {s['median']:.{digits}f} | {s['min']:.{digits}f} | {s['max']:.{digits}f} |")
    lines.append("")
    lines.append("> `PeakWorkingSetSize` 是 OS 口径的进程 RSS 高水位；`tracemalloc` 只覆盖 Python 堆分配，")
    lines.append("> 会漏掉解释器、C 层与 allocator 预留，因此这里以 psapi 为准。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path, help="待测日志文件")
    ap.add_argument("--repeat", type=int, default=3, help="正式测量次数（默认 3，至少 3 次才稳健）")
    ap.add_argument("--warmup", type=int, default=1, help="预热次数（默认 1，不计入统计）")
    ap.add_argument("--json-out", type=Path, help="把完整结果写成 JSON")
    ap.add_argument("--md-out", type=Path, help="把结果写成 Markdown")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--index", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if not args.log.is_file():
        print(f"日志不存在: {args.log}", file=sys.stderr)
        return 2

    if args.worker:
        result = run_worker(args.log, args.index)
        # worker 的 stdout 只输出这一行 JSON，避免污染父进程解析。
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.repeat < 1 or args.warmup < 0:
        print("--repeat 必须 >= 1，--warmup 必须 >= 0", file=sys.stderr)
        return 2

    size = args.log.stat().st_size
    print(f"日志: {args.log}")
    print(f"大小: {size / (1024 * 1024):.2f} MB")
    print(f"预热: {args.warmup} 次（不计入统计，首次冷缓存运行用于填充 OS page cache）")
    for i in range(args.warmup):
        warm = _spawn(args.log, -(i + 1))
        print(
            f"[warmup {i + 1}] {warm['elapsed_s']:.3f}s  "
            f"{warm['lines_per_s']:,.0f} 行/秒  peak={warm['peak_rss_bytes'] / 1048576:.1f} MB"
        )

    print(f"测量: {args.repeat} 次（每次独立子进程，峰值 RSS 从零开始）")
    runs = []
    for i in range(args.repeat):
        r = _spawn(args.log, i + 1)
        runs.append(r)
        print(
            f"[run {i + 1}] {r['elapsed_s']:.3f}s  {r['lines_per_s']:,.0f} 行/秒  "
            f"{r['mb_per_s']:.2f} MB/秒  peak RSS={r['peak_rss_bytes'] / 1048576:.1f} MB  "
            f"(parsed={r['parsed_lines']:,}, skipped={r['skipped_lines']:,}, groups={r['groups']})"
        )

    agg = {
        "lines_per_s": _summary([r["lines_per_s"] for r in runs]),
        "mb_per_s": _summary([r["mb_per_s"] for r in runs]),
        "elapsed_s": _summary([r["elapsed_s"] for r in runs]),
        "peak_rss_mb": _summary([r["peak_rss_bytes"] / 1048576 for r in runs]),
        "current_rss_mb": _summary([r["current_rss_bytes"] / 1048576 for r in runs]),
    }
    payload = {
        "log": str(args.log),
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 3),
        "lines": runs[0]["lines"],
        "parsed_lines": runs[0]["parsed_lines"],
        "skipped_lines": runs[0]["skipped_lines"],
        "groups": runs[0]["groups"],
        "warmup_runs": args.warmup,
        "repeats": args.repeat,
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "cpu_count": os.cpu_count(),
            "executable": sys.executable,
        },
        "runs": runs,
        "aggregate": agg,
    }

    print("")
    print("汇总（median / min / max）:")
    print("  吞吐 行/秒 : " + _fmt_summary([r["lines_per_s"] for r in runs], digits=0))
    print("  吞吐 MB/秒 : " + _fmt_summary([r["mb_per_s"] for r in runs], digits=2))
    print("  耗时 s     : " + _fmt_summary([r["elapsed_s"] for r in runs], digits=3))
    print("  峰值 RSS MB: " + _fmt_summary([r["peak_rss_bytes"] / 1048576 for r in runs], digits=1))
    print("")
    print("说明: PeakWorkingSetSize 是 OS 口径的进程 RSS 高水位；tracemalloc 只跟踪 Python 堆分配，")
    print("      测不到解释器/C 层/allocator 预留的 RSS，因此本基准以 psapi 为准。")
    print("      预热后的数字是热缓存吞吐；Windows 无管理员权限无法可靠清空 standby list。")

    md = to_markdown(payload)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[ok] JSON -> {args.json_out}")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
        print(f"[ok] Markdown -> {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
