# 功能审查与容易误判的点评

先确认用途、完整料号、封装和实际引脚网络，再判断电路。按相关类型读取以下小节；局部编辑不自动扩大为整板重设计。历史点评、候选方案、某次连接读回和实板测试是不同证据，以下反例不证明旧图存在这些故障，也不证明它们已经修好。原厂参数于本次整理重新核对；更换订货型号或封装时重新核验。

## 先消除需求与点评中的矛盾

- 将要求写成供电范围、同时负载、信号方向、电源域、默认状态与实现方式。先解决“低压驱动直接接24V”“无隔离供电却要求电气隔离”“FS端口要求HS速率”等不兼容条件，再落图。用户选择省成本时，可以明确采用非隔离方案，但不能仅保留 `ISO_GND` 名称宣称隔离。
- 点评互相矛盾时逐项回到 `物理引脚→实际网络→器件功能`。例如 EN 接地与接 VIN 是不同状态；`LOAD_EN`、`STATUS_LED` 等名称不能证明接到哪根 GPIO。不要把一段评论的引脚分配拼到另一版本图上，或从截图交叉线推断短路。
- 区分硬件错误、功能缺失、性能待定和可选改进。备用电池、外部晶振、ESD及滤波是否必需取决于用途；不能把去耦电容阵列称为已实现 RTC 备份，也不能把未知堵转电流替换成一个方便的额定值。

## 555、极性与按键

