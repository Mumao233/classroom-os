# PR-11c 第一阶段验证与复审报告（2026-11-26 第六次更新）

负责人林珈诚。工作树 `J:\class-assistant\worktrees\web-pr11c-search`，分支 `feature/web-pr11c-search`，基线 `origin/main` `80d2204`，契约 OpenAPI 0.3.0。已推送并开 Draft PR #39（不合并）。2026-11-26 负责人授权修复 r6 的 P2/P3，随本报告提交。Astra 各轮均以当时的未提交工作区为范围复审。

## 范围与分工

第一阶段只做不与 PR-11b 冲突的部分：后端四个接口（`/search`、`/notifications`、`/notifications/{id}/read`、`/feedback`）、迁移 `0019_search_operations`、通知生产者，以及仅新增文件的前端 domain、mock 和四个未接线组件。11b 共用文件（`api/client.ts`、`MockWorkbench.tsx`、`mock/backend.ts` 等）未改动。第二阶段接线待 11b 完成后另行启动。

负责人另决定把一处 `main` 既有的 04b/04a 缺陷并入本分支修复（见下文"挂载前缀修复"）。

代码由 Codex `gpt-6.2-sol`（xhigh）编写并按 r0–r2 复审意见修复；r3 之后的视图授权收回、挂载前缀修复与 r5 的 P2/P3 修复由 Claude 直接完成。Claude 编排、核对、修测试与 runner、执行 VMware；复审由 `gpt-6.2-astra`（ultra）经 astra-review 脚本只读执行。实现细节、locator 格式、通知生产者与待决契约问题见 [阶段交接](PHASE1-HANDOFF.md)。

## VMware 结果

全部功能测试在 VMware Windows TEST 来宾、独立 PostgreSQL 17.11 集群和随机测试库中执行；宿主只做静态检查与编排。

**当前代码（r6 修复后）对应的运行：**

| 运行 | 后端 pytest | 前端 Vitest | 其他 |
|---|---|---|---|
| `pr11c-20261126-132652-533f5b85` targeted | 28 passed（全部 11c） | 36 passed（全部 11c） | — |
| `pr11c-20261126-133045-e9495b04` full | 1743 passed，0 failed/skip（含 11c 28 项） | 603 passed（含 11c 36 项） | 生产构建、PG 日志隐私标记通过；来宾残留进程 0 |

full 证据：`outputs/pr11c/pr11c-20261126-133045-e9495b04/evidence.zip`，SHA-256 `0173a5ce736afb9aebc7047571bbc27561ef36f712fc5dacd413da78add7b0eb`。r6 修复之后未再做 Astra 复审。

**r6 所审代码对应的运行（历史）：**

| 运行 | 后端 pytest | 前端 Vitest | 其他 |
|---|---|---|---|
| `pr11c-20261124-205641-e1023cd9` targeted | 28 passed（全部 11c） | 34 passed（全部 11c） | — |
| `pr11c-20261124-205831-6c24c145` full | 1741/1743 passed（含 11c 28 项全过） | 601 passed（含 11c 34 项） | 生产构建、PG 日志隐私标记通过；来宾残留进程 0 |
| `pr11c-20261124-212551-e51049e8` full | 1741/1743 passed（含 11c 28 项全过） | 601 passed（含 11c 34 项） | 同上 |

两次 full 的唯一失败都是 `test_pr08c_pool.py` 的两个 `[export]` 用例，`STORAGE_UNAVAILABLE`。该路径为 `PortableTransfers._put` → `store.put_portable`，直接写 SeaweedFS，不经过任何 ASGI 中间件；本分支未改动 `portable_*`、`object_store` 或该测试，分支内与之最近的改动只有 `media_http.py` 的路由路径匹配。现象与 [PR-06c 验证](../pr06c/VERIFICATION.md) 记录的 SeaweedFS 截断上传一致。本会话池测试实际执行 12 次，失败 5 次（`[import]` 2 次、`[export]` 3 次），最近一次通过，建议作为 08c/测试环境事项单独排查。

r6 所审代码的证据：`outputs/pr11c/pr11c-20261124-212551-e51049e8/evidence.zip`，SHA-256 `453cfc34effd34b465cd2f1c1646165c6a5ecdd955a117d7564b910c27ae9ad8`。

**历史运行（早于当前代码，只作过程记录）：** 挂载前缀修复后、r5 修复前的 full `pr11c-20261124-195006-0bdcb51d`（后端 1741、前端 596 全过）；r3 所审代码的 full `pr11c-20261124-093454-4f8a6180`（后端 1730、前端 596 全过）。更早各轮的失败与处理：

- 首轮 targeted 4 项失败：3 项为测试错误（mark-read 缺契约要求的 `{"read": true}`；计费表不在迁移链），1 项为真缺陷（反馈 SQL 字面量 `':feedback'` 被 SQLAlchemy `text()` 当成绑定参数，所有正式反馈 503），已修。
- full 暴露 42 项回归：33 项为 runner 环境（03a 证据路径、`PR05A_REVIEW_OUTPUT`、缺 validation 依赖），7 项为真回归（notifications 外键阻止删除 `sync_conflicts`，改为 `ON DELETE CASCADE`），2 项为既有断言需随新表/新事件同步。另把 0019 在旧表上的 3 个 SQL 管理索引从 metadata 比对中按名排除，并在 11c 迁移测试中断言其真实定义。

## r3 之后由 Claude 完成的改动

