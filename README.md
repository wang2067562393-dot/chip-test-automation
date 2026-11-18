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
│   └── metric_anomaly.py          # 指标异常检测（3σ / 稳健 MAD，AI4Test 第一步）
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
