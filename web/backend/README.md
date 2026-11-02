# Classroom OS Web · data foundation and persistent task engine

Independent Python 3.12 / PostgreSQL 17 package. Contract package **0.1.0** remains
unchanged at `docs/web-alpha/architecture/contracts/`. This package does not import
the local `app.py`, instantiate its Classroom, or use its settings/data directory.

Implemented: seven tables, SQLAlchemy Core repositories behind pure domain ports,
owner UoW, CAS, immutable course material versions, fixed classroom snapshots and
atomic database revocation/tombstones. There is no HTTP server, cookie session
lifecycle or frontend in this package. PR-02b adds six execution/receipt/event
tables, a restricted scheduler and a restartable simulation worker; see below.

## Environment and repeatable tests

Run from the repository root. All selected paths remain on J:.

```powershell
New-Item -ItemType Directory -Force work/pr02a/tmp,work/pr02a/cache | Out-Null
$env:TEMP = (Resolve-Path work/pr02a/tmp).Path
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = (Resolve-Path work/pr02a/cache).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
# Existing app-bundled Python 3.12; substitute a verified 3.12 path on another host.
& 'C:/Users/Mumao/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m venv work/pr02a/venv
& work/pr02a/venv/Scripts/python.exe -m pip install -r web/backend/requirements.lock
& work/pr02a/venv/Scripts/python.exe tools/pr02a/run_tests.py --admin-url 'postgresql+psycopg://<synthetic-migrator>@127.0.0.1:<private-port>/pr02a_synthetic'
& work/pr02a/venv/Scripts/python.exe tools/pr02a/check_protected.py
```

The runner and direct pytest fixture share `classroom_web.test_support` validation.
Both require the exact `postgresql+psycopg` driver, literal `127.0.0.1`, an explicit
non-5432 port and identifier-safe `pr02a_` database/login names. They reject every
URL query parameter and every inherited `PG*` setting before creating an engine,
connecting, migrating or spawning the test process. There is no DNS `localhost`
alias, service configuration, URL option allowlist or implicit pgpass fallback.
The target must be an isolated synthetic cluster, never
an installed application database. Fixtures create a random `pr02a_test_<UUID>`
database plus a restricted random login, run migrations, and drop those two exact
objects afterwards. Missing PostgreSQL is a failure, not a skip or mock fallback.
The migrator needs database/role creation permissions in that disposable cluster.
Temporary loopback trust is suitable only for this private, short-lived test
cluster; deployed login provisioning and credentials are outside this PR.

Each run saves log, JUnit and command/result JSON in a unique `outputs/pr02a/`
folder; temporary data and pytest caches remain in `work/pr02a/`.

## Transactions and adapters

For an installed package, or with `web/backend` on the caller's import path:

```python
from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.database import Database

database = Database(explicit_runtime_database_url)
# Identity comes from trusted server authentication, never a request owner field.
with database.transaction(OwnerContext(authenticated_user_uuid)) as tx:
    semester = tx.catalog.create_semester("2026 autumn")
    course = tx.catalog.create_course(semester.id, "Economics")
    classroom = tx.catalog.create_class_session(course.id, "Lecture 1")
```

The runtime login must inherit the unprivileged NOLOGIN `classroom_web_runtime`
group. It must not own these tables or have superuser/BYPASSRLS privileges. The
UoW checks that boundary, sets transaction-local owner/search path/timezone and
locks the active user shell. Owner access is enforced again by FORCE RLS; the
runtime cannot SELECT users or create functions/tables in public. Narrow fixed
SQL functions expose only the current owner's active-state/row-lock behavior.
Treat this DB role as trusted backend infrastructure: it can set owner context;
RLS does not turn a stolen database credential into an authenticated end-user
identity. PR-03 supplies that authentication boundary.

Catalog updates require a base revision. Material edits compare the **material**
revision, create an immutable version and update the course pointer in one
transaction; metadata updates compare the course/semester/classroom revision.
Conflicts raise `RevisionConflict(current_revision)` without overwriting data.
Full draft conflict retention and operation replay belong to PR-08b/PR-02b.

Read adapters validate current ancestors and owner tombstones. Foreign/missing
IDs raise `NotFound`; proven owned revoked resources raise `Gone`. Rename and
move operations keep stable IDs; classroom moves preserve the material snapshot
and capture clock. New courses always create empty material v1. Database triggers
also reject wrong material lineage and moving out of revoked old ancestors when
SQL bypasses a Repository.

