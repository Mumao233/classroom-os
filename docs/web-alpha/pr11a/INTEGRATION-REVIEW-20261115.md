# PR-11a 接入增量交接

负责人：林珈诚。日期：2026-11-15（America/Los_Angeles；来宾作业 ID 使用 2026-11-16 UTC）。本轮交付为本地未提交的接入候选，范围是独立审核提出的 HTTP、worker、管理员鉴权、周期、通知和迁移接缝。下一轮审核仅检查本增量及其直接影响的调用、契约、迁移与定向回归。

独立模块结论和历史证据继续见 [原交接](README.md)。本轮没有重做整包独立审核，也没有启用真实付费能力。

## 基线与文件识别

正式仓库 main 和本工作树 HEAD 均为 `81ab191b1f33de2371567eccd0ef9589d12483c2`；收尾再次执行 `git ls-remote origin refs/heads/main`，远端仍相同。分支为 `feature/web-pr11a-budget`，实际工作树为 `J:\class-assistant\worktrees\web-pr11a-budget`，继续复用此前经 Codex 工具创建并附加的工作树。

独立审核基线来自正式仓库 `docs/web-alpha/evidence/INDEPENDENT-REVIEW-20261115-04A-05A-11A.md` 及 `outputs/independent-review-20261115/11a-snapshot.json`。修改前的 650 文件已逐项匹配并保存。由于原 PR-11a 文件尚未提交，仅用 `git diff` 会漏掉其后续修改。

本轮审阅入口位于本工作树 `outputs/pr11a/integration-review-20261115-01/`：

| 文件 | 用途 |
|---|---|
| `BASELINE.json` | 独立审核时的 650 文件散列和 dirty 清单 |
| `delta.json` | 相对上述基线的新增、修改、删除，前后 SHA-256、行区间及函数位置 |
| `increment.patch` | 包含原未跟踪文件后续修改的完整增量补丁 |
| `VALIDATION.json` | 五次来宾结果、原始 XML 统计、源码和依赖核验 |
| `static.json`、`powershell-static.json` | AST、链接、空白、保护文件和 PowerShell 解析结果 |
| 各作业目录 | evidence.zip、解压原始日志/XML、broker 回执与输出；最终目录另含 source.zip |

这些 outputs 为本地证据，不替代后续提交的正式交付。增量共 18 文件（9 修改、9 新增、0 删除），基线其余 641 文件保持一致。原始失败轮次全部保留。

## 文件归属与前后行为

以下路径相对仓库根；行号为当前候选的位置，完整变化区间以 delta.json 为准。

| 文件与关键入口 | 修改前 | 本轮候选 |
|---|---|---|
| `web/backend/classroom_web/infrastructure/database.py:28` `UnitOfWork.billing` | 费用端口由调用方手工组合 | 懒加载费用 repository，复用同一连接和事务 |
| `application/auth.py:200` 再认证分派 | 只处理既有用途 | 密码/MFA 验证完成后，可选委托两个管理用途；默认行为保留 |
| `entrypoints/http.py:70` `create_app` | 未组合费用端口 | 可选 billing/billing_admin，个人配额、费用任务取消及管理路由复用现有 HTTPS/Origin/CSRF/响应校验 |
| `application/task_worker.py:26` `TaskWorker` | 只记录模拟发送意图 | 可选生命周期在同事务提交意图与一次性许可；已预留任务缺少 hook 时拒绝发送 |
| `application/billing_browser.py:15` `BillingBrowser` | 无 | 使用真实 Principal 查询配额，费用任务取消后同步账本；普通未关联费用的任务取消交给 owning 模块 |
| `application/billing_browser.py:65` `BillingAdmin` | 无 | Admin+TOTP、理由、用途、目标、正文及幂等键绑定；用户配额只投影费用，不读取学习内容 |
| `application/billing_tasks.py:9` `BillingTaskLifecycle` | 无 | 发送、完成、unknown、撤权和迟到 usage 的元数据核对；最多每轮 100 个恢复路由 |
| `infrastructure/billing_repository.py:50` `for_task` / `sync_execution` | 独立费用端口 | 对接既有 task/attempt 元数据；已证实未发送的取消/失效释放，已发送不确定保留 unknown，已结算保持结算 |
| `infrastructure/billing_control.py:12` `PgBillingAdminRepository` | 无 | 独立 NOINHERIT gateway 登录，只能调用三个固定 JSON 函数，无会话表/平台表直接权限 |
| `infrastructure/billing_control.py:63` `BillingMaintenance` | 无 | 显式调度调用：北京时间当月开期、费用恢复轮询、阈值通知拉取及幂等 ACK |
| `infrastructure/billing_integration.sql:1` | 无 | 元数据权限、预算 FK、管理再认证桥、日历函数和内部通知 outbox |
| `infrastructure/billing_schema.py:9` `integration_ddl` | 只加载独立 DDL | 分开加载接入 DDL，供迁移冻结组合 |
| `tools/pr11a/contract_candidate.py:11` | 无 | 从冻结契约生成显式未审 overlay；共享契约原文件不变 |
| `tools/pr11a/run_tests.py`、`prepare_vmware.ps1` | billing/backend 两种选择 | 增加 integration 选择，快照纳入实际 04a 的候选迁移输入及散列 |
| `web/backend/tests/integration/test_billing_integration.py` | 无 | 12 个新增 PG/认证/ASGI/组合迁移场景 |
| `docs/web-alpha/pr11a/CONTRACT-CANDIDATE.json`、`migration_candidate.py.in` | 无 | 待审契约增量、未分配 revision/parent 的迁移模板 |

