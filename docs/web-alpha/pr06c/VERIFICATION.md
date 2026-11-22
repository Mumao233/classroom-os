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
| 实盘导入 | 达到 5,000,000,000 字节。关闭一个传输并调用 `tick()` 后，预期释放到 4,500,000,000 字节，实际仍为 5,000,000,000。完整运行未捕获该次维护的底层删除状态。 |
| 实盘导出 | 500,000,000 字节对象写入返回 `STORAGE_UNAVAILABLE`。从完整运行归档补查到对应对象的 SeaweedFS 进程报 `truncated request body` 和 `unexpected EOF`，两路并发上传均未读完。 |
| 导入及导出并发准入 | 两项均在共享模块部署中得到 0 次准入，预期各为 10。前两个用例都在清空各自池之前退出，后两个用例沿用同一模块数据库和对象服务。 |

[PR-08c 容量用例](../../../web/backend/tests/integration/test_pr08c_pool.py) · [0016 暂存清理条件](../../../web/backend/migrations/versions/0016_portable_jobs.sql)

容量测试用 `PortableLifecycle.stage()` 写入 `media_assets` 和 `portable_transfer_media`。关闭传输后，`classroom_web_portable_gate()` 将关联媒体置为到期，再由通用到期与删除 worker 释放。0016 的 120 秒最小等待仅用于独立的 `portable_import_assets` 暂存清理路径。此前把该窗口当作本用例失败原因的判断已撤回。

此前工作区的诊断轮失败保留在上表。四个 PR-08c 容量用例及所涉清理与对象存储代码在 `fe4aafc..b8d33a9` 中无直接差异。完整 Web 门禁仍为失败，Draft PR 待完整门禁通过。真实 DashScope / Filetrans 调用均为 0，生产接入关闭，独立真实 Filetrans POC 待林珈诚专项授权。

## 来宾归档与交接

收回证据 ZIP 并在宿主复算哈希后，将本次运行目录及测试、磁盘预检两份传输目录归档在 `J:\class-assistant\vm-archive\2026-11-22\pr06c-final\b10219ee0d424f43`。ZIP 和清单传输哈希一致，宿主逐项复算 19,019 个文件的 SHA-256，原始体积 2,222,946,964 字节。归档 ZIP SHA-256 为 `ADAC4AA9D35F339B74C19EEC979268557AC8A3A510CEEB09C7DADED167FA1ECB`，清单 SHA-256 为 `D7BCC35198200F397A31D2B610C770A04DD7EEBEF270DE6C0ECA194802707168`。

来宾删除前再次核验清单和文件哈希，解除一处共享依赖 junction，确认目标仍在。删除回执退出 0，C: 可用空间回到 26,494,906,368 字节。03:21 PST 把 broker 交还给 PR-10a 时，broker 正在运行且队列为空。

## 2026-11-22 PR-08c 容量失败对照

11 月 20 日的 `pr08c-closeout-full-final`（`17fb7fe`）和 `pr08c-closeout-head-full`（`f34653a`）各有 4 个容量用例通过。复核了两份历史 JUnit 和源文件归档。按 Git 的换行规则归一化后，两轮的 `test_pr08c_pool.py` 与三个对照提交相同，`f34653a` 的 0016 SQL、`portable_maintenance.py` 和 `portable_lifecycle.py` 也相同。历史绿跑能在保留同一清理窗口的情况下完成。

每轮从干净的固定提交启动独立 PostgreSQL 17.11、私有媒体及账本 SeaweedFS 4.53。VMware Windows 11 来宾使用 Python 3.12.14。本轮仅从离线 wheel 装载补充依赖，通过既有 broker 串行排队。每轮在 `web/backend` 执行 `pytest -p pool_control tests/integration/test_pr08c_pool.py -q -rA -p no:cacheprovider --basetemp=<运行目录> --junitxml=<证据目录>`。入口验证 pytest 的 loopback 防护。外置观测器记录 pytest 阶段和只读数据库快照，第二轮起额外捕获 S3 错误类型和状态码。每轮保留完整命令、源码校验、退出码、JUnit、媒体和账本日志。

