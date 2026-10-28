# PR-00 完整基线测试与撤回演练

日期：2026-10-28（北京时间）。本记录是本轮实际执行结果，不沿用此前只读审计中的 57 项部分结果。

## 结论

在未包含任何 PR-00 新增文档、CI、tools 的纯 `43fa23c141e3350e90d72ed7768e3b8de7d68337` 快照中，原测试集合完整运行：

**525 passed、171 subtests passed，0 failed、0 error、0 skipped，pytest exit 0；pytest 报告耗时 70.59 秒，进程墙钟 72.00 秒。**

这证明撤回 PR-00 增量后，原 v1.3.0 基线仍可在本次隔离环境运行。正式开发目录加入 PR-00 后的 runner 验证由 CI 工具代理另行执行，两次执行目的不同；本记录不替代该最终交付检查。

## 仓库和快照

- 正式仓库：`J:\class-assistant\classroom-os`
- 当前开发分支：`feature/web-alpha-0.1.0`
- 固定基线：`43fa23c141e3350e90d72ed7768e3b8de7d68337`（v1.3.0）
- 快照创建：`git archive --format=zip --output=work/pr00/rollback-baseline.zip 43fa23c141e3350e90d72ed7768e3b8de7d68337`
- 解压到全新目录：`work/pr00/rollback-baseline`；未覆盖/删除正式仓库文件，未执行 reset/revert。
- ZIP SHA-256：`803E7B358B75785CA6793D7D1E364B6D968C12006EE2639D582E5F8561E0BBE5`
- 快照文件计数：149。
- `git diff --exit-code 43fa23c... -- '*.py' static tests requirements.txt requirements-dev.txt setup.ps1 start.ps1 stop.ps1` 在记录时 exit 0；本代理未修改任何已有源码、测试或依赖要求。

## 环境

独立 venv：`J:\class-assistant\classroom-os\work\pr00\venv`

| 组件 | 本次版本 |
|---|---|
| Python | 3.10.4 |
| pytest | 9.2.0 |
| FastAPI | 0.162.0 |
| Starlette | 1.7.0 |
| Pydantic | 2.14.1 |
| httpx | 0.28.1 |
| Uvicorn | 0.54.0 |
| Pillow | 12.3.0 |
| faster-whisper | 1.2.1 |
| CTranslate2 | 4.8.4 |
| NumPy | 2.2.6 |

按原 `requirements-dev.txt` 安装，未更改约束。宽松范围解析到的部分依赖高于历史 VALIDATION 环境；本轮结果证明此实际版本组合通过，不能声称严格复刻所有历史包版本。完整解析结果见 `pip-freeze.txt`，安装记录见 `logs/pip-install.log`。

venv、pip cache、TEMP/TMP、Python安装阶段字节码缓存、Hugging Face/Transformers/Torch等可配置缓存全部位于 `J:\class-assistant\classroom-os\work\pr00`；不运行 `setup.ps1`，不创建/读取应用真实配置，不触碰原正式安装。系统 Python 可执行文件在 C 盘，本次只把它作为已有运行时使用。

## 命令与安全边界

执行目录为纯基线快照 `work/pr00/rollback-baseline`。原测试命令只增加输出/缓存定位参数：

```powershell
J:\class-assistant\classroom-os\work\pr00\venv\Scripts\python.exe -m pytest tests -q --basetemp J:\class-assistant\classroom-os\work\pr00\temp\pytest-baseline -o cache_dir=J:\class-assistant\classroom-os\work\pr00\cache\pytest
```

可复现执行脚本：`work/pr00/run-independent-baseline.ps1`。

运行级配置：

- `PYTHONDONTWRITEBYTECODE=1`、`PYTHONNOUSERSITE=1`、`PYTHONUTF8=1`。
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，防止用户环境的无关插件进入执行；原 525 项/171 子测试完整运行，没有删改或新增 skip。
- `HF_HUB_OFFLINE=1`、`HF_DATASETS_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，不下载模型。
- `PYTHONPATH=work/pr00/runtime-guard` 自动加载本轮执行专用 `sitecustomize.py`；Python audit hook 限制 socket connect/bind/getaddrinfo 为 loopback。产品文件和测试没有修改。
- 安全自检允许真实 loopback bind；对外部连接、通配监听和外部 DNS 用 `sys.audit` 模拟事件验证拒绝，没有为了验证防护而实际联网或监听公网。详见 `logs/network-guard.log`。
- 依赖安装阶段访问 Python 包源下载开发依赖；测试阶段外部网络被 guard 拒绝。该 guard是本轮测试进程防护，不是生产网络隔离或整机防火墙。

执行前已核对：测试使用合成 WAV/图片、tmp_path/TemporaryDirectory、FakeASR/FakeBridge、httpx MockTransport；HTTP集成测试仅绑定 `127.0.0.1` 随机端口。Codex subprocess 测试使用 mock，未调用真实 Codex。DPAPI用合成密钥验证。

没有读取/上传真实课堂材料或真实 API Key，没有调用收费 API、启用真实麦克风、部署、开放公网、push、merge或发布。

## 结果文件

- `logs/pytest-baseline.log`：原始完整pytest输出。
- `pytest-result.json`：exit code、墙钟、Python路径、guard模式、完成时间。
- `logs/pip-install.log`：原依赖安装日志。
- `pip-freeze.txt`：完整安装版本。
- `versions.txt`：关键运行时与依赖版本。
- `logs/network-guard.log`：执行级loopback限制自检。
- `runtime-guard/sitecustomize.py`：仅用于本轮执行的guard源码。

以上全部留在被忽略的 `work/pr00` 中；应由最终PR文档记录结论和可复现方法，不提交venv、包缓存、合成测试目录或运行垃圾。

## 不由本轮证明的内容

这不是 Web Alpha 业务验收，也不是真实课堂/云模型质量测试。没有复测真实浏览器、file:// 双击、真实ASR、CUDA、麦克风/续航、长时课堂或2 GiB实体包。Windows本地版原回归通过不能代替未来多用户授权、数据库事务、云对象生命周期、费用预留、浏览器加密缓存等新增测试。
