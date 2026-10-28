# PR-01 · 独立交叉复审记录

日期：2026-10-28（Asia/Shanghai）。仓库：`J:\class-assistant\classroom-os`。分支：`feature/web-alpha-0.1.0`。复审基点 HEAD：`f7731002d974ea29296090c366b68569fc69ba89`；以下记录描述首次设计复审当时的待审核工作区，不冒称当时已经提交、合并或获用户批准。随后用户原则批准架构并授权本地提交，当前批准范围和收尾复核另见[收尾记录](../PR01-CLOSEOUT.md)，不改变下述历史实测事实。

**结论：在本记录所列静态架构复审范围内，修正复核后未发现剩余阻断架构问题。** 这一结论支持提交 PR-01 供用户审核，不授权开始 PR-02/09，不代表运行时安全、持久性、费用或真实浏览器行为已经验收。

## 1. 复审身份与范围

复审由前端子代理执行。该代理编写了 `02-FRONTEND-AURORA.md`、`09-CLAUDE-FRONTEND-HANDOFF.md` 与根 `CLAUDE.md`，因此对这三份文件的检查属于作者自检；对其他代理编写的系统、数据、契约、安全、成本、ADR、实施依赖和 AGENTS 追加内容进行了独立交叉复审。它不是外部安全审计或完全独立的第三方认证。

| 实际复核路径 | 复核内容 |
|---|---|
| [01-SYSTEM.md](../01-SYSTEM.md) | 模块与依赖、旧业务隔离、目录职责、未来客户端 ports |
| [03-DATA-BACKEND.md](../03-DATA-BACKEND.md) | 数据关系与同血缘约束、RLS 引导、任务领取、计划消费、事件水位、DTO 映射 |
| [04-CONTRACTS-FLOWS.md](../04-CONTRACTS-FLOWS.md) | 登录设备上下文、幂等/秘密响应、录音与模型流程、冲突、bootstrap 与版本获取 |
| [05-SECURITY-OFFLINE.md](../05-SECURITY-OFFLINE.md) | 三池与解锁、删除接受、独立删除日志、备份恢复、防止旧内容复活 |
| [06-COST-OPERATIONS.md](../06-COST-OPERATIONS.md) | 双账本、预算预留、unknown、跨月、对象成本、部署与恢复边界 |
| [07-ADR-REGISTER.md](../07-ADR-REGISTER.md) | 技术选择、冻结/实验边界、PCM 格式与其他文档一致性 |
| [08-IMPLEMENTATION.md](../08-IMPLEMENTATION.md)、[pr-dependencies.json](../contracts/pr-dependencies.json) | 28 个 PR、硬/联调/收费依赖、并行条件、角色、验收与停止点 |
| [openapi.json](../contracts/openapi.json)、[events.schema.json](../contracts/events.schema.json)、[domain-model.json](../contracts/domain-model.json) | 针对下表问题检查相关路径、schema、表与不变量；未将抽样契约阅读表述为逐个 API 运行测试 |
| [02-FRONTEND-AURORA.md](../02-FRONTEND-AURORA.md)、[09-CLAUDE-FRONTEND-HANDOFF.md](../09-CLAUDE-FRONTEND-HANDOFF.md)、[CLAUDE.md](../../../../CLAUDE.md) | 作者自检：账号启动、bootstrap 消费、Mock 独立运行、角色/授权边界、围栏、空白和文件引用 |
| [AGENTS.md](../../../../AGENTS.md) | 新增长期协作规则是否保留原规则、是否越过当前授权或改变产品线隔离 |

前端设计阶段还读取了完整 PRD v1.2 的 37 页提取文本、需求矩阵、冲突/决策、既有技术审计和相关旧源码，并查看物理 p21 概念图。旧源码只用于辨认可复用算法与不能继承的行为，不在本次复审中修改或运行真实课堂业务。

## 2. 发现、修正与关闭证据

下表记录实际提出并核对修正的问题。严重度是发现时的风险判断，状态“已关闭”仅指设计缺口已经补齐；后续实现仍须通过相应真实门禁。

