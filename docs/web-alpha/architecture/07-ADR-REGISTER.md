# PR-01 · 技术决策与实验参数登记

日期：2026-10-28。统一状态：**用户已原则批准架构并接受ADR选定的系统结构与技术栈**，批准范围见[收尾记录](PR01-CLOSEOUT.md)。Astra负责此层的冻结、接口契约和跨模块审查，修改冻结项须变更记录。11项TBD中的实验或产品参数全部继续开放，不能因本次批准自动关闭；本次仅授权PR-01本地提交，不授权后续PR、付费、部署或生产变更。

状态定义：**F**＝本交付中明确选定的结构/不变量；**E**＝实施初值，必须实验验证后锁定；**P**＝需产品/安全/上线负责人确认的政策参数。F不代表实现已测试；E/P不能作为真实用户开放依据。既有PRD已确认规则不因本表重新开放投票。

## ADR-01 · 新 Web 入口与前端栈（F）

比较原生JS扩旧页、React/Vite SPA、Next.js SSR、Vue/Vite。选择 **React + TypeScript + Vite + React Router**；登录后长驻交互与录音不需要SSR，静态产物便宜，前端可由Claude独立实现，后端保留Python。代价是新增构建与依赖；不迁移旧static。TanStack Query仅服务端读缓存，Zustand少量运行时，React Hook Form短表单；私人草稿由独立加密controller负责。Radix无样式primitives + CSS Modules/custom properties承载Aurora。详见02，具体库补丁版本在09a锁文件验收，不能混用第二套状态栈。

## ADR-02 · 模块化单体与Python（F）

比较FastAPI单体、Django整站、Node全栈、微服务。选择 **独立FastAPI应用 + Python3.12目标环境**：能复用既有纯Python规则，HTTP/ASGI与实时流可同栈；无需增加分布式服务管理。Django成熟但会增加现有逻辑移植，Node无法直接复用Python校验，微服务对30人没有收益。边界用domain/application/ports而不是同进程全局变量。Python3.12目标不改变原3.10回归，版本兼容需PR02验证。

## ADR-03 · PostgreSQL与数据访问（F）

比较SQLite/JSON、PG、托管全栈BaaS。选择 **PostgreSQL17 + SQLAlchemy2 + Alembic**；事务、复合外键、行锁、CAS、预算/邀请竞态需要PG语义，不能以本机JSON锁代替。BaaS可作为未来托管数据库实现，但不让供应商认证/存储SDK泄漏到领域层。owner复合FK + 应用授权 + FORCE RLS（非owner运行角色）；JSONB仅用在已有schema的不可变结构内容，关键关系不埋JSON。迁移单链/expand-contract；补丁、驱动、连接池大小在PR02锁定。

## ADR-04 · 持久任务与事件（F）

比较内存队列、PG任务/outbox、Celery+Redis、Kafka。选择 **PG tasks/attempts/receipts/outbox +独立worker**；不增加额外必需服务，账本与业务事务可原子提交。SKIP LOCKED领取、有限租约/fence、重启扫描；LISTEN/NOTIFY只作唤醒而不是事件事实。代价是自建一组有明确边界的调度器，需要真实PG并发/崩溃测试。外部收费unknown不自动重试；未来若队列成为实际瓶颈替换执行适配器，账本不搬出PG。

## ADR-05 · 身份与租户（F/E/P）

比较托管认证、Keycloak、应用服务端会话。选择 **应用不透明Cookie会话 + 成熟Argon2id/TOTP组件 +邮件Adapter**。原因是双模式邀请、验证激活原子名额、多设备/撤销、Admin无正文权限需要与本库协调；不必为30人运营独立Keycloak。代价是认证流程测试责任明确属于项目，不能宣称组件自动保证安全。

F：用户即租户、Student/Admin、CSRF+Origin、Admin强制MFA、无JWT长期localStorage、密码重置撤会话。E：Argon2id可从m=64MiB/t=3/p=1进行资源与登录并发实验；最低12字符沿PRD初值，具体安全策略/管理员会话/再认证窗口仍TBD08。不能因本ADR静默提高或降低产品密码规则。P：管理员丢失MFA独立恢复流程。将来原生采用PKCE等委托认证另写ADR。

## ADR-06 · HTTP、WSS和契约源（F）

