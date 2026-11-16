# PR-07a 验证记录

本次验证基于 `b86bed0c37851f2ef65b01729c097d0970cdf551`，负责人为林珈诚。测试使用合成账号、课堂、来源和确定性 Fake Provider，真实付费模型调用为 0。每轮源码逐文件 SHA-256 随 SOURCE.json 保存，日志与 JUnit 位于本工作树 outputs/pr07a。

## 环境与方法

宿主在 J 盘编辑和静态检查，通过既有安全 broker 编排 VMware Windows 来宾。来宾 Python 为 3.12.14，PostgreSQL 为 17.11，私有对象服务使用 SeaweedFS 4.53。每轮使用独立源码快照、随机 loopback 端口及独立数据库集群。来宾 J 盘实际映射至 `C:\j`。凭据由既有内存 broker 提供，验证材料不保存凭据。

`tools/pr07a/prepare_vmware.ps1` 生成源码 ZIP 和 SHA-256 清单。来宾先验证每个文件，再运行 pip check、固定契约检查和测试。定向验证复用 `tools/pr11a/run_tests.py --suite backend --test-target ...`，在独立真实 PostgreSQL 内执行。退出码与 JUnit 独立核对，回收证据 ZIP 后重新计算 SHA-256。

## 前序轮次及修复

| 轮次 | 实际结果 | 后续处理 |
|---|---|---|
| `pr07a-20261116-162522-64def2c2` | 上下文与 Provider 单元测试 47 passed | 后续边界修复再次纳入综合回归 |
| `pr07a-20261116-162845-21c5a235` | 迁移/metadata 1 passed，HTTP fixture 8 errors | SQLAlchemy 将 SQL 字符串内 `:qa` 识别为绑定参数，改为 `-qa` |
| `pr07a-20261116-163207-e0471a04` | 103 项中 91 passed、12 failed | 修复撤权 UPDATE 的 RLS 新行检查，费用撤权测试采用真实 tombstone 入口，并隔离测试遗留排队任务 |
| `pr07a-20261116-163949-235bcea2` | 112 passed、0 failures/errors/skipped，PG stop 0 | 契约检查缺少 openapi_spec_validator，guest 总体退出 1。后续脚本增加隔离校验依赖安装 |
| `pr07a-20261116-165145-e81132ae` | 全回归准备失败，测试未启动 | 来宾 DNS 不可用，改由宿主下载固定版本 Windows wheels，经 hash 校验后离线安装 |
| `pr07a-20261116-165354-62fd85af` | 完整后端 836 passed、4 failed，0 errors/skipped，29 subtests passed | 3 项要求 runner 的 PG 日志位于既定目录，已修正临时 runner。1 项 ACL 精确矩阵需记录 schema_version 的最小 SELECT 授权，继续保留逐列禁止检查 |

第三轮证据位于 `outputs/pr07a/targeted-3/extracted`，源码 ZIP SHA-256 为 `f221e4a9e6486b9c45127511beb4a5375bb98c19b98c94cea59b2bfaa376ff02`，证据 ZIP SHA-256 为 `c48bb45668512844024a2bb70f5d10cc6825aed44f509f437109f46f8c0877d0`。该轮包含真实子进程在 raw commit 后调用 `os._exit(73)` 的故障恢复测试。

宿主早期契约检查同样因缺 jsonschema 失败，记录在 `outputs/pr07a/contracts.json`。首次数据库轮次因 PowerShell 原生 stderr 处理导致证据打包中断，保留 broker 回收日志。之后脚本已修正 UTF-8 与原生命令错误捕获，测试失败也继续回收证据。

## 覆盖映射

