# PR-09a · Aurora 正式整合与验收记录

> **2026-10-30 第三轮整改：**针对连续租约事件身份、模拟 Vault 接线、停止后新采集作用域补充修复及浏览器回归；390px 双主题选中态重新等待动画结束后截图。最终检查及剩余限制见 [整改报告的第三轮记录](PR09A-REMEDIATION-REPORT.md#第三轮针对性整改2026-10-30)。结果仅限本地合成模拟，等待 Astra 第三轮独立复审；上一轮两项部分关闭的问题不因本次整改自动变为关闭。真实设备、原生文字缩放和服务端联调等既有边界仍未验证。

> **2026-10-30 第二轮复审状态：**此前表格与测试结果为第一轮历史记录，其中 AC-09a-06 的“200% 根文字”不能证明正文等实际放大，撤回其作为文字放大验收通过的依据。第二轮修复和最终状态以 [第二轮整改报告](PR09A-REMEDIATION-REPORT.md) 为准。五项 P2 和 P3 已针对本地合成场景修复，等待 Astra 独立复审；真实浏览器原生文字缩放、真机、读屏器与服务端集成仍待验证。

更新日期：2026-10-29（中国时间）。本轮在独立 worktree `J:\class-assistant\worktrees\web-pr09a-aurora` 的 `feature/web-pr09a-aurora` 上继续，保留本地检查点 `0e1c818c4e1a583de8c3a378b3bc00d58c4b1bde`；PR-01 正式基线 `1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f`，HTTP/WS 契约包 `0.1.0`。正式 PRD v1.2 归档 SHA-256：`dd7d970d54fb443969ce6bb210b0b18053bfecf972cf419efa57edb51b2ff5b8`。

本轮已在锁定依赖的隔离环境完成前端模拟与浏览器验收。以下“通过”均限定在列出的本地合成场景，不代表 Web Alpha 已完成或可供真实用户使用。用户已明确批准以最新视觉原型整合，桌面顶部胶囊替代此前侧栏是用户本轮的明确决定；历史架构文档和冻结接口契约未因此改写。

## 整合范围与保护边界

桌面使用左上品牌、相对视口居中的五项胶囊导航和右上独立搜索/通知/头像；移动端使用四入口悬浮底栏，设置经头像进入。Aurora Day 的亮银玻璃与 Nebula Night 的深蓝灰玻璃沿用已批准原型，支持系统主题、低特效、减动效、减少透明、强制颜色与无 blur 回退。完整课程管理、真实课堂和完整 Review 仍为后续 PR，不把占位当作已实现。

模拟 HTTP 使用 MSW；A/B 有不同合成课程响应，身份只在 mock 内存中，不添加契约外 HTTP 字段。模拟事件经独立 EventClient 投影，包含临时/正式片段、重复/乱序、帧确认/持久回执、未知任务、原音/课堂撤权、租约丢失与会话撤销。账号作用域记录 epoch；换号/撤销先使旧 epoch 失效、清内容和事件订阅，并清理/替换相应 QueryClient。采集模拟绑定 classroom、capture epoch、fence 和 chunk；不把路由当成采集归属。事件及响应均仅是合成演示，不构成真实服务端授权、WebSocket、音频持久性或费用门禁验证。

Claude 原有未跟踪 `package.json`、`pnpm-lock.yaml`、`.npmrc` 原件留在忽略的 `work/pr09a/claude-original/`；原有 `node_modules` 未清理。未授权本轮修改冻结契约、Windows 本地版、后端工作树或真实配置/数据，也未授权真实麦克风、ASR/AI、收费服务、Push、Merge、部署或发布。最终保护范围以 Git 对比核验为准。

主要需求映射为 UI-DASH-02/08、UX-01/02/06、DS-01～09 及账号作用域、五流/事件不变量；本轮有限浏览器测试不宣称 DS-10～13 的全面无障碍、真机或性能验收。

## 正式 React 与批准原型对照

截图及测量数据位于工作树忽略的 `outputs/pr09a/aurora-integration/`，不随 Git 提交；对应 `react-*` 和 `prototype-*`，详细说明为该目录 `comparison.md`，数值为 `measurements.json`。截图由 Windows Chrome 156、deviceScaleFactor 1、合成 Mock 采集；原型来自 `work/pr09a/visual-refinement/desktop.html` 及经用户批准的最新 `mobile.html`。这是浏览器视觉和计算样式检查，不是整页像素完全一致或真实设备验证。

| 视口 | Day 正式 / 原型 | Night 正式 / 原型 | 导航测量 |
|---|---|---|---|
| 1440×900 | `react-desktop-day.png` / `prototype-desktop-day.png` | `react-desktop-night.png` / `prototype-desktop-night.png` | x=582、y=15、276×51，中心 x=720 |
| 390×844 | `react-mobile-day.png` / `prototype-mobile-day.png` | `react-mobile-night.png` / `prototype-mobile-night.png` | x=16、y=752、358×78 |
| 320×800 | `react-mobile-small-day.png` / `prototype-mobile-small-day.png` | `react-mobile-small-night.png` / `prototype-mobile-small-night.png` | x=12、y=720、296×66 |

六组对照中导航矩形、圆角、背景层、边框、阴影、模糊和主题色的计算值一致；桌面双主题三组课程封面渐变也与原型一致，另有正式页面 selected/focus 截图。可见差异包括：原型桌面课堂图标的粉色点未作为真实状态凭空加入，部分卡片间距和信息行位置不同；正式页面另有 Mock 标识、场景控制和实际合成状态投影。截图和测量用于审阅，不能替代人工视觉确认。

## 十项验收与待确认状态

| 项目 | 本轮范围和证据 | 最终状态 |
|---|---|---|
| AC-09a-01 | mock 启动不依赖真实服务或设备，未匹配请求失败；浏览器用例验证。 | 本地通过 |
| AC-09a-02 | Web 自有清单/锁文件、生产入口；对比检查点仅修改 Web 前端，冻结契约和旧业务无差异。 | 本地通过 |
| AC-09a-03 | 桌面五入口、手机四入口、头像设置、活动课堂回跳和迷你条；未实现页面明确为占位。 | 本地通过 |
| AC-09a-04 | 双主题/系统跟随、语义状态和阅读表面；低特效、减动效、减少透明、强制颜色及模拟无 blur 回退有浏览器测试。 | 本地通过；实际无 blur 浏览器及真机仍未测 |
| AC-09a-05 | 按钮、输入、模态、导航及组件目录，覆盖标签/错误关联、Tab、Escape、焦点返回、禁用、模拟停止入口与双主题状态；桌面视觉隐藏标题保留辅助技术语义。 | 本地通过；不等于完整 WCAG 或读屏器审计 |
| AC-09a-06 | 320px / 390px / 1440px、200% 根文字、长名称/公式、核心停止操作和溢出；六组原型对照。 | 本地通过；真实手机与跨浏览器未测 |
| AC-09a-07 | MOCK-01～12 可选择；事件脚本连到内存事件端口及可见投影，逐步区分临时/正式和帧/持久确认；52 条契约测试包含逐事件和响应 schema 及反例。05 的两个模拟恢复动作、10 的合成只读资料可演示。 | 模拟验收通过；并非真实数据、完整 Review 或后端事件 |
| AC-09a-08 | 导航/主题不销毁模拟采集；换号/撤销隔离 epoch、查询缓存、旧事件订阅与合成响应，覆盖不可取消晚响应单测、不同 A/B fixture、错课堂/epoch/fence 和撤权后迟到回执。 | 模拟验收通过；不等于服务器授权或真实多设备 |
| AC-09a-09 | 生产模式显示不可用，Mock 按构建模式分离；构建与已知 Mock 标记检查通过。 | 本地通过；不是全面安全审计 |
| AC-09a-10 | README、组件目录、需求映射、截图、测量和本报告；记录真实认证、加密缓存、ASR、AI、后端持久化均未实现。 | 已交付，等待用户最终审阅 |

已针对上述四项补充本地验证；真实设备、安全及服务联调限制仍未关闭。当前模拟 runtime 使用 React 外部 store，Zustand 运行时视图、React Hook Form 短表单及统一 API client 的完整架构接线仍未落实；采集/上传和 Vault 替身不等于持久恢复实现。这些不应被本次模拟验收结果掩盖。

## 本轮最终命令与结果

| 检查 | 本轮最终结果 |
|---|---|
| `pnpm install --frozen-lockfile --offline --ignore-scripts` | 退出 0，按已有离线 store 安装 380 个包；安装脚本未执行，后续实际构建及浏览器测试通过 |
| `pnpm typecheck` | 退出 0，生成 DTO 漂移检查和严格 TS 通过 |
| `pnpm lint` | 退出 0 |
| `pnpm test:contract` | 退出 0，52/52 |
| `pnpm test:unit` | 退出 0，18/18 |
| `pnpm build` | 退出 0，生产产物通过已知 Mock 标记检查 |
| `pnpm test:ui` | 退出 0，Chrome 23/23；包含生产预览 |
| 保护路径、Git diff/check 与提交 | 与检查点相比仅 Web 前端变更，冻结契约与旧业务无差异；暂存前 `git diff --check` 通过，提交见本分支 Git 记录 |

环境：Windows、Node 24.19.0、pnpm 10.34.9、Vitest 4.1.12、Chrome 156.0.8078.48。工作目录为 `work/pr09a/refinement-check/web/frontend`，从当前工作树复制源文件、测试及冻结契约，并以文件哈希确认 24 个源码/测试/脚本/公开资源与清单、锁和配置一致；TEMP/TMP 与 store 在非 C 盘任务目录。完整日志在 `outputs/pr09a/aurora-integration/logs/`。最初在仓库根通过 `corepack pnpm --dir` 调用时选中 pnpm 12 并因版本约束失败；随后在隔离前端目录使用 10.34.9 重新安装成功。测试前修复了窄屏 200% 溢出、撤权后的迟到回执、缺少屏幕阅读标题和排序断言可误过的问题；并未删除或弱化旧测试。终端提示 `NO_COLOR`/`FORCE_COLOR` 冲突不影响退出码。

上轮检查点在当时的隔离环境记录过契约 37/37、单元 8/8、浏览器 13/13、构建和依赖审计通过；这些是历史结果，不是本轮最终数据。旧 Python、九套 Node 和远端 CI 本轮未因仅 Web 前端修改而自动重跑；如实际改动共享模块应按仓库规则重新执行，不得借用历史成绩。

## 限制与恢复

Liquid Glass 是 CSS 多层反射、边缘与 backdrop-filter 的视觉近似，不是原生物理折射。真实 iPhone safe-area、低性能设备、读屏器、不同底色下透明混合对比度及跨浏览器表现尚待独立验证；无 blur 测试是在支持 blur 的 Chrome 中模拟启用项目的回退规则。模拟端口、内存 Vault 和模拟账号没有提供真实麦克风、加密持久化、身份安全、后端隔离、云 ASR/AI、收费或退款保证。

回退仅针对本轮明确列出的前端提交或文件；保留检查点、Claude 原件和其他 worktree，禁止宽泛 reset/clean。未经用户授权不开始 PR-09b/10、不 Push/Merge/部署/发布。
