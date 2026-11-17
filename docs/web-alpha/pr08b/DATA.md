# PR-08b 同步持久化与既有模型映射

基线为 main 的 `8b1e44e`，HTTP 使用 OpenAPI 0.2.1 与既有 PR-07b overlay 的显式组合。新增迁移 `0014_offline_sync` 接续 `0013_trusted_device_guard`。这些表和函数属于默认关闭的同步适配器，启用由显式 `SyncBrowser(enabled=True)` 注入控制。

## 操作与冲突

`sync_operations` 记录 owner、操作类型、op_id、规范请求摘要、对象标识、准确结果版本与冲突标识。通用 `operation_receipts` 继续只存最小状态和资源引用。批次外层 `syncOperations` 键绑定整批请求摘要，每个内层 `syncOperation` 独立事务提交。重复传输复用原键，异体请求保持 409。

同步批次最多 50 个操作，HTTP 请求正文另有 8,000,000 bytes 的资源上限，超限返回 413 CAPACITY_EXCEEDED，调用方可保持每个原操作键并缩小批次。每个请求体指纹使用 UTF-8、按键排序、无额外空白、保留 Unicode 的规范 JSON。内层摘要覆盖完整 SyncOperation，resolve 摘要覆盖 conflict_id 与 body。

`sync_conflicts` 保存冻结的 typed 本地操作与当时云端内容，`sync_resolutions` 追加用户决策。三张表的既有记录不可更新或删除。复习个人稿仍由 PR-07b 的不可变分支与文档 CAS 管理。个人版本的同步冲突由 Review 仓库补写。补写使用原冲突的 `current_revision_id`；未选用个人稿或已采纳版本的旧冲突使用 CAS 递增前的文档历史快照确定当时的候选版本。

旧操作重交先核对原收据指纹。历史比较暂时失权时保留原收据和冲突身份，详情按现有契约报告不可用。来源重新可读后，原请求可按同一身份补写缺失的元数据。读取详情会核对原提交分支授权、冻结的云端版本与 CAS。既有补充行与冻结引用不一致时隐藏该行，保留原始记录以供核查。

客户端收到历史比较尚未补齐的 409 时，可用当前已授权的本地个人稿重交一次原请求。收到同一冲突的原指纹和 CAS 后重新读取详情。原密文及 generation 等待最终决策的准确 ACK。一个原草稿和它的一份已加密决策共用操作数槽，决策仍计入草稿字节限额。

普通可信缓存入口拒绝释放同步记录的明文、写入受保护的同步记录或按操作键清理同步草稿。已有同步密文继续使用原存储格式，交由同步入口按精确版本和冲突重新授权。用户更正已明确拒绝的决策时，先保留仍需恢复的本地输入，再按原决策标识原子替换待提交记录。个人合并另有来源版本时，独立恢复同时核验该精确版本。旧格式的待确认决策也按原请求和原键核对。

冲突读取完整保留既有 ReviewRevision，包括生成候选的原 source_ids、structured_content 和 generation_task_id。main 的 PR-07b 来源别名为 S1 至 S512，使用 `tools/pr07b/contract_candidate.py::build(base)` 组合对应来源定义与生成任务导航。Sync 与 Review 的应用装配均要求既有 x-pr07b-candidate 标识，缺少 overlay 时在启动前明确拒绝。启用 Sync 时须同时注入已启用的 ReviewBrowser，供客户端通过既有精确文档与版本读取接口核验来源授权。

`copy_to_new_note` 为个人稿保留原 Markdown 与 personal_note。标记、课程、偏好冲突的副本正文采用 typed payload 的规范 JSON，保留所提交字段及值。副本返回新 note 的标识和文档 CAS。三种决策同时核对请求 base_revision、冲突冻结 server_revision 与当前 CAS。旧冲突遇到云端后续编辑时需要重新比较，原分支持续保留。

## 标记适配

冻结 domain-model 规划的 `marks` 包含 19 列，并引用 `transcript_segments`、`slides` 及其版本表。当前 main 的来源实现提供 `qa_sources` 与 `qa_source_versions`。PR-08b 使用内部 `sync_marks` 对接现有来源，以 HTTP 的 `Mark` 与 `MarkAnchor` 为公开接口。

| 公开含义 | 内部存储 | 既有规划对应 |
|---|---|---|
| 标识、owner、课堂、CAS、时间 | 独立强类型列 | id、owner_id、class_session_id、revision、created_at、updated_at |
| important / unclear | kind | kind |
| 用户标记文字 | note | note |
| 已理解 | understood boolean | understanding 的 unresolved / understood |
| 来源标识、准确版本与摘要 | anchor.source_ref | segment/slide 标识、对应版本与 content_sha256 |
| 复习文档及准确内容版本 | anchor.document_id、revision_id、content_sha256 | review_document_id、review_revision_id、content_sha256 |
| 采集时点 | anchor.capture_epoch_id、sample_offset | capture_epoch_id、sample_offset |
| 删除撤权 | 课堂祖先、来源版本与复习版本的实时授权 | deleted_at 与删除血缘 |

