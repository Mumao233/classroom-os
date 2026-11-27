# PR-11c 第一阶段交接

负责人林珈诚。工作树 `J:\class-assistant\worktrees\web-pr11c-search`，分支 `feature/web-pr11c-search`。开工时 HEAD 与本地记录的 `origin/main` 均为 `80d2204d4578d6673a1ce74d1f60097d97b372cb`。本阶段由 Claude 管理远端核对、功能测试、复审和提交。依据 OpenAPI 0.3.0、WS protocol 1 与 UI-REVIEW-10、UI-REVIEW-12、UX-03、FE-12、AUTH-05。[OpenAPI](../architecture/contracts/openapi.json) · [WS schema](../architecture/contracts/events.schema.json) · [追溯表](../architecture/10-TRACEABILITY.md)

## 搜索、通知和反馈

- `create_app(..., operations=OperationsService(auth=auth, database=db), deletion=deletion)` 使用共享 HTTPS、Cookie、CSRF、Idempotency-Key、请求边界和 04b 恢复门禁。搜索结果只来自同一认证和数据库下已有可用 HTTP 读取入口。课程依赖 `CourseReader`，课堂详情依赖 Filetrans 读取，转写依赖 ASR 或 Filetrans 读取，复习依赖已启用的 `ReviewBrowser`。
- 搜索去除首尾空白，对正文使用数据库 `lower` 和经过转义的 `LIKE`。百分号、下划线、感叹号均按字面匹配。课程名和课堂名在权威表上使用 `pg_trgm` GIN。转写和复习正文只根据 live 关系与当前指针查询，使用 owner/版本范围和语句时限，没有单独存储正文索引副本。未指定正式采用版本时沿用既有课堂规则，当前实时录音的正式段可查，各次重复转写候选须显式选定。复习查询返回显式采用稿和最新个人稿，同一文档两个命中可以有不同 `revision_id`。
- 搜索每次读取校验已激活账号、当前会话、课程或课堂筛选归属、删除墓碑及相关祖先。复习沿用 revision/source snapshot 的 RLS，转写沿用课堂、原材料课程与学期的 live 条件。删除事务提交及删除日志重放后，权威查询即时使用新状态。旧游标只携带排序锚点，每页重新授权。HTTP 在恢复门禁未打开时返回 `503 SERVICE_DISABLED`。
- 查询为 1–200 字符，默认 30 条，最多 100 条。1–2 字符查询的数据库语句上限为 1 秒，超时返回 `429 RATE_LIMITED`。游标使用 `Database.cursor_secret` 签名并绑定 owner、操作、关键词、过滤和页宽。非法或跨绑定游标返回 `422 VALIDATION_FAILED`。他人或已撤销的搜索过滤目标返回 `404 NOT_FOUND`。
- 搜索响应开始前清除 ASGI 查询串，覆盖正常响应、校验失败和恢复门禁拒绝。复习搜索覆盖当前可读修订的 Markdown、个人备注和标题，以及阅读器展示的笔记标题、正文、补充、卡片标题、正反面、补充、英文及释义和缺失说明。片段取自实际命中的字段。来源标签仍作为定位元数据。
- 搜索翻页或打开结果收到 401、`SERVICE_DISABLED` 等非传输拒绝时，组件清空旧条目、片段和游标。打开单条结果时，404 或 410 只撤回该条。只有明确标为网络传输失败的错误可以保留前一页并提示重试。
- 搜索、标已读和提交反馈计入会话活动。通知列表同时服务前台打开和后台轮询，当前统一保持非触碰读取，WS 握手和重放也保持非触碰读取。
- 通知 mark read 按契约要求请求体 `{"read": true}`（`NotificationRead`），返回 `Notification`，重试保持原 `read_at` 并重新检查可见性。他人和缺失通知匹配 `404 NOT_FOUND`。已读持续故障仍可见，真实解决后保留 `resolved_at`。通知事件只携带 ID、severity、固定 message code。
- 反馈含非空片段且未确认返回 `422 CONSENT_REQUIRED`。无同意的持久片段值只能是 NULL。每账号在 10 分钟内最多接受 10 个新报告，现有 Idempotency-Key 重放复用首个 `202` 报告关联 ID，计数由 owner 事务锁串行化。`request_id` 可保存作关联，永不据此查正文。收据存 HMAC 与报告 ID。`feedback_admin_metadata` 与 `feedback_authorized_excerpts`（只含已确认片段）两个投影目前**不向任何读取角色授权**：负责人 2026-11-24 决定收回直接视图读取，待有受控、分页、逐页复核放行与墓碑的管理端读取接口时再开放。两个投影都按 04b 账号墓碑及擦除状态即时撤回对应报告。Web Admin 身份不授予这两个数据库角色。应用记录有限异常类型和栈帧，部署日志与 APM 也须维持正文和绑定参数采集关闭。
- 04b 开始核对删除日志或进入恢复封存时撤下反馈读取凭据，只有现有门禁放行才重新发布。两种直接 SQL 投影均只在 READ COMMITTED 语句中返回数据，并核对数据库 OID、凭据表 OID、PostgreSQL 启动时间和凭据行的写入事务。原地恢复回写的行具有新的 `xmin`，即使前三项一致也无法放行。重放后仍排除已擦除账号。迁移拒绝 runtime 组与任一反馈读取角色的直接或间接成员重叠，运行时事务同时检查当前角色与会话登录角色。

