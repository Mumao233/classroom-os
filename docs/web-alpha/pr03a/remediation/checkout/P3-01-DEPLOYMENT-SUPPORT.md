# P3-01 OPEN：部署支持范围跟踪

登记日期：2026-11-02（Asia/Taipei）。来源为 Astra 对 `fa041e4c687be3d4b125f1d74024e7417ad19102` 的第三轮独立审查第 4 节及用户本轮范围裁定。本项不是正式迁移或部署授权，不单独阻止本次本地整合候选；P2-01 仍须修复并交独立复审，整合始终需要用户另行授权。

## 当前成功支持范围

`0006_local_reconciler_security` 当前仅支持已验证的可信 bootstrap 迁移路径。第三轮在原 bootstrap 及另一名字的合法 bootstrap 身份上验证了空库全链或有数据的增量升级、重复升级及相关数据/权限检查。不能从账号名称推断具备能力，也不能因此赋予运行角色 bootstrap 权限。

非超级用户成功升级尚未交付。已验证的是：在所测 provisioning 下，普通 REVOKE 不能清除 bootstrap grantor 的临时 ADMIN 成员关系，迁移检测残留后安全拒绝并完整回滚。Reviewer 另行授予旧 schema、表、函数管理权及三角色 ADMIN/SET 后，成功预期探针仍以 `P0001 / local reconciliation function owner cleanup failed` 失败；数据和完整权限状态回到进入 0006 前。独立 provisioning 的变动与 0006 的事务回滚分别留证，不能混称一次迁移成功。

托管平台、非超级用户 provisioning、其他权限撤销方式没有已验证的成功支持承诺。本项保留这个能力缺口，不推断所有可能的非超级用户部署理论上不可行。

## 责任、截止节点与关闭条件

| 项目 | 责任或要求 |
|---|---|
| 状态 | **OPEN**；本轮只登记，不自行关闭 |
| 实施责任 | Codex：核验候选平台、明确迁移身份及必要旧对象管理权，实施获批的适配和真实验证 |
| 独立技术审查 | Astra：审查支持范围、权限/provisioning/撤销方案及精确实现证据 |
| 平台与身份决策 | 产品/运维负责人：选择部署数据库及执行身份，确认运维流程；未指定个人，不编造指派 |
| 最迟处理节点 | **在确定部署数据库和迁移身份前**完成支持性判断；不把待验证平台写入已支持清单 |
| 关闭条件 A：采用当前 bootstrap 路径 | 明确目标平台确实提供所需可信 bootstrap 能力和受控迁移身份；在该平台的隔离合成环境成功升级/重复升级及故障回滚，留存 owner、ACL、RLS、会员/临时权限清理、旧账本和版本证据；Astra 独立核验并由产品/运维确认支持范围 |
| 关闭条件 B：平台不提供该 bootstrap 能力 | **先**形成独立 provisioning/权限撤销方案并交 Astra 审查，再经相应授权实施；用目标能力相符的真实数据库完成成功升级、零残留权限、失败回滚及所需安全回归，Astra 独立复审并由产品/运维确认后才扩展支持清单 |

关闭记录必须包含平台/版本、身份及管理权前提、方案审查记录、完整实现 SHA、实际命令/退出码/原始日志、成功与故障回滚证据及确认人。安全拒绝测试不能替代成功升级条件。若采用条件 B，不得借本登记扩展为通用提权入口、残留 membership 或放宽 RLS。

## 原设计与失败证据：保持原件

以下均是历史材料，未在本轮重跑或改写。第三轮新增探针的原始结果为 **9 项：8 passed、1 failed、0 error/skip，退出码 1**。唯一失败为 `test_approved_non_superuser_upgrade_succeeds_without_residual_membership`，它按原设计的非超级用户成功预期断言，显示该替代能力尚未交付。当前 bootstrap-only 裁定接受这一支持范围限制，**没有把失败改成通过**。

- 原 R2 设计：[已归档设计审查](../third-round/R2-DESIGN-REVIEW.md)。设计中“受控非超级用户成功升级”的预期作为历史保留，不能回写成已经完成。
- 本轮依据的第三轮独立报告：`J:/class-assistant/reviews/pr03a-third-20261102/outputs/reviewer/REVIEW.md`，SHA256 `fcfa1304da2fe60309c817282018eb7507ce36792b3f89afadefb8d865fac88c`。
- 探针原源码：`J:/class-assistant/reviews/pr03a-third-20261102/web/backend/tests/integration/test_reviewer_third.py`，SHA256 `70ec9deb3797e454b535763d35235312c7c3ec89eb29fcefd34cb3cb797d59cf`。
- 原始失败日志：`J:/class-assistant/reviews/pr03a-third-20261102/outputs/pr03a/third-round/targeted/20261102T132248.709401Z/pytest.log`，SHA256 `433e4602ec2eb8213ef2f6a78dd17e8f46b93a9f74c81f68b84a5daa9863941b`。
- 原始 JUnit：同目录 `pytest.xml`，SHA256 `313e2ae87dd04a86d33d5b0898eb21785d71881932e42ab69e92ab6897a7df8b`。
- 原始命令、退出码和集群身份：同目录 `results.json`；完整 Reviewer 执行索引：`J:/class-assistant/reviews/pr03a-third-20261102/outputs/reviewer/execution-index.json`。

审查副本只读，原探针、失败日志和设计材料不删除、不改断言、不覆盖。原 R1/R2/R3 的 CLOSED 结论对应被审 SHA；R2 的范围明确仅限 bootstrap。本轮保护工具 P2 的定向结果由新的验证清单记录，不混用上述历史测试数字。

## 当前运维边界

在范围未扩展前，按当前验收与[后端运维入口](../../../../../web/backend/README.md)计划可信 bootstrap 路径。正式数据库迁移、部署和发布均须另行授权；本文不提供可执行的正式连接目标或提权步骤。若需要停用，保留 raw、Attempt、Receipt、Outbox、unknown 费用风险和迁移账本，采用兼容代码或前滚修复，不清库、不破坏性 downgrade、不重发未知 Provider 请求。

本轮不改变 SQL、角色、RLS、恢复机制、历史迁移或冻结契约。既有两项 PR-02b P3、PR-09a 开放 P3 及 11 项 TBD 保持各自原责任和关闭节点；P3-01 不替代其中任何一项。
