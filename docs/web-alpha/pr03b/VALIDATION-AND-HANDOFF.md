# PR-03b 产品决定修订：验证与独立审查交接

日期：2026-11-05（Asia/Taipei）。本轮只修订六份准备文档。**产品指定规则与人工恢复职责已确认；技术/契约待Astra审查，真实执行身份及凭据待登记，实验/演练未执行。** 风险接受仅在补偿措施验证通过后、仅对10–30人邀请制Alpha生效；本轮未满足该生效条件。TBD-08继续 **OPEN — PARTIALLY DECIDED**。

## 1. 基线与六文件变更清单

复用分支feature/web-pr03b-sessions、工作树J:\class-assistant\worktrees\web-pr03b-sessions，修订父基线为前轮启动包提交 **9e0a03ab013b9e8f565a0c10d171e2330130be98**。正式main、origin/main及只读远端main仍为b5f7ef97833ad0166c743a4c3f01fa7aaff99df4。开始时本树/main均clean；未另建重复分支/工作树，未覆盖原启动包证据。

| 本轮唯一允许修改的文件 | 相对前轮启动包的变化 |
|---|---|
| [README.md](README.md) | 更新四类状态，复用树及本轮证据定位、范围/停止点 |
| [POLICY-DECISIONS.md](POLICY-DECISIONS.md) | 将六项政策及专用命令补充记为已确认；保留条件风险限定，移除旧投票/不提供人工恢复备选，不擅升其他路由分级 |
| [CONTRACT-DELTA.md](CONTRACT-DELTA.md) | 区分已确认政策与待审字段/兼容；名单暂停、具体操作单次、人工恢复命令及本人身份协议单列独立审查 |
| [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md) | 补学生核验/命令权限、本人独立运维主体与签名key登记、批准/消费、受限重绑/全撤销/故障原子性、安全秘密通道及锁序 |
| [TEST-EXPERIMENT-GATES.md](TEST-EXPERIMENT-GATES.md) | 同步已确认值；保留A01–A29并增加A30–A37：条件生效、冒名/代执行/重放/匿名申请/中断/响应丢失/命令秘密等负例 |
| [VALIDATION-AND-HANDOFF.md](VALIDATION-AND-HANDOFF.md) | 以本轮实际验证/变更清单和精确SHA审查任务替换前轮交接口径 |

最终新commit的完整SHA、唯一父、六文件mode/blob/hash、diff统计、clean状态和命令结果在提交外的outputs/pr03b/revision-20261105-01/COMMIT-RECEIPT.json及DELIVERY.md，避免自引用SHA。前轮outputs/pr03b/证据只读保留；本轮helper在work/pr03b/revision-20261105-01/，不提交正式代码或测试。

## 2. 本轮检查、命令与证据边界

本轮重新执行适用检查，不把前轮88链接等计数或机制测试计划写成新结果。精确计数和退出码在本轮CHECKS、CONTRACT-CHECKS及FINAL-CHECK-COMMANDS，最终结果摘要见交付回执。

| 命令/检查 | 本轮核验内容 |
|---|---|
| git rev-parse、branch --show-current、status --porcelain=v1、remote -v、worktree list --porcelain、ls-remote origin refs/heads/main | 核对复用工作树、旧启动包完整SHA、dirty及正式origin；开始main与本树clean、HEAD正确；历史remote仍disabled push |
| python -B work/pr03b/revision-20261105-01/check_docs.py snapshot | 本轮PREFLIGHT：父基线、main/origin/远端、六文件外378跟踪文件物理SHA、其他7树HEAD/branch/status及迁移收据hash；退出0 |
| 已安装Python3.12.14 -B work/pr03b/revision-20261105-01/check_contracts.py | 复用原J盘隔离依赖，只读正式OpenAPI3.1、事件JSON Schema、唯一operationId、既有正反fixtures及五流guard；未改或生成正式契约/类型 |
| python -B work/pr03b/revision-20261105-01/check_docs.py check | 六份Markdown相对文件链接/围栏/空白/UTF-8、7契约JSON/本地引用、118需求/设计路径、28包DAG/03b门禁、原PRD hash、三密码模型12–1024不变；六文件外378文件和其他7树保护；产品状态/37矩阵行及精确六文件白名单 |
| git diff --check、git diff --cached --check、git diff --cached --name-status；本地commit后父链/文件集/status | 确认只修改六份既有Markdown，未改正式contracts/代码/测试/SQL/迁移/权限配置；新提交唯一父为前轮启动包，提交后clean |
| 内部只读交叉审校 | 文件作者之外检查政策是否误升/重开、本人运维信任来源、批准消费/原子性/秘密通道与状态一致性；结果保存在本轮INTERNAL-CROSSCHECK；不冒称Astra正式独审 |

既有架构图及DAG字节保持不变，本包无新增Mermaid，检查围栏和机器DAG而不重跑图渲染。文件链接检查不包括外部URL可用性及Markdown锚点。静态检查只证明结构、范围及保护一致性，不证明真实命令/密码/MFA/恢复安全。

