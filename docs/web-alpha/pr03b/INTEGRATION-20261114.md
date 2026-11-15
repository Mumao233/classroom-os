# PR-03b 核心包合并后的集成补验

日期：2026-11-14（America/Los_Angeles）。负责人：林珈诚。工作包 PR-03b；本次无 GitHub PR 编号，未授权提交、推送或合并。

## 接手基线与范围

已从 origin 重新核对 main 为 `24899325cabf50edd1f7f49a2ceda899852fef0e`。用户确认核心包由 Claude 完成并合并；契约 0.2.0，迁移包含 0007_authentication、0008_manual_recovery。旧分支 `feature/web-pr03b-sessions` / `9b6e523` 及其工作树和历史证据保留。

范围以 [2026-11-12 决定](SCOPE-DECISION-20261112.md) 为准：人工恢复与维护 M0–M4 延后、保持关闭；R05 是设计论证加小样本对照，非原统计标准通过；R02/R04 仅沿用历史单点。不得从路线图的核心包完成状态推导这些能力已获启用，也不授权新的业务工作包或 PR-06a 的真实接口验证。

用户交接的 VMware 后端 582 / 前端 229 及 HTTPS/WSS 成绩属于此前验证，本次新增结果另列。Windows 浏览器只放行当次服务器密钥，不验证主机名与证书链；不能等同此前 Linux 的 TLS 验证。

## main 的远端 CI

