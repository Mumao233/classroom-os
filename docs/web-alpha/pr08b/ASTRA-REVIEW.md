# PR-08b「离线同步与冲突」独立 Astra 初审

审查日期：2026-11-17。负责人：林珈诚。审查角色：独立 Astra 代理；父任务确认本次 spawn 显式指定 `gpt-6-astra`、`reasoning_effort=high`、`fork_turns=none`，未继承实现会话。角色和模型指定不等于独立实机执行证明。

- 工作树：`J:\class-assistant\worktrees\web-pr08b-offline-sync`。
- 分支：`feature/web-pr08b-offline-sync`。
- main 基线：`8b1e44e5fd87f43f0e675856de68dce069d8080f`。
- 冻结审查 HEAD：`325d687bb13ecb5165379a0c27cd2b8f3525df53`，34 文件、+4206/-17；审查开始时工作树干净。
- 产品版本：Web Alpha 0.1.0；HTTP `info.version=0.2.1`，显式组合既有 PR-07b overlay `pr07b-1`。

## 结论

**请求修改，当前冻结 HEAD 不通过。** 发现 1 项 P1、3 项 P2；未发现有实际依据的 P0，本轮不另列 P3 缺陷。4 项均来自实际代码路径的静态分析，尚未由本审查者在 VMware 独立复现。既有 VMware 全绿证据可信地覆盖其已运行场景，但未覆盖下面的部分来源撤权、两处确定性锁环和旧冲突更换 base 的场景。

实现方已收到全部发现和定向回归步骤。后续修复与新测试不能倒写为此冻结 HEAD 已通过；应在本报告追加新 HEAD、修复差异和增量复审结论。Draft PR 可以如实携带未闭环项；本报告不授权合并、部署、生产迁移、启用同步或调用付费 API。

## 发现

### R1 · P1：文档仍可见时，部分来源撤权不会清除冲突明文，重新解锁也缺少准确版本授权

**位置（冻结 HEAD 行号）：** `web/frontend/src/sync/runtime.ts:169-173`、`:227-237`、`:260-266`、`:285-302`；`web/frontend/src/sync/ConflictPanel.tsx:43-57`。服务端依据为 `web/backend/migrations/versions/pr07b_001_review.sql:148-159` 与 `web/backend/migrations/versions/0014_offline_sync.sql:91-99`。

**触发条件：** Review 文档最初基于来源 A 创建，随后基于 A+B 产生新候选/个人版本和冲突；撤销 B，保留 A。文档的 `origin_snapshot_id` 仍有效，故文档仍进入 bootstrap；依赖 B 的准确 `review_revisions` 与 `sync_conflicts` 则被 RLS 隐藏。来源触发器产生同版本 `object.changed`，但 Changes 对仍可见文档给出 `deleted=false`。

**影响：** runtime 对普通变化只清理 learning 副本，不发出 `locked`；新 bootstrap 也只比较对象 ID/类型是否消失。已打开的 ConflictPanel 只在 `locked` 时清除 `conflict/body/note`，因此仍展示已撤权版本的正文。`load/readDraft` 也只依赖文档级投影；仅补一次 lock 后，重新 unlock 仍可能解密基于 B 的旧草稿。文档授权不等于任意内容版本授权。

**建议：** 复用既有准确 `GET /review-revisions/{revision_id}` 与 conflict GET 建立当前解锁周期内的版本授权，不新增公开 schema。个人稿绑定其准确 `base_content_revision_id`；resolution envelope 同时复核原冲突的冻结版本仍可见。相关 Review 事件应先使展示明文失效，再重核授权；迟到返回须受 epoch 防护。重新解锁、恢复和草稿读取不能仅凭文档投影恢复权限。保留唯一草稿密文为 restricted，不将某个内容版本撤权误写成整个仍有效文档的永久墓碑。学习正文也需准确内容版本绑定，不能仅绑定文档 CAS。

