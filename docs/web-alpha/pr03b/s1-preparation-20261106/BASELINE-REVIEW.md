# Classroom OS PR-03b 设计整改独立复审

日期：2026-11-05（Asia/Taipei）。主审承担独立架构/安全复审职责；三个只读子审分别交叉核对信任链、时间授权、status 与通知/秘密交付。最终结论由主审完整阅读六份当前文档、累计设计及原报告后作出。

**APPROVE DESIGN — 仅批准精确提交 `e4853223847b3c26bfc74fa8fb2271cdb10e18b9` 对原 D01–D03 的设计回应。D01/P1、D02/P2、D03/P2 均 CLOSED（设计层）。**

未发现新增 P0/P1/P2 设计阻塞项；N01/N02 的方案选择和后续门禁充分明确，继续保留为 S2 正式契约及实施验收条件。没有要求修改本次精确 SHA 才能取得该设计结论。

**S1 尚未整体通过。** 本结论允许的精确下一阶段是完成 S1 剩余技术/实验方案、环境及成功阈值审定；S1 剩余条件完成后，才可按另行授权提交 S2 正式契约审查与实现门禁定义。当前正式 contracts、机制实验、实现、真实 D0/身份登记、恢复启用均未获本报告批准。本次复审结束即停止。

已确认产品政策不重新投票。条件风险接受仍须补偿措施验证通过后才对 10–30 人邀请制 Alpha 生效；本次审查不使其生效。**TBD-08 保持 OPEN — PARTIALLY DECIDED**，所有既有 P3 保持各自关闭关口。

## 1. 精确对象、独立副本和保护证据

| 项目 | 本次独立核验 |
|---|---|
| 正式 PR 工作树 | `J:\class-assistant\worktrees\web-pr03b-sessions`；分支 `feature/web-pr03b-sessions`；开始及独立检查时 clean |
| 精确 HEAD | `e4853223847b3c26bfc74fa8fb2271cdb10e18b9` |
| 唯一直接父 | `e02614259b5e6483127e14f06e78cdb13a89f0c5` |
| 父链 | 精确对象 → e026142… → `9e0a03ab013b9e8f565a0c10d171e2330130be98` → 指定 main 基线 |
| main 保护基线 | `b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`；正式 main HEAD、本地 main/origin/main 均吻合且 main clean；本轮未访问远端，不声称再次验证了实时远端 main |
| 独立副本 | `J:\class-assistant\reviews\pr03b-design-rereview-20261105`；本轮新建 `clone --local --no-hardlinks --no-checkout`，detached checkout 精确 SHA，独立 `.git`；未向正式仓库注册 worktree |
| 相对唯一父 | 仅原六份准备文档 M，mode 均保持 100644；262 插入、110 删除 |
| 相对 main 基线 | 累计仅新增同六份文档 A，共 876 行；无其余新增、删除或 mode/blob 变化 |
| 其余基线文件 | **378/378 mode、对象类型、blob 逐项相同**，包括正式 contracts、认证/任务代码、测试、SQL/0001–0006 迁移、历史跟踪证据及本地版文件 |
| 实际审阅字节 | 六文档的独立副本字节 = 正式工作树字节 = 精确 Git blob；逐项 SHA-256 已保存 |
| 历史整合依据 | 本轮验证 PR03a `54e26d9…` 与 PR09a `491b4d5…` 均为指定 main 祖先；不从旧验收快照的“待复审”字样推断当前整合状态 |

六份当前文档均完整阅读，既读相对父变化，也核对其相对 main 的累计设计；没有只用开发者回应表作判断。

| 文件（`docs/web-alpha/pr03b/`） | 精确被审 blob |
|---|---|
| CONTRACT-DELTA.md | `d5995ed9288db8c44886fa44a9cb1fbb6208d848` |
| POLICY-DECISIONS.md | `7e1929a960273d89666765cbd6b0dc9849f53f85` |
| README.md | `473016809991543f984551add2222c2de4e163a0` |
| SESSION-RECOVERY-DESIGN.md | `ac5b2c6d735d1095d9d7398862bf7cae599c51a9` |
| TEST-EXPERIMENT-GATES.md | `e674b882eea114ded75ac274235db8476b543175` |
| VALIDATION-AND-HANDOFF.md | `084143a0b542afb4a5cab1c9a0d7896267065ea3` |

