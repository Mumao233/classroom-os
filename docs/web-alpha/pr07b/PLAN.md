# PR-07b 总结卡片与版本实施计划

负责人为林珈诚。开发基线为 `cf882602061e3d65341c2a130f8c4b26584e8850`，包含已合并的 PR-07a GitHub PR #14。独立分支为 `feature/web-pr07b-summary-versions`。本次授权包括实现、测试、独立审查、提交、推送及 Draft PR。

## 复用与实现

复用 PR-02a 的 owner UoW、目录授权、RLS、Repository 和 Alembic，PR-02b 的任务、Attempt、收据、Outbox、租约及本地恢复，PR-03b 的真实会话和 CSRF。来源使用 PR-07a 的 qa_sources、不可变修订、source_snapshots 和 source_items，引用继续使用冻结快照中的 S1 等别名。PR-11a 提供可注入的预留、许可、usage 和 unknown 费用核对。

版本实体沿用既有 ReviewDocument 与 ReviewRevision 契约。AI 首稿和重新生成均创建 ai_candidate。用户保存创建 personal 修订，显式 adopt 使用文档 CAS。并发个人稿保留冲突分支和当时云端文档版本。生成完成、候选版本、任务终态及 Outbox 在同一事务发布。

总结使用完整来源单位，按选定课堂范围冻结为一个全局快照。一次计划最多 512 个单位，每批最多 64 个单位、总计最多 32 批，实际分批同时满足完整消息 Token 预算。单单位超预算、数量超限或时间范围切入来源单位内部时明确拒绝。准备时持久化批次清单与全局引用别名，计划返回实际 estimated_batches 和全部批次的最高费用之和。

根任务承担第 0 批，后续批次由协调器顺序创建子任务。根任务使用 pr07b-1，子任务使用 pr07b-batch-1。每次调用各有 Attempt、预算预留与 usage，子预留通过 quota_root_id 共享一次总结配额。成功批次保存原文与验证结果，恢复复用持久结果。unknown 批次阻止后续新批次。全部结果齐备后在本地合并、校验并原子发布学习版本，汇总阶段仅进行本地计算。

候选 HTTP 契约在 PR-07a overlay 上增加重新生成目标、观察到的文档版本、三种总结粒度和 Task.batch_tasks。SourceSnapshot.items 与 Review 引用上限扩至 512，批次状态列出全局引用子集，单批最多 64 项。已有正式契约及前端生成类型的升级遵循项目冻结流程。

## 工作顺序

1. 新增版本实体、不可变约束、RLS、CAS 与个人冲突存储。
2. 接入完整来源选择、受控提示、严格结构校验和 Fake Provider。
3. 复用计划与 Worker 执行骨架，持久化多批清单、协调子任务和本地合并，按执行协议隔离 Worker。
4. 接入已定义 Review HTTP 路由及生成入口，验证会话、CSRF 和响应 schema。
5. 在 VMware 使用真实 PostgreSQL 执行正常链路、并发、故障恢复、安全与费用测试。
6. 独立复审、修复及回归，交付带证据的 Draft PR。

## 验证与启用边界

测试使用合成账号、课堂、来源与 Fake Provider。宿主负责编辑、静态检查、VMware 编排和证据回收。迁移接在 `pr07a_001` 后，新增 review_batches 与四张学习版本表。PR-04b 合并前再次复核单头、批次与任务清理、删除访问撤销、物理清理顺序和恢复前墓碑重放。实际调用计数、费用断言、测试结果与源码版本由验证记录汇总。[验证记录](VALIDATION.md)

每个生成文档固定创建时的 origin_snapshot_id，创建快照撤权后整个派生文档及后续个人版本隐藏。个人稿、候选列表与采用指针分别保留，个人写入与采用使用文档 CAS。PR-08b 接入稳定 draft_id、操作收据和冲突分支，PR-10b 接入文档版本与批次进度，PR-11b 接入逐调用费用事实、根任务配额归属和 unknown 状态。

开发、验证、合并和生产启用分别记录。Review composition 和 Worker 默认关闭。真实 Provider、正式费用账本迁移、价格核验、04b 物理清理及来源撤权恢复重放遵循各自启用门禁。
