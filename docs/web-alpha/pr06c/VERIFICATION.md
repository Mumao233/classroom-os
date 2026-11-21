# PR-06c VMware 验证记录

基点为 PR-06b（GitHub #27）的 `fe4aafc1ee0a47e627b80fdaab1e2bdb688d5664`。测试均在 VMware Windows 11 来宾（10.0.26100）执行。来宾依赖为 Python 3.12.14、PostgreSQL 17.11、SeaweedFS 4.53 和 Node.js 24.19.0。来宾短路径位于既有 `C:\j` 测试目录。宿主负责 Git、静态校验、编排和取证。合成适配器无真实传输，真实 DashScope 和 Filetrans 调用均为 0。

| 验证 | 源码提交 | 实际结果 |
|---|---|---|
| R2 定向 | `ee6b15957383d282ca4bf91f1052a6723e16830e` | 来宾退出 0。16 项通过，0 失败、错误、跳过，49.87 秒。固定版本读取、字节范围、费用、过期、重复请求、CAS、未知重试和多分片删除竞态均在套内。 |
| 完整诊断 | `a13b3fa3220f59d4e75a7d9676630c5b10af3142` | 契约退出 0，Web 退出 1，1696 主项通过、83 子测试通过、4 失败、4 环境错误。失败涉及迁移库存、历史 QA 架构和既有任务材料回归。四个 PR-08c 实盘池用例要求 13 GB 来宾可用空间，实际容量不足。Windows 原跑法退出 1，519 主项与 171 子测试通过，6 个课堂包暂存长路径用例失败，九套 Node 已通过。 |
| 最终兼容性定向 | `84f564dda9bfd415bd7900a15873cb642b572cb5` | 来宾、pytest 和 owned PostgreSQL 停止均退出 0。20 项通过，0 失败、错误、跳过，56.02 秒。包含上述四个 Web 失败用例的精确补验。 |
| Windows 本地 | `314ae5c4ce73985d158ee99eb0e498afa5cbe931` | 使用短路径重跑，来宾退出 0。525 个主测试和 171 个子测试通过，0 失败、错误、跳过。包含实际 Windows DPAPI 合成往返及损坏测试，九套原 Node 全部通过。后续提交只涉及 Web 后端元数据及迁移回归断言。 |
| 最终完整 Web | `84f564dda9bfd415bd7900a15873cb642b572cb5` | 等待来宾可用空间达到门槛后重跑。完整门禁尚未达到。 |

入口为 `tools/pr06c/guest.ps1`。定向运行 `tools/pr06c/run_targeted.py`，先验证源码 bundle 和 loopback guard，启动独立的 PostgreSQL 和私有 SeaweedFS 并保留 JUnit。完整模式调用 `tools/pr07b/run_combined.py --auth-browser --pytest-loopback-guard …` 和 `tools/pr00/run_checks.py --node … --powershell powershell.exe`。Windows 单独补验使用 `legacy` 模式。宿主静态检查包含 Python AST、PowerShell 解析、`git diff --check` 和保护路径对比，冻结 OpenAPI、事件 schema、前端及路线图与基点无差异。

| VMware 证据 ZIP | SHA-256 |
|---|---|
| R2 定向 | `64FAF8A00AA6F424FC65CD6173F4F130366858491DC85CC4EDF3E1E5777F38A2` |
| 完整诊断 | `4E76F8E8A0E30AC2F1AA491B72C2897891ABBB968963F019CE2F3DF1FBCB685B` |
| Windows 本地 | `77F335A66A35932ECA378E2EFBF290393EE9C0A05550FF14DBC437370C5C562B` |
| 最终兼容性定向 | `1D2B031A1505832B5258D437CDFF246F46188FAEAA3A3E27979321D301E6F8B2` |

诊断和修复过程保留了合成测试 fixture、同计划重试唯一约束及迁移库存对齐的失败。首次安排最终完整复验时，助手移走已被 broker 枚举的待执行项，导致 broker 因文件缺失退出。林珈诚使用本机提示恢复 broker，来宾 VM 维持运行，诊断和后续验证顺利回收。最终完整复验仍取决于来宾磁盘。生产接入保持关闭，真实独立 Filetrans POC 待林珈诚授权。
