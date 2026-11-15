# PR-03b S3 BATCH 2：专用 PostgreSQL 启动与只读归属验证

日期：2026-11-07（America/Los_Angeles）。状态：**本机定向诊断与拒绝前移修订已交审；真实验证仍 BLOCKED，尚未达到 BATCH2 验收。** 系统接口返回的监听状态不一致，尚未修复本机上游原因。PG目标未验证、一般数据库执行权关闭。原失败回执与证据[原样保留](../../../../outputs/pr03b/s3-batch2-20261107-01/DELIVERY.md)；新增诊断、修订和实际拒绝结果见[本轮交付回执](../../../../outputs/pr03b/s3-batch2-local-diagnosis-20261107-01/DELIVERY.md)。不自行批准本批或进入业务迁移、认证实现、真实恢复及部署。

输入仍为`e4853223847b3c26bfc74fa8fb2271cdb10e18b9`、唯一父`e02614259b5e6483127e14f06e78cdb13a89f0c5`，加已获批准的S1/S2及BATCH1精确快照。[APPROVE S3 BATCH 1](../../../../outputs/pr03b/s3-independent-review-20261107-01/REVIEW.md)与消费者契约确认保持生效，本批不改写旧四文件、正式契约或历史报告。

## 最小文件范围

只新增本文及四份专用测试工具，400份原有源文件保持字节不变：

- [native_process.py](../../../../tools/pr03b/native_process.py)：Windows 64-bit原生PID/FILETIME/exe/父进程/命令行/数据目录、IPv4/IPv6 TCP监听与Job内网络快照；悬停创建、私有Job归属后才恢复执行，关闭只针对自身持有的Job句柄。
- [pg_probe.py](../../../../tools/pr03b/pg_probe.py)：两条固定只读系统目录SELECT，无任意SQL/URL/snapshot入口；活连接内验证backend PID的原生父进程与Job归属，rollback/close。
- [pg_launcher.py](../../../../tools/pr03b/pg_launcher.py)：只开放自身合成自检及一次性`verify-live`流程；全新run、固定工具链、固定配置与唯一空测试库，最后关闭自己的进程树。
- [selftest_batch2.py](../../../../tools/pr03b/selftest_batch2.py)：合成连接/原生观察、真实判定函数、前置拒绝和watchdog/副作用边界，不以注入值冒充真实PG验证。

既有shared URL guard及B1入口原样保留。无业务后端/测试/迁移、前端类型/Mock、依赖或正式contracts修改。工作树和暂存区状态、旧证据与helper保护由本批PREFLIGHT和最终清单固定，没有新commit SHA。

## 工具链及创建清单

本机已有PostgreSQL17.11主包及八个依赖归档，先与原审定来源的SHA逐项核对。首个工具链的2439个payload文件均匹配，但真实initdb在加载postgres依赖时暴露缺失iconv.dll；这次失败和原工具链原样保留。静态PE闭包确认唯一缺项后，仅从官方conda-forge补取已记录SHA的libiconv1.18归档，在本树`work/pr03b/toolchains/pg17.11-win64-7409a40dc2bda548/runtime`重新构建10包/2446文件的完整工具链，未放宽PATH或修改系统库，不复制旧PG数据/配置/日志。实际来源、完整文件hash及45个PE的原生依赖闭包见[最终TOOLCHAIN-PROVENANCE](../../../../outputs/pr03b/s3-batch2-20261107-01/TOOLCHAIN-PROVENANCE-02.json)，[原清单](../../../../outputs/pr03b/s3-batch2-20261107-01/TOOLCHAIN-PROVENANCE.json)仍保留。launcher将最终清单精确SHA固定在源码，实际根路径由自身ROOT派生，不接受清单自报外部路径授权。只补任务目录内必要运行文件，未改Python/项目依赖或安装全局组件。

每次执行只新建`work/pr03b/runs/<32位run_id>/pg/data`，拒绝已存在run、任意外部data_dir和resume。数据库名固定`pr02a_<run_id>`，bootstrap登录固定`pr02a_<run_id>_bootstrap`，继续复用原guard的pr02a_限制。端口由字面127.0.0.1独占临时绑定选择，排除5432；释放到创建PG之间若被争用则失败，不连接另一个进程或停止占用者。

