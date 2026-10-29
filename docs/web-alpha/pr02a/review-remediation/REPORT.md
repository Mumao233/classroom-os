# PR-02a 独立审查整改与复审交接

日期：2026-10-30（Asia/Shanghai）。工作树：`J:\class-assistant\worktrees\web-pr02a-data`；分支 `feature/web-pr02a-data`。

Astra 对原提交的结论为 **REQUEST CHANGES**。本轮仅修复所列 1 项 P1 和 2 项 P2；本地验证完成，**等待用户安排 Astra 复审**，不把本地测试通过写成架构审查批准。

原始提交：`272d1175b1919f76905861c0490a5253a25148d3`。
架构基线：`1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f`。契约包仍为 0.1.0。
原提交保留；本轮在其后创建一个非合并修复提交，不 amend、不重写历史。包含本报告的修复 SHA 在提交后写入 `outputs/pr02a/remediation/DELIVERY.md`，并随对话交付。

## A. 三项修复及位置

| 发现 | 修复 | 文件 |
|---|---|---|
| P1 测试目标可被覆盖 | runner 与直接 pytest fixture 共用纯验证器。只允许 `postgresql+psycopg`、字面 `127.0.0.1`、显式 1–65535 且非 5432 端口、合法 `pr02a_` 数据库与用户名标识。拒绝全部 URL query、控制字符、conninfo/URI 注入、DNS/多主机/Unix socket、全部大小写变体 `PG*` 环境变量。未提供密码时用合成占位值，避免默认 pgpass 查找；代码持有的连接选项仅 timeout/sslmode。拒绝发生在 engine/连接/迁移/subprocess 之前。初始迁移直接复用已验证 engine 的连接，消除第二次目标解析。 | [统一验证器](../../../../web/backend/classroom_web/test_support.py)、[fixture](../../../../web/backend/tests/integration/conftest.py)、[runner](../../../../tools/pr02a/run_tests.py) |
| P2 材料撤销竞态 | 材料墓碑 BEFORE INSERT 通过不可变材料 ID 找到所属课程，按 owner→semester SHARE→course **FOR UPDATE**→request SHARE 协调。指针 UPDATE 已持有同一课程行的排他锁，两个事务在检查已完成到 COMMIT 的窗口内仍不能穿过彼此。不锁材料行，不先取得材料/课程共享锁再升级；锁定课程后核对学期关系，若被改归属则失败关闭。Repository 保持在追加版本之前锁住课程的规则。原延迟约束继续保留。 | [DDL 源](../../../../web/backend/classroom_web/infrastructure/ddl.py)、[增量迁移 SQL](../../../../web/backend/migrations/versions/0002_pr02a_review.sql)、[Repository](../../../../web/backend/classroom_web/infrastructure/repositories.py) |
| P2 学期长度不符契约 | 创建与更新共用 100/30 常量；数据库增加 `char_length(name) BETWEEN 1 AND 100` 和 `academic_year IS NULL OR char_length(academic_year) <= 30`。Unicode 按 code point 计数，创建、更新、直接 SQL 一致；学年仍可 null。未更改 OpenAPI。 | [常量](../../../../web/backend/classroom_web/domain/policies.py)、[Repository](../../../../web/backend/classroom_web/infrastructure/repositories.py)、[metadata](../../../../web/backend/classroom_web/infrastructure/schema.py)、[0002](../../../../web/backend/migrations/versions/0002_pr02a_review.sql) |

原 `0001_data_foundation.py/.sql` **逐字节保留**，因此已经应用的初始 schema 也能通过新 `0002_pr02a_review` 得到修复；新空库升级 head 会依次执行两个版本。DDL 生成源的墓碑函数与 0002 字面 SQL 一致，静态检查会核对；不是只改初始 SQL、让既有库漏掉修复。没有增加逻辑表或后续 PR 功能。

类型检查还揭示了两个原有注解不一致：学期更新值字典被错误推断为仅 str、Repository port 的泛化 kwargs 不符合实际签名。本轮明确了字典/端口类型，没有改变运行时 API 或放宽检查配置来掩盖错误。

## B. 原问题复现与验证

**P1：安全的连接前验证。** 修改前用被替换为断言哨兵的 engine/subprocess 工厂运行真实入口：fixture 的 query `hostaddr` 与 `PGHOSTADDR`、runner 的 `PGHOSTADDR` 均到达本不应到达的动作边界。实际数据库连接和实际子进程均为 **0**；没有把恶意目标拿去“试连”。见 [修改前记录](p1-pre-fix-probe.json)。修复后 55 个纯用例对两个真实入口分别检查 URL query、驱动/主机/端口/名称和 libpq 环境变体，并断言 engine、connect、upgrade、subprocess 都未调用。

