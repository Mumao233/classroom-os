# PR-08b 验证记录

负责人林珈诚。开发基线 `8b1e44e`，分支 `feature/web-pr08b-offline-sync`，HTTP 契约 `0.2.1` 加既有 PR-07b overlay（`pr07b-1`）。

## 环境与执行方式

功能验证使用现有 VMware Windows 来宾、隔离 PostgreSQL、私有对象服务和合成资料。宿主负责源码、静态检查、源码归档和证据回收。

宿主文件位于 `J:\class-assistant\worktrees\web-pr08b-offline-sync`。既有来宾只有 C 物理卷，其 J 盘映射到 `C:\j`。来宾测试目录采用该映射，VMware 执行器使用固定的 `C:\Windows\Temp`。本次沿用这些已有路径。

每次运行使用新的 RunId。`SOURCE.json` 列出归档源码与散列，`source.zip` 保留实际执行版本。回收器核验源码和证据归档 SHA-256。`TransportSucceeded` 表示 VMware 传输情况，`GuestExitCode` 表示来宾执行结果，JUnit 和逐步命令记录用于判定验证结论。

## 分阶段记录

| RunId | 阶段 | 实际结果 | 源码 ZIP SHA-256 |
|---|---|---|---|
| `pr08b-targeted-20261117-01` | 定向回归 | 排队期间因源码更新取消，未执行测试，来宾退出码为空 | `46c8b71192bf771edabdef0d98fff90896e1213a767e7eb40f0bd3e4264c511b` |
| `pr08b-targeted-20261117-02` | 定向回归 | VMware 传输成功，来宾退出码 1，172 passed / 26 failed / 0 errors / 0 skipped，25 个 PR-08b 测试函数全部执行 | `0b183f57a2be4faaf244e675fecad61d29d5da43b169e416df3433ac845c2d2c` |
| `pr08b-targeted-20261117-03` | 定向回归 | VMware 传输成功，来宾退出码 1，198 passed / 1 failed / 0 errors / 0 skipped，26 个 PR-08b 测试函数全部执行。正式 HTTP 浏览器通过 | `519805d54ec6aa9a6051f04bcf69cc84d59379b9ee989bb04d9d740445c4c8ed` |
| `pr08b-frontend-20261117-01` | 前端与联合浏览器 | 契约、TypeScript、ESLint、生成类型检查通过。Vitest 315 passed / 1 failed，随后运行器输出编码错误中止，后续阶段未执行 | `30b667c880779b87fe240e59f6d7b96e0bb2c4b900cf41667bec6d7366e1420a` |
| `pr08b-frontend-20261117-02` | 前端与联合浏览器 | Vitest 316/316，PostgreSQL 与正式浏览器 40/40。契约、类型、lint、生成类型、构建及原缓存浏览器通过。08b 原生浏览器 20 passed / 5 failed，失败集中冲突表单定位 | `6a7a6a2d20d60afad43f7df209e0c034c2593e23ba01c135a009e763b933b05a` |
| `pr08b-frontend-20261117-03` | 前端与联合浏览器 | 全部通过。Vitest 316/316，08b 原生浏览器 25/25，08a 28/28，05b+08a 11/11，PostgreSQL 与正式浏览器 40/40，0 skipped。契约、类型、lint、生成类型、构建全部退出 0 | `1c3cffd305e5a3420cbe6b08c9e0b86a621183a0749649279232cd43d4d720bb` |
| `pr08b-full-20261117-01` | 完整 Web 后端 | 1291 项（1262 父用例、29 子测试），1275 passed / 16 failed / 0 skipped。08b 29 个函数与正式浏览器通过 | `3163bb9a1c5b779992afe73ca8f20497a898e19343aff67c54c4345dd377bfb3` |
| `pr08b-full-20261117-02` | 完整 Web 后端 | 全部 1291 项通过（1262 父用例、29 子测试），0 failures / errors / skipped。08b 29 个函数及真实认证浏览器全部执行 | `9c57fad97241af8252a1285cea3912b2b15205893d0046f51f006ea656888cb5` |
| `pr08b-legacy-20261117-01` | 原 Windows 回归 | 525 主用例中 524 通过、1 失败，171 子测试通过，九套 Node 与隔离 guard 通过。失败为原课堂包导入预览返回 422 | `b3bfd6a859ad5430aaacb73cccd77a39e563feb4e5df16a0c22ca8aee08989d1` |
| `pr08b-legacy-20261117-02` | 原 Windows 回归 | 全部 525 主用例与 171 子测试通过，九套 Node、Python/Node guards、JUnit gate 全部退出 0，0 failures / errors / skipped | `ad9a583df42f7e795188701dc1ab5218baf16b4cc06fd5741c3a0f7622fb0b18` |

