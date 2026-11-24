# Classroom OS契约变更记录

## 0.4.0 · 2026-11-24 · PR-11b 运营后台写入和用量

负责人林珈诚。关联 AUTH-07、AUTH-02、QUOTA-01、COST-01/02。邀请创建、撤销、重签改为必填操作者理由，旧请求在新版本下返回 422。六种敏感写入进入封闭的 `ReauthOperation`，因此本次按 Alpha 破坏性次版本交接。HTTP 路径前缀沿用 `/api/web/v1`。

- 每种用途按未来写入的 operationId、目标、幂等键和完整请求体签发单次凭证。额度、开关和新建邀请使用当前管理员账号作为目标；用户更新与已有邀请操作使用相应路径的资源 UUID。
- 邀请理由须为 1–500 个 Unicode 码点，至少包含一个非空白字符。撤销与重签共用 `InvitationAction`。旧版前后端应成对升级或回退。
- 平台月度风险为固定占位、已知结算、剩余预留和未知余量之和。安全预留用于准入。上述金额均为整数微元字符串，沿用原字段。
- PR-11a 已有两个账本写操作可通过显式注入接入公开协议。用户与邀请四项的业务处理和审计事务留给 PR-11b 后端。PR-07a/07b 引用及 PR-11a 细粒度开关继续按各自候选交付。
- 0.4.0 发布前两轮复审对齐了请求边界。写入头及全部 18 个绑定未来 HTTP 写入的凭证用途共用规范小写 UUID；管理理由共用显式空白字符表；绑定的修订号和追加数上限为 `2^53 − 1`。Mock 也在写入前检查幂等键。四种学生恢复操作的 `op_id` 沿用控制协议。

[三项裁决、兼容窗口和验证](../../coordination/PR11B-CONTRACT-DECISION-20261124.md)

## 0.3.0 · 2026-11-22 · PR-10a 音频清单与 PR-06c → PR-10b 错误细分

负责人林珈诚批准方案 B 和机器可读细分，Astra 完成契约设计审查。0.2.1 → 0.3.0 收紧查询参数并扩展封闭枚举，按 Alpha 破坏性升级安排。关联 REC-01/02/03、UI-CLASS-11、ASR-03、QUOTA-01。生产者和消费者的实现、确认及联调见[交接与排期](../../coordination/PR10A-CONTRACT-DECISION-20261122.md)。

- `getAudioManifest` 必须传一个 epoch UUID。新增 `listAudioManifests` 和 `AudioManifestPage`，分页遍历课堂的全部采集轮次，包含退役轮次。两者返回相同的单轮次清单语义。
- Filetrans 在确认原音过期或删除时返回 410 `AUDIO_EXPIRED` / `AUDIO_DELETED`，在提交或重试的课次、月度额度拒绝时返回 409 `QUOTA_SESSION_EXCEEDED` / `QUOTA_MONTH_EXCEEDED`。通用错误继续供其他原因和既有业务使用。前端只按码分支，四种拒绝均禁止自动重提。
- HTTP 前缀沿用 `/api/web/v1`。同步 `info.version`、`Meta.contract_version`、契约集版本、合成 fixtures、生成的 HTTP 类型和 Mock Meta。此次读取及错误投影无业务数据迁移，WS 事件仍为 v1。
- 切换窗口先让 Codex 生产者和 Claude 消费者对齐 0.3.0，再开放真实接线。旧客户端会省略 epoch 或拒绝新枚举，旧服务没有列表端点。联调失败时回到成对的 0.2.1，关闭依赖新契约的界面。

## 0.2.1 补充 · 2026-11-21 · 收紧导入包媒体上传准入

`POST /media/uploads` 的 `purpose=import_package` 默认返回 `503 SERVICE_DISABLED`，在创建上传预留、资源和收据前拒绝。仅显式挂载并通过隔离校验的 PR-08c 合成预检或传输服务可使用该用途。普通图片的上传、既有上传 ID 的补传与确认，以及 04b 已有对象删除流程沿用现有语义。OpenAPI 0.2.1 请求和响应结构沿用现版，产品开放仍需单独批准。

## 0.2.1 补充 · 2026-11-20 · TBD-06 容量与期限策略

