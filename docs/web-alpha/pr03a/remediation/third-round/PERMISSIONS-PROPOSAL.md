# PR-03a 第三轮：受限 LocalReconciler 权限方案（待 Astra 复审）

日期：2026-11-02（Asia/Taipei）。状态：**仅方案与修前取证，未实施，REQUEST CHANGES；不构成整合许可。**

## 1. 为什么先提交方案

用户本轮 R2 明确要求：“若现有 RLS 无法支持所需受限执行，先提供明确权限方案交 Astra 复审，再实施该部分”。原先对受限本地收尾入口的批准继续有效；本文件不重新请求相同功能批准，而是提交该条件要求的具体数据库权限方案。

本次操作前核验正式 Git 根 `J:/class-assistant/worktrees/web-pr03a-identity`、分支 `feature/web-pr03a-identity`、HEAD `cb5f73c288fe013763e1e3ee9ebc3eb94a44a6b0`、父提交 `088984fbd7c9f36f4250c9af340d6a73a0c57960`，工作区/暂存区干净，无冲突。没有第三轮既有修复提交可复用。

真实 PostgreSQL catalog 显示：`provider_results` 只有 `owner_isolation TO classroom_web_runtime`，`USING` 和 `WITH CHECK` 均要求事务 owner 匹配且 `classroom_web_owner_active()` 为真。没有 dispatcher 或其他角色的失效身份处理策略。`task_attempts` 的 dispatcher 例外也不能授权 raw 表。FORCE RLS 仍开启。

因此，新 NOLOGIN 角色即使取得列权限，仍没有读取失效身份 raw 的适用 RLS 策略；继承 runtime 也仍受身份失效谓词拒绝。只给四个函数 `ALTER OWNER` 无法满足原 C 类恢复要求。继续使用 superuser/BYPASSRLS、继承 dispatcher 或增加 Scheduler 权限均不采用。需要 Astra 先审查下列**新内部角色专用政策**。

修前证据目录：[20261102T114612.393722Z](../../../../../outputs/pr03a/third-round/20261102T114612.393722Z/)。其中 `catalog-before.json` 保存策略、FORCE RLS、函数 owner/ACL、角色及 membership；`results.json` 保存命令、集群身份和源码哈希。本文件不声称新政策或修复已通过验证。

## 2. 三个隔离函数所有者

三者均拟设为 `NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION`。只授 `USAGE ON SCHEMA public`，最终不保留 schema CREATE，不持有表、序列或数据库，不获得其他角色成员权。

| 新内部角色 | 拥有的 SECURITY DEFINER 函数 | 职责 |
|---|---|---|
| `classroom_web_identity_checker` | `classroom_web_identity_usable(uuid)` | 唯一读取身份判定列的 helper，只返回 boolean |
| `classroom_web_recovery_metadata` | `classroom_web_attempt_submitted(uuid,uuid)`、`classroom_web_local_reconcile_candidates(integer)` | 只读取跨 owner 恢复元数据，不得读 raw、payload 或 task input |
| `classroom_web_local_result_processor` | `classroom_web_reconcile_saved_result(uuid,bigint)` | 在可信事务 owner 行域内校验既有 raw、来源与 fence，原子收尾；只返回 boolean |

runtime、Scheduler、identity、dispatcher 及其登录角色不得直接或间接成为这三个角色的成员，包括 `SET ROLE` 可达关系。NOINHERIT 不能替代成员图校验。三个内部角色彼此也不通过成员关系继承能力，跨角色调用只用指定 helper 的 EXECUTE。

保留现有 `classroom_web_recover_revoked` 的 dispatcher owner，不改写既有 dispatcher 策略/列权限。历史 owner_active/lock_owner 包装器的安全规则保持；若 owner_active 的现有所有者在非超级用户迁移场景需要执行 identity helper，只向该既有包装器 owner 授精确 helper EXECUTE，不能转授 runtime 直接调用身份 helper。

## 3. 最小列 ACL 与 RLS

以下为拟实施白名单，未列出的权限不授予；最终实现必须以实际 SQL 逐项核对，而非授整表权限凑齐运行条件。

### 3.1 身份 helper 所有者