下文 SRD=SESSION-RECOVERY-DESIGN.md，POL=POLICY-DECISIONS.md，CD=CONTRACT-DELTA.md，TEST=TEST-EXPERIMENT-GATES.md，HANDOFF=VALIDATION-AND-HANDOFF.md；行号均来自上述精确提交。

原报告只读核对，SHA-256 为 `671c490e72e0bf7ca698bdfe0719b64a5b6314de03a07d68e66f50527c0db77f`，与交接一致。DELIVERY 作为开发者收据读取并留 hash，不将其陈述替代独立保护核验或机制证据。

## 2. D01/P1 — CLOSED：独立授权起点已在设计中闭合

主要依据：SRD 73–93、194–198；POL 102–108；CD 53–55；TEST A38–A40（67–69）、静态推演 85–87。

授权链现在可按 Alpha 规模实现：外部既存资源归属/平台 IAM 或受控部署边界 → 项目用户经该渠道明确批准的安装记录 → 具名维护者独立人工核对指定项目用户 → 固定 deployment、authority、个人 OS 主体、恢复公钥指纹及根/变更凭据用途 → 恢复命令只验证既存登记。SRD 75–77 明确安装人、批准人、既存授权 ID、人工核对方式和根指纹进入外部审计，不再由待批准清单授予自身真实性。

| 原反例/检查项 | 当前约束与判断 |
|---|---|
| 提交者同时提供根、自签清单、PG active_version | SRD 75、77、82–87 拒绝此链；新应用 Admin、普通 DB 登录、新 key PoP 都不授部署维护权。三者自洽不证明首次授权 |
| 新 key PoP 冒充“就是项目用户”或变更批准 | 独立核对实际项目用户及其外部批准先行；PoP 仅证明 key 控制。R0、K_change、K_recover 分用途，恢复权限无改根/自授登记权 |
| 偷换 deployment/authority/动作/旧新状态 | SRD 87 规范签名至少绑定 deployment、authority、action、old/new hash 与 version、OS/key 摘要、nonce、签发/到期、维护者及外部批准引用；当前资格、nonce、期限锁后重核 |
| 同时恢复旧 PG 备份和旧签名清单 | SRD 89 要求匹配独立 D0 当前高水位，缺失高水位不得当新环境初始化；等版本只容同 hash，撤销以更高墓碑推进。仅两者匹配不足以放行 |
| 发布中断、清单与 PG 错配 | 先由 D0 外部账本接受原批准变更/预留 nonce 与新版本；错配期间拒绝；只沿原批准前滚，不能退旧清单恢复可用 |
| 撤销与 issue/complete 竞争 | SRD 91、194–198 共同 deployment_guard/authority 有序串行边界，维护排他、恢复共享；PG 控制状态提交是变更线性化点。先持锁的恢复可以先提交；撤销先提交则旧资格拒绝 |
| 根未登记、全失或外部批准不可验证 | 保持不可执行，信任重建另审，无自带根/新 key/可写 PG 临时认领 |

**D0 仍未实际建立。** CLOSED 的理由是本版明确了既存授权来源、首次安装责任、实际人绑定、用途分离及安全拒绝规则；不只是增加“D0”一词，也没有把“谁有权首次授权”整体推迟到 S2。固定公钥 pin 加受保护的外部部署审计可满足小规模 Alpha 的设计方向，不要求现在提供真实公钥、采购硬件或另建 PKI。

