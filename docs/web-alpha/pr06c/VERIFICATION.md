# PR-06c VMware 验证记录

基点为 PR-06b（GitHub #27）的 `fe4aafc1ee0a47e627b80fdaab1e2bdb688d5664`。测试均在 VMware Windows 11 来宾（10.0.26100）执行。来宾依赖为 Python 3.12.14、PostgreSQL 17.11、SeaweedFS 4.53 和 Node.js 24.19.0。来宾短路径位于既有 `C:\j` 测试目录。宿主负责 Git、静态校验、编排和取证。合成适配器无真实传输，真实 DashScope 和 Filetrans 调用均为 0。

| 验证 | 源码提交 | 实际结果 |
|---|---|---|
| R2 定向 | `ee6b15957383d282ca4bf91f1052a6723e16830e` | 来宾退出 0。16 项通过，0 失败、错误、跳过，49.87 秒。固定版本读取、字节范围、费用、过期、重复请求、CAS、未知重试和多分片删除竞态均在套内。 |
| 完整诊断 | `a13b3fa3220f59d4e75a7d9676630c5b10af3142` | 契约退出 0，Web 退出 1，1696 主项通过、83 子测试通过、4 失败、4 环境错误。失败涉及迁移库存、历史 QA 架构和既有任务材料回归。四个 PR-08c 实盘池用例要求 13 GB 来宾可用空间，实际容量不足。Windows 原跑法退出 1，519 主项与 171 子测试通过，6 个课堂包暂存长路径用例失败，九套 Node 已通过。 |
| 最终兼容性定向 | `84f564dda9bfd415bd7900a15873cb642b572cb5` | 来宾、pytest 和 owned PostgreSQL 停止均退出 0。20 项通过，0 失败、错误、跳过，56.02 秒。包含上述四个 Web 失败用例的精确补验。 |
| Windows 本地 | `314ae5c4ce73985d158ee99eb0e498afa5cbe931` | 使用短路径重跑，来宾退出 0。525 个主测试和 171 个子测试通过，0 失败、错误、跳过。包含实际 Windows DPAPI 合成往返及损坏测试，九套原 Node 全部通过。后续提交只涉及 Web 后端元数据及迁移回归断言。 |
| 最终完整 Web | `b8d33a9a751da263443b0827f6b53f563301bcab` | 来宾退出 1。1700 个主测试和 83 个子测试通过，四个 PR-08c 实盘用例失败，0 错误、跳过。Windows 本地 525 主测试、171 子测试、DPAPI 及九套 Node 全部通过。 |

入口为 `tools/pr06c/guest.ps1`。定向运行 `tools/pr06c/run_targeted.py`，先验证源码 bundle 和 loopback guard，启动独立的 PostgreSQL 和私有 SeaweedFS 并保留 JUnit。完整模式调用 `tools/pr07b/run_combined.py --auth-browser --pytest-loopback-guard …` 和 `tools/pr00/run_checks.py --node … --powershell powershell.exe`。Windows 单独补验使用 `legacy` 模式。宿主静态检查包含 Python AST、PowerShell 解析、`git diff --check` 和保护路径对比，冻结 OpenAPI、事件 schema、前端及路线图与基点无差异。

| VMware 证据 ZIP | SHA-256 |
|---|---|
| R2 定向 | `64FAF8A00AA6F424FC65CD6173F4F130366858491DC85CC4EDF3E1E5777F38A2` |
| 完整诊断 | `4E76F8E8A0E30AC2F1AA491B72C2897891ABBB968963F019CE2F3DF1FBCB685B` |
| Windows 本地 | `77F335A66A35932ECA378E2EFBF290393EE9C0A05550FF14DBC437370C5C562B` |
| 最终兼容性定向 | `1D2B031A1505832B5258D437CDFF246F46188FAEAA3A3E27979321D301E6F8B2` |
| 2026-11-22 完整复验 | `4A689682ABAED28DEEDED0941023FF09EB5FB64680B349B039E78982E49359EE` |

