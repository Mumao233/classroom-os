# Classroom OS契约变更记录

## 0.2.0 · 2026-11-06 · PR-03b S2 独立交审候选

S1 已独立 APPROVE，用户授权本轮正式契约及实现门禁修改；**本条是规范候选，尚待S2独审，非实现/启用/发布批准**。D01–D03 CLOSED仅设计层，TBD-08 OPEN，条件风险未生效。AUTH-01/03/04为主体，AUTH-05/06保留权限及撤销，AUTH-02/07/08归属不扩。

- OpenAPI0.1.0→0.2.0，/api/web/v1保留；三新设密码字段6–1024且服务端Admin12；当前实现和生成类型不在本轮改动。
- 明确受限recovery_required及MFA条件分支、操作绑定reauth与当前角色/因子/epoch、Cookie/remember/CSRF/Origin/WS及名单暂停；追加两个仅自动恢复接收路由，不公开manual issue/complete/status/根维护。
- 独立identity-control.schema.json（协议版本1）表达D0安装/变更、issue/manual complete/status及接收端PoP；根/当前高水位/撤销、四时间、单次能力和unknown规则不由JSON结构冒充机制证明。
- 统一HTTP session/reauth token生产编码与控制Secret桥接包络，明确至少32随机字节及canonical unpadded base64url；status独立于目标当前role/epoch和旧写证明/核验详情。authenticate/key-PoP的准确签名body及deployment来源由schema和协议固定。
- domain-model保留schema_version文档格式1.0.0，contract_version=0.2.0，最小身份逻辑增量和不变量不等于已创建迁移；原非身份表、事件schema、五流和DAG不改。
- 新增schema正反fixtures保留原34条；新增错误/状态及Reauth/MFA请求是严格消费者兼容变化。旧未知状态拒绝，不能映射authenticated；同版本前后端联调窗口与禁止混合启用见[S2兼容](../../pr03b/s2/COMPATIBILITY-AND-HANDOFF.md)。
- 原rotateRecoveryCodes operationId/路径保留，完整重登后显式首次生成/轮换；manual final无码，CLI/status/旧cap不领秘密；秘密一次展示和丢响应不回放。
- RecoveryCodes结构要求成功组至少1个且无重复（minItems/uniqueItems），maxItems20保留；每组10及生成编码仍是待实测配置，不以结构约束冒充熵或单次消费验证。
- Reauth双密码JSON转义上限核验后仅该路由提出32KiB技术候选，其他auth16KiB；S1原样阈值不被改写，须独审及S3边界验证。

生产者Codex提交候选；Claude消费者确认与Astra独立契约审查均为S3前门禁，未伪称已取得。后续生成类型、Mock、客户端/后端/增量迁移必须在明确实施授权下同步；本轮不生成或修改代码/测试/Mock/依赖。不能将静态fixtures通过写成真实PG/HTTPS/WS/身份/秘密安全通过。U02真实留存未定时不冻结或启用对应真实协议。

## 0.1.0 · 2026-10-28 · PR-01设计，整体架构原则获批

用户已接受ADR选定的系统结构与技术栈，授权独立本地commit；11项TBD中的实验/产品参数继续开放。见[批准记录](../PR01-CLOSEOUT.md)。此次收尾只更新批准状态与验证工具，不改变本版机器契约内容或版本。

首次建立 `/api/web/v1` OpenAPI3.1、WS/事件JSON Schema、领域模型、合成正反例和28个PR依赖。尚未发布、部署或供真实客户端调用。数据库逻辑模型自身schema_version为1.0.0，表示模型文档格式；不等于数据库migration revision或产品软件版本。

冻结语义：显式class_session_id、服务端owner、稳定op/指纹、CAS、来源固定版本、云durable与ASR分开、unknown不自动收费重放、首稿候选显式采用、三池缓存、墓碑优先、双账本。PRD增补classroom_id统一映射class_session_id，不新增产品实体。

设计复审修正已纳入本首版：秘密响应不得存通用收据；plan单次消费防换op重复执行；同步一致快照与owner提交序号；typed冲突解决；无final时的时间点标记；长课区间manifest；纯音重转写可空snapshot；导入来源时间可未知；课程大纲词表聚合版本。

今后变更每条必须记录日期、需求ID、旧/新版本、受影响operation/event/schema、兼容性、数据迁移、Mock/客户端影响、Astra审查与Claude/Codex确认。破坏性变化附升级窗口；产品政策变化单独记录用户批准。不得只改生成的前端类型或Python实现而不更新规范和fixtures。
