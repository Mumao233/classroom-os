# PR-02a Database Foundation implementation plan

Base: `1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f`; contract package: 0.1.0.
Authorization: user explicitly authorized PR-02a implementation and one local commit, 2026-10-28.

Goal: deliver a PostgreSQL foundation for stable owner-scoped catalog identities,
immutable material snapshots, CAS and deletion precedence without starting PR-02b.

Architecture: seven contract tables, SQLAlchemy Core adapters behind pure domain
records/ports, transaction-local owner context, non-owner runtime role and FORCE
RLS. Alembic has one independently frozen migration snapshot. No HTTP application,
frontend, existing local-product changes, task/receipt/outbox or provider calls.

Tech stack: Python 3.12, PostgreSQL 17, SQLAlchemy 2, psycopg 3, Alembic.

- [x] Add independent `web/backend/` packaging and domain records/ports.
- [x] Implement metadata and literal Alembic initial migration for users, semesters,
  courses, material versions, class sessions, deletion requests and tombstones.
- [x] Enforce compound owner FKs, deferred current-material lineage, immutable
  snapshots, revision checks, FORCE RLS, minimal active-principal function and
  parent/deletion guards with database locks.
- [x] Implement owner UoW, catalog repository and atomic tombstone revocation.
- [x] Use a private synthetic loopback PostgreSQL cluster to test raw SQL owner
  isolation, role permissions, pool reuse, snapshots, concurrent CAS, tombstone
  races, transactional rollback and empty/repeated/failing migrations.
- [x] Run unchanged Windows Python, DPAPI and all nine Node gates; compare frozen
  contracts and all baseline tracked files against the supplied base.
- [x] Record reproducible commands, versions, limitations and recovery guidance;
  review the explicit file set. The authorized final local commit is identified
  in `outputs/pr02a/DELIVERY.md` after commit creation.

The later transcript table is deliberately absent: its reserved adoption column
is constrained NULL until the owning migration supplies the FK/lineage guard.
Rollback means application rollback retaining schema/data. Destructive Alembic
downgrade is refused. PR-04b owns independent deletion journal, erasure workers
and backup recovery; this PR supplies only transaction-local revocation primitives.
