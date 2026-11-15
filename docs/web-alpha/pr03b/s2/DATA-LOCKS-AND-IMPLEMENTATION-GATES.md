# PR-03b S2：身份数据、权限、锁序与实现门禁

日期：2026-11-06（America/Los_Angeles）。基线 HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`；本次在既有获审 S1 工作区上编写正式契约差异，未创建新提交。S1 的 APPROVE 见本树 `outputs/pr03b/s1-remediation-review-20261106-01/REVIEW.md`；D01–D03 保持 CLOSED（设计层）。**本文件和契约包 0.2.0 是待独立审查的 S2 提案，不是 S3 实施/机制通过或真实启用批准。**

数据源为 [domain-model.json](../../architecture/contracts/domain-model.json)；工具边界为 [identity-control.schema.json](../../architecture/contracts/identity-control.schema.json)，HTTP 为 [OpenAPI](../../architecture/contracts/openapi.json)。模型 `schema_version=1.0.0` 表示文档格式，新增 `contract_version=0.2.0` 表示本契约包，均不是 Alembic revision。已有60表的非身份对象保持原顺序/同值，只补必要身份子集；不创建全部架构模型、不迁移任何数据。

## 1. 最小对象与约束

`users.security_epoch` 是唯一账户安全失效版本；架构中的 auth_epoch 仅是映射名称，禁止新增第二列/计数器。所有日期为 UTC timestamptz，实际判断使用数据库当前时刻，等于期限即拒绝。协议的64位小写十六进制hash对应DB的32-byte bytea；用途分域和规范序列化按控制协议，低熵密码的幂等指纹使用服务端HMAC，不能存可离线猜测的普通摘要。

现有未实现身份对象继续逻辑位于 `public`，不把旧表搬到新schema。新增窄控制对象逻辑放 `identity_control`；这只是精确迁移的审查输入，**本轮无 CREATE/ALTER TABLE、角色创建或SQL执行**。所有FK默认RESTRICT，不用CASCADE清理。owner私有对象以 `(owner_id,id)` 为唯一目标和复合FK；控制面跨actor/subject使用显式typed FK、deployment/case复合绑定和受限函数，不把请求自报owner当授权。

| 对象 | 本版必要字段/约束 | 原子性与授权依据 |
|---|---|---|
| users / devices | 既有epoch及owner设备；不新增auth_epoch。设备仅补last_seen，不自动信任学习缓存 | device ID/label/token不是登录证明；恢复不能授cache_keys/采集同意 |
| auth_sessions | token/CSRF摘要；owner/device复合FK；logical_session_id/established_at、MFA等级、idle/absolute、epoch/revoked | 轮换保持原逻辑绝对期限；首次完整登录才建立新逻辑会话。学生14d/30d、Admin15min/8h；remember_me仅学生Cookie持久性 |
| auth_challenges / preauth_receipts | preauth/login_mfa/reauth/WS等用途；owner/session/device/epoch条件非空；scope有界、摘要唯一、期限/消费/失败数 | preauth无owner且无业务权；MFA challenge不是完整登录。reauth challenge与执行proof分开。现有receipt不保存秘密响应 |
| reauth_proofs | owner/session/device/epoch、purpose/target/参数hash、实际verified_at、deadline、R等级、single_use/consumed/revoked | R1学生600s/Admin300s、R2 300s、R3 120s；取父资格最早截止。R3具体操作绑定单次，同成功事务消费；旧session的reauthenticated_at不是独立高风险通行证 |
| mfa_credentials | state=pending/active/revoked、version、KEK密文、last_accepted_step、pending截止、确认/撤销及可空manual case | 保留 `(owner_id,id)`；把旧owner全表唯一改为 `UNIQUE(owner_id) WHERE state='active'`，允许旧active与pending同时存在。最终事务先撤旧再激活新，失败不删旧 |
| recovery_code_groups / mfa_recovery_codes | 组generation/credential/epoch、一次展示持久事实、撤销；码group/credential复合所属、摘要、单次消费 | 每owner至多一个未撤组；原子全组轮换。manual final仅废旧组，不生成；完整密码+新MFA重登及fresh R3后显式生成；丢响应不回放明文 |
| email_actions | 用途/epoch绑定、摘要唯一、期限/消费；保持既有对象 | reset不清MFA、不自动登录；名单不可用不消费token/proof或写成功receipt。当前名单版本在提交前复核 |
| identity_deployment_guards | id=deployment_id、version/manifest_hash、transition_id、closed/ready/applying及最近变更nonce摘要 | PG只镜像D0已批准状态；与外部当前高水位/受签清单不一致拒绝。恢复/status共享锁，维护排他；PG状态COMMIT为资格变更线性化点 |
| recovery_authorities | deployment/稳定authority、当前version/hash、实际人/OS/key映射摘要、validity/revocation、变更nonce摘要 | 来源是D0独立安装/维护，不从PG、自带根或新key PoP自证。K_change与K_recover分用途，恢复角色不能维护 |
| recovery_permissions | deployment/actor/purpose唯一、version/有效/撤销、独立授权引用摘要 | 普通Admin不等于获学生恢复权；只允许当前Admin+MFA+专用权限；权限更新先锁user再锁permission |
| manual_recovery_cases | deployment+case、target user/role/epoch、稳定executor、state、flow、receiver/plan/核验引用hash、pending截止/候选因子/确认hash、completed op | actor只能恢复仍为Student的目标；authority是指定项目用户。原执行者手动complete；浏览器仅pending_confirmed/显式放弃，不能final |
| manual_recovery_operations | stable op_id，deployment/case/executor/action/plan/flow不可变，reserved或committed、committed_at/audit/result_stage | committed事实与case/cap/factor/epoch变更同事务；预留op不是执行授权。final已提交且旧cap失效后仍可查最小事实 |
| identity_operation_proofs | 写操作challenge/nonce摘要，challenge_expiry、实际verified_at、operation_deadline，完整scope和消费/撤销 | issue/reissue/complete各自fresh、不同action/nonce、≤120s；complete含pending_factor/confirmation；原issue已用或过期不阻止有效pending用新complete |
| recovery_receivers | 公开EC P-256 JWK及RFC7638 SHA-256 thumbprint、当前server nonce摘要/代次/期限、注册消费、manual case或auto challenge绑定 | 只接受kty/crv/x/y，无私钥d；注册不授恢复权。每次action的PoP绑定case/challenge、flow、receiver_id、请求hash和当前单次nonce，锁内消费并轮换；nonce轮换不续总pending期 |
| manual_recovery_capabilities | deployment/case/target/flow、摘要唯一、receiver、旧target epoch、issue op/proof、流程期限、消费/撤销 | 只准绑定接收端的新因子证明或显式放弃，无课堂、普通session/WS、status、final或领码权。reissue换flow同时废旧cap |
| identity_control_audit | 最小稳定executor/subject/case/op引用、action/result、批准引用hash | 窄append-only；成功事务同写，失败全回滚。现有public.identity_audit的注册/激活枚举不被假称已支持恢复；新增对象避免重写0004 |
| identity_revocation_notices | 独立内部记录、deployment/subject或authority、epoch、source op、kind、lease/attempt/delivered | 不进业务counter/Outbox/Task，不新增公开WS事件。用户FK只引用已锁user；authority-only通知不虚构Web用户；nullable去重使用NULLS NOT DISTINCT |

case的稳定执行主体在线协议为 `executor_type=actor|authority + executor_id`。SQL镜像另以可空 `executor_actor_user_id` / `executor_authority_id`及XOR/equality CHECK连接真实users/authority FK，不能用多态UUID而放弃引用完整性。case→候选factor和factor→manual case是受限指针环，复合FK为DEFERRABLE INITIALLY DEFERRED；在同事务最终检查owner/target/case一致，普通服务不可任意设置这些指针。

manual op的 `(deployment_id,case_id,executor_type,executor_id)` 复合FK锁定case执行者，proof绑定同case/op与target，cap绑定同case/flow/issue proof。`completed_op_id` 和op→audit为受控延迟FK：同一最终事务必须写齐事实，不能提交半个completed状态。所有插入/引用变更的FK锁及UNIQUE冲突等待均列入第5节，不因DEFERRABLE而假定没有锁。

接收端注册记录只保存公开key和nonce摘要；尚无case也能产生有界注册，但只有被随后核验批准的计划选中并绑定才获得受限通道。初次issue采用flow=1；reissue采用current+1，原子废旧cap/receiver通道并保留原pending总截止。接收端await/seed/confirm/abandon逐次核验当前nonce与请求指纹，不能仅验证“持有这个key”而允许旧签名重放。注册/bind/nonce动作的全部FK引用须在同序父锁已取得后写入；不持receiver锁再补user/case。

自动码恢复与manual分开：`auth_challenges.kind=auto_recovery/recovery_mode=auto_code` 保存短效接收端绑定scope、cap摘要、flow和pending因子；密码+恢复码原子消费只产生这个受限上下文。auto enroll/reissue/confirm**每次**重新验证当前密码、有效code派生cap和receiver PoP，confirm另验证新TOTP；分别记录服务端实际verified_at、具体action/请求/接收端/flow绑定proof摘要、至多120秒operation_deadline及单次消费事实，取当前资格/原flow更早截止，全锁后重核。新proof替换不能让旧action/nonce复活，不重耗原code、不要求已丢旧MFA、不续原至多10分钟候选flow；只有auto final一次消费cap并原子切因子/废旧码/升epoch/全撤及审计。自动receiver确认可按该独立协议完成auto_code转换；**绝不调用或跳过manual case的原人手工complete**。两种路径都不直接登录、不交新码；普通MFA启用/自动恢复的HTTP响应与manual final不混用。

## 2. D0当前性与进程门

采用单个受监督本机broker实例。它持有恢复/维护/status各自受限DB pool的凭据，普通HTTP、Agent、Web Admin或SQL访问者不能直接使用这些pool。OS单实例锁、broker peer ACL、凭据ACL和连接排空构成受信部署边界；不能仅用进程内Boolean声称阻止另一个旧broker继续访问PG。异常重启先保持closed，确认旧实例/其PG连接已退出或排空，再用新随机挑战在线取得D0当前高水位/根/映射证据，与PG和只读清单匹配后开门。

所有恢复写/独立status在**进入PG前**取broker admission共享门并持有至事务结束；登记维护/换根/撤销取同一排他门，等待既有共享操作提交/回滚后才推进外部已批准账本。锁顺序为 `OS singleton → broker admission → PG deployment_guard → users…`；不得持PG锁等待admission、D0网络/人工签名或外部确认。

维护顺序固定为：验证D0既存K_change的完整old/new批准及当前资格 → 排他门排空 → D0外部账本接受/预留nonce和更高版本 → PG窄事务按完整锁序应用version/hash/revocation及审计 → 受控发布对应只读清单/高水位证据 → 在线一致性核对后恢复ready。中断保持closed，只沿原transition前滚；同版本只能同hash，外部账本缺失不能初始化新环境或回退旧备份。应用/恢复接口没有安装根入口，真实根全失另审重建。

外部D0更新必须统一经过此部署维护入口及排他门；否则启动/在线一致性监测应关门而不接受缓存旧高水位。root/D0/验证器被完全控制超出应用层可保证边界，普通DB访问不等同该权限。合成门/账本可以验证协议行为，不证明真实D0已建立；真实资源owner/IAM、安装批准、本人映射、根/凭据及备用处置仍待对应启用前流程。

## 3. 运行LOGIN、函数owner、schema与ACL/RLS

下面是本次S2指定的**实施目标角色**；不会在本轮创建。运行LOGIN均 `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT`，不持任何表ownership或schema CREATE。新增NOLOGIN definer owner同样无上述特权、无LOGIN/成员继承；仅拥有本动作固定函数和所需窄列权限。新schema `identity_control`、`identity_api` 不授PUBLIC CREATE/USAGE；所有函数固定 `search_path=pg_catalog,identity_control,public,pg_temp`、schema限定对象、无动态SQL，撤PUBLIC及非目标角色EXECUTE。`pg_temp`不得覆盖显式引用。

| LOGIN / 仅允许调用的固定入口 | definer owner与最窄数据能力 | 不得获得的能力 |
|---|---|---|
| 每run独立 `pr02a_<run>_runtime` → 既有runtime固定入口 | 现有classroom_web_runtime owner+FORCE RLS业务边界，不加入新控制角色 | 不持recover/status/maintenance/login验证凭据，不继承scheduler/identity/definer；HTTP不能SET任意owner |
| `pr02a_<run>_auth` → `identity_api.locate_auth / verify_auth / verify_factor` | `classroom_web_auth_verifier` NOLOGIN，只读最小用户/凭据定位列，TOTP受限验证/单步消费；密码/seed只在受限内存验证器 | 无通用users/MFA/正文SELECT、无恢复/维护或任意用户参数的正文入口 |
| `pr02a_<run>_session` → `identity_api.establish_session / validate_session / revoke_session / apply_account_change / redeem_auto_recovery` | `classroom_web_session_writer` NOLOGIN，必要session/device/challenge/reauth/factor/code窄写及最小audit/notice；路由scope和user/role/epoch由服务器生成 | 无manual final、D0登记维护、普通学习正文；auto_code不能指向manual case |
| `pr02a_<run>_recovery` → `identity_api.register_receiver / await_manual_receiver / prepare_manual_proof / issue_manual / reissue_manual / confirm_manual_receiver / abandon_manual_receiver / complete_manual` | `classroom_web_manual_recovery_writer` NOLOGIN，仅获本表集合所需列级SELECT/INSERT/UPDATE；current actor/authority、证明、scope及锁内验证全部在固定边界 | 无DDL/通用UPDATE/手工SQL、无正文/业务Outbox/counter、无根/authority维护；接收端入口不能调用complete |
| `pr02a_<run>_status` → `identity_api.read_manual_status` | `classroom_web_manual_status_reader` NOLOGIN，仅当前资格列及本人case/op/audit最小投影，必要行共享锁；无恢复DML路径 | 不INSERT/UPDATE/DELETE case/op/proof/cap/factor/code/notice，不重签/finalize/返回秘密；不能按任意executor读取 |
| `pr02a_<run>_maintenance` → `identity_api.apply_registration_change` | `classroom_web_registration_maintainer` NOLOGIN，guard/authority/permission窄更新及append审计/内部notice；broker须持独占门并验证外部批准 | 无root写入口、无普通业务/密码/seed、无手动恢复执行或代本人批准；新key PoP不授维护权 |
| `pr02a_<run>_notice` → `identity_api.claim_identity_notice / ack_identity_notice / retry_identity_notice / cleanup_identity_notice` | `classroom_web_identity_notice_worker` NOLOGIN，仅notice投影、lease/attempt/delivered列及按审定政策清理；独立后续授权查询入口只返最小撤销事实 | 持notice锁时不能查user/authority/counter；无恢复/普通业务写或秘密 |
| `pr02a_<run>_identity_cleanup` → `identity_api.minimize_identity_history` | `classroom_web_identity_history_cleaner` NOLOGIN，仅已批准U02政策的精确终态集合；按全锁序、精确引用清理，不创建真实政策 | 无权限自行决定保留时长、清反回滚根/高水位/撤销墓碑、恢复factor/epoch或清unknown收费风险 |
| 既有scheduler/dispatcher/LocalReconciler LOGIN | 保持现classroom_web_scheduler/task_dispatcher及0006固定owner职责 | 不加入任何新auth/recovery角色；不读取恢复秘密，不成为HTTP fallback |
| 该run一次性migrator/bootstrap | 仅独占disposable集群的受控迁移身份，记录实际bootstrap能力与每次临时SET/ADMIN/schema权限 | 不授运行LOGIN/NOLOGIN owner同级权限，不复用正式或旧集群；不能把非super安全拒绝当成功升级 |

表ownership归专用 `classroom_web_identity_table_owner` NOLOGIN，无任何运行LOGIN可INHERIT/SET到它；迁移所需临时membership须按0006已暴露的PG17 owner-transfer/撤权限制完整测试后撤净。上述函数owner不应同时成为表owner，保持FORCE RLS及窄列授权有效。函数owner需要的SELECT/UPDATE/INSERT/DELETE均按表/列/动作列明，不能用 `GRANT ALL ON SCHEMA/TABLES`替代；默认权限也撤PUBLIC。

用户私有表保留owner+当前账户/墓碑谓词，空scope拒绝。未知owner的token最小定位/凭据验证由独立auth verifier的专用窄policy及列授权支持，不能靠给普通runtime一个全用户policy实现；owner授权路径与内部验证policy分列。控制表FORCE RLS，只授对应NOLOGIN owner和明确动作的policy；runtime/scheduler/现identity组没有通用policy或表权限。broker把通过OS peer、MFA/签名验证器的身份作为受限函数的受信服务上下文；SQL caller自填 `app.actor_id/authority/owner` 或构造Principal均不能成为授权。专用LOGIN受限凭据与固定函数调用范围是服务信任边界，不能宣传数据库可自行验证未实现的OS peer/签名。HTTP接收端消息只能通过broker的受限receiver操作分派，不能选择pool/SQL函数或获得其凭据；无操作者fresh proof时不能调用complete。

**status业务只读不等于PostgreSQL READ ONLY事务。** PG的 `SET TRANSACTION READ ONLY` 不允许 `SELECT … FOR SHARE`；本方案用普通短事务执行唯一固定投影函数，通过共享锁与撤销线性化，函数不执行恢复DML。若PG行锁要求UPDATE列权限，status NOLOGIN owner仅取必要 `UPDATE(id)`，配合仅供锁的RLS UPDATE policy `WITH CHECK(false)`；不向status LOGIN授表UPDATE/SELECT。此模式需S3直接SQL负例证明不能实际修改行，不能靠文字把原始UPDATE能力开放给status调用者。

## 4. status事实与保留/删除

status的fresh≤120秒证明在broker按当前有效资格签发/验证，是**ephemeral只读凭据**；可共用协议schema/验证器形状，但不在identity_operation_proofs或其他恢复表INSERT/UPDATE/消费。重复合法查询可复用有效只读证明，无恢复业务写；独立限流/最小访问日志不含秘密，不因此生成case/op。未知case/op的查询可返回通用unknown，不为了满足FK而建虚假记录。

固定函数从受信executor上下文按case的typed稳定执行者筛选，只读本人case/op。actor须当前Admin+MFA+专用权限及有效自身session/device；authority须当前独立OS/key/登记/D0。target epoch上升/旧cap消费/浏览器失效不参与此历史查询授权；执行者撤权仍拒绝。

| 返回 | 必要持久依据和读取线性化点 |
|---|---|
| committed | 同最终事务提交的指定op committed事实、不可变执行者/case/action/plan绑定及相容case结果；不能只看某个case completed就猜另一个op成功 |
| not_committed | 当前执行资格/只读证明有效；对本人case/op取得共享锁，指定op仍为reserved且匹配当前有效pending/flow，并能证明该锁线性化点没有该op的已提交效果。只读副本延迟或无行不满足 |
| unknown | 无行、已最小化/清理、错op、无可授权case、PG不可用/超时、提交事实不一致或证据不足。不得因unknown/查不到猜失败后重做 |

锁后及返回前再检查资格/期限；撤权先提交则后续查询拒绝，status先持共享锁则先读该线性化点，再允许撤权生效。not_committed释放锁后可能发生新的complete，因此从不授写权；再次issue/reissue/complete仍须新鲜单次写证明并锁内重读case/flow/op，completed禁止重签或再切因子。响应只有阶段/提交事实/非秘密op/audit/as_of，没有seed/码组/token/签名/target资料/正文。

U02保持OPEN：S1提出的终态事实90天/核验详情引用30天**不是已确认的真实留存政策**，不写进正式TTL/CHECK或自动清理默认值。真实渠道/访问责任/最小保留上限在相应S2真实留存契约最终冻结及能力启用前另获确认。合成案例可以测最小化后的unknown，但不能把测试TTL当真实政策；不得无限保存核验详情来换status可用性。

清理依据精确创建/保留清单和受限权限，先取得与写/status兼容的锁，再从child proof/cap/op引用开始安全处理，最后处理审计/可删case，RESTRICT冲突时停止而不CASCADE。最小事实删后status为unknown。D0初始化、外部最高版本/撤销墓碑与authority反回滚控制状态**不是status查询事实**，不能按90/30天或账户清理降回旧值；对应最小化方案另审，不通过删账本重新授权。

## 5. 全路径锁序

所有调用先做无锁最小定位以确定完整ID集合，释放定位连接后再开短业务事务；定位结果不是最终授权。不持锁跨签名/人工交互、密码hash、KEK/网络I/O或D0读取；准备后在事务内复核版本。准入/限流独立短事务先完成并释放，禁止持限流锁等待用户/任务事务。

通用有序集合：`broker admission → 必要global control/budget或deployment_guard → 所有users按UUID → devices按ID → sessions按ID → authority/permission按类与ID → case → op → receiver → issue/completion proof/nonce → auth_challenge/email_action/cap按类与ID → MFA credential → recovery code group → code → append audit → append identity notice`。用例须在第一次取锁前确定全部actor/subject user；不能持后序锁再补一个较小user。修改密码/因子/epoch/安全授权的user首次即FOR UPDATE；普通业务仍沿原FOR SHARE，不全局弱化为NO KEY UPDATE。

| 用例 | 显式锁、隐式父锁与最终检查 |
|---|---|
| 普通业务/活动刷新 | users SHARE → device/session SHARE；要刷新last_seen/idle的行首次即UPDATE，不先SHARE再升级；保持到业务COMMIT。重核当前账户/epoch、父关系/两类期限/撤销；刷新不复活已到期session |
| set_capacity例外 | 保留现有identity_capacity/control UPDATE → admin user SHARE →必要设备/session/逐笔证明 → audit。不能统一包装“先认证锁user”再进既有control路径 |
| 注册/激活例外 | 保留control → invitation → pending/proof → 新user INSERT；新users邮箱UNIQUE、邀请FK/证明唯一冲突均在此路径审查；不能持已登录session锁重入 |
| 普通login/MFA/reauth/auto_code | 全套user → device/session →必要receiver →auth challenge/所需Web proof →factor →group/code；新session/device/factor的owner复合FK仅引用已取父锁。auto enroll/reissue/confirm各自当前密码+有效cap+PoP（confirm另新TOTP）fresh≤120s单次动作proof，所有锁后重核；不重耗code、不续flow。auto receiver可最终完成仅auto_code模式，不能访问manualcase完成函数 |
| issue/reissue | admission SHARE + deployment_guard SHARE → actor与target有序users（目标UPDATE）→actor device/session →authority/permission →case/op →receiver →当前新写proof/nonce与cap →audit/notice。重核D0/权限、target仍正确role/epoch、证据hash、计划/接收端/flow和所有期限；只创建pending，不删旧因子 |
| manual receiver证明/放弃 | 独立受限入口，不走普通OwnerContext。定位cap/receiver无锁后，admission/guard →目标user及所需控制资格 →case/op/receiver/challenge/cap →pending factor；锁内校验cap/receiver PoP/nonce/flow/expiry，最多pending_confirmed或abandoned，不取complete权，不升epoch |
| manual complete | admission SHARE + guard SHARE →原actor与target全部users（目标UPDATE）→需要撤销的device/session全套有序 →当前authority/permission →case/op →receiver →本次completion proof/cap →新旧factor →旧groups/codes →最小audit/notice。全锁后clock_timestamp再次核fresh≤120s/当前资格/target/flow/确认摘要；消费新proof+cap、切因子、废旧码、升唯一epoch、撤授权、写committed事实同事务 |
| 登记/换key/撤销 | admission EXCLUSIVE排空 →guard UPDATE →所有需引用actor/subject user先按序锁 →authority/permission UPDATE →批准nonce/变更状态 →audit/notice；外部D0准备已完成。没有Web主体的维护者不虚构user；禁止authority→user回取 |
| status | admission SHARE →guard SHARE →**仅当前执行actor自身**所需user/device/session（authority模式不要求target Web MFA）→current authority/permission SHARE →本人case/op SHARE →最小不可变审计投影。不给target user/旧target epoch/factor/code加锁；不消费写proof/cap，不发通知 |
| notice生产 | 已按序持有对应user/authority/guard等父锁后INSERT审计/notice；source op唯一冲突按原事务有界处理。无业务counter/Outbox/Task/FK；不能调用通用业务事件发布器 |
| notice领取 | 只锁notice集合（固定ID次序，批量有界，可SKIP LOCKED），更新lease/attempt并COMMIT释放。此事务不取user/authority/counter |
| notice投递 | 领取已提交后另开短事务，以正确users/control顺序查当前最小撤销事实，再释放；在锁外关闭连接。ack/retry另事务只更新原notice匹配lease列，不在持notice锁时回查授权 |
| notice清理 | 独立受限函数按有界ID集只锁可清理notice并删除/最小化，不查user/counter；未到期lease不抢占。父FK/unique检查不能变成调用业务发布器或恢复授权 |
| case/op历史清理 | 按guard→必要用户/authority→case/op→proof/cap/factor等次序取完整集合，再处理child引用；不持审计/notice后回取user/case，不改factor/epoch来“方便清理”。与status共享锁冲突按同线性化处理 |
| 既有task/LocalReconciler | 保留来源祖先→resource/plan→task→attempt/result→owner counter及原Receipt位置；原0006固定本地收尾不额外反取user；不因session撤销重发Provider或删除raw/风险 |

隐式锁必须单独列清：users邮箱等UNIQUE更新可能取得更强行锁；devices/session/因子/code的owner复合FK，case的executor/target/authority FK，proof/cap的case/op/factor FK，以及notice的subject/authority FK可触发KEY SHARE或延迟检查。事务首次锁定这些真实父行后才插入/改引用；审计只保最小引用，刻意避免末尾再加user/case FK锁。UNIQUE/部分唯一/NULLS NOT DISTINCT冲突会等未提交行，不能仅画显式FOR UPDATE图。触发器不得调用更低序对象/业务counter；DDL审查须给完整函数展开后的锁表及失败回滚顺序。

**A45双连接门禁仍未执行。** 原反例：L先持已有业务owner counter，插Outbox的owner FK等users KEY SHARE；S持users FOR UPDATE若再等同counter形成环。新S只写独立identity notice，不请求该counter；L可在S提交后继续。S3须用真实PG、明确barrier、两个提交顺序、实际FK/UNIQUE/trigger证明新路径有界完成，审计/通知与安全变更原子，旧业务风险保留且Provider零重发。必须覆盖producer、claim、delivery、ack/retry和cleanup的交错，不以放弱所有user锁或删断言消除反例。

数据库重试有界并受同一操作deadline/当前资格限制；每次重取完整同序锁，不能刷新旧verified_at。只重试数据库部分，不重发外部通知/Provider或盲重做未知COMMIT。普通/专用长等待上限、A45和D02真实跨120秒屏障沿获审S1 [验收计划](../s1-preparation-20261106/ACCEPTANCE-AND-EVIDENCE.md)，不得把长测试timeout延伸成授权期限。

## 6. S3之前及S3必须交付的验证

| 门禁 | 需要的实际证据；本轮均未运行 |
|---|---|
| 隔离guard先行 | 先审最小runner/guard适配。现shared guard只认字面127.0.0.1、显式非5432及pr02a_，旧PR03a runner核固定旧相对data_dir；不能直接改URL为pr03b_、复用旧目录或连旧集群。新增run-owned marker/PID/启动时间/data_dir/server/DB/role精确核验，外网/DNS/PG*重定向拒绝，安全自检先于任何建库/迁移 |
| 增量迁移 | 不改0001–0006；一次只建获准子集，单Alembic链。空库、0006增量/重复、旧代码、失败回滚、同名恶意角色、临时membership清零分别验；S2不分配或应用revision |
| 角色正反SQL | 对上表每个LOGIN实测允许固定入口；直接SELECT秘密/正文、UPDATE factor/epoch/authority、SET ROLE/继承owner、schema CREATE、动态SQL、PUBLIC/default权限和空/伪造scope均拒绝。status UPDATE(id)锁权限必须无法转成真实DML。角色/ACL/RLS/FK失败后逐表无副作用 |
| 身份/恢复 | 原A01–A46和19状态推演对应真实PG/HTTPS/WS/本地捕获器及合成工具；D01自带根/新key循环拒绝/外部高水位回滚/撤销前后；D02四分钟/4:59/跨120s/fresh complete/重放；D03 cap用完、target升epoch、撤权、其他Admin、未知/清理/PG故障、重复零写 |
| N01/N02 | A45含全部显式与隐式锁及完整投递/清理交错；A46 final零新码、旧cap/status/CLI无码，完整重登后显式生成/一次展示/丢响应/显式整组轮换，不复用confirmMfa同步秘密响应 |
| 秘密与故障 | PG/KEK/必需审计/COMMIT断连均安全失败或结果不明；不把cap/seed/code/Cookie/key放argv/env/history/stdout/stderr/trace/APM/Receipt/Outbox/core dump。用合成标记检查真实通道和异常，证据不存秘密原文 |
| 回归与阶段边界 | 保留原Windows/DPAPI、九Node及相关前端/后端；按实际改动运行适当范围，原失败/跳过留证。性能/限流/枚举区分门禁依获审S1、环境硬配额及目标复测；不能用结构编号/fixture通过替代机制 |

PR03a P3-01继续在确定部署数据库/迁移身份前处理，0006仅已验证可信bootstrap；旧8 passed/1 failed/exit1原件保留。PR02B-P3-01在reconcile HTTP接入前、P3-02在多Attempt扩展前处理；纯认证路由不自动触发或关闭，也不得顺手暴露reconcile。PR09a及其他P3/TBD保持原关口。

注销/重置/恢复不删除raw/Receipt/Outbox/fence，不把unknown当未发/未收费，不重发/切换Provider或退款清风险。mark_submitting成功COMMIT仍是发送授权线性化点，撤权不能倒转已提交意图；新的控制面不能改动这些任务不变量。

## 7. 本次停止点

本轮只修改分配的domain-model逻辑子集并编写本文；实际静态检查及完整保护结果由S2交付记录汇总。没有创建角色/schema/PG、执行迁移、实现函数/认证代码或测试、运行机制/Argon2/浏览器实验、登记真实身份或执行恢复；不commit、Push、合并、整合或部署。S2独立审查通过及生产者/消费者明确确认后，也须另有精确S3实施/实验授权。U01/U02真实资产与留存仍未决，TBD-08 OPEN，补偿未实测则条件风险不生效。
