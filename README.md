# EasyEDA PCB Design Skill

面向嘉立创 EDA 的原理图与 PCB 设计 skill：把工程经验、按需参考文档和可执行检查工具放在一起，帮助 AI 从需求走到可编辑、可复核的交付结果。

[![Software checks](https://github.com/zsy-smu/easyeda-pcb-design/actions/workflows/pcb-skill-checks.yml/badge.svg)](https://github.com/zsy-smu/easyeda-pcb-design/actions/workflows/pcb-skill-checks.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**适用场景：** 新板设计、原理图整理、PCB 布局布线、改板、审查及有资料支持的复刻。重点是连接正确、机械可装、保存有效、检查范围清楚。

This repository provides a Chinese-first agent skill for EasyEDA schematic and PCB workflows, plus standalone Python validators. It does **not** bundle an EDA bridge or an autonomous placement/routing engine. Hardware examples describe historical lessons, not qualified reference designs.

## 包含什么

| 内容 | 作用 |
|---|---|
| [SKILL.md](SKILL.md) | 工作入口：判断当前阶段，只加载相关参考 |
| [references/](references/) | 电气审查、原理图连接保持、PCB 布局、原生保存与接口差异 |
| [scripts/](scripts/) | 网表、制造文件、布局约束、证据时效、交付文件检查与回归入口 |
| [assets/examples/](assets/examples/) | 可运行的网表和布局输入示例、制造要求示例 |
| [assets/templates/](assets/templates/) | 设计记录模板 |
| [tests/](tests/) | 软件回归测试及独立工作流评估场景 |

```mermaid
flowchart LR
  A[需求与电气关系] --> B[原理图与引脚核对]
  B --> C[机械锚点与关键布局]
  C --> D[布线与实际覆铜]
  D --> E[按修改范围验证]
  E --> F[保存、重读与交付]
  D -->|阻塞反馈| C
```

实际编辑优先使用已有后台接口；一个编辑器只保留一个写入者。局部修改检查受影响部分，批量操作前抽查真实封装、坐标、单位和保存行为。接口成功、DRC、实际铜连接和最终导出文件分别验证，避免重复检查与伪成功。

## 安装与使用

在支持 skill 的 Codex 中，可以让内置安装器从本仓库安装：

```text
$skill-installer 从 https://github.com/zsy-smu/easyeda-pcb-design 安装仓库根目录的 skill
```

也可以在目标工程根目录手动安装为项目级 skill：

```bash
git clone https://github.com/zsy-smu/easyeda-pcb-design.git .agents/skills/easyeda-pcb-design
```

本仓库根目录就是完整的 skill 文件夹。安装位置与发现行为见 [OpenAI 官方 skill 文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。避免在同一环境重复安装同名副本。

调用示例：

```text
$easyeda-pcb-design 根据现有需求重新设计一块双层电源载板。
先核对模块输入范围、负载电流与接口机械尺寸；通过已连接的后台接口工作。
保留旧工程，交付可编辑工程、BOM、检查结果和接线说明。
```

```text
$easyeda-pcb-design 只检查这份实际 Gerber ZIP 的板框、铜层和钻孔。
按我提供的制造要求比较，报告未支持或无法验证的对象，不修改原工程。
```

**绘图前提：** 使用者需要自行配置可用的 EasyEDA 后台接口、扩展或 MCP，并验证版本与读写能力。本仓库不包含这些连接组件，也不会自动安装或启动服务。没有连接时仍可使用下面的离线检查器。

## 运行检查工具

Python 3.10+。网表、布局、证据和交付检查使用标准库；制造几何检查另需 Shapely 2.x。

```bash
python -m venv .venv
# Linux/macOS:
.venv/bin/python -m pip install -r scripts/requirements-manufacturing.txt
# Windows PowerShell:
.\.venv\Scripts\python.exe -m pip install -r scripts/requirements-manufacturing.txt
```

以下 `python` 指上述环境中的解释器；从仓库根目录运行：

```bash
python scripts/compare_netlists.py assets/examples/netlist-expected.json assets/examples/netlist-actual.json --out ../netlist-report.json
python scripts/check_layout.py assets/examples/layout-requirements.json assets/examples/layout-actual.json --out ../layout-report.json
python scripts/run_regression.py --out ../pcb-skill-regression.json
```

| 工具 | 输入和检查边界 |
|---|---|
| `compare_netlists.py` | 预期/实际 JSON 网表，逐引脚与可选实体焊盘网络比较；不求解实际铜连通 |
| `check_manufacturing.py` | 实际 Gerber ZIP + 独立制造要求；检查支持的板框、层与钻孔对象 |
| `check_layout.py` | 机械约束 JSON + 实际布局 JSON；检查支持的矩形、点、朝向和禁布关系 |
| `check_evidence.py` | 检查计划与原报告的输入哈希；不替报告补做计算 |
| `check_delivery.py` | 文件清单及 SHA256；只检查文件是否变化 |
| `run_regression.py` | 运行软件测试并记录输入哈希、失败与跳过；报告须写在 skill 目录外 |

详细格式见 [SKILL.md 的工具索引](SKILL.md#可执行的文件检查)。不支持的几何、依赖缺失或权限限制会被明确报告；`PARTIAL` 不等于完整通过。

## 验证与贡献

[GitHub Actions](https://github.com/zsy-smu/easyeda-pcb-design/actions) 运行 Windows/Linux、Python 3.10/3.12 的软件测试，并覆盖无 Shapely 的降级路径。CI 检验检查器，不代表自动完成了电路功能、EDA 实机或实板验证。

欢迎补充可复现的接口差异、最小测试输入和修复。提交问题时请说明 EDA/扩展版本、预期与实际结果，并去除私人路径、账号、工程资料和凭据。修改检查器后运行回归；流程建议应说明它解决了哪类真实错误或重复工作。

## 来源与边界

经验来自 555、STM32、模块电源载板等设计与排障，以及公开项目的方法研究，详见 [来源索引](references/sources.md) 和 [经验索引](references/experience-index.md)。私人原工程、截图、运行日志和设备数据未随仓库发布；历史案例不是公开可复现测试。

该 skill 不保证“无 BUG”“一键打板”或特定完成时间。DRC 通过不证明电路功能、载流、温升、EMC 或量产可靠性；仍需按具体工程取得相应证据。

采用 [MIT License](LICENSE)。第三方链接内容保留其原许可；本项目与嘉立创 EDA、OpenAI 无官方隶属关系。
