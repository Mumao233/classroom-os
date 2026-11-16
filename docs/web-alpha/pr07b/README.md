# PR-07b 总结卡片与版本

负责人为林珈诚。开发基线为 `cf882602061e3d65341c2a130f8c4b26584e8850`，包含已合并的 PR-07a GitHub PR #14。独立分支为 `feature/web-pr07b-summary-versions`，工作包沿用 PR-07b。

本次实现覆盖授权课堂来源、完整来源选择、总结与卡片生成、引用验证、不可变版本、个人编辑、冲突保稿及显式采用。需求对应 DATA-02、SYNC-02、UI-REVIEW-05 至 UI-REVIEW-09。实施范围、候选 HTTP 契约、实际验证及独立审查分别记录在项目文档中。[实施计划](PLAN.md) · [API 与接入契约](CONTRACT.md) · [验证记录](VALIDATION.md) · [独立审查](REVIEW.md)

## 交付状态

| 状态 | 当前记录 |
|---|---|
| 开发 | 后端实现与自动化测试已落地，独立静态复审未发现剩余 P0/P1/P2 阻断项 |
| 验证 | VMware 实际轮次、首次失败、修复回归和 GitHub CI 以验证记录为准 |
| 合并 | 按独立 Draft PR 交付，合并状态以 GitHub 为准 |
| 生产启用 | Review composition 与 Worker 保持默认关闭，正式迁移和收费启用沿用既有授权要求 |

独立审查的发现、修复和最终结论随实际审查更新。测试统计、提交 SHA、源码与证据摘要、CI 链接集中保存在验证记录，引用时应核对其对应源码。[验证记录](VALIDATION.md) · [独立审查](REVIEW.md)

## 架构与来源复用

`PgReviewGenerationRepository` 复用 PR-07a 的计划准备、执行确认、计划一次消费、冻结来源和 Task 关联。`ReviewWorker` 复用既有持久 send intent、Attempt、租约 fence、结果保存及本地恢复流程，并推进多批协调。根任务使用 `schema_version=pr07b-1`，后续批次使用 `pr07b-batch-1`，kind 均为 `review_summary`，调度按执行协议隔离。[生成 Repository](../../../web/backend/classroom_web/infrastructure/review_generation.py) · [Worker](../../../web/backend/classroom_web/application/review_worker.py)

来源直接使用 `qa_sources`、`qa_source_versions`、`source_snapshots` 和 `source_items`。引用别名采用快照内的 `S1`、`S2` 等映射，保留 source ID、不可变 revision、内容与摘录 SHA-256、文本字符范围及已有课堂时间。来源授权和共享锁覆盖读取、发送及结果发布，来源变更后旧版本仍指向原冻结内容。[PR-07a 接入契约](../pr07a/CONTRACT.md)

`plan_review_context` 按 `all/recent30/recent120/range` 冻结最多 512 个完整来源单位。`partition_review_context` 按固定顺序和输入 Token 预算拆分，每批最多 64 个单位，计划最多 32 批。每批保留全局快照中的原别名，完整消息开销计入预算。单单位超预算和数量超限时显式拒绝。时间范围切入来源单位内部时返回 `SOURCE_TIME_UNALIGNED`，缺少课堂时间的来源仅支持 `all`。[上下文选择](../../../web/backend/classroom_web/application/review_context.py)

准备时保存全部批次的来源清单，返回实际 `estimated_batches` 和各批费用上限之和。根任务执行第 0 批，协调器随后逐批创建子计划、子任务和预留，成功结果留存在各自 Provider result 中。所有批次完成后，本地合并函数统一引用、合并等价重复内容并检查内容冲突及总长度，再由根任务原子发布总结和卡片。最终汇总只使用已经保存的批次输出。[批次协调](../../../web/backend/classroom_web/infrastructure/review_generation.py)

模型返回必须符合 `ReviewModelOutput`。总结包含结构化 notes，卡片类型为 concept、derivation、example、pitfall。结构验证检查字段、长度、重复 JSON key、来源别名、空答案、重复内容及英文成对字段。每个条目保留 `source_ids`、`missing` 与 `supplement`。模型原文先持久化，非法输出记录 `OUTPUT_INVALID`，授权用户可通过 raw-output 接口核对。[内容验证](../../../web/backend/classroom_web/domain/review.py)

