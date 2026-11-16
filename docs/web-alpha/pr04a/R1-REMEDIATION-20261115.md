# PR-04a R1 修改与增量复审交接

负责人为林珈诚。日期为 2026-11-15，America/Los_Angeles。R1 为独审报告中的 P2 流式生命周期缺陷。当前状态为修复完成并经协调端增量复审关闭，VMware 最新定向 39 项通过，原始证据已回收并验证全部 45 个归档条目散列。

## 审查基线与复审范围

基线采用主仓 `outputs/independent-review-20261115/04a-snapshot.json`，记录时间为 2026-11-15T21:23:30.2355309Z，工作树 `J:\class-assistant\worktrees\web-pr04a-storage`。base/head 为 `81ab191b1f33de2371567eccd0ef9589d12483c2`，已有未提交候选保留。修改前逐项重算独审记录的文件散列，差异为 0。修改前文件副本位于 `work/pr04a/r1-20261115/before/`。

本次复审限定独审快照之后新增或修改的下列文件，以及直接受影响的调用链与定向回归。

| 文件 | 修改内容与直接影响 |
|---|---|
| `web/backend/classroom_web/entrypoints/media_http.py` | ASGI 中间件持有并发名额和绝对预算至完整响应结束；取消清理等待同步存储线程结束 |
| `web/backend/tests/integration/test_media_stream_lifecycle.py` | 新增真实认证/PG/S3 服务之上的受控 ASGI send/receive 回归 |
| `web/backend/tests/integration/conftest.py` | 新生命周期用例与已有对象服务套件共同显式启用 |
| `tools/pr04a/submit.ps1`、`run_guest.py` | 新增 r1 模式，仅执行生命周期新用例、Range/状态投影及原正常 HTTP、逐块撤权、HTTPS 浏览器直接回归，核对新用例收集 |
| 本文件及 IMPLEMENTATION/HANDOFF/SHARED-CHANGES | 补充 R1 处理状态、预算收尾语义、增量和证据 |

直接调用链为 `create_app → register_media → MediaRequestLifecycle → StreamingResponse/chunks → blocking → MediaService.read_block → Database.authorized_transaction / S3ObjectStore.read → remaining`。后续复审如需扩大，以本次具体影响或新失败登记依据。当前功能修复与测试准备未涉及契约、迁移、依赖锁及其他业务模块。

## 修改前后行为

| 项目 | 修改前 | 修改后 |
|---|---|---|
| 并发名额 | handler 返回响应对象即归还，响应体仍可能发送 | 从 ASGI 媒体入口持有至正常结束或异常/取消/断开后的收尾结束 |
| 入口预算 | handler finally 重置，后续读块继承空值 | 原始绝对 deadline 保留到完整响应结束，并覆盖发送背压 |
| ContextVar | token 生命周期结束早于响应体 | 创建 token 的 ASGI 任务负责 finally reset，响应子任务只继承值 |
| 同步存储线程 | 已有单次取消等待保护 | 再次取消时继续等待，线程结束后才关闭文件、重置预算和归还名额 |
| 首响应头前超时 | 外层 timeout 逃逸到框架默认纯文本 500 | 未发头时返回原 RESULT_UNKNOWN/503 JSON/no-store，错误发送最多额外 1 秒；已发头流中止并保持唯一响应头 |

超时会中止正在等待的响应发送和后续读取。已经开始的同步线程完成其有界 I/O 后再释放资源，因此资源收尾可能晚于发送 deadline。

## 必要定向验证

新用例覆盖八个合法未完成响应占满名额、第九个 429、结束一个后重新接入；首块之后同一 deadline；慢 send 到期（ASGI 2.0/2.4）；正常完成、断开、异常、重复取消的恰好一次清理；同步线程尚未结束时继续占用名额；HTTP 响应首块后撤权停止读取。各请求还检查返回后调用任务的 ContextVar 恢复。受控背压通过实际 ASGI 应用执行，认证、数据库与对象服务使用真实合成环境。

命令为 `tools/pr04a/submit.ps1 -Mode r1`，运行统一位于 VMware。用户在本机安全输入后恢复 broker，PID 39100 已核对存活；原 PID 57280 已退出。虚拟机运行列表显示同一 Windows 11 目标仍在运行。