**P2 竞态：真实 PG 原提交复现，未预设结论。** 在 PostgreSQL 17.11、原 SQL SHA-256 `dd8065aa02c121e74a29207bdf15ad1a6599aacdd988e573faefb5202f67198c` 和非特权 runtime login 下执行：

1. 普通时序：T1 未提交地把 current=M1；T2 墓碑 M1 并先提交；T1 随后执行延迟检查，正确拒绝 `23514`。这个时序本身不能证明漏洞。
2. 可确定复现：T1 更新 current=M1，然后 `SET CONSTRAINTS check_current_material IMMEDIATE` 完成检查但仍未提交；T2 看到已提交指针 M2，插入 M1 墓碑并提交；T1 提交。最终 live course 指向已撤销 M1。
3. 真正的延迟 COMMIT 窗口：只在合成库添加测试专用末尾延迟触发器，在生产检查已执行后用 advisory gate 暂停 T1 的 COMMIT；T2 完成自身原有检查并提交，再放行 T1。双方均提交，复现同一异常。生产约束未关闭、替换或放宽。

完整实际 SQL、事务时序、结果与独立测试库/角色清理记录见 [复现说明](repro-material-race-272d117.md) 和 [结构化证据](repro-material-race-272d117.json)。修复后的两个 actor 顺序通过 `pg_blocking_pids` **观察到真正的课程锁等待后**才释放先行事务，不以 sleep/超时假装证明互斥。后行者精确返回 `23514`，无 `40P01` 或锁超时；失败请求/墓碑一并回滚。正常同事务替换再撤销、追加新版本与历史撤销也通过。

**P2 长度：** [修改前 Repository 记录](semester-original-limits.json) 明确使用被替换的持久化边界验证原方法接受 101/31，不冒充 PG。新增真实迁移测试另外在原 0001 中直接写入 101/31，证明旧 DB 接受；0002 随后拒绝并完整回滚，原值、所有表、函数和 schema revision 均保留，没有截断。有效原库升级保持七表数据不变。

## C. 新增测试和完整结果

全部原 40 项保留。新增 **88 项**；最终 **128 passed，17.56s**，零失败、错误、跳过、警告。其中 **72 项真实 PostgreSQL**，**56 项纯验证/契约**。

| 测试文件 | 项数 | 覆盖 |
|---|---:|---|
| [原集成测试](../../../../web/backend/tests/integration/test_data_foundation.py) | 40 | 原外键、RLS、CAS、墓碑、迁移与回退门禁 |
| [目标校验负例](../../../../web/backend/tests/unit/test_test_database_target.py) | 55 | 两个入口的连接前拒绝、错误脱敏、有效 canonical URL/子进程环境 |
| [材料竞态](../../../../web/backend/tests/integration/test_material_revocation_race.py) | 4 | 两种先锁顺序、正常替换撤销、追加与历史撤销无退化 |
| [学期长度](../../../../web/backend/tests/integration/test_semester_limits.py) | 26 | 创建/更新 100/101、30/31；直接 SQL；中文/非 BMP/组合字符；null；冻结契约 |
| [已应用迁移](../../../../web/backend/tests/integration/test_migration_remediation.py) | 3 | 有效 0001→0002 保数据；旧 101 与 31 值的真实失败回退 |

Windows 旧版完整 runner 本轮重新执行：**525 主用例 + 171 子测试**，零失败/错误/跳过，真实合成 DPAPI 保留；九套 Node 全通过；runner 的 27 项门禁全部退出 0。没有用本轮前历史结果代替复测。

环境：Windows / Python 3.12.14（Web）、3.10.4（旧版）、PostgreSQL 17.11 / Node 24.19.0；Web 运行依赖锁未改。mypy 1.19.1 单独装在忽略的 `work/pr02a/remediation/type-deps/`，仅为本轮检查使用。

实际命令（本工作树执行）：

