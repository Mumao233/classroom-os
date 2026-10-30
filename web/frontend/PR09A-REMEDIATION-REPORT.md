# PR-09a 第二轮整改与复审记录

## 第五轮定点整改（2026-10-30～31）

从 `feature/web-pr09a-aurora` 的 `35a6bffbe83b76e99395f14c277888902a72e0df` 继续，保留此前全部提交。第四轮独立复审已关闭历史 pending 分片问题，本轮只修复旧记录已核对时掩盖新写入失败的 P2；本地结果等待 Astra 第五轮独立复审。

### 状态和记录身份

修复前，旧记录的 `reconciled` 状态可在新 epoch 写入因 Vault 锁定失败、解锁及切场景之后，被错误投影为当前已同步。现在 `src/mock/runtime.ts` 为每次写入尝试分配 `attemptId`、采集 epoch 和结果；每个成功记录另有 `recordId`、epoch、版本键及独立核对状态。最新失败优先于旧记录核对状态；只有成功重试生成的新记录先进入 `local_only`，且对应记录收到模拟核对后才显示 `synced`。重复或迟到的旧记录核对不改变新记录或最新失败的投影。

`src/mock/ports.ts` 的内存 Vault 原子写入版本记录与当前别名，失败、锁定或配额不足不留下半写入。旧版本保留，账号切换/撤权锁定并清空旧实例。`src/app/MockWorkbench.tsx` 在模拟区展示记录历史，并区分当前锁定与上次写入因锁定被拒绝。它们均为合成内存行为，不是加密持久化或真实云端核对。

### A–H 状态转换矩阵

以下由 `tests/unit.test.ts` 中 `fifth review: attempt and saved-record identity matrix` 验证 Runtime 的页面同步投影、实际 MemoryVault 版本内容/身份及旧记录保留；B 另由 `tests/fifth-review.browser.spec.ts` 中 `a failed new epoch cannot inherit an old reconciled Vault record after unlock and scene changes` 在真实 Chrome 连续操作验证页面投影、旧/新 epoch 与记录历史。每项均通过。

| 项 | 测试与期望 |
|---|---|
| A | 旧记录 pending → 新写入失败 → 解锁、切场景：旧内容不变，最新尝试和同步投影保持失败。 |
| B | 旧记录 reconciled → 新写入失败 → 解锁、切场景：旧记录保持已核对，最新尝试仍失败；Chrome 验证实际页面不显示已同步。 |
| C | 重复解锁：保持相同失败尝试、记录和失败投影。 |
| D | 新采集开始：不替换失败尝试或把旧 epoch 的已核对记录当成当前成功。 |
| E | 失败后成功重试：生成新 attempt/record ID，实际新版本存在、旧版本保留，新记录为待核对及 `local_only`。 |
| F | 核对对应新记录后才进入 `synced`，旧记录仍保留。 |
| G | 旧记录迟到或重复核对不改变新 pending 记录；另测旧 pending 的真实迟到核对不掩盖新失败。 |
| H | 失败后切账号或撤权：清理尝试及历史；保留旧 Vault 引用并重新解锁，确认两个版本及当前别名均已清空。 |

另测配额、写入失败和锁定时版本键及当前别名的原子性。旧的 52 项契约、41 项单元及 35 项浏览器测试均保留，包括旧 pending 分片迟到回执、租约和事件去重等回归；未修改其断言。

### 隔离验证及证据

Windows，Node v24.19.0、pnpm 10.34.9、Vitest 4.1.12、Chrome 156.0.8078.48。`work/pr09a/fifth-review-check/web/frontend` 从批准基线独立检出，`corepack pnpm install --frozen-lockfile --offline --store-dir <项目 store>` 退出 0；实际被测文件与正式工作树逐项 SHA-256 一致。依赖和临时目录位于项目 `work/pr09a/`。