必须保留的 S2/启用条件：选定具体受信平台路径，交根/config ACL、维护者当前资格验证、规范签名格式、独立高水位真实性/新鲜度、防 PG 备份回滚、nonce 预留/中断前滚及撤销竞争协议并验证。旧签名快照或可随 PG 回滚的状态不能冒充“当前独立高水位”。D0/root 及验证器完整性是显式信任边界，完整部署接管超出应用层保证；普通 DB 权限不等于该边界。真实启用前仍需实际建立 D0、安装仪式、本人映射及受限登记、演练和启用批准。

## 3. D02/P2 — CLOSED：四时间和两次 fresh 写证明一致

主要依据：SRD 97–130；POL 63、71–80、94、108；CD 57、65–67；TEST A33/A36/A41/A42 及 81、88–92、160–161。

| 时间 | 本版可验收语义 |
|---|---|
| challenge_expiry | 服务端给等待签名/校验的截止；全部身份、因子、签名/绑定检查成功时必须仍早于该截止 |
| verified_at | 服务端实际完成本次全部校验成功时的 DB 时间；不接受客户端时间、last_seen、挑战创建时间或旧批准时间 |
| operation_deadline | 对 issue、manual complete 分别固定，至多 verified_at+120 秒，并取当前资格及既有 pending 等更早截止；证明绑定具体操作且单次 |
| pending_flow_expiry | 受限重绑流程的存续期，不提供该时段内持续写授权，也不自动续期 |

issue 与 complete 各有新 proof/nonce，绑定 deployment、actor/authority、case、action、目标 role/epoch、计划、接收端及 flow；complete 另绑定新因子 ID/确认摘要。取得**全部**必要锁后用实际 `clock_timestamp` 严格检查 `now < operation_deadline`、当前权限/撤销及 flow；不能冻结入队时间或刷新旧 verified_at。

以下为本轮重新进行的静态时间线推演，未执行机制：

| 时间线 | 独立推导 |
|---|---|
| t0 验证成功，t+4 分钟才 issue 或 complete | 超过本次最多 120 秒期限，拒绝；挑战/pending 尚活着不改变结果；不消费、不切因子 |
| 5 分钟候选挑战在 4:59 完成全部服务端验证 | 可从真实 verified_at 派生至多 2 分钟证明，再受当前资格/pending 更早截止；已消费挑战后续到期不重新缩短该窗口 |
| 5:00 或以后才完成校验 | 等于/超过 challenge expiry，拒绝，不能派生新操作证明 |
| 验证后等锁，最后一把锁取得时已跨 120 秒 | 锁后拒绝，无 nonce/cap 消费、因子切换或延期；不能以请求开始或第一把锁时间放行 |
| issue 已消费且其证明/挑战到期，pending 仍有效并已确认新因子 | 原执行者按当前资格取得新的 complete proof/completion_nonce 后可完成；不复用或二次消费原 issue nonce |
| 重放旧 complete、两个 complete 并发 | nonce/cap/flow/epoch 锁内单次消费，至多一次 final 和一次 epoch 增加；旧证明不能改接收端 |

五分钟挑战和十分钟 pending 仍是候选 TTL，无更长高风险授权例外。独立管理员恢复验证新的运维身份，不要求已丢失的 Web MFA；学生恢复执行 Admin 仍验证其自身当前 Admin/MFA/专用权限。跨文件未发现相反表达。

## 4. D03/P2 — CLOSED：结果不明后的查询有独立当前授权

主要依据：SRD 134–150、194–198；POL 110–112；CD 58–59、67；TEST A43/A44（72–73）及 94、97–101。

status 由受信服务取得原 case 的稳定 actor/authority，按 stored_executor_type/id 过滤本人执行的 case/op。学生恢复执行者须有当前自身会话、Admin/MFA/专用权限；项目用户须有当前独立 OS/签名/authority 登记。只读 proof 单独绑定 status、case/op 及最多两分钟期限，可在有效期内重复读，不是恢复写 nonce。target epoch 提升、cap 已消费或旧浏览器失效不阻断这条重新认证路径。项目用户同时是 target 时使用独立 authority，不要求失效的 Web 会话；当前执行权限/authority/key 撤销仍拒绝。

