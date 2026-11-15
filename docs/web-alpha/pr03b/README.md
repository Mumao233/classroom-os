# PR-03b 启动包：计划、现状与依赖

> **2026-11-15 收尾确认：PR-03b 核心包完成。**用户明确要求同步本地 main、宣布完成并更新路线图网页。核心包与 CI 修复均已进入主线，双 CI、VMware 完整补验及原 Mock 浏览器测试通过；范围与证据见 [收尾记录](INTEGRATION-20261115.md)。人工恢复/维护 M0–M4 延后且关闭，R05 非统计通过，权限补审证据缺口保留。下方 2026-11-14 报告是前一轮历史快照，其中九文件 CI 草稿已被本次整合取代。

> **2026-11-14 当前入口：**用户确认核心包已合并至 main `2489932`，契约 0.2.0；人工恢复与维护 M0–M4 延后、保持关闭，R05 非统计通过。当前 CI、遗漏回归与权限复审状态见 [合并后集成补验](INTEGRATION-20261114.md)。下方各日期均为历史记录，不覆盖此入口及 [范围决定](SCOPE-DECISION-20261112.md)。

> **2026-11-12 接手入口：**[PR-03b 开发交接文档](J:/class-assistant/worktrees/web-pr03b-sessions/docs/web-alpha/pr03b/HANDOFF-20261112.md) 汇总已完成工作、预计实现目标、证据、R/F 状态与接手顺序。后续相关测试统一使用 VMware；下方各日期记录按原范围保留。

> **2026-11-11 R05 后续：**用户已批准同一目标公开更新 preauth、等待预算自然恢复，运行时限额与验收阈值不变。新工具及验证见 [R05 修订记录](R05-RENEWAL-20261111.md)。此前“等待审定”是历史停止点；该批准不关闭 R02/R04、R05 或整包。

> **2026-11-11 当前开发入口：**F01 独审 CLOSED；F02 已补 Cookie 丢失 403 的状态恢复及 MFA/恢复重新登录入口，作者回归 172/48/223 与真实浏览器 1 项通过，待独立复审。整包仍 REQUEST CHANGES。见 [本轮修复](F02-CONTEXT-20261111.md) 和 [当前开发状态及 R05 方法冲突](DEVELOPMENT-STATUS-20261111.md)。以下 2026-11-05/06 段落均为原阶段记录。

> **2026-11-06（America/Los_Angeles）S2 当前入口：**S1已独立APPROVE，见[S2正式契约及实现门禁](s2/README.md)。本轮获授权修改0.2.0候选及交审材料，尚待S2独审；不实施/实验/commit/Push/合并/部署。下方S1‘尚未通过’及2026-11-05段落保留历史状态，不覆盖后来的正式APPROVE S1。D01–D03保持CLOSED（设计层），TBD-08仍OPEN。

