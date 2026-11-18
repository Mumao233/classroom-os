# PR-06a 真实接口最小 POC 执行清单

负责人林珈诚。工作包 PR-06a，Draft PR #19。专项费用策略 v1 对应[第一批授权原件](PR06A-REAL-POC-AUTH-20261118.md)、[第二批授权原件](PR06A-REAL-POC-AUTH-20261119-B2.md)和[费用契约](../architecture/06-COST-OPERATIONS.md)。等用户明确说「开始真实调用」后才允许置为可执行。执行候选必须和 VMware 完整回归及 Astra 独审回执指向同一个 Git SHA。

## 执行位置与身份

- 在 Windows 11 VMware 来宾内使用独立 POC 执行账户、干净的已复审检出和 Python 3.12。由操作员将三份登记 WAV 从宿主的 `J:\class-assistant\Recording` 复制到来宾私有目录。建议为来宾 POC 单独配置持久 PostgreSQL 实例，使用唯一权威库 `classroom_pr06a_real_poc`。开发、CI、VMware 回归数据库、普通 worker 和生产数据均在各自环境运行。
- 在阿里云控制台或受信密钥记录核对实际账号标识、北京 Workspace `ws-bm2ftvz01aej1eof`、Key ID `8245225`、模型 `qwen-audio-3.1-asr-flash-streaming`、权限和费用预警。保留可审计的账号核对记录引用。由核对者把该 Key 通过安全输入注入来宾当前操作进程的 `DASHSCOPE_API_KEY`。授权记录中的 Key ID 无法从 Key 值推导，严禁试连核验归属。
- 先正常安装后端锁定依赖 `web/backend/requirements.lock`。真实驱动固定用 `websockets==17.4`、北京 Workspace 地址、TLS 主机校验和 Bearer 握手。整个客户端禁代理、重定向和自动重连。

## 离线预检

在来宾已复审检出根目录运行。为本次执行指定授权 ID，下方以第二批为例。同一 ID 用于预检、控制和执行。预检只读该 ID 对应的原件和样本，输出摘要、样本数和发送前缀。

```powershell
$authorizationId = 'PR06A-REAL-POC-AUTH-20261119-B2'
python -I -B tools/pr06a/real_poc.py --samples $pocSamples --authorization-id $authorizationId
```

| 文件 | WAV 样本数 | 原始 PCM SHA-256 | 唯一允许发出的样本范围 |
|---|---:|---|---|
| `1_mandarin.wav` | 69,888 | `f8022f15370f16cb6752bd1c8edef9885261c78866312c587f9dd512404ea5c0` | [0, 69888) |
| `2_mixed.wav` | 82,176 | `2d2a37fbbf63e32d0245e1d996c2fe121d3c23b6674ad5e58cc18b17ab243db2` | [0, 82176) |
| `3_long.wav` | 137,472 | `a27033e0bec1c74ff49d3e81259234733301404d3737008cc1983858713a1a37` | [0, 32000)，SHA-256 `e8e7098ccede40dfd05964a6e3ee6b3b54bfddb66bfa531a0e13c2f8c6684c27` |

每批最多发送 184,064 个采样，11.504 秒。第一批 SHA-256 为 `233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777`。第二批 SHA-256 为 `9a56d2ea65799d821b19120a080b00d51689bc9644dce013f04213e4169d8047`。两份授权原件均按原始字节保留。按第二批授权，执行前在来宾确认网卡 Up，并在无 Key 情况下检查 DNS 与 443 端口。POC 结束后由操作员恢复网络隔离。

## 配置独立 PG