查询前和返回前在同一受限读事务重核资格，与撤销通过共同共享锁/一致读边界串行化。读取 final 同事务写入的最小 op 提交事实及 case 状态，status 锁顺序不取 target 因子/旧 epoch 或业务锁。

| 情形 | 可返回的事实/必须行为 |
|---|---|
| final 已提交、CLI 丢响应、cap 已用/target epoch 已升 | 当前合格原执行者可取得 committed/completed 及必要非秘密 op/audit 标识 |
| final 未提交且 pending 有效 | 仅在本人 case/op 共享锁及一致读足以证明该 op 在读取线性化点未提交时返回 not_committed；释放锁后事实可能变化 |
| 正在提交、查不到、PG 故障、证据不足 | unknown/结果不明；不能把缺行或超时当“失败” |
| 撤权先提交 / status 先持有效共享锁 | 前者拒绝；后者读该线性化点事实，随后撤销生效，不承诺倒转已完成的读 |
| 其他 Admin、错误 authority、仅知道 case/op、旧浏览器 cap | 拒绝或通用不可授权结果；不泄露他人 case 是否存在、target 资料或秘密 |
| 重复 status | 零恢复流程、nonce/cap、factor/epoch 变更；最小鉴权/限流日志不包含秘密 |

status 不消费 issue/complete nonce 或 cap，不重签、切因子、finalize、触发恢复通知或返回恢复码/seed/raw。not_committed **不授权重做**；重新执行/重签须另获 fresh、单次、≤2 分钟的写证明，锁内重读当前 case/flow/op，completed 拒绝重新签发和再次切换。浏览器终态查询若需要，仍须另审完整重登 owner 绑定协议；旧 cap 没有该权限。

## 5. N01/N02：后续门禁充分明确，继续 OPEN 于对应阶段

| 项 | 本次判断 | 必须保留的后续证据 |
|---|---|---|
| N01 独立 identity 内部通知 | SRD 194–208 选择安全控制面独立事件/提交通知，不争用业务 counter/Outbox，不新增公开 WS 事件。生产者沿 user/authority/case→identity event；保留 users FK 时只能作用于已按序持锁的 user。领取只锁 event 并提交释放，再另事务查当前授权/关闭连接，未重新引入 event→user/counter 反序 | S2 交生产、claim、delivery、清理/重试全路径的显式锁、FK/UNIQUE/trigger 隐式锁和最小 ACL/表增量。A19/A25/A28/A45 必须以已有 counter、真实双连接 barrier 证明无原锁环、审计/事件原子、有界完成、无 Provider 重发；不笼统降低 user 锁强度 |
| N02 final 只废旧码 | SRD 118–120，POL 94/108/171，CD 60，TEST A35/A36/A46 一致：final 不产生/交付新组，完整密码＋新 MFA 重登并具所需近期证明后才显式生成/轮换；CLI/status/旧 cap 均不能领秘密 | S2 交独立生成操作、尚无新组提示、一次展示/no-store/摘要持久化、SECRET_ALREADY_ISSUED 与响应丢失后的显式轮换规则及客户端兼容；不能沿用 confirmMfa→RecoveryCodes 作为 manual final 响应 |

基线 SQL 只读核对支持 N01 原反例：`0006_local_reconciler_functions.sql:183–192` 先锁 owner counter 再插业务 Outbox，`0003_persistent_tasks.sql:180` 的 owner FK 取得 user 隐式锁；`0004_identity.sql:96–105` 的既有 identity_events 只支持 account_activated 且 `(user_id,kind)` 唯一。本版没有假称旧表已能重复承载撤销；要求最小兼容增量并保留旧迁移字节。

静态反例：L 持业务 counter 并等待 user；安全事务 S 持 user 但只写独立 identity event，不再请求 L 的 counter，因此该特定等待环缺少 S→counter 边。此推导不是实际无死锁证明，新的 FK、唯一约束、触发器及 claim/delivery 仍必须接受完整 S2 锁图和 S3 实测。

## 6. 跨文件一致性与新增问题

