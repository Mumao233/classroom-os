# PR-03b 会话、恢复、权限与锁序草案

日期：2026-11-05（Asia/Taipei），产品决定修订版。状态：**产品方向及指定数值已确认；以下技术/契约方案待Astra审查，真实执行身份及凭据待登记，机制未实施/实验演练未执行**。沿用已批准应用服务端认证架构，不新增身份平台。政策见[决策登记](POLICY-DECISIONS.md)，验证要求见[攻击矩阵与门禁](TEST-EXPERIMENT-GATES.md)。

TBD-08保持 **OPEN — PARTIALLY DECIDED**，产品确认及本轮设计修订不等于安全验收。

## 1. 当前接口与唯一失效版本

实际 [Principal](../../../web/backend/classroom_web/domain/identity.py) 包含 `user_id/role/security_epoch/session_id/device_id/mfa_level/reauthenticated_at/expires_at`；其构造器只检查内部值的格式，不验证 Cookie、数据库 session 或 MFA。实际 [authorized_transaction](../../../web/backend/classroom_web/infrastructure/database.py) 检查 Principal 类型、到期、当前用户身份/角色/epoch，并通过内部 UoW 持有用户共享锁；**没有 auth_sessions/devices 的实时校验**。[identity 管理入口](../../../web/backend/classroom_web/infrastructure/identity_repository.py) 校验内部 `mfa_level == totp`、用户/epoch及再认证时间，同样不是 HTTP 认证器。

架构中的 `auth_epoch`、数据库 `users.security_epoch`、Principal 的 `security_epoch` 是**同一个账户安全失效版本**。后续 session/challenge/email action/ticket 保存该版本快照，全部同当前 users 比较；不新增 `auth_epoch` 列、双计数器或不同步的第二套失效版本。前端请求控制器的本地 epoch 只取消旧请求，不是认证证据。这里建立说明映射；本轮不改冻结架构文字或正式类型。

HTTP 普通请求不得接收反序列化 Principal、owner UUID、任意角色/MFA/认证时间，也不得在认证失败后转用 `transaction(OwnerContext(...))`。内部 worker、测试和 LocalReconciler 仍有各自受限端口；不能将该通路暴露成 HTTP fallback。

## 2. 不透明会话及时间

服务端用成熟安全随机源生成不可预测的 session token（提案：至少 256 bit 随机熵）；Cookie 只含不透明 token，不含可自证身份的 JSON/JWT。数据库用带用途分域的安全摘要定位；随机凭证可用 SHA-256，不把低熵密码的普通摘要用于幂等指纹。数据库 session ID、device ID 与 token 分离。秘密从请求到错误/APM全链路脱敏。

冻结 Cookie 属性为 `__Host-classroom_session; Secure; HttpOnly; Path=/; SameSite=Lax`，不设置 Domain。只经 HTTPS；删除 Cookie 使用相同 Path/属性。不得在 localStorage/URL/可公开 DTO 保存长期凭据。登录成功创建未信任 device；device ID 是标签，不能单独登录或冒充设备。学习缓存 trust 与本次采集 consent 保持独立，本轮不实现私人持久缓存。

服务器按数据库时钟判断 `now < idle_expires_at` 且 `now < absolute_expires_at`；等于边界即拒绝。学生绝对期限为首次完整建立逻辑会话起30天，空闲期限14天，活动更新为 `min(now + 14天, absolute_expires_at)`。管理员已确认为空闲15分钟、绝对8小时，按同一较早截止规则计算。合法业务请求集合、更新节流方式仍须技术审查；轮询、WS ping、失败请求不自动算活跃。学生6字符/MFA可选的剩余风险接受仅在补偿措施验证通过后、对10–30人邀请制Alpha生效；本轮不声称生效或安全验收完成。

登录、MFA完成、权限提升、敏感再认证及必要安全状态变更轮换token/CSRF；原token在同一提交失效。轮换保持逻辑会话原绝对到期时间；只有完整重新登录才能建立新逻辑会话，不能靠滑动或定期轮换续绝对期限。并发轮换仅一个成功，旧token不设可执行写入的宽限窗口。响应丢失按冻结reauthorize-and-reissue重新证明并重签，不能从Receipt回放旧Cookie。已确认：学生remember_me=true为持久Cookie（不晚于服务端绝对截止），false为浏览器会话Cookie，均执行14/30天；管理员始终不持久保存登录Cookie，即使输入true也不延长15分钟/8小时或跳MFA。浏览器会话恢复不等于注销，主动退出仍撤服务端会话；具体wire说明及兼容处理待审。