上述 application/infrastructure 简写均在 `web/backend/classroom_web/` 下。本轮没有修改费用算术、独立账本 DDL、原 48 项费用断言、前端、依赖锁或已注册迁移。

## 接入语义与共享安排

worker 的发送许可和既有 `mark_submitting` 在同一 UoW 内提交后才进入模拟 provider。`usage_for` 仅接收适配器已经取得的 usage，不产生新的供应商查询。worker 的 simulation-only 限制保留。异常、租约到期、撤权和取消后的 finally 回调由受限 reconciler 核对；撤权不开放内容访问或新派发。

管理员 HTTP 组合要求契约上存在 `x-pr11a-candidate`，否则工厂拒绝启动。管理再认证使用既有密码/MFA attestation、会话、设备和 security epoch，证明绑定当前管理员账户、用途、完整正文承诺与幂等键。错误、过期证明不修改开关或新建默认配额行。成功操作使用既有 receipt 保存幂等结果；同键异体拒绝。

用户额度调整事务在验证证明之后才补建当前周期额度行。平台预算仍独立约束，增加个人额度不会提高平台预算。`max_active_users` 的阶段变更返回 SERVICE_DISABLED，等待已登记的阶段切换产品规则。

周期维护只根据既有身份容量开当前北京时间月份，保留已存在周期配置。没有后台线程或自动启动的新服务。旧月 unknown 继续归原周期核对。阈值 outbox 在告警同事务写入；未 ACK 可重复拉取，消费者须按通知 ID 幂等写入，再 ACK。公共通知投影和 fanout 归 PR-11c，本轮仅实现内部可靠端口。

现有任务仍采用“一任务一有界费用窗口”。连续流的新窗口、同供应商流累计计量分摊及 ASR 帧消费归 PR-06b 接缝。业务 prepare/execute 入口未来由 owning 包在同 UoW 调用 BillingService；本轮 HTTP 用例的任务由应用服务建立，不把尚不存在的付费业务入口宣称完成。

PR-09b 继续由 Claude 负责页面与 consumer 类型。细分开关新增字段是候选可选字段；读取的粗粒度 ASR/AI 取对应细开关的 AND，写入粗开关同时设置两子开关，同一 PATCH 不能混写同组粗细字段。共享契约版本、changelog、fixtures 和 consumer 确认由协调流程安排，不直接覆盖冻结 OpenAPI 0.2.0。

## 迁移计划与冻结模型差异

建议顺序是现有 `0008_manual_recovery` → 04a 的 `0009_private_media` 候选 → 协调分配编号的费用迁移。正式编号和 down_revision 仍未分配。模板要求将 billing_schema.sql 与 billing_integration.sql 按顺序冻结进迁移自己的 SQL sidecar，历史迁移不得导入实时应用代码。

本轮组合测试临时复制实际 04a 迁移，在独立测试目录生成 `pr11a_review_candidate`，完成 Alembic 升级，并验证升级前旧任务和 op receipt 可重放。临时测试 revision 不加入正式迁移目录；它导入当前 DDL 仅用于候选测试，不是可发布迁移。没有执行生产迁移。回退采取关闭新收费、保留预留/usage/审计并前滚修复。

以下是架构逻辑模型与物理候选的显式映射或差异，需架构审阅；不能把重命名、派生值和物理结构差异一并称为完全一致。

