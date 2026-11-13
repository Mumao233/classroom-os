> 2026-11-10当前记录：按用户最新要求停止新增负载，继续普通修复及短回归。普通验证通过，R02/R04仍OPEN（高档已有失败），R05仍INCONCLUSIVE。详见[R02/R04当前开发记录](R02-R04-DEVELOPMENT-STATUS.md)；下文历史验收不改写。

# A01–A46：本轮R01–R07补充映射

本轮以候选34冻结材料为基线，执行唯一整改清单R01–R07。分支 `feature/web-pr03b-sessions`，HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`，父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`，没有新提交。当前工作区与执行派生payload的差异均单列，不能只凭HEAD验收。

本次**S3未整体通过**。[合并整改交审回执](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/DELIVERY.md)、[实际摘要及逐项依据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-EXPERIMENT-SUMMARY.json)、[独审原清单](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/REVIEW-INPUT.md)为本轮入口；这是作者侧交审，不能代替独立阶段批准。

原A01–A46完整门禁未删除，下方保留候选34历史映射；本节只给本轮增量，不能把同一断言跨表重复相加。

| A项 | 新证据 | 仍保留的范围 |
|---|---|---|
| A03/A06/A27/A30 | 45的2+8、实际准入回包丢失、16/32短突发、NAT/来源预算、资源组、正常/基线/过载/恢复预排窗口 | 容量仍未整体验收；实际取得行数、成功与拒绝/故障分层均见摘要，预排不等于完成；条件风险不生效。API进程变化未验证。 |
| A04 | 独立trial/目标/时间块、k1/2/4/8与同时CI分析自检；失效旧入口拒绝 | 完整真实采集器/样本均缺，INCONCLUSIVE，不能由分析器存在判无泄漏。 |
| A20/A21/A22 | 40的640负例/80合法控制，43的160次101后中断，41错绑定100、来源8与全局64各20、实际PG57014与撤权重连各20 | 单一批准来源证明来源8上限；未独立填满多个来源的全局未认证16槽。历史超时原因未知，正式课堂链未接入。 |
| A31–A40/A43/A44 | 37撤销双顺序；43禁用子进程20、reserved/ready新进程各20；41丢响应seam20及码组双顺序各20 | 真实登记、外部根部署、正式恢复工具/seed端点和真实用户不在本轮范围。 |
| A25/A41/A42 | 38 issue过期/审计20、MFA截止与实际短等待20；此前真实跨121秒证据仍保留 | 可信fixture设定证明年龄与实际短锁等待分别记录，不冒称新增issue墙钟121秒实验。 |
| A45/A46 | 既有独立identity事件/FK锁与final后显式新码不变量保留；41补恢复码双锁顺序 | 不竞争业务counter/Outbox，不降低user锁；正式产品集成与秘密新组响应规则继续正式审查。 |

D01–D03仅保持设计层CLOSED；TBD-08、既有P3及条件风险门禁继续OPEN。一般数据库执行权CLOSED。未修改或应用0001–0006、正式业务迁移/认证产品或获批S1/S2，未做真实D0/身份登记、真实恢复、外部API、宿主配置或VM关闭/重启/恢复；未commit、Push、合并、部署。独审交接后停止。

---

## 修订前历史记录（保留原文；当前结论以上方本轮记录为准）

# A01–A46 实际机制证据及保留门禁

本页只映射 HOME32 / run `030f3bbef5994a658e218ad175a90146` 的实际断言。harness 状态为 **PASS**；断言成功不表示完整 A 项、产品实现或风险接受已通过。精确下标与名称见 [COVERAGE.json](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/COVERAGE.json)，原始场景/阈值仍见 [获批 S1](../s1-preparation-20261106/ACCEPTANCE-AND-EVIDENCE.md)。

