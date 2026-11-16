# PR-07b 候选 HTTP 与版本接入契约

负责人为林珈诚。候选标识为 `pr07b-1`，基于正式 OpenAPI 0.2.0 和 PR-07a 来源 overlay。生成入口为 `tools/pr07b/contract_candidate.py::build(base)`。正式 0.2.0 契约与现有前端生成类型升级继续遵循项目冻结流程，独立 Astra 审查及后续结论记录在审查文件。[候选生成器](../../../tools/pr07b/contract_candidate.py) · [独立审查](REVIEW.md)

## 应用组合

服务端显式构造 `ReviewBrowser(auth=..., database=..., config=..., billing=..., qa=...)`，注入 `create_app(review=..., contract=...)`。应用要求 `x-pr07b-candidate=pr07b-1`。共享 generation-plan/task 路由按持久计划或任务 kind 分派，qa 参数用于接入既有问答能力。

`ReviewWorker(database, scheduler, provider, worker_id=..., enabled=True, billing=...)` 协调根任务，并通过内部 ReviewBatchWorker 执行子批次。根任务协议为 `pr07b-1`，子批次协议为 `pr07b-batch-1`。其默认 enabled 为 false，继承的 Worker 保留模拟 Provider 限制。生产组合的启用、正式费用迁移与真实收费调用按已有授权执行。

## HTTP 路由

路径统一使用 `/api/web/v1` 前缀，响应采用 `Cache-Control: no-store`。写操作要求当前会话、CSRF、合法 Origin/Fetch Metadata 和 UUIDv4 Idempotency-Key。主体 owner 由服务端会话确定。

| 方法与路径 | 成功码 | 语义 |
|---|---|---|
| POST `/class-sessions/{class_session_id}/generation-plans` | 201 | 冻结总结范围、完整来源、参数、目的地与费用上限 |
| GET `/generation-plans/{plan_id}` | 200 | 读取计划和当前来源状态 |
| POST `/generation-plans/{plan_id}/execute` | 202 | 确认发送并持久提交唯一根任务 |
| GET `/tasks/{task_id}` | 200 | 查询任务、批次进度与结果 summary document ID |
| POST `/tasks/{task_id}/cancel` | 200 | 按持久发送事实处理取消 |
| POST `/tasks/{task_id}/reconcile` | 200 | 对已保存原文执行本地恢复 |
| GET `/tasks/{task_id}/raw-output` | 200 | 授权读取有界原始输出及校验状态 |
| GET `/source-snapshots/{snapshot_id}` | 200 | 读取冻结来源、别名、版本、hash 与定位 |
| GET `/review-documents` | 200 | 列出授权学习文档 |
| POST `/review-documents` | 201 | 创建个人笔记及首个 personal revision |
| GET `/review-documents/{document_id}` | 200 | 读取当前文档或准确历史投影 |
| PATCH `/review-documents/{document_id}` | 200 | CAS 更新标题或理解状态 |
| GET `/review-documents/{document_id}/revisions` | 200 | 分页读取可访问内容版本 |
| POST `/review-documents/{document_id}/personal-revisions` | 201 | 保存新个人版本 |
| POST `/review-documents/{document_id}/adopt` | 200 | CAS 采用同文档指定版本 |
| GET `/review-revisions/{revision_id}` | 200 | 读取不可变内容版本 |

列表支持 semester_id、course_id、class_session_id、kind、understood、limit 和 cursor。limit 为 1–100，默认 50。分页游标绑定 owner 和完整筛选条件。内容历史使用独立版本查询，文档 GET 的 revision 参数对应文档元数据 revision，缺失准确历史投影返回 409。

## 生成请求与候选增量

`GenerationPlanRequest.kind` 使用 `review_summary`。一次总结操作通过根任务和必要的子批次生成总结与所选卡片，scope 支持 all、recent30、recent120、range。`include_images` 必须为 false。`seen_sources` 为空时由服务器选择指定课堂的授权来源，显式列表进一步限定来源及确切版本/hash。

| 字段 | 约束与含义 |
|---|---|
| `card_types` | concept、derivation、example、pitfall 的无重复子集，最多 4 项 |
| `english_answer` | 请求英文答题表述及对应意思 |
| `granularity` | concise、standard、detailed，默认 standard |
| `target_document_id` | 同课堂已有 summary 文档，表示为其追加生成候选 |
| `base_revision` | 与 target_document_id 一起提交，冻结准备时观察到的文档版本 |

三个新增字段 granularity、target_document_id、base_revision 仅用于 review_summary。目标与 base_revision 必须成对出现。目标观察版本用于记录生成依据，用户编辑与 adopt 继续使用各自命令的 CAS。重新生成完成会追加候选，用户可以随后比较和采用。

