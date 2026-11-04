# PR-03b 启动包：计划、现状与依赖

日期：2026-11-05（Asia/Taipei），产品决定修订版。状态：**最新产品政策已确认，具体技术/契约方案待Astra独立审查；真实执行身份及凭据待登记；实验/演练未执行，认证实现未开始**。TBD-08保持 **OPEN — PARTIALLY DECIDED**。学生6字符/MFA可选的剩余风险接受须在补偿措施验证通过后才对10–30人邀请制Alpha生效，本轮文档提交不使其生效。

## 1. 审阅入口与本轮范围

| 文件 | 审阅内容 |
|---|---|
| [POLICY-DECISIONS.md](POLICY-DECISIONS.md) | 已确认产品政策、待Astra技术审查、待真实登记和未实验四类状态；不重开已决定政策 |
| [CONTRACT-DELTA.md](CONTRACT-DELTA.md) | 密码三字段与两个实际入口、角色/epoch差异、恢复和逐笔授权待审扩展、版本/兼容/消费者同步 |
| [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md) | 会话/权限/锁序与第5.1–5.4节专用恢复命令：学生核验、本人独立运维身份、受限重绑/原子撤销/秘密保护 |
| [TEST-EXPERIMENT-GATES.md](TEST-EXPERIMENT-GATES.md) | A01–A37攻击/故障矩阵、Argon2id与候选参数实验、真实机制证据、S1–S4内部门禁 |
| [VALIDATION-AND-HANDOFF.md](VALIDATION-AND-HANDOFF.md) | 本轮实际检查、限制、文件清单与交Astra的复审指令 |

用户本轮授权：复用现有PR03b工作树，同步修订六份准备文档、执行适用静态检查及创建新的独立docs-only本地commit。停止在修订包交付；不实现认证或恢复工具、不改正式contracts/认证代码/测试/ADR/需求矩阵/DAG，不Push、合并或真实恢复。Agent只能设计及在后续获准范围内合成测试工具，不能代用户执行真实管理员恢复。私人持久缓存、真实采集/ASR、运营后台、收费、生产迁移与发布不属于本轮。不要求重交PRD或在聊天中提供密码/私钥/证件。

最新决定及补充已写入政策表：管理员密码最低12字符、无字符组合强制、建议密码管理器生成16字符以上；MFA强制；会话15分钟/8小时且不持久Cookie；学生remember_me仅影响Cookie持久性、14/30天不变；R1学生10分钟/管理员5分钟、R2为5分钟、R3为2分钟具体操作绑定且单次使用，当前会话退出不额外拦截。采用本地弱/泄露密码名单，无有效名单暂停注册/改密/重置，已有正常登录不受影响。具体技术/契约和名单有效性规则待审。

学生人工恢复由管理员使用专用受控命令；管理员人工恢复仅由本项目用户本人通过独立运维身份验证后手动专用命令执行，其他管理员无权。命令须完成身份核验、权限检查、批准执行审计、受限重绑、新MFA确认后撤销旧授权并完整重登；DB更新由命令内部受审事务完成，不人工SQL/改表。秘密不进argv、终端历史或日志。本人身份拟绑定预登记个人OS主体及独立签名公钥现场证明；该方案和权限/期限/单次消费待Astra审查，真实映射和凭据未登记。

## 2. 操作前核验及工作树