**VMware 回归：** 真实 PostgreSQL 创建 A 文档→新增 B→第二候选/个人稿→冲突；真实 HTTP 浏览器打开 ConflictPanel，撤销 B；证明文档 GET/bootstrap 仍可见、准确版本和冲突 GET 已 404，同时面板清明文、旧读取/迟到响应被拒绝、密文保留；再次 unlock 后仍不能读该 B 草稿，A 的有效数据继续可访问。现有 `test_source_revocation_emits_change_and_revokes_all_sync_reads` 撤销的是 origin 来源，不能代替此场景。

### R2 · P2：课程冲突解决与直接新建笔记存在 Review/课程锁环

**位置：** `web/backend/classroom_web/infrastructure/sync_repository.py:312-324`，关联 `:124-128`；`web/backend/classroom_web/infrastructure/review_repository.py:298-303`；`web/backend/classroom_web/infrastructure/task_sources.py:49-60`。

**触发条件：** 同一 owner、同一课程并发执行直接 07b `create_note` 与课程的 sync conflict resolve。前者先通过 `sources._class` 取得课程 `FOR SHARE`，然后等 owner `-review` advisory lock；后者先取得 `-review`，然后通过 `current(course, lock=True)` 等课程 `FOR UPDATE`。

**影响：** 两个合法请求形成相反锁序。PostgreSQL 必须回滚其中一项，HTTP 将表现为存储失败或超时；正在新建的笔记或显式冲突决策不能完成。数据事务回滚并不消除这一正常并发场景的可用性缺陷。

**建议：** 按具体 resolution 获取必要锁，避免无条件先持 Review 序列锁再获取课程排他锁。只读 CAS 冻结可以采用共享锁；课程 edited_merge 可保持排他锁，但不应无故获取 Review 锁。必须同时复核 copy_to_new_note 的目标课堂及直接 07b 路径，不仅调整同步内部顺序。

**VMware 回归：** 两连接与 Event/barrier 确定性构造上述持锁顺序，执行真实 create_note 与 course resolve，要求无 deadlock/超时、两项实际提交，且准确 CAS、收据和笔记正文不变。现有 `test_deferred_catalog_events_do_not_invert_review_lock_order` 只覆盖新学期插入与 Review，不覆盖同课程共享/排他锁。

### R3 · P2：直接 07b 冲突补齐在事件水位锁后再取得来源锁，与来源撤权提交形成锁环

**位置：** `web/backend/classroom_web/infrastructure/review_repository.py:390-398`，关联 `:353-366`、`:244-268`；`web/backend/migrations/versions/0014_offline_sync.sql:198-208`；`web/backend/classroom_web/infrastructure/qa_repository.py:183-201`。

**触发条件：** 文档 origin 为 A、当前云端个人稿基于 A+B，直接 07b `write_personal` 提交基于 A 的旧版本。该请求在 `_advance`→`_event` 后持有 owner counter 锁，新增 `_sync_conflict` 再读取当前 B 版本并在 `_snapshot_row` 请求 B `FOR SHARE`。同时另一连接 `revoke_source(B)` 已持有 B `FOR UPDATE`，其新增 deferred source-event trigger 提交时等待同一个 owner counter。

**影响：** Review 等 B，撤权等 counter，形成数据库锁环。直接 07b 路径受到 08b 的新增逻辑影响；同步 apply 提前读取 current 并不能保护直接 07b 入口。不能以延迟触发器普遍消除了锁序问题作结论。

**建议：** 在写事件/占 counter 之前准确冻结并取得冲突云端版本所需的来源锁，将已授权的准确内容传给补齐步骤；保持既有隐藏历史版本的 fail-closed 行为，不用另一个可见候选替代。审查旧收据补齐路径与首次冲突路径，避免在持 counter 后发现新的来源依赖。

**VMware 回归：** 使用上述 A/A+B 版本布局，在 `_sync_conflict` 前和来源撤权取得 B 排他锁后设置 barrier，提交两个真实事务；要求无 deadlock、撤权最终生效、原提交分支按事务结果准确保留、事件序列连续、重复请求不新增分支。测试须覆盖直接 `writePersonalRevision`，不能只测 syncOperation。

