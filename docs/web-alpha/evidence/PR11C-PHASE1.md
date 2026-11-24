# PR-11c 第一阶段证据

负责人林珈诚。开发基线 `80d2204d4578d6673a1ce74d1f60097d97b372cb`，OpenAPI 0.3.0。第一阶段改动已在本地分支 `feature/web-pr11c-search` 提交（`06ad428`，未推送）。

- **VMware（已运行）**：当前代码的 targeted 与 full 均通过，full 为后端 1741、前端 596 passed，含 11c 后端 26、前端 29 项。运行编号、证据哈希、失败历史与已知偶发项见 [验证与复审报告](../pr11c/PHASE1-VERIFICATION.md)。
- **Astra 复审**：r0–r5 结论与未关闭项见同一报告。
- **宿主静态检查**：Windows，Python 3.10.4、Node 24.19.0。Python AST、两项 TypeScript noEmit、ESLint、PowerShell AST、空白、文档相对链接、迁移图与路由 ID 检查退出码均为 0；原始记录位于 `outputs/pr11c/` 下各轮 `*STATIC-CHECKS.txt`。

实现与 VMware 命令见 [交接](../pr11c/PHASE1-HANDOFF.md)。
