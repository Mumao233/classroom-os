# PR-03b S2：兼容、生产者消费者与独立审查

日期：2026-11-06（America/Los_Angeles）。规范候选包0.2.0、API仍/api/web/v1；本机identity-control协议版本1。S1已独立APPROVE，D01–D03 CLOSED设计层；S2本次仅交审，TBD-08 OPEN，补偿措施未实测、条件风险未生效。

## 1. 兼容变化与同版本窗口

| 差异 | 对旧消费者的影响 | 强制升级/回退门禁 |
|---|---|---|
| 三个新设密码字段12→6、Admin仍12 | 旧表单/生成validator会拒学生6–11；不能仅以后端接收集合扩大称全兼容 | S3同一获批版本同步后端角色校验、表单code point计数/名单暂停及正反例；login/reauth不误用新设最小值 |
| Reauth目的/目标/候选参数绑定 | 旧无operation请求、旧通用token不表达R3单次动作 | 严格拒绝缺绑定/异体/改目标/跨epoch；敏感值只在受限内存及server-HMAC承诺，不用公开裸SHA；旧token不能迁移成通行证 |
| MFA请求的因子分支及recovery_required | 新required/枚举会破坏旧严格schema；未知状态若映射authenticated会越权 | 后端、生成类型、响应验证器、Mock和UI同窗口更新；未知状态明确拒绝/需升级，未完成因子不给完整上下文 |
| 自动受限接收与manual控制分离 | 不能把原confirmMfa→RecoveryCodes拿来完成manual flow，也不能复用完整会话接口接cap | 自动路径专用受限cookie/CSRF/PoP；manual仍原人手动complete、独立当前资格status；类型和路由能力分开 |
| rotateRecoveryCodes显式首次生成/轮换 | 原operationId/路径保持，但不能被manual final隐式调用 | 完整重登后按受保护操作显式发起，一次展示/no-store/仅摘要；丢响应SECRET_ALREADY_ISSUED后另获近期证明显式轮换，不回放旧明文 |
| 错误/新增状态/32KiB reauth候选 | 严格旧客户端可能不识别；错误不能驱动自动重签或收费重发 | 旧客户端兼容测试与generic错误处理只安全拒绝；32KiB仅该路由，其他auth16KiB，廉价大小准入先于密码/签名工作；公开错误不泄露账户/角色 |
| domain逻辑新增控制对象 | 文档版本不等于迁移revision；旧code不能安全读写所有新状态 | S3仅在精确增量迁移及ACL/RLS/旧代码兼容获审后加入必要子集；不能创建全60表、重写旧迁移、降schema或删审计/未知账本 |

采用内部Alpha同版本联调窗口：正式服务入口在完整前后端/契约hash、严格状态处理及补偿证据验证前保持真实认证/恢复关闭。没有“新增字段旧客户端可自动忽略”的假设，不新增未审私有版本头来绕过兼容。静态壳的缓存/旧页恢复及迟到响应必须由S3真浏览器与消费者测试验证，不能仅凭info.version判断运行兼容。

回退先停新签发及相关管理写，保留安全状态、唯一security_epoch、最小控制审计、raw/Receipt/Outbox/fence/unknown与可能收费风险；不清MFA、不自动登录、不恢复旧Cookie、cap或epoch、不把6–11密码当损坏。代码回退不自动降schema；无法处理新状态时安全关闭并前滚修复，不提供OwnerContext/skip_mfa临时绕行。

## 2. 生产者、消费者和实际确认状态

| 资产/职责 | 本次S2实际状态 | S3前必须取得 |
|---|---|---|
| Codex后端/控制协议规范生产者 | 提交OpenAPI、identity-control、逻辑模型及门禁候选，未写业务实现 | Astra精确快照S2独审及明确实施范围；用途/角色/锁/当前性/secret失败路径批准 |
| Claude前端消费者 | 当前项目生成类型、Mock及UI原字节保留；**本轮未获得或代签消费者确认** | 对严格LoginResult/MFA/reauth、未知状态、密码/名单、受限流程、一次秘密交付及升级反馈明确确认；按获批版本同步，不能做generic manual Admin按钮 |
| 类型生成器 | web/frontend/scripts/gen-contracts.mjs仍只读取正式OpenAPI/events；本轮未运行写生成器 | 授权S3后生成openapi.ts/events.ts并用--check消除漂移，保留原跨账号/迟到响应控制；不手改生成文件 |
| Mock/fixture/contract tests | 正式examples增加schema正反例；源测试、Mock和生成文件未改 | S3对新状态、错误及角色语义更新真实消费者断言；Mock通过不作真实身份/锁/浏览器证据 |
| 独立审查 | 已有S1批准是输入；**S2仍待审** | 固定本轮manifest/patch作独立APPROVE或REQUEST CHANGES，不能用作者静态结果自签批准 |