| 范围 | 自动化入口 |
|---|---|
| 时间/可见性、长文中部、重复来源、预算、Prompt Injection、伪造引用、Provider 超时/异常结构 | `tests/unit/test_pr07a_context_provider.py` |
| 不可变版本/摘录定位、owner/class RLS、材料祖先、撤权、签名分页、计划消费、资源限制、迁移升级/失败回滚 | `tests/integration/test_pr07a_sources.py` |
| 租约、真实进程崩溃、迟到结果、同键 reconcile、多 Attempt、并发完成、取消、费用预留/结算/unknown、撤权后 usage 核对 | `tests/integration/test_pr07a_recovery.py` |
| 会话/CSRF、准备→执行→Worker→回答→快照、重复操作、版本修正、取消、默认关闭与候选契约 | `tests/integration/test_pr07a_http.py` |

## 最终 VMware 完整验证

任务为 `pr07a-20261116-170331-d7e0dc70`，实际执行于 2026-11-16 17:03:41–17:11:31 UTC。逐文件 hash 核对 750 项，差异为 0。guest exit 为 0。

| 检查 | 实际结果 |
|---|---|
| 全部 backend `python -B -m pytest tests -q` | 840 passed，29 subtests passed，0 failures/errors/skipped，434.57 秒 |
| 额外收集模块 | PR-04a private_media 16 个函数、media_stream_lifecycle 10 个函数、PR-03b 真实 HTTPS/WSS 浏览器函数及全部 PR-07a 模块均执行 |
| 前端本次构建 | gen-contracts --check、tsc -b、vite production、verify-build 均退出 0 |
| 正式契约与文档 | `tools/overnight/check_contracts.py` 9 项通过 |
| PR-07a 候选 OpenAPI | build(base) 后经 openapi-spec-validator 校验通过 |
| 依赖 | pip check 通过，固定版本校验依赖从 hash 验证后的 Windows wheels 离线安装 |
| 自有测试服务清理 | PG stop 0，SeaweedFS 进程已退出，cleanup_errors 为空 |
| 宿主静态 | 26 个变更 Python 文件 AST、PowerShell ParseFile、git diff --check 通过 |

pytest 有 1 项 Starlette TestClient 关于 httpx 的弃用提示。该提示来自既有依赖组合。

联合 runner 使用 `--root <snapshot> --auth-browser --deps <snapshot>/work/pr07a/validation-deps`，按锁文件核对并复用既有来宾工具链，再构建当前源码前端。它创建本轮独有 PostgreSQL 和 SeaweedFS 实例，显式设置 `PR04A_OBJECT_INTEGRATION=1`、`PR03B_REAL_BROWSER=1` 和 PR-05a 浏览器依赖路径。runner 完整副本随证据归档。

- 源码 ZIP SHA-256 为 `6c607bbab7713fd6cef1f04d4395c78f7025c9ac6f08d5be7d7e46c6d011c24c`。
- runner SHA-256 为 `6b8552e26833358d94c58bb989a8430d95efb680f8c602d364aab58350b53db9`。
- 回收证据 ZIP SHA-256 为 `fb4d7e9ac4b2c8e90aeb864efb3dd4a3cc8cf389c0ff991d43ba51da4884e351`。
- 本机证据为 `outputs/pr07a/full-2/extracted`，联合运行 ID 为 `c926e267f01b46d18ed7c17162663e3b`，内含 SOURCE.json、result.json、pytest.xml、逐命令日志和契约结果。

原 Windows 本地产品 Python、九套 Node 和 DPAPI 门禁由本次 PR 的既有 GitHub Actions 工作流执行。该组门禁需要与 Web 来宾 venv 不同的原产品依赖，远端运行结果另见 PR checks，环境与上述 VMware 回归分别记录。

## 验收边界

实现和合成集成覆盖候选 QA 链路。正式 HTTP 契约冻结、前端生成类型升级、PR-11a 正式账本迁移、真实价格及 Provider 许可接线、PR-04b 物理清理和来源撤权恢复前重放均保留启用门禁。QA composition 和 Worker 默认关闭。OpenAI 兼容传输只使用合成 HTTP transport 验证，未执行真实收费请求。

浏览器测试若使用 Windows SPKI pin，只验证该次测试密钥的允许与另一密钥的拒绝，证书主机名和 CA 链验证需要独立验收。测试结果以本文件最终列出的实际轮次为准。
