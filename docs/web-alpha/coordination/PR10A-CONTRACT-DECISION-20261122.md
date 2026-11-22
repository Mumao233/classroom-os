# PR-10a 契约裁决与 PR-10b 交接

2026-11-22，负责人林珈诚，契约审查 Astra。负责人同意方案 B 及机器可读的错误细分。目标 HTTP 契约定为 **0.3.0**。PR-10b 可按下表设计界面；真实接线以匹配的生产者和消费者为前提。

## 交付顺序

| 交付 | 技术分工 | 起点与完成条件 |
|---|---|---|
| 0.3.0 契约 | Astra | 以正式主线 `a6b7ec4` 的 0.2.1 为基点。契约补丁含 OpenAPI、变更记录、fixtures、生成类型和 Mock Meta。去掉可选调用及扩展严格枚举，故升次版本。 |
| 清单读取、Filetrans 错误投影 | Codex | 清单可从主线开发。错误投影基于 PR-06c 的 Filetrans 路径（本次核对 `f80a64a`）。分别验证，再在同时包含 06c 与 0.3.0 的候选上交回 VMware 证据，更新 06c 的 `PR10B-HANDOFF.md`。 |
| 课后处理页 | Claude，**单独的小 PR，归属 PR-10a 后续** | 接在 Draft PR #29（核对头 `05ff8ea`）之后，真实课堂联调之前。处理列表、分页、旧 Mock 的省略参数行为和课堂完整性。这样可单独修复 #29 的跨设备缺口，不必等 Review 页面。 |
| 重转写错误界面 | Claude，**归 PR-10b** | 开工输入为本裁决的四码和 fixtures。Mock 可先接；真实验收使用 Codex 同版本回包，四种分支可在相同文案下分辨。 |

### 升级窗口

`/api/web/v1`、事件 v1、单项 `AudioManifest` 结构沿用现有定义。0.2.1 前端可能省略 epoch 或拒绝新枚举，0.2.1 后端没有新列表。Codex 须核对 HTTP 入口的版本白名单、07a 候选构造器及其下游组合、版本断言；Claude 须核对生成类型、Mock、客户端及最低兼容 build。新增版本须通过这些组合验证再对客户端宣告。实现验收前沿用原有部署组合，回退按前后端成对执行。本次规范无业务数据迁移。

## 音频清单与验收

- `getAudioManifest` 保留单项语义，`capture_epoch_id` 必须恰好传一个有效 UUID。缺少、重复或非法值返回 422 `VALIDATION_FAILED`。调用者不知道 epoch 时直接走列表。
- `GET /class-sessions/{class_session_id}/audio-manifests` 使用 `listAudioManifests`，返回 `{items: AudioManifest[], next_cursor: string | null}`。已认证的课堂所有者可从另一台设备读取，无需持有采集租约。包括 recording、paused、retired、ended；一次未采集返回 200 空页。继续与接管产生的旧轮次也参与排序。
- 按 `timeline_offset_ms, fence_token, id` 升序。首请求固定遍历的 epoch 集合，页间新增的 epoch 留给下一次从头刷新；各页返回读取时可见的清单内容。可选 `limit` 默认 20、范围 1–100；非空游标最多 512 字符，绑定所有者、课堂和该列表。末页返回 null。非法、篡改或跨作用域游标返回 422，已过期游标返回 409 `CURSOR_EXPIRED`，客户端丢弃本轮分页重新读取。
- 每页重新核实课堂权限、原音删除、epoch 有效期及授权，沿用单项接口的拒绝语义。不可披露的轮次使该页失败，不可静默漏项或回传部分成功；错误与成功均为 `no-store`。列表不披露对象地址、原音或授权凭据。
- **后端验收**覆盖 resume 前后两个 epoch、退役轮次有缺口且末轮完整、并发新增、翻页去重、跨设备、跨课堂/账号、授权撤销及原音到期/删除。单项与列表中的同一轮次应一致。特别检查 `capture.py::manifest_value` 当前把非空 `unknown_provider_ranges` 压成分片序号，而现有契约要求 `start_sample/end_sample`；两个入口都要按实际采样范围输出，并用非空样本过 schema。
- **前端验收**覆盖刷新/换设备无本机 epoch、第一页之后才出现的缺口、空页、续页失败及列表刷新。逐轮标记缺失区间，分片序号只在各自 epoch 内解释。完整性判断须等到末页，同时参考课堂级 `flows.upload`；本机待传、服务端缺片和供应商状态未知分别展示。空列表或中途失败均不能把整节课标为原音完整。

