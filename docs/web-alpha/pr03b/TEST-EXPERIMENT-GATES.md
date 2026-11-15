# PR-03b · 攻击测试、合成实验与分阶段门禁

日期：2026-11-05（Asia/Taipei）。状态：**按已确认产品决定及Astra设计审查D01–D03修订的准备方案；所有机制测试及实验均未执行**。Astra对`e02614259b5e6483127e14f06e78cdb13a89f0c5`的结论为REQUEST CHANGES，本轮补设计与用例，不自行关闭发现或宣称S1整体通过。管理员最低12字符、空闲15分钟/绝对8小时、不持久Cookie；学生remember_me仅决定Cookie持久性；分级再认证、人工恢复权限和本地密码名单故障规则按[政策记录](POLICY-DECISIONS.md)执行，不再列为待选择候选。技术/契约仍待Astra审查，真实恢复身份与凭据只在对应真实能力启用前受限登记，不阻碍使用合成身份继续设计和用例准备。TBD-08 保持 **OPEN — PARTIALLY DECIDED**，不得据此声称实现或演练完成。

依据：[安全设计](../architecture/05-SECURITY-OFFLINE.md)、[HTTP/WS 与秘密响应](../architecture/04-CONTRACTS-FLOWS.md)、[数据与事务](../architecture/03-DATA-BACKEND.md)、[实施映射](../architecture/08-IMPLEMENTATION.md)、[后端现状](../../../web/backend/README.md)、[PR-03a 验收](../pr03a/ACCEPTANCE.md)。政策选择见 [POLICY-DECISIONS.md](POLICY-DECISIONS.md)，待审字段见 [CONTRACT-DELTA.md](CONTRACT-DELTA.md)，事务与权限边界见 [SESSION-RECOVERY-DESIGN.md](SESSION-RECOVERY-DESIGN.md)。

## 1. 证据必须对应真实机制

| 机制 | 后续必须使用的环境 | 证据及不能替代它的结果 |
|---|---|---|
| 事务、RLS/ACL、锁序、单次消费、撤销竞争 | 独占、隔离、可销毁的真实 PostgreSQL；普通非 owner 运行角色与独立 auth/identity/scheduler 角色；合成账号 | SQL 断言、实际角色/权限、两个以上真实连接的 barrier 时间线、提交/回滚及审计状态；字典锁、SQLite、Mock repository 不算 |
| Cookie、登录 CSRF、Origin、fixation、缓存 | 本地受信 HTTPS 域名和真实浏览器，至少 Chromium；精确版本及浏览器策略留证 | 实际 Set-Cookie、浏览器 cookie store、网络请求与拒绝结果；DOM/Fetch Mock 不能证明 Secure/__Host-/SameSite 或跨站行为 |
| WS | 真实 TLS/HTTP upgrade、实际 Cookie 与 Origin、首消息 authenticate、数据库票据消费、活连接撤销 | upgrade/首帧/超时/关闭码及可脱敏时间线；普通 HTTP 或伪 WebSocket 单测不能替代 |
| 邮件 | 本地捕获器与真实应用 adapter/outbox 边界；仅合成地址 | 捕获邮件中的用途/代次/期限、重复消费结果与发送不确定恢复；不寄外网、不把捕获器成绩称真实送达 |
| 密码成本 | 声明 CPU/RAM/容器限额/进程数/依赖版本的合成实验，拟部署环境复测 | 原始分布、资源曲线、吞吐/排队/拒绝及恢复时间；开发机单次 hash 不等于部署容量 |
| 授权及未知任务回归 | 原后端真实 PG、原 Windows/DPAPI、九 Node 与相关前端检查 | 保留原失败/跳过；MockProvider 仅验证禁止重发和状态边界，不证明真实计费/ASR |

每次后续运行建立唯一 `work/pr03b/<run-id>/` 与 `outputs/pr03b/<run-id>/`；保留完整 SHA、契约/迁移版本、环境、argv、退出码、耗时、失败与跳过、原始结果及摘要。测试账号、邮件、seed、密码、音频全部合成。受保护的真实数据、正式数据库、配置/凭据、麦克风和收费服务不进入本方案。

抓包和 APM 证据用合成标记证明脱敏；默认不落原始 Cookie/seed/恢复码/ticket/密码。必要秘密只在测试进程内比较，持久证据保留存在/缺失断言、最小状态和非秘密标识，不能把测试报告变成泄露路径。

学生人工恢复由获授权Admin使用专用受控命令；管理员人工恢复仅允许项目用户本人通过独立运维身份验证后，手动批准和执行专用命令，其他Admin、Agent及持有网页会话者均没有此权限。数据库更新只能在该命令内部受审事务执行，不人工SQL/改表。Agent本轮只设计；后续获明确实施授权后也只运行合成身份/签名key的测试，不登记、生成或使用用户真实恢复凭据，不为真实账号执行恢复。本人运维OS身份、独立现场签名证明与登记维护授权须用途分离；不冻结硬件数量、备用策略、算法或候选TTL，不要求当前购买/提交真实凭据。真实登记缺失时真实恢复保持关闭，合成设计/用例准备可继续。秘密不得进入CLI参数、终端history或日志。

## 2. 攻击与故障矩阵

以下每行都包含成功控制组与拒绝/故障组。期望是后续验收标准，**不是本轮结果**。PG=真实隔离 PostgreSQL；B=真实 HTTPS 浏览器；W=真实 WS；M=本地邮件捕获器；H=密码资源实验。

