# PR-09b · Dashboard、课程与设置 验收记录

日期：2026-11-15。负责人：林珈诚。工作包 PR-09b（无 GitHub PR 编号；未推送、未开 PR、未合并、未部署）。

- 工作树 `J:\class-assistant\worktrees\web-pr09b-courses`，分支 `feature/web-pr09b-courses`（无上游），基点 `main` = `81ab191b1f33de2371567eccd0ef9589d12483c2`，契约 `openapi.json` 0.2.0（未修改）。
- 写入范围：仅 `web/frontend/`。根仓库 `main` 上原有未提交的 `CLAUDE.md`、`.claude/`、evidence 文档未触碰、未带入。
- 开工前的范围与缺口清单：[PR09B-SCOPE-GAPS.md](PR09B-SCOPE-GAPS.md)。

## 1. 交付内容

| 模块 | 文件 | 说明 |
|---|---|---|
| 类型化 API 客户端 | `src/api/client.ts` | 覆盖 09b 用到的全部契约接口；写操作带 `Idempotency-Key`、可选 `X-CSRF-Token`；沿用账号 epoch 检查，旧 epoch 的写请求不会发出 |
| 纯领域逻辑 | `src/domain/flows.ts`、`dashboard.ts`、`courses.ts` | 五流投影（没有单一“完成”捷径）、继续入口优先级、真实待办推导、表单校验、URL 筛选状态、站内返回路径白名单 |
| 页面数据层 | `src/features/data.tsx` | 页面只通过 `AppData` 取得 API、账号作用域、在线状态与采集 runtime 视图；私人查询键首段 `['account', accountKey, …]` |
| Dashboard | `src/pages/Dashboard.tsx` | 替换 PR-09a 的静态示意页 |
| 课程 | `src/pages/courses/*` | 列表、学期/课程表单、四标签详情、改归属、归档、删除影响对话框 |
| 设置 | `src/pages/settings/Settings.tsx` | 11 个分区路由；09b 范围外的分区明示“尚未开放”及所属 PR |
| Mock | `src/mock/backend.ts`、`src/mock/start.ts` | 有状态合成后端（A/B 隔离、CAS、幂等、删除预检），仅 mock 模式加载 |

## 2. Mock 与真实联调的分界

| 接口 | 后端状态 | 本 PR |
|---|---|---|
| `GET /courses/{course_id}` | 已实现（`CourseReader`） | 课程详情使用同一 client 方法。**未在浏览器中与真实后端联调**：生产入口目前仍是 PR-03b 的 `AuthWorkbench`，而 Dashboard/课程所需的其余接口后端都不存在，接入生产壳会让页面主体失败。Mock 返回与 `CourseReader` 同一组字段（契约测试以 `additionalProperties:false` 校验） |
| `/me/sessions`、`/me/devices` 及撤销 | 已实现（PR-03b） | 生产环境由 `AuthWorkbench` 使用，本 PR 不改动；设置页在 Mock 壳中使用同 schema Mock |
| 学期、课程列表/写入、课堂、资料、复习文档、疑问、通知、`/me`、偏好、额度、`/meta` | 只有契约 | 全部为 Mock |

- UI 标注：Mock 壳顶部保留 PR-09a 的“开发预览 · 合成数据”条；每个 09b 页面另有一行“开发预览：以下课程、课堂和设置均为合成数据……由 Mock 按已批准契约提供”。
- 编译期分离：所有 09b 页面只从 `MockWorkbench` 引用，生产入口不包含 Mock、假账号或假数据 fallback；`scripts/verify-build.mjs` 检查生产产物。
- 契约外字段：无。`tests/contract.test.ts` 对每个 Mock 读写及错误响应做 schema 校验，并证明出现额外字段会被拒绝。

## 3. 需求逐条结论

结论只适用于合成 Mock 场景，不代表后端或真实设备已验证。