正式契约已相对旧generated产生预期漂移。本轮不声称frontend gen:contracts --check/typecheck/build通过，也不为刷绿在S2改代码；漂移由上述同版本同步门禁阻止启用。消费者确认是后续实施前条件，不伪造“Claude/Codex均确认”。内部子审不是外部消费者签收。

## 3. S2交审必须核对的具体问题

| 门禁 | 独审检查点 | 后续机制证据，不在本轮执行 |
|---|---|---|
| C01 来源与保护 | S1获批七文件hash保持（父index新增S2入口另列），旧报告/补丁/失败记录保留；本轮白名单及非身份schema/操作不变 | 精确实现SHA、迁移/依赖/环境另核，不从本次HEAD推断实现 |
| C02 严格HTTP | 新设/验证密码分开、可信角色、MFA/恢复状态互斥、非authenticated上下文为空、operation绑定与单次、Origin/CSRF/cookie/预算一致 | A01–A17、A20–A27、A30；真实HTTPS/WS、PG防重放与资源，不以JSON fixture证明 |
| C03 控制协议 | D0既存授权、安装映射、R0/K_change/K_recover隔离，当前外账本与单broker admission gate/PGguard、只前滚/错配拒绝、重启/旧连接隔离 | A32/A33/A38–A40；不能用PG及旧签名snapshot自证“当前”或真实根已安装 |
| C04 四时间/状态 | 验证前challenge有效、actual verified_at、issue/complete分别≤120秒绑定新nonce、全部锁后实际DB时间重核；原issue已用不阻fresh complete | A33/A36/A41/A42；静态等式不等于真实锁跨120秒证明 |
| C05 status | 当前稳定原actor/authority只本人case/op、cap已用/target升epoch不阻查询，撤权拒绝、同锁持久op事实与unknown，不读他人/secret/不写恢复 | A43/A44；not_committed不授权重做，PG共享锁/事务权限须可实施，不能用SQL READ ONLY掩盖行锁权限 |
| C06 N01全锁图 | production→claim提交释放→delivery回查、cleanup/唯一/FK/trigger全路径，无user-counter/Outbox反序，不笼统降user锁 | A19/A25/A28/A45真实双连接、屏障/实际等待/有界完成，原任务发送COMMIT及unknown不变量保留 |
| C07 N02秘密 | final无码、旧组作废，重登显式新组、一次展示/摘要/no-store及丢响应/近期再认证；CLI/status/旧cap不领secret | A24/A35–A37/A46，安全FD/禁trace录制转储/APM、真实browser与异常注入 |
| C08 版本/迁移/回退 | 0.2.0与protocol1分开，既有events/五流/DAG不变；schema_version与migration分开，生产者/消费者/fixture/类型及门禁完整 | S3明确角色/definer owner/ACL/RLS/default privileges/空库及0006升级、失败回滚/旧代码兼容；不改0001–0006 |
| C09 保留开放项 | U01/U02真实资产和留存、S1数值试验性质、P3/TBD与风险生效条件保留 | U02未批准则不冻结真实留存或启用真实恢复；真实登记不前置到纯合成设计，合成通过不证明真实就绪 |

## 4. 用户决定与下一阶段

U01真实平台/成本/维护者和PG能力在对应资源选择前决定；U02真实核验来源、专用获权人、最小留存仍待负责人批准（终态op90天/核验详情30天仅建议，不写成生效策略）；U03仅目标实测暴露容量/交互冲突时提交证据与资源取舍；U04各阶段行动另按范围授权。当前S2文档准备已有授权，不重复请求许可，也不要求现在提交真实公钥/秘密或采购硬件。

PR03a P3-01最迟在确定部署数据库/迁移身份前处理，0006仅已验证可信bootstrap，非超级用户安全拒绝不能冒充成功支持。PR02B-P3-01在reconcile HTTP接入前处理，纯认证不触发且不得顺手挂载；P3-02在多Attempt扩展前。PR09a实体ID P3及其他TBD保持原关口。恢复/退出/重置不重发Provider、不退款/清unknown，旧证据不删。

S2独审若通过，只在另获明确实施/合成实验范围后进入S3。先独立审查并实现run-owned guard/runner适配，保留literal127.0.0.1、非5432、无query/PG*等保护；不能直接把旧pr02a_改成pr03b_或复用旧worktree集群。实际改类型/Mock/后端/迁移与机制验证另行交精确SHA复审。真实登记/恢复/启用、Push、合并、部署仍未获本轮授权。

本轮diff、hash、静态结果和未运行事项以[交付回执](../../../../outputs/pr03b/s2-contracts-20261106-01/DELIVERY.md)为准。作者不预设S2批准；交付即停止，不commit、不实施或机制实验。
