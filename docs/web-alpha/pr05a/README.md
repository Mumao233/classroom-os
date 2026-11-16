# PR-05a 缓存密钥安全 POC

负责人为林珈诚。用户于 2026-11-15 确认开发，仅授权本地实现和 VMware 合成测试。基线为 `81ab191b1f33de2371567eccd0ef9589d12483c2`，契约基线为 OpenAPI 0.2.0。分支为 `feature/web-pr05a-cache-security`。

2026-11-15 独立审核后的正式接入增量见 [正式身份与 PG 接入交接](FORMAL-INTEGRATION-20261115.md)。本轮复审按独审快照的新增/修改及直接影响进行，原独立 POC 作为已审基线。

## 范围与文件归属

本包独占 `tools/pr05a/` 和 `docs/web-alpha/pr05a/`。实验服务、浏览器页面及浏览器存储模块使用独立入口，测试只接收合成样本。工作树通过 Codex 工具创建，工具初始目录位于 C 盘，随后迁至 `J:\class-assistant\worktrees\web-pr05a-cache-security` 并修复 Git 登记、重新 attach。任务输出使用该工作树的 `work/pr05a/` 与 `outputs/pr05a/`。

协调登记由主仓库 `docs/web-alpha/coordination/PARALLEL-20261115.md` 管理。设置页、公共客户端由 Claude 协调。身份共享代码、迁移、冻结契约及依赖锁由协调聊天安排。PR-04a、PR-11a 和 PR-09b 分别保留原文件归属。

## 安全模型和身份接缝

实现采用现有设计的服务端 KEK 封装随机 256-bit DEK、AES-GCM、浏览器不可导出内存密钥、IndexedDB 密文和独立随机 96-bit nonce。AAD 绑定 owner、device、purpose、object、revision、schema、key_id 和 key_epoch。SQLite 仅作为独立合成服务的事务存储，不是生产迁移候选。

合成服务以随机 HttpOnly Cookie 在服务端定位账号、会话、设备和安全版本。客户端不能指定有效 Principal。每次解锁在事务内检查会话、账号 security_epoch、设备状态、key_epoch、用途授权和期限。此实验身份用于验证机制，正式 PR-03b 登录、MFA、再认证及撤销事务联调待协调接入。

正式接入候选使用现有 `AuthService.principal()` / `Principal.security_epoch`、服务端设备状态及 `/me/cache-unlock` DTO。设计中的 auth_epoch 映射到当前 security_epoch。key_epoch 与 KEK 版本各自管理。秘密响应使用 no-store，禁止进入通用幂等收据、outbox、访问日志或浏览器持久化。

实验只支持同源 loopback TLS，服务端端口由 bind 0 分配。浏览器使用本轮证书 SPKI pin，测试另一个证书必须失败。该证据覆盖 TLS 加密和固定密钥校验，系统信任链与主机名信任部署验收仍待后续。

本设计的服务端掌握解封能力。解锁页面 XSS、扩展或受控设备可接触已解锁内容，引用清理和字节覆盖也不能保证托管运行时物理内存擦除。离线设备在重新连接或实验解锁窗口到期后执行撤销，已交付字节的远程收回能力有限。

## 验收矩阵

| ID | 场景 | 必须观察的结果 |
|---|---|---|
| C01 | 封装与在线解锁 | SQLite 仅有封装 DEK；错 KEK、错绑定、越权和失效身份拒绝 |
| C02 | 内存密钥、IDB | CryptoKey 不可导出；IDB、localStorage、sessionStorage、Cache API 无明文或可解密 key |
| C03 | nonce、AAD、重试 | 新加密独立 nonce；重试复用原密文；篡改各绑定及密文认证失败；同版本异体拒绝 |
| C04 | 关闭并重新启动浏览器 | 相同真实 profile 保留密文，打开后锁定，重新在线授权才恢复 |
| C05 | 换号、退出、多标签 | 账号隔离、内存及展示清理、迟到响应失效，保留其他账号未处理密文 |
| C06 | 密码重置 | security_epoch 变化使旧 session/grant 失效；唯一稿封装密钥保留；明确恢复才重新授权 |
| C07 | 设备撤销 | 新解锁拒绝；在线复核锁定；离线到期锁定；恢复需明确授权 |
| C08 | 满盘和浏览器清理 | 原生配额错误及 IDB 原子回滚；保留既有密文和内存输入；清理后准确报告丢失 |
| C09 | KEK 轮换、DEK 轮换 | 旧密文可读，新写使用新版本；中断和并发事务一致；丢 KEK 明确失败 |
| C10 | 断网、到期、失效响应 | 到期停止解密；迟到 unlock/read 不能恢复旧状态；网络恢复重新核验 |
| C11 | 秘密响应、CSRF、Origin、TLS | no-store、无秘密日志/收据，错误来源/CSRF/证书拒绝 |

POC 使用真实 Chromium 持久 profile、Web Crypto 和 IndexedDB。配额受限测试使用 Chromium 原生配额覆盖，报告实际 API、阈值和 QuotaExceededError。物理磁盘耗尽单列验证状态。

## 交付和门禁

当前实现及各轮实际结果见 [本地合成验收](ACCEPTANCE.md)，复验步骤见 [运行说明](RUNBOOK.md)，正式身份与共享文件接入见 [接入候选](INTEGRATION-CANDIDATE.md)。

本地 POC、合成浏览器验收及正式身份联调分别留存精确版本和结果。TBD-03 产品候选见 [决策候选](TBD-03-OPTIONS.md)，实验参数不成为产品默认值。持久恢复启用仍等待安全评审和用户决定。提交、推送、创建 PR、合并、部署及真实私人数据接入另行确认。