证据缩写：**B** = `tests/pr09b.browser.spec.ts`（Playwright，VMware 访客）；**D** = `tests/pr09b-domain.test.ts`；**K** = `tests/pr09b-backend.test.ts`；**C** = `tests/contract.test.ts`。截图位于 VMware 证据包 `screenshots/`（见第 4 节）。

### Dashboard

| ID | 结论 | 证据 |
|---|---|---|
| UI-DASH-01 | 通过（Mock） | 首屏是继续学习与课程，没有录音首页或统计大屏。B「module order…」；截图 `dashboard-seeded-day.png` |
| UI-DASH-02 | 通过（沿用 09a 壳） | 导航、折叠和键盘可达由 PR-09a 壳提供并由其测试覆盖（`shell.browser.spec.ts`）；09b 页面内容宽度在 320px 和 200% 字号下无横向溢出。B「pages reflow… day/night」 |
| UI-DASH-03 | 通过（Mock） | 有数据（种子数据）和空数据（MOCK-01）两种状态下模块顺序都是 继续→快速开始→最近课→待办→动态；空态没有数字占位。B「module order…」；截图 `dashboard-empty-day.png` |
| UI-DASH-04 | 通过（Mock） | 优先级：活动采集 > 本地未存风险 > 最近课堂与最近个人笔记中较新的一项（时间相同时取课堂，结果与输入顺序无关）。D「continue entry」4 条；B「continue entry priority…」 |
| UI-DASH-05 | 通过（Mock） | 按真实 `started_at`（缺省 `created_at`）排序，时间相同按 ID；不显示未来排课。D「recent sessions order…」 |
| UI-DASH-06 | 通过（Mock） | 冲突/失败/结果未知/未同步/临近到期/未懂均来自记录，可点击定位；问题解决后移除；被删课堂的问题不出现。D「derives each kind…」「ignores questions of deleted classes…」；B「five flows…」 |
| UI-DASH-07 | 通过（Mock） | 五流逐项展示；录音正常但上传失败、录音结束仍待上传都不显示整课完成。D「flows」3 条；B「five flows…」 |
| UI-DASH-08 | 通过（Mock） | 无积分/签到/连续学习/掌握度/学习时长文案（B 断言正文不含这些词）；日/夜两套主题截图 `day-320-200pct-*`、`night-320-200pct-*`、`settings-appearance-night.png` |
| UI-DASH-09 | 通过（Mock） | 新用户：创建第一门课程入口；老用户：继续入口；离线：明示可用范围并停用写操作；删除课程后首页、待办与课程 URL 不留失效入口。B「module order…」「offline…」「deleting a course…」；D「deleted classes…」 |

### 课程

