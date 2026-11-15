# PR-03b S1：隔离实验环境方案

日期：2026-11-06（America/Los_Angeles）。准备基线：`e4853223847b3c26bfc74fa8fb2271cdb10e18b9`。本文件只描述**待审、尚未创建的环境**；不提供本轮可执行的建库、迁移、安装、登记或恢复命令。精确基线的 D01–D03 已 CLOSED（设计层），S1 尚未整体通过；正式 contracts、机制实验、实现、真实 D0/身份登记及恢复启用未获批准。TBD-08 保持 OPEN — PARTIALLY DECIDED，条件风险接受尚未生效。

本方案与 [准备索引](README.md)、[技术选择](TECHNICAL-CHOICES.md)、[验收与证据](ACCEPTANCE-AND-EVIDENCE.md)、[审查交接](REVIEW-HANDOFF.md) 一起提交。原 [A01–A46 及阶段门禁](../TEST-EXPERIMENT-GATES.md) 和 [会话/恢复设计](../SESSION-RECOVERY-DESIGN.md) 保留；本文件不以环境选择改变产品政策、DAG、历史迁移或原测试断言。

## 1. 先验证机制，再在真实目标验证容量

| 环境层 | 待审计划与可证明内容 | 明确不能据此推断 |
|---|---|---|
| E-W：Windows 隔离功能环境 | 优先利用本机历史上已验证的可信 bootstrap 能力，在本 PR 工作树内**新建独立** PostgreSQL 合成集群；先验证PG事务/ACL/RLS/锁、nonce、撤销、status、合成D0及Windows本机peer路径。当前只读历史记录，不连接或复用旧 PR 集群；不承担默认浏览器全套验收 | 当前集群/工具已就绪；Windows功能通过等于可信HTTPS/WS浏览器已就绪、Linux容量、真实D0、真实恢复或托管平台支持 |
| E-L：默认HTTPS/WS功能及Linux资源筛选环境 | **推荐一次性隔离Linux VM，优先于WSL2**。VM磁盘/配置位于当前J盘run目录；客体内PG/web/proxy仅loopback，Chromium及客户端/负载也在同一客体，使用独立有硬上限的资源分组。服务侧参考2 vCPU / 4 GiB；合成CA仅进入该一次性客体的有效信任库 | 已选Linux部署平台；VM或许可已可用；桌面进程设定值就是硬配额；同客体资源分组等于独立物理负载机 |
| E-T：真实目标复测 | 部署平台另行确定后，使用同等资源、进程数、数据库/代理拓扑的隔离合成环境复测 Argon2、预算、正常/过载负载及恢复；审查平台实际迁移身份能力 | 在 E-W 或 E-L 的一次通过可代替部署目标复测或关闭 PR03a P3-01 |

默认E-L拓扑为同一一次性客体中的 `Chromium/负载生成器 → 127.0.0.1本地TLS代理 → web → 127.0.0.1独立PG`，邮件捕获器和合成D0也只在该客体的受限本机边界内。浏览器的`app.test/evil.test`仅映射客体loopback；没有跨客体访问PG或对外监听需求。建议整台实验VM预留4 vCPU/8 GiB：服务资源组限定2 CPU/4 GiB（包括web、worker、PG、代理、捕获器及D0），客户端/负载资源组限定1 CPU/2 GiB，其余留客体系统；记录实际限制、CPU分配、所有进程归组和宿主争用。该总量是实验预算建议，不是部署配置或现有硬件承诺；资源不足时不以未隔离桌面负载冒充此拓扑。

VM引擎/许可/虚拟化能力、Linux镜像来源与具体版本在获准创建前只读核验；这些是执行就绪条件，不把明确的默认方案推迟成平台待选。WSL2只作备选，须先证明一次性磁盘/配置、信任库与宿主隔离、真实CPU/RAM/swap硬上限及清理边界，才能申请替代默认VM。若将来分离客户端到另一个客体，应用loopback不可直接被远端访问；必须先提交精确对端/端口的隔离虚拟网及受限TLS代理/端口转发差异，不能悄悄改为`0.0.0.0`或让PG外露。本稿默认同客体，不依赖该扩展。

