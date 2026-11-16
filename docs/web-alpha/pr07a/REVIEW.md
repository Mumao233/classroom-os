# PR-07a 独立架构与契约审查

审查角色为 Astra，执行只读架构与契约审查。项目负责人为林珈诚。调查基线为 `b86bed0c37851f2ef65b01729c097d0970cdf551`。实现、运行测试和证据回收由主执行代理负责，审查者独立读取源码与回收证据。

## 结论

本次默认关闭、合成 Provider 候选范围内，架构与 `pr07a-1` 候选契约审查通过。当前未解决的 P0、P1、P2 发现均为 0。

审查覆盖 QA domain/context/provider/repository/browser/worker、迁移 SQL、RLS、task/billing/http 接缝、候选契约、消费者说明和相关测试。

## 已复核修复

| 发现 | 修复与验证 |
|---|---|
| P1 已返回 usage 在正文保存失败或来源撤权后丢失 | 原始结果写入的 finally 路径独立执行 metadata-only 费用核对，覆盖来源/课堂/账号撤权和 DB 写入故障 |
| P2 跨时间边界片段被省略但 scope_complete 为 true | 报告 unaligned_boundary_segment，范围不完整时要求确认 |
| P2 已删除祖先占用保存结果恢复候选队列 | 完整检查课堂、当前课程、冻结材料原课程及其学期血缘，排除不可访问候选 |
| P2 关闭 QA 时阻断旧 billing 取消 | 显式组合时按任务协议分流，保留 CSRF、会话、owner 授权和 QA 关闭语义 |

通用恢复角色精确权限矩阵补入 `tasks.schema_version`，与专属协议隔离需要一致。其余逐列拒绝及 DELETE/TRUNCATE/TRIGGER/REFERENCES 禁止断言全部保留。

材料来源撤权曾被暂报为问题，完整复读后撤回。最终 metadata-only 路径与补充回归均已核对，该项不计入确认缺陷数。

## 契约与跨模块边界

候选契约记录来源类型扩展、必填快照项、可空课堂时间、回答引用别名和单问题查询。生产者 DTO 与候选说明一致，消费者升级要求已写入 CONTRACT.md。

已检查 owner/课堂组合外键、FORCE RLS、冻结版本重授权、计划唯一消费与预留原子性、专属任务派发、持久 send intent、迟到结果本地恢复、多 Attempt 未发送计划关闭，以及模拟 Provider、价格和目的地绑定。

## 独立核验证据

核验任务为 `pr07a-20261116-170331-d7e0dc70`。JUnit 含 840 个主测试用例，失败、错误和跳过均为 0，pytest 另报告 29 个 subtests passed。测试退出与 PostgreSQL 停止均为 0，对象服务已停止。

前端契约检查、TypeScript、Vite 和构建验证四步均为 0。正式契约九项检查通过，候选 OpenAPI 校验通过，pip check 报告依赖完整。

审查者对照 SOURCE.json 核验当前 29 个变更后端及候选契约文件，SHA-256 全部一致，并独立复算证据 ZIP 的 SHA-256。

- 源码 ZIP SHA-256 为 `6c607bbab7713fd6cef1f04d4395c78f7025c9ac6f08d5be7d7e46c6d011c24c`。
- 证据 ZIP SHA-256 为 `fb4d7e9ac4b2c8e90aeb864efb3dd4a3cc8cf389c0ff991d43ba51da4884e351`。

## 后续启用门禁

正式契约冻结及客户端类型升级、PR-11a 正式账本与真实价格/调用许可接线、真实付费授权、PR-04b 物理清理、来源级撤权独立故障域日志及恢复前重放、并行迁移单链复验继续保留。浏览器证书主机名和 CA 链验证限制见 VALIDATION.md。