| ID | 结论 | 证据 |
|---|---|---|
| UI-COURSE-01 | 通过（Mock） | 学期→课程→课堂以稳定 ID 关联；改名不改 ID；同名课程不合并。K「same-name courses…」；B「same-name courses stay separate…」 |
| UI-COURSE-02 | 通过（Mock） | 学期创建/编辑、日期合法性与结束≥开始校验（前端与 Mock 双重）；`base_revision` 冲突时保留输入并可选择覆盖或放弃。D「semester…」；K「semester dates…」「CAS…」；B「semester CAS conflict…」 |
| UI-COURSE-03 | 通过（Mock） | 学期优先；搜索、排序、卡片/列表、只看已归档全部写入 URL，刷新、进入详情再返回都保持；只显示本人课程。D「list URL state…」；B「semester-first list…」「A/B isolation…」 |
| UI-COURSE-04 | 通过（Mock） | 卡片字段只来自契约 `Course`（名称、英文名、语言、色/图标 token、课堂数、最近课堂时间）；长名称换行不溢出；归档/改归属/删除后数量一致。B「a very long course name wraps…」「moving a class…」「archive keeps content…」 |
| UI-COURSE-05 | 通过（Mock） | 名称和学期必填，语言默认 `auto`，教师/教室/英文名可留空；校验失败时焦点移到第一个错误字段；同名可创建且 ID 独立。D「course: name and semester required…」；B「required fields, defaults and keyboard focus…」 |
| UI-COURSE-06 | 通过（Mock） | 概览/课堂/资料/复习是真实子路由；键盘切换后焦点移到面板标题；新课程四个标签都有空态；未知标签重定向到概览。B「four tabs…」 |
| UI-COURSE-07 | 通过（Mock） | 概览的继续、最近课堂、疑问与资料都来自同一课程的记录，不显示计划排课。B「four tabs…」（概览与复习）；截图 `*-courses_…_overview.png` |
| UI-COURSE-08 | 通过（Mock） | 课堂表显示时间、时长、五流状态；改归属需在对话框明确选择目标课程，带 `base_revision`；采集时间和历史来源不变，两门课的课堂数同步更新。K「moving a class…」；B「moving a class is explicit…」 |
| UI-COURSE-09 | 通过（Mock） | 资料页只管大纲、术语词表和资料语言；页面说明课件照片和板书在各课堂中管理；改语言提示只影响之后课堂的默认值；保存带版本号。K「course materials…」；B「archive keeps content… materials save with versioning」 |
| UI-COURSE-10 | 部分（09b 范围内通过） | 课程复习标签按 `course_id` 过滤同一批 `ReviewDocument`，不复制、不串课，类型/理解状态筛选写入 URL。全局复习页与编辑属于 PR-10b。K「course review filter…」；B「four tabs…」 |
| UI-COURSE-11 | 通过（Mock；离线缓存仅为页面内缓存） | 归档保留内容且可撤销；删除先取新的影响预览（课堂/资料数量），可取消，可选级联或移到其他课程；离线时已读取的课程元数据只读显示，写操作全部停用。K「archiving…」「deletion requires a fresh preview token…」；B「archive keeps content…」「deleting a course…」「offline…」 |
| UI-COURSE-12 | 预留（按授权范围） | 契约中没有计划课实体（缺口 G5）。页面只展示实际课堂，并写明“不会显示计划排课”；没有实现课表，也不修改历史课堂。 |
| UI-REVIEW-02（部分） | 部分 | 只实现课程内的类型/理解状态筛选与 URL 返回上下文；学期/课堂维度的全局组合筛选在 PR-10b。 |

### 设置与冲突裁决

| ID | 结论 | 证据 |
|---|---|---|
| 设置·账号/设备/隐私/偏好/额度 | 通过（Mock） | 11 个分区路由；个人资料与字号偏好带 revision 保存；设备会话可撤销；额度页展示“结果未知，待核对”；数据与缓存、隐私导出等不在 09b 范围的分区明示“尚未开放”并注明所属 PR；未知分区回到外观。B「AI preferences…」「profile and reading preference…」 |
| CF-15 | 通过 | AI 偏好表单没有任何文本输入框，也没有 Key/URL/模型地址字段，并明示不支持自带 API Key。B「AI preferences expose no key…」 |
| CF-09 | 通过 | 没有虚构学习统计、排程或评分；空账号不显示任何数字占位；p21 只作视觉参考。B「module order…」 |

## 4. 检查命令、退出码与数量

最终一次运行在 VMware 访客 `TEST` 中完成（Node v24.19.0，2026-11-16T00:50:22Z–00:52:24Z，broker 任务 `claude-pr09b-20261115-checks-05`）。源码为本工作树快照：81 个文件，逐文件 SHA-256 校验 0 个不一致，基点 `81ab191`，`source.zip` SHA-256 `2ce1dffc6e4d0f00870530835dd53198e124d63e0fe8f70d035059d1b5cae8dd`。访客中直接用 `node` 调用与 `package.json` 脚本相同的命令（访客里没有 pnpm shim），步骤一一对应：

