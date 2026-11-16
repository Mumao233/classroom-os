# PR-04a 私有对象存储与密钥边界 · 开发准备

日期为 2026-11-15，时区 America/Los_Angeles。负责人为林珈诚，后端实施由 Codex 承担，Claude 正在同步开发 PR-09b。

本次交付为开发准备。下一阶段按下列范围实施。工作包编号为 PR-04a，GitHub PR 编号待实际创建后记录。

## 1. 已核对基线

| 项目 | 本次观察 |
|---|---|
| 正式仓库 | `J:\class-assistant\classroom-os` |
| 本地分支和 HEAD | `main`，`81ab191b1f33de2371567eccd0ef9589d12483c2` |
| 远端 main | `git ls-remote origin refs/heads/main` 返回同一 SHA |
| 前置工作包 | PR-02b、PR-03b 核心包已整合，依据当前 Web 总入口及 PR-03b 收尾记录 |
| 冻结契约 | OpenAPI `0.2.0`，SHA-256 `7432ba27581f3d44690138b5606ec61c4008579cb049a6c2dfc20737ea3a6a7a` |
| 数据库迁移末端 | `0008_manual_recovery`，后续迁移实施时再次核对单链 head |
| PR-09b 工作树 | `J:\class-assistant\worktrees\web-pr09b-courses`，`feature/web-pr09b-courses`，同一 HEAD；初查干净，结束复核时已有前端修改 |
| 主仓既有未提交内容 | `CLAUDE.md`、`.claude/`、`docs/web-alpha/evidence/DEFENSIVE-PROGRAMMING-REVIEW-20261115.md` |
| 本次文档位置 | 主仓独立 `docs/web-alpha/pr04a/`，准备记录采用现有 checkout |
| 后续实施分支 | 拟用 `feature/web-pr04a-storage`，实施前建立独立 worktree 并复核共同基线，分支和工作树尚待创建 |

依赖定义见 [实施图](../architecture/08-IMPLEMENTATION.md) 与 [机器 DAG](../architecture/contracts/pr-dependencies.json)。当前整合事实见 [Web 总入口](../README.md) 和 [PR-03b 收尾](../pr03b/INTEGRATION-20261115.md)。PR-03b 延后的人工恢复 M0–M4、容量统计及权限补审限制继续按该收尾记录管理。

## 2. PR-04a 实施范围

1. 建立纯领域 ObjectStore 端口、服务端对象键、私有存储适配器及合成环境配置。SDK 依赖限定在基础设施层。
2. 实现上传创建、流式内容接收、hash/长度核验和完成确认，持久记录 staging、对象版本及清单。正式可用状态在对象核验与数据库提交完成后产生。
3. 实现应用内媒体访问票据与下载代理。每个请求及 Range 请求核验当前会话、owner、资源版本、用途、祖先撤权及期限。
4. 建立服务端密钥注入、用途隔离、错误脱敏和配置校验。对象存储凭据、provider key、签名材料和含票据 URL 的日志须有可检验的泄漏防护。
5. 实现对象写成功而数据库失败、提交后 ACK 丢失、重复/并发 complete、重启扫描及孤儿对象恢复规则，保留唯一副本与可追溯状态。
6. 为 PR-04b 提供对象版本/暂存清单、访问撤销检查及受限清理接口，为 PR-05b 提供持久性边界。

后续包分别负责删除期限与备份恢复（PR-04b）、浏览器缓存密钥安全 POC（PR-05a）、采集租约及音频传输（PR-05b）、真实 ASR 接口验证（PR-06a）和包导入导出流程（PR-08c）。PR-04a 的媒体类型兼容这些能力，端到端验收仍归对应包。

需求追溯至少覆盖 AUTH-05、DATA-01、A10 和 DEV-03，详见 [需求矩阵](../REQUIREMENTS_MATRIX.md)。设计依据为 [ADR-07](../architecture/07-ADR-REGISTER.md)、[数据模型](../architecture/03-DATA-BACKEND.md)、[HTTP/WS 契约说明](../architecture/04-CONTRACTS-FLOWS.md) 与 [私有媒体安全边界](../architecture/05-SECURITY-OFFLINE.md)。

## 3. 现有入口与实现接缝

下表的 application、infrastructure、entrypoints 与 domain 模块位于 `web/backend/classroom_web/`，迁移位于 `web/backend/migrations/`。

