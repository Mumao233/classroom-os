# PR-08a 可信加密缓存

负责人林珈诚。基线 `65dbcaba30a0d6b9c50596b4800bfa66edb18052`，分支 `feature/web-pr08a-trusted-cache`，公开契约 `0.2.1`。

## 正常入口

`AuthClient.createTrustedCache(options)` 创建 `AuthenticatedCache`。默认 `enabled=false`、`recordingEnabled=false`。授权启用后，调用 `unlockScope(scope, password, mfaCode, classSessionId)`，依次使用正式 `/auth/session`、`/me/devices`、绑定 `unlockCache` 的 `/auth/reauth` 与 `/me/cache-unlock`。密码、证明和 DEK 仅在调用期间留在内存。读取旧密文前需要当前合法的在线解锁。

`AuthClient` 的现有构造方式和业务方法继续可用。`clear`、开始登录、退出、账户安全操作和当前设备撤销即时通知缓存失效。退出网络失败也会锁定。当前会话由 `SessionContext` 提供，当前 key_epoch 由设备列表与 key response 双重核对。适配器每 5 秒和网络恢复时复核身份/设备，服务端明确拒绝时锁定，网络不可达时受已有 key deadline 限制。

消费者监听 `locked` 事件，清掉自己持有的展示明文、Blob URL 和编辑视图。调用 `close()` 释放订阅和计时器。当前 PR 提供存储与身份端口，后续课堂/Review/离线同步界面按自身工作包接入。

## 数据与密钥

新增 `web/frontend/src/cache/`。`types.ts` 为内部端口与记录，`indexed-db.ts` 封装原生事务，`trusted-cache.ts` 管理三池与密钥，`auth-cache.ts` 连接正式认证流程。

- AES-GCM 256-bit key 导入为不可导出 `CryptoKey`，仅存在实例内存中。导入缓冲区与临时明文副本在完成后覆盖。
- 每次新加密使用随机 96-bit nonce，持久 nonce 唯一约束与 record 在同一 IDB 事务提交。重试返回既有不可变记录，同版本异体报 `REVISION_CONFLICT`。
- AAD 包含 owner、device、scope、object、revision、generation、key ID/version、schema、祖先关联及操作/核验元数据。记录正文与录音描述、恢复状态均加密。
- IndexedDB v3 包含 `records`、`metadata`、`nonces`、`namespaces`、`tombstones`。v1/v2 升级逐 cursor 回填仅含路由头、字节数、访问时间的 metadata，原密文、AAD 与 nonce 历史保留。升级事务中止时 schema 与回填整体回滚。`migrate()` 使用已获授权的内存 key 将 schema1 记录重新加密为 schema2，CAS 检查期间的原密文。
- 配额、LRU 与有序版本检查读取当前 pool 的 metadata。密文与 metadata 的写入、淘汰、ACK、删除、迁移与清理在同一事务完成。草稿与录音采用只读加载，学习池更新访问时间遇到明确配额失败后以原授权只读重试，继续检查墓碑、期限和解密后的屏障。
- namespace 为 owner/device，session 仅用于内存授权。锁定不删除唯一未同步内容。新 key 无法解开旧 key 的记录时返回 `KEY_REQUIRED` 并保留旧密文。
- 校验期限同时使用解锁请求开始时的墙钟和单调时间，网络耗时计入期限。每次读、写、解密完成和事务阶段复核期限与实例 generation。

## 三池接口

`put(write, bytes)`、`read(reference, classSessionId?)` 处理不可变记录。引用包含 scope、objectId、revision、generation。调用者保留输入直到 Promise 成功。quota、事务失败、锁定或损坏均拒绝 Promise，不会报告已安全保存。

学习池只接收真实云版本核验时刻 `verifiedAt`，执行最近核验后 14 天、实际访问课堂 LRU 20 节、50,000,000 bytes 软额。内容版本重新核验使用新的 generation 写入。淘汰只作用于学习池。可选目录缓存没有课堂 ID，仍受 TTL 与字节额限制。

草稿池需要稳定 `operationId`，有独立字节和操作数上限。`listPending('drafts')` 提供 08b 待处理引用。`acknowledgeDraft(reference, operationId)` 只清理被确认的准确 generation，晚到 ACK 保留后续编辑。超额停止新持久写，已有唯一稿继续保留。

