# PR-05a 正式身份与 PostgreSQL 接入增量

负责人为林珈诚，日期为 2026-11-15（America/Los_Angeles）。本轮依据独立审核后的修改授权，交付默认关闭的正式接入候选及其定向验证。整包安全放行、产品窗口、正式迁移和公开契约审定继续按原门禁处理。

## 增量复审通过登记

2026-11-15 17:43（America/Los_Angeles，核验记录时间为 2026-11-16T01:43:26Z），协调端独立复审通过本轮默认关闭的正式身份/PG 接入候选增量，未发现新增明确阻断缺陷。审核范围为 16 文件增量（12 新增、4 修改）及直接影响，650 个基线文件散列一致，13 个可执行增量文件与最终测试源码 ZIP 逐字节一致。

协调端重新核验源码与证据 ZIP、原始 JUnit 27 项、真实浏览器结果及运行/清理回执，确认零失败、零错误、零跳过，GuestExitCode 与 PG stop 为 0，残留进程为 0。此次通过限定默认关闭的候选。TBD-03、正式 revision/公开契约、trustDevice、恢复证明和录音 consent port 继续按下文待决/待接入项处理。

独立结论见 [主仓审核报告](../../../../../classroom-os/docs/web-alpha/evidence/INDEPENDENT-REVIEW-20261115-04A-05A-11A.md)，机器核验入口为 [05a-formal-recheck.json](../../../../../classroom-os/outputs/independent-review-20261115/05a-formal-recheck.json)。本包在 `outputs/pr05a/formal/review-registration-20261115/` 保留该核验副本、登记前已审核交接原文与登记后散列回执。本次登记仅追加此节，功能代码和已审核增量归档保持其原散列。

## 审查基线与本轮范围

独立审核基线为主仓 `outputs/independent-review-20261115/05a-snapshot.json`，记录于 2026-11-15T21:23:31Z，HEAD 为 `81ab191b1f33de2371567eccd0ef9589d12483c2`。该快照包含未提交交付，以逐文件散列标识。本工作树继续使用 `feature/web-pr05a-cache-security`，HEAD 保持该基线，原有未提交文件保留。

审核结论是在原独立 POC 范围内没有新增明确阻断缺陷。本轮落实正式接入待办。原 POC 程序、原 ACCEPTANCE 和已审核证据作为基线保留，README 与接入候选仅追加本轮入口。

准确增量由 `tools/pr05a/formal/review_delta.py` 对照独审散列生成，输出到 `outputs/pr05a/formal/review-delta/delta.json` 和 `delta.patch`。每项包括修改前后 SHA-256 和增删行区间。下次复审限该增量及其直接调用链、候选契约、DDL 和定向回归。扩大范围时需记录本次改动引起的具体影响或新失败。

## 修改文件、关键行及行为

下表行号对应当前源文件，完整增量行区间以 delta.json 为准。

