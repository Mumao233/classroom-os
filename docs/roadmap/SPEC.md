# phases.json 格式规范

> 给所有改动 `docs/roadmap/phases.json` 的人和 agent（Codex、Claude Code）。
> 规则由 `python scripts/roadmap.py check` 强制执行，CI 不通过的改动不合并。
> 机器可读版本：`docs/roadmap/phases.schema.json`（JSON Schema，只覆盖单字段格式；排序、引用、环等跨字段规则以脚本为准）。

## 1. 怎么改：优先用命令，不手写 JSON

| 要做的事 | 命令 |
|---|---|
| 改状态 | `python scripts/roadmap.py set <id> <status> [<id> <status> ...]` |
| 补记过去的日期 | 加 `--date YYYY-MM-DD` |
| 设 / 清负责人 | `--owner <id> <名字>` / `--owner <id> ""` |
| 挂 / 摘 PR | `--pr <id> <url>` / `--unpr <id> <url>` |
| 写 / 清备注 | `--note <id> "文字"` / `--note <id> ""` |
| 查看进度 | `python scripts/roadmap.py next` |
| 校验 | `python scripts/roadmap.py check` |

`set` 会自动维护 `history`、保持文件格式，并重新生成 `ROADMAP.md`。**`phases.json` 和 `ROADMAP.md` 必须在同一个提交里。**

只有**增删阶段、改标题、改模块名、改依赖边**需要手改 JSON；改完运行 `python scripts/roadmap.py render`，再运行 `check`。

## 2. 顶层结构

```json
{
  "groups": { "00": "工程基线", "01": "架构契约" /* … 模块名，可选 */ },
  "phases": [ /* 阶段对象，按 id 升序 */ ],
  "edges": {
    "hard":        [ ["01", "02a"] ],
    "integration": [ ["03b", "05a"] ],
    "paid":        [ ["03b", "06a"] ]
  }
}
```

顶层只允许 `groups`、`phases`、`edges` 三个键，`groups` 可省略。

### groups（模块名）

- 键是阶段 id 的前两位数字（`"06"` 对应 06a / 06b / 06c），值是模块名，用在看板分区、模块视图和 ROADMAP.md 的「按模块」表。
- 每个键都必须有对应的阶段；按编号升序；值是非空单行字符串，不含英文双引号。
- 没写名字的模块只显示编号，不算错误。新增一个编号的模块时顺手补上名字。

## 3. 阶段对象

一行一个阶段，字段顺序随意，但不能出现下表以外的字段。

| 字段 | 必填 | 类型 | 规则 |
|---|---|---|---|
| `id` | 是 | string | 两位数字加可选的一个小写字母：`00`、`06c`。全局唯一，**定下后不改**（PR、历史和依赖都靠它） |
| `title` | 是 | string | 非空、单行、不含英文双引号 `"`；建议 20 个汉字以内（图上放得下） |
| `status` | 是 | string | `todo` / `doing` / `review` / `done` / `blocked` 之一（见第 4 节） |
| `owner` | 否 | string | 负责人，如 `Codex`、`Claude`、组员名。没有就**删掉字段**，不要写空字符串 |
| `prs` | 否 | string[] | 完整的 http(s) PR 链接，不重复；不要写 `#12` 这种简写。没有就删掉字段 |
| `note` | 否 | string | 阻塞原因、验收要点等。没有就删掉字段 |
| `history` | 否 | object[] | 状态变更记录，由 `set` 自动追加（见第 5 节） |

示例：

```json
{"id": "03a", "title": "邀请验证与资源归属", "status": "review", "history": [{"date": "2026-11-04", "status": "doing"}, {"date": "2026-11-07", "status": "review"}], "owner": "Codex", "prs": ["https://github.com/<org>/<repo>/pull/42"]}
```

## 4. 状态

