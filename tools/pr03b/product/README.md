# PR-03b disposable product checks

> **2026-11-12 current policy:** The user retired the WSL Ubuntu test instance and requires all subsequent related tests to run inside VMware. Follow `J:\class-assistant\AGENTS.md` and the existing `D:\Codex\Projects\vmware-cli` tooling. The WSL commands and host test commands below are historical reproduction notes, not current execution instructions. Do not recreate Ubuntu or fall back to WSL/host tests when guest credentials or a VMware adapter are missing. Since 2026-11-13 the backend, frontend and real-browser suites run in the Windows guest through `tools/pr03b/vmware/` (start `broker.ps1` in your own terminal, then queue jobs with `submit.ps1`); the real-browser test is now guest-native and allow-lists only that run's server key, so it does not exercise hostname or chain validation. The Linux capacity and R05 tooling below has no guest adapter and its WSL route must not be used. Keep previous source snapshots, thresholds, failures and evidence unchanged.

These runners manage only new synthetic databases and owned process trees. They never use a default DB, real classroom content, production identity registrations or paid providers. All configurable assets live in the existing J: checkout work/pr03b/product-completion-20261110-01 (W); evidence is outputs/pr03b/product-completion-20261110-01. Run from repository root. Keep historical failed runs.

## Windows

Use W/venv/Scripts/python.exe (Python3.12, exact web/backend/requirements.lock), and the previously verified native PG17.11 bundle at work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin. `test_backend.py full` creates a random literal-loopback non5432 cluster, validates actual target and roles, runs all existing and new backend tests and normally stops its cluster. Modes auth/unit/browser are narrower diagnostics. The Windows kernel pipe/DPAPI checks require Windows; Linux results do not replace these.

`run_frontend.py checks` runs generation check, TypeScript, lint, Vitest, production build and production marker verification. `run_frontend.py browser` retains the original browser suite and uses installed Edge with a private output directory. Frontend dependencies are pinned in pnpm-lock.yaml and installed with explicit J: store/virtual-store; avoid wrappers that auto-reinstall a mismatched store. The browser suite's mock routes verify existing mock UI only; formal auth has a separate real HTTPS/WSS browser check.

The original local-product gate remains:

    & 'J:\class-assistant\classroom-os\work\pr00\venv\Scripts\python.exe' tools/pr00/run_checks.py --output-dir work/pr00/<new-run>

Do not edit its tests or widen a timed-out test's limit.

## Linux browser and capacity

Requires WSL Ubuntu, root only as namespace/cgroup supervisor, existing cgroupv2 controllers, UID1000 for all SUT/client processes, and prepared task-private dependencies:

- W/linux-python312/bin/python3: supported3.12 interpreter with exact backend requirements.lock; W/mamba-root retains package records. The older linux-venv3.14 run is screening evidence only.
- W/pg17-linux-installed: correctly prefix-relocated PG17.11+runtime libs, installed by task-private micromamba; conda-meta records retain exact package versions/hashes. Manual extraction at pg17-linux is a preserved failed bootstrap and is not used.
- W/linux-browser/chromium-1248/chrome-linux64/chrome and frontend Playwright1.64.1, W/node-v24.19.0-linux-x64/bin/node.
- W/linux-libs/usr/bin/certutil plus NSS/NSPR/ALSA shared libraries in W/linux-libs/usr/lib/x86_64-linux-gnu. Download records are retained in W/linux-downloads.json, linux-pg-deps.json and conda-meta.
- Current frontend production build at work/pr09a/dist. Rebuild before freezing test source.

Run (paths below are Linux paths inside WSL):

    wsl.exe -d Ubuntu -u root --exec unshare --net --mount --fork /mnt/j/class-assistant/worktrees/web-pr03b-sessions/work/pr03b/product-completion-20261110-01/linux-python312/bin/python3 -I -B /mnt/j/class-assistant/worktrees/web-pr03b-sessions/tools/pr03b/product/capacity_linux.py full

Modes browser/smoke/full/timing. Smoke and the120s-baseline/120s-normal timing mode are never capacity acceptance. Full retains original120s baseline,1200s mixed load,600s overload,180s recovery; 30 synthetic students,30 actualWSS,10 business reads/s+1 login/s,32/s authenticated reauth overload. Account/source/preauth budgets remain active; no resets or lowered costs. Only loopback exists in the network namespace, with no routes. SUT(PG+twoAPI) is2CPU/4GiB/swap0, separate same-host client4CPU/2GiB. Temporary PGdata/WAL and TLS private key are volatile tmpfs; PostgreSQL fsync/WAL are enabled. This is not persistence-crash acceptance, active-worker coexistence, a deployment VM or a process-count matrix.

