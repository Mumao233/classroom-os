# PR-03b S3 BATCH 4：一次性 guest 权限实验

本批承接 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`、获批 S2、BATCH 1–3 的快照及专用 guest。授权仅为全新一次性 PG17.11 内的固定角色、ACL/RLS 和实验入口及其正反 SQL；不修改或执行 0001–0006，不运行 Alembic/业务迁移，不实现完整认证、真实登记或恢复，不开放一般数据库执行，不 commit、Push、合并或部署。

本批工具：[源码目录](../../../../tools/pr03b/batch4/)。原03准备包在 [原交付目录](../../../../outputs/pr03b/s3-batch4-acl-20261108-01/)，04入口修订及原失败在 [04交付目录](../../../../outputs/pr03b/s3-batch4-entry-remediation-20261108-01/)，05令牌ABI修订在 [05交付目录](../../../../outputs/pr03b/s3-batch4-token-abi-20261108-01/)，06维护后新目标在 [06交付目录](../../../../outputs/pr03b/s3-batch4-resume-20261108-01/)，07原生退出码在 [07交付目录](../../../../outputs/pr03b/s3-batch4-helper-receipts-20261108-01/)。当前08自动实测及交审材料见 [本轮回执](../../../../outputs/pr03b/s3-batch4-vmware-debug-20261108-01/DELIVERY.md)。本批不需要用户审定新的产品政策。

## 08：降权令牌默认DACL修复及真实验收

本轮经已授权 VMware CLI 自动完成原07首次执行与08新目标验证；未让用户重复手动操作。开始时发现原Ethernet0已启用、RasMan/SstpSvc运行，依既有隔离及两服务受限停止授权，仅在专用guest恢复原禁用网卡/正常停止服务状态；连续3次原guard及维护状态通过才继续。未改宿主、服务启动类型、SCM/注册表依赖、系统ACL、VM硬件或电源，不追溯猜测漂移来源。

07真实STOP为 `restricted_initdb / restricted_helper_exit:0xc0000142`，清理前原HANDLE退出码为3221225794，两个日志0字节，SQL检查0，Job/监听收尾确认。原ZIP、12条索引及晚生成文件哈希已核，07目录28项固定加入08保护清单；旧STOP仍保留。这个值现在是本轮实测，不追写为06的已知退出码。

根因是适配器遗漏降权token的默认DACL修正：当前VMware Administrator/session0的父及降权token均只有Administrators、SYSTEM的GA和logon SID的GX/GR，没有TokenUser的GA；禁用管理组后，原生启动在DLL初始化阶段失败。PostgreSQL官方 `CreateRestrictedProcess` 调用 `AddUserToTokenDacl` 专门补当前用户ACE。本轮最小修复仅为新建derived token保留全部旧ACE（含deny/顺序）并补当前TokenUser的继承GA allow，设置后严格回读；不设NULL/World ACL、不改父token、文件/desktop/系统ACL、不恢复管理组或特权、不加管理员后备。TokenUser/DefaultDacl使用独立有界指针解析，既有class8/2/21/3 ABI与原Job启动/收尾不变，实际postmaster/backend仍逐次核降权及用户ACE。官方源码与当前token只读探针均在本轮证据内归档。

08固定目录 `C:\Users\Administrator\PR03bBatch4Attempt08`，全新run为 `42fe5f4a45b04ab68cb68ef68aaed8e6`；PG仍位于原kit的随机run内。schema.py、cases.py、runner.py、原OS guard和旧helpers字节未改；仅adapter、三处HOME/清单锚点、自检及本文共六个源文件变化。保护清单为6个旧目录/105项文件，其他旧证据规则不变。自检增至24组，新增ACE旧拒绝保留/幂等、畸形ACL/SID、原生指针边界、只操作derived token、设置失败和回读不符拒绝；入口仍严格要求零失败/错误/跳过。

2026-11-08 22:08–22:14 PST真实运行：VMware传输成功、GuestExitCode=0，两个helper清理前均退出0，原 `initdb.exe` image hash与07相同；postmaster和28次backend令牌观测通过。**1254项SQL断言/19阶段/42次成功OS快照全部通过**，包括安装失败全回滚、23角色/ACL/default权限、12个LOGIN正反例、9表FORCE RLS、FK/status锁及撤权/目标epoch基础行为。08证据ZIP为49405字节，SHA256 `1b3b47aa8a8496d695952499fa4544ea8747b5018d01f97078260808a07f5caa`，12成员/11索引逐项核验。宿主对实际证据的32项核验见 [GUEST-VERIFICATION-08.json](../../../../outputs/pr03b/s3-batch4-vmware-debug-20261108-01/GUEST-VERIFICATION-08.json)。宿主24组合成自检、51项PS5.1入口及25项原guard/维护快照重放通过，均不替代上述真实SQL。

收尾已确认 owned Job、helper退出与监听移除，stale guard拒绝、固定lease撤销，`target_active=false`、`database_execution_allowed=false`。额外只读审计再次确认无PG、原隔离/维护状态及全部旧输入、当前包哈希不变，核对全部本地应用日志哈希；原日志含合成SQL错误诊断，按既定白名单留在guest，不导出原文。bootstrap的postgres只读探针继续 `target_verified=false`，仅自有空库身份阶段完成后本run报告 `target_verified=true`；停止后它仅是这次验证事实，不能重用为活动执行权。

**作者验收PASS，提交独立审查；不是独立批准或一般数据库启用。** 已停止，不应重跑08。0001–0006/业务迁移、完整认证、恢复、D0真实登记及后续机制门禁均未执行；本文件末尾保留的A01–A46、双连接锁序、秘密通道、P3与TBD边界继续有效。宿主异常不因guest成功而关闭。

## 07：在原Job收尾前保存helper原生结果

06真实通过19组自检、前后原OS guard/维护状态，以及受限主令牌检查；该guest的class21需要/返回1字节、对齐容量4已实测。06停在 `restricted_initdb / restricted_helper_exit`，两个日志0字节，本批SQL检查0，Job/监听收尾确认。initdb可能有内部活动，不能由空日志推定完全未执行；但尚未进入postmaster目标验证或本批固定SQL。

[完整只读诊断](../../../../outputs/pr03b/s3-batch4-initdb-diagnosis-20261108-01/)及本轮回传的2成员/1索引ZIP表明：固定失败窗口内五类日志查询均无匹配，WER无记录；ACL有Administrator用户本身的ACE，未做受限token AccessCheck，不能据此判定拒绝访问。没有恢复原生退出码，原因继续未知。旧06及全部旧STOP保留，不把没有日志写成启动故障已修复。

07唯一行为修订是记录既有helper流程：保留原start_helper→wait(30)→exit_code→非零拒绝→原Job close，原句柄观测的uint32退出码在close前保存为十进制/8位hex，同时保存PID、精确FILETIME、exe、image hash、等待完成和耗时；收尾再附cleanup事实。超时/未知码不猜测为0，不拿清理终止码替代原始码，不增加重试、管理员后备、其他exe或启动参数。若helper成功，自动继续本批完整固定流程；失败仍立刻STOP，记录用于定位的新事实。测试用的`0xc0000142`等为合成输入，**不是06实际退出码**。

新目录固定 `C:\Users\Administrator\PR03bBatch4Attempt07`，仍须不存在，PG仍用原kit下新UUID；不续跑06旧run。固定SHA清单增为5个旧目录/77项文件，另pin06及只读诊断；06的晚生成ENTRY-RESULT/日志哈希在只读准入中取得，不冒称其原文在06 ZIP中。原guard、schema/cases、restricted_process及旧helper字节不改，当前维护服务/SCM/注册表与每阶段原生归属门禁不减。自检21组新增原生码边界/超时和实际helper闭包的收尾前取码验证；1254项SQL、19阶段和42次成功OS快照要求不变。

## 06：维护后全新一次性目标

05通过17组合成自检和bootstrap，但在首个OS快照前拒绝，未进入token/initdb/PG/SQL。只读诊断确认新增8个WAN Miniport导致原guard的`Adapter set changed`；不更换基线、不忽略隐藏接口。用户随后明确授权仅在原guest顺序正常停止RasMan、SstpSvc并复核原guard，不改启动类型/网卡/guard或宿主。

maintenance01停止RasMan后，接口由13变6，仅剩SSTP；原维护包装器遗漏这一中间状态，故在SstpSvc停止前STOP。01还记录RasMan的SCM依赖丢失DnsCache，缺少此前注册表视图，具体原因保留未定。maintenance02在新目录中只完成已授权但未执行的SstpSvc停止；9成员/8索引的原ZIP及3次原guard快照核验表明回到原5张禁用接口、只有loopback，4个服务停止，02期间SCM及注册表依赖不变。旧STOP及上述历史依赖差异均保留。维护PASS只限该次窗口，不是ACL/RLS实验通过或通用数据库权。

06固定目录为 `C:\Users\Administrator\PR03bBatch4Attempt06`，必须不存在。保留原03/04校验，另外用固定SHA清单核05全部旧文件与两个维护目录共46项，包括这次已归档确认的05 ENTRY-RESULT原字节；任何增减/篡改/链接均拒绝。原guard、schema.py、cases.py、受限令牌适配及所有旧helper字节不改。每个OS阶段同时复核原5接口隔离及当前4服务停止、SCM配置/依赖、只读注册表依赖；配置漂移或服务重启STOP，不在实验脚本中自动停止服务。

快照失败现在输出有界阶段/安全错误码，runner保存退出码、输出长度/哈希及经筛选的失败摘要；非零退出、stderr、过大/非法JSON仍拒绝，诊断不授予许可。成功快照仍须原隔离断言及真实PG归属检查。06自检19组，新增旧05/维护证据篡改拒绝和快照失败留证；固定1254条SQL检查、19阶段、42次成功OS快照要求不变。ABI的真实guest验证仍待首次实际运行，不能用维护PASS代替。

## 05令牌信息ABI修订

04回传ZIP已校验外hash、11成员及10个index摘要，保留STOP。其16组自检、原件/依赖与前后OS隔离通过，停止于 `restricted_initdb / token_info_budget`；没有helper进程、PG或SQL，目标不活动、一般执行权关闭。04报告未记录具体class/required length，不能把推断当作该guest的直接长度测量。

对宿主**当前诊断进程**仅TOKEN_QUERY读取，实际观察class21需要/返回1字节，提供4字节且DWORD对齐容量时API成功；使用新生产解码器只读复核也通过。没有创建token/子进程、改账户或权限、操作数据库；宿主build26300与guest26100.1不同，此结果仅为ABI证据。微软维护者公开澄清该值为DWORD对齐地址上的BOOLEAN；已发布枚举页仍写DWORD，两者及适用版本保证不得混为一谈。05的真实guest长度将记录在实际token回执中。

05仅对class21使用1字节有效值：零初始化DWORD容量、DWORD对齐，先核sizing失败码122/需要长度1，再核实际读取成功且返回长度仍为1，仅解首字节；0为否、1为是，其他值或长度保持拒绝。class8仍须4字节；class2/3按真实返回长度验证计数/结构及1MiB预算，拒绝截断、权限错误和未知class。保留primary、Admin/PowerUser deny-only、禁最大特权及实际PG/backend令牌检查，不把“曾过滤”单一标志当作最小权限证明；不使用仅检查RestrictedSids且语义不同的IsTokenRestricted替代。原悬停创建/Job归属/句柄身份/Resume/失败清理未重写，没有管理员PG后备。

新固定工具目录 `C:\Users\Administrator\PR03bBatch4Attempt05` 必须不存在，PG仍取原kit下新UUID run。03和04都不重跑/覆盖；05逐次核03的原15项，并以04归档ZIP/源manifest/子回执绑定25个旧文件原字节。04在归档后生成的ENTRY-RESULT不在ZIP中，不能假称其原字节已归档：只接受与用户控制台/归档结果一致的完整JSON语义，再在05首次只读准入记录原字节hash并保持到结束。其余旧目录覆盖及原guard保留，旧JSON不能授数据库执行权。

## 04定向修订及旧现场

guest只读核对证明03曾通过15组自检并留下STARTED/STOP；没有RUN或数据库结果/证据ZIP，当前未观察到PG。旧common.bootstrap只读重查得到 `preserved_local_path`：它错误地要求审定历史清单里的所有文件位于 `PR03bNetwork…`，漏掉已获批的18个 `PR03bPreflightDiagnostics01` 文件。04改为先核固定SHA的原清单，再逐条验证其规范本地绝对路径、单链接/无reparse与原hash/size，未给调用者增加路径参数或跳过证据。

另一个已重现问题是03手动入口的顶层多语句：交互粘贴时throw不能阻止下一个顶层语句。04完整入口只有一个外层 `& { ... }`，顺序函数和统一try/catch包含所有解压/启动操作；目录存在、创建竞争、解压失败、子进程STOP或证据缺失均停止。早期与导出失败返回阶段/安全错误码，子Python摘要与本地诊断哈希留证；只有ZIP实际写成并复读核对后才报告可交付，加载两个所需压缩程序集，部分ZIP不冒充完整证据。

03固定目录 `C:\Users\Administrator\PR03bBatch4` 的12个包文件与3个标记原样保留，不能删除、重命名、覆盖或重跑。04每次前后校验这15项及固定hash、禁止目录增减/RUN或旧库复用。新的固定包目录是 `C:\Users\Administrator\PR03bBatch4Attempt04`，仅承接同一BATCH4固定SQL的一次新尝试；PG目标仍由原guard在原kit checkout内创建全新随机run，未改变其ROOT或允许旧目标续跑。

## 固定环境与执行范围

- 沿用 VMware 专用 guest、Windows 11 Pro 24H2 26100.1、64 位 PS5.1、原 kit03 的 Python 3.12.14、六个固定 wheel 和 PG17.11。guest 只有 C 盘，继续已有 guest 路径例外；宿主新文件均在本项目 J 盘。
- 当前包目录固定为 `C:\Users\Administrator\PR03bBatch4Attempt08`，已完成一次执行，拒绝重放。原 kit、B3、03/04/05/06/07、两个维护和只读诊断目录、全部旧 STOP 和批准证据只读保留。不能覆盖失败证据或删除重试。
- PGDATA 为原 kit checkout 的 `work/pr03b/runs/<新32位随机run>/pg/data`；原 guard 的 `ROOT`、路径、`pr02a_`、字面 `127.0.0.1`、非5432端口、nonce/FILETIME/PID/原生双视图 LISTEN=2/Job/目录/配置/system-id 约束均保留。新 run 不接管旧目录/进程，不修改旧 helper、不修改其全局 ROOT。
- 管理员 PS/Python 只负责 OS 只读观察。PG/initdb/controldata 通过受限主令牌启动：Admin、Power Users 为 deny-only，禁最大特权，核实际 postmaster/backend 主令牌及当前用户默认ACE。适配只替换该 Job 实例的 CreateProcess 调度；原悬停创建→归属 Job→HANDLE/父PID核验→恢复以及失败收尾不重写。API失败即 STOP，无管理员启动后备、无服务/账户创建。08已在指定guest验证此适配，其他宿主/账户环境未由此通过。
- 五适配器禁用、唯一活动双栈 loopback、default compartment、无外部/default route 的原七函数逐字节复用；新 Snapshot 包装器只允许本 run 持有 Job 内的 PG PID，任何外来 PG 直接 STOP。每固定阶段前后重读 OS，连接及事务边界复验原 native guard，SQL backend 活着时核其父 postmaster/Job。
- 不恢复网卡、不重跑 B3 外部探测、不动宿主配置/PG。OS/IP 隔离是 B3 获批机制加本轮当前状态复查，不声称抵抗恶意管理员/内核修改。
- DB 默认只读；HBA 仅本 run bootstrap 的 postgres/本 run DB，以及12个固定实验 LOGIN 的本 run DB，均仅 IPv4 loopback/SCRAM，其他拒绝。只有固定脚本短事务显式 READ WRITE。连接参数由内存 plan 生成，不接收 URL、host、SQL文件、角色或续跑 CLI 参数；拒绝 PG* 环境变量。凭据仅合成，PGDATA/pwfile不导出。

## 实施对象与能力表

仅创建实验 schema `identity_control`、`identity_api` 内的九张 `lab_*` 表和十个函数。它们是 S2 权限分离的最小可执行基础，**不与正式入口同名，不是生产迁移或业务状态机**。

| 每 run LOGIN 后缀 | 固定入口 / NOLOGIN owner | 窄能力 |
|---|---|---|
| runtime | lab_runtime / classroom_web_runtime | 当前合成本人文档；Admin无他人正文例外 |
| auth | lab_auth / classroom_web_auth_verifier | 当前本人合成因子存在性布尔；非密码/MFA验证器 |
| session | lab_session / classroom_web_session_writer | 本人 lab effect 的 INSERT；非建立真实session |
| recovery | lab_recovery / classroom_web_manual_recovery_writer | 本人 lab effect 的 INSERT；不切因子/epoch、无final或发码 |
| status | lab_status / classroom_web_manual_status_reader | 当前有效原执行者自己的case/op最小事实及共享锁，不读target因子或秘密 |
| maintenance | lab_maintenance / classroom_web_registration_maintainer | 合成authority的窄guard.version更新；非真实D0登记 |
| notice | lab_notice / classroom_web_identity_notice_worker | 独立identity notice的attempts更新；无业务counter/Outbox/FK |
| identity_cleanup | lab_identity_cleanup / classroom_web_identity_history_cleaner | 仅显式 eligible 合成effect；不创建真实U02保留政策 |
| scheduler/dispatcher/local_reconciler/outsider | 无上述业务入口 | 跨角色/正文/秘密/维护均拒绝 |

12个 LOGIN、8个动作 NOLOGIN owner、独立表 owner、context reader、实验 observer 共23个角色；均非super、非BYPASSRLS、非建库/建角色/复制、NOINHERIT，只有12个LOGIN可登录，无membership。仅表 owner 拥有两个schema及表；各动作函数owner与表owner分离，无schema CREATE；LOGIN无原始表/列授权或TEMP。

全部表 FORCE RLS，按动作/列授予函数owner；所有函数 SECURITY DEFINER、固定 `pg_catalog, identity_control, public, pg_temp` 且限定schema、无动态SQL；PUBLIC无schema使用/创建/函数EXECUTE，bootstrap及所有新owner默认函数EXECUTE撤PUBLIC。角色矩阵同时用catalog权限和实际调用核对，防止“内部ticket拒绝”遮盖错误EXECUTE授予。

合成 broker 由可信 bootstrap 预置 `ticket_hash + session_user LOGIN + actor + purpose`；scope 不从 `app.actor_id/owner_id/authority_id` 自报生成。合成固定ticket仅模拟受信上下文投影，哈希用内置MD5做查找，**不构成生产认证或新的信任根**。本批不验证 OS peer、MFA/签名、D0在线根、完整fresh/nonce/cap协议。LOGIN凭据与broker边界的真实保护仍属后续门禁。

必要测试入口 `lab_observe()` 由独立NOLOGIN observer拥有，授权给这12个实验LOGIN；只返回非秘密状态投影的MD5差分值，不返回正文、ciphertext、ticket摘要、seed或恢复码。此 hook 只存在一次性fixture，不进入正式契约。bootstrap还有一条明确合成status context，仅用于 SET LOCAL ROLE 到不可达NOLOGIN owner，实测其共享锁权限不能变成UPDATE；运行LOGIN仍必须拒绝SET ROLE到这些owner。

## 固定实验及判定

| 固定阶段 | 必须成功 | 必须拒绝/故障阈值 | 证据 |
|---|---|---|---|
| 前置与启动 | 原4513 payload、2446 runtime、六wheel与已安装import文件、原guard/旧证据一致；当前隔离；真实Job/端口/目录/令牌/只读握手一致 | 现有同目录/外来PG、源或依赖变更、环境重定向、双视图或OS不一致即STOP；helper30s、postmaster20s内未就绪即STOP | 输入hash、OS快照、listener control、实际token、system-id、只读身份 |
| 安装控制 | 恶意同名角色未被采用；全fixture安装后故意22012使全部角色/schema回滚；干净状态再仅安装一次 | 同名对象/失败后残留不得继续；不使用IF NOT EXISTS接管 | 精确控制断言与catalog复查；真实SQLSTATE |
| ACL/catalog | 23角色flags及零membership；12×9函数矩阵；无裸表/列/TEMP；9表FORCE RLS；10固定definer及关键列ACL/default权限匹配 | 任一错授或漏撤销即STOP，即使函数内部仍拒绝也不能PASS | 每条命名断言，前后catalog复核 |
| 各LOGIN正反 | 八动作对应固定入口实际成功；写效应事务内可见，回滚后digest恢复；本人正文与当前actor一致 | 其他角色入口、秘密/正文SELECT、factor/epoch/authority/context DML、SET ROLE、schema/TEMP/函数创建、动态SQL、空/伪scope均必须42501；成功/其他SQLSTATE/超时/断连都不算拒绝成功 | 每反例前digest→SAVEPOINT→调用→精确SQLSTATE→回滚savepoint→digest一致；跨角色catalog补证 |
| RLS/FK/status | 表owner受FORCE RLS；池复用A→B→空和固定search_path；本人status重复零写、其他Admin/合成authority各只见本人、target升epoch不破坏读取；撤权后拒绝 | 错executor/owner FK必须23503；status owner FOR SHARE确实读到行，`UPDATE id=id`必须42501；非目标/秘密列无授权 | 实际可见行在内存核对，仅导出布尔；status锁/不可DML与状态差分 |
| 收尾 | 所有helper/owned Job退出、监听消失、stale guard拒绝、当前隔离和原件前后不变 | 任何清理/监听/证据不明保持STOP、active=null，不声称已停止；不触碰外国进程 | 清理事实、最终OS快照、旧输入与源码前后摘要、结果JSON |

SQL `statement_timeout=3000ms`、`lock_timeout=1000ms`；连接2s。每个固定SQL阶段有90s客户端总watchdog（初始只读/建空库沿45s外层与旧6s内层）；超时只关闭自己持有的Job，另有原10s清理预算，不宣传总耗时≤SQL timeout。角色case同连接内每个显式BEGIN/COMMIT/ROLLBACK再次native guard；所有连接关闭后确认backend离Job。OS快照20s客户端限时，不能取得完整证据则STOP。

结果只有所有断言、阶段、OS前后、原件与收尾均通过才PASS；若未执行、证据不全、真实SQL报错或清理不明均STOP/待核验，不能用源代码编号/语法解析补PASS。`database_execution_allowed=false`始终表示一般执行权关闭；另记录仅本 run 固定脚本的短时lease及收尾撤销，不可用历史JSON授写。

## 交付与下一阶段

07准备及其21组自检见原07回执，保留为历史结果。本轮08的24组宿主/guest自检、PS5.1入口、快照重放、真实SQL及证据核验分别见本轮DELIVERY及JSON，不合并为一个测试数。08 ZIP、完整 `RUN-BATCH4-08.ps1` 及自动包装器已归档供独立复核，**已执行后停止，不需要用户重跑**。入口仍仅在固定新目录不存在时准入，校验外ZIP SHA/成员后自动完成自检→固定实验→白名单证据ZIP；安装回执另存 `PR03bBatch4EntryEvidence` 唯一文件，旧现场/STOP不覆盖，不恢复网卡。

本批可覆盖角色ACL/RLS基础，**不关闭**完整A01–A46、19状态推演、A45双连接显隐式锁/通知全路径、D02真实120秒屏障、D03完整认证资格与unknown/提交线性化故障、秘密通道、恢复final或新码响应丢失等未执行门禁。不验证0006增量/重复、非super迁移身份或临时owner迁移membership；本fixture使用全新集群可信bootstrap，因此 PR03a P3-01继续OPEN。D01–D03保留设计层CLOSED；真实D0/U01、U02真实留存、宿主异常、既有任务/P3不变量及TBD-08 OPEN均保留。

guest证据回传核验及独立审查后停止；本批PASS不授权继续业务迁移、完整认证、机制扩展或真实启用。
