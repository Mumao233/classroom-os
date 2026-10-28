# Classroom OS Web Alpha · Aurora 预览

这是 PR-09a 的独立前端工作区，基于 PR-01 `1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f` 与契约包 `0.1.0`。它不会启动本地 v1.3.0、连接真实后端或使用麦克风。模拟数据、状态、账号均为合成内容；课程管理、真实课堂和完整复习页面尚未实现。

用户已批准的视觉原型已整合到 React 壳：桌面为视口居中的五入口胶囊，手机为四入口悬浮胶囊并经头像进入设置；Day 为亮银玻璃，Night 为深蓝灰玻璃。正式页面与原型的六组同视口截图及计算样式对照保存在工作树忽略的 `outputs/pr09a/aurora-integration/`，说明见其中的 `comparison.md`。玻璃折射是 CSS 视觉近似。

## 运行

需要 Node.js 24 和项目声明的 pnpm 10.34.9。以下命令在 `web/frontend` 目录运行；Windows 上可用 `corepack pnpm` 代替 `pnpm`。依赖下载需网络，不包含任何收费服务初始化。仓库要求将可配置的临时目录、缓存和测试结果放到 `work/pr09a/`；现有 `.npmrc` 指定了 pnpm 目录。

```powershell
$root = (Resolve-Path ../..).Path
New-Item -ItemType Directory -Force "$root/work/pr09a/tmp" | Out-Null
$env:TEMP = "$root/work/pr09a/tmp"
$env:TMP = $env:TEMP
$env:COREPACK_HOME = "$root/work/pr09a/corepack"
corepack pnpm install --frozen-lockfile
corepack pnpm dev:mock --host 127.0.0.1
```

访问 Vite 输出的 loopback 地址。页面下方的模拟场景选择器提供 MOCK-01～12 的合成示例；事件脚本可一次运行或逐步演示转写、上传、未知任务、撤权、租约失效和会话撤销。A/B 是不同合成课程响应；撤销后可选择重新进入模拟账号。所有状态均为页面内存模拟，不代表真实认证或持久化。“组件目录”可直接进入 `/__dev/catalog`。`pnpm storybook --host 127.0.0.1` 是本地组件目录的兼容命令，**不是 Storybook 产品**。页面上的“模拟录音”不打开音频设备。开发 Mock 初始化失败或未知请求不会降级到真实业务服务。

本次接手时 Claude 已安装的 `node_modules` 原样保留，其中 Vitest 仍为 3.2.7；为修复开发依赖公告，当前清单和锁文件已升级到 4.1.12，因此这个保留目录与新锁不一致。上面的安装命令适用于干净检出。本轮使用 `work/pr09a/refinement-check/web/frontend` 的隔离副本按新锁安装并完成测试；不要把原依赖目录当作升级后的验证环境，也不要未经审阅清理它。

```powershell
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm test:contract
corepack pnpm test:unit
corepack pnpm build
corepack pnpm test:ui
corepack pnpm preview --host 127.0.0.1
```

浏览器测试使用本机安装的 Chrome，并在独立 loopback 端口启动模拟开发服务器与生产预览；先执行 `build`。构建输出在 `work/pr09a/dist`，生产预览只显示真实服务尚不可用，不载入模拟工作台或 Service Worker。前端构建标识为 `web-alpha-0.1.0-pr09a`。自动生成的 DTO 以冻结的 OpenAPI 和事件 schema 为来源；修改契约需另行走架构评审，不直接改生成文件。

## 边界和恢复

没有真实认证、服务端权限、数据库持久化、加密缓存、录音、ASR、AI、支付或部署。内存事件客户端、采集/上传替身和 Vault 只用于合成演示与测试，不代表真实 WebSocket、离线恢复或设备行为。`storybook` 目录中的组件、模态和状态是设计演示，不代表后续业务页面已经交付。正式测试结果、局限和验收结论见 [验收报告](PR09A-ACCEPTANCE.md)。

此工作位于独立 worktree；回退应只针对本 PR commit 或经审阅的前端文件，不执行 `git clean`、重置其他工作树或删除 Claude 留下的未提交文件。Claude 原始 manifest、lock 和 `.npmrc` 已备份在该工作树忽略的 `work/pr09a/claude-original/`。
