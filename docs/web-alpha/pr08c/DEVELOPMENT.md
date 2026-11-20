# PR-08c 开发日志

- 2026-11-18，远端 main 基线 `01a8ab833ecd4aee21081bebb8b93bd86bb9996a`。GitHub #16、#15、#20 均已合入，也通过本地祖先检查。续接分支 `feature/web-pr08c-portable`。负责人林珈诚。
- 冻结 OpenAPI 使用 `upload_id`、`destination_course_id`、`archive_sha256` 和标准 Task DTO。Task 称等待状态为 `queued`。私有下载使用现有 MediaAsset + ticket，没有公开永久地址。
- 旧 ZIP 在部分媒体损坏时可能降级。Web 按本次要求直接拒绝完整性错误，仅接受明确记录的缺失或未携带状态。ZIP 中旧离线阅读器文件只参加完整性校验。
- 07b 的 `review_revisions` 仅接受有正式生成任务和快照的 `ai_candidate`，以及无结构化内容的 `personal`。导入旧候选需要真实表达导入血缘的专用持久模型或兼容扩展。不能伪造任务或采纳。PR-02a 还约束采用的转写版本指针为 NULL。
- TBD-06 仍未签署。产品容量、临时资源寿命及保留策略留待批准。实验入口默认关闭。

## 实验入口

`PortablePreview(MediaService(..., expire_import_packages=True))` 只接受隔离的 loopback 合成 PostgreSQL 与私有 S3，在 ASGI 工厂中显式注入，且须同时注入同一 MediaService 和现有删除服务。一致性与回收沿用媒体上传状态、对象 ETag/version、owner RLS、墓碑、删除账本、定时扫描和实际物理擦除。未经设置的应用工厂没有预检路由。

建议的合成起点为 8,000,000 bytes 上传、16,000,000 bytes 展开、2,000,000 bytes JSON、256 个文件、单文件压缩比 100、单图 4,000,000 像素、总图像 12,000,000 像素。每进程同时预检 2 个，每 owner 最多 4 个未清除的暂存件，每件最多 4 个预检回执。暂存延用合成 MediaPolicy 的上传期限（一小时），期限过后先挡访问，物理删除按现有 worker 继续。输入限制依据为单次全量验证成本、单进程峰值和现有媒体外围可承载的大小，尚未做正式用户容量实验。TBD-06 的容量、保留期、出口寿命与音频导出策略仍需批准。

## 缺口与回退

- 本候选交付旧包检验、`/imports/preview`、实验上传边界与清理。校验期间仅向通用操作回执写入有限的预检 DTO；未创建课堂、Review、计费任务或下载授权。
- `/imports/commit` 尚缺正式转写版本、历史图片与 Review 导入血缘的写入，及配套的导入 ID 映射、事务发布和删除恢复竞态验证。设计模型有 `transfer_jobs` 和 `import_id_maps`，当前 Alembic 链尚未实现；下阶段需选择可同时被 Review、Sync 和删除生命周期识别的导入来源类型。
- `/exports` 尚缺独立无计费 Worker、Task 结果与私有下载资源的绑定，以及从单节到课程、账户聚合范围的全量血缘撤权。仅将产物挂到一个课堂不足以撤销多个来源的聚合导出。本候选保持这些入口关闭。
- 当前未改 HTTP/WS 冻结文件，也未新增 DDL。回退实验配置时停止注入 PortablePreview，并停收该批新暂存。已有暂存继续依照已登记期限和删除账本清理。基线媒体处理与现有课堂继续可用。

## 开发验证

- VMware Windows 11、真实 PostgreSQL 17 与私有 SeaweedFS。`pr08c-target-01` 22 项通过；`pr08c-target-02` 24 项通过，包含旧 Windows 导出器实际生成的 ZIP、跨所有者、并发同键预检、重启重放、课程删除和暂存清除。命令、JUnit 和源文件哈希见 `outputs/pr08c/`。

