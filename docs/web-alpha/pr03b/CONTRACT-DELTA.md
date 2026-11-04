# PR-03b 契约差异与兼容草案

日期：2026-11-05（Asia/Taipei）。核查基线：`b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`。**本文件将最新已确认产品政策转为待 Astra 审查的契约差异提案，不是新冻结契约；本轮不改正式 contracts、生成类型、Mock、实现或测试。** 产品政策见 [POLICY-DECISIONS.md](POLICY-DECISIONS.md)。已确认政策不再作为备选投票；字段、协议、凭据登记及实验结论尚待技术审查。TBD-08 保持 `OPEN — PARTIALLY DECIDED`。

## 1. 现状及差异事实

正式 [OpenAPI](../architecture/contracts/openapi.json) 的 `info.version` 为 `0.1.0`，API 前缀为 `/api/web/v1`；说明仍标记为设计契约。三个对象均为 `additionalProperties: false`：

| 字段 / 当前入口 | 当前约束或行为 | 需审差异 |
|---|---|---|
| `RegisterRequest.password`；`register` / `POST /auth/register` | `type: string`，`minLength: 12`，`maxLength: 1024` | 学生新设密码最低 6，无字符组合要求；注册仍只激活 Student，不能让客户端选 Admin |
| `PasswordResetRequest.new_password`；`resetPassword` / `POST /auth/password/reset` | 同为 12–1024；请求必含单次 `token` | 学生最低 6；管理员已确认最低 12；角色由服务端解析 token 所属账号取得 |
| `PasswordChangeRequest.new_password`；`changePassword` / `POST /auth/password/change` | 同为 12–1024；另依赖会话及再认证 | 学生最低 6；管理员最低 12；以当前服务端角色与 MFA 状态校验 |
| [IdentityService.register](../../../web/backend/classroom_web/application/identity.py) | 用例入口显式拒绝非字符串及不在 12–1024 的值；替换 hasher 不能绕过 | 后续获准后同步新设密码政策，保留用例层权威校验 |
| [Argon2PasswordHasher.hash](../../../web/backend/classroom_web/infrastructure/identity_passwords.py) | 哈希入口再次强制 12–1024；Argon2id `m=65536 KiB / t=3 / p=1`、salt 16 byte、输出 32 byte | 将角色政策与密码学调用边界分清，避免只改 OpenAPI 或只改 use case；上述成本仍为实验初值 |

当前 `IdentityService` 仅有 PR-03a 注册/邀请/激活等内部用例，未交付密码重置、改密、正式登录/验证会话的服务；不能将三处 schema 当作三处已经运行的 HTTP 实现。现有哈希入口也没有正式 `verify/needs_rehash` 适配器实现。后续须按确认范围增量加入。

`LoginRequest.password` 与 `ReauthRequest.password` 只有 `maxLength: 1024`，没有新设密码的 `minLength`。**不建议把新密码门槛直接复用到登录验证**，否则政策升级可能拒绝已有合法凭据。登录始终验证已有 PHC hash，账户是否必须改密另依批准政策处理。

## 2. 拟审变更集合

### CD-01：密码政策、角色与字符计数

拟提交机器差异为三个新设密码字段 `minLength: 12 → 6`，暂保留上限 1024。这是已确认学生政策的契约对齐提案；本轮正式值保持 12。管理员已确认最低 12，必须作为服务端条件政策说明和契约验收条件，不能仅依公共 JSON schema 的 6 字符通过就接受管理员短密码。产品已**有条件接受**学生 6 字符及可选 MFA 的剩余风险：仅在补偿措施验证通过后、仅对 10–30 人邀请制 Alpha 适用；这不代表补偿措施已经通过或其他阶段风险已被接受。

建议不增加客户端可写 `role`。普通注册不授 Admin；重置通过受控 token 找账号，改密通过当前服务端会话找账号，管理员初始化通过受控运维流程。错误响应不得借角色差异泄露匿名重置目标的身份。

