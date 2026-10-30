# PR-03a 第二轮整改与独立复审准备

日期：2026-10-31（Asia/Shanghai）。基线：`088984fbd7c9f36f4250c9af340d6a73a0c57960`；其父提交：`3b3881540519c27075119f99bb1dbff35d369e35`。原提交及 PR-02a/02b 整合历史保留，本轮不 squash、不改写历史。

## 原 P2 与复现

Astra 首轮结论 REQUEST CHANGES，1 项 P2。已读取 `J:\class-assistant\reviews\pr03a-first-20261031\outputs\REVIEW.md`、最终 log/XML/命令、分组计数及实际探针源码；原材料只读，逐字节副本和 SHA-256 清单位于 `outputs/pr03a/remediation/independent-review/`。

Astra 最终探针实际结果为 6 passed / 4 failed，退出 1。四个失败为 email_verified_at / password_hash 缺失 × 未发送 / 已发送。原任务继续 running，Attempt 为 planned / submitting，保留过期租约并重复出现在 expired 队列；Provider 分别为 0 / 1，没有观察到重发。

本轮在原 HEAD、未修改任务代码和迁移时新增永久负例并独立运行：四项均按预期失败，退出 1，7.05 秒，零跳过；证据：`outputs/pr03a/remediation/prefix-recovery/20261030T170312.992527Z/`。原始失败记录不覆盖、不删除。

根因：0004 增强了 owner_active/lock_owner 的账号可用性，0003 的 recover_revoked 仍只检查 active、deleted_at、墓碑与资源血缘。普通 UoW 抛出 InactiveOwner 后，fallback 错把账号判为可用并返回 false；最早 100 条无法退出 expired 集合，后续任务可能饥饿。

## 修改归属与必要性

| 归属 | 修改 | 必要性与语义 |
|---|---|---|
| PR-03a 身份规则 | 0005 的 identity_usable 布尔函数，owner_active 委托该判定 | 原安全谓词不变：active、未删除；无邮箱内部 shell 兼容，有邮箱须验证且有密码摘要。lock_owner 保留原显式行条件与 FOR SHARE，确保锁等待后正确重检。 |
| PR-02b 恢复兼容 | 0005 替换 recover_revoked 身份判定，完整发送证据布尔 helper | 不再误判失效主体；只返回最小状态，不增加 Scheduler 可读列；保留血缘、墓碑、fence、锁序和原子 Outbox。 |
| PR-02b Worker 兼容 | 可注入本地结果 Reconciler；合成 CLI 显式接线 | 普通恢复与结果收尾分阶段；原 recover_expired 隔离行为保留，run_once 可继续处理已保存 raw。没有 Provider 重试路径。 |
| 获单独批准的系统处理边界 | 固定 SQL 本地校验/收尾函数及 PgLocalResultReconciler | 普通 owner UoW 仍拒绝失效身份；函数内部处理既有 raw，只返回 metadata/boolean，不向调用者或失效账号返回正文。见 [批准方案](RESULT-RECONCILIATION-PROPOSAL.md)。 |
| 永久测试 | 新增恢复/迁移回归及 Astra 原 10 项探针副本 | 原 358 项测试模块全部保留；Astra 探针源码副本 SHA 与原件一致，不删除或改弱旧断言。 |

新迁移为 `0005_identity_recovery`，直接继承 `0004_identity`。0001–0004 八个发布文件保持字节与 Git 内容不变。没有新增表、修改冻结 HTTP/WS 契约、放宽 owner RLS、授予 Scheduler 用户凭据或正文读取权限。

## 任务恢复矩阵

| 场景 | 整改前 | 整改后要求与验证点 |
|---|---|---|
| 未发送，邮箱验证/密码缺失 | running/planned，过期租约滞留 | Task/Attempt cancelled；清租约、fence/revision 与单一事件一起推进；Provider 0；重复恢复无新增写。 |
| 已发送，响应未知 | running/submitting，风险不能收敛 | unknown、may_have_charged=true、清租约；保留 acceptance/request/outcome 证据；Provider 保持 1，不自动重发或退款。 |
| 已保存 raw，账号随后失效 | 普通 UoW 被拒绝，无法直接校验 | 先安全隔离，再由获批准的本地 Reconciler 完成纯校验及 succeeded/failed；保留 raw/digest/Attempt/Provider 事实及费用风险，不创建新 Attempt、不把校验完成当结算完成。 |
| 多 Worker / 身份失效与保存、核对并发 | 缺少交叉验证 | 真实 PG 锁与 fence 确保单次转换；过期、旧 owner、旧 fence、活跃租约及结果冲突不能越权完成。 |
| 超过 100 条失效任务，正常任务排在后面 | 前 100 条可永久挡住后续任务 | 使用原有有界 LIMIT 100 与稳定顺序；处理后的任务退出 eligible 集合，跨批次推进，无 OFFSET 漏项或无限制扫描。 |

Receipt 是不可变操作意图收据，原 response_body 只含 task_id；本轮不把它改写为可变状态快照。Task/Attempt/风险及最小 task.changed Outbox 在同一事务变化，原 Receipt 继续指向同一任务。Outbox 的带时间、revision、序号的状态历史是本轮核验的持久审计轨迹，不冒称实现了后续完整管理审计或费用账本。

