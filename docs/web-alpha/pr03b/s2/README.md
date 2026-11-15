# PR-03b S2：正式契约及实现门禁交审

日期：2026-11-06（America/Los_Angeles）。状态：**获授权准备0.2.0正式契约候选，待独立S2审查；不实施、不实验、不commit。**

输入基线为 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`（唯一父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`），加已通过的S1七文件工作区快照。正式[APPROVE S1](../../../../outputs/pr03b/s1-remediation-review-20261106-01/REVIEW.md)已关闭S1-R01/R02/R03，D01–D03保持CLOSED（设计层）。本轮是用户后来明确授权的S2，不把S1批准当实施授权。TBD-08仍OPEN — PARTIALLY DECIDED，条件风险尚未生效。

本轮开始时本地main ref为b5f7ef9…，执行期间其他任务推进到b71e08d…；其身份契约/认证代码/迁移对象与原main基线未变，新增overnight/ASR模拟工作不属于本S2。完整观察写入交付证据，交审基准仍为上述e485322及获批S1快照；本轮未改main、未rebase或整合新main，也不声称对新main整体重做验证。

## 交审入口

| 材料 | 本轮正式候选/门禁 |
|---|---|
| [OpenAPI](../../architecture/contracts/openapi.json)及[Changelog](../../architecture/contracts/CHANGELOG.md) | 0.2.0身份增量；三密码字段/角色规则、绑定再认证、严格登录分支、Cookie/期限、自动恢复接收和错误语义；/api/web/v1不变 |
| [独立本机控制JSON Schema](../../architecture/contracts/identity-control.schema.json)及[恢复协议](RECOVERY-CONTROL.md) | 协议版本1；D0安装/变更、issue/manual complete/status、受限接收PoP、当前高水位与持久事实；不作为公开Admin HTTP API |
| [领域逻辑模型](../../architecture/contracts/domain-model.json)及[数据/锁/实现门禁](DATA-LOCKS-AND-IMPLEMENTATION-GATES.md) | 最小身份增量、权限/ACL/RLS、显式及FK/UNIQUE/trigger锁、单次消费、N01及S3实现前条件；不是DDL或已创建迁移 |
| [HTTP会话契约](HTTP-SESSION-CONTRACT.md) | 逐路由操作绑定/风险、名单/Unicode、受限cookie、自动与人工恢复分界及秘密响应；新增分级技术选择标S2候选 |
| [结构正反例](../../architecture/contracts/examples.json) | 原34条完整保留，追加身份及控制协议schema fixtures；只证明结构判断，不证明密码/签名/锁/身份真实性 |
| [兼容与交接](COMPATIBILITY-AND-HANDOFF.md) | 版本/生产者消费者/类型/Mock/fixtures/迁移/回退、待决事项及独立审查任务；消费者确认不由作者代签 |

S1原样材料保存在[原入口](../s1-preparation-20261106/README.md)，本轮不修改其中六份文档或重开其已通过方案。其“待审”措辞是当轮历史状态，正式S1结论以上述报告为准。

## 最小差异与实现边界

本轮仅改变必要正式契约及追踪说明，新增独立控制schema和本S2文件。保持公开events.schema、原五业务状态机及pr-dependencies DAG字节不变，不扩收费/采集/私人缓存/运营功能；不改代码、测试、生成类型、Mock、依赖或0001–0006。domain-model的文档格式schema_version仍1.0.0，contract_version=0.2.0，逻辑对象不表示已创建表。

公开自动恢复使用密码＋恢复码获得的受限上下文，不能变成authenticated或运营权限；专用自动enroll/confirm不能处理manual能力。人工恢复继续由原获权Admin或项目用户本人手动专用命令，浏览器仅证明新因子，无manual finalize/status权。manual final只废旧码，完整重登后显式首次生成/轮换，CLI/status/旧cap不领秘密。

所有S1数字仍以获审实验输入解释。唯一显式预算调整是S2逐路由字段核验：reauth同时携当前密码和绑定的新密码，最坏两份1024 code point的JSON转义约24KiB，故该路由提出32KiB候选，其余auth16KiB；不增prepare接口、不静默截断合法密码。此选择须本次独审及S3资源/边界验证，未实施，不能将其扩为其他路由默认值。

## 独立审查与停止

本轮完整对象由[交付回执](../../../../outputs/pr03b/s2-contracts-20261106-01/DELIVERY.md)中的基线、before/after、文件hash及两层补丁固定；没有新提交SHA。独审应逐项判断字段/严格分支/语义、D0当前性、四时间、status事实/权限、N01全锁图及N02交付、兼容/消费者确认和未决真实留存，输出S2 APPROVE或REQUEST CHANGES及明确条件。

S2交审不表示S2已获批准，更不授权S3。实际生成类型/客户端Mock/后端/增量迁移、环境及guard适配、真实PG/HTTPS/WS/密码/签名实验都须S2获审及后续明确实施/实验授权。真实D0/身份/核验渠道/保管与留存批准、演练、补偿证据及启用批准单列；U02未定不冻结对应真实留存协议。不commit、Push、合并或部署，交付即停止。