| ID / 机制 | 正例、负例及竞争/边界 | 必须观察的结果 |
|---|---|---|
| A01 / PG+B | 学生密码边界 5/6/12/1024/1025 字符；管理员11/12字符边界；Unicode/emoji/组合字符、UTF-8字节上限、直接绕前端 | 注册/改密/重置与前后端按审定字符计数一致，不截断；学生最低6、管理员最低12按服务端主体/受控角色区分；现阶段三个公开模型12–1024仍是差异，本轮不修改它来“通过” |
| A02 / PG+H | 本地常见/泄露密码名单命中、不命中、损坏/缺失/更新失败；最后批准版本仍有效或已无有效版本 | 更新失败可用仍有效的最后批准本地版本；无有效名单时停止注册、改密及重置的新密码设置，返回通用可重试错误；已有正确密码登录正常并仍受MFA/会话/状态检查；不上传明文，无静默绕过 |
| A03 / PG+B+H | 单账号多 IP、多账号同 IP、跨 login/reauth/reset/MFA 入口、跨进程同时耗尽预算；校园 NAT 正常组 | 原子总预算和全局哈希准入有效，换入口/challenge/worker 不清零；误拒绝与恢复时间可测；不能只靠 IP 或匿名失败永久封号 |
| A04 / B+H | 存在/不存在、pending、disabled、未验证账号；正确/错误密码与 dummy hash | 正文、状态、长度、限流策略及多轮时序不形成稳定枚举；未知账号计算受同样资源准入约束；不要求所有请求机械 sleep 固定时间 |
| A05 / PG+M | 同邀请两个 pending 并发激活、满额/撤销/过期邀请、验证重发与旧代次重放 | 至多一激活，10/30 容量原子；失败不吞邀请码；邀请码不授 Admin；保留 PR-03a 原子流程 |
| A06 / B+PG | 合法 preauth→登录；跨站登录 CSRF、缺失/错配 token、恶意/缺失/伪同源 Origin、旧 preauth、表单和 JSON 两种发送 | 登录前也有服务端 CSRF/Origin 约束；preauth 与会话绑定规则生效，旧/跨用途不能换取会话；精确 Origin，不靠 SameSite 或 CORS 单独放行 |
| A07 / B | 合法 HTTPS Cookie；HTTP、Domain 注入、同名/path cookie、JS 读取、跨站及缓存恢复 | `__Host-classroom_session`、Secure/HttpOnly/SameSite=Lax/Path=/、无 Domain 实测；不得将长期凭据放 localStorage；所有秘密响应 no-store；GET 不产生业务写 |
| A08 / B+PG | 预设 Cookie；密码成功但 MFA 未完；完整登录/MFA 通过/权限变化/ID 轮换；两个并发轮换请求 | 固定旧标识不能升级，只有有效最终标识可用；MFA 前无普通学习/运营授权；轮换后旧标识拒绝，逻辑绝对期限不重置 |
| A09 / PG+B | 学生idle=14d、absolute=30d及管理员idle=15分钟、absolute=8小时前一刻/等于/后一刻；两角色remember_me true/false；合法活动/失败/心跳/轮询；两个活动刷新及刷新与撤销并发 | 服务端到期拒绝；学生remember_me只改变Cookie持久性，两种值均维持14d/30d；管理员两种值均不持久Cookie并执行15分钟/8小时；合法活动不越absolute、不复活撤销/到期session；touch首次按统一锁序取待写行排他锁，无共享锁升级互等；浏览器恢复/系统重启不恢复过期会话 |
| A10 / PG+B | 有效学生/管理员；失效设备、删账号、缺验证/密码材料、旧 epoch；客户端伪造 user/role/session/device/mfa 时间 | 仅验证会话后由服务端生成 Principal；内部构造器或 OwnerContext 不成为 HTTP 入口；失效身份 fallback 行为保留，Admin 无他人正文权限 |
| A11 / PG+B | 有效 TOTP；同一步跨 challenge/会话并发重放、时钟±窗口/超窗口、重试耗尽 | 同凭据时间步至多一次成功，失败预算跨 challenge 有效；管理员/已启用 MFA 学生不能因 ReauthRequest.mfa_code 可选而省略因子 |
| A12 / PG+B | 两请求同时消费同恢复码；旧组/错用户/错用途/过期恢复上下文；整组轮换 | 至多一次消费及一次受限恢复转换；旧组全部失效；恢复码不直接授普通或管理员运营会话 |
| A13 / PG+B | MFA 新 seed 生成、确认成功/失败/超时/KEK 失效；旧因子仍可用时关闭/替换 | 新因子确认前旧因子不删除；失败不留下可授权半状态；敏感操作验证当前因子与近期证明，被盗 Cookie 单独不能换因子 |
| A14 / PG+M+B | 邮箱密码重置、新旧代次及并发消费；重置与登录/rehash/MFA challenge 同时提交 | 邮箱重置不清 MFA、不自动登录；仅一消费；新密码/epoch/撤销/审计原子，旧凭据不能由迟到 rehash 写回 |
| A15 / PG+M+B | 合法邮箱变更；未验证新邮箱、过期链接、错目标、两个改邮箱并发；故障回滚 | 新邮箱确认前登录身份不变；证明绑定用户/用途/代次；不能凭邮件链接清 MFA；重放、失效证明不能恢复旧身份 |
| A16 / PG+B | R1学生10分钟/Admin5分钟、R2两者5分钟；R3注销、关闭或替换MFA等高风险操作两者2分钟前一刻/等于/后一刻；跨session/owner/epoch、低等级、目标/参数改变、重复消费 | 按已决定窗口和已列操作检查实际因子时间，不把活动时间当再认证；R3绑定具体操作/目标/参数并单次消费，不能窗口内复用；管理员和已启用MFA学生验证现有因子；其他管理写路由的风险归类及字段/响应具体技术契约待Astra审查，不将旧候选表全部提升为批准 |
| A17 / PG+B | 受限恢复访问自己的绑定流程；尝试课堂、正文、导出、会话解锁、运营读写、WS ticket | 只允许批准的恢复动作；owner 相同也不扩大能力；恢复码不授运营权限；完成新因子后全部撤销并完整重新登录 |
| A18 / PG+B | 学生全因子丢失、管理员全失、唯一管理员；核验不足/批准单过期重放/执行人不符/匿名申请 | 学生由获授权Admin按审定人工流程核验；管理员人工恢复仅项目用户本人手动；其他Admin不能恢复Admin；匿名申请不冻结管理员；无可信登记/批准路径维持管理写不可用，无公开bootstrap、DB手改、隐藏账号或skip_mfa |
| A19 / PG+B+W | 注销单会话、撤设备、全退、重置、禁用分别与敏感写入/票据消费竞争；撤销推送丢失；与LocalReconciler持counter及Outbox隐式FK锁的双连接交错 | 数据库锁定及提交线性化可复现；撤销先提交则后续旧授权失败；先获锁提交的动作按定义处理；安全控制面仅独立identity内部事件/提交通知，不在user排他事务取业务counter或业务Outbox；N01交错详见A45，未实测；不靠通知达成正确性 |
| A20 / W+PG | 正确 Cookie+Origin+subprotocol+首条票据；缺/恶意 Origin、错 Cookie、ticket 入 URL、重复 ticket、错 classroom/channel/epoch/fence | upgrade 与首消息均校验；票据一次消费且用途绑定；认证前不订阅/处理音频/泄露资源；不把票据放 URL/日志 |
| A21 / W+PG | 未发首帧、先业务帧/二进制帧、超大帧、慢速连接、认证 deadline 前后；票据过期边界 | 有界未认证连接/内存/帧大小及超时关闭；认证前消息不缓冲成待处理音频；超时不因持续垃圾帧延后 |
| A22 / W+PG | 活 WS 撤销后业务命令/新模拟收费帧；PG 短断、通知丢失、重连+旧 cursor、删除后重放 | 每次敏感命令/新收费帧由 PG 权威检查；故障拒绝新敏感动作；恢复重新鉴权，过期游标 resync_required，不复活被删正文；无真实 Provider 调用 |
| A23 / PG+B+W | A/B 资源替换、嵌套课堂错配、连接池复用、Admin 请求他人正文、auth/scheduler 原始 SQL | 应用 owner+复合 FK+FORCE RLS+ACL 均有效；auth lookup 只返最小元数据；API 不能选择 owner，后台角色不能读正文 |
| A24 / PG+B+M | Cookie/seed/恢复码/ticket 签发提交后响应丢失；同 op 重试、异体、日志/APM/异常抓取、浏览器 cache | 通用 Receipt/Outbox/日志/APM 无秘密；no-store；恢复码不从旧 receipt 回放；按秘密响应契约返回非秘密状态/拒绝后，近期再认证显式轮换 |
| A25 / PG | PG断开/lock_timeout/statement_timeout、审计插入错误、COMMIT前后断连、消费死锁；新增安全事务与既有LocalReconciler隐式FK锁交错 | 恢复命令结果不明使用D03授权status只读核对，其他命令按各自持久事实核对；不据查不到/PG不可用猜失败，不返回假成功或降MFA；按A45验证独立identity通知，禁止业务counter/Outbox反向依赖；数据库重试有界且不重放外部行为；审计失败不留部分授权变更 |
| A26 / PG+B | KEK 不可用/错误版本/解密失败；MFA/恢复/轮换中故障 | 拒绝需要因子的授权/签发，保留原因子与状态；不以密码临时替代 Admin MFA；不记录 seed/解密材料；健康恢复后按原权限继续 |
| A27 / PG+H | 正常哈希并发与洪峰、全未知账号、长输入、取消/客户端断连、多个 API 进程+worker 共存 | 有界在算/排队/请求体，断连不无限遗留任务；预算不只限单进程；无 OOM/swap 抖动失控，不在过载时降低成本 |
| A28 / PG | 重置/注销后已有raw、unknown、Receipt/Outbox/fencing、旧worker与LocalReconciler；旧业务counter已存在、收尾插入Outbox的owner FK隐式user锁 | 原raw/收据/风险保留；只做受限本地收尾，不能认证失效owner；新安全控制面不向共享业务counter/Outbox写通知，A45完整双连接含隐式锁门禁保留且未测；不Provider重发/切换/退款或清unknown/可能收费风险，新授权不复活旧内容 |
| A29 / PG | 增量迁移空库/前版本/重复执行、ACL 不足/恶意同名角色/失败中断；旧代码回退 | 保持既有迁移字节与 schema 兼容；越权 provisioning 安全拒绝并完整回滚；不以宽权限/BYPASSRLS/残留 membership“修好”；0006 bootstrap-only 限制仍在 |
| A30 / PG+B | 六字符/可选MFA条件风险接受：补偿机制全部实测通过、10–30人邀请制Alpha范围内的合成放行组；任一未测/失败/失效或超范围组 | 风险接受仅在补偿措施验证通过后、仅对10–30人邀请制Alpha生效；本轮未测不得据产品决定开放真实用户；失败不能被“已接受风险”豁免或静默降低名单/限流/密码成本门槛，扩大阶段须重新审查 |
| A31 / PG+合成专用命令 | 学生人工恢复：真实合成申请人与假冒者；获授权Admin、无恢复权限Admin、未完成MFA或过期再认证Admin；核验材料不足、审批缺失、证据引用变更、绕命令直接写SQL | 仅登记获授权者通过专用受控命令按技术审定流程核验/批准/执行；留存最小证据引用、核验结论、actor、target、时间及结果，拒绝无权限和假冒；DB更新只在命令内受审事务，不人工SQL/改表；不能读取课堂正文或索取密码/seed作核验；无审计不完成变更 |
| A32 / PG+合成专用运维命令 | 管理员/唯一管理员人工恢复：预登记项目用户本人的运维OS身份＋独立签名key匹配后手动专用命令；其他Admin、伪称本人、仅邮箱/浏览器会话、错误OS身份、未登记/撤销/错key、只拥有其中一个因子、直接SQL | 只有登记绑定同时有效且由项目用户本人手动批准/执行专用命令才可能进入受限重绑；网页Admin权限、邮箱控制或浏览器会话不能代替；Agent与其他Admin无执行权；DB更新只在命令内受审事务；未登记/遗失运维证明时保持不可用，不临时自登记为本人 |
| A33 / PG+合成签名校验 | 有效人工恢复批准；target/epoch/用途/证据/接收端公钥与挑战/nonce/deadline/部署篡改；跨账号/部署、双消费；登记/key/执行Admin撤权与issue/complete竞争；原issue挑战已到期但pending有效；更换接收端 | 批准含接收端计划hash；issue和complete各验证fresh、具体操作绑定、单次且至多2分钟的证明，锁后重核；分别消费批准nonce、fresh completion_nonce及最终能力，不重复消费issue nonce；独立根/当前配置hash与PG版本/权限重核；fresh complete可处理有效pending，不延长issue证明；新接收端重新批准并废旧flow |
| A34 / PG+合成受限运维工具 | 匿名恢复请求；如审定流程在核验后暂停写入则覆盖其边界；批准/消费/新因子确认/撤销各阶段执行中断、PG/KEK/审计故障、COMMIT响应不明后重试 | 匿名申请不冻结目标；如已按审定流程暂停则不因中断意外恢复运营；中断不授完整会话、不先删旧因子；仅按持久事实核对/续受限流程，不重放已消费批准；状态与审计原子，故障安全失败 |
| A35 / PG+B+合成受限运维工具 | 人工恢复seed/能力交付响应丢失、issue/complete响应不明；完整重登后显式生成新恢复码组时响应丢失；批准单/能力重试与超时 | final仅废旧码，不产生或交付新码；status不含秘密；不从Receipt/Outbox/log/APM回放seed/码/ticket；按D03只读查事实，重签是独立重新授权动作；新组仅在完整重登及所需近期证明后显式生成，丢响应不回放原码，按既有规则再轮换 |
| A36 / PG+B+W+合成专用命令 | 新因子证明pending/失败/超时、浏览器finalize、其他Admin代complete、原执行者中断、fresh completion_nonce重放/并发/超过2分钟；旧seed/码组/Cookie/再认证/ticket/cap；完整重登及显式生成新码 | 浏览器仅pending证明；原执行者手动complete，以当前权限及新操作绑定单次≤2分钟证明锁后复核；最终命令事务激活新因子、仅废旧码组、升epoch、全撤/审计；不自动生成新恢复码/登录；status绝不返回码；完整重登后才能显式新建码组，旧cap不能领秘密 |
| A37 / 合成专用命令+PG | 合成秘密输入/输出、失败/取消/异常；检查CLI argv、环境变量、shell trace、终端history/会话录制、stdout/stderr、日志/APM/审计、进程诊断/core dump及通用Receipt/Outbox | 密码、seed、恢复码、PIN、私钥及恢复票据不进入上述通道或通用持久结果；验证不回显输入、采用硬件key时私钥不导出、敏感响应仅绑定浏览器no-store与禁转录/转储控制；不冻结硬件数量/算法，不以debug/人工排障输出秘密；不用真实秘密测试 |
| A38 / 合成部署配置+PG | D01合法独立部署根及本人绑定；输入同时自带root/自签清单/PG active_version；同authority换成人或OS主体；恢复命令/普通Admin/runtime/DB访问者尝试写root/配置 | 清单验签根和首次项目用户绑定只来自既存受信部署渠道及已授权人工核对，不能从待登记清单/PG推导；即使自签与PG完全匹配也拒绝循环信任；恢复角色无写根/配置权；新key PoP不能证明是项目用户 |
| A39 / 合成维护命令+PG | D01登记/换key/撤销：合法独立维护批准；仅新key PoP、旧恢复权限、自称本人、错误deployment/authority/action/old_hash/new_hash/nonce/期限；维护与issue/complete并发 | 维护授权独立于恢复签发权，批准绑定完整变更及当前版本，nonce单次；PoP仅证明新key控制，不是授权来源；同序锁登记行，撤销先提交则旧恢复拒绝，恢复先持锁则维护等待；普通恢复命令不能自授维护权 |
| A40 / 合成部署配置+PG | D01旧签名清单回滚、active_version回退、配置/PG hash或版本错配、分步切换中断、撤销状态竞争、验签根缺失/全失 | active_version只前进；配置与PG短暂错配安全拒绝，不自动降级旧清单或旧登记；撤销/切换串行化并保最小审计；根缺失/全失维持真实恢复关闭，另审重建，不临时认领 |
| A41 / PG+合成时间与命令 | D02 challenge存活、server verified_at、operation_deadline、flow_expiry四时间；issue验证后4分钟执行、complete验证后4分钟执行；5分钟候选challenge在4:59验证或到期后才验证；锁等待跨2分钟 | 验证全部因子完成时DB时钟必须早于challenge_expiry，实际完成时记verified_at；issue/complete各fresh、不同action/证明/nonce、单次≤2分钟；operation_deadline取verified_at+至多2分钟、当前资格期限及已有pending期限较早值，锁后now<deadline；4:59验证可在fresh窗口内执行，不以已消费challenge到期再次拒绝；到期后验证、4分钟后执行或锁后过期拒绝 |
| A42 / PG+合成时间与命令 | D02 issue已合法消费且原challenge已过期、候选10分钟pending仍有效、fresh complete；pending超时/目标epoch变更、旧completion_nonce重复/并发；流TTL试图续执行证明 | 有效pending可由原执行者重新验证的≤2分钟单次complete完成，不重复消费issue nonce；pending超时/版本变化拒绝；fresh completion_nonce至多一次，10分钟仅流程TTL候选，不能放宽issue或complete；锁后重新核全部期限 |
| A43 / PG+合成status命令 | final提交丢响应、cap已消费/目标epoch已升；final明确未提交或正在并发提交；原actor当前资格fresh认证读本人case/op，等待status共享锁跨过2分钟期限；其他Admin/旧cap/browser/公开case/op | 独立只读status不依赖旧目标epoch/cap；锁后及返回前复核当前资格/证明期限，共享锁读取提交事实，无法证明时unknown；not_committed仅为读取线性化点事实、不授权盲重做；只返最小阶段/非秘密audit/op，不消费恢复nonce/cap，不签发/推进/切因子 |
| A44 / PG+合成status命令 | status读锁与actor专用权限/authority/key撤销两个提交顺序；同actor新登录；未知case/错op、PG不可用/超时、重复查询、status后重签已完成case | 撤权先提交则后续查询拒绝，查询先持共享锁则按该线性化点读后撤权生效，不靠历史批准；稳定actor当前重新认证可读本人历史case；未知或PG故障结果不明，重复查询零流程/因子/epoch写；completed不得重签/再切因子 |
| A45 / 真实双连接PG（未来门禁，未执行） | N01反例：连接L持业务owner counter，Outbox owner FK请求users KEY SHARE；连接S持users FOR UPDATE若再取同counter形成锁环；新设计在同交错签发/complete/撤销通知 | 安全控制面选择独立identity内部事件/提交通知，禁止在该user排他事务取业务counter/业务Outbox；核查新表FK/trigger/通知函数的隐式锁，证明S不等待业务counter使L可最终完成；回滚/重试有界无Provider重发；不把所有user锁改弱来消除反例 |
| A46 / PG+B+合成命令 | N02 complete成功/响应丢失后旧恢复码/旧cap查询和领码；status重试；完整密码＋新MFA登录后显式生成码组及响应丢失 | complete仅废旧码，不生成新组；status无码；旧cap与旧epoch不能领新秘密；完整登录及所需近期证明后独立生成一次新组，no-store、只存摘要；响应丢失不回放原码，按既有明确轮换流程处理 |

