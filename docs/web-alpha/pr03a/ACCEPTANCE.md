# PR-03a 第二轮整改：当前验收

日期：2026-10-31（Asia/Shanghai）。本地整改及验证通过，等待 Astra 对新提交独立复审。

- 直接父提交：`088984fbd7c9f36f4250c9af340d6a73a0c57960`，原开发记录完整保留。
- 新迁移 head：`0005_identity_recovery`；0001–0004 八个迁移文件原样保留。
- 本轮全后端 **423 passed / 0 failed / 0 errors / 0 skipped**，退出 0；原 358 + 新恢复 51 + 新迁移 4 + Astra 原探针 10。执行前后源码哈希一致。
- 本轮重跑 Windows **525 主测试 + 171 子测试**、九套 Node / 27 门禁，全部退出 0。
- 新专项证明未发送 Provider 0、已发送 Provider 保持 1；失效身份 raw 本地收尾、事务回滚、双 Worker、真实锁交错及 101 条前缀公平性。
- 类型、依赖、静态、迁移、冻结契约及原测试/本地版保护见 VALIDATION.json 的实际命令与收据。

修复及归属、矩阵、所有失败记录、受限系统处理边界和回退详见 [整改报告](remediation/REPORT.md)。真实邮件、Cookie/MFA、真实收费/预留结算、生产恢复均未实现或未验证；两项 P3 与原 TBD 保持 OPEN。没有整合、push/merge/tag/release/部署，完成本地修复提交后停止。

以下为原始开发验收历史；其旧数字不是本轮最终结果。

# PR-03a 验收、风险与回退

日期：2026-10-30–31（Asia/Shanghai）。状态：本地验证通过，等待 Astra 独立代码审查；精确结果见本目录 `VALIDATION.json`。本地检查不代替审查或远端 CI。

批准基线和本地提交预期父提交：`3b3881540519c27075119f99bb1dbff35d369e35`。
工作树：`J:\class-assistant\worktrees\web-pr03a-identity`；分支：`feature/web-pr03a-identity`。
完整最终 SHA 与父提交在提交完成后的 `outputs/pr03a/COMMIT_RECEIPT.json`，避免把提交自身 SHA 写进自身内容。
完整逐文件变更清单见 [CHANGES.md](CHANGES.md)。

## 交付与原子性

- 保留 0001、0002、0003 六个发布迁移文件，追加 `0004_identity`，形成单 head。
- 新建六张必要表：`invitations`、`pending_registrations`、`identity_capacity`、`identity_mail_intents`、`identity_audit`、`identity_events`。现有 users 列不变；pending 的内部 profile 字段保留 RegisterRequest 可选 nickname。
- 密码 12–1024 字符，使用固定依赖 argon2-cffi 25.1.0 的 Argon2id。m=64MiB/t=3/p=1 为 ADR 初始实验配置，未宣称关闭 TBD-08。
- 邀请固定只能激活 Student；指定邮箱 / 通用单次、默认 7 天、撤销、单次消费。邮箱仅规范化域名及 IDNA，保留本地部分大小写、点号和加号。
- Pending 没有 users 行、人数名额或学习资源访问。默认验证窗口 30 分钟。验证通过事务按 capacity → invitation → pending 锁定，重新检查期限、撤销、绑定邮箱和容量；user、邀请消费、验证消费、人数、审计、内部事件一起提交。
- 10/30 阶段只接受服务端设置；串行化容量行后计算当前 active 账号及既有 admission 计数，不以客户端人数为依据。容量失败、唯一邮箱冲突或审计/事件写失败均整体回滚。
- 邮件队列只存 pending 引用、generation 和租约。Worker 在短事务更新证明摘要及租约，提交后调用模拟捕获器，再按同一证明/租约确认。崩溃恢复轮换令牌；重发立即使同邮箱全部旧未消费证明失效。没有明文令牌进持久 outbox/audit/event。

## 授权与数据隔离

`Database.authorized_transaction(Principal)` 是后续请求层唯一应使用的资源授权入口。Principal 由可信服务端会话适配器提供，本轮没有签发、反序列化或临时登录入口。事务持有原 owner 锁并重新核验数据库状态、角色、security_epoch、验证状态和可信证据有效期；客户端 owner_id 不进入 Repository。

