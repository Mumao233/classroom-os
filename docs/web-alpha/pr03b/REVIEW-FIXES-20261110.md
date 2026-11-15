# PR-03b 独审 F01/F02 修复交付

负责人：林珈诚。工作包 PR-03b；本轮未创建 GitHub PR。日期：2026-11-10（America/Los_Angeles）。

**F01/F02 已实施修复并通过本轮短回归，等待独立复审；整包继续 REQUEST CHANGES，不自行关闭 S3/S4。**

输入为[独立审核报告](../../../outputs/pr03b/product-independent-review-20261110-01/REVIEW.md)及其两条发现。开始前重新核对了被审537份源码，全部与原manifest一致；HEAD为e4853223847b3c26bfc74fa8fb2271cdb10e18b9。旧源码包、证据包和独审目录不覆写。

## 修复

### F01 / P1：再认证共享 MFA 失败预算

`web/backend/classroom_web/application/auth.py`：对需要MFA的再认证，使用与登录相同的`mfa_fail`、稳定账户owner_id、既有PG预算函数。验证前检查；错误、缺失、重放TOTP导致UNAUTHENTICATED时累计失败。阈值保持原5次/900秒，成功不清预算；无MFA学生分支不新增MFA要求。未改变密码预算、MFA强度、SQL迁移或契约。

新增真实合成PG回归分别覆盖学生/管理员：先从登录累积2次，再从独立服务实例/PG连接池的再认证累积缺失、错误和重放3次；预算达到5后，两个已签发会话及新建登录challenge均429，且TOTP验证函数未再被调用。另覆盖一次错误后合法下一计数器验证码成功、会话保留、预算仍为1。这是两服务实例/独立连接池验证，不称为多进程压力验收。

### F02 / P2：凭据错误保留有效 CSRF/验证状态

`web/frontend/src/api/auth-client.ts`：写请求401后只读GET `/auth/csrf` 核对上下文。相同CSRF说明当前Cookie上下文仍有效，保留preauth、MFA/受限恢复状态和接收端密钥，让用户明确再次提交；如恢复nonce更新则同步。上下文改变或无法安全确认时清除旧密钥/flow并发出本地AUTH_CONTEXT_EXPIRED；真正失效的会话读也会清除。异步旧probe不得清除更新的账户上下文。

`web/frontend/src/app/AuthWorkbench.tsx`：收到本地上下文失效信号后清除旧session/challenge/seed/codes/列表，重新只读bootstrap并回到登录。密码/验证码错误仍留在原流程供用户改正。没有自动重放POST或秘密操作，没有新增公共HTTP错误码。

生产前端新增连续操作回归：错误密码→正确密码、错误TOTP→合法下一验证码、再认证改正输入、受限恢复错误当前密码后保留绑定密钥、匿名/MFA/会话/恢复上下文真正失效，以及延迟probe不得破坏新状态。既有RESULT_UNKNOWN不自动重试控制继续通过。

浏览器helper仅在`mode=browser`启用本次重试检查，其他负载模式保持原启动行为；本轮没有运行负载模式。实际浏览器验证错误密码后不刷新页面即可登录（恰好两次显式POST）；随后从服务端注销但保持旧UI，再点查看会话，确认UI清理并重新显示登录，原WSS1008撤权亦通过。

## 本次实际验证

命令使用任务现有Python3.12、Node和依赖；未安装/升级工具。运行脚本与完整argv/退出码存于`work/pr03b/review-fixes-20261110-01/`和同名outputs目录。

| 检查 | 本次实际结果 |
|---|---|
| 后端单元 | **172/172 PASS，3.34秒**；backend-eb7ac7a73e1245c285712d6375081379.*，退出0。 |
| 认证/人工恢复/邮件短回归 | **45/45 PASS，79.58秒**；backend-1615934cdda84a09b61b4102c1953434.*。包括新增4个参数化真实PG场景、登录错误TOTP后合法重试。早一轮45/45、58.03秒的dc325...亦保留。 |
| 加强跨新challenge检查 | 上述两角色场景再定向 **2/2 PASS，6.35秒，43 deselected**；backend-90a9224fd3a144abb699a7fc87f8e011.*，不是新增两个独立测试或完整45项再次执行。 |
| 前端 | **213/213 PASS**；frontend-checks-1794361445544256300-3.log。生成类型校验、TypeScript、lint、Vitest、生产build及verify-build共6命令全部退出0。 |
| 真实浏览器 | **1/1 PASS，39.11秒**；backend-462b404a229a402181f7b894f4a88cc6.*，对应browser-dc86a4c586eb44aca11d17684dfc484d/BROWSER.json。真实HTTPS CA链、错误hostname拒绝、错误密码改正、失效会话清理及真实WSS撤权；无TLS-ignore/SPKI绕过。 |

所有新建原生合成PG均正常停止、退出0。浏览器复验使用原隔离driver的browser模式，负载阶段为空；没有运行正常/过载/恢复长窗口。其SUT/client退出0、自有cgroup为空、tmpfs卸载。PG仅为新建合成数据/loopback/trust测试实例，没有连接现有或生产数据库、执行生产迁移、操作VM或真人恢复。

保留1项既有Starlette/httpx弃用提示；未作为失败或隐藏。前端新增10项消费者测试；45项包含此前既有41项及新增4个参数化场景。重复定向和浏览器测试不叠加为新的完整后端总数。

## 范围、保全与待审

修改限于两处产品修复及回归支持：auth.py、auth-client.ts、AuthWorkbench.tsx、前后端相应测试、browser-only helper开关及浏览器断言。精确清单见DELTA-MANIFEST.json/修复补丁包。受保护契约、0001–0008迁移、历史失败和原交付包不改写。浏览器本轮产生的新记录复制到本修复目录，原记录保留。

未重跑完整后端、Windows原回归、36项Mock浏览器或容量，不用旧540/容量点估计冒充本次修复源码验收。独审对原容量重算的一致性结论保留，旧容量SOURCE-MATCH不外推到本次新源码。

R02/R04 OPEN、R05 INCONCLUSIVE、S3/S4未整体批准，以及真实D0/U02/留存/部署门槛保持原状态。F01/F02仅为作者修复回执，是否关闭由精确补丁的独立复审决定。

没有stage、commit、push、merge、部署、发布、真实身份启用、费用调用或缓存清理。
