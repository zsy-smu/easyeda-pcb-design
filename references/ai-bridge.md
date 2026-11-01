# AI Bridge：从连接到实际文件的操作手册

用户的AI-EDA Bridge由MCP Server、EDA扩展和公开EDA SDK组成。先用已连接的MCP工具；工具未暴露但同一服务已有命令端口时，用本skill的`ai_bridge_client.cjs`。本页记录的是已读AI-EDA实现，不把其它网关的协议混进来。新任务的整条执行路线见[执行手册](execution-playbook.md)。

## 1. 分清三种连接

| 路径 | 端点/入口 | 作用与边界 |
|---|---|---|
| 用户AI Bridge | MCP stdio；扩展默认WS8765；脚本命令WS8766 | MCP调用`eda_*`工具；原始命令是`pcb.getComponent`等，不是MCP工具名。8765给扩展连接，脚本连接8766。配置可变，先核当前源码/进程。 |
| run-api-gateway | 默认HTTP/WS49620–49629 | 另一套实现；用`/health`服务标识、`/eda-windows`、`/execute`，执行`{code:...}`。不能向8766发HTTP，也不能把AI Bridge命令JSON发给它。 |
| 已有CDP公开SDK封装 | 由当前客户端启用的调试端口 | 仅在任务已有可用且已核身份的入口时复用。不是AI Bridge必需组件，不为找SDK默认重启EDA或开调试端口。 |

AI Bridge的8766本身没有HTTP `/health`或可靠的服务名握手。端口监听不证明EDA已连接；要取得匹配请求ID的真实命令返回。也不以声明“122个工具”的旧README推断当前能力：看当前工具目录、协议、handler、编译版本和已加载扩展。

## 2. 首次准备与重连

发现当前AI-EDA根目录（本机线索`D:/ai-eda`，不是移植后的强制路径）。应有`mcp-server/dist/index.js`、`eda-extension`及相关依赖。先检查已有服务和MCP工具，复用正在运行的实例；同一服务只有一个写入者。

若是已有源码但未构建，按该仓库的package脚本分别构建server与扩展；已有依赖/构建可用时不重复安装。安装或切换`.eext`需要通过EDA扩展管理器，不能把磁盘包版本当已加载版本。当前AI客户端未必读取仓库的`.mcp.json`；确认其实际注册的MCP入口指向`node <AI_EDA_ROOT>/mcp-server/dist/index.js`，而不只看配置文件存在。

所读仓库的构建命令是`npm --prefix <AI_EDA_ROOT>/mcp-server run build`和`npm --prefix <AI_EDA_ROOT>/eda-extension run build`，后者在`build/dist/`产出eext。只有需要新构建且依赖确实缺失时，再按该仓库锁文件安装依赖，不在其它目录装“最新插件”来替代原实现。

用户已有连接授权且服务确实未运行时，可启动已核对的构建；不要杀占用端口的未知进程。Windows后台启动示意：

```powershell
$aiEdaRoot = 'D:/ai-eda' # 替换为实际源码/构建目录
$bridgeLogDir = 'D:/pcb-task/logs' # 本次项目内已创建的目录
Start-Process -FilePath (Get-Command node).Source `
  -ArgumentList @((Join-Path $aiEdaRoot 'mcp-server/dist/index.js')) `
  -WorkingDirectory (Join-Path $aiEdaRoot 'mcp-server') -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $bridgeLogDir 'server-stdout.log') `
  -RedirectStandardError (Join-Path $bridgeLogDir 'server-stderr.log') -PassThru