| 项目 | 本轮实查 |
|---|---|
| 正式Git根 | `J:\class-assistant\classroom-os`；上级仅项目容器 |
| main完整HEAD / 本地origin/main / 远端main | 均为 `b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`；使用本地rev-parse与只读`git ls-remote origin refs/heads/main`核对 |
| 正式origin fetch/push | 均为 `https://github.com/Mumao233/classroom-os.git`，唯一正式远端 |
| 历史remote | `legacy-classroom-assistant`保留历史fetch地址，push为disabled协议；不是长期同步源，本轮未访问或写入旧仓 |
| dirty与已有进度 | 开始时main及本工作树均干净；发现完整启动包提交 `9e0a03ab013b9e8f565a0c10d171e2330130be98`，本轮以其为修订父基线，未另建重复工作树/覆盖旧证据 |
| 复用分支/工作树 | `feature/web-pr03b-sessions`；`J:\class-assistant\worktrees\web-pr03b-sessions`；此前从上述main基线建立 |
| 历史创建 | 前轮`git worktree add -b feature/web-pr03b-sessions ... b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`退出0；本轮未重新创建 |
| 可控文件 | 本轮helper/证据分别在 `work/pr03b/revision-20261105-01/`、`outputs/pr03b/revision-20261105-01/`；Git只收既有六份Markdown修改 |
| 其他工作树 | 保留main及原六个其他工作树；已有Claude工作树的未跟踪`.claude/`状态保留，不读其私有内容或清理 |

未改共享remote配置、正式main或其他PR分支。开始/结束快照、六文件以外378个跟踪文件SHA-256及其他7树状态比较保存在本轮新证据目录PREFLIGHT/CHECKS；前轮证据只读保留。变更范围/父SHA与最终提交逐项核对，不能沿用前轮成绩冒充本次校验。

## 3. 已读取的依据及状态解释

适用规则：`J:\class-assistant\AGENTS.md`及工作树根`AGENTS.md`；docs/web/backend子目录未发现更深AGENTS。已读取[Web入口](../README.md)、[测试规则](../TESTING.md)、[ADR登记](../architecture/07-ADR-REGISTER.md)、[安全](../architecture/05-SECURITY-OFFLINE.md)、[契约/流程](../architecture/04-CONTRACTS-FLOWS.md)、[数据后端](../architecture/03-DATA-BACKEND.md)、[实施映射](../architecture/08-IMPLEMENTATION.md)及全部七份contracts JSON/Changelog的相关规则。

PRD继续使用[原件归档/来源](../PRD_PROVENANCE.md)与[需求矩阵](../REQUIREMENTS_MATRIX.md)。原PDF SHA-256以[SHA256SUMS](../SHA256SUMS.txt)为准，检查原件不改字节。需求以AUTH-01/03/04为认证主体，AUTH-02邀请回归，AUTH-05/06权限撤销，AUTH-07/08保留后续运营/集成归属；不把缓存、删除、采集业务一并扩入03b。

专项聊天 `01a18d97-7bb7-7d81-a58c-81a090d4fa62` 的2026-10-31报告在前轮已通过read_thread核实并归档 `outputs/pr03b/TBD08-REVIEW-20261031.md`，当时最终状态OPEN — PARTIALLY DECIDED；本轮复用而非重新声称读取。**当前对话最新六项决定及专用命令补充优先**，将历史候选中的管理员12字符/15分钟/8小时/再认证窗口/remember_me选择改为已确认，人工恢复方向按最新决定替代旧建议。Argon2id等其余候选不升格，TBD08仍OPEN。

前轮已核实正式入口中的迁移收据 `outputs/repository-migration/20261105-migration-01/MIGRATION-RECEIPT.md` 和整合收据 `outputs/integration-20261104-pr03a-pr09a/INTEGRATION-RECEIPT.md` / SUMMARY；本轮复用其依赖结论并重新核对实际main/origin/HEAD/dirty，原证据不覆盖。Git内迁移入口见[迁移记录](../repository-migration/README.md)。历史“旧仓上传”“当时远端CI未运行”不改变当前新仓origin和禁止Push范围。

## 4. 依赖核验及当前缺口

