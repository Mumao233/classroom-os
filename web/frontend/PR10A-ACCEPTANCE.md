# PR-10a · Classroom 与课后页 验收记录

日期：2026-11-21。负责人：林珈诚。工作包 PR-10a「Classroom 与课后页」（工作包编号，与 GitHub PR 编号无关）。授权：用户于 2026-11-21 在对话中明确“执行 PR-10a”。未合并、未部署。

- 工作树 `J:\class-assistant\worktrees\web-pr10a-classroom`，分支 `feature/web-pr10a-classroom`，基点 `origin/main` = `fe4aafc1ee0a47e627b80fdaab1e2bdb688d5664`。契约 `openapi.json` 0.2.1、事件协议 v1，均未修改。
- 写入范围：仅 `web/frontend/`。根仓库 `main` 上原有的未提交改动未触碰、未带入；PR-06c 工作树未触碰。
- 开工前的范围与缺口清单：[PR10A-SCOPE-GAPS.md](PR10A-SCOPE-GAPS.md)（含 6+1 条契约缺口，第 7 条待 Astra 定夺）。

## 1. 交付内容

| 模块 | 文件 | 说明 |
|---|---|---|
| 纯领域逻辑 | `src/domain/classroom.ts` | 转写时间线合并（按段 ID 去重、高修订优先、迟到 final 按时间插回、有 final 后丢弃 partial）、三/双/单栏布局计算（最小宽度优先）、课后处理评估（五流 + 任务 + 本机积压 + 停止范围）、草稿存储、金额格式化 |
| 课堂数据与命令 | `src/features/classroom.ts` | 页面只经 `ClassroomShell` 访问采集端口；`startCapture`（先取服务端租约再本地开始）、`commandCapture`（暂停/继续本地先生效再告知服务端）、`stopCapture`/`confirmStop`（本地立即停止，停止范围以稳定幂等键保存，失败可在处理页重试） |
| API 客户端 | `src/api/client.ts` | 课堂、采集、清单、转写、标记、课件、媒体上传、生成计划、任务等接口；列表按契约上限 100 条翻页；媒体内容 PUT 使用 `application/octet-stream` 与 `Idempotency-Key` |
| 页面 | `src/pages/classroom/*` | 入口、准备、上课（三栏工作区、工具栏、专注模式、课件、详情）、课后处理、课堂记录 |
| Mock | `src/mock/classroom-backend.ts`、`classroom-shell.ts`、`classroom-feed.ts` 及 `backend.ts`/`start.ts`/`runtime.ts` 的接线 | 有状态合成后端（CAS、幂等、租约与 fence、清单封存、计划/任务、候选总结不自动采用）；PR-09a 页内 runtime 到课堂端口的适配；开发用转写事件源 |
| 壳 | `src/app/MockWorkbench.tsx` | 课堂路由（每个课堂独立挂载）、全局采集条“停止”走同一停止流程、开发面板“课堂模拟（PR-10a）” |

## 2. Mock 与真实联调的分界

- 课堂页面只挂在 `MockWorkbench`（mock 构建）；生产入口仍是 PR-03b 的 `AuthWorkbench`，`scripts/verify-build.mjs` 检查生产产物不含 Mock 入口。所有课堂页显示“开发预览：合成数据”。
- 未调用真实麦克风、真实 DashScope 或任何付费服务；浏览器不持有任何服务密钥。
- 契约外字段：无。`tests/pr10a-contract.test.ts` 对课堂相关 Mock 的读、写、错误响应逐一做 schema 校验，并证明出现额外字段会被拒绝；开发事件源产生的 partial/final/迟到/重复事件均通过 v1 事件 schema。

## 3. 需求逐条结论

结论只适用于合成 Mock 场景，不代表后端或真实设备已验证。证据缩写：**B** = `tests/pr10a.browser.spec.ts`（Playwright，VMware 来宾）；**D** = `tests/pr10a-domain.test.ts`；**K** = `tests/pr10a-backend.test.ts`；**C** = `tests/pr10a-contract.test.ts`。