| 编号 / 原严重度 | 原发现及影响 | 已复核的修正证据 | 状态 |
|---|---|---|---|
| REV-01 / P1 | 删除日志不可用时若仅在主库排队并确认接受，随后的主库丢失会使恢复端无法知道日志不完整，旧备份可能恢复已删除内容 | 05 §7：外部 intent 与可验证 head 持久后才能确认接受；不可用返回 503、仅临时禁访问；独立单调 generation/hash 链与条件写，恢复读取当前 head、缺项保持封闭。03 删除事务表同步该边界 | 已关闭 |
| REV-02 / P1 | 通用幂等回执若保存完整响应，会落盘解锁 DEK、MFA seed、恢复码、Cookie 或票据，并可能重放失效权限 | 04 §1 与 05 §1：秘密响应例外、非秘密状态/引用、重新授权重签或显式轮换、no-store；OpenAPI `x-replay-policy`；domain-model `operation_receipts` 明确禁止秘密响应体 | 已关闭 |
| REV-03 / P1 | 生成计划若只作为瞬时响应，或 execute 只按 op_id 去重，客户端更换幂等键可让同一次确认重复创建收费任务 | 04 §1：`(owner_id, plan_id)` 单次消费；domain-model `generation_plans` 有不可变准备字段、期限、`consumed_task_id`，计划行锁与任务/预留/回执/outbox 同事务；03 execute 事务一致 | 已关闭 |
| REV-04 / P2 | 冲突对象包含个人稿、标记、偏好、课程，但仅有 merged_text 无法表达非文本合并，冲突分支也缺少持久归属 | 04 字段约束与 OpenAPI `ResolveConflict.merged_operation` 使用 typed `SyncOperation`；domain-model `sync_conflicts` 保存双方分支/版本和解决操作；删除优先于合并 | 已关闭 |
| REV-05 / P1 | “一致快照 + 水位”没有具体获取契约，若先读取列表再取最新 cursor，会跳过两次请求间的变更 | OpenAPI `GET /sync/bootstrap`；04 §3.3、03 §6：同一 REPEATABLE READ 快照读取有限完整投影与 owner event clock，精确 revision 获取正文后从 watermark 重放；超限明确失败。02 已同步消费规则 | 已关闭 |
| REV-06 / P2 | 采集与缓存解锁要求 device_id，但登录/当前会话没有明确未信任设备建立途径，可能迫使录音者先同意可信学习缓存 | 04 §1、OpenAPI `LoginResult` 与 `/auth/session`：登录建立未信任设备；trust 仅升级学习缓存授权；domain-model `auth_sessions.device_id` 归属 FK；02 启动顺序已对齐 | 已关闭 |
| REV-07 / P2 | 导入/手工转写被实时 ASR 的非空 stream 与时间字段限制，容易伪造来源或把未知时间写成 0 | OpenAPI/数据模型新增 imported 与 import/manual 来源，stream 可空；timestamp_source 含 import_declared/user_declared/unknown；03 明确未知时间为 null，不造精度 | 已关闭 |
| REV-08 / P2 | 课程没有大纲/词表时，课堂与快照需要不可空材料版本，缺少初始有效关系 | 03 表组、04 CourseMaterial 约束明确创建课程时同时建立空材料 v1；大纲/词表/语言作为同一不可变聚合版本，不拆成冲突身份 | 已关闭 |
| REV-09 / P2 | outbox “不存正文”与 wire `transcript.final` 携带文本之间缺少转换边界，容易额外保留无法彻底删除的全文副本 | 03 §6、04 §3.3：outbox 持久保存最小对象/不可变 revision 引用，发送时重新授权并读取正文；墓碑优先，不从旧事件恢复已删正文 | 已关闭 |
| REV-10 / P1 | FORCE RLS 下，opaque Cookie 尚不知 owner 时无法查会话，worker 也需跨 owner 领取调度元数据；未指明机制易诱发通用 BYPASSRLS | 03 §4：auth/scheduler 分开的窄固定 SQL 函数，仅最小元数据；固定 search_path、无动态 SQL、撤 PUBLIC EXECUTE、definer 窄表策略；正文回到普通 SET LOCAL owner 事务 | 已关闭 |
| REV-11 / P2 | ADR 把内部 PCM16/16k/mono 列实验项，而流协议将其作为冻结输入，消费者可能各自协商不兼容格式 | 07 ADR-07 已将内部格式标 F，供应商接受情况、帧/块初值和性能留 E；与04和02一致 | 已关闭 |