```

先确保这些日志是本次新文件，保留PID和启动参数。若MCP宿主已经运行该server，不再起第二份。该实现WS_PORT只调整扩展监听端口，脚本端口在所读实现中仍为8766；不可假定两者一起偏移。新服务不得靠无限等待或重启“修复”。

在EDA中使用 **AI Bridge → Connect/连接AI**；近期实现也有启动自动连接，但要以实际响应为准。用户要求后台操作时，不移动鼠标或抢前台。连接扩展、首次导入等确需用户点击的动作，先准备好具体文件/目标，再说明唯一待操作项。

## 3. 先做轻量探针，不先扫描整板

已暴露MCP时直接在当前工具目录中找到这些实际工具名（命名空间由宿主决定），按各自schema调用：

1. `eda_connection_status`：看`connected`和`wsPort`；这不扫描设计。
2. `eda_dmt_get_document_info`：取得活动页UUID、类型、父工程UUID。
3. `eda_dmt_get_project_info`（参数`uuid`）及`eda_dmt_list_boards`/`eda_dmt_get_board_info`（参数`name`）：核工程和板映射。
4. 目标不在当前页时用`eda_dmt_open_document`（参数`uuid`）打开，再重复身份核对；之后才读该页的SCH/PCB数据。

不要把`eda_sys_get_document_info`当第2步，它是环境信息。MCP结果还可能把JSON放在`content[].text`中，先解析真实返回/`isError`，不按原始8766响应结构套读。工具目录没有这些工具时才使用下面的命令客户端，而非调用一个未注册的名字。

```powershell
node scripts/ai_bridge_client.cjs describe --bridge-root "D:/ai-eda" --out "D:/pcb-task/evidence/bridge-build.json"
node scripts/ai_bridge_client.cjs probe --endpoint ws://127.0.0.1:8766 --out "D:/pcb-task/evidence/bridge-probe.json"
```

`describe`只读本地协议和构建：校验server/extension命令集合相同，记录版本与构建哈希，不证明当前已加载。`probe`只读取活动文档与环境，记录文档UUID、父工程UUID、类型、tabId和编辑器版本；不调用会加载全库的设计概览/组件getAll，也不操作画布。

重要区别：`sys.getDocumentInfo`在该AI Bridge里返回**环境信息**；真正活动页身份用`dmt.getDocumentInfo`。不要从前者读不存在的文档UUID。probe成功但`readyForGuardedCalls=false`时，说明端口能响应但没有可识别活动文档，需要先定位/打开任务页。

Node22+可用内置WebSocket；旧Node可用`--bridge-root`从已有AI-EDA依赖加载`ws`，不自动联网安装。所有`--out`和`--file-out`要求新路径，完整结果写文件，终端只输出摘要。

## 4. 单个读写动作的实际调用

原始命令端口请求/响应：

```json
{"id":"unique-request-id","command":"dmt.getDocumentInfo","params":{}}
{"id":"unique-request-id","type":"response","success":true,"data":{"uuid":"...","parentProjectUuid":"...","documentType":3}}
```

MCP工具优先使用当前实际暴露的schema；不要把`eda_pcb_*`名称当原始命令。CLI从JSON文件读参数，避免PowerShell内联JSON引号与多行转义：

```powershell
# component.json内容形如 {"id":"从当前列表实际读到的图元ID"}
node scripts/ai_bridge_client.cjs call pcb.getComponent `
  --params-file "D:/pcb-task/inputs/component.json" `
  --expect-document ACTUAL_PCB_UUID --expect-project ACTUAL_PROJECT_UUID `
  --out "D:/pcb-task/evidence/component-before.json"
```

写操作使用相同身份参数，并显式加`--write`。它是执行模式标记，不是另起一轮用户审批；已有任务授权时直接使用。客户端默认将未知命令视为可能写入。每次写前后读回文档/工程/类型/tabId，不提供原子事务保证；执行期间保持单写入者，避免切页。身份变化、部分失败或超时后查真实状态，不能自动重试创建/删除。

CLI故意不替用户自动切页。通过已确认MCP的DMT打开目标后重新probe，或在受控SDK/DMT流程中切页并核身份；不要在CLI对另一页的guard下发送`dmt.openDocument`并误把目标页变化视为成功事务。新建工程/板同理：先按当前DMT能力创建、读回身份，再使用带guard的编辑调用。

### 已读handler的常用参数

以下是源码观察，不是跨版本接口保证。先读取小范围基线，检验实际返回和单位，再批量使用。

| 命令 | 参数示例/用途 | 注意 |
|---|---|---|
| `dmt.getDocumentInfo` | `{}` | 活动文档，不扫描设计 |
| `dmt.getProjectInfo` | `{"uuid":"实际父工程UUID"}` | 不因同名省略UUID |
| `dmt.listBoards` / `dmt.getBoardInfo` | `{}` / `{"name":"实际Board名"}` | 建立板→SCH/PCB映射 |
| `dmt.listTabs` / `dmt.openDocument` | `{}` / `{"uuid":"实际页UUID"}` | 切页后重新核身份 |
| `lib.searchDevice` | `{"key":"准确器件名","itemsOfPage":20,"page":1}` | 搜索空时不当作不存在；可走准确供应商编号查询 |
| `lib.getDeviceByLcsc` | 依当前handler/schema | 完整Device与引脚/封装读回后才放置，不能以结果顺序配对 |
| `sch.placeComponent` | `{"deviceId":"libraryUuid:deviceUuid","x":0,"y":0,"rotation":0}` | 返回临时ID；恢复位号、身份和采购字段后重开核对 |
| `sch.drawWire` | `{"points":[{"x":0,"y":0},{"x":20,"y":0}]}` | 当前handler只取points，不会因多传net字段就自动命名；网名用已支持的flag/port/label并核网表 |
| `sch.getNetlist` / `sch.save` | 依schema / `{}` | 网表可能较慢；保存后还要检查持久化和库关联 |
| `pcb.listComponents` | `{"filter":"U1"}` | 紧凑列表没有完整采购/焊盘信息 |
| `pcb.getComponent` | `{"id":"真实图元ID"}` | 看实际组件和引脚上下文，勿把位号当ID |
| `pcb.listPrimitives` | `{"type":"PAD"}`等 | 指定真实类型；包含未联网几何，不只按网络枚举 |
| `pcb.setComponentProperties` | `{"id":"...","properties":{"x":100,"y":100,"rotation":90}}` | 受支持键有限；不能借此修改Device/Footprint。按源和读回校准单位 |
| `pcb.batchMove` | `{"moves":[{"id":"...","x":100,"y":100,"rotation":90}]}` | 可部分成功；检查failed/results及全部实际坐标。旧源码此方法内部并行，不视为原子提交 |
| `pcb.drawLine` | `{"points":[{"x":0,"y":0},{"x":100,"y":0}],"layer":1,"width":10,"net":"实际网名"}` | 此handler直接传mil到PCB API；width=10是10mil，非10mm。分段创建仍需读回全部段数 |
| `pcb.createPour` / `pcb.rebuildPour` | 依当前schema | 铜边界存在不证明填充/连接成功 |
| `pcb.importChanges` | `{}` | 当前返回initiated/applied:null，仅说明已发起，不能说已应用 |
| `sys.runDrc` | `{"type":"pcb"}`或`{"type":"sch"}` | 默认后台。看原始result，不能只数顶层分组；SCH布尔值不提供细节 |
| `pcb.save` | `{}` | 看nativeSaved/nativeResult；persistenceVerified:false仍须重开/导出验证 |
| `sys.exportProjectFile` | 见下节 | 明确工程/页与includeBase64 |
| `pcb.exportGerber` | `{"includeBase64":true,"fileName":"Review"}` | 实际File字节才可检查，hasFile不是文件已在磁盘 |