| 提交与来宾运行 | C: 起跑可用字节 | pytest / 来宾退出 | 结果 |
|---|---:|---|---|
| `b8d33a9` · `4e1cc21015b24853` | 26,494,885,888 | 1 / 1 | 2 通过、2 失败，142.31 秒。实盘导入在写入 16 MB 对象时报 `STORAGE_UNAVAILABLE`，尚未关闭传输。10 个导入预留仍占 5 GB，后续导入并发准入为 0。两个导出用例通过。 |
| `fe4aafc` · `d19b97a65c194576` | 26,491,400,192 | 0 / 0 | 4 通过，144.83 秒。实盘导入的 320 个关联媒体均完成删除，导入池归零。 |
| `6cfac0e` · `5bcc1048e5bb4543` | 26,496,958,464 | 0 / 0 | 4 通过，138.41 秒。 |
| `b8d33a9` · `21447221ddbc4e94`（同版本补跑） | 26,492,723,200 | 0 / 0 | 4 通过，130.96 秒。使用与两个基点相同的观测器，320 个导入关联媒体均完成删除，暂存表无记录。 |

四轮均无错误或跳过，传输成功，独立 PostgreSQL 正常关闭。按约定，首轮单跑失败后，Filetrans、实时 ASR 前置的顺序对照没有触发。最后一轮仍只执行容量模块，补足候选版本的 S3 和数据库观测。

- **并发准入的失败机制已确认。** 各池的前一个实盘用例提前退出，释放逻辑没有跑完。模块复用同一个部署。首轮单跑用只读快照确认导入的 10 个传输仍为 `uploading`，总预留 5,000,000,000 字节。之后导入并发用例的准入数为 0，导出侧两个用例通过。
- **原完整跑的导出失败定位到对象存储传输中断。** SeaweedFS 日志 `weed.exe.Test.Test_Codex.log.ERROR.20261122-031051.9852` 在 03:10:51 PST 记录两路请求体被截断，返回 `unexpected EOF`。其中一条的对象 ID 与导出 JUnit 异常完全匹配。日志位于原完整运行 ZIP 的 `run/work/pr07b/combined/54d1aed1259c4afda2be1aa336029d72/`，已对照归档清单复算。客户端为什么断流仍缺直接证据。首轮单跑的 16 MB 导入写入也报存储不可用，但该请求的底层异常未保留。
- **原完整跑的导入 `tick()` 失败仍未定位到具体调用。** 原证据只有预留未释放的断言。三个通过的单跑中，同一条通用删除路径都清除了 320 个关联媒体，保留 0016 的 120 秒条件。两个基点和候选补跑均绿，首轮候选在写入处失败，现有结果未显示可稳定归因于 06c 的回归。完整 Web 门禁仍为失败，测试断言、0016 和业务实现未改，未开 Draft PR。

对照归档位于 `J:\class-assistant\vm-archive\2026-11-22\pr06c-final\<运行号>`。每轮先核验回收的证据 ZIP，再逐文件验证对应归档，核验通过后删除来宾运行、预检和测试传输目录。各轮保留归档清单和删除回执。

| 运行号 | 证据 ZIP SHA-256 | 来宾目录归档 ZIP SHA-256 | 已验证文件 |
|---|---|---|---:|
| `4e1cc21015b24853` | `97FA66E55A057438A625DEAA34D9C854BECFD6AC6D678F95D5C0FF4B0103DE9A` | `3E29A3B5C0FEA2D302099C81EC3B405B69545502E35D3AF8634C5190E1DE26D6` | 3,331 |
| `d19b97a65c194576` | `47E2634AAD1DCFCE4228A1CC6A55DCCA145EE905EDD09150973260B9D485E714` | `3BB49A7216728428EA0F79BF67B56B84CD2DA583AB004FC2E8EC417EF70869EA` | 3,315 |
| `5bcc1048e5bb4543` | `9DD46A795EC271680B791AB703767075F0BA8810BBDD449B8AAC660203B85A03` | `DA1A375CC21B8D5AE5616730BA938531EA8085D896E38B799BE1C25BB08DFD7D` | 3,306 |
| `21447221ddbc4e94` | `B564A2DA9EF9BC537FE2D72871DF4661E7985ACF6268A3D89F571FCD9B36D2EC` | `844474A56083922F276E4574DBE5F941C4298292626CEA5ACEC28999BADBBA3C` | 3,332 |

最后一轮删除后 C: 可用 26,489,393,152 字节。03:58 PST 核实 broker 正在运行且队列为空，本轮没有移动已入队的任务。真实 DashScope / Filetrans 调用均为 0。

## 2026-11-22 HTTP 0.3.0 合入与复验

在 06c 工作树执行 `git merge --no-ff --no-edit --no-autostash origin/main`。merge 提交为 `370bd0ebc7124c3fde1b84e37e9498b2ea7515a9`，双亲为 `5915593e06c09cd7fc050ef230d7117b26788287` 和 `a0f5b4becfdb8bc35c3d5d0bac71a6181a04e553`。Git 自动合并 HTTP 入口，未发生冲突。冻结契约、前端和清单读取沿用主线 0.3.0。