```powershell
& work/pr02a/venv/Scripts/python.exe tools/pr02a/run_tests.py --admin-url 'postgresql+psycopg://pr02a_migrator@127.0.0.1:4979/pr02a_synthetic'
& 'J:/class-assistant/classroom-os/work/pr00/venv/Scripts/python.exe' tools/pr00/run_checks.py
$env:PYTHONPATH = (Resolve-Path work/pr02a/remediation/type-deps).Path
& work/pr02a/venv/Scripts/python.exe -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs --cache-dir work/pr02a/remediation/mypy-cache web/backend/classroom_web tools/pr02a/run_tests.py
& work/pr02a/venv/Scripts/python.exe tools/pr02a/check_remediation.py
& work/pr02a/venv/Scripts/python.exe tools/pr02a/check_protected.py
& work/pr02a/venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

最终均退出 **0**。mypy 覆盖 12 个生产/runner 源文件（不是 strict 全注解证明）；AST 覆盖 23 个 Python 文件。离线 Alembic head SQL 完整渲染；仅有一个 head `0002_pr02a_review`；原 0001 校验一致；DDL/迁移函数同步、100/30 一致。有限秘密模式检查无命中，不当成全面安全认证。首次 mypy 两项错误及修复后成功日志分别保留 `mypy-before-fix.log`、`mypy-final.log`。

证据索引：

- [本轮机器可读结果](VALIDATION.json)。
- Web 原始日志/JUnit/命令：`outputs/pr02a/20261029T184939.304210Z/`。
- 旧版原始结果：`work/pr00/runs/20261029T183208.162672Z/`，交付副本 `outputs/pr02a/remediation/local-baseline/`。
- 类型、静态、离线 SQL 和前端前后快照：`outputs/pr02a/remediation/`。

## D. 提交与保护范围

原始 SHA 保留为 `272d1175b1919f76905861c0490a5253a25148d3`。新增修复 commit 是其单个直接子提交；完整 SHA 和提交后 clean 状态随 `outputs/pr02a/remediation/DELIVERY.md` 提供。

212 个 PR-01 基线文件内容全部一致；冻结 OpenAPI/domain/event 等契约未修改，v1.3.0 业务、原 tests/requirements 未修改。Claude 前端工作树 `web-pr09a-aurora` 的 HEAD、状态和 34 个前端文件内容哈希前后相同；旧视觉工作树 HEAD/状态也相同，原有未跟踪 `.claude/` 保留。没有在这些工作树编辑、清理、提交或运行安装。

## E. 剩余风险与回退

- 本地修复验证不是 Astra 复审通过；未运行远端 CI、真实用户、HTTP/浏览器、ASR/付费、部署或生产迁移。
- 新版测试入口有意拒绝 `localhost`、非 `pr02a_` 登录名和任何 `PG*` 环境变量。应使用独立合成角色及显式参数，不能通过放松验证器复用正式数据库。
- 0002 校验既有学期长度。发现旧超长数据时整个迁移回滚到 0001，数据原样保留；停止放行，核对后另获授权处理，不能截断、跳过 CHECK 或把迁移失败标记为 head。
- 课程行锁防止本次并发异常新增，不自动恢复旧 schema 已经写下的错误材料指针/已撤销历史。没有生产库验收承诺；任何实际升级需另行授权，检查原有一致性，不能自动复活被撤销材料。
- 这四个并发用例证明相关同课程竞争的锁行为与正常路径，不宣称任意跨对象手写长事务绝无死锁。PostgreSQL 死锁/超时必须整笔回滚；不得因此重试任何外部副作用。本包没有新增外部 I/O。
- 兼容代码回退保留 **0002 schema 与全部数据/墓碑**；不降回含竞态的 0001，不恢复宽松测试目标校验。破坏性 downgrade 继续明确拒绝。遇到问题先停用受影响入口，保留合成失败证据；以新的修复迁移前滚，不删除账本。

## F. Astra 准确复审范围与停止点

本轮增量为 **原始 `272d1175...` 到单个修复 commit**；完整修改/新增文件见 [变更表](CHANGES.md)。若审阅整个 PR-02a，范围为架构基线 `1c3ef02e...` 到修复 commit，共两个非合并本地提交。使用交付 SHA 固定 diff，避免后续 HEAD 漂移：

```text
git diff 272d1175b1919f76905861c0490a5253a25148d3 <修复完整SHA>
git log --oneline 272d1175b1919f76905861c0490a5253a25148d3..<修复完整SHA>
```

没有 PR-02b、PR-03 或其他功能；没有 Push、Merge、Tag、Release、部署或自动联系其他用户聊天。完成本地交付后停止，等待用户安排 Astra 复审。