## 3. preauth、CSRF与 Principal 生成

登录前 `GET /auth/csrf` 建立短效预认证上下文及 CSRF，不创建登录用户、设备信任或业务授权。预认证 Cookie 与登录 Cookie 分开，使用 Secure/HttpOnly/host-only 属性并存凭证摘要；CSRF token 绑定该上下文，返回 no-store。GET仅创建协议防护材料，不触发注册、邮件或领域写入。preauth 有限期/数量/清理及 Origin 规则需实验，防匿名存储耗尽。

公开认证写操作先检查精确 Origin（scheme/host/port）、CSRF、Fetch Metadata、Content-Type、大小及廉价多维准入，再进行昂贵密码验证。缺失或 `null` Origin 的支持规则须明列；浏览器认证默认安全拒绝，不靠模糊后缀匹配域名。可信代理名单固定，不能信任任意 X-Forwarded-*。CORS凭据不允许通配 Origin。跨进程限流用原子共享状态，账号、IP/NAT、challenge、跨入口和全局预算组合，不因换 challenge 清空失败预算；匿名失败不得永久冻结管理员。

已登录写请求同时检查 Cookie、会话绑定 CSRF 和 Origin；SameSite 不替代 CSRF。登录后新 token 对应新 CSRF；可按架构用 token 与服务端密钥做 HMAC派生，DB仅存校验值。所有私有响应 no-store，GET不得借重放触发业务副作用。

认证链：摘要最小定位 → 受限认证适配器取密码/因子材料并验证 → 事务重新核对用户、session、device、security_epoch和到期 → 服务端生成 Principal → 资源 owner/墓碑/血缘及路由能力检查。未知账号走受同一准入预算约束的 dummy hash并给通用失败，pending/disabled不靠错误差异枚举。密码校验在长事务外；提交前重新比较已校验的密码版本/hash和epoch，防重置或禁用竞争。

新设密码政策已确认：学生最低6字符，管理员最低12字符，均不强制字符组合；管理员建议用密码管理器生成16字符以上密码。使用本地弱/泄露密码阻断，无有效名单时暂停注册、改密、密码重置，已有正常登录不受影响，不依赖名单服务才验证已存PHC。有效名单版本/来源、完整性和最大陈旧期及原子切换方法仍待Astra审查/登记；本轮正式契约和两个实际入口仍12–1024，不直接改码。

MFA challenge 不是完整登录：不得返回业务 user/device/CSRF上下文或访问课堂。管理员密码正确但未完成 TOTP、学生已启用 MFA但未完成因子，均不签发完整会话。`ReauthRequest.mfa_code` 可选只是 wire 结构；服务端依据实际因子状态/角色要求，缺少必要第二因素即拒绝，不能采信请求的“未启用MFA”。

## 4. 实时撤销与业务事务

每次 HTTP 请求及 WS消息重新校验账户状态、验证材料、role、security_epoch、session未撤销/两类期限、device未撤销及所需因子等级。DB故障拒绝新私有服务授权和敏感/收费动作；不退回旧内存 Principal、签名 Cookie或OwnerContext。连接关闭/缓存通知是加速手段，PG事实是授权依据。

认证入口检查与业务写之间不能留下撤销空窗。提案是在同一短业务事务内、沿现有用户共享锁边界，再持有 device/session 锁并检查实时状态直到 COMMIT。纯校验取共享锁；本事务会刷新活动/空闲期限时，对需要更新的device/session从第一次加锁即取排他锁，避免共享锁升级互等。撤销取对应排他锁；若撤销先提交，后续旧授权不能写入；若业务先持锁，撤销等待其提交。路由不能把进事务前的 Boolean 当最终授权。

