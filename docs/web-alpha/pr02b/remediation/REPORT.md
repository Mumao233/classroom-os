# PR-02b R1/R2 整改与独立复审交接

日期：2026-10-30（Asia/Shanghai）。工作树：`J:\class-assistant\worktrees\web-pr02b-tasks`；分支：`feature/web-pr02b-tasks`。

结论：**两项阻塞问题的代码整改及本地验证完成，等待 Astra 独立复审。** 原审查结论 REQUEST CHANGES 不因本地测试通过自动变为批准。本轮实现内检查不替代独立复审。

原 PR-02b 提交：`406bb9c6d943e4b30eef62e0397bfed1103e96e6`；PR-02a 基线：`a76558ac64a95ae28874eaecabb71c5a4381a7ad`。保留原始提交，新增单个非合并修复提交，不 amend/rebase/reset；完整修复 SHA 和 clean 状态在提交后写入 `outputs/pr02b/remediation/DELIVERY.md`。

输入审查报告：`outputs/pr02b/independent-review-20261030-151053/REPORT.md`。机器结果、每文件计数、源码 SHA-256 和复审证据入口见 [VALIDATION.json](VALIDATION.json)。原验收/审查证据均保留。

## 复现与事实边界

先在未经本轮修改的 406bb9c 源码上，使用原审查脚本的逐字节副本重新运行三个真实 PostgreSQL 反例，全部复现：

1. 课堂从课程 A 移到另一课程 B，仍固定引用 A 的材料；A 撤权后，省略材料字段的任务仍调用 MockProvider **1 次并 succeeded**。
2. 显式材料字段的提交事务完成旧授权读取后，撤权事务仍能先提交；再放行发送意图，MockProvider **1 次**。
3. 已存 raw 的任务恢复 queued 后取消：Task=cancelled、Attempt=submitting、raw=pending，后续领取为0、reconcile仍cancelled。

记录：`outputs/pr02b/remediation/probe-results-before.json`、`probe-before.log`；副本脚本位于 `work/pr02b/remediation/probe.py`。没有把反例中的非法调用当作真实内容外发或真实收费；所有 Provider 都是本地 Mock。

审查的 A/B 是同一 owner 下的两个课程，不是两个用户。修复保留跨 owner 的 RLS/FK/NotFound 边界，新增跨用户移动/来源拒绝测试，没有开放课堂跨租户转移。

## R1 · P1：固定来源授权与提交线性化

新增 `classroom_web/infrastructure/task_sources.py`，作为任务来源的唯一校验路径。任务创建、收据回放、get、准备 Attempt、发送意图、存储/发布结果、reconcile 和恢复，均从持久课堂读取不可变 `course_material_version_id`；无论可选 input_ref 是否携带材料 ID，都必须检查固定材料、原课程及原学期，同时检查课堂当前课程及学期。

可选 ID 仅作与服务端持久关系相等的断言，不能选择是否授权或替换材料。无课堂的通用任务不存在固定材料引用；它若携带材料/课堂输入引用仍被拒绝。授权查询仅投影 ID/revision/删除状态，不读取大纲、词表或课堂正文。

锁序固定为：

`owner（UoW）→ 全部相关学期按 UUID 排序 SHARE → 全部相关课程按 UUID 排序 SHARE → classroom SHARE → task UPDATE → attempts 按序 → results → owner event counter`

先发现完整祖先集合，再统一加锁；不会先锁当前课堂、随后才补锁原课程。锁后重读课程学期、课堂课程/材料关系并核对墓碑；关系发生变化则整笔回滚，不能对未锁的新祖先继续执行。材料不可变，避免锁材料行后再升级课程锁。原课程 SHARE 与 PR-02a 材料撤权/课程撤权使用的 UPDATE 锁冲突，原学期 SHARE 与学期撤权 UPDATE 冲突，锁持续到业务事务提交。

**发送授权的线性化点是 `mark_submitting` 所在事务成功 COMMIT。** 此前撤权已完成，则发送前被拒绝、零 Provider 调用；提交事务先取得来源锁，则撤权须等待其 COMMIT。该次已持久授权的调用可以随后发生，后来撤权不能回滚网络动作。之后保存/发布仍重新授权，已撤权结果不能发布；已发而无法确认的任务按既有 metadata-only 恢复进入 unknown，不自动重发、换 Provider、退款或释放风险。

