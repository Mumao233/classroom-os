# PR-03b 会话、恢复、权限与锁序草案

日期：2026-11-05（Asia/Taipei）。状态：**设计提案，未实施，待产品政策及 Astra 契约/安全审查**。沿用已批准应用服务端认证架构，不新增身份平台。政策数值以[决策表](POLICY-DECISIONS.md)为待确认输入；验证要求见[攻击矩阵与门禁](TEST-EXPERIMENT-GATES.md)。

## 1. 当前接口与唯一失效版本

实际 [Principal](../../../web/backend/classroom_web/domain/identity.py) 包含 `user_id/role/security_epoch/session_id/device_id/mfa_level/reauthenticated_at/expires_at`；其构造器只检查内部值的格式，不验证 Cookie、数据库 session 或 MFA。实际 [authorized_transaction](../../../web/backend/classroom_web/infrastructure/database.py) 检查 Principal 类型、到期、当前用户身份/角色/epoch，并通过内部 UoW 持有用户共享锁；**没有 auth_sessions/devices 的实时校验**。[identity 管理入口](../../../web/backend/classroom_web/infrastructure/identity_repository.py) 校验内部 `mfa_level == totp`、用户/epoch及再认证时间，同样不是 HTTP 认证器。

架构中的 `auth_epoch`、数据库 `users.security_epoch`、Principal 的 `security_epoch` 是**同一个账户安全失效版本**。后续 session/challenge/email action/ticket 保存该版本快照，全部同当前 users 比较；不新增 `auth_epoch` 列、双计数器或不同步的第二套失效版本。前端请求控制器的本地 epoch 只取消旧请求，不是认证证据。这里建立说明映射；本轮不改冻结架构文字或正式类型。

HTTP 普通请求不得接收反序列化 Principal、owner UUID、任意角色/MFA/认证时间，也不得在认证失败后转用 `transaction(OwnerContext(...))`。内部 worker、测试和 LocalReconciler 仍有各自受限端口；不能将该通路暴露成 HTTP fallback。

## 2. 不透明会话及时间

服务端用成熟安全随机源生成不可预测的 session token（提案：至少 256 bit 随机熵）；Cookie 只含不透明 token，不含可自证身份的 JSON/JWT。数据库用带用途分域的安全摘要定位；随机凭证可用 SHA-256，不把低熵密码的普通摘要用于幂等指纹。数据库 session ID、device ID 与 token 分离。秘密从请求到错误/APM全链路脱敏。

冻结 Cookie 属性为 `__Host-classroom_session; Secure; HttpOnly; Path=/; SameSite=Lax`，不设置 Domain。只经 HTTPS；删除 Cookie 使用相同 Path/属性。不得在 localStorage/URL/可公开 DTO 保存长期凭据。登录成功创建未信任 device；device ID 是标签，不能单独登录或冒充设备。学习缓存 trust 与本次采集 consent 保持独立，本轮不实现私人持久缓存。

服务器按数据库时钟判断 `now < idle_expires_at` 且 `now < absolute_expires_at`；等于边界即拒绝。学生绝对期限为首次完整建立逻辑会话起 30 天，空闲期限为 14 天，活动更新为 `min(now + 14天, absolute_expires_at)`。合法业务请求集合、更新节流方式须审查；轮询、WS ping、失败请求不自动算活跃。管理员采用待批准的更短期限，**15分钟/8小时仍是候选**。

登录、MFA完成、权限提升、敏感再认证及必要安全状态变更轮换 token/CSRF；原 token 在同一提交失效。轮换保持逻辑会话原绝对到期时间；只有完整重新登录才能建立新逻辑会话，不能靠滑动或定期轮换续绝对期限。并发轮换仅一个成功，旧 token 不设可执行写入的宽限窗口。响应丢失按冻结 reauthorize-and-reissue 重新证明并重签，不能从 Receipt 回放旧 Cookie。`remember_me` 只决定 Cookie 是否跨关闭保留的推荐方案及备选见决策表，不能自行改变已决定的服务器 14/30 天期限。

## 3. preauth、CSRF与 Principal 生成

登录前 `GET /auth/csrf` 建立短效预认证上下文及 CSRF，不创建登录用户、设备信任或业务授权。预认证 Cookie 与登录 Cookie 分开，使用 Secure/HttpOnly/host-only 属性并存凭证摘要；CSRF token 绑定该上下文，返回 no-store。GET仅创建协议防护材料，不触发注册、邮件或领域写入。preauth 有限期/数量/清理及 Origin 规则需实验，防匿名存储耗尽。

