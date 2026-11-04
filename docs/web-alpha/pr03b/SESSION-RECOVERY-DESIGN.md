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

人工恢复顺序：有效issue证明单次消费 → 受限pending → 新因子证明 → fresh manual complete → 原子替换因子/废旧恢复码组/撤销旧授权并审计 → 完整重新登录 → 显式生成新码组（N02）。不在final交新码。种子、恢复码、Cookie、ticket不进通用Receipt、Outbox、日志/APM；其他显式秘密签发丢响应遵守SECRET_ALREADY_ISSUED，不回放明文。所有秘密响应no-store，自动恢复码路径的独立协议也须S2审查。

邮箱密码恢复：通用受理响应 → 本地邮件Adapter签发单次用途/epoch绑定重置令牌 → 设置新密码、消费令牌、递增epoch并全撤 → 重新登录。**不清MFA、不自动登录**。改密码须近期证明；改邮箱先验证新邮箱，成功事务前不更改登录身份，重发/旧邮箱冲突/并发重置不允许部分成功。邮件持久意图只存引用/代次/lease，发送前在内存生成秘密，外部发送不持PG锁；故障重发必须旋转旧令牌，复用现有安全模式而非通用Outbox保存秘密。

再认证证明绑定user/session/device/epoch、已验证因子及实际验证时间/允许范围；窗口取路由政策、证明到期及会话到期的最早值，不使用last_seen。已确认撤销其他设备等R1操作学生10分钟/管理员5分钟，改密码/邮箱等R2为5分钟，注销、关闭/替换MFA等R3为2分钟，R3绑定具体操作并单次使用；当前会话退出不额外拦截。目标/参数指纹规范、单次消费、其余路由分级及新增wire字段/状态待Astra审查，不能私加头/字段。管理员敏感操作必须密码＋现有MFA；恢复码不能替代普通管理写入因子。

人工恢复产品方向已确认：学生由管理员使用专用受控恢复命令；管理员只能由本项目用户本人经独立运维身份验证后手动执行专用命令，其他管理员无权执行。这里“用户本人”指本次作出决定的项目所有者，不是任意被恢复账号本人、任意Admin、Agent或自称所有者的人。具体核验/批准/命令/登记流程为下述待审提案，不再保留“不提供人工恢复”的产品备选。匿名申请不能冻结目标管理员。禁止公开bootstrap、隐藏超级账号、skip_mfa、邮箱重置清MFA及人工SQL/改表；数据库更新只能由命令内部受审事务完成。Agent仅能设计与在后续获准范围内合成测试工具，不能替用户执行真实管理员恢复。

### 5.1 学生人工恢复：核验与管理员权限

待Astra审查的具体流程：登记受控case → 学生独立身份核验 → 管理员命令批准/签发受限重绑 → 学生确认新MFA → 命令服务完成原子切换/全撤销 → 完整重新登录。专用命令暂称 `student-recovery`，是设计名称，不是本轮已交付可执行程序；不能用通用数据库shell或网页后台操作代替。

学生核验采用两类相互独立、与该账户预登记绑定的依据：原邀请/注册主体映射及已核实在籍身份（例如当面核对或经学校正式目录独立回联），加预登记独立联系渠道的现场挑战响应。仅控制邮箱、知道昵称/邀请码、持浏览器会话、提供截图或回答课堂内容均不充分。候选材料是最小账户/邀请ID、核验方式/来源、回联渠道登记版本、核验结果和时间，不索取密码/seed或读取课堂正文。若尚无独立预登记依据或无法可靠匹配，case保持未核实，允许补齐材料/复核而不降低核验；不能由管理员自由勾“已确认”就清MFA。具体可接受证据组合、核验人员及留存期限须Astra审查并实际登记，不要求在聊天中提交证件或秘密。

执行管理员须通过其自身已完成MFA的有效短会话，在专用命令中完成2分钟内、绑定该case/学生/拟执行动作的单次强再认证；服务端重核Admin角色、security_epoch、设备/会话和专用student-recovery权限。权限由受限运维配置/授权记录列明，不由请求声称角色或普通DB登录继承；具体权限映射待审。核验者、批准者、执行者即使是同一获权管理员也必须分别记录，不虚构已实行双人审批；推荐核验与执行分离，但不是本轮新增已批准产品门槛。命令验证case的核验证据引用、批准内容和签名/服务端防篡改记录，锁内消费批准nonce，留痕后才产生受限能力。Admin权限不能借此恢复Admin目标，服务器必须重读目标角色并拒绝，不能只信命令名。