另复核了 03 后续新增的 DTO 映射，以及无 final 字幕时使用 capture_epoch/sample_offset 的 mark 时间锚点；这些不要求前端伪造正式来源。自动解释默认额度也未被 schema 误写成管理员追加后仍不可超过的永久上限。

## 3. 已实际执行的静态检查

执行位置为仓库根，PowerShell；Python 使用已有安装的 `C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`，未安装额外依赖。检查均只读取仓库和 Git，不创建数据库、不调用供应商。

### 3.1 PR 依赖图

实际读取 `contracts/pr-dependencies.json`，建立 ID 映射，对两种边集分别做深度优先遍历：进入节点记 visiting，重复进入判环，引用不存在 ID 判错，退出加入 seen。

```text
DAG depends_on PASS 28
DAG depends_on,integration_depends_on,paid_depends_on PASS 28
```

命令退出码 0。两种图均覆盖全部 28 节点，无未知依赖、无环。另人工核对：11a 在真实收费前置；06a 可先做模拟、付费依赖另列；09a 只依赖已审核架构，可独立 Mock；09b/10 的真实联调仍受后端与同步依赖约束。

### 3.2 AGENTS 原文保护

实际通过 `git show HEAD:AGENTS.md` 读取基点内容，与工作区 `AGENTS.md` 统一换行为 LF 后执行 `new.startswith(old)`，结果：

```text
AGENTS original prefix preserved True
```

命令退出码 0。另阅读 `git diff -- AGENTS.md`，确认变化仅为原文之后追加长期协作规则，没有删改原 PR-00 安全、产品隔离、存储和测试要求；追加段明确 PR-00 是已完成阶段、PR-01 当前仍为设计及交接，不自动开始后续 PR。

### 3.3 作者文件与模型清点

- 对 02、09、CLAUDE 的实际文本做 Markdown 围栏与行尾空白检查：围栏配对、无行尾空白；后续对02新增 `/auth/session` 与 bootstrap 描述进行了逐句复核。
- 对08、09、CLAUDE、AGENTS做相对文件链接检查，并将仍在组装的架构入口/验收报告交由主代理最终全局复验；文档组装过程不登记为架构缺陷。
- 最终清点 `domain-model.json` 为 **60 张表**。该计数不等于本代理已经穷尽验证每个 FK、CHECK 或数据迁移；完整模型结构校验由主代理的统一验证流程负责，结果应以其验收记录为准。

## 4. 复审结论和限制

本次发现已在设计文档和对应契约中关闭；当前未发现阻断 PR-01 架构提交审核的问题。读取并复核的实现图、前端交接、角色、隐私/收费边界彼此一致，尚需实验的参数仍保留相应 TBD 与发布门禁，未把实验设想写成已验证能力。

本代理**没有运行**真实 PostgreSQL、RLS SQL、迁移、worker、对象存储、持久录音/浏览器、ASR/AI 计费、恢复演练或新旧产品端到端测试；没有激活麦克风、读取真实课堂资料/密钥、调用收费 API、部署、推送或合并。PR-00 的历史测试成绩不在此重复记作本轮结果。安全函数、独立日志条件写、一致快照、成本上界与真机缓存行为必须在所属实施 PR 中提供运行证据。

本记录仅新增复审证据文件；最终设计验收、全量链接/图表/契约校验与用户审核状态以主代理汇总的 PR-01 验收报告为准。
