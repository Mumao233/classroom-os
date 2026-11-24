# PR-11c 第一阶段验证与复审报告（2026-11-24 第四次更新）

负责人林珈诚。工作树 `J:\class-assistant\worktrees\web-pr11c-search`，分支 `feature/web-pr11c-search`，基线 `origin/main` `80d2204`，契约 OpenAPI 0.3.0。全部改动**未提交**，等待负责人决定。

## 范围与分工

第一阶段只做不与 PR-11b 冲突的部分：后端四个接口（`/search`、`/notifications`、`/notifications/{id}/read`、`/feedback`）、迁移 `0019_search_operations`、通知生产者，以及仅新增文件的前端 domain、mock 和四个未接线组件。11b 共用文件（`api/client.ts`、`MockWorkbench.tsx`、`mock/backend.ts` 等）未改动。第二阶段接线待 11b 完成后另行启动。

负责人另决定把一处 `main` 既有的 04b 缺陷并入本分支修复（见下文"挂载前缀修复"）。

代码由 Codex `gpt-6.2-sol`（xhigh）编写并按复审意见修复；r3 之后的视图授权收回与挂载前缀修复由 Claude 直接完成。Claude 编排、核对、修测试与 runner、执行 VMware；复审由 `gpt-6.2-astra`（ultra）经 astra-review 脚本只读执行。实现细节、locator 格式、通知生产者与待决契约问题见 [阶段交接](PHASE1-HANDOFF.md)。

## VMware 结果

全部功能测试在 VMware Windows TEST 来宾、独立 PostgreSQL 17.11 集群和随机测试库中执行；宿主只做静态检查与编排。

**当前工作树对应的运行：**

| 运行 | 后端 pytest | 前端 Vitest | 其他 |
|---|---|---|---|
| `pr11c-20261124-191804-aea179e3` targeted | 26 passed（全部 11c） | 29 passed（全部 11c） | — |
| `pr11c-20261124-195006-0bdcb51d` full | 1741 passed，0 failed/skip（含 11c 26 项） | 596 passed（含 11c 29 项） | 生产构建、PG 日志隐私标记通过；来宾残留进程 0 |

full 证据：`outputs/pr11c/pr11c-20261124-195006-0bdcb51d/evidence.zip`，SHA-256 `2716b6af884a31a525daa820e41ba813aa82fb7747091f9a812d58e2016c0c63`；包内 `SOURCE.json` 记录基线 `80d2204`、源码包 SHA-256 前缀 `5b02c64e`、1036 个文件。此后工作树仅本报告与 `docs/web-alpha/evidence/PR11C-PHASE1.md` 有改动。

同一代码的前一次 full `pr11c-20261124-191942-41e5a9d0`：后端 1739/1741，两个 `test_pr08c_pool.py` `[export]` 用例 `STORAGE_UNAVAILABLE`。该用例经 `service._put` 直接写对象存储，不经过任何 ASGI 中间件；与 [PR-06c 验证](../pr06c/VERIFICATION.md) 记录的 SeaweedFS 截断上传偶发一致。本会话 10 次 full 中池测试失败 3 次（`[import]` 2 次、`[export]` 1 次），11c 未改动导入导出与对象存储代码。

**历史运行（早于当前代码，只作过程记录）：** r3 所审代码的 full `pr11c-20261124-093454-4f8a6180`（后端 1730、前端 596 全过）；第 2 轮修复前的 full `pr11c-20261123-213446-b3aa6585`（后端 1727、前端 592 全过）。更早各轮的失败与处理：

- 首轮 targeted 4 项失败：3 项为测试错误（mark-read 缺契约要求的 `{"read": true}`；计费表不在迁移链），1 项为真缺陷（反馈 SQL 字面量 `':feedback'` 被 SQLAlchemy `text()` 当成绑定参数，所有正式反馈 503），已修。
- full 暴露 42 项回归：33 项为 runner 环境（03a 证据路径、`PR05A_REVIEW_OUTPUT`、缺 validation 依赖），7 项为真回归（notifications 外键阻止删除 `sync_conflicts`，改为 `ON DELETE CASCADE`），2 项为既有断言需随新表/新事件同步。另把 0019 在旧表上的 3 个 SQL 管理索引从 metadata 比对中按名排除，并在 11c 迁移测试中断言其真实定义。

## r4 之后的改动（已由 r5 复审）

