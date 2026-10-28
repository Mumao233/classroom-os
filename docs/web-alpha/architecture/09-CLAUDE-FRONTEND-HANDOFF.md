# PR-01 · Claude Code 前端交接包

日期：2026-10-28。对象：通过 Claude Desktop 使用的 Claude Code 新会话。目标是让新会话仅凭本仓库，就能在**用户明确授权 PR-09a 且 PR-01 设计获审核后**独立建设前端壳与 Aurora；不依赖历史聊天、真实账号、真实后端或真实模型。

本文件随 PR-01 交付，**不是已经开始 PR-09a**。本轮没有创建 `web/frontend/`、安装前端依赖、启动 Mock 服务或完成 UI。下文目录、命令与测试是 PR-09a 必须建立的交付契约；在实现前不可声称这些命令已能运行。

2026-10-28审核更新：用户原则批准PR-01整体架构和已选结构/技术栈，并授权PR-01本地commit；11项TBD继续开放。批准范围及定位批准提交的方法见[收尾记录](PR01-CLOSEOUT.md)。PR-09a实施仍需用户另行明确授权。

## 1. 分工与本次接手边界

| 角色 | 主责 | 与前端的交界 |
|---|---|---|
| Astra | 全局架构、技术选型、HTTP/WS/事件契约、跨模块 review | 批准架构/契约变更，复核状态、费用、权限和数据边界 |
| Claude Code（Claude Desktop） | 前端主开发 PR-09a、09b、10a、10b | 实现页面、组件、前端控制器/状态投影、Mock、可访问性和前端测试 |
| Codex | 后端、数据库、ASR、AI、身份、基础设施与集成 | 实现真正的领域命令、状态、账户/费用/持久化；与 Claude 联调 ports |
| Terra（可选） | 视觉原型、风格/布局探索 | 由明确委派决定范围；结果由 Claude 整理入组件，不能另建一套业务主线 |

PR-09a 的交付是独立可运行的 Web 开发壳、Aurora 基础组件、主题/导航/路由、全状态组件展示、Mock 合同和基础前端验证。完整课程管理属 PR-09b；真实课堂/处理页属 PR-10a；完整 Review 属 PR-10b。可以为后续页面放明示开发占位和合成故事，不能把占位页算成对应 PR 交付。

## 2. 新会话阅读与授权检查

按下列顺序阅读，先核对实际分支和状态再写文件：

1. 根 [AGENTS.md](../../../AGENTS.md) 和 [CLAUDE.md](../../../CLAUDE.md)：正式仓库、两产品线、数据和操作边界。Git 根应为 `J:\class-assistant\classroom-os`；用 `git status --short --branch` 检查已有改动，不 reset/覆盖他人工作。
2. [Web 文档入口](../README.md)、[PR-00 验收](../PR00_ACCEPTANCE.md)、[PR-01 验收](PR01-ACCEPTANCE.md)：区分已设计、已批准、已实现和实际测试。PR-00 远端 CI 待验证的记录不能被本地 UI 测试关闭。
3. [系统设计](01-SYSTEM.md)、[前端与 Aurora](02-FRONTEND-AURORA.md)、[接口和数据流](04-CONTRACTS-FLOWS.md)、[安全离线](05-SECURITY-OFFLINE.md)：了解完整边界，不只读 UI 风格。
4. [ADR 登记](07-ADR-REGISTER.md)、[28 个 PR 依赖](08-IMPLEMENTATION.md)：识别冻结结构、待测参数和 PR-09a 的并行条件。文件若在待审核变更中尚未存在，先核实当前交付版本，不另猜一套规则。
5. [OpenAPI](contracts/openapi.json)、[事件 Schema](contracts/events.schema.json)、[合成契约例子](contracts/examples.json)，以及 [需求矩阵](../REQUIREMENTS_MATRIX.md) 中 UI/UX/DS、DATA/SYNC 部分。需要追溯视觉/文字时查 PRD 物理 p16–23；p21 只有视觉参考。

授权判断：当前用户明确说“执行 PR-09a”即对应本工作包授权，无须重复询问相同权限。若只有本交接文件而无可核实授权，或 PR-01 仍明确“待审核”，不能自行开始 PR-09a；应报告所缺的**具体审核/范围确认**，不要将架构里列出的未来任务当成执行命令。批准记录应包含日期、批准范围和具体架构 revision/commit；没有记录不能臆造“用户已同意”。

PR-09a 可在后端尚未实现时使用 Mock 并行；这不降低后续 PR-09b/10 的真实联调依赖。只对本 PR 必需的普通开发依赖在可控仓库环境建立，真实 Key、收费模型、真实麦克风、真实课堂资料、部署、推送、合并、发布仍不是 UI 开发的隐含权限。