| 实际命令 | 结果 |
|---|---|
| `corepack pnpm typecheck` | 退出 0 |
| `corepack pnpm lint` | 退出 0 |
| `corepack pnpm test:contract` | 退出 0，52/52 |
| `corepack pnpm test:unit` | 退出 0，51/51 |
| `corepack pnpm build` | 退出 0，生产构建的已知 Mock 入口标记检查通过 |
| `corepack pnpm test:ui` | 退出 0，36/36，包含生产预览和 Chrome 场景 B |
| 冻结契约对批准基线 Git diff | 退出 0，无变更 |

同一基线复现测试修复前退出 1（错误 `synced`），修复后退出 0、1/1 通过；源码与 `baseline.log`、`fixed.log` 保存在 `outputs/pr09a/fifth-review/repro/`。Chrome 基线失败与修复后通过日志在 `outputs/pr09a/fifth-review/ui/`；完整门禁日志、`results.txt` 和源码哈希检查在 `outputs/pr09a/fifth-review/`。这些证据与隔离依赖被 Git 忽略，不包含在提交内。生产 Mock 标记检查与预览测试不是全面安全审计。

### 变更、剩余边界与回退

本轮提交文件：`src/mock/runtime.ts`、`src/mock/ports.ts`、`src/app/MockWorkbench.tsx`、`tests/unit.test.ts`、新增 `tests/fifth-review.browser.spec.ts`、`PR09A-ACCEPTANCE.md` 和本报告。未改导航样式、冻结契约、后端或其他工作树。跨课堂脚本复用领域实体 ID 的 P3 仍为 **OPEN**，最迟在 PR-10a 接入真实课堂事件前处理；第四轮记录保留其具体场景。真实 IndexedDB、加密 Vault、云端核对、认证、音频、ASR 和收费服务未实现或测试；原生文字缩放、真实设备和跨浏览器检查仍是后续验收边界。

回退时核对本轮完整 SHA 与后续依赖，使用 `git revert <本轮修复提交完整 SHA>` 创建反向提交并审阅冲突；不 reset、clean 或覆盖历史证据。本轮不 Push、Merge、Tag、Release、部署或开始后续 PR。

---

## 第四轮针对性整改（2026-10-30）

从 `feature/web-pr09a-aurora` 的 `edce43ecb6792e0d85dd6a91fbb0ea5431f297c0` 继续；此前检查点与历史均保留。以下结果限本地合成数据与内存 Mock，等待 Astra 第四轮独立复审。

| 问题 | 修复前 | 本轮修复与证据 |
|---|---|---|
| P2-01：重复解锁 | 保存后对已解锁 Vault 再解锁，再切场景和开始采集，记录仍在却显示 `none/synced`。 | `src/mock/runtime.ts` 分别保存锁状态和 `localRecordSync`；重复解锁无状态变更，场景切换保留记录与待核对状态，只有显式模拟核对成功才标记已同步。切号、撤销仍锁定并清理旧实例。`tests/unit.test.ts` 的 `keeps an unverified actual vault record and sync projection through repeated unlocks, scenarios and capture`、`never marks the old record synced when an attempted write is rejected by a locked vault` 和 `tests/fourth-review.browser.spec.ts` 的 `repeated unlock and scene changes preserve the saved record and pending reconciliation` 检查实际 Vault 内容及页面状态。 |
| P2-02：旧轮 pending 丢失 | 第一轮 frame ACK 后停止、启动第二轮时，旧 UploadPort 被替换，迟到 durable ACK 无法补入第一轮。 | Runtime 按课堂、epoch、chunk 和序号保留各轮内存分片与端口；frame ACK 仅表示收到帧，durable ACK 更新匹配轮次历史，只有当前轮回执改变当前上传投影。旧轮重复回执保持幂等；撤权、租约失效及账号切换的受保护作用域不能接收回执。开发模拟区展示历史与有效迟到回执操作。`tests/unit.test.ts` 的 `accepts interleaved old durable receipts in three independent epochs without altering the active chunk`、`retains pending history across scenario changes but rejects old receipts after revocation or account change`、`refuses late receipts for lease-lost or classroom-revoked scopes, including after scene reset` 和 `tests/fourth-review.browser.spec.ts` 的 `late durable updates its historical chunk while the new capture remains pending`、`a revoked lease does not offer an invalid historical receipt action` 覆盖同序号三轮交错、去重、撤权与页面投影。 |

