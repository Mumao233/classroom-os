# PR-08b 离线同步与冲突

负责人林珈诚。Web Alpha 0.1.0，基线为远端 main `8b1e44e`，分支 `feature/web-pr08b-offline-sync`。HTTP 使用 `0.2.1` 加现有 PR-07b 契约 overlay（`pr07b-1`）。本次授权包含独立分支开发、VMware 分阶段验证、独立 Astra 复审、提交与 Draft PR。

本包覆盖 SYNC-01/02、UI-REVIEW-09/11，并支撑 AUTH-06、DATA-03/04/06、UI-COURSE-11、UI-REVIEW-08 的同步与撤权接缝。

## 依赖核对

| 工作包 | 当前 main 的实现 | PR-08b 接入 |
|---|---|---|
| PR-02b | `PgEventRepository` 的 owner counter、事务提交序号、HMAC cursor、缺页检测，以及 `PgReceiptRepository` 的操作锁和指纹 | 一致性 bootstrap、连续 changes、逐操作收据与结果未知恢复 |
| PR-04b | 删除请求、墓碑、后代访问撤销、`object.deleted` 事件及 `/deletions/{id}` | 删除事件优先，恢复授权后仍拒绝旧写回 |
| PR-07b | 不可变个人版本、稳定 draft_id、文档 CAS、冲突分支与历史投影 | 四类 typed 操作、冻结冲突双方、显式解决及另存笔记 |
| PR-08a | Web Crypto/IndexedDB 三池、精确 generation ACK、持久失效屏障及祖先墓碑 | 加密离线 outbox、解锁前水位补齐、晚到 ACK 保护与刷新恢复 |

现有公开契约已定义 `SyncOperation`、`SyncBatch`、`SyncResult`、`OperationReceipt`、`SyncBootstrap`、`Changes`、`Conflict` 和 `ResolveConflict`。支持的操作为个人版本、标记、偏好和课程。解决模式为手动合并、另存新笔记及明确采用云端。

同步冲突可以包含实际 PR-07b 生成候选。装配使用既有 `tools/pr07b/contract_candidate.py` 的 `build(base)`，保持候选的原始来源别名、结构化内容及生成任务引用。该依赖与现有 ReviewBrowser 入口一致，08b 沿用该 overlay。

## 实现约束

- bootstrap 在同一 REPEATABLE READ 事务内读取完整授权元数据与 committed owner watermark。超过 10,000 投影显式拒绝。
- changes 按 owner 提交顺序返回。删除事件投影为 `object_type=deletion`，ID 与 revision 来自对应删除请求，客户端通过既有删除详情接口取得范围。
- 外层 batch 键绑定完整有序批次。内层操作保持稳定键与冻结请求体，各自独立事务。失败或冲突保留其余操作的已提交事实。
- 收据只保存必要状态和引用。重放先检查当前授权与墓碑，同键异体拒绝，结果未知先核对原收据。
- 本地未同步内容保留在独立加密草稿池。完整快照用于核对授权，当前授权无法证明的学习缓存失效，受限草稿密文保留。删除与撤权后继续禁止读取原对象或自动创建替代对象。
- ACK 只确认所发送的准确 generation。同步响应中的 cursor 不用于跳过未消费事件。
- 冲突保留提交时的本地操作和服务器版本。用户解决采用当前 CAS 与新操作键，继续编辑后到达的旧 ACK 不得标记新内容已同步。
- PR-07b 旧冲突缺少原始 base_revision 时保持原历史入口可读，专用同步接口明确报告兼容缺口。仅在原始操作与旧收据指纹核对一致后补全操作信息。
- 服务通过显式依赖与默认关闭配置接入。生产缓存策略和可信设备约束继续由原门禁控制。

## 分阶段验证

| 阶段 | 本次要求 |
|---|---|
| 静态与契约 | Python AST、TypeScript、ESLint、生成类型一致性、契约与 fixtures、diff 检查 |
| 真实 PostgreSQL | 前向迁移、RLS、跨 owner、两连接 CAS、提交顺序、水位一致性、重复操作、断点恢复、删除竞态、typed resolve |
| 原生浏览器 | Web Crypto/IndexedDB、刷新与离线队列、延迟 ACK、保留新输入、冲突操作、撤权与删除、满额或持久失败 |
| 回归 | 全量 Web 后端、前端、原 Windows Python/Node/DPAPI 门禁，记录实际环境、源码散列、退出码和失败 |
| 独立复审 | Astra 核对实现、契约、测试证据，按 P0–P3 登记并修复复验 |
| Draft PR | 精确暂存、Mumao233 提交身份、负责人林珈诚、验证与限制，保持 Draft |

功能测试在现有 VMware Windows 来宾执行。宿主承担源码编辑、静态检查、打包、编排与证据回收。测试使用隔离数据库和合成内容。
