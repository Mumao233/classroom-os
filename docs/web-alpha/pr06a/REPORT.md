# Local simulation evidence — 2026-11-06

## Second independent-review repair

SIMULATION ONLY. Checkout HEAD `c80fca0e177597b40d02125ebea7f1b03d08ab1f`,
branch `feature/web-pr06a-pilot-20261106`. Restricted Windows cmd.exe; no
dependencies installed. The initial Git status was clean. Only previously
added POC files in authorized directories are edited in this repair.

Committed-final revisions now advance only on successful final commits;
partials have a separate revision map. Terminal success requires committed
final evidence and no segment whose newest partial exceeds its committed final.
No-transcript outcome proofs are unsupported. Terminal-only positive query
evidence cannot resolve unknown unless finals already committed before loss.

Five added regression tests cover partial revision 3 followed by final revision
2 with an injected commit failure (failure consumed, stable retry committed,
newer transient evidence still blocks success), a distinct partial-only segment,
terminal without finals, repeated terminal-only/partial-only reconciliation,
and successful reconciliation against a final committed before outcome loss.

| Command | Actual result |
| --- | --- |
| `python -B -m unittest discover -s web/backend/tests/asr_sim -p test_*.py -v` | Exit 1; 18 tests, 17 passed, schema/FormatChecker test errors: missing jsonschema |
| `python -B tools/pr06a/inspect_contract.py` | Exit 0; frozen contract inspection |
| `git diff --check` | Exit 0 |
| `python -B tools/pr06a/check_repair_scope.py` | Exit 0; authorized-directory inventory below 30 files / 500000 bytes |

The mandatory full-discovery acceptance gate remains **unpassed locally**.
The trusted harness dependency directory is not on this worker's import path;
the controller must supply its isolated jsonschema dependency and rerun full
discovery. No assertion was skipped, relaxed or substituted. No successful
schema-validation result is claimed. Shell startup attempts with PowerShell
and Bash failed; cmd.exe worked. Two optional inline Python inspection commands
failed with shell quoting SyntaxError; they changed no files. Frozen sources
were read directly and through the existing contract inspection helper.

No commit, push, PR, networking, credentials, other agents/models, microphone,
provider, real data or database operations occurred. All proofs remain synthetic
memory facts; no real quality, cost, SDK, persistence or device validation.

## Independent-review repair

SIMULATION ONLY. Repair checkout HEAD:
`956d27bff24989a0ec7f341926775df79a5fa970`, branch
`feature/web-pr06a-pilot-20261106`. Python 3.10.4, restricted Windows cmd.exe.
The earlier base/results below are historical, not this repair's acceptance.

Four regression tests now cover repeated batch validation failures followed by
safe completion with stable event IDs, reconciliation validation errors retaining
unknown followed by corrected synthetic proof, contiguous multi-frame coverage
and out-of-range boundaries, and partial/final revision ordering. Batch preflight
prevents any earlier final from committing before a later validation error.
Query restores unknown on exceptions as well as unsuccessful commits.

| Command | Repair result |
| --- | --- |
| `python -B -m unittest discover -s web/backend/tests/asr_sim -p test_*.py -v` | Exit 1; 13 tests, 12 passed, 1 error: missing jsonschema |
| `python -B tools/pr06a/run_stdlib_tests.py` | Exit 0; 12 passed; schema gate explicitly not run |
| `python -B tools/pr06a/check_scope.py` | Exit 1; helper assumes no tracked edits, but this repair edits the previously added POC files |
| `python -B tools/pr06a/check_repair_scope.py` | Exit 0; 10 regular files in allowed directories, below 500000 bytes; four tracked edits and one new helper, all allowed |
| `git diff --check` | Exit 0; no whitespace errors |

The schema/FormatChecker assertion is unchanged and mandatory. No installation,
network access or assertion skipping was used to claim a full pass. A successful
full trusted-harness run with its isolated jsonschema directory is still required.
No publication, commit or external service invocation was performed.

## Historical initial implementation results

SIMULATION ONLY. Worker base HEAD:
`1cc35d5db3117bce0585acbd9d9c7aa8d2d0498b`.
Windows restricted worker, Python 3.10.4 at `C:\Program Files\Python310\python.exe`.
No dependency installation or networking occurred.

| Command | Actual result |
| --- | --- |
| `python -B tools/pr06a/inspect_contract.py` | Exit 0; read-only frozen payload/ASR inspection |
| `python -B tools/pr06a/run_stdlib_tests.py` | Exit 0; 8 independent tests passed |
| `python -B -m unittest discover -s web/backend/tests/asr_sim -p test_*.py -v` | Exit 1; 9 tests, 8 passed, 1 import error: jsonschema unavailable |
| `git diff --check` | Exit 0 for tracked diff; new-file checks/controller validation still required |
| `python -B tools/pr06a/check_scope.py` | Exit 0; 9 new regular files, allowed paths, under 500000 bytes, no existing tracked diff, no trailing whitespace |

The first full-discovery attempt failed at module import with the same missing
jsonschema. Import was moved into the contract test so independent tests can
execute; no contract assertion was removed, replaced, or skipped. Full discovery
continues to fail visibly in this restricted environment. The trusted controller
must run it with its isolated jsonschema directory. No full acceptance claim.

Default PowerShell could not launch in this sandbox; read/test commands used
installed cmd.exe instead. Root TESTING.md does not exist; the Web index points
to docs/web-alpha/TESTING.md, which was read along with historical acceptance.
Skills under C:/Users/Mumao/.codex are policy-denied and were not read.

Fault coverage includes literal guards, send lifecycle, PCM format/hash/ranges,
WAV rejection, boolean integers, queue/frame/text/script bounds, no terminal,
commit failure/retry identity, known failure, lost outcome, unsupported query,
positive query proof and failed reconciliation commits, reconnect overlap,
revision ordering, distinct equal-text identities and conflicting revision
quarantine. Mandatory contract assertions cover generated partials and finals,
both timestamp sources, invalid UUIDs and forbidden wire simulation fields.

All storage/commit/query proofs are synthetic memory facts. No ASR quality,
cost, SDK compatibility, persistent recovery, database, browser or device claim.
No original regression tests or remote CI were run. Independent review,
immutable-diff checks and publication remain the controller's responsibility.
