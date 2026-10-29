# PR-02a 变更清单

> 本页下文为原提交 `272d1175` 的 27 文件历史清单。2026-10-30 独立审查整改的完整增量见 [整改变更表](review-remediation/CHANGES.md)，结果见 [整改报告](review-remediation/REPORT.md)。

全部为新增文件；基线 212 个跟踪文件没有修改。未创建或改动 web/frontend/。

共 27 个文件：

## 领域与事务

```text
web/backend/classroom_web/__init__.py
web/backend/classroom_web/domain/__init__.py
web/backend/classroom_web/domain/data.py
web/backend/classroom_web/domain/policies.py
web/backend/classroom_web/domain/ports.py
web/backend/classroom_web/infrastructure/__init__.py
web/backend/classroom_web/infrastructure/database.py
web/backend/classroom_web/infrastructure/ddl.py
web/backend/classroom_web/infrastructure/repositories.py
web/backend/classroom_web/infrastructure/schema.py
```

## 迁移

```text
web/backend/migrations/env.py
web/backend/migrations/script.py.mako
web/backend/migrations/versions/0001_data_foundation.py
web/backend/migrations/versions/0001_data_foundation.sql
```

## 真实 PostgreSQL 集成测试

```text
web/backend/tests/integration/conftest.py
web/backend/tests/integration/test_data_foundation.py
```

## 环境与运行说明

```text
web/backend/README.md
web/backend/alembic.ini
web/backend/pyproject.toml
web/backend/requirements.lock
```

## 验证工具

```text
tools/pr02a/check_protected.py
tools/pr02a/run_tests.py
```

## 验收与交接

```text
docs/web-alpha/pr02a/ACCEPTANCE.md
docs/web-alpha/pr02a/CHANGES.md
docs/web-alpha/pr02a/PLAN.md
docs/web-alpha/pr02a/POSTGRESQL-PROVENANCE.json
docs/web-alpha/pr02a/VALIDATION.json
```

任务运行文件不进入 commit：work/pr02a/ 保存独立依赖、PG、临时脚本和合成数据；outputs/pr02a/ 保存原始日志、JUnit、结果 JSON 和提交后的 SHA/交付单。旧回归 runner 的 work/pr00/runs/ 原位置保留。