| ID | 结论 | 证据 |
|---|---|---|
| UI-CLASS-01 | 通过（Mock） | 顶部状态与五流、中央学习区、底部工具栏；320px/200% 文字下无横向溢出且“停止”在视口内。B「320 px with 200% text…」 |
| UI-CLASS-02 | 通过（Mock） | 分隔条拖动/方向键/Home/End/双击重置、折叠与恢复、比例服从最小宽度、布局按设备记忆（只存布局）。D「classroom layout」4 条；B「three/two/one columns…」 |
| UI-CLASS-03 | 通过（Mock） | 专注模式只保留原文与必要控制；进出不中断采集、不丢草稿。B「three/two/one columns…drafts survive…focus mode」 |
| UI-CLASS-04 | 通过（Mock） | 准备页核对课程/标题/语言/模拟试音/额度与授权文字，勾选授权前“开始采集”不可用；创建课堂不开麦。K「creating a classroom never starts capture…」；B「creating a classroom never opens capture…」 |
| UI-CLASS-05 | 通过（Mock） | 暂停/继续/结束/重点/没懂/课件/详情键盘可达；继续采集先绑定服务端新发的 epoch 与 fence；未被确认的暂停/继续以原命令体和幂等键在联网后重放，离线点的“继续”联网确认后直接恢复采集；继续的响应丢失后结束课堂，停止会先取回继续回执、改按服务端新 epoch 封存；同一命令只对账一次；开始采集的响应丢失时以原键重放。B「pause/resume, marks…」「a pause sent offline is replayed on reconnect…」「an offline 继续 is completed on reconnect…」；K「resume retires the paused epoch…」「an offline 继续 confirmed on reconnect…」「a stop after a resume whose response was lost…」「concurrent reconciliations…」「a start whose response was lost…」 |
| UI-CLASS-06 | 通过（Mock） | 未开始、采集中、已暂停、本机已停止待确认、其他页面采集、已结束分别显示；租约失效后回到可明确重新开始的准备状态，不显示为待确认的停止。B「pause/resume…」「only one classroom captures…」「after a lost lease the classroom offers an explicit new start…」 |
| UI-CLASS-07 | 部分（Mock） | 临时/正式字幕以标签、样式、边框区分；回看不被强拉并提示新内容；迟到 final 按原时间插回；重复送达不覆盖。D「transcript timeline」4 条；B「partial vs final…」。个人稿修订入口归 PR-10b |
| UI-CLASS-08 | 部分（Mock） | 课件选择文件/粘贴、媒体上传三步、旋转/移除产生新版本；解释课件需在计划确认中另行勾选发送图片，无课件时拒绝而不回退到转写；课件修改带幂等键、重放返回原回执，标题按契约上限 160 字。K「slides go through upload…」「a slide-scoped plan needs slides…」「a replayed slide edit returns its receipt…」；B「pause/resume, marks…and slide versions」 |
| UI-CLASS-10 | 通过（Mock） | 五流逐项显示；AI 任务排队/进行/失败/未知均使整课“未完成”；未知结果只能核对、不能重试。D「processing assessment」；B「an unknown result can only be reconciled…」 |
| UI-CLASS-11 | 通过（Mock） | 结束（工具栏或全局“停止”）先本地停止并记录停止范围，再请求服务端封存；离线失败保留停止范围并可在联网后重试；本机未上传分片红色警告；材料不全只给“等待”与“生成部分范围总结”；结果是候选、不自动采用；退役 epoch 的未确认分片同样使上传保持待处理、总结只能按部分范围生成；有本机未上传音频或未确认停止时，任何页面关闭前都会提醒，已停止课堂的本机音频在开始另一节课堂后仍计入；处理页按整节课上传状态判断完整，早先 epoch 缺片时只提供部分范围总结。B「ending a class…」「the global stop records…」「a stop sent while offline…」「a stopped classroom with local audio still guards unload…」；K「incomplete material needs an explicit partial choice…」「outstanding chunks of a retired epoch…」；D「a class-wide pending upload keeps the material partial…」 |
| UI-CLASS-12 | 通过（Mock） | 容器宽度 ≥1160 三栏、772–1159 双栏、<772 单栏（PRD 初值 720 与最小宽度冲突：辅助栏需容纳问答标签，440+320+12=772，按 CF-14 最小宽度优先）；切换布局/路由/专注模式不丢草稿，两个课堂草稿互不串。D「uses container width breakpoints…」；B「three/two/one columns…」 |
| ASR-04 | 通过（Mock） | 回看不抢滚动；临时与正式非仅颜色区分；页面说明 ASR/AI/存储去向。B「partial vs final…」 |
| UX-01/02 | 通过（Mock） | 桌面三栏、手机单栏标签；全局课堂入口回到活动采集；站内导航保持采集，不隐式新建录音。B「creating a classroom never opens capture…」「only one classroom captures…」 |
| FE-01 | 通过（Mock） | 跨页面与切主题保持同一采集。B「…persists across pages and theme」 |
| FE-05 | 通过（Mock） | 上传确认、final、任务乱序到达时五流独立；停止后才到的持久回执也会更新服务端清单与上传状态。K「durable receipts after the stop…」 |
| FE-09 | 通过（Mock） | 部分总结显示缺失范围并需两次明确确认；候选不自动采用。B「ending a class…」 |
| 课堂撤权 | 通过（Mock） | 删除事件后页面不再显示缓存内容、不再提供标记/课件/AI 操作；服务端返回 `NOT_FOUND`/`GONE`/`FORBIDDEN` 时也不再显示缓存（只对暂时性错误保留）；Mock 服务端同时撤销访问，撤权后不重放旧回执（含创建课堂的回执）；采集中的课堂因课程级联删除不可访问时，采集与租约随之撤销；任何账号退出路径（切换、撤销按钮、`AUTH_REVOKED` 事件）都释放该账号的 Mock 租约。K「a revoked classroom is no longer served…」「a revoked classroom never replays…」「a creation receipt of a deleted classroom…」「an account teardown releases every lease…」；B「a classroom deletion removes its content…」「a classroom whose course was deleted shows no cached content…」「deleting the course of the capturing classroom…」「a session revocation during capture leaves no lease behind…」 |