Worker 对来源发现与加锁之间并发移动产生的 RevisionConflict 采取整笔回滚，下次扫描重新核对；只重做数据库恢复步骤，不重试已发生的外部副作用。

R1 新增 **41 项真实 PostgreSQL 测试**，包括：

- 当前课程与原课程位于不同学期，原课程/原学期/历史材料本身撤权，省略和显式引用两种输入。
- 创建拒绝时六张任务/事件/收据表均无残留；既有任务派发拒绝时 Provider=0、无新 Attempt/结果、风险标记不误设为收费。
- get、收据回放、prepare、submit、store、publish、reconcile 均复用来源检查；不能通过显式字段或旧收据绕过。
- 两连接的两种锁胜出顺序，三类撤权 × 两类输入。通过 `pg_blocking_pids` 观察实际等待后才释放先行事务，不以 sleep/超时当作锁证明。
- 撤权胜出调用0次；提交胜出只允许原授权调用1次，随后结果不能发布、恢复unknown、不能再次领取。
- 并发移动触发来源发现冲突时，首次恢复不改变状态/fence/Attempt/Outbox且调用0次；下次可复用未发送 Attempt，最终总调用1次。

没有新增预算实现或对费用账本执行释放/退款。本包尚无真实 hold/usage 账本；本轮以无提交意图、零调用、既有风险标记和事务无残留验证相关边界，不冒充真实预算结算测试。

## R2 · P2：取消分类与已存结果完成路径

取消、恢复和准备执行会锁定并检查全部历史 Attempt，而不是把 queued 等同于尚未发送；同时检查 submitted_at、Provider request/outcome、Attempt 状态、持久 raw 及单向收费风险标记。已存结果优先于后来尚未发送的计划；不会选择 newer planned 再发请求。

| 事实 | 取消后的处理 |
|---|---|
| 确知从未发送 | Task cancelled/completed；全部未发送 planned Attempt cancelled/finished；废弃旧 fence；Provider=0 |
| 已发送、结果未知 | 活跃在途任务保留 cancel_requested，未持有有效租约时 unknown；旧发送证据和风险保留，不重发、不退款、不释放预留 |
| raw 已持久、尚待本地处理 | 保留 raw、取消时间和可能收费风险；恢复队列仍 queued，供 Worker 仅做本地校验；活跃 Worker 可经 cancel_requested 继续完成 |
| 原提交已经留下 cancelled + pending raw | 经授权的显式 reconcile 可完成该结果；也可通过取消命令修复为可本地处理状态；不删除 raw/收据或创建新收费 Attempt |

`prepare_attempt` 优先复用已有结果，未发送计划被明确终止；`mark_submitting` 拒绝已有取消请求、历史发送证据或既有收费风险。发布可以完成实际保存 raw 的那个 Attempt；Task/原结果 Attempt 最终一致为 succeeded 或 failed/OUTPUT_INVALID，未使用的新计划保持 cancelled，旧 unknown 历史仍作为风险记录保留。

reconcile 只处理已有持久结果，没有 Provider 查询/新发送/新hold；不抢占仍持有效租约的 Worker。同键重复 cancel/reconcile 不重复生成状态事件、Attempt 或调用。非法 raw 仍保留 Provider 成功事实及原文，正常收敛为本地校验失败，不发修复请求。

R2 新增 **12 项真实 PostgreSQL 测试**：从未领取/恢复 planned 的安全取消、发送响应丢失后取消、raw 恢复后取消再由 Worker 或 reconcile 完成、合法/非法 raw、原 cancelled 卡死状态修复、旧 unknown + 新 planned、旧 raw + 新 planned，以及取消/重新领取两种真实竞争顺序。均断言 MockProvider 次数；已产生结果的案例先真实调用本地 Mock 一次，完成后总次数仍为1，既有结果和风险不丢失。

## 实际验证结果

最终统一后端：**293 passed / 94.03s，零失败、错误、跳过**。原240项身份全部保留，含 PR-02a 的128项；新增53项（R1=41，R2=12）。不是把293项都表述为数据库机制验证；每模块计数及测试身份差集在 VALIDATION.json。

最终原 Windows 门禁重新执行：**525 主测试 +171 子测试通过（60.05s），九套 Node 通过，27 门禁全部退出0**。真实合成 DPAPI 保留。