| 项目 | 现状/依据 | 对03b的意义 |
|---|---|---|
| PR03a批准链 | 最终源SHA `54e26d997e0c1f883ef2ef340124190066f6aad1`，已经由整合 `c80fd29d7834cca040a0a037019c8ba4731ce4ec` 纳入main；本轮实际验证祖先关系 | 复用注册/邀请/激活/名额/资源owner授权，不重做03a；历史[验收快照](../pr03a/ACCEPTANCE.md)的“待精确复审”文字不单独代表当前未整合 |
| PR03a开放支持 | 0006 bootstrap-only；[P3-01 OPEN](../pr03a/remediation/checkout/P3-01-DEPLOYMENT-SUPPORT.md)；旧额外九探针8通过/1失败/exit1 | 不承诺托管/非superuser升级；选择部署数据库/迁移身份前触发原关口，本轮不执行部署或迁移 |
| identity与密码 | [用例](../../../web/backend/classroom_web/application/identity.py)与[哈希入口](../../../web/backend/classroom_web/infrastructure/identity_passwords.py)均12–1024；没有正式login/reset/change服务 | 提交差异草案；哈希64MiB/t3/p1只是已编码实验初值，不能当批准生产值 |
| Principal与UoW | [内部主体](../../../web/backend/classroom_web/domain/identity.py)、[授权事务](../../../web/backend/classroom_web/infrastructure/database.py)重核账户/role/epoch/到期；无真实HTTP session/device链 | 03b补真实认证入口与同事务实时撤销；不把构造内部值当HTTP认证，不走OwnerContext绕行 |
| 数据与传输 | 实际迁移到0006，无auth_sessions/devices/MFA/challenge实现；entrypoints仅worker；正式契约设计了HTTP/WS | 必要表/受限role/ACL/RLS、HTTP与WS真实机制均待分步交付，不把设计表/API当已运行 |
| PR09a | 源SHA `491b4d5ae2ead9d1dab944be9e5a1660f001ab1b`已纳入main，本轮验证祖先关系；仍为Mock壳 | 正式认证消费者同步交Claude；跨课堂固定实体ID P3维持原关口，Mock通过不代表真实身份联调 |
| TBD08 | 产品指定政策已确认；风险接受尚未满足补偿验证生效条件，恢复技术/真实登记/机制实验未闭合 | 按四类状态追踪并经S1–S4放行，不重开已批政策、不提前关闭 |

历史整合的464后端、Windows525主+171子、九Node、前端52/51/36及迁移后的远端PR00 CI是各自精确SHA成绩，**本轮未重跑**。历史WinError5、后端15秒SQL timeout、Chrome崩溃与有界复验记录继续保留，不能被准备检查覆盖。

## 5. PR02b开放P3的具体关闭关口

依据[技术债登记](../pr02b/TECHNICAL-DEBT.md)：PR02B-P3-01在**该reconcile操作HTTP接入前**关闭语义缺口，本轮只写文档不触发。后续若只实现auth/session/MFA路由，也不自动触发；若为了联调顺带挂载 `reconcileTask`，必须先明确同键继续协调还是首次结果回放，交产品/契约判断与Astra审查，完成“无raw→K→迟到raw→K→K”真实PG回归。实施范围须明列启用路由，禁止靠通用路由器顺带暴露它。

PR02B-P3-02在**多Attempt工作流扩展前**处理；03b会话接入本身不扩展Attempt。本轮两项P3继续OPEN，不一概阻塞准备、不擅自关闭、不借此扩写任务系统。原raw/Receipt/Outbox/fence/unknown与发送intent COMMIT边界全部保留。

## 6. 推进次序与停止点

03b内部顺序为：核对已决定政策、恢复身份登记与技术/实验方案审定 → 正式契约审查 → 明确范围的分步实施验证 → 固定完整实现SHA的Astra独立复审。阶段详见S1–S4；不改28包DAG、不新增包。真实凭据通过后续受限登记保管，不交聊天；实验方案审定不等于运行通过，产品确认不等于安全验收。

本轮完成适用检查后创建以9e0a03a完整SHA为父的新docs-only本地commit。最终SHA、六文件变更清单、命令/结果及未验事项写入 `outputs/pr03b/revision-20261105-01/DELIVERY.md` / `COMMIT-RECEIPT.json`，避免自引用SHA。本包交Astra审查的入口在交接文档，未冒称已审查通过；正式契约与明确实施授权后再推进，Push/合并/部署/真实恢复仍不在本轮范围。
