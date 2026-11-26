# PR-11a 费用预留与开关前置

负责人为林珈诚。用户于 2026-11-15 授权本地实现、VMware 合成账单及独立数据库测试，确认方案后开始开发。Git 提交、推送、创建 PR、合并、部署与真实付费调用按用户要求另行确认。

当前交付包含本包独立模块及后续接入候选，均有 VMware 合成验收。最新 HTTP、worker、管理鉴权、周期、内部通知和组合迁移增量见 [接入交接](INTEGRATION-REVIEW-20261115.md)。下文保留独立模块原交付时点；正式契约、迁移链、公共通知与持续流追加窗口仍待协调。改动保留为本地未提交文件。

2026-11-16 的 R2 恢复缺口已通过独立增量复审并关闭，范围为本次 5 文件修复及直接影响，对应 28 项 VMware 定向验证。审核记录与独立证据入口见 [R2 修复交接](R2-RECOVERY-20261116.md)。正式迁移、契约/模型及原产品待审事项继续开放。

## 基线与工作树

- 正式仓库为 `J:\class-assistant\classroom-os`，正式远端为 `Mumao233/classroom-os`。
- 开发基线为 `81ab191b1f33de2371567eccd0ef9589d12483c2`。开始时本地 main 与 `git ls-remote origin refs/heads/main` 一致，PR-02b 的 `7ac4caa`、PR-03b 核心合并 `2489932` 均通过祖先关系检查。
- Codex `create_worktree` 首先创建托管工作树。跨盘 `git worktree move` 失败后，通过复制和 `git worktree repair` 将工作树登记到 `J:\class-assistant\worktrees\web-pr11a-budget`，再通过 Codex `attach_worktree` 附加。分支为 `feature/web-pr11a-budget`。
- 工具初始 C 盘副本的清理命令被自动审批拒绝，该副本保留。开发文件、脚本、源码包和回收证据使用 J 盘目录。
- 契约基线为 OpenAPI 0.2.0，SHA-256 为 `7432ba27581f3d44690138b5606ec61c4008579cb049a6c2dfc20737ea3a6a7a`。共享 Alembic 末端为 `0008_manual_recovery`。

依据为 [实施图](../architecture/08-IMPLEMENTATION.md)、[费用契约](../architecture/06-COST-OPERATIONS.md)、[任务与接口契约](../architecture/04-CONTRACTS-FLOWS.md)、[PR-02b 技术债](../pr02b/TECHNICAL-DEBT.md) 和 [PR-03b 当前交接](../pr03b/INTEGRATION-20261115.md)。PR-02b 两项 P3、PR-03b 延后项和补审证据缺口继续开放。

## 范围与参数

PR-11a 提供产品配额和实际费用双账本、保守费用上界、原子预留、发送意图、一次性计量许可、累计 usage 幂等结算、unknown 保留、账单更正、北京时间日/月边界、平台预算池及收费开关。任务、attempt、fence、owner 事务和操作收据复用现有机制。

| 参数 | 10 人阶段 | 30 人阶段 |
|---|---:|---:|
| 实时 ASR 月额度 | 12 小时 | 18 小时 |
| ASR 日额度 | 3 小时 | 3 小时 |
| 重转写月额度 | 2.4 小时 | 3.6 小时 |
| 互动月额度 | 20 次 | 36 次 |
| 总结月额度 | 8 次 | 12 次 |
| 平台月预算 | ¥500 | ¥2,000 |
| 基础设施占位 | ¥100 | ¥300 |
| ASR 池 | ¥160 | ¥700 |
| 重转写池 | ¥35 | ¥150 |
| AI 池 | ¥105 | ¥450 |
| 安全预留 | ¥100 | ¥400 |

金额以 bigint 微元存储，1 元等于 1,000,000 微元。公开 DTO 的金额字段沿用十进制整数字符串。内部列采用 `_microyuan` 命名，接入时显式映射架构逻辑模型的 `_micro_cny` 列名。

