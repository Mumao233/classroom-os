# Configured development workflow (v2)

The reusable workflow stays in Classroom OS. It reuses the subscription executor,
locked state, usage ledger, exact Git object validation, immutable verification
copies and personal-account Draft publisher. The original v1 pilot and its
receipts remain unchanged and readable; it is not rerun or migrated.

This change authorizes workflow engineering and synthetic acceptance only.
The checked-in plan enables **06a audit only**: verified existing simulated work
is recorded as SKIPPED without a model call or new PR. **05a is a disabled future
template**, not an implementation or an approval. Loading all 28 DAG nodes does
not enable them. N-02 stays a non-blocking P3.

## Configuration and authority

`automation/workflow.json` is validated against the strict contract implemented by
`task_config.py`, with its JSON Schema in `automation/workflow.schema.json`. The
plan records a stable authorization ID, explicit enabled tasks, batch limits and
accepted existing baselines. Each task specifies goal/acceptance, exact branch and
base, three dependency classes, gate phases, add directories/modify/delete exact
files and denials, test argv/completion requirements, independent review criteria,
model/effort and task time/call/shared-repair ceilings.

The exact actual `pr-dependencies.json` supplies all 28 nodes and all hard,
integration and paid edges. Task edges must equal that contract. Gates are
explicitly partitioned by the trusted operator into development, enablement and
paid phases; the worker cannot change the partition. POC/implementation requires
hard edges and development gates; integration also requires integration edges;
paid also requires paid edges/gates; activation requires all edge classes plus
enablement and paid gates. Deferred gates remain visibly unapproved in reports.

The controller SHA is a required full CLI pin. The plan, schema, contract, audit
evidence and trusted test entrypoints come from tracked objects at that same
clean control commit. No self-referential SHA is embedded in the plan. Changing
the plan, control version or contract during a run is rejected before state is
rewritten; do not change the authorization ID to bypass an interrupted ledger.

Existing implementation audit is an explicit trusted record with source head,
tree and hashed evidence, not a model claim that a filename means COMPLETE.
The bundled baseline receipt records the actual accepted b71e08d main CI and its
scope limitations. Only valid COMPLETE evidence can produce completion-type
SKIPPED. Other statuses are PARTIAL, IMPLEMENTED_UNVERIFIED, NOT_STARTED or
BLOCKED; development prompts require reuse and verification before new work.

Hard controller/configuration/contract paths and existing tests remain protected.
Only explicit exact files may be modified/deleted; new task tests can be added.
Overlapping allow/deny grants are rejected instead of creating ambiguous writable
parent directories. Source/object checks remain authoritative after execution.

## Lifecycle and scheduling

`AUDIT → READY → DEVELOPING → TESTING → REVIEWING → READY_FOR_HUMAN_REVIEW`.
Verified existing work is SKIPPED; failures become BLOCKED or FAILED. All model
work remains serial. Each successful development turn is committed under the
operator's Mumao233 identity, tested on a fresh exact tracked-only read-only copy,
then reviewed in a fresh ephemeral read-only Codex session. Configured tests must
include an independent trusted controller-side acceptance entrypoint. Structured
completion requires positive, equal discovered/run/completed counts and zero
failures/errors/skips; exit zero alone never proves a test gate.

Development semantic issues, test failures and review findings share one
StateStore repair count, at most three repairs after the initial attempt. Scope
violations fail the task before publication. Ordinary task blocks/failures stop
their dependents and permit other independent explicitly enabled tasks. No ready
task means generate the report and exit; no new daemon, scheduler or service is
installed.

READY_FOR_HUMAN_REVIEW and a Draft PR are **not human acceptance**. Prerequisites
need either a verified already-accepted baseline or an explicit operator approval
for their exact head. Multiple prerequisite branches need a human-confirmed
common base containing every prerequisite head; the controller never merges
branches to create one. Failed, blocked or invalidated evidence blocks consumers.

## Human input and gates

An optional `--approvals` JSON file must be in a controller-owned directory:
`work/overnight/approvals/`, `automation/`, or
`docs/web-alpha/overnight/approvals/`. It is outside all worker write scopes.

```json
{
  "version": 1,
  "authorization_id": "THE_SAME_PLAN_AUTHORIZATION_ID",
  "gates": [],
  "dependencies": [],
  "bases": []
}
```

Records use `actor: "Mumao233"` plus nonempty `evidence: [{"path": "docs/...", "sha256": "..."}]`:

- gate: `task_id`, `gate`, `base_sha`, `actor`, `evidence`;
- dependency: `task_id`, `head_sha`, `actor`, `evidence`;
- base: `task_id`, `ref`, `sha`, `dependency_heads`, `actor`, `evidence`.

