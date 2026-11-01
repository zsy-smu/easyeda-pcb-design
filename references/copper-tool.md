# 实际铜连接检查：不能靠同名网、颜色或一截铜碰到焊盘过关

用于布线/覆铜完成后、换层/移动通孔后、怀疑断线或跨层短路时。脚本读取真正导出的两层铜与钻孔，不连接EDA。

```powershell
python scripts/check_copper_connectivity.py MANUFACTURING.zip --expected COPPER_EXPECTED.json --out COPPER_REPORT.json
python scripts/run_copper_benchmarks.py --out-dir NEW_EXAMPLES_DIRECTORY
```

Python3.10+，Shapely2.x，依赖同目录`check_manufacturing.py`。返回0为**所支持铜检查通过**，1为缺陷，2为输入/格式/依赖不支持。新样例目录会生成可打开的ZIP、预期JSON和每个反例的报告。样例合格指坏板被拒绝、修复板被接受，并非所有样例板都应PASS。

CLI报告绑定`inputs.export/expected`的文件名与SHA256，可交给`check_evidence.py`核时效；schema为`easyeda-copper-report/v1`，可请求`actual-two-layer-copper`、`physical-pad-coverage`、`pth-npth-connectivity`、`explicit-nc-contact`。它不声明原生文档身份；文件变更后须重做铜检查，不给旧报告补哈希。生成器CANDIDATE不接受为铜检查报告。

## 输入合同

```json
{
  "schema":"easyeda-copper-expected/v1", "unit":"mm", "frame":"gerber",
  "expectedPadCount":2, "minPadCoverage":0.98, "rejectUnassignedIslands":false,
  "files":{"top":"top.gtl","bottom":"bottom.gbl","pth":["pth.drl"],"npth":["npth.drl"]},
  "pads":[
    {"id":"physical-pad-1","ref":"J1","pin":"1","net":"VOUT","layers":[1],"x":2,"y":3,"width":1.2,"height":1.2,"rotation":0,"shape":"circle"},
    {"id":"physical-pad-2","ref":"J2","pin":"1","net":"VOUT","layers":[1,2],"x":8,"y":3,"width":2,"height":2,"rotation":0,"shape":"oval"}
  ]
}
```

- `pads`来自**独立引脚设计+真实封装/位姿**，不能从Gerber当前铜反推它应当有哪些网络。`expectedPadCount`来自完整物理焊盘清单；一脚多块焊盘分别列，物理`id`唯一，`ref.pin`可重复。`net:null`是明确NC，不能把全部NC合成一个网络。
- 坐标与Gerber同一mm笛卡尔系。画布可能Y向下或有平移；先用三个不共线的已知焊盘校准，记录变换，再变换全部中心/角度/层。不能只移动Gerber去迎合预期。`rotation`绕中心逆时针，支持rect、circle、oval（胶囊形，不是数学椭圆）。自定义多边形/真实椭圆先扩展适配与测试，不能换个名字冒充。
- 所有钻孔文件明确归入PTH或NPTH；空列表表示确实没有。重复的PTH via子集按完全相同的几何去重，报告原始记录数和物理孔数。互相重叠的PTH/NPTH分类会拒绝。孔/槽的存在与尺寸另用制造检查核对，不能漏掉一个孔后声称铜连接无误。
- 默认逐层覆盖率98%是解析/几何匹配容差，不是允许生产缺铜2%的标准。查看每个`padCoverage`和缺损形状；按本板分辨率设更严阈值。孔面积从预期与实际铜中同时扣除，剩余环铜才是分母。只要细线穿过中心不能冒充完整焊盘。
- `rejectUnassignedIslands=false`仍输出无已知焊盘归属的铜岛；明确净空/屏蔽设计后决定是否允许，不能自动猜它们是GND。空铜、缺焊盘或全NC的输入不能空集合通过。

## 具体如何判断

1. 按实际光圈形成圆/矩形/胶囊形flash和有宽度的直线；封闭区域按轮廓奇偶形成外边和内孔。
2. 从两面铜减去**全部PTH和NPTH**；每面分成真实连通铜块，同面接触归为同一分量。
3. 只有实际PTH铜桶允许把接触孔壁的铜块跨层连接。NPTH只减铜，绝不桥接两面。底层线碰到异网通孔仍会短路。
4. 逐焊盘逐面比较铜覆盖，再把独立预期网络附到实际铜分量上。一个分量挂了两个网络→短路；一个网络分到多个分量→断网；NC接到其他焊盘→意外连接。
5. 不在算法中用网名联合孤立铜。缺焊盘和断网分别报，不因“其余端点都在一块铜”而忽略丢失的端点。

## 输出如何用于修复

| 字段 | 下一步 |
|---|---|
| `missingOrDamagedPads` | 回查这块物理pad的封装源、面、旋转、孔和flash；不要用提高容差掩盖缺焊盘 |
| `copperShorts[].pads/nets` | 按对应真实层查整个铜分量；尤其PTH环铜、铜桶、残留旧粗线和FILL |
| `netConnectivity[].groups` | 从各组选择真实端点检查缺口或缺via，保持所需线宽补全；不能只消飞线 |
| `ncContacts` | 查NC端附近穿线、焊盘合并、错误PIN_NET；NC不等于可以碰任何铜 |
| `unassignedIslands` | 按层/包络回查功能；确认删除或合理连接，不能凭同色认定接地 |

## 支持边界与真实验证

只支持两层、通孔贯穿、实心C/R/O光圈、直线描绘和有效封闭区域。宏、孔光圈、clear/mixed polarity、铜弧、多层/盲埋孔等返回不支持；不支持不是检查通过。矩形扫掠按端点光圈凸包，孔边/圆弧由多边形近似；接触/极窄连接仍需制造与线宽检查。此工具不计算载流、回路电感、阻抗、温升，也不取代原生DRC。

开发基准使用过已完成双板版本的150/243个独立物理焊盘、实际两面铜和全部钻孔；两板均无本检查范围内的短路、断网或缺焊盘。它们不是公开测试夹具，也不是用户后来同步后的最新工程认证。公开反例覆盖：同层交叉、断线清短路、换层后碰异网PTH、残留细线假焊盘、PTH与NPTH区别、NC误连和浮岛。
