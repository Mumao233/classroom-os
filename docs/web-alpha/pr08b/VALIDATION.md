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
| `pr08b-frontend-20261117-04` | Astra 修复复验 | Vitest 323/323、同步原生浏览器 29/29、缓存 28/28 与组合 11/11 通过。PostgreSQL 45 passed / 1 failed，新增锁序与冻结 CAS 用例通过，正式浏览器在首次离线入队前尚未读取准确 base 版本 | `cc49a90224ef81505bf8b23cd8bfc2bc3f04d4e14befd995c87fe4a9191faf32` |
| `pr08b-frontend-20261117-05` | Astra 修复复验 | 单元、原生浏览器和其他后端测试通过。正式浏览器在恢复引用比较断言失败，已完成部分来源撤权的 200/200/404/404 授权及清面板检查 | `26ab1cfaee19877b49bd21760ac3388446b8f2c106a3b1d8564240bb4a04a44b` |
| `pr08b-frontend-20261117-06` | Astra 修复复验 | 全部通过。Vitest 323/323，同步原生浏览器 29/29，缓存 28/28，组合 11/11，PostgreSQL 与正式浏览器 46/46。33 个 PR-08b 测试函数及真实认证浏览器全部执行，0 failures / errors / skipped | `04bed6a56cac8f7edde88aacbcadcea556764b4899cc0d7d94a764fd8413a8aa` |
| `pr08b-full-20261117-03` | Astra 修复完整 Web 复验 | 1297 项（1268 父用例、29 子测试），1296 passed / 1 failed / 0 errors / 0 skipped。33 个 PR-08b 函数与正式浏览器通过。既有并发删除用例有一个请求在认证会话行锁处超时 | `83c68ae9cec949b140de9b76cfb4444414d30098b6aa9926ef97b9b1f6d78dd2` |
| `pr08b-frontend-20261117-07` | R5 编辑恢复复验 | 排队期间发现默认缓存删除的迟到恢复密文边界，确认尚未执行后取消。原队列、源码包与取消记录保留 | `b13a4686aac50f676bbff0f981298edfb3cdad249f5310ef651db1b65101ad71` |
| `pr08b-full-20261117-04` | 完整 Web 复验 | 1297 项，1280 passed / 0 failures / 17 errors / 0 skipped。17 个错误均在 capture 模块共享对象服务的启动前置阶段，原并发删除与 33 个 PR-08b 函数通过 | `83c68ae9cec949b140de9b76cfb4444414d30098b6aa9926ef97b9b1f6d78dd2` |
| `pr08b-frontend-20261117-08` | R5 编辑恢复复验 | Vitest 332/332、PostgreSQL 与正式浏览器 46/46、缓存及组合浏览器均通过。同步原生浏览器 43 passed / 1 failed，合成 resolve 回执误绑定另一文档，运行时正确保留未确认操作 | `0d0ac1fcc358d3747c3ea9c3f59d13617f9ef4b71213ad7ebc7791dffba97183` |
| `pr08b-frontend-20261117-09` | 最终前端与正式接口复验 | 全部通过。Vitest 332/332、同步原生浏览器 44/44、缓存 28/28、组合 11/11、PostgreSQL 与正式浏览器 46/46，契约、类型、lint、生成类型及构建全退出 0 | `7b145b73010ad8bb5bb002db865e69555df92d12409252388954083724a93c4d` |
| `pr08b-capture-20261117-01` | 对象端口与捕获模块补验 | 真实端口冲突候选被拒绝，两个服务均就绪，原 capture 模块 17/17 通过，含真实 Chromium PCM/WSS，0 failures / errors / skipped | `7b145b73010ad8bb5bb002db865e69555df92d12409252388954083724a93c4d` |

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

## Astra 修复复验

