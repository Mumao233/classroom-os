# PR-01 · 架构设计验收报告

日期：2026-10-28（北京时间）。正式仓库：`J:\class-assistant\classroom-os`。分支：`feature/web-alpha-0.1.0`。首次设计交付阶段开始及结束HEAD均为`f7731002d974ea29296090c366b68569fc69ba89`；本地业务基线`43fa23c141e3350e90d72ed7768e3b8de7d68337`。本报告保留该阶段验收口径；随后获批的本地提交收尾另见下文及收尾记录。

**结论：PR-01整体架构设计、版本化契约、跨Agent协作规范与Claude前端交接已完成，本地设计验收通过。2026-10-28用户原则批准架构并接受ADR已选结构和技术栈，授权独立本地commit收尾。** 11项TBD中的实验/产品参数继续开放，见[批准与收尾](PR01-CLOSEOUT.md)。没有开始PR-02/PR-09或其他业务实现。下述通过只针对设计/静态契约/合成规则，不能读作Web已运行或供应商/浏览器/数据库集成已通过。

## 1. 正式交付物

完整导航见[架构入口](README.md)。包括10份主体文档（01–10）、本验收、13项ADR、118项需求追踪、机器契约和验证证据。

| 类型 | 交付规模 / 位置 |
|---|---|
| 系统/前后端/安全/成本/部署 | [01](01-SYSTEM.md)–[08](08-IMPLEMENTATION.md)，包括系统图、模块图、ER、时序图与状态图 |
| 前端独立交接 | [09 Claude Frontend Handoff](09-CLAUDE-FRONTEND-HANDOFF.md)，独立Mock/模拟采集/AI、目录/路由/组件、PR09a可判定验收 |
| 数据模型 | [domain-model](contracts/domain-model.json)：60张逻辑表、688列、164条外键；明确按所属PR分步迁移，非60个服务，也未执行DDL |
| HTTP | [OpenAPI3.1](contracts/openapi.json)：118条路径、134个操作、149个schema |
| WS | [events.schema](contracts/events.schema.json)：21类命令/事件，音频二进制协议和持久/临时语义见04 |
| 合成契约 | [examples](contracts/examples.json)：34个正/反例；[五流状态守卫](contracts/state-machines.json)：16条允许/拒绝路径 |
| 实施依赖 | [28节点DAG](contracts/pr-dependencies.json)，区分硬依赖、联调依赖、收费门槛；11a前置 |
| 需求追溯 | [10逐项追踪](10-TRACEABILITY.md)及[机器表](contracts/requirements-traceability.json)：118个唯一需求，另列Settings与Agent补充 |
| 长期协作 | 根[AGENTS.md](../../../AGENTS.md)保留原文追加；根[CLAUDE.md](../../../CLAUDE.md)为薄入口，详细决策不重复维护 |
| 验证工具 | [Python静态检查](../../../tools/pr01/validate_architecture.py)、[Mermaid检查](../../../tools/pr01/check_diagrams.mjs)；不导入/运行业务代码 |

## 2. 逐条设计验收

| ID | 验收要求 | 结果与证据 |
|---|---|---|
| AC-01-01 | 核读规范、完整PRD、矩阵、技术审计/决策与源码 | 完成：AGENTS、PR-00文档、完整37页文字；p21/35/36/37原页渲染核读；源码依据分别列01/02/04，PDF hash复核 |
| AC-01-02 | 完整系统和模块边界 | 通过：01模块责任/依赖/进程/总图；明确本地独立、单体优先与规模约束 |
| AC-01-03 | 前端栈、路由、状态、目录、Aurora | 通过：02完整路由与状态所有权、ports、组件props、tokens/无障碍、Claude/Terra边界 |
| AC-01-04 | 后端、领域、持久任务与迁移 | 通过：03事务/worker/fence/unknown、RLS引导、DTO映射、扩展后收缩迁移与故障表 |
| AC-01-05 | PostgreSQL实体/归属/关系可实施 | 通过：60表字段规格；164 FK目标/类型/owner结构检查；ER/血缘约束明确。未冒称已执行SQL |
| AC-01-06 | HTTP/WS/错误/幂等/CAS契约 | 通过：OpenAPI、事件schema、599个本地ref可解析，操作ID无重复；34正反例通过 |
| AC-01-07 | 录音→云ASR→问答→课后总结全链路 | 通过：04完整时序/状态；原偏移/缺口、unknown、部分总结、首稿候选、raw输出保留 |
| AC-01-08 | 认证、多租户、隐私、删除 | 通过：05威胁/权限/会话/MFA、秘密回执例外、删除清单、可验证异故障域日志；敏感政策有门禁 |
| AC-01-09 | 离线、多设备、版本和恢复 | 通过：三池、DEK/nonce/AAD、在线解锁、CAS/typed冲突、晚ACK、防复活、SW兼容、包ID映射 |
| AC-01-10 | Token与资源成本及预算保护 | 通过：06双账本、原子hold、累计usage、跨月/unknown、20%预留、60/80/100阈值、PCM容量算术 |
| AC-01-11 | 未来客户端/部署/扩容/风险 | 通过：01/02 ports边界与原生认证扩展；06单VM拓扑/指标/扩容触发/回退；无提前原生业务 |
| AC-01-12 | 明确比较选型、冻结与实验区别 | 通过：07 ADR-01–13作明确推荐；F/E/P分类；11项TBD逐项关闭证据/责任/最晚门槛 |
| AC-01-13 | 28PR依赖、并行、实施顺序 | 通过：08及JSON；硬图与合并联调/付费图均无环/未知节点；09a只依01，11a先于收费 |
| AC-01-14 | Astra/Claude/Codex/Terra协作落库 | 通过：AGENTS/CLAUDE/07–09记录角色、Worktree/分支/交接/契约变更/联合验收；不依赖聊天 |
| AC-01-15 | Claude可凭仓库独立接手09a | 通过：09明确阅读/批准检查、未来命令、Mock模式/模拟ports/12场景、允许自决与需审边界；明确当前未实现 |
| AC-01-16 | 架构图、追踪、文档质量 | 通过：15 Mermaid图语法、Markdown围栏/本地链接/空白检查；118项需求全集一致 |
| AC-01-17 | PR-00/本地版/原文保护 | 通过：149旧文件SHA不变、7份源资料内容hash集合不变、归档PDF SHA不变；PR-00原文未改，AGENTS原HEAD全文仍为现文件完整前缀 |
| AC-01-18 | 仅授权范围、无自行推进 | 通过：仅AGENTS追加、新CLAUDE、architecture文档/工具；无Web业务目录、收费/开麦/部署/真实数据。首次设计阶段未commit，后续仅执行用户单独授权的PR-01本地commit；无push/merge/tag/release |

