# PR-06c VMware 验证记录

测试使用具备 VMware Tools 的 Windows 11 来宾（10.0.26100）、Python 3.12.14、PostgreSQL 17.11、SeaweedFS 4.53 和 Node.js 24.19.0。既有 `J:` 来宾映射落在 `C:\j`。宿主负责 Git、静态校验、编排和取证。合成适配器本身没有真实 provider 传输；定向 pytest 验证启用了 loopback guard。真实 DashScope 和 Filetrans 调用均为 0。

| 验证 | 源码提交 | 实际结果 |
|---|---|---|
| 最终定向 | `ee6b15957383d282ca4bf91f1052a6723e16830e` | VMware broker 传输成功，来宾退出 0。16 项通过，0 失败、错误、跳过，49.87 秒。pytest 和 PostgreSQL 关闭退出 0。涵盖持久分片、字节范围、费用、过期、重复请求、CAS、未知重试和多分片删除竞态。 |
| 完整回归诊断轮 | `a13b3fa3220f59d4e75a7d9676630c5b10af3142` | 来宾退出 1。离线依赖、loopback guard、冻结契约、Windows 本地 venv 与依赖安装、源码清洁检查退出 0。Web 后端完整门禁与 Windows 本地完整门禁各退出 1；明细待回收。 |
| 最终完整回归 | `ee6b15957383d282ca4bf91f1052a6723e16830e` | 完整运行未开始。暂停队列项时，broker 已取得该项的列表快照，读取缺失文件后退出并释放凭据。目标 VMware VM 保持运行。 |
| 宿主静态 | `ee6b15957383d282ca4bf91f1052a6723e16830e` | 28 个 Python 文件 AST、PowerShell 解析、差异空白检查和受保护路径差异检查通过。OpenAPI、事件 schema、前端和路线图与基点无差异。 |

定向使用 `tools/pr06c/guest.ps1` 的 `targeted` 入口，依次验证 Git bundle、离线安装依赖、启用 loopback guard，以 `tools/pr06c/run_targeted.py` 运行 `tests/integration/test_filetrans.py` 与 `tests/integration/test_trusted_device_guard.py`。完整模式调用 `tools/pr07b/run_combined.py --auth-browser --pytest-loopback-guard …` 与 `tools/pr00/run_checks.py --node … --powershell powershell.exe`，覆盖保留的 Windows 本地 Python、DPAPI 和九套 Node 门禁。

来宾报告的最终定向证据 SHA-256 为 `64FAF8A00AA6F424FC65CD6173F4F130366858491DC85CC4EDF3E1E5777F38A2`。完整诊断轮证据为 `4E76F8E8A0E30AC2F1AA491B72C2897891ABBB968963F019CE2F3DF1FBCB685B`。两份证据均已在来宾产生；宿主取回及重算哈希待 broker 恢复。更早的定向失败包括合成 fixture 的 SQL JSON 绑定、QA 预算配置和历史一计划一任务约束。最终定向已验证对应修正。

Astra R2 静态复审为 P0/P1 均 0。生产接入关闭，真实独立 POC 待林珈诚授权，长课吞吐未量测。当前完整验证门禁尚未达到。