字符计数建议明确采用 JSON Schema 的 Unicode code point 语义，Python `len` 与前端按 code point 计数一致；前端不能用 UTF-16 code unit 数量误判含补充平面字符的密码。是否拒绝非法 Unicode、传输字节上限及任何规范化行为应随契约审查记录，不静默截断、trim 或改变已有密码。覆盖组合字符、空格、中文、emoji、6/1024 边界；“6 字符”不擅自解释成“6 字节”或字符组合规则。

已确认采用**本地弱密码/泄露密码阻断名单**。无有效名单时暂停注册、改密和密码 reset；已有正常 login 不受名单不可用影响，仍执行密码验证、MFA、限流及会话安全检查。契约需明确三条新设密码入口的名单校验及不可用响应、幂等重试和 token 消费边界，避免先消费重置 token 后才发现名单不可用。名单来源、有效性/版本/更新规则、校验事务边界和错误映射由 Astra 审查；不能用外发密码明文、空名单自动通过或关闭正常登录替代已确认政策。若现有错误码不足以表达安全暂停，须独立审查新增类型，不私加未冻结 code。

### CD-02：ReauthRequest 与认证强度

当前 `ReauthRequest` 仅必填 `password`，`mfa_code` 为可选字符串；响应 `ReauthResult` 为 `reauth_token + expires_at`，敏感路由通过 `X-Reauth-Token` 使用。字段可选只允许未启用 MFA 的学生走密码再认证，**不代表已启用 MFA 的学生或管理员可以跳过第二因素**。

契约描述和正反 fixtures 应落实已确认政策：服务端按当前角色、已绑定因子和操作风险决定所需强度；管理员始终需要密码＋MFA；恢复码仅可进入受限恢复流程，不可充当管理员运营写入的常规第二因素替代。再认证证明绑定 owner/session/当前 `security_epoch`、实际因子验证时间和允许范围；失败、Cookie 刷新或普通活动不能产生近期认证证明。

再认证窗口已确认：R1 为学生 10 分钟 / 管理员 5 分钟，R2 为 5 分钟，R3 为 2 分钟；各级操作范围以产品政策表为准。R3 已确认要求绑定具体操作并单次消费，不能退化成该窗口内任意高风险写入通行证。现有请求没有 `purpose`、目标或参数指纹；实现该已确认逐笔语义仍须独立审查目的枚举、目标标识、规范化参数指纹、单次消费与过期、跨操作/跨会话拒绝、响应和错误的消费者影响。字段命名、证明传输及路由映射是待审技术设计，不能先私加请求字段、header 或 token 自定义语义再补契约。

### CD-03：受限恢复不是 authenticated

当前 `MfaChallengeRequest` 为 `challenge_id + code`，`code` 长度 6–128；`LoginResult.state` 仅有 `authenticated | mfa_required`，并要求 `challenge_id/user/device_id/csrf_token` 字段存在，允许值按 schema 为 null。冻结流程要求 MFA challenge 阶段 `user/device_id/csrf_token` 为 null，不能提前披露已认证上下文。

现有模型未表达完整的受限恢复协议。密码＋恢复码仅进入受限重绑上下文，不授予课堂、导出或运营权限；学生人工恢复由 Admin 经专用受控恢复命令执行，管理员人工恢复则**仅限本项目用户本人，以独立运维身份手动执行专用命令**。其他 Admin 无权批准或执行管理员人工恢复；普通 Admin 角色、账号邮箱或 Web session 不等同该独立运维身份。专用命令内部使用受审事务完成身份核验、权限、审计、受限重绑、旧授权撤销与故障原子性，禁止人工 SQL 改表。

以下须作为**独立恢复契约审查**，不能因产品同意人工恢复就视为现有 OpenAPI 已能表达：

