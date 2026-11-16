# PR-05a 正式身份接入候选

本文件交协调聊天安排接入。当前 POC 修改范围仍为 `tools/pr05a/` 和本目录。下列共享增量需要相应归属、契约审查和真实 PostgreSQL/PR-03b 联调证据。

2026-11-15 审核后已按协调授权形成正式服务、PG repository/crypto、可选 HTTP 接线及安全检查候选，27 项定向用例通过。授权复用现有 reauth_proofs，未增加 grant 表，auth_repository.py 无需修改。候选 DDL、公开契约 overlay、录音 port 和剩余启用条件见 [本轮增量交接](FORMAL-INTEGRATION-20261115.md)。下方保留此前接入规划，当前已落实范围以该交接及独审快照增量为准。

## 当前源代码确认

- `web/backend/classroom_web/application/auth.py` 的 `AuthService.principal()` 通过服务端 session 校验生成 Principal，使用 `security_epoch`。
- `web/backend/migrations/versions/0007_authentication.sql` 的设备撤销同时增加 `devices.key_epoch` 并撤销派生会话。POC 已按这一失效语义对齐合成设备撤销。密码重置则更新用户安全版本并撤会话。
- 同一迁移目前对 `devices.trusted` 使用 `CHECK(NOT trusted)`，`trustDevice` 返回 `SERVICE_DISABLED`。该关闭状态的正式变更需要安全 POC 评审、TBD-03 决定及协调的新迁移。
- `web/backend/classroom_web/entrypoints/http.py` 当前选中的正式路由包括设备列表和设备撤销，缓存解锁和信任设备尚待接线。
- OpenAPI 0.2.0 已有 `/me/cache-unlock`、`CacheUnlockRequest`、`CacheUnlockResult`。description 要求在线再认证，当前该 operation 没有明确的 `x-reauth-policy`。具体目的、风险级别、证明绑定与窗口应经安全审查明确。

## 最小文件增量候选

| 文件或目录 | 增量及所属安排 |
|---|---|
| `web/backend/classroom_web/application/cache_keys.py`，新增候选 | 解锁、恢复、轮换用例，消费经过正式校验的身份和事务端口；不从请求反序列化 Principal |
| `web/backend/classroom_web/infrastructure/cache_key_repository.py`，新增候选 | PostgreSQL 封装 key/grant、锁序及撤权事务；复用现有数据库连接与 owner 事务模式 |
| `web/backend/classroom_web/infrastructure/cache_key_crypto.py`，新增候选 | 版本化 KEK 包装和用途分离；复用现有 cryptography 依赖。现有 AuthCrypto 的 MFA seed 字符串封装绑定不同，不直接套用 |
| `web/backend/classroom_web/entrypoints/http.py`，共享 | 在协调后的 create_app 接缝注册缓存用例，使用既有安全请求校验、错误 DTO 和 no-store 策略 |
| `web/backend/classroom_web/infrastructure/database.py`，共享 | 协调新增表的安全检查，保持 RLS、运行角色及 OwnerContext/Principal 边界 |
| `web/backend/classroom_web/infrastructure/auth_repository.py` 与身份 SQL 函数，共享 | 将重置、撤销、禁用、注销的 grant 失效纳入同一事务，保持现有锁序及 outbox/WS 失效机制 |
| `web/backend/migrations/versions/`，协调分配 revision | 根据已接受的组合链新增迁移，保留现有 0007 历史字节。0009_private_media 已归 PR-04a，05a 不抢占编号 |
| `web/backend/tests/integration/` 与 `conftest.py`，共享测试增量 | 真实03b Cookie/MFA/再认证、PG撤销竞态和组合迁移回归，具体文件由协调聊天分配 |
| `web/frontend/src/api/auth-client.ts` 及缓存客户端，Claude 协调 | 正式会话/撤销事件接入、秘密响应即用即清、锁定渲染清理。独立实验模块不能直接成为设置页客户端 |
| PR-09b 工作树的 `web/frontend/src/pages/settings/Settings.tsx` | 读取时确认的 Claude 当前设置页，协调信任/撤销/恢复提示及未同步内容处理 |

共享依赖和锁文件暂未提出新增需求。现有 AES-GCM、HTTP、PG 与浏览器工具依赖足以支撑这一步候选。

## 表与权限候选

正式封装实体沿用 `domain-model.json` 已有的 `cache_keys`。前稿称为 cache_key_envelopes 的内容对应该实体的封装职责，接入候选使用既有名称和字段 `owner_id/device_id/purpose/class_session_id/wrapped_dek/kek_version/key_version/revoked_at`。nonce、算法和封装格式可放入版本化 wrapped_dek，是否拆字段交契约审查。约束需覆盖 owner/device/课堂复合外键、用途、版本唯一性，以及 class_session_id 为 null 时的唯一性语义。运行角色通过受审查的最小权限路径读取封装值，owner RLS 与授权检查同时成立。

`cache_unlock_grants` 需要 owner、session、device、security_epoch、当前 device key_epoch、key_id、purpose、同意或恢复证明引用、失效时间及撤销状态。只保存状态和引用。解封及秘密响应在重新校验全部身份和期限后产生，通用操作收据不能保存明文 DEK。

该 grant 表为额外事务化授权状态候选，须论证与既有 session/reauth/consent_records 的职责及失效关系。若既有设施足以表达授权状态，则优先复用。它不替换 cache_keys，也不由本轮直接创建共享表。

服务端 KEK 由受限 secret/KMS 版本化提供，与 MFA、媒体和承诺密钥的用途边界分别审查。部署及真实 secret 注册另有授权。本轮 POC 的 SQLite 和进程内合成 KEK 仅承担机制验证。

## 历史 key 恢复契约候选

POC 的 `recover-key` 是独立合成协议。当前正式 DTO 没有历史 key_id 或恢复证明字段，不能私加字段接入。

| 候选 | 契约影响 |
|---|---|
| 单独的恢复授权端点 | 保留普通解锁 DTO，新增 exact target/key_id、当前设备版本及单次恢复证明的端点，需新增版本、fixtures 和消费者 |
| 扩展现有解锁 DTO | 新增明确的恢复意图、历史 key 引用和证明，更新严格 schema 与全部生产者/消费者 |

当前设备失效版本与历史 DEK 版本需要分别表达。不能为了恢复旧密文回退 `devices.key_epoch` 或清除安全版本。POC 的明确恢复允许在合成账号重新登录后重授权原设备，这只是实验候选。正式产品的新设备登记、已撤销设备如何重新信任、MFA/再认证或额外核验要求，由 TBD-03 和安全审查确定。

## 接入验证

组合验收至少覆盖真 PG 空库/旧库升级、运行角色与 RLS、服务端 Principal、MFA/再认证证明、撤销和解锁交错、旧 device/key epoch、历史 key 恢复、秘密响应丢失和重签、WS/多标签清理，以及真实浏览器存储矩阵。作者合成测试的通过结果和正式身份链路证据分别记录，持久恢复启用等待既有评审门禁。
