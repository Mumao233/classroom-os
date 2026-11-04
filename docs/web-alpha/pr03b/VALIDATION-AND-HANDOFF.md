# PR-03b Astra D01–D03整改与精确SHA设计复审交接

日期：2026-11-05（Asia/Taipei）。本轮docs-only修订原六份准备文档，依据Astra对e02614259b5e6483127e14f06e78cdb13a89f0c5的REQUEST CHANGES。**本稿提交设计回应，D01–D03是否关闭由Astra复审；S1未整体通过，正式恢复契约未批准。** 产品政策不变；风险接受仍须补偿措施验证通过后才对10–30人邀请制Alpha生效。TBD-08保持 **OPEN — PARTIALLY DECIDED**。

## 1. 精确对象与操作前核验

复用feature/web-pr03b-sessions及J:\class-assistant\worktrees\web-pr03b-sessions，开始HEAD精确为被审e02614259b5e6483127e14f06e78cdb13a89f0c5，工作树/main均clean。正式main、origin/main及只读远端main仍为b5f7ef97833ad0166c743a4c3f01fa7aaff99df4；origin fetch/push均为classroom-os唯一正式远端，历史旧仓push仍disabled。本轮未重建分支/工作树、未改写历史，保留9e0a03a及e026142两轮提交与全部旧失败证据。

指定报告J:\class-assistant\reviews\pr03b-design-20261105\outputs\reviewer\REVIEW.md已完整读取；原报告SHA-256为671c490e72e0bf7ca698bdfe0719b64a5b6314de03a07d68e66f50527c0db77f。原报告不改动；副本和六份修前Git blob保存在outputs/pr03b/astra-design-remediation-20261105-01/的ASTRA-REVIEW-INPUT.md、before/及INPUT-MANIFEST.json。

## 2. 六文件修改清单与逐项回应

| 唯一允许修改的文件 | 本轮实质变化 |
|---|---|
| [README.md](README.md) | 更新被审状态/父SHA/证据目录；D01–D03及N01/N02回应索引，真实启用与合成准备区分 |
| [POLICY-DECISIONS.md](POLICY-DECISIONS.md) | 保留产品规则，明确独立根/维护执行分离、四时间及两分钟证明、status独立授权、N01/N02与N03/N04门禁 |
| [CONTRACT-DELTA.md](CONTRACT-DELTA.md) | 安装/登记变更协议、issue/complete四时间、独立status权限/响应、通知和恢复码方案列入S2；不改正式contracts |
| [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md) | 重写信任起点、状态/期限/权限、只读status；锁序和FK隐式锁、N01独立内部事件、N02新码后置及真实登记条件 |
| [TEST-EXPERIMENT-GATES.md](TEST-EXPERIMENT-GATES.md) | 保留并同步A01–A37，新增A38–A46及19条静态状态推演，覆盖全部报告反例；未运行 |
| [VALIDATION-AND-HANDOFF.md](VALIDATION-AND-HANDOFF.md) | 本轮父SHA/修前修后证据、逐项回应、实际检查范围和Astra复审任务 |

