# PR-00 仓库与本地版保护基线

## Git获取与路径

执行前`J:\class-assistant\classroom-os`不存在，因此没有现有文件/Git历史冲突。使用普通`git clone --origin origin https://github.com/Mumao233/classroom-assistant.git`创建正式仓库，没有使用`--depth`、`--filter`、稀疏检出或清理已有目录。

| 字段 | 本轮值 |
|---|---|
| 正式仓库 | `J:\class-assistant\classroom-os` |
| origin | `https://github.com/Mumao233/classroom-assistant.git` |
| 基线HEAD | `43fa23c141e3350e90d72ed7768e3b8de7d68337` |
| 本地main / origin/main | 同上 |
| v1.3.0解引用后的commit | 同上；该标签原为annotated tag，tag对象hash与commit不同是正常现象 |
| 远端默认分支 | main |
| 开发分支 | `feature/web-alpha-0.1.0`，从基线本地创建 |
| `--is-shallow-repository` | false |
| `rev-list --all --count` | 8个提交（克隆时可达历史） |
| 历史标签 | v1.0.0、v1.1.0、v1.1.1、v1.2.0、v1.2.1、v1.3.0；原对象保留，未创建/修改标签 |
| 原跟踪文件数 | 149 |
| `git fsck --full` | 退出0，无对象完整性问题 |

以上是PR-00初验时的Git基线。用户随后原则认可本地验收，并授权复查后创建一个独立PR-00本地commit；该提交只推进`feature/web-alpha-0.1.0`，不移动main、origin/main或历史tag。实际SHA及最终新增文件清单由Git记录和收尾回执给出。未推送、未合并、未创建或改写tag/Release。远端Release的历史发布事实不代表本轮进行了发布。

## 保护边界

- `version.py`继续是本地`1.3.0`；原业务代码、static、tests、requirements及setup/start/stop/VBS启动器保持。
- 不把全局Classroom/current、JSON存储或local_only改成多用户服务。Web身份/数据库/任务/缓存等留给后续独立工作包。
- 不读取或迁移真实`data/`、`config.json`、DPAPI/API Key、模型权重或Codex登录。新仓库没有复制旧正式安装的数据与环境。
- 所有本轮依赖/临时文件在`work/pr00/`；日志在`outputs/pr00/`或`work/pr00/`，两者都被既有gitignore排除。没有为PR-00修改gitignore或旧requirements。
- [原文件清单](evidence/baseline-manifest.json)记录149个既有工作树文件hash和原PRD文件夹7个文件hash；最终比较确认均保持不变。新文档/检查工具不加入原文件清单冒充原有资产。

## 验证证据的时间层次

1. `docs/VALIDATION.md`是原开发过程记录，历史记载525项Python、171子测试及九套Node通过；其中“尚未发布”“file://受工具限制”是当时状态。
2. v1.3.0 GitHub Release后补用户人工双击离线页通过，且现有tag/Release已存在；不改写旧验证记录来消除时间差。
3. 上轮需求审计因依赖不全只复跑57项通过、1项依赖错误，不是全量成绩。
4. 本轮PR-00在新隔离环境复跑完整回归；具体结果、时间、命令、合成边界和CI未远端运行限制见[验收报告](PR00_ACCEPTANCE.md)。不以历史通过替代当前结果。

## 回退方式

本轮只有新增文档、CI与工具，没有业务数据/schema变更。未提交阶段的回退范围是明确列出的PR-00新增项；保留原PRD目录、Git历史、main、v1.3.0和唯一日志，不执行宽泛`git clean`或递归清空项目。

本轮使用`git archive`导出固定HEAD到新的`work/pr00/rollback-baseline/`，验证只有原149个跟踪文件、没有PR-00新增工具/文档时，原完整pytest仍通过。此为基线快照回退演练，没有删除正式仓库新增文件，也没有执行`git revert`或修改main/tag。后续若PR-00形成commit，可按实际commit精确revert；不回退业务数据，因为本轮没有迁移。
