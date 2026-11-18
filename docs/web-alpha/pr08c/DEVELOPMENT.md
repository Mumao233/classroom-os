# PR-08c 开发日志

- 2026-11-18，远端 main 基线 `01a8ab833ecd4aee21081bebb8b93bd86bb9996a`。GitHub #16、#15、#20 均已合入，也通过本地祖先检查。续接分支 `feature/web-pr08c-portable`。负责人林珈诚。
- 冻结 OpenAPI 使用 `upload_id`、`destination_course_id`、`archive_sha256` 和标准 Task DTO。Task 称等待状态为 `queued`。私有下载使用现有 MediaAsset + ticket，没有公开永久地址。
- 旧 ZIP 在部分媒体损坏时可能降级。Web 按本次要求直接拒绝完整性错误，仅接受明确记录的缺失或未携带状态。ZIP 中旧离线阅读器文件只参加完整性校验。
- 07b 的 `review_revisions` 仅接受有正式生成任务和快照的 `ai_candidate`，以及无结构化内容的 `personal`。导入旧候选需要真实表达导入血缘的专用持久模型或兼容扩展。不能伪造任务或采纳。PR-02a 还约束采用的转写版本指针为 NULL。
- TBD-06 仍未签署。产品容量、临时资源寿命及保留策略留待批准。实验入口默认关闭。