| 待审协议边界 | 必须表达及验证的内容 |
|---|---|
| 学生人工恢复 | 核验者/批准者/执行者的权限、身份核验证据与受控引用、目标学生和允许动作、专用命令输入/输出、拒绝与申诉状态；不得把邮箱或邀请记录单独当充分证明，也不得读取课堂正文或索取秘密 |
| 管理员人工恢复 | 仅本项目用户本人执行的授权规则、独立运维身份与批准单的绑定、目标管理员及恢复用途；其他 Admin 明确拒绝，不能让 generic admin 接口/网页创建、批准或消费该恢复能力 |
| 本人独立身份的实际绑定 | [会话与恢复设计](SESSION-RECOVERY-DESIGN.md) 提议 OS 受限运维身份＋预登记独立签名公钥及 nonce；公钥登记、身份绑定、签名许可内容、登记变更/撤销、信任来源和验签算法尚待 Astra 审查。不能只比较 role/email/session 或自行填写“本人”文本；Agent 不代用户登记真实凭据或执行恢复 |
| 签名许可及批准记录 | 提案绑定恢复用途、目标账号、当前 epoch、批准单/核验证据引用、动作/参数指纹、签名者/key ID、环境、签发/到期与唯一 nonce；批准记录不能把“用户本人手动执行”转成任何 Admin 可代执行。签名和能力的具体 wire/工具字段均未冻结 |
| 单次能力与受限重绑状态 | 有效期、父证明和scope、事务单次消费、防跨目标/用途/旧epoch重放；签发批准nonce、派生能力及fresh completion_nonce分开消费，期限关系显式审查。浏览器仅提交pending新因子证明，无finalize权限；原执行者手动专用命令complete，重新验证权限并绑定新因子/接收端/flow；新增state/受限cookie/token均须显式差异，不伪装authenticated。命令最终事务撤旧授权后要求完整重登 |
| 响应丢失、中断与故障 | 命令/HTTP 响应丢失不回放秘密，不重复执行已消费授权；重启后查询最小非秘密状态、重新核验或申请新许可的边界须明定。PG/KEK/必须审计失败安全回滚，不以恢复便利性跳 MFA、直接改表或临时放权 |
| 审计与秘密传输 | 审计只留必要操作状态、主体/目标、证据/批准单引用、许可/nonce消费结果及执行时间；密码、seed、恢复码、Cookie、票据和恢复秘密不得进入 CLI 参数、终端历史、通用日志/APM/Receipt/Outbox。工具秘密输入/输出通道及不回显行为也须审查 |

管理员人工恢复不设公共网页入口，不由普通 Web 管理会话发起运维级恢复授权；唯一管理员失去全部恢复因子仍使用上述本人独立身份边界。若无法验证该独立身份或缺少有效许可，保持不可恢复，不转为公开 bootstrap、`skip_mfa`、隐藏账号或手工 SQL 后门。学生和管理员恢复职责已经确认；具体凭据登记、协议字段和实现仍待技术审查与另行授权。

密码邮件重置保持：单次 token、重置后撤全部会话及派生授权、**不清除 MFA、不自动登录**。新因子未确认前保留旧因子；恢复码响应丢失不回放原明文。

### CD-04：remember_me、期限与 WS

`LoginRequest.remember_me` 当前是必填 boolean。产品已确认：学生 true/false **仅控制 Cookie 持久化**，不改变服务器 14 天空闲 / 30 天绝对期限；管理员最低密码 12、空闲 15 分钟 / 绝对 8 小时，Cookie 不持久化，remember_me 不延长期限或绕过 MFA。契约描述和 browser fixtures 应同步这些确定规则；管理员传 true 时如何明确表达/处理、Cookie 的 Max-Age/Expires 如何受服务器剩余绝对期限约束，以及浏览器关闭/恢复行为的验证方式，仍属于技术审查，不能把已确认政策重新列为备选。

现有 `WsTicketRequest` 已含 `channel`（events/capture）、可空 `class_session_id/capture_epoch_id/fence_token`；`WsTicket` 已有 `ticket/expires_at/path/subprotocol`。继续采用 Cookie＋精确 Origin upgrade 检查、首消息 `authenticate` 单次 ticket、`classroom.v1`，ticket 不进 URL。票据及握手期限是实验候选。票据所需 user/session/epoch 等服务端绑定不必全部暴露为客户端字段；若最终流程需要新字段、错误码或 WS 状态，单独提交差异，不能将“实现方便”视为扩展许可。