候选提交 `080c8ed5abbfcfd6635cbd7353b34fb1b4d66da5` 将原音到期、删除或读取权撤销分别投影为 HTTP 410 `AUDIO_EXPIRED`、`AUDIO_DELETED`。课次追加审批和月时长余额不足分别返回 HTTP 409 `QUOTA_SESSION_EXCEEDED`、`QUOTA_MONTH_EXCEEDED`。月额由实际超限的 `retranscribe_month_ms` 账本识别。四码共用默认文案，均为 `retryable=false`。PR-10b 按 `error.code` 分支。

| 本轮验证 | 源码与来宾运行号 | 实际结果 | 证据 ZIP SHA-256 |
|---|---|---|---|
| VMware 定向与冻结契约 | `080c8ed` · `5bef693205174156` | 传输成功，来宾、pytest、独立 PostgreSQL 停止均退出 0。20 个主测试通过（含 14 个 Filetrans 用例），0 子测试、失败、错误、跳过，59.24 秒。冻结契约 9/9 通过，144 份正反 fixtures 包含四份同文案正例和一份未知码反例。 | `A8FDA487CC5E1D0DF0638D7EB356A3DE40CB0BAD8E3D9F9DCBE6AD86C59FC982` |
| A/B 第 1 轮，主线单跑 pool | `a0f5b4b` · `44e84aa7df454344` | 启动后发生来宾 bugcheck 和重启，随后 broker 传输超时。没有 pytest / 来宾退出码、JUnit 或完整用例结果。证据包由重启后的取证任务生成，收录残留日志。 | `88FC31EACDE1A72781FC272133F713C64E273E6F6C904387F5F1C4294AB22F3D` |

定向命令为 `tools/pr06c/guest.ps1 -Mode targeted`，含 `tools/overnight/check_contracts.py` 和 `tools/pr06c/run_targeted.py`。真实 HTTP 合成请求经 PostgreSQL 与私有对象存储，测试校验四种回包的 Error schema、HTTP 状态、`no-store`、重试标志，以及拒绝前后的任务、预留与供应商尝试数。另覆盖已失效原音的计划预览、显式重试的课次审批、来源不全、课堂删除和平台预算拒绝。

宿主对 `a0f5b4b..080c8ed` 的 31 个 Python 文件执行 AST 解析，对 `tools/pr06c/guest.ps1` 执行 PowerShell 解析。`git diff --check`、受保护路径及本轮改动范围检查均退出 0。与主线比对，冻结契约、前端、路线图和既有测试工具未改。历史 06c 分支已有的 `docs/web-alpha/DECISIONS.md` 在本轮与 `5915593` 一致。

### A/B 中断与取证

原计划按主线、06c 交替三组。第一轮启动前实测来宾 C 盘可用 26,342,666,240 字节。启动了独立 PostgreSQL、媒体及账本 SeaweedFS，运行 `tests/integration/test_pr08c_pool.py`，开启 urllib3 连接池 DEBUG、botocore 请求结果及异常链记录、SeaweedFS `-v=2`。pytest 已打印 loopback guard 校验成功。源码和观测器随运行目录归档。

broker 于 15:13:16 UTC 开始执行，来宾在 15:15:20 UTC 重新启动。Windows 系统事件 1001（记录 2271，15:15:31 UTC）报告 bugcheck `0x00000124`，同时段有 Kernel-Power 41 与异常关机事件 6008。15:39:08 UTC，broker 在 1,550 秒超时后返回 `Result=null`。取证时没有进程引用原运行目录。部分 JSONL 记录已无法解析，回收了客户端异常链和 6 份 SeaweedFS 原生日志或进程输出。可读异常链含就绪探针的读超时。现有证据无法确定 bugcheck 的底层原因，也无法把它与旧轮的请求体截断归为同一故障。

| 版本 | 已启动 | 有完整结果 | pool 失败率 |
|---|---:|---:|---|
| 主线 `a0f5b4b` | 1/3 | 0/3 | 无有效分母 |
| 06c `080c8ed` | 0/3 | 0/3 | 无有效分母 |

按第 4 步停止条件结束本轮。新 HEAD 上的完整 Web 与 Windows 本地门禁未执行，主测试、子测试及失败数均无本轮结果。未触发 Astra 复审，P0/P1 未评定。未推送，未开 Draft PR。真实 DashScope / Filetrans 调用为 0，生产接入关闭。

