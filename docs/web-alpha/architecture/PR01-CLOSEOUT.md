# PR-01 · 原则批准与本地提交收尾

日期：2026-10-28（Asia/Shanghai）。仓库：`J:\class-assistant\classroom-os`。目标分支：`feature/web-alpha-0.1.0`。唯一父提交为PR-00 `f7731002d974ea29296090c366b68569fc69ba89`，本地v1.3.0基线为`43fa23c141e3350e90d72ed7768e3b8de7d68337`。

## 1. 用户批准的准确边界

用户已审阅整体交付并明确：“原则上批准本次架构设计，接受ADR中已选定的系统结构和技术栈”；同时要求“11项TBD中仍需实验或产品确认的参数继续保持开放，不得自动关闭”。此次另行授权工作区核对、静态复验及一个独立PR-01本地commit。

因此，F类系统结构/技术栈进入获批准基线；E/P参数及TBD-01至11全部保持开放。批准不是产品规则变更、运行时验收、收费/部署许可或后续实施授权。契约包仍为0.1.0，收尾没有修改机器契约内容。本次不创建后续分支/Worktree，不启动PR-02a、PR-09a或其他PR；不Push、Merge、Tag、Release或部署。

批准对应本文件首次加入仓库的独立PR-01提交。文件不写入包含自身的commit SHA，避免自引用；以下只读命令可从任何后续分支定位完整批准提交。最终向用户交付的完整SHA及实际提交文件表同时保存在忽略的`outputs/pr01/closeout-report.md`。

```powershell
git -C 'J:\class-assistant\classroom-os' log --diff-filter=A -1 --format=%H -- docs/web-alpha/architecture/PR01-CLOSEOUT.md
```

## 2. 完整提交文件清单

共28个文件：1个修改（仅AGENTS原文之后的追加段）、27个新增。除根AGENTS追加之外，PR-00已跟踪文件均不变。没有业务代码、依赖锁或真实数据进入提交。

```text
M AGENTS.md
A CLAUDE.md
A docs/web-alpha/architecture/README.md
A docs/web-alpha/architecture/01-SYSTEM.md
A docs/web-alpha/architecture/02-FRONTEND-AURORA.md
A docs/web-alpha/architecture/03-DATA-BACKEND.md
A docs/web-alpha/architecture/04-CONTRACTS-FLOWS.md
A docs/web-alpha/architecture/05-SECURITY-OFFLINE.md
A docs/web-alpha/architecture/06-COST-OPERATIONS.md
A docs/web-alpha/architecture/07-ADR-REGISTER.md
A docs/web-alpha/architecture/08-IMPLEMENTATION.md
A docs/web-alpha/architecture/09-CLAUDE-FRONTEND-HANDOFF.md
A docs/web-alpha/architecture/10-TRACEABILITY.md
A docs/web-alpha/architecture/PR01-ACCEPTANCE.md
A docs/web-alpha/architecture/PR01-CLOSEOUT.md
A docs/web-alpha/architecture/contracts/CHANGELOG.md
A docs/web-alpha/architecture/contracts/domain-model.json
A docs/web-alpha/architecture/contracts/events.schema.json
A docs/web-alpha/architecture/contracts/examples.json
A docs/web-alpha/architecture/contracts/openapi.json
A docs/web-alpha/architecture/contracts/pr-dependencies.json
A docs/web-alpha/architecture/contracts/requirements-traceability.json
A docs/web-alpha/architecture/contracts/state-machines.json
A docs/web-alpha/architecture/evidence/INDEPENDENT-REVIEW.md
A docs/web-alpha/architecture/evidence/architecture-validation.json
A docs/web-alpha/architecture/evidence/diagram-validation.json
A tools/pr01/check_diagrams.mjs
A tools/pr01/validate_architecture.py
```

## 3. 收尾验证与证据口径

核对现有文件与[验收报告](PR01-ACCEPTANCE.md)一致：118个HTTP路径/134操作/149模型、21类WS消息、34个契约正反例、五流16条守卫用例、60张逻辑表/688列/164外键、118项需求及28节点PR依赖图。前端交接、Mock/模拟ports、Claude薄入口、Aurora和跨Agent边界完整。独立只读复核没有发现新增架构阻断；02a和09a均只依赖01。

静态检查覆盖10组验证，Mermaid覆盖15张图的语法。收尾增强保护检查：显式指定完整expected HEAD，核对28文件白名单与完整性；提交后只接受PR-00的一个非merge直接子提交；仍核验149旧文件哈希、7份源资料内容哈希集合、PRD哈希、AGENTS原全文前缀，以及所有PR-00跟踪路径保护。没有放松业务/基线保护来允许本次commit。

