# PR-03b 准备阶段验证与独立审查交接

日期：2026-11-05（Asia/Taipei）。范围：docs-only启动包。本轮未实施认证，未获得产品政策批准或Astra正式独审结论，TBD-08继续 **OPEN — PARTIALLY DECIDED**。

## 1. 精确基线与交付文件

正式main、origin/main与只读远端main核查基线：`b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`。独立分支 `feature/web-pr03b-sessions`；工作树 `J:\class-assistant\worktrees\web-pr03b-sessions`。本地提交只新增以下六份Markdown：

1. [README.md](README.md)：启动范围、实查依赖与现状、P3关口、停止点。
2. [POLICY-DECISIONS.md](POLICY-DECISIONS.md)：已决定/待批准/实验候选、一次确认表。
3. [CONTRACT-DELTA.md](CONTRACT-DELTA.md)：三密码字段与两入口、版本/追踪/消费者/兼容草案。
4. [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md)：会话/恢复/权限/增量迁移和锁序。
5. [TEST-EXPERIMENT-GATES.md](TEST-EXPERIMENT-GATES.md)：真实机制攻击矩阵、合成实验与分阶段门禁。
6. [VALIDATION-AND-HANDOFF.md](VALIDATION-AND-HANDOFF.md)：本文件。

提交完成后的完整HEAD、唯一父SHA、逐文件Git blob/文件SHA-256、最终状态和检查报告摘要保存在 `outputs/pr03b/COMMIT-RECEIPT.json` 及 `outputs/pr03b/DELIVERY.md`；不将待生成或自引用SHA写入Git文档。输出为本地忽略证据，不声称已上传GitHub。受控临时helper仅在 `work/pr03b/`。

## 2. 本轮验证范围与命令

文档改动不运行旧阶段硬编码分支/提交白名单的全量PR01/PR03a runner，不修改其断言来容纳新包。使用独立只读helper逐项比较当前精确基线，并复用未修改契约的结构检查；不把这些检查称运行时安全验收。

| 实际命令/操作 | 验证目的与证据 |
|---|---|
| `git rev-parse --show-toplevel`、`branch --show-current`、`rev-parse HEAD/main/origin/main`、`remote -v`、`status --porcelain=v1`、`worktree list --porcelain`、相关branch/path/history查找、`ls-remote origin refs/heads/main` | 开始身份/dirty/重复成果/远端核验；创建前未发现03b冲突。工作树创建命令见README，退出0 |
| `python -B work/pr03b/check_docs.py snapshot` | 建立PREFLIGHT：378既有跟踪文件SHA-256、main/origin/远端、7个其他工作树HEAD/branch/status、迁移收据hash。最终成功退出0；首次helper路径转义问题见下节 |
| `C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B work/pr03b/check_contracts.py` | 实际Python3.12.14，复用原J盘隔离validation-deps，只读OpenAPI3.1验证、事件JSON Schema、唯一operationId、既有正反fixtures、五流状态机guard；`outputs/pr03b/CONTRACT-CHECKS.json` |
| `python -B work/pr03b/check_docs.py check` | 新Markdown相对文件链接/围栏/行尾/UTF-8、全部7份JSON及本地引用、118需求ID及设计路径、28包DAG、03a→03b/TBD08不变、三字段12–1024不变、原PRD SHA、378既有文件及其他7树保护、docs-only白名单、diff whitespace；`outputs/pr03b/CHECKS.json` |
| `git diff --check`、`git diff --cached --check`、明确六文件`git add`及`git diff --cached --name-status` | 检查工作区和最终暂存区、精确docs-only范围；本地提交后复核提交父链/改动类型/文件集与clean状态 |
| 内部交叉审校 | 独立子任务读取实际代码及政策/契约/设计交叉比较，发现并修正历史无邮箱owner shell边界及会话活动刷新共享锁升级问题；这不是Astra正式独审，不代产品确认 |

本轮实际结果：两项helper均退出0；6份新文档的88条相对文件链接、围栏/空白/UTF-8检查通过；7份JSON可解析，OpenAPI的578及事件schema的21个本地引用可解析；134个operationId唯一、34个既有正反fixture、5个状态机/16条guard case通过结构验证。118项需求映射、28包DAG及原PRD SHA通过；378个既有跟踪文件SHA-256、其他7个工作树HEAD/branch/status、main/origin/远端main和冻结三字段12–1024均未变。正式暂存及提交后再次核对的准确结果见交付回执；以上只证明静态结构与保护一致性。

