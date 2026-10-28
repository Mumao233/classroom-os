# Web Alpha 0.1.0 · PR-00 工程基线

正式 Git 仓库：`J:\class-assistant\classroom-os`。

项目总目录：`J:\class-assistant`。

开发分支：`feature/web-alpha-0.1.0`。

本地版基线：`43fa23c141e3350e90d72ed7768e3b8de7d68337`（`main`、`v1.3.0`对应提交）。

用户于2026-10-28明确指定以上路径并授权执行PR-00工程准备和基线测试；该决定替代早先把D盘审计副本作为操作目录的临时安排。所有后续Web开发在本仓库进行。上级目录的原PRD资料保持原样，未移动或删除。

用户随后原则认可PR-00本地验收，授权复查后创建独立本地commit。AC-00-09明确为**“本地通过，远端CI待验证”**；远端验证不因本地提交而自动完成。收尾不包含PR-01开发或push/merge/发布。

## 文档入口

| 文档 | 用途 |
|---|---|
| [正式PRD v1.2原件归档](Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2.pdf) | 37页PDF，包含视觉概念页和最后三页阿里云ASR增补；仅规范副本文件名，字节不变 |
| [SHA-256](SHA256SUMS.txt) / [归档来源](PRD_PROVENANCE.md) | 原件路径、文件版本差异及页码映射 |
| [仓库基线](BASELINE.md) | 完整Git历史、分支、标签、旧版边界和验收证据口径 |
| [需求矩阵](REQUIREMENTS_MATRIX.md) | 每个需求ID的覆盖分类、后续PR与验收入口 |
| [冲突登记](CONFLICTS.md) | PRD正文/增补/代码差异，禁止隐式更改的行为 |
| [工程决策与TBD](DECISIONS.md) | 两条版本线、主要依赖、11项TBD的关闭证据 |
| [测试说明](TESTING.md) | 隔离依赖、完整检查、Windows CI与出网限制 |
| [后续Web测试门禁](WEB_TEST_GATES.md) | PostgreSQL/对象/Worker/授权/浏览器等新增验收义务；未冒称已执行 |
| [PR-00验收报告](PR00_ACCEPTANCE.md) | AC-00-01至AC-00-14实际结果、限制与下一步 |
| [早期完整分析归档](archive/2026-10-28-initial-analysis.md) | 本轮前的A—G分析和28个PR计划；仅规范行尾空白，内容作为历史记录保留 |

历史分析中的D盘路径、上一轮部分Python测试结果和“等待PR-00授权”只描述当时状态。当前目录、授权和实际测试结果以本目录的基线及验收报告为准。原始分析未被改写成新的测试结果。

## 当前边界

PR-00只新增文档、CI和检查工具；不实现Web业务，不改变Windows本地版、不迁移真实数据、不运行真实云模型。仓库变更保留为本地可审阅文件，不执行push、merge、tag或Release，不部署。PR-01实体/API/状态契约是下一工作包，并未因本轮授权自动开始。