并发用真实连接、明确 barrier、提交顺序和有界等待复现，不能依靠 sleep 偶然命中。期限用服务端可控时钟/DB 时间与真实浏览器到期两类证据；不得修改机器全局时间或为了通过放宽生产检查。状态/权限/审计/副作用一起断言，只有 HTTP 状态码不足以证明拒绝后没有写入。

### 2.1 D01–D03及N01/N02的静态状态推演

以下是将审查反例转为后续可断言结果的**文档推演，均未执行**，不是Astra已经关闭发现的证据。issue和complete分别保留 `challenge_expiry`、服务端实际 `verified_at`、`operation_deadline` 及 `flow_expiry`；证明须绑定各自action/目标/case/flow/接收端与不同nonce。challenge候选5分钟只限制等待签名/验证阶段；pending候选10分钟只限制流程存活，不替代两次操作各自≤2分钟新鲜度。

| 状态或交错 | 必须推导的结果及后续断言 |
|---|---|
| 输入同时携带自己的root、自签清单、本人OS/key映射和匹配PG版本 | 拒绝；验证根/首次项目用户绑定来自既存受信部署边界及独立人工核对，三者自洽不能自证授权；普通DB权限不等于部署信任 |
| 新key完成PoP，但没有独立维护批准，或恢复角色尝试登记/换根 | 拒绝；PoP只证明控制，维护权与恢复权分离；维护批准绑定deployment/authority/action/old_hash/new_hash/nonce/expiry，不借恢复命令自授 |
| 合法变更配置先切换、PG未切换或反向次序；旧有效签名清单被重放 | 暂态不匹配安全拒绝，active_version单调前进，不回退旧配置“恢复可用”；变更/撤销与issue/complete同序锁串行化；根丢失保持关闭，另审重建 |
| t0全部独立验证成功，t+4分钟才issue；或complete独立证明已验证4分钟才执行 | 拒绝，即使5分钟challenge或10分钟pending仍存活；DB锁后重核operation_deadline≤verified_at+2分钟，未消费证明不能延期 |
| 5分钟候选challenge在4:59完成服务端校验，随后fresh窗口内执行 | 可在实际verified_at起至多2分钟内执行，仍取当前资格/已有pending更早截止；challenge只管验证阶段，已消费后不再以其5分钟到期代替operation_deadline；若5:00或之后才完成验证则拒绝 |
| 验证时证明新鲜，但等user/authority/case锁后超过operation_deadline | 拒绝且不消费批准/能力、不改因子；不能以请求进入队列或拿第一把锁的时间冻结授权 |
| issue成功消费nonce；原issue证明/challenge已到期；pending仍有效且新因子已pending-confirmed | 原执行者以当前资格、不同action/新证明/fresh completion_nonce完成complete；不重复消费issue nonce，不要求它未用；flow/目标epoch/接收端及新因子版本均锁内匹配 |
| 两个complete、旧completion_nonce重放或改变接收端 | 至多一次切换/一次epoch增加；旧证明不能换接收端；改接收端是独立重新核验/批准新nonce及原子废旧flow；不能靠浏览器直接finalize |
| 初次issue前PG/必需审计失败 | nonce、能力摘要及审计全部回滚，旧因子不变；无秘密响应或普通session |
| issue已提交但响应丢失，或浏览器未确认/超时、执行者中断 | 只留受限pending或到期状态，旧因子保留；原稳定执行者重新认证后status只读核对；重签是独立重新授权且先锁内重读case，不能按查不到猜失败 |
| 新TOTP证明已pending-confirmed，浏览器/其他Admin请求finalize或代complete | 拒绝；仅原获权学生恢复Admin或项目用户本人独立运维身份手动complete，且新鲜证明、当前权限、scope及全部锁后条件成立 |
| 原执行者撤权先提交；或complete先持有全部必要锁 | 前者issue/complete锁后拒绝且target不变；后者先提交一次变更，撤权随后生效，不能声称撤销倒转已提交动作；status仍重验查询时资格 |
| 最终PG/KEK/必需审计失败，或最终COMMIT回包丢失 | 失败事务不部分切因子/撤权；回包丢失不能推断失败：由原稳定actor/authority以当前有效资格重新认证的独立status查自己的case/op，cap消费和target升epoch不妨碍此历史只读事实查询 |
| status看到final已提交/未提交，或只看到pending | 分别返回最小持久阶段/提交事实及audit/op非秘密标识；任何分支均不签发能力、消费恢复nonce、finalize或返回恢复码；未完成需后续独立授权动作，不由status自动推进 |
| status调用者权限/authority已撤销、其他Admin、旧targetepoch/cap/browser或仅case/op | 拒绝，历史批准不兜底；稳定actor当前合法重新认证可查询自己的历史case，不能将目标旧epoch或旧cap当查询权限；浏览器没有该控制面status权限 |
| status查不到、错op、PG不可用/超时或重复查询 | 查不到或故障保持结果不明，不重做消费/切因子；重复查询对恢复流程/因子/epoch零变更；未知标识不泄露别人case，不返回raw/秘密 |
| completed后要求重签能力/用旧cap领新恢复码，或complete响应丢失后重试 | 已完成不可再次切换或重签恢复能力；final只废旧恢复码，不生成新组；原actor合法status只给事实；完整密码＋新MFA重登及所需近期证明后显式生成新组，丢响应不回放原码 |
| 安全事务持users排他锁，LocalReconciler持业务counter、Outbox owner FK等待user | 新通知路径不再等待该业务counter：只写独立identity内部事件/提交通知；S2列明含所有隐式FK/trigger锁的图与双连接门禁，实施阶段真实PG证明无该锁环；当前没有执行或“修复运行死锁”结论 |
| 私钥/PIN/seed/能力进入错误诊断，或mark_submitting/旧unknown结果不明 | 前者必须由安全prompt/FD ACL、禁trace/录制/转储及脱敏阻止；后者保原持久意图/raw/Receipt/Outbox/风险，不新发Provider、不清unknown，不借恢复状态查询重做外部操作 |

