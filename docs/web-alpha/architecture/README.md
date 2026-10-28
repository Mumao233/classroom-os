# Classroom OS · PR-01 架构交付入口

日期：2026-10-28。设计起点：`feature/web-alpha-0.1.0` / `f7731002d974ea29296090c366b68569fc69ba89`。状态：**用户已原则批准整体架构，接受ADR选定的系统结构和技术栈；授权PR-01独立本地提交收尾。** F类结构成为获批准设计基线；11项TBD中的实验/产品参数全部继续开放，不把架构批准当成功能实现或后续PR授权。批准范围与提交核对见[收尾记录](PR01-CLOSEOUT.md)。

本次以完整37页 [PRD v1.2](../Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2.pdf) 为产品基准，保留 [PR-00验收](../PR00_ACCEPTANCE.md)、[需求矩阵](../REQUIREMENTS_MATRIX.md)、[决策](../DECISIONS.md)、[冲突](../CONFLICTS.md) 的历史原文。p21视觉示意和p35–37增补已核读。本目录新增设计，不重写产品规则。

## 建议阅读顺序

| 交付 | 解决的问题 |
|---|---|
| [01 系统架构](01-SYSTEM.md) | 总图、模块边界、后端进程、目录、旧源码复用、未来客户端 |
| [02 前端与Aurora](02-FRONTEND-AURORA.md) | 技术栈、路由、状态/采集生命周期、组件/tokens、响应式、Terra边界 |
| [03 数据与后端](03-DATA-BACKEND.md) | ER、字段/关系/约束、RLS、事务、worker、迁移、DTO映射 |
| [04 接口与数据流](04-CONTRACTS-FLOWS.md) | HTTP/WS、状态机、录音→ASR→问答→总结、幂等/重放/故障 |
| [05 安全、离线与删除](05-SECURITY-OFFLINE.md) | 邀请/MFA/会话、租户、三池密钥/同步、删除/备份防复活 |
| [06 成本与部署](06-COST-OPERATIONS.md) | 双账本/Token/预算闸门、资源算术、单机拓扑、扩容/运维/回退 |
| [07 ADR登记](07-ADR-REGISTER.md) | 13项明确选型比较、冻结F/实验E/政策P、11项TBD关闭责任 |
| [08 28个PR实施与协作](08-IMPLEMENTATION.md) | 完整依赖/并行/角色/Worktree/契约变更/联合验收 |
| [09 Claude前端交接](09-CLAUDE-FRONTEND-HANDOFF.md) | 新会话接手PR-09a所需文档、Mock/模拟ports、边界和验收 |
| [需求追踪](10-TRACEABILITY.md) | 原118项需求逐项映射到设计和后续验收，设置页另列 |
| [PR-01验收报告](PR01-ACCEPTANCE.md) | 本轮实际检查、证据、保护范围、限制与审核停止点 |
| [PR-01批准与收尾](PR01-CLOSEOUT.md) | 批准范围、完整文件清单、重新验证和后续Worktree建议 |

## 可机器使用的设计契约

- [OpenAPI3.1](contracts/openapi.json)：HTTP操作/请求/响应/错误/安全要求。
- [WS与事件JSON Schema](contracts/events.schema.json)：命令、字幕、durable回执、重放及关闭语义。
- [合成正反例](contracts/examples.json)：结构验证输入，不是已运行后端。
- [五流状态与守卫样例](contracts/state-machines.json)：16条允许/拒绝路径，覆盖unknown、durable、晚到ACK和删除优先。
- [数据模型](contracts/domain-model.json)：字段、类型、NULL、PK/UK/FK/index/check、事务与RLS不变量；不是已执行DDL。
- [PR依赖图](contracts/pr-dependencies.json)：28节点、硬依赖/联调/付费门槛。
- [契约变更记录](contracts/CHANGELOG.md)：唯一版本变更入口。

Markdown中的Mermaid是可维护图源，可在支持Mermaid的阅读器渲染；本轮校验结果见验收报告。目录/schema/snippet是后续实施规范，本次没有创建`web/frontend`或Web后端应用骨架。

## 用户要求到交付的位置

| 需求范围 | 主要位置 |
|---|---|
| 1 全局架构/模块；2 前端架构 | 01、02 |
| 3 后端/领域/任务；4 PostgreSQL | 03、domain-model |
| 5 HTTP/WS/事件；6 录音/ASR/AI/总结 | 04、OpenAPI、events |
| 7 认证/租户/隐私/删除；8 离线/同步/恢复 | 05、03、04 |
| 9 Token/预算/成本；12 部署/扩容/风险 | 06 |
| 10 Aurora/可复用组件；11未来客户端 | 02、01 |
| 13 28个PR依赖/并行/顺序 | 08、pr-dependencies |
| 补充 Agent长期规范/Claude接手 | 根AGENTS/CLAUDE、07–09 |

用户于2026-10-28单独授权本地PR-01 commit。本次不访问收费API、不部署、不读取真实课堂或密钥、不修改旧业务、不推送/合并/Tag/Release/创建远端PR。用户另行明确授权后，才进入所选实施PR；本次不创建后续Worktree。
