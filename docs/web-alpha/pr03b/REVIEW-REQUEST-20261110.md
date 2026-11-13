# PR-03b 精确快照独立审查输入

负责人：林珈诚。此文件是待执行的审查请求，不是审核通过回执。工作包为PR-03b；没有对应本轮新建GitHub PR编号。

## 输入与保全

以outputs/pr03b/product-completion-20261110-01/PR03b-source-20261110.zip、SNAPSHOT.json和FINAL-SOURCE-MANIFEST.json为精确输入。首先重新计算压缩包和解包文件摘要，再核对PRESERVATION-AUDIT.json与BASELINE.json。未提交工作区不能仅靠基线HEAD e4853223847b3c26bfc74fa8fb2271cdb10e18b9指代。历史设计/审核/失败原件保持各自范围；本轮371个受保护文件字节未变。索引当前暂存内容与HEAD一致，索引文件字节摘要发生过变化，不能按原摘要声称字节保全。

源码/接线说明见PRODUCT-IMPLEMENTATION-20261110.md，实际验证/局限见DELIVERY-20261110.md。验证命令、运行日志、JUnit、严格CA浏览器和原始容量观测在交付目录。DIAGNOSTIC-REDACTIONS.json记录本轮诊断中合成秘密的脱敏；失败与计数保留。

## 建议审查重点

1. 0007/0008固定SQL函数、独立NOINHERIT凭据、表/函数owner、FORCE RLS、角色成员关系、search_path及锁序；确认0001–0006不被改写。owner必须来自当前会话，同一业务事务检查device/factor/epoch及撤销事实。
2. 哈希PHC边界、清单审批/提交前重新校验、全API共享2执行/8排队/1秒、真实计算在HTTP取消后仍持槽、WS全局准入及到期复查。5秒是HTTP预算；原连接池5秒只限制等待排空，不保证整个关闭过程5秒完成。
3. CSRF/Origin/JSON/Unicode/size边界、登录三态及MFA隔离、再认证精确绑定、一次性恢复码、密码/邮箱变更后epoch和撤销、提交未知结果禁止自动写重试。
4. 人工恢复：独立安装根与外部当前D0、Windows真实内核peer和服务器身份、接收端等待/PoP/cap/seed仅一次、学生当前密码、原执行者新鲜complete、status最小凭据及不发新proof。浏览器不能调用complete/status。
5. 维护中断后的原transition向前恢复、PG已经提交与尚未提交的差别、过期原批准只用于精确已提交恢复、root轮换的历史独立安装根及发布ready。合成外部reader不是操作系统隔离D0的部署证明。
6. 邮件领取提交后释放PG锁再发送、存在性两臂相同回执/不等待SMTP、独立密钥与邮件凭据、租约/重试/密文清理。真实SMTP认证/TLS服务未在本轮接入，loopback传输仅验证协议与用例。
7. 前端生成契约、成熟规范化、receiver严格schema、秘密内存生命周期、异步旧响应丢弃、fragment令牌移除、不自动重发写操作、生产不回退Mock。

## 不能从本轮结果推导的批准

支持Python3.12的完整混合/过载报告仅覆盖实际跑过的单一配置。R02/R04完整候选/分层/进程变化/worker/目标VM矩阵未齐，R05同目标序列统计仍INCONCLUSIVE；请明确区分点估计、N门槛、置信与相关性。历史R06 STOP未知根因不被新通过覆盖。真实D0、OS本人映射、留存与启用尚未登记，S3/S4不得由作者自行关闭。

如需复验，只使用新建、合成、隔离的数据库和任务目录；不要读取或迁移现有真实数据库/配置、安装真实根、恢复真人账户、启用收费服务、push/merge/deploy。禁止降低原阈值、清除真实限流预算、删除失败或改写历史审核结论。审核回执应给出精确源码摘要、实际命令/退出码、发现及其边界。
