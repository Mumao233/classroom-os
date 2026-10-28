# PR-00 工程检查工具与 CI 结果

日期：2026-10-28。基线：`43fa23c141e3350e90d72ed7768e3b8de7d68337`，本地分支 `feature/web-alpha-0.1.0`。

## 已新增的文件

- `tools/pr00/run_checks.py`：支持显式 `--python`/`--node`/`--powershell`，顺序执行完整原测试与静态检查，非零失败退出，独立日志/JUnit/JSON/Markdown摘要。
- `tools/pr00/runtime_guard/sitecustomize.py`：测试子进程专用，限制loopback网络、拒绝外部DNS/实际子进程与仓库私人数据/配置/模型路径；不由业务应用导入。
- `tools/pr00/node_guard.cjs`、`selftest_guards.py`、`selftest_guards.cjs`：Node防误调用与Python/Node合成防护验证。
- `tools/pr00/selftest_junit.py`：主用例与子测试门禁的合成自检。
- `tools/pr00/constraints-py310-windows.txt`：原requirements在独立Windows/Python3.10.4环境的完整解析约束，不修改旧requirements。
- `.github/workflows/pr00-windows.yml`：Windows/Python3.10/Node24.19.0，仅pull_request和手动触发、contents:read、无凭据/收费/部署/发布步骤。
- `docs/web-alpha/TESTING.md`：可复现命令、先配置非C盘temp/cache、失败与验证边界说明。

## 最终本地等价流程

```powershell
& 'J:\class-assistant\classroom-os\work\pr00\venv\Scripts\python.exe' tools/pr00/run_checks.py --python 'J:\class-assistant\classroom-os\work\pr00\venv\Scripts\python.exe' --node 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --output-dir work/pr00/runner-acceptance
```

退出码 **0**。Python **3.10.4**；Node **24.19.0**；Windows PowerShell **5.1.26100.9711**。

|检查|实际结果|
|---|---|
|Python与Node防护自检|通过；loopback真实合成通信；外网/DNS/通配绑定/实际Codex进程被拒绝|
|JUnit门禁合成自检|通过；524父用例+1000子测试也拒绝；少子测试/失败/错误/skip/空报告均拒绝|
|原完整pytest|**525 passed + 171 subtests passed**；74.68秒；零失败、错误、跳过|
|JUnit结构核对|525个实际testcase节点；testsuite总数696=525+171；不能以696替代父用例覆盖|
|九套原Node测试|全部通过|
|Python AST|44个原文件+4个新增工具，共48个，通过|
|六个应用JS语法|通过|
|三个原PowerShell启动/安装/停止脚本|只解析，通过，未执行|
|git diff --check|通过；运行时新增文件尚未intent-to-add，最终新增文件diff检查由主代理补做|
|CI YAML/约束文件校验|触发器/只读权限校验通过；constraints与本轮pip-freeze条目精确一致|
|旧文件完整性|149个原跟踪文件SHA-256全部不变；见ci-original-files-check.json|

最终证据：`work/pr00/runner-acceptance/results.json`、`summary.md`、`pytest-junit.xml`及各步骤日志。完整依赖记录在 `work/pr00/pip-freeze.txt`。

## 修正与失败留痕

第一次 `work/pr00/runner-validation` 返回失败：523父用例通过、2项DPAPI测试失败、171子测试通过。原因是新测试防护在 `open` 审计中动态选择Path类，而旧测试故意mock `os.name='posix'` 验证非Windows错误分支，导致防护干扰原用例。已固定使用初始化时的宿主Path类型，并增加对应合成自检；未修改旧测试或业务代码。

第二次 `work/pr00/runner-validation-final` 全部通过。随后独立复核补强JUnit父/子用例计数、清除继承的选测参数与用户site/插件自动加载；最后一次 `runner-acceptance` 完整复验通过。三个目录全部保留，未删除失败证据。

## 限制

**GitHub Actions 尚未远端执行**，本轮没有push/commit/合并/发布；结果是CI定义检查与本地等价runner通过。CI的Python3.10补丁版本由setup-python解析，可能不同于本地3.10.4；实际版本将写入每次日志，Node已固定与本地相同。

进程级防误调用不是OS沙箱。现有测试为合成/mock与127.0.0.1回环测试，Windows真实DPAPI仅处理合成临时密钥；没有真实麦克风、真实Codex/收费API、模型权重下载、Web E2E或真实浏览器新增验收。未改业务文件、旧tests或原requirements。