## 3. 本轮实际验证与环境

详单：[静态验收JSON](evidence/architecture-validation.json)、[图语法JSON](evidence/diagram-validation.json)、[独立复审](evidence/INDEPENDENT-REVIEW.md)。Python检查10组全通过，Mermaid15/15通过；结构样例34/34，状态守卫16/16。这些都是本轮真实运行，未借用历史pytest成绩。

环境：Windows PowerShell；Python3.12.14（Codex已安装运行时）；Node24.19.0；jsonschema4.26.0、openapi-spec-validator0.9.0、Mermaid11.17.2、jsdom26.1.0。用于阅读原件的pypdf6.10.0、pypdfium2 5.14.0。新检查依赖与npm/pip cache、临时脚本均在`work/pr01/`；原运行时位于C盘只读使用，没有迁移应用全局缓存或旧环境。

实际核心命令（工作目录为正式仓库；均退出0）：

```powershell
& 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools/pr01/validate_architecture.py --deps work/pr01/validation-deps
& 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools/pr01/check_diagrams.mjs
git diff --check
git status --short --branch
```

重建检查依赖时先将TEMP/TMP/PIP_CACHE_DIR/npm_config_cache指向仓库已创建的`work/pr01/`子目录；Python用`pip install --target work/pr01/validation-deps jsonschema==4.26.0 openapi-spec-validator==0.9.0`，Node用`npm install --prefix work/pr01/diagram-deps --ignore-scripts --no-audit --no-fund mermaid@11.17.2 jsdom@26.1.0`。不改根requirements或添加前端业务依赖。完整依赖锁保存在忽略的work目录，正式源码只有检查器与精选证据。

执行限制/修正如实记录：最初按一个不存在的运行时npm-cli路径调用失败（MODULE_NOT_FOUND），随后使用已安装npm把依赖装入work后成功；这是工具定位问题，不是产品测试失败。PDF提取器对字体映射发出警告，因此另外渲染p21和三页附录逐页核读，未重写原PDF。Mermaid检查是语法解析，未声称所有图在所有阅读器已逐像素视觉验收。

## 4. 独立审查与关闭的问题

前端专项设计与跨模块独立复审并行进行；复审明确区分其自己撰写部分和审查其他模块。实际修正包括：secret不能落通用receipt、独立删除日志可验证head/持久接受边界、plan换op防重复、同步低序号晚提交漏洞、typed冲突、未信任device、录音时间点标记、长课manifest上限、空课程材料、DTO/字段映射和RLS身份/调度引导。详见独立报告与最终契约。

复审结论为当前设计未见剩余阻断问题；不是不存在未知缺陷的证明。最后字段/FK/引用/保护检查由主流程执行，未以Agent口头声明代替工具结果。

## 5. 未执行与仍需验证的内容

- 没有真实PG建表/约束trigger/RLS/迁移/并发测试，没有对象存储、worker进程崩溃、浏览器录音/IndexedDB或E2E实现。机器模型不是已生效数据库。
- 没有接入云ASR/LLM、访问真实密钥/课堂、开麦、读取账号账单、发送邮件、部署或采购；不声明首字幕3秒/预算可覆盖/质量/隐私合规已经达标。
- 没有重新执行旧Python/九套Node业务回归，因为本轮未改任何旧业务、测试或依赖，149文件hash保持。PR-00的525主测试+171子测试、九套Node成绩仍属于其原时间；远端CI待验证状态不变。
- 11项TBD中的真实协议、成本/质量、安全恢复窗口、异常删除/备份政策、容量/真机、供应商条款等仍按07关闭。框架与结构已经设计完成，不把这些实验项混称“架构还没做”。

## 6. 首次设计交付工作区与当前停止点

首次设计交付时跟踪文件只有`AGENTS.md`追加；新增`CLAUDE.md`、`docs/web-alpha/architecture/`、`tools/pr01/`。其余中间文件和完整本地输出在忽略的`work/pr01/`、`outputs/pr01/`。该阶段没有创建commit或远端PR，HEAD保持PR-00。用户随后授权独立PR-01本地提交；文件清单、重新验证和提交范围见[收尾记录](PR01-CLOSEOUT.md)。

回退仅针对明确列出的PR-01新增文件及AGENTS追加段，保留原文、旧业务、PRD、PR-00和用户文件；无数据库/业务数据需要回滚。不要使用广泛递归删除或git clean。当前保留完整交付供审核，不执行回退。

**当前架构已原则获批，完成授权的PR-01本地提交后停止等待确认。架构批准不自动授权收费、部署或后续PR；下一实施工作包由用户明确指定。**
