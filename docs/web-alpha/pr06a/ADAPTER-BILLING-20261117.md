# PR-06a 单连接费用接缝

负责人林珈诚。工程验证使用离线 Transport、测试数据库和合成价格。真实 DashScope 请求数 0，新增费用 ¥0。

## 单请求许可与事务顺序

1. 已认证的业务调用方通过 PR-11a `reserve` 同事务预留产品时长和最坏费用，由 PostgreSQL 保存 `task_id` 与 reservation。由已有 scheduler 领取并准备 attempt，取得 fence。生产接入还要证实 consent、durable 音频以及有权使用该范围的派发意图。
2. 调用方在同一事务中先 `authorize_send`，由仓储绑定原 task、attempt 和 fence 并写入 submitting，再用 attempt UUID 作为本次唯一 submission key 调用 `consume_permit`，消耗单请求上界。检查第二连接在提交前只可见原 reservation。事务失败时保留原账本状态。若提交结果不明，必须查询原意图并保守保留风险，无法确认时停止外部 I/O。
3. 已确认提交后，离线组合层将 synthetic reservation、permit、local task、attempt、stream、独立的供应商 task UUID 和三个计量上界绑定给 `SyntheticRequestPermit`。单个对象只可消费一次，即使握手失败也不复用。每帧把累计样本数与 `audio_duration_ms × 16` 对照，输入与输出 Token 各有独立上界。此对象供内部测试构造，已提交 PG 事实由测试在独立连接检查。正式跨进程防重由持久发送意图及 `billing_permits` 唯一键承担。
4. 连接终止后分别判断识别结果和费用证据。已有发送意图且无法确认结果或最终费用时保持 unknown 或待核对，保留原 attempt、已知 usage、剩余预留以及原预留期。确认的后续 usage 由原 reservation 的 PR-11a reconciliation 接口接收，账单月单列。 [PG 仓储](../../../web/backend/classroom_web/infrastructure/billing_repository.py) · [任务费用接缝](../../../web/backend/classroom_web/application/billing_tasks.py) · [离线许可](../../../web/backend/classroom_web/asr_dashscope/billing.py)

## 合成计量与 PostgreSQL 验证

三项合成价目使用 `audio_duration_ms`（仅配额，微元单价 0）、`input_tokens`、`output_tokens`。测试按独立数值造数，验证价目键集合与预留、累计量集合完全一致。`Usage` 中的 `meter` 是一次累计用量流标签，`quantities` 保存独立的各项值。正向场景预留 750 ms、60 输入 Token 和 40 输出 Token，预留 180 合成微元。已知累计金额从 36 到 48 合成微元，日／月最终扣减量同为 630 ms。负对照覆盖仅 Token 价目、回滚、重复许可、耗尽 meter、同 revision 冲突、累计回退和错误请求归属。测试的配额时长、供应商 ID 和价格均为合成事实。 [PG 用例](../../../web/backend/tests/integration/test_pr06a_dashscope_billing.py) · [计量定义](../../../web/backend/classroom_web/domain/billing.py)

线上事件的可信高水位、发生冲突的字段和最终计量需要单独留证。累计量按请求归属，重复包只应计一次。缺最终量时保留此前已知金额和残余敞口；完整、可归属的晚到凭证可按更高版本核对。超出上界需停止新音频并提交费用异常证据，后续正式费用结算由账本的调整机制和核对流程处理。`PgBillingRepository.reconcile` 会记录实际可证实的超额风险；申请接入时仍要由组合层区分供应商高水位、待核数据及可结算最终值。 [费用操作契约](../architecture/06-COST-OPERATIONS.md)

## 真实 POC 仍需核对

- 目标输入及输出 Token 的独立保守上界，以及模型、账号和业务空间适用的最低计费、取整单位、失败或无音频成功启动的收费规则。公开每百万 Token 费率只提供报价，最坏费用仍待证明。
- 正式时长来源与配额分摊约定。现有 `ReservationRequest` 强制 ASR 有正的 `audio_duration_ms`，`Price.amount` 要求价格键与上界完全一致。离线用例证明零费率配额项在当前账本内可运行；实际费率目录是否允许该表示、采用已提交样本数或其他计量源、取整和供应商可对账数据，均待 PR-11a 共同确认。
- 由服务端依赖注入确认真实价格版本和完整提交的 permit，关联供应商请求与本地 attempt 并留有过期、撤销、unknown 与迟到 usage 的持久审计。一旦授权变化即停止新发送。购买、授权或网络操作需单独审批。
- PR-06b 还需要衔接 PR-05b 采集持久化、PR-02b attempt/fence、PR-11a 持续流窗口与同一供应商累计 usage 的分配、持久 final 提交与已提交范围的恢复核对。当前 adapter 有明确的合成许可和单连接边界。 [真实接口验证计划](REAL-API-PLAN-20261117.md) · [已冻结接口](../architecture/04-CONTRACTS-FLOWS.md)
