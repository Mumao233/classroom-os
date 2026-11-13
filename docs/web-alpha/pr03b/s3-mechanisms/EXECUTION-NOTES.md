> 2026-11-10当前记录：按用户最新要求停止新增负载，继续普通修复及短回归。普通验证通过，R02/R04仍OPEN（高档已有失败），R05仍INCONCLUSIVE。详见[R02/R04当前开发记录](R02-R04-DEVELOPMENT-STATUS.md)；下文历史验收不改写。

# PR-03b R01–R07：复现、根因与修复边界

本轮以候选34冻结材料为基线，执行唯一整改清单R01–R07。分支 `feature/web-pr03b-sessions`，HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`，父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`，没有新提交。当前工作区与执行派生payload的差异均单列，不能只凭HEAD验收。

本次**S3未整体通过**。[合并整改交审回执](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/DELIVERY.md)、[实际摘要及逐项依据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-EXPERIMENT-SUMMARY.json)、[独审原清单](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/REVIEW-INPUT.md)为本轮入口；这是作者侧交审，不能代替独立阶段批准。

1. **先反例后修复。** 用HOME32候选34冻结模块、合成session stub和真实TLS依赖握手，实际触发disabled D0仍被接受、8次缺子协议后计数[8,8]且合法握手受阻；零SQL。第一次harness因预期stderr被PowerShell中止保留，第二次才形成有效反例。
2. **R01/R03最小修复。** D0当前状态、独立child与PG资格同时拒绝撤销；维护与恢复按既有独立gate/PG锁序串行。WSS在handshake/close_socket/handler三条路径归还同一reservation，锁内幂等，关闭与预留竞态拒绝。37/40/43的实际控制分列，整体STOP不抹掉已完成子例，也不伪称整个run PASS。
3. **R02入口预算。** 绝对准入deadline从入口建立，传至锁、池、IPC/PG，普通操作仍同入口5秒。超时调用不提前释放实际执行槽；已提交但尚未开始昂贵操作的未知答复只可按owned jid解析/回收，不能用于仍在算的hash。原guard与全部源码/角色/Job核验保留；原生完整读观察并行，后端生命周期仍排他。
4. **历史WSS与TLS。** 39在清理前取得同连接TCP/TLS已完成、未进入upgrade/PG、线程停在SSL读取/关闭的事实。TLS对照各500次：1.2全部HTTP400；1.3为486次HTTP400、13超时、1连接关闭。固定实验采用正常验签的TLS1.2 min/max，5秒不变；不能据此认定底层SSL内因或解释HOME24/28/30。
5. **夹具与工具故障。** 36的线程启动前提不足；38会话fixture ID冲突23505；40固定IPC标签不匹配；42的guest扁平目录使journal合成测试临时路径无效。均保留原STOP/错误，分别按唯一ID、既有标签、owned临时目录等修正，不删除约束或放宽guard。
6. **43的HTTP采集器。** 嵌套`http()`遮蔽import的`http`模块，HTTPSConnection前即AttributeError。已对冻结43 AST离线触发，零网络/SQL。改名`call_http`，加GET/POST及TimeoutError必关闭回归；44使用修复版。43的60 NAT/121 preauth无有效响应，不能算服务容量失败或成功。
7. **44/45实际容量。** 44已取得真实HTTP结果，随后过载worker失败并STOP；982条固定断言通过不能替代容量验收。其baseline为4次200、10805次TimeoutError、187次401及4次429；continuous为0次200、19272次TimeoutError、302次401及6次429；实际worker内因证据不足，继续未定因。最后候选45的NAT60取得29完整认证、31次429，最后完成62.0秒；preauth实际分布、成功/拒绝/故障尾部见摘要。预登记时长与样本安排未改，不得把拒绝延迟混入成功登录后宣称达标。
8. **完整源码读取。** 旧共享读取锁的受控fake-handle反例只证明串行控制流，不作磁盘性能证据；45真实Windows执行20轮×16并发完整重读/哈希，共320次，实际峰值16、最大0.110秒，归还全部reader。错误预期hash被拒、字节未变，WRITE/DELETE仍由原共享约束拒绝。16组独立CreateFile游标和scratch复用不缓存源码字节；每个边界仍核对全部身份、路径及hash。
9. **HTTPS接入根因与修复。** 旧HTTPS监听在主accept线程完成TLS握手，空闲原生peer使后续合法请求20/20达到5秒Timeout；关闭空闲peer后200。修复为最多64个worker内握手，正常验签、TLS1.2、5秒不变；同20组控制在idle仍打开时全部200，结束后worker排空、真实降权child退出0。4份探针harness STOP均保留。该控制不解释44所有超时或HOME24/28/30内因。
10. **超大日志保留。** 44 stderr原件101169845字节留在guest，全文件SHA为cc928cb79b3be3cf9c5be4316d527a6a5b5902661b372bb7748fc249f1c755bd。仅导出完整hash、异常与栈计数；前101169245字节已审计0标记，尾600字节未覆盖，故未导出raw。仅此精确hash/size固定的已关闭历史stderr可按128MiB完整读取；原regular/single-link/no-reparse及全hash检查不变。新HTTP日志保留所有类型计数和最后64份无秘密栈元数据，不记录请求/异常消息。
11. **R05仍未完成。** 旧单preauth采集器默认入口已安全拒绝。新计划分析器只支持预登记、单位隔离、同时CI及不足判定，未实现或验证完整真实collector。108个交叉cell按每split/arm1000组至少432000个arm序列、3456000次预排请求；这只是算术下限，不保证同时CI上界≤.55。实际prototype账号预算3阻断admitted k4，未擅自重置或放宽。容量前置未整体通过，不运行旧短采样器充数。
12. **只读进度诊断的口径。** timing-45首次按文件名字典序选择末两片，错误包含初始native-events.jsonl；原输出保留但不作“当前尾部”证据。timing-45-02改为按分片编号选取，仍只给采样观察，不能据此归因全部时延。最终归档统计按完整journal索引重算，不依赖这两份局部进度值。
13. **45清理失败及46定向修复。** 45的worker8000在解析未开始任务的未知准入结果时，poll遭遇PG `55P03`，父端服务循环退出，child直到owned Job清理才退出；worker68正常退出0。此诊断不能解释44的旧内因。仅在原单次5秒清理deadline内重试已回滚的55P03，重新poll同一owned jid，不调用enter、不执行hash；其他SQLSTATE/guard错误及实际过期仍拒绝，不延长原认证授权。46终态PASS，实际形成旧控制20、新控制20；原过期上下文/零hash及空表收尾按原始回执判断。41项当前宿主自检通过。此修复后未执行完整容量，R02/R04继续OPEN。