未列参数不要靠名字猜。看当前工具schema、`mcp-server/src/protocol.ts`和`eda-extension/src/handlers/`对应handler；存在源码不证明当前扩展有该命令。

## 5. 单位、结果和文件

项目内部布局计算统一用mm，调用前按每个接口转换。`mm/0.0254=mil`是已确认mil接口的转换；公开getter、component pin、hole和原生源可能不一致，先以两个真实焊盘与机械图校准，记录每种对象的单位及角度。不要用一个`SCALE`套所有数据。

响应至少分三层：传输外壳success、handler的success/verified/failed、原生动作是否保存/应用/产生文件。客户端会拒绝明确的应用失败与部分失败，但`RESPONSE_RECEIVED`**不是设计通过**，`nativePersistenceVerified`始终为false。DRC原始result、未连接、实铜和装配仍分别检查。

服务普通等待约30秒，所读实现中部分网表/导出动作约180秒；CLI默认35/185秒，也可按确切动作设置`--timeout-ms`。超时只停止等待，不取消EDA里的操作。不因超时反复放件、导入或启动服务。

导出实际工程和制造文件（先按该页类型保存，导出接口本身不保存）：

```powershell
node scripts/ai_bridge_client.cjs call sys.exportProjectFile `
  --expect-document ACTUAL_DOC_UUID --expect-project ACTUAL_PROJECT_UUID `
  --file-out "D:/pcb-task/native/Review.epro" --out "D:/pcb-task/evidence/project-export.json"
node scripts/ai_bridge_client.cjs call pcb.exportGerber `
  --expect-document ACTUAL_PCB_UUID --expect-project ACTUAL_PROJECT_UUID `
  --file-out "D:/pcb-task/gerber/Board_Review.zip" --out "D:/pcb-task/evidence/gerber-export.json"
```

CLI请求base64，校验长度、编码和ZIP签名，写入新文件并记录SHA256，回执不再重复保存base64。此验证不替代ZIP CRC、板框、钻孔、层或库闭合检查。当前实现有32MiB字节传输上限；超过上限不是空文件通过，要另找受支持导出方式。

## 6. 故障判别和下一步

| 现象 | 判断与动作 |
|---|---|
| 拒绝连接 | 查原有进程/端口、实际启动参数和日志；不控制鼠标、不直接重启EDA |
| `EDA extension is not connected` | 服务器能响应，扩展未连接；确认加载包/端口，使用Connect后再probe |
| `Unknown command` | 协议或已加载扩展不匹配；读当前能力，不复发同一失败指令 |
| sys信息有版本却无UUID | 命令用错，改用dmt.getDocumentInfo，不虚构身份 |
| 空列表/仪表盘“--” | 可能未收集、错页或不支持，不等同于零器件/零错误 |
| batch部分失败/写后读回不符 | 对照基线逐个核对；完成者不重放，未决者查状态，保留恢复点 |
| 发起导入后仍报网表错误 | 获取完整原生差异，核库、属性、多单元和引脚；不直接清空PCB重导入 |
| File生成但本地没有文件 | 未取回字节；使用includeBase64或受支持保存方式，实际读包验证 |

源码证据：AI-EDA的`mcp-server/src/ws-bridge.ts`、`protocol.ts`、`tools/connection.ts`及扩展`dispatcher.ts`、`document-guard.ts`、各handler；2026-11-01读取的server package为2.1.0、扩展源码为1.2.16，README的版本/数量与包内容不同。本skill测试只证明客户端协议及静态源码观察；未连当前EDA做新一轮实板读写，不冒充已验证在线连接。