`users` 只授 SELECT：`id, state, deleted_at, normalized_email, email_verified_at, password_hash`。不授 profile、角色管理或用户写入。当前 users 没有本次要绕过的 FORCE RLS 策略；身份 helper 按指定 UUID 计算原安全谓词，不输出列值。

### 3.2 恢复元数据所有者

| 表 | SELECT 列 |
|---|---|
| tasks | id, owner_id, kind, lease_token, state, not_before, lease_expires_at, updated_at, error_code |
| task_attempts | id, owner_id, task_id, attempt_number, raw_output_ref, submitted_at, provider_request_id, provider_outcome, state |
| provider_results | id, owner_id, attempt_id, validation_status, conflict_sha256 |

只为该角色添加上述三表的 SELECT 元数据政策，允许跨 owner 扫描；列 ACL 排除 `tasks.input_ref`、`provider_results.raw_output/validated_payload`。不授 INSERT/UPDATE/DELETE、来源正文或身份字段。仅可执行 identity boolean helper。队列仍按稳定 `(updated_at,id)` 排序、每批 1–100 条。

### 3.3 本地处理函数所有者

行域表达式记为 `O = nullif(current_setting('app.owner_id',true),'')::uuid`。所有以下新政策只 `TO classroom_web_local_result_processor`，SELECT/UPDATE 的 USING 与写入 CHECK 均限定 `owner_id = O`。不对 PUBLIC/runtime/Scheduler/identity/dispatcher 添加政策，不改现有 owner_active 条件或 FORCE RLS。

| 表 | SELECT 列 | 写权限 |
|---|---|---|
| tasks | id, owner_id, kind, state, lease_token, lease_expires_at, revision, class_session_id, not_before, error_code, may_have_charged | UPDATE state,error_code,may_have_charged,lease_token,leased_by,lease_expires_at,revision,updated_at,completed_at |
| task_attempts | id, owner_id, task_id, attempt_number, raw_output_ref, provider_outcome, state, finished_at | UPDATE state,raw_output_ref,provider_outcome,error_code,finished_at |
| provider_results | id, owner_id, attempt_id, raw_output, validation_status, conflict_sha256 | UPDATE validation_status,validated_payload；不改 raw、digest、conflict 或接收记录 |
| semesters | id, owner_id, deleted_at, revision | 下述行锁所需权限 |
| courses | id, owner_id, semester_id, deleted_at, revision | 下述行锁所需权限 |
| class_sessions | id, owner_id, course_id, course_material_version_id, deleted_at, revision | 下述行锁所需权限 |
| course_material_versions | id, owner_id, course_id | 无 |
| deletion_tombstones | owner_id, resource_type, resource_id | 无 |
| owner_event_counters | owner_id,next_sequence | INSERT id,owner_id,next_sequence；UPDATE next_sequence |
| outbox_events | 无 | INSERT id,owner_id,event_sequence,event_type,aggregate_type,aggregate_id,aggregate_revision,payload,schema_version |

PostgreSQL 来源 `FOR SHARE` 行锁还需目标表的 UPDATE 权限。拟仅向 processor 授 semesters/courses/class_sessions 的 `UPDATE(id)` 用于行锁；固定函数不执行来源 UPDATE，既有不可变身份 guard 保留。该需求必须公开列入审查，不能假称“只授 SELECT 即可锁行”。实施时拟用同 owner 的 UPDATE USING 与 `WITH CHECK(false)` 拒绝实际来源改写；必须在 PG17 实测 FOR SHARE 与该组合相容，再验实际 UPDATE 拒绝。若不相容，不自行改成可写来源政策，返回 Astra 审查具体差异。

processor 只执行 identity boolean helper 和现有纯 JSON invoker helpers。JSON helpers 不改为 SECURITY DEFINER；其返回正文的函数继续对 runtime/Scheduler/dispatcher/identity/PUBLIC 撤权。

`app.owner_id` 只是该内部角色下的行域，并非授予系统权限的开关。不能增加 `app.reconcile=true` 等能让普通运行角色启用的绕过。固定入口还必须核验任务 owner/fence/state/无有效租约、身份确实失效、Attempt/raw 血缘、冲突状态和全部来源权限。普通运行角色伪造任何 GUC 后，仍不能继承上述角色、直接读失效身份 raw 或调用私有正文 helper。