ADR-02 已选择 Python 3.12，ADR-03 已选择 PostgreSQL 17，见 [ADR 登记](../../architecture/07-ADR-REGISTER.md)。本次只读 [PR03a 历史 VALIDATION](../../pr03a/VALIDATION.json) 中的服务版本为 **PostgreSQL 17.11 / x86_64-windows**；这证明历史环境，不是当前运行中的版本或新环境验收。后续保持已选 major 17，在 run manifest 中记录实际 patch、二进制来源与 SHA-256、编译/平台、扩展及客户端版本；换 major 须另审，不在本方案任选。

只读 [后端依赖锁](../../../../web/backend/requirements.lock) 当前包含 Python 3.12 对应的 SQLAlchemy 2.0.46、psycopg 3.3.3、Alembic 1.18.4、argon2-cffi 25.1.0；本轮没有安装、导入或验证这些运行依赖。Linux 组件、浏览器/驱动及本地捕获器的具体版本、来源/hash和安装方式须在获准创建前固定；不得把 Windows 解析锁无验证地称为 Linux 安装成功。

## 2. 路径、身份与运行清单

计划的唯一可写根是本 PR 工作树 `J:\class-assistant\worktrees\web-pr03b-sessions`。未来每次运行使用未存在的 `work/pr03b/<run-id>/` 和对应 `outputs/pr03b/<run-id>/`；依赖、下载、cache、TMP/TEMP、PG、D0 模拟、浏览器 profile、捕获器及中间文件全部归该 run 管理。本轮仅写 S1 准备文档，**不创建这些运行目录或进程**。

未来 run manifest 至少固定：完整源码 SHA/dirty 白名单及源文件 hash、契约 hash/版本、增量迁移 head、run-id、UTC 起止时间及显示时区、OS/CPU/内存/虚拟化、实际 CPU/RAM/swap 限制、应用/worker/PG/代理进程数和 PID/启动时间、端口、绝对工作路径、cluster marker、数据库/角色映射、依赖及证书指纹、网络准入范围、负载生成器与被测服务的位置。秘密及完整环境变量不写入 manifest。

正式入口 `J:\class-assistant\classroom-os`、其他 PR 工作树、旧 `work/pr02a`/`work/pr03a` 集群及其证据不是本 run 的所有物。正式入口可能存在其他分支或用户 dirty 状态；只读快照不能变成清理、切分支、停止进程或复用数据库的授权。

## 3. PostgreSQL 连接与目标保护

原 [统一目标验证器](../../../../web/backend/classroom_web/test_support.py) 当前仅接受 `postgresql+psycopg`、**字面 `127.0.0.1`、显式非 5432 端口**及安全的 `pr02a_` 数据库/登录名；拒绝全部 URL query、控制字符、任意大小写 `PG*` 环境变量、DNS/`localhost`/多主机/Unix socket/连接串注入。它在创建 engine/连接/子进程前拒绝危险输入，不接受调用者额外传 libpq options。保留这些拒绝语义。

原 [PR03a runner](../../../../tools/pr03a/run_tests.py) 及 [整改 runner](../../../../tools/pr03a/remediation/run_tests.py) 进一步核对实际 server 的 login、database、host、port、listen_addresses 和相对该 runner ROOT 的 `work/pr03a/pg/data`。它们**没有**本文件计划中的 run-owned cluster marker/PID 全套保护。不得直接把 URL 改成 `pr03b_`、替换旧路径或连接已有 PR03a 服务来运行。

后续若获 S3 精确实施授权，须先提交独立最小 guard/runner 适配方案及负例审查，再实现和运行；不得修改旧 runner、放松旧断言或伪造旧目录来通过。新的 run-owned 保护必须在任何数据库/角色创建、迁移、测试写入及清理之前完成：

