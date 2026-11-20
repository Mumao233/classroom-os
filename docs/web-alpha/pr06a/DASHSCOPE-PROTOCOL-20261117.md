# PR-06a · DashScope 单连接协议与 PR-06b 交接

负责人为林珈诚。工作包为 PR-06a「ASR 协议与模拟 POC」，对应 Draft PR #19。本轮核对日期为 2026-11-17，真实 DashScope 握手、推理请求和新增费用均为零。PR-06a 保持 `doing`。

## 协议锁定

公开资料锁定 `qwen-audio-3.1-asr-flash-streaming`、`cn-beijing` 和 Qwen-Audio-ASR-Streaming WebSocket 协议族。北京业务空间端点模板为 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`。服务端凭据在握手使用 `Authorization: Bearer <api_key>`，可选 `X-DashScope-WorkSpace`。官方说明在凭据无效或缺失时以 HTTP 401/403 拒绝握手。实际业务空间、模型服务修订及账号配置留待专项验证。适配器需要显式注入离线 `Transport`，没有真实连接实现或环境变量激活路径。[WebSocket 接入指南](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-websocket-api) · [模型信息](https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-asr-flash-streaming)

`/api-ws/v1/inference` 的 `v1` 是供应商 URL 路径。仓库 OpenAPI 包版本为 `0.2.1`，公开浏览器事件使用 `protocol_version=1`。以下字段表和对应的脱敏测试样例记录了本轮采纳的 wire 语义。Web 后端现有依赖固定 `websockets==17.4`，离线适配器只依赖注入端口。真实连接实现的库和配置需要随获批候选一起冻结。[OpenAPI](../architecture/contracts/openapi.json) · [事件 Schema](../architecture/contracts/events.schema.json) · [后端依赖](../../../web/backend/pyproject.toml) · [离线 wire 样例](../../../web/backend/tests/asr_dashscope/test_wire.py)

| 方向与事件 | 本轮采纳的字段与行为 |
|---|---|
| 客户端 `run-task` | `header.action=run-task`、客户端独立生成的 `header.task_id`、`header.streaming=duplex`。`payload` 包含 `task_group=audio`、`task=asr`、`function=recognition`、目标 `model`、`parameters.format=pcm`、`parameters.sample_rate=16000`、`input={}`。 |
| 服务端 `task-started` | `header.event`、匹配的 `header.task_id`、对象型 `payload`。样例额外带 `header.task_uuid`，本轮将其视为供应商附加信息。 |
| 客户端 binary frame | 只在启动确认后发送。项目侧传入 16 kHz 单声道 signed PCM16 little-endian 原始字节，校验连续半开样本区间、格式声明、字节长度和 SHA-256。 |
| 服务端 `result-generated` | `payload.output.sentence` 含正整数 `sentence_id`、毫秒 `begin_time`、可空 `end_time`、`text`、布尔 `sentence_end`。`sentence_begin` 部分样例缺省。本轮使用 `sentence_end=true` 认定句尾；`sentence_id=0` 配合 `heartbeat=true` 只提供连接活动及可能的用量证据。 |
| 客户端 `finish-task` | `header.action=finish-task`、与 `run-task` 相同的 `task_id` 和 `duplex`，`payload.input={}`。停止新音频，继续读取尾句和终态。 |
| 服务端 `task-finished` | `header.event` 和匹配的 `task_id` 确认该连接的识别结果已送达终态。`payload.usage` 可提供任务终态累计值，仍需独立核对用量。客户端随后关闭连接。 |
| 服务端 `task-failed` | `header.error_code`、`header.error_message` 和匹配的 `task_id`。供应商在明确失败后关闭连接，领域错误保留归一化事实，原始错误正文不进入普通日志。 |

官方结果样例的 `payload.output.output` 可能镜像 `sentence` 和可选 `request_id`，`usage` 也可能同时出现在 `payload.usage` 和 `payload.output.usage`。解析检查重复的已知语义是否相等，累计值只处理一次。可选的字级、声道和 `stash` 元数据不进入领域事件。解析器拒绝重复的 JSON 对象键、未知事件类型、错误 UUID、错误任务归属以及冲突的已知镜像，并限制消息和正文大小，拒绝 NaN 与无穷大。[客户端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-client-events) · [服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events)

`header.task_id` 是该供应商连接的任务关联值，`output.request_id` 为可选供应商请求标识。两者可能恰好相同，始终按不同来源处理。本地 `task_id`、`attempt_id`、`stream_id` 分别在应用侧归属。`segment_id` 由本地 stream 和供应商句子编号稳定派生。官方样例显示同一个 `sentence_id` 的 `begin_time` 可从 0 ms 更新到 160 ms，保持句子身份并允许时间修订。供应商未提供 revision。适配器按首次出现的句号从 1 连续递增验证，并在连接内生成 revision。已见 partial 的原样重现按重放丢弃，供应商缺少修订序号时无法区分它和一次恰好回到相同内容的新更新。已接受的 final 不被后续 partial 改写，同一句的冲突 final 会终止该流并保留先前证据。稳定重放和持久版本归 PR-06b。[服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events) · [项目接口与数据流](../architecture/04-CONTRACTS-FLOWS.md)

3.1 的计费用量为 `input_tokens` 与 `output_tokens` 两个非负累计值。若给出 `total_tokens`，应等于两项之和。仅有秒数 `duration` 时保留计费待核状态；`duration` 是官方 3.0 的计费字段。已见高水位保留在原任务上。相同规范化结果包的重放不会把较早用量当作回退。心跳将可见的时间字段及已验证的 duration 纳入重放键，形状与时间完全相同且没有供应商序号时仍按重放处理。`asr.usage` 为每个新观察或最终性变化分配递增 revision；回退和超限的完整数量另外保留为 `asr.usage_anomaly`。确认识别终态后，缺失或异常的终态用量，以及缺少稳定请求 ID 的用量，继续等待核对。真实账单的最低计费、取整和 Token 保守上界仍待验证。终态或已收到的 final 候选均无法单独释放费用预留。内部 billing_pending 只描述供应商识别和最终 Token 证据是否齐全，账本结算仍归事务调用方。[服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events) · [真实接口验证计划](REAL-API-PLAN-20261117.md) · [费用契约](../architecture/06-COST-OPERATIONS.md)

## 五方法与调用顺序

每个适配器通过一次性 claim 独占一个注入的 Transport，并在一个事件循环内运行。Transport 提供异步建连、JSON 发送、二进制发送、文本接收和关闭，必要时以立即、可重复调用的 `abort()` 释放部分打开的连接。`start(config)`、`send(frame)`、`finish()` 是异步方法，保持五方法的流状态回执 `{stream_id, state}`；`events()` 批量取走已规范化事件，`query(receipt)` 同步返回 `unsupported`。文档未公开可核对同一原请求的查询指令。退出时使用异步上下文或 `aclose()`，同一个实例没有重新建连和自动重传路径。[Transport](../../../web/backend/classroom_web/asr_dashscope/transport.py) · [项目接口与数据流](../architecture/04-CONTRACTS-FLOWS.md)

| 领域步骤 | 调用方可依赖的事实 |
|---|---|
| `await start(config)` | 完成身份、配置与离线许可门禁，建连和 `run-task` 发送后等到匹配的启动确认。确认事件的 Transport 入队顺序须在该命令交付后。 |
| `await send(frame)` | 串行核对 PCM 和已授权范围，发出或可能发出的范围归属于原尝试；I/O 结果未明时保留 `unknown`，本连接不重送。 |
| `await finish()` | 停止新发送，在本连接至多发一次 `finish-task`，接收剩余识别事件和终态。明确拒绝和无证据终态分别保留。 |
| `events()` | 批量返回内部 `asr.started`、`asr.partial`、`asr.final_candidate`、`asr.transcript_unconfirmed`、`asr.usage`、`asr.usage_anomaly`、`asr.terminal`。`asr.final_candidate` 从 wire 取已确认发送范围的句尾。活动写入期间暂存覆盖未确认样本的句子，无结束时间的 partial 以接收帧入队时已尝试发送的末样本为边界。关闭前先收拢活跃写入结果，仍缺覆盖的句子以 `asr.transcript_unconfirmed` 交给原尝试核对。终态回执与分类完成的事件同批可见。持久事务完成后才有可公布的 final。 |
| `query(receipt)` | `unsupported`，保留原任务和费用状态。 |

供应商毫秒时间轴从连接开始的音频计算，本轮以首个已提交样本为基点加 `wire_ms × 16`。可空的 partial 结束时间留在内部证据中，最终课堂时间还需用 PR-05b 的采样与 epoch 映射。公开 `transcript.partial` 规约没有 `end_ms`，`transcript.final` 要求持久 `event_id`、`cursor`、`segment_revision_id` 和 `transcript_version_id`，候选适配器不直接发布公开浏览器事件。连接中断、超时或协议歧义后，保留已确认的文本和可疑发送边界供原尝试核对。[事件 Schema](../architecture/contracts/events.schema.json) · [采样时间轴与状态](../architecture/04-CONTRACTS-FLOWS.md)

本地候选截止时间分别为建连 5 秒、启动确认 5 秒、单次写入 5 秒、读取 15 秒、finish 等待 8 秒、关闭 2 秒和会话总长 45 秒。每项独立可配置。短连接使用有限等待来覆盖握手、样本馈入和尾部排空，避免处理失联连接时继续占用会话资源。实际网络延迟、VAD 排空和关闭时长需要在获批的真实短样本中验证。[WebSocket 接入指南](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-websocket-api)

## PR-06b 前置与接入责任

1. 按 DAG 验证 `02b`、`05b`、`06a`、`11a` 的集成。PR-06a 的真实接口验证需专项授权、`03b`、`04a`、`05b`、`11a` 及 `TBD-01/02`。在费用上界、真实隔离账号、批准样本、连接次数与审计条件可证以前，真实发送继续拒绝。[依赖 DAG](../architecture/contracts/pr-dependencies.json) · [真实接口验证计划](REAL-API-PLAN-20261117.md)
2. 从 `CaptureSource.durable_source` 取得持久采集事实，经同意与所有权复核后读取实际 PCM，再核对 hash、采样区间、epoch 和时轴。保存任务、尝试、供应商任务及请求、stream、音频范围和费用记录的身份映射。供应商身份不参与浏览器归属授权。[职责边界](BOUNDARY-20261117.md) · [项目接口与数据流](../architecture/04-CONTRACTS-FLOWS.md)
3. 在 I/O 前完成 PR-11a 费用预留、许可、一次性消费、持久提交与返回核验。区分采集配额、输入／输出 Token 和已发费用风险。延迟计量与 unknown 关联原尝试和费用窗口；连续发送、停送、跨窗和结算需在 PR-06b 单独联验。[费用契约](../architecture/06-COST-OPERATIONS.md) · [真实接口验证计划](REAL-API-PLAN-20261117.md)
4. 把未提交的 partial 作为临时事件。通过所有权、来源和 revision 检查后，持久化 `asr.final_candidate` 并用 owner outbox 发布现有的 `transcript.final`。按 stream、segment、revision 去重，避免已经确认的 final 被较旧 partial 或冲突内容替换。[事件 Schema](../architecture/contracts/events.schema.json) · [项目接口与数据流](../architecture/04-CONTRACTS-FLOWS.md)
5. 恢复流程保存已发送和可能已发送范围，先核对原尝试。有效的恢复或人工确认可建立新 attempt、stream 和预留。供应商断开与采集 epoch 轮换分别归档。持续流、重启恢复、跨窗与课堂字幕都需 PR-06b 单独验收，Filetrans 和长时真机验收保留在各自工作包。[职责边界](BOUNDARY-20261117.md) · [依赖 DAG](../architecture/contracts/pr-dependencies.json)
