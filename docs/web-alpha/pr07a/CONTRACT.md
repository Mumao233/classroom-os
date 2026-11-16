# PR-07a 候选 HTTP 契约与下游接入

基于正式 OpenAPI 0.2.0，候选标识为 `pr07a-1`。生成器为 `tools/pr07a/contract_candidate.py` 的 `build(base)`。HTTP composition 必须显式注入 `qa=QaBrowser(...)` 并提供对应候选契约。服务配置和 Worker 的 enabled 默认关闭。正式契约文件及现有生成前端类型保留原版本。

## 路由

全部路径使用既有 `/api/web/v1` 前缀、真实会话、标准错误结构及 no-store。写操作继续要求 Origin、Fetch Metadata、CSRF 与 UUIDv4 Idempotency-Key。

| 操作 | 路径 | 成功状态与语义 |
|---|---|---|
| 准备 | POST `/class-sessions/{class_session_id}/generation-plans` | 201，冻结问题、来源、摘录、目的地及费用上限 |
| 预览 | GET `/generation-plans/{plan_id}` | 200，重新授权并报告当前 source_status |
| 执行 | POST `/generation-plans/{plan_id}/execute` | 202，问题、任务、预留、计划消费和收据已提交 |
| 任务 | GET `/tasks/{task_id}` | 200，终态、费用风险及结果/快照 ID |
| 问题列表 | GET `/questions` | 200，可按 class_session_id、limit、cursor 查询 |
| 单项回答 | GET `/questions/{question_id}` | 200，问题、回答和已验证引用别名 |
| 来源 | GET `/source-snapshots/{snapshot_id}` | 200，实际使用的不可变摘录与定位 |
| 取消 | POST `/tasks/{task_id}/cancel` | 200，返回当前持久状态 |
| 本地核对 | POST `/tasks/{task_id}/reconcile` | 200，仅处理已持久结果，new_submission=false |

准备请求沿用 GenerationPlanRequest，kind 支持 ai_question，scope 支持 all、recent30、recent120、range。seen_sources 为空时从指定课堂的已授权持久来源中选择，非空时进一步限定来源和确切版本/hash。include_images 必须 false。来源不含真实课堂时间时，范围筛选不能把它当作具有时间对齐的转写。

执行请求沿用 ExecutePlan。来源已修订时，需要 confirm_original_snapshot。范围存在截断或无法对齐的跨边界片段时，需要 confirm_partial_scope。计划消费后，更换操作键仍返回相同任务。相同操作键异体返回 IDEMPOTENCY_MISMATCH。旧收据重放必须通过当前授权。

取消沿用 PR-02b 语义。未派发任务进入 cancelled，已派发任务保留 cancel_requested 或 unknown，已保存输出仍可本地完成。reconcile 同键第一次无输出、迟到输出保存后再次调用可以完成，后续重放不会再次发布终态或调用模型。

## 候选增量及兼容性

- SourceRef.kind 增加 course_material_version。已有穷举来源类型的消费者需要先升级。
- SourceSnapshot 增加必填 items，start_ms/end_ms 可为 null，表示资料缺少课堂时间。
- 每个 item 包含 alias、kind、source_id、fragment_id、object_id、revision_id、class_session_id、content_sha256、excerpt_sha256、excerpt、excerpt_start、excerpt_end、start_ms、end_ms、visible_at 和 origin。
- Question 增加必填 cited_source_ids，对应本快照的 S1 等别名。新增单问题 GET。

这些增量需要消费者采用候选 schema。旧 0.2.0 客户端的严格对象校验无法直接接受新增必填字段和可空时间，正式合并契约须更新版本、fixtures、生成类型和最低客户端要求。候选注入是当前集成边界。

excerpt_start/end 为原始 Unicode 文本的字符位置，end 为开区间。content_sha256 对应原始版本，excerpt_sha256 对应送入模型的 UTF-8 摘录。fragment_id 标识稳定来源片段，revision_id 标识本次实际采用的不可变内容。origin 保留 manual/material/live_asr/import，合成测试使用 manual。

## 明确错误与资源限制

空课堂或无可见上下文返回 CONTEXT_EMPTY/CONTEXT_INSUFFICIENT。过期或配置漂移返回 PLAN_EXPIRED/PLAN_CHANGED。无权限和撤销后的内容拒绝访问，错误响应不含原文。非法模型引用持久标记 OUTPUT_INVALID，费用事实独立保留。

当前技术限制为每课堂 512 个来源、每版本 32768 UTF-8 字节、每来源 100 个修订、最多 12 个摘录、每用户每分钟 12 个准备成功的计划、10 分钟计划有效期及最多 4 个活跃 QA 任务。HTTP JSON 请求体继续受既有 16 KiB 上限约束。输入采用完整 system/user 消息 UTF-8 字节数加消息开销的保守 Token 上界，输出另行限长。这些边界覆盖服务器资源和合成接入测试。

## 下游

06b 在授权事务中调用 add_source/revise_source，仅持久 final 内容成为来源。07b 可复用冻结快照，另定义其输出 schema 和任务类型。08a 缓存保留快照版本，同时执行撤权和删除失效。10a 按准备、确认、执行、轮询、回答、来源的顺序接入。回答 Markdown 必须按不可信内容安全渲染，引用导航使用服务器返回的 cited_source_ids/items 映射。

实时流式前端、图片理解、跨课堂范围、主动新收费 Attempt、Provider 查询、课后总结和理解状态编辑仍需各自后续实现。未公开的内部来源写入方法不能直接作为无鉴权上传端点。
