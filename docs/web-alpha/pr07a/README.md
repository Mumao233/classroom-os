# PR-07a 问答与来源快照

负责人为林珈诚。实现从 `b86bed0c37851f2ef65b01729c097d0970cdf551` 开始，使用独立分支 `feature/web-pr07a-qa-snapshots`。

实施依据见 [PLAN.md](PLAN.md)，候选 API 及下游接入见 [CONTRACT.md](CONTRACT.md)。[验证记录](VALIDATION.md)与[独立审查](REVIEW.md)提供实际环境、源码摘要、结果和启用门禁。

## 组合与执行

`QaBrowser` 接入既有 `create_app(qa=...)`。应用 composition 使用 `tools/pr07a/contract_candidate.py` 的 `build` 生成对应契约，向 `QaBrowser` 显式注入 AuthService、Database 和配置。数据库继续使用受限 runtime 身份，分页复用 Database 的稳定 cursor_secret。

`QaWorker(database, scheduler, provider, worker_id=..., enabled=True, billing=...)` 使用独立受限 scheduler 身份领取 `schema_version=pr07a-1` 的任务。既有 Worker 继续领取 schema_version=1。默认 disabled，当前 Worker 接受 simulation_only=True 的 Provider。

合成配置示例可以使用下列值。

```python
config = {
    "enabled": True,
    "billing_required": False,
    "destination": {
        "provider": "fake",
        "model": "qa-deterministic-v1",
        "region": "synthetic",
        "configuration_revision": "pr07a-fake-1",
    },
    "price_id": None,
    "price_version": "synthetic-no-charge",
    "maximum_cost_microyuan": 0,
    "input_token_budget": 8192,
    "max_output_tokens": 1024,
}
```

内部来源写入使用 `PgQaRepository`，执行前须进入已授权 UoW。手工合成课堂使用 origin=manual。课程材料来源必须对应课堂已冻结的材料 ID 和准确文本。外部请求不能传 owner、任意 Provider URL、API Key 或原文绕过已有来源。

## 故障与费用

持久 send intent 先于 Provider 调用提交。任务超时后，可能已派发的 Attempt 保留 unknown 并禁止自动重发。原始结果持久后，本地恢复可验证来源别名并原子写入回答、任务终态及 Outbox。多个本地完成者仅能发布一次，未提交的遗留 Attempt 在完成时取消。

PR-11a 合成接入通过候选账本、ReservationRequest 和 BillingTaskLifecycle 完成。计划消费与预留共用事务，同计划无法通过不同操作键重复预留。Provider 返回的 usage 在正文保存被撤权或发生故障时，仍进入 metadata-only 费用核对。缺少最终 usage 或结算中断时继续保留 unknown 风险，可用既有 late_usage 幂等核对。

OpenAI 兼容传输适配器可通过合成 HTTP transport 验证，默认关闭，要求单次绑定的调用许可。当前 Worker 的 simulation-only 门禁继续生效。后续真实启用需要正式账本迁移、经过核验的价格、费用配置、Provider 许可接线及用户授权。

## 数据迁移与回退

新增 Alembic revision `pr07a_001`，父版本为 `0009_private_media`。迁移创建六个领域表、FORCE RLS、不可变版本约束、精确课堂组合外键、窄权限撤销函数以及专属调度/恢复 SQL。既有 0001–0009 迁移内容保持原样。

在隔离测试库执行 `alembic upgrade head`。正式数据库执行仍须单独授权。升级失败依赖 PostgreSQL 事务回滚，验证覆盖升级前已有目录和任务的保留、重复升级和失败迁移回滚。

自动 downgrade 明确拒绝删除已保存问题、来源快照及派发证据。应用回退先关闭 QA 路由 composition 和 QA Worker，保留新增数据库对象，后续使用前向修复。迁移后的三参数调度函数只领取旧执行协议任务，旧 Worker 不会领取 QA 任务。恢复数据库备份时继续遵守先应用删除墓碑的项目规则。

并行 PR-04b 使用独立候选迁移。合并次序确定后必须复核 Alembic 单链并重新执行集成验证。

## 删除生命周期接缝

来源撤销、课堂/课程/学期删除、账号失效会阻止回答、快照项、任务及 Provider 原文的读取。来源当前版本更新不改写历史快照。撤销后的费用核对只读取必要元数据。

04b 的物理清理需纳入 qa_sources、qa_source_versions、source_snapshots、source_items、generation_plans、questions 以及对应 provider_results。当前不可变正文表拒绝通用 DELETE，后续 04b 必须通过受控清理角色和迁移接入，同时保留最小幂等与费用证据。当前范围完成访问撤销，物理清理联调仍待关闭。

内部 revoke_source 当前持久化 PostgreSQL revoked_at，尚待 04b 接入独立故障域的删除日志及恢复前重放。恢复旧备份可能丢失备份后发生的来源级撤权，这项恢复门禁需要在正式启用前关闭。目录祖先继续使用既有墓碑语义。

## 验证入口

模型使用确定性 Fake Provider。所有运行测试在 VMware Windows 来宾和独立 PostgreSQL 17.11 集群内执行。`tools/pr07a/prepare_vmware.ps1` 打包当前已跟踪及未忽略源码，生成逐文件 SHA-256 清单，复用既有安全 broker 和 PR-11a 测试 runner。单元、真实 PostgreSQL、HTTP 认证、来源、并发、恢复及费用测试在 `web/backend/tests/*/test_pr07a_*`。

来宾使用离线校验依赖。宿主材料准备时将 pip 缓存及临时目录设在本工作树 work/pr07a，执行 `python -m pip download --only-binary=:all: --platform win_amd64 --python-version 312 --implementation cp --abi cp312 --dest work/pr07a/validation-wheels -r automation/validation-requirements.txt`。打包脚本逐项记录 wheel SHA-256，来宾校验后安装到新快照的 work/pr07a/validation-deps。

宿主材料保存在本 J 盘工作树。既有来宾 J 盘映射到 C 盘的 `C:\j`，测试报告保留其实际路径。