录音池默认关闭，独立额度通过显式 `CacheLimits` 注入。`putRecording` 冻结完整 descriptor、PCM、祖先与 fence，核验 SHA-256、字节与样本范围后加密提交。`readRecording` 要求全量 descriptor 匹配。`acknowledgeRecording` 冻结服务端 receipt，要求 `durable`、有效 durable_at 与 receipt ID 后，只清理该 descriptor 的记录。

## PR-05b 与 PR-08b 接缝

主控决定 `B08A-001/002/003` 经 Astra 审查。适用于上述基线与 0.2.1。

| 接口 | 参数与语义 |
|---|---|
| `putRecording` | `AudioChunkDescriptor, Uint8Array, CacheAncestry, fenceToken` |
| `readRecording` | `AudioChunkDescriptor`，返回当前授权下的 PCM 或 null |
| `acknowledgeRecording` | `AudioChunkReceipt`，仅服务端 durable 事实清理唯一块 |
| `saveCaptureState` | `classSessionId, epochId, stateBytes, ancestry, fenceToken, generation` |
| `readCaptureState` | `classSessionId, epochId`，返回最新授权状态字节或 null |

稳定录音 ID 为课堂、epoch、seq，外层 owner/device 由当前 identity 命名空间绑定。采集状态是 05b 定义的不透明字节，缓存按 `(fence, generation)` CAS 接收更大版本，同版本异体拒绝。05b 负责服务端 lease、fence、durable 确认和恢复状态机。08b 负责未来同步、CAS 冲突界面与收据核对。

`readCaptureState` 按当前 namespace、scope 和目标 object 的复合索引倒序读取最高 fence/generation，再经正常 `read` 解密该条记录。无关 PCM 块的数量与损坏不影响目标状态查找。

08a 占有 `src/cache/*`、`api/auth-client.ts`，05b 占有 `src/capture/*`。公共类型复用生成的 `AudioChunkDescriptor`、`AudioChunkReceipt` 等，A 的结构兼容 port 支持两个 PR 独立检查。

## 删除、撤权与并发

同源 BroadcastChannel 通知内存锁定，同时 IDB namespace generation 提供独立持久失效屏障。即使广播未送达，读写事务仍检查 generation。账户切换、撤权与旧请求迟到均不能重用旧授权。

`applyDeletion` 消费 04b `object.deleted` 根事件，以 account/semester/course/class_session/audio 及具体对象 ID 匹配后代。四个删除状态都撤销缓存访问。墓碑与 namespace 递增、匹配密文清理在事务中完成，重新解锁后也拒绝旧对象写回。audio 删除只覆盖录音池。`purge()` 为明确清理操作，只清当前 owner/device 的记录，保留持久失效屏障和墓碑。

正式事件消费者与 08b 同步入口需要在重新开放离线数据前补齐授权/删除水位。AuthCache 本身轮询会话、设备和 key_epoch，调用方负责把补齐的删除事件交给 `applyDeletion`。当前默认关闭入口的独立验收覆盖事件到达后的墓碑处理，完整事件补齐在对应集成阶段验证。

## 迁移、启用与边界

共享前置由 05b 提供 `0011_cache_keys`，接在 `0010_deletion_lifecycle` 后，05b 的 `0012_capture_recovery` 已合入 main。本轮08a新增前向 `0013_trusted_device_guard`，同一迁移事务把既有 trusted=true 归为 false 并恢复已验证的 CHECK(NOT trusted)，保留 wrapped keys 与 key_epoch。生产策略继续关闭且窗口为空，可信设备产品入口继续关闭。合成 cache 测试只在自身隔离数据库临时移除该约束，结束时清除 true 并恢复。0.2.1 公开契约沿用现状。

产品解锁窗口、跨设备旧 key 恢复、信任设备入口与真实录音授权仍待对应产品决定。`CacheLimits` 的合成测试数值没有产品默认效力。当前功能只在合成配置下验收。浏览器离线撤权最迟在内存授权期限到期后生效，已交付的明文受页面/设备控制。托管运行时内存覆盖无法证明物理擦除。

04b 的数据库正文物理清除、独立账户撤销日志等既存边界继续按其交接记录处理。主线先合入另一包后，本包须重核单链迁移、0.2.1、墓碑及身份回归，再进入合并验收。
