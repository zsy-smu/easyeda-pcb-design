# 交付文件清单校验

`scripts/check_delivery.py` 只核对文件字节，不检查原理图、网表、实体铜、DRC 或制造正确性。用于把已经完成对应工程验收的交付目录冻结为可复核清单。Python 3.10+，仅标准库。

```sh
python scripts/check_delivery.py snapshot DELIVERY_DIRECTORY --out MANIFEST.json
python scripts/check_delivery.py verify MANIFEST.json --out REPORT.json
python scripts/check_delivery.py verify MANIFEST.json --root COPIED_DELIVERY_DIRECTORY --out REPORT.json
```

命令相对当前工作目录解析路径；没有默认工程位置。输出父目录不存在时会创建。推荐把清单和报告放在交付目录外；放在目录内也支持。

## 清单与结果

清单格式为 `easyeda-delivery-manifest/v1`：

- `root`：创建时目录的绝对路径；验证副本时用 `--root` 覆盖。
- `manifestPath`：清单位于目录内时的准确相对路径，否则为 `null`。
- `files`：按相对路径排序的 `{path, size, sha256}` 数组。路径使用 `/`，大小为字节数，SHA256 为小写十六进制。

验证报告列出 `changed`、`missing`、`extra`。只忽略清单明确记录的自身路径、此次读取的清单路径、此次报告路径；不按扩展名或整目录忽略。报告不能占用清单内的受检文件路径。空目录、时间戳和文件权限不纳入字节清单。

退出码：`0` 为创建成功或验证相同；`1` 为检测到文件变化；`2` 为非法输入、读取期间变化或不支持的文件系统条目。非法输入不会用“通过”报告覆盖已有结果。

## 边界与使用要求

- 拒绝绝对清单条目、`..`、非规范路径、重复或大小写冲突条目、未知字段、非法哈希及重复 JSON 键。文件名采用可跨 Windows/POSIX 使用的形式；例如 Windows 保留名、结尾空格或句点不支持。
- 不跟随符号链接或 Windows junction/reparse point，包括输入、输出的祖先目录。发现后退出 `2`，不会把外部目录内容算成交付物。
- 拒绝清单与报告同路径或硬链接别名；不覆盖无关的已有输出文件。重复运行可覆盖同类工具生成的清单或报告。创建清单会重新定义基线，不能替代先前清单的验证。
- 校验时保持目录静止。工具检测单文件读取期间的身份、大小和修改时间变化，但不提供操作系统级全目录事务快照。SHA256 清单证明与基线的字节关系，不证明基线可信；需另行保存可信清单或其哈希。

运行测试：

```sh
python -m unittest discover -s tests -p test_delivery.py
```

测试全部使用临时目录。实际符号链接创建依赖系统权限；无权限时该例跳过，reparse 标志拒绝逻辑仍独立测试。
