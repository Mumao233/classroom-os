# 阶段进度（roadmap）

- `phases.json`：唯一的数据源。模块名（`groups`），28 个阶段，每个阶段的状态、负责人（`owner`）、PR 链接（`prs`）、备注（`note`）和状态历史（`history`，每次改状态自动记日期），加上三类依赖边（`hard` 硬依赖、`integration` 完整联调、`paid` 真实付费接入前置）。
- `ROADMAP.md`：由脚本生成的彩色进度图和表格，GitHub 上直接显示。**不要手改。**
- `SPEC.md`：phases.json 的格式规范，改数据前先读；`phases.schema.json` 是对应的 JSON Schema。
- `site/`：可交互的可视化页面（`index.html` + 渲染库本地副本），用 `roadmap.py site` 部署到网站。
- `../../scripts/roadmap.py`：改状态、校验、生成、部署。只用 Python 标准库。

## 状态

| 状态 | 含义 | 颜色 |
|---|---|---|
| `todo` | 未开始 | 灰 |
| `doing` | 进行中 | 蓝 |
| `review` | 代码已提交，等人验收 | 紫 |
| `done` | 验收通过 | 绿 |
| `blocked` | 受阻（用 `--note` 写原因） | 红 |
| *可开工* | 自动算出：`todo` 且所有硬依赖都 `done` | 黄色粗框 |

连线的源头阶段 `done` 以后，这条线变绿，表示这一条前置已满足。

## 常用命令

```bash
python scripts/roadmap.py next                      # 看进度、可开工、关键路径、瓶颈、不一致
python scripts/roadmap.py set 03a doing             # 开始一个阶段（自动记今天的日期）
python scripts/roadmap.py set 03a review --pr 03a https://github.com/<org>/<repo>/pull/42
python scripts/roadmap.py set 03a done 03b doing    # 一次改多个
python scripts/roadmap.py set 02a done --date 2026-11-03   # 补记过去的日期
python scripts/roadmap.py set --owner 03b Codex     # 只改负责人；"" 表示清除
python scripts/roadmap.py set 05b blocked --note 05b "等对象存储租约方案定稿"
python scripts/roadmap.py set --unpr 03a https://github.com/<org>/<repo>/pull/42
python scripts/roadmap.py check                     # CI：数据有误或 ROADMAP.md 过期则失败
```

`set` 会同时更新 `phases.json` 和 `ROADMAP.md`，两个文件一起提交。ROADMAP.md 里除了彩色依赖图，还有可开工清单、关键路径、瓶颈 Top 5、剩余并行波次和全部阶段的表格。

## 给 Claude Code / Codex 的约定

可以把下面这段放进仓库的 `AGENTS.md` / `CLAUDE.md`：

> 改 roadmap 前先读 `docs/roadmap/SPEC.md`，改完必须通过 `python scripts/roadmap.py check`。
> 开始一个阶段前运行 `python scripts/roadmap.py next`，只领「可开工」或已分配给你的阶段。
> 开工时 `set <id> doing --owner <id> <你的名字>`；PR 提交、测试通过后 `set <id> review --pr <id> <PR 链接>`，并把 `phases.json` 和 `ROADMAP.md` 的改动放进同一个 PR。
> 不要自己把阶段设为 `done`，`done` 由人验收后设置。遇到阻塞用 `set <id> blocked --note <id> "原因"`。
> 不要手改 `ROADMAP.md`；要调整阶段或依赖，改 `phases.json` 后运行 `python scripts/roadmap.py render`。

## 可视化页面

`site/index.html` 用同一份数据：

- 两个标签页：「依赖图」看关系，「看板」按模块（00–12）分区看每个阶段的状态，可按 全部 / 可开工 / 等前置 / 进行中 / 受阻 / 已完成 筛选；网址加 `#board` 直接打开看板。两边共用选中状态和详情面板。
- 点节点：右侧显示详情（状态、负责人、PR、备注、每条前置是否完成、做完能解锁什么、时间线），图上蓝色是它依赖的阶段，橙色是依赖它的阶段；「整条链」显示全部上下游。
- Shift + 点第二个节点：紫色显示两者之间的所有依赖路径。
- 视图切换：状态 / 瓶颈热力图 / 并行波次 / 模块 / 负责人；可叠加关键路径、淡化已完成，分别开关三类线；搜索定位（按 `/` 聚焦）；悬停提示。
- 页面打开时读取同目录的 `phases.json`；读不到（比如直接双击打开文件）就用页面里内置的那份。
- 在页面上的改动只存在当前浏览器，头部会提示「本地有改动，未同步」；用「导出」拿到 `set` 命令或整份 phases.json 同步回仓库。仓库数据更新后，旧的本地改动不会自动套用，可以选择恢复或丢弃。

