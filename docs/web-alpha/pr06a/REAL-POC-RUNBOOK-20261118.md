# PR-06a 真实接口最小 POC 执行清单

负责人林珈诚。工作包 PR-06a，Draft PR #19。专项费用策略 v1 对应 [授权原件](PR06A-REAL-POC-AUTH-20261118.md) 和[费用契约](../architecture/06-COST-OPERATIONS.md)。等用户明确说「开始真实调用」后才允许置为可执行。执行候选必须和 VMware 完整回归及 Astra 独审回执指向同一个 Git SHA。

## 执行位置与身份

- 在 Windows 11 VMware 来宾内使用独立 POC 执行账户、干净的已复审检出和 Python 3.12。由操作员将三份登记 WAV 从宿主的 `J:\class-assistant\Recording` 复制到来宾私有目录。建议为来宾 POC 单独配置持久 PostgreSQL 实例，使用唯一权威库 `classroom_pr06a_real_poc`。开发、CI、VMware 回归数据库、普通 worker 和生产数据均在各自环境运行。
- 在阿里云控制台或受信密钥记录核对实际账号标识、北京 Workspace `ws-bm2ftvz01aej1eof`、Key ID `8245225`、模型 `qwen-audio-3.1-asr-flash-streaming`、权限和费用预警。保留可审计的账号核对记录引用。由核对者把该 Key 通过安全输入注入来宾当前操作进程的 `DASHSCOPE_API_KEY`。授权记录中的 Key ID 无法从 Key 值推导，严禁试连核验归属。
- 先正常安装后端锁定依赖 `web/backend/requirements.lock`。真实驱动固定用 `websockets==17.4`、北京 Workspace 地址、TLS 主机校验和 Bearer 握手。整个客户端禁代理、重定向和自动重连。

## 离线预检

在来宾已复审检出根目录运行。此命令只读授权原件和样本，输出摘要、样本数和发送前缀。

```powershell
python -I -B tools/pr06a/real_poc.py --samples $pocSamples
```

| 文件 | WAV 样本数 | 原始 PCM SHA-256 | 唯一允许发出的样本范围 |
|---|---:|---|---|
| `1_mandarin.wav` | 69,888 | `f8022f15370f16cb6752bd1c8edef9885261c78866312c587f9dd512404ea5c0` | [0, 69888) |
| `2_mixed.wav` | 82,176 | `2d2a37fbbf63e32d0245e1d996c2fe121d3c23b6674ad5e58cc18b17ab243db2` | [0, 82176) |
| `3_long.wav` | 137,472 | `a27033e0bec1c74ff49d3e81259234733301404d3737008cc1983858713a1a37` | [0, 32000)，SHA-256 `e8e7098ccede40dfd05964a6e3ee6b3b54bfddb66bfa531a0e13c2f8c6684c27` |

实际计划发送 184,064 个采样，11.504 秒。授权原件按原始 CRLF 字节保留，SHA-256 为 `233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777`。

## 配置独立 PG

1. DBA 确认唯一权威库及其持久存储。用同一检出的 Web Alembic head 建立干净 schema，再事务安装 `web/backend/classroom_web/infrastructure/billing_schema.sql` 和 `web/backend/classroom_web/pr06a_real_poc/install.sql`。例如从检出根目录使用 DBA 的安全连接配置执行：

   ```powershell
   psql -X --dbname classroom_pr06a_real_poc --set ON_ERROR_STOP=1 --single-transaction `
     --file web/backend/classroom_web/infrastructure/billing_schema.sql `
     --file web/backend/classroom_web/pr06a_real_poc/install.sql
   ```