**P3 技术债，未修复：**`src/mock/event-scripts.ts` 固定转写的 `stream_id`、`segment_id` 及任务 `task_id`；`bindScriptEvent` 切换课堂时只替换 `class_session_id`，但转写 Map 以 stream/segment 为键，任务 Map 以 task ID 为键。同一账号对不同课堂重放脚本时，B 课堂转写可能被 A 课堂的 final/revision 拦截，B 的任务事件可能因已有 A 课堂的同 ID 任务而被忽略。建议最晚在 PR-10a 接入真实课堂事件前修复模拟实体身份并增加跨课堂回归。本轮未改动这项 P3，也未声称关闭。

### 隔离测试与截图

Windows，Node v24.19.0、pnpm 10.34.9、Vitest 4.1.12、Chrome 156.0.8078.48。最终环境 `work/pr09a/fourth-review-check/web/frontend` 从起点独立检出，执行 `corepack pnpm install --frozen-lockfile --offline --store-dir <项目 store>` 退出 0；测试源码与正式工作树逐项哈希核对。未更动原有依赖目录。

| 实际命令 | 结果 |
|---|---|
| `corepack pnpm typecheck` | 退出 0 |
| `corepack pnpm lint` | 退出 0 |
| `corepack pnpm test:contract` | 退出 0，52/52 |
| `corepack pnpm test:unit` | 退出 0，41/41 |
| `corepack pnpm build` | 退出 0，已知 Mock 入口标记检查通过 |
| `corepack pnpm test:ui` | 退出 0，35/35 |

同一组两项独立复现测试在原始 `edce43e` 上 2/2 失败、退出 1，修复后 2/2 通过、退出 0；原始源码、`baseline.log`、`fixed.log` 和环境说明保存在 `outputs/pr09a/fourth-review/repro/`。页面基线失败与修复后日志保存在 `outputs/pr09a/fourth-review/ui-check/`。最终检查日志及截图位于工作树忽略的 `outputs/pr09a/fourth-review/`，不包含在提交中；原有测试未删除、断言未降低。

新的 390×844 Day/Night 稳定选中截图及放大导航图位于 `visual/`。Chrome 正常动态下点击“课堂”、等待 600ms，再以 120ms 间隔采样四次，动画数为 0，`aria-current=page`，选中层和入口均为 x=195、y=758、86.5×66；测量保存在 `stable-selection-measurements.json`。桌面 Chrome 移动视口并非真实 iPhone 或物理折射验证。

### 变更与边界

本轮提交文件：`src/mock/runtime.ts`、`src/app/MockWorkbench.tsx`、`tests/unit.test.ts`、新增 `tests/fourth-review.browser.spec.ts`、`PR09A-ACCEPTANCE.md` 和本报告。未修改冻结契约、后端、其他工作树或 Windows 本地版；未改变已批准导航的样式。内存记录不是 IndexedDB、真实音频持久化、加密 Vault 或云端恢复；真实认证、ASR、AI、收费服务、设备及服务器授权均未验证。已有原生文字缩放及跨浏览器限制仍在。

回退时先核对本轮提交 SHA 与后续依赖，使用 `git revert <本轮修复提交完整 SHA>` 创建反向提交并审阅冲突，不用 reset、clean、强制覆盖或删除历史证据。本轮不 Push、Merge、Tag、Release、部署或开始后续 PR。

---

## 第三轮针对性整改（2026-10-30）

