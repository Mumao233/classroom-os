# 2026-11-07 实际进度盘点

依据当前 main `1de06a04f66e19f95a73fd81189b1df00d54dc40`、GitHub PR 状态、各工作包验收和整合记录核对。用户在审阅候选数据后明确回复「确认」，授权将下列六项登记为 `done`。本次通过 `roadmap.py set` 同步数据与报告，不扩大业务实现或执行授权。

| 工作包 | 登记状态 | 已完成范围和保留边界 | 已核对的 main 祖先提交 |
| --- | --- | --- | --- |
| PR-00 | done | 工程基线及隔离 CI 门禁；不代表完整 Web/生产验收 | `f7731002d974ea29296090c366b68569fc69ba89` |
| PR-01 | done | 已批准架构、ADR、HTTP/WS 契约；11 项 TBD 仍开放 | `1c3ef02eaba060d9ca9bc173e5bf1b8ab906ee5f` |
| PR-02a | done | PostgreSQL 实体、迁移、RLS、Repository 及整改；不代表生产迁移或真实身份联调完成 | `a76558ac64a95ae28874eaecabb71c5a4381a7ad` |
| PR-02b | done | 持久任务、事件、回执及恢复整改；两项 P3 仍开放 | `7ac4caaa23c89548c5f93aef1cdd64b1d042528a` |
| PR-03a | done | 邀请激活与资源归属及权限/来源整改；bootstrap-only、P3-01 和真实认证限制保留 | `54e26d997e0c1f883ef2ef340124190066f6aad1` |
| PR-09a | done | 已批准 Aurora Web 壳和 Mock 工作台；Mock 实体 ID P3、真实服务和真机边界保留 | `491b4d5ae2ead9d1dab944be9e5a1660f001ab1b` |
| PR-03b | blocked | S1、S2、S3 Batch 1 通过；Batch 2 诊断修订获独审接受，但真实 PG 归属/握手受本机监听状态观察不一致阻塞，数据库执行权关闭 | 本地 worktree 的分批记录，未将整包登记完成 |
| PR-06a | doing | 模拟 POC 已验收并通过 GitHub #4 整合；PR-06a 的真实接口验证未执行，仍需专项授权及原门禁 | 模拟提交 `2c7831b48080c810dbf7fb3aa28aad2f5791b9d5` |

其余 20 项保持 `todo`。设计、Mock 页面、自动化控制器费用限制和已有集成测试不作为这些完整业务工作包已实施的证据。05a 仍是关闭的未来模板。

初始登记曾将 `2026-11-07` 统一写入 history，页面据此误把盘点日显示为开工/完成日。用户要求核对实际开发时间后，已按原始对话和整合记录纠正这批占位日期；原版本保留在提交 `22cc426`。日期、时区、完成口径和证据见[历史时间依据](HISTORY-EVIDENCE.md)。阶段编号、标题、模块名、依赖边及已确认状态不变。

## 证据入口

- [PR-00 验收](../web-alpha/PR00_ACCEPTANCE.md)、[PR-01 收尾](../web-alpha/architecture/PR01-CLOSEOUT.md)、[独立仓库迁移边界](../web-alpha/repository-migration/README.md)。
- 本地整合收据：`outputs/pr02a-integration/INTEGRATION-RECEIPT.md`、`outputs/pr02b-integration/INTEGRATION-RECEIPT.md`、`outputs/integration-20261104-pr03a-pr09a/INTEGRATION-RECEIPT.md`。其批准提交分别核对为当前 main 祖先。
- PR-03b：`web-pr03b-sessions` worktree 中 `outputs/pr03b/s3-batch2-diagnosis-review-20261107-01/REVIEW.md` 和 `docs/web-alpha/pr03b/s3-batch2/README.md`；诊断接受不等于 Batch 2 或整包验收。
- PR-06a：[原模拟 PR GitHub #3](https://github.com/Mumao233/classroom-os/pull/3)、[整合 PR GitHub #4](https://github.com/Mumao233/classroom-os/pull/4)；合入后模拟验证见本地 `outputs/main-pr5-20261107/MAIN-VERIFICATION.md`。#3 的批准提交通过 #4 进入 main，不声称 #3 自身被 GitHub 标记 merged。
- [GitHub #1](https://github.com/Mumao233/classroom-os/pull/1) 是历史迁移文档 PR，不是工作包 PR-01；[GitHub #5](https://github.com/Mumao233/classroom-os/pull/5) 是工作流基础设施，不是新业务工作包或真实 ASR 接口完成。

本次盘点只更新进度资料和静态交付物；提交、推送用于保存这些已确认的改动，不包含合并、部署或真实数据库/API 调用。状态校验、报告生成和导出校验记录保存在本地 `outputs/roadmap-pages-20261107/`。