两轮归档均位于 `J:\class-assistant\vm-archive\2026-11-22\pr06c-final\<运行号>`。每个目录内含归档 ZIP、逐文件清单与核验和删除回执。主线中断轮另存原始 broker 回执、传输错误和来宾事件 1001 的停止码。

| 运行号 | 归档 ZIP SHA-256 | 清单 SHA-256 | 逐文件核验数 |
|---|---|---|---:|
| `5bef693205174156` | `C7536A4CB8569297B646188C1D5833E79FA1E3FC44273D3C354C82791F35D985` | `34025485A98833E76A21662781956E9B431344C43962B7F56AFB8C8545C94C09` | 3,615 |
| `44e84aa7df454344` | `E615ED7C236BB3E8809A3BC0F899474A6FDB13DF5349AE01E65FC3E09CB66A2B` | `F074B5F39C59642CE367593F81D53330DAFF9F0B73629122D9F9079B5540D1A3` | 4,349 |

中断轮第一次打包因一份 PostgreSQL 文件的元数据在读取前后不一致而中止。复查同一文件，两次读取的长度、修改时间与 SHA-256 一致。随后打包时重新读取各文件的元数据，并对最终归档逐文件复算 SHA-256。两轮均在宿主核验和来宾再次核对后删除原运行目录及对应测试、预检传输目录，发现 junction 数均为 0。主线中断轮的未完成打包临时件也已清理。15:45 UTC 实测来宾 C 盘可用 26,551,017,472 字节。

## 2026-11-22 第三轮续跑

### VMware 容量 A/B

主线 `a0f5b4becfdb8bc35c3d5d0bac71a6181a04e553` 的整轮失败率为 **1/3**（8 个用例通过、4 个失败），06c `b2abce9d1d920c97ecc15b341610c3787360e683` 为 **0/3**（12 个通过）。六轮均有完整 JUnit，0 错误、0 跳过。上节的蓝屏轮没有完成 pytest，另行留存。两个版本使用同一观测器、相同顺序的四个容量用例，各轮从固定提交的干净副本启动独立 PostgreSQL 和 SeaweedFS。来宾 loopback 限制、源码完整性和各轮 PostgreSQL 关闭均通过。

| 轮次 | 提交 · 运行号 | 宿主起跑可用物理内存（字节） | 来宾起跑 C 盘可用（字节） | pool 通过 / 失败 | 新增 1001 / 41 / 6008 | 证据 ZIP SHA-256 |
|---|---|---:|---:|---:|---|---|
| 1 | `a0f5b4b` · `9eb9aa0075054645` | 17,814,634,496 | 26,542,305,280 | 4 / 0 | 0 / 0 / 0 | `553619F41A7DBD95204F73FF21914EA8EFCF18C724618784A603658F08D72148` |
| 2 | `b2abce9` · `a16f27e3e57b4124` | 11,000,741,888 | 26,527,993,856 | 4 / 0 | 0 / 0 / 0 | `281A4BA553409755497A782DB30F846567F0F4FF9D85EC3785DBA0F6A85474DA` |
| 3 | `a0f5b4b` · `ef3cf46f83224459` | 10,774,085,632 | 26,516,230,144 | 4 / 0 | 0 / 0 / 0 | `BB9EFCE8A81344235D6D380AD6F054185F87964037D18E394DFDAE66EF45D4AA` |
| 4 | `b2abce9` · `c8852796864e4967` | 10,352,906,240 | 26,504,933,376 | 4 / 0 | 0 / 0 / 0 | `5AA426574D8A3B430040F2E1AC56F5314C2D4462B2BA3B6DCBCAE1849579B826` |
| 5 | `a0f5b4b` · `5f3b1db0eee64529` | 8,516,440,064 | 26,488,696,832 | 0 / 4 | 0 / 0 / 0 | `0F8D5A746EBDBA84820544AF59F63EA95DC5C1B8AEB70FC16185AD4560999C10` |
| 6 | `b2abce9` · `494923b63056486a` | 9,400,287,232 | 26,489,856,000 | 4 / 0 | 0 / 0 / 0 | `C43E9F36210CA3E0E80B8689844527516F55281A8C92822E21ED4580A91A4397` |

每轮提交前读取宿主可用物理内存和来宾 C 盘可用字节，再按来宾 System 日志的事件记录号查询本轮新增 1001、41、6008。起止启动时间均为 15:15:20.500 UTC，与上节重启相符。宿主可见总物理内存 33,687,732,224 字节，来宾配置 8 GiB、4 vCPU。六轮均未触发蓝屏重试。