### CD-05：epoch 命名与实际授权边界

冻结 [安全文档](../architecture/05-SECURITY-OFFLINE.md) 使用 `auth_epoch`；[数据设计](../architecture/03-DATA-BACKEND.md)、[domain-model](../architecture/contracts/domain-model.json) 与实际 `users`、[Principal](../../../web/backend/classroom_web/domain/identity.py)、[authorized_transaction](../../../web/backend/classroom_web/infrastructure/database.py) 使用 `security_epoch`。建议在 ADR/映射说明中明确：两者指**同一用户安全失效版本**，持久字段继续以 `users.security_epoch` 为权威；不新增第二个 auth_epoch 列、不双增计数、不由前端提交有效版本。

前端 [ApiClient 的 scope.epoch](../../../web/frontend/src/api/client.ts) 是本机请求/账号切换失效计数，只控制迟到响应能否更新界面；它不是服务器认证凭据，也不能替代数据库 `security_epoch`。后续 mapper 必须明确该区别。

当前内部 `Principal` 构造器只做类型/格式检查；`authorized_transaction` 在内部 owner UoW 上校验主体有效期及数据库用户状态、role、security_epoch。它尚未查询正式 session/device 撤销与期限，不能作为“HTTP 已认证”的证据。03b 适配器必须由服务端验证 Cookie、session/device/epoch/因子再产生 Principal，并在写入事务内保持撤销一致性。普通 HTTP 不得选择内部 `OwnerContext` 路径或从 header/JSON 反序列化 Principal。

## 3. 版本、Changelog 与追踪提案

建议将产品已确认政策对应的完整认证差异作为契约包 **`0.1.0 → 0.2.0` 候选**审查，保留 `/api/web/v1`，最终版本由 Astra 决定。放宽密码输入对新服务是接收集合扩大，但旧验证器仍会拒绝 6–11；新增恢复 state、逐笔 scope 或 MFA 语义也可能破坏严格消费者。不能仅以“字段没删”认定全兼容。若最终拆为多个版本，每一版都须独立列明 schema/语义范围，避免把尚未审查的恢复协议字段混入最小密码变更；产品规则已确认不代替契约版本批准。

| 后续获准才可更新的资产 | 必须记录或同步的内容 |
|---|---|
| [contracts/CHANGELOG.md](../architecture/contracts/CHANGELOG.md) | 日期、需求 ID、旧/新版本、三个密码字段、本地名单暂停边界、期限/remember_me、受限恢复/逐笔授权协议范围、破坏性判断、旧消费者处理、迁移、Astra 审查与 Claude/Codex 确认 |
| [ADR-05 / TBD-08](../architecture/07-ADR-REGISTER.md) 与 [05-SECURITY-OFFLINE](../architecture/05-SECURITY-OFFLINE.md) | 用追加变更记录区分历史 12 字符初值、学生 6 字符、10–30人邀请制Alpha且补偿措施验证通过后才适用的条件风险接受、已确认管理员值/恢复职责与待审技术/实验结论；不用文档同步关闭 TBD |
| [requirements-traceability.json](../architecture/contracts/requirements-traceability.json) 与 [REQUIREMENTS_MATRIX.md](../REQUIREMENTS_MATRIX.md) | AUTH-01 的“12 字符初值”追踪到新产品决定及批准版本；AUTH-03 对应会话/设备，AUTH-04 对应 MFA/恢复，AUTH-05/06 对应权限/撤销；AUTH-07/08 保留后续后台/集成归属 |
| [openapi.json](../architecture/contracts/openapi.json) | 三处限制、角色条件/本地名单暂停、reauth 因子和R1/R2/R3约束、remember_me/管理员非持久Cookie、必要受限恢复/逐笔字段及错误/状态；独立运维工具协议不伪装成generic admin网页API；保持 strict schema 与 secret replay policy |
| [domain-model.json](../architecture/contracts/domain-model.json) | 仅对本阶段实际需要的 session/challenge/MFA/recovery/邮件/名单状态增量差异，以及恢复批准引用、独立执行身份/许可/nonce消费的最小必要持久映射；与实际迁移字段、权限一致，不一次创建全部逻辑表 |
| [events.schema.json](../architecture/contracts/events.schema.json) | 只有实际采用的新 WS 契约才变更；保留认证前禁止资源/音频、撤销后拒绝与 owner 游标；schema_version 与契约包版本分别记录 |
| [state-machines.json](../architecture/contracts/state-machines.json) | 现有五流状态机继续保留；认证/恢复若需新机须明确新增范围，不改 unknown/发送风险不变量 |
| [examples.json](../architecture/contracts/examples.json) | 同版本正反 schema fixtures 与认证语义用例；原 cases 原样保留，新增用例不得替代真实 PG/browser/WS 证据 |
| [pr-dependencies.json](../architecture/contracts/pr-dependencies.json) | 本包只读取 PR03b→03a 依赖和 TBD08 gate；本轮及拟议内部阶段不更改 DAG，不新增工作包 |

