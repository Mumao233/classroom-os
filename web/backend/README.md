# Classroom OS Web · data foundation and persistent task engine

Independent Python 3.12 / PostgreSQL 17 package. Contract package **0.1.0** remains
unchanged at `docs/web-alpha/architecture/contracts/`. This package does not import
the local `app.py`, instantiate its Classroom, or use its settings/data directory.

Implemented: seven tables, SQLAlchemy Core repositories behind pure domain ports,
owner UoW, CAS, immutable course material versions, fixed classroom snapshots and
atomic database revocation/tombstones. There is no HTTP server, authentication
workflow or frontend in this package. PR-02b adds six execution/receipt/event
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
