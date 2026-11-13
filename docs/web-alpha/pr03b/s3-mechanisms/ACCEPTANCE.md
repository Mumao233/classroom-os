> 2026-11-10当前记录：按用户最新要求停止新增负载，继续普通修复及短回归。普通验证通过，R02/R04仍OPEN（高档已有失败），R05仍INCONCLUSIVE。详见[R02/R04当前开发记录](R02-R04-DEVELOPMENT-STATUS.md)；下文历史验收不改写。

> 当前补充：用户要求取消本轮压力测试，改为最小诊断及普通功能短回归。实际结果PASS；R02/R04仍OPEN，R05仍INCONCLUSIVE，S3未整体通过。见[本轮短回归](AVAILABILITY-SHORT-CHECK.md)。下文是原阶段记录，保留其容量失败。

# PR-03b 剩余 S3：R01–R07 合并整改验收记录

本轮以候选34冻结材料为基线，执行唯一整改清单R01–R07。分支 `feature/web-pr03b-sessions`，HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`，父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`，没有新提交。当前工作区与执行派生payload的差异均单列，不能只凭HEAD验收。

本次**S3未整体通过**。[合并整改交审回执](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/DELIVERY.md)、[实际摘要及逐项依据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-EXPERIMENT-SUMMARY.json)、[独审原清单](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/REVIEW-INPUT.md)为本轮入口；这是作者侧交审，不能代替独立阶段批准。

| 项 | 作者侧本次结论 | 实际依据及限制 |
|---|---|---|
| R01 | 固定原型范围 CLOSED，待独审 | 先用候选34冻结模块触发禁用D0仍被接受；37完成20次双顺序控制，43另有20个真实降权子进程禁用拒绝，零签名/零私钥交付。当前性、独立验签和PG资格撤销一致；真实D0未建立。 |
| R02 | OPEN | 入口1秒准入/同入口5秒操作期限、锁/池/IPC/PG覆盖，计算实际结束才释放槽；45容量内部STOP。46仅补55P03 owned清理，终态PASS；不替代完整容量和worker生命周期验收。 |
| R03 | 固定原型范围 CLOSED，待独审 | 旧8次失败泄漏[8,8]反例已触发；40的640次失败+80次合法控制通过；43补160次实际HTTP101后的中断+20次合法控制。所有路径幂等归还，TimeoutError不能PASS。 |
| R04 | OPEN | 45同NAT 60次中29次完整认证成功、31次429；正常/无hash/过载/恢复详见原始统计，未通过项不删除。hash worker进程变化不冒称API进程变化；E-T部署平台仍待确定。 |
| R05 | OPEN / INCONCLUSIVE | 新真实样本0；旧采集器默认入口已封住。容量前置未整体PASS，完整独立trial采集器未实现/验证；实际3次失败预算与admitted k=4不符，未擅自改为更宽预算。 |
| R06 | 诊断与重复范围已补，旧内因未定因 | 39清理前同连接诊断；TLS1.2/1.3各500次对照；40/41/43重复及PG57014故障证据。HOME24/28/30内因继续UNDETERMINED，未把新成功回填旧STOP。 |
| R07 | 固定调度已补，待独审 | 38 issue过期/审计各20及普通MFA边界/短等待20；41秘密丢响应seam20、恢复码双顺序各20、WS矩阵180；43持久状态reserved/ready各20个新子进程。seam不是正式seed HTTP端点，进程新建不是VM重启。 |

本轮最后容量目标为 `C:\Users\Administrator\PR03bS3Mechanisms45`，run `1fc63026a4464932b19734c9abcf89eb`，实际终态 `STOP`。[最终只读guest审计](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-GUEST-AUDIT.json)确认原boot guard、原隔离/服务状态、PG与实验进程停止、LocalMachine\Root中本实验合成CA无残留、固定lease撤销和来源不变。VMware Run退出码只说明相应脚本执行结果；协调器终态另有保留HANDLE的NATIVE-EXIT，传输成功不替代验收通过。

容量门槛未通过项：`["60_full_NAT_logins_at_1_per_second", "HTTP_source_budget_exhaustion_90", "baseline_full_samples_and_zero_unexpected_denial"]`。所有HTTP原始行另重算“全部响应/成功/拒绝或故障”样本和分位；快速429不计为成功登录的尾延迟通过。1秒排队、500ms满队列拒绝、哈希成本64MiB/t3/p1、90/120创建预算及样本下限未降低。

[完整原生记录重新核验](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/ARCHIVE-VERIFICATION-final.json)按分片顺序逐条解析、核对计数/摘要、loopback及后端HANDLE退出；[当前源码与已执行函数关联](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/SOURCE-RELATION-final.json)列出21项相同AST及2项明确差异，不能扩称全程序等价。候选45包含完整源码并发读取与HTTPS接入修复及真实控制；46只补owned清理定向控制，之后未重采完整容量。未采集新的A04机制样本。候选44/45的STOP和原始行、超大stderr隔离元数据及工具失败均保留。