| 状态 | 含义 | 谁来设 |
|---|---|---|
| `todo` | 未开始 | 默认 |
| `doing` | 有人在做 | 开工的人 / agent |
| `review` | PR 已提交、测试通过，等人验收 | 做完的人 / agent |
| `done` | 验收通过、已合并 | **只能由人设置**，agent 不要把阶段设为 `done` |
| `blocked` | 做不下去，必须同时用 `note` 写清原因 | 发现阻塞的人 / agent |

「可开工」不是状态，不要写进文件：`todo` 且全部 `hard` 前置都是 `done` 时，脚本和页面会自动算出来。

agent 的工作约定：
1. 开工前运行 `next`，只领「可开工」或明确分配给你的阶段；前置没完成就开工，`next` 会报「状态与依赖不一致」。
2. 开工：`set <id> doing --owner <id> <你的名字>`。
3. 提交 PR 且测试通过：`set <id> review --pr <id> <PR 链接>`，roadmap 的改动放进这个 PR。
4. 卡住：`set <id> blocked --note <id> "原因"`；解决后设回 `doing`，并用 `--note <id> ""` 清掉备注。

## 5. history

- 每条形如 `{"date": "YYYY-MM-DD", "status": "<状态>"}`，只有这两个键。
- 按日期从早到晚排列；**最后一条的 status 必须等于当前 `status`**。
- 只追加、不改写、不删除旧记录。改状态用 `set`，它会自动追加；要补记过去的日期用 `--date`。
- 从没改过状态的阶段没有 `history` 字段。

历史补录使用项目约定的时区（本次为 `America/Los_Angeles`），不得把盘点日期、文件修改时间或首次提交日期直接当作开工日期。只有 `doing` 记录作为页面和报告的开工依据；缺少该记录时保留未知。日常历史仍只追加；用户明确要求修正已证实错误的历史时，应保留修正前的 Git 版本及日期来源，记录修正理由后统一重生成报告。首次实际日期核对见 [HISTORY-EVIDENCE.md](HISTORY-EVIDENCE.md)。

## 6. 依赖边

每条边是 `[from, to]`，意思是 **to 依赖 from**（from 要先完成）。

| 类型 | 含义 | 影响 |
|---|---|---|
| `hard` | 硬依赖：不完成就不能开工 | 决定「可开工」、关键路径、瓶颈、波次 |
| `integration` | 完整联调依赖：可以先开工，但完整联调前必须完成 | 只做提示 |
| `paid` | 接入真实付费服务前必须完成（目前用于 06a） | 只做提示 |

规则：
- 两端必须是已存在的阶段 id；不能指向自己；同类型里不能重复。
- 所有边合在一起不能成环。
- 不要为了「保险」把间接依赖也写成直接边：A→B、B→C 已经存在时，不要再加 A→C。

## 7. 增删和拆分阶段

- **新增**：按 id 顺序插到 `phases` 里对应位置，状态写 `todo`，再在 `edges` 里补上前置和后续；如果是新编号，在 `groups` 里加模块名。同一模块的延伸用下一个字母（`06c` 之后用 `06d`）。
- **拆分**：保留原 id 作为其中一部分，新部分用新字母；把原来指向它和从它出发的边分配到对应部分。
- **删除**：先删掉所有引用它的边，再删阶段。已经有 PR 或历史的阶段原则上不删，改标题或合并备注。
- 改完运行 `render` 和 `check`，并在 PR 描述里说明调整依赖的理由。

## 8. 格式

- 标准布局：`groups` 一行一个模块，`phases` 一行一个阶段，`edges` 一行一条边，顺序固定为 groups → phases → edges，UTF-8，中文不转义，结尾有换行。`set` 每次都会按这个布局重写整个文件；`render` 不改 `phases.json`，所以手改时请自己保持这个布局，方便看 diff。
- `phases` 按 id 升序排列（`00 < 01 < 02a < 02b < … < 12c`）。
- 不要手改 `ROADMAP.md`，它会被 `render` 覆盖，CI 也会检查它是否过期。