### 3.4 函数调用 ACL

保留原两个公开于内部 runtime 的固定入口：candidates（最小元数据）、finish（boolean）。Scheduler 对这两个入口仍无 EXECUTE，也不新增任何表列权限。identity/attempt boolean helper 保留 dispatcher 必要调用权；identity helper 另授 metadata/processor 及确有需要的既有 owner_active 包装器 owner。PUBLIC EXECUTE 保持撤销。不得向应用运行角色新增正文表权限。

固定 `search_path=public,pg_temp`、显式 schema、不含动态 SQL；运行角色继续没有 public CREATE。owner 转移前后需逐函数检查实际 OID/owner/flags/ACL，不能依赖名称或调用者低权限推断。

## 4. 与 R1、R3 的配套修改计划（同样尚未实施）

### R1：存储尺寸与安全错误边界

保留原 raw 长度、重复键、根对象、非有限数与 Python 序列化大小规则。在最终 JSONB 值写入前，用 `pg_column_size(payload) <= 1048576` 对照现有 CHECK 的实际口径；超限将 payload 置 SQL NULL，按 `failed/invalid/OUTPUT_INVALID` 原子收尾，保留 immutable raw、digest、Provider outcome、Receipt、unknown 费用风险和最小事件。

在固定 SQL 入口内部建立异常回滚边界：不向客户端重新抛出原 MESSAGE/DETAIL/HINT/CONTEXT，不读取或转发诊断中的正文；保留必要 SQLSTATE，以固定无正文消息上报。不得把整个复合行写进 RAISE、NOTICE 或日志。调用链只传 task/fence，不传 raw；补充异常链、客户端诊断及 PostgreSQL 原始日志的合成标记扫描。hide_parameters 不作为修复证据。基础设施/注入失败仍须整体回滚，不伪造成功。

超限候选转入确定的既有失败终态后退出候选队列；下个账号继续推进。不可吞异常后让同一 pending 候选无限重试。安全错误实现须保留原回滚测试的 SQLSTATE 断言，不能删除或降低断言。

### R3：来源授权与撤权锁序

从 task.class_session_id、课堂持久 material 指针发现当前课程及固定材料原课程，再发现两边学期。锁前只读元数据；完整集合稳定排序后依次锁 `semester SHARE → course SHARE → classroom SHARE → task UPDATE → attempt → result → owner counter`。锁后重新检查关系、课堂/祖先 deleted_at 和 account/classroom/material/原当前课程与学期墓碑；移动导致图变化时本轮不写状态，下轮重新发现。

不能先持 task 锁再取祖先锁。需以 pg_blocking_pids 等现场数据验证撤权先行、收尾先行两种顺序，与普通 publish 及材料撤权锁序相容。

撤权结果保持 raw/pending、unknown、费用风险与 Receipt，不错误标 valid/succeeded。拟用现有 `ACCESS_REVOKED` 标记和一次最小 Outbox 明确隔离，候选排除已隔离任务，重复处理不重复事件。原探针要求 `reconcile=False` 且结果 pending，这两条断言保持；false 表示未完成本地结果校验，不表示重新授权或允许 Provider 重发。该隔离状态与 Outbox/Attempt 的精确写集合仍须列入实现差异和测试审查。

## 5. 迁移交付与失败处置

拟新增 `0006_local_reconciler_security`（最终以实际 Alembic head 为准）。保留 0001–0005 十个历史迁移文件，不覆盖 0005。无新业务表、不改冻结 API/WS、Receipt/state enum/费用政策。

角色建立、函数定义与 owner 转移、ACL、角色专用政策均在同一迁移事务。已有同名角色如存在危险 flags、未知成员、其他角色继承、表 ownership 或超出白名单的有效权限，必须拒绝升级并完整回滚，不能静默重置外部角色或回退到 superuser owner。

覆盖两类迁移执行身份：隔离集群 bootstrap superuser；不同名字的可信非 superuser 部署身份。后者只在隔离验证中由明确 provisioning 提供必要 DDL/角色管理能力，不给运行角色。owner 转移所需临时成员关系/schema CREATE 必须在事务结束前撤销并实测；最终所有者不保留 CREATE。若部署身份能力不足则失败关闭，不能靠账号名特判。具体授予/撤销语句及 PostgreSQL17 owner-transfer 限制需随实现提交审查。