当前会话退出撤销该 session并清Cookie，不能要求额外再认证才能退出；设备撤销覆盖其 session和派生授权；全部退出、密码重置、账户禁用/注销及因子安全变更按审定策略撤全会话，账户级变更递增唯一 security_epoch。再认证证明、未完成challenge、受限恢复能力、WS ticket和未来缓存解锁资格同时受epoch/父session/device约束，不仅发一条关闭通知。

**会话撤销不等于抹除持久业务意图**。普通退出不改用户active状态、不删除 tasks/raw/Receipt/Outbox；密码重置使旧认证能力失效，不自动把既有 unknown 当未发送。内部任务继续服从原账户/资源授权、intent和fence规则。未来需要“撤销尚未发送用户意图”时须明确单独业务动作，不能由清Cookie隐式重排或重发Provider。

## 5. TOTP、恢复和敏感变更

采用成熟 TOTP 组件，seed用版本化KEK加密；解密只在受限验证内存路径。KEK不可用不允许绕过MFA或保存明文seed。pending新因子有期限，需正确验证码确认；确认前旧因子继续有效。凭据行保存已消费时间步，在事务锁内比较并推进，登录/再认证/恢复间不可并发重复接受同一凭据时间步。30秒/±1步仍是候选，要测时钟漂移、过去步和未来步推进行为。

恢复码高熵、只展示一次、只存用途分域摘要；单码/整组撤销与消费在事务内原子完成。密码＋恢复码的推荐结果是**仅可绑定新MFA的受限恢复上下文**，管理员不能由此得到运营读写，学生也不能读取课堂、导出或其他私人学习数据。该上下文不可当普通 Principal；当前 wire没有对应结果状态，须先独立契约审查，不能把 `authenticated` 填给前端后靠隐藏标记限制。

恢复完成顺序：验证有效恢复证明 → 原子消费 → 受限能力 → 新因子确认 → 原子替换因子/轮换整组恢复码/撤销旧授权并审计 → 要求密码＋新因子完整登录。种子、恢复码、Cookie、ticket不进入通用 Receipt、Outbox、日志/APM。恢复码响应丢失只给非秘密状态或冻结 `409 SECRET_ALREADY_ISSUED`，近期再认证后显式轮换整组；不保存一份可重放明文。所有秘密响应 `Cache-Control: no-store`。

邮箱密码恢复：通用受理响应 → 本地邮件Adapter签发单次用途/epoch绑定重置令牌 → 设置新密码、消费令牌、递增epoch并全撤 → 重新登录。**不清MFA、不自动登录**。改密码须近期证明；改邮箱先验证新邮箱，成功事务前不更改登录身份，重发/旧邮箱冲突/并发重置不允许部分成功。邮件持久意图只存引用/代次/lease，发送前在内存生成秘密，外部发送不持PG锁；故障重发必须旋转旧令牌，复用现有安全模式而非通用Outbox保存秘密。

再认证证明绑定user/session/device/epoch、已验证因子及实际验证时间/允许范围；窗口取路由政策、证明到期及会话到期的最早值，不使用last_seen。已确认撤销其他设备等R1操作学生10分钟/管理员5分钟，改密码/邮箱等R2为5分钟，注销、关闭/替换MFA等R3为2分钟，R3绑定具体操作并单次使用；当前会话退出不额外拦截。目标/参数指纹规范、单次消费、其余路由分级及新增wire字段/状态待Astra审查，不能私加头/字段。管理员敏感操作必须密码＋现有MFA；恢复码不能替代普通管理写入因子。

人工恢复产品方向已确认：学生由管理员使用专用受控恢复命令；管理员只能由本项目用户本人经独立运维身份验证后手动执行专用命令，其他管理员无权执行。这里“用户本人”指本次作出决定的项目所有者，不是任意被恢复账号本人、任意Admin、Agent或自称所有者的人。具体核验/批准/命令/登记流程为下述待审提案，不再保留“不提供人工恢复”的产品备选。匿名申请不能冻结目标管理员。禁止公开bootstrap、隐藏超级账号、skip_mfa、邮箱重置清MFA及人工SQL/改表；数据库更新只能由命令内部受审事务完成。Agent仅能设计与在后续获准范围内合成测试工具，不能替用户执行真实管理员恢复。

### 5.1 学生人工恢复：核验与管理员权限

