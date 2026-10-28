# PR-09a · 交付与验收记录

日期：2026-10-28（中国时间）。本次提交是**供审核的实现检查点，尚不能判定十项验收全部通过**。工作树 `J:\class-assistant\worktrees\web-pr09a-aurora`，分支 `feature/web-pr09a-aurora`；起点 PR-01 `1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f`，HTTP/事件契约包 `0.1.0`。正式 PRD v1.2 归档的 SHA-256 为 `dd7d970d54fb443969ce6bb210b0b18053bfecf972cf419efa57edb51b2ff5b8`，与仓库记录一致。

## 范围与证据

实现 Aurora Day/Nebula Night/跟随系统、低特效、组件目录、响应式壳、模拟五流和合成场景。桌面采用可折叠侧导航，手机采用四入口底栏，遵循 PRD；附图仅作视觉参考。主要需求映射：UI-DASH-02/08、UX-01/02/06、DS-01～09；本记录不宣称 DS-10～13 的全面无障碍和真机验收完成。

Claude 原有未跟踪 `package.json`、`pnpm-lock.yaml`、`.npmrc` 的原件已备份于忽略的 `work/pr09a/claude-original/`；已安装的 `node_modules` 原样保留。没有改冻结契约、本地业务、旧测试或启动器；未操作主仓库和后端 worktree。无真实账号、麦克风、课堂、密钥、收费请求、部署、推送、合并或发布。PR-09b、PR-10a、PR-10b 页面仍为明确占位。

实际截图位于本工作树的 `outputs/pr09a/day-desktop.png`、`night-desktop.png`、`day-tablet.png`、`night-mobile.png`，分别为 1440、1440、900、320 CSS px；截图和完整审计文件在忽略的 outputs 中，不随 Git 提交。组件目录在 mock 模式 `/__dev/catalog`，`storybook` 命令是等价目录入口，不是 Storybook。

## 十项验收

| 项目 | 结论 | 实际证据与余项 |
|---|---|---|
| AC-09a-01 | 通过本地模拟检查 | MSW 初始化后才显示预览；Playwright 对已知课程响应及未知业务请求拒绝有断言。未启用真实设备或服务。 |
| AC-09a-02 | 通过本地范围检查 | 新前端独立清单、锁和版本；跟踪文件与指定基线比较，旧业务及契约无差异。 |
| AC-09a-03 | 通过已测路径 | 五个桌面入口、移动四入口、头像设置、活动课堂回跳与五流条可见；功能页明示后续开发。 |
| AC-09a-04 | 部分通过 | 双主题、系统跟随、低特效及减动效回退已实现；浏览器测试低特效/减动效，静态检查无 blur 回退。forced-colors、真实设备性能和透明混合对比未全面验证。 |
| AC-09a-05 | 部分通过 | 目录含按钮、输入、状态及 Radix 模态；真实浏览器测了标签/错误关联、Tab、Esc、焦点返回以及模态内停止模拟课堂。未对全部控件和状态组合完成全面无障碍审计。 |
| AC-09a-06 | 通过已测视口 | Chrome 测 320px、200% 根文字、可操作停止按钮、长名称及局部可聚焦滚动的长公式，整页无横溢出；截图包含桌面、平板、手机。不是跨浏览器及真机保证。 |
| AC-09a-07 | 部分通过 | 12 个场景有可重复预览和浏览器操作；34 个正式契约正反例与 3 个 HTTP fixture 通过 schema。事件端口和内存替身仅有单元测试，尚未把完整脚本事件接到 UI 并逐事件校验；05 的恢复路径、08 的处理与 10 的阅读页面均只是预览或占位。未证明全主题×全状态组合。 |
| AC-09a-08 | 部分通过 | 导航/切主题保留模拟状态，换号清状态、取消/清除 Query 缓存；单测拒绝旧 epoch，浏览器测试延迟请求后切到 B。两账号 HTTP fixture 均为空，未证明不同响应内容的端到端隔离；模拟撤销和所有订阅 teardown 尚未覆盖。 |
| AC-09a-09 | 通过已测构建检查 | 生产预览显示服务不可用，测试未注册 Mock worker；构建标识和已知 Mock 标记检查通过。完整依赖审计见下，检查不等同完整安全审计。 |
| AC-09a-10 | 通过交付记录 | README、组件目录、需求映射、截图、实际命令和限制见本文件。真实认证、加密缓存、ASR、AI 和后端持久化均未实现。 |

此外，当前模拟 runtime 使用 React 外部 store 和 TanStack Query；冻结选型中的 Zustand、React Hook Form、统一 API client 及完整账号 runtime/事件 port 接线尚未落实。`MockCapturePort`、`MockEventClient`、`MockUploadPort` 和 `MemoryVault` 是隔离内存测试替身，未驱动完整页面流程。需在 PR-09a 后续修订中完成并复验，不能把上述部分通过当成该 PR 已关闭，也不能借此开始后续 PR。

## 实际命令与结果

Windows、Node 24.19.0、pnpm 10.34.9、Chrome 156.0.8078.48；不使用真实麦克风。工作目录为隔离复现副本 `work/pr09a/repro/web/frontend`，其源码、测试和冻结契约由本工作树复制，清单/锁与最终内容一致；所有可配置临时目录及 pnpm 缓存放在 `work/pr09a/`。以下均退出 0：

| 命令 | 结果 |
|---|---|
| `pnpm install --frozen-lockfile` | 升级后的锁文件安装成功；pnpm 默认未运行 esbuild/MSW 安装脚本，实际构建和浏览器测试成功 |
| `pnpm typecheck` / `pnpm lint` | 通过，包括生成 DTO 的漂移检查、React Hooks 和 UI 边界规则 |
| `pnpm test:contract` | 37/37，包括规范正反例和三条 HTTP fixture |
| `pnpm test:unit` | 8/8，覆盖五流、unknown、epoch、采集时间、durable ACK 和内存 Vault |
| `pnpm build` | 成功，包含标识并通过已知 Mock 标记检查 |
| `pnpm test:ui` | 13/13；Chrome，loopback 19309/19310，包含生产预览、拒绝未知请求、主题、键盘、响应式及场景交互 |
| `pnpm audit` | 依赖升级后 0 条已知公告，结果保存在 `outputs/pr09a/dependency-audit-updated.json`；是当次 registry 数据的结果 |

初次浏览器测试遇到 5173 被占用，未终止既有进程，改为独立端口后重跑。初版 MSW 未匹配请求一度透传到 Vite 回退页，补充显式拒绝并用浏览器重测。初版依赖审计发现 Vitest 3/Tinypool 开发链 2 条 critical、3 条 moderate；隔离升级 Vitest 4.1.12 后重新冻结安装并复测。原 `node_modules` 未重装，仍含旧版本；不得以其作为新锁测试证据。许可证清单与原始审计分别存于 `outputs/pr09a/dependency-licenses.json` 和 `dependency-audit-all.json`。

旧版 Python、九套 Node 和远端 CI 本次未重新运行；本次未修改其源码或依赖，不把历史结果当成本次测试。截图未替代真实设备、读屏器、浏览器兼容和安全审查。回退限于本次前端 commit 和明确列出的任务文件，保留原成果；不使用广泛 reset/clean。
