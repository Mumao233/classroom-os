# Subscription-only unattended pilot

The approved scope is automation infrastructure plus **one simulated PR-06a**.
The controller retains existing packages 00/01 and only adds the missing 06a
simulator. Other packages are disabled; paid-mode gates are never marked passed.
The original 28-node dependency contract is unchanged. All Git authors,
committers and Draft PR authors must be Mumao233; no Codex coauthor trailers.

## Execution and authentication

The local Codex CLI uses saved **ChatGPT subscription** authentication. GitHub
Actions emits a data-only request and runs credential-free tests. Codex auth
caches never go to GitHub, artifacts, repository files or another machine.
No credential-bearing self-hosted runner is attached to public PR events.
Extra API billing is not authorized ($0); no API-key or paid-provider fallback.

Official references checked for this design:

- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode):
  saved auth, JSONL usage, ephemeral operation; its advanced account-auth CI path
  warns about public/open-source repositories. This design keeps auth local.
- [Authentication](https://learn.chatgpt.com/docs/auth).
- [Named permissions](https://learn.chatgpt.com/docs/permissions): shell network
  permissions are distinct from the model transport.
- [Windows MXC](https://learn.chatgpt.com/docs/windows/windows-sandbox): explicit
  sandbox selection fails closed. No automatic full-access fallback.

The local machine must remain awake, online for Codex/GitHub, and logged in.
Use Python 3.12 for the local controller and existing PR-01 validation environment;
the controller's unit tests also support Python 3.10. Subscription availability
and account limits can change. Exact monetary usage
is unavailable; `subscription.json` records observed tokens, unknown usage,
calls, turns, wall time and repairs. Calls reserve persistent allowance before
launch. No automatic reset or repeated call follows an uncertain outcome.

If this CLI build leaves its process alive after a successful model turn, the
executor verifies the unique completion event, exact final message, usage and
finished tool items, then allows five seconds for natural exit. It may then stop
only the owned process group/job. Receipts distinguish forced cleanup from a
natural exit and retain the actual exit code and observed usage. Missing or
conflicting completion evidence remains a failure; it never triggers a second
model call. Failed executions also retain any valid observed usage.

## Components

| File | Responsibility |
| --- | --- |
| `automation/pilot.json` | Single enabled task, model and cumulative limits |
| `automation/prompts/06a.md` | Frozen scope and failure-path acceptance criteria |
| `tools/overnight/state.py` | Locked atomic state, revision CAS, dependency/attempt identity |
| `tools/overnight/budget.py` | Subscription execution ceilings, zero extra API spending |
| `tools/overnight/executor.py` | Clean environment, restricted process, timeout, auth/probes |
| `tools/overnight/controller.py` | Serial develop/test/review, at most three repairs |
| `tools/overnight/validation.py` | Immutable Git objects, addition-only path/content manifest |
| `tools/overnight/publish.py` | Verify personal GitHub identity, push and Draft PR reconciliation |
| `tools/overnight/queue.py` | Read a successful Actions request at a pinned control SHA |
| `tools/overnight/check_contracts.py` | Current generic contract checks; old PR-01 validator retained |
| `tools/overnight/ci_backend.py` | Disposable native PG17, original full backend runner |

Control state/evidence stays in ignored `work/overnight/runs/<run-id>/` on the
existing J: project drive. This is local persistence, not a cross-machine store.
Back up that directory through the user's own approved local backup practice.
Raw model logs remain local and are not published automatically.

## Bootstrap and operation

First review the infrastructure Draft PR. The pilot can stack its Draft on the
infrastructure branch without merging it. GitHub schedules and workflow_dispatch
normally require the workflow on the default branch; **this change does not
merge or activate a default-branch nightly schedule**. A push to the infrastructure
branch produces the initial request and tests. The local queue checks successful
run SHA, workflow path, event, repository, owner actor and strict request schema.

Use an approved, clean infrastructure checkout at a full commit SHA. Replace
`APPROVED_SHA` with that exact reviewed commit, and retain the same run ID on
resume. `--base-ref` must identify the infrastructure feature branch at that SHA.
`work/pr01/validation-deps` is an existing isolated local dependency directory;
on another machine install `automation/validation-requirements.txt` in ignored
work storage first.

```powershell
$pilotPython = 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $pilotPython -B -m unittest discover -s tools/overnight/tests -p test_*.py -v
& $pilotPython -B tools/overnight/check_contracts.py --repo . --deps work/pr01/validation-deps
& $pilotPython -B -m tools.overnight.queue --source . --base APPROVED_SHA --base-ref feature/overnight-pipeline-20261106 --run-id pilot-20261106 --deps work/pr01/validation-deps --publish --watch
```

The watcher polls once per minute and consumes one matching request. This pilot
is deliberately one-shot: rerunning its stable run ID reconciles existing state
and PR instead of beginning another development task. Changing the run ID to
bypass an exhausted/uncertain ledger is not authorized. After completion no
daemon or OS scheduled task remains. For an explicitly local initial trial the
same controller can run directly with identical flags, omitting `--watch`:

```powershell
& $pilotPython -B -m tools.overnight.controller --source . --base APPROVED_SHA --base-ref feature/overnight-pipeline-20261106 --run-id pilot-20261106 --deps work/pr01/validation-deps --publish
```

The publisher obtains the existing GitHub credential through Git Credential
Manager in memory, validates `/user` against Mumao233's numeric identity, then
only permits approved feature-branch pushes and Draft PR creation. Tokens are
not placed in command arguments, files, Git configuration, reports or logs.
The worker receives no GitHub credential; candidate tests also execute inside
the sandbox. The publisher independently revalidates the exact Git objects.

## Failure and review

| Trigger | Result |
| --- | --- |
| Auth/quota/rate error, unsupported sandbox, timeout | Stop, preserve local evidence; no API fallback |
| Existing unresolved model call | Block; no duplicate invocation |
| Out-of-scope or protected change, empty diff, symlink/submodule, excessive patch | Fail before publication |
| Failed tests or independent review | At most three bounded repair rounds |
| Dependency/base changes, conflicting remote branch/PR | Stop for explicit reconciliation; no force push |
| Controller interrupted during a settled stage | Resume the same state and completed-call receipt |
| Crash between initialization or publication steps | Preserve state; reconcile exact objects/PR, never blindly restart |

`tools/overnight/recover.py` is an explicit operator repair for the observed
first-launch MCP transport configuration error only. It requires empty model
stdout, the exact startup error, unchanged clean source/candidate mappings and
an unpublished candidate. Its journal retains the original failed reservation,
clock, limits and logs while binding the same run ID to a corrected descendant
infrastructure commit. It cannot recover quota/auth failures, uncertain model
execution, exhausted limits or a modified candidate. It is never an automatic
fallback and does not refund the failed reservation.

The controller's local gate includes nonzero synthetic tests with zero skips,
frozen-contract static checks, scope validation and independent read-only model
review. The reviewer receives the passing harness result and logs bound to the
exact candidate SHA and patch hash. Missing packages in the review shell are
recorded as a rerun limitation, while real defects still block acceptance.

Tests and review now use fresh independent repositories materialized directly
from the validated commit's regular Git blobs. They never execute in the worker
directory. Checkout filters, ignored files and a dirty worker index cannot enter
these copies. The controller verifies the complete source manifest, Git identity
and absence of extra files before and after execution; the local sandbox grants
read-only source and a separate writable scratch directory.

Validation dependencies must be disjoint from the worker/run/control roots and
must not contain `classroom_web`, links or reparse points. Their complete content
manifest is fixed in `dependencies.json` and checked again after execution.
The trusted gate verifies the simulator's actual import origin in the snapshot.
Candidate discovery and controller-owned ASR/schema acceptance assertions run in
separate processes. Both must provide one complete structured session result,
with every discovered test completed and zero failures/errors/skips. A process
exit of zero alone, missing/truncated result or early `os._exit(0)` fails the gate.
These receipts are not a proof against arbitrary malicious Python in the same
interpreter; the independent trusted phase never imports candidate test modules.
The hosted explicit ASR gate applies the same parent-side completion validation.

Before initialization or generic failure reporting, ordinary resume checks for
an incomplete/invalid `prelaunch-recovery.json`. It exits without changing state,
ledger, scope, journal or report, and directs the operator to finish the dedicated
recovery on the same run ID. Completed journals remain historical evidence.
GitHub additionally runs original Windows Python/Node/DPAPI gates and
the original PostgreSQL backend suite on Linux/Python3.12/PG17. These are distinct
evidence sets. A local pass is not a remote CI pass, and mock guards do not prove
database durability or real-service fees/quality.

`report.json`, `candidate.json`, `contracts.json`, `tests.json`, review receipts,
`subscription.json`, and `publication.json` contain the overnight handoff.
Review infrastructure first, then its stacked 06a Draft and all GitHub checks.
Neither PR is auto-merged. Real ASR remains blocked by 03b/04a/05b/11a and the
documented product/payment gates. No frontend, deployment, release or real
classroom data is part of this pilot.