第二次运行的证据归档 SHA-256 为 `a20eaee07743f52d51f690a0db5dabe79bda7268e9c37b443c4b8162ab1c81db`。来宾 PostgreSQL 正常停止，退出码 0，受管对象服务残留进程数为 0。新增同步和正式 HTTP 浏览器路径仍有失败，后续运行负责验证修复。

第三次运行的证据归档 SHA-256 为 `996927d6c32245074774b24d2c37a88b74068644355bd47de381c188775fccdd`。剩余失败暴露了实际生成候选依赖 PR-07b overlay 的来源别名规则。后续改为完整复用既有 overlay，保留原候选结构与内容。正式浏览器已验证真实提交后丢失 ACK、原键收据恢复、两设备 CAS、ConflictPanel 的明确采用云端、后台删除与设备撤销。

首次前端阶段证据归档 SHA-256 为 `999b1aa185b34796f25808861f93e0a87a37b74d626383e43e94a3b2b4410222`。失败用例将异 operation 收据放入了会按 operation 过滤的 fixture，因此收到 NOT_FOUND 并正常重试。用例已改为直接注入错误的成功响应，明确断言保留密文、INVALID_RECEIPT 和零重发。运行器输出已设为 UTF-8，后续使用新 RunId 完整重跑。

第二次前端阶段证据归档 SHA-256 为 `65b8989f7221c2d39c39e3a5e0d97993c927772636257e3de3790197d4869b84`。40 项后端测试包括 29 个 PR-08b 测试函数及原 PR-03b 真实认证浏览器。原生浏览器仍需修复并复验全部 25 项。

来宾诊断的 accessibility tree 确认 textbox「合并正文」「个人补充」和 combobox「目标课堂」名称正确，role/name/exact 各唯一命中。`getByLabel` 的完整 DOM 文本匹配包含了嵌套 textarea 初值或 select 选项。原生 harness 改用严格 role/name/exact 定位，继续验证实际输入和显式提交。诊断保存在 `outputs/pr08b/pr08b-native-debug-20261117-01/diagnostics.json`。前两次诊断编排在脚本位置和来宾盘符预检处失败，第三次成功运行，原始失败证据保留。

第三次前端阶段证据归档 SHA-256 为 `89d15505fbb2d8eec8c01b075939112d4c9ff902dce2790eea40ddfedff953ab`。真实 HTTP/PostgreSQL 浏览器完成全部断言，受管服务清理残留为 0。原生 25 项包含三种冲突交互、再次 CAS、延迟 ACK、跨标签确认、授权快照遗漏、事件水位跨越删除、来源撤权与受限密文保留。

首次完整 Web 阶段证据归档 SHA-256 为 `c515887d9aac376266a89c545a4fe0c8164a5fd5b47bedf097fc61a0883a3e77`，后端命令耗时约 1074 秒，受管服务清理残留为 0。其中 3 个失败来自原日志测试要求的受控路径，另 13 个旧断言假定目录操作没有 outbox 事件。本包目录变化已参与 owner 水位，后续调整测试基线以精确区分新增目录事件与原任务账本，并继续保留原回滚、隔离与零副作用断言。

