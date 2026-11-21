# PR-06c → PR-10b Review 与采用交接

沿用 [OpenAPI 0.2.1](../architecture/contracts/openapi.json) 与 [events v1](../architecture/contracts/events.schema.json)。PR-10a 使用的实时流、任务、计划、版本和片段字段保持原形状。

| 交互 | 现有接口和处理 |
|---|---|
| 准备重转写 | `POST /class-sessions/{id}/generation-plans`，`kind=asr_retranscribe`，使用 `all` 或 `range`，`seen_sources=[]`，`include_images=false`。计划提供冻结 `audio_manifest_sha256`、`audio_chunk_ids`、月额度计量的毫秒数、最高费用及有效期。 |
| 确认提交 | 展示冻结范围后走 `POST /generation-plans/{id}/execute`。部分片段须确认 `confirm_partial_scope`。原音选择变化可重做计划，或显式 `confirm_original_snapshot` 后发送原计划。 |
| 失败与拒绝 | `GONE` 提示原音过期或已删除，使用现有 `error.message` 区分。课次与月额均可返回 `QUOTA_EXCEEDED`，课次的文本明确需审批。合成审批仅有受限内部入口，浏览器保持待审批状态。 |
| 进度与未知 | 使用 `GET /tasks/{id}`、`task.changed` 和任务的 `attempt_id`、`may_have_charged`。reconcile 返回 `provider_query_supported=false`、`new_submission=false`。可能重复计费的重试需用户确认 `original_attempt_id` 和 `confirm_duplicate_billing`，且额外课次已获审批。异常后保留原 attempt。 |
| 比较候选 | 成功任务的 `result_id` 是新版本 ID。刷新 `/transcript-versions` 与 `/transcript-versions/{id}/segments`，按时间和分片来源对照 `kind=retranscription`、`origin=retranscription`。候选片段 `stream_id=null`，采集偏移用 `timestamp_source=capture_mapping`。 |
| 显式采用或切回 | 使用 `POST /class-sessions/{id}/adopt-transcript`，传 `transcript_version_id`、课堂 `base_revision`、CSRF 和稳定幂等键。成功返回新课堂修订与采用 ID，版本列表显示 adopted。冲突刷新课堂与版本再选择。新的 QA、Review 和导出读取当前采用版本及独立个人来源。此前快照和个人稿仍可打开。 |

合成完成事件来自现有任务事件。候选比较界面应在任务完成后刷新版本。采用后使用课堂 `object.changed` 或读取新课堂修订同步界面。真实供应商产出质量、格式与时间戳行为留待另行授权的独立 POC。