待Astra审查的具体流程：登记受控case → 学生独立身份核验 → 管理员命令批准/签发受限重绑 → 学生确认新MFA → 命令服务完成原子切换/全撤销 → 完整重新登录。专用命令暂称 `student-recovery`，是设计名称，不是本轮已交付可执行程序；不能用通用数据库shell或网页后台操作代替。

学生核验采用两类相互独立、与该账户预登记绑定的依据：原邀请/注册主体映射及已核实在籍身份（例如当面核对或经学校正式目录独立回联），加预登记独立联系渠道的现场挑战响应。仅控制邮箱、知道昵称/邀请码、持浏览器会话、提供截图或回答课堂内容均不充分。候选材料是最小账户/邀请ID、核验方式/来源、回联渠道登记版本、核验结果和时间，不索取密码/seed或读取课堂正文。若尚无独立预登记依据或无法可靠匹配，case保持未核实，允许补齐材料/复核而不降低核验；不能由管理员自由勾“已确认”就清MFA。具体可接受证据组合、核验人员及留存期限须Astra审查并实际登记，不要求在聊天中提交证件或秘密。

执行管理员须通过其自身已完成MFA的有效短会话，在专用命令中完成2分钟内、绑定该case/学生/拟执行动作的单次强再认证；服务端重核Admin角色、security_epoch、设备/会话和专用student-recovery权限。权限由受限运维配置/授权记录列明，不由请求声称角色或普通DB登录继承；具体权限映射待审。核验者、批准者、执行者即使是同一获权管理员也必须分别记录，不虚构已实行双人审批；推荐核验与执行分离，但不是本轮新增已批准产品门槛。命令验证case的核验证据引用、批准内容和签名/服务端防篡改记录，锁内消费批准nonce，留痕后才产生受限能力。Admin权限不能借此恢复Admin目标，服务器必须重读目标角色并拒绝，不能只信命令名。

### 5.2 管理员人工恢复：仅用户本人的独立运维身份

提案采用**预登记的个人运维登录身份＋独立硬件签名凭据的现场持有证明**，两者均独立于普通网页登录及被恢复TOTP：项目所有者使用固定受限管理主机/本机console的个人OS身份（例如硬件密钥支持的SSH证书登录，禁止共享root账号作身份依据），再通过另一枚预登记、不可导出且要求触碰/PIN的签名key签本次恢复挑战。硬件认证由成熟组件/库验证，不自行发明签名算法。实际硬件/OS与算法支持须在目标环境审查，当前不假称已部署或已登记。

受信任的签名部署清单维护恢复授权绑定：`recovery_authority_id`（唯一项目用户）、部署ID、OS主体/证书principal、允许登录证书CA或精确公钥指纹、独立签名公钥指纹/key_id、批准工具版本/签名、允许动作与目标角色、登记人/日期/核验引用。恢复服务只读该清单，并核对其受信签名。可事务锁定的窄表登记行另保存authority_id、清单版本/hash、active_version、凭据有效期和revoked_at；**清单提供身份信任根，PG登记行提供当前有效/撤销事实，两者必须匹配**，缺失/版本不符不放行。不能只读配置验签后绕过PG撤销检查。

登记激活/更换/撤销只由受审独立运维变更命令完成，按第8节同序锁authority登记行，与恢复签发/最终确认串行化；命令校验签名清单及批准变更，原子更新状态并审计，不人工SQL。普通Admin、Web/runtime和恢复命令自身均不能增加/替换信任根或授权给其他人。首次绑定须在因子可用时由用户本人出示独立控制证明并经受控登记核对；不能在丢失后仅凭“我是本人”临时登记新key。实际登记和变更命令均未交付，本轮不创建真实身份/密钥。

专用命令暂称 `admin-recovery`。命令先核验操作系统可信peer/证书主体和完整登记映射，签发随机挑战；**在签名前已确定接收端临时公钥和浏览器挑战完整指纹**，纳入规范计划hash。用户本人在本地可信界面查看部署、目标账号、case、动作、目标security_epoch/因子版本、接收端公钥/挑战指纹、证据/计划摘要、随机nonce和到期，再用独立key现场签名并显式确认。校验者从OS认证通道取得身份，不能信环境变量/命令参数里的用户名，不能只查调用者Admin角色、邮箱、普通浏览器会话或口头声称。其他管理员即使拥有有效密码/TOTP、自己的SSH或网页会话，也必须因不匹配唯一授权登记而失败。签名只对确切部署/case/目标/动作/版本及接收端有效，不可转到其他账号或机器。

