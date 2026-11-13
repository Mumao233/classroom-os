# PR-03b S2：HTTP、会话与自动恢复契约候选

日期：2026-11-06（America/Los_Angeles）。精确Git基线 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`；正式main保护基线 `b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`。本文件与 [OpenAPI](../../architecture/contracts/openapi.json) 的 `0.1.0 → 0.2.0` 同为 **S2正式契约审查候选，尚非批准契约或已运行服务**，前缀保持 `/api/web/v1`。

S1已通过 [定向复审](../../../../outputs/pr03b/s1-remediation-review-20261106-01/REVIEW.md) 的精确七文件快照；它批准技术/实验方案、环境及阈值，不冻结生产参数。D01–D03仍CLOSED（设计层），TBD-08仍OPEN — PARTIALLY DECIDED；U02真实留存/实际资产仍未决。本轮仅契约/文档/静态检查，不实现认证、生成客户端、修改Mock/测试、安装依赖、启动机制实验、登记真实身份、执行恢复或commit/Push/合并/部署。

## 1. 机器契约变化和范围

| 对象 | 本次候选变化 | 保留边界 |
|---|---|---|
| 三个新设密码字段 | RegisterRequest.password、PasswordResetRequest.new_password、PasswordChangeRequest.new_password公开schema最小6、最大1024；描述强制可信服务端Admin最小12 | 注册只Student；无客户端role字段。当前服务端12–1024实现未改，不声称已支持6–11 |
| LoginResult | 三个互斥严格分支authenticated、mfa_required、recovery_required；后两者普通user/device/CSRF为null | MFA未完成或恢复码验证成功均不能伪装authenticated；旧客户端未知state必须拒绝/升级 |
| MfaChallengeRequest | 必填factor_type=totp或recovery_code；两支字段严格，后者要求接收端P-256公钥及PoP | 恢复码保持原输入长度包络6–128，熵/格式是服务端安全生成及校验约束，不借长度充当高熵证明 |
| ReauthRequest | operation={purpose,target,candidate_parameters}按purpose严格oneOf；当前password与必要TOTP重新验证 | 风险、角色、epoch和参数承诺由服务器决定；不新增通用私有header、任意用途或客户端低熵SHA |
| 自动恢复接收端 | 仅新增POST `/auth/recovery/enroll`、`/auth/recovery/confirm`，独立recoveryCookie+CSRF+Origin+key PoP | 仅密码＋恢复码的automatic flow；manual case不得通过这两路finalize，不新增公开manual issue/complete/status |
| 新恢复码 | 保留现有POST `/auth/mfa/recovery-codes`及operationId `rotateRecoveryCodes`；明确其也用于完整重登后首次显式生成 | 不另加generateRecoveryCodes路由或改旧operationId；final/status/CLI/旧cap无权领码 |
| WS/会话 | 机器描述补同事务实时user/session/device/epoch授权、Cookie/期限及WS两deadline | 不改业务/收费/unknown语义；不新增公开WS枚举；缓存/采集/运营保留所属门禁 |

原非auth业务path对象保持；仅既有个人会话/设备安全、注销与WS认证节点补明确安全描述，Meta.contract_version随包版本更新。Error原枚举和嵌套严格对象保持，未以此轮扩展业务错误/供应商接口。新增对象均`additionalProperties:false`；oneOf封闭分支，不能以宽松任意JSON替代。

## 2. 密码、Unicode和名单

新密码按Unicode code point/标量计数；拒绝非法UTF-8、孤立surrogate及重复JSON成员，原样验证，不trim、截断、规范化或强制组合。Student最小6；Admin最小12，由注册用途/有效reset token/当前服务端账号判定，不采信请求role。建议Admin密码管理器生成16字符以上仍只是建议。

login/reauth校验既有PHC，不套新设最低值，不静默重写历史密码。新设密码必须本地弱/泄露密码名单筛查，明文不外发。无有效批准名单时register/change/reset以`503 SERVICE_DISABLED`、`retryable=true`安全暂停，**不消费reset/reauth证明、不留成功Receipt**，正常既有密码login继续原MFA/准入。名单命中为通用`VALIDATION_FAILED`，不披露名单内容。实际写入提交前重核版本仍有效且未撤销；名单/PG/审计故障不留部分改密/撤权。

S1认证JSON原始body实验输入为16KiB。本次逐路由核验发现reauth可同时携当前密码及候选new_password：两者各1024个补充平面字符采用JSON surrogate转义时，仅字符串就约24KiB。因此S2候选 **仅 `/auth/reauth` 调整为32KiB，其余auth仍16KiB**；这是有依据的传输预算提案，不修改S1原件、不放宽1024 code point上限、不冻结生产配置。所有预算在昂贵校验前实施；实际路由字段/编码/资源仍须独审及S3验证。

## 3. 会话、CSRF与请求授权

完整登录Cookie固定 `__Host-classroom_session; Secure; HttpOnly; Path=/; SameSite=Lax`、无Domain，随机不透明token仅存用途分域摘要。设备ID是标签，登录建立未信任device，不构成缓存信任或本次录音consent。Student服务端idle14天/absolute30天，remember_me只改变Cookie持久性；Admin始终非持久Cookie、idle15分钟/absolute8小时且MFA强制，即使remember_me=true也不延长。严格`now<idle && now<absolute`，等于即拒绝；活动/轮换/再认证不延原逻辑absolute，只有完整重登新建逻辑会话。

preauth、完整session及自动recovery是三个独立上下文；CSRF不能互换。浏览器写入同时校验精确配置HTTPS Origin、对应`X-CSRF-Token`、Fetch Metadata、strict JSON、body/准入预算；缺失/null Origin拒绝，不允许通配或后缀匹配。反向代理仅信受限配置，不相信任意X-Forwarded-*。CLI控制协议不是浏览器Auth豁免路径。

GET `/auth/csrf`只发协议防护材料：普通会话取自己的CSRF，匿名取preauth；若存在有效automatic recoveryCookie则只返回该受限上下文CSRF及receiver_challenge，不产生普通身份、领域写入或终态status授权。刷新挑战/CSRF不续session/proof/flow有效期。过期或已消费recoveryCookie不能查已完成事实。GET `/auth/session`仅接受完整session，不能从恢复/preauth返回普通User。

服务器最小定位后，在与受保护写相同短事务中按已审锁序复核active user、role、唯一`users.security_epoch`、device/session、当前因子及两个期限，取得所有锁后再查实际DB时钟。不得接受客户端Principal/owner/epoch、缓存Boolean或OwnerContext fallback。通知只是加速，PG实时事实决定授权。密码验证在长事务外，写前重核密码版本/hash及epoch，防迟到rehash或撤权覆盖。

## 4. 再认证操作绑定

客户端向 `/auth/reauth`提交当前password、必要mfa_code及严格operation。只有未启MFA的Student可省略mfa_code；Admin/已启用Student必须当前TOTP，恢复码不能作普通敏感写的替代因子。当前退出不额外再认证。

普通HTTP绑定结构：

```json
{
  "purpose": "changePassword",
  "target": {"kind": "account", "id": "11111111-1111-4111-8111-111111111111"},
  "candidate_parameters": {
    "idempotency_key": "22222222-2222-4222-8222-222222222222",
    "body": {"new_password": "synthetic-only-password"}
  }
}
```

例值仅为静态形状，不是账户/凭据或已执行命令。candidate_parameters中的key是**未来写操作**的Idempotency-Key，区别于本次reauth自己的key；body须完全匹配该purpose对应严格request schema。目标只是定位：account必须当前user；session/device/enrollment必须其当前owner及purpose匹配。服务器不允许客户端选择risk/role/MFA等级。

完整session与reauth token的生产者均使用至少32随机字节，编码为canonical unpadded base64url、43–256字符；接收端解码重编码并核最小字节数，不以长度断言随机熵。ReauthResult与X-Reauth-Token同形，可原样进入控制协议StudentExecutorEvidence.Secret；session token同样可进入ActorSessionEvidence.Secret。控制Secret的43–512外包络不表示HTTP生产者会生成超过256字符；语义/用途仍不可互换。

服务端使用用途分域、版本化独立binding key的HMAC-SHA-256，承诺purpose、目标、未来操作key、完整经校验typed参数及当前user/session/device/epoch。参数采用确定性带类型/长度编码：字段按名字稳定排序、UTF-8字符串按字节长度前缀、整数最短十进制、null/bool/object/array分别编码；拒绝未审float/额外字段，不trim/改写密码。该编码仅服务端执行、最终写重算；密码/确认token/码原文仅在受限内存，不存candidate body、普通SHA或可离线猜测的公开指纹，不回显HMAC或新密码。持久proof仅保存nonce摘要、绑定key版本/承诺、当前版本和期限等最小数据。

verified_at为服务器实际验证全部当前因子及绑定成功的DB时间；不能用last_seen、客户端时间、挑战签发或等待锁后的新时间替代。R1 Student≤600秒/Admin≤300秒，R2≤300秒，R3≤120秒，再与证明/当前资格/session/device截止取最早；锁后严格小于deadline，等于/晚于拒绝且不消费恢复业务nonce。R3单次，绑定具体操作，参数/key/目标变化或重复执行拒绝。普通绑定也指向一个确定幂等操作；已提交相同key的非秘密回执只按现有当前授权读事实，不构成再次执行或秘密回放。

| 路由/operationId | 本次S2风险映射候选 | 操作绑定及保留条件 |
|---|---|---|
| `/auth/logout` logout | R0，无额外再认证 | 当前session撤销并清匹配Cookie；不能阻挡用户退出 |
| GET `/auth/session`、`/me/sessions`、`/me/devices`及普通本人非敏感读取 | R0 | 仍需完整当前身份/owner；不是通用业务或运营权限豁免 |
| `/me/sessions/{id}/revoke` revokeSession、`/me/devices/{id}/revoke` revokeDevice | R1 | 前者限本人其他session；当前session走logout；target分别session/device，body Empty，未来key绑定 |
| `/auth/logout-all` logoutAll | R1技术推荐 | target当前account、Empty；撤全派生授权，不删任务证据 |
| `/me/devices/trust` trustDevice | R1技术推荐 | target当前device，严格TrustDeviceRequest；既有PR-05a缓存/信任POC未解禁 |
| `/auth/password/change` changePassword、`/auth/email/change` requestEmailChange | R2 | 当前account，分别严格new_password/new_email；邮箱确认仍需独立新邮箱证明 |
| `/auth/mfa/enroll` enrollMfa、`/auth/mfa/confirm` confirmMfa | 首次Student可选MFA为R2；已有因子替换为R3技术推荐 | enroll绑account/Empty；confirm另取proof绑enrollment及精确确认body；确认前旧因子不删。无MFA Admin不得凭普通session启动初始绑定；首Admin另有受控初始化门禁 |
| `/auth/mfa/disable` disableMfa | R3 | 仅Student，当前因子；Admin即有有效proof也拒绝关闭MFA |
| `/auth/mfa/recovery-codes` rotateRecoveryCodes | R3技术推荐 | 完整登录后显式首次生成/轮换；account/Empty，新旧组原子替换及一次展示 |
| `/me/delete` deleteAccount | R3 | 当前account、原DeletionSubmit确认token/base_revision及key，保墓碑/unknown/账本 |
| 其余admin写、cache-unlock、收费/采集及未列路由 | 保留原门禁，本次不追加通用risk/purpose | 不在ReauthOperation枚举中假装已批准；所属增量契约先审逐路由映射，不能挂一个万能R1/R2/R3 proof或顺带启用 |

窗口与已确认代表操作不重新投票。表内注明的新增映射只是S2技术候选，必须纳入本包独立复审，不能写成历史产品已经逐路由批准。运营仍Admin+MFA无正文后门；普通学习仍R0而非一律再认证。

### 4.1 学生恢复执行Admin的窄桥接

ReauthOperation另列四个purpose：`student_recovery_issue`、`student_recovery_reissue`、`student_recovery_complete`、`student_recovery_status`。target严格为`{kind:"recovery_case",id:case_id}`；candidate_parameters严格为`{op_id,plan_hash}`，plan_hash是完整受审控制计划的64位小写hex SHA-256，不能拿其自洽证明已核验或有权执行。

四类均要求当前Admin/password/TOTP、自己的有效session/device/epoch和专用student-recovery权限，期限≤120秒。写类单次，status proof在有效期内仅本人case/op只读可复用，不消费issue/completion nonce/cap。opaque token经受限FD交本机broker，broker独立取得当前主体、重核当前权限及锁后期限；issue/reissue/complete另核目标仍为Student及当前case/op/写计划/核验证据。status另核稳定stored executor、本人case/op、独立StatusPlan及持久op事实，不依赖目标当前role/epoch、原issue证明或已按政策清理的核验详情；证据不足仍为unknown。不从token自报role授权，也不允许转交另一Admin代执行。HTTP端仅产生窄proof，不执行公开issue/complete/status；管理员target恢复只走项目用户本人独立authority/OS/key，不要求已丢失Web MFA。详细控制状态/字段与 [独立控制契约](RECOVERY-CONTROL.md) 同审。

## 5. 自动受限恢复：浏览器闭环

普通密码登录先得到mfa_required，普通user/device/csrf均null，另有非秘密随机receiver_challenge。完整会话只可能从正确当前TOTP分支产生。使用恢复码时，`factor_type=recovery_code`必须同时给当前challenge_id、恢复码、严格P-256公钥JWK和key PoP；服务器重核此前密码验证的版本/epoch、challenge、预算，原子消费一个码并创建automatic flow，只返回recovery_required。

该分支顶层challenge_id/user/device/csrf均null，嵌套recovery仅含flow_id、mfa_rebind scope、pending、expires_at、该flow专属CSRF和下一receiver_challenge。服务器发独立 `__Host-classroom_recovery` HttpOnly/Secure/Path=/、SameSite=Lax、无Domain Cookie，只存摘要并绑目标epoch、原密码版本、flow generation、接收key。它不是普通session，不能读课堂/导出/运营、取WS ticket或cache key，不可进入普通confirmMfa、rotateRecoveryCodes或手工控制面。

1. POST `/auth/recovery/enroll`：body为flow_id、当前password和receiver_proof；同时需recoveryCookie、其CSRF、Origin及当前flow/key。返回该接收端的pending enrollment_id、otpauth_uri、固定expires_at和下一challenge，旧因子仍有效。响应或签名挑战丢失时，可在flow仍有效时GET csrf取新协议挑战，再重新校验当前密码、fresh接收端key PoP及新的Idempotency-Key显式重签pending seed；原pending generation/seed失效，不能从Receipt回放，也不延原flow TTL。接收公钥保持已绑定那一枚；换接收端须独立重新授权，不能直接覆盖。
2. POST `/auth/recovery/confirm`：flow_id、enrollment_id、当前password、六位新TOTP及receiver_proof；锁后重核flow/目标epoch/密码因子版本/新因子确认/所有期限，原子消费cap和challenge、换因子、废旧恢复码组、递增唯一security_epoch、全撤旧session/device派生grant及challenge/ticket，写最小审计和独立identity通知。
3. 最终只回`{state:"completed",flow_id,requires_full_login:true,new_recovery_codes_issued:false}`，清受限cookie；不回User、seed、新码或普通session。必须密码＋新MFA完整重登，再以所需近期proof显式调用现有rotateRecoveryCodes生成新组。人工final同样不产生新码，但不通过上述automatic路由执行。

PG/KEK/必需审计失败原子回滚；新因子确认前旧因子保留。automatic final COMMIT结果不明不能根据查无或重试响应猜失败，更不能用已消费cap/目标旧epoch查询终态。先以完整密码＋新MFA尝试正常重登取得当前事实，或转另行受审的fresh授权流程；不能盲重做final、消费另一恢复码或以旧cap重签。当前候选没有新增公开自动/人工status；`x-replay-policy=non-secret-status`仅约束秘密不回放，不创设已消费cookie的查询权。manual D03只读status仍限独立control协议的当前原actor/authority。

**S2候选操作细化：**automatic enroll、pending seed重签及confirm各自重新校验当前密码和fresh接收端PoP；confirm另验新TOTP。服务端在本次全部验证实际成功时记录各自verified_at，固定具体动作/参数/flow绑定的单次operation_deadline≤verified_at+120秒，再取当前资格及原flow更早截止，取得所有锁后用clock_timestamp严格检查now<deadline。过期或等于即拒绝，不刷新旧verified_at；候选10分钟flow只延续受限流程，绝不能成为10分钟高风险写授权。无需已丢旧Web MFA，也不重消费原恢复码；cap最终只消费一次，重签/防护刷新不续flow TTL。每路只有一个password字段，沿16KiB rawbody候选且不套新设密码minimum；reauth双密码32KiB例外保持。该细化须本包独审和S3机制证据，未据此声称已实现。

### 5.1 接收端PoP规范

JWK仅四个字段`kty:"EC"`、`crv:"P-256"`、x、y；x/y为32-byte坐标的43字符canonical unpadded base64url，拒绝d、额外字段、非canonical编码、无效曲线点。成熟组件执行ECDSA SHA-256；WebCrypto签名固定64-byte IEEE-P1363 `r||s`，输出86字符canonical unpadded base64url；不接受混杂DER。公钥thumbprint按RFC7638对四字段JCS取SHA-256再base64url。

签名输入为UTF-8，以下字段依次用单个LF分隔、无末尾LF：固定域`ClassroomOS.Web.ReceiverPoP.v1`、大写HTTP method、完整固定API path（不含query）、canonical UUID Idempotency-Key、上下文种类`login_challenge`或`automatic_recovery`、对应challenge_id或flow_id、receiver thumbprint、服务器随机32-byte challenge的base64url、`SHA256(JCS(unsigned_body))`的小写hex。unsigned_body是实际严格body删去整个receiver_proof属性后的对象；这几个新协议body仅为字符串/固定JWK对象，使用RFC8785 JCS，不接受浮点或任意扩展。不得以客户端传入的body hash、时间或thumbprint替代对实际请求/绑定key的重算。

receiver_proof严格只含`challenge`和`signature`。服务端确认挑战来自该login/recovery receiver上下文及当前generation、未过期未消费且key匹配，再校验签名绑定的具体method/path/action在当前flow的受限允许集合内，锁内单次消费。GET防护刷新可发该receiver上下文的新挑战，但不能把它当作业务授权或取消签名中的具体动作绑定。PoP只证明已绑定接收key控制权，不证明项目用户身份、Admin权、普通MFA或manual批准。签名原文/签名/TOTP/seed/cap及低熵消息摘要不进入通用日志、Receipt、Outbox或证据盘；持久审计仅保非秘密挑战/操作引用、消费结果和阶段。全部秘密响应no-store，合法seed只到绑定TLS页面内存。

## 6. 恢复码、错误和WS

普通首次MFA确认的既有`confirmMfa → RecoveryCodes`只适用于已完整授权的普通enrollment；不复用为manual或automatic final。现有rotateRecoveryCodes既能完整重登后首次显式生成，也能轮换；一次展示、仅存摘要，最小成功组非空且无重复码。相同key响应丢失返回`409 SECRET_ALREADY_ISSUED`，fresh再认证、新明确操作整组轮换，旧组废弃；不从回执回放明文。status/CLI/旧cap无领秘密路径。

| 场景 | 既有错误候选映射 | 限制 |
|---|---|---|
| 凭据/上下文失效 | 401 UNAUTHENTICATED | 通用消息，不枚举账号/因子/他人case |
| 越权、Origin/CSRF、缺必要因子/近期proof | 403 FORBIDDEN/MFA_REQUIRED/REAUTH_REQUIRED | 无业务写；错误不得包含秘密或内部权限细节 |
| 幂等异体/秘密已发 | 409 IDEMPOTENCY_MISMATCH/SECRET_ALREADY_ISSUED | 原动作不重复，秘密不回放 |
| 结构、Unicode、角色新密码门槛、名单命中 | 422 VALIDATION_FAILED | 无客户端role绕过，无半成功 |
| 准入/失败预算 | 429 RATE_LIMITED | 同来源失败、账号、preauth及昂贵计算预算分别计；不靠重开challenge清零 |
| 无有效名单/必要服务不可用/提交不明 | 503 SERVICE_DISABLED/STORAGE_UNAVAILABLE/RESULT_UNKNOWN | 前两者安全暂停；RESULT_UNKNOWN不构成重试授权；retryable字段不能驱动秘密重放或盲目恢复 |

Error嵌套字段和枚举未扩；上述组合进入S2正反fixture审查，不能实现时发未冻结code。错误、私有及秘密响应no-store，日志/APM/错误追踪禁收密码、seed、恢复码、Cookie、ticket、签名和正文。JSON schema不证明Origin、当前资格或副作用已实现。

WS先由完整会话＋CSRF＋Origin申请单次ticket；TLS upgrade另核Cookie/精确Origin，固定subprotocol classroom.v1，第一消息authenticate消费ticket。**ticket expires_at与upgrade-relative首帧deadline分别检查，任一到/超过即拒绝**；垃圾帧不续时，认证前零业务/音频buffer。S1的30秒ticket/5秒首帧仅实验输入，不冻结生产TTL。票据不得进URL、subprotocol凭据或日志；消息/敏感事件发送前再核PG撤销和owner/墓碑，通知丢失不复活权限。保既有关闭码和owner cursor/resync语义，不自动Provider重发或开麦。

## 7. 消费者、兼容与S3门禁

0.2.0包含必填factor_type、严格operation绑定及未知recovery_required分支，旧严格客户端并不自动兼容；密码6–11也会被旧验证器拒绝。后端和Claude消费者必须共同确认版本/生成类型/Mock/fixtures；暂未改生成器输出或实际前端，不能以TS字符串类型或普通MFA假状态兼容掩盖差异。未知state必须明确关闭/提示升级，不把它当authenticated。Meta只报告契约版本，不放宽功能开关或声明真实联调完成。

S3获明确范围授权后才同步实际IdentityService/hasher/reset/change/verify、HTTP/控制broker、迁移/ACL/RLS、生成类型、Mock、认证UI和正反有状态测试；不得本轮顺手改代码、测试或依赖。后续证据含真实PG锁/撤权、HTTPS Cookie/Origin/CSRF、PoP/秘密渠道、nonce单次、完整重登/丢响应、WS两deadline、S1 A01–A46与十九推演、原回归/失败。纯JSON结构通过不证明这些机制已执行。

PR03a P3-01/bootstrap-only、PR02b reconcile HTTP及多Attempt关口、PR09a及其他TBD保各自触发节点。保mark_submitting成功COMMIT发送授权边界、raw/Receipt/Outbox/fence/unknown/may_have_charged；恢复或注销不清账本、不重发Provider。真实D0/本人映射/学生核验/权限/留存另审登记，条件风险仅补偿全部验证后对10–30邀请Alpha生效。S2候选完成即交精确快照独审，不自行进入S3或真实启用。
