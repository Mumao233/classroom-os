# PR-03a 第三轮定向整改报告

日期：2026-11-02（Asia/Taipei）。开发者已实施并完成本轮本地验证；**R1/P1、R2/P1、R3/P2 的正式审查状态仍为 OPEN / REQUEST CHANGES，等待 Astra 对新精确 SHA 独立代码复审。** R2 的 APPROVE DESIGN 只批准设计，原文归档为 [R2-DESIGN-REVIEW.md](R2-DESIGN-REVIEW.md)。不批准整合、Push、Tag、部署、发布或 PR-03b。

## 根因与修复归属

原 HEAD cb5f73c288fe013763e1e3ee9ebc3eb94a44a6b0、父 088984fbd7c9f36f4250c9af340d6a73a0c57960。修前七项原探针 2 passed / 5 failed / exit 1，原审查副本只读；第三轮修前证据在 outputs/pr03a/third-round/20261102T114612.393722Z。没有改写历史。

| 发现 | 根因 | 本次实施 |
|---|---|---|
| R1 P1 | Python JSON 大小符合限制但最终 JSONB 超过 CHECK，原始行诊断泄露合成正文且回滚重选阻队 | 固定 SQL 写入前检查 pg_column_size(payload) ≤1048576；超限按 failed/invalid/OUTPUT_INVALID 原子收尾。整个 finish 嵌套调用/写入加异常边界，只重新抛固定消息与原 SQLSTATE，不拷贝原 DETAIL/HINT/CONTEXT |
| R2 P1 | 四个 definer 自动归迁移超级用户持有 | 按获批三角色和精确列 ACL/分命令 RLS 转移四函数，撤 PUBLIC、不增加 Scheduler 正文权限；仅内部 P 可读事务行域 raw，I 判身份、M 只读队列元数据；临时成员/CREATE 清理并核对 |
| R3 P2 | 本地收尾没有普通 publish 的固定材料、原/当前祖先与墓碑检查 | 从服务端持久指针发现完整来源图，semester→course→classroom SHARE 后才 task→attempt→result→counter；锁后复验；撤权结果 unknown/ACCESS_REVOKED 一次隔离并排除后续队列 |

本轮 PR-03a 身份功能只涉及既有身份 helper 的受限执行 owner/ACL，不新增注册、密码或会话政策。PR-02b 恢复兼容适配集中在 0006 的候选与固定收尾 SQL；现有 Python Worker、Scheduler、LocalReconciler、Owner UoW 业务源码本轮未改，原身份 fallback 与原 423 断言保留。未改变 Task/Attempt enum、Receipt、fencing、unknown 或费用风险语义。

## 精确状态与来源锁序

| 输入/条件 | Task / Attempt / Result | 事件、租约与费用 |
|---|---|---|
| 从未发送且身份失效 | 原 fallback cancelled，原 planned Attempt 终止 | 清租约；Provider=0；原幂等语义保留 |
| 已发送但无可信 raw | 原 fallback unknown，不伪造结果 | 风险保留；不重发，不释放 unknown 费用 |
| 已保存可接受 raw，来源有效 | succeeded / succeeded / valid；非法或 JSONB 超限则 failed / failed / invalid | fence/revision +1、清租约、completed_at、最小 Outbox 同事务；原 raw/digest/Receipt 保留；Provider仍1 |
| 来源已撤权 | Task unknown、error ACCESS_REVOKED、fence/revision +1、clear lease、completed_at NULL；最新 Attempt unknown/error ACCESS_REVOKED，outcome/rawref/finished_at 不改；Result 整行 pending 不改 | 一次 counter/最小 Outbox；may_have_charged=true；返回 false 表示未完成校验；候选排除此明确隔离，重复无写 |
| 发现图在加锁间改变 | false、无状态/账本写入 | 下一有界 poll 重新发现；不绕过来源 |

正常已链接 raw 的 Provider outcome 保留；原 0005 已有的“legacy 缺 raw pointer、精确同 Attempt durable result”修补仍按原兼容断言建立指针/恢复 succeeded outcome，没有扩大多 Attempt 工作流。Receipt 仍只指向原 task，未重写。结果终态是内部本地处理，不向失效身份发布正文或开放 owner 访问。

来源检查包含课堂、固定历史材料、材料原课程/学期、课堂当前课程/学期的 deleted_at 与相关 account/classroom/material/course/semester 墓碑；可选客户端 input ID 不参与授权选择。完整集合按 ID 稳定排序锁定；task 锁之前拿齐来源锁。材料不可变，以原课程锁与撤权协调。撤权先行/收尾先行三类原来源均有真实 pg_blocking_pids 锁等待证据；测试未关闭触发器。合成 revoker 在独立事务内暂设有效身份以运行真实仓储 guard，提交前再失效，外部未观察到身份恢复；这是机制测试，不声称失效账号能发起新业务写入。

101 条已撤权保存结果后的另一账号正常结果按第一批100、第二批2推进；已隔离项不重选，单次仍最多100。R1 的原 199850 字符 raw / 299752 Python bytes / 1198865 JSONB bytes 在修后无错误、当前 failed、后续 succeeded、两个 Provider 各1次；原 raw/digest/Receipt 不变。客户端异常链、DETAIL/CONTEXT 及新 PostgreSQL 原始日志切片的合成标记负例通过；故障触发器分别在 result 与末尾 Outbox 注入正文异常，所有账本整体回滚。

## 0006 与权限实测

