> 2026-11-10当前记录：按用户最新要求停止新增负载，继续普通修复及短回归。普通验证通过，R02/R04仍OPEN（高档已有失败），R05仍INCONCLUSIVE。详见[R02/R04当前开发记录](R02-R04-DEVELOPMENT-STATUS.md)；下文历史验收不改写。

# R01–R07合并整改记录

本轮以候选34冻结材料为基线，执行唯一整改清单R01–R07。分支 `feature/web-pr03b-sessions`，HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`，父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`，没有新提交。当前工作区与执行派生payload的差异均单列，不能只凭HEAD验收。

本次**S3未整体通过**。[合并整改交审回执](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/DELIVERY.md)、[实际摘要及逐项依据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/FINAL-EXPERIMENT-SUMMARY.json)、[独审原清单](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-remediation-20261109-01/REVIEW-INPUT.md)为本轮入口；这是作者侧交审，不能代替独立阶段批准。

| 项 | 作者侧本次结论 | 实际依据及限制 |
|---|---|---|
| R01 | 固定原型范围 CLOSED，待独审 | 先用候选34冻结模块触发禁用D0仍被接受；37完成20次双顺序控制，43另有20个真实降权子进程禁用拒绝，零签名/零私钥交付。当前性、独立验签和PG资格撤销一致；真实D0未建立。 |
| R02 | OPEN | 入口1秒准入/同入口5秒操作期限、锁/池/IPC/PG覆盖，计算实际结束才释放槽；45容量内部STOP。46仅补55P03 owned清理，终态PASS；不替代完整容量和worker生命周期验收。 |
| R03 | 固定原型范围 CLOSED，待独审 | 旧8次失败泄漏[8,8]反例已触发；40的640次失败+80次合法控制通过；43补160次实际HTTP101后的中断+20次合法控制。所有路径幂等归还，TimeoutError不能PASS。 |
| R04 | OPEN | 45同NAT 60次中29次完整认证成功、31次429；正常/无hash/过载/恢复详见原始统计，未通过项不删除。hash worker进程变化不冒称API进程变化；E-T部署平台仍待确定。 |
| R05 | OPEN / INCONCLUSIVE | 新真实样本0；旧采集器默认入口已封住。容量前置未整体PASS，完整独立trial采集器未实现/验证；实际3次失败预算与admitted k=4不符，未擅自改为更宽预算。 |
| R06 | 诊断与重复范围已补，旧内因未定因 | 39清理前同连接诊断；TLS1.2/1.3各500次对照；40/41/43重复及PG57014故障证据。HOME24/28/30内因继续UNDETERMINED，未把新成功回填旧STOP。 |
| R07 | 固定调度已补，待独审 | 38 issue过期/审计各20及普通MFA边界/短等待20；41秘密丢响应seam20、恢复码双顺序各20、WS矩阵180；43持久状态reserved/ready各20个新子进程。seam不是正式seed HTTP端点，进程新建不是VM重启。 |

D01–D03仅保持设计层CLOSED；TBD-08、既有P3及条件风险门禁继续OPEN。一般数据库执行权CLOSED。未修改或应用0001–0006、正式业务迁移/认证产品或获批S1/S2，未做真实D0/身份登记、真实恢复、外部API、宿主配置或VM关闭/重启/恢复；未commit、Push、合并、部署。独审交接后停止。

实际阶段、根因、源码关系、未通过与未执行项分别见 [验收](ACCEPTANCE.md)、[调试记录](EXECUTION-NOTES.md) 和交付回执。历史进行中记录保存在本轮输出的docs-before-finalization，未覆盖任何旧证据。