Business transport uses business_client.py in a separate process/event loop inside the SAME4CPU/2GiB client cgroup; authenticated WS, fresh full-login and reauth generation remain in the parent. Both use the same planned phase timestamps and original rates, source address and30 real synthetic sessions. Cookies move only through private anonymous stdin and never enter argv/logs. This avoids measuring unrelated TLS/client event-loop work as business-request latency. Earlier shared-event-loop runs and their failed20% deterioration points remain archived; the short independent-client diagnostic does not replace a full run. The ASGI timing header exists only in the synthetic harness and supplements, rather than replaces, end-to-end measurements.

BUSINESS-REQUESTS.jsonl is the worker's raw stream. The parent merges every row into REQUESTS.jsonl at each drained phase boundary; use REQUESTS.jsonl ONCE for sample counts and use recorded monotonic timestamps for ordering. Do not add both files' counts. Generator admission errors, missing scheduling observations and failed statuses remain visible; child exit and cgroup cleanup must also succeed.

The supervisor freezes source/dist/harness and SHA256 before boot, writes RUN/FINAL and per-stage records, drains owned API/PG, confirms empty cgroups and unmounts its tmpfs. Check FINAL rather than treating supervisor exit alone as acceptance. Browser receives a private mount namespace NSS store with only this run's CA; invalid hostname must fail. No TLS-ignore/SPKI-bypass flag is used and no host trust store is modified.

Run `analyze_capacity.py <outputs/.../capacity-run>` using the native task Python after completion. It preserves every request, error, timeout and rejection. Wrong-password and missing-account strata remain separate. Full-queue reject events correlate to exact reauth operation IDs and actual hash count; queue timeouts are reported separately. Normal hash strata, all-outcome HTTP latency, scheduling lag, memory limits and weighted occupancy trajectories remain visible. Each claimed p99 requires N>=1000; point estimates without confidence intervals cannot close the original tail uncertainty requirement. Same-host interference and frozen-source differences must be reported.

## R05 renewal and finite process-count probes (2026-11-11)

The user approved updating preauth through the public protocol and waiting for natural budget expiry within a same-target sequence. Runtime account3/900s and preauth300s, independent sample minima and statistical limits remain unchanged. Historical schema2 and its closed collector remain intact; schema3 is in `mechanisms/enumeration_renewal.py`.

`enumeration_report.py plan --seed 20261111` prints an immutable preregistration, readiness and explicit resource assumptions. `enumeration_report.py analyze --plan <saved registration> <rows...>` rejects modified requirements and functional splits. The offline CLI cannot certify external campaign controls. `test_enumeration.py` uses a private fake clock for fault cases only.

Use the same isolated `capacity_linux.py` command above with mode `r05_probe` to run four functional arms: known/unknown quota controls and known/unknown natural-expiry sequences. It waits real902s, records every public request and missing observation, alternates between two real API processes and correlates actual hash calls. It does not launch the full statistical campaign or claim R05 PASS. `r04_probe 1`, `r04_probe 2`, and `r04_probe 4` run finite16/32-request bursts under the original shared2 execution/8 waiting slots and2CPU/4GiB SUT limits, followed by explicit fresh recovery requests. They do not replace full mixed load, worker coexistence, tail confidence or deployment-platform acceptance.

These modes use only the supervisor's fresh synthetic cluster and own namespaces/cgroups/CA. Final cleanup and exact executed source must be checked for every run, including failures. See [current method and limits](../../../docs/web-alpha/pr03b/R05-RENEWAL-20261111.md).

## Trust and delivery limits (continued)

No run here registers a real D0/person, enables recovery, applies a production migration, creates a PR, commits/pushes/merges or deploys. Supported runtime checks alone do not close R02/R04 full matrix, R05 enumeration or S4 independent review. The final package must retain those gates explicitly.

After all owned runs stop, `compare_source.py <run hex>` checks executed backend/dist/harness bytes, and `freeze_source.py` audits protected baseline files, unchanged HEAD and current staged content before creating the source ZIP. Git index byte identity is reported separately from staged content. `freeze_evidence.py` retains successful and failed test reports, original Windows gate logs and each executed source snapshot, runs recorded diagnostic redaction and XML integrity checks, then creates the evidence ZIP/manifest. It refuses unfinished runs or unresolved owned cgroups/tmpfs. It does not include PGdata, caches, downloaded dependencies or real credentials. Review both SNAPSHOT.json and EVIDENCE.json; later passing point estimates never remove an earlier capacity failure.