### 5.2 D01：独立信任起点、首次安装与登记维护

本版选择复用目标主机之外的**可信部署控制面D0**安装固定信任起点，不从恢复清单自举。D0指拟选云/物理主机资源所有者已经通过平台IAM、个人管理控制台或受控本机console取得的部署管理边界；其资源归属、部署授权及项目所有者认定记录独立于Classroom Web、PG和新恢复key。**当前尚未选定/建立这个真实渠道，也没有已部署PKI**；下述是必须建立并验证的信任假设和流程，不声称现有系统已经拥有可信根。

首次安装由D0的具名部署维护者执行，凭目标平台已存在、可核验的资源所有/部署IAM授权和项目用户通过该外部渠道明确批准的安装记录；不能凭新应用Admin、数据库登录或新key的PoP取得部署维护权。维护者在外部可信渠道核实“指定项目用户”的实际身份/项目归属及本人批准，将其唯一authority_id、个人OS主体、恢复公钥指纹和deployment_id固定入安装记录。安装人/批准人、既存平台授权ID、人工核对方式、版本和根指纹须留外部部署审计。可由同一自然人承担维护与恢复，但权限、用途及凭据必须分开；本版不新增强制双人批准或固定硬件数量。

首次安装记录固定：deployment_id、清单验签根R0的指纹、独立登记变更授权K_change及其维护主体、指定项目用户映射hash、工具/验证器版本和initial_version。由D0受控安装器写入恢复服务只读、普通服务不可写的部署配置，并在独立于应用PG的部署变更账本固定初始化已完成及最高生效版本/hash；恢复命令没有安装根的入口。R0用于验清单，K_change用于批准登记变更，K_recover仅证明本人恢复操作，三种用途不因同机或同一人而合并。可采用最小的固定公钥pin和既有部署审计，不要求另建CA/采购两枚硬件；具体算法、凭据形态和数量待目标平台审查。

| 主体 | 可做什么 | 不得做什么 / 权威来源 |
|---|---|---|
| D0具名部署维护者 | 凭独立既存部署权限和外部批准安装/维护根及受保护配置，执行受审登记变更命令 | 不能把恢复key持有证明当变更批准；无既存外部授权就不能首次安装 |
| 登记维护命令 | 按D0固定的K_change验证独立批准，登记/换key/撤销及推进版本 | 无普通Web调用入口；不能接受请求自带根或修改自身验签根 |
| 项目用户恢复执行身份 | 受信OS主体/本机peer＋预登记独立现场签名，执行本人的admin-recovery | 无写根、登记维护、自增权限或给其他人授权能力；不凭Web MFA或普通DB登录证明本人 |
| 普通Web/Admin/runtime/DB访问身份 | 仅原有业务或窄控制面SQL权限 | 无部署配置文件写权、无D0/K_change凭据、无登记表通用写权；可写PG行/自签清单也不构成根 |
| 目标接收浏览器/Agent | 仅批准范围内的新因子证明；Agent仅设计/合成用例 | 无安装根、维护登记、manual complete或真实恢复执行权 |

独立变更授权的规范签名内容至少含deployment、authority、action（enroll/rotate/revoke）、old_hash/new_hash、old_version/new_version、旧/新OS/key绑定摘要、nonce、签发/到期、维护者及外部批准引用。验证器只能使用D0已固定的K_change/根及当前账本状态，核对维护者当前资格、签名、部署、旧hash/版本、nonce未用和期限，等待锁后再核；新key另做PoP，但PoP只证明控制新key，不能证明它有权成为项目所有者。根本身更换只能由D0受控部署维护程序以当前有效独立变更授权批准同样的old/new绑定，不能由恢复/登记命令传入新验签根覆盖。根未安装、根全失或无法证明独立变更授权时保持不可执行；信任根重建另审，不用自带根/新key/可写PG认领。