## 测试与证据

本轮最终执行命令、环境、每组数量/退出码、失败修复记录、重新执行结果和源码/证据哈希由本目录验证收据及上级 `VALIDATION.json` 记录。所有 PostgreSQL 用例使用本工作树专用合成集群；Provider 只记录合成任务/Attempt ID、请求指纹和模拟响应。

已实际重跑的 Windows 门禁：525 主测试 + 171 子测试，九套 Node / 27 门禁，零失败、错误、跳过，退出 0；本轮新证据在 `outputs/pr03a/remediation/local-baseline-20261030T165947Z/`，不沿用首轮数字冒称新执行。

本轮必须保留的验证包含原 358 项、Astra 全 10 项、未发送/已发送/保存 raw、running/cancel_requested、身份与保存/核对/领取交错、双 Worker、超过 100 条队列、空库/0003/0004 升级、重复升级、真实 DDL/数据故障回滚、函数 ACL/RLS 与所有旧资源授权回归。最终结果以实际生成收据为准，未跑完成前不宣称整改验收完成。

## 风险、后续边界与回退

- 新本地校验是用户明确批准的固定系统处理入口，不能用于认证、任意正文读取或通用 owner 绕过；需要 Astra 重点审查函数授权、owner/fence/lease/血缘、验证器对照和事件原子性。
- 当前 tasks.plan_id 仍受未实现约束，reservation/usage 账本尚未实现。本轮验证未发送没有计费动作、已发送风险不被清除、不产生新调用；不声称真实预留已释放或真实费用已结算，不提前实施 PR-11a。
- 真实邮件投递、Cookie、MFA/恢复码、多设备撤销、真实 Provider/费用结算、生产数据库恢复仍未验证或未实现。PR-03b、PR-11a、PR-04b 的工作不由本轮测试替代。
- 两项 PR-02b P3 及原 TBD 保持 OPEN。本轮不重定同键 reconcile 或多 Attempt 产品语义。
- 回退保留 0005 兼容 schema 和账本，可停本地 Reconciler/注册及 Worker 领取，采用兼容代码回退或前滚修复；不得恢复旧的失效放行、清除风险、重发未知请求、删除 raw/Receipt/Outbox。迁移 downgrade 主动拒绝破坏安全修复；失败 DDL/数据/版本由同一 PG 事务回滚。
- 完成新本地 commit 后停止，等待 Astra 对新 SHA 独立审查；不整合、不 push/merge/tag/release/部署。

## 最终实际执行结果

- 全后端：423 passed，零失败/错误/跳过，退出 0；原 358 项全部保留，新恢复 51、新迁移 4、Astra 原探针 10。
- 真实 PG 专项覆盖未发送/已发送/保存 raw、重复/重启、双 Worker、领取/保存/核对与身份更新的真实锁等待、失效普通恢复的 42501→InactiveOwner 映射，以及 101 条失效前缀后的正常任务。
- 迁移：空库、0003/0004→0005、重复、19 表全部非空记录保持、函数定义/owner/ACL/DDL/数据/版本故障回滚；唯一 head。
- Windows 525 主测试＋171 子测试、九套 Node / 27 门禁，本轮实际重跑全部退出 0。
- 首次专项 58/1、首次全量 417/1 的失败完整保留。前者补齐历史 raw 指针及 Provider 成功事实；后者修正新测试的数值比较基准，仍与原 Python 路径写入后的真实 JSONB 精确比较，没有删除断言或改变 SQL 来迎合错误预期。
- 普通 owner 锁等待后账号失效会抛原始 42501，本轮仅在 lock_owner 边界转换为 InactiveOwner，使 Worker fallback 真正接管；其余权限错误不掩盖。
- 完整 argv、环境、退出码、分组数量和哈希见上级 VALIDATION.json；本次所有最终源文件与完整测试执行后的哈希一致。

最终静态 8 项、mypy 38 个源文件、依赖、runner 对抗自检、保护和 diff 门禁均退出 0。合成随机数据库和登录清理已核验，专用集群正常关闭、55439 端口关闭，日志及原失败证据保留。

主要代码定位：`0005_identity_recovery.sql:6` 共享账号判定，`:53` 撤权 fallback，`:284` 私有 JSON 校验，`:348` 受限结果收尾；`database.py:78` 为 owner 锁等待拒绝转换；`task_worker.py:52` 接入独立收尾阶段；`local_reconciler.py:13` 是可信运行身份的适配器。全部修改服务于 P2 兼容性及本次明确批准的 C 边界。

JSON 对照覆盖重复键、非有限数与溢出、浮点表示、Unicode/转义控制符及 799999/800000/800001 字节边界。比较的是原 Python 校验后写入 PostgreSQL JSONB 的实际值，不是内存浮点值。此语料不是所有 JSON 或未来自定义 producer schema 的等价证明；JSONB 无法表示的 NUL/单独代理项按无效输出处理，raw 仍保留。未来真实 producer 接入需按其版本化结构重新验证。