个人运维会话/挑战批准的候选有效期为5分钟，执行挑战成功后只允许该单次命令计划；派生受限重绑能力候选10分钟。提案的期限关系是：签发必须在批准到期前原子消费批准nonce，能力从该提交起最多10分钟；最终确认检查派生能力期限和批准仍归本case/flow且未撤销，不要求原nonce仍未用、不再消费同一批准，也不把原5分钟批准到期直接当作能力到期。最终能力仍受目标epoch、执行权限/authority当前撤销及流程到期约束；与已确认Web R3的2分钟窗口不同，这一派生规则待Astra专门审查/实验。

重放、过期、被撤销key、改变目标/计划/epoch、并发同case全部拒绝。不得把签名文件当可转交其他管理员执行的长期bearer批准：消费还须核对同一受信OS主体、现场挑战及工具调用上下文。用户丢失普通管理员MFA但仍持上述两类独立凭据时可手动恢复；若这些凭据也全失或未登记，恢复功能可保留但不能成功执行，须走另行审查的信任根重新建立，不能回退普通网页登录/邮箱或让其他Admin代执行。

### 5.3 命令内部事务、受限重绑与故障原子性

两类命令只向受限本机控制服务/认证控制面提交审定计划，不暴露公网bootstrap。控制服务使用窄运行身份，仅EXECUTE固定函数/受审事务入口，无通用SQL shell、任意表UPDATE/DDL、BYPASSRLS、迁移角色或正文读取。命令选择的user/case只是待核验目标，不能成为任意OwnerContext。命令内部完成身份/权限核验、证据/批准校验、审计、受限能力和最终撤销；操作者不能手写SQL作为中间步骤。具体函数、角色/ACL/RLS和新增状态须先走契约/迁移审查。

初次签发事务按第8节锁序重核actor/authority与目标角色/epoch、case/批准nonce、期限、证据摘要及重绑范围；消费批准、创建能力摘要和最小审计同COMMIT。此时不删除旧因子、不签发完整session、不授课堂/导出/运营权限，也不因匿名申请冻结管理员。受限能力只用于该目标绑定新MFA、确认、查询最小流程状态或显式放弃；不能申请普通WS ticket/缓存解锁。学生先证明当前密码（忘记时先走不清MFA的正常邮件重置），管理员重绑路径使用已验证的独立运维授权；恢复命令不能设置或打印目标密码。

重绑接收端使用服务端生成的随机浏览器挑战和临时公钥持有证明，与case/目标/批准计划绑定。学生在管理员批准前通过独立核验渠道核对完整指纹并写入批准计划；用户本人管理员恢复在已验证运维通道确认相同指纹后才签名。指纹只作匹配，不单独授权；私钥、能力token和seed不交给执行管理员。更换接收端或重签能力必须由原同一权限主体重新核验接收端并批准新nonce/计划，事务内增加flow generation、使旧能力失效并审计，不可凭旧批准换浏览器。具体临时key协议、受限Cookie/CSRF和交付通道待审，不能由当前 `authenticated` 状态偷渡；能力不通过CLI参数、邮箱单独证明或公开URL交付。

浏览器只提交新TOTP持有证明，形成短效、未激活的pending-confirmed状态，**无finalize/撤销权限**。最终完成必须由原case执行者手动运行同一专用命令的complete阶段：学生路径是原获权Admin，管理员路径是项目用户本人重新通过独立OS主体＋签名key证明；不能由其他Admin代执行。complete使用全新的单次completion_nonce绑定case/flow generation/目标epoch/接收端/新因子ID与确认摘要，其有效期及协议待审，不重用已消费签发批准nonce。学生执行者取得绑定complete操作的2分钟内单次再认证，用户本人运维路径采用待审短效现场签名挑战。命令中断后pending可到期，浏览器/后台重试不能自动完成。