`tx.tombstones.revoke(resource, id, base_revision, ledger_sequence, erasure_epoch)`
is an internal database primitive: its caller must already have the independently
durable deletion-log sequence. It commits request, immutable tombstone and CAS
access revocation together. It is **not** a complete delete endpoint, journal,
erasure worker, media cleanup or backup recovery implementation. Do not expose it
as the PR-04b workflow. It sets no unapproved deletion deadline. General runtime
roles have no physical DELETE grants.

## Migration and compatibility

`migrations/versions/0001_data_foundation.sql` is the self-contained initial
snapshot; applying it does not import current models or load contract JSON.
Its Python/SQL files remain byte-identical to commit `272d1175`. The additive
`0002_pr02a_review` migration fixes material revocation coordination and adds
semester length CHECKs (name 100, academic year 30). Always upgrade to `head`.
Existing oversized values cause a transactional failure; no value is truncated.
`schema.py` describes the current SQLAlchemy metadata; `ddl.py` records its
authoring SQL for review. Published snapshots must never be silently regenerated;
future changes require a new migration revision.

Material revocation locks the owner, ancestor semester and owning course before
the deletion request. The course row uses `FOR UPDATE`, conflicting with a current
pointer update even when its deferred check has already completed. Immutable
material lookup takes no row lock. The repository keeps its existing exclusive
course lock before material append; it never upgrades a material read lock to a
course write lock. Both winner orders are tested with actual backend lock waits.

Alembic accepts an explicit Connection first, then an explicit Config URL. Only
the placeholder URL falls back to `CLASSROOM_WEB_DATABASE_URL`. There is no local
product configuration fallback. Review the exact target before any separately
authorized migration; this PR executed migrations only in its synthetic cluster.

The reserved `class_sessions.adopted_transcript_version_id` is constrained NULL.
Its owning later migration must add the owner/class lineage FK and remove that
staging check. No transcript tables or adoption behavior are implemented here.
`scheduled_class_ref` is also constrained NULL under the Alpha contract.

Recovery uses compatible application rollback while preserving the schema and
all learning/deletion data. `alembic downgrade base` intentionally refuses to drop
data. Transactional migration failures roll back DDL, data and Alembic version;
the integration suite injects a real SQL error after both DDL and data mutation.
An initial foundation has no prior deployed Web schema or executable Web app to
rollback to; later migrations must retain this baseline compatibility window.

Full acceptance and change inventory: [PR-02a report](../../docs/web-alpha/pr02a/ACCEPTANCE.md).
Independent-review fixes and current results: [remediation report](../../docs/web-alpha/pr02a/review-remediation/REPORT.md).

## PR-03a identity and authorization foundation

Additive `0004_identity` preserves the six published 0001–0003 migration files.
It adds invitations, pending registrations and four small internal capacity,
mail-intent, audit and activation-event tables. Users remain independent owners.
The frozen OpenAPI, event schemas and domain-model files are unchanged.

`IdentityService` takes a `PgIdentityRepository`, an injected
`Argon2PasswordHasher` and an explicit administrator reauthentication window.
Only an independently verified server `Principal` may authorize management.
There is no Principal issuer, login endpoint, cookie, password-reset flow or
administrator bootstrap in this package. MFA/session evidence must come from
PR-03b; constructing a Principal from a request is forbidden.

The identity connection uses a separate unprivileged login inheriting only
`classroom_web_identity`; it cannot read private catalog/task tables. The owner
runtime never inherits that role. All six new control-plane tables have FORCE
RLS, no PUBLIC/runtime/scheduler access, and no physical DELETE grants.
Management verifies active/verified Admin, epoch, TOTP evidence, expiration and
recent reauthentication. This is an internal use-case boundary, not a completed
HTTP endpoint with CSRF, Origin, rate-limit and idempotency middleware.

Registration stores Argon2id and SHA-256 digests of high-entropy random tokens.
Pending records have no users row, seat or learning access. Verification defaults
to 30 minutes and invitations to seven days. Activation locks capacity, invite,
then pending and commits user, both consumptions, capacity, audit and internal
event together. Same-email duplicate activation rolls back through the unique
user email constraint. No invitation or input field grants Admin.

