# PR-00 工程准备与基线验收报告

日期：2026-10-28（北京时间）。范围：本轮用户授权的仓库准备、需求归档、CI/检查工具和完整旧版回归；不包含PR-01业务开发。

**结论：PR-00本地工程准备及基线验收完成。13项通过；AC-00-09为“本地通过，远端CI待验证”，不属于完全通过。** 用户已原则认可本地验收，并单独授权复查后创建PR-00本地commit；未授权push、merge或发布。本文及新增文件纳入该独立本地提交，实际SHA与提交后清单由Git记录及收尾回执给出。PR-01尚未开始。

## 1. 正式目录、原件和Git状态

| 项目 | 核实结果 |
|---|---|
| 项目总目录 | `J:\class-assistant`，保留原有资料 |
| 正式Git仓库 | **`J:\class-assistant\classroom-os`**；所有后续Web开发使用此目录 |
| 目标目录冲突检查 | 克隆前不存在；没有覆盖既有文件或Git历史 |
| 获取方式 | 普通完整clone，无depth/filter/sparse选项 |
| origin | `https://github.com/Mumao233/classroom-assistant.git` |
| 当前分支 | `feature/web-alpha-0.1.0` |
| 验收测试时HEAD / 固定基线main、origin/main、v1.3.0对应commit | `43fa23c141e3350e90d72ed7768e3b8de7d68337`；收尾commit只推进开发分支 |
| 历史完整性 | 非浅仓库；克隆基线8个可达提交；`git fsck --full`通过；收尾提交在其上单独增加1个commit |
| 已有标签 | v1.0.0、v1.1.0、v1.1.1、v1.2.0、v1.2.1、v1.3.0全部保留 |
| 原跟踪文件 | 149个，逐文件SHA-256全部不变 |
| 原PRD文件夹 | 7个文件，逐文件SHA-256全部不变，未移动/删除 |
| 本地版 | `version.py`仍为1.3.0；源码、启动器、旧tests、requirements和存储格式无改动 |

归档：[PRD v1.2](Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2.pdf)，原件和副本SHA-256均为：

```text
DD7D970D54FB443969CE6BB210B0B18053BFECF972CF419EFA57EDB51B2FF5B8
```

文件名v1.2与内页v1.1的差异、37个物理页、插入预览页及最后3页A1–A10见[归档来源](PRD_PROVENANCE.md)。只复制原始字节，不重写PDF。原目录为`J:\class-assistant\Classroom_OS_Web_Alpha_0.1.0_PRD_v1.1_Package`。

## 2. AC-00-01至AC-00-14逐条结果

标准来源：[原分析G2](archive/2026-10-28-initial-analysis.md)。用户最后确认的J盘正式路径替代历史D盘审计副本路径；原验收要求未删减。

| 编号 | 原验收事项 | 本轮结果及证据 | 状态 |
|---|---|---|---|
| AC-00-01 | 实际仓库、HEAD、分支、工作树 | 正式路径、完整clone、43fa23c基线及新分支见上表；没有用户已有工作被覆盖；第6节区分初验工作树与收尾提交，[初验完整性记录](evidence/final-integrity.json)保留原时间点 | 通过 |
| AC-00-02 | 37页PRD与附录归档 | 原件/副本hash一致；v1.2/v1.1差异、物理页偏移及A1–A10完整索引见[来源](PRD_PROVENANCE.md)、[校验值](SHA256SUMS.txt) | 通过 |
| AC-00-03 | 优先级与冲突登记 | [25项冲突](CONFLICTS.md)明确Streaming/北京首选、20%按默认月额、未知收费段不自动重发、首稿显式采用、A10拆分依赖和概念图边界；不修改旧实现 | 通过 |
| AC-00-04 | 完整需求追踪 | [矩阵](REQUIREMENTS_MATRIX.md)包含108个正文编号和10个ASR附录编号，共118个唯一需求；逐项分类、未来PR、验收入口，范围外项另列 | 通过 |
| AC-00-05 | 两产品版本ADR | [ADR-00](DECISIONS.md)列本地1.3.0、Web显示/包/tag/API/schema/设计版本映射及批准状态；方案记录不等于发布或重大TBD批准 | 通过 |
| AC-00-06 | 测试清单与隔离环境 | [测试说明](TESTING.md)与[依赖记录](evidence/pip-freeze.txt)列完整清单、解释器、约束、J盘temp/cache、外网限制；旧tests和requirements hash不变 | 通过 |
| AC-00-07 | 原完整Python回归 | 最终原pytest：**525 passed + 171 subtests passed**，74.68秒；0失败/错误/跳过；包括此前缺依赖的faster-whisper相关用例；见[原始输出](evidence/final-pytest.txt)、[JUnit](evidence/final-pytest-junit.xml) | 通过 |
| AC-00-08 | 九套Node与静态检查 | 九套原Node全部通过；48个Python AST（44原文件+4工具）、6个应用JS语法、3个PowerShell脚本解析通过；新增文件纳入diff后空白检查通过；见[最终结果](evidence/final-results.json)、[完整性记录](evidence/final-integrity.json) | 通过 |
| AC-00-09 | Windows旧版CI | [工作流](../../.github/workflows/pr00-windows.yml)定义Windows完整旧回归及合成DPAPI，PowerShell只解析；同一runner本地Windows已通过；YAML触发器/权限/步骤检查通过。**GitHub Actions未远端运行**，托管环境仍待后续获准推送后验证，不标记为完全通过 | 本地通过，远端CI待验证 |
| AC-00-10 | 新Web测试门禁设计 | [后续门禁](WEB_TEST_GATES.md)列真实PG事务、对象存储、worker、鉴权、浏览器/故障/费用测试及依赖；没有Web业务/E2E通过的虚假成绩 | 通过 |
| AC-00-11 | 安全、费用、部署开关 | CI无收费密钥或模型/麦克风/部署/发布步骤；合成fixtures、loopback限制、真实进程拒绝和HF离线自检通过；不读取真实data/config/key；进程防护不冒充OS沙箱，见第5节 | 通过 |
| AC-00-12 | 无业务改动证明 | 基线149个原文件SHA-256完全一致；变更仅新增文档、CI、检查工具与证据；原7份资料也不变；见[基线清单](evidence/baseline-manifest.json)与[最终比对](evidence/final-integrity.json) | 通过 |
| AC-00-13 | TBD与后续依赖 | [11项TBD](DECISIONS.md)逐项列所需关闭证据、责任角色、最晚节点，均保留开放状态；11a费用保护先于06/07收费，05a加密POC先于持久恢复 | 通过 |
| AC-00-14 | 回退演练及交接 | 纯HEAD导出149文件，不含PR-00新增项；原pytest **525+171全部通过**（70.59秒）；见[回退记录](evidence/rollback-baseline-result.md)。未实际删除正式文件或revert；下一PR-01范围见第7节 | 通过 |

