# PR-03b：连接池关闭路径修复

2026-11-10（America/Los_Angeles）。负责人：林珈诚。本轮继续固定 S3 原型的普通开发，只做宿主合成故障注入和自检。R02/R04 仍 OPEN，R05 仍 INCONCLUSIVE，S3 未整体通过。

`FixedPool.close_pool()` 有两个可复现问题：第一个连接关闭抛错会跳过其余连接的清理；逐通道重新等待 5 秒会使总排空等待超出一次清理预算。修复前 3 项定向测试中 2 项失败，原始记录保留于本轮 BEFORE.json。

本轮修复保持先排空全部通道、再关闭连接的顺序。所有通道共用一次 5 秒排空等待预算，不继承已过期请求的截止时间。排空失败返回 `Expired(pool_close_deadline)`，归还已经取得的通道，不关闭仍可能使用中的连接，也不消费其关闭回调。该 5 秒只约束排空等待，不是全部连接关闭操作的总耗时保证。

排空成功后，即使某个连接关闭失败，也继续尝试其余连接。最后重新抛出第一个关闭错误；处于请求诊断上下文时，首个错误保留为原始原因，后续关闭错误标为次生事件。诊断只新增固定错误码，不保存异常消息或秘密。失败后连接池继续拒绝新事务。

修改文件为 `service_pool.py`、`request_diagnostics.py`、`selftest.py`，并更新本目录交接记录。未改变连接数、请求准入与操作期限、哈希成本、SQL、guard、正式契约或产品路由。

实际命令在 `J:\class-assistant\worktrees\web-pr03b-sessions` 执行，解释器为现有 PR-03a 独立环境的 Python 3.12.14：

```powershell
& 'J:\class-assistant\worktrees\web-pr03a-identity\work\pr03a\venv\Scripts\python.exe' -I -B work/pr03b/s3-pool-cleanup-20261110-01/run_checks.py focused BEFORE.json
& 'J:\class-assistant\worktrees\web-pr03a-identity\work\pr03a\venv\Scripts\python.exe' -I -B work/pr03b/s3-pool-cleanup-20261110-01/run_checks.py focused AFTER.json
& 'J:\class-assistant\worktrees\web-pr03a-identity\work\pr03a\venv\Scripts\python.exe' -I -B work/pr03b/s3-pool-cleanup-20261110-01/run_checks.py full SELFTEST.json
```

结果依次为退出码 1（2 失败/3 项，预期反例）、0（3/3）、0（63/63，零失败、错误或跳过）。新增用例覆盖多连接关闭失败后的完整清理与原始错误保留、一次等待预算及部分通道归还、已过期请求下的清理和关闭后拒绝新事务。时间预算用可控时钟和队列故障验证，未用真实数据库模拟容量成绩。

本轮数据库连接及 guest 操作为 0；未跑压力测试、真实 PG/HTTPS 回归、完整后端/Windows/Node/浏览器套件或远端 CI。最终源码尚未在真实 guest 重跑。旧候选 69 的普通功能结果和候选 67 的容量失败保持各自历史归属，不能算作本次最终源码验证。

回执及修前/修后原始记录：[本轮交付](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-pool-cleanup-20261110-01/DELIVERY.md)。本地 HEAD 仍为 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`；最终保护检查另存 PRESERVATION.json。未 stage、commit、Push、合并或部署。一般数据库执行权继续 CLOSED。