D01–D03仅保持设计层CLOSED；TBD-08、既有P3及条件风险门禁继续OPEN。一般数据库执行权CLOSED。未修改或应用0001–0006、正式业务迁移/认证产品或获批S1/S2，未做真实D0/身份登记、真实恢复、外部API、宿主配置或VM关闭/重启/恢复；未commit、Push、合并、部署。独审交接后停止。

---

## 修订前历史记录（保留原文；当前结论以上方本轮记录为准）

# PR-03b 剩余 S3：实际机制验收记录

本阶段完成固定一次性原型实验，**待一次独立阶段审查；不宣告 S3 整体通过**。D01–D03 保持设计层 CLOSED；TBD-08、既有 P3、条件风险及真实启用门禁不关闭。枚举结果 **INCONCLUSIVE**，部署容量未验收。没有新 Git 提交，以候选、源码摘要和实际 run 绑定交审。

| 对象 | 实际值 |
|---|---|
| HEAD / 分支 | `e4853223847b3c26bfc74fa8fb2271cdb10e18b9` / `feature/web-pr03b-sessions` |
| 候选 / ZIP SHA256 | 34 / `2aa7e9597a7ccb9b674686b19b2cd35a16e76b21bd3a8e39103758a462b8d20e` |
| BUNDLE SHA256 | `b96ee048b4630cff2a131f5ad4d573efb1b5156f0deb0abd82273636b0a0132f` |
| guest / run | `C:\Users\Administrator\PR03bS3Mechanisms32` / `030f3bbef5994a658e218ad175a90146` |
| 固定脚本结果 | 2618/2618 断言；25 阶段完成；原生协调器退出码 0 |
| 原生记录 | 76651 条完整 journal；2646 次后端 HANDLE 退出观察 |
| 当前目标/权限 | target_active=false；固定 lease 已撤销；通用数据库执行权 CLOSED |

宿主证据位于 [阶段输出](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/)，guest 的本 run PGDATA 位于 `C:\Users\Administrator\PR03bLabBasic\checkout\work\pr03b\runs\030f3bbef5994a658e218ad175a90146\pg\data`，停止后仅保留现场。证据 ZIP 导出白名单结果及 journal，不导出 PGDATA、TLS 私钥或浏览器 profile。完整统计见 [摘要](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/FINAL-EXPERIMENT-SUMMARY.json)。

## 实际覆盖与修复

真实 PostgreSQL 的原子 DDL、最小角色/ACL/FORCE RLS、nonce/cap/complete、独立 status 当前资格、审计失败与 COMMIT 不读回包、会话/TOTP/恢复码竞争、邀请/重置/邮箱 CAS、独立通知/FK 锁，以及受限进程秘密 pipe、合成 D0/成熟签名/AEAD、本地 HTTPS/WSS/真实 Chromium 均在固定脚本中执行。`/lab/*`、`lab3_*` 和可信合成验证器投影不是正式产品实现。

本轮修正 complete 在 factor/group 锁之前取时间的缺口，并为 write/status 补锁后资格与返回前 status 期限复核；真实 user 与 factor→group 跨两分钟等待、资格先到期而写证明仍有效的拒绝均分列保留。夹具修正使用单一 DB 时钟、新故障 case/op 和 actor 独立 token 摘要，不续旧证明、不修改唯一约束。WSS 拒绝必须取得协议终态，客户端 TimeoutError 不能 PASS。

HOME22 的 23514 夹具失败、HOME23 的过期证明/审计预期失败、HOME24 的首次 WSS 握手超时以及更早 STOP 全部保留。HOME25 严格传输定向复现 54/54 通过；本 run 又完成 20 次 fresh ticket 握手/认证。**HOME24 超时的具体原因仍未证实**，后续成功不能回填成已定位修复。过程依据见 [调试记录](EXECUTION-NOTES.md)。

HOME28 在重复 fresh-ticket 握手中再次超时；增加清理前线程/TCP 与服务端握手阶段诊断后，HOME29 定向 74/74、HOME31 含 100 次重复的 154/154 均通过，未复现。**两次历史 WSS 超时的具体原因仍未证实，诊断适配与后续成功不构成根因关闭**。HOME30 在最初只读集群探测 STOP，尚未进入 WSS；PG 当时已 ready，但原探测的内因被脱敏边界隐藏。阶段适配现只记录原探测异常类型、位置和受控分类，不输出连接参数、不绕过原探测；该次内因亦保持未证实。

HOME26 的 worker 全部退出后，过期但无人继续轮询的 queued 项仍挡住后续准入，`hash_no_slot_leak_after_workers` 失败并保持 STOP。候选 34 仅在原全局事务锁内先清理到期 queued 项；本 run 的旧定义反例复现、原状态切入新定义后恢复及 active 项不被队列 TTL 清理，均以固定 SQL 留证（old_control_reproduced=True，fresh_recovery_restored=True）。未重置配额、延长期限或释放仍在执行的槽；旧控制函数在实验后移除。