## 部署到网站

### GitHub Pages（本仓库自动发布）

仓库的 `.github/workflows/roadmap.yml` 会在 PR 中运行格式、报告和工具测试；相关文件合并到 `main` 后，再生成并发布静态站点。首次配置时，在 GitHub 仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。随后运行一次 **Actions → Roadmap / GitHub Pages → Run workflow**（选择 `main`），或合并一次 roadmap 的改动。

发布过程运行 `python scripts/roadmap.py site _site --readonly`，将以下文件一起上传到 Pages，保留一份受版本管理的数据源：

```text
_site/
  index.html
  phases.json        来自 docs/roadmap/phases.json
  vendor/
```

页面打开或刷新时自动读取同目录的 `phases.json`，请求使用 `cache: 'no-store'`。适配 `https://<用户>.github.io/<仓库>/` 这样的项目子路径，不用手动导入、不用跨仓库复制。发布版只读，不会套用浏览器中的本地状态改动。数据请求失败或超时会持续提示「当前显示内置快照」，这时不能据此认定进度已更新。

日常更新仍按 [SPEC.md](SPEC.md) 使用 `set`，将 `phases.json` 和自动生成的 `ROADMAP.md` 放进同一 PR。**合并后，等待本次 Pages 部署成功，再刷新网页**；仅修改本地文件、仅提交 PR、或部署失败都不会更新公开页面。`done` 仍由人验收后设置，合并 PR 不会自动把阶段改成 `done`。2026-11-07 已核对实际进度并获用户确认：6 项 `done`、03b `blocked`、06a `doing`，其余 20 项 `todo`；范围及证据见[本次盘点记录](PROGRESS-RECONCILIATION.md)。

Pages 的地址以 Actions 部署任务的输出为准。本工作流接管该仓库的 Pages 站点根目录；已有其他 Pages 内容时，应先确认是否需要合并站点。项目的 Web 应用、业务 API 与数据库不由这个工作流部署。

本地预览（在仓库根目录执行）：

```bash
python scripts/roadmap.py site work/roadmap-preview --readonly
python -m http.server 8768 --bind 127.0.0.1 --directory work/roadmap-preview
```

然后打开 `http://127.0.0.1:8768/`。`docs/roadmap/site/` 是页面模板，数据在其上一级；直接双击模板会回退到内置快照，不能验证在线读取。

### 个人网站（手动导出）

```bash
# 在 Classroom OS 仓库根目录运行；目标是网站仓库里会被原样发布的静态目录
python scripts/roadmap.py site ../personal-web/public/roadmap            # 可以在页面上改（只影响访客自己的浏览器）
python scripts/roadmap.py site ../personal-web/public/roadmap --readonly # 访客只能看
```

然后在网站仓库提交 `roadmap/` 目录，页面地址就是 `https://<你的域名>/roadmap/`。

- 静态目录因框架而异：Next.js / Vite / Astro 是 `public/`，Hugo 是 `static/`，纯 HTML 站点就是仓库根目录。
- 进度更新后重新运行同一条命令，再提交网站仓库。
- 如果 Classroom OS 仓库是公开的，也可以不复制数据：把 `index.html` 顶部 `ROADMAP_CONFIG.dataUrl` 改成 `https://raw.githubusercontent.com/<org>/<repo>/main/docs/roadmap/phases.json`，页面每次打开都读最新数据。私有仓库不行。
- 也可以在网址后加 `?data=<链接>` 临时指定数据源，加 `?readonly` 临时切到只读。
- 页面默认带 `<meta name="robots" content="noindex">`，不会被搜索引擎收录；但知道链接的人都能看到完整路线图，公开前确认里面没有不想公开的信息。
- `vendor/mermaid.min.js` 是渲染库（Mermaid 11，MIT 许可，约 3.5 MB）的本地副本；缺了它会自动从 jsdelivr 加载。
