# PR-08b 接入与恢复

负责人林珈诚。本包使用 HTTP 契约 `0.2.1` 加现有 PR-07b overlay（`pr07b-1`），产品版本为 Web Alpha 0.1.0。

## 服务端入口

`SyncBrowser(auth=auth, database=database, enabled=True)` 通过 `create_app(..., review=review, sync=sync)` 接入既有认证、Cookie、CSRF、Origin、严格 JSON 与响应校验。启用同步时需显式提供已启用的 ReviewBrowser，供准确文档和版本授权读取使用。构造参数默认关闭。生产启用继续使用原发布审批流程。

与 ReviewBrowser 一样，应用装配需传入现有 `tools/pr07b/contract_candidate.py` 生成的完整契约。同步冲突携带实际候选的 `S1` 等来源别名、结构化内容和 `generation_task_id`。入口检查 `x-pr07b-candidate=pr07b-1`，确保实际候选与响应校验一致。

公开端点包括 `/sync/bootstrap`、`/sync/changes`、`/sync/operations`、`/operations/{op_id}`、`/sync/conflicts/{conflict_id}` 及其 `/resolve`。标记和偏好使用同一持久适配器提供正常在线入口，四类同步命令共用 CAS 与幂等记录。

bootstrap 的完整目录与 owner 水位在同一 REPEATABLE READ 快照内产生，包含当前可访问的学期、课程、课堂、复习文档、标记与偏好。上限为 10,000 项，超限返回 `CAPACITY_EXCEEDED`。客户端时间不参与排序。changes 使用既有带 owner 认证的游标，缺页或过期要求重新 bootstrap。每个删除变化指向既有 `Deletion` 详情，客户端以其范围失效本地内容。

同步批次的外层键固定整个有序请求体，每项操作有独立稳定键与事务。指纹使用规范 JSON 字段排序、UTF-8、SHA-256，包含操作类型、对象 ID、draft_id、base_revision 与完整 payload。外层与 payload 的 base_revision 必须一致，个人稿的 draft_id 同样必须一致。`syncOperation` 为内层收据作用域，`resolveConflict` 为显式解决作用域。

结果未知时先查询原收据，再使用原键和原体重试。只有 `applied` 能确认本次提交。冲突、删除、拒绝和待处理均保留本地操作。读取旧收据会复核当前授权，删除后返回最小状态。

## 前端入口

```typescript
const sync = auth.createOfflineSync({
  enabled: true,
  databaseName: 'classroom-offline-sync',
  limits: approvedCacheLimits,
});
await sync.unlock(password, mfaCode);
const reference = await sync.enqueue(operation, ancestry, editorGeneration);
await sync.flush();
```

示例中的 `approvedCacheLimits` 来自明确的缓存策略，调用方应保留正在编辑的内容，直到 `enqueue` 成功。开启工厂功能仍需设备信任、在线认证、再认证证明和服务端允许的缓存策略。

`enqueue` 冻结操作并加密保存。`snapshot` 返回队列状态与准确引用，`readDraft` 受当前授权与缓存期限约束。恢复时保留原操作键、原正文和 generation。收到旧 ACK 只清理该引用，后续输入继续留在 outbox。

解锁会先检查完整授权目录和增量删除，再取得内存密钥。无法由当前投影证明有效的学习缓存失效。草稿密文独立保留，失去原对象授权的条目标记为受限。网络恢复重新验证授权，关闭页面或身份失效时释放内存密钥。运行时定期消费事件，收到删除或明确撤权后发出 `locked` 事件，消费者需清除其持有的展示明文。

学习缓存通过 `unlockLearning` 独立解锁，`snapshot.unlockedScopes` 明确列出当前作用域。`cacheLearning` 接收精确对象类型、ID、revision、generation、祖先和授权读取回调，核对实际返回的对象与版本后加密保存。`readLearning` 在解密完成后再次核对授权。普通更新和同版本的来源变化也会使对应学习副本失效。

个人稿的 `base_content_revision_id` 通过既有 ReviewRevision GET 核对文档归属和当前授权。无 parent 的个人稿需通过文档 GET 确认为独立 note。冲突决策同时核对原冲突可见性。成功授权只在当前有期限的解锁周期内复用，相关文档变化会清除展示与在途正文，重新解锁逐项复核。文档仍可见而某个来源版本撤权时，该版本草稿保持受限密文。

编辑器联网打开个人稿时，通过 `readReviewRevision(documentId, baseContentRevisionId)` 读取并确认准确版本，再进入离线编辑。当前解锁周期内已经成功确认的版本可离线复用。首次精确授权需要网络，未完成授权的保存错误应由编辑器保留当前输入并提示。