在 `feature/web-pr09a-aurora` 从 `60b0eaf8f8da978b541f9480b2fcaf486a4fad37` 继续，保留全部历史。以下为本地合成模拟结果，等待 Astra 第三轮独立复审；本轮未重新设计导航，也不宣布上一轮两项部分关闭问题已关闭。

| 问题 | 修复前 | 修复后及代码位置 |
|---|---|---|
| P2-01：续租去重 | 模板事件沿用相同 `event_id`，第二轮 durable 与 lease loss 被第一次投递的去重记录吞掉。 | `src/mock/runtime.ts` 的 `scriptedId` 和 `bindScriptEvent` 为逻辑模板与采集 epoch/fence/chunk 组合缓存稳定事件身份；同一事件重复投递复用 ID，新生命周期获得新 ID，durable `receipt_id` 也按资源稳定绑定。去重和旧作用域检查保留。 |
| P2-02：Vault 接线 | `simulateLocalWrite` 直接改同步状态，不调用 Vault；页面自己的局部状态可错误显示成功。 | Runtime 实际调用 `MemoryVault.write`，依据 `stored`、`locked`、`quota`、`write_failed` 投影结果；页面 `MockWorkbench.tsx` 展示 Runtime 返回的状态和合成记录。切号或撤销会锁定并清空旧实例。停止重启及锁定解锁不会把未核对记录伪称已同步。仅为内存替身。 |
| P2-03：新采集继承旧确认 | 停止后新建 CapturePort，但旧 UploadPort 和 durable 投影仍在，出现 pending 与已确认并存。 | `start` 为新轮次建立新的 epoch、fence、chunk 与 Capture/Upload 端口，清除当前 ACK 投影；`durableUploads` 保留同账号旧轮合成持久回执。错作用域旧事件不能改变新轮。场景 03 的持久示例也经同一事件及端口路径。 |
| P3：稳定选中截图 | 原 selected 截图可能处于动画中间帧。 | Chrome 正常动态下点击“课堂”，等待 600ms 后每隔 120ms 测量四次，确认无运行中动画、`aria-current=page`、浮层和入口坐标一致后截图。390×844 两主题截图及局部放大图在 `outputs/pr09a/third-review/visual/`；原截图保留。 |

新增单元测试位于 `tests/unit.test.ts`：`replays stable logical ids across two lease lifecycles without accepting old receipts or losses`、`starts a fresh upload lifecycle after stop`（首轮 pending/durable 两种情况）、`uses the real MemoryVault result and locks and clears old account data on switch and revocation`、`does not mark a saved local record synced by restarting capture or locking and unlocking`、`records scenario durable receipts through the same upload event path`。测试保留旧 Vault 引用，在锁定后重新解锁以验证内容确已清空，而非仅不可读取。

新增 `tests/lifecycle.browser.spec.ts` 三个用例：`two capture lifecycles show only their own upload acknowledgement`、`both renewed lease lifecycles accept their own durable and lease-loss events`、`mock Vault results shown on the page track writes, locking, failure and account switch`；覆盖两轮采集/租约、五流投影、真实合成记录、失败、锁定、切号及撤销后重新登录。原测试未删除、断言未降低。

### 本轮隔离验证

Windows，Node v24.19.0、pnpm 10.34.9、Vitest 4.1.12、Chrome 156.0.8078.48。目录 `work/pr09a/third-review-check/web/frontend` 从整改起点独立检出，执行 `corepack pnpm install --frozen-lockfile --offline --store-dir <项目 store>` 退出 0；被测修改文件与正式工作树逐项哈希核对。依赖与临时文件使用项目 `work/pr09a/`，未更动原有依赖目录。

| 实际命令 | 结果 |
|---|---|
| `corepack pnpm typecheck` | 退出 0 |
| `corepack pnpm lint` | 退出 0；最终浏览器测试补强后复跑退出 0 |
| `corepack pnpm test:contract` | 退出 0，52/52 |
| `corepack pnpm test:unit` | 退出 0，36/36 |
| `corepack pnpm build` | 退出 0，已知 Mock 入口标记检查通过 |
| `corepack pnpm test:ui` | 最终退出 0，32/32 |