| 报告项 | 具体设计回应 | 后续验证/决定 |
|---|---|---|
| D01/P1 | D0外部既存资源owner/部署IAM授权及独立安装批准先固定清单验签根、K_change、指定项目用户映射；维护与恢复执行权分离；新key PoP非授权；变更签名含deployment/authority/action/old_hash/new_hash/版本/nonce/期限；外部高水位+清单/PG只前进、错配拒绝，登记撤销与恢复共享串行边界 | 当前没有已建立的真实部署渠道/PKI，列明假设和首次安装/真实启用条件；根未登记/全失不可执行，重建另审；部署root完全控制不由应用层抵御；待Astra新SHA复审 |
| D02/P2 | challenge_expiry、服务端实际verified_at、operation_deadline、pending_flow_expiry分开；issue/manual complete各重新验证、具体操作绑定、单次且≤2分钟，锁后再检查；独立恢复用新运维证明，无需丢失的Web MFA | 5分钟challenge/10分钟pending只是候选；覆盖四分钟旧验证/近挑战到期/锁等跨两分钟/有效pending的fresh complete/旧complete重放；无产品例外、未实验 |
| D03/P2 | control-plane status仅原稳定actor/authority按当前资格fresh重新认证只读本人case/op；不依赖旧cap/target epoch/browser，撤权拒；最小提交事实/阶段/op/audit标识；未知或PG故障结果不明 | 不消费恢复nonce/cap、不重签/切因子/finalize/返回码；not_committed须有一致读证据且不授权盲目重做；浏览器另审完整重登owner绑定协议；未实验 |
| N01 | 明确选独立identity内部事件/提交通知，不共享业务counter/Outbox、不新增公开WS事件；事件领取提交释放后再另事务查user，不反向持锁 | S2审完整显式/FK/唯一锁图及A45双连接反例；现有identity_events仅激活须最小增量，不改旧迁移，不笼统降user锁；未实测 |
| N02 | final仅废弃旧恢复码组，不生成新组；完整重登后另行显式生成/轮换新组 | S2审新组操作及一次秘密响应丢失；旧cap/CLI/status不领码，不复用confirmMfa同步响应；未实测 |
| N03/N04 | 硬件数量/算法/候选TTL不冻结；真实根/人/凭据登记仅对应能力真实启用条件 | 可用合成身份材料准备设计/实验用例，不要求现在交真实key/秘密或采购；本轮也不执行机制实验 |

开发者不将上表“回应”写成Astra已关闭发现，不以静态推演代替PG/浏览器/命令演练，不标记S1整体通过或批准正式恢复契约。

## 3. 本轮适用检查及证据

中间helper在work/pr03b/astra-design-remediation-20261105-01/；证据在同名outputs目录，旧轮次只读保留。所有最终检查保留argv/cwd/退出码/stdout/stderr，修前/修后hash、diff和提交收据一并保存。最终精确计数和结果见CHECKS、CONTRACT-CHECKS、FINAL-CHECK-COMMANDS及DELIVERY，不把报告的91链接/32断言或前轮成绩当本轮结果。

| 命令或操作 | 本轮作用 |
|---|---|
| git rev-parse、branch/status/remote/worktree list及ls-remote origin refs/heads/main | 核对被审HEAD/dirty/已有进度/正式main与origin，未写旧仓或其他树 |
| python -B work/pr03b/astra-design-remediation-20261105-01/save_before.py | 从精确e026142 Git blob保存六份修前文档及报告原始副本/hash，退出0 |
| python -B work/pr03b/astra-design-remediation-20261105-01/check_docs.py snapshot | 新PREFLIGHT记录六文件外378个跟踪文件原始SHA、main/origin/远端、其他7树HEAD/branch/status及迁移收据，退出0 |
| python -B work/pr03b/astra-design-remediation-20261105-01/check_docs.py check | 六份Markdown文件链接/围栏/空白/UTF-8、7份JSON/本地引用、118需求/设计路径、28包DAG/TBD门禁、三密码模型12–1024/原PRD、保护文件/其他树、精确六M范围、A01–A46连续及D01–D03/N01/N02状态断言；修前证据/report hash不变 |
| 已安装Python3.12.14 -B work/pr03b/astra-design-remediation-20261105-01/check_contracts.py | 复用J盘既有隔离依赖，只读OpenAPI3.1、事件JSON Schema、唯一operationId、34既有正反fixtures及五流16个guard；这是结构检查，不执行认证 |
| git diff --check、git diff --cached --check、精确git add及提交前后父链/文件集/status | 只修改六份既有Markdown；新commit唯一父为e026142，未改代码/正式契约/测试/迁移；最后clean |
| 内部只读交叉审查 | 分别核对独立根/D0假设/权限分离、时间/状态查询、N01/N02和跨文件一致性；不是Astra正式独立复审 |