`initdb`只创建新集群系统目录、template0/template1/postgres及本run的合成bootstrap超用户。这不是应用身份登记、托管数据库通用provisioning或PR03a P3-01关闭。随后唯一受限目录DDL为`CREATE DATABASE <该run精确名称> TEMPLATE template0 ENCODING UTF8`，无业务表、业务行、应用角色或迁移。该动作不能重做，也没有通用execute/SQL/迁移接口。

## 真实验证顺序与执行边界

以下为候选工具的受管流程，不代表全部已经执行。原轮三次实际尝试均在只读SQL握手前失败且已确认关闭：第一次为缺失DLL（已补齐），后两次PG已ready，但原生监听状态门禁拒绝，仅执行到第4步。本轮新增能力控制后，真实入口在initdb之前即拒绝（无helper/PG/SQL），详见下节。两轮均未执行后续probe或唯一空测试库CREATE DATABASE；合成自检不能替代这些缺失的真实成绩。

1. 拒绝继承的所有大小写/空值PG*及应用DB URL，核准工具链完整文件集/hash。在自身独占的127.0.0.1端口reservation上listen，核SO_ACCEPTCONN=1及listener-only/ALL两族视图均确认同一精确LISTEN=2；不一致即在任何initdb/helper/PG/SQL之前拒绝并关闭reservation。所有可控work/temp/output在J盘；子进程只继承最小OS环境，不读真实服务配置或账户秘密。
2. `initdb`与`pg_controldata`也使用独立受管Job：CREATE_SUSPENDED→指定继承句柄→AssignProcessToJobObject→核归属→ResumeThread；超时关闭该Job并确认全树退出，无Popen后赋权或不受管降级。PG主进程同样受管，Job不允许breakaway，父进程退出时KILL_ON_JOB_CLOSE生效。
3. 写入固定postgresql/hba/ident/auto.conf并固定hash：仅127.0.0.1监听、限定本run登录和两个获准DB、SCRAM、禁Unix socket/预加载/归档/复制/并行工作和autovacuum；server默认只读。运行只读`pg_controldata`取得新集群system_identifier作为独立磁盘基准。
4. 从实际created HANDLE固定主PID/精确FILETIME/exe，核原生父PID、精确argv/-D及两族监听表；须唯一127.0.0.1目标监听且属于本Job。listener-only与同PID/端口ALL视图必须同时确认同一LISTEN=2，ALL中的state0拒绝；前后均重新检查，不因能力控制一次成功而跳过。两个视图同属IP Helper，不能称独立内核可信证明。marker由已创建进程和新数据目录建立，仅作运行清单，不成为自证授权来源。
5. 每次连接前后重新核本地marker/postmaster/config/exe/进程/监听及Job，连接仅host+hostaddr=127.0.0.1、精确port/login/DB、固定合成password，不接受query/service/DNS重定向。首次只读连接限新集群内置postgres；其例外是launcher内部用途，不放宽shared URL guard。
6. 同一READ ONLY、REPEATABLE READ事务读取精确身份和非秘密目录。SQL backend仍活着时，核其原生exe、父postmaster及Job归属；system_identifier与pg_controldata一致，实际地址/端口、路径、精确版本/启动时间、只读状态相符。事务始终rollback/close。连接结束后待本backend退出再观测，防止把会话退出瞬态误报为稳定快照。
7. 只在fresh边界成立时开放一次内部空测试库创建：连接仍绑定该Job的bootstrap系统库，临时覆写自身session默认只读以执行唯一CREATE DATABASE；随后回到固定只读probe。该窄例外不产生一般数据库执行权。
8. 对最终测试库重复只读检查，确认目录快照稳定、无非系统关系、无额外用户角色/数据库/扩展；真实破坏owner nonce的反例须在连接准入前拒绝，再恢复本run原marker。没有业务写或收费重试。
9. 最后只通过持有的私有Job关闭全树；不使用`pg_ctl stop`或PID文件选择信号目标。清理必须确认所有受管进程退出、目标监听消失；失败保留active未知，不伪报已停。再次使用已停止run的边界应拒绝。

这是一次性测试集群，Job停止方式不承诺clean shutdown；数据目录仅保留证据，不恢复、不重用。本批没有复原/迁移/清理旧集群或递归删除路径。

probe有固定connect/statement/lock/idle超时；这些不是客户端硬期限。launcher另在6秒等待触发时关闭本次Job，随后进行有界清理/worker结束核对，超时不使用迟到结果、不复用连接。不能把“6秒触发”写成含清理在内总耗时必≤6秒。

## 证据含义与验证方式