### R4 · P2：旧 conflict 可以通过换成最新 base_revision 绕过冻结云端版本

**位置：** `web/backend/classroom_web/infrastructure/sync_repository.py:321-335`；前端对应约束已在 `web/frontend/src/sync/runtime.ts:599-606` 实现，但服务端缺失。

**触发条件：** 某冲突冻结的云端版本是 v3，另一设备已将对象推进 v4。客户端向原 conflict_id 提交 `base_revision=4, resolution=use_server`，或提交以 v4 为 base、内容仍来自旧比较的 edited_merge。当前服务端只要求请求 base 等于实时 revision，没有要求冻结 `conflict.server_revision` 同样等于该版本。

**影响：** 公开 HTTP 端口允许将旧冲突直接标记 resolved；edited_merge 还能把旧比较的内容写入最新对象。现有 runtime 不这么做，但服务端的冻结比较和再次 CAS 不变量不能依赖某一个客户端实现。客户端收到冲突后简单刷新 base 重发即可绕过显式重新比较。

**建议：** 在解决前要求请求 base、冻结 server_revision、锁定后的当前 revision 三者一致，否则返回 REVISION_CONFLICT，保持原冲突与草稿。重新核对必须建立准确的新冲突比较，而非仅替换数字。

**VMware 回归：** 在 `test_resolution_checks_newest_cas_and_typed_object` 增加原 conflict_id 换最新 base 的三种 resolve 请求；应全部 409 且不新增 resolution、不写正文。随后通过显式新比较再解决成功。现有测试只校验旧 base 被拒绝和异类型 merge，不校验此绕过。

## 已核对范围与未发现新增缺陷的部分

已读父级和仓库 AGENTS.md、Web README/TESTING、PR-08b 数据/接入/验证文档、实施图和前端交接的相关范围。按 task-workflows 选择表检查后，本任务无匹配的专用工程审查工作流，采用仓库要求的实际源码、契约与证据审查，没有套用论文审查技能。

实际检查覆盖新增 SyncBrowser、PgSyncRepository、0014 SQL/迁移、HTTP 装配变化、Review 补齐变化、完整 runtime/ConflictPanel/transport/认证工厂、trusted-cache/types 改动；向下追踪 main 的 database、catalog、task_events、task_sources、QA 和 Review owner/RLS/来源逻辑。检查新增后端测试、主要前端恢复/撤权用例、正式浏览器 harness、runner 和两份旧任务断言差异。未逐条重审整个仓库所有既有业务或所有历史安全结论。

- bootstrap 使用同一 REPEATABLE READ authorized_transaction；完整投影与 committed owner counter 在同一快照读取。事件 replay 检查连续序列、owner 签名和缺页；运行时不把 push 返回 cursor 当作已消费水位，刷新快照保留旧游标补消费删除。这些机制没有在本轮发现新的跳水位缺陷，锁序问题另见 R2/R3。
- 外层批次摘要、内层完整 typed 操作摘要和独立事务保留幂等边界。unknown 先读原 receipt，只有明确 NOT_FOUND 才按原键重交；applied ACK 绑定 operation/hash/object 和准确缓存 generation。冲突和失败不会确认清理唯一草稿；多标签缺失记录会回查收据。
- 4 种 typed 操作和 3 种解决方式有实际实现；Review 个人正文与 personal_note 另存副本均保留。多 AI 候选冻结读取历史候选列表并保留准确生成版本，完整 `S1..S512`、structured_content 与 generation_task_id 使用已有 overlay。旧 07b 冲突无精确输入时显式 INCOMPATIBLE_CLIENT，原请求经指纹验证后才补齐。
- 新表启用并强制 RLS；ownership probe 不接受 owner 参数、只返回固定类型下本人对象存在性，PUBLIC EXECUTE 已撤销。新正文沿用课堂、文档与准确来源的授权层级。来源事件只暴露标识与版本，但浏览器版本级授权还有 R1。
- 0014 对 users.preferences 直接复制 JSONB，未施加额外对象形状/8192-byte 限制；公开读取不修改原值，写入保留未知字段，不兼容历史值显式拒绝。前向迁移测试包含大字段、数组、null 和已知字段错类型。
- `sync_marks` 是现有 qa_sources/versions 与公开 MarkAnchor 的内部适配，没有另外引入一套外部来源或绕过 owner/课堂/准确版本/hash 校验。本轮接受其作为当前 main 下的内部实现；不等于冻结规划里的 transcript_segments/slides 实体已经落地。未来替换仍需保留标识、CAS、anchor 和收据关联的迁移。
- objectType 加入认证缓存 header，旧无类型 learning 记录失效重建，未删除唯一草稿或 nonce 历史。学习正文的准确版本问题仍属 R1。
- 两份既有任务回归改动保留原账本、provider calls 和回滚断言；新增断言精确验证目录事件与 owner counter 只增加一次，没有用“忽略所有事件”弱化原保障。
- 当前交付是默认关闭的认证工厂与可挂载 ConflictPanel；正式 App 仍为 AuthWorkbench，09b 后续联调与 10a/10b 页面接入按实施图保留。没有把 mock 页面算作真实产品入口。