1. DBA 确认唯一权威库及其持久存储。首次安装时，用同一检出的 Web Alembic head 建立干净 schema，再由 DBA 的安全连接配置执行下列事务。已有第一批的库走后面的增量迁移。

   ```powershell
   psql -X --dbname classroom_pr06a_real_poc --set ON_ERROR_STOP=1 --single-transaction `
     --file web/backend/classroom_web/infrastructure/billing_schema.sql `
     --file web/backend/classroom_web/pr06a_real_poc/install.sql `
     --file web/backend/classroom_web/pr06a_real_poc/authorization_batches.sql
   ```

2. 在这个库撤销 PUBLIC 的 CONNECT。分别建立专用 `NOSUPERUSER` 执行和复核登录，授予本库 `CONNECT` 及对应的 `classroom_pr06a_executor` 或 `classroom_pr06a_reconciler` 角色。执行登录无底表权限，复核登录只有对账和确认释放两项函数权限。Web、worker、浏览器、本项目的其他数据库用户无权进入。

   控制登录仅由 DBA 持有。首次安装使用独立控制登录时，仅为该登录授予本库 `CONNECT`、`pr06a_api` 和 `pr06a_control` 的 `USAGE`、`pr06a_control.activation` 的 `INSERT`、`pr06a_control.revocation` 的 `SELECT`（由激活触发器使用）及 `pr06a_api.revoke(text)` 的 `EXECUTE`。按控制连接的实际生效角色确认这些权限，紧急撤销使用同一控制角色。核对执行和复核主体及其可切换的角色均无撤销权限。核对范围包括本库中已改为 `NOLOGIN` 的主体尚未断开的会话。

   以 DBA 为该库将 `log_parameter_max_length` 和 `log_parameter_max_length_on_error` 均设为 0，把 `log_error_verbosity` 设为 `terse`，在新连接中生效。保持转录和账户证明访问受控。
3. DBA 查验北京时间当月的 `billing_control.platform_budgets`。已有预算行时复用该行，核对 ASR 池、安全余量、固定费用及累积敞口能容纳本批独立 ¥1。第二批与第一批同月时，预算核对包含第一批的原预留。仅当当月无预算行时，才用 `billing_api.open_period` 签开该月周期。检查 `billing`、`asr_streaming` 为 false，所选授权 ID 在 `pr06a_control.activation`、`pr06a_control.batch` 和 `pr06a_control.revocation` 中均无行。第二批还需核实第一批已关闭且原始预留仍在原账期。设置来宾安全连接参数。仅控制进程使用 `PR06A_CONTROL_DSN`，执行进程使用 `PR06A_EXECUTOR_DSN`，两者须使用 `host=127.0.0.1 dbname=classroom_pr06a_real_poc` 和已授权的专用登录。

若专用库已经安装覆盖层，以下增列仅适用于安装证据可证明与 `05559351` 覆盖层一致的数据库。核对实际安装源。该提交 Git 源树中 `install.sql` 的 SHA-256 为 `e9487b357965e30d4264ffcaa322d2b0ecaed1fc15c6d0cf9d35e3e451d9e3f0`。安装版本更早、无法确认或数据库有未核实变更时，停在此步，先完成经验证且保留既有数据的迁移。对确认无误的原库由 DBA 事务执行以下增列。原激活行的风险摘要为空，保留既有授权、激活、撤销和批次记录。

```sql
DO $upgrade$
BEGIN
  IF pg_catalog.to_regprocedure('pr06a_api.retain_packet(uuid,uuid,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'PR06A_OVERLAY_UPGRADE_BLOCKED';
  END IF;
  ALTER TABLE pr06a_control.activation
    ADD COLUMN IF NOT EXISTS risk_acceptance_sha256 text
    CHECK (risk_acceptance_sha256 ~ '^[a-f0-9]{64}$');
END $upgrade$;
```

已有第一批的库在原专用库升级。以安装凭证确认旧覆盖层与 `adf9f79` 一致并含风险接受审计列，确认第一批已关闭，保留其原行和预留。DBA 在同一原库事务执行下列增量 SQL。迁移在锁有争用、发现开放批次或缺少透传审计函数时拒绝，旧版本或安装来源不明的库先核实。迁移后核对第一批激活、尝试、账单及预留未变化。各替换函数保留原有所有者，撤销函数及两个内部校验函数也保留原所有者直接授予的执行权。迁移为原撤销和建批函数的所有者校验或补授新增查询所需的列级读取权限。如旧函数存在转授或必要列权限无法补授，迁移拒绝并回滚，由 DBA 核对原授权。按第 2 步核对实际登录。单个授权 ID 只建一批，第二批另计 ¥1，合计本地预留最多 ¥2。

```powershell
psql -X --dbname classroom_pr06a_real_poc --set ON_ERROR_STOP=1 --single-transaction `
  --file web/backend/classroom_web/pr06a_real_poc/authorization_batches.sql
```

## 明确启动授权后

1. 操作员在控制会话核对用户本次「开始真实调用」记录、受信 Astra `status.json` 和 VMware `digest.json` 的 SHA-256。独审 `review.md` 须与 `status.json` 同目录。VMware 回执与 `host-SOURCE.json` 同目录。两份回执均绑定干净候选 HEAD。核对账号证明和费用基线。以安全输入在来宾当前进程设置 `DASHSCOPE_API_KEY`。例如 `[System.Net.NetworkCredential]::new('', (Read-Host -AsSecureString)).Password` 的结果直接赋给环境变量，勿打印。运行一次预授权登记，按提示输入用户已给出的启动词。此步骤写入 PG 并绑定候选、账号证明、Key 指纹和专用执行登录。

   ```powershell
   python -I -B tools/pr06a/real_poc.py --samples $pocSamples --authorization-id $authorizationId --arm `
     --account-ref $accountAttestationRef --approval-ref $startAuthorizationRef `
     --review $astraStatusPath --review-sha256 $astraStatusSha256 `
     --vmware-receipt $vmwareDigestPath --vmware-sha256 $vmwareDigestSha256 --executor-login $pocExecutorLogin
   ```

   上述命令适用于整包 Astra `PASS` 且零 P0/P1。使用已登记风险接受路径时，向同一命令增加 `--risk-acceptance-sha256 $riskAcceptanceSha256`。取 [风险接受记录](risk-acceptance/REPORT.md) 的固定 SHA-256 `c8f6eb43c3ec7d041bb8a63a847d70f7175e45e446528b89fc68ec607078451c`。Astra 必须对当前候选基于 `05559351` 的门禁改动给出 `PASS`，并提交同摘要的 `RISK-ACCEPTANCE` 确认行和原样列出的四项已接受发现。旧的整包 `BLOCK` 回执作为历史证据。新发现、候选 SHA 不符、报告摘要或发现内容不符立即拒绝。通过时在 `pr06a_control.activation.risk_acceptance_sha256` 与脱敏启动回执中保留风险记录摘要。

2. 关闭控制会话，在专用执行账户的来宾会话向当前进程安全注入同一个 Key。使用本次已核对的账号证明引用。运行一次，按提示输入启动词。执行后清除该会话中的环境变量和执行 DSN。任何失败都按下节停止。保留原数据库与审批记录，即使进程在 COMMIT 回执之前退出。

   ```powershell
   python -I -B tools/pr06a/real_poc.py --samples $pocSamples --authorization-id $authorizationId --execute --account-ref $accountAttestationRef
   ```

## 停止与产物

- 单批并发 1，最多 3 次连接，失败握手也占名额。每连接最多 10 秒，整批最多 30 秒。每段样本只允许一个 attempt。每连接最多 160,000 个采样，整批最多 480,000。任一异常、PG 不可用、提交结果不明、费用达到 ¥1、授权过期或撤销立即停止后续连接和新 PCM。费用 ¥1 是本地单批风险预留。最终账单按实核对。被动接收晚到的尾句和用量仍受连接截止时间约束。
- 紧急停止由控制账户调用 `pr06a_api.revoke(text)`，显式传入要撤销的授权 ID。PG 在正在交付的单次写入离开后提交撤销，撤销生效后永久阻断该批新的握手、run-task 和帧。事故或超时保留原批和 intent，禁止重新初始化库、换操作号或重送。停止命令本身不可作为未收费证据。
- CLI 输出脱敏逐例状态、停止码与退出码。私有 PG 保存批次与许可、WAV/PCM/帧范围摘要、握手与协议事件、保留的脱敏供应商响应、识别与计费收据、累计用量及账单。引用未确认帧的识别候选仍待核对。使用只读审计导出留存候选 SHA、依赖锁摘要、UTC/北京时间、实际命令、结果哈希、运行退出码和控制台窗口核对结果。转录人工比对在私有 PG 结果内完成。
- 复核角色仅依据已确认归属的控制台凭证调用 `pr06a_api.settle`。入参为 attempt、连续修订号、已核实 request ID、输入和输出 Token、原价及账单净额微元、供应商账期、凭证摘要、复核使用的计量修订号和是否有凭证证明用量差异已解决。若有更新观测、修订不符或差异待核对，维持敞口并保留已入账事实。如请求 ID 因包含凭据而被隔离，保持 request ID 参数为空，只额外提供从可信账单凭证核对得到的 SHA-256 指纹。失败连接仅在有核实的零费用证据时可提交零额凭证。归属不明的费用继续保留敞口。批次已关闭或已过执行截止时间，且所有已消费的连接均按最新用量修订完成复核后才调用 `pr06a_api.release_reconciled(text)`，传入已核清的授权 ID 释放该批剩余占用。保留迟到超额及原周期，核对 `bill_evidence` 与授权额的差额并写入验收结果。
