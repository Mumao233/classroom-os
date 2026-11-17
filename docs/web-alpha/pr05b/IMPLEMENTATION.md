# PR-05b 采集恢复与租约

负责人林珈诚。基线为 PR-04b 合并提交 `65dbcaba30a0d6b9c50596b4800bfa66edb18052`。本工作包提供采集权威状态、媒体分块提交与客户端恢复 runtime，供 PR-06b 消费。

## 共享依赖与迁移

正式迁移保持单链 `0010_deletion_lifecycle → 0011_cache_keys → 0012_capture_recovery`。0011 将既有 PR-05a 正式候选迁入 Alembic，并包含验证时间在 policy 锁后重查的修复。与 PR-08a 共享 OpenAPI 0.2.1 的 unlockCache 合约及生成文件。两方谁先合并，另一方同步 main 后重新核对唯一 head。

主控 INTEGRATION-001 对 A `783b733` 与 B `e576dfa` 的 merge-tree 预检发现两处测试文本冲突，分别为 `test_capture_migration.py` 的 add/add 和 `test_data_foundation.py` 的内容冲突。合流时保留 A 的严格断言版本，其中完整包含 B 的 0011/cache_keys 断言，另加 0012 升级与九张 capture 表的列校验。业务与公开合约预检可自动合流。实际同步后仍需运行迁移、这两个测试、公开合约、身份缓存和 04b 回归。

0012 保留冻结表名和公开 DTO，增加内部 capture policy、epoch progress、recovery grant 和 chunk receipt 状态。owner RLS、组合外键、身份不可变和单调触发器约束状态。媒体 audio commit 由绝对关闭约束升级为默认关闭的策略触发器，04b 原 retention 触发器继续执行。

锁顺序为 auth/user、catalog ancestors、capture policy/owner advisory/lease、epoch/consents、chunk/media、owner event counter。05a recording-key authorizer 在既有 auth/reauth/cache policy/catalog 前缀后取得 capture 锁。capture 路径没有反向获取 proof/cache 锁。

## 服务端权威

`CaptureService` 必须显式启用，且 `DeletionService.ready` 为真。数据库 capture policy 另有独立开关，所有时间和 consent 产品参数初始为空。默认构造、正式迁移、未配置真实运行入口均保持关闭。

- acquisition 绑定当前 owner/device 与 consent，建立新 epoch 和 owner 唯一 lease。显式 takeover 需要 expected lease ID，fence 单调增加。
- 新设备从已授权 acquisition 的 `LEASE_CONFLICT` 响应读取既有 `error.conflict_id`，再显式确认同一 lease ID。该值在 owner 与目标课堂、device、consent 检查通过后返回，客户端仅接受合法 UUID。
- renew/pause/resume/stop 通过 DB 时钟、fence、device 和状态检查。resume 关闭旧 epoch 与 pause gap，创建新 epoch。
- chunk 的 `(owner, epoch, seq)` 唯一，sample 范围、PCM byte length、SHA-256 和媒体 lineage 固定。匹配 descriptor 使用新幂等键时也登记该键。
- private object 的条件创建固定首个 object timestamp。对象成功、DB 回滚后可重试关联同一对象。prepare 已写 expires/hard expiry，未重试对象可被 04b expiry 和删除 worker 清除。
- durable receipt 与 owner outbox 在同一 DB 事务提交。`frame_received` 永远 `durable=false`。durable event 使用已有签名 cursor。
- manifest 压缩 durable/missing seq 范围。恢复授权固定旧 epoch 的本地封口范围、session/device/security epoch 和短期限，只存 token digest。恢复分块的 ASR ingest mode 为 recovery，`asr_authorized=false`。
- classroom/祖先墓碑、任意 audio 删除请求、consent 撤销、期限和 restore gate 都在操作入口检查。audio 删除后该课堂的重新 acquisition 也拒绝。

## WSS 与 06b port

`/api/web/v1/ws/capture` 使用 03b 精确 Origin、当前 HttpOnly Cookie、单次 ticket 和 `classroom.v1`。ticket 扩展仅在 0012 中前向替换 identity function，events 通道继续原行为。

每条连接只接收一个 pending chunk，每帧最多 3200 bytes、每 chunk 最多 32000 bytes。JSON header 后必须紧邻精确长度/hash 的 binary，frame seq 和 sample offset 连续。heartbeat 校验连接序号和当前权限，不续租、不产生音频。运行服务器应设置 WS 单消息和队列上限，合成验收使用 `ws_max_size=8192`。

`CaptureSource.durable_source` 返回稳定 chunk/epoch/classroom ID、sample 范围、SHA-256、asset ID、receipt ID 与 live/recovery 来源。PR-06b 负责 provider streaming、partial/final、unknown 与付费授权，本包没有 provider 调用。

## 客户端与 PR-08a 接口

`src/capture/ports.ts` 定义结构化 `RecordingCachePort`，直接适配 PR-08a 的 put/read/ack recording 和 save/read opaque capture state。runtime 负责恢复状态，cache 负责加密、身份绑定、配额和失效。此 PR 可独立编译。

顺序为显式 acquisition、recording-key unlock、`runtime.prepare` 验证可写的加密 state、显式 `start`、worklet 输入。runtime 不获取麦克风，不在 mount/reconnect/restore 后自行采集。传入 lease 和 ancestry 都被复制。

pending descriptor/稳定操作键先写 state，再写 recording bytes。上传前必须成功读回本地 bytes 并核对 hash。完整 durable receipt 先移入持久 cleanup 队列，再让 cache 清块，清理失败或刷新可重放。恢复后需要显式授权。

本地 pause/stop 先关闭采集状态并保留未传内容。离线 stop 保存冻结范围与幂等 intent。旧 lease 过期后，确定的 STALE_FENCE 允许转同一范围的 recovery；RESULT_UNKNOWN 保留待核对 intent。被安全停止覆盖的未知 renew 仍保留原 key/body。

worklet 复用 `static/pcm-worklet.js` 的连续重采样、stereo→mono 和尾片 flush，adapter 量化为 little-endian PCM16。adapter 与 runtime 都限制排队 bytes。append 挂起或存储失败时停止 source，错误可通过 `status` 和 `stop` 观察。调用者须先停止/flush worklet并等待 append，再调用 runtime pause/stop。

## 本轮授权与产品门禁

真实录音持久化、真实 provider、付费 API 和生产启用关闭。独立 synthetic fixture 显式配置短 lease/recovery、synthetic consent 与 retention；这些值没有进入产品默认配置。正式 retention anchor/期限、consent 文案、云 IAM/region 和真实证书链仍需对应产品与部署验收。

采集 runtime 提供库入口，产品采集 UI 与真实麦克风启用须在后续明确授权下接入。其与 PR-08a 的联合浏览器验证采用独立合成快照，验证记录另见验收文件。Draft PR 未经用户授权不得合并。
