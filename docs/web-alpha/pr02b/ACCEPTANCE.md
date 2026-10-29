# PR-02b · Persistent Task Engine 验收与审查交接

日期：2026-10-30（Asia/Shanghai）。工作树：`J:\class-assistant\worktrees\web-pr02b-tasks`；分支：`feature/web-pr02b-tasks`。

结论：**本地实现与专项/回归门禁通过，等待独立代码审查。** 这不是独立审查批准、远端 CI 或生产验收。用户本轮明确授权实现和独立本地 commit；提交后完整 SHA、父提交及 clean 状态写入 `outputs/pr02b/DELIVERY.md`，不为嵌入自身 SHA 重写提交。

基线：`a76558ac64a95ae28874eaecabb71c5a4381a7ad`。冻结契约包保持 **0.1.0**，所有八份机器契约不变；PR-02a 四个迁移文件不变。

## 交付范围与关键行为

| 已授权项目 | 实现与验收 |
|---|---|
| 持久 Task / TaskAttempt | 六张执行/操作/事件表；固定 kind/state；不可变输入；owner 复合 FK、FORCE RLS；任务内部 revision/updated_at 支撑冻结 DTO；attempt 与 raw result 的精确血缘三列延迟 FK 覆盖 INSERT/UPDATE |
| OperationReceipt | `(owner,operation,op_id)` 唯一；规范 JSON SHA-256，路径/输入同一指纹；事务 advisory lock 保护尚无收据的并发；不同体冲突；只存 task ID 和最小状态；当前授权/墓碑优先于回放；不删除旧收费意图来重新入队 |
| Transactional Outbox | 任务创建/状态变化、收据和事件同一事务；只存 ID/状态，不复制正文；事件 ID 稳定；投递 ACK 不删除历史；NOTIFY 只传事件 ID，重放完全依赖 PG |
| Worker 领取/租约/fence | 独立 scheduler 登录；SKIP LOCKED 短事务；DB 时钟租约；领取递增 fence；续租和发布验证 worker/fence/到期；过期恢复再次锁定并核对；十分钟老化避免低优先级永久饥饿 |
| 重启/崩溃恢复 | 真正 OS kill 四个子进程检查点：planned、submitting、模拟发送后、raw 落库后；新 Database 实例读取持久状态；仅未发步骤可重新安排；已存 raw 只做本地校验，无第二个 Provider 调用 |
| 用户事件序列/游标 | 每 owner counter 行锁持有至业务 COMMIT；真实 `pg_blocking_pids` 证明后序事务等待；无 BIGSERIAL/MAX 水位；HMAC opaque cursor 绑定 owner；伪造/跨 owner/保留窗口缺失均 resync；同一 REPEATABLE READ 快照读取投影和水位 |
| unknown 保护 | 发送前提交 intent；结果不明永不自动重发、换供应商、退款或新建 attempt；raw 迟到可落库但旧 fence 不能发布；显式 `reconcile_result` 仅校验已存结果，`new_submission=false`；无 raw 保持 unknown |
| 增量迁移/Repository | 新 `0003_persistent_tasks`，单一 Alembic head；0002→0003 七张原表有数据逐表保留；重复迁移无变化；真实后续 SQL 失败时 DDL/任务数据/revision 全回滚；破坏性 downgrade 拒绝 |
| 真实 PostgreSQL 验证 | PostgreSQL 17.11 独立合成集群，真实并发连接、角色、RLS、提交/回滚和进程 kill；只有本地 MockProvider，没有真实收费调用 |

相关架构门禁：DB-05、DB-08、DB-09、DB-10、DB-12、DB-14；03-DATA-BACKEND §4–7、04-CONTRACTS-FLOWS §1.2/4/6、08-IMPLEMENTATION 的 02b 行。生成计划唯一消费/预算预留属于后续 owning PR，未把这些未实现模块冒充已验收。

