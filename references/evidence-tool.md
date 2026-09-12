# 已有报告的时效与范围

用于恢复任务或沿用报告时，核对报告原来检查的输入是否仍是当前文件。它不重跑电路分析，也不认证报告作者；期望计划及固定的报告哈希必须来自已审阅证据。

```sh
python scripts/check_evidence.py PLAN.json --out REPORT.json
```

Python 3.10+ 标准库，依赖同目录 `check_delivery.py` 的公共文件保护函数；复制时保留整个 skill。输出必须独立于计划、原报告和输入。相对路径相对计划文件目录，允许绝对路径；不接受 `..` 跳转、符号链接或 junction/reparse 路径。

## 计划合同

根对象为 `schema:"evidence-plan/v1"` 和非空 `checks` 数组。每项：

| 字段 | 含义 |
|---|---|
| `id` | 本次检查的唯一名称 |
| `report` / `reportSha256` | 已审阅报告路径及其64位小写SHA256；检测报告是否被替换/重写 |
| `expectedSchema` | 下表中明确支持的报告类型 |
| `requiredScope` | 本次独立要求的非空检查范围数组；不能直接从报告现有范围推导需求 |
| `inputs` | 该报告的**全部**原始输入角色 → 当前文件路径；与报告原有哈希比较，不能漏绑角色 |
| `context` | `null` 表示只检查文件/选择索引关系；需要身份时用 `{documentId:...}` 或 `{projectId:...,documentId:...}` |
| `selection` | 仅网表报告必需，`{expectedIndex:null|非负整数,actualIndex:null|非负整数}`；对应数组文档的显式选择 |

已有报告缺少原始输入哈希或所需身份时，重跑能产生这些证据的检查，或明确缩小声明范围。**不能给旧 PASS 附上当前输入的哈希，制造“新鲜报告”。** 同一字节的输入搬到新目录可通过，但不因此取得原生文档身份。

## 支持范围

| `expectedSchema` | `inputs` 角色 | 支持的 `requiredScope` |
|---|---|---|
| `eda-netlist-comparison/v1` | `expected`,`actual`；报告使用别名文件时另需 `aliases` | `logical-connected`；全量已列引脚范围可加 `logical-all-listed`,`explicit-nc`；报告实际提供相应实体清单时可加 `physical-assignment`,`physical-multiplicity` |
| `easyeda-manufacturing-check/v1` | `expected`,`export` | `outline`,`drill-geometry`,`copper-layer-inventory`；镀层已按声明要求验证时可加 `known-plating` |
| `eda-layout-report/v1` | `requirements`,`actual` | `declared-rectangular-layout-contract`，保留该工具自身的全部范围限制 |

网表的 `connected` 不覆盖全部已列引脚或NC；制造几何通过不自动证明镀层已知。布局报告可比较其原本绑定的 `documentId`，此ID仍是布局输入声明，不是原生 API 的独立认证。当前网表/制造 v1 报告不提供可信项目/文档身份；要求该身份会返回未知，而非把计划值补进去。当前布局报告也不提供项目ID。

交付目录请直接运行 `check_delivery.py verify`，以重新扫描目录。旧交付报告、原生 DRC 布尔值、手工点评、回归报告及其他未知 schema 不在本工具范围内，不自动转换成已认证报告。

## 生成一份可运行样例

下面 Python 从**项目工作目录**运行，在其中生成 `pcb-check-demo`；自定义安装位置时调整 `skill`。示例固定的是本次真实运行的报告，用于演示全量已列引脚与NC检查，不给历史报告补盖输入哈希：

```python
import hashlib, json, subprocess, sys
from pathlib import Path
skill = Path.home() / ".codex/skills/easyeda-pcb-design"
work = Path("pcb-check-demo").resolve()
report = work / "netlist-report.json"
expected = skill / "assets/examples/netlist-expected.json"
actual = skill / "assets/examples/netlist-actual.json"
subprocess.run([sys.executable, str(skill / "scripts/compare_netlists.py"),
                str(expected), str(actual), "--out", str(report)], check=True)
plan = {"schema": "evidence-plan/v1", "checks": [{
    "id": "example-nets", "report": str(report),
    "reportSha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    "expectedSchema": "eda-netlist-comparison/v1",
    "requiredScope": ["logical-all-listed", "explicit-nc"],
    "inputs": {"expected": str(expected), "actual": str(actual)},
    "selection": {"expectedIndex": None, "actualIndex": None}, "context": None
}]}
plan_path = work / "evidence-plan.json"
plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
subprocess.run([sys.executable, str(skill / "scripts/check_evidence.py"), str(plan_path),
                "--out", str(work / "evidence-report.json")], check=True)
```

## 结果

退出 `0`：全部所需报告仍对应当前输入，原报告通过且覆盖要求；`1`：报告/输入已变、缺文件、范围不足、身份不符或原检查失败；`2`：格式、缺少绑定或其他不支持情形。解析/输出无效时可能未写新报告而保留旧文件，应以本次退出码和 stdout JSON 为准。

`eda-evidence-check/v1` 报告保留计划/报告/当前输入哈希、各项范围与 `findings`。`contextVerified:false` 不能解读成原生项目身份已验证；整体 PASS 也不证明电气、实铜、实板或未保存的编辑器状态正确。文件需在核验期间保持稳定，前后复核不等于原子文件系统快照。