第二次完整 Web 阶段证据归档 SHA-256 为 `6658316d13ec7a77e57e04bb00a44c43b1e7ab150ee36ef11adb5defdd8d755e`，全部通过，服务残留为 0。证据同时收回了原 03a 日志验证的 42 份观测 JSON。测试精确验证旧任务事件完整保留、每次仅新增对应事件、课堂移动只递增一次 owner 水位，以及拒绝入队保持全部准备后账本。

首次原 Windows 阶段证据归档 SHA-256 为 `50d1885ef92368e03f484c8409a122e0578fe7b5435b3d21b2bd26f5f9d7f518`。旧产品源码与测试保持原版本，课堂包导入失败需诊断后完整复跑。

来宾诊断捕获 `portable_package.py` 的图片暂存 `destination.open('xb')` 发生 FileNotFoundError、errno 2，目标路径长 265 字符。诊断调用原错误处理，保留 422 与原测试失败。记录位于 `outputs/pr08b/pr08b-legacy-diagnostic-20261117-01/exception.json`。运行器将本次 legacy pytest 临时目录缩短到快照的 `work/p`，对应图片路径为 247 字符，随后完整复跑旧回归。

第二次原 Windows 阶段证据归档 SHA-256 为 `da37a8702e98b898fd8186f0e1991abe88a92146301d84d7b5ffcb140381ebba`，完整回归通过。此次仅缩短测试临时路径，原 Python、九套 Node 和 Windows DPAPI 用例均执行。来源归档逐文件核对确认正式功能源码与通过的 frontend03/full02 快照一致，legacy02 之后只更新了验证文档。

本次通过阶段的宿主编排命令如下，均由既有 VMware broker 执行来宾测试并按 RunId 回收。

```powershell
./tools/pr08b/submit.ps1 -Mode frontend -RunId pr08b-frontend-20261117-03
./tools/pr08b/collect.ps1 -RunId pr08b-frontend-20261117-03
./tools/pr08b/submit.ps1 -Mode full -RunId pr08b-full-20261117-02
./tools/pr08b/collect.ps1 -RunId pr08b-full-20261117-02
./tools/pr08b/submit.ps1 -Mode legacy -RunId pr08b-legacy-20261117-02
./tools/pr08b/collect.ps1 -RunId pr08b-legacy-20261117-02
```

来宾实际 Python/Node/pytest 命令、工作目录、逐步退出码和耗时保存在各证据目录的 `run.json`。前端原生浏览器使用真实 Chromium/Web Crypto/IndexedDB 与合成 transport，正式浏览器使用真实 TLS、会话、HTTP、PostgreSQL 和独立设备上下文。

完整本机证据保存在 `outputs/pr08b/<RunId>/`。已取消运行保留 `cancellation.json` 与原排队文件。失败运行保留原 JUnit、日志和归档。

## 静态与契约

宿主默认 Python 3.10 执行 `python -B tools/overnight/check_contracts.py --output outputs/pr08b/static/contracts.json` 失败，缺少 jsonschema。来宾前端和全量阶段继续执行该检查并记录实际结果。

2026-11-17T14:28:59Z 宿主静态检查完成。Git 文件清单内 393 个 Python 文件 AST 全部通过，`tools/pr08b` 三个 PowerShell 脚本解析通过，三个 `.mjs` 文件 `node --check` 通过，`git diff --check` 通过。静态结果保存在 `outputs/pr08b/static/`。

旧产品六个 JavaScript 文件 `app.js`、`pcm-worklet.js`、`study-cache.js`、`review-render.js`、`review.js`、`portable-reader.js` 的语法检查全部退出 0。`setup.ps1`、`start.ps1`、`stop.ps1` 仅解析并通过，记录见 `legacy-static.json`。修正旧 Web 回归基线后，再次完成 393 文件 AST 与差异检查。

## 尚待完成

- 全部测试通过后提交独立 Astra 复审，登记 P0–P3 风险及修复验证。
- 以最终源码和证据提交 Draft PR。