HOME27 的后端 HANDLE 已退出 0、TCP 已空，但同次 Job 列表仍列该 PID，单次观察 16ms 后即 STOP；该失败不回填成通过。最终适配在原两秒总预算内复查全部视图，仍须 HANDLE/exit0/Job 空/TCP 空同时满足。当前 2646 次关闭中 1 次使用多个观察，最长 0.031 秒；未额外续预算或删掉 Job/TCP 条件。

N01 固定脚本实际完成 120 次 producer/FK 等待、200 次事件路径交错，以及 20 次预登记旧坏锁序死锁检测控制；旧坏路径异常单列，不能称“所有路径零死锁”。未降低 user 锁强度、未接业务 counter/Outbox、未触发 Provider。

## 数量与未通过项

- 枚举保留独立校准/留出、k=1/2/4/8、完整公开序列与真实限额；实际 40 个目标臂序列、24 个必测比较分支。样本/分层/独立 trial 及分位等价不满足获批下限，A04 不得 PASS，也不将“正确报告不足”的断言算安全证明。
- NAT 完整登录控制实际 60 次。按首请求至最后一次完整登录完成的记录计算为 18.599 次/分钟（193.562 秒）；全部控制 219.203 秒还包含后续 32 次 preauth。原报告 18.819 使用“最后开始时间＋1秒”估算，原始回执保留，统计更正见 [口径记录](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/NAT-THROUGHPUT-CORRECTION.json)。不能由请求总数称作已验证 60/min；preauth HTTP 来源耗尽观察为 False，SQL 精确额度控制另列。
- 六候选按并发/正确错误 dummy 分层，单配置 N=16 不支持 p99 通过。真实两进程 15/600/120 秒实验的原生生命周期见摘要；PG 准入表最多 2 执行槽/8 排队及拒绝零昂贵执行是本原型已核对的不变量，不能替代完整入口排队、正常负载或部署容量。
- 满队列拒绝 500ms 比较实际为 **FAIL**，样本 p99=59906.0ms。恢复 hash p95=141.0ms；这些不是完整登录/混合负载通过。整 guest 采样最小可用内存比例为 0.621290，不代表连续硬配额证明。
- 当前环境是用户重启后 4 CPU/~8GiB 的 Windows guest 筛选，未建立 E-L/E-T 服务/客户端硬资源分组，未完成 30 用户/30 WS/10 business requests/s＋1 login/s 及无 hash 基线，未覆盖所有密码长度/角色/冷热/负载分层。容量、A03/A27/A30 与对应补偿保持 OPEN；不降安全成本换数字。
- 稳定性仍有保留项：HOME24/HOME28 握手超时及 HOME30 初始只读探测 STOP 的具体原因未证实，不把后续新目标成功或诊断覆盖当作已定位修复。
- 3 份明确通道高熵秘密标记审计命中 0；不宣称覆盖任意编码、任意管理员内存取证、所有系统录制/转储，亦不把 IPC/TLS 标记检查扩称全部后续 worker 通道已动态审计。

逐项实际断言及剩余范围见 [A01–A46](COVERAGE.md) 和 [十九条状态推演](STATE-TRACE.md)。同一断言可支持多个条目，不可重复相加；固定脚本 PASS 与这些完整条目是否通过分开判断。

## 本次命令及核验边界

拒绝耗时包含本实验的 IPC/native guard 路径；`perf_worker.rpc` 在发送/接收期间持进程内锁，调用可能先等这把锁再进入 PG 准入。PG 表的 2 槽/8 排队计数不覆盖这段等待，不能据此宣称全入口队列≤8或总等待≤1秒。本轮没有分摊各段耗时，不能把 59.9 秒全部归因于某一个环节。最小后续验证须统一入口有界准入/调用 deadline，分离负载生成与 SUT 资源，保留原 guard 和哈希成本，再做完整正常/过载回归。

宿主统一 cwd 为 `J:\class-assistant\worktrees\web-pr03b-sessions`，Python 为既有 PR03a 隔离解释器。实际由 `vm_job.py Run --channel admin --script guest-execute-34.ps1` 调用 `Invoke-VMware Run`，其 GuestExitCode=0 是启动成功；实验结果由保留 HANDLE 的 NATIVE-EXIT=0 和 results.json 确认。完整请求/脚本 SHA、stdout/stderr 路径与退出码见 [命令账目](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/COMMAND-LEDGER.json)。

实际校验包括 `selftest.py`（17 组合成检查）、`static_check.py`（Python/SQL/PLpgSQL/固定计划/差异）、PowerShell AST、`collect_stage.py 32`、`verify_run.py 32`（导出原件/hash/全部 journal 逐条核对）、`protected_check.py` 及最终只读 guest 审计。静态/归档检查不替代机制执行，VMware 传输成功不替代实验退出。

没有运行原后端 464、Windows/DPAPI、九 Node、正式前端/远端 CI 或业务升级回归，不引用历史成绩作本次通过。未改/应用 0001–0006、正式认证/迁移/产品代码或获批 S1/S2，未登记真实 D0/身份、执行真实恢复、恢复网卡、修复宿主旧异常、关闭/重启/恢复 VM、commit、Push、合并或部署。一次交独立阶段审查；不自动进入产品实现、真实启用或下一工作包。
