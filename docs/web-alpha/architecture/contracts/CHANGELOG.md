# Classroom OS契约变更记录

## 0.1.0 · 2026-10-28 · PR-01设计，整体架构原则获批

用户已接受ADR选定的系统结构与技术栈，授权独立本地commit；11项TBD中的实验/产品参数继续开放。见[批准记录](../PR01-CLOSEOUT.md)。此次收尾只更新批准状态与验证工具，不改变本版机器契约内容或版本。

首次建立 `/api/web/v1` OpenAPI3.1、WS/事件JSON Schema、领域模型、合成正反例和28个PR依赖。尚未发布、部署或供真实客户端调用。数据库逻辑模型自身schema_version为1.0.0，表示模型文档格式；不等于数据库migration revision或产品软件版本。

冻结语义：显式class_session_id、服务端owner、稳定op/指纹、CAS、来源固定版本、云durable与ASR分开、unknown不自动收费重放、首稿候选显式采用、三池缓存、墓碑优先、双账本。PRD增补classroom_id统一映射class_session_id，不新增产品实体。

设计复审修正已纳入本首版：秘密响应不得存通用收据；plan单次消费防换op重复执行；同步一致快照与owner提交序号；typed冲突解决；无final时的时间点标记；长课区间manifest；纯音重转写可空snapshot；导入来源时间可未知；课程大纲词表聚合版本。

今后变更每条必须记录日期、需求ID、旧/新版本、受影响operation/event/schema、兼容性、数据迁移、Mock/客户端影响、Astra审查与Claude/Codex确认。破坏性变化附升级窗口；产品政策变化单独记录用户批准。不得只改生成的前端类型或Python实现而不更新规范和fixtures。
