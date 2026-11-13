> 2026-11-10当前记录：按用户最新要求停止新增负载，继续普通修复及短回归。普通验证通过，R02/R04仍OPEN（高档已有失败），R05仍INCONCLUSIVE。详见[R02/R04当前开发记录](R02-R04-DEVELOPMENT-STATUS.md)；下文历史验收不改写。

# 十九条状态推演：本轮新增证据

本轮以候选34冻结材料为基线，执行唯一整改清单R01–R07。分支 `feature/web-pr03b-sessions`，HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`，父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`，没有新提交。当前工作区与执行派生payload的差异均单列，不能只凭HEAD验收。

本次**S3未整体通过**。[合并整改交审回执](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/DELIVERY.md)、[实际摘要及逐项依据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-EXPERIMENT-SUMMARY.json)、[独审原清单](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/REVIEW-INPUT.md)为本轮入口；这是作者侧交审，不能代替独立阶段批准。

下方候选34记录仍是历史原文。本轮对应新增范围如下；其余条目不因此自动PASS，完整A项与产品实现继续分列。

| 原状态序号 | 本轮增加 | 限制 |
|---|---|---|
| 1–3 | 当前禁用D0的parent/独立child/PG撤销；20次reserved/ready持久状态读回和新执行进程 | 合成installer固定根；不等同真实D0或VM重启/断电恢复。 |
| 4/6 | issue证明过期拒绝20；MFA前/等于/后及实际短锁跨截止20 | issue年龄由受信fixture设定；原complete/status的真实121秒记录保留，不重复伪称本轮新墙钟等待。 |
| 9 | issue审计晚失败20次与完整事务状态不变 | 23505为预登记合成审计碰撞故障，不冒称所有PG故障类型。 |
| 10/17 | cap/seed交付丢失固定seam20，status事实查询无秘密/无重签/无变更；旧issue重放拒绝 | 未实现正式seed HTTP端点，不把可信fixture当完整端到端恢复。 |
| 12/15 | 撤销先提交后issue/complete/status拒绝；反向持锁顺序各20；真实降权child禁用拒绝20 | 真实权限/根维护部署与真实身份生命周期仍未启用。 |
| 13/16/19 | 同连接清理前TCP/TLS/upgrade/PG/首帧诊断、真实57014、错绑定/容量/重连矩阵和明确秘密通道审计 | 旧HOME24/28/30具体内因未追回；不扩称全系统/任意编码或管理员内存取证覆盖。 |

D01–D03仅保持设计层CLOSED；TBD-08、既有P3及条件风险门禁继续OPEN。一般数据库执行权CLOSED。未修改或应用0001–0006、正式业务迁移/认证产品或获批S1/S2，未做真实D0/身份登记、真实恢复、外部API、宿主配置或VM关闭/重启/恢复；未commit、Push、合并、部署。独审交接后停止。

---

## 修订前历史记录（保留原文；当前结论以上方本轮记录为准）

# 原十九条状态推演的实际证据

原文及预期保持 [设计矩阵](../TEST-EXPERIMENT-GATES.md) 不变。本页仅将当前一次性原型的实际子例映射回来；**不将设计推导、静态顺序检查、可信时间 seam 或单个成功子例写成完整十九条全部通过**。断言下标见 [STATE-TRACE.json](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-mechanisms-20261108-01/STATE-TRACE.json)。