| 保护层 | 后续必须验证 | 拒绝条件 |
|---|---|---|
| 连接解析 | 显式批准的单个 loopback 地址/非默认端口、无重定向 query/环境、合成数据库/登录名；名称策略与旧 `pr02a_` guard 的兼容差异先审 | DNS、外网、5432、缺端口、任意应用连接串、继承 service/passfile 等隐式目标 |
| 本地所有权 | manifest 的绝对 data_dir 位于该 run 根内，无 reparse/junction/symlink 逃逸；run-id 与独立 marker、初始化记录匹配 | 只有目录名或数据库前缀相同，marker 缺失/不匹配，指向正式或旧 PR 数据 |
| 实际进程 | postmaster PID、启动时间、可执行路径/hash、该集群 data_dir/端口与 manifest 一致；只管理该 PID，不按进程名批量停止 | PID 复用、服务来源不明、相同端口指向别的进程、集群已被替换 |
| 实际 server | 受控初次握手只读核对 `data_directory`、server addr/port、listen_addresses、current_database/current_user、PG major/patch 与 marker 所指实例；使用代码持有的固定连接选项 | 任一不一致、无法核验、超时或连不到；不得改猜测目标继续 |
| 数据库与角色 | 集群为该 run 独占；目标 DB、随机测试 DB、合成 LOGIN/NOLOGIN 角色均在精确创建清单中。迁移和测试复用已验证连接，不再次从环境解析目标 | 任意正式 DB、共享集群、未登记同名角色/数据库、连接池回收后身份漂移 |
| 网络 | PG 仅监听 `127.0.0.1`；进程/系统隔离层拒绝外部连接与 DNS，guard 失败即停；固定端口和集群身份共同匹配 | 为“连通”而开放 `0.0.0.0`、公网/局域网、通用 trust 或关闭保护 |

具体端口尚未分配；将来先核对占用和目标身份，不能沿用历史 4979/55439 或默认端口。旧记录中的临时 loopback trust 不是部署认证方案；新合成环境须限定 OS/进程访问边界和角色认证，不复制到真实部署。

## 4. 角色、权限及正反 SQL 矩阵

下表是待审的**逻辑能力矩阵**，不冻结新 SQL 角色名/GRANT 或创建全套模型。已有角色/0001–0006 字节保留；实际新增函数、owner、search_path、默认权限、ACL/RLS/复合 FK 和迁移差异须 S2 正式审查。将来每个运行 LOGIN 只显式获得本能力所需最窄授权，不继承其他控制面、scheduler、migrator 或 owner；若需 membership，单独列明 INHERIT/SET/ADMIN 边，禁止间接提权链。不得用 superuser/owner fallback 使负例“通过”。

| 逻辑主体 | 正向能力与验证 | 必须拒绝的 SQL/调用 |
|---|---|---|
| 普通 HTTP 外部调用者 | 仅经服务端真实 Cookie/session/device/epoch/CSRF/权限链访问；没有 SQL 登录或 DB 凭据 | 输入 owner/role/Principal、任意表/函数、借 OwnerContext fallback；伪造 actor/authority |
| owner runtime | `SET LOCAL` 的可信 owner 及 FORCE RLS 下访问本人授权资源，复合 FK 保持所属关系 | 读写另一 owner 正文；查密码/seed/恢复批准；继承 identity/scheduler；SET ROLE 到高权身份 |
| auth lookup/验证适配器 | 固定最小定位/验证函数；密码/因子材料只进入受限验证路径，返回认证所需最小事实 | 通用 SELECT 正文/MFA/全用户表、任意 owner 参数、任意动态 SQL、创建/修改角色 |
| identity 安全变更 | 经审定事务创建/撤销会话、因子/epoch等必要窄写，按统一锁序同提交审计/内部事件 | 普通业务 Outbox/counter 通知、无权限直接清 MFA、改 D0 根/登记、越权恢复 Admin |
| recovery issue/complete | 受控本机命令、当前 actor/authority、fresh 单次≤2分钟操作证明及 scope，在窄事务中推进本人执行 case | 通用 UPDATE/DDL/BYPASSRLS/正文读取、人工 SQL、浏览器或其他 Admin finalize、自授维护权 |
| control-plane status | 当前稳定执行者重新认证；只读本人 case/op 最小事实、锁内重核资格/期限及读取线性化 | 其他 case 存在性/target资料/秘密；任何 case/nonce/cap/factor/epoch 写、重签、finalize |
| 登记维护 | 验证 PG 外 D0 固定 K_change 的独立批准，只前进地推进窄 authority/deployment 状态及审计 | 用 K_recover/新 key PoP/Web Admin/普通 DB 登录自授；请求携带新根覆盖可信配置 |
| migrator/bootstrap | 仅该 run 的 disposable cluster 初始化/已审增量迁移；记录临时权限前后差异及撤销、失败完整回滚 | 向 runtime 等授 bootstrap/owner/superuser；复用真实身份；把非超级用户安全拒绝当成功升级 |
| scheduler/dispatcher/LocalReconciler | 保留各自既有固定端口、raw/fence/unknown/Receipt/Outbox边界 | 登录/恢复/清 MFA、HTTP fallback、正文返回、重发 unknown Provider 或清收费风险 |
| SECURITY DEFINER owner | 无 LOGIN、固定 search_path、无动态 SQL、撤 PUBLIC，最小表/列权限及可审 RLS；仅供受限函数 | SET ROLE/继承取得 owner，schema CREATE/危险 membership 残留，任意 owner/动态函数入口 |
| PG 外合成 D0 维护者 | 在独立受保护目录/进程权限中固定合成根、本人映射、K_change和版本高水位；模拟首次安装/变更/撤销 | 把普通恢复进程/DB写权限当 D0 权限；使用真实人的身份/key、改真实部署 IAM |