版本只前进：D0部署账本的高水位、受签清单版本/hash和PG authority登记行必须一致且不低于已见/撤销高水位；等版本只能读同hash，变更必须new_version>old_version，撤销也写更高版本的拒绝/墓碑。旧清单/旧PG备份不能降高水位，缺失高水位不能当新环境重新初始化。维护流程先在D0有界、可重放的外部账本接受签名变更/预留单次nonce及更高版本，使不匹配期间真实恢复安全拒绝；随后用窄命令事务按第8节顺序锁部署/authority控制行及需要的actor行，核对old_hash/version、批准和nonce，再应用新状态与审计，最后通过D0安装对应只读清单/高水位证据。中断只能沿原已批准变更向前完成，不能回退旧清单“恢复可用”。

验签在锁外准备，签发/complete在锁内重新核对PG版本、撤销和与当前受保护清单/部署高水位的一致性；登记撤销与这两类写操作使用相同串行化锁行/顺序。变更的生效线性化点是PG控制状态提交；已先持锁提交的恢复写在其之前，变更提交后旧资格的写拒绝；清单/PG发布非原子时宁可拒绝，不承诺跨系统瞬时切换。D0批准包/高水位证据的真实性及新鲜度、安装权限、更新中拒绝和数据库恢复防回滚需S2审查并以后用合成材料验证，本轮未建立外部账本或登记真实资产。

上述边界信任D0/部署root及验证器完整性。部署/root被完全控制时攻击者可替换根、程序或绕数据库权限，应用层无法保证抵御其完全控制；普通DB访问与这种部署完全控制不是同一权限。当前没有成熟部署渠道时，只能以合成D0/身份/签名材料准备设计与实验用例；真实恢复启用前必须完成D0归属/权限核实、首次安装仪式、非秘密根/人映射登记、变更/撤销/高水位演练及独立批准。无需现在提交真实公钥、采购硬件或提供秘密。

### 5.3 D02：四类时间、恢复状态与写操作授权

选用不申请产品例外的方案：**issue及manual complete各自重新验证，使用绑定具体操作、单次且至多两分钟的证明**。独立管理员恢复验证新的独立运维证明，不要求先登录已丢MFA的Web账号；学生恢复执行Admin仍以当前自身Admin/MFA及专用权限重新验证。

| 时间 | 定义与服务端来源 | 约束 |
|---|---|---|
| challenge_expiry | 服务端发起挑战时确定的等待签名/校验截止，五分钟仍为候选 | 全部身份/签名校验成功时须now<challenge_expiry；nonce随机不代表已认证 |
| verified_at | 服务端实际完成本次全部因子、签名/绑定检查成功的DB时钟时间 | 不采信客户端时间、挑战创建/签名声称时间、last_seen或旧批准时间 |
| operation_deadline | 服务端形成本操作不可篡改证明时固定 | 至多verified_at+120秒，再取当前资格有效期、pending_flow_expiry（若已有）等较早值；issue/complete各有新证明与单次nonce |
| pending_flow_expiry | issue提交后受限pending允许等待新因子/人工complete的截止，十分钟仍为候选 | 只延续受限流程，不是可执行十分钟的授权；cap不能替代fresh issue/complete证明 |

挑战校验成功即结束等待阶段；例如五分钟挑战在4:59完成服务端验证，可取得从该verified_at起至多两分钟的操作证明（仍受pending/资格更早截止），不是挑战从签名时再延五分钟；5:00才完成验证则拒绝。证明必须绑定deployment/authority或actor、case、action、目标role/epoch、计划与接收端/flow版本；complete另含新因子ID与确认摘要。操作获取所有锁后用clock_timestamp再次检查now<operation_deadline、当前资格和各scope，等于或晚于截止拒绝且无消费。锁等待不能延长证明；过期须原主体重新验证取得新nonce，不能刷新旧verified_at。

issue消费的是本次签发批准nonce；manual complete消费新completion_nonce及有效cap。原签发nonce已用或原challenge/操作证明已到期，不阻止仍有效pending用fresh complete完成；不能再消费签发nonce，不能永久复用旧批准。pending不自动续期，接收端变更/能力重签须先走第5.4节status确认当前事实，再由原主体fresh证明、新计划/nonce和flow generation原子废旧；已completed禁止重新签发或再次切因子。

状态机（协议名称待S2审查）：unverified_case → verified_case → issue提交的pending → 接收端正确证明形成pending_confirmed → 原执行者手动complete提交completed；pending可expired/abandoned但不得自动complete。状态查询返回的是已提交状态或结果不明，不把客户端超时写成业务failed，不以不明状态触发恢复副作用。

