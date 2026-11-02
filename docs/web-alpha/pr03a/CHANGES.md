# PR-03a P2-01 最小整改变更清单

基线：`fa041e4c687be3d4b125f1d74024e7417ad19102`。共8文件。只改保护工具、追加定向测试和当前文档；不修改SQL、角色、RLS、恢复逻辑、历史迁移或契约。

- `docs/web-alpha/pr03a/ACCEPTANCE.md`
- `docs/web-alpha/pr03a/CHANGES.md`
- `docs/web-alpha/pr03a/remediation/checkout/P3-01-DEPLOYMENT-SUPPORT.md`
- `docs/web-alpha/pr03a/remediation/checkout/REPORT.md`
- `docs/web-alpha/pr03a/remediation/checkout/VALIDATION.json`
- `tools/pr03a/remediation/check_protected.py`
- `tools/pr03a/remediation/test_protected_checkout.py`
- `web/backend/README.md`

原18文件的第三轮清单保留在fa041e4提交；本轮保留原根VALIDATION.json及第三轮设计/报告/失败证据。实际新验证见remediation/checkout/VALIDATION.json；bootstrap-only与P3-01见当前验收和后端运维入口。
