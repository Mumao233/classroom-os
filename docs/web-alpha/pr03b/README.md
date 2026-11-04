# PR-03b 启动包：计划、现状与依赖

日期：2026-11-05（Asia/Taipei）。状态：**准备文档已形成，供产品确认与 Astra 独立审查；认证实现未开始**。TBD-08 保持 **OPEN — PARTIALLY DECIDED**。本包不修改已批准架构或冻结契约，不表示候选政策、实验结果或风险已获批准。

## 1. 审阅入口与本轮范围

| 文件 | 审阅内容 |
|---|---|
| [POLICY-DECISIONS.md](POLICY-DECISIONS.md) | C01–C06已决定、P01–P09待批准、E01–E08实验候选；一次确认表和恢复责任登记 |
| [CONTRACT-DELTA.md](CONTRACT-DELTA.md) | 密码三字段与两个实际入口、角色/epoch差异、恢复和逐笔授权待审扩展、版本/兼容/消费者同步 |
| [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md) | Cookie、CSRF/Principal、实时撤销、MFA/恢复、WS、必要增量表/ACL/RLS和锁序 |
| [TEST-EXPERIMENT-GATES.md](TEST-EXPERIMENT-GATES.md) | 29项攻击/故障矩阵、Argon2id与候选参数实验、真实机制证据、S1–S4内部门禁 |
| [VALIDATION-AND-HANDOFF.md](VALIDATION-AND-HANDOFF.md) | 本轮实际检查、限制、文件清单与交Astra的复审指令 |

用户本轮授权：独立分支/工作树、准备文档、适用文档检查及docs-only本地提交。停止在启动包交付；不实现正式认证、不将12改6、不改正式contracts/ADR/需求矩阵/DAG、不Push或合并。私人持久缓存、真实采集/ASR、运营后台、真实收费、生产迁移与发布均不属于本轮。本包不要求用户重交已归档PRD。

## 2. 操作前核验及工作树

| 项目 | 本轮实查 |
|---|---|
| 正式Git根 | `J:\class-assistant\classroom-os`；上级仅项目容器 |
| main完整HEAD / 本地origin/main / 远端main | 均为 `b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`；使用本地rev-parse与只读`git ls-remote origin refs/heads/main`核对 |
| 正式origin fetch/push | 均为 `https://github.com/Mumao233/classroom-os.git`，唯一正式远端 |
| 历史remote | `legacy-classroom-assistant`保留历史fetch地址，push为disabled协议；不是长期同步源，本轮未访问或写入旧仓 |
| dirty与已有进度 | 正式main开始干净；相关branch/worktree/docs路径无已有PR-03b成果。全历史grep命中PR03a提及03b的提交，不是03b实现；复用现有架构/PRD/验收及专项报告 |
| 新分支/工作树 | `feature/web-pr03b-sessions`；`J:\class-assistant\worktrees\web-pr03b-sessions`；从上述精确main基线建立 |
| 创建命令 | `git worktree add -b feature/web-pr03b-sessions J:\class-assistant\worktrees\web-pr03b-sessions b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`，退出0 |
| 可控文件 | 工作树内 `work/pr03b/` 为忽略helper/中间文件；`outputs/pr03b/`为忽略证据；Git只收本目录Markdown |
| 其他工作树 | 保留main及原六个其他工作树；已有Claude工作树的未跟踪`.claude/`状态保留，不读其私有内容或清理 |

用户明确指定J盘工作树路径优先；本次以Git原生命令建立精确落点，不使用会自行选择目录的managed worktree工具。未改共享remote配置、正式main或其他PR分支。开始/结束快照及378个既有跟踪文件的原始SHA-256比较保存在 `outputs/pr03b/PREFLIGHT.json` 与 `CHECKS.json`。

## 3. 已读取的依据及状态解释

适用规则：`J:\class-assistant\AGENTS.md`及工作树根`AGENTS.md`；docs/web/backend子目录未发现更深AGENTS。已读取[Web入口](../README.md)、[测试规则](../TESTING.md)、[ADR登记](../architecture/07-ADR-REGISTER.md)、[安全](../architecture/05-SECURITY-OFFLINE.md)、[契约/流程](../architecture/04-CONTRACTS-FLOWS.md)、[数据后端](../architecture/03-DATA-BACKEND.md)、[实施映射](../architecture/08-IMPLEMENTATION.md)及全部七份contracts JSON/Changelog的相关规则。

PRD继续使用[原件归档/来源](../PRD_PROVENANCE.md)与[需求矩阵](../REQUIREMENTS_MATRIX.md)。原PDF SHA-256以[SHA256SUMS](../SHA256SUMS.txt)为准，检查原件不改字节。需求以AUTH-01/03/04为认证主体，AUTH-02邀请回归，AUTH-05/06权限撤销，AUTH-07/08保留后续运营/集成归属；不把缓存、删除、采集业务一并扩入03b。

指定专项聊天 `01a18d97-7bb7-7d81-a58c-81a090d4fa62` 本轮实际通过read_thread读取，完整历史仅一个已完成评审回合、无更多页或后续产品批准。最终报告日期2026-10-31，评审核查基线 `3b3881540519c27075119f99bb1dbff35d369e35`；最终状态 **OPEN — PARTIALLY DECIDED**。副本 `outputs/pr03b/TBD08-REVIEW-20261031.md` 供追溯，规范决定已摘录到本包，不能依赖聊天外链才能理解政策。本轮当前用户再次确认的四项规则优先，不把历史12字符初值或管理员15分钟/8小时等候选当批准值。

