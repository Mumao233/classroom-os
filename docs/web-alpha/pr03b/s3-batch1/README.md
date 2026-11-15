# PR-03b S3 首批：runner/guard 隔离适配交审

日期：2026-11-07（America/Los_Angeles）。状态：**本批实现候选及自身合成自检，待独立审查；不进入数据库迁移、认证机制实验或完整认证实现。**

输入为 HEAD `e4853223847b3c26bfc74fa8fb2271cdb10e18b9`、唯一父 `e02614259b5e6483127e14f06e78cdb13a89f0c5`，加获批 S1/S2 的未提交精确快照。[APPROVE S2](../../../../outputs/pr03b/s2-independent-review-20261106-01/REVIEW.md)及其16文件清单仍是正式依据；本批不改写其正文、快照或历史状态。D01–D03保持设计层CLOSED，TBD-08继续OPEN — PARTIALLY DECIDED，补偿未实测则条件风险未生效。

## 本批源文件与边界

只新增下列三份工具和本文，不修改已存在源文件、正式契约、旧runner/fixture、认证代码、迁移、前端生成类型、Mock或测试：

- [target_guard.py](../../../../tools/pr03b/target_guard.py)：复用原shared URL guard，加精确run绑定、本地归属读取、Windows原生进程观察及未来server事实比较。
- [run_tests.py](../../../../tools/pr03b/run_tests.py)：仅`selftest`与`validate-plan`两个入口，无连接数据库、建库、创建角色、迁移、pytest业务套件、停止进程或清理数据库入口。
- [selftest_runner.py](../../../../tools/pr03b/selftest_runner.py)：标准库unittest，使用本次work内合成文件及明确注入的观察值；不启动PG或认证机制。

正式交审对象、实际命令/退出码、逐case结果、快照及补丁以[交付回执](../../../../outputs/pr03b/s3-guard-20261107-01/DELIVERY.md)为准。没有新commit SHA；本批独审应对新增四文件及证据给出明确范围的APPROVE或REQUEST CHANGES。

## 隔离模型和证据层次

| 层 | 本批能力与拒绝边界 | 不能据此声称 |
|---|---|---|
| 计划 | trusted root由runner源码位置决定；run_id固定32位小写hex，instance_nonce64位hex；数据目录严格`work/pr03b/runs/<run_id>/pg/data`；DB精确`pr02a_<run_id>`、bootstrap登录精确`pr02a_<run_id>_bootstrap` | 任意同前缀DB/role可用；plan自报root、binary hash或nonce就是外部授权 |
| URL/环境 | 原shared guard不改：`postgresql+psycopg`、字面`127.0.0.1`、显式非5432端口、保留`pr02a_`、禁全部query；所有大小写/空值/未知`PG*`先拒，继承应用DB URL也拒；只接固定合成password，避免pgpass回退 | 先清除污染变量再接受；换成`pr03b_`即可绕过旧guard；DNS/IPv6/另一loopback自动兼容 |
| 文件/路径 | UNC/设备路径/控制字符/未配对surrogate/`..`词法先拒，随后检查完整祖先链reparse/symlink；plan路径固定；JSON大小/字段/重复键检查，marker与postmaster文件两次读取且身份/大小/mtime稳定 | 同名目录、复制旧marker、邻run路径或任意外部snapshot证明当前集群归属；能防恶意同OS账号修改 |
| 本地观察 | 默认Windows API读取实际PID、精确FILETIME creation token、exe路径；PID限制原生DWORD且句柄PID再核。marker、postmaster.pid、固定exe hash与前后进程观察绑定；已退出/复用/漂移拒绝 | 合成observer等于实际PG进程证明；秒级postmaster时间可代替精确creation token；local检查已完成PG握手 |
| server事实 | 纯比较未来可信只读probe得到的login/database/data_dir、地址/端口/listen、精确版本、postmaster PID/start；字段缺失/异型/错值拒绝 | 调用者提交JSON就是实际server证据；比较函数返回成功可授迁移或业务写权限 |

`instance_nonce`只是每次运行的合成归属标识，不是认证key或D0。plan与marker是未来受控launcher的创建清单；本批只创建自检fixture，不建立真实cluster/marker/登记。全局已批准工具可只读使用，exe不要求位于run目录，但路径与hash必须明确；这一pin仍须后续组件来源核验，不由自报hash自证可信。