| 逻辑模型 | 物理候选及审阅事项 |
|---|---|
| bigint `_micro_cny` | `_microyuan`，相同 1 元=1,000,000；公开 Quota/Task/AdminUsage 沿用整数十进制字符串 |
| quota 的 period_kind、unit、reserved_value、committed_value | 周期/单位由受 CHECK 限制的 resource 推导；计数按 allocation 与 reservation 状态投影，未复制可漂移累计列；唯一键相应为 owner/resource/start |
| platform_budgets UUID id、quota_reservations.budget_id | 私有平台表以 period_start timestamptz 为 PK，reservation.period_start 本轮补真实 FK；不是 UUID 的等价映射，正式模型须明确批准或调整 |
| platform created_at、文本 stage、reserved/settled 列 | 当前无独立 created_at，stage 为 integer 10/30；风险列由 exposures 汇总，含额外 unknown、池划分和 fixed/safety；均为待审物理差异 |
| reservation.price_revision | 不可变 price_id FK 指向 price_catalog.revision，窗口上限、quota_root、pool 等为额外字段 |
| reservation attempt FK | owner/attempt/task 三列精确关联，增加 attempt 复合 UNIQUE；其他 owner FK 保留。候选使用默认 NO ACTION，未逐项改写逻辑 RESTRICT 的字面声明 |
| allocation.created_at | 当前原候选无单独时间列，时间可由父 reservation 追溯；是否增列需要明确审阅，不隐称字段已存在 |
| usage.provider_usage_key、标量 meters | request_id+meter+metric_revision 的全局唯一键；input/output/audio 数量在 quantities JSON，空计量保留 NULL，按受限价格目录计价 |
| usage.price_revision、measurement_kind、bill_month、reconciled_at | price 由 reservation FK 固定；累计 usage/最终更正由 revision/final/adjustment_of 表达；未单列 measurement_kind/reconciled_at；bill_month 可空以保留供应商未知事实，与逻辑非空有差异 |
| quota_adjustments、price_catalog | 保留 owner/配额/actor FK 与价格版本唯一键，额外幂等 op_id、minimum_microyuan、synthetic；缺省 UUID 由应用生成。price_catalog 对 runtime 为只读可见，平台预算仍私有，需确认逻辑控制面授权描述 |
| 扩展表和权限 | 额外 billing_permits、exposures 路由元数据及 notification_outbox；control 仅读 task/attempt 指定元数据列，RLS 按 owner，不授予 input_ref/raw_output_ref 等正文 |

本轮独立账本表的状态、非负、周期、唯一性与原 owner RLS 检查保留。冻结 domain-model.json、OpenAPI、正式迁移及 lock 文件均未修改；模型差异是正式接入关口，不能由测试通过自动批准。

## 定向验收矩阵与实际结果

| 组 | 本轮覆盖 |
|---|---|
| HTTP / 真实身份 | 实际登录 cookie、Principal、CSRF 拒绝；配额读取包含 held；费用任务取消释放；非法用户 UUID 返回 422 |
| worker | 另一个 PG 连接在 provider 前看到 submitted 和 permit；缺 hook 不发送；原 worker 直接回归 |
| unknown / 撤权 | 租约超时关联 task unknown；账号禁用后保留 unknown，受限 reconciler 接收迟到最终 usage；无正文读取权 |
| 管理员 | 学生拒绝、TOTP 必需、理由与正文绑定、幂等回放、错误/过期证明无修改、错误调整证明不建配额行 |
| 周期 / 通知 | 当月开期幂等、金额字符串、持久阈值消息重复读取和 ACK；跨月算术/旧账保留沿用独立专项，最终 32 项未重复它们 |
| 组合迁移 | 真 PG 上 0008→04a→费用临时 revision；旧任务和操作回执保留 |

来宾为 VMware Windows TEST、Python 3.12.14、PostgreSQL 17.11，独立随机数据库、端口和 owned cluster。broker 由用户本机启动，凭据留在其内存；本轮不读取凭据。Tools running、只读共享可用，IP 查询为空，传输使用 VMware Tools。宿主只编辑、静态核对、编排和回收证据。

| 作业 | 实际结果 | 说明 |
|---|---|---|
| `pr11a-20261116-011544-ea316cd5` | 73 通过，6 失败 | 发现元数据列授权缺 task/attempt 的 RLS SELECT policy；已修复，不删除失败 |
| `pr11a-20261116-011859-ef4c6a99` | 79 通过，退出 0 | 含既有 48 项费用兼容回归，属于该中间快照 |
| `pr11a-20261116-012458-bd869a67` | 78 通过，2 失败 | 新用例误期望无 MFA 返回 403，实际既有 401 UNAUTHENTICATED；过期 fixture 违反时间 CHECK。修正 fixture/精确断言 |
| `pr11a-20261116-013209-7de0e420` | 32 通过，退出 0 | 收窄到本增量和直接调用的回归 |
| `pr11a-20261116-013631-0834480e` | 最终 32 通过，0 失败/错误/跳过，19.83 秒 | 增补非法用户 UUID 422 后的最终代码；PG stop=0，broker transport=True、guest_exit=0 |