独立部署/root及受信安装渠道本身遭攻破超出应用恢复机制能提供的保证，必须作为威胁模型边界写明；不能因此把普通DB或Web权限升级为部署授权。硬件数量、算法、备用方式及5/10分钟候选TTL仍待目标环境技术审定，不因上表用例冻结。真实身份/凭据登记是对应能力真实启用条件；合成清单、key、OS主体和case足以继续设计及用例准备，实际执行仍需明确授权。

## 3. Argon2id 合成实验方案

### 3.1 环境声明及候选组

实验前记录：CPU 型号/可用核心和配额、RAM/容器 hard limit/swap、OS、Python、argon2-cffi/原生库、进程/线程数、PG 连接池、代表性后台工作、反向代理请求体/超时、负载机和部署拓扑。`2vCPU/4GiB` 仍是 ADR-12 环境候选。当前 Windows 开发机成绩只能作筛选，不能冻结拟部署参数。

继续使用成熟 Argon2id，实现显式声明 version、salt 长度和 hash 长度；首轮实验候选为 v19、随机 16-byte salt、32-byte hash。不得用固定 salt、明文真实密码或自行实现算法。下表沿用专项评审的实验组，不将它们称生产默认值或本轮重新核验过的外部基准。

| 组 | m（MiB） | t | p | 比较目的 |
|---|---:|---:|---:|---|
| H-A | 19 | 2 | 1 | 低成本参考对照，非自动允许下调目标 |
| H-B | 64 | 2 | 1 | 固定内存的时间成本对照 |
| H-C | 64 | 3 | 1 | ADR-05 首要实验候选；不是已批参数 |
| H-D | 64 | 3 | 4 | 并行度对照，不能把 p1 写成 RFC 原样配置 |
| H-E | 128 | 3 | 1 | 内存成本对照 |
| H-F | 64 | 4 | 1 | 时间成本对照 |