1. **收回反馈视图直接读取**（负责人决定）：`0019` 不再向 `classroom_web_feedback_admin` / `classroom_web_feedback_excerpt` 授予视图读取，并对两者及 `classroom_web_runtime` 显式 `REVOKE`。行策略、放行凭据与角色重叠检查保留为纵深防御；测试断言读取角色读视图与基表均被拒绝，原有行策略用例改以视图属主执行。
2. **挂载前缀修复**（负责人授权）：新增 [`asgi_paths.py`](../../../web/backend/classroom_web/entrypoints/asgi_paths.py)，安全中间件同时检查原始 `path` 与相对 `root_path` 的路径。
   - `SearchQueryRedaction`（11c）：修复 r4 P1"挂载前缀下搜索词进入访问日志"。
   - `DeletionGate`（04b，`main` 既有）：原以 `path.startswith('/api/web/v1/')` 判断，应用挂在 `root_path` 下时所有 API 路由绕过删除/恢复门禁。由新增的挂载端到端测试发现。
   - `MediaRequestLifecycle`（04a，`main` 既有）：同样写死前缀，挂载下媒体查询串不脱敏，并发槽位与超时预算也被绕过。
3. **r5 的 P2/P3**（负责人授权）：
   - 反馈等请求体上限改按相对路由路径选择（`route_path`），挂载下 5000+5000 字中文反馈返回 202。
   - 通知中心在已读/移除结果之后重发被打断的列表请求，新通知、移除与游标不再丢失。
   - 打包前逐级拒绝源文件路径上的 reparse point 与越出 worktree 的路径。
   - `run_guest.py` 先保留 `ObjectService` 句柄再启动。
   - 来宾不再静默映射：映射后核验 `J:` 为 `C:\j` 替代盘，否则在写入前失败。TEST 来宾只有单一 C: 卷，此为 03b/04b/06c/11a runner 共用的既有约定，未改为非 C 卷。
   - 已解决冲突标题改为"同步冲突已解决"；mock 拒绝含 NUL 的反馈文本。
4. **r6 的 P2/P3**（负责人授权，2026-11-26）：
   - 通知中心记录被打断请求的发起回调，重发时使用该回调；父组件在写入期间更换 `list` 时不再回落到旧回调。
   - 自动重发保留"已从列表移除"提示，后续加载或错误仍可覆盖。
   - 打包时每个文件只打开一次，以该句柄的 `GetFinalPathNameByHandle` 结果核验位于 worktree 内，哈希与归档均从同一句柄读取；路径级预检保留。
   - mock 的错误优先级与后端一致：先同意检查，再 NUL 检查。

## Astra 复审

| 轮次 | 结论 | 处理 |
|---|---|---|
| 一次作废 | 启动后工作树被改动，`working_tree_unchanged` 门禁必然失败，已中止，未使用其结果 | — |
| r0 | P0 0 / P1 4 / P2 7 / P3 1 | 12 项核实成立，Sol 修复 |
| r1 | P0 0 / P1 3 / P2 3 / P3 1 | 7 项核实成立，Sol 修复 |
| r2 | P0 0 / P1 2 / P2 6 | 8 项核实成立；负责人授权额外一轮，Sol 修复 |
| r3（脚本标签 `r2`） | P0 0 / P1 3 / P2 6 | 停止；负责人随后决定收回视图授权 |
| r4（脚本标签 `r2`） | P0 0 / P1 2 / P2 6 / P3 1 | 反馈投影类 P1 全部消失；负责人授权修复挂载前缀脱敏 |
| r5（脚本标签 `r2`） | P0 0 / P1 1 / P2 5 / P3 3 | 挂载前缀修复无新发现；负责人授权修复 P2/P3 |
| r6（脚本标签 `r2`） | **P0 0 / P1 0** / P2 2 / P3 2 | 无新 P1，按负责人条件开 Draft PR；其余记为未关闭项 |

复审产物在工作树 `outputs/astra-review/`（已忽略）：`…r0-kaE80U`、`…r1-bWjpvB`、`…r2-MfQat2`、`20261124T100303Z-…-aZWw2d`（r3）、`20261124T163832Z-…-DQ1vfS`（r4）、`20261124T202025Z-…-9zvOuc`（r5）、`20261124T212545Z-…-plj6q0`（r6）。脚本轮次上限为 2，r3–r6 以 `--round 2` 运行。

### 仍未关闭

- **跨进程删除放行（r0–r5 的 P1，r6 未再报出）**：`OperationsService` 与事件轮询只看进程内 `deletion.ready`；`DeletionGate` 对全部 API 路由同样只看进程内状态，属 04b 既有架构。仍建议作为 04b 独立事项，在门禁层统一读取数据库放行记录。
- **r6 的 2 条 P2、2 条 P3**：已修并经 VMware 验证（见上文第 4 项），未再复审。
- **测试环境**：`test_pr08c_pool.py` `[export]` 偶发 `STORAGE_UNAVAILABLE`，见上文。

已关闭：前三轮针对反馈投影直接读取的 P1 因不再授予直接读取而不再适用；r4 挂载前缀脱敏 P1、r5 全部 P2/P3 已修并经 VMware 验证。

## 待负责人决定

- 跨进程放行与 08c 池测试是否单独立项；是否对 r6 修复再做一次复审。
- 预算告警映射、复习文档独立删除、持续故障解决信号等契约问题需 Astra 定（见交接）。