These are operator-supplied decisions; the actor string is attribution, not an
authentication signature. Never generate them from a model verdict or worker
output. Evidence must be in controller-owned documentation/configuration or the
approval directory, not any run/worker output. The controller rechecks hashes
and exact source identities and saves an append-only snapshot. Removing or
rewriting an accepted record stops resume. New independent approvals can arrive
without resetting calls or the cumulative clock. A started task cannot silently
adopt another base or changed prerequisite/approval identity.

## Limits and model settings

Only existing ChatGPT/Codex subscription authentication is used. Extra API spend
is fixed at zero, no API key/credit purchase/provider fallback. Model/effort are
task configuration, defaulting to Sol/high in the templates; the current chat's
model is unchanged. A small explicit allowlist of Codex model names prevents an
arbitrary provider or CLI configuration override.

`low`, `medium`, `high`, `xhigh`, `max` and `ultra` may be requested. Actual
model/client/account combinations are not inferred from syntax acceptance.
Ultra is not the default and has not been model-tested here; unsupported settings
stop without downgrading or switching billing. The restricted worker profile
still disables arbitrary subagents/apps/MCP/hooks and real shell network access.
See [official model guidance](https://learn.chatgpt.com/docs/models?surface=cli)
and [configuration guidance](https://learn.chatgpt.com/docs/developer-settings).

Per-task soft call/time ceilings are checked from the same persistent ledger and
task context before more work. Reaching them blocks only that task. Batch limits,
auth/quota errors, unknown calls, clock rollback and the existing ledger's hard
task/turn/token envelopes are global safety fuses; they are never unlocked or
reset to continue. Token/USD costs cannot be predicted precisely; observed usage,
unknown values, reserved calls and elapsed time remain separate report fields.

## Operation

Use a reviewed clean checkout containing the v2 code, pinned by full SHA. Keep
the checked-in authorization ID on resume. Validate before executing:

```powershell
$workflowPython = 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $workflowPython -B -m tools.overnight.workflow --source . --control-sha REVIEWED_CONTROL_SHA --deps work/pr01/validation-deps
```

With the reviewed default plan, execution only verifies accepted audit evidence
and skips 06a. It does not redevelop ASR or call the model:

```powershell
& $workflowPython -B -m tools.overnight.workflow --source . --control-sha REVIEWED_CONTROL_SHA --deps work/pr01/validation-deps --execute
```

For a later separately authorized task, review/enable its configuration, supply
required approval receipts and optionally pass `--publish` to create validated
Draft PRs. `--publish` never allows main writes, merge, deploy or release.

The v2 GitHub request workflow is manual-only and emits data pinned to control
SHA, plan hash and authorization ID. It never calls a model or enables tasks.
`tools.overnight.workflow_queue` validates and durably binds that request; its
default is validation-only. With explicit `--execute`, it resumes the same plan
identity through the same Workflow. No request means report and exit. The legacy
`queue.py` remains for historical v1 request artifacts and pilot records.

```powershell
& $workflowPython -B -m tools.overnight.workflow_queue --source . --control-sha REVIEWED_CONTROL_SHA
```

The default plan pins its accepted base to b71e08d. If `main` moves, validation
stops until the operator reviews the new base and updates the configuration;
neither CI nor the queue silently advances this approval.

## Recovery and evidence

State is under `work/overnight/workflows/<authorization_id>/`; source/control and
dependency manifests, contexts, calls, tests, review output and publication intent
are separate immutable receipts. Control snapshots are isolated on the existing
J: project drive outside the worker repositories. All versions share the v1
controller process lock, preventing concurrent real model work in this repository.

Each call reserves before launch and uses a stable authorization/task/attempt/
stage/repair ID. A complete saved call settles its original reservation after a
crash without invoking the model again. Missing or uncertain output stops the
batch; do not create another run ID. A blocked active checkpoint is written before
the BLOCKED transition and resumes the same attempt, preserving all counters.
Completed tests are reused after validation; an interrupted pure test transport
can rerun in a fresh evidence directory, preserving partial logs without repeating
development. Test and review logs, completion output, snapshots, dependencies,
source SHA, scope hash and settled model usage are rechecked before reuse,
publication or accepting a prerequisite. Changed evidence blocks the task.

Publishing first persists intent. After an interrupted push/POST the existing
publisher reconciles exact remote head/base/owner/Draft rather than creating a
second PR. Conflicting mapping requires operator reconciliation. The controller
retains the first publication receipt when confirmation is interrupted and saves
subsequent matching remote observations separately rather than overwriting it.
It reports human gates and external blocks; it never turns automatic approval into
a security/product enablement decision.

Acceptance uses fake executors/fault injection, actual local Git and Python test
processes, plus regression of the retained 06a tests. No new business package,
real model development trial, production data, microphone or paid provider is
used. New Draft PR CI and exact-head evidence are recorded separately from
historical baseline results.
