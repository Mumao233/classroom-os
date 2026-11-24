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

**落地边界。** 已有账本的额度与开关消费者须显式注入；公开 DTO 只返回公开开关字段。11a 候选的额外开关仍在独立组合中。四个用户/邀请写操作尚缺完整的 HTTP 消费者、目标授权、审计和事务回执。有效管理员请求这些用途时，签发入口返回 `503 SERVICE_DISABLED`；学生不得取得管理凭证。前端应分别控制可用操作，不能仅凭 Meta 版本开放整页写入。

[账本浏览器服务](../../../web/backend/classroom_web/application/billing_browser.py) · [受限账本网关](../../../web/backend/classroom_web/infrastructure/billing_control.py) · [签发与消费事务](../../../web/backend/classroom_web/infrastructure/billing_integration.sql) · [HTTP 组合入口](../../../web/backend/classroom_web/entrypoints/http.py)

## 二、邀请操作的理由

**问题与选项。** 创建只收 kind/email，撤销与重签使用 `Empty`。可选理由能接纳历史请求，却允许新操作只留下固定动作名。04-CONTRACTS-FLOWS 的运营行已要求“记理由”，AUTH-07 要求管理操作 MFA 与审计，现有用户、额度和开关写入都带操作者理由。因此三项均**必填**，无需新增产品规则。

`InvitationCreate.reason` 和 `InvitationAction.reason` 为 1–500 个 Unicode 码点，至少一个非空白字符。绑定客户端提交的确切文本。旧创建体和旧空对象均与新 schema 不兼容。在已挂载的生产者上，缺失、超长或纯空白均应在写入前返回 `422 VALIDATION_FAILED`。前端须先收集理由，再请求再认证。后续后端须把业务写入与受限审计记录放入同一事务，保存操作者、操作、资源引用、时间和真实理由。邀请 token 只在首次成功响应展示。创建和重签丢响应沿用 `409 SECRET_ALREADY_ISSUED`，不能通过审计或收据取回明文。

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