## 4. 验证

全部测试在 VMware 来宾执行（宿主只做 `tsc`、`eslint` 静态检查，不作为通过证据）。最终运行：`claude-pr10a-f09`（2026-11-22 09:25–09:29 UTC），来宾 `TEST`（VMware，Node v24.19.0，Playwright Chromium 1248），源码快照 117 个文件逐一校验 SHA-256 无差异（基点 `fe4aafc`，含全部未提交改动），证据包 `evidence.zip` SHA-256 `963e11730c01795ee48ed3408663861afb0ea81879e010cfed95a3f8ef230773`。

| 步骤 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 契约生成检查 | `node scripts/gen-contracts.mjs --check` | 0 | 生成类型与冻结契约一致 |
| 类型检查 | `node node_modules/typescript/bin/tsc -b --pretty false` | 0 | — |
| Lint | `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | 0 | 0 警告 |
| 契约测试 | `vitest run tests/contract.test.ts tests/pr10a-contract.test.ts` | 0 | 2 个文件，155 条通过 |
| 单元测试 | `vitest run tests/unit.test.ts tests/api.test.ts tests/pr09b-*.test.ts tests/pr10a-domain.test.ts tests/pr10a-backend.test.ts` | 0 | 6 个文件，147 条通过 |
| 全部 Vitest | `vitest run --config tests/vitest.config.ts` | 0 | 12 个文件，404 条通过 |
| 构建 | `tsc -b` + `vite build --mode production` | 0 / 0 | — |
| 生产产物检查 | `node scripts/verify-build.mjs` | 0 | 7 个文件，无 Mock 入口标记 |
| 浏览器测试 | Playwright（`*.browser.spec.ts`，单 worker，来宾 Chromium） | 0 | 81 条通过，0 跳过，0 不稳定（含 PR-09a/09b 全部既有测试与 PR-10a 20 条） |

- 来宾测试配置与 `tests/playwright.config.ts` 相同，只把系统 Chrome 通道换成来宾已预置的锁定版 Playwright Chromium（来宾没有系统 Chrome）。
- 测试前检查：broker 在运行，队列中没有残留的 06c 任务，未动其他人的队列项；来宾 C: 盘运行前剩余 24.71 GB（只读探针 `claude-pr10a-disk-04`；此前依次为 14.06、24.82、24.76 GB），未删除来宾上的任何内容。
- 中间运行如实记录：`f03` 有 1 条 PR-10a 浏览器断言失败（新加的上传历史断言漏算了 PR-09a 演示课堂的初始分片，属测试错误；已改为按 epoch 断言）；`f04` 的应用代码与 `f05` 完全相同（116 个文件哈希一致），其中 PR-09a 既有测试「MOCK-01/02 provide empty and synthetic dashboard states in order」切到空数据场景后 5 秒内未出现“创建第一门课程”，一次失败，`f05` 原样重跑通过，判定为来宾负载下的偶发时序问题，未修改该测试。复审第 2 轮问题修复后的 `f06` 有 2 条新加的 PR-10a 浏览器测试失败，均为定位器问题：课堂名“全局停止”使采集条标题按钮与“停止”按钮同名匹配（已改为精确匹配），`getByLabel('课程')` 命中导航链接（已改为按 combobox 定位）；`f07` 全部通过。复审第 3 轮问题修复后的 `f08` 有 8 条浏览器测试与 1 条单元测试失败：5 条 PR-09a/09b 既有测试暴露了产品代码回退——新增的“绑定课堂被服务端判定不存在即撤销采集”把尚未注册到 Mock 服务端的 PR-09a 演示课堂也撤销了，已改为只对服务端曾成功返回过的课堂生效；其余 3 条浏览器测试与 1 条单元测试为新测试自身问题（课堂名含“删除”使 `/删除/` 定位器命中采集条；手动派发 `beforeunload` 会令 MSW 停止拦截，改为真实关闭页面检查确认框；额度断言取的是对象引用而非快照），已修正；`f09` 全部通过。
- 宿主只做静态检查：`tsc -b`、`eslint`，以及用临时配置（`work/` 下，不进仓库）对 4 个 PR-10a 测试文件做类型检查；均不作为通过证据。

- 截图（来宾证据包 `screenshots/`）：上课三栏、双栏、单栏、专注模式、课后处理部分范围、课堂记录等。
- 既有测试调整（1 处）：`tests/shell.browser.spec.ts` 中“活动课堂导航保留上传失败”一条，原断言跳转到 `/classrooms/mock/live`，现改为 `/classrooms/22222222-2222-4222-8222-222222222222/live`。原因：PR-10a 起全局课堂入口回到所绑定课堂自己的页面（UX-02），`/classrooms/mock/live` 仍作为兼容路由重定向到同一目标。断言的业务含义（回到活动采集、上传失败状态保留）不变。

## 5. 独立复审

用 astra-review（Codex，ultra，只读沙箱）复审四轮，详细记录见本机 `outputs/astra-review/20261121T214706Z-uncommitted-r2-XD3LeJ/final-report.md` 与 `outputs/astra-review/20261122T080236Z-uncommitted-r3-btYjB7/final-report.md`（`outputs/` 不进仓库）。第 2 轮后按规则停止并报告；用户随后两次明确授权超出 CLAUDE.md 的两轮上限：先“再修一轮”（第 2 轮发现，经 `f07` 验证），再“放开再复审”（第 3 轮，脚本上限临时调为 3 后已恢复为 2），之后又“再修一轮”（第 3 轮发现，经 `f09` 验证）。**第 3 轮发现的修复尚未再经独立复审。**

| 轮次 | 实际模型 | 状态 | 发现（P0/P1/P2/P3） | 处理 |
|---|---|---|---|---|
| 0 | gpt-6-astra / ultra | `review_completed`，门禁全部通过 | 0 / 2 / 8 / 0 | 全部成立，已修复并补测试 |
| 1 | gpt-6.2-astra / ultra | `review_completed`，门禁全部通过 | 0 / 5 / 17 / 1 | 全部成立，已修复并补测试 |
| 2 | gpt-6.2-astra / ultra | `review_completed`，门禁全部通过（首次运行因复审期间编辑本文档触发 `working_tree_unchanged` 而作废，经用户同意冻结工作区重跑） | 0 / 3 / 15 / 0 | 17 条成立、1 条为契约与后端实现不一致；经用户授权的额外一轮全部处理 |
| 3 | gpt-6.2-astra / ultra | `review_completed`，门禁全部通过（用户授权的额外复审） | 0 / 5 / 11 / 0 | 全部成立；经用户授权的再一轮全部修复并补测试，未再复审 |

第 2 轮问题在额外一轮中的处理：

- P1：权威错误（`NOT_FOUND`/`GONE`/`FORBIDDEN`）不再显示缓存内容；未确认的暂停/继续保留原命令体与幂等键，联网后重放，继续确认后才绑定新租约；退役 epoch 的未确认分片计入上传状态与总结完整性，退役 epoch 的清单可按 epoch 读取。
- P2（前端）：账号切换/撤销时释放 Mock 租约；关闭页面提醒移到外壳层，覆盖所有路由；原快照确认使用独立 UUID 幂等键；全局停止确认后刷新全部投影；确认发送图片时逐张列出课件标题与版本，字幕与图片分别计数；课件按采集时钟定位；全局采集条显示所绑定课堂的名称、暂停状态与采集时长；字幕默认使用账号阅读字号（14–32）。
- P2（Mock 保真度）：`taskUnknown` 等任务事件投影进任务表；时间窗只取完整落在窗口内的字幕，`marked` 只取被标记的来源；整节课范围包含无时间的 final；额度在首次执行时复核并预留；标记锚点覆盖契约全部变体并在本课堂内解析；转写与课件均保留旧修订，原快照可解析。
- 契约问题（交 Astra）：`GET /class-sessions/{id}/audio-manifest` 的 `capture_epoch_id` 在契约中可选，但后端 `capture_http.py` 要求必填。前端现在在已知 epoch 时传入（与契约和后端都兼容）；未知 epoch 的历史课堂仍按契约不传，接真实后端会得到 422，需要契约或后端一方调整。

第 3 轮问题的处理：

- P1：离线点的“继续”联网确认后直接恢复本地采集；继续的回执未知时结束课堂，停止请求等该命令有结论后再定稿（继续已生效则改按服务端新 epoch 与 fence 封存）；采集中的课堂经课程/学期级联删除而不可访问时，按撤权处理（停止采集、撤销 Mock 租约、清理待确认的停止与命令）；已停止课堂的本机未上传音频在开始另一节课堂后仍触发关闭提醒；所有账号退出路径（含 `AUTH_REVOKED` 事件和离线停止后切换账号）在账号作用域切换时统一释放该账号的 Mock 租约。
- P2（前端）：同一命令键的对账共享一次请求，回执只应用一次；开始采集保留原幂等键直到租约绑定或被明确拒绝；处理页按整节课上传状态判断完整；租约失效显示为可重新开始，不再显示为待确认的停止，并刷新服务端投影；全局采集条的 AI 状态同时参考所绑定课堂，未知结果优先；课件标题上限 160；课件修改携带契约要求的 `Idempotency-Key`；创建课堂的幂等键随请求内容变化而更换。
- P2（Mock 保真度）：创建课堂的幂等回执重放前复核访问；额度按任务结果一次性转移（运行中→已提交，成功或提交后失败→已结算，未知→未知且不退回，未提交即取消或失败→退回）；来源引用的 `content_sha256` 为该不可变修订内容的 SHA-256（字幕为文本，课件为图片摘要与旋转），标记校验同一摘要；课件修改走幂等回执。

## 6. 限制与遗留

- 合成数据，未与真实后端或设备联调；课堂实时事件订阅入口、AI 解释结果资源、07a 问答引用字段等契约缺口见 [PR10A-SCOPE-GAPS.md](PR10A-SCOPE-GAPS.md) 第 3 节。
- 错误细分依赖 `error.message` 文字的问题（06c 交接）只记录，待 Astra 定夺，10b 开工前处理；10a 只按 `error.code` 分支。
- 重转写入口、候选转写比较与采用、个人稿修订归 PR-10b（需单独授权）；Mock 对 `asr_retranscribe` 返回 503 `SERVICE_DISABLED`。
- PR-09a 页内 runtime 的合成分片序号从 1 开始，课堂适配层按契约换算为从 0 开始。
