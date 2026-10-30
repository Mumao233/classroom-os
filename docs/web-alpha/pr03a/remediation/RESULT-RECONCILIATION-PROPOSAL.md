# 已持久化结果的受限本地收尾方案

日期：2026-10-31。状态：用户已明确批准受限本地收尾入口。依据：本轮整改要求 C，以及不放宽 owner/RLS、不扩大 Scheduler 正文权限的约束。

## 已确认的兼容修复

0005 增量迁移统一普通 owner 可用性与 fallback 的布尔判定。未发送工作取消，可能发送工作 unknown，清理租约、保留风险及已有 raw。0001–0004 和原提交保持不变。该部分属于已授权 P2 修复。

## C 要求需要明确的系统处理边界

普通 owner UoW 必须继续拒绝失效主体；因此它不能在身份失效期间读取 raw 完成本地校验。原 PR-02b 撤权测试也明确检查 `recover_expired()` 后 raw 仍在、状态 pending、任务 unknown。这一隔离阶段不能被删除或削弱。

为同时满足“原测试原断言保留”与“Reconciler 随后处理已保存结果，不永久 pending”，建议独立增加本地收尾阶段：

1. `recover_expired()` 继续通过原 metadata fallback 收敛租约；不向失效账号开放正文。
2. 内部 LocalReconciler 只针对已经存在 immutable raw 的任务调用固定 SQL SECURITY DEFINER 函数。函数只返回布尔结果/最小状态，不返回输入、邮箱、密码、raw 或校验 payload。
3. 函数限定可信事务 owner、task ID/fence、无有效租约、同一 Attempt/raw 血缘；按 task→attempt→result→owner counter 锁序执行。没有网络、动态 SQL、Provider、重试收费或新 Attempt。
4. 在数据库内部进行有界的纯 JSON 结构检查；与当前 Python 校验器的重复键、非有限数、对象类型和大小规则进行对照测试。保留原始字节、digest、Provider outcome，完成 Task/Attempt/结果校验元数据和最小 Outbox。原始 OperationReceipt 不改写，保留费用未知风险。
5. Scheduler 的现有权限与表列授权逐项保持一致；它得不到 raw SELECT 或新正文接口。普通 runtime 也没有新增直接 SELECT/UPDATE 表权限；仅受限内部收尾函数有固定系统处理权。
6. 函数只处理既有结果，不对失效用户发布正文，不复活删除资源，不释放未知费用。实际 reservation/usage 账本尚未实现，不能声称真实费用已结算或已释放。

这是系统后台处理已保存结果的例外入口，不能伪装成通过了用户认证，也不能推广为通用 RLS 绕过。用户已明确批准本方案；实施及测试只覆盖这一受限边界，不扩展至 Cookie/MFA 或真实 Provider。
