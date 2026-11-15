# PR-03b S3 BATCH 3：专用 guest 的 OS IP 出网隔离

日期：2026-11-07（America/Los_Angeles）。本批只实现、验证专用 VMware guest 的 OS IP 出网隔离。原 BATCH 2 已获[独立批准](../../../../outputs/pr03b/s3-batch2-independent-review-20261107-01/REVIEW.md)；原九源、guard、kit03、快照和证据不改。**新 guest 成绩须在用户手动执行后另行导入并审查；本地静态/合成检查不能替代真实验证。一般数据库执行权始终关闭。**

## 选择与适用边界

全部依赖已离线备齐，后续测试只需 loopback。最小实现是在专用 guest 中禁用全部原先 AdminStatus=Up 的非 loopback 适配器，含隐藏、软件、虚拟适配器；不配置宿主、hypervisor、VMware 网络、Windows 防火墙或服务。OS 中不存在可用外部 IP 路径时，Python、不同 exe、原生 libpq/psql 及系统 DNS 代理均不能借该路径出网。不能抵御同一 guest 内有权重新启用/新建设备的管理员；每项探测前后重核，未来实验仍须重新取得当时的隔离证据。

包只支持既有 `C:\Users\Administrator\PR03bLabBasic`、VMware Windows build 26100、64 位 Windows PowerShell 5.1、guest 本地管理员及一个默认 network compartment。通过 NetTCPIP 的 `-IncludeAllCompartments` 检查所有 compartment；发现其他 compartment、未映射接口、未知状态、适配器集合变化或残留非 loopback 路由直接 STOP，不自动删除路由/调整网络来通过。loopback 身份由 .NET 原生接口枚举的 software-loopback type=24、IPv4/IPv6 索引和 `127.0.0.1`/`::1` 地址交叉核对。其 multicast/broadcast 路由可保留；`NextHop=0` 不是豁免条件。

补充包固定放 `C:\Users\Administrator\PR03bNetworkIsolation`，在旧 kit 的 exact-file-set anchors 之外。旧 guest 只有 C 盘的例外沿用，不在宿主 C 盘生成任务文件。新 helper 只读调用原 `guest.py.verify()`，核原 4513 文件清单，不调用旧 prepare/selftest/verify-live，不覆盖旧收据，也不使用旧 PGDATA 或业务配置。

