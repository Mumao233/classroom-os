# 账号壳“收到删除回执即撤销”验收记录

分支 `feature/web-receipt-revocation`，基于 PR-10b `feature/web-pr10b-review` @ `16859d5`（Draft PR #34）。设计见 [REVOCATION-DESIGN.md](REVOCATION-DESIGN.md)。只改 `web/frontend/`，不改契约（OpenAPI 0.3.0），只用合成数据，未调用真实服务。

## 1. 范围

| 场景（PR-10b 第 5 轮 P1） | 处理 | 单元测试 | 浏览器测试（回复后立即断网） |
|---|---|---|---|
| 1 删除进行中切换级联/移走 | 范围、方式、接收位置作为 mutation 变量；请求中锁住选项；回执在请求内应用 | `scenario 1` | `scenario 1` |
| 2 级联确认后断网，采集仍绑定被删课堂 | 回执即按成员关系撤销绑定采集；成员关系只是未知时走正常停止路径暂停 | `scenario 2` | `scenario 2` |
| 3 成员关系不在缓存 | 新撤销那次清扫把可能受影响的未知项丢弃（reset），不当作不受影响 | `scenario 3` | `scenario 3` |
| 4 权威拒绝后仍显示缓存 | 守卫在拒绝/401 时去掉缓存；页面只在传输失败时显示缓存且只读 | `scenario 4` | `scenario 4` ×2 |
| 5 覆盖 09b/10a/10b | 一个账本、按查询种类判定；替换 10b 的 `evictClassroomFromReview`/`evictScopeFromReview` | `scenario 5` | `scenario 5`、会话撤销 |

## 2. 验证（全部在 VMware 来宾 `TEST`，Node v24.19.0，Playwright Chromium；宿主只做 tsc/eslint）

每次运行前校验源码快照逐文件 SHA-256（134 个文件，0 不符）。

| 运行 | 源码 | 结果 |
|---|---|---|
| q02 | `d91d3b2` 前的工作树 | 单元 241/241；新浏览器用例 7/7 |
| f01 | `d91d3b2` | **10/10 步骤退出 0**：契约 172、单元 241、全量 vitest 515、构建检查，浏览器 104/104。证据 SHA-256 `4ba7064017b2af8a49d9351cd2c73fe7cc818fd2dbf59de892b38ef23112b2dd` |
| q03 | `cca471e` 前的工作树 | 单元 252/252；10a/10b/新浏览器用例 43/43 |
| f02 | `cca471e`（第 0 轮修复后） | **10/10 步骤退出 0**：契约 172、单元 252、全量 vitest 526、构建检查，浏览器 104/104。证据 SHA-256 `f90f4c5b6281f49208ecfb211ae7bd15b01dbbe4df3106b96f7f8e34d3ddbafe` |
| f03 | `fb2319f`（第 1 轮修复后，最后验证的源码） | **10/10 步骤退出 0**：契约 172、单元 260、全量 vitest 534、构建检查，浏览器 104/104（含 PR-09a/09b/10a/10b 既有 97 条）。证据 SHA-256 `5fc0a00215531289d378d4cdae21c45d2a84ea94d9c152ef20ff27a40fb7d24a` |

PR-10b 原有 3 条清理测试改为走账本，断言不变；其余既有测试未改动。

## 3. 独立复审（本机 Codex CLI，gpt-6.2-astra / ultra，只读沙箱，`--base feature/web-pr10b-review`）

| 轮次 | 状态 | P0/P1/P2/P3 | 处理 |
|---|---|---|---|
| r0 | `review_completed` | 0/6/5/0 | 全部成立，修于 `cca471e` |
| r1 | `review_completed` | 0/8/4/0 | 全部成立，修于 `fb2319f` |
| r2 | `review_completed` | **0/6/4/0** | 已到两轮上限，按规定停止，未修复 |

**结论：未达到“无 P0/P1”的复审门槛。** r2 遗留问题（均判定成立）：

- P1 提交前读绑定课堂的成员关系用了缓存（`ensureQueryData`），其他设备移动课堂后会误判；应强制新读。
- P1 不确定删除的临时清扫中，`cancel({ revert: true })` 同步触发缓存订阅，`learn()` 看到临时级联而写入永久撤销并调用撤销钩子（第 1 轮修复引入）。
- P1 不确定删除恢复账本后 `isEmpty()` 早于 `droppedQueries` 检查，晚到的 mutation 回复可重新填回被丢弃的资料（第 1 轮修复引入）。
- P1 学期级联后晚到的课程列表里属于该学期的课程未记入账本，随后到达的课堂只被“暂停”而非确认撤销。
- P1 删除请求自身返回 401 时没有走账号纪元退出（只有查询 401 会）。
- P1 版本读取被拒绝后，已打开的个人稿编辑框里仍显示从该版本复制的服务端正文。
- P2 演示课堂先 404 后登记成功，旧拒绝标记未清除，之后离线时被隐藏。
- P2 已确认撤销的课堂，首次离线打开其详情只显示“无法连接”，不显示“已删除”。
- P2 暂停采集的停止在计算摘要期间若课堂被确认撤销，续写的待确认停止会复活。
- P2 只读回退中“重新准备”重新转写按钮未检查 `online`（10b 既有，r5 P3 同类）。

详细判定见工作树 `outputs/astra-review/20261123T010146Z-base-feature_web-pr10b-review-r2-r61iif/final-report.md`。

## 4. 限制

- 只验证 Mock 工作台；真实后端事件通道、课程/学期 `object.deleted` 事件未处理，留给集成 PR。
- 本机草稿不在清扫范围。
- 浏览器测试使用仅测试用的 Mock 控制（`localStorage` 开关与 `__classroomMock.denyRevisions`），不改 HTTP 契约。