同一组独立复现测试在原始 `60b0eaf` 上三项均失败（第二轮 durable、未调用 Vault、重启继承确认），修复后相同测试 3/3 通过；失败及通过日志分别是 `outputs/pr09a/third-review/baseline-repro.log`、`baseline-repro-after.log`，测试源保留在同目录。浏览器基线复现失败、修复后通过日志保存在 `outputs/pr09a/third-review/ui-agent/`。最终完整日志位于 `outputs/pr09a/third-review/`；截图、日志和隔离依赖被 Git 忽略，不包含在提交内。

### 截图、变更清单和剩余限制

稳定选中截图为 `visual/react-390-day-selected-stable.png` 和 `visual/react-390-night-selected-stable.png`；对应放大的导航截图及 `stable-selection-measurements.json` 同目录。两主题导航均为 x=16、y=752、358×78；选中层与当前入口均为 x=195、y=758、86.5×66；四次采样一致。截图环境为桌面 Chrome 的 390px 视口，不代表真实 iPhone 的 Safe Area 或物理折射。

本轮提交文件：`src/mock/runtime.ts`、`src/app/MockWorkbench.tsx`、`tests/unit.test.ts`、新增 `tests/lifecycle.browser.spec.ts`、`PR09A-ACCEPTANCE.md`、本报告。未修改冻结契约、其他工作树、后端或 Windows 本地版。Mock 内存历史和 Vault 并非真实持久化、加密、认证、录音或服务端授权；原生文字缩放、真实设备/读屏器和跨浏览器等已有缺口仍待验证。无 PostgreSQL、真实服务或收费调用。

若需安全撤销，在确认本轮修复提交 SHA 后执行 `git revert <SHA>`，审阅后续依赖和冲突，保留此前提交及证据；不执行 reset、clean 或强制覆盖。不 Push、Merge、Tag、Release、部署或开始后续 PR。

---

2026-10-30，中国时间。工作树 `J:\class-assistant\worktrees\web-pr09a-aurora`，分支 `feature/web-pr09a-aurora`；整改起点 `b134db9e1eee54c18fd3147accc39932a91e3186`，保留检查点 `0e1c818c4e1a583de8c3a378b3bc00d58c4b1bde` 和 PR-01 基线 `1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f`。本报告的通过均限定于本地合成数据，等待 Astra 独立复审；不是生产安全或真实服务验收。

## 问题、修复与新增测试

