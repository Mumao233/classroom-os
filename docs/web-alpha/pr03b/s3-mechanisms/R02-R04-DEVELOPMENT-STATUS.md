# PR-03b R02/R04：常规开发修复及实际验证

> 2026-11-10 续开发：修复连接池关闭遇错中断及逐通道重复等待预算的问题。修前定向 3 项有 2 项失败，修后 3/3、宿主全部自检 63/63。仅宿主合成验证，未运行 guest、PG 或新增负载；最终源码未做真实 PG 短回归。见[本次关闭路径修复](POOL-CLEANUP.md)。R02/R04 仍 OPEN，R05 仍 INCONCLUSIVE；以下候选 69 记录保留为前轮历史结果。

2026-11-10（America/Los_Angeles）。按用户最新要求停止新增负载测试，继续普通代码修复和短功能回归。当前普通回归通过；R02/R04仍OPEN，S3未整体批准。此前高档位有明确失败，不能改写为“仅缺样本”或以短回归PASS覆盖。

分支`feature/web-pr03b-sessions`，HEAD`e4853223847b3c26bfc74fa8fb2271cdb10e18b9`，唯一父`e02614259b5e6483127e14f06e78cdb13a89f0c5`。沿用原dirty worktree，不切换、重置、stage或commit。改动限`tools/pr03b/mechanisms/`及本目录记录，所有获批契约、原guard文件、0001–0006和历史证据保留。

本轮补齐请求关联、阶段、原异常类别/SQLSTATE和耗时；先记录原异常，再做ROLLBACK；回滚和响应写失败标为次生。首32/末32失败独立保留，后续成功不能挤掉最早失败。宿主冻结旧函数反例证明：旧路径会把原Expired丢成回滚ConnectionError；新路径保留原因。日志不保存异常消息、请求正文、Cookie、密码、PHC或秘密。

固定原型的只读适配器减少重复Python/ctypes分派：持有不可写/删除的单链接文件和祖先句柄，使用只读映射，每边界仍完整计算PG可执行文件/配置/全部当前源码SHA。原进程身份查询及文件元数据循环使用来源绑定的只读AMD64原生库；新查询逐次读内核，不缓存进程、网络、权限或已验证摘要。源码完整SHA提供者改为Windows BCrypt；原guard的四个决策函数保留，但运行时读取器、进程观察辅助函数、纯路径语法及DLL绑定分派确实有变化。不能笼统说所有运行时guard函数未变。具体边界、编译命令和补偿措施见[适配说明](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-r02-r04-20261110-01/READ-ADAPTER-RATIONALE.md)。

64为启动前SQLAlchemy导入依赖ERROR，未启动runner/PG，已修正测试加载方式。66为两个适配器改写同一CPython函数签名引发ArgumentError，guard拒绝后退出；冻结66原件已在宿主复现。修复改用私有PYFUNCTYPE原型，保留CPython所需GIL，避免共享签名污染。另在每次native源码校验前确认期望参数未改变，并修正完整WRITE/DELETE控制项数，防止native计划沿用旧参数；这些检查不改任何实际源码字节或历史输入。

68的5项安装控制全部完成后，只读OS快照子进程超过原20秒期限而STOP；未执行HTTP功能请求。现补齐独立阶段耗时记录、原超时分类及自有子进程kill/reap回执，原20秒边界不变，不记录任意异常参数或消息。单独只读同路径诊断约2.093秒通过，零数据库连接；这不能反填68当时的具体内部原因，仍保留未定因。

候选69，guest`C:\Users\Administrator\PR03bS3Mechanisms69`，run`c3915682d68545c7b05f4f3217619a27`。新一次性受限PG，实际935/935固定断言、原生协调器退出码0及清理通过。仅一名合成用户、并发1、7次HTTP；另一次双源码读者重叠/归还和期望参数篡改拒绝控制。未运行负载生成器、NAT/长窗口/突发、R05采样或完整容量验收。

| 本次普通请求 | HTTP状态 | 秒 |
|---|---:|---:|
| preauth | 200 | 0.110 |
| login | 200 | 0.406 |
| owned_business | 200 | 0.172 |
| fixed_PG_timeout | 401 | 3.109 |
| after_fault_business | 200 | 0.172 |
| wrong_password_context | 200 | 0.078 |
| wrong_password | 401 | 0.282 |

真实PG固定超时为SQLSTATE57014，原异常先于HTTP映射记录，后续业务请求恢复成功。这里是`/lab/*`固定原型，PG故障安全拒绝的401不是正式HTTP契约的产品实现。宿主最后60项自身检查全通过，旧/新回滚反例及buffer并用反例通过；SQL解析及最后只读guest安全审计分别见回执原件。没有重跑历史后端464、Windows/Node套件或远端CI，不引用历史成绩代替本次验证。

67的高档位无hash臂业务100/100、登录10/10，但登录p95约3671ms；真实hash臂业务56/100、登录1/10，有超时和拒绝。完整来源与阶段记录见[67原始STOP](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-r02-r04-20261110-01/STAGE67-STOP.zip)。这证明高档可用性仍不合格；局部原生适配未闭合性能因果或容量。用户随后取消新增负载，故本轮没有运行1000/1780秒完整窗口；也未补API进程变体、角色/长度/冷热主要分层、过载和恢复的最终源码验收。原阈值、样本下限和哈希成本未降低。这些结论不阻止继续已授权的普通代码开发，也不构成容量或正式认证启用批准。

R01/R03保持独审固定原型CLOSED，R06历史HOME24/28/30/44具体内因继续未定因；新成功不反填旧STOP。R05在容量前置未满足时INCONCLUSIVE，真实新样本0。D01–D03设计层CLOSED，TBD-08OPEN，既有P3与部署门禁保留。正式认证产品、Principal/OwnerContext、业务迁移、真实D0/身份登记与恢复、E-T部署验收均未在本轮完成。一般数据库执行权CLOSED；本次仅固定脚本执行lease，结束已撤销。

交审材料：[交付回执](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-r02-r04-20261110-01/DELIVERY.md)、[最终实际摘要](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-r02-r04-20261110-01/FINAL-RESULTS.json)、[69实际证据](J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/s3-r02-r04-20261110-01/STAGE69-PASS.zip)。最终源码冻结、round47与候选34两层精确字节补丁、执行payload差异和独立scratch重建，均放在同一输出目录。作者侧功能PASS不等于独立阶段批准。

未修改宿主配置/VM硬件，不关闭、重启或恢复VM；未做正式迁移、真实恢复、外部API、Push、合并、部署或发布。保留原隔离与降权。停止数据库执行并收回证据后交付。
