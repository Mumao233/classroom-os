# Independent review corrections

The independent review of PR #2 `0906bff…` and PR #3 `5546a292…` requested
changes. Its reproduced counterexamples supersede the first pilot's model
approval; the old run evidence and subscription ledger are retained unchanged.

| Finding | Correction | Regression evidence |
| --- | --- | --- |
| I-01: ignored worker files can influence tests but be absent from publication | Materialize fresh independent repositories from exact Git blobs for both tests and review; read-only sandbox source, external scratch, complete file/Git manifests; fixed isolated dependency manifest and import-origin checks | Real synthetic Git repositories cover ignored helpers, dirty worker/index, global ignores/filters, extra ignored files/directories, source drift and unsafe metadata; dependencies cover overlap, module substitution, links and content drift |
| I-02: early exit 0 can impersonate complete testing | Candidate discovery and trusted controller assertions run in separate processes; the parent requires unique complete structured records and positive, equal discovered/run/completed counts with zero failures/errors/skips | Real subprocesses cover normal discovery, zero tests, skips, SystemExit, os._exit during import and execution, missing/truncated/duplicate records, wrong session binding and independent rejection of a broken simulator despite passing candidate tests |
| I-03: ordinary resume can corrupt an interrupted operator recovery | Read-only journal guards before initialization and before generic exception writes | Interrupt after journal, workspace, state, ledger and scope checkpoints; ordinary main must preserve every checkpoint; dedicated recovery remains resumable; completed journals allow normal progress |

The explicit hosted ASR gate also reconstructs the tracked source and validates
completion in its parent process. Cloud regression, original backend tests and
Windows/Node/DPAPI checks still run separately. Local test results cannot stand in
for the new commit's actual CI. Final run links and exact counts are recorded in
the PR descriptions and the local repair receipt under `outputs/overnight/`.

This follow-up repairs code and uses synthetic verification; it does not rerun
the subscription model pilot, reset its ledger, activate a schedule or authorize
another business package. Both PRs remain Draft pending independent re-review.
