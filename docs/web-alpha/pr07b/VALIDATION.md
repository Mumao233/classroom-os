# PR-07b 验证记录

负责人为林珈诚。开发基线为 `cf882602061e3d65341c2a130f8c4b26584e8850`，分支为 `feature/web-pr07b-summary-versions`。每轮验证使用独立冻结源码、逐文件 SHA-256 清单、合成账号与确定性 Fake Provider。

实现提交为 `347d37d0d6c53db900ff580e676f9b581da985f6`，交付对应 Draft GitHub PR #15。各轮完整原始证据保存在本工作树 `outputs/pr07b/<run-id>`，仓库内精简索引保留原始字节及统一 LF 后的 SHA-256，供不同 checkout 换行配置下复核。[Draft PR](https://github.com/Mumao233/classroom-os/pull/15) · [证据索引](EVIDENCE.json)

## 实际环境

2026-11-16 17:41–17:42 UTC 重新查询既有 VMware broker 和来宾。VMware Tools 为 running，来宾为 `TEST`，manufacturer 为 `VMware, Inc.`，model 为 `VMware20,1`。来宾当时没有非 loopback IPv4，Tools 执行、只读共享目录及文件回收均可用。

来宾使用 Python 3.12.14、PostgreSQL 17.11，以及已准备的 Node、Chromium 和 SeaweedFS 工具链。固定验证依赖的 20 个 Windows wheels 从 PR-07a 已有 wheelhouse 复制，并按每轮清单校验 SHA-256 后离线安装。宿主材料位于本 J 盘工作树，来宾既有 `J:` 实际映射为 `C:\j`。

环境查询、来宾依赖路径及实际 transport/guest exit 保存在 `outputs/pr07b/environment`。凭据由已有 broker 内存提供，验证材料不包含密码或真实 Provider Key。

## 命令与证据

`tools/pr07b/prepare_vmware.ps1` 负责冻结源码与生成来宾脚本。`tools/pr07b/run_verification.ps1` 负责提交独立任务、回收证据 ZIP、核对来宾与宿主 SHA-256、检查 ZIP 路径并生成 `RECEIPT.json`。早期轮次使用 `work/pr07b` 中的同源脚本，现已纳入版本控制。每次测试使用独立随机 PostgreSQL 端口及数据目录，只关闭本轮创建的服务。

定向测试实际入口如下，测试节点按轮次显式指定。

```powershell
& '.\tools\pr07b\run_verification.ps1' -Mode Targeted -TestTarget @(
    'tests/integration/test_pr07b_http.py',
    'tests/integration/test_pr07b_versions.py'
)
```

来宾执行 `tools/pr11a/run_tests.py --pg-bin <prepared-pg> --suite backend --test-target <node>`。该 runner 在独立真实 PostgreSQL 内执行 pytest，保留 JUnit、命令退出码、网络防护检查和服务清理结果。

完整联合验证使用以下入口，最后一轮结果记录如下。

```powershell
& '.\tools\pr07b\run_verification.ps1' -Mode Full
```

完整 runner 基于 PR-07a 已有联合 runner 适配，保存在 `tools/pr07b/run_combined.py`。它启用对象存储与真实 HTTPS/WSS 浏览器集成，核对 frontend 锁文件后构建当前源码，并核对 07a/07b 测试模块实际收集情况。每轮源码清单记录 runner SHA-256，runner 完整副本随证据 ZIP 回收。

## 已执行轮次