### Locator 与打开行为

| `object_type` | `locator` | `revision_id` | 第二阶段重新授权与定位 |
| --- | --- | --- | --- |
| `course` | `course/{course_id}` | null | GET 课程详情后打开 `/courses/{course_id}/overview` |
| `class_session` | `classroom/{class_session_id}` | null | GET 课堂详情后打开 `/classrooms/{class_session_id}/record` |
| `transcript_segment` | `transcript/{class_session_id}/{transcript_version_id}/{segment_id}` | 正式段的 `segment_revision_id` | 列该转写版本的段并核对 segment 与 revision，传 `transcriptVersion`、`segment`、`segmentRevision` 给课堂记录页 |
| `review_document` | `review/{document_id}/{revision_id}` | 精确复习修订 UUID | GET 文档与指定修订后打开 `/review/items/{document_id}?revision={revision_id}` |
| `card` | `review/{document_id}/{revision_id}` | 精确卡片修订 UUID | 与复习文档共用入口和读取方式，核对文档为 card |

注入的 `openResult` 负责完成上述授权及导航。404、410、`NOT_FOUND` 或 `GONE` 移除结果。切换账号或进入离线状态后立即丢弃当前查询结果。定位参数由第二阶段的现有阅读页消费。

冲突通知的 `object_id` 是 `conflict_id`。打开时以它调用 `GET /api/web/v1/sync/conflicts/{conflict_id}` 重新授权，再交给冲突编辑器。冲突详情另含受影响对象的 `object_id`。

### 通知生产者

| 持久源 | Kind / message code | 去重 / 解决 |
| --- | --- | --- |
| `outbox_events` 中 task.changed → succeeded | `task_completed / TASK_COMPLETED` | 按 task UUID 去重，读取时复用任务、课堂、生成计划与任务类型绑定的活源判定 |
| `sync_conflicts` 以及 `sync_resolutions` | `conflict / SYNC_CONFLICT` | 按 conflict UUID 去重，0019 补发升级前仍未解决的冲突，解决记录更新 `resolved_at`；相关源撤权即不可见 |
| `capture.lease_lost` | `capture_failure / CAPTURE_LEASE_LOST` | 按丢失的 capture epoch 去重；新租约无法证明旧录音已恢复，当前没有可确认解决的事件 |
| 04b `object.deleted` 对应已确认音频的系统到期执行 | `audio_expiry / AUDIO_EXPIRED` | 按课堂去重，避免分片各自产生通知；课堂删除后不可见 |
| `audio.chunk_durable` | 上传 ACK 保持原事件 | 分片 ACK 不生成通知 |
| 浏览器缓存写失败、仅客户端失败 | `save_failure` 待持久源接入 | 需要与真实失败配对的恢复证明，避免把取消或验证错误当保存故障 |
| 身份撤权、设备撤销、账号禁用 | `security` 待审批事件入口 | PR-03b 要求身份撤销使用独立内部队列，避免与业务 counter/outbox 竞争；需 Astra 选择桥接和权限失效后的收件范围 |

已接入的生成路径通过同事务触发器写 notification/outbox。既有历史 outbox 不批量回填。启用 PR-11c 后，事件 socket 在 ASR 关闭时也能推送通知。WS 重放、列表和 read 共用实时可见性。重放游标会越过已失效通知，以延续 owner 事件流。

QA 与复习任务的生成计划沿用快照与来源 RLS。快照任一来源撤权后，完成通知从列表和 WS 重放隐藏，标已读会返回 `404 NOT_FOUND`。通知中心收到确定性的 404 或 410 时移除该项并清掉待重试的键。