独立初审在 `325d687` 上发现 1 项 P1 与 3 项 P2，详见 [初审报告](ASTRA-REVIEW.md) 和 [闭环记录](REVIEW-CLOSEOUT.md)。修复后的 frontend04 证据 SHA-256 为 `422ebfff8baadf08f9d75a7ec5894d265a89fcab57684d5205e9c219d6a7a684`。首次离线编辑场景已补充正常编辑器在线打开时的准确 `readReviewRevision`，然后再断网保存，继续保留原幂等与密文断言。该轮正式浏览器尚未通过，后续用新 RunId 完整复验。

frontend05 证据 SHA-256 为 `556754167f445531faba6f523ce5c77eb1874aacb5310c21a3040881b868820e`。新增断言错误地要求写入引用与恢复后的完整认证 header 对象完全相同。恢复记录包含合法的 owner/device/key/schema 元数据。修正后逐项精确核对原引用的全部字段，并另行核对真实会话 owner/device、schema、keyId 与 keyVersion。测试服务采用 3 秒 graceful shutdown 和 15 秒 join，并在 finally 中始终隔离合成 Review 任务。

frontend06 证据 SHA-256 为 `156a63f3227ef19fa7487801fdc77b6d50ed092014391adb5c19db4a7c32d023`，全部通过，服务残留为 0。正式浏览器完成部分来源撤权后的面板清理、准确 HTTP 授权、重新打开加密存储后的受限状态与拒读断言。两处真实双连接锁序回归和旧冲突换 base 的拒绝回归同时通过。

full03 证据 SHA-256 为 `7e19f14d4a294e63c115b432da5012e54ded5f231857898c438b6b4696381769`，服务残留为 0。唯一失败来自 `test_concurrent_and_repeated_deletes_of_one_object_are_one_request`，六个并发请求中一个返回 503。PostgreSQL 日志记录 `identity_api.validate_session` 对 `auth_sessions` 执行 FOR UPDATE 时发生 lock timeout，需诊断与复验。

`pr08b-lock-01` 复用 full03 冻结源码，对原并发删除用例执行五轮，30 个请求全部通过。原 500 ms 阈值、产品代码和测试断言保持原值。20 ms 采样记录认证会话行锁及 transactionid 等待，各轮账本追加耗时 31–32 ms，最长 submit 为 93–156 ms，服务残留为 0。证据 SHA-256 为 `45fcdb9d10704fb43d2e14b7ea4a8d0d90446b8e3aa3d6b55d2d42ee1bce93e8`。该诊断未复现 full03 的 503，既有认证与删除事务持锁边界仍作为并发可用性风险保留。0014 仅在首次删除提交时增加一个事件，复用事务已持有的 owner counter 锁，实际新增耗时尚未独立量化。

Astra 对 `c711458` 的增量复审新增 R5（P2），普通 Review 事件引起锁定时，尚未确认的手动合并输入缺少独立加密恢复。后续补齐独立恢复记录、准确 ACK、重新发现、锁后失败状态和删除屏障，最终验证见 frontend09。

full04 证据 SHA-256 为 `6729fa56025dde9aaed0ba4a4c9ad6cd53ac80ca73b12ebc16381a083c4e3870`，服务残留为 0。对象服务日志明确记录同一夹具的 media gRPC 已占用 32727，提前分配端口的 ledger 又使用 32727 作为 S3 端口，启动时 bind 失败。夹具改为 media 完成启动后再构造并启动 ledger，保留原就绪检查、断言、超时和启动失败清理。后续补验覆盖全部 17 个受影响用例。

frontend08 证据 SHA-256 为 `22c3d4a28885eefacbca941b2f04f13bac117d4bc506df19b9fbcf169943f4e9`，服务残留为 0。唯一原生失败来自第二文档的合成 resolve 仍硬编码第一文档 ID。夹具改为读取准确 conflict 的对象 ID，并在删除前增加成功 ACK 的强断言。最终恢复记录数与空 pending 的断言继续保留。真实 HTTP/PostgreSQL 的普通远端更新、独有合并输入重开恢复、零自动提交和撤权拒读已在该轮通过。