比较REST+WSS、GraphQL、纯WSS命令。选择 **OpenAPI3.1 HTTP `/api/web/v1` + JSON Schema 2020-12事件/WS命令**。HTTP负责可审计资源命令与幂等，WSS负责音频传输/字幕/状态；不让所有普通CRUD依赖在线长连接。API领域版本v1、契约包0.1.0、事件schema_version分别记录。前端使用生成类型+Mock handlers；后端用schema验证响应，禁止共享Python内部类作为前端编译依赖。

非破坏添加字段仅对显式允许的扩展位置；严格请求schema新增可选字段需要更新兼容表，不能任意发送未知字段。移除/改义/枚举收窄视为破坏；Alpha也不允许静默改名。候选契约变更需Astra审查、Claude/Codex确认及同套正反fixtures，版本与迁移/兼容窗口一起记录。

## ADR-07 · 文件与流式持久性（F/E）

比较服务器本地磁盘、私有对象存储、浏览器直连provider。选择 **私有对象存储 + staging/manifest/hash +PG回执**；生产媒体由应用授权代理。可替换ObjectStore端口，不绑定整个领域到OSS/S3 SDK。开发兼容存储由04a决定、生产首选北京地域，与ASR同域降低跨域延迟。

F：本地安全持久后上传、云对象+PG确认后才durable，再送ASR；内部格式固定PCM16/16k/mono，传输frame与durable chunk不同。E：供应商实际接受格式、约1s持久块与100ms发送帧初值，真实重采样/尾帧/对象开销/延迟必须测。压缩/打包优化不能改变块ID与偏移或绕过删除清单。供应商文件拉取短链与浏览器媒体代理分开。

## ADR-08 · ASR、AI与来源（F/E）

实时首选 **qwen-audio-3.1-asr-flash-streaming / cn-beijing** 来自PRD增补；Filetrans独立Adapter/POC。保留备用商接口，不自动切换、不并行双收费。LLM只选择一个经样本/预算批准的默认配置，具体型号E未冻结；不能虚构某型号质量/价格。上游SDK只在Adapter内部，版本/API族经06a验证后锁定。

生成采用 prepare/confirm/execute；不可变来源快照、材料版本、模型/提示/结构/价格版本。partial不是证据；30秒/2分钟/意群用稳定时间范围，课件逐张确认。首AI稿也是candidate，采用独立CAS。无工具自主执行/网络浏览/任意文件读取；内容注入只作为材料。

## ADR-09 · 离线和多设备（F/E/P）

比较不持久缓存、明文IndexedDB、浏览器持久key、服务端封装key+内存解锁。选择 **三池加密IndexedDB +服务端封装DEK +在线认证后内存key**；满足关闭重开需认证、未信任设备仅本次录音授权，代价是不能独立离线登录，也不防已解锁XSS。AES-GCM/AAD与nonce规则F；信任/撤销/重置恢复和解锁窗口必须05a POC后P批准。

同步用op+CAS+显式冲突，不使用CRDT（无协同编辑需求）、不LWW静默覆盖。14天/20节/50MB是PRD学习池初值；显示单位统一十进制bytes，恢复/草稿容量E待测。静态SW不缓存私人API。关键词用PG子串/trigram，不增加向量检索。

## ADR-10 · 删除与备份（F/P）

比较只软删、桶lifecycle、应用任务+独立删除日志。选择 **即时撤访问/墓碑+持久清除任务+对象版本清单+异故障域日志**，lifecycle仅兜底。恢复先重放删除/撤权，unknown不重放。正常7天、24h目标沿PRD；异常截止、备份保留/RPO/RTO/审计例外仍P而非随便写默认值。原音不进长保留通用备份为推荐待确认方案，详见05。

## ADR-11 · 费用保护（F/E/P）

比较事后报表、客户端扣次数、服务端原子预留。选择 **时长/次数与micro-CNY费用双账本，用户/任务/平台三层原子hold**。预算60/80/100阈值不等于厂商硬封顶；20%预留不当普通额度。unknown不可按零释放，累计usage按差额。版本化PriceBook和有界最大输入/输出/图片/音频窗口是收费前置。单任务上限/Token秒/失败扣还/跨月归属P/E在11a/06a前关闭，默认真实收费闸门关闭。

## ADR-12 · 部署与扩展（F/E）

比较单VM模块单体、托管全栈、多节点集群。选择 **单VM Caddy +API/网关+worker+PG持久卷、外部私有对象与备份**作为低成本首选拓扑；2vCPU/4GiB等仅E压测候选，实际报价与预算决定配置。先增加worker/优化数据库，再拆PG/多API；不预设百万用户。支持原生通过capture/secure-store/transport/lifecycle ports扩展，Alpha不提前建原生客户端/OAuth服务器。