原 `transaction(OwnerContext)` 仅为内部 worker/数据层端口。兼容 PR-02a 的无登录邮箱 shell 不获得 Principal 访问资格；有登录邮箱的账号一旦失去验证/密码摘要，也会被原 owner RLS/锁入口拒绝。无会话的 UUID 容器不等于认证结果。

身份服务登录只能继承独立 `classroom_web_identity`，不得同时继承 runtime，不是 superuser、BYPASSRLS、表 owner 或建库/建角色身份。新控制表全部 FORCE RLS，runtime/scheduler/PUBLIC 无读写权限；身份角色不能读取学生课程或任务正文。直接 SQL 经 identity 登录插入 users 也不能创建 Admin 或未验证账号。

Admin 管理用例检查当前 DB Admin + 验证 + epoch、TOTP 证据、有效期及显式配置的再认证窗口。Admin 私人资源查询仍受本人 owner 限制。应用没有 Admin 读学生正文或冒充登录能力。

## 实际验证与失败保留

命令均从工作树根执行；精确解释器、完整 argv、退出码、JUnit、文件哈希和证据路径保存在 `VALIDATION.json`。合成账号/邮箱只使用 `example.test` 等保留测试域；无真实邮件、生产 DB 或 Provider。

```powershell
& work/pr03a/venv/Scripts/python.exe tools/pr03a/run_tests.py --admin-url 'postgresql+psycopg://pr02a_pr03a_migrator@127.0.0.1:55439/pr02a_pr03a_synthetic'
& 'J:/class-assistant/classroom-os/work/pr00/venv/Scripts/python.exe' tools/pr00/run_checks.py --output-dir work/pr00/pr03a-identity-baseline
& work/pr03a/venv/Scripts/python.exe tools/pr03a/check_protected.py
& work/pr03a/venv/Scripts/python.exe tools/pr03a/check_static.py --contract-deps 'J:/class-assistant/classroom-os/work/pr01/validation-deps'
$env:PYTHONPATH='J:/class-assistant/worktrees/web-pr02a-data/work/pr02a/remediation/type-deps'
& work/pr03a/venv/Scripts/python.exe -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs --cache-dir work/pr03a/mypy-cache web/backend/classroom_web tools/pr03a
& work/pr03a/venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

PG runner 验证实际 `data_directory` 等于本工作树 `work/pr03a/pg/data`，只监听 `127.0.0.1:55439`；继续使用 PR-02a 严格 URL/PG 环境守卫，再注入 loopback-only 网络防护。每个身份用例建立新的随机数据库、独立 identity/runtime 受限登录，清理精确创建的对象。PG 二进制及缓存来源哈希见 outputs 中 provenance。

验证证据覆盖：同邀请码两 pending 竞争、不同邀请同邮箱竞争、10/30 最后名额竞争、失败不吞码；邀请失效/撤销/绑定不符、证明过期/重复/重发；邮件失败租约恢复和双 worker；中途事件失败后 user/邀请/证明/人数/审计/outbox 全回滚；Student/Admin 边界；跨 owner 目录/材料/课堂/任务读取与修改拒绝、直接 SQL 复合外键和 RLS；禁用/删除/失去验证/密码/角色变化/过期/失效 epoch/缺失主体；0003→0004、重复升级、真实 SQL 故障回滚及拒绝破坏性 downgrade。

最终全后端 **358 passed，0 failures/errors/skips，退出码 0**，包含批准基线全部 **293** 项（含 PR-02b 整改）及新增 **65** 项（身份集成 35、迁移 3、纯值/Argon2id 27）。对应 `outputs/pr03a/20261030T160712.053390Z/`。原 Windows **525 主测试 + 171 子测试**、**九套 Node / 27 门禁** 全部退出 0；真实合成 DPAPI 保留。类型检查 **33** 个源文件无问题；静态 8 项、迁移 head/离线 SQL、pip check、保护及 diff/staged-diff 检查均退出 0。runner 另有 3 个 unittest 方法 / 24 种恶意目标或环境反例通过。

全后端完成后仅把 runner 的历史最低计数 240 加强为整合基线实际 293；实际 pytest 命令、测试和业务代码未改变。原始执行报告保留当时的 240 字段，另以 `outputs/pr03a/postrun-count-gate.json` 验证同一 JUnit 的 358≥293，并重新运行工具对抗、类型、静态与保护检查。随机测试数据库/登录清理已核查；专用 PostgreSQL 集群已关闭、55439 端口已关闭，数据和日志留存，见 shutdown 证据。

首轮后端结果为 344 passed / 6 failed / 0 skipped，退出码 1，保留在 `outputs/pr03a/20261030T160217.258581Z/`。失败涉及异常映射、新测试方法名和旧回滚测试临时图的多 head，均保留原始日志；最终结果另见 VALIDATION，不用历史通过覆盖失败记录。

既有后端测试仅有两处必要演进：原结构测试增加 identity 表集合，保留所有原列/NULL/full-metadata 断言；原 task 故障迁移测试显式指向 `ffff_task_failure`，避免新增 0004 后临时双 head，仍执行真实 DDL/数据修改/除零和全部回滚断言。保护工具按全模块 AST 仅允许这些精确变化。原 Windows 测试、九套 Node、旧迁移、冻结机器契约、本地产品和两项技术债文件保持不变。

## 未决事项与 PR-03b 边界

1. TBD-08 保持 OPEN：密码成本/更完整密码政策、Admin 会话/再认证窗口及恢复演练。测试的 5 分钟窗口是显式合成配置，不是产品政策；60 秒重发间隔、1 分钟邮件租约、每批最多处理 100 条过期邮件意图也只是实现初值。
2. 冻结模型没有邮件意图表；pending 尚无 owner，不能滥用要求 user FK 的通用 outbox。新增的内部引用机制不改变机器契约。冻结 WS 的 object_type 没有 user/account/invitation；内部 `account_activated` 只作为控制平面事务事件保留，没有向冻结 WS 发送新类型。未来映射交 Astra 审查。
3. `max_active_users` 本轮按总 active 账号保守计算，包含 Admin；未实现禁用/注销后席位回收，不自动释放 admission 计数。PRD 学生人数措辞与 Admin 是否单列、生命周期席位复用须在对应管理/生命周期入口前确认。本轮不把此保守限制写成已批准政策。
4. 本轮交付内部用例及授权基础，没有声称完成冻结 HTTP 端点。Origin、CSRF、注册/验证独立限流、HTTP 幂等/回执、管理 CAS/reason 等须在相应安全传输层接入时实现；当前没有 HTTP/SMTP 可运行入口，不能对真实学生开放。
5. PR-03b 的完整 Cookie 会话、MFA/TOTP/恢复码、会话撤销、多设备、密码重置、邮箱变更、敏感操作再认证及 WS 会话认证均未实现；Admin bootstrap/丢失 MFA 恢复也没有临时绕过。
6. PR02B-P3-01 和 PR02B-P3-02 原样 **P3 / OPEN**，未修复、未关闭、未改变语义。
7. 本轮 Windows/Python/PG 都是真实本地执行；邮件和 Provider 为模拟。mypy 使用既有 `--check-untyped-defs` 范围，不是 strict 完整注解证明。未执行远端 CI、真实浏览器、真实邮件投递、生产容量/安全验收或收费调用。

## 回退与停止点

先暂停注册及邮件领取：受控维护事务将 identity_capacity.registration_enabled 置 false，停止调用 identity 用例/邮件 worker；没有公开管理接口或未认证维护路径。保留待验证记录、邀请、admission、审计、内部事件和既有学习数据。

采用兼容代码回退并保留 0004 schema。旧版本可继续使用原数据/任务表，但不得借旧代码重新暴露未经审核的身份入口。不要 drop 表、重置消费状态、回滚已激活账号、重用令牌、自动释放名额或运行破坏性 downgrade。0004 downgrade 主动拒绝删除身份账本；SQL 迁移失败由 PG 事务整体回滚。

独立测试集群可通过核对精确 data_directory 后的 pg_ctl stop 关闭；合成测试和失败日志留存。未连接/迁移生产数据库。最终本地 commit 后停止，等待 Astra 独立审查，不启动 PR-03b，也不 push/merge/tag/release/部署。
