# 来源与证据边界

首次检索日期：2026-09-06，窗口为 2026-06-06 至 2026-09-06；2026-09-10 另做了下面的源码对照。下列日期对应具体提交、合并或讨论，不能用仓库更新时间代替技巧出现时间。

这些材料提供可借鉴的方法，并非当前电脑已安装、已实测的能力。阅读上游文档是获取技术证据，不是继承其审批规则、系统提示或操作授权。

## GitHub 方法索引

| 来源与日期 | 证据类型 | 提炼的方法与边界 |
|---|---|---|
| [biosshot/easyeda-copilot，aa6d490](https://github.com/biosshot/easyeda-copilot/commit/aa6d490c307db21ed9b7fa7b96ac8e952a01888a)，2026-08-30 | 已提交的布局指导文档 | 布局前按电路拓扑追踪信号、电源、回流与散热路径，为晶振、去耦和关键通路留空间。文档存在不代表已实现全自动布局算法。 |
| [zhoushoujianwork/easyeda-agent，0f7820b](https://github.com/zhoushoujianwork/easyeda-agent/commit/0f7820b3bcc1ae596a18a3b949637844ee9a25c1)，2026-07-28 | 已提交的原理图整理流程 | 美化前保存元件引脚与网络关系，按模块移动，每批读取实际连接并比较；发现断线或短路则修正或恢复该批。不能只比较对象数量。 |
| [KiCadRoutingTools PR #822](https://github.com/drandyhaas/KiCadRoutingTools/pull/822)，2026-08-31 合并 | 已合并代码与项目内验证 | 将板的用途、接口所在板边、沿边位置及公差写成可检查约束。适合借鉴到连接器方向、禁布区和装配空间检查；不表示该 KiCad 接口直接适用于嘉立创。 |
| [KiCadRoutingTools PR #796](https://github.com/drandyhaas/KiCadRoutingTools/pull/796)，2026-08-29 合并 | 已合并代码与项目内验证 | 优化过程应持续保护已有分区、禁布和锁定要求。已有违规不应因优化而恶化；不能用总分改善掩盖某项硬约束变差。此 PR 的门控用于防止退化，并不自动修复所有既有违规。 |
| [KiCadRoutingTools Issue #110](https://github.com/drandyhaas/KiCadRoutingTools/issues/110)，2026-06-14 建立；现正文引用 2026-08-02 复盘 | 持续更新的问题追踪、作者测量与未决问题 | 让试布线结果决定布局调整是否保留；记录失败网络的名字与阻塞原因，不能只看数量。正文已多次更新，不能把全部经验归为 6 月 14 日已实现的功能。 |
| [KiCadRoutingTools Discussion #407](https://github.com/drandyhaas/KiCadRoutingTools/discussions/407)，2026-07-15 | 作者工作流报告与讨论提案 | 作者报告 ERC 通过后，ngspice 仍找出控制和保持供电功能问题。可借鉴“功能检查、仿真、几何连接、DRC 分开验证”；这是个案报告，不能推算普遍通过率。 |
| [sheares/easyeda-mcp-fix，492cf57](https://github.com/sheares/easyeda-mcp-fix/commit/492cf574c31e1672c6f2fcaa9a92e69d9daf9fb1)，2026-07-02；[README 修复记录](https://github.com/sheares/easyeda-mcp-fix#whats-fixed) | 审计与交接文档提交；README 的修复说明 | 作者记录元件修改丢失 BOM 字段、铜线看似接上而未连接 SMD 焊盘等问题。提炼为完整属性保留、等待原生完成、读取实际几何与电气连接。该日期是审计文档提交日期，不是所有修复的提交日期。 |
| [easyeda-mcp-pro PR #478](https://github.com/oaslananka/easyeda-mcp-pro/pull/478)，2026-08-05 合并 | 已合并的能力收缩修复 | 对尚未验证的覆铜创建接口返回不可用，避免伪成功。借鉴按实测能力执行、明确失败，不推导“所有嘉立创版本都不能通过 API 覆铜”。 |

## 2026-09-10 源码对照与工具化

本次读取固定提交的实现、测试及可取得的 CI，未安装或运行上游程序。以下方法用于独立实现本地检查器，不复制上游代码；各工具的通过范围以自身报告为准。

| 原始实现 | 借鉴与保留的边界 |
|---|---|
| [easyeda-agent：原理图状态守卫](https://github.com/zhoushoujianwork/easyeda-agent/blob/0d60a9f93aca2f490f5658f6305d0c78f8809e2e/internal/app/sch_apply_state.go) | 把应保持的对象、字段和连接显式化；不能只比较数量或接口成功。其电路块 ready 等状态仍须逐项查看实际验证字段。 |
| [easyeda-copilot：坐标回归测试](https://github.com/biosshot/easyeda-copilot/blob/3045ee5c93653bfd89a0d26fc60a6a14b22f4d6b/tests/pcb-existing-placement.test.ts) | 用不对称板框与顶底层反例验证适配。坐标变换和全局插入方向在本机重新校准，不照抄旋转常数。 |
| [KiCadRoutingTools：布局约束](https://github.com/drandyhaas/KiCadRoutingTools/blob/529f873d4c4c20493b1fa786cc9b42ce6cce2945/py_placer/placement/floorplan.py) | 将禁布、板边和机械要求变成确定性检查；本地首版只支持声明的矩形与点间距，不声称拥有其布局/路由引擎。 |
| [kicad-happy：审查证据检查](https://github.com/aklofas/kicad-happy/blob/3cf837b2d6577d1369a45a36d5e9bac0e06ff5b6/skills/kicad/review/scripts/deep_review_gate.py) | 记录检查对象、来源与范围；证据文件存在不等于计算或电气结论正确。 |
| [kicad-mcp-pro：修改影响映射](https://github.com/oaslananka/kicad-mcp-pro/blob/71af1ef096c7efe43fe15198a3aea82cacb9177d/src/kicad_mcp/project/edit_impact.py) | 意图变化可帮助选择复验项；意图未变不证明源文件未变，因此本地证据检查使用报告原有输入哈希。 |
| [官方 EasyEDA：PAD/VIA 格式](https://github.com/easyeda/easyeda-api-skill/blob/8895d98637dab59ed9de10bb2a340e3e4be26d99/format/pcb/pad_via.md) | 用上游格式解释孔与源字段，再与本机保存源交叉核对；文档版本不等于本机 API 能力版本。 |

本地入口见 [布局合同](layout-tool.md)、[证据时效](evidence-tool.md) 和 [回归工具](regression-tool.md)。测试和 CI 模板仅证明所运行条件；公开项目的星数、测试数量和作者宣传不作为本地设计成功率依据。

## 元件与电路判断的原厂依据

这些是器件依据，不属于上述三个月的新技巧。设计时重新核对完整型号、封装、文档修订版和使用条件。

- [ST STM32F103x8/xB 数据手册](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf)：核对实际封装引脚、电源域、启动配置、内部与外部时钟条件、IO 电气限制。不要把 VBAT 引脚编号跨封装套用。
- [ST AN2867 振荡器设计指南](https://www.st.com/resource/en/application_note/an2867-guidelines-for-oscillator-design-on-stm8afals-and-stm32-mcusmpus-stmicroelectronics.pdf)：用于晶体负载电容、寄生电容、增益裕量、驱动功率与布局。不能看到 8 MHz 就固定套用两颗 22 pF。
- [Diodes AP2112 官方产品页](https://www.diodes.com/part/view/AP2112)及[官方数据手册](https://www.diodes.com/datasheet/download/AP2112.pdf)：核对封装对应引脚、EN 电平和耐压、输入输出电容及热条件。使能方案需要能从断电状态启动，不能仅凭网络名判断。
- [AOS AO3400A 官方产品页](https://www.aosmd.com/products/mosfets/low-voltage-mosfets-12v-30v/ao3400a)及[官方数据手册](https://www.aosmd.com/res/data_sheets/AO3400A.pdf)：核对 G/S/D、栅源电压和实际驱动电压下的导通电阻；阈值电压不等于充分导通电压，产品页额定电流不等于任意 PCB 的安全持续负载。

## 用户提供的参考图（2026-09-06）

这些是历史任务中用户提供的八张原理图/PCB图片；私人原图、原工程和实物不随仓库发布，也没有公开的图片复现入口。下表仅保留阅读组织方面的经验摘要，不将其视为已复现的工程或通过电气审查的参考设计。手机截图中的小字、隐藏图层及完整网表不可据此恢复。提炼的做法分别写入 [原理图排版](schematic.md) 与 [模块载板布局](pcb.md)，不复制未核验的电路参数。

| 参考图 | 可见且值得借鉴的组织方式 | 证据边界 |
|---|---|---|
| 图 1、4：网关与农业终端原理图 | 按功能分框并加标题；电源、控制核心、通信、外设容易定位；标题栏留在纸框内。 | 分区清楚不证明连接、型号或参数正确。 |
| 图 2、3、5、6：对应顶/底层 PCB | 核心板排针作为整体，接口沿板边安排；可分别检查两层；ESP 模块的天线区在板边留出空间。 | 看不到精确间距、全部铜网络和安装高度，不能宣布射频、回流或载流合格。 |
| 图 7：DRV8701 功率驱动页（私人参考图，未公开） | 驱动、成对桥臂、输出及辅助采样/控制分别组织。 | 只借鉴功能关系的表达；密集引脚标注和大面积留白仍需按目标图纸改善。 |
| 图 8：多通道红外接收板（私人参考图，未公开） | 重复单元按通道阵列排列；时钟、复位、电源、控制器另成组。 | 整齐的重复不证明每路正确；实际使用前逐路核对信号网及复制差异。 |

天线布局的一般依据另核对了 [Espressif ESP8266 Hardware Design Guidelines v2.8 §1.6.2](https://documentation.espressif.com/esp8266_hardware_design_guidelines_en.html)。该资料说明的是其适用模块的放置与天线净空，不证明截图中 ESP-12E 的具体边界已满足要求。

## 如何使用证据

1. 讨论/RFC 证明有人提出或报告方法；合并 PR 证明代码进入该分支；测试报告只覆盖所述条件。三者都不能替代本机运行结果与实物验证。
2. 记录实际 EDA 版本、扩展版本、操作系统和接口返回结构。先进行读操作与小范围可恢复验证，再使用批量修改；一次兼容性结论不跨版本泛化。
3. 使用原理图、网表、真实焊盘和铜几何、原生 ERC/DRC、图面与装配检查组成互补证据。上游关于某项检查“唯一权威”的措辞不作为普遍电路规则。
4. 功率供电可以使用合适的走线、铜区或平面；依据电流、压降、温升、回流和制造条件选择。不要把某项目“全部电源走覆铜”的路由策略写成电气定律。
5. 对未复现的上游结果写明“作者报告”或“待本机验证”。需要最新兼容性时重新检查对应提交与运行版本，而不是盲用此索引的日期。