`m` 是单次调用总内存参数，不再机械乘 `p`；预算使用实测进程 RSS 增量及库开销。不能仅按耗时把不同 m/t/p 组合绝对排序。

### 3.2 负载、指标及全进程预算

1. 单次正确、错误、未知账号 dummy hash；6/12/64/1024 字符和 Unicode；超限请求在廉价输入检查阶段拒绝。先预热，再采集足够的独立样本与原始分布，声明样本量、测试顺序、冷/热条件及 p99 样本限制。
2. 正常并发 1/2/4/8 与持续代表性 API/后台工作共存；测 hash/verify p50/p95/p99、登录端到端、CPU time/利用率、峰值 RSS、队列长度/等待、event-loop lag、PG 池等待、吞吐、拒绝比例。
3. 16/32 请求突发；入围组至少 10 分钟持续过载是**实验时长候选**。改变 API 进程数、同机 worker 负载、未知账号比例和请求长度；测正常用户成功率、拒绝/超时、负载撤去后的恢复时间。不得只测一个 executor。
4. 对比“无哈希负载”和“批准正常负载”基线。候选交互目标：单次验证 p95≤500ms/p99≤1s；正常登录 p95≤1s/p99≤2s；普通 API p95 劣化≤20%；内存余量 25%。这些目标仍待审定，未达应提交环境/容量/体验取舍，不能改测试断言刷绿。