## ADR-13 · 跨Agent交付（F）

按用户2026-10-28决定：**Astra**设计整体架构/技术选型/契约和跨模块审查；**Claude Code（Claude Desktop）**主做PR09a/09b/10a/10b及相关前端；**Codex**负责后端、DB、ASR、AI、身份、基础设施与集成；**Terra**可选视觉探索。模型名称不自动授予产品变更/收费/发布权限。

共用AGENTS为唯一协作规则，CLAUDE.md是薄入口；详细UI交接独立09文档。任务分支/Worktree独立，契约在独立变更中经Astra审查；禁止两个Agent同时改同文件或以聊天约定代替Git记录。允许Claude自决局部组件拆分/实现细节，不允许自行变更API/状态/安全/配额/PRD。详见08/09。

## 11项TBD的当前处理

| TBD | PR-01已决定 | 仍需关闭证据 / 最晚关口 | 拥有者 |
|---|---|---|---|
| 01 ASR | 首选/Adapter、格式/持久边界、未知语义 | 真实SDK/权限、Token秒、热词/时间戳、连接寿命、Filetrans、30段/3次长时；06真实接入 | Codex实施，Astra审查，产品质量确认 |
| 02 费用 | 双账本、hold、unknown保留、预算gate | LLM型号/费率/最大Token/每任务与流窗口、失败扣还/跨月签署；任意收费前 | Codex + 产品 |
| 03 缓存安全 | 三池/DEK封装/内存key、重登认证 | 重置/撤销/恢复/共享设备/解锁窗口POC与批准；05b/08启用前 | Codex + Claude + 安全审查 |
| 04 删除异常 | 明确状态/日志/访问先禁/清单 | 正常/迟到/未结束/导入的hard deadline、短链边界和24h演练；04b | 产品 + Codex |
| 05 备份 | 异故障域日志、恢复先墓碑/撤权 | 备份内容/保留/RPO/RTO/审计例外与恢复实测；04b必要策略、Gate1全关 | 运维/产品 + Codex |
| 06 容量 | 三池独立、超限不能丢唯一副本、统一bytes | 文件/包/草稿/恢复/导出限额与真机/大包；04/05/08验收 | Codex + Claude |
| 07 质量 | 稳定来源与结构/事实分开、无虚构承诺 | WER/CER/混合规则、术语/总结阈值人工签署；Gate1 | 质量/产品 |
| 08 身份 | 会话/CSRF/MFA/邀请事务及组件路线 | 哈希成本、管理员窗口、恢复演练、密码策略；03b | Codex + 安全/产品 |
| 09 栈与规模 | 框架/DB/任务/拓扑/ports已选定 | 具体版本锁、并发/延迟/规格/浏览器版本；09a构建/12b/Gate1 | Astra + Codex + Claude |
| 10 隐私上线 | 数据流/应用角色边界/最小发送 | 供应商留存条款、录音许可、地域/必要上线手续；真实学生前 | 上线负责人 |
| 11 视觉 | tokens分层/组件契约/低特效/状态 | 语义色/混合对比、读屏/键盘/320px/200%、真机性能；设计QA/Gate1 | Claude，Terra可辅助 |

关闭单必须记录：参数旧/新值、证据位置与日期、实际环境/模型版本、风险/兼容/费用变化、确认人、后续PR。PR-00原DECISIONS与验收作为历史不改写；本表只记录PR-01推进的结构决策与仍开放实验，不伪造全部TBD已关闭。

## 变更纪律与资料

设计变更先记录 `contracts/CHANGELOG.md` 的版本/影响，再更新OpenAPI/事件/schema/fixtures/数据映射/前端mock；破坏性变更附兼容窗口与受影响PR。产品政策变更另有需求ID与用户明确确认。发布审批不同于技术评审。

参考技术规范（用于实施查阅，未声称本PR在线逐页核验或安装最新版本）：[OpenAPI3.1](https://spec.openapis.org/oas/v3.1.0)、[JSON Schema2020-12](https://json-schema.org/draft/2020-12)、[PostgreSQL行安全](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)、[PG显式锁](https://www.postgresql.org/docs/17/explicit-locking.html)、[Web Audio](https://www.w3.org/TR/webaudio/)、[OWASP会话](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[WCAG2.2](https://www.w3.org/TR/WCAG22/)。供应商协议以PRD附录链接与授权POC锁定版本为准。