本稿无新增Mermaid，既有图和DAG文件字节不变；检查围栏/机器DAG而不重跑图渲染。相对链接只检查文件存在，不检查Markdown锚点或外部URL可用性。原审查报告及其审查副本不改动。辅助准备失败若发生如实存PREPARATION-ISSUES及原日志，不改正式断言刷绿。

本轮同组静态检查已实际全退出0：91条相对文件链接、7份JSON/599个本地引用、134个唯一operationId、34个既有正反fixtures、5流/16条guard、118需求和28包DAG通过结构检查；A01–A46编号连续及D01–D03/N01/N02状态标记有效。六文件外378个跟踪文件、其他7树HEAD/branch/status、main/origin/远端main、三密码模型12–1024及原PRD SHA未变；修前六文档和Astra输入报告hash保全。最终暂存/提交后同组记录另随回执保存；46条矩阵及19条状态推演仍未执行。

## 4. 本轮未执行和保留边界

未改正式contracts、认证代码、测试/生成类型/Mock或迁移；未实现恢复命令、建立真实部署PKI、登记实际身份/凭据或运行恢复。未跑A01–A46及19条交错、真实PG锁/ACL/RLS/FK/nonce竞争、HTTPS/WS/邮件、Argon2id或机制实验/恢复演练。方案选择、状态推演和结构检查不构成机制通过。

未机械重跑历史后端464、Windows525主+171子/DPAPI、九Node或相关前端回归，保留它们在相应实施阶段的回归义务和原失败证据；未运行远端CI、连接正式数据库、读取课堂/秘密、真实Provider/收费/麦克风、Push、合并、部署或发布。PR03a bootstrap-only/P3-01、PR02b两项P3及原unknown风险/账本/COMMIT发送授权边界不变；注销/重置/恢复不能重发Provider或清风险。

## 5. 交Astra新精确SHA独立设计复审

新完整SHA、唯一父e02614259b5e6483127e14f06e78cdb13a89f0c5、六文件清单与mode/blob/hash、diff统计、实际检查和未验项写入提交外的本轮COMMIT-RECEIPT.json / DELIVERY.md，避免自引用SHA。请以新精确SHA对被审父作六文件差异审查，不把本稿当实现复审。

1. 对D01逐项核实首次安装的既存外部部署授权、实际指定用户映射、验签根来源、根/登记维护和恢复执行隔离、变更签名字段/新key PoP边界、版本高水位/错配拒绝/撤销串行化及根丢失；判断假设/真实启用条件是否充分，不要求当前交真实key。
2. 对D02核实四类时间的计算/验证时点、issue和manual complete各fresh单次≤2分钟、锁后重核、独立证明无需已失Web MFA；逐条推演四分钟延迟、近challenge到期、锁跨120秒、旧批准已用但pending有效的新complete、旧complete重放。
3. 对D03核实原稳定actor/authority当前资格的独立只读status：本人case/op、cap消费与target epoch变化不影响、执行者撤权拒、未知/PG故障结果不明、重复零恢复写；不得顺带领码/reissue/finalize，浏览器旧cap无权。
4. 对N01/N02判断独立identity通知方案和完整锁图/双连接S2门禁、final废旧码/重登后显式新组及秘密丢响应协议；不笼统降锁、不新增公开WS事件、不把本轮列方案等同实测。
5. 核对产品政策、条件风险、隐私/无后门/无人工SQL/无代执行/秘密通道、任务不变量及P3具体关口不变；六文档状态机/权限/期限/契约差异/矩阵/阶段门禁一致。确认N03/N04适度设计，真实登记不挡纯合成准备。
6. 给出D01–D03逐项独立结论、剩余设计项和下一阶段边界；S1未整体通过、恢复契约未批准、TBD-08继续OPEN，不能把该稿批准自动变成实施、真实启用、Push或合并授权。

此为复审交接入口，不声称Astra已复审新提交。交付后停止，不继续实施或实验；若需撤销文档先另审反向diff，保留前轮提交和全部证据。