## 3. 实际测试与运行环境

最终检查开始于2026-10-28 14:56:58、结束于14:58:19（UTC+08:00），runner退出码0。此轮成绩替代历史审计中的57项部分通过、1项依赖错误；历史记录保持原样。

| 检查 | 结果 |
|---|---|
| Python / Node / PowerShell | 3.10.4 / 24.19.0 / 5.1.26100.9711 |
| 关键依赖 | pytest 9.2.0；FastAPI 0.162.0；httpx 0.28.1；Uvicorn 0.54.0；faster-whisper 1.2.1 |
| 原pytest主用例/子测试 | 525 / 171；JUnit总数696为两者之和，不能代替525个实际testcase节点 |
| 原Node套件 | audio、ai-settings、input-limits、study-ui、study-cache、review-render、review-ui、review-mount、portable-reader：9/9通过 |
| 额外工程自检 | Python/Node防护、自检在os.name被mock时的兼容、JUnit主/子计数及失败/skip拒绝，均通过 |
| 静态检查 | Python AST 48个；应用JS 6个；PowerShell脚本3个（仅解析）；完整新增diff检查通过 |
| 隔离依赖 | `work/pr00/venv`；新增constraints固定本次解析版本，原requirements未改 |

全部命令、退出码和耗时见[final-results.json](evidence/final-results.json)，简表见[final-summary.md](evidence/final-summary.md)，完整解析依赖见[pip-freeze.txt](evidence/pip-freeze.txt)。不能声称本次依赖版本与历史发布环境逐包相同。

逐步日志汇编见[final-step-logs.txt](evidence/final-step-logs.txt)；各原始日志仍保存在`work/pr00/runner-acceptance/`。历史分析及失败日志的归档文本仅去除行尾空白，以通过新增文件diff检查；历史结论、原工作目录日志及PDF原始字节不变。

实际运行命令（工作目录为正式仓库）：

```powershell
& 'J:\class-assistant\classroom-os\work\pr00\venv\Scripts\python.exe' tools/pr00/run_checks.py --python 'J:\class-assistant\classroom-os\work\pr00\venv\Scripts\python.exe' --node 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --output-dir work/pr00/runner-acceptance
```

复验时将`--output-dir`改为`work/pr00/`下未存在的新目录，例如`work/pr00/runner-review-01`，以免覆盖证据。新环境创建顺序与安装命令见[TESTING.md](TESTING.md)。C盘路径仅指已安装的Node运行时；本轮可配置依赖、缓存和中间文件在J盘。

### 失败与修正留痕

第一次`runner-validation`有523个主用例通过、2项DPAPI失败、171个子测试通过。新测试防护动态选择Path类，受到原用例故意mock `os.name='posix'`影响。只修正新防护为初始化时固定宿主Path类，并增加对应自检；未改原测试或业务。保留[首次失败输出](evidence/first-run-pytest-failure.txt)。

`runner-validation-final`全量通过后，独立审查发现JUnit的696总数混含子测试，进一步改为分别检查525个主用例和171个子测试，并加入“524主用例+1000子测试仍失败”等负例。同时清除继承的选测参数和外部插件自动加载。最后`runner-acceptance`重新完整运行，得到上述525+171通过结果。三个运行目录和失败日志均保留，未以删skip、改断言或过滤用例达成通过。

