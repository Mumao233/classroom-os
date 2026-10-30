# PR-03a 第二轮整改变更清单

比较基线：`088984fbd7c9f36f4250c9af340d6a73a0c57960`；本次共 20 个文件。忽略目录中的数据库、日志、临时脚本、缓存不提交。

- `docs/web-alpha/pr03a/ACCEPTANCE.md`
- `docs/web-alpha/pr03a/CHANGES.md`
- `docs/web-alpha/pr03a/VALIDATION.json`
- `docs/web-alpha/pr03a/remediation/REPORT.md`
- `docs/web-alpha/pr03a/remediation/RESULT-RECONCILIATION-PROPOSAL.md`
- `tools/pr03a/remediation/check_protected.py`
- `tools/pr03a/remediation/check_static.py`
- `tools/pr03a/remediation/run_tests.py`
- `tools/pr03a/remediation/selftest_runner.py`
- `web/backend/README.md`
- `web/backend/classroom_web/application/task_worker.py`
- `web/backend/classroom_web/domain/task_ports.py`
- `web/backend/classroom_web/entrypoints/worker.py`
- `web/backend/classroom_web/infrastructure/database.py`
- `web/backend/classroom_web/infrastructure/local_reconciler.py`
- `web/backend/migrations/versions/0005_identity_recovery.py`
- `web/backend/migrations/versions/0005_identity_recovery.sql`
- `web/backend/tests/integration/test_identity_recovery.py`
- `web/backend/tests/integration/test_identity_recovery_migrations.py`
- `web/backend/tests/integration/test_pr03a_review_regressions.py`

身份规则只做原安全谓词复用；Worker/数据库 UoW/CLI/新 Reconciler 属于本次 P2 必需的跨 PR-02b 兼容适配。原 0001–0004、冻结契约、全部已批准测试模块、本地 v1.3.0、PR-09a 及原 PR-02b 工作树未改。

原始开发的 26 文件清单保留在父提交和 `outputs/pr03a/remediation/before-documents/CHANGES.md`。