首轮为 25 passed/9 setup errors，原因是新增测试模块尚未导入 auth fixture。第二轮为 25 passed/9 failed，原因是测试读取并发计数器的对象路径错误。两处测试准备问题已修正，原始记录保留。随后分别取得 34 项、36 项通过。协调端增量复审补查了首响应头前到期分支，本轮进一步修复并增加三个必要用例，最终 39 passed，0 failure/error/skip，PG stop=0、对象进程已停止，pip check=0。

最终来宾 run 为 `aff023d7b0914caaa49e8a9103f409ca`，请求 `pr04a-20261116-012120-9e045434`，UTC 起止为 2026-11-16T01:21:42–01:22:26。源码 ZIP SHA-256 为 `9e975536e75823bfbe348ba97ef043c8b58b47b46306a29e3bf2d24df1fb9086`，661 个实际打包源文件均已校验。39 项包含 14 项 R1 场景及 25 项直接相关既有回归。协调端已独立核对源码、broker 回执和 stdout，记录位于主仓 `outputs/independent-review-20261115/04a-r1-recheck.json`。

| 关键位置 | 说明 |
|---|---|
| media_http.py 第 18、26 行 | ASGI 请求生命周期与完整响应持有 |
| media_http.py 第 54 行 | 未发头超时的契约错误与有界发送补偿 |
| media_http.py 第 77 行 | 同步存储线程结束前的重复取消保护 |
| test_media_stream_lifecycle.py 第 96、127、148 行 | 八流名额、后续读块预算、预算到期时的线程收尾 |
| 同文件第 180、201、224 行 | 两种 ASGI 慢发送、首头前超时和错误发送补偿 |
| 同文件第 247、266、289、309 行 | 断开、异常、HTTP 流中撤权与取消保护 |
| conftest.py 第 23 行；run_guest.py 第 135、143 行；submit.ps1 第 4 行 | 测试显式启用与 r1 定向收集 |

本轮命令、全部运行回执、最终源码和相对独审快照的增量保存在 `outputs/pr04a/r1-20261115/`。其中 `delta.json` 与 `delta.patch` 用于下次复审，源文件散列以独审快照为修改前基准。五轮原始 XML/log/source-manifest 归档采用单独 `evidence.zip`，保留失败历史。

证据 ZIP SHA-256 为 `f26626b408854f7ebccdf34326028e5f0d1614e8e90bc31abb5f680db4812312`，来宾与宿主散列一致；直接解析最新 XML 得到 39 主用例、0 failure/error/skip，其中 14 项属于新增生命周期用例。原始导出第一次因来宾 J: subst 在新会话不可见而退出 1，后续用物理 C:\j 路径并在 Run 会话建立既有映射恢复，导出退出 0、CopyFromGuest 退出 0，功能测试没有因导出而重跑。

测试范围扩展的依据限于同一 R1 的真实 ASGI 生命周期和新引入的首头前超时分支，未重做整包复审。并发额度、时间预算和 send 背压使用受控短时实验，全包回归、容量统计和生产网络背压极值在本轮未执行，原因是本次修改与复审已明确限定为该增量。既有全包和边界结果仍归属旧源码快照。

本轮保留既有 Starlette/httpx 弃用警告。原整包通过结果归属其原快照，当前验证聚焦 R1 增量。契约候选审定、真实云 IAM/地域、完整证书链及音频策略前置仍按原交接处理。

协调端最终独立核验记录为主仓 `outputs/independent-review-20261115/04a-r1-final.json`，确认 9 文件增量、654 个未变基线文件、源码/证据归档散列及原始 XML 一致，主仓独审报告已关闭 R1。收尾只更新本文件的关闭状态并重生成 delta，不修改已测代码。宿主仅进行静态收尾，9 个增量文件散列、4 个 Python 文件 AST、submit.ps1 解析、4 份 Markdown 围栏及 13 个相对链接、git diff --check 均通过，记录为 `outputs/pr04a/r1-20261115/static-checks.json`。本轮整改验收完成，工作树仍为未提交候选。