通知列表刷新带请求序号，已读结果使尚在途的旧列表失效。列表、翻页或标已读收到 401、403 或 `SERVICE_DISABLED` 时清空条目和游标，作废在途请求。合并分页与已读状态时保留服务端的微秒时间排序，同一时刻按 UUID。合成 mock 在所有接口入口拒绝已撤销账号，并在列表和标已读时检查通知的对象及祖先范围。

### 预算映射建议

周期阈值来自平台级 `billing_control.notification_outbox` 和 `billing_api.threshold_messages`。建议保留平台级 projection，由拥有预算运营权限的 Admin 在管理端接收，按账期和阈值去重。应保留原周期和阈值，并用预算权限限制领取、投递及确认。若产品要求将其投递到用户通知中心，先由 Astra 定义预算专用 kind、owner 收件规则、可见字段和契约版本。学生在自身额度受限时沿用现有额度视图和任务状态。

### 缓存与契约问题

- `TrustedCache.read(reference)` 可以只读已知引用，`listPending` 仅用于草稿及录音恢复。现有公开接口缺少按当前授权代际枚举 learning 条目的入口，也缺少一套可验证的搜索内容解码协议。第一阶段离线界面显示范围不完整，等待缓存模块提供授权枚举并复用加密读取和墓碑校验。
- 0.3.0/04b 没有 review document 单独删除命令、相应墓碑类型或恢复重放条目。阶段测试覆盖课堂/课程删除、来源撤权和 04b 重放导致复习文档不可访问。文档独立删除用例需要 Astra 决定是否新增契约与生命周期。当前 `locator` 格式以及反馈 202 关联 ID 的稳定语义同样请 Astra 核对。
- 当前通知契约缺少持续故障标准解决信号，也未定义新采集恢复是否解决旧 epoch 故障。请 Astra 确认 capture、save、security 的事件责任与恢复证据。
- 正文 GIN 仍是性能决策。当前活指针查询避开为历史及候选建立正文索引。请用合成中文数据量验证 PostgreSQL locale、执行计划和短查询超时，再决定是否追加只维护当前可读内容的 trigram 投影。
- 反馈管理端的正式 HTTP 投影、角色分配与保留期限需运营与契约决策。阶段一交付数据库级两个投影，不增加 /admin 路由。

## 文件清单

| 范围 | 文件 |
| --- | --- |
| 后端入口 | `classroom_web/entrypoints/http.py`、`operations_http.py` |
| 后端服务与数据 | `classroom_web/application/{operations.py,asr_events.py,deletion.py}`、`classroom_web/infrastructure/{operations_repository.py,deletion_repository.py,database.py}` |
| 迁移 | `web/backend/migrations/versions/0019_search_operations.py`、`0019_search_operations.sql`。前驱 `0018_filetrans`，已检查本地未合并分支的迁移路径，含 PR-11b，未见 0019 冲突。 |
| 前端 domain | `web/frontend/src/domain/search.ts`、`notifications.ts`、`feedback.ts` |
| 前端 mock | `web/frontend/src/mock/search-backend.ts` |
| 前端界面 | `features/search/{SearchForm.tsx,CommandPalette.tsx,search.module.css}`、`pages/search/{SearchPage.tsx,SearchPage.module.css}`、`pages/notifications/{NotificationCenter.tsx,NotificationCenter.module.css}`、`features/feedback/{FeedbackDialog.tsx,FeedbackDialog.module.css}` |
| 测试 | `web/backend/tests/integration/{conftest.py,test_pr11c_operations.py,test_pr11c_migrations.py}`、`web/frontend/tests/pr11c-{domain,mock,components}.test.ts` |
| 工具与证据 | `tools/pr11c/{prepare_vmware.ps1,pending-source-files.txt,guest.ps1,run_guest.py}`、本交接、[持久证据](../evidence/PR11C-PHASE1.md)、`outputs/pr11c/STATIC-CHECKS.txt` |

## VMware 验证

Claude 实测结果见证据目录 `outputs/pr11c/<job-id>/` 内的证据包与 broker 收据。用其中的 `results.json`、`pytest.xml` 和 `vitest.xml` 核对对应源码清单、退出码、测试数、失败与跳过。Astra 第 2 轮修复后的 VMware 回归待 Claude 执行。

