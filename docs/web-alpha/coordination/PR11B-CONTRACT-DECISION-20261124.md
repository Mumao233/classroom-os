# PR-11b 运营后台 HTTP 契约裁决

2026-11-24，负责人林珈诚，契约设计 Astra。基点 `80d2204d4578d6673a1ce74d1f60097d97b372cb`，HTTP **0.3.0 → 0.4.0**。本地对象库中的前端缺口说明取自 `feature/web-pr11b-admin@56130b7bc8febf8aef7e7512c2d98d4206e4f36a`。适配和独立复审另行交付。

## 一、六项管理写入如何取得与消费凭证

**问题与选项。** 0.3.0 在六个写操作上要求 `X-Reauth-Token`，签发入口的封闭联合类型却无法表达对应的写入。可以重定 PR-11a 已有的额度和平台目标，也可以保留其账号绑定，只给带资源路径的新增用途定义目标。采用后者，沿用已实现的服务端 HMAC 输入和客户端的写入 DTO。

| 未来写入的 purpose | target.kind 与 target.id | candidate_parameters.body |
|---|---|---|
| `adminAdjustQuota` | `account`，当前管理员 ID | `QuotaAdjustment`，被调额用户在 `body.user_id` |
| `adminUpdateSwitches` | `account`，当前管理员 ID | `AdminSwitchesPatch` |
| `adminCreateInvitation` | `account`，当前管理员 ID，邀请 ID 尚未生成 | `InvitationCreate` |
| `adminUpdateUser` | `user`，路径中的 `user_id` | `AdminUserPatch` |
| `adminRevokeInvitation` | `invitation`，路径中的 `invitation_id` | `InvitationAction` |
| `adminReissueInvitation` | `invitation`，路径中的 `invitation_id` | `InvitationAction` |

**消费者。** Claude 在确认提交时固定目标、完整请求体和未来写入的 UUID 幂等键。用当前密码、TOTP 及下列 operation 请求 `POST /auth/reauth`，该 POST 使用自己的幂等键。成功后将响应凭证放进写请求的 `X-Reauth-Token`，使用已绑定的写入键和原请求体，含 CAS 版本及理由。用户修改目标、内容或重建操作时，重新认证。凭证仅存内存，期限以服务端返回的 `expires_at` 为准。

```json
{
  "purpose": "adminRevokeInvitation",
  "target": {"kind": "invitation", "id": "44444444-4444-4444-8444-444444444444"},
  "candidate_parameters": {
    "idempotency_key": "22222222-2222-4222-8222-222222222222",
    "body": {"reason": "本次测试批次已结束"}
  }
}
```

**生产者。** `BillingAdmin.issue_reauth/apply` 对整个操作计算 `commitment('operation', operation)`，通过受限的 `PgBillingAdminRepository` 通道调用 SQL。签发核验当前管理员、密码/TOTP 证明、会话及其期限。消费从可信会话和实际路由、写入键、正文重建操作，在事务中核对角色、会话、设备、安全 epoch、凭证期限与承诺，再完成单次消费。已完成的幂等回执沿用各操作既有的重放规则。后续用户/邀请消费者还须比对实际路径 UUID，防止同一凭证改写另一条资源。正文、凭证以及可离线猜测的摘要均不得进入普通日志。

**落地边界。** 已有账本的额度与开关消费者须显式注入。独立开关、组摘要和批量更新见下文第 2 轮裁决。四个用户/邀请写操作尚缺完整的 HTTP 消费者、目标授权、审计和事务回执。有效管理员请求这些用途时，签发入口返回 `503 SERVICE_DISABLED`；学生不得取得管理凭证。前端应分别控制可用操作，不能仅凭 Meta 版本开放整页写入。

[账本浏览器服务](../../../web/backend/classroom_web/application/billing_browser.py) · [受限账本网关](../../../web/backend/classroom_web/infrastructure/billing_control.py) · [签发与消费事务](../../../web/backend/classroom_web/infrastructure/billing_integration.sql) · [HTTP 组合入口](../../../web/backend/classroom_web/entrypoints/http.py)

**2026-11-27 后端接入。** 显式注入管理服务的 HTTP 0.4.0 应用现已挂载表中的六项写入。四项新增用途沿用上表中的目标、写入键和 DTO。创建邮箱邀请在核验因子前按注册服务的规则检查邮箱，原始拼写参与操作承诺。用户状态更新校验版本，停用同时推进安全 epoch 并撤销会话。创建与重签返回的随机令牌只在首次成功响应出现；同键同操作再试取得 `409 SECRET_ALREADY_ISSUED`。重签保留邀请 ID，换发新令牌，并使源自前一令牌的待激活注册失效。用户和邀请的成功写入、受限审计、凭证消费与回执一同提交。HTTP 字段和版本沿用 0.4.0。

## 二、邀请操作的理由

**问题与选项。** 创建只收 kind/email，撤销与重签使用 `Empty`。可选理由能接纳历史请求，却允许新操作只留下固定动作名。04-CONTRACTS-FLOWS 的运营行已要求“记理由”，AUTH-07 要求管理操作 MFA 与审计，现有用户、额度和开关写入都带操作者理由。因此三项均**必填**，无需新增产品规则。

`InvitationCreate.reason` 和 `InvitationAction.reason` 为 1–500 个 Unicode 码点，至少一个非空白字符，任何位置都不能含 U+0000。绑定客户端提交的确切文本。旧创建体和旧空对象均与新 schema 不兼容。在已挂载的生产者上，缺失、超长或纯空白均应在写入前返回 `422 VALIDATION_FAILED`。前端须先收集理由，再请求再认证。后续后端须把业务写入与受限审计记录放入同一事务，保存操作者、操作、资源引用、时间和真实理由。邀请 token 只在首次成功响应展示。创建和重签丢响应沿用 `409 SECRET_ALREADY_ISSUED`，不能通过审计或收据取回明文。

[运营与秘密响应规则](../architecture/04-CONTRACTS-FLOWS.md) · [AUTH-07](../REQUIREMENTS_MATRIX.md)

## 三、风险合计和准入

**问题与选项。** 如果固定与已计提额含本期供应商费用，就应从合计中去掉已结算。实际 `usage_view` 直接投影 `platform_budgets.fixed_microyuan`；`lock_budget` 再从敞口表独立汇总 held、unknown、settled。裁决采用以下六个字段，金额均以十进制整数微元字符串传输（1 元 = 1,000,000 微元）。

| 字段 | 同一预算周期内的账本含义 |
|---|---|
| `budget_microyuan` | 平台总预算，含安全预留。60%、80%、100% 告警的分母 |
| `fixed_and_accrued_microyuan` | 周期固定基础设施占位，只取预算行的 fixed 值。供应商已知结算单列 |
| `settled_microyuan` | 各敞口已知费用之和，含仍在执行或待核对任务的已知部分 |
| `reserved_microyuan` | reserved/submitted 工作的剩余预留，每笔按“预留上界 − 已知结算”取值，下限为 0 |
| `unknown_microyuan` | 状态为 unknown 的剩余风险，每笔同样扣除已知结算，下限为 0 |
| `safety_reserve_microyuan` | 普通准入须从总预算扣下的额度 |

**风险 = 固定占位 + 已知结算 + 剩余预留 + unknown 余量。** 前端按整数运算。例如 fixture 的四项为 100、70、20、30 微元，风险就是 220。总预算 1,000 微元、安全预留 200 微元时，风险占比为 22%，平台普通准入上限为 800 微元。比较阈值可直接使用 `risk × 100 ≥ budget × threshold`。后端还按单个预算池核验新预留，本次沿用原账本算法。

[账本聚合与准入 SQL](../../../web/backend/classroom_web/infrastructure/billing_schema.sql) · [11a 账本说明](../pr11a/README.md)

## 版本、回退与后续

邀请理由收紧请求结构，管理用途扩展封闭联合类型，因此依照 0.3.0 先例升次版本。API 前缀为 `/api/web/v1`。版本切换时，客户端须同时更新生成类型、Mock、用途/目标映射、邀请表单及用量显示。回退时成对恢复 0.3.0 客户端与服务并关闭依赖新契约的管理写入。无数据迁移。

- **Codex / PR-11b 后端**补齐用户更新和三项邀请写入的鉴权、并发、幂等、同事务审计、单次秘密响应以及对应的真实 HTTP 与 PostgreSQL 验收。现有 03a 邀请内部端口不足以直接挂载。
- **Claude / Draft #38** 按表中映射对接可用生产者，更新未挂载态与 Mock。等待独立复审和同版联调后再开放相应操作。
- PR-10a 先前把 PR-07a 引用字段暂排在 0.4.0 窗口。本次仅交付这三项后台裁决，引用字段仍由独立候选负责，公开版本号待该批实际冻结时分配。

