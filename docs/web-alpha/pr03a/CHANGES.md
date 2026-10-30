# PR-03a 变更文件清单

批准父提交：`3b3881540519c27075119f99bb1dbff35d369e35`。

本清单共 26 个文件；不含忽略的 work/outputs 合成环境与日志。

- `docs/web-alpha/pr03a/ACCEPTANCE.md`
- `docs/web-alpha/pr03a/CHANGES.md`
- `docs/web-alpha/pr03a/PLAN.md`
- `docs/web-alpha/pr03a/VALIDATION.json`
- `tools/pr03a/check_protected.py`
- `tools/pr03a/check_static.py`
- `tools/pr03a/run_tests.py`
- `tools/pr03a/selftest_runner.py`
- `web/backend/README.md`
- `web/backend/classroom_web/application/identity.py`
- `web/backend/classroom_web/domain/identity.py`
- `web/backend/classroom_web/domain/identity_ports.py`
- `web/backend/classroom_web/infrastructure/database.py`
- `web/backend/classroom_web/infrastructure/identity_mail.py`
- `web/backend/classroom_web/infrastructure/identity_passwords.py`
- `web/backend/classroom_web/infrastructure/identity_repository.py`
- `web/backend/classroom_web/infrastructure/identity_schema.py`
- `web/backend/migrations/versions/0004_identity.py`
- `web/backend/migrations/versions/0004_identity.sql`
- `web/backend/pyproject.toml`
- `web/backend/requirements.lock`
- `web/backend/tests/integration/test_data_foundation.py`
- `web/backend/tests/integration/test_identity.py`
- `web/backend/tests/integration/test_identity_migrations.py`
- `web/backend/tests/integration/test_task_migrations.py`
- `web/backend/tests/unit/test_identity_values.py`

旧文件仅涉及 backend README、可信资源授权入口、Web 独立密码依赖，以及两处为增量表/迁移链保留原断言的测试适配；其他均为本包新增。冻结契约、本地版、原 Windows/Node 测试、六个既有迁移及两项 OPEN 技术债文件保持不变。
