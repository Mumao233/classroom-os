# Original material revocation concurrency reproduction

Original commit: `272d1175b1919f76905861c0490a5253a25148d3`. SQL snapshot SHA-256: `dd8065aa02c121e74a29207bdf15ad1a6599aacdd988e573faefb5202f67198c`.

Engine: PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35228, 64-bit. Private loopback `127.0.0.1:4979`, UTC, READ COMMITTED. Applied the exact original `0001_data_foundation.sql` with a privileged bootstrap connection to an independently generated database; this reproduction exercised original migration DDL, not the Alembic runner. Both test writers used a unique unprivileged LOGIN inheriting only `classroom_web_runtime`; no shared test DB was modified.

The final disposable DB `pr02a_review_e6dd2a76ea1542799af0caab619f53fa` and LOGIN `pr02a_review_login_e6dd2a76ea1542799af0caab619f53fa` were dropped in `finally`; cleanup completed. Existing cluster remains running for root tests.

## Results

1. T1 changes current M2→M1 without committing; T2 commits M1 tombstone; T1 then runs deferred commit check: T1 rejects with SQLSTATE23514. Final current=M2. This ordinary ordered schedule is safe.
2. T1 changes current M2→M1, forces its deferred check early with supported `SET CONSTRAINTS check_current_material IMMEDIATE`, then remains uncommitted. T2 observes committed M2 and commits M1 tombstone. T1 commits: **both commit**, final current=M1 and M1 tombstone exists.
3. No `SET CONSTRAINTS` adjustment: test-only last deferred trigger pauses T1 after the unchanged production current-material checker, before COMMIT publishes T1. Actual PostgreSQL `pg_stat_activity` advisory-lock wait confirms that boundary. T2 sees committed M2 and commits M1 tombstone; release test gate, T1 commits: **both commit**, same broken invariant. No application checker or constraint was disabled or replaced. This controlled trigger only made the real concurrent-commit window deterministic.

No arbitrary sleep assertions: cases1/2 use threading Events; case3 observes backend lock state under a bounded deadline. All final state assertions are actual PostgreSQL queries.

## Exact case2 transaction SQL

After synthetic seed commits with course current=M2, material revisions M1=1 and M2=2:

```sql
-- T1 (unprivileged runtime connection)
BEGIN;
SELECT set_config('app.owner_id','2659ecf6-8755-486e-afc6-f11ece4b67e0',true);
SET LOCAL lock_timeout='10s';
SET LOCAL statement_timeout='20s';
UPDATE courses SET current_material_version_id='9c6737d9-df55-4481-8cda-685f0aede9ae',revision=3
 WHERE id='5e7234b4-d5e9-4fea-befc-bce90bae55ca';
SET CONSTRAINTS check_current_material IMMEDIATE;
-- Wait for T2 committed Event; T1 is still open.

-- T2 (separate unprivileged runtime connection)
BEGIN;
SELECT set_config('app.owner_id','2659ecf6-8755-486e-afc6-f11ece4b67e0',true);
SET LOCAL lock_timeout='10s';
SET LOCAL statement_timeout='20s';
SELECT current_material_version_id FROM courses WHERE id='5e7234b4-d5e9-4fea-befc-bce90bae55ca';
-- Returns M2=148bf8b8-8af5-485c-84cb-2b3a69d79869, because T1 is uncommitted.
INSERT INTO deletion_requests(id,owner_id,scope_type,scope_id,state,access_revoked_at)
 VALUES('0dffb895-fcf1-4862-acbd-67b4b4608322','2659ecf6-8755-486e-afc6-f11ece4b67e0','course_material_version','9c6737d9-df55-4481-8cda-685f0aede9ae',
        'access_revoked',transaction_timestamp());
INSERT INTO deletion_tombstones(id,owner_id,request_id,resource_type,resource_id,
                               deleted_revision,erasure_epoch,ledger_sequence)
 VALUES('a21ae294-f279-49db-897d-3579803dc162','2659ecf6-8755-486e-afc6-f11ece4b67e0','0dffb895-fcf1-4862-acbd-67b4b4608322',
        'course_material_version','9c6737d9-df55-4481-8cda-685f0aede9ae',1,1,1);
COMMIT; -- Passes.

-- Resume T1
COMMIT; -- Passes; live course now points at revoked M1.
```

Complete executable seed, SQL, Events, deferred-trigger gate and cleanup are in `work/pr02a/remediation/reproduce_material_race.py`. Structured evidence includes every synthetic ID and ordered outcome in `repro-material-race-272d117.json`.

## Minimal coordination recommendation

The material-tombstone BEFORE INSERT path must hold an exclusive row lock on the immutable material's owning course until commit. Pointer UPDATE already owns that row, so the two operations cannot finish validation concurrently. Keep owner→semester→course lock ordering where application code can control it; material insertion should not first hold course SHARE then later upgrade it. Recheck after locks, retain both deferred invariant checks, and cover both winner orders. PostgreSQL deadlock aborts roll back transactions; external work must never be retried as part of that rollback.