### 后续 · 关闭新采集后的租约处理（2026-11-27）

首次采集和停止后的再次采集会创建 epoch。现有接管同样创建 epoch，并按账号锁定未释放的租约，在 `confirm_takeover` 为真且 `expected_lease_id` 匹配时替换它。持有旧租约的设备可以在租约过期后接管，也可以接管到另一课堂。暂停后恢复会在同一事务中释放原租约并创建新 epoch。续租、有效回执重放和已采集数据的读取各有独立路径。

两种处理会产生不同的用户可见结果。保留全部既有接管时，关闸后持有旧租约的用户仍可以开始另一课堂的采集。仅保留同课堂、未过期租约的接管时，这两类原本有效的接管会被拒绝。建议关闸后允许同课堂有效租约续租、接管和暂停恢复，阻止新课堂采集与过期租约重新开始；该例外范围待负责人确认。确认前沿用额外复审的保护，GET 返回存储值，PATCH 尝试改变 `new_capture_enabled` 时整笔返回 `503 SERVICE_DISABLED`；同值 PATCH 沿用 CAS。此次未改采集准入或公开契约。

[创建、接管与暂停恢复](../../../web/backend/classroom_web/application/capture.py) · [租约锁与课堂范围](../../../web/backend/classroom_web/infrastructure/capture_repository.py) · [采集回归](../../../web/backend/tests/integration/test_capture_recovery.py)

[HTTP 契约](../architecture/contracts/openapi.json) · [合成正反例](../architecture/contracts/examples.json) · [上次契约裁决](PR10A-CONTRACT-DECISION-20261122.md)

## 第 0 轮独立复审核实 · 2026-11-24

复审对象为 `57fc07ebc6db605d3dc64da86492edfbabac545c`。下表的代码依据均指该提交。六项修复归入尚未发布的 HTTP 0.4.0。新约束与实际写入头、可承诺的整数范围、审计理由要求对齐；同版生成类型、合成样例和 11a 候选的基准散列一并更新。

| 项目 | 判定及原代码依据 | 修复与回归覆盖 |
|---|---|---|
| 1 · 写入方法 | **成立。** `AuthClient.request` 遇到 body 一律选 POST，`change` 签发及消费凭证都调用它。OpenAPI 把开关更新和用户更新定义为 PATCH。 | 两种更新以 PATCH 写入，其余现有用途继续用 POST，签发端仍为 POST。传输用例核对六种管理员写入与一个账号操作的两次 HTTP 方法、路径、键和正文。 |
| 2 · 未来幂等键 | **成立。** 六个候选键只声明 UUID 格式，可接收大写十六进制。HTTP 写入口却检查 `str(UUID(key)) == key`，`BillingAdmin` 按原字符串计算承诺。 | 共享 `CanonicalIdempotencyKey` 用于写入头和六个管理候选键。大小写错误在 schema 校验时返回 422，不触发验证因子；客户端传原始规范小写键。正反例含实际的 a–f 字母。 |
| 3 · 空白理由 | **成立。** `AdminSwitchesPatch`、`QuotaAdjustment` 只设长度，签发可放过纯空白；`BillingAdmin.apply` 到写入时才执行 `.strip()` 检查。 | 两个 DTO 引用 `AdminWriteReason`。用户更新和邀请操作共用该规则。签发和消费沿用相同 DTO，检查位于业务调度及因子验证之前。 |
| 4 · 承诺整数上限 | **成立。** 候选写入 DTO 仅设 minimum。`Credentials.commitment` 调用 `rfc8785.dumps`，超出安全范围的整数会抛异常，HTTP 通用异常处理可返回 503。 | 两个 `base_revision`（用户、开关）和 `additional_units`（额度）统一接受 1 至 9,007,199,254,740,991。样例覆盖上限及上限加一，HTTP 回归要求越界在验证凭据前返回 422。 |
| 5 · 容量工具的委派 | **成立。** 调度器对 admin 用途显式传 `admin_reauth=billing_admin`；`MeasuredAuth.reauthenticate` 的旧签名只收 ctx 和 body。 | 将关键字原样传给父类，保留计时关联的 ContextVar 及 finally 恢复逻辑。专门的单元测试检查参数转发与上下文恢复，无需发起容量负载。 |
| 6 · 跨运行时空白字符 | **成立。** 旧邀请 schema 使用 `\S`。Python 将 U+0085 视为空白、将 U+FEFF 视为非空白，ECMAScript 的这两项判定相反。 | `AdminWriteReason` 以显式字符范围表示 Unicode White_Space，加上 U+FEFF 及既有 Python trim 处理的 U+001C–001F。长度仍为 1–500 个码点。仅含 U+0085 或 U+FEFF 均无效，夹有可见文字则有效，提交的原文参与承诺。三种样例均由 Python 与 Ajv 的现有入口读取。 |

[前端传输](../../../web/frontend/src/api/auth-client.ts) · [HTTP 校验和调度](../../../web/backend/classroom_web/entrypoints/http.py) · [凭据承诺](../../../web/backend/classroom_web/infrastructure/auth_crypto.py) · [容量验证工具](../../../tools/pr03b/product/capacity_service.py)

## 第 1 轮独立复审核实 · 2026-11-24

复审输入为第 0 轮修复后的未提交工作树。以下三项均成立，仍属 0.4.0 的发布前修正。

| 项目 | 原因与修复 | 回归覆盖 |
|---|---|---|
| 1 · 非管理凭证的未来写入键 | 12 种非管理员用途仍仅校验 UUID 格式。`AuthService.reauthenticate` 对操作原文计算承诺，消费端从实际写入头重建操作。现有的 6 种管理员用途和这 12 种用途均引用写入头的 `CanonicalIdempotencyKey`。学生恢复的 4 个 `op_id` 标识独立的本机控制操作。 | 共享 fixtures 新增 `logoutAll` 大写候选键反例，由 Python 与 Ajv 读取。 |
| 2 · 浏览器 Mock 接受大写键 | MSW 把原始请求头交给 Mock 后端，原有幂等处理仅检查键是否存在，部分写入直接走业务处理。现由共同入口在分派写操作前验证规范小写 UUID，非法输入返回 `422 VALIDATION_FAILED`。 | 同一负样例分别交给 schema 和 Mock 写入处理器，核对拒绝结果和写入前后的状态，再用合法键验证正常写入。 |
| 3 · 成功测试的凭证编码 | 原测试的 43 个 `p` 可解码，但最后一个字符含非零填充位；按后端 `unb64` 的解码再编码规则会被拒绝。成功响应统一使用 32 个确定性合成字节编码成无填充 base64url。 | 保留传输用例对 `X-Reauth-Token` 原样转发的断言。 |

[后端签发与消费](../../../web/backend/classroom_web/application/auth.py) · [Mock 写入入口](../../../web/frontend/src/mock/backend.ts) · [传输测试](../../../web/frontend/tests/auth.test.ts)

## 第 2 轮独立复审核实及开关裁决 · 2026-11-24

复审对象为 `ddf5da7165299313ba9821a9cb0e00f5ececc902`。六项均成立，以下变更归入未发布的 HTTP 0.4.0。

### 1 · 开关分项、组摘要和 PATCH（P1）

**依据与选项。** `billing_api.switch_view()` 原先对组内两项取 AND，HTTP 又过滤了分项。`PgBillingRepository._switches` 的准入条件是总闸和本次用途对应的单项同时开启。改成 OR 可以消除“单项可运行、整组显示关”，但单个布尔仍看不出哪项可用。三态能显示“部分开启”，仍需另一条信息定位具体服务。裁决定为公开现存分项，同时保留组别批量操作。

| 公开字段 | GET / PATCH 的含义 |
|---|---|
| `billing_enabled` | 收费总闸。关闭会阻止新的计费准入，保留各分项原配置。 |
| `new_capture_enabled` | 新采集的独立配置位。2026-11-26 额外复审后，GET 显示存储值，采集准入接入前，PATCH 改变该值返回 503。 |
| `asr_streaming_enabled`、`asr_filetrans_enabled` | 分别控制实时 ASR、文件重转写。 |
| `ai_interaction_enabled`、`ai_summary_enabled` | 分别控制 AI 互动（问答、讲解）和总结。 |
| `asr_enabled`、`ai_enabled` | 读取为对应组两项配置的 OR。两项都关才显示关，一开一关应按分项显示“部分开启”。PATCH 显式写组字段会把两项都设为该值。 |

