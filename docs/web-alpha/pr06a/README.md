# PR-06a simulated ASR protocol POC

**SIMULATION ONLY.** Contract baseline 0.1.0; wire protocol_version 1.
Base: `1cc35d5db3117bce0585acbd9d9c7aa8d2d0498b`, branch
`feature/web-pr06a-pilot-20261106`. No frozen source was changed.

The pure Python 3.10 stdlib implementation is in
`web/backend/classroom_web/asr_sim/simulator.py`. Import `Simulator` from
`classroom_web.asr_sim` with `web/backend` on sys.path. There is no server,
provider SDK, audio device, database, thread, wall-clock dependency or I/O.

## Interface and synthetic inputs

`start(config)` requires literal `True` for all four guards: `durable_audio`,
`budget_permit`, `consent`, `dispatch_intent`. Supply canonical UUID strings for
`class_session_id`, `capture_epoch_id`, `stream_id`. These injected facts do not
implement authentication, consent capture, durable storage or a budget ledger.

`send(frame)` accepts a dict containing bytes `pcm`, `encoding='signed16le'`,
`sample_rate=16000`, `channels=1`, `start_sample`, `end_sample`, `sample_count`,
`byte_length`, and lowercase `sha256`. Ranges are half-open and contiguous within
each stream; the first range may start after a gap. All integer fields reject
booleans. Bytes must be nonempty, even, correctly hashed and exactly twice the
sample count. RIFF/RIFX/RF64 containers are rejected. This validates a declared
format; it cannot detect a caller falsely declaring an arbitrary raw waveform.

`finish()` only enters draining and refuses new frames. `events()` returns a
bounded batch of actual frozen-schema wire payloads. It advances the injected
`script`, and may be called before finish for partials/finals. A terminal action
waits for draining. There is no success until the terminal action is consumed
after all earlier finals have committed in the synthetic memory model.

Script transcript actions use `kind` (`partial` or `final`), UUID `segment_id`,
positive `revision`, nonnegative `sequence`, `start_sample`, `end_sample`, `text`;
finals also require `timestamp_source` (`provider_segment` or `capture_mapping`).
Each segment must fit a frame sent on that stream. Control actions contain only
`kind`: `terminal`, `lost`, or `failure`, and must be last. No script action
causes an external operation. `failure` represents injected known negative
evidence; `lost` represents a sent/outcome-lost fault and enters unknown.

`commit_failures=N` fails the next N synthetic final commits without publishing
them. `events()` retries the same queued final, preserving UUID5 stable IDs.
Deduplication uses stream/segment/revision, never text. Higher revisions advance;
older revisions cannot regress. Same-revision conflicts are quarantined as
identity tuples, retaining the original committed result. Quarantine is exposed
as a read-only snapshot. Finals carry event/cursor/version identifiers; partials
carry only connection sequence. Timestamps use a fixed synthetic UTC instant;
sample-to-ms capture mapping truncates sub-ms fractions.

`query(receipt)` defaults to `{'status': 'unsupported'}` and preserves unknown.
A receipt comes from start/send/finish and identifies the current stream.
Optional `query_result={'proven': True, 'script': [...]}` injects synthetic
positive outcome evidence ending in terminal. Only this evidence plus successful
final commits resolves unknown. Failed reconciliation commits remain unknown;
repeat query uses the same identities. No `not_found`, resend, refund, billing
settlement or provider switching path exists.

## Reconnect guard and bounds

Reuse the **same Simulator instance** for sequential reconnects in one capture
lineage. `start()` requires a new stream UUID and the same session/epoch. Its
private sent-range ledger survives reconnect and cleanup, rejecting every
overlapping previously sent range, including unknown ranges and subranges.
It conservatively also blocks already known sent ranges. A new stream may send
only previously unsent ranges; it cannot produce finals for prior-stream audio.
Prior receipts must be reconciled before reconnect if query is desired: this
small interface does not retain query contexts for abandoned streams.

This guard is in-memory only. A new object is a new synthetic lineage, never
crash recovery. Production recovery would need an authoritative persistent
range/attempt ledger; this POC makes no such claim.

Hard limits: 16 streams/lineage, 128 frames/lineage, 32,000 bytes/frame,
128,000 pending bytes, 128 actions/script, 20,000 characters/text. Query script
has the same limits. Bounds are checked before acceptance/copy; backpressure
leaves caller audio untouched. No PCM bytes are retained. Pending-byte accounting
is released on known failure, unknown or terminal; sent evidence stays bounded
and retained. Empty PCM is rejected rather than interpreted as a heartbeat.
Exhausting a lineage requires explicit caller handling, never an automatic reset.

## Reproduce and scope

From repository root, with trusted jsonschema available:

```text
python -B -m unittest discover -s web/backend/tests/asr_sim -p test_*.py -v
```

Tests add web/backend explicitly to sys.path. The schema test loads the actual
frozen events.schema.json and uses Draft202012Validator with FormatChecker;
missing jsonschema is a hard error, never skipped or installed by this task.
`python -B tools/pr06a/run_stdlib_tests.py` explicitly reports only its independent
stdlib subset; it is not the full acceptance gate. `-B` avoids bytecode artifacts.
See [actual local results](REPORT.md).

Traceability: architecture 04 ASR adapter/PCM/reconnect semantics, frozen ASR
state-machine start/finish/terminal/unknown/reconcile guards, frozen transcript
partial/final schema, implementation package 06a. This does not implement 06b,
05a/05b or 11a. No real ASR quality, cost, streaming SDK, PostgreSQL durability,
microphone, browser, remote CI or existing regression-suite validation is claimed.
No dependencies, contracts, migrations or existing tests changed. Rollback is
removal of this explicitly added file set only; no data operations are involved.