**命令控制面的同一最终事务**重核新因子证明/能力仍有效/本flow generation/接收端、目标role/epoch/因子版本、原批准已归本case/flow消费且未撤销（不再次消费），并消费fresh completion_nonce。学生路径重新检查原执行Admin当前role/epoch/专用权限、会话/设备/MFA及本次complete再认证；本人运维路径重核authority活跃版本、清单hash、OS主体/key有效/撤销和本次签名挑战。任何执行权撤销或目标版本变化均阻断完成，不把曾经获准当永久权限。

通过后在该事务消费受限能力、激活新因子、废弃旧因子及旧恢复码、递增唯一security_epoch、撤销全部旧session/device派生授权/challenge/再认证/WS票据/未来解锁grant，并写最小审计与非秘密撤销通知。完整重新登录后才恢复普通学习或运营权限；Admin登录仍密码＋新MFA。撤销通知丢失时PG事实仍有效。恢复不删除设备记录或业务意图，raw/Receipt/Outbox/unknown风险按第9节保留。

PG/KEK/必需审计失败时整个当前事务回滚，不能出现“新因子生效但旧授权未撤”或“批准消费但无能力/审计”。流程跨多个短事务，不承诺跨用户交互的一个长事务：签发已提交但重绑未完成时仅留受限pending，旧因子保留；到期/放弃关闭pending不扩大权限。最终COMMIT连接断开时先用相同非秘密case/op查询最小状态，再决定是否重新签发，不盲目重做消费或重新切因子。若签发能力响应丢失，经原权限/独立身份重新验证后显式重签并使旧能力失效，不能以相同批准token无条件回放秘密。最终已完成但恢复码响应丢失时不重放旧明文，按 `SECRET_ALREADY_ISSUED` 规则在完整登录/近期再认证后轮换整组。各阶段失败/拒绝审计须不泄露秘密，日志不可用不放行关键变更。

### 5.4 秘密输入输出与必须登记项

命令行仅允许case/op等非秘密标识；密码、TOTP、恢复码、PIN、临时能力、私钥、签名认证材料不放argv、环境变量、终端历史、stdout/stderr、通用Receipt/Outbox、日志或APM。密码等只经不回显TTY/专用安全prompt或受限FD/本机认证通道输入；硬件私钥不导出，秘密响应只到绑定的受限TLS浏览器上下文并no-store。禁shell trace、终端录制/会话转录、请求/响应体采集和core dump的具体控制待演练，不能靠开发者“记得不打印”。命令默认只输出case状态、非秘密审计ID及结果；丢响应也不从日志恢复秘密。

| 待登记资产（不是要求在聊天提供秘密） | 必填非秘密元数据/控制 | 未就绪时行为 |
|---|---|---|
| 学生核验依据 | 独立渠道/在籍核验来源、账号邀请映射、核验者/复核者、材料受限存储引用、保留/销毁期限 | 不核验通过、不清MFA，继续受理材料核实 |
| 学生恢复管理员 | 实际user_id、专用权限有效期、核验/批准/执行身份与可撤销映射、MFA及再认证要求 | 普通Admin身份不自动获得命令执行权 |
| 唯一项目用户运维身份 | authority_id、个人OS主体/证书principal、独立key公钥指纹、绑定证据、部署/工具版本、有效期/撤销及替换流程 | 管理员人工恢复不能成功执行，其他Admin不能代办 |
| 运维凭据与主机 | 硬件key保管/备用、个人登录凭据、可信主机和通道、PIN/本机密钥库保护、控制服务peer验证与窄DB身份 | 不凭邮箱/网页/“有DB权限”降级放行；不索取私钥/PIN |
| case/批准/能力与审计 | 目标/角色/epoch、操作计划hash、证据引用/核验结果、批准者/执行者、nonce/expiry/消费、阶段结果、失败/中断及受限保留策略 | 无有效批准/审计不提交安全变更；无私有正文/秘密 |

所有实际名字、指纹、主机路径、名单来源/陈旧期、候选期限和演练环境仍待登记或审查。产品确认“提供人工恢复”不是这些资产已就绪的证明；本轮只交具体方案，不创建/收集真实凭据或执行真实恢复。

## 6. WS与事件恢复