六个独立布尔在 GET 和成功 PATCH 响应中均必返。`AdminSwitches` 表达配置，额度视图中的组摘要另与收费总闸取 AND。具体请求还要通过用途、额度和预算检查。读取幂等回执时，从回执自身保存的分项重算摘要，保留当时的版本和配置。

PATCH 只提交有意更改的项，省略项保持原值。一个请求至少指定一项设置；同组的组字段与任一成员混写时，即使二者同值也返回 422。跨组组合允许。整组配置共用一个 `base_revision`，在同一行锁内比对版本、应用所有变更并递增一次。过期版本返回 `409 REVISION_CONFLICT`，同键同体的已完成请求沿用既有回执。注册开关、人数档位沿用现有规则。

**兼容性与前端要求。** 既有组 PATCH 可继续批量启停。旧版严格响应校验器需更新生成类型和 schema，旧 UI 也需按两个分项区分“部分开启”和“全部开启”。#38 编辑单项时只发送该分项，避免回传组摘要连带改动另一项。11a 候选组合沿用公开定义。版本号保持 0.4.0。

### 2–6 · 写入重试、签发输入及额度边界

| 发现 | 判定、代码依据与处理 |
|---|---|
| 2 · 结果未知后的键（P2） | 成立。`AuthClient.change` 先前每次调用都生成新键。六项管理员操作现在要求调用方在首次确认时生成并保留目标、正文与键。结果未知后停止，由操作者显式重试。重试使用当次密码/MFA 申请新凭证，绑定并发送相同目标、正文和写入键。更改内容属于新操作。已有秘密响应的回放限制继续生效。 |
| 3 · 账号目标 UUID（P2） | 成立。签发按原文与 `str(principal.user_id)` 比较，大写可晚于 TOTP 才被拒绝。所有 `kind=account` 的签发目标采用规范小写、含连字符的 UUID schema，在验证因子之前返回 422。共享样例包含有 a–f 字母的大小写对照。 |
| 4 · 整数的 JSON 写法（P2） | 成立。JSON Schema 接受整值小数，旧路径可将它写为 JSONB 的 `1.0`，之后的 PostgreSQL 文本转 bigint 失败。HTTP 在校验成功后统一规范三个管理 DTO 中的整数域：用户和开关的 `base_revision`、额度的 `additional_units`。签发端与消费端在首次承诺、数据库序列化前完成转换。原始 JSON 集成用例覆盖小数、指数及整数重放。 |
| 5 · 理由中的 U+0000（P2） | 成立。签发只需承诺，可以放过 PostgreSQL text/jsonb 无法保存的 NUL。共享 `AdminWriteReason` 现在拒绝任何位置出现 U+0000，保留其余有效原文。嵌入正常文字的反例同时覆盖签发 DTO 和写入 DTO，均应在调度前返回 422。 |
| 6 · 调整后的额度（P3） | 成立。此前只校验增量。数据库锁定同一额度行后，先识别成功回执，再比较增量与 `2^53−1 − 当前上限`。超额返回 `422 VALIDATION_FAILED` 并回滚本次写入；达到上限的成功回执仍可重放。`QuotaBucket.limit` 与 `remaining` 在公开 schema 中明确安全整数上限。并发测试让两个独立事务竞争最后一个单位。 |

[开关与凭证事务](../../../web/backend/classroom_web/infrastructure/billing_integration.sql) · [额度行锁](../../../web/backend/classroom_web/infrastructure/billing_schema.sql) · [准入与用量投影](../../../web/backend/classroom_web/infrastructure/billing_repository.py) · [公开 HTTP 校验](../../../web/backend/classroom_web/entrypoints/http.py) · [客户端显式重试](../../../web/frontend/src/api/auth-client.ts)

### PR-05a 设备目标的兼容处理

可信缓存的消费路径决定了设备 ID 的格式。`CacheKeyService.release` 先校验正文 `device_id` 为规范小写、带连字符的 RFC UUIDv4，并核对当前设备，再从该值重建 `target.id` 和完整写入承诺。公开 `unlockCache` 和 05a 候选 `recoverCache` 的目标 ID 与绑定正文的设备 ID 因此共用 `CacheDeviceId`。同一会话设备在两处须提交相同值。缓存操作中原本就不能消费的大写或非 v4 设备 ID 现在提前返回 422。HTTP 保持未发布的 0.4.0。

`revokeDevice` 按路径中设备 ID 的原始文本重建承诺，数据库按 UUID 值核对设备。因此签发目标与撤销路径必须使用同一拼写，原有 UUID 格式继续有效。`trustDevice` 当前未开放，保留原契约。前端对当前设备可直接复用会话返回的小写 ID。

05a 构建器按用途名选取 `revokeDevice` 的设备操作结构，再从对应缓存写入 DTO 取得设备 ID 约束。既有 `unlockCache` 的唯一性和整分支等值检查继续执行。组合回归覆盖顺序变化、已有或缺失解锁分支，以及重复、篡改目标或篡改正文时的拒绝。

核查了 `tools/`、`web/backend/tests/` 和 `web/frontend/tests/` 中的联合类型构造、顺序索引和间接引用。05a 是唯一按首分支派生再认证操作的构建器。11a 按用途名构造缺失分支，索引切片仅用于导出新追加的分支。07a/07b 复制整个契约以扩展各自 DTO，07b 样例校验完整再认证联合。前端契约测试按用途筛选并要求结果唯一。

[缓存凭证消费](../../../web/backend/classroom_web/application/cache_keys.py) · [05a 候选构建器](../../../tools/pr05a/formal/candidate.py) · [组合与可信缓存回归](../../../web/backend/tests/integration/test_trusted_cache_browser.py)

## 额外复审核实 · 2026-11-26

复审输入为 `14201d2080f9014b8656525c527e2170ead77467`。逐项核对生产者、消费者和实际路由后，六项均成立。以下更新仍属未发布的 HTTP 0.4.0。

| 发现 | 原代码依据 | 本轮裁决与回归 |
|---|---|---|
| 1 · 新采集开关（P1） | `apply_switch_body` 可保存 `new_capture`。`PgCaptureRepository.scope` 检查采集策略和保留期限，`CaptureService.acquire_in_transaction` 随后创建 epoch 和租约，全程未读取该配置位。 | 在管理写事务锁定开关行后，尝试改变 `new_capture_enabled` 时整笔返回 `503 SERVICE_DISABLED`。GET 显示存储值，同值 PATCH 走既有 CAS。这样可避免把账本变成采集模块的新依赖，也可避免改变续租和接管语义。回归覆盖开→关、关→开、同值与其他开关混写，以及拒绝后的版本、审计、回执和凭证。前端应将该项显示为只读配置。 |
| 2 · 重新登录后的开关回执（P2） | `apply_admin` 原先用会话 token 摘要查找 `preauth_receipts`。新会话漏查，底层开关审计发现重复后，`apply_switch_body` 仍返回当时的全局视图。 | 新快照按当前管理员 ID、操作和写入键保存。回放复核当前账号、角色、TOTP 会话、设备、安全 epoch、绑定凭证和完整正文承诺。同键异体返回 409。历史会话格式按管理员归属读取首份快照。只有审计且缺少可恢复快照时返回 `503 RESULT_UNKNOWN`。开关审计按操作者和键唯一，其他账本动作保留原唯一性。用例覆盖登出再登录、期间另有修改、旧会话凭证、角色降级、历史多份回执和同键隔离。仅更新 11a 候选 DDL。 |
| 3 · 精确整数（P2） | `strict_json` 默认通过二进制浮点解析小数。到 schema 和 `normalize_admin_write_integer` 时，原始分数可能已成为整数。 | 三个管理用途的修订号或额度增量及相应签发路径，按精确十进制检查整数性和范围 1 至 9,007,199,254,740,991，再统一为整数。原始 HTTP 字节用例覆盖舍入后看似合法的分数、上下限外的小数、大指数、精确上限，以及 `1.0`、`1e0` 与整数之间的签发、写入和回放。 |
| 4 · 课堂实时能力（P2） | `PgBillingRepository.quota` 对组内分项取 OR，再与收费总闸取 AND。`ClassroomLive.Preparation` 用两个组位显示「✓」和「可按需使用」，文件重转写或总结单独开启也会触发。 | 课前准备页从两个额度桶读取转写分钟和问答、解释次数。整组关闭时提示暂停。组摘要为真时仅显示余额，额度桶缺失时显示暂无信息。新增前端用例覆盖组开但实时能力未知、单组关闭以及缺少额度桶。沿用学生已有的 `QuotaView`。 |
| 5 · 缓存未来写入键（P2） | `unlockCache` 签发 DTO 和 POST 的写入头只要求规范 UUID，`CacheKeyService.release` 对实际写入键调用 `canonical_uuid`，要求 UUIDv4。 | 缓存签发与实际写入头共用规范小写 UUIDv4。05a 恢复覆盖层继承解锁头，并据其 schema 构造两种凭证。HTTP 在密码/TOTP 及缓存服务分派之前拒绝 v7。用例核对解锁、恢复、覆盖层冲突和普通写入键的适用范围。 |
| 6 · 邀请类型与邮箱（P2） | `InvitationCreate` 原先允许两种类型都省略或附带 email。`IdentityService.create_invitation` 已按 kind 检查邮箱是否存在。 | 共享 DTO 改为两个互斥分支。`email_bound` 必填有效 email，`single_use` 必须省略 email。两分支均必填理由。写入 DTO、再认证嵌套样例、生成的 TypeScript 联合类型及因子前拒绝回归同步。四项尚未挂载的管理写入仍按原裁决处理。 |

