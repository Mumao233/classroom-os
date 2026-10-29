# PR-02b Persistent Task Engine Implementation Plan

**Goal:** implement the approved PostgreSQL task/attempt, receipt and outbox engine on base `a76558ac64a95ae28874eaecabb71c5a4381a7ad` without changing frozen contracts or PR-02a migrations.

**Architecture:** short owner-scoped transactions persist all state. A separate scheduler identity claims only queue metadata through narrow functions. A committed submitting intent is the uncertainty boundary; recovery never submits it again. Owner counter allocation and outbox writes commit with state changes.

**Tech stack:** existing Python 3.12, SQLAlchemy 2, Alembic, PostgreSQL 17; synthetic simulation only.

The user has explicitly approved this implementation and its existing PR-01 design. Reuse that approval; no new product design or execution-choice approval is needed. The planner/brainstormer approval steps do not reopen the authorized scope.

- [x] Add `task_schema.py` and additive `0003_persistent_tasks.py/.sql`: six execution/receipt/event tables, FORCE RLS, composite owner keys, narrow scheduler functions. Keep prior migration bytes unchanged. Missing generation/media parents remain blocked by NULL checks until their owning migrations.
- [x] Add pure `domain/tasks.py` types, canonical fingerprints, bounded ID input and minimized receipt validation. Preserve all frozen states/kinds.
- [x] Add receipt/outbox repositories: atomic operation/task creation; same-key mismatch; deletion-first replay; owner-scoped authenticated opaque cursors; commit-ordered counters; repeatable-read bootstrap watermark; replay and delivery acknowledgement.
- [x] Add task repository/scheduler and simulation worker: SKIP LOCKED claims, lease renewal/fencing, durable submitting intent, immutable raw results, local validation, cancellation and dead-lease recovery. Unknown work stays unknown unless explicit local reconciliation proves an already stored result; no automatic resend/refund or retry entrypoint without future budget gate.
- [x] Add real PostgreSQL integration tests for races, killed processes, restart, pending/late/conflicting results, owner isolation and replay ordering. Reuse safe target validator; create a new isolated local cluster only.
- [x] Run all PR-02b tests, all PR-02a regression assertions, original 525 Python tests/171 subtests and nine Node suites. Record actual commands, logs, exits, environment and limitations.
- [x] Review explicit diff/protected file hashes, create one independent local commit, write acceptance evidence and full SHA delivery receipt, stop for independent review. No push/merge/deploy/PR-03a.

Commands: `python tools/pr02b/run_tests.py --admin-url <verified synthetic URL>`; legacy `python tools/pr00/run_checks.py`; static/type/protected checks recorded with actual interpreter paths in acceptance. Tests first establish PostgreSQL mechanisms, implementation then resolves observed failures; no weakened assertions or skipped failures.