2026-10-28收尾复验结果：10/10静态检查通过、15/15图语法通过，两个检查器均退出0；34/34契约结构样例与16/16状态守卫通过。额外传入全零的错误expected HEAD，保护检查如期拒绝并退出1，错误为`HEAD differs from explicit expected commit`；这是预期负例，不是未解决的验证失败。有限凭据签名扫描未命中私钥、AWS访问ID或长`sk-`令牌形式；此扫描不构成全面安全审计。

```powershell
# 提交前，默认expected HEAD为PR-00完整SHA；隔离依赖沿用work/pr01。
& 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools/pr01/validate_architecture.py --deps work/pr01/validation-deps
& 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools/pr01/check_diagrams.mjs
git diff --check f7731002d974ea29296090c366b68569fc69ba89
git diff --cached --check
# 提交后，使用刚核对的PR-01完整SHA，不把任意HEAD自动当成正确基点。
# python tools/pr01/validate_architecture.py --deps work/pr01/validation-deps --expected-head <PR01完整SHA> --output outputs/pr01/architecture-validation-postcommit.json
```

环境沿用Windows/PowerShell、Python3.12.14、Node24.19.0与原隔离检查依赖；无新增安装。提交前结果归档于[evidence/architecture-validation.json](evidence/architecture-validation.json)和[evidence/diagram-validation.json](evidence/diagram-validation.json)。提交后的复验输出、完整SHA、文件表及最终工作区状态写入忽略的outputs报告，避免为回填提交自身SHA再创建第二个commit。

## 4. 保留风险与未验证事项

11项TBD全部开放，逐项关闭证据、责任人及最晚关口见[ADR登记](07-ADR-REGISTER.md)：ASR协议/时长计费；模型与费用；缓存密钥安全；删除异常期限；备份恢复；容量；质量阈值；身份安全参数；具体版本与规模；隐私上线条件；视觉/无障碍与真机性能。本次没有更改任何TBD的关闭状态。

真实PostgreSQL约束/RLS/迁移/并发、worker崩溃恢复、对象存储删除与备份恢复、浏览器录音/加密缓存/E2E、供应商质量/费用/隐私条款均尚未实施验证。机器契约和状态守卫通过不等于这些机制已运行。图只做语法解析；PR-00远端CI仍待验证。旧Python/九套Node回归未因本次纯文档/工具收尾重复执行，其历史成绩不改写；旧业务与测试文件哈希保持不变。

## 5. 后续独立Worktree建议（本轮不执行）

先获对应PR实施授权，再核对分支/目标目录不存在；若已有合适工作树，先复用或核实，不覆盖。推荐保持集成仓库原位，将两个工作树放在其外部的J盘项目容器中，不放C盘，也不把工作树嵌套进仓库。

| 工作包 | 角色、分支与路径 | 实施边界 |
|---|---|---|
| PR-02a | Codex；`feature/web-pr02a-data`；`J:\class-assistant\worktrees\web-pr02a-data` | 独立Web后端数据基础、PG实体/repository/CAS/Alembic迁移；真实合成PG空库/升级/跨owner FK/血缘与版本验证；持久任务调度属02b，不扩展至其业务实现 |
| PR-09a | Claude Code；`feature/web-pr09a-aurora`；`J:\class-assistant\worktrees\web-pr09a-aurora` | `web/frontend/`开发壳、Aurora基础组件、路由/导航、Mock与模拟录音/AI、前端验证；不得提前算作09b/10业务完成 |

两者从同一PR-01批准提交分叉，09a无须等待02a实现。日常文件各自所有；共享契约/ADR变更由Astra协调一个编辑者，Claude/Codex同步确认后再更新生产者/消费者和fixtures。真实联调按[28个PR实施规则](08-IMPLEMENTATION.md)执行；Mock通过不代替后端验收。

以下是用户后续授权后的PowerShell建议，**当前收尾不执行这些命令**。执行时先将变量结果与已交付完整SHA核对；任何不一致先检查。

```powershell
$pr01Commit = git -C 'J:\class-assistant\classroom-os' log --diff-filter=A -1 --format=%H -- docs/web-alpha/architecture/PR01-CLOSEOUT.md
git -C 'J:\class-assistant\classroom-os' worktree add -b feature/web-pr02a-data 'J:\class-assistant\worktrees\web-pr02a-data' $pr01Commit
git -C 'J:\class-assistant\classroom-os' worktree add -b feature/web-pr09a-aurora 'J:\class-assistant\worktrees\web-pr09a-aurora' $pr01Commit
```

交接须记录base SHA、契约0.1.0、明确PR范围、实际工作树路径、文件所有权及验证输出。各自的依赖/缓存置于各工作树`work/<pr>/`，不要共用可写数据库或复制真实本地数据。Claude入口为根[CLAUDE.md](../../../CLAUDE.md)及[09交接](09-CLAUDE-FRONTEND-HANDOFF.md)；Codex入口为[03数据后端](03-DATA-BACKEND.md)、机器模型及PR-02a门禁。两个后续分支的创建、开发与合流均不因本文件自动获得授权。