两类专用命令只向受限本机控制服务提交计划，使用窄执行身份和受审事务；无公网bootstrap、通用SQL shell、任意表UPDATE/DDL/BYPASSRLS、迁移角色或正文读取。操作者不能手工SQL/改表。签发事务按第8节锁序重核当前资格、D0/authority、目标role/epoch、核验证据/接收端、期限，原子消费issue nonce、创建cap摘要及审计；不删旧因子，不签完整会话。cap只可提交指定新因子证明或显式放弃，不提供终态status授权，也不能读课堂/导出/运营、申请普通WS ticket/缓存解锁。

学生先证明当前密码（忘记时走不清MFA的普通邮箱重置），管理员恢复用独立运维身份；恢复命令不设或打印目标密码。接收端的临时公钥及随机挑战完整指纹在签名前纳入计划hash，通过独立核验渠道/受信运维通道核对；指纹只作匹配，私钥/cap/seed不交执行管理员，不经URL、argv或单一邮箱控制交付。更换接收端必须fresh批准，新flow generation废旧并审计。

浏览器只提交新TOTP持有证明，形成短效pending_confirmed，无finalize权。原case执行者手动运行complete：学生为原获权Admin，管理员为项目用户本人。各自重新验证并产生本次≤2分钟、具体操作绑定的单次complete证明；其资格和目标/flow/接收端在锁后重核。命令中断后pending到期，不由浏览器或后台自动完成。

最终命令事务原子消费completion_nonce及cap、激活新因子、废旧因子与旧恢复码组、递增唯一security_epoch、全撤旧session/device派生授权/challenge/reauth/WS票据/未来解锁grant，并写最小审计与独立identity内部撤销通知（N01）。学生执行者的当前role/epoch/专用权限及自身session/device/MFA仍有效；运维执行者的D0固定映射、authority版本/OS/key资格仍有效；原issue批准只验证归本case/flow且未撤销，不重消费。权限撤销/版本变化或PG/KEK/必需审计失败使当前事务全回滚，不能留下部分切因子/撤销。

**N02本稿明确选择：final仅废弃旧恢复码组，不生成或交付新组。** 完整重新登录（管理员密码＋新MFA）后，通过独立的显式恢复码生成/轮换操作及近期再认证生成新组；只展示一次、no-store、仅存摘要，丢响应沿SECRET_ALREADY_ISSUED/显式轮换规则处理。final无新码秘密交付，status不返回码，旧cap在epoch提升后不得领取秘密；不得复用现有confirmMfa→RecoveryCodes响应作为manual final的隐含契约。新组尚未生成的提示/状态和独立生成操作须S2审查，不影响新MFA完整登录。普通TOTP/自动恢复码路径的未审协议也不得与manual final混用。

D02静态时间线（全部未实验）：

| 反例/顺序 | 规定结果 |
|---|---|
| verified_at后四分钟才执行issue或complete | 即使挑战/流程尚有效也拒绝；取得新操作证明 |
| 五分钟挑战4:59才验证成功 / 5:00才成功 | 前者按真实verified_at派生≤2分钟且受其他较早截止；后者挑战已过期拒绝 |
| 验证后取得锁时已跨120秒 | 锁后拒绝，不消费nonce/cap、不切换、不延期 |
| 原issue批准已消费且到期，pending仍有效 | 原执行者以fresh complete证明完成；不复用或二次消费issue nonce |
| 重放旧completion_nonce/旧verified_at | 即使pending有效也拒绝；最多一次final切换/epoch递增 |

### 5.4 D03：独立授权的control-plane只读status

COMMIT响应丢失先执行独立status，不依赖已消费cap、旧目标epoch或已失效接收浏览器会话。case/op只是定位标识，绝不是授权凭据。status由原case的稳定执行actor_id（学生恢复Admin）或authority_id（项目用户）以**当前有效资格重新认证**：前者当前Admin/MFA/专用权限及有效自身会话，后者D0固定本人OS主体/独立签名资格及当前登记。status认证proof绑定只读status和case/op、期限至多两分钟；它不是恢复写操作nonce，不得消费issue/complete nonce或cap。重复合法查询可以重用其仍有效的只读证明，不获得任何写入能力。

