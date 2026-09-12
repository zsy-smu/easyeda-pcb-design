# 有限布局合同检查

已有独立要求与可靠读回时运行：

```sh
python scripts/check_layout.py REQUIREMENTS.json ACTUAL.json --out REPORT.json
```

Python 3.10+，只用标准库，不连接 EDA。可直接试用 [要求样例](../assets/examples/layout-requirements.json) 和 [实际样例](../assets/examples/layout-actual.json)；这是合成测试板，不是已完成的参考设计。

## 输入和坐标

根字段全部必填，未知字段拒绝，以免拼错的要求被静默忽略。

| 两侧共同字段 | 含义 |
|---|---|
| `schema` | 要求为 `eda-layout-requirements/v1`；实际为 `eda-layout-actual/v1` |
| `units` / `coordinates` | 仅支持 `mm` / `cartesian-y-up`；同一原点、从顶面看，x向右、y向上 |
| `documentId` | 要求目标与实际读回文档ID；不匹配报告不符 |
| `source` | `id`、`description`、`confirmed:true`；要求另需 `independentOfActual:true`，两侧来源ID不能相同 |
| `board` | `xMinMm,yMinMm,xMaxMm,yMaxMm`，正面积矩形；要求另加 `toleranceMm`，逐边比较 |
| `components` | 非空且位号唯一的完整清单；实际多件/少件均报告不符 |

`source` 的确认、独立性、文档ID和实际 `inventoryComplete:true` 都是适配者声明，脚本不认证其真实性。要求必须来自当前设计/机构/原厂依据，不能读取现有摆放后反向生成全部“要求”。只有完整的目标板清单才可声明完整；不支持把局部片段冒充全板。

实际每个器件：`id`、`position:{xMm,yMm}`、`side:TOP|BOTTOM`、`throughBoard:true|false`、`envelope:{xMinMm,yMinMm,xMaxMm,yMaxMm}`。包络为旋转/镜像后实际本体或已声明装配空间的全局轴对齐矩形；不是统一尺寸方框，也不是实体铜。贯穿板对象按两面参与器件禁布检查。

原生单位、原点、y轴、底面镜像必须先适配。用不对称封装的已知两个焊盘/孔核对；本工具不猜变换，不把封装角度自动解释成插口方向。

## 可检查的要求

要求器件项除 `id` 外按需要添加：

- `fixedPosition:{xMm,yMm,toleranceMm}`：原点的平面直线偏差不超过公差。
- `side`、`throughBoard`：固定面和贯穿属性。
- `allowedOverhangMm:{left,right,top,bottom}`：相对实际板框允许的各边包络外伸量，非负；省略表示此合同要求全包络在板内。正常板边接口有明确外伸要求时填写实值，不为了通过随意扩大。
- `connector:{anchor:{xMm,yMm,toleranceMm},directionDeg,directionToleranceDeg}`：检查插入/插线面的全局锚点与方向。实际同字段去掉两种 tolerance；0°向右，90°向上，180°向左，270°向下，角差按最短环绕角比较。该方向是经封装校准的数据，不是 `rotation`。

要求根字段还需以下数组，无相关要求时显式传空数组：

- `keepouts:[{id,rect,sides:[TOP|BOTTOM]}]`：仅**器件包络**禁布。触碰边界也视为不符；贯穿对象会检查任一指定面。不能据此声称走线、孔、铜区、天线或隔离净空通过。
- `maxDistances:[{id,fromPoint,fromComponentId,toPoint,toComponentId,maxMm}]`：两个具名点的最大平面直线间距。实际根 `points:[{id,componentId,xMm,yMm}]` 提供测量；点必须存在且所属器件与要求一致。点名可以表示物理脚，但真实脚号和坐标映射由适配者核对，不能凭名字证明正确。

直线距离只供布局筛查，不证明线长、去耦回路、参考地、供电质量或两点实际相连。首版不检查器件两两碰撞、铜、开槽/异形板、3D/高度、热、功能、保存和制造。

## 结果和使用

退出 `0`：声明范围通过；`1`：确定不符；`2`：无效、不完整或不支持输入。单位/坐标错误、非有限数、重复字段/ID、未确认来源、缺测量均不会成为 PASS。组件缺失等已明确的清单差异报告 FAIL。

报告 `eda-layout-report/v1` 保存两份输入的原始 SHA256、文档ID、检查计数、稳定排序的 `findings` 和限制。对“接口朝里”可得到 `CONNECTOR_DIRECTION_MISMATCH`；要求点被错误绑定到其它器件得到 `POINT_OWNER_MISMATCH`。

报告不能覆盖任一输入或其硬链接别名。输出成功不证明原生编辑器中已应用/保存此布局；接受原生修改后重新读回相关输入并核验。后续沿用该报告时可用 [证据时效工具](evidence-tool.md)，不能把旧 PASS 与新坐标混用。