## 4. CI配置和远端限制

工作流只响应`pull_request`和`workflow_dispatch`；`contents: read`；checkout `persist-credentials: false`；先配置工作区temp/cache再安装依赖；Windows、Python3.10和固定Node24.19.0；最终上传明确列出的合成日志/JSON/JUnit/摘要，保留7天。

**AC-00-09尚无GitHub Actions运行链接。** 本轮未push，不能验证GitHub托管环境、actions下载或其Python3.10补丁版本；本地3.10.4成功不保证该远端组合必然通过。后续获准推送/创建PR后，运行新增工作流并补充run URL、commit、实际版本和结果，才关闭远端验证部分。本轮不为补齐此证据而擅自推送。

## 5. 安全与数据边界

- 依赖安装只下载开发依赖；测试期限制外网/DNS和实际子进程，HF离线，无模型权重下载。现有mock调用和127.0.0.1随机端口合成HTTP仍按原测试执行；未部署或开放公网。
- 原Windows DPAPI用合成临时密钥测试；没有读取真实API Key、真实课堂资料或用户Codex凭据；未启动真实麦克风、Codex或收费ASR/LLM。
- Python/Node保护为测试进程防误调用措施，不是OS沙箱，也不证明恶意代码无法绕过。它不由本地业务应用加载。
- 归档证据只保留选定文本/JSON/JUnit；venv、缓存、临时媒体、运行目录、真实配置不纳入Git变更。历史原件PDF只在用户明确授权的仓库本地归档，本轮没有上传。
- 输入6元/百万Token、输出4.5元/百万Token作为用户确认的ASR价格口径保留；未进行任何收费调用或账户权限/用量验证。11项TBD保持开放。

## 6. 工作区、授权收尾与回退

新增范围如下，初验逐文件清单见[初验完整性记录](evidence/final-integrity.json)，收尾复查见[提交前审查](evidence/precommit-review.json)：

| 新增路径 | 用途 |
|---|---|
| `AGENTS.md` | 正式仓库路径、两版本线及本轮边界 |
| `.github/workflows/pr00-windows.yml` | Windows完整旧版回归工作流 |
| `tools/pr00/` | runner、防误调用与计数自检、依赖解析约束 |
| `docs/web-alpha/` | PRD副本/hash、矩阵、冲突/ADR/TBD、测试设计、验收与精选证据 |

初验阶段新增文件通过`git add -N`登记intent-to-add，内容尚未暂存；该时间点的原始状态和全部测试证据保持。用户随后授权PR-00收尾：复查所有新增文件，明确AC-00-09状态，再显式暂存已审查清单并创建一个独立本地commit。提交包含的清单以`git show --name-status <PR-00提交SHA>`及收尾回执为准。原149文件不修改/删除；不执行push、merge、新tag、GitHub PR或Release。

收尾审查覆盖文件范围、敏感凭据模式、合成日志来源、原件hash、原代码完整性及测试证据留存。`work/`中的虚拟环境、缓存、原始运行证据继续本地保留，均不暂存。扫描结果不打印疑似密钥值；自动模式扫描与人工来源复核共同使用，不宣称自动扫描能识别任意秘密。本次仅调整收尾文档和归档审查记录，复用既有完整回归成绩，不将文档检查冒充一次新pytest运行。

回退演练使用`git archive`导出固定基线到新目录`work/pr00/rollback-baseline/`，没有删除当前新增文件或实际revert。快照包含原149文件，pytest独立完整通过525主用例+171子测试，70.59秒。[回退记录](evidence/rollback-baseline-result.md)包含命令、归档SHA及限制。

后续撤回只针对这里列明的PR-00增量，不使用宽泛`git clean`或清空上级目录，不动原PRD、main、历史tag或本地版安装。独立本地commit创建后，可在另行授权下按其实际SHA精确revert；本轮不执行revert。测试日志和隔离环境继续保留，不删除唯一证据。

## 7. 交接与下一工作包

本轮完成的是工程准备及本地回归保护。下一工作包为PR-01：审查实体、API/WebSocket事件、五工作流状态、资源归属、幂等、unknown、删除墓碑、版本与迁移契约，并提供合成正反例；**本轮没有启动该工作包**。

后续仍需：AC-00-09托管CI补验；11项TBD按节点关闭；Web实际业务、PG/对象/Worker/身份/浏览器测试；经过专项授权的真实ASR质量、长时和成本验证。PR-11a费用保护必须在06/07收费接入前，PR-05a密钥POC必须在持久缓存/恢复前。

原A—G分析与分阶段PR计划保存在[初始分析归档](archive/2026-10-28-initial-analysis.md)，其目录和测试状态以本报告更新；当前依赖、回退和开放问题以[DECISIONS.md](DECISIONS.md)为准。等待用户确认下一工作包，不把PR-00验收视为业务开发、真实资料、收费、部署或发布授权。