本轮同组静态检查在修正文档后已全退出0：91条相对文件链接、7契约JSON及599本地引用、134个唯一operationId、34个既有正反fixtures、5流/16条guard、118项需求和28包DAG通过；A01–A37编号连续及四类产品状态断言通过。六文件外378跟踪文件SHA-256、其他7树HEAD/branch/status、正式main/origin/远端main、三密码字段12–1024和原PRD SHA保持不变。最终暂存及提交后检查的精确记录随回执保存，不以这些结构数字代表37项机制已经运行。

## 3. 提交前后的记录原则

本轮验证命令保留argv/cwd/退出码/stdout/stderr；未通过/跳过与辅助准备问题如实另存，不以外层shell最终退出0掩盖子命令失败。前轮首次提交缺作者身份/路径helper等原记录保留，不计作本轮结果。本轮一次补丁将同一路径删除再新增，被apply_patch以重复目标拒绝、未产生改动；改用单次文件更新，未改正式断言，摘要存本轮PREPARATION-ISSUES。提交沿用项目历史命令级作者身份，不改共享或全局Git配置。

产品来源是当前对话六条及补充，不把专项旧候选当新批准；真实OS主体、公钥指纹、硬件凭据/证书和学生核验材料未收集/登记，不在聊天要求秘密。Argon2id、challenge/票据/运维批准等候选参数和恢复命令流程尚未实测。实质问题先修文档再检查，不改正式业务断言或冻结文件以求通过。

## 4. 明确未执行

未实现认证或专用恢复工具，未改正式contracts/生成类型/Mock/代码/测试/数据库迁移。未运行A01–A37、Argon2id及资源压测、PG事务/ACL/RLS/nonce消费/撤销竞争、真实HTTPS Cookie/CSRF/fixation、WS握手/首消息/撤销、本地邮件捕获、身份核验/恢复命令合成演练或故障注入。仅做文档及既有契约结构检查。

未重跑原后端464、原探针、Windows525主+171子/合成DPAPI、九Node及前端typecheck/lint/contract/unit/ui/build；它们仍是实施阶段回归义务，历史成绩不计作本轮通过。未运行远端CI、连接正式数据库、读取真实课堂或配置秘密、登记/使用真实运维凭据、执行真实学生或管理员恢复、创建真实管理员、启用麦克风/ASR/收费Provider、Push、合并、部署或发布。Agent不代用户执行真实管理员恢复。

## 5. 给Astra的精确SHA独立审查任务

从本轮交付回执取得新完整SHA，以 **9e0a03ab013b9e8f565a0c10d171e2330130be98** 为唯一父核对仅六文件修改；正式main仍为b5f7ef97833ad0166c743a4c3f01fa7aaff99df4。只读审查启动包，给出准备设计结论、需修事项和保留门禁，不将其当认证实现复审。

1. 核对已确认产品：学生6字符/MFA可选、条件风险仅补偿验证通过后在10–30邀请Alpha生效；本地名单无效暂停注册/改密/重置而正常login继续；管理员12字符无组合、建议密码管理器16字符以上、15分钟/8小时/非持久Cookie；学生remember仅持久性；R1/R2/R3指定窗口及高风险操作绑定单次、当前退出不拦截。未明确的路由分级仍需审查，不重新投票已批方向。
2. 学生人工恢复是否有可执行的核验证据组合、预登记依据、获权Admin权限/MFA/再认证/批准和审计；专用受控命令不得靠角色自称、单一邮箱/邀请或课堂正文核验身份，不可用同一权限恢复Admin目标。
3. 管理员人工恢复是否严格仅项目用户本人：独立个人OS/证书主体＋预登记独立签名key的实际绑定、登记信任根/更换/撤销权限、现场批准与nonce/部署/目标/操作/版本/期限绑定；其他Admin、转交批准、普通Web会话或邮箱不得代执行。实际资产待登记，Agent只设计及后续获准的合成测试，不真实恢复。
4. 命令能否在内部受审事务完成身份/权限/核验/批准审计、受限重绑、新MFA确认后全撤销和故障原子性；无人工SQL/改表、公开bootstrap或skip_mfa。秘密不进argv、环境/终端历史、stdout/stderr、日志/APM/通用Receipt/Outbox；交付通道/响应丢失/重签/中断与最小状态查询是否严谨。
5. 三密码字段及两个入口、角色/Unicode、名单暂停和token消费、版本/Changelog/ADR/需求/fixtures/类型/Mock/兼容，以及人工恢复/逐笔授权独立契约是否完整；本轮正式三字段及实现仍12–1024，不因产品确认静默改变。
6. 普通HTTP不能构造Principal或借OwnerContext；唯一security_epoch、session/device实时校验、锁序、批准/能力/TOTP消费、撤销通知丢失仍有效；邮箱重置不清MFA/不自动登录，新因子确认前不删旧因子，恢复不读课堂/导出/运营或给Admin正文后门。
7. 原PR03a fallback/LocalReconciler/raw/Receipt/Outbox/fencing/unknown与Provider发送COMMIT边界保留；PR02b P3按原关口，不一概阻塞或擅自关闭。S1技术/登记/实验方案→S2正式契约→S3分步实施→S4实现精确SHA独审，不改DAG、不提前关闭TBD08。

以上为可交Astra的审查任务，不声称已经取得结论。正式实施须待技术/契约审查及明确实施范围；真实身份登记在受限流程完成，不向聊天交秘密。修订包交付即停止；如需撤销文档，应另审反向diff，保留提交及历史证据。