仅在有效Cookie＋CSRF＋Origin的 `POST /ws-tickets` 签发短效单次ticket，绑定user/session/device/epoch、channel及必要课堂/采集epoch/fence；只存摘要。ticket不进URL、subprotocol凭据或日志；subprotocol固定 `classroom.v1`。实际upgrade同时检查精确Origin和有效Cookie，不能只凭ticket绕过会话。

upgrade后第一条JSON必须为 `authenticate`，在候选握手期限内事务消费ticket并重核父会话与scope。过期/重放/错Cookie/错通道/错课堂/错fence一律拒绝；认证前不给订阅、资源元数据、音频处理或无界缓冲。控制连接数量、首消息大小和认证前buffer，超时关闭；ticket寿命和握手超时通过实验确定，不把5分钟challenge误用作ticket期限。

每条命令、新收费帧及敏感事件发送前复核DB撤销和资源有效性；撤销通知丢失仍有效。事件按既有owner cursor重放，恢复每次重新鉴权并查墓碑；cursor不当权限凭证。超出窗口显式resync_required，不清本地未同步意图，不补发Provider。沿冻结关闭码 `4401/4403/4409/4429/1013` 处理，不增加私有枚举。后续采集/ASR能力仍由所属PR实施；03b只交认证边界和合成连接机制证据。

## 7. 必要增量数据与角色

实际迁移仅到0006；现有身份表是 users、invitations、pending_registrations、identity_capacity/mail_intents/audit/events，**auth_sessions/devices/mfa等尚无实现**。完整逻辑模型在[domain-model](../architecture/contracts/domain-model.json)，本表只提议03b按依赖加入必要子集，字段细化须审查后再迁移。

| 递增子集 | 必要对象 | 审查重点 |
|---|---|---|
| 会话基本层 | devices、auth_sessions、auth_challenges的preauth/login_mfa/reauth、preauth_receipts及限流必要最小状态 | owner复合FK、摘要唯一、期限/撤销/epoch、空scope拒绝；不建cache_keys/consent采集表 |
| 因子和变更层 | mfa_credentials、mfa_recovery_codes、email_actions；复用/增量扩展邮件意图与审计 | KEK版本、pending/confirmed、消费唯一性、TOTP last-step、邮件用途和重发代次；受限恢复scope/status要审契约及约束 |
| WS认证层 | auth_challenges启用ws_ticket用途 | session/device/epoch及scope绑定；不顺带建媒体/ASR/计费表 |
| 运维恢复最小层 | case/核验证据引用、一次批准nonce、受限能力、authority只读登记映射及执行审计所需增量 | 人工恢复方向已批，技术/契约/真实登记待完成后按获准实现范围加入；不建运营后台或全量逻辑模型 |

迁移继续现有Alembic单链、expand-contract，实际revision名待实施时分配，不重写0001–0006。空库、0006升级、重复升级、失败回滚、旧代码兼容、受限角色直接SQL负例分别验证。代码回退不drop身份/审计/业务账本；需要停用时关闭新签发及管理写，不降低MFA。

认证引导角色只可执行固定search_path、无动态SQL、撤销PUBLIC权限的最小定位/校验函数；未知owner token查询只返回定位所需元数据。密码材料只到专用验证适配器，不向普通runtime/HTTP返回。认证运行角色非owner、非superuser、无BYPASSRLS/CREATEROLE，不继承scheduler/dispatcher/迁移身份；definer使用窄表/列权限和窄RLS，不能通过可选owner参数读取他人数据。

owner业务仍走受限runtime＋SET LOCAL＋FORCE RLS；identity控制平面保留独立角色。MFA材料及批准记录不由通用owner repository任意写；精确GRANT/REVOKE、函数owner、默认权限和升级撤权清单在各增量迁移审查。**0006 bootstrap-only范围不变**，新增auth角色不解决托管PG的非超级用户provisioning；部署平台/迁移身份选择前处理既有P3-01。

## 8. 并发锁序提案与现有路径衔接

以下是03b待审的锁序约束，不声称已经实现，也不重排无关模块。先只读最小定位不得持锁跨入后续事务；哈希/TOTP解密准备、邮件和网络I/O在锁外。准入计数短事务先完成并释放，不能拿限流锁等待身份事务。