## 3. 冻结的技术与可自主决定范围

| Astra 架构基线，Claude 不可静默改动 | Claude 在本 PR 内可自行决定 |
|---|---|
| React + TypeScript + Vite；独立 Web 入口，不迁移旧 `static/` | 小组件拆分、局部样式文件组织与函数命名 |
| React Router、TanStack Query 内存投影、Zustand 运行时视图、React Hook Form 短表单 | 不改变语义的 selector、hook、memo 和页面 lazy-load 细节 |
| CSS custom properties 三层 tokens + CSS Modules；Radix 无样式 primitives | 具体排版微调、间距、图标选择、符合既定 token 的视觉探索 |
| AccountRuntime 位于子路由之外；账号切换清理与 authEpoch | 各页面 ErrorBoundary 和 skeleton 的布局 |
| 五流独立、durable ACK、CAS、稳定操作键、unknown 不自动重发、首稿不自动采用 | 这些状态对应的自然中文文案与展示位置，但不能隐去风险 |
| 用户归属服务端决定；同源 Cookie/CSRF；平台 Key 不入浏览器 | Mock 场景选择器和开发工具布局 |
| 三缓存池/在线解锁/私人资料不进普通 SW cache | 非敏感主题/设备布局偏好的持久键名 |
| 学习缓存 50MB=50,000,000 bytes；14天/20课限制范围 | 实验参数在约定范围内的 UI 展示，不自行关闭 TBD |
| HTTP/事件 Schema 与冻结字段、API namespace `/api/web/v1` | 生成类型工具的配置和输出组织，保留可复现生成命令 |

新增主要依赖、改路由语义、改变接口字段/状态、扩大离线/数据收集范围必须带设计变更供 Astra review；不要在 Mock 里创造后端不存在的字段后要求 Codex 被动兼容。确实需要调整契约时按第 7 节提交。

## 4. 目录与运行边界

