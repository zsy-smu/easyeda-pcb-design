# 可重建的地铜候选：避让、连接和孔洞必须同时保留

用于已有可靠归一化几何、需要生成/重建实心铜区的后台工作流。优先使用当前EDA可靠的原生覆铜；本工具提供离线候选，不会直接修改工程。

```powershell
python scripts/build_ground_plane.py GROUND_INPUT.json --out GROUND_CANDIDATE.json
```

输入模板见[geometry-input.json](../assets/examples/geometry-input.json)。模板中的数值是合成小板示例，不能当新项目要求。Python3.10+，Shapely2.x；同目录依赖`check_copper_connectivity.py`和`check_manufacturing.py`。

## 归一化数据合同

`schema=easyeda-ground-input/v1`，`unit=mm`；`sourceRevision`应绑定当前只读几何/原生快照的版本或哈希，不用“latest”代替。板框`board`为有效XY多边形。显式输入`net`、`edgeClearance`、`minBridgeWidth`、`minIslandArea`；不能照抄历史板的0.4mm或固定GND网名。

- `copper`列出当前两面**全部**焊盘、铜线、过孔环铜和已有实心铜；每个对象`id/kind/net/layers`明确，NC为null，layers为[1]、[2]或[1,2]。不是只列已命名网络，未联网焊盘同样是障碍。
- `kind=pad`使用铜检查相同的中心/宽高/rotation/shape；`track`用`points/width`；`circle`用`x/y/diameter`；`polygon`用`points`。坐标逆时针mm；孔洞多边形先准确分割，禁止只传外环。
- `clearanceByNet`为每个异网分别给出铜净距，NC键为`NC`。没有依据的高压/隔离间距不能由工具自动编造。机械孔、槽和垫片不靠此项解决。
- `holes`包含全部通孔/过孔/定位孔/槽，使用circle或track（胶囊槽）几何；必须有`plated`布尔、`net`（明确NC或NPTH可null）和非负`clearance`。异网/NC的PTH还按所属网络/NC避让，独立原铜先扣孔。不能因库焊盘列表没给定位孔而把它丢掉。
- `keepouts`包含天线、安装垫片、隔离/工具等实际禁铜几何与各自非负`clearance`，当前按两面共同禁布。单面不同规则先分开候选处理，不能静默忽略。
- `anchorPadIds`只选择确定应接主地的**实际焊盘**，不要把所有叫GND的via当锚点。候选经实际同网几何和明确PTH回到锚点才保留。多个锚点本身是否已经等电位还要由完整铜连接审计证明；仅保留“碰到某个地焊盘”的铜不保证全板GND接通。

## 生成步骤与失败处理

1. 板框减板边退让；扣除所有异网铜+间距、孔/槽及机械禁铜。
2. 按`minBridgeWidth`做形态开运算并与原允许区相交，减少细碎区域。这是几何清理参数，**不是每一条颈部都满足最小线宽的数学证明**。
3. 按真实同面接触建立候选区+已有同网铜的图；已有铜也先扣全部孔，避免名义地轨跨过NPTH长槽却被当作接通。只有指定同网PTH可连接两面。
4. 排除过小/不回到指定焊盘的区域，在`discarded`中给出原因。
5. 对每个带内孔区域，取一个孔内部代表点作切分线，递归切成无内孔多边形；比较分割前后并集的对称差，面积误差超过1e-7mm²拒绝输出。绝不把孔涂实来迁就接口。

输出`status=CANDIDATE`、fills、每面面积、丢弃原因和输入哈希。0表示候选计算完成，2表示输入/依赖不支持；没有原生或制造PASS含义。若某面没有可连接区域，其fills可以为空，检查锚点/通孔/阻挡，不通过盲加孔填满它。

## 写回、渲染与复验

1. 核对sourceRevision仍是当前板，按本版SDK真实层映射写实心多边形。每个fill输出`layer/net/points`；写入前转换当前接口单位，mm到已确认mil接口为除0.0254。不要把JSON直接当原生文件导入。
2. 如使用已确认的旧版JSON行式原生FILL，某次观察到的结构为`["FILL", id, 0, net, layer, 0, 0, [path], 0]`，path为`[x0,y0,"L",x1,y1,...]`。这是版本相关适配示例，先在副本写一块、原生重开/导出验证，再批量；未识别记录全部保留。优先当前可靠API。
3. 渲染使用外环+内环的compound path，或画已经无损分割的fills。把`polygon.exterior`直接填色会掩盖孔洞；不要依据这种假预览去删真实铜。
4. 清理确认被替代的旧铜区，保留不相关记录；读回对象数量/层/网/面积。实心FILL不是自动覆铜指令，后续任何走线或器件变化都需要重新生成。
5. 导出真实Gerber，运行[实际铜检查](copper-tool.md)及原生DRC/未连接检查；重新看关键返回路径、板边、孔垫片、最窄处。候选计算通过不能盖掉未连接报告。