| 序号 | 原场景 | 实际子断言 | 证据边界 |
|---|---|---:|---|
| 1 | 输入同时携带自己的root、自签清单、本人OS/key映射和匹配PG版本 | 2 | 受信合成安装、固定根/映射/镜像拒绝；没有建立真实外部授权来源或完整自带根输入接口；PARTIAL |
| 2 | 新key完成PoP，但没有独立维护批准，或恢复角色尝试登记/换根 | 17 | 真实签名字段篡改/PoP 非批准/受限写根拒绝；不替真实维护身份登记；PARTIAL |
| 3 | 合法变更配置先切换、PG未切换或反向次序；旧有效签名清单被重放 | 6 | reserve/PG/清单错配和前滚/根缺失；尚未覆盖进程崩溃后 ledger 重启恢复；PARTIAL |
| 4 | t0全部独立验证成功，t+4分钟才issue；或complete独立证明已验证4分钟才执行 | 6 | 包含算术/可信时间 seam 及实际过期 complete；旧 issue 过期写入的全独立反例未整体覆盖；PARTIAL |
| 5 | 5分钟候选challenge在4:59完成服务端校验，随后fresh窗口内执行 | 4 | 可信时间 seam 的 4:59/5:00/5:01 和消费后不再截断；不宣称五分钟墙钟实验；PARTIAL |
| 6 | 验证时证明新鲜，但等user/authority/case锁后超过operation_deadline | 14 | 真实 PG user、factor/group 等待及独立资格到期子例；精确等待/SQLSTATE 见原始断言；PARTIAL |
| 7 | issue成功消费nonce；原issue证明/challenge已到期；pending仍有效且新因子已pending-confirmed | 3 | 实际已消费并老化的 issue 与 fresh complete；未续旧证明、未要求丢失的旧 MFA；PARTIAL |
| 8 | 两个complete、旧completion_nonce重放或改变接收端 | 49 | 20 次 complete 竞争与 receiver/flow/plan 拒绝；更换接收端的完整重新批准链未整体覆盖；PARTIAL |
| 9 | 初次issue前PG/必需审计失败 | 2 | 审计故障实测针对 complete；初次 issue 的全部 PG/审计故障尚未整体覆盖；PARTIAL |
| 10 | issue已提交但响应丢失，或浏览器未确认/超时、执行者中断 | 2 | 锁定 pending/not_committed 与零恢复写；issue seed/cap 回包丢失、取消/超时链未整体覆盖；PARTIAL |
| 11 | 新TOTP证明已pending-confirmed，浏览器/其他Admin请求finalize或代complete | 44 | 浏览器无 final 路由、其他 Admin 拒绝与单次 final；产品 receiver pending-confirm 入口未接入；PARTIAL |
| 12 | 原执行者撤权先提交；或complete先持有全部必要锁 | 80 | status 撤权的两个提交顺序；write/登记撤销全部竞争链未整体覆盖；PARTIAL |
| 13 | 最终PG/KEK/必需审计失败，或最终COMMIT回包丢失 | 6 | 真实审计回滚及发 COMMIT 后不读回包；AEAD key/version 拒绝，不等于外部 KEK 故障注入；PARTIAL |
| 14 | status看到final已提交/未提交，或只看到pending | 42 | 本人最小持久事实和重复零恢复写；完整正式 status 工具尚未实施；PARTIAL |
| 15 | status调用者权限/authority已撤销、其他Admin、旧targetepoch/cap/browser或仅case/op | 66 | 当前资格投影/撤权/错 actor；真实 authority/key 生命周期与全部旧 browser/cap 入口未整体验收；PARTIAL |
| 16 | status查不到、错op、PG不可用/超时或重复查询 | 26 | 未知标识与锁/statement 超时分列；未将普通 SQL 故障冒称全部 status PG 不可用实验；PARTIAL |
| 17 | completed后要求重签能力/用旧cap领新恢复码，或complete响应丢失后重试 | 26 | completed 拒再执行、final 无码和新组响应丢失不回放；CLI/status 取码权限另由窄角色矩阵支持；PARTIAL |
| 18 | 安全事务持users排他锁，LocalReconciler持业务counter、Outbox owner FK等待user | 961 | 实际 PG counter/FK 与独立事件路径及预登记坏锁序对照；不替正式业务集成或所有可能交错证明；PARTIAL |
| 19 | 私钥/PIN/seed/能力进入错误诊断，或mark_submitting/旧unknown结果不明 | 12 | 明确禁止通道高熵标记及 lab 任务哨兵；不宣称覆盖任意编码/管理员取证，也未重跑原业务账本回归；PARTIAL |

相同断言可支持多条推演，不可相加为独立测试总数。所有实际失败、STOP 及未执行分支保留；D01–D03 的设计层 CLOSED 不由此重开或升级成真实启用批准。