| 项目 | 原问题与本轮处理 | 主要代码与测试 |
|---|---|---|
| P2-01 | 课堂撤权未停止采集，失效租约可复用。现立即停止端口并使作用域失效；启动检查账号、课堂和有效租约；显式续租创建新的 epoch、fence 与 chunk，旧事件不影响新作用域。场景重置重建订阅、端口和墓碑，保留同账号未核对任务。 | `src/mock/runtime.ts` 的 `handleEvent`、`start`、`acquireMockLease`、`chooseScenario`；`tests/revocation.browser.spec.ts` 两个浏览器用例，`tests/unit.test.ts` 的撤权、续租、错课堂及重置用例。 |
| P2-02 | 不同任务共用 AI 状态，成功事件掩盖其他 unknown 费用风险。现按 task ID、课堂和 revision 存储，投影当前课堂并汇总跨课堂风险；同账号切换场景仍保留风险，同任务新版本的可信模拟终态才能解除。账号切换/撤销清理本地账号作用域，不表示服务端已核对。 | `src/mock/runtime.ts` 的 `taskProjection`、`task.changed`、`switchClassroom`、`chooseScenario`；`tests/unit.test.ts` 的 `isolates tasks by classroom...` 和 `preserves unknown charge risk across scene changes...`。 |
| P2-03 | Windows CRLF 导致生成文件与 LF 输出逐字节比较失败。前端 `.gitattributes` 仅对 `src/generated/*.ts` 固定 LF，不修改契约。真实 Windows `core.autocrlf=true` 的独立干净检出、锁文件安装、类型检查、再次生成均验证；生成后 Git 无差异，普通源码仍为 CRLF。 | `web/frontend/.gitattributes`；`outputs/pr09a/second-review/windows-crlf.log`。 |
| P2-04 | 页面直接 fetch，端口测试与页面状态分离。课程 GET 走统一 client，集中处理 HTTP/网络错误并在响应和解析前后检查账号 epoch；页面发命令给 Runtime，Capture、Upload、Event 的状态与页面使用相同端口。生产构建保留 Mock 隔离。 | `src/api/client.ts`、`src/app/MockWorkbench.tsx`、`src/mock/runtime.ts`；`tests/api.test.ts` 和 `tests/ports.browser.spec.ts`，覆盖迟到响应、页面录音、帧确认、持久确认及切号后端口状态。 |
| P2-05 | 旧测试只改变根字号，未放大固定像素文字。保留旧断言并更正名称；新增真实 Chrome 渲染的用户样式模拟，先测原始计算字号，再对每个既有元素设置 200%，逐项验证标题、课程名、正文和导航文字，并检查 320px 溢出、导航、停止、对话框、长名称、公式与操作。 | `tests/text-scaling.browser.spec.ts` 三个用例；`tests/shell.browser.spec.ts` 旧名称更正。该方法是逐元素文字样式模拟，不是浏览器原生文字缩放，也不是整页缩放；新插入 DOM 需重新应用。原生设置、真机和跨浏览器仍待测。 |
| P3 | A 段 final 会清除 B 段 partial。现按 stream/segment 保存，final 只清同一片段；不同课堂和删除墓碑分别投影。 | `src/mock/runtime.ts` 的 `partialSegments`、`transcript.partial/final`；`tests/unit.test.ts` 的 `a final only clears the partial of the same stream and segment`。 |

P2-04 有意保留现有 React 外部 store 的跨路由生命周期：它已承载本地 Runtime 与账号隔离，不为形式上的依赖清单强制重写。它与冻结架构所列 Zustand 运行时视图仍有差异，交 Astra 审查；React Hook Form 待实际短表单出现时接入。统一客户端当前只接 Mock 课程 GET，生产页仍不可用；它不是实际认证、CSRF 或完整真实 API 集成。模拟 Vault 不构成加密持久化。

## 最终隔离检查

Windows，Node v24.19.0、pnpm 10.34.9、Vitest 4.1.12、本机 Chrome 156；隔离目录 `work/pr09a/review2-final-check/web/frontend`。从提交建立独立检出，以原锁文件安装；修改文件和生成契约与正式工作树逐项哈希核对。依赖与临时目录放在项目 `work/pr09a/`，保留 Claude 的原有 `node_modules`。构建和 UI 运行的是隔离副本，未接真实服务、麦克风或收费 Provider。

| 命令 | 最终结果 |
|---|---|
| `corepack pnpm install --frozen-lockfile --offline --store-dir <项目 store>` | 退出 0；最终隔离副本按锁安装 |
| `corepack pnpm typecheck` | 退出 0 |
| `corepack pnpm lint` | 退出 0 |
| `corepack pnpm test:contract` | 退出 0，52/52 |
| `corepack pnpm test:unit` | 退出 0，30/30 |
| `corepack pnpm build` | 退出 0；已知 Mock 入口标记检查通过 |
| `corepack pnpm test:ui` | 退出 0，29/29，含生产预览 |

