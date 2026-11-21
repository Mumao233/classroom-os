# PR-08c · TBD-06 v2.1 分片与续传契约

林珈诚于 2026-11-20 签署 v2.1 第 1–6 节，关闭 TBD-06 的服务端与导入导出部分。签署证据为 [PR #22](https://github.com/Mumao233/classroom-os/pull/22) Head `f34653aae826f829332c7e0cfe41ef7b333301bc`。浏览器本地数值已确认、验收仍开放，详见[分部分签署](../DECISIONS.md#tbd-06-分部分签署2026-11-20)。本契约补充适用于 PR-08c 显式启用的隔离合成实现。公开 OpenAPI 0.2.1 和 WS 事件协议沿用现有版本。

## tbd06-v2 签署策略

生效记录为 2026-11-20 的策略签署。单位采用十进制，1 MB = 1,000,000 bytes，1 GB = 1,000,000,000 bytes。比较旧包时统一换算成字节。前后端使用同一版本的策略，服务端裁决。超限显式拒绝，复用 `CAPACITY_EXCEEDED`、`QUOTA_EXCEEDED`、`RATE_LIMITED`、`GONE` 等契约错误码。导入按整包校验并原子提交。降额后已有超额数据仍可读可删，新增写入拒绝。

| 计量对象 | 签署值与行为 |
| --- | --- |
| 单张图片与上下文 | 单张图片 16,000,000 bytes；问答与 Review 各自沿用 16,000,000 bytes 的上下文总预算。Alpha 不提供 PDF/PPT 文档上传。 |
| 导入与导出包 | 导入压缩后最多 500,000,000 bytes，展开最多 1,000,000,000 bytes；导出最多 500,000,000 bytes。原样重导承诺限于聚合 ZIP，发布前按导入上限自检。Alpha 禁止导出原音。 |
| 归档解析预算 | 最多 10,000 个常规文件（目录不计），路径最多 32 段，单条目及整包的展开字节数与压缩字节数之比至多 100:1。JSON/manifest 合计最多 64,000,000 bytes，单个 JSON 最多 16,000,000 bytes。单图最多 50,000,000 像素，整包累计最多 4,000,000,000 解码像素。 |
| 分片与下载票据 | 上传分片和单 Range 下载均最多 16,000,000 bytes；票据取签发后 60 秒、会话到期及对象访问截止中的最早值。续签绑定同一对象版本。 |
| 跨导入导出的共享任务槽 | 每账号 1 槽，平台 4 槽，第 5 个拒绝且不排队。预检开始或导出接单时占槽；等待确认最多占槽 30 分钟；执行租约以 5 分钟为限，通过心跳续期。 |
| 导入暂存和导出产物 | 预检前暂存每账号最多 1 个未完成上传，平台暂存池 5,000,000,000 bytes；每账号最多 1 个可访问导出，平台导出池 5,000,000,000 bytes。两池各自计入已预留和尚未证明擦除的对象字节。 |
| 可访问时长与清除 | 暂存从创建、导出从发布起 24 小时截止。撤权、取消、失败立即截止。物理清除从实际截止起算，24 小时为目标，超时告警，72 小时为硬上限，届时仍有残留升级事故。 |
| 云端存量 | Alpha 只观察每账号存量字节，并按运维设置的平台总量阈值告警。每账号存量额度另行设计，独立于 11a 周期用量。 |

生产放行须另行接入生产配置、满足 TBD-10 等相关条件，并先合入对既有媒体上传 `purpose=import_package` 默认拒绝的独立变更。PR-08c 新增入口默认关闭。

## 通用

- 路径相对于 `/api/web/v1`。会话与现有 CSRF、Origin、no-store、墓碑及恢复门禁沿用。owner 只从服务器会话取值。其他账号的资源返回 404。所有 UUID 为小写标准 UUIDv4。SHA-256 为 64 位小写十六进制。字节量为十进制整数，时间为 UTC RFC 3339。
- 每个写请求要求 `Idempotency-Key: <uuid>`。作用域为 owner 和 operation。服务器持久记录行为指纹及最小结果，同键同指纹重放，同键异体返回 `409 IDEMPOTENCY_MISMATCH`。成功收据只在对应事实提交后返回。重放仍检查访问权限和期限。读取关闭后的状态不得发放访问权限。
- 参数 `policy_version=tbd06-v2` 表示已签署的服务端与导入导出策略。每个上传会话锁定该版本的限制，重启和重试保持一致。

## 上传会话

| 操作 | 请求 | 成功结果 |
| --- | --- | --- |
| POST `/imports/uploads` | JSON `{byte_length, archive_sha256, mime_type}`。长度 1..500000000。类型为 `application/zip` 或兼容的 `application/json`。JSON 额外受单条 JSON 长度约束。 | 201 `{upload_id, policy_version, byte_length, archive_sha256, part_bytes, part_count, expires_at, state, parts}` |
| GET `/imports/uploads/{upload_id}` | 会话。 | 200 同上。仅列出已校验并提交记录的分片。 |
| PUT `/imports/uploads/{upload_id}/parts/{part_number}` | `Content-Type: application/octet-stream`，`Content-Length: <byte_length>`，`X-Part-Offset: <offset>`，`X-Part-SHA256: <sha256>`，请求体只含分片字节。禁止 Content-Encoding。 | 200 `{upload_id, part_number, offset, byte_length, sha256}` |
| POST `/imports/uploads/{upload_id}/seal` | JSON `{archive_sha256}`。先验证分片是否完整。 | 202 `{upload_id, state, expires_at}`。流式校验完成后为 `verified`，重试可从持久回执获得结果。 |
| POST `/imports/uploads/{upload_id}/cancel` | JSON `{}`。 | 200 `{upload_id, state, expires_at}`。状态为 `cancelled`，访问截止与清除时钟已记录。 |

`part_bytes=16000000`。`part_count=ceil(byte_length/part_bytes)`，最多 32。序号从 0 起，固定 `offset=part_number×part_bytes`。除最后一片外均恰好 16000000 bytes。最后一片为剩余字节。续传按 `parts` 选择缺片，可乱序；每片以 `{part_number, offset, byte_length, sha256}` 表示，按序号升序返回。几何错误、溢出或请求体/哈希不符返回 `422 VALIDATION_FAILED`。已提交序号的字节标识不能替换。相同内容重放原结果，异体返回 409。缺片封口返回 `409 SOURCE_UNAVAILABLE`，允许补齐后重试。分片不执行整 ZIP 文件签名检查。

状态为 `uploading → verified`，或 `failed / cancelled / expired`。封口流式按序读取固定对象版本，重新验证每片和整包 SHA-256。只有提交 verified 后可以进入预检。失败或取消立即禁读与封口。对象键和临时文件由服务端生成，先登记物理清除清单再写对象。进程崩溃后按持久记录恢复或撤销，不能把未校验对象当已完成。清除必须证明全部对象版本和未完成上传均已移除。已释放的对象预留不得由落后 worker 重新写入。

## 准入与生命周期

- 每 owner 仅一个未完成上传。上传创建即按声明字节预留。平台导入暂存加预留最多 5000000000 bytes，平台导出产物加预留也最多 5000000000 bytes。尚未证明擦除的副本继续占用各自池。组装采用流式逻辑对象或有界 spool，禁止无预留地复制一整份到对象桶。每 owner 最多一个仍可访问的导出产物。重叠的新写入受平台字节预算约束。
- 预检开始及导出接单共用四槽，每 owner 总共一槽。满额返回 `429 RATE_LIMITED`，复用 Error 的 `retry_after_seconds`，不进入等待队列。PG 在同一事务执行 owner 和平台判定。worker 使用单调 fence 和最长 5 分钟执行租约。过期 worker 的心跳、状态转换及发布均拒绝。已绑定持久 Task 的本地作业在最近一次租约失效后的五分钟内可重新申请一次执行槽，须重新取得 Task 与槽位 fence。未取回、超出上传截止或二次丢租约即按原截止进入清除。成功预检转为最多 30 分钟等确认，取上传 24 小时截止与等待期限的最早值。等待过期只释槽，旧预检凭据拒绝，仍可在上传期限内用新键重做预检。
- 上传从创建起 24 小时截止，导出从发布起 24 小时截止。失败、取消或撤权实际生效时提早截止。清除时钟取最早访问截止，+24 小时仍有残留即告警，+72 小时仍有残留升级事故。清除重试不推迟时钟。逻辑撤权后的清除照原计划重试。

## 导出续传与原样重导

复用 `POST /media/{asset_id}/access` 与 `GET /media/{asset_id}/content` 的会话、票据、单 Range 与 206 语义。候选分段须为单 Range，最多 16 MB。候选下载授权沿用 `MediaAccess` 响应体，通过 `X-Archive-Length`、`X-Archive-SHA256` 和 `X-Asset-Version` 响应头提供续传指纹。续签通过 `X-Asset-Version` 请求头提交首次下载的版本。旧接口消费者不增加必填字段。读取任一分段和流内授权点都重新校验 owner、会话、对象版本与每个聚合来源。版本变化返回 `409 SOURCE_CHANGED`，过期或撤权返回 `410 GONE`。票据截止取签发 +60 秒、会话期限、对象访问截止的最早值。断点从固定版本下最后完整验证的偏移继续。最终按完整 SHA-256 验收。

`include_audio=true` 明确拒绝。仅聚合 ZIP 对 account、course、class_session 承诺原样重导。导出先校验预计清单，实际字节仅在私有隔离区生成。发布前以同一受限导入解析器复核实际归档与成员哈希、压缩后 500 MB、展开 1 GB、常规文件 10000、深度 32、比例 100:1、元数据合计 64 MB/单 JSON 16 MB、单图 5000 万像素及总 40 亿像素。目录记录另设解析预算，所有路径均须安全。任何失败不发布对象和下载授权。JSON、Markdown 仅承诺阅读。

## §8 验证

1. 真实导出及分片续传再预检，达到 500000000 bytes 后按 SHA-256 比对全部内容与作用域。保留缺片、破坏字节与预算超出负对照。
2. 四个不同账号完成预检停在确认页，第五个收到 429。用数据库时钟跨过 30 分钟，再尝试第五个和旧确认。执行租约过期后旧 fence 不得写或发布。
3. 在真实 Range 传输中跨到期或撤销一个聚合来源。旧票据、新票据和续传下一段均拒绝。用对象服务实际删除及可控时钟验证告警和事故。
4. 使用已合并的 05b/08a 浏览器代码跑真实 IndexedDB 写满、安全停止与可恢复块复读。若现有入口达不到 v2.1 满额实验，单独记录差距。浏览器产品改动另行安排。

以上为探索用例。最终 VMware 矩阵和 Astra 复审见 [PR #22 验收记录](https://github.com/Mumao233/classroom-os/pull/22)。浏览器配额探针只验证探索场景，浏览器本地部分仍按[剩余证据要求](../DECISIONS.md#tbd-06-分部分签署2026-11-20)补验。


## 实现候选接线

- `create_app(portable_jobs=...)` 在同一显式合成环境中挂载业务路由和 multipart。冻结的 `ExportRequest`、`ImportCommit`、`ImportPreview`、`Task`、`MediaAccess` 响应体维持原样。兼容上传经现有 Media 流程校验后可转换为同一原样重导流程，使用更早的既有访问截止时间。旧 Media 客户端可使用 `Upload.id` 调用候选 cancel 路径，释放未完成上传的 owner 名额；对象字节仍计到物理擦除。
- `POST /exports` 和 `POST /imports/commit` 均按现有 202 Task 协议回执。平台槽位、Task、回执、Outbox、绑定在同一事务建立。仅 `pr08c-1` 专用 worker 执行，可查询或取消。成功导出的 Task.result_id 指向私有资产，导入指向首个新课堂。所有原课堂 UUID 与新课堂 UUID 的映射随提交持久化。
- 现行候选封口在有界请求内完成流式校验并提交 verified。对象存储或数据库失败后按同一操作键和固定分片版本重试。封口未写正式课堂，也不排入执行槽。旧草案设想过异步 sealing 状态，本次 §3.1 复审覆盖这项调整。
- 聚合 ZIP 根为 `aggregate.json`，字段为 `schema_version=1`、`format=classroom-assistant-aggregate`、`scope` 和 `sessions`。每个课堂命名空间由清单列出的 32 位小写十六进制 ID 界定。成员只含 `session.json`、`manifest.json` 及清单列出的图片，单课堂清单兼容 schema 1。Web 持久模型使用 `web_transcript`、`web_images`、`web_notes` 保留真实版本和缺失时间信息。原始来源 UUID、片段哈希与 frozen excerpt 不重写，独立副本另建本地映射。已导入后编辑的正文与 Review 导出当前版本，旧证据独立保留。Web Review 的 Markdown 与个人注释分别存储，并通过原 Review 映射覆盖个人稿。
- 单候选范围包含明确授权的课堂集合。对聚合来源课堂、导出的可用图片与转写来源重新做授权，撤销时整个导出失效。图片与转写完整性受源哈希和对象版本约束。JSON/Markdown 仍为阅读格式。
- 运行 `python -m classroom_web.entrypoints.worker --portable-config <isolated-config>` 时额外明确提供 runtime/scheduler URL 和 worker ID。配置包含相同合成数据库的 deletion_queue_url、已有 spool_directory、私有 loopback media 与 ledger 配置。worker 启动时复核删除账本，每 30 秒处理到期、擦除和计数告警。签署的 Alpha 容量与生命周期见上文。该命令用于隔离合成环境，生产配置另行接入。