额外安全细节：已提交 intent 不能清除或退 planned；可能收费标记不能 true→false；unknown 不得回 queued/running；raw 不可覆盖；冲突结果保留首份 raw 和首个冲突摘要/时间，转入隔离并阻止发布。非法 JSON/重复 key 产生 OUTPUT_INVALID，保留 Provider 成功事实和原始输出，不调用模型修复。取消在确定未发送时直接完成；发送中取消不宣称零费用，成功返回仍可如实完成。

## 权限、删除与兼容边界

运行身份不能为 superuser/BYPASSRLS/表 owner；每个 UoW 设置事务局部 owner 并锁定活跃用户。独立 scheduler 只能 EXECUTE 固定函数，不能读取任务输入、课程正文、raw、收据或用户邮箱。函数 owner 是无登录、无 BYPASSRLS 的独立角色，仅有逐列队列/访问元数据及最小事件权限；固定 search_path、撤销 PUBLIC EXECUTE。

任务发送/发布重新检查用户、课堂祖先和固定材料是否撤权。撤权后过期任务通过 metadata-only 函数收敛：未发 cancelled、已发 unknown；不读取/复活正文。已有 raw 在删除/禁用后保持隔离，不由恢复过程发布。原始结果仅在撤权后到达时拒绝保存其私人文字，继续保留 sent intent/unknown；完整 usage 对账与物理清理留给 PR-11a/PR-04b。

`generation_plans`、`media_assets` 尚未落地，因此 `tasks.plan_id`、`provider_results.asset_id` 受 NULL CHECK 保护；其所属后续迁移必须先加 owner FK 再移除检查。没有凭空创建全部领域表。内部额外 revision、风险标记和冲突摘要均不修改冻结 API 字段/枚举。没有新增 HTTP/WS 路由、身份流程、ASR/AI 适配器或前端。

PR-02a 测试只将原“恰好七张表”断言扩展为“原七张 + 新六张”；原七张列/nullability、全 metadata 差异、RLS、CAS、墓碑、竞态、迁移断言全部保留。原 `tests/`、本地业务和 requirements 均未改。

## 最终结果与证据

- **后端 240 passed，零失败/错误/跳过，pytest 54.35s。** 包含 PR-02a 全部 128 项与 PR-02b 新增 112 项。精确分文件计数、命令、环境及源文件 SHA-256 在 [VALIDATION.json](VALIDATION.json)。
- 最终真实 PG/模拟 Worker 完整日志、JUnit、网络防护自检：`outputs/pr02b/20261029T224503.992628Z/`。runner 总耗时以该目录 results.json 为准。
- 实现完成后重新运行原本地完整门禁：**525 Python 主测试 + 171 子测试，九套 Node 全通过，27 门禁均退出 0**。保留 Windows 真实合成 DPAPI。证据 `outputs/pr02b/local-baseline-final/`，原 runner 目录 `work/pr00/runs/20261029T222234.392679Z/`。
- mypy：22 个源文件无错误（现有 `--check-untyped-defs` 口径，非 strict 全注解证明）；Python AST、pip check、单 head、离线 Alembic SQL、diff/staged-diff 检查通过。日志在 `outputs/pr02b/`。
- 保护检查：基线 254 文件，231 受保护文件；四个旧迁移和八个契约的 Git 内容/按基线 checkout 过滤后的物理字节一致；零范围违规。两个前端工作树 HEAD、状态和 34 个前端文件哈希保持不变。证据 `outputs/pr02b/protected-files.json`；忽略目录和私有 `.claude` 正文未读取。

主要命令（从本工作树运行）：

