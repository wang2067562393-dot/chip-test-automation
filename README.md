# 🔬 chip-test-automation · 半导体芯片测试笔记与自动化工具

> 从 UWB 芯片功能 / 可靠性测试实践中沉淀的通用知识与可复用脚本：固件版本管理、测试日志（Log）自动解析、标准化报告输出。
> 目标：把"人工翻 Log、手工填 Excel"变成"一条命令产出结构化结果"。

![tests](https://github.com/wang2067562393-dot/chip-test-automation/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![deps](https://img.shields.io/badge/dependencies-标准库-16A34A)
![status](https://img.shields.io/badge/status-学习沉淀中-F59E0B)

## 这是什么

两部分内容：

1. **测试方法论笔记**（`notes/`）：UWB 是什么、功能测试与可靠性测试分别测什么、固件与版本如何管理、一份可信的测试记录应该包含哪些字段；
2. **自动化小工具**（`scripts/`）：纯 Python 标准库实现，解析上位机导出的测试日志，按设备 / 测试项聚合通过率、误差、信号质量与失败原因，输出 JSON + Markdown 标准化报告；
3. **AI4Test 方向探索**：从统计异常检测（3σ / 稳健 MAD）起步，路线见 [`notes/03-ai4test-roadmap.md`](notes/03-ai4test-roadmap.md)（日志模板挖掘 → 失败归因 → LLM 测试报告助手）。

> ⚠️ 仓库内所有示例数据均为**虚构数据**（`examples/` 下有明确标注），不含任何真实产品、公司或客户信息。

## 目录结构

```
chip-test-automation/
├── notes/
│   ├── 01-uwb-testing-basics.md   # UWB 测试基础：原理、流程、指标
│   └── 02-log-driven-test-qa.md   # 日志驱动的测试质量保障思路
├── scripts/
│   ├── parse_test_log.py          # 日志解析 + 聚合 + JSON/Markdown 报告
│   ├── compare_fw.py              # 跨固件版本通过率配对对比
│   ├── metric_anomaly.py          # 指标异常检测（3σ / 稳健 MAD，AI4Test 第一步）
│   ├── gate.py                    # 回归门：报告间指标漂移超阈值则 exit 1
│   ├── gen_log.py                 # 合成日志生成器（百万行、固定种子可复现）
│   └── bench_parse.py             # 吞吐与进程峰值 RSS 基准
├── notes/
│   ├── 01-uwb-testing-basics.md
│   ├── 02-log-driven-test-qa.md
│   └── 03-ai4test-roadmap.md      # AI4Test 方向路线（P1–P4）
├── tests/                         # 单元测试（unittest，CI 在 3.10/3.11/3.12 上运行）
├── examples/
│   ├── sample_run.log             # 虚构示例日志
│   ├── sample_report.md/json      # 单批次报告样例
│   ├── sample_compare.md          # 固件版本对比样例
│   └── metrics.jsonl              # 逐次测量（异常检测示例输入）
└── pyproject.toml
```

## 快速开始

无需安装第三方依赖（Python 3.10+）：

```bash
# 用虚构示例数据跑一遍
python scripts/parse_test_log.py examples/sample_run.log \
    --json-out outputs/report.json \
    --md-out   outputs/report.md

# 只看 Markdown 报告
python scripts/parse_test_log.py examples/sample_run.log --md

# 跨固件版本对比（可传入多份日志，按 FW 分组算通过率差值）
python scripts/compare_fw.py examples/sample_run.log --md

# 指标异常检测（3σ / MAD，抓测距误差或 RSSI 的离群批次/测量点）
python scripts/metric_anomaly.py examples/metrics.jsonl --metric rssi_dbm --method mad --md
```

## 测试

```bash
python -m unittest discover -s tests -v
```

示例数据上的固件对比结论（1.2.2 → 1.2.3）：测距项通过率 33.3% → 83.3%（+50pp），接收灵敏度 50% → 66.7%（+16.7pp）。注意这是**虚构数据**，仅用于演示工具输出。

用示例数据跑出的报告样例：[sample_report.md](examples/sample_report.md) / [sample_report.json](examples/sample_report.json)。

## 性能：百万行日志

```bash
# 1. 生成 100 万行合成日志（固定种子，逐字节可复现）
python scripts/gen_log.py --lines 1000000 --seed 20261121 --out data/synthetic-1m.log

# 2. 测吞吐与峰值内存（1 次预热 + 3 次测量，每次独立子进程）
python scripts/bench_parse.py data/synthetic-1m.log --repeat 3 --warmup 1
```

实测（Windows 11 / Ryzen 5 5600 / Python 3.12.10，日志 1,000,000 行 / 95.82 MB）：

| 指标 | median | min | max |
|---|---|---|---|
| 吞吐（行/秒） | **232,390** | 230,403 | 244,933 |
| 吞吐（MB/秒） | 22.27 | 22.08 | 23.47 |
| 解析耗时（秒） | 4.303 | 4.083 | 4.340 |
| 峰值 RSS（MB） | **309.5** | 309.5 | 309.6 |

口径与取舍：

- **行/秒** = (parsed + skipped) / 耗时，计时在 worker 进程内、读文件前启动，不含子进程开销；
- **峰值 RSS** 用 `ctypes` 调 `psapi.GetProcessMemoryInfo` 取 `PeakWorkingSetSize`。
  不用 `psutil`（保持零依赖），也不用 `tracemalloc`——它只统计 Python 堆分配，测不到
  解释器、C 层与 allocator 预留的 RSS，会给出偏低的假数字；
- 每次重复都起**独立子进程**：`PeakWorkingSetSize` 是高水位，同进程重跑会继承上次峰值；
- 报告的是**热缓存**吞吐（先预热 1 次）；Windows 无管理员权限无法可靠清空 standby list，
  冷盘最坏值需另测；
- 当前解析器是**非流式**的（`read_text()` + `splitlines()`），96 MB 日志峰值约 309 MB。
  这是实现的结构性成本，流式改造在后续计划里。

两次独立运行 median 分别为 232,390 与 235,244 行/秒（差 1.2%），峰值 RSS 完全一致。

## 回归门：让指标漂移自动报警

```bash
# 指标漂移超过 5pp 就 exit 1，并指出是哪个测试项的哪个指标
python scripts/gate.py --baseline outputs/old.json --candidate outputs/new.json --threshold 5
```

实测：把 `ranging_ss_twr` 通过率人为下调 10pp 后

```
[FAIL] test=ranging_ss_twr metric=pass_rate old=95.78% new=85.78% drift=-10.00pp threshold=5pp exceeded_by=5.00pp

结论: FAIL —— 1 个指标漂移超阈值
```

退出码语义：`0` 通过（允许 SKIP）、`1` 有指标超阈值、`2` 输入或格式错误。
边界处理：漂移**严格大于**阈值才失败；某测试项只出现在一侧时记为 SKIP，
默认不让 gate 失败（可用 `--fail-on-missing` 收紧），避免报告格式演进时误伤 CI。

## 日志格式约定

每行一条测试结果，key=value 空格分隔，便于正则解析，例如：

```
[2026-01-01 10:00:01] DUT=UWB001 FW=1.2.3 TEST=ranging_ss_twr RESULT=PASS dist_m=1.02 err_mm=18 rssi_dbm=-71
[2026-01-01 10:00:05] DUT=UWB001 FW=1.2.3 TEST=ranging_ss_twr RESULT=FAIL err_code=E_TIMEOUT rssi_dbm=-94
```

- 必需字段：`DUT`（被测设备）、`FW`（固件版本）、`TEST`（测试项）、`RESULT`（PASS/FAIL）；
- 可选字段：`err_mm`（测距误差毫米）、`rssi_dbm`、`err_code` 等；
- 解析器对字段缺失健壮处理，FAIL 但无 err_code 记为 `UNKNOWN`。

## 报告包含什么

- 每个 DUT × 测试项的：总数、通过数、通过率；
- 测距误差均值 / 样本标准差 / 最大误差；
- RSSI 最小 / 均值（信号质量）；
- 失败原因码直方图；
- 固件版本覆盖（防止"拿旧版本结果交付"）。

## 为什么值得自动化

- 人工读几千行 Log 极易漏看偶发 FAIL，聚合统计让可靠性问题（如某 err_code 集中出现）立刻可见；
- 报告格式固定后，可直接贴入测试归档，减少转录错误；
- 解析器与测试设备解耦：只要上位机能导出文本日志即可复用。

## 后续计划

- [ ] 增加跨多份日志文件的批量汇总与版本对比（同测试项 FW 1.2.2 vs 1.2.3）
- [ ] 增加趋势图（matplotlib，可选依赖）
- [ ] 补充可靠性测试（老化 / 温循）结果模板