最终源码 ZIP SHA-256：`2a36c0af8ac980dc4772c8190b3c84833995b35882d849e4dcb2ed9ce79b27ef`。最终 evidence ZIP SHA-256：`c8310fdc6096214c8830dd557726bad01a50e6d04d2d94f129b066ec3356f197`。五次证据均回收并重算散列，直接读取原始 JUnit 和 results.json，未把失败轮次合并为全通过。44 个依赖锁定版本与 freeze 一致，pip check 和 9 项契约检查通过。保留 1 条既有 Starlette/httpx 弃用警告，没有为此改依赖。

最终 source.zip 内 660 项逐文件散列均匹配 manifest，当前 web/tools 下的 346 个文件均与来宾快照相同。README 和本交接在回收证据后更新，不属于已测代码。宿主对 13 个 Python/迁移模板文件执行 AST 解析，PowerShell 解析、相对链接、尾空白和 git diff --check 均通过；保护文件检查确认冻结契约、依赖锁与注册迁移无变化。

最终选择为新增 `test_billing_integration.py`、原 `test_task_worker.py` 以及原认证的 login/logout/revocation、bound reauth/password change、TOTP server-role 三项。测试是 ASGI TestClient+真实 PG/认证，不是 live browser/TLS，也不是付费 provider。历史 48 项及 627+29 结果保留原时点，本轮未在最终快照重跑全后端或原 Windows/Node/DPAPI 全门禁。

## 实际命令与恢复

```powershell
# J:\class-assistant\worktrees\web-pr11a-budget
& tools/pr11a/prepare_vmware.ps1 -Suite integration
& 'J:\class-assistant\worktrees\web-pr03b-sessions\tools\pr03b\vmware\submit.ps1' -Id 'pr11a-20261116-013631-0834480e' -Kind run -Script 'J:\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\vmware-broker\scripts\pr11a-20261116-013631-0834480e.ps1' -TimeoutSeconds 900 -TailLines 105
& 'J:\class-assistant\worktrees\web-pr03b-sessions\tools\pr03b\vmware\submit.ps1' -Id 'pr11a-integration-evidence-05' -Kind copyFrom -GuestPath 'C:\j\class-assistant\worktrees\pr11a-20261116-013631-0834480e\outputs\pr11a\evidence.zip' -TimeoutSeconds 180
python -B work/pr11a/integration-review-20261115-01/verify_increment.py
git diff --check
```

最后三条宿主操作是证据核对与静态检查，不运行功能测试。来宾真实执行 `python -I -B tools/pr11a/run_tests.py --pg-bin <已冻结 PG17.11 bin> --suite integration`，完整绝对命令、pytest 参数和返回码见每轮 results.json。网络防护探测故意退出 1 并核验拒绝外网的日志；initdb/start/pytest/stop 最终均退出 0。

若发送后中断，保留原 task/attempt/reservation 并运行受限恢复核对；unknown 不自动退款、重发或重新执行。通知先幂等交付再 ACK。应用接入前关闭收费，先安装已批准迁移和受限角色，再显式组合服务。后续 broker 超时先按原 ID 查询回执，不能因宿主等待结束直接重复发送。

## 剩余关口

- 协调聊天分配正式 migration revision/parent，冻结 SQL sidecar；架构确认上述完整模型差异及控制面权限。
- 契约负责人确认版本、细分开关、两个管理再认证用途、producer/consumer fixtures；PR-09b 更新页面和类型。
- owning 业务 prepare/execute 与 PR-06b 持续流追加窗口接入；本轮未创建付费业务端点。
- PR-11c 公共通知 fanout、投影及消费者幂等；当前内部 outbox 已提供可靠接缝。
- TBD-01/02、真实价格/模型/窗口、阶段切换、安全预留审批等原待定项保持开放。max_active_users 变更仍拒绝。
- 后续独立复审只看本增量和直接影响；真实供应商、全浏览器、远端 CI、生产迁移和部署尚无本轮结果。

本轮无提交、推送、PR、合并、部署或真实付费调用，其他工作树改动保留。
