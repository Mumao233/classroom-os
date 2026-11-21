# PR-06c · Filetrans 与候选采用

负责人为林珈诚（GitHub `Mumao233`）。基点为 `fe4aafc1ee0a47e627b80fdaab1e2bdb688d5664`（PR-06b，GitHub #27）。本轮覆盖 ASR-03 与 A6 的合成路径。生产入口默认关闭，真实 Filetrans 独立 POC 待林珈诚另行授权。沿用 [规划](../architecture/08-IMPLEMENTATION.md)、[接口与数据流](../architecture/04-CONTRACTS-FLOWS.md)、[PR-11a 费用](../pr11a/README.md) 和 [PR-06a 适配器边界](../pr06a/BOUNDARY-20261117.md)。

## 计划与私有原音

- 使用冻结的 generation-plans 路由和 DTO。prepare 从服务端 owner 上下文读取本课，在结束或已封存的退休采集 epoch 上解析有效的 `all` 或明确的 `range`，按真实映射与可用片段确定覆盖。无法完整覆盖必须在 prepare 和 execute 分别确认。既有 `source_snapshot` 为 null，`source_status` 和明确的错误消息说明原音变化、过期或删除。
- 服务端用排序确定的清单计算 SHA-256。清单含每个分片 ID、持久回执、采集 epoch、资产 ID、哈希、大小、样本位置、选取区间、期满时间、采集 timeline offset、对象 ETag/版本和对应 capture seal 声明。响应中的 `audio_chunk_ids` 保留相同顺序。只接受 PR-05b 持久回执及有效恢复授权所确认的分片；已排入上传队列的分片不足以授权。prepare 冻结配置、价格版本、最高费用、配额单位和清单。十分钟计划期限仍受每片更早的保留期限约束。
- 新迁移 `0018` 允许共享计划存储中的 Filetrans 计划使用空文本快照。计划哈希包含原音清单哈希；数据库保护不可变列、owner/课堂外键、消费关系及作用域。共享 QA/Review 保留既有非空快照规则、路由和响应。没有需要第 7 节契约变更的字段。
- execute 核对提交内容、当前合成配置、原音及价格。按课堂串行首次准入，有持久初次执行唯一约束。重复操作按已有收据处理，重复确认同一计划沿用原任务。超次数返回 `QUOTA_EXCEEDED` 并提示审批；例外仅由独立受限费用运维身份录入绑定 owner、课堂和计划的一次性、限时审批记录，运行身份只读。审批不免月额度或预算。未知重试同时要求该例外、冻结 RetryRequest 确认、匹配原 unknown attempt 以及独立新预留；同一 unknown task 只可产生一个直接后继。合成审批接口仅为内部数据库入口，无新增浏览器管理 API。
- execute 与 retry 在同一事务内建立任务、预留、额度分配、事件、收据和一次计数。额度由 PR-11a 的 `retranscribe_month_ms` 管理，10 人阶段 8,640,000 ms，30 人阶段 12,960,000 ms。保守时长按选中原音的样本数向上取毫秒。金额由已生效的合成价目与独立 `filetrans` 池计算，使用 `asr_filetrans` 内部开关。

## 派发、恢复与删除

- 新 worker 只领取 PR-06c schema。独立文件作业适配器以一次批量提交处理固定原音片段，无 WebSocket、Streaming 帧、SDK、HTTP 客户端或真实供应商配置。用私有的 loopback 合成对象存储实例读固定对象版本，在发送意图前按块核验完整对象字节数与哈希，批作业只接收选中 PCM 区间的字节所计算的合成输入。各 epoch 独立保留偏移与空洞，按实际选中样本总和计费。虚拟 provider 返回显式成功、已知失败、送达不明或超时。成功结果包含原位置与来源；合成输出使用 capture mapping。不同片段之间保留采集空洞。
- 私有读使用受限生命周期，提交发送意图前再次核对当前授权、语义不可变的冻结源、04b revocation、过期、租约及 PR-11a 预留。启用时共享 04b DeletionService 的恢复对账 gate，领取、读取、发送和发布均检查 ready。对课堂使用专属事务 advisory lock 后获取既有授权共享锁，按资产 ID 取锁，不升级课堂锁，再使用账本的预算、任务、费用锁。提交费用许可与原 attempt 后才进入适配器。提交与删除以数据库授权边界排序。晚到结果单独再次检查 owner、课堂和音频删除状态；已删范围只允许最小费用元数据结算，禁止写正文、版本或正文事件。自然过期关闭新派发，已经授权的正常结果可在课堂仍有效时结算。
- 结果与候选在一个受租约保护的完成事务内落库。已知失败由 task attempt 与费用回执共同记录，缺正式费用证据保留 hold。产物可以先于费用结算完成。本地校验失败发生在发送意图前，按明确未提交收口；可能送达的异常和超时原样进入 unknown。进程退出后回收按现有 fence 和提交标记判断；已提交且没有可信结果则 unknown。原费用证据持续保留，查询不支持时呈现原状态。关闭工作包入口或费用开关阻断新派发，撤销未送达预留按既有账本处理，历史版本及费用继续读取和核对。

## 版本和 PR-10b 接口

- 独立候选与片段为追加式记录。现有 transcript-versions 分页与 06b 版本共同排序，segments 保留冻结形状；候选为 `kind=retranscription`、`state=sealed`，片段为 `origin=retranscription` 且 `stream_id=null`。使用 task.changed 触发重新拉取；实时 ASR 事件语义保持。
- 接入冻结的 `POST /class-sessions/{id}/adopt-transcript`，接受同课堂可见的已封存版本 ID 以及课堂 `base_revision`，复用 catalog CAS 和 operation receipt。变更只移动课堂采用指针和递增课堂修订。未采用的候选、原实时版本、个人修订和既有 Review 快照分别保留。课堂读取、bootstrap 和 object.changed 同步采用指针或 revision；新 QA/Review 与导出挑选当前正式机器版本，并保留独立人工来源和历史冻结快照。候选学习源仅在被采用后进入新材料选择。跨 owner/跨课堂版本 404，CAS 冲突 409。音频软删除及自然过期后可保留已建成的课堂文字；课堂或祖先级删除撤销正文读取。
- Review 使用既有版本/片段接口比较，使用 `Task.attempt_id` 与 `may_have_charged` 展示未知风险。发起与正式采用都是显式操作。前端 PR-10a/10b 无生成物改动。

## 验证

VMware Windows 来宾执行真实 PostgreSQL、私有 SeaweedFS 与合成 Filetrans 定向测试，覆盖持久确认、冻结、正常完成、已知失败、unknown、超时、过期、额度不足、重复提交、审批、删除竞态、owner 和 CAS。再执行完整 Web 后端回归与 Windows 本地 Python/合成 DPAPI/九套 Node。记录精确源提交、命令、版本、失败或跳过、退出码和 SHA-256。Astra 设计审查先于实现，实施复审最多两轮修复，P0/P1 关闭后才发布 Draft PR。
