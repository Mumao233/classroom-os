# PR-02a · Database Foundation 验收报告

> 2026-10-30 更新：下文保留原提交 `272d1175` 的历史本地结果。Astra 独立审查结论为 REQUEST CHANGES；本轮三项修复及最新 128 项验证见 [独立审查整改报告](review-remediation/REPORT.md)。本地修复完成后等待 Astra 复审，不代表架构审查已通过。

日期：2026-10-28（Asia/Shanghai）。工作树：`J:\class-assistant\worktrees\web-pr02a-data`。
分支：`feature/web-pr02a-data`。基线：`1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f`。
冻结契约包：0.1.0。用户已明确授权本包实现、验证与一个独立本地 commit。

**结果：PR-02a 数据基础本地验收通过。** 最终真实 PostgreSQL 测试 **40/40**，无失败、错误、跳过或警告；原 Windows 回归 **525 个主用例 + 171 个子测试**与九套 Node 全部通过。212 个基线跟踪文件逐项核验无变化。机器可读记录见 [VALIDATION.json](VALIDATION.json)，完整变更表见 [CHANGES.md](CHANGES.md)。

## 实现边界与需求映射

已阅读根 AGENTS、PRD v1.2、PR-01 架构和关闭记录、03/04/07/08、领域模型与需求矩阵。PRD 归档 SHA-256 已重新核对；复用既有 PR-01 的可读提取，重点核对 p13–17 的数据、删除、版本和课程关系。未修改原 PRD、11 项开放 TBD 或冻结机器契约。

| 范围 | 本次产物与不变量 | 需求/设计 |
|---|---|---|
| 独立数据基础 | Python 3.12 / SQLAlchemy 2 / psycopg 3 / PostgreSQL 17；独立依赖和版本，不导入本地应用 | DATA-01、ADR02/03 |
| 7 张必需表 | users、semesters、courses、course_material_versions、class_sessions、deletion_requests、deletion_tombstones | DATA-01/02、03 §3/7、08 PR02a |
| 关系和身份 | 服务端 UUIDv4；owner 复合外键/唯一键；同名独立 ID；日期检查；归档字段；改归属不换 ID | UI-COURSE-01/02/05/08/12 |
| Repository/UoW | 纯领域 records/ports；显式 owner 事务、活动主体行锁；NotFound/Gone/RevisionConflict；事务成功一次提交，异常回滚 | 03 §4、04 §1/2 |
| owner 防线 | 私有表 FORCE RLS；事务级 owner；非 owner/非 superuser/非 BYPASSRLS 运行角色；Admin 无跨 owner 内容读取能力 | DATA-01、ADR03/05 |
| 材料与 CAS | 新课同时建空材料 v1；材料不可变；材料 CAS 与元数据 CAS 独立；延迟同课程指针约束；课堂固定材料快照 | DATA-02、UI-COURSE-09、DB-02/03/04 |
| 墓碑基础 | 最小不可变墓碑+匹配请求+资源撤权在同事务；精确删除版本；已删祖先下的对象不能移动逃逸或被迟到写入复活 | SYNC-02 的基础、ADR10、DB-13 |
| 初始迁移 | 一条 Alembic 单链与独立字面 SQL 快照；空库升级/重复升级/真实失败回滚；拒绝破坏性 downgrade | DB-14、08 PR02a |

`users` 仅提供后续身份模块的完整字段基础及窄主体检查，不实现注册/邀请/会话。
`class_sessions.adopted_transcript_version_id` 保留字段并临时 CHECK 为 NULL，等待其所属后续迁移加入 owner/class 血缘 FK；未提前创建转写表。`scheduled_class_ref` 按 Alpha 约束为 NULL。

墓碑 Repository 是内部撤权原语，接收已由调用方独立持久化的日志序号；本次测试用明确合成序号。独立删除日志、物理清理、备份恢复、账号注销和删除期限仍属 PR-04b。没有把 DB 原语冒充完整删除接受流程，也没有自行关闭相关政策 TBD。

PR-02b 的 tasks/attempts/outbox/operation_receipts/event counters、完整同步冲突稿保留、HTTP/API、前端、ASR/AI 均未实现。本次未修改 `web/frontend/`、任何基线业务/测试/requirements、v1.3.0 标识或行为。

## 实际环境、命令和证据

Windows / PowerShell。Web：Python **3.12.14**，SQLAlchemy **2.0.46**，Alembic **1.18.4**，psycopg / binary **3.3.3**，pytest **9.2.0**。原回归使用既有隔离 Python **3.10.4** 环境，保留 Windows 真实合成 DPAPI 测试；Node **24.19.0**。

数据库为原生 Windows **PostgreSQL 17.11**，仅监听 `127.0.0.1:4979`，数据目录 `work/pr02a/pg/data`，未安装系统服务。最初 EDB ZIP 下载过慢后停止，未执行残缺包；改用 conda-forge 原生包及 8 个依赖，按来源元数据逐项核验 SHA-256，见 [下载来源与校验](POSTGRESQL-PROVENANCE.json) 和 VALIDATION 的 PostgreSQL 主包记录。仅创建合成数据库；测试每轮随机创建独立数据库和受限登录，结束精确清除。临时 loopback trust 不作为部署身份方案。

