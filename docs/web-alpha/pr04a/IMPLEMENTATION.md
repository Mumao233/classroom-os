# PR-04a 私有对象存储与密钥边界

负责人为林珈诚。当前实现基于 `81ab191b1f33de2371567eccd0ef9589d12483c2`，分支 `feature/web-pr04a-storage`，工作树 `J:\class-assistant\worktrees\web-pr04a-storage`。基线快照、共享接入顺序及阶段结果分别见 [BASELINE](BASELINE-20261115.json) 和 [交接清单](SHARED-CHANGES-20261115.md)。

## 运行接入

正常入口为 `classroom_web.entrypoints.http.create_app`，新增可选参数 `media` 与 `media_spool_directory`。原有调用可继续使用。媒体服务采用现有 AuthService、Database.authorized_transaction 与 owner 上下文，写操作同时校验当前会话、CSRF 和 HTTPS/Origin。

```python
from classroom_web.application.media import MediaService
from classroom_web.domain.media import MediaPolicy
from classroom_web.infrastructure.object_store import S3ObjectStore
from classroom_web.entrypoints.http import create_app

objects = S3ObjectStore(
    endpoint=object_endpoint,
    bucket=private_bucket,
    region=object_region,
    access_key=injected_access_key,
    secret_key=injected_secret_key,
)
media = MediaService(auth=auth, database=database, store=objects, policy=MediaPolicy())
app = create_app(
    auth=auth,
    contract=contract,
    origin=origin,
    media=media,
    media_spool_directory=explicit_private_spool_directory,
)
```

密钥由服务端显式注入，SDK 使用明确凭据与端点。服务构造时校验桶 ACL 与策略，公开组或通配公开 Principal 的 Allow 策略会阻止服务初始化。配置 HTTPS 端点并保留 SDK 证书验证，合成测试仅通过显式 `synthetic_loopback=True` 使用 `127.0.0.1` HTTP。生产云平台 IAM、桶策略与地域需单独验收。

## 上传与确认

`POST /media/uploads` 创建 staging 资产和上传清单，服务器生成对象 key。`PUT .../content` 在有界临时文件中接收字节，核对实际长度、SHA-256 及 MIME 文件签名，随后条件创建私有对象并重新读取校验。JSON 做语法验证，ZIP 保留为待后续导入流程验证的对象。课件图片的解码、衍生版本以及完整包导入按对应后续包接入。

`POST .../complete` 再次核对完整对象并提交 available/verified 与最小幂等回执。对象身份包含 key、ETag 和可用时的 version ID，数据库约束防止正式资产身份被替换。先成功写对象而 DB 未提交时，原 staging 清单与确定性 key 可用于重试或 `MediaService.recover(ctx, limit=20)` 扫描恢复。扫描逐项重新授权、核验对象并保留未能确认的项。

媒体确认依赖对象和数据库提交事实。音频 chunk 的 manifest、持久事件与 ASR 发送边界由 PR-05b 等后续流程接入，上传图片与包的 available 状态仍按本包媒体 API 表达。

## 下载授权与日志

短票据使用独立 `media_tickets` 表，仅保存带用途约束的 token hash，绑定当前会话、security epoch、资产 revision 和到期时间。签发重试重新核验并产生新票据，通用 operation_receipts 仅保存资产引用。

下载与每个存储块均重新校验当前身份、owner、祖先墓碑、资产状态、期限和票据。支持完整读取和单一字节 Range，返回 200/206，非法或不可满足区间返回 416。媒体响应使用 `Cache-Control: no-store`、nosniff 和 attachment；ASGI 中间件在响应开始前清除服务器 scope 的媒体 query，以防标准 Uvicorn 访问日志记录票据。应用错误队列仅记录异常类型、函数和行号。

逐块授权允许在撤权后停止后续读取。单块为 64,000 bytes，存储读超时为 5 秒；这两项是实现上限，删除过程中实际在途残留时长仍需 PR-04b 的完整测量。

## 实验参数