[采集策略](../../../web/backend/classroom_web/infrastructure/capture_repository.py) · [租约创建](../../../web/backend/classroom_web/application/capture.py) · [开关事务与回执](../../../web/backend/classroom_web/infrastructure/billing_integration.sql) · [HTTP 入站校验](../../../web/backend/classroom_web/entrypoints/http.py) · [课前准备页](../../../web/frontend/src/pages/classroom/ClassroomLive.tsx) · [邀请处理](../../../web/backend/classroom_web/application/identity.py)

## VERIFICATION · 2026-11-24

**初版提交 `57fc07e` 的指定门禁已全部通过。** 以下三轮使用同一源码包。第三轮通过冻结检查、候选组合检查、生成物检查、前端契约测试、全量 Vitest 和后端定向 PostgreSQL 测试。前两轮因来宾启动脚本和日志编码中断。第 0 轮复审后的修复另行验证，见本节末尾。

### 首轮 · 来宾 Python 启动失败

送测分支 `feature/web-contract-admin-reauth`，父提交 `80d2204d4578d6673a1ce74d1f60097d97b372cb`。源码包固定了工作树的待提交改动及验证脚本，共 1,007 项。本段在回收证据后补记。

| 环境 | 实际命令或动作 | 结果 |
|---|---|---|
| Windows 宿主 | 在 `web/frontend` 运行 `node scripts/gen-contracts.mjs` 及 `node scripts/gen-contracts.mjs --check` | 均退出 0，生成类型与公开契约一致 |
| Windows 宿主 | `node node_modules/typescript/bin/tsc --noEmit --pretty false`；`node node_modules/eslint/bin/eslint.js tests/contract.test.ts tests/pr10b-contract.test.ts src/mock/fixtures.ts --max-warnings 0` | 均退出 0 |
| Windows 宿主 | Python 解析 6 个改动的 JSON 和 7 个 Python AST，检查文内源文件链接及 11a 候选指向的 OpenAPI 散列；`git diff --check` | 均退出 0 |
| VMware broker | `tools/pr03b/vmware/submit.ps1 -Id b11-62b8c864b31d -Kind run -Script <broker>/scripts/b11-62b8c864b31d.ps1 -TimeoutSeconds 1500` | 10:02:29–10:02:35 UTC，传输成功，来宾退出 1。来宾已核对源码 ZIP 的 SHA-256 并解包 |
| VMware 来宾 | `C:\j\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe -I -B -m venv --without-pip <snapshot>\work\pr11b-contract\venv` | 解释器报 `No Python at`，引用 J: 上 broker 的 `payload\python-base\python.exe`。本次启动脚本漏了既有脚本中的来宾盘符初始化。未到达逐文件验证或任何测试命令 |
| VMware broker | `submit.ps1 -Id b11-62b8c864b31d-evidence -Kind copyFrom -GuestPath <snapshot>\work\pr11b-contract\evidence.zip` | 退出 0，回收 ZIP 的散列与来宾输出一致，包内 SOURCE.json 与宿主清单相同 |

尚待来宾执行的命令为 `python -I -B tools/overnight/check_contracts.py`、`python -I -B tools/pr07b/check_contract_candidate.py`、`node scripts/gen-contracts.mjs --check`、前端 `test:contract` 脚本及 `vitest run --config tests/vitest.config.ts`。后端入口为 `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin> --test-target …`，目标覆盖账本策略和集成、签发/消费、管理员与邀请权限、07b 契约、可信缓存浏览器版本回归。全部参数已保存在源码包的 `work/pr11b-contract/guest/verify.py`。本次来宾执行测试数 **0**，PostgreSQL 未启动。

证据目录为本工作树 `outputs/pr11b-contract/`，broker 原始回执、stdout、stderr 和来宾 ZIP 位于子目录 `b11-62b8c864b31d/`。源码包及启动脚本位于 `work/pr11b-contract/b11-62b8c864b31d/`。

| 文件 | SHA-256 |
|---|---|
| 公开 openapi.json | `ce08ad4e9f11bfe6ca83ab253c24a5fd7338281bf90e0c25bc6b112ac22ad195` |
| 生成 openapi.ts | `33f42915e37568ddc3cb55f63e24aa6ca8aa49c1a8eab28385efbaa13515bfe1` |
| host-static.json | `40a89ee4b2db433128dc9a5f811ac9e64c6956dddd88ba87107bcdfbba7e39f6` |
| source.zip | `993ddf7e04be2d290e38330351f3ad8d0801d5ca813139544d5f178a010be99e` |
| guest.ps1 | `0651cbfff8ca1ff455ee75b051e681146ab12da03bb989abb324b8bc5495172c` |
| run 回执 | `3d91dcc373f79b993c891cc86e5714dfc5ae254bead4b52f7e3b4382ec6536bd` |
| evidence.zip | `b60e8f7a207911ae43e8cf6a50e6e04d7b40788ba36b655ecbe50aa0188e3f43` |

**首轮恢复决定。** 负责人选择复用原包，在来宾 J: 缺席时按 `tools/pr04a/guest.ps1` 建立映射；仅在 J: 有其他占用时选用备用解释器。

### 第二轮 · 契约检查通过，验证脚本转印日志失败

作业 `b11-9df2928c3d61`，2026-11-24 10:07:55–10:08:43 UTC。broker 执行 `submit.ps1 -Id b11-9df2928c3d61 -Kind run -Script <broker>/scripts/b11-9df2928c3d61.ps1 -TimeoutSeconds 1500`。宿主核对原 ZIP、清单和随包的 verify.py 散列；来宾核对 ZIP、清单中 1,007 项文件和前端依赖锁。来宾记录映射 `J:\: => C:\j`，原预置 Python 3.12.14 成功启动。Node v24.19.0，Vitest v4.1.12。

以下命令在本轮快照根目录执行，Node 契约检查及 Vitest 的工作目录为 `web/frontend`。`<out>` 为来宾快照内的 `outputs/pr11b-contract`。每条已执行命令的完整路径、退出码和耗时均在证据包的 `commands.json`。

| 命令 | 通过 | 失败 | 跳过 | 执行结果 |
|---|---:|---:|---:|---|
| `python -I -B -m pip check` | 1 | 0 | 0 | 退出 0 |
| `python -I -B tools/overnight/check_contracts.py --output <out>/contracts.json` | 9 | 0 | 0 | 退出 0 |
| `python -I -B tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` | 116 | 0 | 0 | 退出 0。review 与 billing-and-review 两个组合各执行相同的 20 个正例、38 个反例 |
| `node scripts/gen-contracts.mjs --check` | 1 | 0 | 0 | 退出 0 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` | 192 | 0 | 0 | 退出 0，三个文件各通过 186、3、3 项 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` | — | — | — | 未启动 |
| `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin> --test-target …` | — | — | — | 未启动，未创建 PostgreSQL 集群 |

**失败位置。** 前端契约测试的子进程已退出 0，并已保存完整日志。随后 `work/pr11b-contract/guest/verify.py:29` 将其末尾的 `✓` 打印到 cp1252 标准输出时抛出 `UnicodeEncodeError`。来宾以 `python -I -B` 执行验证脚本，隔离模式忽略环境中的 `PYTHONIOENCODING`。broker 传输成功，整轮来宾退出 1。按停止条件保留工作树并暂停提交。下次可将启动脚本中的验证命令改为 `python -I -B -X utf8 work/pr11b-contract/guest/verify.py`，沿用同一源码包和原测试参数。

