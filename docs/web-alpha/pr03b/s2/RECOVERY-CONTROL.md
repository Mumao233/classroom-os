# PR-03b S2：独立恢复控制协议候选

日期：2026-11-06（America/Los_Angeles）。契约包候选 `0.2.0`；独立控制协议 `protocol_version=1`。这是 S2 正式契约审查输入，**尚未获 S2 批准、未实现或执行**。精确源码基线仍为 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`；既有 S1 工作区快照保持，S1 审定通过见 [APPROVE S1 报告](../../../../outputs/pr03b/s1-remediation-review-20261106-01/REVIEW.md)。D01–D03 保持 CLOSED（设计层）；此处补协议与事务细节，不重开已确认政策。

机器形状见 [identity-control.schema.json](../../architecture/contracts/identity-control.schema.json)。其 `$defs` 和根 `oneOf` 是本机工具、D0 与受限浏览器接收桥的独立消息模型，**不在公开 OpenAPI 增加管理员运维端点**。原五个业务状态机不改。JSON Schema 只验证结构；下列验签、当前授权、时间、跨字段、锁与持久事实也是规范，不能以 schema fixture 通过代替机制证明。

TBD-08 继续 OPEN — PARTIALLY DECIDED；条件风险未生效。真实 D0、平台 IAM、实际人/凭据/根、学校核验渠道及启用授权均未建立或执行。U02 的最小 op/audit 90 天与核验详情 30 天仍是候选，**本协议不冻结真实留存期限**。本轮无真实 key、登记、恢复、数据库、部署或发布操作。

## 1. 通道、消息与受信上下文

| 通道 | 允许消息 | 身份来自哪里 | 不允许什么 |
|---|---|---|---|
| D0 安装/维护通道 | `d0.install_record`、manifest/current/change 消息 | 已存在的外部资源/部署授权、安装批准与独立维护身份；首次安装器和后续 broker 的入口分离 | 普通 Web/Admin/runtime/DB 身份发来同形 JSON，不获得安装或维护权 |
| 本机控制 IPC | hello/authenticate、case.open、proof、issue/complete/status | broker 取得的真实 OS peer，加当前完整 Admin 会话或已登记 authority 的独立签名；写操作另外取得逐笔证明 | 自报 executor/OS 用户名、case/op、schema-valid 结构不能授权；无公网 generic admin 控制入口 |
| 绑定 TLS 接收桥 | **仅** `receiver.*` 消息 | 精确 Origin/CSRF、登记临时 P-256 key 的 PoP，签发后另须本 flow 的 cap | 不接收本机工具/D0 kind；无 manual complete、control-plane status、普通会话或新恢复码领取 |

broker 以受限本机 Unix socket 或 Windows named pipe 接受工具连接。Linux 从内核 peer credentials 取得主体；Windows 从受 ACL 限定的 pipe 对端 OS token/SID 取得主体，并校验服务端实例身份。路径及 pipe ACL 只准入具名工具使用者；不是靠调用者填写 `OsSubject`。未知、歧义或无法核验的 peer 拒绝。部署维护与恢复执行的 OS/密钥用途分开，普通服务身份不可连维护入口。

一条连接先执行 `identity_control.hello.request`；`authentication_mode` 仅选择验证流程，不授角色。broker 产生随机 challenge，记录真实 peer、连接实例及期限。actor 用 `ActorSessionEvidence.session_token` 经受限 FD 交付，broker 通过固定认证适配器验证其自身当前完整 Admin/MFA 会话、device/epoch、状态和学生恢复专用权限，服务端取得稳定 actor ID。合法 CLI 可以按普通认证契约完整登录取得其自身会话，秘密仅在受限输入/进程内/FD 中传递；不读取用户常用浏览器库，不让网页导出 HttpOnly Cookie，不为 CLI 豁免浏览器 Origin/CSRF。

authority 由 peer 对应的当前预登记 OS 主体和 K_recover 签 hello challenge，broker 核 D0 当前映射与 key/authority 有效性，得到稳定 authority ID；不要求目标 Web MFA。`identity_control.authenticate.response` 的 `auth_context_id` 只是连接内引用，复制到另一连接、peer 或 broker 实例无效；上下文最多存续 120 秒，且后续每次仍重核当前资格。连接认证不替代 issue/reissue/complete 的具体计划证明。签名 challenge/会话验证失败即销毁该连接认证上下文。

响应中的 `ExecutorRef`、`WritePlan.executor` 是服务端回显的绑定，不接受客户端声称某 ID 就授权。请求不含可选择 SQL role/owner 的字段。学生 Admin 的最终身份来自受信 HTTP 再认证验证器及当前数据库，不来自本机 OS 用户名与 Web 角色的字符串拼接。

## 2. 编码、规范化与验签分域

所有消息为 UTF-8 JSON。解析器先拒绝非法 UTF-8、未配对 surrogate、重复 JSON key、非有限数字及超界整数，再按 schema 拒绝未知属性、未知 `kind`、未知版本、无效日期和错误类型。标识使用小写 UUID；版本/epoch 为 JSON 安全整数；时间采用 UTC `Z`、最多微秒精度。Hash 为无前缀 64 位小写十六进制 SHA-256，与 HTTP 的 `candidate_parameters.plan_hash` 一致。base64url 不带 padding，必须重新解码并核对精确字节长度，不能只看正则。

签名输入统一为：`ASCII(domain) || 0x00 || UTF8(JCS(body))`。JCS 指 RFC 8785 规范化；使用成熟实现，不自行通过普通 JSON pretty-print 模拟。hash 输入为同一规范化 body 的 UTF-8 字节；签名本身不纳入 body hash。整数不变成字符串，JSON 不进行 Unicode 规范化；协议字符串必须保留原码点。版本/epoch 越界或不同规范化结果直接拒绝，不试多个编码直到验过。

| domain | body / key | 用途限制 |
|---|---|---|
| `classroom.identity.manifest.v1` | `ManifestBody` / 当前已固定 R0 | 清单及其本人映射；输入中的 root_key 不能替代先前安装 pin |
| `classroom.identity.current.v1` | `CurrentEvidenceBody` / D0 当前受信 R0 | 回应本次新 nonce 的外部账本当前读，不是任意旧签名快照 |
| `classroom.identity.change.v1` | `ChangeBody` / 当前 K_change | 登记、换 key、撤销、受控换根；K_recover 不得批准它 |
| `classroom.identity.key-pop.v1` | deployment、key_purpose、key_id、new_binding_hash、approval_nonce / 对应新 key | 只证明新 key 控制，不证明维护权限或“就是项目用户” |
| `classroom.identity.authenticate.v1` | 完整 `ControlHelloResponse`、broker 从内核取得的 peer 规范主体 / 当前 K_recover | 本机连接身份；不授恢复写入 |
| `classroom.identity.recovery-proof.v1` | 完整 `ChallengeBody` / 当前 K_recover | 该 authority 对本次 case/op/action/计划的明确现场批准 |
| `classroom.identity.receiver.v1` | 下述 ReceiverSignedBody / 本 flow 临时 P-256 私钥 | 接收端 PoP，仅在批准的 receiver/flow 中有效 |

两种组合签名body的准确键名由schema固定：`AuthenticateSignedBody={hello:<完整ControlHelloResponse>,peer:<OsSubject>}`；签名者使用自身规范OS主体和本连接hello，broker必须以当前连接保存的完整hello与内核取得的peer独立重建，不相信请求自报peer。`KeyPopSignedBody={deployment_id,key_purpose,key_id,new_binding_hash,approval_nonce}`；deployment_id取已经核验的外层`D0ChangeRequest.approval.body.deployment_id`，其余四字段逐字取该KeyPossessionProof并与获批变更及new_manifest核对；signature不在body中。两者均只对该准确对象应用上式domain/NUL/JCS，不拼字符串或尝试多个编码。

R0、K_change、K_recover 优先采用 Ed25519 成熟实现，公钥 32 字节、签名 64 字节，格式按 schema；算法或凭据设施不兼容时先另审契约差异，不自动回退。R0 和 K_change 不能与 K_recover 共 key_id/公钥；同一自然人可持分用途凭据，不要求双人或采购硬件/另建 CA。

receiver key 为严格 EC P-256 JWK，仅 `kty/crv/x/y` 四属性；x/y 各解码 32 字节，须验证曲线点有效、非无穷远及所声明曲线。指纹采用 RFC 7638 对这四属性的 JCS UTF-8 做 SHA-256。WebCrypto ECDSA 使用 SHA-256，签名为 P1363 `r || s` 两个 32 字节整数的 64 字节串再 base64url；不接收 DER 或算法协商。验签还要校验 r/s 合法范围；签名可塑性不能绕过按 nonce 与请求摘要进行的原子单次消费。

ReceiverSignedBody 精确为 `{protocol_version:1, action, receiver_id, case_id, flow_generation, server_nonce, request_hash}`；action 为 `await|factor_start|factor_confirm|abandon`。`request_hash` 为完整请求去掉 `receiver_auth.signature` 后的 JCS hash；cap/密码/验证码仅在内存参加摘要，**请求摘要也不进通用日志或审计**，避免保存低熵秘密的离线猜测依据。broker 从绑定记录取得 key，不接受请求另带 key 换接收端。服务端 nonce 每步不同，验证当前 nonce 后锁内消费；仅持 key 不能重放旧 nonce 或跨 action。

## 3. D0 首次授权与维护对象

首次安装使用与恢复命令分离的受控安装器。具名维护者先以外部既存资源归属/部署 IAM 及项目用户经该渠道明确批准的记录，人工核对实际项目用户与 authority/个人 OS 主体/K_recover 的对应关系；保留 installer、外部授权/批准引用、人工核对方式及指纹。它不是应用新 Admin、普通 DB 登录或新 key PoP 的派生权力。

`D0InstallRecord` 由该受信安装过程产生并验证，而不是由其 JSON 内容自证。首次安装固定 deployment、R0、K_change、维护主体、指定项目用户映射、验证器版本、initial manifest/version；写只读部署配置，并在 **应用 PG 外的外部 D0 账本**记初始化完成及最高版本/hash。初始 version 为 1，previous_manifest_hash 为 64 个 `0`；只准首次安装一次。配置或高水位缺失不被解释成“新环境可重新认领”。

安装器必须核 record 与 initial_manifest 的 deployment、项目用户映射引用完全相同，manifest_hash等于初始ManifestBody的JCS hash；authority/key/OS映射须与外部批准逐项相符，不能只比人名文本。任何跨字段不符均不安装，不能由schema-valid替代这次独立核对。

`ManifestBody.authority.enabled=false` 表示更高版本的撤销墓碑，仍保留不可回退的版本/hash。清单签名验证只用已有 pin；body 内 root_key 用于与 pin/已批准 root_rotate 比对，不作为新的验证来源。一个 deployment 在本候选中仅有指定项目用户的一项恢复 authority；维护主体独立，不新增可由普通 Admin 扩展的 owner 名单。

每份 `ChangeBody` 绑定 deployment、authority、动作、old/new manifest hash 与 version、old/new OS/key/person binding hash、approval_nonce、签发/到期、维护者及外部批准引用。`new_version > old_version`；old version/hash 必须匹配当前外部账本、已安装清单与 PG；同版仅同 hash 可读，不能拿同 nonce 改内容。当前 K_change 验签、外部当前维护资格和批准引用均有效后，才可接受变更。所有变化的新 key 另交 key PoP，但不能代替独立批准。

这里三方old状态一致是进入M1前的批准前提。M1已预留后，外部账本保留不可改的old/new完整配对及原批准，当前高水位已是new；M2核的是PG/清单的old与该已批准配对相符，外部仍为同transition的new预留，不要求暂态old PG等于new高水位。普通恢复在该错配期关闭；只有该transition的窄维护路径能前滚。

root_rotate 仍由当前有效 K_change 的批准及 D0 受控维护程序更换 pin，绑定完整 old/new manifest；新根 PoP 只证明控制。普通登记/恢复命令不能传一个新 root 覆盖 pin。根未安装、全失、所有独立授权不可验证，或无法与实际项目用户可靠匹配时关闭；**信任重建不在本协议，须另审**。完整部署/root/验证器被接管超出应用层保证，普通 DB 写权不等于该信任边界。

普通变更的new_manifest用当前R0签；root_rotate的待审载体先用旧R0签同一个新ManifestBody，并提交K_change批准及新R0 PoP。发布时受控安装器才以新R0签同一body并固定新pin；manifest_hash只针对body，签名包装变化不改变已批准的new hash。新pin与新签名须作为同一受控发布对象，M4用新pin核验；若中断则CLOSED并沿原transition继续，不能先信自签新根。批准、载体与新清单的deployment/authority/old-new版本与hash、变化key列表必须逐项一致；revoke的new authority.enabled必须为false，其他变更不得借字段缺失偷偷换人。

## 4. 外部当前高水位与单 broker 变更协议

本候选选择一个受监督的本机 identity-control broker 独占 recovery、status、maintenance SQL 凭据和 D0 当前配置。它是原受限本机控制服务的具体化，不是新增身份平台。Web runtime、CLI、receiver 适配器不持这些 SQL 凭据，不可旁路调用表/函数。部署级 OS 独占锁同时绑定部署 ID、broker 实例 ID 与服务端 IPC 身份；只存在一个活实例，不能以另一个进程/端口/worker 绕锁。

broker 有公平的进程内 RW admission gate，**取得次序先于任何 PG 控制锁**。所有恢复写、receiver 写和 status 取得共享 gate；所有根/config、登记/换 key/撤销、高水位、数据库恢复及服务重启维护走独占 gate。gate 保护的不是客户端 bearer token；handle 从不离开 broker，不能保存后跨重启复用。一个受控身份角色对应的 PG 连接/事务和 gate handle 由同一 broker 实例持有。

### 4.1 启动与普通操作

1. 启动默认 `CLOSED`。先核部署独占锁、安装记录/pin/验证器，确认旧 broker 已终止且其所有 PG 控制连接已结束；不能只根据 PID 名字或 socket 消失猜测。监督者核实例标识、连接注册及实际 backend 终止事实；无法排空则继续 CLOSED。崩溃前 COMMIT 是否成功仍由 op 持久事实核对，不凭进程退出推断。
2. 在无 PG 锁时，以全新随机 request_nonce 在线向外部 D0 做当前账本读取；验当前 pin、deployment、nonce、签发/到期、初始化标记、高水位/version/hash 与 transition 状态。nonce 必须与本次请求相等且本实例未用；旧磁盘证据、旧签名快照、超时或无法读到当前账本均不能开门。
3. 在 PG deployment_guard 核 version/hash/transition，读已安装只读清单。外账本、清单、PG 都 ready 且完全相同，才开启 READY。恢复旧 PG 备份必须先关闭准入/排空旧连接，再按此步骤重新核对；旧 PG 与旧清单共同回放仍不匹配外部最高水位。
4. 普通操作先持共享 gate，在 PG 加锁前向外部 D0 以新 nonce 得到当前证据；核当前外部维护/authority 资格与版本。此时持的是进程准入 gate，不是 PG 行锁。外部调用有界超时，失败关闭该次操作；它不能离线续签或以缓存替代。之后按第 8 节取得 PG deployment_guard 共享锁、必要主体/flow 锁，核证据、清单、PG 的 version/hash/transition 一致及当前撤销事实。
5. 持共享 gate 到 PG 提交/回滚完成后释放。维护只有在所有共享 handle 排空后才能推进外部账本，因此已 admission 的操作与随后变更有明确先后；任何客户端签名/网络/人工输入都在这些 PG 锁之前完成。普通 PG 预算耗尽或操作 deadline 到期安全拒绝，不能持锁等操作者再签。

D0 的本系统变更/撤销入口均必须经该 broker 的独占流程；外部账本写者仅是经受控部署批准的维护程序，不向通用 runtime 开放。无法证明单 broker、受控写者或外部账本当前性时保持 CLOSED，不把带 TTL 的旧签名当“可能仍当前”。平台资源 owner 若完全绕过这些受信程序改根/程序，属于前述完整部署控制边界，不承诺应用仍可防御。

### 4.2 登记/撤销/换根的有序提交

| 步骤 | 持有/提交与持久事实 | 中断后的唯一继续方式 |
|---|---|---|
| M0 关门排空 | 维护请求先验证结构和独立批准；取得独占 admission gate，停止新操作，等待旧 PG 控制事务完成；重新在线核 D0 当前状态及维护资格 | 未接受外部变更前失败可释放；真实路径仍须三方一致才重开 |
| M1 外部预留 | 外部账本原子 CAS old version/hash→接受该批准、预留更高 version/new hash 和 nonce，产生 transition_id；同 nonce 同内容可重读，异内容拒绝。高水位已前进，phase=`transition_reserved` | broker 保持 CLOSED；只允许同批准/transition 前滚，不得退回旧高水位 |
| M2 PG 控制提交 | 无 D0 网络调用；以独立 maintenance 连接先锁 deployment_guard 排他，再必要有序 actor/user、authority、case/op 等；锁后重核当前批准期限/nonce与old状态，应用新 authority/版本/撤销和最小审计，将 transition_id 与 new hash/version 同事务提交 | 这次 PG COMMIT 是**资格变更线性化点**。回包不明先只读核该 transition 提交事实，不能重用批准做另一变更 |
| M3 发布一致配置 | PG 已提交后，D0 受控安装器按原批准发布对应只读清单/新 pin（如 root_rotate），外账本把相同 transition 标为 ready；核安装字节 hash | 仅原 transition 的幂等发布可重复；批准在有效期已由 M1 接受且 M2 合法提交后，发布重试不成为新的权限变更 |
| M4 重开 | 新 nonce 在线读外账本，确认清单/PG/账本新 version/hash/transition 完全一致且 ready；解除独占 gate | 任一错配/当前性不明继续 CLOSED；不回旧清单恢复可用 |

M2 前批准已过期则不应用 PG；已在 M1 接受但尚未合法执行的 transition 保持关闭，须由原独立批准方补交**绑定同 transition/old-new 内容的新期限授权**后继续，不能自己续签，也不能用另一目标/新 key 接管。外部预留不能因期限到而删除高水位。根全失或不能重新证明原授权时，停止并另审重建。

续授权使用新的ChangeBody/approval_nonce和签发/到期，deployment/authority/action/old-new版本与hash及binding hash必须与外部账本中唯一未完成transition完全相同。D0先验证当前独立维护资格和K_change签名，再以该完整配对定位既有transition并原子接受新nonce；它只更新这次前滚的批准引用/期限，不另占版本、不改变原目标，也不把原nonce改内容后复用。broker在无PG锁时核得该接受事实，才重进M2；原批准与续授权均留外部审计。

撤销与 issue/complete 使用同一 PG deployment_guard/authority 锁顺序。恢复先持共享 gate/PG 锁并提交，维护在其后；维护先取得独占 gate 并提交，旧资格后续请求被拒绝。PG 提交后的配置发布非原子期仅拒绝，不声称跨系统瞬时切换。每次资格变更还原子写独立 identity 撤销通知，**不进入业务 counter/Outbox**。

broker 崩溃会失去本机 IPC、gate 与内存 proof/secret 交付材料；监督者保持门关闭，确认旧进程和 PG 连接排空后才重启。不得通过只重建内存 RW 锁就接收新实例。数据库故障、外账本分区、watchdog 或审计故障都不授权旁路、人工 SQL 或保留旧资格继续恢复。

## 5. case、计划和独立 proof

### 5.1 case 与服务端计划

连接当前认证后执行 `recovery.case.open.request`。学生目标须有两类独立、与账号预登记绑定的核验材料：邀请/注册主体映射及独立在籍核实，加预登记独立渠道的现场挑战；具体可信引用由固定核验适配器解析并验证来源/版本/结论，不能自由填一个“已核实”字符串。无来源或不匹配时仅留 unverified_case，不冻结目标、不清 MFA。操作者仅存最小引用/hash，不收正文、密码或证件副本。管理员目标只允许当前 D0 authority 连接，普通 Admin 即使有学生恢复权限仍拒绝。

case 的稳定 executor 由当前连接认证器写入并不可替换；target/recovery_type/证据引用与初始 flow_generation=1 绑定。case.open 不发 cap/seed、不切因子、不给普通会话，也不代表 issue 已获逐笔批准；目标 role/epoch 和核验材料在真正写事务仍重读。无法验证的 target/case 与无权 case 均使用通用拒绝，不形成他人存在性接口。

`recovery.proof.challenge.request` 只提交非秘密 case/op/action 和接收端预期标识；服务端从当前 case、registration、当前 target/权限记录生成 `WritePlan`，不接受客户端填 role/epoch/executor。op_id 是具体本次动作的稳定标识，不是授权；issue/reissue/complete 各用新 op_id。计划 hash 为 WritePlan 的 JCS SHA-256。complete 计划额外固定 pending_factor_id 和确认事实 hash；确认 hash 只涵盖服务端确认 ID/版本/时间等非秘密事实，**不保存验证码或其低熵摘要**。

计划与以下事实绑定：deployment、稳定 executor、authority 当前版本（actor 为 null）、case/op/action、target user/role/唯一 security_epoch、核验记录 hash、receiver/key 指纹、flow_generation；complete 必须含新因子/确认 hash，issue/reissue 必须为 null。receiver_id/key 必须匹配有效登记，reissue 的新 generation=current+1；complete 不改变接收端。写 challenge 取得全部真实验证前有效的独立 challenge_expiry，S1 候选上限 5 分钟；不能靠请求重开清除失败预算。

### 5.2 两类执行者的具体证明

| 执行者 | `ProofVerifyRequest.evidence` | 当前资格与计划匹配 |
|---|---|---|
| 学生恢复执行 Admin | `student_admin_reauth`，`reauth_token` 通过受限 FD | HTTP reauth purpose 精确为 `student_recovery_issue|student_recovery_reissue|student_recovery_complete|student_recovery_status`；target.kind=`recovery_case`、target.id=case_id；candidate_parameters=`{op_id,plan_hash}`。broker 验服务端证明/HMAC和当前自身 session/device/Admin/MFA/epoch、专用权限、case原执行者及完整计划一致，不接受 token 自报角色 |
| 管理员恢复项目用户 | `authority_signature`，K_recover 对完整 ChallengeBody 的独立签名 | 当前 peer、D0 固定实际人/OS/key 映射、authority/key/version、用途和完整计划都匹配；K_change 或新 key PoP 不可替代。不要求已失去的目标 Web MFA |

HTTP token 只作这条窄工具桥，不成为 generic admin 权限；本地 broker 才能调用恢复控制事务。学生 proof 的原实际因子成功时间沿 HTTP 服务端 `verified_at`，broker 的验证完成时间不能把旧因子时间刷新为新认证；最终 deadline 取两者允许的更早界。authority 的 verified_at 为 broker 实际成功完成本次签名、peer、映射与绑定校验的 DB 时间，不采信客户端签名中的时间。

验证成功时必须仍早于 challenge_expiry，写入/返回不可篡改的 proof 绑定及 verified_at/operation_deadline。对 issue/reissue/complete，`operation_deadline <= verified_at+120秒`，并取执行资格、HTTP 原证明、自身会话及既有 pending expiry 的更早值；不能取 last_seen 或挑战创建时间。取得**全部 PG 锁后**再以实际 `clock_timestamp()` 检查 `now < operation_deadline` 和当前资格/scope/flow，等于边界也拒绝。已验证的 challenge 后来过期不再次截断该操作窗；锁等待不能延长窗。

写 proof 存 `identity_operation_proofs`，分别包含 approval_nonce（issue/reissue）或 completion_nonce（complete），单次消费。服务器验证阶段建立不可变的 reserved op 事实，固定 executor/case/action/plan_hash；这只是待执行意图，**不是已提交的恢复效果**。proof 过期/新 flow/撤权不让旧 reserved op 自动执行。重复 proof.verify 对已用 challenge 不再产生第二个写 proof；失败/已用按通用拒绝，不重签或更新 verified_at。

## 6. issue、接收桥与手动 complete

### 6.1 独立临时接收端

浏览器先在绑定 TLS 接收桥发 `receiver.register.request`，生成本机临时 P-256 私钥，只交公钥和 client_nonce。服务端以独立受限登记保存 public JWK、RFC7638 hash、server_nonce 摘要、registration_expiry 与数量预算；注册只是防护材料，不授 case/target 权限。初始登记等待期采用 S1 challenge 5 分钟候选，pending 总期采用 10 分钟候选；两者独立且不能续写 proof。普通 preauth 创建预算/Origin/CSRF 仍适用，注册不清其他失败预算。

项目用户/学生核验渠道独立核对完整 key 指纹后，操作者将 receiver_id 纳入计划。浏览器取得非秘密 case/预期 flow 标识，发送签名 `receiver.await.request`；broker 验 nonce/key/case/预期 generation 并把该条 TLS 连接记录为仅供本次计划的接收通道。等待连接不持 PG 行锁；未签发前没有 cap，也不能读 case。断线或换接收端不能用旧 nonce 猜取秘密，必须重新登记/批准对应计划。

### 6.2 issue/reissue 的事务及一次交付

`recovery.issue.request` 引用新 proof_id/case_id/op_id。broker 取得共享 admission gate、当前 D0 证据及第 8 节全锁，重读当前 actor/authority、target role/epoch、核验证据、receiver/flow、challenge已验证事实、proof期限/单次性。actor 只能恢复 Student，authority 才能恢复 Admin；请求名称/角色声明不改变它。

首次 issue 在同事务单次消费 approval_nonce、创建当前 cap 的用途分域摘要、固定 flow/pending expiry、标 op committed，并原子写最小审计；旧因子/旧码/旧会话不动，不签普通 session。cap 为至少 256-bit 随机秘密，PG 只存其用途分域摘要。pending expiry 从首次 issue 的提交时间固定，S1 候选 10 分钟，且不晚于当前批准配置给出的其他更早期限。

reissue 是 `WritePlan.action=reissue` 的同一消息族，须原执行者 fresh 证明、新 op/nonce、新 receiver 计划及 generation+1；先独立 status 核当前事实，写锁内仍重新读取。它在一次提交中废旧 cap、旧 receiver flow、未激活新因子及未用 proof，再建立新 cap/generation并记 op；**不延长首次 pending 总截止**。case 已 completed/expired/abandoned 或当前资格失效则拒绝，不把旧 issue proof 重新消费。

cap 原文只存在 broker 本次内存；提交后直接通过已绑定、验过 receiver PoP 的 TLS 等待连接发送 `receiver.delivery`，随后清除。`recovery.issue.response` 到操作者只含非秘密 case/op/flow/pending/audit/committed，**没有 cap 或 seed**。交付前后断连不回放秘密：已提交则保留 pending，原执行者先 status，必要时 fresh reissue；未提交则保持原状态。不得为了重传把 cap 保存到通用 Receipt、Outbox、日志、磁盘队列或 CLI 输出。

### 6.3 新因子证明与浏览器权限

浏览器凭本 flow cap 加 receiver PoP 发 `receiver.factor.start.request`。Student 必须在该受限 TLS 通道验证当前目标密码；忘记密码走不清 MFA 的普通邮件重置，恢复操作者不能设置或读取密码。Admin 人工恢复不要求目标旧 Web MFA，target_password 必须为 null。实际 target role 只取 case/数据库，不根据客户端 null/字符串决定。

密码哈希/KEK准备在 PG 长锁外；写事务重核当前密码版本/hash、target epoch、父批准/issuer 当前权限、cap/flow/receiver/期限，生成并保存加密 pending seed，返回一次 `ReceiverFactorStartResponse` 到原绑定浏览器。密码验证不是新设密码入口，不套最低6/12新设门槛；无有效名单也不阻止已有正确密码验证。seed 响应丢失不回放原文；保持旧因子和受限 pending，先由原执行者核事实后另行 fresh reissue。

`receiver.factor.confirm.request` 绑定 pending_factor_id、当前 nonce、cap/flow/key，并验证新 TOTP。锁内单次消费该新凭据时间步/nonce，记录不可变确认事实，形成 pending_confirmed；失败不切旧因子、不生成普通会话。各步 nonce 原子消费后发新的 server_nonce；旧 nonce 即使签名正确也不能再执行或换 action。合法同响应秘密已签发但丢失，仅回非秘密 secret_already_issued/拒绝，不重新泄露秘密。

receiver 可凭当前 cap/nonce/PoP 显式 abandon 本 flow，终态 abandoned 后失效其 cap及pending因子；它不能批准新 receiver、重签能力、manual final 或访问 control-plane status。操作者撤权、authority/key/version变化、target epoch变化、flow过期或重签均使旧 receiver 派生授权无效；后续 receiver 事务沿相同锁序重核父资格，不仅靠通知。

### 6.4 fresh manual complete 与终态

新因子确认后，**原 case 执行者手动**生成 complete challenge，重新证明当前资格及新 action/plan，取得不同的 completion_nonce 与至多120秒证明；原 issue nonce 已消费或其原 challenge/证明过期不阻止该合法 fresh complete。浏览器/其他 Admin/后台不能代 complete。

完整锁内核 target role/epoch、父 case/flow/receiver、新因子/确认 hash、当前 cap未消费、完整执行者资格及 proof期限。最终同事务：消费 completion_nonce和当前cap，激活新因子并废旧因子/旧恢复码组，递增唯一security_epoch，撤销旧 session/device派生challenge/reauth/WS/未来解锁资格，标op committed/case completed，写最小审计和独立identity内部通知。任一 PG/KEK/必需审计失败均全回滚，不能先废旧因子后丢审计。

最终命令不创建新恢复码、不返回秘密、不签登录会话；N02 由完整密码＋新 MFA 重登后独立显式生成/轮换新组，HTTP正式协议另列。确认 TOTP 的时间步已消费，完整登录需新的未消费时间步，不能为体验回放旧码。final 响应丢失先独立 status，不再执行旧 proof；两个 complete 在同 case/op/cap锁上至多一个提交、一次epoch提升。

## 7. 结果不明后的独立 status

status 先经当前连接认证，再以 action=status 的独立 challenge/计划取得当前执行者只读证明。StatusPlan 仅含 deployment、executor、case/op/action，**不返回 target身份/角色/epoch、因子或接收端细节**；学生 Admin 的 HTTP bridge purpose=`student_recovery_status`，authority仍用当前独立运维资格。目标已升epoch、cap已耗、旧浏览器失效不影响这条路径；执行者角色/专用权/session/device/authority/key撤销仍拒绝。

status proof 与写 proof 共用消息/验证器形状，但仅存 broker 内存、绑定当前连接/peer/稳定executor/case/op，`single_use=false`、有效期至多120秒且取当前资格更早值。**不 INSERT/UPDATE 恢复proof、case/op、nonce/cap或因子表**，不为未知case制造数据库记录；合法有效proof可重复读。broker重启后需重新认证，不从历史批准恢复它。

查询从受信上下文取得executor，按 stored_executor_type/id 与 case/op复合关系筛选；不接收一个“原执行者ID”就授权。先持共享admission/D0与PG deployment_guard，随后当前执行者自身所需user/device/session、authority/permission、本人case/op共享锁。它是**业务只读**：PG固定投影函数为取得行锁可能需要窄UPDATE列权限，但调用角色无恢复DML能力，不使用与行锁不兼容的 `SET TRANSACTION READ ONLY`。函数无动态SQL、固定search_path，返回前同事务再核当前资格/证明期限，status不锁target因子/业务counter或按target旧epoch鉴权。

status不touch自身session/device活动或延长期限；当前资格过期就重新认证，不让反复查询维持执行权。独立鉴权失败计数/准入日志按其受审边界分列，不混入恢复proof或nonce消费。

| result | 必须存在的持久/线性化依据 | 最小输出与后续 |
|---|---|---|
| committed | 本executor的指定op与恢复效果在同事务写入committed及审计，case状态一致 | 必要当前stage、该op/audit非秘密标识、as_of；cap消费/target升epoch不破坏读取 |
| not_committed | 已有不可变reserved op属于该executor/case/action/plan；在共享case/op锁的读取线性化点没有提交效果，case仍pending或pending_confirmed且总期有效；正在写同case的事务已被此锁序串行化 | 返回必要pending阶段及as_of。释放锁后可能有新complete，**不是允许重做的授权** |
| unknown | 缺case/op、清理后无充分事实、无可授权case、PG故障/并发事实无法确定或证据不足 | stage/audit_id为null；不把查不到当失败，不透露他人case是否存在。已认证但错case与未知case采用同样结果形状 |

当前凭据/权限验证失败先通用denied，不能依据case是否存在细分。committed/not_committed须stage非null；unknown的stage/audit_id必须null；not_committed不回final审计ID。连接/PG断开无法给可靠响应时保持结果不明，不伪造not_committed。上述跨字段与持久事实由服务端落实，schema不能替代它。

status不消费恢复nonce/cap、不重签、不切因子、不finalize、不生成通知或秘密；最小独立鉴权/限流访问日志不算恢复业务副作用，也不含请求秘密。后续重签/再执行必须另取fresh、单次、动作绑定写proof并在锁内重读当前case/op；completed拒绝再次切换。浏览器没有该控制面status权限；如需终态显示，另审完整重登owner绑定的最小协议，不复用旧cap。

## 8. 锁、权限与最小持久对象

逻辑对象与精确ACL/完整锁图须和S2数据文档共同审：deployment_guard、authority/permission、case/op/proof、receiver/cap、pending factor、最小control审计及独立identity通知。协议公开 `executor_type/id`，持久层可用actor_id/authority_id XOR和复合FK保证类型与归属，不能只靠普通多态字符串。Hash在wire为hex，PG可存32-byte bytea；不新增第二个auth_epoch。

统一基本序：**broker admission gate → PG deployment_guard → 按UUID一次取全必要users → device → session → authority/permission → case/op → receiver → proof/challenge/cap → factor → recovery-code group → 最小审计/identity内部通知**。同类按稳定ID排序，待写行首次即所需排他强度；不先SHARE后升级，也不在后序补低序user。普通status只取其自身必要认证行和本人case/op共享锁，不取target。authority独立身份不虚构Web user；仅在实际target变更时锁target user。

receiver事务也取得当前issuer必要认证行、target及case/receiver/cap等有序锁；RLS/FK/唯一约束的隐式锁必须在完整图出现。恢复行复合FK不得在已持case后才隐式补一个先前未取得的user锁；写审计/通知不能重入业务counter、Outbox、Task。领取identity内部事件先锁event并提交释放，再另事务查当前授权/关闭连接，禁止持event回取user。通知只加速撤销，PG当前事实兜底。

各role仅broker持有：recovery只能固定issue/complete/receiver函数，status只能固定最小投影，maintenance只能受审登记/版本转换；均非owner/superuser/BYPASSRLS/CREATEROLE，无generic UPDATE/DDL/正文读取，不继承scheduler/migrator。status definer可持取得共享锁必要的窄列权限，其RLS/DML策略仍拒实际写入。普通HTTP无法通过附带actor/owner参数取得这些角色。现0001–0006及bootstrap-only限制不改；无人工SQL恢复权。

本协议固定需要验证的机制，而非现在创建表/角色：同nonce双写、两complete、撤权两顺序、status读写/清理交错、D0三阶段中断/备份回滚、N01隐式FK锁环及claim/delivery路径。原A19/A25/A28/A31–A46与19条状态推演继续有效；S1审批的预算/超时及普通与专项区别保留，不因本协议把240秒测试watchdog变成写proof期限。

## 9. 错误、秘密响应与兼容边界

`ControlError` 只含稳定code、retryable及非秘密correlation_id，不带target/case存在性、key材料、签名、TOTP或SQL参数。invalid_request/denied/expired/conflict/unavailable/result_unknown/secret_already_issued为本独立协议错误；不是悄悄新增公开HTTP error枚举。`retryable=true`只表示可重新取得事实/认证，不授权回放写proof或自动重签。COMMIT不明必须先status；不能因CLI超时把case写failed。

秘密含session/reauth token、cap、seed、TOTP/密码、私钥/PIN；不进argv/env/history/stdout/stderr/trace/APM/录制/core dump、Receipt/Outbox或通用审计。工具原始协议输入经受限FD/本机IPC，输出renderer只允许非秘密case/op/flow/阶段与必须提示；签名/认证材料也不作普通诊断输出。接收桥秘密只到批准key绑定的TLS浏览器，no-store；HTTP/proxy关闭body日志，错误写盘前脱敏。FD/继承/关闭、禁录制/转储、异常/断连须实测，不能仅约定不打印。

schema所有对象strict、required逐项列明，root按kind唯一分支；旧工具遇未知kind/版本/字段/状态必须关闭，不能默认authenticated。控制协议v1独立于HTTP包0.2.0；部署升级必须同时核broker、CLI、receiver和D0验证器版本/fixture，停旧签发与写入后保留已提交case/op/审计和反回滚墓碑。回退不清因子、不恢复旧cap/epoch、不降schema或绕MFA。

目前无真实平台、根、人/学校资料登记；实际配置/算法设施不满足时关闭相应路径。U02批准真实留存前不冻结生产90/30天；过期清理只减少查询证据，缺证据返回unknown，不清D0高水位或重新授权。PR03a P3-01、PR02b两项P3及其他TBD/P3原关口不变，认证控制面不改持久任务/unknown/Provider发送COMMIT不变量。

## 10. 审查与停止点

S2审查须同时核schema正反例、上述跨字段不变量、HTTP再认证桥、D0 gate/高水位、status业务只读与实际行锁权限、完整显式/隐式锁图、N01及N02秘密交付边界；验收编号或JSON解析通过不表示机制已执行。合成fixture可用合成ID/key/签名占位的结构值，不能称它们是实际项目用户或验签成绩。

本轮仅创建协议/schema候选，不登记、生成真实key、运行恢复/认证/PG/HTTPS/WS/Argon2/机制实验，不改业务实现、原测试或旧证据，不commit、Push、合并或部署。S2获审后仍须另获明确S3实施/实验范围；真实登记、演练、恢复启用及开放各按原门禁处理。