每类至少以独立受限连接执行一个允许路径和全部相关拒绝路径；不仅检查错误码，还断言所有相关表、nonce/cap/epoch、审计、通知、membership及正文投影无越权副作用。直接 SQL 负例是合成权限验收，不授操作员人工 SQL 恢复权。还须核对 `rolsuper/rolbypassrls/rolcreaterole/rolcreatedb`、对象 owner、PUBLIC、默认权限及完整 membership 图，覆盖连接池身份复用和失败回滚。

PR03a [P3-01](../../pr03a/remediation/checkout/P3-01-DEPLOYMENT-SUPPORT.md) 继续 OPEN；当前成功支持仅可信 bootstrap。E-W 的 disposable bootstrap 不能证明非超级用户/托管 provisioning 可用，更不能据此决定正式部署数据库/迁移身份。真实平台选择前仍须按原关闭条件评审。

## 5. PG 外的合成 D0

模拟器只用合成 deployment/authority/OS/key/批准材料。其固定根、指定用户映射、K_change 与不可回退高水位/批准 nonce 账本位于 **PG 外**的 run-owned 目录；以独立进程/OS权限使恢复执行和普通数据库角色不能写入。单进程内可任意改写的测试字典不能替代这个隔离边界。目录ACL、维护者与恢复进程身份、读写能力及高水位来源均留非秘密证据。

模拟器必须支持：首次安装须外部合成批准；自带根/自签清单拒绝；独立变更绑定 deployment/authority/action/old-new hash与版本/nonce/期限；只前进高水位；清单与PG错配拒绝；中断仅沿原批准前滚；旧PG备份/旧清单回放、根缺失/全失安全关闭；登记撤销与issue/complete/status按各自锁集串行化。外部网络或人工交互不在PG持锁期间执行；PG控制状态为审定的变更线性化点，跨系统发布不原子时拒绝。

这些可测试应用在合成 D0 假设下的行为，**不能证明真实平台IAM、实际项目用户归属、真实根已安装、部署/root/验证器完整性或真实恢复可启用**。模拟器宿主也被完全控制时不声称抗篡改；真实D0建立与信任重建仍须独立登记/批准流程。

## 6. HTTPS、浏览器、邮件、WS与出网边界

| 组件 | 待审隔离安排 | 开始前必须解决/留证 |
|---|---|---|
| 本地 HTTPS | 仅 `127.0.0.1`监听；`app.test`、`evil.test` 及相同host不同HTTPS端口形成受控跨Origin组，固定本机解析且拒绝外部DNS；证书SAN、有效期和端口清单固定 | 不能修改全局 hosts/DNS/系统代理；先证明测试进程解析精确指向loopback。生产缺失/null Origin不因实验方便放宽 |
| 浏览器信任 | 默认在上述一次性Linux客体内使用独立run-owned Chromium profile，将合成CA导入**仅该一次性客体**内Chromium实际采用的信任库；可影响该客体专用用户/系统信任，但不进入宿主、用户常用浏览器或真实部署。浏览器实际验证合成CA→站点链 | 留证客体ID/磁盘归属、CA指纹/有效期/SAN、Chromium实际链验证及宿主根存储前后不变，关停/后续获准销毁只影响该run客体。Windows Chromium的profile不能默认隔离CA；若增加Windows浏览器验收，采用同样一次性Windows客体整套loopback拓扑或另行证明的信任隔离。不得用ignore TLS/certificate errors、SPKI忽略列表或关闭证书验证；其他浏览器不能代替Chromium证据 |
| Cookie/CSRF | 真实 Set-Cookie、cookie store、网络请求、Origin/CSRF、SameSite、会话恢复与期限；独立profile避免真实账号/扩展 | 禁用同步/账号登录/扩展及后台出网；证明没有真实存储或凭据。抓包只保留脱敏断言 |
| 本地邮件捕获器 | SMTP和查看接口只在loopback；合成`.test`地址，adapter真实到捕获器，禁relay/外网发送；不使用真实邮箱账号 | 端口/PID、无外网转发和捕获清理边界；测试邮件中token仅在隔离进程内比较，持久报告不保存原秘密 |
| 真实 WS | 真实TLS upgrade、Cookie/精确Origin、`classroom.v1`和首帧ticket消费；错误Origin、首帧期限、撤销/通知丢失/重连用真实连接 | 仅loopback，真实PG单次竞争；不能用MockWebSocket或关闭TLS验证声称通过 |
| Provider/音频 | 只有MockProvider和合成数据；无外部API、收费、真实ASR/AI、麦克风或录屏采集 | 网络隔离和调用断言证明无实际发送；旧unknown/raw/Receipt/Outbox/fence不变量仍须真实PG验证 |