Review 学习副本还需提供 `contentRevisionId`，并由准确内容版本的授权 GET 确认。`readReviewRevision(documentId, revisionId)` 提供该在线读取入口。`cacheLearning` 的引用与 hydrate 结果需具有相同的 `contentRevisionId`，该字段进入加密 header/AAD。旧缺失准确内容版本的 Review 学习副本在重新核验时失效。

08b 学习记录将 `objectType` 加入原 schema2 的认证 header/AAD。旧08a无类型学习副本在完整投影核对时失效，后续经在线精确版本读取重新缓存。草稿、录音和 nonce 历史继续保留。同 ID、版本和 generation 的异类型记录明确冲突，原内容持续保留。

`ConflictPanel` 为课堂与 Review 编辑器提供冲突交互组件，接受 runtime、准确 reference、下一 editor generation 和可选目标课堂列表。组件展示本机提交与冻结云端版本，支持手动合并、明确采用云端及另存新笔记。云端再次变化时，用户可显式重新核对。旧分支和新输入在服务器确认解决前持续保留。

受保护 Review 文档的普通变化也会触发锁定，包含本设备尚未消费的提交事件。界面清除明文，用户重新解锁后继续比较。这是当前精确授权核验采用的保守处理。

尚未确认的合并正文与个人补充使用独立加密恢复记录，每个编辑会话保留自己的最新快照。恢复记录具有原冲突、原操作和准确版本绑定，后台同步只发送已经显式提交的操作。已在授权期间开始的加密可以在锁定后完成密文保存，显式清空缓存的独立持久代次阻止迟到写入恢复已清除的记录。存储失败需要界面显示保存状态并重试，读取恢复内容继续要求准确授权。多个编辑会话的恢复分支由用户选择。

消费者先完成 `unlock` 和 `flush`，再用恢复队列中的准确引用挂载冲突组件。`awaitEditorDraftSaves()` 等待已开始的保存，失败时由 `retryEditorDraftSaves()` 重试保留的密文。明确采用云端或另存新笔记后，尚未提交的手动合并输入继续保留，组件提供查看与复制区域。只有实际包含在手动合并请求内的准确恢复 generation 可随成功收据清理。

解锁后的页面可同时挂载 `ConflictRecoveryList`。它通过 `listConflictDraftSources()` 从加密存储重新发现恢复记录，原操作已确认或页面重开后仍能找到有权读取的输入。单项失权显示受限状态，其他有权分支继续可用。

```tsx
<ConflictPanel
  runtime={sync}
  reference={conflictingDraft.reference}
  generation={nextEditorGeneration}
  destinations={authorizedClassrooms}
/>
<ConflictRecoveryList runtime={sync} />
```

本包提供认证工厂和可挂载的冲突交互组件。现有产品入口为 AuthWorkbench，课程与 Review 演示页面使用 mock。09b 课程及偏好消费者的后续联调、10a 课堂页面和 10b Review 页面接入沿用实施图与前端交接的安排。PR-08b 的正式浏览器验证直接挂载同一组件，联通真实会话、HTTP、PostgreSQL 与加密缓存。

## 数据与兼容

前向迁移 `0014_offline_sync` 接在 `0013_trusted_device_guard` 后。同步记录、冲突、解决记录和偏好均使用 owner 隔离与 RLS。内部标记适配表和冻结数据规划的映射见 [数据说明](DATA.md)。

PR-07b 的既有个人版本、采用指针与历史读取继续使用原接口。历史冲突缺少原始提交参数时，专用同步接口返回 `INCOMPATIBLE_CLIENT`。用户持有原始操作并通过既有收据指纹校验后，可补齐精确冲突数据。无法核对时继续保留版本历史与本地密文。

个人正文和冻结冲突内容属于私有数据。通用收据只保存必要引用，身份秘密、密码、解密密钥及正文均不进入收据或正常日志。

## 验证和回退

阶段运行入口为 `tools/pr08b/submit.ps1 -Mode targeted|frontend|full|legacy`，每次使用新 RunId。回收入口为 `tools/pr08b/collect.ps1 -RunId <id>`。提交器记录源码清单和归档 SHA-256，回收器核对证据归档。实际结果与风险闭环记录在本包交接文件。

暂停自动同步时保留数据库收据、版本分支和加密 outbox。迁移采用前向修复，保留已保存用户数据。现有可信设备数据库约束、生产缓存策略及付费调用开关仍由各自原门禁管理。