## 本次独立证据核验

本审查者只在宿主进行只读静态分析、JSON/XML 证据读取、SHA-256 与差异检查；**没有在宿主执行功能测试，没有独立启动 VMware 测试**。下表是独立读取实现方本次 VMware 证据后的核对结果，而非声称本审查者亲自跑过全部套件。

| RunId | 独立读取的结果 |
|---|---|
| pr08b-frontend-20261117-03 | run.json 所有步骤退出 0；Vitest 日志 316/316；JUnit 40 个父节点且无 failure/error/skipped；required/observed PR-08b 均29。原生 sync 报告标明真实 Chromium/Web Crypto/IndexedDB、合成 transport；formal-browser 标明真实 HTTP/PostgreSQL、两认证设备、丢 ACK、冲突 UI、删除与设备撤销。 |
| pr08b-full-20261117-02 | run.json 通过；JUnit 实际1262父节点，套件统计29子测试、共1291；无 failure/error/skipped；29个08b函数及原认证浏览器已执行。 |
| pr08b-legacy-20261117-02 | run.json 通过；JUnit 实际525父节点，日志171子测试通过；九套Node及guards退出0；JUnit含4个dpapi命名用例，Windows来宾证据没有替换为宿主mock。 |

三个阶段 cleanup.json 的残留进程 count 均为0，frontend/full 的 pg-stop 均退出0。重新计算的归档 SHA-256 与 VALIDATION.md 一致：

| 阶段 | source.zip SHA-256 | evidence.zip SHA-256 |
|---|---|---|
| frontend03 | 1c3cffd305e5a3420cbe6b08c9e0b86a621183a0749649279232cd43d4d720bb | 89d15505fbb2d8eec8c01b075939112d4c9ff902dce2790eea40ddfedff953ab |
| full02 | 9c57fad97241af8252a1285cea3912b2b15205893d0046f51f006ea656888cb5 | 6658316d13ec7a77e57e04bb00a44c43b1e7ab150ee36ef11adb5defdd8d755e |
| legacy02 | ad9a583df42f7e795188701dc1ab5218baf16b4cc06fd5741c3a0f7622fb0b18 | da37a8702e98b898fd8186f0e1991abe88a92146301d84d7b5ffcb140381ebba |

修复开始前，对本包15个变更的生产源码/迁移文件执行 Get-FileHash，并与 frontend03、full02 的 SOURCE.json.files_sha256 逐项比较，两组均0差异。使用 XML XPath 独立数 testcase 与 failure/error/skipped；使用 git diff --check，退出0。修复开始后阅读冻结代码使用 git show 325d687，避免把在途修复混入初审。

未核验新的远端 CI、真实课堂数据、真实 Provider、生产配置、生产部署或真实设备录音。未读取真实凭据。测试通过只能说明合成环境中的已覆盖路径；不关闭 R1–R4。

## 残余边界