已实读正式入口中的 `outputs/repository-migration/20261105-migration-01/MIGRATION-RECEIPT.md`，并核对 `outputs/integration-20261104-pr03a-pr09a/INTEGRATION-RECEIPT.md` 与 SUMMARY。它们是既有本地忽略证据，不在本工作树重复生成/覆盖；路径与SHA索引保存于本轮证据。Git内迁移入口见[迁移记录](../repository-migration/README.md)。历史收据中“旧仓上传”“当时远端CI未运行”仅描述前一阶段，不改变本轮新仓origin和禁止Push的范围。

## 4. 依赖核验及当前缺口

| 项目 | 现状/依据 | 对03b的意义 |
|---|---|---|
| PR03a批准链 | 最终源SHA `54e26d997e0c1f883ef2ef340124190066f6aad1`，已经由整合 `c80fd29d7834cca040a0a037019c8ba4731ce4ec` 纳入main；本轮实际验证祖先关系 | 复用注册/邀请/激活/名额/资源owner授权，不重做03a；历史[验收快照](../pr03a/ACCEPTANCE.md)的“待精确复审”文字不单独代表当前未整合 |
| PR03a开放支持 | 0006 bootstrap-only；[P3-01 OPEN](../pr03a/remediation/checkout/P3-01-DEPLOYMENT-SUPPORT.md)；旧额外九探针8通过/1失败/exit1 | 不承诺托管/非superuser升级；选择部署数据库/迁移身份前触发原关口，本轮不执行部署或迁移 |
| identity与密码 | [用例](../../../web/backend/classroom_web/application/identity.py)与[哈希入口](../../../web/backend/classroom_web/infrastructure/identity_passwords.py)均12–1024；没有正式login/reset/change服务 | 提交差异草案；哈希64MiB/t3/p1只是已编码实验初值，不能当批准生产值 |
| Principal与UoW | [内部主体](../../../web/backend/classroom_web/domain/identity.py)、[授权事务](../../../web/backend/classroom_web/infrastructure/database.py)重核账户/role/epoch/到期；无真实HTTP session/device链 | 03b补真实认证入口与同事务实时撤销；不把构造内部值当HTTP认证，不走OwnerContext绕行 |
| 数据与传输 | 实际迁移到0006，无auth_sessions/devices/MFA/challenge实现；entrypoints仅worker；正式契约设计了HTTP/WS | 必要表/受限role/ACL/RLS、HTTP与WS真实机制均待分步交付，不把设计表/API当已运行 |
| PR09a | 源SHA `491b4d5ae2ead9d1dab944be9e5a1660f001ab1b`已纳入main，本轮验证祖先关系；仍为Mock壳 | 正式认证消费者同步交Claude；跨课堂固定实体ID P3维持原关口，Mock通过不代表真实身份联调 |
| TBD08 | 参数、恢复责任、剩余风险接受和机制实验未闭合 | 本轮可准备，正式实现及验收按S1–S4逐项放行；不提前关闭 |

历史整合的464后端、Windows525主+171子、九Node、前端52/51/36及迁移后的远端PR00 CI是各自精确SHA成绩，**本轮未重跑**。历史WinError5、后端15秒SQL timeout、Chrome崩溃与有界复验记录继续保留，不能被准备检查覆盖。

## 5. PR02b开放P3的具体关闭关口

依据[技术债登记](../pr02b/TECHNICAL-DEBT.md)：PR02B-P3-01在**该reconcile操作HTTP接入前**关闭语义缺口，本轮只写文档不触发。后续若只实现auth/session/MFA路由，也不自动触发；若为了联调顺带挂载 `reconcileTask`，必须先明确同键继续协调还是首次结果回放，交产品/契约判断与Astra审查，完成“无raw→K→迟到raw→K→K”真实PG回归。实施范围须明列启用路由，禁止靠通用路由器顺带暴露它。

PR02B-P3-02在**多Attempt工作流扩展前**处理；03b会话接入本身不扩展Attempt。本轮两项P3继续OPEN，不一概阻塞准备、不擅自关闭、不借此扩写任务系统。原raw/Receipt/Outbox/fence/unknown与发送intent COMMIT边界全部保留。

## 6. 推进次序与停止点

03b内部顺序为：政策与实验方案审定 → 正式契约审查 → 明确范围的分步实施验证 → 固定完整实现SHA的Astra独立复审。阶段证据详见测试文档S1–S4；不修改原28包DAG、不新增工作包。实验方案批准不等于运行或通过，准备文档提交不等于授权正式编码。

本轮在适用文档/链接/一致性/保护校验后建立独立docs-only本地提交，将完整SHA、文件清单、真实检查和未执行事项交付供审阅。最终SHA记录在提交外的 `outputs/pr03b/DELIVERY.md` / `COMMIT-RECEIPT.json`，避免自引用SHA。本包不冒称已经取得Astra批准；后续产品确认、契约审查与实施范围决定后再推进。任何后续Push、合并或部署仍不在本轮授权内。