创建时检查 owner、课堂、准确版本和 hash。时间点绑定已有采集 epoch。所有读取和 bootstrap 投影受 FORCE RLS 保护，来源或课堂撤权后标记退出可见投影。更新仅允许 note、understood、版本和更新时间，数据库守卫要求版本恰好递增一次。

后续正式 learning 标记表落地时，前向迁移应保留标识、CAS、原 anchor、时间与收据关联，逐类展开 anchor 并建立准确版本外键，再切换同一公开端口。旧版本来源缺失时保留恢复证据并显式报告，迁移不得选择最新版本替代原版本。

## 偏好与授权

`sync_preferences` 持有独立 CAS。迁移精确复制现有 users.preferences，新用户在插入时初始化。旧列接受任意 JSONB，迁移保留原值与全部未知字段，不另施加对象形状或 8192-byte 限制。公开 DTO 仅输出冻结字段，对缺失字段应用读取默认值。非对象历史值或已有已知字段不满足冻结类型时返回 409 INCOMPATIBLE_CLIENT，原数据与版本保留。bootstrap 仅读取偏好版本元数据，读取没有写入副作用。身份行继续由现有身份模块管理。

全部新表 FORCE RLS 并绑定当前 owner 与活动账户。含冲突正文的行另受课堂、文档、准确来源版本和课程祖先授权约束。`classroom_web_sync_owned_object` 仅对四种固定类型返回当前活动 owner 的对象存在性，用于隐藏正文后识别本人已撤权对象。函数不接收 owner 参数，PUBLIC 执行权限已撤销。

## 事件和锁顺序

目录、标记与偏好的新增变化事件使用延迟约束触发器，在业务变更完成后的提交阶段递增既有 owner event counter。Review 与任务事件继续使用已有事务内写入。bootstrap 在同一 REPEATABLE READ 快照读取完整授权元数据和 committed counter，超出 10000 个投影显式拒绝。

新增触发器避免目录写先占 event counter 后等待 Review 序列锁的增量锁环。任意组合多个现有任务、Review 与目录写入的调用仍须遵守其既有锁序，PR-08b 的逐操作事务限定实际写入组合。双连接用例核验本包新增组合。

个人版本冲突在递增 event counter 前读取并锁定将冻结的准确云端来源，随后补齐同步元数据直接使用该已读内容。课程冲突的采用云端与复制决策用共享课程锁冻结 CAS，手动合并取得课程写锁时不获取 Review 序列锁。只有个人版本决策及复制到新笔记需要 Review 序列锁。

删除事件在 HTTP Changes 中映射到 `deletion` 类型、真实删除请求标识和删除进度版本。客户端可读取删除请求范围并重新核对完整授权投影。来源撤权后的完整投影移除对应对象，未同步草稿保留受限恢复状态。

变更页宣布删除时，客户端先锁定当前视图并通知同账号同设备的缓存上下文。IndexedDB 在详情读取前记住待处理删除标识，阻止同账号同设备重新解锁。应用对应墓碑的事务清除等待，启动新的同步运行时会继续核实已有待处理标识。只有整页处理成功才保存游标。锁前已授权并完成加密的编辑输入继续按现有墓碑保留规则只写保存。

缓存打开版本升到 IndexedDB v4，沿用 v3 的现有记录与索引。仍按旧版本打开的标签页需要刷新。读取本地删除屏障失败时锁定视图，只有经过结构校验的服务端删除请求 ID 才写入等待记录。

qa_sources 的新增、修订与撤权在同一事务提交阶段给本课堂 Review 文档和标记追加既有 object.changed 事件，事件只含标识和真实对象版本。来源状态变化可以发生在对象 CAS 未改变时，客户端收到该事件仍须核对授权和精确内容。已被 RLS 隐藏的文档和标记在 Changes 中标记 deleted，其他对象继续按同一标识重新读取。

文档原始来源仍有效时，后续某个来源撤权可以只隐藏其派生版本和冲突。此时文档投影保留，Changes 仍发同版本的变化事件。精确 ReviewRevision 读取核对该版本的来源快照，冲突读取同时核对冻结的云端版本与本地提交分支，客户端据此控制正文和草稿访问。

## 验证边界

上述内部表示与迁移映射提交独立 Astra 复审。实际 VMware 验证结果、失败修复和覆盖范围由本包验证记录统一登记。

独立审查在宿主进行静态源码和证据核对。产品代码、浏览器和合成夹具的执行统一交由 VMware 来宾。