| 核对面 | 独立结果 |
|---|---|
| 产品政策/权限 | 学生 6 字符/MFA 可选、Admin 12/MFA 强制、已定会话/Cookie、R1/R2/R3、名单故障边界未重开；普通 Admin 不自动有学生恢复专用权，且不能恢复 Admin |
| 状态机/执行责任 | unverified→verified→pending→pending_confirmed→原执行者 manual complete→completed；无浏览器/后台自动 final、无其他 Admin/Agent 代执行；管理员只限项目用户本人独立运维身份 |
| D01–D03 与 CD | 安装授权/四时间/独立 status 与 CD-03、POL、README/HANDOFF 一致；正式 wire/状态/错误版本仍待 S2，不偷改 authenticated 语义 |
| A01–A46 与 19 行状态推演 | 逐项静态阅读，D01 反例对应 A38–A40，D02 对应 A41/A42，D03 对应 A43/A44，N01/N02 对应 A45/A46，并与原 A19/A25/A28/A33–A37 一致；无把编号连续当机制已执行 |
| 秘密/失败边界 | 无人工 SQL、公开 bootstrap、skip_mfa、直接签普通会话或正文后门；秘密不进 argv/env/history/stdout/stderr/日志/APM/Receipt/Outbox；PG/KEK/必需审计失败安全回滚 |
| PR03a/02b 不变量 | 唯一 security_epoch；失效身份 fallback/LocalReconciler、raw/Receipt/Outbox/fence/unknown 风险保留；mark_submitting 成功 COMMIT 仍为发送授权点；恢复不重发 Provider、不退款或清风险 |
| P3 与风险 | PR03a bootstrap-only/P3-01、PR02b 两项 P3、PR09a 及既有 TBD 不关闭；条件风险不因文档审查生效 |

**新增问题分级：未发现新增 P0/P1/P2 或必须修订当前 SHA 的设计问题。** 保留一个 P3 非阻塞表达建议 R01：SRD 198 的“所有锁等待后重核四时间”在 S2 正式协议/SQL 中宜拆成两个谓词：验证时 `verified_at < challenge_expiry`；执行时实际时钟 `< operation_deadline` 且当前资格/pending 有效。最小改进是在正式字段/事务契约明确这两个阶段，避免误把已消费 challenge 的过期再次用于执行窗口。SRD 106、CD 65、TEST A41 已明确该语义，当前不存在相反规则，因此不要求为该建议改动本次 SHA，不影响 D02 CLOSED。

D0 当前证据的新鲜度、status op 事实持久化/保留及线性化、完整锁集/ACL、秘密生成响应等是已明确必须交付的 S2 设计细化与实现验收条件，不能因本次 APPROVE DESIGN 省略或把它们解释为可选措施。

## 7. 本轮实际验证、原始证据与未执行事项

| 实际操作/命令 | 结果和证据 |
|---|---|
| 新建本地独立 clone，detached checkout 精确 SHA | 两条 Git 命令均 exit 0；无 fetch、远端请求或正式 worktree 注册 |
| `python -B work/reviewer/verify_review.py`（cwd 为独立副本；Python 3.10.4） | **exit 0，33 项静态断言全部通过**；[verification.json](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/verification.json)、[commands.json](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/commands.json) 保留原 argv/cwd/stdout/stderr/退出码 |
| rev-parse/rev-list/status/ls-tree/cat-file/merge-base | 精确对象、唯一父、祖先、clean、基线保护和实际文档字节一致；[保护清单](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/protected-files.json)、[文档清单](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/document-manifest.json)、[来源快照](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/source-snapshot.json) |
| 两层 diff 与各自 `git diff --check` | 均 exit 0；[相对父 diff](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/parent.diff)、[相对 main diff](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/main.diff) |
| 六文档文件链接/UTF-8/围栏及契约结构 | 91 条相对文件链接有效；7 JSON 可解析、599 本地 `$ref` 可解析、134 operationId 唯一；三正式密码模型仍 12–1024、契约 0.1.0；118 需求/28 包只核计数及基线不变；[结构结果](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/contract-structure.json) |
| A01–A46/19 行 | 脚本仅核编号/行数；主审及子审进行语义/反例静态推演，未执行机制。正式 DAG 的 03b→03a/TBD-08 另只读核对；没有重跑业务状态机 guard |
| 原报告/开发者交付来源 | [输入来源 hash](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/input-provenance.json)；开发者的 34 fixtures、16 guards、五命令成绩未换算为本次运行结果 |