原始证据位于 `outputs/pr11b-contract/b11-9df2928c3d61/`。使用 `submit.ps1 -Id b11-9df2928c3d61-evidence -Kind copyFrom` 回收，证据包的宿主 SHA-256 与来宾输出一致。

| 文件 | SHA-256 |
|---|---|
| 两轮共用 source.zip | `993ddf7e04be2d290e38330351f3ad8d0801d5ca813139544d5f178a010be99e` |
| 共用 SOURCE.json | `d090229ba3ff9867ab37381b60fa52f62085698e76b7299b1299efa4546d2d37` |
| 第二轮 guest.ps1 | `fc5435ca9a377389e43ca7164d2f0ce0da0a23e9ece8ad896ef76c62f61c1b57` |
| run 回执 | `3afd9821f9870548badd4110cc59dcf1df3b42e697e4adbd2bc9bec3664d8c2e` |
| copyFrom 回执 | `9a799e2b712f44bb498cbd6167dfd92305cda3506fbb6d58d8790451435f2fc9` |
| evidence.zip | `7f8e74869b661c82ffd4858312291fbd862b78bbe9994f8de018fe51d67db62a` |

### 第三轮 · VMware 完整验证通过

2026-11-24 10:12:36–10:14:14 UTC，broker 作业 `b11-15bac96fd837` 传输成功，来宾退出 0。复用前两轮的源码 ZIP、SOURCE.json、verify.py 和测试参数。启动命令改为 `python -I -B -X utf8 work/pr11b-contract/guest/verify.py`。来宾核验 1,007 项文件和前端依赖锁，使用 Python 3.12.14、Node 24.19.0、Vitest 4.1.12。

| 来宾命令 | 通过 | 失败 | 跳过 | 证据 |
|---|---:|---:|---:|---|
| `python -I -B -m pip check` | 1 | 0 | 0 | 依赖检查退出 0 |
| `python -I -B tools/overnight/check_contracts.py --output <out>/contracts.json` | 9 | 0 | 0 | 含 158 个合成契约样例 |
| `python -I -B tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` | 116 | 0 | 0 | review、billing-and-review 各通过 20 个正例与 38 个反例 |
| `node scripts/gen-contracts.mjs --check` | 1 | 0 | 0 | 生成类型一致 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` | 192 | 0 | 0 | 3 个文件 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` | 587 | 0 | 0 | 16 个文件，包含上一行的契约测试 |
| `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin>` 加下列 10 个 `--test-target` | 222 | 0 | 0 | JUnit 同为 222 项、0 错误。1 条 Starlette TestClient 使用 httpx 的弃用警告 |

后端按下列顺序传入目标，每行对应一个 `--test-target` 参数。测试覆盖公开契约中的额度、开关签发与消费、缺少消费者时拒签，以及非零账本的风险四项和。

```text
tests/unit/test_pr07b_contract.py
tests/unit/test_billing_policy.py
tests/unit/test_auth_commit.py
tests/integration/test_billing.py
tests/integration/test_billing_integration.py
tests/integration/test_authentication.py
tests/integration/test_auth_review_fixes.py
tests/integration/test_identity.py::test_invitation_always_activates_student_and_student_cannot_manage
tests/integration/test_identity.py::test_admin_management_rechecks_all_trusted_auth_evidence
tests/integration/test_trusted_cache_browser.py
```

隔离 PostgreSQL 17.11 监听 `127.0.0.1:50283`。外网拒绝探针按预期退出 1，runner 核验了拒绝原因。initdb、启动、pytest、停库均退出 0，PG 日志记录已关闭。真实供应商调用计数为 0。

broker 使用 `submit.ps1 -Id b11-15bac96fd837 -Kind run -Script <broker>/scripts/b11-15bac96fd837.ps1 -TimeoutSeconds 1500` 送测，以 `-Id b11-15bac96fd837-evidence -Kind copyFrom` 回收原始 ZIP。本工作树 `outputs/pr11b-contract/b11-15bac96fd837/` 保留证据包、回执、stdout、stderr 及核验摘要。包内 `commands.json` 记录各子命令的完整路径、返回码和耗时。宿主按清单核实待提交的源码、契约、生成物和测试，裁决文档仅追加和更新本节验证记录。

| 文件 | SHA-256 |
|---|---|
| 三轮共用 source.zip | `993ddf7e04be2d290e38330351f3ad8d0801d5ca813139544d5f178a010be99e` |
| 第三轮 guest.ps1 | `122e870a18559e8df8da6d687b5ec77e6b0d1d131ba55cd8da9b98b13a966203` |
| run 回执 | `b439751e1efd468cf285e1d69c2533912018b0efeef61c7415ee4a40ce49400c` |
| copyFrom 回执 | `befd633f0d87213088b70f1397fbb5ab860eced27dccafd6040bf51f14bcfed3` |
| evidence.zip | `000a8dab1d14edd3929b18c73e1c5c1f33307ab35af2741dbc1627a16324e46c` |
| 后端 pytest.xml | `c5d4ef64b39a0f72bfe4fadcb538e82592b41261213a70f8e5753276bcb75870` |

暂存核验时，3 个 TypeScript 文件（Mock fixtures、contract.test、pr10b-contract.test）在来宾包中使用 CRLF，Git 按仓库文本属性存为 LF。逐文件比对确认差异仅为行尾。

### 第 0 轮复审修复 · 宿主静态检查通过，VMware 未开跑

待验分支为 `feature/web-contract-admin-reauth`，HEAD 为 `57fc07ebc6db605d3dc64da86492edfbabac545c`。六项修复均为本地未提交改动。上方 192、587、222 项的来宾结果属于该 HEAD，不能用于验收本轮增量。

| 宿主命令或静态检查 | 通过 | 失败 | 跳过 |
|---|---:|---:|---:|
| Python `json.loads` 解析 OpenAPI、examples 和 11a 候选 JSON | 3 | 0 | 0 |
| Python `ast.parse` 解析容量代理、新增单测及改动的账本集成测试 | 3 | 0 | 0 |
| `node scripts/gen-contracts.mjs --check` | 1 | 0 | 0 |
| `node node_modules/typescript/bin/tsc --noEmit --pretty false` | 1 | 0 | 0 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | 1 | 0 | 0 |
| `git diff --check` | 1 | 0 | 0 |

静态核对了六种管理凭证对共享键和理由的引用、三个整数输入上限、11a 候选的 OpenAPI 散列。新增 15 个共用 fixture（预期有效 6 个、无效 9 个），其运行结果须由 Python 和 Ajv 分别给出。

**停止依据。** 2026-11-24 16:49:59 UTC 在投递前读取指定 broker 状态。最后心跳为 12:57:59 UTC，距核查 13,920 秒，超过负责人规定的 120 秒。记录的宿主 PID 49536 已退出，队列为空。按离线条件暂停，未投递作业；本轮来宾命令执行数为 0，没有用例运行结果，也没有提交。

**待跑门禁。** 原包对应初版提交，恢复时需从本轮修复重新打包。来宾启动脚本沿用 J: 映射及 `python -I -B -X utf8 work/pr11b-contract/guest-r0/verify.py`，按以下顺序执行。表中 Node 命令从 `web/frontend` 运行。

| 来宾命令 | 待验内容 |
|---|---|
| `python -I -B -m pip check` | 隔离依赖 |
| `python -I -B tools/overnight/check_contracts.py --output <out>/contracts.json` | 冻结契约、Python 正反例及追踪 |
| `python -I -B tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` | 两个 07b 组合 |
| `node scripts/gen-contracts.mjs --check` | 生成物一致性 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` | Ajv 与前端契约 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` | 全量前端，含新增 7 个 `auth.test.ts` 传输用例 |
| `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin>` | 第三轮列出的 10 个 `--test-target` 加 `--test-target tests/unit/test_measured_reauth.py`。原账本集成目标含新加的凭据验证前 422、TOTP 计数及有效理由原文审计用例 |

宿主证据目录为 `outputs/pr11b-contract/review-r0/`。

| 文件 | SHA-256 |
|---|---|
| host-static.json | `e8d133f3ecdd93d17fa40e7d38315b446e53e7df387c75734ef72ca4a53741bc` |
| broker-preflight.json | `aa49ca0143fe7a8bf657982cd886ff87d1697d8d9c80322725c7bc86fb98d718` |
| 修复后 openapi.json | `6416b3c2ea3040ef1ba3a79f0b6d1574301b31f82a55efe61ef488ff535e1f2a` |
| 生成的 openapi.ts | `b09f251193727439654d9ed999d5f7c32df7e56bea8ef686a512e41389bdc607` |