## PR-06c 重转写错误

| HTTP | `error.code` | 确认事实 | PR-10b 行为 |
|---|---|---|---|
| 410 | `AUDIO_EXPIRED` | 所选原音已到服务端保留期限 | 提示原音已到期，停止本次重转写 |
| 410 | `AUDIO_DELETED` | 所选原音已删除、进入删除，或已明确撤销原音读取 | 提示原音已移除或已撤销访问，停止本次重转写 |
| 409 | `QUOTA_SESSION_EXCEEDED` | 本节课已用过一次且没有有效的追加审批 | 提示需要课次审批，保留当前选择 |
| 409 | `QUOTA_MONTH_EXCEEDED` | 账户本月重转写时长余额不足以预留所选范围 | 提示本月时长不足，用户可重新选择范围或等待额度恢复 |

适用边界是 `asr_retranscribe` 的准备、执行、显式重试，额度拒绝发生在执行或重试的准入阶段。预览仍可返回现有 `source_status=unavailable`。先鉴权，再从删除记录、截止时间、课次审批和 `retranscribe_month_ms` 账本等已证实原因映射。课堂墓碑、一般来源缺失、授权不足、平台预算或其他资源额度继续使用各自的原码。检查 06c 的 `_sources_status` 与 `source_changed` 对新码的处理；保留 `PLAN_EXPIRED` 与原音到期的区别。四码均为 `retryable=false`，客户端仅按 `error.code` 分支。旧服务的 `GONE`、`QUOTA_EXCEEDED` 和未识别码显示通用提示，不从 `message` 推测成因。

生产者验收要求四个真实 HTTP 合成场景，拒绝后不新建任务、预留或供应商尝试。消费者验收将四种错误的文案设为相同，并更换本地化文案，检查仍显示对应操作。来源缺失、其他资源额度和预算拒绝不能误入上述分支。

## 第 3 节四项排期

按下游需要的最晚节点排期。四项均不阻塞 PR-10a 当前 Draft。

| 原编号 | 交付节点 | 技术分工与关闭证据 |
|---|---|---|
| 3.1 AI 解释结果 | PR-07a 解释能力接入前完成资源裁决，随后在 PR-10a 解释栏真实接线前验收 | Codex 先列出已持久化结果及权限、删除行为；Astra 定义成功任务的 `result_id` 指向、读取路径/复用类型和失败语义；Claude 以“成功任务 → 可授权读取的解释正文”fixture 验收。在此之前只显示任务状态。 |
| 3.2 07a 引用字段 | 排入 **0.3.0 后的下一次破坏性契约窗口，目标 0.4.0**，在 PR-10b 来源阅读联调及课堂问答开启引用前冻结 | Codex 对照 `pr07a-1` 移植完整增量。Astra 审核 `Question.cited_source_ids`、`SourceSnapshot.items`、可空时间、来源类型与单问题 GET；Claude 用历史数据、空引用和有引用样本验证别名能定位到同一快照，随后移除相应候选依赖。 |
| 3.3 课堂事件 WS | 排在 PR-10a 真实字幕联调前，可与 0.3.0 消费者适配并行 | 现有架构 §3 与 `entrypoints/http.py` 已定义同源 `/api/web/v1/ws/events`，子协议 `classroom.v1`，首帧 `authenticate`。现实现发给已鉴权主体，events 票据不带课堂/epoch/fence；前端按事件的课堂归属分派。Astra/Codex 补票据请求、连接、`resume.after`、重放/重同步及跨课堂示例，Claude 以两个课堂和重连验证。 |
| 3.4 三项授权版本来源 | 在 PR-05b 采集策略与 PR-10a 准备页真实 acquire 联调前冻结 | 当前后端按 `capture_policy` 比对三项版本。Codex 提供服务端发布的版本与对应授权文本来源，Astra 审定读取接口、版本匹配与失效规则；Claude 使用所展示文本的版本发起 acquire，以策略更新后拒绝旧版、刷新文本再确认两种场景验收。 |

[HTTP 契约](../architecture/contracts/openapi.json) · [契约变更记录](../architecture/contracts/CHANGELOG.md) · [合成 fixtures](../architecture/contracts/examples.json) · [07a 候选差异](../pr07a/CONTRACT.md) · [事件连接与重放](../architecture/04-CONTRACTS-FLOWS.md)