建立部署级并发上限，而非每个 worker 各给一套无限池：

`总峰值 = API/worker/PG/代理基线峰值 + Σ(进程实际同时哈希数 × 每次实测峰值增量) + 所有短队列/请求体/线程栈开销 + 余量 < 机器或容器有效内存限额`

CPU 核心/配额同时约束执行槽；p 增大要记录线程调度与其他工作饥饿。明确进程数上限、每进程执行槽、跨进程配额分配、总队列长度与最长等待；扩进程或重启不能扩大总预算。哈希在有界执行器中运行，ASGI event loop 不直接做 CPU 哈希；拒绝/短队列在高成本计算之前实施。未知账号 dummy hash 受相同准入约束。客户端断连与取消要测试在算工作最终释放，不承诺线程可即时中断。

过载必须有界拒绝并保持恢复能力；禁止临时降低 m/t、跳 dummy hash、绕 MFA、无界重试或把廉价成功/失败路径作为稳定枚举口。若预算不足，先提交容量、并发或交互目标调整决策，再选择配置。

### 3.3 PHC 升级、rehash 与重置竞争

覆盖旧合法 PHC、新配置、损坏 hash、未知算法/版本、恶意超预算参数、组件异常和 rehash 失败。PHC 参数在受审上限内验证，不能让异常数据库字段请求无限资源；错误不能泄露 hash。旧 hash 可验证后按批准配置升级，不能无认证批量推导新 hash。