原测试只补齐一个 `CancellingScheduler` 包装器的 `recover_revoked` 委托，使其符合已有真实接口；原断言未删除或弱化。首次全套291通过/1失败即该接口缺失，日志完整保留。初次 mypy 两处可空局部变量注解已修复；最终 mypy **23个源文件无错误**。AST、pip check、Alembic单head/离线渲染及diff检查通过。

最终证据：

- 后端日志/JUnit/精确命令/已核验服务器身份：`outputs/pr02b/20261030T075612.863345Z/`。
- 本地原门禁：`outputs/pr02b/remediation/local-baseline-final/`；runner原目录 `work/pr00/remediation-final-20261030/`。
- 类型/AST/依赖/离线SQL：`outputs/pr02b/remediation/`。
- 全部已有0001/0002/0003迁移 **六文件物理字节与Git内容均不变**；八份冻结机器契约不变；无需新迁移或数据回填。原240项的迁移、RLS、CAS、unknown、Outbox、崩溃、升迁/失败回滚检查仍全部执行。
- v1.3.0源码、原tests/requirements及231个保护文件无变化。前端在本轮开始前已经从原报告的b134db9推进到 `60b0eaf8f8da978b541f9480b2fcaf486a4fad37`，本轮以实际开始快照核对41个前端文件；没有回退或改写前端。原保护脚本因旧硬编码HEAD失败的日志保留，未将其失败写成成功；补充的本轮快照/旧文件物理哈希校验记录见 `protected-after.json`。

主要命令（CWD为本工作树）：

```powershell
& 'J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/venv/Scripts/python.exe' -B tools/pr02b/run_tests.py --admin-url 'postgresql+psycopg://pr02a_pr02b_migrator@127.0.0.1:4981/pr02a_pr02b_synthetic'
& 'J:/class-assistant/classroom-os/work/pr00/venv/Scripts/python.exe' -B tools/pr00/run_checks.py --output-dir work/pr00/remediation-final-20261030
& 'J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/venv/Scripts/python.exe' -B work/pr02b/remediation/verify_scope.py --stage after
# mypy沿用既有隔离type-deps，缓存只写本树work/pr02b/remediation/mypy-cache
python -B -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs --cache-dir work/pr02b/remediation/mypy-cache web/backend/classroom_web tools/pr02b/run_tests.py
git diff --check
git diff --cached --check
```

环境：Web Python3.12.14、旧版Python3.10.4、PostgreSQL17.11、Node24.19.0。仅连接本树已核验的 `127.0.0.1:4981` 合成集群；测试创建并清理随机数据库/受限角色。二进制与主包重新核验，实际data_directory/listen/port/login/database核对成功。没有真实课程资料、付费模型、麦克风、用户配置或生产库。所有新增可控文件在指定J:树中。

## 遗留风险、回退与复审停止点

本轮只处理R1/R2；未实现真实Provider/预算、HTTP/WS/浏览器、身份/媒体/物理删除，也未执行远端CI、生产迁移、PG主进程断电或长时压测。提交线性化后可能已有外部副作用；后续真实适配器仍须完成PR-11a预算闸门及统一unknown保护。

已持久发送意图而在后来撤权的任务，可能继续显示unknown等待计量/Provider核对；不得重排或退款。来源发生合法并发移动时本轮选择失败关闭，再次授权后才允许未发送工作继续。任意跨对象手写事务仍可能死锁/超时，必须整笔回滚；不能因此重试外部收费。只读授权也持有来源SHARE锁，真实负载下等待/吞吐仍待后续验证。

本轮无需schema迁移。异常时停止Worker/领取，保留所有Task、Attempt、raw、receipt、Outbox和墓碑；不要降schema/清账本/重置unknown。406bb9c有已知来源授权缺陷，**不能以回退到该版本并恢复派发作为安全回退**；需要回退时先关闭相关入口/派发，保留0003数据，以新修复前滚。仅验证环境的专用PG在交付前按精确目录关闭，清理日志见 `outputs/pr02b/remediation/postgres-shutdown.json`。

复审应以 `406bb9c6d943e4b30eef62e0397bfed1103e96e6 → 修复完整SHA` 为增量；审查整个PR时以 `a76558ac64a95ae28874eaecabb71c5a4381a7ad → 修复完整SHA` 为范围。修复SHA见DELIVERY，不重写原commit。

没有Push、Merge、Tag、Release、部署或后续PR开发。提交与证据封存完成后停止，等待独立复审和用户后续决定。
