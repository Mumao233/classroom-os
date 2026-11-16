# PR-09b 开工前范围与缺口清单

日期：2026-11-15。工作树 `J:\class-assistant\worktrees\web-pr09b-courses`，分支 `feature/web-pr09b-courses`（无上游），基点 `main` = `81ab191b1f33de2371567eccd0ef9589d12483c2`。契约 `openapi.json` 0.2.0。写入范围仅 `web/frontend/`。

## 1. 后端实现与契约对照

后端 HTTP 入口 `web/backend/classroom_web/entrypoints/http.py` 只注册认证/会话路由和一个业务读：

| 接口 | 契约 | 后端 | 本 PR 处理 |
|---|---|---|---|
| `GET /courses/{course_id}` | 有 | **已实现**（`CourseReader`，含 `class_session_count`、`last_class_session_at`，支持 `revision` CAS 读） | 课程详情头部走同一 typed client；Mock 返回同 schema。真实联调需要 VMware 来宾内 PG + 后端，见第 4 节 |
| `GET /me/sessions`、`GET /me/devices`、`POST /me/sessions/{id}/revoke`、`POST /me/devices/{id}/revoke`、`/auth/*`（MFA、改密、logout-all） | 有 | **已实现**（PR-03b） | 生产入口已由 `AuthWorkbench` 使用；设置页在 Mock 壳内用同 schema 的 Mock，不重写 PR-03b 安全流程 |
| `GET/POST /semesters`、`GET/PATCH /semesters/{id}`、学期 deletion-preview/delete | 有 | 未实现 | 符合 schema 的有状态 Mock |
| `GET/POST /courses`、`PATCH /courses/{id}`、课程 deletion-preview/delete、`GET/POST /courses/{id}/materials` | 有 | 未实现 | Mock |
| `GET /class-sessions`、`GET/PATCH /class-sessions/{id}` | 有 | 未实现 | Mock（改归属 = `PATCH course_id + base_revision`） |
| `GET /review-documents`、`GET /questions`、`GET /tasks`、`GET /notifications` | 有 | 未实现 | Mock，只读，用于待办/动态/复习标签 |
| `GET/PATCH /me`、`GET/PATCH /me/preferences`、`GET /me/quota`、`GET /meta` | 有 | 未实现 | Mock |

## 2. 需求范围（09b）

- **Dashboard** UI-DASH-01、03～07、09 主责；UI-DASH-02、08 与 09a 共担（壳已交付，本 PR 只核对）。04/06/07 与 11c 共担：本 PR 做前端投影与 Mock 证据。
- **Courses** UI-COURSE-01～12（01/02/05/12 与 02a 共担后端约束；08/10 与 10b 共担；09 与 10a；11 与 08b）。UI-REVIEW-02 只做课程内筛选入口。
- **Settings**（矩阵第 158 行 + PRD p19 §5.5）09b 部分：个人资料、主题与阅读、AI 使用偏好（无 Key/URL/BYOK，CF-15）、个人配额只读、设备会话与可信设备（列表与撤销）、版本信息；安全/MFA 由 PR-03b 交付，本 PR 只做状态展示与入口说明；数据与缓存（08a）、导入导出、删除与隐私执行（11b）、反馈只放明确“尚未开放”状态，不做假功能。

## 3. 契约缺口与歧义（不改 openapi.json，必要时交 Astra）

1. **课程材料首次写入**：`CourseMaterialWrite.base_revision` 必填且 ≥1，但没有“创建课程材料”的语义。Mock 假设课程创建时服务端同时生成 revision 1 的空材料。需 Astra/Codex 确认。
2. **最近课堂排序**：`GET /class-sessions` 无 `sort`、`semester_id` 参数，排序未定义。前端只对已取回的一页按 `started_at`/`created_at` 排序，并在验收文档注明不保证跨页全局最近。
3. **课程卡片“最近课堂”**：`Course` 只有 `last_class_session_at`，没有最近课堂 ID/标题；卡片显示时间，不虚构标题。
4. **未懂/疑问按课程筛选**：`GET /questions` 只支持 `class_session_id`；课程概览按课程内最近若干课堂逐一查询并注明范围。
5. **计划课（UI-COURSE-12）**：契约无计划课实体，本 PR 不展示排课，只在课程页说明未提供课表。
6. **Notification 文案**：只有 `message_code`，无本地化字典；按 `kind` 显示中文类型，不臆造正文。
7. **学期删除 move_children** 的合法目标由后端预检返回的说法（02 文档 §5.5）在 `DeletionPreview` 中没有对应字段；UI 允许用户选同账号其他学期/课程作为目标，由服务端最终校验。

## 4. 验证与环境

- 根 `J:\class-assistant\AGENTS.md`（2026-11-12）：测试统一在 VMware 来宾执行，宿主只做编辑和静态检查。宿主运行 `typecheck`/`lint`/`build`（静态/构建）；`test:contract`、`test:unit`、`test:ui` 需要在 VMware 来宾执行，**需要用户在本机终端提供 VM 加密密码与来宾凭据**。
- 真实后端联调（`GET /courses/{id}` + 会话/设备）需要来宾内 PostgreSQL 与后端；若来宾未就绪，如实记为未执行。
- 08a/08b 未完成：离线、学习缓存、同步 outbox 只做契约/Mock 层与 UI 文案。