本批CLI没有接收server observation、任意observer、任意test path或动态插件的选项。即使合法计划返回exit0，结果仍固定`evidence_kind=plan_only`、`target_verified=false`、`database_execution_allowed=false`。Python注入点只供受信自检代码调用，不是HTTP/CLI身份或授权入口。

自检入口安装Python audit hook，在实际socket连接/绑定、DNS和子进程执行之前拒绝；本批根本不加载DB连接器、alembic命令或旧业务pytest。hook是受信Python测试的误调用保护，不是OS沙箱，不声称能覆盖native libpq或任意恶意扩展。因此不能用本次hook/合成值证明未来真实PG出网受限。

## 自检与最小运行方式

使用已存在的Python3.12/SQLAlchemy2.0.46环境，不安装依赖，不读取正式配置/真实数据。示例路径中的既有虚拟环境只作解释器和已安装库来源，未调用该旧工作树runner或集群：

```powershell
# cwd: J:\class-assistant\worktrees\web-pr03b-sessions
& 'J:/class-assistant/worktrees/web-pr03a-identity/work/pr03a/venv/Scripts/python.exe' -I -B tools/pr03b/run_tests.py selftest --output-dir 'J:/class-assistant/worktrees/web-pr03b-sessions/outputs/pr03b/<新的审查目录>/selftest'
```

输出目录必须位于本工作树`outputs/pr03b/`且尚不存在，work使用本次独立`work/pr03b/selftests/<随机ID>/`，保留合成文件以便核对，不执行递归删除。所有可控制临时目录都在J盘。`validate-plan --admin-url <显式合成URL> --plan <本run绝对plan.json>`只读计划，不准备/启动集群；本批没有提供可直接启用数据库执行的命令。

验收覆盖URL/环境前置拒绝、精确run/DB/role、JSON与路径、marker/postmaster/原生PID范围与creation token、exe hash、server字段、证据漂移、脱敏及拒绝后零连接/迁移/child调用。成功要求相关case全部通过、零fail/error/skip，工具与396份原输入hash稳定；任何缺证据或真实观察不可用不能冒称实际target verified。既有Windows/Node/464后端历史成绩不替代本批证据，本批不机械重跑这些无关套件。

## 消费者确认

已通过用户授权向“实现 Aurora 双主题设计系统”对话请求确认。该对话此前由用户明确指定Codex临时接任原Claude前端负责人；本次[实际ACK原样副本](../../../../outputs/pr03b/s3-guard-20261107-01/CONSUMER-ACK.md)结论为**ACCEPT CONSUMER CONTRACT**，不是Claude Code签名或作者代签。ACK核对S2全部16文件hash/字节，并接受严格三分支、操作绑定、密码/名单、32KiB例外、auto/manual分离和一次秘密交付/同版本同步义务。

该确认只关闭本批“取得实际前端消费者契约确认”的交付项。生成类型、Mock/UI/测试及真实客户端同步尚未实施，预期漂移仍在；不声称gen check/build/browser已通过。若后续前端负责人再次移交，执行者应沿该精确快照与义务交接，不能扩大为已实现兼容。

## 独审后仍需保留的下一阶段门禁

本批可交审，作者合成自检不是独立批准。独审通过也不自动运行数据库或认证实验。后续须按明确授权的下一批范围，先实现并审查run-owned launcher/真实OS及端口归属观察、可信只读PG握手和执行前/探测前后重新核验、受限DB/角色精确创建清单与原生网络隔离；无实际握手不得将target_verified置true，更不能复用旧集群。

只有实际隔离门禁成立后，才能进入获授权的空库/0006升级、角色/ACL/RLS等合成机制步骤；继续保留可信bootstrap/PR03a P3-01、PR02b reconcile HTTP与多Attempt、PR09a及其他P3触发节点。完整认证、A01–A46/十九交错、A45双连接、N02秘密通道、真实D0/身份/核验/留存及恢复启用均未因本批获批；U01/U02仍按既有节点决定。无需在本批采购硬件、提供真实key或重新投票既定政策。

本批无commit、Push、合并、部署，交付后停止。