枚举分开使用：[MSFT_NetIPInterface.ConnectionState](https://learn.microsoft.com/en-us/windows/win32/fwp/wmi/nettcpipprov/msft-netipinterface) 的 Disconnected=0、Connected=1；NetAdapter.InterfaceAdminStatus 的 Up=1、Down=2。不得混用两个“断开/禁用”的数值。

这是 guest 当前 **IP** 出网隔离，不覆盖 VMware shared folders、clipboard、VMCI、USB 或其他非 IP 通道，不宣称通用恶意代码沙箱。没有真实 D0、认证/恢复试验、收费 API 或外部数据库目标。

## 受限探测和验收

外部控制目标固定为 Cloudflare 公共 HTTPS 地址 `1.1.1.1:443` 和 IPv6 `2606:4700:4700::1111:443`；没有 DNS、端口扫描或第三方数据库。禁用前的 IPv4 native Winsock 仅 TCP connect 后关闭，不发送应用数据，必须成功。IPv6 前置可达性单独记录；原本不通不能写成 IPv6 外部 A/B 成功。

| 门禁 | 成功要求 | 拒绝/故障处理与证据 |
|---|---|---|
| 来源/环境 | 原 kit manifest、Python、wrapper、完整 payload 与新包哈希吻合；固定 guest/权限/平台满足 | 不符时在任何禁用前 STOP；不得在宿主运行 |
| 外部正控 | 同一新 helper 的原生 IPv4 TCP connect 成功并关闭 socket | 失败/超时不能 PASS；记录原错误并保留现场 |
| 变更前回滚准备 | 新 case 中 PLAN 和 RECOVERY 独占写入；原 GUID、索引、AdminStatus 保存；每次禁用前独占写 intent | 无可持久证据则不修改；中断后不自动恢复 |
| OS 拓扑 | 全部非 loopback adapter AdminStatus=Down；原生接口没有 Up/未知态；所有 compartment 的 IP interface 不连接，ActiveStore 路由仅属于已核 loopback | 一个接口、路由、compartment 或 GUID 无法解释即 STOP；每个 probe 前后保存快照 |
| 原生 TCP/UDP 负控 | IPv4/IPv6 Winsock 立即返回明确不可达错误，与 OS 拓扑一致 | 超时、connection refused、协议错误、成功发送或连接均不是隔离通过 |
| 原生数据库客户端负控 | 同一已 pin 的 `psql.exe` 对两个固定外部地址失败，明确为网络不可达；子进程已结束 | 5 秒 worker watchdog 超时永不 PASS；只终止本 helper 捕获的 client 句柄，不操作其他进程 |
| loopback 正控 | IPv4、IPv6 独占 listener 各仅收到固定 8-byte PG SSLRequest，回复 `N` 后关闭，psql 按 require SSL 拒绝 | 仅合成传输握手；不进行 PG 认证、StartupMessage、SQL 或目标归属重跑；额外字节/线程或 client 未收尾均 STOP |
| 最终保护/交付 | 再核完整旧 payload；拓扑仍满足；结果、所有阶段、前后快照及索引打包 | 保留 disabled 状态待审；ZIP 导出失败则整体交付 STOP，不把 JSON 当可复用执行能力 |

明确不可达集合以源码为准：TCP/UDP/psql 的 IPv4 为 10050/10051/10065；IPv6 可另记录 10049（地址不可用）。这些错误仅与当次已禁用适配器/无路径事实共同支持结论，不能单凭错误文案认定隔离。IPv6 没有前置可达正控时，结论限于“当前 guest 无可用 IPv6 出网路径及原生调用拒绝”，不扩写为真实双栈外部 A/B。

psql 只接受代码内的数字 hostaddr、合成 user/db、`-X -w`、`sslmode=require`、`gssencmode=disable` 和有限 connect_timeout。使用新 case 内零字节 passfile、空 stdin 和最小环境，防止读取默认 pgpass/psqlrc 或交互输入。没有任意 SQL、数据库 URL、迁移入口或真实凭据；`\quit` 是客户端退出命令。新 helper 不放宽旧 NativeJob helper allowlist 或 shared URL guard。

## 手动入口与证据

主线程在宿主生成小型补充 ZIP，用户将其复制到 guest 桌面后，在 guest **管理员** PowerShell 粘贴交付页的完整 bootstrap。ZIP 哈希必须匹配，目的目录不得存在，解压后用独立 PowerShell 5.1 子进程执行 Apply；逐项检查原生命令退出码，不靠 unconditional Write-Host 认定通过。

Apply 只允许一次；已有 cases 则拒绝重跑。STOP 时保留窗口和目录，不覆盖/删除旧 case，不自行重试。成功后适配器继续禁用。VMware UI 可用于操作本地 guest，shared folders/clipboard 的可用性不构成 IP 联通证据。

控制台给出 CASE、EVIDENCE ZIP、SHA256 和最终 PASS/STOP。把 ZIP 原样复制回本轮交付目录的 incoming，主线程再做安全导入、原件核验和交审。**在此之前真实验证仍为 PENDING；任何检查通过都不自动开始后续机制实验。**

需要恢复网络时，使用该 case 的 RECOVERY.json 中固定命令：先独占写 REVOKED，撤销当前隔离观察效力，再仅恢复本 run 原先启用、已记录禁用 intent、当前仍 Down 且 GUID/索引匹配的适配器。不得“启用全部”。恢复后新增独立 rollback 证据 ZIP，原 PASS ZIP 保持历史含义；部分恢复失败保留 STOP，不自动反复执行。

## 原有边界与官方依据

S1/S2/BATCH 1/BATCH 2 原批准不变；D01—D03 只在设计层 CLOSED，TBD-08、既有 P3、U01/U02、条件补偿及 ACL/RLS、业务迁移、认证、Cookie/CSRF/WS、N01/N02、A01—A46/十九交错、真实 D0/恢复启用等继续保留。本批不 commit、Push、合并或部署，不读/使用 VMware 认证密文，不排查 VIX。

- [Disable-NetAdapter](https://learn.microsoft.com/en-us/powershell/module/netadapter/disable-netadapter)：禁用适配器，支持 IncludeHidden/InputObject。
- [Get-NetIPInterface](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipinterface) 与 [Get-NetRoute](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netroute)：all-compartment 枚举边界。
- [Winsock 错误码](https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-error-codes-2)：明确不可达和超时/拒绝连接的区别。
- [psql 17](https://www.postgresql.org/docs/17/app-psql.html) 与 [libpq 17](https://www.postgresql.org/docs/17/libpq-connect.html)：客户端开关、连接参数及传输行为。