在当前 worktree 使用已运行的 03b broker 打包并提交，`targeted` 跑新增服务与迁移用例，`full` 跑 backend 全量（包括现有可启用的本地对象服务和浏览器用例）及 Vitest 全量。来宾 runner 设置 `PR11C_OPERATIONS_INTEGRATION=1` 才收集 PR-11c 服务用例，并绑定来宾 PostgreSQL 日志。迁移测试仅依赖合成 PostgreSQL，旧 runner 也会收集。主机以 `HEAD` 树为路径基线。打包候选中所有 `HEAD` 之外的路径都必须在 `tools/pr11c/pending-source-files.txt` 中，不论已经暂存或仍未跟踪。拒绝发生在读取源码和创建归档前。

```powershell
$job = & pwsh -NoProfile -File tools/pr11c/prepare_vmware.ps1 -Suite full | ConvertFrom-Json
$brokerRepo = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$broker = Join-Path $brokerRepo 'work/pr03b/vmware-broker'
$submit = Join-Path $brokerRepo 'tools/pr03b/vmware/submit.ps1'
& pwsh -NoProfile -File $submit -Id $job.id -Script $job.script -TimeoutSeconds 3600
$guestExit = $LASTEXITCODE
$copy = "$($job.id)-evidence"
& pwsh -NoProfile -File $submit -Id $copy -Kind copyFrom -GuestPath $job.guest_evidence
$out = New-Item -ItemType Directory -Path "outputs/pr11c/$($job.id)"
Copy-Item -LiteralPath "$broker/incoming/$copy/evidence.zip","$broker/done/$($job.id).result.json" -Destination $out
Get-FileHash -LiteralPath (Join-Path $out.FullName 'evidence.zip') -Algorithm SHA256
"guest exit $guestExit"
```

来宾入口验证源码 zip 与逐文件 SHA-256，复用准备好的 Python、Node、SeaweedFS 与 PostgreSQL 17 工具链，将 Node 依赖复制到独立快照。pip 离线依赖、临时文件和缓存均保存在 `work/pr11c` 下。来宾命令为 `python -I -B tools/pr11c/run_guest.py --suite full --pg-bin <pg-bin> --weed <weed.exe> --node <node.exe> --dependency-root <pr03b-guest-root> --deps <work/pr11c/deps>`。PostgreSQL 集群位于 `work/pr11c`，日志沿用旧 runner 所需的 `outputs/pr03a/third-round`，回收证据放在 `outputs/pr11c`。普通资料使用合成对象，网络服务仅绑定 loopback。

runner 在首次连接前清除自身和子进程环境中的 `PG*` 变量，并校验 loopback 目标及数据目录。pytest、Vitest 和构建各自使用 kill-on-close Windows Job Object，进程进入 Job 后才启动，超时和正常退出均清理其进程树。`guest.ps1` 继续检查残留进程。

| 用例 | 预期观察 |
| --- | --- |
| 搜索与过滤 | A/B 查询、课程和课堂过滤、字面通配字符、短中文、已保存个人稿、游标防篡改和跨账号；结构化字段能命中并显示实际片段；访问日志无搜索词 |
| 删除、撤权、恢复 | 课程/课堂删除及复习来源撤权后无命中或片段；真实 pg_dump + 04b 独立日志重放后无命中 |
| 通知 | 重放、并发完成事件只生成一条；升级前的未解决冲突补发一条，可以打开详情并在解决后看到 `resolved_at`；会话撤销后客户端清空列表；任务源撤权和租约接管等既有覆盖保留 |
| 反馈 | CSRF、同意、每账号幂等、A/B 关联；两个读取角色读视图与基表均被拒绝；以投影属主覆盖新库恢复、原地回写和长事务下的行策略；拒绝重叠的 runtime 登录；日志、outbox、审计和收据无正文标记 |
| 费用和迁移 | 搜索前后任务、预留和 usage 收据数量一致；空库和 0018 基线升级到 0019，重跑保持同一 head |

## 第二阶段待办

1. 11b 合并后接入 client 的四个方法、路由与组件依赖。账号键包含授权代际，`openResult` 在导航前按 locator 重新授权，目标页面迁入对应定位参数。
2. 在应用 shell 接入 Ctrl/⌘K、焦点返回，以及当前采集仍可停止的 Aurora 要求。
3. 注册合成 mock handlers 与账户撤权桥接，订阅 `notification.created` 刷新当前账号列表。
4. 增加浏览器 e2e 验证快捷键、搜索定位、墓碑、账号切换、通知、反馈确认和离线显示。待缓存接口确认后接入 learning 离线搜索。
5. 回收本轮 VMware 证据并交 Astra 复审，核定通知缺口、预算映射及独立文档删除归属。