风险由基础设施占位、活跃 hold、unknown 剩余风险及已知实际费用组成。正常进行中的完整部分计量保留 submitted 状态，已知金额计入 settled，其余金额继续 hold，窗口内许可仍受上限控制。缺失计量或已经 unknown 的执行保留 unknown 风险，最终计量到达后核对。普通准入同时检查池上限及扣除安全预留后的总预算。60%、80%、100% 越线记录按周期和阈值唯一保存，供通知接缝消费。

默认合成模式只接受合成价格。价格目录保存 provider、model、region、revision、effective/verified 时间和计量规则。当前合成计价支持分计量向上取整及最低费用，实际供应商的参数和取整规则仍需对应真实接入验证。

## 本包文件归属

| 文件 | 职责 |
|---|---|
| `web/backend/classroom_web/domain/billing.py` | 周期、产品参数、价格算术、预留和 usage 输入 |
| `web/backend/classroom_web/domain/billing_ports.py` | 事务内费用端口 |
| `web/backend/classroom_web/application/billing.py` | 已认证用户服务及撤权后专用费用核对事务 |
| `web/backend/classroom_web/infrastructure/billing_repository.py` | 复用现有 UoW 的 PG 账本与任务组合 |
| `web/backend/classroom_web/infrastructure/billing_schema.py`、`.sql` | 独立候选 DDL，供协调迁移链接入 |
| `web/backend/tests/integration/test_billing.py` | 独立 PG 数据库的并发、故障、权限、周期和迁移验证 |
| `web/backend/tests/unit/test_billing_policy.py` | 算术与边界验证 |
| `tools/pr11a/` | VMware 源码打包、独立集群 runner 与进程退出检查点 |
| `work/pr11a/`、`outputs/pr11a/` | 本包中间材料与回收证据 |

## 事务和接入要求

`PgBillingRepository.reserve` 在同一既有 UoW 中建立任务、配额分配、费用预留、事件和收据。同键异体拒绝，同键同体重新授权后返回原任务与预留。总结子任务引用原根预留，复用一次产品额度，费用单独预留。

平台周期锁先于费用配额、任务与预留锁。外层既有 UoW 已取得账号身份锁，保留该授权语义。发送授权将现有 `mark_submitting` 与 reservation 状态绑定在同一事务中，COMMIT 完成后才允许调用方进行供应商 I/O。流式消费者在每个新发送单元前消费计量许可，重复键拒绝再次授权。许可到日/月边界即失效，窗口耗尽须重新预留。

unknown 和 submitted 的 hold 依据最终计量核对。释放入口会锁定任务和所有 attempt，确认不存在发送意图，再复用任务取消逻辑。账单更正追加回执，降低最终累计值时要求引用上一份最终回执。结果正文保存和产物发布继续由任务 owning 模块负责。

撤权后的迟到 usage 通过 `classroom_billing_reconciler` 专用登录处理。其权限限定于费用元数据，缺少任务正文读取和新任务派发能力。`reconciliation_transaction` 校验实际数据库角色，并拒绝 runtime、operator、表 owner、superuser 或 BYPASSRLS 身份混用。

## 交协调聊天的共享接缝

| 共享位置 | 所需接入 |
|---|---|
| `entrypoints/http.py` | 用户配额读取及 owning 业务请求的费用服务接入，复用 HTTPS、Origin、CSRF 和已验证 Principal |
| 管理端认证入口 | `classroom_billing_operator` 为内部能力。HTTP 管理写入先验证现有 Admin、MFA、用途绑定再认证、理由及幂等键，再调用开关和额度调整端口 |
| `infrastructure/database.py` | 需要时将 billing repository 加入 UoW，扩展新增表的运行身份检查，复核统一锁顺序 |
| 任务 worker／scheduler／repository | 付费发送前调用预算授权；ASR 新帧调用一次性许可；取消、超时、撤权和迟到 usage 联动费用状态；保留现有模拟 worker 的行为 |
| 周期调度 | 根据已配置阶段打开新周期，旧期风险与账单持续核对。新周期缺配置时收费准入拒绝 |
| 通知／事件 | 消费持久阈值记录，接入既有 outbox 和通知投影，已读状态独立于持续预算风险 |
| OpenAPI 与 fixtures | HTTP 0.4.0 公开总闸、存储的新采集配置与四个用途开关。采集准入接入前，改变新采集配置返回 503。ASR/AI 的组读数取 OR，组 PATCH 同时设两项，详见 [PR-11b 裁决](../coordination/PR11B-CONTRACT-DECISION-20261124.md) |
| PR-09b | 个人配额页面消费既有 QuotaView，页面实现归 Claude；收费总闸开启且至少一项分项开启时，对应组可用 |
| 迁移链与依赖锁 | 协调分配 revision/down_revision 和接入顺序；本包复用现有依赖 |