原04b物理清理主要覆盖媒体对象及其生命周期，不包含数据库 Review/QA 正文的全面物理擦除。08b 新增 conflict/local_operation/server_content 当前依赖墓碑和 RLS 撤权、保留不可变恢复证据；本报告不将“访问不可见”称为“数据库正文物理删除完成”。后续物理保留期限和清理方案仍需在对应授权范围处理。

所有本次修复应保持非生产、无付费调用、独立分支和唯一草稿不静默丢弃的边界。初审仅新增本报告，未提交、推送、创建 PR、修改 main 或改变运行开关。

## 对 c711458 的增量复审

本轮继续由同一独立 Astra 代理审查，基于初审提交 `325d687bb13ecb5165379a0c27cd2b8f3525df53`，检查修复提交 `c71145836f0a6f4a2deb0ffe2ea7c1126c843a17`。开始时已确认 HEAD 和干净工作树。上面的初审发现保留为历史记录。

R1–R4 的修复已经在源码和定向 VMware 证据中得到验证，但本轮发现了修复引入的 R5。**c711458 仍请求修改，不能放行。** 完整 Web 的 full03 结果尚待回收，不能提前记为通过。

| 初审项 | 增量核对结果 |
|---|---|
| R1 | runtime 复用既有准确 revision、note 和 conflict GET，授权只在当前解锁周期内复用，lock 清除授权集合。Review learning 的 contentRevisionId 进入认证 header，读取和 hydrate 都核对准确版本。受保护文档事件使在途返回失效。正式浏览器验证了文档及 A 版本仍为200、B版本及冲突为404、面板清除，以及同一 IndexedDB 重新打开后草稿 restricted 且拒读。原撤权漏洞已修复，但新的无条件锁定行为引出 R5。 |
| R2 | 课程 edited_merge 不再获取 Review 序列锁。use_server 和 copy_to_new_note 采用课程共享锁冻结 CAS，避免与直接 create_note 的共享祖先锁形成环。三种模式的真实两连接 barrier 用例均在 frontend06 的 JUnit 中执行并通过。 |
| R3 | 新冲突在 _append/_advance/_event 前读取并锁定准确云端来源，将已读取的 DTO 或明确的 None 传给补齐步骤。sentinel 区分“未读”和“已读但不可见”，不会在持 counter 后再次发现来源依赖。历史收据补齐保留准确历史版本偏移。真实 PostgreSQL 用例通过 pg_blocking_pids 确认来源锁等待，最终完成写入与撤权。 |
| R4 | 服务端同时要求请求 base、冻结 server_revision 和锁定后的当前 CAS 一致。三种模式使用旧 conflict_id 换新 base 均被拒绝，文档、正文、resolution 数量不变。新比较随后可以成功解决，异类型 merge 仍拒绝。 |

本轮还核对了启用同步必须装配已启用 ReviewBrowser 的启动检查，避免精确授权端点缺失。没有新增公开 HTTP schema，默认关闭和既有 PR-07b overlay 的边界保持不变。

### R5 · P2：普通文档事件会清空尚未确认的唯一手动合并输入

发现基于 `c711458`。主要位置是 `web/frontend/src/sync/runtime.ts:237` 和 `web/frontend/src/sync/ConflictPanel.tsx:35-47`，提交路径位于同组件 `:59-75`。

用户在 ConflictPanel 中输入新的合并正文或个人补充，但尚未点击“确认处理”。这些输入目前只保存在 React state，只有 submit 才会通过 runtime.resolve 持久化。另一设备正常更新同一 Review 文档，或者此前冲突产生的事件尚未消费，都会触发新增的无条件 cache.lock。组件的 locked handler 随即清空 body 和 note。

这不需要发生来源撤权。重新解锁后，服务器和原 outbox 只有原提交，无法恢复用户刚输入的唯一合并内容。保留原冲突草稿并不能证明保留了这次新输入。原生用例 `partial-source-revocation-clears-conflict-and-restricts-draft-after-unlock-and-page-reload` 虽然输入了 `SOURCE B UNSENT MERGE`，但只断言它从界面消失及原草稿仍在，没有验证新输入进入了加密恢复记录。

