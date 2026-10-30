# PR-02b 技术债登记

登记日期：2026-10-30（Asia/Shanghai）。状态依据：用户最终本地整合授权及 Astra 第二轮独立复审。

批准实现：`7ac4caaa23c89548c5f93aef1cdd64b1d042528a`；原始实现：`406bb9c6d943e4b30eef62e0397bfed1103e96e6`。

以下两项均为 **P3 / OPEN**，不阻止本次 PR-02b 本地整合，尚未修复或关闭。本登记不改变冻结契约，不授权 HTTP 接入、多 Attempt 扩展、真实 Provider、收费或后续 PR 开发。

## PR02B-P3-01：同键 reconcile 与迟到结果的语义

- 优先级与状态：**P3 / OPEN**。
- 当前事实：unknown 且尚无 raw 时，reconcile(K) 可保存收据；raw 迟到后，再用同 K 可完成原 Attempt 并产生一次完成事件；再次同 K 不继续写入。Provider 总次数仍为 1，没有新增收费调用。
- 待明确：同键究竟只回放首次核对结果，还是继续协调同一执行的当前结果。不能将现状描述为“旧收据回放必然零写入”。
- 关闭关口：**该 reconcile 操作的 HTTP 接入前**明确产品/契约语义，经 Astra 审查；如契约有变化，遵守现有版本、兼容与消费者同步流程。
- 验证要求：补充“无 raw → K → 迟到 raw → K → K”回归，断言单一收据、原 Attempt、最多一次实际完成事件、无新增 Provider 调用。
- 后续责任：Codex 实施与测试，Astra 审查；涉及产品选择时由用户确认。
- 本轮处理：登记并保留，不修改行为，不声称已完成上述新增回归。

## PR02B-P3-02：多 Attempt 历史中的未使用计划终态覆盖

- 优先级与状态：**P3 / OPEN**。
- 当前事实：人工构造旧 Attempt 已有 raw、后来还有 planned、且没有 cancel_requested_at 的历史时，可完成旧结果并留下未发送 planned。正常 PR-02b 入口仅建立首个 Attempt，Task 终态阻止后续领取；未发现该组合产生重复执行或收费。
- 待明确：无取消请求的多 Attempt 历史完成后，未使用 planned 的终态及清理/保留规则。
- 关闭关口：**扩展多 Attempt 工作流前**补充一致性覆盖并审查终态规则。
- 验证要求：覆盖旧 raw + 新 planned + 无取消请求，断言原结果/原 Attempt 一致完成、未使用计划按明确规则处理、Task 不可再次领取、无新增 Provider 调用；继续保留旧 unknown 与可能收费风险。
- 后续责任：Codex 实施与测试，Astra 审查。
- 本轮处理：登记并保留，不修改 Attempt 历史，不声称已修复或已完成上述新增回归。

## 必须保持的发送授权边界

发送授权的线性化点是 `mark_submitting` 事务成功 **COMMIT**。撤权先完成时拒绝发送、Provider 调用为 0；发送意图事务先持锁时，撤权等待其 COMMIT，已经授权的单次调用可能在后来撤权完成后才实际进入 Provider。不能据此宣称“撤权返回后绝无物理网络调用”。之后保存/发布仍重新授权；不确定发送保持 unknown，不自动重发、切换 Provider 或退款。

复审依据：源工作树 `outputs/pr02b/independent-review2-20261030-173221/REPORT.md` 的 P3-01/P3-02；本轮原样留存副本位于正式仓库 `outputs/pr02b-integration/ASTRA-REVIEW2-APPROVE.md`。完整整合证据位于 `outputs/pr02b-integration/`。

本文件以独立文档提交归档两项开放技术债；批准的两个实现提交历史保持原样。归档不表示技术债已修复，也不扩大后续业务开发授权。
