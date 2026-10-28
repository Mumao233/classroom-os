# PR-00 工程检查

本入口保护本地 v1.3.0 的既有回归。新增工具不改业务实现、旧测试和 `requirements*.txt`，也不宣称实现 Web 端到端测试。

## 在 Windows 运行

需要 Python 3.10、原 `requirements-dev.txt` 依赖、Node.js 与 PowerShell。使用独立环境；不要把正式本地版的环境当成检查环境。

```powershell
$pr00Root = Join-Path (Get-Location) 'work/pr00'
New-Item -ItemType Directory -Force -Path (Join-Path $pr00Root 'tmp'),(Join-Path $pr00Root 'cache/pip') | Out-Null
$env:TEMP = Join-Path $pr00Root 'tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $pr00Root 'cache/pip'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m venv work/pr00/venv
./work/pr00/venv/Scripts/python.exe -m pip install -r requirements-dev.txt -c tools/pr00/constraints-py310-windows.txt
./work/pr00/venv/Scripts/python.exe tools/pr00/run_checks.py
```

依赖安装阶段需要网络。上面的顺序先将 `TEMP`、`TMP`、`PIP_CACHE_DIR` 设为仓库 `work/pr00/` 下已创建的目录，再创建环境/安装依赖，避免把可配置安装缓存写入C盘。不要改写正式安装配置或读取已有 `data/`。已有测试环境可直接运行最后一条命令；runner 不安装依赖。

可通过 `--python <绝对路径>` 指定独立Python环境，runner会在该解释器重新运行，保证环境记录与实际测试一致。Node/PowerShell 不在 PATH 时，用 `--node <绝对路径>`、`--powershell <绝对路径>`。可选 `--output-dir work/pr00/<新目录>`；为避免覆盖证据，目录必须尚不存在且处于仓库 `work/pr00/` 下。

新增 `tools/pr00/constraints-py310-windows.txt` 来自本轮独立 Windows/Python **3.10.4** 环境成功安装原requirements后的 `pip freeze`，用于约束工程检查的解析结果，不替换或修改本地版requirements，也不含模型权重。CI的 `3.10` 会选择平台可用的最新3.10补丁版，尚未实跑前不能声称与本地3.10.4完全相同；每次报告记录实际补丁版本。更新约束须再次执行全回归并留存差异，不静默自动升级。

## 检查清单与失败口径

1. Python/Node 防护合成自检：确认允许 TCP loopback、拒绝外部网络/DNS和实际子进程；Python同时拒绝访问仓库正式数据、模型及私人配置。自检失败则拒绝启动回归，整体返回失败。
2. 原命令 `python -m pytest tests -q`，仅增加隔离 `--basetemp`、JUnit输出和pytest缓存目录；不筛选、删除或修改旧测试。Windows真实DPAPI仅使用原用例的合成密钥，继续执行。JUnit要求至少525个实际 `testcase` 节点和171个子测试，零失败、错误、跳过；不能把含子测试的696总数当成525个主用例的证据。新增合成门禁自检覆盖少主用例/少子测试/失败/错误/跳过/空报告。数量不能代替原测试哈希与逐项保留审查。
3. 原九套Node：`audio`、`ai-settings`、`input-limits`、`study-ui`、`study-cache`、`review-render`、`review-ui`、`review-mount`、`portable-reader`。
4. 44个既有Python文件及新增PR-00 Python工具AST；六个应用JS语法；`setup.ps1`/`start.ps1`/`stop.ps1`只解析、不执行；`git diff --check`。

每步保留命令、退出码、耗时与独立日志；任一失败都使runner退出非零，其他安全的检查继续收集证据。最终生成 `results.json`、`summary.md`、`pytest-junit.xml`。报告记录依赖版本与解释器，不输出整份环境变量或密钥。完整pytest控制台中的子测试结果与JUnit须一起保留，不能只比较总数。

## 隔离与实际验证边界

测试子进程设置HF离线、临时目录及缓存到本次 `work/pr00/runs/`；清除继承的 `PYTEST_ADDOPTS`/`PYTEST_PLUGINS`/`PYTHONPYCACHEPREFIX`，设 `PYTHONNOUSERSITE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，防止环境过滤测试或额外插件介入。子测试为固定pytest 9的内建能力，无需外部插件。Python通过仅runner注入的 `sitecustomize` audit hook限制网络到loopback，并阻止实际子进程；原Codex测试在调用边界使用mock，因此无需真实Codex或账号。Node以 `--require` 加载同目的防护；原HTTP/DOM mock不受影响。Python真实HTTP回归仍能绑定127.0.0.1，Windows DPAPI不替换为mock。

这些是防止测试误调用的进程级保护，并非操作系统沙箱，也不是对恶意测试代码的安全证明。新测试若需要额外外部集成，必须另设明确授权入口，不应关闭本入口的防护。未运行浏览器或麦克风、未下载模型权重、未请求真实模型、未读取真实课堂资料；Node DOM/VM测试不能称作真实浏览器或Web E2E验收。

## Windows CI

`.github/workflows/pr00-windows.yml` 仅响应 `pull_request` 和手动 `workflow_dispatch`，权限为 `contents: read`，checkout不保留凭据。Windows/Python3.10/Node24.19.0（与本轮本地Node一致），按原requirements安装；安装期允许依赖下载，测试期由runner限制网络。没有 `push` / `pull_request_target`、密钥输入、模型调用、部署或发布步骤。

CI只上传明确列出的合成测试日志、JUnit、摘要与依赖清单，保存7天；不上传tmp、媒体、浏览器存储、data或配置。新增工作流尚未在GitHub远端执行前，报告只能写“本地runner验证/CI定义”，不能写“GitHub CI已通过”。

所有可配置任务产物位于工作区 `work/pr00/`；GitHub托管runner自身的工具缓存/运行日志由平台管理，不能据此声称改变了平台全局存储位置。