Mail intents store only a pending reference, generation and recoverable lease.
`dispatch_mail(CapturingMailAdapter())` commits a new proof digest and lease,
sends outside the transaction, then conditionally acknowledges that generation.
Uncertain delivery recovers after a one-minute experimental lease and rotates
the secret; resend invalidates all unconsumed proofs for that email. No secret
is stored in mail intents, audit, internal activation events or generic outbox.
Only the synthetic capture adapter is provided. Real sending is not enabled.

Use `Database.authorized_transaction(principal)` for future request adapters.
It rechecks DB state, verification, role, security epoch and expiration before
exposing the existing owner repositories; owner is never a request parameter.
The original `transaction(OwnerContext)` remains a trusted internal worker/test
port. Legacy email-less shells can still support internal data foundation
fixtures, but the Principal boundary always rejects those shells. Login users
losing verification also fail raw SQL RLS and the internal owner lock.

Capacity stages are restricted to 10/30. The admission counter starts from
active accounts and is reconciled against the current active count under the
global lock. It conservatively includes Admin; it does not implement seat reuse
after disabling/deletion. These lifecycle policies remain open for review, not
silently closed by this foundation. Argon2id m=64MiB/t=3/p=1, the 60-second
resend interval and mail lease are experimental settings; TBD-08 is still OPEN.

Run `work/pr03a/venv/Scripts/python.exe tools/pr03a/run_tests.py --admin-url
<explicit-synthetic-url>` from the root. The runner verifies the exact new
worktree-owned loopback cluster before running every backend test. Full test
commands, evidence, known boundaries and rollback are in the
[PR-03a acceptance report](../../docs/web-alpha/pr03a/ACCEPTANCE.md).

### PR-03a recovery remediation

`0005_identity_recovery` adds the shared boolean eligibility check used by
owner RLS and expired-task fallback. The 0004 row-lock predicate is retained
for correct rechecking after a user update. Missing verification or password
material now cancels provably unsent work or quarantines sent work as unknown,
atomically clearing its lease and recording the existing minimal task event.
All eight 0001–0004 migration files remain unchanged.

The separately approved `PgLocalResultReconciler` finishes already persisted
results for unavailable owners through fixed status-only SQL functions. The
synthetic worker CLI injects this port; `recover_expired()` by itself retains
the existing quarantine stage. `TaskWorker(..., reconciler=...)` runs the local
phase after expiry recovery. It never submits a Provider request, returns raw
output, authenticates an unavailable owner or changes Scheduler table grants.
Ordinary owner transactions remain denied. The functions preserve immutable
raw and receipts, validate bounded JSON locally, and keep possible-charge risk
even after successful output validation. No actual reservation/usage ledger or
refund is implemented. Conflicting results stay quarantined for explicit review.

Both expiry and local processing use bounded batches of at most 100 with stable
ordering; successfully processed rows leave their candidate set. Full remediation
evidence, reviewed-source provenance, validation limits and rollback are in the
[remediation report](../../docs/web-alpha/pr03a/remediation/REPORT.md).

### 0006 migration support boundary

`0006_local_reconciler_security` currently supports only the verified trusted
bootstrap migration path. Successful upgrades were tested with the original
bootstrap identity and another explicitly provisioned bootstrap identity; this
does not require application/runtime logins to receive bootstrap privileges.
Non-superuser upgrade has not succeeded. The verified behavior for that path is
safe refusal and complete rollback, including when the tested non-superuser had
explicit old-object management and temporary ADMIN/SET provisioning.

Do not treat hosted database platforms or non-superuser provisioning as supported.
Before selecting a platform without this bootstrap capability, obtain an
independently reviewed provisioning/revocation design and prove a real successful
upgrade with no residual authority. Do not add elevation endpoints, leave role
membership behind, loosen RLS or suppress migration errors to achieve success.
This support statement does not authorize a production database migration.

The third independent review closed the original R1/R2/R3 findings at commit
`fa041e4c687be3d4b125f1d74024e7417ad19102`, with R2 limited to the bootstrap path.
Its additional probes remain **8 passed / 1 failed / exit 1**: the failure asserts
the earlier optional non-superuser success expectation and is retained as evidence
of the unsupported path, alongside its full rollback evidence. The original
design, probe source and failed logs are not rewritten as a green result.
Track platform/identity selection in
[P3-01 OPEN: deployment support](../../docs/web-alpha/pr03a/remediation/checkout/P3-01-DEPLOYMENT-SUPPORT.md)
and read the [current acceptance](../../docs/web-alpha/pr03a/ACCEPTANCE.md) before
planning deployment. This focused checkout-protection remediation does not rerun
the historical backend 464, Windows 525+171 or nine Node suites; those historical
results belong to `fa041e4c687be3d4b125f1d74024e7417ad19102`.