唯一 head 0006_local_reconciler_security，down_revision=0005_identity_recovery。新增 .py、权限 .sql、函数修复 companion .sql，全部属于一个 revision 事务；先在原 owner 管理权下替换函数，再转 owner/ACL/政策/清理。0001–0005 十个历史文件不变，无新业务表或冻结契约变更。回滚承诺针对 0006 单 revision，先前历史链或独立 provisioning 不冒称被同一事务回退。

I=classroom_web_identity_checker；M=classroom_web_recovery_metadata；P=classroom_web_local_result_processor。四函数实际 owner 分别 I/M/M/P，均 NOLOGIN/NOINHERIT/非super/非BYPASSRLS，无 CREATEDB/CREATEROLE/REPLICATION、成员入出边、schema CREATE 或业务表 ownership。逐列有效权限核对按批准矩阵，包含完整 ACL、PUBLIC/default、会员 ADMIN/INHERIT/SET、普通 owner/RLS 与 Admin 无正文后门。

来源 SELECT 与 UPDATE policy 分开：UPDATE USING owner匹配 / WITH CHECK(false)，仅 UPDATE(id) 供行锁；真实 FOR UPDATE 成功，真实 UPDATE 拒绝，完整有效政策集没有抵消 false 的 P FOR ALL。独立事务的 permissive OR 反例证明额外宽松 FOR ALL 会破坏保护，随后回滚。现有 runtime/dispatcher/identity policy 和 FORCE RLS 保留；Scheduler 权限不扩大。

GUC 是候选行域而非身份证明。runtime 仍有既有固定跨账号后台动作能力；不能从 HTTP 输入 owner/task/fence 直连。伪造 GUC 不能获取内部角色、直接读失效身份 raw、调用正文 helper或绕过固定 fence/lease/身份/来源/血缘核验。没有新增通用 SQL、Provider 调用、Task/Attempt INSERT 或 Receipt 权限。

空库全链、0005 增量、重复升级、19 张旧表非空数据、原账本、单 head、真实中途故障的函数定义/owner/ACL/角色/会员/政策/数据/版本回滚已验证。原 0003/0004→0005 历史升级测试保留并在全量重跑；新完整链通过 0006。

**迁移执行能力限制：** 本轮成功升级使用原 bootstrap 和另一随机名字的合法 bootstrap 身份。真实非超级用户 LOGIN 即使已获旧对象管理权，在所测 provisioning 下仍不能撤掉 bootstrap grantor 的临时 ADMIN 边；0006 精确检测并整体回滚。缺旧对象管理权/同名危险角色同样安全失败。未宣称非超级用户独立升级成功，也不推断所有可能的非超级用户部署理论不可行。不让 actor SET bootstrap、不遗留权限、不另创 cleanup 入口；若将来需要不同部署方式，须有另审的明确 provisioning/撤销能力。当前交付采用已批准 bootstrap 升级路径。

## 本轮实际验证

精确命令、环境、退出码、JUnit、前后源码 SHA 和各阶段原始日志索引在 ../../VALIDATION.json。后端完整464项=原423（当前JUnit逐项包含）+七原探针+26新安全/并发+8新迁移，零失败/error/skip、exit0，执行前后及交付源码哈希相同。冻结后七原探针7/7 exit0；专项26/26、迁移8/8。原探针字节作为正式副本完整前缀保留，所有原函数 AST 相同，仅追加唯一证据输出目录适配。

Windows Python3.10.4 本轮525主测试+171子测试、Node24.19.0九套、27门禁均通过；不是把696称主测试或把27称Node套件。后端Python3.12.14、PG17.11、pytest9.2.0、SQLAlchemy2.0.46、psycopg3.3.3、Alembic1.18.4。mypy41源文件、静态8项、pip check、runner对抗、历史/契约/产品保护、diff检查均通过。旧门禁仅将原runner两个路径字面量指向本树work/pr03a，原业务/测试/runner文件不改，具体适配与hash单独留证。

修前5失败不覆盖；第一次7通过但manifest漂移被拒为最终证据；首次专项74/77的3失败是新增fixture/DDL设置错误（%占位符与误用首批必见helper），修设置不降断言；首次工具检查7个typing错误和原有CRLF raw误判已修并保留日志。原selftest工具未改，保护用已提交历史验收raw SHA + Git对象双核对这个既有差异；十历史迁移仍严格原字节检查。最终全量重新执行，不能用中间成功冒充最终源码证据。

## 未验证、风险与停用

真实邮件、Cookie会话/MFA/恢复码/多设备撤销、真实Provider/费用账本结算、生产恢复、远端CI与浏览器联合整合未验证；未实施PR-03b/TBD-08。没有reservation/usage账本，不称真实预留已释放、退款或结算。原两项PR-02b P3、PR-09a开放P3和11项TBD继续OPEN，隐私预检NO-GO不变。本次只证明固定内部收尾边界，不宣传历史全部definer或全部应用日志已完成通用降权/脱敏。

停用时暂停本地收尾/领取，保留raw、Attempt、Receipt、Outbox、unknown风险及迁移账本，用兼容代码或前滚修复；0006拒绝破坏性downgrade，不清库、不重发未知请求。所有合成数据、缓存与日志保留于本树work/pr03a和outputs/pr03a；没有访问正式数据库、真实资料或付费服务，没有修改其他工作树、执行远端操作或自动整合。

独立本地提交的完整SHA、直接父提交、文件清单及最终Git状态见 outputs/pr03a/third-round/DELIVERY.md 与 COMMIT_RECEIPT.json。开发者验证不能替代 Astra 对该精确新 SHA 的独立复审；最终停止在本地交付。