公开认证写操作先检查精确 Origin（scheme/host/port）、CSRF、Fetch Metadata、Content-Type、大小及廉价多维准入，再进行昂贵密码验证。缺失或 `null` Origin 的支持规则须明列；浏览器认证默认安全拒绝，不靠模糊后缀匹配域名。可信代理名单固定，不能信任任意 X-Forwarded-*。CORS凭据不允许通配 Origin。跨进程限流用原子共享状态，账号、IP/NAT、challenge、跨入口和全局预算组合，不因换 challenge 清空失败预算；匿名失败不得永久冻结管理员。

已登录写请求同时检查 Cookie、会话绑定 CSRF 和 Origin；SameSite 不替代 CSRF。登录后新 token 对应新 CSRF；可按架构用 token 与服务端密钥做 HMAC派生，DB仅存校验值。所有私有响应 no-store，GET不得借重放触发业务副作用。

认证链：摘要最小定位 → 受限认证适配器取密码/因子材料并验证 → 事务重新核对用户、session、device、security_epoch和到期 → 服务端生成 Principal → 资源 owner/墓碑/血缘及路由能力检查。未知账号走受同一准入预算约束的 dummy hash并给通用失败，pending/disabled不靠错误差异枚举。密码校验在长事务外；提交前重新比较已校验的密码版本/hash和epoch，防重置或禁用竞争。

MFA challenge 不是完整登录：不得返回业务 user/device/CSRF上下文或访问课堂。管理员密码正确但未完成 TOTP、学生已启用 MFA但未完成因子，均不签发完整会话。`ReauthRequest.mfa_code` 可选只是 wire 结构；服务端依据实际因子状态/角色要求，缺少必要第二因素即拒绝，不能采信请求的“未启用MFA”。

## 4. 实时撤销与业务事务

每次 HTTP 请求及 WS消息重新校验账户状态、验证材料、role、security_epoch、session未撤销/两类期限、device未撤销及所需因子等级。DB故障拒绝新私有服务授权和敏感/收费动作；不退回旧内存 Principal、签名 Cookie或OwnerContext。连接关闭/缓存通知是加速手段，PG事实是授权依据。

认证入口检查与业务写之间不能留下撤销空窗。提案是在同一短业务事务内、沿现有用户共享锁边界，再持有 device/session 锁并检查实时状态直到 COMMIT。纯校验取共享锁；本事务会刷新活动/空闲期限时，对需要更新的device/session从第一次加锁即取排他锁，避免共享锁升级互等。撤销取对应排他锁；若撤销先提交，后续旧授权不能写入；若业务先持锁，撤销等待其提交。路由不能把进事务前的 Boolean 当最终授权。

当前会话退出撤销该 session并清Cookie，不能要求额外再认证才能退出；设备撤销覆盖其 session和派生授权；全部退出、密码重置、账户禁用/注销及因子安全变更按审定策略撤全会话，账户级变更递增唯一 security_epoch。再认证证明、未完成challenge、受限恢复能力、WS ticket和未来缓存解锁资格同时受epoch/父session/device约束，不仅发一条关闭通知。

**会话撤销不等于抹除持久业务意图**。普通退出不改用户active状态、不删除 tasks/raw/Receipt/Outbox；密码重置使旧认证能力失效，不自动把既有 unknown 当未发送。内部任务继续服从原账户/资源授权、intent和fence规则。未来需要“撤销尚未发送用户意图”时须明确单独业务动作，不能由清Cookie隐式重排或重发Provider。

## 5. TOTP、恢复和敏感变更

采用成熟 TOTP 组件，seed用版本化KEK加密；解密只在受限验证内存路径。KEK不可用不允许绕过MFA或保存明文seed。pending新因子有期限，需正确验证码确认；确认前旧因子继续有效。凭据行保存已消费时间步，在事务锁内比较并推进，登录/再认证/恢复间不可并发重复接受同一凭据时间步。30秒/±1步仍是候选，要测时钟漂移、过去步和未来步推进行为。

恢复码高熵、只展示一次、只存用途分域摘要；单码/整组撤销与消费在事务内原子完成。密码＋恢复码的推荐结果是**仅可绑定新MFA的受限恢复上下文**，管理员不能由此得到运营读写，学生也不能读取课堂、导出或其他私人学习数据。该上下文不可当普通 Principal；当前 wire没有对应结果状态，须先独立契约审查，不能把 `authenticated` 填给前端后靠隐藏标记限制。

恢复完成顺序：验证有效恢复证明 → 原子消费 → 受限能力 → 新因子确认 → 原子替换因子/轮换整组恢复码/撤销旧授权并审计 → 要求密码＋新因子完整登录。种子、恢复码、Cookie、ticket不进入通用 Receipt、Outbox、日志/APM。恢复码响应丢失只给非秘密状态或冻结 `409 SECRET_ALREADY_ISSUED`，近期再认证后显式轮换整组；不保存一份可重放明文。所有秘密响应 `Cache-Control: no-store`。