- 标准无稳态：2/TRIG 与6/THRES接定时节点；`VCC→RA→7/DISCH→RB→定时电容→GND`。4/RESET是**低有效复位**，未使用时按手册拉高；低电平会强制输出低，不能写成“复位高有效”。
- 标准拓扑近似 `tH=0.693(RA+RB)C`、`tL=0.693RB C`、`T=0.693(RA+2RB)C`；先区分输出高电平占空比与负载低有效导通占空比。有旁路二极管、电位器或特殊负载时，沿实际充放电路径重算，并检查电位器极限是否使放电支路失去必要限流。[TI NE555，功能表与无稳态应用](https://www.ti.com/lit/ds/symlink/ne555.pdf)
- LED、电解、二极管、MOS体二极管以厂家脚号核极性，不按符号朝向或“1脚必为正”猜测；LED限流按供电、VF、驱动压降与目标电流计算。上拉后的按键拉地是正常低有效输入；四脚按键须确认内部常通脚组，RC初值不能替代触点与固件去抖核验。

## 电池、稳压和模块复用

- **EN必须能冷启动。** AP2112的EN高电平范围包含5V，低电平为关断；常开且VIN合规时可让EN与VIN同源。“EN不能接5V，应接自身3V3输出”不是正确通则。其EN下拉与关断输出放电意味着自接VOUT可能形成启动依赖，这是电路推断；GPIO控制也须查GPIO是否由被关闭的电源供电。[Diodes AP2112，电气特性与典型应用](https://www.diodes.com/datasheet/download/AP2112.pdf)
- **TPS63020不是3S直入器件。** 芯片VIN/VINA推荐1.8–5.5V、可调输出1.2–5.5V；若电芯满充4.2V，3S即12.6V，不能直接接其输入，6.5V/7.4V也不合规。需要已确定的前级和独立回接网络；芯片典型4A开关限流不是4A输出保证。成品模块还须核实际版本、EN/PS配置、电感、端子与散热，不能用芯片上限替代模块额定值。[TI TPS63020，§6.3–6.5](https://www.ti.com/lit/ds/symlink/tps63020.pdf)
- 各支路以功率守恒汇总：`I上游 ≥ I本级外供 + Σ[(V下游×I下游)/(V上游×η下游)]`。级联不能同时重复占用上游全部额定电流；多个排针共享一路能力。电池范围采用满充、放电终点和瞬态；普通降压器接近输入下限时的压差、启动和电机回灌吸收均需核验。
- LDO按压差、总电流、静态损耗、实际铜面积和环境温度算热；“600mA”“低压差”不等于板上持续能力。Buck还查电感Isat/Irms、DCR、电容有效值/ESR/纹波、反馈与补偿条件；不能随意把参考电解全换陶瓷后宣称等效。手焊可行性与整路成本要包含底部焊盘、磁件、端子、散热和装配。
- 散热背板不默认接地。例如XL4016E1的TAB接SW，4/VC通过1µF接VIN而非对地；两路共享导电散热器须防止开关节点被短接。这里是原厂引脚规则，不是已采用该裸芯片方案或已证明持续大电流。[XLSEMI XL4016，Rev1.6引脚表](https://www.xlsemi.com/datasheet/XL4016-EN.pdf)
- 模块接口编号、安装孔电位与机械尺寸来自实物或准确机械图；载板接口的1–4不自动等于模块四角孔号。板外电源模块必须在连接与交付说明中标为板外，不能把未确定的前级写成板上功能。

## STM32F103/F407：封装、电源、启动和时钟

| 情形 | 决策要点与原厂依据 |
|---|---|
| STM32F103C8T6 / LQFP48 | VBAT为1脚，BOOT0为44脚，PB2/BOOT1为20脚。硬件I2C1默认PB6/SCL、PB7/SDA，重映射为PB8/SCL、PB9/SDA；PB7/PB8不是该硬件配对，若软件模拟必须明确。逐个核VDD/VSS、VDDA/VSSA与VBAT，不能套F407的VCAP要求。[ST DS5319引脚表](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf) |
| STM32F407VET6 / LQFP100 | 内部稳压器正常启用时，VCAP1/49与VCAP2/73各自就近2.2µF低ESR陶瓷到地，ESR<2Ω；VCAP不是3V3输入或外设供电输出。该封装没有PDR_ON/BYPASS_REG，不能照搬其它封装的旁路接法。[ST DS8626，封装图与表16](https://www.st.com/resource/en/datasheet/stm32f407ve.pdf) |
| 本地去耦与备份域 | 按实际VDD脚分别就近去耦并配本地总电容；VDDA/VREF有独立去耦要求。没有备份电池时按器件指南把VBAT接VDD并去耦；主电源电容并不保证断电RTC保持。使用一次电池须避免充电/反灌路径。[ST AN4488 §2.2](https://www.st.com/resource/en/application_note/an4488-getting-started-with-stm32f4xxxx-mcu-hardware-development-stmicroelectronics.pdf) |
| BOOT与NRST | F103及这里的F407正常Flash启动需BOOT0低；进系统存储器还须核BOOT1、复位动作及该型号ROM支持的接口。NRST为低有效，按键、下载器和监督器须避免输出争用。SWD插座未引NRST就不能声称提供直接硬件connect-under-reset连接。[ST AN2586](https://www.st.com/resource/en/application_note/an2586-getting-started-with-stm32f10xxx-hardware-development-stmicroelectronics.pdf)、[ST AN4488](https://www.st.com/resource/en/application_note/an4488-getting-started-with-stm32f4xxxx-mcu-hardware-development-stmicroelectronics.pdf) |

复位后可从内部HSI运行，不等于任意时钟精度、目标频率或USB时钟链都已满足。对F103，不能直接使用等待缺失HSE的固件；核HSE/PLL配置和精度，而非一律判“无晶振不工作”。晶体按**完整订货型号**查频率、CL、ESR、C0、驱动功率、温档与封装；同系列不同CL/ESR不能沿用原起振结论。`CL≈C1×C2/(C1+C2)+Cstray`只是负载初值，寄生要声明；另按MCU的临界跨导/增益裕量与驱动条件筛选，再实测启动和频率，不能固定给所有8MHz晶体配22pF。[ST AN2867](https://www.st.com/resource/en/application_note/an2867-guidelines-for-oscillator-design-on-stm8afals-and-stm32-mcusmpus-stmicroelectronics.pdf)

## 模拟参考、采样与上下电

- F407的VDDA与VDD推荐同源；手册容许的300mV差值针对上下电过程，不能当作任意独立电源的稳态许可。VREF+不得高于实际VDDA，还须满足ADC规定的参考下限。精密“3.3V”源可能高于带负容差的VDDA；必须合并误差、滤波压降和时序，不能仅比较标称值。[ST DS8626，表14及ADC条件](https://www.st.com/resource/en/datasheet/stm32f407ve.pdf)
- 外部参考/模拟供电变体要明确互斥装配与BOM中的DNP，并计算VDDA上电位器、开关、外接传感器等全部负载；参考源不一定能带动整条模拟电源。ADC源阻抗、采样时间、RC、输入范围与参考一起审查；电位器与参考同源可以比率测量，不一律增加独立稳压器。
- “掉电高阻”不等于整个电源坡沿零注入。TMUX1511的掉电保护有规定供电和信号范围；外部信号持续存在时还要查开关重新导通门限、MCU注入限制及供电下降速度。[TI TMUX1511，powered-off protection](https://www.ti.com/lit/ds/symlink/tmux1511.pdf)
- 监督器分别核下降门限/误差、迟滞、欠压响应与重新释放延时。TPS3808的CT悬空是典型20ms释放延迟，不能写成40µs或把CT接地当作该配置；增加释放延迟不等于加快欠压关断。典型传播时间不能当最坏保证，外部信号保持而MCU骤降电须测实际波形。[TI TPS3808，reset delay](https://www.ti.com/lit/ds/symlink/tps3808.pdf)

## MOSFET、感性负载与24V驱动

- 标准NMOS低侧是S接地、D接负载负端、负载正端接电源，栅极有默认关断偏置。负载若经通信线或机壳另接固定地，检查是否绕过开关或反灌；需要切正极时选择合适高侧驱动，不能只把同一NMOS移到正极。
- 以**实际VGS下保证的RDS(on)**、SOA、温度及开关损耗选MOS，VGS(th)仅表示开始导通。AO3400A有2.5V栅压的导通电阻规格，但标题电流不代表任意PCB的持续负载。[AOS AO3400A](https://www.aosmd.com/res/data_sheets/AO3400A.pdf)
- 常规低侧续流二极管K接负载电源、A接D；全桥双向电机不能照抄一只跨电机的普通反并联二极管。释放速度、制动能量和供电能否吸收回灌决定钳位/缓冲；负载旁电容先分清对电源地还是跨开关负载，按浪涌与开关损耗判断。[TI感性负载说明](https://www.ti.com/document-viewer/lit/html/SNVAA45)

下表纠正历史需求/点评中的**直接接24V选型反例**，并非替代器件推荐清单；额定/堵转电流、输入容差与回灌峰值仍决定最终方案。

| 器件 | 原厂工作范围及含义 |
|---|---|
| DRV8833 | VM为2.7–10.8V，不能直接24V。[TI手册](https://www.ti.com/lit/ds/symlink/drv8833.pdf) |
| DRV8838 | 电机VM为0–11V，逻辑VCC另为1.8–7V；也不是24V替代。[TI手册](https://www.ti.com/lit/ds/symlink/drv8838.pdf) |
| DRV8847 | VM为2.7–18V，不能因型号接近便称24V兼容。[TI手册](https://www.ti.com/lit/ds/symlink/drv8847.pdf) |
| DRV8701 | 工作5.9–45V，是驱动四颗**外置NMOS**的栅极驱动器；还须设计功率桥、采样与保护。E/P控制接口不同。[TI手册](https://www.ti.com/lit/ds/symlink/drv8701.pdf) |
| BTS7960 | 工作5.5–27.5V；24V母线的上偏差、过压保护和再生制动裕量需认真核算，不能标成43A持续模块。TAB为OUT开关节点，共用散热器须核绝缘。[Infineon手册](https://www.infineon.com/assets/row/public/documents/10/57/infineon-bts7960-ds-en.pdf) |

## CAN/RS485隔离与USB能力

- **隔离要同时成立于信号、电源和回流。** 普通信号隔离器不传输供电；外侧须有隔离DC/DC或独立隔离电源，外侧地不能被电源负端、屏蔽/机壳、调试器或0Ω旁路。省成本改为本地供电共地时，明确称非隔离，并同步修改接口、保护回路与网络定义。
- 先统计方向与控制线。CAN TX/RX需要一去一回：ADuM1200两通道同向，ADuM1201才是一去一回。半双工RS485若需独立TX、RX与DE控制，即使DE和/RE并接也通常需要两去一回；不能只数数据两线。ADuM1301提供2+1通道，选择时再核封装脚号、电平与默认态；自动方向方案须另有确切实现与时序依据。[ADI ADuM120x](https://www.analog.com/media/en/technical-documentation/data-sheets/ADuM1200_1201.pdf)、[ADuM130x](https://www.analog.com/media/en/technical-documentation/data-sheets/ADuM1300_1301.pdf)
- SN65HVD230的VCC推荐3.0–3.6V，不能接本地或隔离5V。隔离电源预算包括收发器**有终端负载**时电流、隔离器、后稳压损耗与预负载；非稳压模块的轻载电压、全容差和启动过冲也须符合接收器件要求。[TI SN65HVD230](https://www.ti.com/lit/ds/symlink/sn65hvd230.pdf)
- 上电/复位时让CAN发送默认释放、RS485发送关闭；核隔离器输入侧失电及外侧仍供电的真值表，不能只依赖未运行的固件。例如隔离器失效默认输出高时，直接接高有效DE会打开发送，修正通道数量后仍须设计默认关闭路径。终端按总线物理末端启用；RS485空闲偏置按双终端、节点负载与门限最坏值计算，不照搬一对电阻声称任意网络可靠。普通GPIO UART也不是RS-232电气接口。
- USB明确速率和PHY：F407片内FS为12Mbit/s；即使外设名有OTG_HS，要480Mbit/s仍需ULPI外置HS PHY，不能仅凭USB插座或“USB2.0”称高速。[ST DS8626 §3.30–3.31](https://www.st.com/resource/en/datasheet/stm32f407ve.pdf)
- 按具体PHY核串阻/上拉，不能把22Ω列为所有STM32必装值。查VBUS检测、ESD回灌与自供电设备的连接时序；改成反相GPIO检测必须有匹配固件，不能推断ROM DFU自动兼容。差分返回路径与保护回流保持短而连续。[ST AN4879](https://www.st.com/resource/en/application_note/an4879-usb-hardware-and-pcb-guidelines-using-stm32-mcus-stmicroelectronics.pdf)

## 保护与地回流

- 常规TVS是跨被保护节点与相应回流的并联钳位支路，不是按器件清单依次串入供电。分别核工作电压、击穿、规定脉冲下钳位、能量与走线电感；“24V TVS”不保证钳在24V。eFuse的关断延迟也不能用典型值证明任意浪涌都低于下游极限。[TI TVS说明](https://www.ti.com/product-category/passive-discrete/diodes/tvs-diodes/overview.html)
- 保险丝/PTC解决过流，单独替换串联防反二极管不能保留防反功能；PMOS防反另查体二极管、栅源钳位和故障电流路径，导通后也不等于反向电流阻断。SS14压降随电流/温度/厂家变化，不能固定扣0.7V。[MDD SS14系列手册](https://www.microdiode.com/uploadfiles/PDF/SS12-THRU-SS1200-SMA.pdf)
- 按实际高di/dt回路划分摆放、去耦和回流，让电机/开关电源脉冲不经过MCU/ADC参考。AGND、DGND、PGND名称不等于必须割地，更不等于隔离；不要把VDDA电源滤波磁珠机械搬到VSSA/AGND回流中。采用单点连接或分区须说明器件依据并确认返回路径；USB和时钟不能跨无回流通道的地缝。[ADI MT-031](https://www.analog.com/media/en/training-seminars/tutorials/MT-031.pdf)、[ST AN4488电源方案](https://www.st.com/resource/en/application_note/an4488-getting-started-with-stm32f4xxxx-mcu-hardware-development-stmicroelectronics.pdf)
- 线宽、铜厚、过孔、端子和散热共同限制额定能力。泪滴是制造/机械裕量，不替代功能、线宽、环宽或DRC；操作见[PCB指南](pcb.md)。

只验证本次改动涉及的功能。无实板时明确上电、启动/堵转、温升、掉电反灌及EMC尚未测试；ERC/DRC、网表一致和原厂参数筛选均不能单独证明量产就绪。