2. 在这个库撤销 PUBLIC 的 CONNECT，分别建立专用 `NOSUPERUSER` 登录并授予 `classroom_pr06a_executor`、`classroom_pr06a_reconciler`。Web、worker、浏览器、本项目的其他数据库用户无权进入。控制角色仅由 DBA 持有。执行登录无底表权限，复核登录只有对账和确认释放两项函数权限。以 DBA 为该库将 `log_parameter_max_length` 和 `log_parameter_max_length_on_error` 均设为 0，在新连接中生效。保持转录和账户证明访问受控。
3. DBA 按既有 `billing_api.open_period` 签开北京时间当月的隔离 ASR 预算周期，保留预算池、安全余量及固定费用。检查 `billing`、`asr_streaming` 为 false，`pr06a_control.activation` 和 `pr06a_control.batch` 均为空。设置来宾安全连接参数。仅控制进程使用 `PR06A_CONTROL_DSN`，执行进程使用 `PR06A_EXECUTOR_DSN`，两者须使用 `host=127.0.0.1 dbname=classroom_pr06a_real_poc` 和已授权的专用登录。

## 明确启动授权后

1. 操作员在控制会话核对用户本次「开始真实调用」记录、Astra `status.json` 和 VMware `digest.json` 的 SHA-256、与 VMware 回执放在同一目录的 `host-SOURCE.json`、干净候选 HEAD、账号证明和费用基线。以安全输入在来宾当前进程设置 `DASHSCOPE_API_KEY`。例如 `[System.Net.NetworkCredential]::new('', (Read-Host -AsSecureString)).Password` 的结果直接赋给环境变量，勿打印。运行一次预授权登记，按提示输入用户已给出的启动词。此步骤写入 PG 并绑定候选、账号证明、Key 指纹和专用执行登录。

   ```powershell
   python -I -B tools/pr06a/real_poc.py --samples $pocSamples --arm `
     --account-ref $accountAttestationRef --approval-ref $startAuthorizationRef `
     --review $astraStatusPath --review-sha256 $astraStatusSha256 `
     --vmware-receipt $vmwareDigestPath --vmware-sha256 $vmwareDigestSha256 --executor-login $pocExecutorLogin
   ```

2. 关闭控制会话，在专用执行账户的来宾会话向当前进程安全注入同一个 Key。使用本次已核对的账号证明引用。运行一次，按提示输入启动词。执行后清除该会话中的环境变量和执行 DSN。任何失败都按下节停止。保留原数据库与审批记录，即使进程在 COMMIT 回执之前退出。

   ```powershell
   python -I -B tools/pr06a/real_poc.py --samples $pocSamples --execute --account-ref $accountAttestationRef
   ```

## 停止与产物

- 单批并发 1，最多 3 次连接，失败握手也占名额。每连接最多 10 秒，整批最多 30 秒。每段样本只允许一个 attempt。每连接最多 160,000 个采样，整批最多 480,000。任一异常、PG 不可用、提交结果不明、费用达到 ¥1、授权过期或撤销立即停止后续连接和新 PCM。费用 ¥1 是本地单批风险预留。最终账单按实核对。被动接收晚到的尾句和用量仍受连接截止时间约束。
- 紧急停止由控制账户设 `pr06a_control.activation.revoked = true`。撤销永久阻断再入场。事故或超时保留原批和 intent，禁止重新初始化库、换操作号或重送。停止命令本身不可作为未收费证据。
- CLI 输出脱敏逐例状态、停止码与退出码。私有 PG 保存批次与许可、WAV/PCM/帧范围摘要、握手与协议事件、保留的脱敏供应商响应、识别与计费收据、累计用量及账单。引用未确认帧的识别候选仍待核对。使用只读审计导出留存候选 SHA、依赖锁摘要、UTC/北京时间、实际命令、结果哈希、运行退出码和控制台窗口核对结果。转录人工比对在私有 PG 结果内完成。
- 复核角色仅依据已确认归属的控制台凭证调用 `pr06a_api.settle`。入参为 attempt、连续修订号、已核实 request ID、输入和输出 Token、原价及账单净额微元、供应商账期、凭证摘要、复核使用的计量修订号和是否有凭证证明用量差异已解决。若有更新观测、修订不符或差异待核对，维持敞口并保留已入账事实。失败连接仅在有核实的零费用证据时可提交零额凭证。归属不明的费用继续保留敞口。批次已关闭或已过执行截止时间，且所有已消费的连接均按最新用量修订完成复核后才调用 `pr06a_api.release_reconciled()` 释放剩余占用。保留迟到超额及原周期，核对 `bill_evidence` 与授权额的差额并写入验收结果。