### 第 0、1 轮合并验证 · 宿主及 VMware 来宾全部通过

2026-11-24，从本工作树的 `57fc07ebc6db605d3dc64da86492edfbabac545c` 加两轮修复打包。打包后逐字节核对工作树、`source.zip` 和 `SOURCE.json` 中的 1,008 个文件，包含新增的容量代理单测。公开 OpenAPI 的 18 个 HTTP 候选键全部与写入头引用同一 schema，4 个学生恢复用途使用独立的 `op_id`。11a 候选记录中的 `base_sha256` 与公开 OpenAPI 一致。

| 本轮宿主静态检查 | 通过 | 失败 | 跳过 |
|---|---:|---:|---:|
| Python `json.loads` 读取 OpenAPI、examples、11a 候选 | 3 | 0 | 0 |
| Python `ast.parse` 读取容量代理、账本集成测试、容量代理单测 | 3 | 0 | 0 |
| `node scripts/gen-contracts.mjs --check` | 1 | 0 | 0 |
| `node node_modules/typescript/bin/tsc --noEmit --pretty false` | 1 | 0 | 0 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | 1 | 0 | 0 |
| `git diff --check` | 1 | 0 | 0 |

**排队判断更正。** 原作业 `b11-fff92b251cec` 通过 `submit.ps1 -Id b11-fff92b251cec -Kind run -Script <broker>/scripts/b11-fff92b251cec.ps1 -TimeoutSeconds 1500` 于 19:20:37 UTC 入队。19:23:15 UTC 记录的等待 158 秒、心跳年龄 210 秒均属实。当时把忙碌期间心跳停更误判为 broker 卡住，提前暂停了验收。共享 broker 串行执行作业，运行期间心跳不更新。上一项 `pr11c-20261124-191942-41e5a9d0` 的完成回执为 19:47:33 UTC。两秒后 broker 领取本作业，19:47:35–19:49:12 UTC 完成。原作业回执的 `TransportSucceeded=true`、`GuestExitCode=0`。后续遇到 busy，应查看对应的 `done/<id>.result.json` 与队列，等待当前作业结束。

**来宾命令与结果。** 启动脚本核对 `J:` 映射到 `C:\j`，以 `python -I -B -X utf8 work/pr11b-contract/guest-r0/verify.py` 调度。Python 3.12.14、Node 24.19.0、Vitest 4.1.12。下表的 Node 命令从 `web/frontend` 执行，`<out>` 为本作业的 `outputs/pr11b-contract`。stdout 的退出码与证据包 `commands.json` 逐项相符。

| 实际命令 | 退出码 | 通过 | 失败 | 跳过 | 结果口径 |
|---|---:|---:|---:|---:|---|
| `python -I -B -m pip check` | 0 | 1 | 0 | 0 | 离线依赖完整 |
| `python -I -B tools/overnight/check_contracts.py --output <out>/contracts.json` | 0 | 9 | 0 | 0 | 含 174 个共享样例，82 正例、92 反例 |
| `python -I -B tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` | 0 | 116 | 0 | 0 | review 通过 20 正例、38 反例，billing-and-review 通过 20 正例、38 反例 |
| `node scripts/gen-contracts.mjs --check` | 0 | 1 | 0 | 0 | 生成类型一致 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` | 0 | 209 | 0 | 0 | 3 个测试文件 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` | 0 | 611 | 0 | 0 | 16 个测试文件，含上一行的 209 项 |
| `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin>` 加 11 个 `--test-target` | 0 | 224 | 0 | 0 | JUnit 为 224 项、0 错误。Starlette TestClient 使用 httpx 产生 1 条弃用警告 |

后端使用 `C:\j\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin`。11 个目标按实际参数顺序如下，每行对应一个 `--test-target`。

```text
tests/unit/test_pr07b_contract.py
tests/unit/test_measured_reauth.py
tests/unit/test_billing_policy.py
tests/unit/test_auth_commit.py
tests/integration/test_billing.py
tests/integration/test_billing_integration.py
tests/integration/test_authentication.py
tests/integration/test_auth_review_fixes.py
tests/integration/test_identity.py::test_invitation_always_activates_student_and_student_cannot_manage
tests/integration/test_identity.py::test_admin_management_rechecks_all_trusted_auth_evidence
tests/integration/test_trusted_cache_browser.py
```

新增的再认证委派单测和因子验证前拒绝非法候选的集成用例均在 JUnit 中通过。PostgreSQL 17.11 监听 `127.0.0.1:58054`。initdb、启动、pytest、停库均退出 0。网络隔离探针按预期退出 1，真实供应商调用计数为 0。

**证据回收与源码核验。** 等队列中的作业结束后，通过 `submit.ps1 -Id b11-fff92b251cec-evidence -Kind copyFrom -GuestPath C:\j\class-assistant\worktrees\b11-fff92b251cec\work\pr11b-contract\evidence.zip -TimeoutSeconds 180` 于 20:17:45 UTC 回收。回执成功，宿主 ZIP 的 SHA-256 与来宾 stdout 相同。包内 `SOURCE.json` 与送测清单逐字节一致，宿主核实源码包的全部 1,008 项散列，来宾 stdout 确认全部文件及前端依赖锁匹配。与送测快照相比，只有裁决文档追加了本轮 VERIFICATION；5 个 TypeScript 文件由 Git 按文本属性将 CRLF 归一化为 LF。

宿主静态记录位于 `outputs/pr11b-contract/review-r1/host-static.json`。本作业的证据 ZIP、原始 stdout/stderr、运行和回收回执、当时的排队观测位于 `outputs/pr11b-contract/b11-fff92b251cec/`。输入包、清单与启动脚本位于 `work/pr11b-contract/b11-fff92b251cec/`。

| 文件 | SHA-256 |
|---|---|
| 第 0 轮独立复审报告 | `f5cf86c77825be6b353994e8444535a487a8a001e7870fe42867e9994409e38a` |
| 第 1 轮独立复审报告 | `2b6fdade742ef9e24c8ea4e95344b2c7f0a5bdb90389b36bb685e6df6acd91be` |
| host-static.json | `2e0f84cd8e81fec7043491c0fe2af818a966d098587188e9f7e370f460c3e425` |
| source.zip | `ddfdb15bea6e22ade48d64a25e7ce8740f91492df09f160e8fd9e52de0a4d63f` |
| SOURCE.json | `38083b54b36365a933c8cd6116ec8ba582044c0d05f7c83f5bbdf42fca1a7de7` |
| guest.ps1 | `3f9e5b41e4b411bb9192c87dd44c5781522d5cb53cf9cc8d24725ca116617797` |
| queue-status-pause.json | `c0c8afb3312417c39cb8c201155a29905a455cfb6a6fcc1b71e524bc3ef4a7ba` |
| run 回执 | `3c13ef5ba1ec73a65e156b040c4cc3e777ea7ba0e8af7cb233d3fdb9472d5b49` |
| 原始 stdout | `9e8ce3107fd59b5febedada91e41050a96f645e29411055a204c16c97ead34c5` |
| copyFrom 回执 | `5565d8ce25bd4422af98394169a26c2112f7792e5918bd740e9964e3c7d0cf0f` |
| evidence.zip | `7942211f27ff2f5b522e399b5829c537f0a53098d5316d4aaf8bbbe21f00fb42` |
| 包内 commands.json | `54e75b756528897dc9548b7467ffeb2a2dd7890aac0f6904a3178344b30bd0a9` |
| 包内后端 pytest.xml | `fa28a10d55bb80bc6eed7c10da2df248bd92379cdcf821e2faedb2fd03e4199a` |

### 第 2 轮修复验证 · 后端 1 项准备阶段错误，未提交

2026-11-24，以 `ddf5da7165299313ba9821a9cb0e00f5ececc902` 加本轮改动打包，契约为 0.4.0。打包前宿主执行 `json.loads`（3 个 JSON）、`ast.parse`（5 个 Python 文件）、`node scripts/gen-contracts.mjs --check`、`node node_modules/typescript/bin/tsc --noEmit --pretty false`、`node node_modules/eslint/bin/eslint.js . --max-warnings 0`、`git diff --check`，均退出 0，共 12 项通过、0 失败、0 跳过。另运行 `python -B -X utf8 tools/pr11a/contract_candidate.py`，确认候选文件可逐字节再生成。