1. 按操作需要先取既有global control/budget；仅注册/激活/容量或未来预算操作使用。不能一律先“通用认证持锁”再调用 `set_capacity`，因为其现有顺序为 control → admin用户锁。
2. 存在的user行按UUID排序锁定。普通业务沿现有FOR SHARE；会改变密码/因子/epoch/安全授权的事务从开始即FOR UPDATE，避免共享锁升级死锁。涉及actor/subject时一次取得全套有序user锁，禁止事后补低序user锁。注册激活继续原control → invitation → pending/proof → 新user插入的独立路径，不在持有认证锁后重入激活。
3. 设备 → 会话，各集合按ID排序；不更新活动的只读授权FOR SHARE，撤销/轮换以及会更新last_seen/idle的请求，对待写行首次即FOR UPDATE，不能先SHARE后升级。账户级撤销先锁user再按同序更新设备/会话；所有入口不能反向 session → user。敏感事务持这些锁到业务COMMIT，锁等待后重核状态与数据库时钟；活动刷新不能复活已撤销/已到期会话。并发刷新、刷新与撤销须显式竞争测试；若采用独立短touch事务，须另审其条件更新和业务授权一致性。
4. 恢复authority/权限登记 → case/批准nonce → auth challenge/email action/受限能力 → MFA credential → recovery code，各种类及ID稳定排序；需要消费/推进的行FOR UPDATE。所有入口先取步骤2的actor/subject用户锁再取登记行，禁止登记管理命令反向持authority等user；批准执行锁内重核key撤销/权限有效性。持session锁前不能先锁恢复码；流程初步定位仅只读。会话创建前无session行时在已锁user/device下插入，因子成功才发布完整会话。初次签发和最终确认分事务，分别列出完整有序锁集，不能用长锁跨交互。
5. 进入已授权领域命令后保留既有归属/来源祖先稳定排序 → 资源/plan → task → attempt编号/结果ID → owner event counter（最后）；既有Receipt幂等锁在原命令位置，新增认证事务不反向获取它，秘密签发走专用最小状态。无后续身份锁从领域内回取。

每个实际用例须列出完整调用图（特别是identity.set_capacity、邀请、激活、device revoke、reset、WS ticket consume及task事务），证明不跨连接用“先验认证”替代同事务检查。若复用边界需要窄验证函数/授权port调整，作为03b明确最小差异交Astra，不能假称已有authorized_transaction已覆盖。真实PG用屏障控制两个顺序、lock timeout/deadlock/回滚故障；数据库事务重试必须有界且只重试数据库部分，不重复外部发送。

## 9. PR-03a/02b不变量与故障回退

保留失效身份fallback：具有登录身份（normalized_email非空）但缺验证/密码材料的账户拒绝普通owner与Principal路径，未发送任务可取消，已发送任务保unknown。历史无邮箱opaque owner shell只保留内部provisioning/worker兼容边界，principal_valid始终拒绝，永不授HTTP登录。LocalReconciler仅通过固定状态函数本地核验已落库raw，不返回正文、不授登录权限。[原实现](../../../web/backend/classroom_web/infrastructure/local_reconciler.py)与[PR-03a支持边界](../pr03a/ACCEPTANCE.md)均继续有效。

保留raw、Receipt、Outbox、fencing和may_have_charged/unknown风险；退出、重置、恢复及会话失效不得删除证据、把unknown改成未收费、触发Provider重发/切换/退款。发送授权线性化点仍为mark_submitting事务成功COMMIT：撤权先完成则拒绝；发送意图先持锁并提交则单次已授权调用可能稍后才实际进入Provider，不能承诺撤权返回后绝无物理网络调用。之后保存/发布继续按原授权检查，不重新暴露已撤正文。

PG、KEK或必须审计失败时安全变更原子回滚，不能先改因子/密码再静默丢审计；不可用时停止相应签发/管理写，保已有账本。恢复码或Cookie响应丢失不回放秘密。服务回退不提供skip_mfa或OwnerContext绕行，不启动真实Provider、私人缓存、采集/ASR、运营后台或真实收费。开放P3的具体触发判断见[范围与依赖](README.md)。
