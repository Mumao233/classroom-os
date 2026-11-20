# PR-06a 残余风险接受记录

决定人林珈诚。日期 2026-11-18。适用于工作包 PR-06a、Draft PR #19 的单批真实接口最小 POC。第 3 轮发现来自候选 `05559351bd709a07f7eee88fb357b749a6f49288`，原始 Astra 报告 SHA-256 为 `61987e0be5f2f59e1f883e3c9ae80229b445ecf09868590146d162ef0b1f4653`。

| 编号 | 影响 |
|---|---|
| P1-1 | 跨回执累计输出下界未保留，可延后 ¥1 观察停发。 |
| P1-2 | 计量镜像冲突时可能低估待对账敞口。 |
| P2-1 | 带一致冗余总量的重放可能被误判为新回退。 |
| P2-2 | 重复但一致的心跳标识可能导致漏记新回退。 |

林珈诚决定本次 POC 接受这四项风险，保留三次连接、每样本一次、累计 184,064 个采样（11.504 秒）和禁自动重试的硬上限。出现费用差异按控制台凭证人工核对。调用量和采样上限限制暴露，供应商账单金额上限仍未证实。¥1 继续作为本地风险预留和观察停发阈值。

以下保留原始发现内容，按表中的编号顺序排列。位置采用仓库相对路径。新候选需要自己的 Astra 定向复审通过回执，并对这四项逐条确认。

<!-- BEGIN ASTRA FINDINGS -->
- [P1] Carry the inferred output floor across cumulative receipts — web/backend/classroom_web/pr06a_real_poc/runner.py:104-110
  Severity: high
  Trigger: Before the first PCM frame, three matching `task-started` receipts report `total_tokens=180000`, then `input_tokens=0`, then `input_tokens=40000`.
  Evidence: The zero input reading establishes that at least 180,000 earlier tokens were output. When input reaches 40,000, the cost floor is therefore ¥1.05. `_meter_decision` only infers input from output, so PostgreSQL retains no output floor, prices the evidence at ¥0.87, and can permit a frame past the ¥1 stop threshold. [AGENTS.md:29](AGENTS.md#L29)
  Fix: Apply the cumulative inference in both directions. Retain the output floor derived from a fresh input ceiling as risk evidence, not settlement usage, and add this sequence to the no-frame PostgreSQL cases.

- [P1] Preserve a usage body's inferred floor when a mirror disagrees — web/backend/classroom_web/asr_dashscope/wire.py:264-268
  Severity: high
  Trigger: Canonical `payload.usage` reports 180,000 total tokens and zero output tokens, while its mirror reports only `input_tokens=0`.
  Evidence: The canonical pair implies 180,000 input tokens. The mirror puts input in `high`, so `missing not in high` skips that inference. Dispatch stops on the contradiction, but `risk_microyuan` values the tokens at ¥0.81 and the budget view shows only the ¥1 hold, below the ¥1.08 evidenced by the canonical receipt. The previous per-body inference retained that exposure. [AGENTS.md:38](AGENTS.md#L38)
  Fix: Retain the maximum of each independently supported component floor and the reported component readings. Keep the disagreement pending rather than treating the inferred split as settlement usage.

- [P2] Normalize redundant totals in the metering replay key — web/backend/classroom_web/asr_dashscope/wire.py:344-346
  Severity: medium
  Trigger: A result reports 5 input and 1 output token, a later result reports higher usage, and the first result is replayed with its optional `total_tokens=6` added.
  Evidence: Both copies parse to the same `WireEvent` and both already have an effective total of 6. But `total_floor` changes from `None` to `6`, and `observation_fingerprint` hashes the entire evidence object. `_meter_decision` treats the replay as a fresh regression, stops the batch, and advances the PostgreSQL metering revision. [AGENTS.md:38](AGENTS.md#L38)
  Fix: Canonicalize a matching explicit total and the sum of a complete, undisputed split to the same replay key. Keep genuinely conflicting totals distinct.

- [P2] Preserve heartbeat timing when duplicate identity fields agree — web/backend/classroom_web/asr_dashscope/wire.py:290-294
  Severity: medium
  Trigger: Three attributable heartbeats have distinct begin times and low, high, then low token counts. Each repeats `sentence_id: 0` twice with the same value.
  Evidence: The duplicate makes strict event parsing fail. The singleton check also discards the metering heartbeat, even though both copies identify the same heartbeat and its begin time is unambiguous. The third receipt then hashes like the first, so its regression creates neither a metering issue nor a new revision. [AGENTS.md:38](AGENTS.md#L38)
  Fix: For metering identity only, accept repeated identity or timing fields when every occurrence has the same validated type and value. Continue rejecting the packet for recognition and dispatch.
<!-- END ASTRA FINDINGS -->
