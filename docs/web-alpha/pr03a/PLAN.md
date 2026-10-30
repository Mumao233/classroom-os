# PR-03a · Identity, Invitations & Ownership

日期：2026-10-30–31，Asia/Shanghai。用户正式授权的工作包，只在
`J:\class-assistant\worktrees\web-pr03a-identity` 实施。

- 分支：`feature/web-pr03a-identity`。
- 批准基线 / 预期父提交：`3b3881540519c27075119f99bb1dbff35d369e35`。
- 开始时 HEAD 完全一致，Git 状态干净。
- 冻结契约包：0.1.0；本轮不编辑 contracts 目录。
- 需求：AUTH-01/02/04/05/07/08 中本工作包的注册、验证、邀请、容量、归属和审计基础。

已核读根 AGENTS、PRD v1.2 的相关完整页面、PR-01 架构入口与 01/03/04/05/07/08、三份指定机器契约及既有 PR-02a/02b 实现、测试和两项 OPEN 技术债。PRD 原件 SHA-256 为
`DD7D970D54FB443969CE6BB210B0B18053BFECF972CF419EFA57EDB51B2FF5B8`，与归档清单一致。

## 实施顺序与隔离

1. 复核已批准基线、分支、状态、依赖和旧迁移；保留 PR-09a 的并行工作。
2. 增加 0004 单链迁移及必要身份表；密码使用 argon2-cffi，令牌只存摘要。
3. 在内部用例实现邀请、待验证注册、模拟邮件意图、原子激活和容量闸门。
4. 建立可信 Principal → owner UoW 入口；沿用目录、任务和相关资源的 owner 复合外键、FORCE RLS、墓碑和事务隔离。
5. 独立 PostgreSQL 17.11 / Python 3.12 运行全后端；保留 Windows 525+171、九套 Node；类型、静态、迁移、保护检查另留实际证据。
6. 更新验收、变更、风险与回退材料，显式暂存本包文件，创建独立本地 commit 后停止，等待 Astra 独立审查。

所有可控临时文件、依赖、数据库和日志在本工作树 `work/pr03a/`、`outputs/pr03a/`；旧 Windows runner 继续使用本工作树忽略的 `work/pr00/`。既有只读 Python/Node/PG 二进制按验证路径复用，未搬迁。

## 不跨越的边界

没有 HTTP 登录、临时账号认证、Cookie/MFA/恢复码、密码重置、邮箱变更、多设备撤销或 WS 会话认证。Principal 只能由后续经过认证的服务端适配器提供，测试构造器不是正式登录。没有真实邮件适配器、真实账号/课堂/密钥、正式 DB、收费 Provider、前端或本地 v1.3.0 改动；不 push/merge/tag/release/部署。

内部邮件意图、激活事件和容量账本只填补当前实现必需的持久边界，不新增冻结 HTTP/WS 字段，不一次创建 60 张逻辑表。涉及 TBD 和容量生命周期的未决语义见验收报告；本轮不关闭产品政策。