```powershell
& 'J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/venv/Scripts/python.exe' tools/pr02b/run_tests.py --admin-url 'postgresql+psycopg://pr02a_pr02b_migrator@127.0.0.1:4981/pr02a_pr02b_synthetic'
& 'J:/class-assistant/classroom-os/work/pr00/venv/Scripts/python.exe' tools/pr00/run_checks.py
& 'J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/venv/Scripts/python.exe' tools/pr02b/check_protected.py
# mypy dependency path is an existing isolated verification dependency, not installed into production.
$env:PYTHONPATH='J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/remediation/type-deps'
& 'J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/venv/Scripts/python.exe' -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs --cache-dir work/pr02b/mypy-cache web/backend/classroom_web tools/pr02b/run_tests.py
git diff --check
git diff --cached --check
```

环境：Windows、Web Python 3.12.14、旧版 Python 3.10.4、PG 17.11、Node 24.19.0；Web 依赖锁未变。已有解释器/PG binaries 只复用工具，所有本轮数据/缓存/脚本/输出位于指定 J: 工作树。PG 主包和八个依赖重新校验 SHA；[来源/启动/停止记录](POSTGRESQL-PROVENANCE.json)。runner 在创建测试子进程前重复核对实际 data_directory、字面 loopback、端口、监听地址、数据库和登录；复用原连接目标验证器，拒绝 PG* 环境和任意 query。Python socket guard 仅是进程级保护，native libpq 依赖严格目标验证，不宣称 OS 沙箱。

首次失败均保留：迁移 `%I` 的 psycopg 占位处理；invalid payload 的 SQL NULL/JSON null 区别；最后新增对抗测试后的 OS kill 检查点文件发布竞态及全局测试队列污染。最终采用 no-parameters SQL、SQL NULL、完整文件原子 rename、共同合成 owner teardown 修复。未跳过失败、删除断言或把失败改成 mock 成功。各失败 run 的日志仍在 `outputs/pr02b/`。

## 遗留风险与停止点

1. 仍需用户安排的独立代码审查。本轮多代理实现内复核不是该独立审查，不因测试全绿放行后续 PR。
2. 未验证真实 Provider、真实收费/hold/usage、跨网络 exactly-once、HTTP/WS/浏览器、生产数据库、远端 CI、长时压测或 PostgreSQL 主进程断电恢复。本轮 kill 的是实际 worker 子进程，持久性由真实 PG 承载。
3. 只提供模拟执行入口；真实 Provider 接入和 unknown 后新尝试必须先完成 PR-11a 预算闸门、独立重复费确认/新 hold，以及相应生成/ASR owning PR。现有代码没有这样的收费重试入口。
4. 纯结果校验是通用 JSON 结构边界；领域输出、来源 ID、学习候选发布和事实质量由 PR-06/07 补齐。任务成功不等于笔记已采用或金额已结算。
5. 租约、轮询和十分钟老化是初始工程参数，未作生产 SLA 验证。长 Provider 调用需要后续适配器定期调用受 fence 约束的 heartbeat；失去租约宁可 unknown，不以重发补偿。任意跨多对象手写事务仍可能死锁；必须整笔回滚，绝不把外部副作用纳入通用重试。
6. cursor secret 必须由服务端配置稳定提供，轮换需 resync；当前没有身份/密钥配置系统。事件保留/清理策略、真实 WS 投递及正文 hydration 由 owning PR 落地。当前 ACK 不删除事件。

完成本地 commit 后停止；没有 Push、Merge、Tag、Release、部署、真实数据迁移、真实收费调用或 PR-03a 开发。

## 回退方案

停止领取和 worker，保留所有 tasks/attempts/receipts/raw/outbox/墓碑；隔离入口，保留失败证据。兼容应用代码可回到基线 `a76558ac...`，**schema 保持 0003**；不要降级/删表/清收据，也不要把 unknown 改回 queued。旧 catalog Repository 与 0003 共存已实测。修复使用新增迁移前滚，不能编辑 0001/0002 或重写已应用 0003。

本轮专用合成 PG 在交付前按 provenance 的精确数据目录关闭，记录在 `outputs/pr02b/postgres-shutdown.json`；保留合成集群和日志供审查重现，不操作任何其他数据库或工作树。