网络允许清单限定本次各组件的精确loopback端口及本机合成域名映射。PG继续只接受字面IP，不因浏览器`.test`域名而放宽PG guard。机制与负载运行期间外网默认拒绝；若后续获准下载依赖，下载与测试分阶段，来源/hash先核验，不把安装出网延续到测试进程。隔离措施及其自检本身也未实现/运行。

## 7. 资源、并发和时钟

正常参考负载统一为：**30个合成用户、30条已认证WS、持续10个业务请求/秒，另加1次登录/秒**。业务读写/账号角色/已启用MFA比例、真实PG持久任务背景流量、代理及应用/worker数量在run计划中固定；不发送真实Provider。默认负载生成器与Chromium在同一E-L客体的独立客户端资源组，与被测服务2vCPU/4GiB组分离，记录两组CPU/RAM硬上限、实际资源使用与时钟；不称独立物理负载机。无哈希、正常与过载三类基线采用**相同VM/服务/客户端资源组和loopback拓扑**，不得将未计入客户端资源的基线与共享预算的负载结果比较。报告同guest/宿主争用及网络近距限制；真实目标拓扑不同须复测。

建议首轮正常组固定为26名合成学生（13名启用MFA、13名未启用）及4名强制MFA合成Admin；专用恢复权限只给显式选定的合成执行Admin，其他Admin用于拒绝组。30条WS每账号1条；业务10req/s先采用8次本人课程列表读取与2次本人课堂元数据读取，不含收费、采集、reconcile或多Attempt扩展，后续S2/获准S3必须明确映射实际路由，不能为压测顺手挂未授权入口。默认2个API进程、1个空闲合成worker（只扫描本run队列，无待提交Provider工作），服务预算包括其开销；各API/worker/控制面PG池上限与连接总预算由S2列明。每秒完整登录使用新的合法preauth；同loopback/NAT来源每分钟60次在技术文档候选90次/分钟来源创建预算和120次/分钟全局预算内。已启用MFA账号仅在其凭据产生未消费新时间步时排入成功控制请求，其余时隙用其他合法账号，不能重放TOTP后将必然拒绝计作性能故障；实际角色/因子请求比例随证据列出。

这里2vCPU/4GiB是E-L的**服务资源组筛选上限**；客体OS/内核及未归组开销仍须另记，组内25%余量不能证明整台2vCPU/4GiB主机已具同样容量。E-T目标复测须把OS/内核、全部部署进程及共享开销纳入整节点有效CPU/RAM，满足全节点至少25%内存余量；不得把实验VM外侧余量当部署主机的免费资源。

PG连接的本次首轮候选为：每API进程auth lookup池2、owner池2（两进程共8）；独立identity池2、recovery池2、status池2、maintenance池1、worker池2、邮件dispatcher池1，服务侧总上限18、各池额外overflow=0；测试屏障/观察/迁移连接最多4，PG实例max_connections候选32，为数据库自身及失败诊断留余量。各池采用不同受限LOGIN/已审角色，不为复用连接混合控制面权限。它们是合成容量输入，不冻结生产连接参数；S2须核实际角色/调用图与连接生命周期，并验证超池有界拒绝/归还和不会借fallback角色增权。D02长等待和双连接专项独占对应测试资源，不在正常容量曲线中混算。