## Astra R0 修复记录

- 复审范围为基线 `01a8ab833ecd4aee21081bebb8b93bd86bb9996a` 到冻结提交 `13c454c5109442dc21d164a96c9c273a92db6bc2`。Astra R0 完成，P0/P1/P2/P3 为 0/1/1/0。
- P1 成立。旧版 `plan_batches()` 在 `evidence_subset()` 的快照哈希后添加三项批次上下文。批次按原版哈希投影验签，并核对上下文引用与父快照内容。卡片等原本包含这些字段的全量哈希仍按原语义校验。黄金测试加入旧版实际批次生成器。
- P2 成立。畸形 `selected_note_id` 和不可解码 ZIP 文件名分别在类型和归档边界返回确定性的 422，测试覆盖两条路径。历史课件版本额外拒绝重复版本或倒挂版本。
- R0 审查进程自行运行过一组宿主只读探针，这些结果未计入项目验证。项目回归证据来自 VMware 来宾及独立 GitHub CI。


## 2026-11-20 · TBD-06 v2.1 探索候选

- 续接 Draft #22，原 head 为 `20085ec5444efc3cac85da8ed6c13add0b97807f`，分支合流点为 `01a8ab833ecd4aee21081bebb8b93bd86bb9996a`。本轮查得远端 main 为 `db0bc8337787cc89a11a989b125d22ce28d0e01d`，其新增提交不包含 Alembic 变更。保持 PR-08c 独立提交。
- [字段级契约](TBD06-V21-TRANSFER-CONTRACT.md) 提交 `da03efd1ecc39e6cf9f373daec6239256b3178d0` 经 Astra 静态复审，P0/P1/P2/P3 为 0/0/0/0。报告位于 `outputs/astra-review/20261120T183709Z-commit-da03efd1ecc39e6cf9f373daec6239256b3178d0-r0-7u9BJV/review.md`。此次覆盖该契约提交。之后的实现和聚合包形状待复审。
- Alembic `0015_portable_lifecycle` 接在 `0014_offline_sync` 后。新增 owner RLS 表和受限维护身份，两个暂存池各计 5 GB，以数据库事务决定准入。撤权及到期后复用 04b 账本及对象版本擦除，确认已清除后才回收预留。维护入口返回超过 24 小时的告警数和超过 72 小时的事故数，探索环境由测试显式驱动。
- 候选 ZIP 写入器接受固定版本、已授权的课堂快照。根 `aggregate.json` 携带 `schema_version=1`、`format=classroom-assistant-aggregate`、`scope`、`sessions`，每个会话目录包含旧 schema-1 的 `manifest.json` 和 `session.json` 及图片。生成前估算边界，实际生成后由同一导入解析器复核，私有对象的版本和字节再次核验后才生成候选下载能力。该 ZIP 布局属于实现实验。
- 新服务仅接受隔离的合成 PostgreSQL 与私有 loopback S3。正式 HTTP/WS 契约和既有入口沿用。§8 探索直接调用候选服务。正式 `/exports` Task/Attempt/Outbox 挂接、封口异步 202 状态、HTTP Range 206 集成、撤权与物理删除后台接线、全来源的原生快照采集和 `/imports/commit` 独立课堂持久化，仍属于 PR Draft 阻断。按停用候选调用方回退，保留前向迁移和删除账本。尚无生产启用，后续须在改造后台前保留受控暂存的清理能力。
- VMware 首轮后端为 1 通过、2 失败。候选维护函数的固定 search path 缺少受保护的 public schema，使 05b 现有媒体触发器无法解析 capture 表类型。仅修复新函数的 search path，未修改 05b 触发器。第二轮 3 通过。浏览器探针两次测试夹具错误均保留日志，修复路径解析及 grant 克隆后在真实 Chromium 验证通过。此探针调低真实 origin quota，尚未测 1 GB 近满池性能。05b/08a 产品文件均未更改。
