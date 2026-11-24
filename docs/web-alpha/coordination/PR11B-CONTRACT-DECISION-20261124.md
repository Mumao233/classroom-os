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

## VERIFICATION · 2026-11-24

**HTTP 0.4.0 本次指定门禁已全部通过。** 三轮使用同一源码包。第三轮通过冻结检查、候选组合检查、生成物检查、前端契约测试、全量 Vitest 和后端定向 PostgreSQL 测试。前两轮因来宾启动脚本和日志编码中断，原始记录保留如下。

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