原 [PRD 归档及来源](../PRD_PROVENANCE.md) 作为历史依据保留，不改原 PDF 或要求重交 PRD。新产品决定应独立追加可追踪记录，记录确认者、日期、适用阶段与风险边界。

## 4. 生产者、消费者与 fixtures 同步清单

| 层 / 当前入口 | 后续同步要求 |
|---|---|
| 后端用例和密码适配器 | `IdentityService.register`、`Argon2PasswordHasher.hash` 与新增 reset/change 服务采用已确认角色规则及有效本地名单；无有效名单停三类新设入口、正常login不受影响；verify/rehash 不误套新设门槛，不重写已有 hash |
| 后端现有测试 | [test_identity_values.py](../../../web/backend/tests/unit/test_identity_values.py) 现有 11 拒绝、12/1024 接受必须在批准变更时明确修订理由并扩展角色/边界用例；[test_identity.py](../../../web/backend/tests/integration/test_identity.py) 与原邀请/激活/权限/失效 fallback 回归全部保留 |
| 前端类型生成 | [gen-contracts.mjs](../../../web/frontend/scripts/gen-contracts.mjs) 唯一读取正式 OpenAPI/事件 schema，生成 [openapi.ts](../../../web/frontend/src/generated/openapi.ts) 与 [events.ts](../../../web/frontend/src/generated/events.ts)；通过 `gen:contracts` 更新，再以 `--check` 检查漂移；不手改 generated 文件 |
| 前端校验 | TS string 类型本身不强制 minLength；表单须同步计数/角色反馈，服务端仍是权威。现有壳仅课程 GET client，不声称已有完整真实认证表单或 auth handler |
| Mock 与契约测试 | [fixtures.ts](../../../web/frontend/src/mock/fixtures.ts)、[start.ts](../../../web/frontend/src/mock/start.ts)、[runtime.ts](../../../web/frontend/src/mock/runtime.ts)、[contract.test.ts](../../../web/frontend/tests/contract.test.ts) 同步新增认证状态、名单安全暂停、已确认期限/remember_me、错误及空上下文；不把管理员人工恢复做成普通后台按钮，不伪造后端安全通过；原跨账号清理/unknown 风险显示保留 |

拟新增 fixture 组：三个密码字段的 5/6/11/12/1024/1025、非字符串/缺字段/未知 role、Unicode；管理员11拒绝/12接受；名单命中/无有效名单三类新设入口暂停而正常login可用；未启用 MFA 学生密码再认证成功、已启用学生/管理员缺第二因素拒绝；R1/R2/R3边界及R3目标/参数改变或二次消费拒绝；失效/错 scope/跨 session/跨 epoch 的再认证；学生/Admin人工恢复命令权限及批准单/签名/nonce/到期/中断；其他Admin冒充本人独立运维身份必拒；恢复受限上下文对课堂/导出/运营全拒；恢复码单次及秘密丢失 `SECRET_ALREADY_ISSUED`；学生remember_me仅控制持久化、管理员非持久Cookie且15分钟/8小时有效期、正常活动不延长绝对期限；WS票据错用途/跨课堂/重放/认证前帧拒绝。结构cases、工具协议用例与真实有状态机制验证分别标记，不能让JSON fixture宣称并发、Cookie、身份核验或事务安全。