| 文件及关键行 | 修改前行为 | 本轮行为与直接影响 |
|---|---|---|
| `web/backend/classroom_web/application/cache_keys.py` 第 24、34 行 | 独立实验使用合成登录与恢复 | 正式服务调用已有 AuthService 的 CSRF 和 Principal 校验，精确绑定当前设备、用途、body、operation key 和真实再认证证明。服务 enabled、历史恢复 allow_recovery 均默认 false |
| `web/backend/classroom_web/infrastructure/cache_key_repository.py` 第 17、49、71 行 | POC 使用 SQLite | 复用 Database.authorized_transaction，消费同事务单次证明，读取/竞争创建 owner-scoped cache_keys。PG 或解封失败回滚证明消费，提交不明返回 RESULT_UNKNOWN，不返回秘密 |
| `web/backend/classroom_web/infrastructure/cache_key_crypto.py` 第 18、39 行 | 实验封装格式仅供 POC | 版本化 61-byte 二进制封装，AAD 绑定 key ID、owner、device、存储用途、课堂和 key_version，复用现有 AES-GCM 依赖；KEK 与 MFA 密钥分离注入 |
| `web/backend/classroom_web/entrypoints/http.py` 第 70、111、154 行 | 无缓存服务注入 | 新增可选 cache 注入和现有 cache-unlock 注册，继续使用现有 JSON/schema、Origin、CSRF、入口预算、错误和 no-store 响应。历史恢复路由还需显式候选契约和服务开关 |
| `web/backend/classroom_web/infrastructure/database.py` 第 63 行 | 运行角色安全检查未列 cache_keys | 将新表加入现有非表 owner 检查，现有调用签名保持兼容 |
| `tools/pr05a/formal/cache_candidate.sql` 第 3、39、60 行 | 正式 DB 无 cache_keys，设备可信约束关闭 | 新增冻结模型的 cache_keys、空课堂部分唯一索引、复合外键和 FORCE RLS；配置表默认关闭且无产品参数；固定 public 授权函数在当前账号/会话/设备/epoch 下原子消费现有 proof |
| 同一 SQL 第 26、30、55、58 行 | 运行角色无新表能力 | runtime 仅可 SELECT、INSERT 及用于行锁的 UPDATE(id)，trigger 禁止改 key ID。策略读取角色仅获固定 singleton 列的行锁权限。移除 NOT trusted 约束只是候选 DDL，公开 trustDevice 仍关闭 |
| `tools/pr05a/formal/candidate.py` 第 7、26 行及 fixtures.json | unlockCache 无再认证用途映射和历史恢复 DTO | 对只读的 0007 issue_reauth 插入两个候选用途，复用原密码/TOTP 验证；生成标记为 x-pr05a-candidate 的内存契约 overlay 和正反 fixtures。公开冻结文件没有写入 |
| `web/backend/tests/integration/test_cache_keys.py` 第 96 起 | 原测试覆盖独立合成服务 | 新增 22 个实际收集用例，覆盖真实 Cookie/再认证/学生及管理员 MFA、撤销、重置、跨 owner/设备、历史 key、PG/crypto 回滚、权限、课堂删除与原生 TLS 浏览器 |
| `tools/pr05a/formal/run_tests.py`、prepare.py、browser.mjs | 原 POC runner 不执行正式应用 | 源码归档后在 VMware 内启动独立 PG 集群，仅执行新增用例及 5 项直接身份回归，并回收 JUnit、浏览器结果和停止回执 |

`auth_repository.py` 的现有固定接口已经足够，本轮未修改。授权直接复用现有 reauth_proofs，没有新增 grant 表。密码重置、改密及全部退出通过原 revoke_derived 消费证明并撤会话，设备撤销通过原设备/session 状态及 key_epoch 阻断后续释放。历史迁移、共享依赖锁、前端客户端与设置页保持原归属。

## 候选 DDL、契约及录音接缝

cache_keys 沿用冻结模型的列名、用途 learning/draft/recording、owner/device/class_session 复合约束和 key_version。API scope drafts/recording_recovery 在服务内显式映射。现有 nullable class_session_id 的唯一性由部分索引补齐。运行角色不能覆盖封装或重命名 key。

新增 `identity_control.cache_policy` 只承担受限配置，初始 enabled/history_enabled=false，窗口和 revision=null。合成测试显式设置 revision=synthetic-candidate-only、key 窗口 30 秒、再认证窗口 120 秒。该配置只验证候选策略，不是产品默认或批准记录。原 proof 上限仍限制学生 600 秒、管理员 300 秒，实际 key 期限还与 proof 和 session 期限取最小值。选择更长的产品离线窗口需要另审生命周期方案。

`public.classroom_web_authorize_cache_key(jsonb)` 与原 public session 检查保持同类运行角色边界，未向 runtime 开放 identity_api schema。该函数先锁账号，再检查设备、会话、策略及 proof。`issue_reauth` 的新增分支由只读源文件生成，既有用途分支和原权限保持，变更内容及生成后的 SQL 可在本轮 review-delta 目录核对。正式 Alembic 编号由协调聊天结合 04a 的 0009 与 11a 链安排。

当前 `/me/cache-unlock` 请求和响应结构沿用既有 DTO，新增 exact-purpose 再认证候选。候选 `/me/cache-recover` 增加 key_id 请求与 key_version 响应，key_epoch 表示当前设备授权版本，key_version 表示历史 DEK 版本。该路由只在显式候选契约和历史恢复开关下注册。契约版本、changelog、前端类型与消费者由 Astra/协调流程审定后接入，当前 overlay 不是已发布版本。

录音的连接位置为 CacheKeyService.release 内 recording_authorizer.authorize(connection, principal, classroom)。同一事务先使用现有 catalog 检查 owner、祖先和墓碑。授权 port 默认缺失并返回 SERVICE_DISABLED。PR-05b 与 consent/privacy owning 流程需提供当前设备、该课堂、本次录音的独立同意、撤销和期限校验，并持有对应祖先与 consent 锁至提交。05a 负责释放边界和窗口，05b 负责采集、录音授权与恢复 runtime。只有此 port 的正式实现与契约批准后才能验证并启用真实录音 key 释放。