| 任务 | 内容 | 实际结果 |
|---|---|---|
| `pr07b-20261116-174855-a156677b` | 07b 内容验证与 07a 上下文/Provider 单元测试 | 131 passed，0 failures/errors/skipped，guest exit 0，PG stop 0 |
| `pr07b-20261116-175010-430a7515` | 数据基础与 07a HTTP 回归 | 48 passed，0 failures/errors/skipped，guest exit 0，PG stop 0 |
| `pr07b-20261116-175126-8e9fa102` | 07b HTTP 与版本测试 | 22 passed、5 failed，0 errors/skipped，guest exit 1，PG stop 0 |
| `pr07b-20261116-175423-1a213c0c` | 07b 内容、版本、HTTP、恢复及 07a 恢复回归 | 176 passed、1 failed，0 errors/skipped，guest exit 1，PG stop 0 |
| `pr07b-20261116-181909-9871799e` | 07b 多批首跑，含 schema、恢复和费用 | 121 passed、67 failed、21 errors，0 skipped，guest exit 1，PG stop 0 |
| `pr07b-20261116-182224-2a2da931` | 修复迁移变量歧义后的 07b 六模块 | 209 passed，0 failures/errors/skipped，guest exit 0，PG stop 0 |
| `pr07b-20261116-182806-19abc337` | 多批审查修复、恢复及既有持久任务 | 88 passed、1 failed，0 errors/skipped，guest exit 1，PG stop 0 |
| `pr07b-20261116-183242-bf77cbd7` | 全套后端、对象存储、HTTPS/WSS、当前前端构建 | 1145 passed、2 failed，29 subtests passed，0 errors/skipped，guest exit 1，PG stop 0，对象服务已停止 |
| `pr07b-20261116-184628-69a2c25f` | 历史原稿导航、多批及两项迁移修复 | 40 passed，0 failures/errors/skipped，guest exit 0，PG stop 0 |
| `pr07b-20261116-185003-9b1703db` | 最终全套后端、对象存储、HTTPS/WSS 与前端构建 | 1156 passed，29 subtests passed，0 failures/errors/skipped，guest exit 0，PG stop 0，对象服务已停止 |

第三轮发现三个 HTTP 用例的 PATCH 请求返回 405，原因是 ASGI 路由注册仍只包含 get/post 和既有管理 PATCH。另一个 HTTP 用例发现关闭 Review 后的生成准备被错误路由到 QA，返回 422。版本测试调用了不存在的 `tx.catalog.revoke`，真实墓碑入口是 `tx.tombstones.revoke`。这些失败保留在原始日志和 JUnit 中，修复后的验证结果需另行记录。

第三轮证据复制成功后，宿主辅助脚本误读前一个测试失败留下的 `LASTEXITCODE=1`，误报了证据传输失败。辅助脚本已改为读取 copy job 的实际 `BrokerError` 与 `Result.Succeeded`。随后使用已成功回收的 ZIP 完成 SHA-256 核对和归档，测试执行结果仍保留为失败。

第四轮确认第三轮五项失败均已修复并通过。新发现的失败来自真实 PostgreSQL 来源撤权竞态，撤权先取得数据库锁后，Worker 正确拒绝发送，但 `fail_unsent` 的错误代码白名单缺少 `SOURCE_UNAVAILABLE`，导致抛出 `ValueError`。实现已补充该错误代码，第六、七轮对应回归通过。

第五轮定位到批次触发器中局部变量与列名 `source_key` 歧义，导致所有依赖准备计划的测试失败。修复后第六轮 209 项全部通过。第七轮迟到原稿、冲突隔离、取消终态和恢复回归通过，唯一失败为公平调度测试的合成任务复用了同一个 plan_id，违反既有 `uq_task_plan`。该 fixture 已改为每个任务建立独立计划，后续完整回归覆盖修正结果。

第七轮另外完成候选契约双组合校验，两种组合各 52 个正反例全部通过，正式契约和输入对象保持一致。新专用契约模块由完整 VMware 回归收集。

第八轮全部 07a/07b 必需测试、20 个多批用例、契约模块、对象服务与浏览器用例均执行，前端契约生成检查、TypeScript、Vite production build 和产物验证退出码均为 0。两项失败来自旧 auth 测试固定期望 `pr07a_001`，以及费用组合测试将候选迁移固定挂在 `pr07a_001` 下。实现的正式迁移单头为 `pr07b_001`。测试已更新当前 head 与候选父节点，并增加 `pr07b_001 → pr07a_001` 断言。原有身份/RLS、重复升级及旧数据保留断言继续执行。

第八轮之后补充历史版本的 generation_task_id 导航字段。该字段的独立静态复审已完成，新增 HTTP 用例从重新登录的客户端进入历史文档，覆盖单批、多批、再生成、个人父链、越权和撤权。第九轮上述新增用例、全部 20 个多批用例及两个旧迁移失败均通过。两个候选契约组合各 56 个正反例全部通过。