本轮文档没有新增Mermaid；既有架构图/机器DAG字节保持不变，检查围栏和DAG结构，不重跑图渲染。外部URL可用性与Markdown锚点不在本轮自动链接校验范围。

## 3. 辅助问题及修正口径

本轮辅助准备问题不冒称产品测试失败或通过：read_thread第一次有效读取后，追加请求曾超出工具的20000字符限制而被拒，按允许上限重读成功，确认仅一个最终评审回合；初版snapshot使用Git默认quotePath转义中文文件名导致路径错误，helper改为命令级 `-c core.quotepath=false`后成功，不改Git配置/文件；一次从main路径尝试读取尚只存在新工作树的测试草案未找到，随后从正确工作树读取；一次PowerShell下向rg传字面`*.md`路径出错，未用于任何成功结论。

子任务自查首次矩阵计数helper正则转义错误，改为明确行前缀计数后通过；一次“diff后搜索子AGENTS”的组合命令因rg无匹配整体退出1，diff自身无错误，未把该搜索退出1说成门禁成功。上述问题的摘要另存 `outputs/pr03b/PREPARATION-ISSUES.md`，不放松正式代码、测试、冻结契约或安全检查。

## 4. 明确未执行

未运行Argon2id性能/资源实验、多进程压测、限流/票据/challenge参数实验、真实PG事务/ACL/RLS/迁移/恢复码/撤销竞争、HTTPS浏览器Cookie/CSRF/fixation、真实WS握手/首消息/撤销、本地邮件捕获器与恢复演练。测试文档中的预期、覆盖与候选均不是已通过证据。

本轮未重跑原后端464、原探针、Windows525主+171子/合成DPAPI、九Node、相关前端typecheck/lint/contract/unit/ui/build；这些回归在实施阶段仍须按影响范围执行且不可被Mock替代。历史成绩及原失败仅作为依赖背景；未运行远端CI。未连接正式数据库、读取真实课堂/配置秘密、启用麦克风/真实ASR/收费Provider、创建真实管理员、运行生产迁移、Push、合并、部署或发布。

## 5. 给Astra的精确审查任务

取得交付回执中的完整docs-only SHA，以其唯一父SHA为上表main基线，只审这六文件；先核对diff与冻结contracts/实现/迁移无变化，再独立检查政策边界、恢复责任和技术提案。重点结论应逐项给出：

1. 已确认学生6字符/14天/30天/可选MFA和管理员强制MFA原则是否保留，风险接受是否仍由产品决定。
2. 管理员密码/期限/窗口、人工恢复与唯一管理员批准链、remember_me及实验方案是否足以供产品一次确认；未指定人员和环境的项目继续OPEN。
3. 三密码字段及两个实际入口、角色差异、Unicode计数、版本/Changelog/ADR/追踪/fixtures/类型/Mock/兼容，以及受限恢复/逐笔授权新增协议的独立契约审查边界。
4. 普通HTTP不可伪造Principal/选择OwnerContext，唯一security_epoch、session/device实时授权及锁序是否可实施；授权检查与撤销、活动touch、TOTP/恢复码消费是否同事务安全。
5. 邮箱重置不清MFA/不自动登录、恢复无正文/运营授权、新因子确认前旧因子保留、无公开bootstrap/skip_mfa、秘密不重放/不入通用收据日志，WS未认证无资源/音频及通知丢失仍由PG阻断。
6. 原PR03a fallback/LocalReconciler/raw/Receipt/Outbox/fencing/unknown与发送COMMIT边界是否保留；PR02b P3按具体触发关口而非一概阻塞或提前关闭。
7. S1政策与实验方案→S2正式契约→S3分步实施验证→S4实现精确SHA独审的03b内部门禁；给出准备包结论及尚缺决定，**不要将文档批准当认证实现批准或关闭TBD08**。

此文件是供用户转交/在独立审查聊天引用的审查入口，本轮未使用消息工具向其他聊天或人员发送。正式实现必须在产品决定、正式契约审查及明确实施范围后另行推进。本轮停止在启动包本地提交交付；回退仅需保留分支或另审docs-only反向diff，无数据库/运行服务需要回退。
