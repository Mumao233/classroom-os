# PR-10b · Review、来源与采用 验收记录

日期：2026-11-22。负责人：林珈诚。工作包 PR-10b「Review、来源与采用」（工作包编号，与 GitHub PR 编号无关）。授权：用户于 2026-11-22 在对话中明确“自动执行 PR-10b”。未合并、未部署。

- 工作树 `J:\class-assistant\worktrees\web-pr10b-review`，分支 `feature/web-pr10b-review`，基点 `origin/main` = `431fffa`。HTTP 契约 0.3.0、事件协议 v1，均未修改。
- 写入范围：仅 `web/frontend/`。根仓库 `main` 上原有的未提交改动未触碰。
- 开工前的范围与缺口清单：[PR10B-SCOPE-GAPS.md](PR10B-SCOPE-GAPS.md)（5 条契约缺口）。

## 1. 交付内容

| 模块 | 文件 | 说明 |
|---|---|---|
| 纯领域逻辑 | `src/domain/review.ts` | 版本角色与阅读版本选择（候选从不被默认采用）、待处理/未解决分组、筛选与地址栏互转、逐行比较（含结构化小节、卡片、英文和来源标签）、冻结来源解析（新版本不顶替、标记改过即不显示）、0.3.0 四个错误码文案、两版转写按时间对照、安全 Markdown 子集、本机阅读位置 |
| 数据钩子 | `src/features/review.ts` | 账号作用域查询；阅读记忆只存文档 ID 和滚动位置 |
| API 客户端 | `src/api/client.ts` | 资料库分页读取、文档/版本/快照、个人稿、采用、理解状态、重试、采用转写 |
| 页面 | `src/pages/review/*` | 全局复习 `/review`、阅读页 `/review/items/:id`、单课复习 `/classrooms/:id/review`；课程“复习”标签按课堂分组并改用分页读取 |
| Mock | `src/mock/review-backend.ts`，`classroom-backend.ts`/`backend.ts`/`start.ts` 接线 | 复习文档、不可变版本、个人稿（冲突时保留提交的分支）、显式采用、按当前来源投影 `source_status`、来源快照；重新转写：按时间窗只冻结已持久分片、逐分片复核期限与撤权、按次审批（绑定计划或原任务）、月度时长预留、未知结果只能核对、每个尝试最多一个重试、撤权后完成的结果不发布 |
| 壳 | `src/app/MockWorkbench.tsx` | 路由；开发面板“重新转写（PR-10b）”（原音状态、额度、批准追加一次）；MOCK-10 下资料库照常可用 |

## 2. Mock 与真实联调的分界

- 复习页面只挂在 `MockWorkbench`（mock 构建）；生产入口仍是 PR-03b 的 `AuthWorkbench`，`scripts/verify-build.mjs` 检查生产产物不含 Mock 入口。页面显示“开发预览：合成数据”。
- 未调用真实模型、DashScope 或 Filetrans；浏览器不持有任何服务密钥。审批只有 Mock 内部入口（开发面板），浏览器端保持“待审批”。
- 契约外字段：无。`tests/pr10b-contract.test.ts` 对复习与重新转写的读、写、错误响应逐一做 schema 校验，并证明额外字段会被拒绝。

## 3. 需求逐条结论

结论只适用于合成 Mock 场景。证据缩写：**B** = `tests/pr10b.browser.spec.ts`（VMware 来宾）；**D** = `tests/pr10b-domain.test.ts`；**K** = `tests/pr10b-backend.test.ts`；**C** = `tests/pr10b-contract.test.ts`。