林珈诚签署关闭服务端与导入导出部分，策略版本 `tbd06-v2`，证据为 [PR #22](https://github.com/Mumao233/classroom-os/pull/22) Head `f34653aae826f829332c7e0cfe41ef7b333301bc`。浏览器本地仅确认数值，验收仍开放。见[签署与剩余证据](../../DECISIONS.md#tbd-06-分部分签署2026-11-20)。

- **服务端与导入导出。** 使用十进制 MB/GB，超限显式拒绝，降额保留已有数据。单图 16 MB，问答与 Review 各沿用 16 MB 上下文总预算，Alpha 不提供 PDF/PPT。导入 500 MB 压缩 / 1 GB 展开，导出 500 MB；最多 10,000 个常规文件、32 段路径、单条目及整包压缩比 100:1、元数据合计 64 MB / 单个 JSON 16 MB、单图 5,000 万像素 / 整包 40 亿像素。原样重导限聚合 ZIP，Alpha 不导出原音。
- **传输、准入与清除。** 16 MB 分片和单 Range 下载，票据取签发后 60 秒、会话到期、对象截止三者最早值。每账号 1 个任务、平台 4 槽，第 5 个拒绝且不排队；等待确认最多 30 分钟，执行租约 5 分钟。预检前导入暂存和可访问导出产物各为每账号 1 个、平台各 5 GB。访问 24 小时截止，物理清除自实际截止起算，24 小时目标、超时告警、72 小时硬上限。Alpha 每账号存量只观察，平台总量告警。详见[传输契约](../../pr08c/TBD06-V21-TRANSFER-CONTRACT.md)。
- **浏览器本地。** 学习缓存 14 天 / 20 节 / 50,000,000 bytes，草稿 20 MB / 默认 2,000 条操作，录音恢复 1 GB，录音可用空间准入 300 MB。草稿与录音恢复按密文 + nonce + AAD 计量，达到 80% 时提示，满额保护已有内容，清空缓存只清学习池。详见[三池契约](../05-SECURITY-OFFLINE.md#4-本地加密与三池生命周期)。

本条登记策略与兼容边界，公开 OpenAPI 0.2.1、WS 事件协议、生成类型和 fixtures 沿用现版。生产开放须另行接入配置、满足 TBD-10 等要求，并先合入既有媒体上传 `purpose=import_package` 默认拒绝的独立变更。

## 0.2.1 补充 · PR-06a 真实接口最小 POC 第四批

COST-01/02/03、QUOTA-01、ASR-05。负责人林珈诚。内部费用策略 v1 登记 [第四批签署记录](../../pr06a/PR06A-REAL-POC-AUTH-20261120-B4.md)。专用库保留前三批及其预留，三批关闭后第四批可独立预留 ¥1。内部执行回执新增脱敏的关闭状态、帧代码、原因和耗时，在专用 PostgreSQL 中随尝试完成入账。公开 HTTP、浏览器 WS 和 fixtures 沿用现有版本。

## 0.2.1 补充 · PR-06a 真实接口最小 POC 第三批

COST-01/02/03、QUOTA-01、ASR-05。负责人林珈诚。内部费用策略 v1 登记 [第三批签署记录](../../pr06a/PR06A-REAL-POC-AUTH-20261119-B3.md)。专用库事务迁移保留前两批及其预留，第三批须在前两批都已关闭后独立建批，单批 ¥1。操作流程增加第三批同候选的无 Key TLS 前置。公开 HTTP、浏览器 WS 和 fixtures 沿用现有版本。

## 0.2.1 补充 · PR-06a 真实接口最小 POC 第二批

COST-01/02/03、QUOTA-01、ASR-05。负责人林珈诚。内部费用策略 v1 增加 [第二批签署记录](../../pr06a/PR06A-REAL-POC-AUTH-20261119-B2.md) 和仅针对专用 PG 的事务迁移。迁移保留第一批数据及原预留。每个登记授权 ID 独立建批、撤销和释放，单批 ¥1。CLI 操作员按 ID 提交候选证明，复核角色按 ID 释放。旧无参撤销和释放调用迁移后改传明确 ID。公开 HTTP、浏览器 WS 和 fixtures 继续沿用现有版本。合成验收覆盖历史第一批保留、第二批独立预算、重复及错误授权拒绝。

## 0.2.1 补充 · 2026-11-18 · PR-06a 真实接口最小 POC 费用例外

COST-01/02/03、QUOTA-01、ASR-05。负责人林珈诚。专项内部费用策略首版 `v1`，适用 Draft PR #19。公开 OpenAPI `0.2.1` 和浏览器事件协议 `1` 继续使用现有 operation、event、schema 和 fixtures。

- 缺少可信计量上界原先拒绝所有真实请求。专项隔离入口可在独立复审、真人启动授权、受信身份与 PG 风险门禁均满足时，按唯一授权 ID 原子预留整批 ¥1。模型原价用于观察停止，实际账单按请求核对。普通收费继续使用原计量门禁。
- 提供专用 PG 安装脚本，在隔离数据库的现有预算 schema 上增量安装。批次、逐连接许可、帧、用量和账单证据新增独立表，旧预留、费用记录和计量结构兼容。公开 fixtures 沿用；专项 fixtures 用 synthetic PCM、FakeTransport 与连到 loopback 的固定版 WebSocket 驱动。
- Codex 负责隔离生产者，消费端为 POC 操作员及费用复核角色。Claude 前端接口及 Mock 无需接入，本轮没有 Claude 确认。Astra 已有条件接受设计，候选独审回执交付后由操作员绑定执行 SHA。当前真实调用授权仍待用户明确启动。
- 关闭专项开关或撤销授权即阻断新握手及新帧。保留原周期费用敞口并开放对账。部署面继续使用原生产开关，PR-06b 和其他工作包的真实付费依赖按原门禁执行。

## 0.2.1 · 2026-11-17 · PR-05b / PR-08a 缓存解锁接入

主控决定 B08A-003 经 Astra 审查，负责人林珈诚。AUTH-05/06、DATA-03/04、REC-02/03。API 前缀继续为 `/api/web/v1`。

- `ReauthOperation` 新增 `unlockCache`，目标为当前设备，未来操作键与精确 `CacheUnlockRequest` 共同绑定。`/me/cache-unlock` 引用既有 `ReauthToken` header 并登记再认证策略。
- `CacheUnlockRequest`、`CacheUnlockResult` 结构沿用已有定义。秘密响应 `no-store`，内存持钥，授权与策略期限取交集。服务和数据库策略初始关闭。
- Meta、生成类型及合成 fixture 的版本元数据同步为 0.2.1。PR-07a/07b 候选构建接受 0.2.0/0.2.1，原用例保留。05a overlay 对已存在的同形 unlock 分支复用，历史恢复继续由候选入口隔离。
- 生产者由 0011_cache_keys 增量迁移及既有 CacheKeyService 承接，消费者由 PR-08a AuthClient adapter 接入。旧客户端可继续调用既有操作，新增解锁需匹配新契约。WebSocket 与身份控制协议结构沿用现版本。
- 本次没有产品窗口数值批准。TBD-03、录音授权、真实原音与生产启用继续受各自门禁约束。验证记录分别随两个工作包交付。

## 0.2.0 · 2026-11-06 · PR-03b S2 独立交审候选

S1 已独立 APPROVE，用户授权本轮正式契约及实现门禁修改；**本条是规范候选，尚待S2独审，非实现/启用/发布批准**。D01–D03 CLOSED仅设计层，TBD-08 OPEN，条件风险未生效。AUTH-01/03/04为主体，AUTH-05/06保留权限及撤销，AUTH-02/07/08归属不扩。

- OpenAPI0.1.0→0.2.0，/api/web/v1保留；三新设密码字段6–1024且服务端Admin12；当前实现和生成类型不在本轮改动。
- 明确受限recovery_required及MFA条件分支、操作绑定reauth与当前角色/因子/epoch、Cookie/remember/CSRF/Origin/WS及名单暂停；追加两个仅自动恢复接收路由，不公开manual issue/complete/status/根维护。
- 独立identity-control.schema.json（协议版本1）表达D0安装/变更、issue/manual complete/status及接收端PoP；根/当前高水位/撤销、四时间、单次能力和unknown规则不由JSON结构冒充机制证明。
- 统一HTTP session/reauth token生产编码与控制Secret桥接包络，明确至少32随机字节及canonical unpadded base64url；status独立于目标当前role/epoch和旧写证明/核验详情。authenticate/key-PoP的准确签名body及deployment来源由schema和协议固定。
- domain-model保留schema_version文档格式1.0.0，contract_version=0.2.0，最小身份逻辑增量和不变量不等于已创建迁移；原非身份表、事件schema、五流和DAG不改。
- 新增schema正反fixtures保留原34条；新增错误/状态及Reauth/MFA请求是严格消费者兼容变化。旧未知状态拒绝，不能映射authenticated；同版本前后端联调窗口与禁止混合启用见[S2兼容](../../pr03b/s2/COMPATIBILITY-AND-HANDOFF.md)。
- 原rotateRecoveryCodes operationId/路径保留，完整重登后显式首次生成/轮换；manual final无码，CLI/status/旧cap不领秘密；秘密一次展示和丢响应不回放。
- RecoveryCodes结构要求成功组至少1个且无重复（minItems/uniqueItems），maxItems20保留；每组10及生成编码仍是待实测配置，不以结构约束冒充熵或单次消费验证。
- Reauth双密码JSON转义上限核验后仅该路由提出32KiB技术候选，其他auth16KiB；S1原样阈值不被改写，须独审及S3边界验证。

生产者Codex提交候选；Claude消费者确认与Astra独立契约审查均为S3前门禁，未伪称已取得。后续生成类型、Mock、客户端/后端/增量迁移必须在明确实施授权下同步；本轮不生成或修改代码/测试/Mock/依赖。不能将静态fixtures通过写成真实PG/HTTPS/WS/身份/秘密安全通过。U02真实留存未定时不冻结或启用对应真实协议。

### 0.2.0 补充 · 2026-11-12 · PR-03b 复审修复（行为说明，结构与版本不变）

来源：2026-11-12 Astra 对 PR-03b 分支的复审发现。AUTH-01/03/04。OpenAPI 文件、生成类型、fixtures 均未改动，版本保持 0.2.0；以下是同一结构下的行为变化，待 Astra 重审确认。

- `logout`（POST /auth/logout）：请求没有会话 Cookie 但带有待完成的自动恢复上下文及其 recovery 作用域 CSRF 时，作废该恢复流程并清除恢复 Cookie，响应结构不变。此前这种请求一律 403，浏览器丢弃接收端密钥后会被固定在恢复作用域直到流程过期。已消费的恢复码不恢复。消费者影响：前端在恢复页“重新开始登录”时先调用它；旧客户端不受影响。
- `listSessions` / `listDevices`（GET /me/sessions、/me/devices）：结果固定不超过契约的 maxItems=100，当前会话或设备优先，其次是仍有效的，再按创建时间从新到旧；`next_cursor` 仍为 null。超过 100 条的更早历史暂不可见，完整游标分页未实现。此前超过 100 条时响应校验失败返回 503。
- 登录、MFA 与再认证的失败预算数值不变（密码 3 次/900 秒，MFA 5 次/900 秒）。计数改为验证前预留、凭据错误才保留，因此同一账号并发中的尝试会计入预算，超出部分即时返回 429 `RATE_LIMITED`。
- 数据迁移：0007、0008 的函数定义在未部署的分支上原地修改，没有新增迁移版本；已应用旧定义的数据库不能靠重跑迁移获得新定义。

## 0.1.0 · 2026-10-28 · PR-01设计，整体架构原则获批

用户已接受ADR选定的系统结构与技术栈，授权独立本地commit；11项TBD中的实验/产品参数继续开放。见[批准记录](../PR01-CLOSEOUT.md)。此次收尾只更新批准状态与验证工具，不改变本版机器契约内容或版本。

首次建立 `/api/web/v1` OpenAPI3.1、WS/事件JSON Schema、领域模型、合成正反例和28个PR依赖。尚未发布、部署或供真实客户端调用。数据库逻辑模型自身schema_version为1.0.0，表示模型文档格式；不等于数据库migration revision或产品软件版本。

冻结语义：显式class_session_id、服务端owner、稳定op/指纹、CAS、来源固定版本、云durable与ASR分开、unknown不自动收费重放、首稿候选显式采用、三池缓存、墓碑优先、双账本。PRD增补classroom_id统一映射class_session_id，不新增产品实体。

设计复审修正已纳入本首版：秘密响应不得存通用收据；plan单次消费防换op重复执行；同步一致快照与owner提交序号；typed冲突解决；无final时的时间点标记；长课区间manifest；纯音重转写可空snapshot；导入来源时间可未知；课程大纲词表聚合版本。

今后变更每条必须记录日期、需求ID、旧/新版本、受影响operation/event/schema、兼容性、数据迁移、Mock/客户端影响、Astra审查与Claude/Codex确认。破坏性变化附升级窗口；产品政策变化单独记录用户批准。不得只改生成的前端类型或Python实现而不更新规范和fixtures。
