# PR-05a 本地合成验收

日期为 2026-11-15，负责人为林珈诚。当前结论为本地合成作者验收通过，最终归档已回收，源码与往返归档哈希一致，清理核对通过。独立安全评审、TBD-03 产品决定及正式身份接入保持待完成。

## 实现与基线

分支为 `feature/web-pr05a-cache-security`，工作树为 `J:\class-assistant\worktrees\web-pr05a-cache-security`。起点和当前 HEAD 均为 `81ab191b1f33de2371567eccd0ef9589d12483c2`。本轮改动保留为本地新增文件，范围为 `tools/pr05a/` 与 `docs/web-alpha/pr05a/`。

独立合成服务使用真实 SQLite 事务、AES-GCM 随机 DEK 和进程内合成 KEK。浏览器使用不可导出内存 CryptoKey、真实 IndexedDB、nonce 唯一约束、严格密文记录、AAD 绑定、同密文重试及不可变版本。独立页面只操作内置合成文本。

在线授权逐次核查合成 session、owner、设备、安全版本、key epoch、用途与期限。重置和撤销使旧授权失效，历史封装 key 保留，恢复通过实验专用明确确认流程。设备撤销增加 key_epoch，历史 key 需要明确选择。KEK 轮换重包及事务回滚、DEK 新旧版本兼容和丢 KEK 失败均已覆盖。

## 最终执行

最终 run ID 为 `pr05a-20261115-05`。源码归档 SHA-256 为 `323afa49047ac62657b94299a456eebe0ceeb6ecdce075d6cc2c9ea00e3e304f`。归档、源文件清单、broker 回执及测试输出保存在 `outputs/pr05a/pr05a-20261115-05/`。

实际执行命令为 `tools/pr05a/prepare-vmware.ps1 -RunId pr05a-20261115-05`，随后使用既有 broker 的 `submit.ps1 -Id pr05a-20261115-05 -Script <本轮bootstrap> -TimeoutSeconds 600`。来宾入口验证 manufacturer 为 VMware, Inc.、model 为 VMware20,1，再执行归档内 `run_experiment.py`。测试任务于 12:30:06–12:30:17 UTC 经 broker 执行，transport 成功、GuestExitCode 为 0，runner 实际测试耗时 5.595 秒。

实际环境为 Windows 11 build 26100、Python 3.12.14、cryptography 50.0.2、Node v24.19.0 和 HeadlessChrome/156.0.8078.4。最终 origin 为 `https://127.0.0.1:58972`。VMware Tools 查询为 running，vmrun IP 查询及来宾 IPv4 列表返回空值，测试仅使用来宾 loopback。

最终证据 ZIP SHA-256 为 `782ebd0edb5986949611bd499d4f0306d9e3f6a6aeedc0302df57697dc470824`，已对照来宾计算值。12:39:27–12:39:33 UTC 的清理请求 transport 成功、GuestExitCode 为 0，本包剩余 Python/Node/Chromium 进程数量为 0，测试端口剩余监听为 0。逐文件源码哈希同时与宿主冻结清单和当前文件一致。

可直接查看 [最终原始结果](../../../outputs/pr05a/pr05a-20261115-05/evidence/results.json)、[清理回执](../../../outputs/pr05a/pr05a-20261115-05/cleanup.json)、[浏览器截图](../../../outputs/pr05a/pr05a-20261115-05/evidence/experiment-ui.png) 和 [宿主静态核对](../../../outputs/pr05a/static-verification.json)。