执行沿用 ExecutePlan，确认 plan_sha256、目的地配置 revision、发送、原快照和范围。计划有效期为 10 分钟，同计划使用不同操作键执行仍关联原根任务。同操作键异体返回 `IDEMPOTENCY_MISMATCH`，配置变动返回 `PLAN_CHANGED`，过期返回 `PLAN_EXPIRED`。计划的 estimated_batches 为持久批次清单的数量，maximum_cost_microyuan 为全部批次最高费用之和，quota_units 为一次总结。

## 批次与任务进度

准备阶段冻结一个最多 512 个完整来源单位的全局快照。每批最多 64 个来源，计划最多 32 批，实际划分还受完整输入消息 Token 上界限制。`review_batches` 保存根计划、批次序号、全局 snapshot_id 和 source_keys。来源清单不可变，task_id 与 child_plan_id 成对绑定一次。第 0 批绑定根任务及根计划，后续批次按顺序建立独立子计划和子任务。

多批根任务的 Task 响应增加可选 batch_tasks，total_units 和 completed_units 表示批次总数与已有有效持久结果的批次数。单批任务继续使用既有任务字段。

| batch_tasks 字段 | 语义 |
|---|---|
| `batch_index` | 从 0 开始的固定序号，范围 0–31 |
| `task_id` | 已创建批次任务的 UUID，待调度时为 null |
| `state` | planned 或持久任务状态，有效结果已保存时为 succeeded |
| `source_ids` | 本批全局快照别名子集，1–64 个唯一值 |

第 0 批完成后，根任务在等待其余批次时为 running/BATCHES_PENDING。子批次结果不明时根任务进入 unknown/BATCH_RESULT_UNKNOWN，后续新批次等待核对。所有有效结果齐备后在本地合并，校验通过即原子发布学习版本并完成根任务。合并只执行确定性内容比较、引用与 missing 合并及全局结构校验。

跨批合并先对卡片题目或笔记正文执行 NFKC、casefold 和空白规范化，识别重复项，再对全部内容字段执行 NFC 和空白规范化并逐一比较。一致时合并引用和缺失说明，任一内容字段存在差异时触发 OUTPUT_INVALID，包括大小写差异。单批内的重复题目或笔记正文直接判为无效输出。合并后的数量、长度与引用仍受完整 ReviewModelOutput 约束，各批原文继续保留。

## 来源及模型输出

ReviewCard 与 ReviewNoteSection 的 `source_ids` 在候选 overlay 中限制为 S1–S512 的规范别名，包含 1–512 个唯一值，对应 PR-07a 的实际 source_snapshot.items。单次模型返回的引用必须属于该批来源子集，最终合并结果可保留多个批次的引用。服务器验证引用关系，模型输出中的任意资源 ID 均无法替代该映射。

`ReviewModelOutput` 保留 schema_version、cards 和 notes。最终 cards 最多 40 张，notes 为 1–80 条。按批次数为每批分配数量上限，分别采用 `floor(40 / batch_count)` 和 `floor(80 / batch_count)`，实际生成数量可以更少。notes 的 section 为 structure、focus、questions、slides。卡片包含题目、答案、补充、成对英文、引用和缺失说明，每条总结同样保留引用及缺失说明。完整渲染后的 summary Markdown 上限为 120000 Unicode 字符，各批原始输出和最终序列化结果均受 200000 字符和 262144 UTF-8 字节预算约束。

完整来源单位超出单批预算时返回 CONTEXT_BUDGET_EXCEEDED。来源超过 512 个或分批超过 32 批时返回 CONTEXT_LIMIT。SOURCE_TIME_UNAVAILABLE 和 SOURCE_TIME_UNALIGNED 用于报告范围与时间定位问题。输入分批始终保留完整来源单位。

候选 overlay 同时继承 PR-07a 的 SourceRef、SourceSnapshot.items、可空课堂时间和问答引用字段，将 SourceSnapshot.items 上限扩至 512，并增加 Task.batch_tasks。严格消费正式 0.2.0 schema 的客户端需要升级候选类型，正式合约冻结时需同步版本、fixtures、生成类型及最低客户端要求。[PR-07a 候选增量](../pr07a/CONTRACT.md)

## 文档与版本

`ReviewDocument.kind` 为 summary/card/note。文档保存 title、revision、created_at、updated_at、class_session_id、adopted_revision_id、latest_personal_revision_id、candidate_revision_ids、understood 和 source_status。其数据库 owner 与课程归属通过授权主体及课堂关系确定。

`ReviewRevision.kind` 为 ai_candidate/personal。内容包含 document_id、revision、父版本、source_snapshot_id、raw_output_ref、body_markdown、structured_content 和 personal_note。候选契约增加可选、可空的 generation_task_id，AI revision 返回该历史修订对应的根任务 ID，personal 返回 null，并可通过 parent_revision_id 追到 AI 来源。AI revision 的 raw_output_ref 绑定根任务第 0 批的实际 Provider result，多批 structured_content 保存本地合并结果。