## 持久模型

| 表 | 责任与关键关系 |
|---|---|
| `review_batches` | 根计划的不可变批次序号、全局快照与来源别名子集，以及一次绑定的批次任务和子计划 |
| `review_documents` | summary/card/note 的稳定身份、文档 revision、采用指针、最新个人稿指针、标题和理解状态 |
| `review_revisions` | 不可变 ai_candidate/personal 内容、父版本、来源快照、准确 Provider 原文引用和生成任务 |
| `review_document_history` | 每个文档 revision 的历史投影，支持精确版本读取 |
| `review_conflicts` | 冲突提交的个人版本、当时有效版本引用及文档 revision |

五张表启用 FORCE RLS。owner、课堂、计划、文档与来源使用组合外键，采用与个人稿指针绑定同一文档。数据库逐项验证批次别名来自对应全局快照，批次的 task_id 与 child_plan_id 允许从空值成对绑定一次。学习版本由根任务发布，数据库验证其快照属于根计划，raw 结果经 Attempt 关联到准确任务。版本、历史投影和冲突记录采用不可变约束。[数据库迁移](../../../web/backend/migrations/versions/pr07b_001_review.sql)

一次完整生成创建 summary 文档及其卡片文档。summary revision 保存本地合并后的完整结构化输出，每张卡片有独立稳定 document ID、内容 revision 和来源快照。`task_id + artifact_key` 唯一约束保护恢复重放，summary 的 artifact_key 为 `summary`。多批任务在所有有效结果齐备后才创建学习版本，候选版本、文档历史、根任务终态及最小 Outbox 事件在同一事务提交。历史版本的 `generation_task_id` 指向对应根任务，再由 `batch_tasks` 中的任务 ID 读取各批真实返回。个人版本返回空 generation_task_id，并保留 parent_revision_id 以导航原 AI 来源。[版本 Repository](../../../web/backend/classroom_web/infrastructure/review_repository.py)

## 版本与用户控制

首次生成和重新生成均追加 `ai_candidate`。用户保存会追加 `personal`，继承所编辑版本的来源快照。独立个人笔记允许空快照。`candidate_revision_ids` 列出可访问的 AI 候选，`latest_personal_revision_id` 记录成功保存的个人稿，`adopted_revision_id` 由用户 adopt 命令单独移动。理解状态由用户 PATCH 显式设置。生成耗时跨越用户编辑时，最终候选发布仍保留这三个字段各自的职责。

个人保存、采用及元数据修改使用文档 `base_revision`。个人保存遇到过期 base 时，事务先保存提交分支和冲突记录，再返回 HTTP 409，包含 `conflict_id` 和 `current_revision`。分支正文可从版本历史读取。`draft_id` 保留编辑来源身份，连续保存可以沿用同一个值，每次新的保存使用新的 Idempotency-Key 和观察到的 base_revision。相同操作键异体返回 `IDEMPOTENCY_MISMATCH`。

重新生成目标目前支持同课堂 summary 文档。准备请求冻结 `target_document_id` 与观察到的 `base_revision`，完成时追加候选。卡片通过所属 summary、卡型和 NFC/空白规范化题目的哈希稳定匹配，相同题目追加版本，新的题目形成新文档。历史卡片继续保留。文档 revision 同时覆盖元数据与内容变化，内容 revision 允许存在数值间隔。

## 权限与数据生命周期

HTTP 使用现有会话、CSRF、Origin、Fetch Metadata 和服务端主体授权。RLS 覆盖跨用户、跨课堂、账号状态、目录祖先及来源撤权。所有读取和收据重放重新检查当前权限，日志与事件仅记录受限状态和对象引用。

每个生成文档固定创建时的 `origin_snapshot_id`。该快照任一来源撤权后，整个派生文档及其后续个人版本都会隐藏。各内容 revision 还独立验证自己的来源快照。这个保守访问规则适用于重新生成后的文档，用户需要了解历史来源撤权对个人稿可达性的影响。