共享 HTTP、数据库基础设施、任务调度、契约、依赖锁和迁移链尚待协调接入。独立端口的合成验收与完整应用链路验收分别记录实际覆盖。

当前任务关联一个已冻结的有界费用窗口。持续流的追加窗口、同一供应商流的计量分摊和现有 attempt 关联，需要在 PR-06b 的任务接缝接入前协调落实。当前粗粒度 ASR/AI 展示按各自子开关同时开启聚合，该映射需随公开契约接入审定。

## 迁移计划

候选 DDL 新增 owner 隔离费用表、私有平台预算和风险表、开关、审计、阈值记录及受限函数。owner 表启用 FORCE RLS，attempt 关联使用 owner/task 精确复合外键。追加 `task_attempts` 的复合唯一约束以支持该外键。

候选 DDL 在独立数据库中、现有 head 后事务安装。协调聊天分配正式增量迁移编号后，冻结 SQL 内容并纳入单链。旧任务和操作回执保留，历史缺失费用继续保留未知事实。回退通过停止新收费、保留账本和前滚修复实现。

需要接入前确认的逻辑模型映射包括配额计数投影、实际 SQL 列名和一次性发送许可表。当前实现从分配/回执账本计算用户计数，平台风险保留受限汇总，避免把累计 usage 重复相加。

## 验收矩阵与证据

| 检查组 | 覆盖 |
|---|---|
| 真 PG 并发 | 同键八连接、用户最后额度、跨用户平台池竞争、重复结算竞争 |
| usage | 累计差值、重复、乱序、冲突、最终账单更正、缺计量、超预留费用 |
| 恢复 | 事务失败、结算 COMMIT 前后进程硬退出、发送意图后退出、现有任务恢复保持 unknown |
| 周期 | 北京时间日/月和年边界、旧期 unknown、新期预留、账单月另记、旧日许可拒绝发送 |
| 开关与风险 | 总闸关闭后的新准入拒绝和迟到结算、60/80/100、池上限、安全预留和发送许可 |
| 权限 | 跨 owner、平台表直接访问拒绝、专用 reconciler、撤销账号后的费用核对 |
| 迁移 | 空库经现有链再装候选、DDL 中途失败回滚、旧任务/收据兼容、既有 Alembic head 保留 |

工具入口为 `tools/pr11a/prepare_vmware.ps1 -Suite billing` 或 `-Suite backend`。源码逐文件 SHA-256 清单随 ZIP 传递，在来宾核对后运行。`tools/pr11a/run_tests.py` 使用随机端口、新建独立 PG 集群、受限测试登录和随机数据库，复用已有 loopback 网络防护。测试结束后只停止本轮拥有的 PG 集群。

本次环境为 VMware Windows 来宾 TEST、Python 3.12.14、PostgreSQL 17.11。执行前 broker 查询确认目标 VMX 和 Tools running，IP 查询返回空值，作业通过 Tools 与既有只读共享目录传输。来宾沿用 J 盘到 `C:\j` 的既有映射，实际 PG 数据和运行产物物理位于来宾 C 盘。

