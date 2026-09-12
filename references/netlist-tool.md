# 网表差异检查命令

使用 `scripts/compare_netlists.py` 比较两个文件里的逻辑引脚到网络分配。Python3.10以上，只有标准库依赖。不会连接EDA、切页或修改工程。

## 最小运行示例

在本skill目录下运行；Windows可将`python`替换为`py -3.10`。报告写到工作目录，不改样例输入。

```text
python scripts/compare_netlists.py assets/examples/netlist-expected.json assets/examples/netlist-actual.json --require-physical --out netlist-report.json
```

此样例有6个逻辑引脚、7块实体焊盘，其中J1.10有两块不同ID的实体焊盘。两侧都有物理清单，预期PASS且`physicalMultiplicityCompared:true`。改动实际样例的某个net后重跑，应列出具体位号、脚号、预期与实际网络；删去J1.10的一块焊盘也会报差异。

## 输入格式

优先复用当前工具支持的真实导出，不为运行检查而人工补造未知连接。支持三种显式结构：

1. `nets`对象：键为网名，值为`{"ref":"U1","pin":"1"}`数组。可选`unconnected`数组单独列无网引脚，`no_connect:true`表示有独立证据的NC标记。未提供NC字段就保持未知。
2. `schema:"eda-netlist/v1"`，`components`数组，每项为`{"ref":"U1","pins":[{"number":"1","net":"GND"}]}`；每个pin显式提供net，空字符串/null表示该脚无网络。
3. 已观测的嘉立创导出：`components[uid].props.Designator`与`components[uid].pinInfoMap[pin].net`。如果真实版本使用其他字段，报输入不支持，先增加经核验的适配器，不能当作空网通过。

文件若为多文档数组（各项可为对象或JSON字符串），用`--expected-index N`／`--actual-index N`明确指定从0开始的索引；多文档不自动选第一张或最后一张。先核对所选文档的元件身份与目标板，检查器不能由索引确认用户意图。

支持的输入结构可附带`physicalPads`数组：每项包含唯一`id`、`ref`、`pin`和`net`。同一逻辑脚可对应多个实体ID，逐块检查网络分配；`--require-physical`要求actual确实包含该清单，且每个逻辑脚至少有一块焊盘。

expected也提供实体清单时，自动要求actual提供清单，并逐逻辑脚比较实体数量，可检出同脚两块焊盘中少了一块或多了一块。不同导出的实体ID可能重建，因此不要求两侧ID相等。只有actual清单时不能独立证明实体数量完整，报告的`physicalMultiplicityCompared`为false。原生pinInfoMap通常只表达逻辑引脚，不能据它宣称每块实体铜都已检查。

## 检查范围与退出码

- 默认`--scope all`比较全部列出的引脚、网名，并检查expected显式要求的NC标记。actual缺少NC证据会列为`ncDifferences`，不把无网脚当作已标NC。
- `--scope connected`仅比较有网引脚；用于预期清单只列了有网脚的情况。报告明确不覆盖无网引脚完整性及NC，实际有网脚变成无网仍算漏接。
- 网名默认逐字符比较，不自动去引号、改大小写或合并电源名。明确核实的别名可通过`--actual-net-aliases aliases.json`传入，结构为`{"实际名称":"预期名称"}`；只做一次精确替换，记录在报告内。
- 退出码：`0`所选范围PASS；`1`实际比较有差异；`2`输入非法、格式不支持、选择不明确或写报告失败。重复JSON键、重复逻辑端点、重复实体ID、未知结构及空网表不会默认为成功。

报告包含输入SHA256、选择索引、两侧计数，以及`missing`、`extra`、`wrongNet`、`ncDifferences`、`physicalPadAssignmentFindings`和`physicalPadInventoryDifferences`。空网脚彼此独立，不会被组成一个公共网络。

本工具**不检查**Channel ID/库/BOM一致性、器件功能、电压电流或实体铜几何接触；不能以PASS替代原生网表关联检查、ERC/DRC和铜连通。属性关联问题仍按[器件替换](replacement-and-delivery.md)处理。