[完整实际回执及命令](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/DELIVERY.md)、[受保护来源与index](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/PROTECTED-FINAL.json)、[源码/执行差异与两层补丁](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-SOURCE-AND-PATCH-CHECK.json)供一次阶段独审。source关系的两项静态差异不隐去：D0额外deployment相等拒绝、撤销循环后的合成资格刷新；不宣称全程序等价。

D01–D03仅保持设计层CLOSED；TBD-08、既有P3及条件风险门禁继续OPEN。一般数据库执行权CLOSED。未修改或应用0001–0006、正式业务迁移/认证产品或获批S1/S2，未做真实D0/身份登记、真实恢复、外部API、宿主配置或VM关闭/重启/恢复；未commit、Push、合并、部署。独审交接后停止。

---

## 修订前历史记录（保留原文；当前结论以上方本轮记录为准）

# 剩余 S3 的调试依据与验证边界

本页记录同一获授权阶段内的最小原型修复。精确 Git HEAD 仍为 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`；实际执行对象由各候选 BUNDLE、源码 SHA256、一次性 run-id 和原生退出回执固定。历史 STOP 原样保留，不用后续成功覆盖。最终成绩以本阶段验收记录及导出证据为准。

## 已定位的适配问题

| 问题 | 实际依据与最小处理 | 保留限制 |
|---|---|---|
| 依赖清单自成员、合成自检与子进程导入 | 早期候选分别在清单精确集合、误拒 PL/pgSQL 的 `IF NOT EXISTS` 条件、`-S` 子进程缺少非必要 SQLAlchemy 导入处停止；修正集合/DDL 识别，SID 查询直接使用 Win32 TokenUser | 原失败目录及导出包不改；没有靠跳过自检放行 |
| Windows 文件拒绝的错误层次 | CRT 将原生 ACCESS_DENIED 映射为 EACCES，未保留 winerror；改用 CreateFileW 要求真实错误 5，并保留依赖摘要和只读 ACL | 不把任意打开失败当作权限拒绝 |
| PG 后端退出判据 | 原生控制及真实后端记录显示已退出的进程仍可报告 Job 成员关系；阶段适配器保留连接建立时的 HANDLE，并在原两秒内要求退出码 0、活跃 Job 集合无该进程、TCP 行清空 | 原 guard/launcher 不改；不是仅靠 PID 不存在或 IsProcessInJob 推断退出 |
| 原生边界观察开销 | 旧竞争失败的线程栈停在重复目录祖先扫描；新增不可继承源码句柄拒绝写入/替换，每个边界仍重读全部源码字节和身份 | 真实 SQL 在观察锁外并发；不扩普通 SQL 或授权期限 |
| 本地 HTTPS 及 Chromium | 非交互 CurrentUser Root 导入实际要求 UI；仅在专用 guest 临时安装本 run 合成公有 CA、精确回读、finally 删除。CDP 的 Network 操作须送至 page session，而非 browser target；独立普通 Domain cookie 正控制后再验证 __Host 注入拒绝 | 无证书错误或 sandbox 绕过；没有修改宿主信任库；旧 cookie 失败不能冒称已证明前缀行为 |
| guest 重启后的隔离绑定 | 用户调整内存后 boot/ifIndex 改变，旧绑定正确拒绝。另获当前 boot 更新授权后正常停止既有两项 RAS 服务，八个 WAN miniport 消失；实际零 Disable-NetAdapter 调用。新记录固定实际剩余五项、机器及 boot，原策略 code object 和拒绝条件保持 | 原 PLAN、guard 和旧拒绝保留；新 boot 再次失配即关闭。原生客户端拒绝与 loopback 正控制另有本 boot 实测证据 |
| HOME22 的旧证明时间夹具 | `FAILED-proof_scope_complete_races.json` 记录 SQLSTATE 23514，定位到分别两次 clock_timestamp 构造 verified_at/deadline；微秒漂移越过原 CHECK | 改为同一个 MATERIALIZED 时钟样本，新增 120 秒精确边界和超出 1 微秒拒绝的真实 SQL 回归；原 CHECK 不改，HOME22 保持 STOP |
| HOME23 的审计故障夹具 | 40 组 status/撤权调度之后，旧证明已超过 120 秒；实际观察 42501，而非预期审计故障 23514 | 独立审计故障场景新建 case/op 和 fresh 证明，不续期或重签旧 op；HOME23 的失败断言和原生 exit 1 保留 |
| 所有相关锁后的时钟检查 | 静态复核发现 complete 先取时间再等 factor/group 锁，以及资格有效期在后续锁等待后未重查；这不能由之前等待 user 锁的成功用例排除 | 原型改为先取得 factor/group 锁，再取 DB 时钟重查证明/当前资格；status 锁后及返回前复核资格/查询期限。另加真实 factor→group 跨 120 秒及资格过期等待控制；不修改设计层 CLOSED 结论，不宣称旧产品已出现实测漏洞 |
| WSS 拒绝证据与会话夹具 | 旧 WSS 反例捕获所有异常，会把客户端超时也计作拒绝；静态发现两个角色共用固定 token_hash 会与真实唯一约束冲突 | 反例必须取得 1008/1009 关闭帧或 403 握手状态，TimeoutError 传播失败；新增纯自检。会话夹具按 actor 使用独立摘要，不改唯一约束或 token 轮换要求 |
| HOME24 的 WSS 首次握手超时 | 正常握手触及原五秒预算并 STOP；同轮原生观察中位约 94ms、最大约 141ms，不能据此推出具体超时原因 | 加入无秘密的阶段/线程栈/TCP 诊断。HOME25 新目标的 54/54 严格传输断言通过，实际取得 1008/1009、403 及约 5.437 秒首帧关闭；该次未复现不证明根因已修复。后续整阶段另加 20 次 fresh ticket 握手/认证控制，不扩大五秒预算；旧 STOP 保留，根因仍未证实 |
| 宿主导出证据校验器 | ALL-TCP 表实际包含 LISTEN 及已接受的 loopback 连接；误要求 ALL 表总共只有一行，导致宿主静态检查失败 | 按原 guard 精确重放 state=2 子集，继续拒绝 state=0、错 PID/端口/地址等；14 项定向正反例通过，3915 个真实边界重放通过。旧错误报告保留，实验 STOP 未改为 PASS |
| HOME26 的哈希队列残留 | 两个真实 worker 完成 600 秒过载和 120 秒恢复后，`hash_no_slot_leak_after_workers` 仍失败；旧准入只清理当前被轮询的过期项，无人继续轮询的排队项会挡住后续准入 | 候选 29 在原全局事务锁内、读取队列计数前，仅删除已到期的 queued 项。固定反例先用旧函数复现 active=0、过期队列仍阻塞，再不重置配额或延长期限切入新函数验证恢复；执行中的 active 项跨队列 TTL 仍保留。原 HOME26 STOP、六个 worker 的真实退出记录及旧定义摘要均保留；完整回归成绩以最终 run 为准 |
| HOME27 的退出观察不同步 | 通知 producer/FK 阶段 1684 个断言达预期，但 PID 1316 的保留 HANDLE 已 signalled/exit=0、TCP=0 时，Job 列表仍含该 PID；单次观察只耗 16ms，原型立即 STOP。该记录证明不同视图在该次读取中尚未一致，不能把列表行直接忽略 | 候选 30 在原两秒**总**预算内复查 HANDLE/退出码、活跃 Job 列表和 TCP，全部满足才成功；不另加两秒、不去掉任一条件。增加已退出但列表/TCP延后清空、长期残留、仍运行、非零退出、API失败及观察本身耗尽预算的合成控制。旧自检曾固定断言“一次 Wait(2000)”，修改为单一递减剩余预算检查，原全部正反语义保留；初次自检失败另存。HOME27 原 STOP、原生退出 1 和未确认观察均保留，不转换为通过 |
| HOME28 重复 WSS 握手超时及后续定向诊断 | 首个及多次 fresh-ticket 控制成功后，重复握手再次 TimeoutError；旧快照在库清理后采集，接收线程已丢失。增加库清理前线程/TCP、服务端 TLS/握手/应用阶段诊断，不记录 locals/headers/body | HOME29 定向 74/74、HOME31 含 100 次重复的 154/154 通过但未复现；原五秒连接预算保留。扩大重复控制的首个包误保留 180 秒整体预算，但它实际在初始只读探测 STOP，未进入 WSS；此打包问题另记，后续定向 400 秒、完整计划 180 秒分别固定。未更新依赖，也未证明历史超时根因已修复 |
| HOME30 最初只读探测 STOP | 原始探测器在任何业务 DDL/断言前失败关闭；唯一 native 观察为原 LISTEN/Job/loopback 通过，PG 启动日志只有 ready，原接口已脱敏掉具体内因 | 保留 STOP，阶段适配仅在原探测文件中收集异常类型/位置、受控分类及安全拒绝原因，不输出连接参数或替换探测判断。HOME31 新目标只读探测通过，不回填旧失败的原因；具体内因继续未证实。首次日志采集的 PSObject 元数据及进程计数字段含义另作归一化说明，原回执不改 |

证据目录为 [阶段输出](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/)，包含候选包、VMware stdout/stderr/result、原生退出、完整 journal 及旧 STOP ZIP。boot 更新实际差异见 [说明](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/boot-binding02/README.md)。HOME22 的 [原始 STOP](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/STAGE22-STOP.zip) 与 [校验器定向检查](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/ENDPOINT-ARCHIVE-VERIFIER-REGRESSION.json) 分别保存。

## 结论不能超出的范围

本阶段使用 `/lab/*`、固定 `lab3_*` SQL 和可信合成验证器投影，不接入正式产品。合成 D0 文件/签名、高水位和 OS peer 只能验证该原型边界，不能证明真实外部部署批准渠道已经建立。管理员协调器不是实际获登记的项目用户。

秘密检查报告高熵合成标记的明确通道、文件与命中数；不导出 TLS 私钥、PGDATA、浏览器 profile，不宣称覆盖任意编码、管理员内存取证或所有系统日志。Windows guest 的资源筛选不替代 E-L/E-T 硬资源分组、正常混合负载或部署容量验收。枚举保留真实限额和完整响应序列，留出/分层/样本不足时必须 INCONCLUSIVE，不能以校验程序退出 0 宣称 A04 PASS。

正式迁移 0001–0006、业务认证代码、获批 S1/S2、BATCH 1–4 和历史证据保持原样。通用数据库执行权 CLOSED；D01–D03 仅设计层 CLOSED，TBD-08 和既有 P3 OPEN，条件风险接受尚未生效。本阶段不 commit、Push、合并、部署或执行真实恢复。