日志在工作树忽略的 `outputs/pr09a/second-review/`，最终结果为 `*-final2.log` 和 `test-results-final.txt`；截图与日志不包含在提交内。第一次 Windows 离线安装因 store 缺少 Vitest tarball 退出 1，后按同一锁文件联网补齐后退出 0；保留在 `windows-crlf.log`。第一次完整 UI 测试 28/29、退出 1：新账号的独立合成片段实际为待处理，测试误期望无待处理；记录在 `test-ui-initial-failure.log`，随后改为检查新账号待处理且不继承旧账号的帧及持久确认，最终 29/29。开发期间一次旧依赖环境 tsc 出现事件联合类型 TS2322，拆分分支后复查通过；原始终端输出未另存，不能作为最终锁文件环境结果。另一次对旧工作树依赖运行 pnpm 被保护现有 `node_modules` 的无交互确认阻止，未删除旧依赖。

## 视觉与实际边界

Windows Chrome、deviceScaleFactor 1，最终 React 与批准原型的 Day/Night 截图、选中和焦点截图及 `measurements.json` 在 `outputs/pr09a/second-review/visual-final/`。1440×900 桌面导航 x=582、y=15、276×51；390×844 手机 x=16、y=752、358×78；320×800 手机 x=12、y=720、296×66。六组导航位置、尺寸、背景、描边、圆角、阴影、blur 和颜色的计算值与原型一致，均无整页横向溢出。正文有模拟控制与合成状态，不是逐像素复刻。键盘焦点、低动效、减少透明、强制色和在支持 blur 的 Chrome 中模拟无 blur 回退由浏览器测试覆盖。CSS 是液态玻璃的视觉近似，不是物理折射；真实 iPhone Safe Area、低性能设备、原生文字缩放和跨浏览器未验证。

## 十项验收最终状态

| 验收 | 状态与边界 |
|---|---|
| AC-09a-01 | 本地 Mock 启动与失败关闭通过；不代表真实服务可用。 |
| AC-09a-02 | 前端隔离、冻结契约和本地版保护通过本轮差异检查。 |
| AC-09a-03 | 路由与响应式导航本地通过；完整业务页面不在本 PR。 |
| AC-09a-04 | 双主题和测试覆盖的降级本地通过；真实无 blur 浏览器、设备表现未测。 |
| AC-09a-05 | 测试覆盖的焦点、模态和停止入口本地通过；读屏器和完整无障碍审计未测。 |
| AC-09a-06 | 三种尺寸与 200% 用户样式文字模拟通过；原生浏览器文字放大和真实设备待测，不能宣称全面通过。 |
| AC-09a-07 | 合成事件、端口和风险投影本地通过；不代表真实 WebSocket、音频、ASR 或收费过程。 |
| AC-09a-08 | 合成撤权、租约、账号 epoch 和迟到响应隔离本地通过；服务端权限和多设备隔离待联调。 |
| AC-09a-09 | 生产构建不含已知 Mock 入口标记，本地检查通过；不是全面安全审计。 |
| AC-09a-10 | 本报告、变更清单、日志、截图与使用说明已备齐；等待独立复审。 |

## 变更清单与回退

- `src/mock/runtime.ts`：租约、任务、片段和端口投影；`src/app/MockWorkbench.tsx`：统一调用和风险/续租入口；新增 `src/api/client.ts`。
- `.gitattributes`、`package.json`：生成文件换行规则与新单测入口；`tests/unit.test.ts`、`tests/shell.browser.spec.ts` 与新增的 `tests/api.test.ts`、`tests/ports.browser.spec.ts`、`tests/revocation.browser.spec.ts`、`tests/text-scaling.browser.spec.ts`。
- `README.md`、`PR09A-ACCEPTANCE.md`、本报告；未改冻结契约、后端、PR-02a 或 Windows v1.3.0。

如需撤销，在确认目标提交后使用 `git revert <本轮修复提交完整 SHA>` 创建反向提交，保留两个原检查点和历史；先检查有无后续依赖并审阅冲突。不使用 reset、clean、强制覆盖或删除其他工作树/Claude 证据。本轮不 Push、Merge、Tag、Release、部署或开始后续 PR。