frontend09 证据 SHA-256 为 `8b12945f630d97a09eac1bcf32e54738b34715fc8292f1021669fa2d2a359f9c`，全部步骤退出 0，服务残留为 0。44 个原生场景覆盖锁定期间的加密完成、配额和存储失败、多标签分支、迟到 ACK、原操作消失后的重新发现、默认删除阻止迟到密文、显式保留和跨对象隔离。正式浏览器连接真实会话、HTTP、PostgreSQL 与 IndexedDB，验证独有正文和个人补充在普通远端更新后重开恢复，云端内容维持另一设备提交值，撤权后恢复入口拒读。

capture01 证据 SHA-256 为 `c53863a4737dcf65fcb472a717b3bea3a69c9c3547b7a04abd3c44e51ee87ffa`，全部步骤退出 0，服务残留为 0。真实服务使用 media 基端口 22727，ledger 先尝试 32724，其 S3 端口 32727 被已运行的 media gRPC 拒绝，随后使用 35500 正常启动。该验证直接调用修复后的原夹具，原 17 项 capture 测试全部执行并通过。

逐名核对 full04 的 17 个 setup error 与 capture01 的 17 个通过用例，集合完全一致。full04 的 1251 个已通过父用例和 29 个子测试，加上上述补验，共覆盖 1268 个父用例与 29 个子测试，合计 1297 项。full04 的原失败状态继续保留。两个快照的 139 个后端生产文件字节一致，204 个后端文件仅 `deletion_support.py` 的夹具启动顺序发生变化。逐项核对见本机 `COVERAGE-RECONCILIATION.json` 与 `SOURCE-COMPARISON.json`。

最终前端执行 `./tools/pr08b/submit.ps1 -Mode frontend -RunId pr08b-frontend-20261117-09` 并通过对应 collect 入口回收。捕获补验通过既有 VMware broker 执行真实端口探针及原 `pytest tests/integration/test_capture_recovery.py`，完整命令、参数、解释器、工作目录和退出码保存于该阶段 `run.json`。本包最终功能源码与测试修复基点为 `2a7fa90`，其后交接收尾仅修改文档。

完整本机证据保存在 `outputs/pr08b/<RunId>/`。已取消运行保留 `cancellation.json` 与原排队文件。失败运行保留原 JUnit、日志和归档。

## 静态与契约

宿主默认 Python 3.10 执行 `python -B tools/overnight/check_contracts.py --output outputs/pr08b/static/contracts.json` 失败，缺少 jsonschema。来宾前端和全量阶段继续执行该检查并记录实际结果。

2026-11-17T14:28:59Z 宿主静态检查完成。Git 文件清单内 393 个 Python 文件 AST 全部通过，`tools/pr08b` 三个 PowerShell 脚本解析通过，三个 `.mjs` 文件 `node --check` 通过，`git diff --check` 通过。静态结果保存在 `outputs/pr08b/static/`。

旧产品六个 JavaScript 文件 `app.js`、`pcm-worklet.js`、`study-cache.js`、`review-render.js`、`review.js`、`portable-reader.js` 的语法检查全部退出 0。`setup.ps1`、`start.ps1`、`stop.ps1` 仅解析并通过，记录见 `legacy-static.json`。修正旧 Web 回归基线后，再次完成 393 文件 AST 与差异检查。

## 验收边界

- 本次 VMware 分阶段覆盖已闭环，独立结论见 [Astra 复审](ASTRA-REVIEW.md) 和 [风险闭环](REVIEW-CLOSEOUT.md)。GitHub CI 的实时结果以 Draft PR checks 为准。
- 既有认证会话 500 ms 行锁超时保留为并发可用性 P2，五轮诊断及后续完整运行通过，长事务与账本延迟下的容量边界仍待专项验证。
- HTTPS 浏览器使用测试证书 SPKI pin，系统证书链与 hostname 验证覆盖有限。原 Windows 回归使用缩短后的测试路径。当前交付为默认关闭的同步模块和冲突组件，后续页面接入、生产启用及数据库正文物理清理沿用既有范围。
