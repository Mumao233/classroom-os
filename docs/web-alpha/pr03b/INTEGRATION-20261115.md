# PR-03b：CI 修复合并后的接手整合

日期：2026-11-15（America/Los_Angeles）。负责人：林珈诚。业务工作包 PR-03b；核心包合并提交 2489932，支撑其 CI 修复的 GitHub PR 为 #9。

**收尾结论：PR-03b 核心包按已确认范围完成。**本结论由用户要求宣布，覆盖登录/会话、MFA、改密与重置、CSRF、WSS 票据、F01/F02；不把延后的 M0–M4、R05 统计证明或未取得证据的权限补审记为完成。

## 基线与已合并内容

用户确认另一流程合并结束后，重新 fetch 并核对 `origin/main` 为 `e793e8fd412c236c0930e5b5a57acf57ad5958e3`，即 GitHub [PR #9](https://github.com/Mumao233/classroom-os/pull/9) 的 merge commit。它含 `7db6566` 和 `c13b214`：对齐静态校验依赖、规范两份历史文档行尾、真实浏览器显式收集、人工恢复测试只在 Windows 收集。PR #9 是支撑 PR-03b 的 CI 修复，不能将仓库编号 #9 当业务工作包编号。

本轮保留已合并的测试分工：GitHub Web job 为 Linux 可移植后端检查，不收集 `test_auth_browser.py` 和 Windows-only 的 `test_manual_recovery.py`；完整 Windows 后端、真实 HTTPS/WSS 继续通过 VMware 执行。`tools/pr03b/product/test_backend.py` 显式设置 `PR03B_REAL_BROWSER=1`，Windows 来宾也不会触发人工恢复模块的非 Windows 排除条件。CI 成功不能冒称已覆盖这两个模块。

## 草稿去重与保留

原轮次的 Windows CI 改写、`.gitattributes` 例外及依赖重复修复已撤下；这三个文件与当前 HEAD 一致，没有替换 PR #9 的方案。修前完整候选文件和未提交差异存入 `outputs/pr03b/integration-20261115-01/before-reconciliation.zip`、`before-reconciliation.patch`（后者是当时完整已跟踪差异的本地备份，含原有 CLAUDE.md 差异，不是提交清单）；原始记录 [2026-11-14 补验](INTEGRATION-20261114.md) 和对应 outputs 不删除、不改写。

保留的补充：路线图按用户确认的核心包完成事实更新（历史完成日 2026-11-14，负责人林珈诚）；本目录当前入口与两轮报告；Mock 浏览器 runner 使用已准备的 Playwright Chromium，避免依赖来宾不存在的系统 Edge。原 36 项测试、断言和超时不变。新的本地分支为 `feature/web-pr03b-integration-20261115`，基于上述 main；原有 CLAUDE.md 的未提交内容逐字节保留，.claude/ 不动，不纳入本轮候选。旧工作树仍为 `fix/pr03b-main-ci` / `c13b214`，其历史证据保留。

## 本轮验证

| 环境 / 项目 | 基线、命令或入口 | 状态 |
|---|---|---|
| GitHub Overnight | main e793e8f / [43702226998](https://github.com/Mumao233/classroom-os/actions/runs/43702226998) | 本轮手动触发，completed / success，三个 job 全通过；artifact 的 checkout_sha、event_sha 均为精确新主线 |
| GitHub PR-00 Windows | main e793e8f / [43702229669](https://github.com/Mumao233/classroom-os/actions/runs/43702229669) | 本轮手动触发，completed / success；artifact 核实 525 个主测试、171 个子测试通过，0 失败/错误/跳过，九套 Node 全通过，合成 DPAPI 保留 |
| VMware 精确源码 | e793e8f 加本轮文档和 Mock runner；638 个文件与本轮 SOURCE.json 逐字节一致 | broker transport=True / guest_exit=0；后端 582 主＋29 子测试、前端 229、Mock 浏览器 36 均通过；无失败/错误/跳过，Mock 无 flaky；owned PG cluster stop=0 |
| 宿主静态检查 | `python -B scripts/roadmap.py check`、差异 / 链接 / 文件保护核对 | 均通过；35 个相对链接存在，Markdown 围栏平衡，原有 CLAUDE.md 字节保持一致，已合并 CI 文件无本轮差异 |

可控材料与输出均在既有 J: 项目的 `work/pr03b/integration-20261115-01/`、`outputs/pr03b/integration-20261115-01/`；通过用户已有 broker 调用 VMware，宿主不运行功能或单元测试。来宾使用原有 J:→C:\j 映射，物理位置如实另记。

Overnight artifact 的实际分项：Windows / Python 3.10 和 Linux / Python 3.12 控制器各 281 项通过，无 failure/error/skip；Linux 后端 567 个实际 testcase、29 个子测试（JUnit 总数 596），0 失败/错误/跳过，独立合成集群已停止；ASR candidate 24 项与 trusted 4 项通过。Linux 少于 Windows 完整集的原因是上述两个模块未收集，不能拿 JUnit 总数含子测试的 596 与 Windows 的 582 主用例直接比较。

本轮 VMware（TEST、Python 3.12.14，沿用已冻结的 PostgreSQL 17.11 与 Node 工具链）实际入口：`python -B tools/overnight/check_contracts.py` 的 9 项静态检查；`python -I -B tools/pr03b/product/run_frontend.py checks` 的生成、类型、lint、Vitest、构建和生产标记六项；同 runner 的 `browser` 模式原 36 项；`python -I -B tools/pr03b/product/test_backend.py full` 的完整集。新来宾目录为 `J:\class-assistant\worktrees\pr03b-integration-20261115-01`（物理 C:\j 下），显式复用前轮冻结的工具链与任务独立 venv，pip check 退出 0；未重新安装或改动其依赖。

后端 run `af1ed4495c7c495991397ada405e842e`，296.74 秒，JUnit 实际 testcase 582、总数含子测试 611；XML 已另核对两个 CI 排除模块在此完整集中确实执行。测试退出码 0、集群关闭退出码 0。1 条既有 Starlette/httpx 弃用警告保留。真实 HTTPS/WSS 沿用当次服务器密钥放行，未覆盖主机名或证书链校验，未增强为 Linux 的 TLS 保证。

证据包已回收至 `outputs/pr03b/integration-20261115-01/evidence.zip`，SHA-256 `638a29bfd68870e75a267065e24741d4e4e98964249505af0d0a1fc3a7e64aa1` 与来宾一致；具体命令、退出码、原始 log/XML/JSON 及 CI artifact 均保留。宿主仅核对证据、做静态校验，不在宿主运行上述套件。本轮既有业务/认证代码、迁移、契约与原测试均未新增改动。

## 仍开放的边界

人工恢复与维护 M0–M4 按 [范围决定](SCOPE-DECISION-20261112.md) 延后、保持关闭；接收端会话提前到期、ERROR_NO_DATA 重连、已发布迁移 adopt 三项均未在本轮修复。R05 为设计论证和小样本，非统计通过；R02/R04 仍只沿用历史单点。没有启用任何真实 D0、生产迁移、付费 Provider、麦克风或发布流程。

本轮未取得 062972c 的有效 Astra 补审证据：前轮现有 skill 脚本因 “registered Core launch requires an installed app” 而 `integrity_failed`，原始记录保留。本轮不以 PR #9 的合并、CI 或作者测试代替该补审，也不重试、降级或擅改 Codex 全局环境。此限制不回写为对已合并核心包的撤销。

## 收尾发布与授权边界

用户本次原话为：“那将本地的main也按照github上合并 然后宣布pr03b完成吧 记得改网页”。据此执行本地 main 同步、核心包收尾确认及路线图网页发布；不重复询问已有明确授权。

发布前基线为 e793e8f。本轮在此前七文件补充上增加 Web 总入口的过时状态修正，共八个文件：路线图 JSON/生成报告、Web 总入口、本目录 README、两轮整合记录、product README 与 Mock 浏览器配置。原有 CLAUDE.md / .claude/、工作期间外部新增的 `docs/web-alpha/evidence/DEFENSIVE-PROGRAMMING-REVIEW-20261115.md` 均排除且保留；原工作树、旧输出和冻结源未清理。具体文件、发布提交、Pages 部署和线上核对结果另记同轮 outputs 的 `CLOSEOUT-RECEIPT.json`，避免在本提交内自引提交 SHA。

发布使用仓库现有 GitHub Pages 工作流，只发布路线图静态站点；不部署 Web 业务应用、不迁移生产数据库、不启用恢复或付费服务。完成日按核心包实际合入的 2026-11-14 保留，2026-11-15 是本次收尾与网页发布确认日。网页的完成状态来自用户本次明确要求，原状态历史只追加并保留。