PR-04b 清理范围需纳入五张 Review 表、全局来源快照、根与子生成计划、逐批 Task/Attempt/Provider result 和必要账本证据。批次清单绑定根计划、子计划和任务，采用指针、个人稿指针、版本父链和卡片父文档构成学习内容引用关系。清理角色须依受控迁移处理不可变约束，并保留必要的幂等及费用记录。物理清理和备份恢复仍由 PR-04b 负责。

来源级 `revoked_at` 的独立故障域删除日志、备份恢复前墓碑与来源撤权重放继续作为已有恢复门禁。恢复旧备份时必须先恢复撤权事实，再开放正文读取。07b 的访问撤销实现与 04b 的物理清理、恢复联调需要共同验收。[PR-07a 删除接缝](../pr07a/README.md)

## 费用与故障恢复

测试注入 Fake Provider。继承的 Worker composition 要求 `simulation_only=True`，配置默认关闭。零计费配置限定 fake/mock 目的地和零费用上限。PR-11a 合成账本通过 `ReservationRequest` 与 `BillingTaskLifecycle` 接入。根预留在计划消费事务内建立，后续批次在调度时各自建立费用预留，通过 `quota_root_id` 引用根预留，共享一次总结额度。每个实际调用保留独立 Attempt、预留与 usage，发送前再次检查许可与状态。

发送 intent 提交后发生超时、Worker 崩溃或调用结果不明时，Attempt 保留 unknown。成功批次复用持久结果，unknown 子任务使根任务显示 `BATCH_RESULT_UNKNOWN` 并暂停后续批次调度。等待其余批次时根任务显示 `BATCHES_PENDING`。本地 reconcile 校验已有结果并尝试合并，后续新批次由 Worker 按冻结计划调度。每次预留仍受当时预算约束，后续批次预算不足时根任务记录 `BUDGET_BLOCKED`，已完成批次的原文与费用事实保留。

用户取消时协调器停止创建后续批次，已创建任务按持久发送事实处理。迟到结果受 fence 和任务幂等约束。用户取消、来源撤权和保存失败仍需处理已发生调用的 usage，费用核对走 metadata-only 路径。缺失 usage 或结算中断继续保留 unknown 保护。合并输出超过全局限制或包含冲突重复内容时，根任务记录 `OUTPUT_INVALID` 并保留各批原文供核对。

正式费用账本迁移、价格核验、真实调用许可接线及收费授权沿用 PR-11a 和 PR-07a 已有要求。当前调用次数、费用测试和恢复断言以实际验证记录为准。[验证记录](VALIDATION.md)

## 迁移与回退

新增 Alembic revision `pr07b_001`，父版本为 `pr07a_001`。迁移追加五张表及约束，并扩展全局快照容量、生成计划、根与子任务协议、收据资源类型和受限调度函数。PR-04b 并行迁移合并前需要复核单链，重新运行旧库升级、失败回滚、RLS、批次调度、删除和恢复集成测试。

验证数据库使用 `alembic upgrade head`。生产迁移遵循对应授权。自动 downgrade 拒绝删除版本和费用证据，应用回退先关闭 Review composition 与 Worker，保留 schema、历史、raw 和账本，修复通过增量迁移前滚。

## 下游接入

| 工作包 | 接入要求 |
|---|---|
| PR-08b | 复用文档 CAS、op_id 收据、稳定 draft_id、冲突分支和准确历史 revision；冲突解决与同步 outbox 接入时保留双方内容 |
| PR-10b | 全局、课程、课堂入口使用同一 document ID；展示候选、个人稿、采用稿、逐批进度、来源状态和缺失说明；引用导航使用服务器全局快照映射 |
| PR-11b | 汇总根与子任务状态、逐调用预留和结算、单次总结配额归属、unknown 风险和功能开关；管理角色继续遵守现有用户内容授权边界 |
| PR-04b | 协调迁移单链、批次与学习版本引用清理、独立删除日志及恢复前撤权重放 |

完整 Review 前端、离线同步、物理删除、实时 ASR 与 Filetrans 各按原工作包推进。当前后端接入方法和字段见候选契约。[API 与接入契约](CONTRACT.md)
