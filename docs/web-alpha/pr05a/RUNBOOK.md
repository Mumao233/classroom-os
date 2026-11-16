# PR-05a 实验运行说明

所有运行测试在 VMware Windows 来宾执行。宿主只准备源码、执行静态解析、排队和回收证据。复用 PR-03b 的 VMware CLI 与已安全输入凭据的 broker。

## 宿主准备

在本包工作树使用 PowerShell 7，给每次执行选择全新 run ID。

```powershell
& 'J:\class-assistant\worktrees\web-pr05a-cache-security\tools\pr05a\prepare-vmware.ps1' -RunId pr05a-YYYYMMDD-NN
```

脚本将当前 `tools/pr05a/` 文件打包到既有 broker 的 ignored scripts 目录，生成独立 bootstrap，并在本包 `outputs/pr05a/<run-id>/source-receipt.json` 记录源码和归档哈希。它读取 broker 的真实状态与只读共享信息。凭据继续只在已有 broker 的内存中。

按脚本输出调用既有 `tools/pr03b/vmware/submit.ps1`，传入同一 ID、bootstrap 和 `-TimeoutSeconds 600`。共享队列按序执行。先保留一次提交的请求与回执，调用方等待超时后查询原请求及来宾状态。

## 来宾执行

`guest-run.ps1` 验证 VMware manufacturer/model，将归档复制到来宾独立 run 目录并校验 SHA-256。源码和 profile 使用独立目录，服务端 bind 0 分配 loopback TLS 端口。脚本复用来宾已准备的 Python、cryptography、Node、Playwright 和 Chromium，完整记录实际版本。

来宾现有 J 盘路径通过 `subst` 映射到来宾 `C:\j`，沿用已有测试布局。宿主任务文件位于实际 J 盘，来宾任务文件位于既有来宾磁盘的该映射内。该实验使用现有磁盘布局。

Python runner 执行真实 SQLite/AES-GCM 故障与并发用例，然后启动两个不同证书的 TLS listener。Chromium 对当前实验服务器 SPKI 单独放行，另一个 listener 用作证书负对照。浏览器测试使用原生 Web Crypto、IndexedDB、BroadcastChannel、持久 profile、离线开关及存储清理。

`Storage.overrideQuotaForOrigin` 限制实验 origin 为 1,048,576 bytes，写入高熵合成数据，要求观察原生 `QuotaExceededError`、失败记录缺失、既有唯一稿可读和当前输入仍在内存。SQLite 使用 `max_page_count` 触发原生 `SQLITE_FULL`。物理磁盘耗尽与其他浏览器版本的差异仍需另有真实证据。

实验恢复接口 `confirm-recovery` 和历史 key 选择属于本包合成协议。正式产品沿用冻结 `/me/cache-unlock`，恢复证明、参数及契约差异交协调聊天和安全评审。

## 证据回收

来宾输出 `results.json`、`service-tests.log`、`browser-tests.log`、`browser-results.json` 和实验页截图，打包为本轮 `evidence.zip`。用同一 broker 的 `copyFrom` 操作复制该归档，再回收到本包 `outputs/pr05a/<run-id>/`。原始源码包、失败回执和失败轮次保留。

vmrun 文件复制使用来宾物理路径 `C:\j\class-assistant\worktrees\web-pr05a-cache-security\work\pr05a\<run-id>\evidence.zip`。来宾脚本内的 J 盘 subst 映射对 vmrun 的复制进程不可见，本轮使用 J 路径复制曾返回 file not found，改用上述实际路径后成功。

对照宿主 source receipt 与来宾 `source_sha256`，检查 transport、guest exit、service/browser 结果及进程关闭。最终报告列明实际运行项、失败、跳过和限制。作者测试结果等待独立安全评审，TBD-03 等产品选择由用户决定。