正确密码验证与 rehash 计算不持长 PG 锁。写回时对读取的原 hash/凭据版本及当前 security_epoch 作条件更新，并在同一受权事务重检用户状态；若并发重置/禁用/改密已提交，条件更新失败，不能写回旧密码或继续签发旧证明。重置先提交、rehash 先提交、验证完成但签发前重置三种顺序均用真实 PG barrier 验证。rehash 失败不影响已批准安全成本验证的含义，但是否仍可完成登录须在实施设计中明确；无论选项如何，不能绕 epoch/会话检查。

报告交付入围组、实际资源上限、可承载正常负载、过载边界、兼容升级与剩余风险，由 Astra 技术审查及产品批准相应体验/风险；性能数字不替代风险接受。

## 4. 其他候选参数的验证方法

下表中限流、challenge、TOTP、邮件/恢复、WS等数值仍为**首轮实验输入**；已决定的管理员会话与再认证值独立列为验证规则，不再比较产品备选。旧实现已有实验初值也不因此变成生产默认。名单来源、有效版本/陈旧期判定与签名恢复工具等技术细节仍待Astra审查。

| 参数 | 首轮候选输入 | 验证及选择依据 |
|---|---|---|
| 账号密码失败 | 5次/15分钟后渐进延迟，30秒起、封顶15分钟 | 分布式猜测、跨入口/进程、恶意锁号、校园 NAT 误伤与解除；计数原子，比较便利性和攻击吞吐 |
| 来源 IP | 20次失败/分钟 | 多账号凭证填充与大 NAT 正常群体，不能单独作为账号锁定依据 |
| MFA challenge | 5分钟、最多5次失败；账号跨 challenge 10次/30分钟 | 真实完成时长/时钟偏差/并发重发/慢速攻击；不能通过重签 challenge 重置总预算 |
| TOTP | 30秒步长、±1步 | 设备偏差、边界并发、同一步防重放；易用性与可猜窗口一起报告 |
| 邮件/重置 | 3次/账号/小时、重发60秒；重置令牌15分钟 | 捕获器测排队/迟到/重发旧代次；账号/IP/全局约束；匿名请求不能改变正常账号权限 |
| 受限恢复 | pending流程10分钟候选；每组10个、每码至少128-bit随机熵 | 人工恢复完成率、到期/并发/响应丢失；10分钟仅流程TTL，不延长issue/complete各自fresh≤2分钟；final不生成码组，完整重登后显式生成及轮换；熵下限不因性能下调 |
| 独立运维issue/complete四时间 | challenge存活5分钟候选；verified_at为服务端全部验证实际完成时刻；operation_deadline≤verified_at+2分钟并取当前资格/已有pending较早期限；flow_expiry候选10分钟 | 验签前challenge未到期；issue/complete分别不同action、新证明、新nonce，锁后再次验证deadline；覆盖验证4分钟后执行、4:59验证、到期后验证、锁等跨2分钟、有效pending的fresh complete及旧complete重放；5/10分钟与算法/硬件配置不冻结 |
| WS ticket / 首消息 | 寿命30秒 / upgrade后5秒（本启动包新增测试候选） | 正常TLS/代理延迟、低速连接、首消息deadline、复用/票据临近到期、多连接内存；同时绑定会话绝对期限，任何候选不得使过期会话复活 |
| 已决定的会话/再认证规则验证 | Admin15分钟/8小时且不持久Cookie；学生remember_me仅Cookie持久性；R1学生10分钟/Admin5分钟、R2均5分钟、R3注销/关闭或替换MFA等高风险操作均2分钟且具体操作单次 | 真实操作时长、后台轮询/心跳、轮换不续绝对期、过期中操作、逐笔重放；验证实现符合决定，其他管理路由归类待Astra；发现问题报告证据，不自行改回候选或调整已决定值 |

票据寿命和首消息超时是两个独立 deadline，任一个先到即拒绝；垃圾消息不延期。limit、deadline、失败/锁定行为等若影响公开响应、错误码或恢复状态，先经过契约差异审查，不能用内部配置绕过消费者兼容评估。

## 5. PR-03b 内部阶段与放行门禁

沿用 [原 DAG](../architecture/contracts/pr-dependencies.json) 的03a→03b及TBD-08门禁；下表是PR-03b内部审查顺序，**不新增工作包、不改依赖图、不提前关闭TBD**。Astra对上一精确SHA仍为REQUEST CHANGES；本轮为D01–D03及N01–N04的最小设计修订，**不能标S1整体通过，也不由作者关闭发现**。已确认政策不重新投票。设计/用例及获准后的隔离合成实验可用合成身份材料；真实登记是对应真实能力启用条件，不前置到纯合成准备。本轮没有启动实验、登记、实现或真实恢复。