| 验证 | 实际结果 | 本地证据 |
|---|---|---|
| 后端回归，包含既有模拟 ASR | 627 个主用例、29 个子测试通过，0 失败／错误／跳过；pytest 262.38 秒，退出 0 | `outputs/pr11a/pr11a-20261115-124601-d0e33263/` |
| 最终费用专项 | 48 项通过，0 失败／错误／跳过，pytest 5.62 秒，退出 0 | `outputs/pr11a/pr11a-20261115-125512-9938e7fa/` |
| 后端回归及最终专项的 PG 关闭 | 两次 owned cluster stop 均退出 0 | 各次 `results.json` 和 `stop.log` |
| 契约静态检查 | 9 项全部通过 | 最终 evidence 内的 `contracts.json` |
| 来宾依赖 | pip check 通过，44 个锁定依赖与实际 freeze 逐项一致 | `dependencies.log`、`dependency-versions.log`、`outputs/pr11a/DELIVERY.json` |
| 宿主静态与保护检查 | 9 个 Python 文件 AST、PowerShell 解析、相对链接、空白检查通过；既有跟踪文件与 HEAD 一致 | `outputs/pr11a/static-20261115/results.json` |
| 证据与源码 | 9 次 evidence ZIP 哈希均与来宾报告一致；最终专项源码清单与最终代码匹配 | `outputs/pr11a/DELIVERY.json` 与各次 `SOURCE.json` |

后端回归使用 `python -B tools/pr11a/run_tests.py --pg-bin <冻结的来宾 PG17.11 bin> --suite backend`，实际 pytest 选择为 `tests --ignore=tests/integration/test_auth_browser.py`。真实 Chromium／HTTPS／WSS 浏览器模块明确排除，本轮也未重跑原本地产品的 525 主测试、171 子测试及九套 Node 门禁。

627 项回归完成后，对独立费用模块修正了完整部分计量的状态转换，补充窗口继续发送及开关并发用例，再运行最终全部 48 项费用专项。共享既有源码、契约和迁移文件没有本轮修改，最终叶子模块修正后采用专项复验。后端回归保留 1 条既有 Starlette/httpx 弃用警告。

最终专项源码 ZIP SHA-256 为 `d752f06ab3125b976d8ca9fbd7c22599f1fbc7964c8293033f9ffd569e037099`。该次 evidence ZIP SHA-256 为 `aeb8fc04fd0ab654f9e49bb7a3f058a58d58d56351d01503a896656d4c9a41b7`。后端回归 evidence ZIP SHA-256 为 `88bf735d73c6d7124bbf57ceddd5ebf17b5f0702355157bbe724b3466d0c3292`。文档在回收结果后补充，代码哈希核对排除本 README。

前期失败记录全部保留在对应 outputs 中。第一轮因测试模块同名在收集阶段失败，第二轮 37 项通过、2 项 ASR 测试 SQL 绑定失败。随后三份较早快照分别得到 596／599／600 项通过及 3 项既有日志门禁失败，原因依次为 runner 未设置日志参数及日志路径不在既有门禁目录内。修正 runner 后 603 项单元／集成检查通过，再补齐模拟 ASR 得到上述 627 主用例和 29 子测试结果。未调整既有测试断言。

宿主仅执行源码编辑、静态检查、VMware 编排及证据核对。所有运行测试均在来宾独立合成集群与数据库执行，真实付费调用为 0。真实供应商账单一致性、持续流完整集成、公共 HTTP 联调和生产迁移继续按 owning 工作包及专项授权推进。

## 待定参数

- TBD-02 的实际模型、文本／图片上限、每任务最高费用、自动解释额度和跨月样例签署。
- TBD-01／06a 的实际价格、最小计费单位、音频 Token 上界、流式窗口长度和最终计量语义。
- 阶段切换时既有周期配额如何调整。当前周期配置固定，另期开期采用所提供阶段。
- 管理员使用安全预留的具体审批与审计协议。
- 供应商账单修订凭据来源和受限原始 usage 摘要格式，按真实适配器能力确认。
- PR-02b 多 Attempt 工作流及 reconcile HTTP 语义，继续按原 P3 关口处理。

合成测试参数只用于检验事务和边界，实际付费能力继续受上述产品参数、前置门禁和专项授权约束。