| 位置 | 本次核对结果与下一步 |
|---|---|
| `web/backend/classroom_web/domain/ports.py` | 当前为 catalog、tombstone 端口。媒体端口宜独立增加，复用 OwnerContext 与现有授权规则 |
| `application/`、`infrastructure/` | 尚无媒体应用服务、媒体表映射和 ObjectStore 适配器，新增模块按现有分层组织 |
| `infrastructure/database.py` | 已有 owner UoW、会话授权事务及运行角色检查。媒体接入需覆盖新增表的角色安全检查、原事务语义及撤权竞态 |
| `entrypoints/http.py` | `create_app` 要求契约 0.2.0，现有 JSON body 限制为认证/业务请求设计。二进制上传需独立有界流处理，同时复用 HTTPS、Origin、CSRF、会话入口 |
| `infrastructure/task_schema.py` | operation_receipts 资源类型 CHECK 当前仅接受 task、semester、course、class_session、course_material_version，媒体引用扩展需要增量迁移 |
| `infrastructure/task_events.py` | 已有 `lock_and_get` 幂等串行化，写入入口为 `store_task`。媒体增加专用最小回执，敏感票据响应按重新授权签发语义处理 |
| `migrations/versions/0007_authentication.sql` | 实际认证挑战 kind 集合尚无媒体票据。需在媒体票据设计中确定复用方式或专用表，并验证用途隔离与会话撤销 |
| `migrations/versions/0008_manual_recovery.py` | 当前末端；新迁移使用增量方式，结合 PR-09b 实际迁移变动确定 down_revision |

`web/backend/README.md` 开头仍保留 PR-02 阶段的契约 0.1.0、HTTP 尚未实现描述。接手以当前源码、OpenAPI 与 PR-03b 收尾为准；PR-04a 实施时仅修订直接相关的后端接入说明。

### 已有公开接口

路径相对 `/api/web/v1`，取自 [OpenAPI](../architecture/contracts/openapi.json)。

| 方法与路径 | 既有 DTO / 语义 |
|---|---|
| `POST /media/uploads` | MediaUploadCreate → MediaUpload，Idempotency-Key 与 CSRF |
| `PUT /media/uploads/{upload_id}/content` | application/octet-stream → MediaUpload，独立流式字节上限 |
| `POST /media/uploads/{upload_id}/complete` | MediaVerify → MediaAsset，重算实际 hash/长度 |
| `POST /media/{asset_id}/access` | Empty → MediaAccess，重新授权并签发，敏感响应脱离通用回执 |
| `GET /media/{asset_id}/content?ticket=...` | 当前会话与短期票据共同授权，代理返回二进制 |

上传 purpose 当前为 `slide`、`import_package`。MediaAsset kind 另含 `audio_chunk`、`export`。上传状态为 staging/uploaded/verified/rejected，资产状态为 staging/available/access_revoked/pending_delete/delete_failed/deleted。实现应分别维护这两组状态。

### 编码前需要收口的契约细节

- 下载说明要求每次 Range 授权，但 OpenAPI 当前只有 200/default，Range、206、416 及 Content-Range 等字段待补齐。需记录支持的区间形式、非法范围响应和断流行为。
- 安全说明要求 `private, no-store`，现有 OpenAPI 成功头为 `const: no-store`。须统一规范与断言的精确值。
- 数据设计将媒体票据列入认证挑战并描述单次消费，媒体 Range/重复读取如何重新签发或复用需明确，避免实现产生相互冲突的规则。
- 上传/下载容量、并发上限、暂存期限和票据有效期需形成参数表。TBD-06 的产品额度与技术实验初值分别登记。

以上按仓库既有契约协作流程提交版本/CHANGELOG、兼容性、生产者/消费者影响和正反 fixtures，由 Astra 审查并交 Claude 对齐。准备阶段保留候选事项，冻结文件仍使用当前版本。

## 4. 与 PR-09b 的并行安排

| 区域 | 拟定协作方式 |
|---|---|
| PR-04a 新增媒体 domain/application/infrastructure、媒体测试、专属文档与工具 | Codex 独立工作树实施 |
| PR-09b Dashboard、Courses、Settings、页面状态及前端 Mock | Claude 使用现有 `web-pr09b-courses` 工作树 |
| `entrypoints/http.py`、`infrastructure/database.py`、共享 repository 和依赖锁 | 每次修改前比对两个工作树最新差异，明确单个文件修改者；新媒体模块尽量独立，注册接入集中收口 |
| `migrations/versions/` | 实施前再次核对两个分支的 revision，确定单链顺序，避免双方占用同一编号 |
| `architecture/contracts/`、生成客户端类型、公共 fixtures | 统一契约变更记录及审查后同步消费者 |
| `docs/roadmap/phases.json` 与生成页 | 在实际阶段状态确认后集中更新 |
| VMware broker、来宾源码、数据库、对象存储和端口 | broker 队列串行提交，任务采用独立运行 ID、目录、数据库和对象前缀 |

这是基于源码观察提出的协作安排，Claude 对本表的确认待后续交接。结束复核时 PR-09b 已修改 `web/frontend/src/api/client.ts`，新增 `web/frontend/PR09B-SCOPE-GAPS.md`。该清单明确当前写入范围为 `web/frontend/`，未实现的业务端点采用 Mock，真实联调仍需后续验收。两包的硬依赖均已具备共同主线基础，PR-09b 与 PR-04a 可并行推进。

PR-09b 清单提出的课程材料初始 revision 问题，可由现有 `web/backend/README.md` 与 catalog 实现核对，当前设计为创建课程时生成空材料 v1。其余课程、Dashboard、设置所需后端缺口应按相应工作包分配，PR-04a 排期集中于媒体能力。

## 5. 实施顺序与交付物