建议在用户编辑过程中提供独立的加密本地恢复，不把未确认编辑当作可自动发送的解决操作。普通更新或撤权导致明文失效时，应保留这份唯一密文。重新授权后恢复允许访问的编辑内容，撤权后继续保持 restricted，不能为了保存输入而继续展示失权明文。也可以采用经过准确授权重核的普通变化处理，但必须证明既不重新引入 R1，也不丢失尚未确认的输入。

VMware 回归应在真实浏览器中输入独有的合并正文和个人补充，再由另一设备正常更新对象。消费事件、重新解锁并重新打开同一缓存后，必须能够恢复这两项输入，且没有自动提交或覆盖云端。另测撤权时只留密文、迟到写入、存储失败和后续 generation，不能只验证原始 draft 仍存在。

### 本轮证据

已独立读取 `pr08b-frontend-20261117-06` 的 run.json、JUnit、Vitest 日志、原生与正式浏览器报告和 cleanup.json。全部步骤退出0，JUnit实际46个父节点，无 failure、error 或 skipped。33个 PR-08b 函数均有执行记录，包含 R2 三种模式、R3 锁序、R4 再次 CAS、R1 部分来源撤权及启用入口检查。Vitest为323项通过，同步原生浏览器29项通过，原08a和联合缓存阶段均退出0。正式浏览器报告记录部分来源撤权清面板和重开后拒读，cleanup残留为0。

本审查者重新计算 frontend06 的 source.zip SHA-256，得到 `04bed6a56cac8f7edde88aacbcadcea556764b4899cc0d7d94a764fd8413a8aa`。evidence.zip 为 `156a63f3227ef19fa7487801fdc77b6d50ed092014391adb5c19db4a7c32d023`，均与提供的记录一致。

267个 Web 源码、迁移和测试文件分别与 frontend06、full03 的 SOURCE.json 逐项比较，两组均0差异。此时 full03 的 source.zip 和运行证据尚未回收，因此仅核对其源码清单，未声称独立核验该阶段归档或测试结果。另核对133个旧产品源码、测试、static、启动器和 requirements 文件，与 legacy02 的清单均一致。历史525主用例、171子测试、九套Node和DPAPI证据仍只对应其实际运行快照。

本轮仍只执行宿主只读静态分析、文件散列、证据解析和 git diff --check，没有在宿主跑功能测试，也未独立启动新的 VMware 运行。R5 已交实现方修复，后续结论需新的冻结提交和回归证据。

### full03 回收后的并发可用性观测

随后回收的 `pr08b-full-20261117-03` 没有全过。run.json 记录1297项，其中1268父用例和29子测试，1项失败、0错误、0跳过。失败的是既有 `test_concurrent_and_repeated_deletes_of_one_object_are_one_request`，六个并发删除响应为 `[202,202,202,503,202,202]`。PostgreSQL日志138–141行记录 `identity_api.validate_session` 在 `auth_sessions FOR UPDATE` 等待超过锁时限。

认证仓储的500ms lock_timeout、该会话锁和删除事务路径相对main没有改动。08b增加目录事件会参与原owner counter，但当前观测不足以把此次超时认定为08b新引入的锁环。也不能因随后重跑通过，就把它记作根因已经修复。

本审查者读取了 `pr08b-lock-01` 的运行记录和五份锁采样。五轮均保留原断言和500ms时限，每轮用例通过。30次submit记录耗时62–156ms，五次ledger append耗时31–32ms，等待关系指向auth_sessions，诊断未记录错误。这些结果说明受控样本中未复现超时，不证明较慢ledger或较高负载下仍可满足时限。

此项按P2影响等级保留为已观测的既有并发可用性残余，不计入已修复的R1–R5。后续完整新快照仍须通过原门禁。即使通过，也只支持08b当前范围的审查结论，不关闭同会话长事务和并发容量的生产验收边界。现阶段没有依据要求在08b内重构认证架构或放宽原测试阈值。