| ID | 实测子断言 | 剩余范围与结论 |
|---|---:|---|
| A01 密码边界 | 13；另 1 组合成自检 | 标量/角色纯自检；三正式设密入口、客户端计数及全 wire 分支未实现验收。**PARTIAL / OPEN** |
| A02 本地名单 | 13 | 固定本地合成名单控制；真实名单供应、签名更新/陈旧期与全入口故障尚未验收。**PARTIAL / OPEN** |
| A03 总预算 | 119 | 真实多进程准入与误拒数据分列；正常混合负载、跨全部入口预算及尾部阈值未整体通过。**PARTIAL / OPEN** |
| A04 枚举 | 2 | 必测留出/重复聚合保留；样本、独立 trial、角色/长度/冷热/负载分层及分位等价不足，不得 PASS。**PARTIAL / OPEN** |
| A05 邀请激活 | 29 | 一次性 PG 原型的名额/代次/消费与 20 次竞争；不替正式邀请邮件/应用链回归。**PARTIAL / OPEN** |
| A06 preauth/CSRF | 73 | 16/32KiB、Origin/CSRF 与限流控制分列；实际 NAT 吞吐按测量值，不能由 60 个请求推断 60/min。**PARTIAL / OPEN** |
| A07 Cookie | 5 | 真实浏览器子集；正式 HTTP 拒绝、缓存/代理链和完整 GET 无业务写仍需产品接入验收。**PARTIAL / OPEN** |
| A08 fixation/轮换 | 2 | 固定 PG 轮换/旧 token 拒绝；完整并发轮换及产品会话迁移链未整体验收。**PARTIAL / OPEN** |
| A09 会话期限 | 4 | PG 期限差与过期不复活；全前/等于/后、remember 和长时浏览器策略未整体覆盖。**PARTIAL / OPEN** |
| A10 Principal | 501 | 实际受限角色与当前资格投影；没有接入正式 Principal/OwnerContext HTTP 链。**PARTIAL / OPEN** |
| A11 TOTP | 23 | 真实库/HTTPS 与同一步 PG 竞争；全漂移窗口、跨所有入口累计退避未整体验收。**PARTIAL / OPEN** |
| A12 恢复码 | 64 | PG 单次消费、无码普通会话及显式新组；完整公开受限恢复入口未实现。**PARTIAL / OPEN** |
| A13 新因子 | 6 | 加密新因子、final、完整重登子集；全部 pending/取消/超时故障矩阵未整体覆盖。**PARTIAL / OPEN** |
| A14 重置/rehash | 29 | 真实 PG 两种顺序及 20 次 CAS；正式邮件服务、验证到签发的全故障链仍未验收。**PARTIAL / OPEN** |
| A15 改邮箱 | 20 | 同代次两有效证明真实竞争；外部邮件投递/保留及完整应用入口未验收。**PARTIAL / OPEN** |
| A16 再认证 | 68 | R3 绑定、单次/陈旧与运维证明子集；R1/R2 全路由分类和边界未整体覆盖。**PARTIAL / OPEN** |
| A17 受限权限 | 277 | 窄角色拒绝/浏览器不能 final；所有课堂/导出/缓存入口尚未接入验证。**PARTIAL / OPEN** |
| A18 全因子丢失 | 41 | 仅合成登记及受限命令原型；真实独立核验、根和身份登记明确不在本次范围。**PARTIAL / OPEN** |
| A19 撤销竞争 | 1042 | status/通知/活 WSS 的实测子集；全业务撤销事务线性化未验收。**PARTIAL / OPEN** |
| A20 WS认证 | 24 | 真实 TLS upgrade/首帧及重复控制；历史超时原因未证实，全课堂/fence/epoch 错配及 URL 票据变体仍未整体覆盖。**PARTIAL / OPEN** |
| A21 WS预算 | 4 | 实际二进制/越界/业务先行/首帧期限；64/16/8 容量穷尽和 30 条正常 WS 混合负载未整体验收。**PARTIAL / OPEN** |
| A22 活WS撤销 | 2 | 每敏感帧实际 PG 复核、通知丢失；PG 故障/墓碑/cursor 重连全矩阵未整体覆盖。**PARTIAL / OPEN** |
| A23 RLS/ACL | 462 | 一次性 lab FORCE RLS/EXECUTE 矩阵；正式业务嵌套 owner/池复用需原业务套件。**PARTIAL / OPEN** |
| A24 秘密丢响应 | 4 | 新码 TLS 提交后丢响应实测；所有秘密类别/缓存/APM/异体重试未整体覆盖。**PARTIAL / OPEN** |
| A25 PG/提交故障 | 7 | 真实审计回滚、普通 lock/statement 超时和不读 COMMIT 回包；全 PG 不可用/最多三次内部重试未整体验收。**PARTIAL / OPEN** |
| A26 KEK | 4 | 成熟 AEAD 成功/错 key/错版本；外部 KEK 可用性、版本生命周期和所有入口回滚未整体验收。**PARTIAL / OPEN** |
| A27 哈希过载 | 86；另 1 组合成自检 | 实际 Windows 筛选/过载与恢复分列；E-L/E-T、p99 样本、正常混合负载及所有 PHC 兼容条件未整体通过。**PARTIAL / OPEN** |
| A28 旧任务兼容 | 1 | 只检查 lab 哨兵与零 Provider；原后端 raw/Receipt/Outbox/fence/unknown 回归未运行。**PARTIAL / OPEN** |
| A29 增量迁移 | 0 | OUT OF SCOPE：用户禁止正式业务迁移；0001–0006 不改，不把 fixed lab DDL 当升级验收。**NOT RUN / OPEN** |
| A30 条件风险 | 0 | NOT EFFECTIVE：补偿尚未整体通过；未申请或激活真实 10–30 人 Alpha 风险接受。**NOT RUN / OPEN** |
| A31 学生人工恢复 | 42 | 合成执行者及 case/op 原型；真实申请人核验/获权 Admin 登记与专用产品命令未启用。**PARTIAL / OPEN** |
| A32 管理员人工恢复 | 10 | 合成 OS peer＋独立签名/映射及一次性 PG 手动 final；不代表真实项目用户本人执行。**PARTIAL / OPEN** |
| A33 批准绑定 | 68；另 1 组合成自检 | 签名/plan、receiver/flow、nonce 及 PG 重核子集；全字段/撤销竞争矩阵未整体覆盖。**PARTIAL / OPEN** |
| A34 恢复中断 | 4 | 实际提交/回滚/结果不明子集；各阶段崩溃、暂停与重启持久恢复全矩阵未整体覆盖。**PARTIAL / OPEN** |
| A35 恢复秘密响应 | 25 | final 无码、status 无码、新组独立轮换；issue seed/cap 交付丢失全链未整体覆盖。**PARTIAL / OPEN** |
| A36 手动complete | 49 | 实际合成手动 final/竞争/旧权拒绝；完整产品命令与所有旧授权类别未整体验收。**PARTIAL / OPEN** |
| A37 秘密通道 | 17 | 受限进程/本机 pipe/明确高熵标记通道；全系统日志/编码/录制/任意管理员取证不作覆盖声明。**PARTIAL / OPEN** |
| A38 首装根 | 6 | 受信合成安装/独立子进程/写根拒绝；真实外部部署授权来源未建立。**PARTIAL / OPEN** |
| A39 登记/换key/撤销 | 14 | 完整维护签名字段、PoP 非授权与单次控制；跨进程维护/恢复竞争及真实维护渠道未整体验收。**PARTIAL / OPEN** |
| A40 高水位/错配 | 6 | 合成高水位前滚/错配/根缺失；进程崩溃后的持久重启和真实外部 ledger 未整体验收。**PARTIAL / OPEN** |
| A41 四时间 | 23；另 2 组合成自检 | DB 计时及真实跨 120 秒锁等待分列；不是全产品校验器/全部 schedule 的通过结论。**PARTIAL / OPEN** |
| A42 fresh complete | 43 | 旧 issue 已用/已老化仍可 fresh complete，旧 complete 拒绝；全 pending/接收端变更矩阵未整体覆盖。**PARTIAL / OPEN** |
| A43 status事实 | 33 | 本人最小事实、锁后期限和 cap/目标 epoch 独立性子集；未知/故障分支逐项保留。**PARTIAL / OPEN** |
| A44 status当前资格 | 117 | 当前投影/撤权两顺序和零恢复写；未接入正式独立认证工具或真实 authority 生命周期。**PARTIAL / OPEN** |
| A45 通知与FK锁 | 961 | 真实 PG 120 次 producer/FK、200 次事件路径交错及 20 次预期旧坏路径死锁须逐项达预期；不替原业务代码集成验收。**PARTIAL / OPEN** |
| A46 final后新码 | 45 | 真实 TLS final 无码/完整密码＋新 MFA/显式新组及丢响应不回放；未开放正式命令或真实恢复。**PARTIAL / OPEN** |

计数允许同一断言支持多个 A 项，不可相加为不同测试数。未匹配名称不自动说明机制不存在；须结合源码、原始结果和独立审查。A04 和容量数字另以原始统计报告判断，不能将“正确报告 INCONCLUSIVE”的断言记成安全 PASS。D01–D03 仅保持设计层 CLOSED，TBD-08/既有 P3 OPEN，条件风险不生效。