以下命令均在本工作树执行，退出码均为 **0**：

```powershell
& work/pr02a/venv/Scripts/python.exe tools/pr02a/run_tests.py --admin-url 'postgresql+psycopg://pr02a_migrator@127.0.0.1:4979/pr02a_synthetic'
& 'J:/class-assistant/classroom-os/work/pr00/venv/Scripts/python.exe' tools/pr00/run_checks.py
& work/pr02a/venv/Scripts/python.exe tools/pr02a/check_protected.py
& work/pr02a/venv/Scripts/python.exe -m pip check
git diff --check
```

另外对新增 16 个 Python 文件执行 AST 解析；Alembic 离线 SQL 渲染确认 JSON 默认值正确并包含 COMMIT。安装与可配置缓存、临时数据、输出均在本 J: 工作树；旧回归 runner 强制使用的 `work/pr00/runs/` 原样保留，其结果文件已复制至 `outputs/pr02a/local-baseline/`。

| 检查 | 实际结果 | 证据位置（工作树相对） |
|---|---|---|
| 最终真实 PG | 40 passed，11.38s；零失败/错误/跳过/警告 | `outputs/pr02a/20261028T100259.971569Z/{pytest.log,pytest.xml,results.json}` |
| Windows Python | 525 主用例 + 171 子测试，零失败/错误/跳过 | `outputs/pr02a/local-baseline/{pytest.log,pytest-junit.xml}` |
| 九套 Node | audio、ai-settings、input-limits、study-ui、study-cache、review-render、review-ui、review-mount、portable-reader 全通过 | `outputs/pr02a/local-baseline/node-*.log` |
| 原回归 runner | 27 项检查全部退出 0；含防护自检、AST/JS语法、PowerShell 只解析 | `work/pr00/runs/20261028T095804.972718Z/results.json` |
| 基线保护 | 212 个原跟踪文件内容一致；PRD 校验一致 | `outputs/pr02a/protected-files.json` |
| 独立复核 | URL 覆盖、材料墓碑、旧祖先逃逸、删后指针血缘、port 签名五项均修复并复核 | 对应源码及集成负例；本报告记录 |

40 项真实 PG 用例包含：空库→head与重复迁移；表字段/元数据一致性；运行角色权限/FORCE RLS；跨 owner 复合 FK（直接验证 SQLSTATE 23503）；Admin/空 owner/禁用用户拒绝；连接池提交和回滚后的 owner 清理；课程创建原子性；材料不可变/同课指针/课堂快照；保历史改归属；元数据和材料的双连接 CAS 各仅一胜者；墓碑优先、父级撤权、迟到更新竞态；直接 SQL 逃逸拒绝；错误材料指针即使随后删除仍拒绝；墓碑请求血缘；错误迁移的 DDL/数据/版本全回滚（SQLSTATE 22012）；拒绝破坏性降级。

## 失败记录与修复

首次 PG 运行退出 **1**、26 个 setup error，未进入应用用例。原因是 SQLAlchemy 将 JSON 默认值中的冒号解释为绑定参数，生成/执行了错误快照。已改为字面 JSON 默认值、在线 raw driver SQL 和离线转义；首次失败 JUnit 保留于 `outputs/pr02a/pg-first.xml`，随后 30、37、最终 40 项真实运行均通过。没有跳过失败、删除断言或用内存替代 PostgreSQL。

独立复核发现环境变量可能覆盖显式 Alembic URL，已调整为 Connection > 显式 Config URL > 占位配置下的环境变量；测试加入错误环境目标陷阱。还补强了材料墓碑读取拒绝、旧祖先撤权后不能改归属复活、删除后的材料指针血缘，以及领域端口签名。相关修复均在最终 40 项验收之前完成。

## 兼容、恢复与停止点

这是首个 Web schema，没有既有已部署 Web 运行程序可实际退回；未声称验证不存在的旧 Web 应用。初始 schema/数据能被当前 Repository 重复读取，失败的后续合成迁移保留之前 schema、数据和版本。后续 PR 应新增 expand 迁移，保留本基线读写兼容窗口。

代码回退保留 schema/学习数据/删除账本；`downgrade base` 明确拒绝删除数据。初始或后续迁移失败时依赖 PostgreSQL 事务回滚，核查 Alembic 版本后修正并前滚。测试数据库不含正式资料，不执行生产迁移。临时 PG 在交付结束时关闭；数据库文件和复现证据留在忽略目录，运行方法见 [后端说明](../../../web/backend/README.md)。

未运行远端 CI、HTTP/浏览器 E2E、真实认证/麦克风/供应商、对象存储或备份恢复；本报告不将这些能力标记完成。没有 Push、Merge、部署、远端 PR、Tag/Release 或自行开始 PR-02b。全部 11 项 TBD 继续开放。

此报告归入本次独立本地 commit；包含自身的 SHA 不回填到跟踪文件以避免第二次提交。最终 SHA、完整文件表和提交后状态另存 `outputs/pr02a/DELIVERY.md` 并在对话交付。