测试空库→新 head、0003/0004/尤其已部署 0005→新 head、重复 upgrade、19 张非空旧表及账本保留；故障注入比较函数定义/owner/ACL/政策/membership/数据/版本全部回滚。唯一 head 必须现场验证。

若需要停用，暂停本地收尾并保留 raw、Attempt、Receipt、Outbox、unknown 风险和迁移账本；采用兼容代码或前滚修复。不破坏性 downgrade、不清库、不删除审计、不重发未知请求。

## 6. 实施后的必需验证（当前未运行）

1. 保留原 423 项及原断言，追加本轮七探针为持续回归；完整后端零失败/error/skip。修前失败原件与修后同组证据分开。
2. 逐函数 owner/role flags/ACL/PUBLIC EXECUTE/search_path；全部运行主体对内部角色的 MEMBER、USAGE、SET ROLE 传递图；Scheduler 全列权限快照不变。
3. metadata 无 raw/payload 权限；processor 行域、伪造 GUC、跨 owner/Admin、错误 fence、live lease、无 raw/冲突 raw 均拒绝；普通 Owner UoW 失效身份仍拒绝。
4. R1 大 JSONB 原子 OUTPUT_INVALID；客户端 DETAIL/CONTEXT/异常链与服务器原始日志无标记；故障回滚；后续账号推进；重复执行不多发/多事件。
5. R3 材料、原/当前课程与学期、课堂及墓碑矩阵；两向真实锁等待、来源移动复验、双 Reconciler/Worker 并发与隔离队列推进。
6. 不改 raw/digest/Provider outcome/Receipt、不新增 Attempt；Provider 调用保持未发送 0 / 已发送及已存 raw 1。没有真实 reservation/usage 账本，不声称已结算或释放预留。
7. 新迁移全路径与事务故障回滚；类型/静态/依赖/冻结契约/Windows 产品保护。本次暂停阶段没有重跑完整 423、Windows 525+171 或九套 Node，历史结果不充当本轮证据。后续联合整合门禁另需授权，不操作集成分支或 PR-09a。

## 7. 本次实际取证与审查决定点

2026-11-02 在本工作树隔离 PG17.11 / Python3.12.14 上，七探针 **2 passed / 5 failed / 0 error / 0 skipped，退出码 1**。探针 SHA256 `e5bd7b5849e9ac162e96e3cef994ed4e690b0b5ccb8db4aa193305b816e38c9e` 与第二轮副本一致，仅测试 harness 改写证据输出路径；攻击条件及断言未改。

- R1：raw 199850 字符、Python 序列化 299752 bytes、JSONB 1198865 bytes；两次 23514 含合成标记，后续任务仍 unknown；两个 Provider 各一次。日志含标记是**修前泄露证据**，不可称作无正文验证通过。
- R2：四函数实际 owner 仍为迁移 superuser，super/bypass 均为真；catalog 证明当前 raw 表没有所需非特权例外。
- R3：三种原来源撤权均错误完成 valid/succeeded；未观察到跨用户移动或私人页面恢复，不推断这些后果。
- 源码前后 manifest 相同；旧审查副本/失败日志未覆盖。数据库启动与停止退出均 0，55439 已关闭，postmaster.pid 已消失。

**请求 Astra 审查的具体决定：是否接受第 2–3 节三个隔离 owner、逐列 ACL、角色专用 RLS、来源锁行最小权限，以及第 5 节迁移/撤销要求。** 该决定只允许随后在原 PR-03a 工作树实施和验证，不批准合并或发布；实现完成后还需对新精确修复 SHA 独立复审。

目前 R1/R2/R3 均保持 OPEN，原首轮身份失效 P2 维持已关闭历史结论但仍需后续全回归防回退。未创建修复 commit、未执行新迁移或生产源码修改。两项 PR-02b P3、PR-09a 开放 P3 和 11 项 TBD 继续 OPEN；真实邮件/Cookie/MFA/多设备/Provider费用/生产恢复/远端 CI 均未验证，隐私预检 NO-GO 不变。
