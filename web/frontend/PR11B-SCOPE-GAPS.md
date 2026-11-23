# PR-11b 运营后台（前端）范围与缺口

日期：2026-11-23。分支 `feature/web-pr11b-admin`，基线 `origin/main` = `80d2204`。契约为冻结的 OpenAPI 0.3.0，本 PR 不修改契约。

按 [08-IMPLEMENTATION](../../docs/web-alpha/architecture/08-IMPLEMENTATION.md)，11b 的后台 API 由 Codex 实现，界面由 Claude 实现。本 PR 只做界面，配合开发用的 Mock 后端（MSW），不接真实服务，也不调用任何真实模型或供应商。

## 需求

| 需求 | 本 PR 的界面落点 |
|---|---|
| AUTH-07 后台四页 | `/admin/users`、`/admin/invites`、`/admin/usage`、`/admin/runtime`（02-FRONTEND-AURORA §路由） |
| AUTH-07 管理操作 MFA 与审计 | 每个管理写操作都要填理由（1–500 字），并先做一次再认证（密码 + 验证码）；运行状态页列出审计记录 |
| AUTH-07 日志无正文/密码/Key/恢复码 | 界面不记录、不缓存密码、验证码、再认证凭证和邀请令牌；邀请令牌只在创建或重签的响应里显示一次 |
| AUTH-05 Admin 无默认正文、无冒充 | 后台只用 `/admin/*` 的无正文投影；没有“查看学习内容”“以该用户身份登录”之类入口 |
| FE-13 学生直接打开后台 URL | 界面先读 `/me` 的角色，学生不发出任何 `/admin/*` 请求；服务端同样以 `FORBIDDEN` 拒绝 |

## 页面

- **用户**：用户列表（邮箱、昵称、角色、状态、MFA、创建时间）。可停用或恢复账号（CAS `base_revision`），可查看某用户的额度桶，并追加额度（`/admin/quota-adjustments`）。
- **邀请**：邀请列表；创建邮箱绑定邀请或单次邀请；撤销、重新签发。一次性令牌只保存在组件内存里。
- **用量**：按月查看平台预算、已结算、预留、unknown、安全预留和价格版本。金额用整数微元字符串换算，不经过浮点数。风险占比对照 60%、80%、100% 阈值显示。
- **运行状态**：数据库、对象存储、worker 心跳、排队、unknown 任务、待删除和删除失败、ASR 流、最近备份；平台开关（注册、ASR、AI、活跃人数上限，CAS + 理由 + 再认证）；审计记录。

## 契约缺口（需 Codex / Astra 处理，本 PR 不改契约）

1. **管理写操作拿不到再认证凭证。** 所有 `/admin/*` 写操作都要求 `X-Reauth-Token`，但 `/auth/reauth` 的 `ReauthOperation` 只有账号自身用途（`logoutAll`、`changePassword` 等），没有任何管理员用途，也没有 `user`、`invitation`、`platform` 之类的目标。按 0.3.0 无法签发合法凭证。
   - 界面处理：写操作统一经过 `AdminReauthPort`。Mock 模式下由 Mock 后端在进程内签发开发用凭证（绑定操作、目标、幂等键和请求体，单次有效，300 秒）。真实 API 模式下该端口直接拒绝，界面提示“管理写操作暂不可用”，不发请求。
   - 建议：在 `ReauthOperation` 中补充 `adminUpdateUser`、`adminCreateInvitation`、`adminRevokeInvitation`、`adminReissueInvitation`、`adminAdjustQuota`、`adminUpdateSwitches` 六个用途及对应目标类型。
2. **邀请创建或重签的响应丢失。** 两者的 `x-replay-policy` 是 `non-secret-status`，但契约没有写同键重放时返回什么。Mock 按秘密签发的通用规则返回 `409 SECRET_ALREADY_ISSUED`；界面提示邀请可能已创建，请刷新列表后对它“重新签发”。
3. **能否停用自己或其他管理员。** 契约没有规定。界面不给自己的账号显示停用按钮；Mock 拒绝停用自己（`FORBIDDEN`）。其他管理员能否被停用由后端决定。
4. **审计记录只有 `actor_id`。** 界面用用户列表把它换成邮箱；不在列表里的显示为“已不在列表中的账号”。
5. **管理员会话时长。** Admin 空闲 15 分钟、绝对 8 小时由服务端执行（05-SECURITY §S2）。界面不另做倒计时，过期后统一走 `UNAUTHENTICATED` 退出。
6. **用量只有一个平台汇总。** `AdminUsage` 没有按用户或按功能拆分；按用户的明细只能通过 `/admin/users/{id}/quota` 看额度桶。
7. **邀请操作没有理由字段。** `InvitationCreate` 只有 `kind`、`email`，撤销和重签的请求体为空，和“管理操作记理由”的要求不一致。界面对邀请操作不收集理由；Mock 在审计里写固定说明（如“撤销邀请”）。
8. **`fixed_and_accrued_microyuan` 的口径。** 契约没有说明它是否已包含 `settled_microyuan`。界面按 11a README 的风险构成计算：风险合计 = 固定与已计提 + 已结算 + 预留 + unknown。若后端口径是“固定与已计提已含已结算”，风险会被高估，需要 Codex 确认。

## 其他说明

- 手机底部导航固定四项，管理员在手机上从“设置”导航末尾的“运营后台”进入；桌面导航对管理员多一个“运营”入口。
- 设置页“导入导出”和“删除与隐私”两个占位写着“计划于 PR-11b”，但它们不属于运营后台（导入导出是 08c，账号删除的后端在 04b）。本 PR 不改这两处文案，留给相应 PR 处理。
- Mock 增加了合成管理员账号 C（`admin@example.invalid`，已启用 MFA，没有学习数据）。开发面板“运营后台（PR-11b）”可以登录管理员、切换健康读数、切换管理员是否启用 MFA。合成再认证：任意非空密码，任意 6 位验证码，`000000` 表示验证码错误。
- 停用账号后，该账号在 Mock 中的所有请求立即返回 `UNAUTHENTICATED`，账号壳随之退出。

## 不在范围内

- 后台 API、权限、审计落库、独立限流（Codex）。
- 首个管理员创建、管理员 MFA 丢失恢复（运维工具，没有网页入口）。
- 搜索、通知、反馈（11c）。