专用status角色/固定查询入口只能按stored_executor_type+stored_executor_id筛选原case/op，读最小阶段及操作提交事实。认证主体由受信服务取得，不接受输入一个“原执行者ID”就授权。目标角色/epoch升级、target会话撤销不会破坏这条独立只读授权；执行者当前权限/authority/key被撤销则拒绝，不靠历史批准或本机命令名兜底。若项目用户既是target又是authority，按独立authority当前资格查询，不要求其目标Web MFA/session。只读事务/最小投影不走普通OwnerContext读取正文，查询前及返回前在同一受限读事务核对当前执行资格/authority撤销；与撤销采用可审的共享锁/一致读边界，先提交撤销者的后续查询必须拒绝。

只返回必要stage、对指定op有持久证据的committed/not_committed/unknown及非秘密op/audit标识；锁内读取同最终事务写入的最小op提交事实及case状态。not_committed仅在真实PG对本人case/op取得共享锁、重核当前资格及status证明期限，并能在该读取线性化点证明该op未提交且pending有效时报告，否则unknown；释放锁后可能有新complete，not_committed也从不授权盲目重做。未知/无可授权case、PG故障或一致性不充分均保持“结果不明”，不因查不到行推断失败。不得泄露他人case是否存在、target资料、旧/new seed、码组、token或签名。status本身不写case/能力/批准状态，不重签、不切因子、不自动finalize；鉴权/限流的独立最小访问日志不构成恢复业务副作用，也不得含秘密。

浏览器本稿不增加旧cap的status能力；如需显示终态，另审**完整重新登录后owner绑定的最小查询**或独立只读协议，不复用旧cap。status获知completed只能显示非秘密完成事实并要求重登/后续显式生新码；not_committed/unknown也不自动重做。reissue/continue是另一写操作，须原主体fresh≤2分钟单次证明，锁内重新读当前case/flow与原op事实，已完成则拒绝。未提交的事实与重新执行资格分开，不能让只读查询顺便授权恢复。

| 状态推演（未执行） | status规定结果 |
|---|---|
| final已提交、cap已消费、target epoch已升、CLI丢响应 | 原执行者当前资格重认证后查到committed/completed，不需旧cap或目标旧会话 |
| final明确未提交、pending仍有效 | 有一致证据才报not_committed及必要pending阶段；后续仍需fresh manual complete |
| 同一op提交仍并发/故障断连无法证明 | unknown，不推断失败、不盲目重做 |
| 执行者专用权限/authority/key已撤销 | 拒绝；历史批准不能授权查询 |
| 其他Admin、错误authority、仅case/op或浏览器旧cap | 拒绝/通用无可授权结果，不泄露该case |
| 未知case/op或PG不可用 | 结果不明，不返回可据以重做的失败结论 |
| 重复status、反复查询已消费能力的case | 仅最小事实相同/更新，无nonce消费、重签、factor/epoch写、secret返回 |

### 5.5 秘密通道、资产与真实启用条件

命令argv仅允许case/op等非秘密定位符；密码/TOTP/恢复码/PIN/私钥/cap及认证材料不进argv、环境变量、终端history/录制、stdout/stderr、通用Receipt/Outbox、日志/APM或core dump。采用不回显安全prompt/受限FD/本机认证通道；签名原语用成熟组件，秘密响应仅到绑定TLS接收端且no-store。具体禁trace/转录/转储、FD ACL、异常脱敏仍须演练，不能仅靠约定不打印。

| 真实启用时登记/验证的资产 | 必要非秘密证据 | 本轮状态 |
|---|---|---|
| D0信任起点/维护权 | 目标平台既存资源/部署授权、指定用户人工核对、固定根/K_change指纹、用途隔离、外部高水位/变更审计、根丢失处置 | 未建立真实渠道/PKI、未安装，不从待批清单认领 |
| 学生核验及执行者 | 独立渠道/在籍核验与邀请绑定、获权Admin、批准/核验引用和保留期限 | 待审/待真实登记；无正文或密码收集 |
| 本人恢复身份 | authority、OS/peer和独立签名key映射、有效/撤销/版本、凭据保管/丢失方案 | 未登记；硬件数量/算法不冻结，不要求当前采购或提交公钥 |
| 控制协议/权限 | issue/complete四时间、status只读权限、case/op/nonce/flow、N01事件、N02新码后置、精确ACL/RLS/锁序 | S2审查门禁，未实施/未机制验证 |