Linux候选资源必须留出硬限制证据，例如VM配置与客体实际可见CPU/RAM、cgroup限额及计数、swap限制、进程树和OOM事件。不能仅填写“2核4G”或用浏览器/PG/负载机共享未计入的宿主内存来宣称满足。应用、worker、PG、代理及短队列在同一拟部署节点时全部计入总预算；远端PG情形必须单列，不与同机结果混算。

Argon2筛选沿原H-A至H-F：并发1/2/4/8、突发16/32，含正确/错误/dummy、长输入、取消、跨进程、后台共存及过载后恢复。首要候选64MiB/t3/p1仍是候选，不因资源不足自动改低。总峰值包含全进程实际RSS增量、基线、队列/请求体/线程栈等，**至少保留25%有效内存余量**；`m`是单次总内存参数，不机械乘`p`。CPU执行槽、总队列与最长等待须跨进程有界，增进程不扩大总预算。具体成功阈值、样本和持续时长统一见 [验收与证据](ACCEPTANCE-AND-EVIDENCE.md)。预算不足应报告环境/容量/体验取舍，不降哈希成本、不跳dummy/MFA、不放宽断言。

时间使用服务端数据库实际时钟，锁后使用能反映当前时间的`clock_timestamp`重核；不修改宿主/全局PG时钟。D02真实跨120秒专用交错须另设足够且有界的事务局部timeout及harness watchdog，普通用例不继承这个长上限；具体180/210/240秒候选及≥121秒屏障安排由验收文件统一。可控clock仅限获审的测试边界，不能替代真实锁等待/HTTPS到期证据或延长生产授权。

## 8. 秘密、证据与将来清理

只使用随机合成密码/seed/key/PIN/cap/token；不读取用户真实配置、课堂、证件、公钥/私钥或部署凭据。秘密不进argv、环境、stdout/stderr、shell history/trace、录制、APM、core dump、通用Receipt/Outbox和最终报告；安全prompt/受限FD、异常脱敏及禁转储须作为机制门禁验证，不靠“不打印”约定。浏览器原始profile、PG数据和捕获邮件属于隔离中间物，不自动打包上传；证据仅保留最小非秘密状态、批准引用/指纹、脱敏屏障时间线、SQL状态/副作用断言、完整命令结构及退出码。

失败保留原日志和断言，写明实际运行/未运行、失败/跳过；不能用历史464/Windows/Node或目录/编号检查代替本轮机制成绩。将来的证据保留期限和必要故障取证经审查后固定，未清理秘密中间物时不得声称已销毁。

清理另需对应执行授权。本轮不停止服务、杀进程、DROP数据库/角色或删除目录。未来只清理创建清单内、marker/PID/启动时间/实际server身份一致的该run资源；任何递归删除前重新解析并核实绝对目标位于本run根内，拒绝根路径、其他树、junction逃逸、旧集群或不明所有权。不得按通配前缀、进程名、共享端口或`CASCADE`扩大清理范围；发现不一致应停止并留证，不能转用其他shell拼接删除。

## 9. 环境未定项与当前停止点

本稿已推荐默认拓扑和信任边界：E-W仅PG/Windows本机peer机制，完整HTTPS/WS及Linux资源筛选在一次性Linux VM内完成，CA只进入该客体，客户端与服务用独立资源组并保持一致性能基线。待S1审定的是这套具体方案及预算、具体端口/版本/来源和证据保留计划；实际VM能力/许可、硬配额及Chromium信任库导入验证在获准创建前核验，未核验不声称就绪，也不因此把方案审查改成必须先建环境。待S2：新增角色/ACL/迁移、D0控制协议、四时间/issue/complete/status、N01显式/FK/唯一锁图与双连接门禁、N02完整重登后显式新组及丢响应契约。待S3另行授权：guard/runner最小适配、建环境和安装、合成机制实验及实现；真实目标与真实D0另行核实，不在本轮建立。

本轮只读核对已有ADR、依赖锁、历史PG版本和runner源码并准备本文；**没有创建环境、安装依赖、连接PG、运行实验/测试或登记/恢复**。正式树、contracts、代码、测试、迁移和历史证据保持；不commit、Push、合并或部署。S1剩余材料由独立审查裁定，不自批S1通过或关闭TBD/P3。
