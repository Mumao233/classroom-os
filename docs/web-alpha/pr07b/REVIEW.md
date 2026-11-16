# PR-07b 独立审查与整改

负责人为林珈诚。独立审查由 Astra 代理执行，只读检查实际差异、迁移、领域逻辑、HTTP 接缝、费用和任务恢复。审查者没有运行功能测试，动态证据来自主代理编排的 VMware 来宾。首轮与多批首轮均为 REQUEST CHANGES。

## 发现与修复

| 发现 | 修复与回归覆盖 |
|---|---|
| 多 Attempt 原稿读取可能选择没有结果的最新 Attempt | 优先使用已发布版本的准确 raw 引用，再取实际持久结果。旧结果恢复测试验证返回原稿 |
| 后续来源撤权导致不可见个人稿指针阻止整个文档更新 | 数据库仅在指针实际变化时重验目标，真实 PG 测试覆盖撤权个人稿与可见候选并存 |
| Review 组合阻断 legacy billing cancel | 取消路由保留现有 BillingBrowser 入口，HTTP 账本回归验证 |
| QA 经 Review 路由丢失自身费用生命周期 | 按选中的 Repository 使用 QA 或 Review billing，HTTP 回归验证 |
| 重新生成目标撤权后保存结果反复恢复 | 发布使用 savepoint，永久目标错误终结任务并保留原稿与费用 |
| 收据 metadata CHECK 缺少 Review 类型 | 与正式迁移同步三类 Review receipt resource |
| 初始实现缺少完整多批编排 | 增加不可变清单、逐调用任务与预留、共享配额、全局别名、本地最终合并及故障测试 |
| 候选契约缺少配套 fixtures | 增加 56 个正反样例、双组合 OpenAPI 校验与专用测试 |
| 已验证结果后出现冲突原稿仍继续调度 | 整族冲突检查使父任务进入 unknown，取消后继未发送任务，发送前再次检查 |
| 子批原稿路由未选择子协议 Repository | 按持久 task schema 分派 raw-output，子批原稿回归覆盖 |
| 前 32 个 unknown 父任务使后续任务饥饿 | 受限调度函数按 not_before 轮转，并使用 SKIP LOCKED。真实 PG 测试覆盖超过一页等待任务 |
| unknown 父任务恢复被旧执行 guard 拒绝 | 新迁移保留原守卫，仅允许具有有效无冲突首批结果、无租约的多批根任务恢复本地协调。父与子批迟到结果覆盖自动和显式恢复 |
| 冲突诊断与最终发布、发送前检查存在竞争窗口 | 统一锁定根及已创建子任务后检查整族结果，冲突提交与发布具备明确事务顺序 |
| 取消终态重放误入单调用 legacy 修复 | 多批 cancelled 根任务使用稳定收据路径，同键与新键 cancel/reconcile 保持终态和版本 |
| parent reconcile 与协调器锁顺序相反 | 统一 owner advisory、根任务、按批序子任务的加锁顺序。真实 PG 测试观察阻塞关系并验证双方完成 |
| 新客户端无法从历史版本发现对应生成任务 | 候选 ReviewRevision 增加可选可空 generation_task_id，绑定既有根任务列，提供历史版本到逐批原稿的导航并补权限回归 |

迁移首跑另发现 `review_batches` 触发器局部变量与列名 `source_key` 歧义，已重命名局部变量，保留原始失败证据。

## 最新静态结论

2026-11-16 的多批独立静态复审确认未发现剩余 P0/P1/P2 阻断问题。随后收尾核对发现历史版本到生成任务的导航缺口，审查者将其确认为 P2 接入问题。补充字段、兼容说明和回归后，最终静态复核确认该 P2 已补齐，本轮未发现新的阻断问题。两项旧迁移测试仅更新当前 head 与候选父节点，原迁移链断言保留。

审查者确认 cancel、reconcile、advance 和发送前检查在 Outbox 写入前锁定任务族，消除了此次发现的 counter/child 逆序。真实模型内容质量仍待专项验证。

审查覆盖下列最终实现文件。完整 VMware 验证采用同一组源文件，其结果以验证记录为准。

| 文件 | SHA-256 |
|---|---|
| `review_generation.py` | `8ab2452e8ae0cc5126708d7fbd2821ab87aba24f7454a784b9b693fee090f8ba` |
| `pr07b_task_functions.sql` | `1298191ba301fd50e822b589201c3a9353ca158313e2b00260de3afeffa2f056` |
| `task_repository.py` | `b27151c18e547cdf9952eec797c36f65d625e45052c42f34fac6ff273ad537e5` |
| `review_repository.py` | `f570c4cb6c05abcd6fd4976f0988748c34d83b00c919976a0b7e3269f4114c3f` |
| `tools/pr07b/contract_candidate.py` | `177288ddcc12d2250ea4c430e4e14d85360d9353a378fa1b3f4bb45897370e1b` |
| `tools/pr07b/fixtures.json` | `234d9a033cb4a24a229360051ff3cecf58ff559974c38fcb382922c3113a61cf` |

本文件对应的动态结果及源文件 SHA-256 清单记录在验证文档。合并前仍需复核 PR-04b 的迁移单链、物理清理与备份恢复接缝，真实 Provider 接线和收费启用遵循原有授权门禁。[验证记录](VALIDATION.md) · [接入契约](CONTRACT.md)