N03/N04：真实资产登记仅是**对应能力真实启用条件**，不阻塞用合成D0、actor/authority、签名材料准备设计、契约差异与实验用例。合成身份不是实际项目用户，不据此启用真实恢复；本轮也不运行机制实验。S1仍未整体通过，正式恢复契约未批准，TBD-08保持OPEN。

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
| 运维恢复最小层 | case/op执行主体、四时间/单次nonce/受限能力、authority当前版本、只读status投影及独立identity撤销事件 | D01–D03/N01/N02须先S2审查；真实资产只限制真实启用，合成设计/用例可准备；不建运营后台或全模型 |

迁移继续现有Alembic单链、expand-contract，实际revision名待实施时分配，不重写0001–0006。空库、0006升级、重复升级、失败回滚、旧代码兼容、受限角色直接SQL负例分别验证。代码回退不drop身份/审计/业务账本；需要停用时关闭新签发及管理写，不降低MFA。

认证引导角色只可执行固定search_path、无动态SQL、撤销PUBLIC权限的最小定位/校验函数；未知owner token查询只返回定位所需元数据。密码材料只到专用验证适配器，不向普通runtime/HTTP返回。认证运行角色非owner、非superuser、无BYPASSRLS/CREATEROLE，不继承scheduler/dispatcher/迁移身份；definer使用窄表/列权限和窄RLS，不能通过可选owner参数读取他人数据。

owner业务仍走受限runtime＋SET LOCAL＋FORCE RLS；identity控制平面保留独立角色。MFA材料及批准记录不由通用owner repository任意写；精确GRANT/REVOKE、函数owner、默认权限和升级撤权清单在各增量迁移审查。**0006 bootstrap-only范围不变**，新增auth角色不解决托管PG的非超级用户provisioning；部署平台/迁移身份选择前处理既有P3-01。

## 8. 并发锁序提案与现有路径衔接

以下是03b待审的锁序约束，不声称已经实现，也不重排无关模块。先只读最小定位不得持锁跨入后续事务；哈希/TOTP解密准备、邮件和网络I/O在锁外。准入计数短事务先完成并释放，不能拿限流锁等待身份事务。

1. 按操作需要先取既有global control/budget；注册/激活/容量或未来预算使用原路径。恢复写、status及登记维护先取独立deployment_guard控制行（恢复/只读共享，维护更新排他），核对部署高水位/当前版本，再进入必要actor行；不跨事务持锁等待D0网络I/O。不能一律先“通用认证持锁”再调用set_capacity，其原顺序仍control→admin用户锁。
2. 存在的user行按UUID排序锁定。普通业务沿现有FOR SHARE；会改变密码/因子/epoch/安全授权的事务从开始即FOR UPDATE，避免共享锁升级死锁。涉及actor/subject时一次取得全套有序user锁，禁止事后补低序user锁。注册激活继续原control → invitation → pending/proof → 新user插入的独立路径，不在持有认证锁后重入激活。
3. 设备 → 会话，各集合按ID排序；不更新活动的只读授权FOR SHARE，撤销/轮换以及会更新last_seen/idle的请求，对待写行首次即FOR UPDATE，不能先SHARE后升级。账户级撤销先锁user再按同序更新设备/会话；所有入口不能反向 session → user。敏感事务持这些锁到业务COMMIT，锁等待后重核状态与数据库时钟；活动刷新不能复活已撤销/已到期会话。并发刷新、刷新与撤销须显式竞争测试；若采用独立短touch事务，须另审其条件更新和业务授权一致性。
4. 恢复authority/权限登记 → case/op/批准nonce → auth challenge/email action/cap → MFA credential → recovery code，按种类/ID稳定排序；消费/推进行FOR UPDATE。恢复写和登记维护先取所需actor/subject用户锁再取登记行，禁止authority→user倒序，独立维护者不虚构Web用户身份。status在deployment_guard后只取当前执行actor自身所需user/device/session、authority/权限及本人case/op共享锁，既不取target用户/因子锁，也不按目标旧epoch鉴权；锁后再次检查资格和只读证明期限，不消费恢复nonce/cap。身份不由case/op决定。初次签发、最终确认、维护和status分别列完整有序锁集及隐式FK锁，不持长锁跨交互；会话创建仍在已锁user/device下插入，因子成功才发布完整会话。
5. 进入既有业务命令后保留来源祖先 → 资源/plan → task → attempt/result → owner event counter，原Receipt位置保留。安全控制面issue/complete/登记撤销**不进入这段业务counter/Outbox链**，只在其控制行后追加最小identity审计/内部事件；status不取target业务锁/不写恢复状态。nonce证明在所有锁等待后重核四时间。不得从后序事件/计数器再回取user锁。