第 5 轮的客户端异常链和 SeaweedFS 日志保存在该轮证据 ZIP 的 `diagnostic/`。导入时 16,000,000 字节写入出现 5 秒读超时，导出时两个并发的 500,000,000 字节 PUT 底层均报 `TimeoutError`。按对象键与 `media-seaweed-1.log` 对照，两个导出请求在服务端读取至偏移 67,108,864 字节、再收到 524,288 字节后报 `unexpected EOF`。只读数据库快照显示导入和导出各有 10 个预留，各占 5,000,000,000 字节。随后两项准入测试共用这两个未释放的池，各得到 0 个新预留，期望各为 10。主线已复现这条失败路径，06c 三轮均通过，因此继续运行 06c 完整门禁。现有证据无法确定最初触发传输超时的系统因素。

六轮均经 VMware Tools 回收证据 ZIP 并在宿主重算 SHA-256。运行目录归档在 `J:\class-assistant\vm-archive\2026-11-22\pr06c-final\<运行号>`，逐轮核验文件数为 3,345、3,362、3,344、3,362、3,346、3,362。各目录保留 ZIP、清单、含归档哈希与删除结果的 `receipt.json`、来宾前后探针及宿主内存采样。逐文件核验后已删除本轮来宾原件，六个运行目录均无 junction。第 6 轮清理后 C 盘可用 26,479,255,552 字节。

### 同一提交的完整门禁

来宾运行 `bd75f58888c64395` 使用 `tools/pr06c/guest.ps1 -Mode full` 验证 `b2abce9`。源码 bundle SHA-256 为 `167614B7B99F58563FF8A5C532DD5F0EF5E2C40F0BCB8212C73A89F5E7872A64`。VMware 传输成功，来宾总退出码为 0。完整门禁证据 ZIP SHA-256 为 `E0DFDCAB1884CE80FF99F2FA3071526DC1BF5874A2A9406F5A63E790F007EF8E`，宿主复算一致。

| 门禁 | 主测试 | 子测试 | 失败 / 错误 / 跳过 | 核对结果 |
|---|---:|---:|---|---|
| 完整 Web | 1,715 | 83 | 0 / 0 / 0 | pytest 退出 0，1,546.66 秒。四个 pool 用例全部通过。冻结契约 9/9、前端构建、真实 Chromium 鉴权、loopback 防护和源码完整性均通过。PostgreSQL 和对象存储均已停止。pytest 报一条 Starlette TestClient 弃用警告。 |
| Windows 本地 | 525 | 171 | 0 / 0 / 0 | pytest 退出 0，56.87 秒。实际执行 Windows DPAPI 合成往返和损坏用例，九套原 Node 测试全部退出 0。 |

完整门禁开跑前宿主可用物理内存为 9,409,986,560 字节，来宾 C 盘可用 26,479,087,616 字节。运行前后启动时间相同，System 记录号从 2334 到 2336，新增 1001、41、6008 均为 0。所有 Filetrans 工作均使用合成适配器，真实 DashScope / Filetrans 调用为 0，生产入口关闭。

完整运行归档位于同一归档根目录的 `bd75f58888c64395`，ZIP SHA-256 为 `1E90D2993D82F93F6B6E16CE7FC6FC0A2F88459AC0D70EAE6B668B303A826597`，清单 SHA-256 为 `4E59AD3C53F0E4F8820D21754C970947C27AC92B0D8338383B3499125ACBDD2E`。宿主按清单逐一核验 19,074 个文件，来宾删除前复核并拆除一处前端依赖 junction，确认其目标仍在。18:17 UTC 清理回执显示来宾 C 盘可用 **26,468,794,368 字节**。

### Astra ultra 增量静态复审

完整门禁通过后，`gpt-6.2-astra`（ultra，只读）复审了 `370bd0e` 合并、`080c8ed` 四码接入及其在 `b2abce9` 上的直接影响，**P0 为 0，P1 为 0**。复审核对了冻结契约与正反 fixtures、先鉴权再读原音、课次和月额度、拒绝时事务回滚、未知费用保留及显式重试、合成适配器和 PR-10b 按码处理，并对照 A/B 回执及完整门禁的 JUnit 和 pytest 摘要。

复审保留一项非阻塞配置边界。若手动向 HTTP 工厂注入新版合成 Filetrans，同时指定旧 0.2.1 契约，旧契约的封闭枚举无法识别四个新码。已验证配置使用 0.3.0，回退时应将实现与其契约成对恢复。[HTTP 工厂](../../../web/backend/classroom_web/entrypoints/http.py) · [PR-10b 交接](PR10B-HANDOFF.md)
