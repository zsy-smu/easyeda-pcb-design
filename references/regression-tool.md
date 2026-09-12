# 可移植软件回归

`scripts/run_regression.py` 使用 Python 3.10+ 标准库，从脚本位置找到 skill，递归加载 `tests/` 下全部 `test_*.py`。测试模块独立加载，支持 `load_tests`；不支持包相对导入。自身测试只运行临时最小 suite，不递归启动整个回归。运行测试前应信任本地测试代码；这不是沙箱或 EDA/实板验证。

本机可从任意工作目录运行；报告放在 skill 目录外：

```powershell
py -3.10 -X utf8 "$env:USERPROFILE/.codex/skills/easyeda-pcb-design/scripts/run_regression.py" --out "$env:TEMP/pcb-regression.json"
```

复制整个 skill 到新目录后，只改脚本路径，例如：

```sh
python /work/tools/easyeda-pcb-design/scripts/run_regression.py --out /work/reports/pcb-regression.json --strict-skips
```

制造几何测试需要 [声明的 Shapely 依赖](../scripts/requirements-manufacturing.txt)，在项目隔离环境安装；缺失时几何测试跳过，其余适用测试仍运行。入口本身不安装任何依赖。

| 结果 | 退出码与含义 |
|---|---|
| `PASS` | `0`，实际运行的测试通过，无跳过/预期失败；仅覆盖软件测试范围 |
| `PARTIAL` | 默认 `0`，含跳过或 `expectedFailure`，不表示完整通过；`--strict-skips` 在有跳过时返回 `1` |
| `FAIL` | `1`，断言失败或 `unexpectedSuccess` |
| `ERROR` | `2`，测试错误、发现/执行异常、零测试，或脚本/测试输入在运行中改变 |

JSON schema 为 `easyeda-regression-report/v1`。读取 `testsRun`、实际成功数 `passed`、`failures/errors/skips` 及各 `*Details`；跳过带原因，发现错误另列 `discoveryErrors`，入口异常列 `runnerErrors`。失败/错误为 unittest 事件数：同一测试多个失败子测试、类级 setup 错误时，数值不一定相加等于 `testsRun`。不要只凭退出 `0` 宣称全部测试通过。

`inputs` 在执行前保存 `scripts/`、`tests/` 和存在时的 `assets/` 文件相对路径、大小、SHA256，排除生成的字节码和缓存；运行后再核对。`inputsUnchanged=false` 时保存前后清单并返回 `ERROR`，不能把结果用于单一源码版本。外部依赖、元数据、参考文档和 EDA 状态不在该哈希证明内。

输出路径不得在 skill 内，不能含 symlink/junction/reparse 路径或指向硬链接；这些检查在加载测试前执行。已有输出只允许替换相同 schema 的回归报告，使用原子替换；输入/输出无效时可能没有 JSON，应同时检查退出码和 stderr。

[GitHub Actions 模板](../assets/ci/pcb-skill-checks.yml) 是供其他项目复制的资产；本仓库的实际 CI 配置见 [工作流](../.github/workflows/pcb-skill-checks.yml)，其中 `PCB_SKILL_PATH` 为 `.`。迁移到其他仓库时将该值设为实际 skill 路径。工作流在 Windows/Linux、Python 3.10/3.12 安装声明的 Shapely 并运行回归，上传 JSON；CI只接受具名的预期跳过：Linux上的Windows大小写路径测试，以及Windows上两项符号链接测试的1314权限错误；其他跳过仍失败，接受的跳过仍报告 `PARTIAL`。Windows测试子进程使用规范临时路径，避免托管环境的8.3短名触发路径别名拒绝；检查器保护逻辑不变。另一个隔离且无 Shapely 的任务要求缺依赖被报告为 `PARTIAL`，不将它算作几何测试通过。配置文件存在不表示 CI 已成功执行，实际结果以对应提交的 GitHub Actions 运行记录为准。Action 用法依据 [setup-python](https://github.com/actions/setup-python)、[checkout](https://github.com/actions/checkout) 和 [upload-artifact](https://github.com/actions/upload-artifact) 官方文档；部署时按所在仓库的版本固定规则维护。