## PR-02b persistent execution

`task_schema.py` and additive `0003_persistent_tasks` implement Task/TaskAttempt,
OperationReceipt, ProviderResult, owner counters and transactional Outbox. The
four prior migration files remain unchanged. Generation-plan and media-asset
references are constrained NULL until their owning migrations supply real FKs.
Task revision/timestamps/charge-risk and result conflict hashes are internal
persistent metadata supporting the existing frozen contract, not HTTP extensions.

Within an authenticated owner UoW, `tx.tasks.enqueue(kind, {"schema_version": 1},
op_id=uuid4(), class_session_id=...)` atomically creates one task, a minimized
receipt and event. Same-key mismatches fail; replay reauthorizes current ancestry.
Only implemented ID references are accepted. The package has no execute-plan or
charged retry endpoint; those require later authorization, generation and budget
components. `MockProvider` is the sole CLI provider.

The runtime login reads bodies only in an owner-scoped transaction. A different
login inheriting `classroom_web_scheduler` can only execute narrow fixed queue
functions; it cannot SELECT task input, source text, provider results or receipts.
Its no-login definer has only column-level access to scheduling/access metadata.
Claims use SKIP LOCKED, bounded leases, increasing fences and same-transaction
state events. Tasks overdue ten minutes are ordered by age ahead of fresh tasks
to prevent permanent low-priority starvation; this is a local initial tuning
parameter, not a tested production SLA.

`TaskWorker.run_once()` scans expired leases and claims persisted work. A planned
unsent attempt can recover. The worker commits submitting intent before calling
the mock outside a transaction. An uncertain submission becomes unknown and is
never automatically resent, switched or refunded. Stored raw results recover by
local validation only. A stale worker may store a late result but cannot publish;
an explicit `tx.tasks.reconcile_result(task_id, op_id)` can validate already stored
bytes without a new submission. Conflicting results preserve the first raw and
first conflicting digest/time and block publication. Invalid output retains raw
and provider success with OUTPUT_INVALID; no repair model call occurs.

Cancellation completes immediately when provably unsent; sent work remains
cancel_requested until its actual outcome is known. Expired work after owner or
ancestor revocation terminates through a narrow metadata-only function; it never
loads or republishes deleted content. Full erasure/usage reconciliation remain
PR-04b/PR-11a responsibilities. If a result arrives only after revocation, this
engine rejects its private text and conservatively retains the sent intent as
unknown; this is not a provider usage receipt or refund implementation.

`tx.events.replay(cursor, limit)` returns bounded frozen task.changed envelopes.
Stable HMAC cursors require `Database(..., cursor_secret=<server bytes >=32>)`;
no generated per-process secret is used. `ack(event_id)` records delivery but
never deletes replay history. Allocation locks each owner's counter until commit;
notifications contain only event IDs and are optional hints. Invalid/foreign/
expired cursors require resync. Bootstrap callers must use
`transaction(owner, isolation_level='REPEATABLE READ')` for both authorized
projections and `tx.events.watermark()` in the same snapshot. HTTP/WS transports,
text hydration and general synchronization are subsequent PRs.

Tests use `tools/pr02b/run_tests.py --admin-url <verified synthetic URL>`; this
reuses the unchanged strict PR-02a target guard, verifies the actual worktree-owned
server identity and applies loopback-only Python network guards. Commands, exact
results, limitations and rollback are recorded in the
[PR-02b acceptance report](../../docs/web-alpha/pr02b/ACCEPTANCE.md).

The standalone `python -m classroom_web.entrypoints.worker --runtime-url ...
--scheduler-url ... --worker-id ... --once` intentionally accepts only guarded
synthetic loopback targets and constructs MockProvider. Run it only against the
verified isolated cluster described in provenance. It does not read application
configuration, provider keys or the local v1.3.0 data/configuration.

PR-02b independent review R1/R2 corrections unconditionally authorize the
classroom's frozen material and both current/original ancestry under one sorted
source lock order through send-intent COMMIT. Cancellation inspects all attempts
and saved results; queued local validation remains runnable after cancellation.
Previously stranded cancelled tasks with saved raw can finish by explicit local
reconciliation without a provider call. See the
[remediation/re-review report](../../docs/web-alpha/pr02b/remediation/REPORT.md).