> **2026-11-06（America/Los_Angeles）S1 补充入口：**见 [S1 剩余准备包](s1-preparation-20261106/README.md)。正式独立复审已对精确 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9` 给出 APPROVE DESIGN，D01–D03 为 CLOSED（仅设计层）；S1 尚未整体通过。本轮新增技术建议、合成实验环境、验收阈值及审查交接，全部待独立审定，未执行实验或真实登记/恢复。下方 2026-11-05 内容保留为该精确提交的历史准备快照，其中“待复审”不覆盖后来取得的正式结论。正式契约及实现仍未批准，TBD-08 继续 OPEN。

日期：2026-11-05（Asia/Taipei），Astra设计整改版。被审提交e02614259b5e6483127e14f06e78cdb13a89f0c5的结论为 **REQUEST CHANGES（D01/P1、D02/P2、D03/P2）**。本稿提交逐项设计修订，发现是否关闭由Astra对新精确SHA独立复审；**S1未整体通过，正式恢复契约未批准**。产品政策不变、真实信任渠道/身份未建立登记、机制实验未执行。TBD-08保持 **OPEN — PARTIALLY DECIDED**。风险接受须补偿措施验证通过后才对10–30人邀请制Alpha生效，本轮提交不使其生效。

## 1. 审阅入口与本轮范围

| 文件 | 审阅内容 |
|---|---|
| [POLICY-DECISIONS.md](POLICY-DECISIONS.md) | 已确认产品政策、待Astra技术审查、待真实登记和未实验四类状态；不重开已决定政策 |
| [CONTRACT-DELTA.md](CONTRACT-DELTA.md) | 密码三字段与两个实际入口、角色/epoch差异、恢复和逐笔授权待审扩展、版本/兼容/消费者同步 |
| [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md) | 第5.2节D01独立安装根/维护授权，第5.3节D02四时间及状态，第5.4节D03只读status，第8.1节N01锁边界及N02后置新码 |
| [TEST-EXPERIMENT-GATES.md](TEST-EXPERIMENT-GATES.md) | A01–A46攻击矩阵、19条静态状态推演、D01–D03反例、N01/N02的S2门禁；全部非机制成绩 |
| [VALIDATION-AND-HANDOFF.md](VALIDATION-AND-HANDOFF.md) | 本轮实际检查、限制、文件清单与交Astra的复审指令 |

本轮仅修原六份准备文档、更新适用文档/结构/保护检查并创建独立本地commit，保存修前/修后证据。不改正式contracts、认证代码、测试、迁移或DAG，不登记真实凭据、不执行恢复/机制实验，不Push/合并/部署。复用原分支/树、保留前轮提交及失败证据；不重建或改写历史。Agent仅做设计与本轮文档检查，不代用户真实恢复；无需现在提供公钥、秘密或采购硬件。

最新决定及补充已写入政策表：管理员密码最低12字符、无字符组合强制、建议密码管理器生成16字符以上；MFA强制；会话15分钟/8小时且不持久Cookie；学生remember_me仅影响Cookie持久性、14/30天不变；R1学生10分钟/管理员5分钟、R2为5分钟、R3为2分钟具体操作绑定且单次使用，当前会话退出不额外拦截。采用本地弱/泄露密码名单，无有效名单暂停注册/改密/重置，已有正常登录不受影响。具体技术/契约和名单有效性规则待审。

学生由获权Admin专用命令人工恢复，管理员仅项目用户本人以独立运维身份手动专用命令恢复；不手工SQL改表、无skip_mfa/公开bootstrap/代执行。新因子确认后原执行者fresh manual complete在命令事务内切因子/全撤销/审计，浏览器无finalize权，完整重登后才恢复权限。秘密不进argv/env/终端历史/日志。硬件数量、算法与挑战/pending候选TTL不冻结；真实登记是对应能力真实启用条件，不阻塞合成材料设计和实验用例准备，本轮仍不执行实验。

| 报告项 | 本稿明确选择/修订 | 剩余审查门禁 |
|---|---|---|
| D01/P1 | 外部部署D0既有资源owner/部署IAM及独立安装批准固定验签根、K_change和指定用户映射；维护权与恢复执行权分离，变更绑定old/new hash/版本/nonce/期限；高水位只前进，错配拒绝，根重建另审 | 当前无真实部署渠道/PKI；安装流程/权限/版本切换/撤销并发须复审与后续合成证明，不从清单/新key PoP/PG认领根 |
| D02/P2 | challenge_expiry、实际server verified_at、operation_deadline、pending_flow_expiry分开；issue/manual complete各自fresh、操作绑定、单次≤2分钟，锁后重核 | 五分钟challenge/十分钟pending仍候选，不给更长写权限；不要求丢失的Web MFA；新反例未运行 |
| D03/P2 | 原稳定actor/authority按当前资格fresh重认证只读自己的case/op，不依赖旧cap/target epoch/browser；仅阶段/提交事实/非秘密标识，撤权拒，未知/PG失败结果不明 | status不消费恢复nonce/不重签/不finalize/不返回码；浏览器查询另审完整重登owner绑定协议；无盲目重做 |
| N01 | 选择独立identity内部事件/提交通知，不争用业务counter/Outbox、不新增公开WS事件 | S2完整显式/FK隐式锁图及双连接旧环反例；不降低所有user锁强度，未实测 |
| N02 | final仅废旧恢复码组，完整重登后显式生成新组；status及旧cap不领码 | S2生成/轮换/丢响应协议与客户端提示；不能沿用confirmMfa同步发码隐含假设，未实测 |

## 2. 操作前核验及工作树

| 项目 | 本轮实查 |
|---|---|
| 正式Git根 | `J:\class-assistant\classroom-os`；上级仅项目容器 |
| main完整HEAD / 本地origin/main / 远端main | 均为 `b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`；使用本地rev-parse与只读`git ls-remote origin refs/heads/main`核对 |
| 正式origin fetch/push | 均为 `https://github.com/Mumao233/classroom-os.git`，唯一正式远端 |
| 历史remote | `legacy-classroom-assistant`保留历史fetch地址，push为disabled协议；不是长期同步源，本轮未访问或写入旧仓 |
| dirty与已有进度 | 开始main及本树干净；HEAD为被审e02614259b5e6483127e14f06e78cdb13a89f0c5、前轮启动包/产品修订均保留，未发现额外待继承改动；以被审SHA为新commit唯一父 |
| 复用分支/工作树 | `feature/web-pr03b-sessions`；`J:\class-assistant\worktrees\web-pr03b-sessions`；此前从上述main基线建立 |
| 历史创建 | 前轮`git worktree add -b feature/web-pr03b-sessions ... b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`退出0；本轮未重新创建 |
| 可控文件 | 本轮helper/证据在work/pr03b/astra-design-remediation-20261105-01/、outputs/pr03b/astra-design-remediation-20261105-01/；Git只收六份既有Markdown修改 |
| 其他工作树 | 保留main及原六个其他工作树；已有Claude工作树的未跟踪`.claude/`状态保留，不读其私有内容或清理 |

