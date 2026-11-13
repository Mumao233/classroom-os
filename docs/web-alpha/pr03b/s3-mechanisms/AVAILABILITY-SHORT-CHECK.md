# PR-03b：异常诊断与普通功能短回归

按用户最新要求，本轮不执行压力测试、长时间容量窗口、WSS大矩阵或R05枚举采样；不减少原安全门槛来换取通过。R01/R03沿独立复审结论CLOSED（限固定原型），R07固定调度结论保留。**R02/R04仍OPEN，R05仍INCONCLUSIVE，S3未整体批准。**

本轮普通功能结果 **PASS**，run `b707fd8da722498c9a710d5dbedaa04a`；guest固定断言931/931。实际普通HTTPS请求7次、单用户、串行执行；前置原guard/角色与所有权检查保留，不是压力测试。此前45的NAT29/60、无hash登录0/1000、业务12/10000、零实际hash过载和STOP完整保留，不以本次短回归覆盖。

修改：新增有界请求诊断，按请求编号关联pool等待、事务、原生身份、角色、SQL正文、hash及回复阶段；在401/403/429映射之前记录原始异常类别/SQLSTATE/耗时，回复写失败作为次生事件，不能覆盖最初原因。只记录固定阶段、固定路由和栈位置，不记录异常消息、密码、Cookie、token或请求正文；最多128请求/每请求64条事件，丢弃数显式给出。所有安全检查、期限、SQL、哈希成本、HTTP错误语义及获批契约保持原规则。

短回归包括preauth、完整登录、本人业务读取、一次固定PG语句超时、故障后的正常读取和错误密码拒绝。它不证明30用户容量、500ms满队列拒绝、真实哈希过载恢复或不可枚举，也不证明45负载超时的全部根因已定位。根因证据不足继续OPEN。

| 请求 | HTTP或故障 | 秒 |
|---|---|---|
| preauth | 200 | 0.406 |
| login | 200 | 1.297 |
| owned_business | 200 | 0.797 |
| fixed_PG_timeout | 401 | 3.406 |
| after_fault_business | 200 | 0.797 |
| wrong_password_context | 200 | 0.406 |
| wrong_password | 401 | 0.891 |

宿主仅跑既有41项快速自检和新增4项定向回归；初次新测试因fake Lab缺少namespace而失败，原件保留，修正fixture后通过。没有重跑历史后端/Windows/Node/浏览器全套。新真实记录共855条，归档索引/hash/loopback及实际HANDLE退出已经核对。

源码与guest派生包差异明确保留：47执行后，只补了一处关闭失败时仍在finally落诊断文件的保护，正常关闭操作顺序不变；此失败分支仅通过宿主注入测试，未再启动guest目标。两版transport.py的精确hash见VERIFICATION.json，不声称全部最终源码都已在guest重跑。

[实际验证、诊断及未通过项](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-availability-20261110-01/VERIFICATION.json) · [真实guest证据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-availability-20261110-01/STAGE47-PASS.zip) · [最终只读安全审计](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-availability-20261110-01/FINAL-GUEST-AUDIT.json)。guest目录 `C:\Users\Administrator\PR03bS3Mechanisms47`，本机证据根 `J:\class-assistant\worktrees\web-pr03b-sessions\outputs\pr03b\s3-availability-20261110-01`。

原guard、隔离、降权、固定实验执行权保持；一般数据库执行权CLOSED。D01–D03设计层CLOSED，TBD-08OPEN。未改/应用0001–0006、正式认证产品/业务路由、真实D0登记或恢复；未改宿主系统、关闭/重启/恢复VM、commit、Push、合并或部署。已有dirty状态/index保留。本轮只交这份小改动及短回归证据，不把压力测试重新设为本轮开发动作。
