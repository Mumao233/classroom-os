# Web Alpha 独立仓库迁移记录

日期：2026-11-05（Asia/Taipei）。本文件随最小文档 PR 提交；提交时远端验证尚待执行，最终 PR、CI run/checkout、合并对象及本地连接切换结果以本地最终收据为准。没有把历史测试改写成本次成绩。

## 来源与身份

- 旧来源：<https://github.com/Mumao233/classroom-assistant>。旧仓只保留历史，本轮不对它进行任何写操作。
- 新仓：<https://github.com/Mumao233/classroom-os>，普通独立 public 仓库，ID `1445898183`，默认主线 `main`；不使用 fork、orphan 或无关初始化提交。
- 审核源 S：`c80fd29d7834cca040a0a037019c8ba4731ce4ec`。
- 源 tree T：`a7d8e70828d1187fb55485879013e1b3f11408dd`。
- 原源分支：`feature/web-alpha-0.1.0`；完整祖先 26 个提交，源 tree 376 个跟踪文件。含 PR-03a 全部四个实现/整改提交、PR-09a 全部六个提交及两父整合提交。历史 SHA 不改写。
- 正式本地入口仍为 `J:\class-assistant\classroom-os`；新仓最终 main CI 通过后，才切换其共享 remote、本地 main 和 upstream。临时 clone 不作为长期第二来源。

## 已完成的纯迁移核验与后续准入

先建立本地可验证 Git bundle 备份与 worktree/refs/必要配置快照，再用无共享对象、非浅、仅必要源分支、无 tags 的独立 clone，精确推送 S 到新 `refs/heads/main`。未使用 force/mirror/all/tags 推送，也未先改正式 origin。

从新 GitHub 仓库重新完整 clone，已实际验证初始 main=S、tree=T，全部 26 祖先仍可达，376 路径的 mode/type/object ID 逐项相等，`git fsck --full --strict` 退出 0。不是只比较文件数。原跟踪证据、历史来源 URL、失败日志及冻结契约随原历史保留。

后续文档只允许根 README、追加 AGENTS、Web 索引及本目录两文件。独立审查确认其余路径/mode/blob 不变后，普通 PR 的现有 `PR-00 Windows regression` 应在新仓运行；使用 GitHub merge commit，核对固定 head/base。最终 M 必须以 S 为祖先、父链对应实际 base/head、tree 等于获审文档 head D，且 S→M 仅为文档白名单差异。再对固定 M 执行现有 workflow_dispatch 并核对实际 checkout；通过后才切换正式本地连接。

现有 `.github/workflows/pr00-windows.yml` 未修改。PR-00 CI 范围是 Windows 525 主用例＋171 子测试（含合成 DPAPI）、九套 Node 及相关 guard/语法/静态，共 27 门禁；**不包含**远端后端 464、Chrome、真实身份、邮件或 Provider。实际数量、失败、skip、exit 以本次 run 原件为准，不能提前宣称通过。现有 artifact 仅保留 7 天，原件须及时另存。

## 历史验证与开放边界

2026-11-04 整合收据记录 S 的历史本地联合验证：后端最终 464＋原七探针、六项 EOL；前端 52/51/36，额外审查 13/3；Windows 525＋171、九 Node/27、类型/静态/双来源保护。这些没有在本次文档迁移中重新执行，不是远端业务验收。

原失败保持原义：Windows 首轮 WinError5；后端首轮 463/464、15s statement_timeout；Chrome 原 7 workers 为 35/36、Target crashed。后续有界复验通过，根因没有全部确定；不归咎为已证实的环境问题，不删原件、不改断言或超时。

- [PR-03a P3-01 OPEN](../pr03a/remediation/checkout/P3-01-DEPLOYMENT-SUPPORT.md)：0006 仅支持已验证可信 **bootstrap-only**；决定部署数据库与迁移身份前，按登记责任和条件处理。历史额外九探针为 **8 passed / 1 failed / exit 1**，非超级用户成功升级尚未交付；没有改成全绿。
- [PR-02b 两项 P3 OPEN](../pr02b/TECHNICAL-DEBT.md)：同键 reconcile 的迟到结果/回放语义在相应 HTTP 接入前明确；多 Attempt 历史清理覆盖在扩展多 Attempt 前补充。不得称已修复。
- [PR-09a Mock P3 OPEN](../../../web/frontend/PR09A-REMEDIATION-REPORT.md)：跨课堂固定合成实体 ID 复用，最迟 PR-10a 真实课堂事件接入前处理；当前 Mock 通过不是实际服务验收。
- [11 项 TBD](../DECISIONS.md) 按原责任、节点和证据追踪，不因建仓/CI自动关闭。
- 发送意图事务成功 COMMIT 仍为授权线性化点，不能改写为撤权返回后绝无物理网络调用；unknown 不自动重发、换 Provider 或退款。
- 真实邮件、Cookie/MFA/恢复码/多设备、真实收费 Provider、麦克风/设备、加密恢复、生产数据库、完整前后端 HTTP 联调、真机/原生缩放/读屏及发布部署仍未验收。不自动启动后续 PR。

## 证据保存与恢复

[精选证据哈希索引](EVIDENCE-INDEX.json) 仅保存摘要、原件定位和 SHA-256。Git 原已跟踪证据随历史迁移；本地忽略的原始日志、备份、依赖和测试产物继续留在 J 盘，没有批量纳入 GitHub，也没有声称所有原件都已上传。

本轮独立本地证据目录：`J:\class-assistant\classroom-os\outputs\repository-migration\20261105-migration-01`；备份和临时副本在对应 `work/repository-migration/20261105-migration-01`。最终收据另存本地，不在尚未验证时预填最终 M 或 CI 成绩。

迁移失败先保存已完成状态和原始结果，不删除仓库重来、不写旧仓、不改批准历史。正式 remote 切换失败按已保存的必要配置快照恢复连接关系；不 reset/clean 或覆盖他人修改。代码/文档回退需另行审阅反向 diff 并获相应授权，保留原批准历史和数据库账本，不作破坏性降级。
