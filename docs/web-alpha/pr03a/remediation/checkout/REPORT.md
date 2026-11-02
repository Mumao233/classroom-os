# PR-03a P2-01 最小整改报告

日期：2026-11-02（Asia/Taipei）。起点/被审SHA：`fa041e4c687be3d4b125f1d74024e7417ad19102`，分支 `feature/web-pr03a-identity`，开始时工作区/暂存区干净、无冲突。原 R1/R2/R3 由 Astra 关闭，R2 仅限可信 bootstrap。**本轮开发者定向验证通过；P2-01 的正式结论待 Astra 对新精确 SHA 独立复审，不自行批准或整合。**

## 判定与报告修复

旧特殊分支要求历史 CRLF hash，误拒绝仓库要求的规范 LF。现在只对 `tools/pr03a/remediation/selftest_runner.py` 判定：

`Git blob 一致 AND（git cat-file --filters --path 渲染的规范字节一致 OR 已归档准确历史 CRLF hash 一致）`。

规范 LF SHA256 为 `1632787cd443a8408e766d13cd9955326cabafa850bf0f61de808d493addf686`；准确历史 CRLF 为 `c5a321b794c5cbbe9c548641c08e137bab45f24193f2a1302c5e718b4a366c44`。历史值仍只从原 cb5 提交的 VALIDATION 读取，不用可变当前文件。没有全局换行归一化或任意 EOL 白名单。其他受保护工具继续要求规范原始字节与 Git blob 同时一致。

逐项结果增加原/current Git object、raw_checkout_allowed 与 failure_reasons；失败路径汇入最终 mismatches/tool_preservation_failures。基础阶段输出保留为 base-guard.log，stdout 只给最终结果；最终 passed 和退出码均要求基础与附加检查全部通过。缺文件也形成结构化失败，不把保护结果强改为绿。本轮输出使用新的 checkout-remediation 目录，不覆盖第三轮日志。

## 隔离实跑

新增持续回归：`tools/pr03a/remediation/test_protected_checkout.py`。使用真实 local clone、精确 fa SHA、`core.autocrlf=true`，每例初态均 Git clean、`i/lf w/lf attr/text eol=lf`。修后候选只覆盖授权 guard 文件；执行时该 adapter 是已知 dirty，未谎称整个副本仍完全 clean。提交后再在新精确 SHA 的完全干净检出运行一次保护，结果单独保存在交付 COMMIT_RECEIPT.json。

| 场景 | 完整保护命令退出码 | 判定 |
|---|---:|---|
| 修前 fa 干净 LF | 1 | 原 P2 确实复现，canonical raw/blob 均一致但旧特殊分支拒绝 |
| 修后规范 LF / autocrlf=true | 0 | 接受规范检出，历史 CRLF 标志为 false |
| 修后准确历史 CRLF | 0 | 同原 Git blob，且 raw hash 精确匹配归档 |
| 真实代码改动（CRLF外形） | 1 | 准确定位 selftest；git_blob_mismatch 与 unapproved_checkout_bytes |
| 已知文件混合 EOL | 1 | Git blob 相同也拒绝，unapproved_checkout_bytes |
| 另一受保护工具改 CRLF | 1 | Git blob 相同仍拒绝，证明例外没有扩展 |

两组 unittest 各3项、各退出0，合计6项通过，零失败/error/skip；前后保护命令的非零是预期拒绝，不混作产品通过。每例保存初态和执行前后 EOL、Git blob、raw hash、完整保护JSON、每条命令/退出码及stdout/stderr。

主命令：`work/pr03a/venv/Scripts/python.exe -B tools/pr03a/remediation/test_protected_checkout.py`；本次为两组独立进程各显式选择3个用例，完整参数及原始日志在本轮 VALIDATION 索引的 harness。环境 Windows / Python3.12.14，Git版本及临时环境在 checks/results.json；隔离 GIT_CONFIG_GLOBAL/NOSYSTEM、-B 与临时目录均显式指定。

初次两轮编排因描述性clone路径超过Windows Git MAX_PATH而失败，未形成有效P2保护报告；仅将隔离clone子目录缩为case-00等短名重跑，原断言/正式保护逻辑/Git属性未放宽。原失败日志与退出码保留 earlier-harness-failures.json；真正修前P2与最终6项证据分开。

## 本轮检查与历史边界

本轮实际运行：6项隔离回归、正式工作树保护退出0、改动两Python文件的mypy退出0、8项静态/契约/离线迁移检查退出0、diff检查及明确文件范围/原Git对象/历史raw hash一致性检查。0001–0006共13迁移文件、全部backend业务与既有测试、冻结契约、.gitattributes、历史selftest和原根VALIDATION保持不变。没有数据库连接或角色/RLS/恢复改动。

没有重跑464后端、Windows525主+171子或九套Node；这些历史结果属于fa041e4，不能代表新SHA重新执行。Reviewer新增9探针的8 passed/1 failed/exit1、原设计、原P2失败日志只读保留，原件与复制证据hash见本轮索引。非super成功预期的失败仍为失败，不改断言或删日志。

## 支持范围与停止点

当前0006仅支持已验证可信bootstrap；非超级用户成功升级尚未交付，已验证安全拒绝和完整回滚，托管平台/provisioning不作成功承诺。[P3-01 OPEN](P3-01-DEPLOYMENT-SUPPORT.md)登记责任、决定部署数据库/迁移身份前的截止节点和关闭条件。平台若不提供此bootstrap能力，先独立审查provisioning/撤销方案再做真实成功测试，不新增提权入口或遗留membership。本说明不是正式迁移授权。

当前验收和后端运维入口同步以上范围。无SQL/业务/历史迁移/冻结契约变化，未改其他工作树。真实邮件、认证会话/MFA、真实Provider费用、生产恢复、远端CI仍未验证；旧P3/TBD与新P3-01继续OPEN。没有Push、远端PR、Merge、Tag、Release、部署或后续PR。

本地提交完整SHA/父SHA、8文件清单、最终Git状态和新SHA干净检出证据见 outputs/pr03a/checkout-remediation/DELIVERY.md / COMMIT_RECEIPT.json；完成后停止等待Astra复审，即使APPROVE仍需用户另行授权整合。