1. **契约与参数细化**。完成上述媒体契约接缝、状态转换、ObjectStore 能力表、开发存储选型与合成限额。开发候选为 VMware 内 S3 兼容私有存储，具体实现和版本根据 Windows 来宾可用性锁定；生产按 ADR 首选北京地域。
2. **持久模型与端口**。媒体资产、上传清单、对象版本和票据存储的增量迁移，owner 复合 FK、FORCE RLS、只授予必要权限，保留既有数据及约束。
3. **上传及恢复**。实现 create/put/complete、内容校验、不可变对象身份、并发幂等、对象与 DB 不一致恢复。明确“可用”的提交点、返回结果及恢复路径。
4. **访问与密钥边界**。实现短票据、媒体代理、Range、会话/墓碑再检查、服务端配置与脱敏。上传对象默认私有，按资产身份授权。
5. **VMware 验收及评审交付**。运行真实 PG 与兼容对象服务故障矩阵，补齐 HTTP/浏览器证据，执行受影响回归，记录版本、源码 SHA、实际命令/退出码、失败和开放项。

每阶段产出集中在 `docs/web-alpha/pr04a/`，任务材料置于 `work/pr04a/`，执行证据置于 `outputs/pr04a/`。回退采用停止新上传与票据签发、保留对象清单/已确认副本及兼容读路径的方式；已写入数据通过前滚修复维护一致性。

## 6. 验收矩阵

| 检查组 | 必测行为 | 所需环境 |
|---|---|---|
| 私有存储 | 匿名拒绝、最小读写权限、对象键不可由文件名穿越、版本定位和完整性核验 | VMware 内真实兼容对象服务 |
| 上传闭环 | 三步上传、重复与并发请求、不同 hash 同键、长度不符、MIME 异体、半途断开、超限 | VMware 内 HTTP + PG + 对象服务 |
| 持久性 | 对象成功 DB 失败、DB 提交后 ACK 丢失、重启恢复、临时对象引用竞态、唯一副本保护 | 故障注入及独立进程重启 |
| 授权 | 用户 A/B、Admin、已撤销会话、禁用账号、祖先墓碑、旧版本及过期资源 | 真实会话 + FORCE RLS |
| 下载/Range | 完整与部分读取、非法/越界范围、过期/撤销后新请求、流中撤权检查点 | HTTPS 代理与真实对象服务 |
| 票据 | 会话/用途/版本绑定、过期、并发消费规则、重新授权签发、回执查询泄漏检查 | PG 并发 + HTTP |
| 密钥 | 配置缺失拒绝、错误/log/trace/JSON/URL 脱敏、浏览器构建与存储中秘密检查 | 合成哨兵密钥 + 浏览器 |
| 迁移 | 空库、现有 head 升级、历史数据兼容、跨 owner FK、RLS、单链 | VMware PG17 |
| 回归 | 认证/课程读取/任务回执、受影响前端；共享逻辑改动对应旧 Python/Node/DPAPI 门禁 | VMware Windows 来宾 |

对象模拟器用于端口与失败分支验证；兼容服务验证真实对象协议。真实云平台的私有桶/IAM/地域配置单独取得证据。PR-04b 负责删除 SLA、全部历史版本清除、备份恢复防复活及在途流最长残留的完整验收。

## 7. 本次验证与环境缺口

| 操作 | 实际结果 |
|---|---|
| Git status / worktree list / ls-remote | 已核对上述主线与 PR-09b 快照 |
| 默认 `python -B tools/overnight/check_contracts.py` | 退出 1，宿主默认 Python 3.10.4 缺 jsonschema |
| 默认 Python 加 `--deps work/pr01/validation-deps` | 退出 1，原生 rpds 扩展与解释器不匹配 |
| Python 3.12.14 加既有 validation-deps | 退出 0，9 项静态检查全部通过，包含 118 项需求映射、28 包 DAG、OpenAPI 和正反 fixtures |
| VMware CLI `List` | 退出 0，当前 1 台 Windows 11 来宾运行 |
| 既有 broker `status` | 退出 0，ToolsState=running，GuestIP 为空，共享目录指向 `web-pr03b-sessions`，readonly/Ready=true |
| PR-04a 运行验收 | 待实施；本次仅执行静态检查与运行环境状态查询 |

静态检查成功命令如下，执行目录为正式仓库根目录。前两次失败报告与最终报告均保留在 `outputs/pr04a/preparation-20261115/`。

```powershell
& 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B tools/overnight/check_contracts.py --deps work/pr01/validation-deps --output outputs/pr04a/preparation-20261115/contracts-py312.json
```

环境待办包括 PR-04a 独立源码传递/哈希清单、来宾目录与端口安排、兼容对象服务及 SDK 版本锁定、合成配置、动态 IP 的再次查询与 HTTPS 地址核对。现有 broker 的 Tools 通道已可读，IP 返回空值的原因尚待诊断。

宿主承担源码、静态检查、材料准备、VMware 编排与证据回收。后续功能、单元、PostgreSQL、对象存储、浏览器、HTTPS/WSS 与回归测试统一在 VMware 来宾执行。当前准备可进入契约细化与实现阶段，运行验收的就绪状态在上述环境待办完成后更新。