## 本轮实际验证

用户在本机重新启动 broker 后，查询确认 Tools=running、只读共享就绪，IP 查询为空。运行环境为既有 VMware Windows 来宾，服务和 PG 仅使用 loopback。

最终请求为 `pr05a-formal-20261115-10`，broker 执行窗口为 2026-11-16T01:28:44Z–01:29:19Z，对应用户时区 2026-11-15。源码 665 文件，SHA-256 为 `49a7030445b09652805de0c9de0992a5f08e800e4ca81c3bd20d458f19e69af8`。测试后只补充交接文档，功能源码散列与该快照一致。

实际宿主操作如下。

```powershell
python -B tools/pr05a/formal/prepare.py --run-id pr05a-formal-20261115-10
& 'J:\class-assistant\worktrees\web-pr03b-sessions\tools\pr03b\vmware\submit.ps1' -Id pr05a-formal-20261115-10 -Script 'J:\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\vmware-broker\scripts\pr05a-formal-20261115-10.ps1' -TimeoutSeconds 600
```

来宾 run_tests.py 的完整 pytest/PG 命令在 `outputs/pr05a/formal/pr05a-formal-20261115-10/evidence/run.json`。实际收集 27 项，零失败、零错误、零跳过，耗时 23.94 秒。其中本轮新增 22 项，既有直接身份回归 5 项。日志保留一个现有 Starlette/httpx 弃用警告，依赖锁未改。

原生 Chromium 156.0.8078.4 使用真实 TLS、Cookie、现有密码再认证、Web Crypto 和 IndexedDB，验证当前设备 key 解锁及真实设备撤销后旧 Cookie/证明不能再解锁。其他定向用例使用同一正式服务和真实 PG，通过 HTTPS TestClient 验证请求流程。TLS 使用本轮 SPKI pin，系统信任链沿用原未验证边界。

最终 evidence.zip SHA-256 为 `30ab05d03ac1fedbfc8780236b0448662ed1f317d0d12daa2087c0178a9a5ab8`，已对照来宾值。源 ZIP 也与来宾值一致。清理请求 transport 成功、GuestExitCode=0，测试与 owned PG stop 均为 0，本包残留 Python/Node/Chromium/PostgreSQL 进程为 0。

前期失败原样保留在各 run 目录。修复包括原生 SQL 参数传递、行锁列权限、固定 public 函数调用边界、撤销后旧 Cookie 重放的测试上下文、独立 reset 客户端生命周期、第二设备的新登录上下文，以及删除场景复用既有 tombstone 入口。08 曾通过 26 项；09 的新增课堂删除测试因缺 owner context 失败，改用正常 repository 入口后 10 的 27 项全部通过。最终结果以 10 为准。

## 待决定与未验证

| 项目 | 具体影响及当前处理 |
|---|---|
| TBD-03 解锁/离线窗口 | 产品值未定，默认服务和 DB 策略关闭。合成 30/120 秒配置不作为产品批准 |
| 历史恢复证明与新设备 | 当前只验证仍有效的同账号同设备历史 key。PR-03b 新登录生成新 device，重置后重登及另一设备不能借此恢复旧 device 的 key。跨设备重绑定、额外核验和留存期限待用户决定 |
| 设备信任入口 | 候选 DDL 支持 trusted=true，合成 fixture 显式配置；正式 trustDevice、同意版本与设置交互继续关闭并待对应接入 |
| 录音课堂授权 | 默认 port 缺失保持关闭，已验证 owner/墓碑和关闭行为；05b/consent 实现与真实采集链未验证 |
| 组合迁移和正式契约 | 候选 DDL 已在真实独立 PG 上装载，正式 revision、04a/11a 组合迁移及契约版本审定待协调 |
| 前端与真实启用 | 公共客户端、设置页和恢复交互由 Claude owning 流程接入，当前未启用私人持久恢复 |
| 其他验证 | 原独立 POC 和整包测试未重复运行。物理盘耗尽、全产品回归、远端 CI、生产与真实私人数据不在本轮新增通过结论内 |

本轮复审入口为本文件及 delta.json/delta.patch。后续发现由本次改动造成的新影响或新失败时，在增量记录中说明扩大范围的依据。Git 提交、推送、PR、合并与部署仍按对应授权处理。