| package.json 脚本 | 访客中执行的命令 | 退出码 | 数量 |
|---|---|---|---|
| `pnpm typecheck` | `node scripts/gen-contracts.mjs --check` → `tsc -b --pretty false` | 0 / 0 | 生成类型与契约一致 |
| `pnpm lint` | `eslint . --max-warnings 0` | 0 | 0 错误 0 警告 |
| `pnpm test:contract` | `vitest run … tests/contract.test.ts` | 0 | 1 文件，148 通过 |
| `pnpm test:unit` | `vitest run … unit/api/pr09b-domain/pr09b-backend` | 0 | 4 文件，92 通过 |
| （补充）全部 Vitest | `vitest run --config tests/vitest.config.ts` | 0 | 8 文件，274 通过 |
| `pnpm build` | `tsc -b` → `vite build --mode production` → `node scripts/verify-build.mjs` | 0 / 0 / 0 | 生产产物 7 个文件，未发现 Mock 入口标记 |
| `pnpm test:ui` | `playwright test`（配置差异见第 5 节第 5 条） | 0 | 55 通过，0 失败，0 跳过，0 flaky；其中 `pr09b.browser.spec.ts` 19 条，其余 36 条为 09a/03b 已有用例 |

- 证据包：`outputs/pr09b/vm-run-05/evidence.zip`（SHA-256 `e9c69c64a17c867a47f635ab22b69c90fcb4f0963c076d5ca3375981bbf8abbb`），内含各步骤日志、`checks.json`（逐步退出码与耗时）、`playwright.json` 和 19 张截图。`typecheck-*`、`lint`、`build-tsc` 成功时没有输出，日志为空文件，`Compress-Archive` 不打包空文件，所以包里没有这几份日志；它们的结论以 `checks.json` 中的退出码为准。`outputs/` 被 Git 忽略，不随提交。
- 过程如实记录：第 01–04 次运行失败，均已修复。
  - 01：访客无网络导致 `navigator.onLine=false`；J: 盘 subst 路径与 Vitest 的 realpath 不一致；定位器有歧义。
  - 02：场景 05 的测试步骤写错；未知标签的断言写错；只看已归档复选框在路由 transition 中短暂回到旧状态（页面缺陷，已改为本地镜像状态）；离线时重新请求失败会把已读取的数据替换成错误页（页面缺陷，已改为保留缓存，确认删除的 404 除外）。
  - 03–04：表单名称与标签子串歧义（测试定位器问题）。
- 宿主机只做了 tsc 和 eslint 静态检查，不作为通过证据。

## 5. 已知限制

1. **没有与真实后端在浏览器中联调。** 后端目前只实现 `GET /courses/{id}` 与 PR-03b 的会话/设备接口，生产入口仍是 `AuthWorkbench`。09b 页面全部在 Mock 壳中验证，结论不代表真实后端行为。
2. **离线与同步（08a/08b）只做到契约/Mock 层。** 离线时只能显示本页面会话中已读取（TanStack Query 内存缓存）的元数据，刷新后不保留；没有持久化学习缓存、离线队列或同步。
3. **契约缺口 7 项**，见 [PR09B-SCOPE-GAPS.md](PR09B-SCOPE-GAPS.md)：资料首次写入的 `base_revision`、课堂列表排序/学期筛选、课程最近课堂 ID/标题、`/questions` 只能按课堂过滤、计划课实体、通知只有 `message_code`、删除预检不返回合法的 `move_children` 目标。Mock 没有为这些缺口创造字段，需要 Astra 按交接文档第 7 节决定是否变更契约。
4. **“临近到期”阈值 48 小时**是前端常量（`EXPIRY_WARNING_MS`），属于产品参数，待产品确认。
5. **test:ui 配置差异**：VMware 访客没有系统 Chrome，生成的配置与 `tests/playwright.config.ts` 只差浏览器通道（改用访客中已锁定版本的 Playwright Chromium），其余选项一致。
6. **测试环境分工**：按仓库 AGENTS.md，全部测试在 VMware 访客中运行；宿主机只做编辑和静态检查（tsc、eslint），不作为通过证据。
7. 没有调用真实收费模型/ASR，没有使用真实麦克风、真实课堂资料或真实 Key。