当前上限为单对象 16,000,000 bytes、每 owner 32 个未过期待确认上传、128 个同时有效票据、票据 60 秒、上传窗口 1 小时、单进程 8 个同时处理的媒体请求、上传流读取 30 秒。MediaPolicy 可在限定范围内显式配置。上述参数供合成实现和验证使用，TBD-06 的产品额度及正式安全政策继续留待确认。

媒体 HTTP 操作使用 60 秒入口预算，传播到工作线程、对象分块校验和数据库提交检查。对已建立下载流继续逐块执行当前票据和会话授权。

R1 修正后，名额与绝对 deadline 由媒体 ASGI 中间件持有至响应调用结束，覆盖响应体迭代与 send 背压。到期取消发送和异步等待；同步存储线程结束前继续保留请求名额及临时文件，收尾后在创建 token 的上下文中重置一次并归还名额。首响应头前超时返回既有 RESULT_UNKNOWN/503 JSON，错误发送最多额外等待 1 秒；已发头的超时流直接中止。存储线程的剩余有界 I/O 等待可能延长资源收尾时间。具体定向证据见 [R1 修复交接](R1-REMEDIATION-20261115.md)。

临时上传文件关闭时删除。过期上传、票据与历史对象的清理策略及恢复清单保留由 PR-04b 的删除和备份机制接续。当前恢复流程保留待处理的对象身份与清单。

## 契约与迁移

正式 OpenAPI 保持 0.2.0。Range 206/416、票据复用及缓存头精确语义已形成 [0.2.1 候选增量](CONTRACT-CANDIDATE.json)，须按既有 Astra 审查和消费者同步流程更新冻结源。当前实现与冻结源之间的差异已在该候选及共享清单中记录。

迁移为 `0009_private_media` → `0008_manual_recovery`，由协调聊天登记给 PR-04a。新增 media_assets、media_uploads、media_tickets，启用 FORCE RLS 和 owner 复合 FK，扩展媒体回执资源类型，约束对象身份和撤权方向。代码回退时保留数据库和对象，以前滚修复维护清单。

media_assets 按冻结模型使用 size_bytes/status、确认和撤权时间、保留策略字段及索引，repository 显式投影为 HTTP byte_length/state。内部 verified 对应尚待 complete 的公开 staging，内部 committed 对应 available，并额外检查撤权时间。对象存储 LastModified 用于保留首次云接收时间，数据库失败恢复维持该原时间。保留策略未分配时使用 unassigned；音频 committed 在 PR-04b 提供已批准策略机制前由数据库约束拒绝，任意政策字符串均无法放行。

## VMware 验证入口

现有用户 broker 保持运行并持有内存中的 VMware 凭据。宿主 `tools/pr04a/submit.ps1` 准备带哈希清单的源码快照，按唯一 ID 排队。来宾复用已冻结 Python3.12/PG17.11 工具链，在本包独立 venv 中安装锁定的 S3 SDK，启动独立 PG 与 SeaweedFS 4.53 实例并执行 pytest。新服务只监听 loopback，测试结束关闭本次创建的实例。

```powershell
& 'J:\class-assistant\worktrees\web-pr04a-storage\tools\pr04a\submit.ps1' -Mode targeted
& 'J:\class-assistant\worktrees\web-pr04a-storage\tools\pr04a\submit.ps1' -Mode full
```

首次准备需要 `work/pr04a/downloads/seaweedfs-4.53-windows-amd64.zip` 与 `work/pr04a/wheels/` 的七个依赖 wheel。当前 SeaweedFS ZIP SHA-256 为 `d458d60e6a4120a64244b0ad7bc02cbc0e2fca71a7f775a88edc01da3b435cd7`，来源为官方 GitHub 4.53 release。来源与本机下载散列随验收记录保存。

来宾沿用现有 J:→C:\j 映射，来宾物理文件位于 C:\j；宿主可控源码、材料和证据均位于 J: 项目目录。工作树工具初次创建到了宿主 C:，已迁到 J: 并修复登记。