| 阶段 | 放行前必须具备 | 仍不得推断 |
|---|---|---|
| S1 已决定政策核对、设计缺口复审与方案审定 | 核对六项已确认决定及条件风险；D01独立根/首次本人绑定/维护授权与版本切换、D02四时间、D03独立status授权及N01/N02路径补具体方案交新精确SHA复审；明确真实启用登记项、合成身份边界及实验方案/环境；当前尚未整体通过 | 由作者宣布D01–D03关闭或S1全通过、要求产品重选已决定政策、要求先交真实秘密才准设计/合成用例、真实登记或补偿已完成、风险已生效 |
| S2 正式契约审查及实现门禁定义 | Astra审查版本/Changelog/ADR/需求追踪；Claude/Codex确认密码三模型、角色语义、pending/issue/complete/status和逐笔授权差异；N01已选择独立identity事件/提交通知，禁共享业务counter/Outbox，提供含隐式FK/trigger的锁图和A45双连接实现验收门禁（未执行）；final仅废旧码、重登后独立生成新组及无码status契约明确；fixtures/类型/Mock/兼容一致 | 文档选择等于真实PG证明无死锁、恢复契约已冻结、准备草案可直接编码、旧客户端自动兼容 |
| S3 分步实施验证 | 明确授权的精确实现范围；先最小会话/CSRF/Principal/撤销，再MFA/恢复/密码邮箱，再WS与跨模块回归；各步真实机制测试、失败回退和合成实验通过；所需增量迁移逐步加入 | 一次建立全模型、启用私人持久缓存/真实采集ASR/运营后台/收费 |
| S4 精确SHA独立复审与真实启用条件分列 | 固定实现SHA/契约/依赖/环境、全命令结果及未验项；Astra独立复现A01–A46、状态推演及原回归；合成实现验收与真实能力启用分列，真实启用前再核实际独立根/本人绑定/获权Admin及恢复材料受限登记、演练和启用批准；补偿全部通过后才记10–30邀请Alpha风险生效，满足TBD-08全部关闭证据后才可申请关闭 | 本轮启动包等于机制验收、要求真实凭据前置纯合成设计、合成通过等于已登记或已做真实账号恢复、独审自动授权push/merge/部署/发布 |

认证绕过、Admin MFA 绕过、越权/正文泄露、秘密泄露、恢复后门、保存丢失与失控收费不能由总体成功率豁免。PG/KEK/审计故障须安全失败；代码回退不降 schema、不删账本、不降 MFA，不启用旧 Cookie/epoch 或内部 OwnerContext 作为临时 HTTP fallback。

## 6. 必须保留的回归与条件关口

| 回归/关口 | 后续实际执行要求与限制 |
|---|---|
| 原后端 | 保留完整后端套件、PR-03a 的失效身份fallback/LocalReconciler/raw/Receipt/Outbox/fencing与unknown竞争探针及迁移保护；真实隔离 PG。按 [后端 README](../../../web/backend/README.md) 的严格 target guard，实施阶段先适配并审查 PR-03b 工作树的隔离目标/runner路径，不对旧工作树运行迁移或写测试数据 |
| Windows/DPAPI | 保留 [TESTING.md](../TESTING.md) 原525主+171子测试、合成真实DPAPI、原防护及27步口径；数量不代替原测试逐项保留。本轮不重跑或借历史成绩宣称通过 |
| 九Node | audio、ai-settings、input-limits、study-ui、study-cache、review-render、review-ui、review-mount、portable-reader 全保留；不以新Web检查替代 |
| 相关前端 | 保留 `web/frontend/package.json` 实际 scripts：typecheck（含生成契约校验）、lint、test:contract、test:unit、test:ui、build；认证联调增加真实HTTPS/WS，不用既有Mock浏览器成绩替代；依赖/缓存/浏览器profile置本轮J盘work |
| PR02B-P3-01 OPEN | [技术债](../pr02b/TECHNICAL-DEBT.md) 关口是“该 reconcile 操作 HTTP 接入前”。本轮仅文档不触发；后续仅认证路由也不自动触发。若接入 task reconcile，先明确同键语义并经 Astra/产品审查，补“无raw→K→迟到raw→K→K”真实PG回归；不能把旧Receipt描述成必然零写入 |
| PR02B-P3-02 OPEN | 关口是扩展多Attempt工作流前。会话HTTP接入本身不触发；如扩展，先补旧raw+新planned+无cancel_requested_at覆盖及终态清理审查。两项P3均不由实现者擅自关闭，也不一概阻塞当前准备 |
| PR03a P3-01 OPEN | 0006仅可信bootstrap路径支持；最迟在确定部署数据库/迁移身份前处理。旧额外探针8通过/1失败/exit1保留，不能作为本轮成功或删除失败断言；本轮不选择部署平台或执行生产迁移 |

历史远端 PR-00 CI 仅证明其当次 Windows/Node/static 门禁；不包含新会话、浏览器、邮件、WS 或真实身份实现。本轮不运行远端CI，不将静态文档检查称安全测试通过。

## 7. 本轮实际执行边界

本文件本轮仅据Astra REQUEST CHANGES报告修订D01–D03及N01–N04的测试计划和静态状态推演并做文档自查；启动包整体的链接、契约引用、需求追踪、保护文件及diff校验以根代理交付的本轮验证记录为准。**未执行**：本矩阵A01–A46及2.1节所有状态交错、Argon2id/资源压测、PG事务/迁移/权限/并发/隐式FK锁环、HTTPS浏览器、WS握手/首消息/撤销、本地邮件捕获、身份登记/维护/恢复工具合成演练、原后端/Windows/DPAPI/九Node/前端回归、远端CI；**未登记或使用真实运维身份/签名key、未执行真实账号恢复**。D01–D03待Astra新精确SHA复审，S1未整体通过，正式契约未冻结，条件风险接受未因文档工作生效。不得用“规划覆盖”或“静态推演”替代“实际通过”。