附带读取的两个诊断失误如实保留在 [PREPARATION-NOTES.md](J:/class-assistant/reviews/pr03b-design-rereview-20261105/outputs/reviewer/PREPARATION-NOTES.md)：一次探索把 JSON machines 字典按列表索引，产生 KeyError；一次子审猜错 0004 SQL 名称的 rg 失败后读取实际文件成功。独立验证脚本首次运行即 exit 0；未为取得绿色修改任何正式断言/文件。

本轮没有运行 OpenAPI/JSON Schema 验证器、34 fixtures、五流 16 guards、A01–A46 机制测试、19 行实际交错、PG/ACL/RLS/迁移/并发、HTTPS/Cookie/CSRF/WS、邮件、Argon2 或秘密通道/身份工具实验。没有重跑历史后端 464、Windows/DPAPI、九 Node、前端或远端 CI；没有连接数据库或真实服务、读取课堂/配置秘密、登记真实身份/根/凭据、执行恢复或建立实际 D0。范围检查和静态推演不证明机制执行成功。

## 8. 精确下一阶段和停止边界

**当前可进入：S1 剩余技术与实验方案审定。** 本次已完成的子条件是 D01–D03 设计缺口关闭，以及 N01/N02 具体路径的门禁明确；没有宣告 S1 整体通过。

S1 仍须明确并审定：拟验证的运行环境/隔离目标及平台能力假设；凭据/OS-peer/算法等适度技术选择；名单有效性与关键安全控制的实验输入、成功/拒绝/故障阈值和证据方案；A01–A46 的环境/角色/时间/竞争/秘密通道准备与成功标准。学生核验证据组合、执行者专用授权边界及真实启用资产清单仍须保持可审；实际身份登记不前置为纯合成设计/用例准备条件。已定产品数值不重新投票，候选硬件数量、5/10 分钟 TTL、哈希成本不因本报告冻结。选择部署数据库/迁移身份前必须触发原 PR03a P3-01，不能用 D0 设计认可跳过它。

S1 剩余条件完成后，可按相应授权提交 **S2 正式契约审查及实现门禁定义**：版本/Changelog/ADR/需求、生产者/消费者、严格字段/错误/新状态、issue/complete/status 独立工具协议、D0 变更/高水位、当前资格/nonce、ACL/RLS/完整锁图、identity 内部通知、独立生成新恢复码及一次响应丢失规则、fixtures/类型/Mock/兼容/回退。此后仍需明确实施范围授权才可 S3；本次没有修改正式契约的授权，也没有启动实验的授权。

真实启用须在实现和精确 SHA 独审之后另核实际 D0、根/本人映射、获权 Admin/学生核验证据受限登记、变更/撤销/防回滚和恢复演练、秘密通道及补偿措施实测和独立批准。合成材料通过不证明这些真实资产就绪，亦不允许 Agent 为真实用户恢复。

PR02B-P3-01 在 reconcile 操作 HTTP 接入前关闭语义关口，认证路由本身不自动触发；不得顺手挂载 reconcile。PR02B-P3-02 在多 Attempt 扩展前处理。PR03a 0006 仅可信 bootstrap 支持及原 8 passed/1 failed/exit1 证据保留。PR09a 和其他既有 P3/TBD 保持原关口。

**APPROVE DESIGN 仅属于本次精确 SHA 的设计回应。** 审查报告/脚本/证据只写入新的独立审查副本，正式树、代码、契约、测试与历史证据未修改。复审后停止；未 Push、合并、整合、迁移、部署或发布。