邮箱密码恢复：通用受理响应 → 本地邮件Adapter签发单次用途/epoch绑定重置令牌 → 设置新密码、消费令牌、递增epoch并全撤 → 重新登录。**不清MFA、不自动登录**。改密码须近期证明；改邮箱先验证新邮箱，成功事务前不更改登录身份，重发/旧邮箱冲突/并发重置不允许部分成功。邮件持久意图只存引用/代次/lease，发送前在内存生成秘密，外部发送不持PG锁；故障重发必须旋转旧令牌，复用现有安全模式而非通用Outbox保存秘密。

再认证证明绑定 user/session/device/epoch、已验证因子及实际验证时间/允许范围；窗口取路由政策、证明到期及会话到期的最早值。不使用last_seen当认证时间。提案高风险逐笔证明再绑定 operation/目标/参数指纹并单次消费；需要新增wire字段或状态时先走契约审查，不能私加头/字段。管理员敏感操作必须密码＋现有MFA；恢复码不能替代普通管理写入因子。

全部因子丢失的人工流程、核验者/批准者/执行者、证据和唯一管理员处理以决策表待批准项为准。不收密码、seed或课堂正文作核验材料；证据原件不进通用审计，仅存受控批准单引用、执行人和时间。匿名申请不能冻结目标管理员。没有可信批准链时不提供恢复写入口。首个管理员仅受控运维初始化，禁止公开网页bootstrap、隐藏超级账号、数据库手改绕过及 `skip_mfa`；不因本轮准备创建或操作真实管理员。

## 6. WS与事件恢复

仅在有效Cookie＋CSRF＋Origin的 `POST /ws-tickets` 签发短效单次ticket，绑定user/session/device/epoch、channel及必要课堂/采集epoch/fence；只存摘要。ticket不进URL、subprotocol凭据或日志；subprotocol固定 `classroom.v1`。实际upgrade同时检查精确Origin和有效Cookie，不能只凭ticket绕过会话。

upgrade后第一条JSON必须为 `authenticate`，在候选握手期限内事务消费ticket并重核父会话与scope。过期/重放/错Cookie/错通道/错课堂/错fence一律拒绝；认证前不给订阅、资源元数据、音频处理或无界缓冲。控制连接数量、首消息大小和认证前buffer，超时关闭；ticket寿命和握手超时通过实验确定，不把5分钟challenge误用作ticket期限。

每条命令、新收费帧及敏感事件发送前复核DB撤销和资源有效性；撤销通知丢失仍有效。事件按既有owner cursor重放，恢复每次重新鉴权并查墓碑；cursor不当权限凭证。超出窗口显式resync_required，不清本地未同步意图，不补发Provider。沿冻结关闭码 `4401/4403/4409/4429/1013` 处理，不增加私有枚举。后续采集/ASR能力仍由所属PR实施；03b只交认证边界和合成连接机制证据。

## 7. 必要增量数据与角色

实际迁移仅到0006；现有身份表是 users、invitations、pending_registrations、identity_capacity/mail_intents/audit/events，**auth_sessions/devices/mfa等尚无实现**。完整逻辑模型在[domain-model](../architecture/contracts/domain-model.json)，本表只提议03b按依赖加入必要子集，字段细化须审查后再迁移。

| 递增子集 | 必要对象 | 审查重点 |
|---|---|---|
| 会话基本层 | devices、auth_sessions、auth_challenges的preauth/login_mfa/reauth、preauth_receipts及限流必要最小状态 | owner复合FK、摘要唯一、期限/撤销/epoch、空scope拒绝；不建cache_keys/consent采集表 |
| 因子和变更层 | mfa_credentials、mfa_recovery_codes、email_actions；复用/增量扩展邮件意图与审计 | KEK版本、pending/confirmed、消费唯一性、TOTP last-step、邮件用途和重发代次；受限恢复scope/status要审契约及约束 |
| WS认证层 | auth_challenges启用ws_ticket用途 | session/device/epoch及scope绑定；不顺带建媒体/ASR/计费表 |
| 运维恢复最小层 | 批准记录引用、单次恢复能力、执行审计所需增量 | 仅在恢复政策批准后加入；不实现运营后台或全量逻辑模型 |

迁移继续现有Alembic单链、expand-contract，实际revision名待实施时分配，不重写0001–0006。空库、0006升级、重复升级、失败回滚、旧代码兼容、受限角色直接SQL负例分别验证。代码回退不drop身份/审计/业务账本；需要停用时关闭新签发及管理写，不降低MFA。

认证引导角色只可执行固定search_path、无动态SQL、撤销PUBLIC权限的最小定位/校验函数；未知owner token查询只返回定位所需元数据。密码材料只到专用验证适配器，不向普通runtime/HTTP返回。认证运行角色非owner、非superuser、无BYPASSRLS/CREATEROLE，不继承scheduler/dispatcher/迁移身份；definer使用窄表/列权限和窄RLS，不能通过可选owner参数读取他人数据。