宿主投递前查得共享 broker 空闲。VMware 作业 `b11-b08922dbc363` 从 20:37:53 到 20:39:39 UTC 执行，传输成功，来宾退出 1。启动脚本核对 `J:` → `C:\j`，使用 `python -I -B -X utf8 work/pr11b-contract/guest-r2/verify.py`。宿主和来宾逐项核对 1,008 个文件，来宾确认前端依赖锁相符。本轮未重跑。

| 来宾命令 | 退出码 | 通过 | 断言失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| `python -I -B -m pip check` | 0 | 1 | 0 | 0 | 0 |
| `python -I -B tools/overnight/check_contracts.py --output <out>/contracts.json` | 0 | 9 | 0 | 0 | 0 |
| `python -I -B tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` | 0 | 116 | 0 | 0 | 0 |
| `node scripts/gen-contracts.mjs --check` | 0 | 1 | 0 | 0 | 0 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` | 0 | 221 | 0 | 0 | 0 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` | 0 | 625 | 0 | 0 | 0 |
| `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin>` 加上节列出的全部 11 个 `--test-target` | 1 | 229 | 0 | 1 | 0 |

冻结检查含 186 个共享样例（86 正、100 反）。07b 的两个组合各通过 20 个正例、38 个反例。前端全量覆盖 16 个文件，其中含契约专项的 3 个文件。后端 JUnit 共 230 项，另有 1 条 Starlette TestClient 弃用警告。本轮的混合开关与准入、旧回执、CAS、因子前拒绝、两种原始 JSON 数字写法以及并发额度边界回归均通过。PostgreSQL 17.11 位于 `127.0.0.1:55685`，停库退出 0。出网拒绝探针按预期退出 1，真实供应商调用为 0。

**阻塞项。** `tests/integration/test_trusted_cache_browser.py::test_formal_authclient_encrypted_cache_and_revocation` 在 fixture 准备时抛出 `ValueError: incompatible existing unlockCache contract`，浏览器场景未执行。`tools/pr05a/formal/candidate.py` 从 `ReauthOperation.oneOf[0]`（账号用途 `logoutAll`）复制候选分支，把目标种类改成 `device`，同时继承了本轮只适用于账号的规范小写 UUID。公开 `unlockCache` 的设备 UUID 保留既有格式，两者在第 57 行的结构一致性检查处冲突。根据产品或测试失败的停止条件，保留工作树，未提交。

建议下一步只修正 05a 对设备分支的构造：显式选取设备目标的已知结构，保留对既有 `unlockCache` 定义的独立一致性检查；补组合回归并重跑本轮门禁。此次暂停后获得负责人授权，后续处理见上文“PR-05a 设备目标的兼容处理”。

`copyFrom` 作业 `b11-b08922dbc363-evidence` 于 20:40:00 UTC 完成。宿主验证 ZIP 散列与来宾 stdout 一致，包内 `SOURCE.json` 与送测清单逐字节相同。宿主静态证据位于 `outputs/pr11b-contract/review-r2/host-static.json`，作业回执、原始 stdout/stderr、验证摘要与证据包位于 `outputs/pr11b-contract/b11-b08922dbc363/`。完整路径和 11 个目标的参数顺序见证据包中的 `commands.json`。

| 文件 | SHA-256 |
|---|---|
| 独立复审 review.md | `ac0d16e646353eab2fa28a987ca58667d1d9e06ab2d72972fa40f3e47b7844b7` |
| Claude 判定 final-report.md | `aa119f21bb3e11fca0836352bafbbcf4d168e0300280b2625e87928a9a16946e` |
| host-static.json | `ad7ef978382fac9a49759447c9eb0eb2600dca56be6b0fcf363530d7bc2783c3` |
| source.zip | `ca3278bf41512f418b2e036d56e9b067dfa2401b011e6b86ce0c54f33a0f2c0a` |
| SOURCE.json | `70e5e89d6054d5a47ed9ac0f18c28d2f209a92e21c939ee227fc58f94275cafe` |
| guest.ps1 | `13a2c7503c9b7d06c619d6e046d2a8dc1db889bb02ab9b4284b76b89bf82abb9` |
| run 回执 | `6b92e7b3d87e342b3528c25e3056b2253a96730a89de279675302b23eac9d84d` |
| copyFrom 回执 | `1d07c680f92dbce8f883e78f6de5a63611421d5de1ff75642d4d77b48432fb99` |
| evidence.zip | `6f07755dedcd6315161f11123b2fbc206d8d55e978649b50fd664babac494d5d` |
| 包内 commands.json | `8f4a92d09dac28c7ea7b8e01b76bc680ab91e22775ddcf8e4a9a7d203997f94c` |
| 包内 pytest.xml | `593e6fb924d0d84fd357e3cc1fb02d29a1736f6ec52cdf1f8219d0c60a07953a` |

### 第 2 轮修复复验 · 可信缓存及全部门禁通过

作业 `b11-308edacab34f` 使用 `ddf5da7165299313ba9821a9cb0e00f5ececc902` 加本轮全部改动重新打包。前一次后端 fixture 错误及其证据保留在上一节。投递前已确认共享 broker 空闲，原作业回执记录 20:46:29–20:48:16 UTC。来宾核对 1,008 个文件、前端依赖锁和 `J:` 到 `C:\j` 的映射，以 `python -I -B -X utf8 work/pr11b-contract/guest-r2/verify.py` 调度。作业退出 0。

宿主运行 `json.loads`（3 个 JSON）、`ast.parse`（7 个 Python 文件）、`node scripts/gen-contracts.mjs --check`、`node node_modules/typescript/bin/tsc --noEmit --pretty false`、`node node_modules/eslint/bin/eslint.js . --max-warnings 0` 和 `git diff --check`。共 14 项通过，失败、错误、跳过均为 0。完整命令和工作目录见 `outputs/pr11b-contract/review-r2-cache/host-static.json`。