新客户端从版本列表或版本详情取得 generation_task_id，读取 Task，再按 batch_tasks 中的 task_id 读取各批 raw-output。单批直接使用根任务 ID。每个历史重新生成版本各自绑定原任务，服务端每一步重新检查当前来源权限。personal revision 继承父版本快照，个人正文与备注归入 personal 来源。严格消费 additionalProperties=false 的旧客户端需要随候选契约升级该字段。

生成文档固定创建时的 origin_snapshot_id。该快照撤权会使整个派生文档及后续个人版本不可访问。可访问的历史文档投影保持原字段，source_status 按当前来源权限计算。正文必须通过版本端点重新授权。

重新生成使用既有 summary 文档身份。卡片的稳定匹配由 summary ID、卡型与规范化题目决定。每次完成追加 ai_candidate，内容历史通过 revision ID 定位。候选列表上限为 1000，内容写入在文档 revision 超过 10000 时返回 `CAPACITY_EXCEEDED`。

## 编辑、冲突与幂等

个人保存提交 base_revision、base_content_revision_id、draft_id、body_markdown 和 personal_note。正文上限为 120000 Unicode 字符，备注上限为 12000。相关 JSON 入站请求预算为 2000000 字节，同时执行字段级字符约束。客户端应保留原始文本直到获得明确保存结果。

base_content_revision_id 必须属于同一文档。生成文档的编辑需要明确父版本，独立个人笔记允许空父版本和空来源快照。draft_id 可在连续保存中保持稳定。新的内容保存使用新的 Idempotency-Key，重复提交相同操作键返回既有结果，相同操作键异体返回 409。

base_revision 过期时，提交内容以不可变 personal 分支保存，冲突记录保留当时的有效版本引用。事务提交后返回 `REVISION_CONFLICT`，错误对象携带 conflict_id 和 current_revision。客户端可重新查询文档和版本历史进行比较，完整离线冲突解决与专用交互由 PR-08b 接入。

candidate_revision_ids、latest_personal_revision_id 和 adopted_revision_id 分别表示候选列表、最新个人稿和采用稿。生成、保存和采用分别更新对应字段。采用请求只包含 base_revision 和 revision_id，成功后更新 adopted_revision_id。标题和 understood 使用 PATCH。内容版本号随文档写入时钟分配，客户端应使用服务端返回值。

## 故障、权限与费用

已有任务终态、unknown、取消、租约和恢复语义适用于根与子 review_summary 任务。各批结果先持久保存，结构无效时记录 failed/OUTPUT_INVALID，Provider 成功事实与 usage 独立保留。reconcile 使用持久原文和本地验证，允许完成已有结果的合并。新批次由 Worker 按冻结计划继续调度。最终发布失败回滚整个学习版本事务，已提交的批次原文仍可供后续恢复。

每次实际调用各有 Attempt、预留和 usage。子预留的 quota_root_id 引用根预留，整次总结共享一个 summary_month_count 额度。费用预留按批次创建并分别受当前预算、费用开关及许可控制。后续批次预算不足时根任务记录 BUDGET_BLOCKED。maximum_cost_microyuan 展示计划全部批次上限之和，settled_cost_microyuan 在所有批次均完成结算后返回合计，其余状态返回 null。

取消根任务时停止创建后续批次，已创建任务按各自发送状态取消或进入 unknown/cancel_requested。成功批次的原文和结算继续保留。已发送 unknown 批次等待持久结果或费用核对，后续批次调度暂停。

来源、目标或账号在发送前失效会阻止发送。发送后失效时按现有权限边界停止内容发布，调用事实和费用风险继续保留。usage 缺失或调用不确定时进入既有 unknown 保护，费用许可、预留和幂等结算沿用 PR-11a。所有自动化模型测试使用 Fake Provider，实际结果见验证记录。[验证记录](VALIDATION.md)

## 下游与迁移协调

PR-10b 应展示候选、个人稿、采用稿、逐批进度及来源状态，并使用服务器返回的全局 source_snapshot.items 导航。各批 raw-output 通过对应 task_id 读取，最终总结从根任务 result_id 指向的文档进入。Markdown、模型 raw 和个人内容均按不可信文本渲染。PR-08b 复用文档 CAS、操作收据、稳定 draft_id、冲突分支及准确历史 revision。PR-11b 消费根与子任务状态、逐调用费用、一次总结配额归属、既有功能开关和最小审计信息。

Alembic `pr07b_001` 接在 `pr07a_001` 后。PR-04b 合并前需协调迁移顺序，重验单头、五张 Review 表、根与子计划、Task/Attempt/Provider result 的清理及引用关系，以及恢复前墓碑和来源级撤权重放。批次清单与学习版本的不可变约束需要由受控清理迁移接入。生产启用继续遵循已确定的数据库、费用和恢复门禁。[架构与生命周期](README.md)