每个实际用例须列出完整调用图（特别是identity.set_capacity、邀请、激活、device revoke、reset、WS ticket consume及task事务），证明不跨连接用“先验认证”替代同事务检查。若复用边界需要窄验证函数/授权port调整，作为03b明确最小差异交Astra，不能假称已有authorized_transaction已覆盖。真实PG用屏障控制两个顺序、lock timeout/deadlock/回滚故障；数据库事务重试必须有界且只重试数据库部分，不重复外部发送。

### 8.1 N01：选择独立identity内部撤销通知

本稿选择安全变更在同一PG事务写独立identity内部事件/提交通知，不复用业务owner_event_counters或outbox_events，不顺带新增公开WS事件。现有identity_events仅支持account_activated且按(user_id,kind)唯一，不能直接假称其已支持重复撤销；S2须审查独立内部撤销记录或该表最小兼容增量、审计/清理、ACL及无秘密payload，不改0004历史迁移。

生产者按user/authority/case等既定锁序后写identity事件；该记录不引用业务counter/Outbox/Task。若保留users FK，其隐式KEY SHARE在已经按序持有的同一事务user锁上取得。内部投递者领取事件只锁自己的事件行并提交释放，随后才另开事务查当前授权/关闭连接；不能持事件锁再等user或业务counter。通知丢失由PG实时撤销复核兜底，不能为补通知在同一安全事务调用业务事件发布器。

S2锁兼容门禁须列显式锁和所有FK/唯一约束隐式锁。原路径中LocalReconciler先锁owner counter再插业务Outbox，其owner FK取users KEY SHARE；被放弃的共享方案会出现A持user FOR UPDATE等counter、B持counter等user的双连接环。本选择令A只写identity内部事件，不等该counter，B可在A提交后继续；以后必须用真实双连接屏障、已有counter及包含隐式FK的写入证明有界完成、审计/事件原子且无Provider重发。本轮只列反例/验收计划，未运行。不得笼统把所有user锁降为NO KEY UPDATE；邮箱唯一键等仍须各自正确锁强度。若后续改回业务Outbox，必须先独立提交包含隐式FK锁的完整替代方案，不能视本稿已批准。

## 9. PR-03a/02b不变量与故障回退

保留失效身份fallback：具有登录身份（normalized_email非空）但缺验证/密码材料的账户拒绝普通owner与Principal路径，未发送任务可取消，已发送任务保unknown。历史无邮箱opaque owner shell只保留内部provisioning/worker兼容边界，principal_valid始终拒绝，永不授HTTP登录。LocalReconciler仅通过固定状态函数本地核验已落库raw，不返回正文、不授登录权限。[原实现](../../../web/backend/classroom_web/infrastructure/local_reconciler.py)与[PR-03a支持边界](../pr03a/ACCEPTANCE.md)均继续有效。

保留raw、Receipt、Outbox、fencing和may_have_charged/unknown风险；退出、重置、恢复及会话失效不得删除证据、把unknown改成未收费、触发Provider重发/切换/退款。发送授权线性化点仍为mark_submitting事务成功COMMIT：撤权先完成则拒绝；发送意图先持锁并提交则单次已授权调用可能稍后才实际进入Provider，不能承诺撤权返回后绝无物理网络调用。之后保存/发布继续按原授权检查，不重新暴露已撤正文。

PG、KEK或必须审计失败时安全变更原子回滚，不能先改因子/密码再静默丢审计；不可用时停止相应签发/管理写，保已有账本。恢复码或Cookie响应丢失不回放秘密。服务回退不提供skip_mfa或OwnerContext绕行，不启动真实Provider、私人缓存、采集/ASR、运营后台或真实收费。开放P3的具体触发判断见[范围与依赖](README.md)。