四份原始 ZIP 已回收并在宿主复算 SHA-256。合并证据包 `PR-06c-VMware-evidence.zip` 的 SHA-256 为 `BC532BF705A52A88BBB95EFF0E04268E2341AB17D3837F3076B9F784B74A7314`。

诊断和修复过程保留了合成测试 fixture、同计划重试唯一约束及迁移库存对齐的失败。首次安排最终完整复验时，助手移走已被 broker 枚举的待执行项，导致 broker 因文件缺失退出。林珈诚使用本机提示恢复 broker，来宾 VM 维持运行，诊断和后续验证顺利回收。磁盘阻塞在 2026-11-22 的完整复验前已解除，复验结果见下节。

## 2026-11-22 完整复验

运行 `b10219ee0d424f43` 从干净的 `b8d33a9a751da263443b0827f6b53f563301bcab` 创建。提交前确认已有 VMware broker 在运行、队列为空，VMware Tools 为 running。来宾在 02:45 PST 实测 C: 可用 26,497,101,824 字节，符合四个实盘容量用例的 13,000,000,000 字节前置条件。源码 bundle SHA-256 为 `6B33195E1F850FDA0BF88C9CE26FD5A07C62895ED4E0C2B187C5C8D8A7446213`。

命令经 `tools/pr06c/guest.ps1` 的 `full` 模式执行。传输成功，来宾总退出 1。Web pytest 退出 1，1700 个主测试及 83 个子测试通过，4 个主测试失败，0 错误、跳过；耗时 1676.29 秒。冻结契约、前端构建、真实 Chromium HTTPS/WSS 鉴权、pytest loopback guard、PostgreSQL 关闭、SeaweedFS 退出、源码完整性门禁均通过。Windows 本地门禁退出 0，525 个主测试和 171 个子测试通过，实际 Windows DPAPI 合成用例及九套 Node 均通过。

四个失败全部来自 `test_pr08c_pool.py`，本轮四项均实际启动。

| 用例 | 本次实际结果 |
|---|---|
| 实盘导入 | 达到 5,000,000,000 字节。关闭一个传输后，用例立即要求维护释放到 4,500,000,000 字节，实际仍为 5,000,000,000。基线 0016 的暂存清理 SQL 规定至少 120 秒，测试的即时断言与该窗口冲突。 |
| 实盘导出 | 500,000,000 字节对象写入返回 `STORAGE_UNAVAILABLE`。日志只保留了服务层错误，底层存储异常未定位。 |
| 导入及导出并发准入 | 两项均在共享模块部署中得到 0 次准入，预期各为 10。前两个用例的失败发生在池清除之前；本轮未独立隔离后两项的根因。 |

[PR-08c 容量用例](../../../web/backend/tests/integration/test_pr08c_pool.py) · [0016 暂存清理条件](../../../web/backend/migrations/versions/0016_portable_jobs.sql)

此前工作区的诊断轮失败保留在上表。四个 PR-08c 容量用例及所涉清理与对象存储代码在 `fe4aafc..b8d33a9` 中无直接差异。完整 Web 门禁仍为失败，Draft PR 待完整门禁通过。真实 DashScope / Filetrans 调用均为 0，生产接入关闭，独立真实 Filetrans POC 待林珈诚专项授权。

## 来宾归档与交接

收回证据 ZIP 并在宿主复算哈希后，将本次运行目录及测试、磁盘预检两份传输目录归档在 `J:\class-assistant\vm-archive\2026-11-22\pr06c-final\b10219ee0d424f43`。ZIP 和清单传输哈希一致，宿主逐项复算 19,019 个文件的 SHA-256，原始体积 2,222,946,964 字节。归档 ZIP SHA-256 为 `ADAC4AA9D35F339B74C19EEC979268557AC8A3A510CEEB09C7DADED167FA1ECB`，清单 SHA-256 为 `D7BCC35198200F397A31D2B610C770A04DD7EEBEF270DE6C0ECA194802707168`。

来宾删除前再次核验清单和文件哈希，解除一处共享依赖 junction，确认目标仍在。删除回执退出 0，C: 可用空间回到 26,494,906,368 字节。03:21 PST 把 broker 交还给 PR-10a 时，broker 正在运行且队列为空。
