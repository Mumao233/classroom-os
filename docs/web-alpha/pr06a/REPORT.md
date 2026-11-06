# Local simulation evidence — 2026-11-06

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