完整目录以 [02 文档第 7 节](02-FRONTEND-AURORA.md#7-未来目录与前端协作契约) 为权威。PR-09a 最小创建范围：

```text
web/frontend/
  package.json / pnpm-lock.yaml / tsconfig.json / vite.config.ts
  src/app/                   # Router、providers、auth/mock scope、全局错误边界
  src/pages/                 # 壳页面及明确开发占位
  src/features/              # view model、command facade
  src/domain/                # 纯类型/状态投影，不依赖DOM/供应商
  src/api/generated/         # 从已批准 contracts 生成
  src/api/client.ts          # 统一请求、CSRF、错误、authEpoch
  src/runtime/               # 账号壳runtime与mock ports接线
  src/platform/ports.ts
  src/platform/mock/         # MockCapturePort、MockEventClient、MemoryVault等
  src/ui/tokens/ primitives/ patterns/ content/
  src/fixtures/              # 静态合成响应+事件脚本+场景清单
  src/styles/
  public/                    # 公开静态资源，Mock SW只在dev使用
  tests/                     # contract/unit/component/browser
  .storybook/                # 双主题全状态组件目录
work/pr09a/                  # 忽略：依赖缓存、临时结果、浏览器测试数据
outputs/pr09a/               # 忽略：截图、日志、验收汇总
```

根业务 Python、`static/`、旧 `tests/`、本地启动器/依赖/版本、真实 `data/` 和配置保持独立。允许新增自己的前端文件，不为让旧服务器托管新壳而改根 `app.py`。PR-09a 的 Mock 服务只需 Vite loopback 开发服务；不以开放 `0.0.0.0` 或部署公网作为验收前提。

设计期规范仍在 `docs/web-alpha/architecture/contracts/`。生成脚本可以读取该路径；若后续正式迁入 `web/contracts/`，须由 Astra/Codex/Claude 一起更新唯一来源和版本记录。不能长期维护两份独立手写契约。

## 5. 无后端、无真实 Key 的 Mock 契约

### 必须能够独立运行

使用 MSW 拦截本地 `/api/web/v1` HTTP，并用同一 API client 访问；事件使用实现统一 EventClient port 的内存脚本适配器，不需要实际后端 WS。默认开发命令进入 mock mode，显式显示“开发模拟数据”标识。运行时真实 provider adapter 在该模式中不存在；未匹配业务请求直接失败并在开发面板显示，不能透传到网络。Vite/Storybook 自身静态资源按 allowlist 放行。

Mock 与 production 入口必须编译期分离，发布构建不能自动启用 MSW、伪账号、开发场景切换器或成功 fallback；API 不可用时显示错误，不能静默退到假数据。Mock mode 不要求真实账号登录、后端数据库、邮箱发送、供应商 Workspace 或 `.env` Key。前端 `.env.example` 只包含公开 app 配置和 mock 开关，不设置收费服务 Key 字段。

| 替身 | 行为 | 不允许行为 |
|---|---|---|
| `MockCapturePort` | 显式按按钮推进 idle/recording/paused/local_only/failed/ended，产生确定性计时和模拟音量 | 调 getUserMedia/真实录音；把模拟音量称真实音量 |
| `MockEventClient` | 按脚本发送 partial/final、重复/乱序、task.changed、删除和租约丢失 | 连接供应商；模型式实时随机生成正文 |
| `MockUploadPort` | 控制 pending/uploading/durable/failed 和 ACK 丢失 | 未确认便清理模拟唯一分片；把 frame_received 当 durable |
| `MockAIAdapter` / API fixture | prepare→用户确认→任务状态；可模拟 unknown、结构失败、部分范围和候选 | 真实模型请求；任何自动重试收费语义；生成后自动采用 |
| `MemoryVault` | 只保存合成资料，模拟 locked/unlocked/quota/write_failed | 以明文 IndexedDB 替代真实加密实现后宣称 DATA-06 完成 |
| `MockAuthPort` | A/B 两合成账号、匿名、MFA挑战、过期/撤销 | 真实密码/恢复码；前端假登录带入真实运行模式 |

固定 `clock`、UUID fixture 和事件次序，避免随机数据让截图/竞态不可复现。示例业务文本使用虚构课程与明确合成短句；不取个人真实课堂生成 fixture。契约例子不等于完整 UI fixture 库：Claude 应在既有 schema 上补齐页面所需响应，新增反例须确实违反已存在规则。

### PR-09a 场景最小清单

| 场景 ID | 状态输入 | UI 应显示 |
|---|---|---|
| MOCK-01 | 首次登录，无课程 | 创建第一门课程入口，无假统计/日程 |
| MOCK-02 | 有真实形态的合成最近记录 | 继续→快速开始→最近课程→待处理→动态顺序 |
| MOCK-03 | 模拟录音正常，站内切页/切主题 | 迷你录音条和停止入口保留，runtime不重建 |
| MOCK-04 | 采集正常但上传失败 | 五流中上传故障独立可见，不能显示整课完成 |
| MOCK-05 | 断网但模拟本地暂存成功/失败 | 分别显示本地保存和未安全保存，恢复动作不同 |
| MOCK-06 | AI unknown + 已可能收费 | 核对入口与风险说明，没有普通无条件重试 |
| MOCK-07 | 新 AI 候选 + 旧个人稿 | 候选未采用、个人稿保留，可进入对比 |
| MOCK-08 | 双设备版本冲突 | 双方版本仍在、合并/另存/采用云端操作明确 |
| MOCK-09 | 原音已删/图片离线缺失 | 不能播放原音，保留来源时间和缺图文字 |
| MOCK-10 | Quota/Budget 停新收费 | 学习阅读入口仍可用，原因可见 |
| MOCK-11 | A 的响应延迟，在 B 账号完成 | B 页面不出现 A 内容，旧缓存与订阅清理 |
| MOCK-12 | 空/加载/失败/unknown/离线/冲突/禁用/聚焦 | 全主题组件故事覆盖，不只首页截图 |

五流枚举以契约为准：capture `idle/recording/paused/local_only/failed/ended`；upload `pending/uploading/durable/failed`；ASR `idle/streaming/catching_up/draining/succeeded/failed/unknown`；AI 任务 `queued/running/succeeded/failed/unknown/cancel_requested/cancelled`；sync `synced/local_only/syncing/failed/conflict`。`idle` 的 AI 视图表示尚无任务，不伪造一条 task。`local_only` 保存文案仅在模拟/真实持久写入明确成功后使用。

## 6. PR-09a 必须建立的命令与验证

**以下脚本当前尚不存在，属于后续 PR-09a 的验收要求。** Claude 建立 package scripts 后须在 README 写实际命令、版本和退出码。初次建立锁文件用受控 `pnpm install`；之后使用 `--frozen-lockfile`，不得把首次无 lock 的失败忽略。缓存、临时目录、浏览器下载和报告定位 `work/pr09a/` / `outputs/pr09a/`，不写 C 盘默认临时位置。

| 未来命令（工作目录 `web/frontend`） | 必须具备的语义 |
|---|---|
| `pnpm install --frozen-lockfile` | 按已建立 lock 可复现安装；不执行收费服务初始化 |
| `pnpm dev:mock --host 127.0.0.1` | 无后端/Key 启动 Mock 工作台；所有业务请求受拦截 |
| `pnpm storybook --host 127.0.0.1` | 本地 Aurora 全状态目录 |
| `pnpm typecheck` | 严格 TS 类型检查，generated DTO 无手写漂移 |
| `pnpm lint` | 检查边界导入、React hooks 和样式/代码约定 |
| `pnpm test:contract` | 所有 Mock 响应/事件验证已批准 schema；关键反例确实拒绝 |
| `pnpm test:unit` | 纯状态投影、query scope、草稿 serial、epoch 晚回包等关键逻辑 |
| `pnpm test:ui` | 组件交互/焦点/键盘/Mock 页面 smoke；真实浏览器但无真实麦克风/模型 |
| `pnpm build` | 生产构建成功，包含构建标识且不包含Mock入口/秘密 |
| `pnpm preview --host 127.0.0.1` | 仅检验构建产物；真实API未接入时如实显示不可用，不降级假数据 |

测试应针对失效模式：主题/导航不卸载采集 runtime、A/B 账号晚回包隔离、unknown 不自动重发、五流状态不会被合并，以及键盘焦点/长文字。不要用“组件能 render”替代这些行为，也不要为了计数给每个静态 token 写无价值镜像测试。未改共享模块时记录旧回归沿用 PR-00；一旦抽取/改动旧共享模块须完整跑原门禁，不能擅删/弱化断言。

## 7. 分支、契约变更与联合验收

新工作开始先读取最新 `git status`、分支和架构批准 commit。优先复用合适现有 checkout/worktree；确需与 Codex 并行改同仓库时，为 Claude 的 PR-09a 建隔离工作树与独立前端分支，记录基点和实际路径，不移动/重置用户当前分支。分支名由当前任务约定，不将交接中的示例当已创建分支。不得在同一路径互相覆盖改动。

日常并行所有权：Claude 写 `web/frontend/`；Codex 写 `web/backend/`、相关集成/基础设施；Astra 审查并维护已冻结契约/ADR。跨模块共享文件指定一名编辑者，另一方提交具体变更说明；不让两个代理同时重写 OpenAPI 或生成产物。前端 UI 工作不依赖 Codex 实时在线，只依赖已校验 fixtures；真实接线通过后续集成 PR 进行。

契约变更必须记录：需求 ID → 当前字段/行为 → 建议字段/行为 → 是否破坏兼容 → owner/隐私/费用/缓存影响 → 新旧示例和验收。Astra 审核后同步 OpenAPI/事件 Schema、后端 DTO、生成 TS 类型、Mock、测试与版本；任何一方不能只改自己的 DTO。破坏性变化提高契约版本/最低客户端要求，不把生产未知枚举默认映射成成功。

联合验收边界：

- Claude 提供组件/页面构建、状态 fixture、键盘/缩放/主题验证和前端故障证据。
- Codex 提供真实 API/PG/权限/任务/持久性/费用门禁证据及相同契约集成。
- Astra 对照 PRD 和跨模块不变量复审，确认模拟和真实结果没有混用。
- 用户审核交付后决定是否授权下一个 PR；PR-09a 完成不自动开始 PR-09b/10 或真实收费联调。

## 8. PR-09a 验收清单

| 编号 | 可判定标准 |
|---|---|
| AC-09a-01 | 新前端可在没有后端、数据库、真实 Key、真实账号和真实麦克风条件下用 `dev:mock` 启动；未处理业务请求失败而非出网 |
| AC-09a-02 | 根本地业务、`static/`、旧 tests/启动器/版本/依赖不改；Web 自己的 manifest/lock/版本清晰 |
| AC-09a-03 | 五个桌面导航、四个移动入口、头像设置、课堂活动回跳与全局迷你状态条正确；占位页面明确标开发状态 |
| AC-09a-04 | Aurora Day/Nebula Night/系统、稳定阅读表面、低特效/减动效和无 blur 回退可演示，状态不只依赖颜色 |
| AC-09a-05 | 基础按钮/输入/模态/导航完整状态；焦点管理、标签/错误关联、键盘关闭/返回有效 |
| AC-09a-06 | 320 CSS px、200%文字、桌面/平板/手机布局、长公式/长名称不导致核心操作消失或整页横溢出 |
| AC-09a-07 | MOCK-01～12 可重复，所有规范响应与事件过 schema；模拟/真实标识不会混淆 |
| AC-09a-08 | Capture runtime 不随站内导航/主题切换重建；账号撤销/换号清展示和旧异步回包，测试有失效断言 |
| AC-09a-09 | 生产构建没有 Mock fallback、测试秘密或供应商 Key，使用固定公开 assets；构建/类型/必要测试退出码与限制记实 |
| AC-09a-10 | 交付 README、组件状态目录、需求 ID、实际截图/命令/结果/已知限制；声明尚未实现真实认证、加密缓存、ASR/AI/后端持久化 |

PR-09a 不以 10 项全部通过声称 Web Alpha 完成，也不声称云 ASR、离线恢复、安全或 WCAG 全面合规。交付到这里即可等待审核，后续功能严格按各 PR 范围接续。