## 5. 兼容、升级和安全回退

建议采用内部 Alpha 的同版本联调窗口：按已确认产品政策，先经 Astra 正式契约审查，再在明确实施授权下更新规范/fixtures/生产者/消费者，固定完整 SHA 与契约 hash；确认旧验证器对6–11的拒绝已消除、本地名单等补偿措施验证通过后才开放新输入。旧客户端如无法识别新恢复状态，应明确拒绝/提示升级，不把未知状态当 authenticated。请求schema的新增字段必须让旧服务保持拒绝，不可在后端静默忽略。恢复工具权限升级独立审查，不把历史“项目负责人批准或双人核验”候选当作现在允许其他Admin执行管理员恢复的依据。

已有 12 字符以上 PHC hash 无需重算或强制重置；现有 pending 注册不重新消费邀请或名额。新成本只在成功验证后有条件 rehash，需比较旧 hash/epoch，不能覆盖并发重置。若上线后的回退代码无法处理新状态，先停相关认证/恢复写入并保留数据；不得删除恢复审计、清 MFA、自动登录、把合法 6–11 凭据当损坏而锁死用户，或用旧代码绕过服务器校验。数据迁移按 expand/contract 和旧代码兼容证据审查，代码回退不自动降 schema。

## 6. PR-02b P3 的触发判断

依据 [TECHNICAL-DEBT.md](../pr02b/TECHNICAL-DEBT.md)：

| 开放项 | 当前准备阶段 / 只接认证 HTTP | 后续触发与停止条件 |
|---|---|---|
| `PR02B-P3-01` 同键 reconcile 与迟到 raw | 本轮无 HTTP 代码，不触发；将来仅接 login/session/MFA 不等于接 reconcile | **`POST /tasks/{task_id}/reconcile` / `reconcileTask` 接入 HTTP 前**，必须明确同键是首次结果回放还是继续协调原 Attempt 当前结果，经产品/契约判断和 Astra 审查，补“无 raw→K→迟到 raw→K→K”真实 PG 回归。不能在“统一挂路由”时顺便暴露它 |
| `PR02B-P3-02` 多 Attempt 历史未用 planned 终态 | 认证适配器不会自动扩展多 Attempt，不作为本启动包的一概阻塞 | 扩展多 Attempt 工作流前审定终态规则并补旧 raw＋新 planned＋无取消请求回归；若03b具体实施范围纳入该扩展，则先处理，不擅自关闭 |

正式 OpenAPI 已设计 reconcile 路由不等于运行时已经接入；当前 entrypoints 只有 worker，没有可据此认定完成的 HTTP auth 应用。下阶段实施清单必须逐条列出实际启用路由，以免误触关闭关口。本轮两项 P3 均保持 OPEN；它们不授权修改任务语义，也不阻塞本次文档提交。

继续保留：发送授权线性化点是 `mark_submitting` 成功 COMMIT；之后撤权可能发生在已获授权的实际网络调用之前。注销/重置阻止后续授权并保留 raw、Receipt、Outbox、fencing、unknown 与可能收费风险，不自动重发 Provider、切换 Provider、退款或清风险。

## 7. 本文件的核查口径

初始包已只读核对全部七份契约JSON的可解析性及上述相关字段/状态、当前identity/password/Principal/transaction代码、前端生成器与Mock/测试入口；本次文档修订落实用户最新确认政策并列明独立恢复契约缺口，未更新或生成任何正式契约/类型。参数实验、真实PG、HTTPS浏览器、WS/邮件、工具恢复演练及完整回归均不由本文件证明；初始包成绩不冒充本次重跑，以当前启动包实际验证记录为准。
