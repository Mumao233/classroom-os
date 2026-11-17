# PR-06a 的真实接口验证计划

负责人为林珈诚。计划日期为 2026-11-17，关联工作包为 PR-06a「ASR 协议与模拟 POC」。本计划处于待专项授权状态，当前真实 API 调用授权金额为 0，调用次数为 0。

## 当前前置缺口

审计基准为 `8b1e44e5fd87f43f0e675856de68dce069d8080f`。现有 Web ASR 仅提供模拟器，供应商 WebSocket transport、SDK/API 版本锁定、wire 解析、密钥接入、超时和 socket 清理尚未实现。上述工程缺口需在 PR-06a 的范围与契约流程内处理，并完成离线验证及独立审查。[本轮审计](AUDIT-20261117.md)

真实验证还依赖专用账户与凭据、费率和计量上界、环境隔离、预算预留、发送门禁、停止条件与审计材料。完成该计划文档不会启用任何调用开关。

## 已核对的规范

仓库指定模型为 `qwen-audio-3.1-asr-flash-streaming`，地域为 `cn-beijing`。领域接口为 start/send/finish/events/query，查询能力不支持时返回 unsupported，已发音频结果不明时保留 unknown。[ASR 契约](../architecture/04-CONTRACTS-FLOWS.md)

2026-11-17 复核阿里云公开帮助页。协议族为 Qwen-Audio-ASR-Streaming WebSocket，URL 路径版本为 `/api-ws/v1/inference`。北京业务空间端点模板为 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`，在握手时使用服务端 Bearer 凭据。候选需锁定 WebSocket 库版本并记录协议文档快照。模型 ID 和 URL 路径已确定，实际业务空间、访问权限和服务端模型修订仍待确认。顺序为建连、run-task、task-started、binary、finish-task、剩余结果、task-finished、关闭连接。[WebSocket 接入](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-websocket-api)

run-task 与 finish-task 共用 UUID task_id。结果句子身份及 partial/final 需映射到本项目稳定标识和 revision。累计 usage 应按高水位/差量核对，任务 final 和费用最终结算分别记录。[客户端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-client-events) · [服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events)

## 执行前登记

| 前置材料 | 当前状态 | 执行要求 |
|---|---|---|
| 候选源码与审查 | 待完成 | 固定 commit、依赖锁、协议/API 族、离线测试及独立审查结论 |
| 专项人工授权 | 未授予 | 记录授权人、账户、模型、region、用途、有效期、连接数、音频时长、预算和停止条件 |
| 隔离账户与 Workspace | 未验证 | 使用独立 POC 环境，核对服务权限、地域、配额和已有费用基线 |
| 凭据配置 | 未验证 | 服务端安全注入，日志仅记录配置存在性，密钥不进入仓库、命令文本、浏览器或报告 |
| 真实费率 | 未验证 | 记录控制台费率、minimum、取整、计量单位、价格版本及核对时间 |
| 费用上界 | 未验证 | 取得全部配置 meter 的可信最高量，确认保守总价落在授权预算内 |
| PG 预算与调度 | 待 ASR 接入 | 验证 reserve、submitting、一次性 permit、COMMIT 后 I/O、unknown 留存和迟到结算 |
| 调用与时长硬限制 | 候选参数待批准 | 每次连接计数、累计音频样本数、会话截止时间和禁止自动重试均由执行器约束 |
| 样本 | 待准备并批准 | 只用获准非敏感短语音，登记 SHA-256、格式、样本数、人工转录及用途 |
| 请求/响应脱敏 | 待实现验证 | secret、Authorization、供应商原始错误及正文不进入普通日志 |
| 账单与审计 | 待配置 | 保留 attempt/stream/request ID、累计 usage、状态、预留/结算及控制台核对记录 |

现有 `PgBillingRepository` 默认 `synthetic_only=True`，初始 `billing` 和 `asr_streaming` 开关关闭。真实价格需有效版本及 `verified_at`。同一操作按 `op_id` 预留，发送前用 `authorize_send` 重验开关/风险、绑定 attempt 与 fence，再用 `consume_permit` 消耗唯一 submission key。本地事务 COMMIT 成功后才能建立真实连接或执行许可覆盖的 I/O。提交结果不明时保存原意图并阻断重试，重复 key 不产生第二次许可。现有 worker 的通用钩子在提交时一次消耗整笔 `maximum_units`。06a 需离线验证短任务怎样限定其连接、样本范围与预算，持续流逐窗预留和帧级交接归 06b。[PG 费用仓储](../../../web/backend/classroom_web/infrastructure/billing_repository.py) · [任务钩子](../../../web/backend/classroom_web/application/billing_tasks.py)

## 候选最小用例

以下参数用于提交专项审批，执行前需要明确批准。建议并发为 1，最大连接数为 3，每条连接最多上传 10 秒音频，总上传时长最多 30 秒。握手失败、认证失败或异常断开的连接也消耗连接次数。每个样本至多发送一次。

| 用例 | 候选输入 | 核验事实 |
|---|---|---|
| 最小正常识别 | 一段至多 10 秒的普通话短句 | 凭据/握手、启动事件、partial/final、样本时轴、尾部结果、terminal、最终 usage |
| 中英混合及多句身份 | 一段至多 10 秒的中英混合短句 | 句子 identity、revision、顺序、文本与时间范围、finish 后剩余 final |
| 受控主动停止 | 一段至多 10 秒的短语音，在已发送少量音频后停止输入 | 停止新帧、drain、终态或 unknown、费用保留、资源释放 |

鉴权拒绝、坏 JSON、缺字段、重复/乱序终态、服务端错误、初始化超时和网络中断首先由离线替身执行。需要在线观察的额外故障场景应纳入新的明确用例、连接和费用额度。

样本统一为 16kHz、单声道、signed PCM16 little-endian 原始帧，源文件容器由测试准备阶段明确剥离。实际发送按原样本数计时并校验 SHA-256。程序不自动开麦、获取课堂录音或调用语音合成服务。

## 成本计算与候选上限

2026-11-17 核对模型页及统一价表，北京原价为输入 ¥6/百万 Token、输出 ¥4.5/百万 Token。计价表中的“百万”是报价分母。该型号按两种 Token 独立计费。公开的 3.0 Streaming 时长单价及实时对话模型的音频换算公式均有各自的适用型号。候选预算采用原价，账户优惠或免费额度不计入安全预算。[模型价格](https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-asr-flash-streaming) · [统一价表的 Qwen-Audio-ASR-Streaming 小节](https://help.aliyun.com/zh/model-studio/model-pricing)

按公开原价计算的金额为

```text
金额（元） = 6 × input_tokens / 1,000,000
           + 4.5 × output_tokens / 1,000,000