第十轮于 19:02 UTC 完成，全部 1156 个主用例及 29 个子测试通过。JUnit 的 declared tests 为 1185，主 testcase 节点为 1156，证据索引分别记录两种统计。07a/07b、对象存储和浏览器必需模块均无漏收集，全部前端检查退出码为 0。PostgreSQL 停止成功，对象服务进程已退出，cleanup_errors 为空。

| 任务 | 源码 ZIP SHA-256 | 证据 ZIP SHA-256 |
|---|---|---|
| `174855-a156677b` | `d31f68c2469f84eb61d9a2b32296fe8599c522cbc5de297277d6810e02ee56e7` | `3967ccaa09ab141fde013e8e26c546e9285596752036412a2dee8801bbba53da` |
| `175010-430a7515` | `0230702d17c385bced4d2b0ce0a3b035e7ebae9ccba0b86cac72741b78412131` | `952f77cb15760dbb6a63a56a98ad99cfefca88712650fadb35777d89b56a93b9` |
| `175126-8e9fa102` | `f17fe38c41629e30a0b30c2bdbe4928e96988c94d71e0677fd9dec498cdc7fbf` | `a066b13d994a5244d00885893c46a7ff24caefd82193ff9b80d988075af23623` |
| `175423-1a213c0c` | `3206480f522af18c5eac6479d0d2cad44354df9683c2bdbc30f720a7f2766714` | `c0fa53dfe61464ce2e8d46d90389851ed5ab180fb3e97aaaa889d923565e0f76` |
| `181909-9871799e` | `4f41b67e5d34205f59db98bf3c4ac718fc7860786badca30b316c8697279c52c` | `a3552e1ee3f84af0a5816c49ebf452dca4fb0bcd9ddb66daf15f108b0bc57509` |
| `182224-2a2da931` | `c535f9287fe0ef517135f6d52699b33a4698ddac036931e720eea16e14ab4329` | `f4df174842cd35c5dd60267ee5561289b613ee3511e8af36161719daf504ca01` |
| `182806-19abc337` | `95e3b795a8d85a62906f7683fcd5f5bcddaf081c6964ed7c2d73affdc1e4ded2` | `cc5983ddced46f2311e27851846b089edb11966546ef2d504756ee41482e8790` |
| `183242-bf77cbd7` | `ed507c1f06a037d15c9dc74d2efe60294b47d405dadc1b877c9dd36f497243aa` | `8cf6139b6ad19518995e4f0516444d6c21099afb90a1c0e97b3ad2f7ead9aedf` |
| `184628-69a2c25f` | `5a73a54ab572c375042d84a8ce489f56f340767dc3d53e95258b1f1319808deb` | `58e6b02c3f1fe83e48742c0d23c7209369bbf4f92716b13f32ffc596f067df98` |
| `185003-9b1703db` | `921bed9ff36cfecc248cbe5043a4803f66eda9d5a1c8f19e78d4f7c3968e5a0e` | `c7f247bcb7e7a203b906b0b8a8781799740b1847550e9a22a869a1b0db87da23` |

## 当前验证范围

宿主已执行 152 个 Python 文件 AST 校验、准备与回收脚本的 PowerShell ParseFile、`git diff --check`。这些检查均通过。HTTP 测试使用真实会话、CSRF、受限 runtime 数据库身份和 PostgreSQL，覆盖正常生成、来源、个人版本、冲突保存、采用、撤权、越权及 120000 字符 Unicode 请求边界。

完整 VMware 回归、修复后定向验证和独立审查后的回归已完成。既有 GitHub Actions 工作流负责远端原 Windows 产品、Node、DPAPI、控制器及 Linux PostgreSQL 兼容性检查，交付提交的实际状态和执行链接在 PR 正文与检查页记录。[PR #15 检查](https://github.com/Mumao233/classroom-os/pull/15/checks)

全部已执行模型测试使用合成 Provider，真实付费调用为 0。测试运行没有启用生产服务或执行生产数据库迁移。

冻结之后仅对联合 runner 删除一个末尾空行，宿主 AST 比较完全一致，该格式差异单独记录于证据索引。正式迁移仍为 `pr07b_001 → pr07a_001`。2026-11-16 本次远端查询显示 main 仍为开发基线，未发现已发布的 PR-04b Pull Request。04b 合并前的迁移与生命周期联调要求继续保留。
