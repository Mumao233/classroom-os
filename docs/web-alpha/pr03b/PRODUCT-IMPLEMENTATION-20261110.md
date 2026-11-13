# PR-03b 正式实现接线与验证

负责人：林珈诚。工作包 PR-03b；未创建新的 GitHub PR。基线 HEAD e4853223847b3c26bfc74fa8fb2271cdb10e18b9。本文描述本轮新增实现，历史 S1/S2/S3 记录和审核原件不改写。最终结果见本目录 DELIVERY-20261110.md 和 outputs/pr03b/product-completion-20261110-01/DELIVERY.md。

## 应用接线

后端 Python 支持范围仍为 >=3.12,<3.13。应用由 `classroom_web.entrypoints.http.create_app` 显式注入 AuthService、0.2.0 OpenAPI 契约、唯一 HTTPS origin 和跨进程 WebSocketAdmission。可选注入原 PR-03a IdentityService 与 CourseReader；缺失依赖时相应路径关闭，不用 Mock 补位。无 import 启动、默认数据库、环境凭据发现或生产安装器。

AuthService 使用独立 PgAuthRepository 验证器和会话写入登录，Credentials 两把不同密钥、有效审批的 LocalPasswordPolicy、同机所有 API 进程共同使用的 HashAdmission。密码清单是逐行 UTF-8 密码的 SHA-256 小写十六进制摘要；审批摘要、有效期从独立受控配置提供，每次验证及提交前重新校验实际文件。清单缺失或审批过期不阻止现有密码登录，但拒绝新密码设定。密钥和数据库口令通过部署监督进程的安全配置/内存提供，不放入源码、命令参数、浏览器或日志。

追加迁移 0007/0008 只能按原数据库部署支持门槛评审后应用；本轮只在全新、可丢弃的合成 PostgreSQL 应用。0001–0006 原字节保留。运行角色不能使用表所有者、superuser、BYPASSRLS 或继承验证/恢复角色。各服务角色仅授予对应固定 identity_api 函数，不暴露通用 SQL 网关。

## 实现边界

- HTTP：邀请注册/激活/通用重发回执，登录三态、MFA、CSRF、注销/全部注销、绑定操作再认证、密码修改/邮件重置、邮箱变更/确认、会话和设备列表/撤销、MFA 管理、一次性恢复码和绑定接收端的自动恢复。
- WS：一次性票据、精确 Origin/cookie 校验、有界首帧和消息、周期重新检查真实会话/设备/factor/epoch、撤权关闭。capture/trust/reconcile 及其他工作包端点保持原关口。
- CourseReader：仅受保护课程详情 GET，运行时在同一业务事务重查 owner/session；成功用户活动更新空闲时间，轮询和失败读取不续期。
- 请求体、任务数、哈希槽2/队列8/排队1秒均有界。取得执行槽后再次检查绝对排队截止，避免文件锁I/O延迟后仍启动哈希；实际调度/系统调用耗时另报，不声称硬实时返回。HTTP 总预算5秒；取消返回后实际线程继续持有哈希槽直至计算结束。未知提交结果返回 RESULT_UNKNOWN，不自动重试写操作。
- 前端正式 App 使用 AuthWorkbench 和 /recover-receiver。使用生成的0.2.0类型与控制协议类型、成熟规范化与签名验证；秘密仅在内存。邮件令牌从 fragment 读取后立即移出地址栏；需退出已有会话再处理匿名验证。

## 邮件

AuthMailCipher 使用独立32字节密钥；AuthMailDispatcher 使用独立仅邮件函数登录和显式 SMTPActionTransport。生产传输必须验证 SMTP TLS；合成模式只允许字面127.0.0.1、非标准端口和 .test 收件人。密码重置存在/不存在两臂均入相同外形的不透明加密队列并返回通用回执，HTTP不等待SMTP。邮箱变更与邮件意图同事务。工作线程先提交领取，再读取有效动作并释放数据库锁后发邮件；队列最多200个活动任务，30秒租约、最多3次投递尝试，完成/丢弃清空密文。SMTP接受后确认丢失可能产生重复邮件，令牌的实际账户变更仍一次性。

SMTPVerificationTransport 复用原 PR-03a 的邀请邮件投递接口和提交协议。链接令牌在fragment中，不出现在HTTP请求目标或Referer中。

## 人工恢复与维护

RecoveryBroker 必须显式注入独立安装的根/当前D0读取器、外部当前核验记录、独立控制数据库登录以及真实内核 peer/单实例租约。真实D0部署渠道、本人OS映射、留存策略未登记前关闭相应能力。合成字典和本机当前SID仅是测试材料，不是真实登记证据。

LocalControlServer 使用 Windows ACL命名管道，固定服务器PID/SID验证、最大64KiB帧和16连接工作槽。普通控制面与维护管道分别配置；浏览器receiver不接受case/status/complete。receiver绑定等待后仅交付一次cap和seed；最终完成只能由原执行者新鲜证明原子提交，递增epoch、撤销旧凭据，不签发普通会话或恢复码。

`classroom-recovery --factory trusted_module:factory --deployment-id <公开部署UUID> <action>` 是交互式入口。工厂必须由部署方受控安装，返回固定服务器身份的LocalControlClient和凭据提供者（mode、authenticate(hello,peer)、prove(challenge)）。提供者只从保护输入/内存读取凭据。CLI要求交互终端，写操作先展示不含秘密的精确plan并要求输入动作和op-id；不会自动重试未知写入。`status` 必须沿用原op-id。此入口不能安装根、增加恢复权限或替代本人核验。

维护通过独立MaintenancePort执行M0–M4、排空池、独占门禁、外部接受、PG单调提交、发布ready。中断后可用 maintenance_start=True 关闭普通准入并仅向前恢复原始精确transition；不能回滚版本或借过期批准发起新变更。

## 复验入口

工具说明见 [验证工具](../../../tools/pr03b/product/README.md)。使用已有本轮任务目录中的明确依赖，不调用本地Windows产品启动器，不读真实配置。所有测试数据使用新建合成数据库，只有loopback；容量测试另用无外部路由namespace和cgroup。最终证据必须区分点估计、样本门槛、实际运行环境和独立审核。