| 本轮来宾命令 | 退出码 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| `python -I -B -m pip check` | 0 | 1 | 0 | 0 | 0 |
| `python -I -B tools/overnight/check_contracts.py --output <out>/contracts.json` | 0 | 9 | 0 | 0 | 0 |
| `python -I -B tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` | 0 | 116 | 0 | 0 | 0 |
| `node scripts/gen-contracts.mjs --check` | 0 | 1 | 0 | 0 | 0 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` | 0 | 226 | 0 | 0 | 0 |
| `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` | 0 | 630 | 0 | 0 | 0 |
| `python -I -B tools/pr11a/run_tests.py --pg-bin <PG17.11-bin>` 加相同的 11 个 `--test-target` | 0 | 235 | 0 | 0 | 0 |

冻结检查包含 191 个共享样例。07b 的两个组合各通过 20 个正例及 38 个反例。全量 Vitest 包含契约专项。JUnit 确认 5 项新增的 05a 组合与拒绝用例全部通过，`test_formal_authclient_encrypted_cache_and_revocation` 完成实际浏览器缓存及设备撤销验证。后端有 1 条 Starlette TestClient 弃用警告。隔离 PostgreSQL 17.11 在 `127.0.0.1:56575` 启动并正常停止。出网拒绝探针按预期退出 1，真实供应商调用为 0。

`copyFrom` 作业 `b11-308edacab34f-evidence` 于 20:48:30 UTC 成功回收证据。宿主 ZIP 散列与来宾 stdout 相符，包内源码清单和宿主送测清单逐字节一致。从送测包到提交仅追加此验证记录，Git 按仓库属性处理文本行尾。源码包在 `work/pr11b-contract/b11-308edacab34f/`，原始日志、证据 ZIP、运行和回收回执在 `outputs/pr11b-contract/b11-308edacab34f/`。

| 文件 | SHA-256 |
|---|---|
| host-static.json | `8a4ac8a85fd908c37a58cbc293aeb049777bb3ce027d1fe640f45925fb9de5b0` |
| source.zip | `23608a405886fa5fe9e46687e5080aa90ae8c03bc2638caf084b12bc18eb1f34` |
| SOURCE.json | `bc0062a741fe7c78fdcd2b78beb72d970858a7bb3069e3a823862a82b2d219fb` |
| guest.ps1 | `e29c297b5f3fea6c3f1ac24009c995d7b54e025cd3833bae00b39ecdec24ef0f` |
| run 回执 | `8d67e1c07e2e3f71f08fd539a0a27dccb76fcc1ea876ed031d61cfc809b6a115` |
| copyFrom 回执 | `368d3476ef8211cdc815b660ca2fd44a7022743109099ea49de92dcecec39917` |
| evidence.zip | `4edeaab68da9aa85c53ff6a5007fe9683779fe6d2e9b687c4d3cab6f0af8f9d2` |
| commands.json | `99efcb40016adf1c021e8590e689b0fb42a7b28390d5fd5c4c05af45bcda7978` |
| pr11a/20261124T204649-ee627d3c/pytest.xml | `ad2cd812ed83aed73179d28f67145f2d0b0cadc3734796a46157ea47cb621241` |

### 额外复审修复 · 2026-11-26

送测内容为本分支 `14201d2` 加上“额外复审核实”表中的六项修复、生成类型、测试与裁决。宿主为 Windows、Python 3.10.4、Node v24.19.0。打包后逐个核对 1,008 份输入的工作树、ZIP 和清单散列。打包后对送测文件的唯一编辑是追加本节验证记录。

| 宿主门禁 | 退出码 | 通过 | 失败 | 跳过 |
|---|---:|---:|---:|---:|
| Python `json.loads`，OpenAPI、共享样例和 11a 候选清单 | 0 | 3 | 0 | 0 |
| Python `ast.parse`，4 个变更的仓库文件和 2 个本轮打包、来宾脚本 | 0 | 6 | 0 | 0 |
| `node scripts/gen-contracts.mjs --check` | 0 | 1 | 0 | 0 |
| `node node_modules/typescript/bin/tsc --noEmit --pretty false` | 0 | 1 | 0 | 0 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | 0 | 1 | 0 | 0 |
| `git diff --check` | 0 | 1 | 0 | 0 |

先在 `web/frontend` 运行 `node scripts/gen-contracts.mjs` 刷新类型，再执行上表。六组共 13 项通过，错误 0。命令、工作目录和退出码见 `outputs/pr11b-contract/review-extra-20261126/host-static.json`。

**来宾命令。** 启动脚本在来宾缺少 `J:` 时执行 `subst.exe J: C:\j`。以 `python -I -B -X utf8 work/pr11b-contract/guest-r3/verify.py` 执行下表中的七组门禁。前端命令在 `web/frontend` 运行，Python 使用本作业的私有解释器，PG 指向既有来宾 PG 17.11 的 bin 目录。前端契约专项是全量 Vitest 的子集。

| 顺序 | 来宾命令 |
|---:|---|
| 1 | `python -I -B -X utf8 -m pip check` |
| 2 | `python -I -B -X utf8 tools/overnight/check_contracts.py --output <out>/contracts.json` |
| 3 | `python -I -B -X utf8 tools/pr07b/check_contract_candidate.py --output <out>/compositions.json` |
| 4 | `node scripts/gen-contracts.mjs --check` |
| 5 | `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts tests/contract.test.ts tests/pr10a-contract.test.ts tests/pr10b-contract.test.ts` |
| 6 | `node node_modules/vitest/vitest.mjs run --config tests/vitest.config.ts` |
| 7 | `python -I -B -X utf8 tools/pr11a/run_tests.py --pg-bin <PG17.11-bin>`，每个下列目标单独传 `--test-target` |

```text
tests/unit/test_pr07b_contract.py
tests/unit/test_measured_reauth.py
tests/unit/test_billing_policy.py
tests/unit/test_auth_commit.py
tests/integration/test_billing.py
tests/integration/test_billing_integration.py
tests/integration/test_capture_recovery.py::test_acquire_idempotency_and_parallel_owner_lease
tests/integration/test_capture_recovery.py::test_independent_device_discovers_lease_and_explicit_cas_takeover
tests/integration/test_authentication.py
tests/integration/test_auth_review_fixes.py
tests/integration/test_identity.py::test_invitation_always_activates_student_and_student_cannot_manage
tests/integration/test_identity.py::test_admin_management_rechecks_all_trusted_auth_evidence
tests/integration/test_trusted_cache_browser.py
```

**首轮 `b11-54e49eef01d8`。** 来宾于 13:28:22–13:30:28 UTC 执行。前六组退出 0，前端全量 642 项通过。后端 247 项通过，断言失败 0，fixture 错误 2，跳过 0。两个采集目标在同一处初始化中断，`deletion_support.services` 缺少本作业的 `PR04B_OBJECT_ROOT`。按已有 PR-05b 来宾脚本，为本作业指定独立目录及既有 `PR04B_WEED` 可执行文件，然后复用字节相同的源码 ZIP、清单和测试目标。产品与测试文件未改动。

**复验 `b11-1a4ae695cbc9`。** 等待共享 broker 的在先作业写出完成回执后投递，来宾于 13:59:44–14:02:13 UTC 完成。来宾为 Windows VMware、Python 3.12.14、Node v24.19.0、隔离 PostgreSQL 17.11。下表为复验实际结果。

| 来宾门禁 | 退出码 | 通过 | 失败 | 错误 | 跳过 |
|---|---:|---:|---:|---:|---:|
| pip check | 0 | 1 | 0 | 0 | 0 |
| 冻结契约 | 0 | 9 | 0 | 0 | 0 |
| PR-07b 候选组合 | 0 | 116 | 0 | 0 | 0 |
| gen-contracts --check | 0 | 1 | 0 | 0 | 0 |
| 前端契约专项 | 0 | 236 | 0 | 0 | 0 |
| 前端全量 Vitest | 0 | 642 | 0 | 0 | 0 |
| 后端定向 PostgreSQL 回归 | 0 | 249 | 0 | 0 | 0 |

冻结检查覆盖 201 个共享样例（92 正、109 反）。07b 的两个组合各通过 20 个正例及 38 个反例。JUnit 记录了采集并发准入和显式接管、新旧开关回执的跨会话回放、精确整数、缓存解锁和恢复的 v7 早拒。首轮的两项 fixture 错误均已复验通过。PG 在 `127.0.0.1:49940` 正常停库，真实供应商调用 0。后端有 1 条既有 Starlette TestClient 弃用警告。

两轮的原始日志、作业回执、证据包与 `verification.json` 分别位于 `outputs/pr11b-contract/b11-54e49eef01d8/` 和 `outputs/pr11b-contract/b11-1a4ae695cbc9/`。两个证据 ZIP 均按来宾 stdout 的 SHA-256 核对，包内 `SOURCE.json` 与各自宿主清单逐字节相同。复核对象仍为本地未发布的 0.4.0，采集配置的实际执行及四个管理写入消费者按上文保留。

| 证据 | SHA-256 |
|---|---|
| 本轮输入 review.md | `55b3370b2b07dfa838fe95898d0897fd5d2413d4d742d331e0f8a211d2d0c4be` |
| 本轮输入 final-report.md | `3952251ef776137414e7bac24c50bc72dd2ac3ea29b1c09e7cad7629fec6c5b3` |
| 宿主 host-static.json | `7412b0d09f36b7ad4a9004e7c1b00f1056f99e2ed4200dc1a850055a63f25f5e` |
| 两轮共用 source.zip | `532c2a72ab89c8e8132a5fb25a39f2a012d58b2ac663ec414c3e37ac4c94f9cc` |
| 两轮共用 SOURCE.json | `47fd78feb6d9ceccb6095fa830a421e3c3bd116e3953437915604a96bf7f8eb0` |
| 首轮 guest.ps1 | `58ea1d5c6134b69dcf16d8dad95e8460e4a5120cdc01e11deddcc95fbfb5772e` |
| 首轮 evidence.zip | `5731e1a6b13e7607e2599d870bf4e5cde0204196737f0bd55fb1d83a4d80bb75` |
| 首轮 verification.json | `bbfa3a7032594f14416299344c8996c96d1b5f8a2084823b1be45edb80c50aeb` |
| 复验 guest.ps1 | `c109f3863d8210815304827788fc7769053bc83cec3ca5abf673641e55c84fbb` |
| 复验 run 回执 | `d42f7c53ff4b24baaa774a47030cc587cce37b5b49787561d2a238db87a2ed4d` |
| 复验 copyFrom 回执 | `d337710a797b36b34d8adcf894fc7cf2b8153e9e9b5ddf7869728b3f91a01315` |
| 复验 evidence.zip | `918b55cd1c05508d91bb39c7c620819f3f7bc4471c1f7549d31f10d4d2bb24e1` |
| 复验包内 commands.json | `dceb9e56443521ace9144809fee9ecf3c3971f3ca853a0d494e45f29bf14ef92` |
| 复验包内 pr11a/20261126T140007-2c035052/pytest.xml | `f18c2520b20ec821a9041c160c93e61f6d4824399e486459141c543fe71081b8` |