1. **收回反馈视图直接读取**（负责人决定，r4 所审代码已包含）：`0019` 不再向 `classroom_web_feedback_admin` / `classroom_web_feedback_excerpt` 授予视图读取，并对两者及 `classroom_web_runtime` 显式 `REVOKE`。行策略、放行凭据与角色重叠检查保留为纵深防御；测试断言读取角色读视图与基表均被拒绝，原有行策略用例改以视图属主执行。
2. **挂载前缀修复**（负责人授权，r4 之后）：新增 [`asgi_paths.py`](../../../web/backend/classroom_web/entrypoints/asgi_paths.py)，安全中间件同时检查原始 `path` 与相对 `root_path` 的路径，任一命中即匹配。
   - `SearchQueryRedaction`（11c）：修复 r4 P1"挂载前缀下搜索词进入访问日志"。
   - `DeletionGate`（04b，`main` 既有）：原以 `path.startswith('/api/web/v1/')` 判断，应用挂在 `root_path` 下时所有 API 路由绕过删除/恢复门禁。由新增的挂载端到端测试发现。
   - `MediaRequestLifecycle`（04a，`main` 既有）：同样写死前缀，挂载下媒体查询串不脱敏，并发槽位与超时预算也被绕过。
   - 测试：搜索访问日志用例增加 `/pilot` 挂载参数（200/422/503）；三个中间件各自在"不挂载 / path 含前缀 / path 不含前缀"三种 scope 下的直接用例（门禁 503 与放行 200、媒体脱敏与 429 拒绝）。

## Astra 复审

| 轮次 | 结论 | 处理 |
|---|---|---|
| 一次作废 | 启动后工作树被改动，`working_tree_unchanged` 门禁必然失败，已中止，未使用其结果 | — |
| r0 | P0 0 / P1 4 / P2 7 / P3 1 | 12 项核实成立，Sol 修复 |
| r1 | P0 0 / P1 3 / P2 3 / P3 1 | 7 项核实成立，Sol 修复 |
| r2 | P0 0 / P1 2 / P2 6 | 8 项核实成立；负责人 2026-11-24 授权额外一轮，Sol 修复 |
| r3（脚本标签 `r2`） | P0 0 / P1 3 / P2 6 | 停止；负责人随后决定收回视图授权 |
| r4（脚本标签 `r2`） | P0 0 / P1 2 / P2 6 / P3 1 | 反馈投影类 P1 全部消失；负责人授权修复挂载前缀脱敏 |
| r5（挂载前缀修复后，脚本标签 `r2`） | P0 0 / P1 1 / P2 5 / P3 3 | 挂载前缀修复无新发现；仅改正证据摘要文档，其余未修 |

复审产物在工作树 `outputs/astra-review/`（已忽略）：`20261123T200524Z-…-r0-kaE80U`、`20261123T210532Z-…-r1-bWjpvB`、`20261123T220216Z-…-r2-MfQat2`、`20261124T100303Z-…-r2-aZWw2d`（r3）、`20261124T163832Z-…-r2-DQ1vfS`（r4）、`20261124T202025Z-…-r2-9zvOuc`（r5）。脚本轮次上限为 2，r3–r5 以 `--round 2` 运行。

### 仍未关闭

- **P1 跨进程删除放行不一致**：`OperationsService` 与事件轮询只看进程内 `deletion.ready`；`DeletionGate` 对全部 API 路由同样只看进程内状态，属 04b 既有架构。建议作为 04b 独立事项，在门禁层统一读取数据库放行记录。
- **P2（r5，5 条）**：挂载前缀下反馈请求体上限仍以 `request.url.path` 判断，退回 16 KiB，合法长中文反馈会被拒；通知刷新与已读重叠时整页刷新被丢弃，新通知与游标丢失；打包未拒绝路径祖先上的目录联接；`run_guest.py` 在 SeaweedFS 就绪探测失败时丢失进程句柄；来宾缺少 `J:` 时静默映射到 C:、证据路径硬编码。
- **P3（r5，3 条）**：mock 未拒绝含 NUL 的反馈文本；已解决冲突的通知标题仍为"待处理"；证据摘要与验证报告不一致（本次已改正）。

已关闭：前三轮针对反馈投影直接读取的 P1（原地恢复、长事务快照、服务端游标、runtime 角色重叠）因不再授予直接读取而不再适用；r4 挂载前缀脱敏 P1 已修并经 VMware 验证。

## 待负责人决定

- 跨进程放行是否转为 04b 独立事项；反馈请求体上限等 P2 是否修复。
- 是否在当前状态提交到 `feature/web-pr11c-search`（本地，不推送）。
- 预算告警映射、复习文档独立删除、持续故障解决信号等契约问题需 Astra 定（见交接）。