```powershell
# cwd: J:\class-assistant\worktrees\web-pr03b-sessions
& 'J:/class-assistant/worktrees/web-pr03a-identity/work/pr03a/venv/Scripts/python.exe' -I -B tools/pr03b/pg_launcher.py selftest --output-dir 'J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/<新目录>/selftest'
& 'J:/class-assistant/worktrees/web-pr03a-identity/work/pr03a/venv/Scripts/python.exe' -I -B tools/pr03b/pg_launcher.py verify-live --output-dir 'J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/<新目录>/live'
```

只使用已存在Python3.12.14/SQLAlchemy2.0.46/psycopg3.3.3环境作为解释器和库来源，不执行旧runner或旧集群。输出目录必须新建且在本树outputs/pr03b下。具体实际命令、退出码、首次失败及修正、最终case/快照/补丁均以本轮回执为准，示例命令不是通过成绩。

成功的`target_verified`仅表示该次运行中，已对该新PG进程/端口/目录/system-id与精确合成DB/login完成真实联证。结束时`target_active=false`；保存的JSON、plan、marker或旧target_verified不是下一次执行能力。每次均重新建立run和真实边界，`database_execution_allowed`始终false，无任意SQL/业务套件入口。

原生TCP快照只证明观测时本Job实际流量/监听为loopback，**不等于OS防火墙或native libpq出网阻断**。本批不修改系统防火墙、服务、全局策略或其他进程；不声称能抵御控制同一OS账号/宿主的恶意修改。更广机制实验的原生网络隔离和最小数据库权限仍保留后续门禁，不能由本次metadata只读成功代替。

交付只复制受guard有界读取的本run计划、marker、进程/端口/只读目录事实及合成日志，不打包完整PGDATA、pg_authid、pwfile或任何真实业务内容。日志SQL仅限固定目录读取及明确的一次CREATE DATABASE；名单、MFA、恢复码、D0/真实key、真实身份和收费Provider均未触及。

## 独审与后续停止点

独审须核五文件累计候选差异及本轮四文件修订，获批B1/S2快照、工具链来源、每项实际/合成证据边界、helper与PG进程树收束、尚缺的真实listener/backend联证、只读会话及唯一目录DDL、超时与清理失败的如实表达。本轮保护401份非目标源文件、445份旧输出、78份旧helper，并保留原轮全部冻结证据；原轮400原源/306旧证据/39旧helper的保护检查不被覆写。结果不预设整个BATCH2或S3通过。

本轮诊断已定位到公开IP Helper上游的NSI返回数据也异常：独立C# 32/64位、禁site的Python及已加载旧9278 DLL均复现自身真实listener与状态不一致；ABI、行起点、端口/PID解析、延迟、排序、未发生accept以及预填缓冲未写入等解释已被对照排除。目标DLL代码/实际NSI绑定核对未发现替换证据，但不代表系统或内核已获完整可信证明。NSI与OWNER_MODULE仅用于诊断，不是guard的替代来源；具体内核/提供者原因仍未证实，不能将DLL版本差异或hotpatch映射直接报为根因。

最小修订保持0/1不作为LISTEN：新增ALL交叉检查，阻止listener-only偶然报2即成功；将观察能力控制前移到initdb之前。最终154项合成自检通过，真实入口重跑退出1，failed_phase=native_listener_control、commands=[]、无PGDATA、无SQL握手、target_verified=false、target_active=false、database_execution_allowed=false；工具链2446文件及源码前后hash一致。这是安全拒绝验证，不是本机系统修复或真实握手通过。没有替换系统组件、重启、改网络/防火墙配置或试到绿色。

继续正向验证的必要条件是：获准范围内提供可信且一致的端口状态来源/测试主机，先通过同一能力控制和guard，再全新run完成真实PG握手；本轮不自行修复宿主系统或切换数据库。正向成功验收器的原断言保留且未运行，本轮检查只覆盖诊断、修订和拒绝交付，不能表述为BATCH2通过。

本候选及阻塞证据交审后停止。后续业务迁移/0006升级、角色ACL/RLS、完整认证/HTTP/WS、N01/N02、A01–A46/十九交错、真实D0/登记/恢复/启用均仍须对应明确范围与原有门禁。D01–D03保持设计层CLOSED，消费者契约确认项已关闭；TBD-08、U01/U02及既有P3继续原状态，条件风险未生效。不commit、Push、合并或部署。
