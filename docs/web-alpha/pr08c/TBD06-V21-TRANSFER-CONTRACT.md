# PR-08c · TBD-06 v2.1 分片与续传契约候选

状态为待 Astra 复审。适用范围为 Web Alpha 隔离合成环境。负责人为林珈诚。依据是 2026-11-20 v2.1 第 3.1、4、5、8 节。冻结 OpenAPI 0.2.1 的现有媒体、采集、缓存和计费操作维持原语义。新端点在显式启用 PR-08c 候选时挂载。暂不关闭 TBD-06。

## 通用

- 路径相对于 `/api/web/v1`。会话与现有 CSRF、Origin、no-store、墓碑及恢复门禁沿用。owner 只从服务器会话取值。其他账号的资源返回 404。所有 UUID 为小写标准 UUIDv4。SHA-256 为 64 位小写十六进制。字节量为十进制整数，时间为 UTC RFC 3339。
- 每个写请求要求 `Idempotency-Key: <uuid>`。作用域为 owner 和 operation。服务器持久记录行为指纹及最小结果，同键同指纹重放，同键异体返回 `409 IDEMPOTENCY_MISMATCH`。成功收据只在对应事实提交后返回。重放仍检查访问权限和期限。读取关闭后的状态不得发放访问权限。
- 参数 `policy_version=tbd06-v2` 对应 v2.1 测试版。每个会话锁定该版本的限制，重启和重试保持一致。正式开启另需验收。

## 上传会话

| 操作 | 请求 | 成功结果 |
| --- | --- | --- |
| POST `/imports/uploads` | JSON `{byte_length, archive_sha256, mime_type}`。长度 1..500000000。类型为 `application/zip` 或兼容的 `application/json`。JSON 额外受单条 JSON 长度约束。 | 201 `{upload_id, policy_version, byte_length, archive_sha256, part_bytes, part_count, expires_at, state, parts}` |
| GET `/imports/uploads/{upload_id}` | 会话。 | 200 同上。仅列出已校验并提交记录的分片。 |
| PUT `/imports/uploads/{upload_id}/parts/{part_number}` | `Content-Type: application/octet-stream`，`Content-Length: <byte_length>`，`X-Part-Offset: <offset>`，`X-Part-SHA256: <sha256>`，请求体只含分片字节。禁止 Content-Encoding。 | 200 `{upload_id, part_number, offset, byte_length, sha256}` |
| POST `/imports/uploads/{upload_id}/seal` | JSON `{archive_sha256}`。先验证分片是否完整。 | 202 `{upload_id, state, expires_at}`。持久记录 `sealing`，worker 逐片验哈希及整包哈希。 |
| POST `/imports/uploads/{upload_id}/cancel` | JSON `{}`。 | 200 `{upload_id, state, expires_at}`。状态为 `cancelled`，访问截止与清除时钟已记录。 |

`part_bytes=16000000`。`part_count=ceil(byte_length/part_bytes)`，最多 32。序号从 0 起，固定 `offset=part_number×part_bytes`。除最后一片外均恰好 16000000 bytes。最后一片为剩余字节。续传按 `parts` 选择缺片，可乱序；每片以 `{part_number, offset, byte_length, sha256}` 表示，按序号升序返回。几何错误、溢出或请求体/哈希不符返回 `422 VALIDATION_FAILED`。已提交序号的字节标识不能替换。相同内容重放原结果，异体返回 409。缺片封口返回 `409 SOURCE_UNAVAILABLE`，允许补齐后重试。分片不执行整 ZIP 文件签名检查。

状态为 `uploading → sealing → verified`，或 `failed / cancelled / expired`。封口流式按序读取固定对象版本，重新验证每片和整包 SHA-256。只有提交 verified 后可以进入预检。失败或取消立即禁读与封口。对象键和临时文件由服务端生成，先登记物理清除清单再写对象。进程崩溃后按持久记录恢复或撤销，不能把未校验对象当已完成。清除必须证明全部对象版本和未完成上传均已移除。已释放的对象预留不得由落后 worker 重新写入。

## 准入与生命周期

- 每 owner 仅一个未完成上传。上传创建即按声明字节预留。平台导入暂存加预留最多 5000000000 bytes，平台导出产物加预留也最多 5000000000 bytes。尚未证明擦除的副本继续占用各自池。组装采用流式逻辑对象或有界 spool，禁止无预留地复制一整份到对象桶。每 owner 最多一个仍可访问的导出产物。重叠的新写入受平台字节预算约束。
- 预检开始及导出接单共用四槽，每 owner 总共一槽。满额返回 `429 RATE_LIMITED`，复用 Error 的 `retry_after_seconds`，不进入等待队列。PG 在同一事务执行 owner 和平台判定。worker 使用单调 fence 和最长 5 分钟执行租约。过期 worker 的心跳、状态转换及发布均拒绝。成功预检转为最多 30 分钟等确认，取上传 24 小时截止与等待期限的最早值。等待过期只释槽，旧预检凭据拒绝，仍可在上传期限内用新键重做预检。
- 上传从创建起 24 小时截止，导出从发布起 24 小时截止。失败、取消或撤权实际生效时提早截止。清除时钟取最早访问截止，+24 小时仍有残留即告警，+72 小时仍有残留升级事故。清除重试不推迟时钟。逻辑撤权后的清除照原计划重试。

## 导出续传与原样重导

复用 `POST /media/{asset_id}/access` 与 `GET /media/{asset_id}/content` 的会话、票据、单 Range 与 206 语义。新候选在下载授权响应中给出 `byte_length`、`sha256` 和不透明的 `asset_version`。重签必须带首次下载记录的 `asset_version`。旧接口消费者不增加必填字段。读取任一分段和流内授权点都重新校验 owner、会话、对象版本与每个聚合来源。版本变化返回 `409 SOURCE_CHANGED`，过期或撤权返回 `410 GONE`。票据截止取签发 +60 秒、会话期限、对象访问截止的最早值。断点从固定版本下最后完整验证的偏移继续。最终按完整 SHA-256 验收。

`include_audio=true` 明确拒绝。仅聚合 ZIP 对 account、course、class_session 承诺原样重导。导出先校验预计清单，实际字节仅在私有隔离区生成。发布前以同一受限导入解析器复核实际归档与成员哈希、压缩后 500 MB、展开 1 GB、常规文件 10000、深度 32、比例 100:1、元数据合计 64 MB/单 JSON 16 MB、单图 5000 万像素及总 40 亿像素。目录记录另设解析预算，所有路径均须安全。任何失败不发布对象和下载授权。JSON、Markdown 仅承诺阅读。

## §8 验证

1. 真实导出及分片续传再预检，达到 500000000 bytes 后按 SHA-256 比对全部内容与作用域。保留缺片、破坏字节与预算超出负对照。
2. 四个不同账号完成预检停在确认页，第五个收到 429。用数据库时钟跨过 30 分钟，再尝试第五个和旧确认。执行租约过期后旧 fence 不得写或发布。
3. 在真实 Range 传输中跨到期或撤销一个聚合来源。旧票据、新票据和续传下一段均拒绝。用对象服务实际删除及可控时钟验证告警和事故。
4. 使用已合并的 05b/08a 浏览器代码跑真实 IndexedDB 写满、安全停止与可恢复块复读。若现有入口达不到 v2.1 满额实验，单独记录差距。浏览器产品改动另行安排。

此候选不会启用真实学生、录音或付费调用。完整矩阵在汇报上述四项结果之后安排。