| 检查 | 本轮实际结果 |
|---|---|
| 服务端 | 14 项，0 失败、0 错误、0 跳过 |
| 真实浏览器 | 13 个复合场景，0 失败 |
| 密钥封装与隔离 | DB 无明文 DEK，跨 owner/用途与失效会话拒绝，未信任设备录音需单独同意 |
| 内存及持久化 | 不可导出 CryptoKey，IDB 密文，localStorage/sessionStorage/Cache API 为空；锁定写入不保留输入 |
| nonce/AAD | 9 项篡改拒绝，同密文重试成功，异体版本冲突，强制 nonce 碰撞触发原生约束错误 |
| 关闭重开和换号 | 真正退出 Chromium 进程后复用 profile，重开锁定并要求在线解锁；账号密文隔离和多标签退出锁定 |
| 重置和设备撤销 | 旧 grant 失效、在线锁定、device key_epoch 推进、明确恢复历史 key 后读取原唯一稿 |
| 离线和竞态 | 离线期限到达锁定，重新连接核验撤销，迟到解锁不能恢复已锁定状态 |
| 满盘分支 | Chromium 原生配额错误和 IndexedDB 回滚、唯一稿可读、当前输入保留；SQLite 原生 SQLITE_FULL |
| 轮换 | KEK 重包后旧版本退休、事务中断回滚、并发解锁及重复 KEK 版本保护、缺失/错误 KEK、DEK 新旧版本兼容 |
| 浏览器入口 | 登录、解锁、写入、读取及锁定渲染清理实际点击通过，并留截图 |
| HTTP/TLS | no-store、缺 CSRF 与外来 Origin 拒绝，另一证书的真实浏览器负对照拒绝 |
| 宿主静态检查 | Python/Node/PowerShell 语法、新文件空白和本地文档链接、文件范围及冻结源码比对通过 |

## 失败与修复历史

| 轮次 | 保留的结果及修复 |
|---|---|
| 01 | 传输成功但来宾退出 1，直接调用共享 ps1 被现有执行策略阻止；改为既有 runner 的脚本文本执行方式 |
| 02 | 服务断言无失败但 13 项清理错误，SQLite 连接没有显式 close；另有证书负对照导航竞态。显式关闭连接，并隔离负对照标签页 |
| 03 | 13 服务测试和 13 浏览器场景通过，源码与来宾逐文件哈希、证据 ZIP 往返哈希一致，清理确认无本包进程或监听 |
| 04 | 新增严格字段、锁定写入与重复 KEK 版本保护后，14 服务测试和 13 浏览器场景通过 |
| 05 | 对齐正式设备撤销的 key_epoch 增长及历史 key 恢复选择，14 服务测试和 13 浏览器场景通过，为当前最终源码 |

另有一次证据复制使用来宾 J 盘 subst 路径返回 file not found，改用实际 `C:\j\...` 路径成功。宿主 submit 等待队列曾超时，后续读取原请求的真实回执处理，没有将等待超时等同为来宾任务已终止。

## 边界与门禁

- 浏览器证据覆盖本轮 Chromium 原生存储。物理磁盘耗尽未实测，满盘分支使用真实 Chromium 配额覆盖与 SQLite 满库。浏览器 estimate 只是估计，状态判断以实际写入结果为准。
- TLS 使用每轮服务器 SPKI pin 和不同 key 的负对照，系统证书信任链部署验证待后续。
- 合成身份尚未接正式 PR-03b Cookie/MFA/再认证与 PostgreSQL 撤销事务。明确恢复允许重新授权原合成设备，正式产品的新设备/撤销设备恢复证明仍待决定。完整按课堂录音授权和采集 runtime 随正式接入验证。
- 实验窗口为正常 30 秒、到期场景 1.2 秒，产品窗口由用户决定。浏览器托管内存不承诺物理擦除；已解锁页面 XSS、插件和受控设备属于既有威胁边界。
- 完整三池生命周期、采集、同步和业务设置页接入按后续工作包推进。当前 POC 的独立 SQLite 和合成 KEK 用于机制验证，正式 cache_keys/secret 设施见 [接入候选](INTEGRATION-CANDIDATE.md)。
- 原产品全回归、正式 PG 联调与远端 CI 本轮没有运行，本轮实际验证针对独立 POC。TBD-03、持久恢复安全门禁、Git 发布与部署分别按已有授权边界处理。

候选与建议见 [TBD-03](TBD-03-OPTIONS.md)，复验步骤见 [运行说明](RUNBOOK.md)。