```

建议申请单批总预算为 **¥1，待批准**。逐连接核算后累加，最多 3 次连接、30 秒音频（480,000 个单声道采样）。执行器必须在连接前持有以下材料。

| 待证项目 | 本次核对结果及阻断原因 |
|---|---|
| 输入 Token 上界 | 发送时长可以由样本数限定。目标型号的公开计价小节未提供从样本到计费输入 Token 的保守上界。供应商事件示例中的时长和实测比例都不足以计算上界。 |
| 输出 Token 上界 | 模型页最大输出长度显示为 `-`，公开的 Streaming 客户端事件未列出可强制的 `max_output_tokens`。预留还需覆盖 finish 后返回的尾部结果。 |
| 最低计费和取整 | 所核对的目标型号公开页未说明失败握手、已启动无音频、短会话的计费起点、按请求最低收费、取整单位和粒度。需取得适用于目标账户和模型的费率条款或供应商书面依据。 |
| PG 的计量映射 | `ReservationRequest.validate` 对 ASR 强制正数 `audio_duration_ms`，而 `Price.amount` 要求上界集合与价目集合完全一致。仅填两项 Token 价目无法按现有接口完成 ASR 预留。须在离线 PG 用例中验证配额时长与两项收费量的独立来源、完整价目及结算。若选用零单价时长项，应明确其配额用途并验证计量来源。 |

代码按计量项向上取整至微元，再应用该价目的最低金额。此算法用于本地预留，真实价目的最低收费、取整与输出上界仍须单独举证。当前无法证明三条连接的最坏费用落在 ¥1 内，执行器应于握手前拒绝全部真实请求。[费用领域实现](../../../web/backend/classroom_web/domain/billing.py) · [客户端参数](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-client-events)

预算预留必须采用各计量单位上界，并应用目标账户实际最低收费与取整。停止新输入仍可能收到尾部输出及滞后账单，预留应涵盖这部分风险。现有架构明确说明供应商账单滞后与在途费用可能使总账超限。[费用契约](../architecture/06-COST-OPERATIONS.md)

费用契约第 2 节保留原文“没有可靠费用上界时只开放明确总费用授权的POC”。该条仍需专项人工风险决定与可审计执行方案。此任务未授权调整计量上界硬门禁、源码或契约。在对应决定正式完成前，缺上界时维持拒绝发送。

## 累计用量与账单归属

- 按 provider task_id 绑定本次 stream、attempt 和 reservation。供应商的 `result-generated` 可省略 `usage`；有值时读取任务至今的 `input_tokens`、`output_tokens`。若含 `total_tokens`，核对二者之和。中间事件或内外层的重复累计值只记一次，缺失字段保留为未知。逐帧可使用相邻高水位之差做观察，传入 `reconcile` 的数量始终是同一请求的累计值。 [服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events)
- PG 对同一 reservation 接受一个 `provider_request_id + meter`，通过 `revision` 与内容摘要去重。适配器需保存这一映射和稳定的计量版本；相同版本内容冲突、新累计值倒退或量超过预留均停止新发送。仅在有依据的最终账单更正时按仓储规则引用原回执。原 attempt、预留周期及供应商账期分别保留。[费用仓储](../../../web/backend/classroom_web/infrastructure/billing_repository.py)
- `task-finished` 确认识别结果已返回，取其 `payload.usage` 核对最终累计量。缺失或不完整时保留已有高水位与费用敞口，等待迟到 usage 或账单。费用用量与音频配额时长分别核对；3.1 的 `duration` 字段不可替代输入和输出 Token。[服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events)
- 账单按业务空间、Key ID、模型、输入/输出计费项及时间窗核对。官方说明推理账单按分钟汇总，通常有 2–10 分钟延迟。保存脱敏关联记录，将隔离窗口内各请求的最终用量按输入、输出分别汇总与账单对照。无法唯一归属时保留待核，账单未出现期间继续计入风险。[官方账单字段与出账说明](https://help.aliyun.com/zh/model-studio/bill-query-and-cost-management)

## 停止与恢复

以下任一条件成立即停止后续连接和新音频发送。

- 授权、账号、Workspace、模型、地域、价格版本或样本 hash 与批准记录不符。
- 预算开关关闭，预留、计量上界、permit、权限或数据库不可用。
- 达到连接数、音频样本数、执行时间或费用限制。
- 认证失败、启动超时、异常关闭、身份不匹配、消息格式歧义或无法解释的终态。
- usage 回退、冲突、超预留，或无法将计量归属原 request/attempt。
- 普通日志或异常中出现密钥、Authorization、音频内容或未批准的转录正文。

已有在途连接按其状态尽可能 finish/drain，达到本地关闭截止时间后释放资源。超时具体数值在离线实现完成后作为候选参数提交批准，当前文档没有把实现阈值当作供应商保证。

已发结果不明时保留原 request/attempt、样本范围、预留及 unknown。执行器禁止自动重连、重送、换商和重新申请额度。所有剩余调用待人工核对原结果及费用后决定。

## 验收与审计产物

| 验收维度 | 所需证据 |
|---|---|
| 协议 | 固定模型/API 族与库版本，真实握手及事件类别记录，task_id 贯穿一致 |
| 音频 | 已批准样本 hash、发送字节/样本数、起止范围和帧序，发送前有 durable 与许可事实 |
| 识别 | 脱敏后的 partial/final 顺序、句子身份与 revision、合法时间范围，人工核对尾句 |
| 终态 | finish/drain 完成，有 provider terminal 和已提交 final，未完成项保留原状态 |
| 资源 | socket、任务、reader/writer 和缓冲退出，重复关闭安全 |
| 安全 | 普通日志、错误及导出中无密钥或未经批准正文，浏览器没有供应商凭据 |
| 费用 | 每请求预留、permit、累计 usage、最终计量与控制台账单对照，unknown 风险继续留存 |
| 复现 | 源码 SHA、命令、环境、脱敏参数、时间、退出码、日志 hash、审批记录与逐例结论 |

成功判定要求协议、结果持久化、资源清理和费用事实分别具有证据。识别产物可用但最终 usage 暂缺时，应保留费用核对待办及风险预留。该最小 POC 的质量观察只覆盖所选短样本。

本轮仅核对公开文档，真实 ASR 握手与请求均为 0，本轮新增费用 ¥0，新增授权 ¥0。供应商账户账单未经访问。未来短样本的最坏费用仍待证明。执行后应记录每例通过、失败、未完成与未执行原因，并保留所有失败轮次。

## PR-06a 与 PR-06b 状态

PR-06a 当前保持 `doing`。供应商 transport、费用映射与专项授权门禁仍待关闭。各候选提交的测试与独审状态见对应回执。[验收记录](CLOSEOUT-20261117.md) · [审查记录](REVIEW-20261117.md)

PR-06b 可参考冻结 Adapter、PCM、稳定版本与 unknown 语义。其正式实施还需要 06a 适配器与协议验证，以及 05b 采集持久化、02b 任务、11a 持续流计量接缝就绪。本计划没有启动 PR-06b。