owner业务仍走受限runtime＋SET LOCAL＋FORCE RLS；identity控制平面保留独立角色。MFA材料及批准记录不由通用owner repository任意写；精确GRANT/REVOKE、函数owner、默认权限和升级撤权清单在各增量迁移审查。**0006 bootstrap-only范围不变**，新增auth角色不解决托管PG的非超级用户provisioning；部署平台/迁移身份选择前处理既有P3-01。

## 8. 并发锁序提案与现有路径衔接

以下是03b待审的锁序约束，不声称已经实现，也不重排无关模块。先只读最小定位不得持锁跨入后续事务；哈希/TOTP解密准备、邮件和网络I/O在锁外。准入计数短事务先完成并释放，不能拿限流锁等待身份事务。

1. 按操作需要先取既有global control/budget；仅注册/激活/容量或未来预算操作使用。不能一律先“通用认证持锁”再调用 `set_capacity`，因为其现有顺序为 control → admin用户锁。
2. 存在的user行按UUID排序锁定。普通业务沿现有FOR SHARE；会改变密码/因子/epoch/安全授权的事务从开始即FOR UPDATE，避免共享锁升级死锁。涉及actor/subject时一次取得全套有序user锁，禁止事后补低序user锁。注册激活继续原control → invitation → pending/proof → 新user插入的独立路径，不在持有认证锁后重入激活。
3. 设备 → 会话，各集合按ID排序；不更新活动的只读授权FOR SHARE，撤销/轮换以及会更新last_seen/idle的请求，对待写行首次即FOR UPDATE，不能先SHARE后升级。账户级撤销先锁user再按同序更新设备/会话；所有入口不能反向 session → user。敏感事务持这些锁到业务COMMIT，锁等待后重核状态与数据库时钟；活动刷新不能复活已撤销/已到期会话。并发刷新、刷新与撤销须显式竞争测试；若采用独立短touch事务，须另审其条件更新和业务授权一致性。
4. auth challenge/email action → MFA credential → recovery code，按种类和ID稳定排序；需要消费/推进的行FOR UPDATE。持session锁前不能先锁恢复码；流程初步定位仅只读。会话创建前无session行时在已锁user/device下插入，因子成功才发布完整会话。
5. 进入已授权领域命令后保留既有归属/来源祖先稳定排序 → 资源/plan → task → attempt编号/结果ID → owner event counter（最后）；既有Receipt幂等锁在原命令位置，新增认证事务不反向获取它，秘密签发走专用最小状态。无后续身份锁从领域内回取。

每个实际用例须列出完整调用图（特别是identity.set_capacity、邀请、激活、device revoke、reset、WS ticket consume及task事务），证明不跨连接用“先验认证”替代同事务检查。若复用边界需要窄验证函数/授权port调整，作为03b明确最小差异交Astra，不能假称已有authorized_transaction已覆盖。真实PG用屏障控制两个顺序、lock timeout/deadlock/回滚故障；数据库事务重试必须有界且只重试数据库部分，不重复外部发送。

## 9. PR-03a/02b不变量与故障回退

保留失效身份fallback：具有登录身份（normalized_email非空）但缺验证/密码材料的账户拒绝普通owner与Principal路径，未发送任务可取消，已发送任务保unknown。历史无邮箱opaque owner shell只保留内部provisioning/worker兼容边界，principal_valid始终拒绝，永不授HTTP登录。LocalReconciler仅通过固定状态函数本地核验已落库raw，不返回正文、不授登录权限。[原实现](../../../web/backend/classroom_web/infrastructure/local_reconciler.py)与[PR-03a支持边界](../pr03a/ACCEPTANCE.md)均继续有效。

保留raw、Receipt、Outbox、fencing和may_have_charged/unknown风险；退出、重置、恢复及会话失效不得删除证据、把unknown改成未收费、触发Provider重发/切换/退款。发送授权线性化点仍为mark_submitting事务成功COMMIT：撤权先完成则拒绝；发送意图先持锁并提交则单次已授权调用可能稍后才实际进入Provider，不能承诺撤权返回后绝无物理网络调用。之后保存/发布继续按原授权检查，不重新暴露已撤正文。

PG、KEK或必须审计失败时安全变更原子回滚，不能先改因子/密码再静默丢审计；不可用时停止相应签发/管理写，保已有账本。恢复码或Cookie响应丢失不回放秘密。服务回退不提供skip_mfa或OwnerContext绕行，不启动真实Provider、私人缓存、采集/ASR、运营后台或真实收费。开放P3的具体触发判断见[范围与依赖](README.md)。