| 工作流 | 精确基线与运行 | 本次核对结果 |
|---|---|---|
| Overnight pipeline and Web verification | `2489932` / [43677058418](https://github.com/Mumao233/classroom-os/actions/runs/43677058418) | 失败。两个 controller job 在 clean-source 校验前退出；Web job 在依赖解析退出，实际测试均未开始 |
| PR-00 Windows regression | `2489932` / [43677512450](https://github.com/Mumao233/classroom-os/actions/runs/43677512450) | 本次手动触发，已完成且 success；包含原 Windows Python、九套 Node、合成 DPAPI 和静态门禁。明细以该 run 的 artifact 为准 |

Overnight 整改草稿：静态校验依赖的 Pydantic / core / typing-inspection 与后端锁定版本一致；用精确路径的 `-text` 属性保留两份混合行尾历史文档的原始 blob，保留 clean-source 安全检查。完整后端含 Win32 命名管道，job 改用 Windows 和现有合成 PG runner，先构建前端、准备 Chromium，并运行原 Mock 浏览器套件；保留 Linux controller job。只使用托管 runner 的 PostgreSQL 17 二进制，创建随机端口独立合成集群，不接其已有数据库或服务。新增 Node action 固定到核实的 v4 SHA；后端保留 JUnit 非空、完整数量、零失败/错误/跳过与集群关闭门禁，最低实际主用例数为本轮确认的 582，不能用 29 个子测试补数。

上述工作流变更尚未提交、推送或在 GitHub 执行，不能记为 main 的 CI 已通过。VMware 补验和静态检查的命令、环境、退出码及原始证据写入 `outputs/pr03b/integration-20261114-01/`。PR-00 artifact 已回收核对：525 主测试、171 子测试、九套 Node 全通过，0 失败/错误/跳过；它是远端 Windows 结果，不冒称 VMware 结果。

## 本轮补验记录（持续补记）

- 来宾 TEST，Windows / Python 3.12.14 / PostgreSQL 17.11；VMware Tools 为 running，共享目录只读。来宾仅报告 IPv4 loopback，没有依赖 IP 网络传输。独立源码目录为 `J:\class-assistant\worktrees\pr03b-integration-20261114-01`（来宾 J: 是 C:\j 的既有映射），不覆盖旧来宾或宿主源码。
- 离线安装 backend lock 与修订后的 validator requirements 成功，`pip check` 退出 0；637 个快照文件与 SOURCE.json 逐字节一致。用候选行尾属性重现全新 main checkout，状态干净，配置校验退出 0，模型调用为 0。
- 契约 9 项静态校验退出 0；前端生成检查、TypeScript、lint、229 项 Vitest、生产构建及标记验证均退出 0；原 Mock 浏览器 36 项通过，0 跳过 / 0 unexpected / 0 flaky。Mock runner 只移除系统 Edge channel，使用锁定 Playwright 的 Chromium，测试内容、断言、匹配范围、超时均未更改。
- 完整后端命令 `python -I -B tools/pr03b/product/test_backend.py full`：582 主测试、29 子测试通过，287.99 秒，0 失败/跳过；另有 1 条 Starlette 的 httpx 弃用警告。真实 HTTPS/WSS 和 Win32 命名管道均在完整集内。run `ca686ee7ea3f4ead98278b9217775368`，测试退出码 0、合成集群停止退出码 0，broker transport=True / guest_exit=0。
- 首次 controller 补验未通过：281 项，2 failure / 29 error / 1 skip；ASR 模拟也在 Git fetch 验证阶段失败。补齐 task-private Git 的 shell 及 DLL 后，fetch 从原生进程退出码 -1073741819 恢复为 0；ASR 模拟 24 项候选测试与 4 项可信断言通过（无真实 Provider、音频设备或模型调用）。
- 第二次 controller：281 项，1 failure / 1 skip。剩余失败用例为 `test_engine_real_git_and_python_resume_complete_receipts_without_duplicate_calls`：保存的状态明确为 TESTING→FAILED，reason 为 Git rev-list 失败；单独读取同一合成仓库，原生 Git 退出 128，stderr 明确为 `Filename too long`（仓库路径长度 200，加 SHA 范围超过 Win32 限制）。未修改断言/超时或系统长路径设置，另将相同源码复制到来宾短目录 `J:\p3ci1114`（物理 C:\j\p3ci1114）复验。1 项符号链接攻击用例因来宾无创建符号链接权限跳过，覆盖仍未取得，不作通过处理。
- 短目录的 637 个源码文件逐字节校验一致，原命令 `python -B -m unittest discover -s tools/overnight/tests -p 'test_*.py' -v` 运行 281 项、264.095 秒，退出 0：280 通过、1 项上述权限跳过，0 失败/错误。深目录失败未删除或回填；这不是 GitHub Windows/Python 3.10 或 Linux/Python 3.12 矩阵实跑。
- 新增的工作流后端 JUnit 检查在来宾中读取本轮真实 XML/JSON：582 个实际 testcase、0 失败/错误/跳过、集群关闭 true，退出 0。首次单独执行因未复现工作流初始化步骤中的 `outputs/overnight` 目录而写报告失败；补齐相同初始化后通过，未改门禁条件。新增门禁和 action SHA 是冻结功能快照后的 CI 草稿补充，独立校验，不冒称整个最终 YAML 已在 GitHub 实跑。
- 已回收并核对三个证据 ZIP 的 SHA-256：check `b6ed68e36c9ce2a50d767bfd1ff107a76e18012bd2a32e2da8a822ee31f5ad0f`、backend `8834ffa9f1563d7eeda7eed91cbda88d3f02bbae0e033a6262b37e66d99f024c`、recheck `4269d80e50aa6de77967d67b2a6e3fc7e4021425f115f29e7d98be5d4fbbace4`。首次盘点因未映射来宾 J: 退出 1；首次通过 J: 回收文件失败，改用实际 C:\j 路径后成功；这些准备失败的 broker 记录也保留。
- 最终来宾证据包 `final-evidence.zip` 已回收，SHA-256 `802fb8dc0057eb682f072b0db5be41608fb8d729759bd5ac6359d55886d5709d` 与来宾相同。门禁独立作业的额外记录见 broker-records 中 `codex-main-20261114-junit-gate-02`。
- 宿主只执行静态检查：路线图 check、最终 14 段工作流 PowerShell 语法解析、diff 空白检查通过，两份历史文档保持 main blob 字节相同。一次尝试复用旧静态依赖目录因 `rpds.rpds` 缺失而失败，原输出保留；之后上述契约校验在完整来宾依赖内成功。

## 独立复审状态

使用现有 `.claude/skills/astra-review/SKILL.md` 的脚本，按 `--commit 062972c --round 0` 补审。预检通过，但审查命令无法执行，报 “registered Core launch requires an installed app”；模型未能读取提交。脚本最终为 `integrity_failed`：`sandbox_commands_worked` 为 0/2，`no_unsandboxed_tools` 为 2 个 MCP 调用。0 个发现不代表无缺陷。

按 skill 的“退出码非 0 就停下来报告”要求停止复审，没有降级、换二进制、修改全局配置或改走 API。原始证据及 final-report 位于 `outputs/astra-review/20261115T045054Z-commit-062972c-r0-XD8L84/`。062972c 仍无有效 Astra 结论，本轮 CI 草稿也未获独审。

## 延后与未关闭事项

| 项目 | 接手核对 | 处置 |
|---|---|---|
| 接收端会话早于恢复流程到期 | `recovery_receiver.py` 创建固定十分钟会话，后续操作拒绝过期会话 | 保留待修；不启用人工恢复 |
| 命名管道 ERROR_NO_DATA | `PipeListener.accept` 将 232 当重试，未在该分支断开或更换管道实例 | 保留待修；需真实 Win32 断连重连用例 |
| 已发布迁移的 adopt | `adopt_registration_transition` 的 ready 分支只认 old_version/old_hash；调用端在 committed 且外部 ready 时仍会调用 adopt | 静态路径支持原疑点；尚无本次真实 PG 复现，不标为已核实或修复 |
| 062972c 权限扩展 | `identity_api.preauth_receipt` 为 SECURITY DEFINER，固定 search_path，只认三种操作、64 位摘要及 UUID；按键事务锁串行化，identity 角色获 schema USAGE 和该函数 EXECUTE | 意图已确认；本次未修改权限或迁移。Astra 补审完整性失败，仍无有效结论，不能用早于该提交的最终报告替代 |

`queue_auth_mail` 的事务原子性、失败预算先占位及 release、键集翻页、AuthPolicy 固定默认值均保留。`EPOCH-BINDING.json` 按用户决定原样保留。

## 工作区和后续发布

正式入口已从旧路线图分支切换至基于 main 的 `feature/web-pr03b-integration-20261114`；原有 `CLAUDE.md` 和 `.claude/` 保留，不属于本次提交候选。原 PR-03b 工作树只新增独立、忽略的 broker 作业和传输材料，不覆盖既有文件或历史证据。所有相关本机测试仅通过现有 broker 在 VMware 来宾内执行，宿主只编辑、准备材料、静态校验和回收证据。

接手期间另一个流程将旧工作树切至 `fix/pr03b-main-ci`，增加提交 `7db6566` 并继续修改其 conftest.py；这不是本轮操作，未覆盖、采纳或提交其改动。双方共享 broker，作业按其队列串行执行。本轮源码仍固定来自 2489932 加 SOURCE.json 列出的集成草稿，不使用对方未提交的测试选择规则。

路线图依据用户本次明确的“核心包完成并合并”事实补记 2026-11-14；保留旧状态历史，负责人改为林珈诚。生成的本地文件不代表线上 Pages 已更新。

## 当前交付与停止点

候选为 9 个文件，见同轮输出 `candidate-files.json` 和 `integration-draft.patch`；排除原有 CLAUDE.md / .claude/ 以及另一流程的改动。未修改正式认证/业务源码、迁移、契约、原测试或运行时阈值。M0–M4 和三项延后问题未修、未启用。没有提交、推送、合并、部署或付费业务调用。

本机已完成可用环境下的上述补验，不能据此宣布 main 的 Overnight 通过或独立复审通过。后续应先恢复有效 Astra 复审，再由用户逐次授权提交和推送本轮明确文件集；工作流进入远端后，须重新核对精确 checkout SHA 及三个 job 的实际结果。此记录不授权修复 Codex 全局环境或扩大业务包。