| ID | 结论 | 证据 |
|---|---|---|
| UI-REVIEW-01 | 通过（Mock） | 继续 / 待处理 / 未解决在前，资料库在后；疑问没读完时不声称“全部已理解”。D「task-first」；B「three entries…」 |
| UI-REVIEW-02 | 通过（Mock） | 学期、课程（含已归档）、课堂、类型、理解状态组合筛选，写入地址栏，返回后恢复。D「library filters」；K「the library combines…」；B「three entries…」 |
| UI-REVIEW-03 | 通过（Mock） | 三个入口读同一批文档 ID，打开同一阅读页；课程入口按课堂分组，三处都按契约分页读取。K「lists are paged…」；B「three entries…」 |
| UI-REVIEW-04 | 通过（Mock） | 沉浸正文、目录、按需来源侧栏（窄屏堆叠）；字号用账号偏好（CAS）；阅读位置按设备记忆，离开时写入最后位置。B「the reader opens…」「reflow at 320 px…」；D「reading memory」 |
| UI-REVIEW-05 | 通过（Mock） | 逐张、揭晓、前后切换，不补卡；“没懂”标记按卡片序号区分，重试沿用同一幂等键。B「cards…」 |
| UI-REVIEW-06 | 通过（Mock） | 已理解只由用户切换，带 `base_revision` 与幂等键；揭晓答案不改理解状态。K「understood is a CAS write…」「…replayed with the same key…」；B「cards…」「a stale adoption…」 |
| UI-REVIEW-07 | 通过（Mock） | 冻结快照逐条解析；版本不一致显示“来源已更新”，不拿新版本顶替；标记在冻结后改过不显示新内容；原音只显示时间与期限，不可播放；课件图片未加载时明示。D「frozen source resolution」；B「the reader opens…」 |
| UI-REVIEW-08 | 通过（Mock） | AI 候选、个人稿、采用版本分开；保存个人稿不动其他指针；采用必须显式操作；较早的 AI 版本保留为历史、可再次采用。K「a personal revision never replaces…」「adoption is explicit…」；B「comparing and adopting…」 |
| UI-REVIEW-09 | 通过（Mock） | 任两版比较阅读时显示的全部内容；采用/保存冲突时不改动并保留本地输入；个人稿冲突时提交的分支另存，草稿固定在开始编辑时的版本，只有用户选择后才改基础。K「a stale personal save keeps…」；B「a draft stays pinned…」「a stale adoption…」 |
| UI-REVIEW-11 | 部分（Mock） | 离线时已加载内容可读、写操作全部停用；可信缓存范围沿用 08a。B「offline keeps…」 |
| UI-REVIEW-12 | 部分（Mock） | 错误按错误码显示，不伪造已读取；导出与反馈不在本 PR |
| 06c 重新转写 | 通过（Mock） | 计划冻结时间窗内的已持久分片（窗内无音频即拒绝）、显示冻结范围、改输入即作废；四个错误码只按 `error.code` 分支；准备后原音变化需重新准备或显式按原样提交，撤权不可被确认覆盖；执行结果不确定时显示“待确认”并以同一幂等键重放；未知结果只能核对，重试需确认重复计费、原尝试 ID 与审批，且只能一次；撤权后完成的结果不发布；候选按时间对照（无基线时单独预览），显式采用与切回。K「PR-10b mock retranscription…」「Astra r0/r1 findings」；B「retranscription…」「the four refusals…」「an unknown result…」 |

## 4. 既有测试的调整

| 文件 | 调整 | 原因 |
|---|---|---|
| `tests/pr10a-backend.test.ts` | “重转写返回 `SERVICE_DISABLED`”改为“整节计划冻结原音清单” | 那是 PR-10a 的占位行为，PR-10b 实现了该流程 |

其余 PR-09a/09b/10a 测试未改动。开发面板按钮使用不与页面操作重名的文字（“原音撤权”“批准追加一次”等），避免既有测试的按钮定位冲突。

## 5. 验证

全部测试在 VMware 来宾执行（宿主只做 `tsc`、`eslint` 静态检查与开发期自测，不作为通过证据）。最终运行结果见第 6 节。

## 6. 独立复审与最终运行

见 [第 6 节最终结果](#final-results)（复审与最终 VMware 运行完成后填写）。

<a id="final-results"></a>

## 7. 遗留与限制

- 契约缺口 5 条见范围清单第 3 节（卡片来源标签与快照的对应、旧版本来源不可读、无原音回放接口、问答只能按课堂查询、“继续”无服务端记录）。
- Mock 的审批只有内部入口；真实审批流程、月度额度与 06c 后端的联调由后续集成 PR 验证。
- 宿主 Chrome 上 `pr10a.browser.spec.ts`“已停止课堂的本机音频在开始另一节后仍拦截关闭”一条不通过，在合并后的 10a 代码上同样不通过；VMware 来宾的 Playwright Chromium 上通过。验收以来宾为准。