未改共享remote配置、正式main或其他PR分支。开始/结束快照、六文件以外378个跟踪文件SHA-256及其他7树状态比较保存在本轮新证据目录PREFLIGHT/CHECKS；前轮证据只读保留。变更范围/父SHA与最终提交逐项核对，不能沿用前轮成绩冒充本次校验。

## 3. 已读取的依据及状态解释

适用规则：`J:\class-assistant\AGENTS.md`及工作树根`AGENTS.md`；docs/web/backend子目录未发现更深AGENTS。已读取[Web入口](../README.md)、[测试规则](../TESTING.md)、[ADR登记](../architecture/07-ADR-REGISTER.md)、[安全](../architecture/05-SECURITY-OFFLINE.md)、[契约/流程](../architecture/04-CONTRACTS-FLOWS.md)、[数据后端](../architecture/03-DATA-BACKEND.md)、[实施映射](../architecture/08-IMPLEMENTATION.md)及全部七份contracts JSON/Changelog的相关规则。

PRD继续使用[原件归档/来源](../PRD_PROVENANCE.md)与[需求矩阵](../REQUIREMENTS_MATRIX.md)。原PDF SHA-256以[SHA256SUMS](../SHA256SUMS.txt)为准，检查原件不改字节。需求以AUTH-01/03/04为认证主体，AUTH-02邀请回归，AUTH-05/06权限撤销，AUTH-07/08保留后续运营/集成归属；不把缓存、删除、采集业务一并扩入03b。

专项聊天 `01a18d97-7bb7-7d81-a58c-81a090d4fa62` 的2026-10-31报告在前轮已通过read_thread核实并归档 `outputs/pr03b/TBD08-REVIEW-20261031.md`，当时最终状态OPEN — PARTIALLY DECIDED；本轮复用而非重新声称读取。**当前对话最新六项决定及专用命令补充优先**，将历史候选中的管理员12字符/15分钟/8小时/再认证窗口/remember_me选择改为已确认，人工恢复方向按最新决定替代旧建议。Argon2id等其余候选不升格，TBD08仍OPEN。

本轮完整读取指定Astra报告J:\class-assistant\reviews\pr03b-design-20261105\outputs\reviewer\REVIEW.md，SHA-256记录于本轮INPUT-MANIFEST；原报告不修改。其精确对象e026142的REQUEST CHANGES及D01–D03/N01–N04是本轮整改依据，不将其静态32断言或历史91链接等结果计作新检查。修前六文档从被审Git blob逐字节保存于本轮before/，修后记录精确SHA及原始hash。

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

03b内部顺序仍S1技术/实验方案审定→S2正式契约/实现门禁→S3获准范围分步实施→S4实现精确SHA独审。S1未整体通过，D01–D03是否关闭由Astra决定；N01/N02具体选择仍需S2审查，不能从本稿批准恢复契约。真实D0/身份资产仅在对应能力真实启用前登记验证，合成设计/用例可用合成材料；本轮不实施/实验，不改DAG或新增包。

本轮完成适用检查后创建以e02614259b5e6483127e14f06e78cdb13a89f0c5为唯一父的新docs-only本地commit。最终完整SHA、父SHA、六文件清单、D01–D03逐项回应、N01/N02及命令结果写入本轮DELIVERY/COMMIT-RECEIPT；交接文档提供新精确SHA复审任务，不自引SHA或自批发现。交付即停止，不Push/合并，不登记、恢复或运行机制实验。
