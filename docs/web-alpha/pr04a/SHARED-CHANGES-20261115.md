# PR-04a 共享改动与阶段交接

负责人为林珈诚。基线与当前 HEAD 均为 `81ab191b1f33de2371567eccd0ef9589d12483c2`，工作树为 `J:\class-assistant\worktrees\web-pr04a-storage`，分支为 `feature/web-pr04a-storage`。精确文件及 hash 快照见 [BASELINE-20261115.json](BASELINE-20261115.json)。

契约使用 OpenAPI 0.2.0，SHA-256 为 `7432ba27581f3d44690138b5606ec61c4008579cb049a6c2dfc20737ea3a6a7a`。本包新增迁移候选 `0009_private_media`，down_revision 为 `0008_manual_recovery`。

## 共享文件清单

| 文件 | 已有改动 | 后续计划及接入要求 |
|---|---|---|
| `web/backend/classroom_web/entrypoints/http.py` | create_app 追加可选 media/media_spool_directory，并按需注册独立 media_http 模块 | 保留认证与现有业务调用参数兼容；其他包按同样可选注册接入，由协调聊天确认顺序 |
| `web/backend/classroom_web/infrastructure/database.py` | 运行角色安全检查增加三张媒体表 | 与 PR-11a 的表清单增量合并，保留 authorized_transaction 语义 |
| `web/backend/pyproject.toml` | 增加 boto3 1.44.3 | 与其他包依赖增量一起检查解析结果 |
| `web/backend/requirements.lock` | 增加 boto3/botocore/jmespath/s3transfer/python-dateutil/six/urllib3 共七个固定依赖 | 保留现有 pin，共享依赖冲突由协调聊天统一决策 |
| `web/backend/tests/integration/conftest.py` | 真实对象服务测试通过 PR04A_OBJECT_INTEGRATION 显式启用 | 现有全部测试保留；PR-04a runner 的 full 模式包含该对象服务套件 |
| `web/backend/tests/integration/test_authentication.py` | 原迁移测试将 head 固定为 0008 | 新增 0009 后更新精确 head，追加 0008→0009 祖先断言，保留认证表 FORCE RLS、角色与重复升级断言 |
| `web/backend/tests/integration/test_data_foundation.py` | 原完整表清单尚无媒体三表 | 精确增加媒体三表，原 catalog 元数据逐字段检查保留；新媒体 SQL 表由本包独立列清单与真实约束测试校验 |
| `web/backend/classroom_web/infrastructure/task_schema.py` | 回执 resource_type 的 ORM CHECK 仍为旧枚举 | 同步迁移已经增加的 media_asset/media_upload，任务写入行为保持原语义 |
| `web/backend/migrations/versions/0009_private_media.py/.sql` | 新增三张媒体表、RLS、不可变对象身份、媒体回执资源类型 | 候选 revision 先登记，其他包确认后按单链顺序接入 |
| `docs/web-alpha/architecture/contracts/` | 当前无改动 | Range 206/416、票据重复读取、缓存头细节形成 PR-04a 候选增量，协调接入及契约审查后更新冻结源 |

媒体独立源码包括 `application/media.py`、`domain/media.py`、`entrypoints/media_http.py`、`infrastructure/media_repository.py` 与 `infrastructure/object_store.py`。独立工具与测试位于 `tools/pr04a/`、`tests/integration/test_private_media.py` 和 `tests/unit/test_media_ranges.py`。

## 阶段一交接

- 已实现上传创建、流式字节上限、hash/签名核验、私有对象条件创建、数据库确认、下载票据、Range 与逐块再授权，以及有界恢复扫描。代码仍在验证和整改中。
- VMware 首轮定向测试为 9 failed/15 passed，修复 SQL 绑定后为 8 failed/16 passed。后续对象服务写入探测遇到超时，原日志保留。测试创建的数据库和对象服务进程均由 runner 清理。
- 宿主静态契约检查 9 项通过。运行测试全部使用 VMware Windows 来宾、PG17.11 与 SeaweedFS 4.53，当前成绩尚不足以交付验收。
- 未完成对象服务就绪修复、完整故障/授权矩阵、迁移兼容与后端回归、候选契约及独立审查材料。
- PR-11a 需要协调数据库表安全清单、迁移顺序及依赖锁。PR-05a 的缓存密钥 POC 使用自己的密钥封装及浏览器边界，媒体 opaque ticket 使用独立表。PR-09b 当前前端工作树独立推进，媒体 API 接入及真实联调按后续合同交接。

用户已要求与协调聊天确定共享文件顺序。共享文件后续修改先在本清单登记并协调，独立媒体源码和 VMware 测试准备继续推进。

## 协调确认

协调聊天“评估04a、11a、05a并行开发”已于本轮确认，04a 现有 HTTP 注册、数据库表安全清单、依赖增量和 conftest 接入作为首批候选继续完善验证，接入顺序先 04a。后续 11a/05a 的交叉增量等其初始清单到达后安排。

`0009_private_media` 登记给 PR-04a，当前前置保持 `0008_manual_recovery`。其他包的 revision/down_revision 和组合链等其迁移清单明确后统一整理。Range、票据复用与缓存头继续按候选契约及既有审查流程推进。

内部模型复核发现，媒体表初稿采用了对外 DTO 的 byte_length/state。拟在尚未发布的 0009 候选中对齐冻结 domain-model.json 的 size_bytes/status，内部状态使用 staging/verified/committed/delete_pending/delete_failed/deleted，repository 映射到现有 HTTP DTO。补齐 confirmed_at、access_revoked_at、first_cloud_received_at 和保留策略字段及既定索引，object_etag 为额外实现字段。retention_policy_version 采用明确未分配标记，音频提交必须具有后续服务端批准的期限策略。独立媒体数据库断言将直接读取冻结模型核对列和可空性，避免由实现自行复制期望值。此修正保留 HTTP 字段与原冻结契约，由协调聊天确认迁移配套顺序。

阶段二的首轮定向结果为 24 passed，0 failed/error/skipped，VMware 来宾 run 为 `458109b5a11341b2833880823da57d6f`。该轮 PG stop=0，对象服务进程已停止。之后新增公开桶策略、损坏对象、迁移升级和浏览器用例，须重新运行。

## 最终阶段交接

以下为首次候选的阶段记录。后续 R1 复审增量已登记，见 [R1 修复交接](R1-REMEDIATION-20261115.md)。本轮生产代码仅修改 media_http.py，配套 conftest 对象测试显式启用和 submit/run_guest 的 r1 定向入口；契约、迁移及其他共享基础设施保持独审基线。

冻结模型对齐后的 full 快照 `0c3b7dc3…` 已通过 619 主＋29 子测试，补充上传上限后的 `840bb525…` 通过 38 项定向，均为 VMware 实际结果，失败/错误/跳过均为 0。PG stop=0，对象进程已停止。两快照应用代码和迁移相同，唯一差异为媒体测试新增边界用例。

证据归档已回收并核对 75 个文件哈希，完整状态及源码/run/命令见 [验证记录](VALIDATION-20261115.json)，跨包接口、未完成项与当前未提交状态见 [最终交接](HANDOFF-20261115.md)。正式契约仍为 0.2.0，0.2.1 候选按原有审查流程推进。
