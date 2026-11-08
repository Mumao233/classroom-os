#!/usr/bin/env python3
"""Classroom OS 阶段进度工具。

数据只有一份：docs/roadmap/phases.json（阶段、状态、负责人、PR、状态历史，以及三类依赖边）。
这个脚本负责改数据、校验，并生成 docs/roadmap/ROADMAP.md（带颜色的 Mermaid 图，GitHub 直接显示）。

用法：
  python scripts/roadmap.py                      # 等同 render：重新生成 ROADMAP.md
  python scripts/roadmap.py next                 # 终端里看进度、可开工、关键路径、瓶颈
  python scripts/roadmap.py set 03a doing        # 改状态（自动记一条带日期的历史），并重新生成
  python scripts/roadmap.py set 02a done 03a review --date 2026-11-06
  python scripts/roadmap.py set --owner 03a 林珈诚 --pr 03a https://github.com/o/r/pull/42
  python scripts/roadmap.py set --note 05b "等租约方案"   # 空字符串 "" 表示清除
  python scripts/roadmap.py set --unpr 03a https://github.com/o/r/pull/42
  python scripts/roadmap.py check                # CI 用：数据有误或 ROADMAP.md 过期时退出码 1
  python scripts/roadmap.py site ../personal-web/public/roadmap [--readonly]
                                                 # 把可视化页面和最新数据复制到网站目录

状态：todo（未开始） doing（进行中） review（待验收） done（完成） blocked（受阻）
“可开工”不是手动状态：todo 且全部硬依赖都 done 时自动算出来。
只依赖 Python 标准库（3.8+）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "roadmap" / "phases.json"
OUT = ROOT / "docs" / "roadmap" / "ROADMAP.md"
SITE_SRC = ROOT / "docs" / "roadmap" / "site"

STATUSES = ["todo", "doing", "review", "done", "blocked"]
LABEL = {"todo": "未开始", "doing": "进行中", "review": "待验收", "done": "完成", "blocked": "受阻", "ready": "可开工"}
ICON = {"todo": "⚪", "ready": "🟡", "doing": "🔵", "review": "🟣", "done": "🟢", "blocked": "🔴"}
# fill / stroke / text — 深色文字配浅色底，GitHub 亮色和暗色主题下都看得清
CLASSDEF = {
    "done": "fill:#bbf7d0,stroke:#15803d,color:#14532d",
    "review": "fill:#e9d5ff,stroke:#7e22ce,color:#3b0764",
    "doing": "fill:#bfdbfe,stroke:#1d4ed8,color:#1e3a8a,stroke-width:2px",
    "ready": "fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:3px",
    "blocked": "fill:#fecaca,stroke:#b91c1c,color:#7f1d1d,stroke-width:2px",
    "todo": "fill:#f5f5f4,stroke:#a8a29e,color:#57534e",
}
ANSI = {"todo": "90", "ready": "33", "doing": "34", "review": "35", "done": "32", "blocked": "31", "bold": "1"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^\d{2}[a-z]?$")
URL_RE = re.compile(r"^https?://\S+$")
PHASE_KEYS = {"id", "title", "status", "owner", "prs", "note", "history"}
EDGE_KINDS = ("hard", "integration", "paid")


def valid_date(value: str) -> bool:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


# ---------- 数据 ----------

def load() -> dict:
    with DATA.open(encoding="utf-8") as f:
        return json.load(f)


def save(data: dict) -> None:
    """一行一个阶段 / 一条边，方便看 git diff。"""
    lines = ["{"]
    if data.get("groups"):
        lines.append('  "groups": {')
        lines.append(",\n".join(f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in data["groups"].items()))
        lines.append("  },")
    lines.append('  "phases": [')
    lines.append(",\n".join("    " + json.dumps(p, ensure_ascii=False) for p in data["phases"]))
    lines += ["  ],", '  "edges": {']
    blocks = []
    for kind, edges in data["edges"].items():
        body = ",\n".join("      " + json.dumps(e) for e in edges)
        blocks.append(f'    "{kind}": [\n{body}\n    ]')
    lines.append(",\n".join(blocks))
    lines += ["  }", "}"]
    DATA.write_text("\n".join(lines) + "\n", encoding="utf-8")


def node(pid: str) -> str:
    return "P" + pid.upper()


def pr_label(url: str) -> str:
    m = re.search(r"/pull/(\d+)", url)
    return f"#{m.group(1)}" if m else "PR"


class Graph:
    def __init__(self, data: dict):
        self.phases = data.get("phases", [])
        ok = [p for p in self.phases if isinstance(p, dict) and isinstance(p.get("id"), str)]
        self.by_id = {p["id"]: p for p in ok}
        self.order = {p["id"]: i for i, p in enumerate(ok)}
        self.edges = {k: [tuple(e) for e in v] for k, v in data.get("edges", {}).items()}
        self.top_extra = set(data) - {"groups", "phases", "edges"}
        self.groups = data.get("groups", {})
        self.deps = defaultdict(lambda: defaultdict(list))  # deps[kind][target] -> [sources]
        self.succ = defaultdict(lambda: defaultdict(list))  # succ[kind][source] -> [targets]
        for kind, edges in self.edges.items():
            for a, b in edges:
                self.deps[kind][b].append(a)
                self.succ[kind][a].append(b)

    def status(self, pid: str) -> str:
        return self.by_id[pid].get("status", "todo")

    def missing(self, pid: str, kind: str = "hard") -> list:
        return [d for d in self.deps[kind][pid] if self.status(d) != "done"]

    def shown(self, pid: str) -> str:
        s = self.status(pid)
        return "ready" if s == "todo" and not self.missing(pid) else s

    # ---- 分析（只看硬依赖） ----
    def topo(self) -> list:
        indeg = {p["id"]: len(self.deps["hard"][p["id"]]) for p in self.phases}
        queue = [p["id"] for p in self.phases if indeg[p["id"]] == 0]
        out = []
        while queue:
            u = queue.pop(0)
            out.append(u)
            for v in self.succ["hard"][u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)
        return out

    def blocked_downstream(self) -> dict:
        """每个阶段的未完成下游（传递）数量。"""
        res = {}
        for p in self.phases:
            seen, stack = set(), list(self.succ["hard"][p["id"]])
            while stack:
                v = stack.pop()
                if v not in seen:
                    seen.add(v)
                    stack.extend(self.succ["hard"][v])
            res[p["id"]] = sum(1 for v in seen if self.status(v) != "done")
        return res

    def waves(self) -> dict:
        """剩余波次：done=0；未完成阶段 = 1 + 未完成硬依赖的最大波次。同一波可以并行。"""
        w = {}
        for u in self.topo():
            if self.status(u) == "done":
                w[u] = 0
            else:
                w[u] = 1 + max([w[d] for d in self.deps["hard"][u]] + [0])
        return w

    def critical_path(self) -> list:
        """未完成部分里最长的硬依赖链。"""
        dist, nxt = {}, {}
        for u in reversed(self.topo()):
            best, bn = 0, None
            for v in self.succ["hard"][u]:
                if dist[v] > best:
                    best, bn = dist[v], v
            dist[u] = (0 if self.status(u) == "done" else 1) + best
            nxt[u] = bn
        cands = [p["id"] for p in self.phases if self.status(p["id"]) != "done"]
        if not cands:
            return []
        start = max(cands, key=lambda x: (dist[x], -self.order[x]))
        path, u = [], start
        while u is not None:
            if self.status(u) != "done":
                path.append(u)
            u = nxt[u]
        return path

    @staticmethod
    def started(p: dict) -> str:
        for h in p.get("history", []):
            if h["status"] == "doing":
                return h["date"]
        return ""

    @staticmethod
    def finished(p: dict) -> str:
        if p.get("status") != "done":
            return ""
        for h in reversed(p.get("history", [])):
            if h["status"] == "done":
                return h["date"]
        return ""

    # ---- 校验（规则见 docs/roadmap/SPEC.md） ----
    def errors(self) -> list:
        errs = []
        if self.top_extra:
            errs.append(f"顶层只允许 groups、phases 和 edges，多了：{', '.join(sorted(self.top_extra))}")
        if not isinstance(self.groups, dict):
            errs.append("groups 必须是对象，例如 {\"00\": \"工程基线\"}")
        else:
            prefixes = {p["id"][:2] for p in self.phases if isinstance(p, dict) and isinstance(p.get("id"), str)}
            for k, v in self.groups.items():
                if not re.match(r"^\d{2}$", k):
                    errs.append(f"groups 的键 {k!r} 必须是两位数字")
                elif k not in prefixes:
                    errs.append(f"groups 里的 {k} 没有对应的阶段")
                if not isinstance(v, str) or not v.strip() or '"' in v or "\n" in v:
                    errs.append(f"groups[{k!r}] 必须是非空单行字符串，且不能含英文双引号")
            keys = list(self.groups)
            if keys != sorted(keys):
                errs.append("groups 必须按编号升序排列")
        seen = set()
        for i, p in enumerate(self.phases):
            if not isinstance(p, dict):
                errs.append(f"phases[{i}] 必须是对象")
                continue
            pid = p.get("id")
            where = pid or f"phases[{i}]"
            for k in ("id", "title", "status"):
                if k not in p:
                    errs.append(f"{where} 缺少必填字段 {k}")
            extra = set(p) - PHASE_KEYS
            if extra:
                errs.append(f"{where} 有未定义的字段：{', '.join(sorted(extra))}（允许：{', '.join(sorted(PHASE_KEYS))}）")
            if not isinstance(pid, str) or not ID_RE.match(pid):
                errs.append(f"{where} 的 id 格式不对：需要两位数字加可选的一个小写字母，如 03 或 06c")
            elif pid in seen:
                errs.append(f"阶段 id 重复：{pid}")
            seen.add(pid)
            t = p.get("title")
            if not isinstance(t, str) or not t.strip() or '"' in t or "\n" in t:
                errs.append(f"{where} 的 title 必须是非空单行字符串，且不能含英文双引号")
            if p.get("status") not in STATUSES:
                errs.append(f"{where} 的状态 {p.get('status')!r} 无效，可选：{', '.join(STATUSES)}")
            for k in ("owner", "note"):
                if k in p and (not isinstance(p[k], str) or not p[k].strip()):
                    errs.append(f"{where} 的 {k} 必须是非空字符串（不需要就删掉这个字段）")
            if p.get("status") == "blocked" and not (isinstance(p.get("note"), str) and p["note"].strip()):
                errs.append(f"{where} 受阻时必须用 note 写清原因")
            if "prs" in p:
                prs = p["prs"]
                if not (isinstance(prs, list) and prs and all(isinstance(x, str) and URL_RE.match(x) for x in prs)):
                    errs.append(f"{where} 的 prs 必须是非空的 http(s) 链接列表")
                elif len(set(prs)) != len(prs):
                    errs.append(f"{where} 的 prs 有重复链接")
            if "history" in p:
                hs = p["history"]
                ok = isinstance(hs, list) and hs and all(
                    isinstance(h, dict) and set(h) == {"date", "status"} and isinstance(h["date"], str)
                    and valid_date(h["date"]) and h["status"] in STATUSES for h in hs)
                if not ok:
                    errs.append(f"{where} 的 history 必须是非空列表，每条形如 {{\"date\": \"YYYY-MM-DD\", \"status\": \"doing\"}}")
                else:
                    dates = [h["date"] for h in hs]
                    if dates != sorted(dates):
                        errs.append(f"{where} 的 history 日期必须从早到晚排列")
                    if hs[-1]["status"] != p.get("status"):
                        errs.append(f"{where} 的 history 最后一条是 {hs[-1]['status']}，和当前状态 {p.get('status')} 不一致（请用 roadmap.py set 改状态）")
        ids = [p.get("id") for p in self.phases if isinstance(p, dict) and isinstance(p.get("id"), str) and ID_RE.match(p.get("id"))]
        if ids != sorted(ids):
            errs.append("phases 必须按 id 升序排列（新阶段插到对应位置）")
        extra = set(self.edges) - set(EDGE_KINDS)
        if extra:
            errs.append(f"edges 里有未定义的类型：{', '.join(sorted(extra))}（允许：{', '.join(EDGE_KINDS)}）")
        for kind, edges in self.edges.items():
            dup = set()
            for e in edges:
                if len(e) != 2:
                    errs.append(f"{kind} 里的边 {list(e)} 必须是 [from, to] 两个 id")
                    continue
                a, b = e
                for x in (a, b):
                    if x not in self.by_id:
                        errs.append(f"{kind} 边 {a} → {b} 引用了不存在的阶段 {x}")
                if a == b:
                    errs.append(f"{kind} 边 {a} → {b} 指向自己")
                if (a, b) in dup:
                    errs.append(f"{kind} 边 {a} → {b} 重复")
                dup.add((a, b))
        if not errs and len(self.topo()) != len(self.phases):
            errs.append("硬依赖出现环")
        if not errs:
            cyc = self.cycle()
            if cyc:
                errs.append("依赖出现环：" + " → ".join(cyc))
        return errs

    def cycle(self):
        g = defaultdict(list)
        for edges in self.edges.values():
            for a, b in edges:
                g[a].append(b)
        state = {}

        def dfs(u, path):
            state[u] = 1
            for v in g[u]:
                if state.get(v) == 1:
                    return path[path.index(v):] + [v]
                if not state.get(v):
                    r = dfs(v, path + [v])
                    if r:
                        return r
            state[u] = 2
            return None

        for p in self.phases:
            if not state.get(p["id"]):
                r = dfs(p["id"], [p["id"]])
                if r:
                    return r
        return None

    def warnings(self) -> list:
        out = []
        for p in self.phases:
            s = self.status(p["id"])
            miss = self.missing(p["id"])
            if s in ("doing", "review", "done") and miss:
                out.append(f"{p['id']} 已是「{LABEL[s]}」，但硬依赖 {', '.join(miss)} 还没完成")
        return out


# ---------- 生成 ----------

def mermaid(g: Graph) -> str:
    lines = ["flowchart TD"]
    for p in g.phases:
        lines.append(f'    {node(p["id"])}["{p["id"]} {p["title"]}"]')
    lines.append("")
    styles = defaultdict(list)
    i = 0
    for a, b in g.edges.get("hard", []):
        lines.append(f"    {node(a)} --> {node(b)}")
        styles["stroke:#16a34a,stroke-width:2px" if g.status(a) == "done" else "stroke:#a8a29e,stroke-width:1.2px"].append(i)
        i += 1
    for a, b in g.edges.get("integration", []):
        lines.append(f"    {node(a)} -. 完整联调 .-> {node(b)}")
        styles["stroke:#16a34a,stroke-width:1.5px" if g.status(a) == "done" else "stroke:#78716c,stroke-width:1.2px"].append(i)
        i += 1
    for a, b in g.edges.get("paid", []):
        lines.append(f"    {node(a)} ==>|真实付费接入| {node(b)}")
        styles["stroke:#16a34a,stroke-width:3px" if g.status(a) == "done" else "stroke:#ea580c,stroke-width:3px"].append(i)
        i += 1
    lines.append("")
    for name, css in CLASSDEF.items():
        lines.append(f"    classDef {name} {css}")
    groups = defaultdict(list)
    for p in g.phases:
        groups[g.shown(p["id"])].append(node(p["id"]))
    for name in CLASSDEF:
        if groups[name]:
            lines.append(f"    class {','.join(groups[name])} {name}")
    for css, idx in styles.items():
        lines.append(f"    linkStyle {','.join(map(str, idx))} {css}")
    return "\n".join(lines)


def counts(g: Graph) -> dict:
    c = defaultdict(int)
    for p in g.phases:
        c[g.shown(p["id"])] += 1
    return c


def bar(done: int, total: int, width: int = 20) -> str:
    n = round(width * done / total) if total else 0
    return "█" * n + "░" * (width - n)


def extra_gates(g: Graph, pid: str) -> str:
    bits = []
    m = g.missing(pid, "integration")
    if m:
        bits.append("完整联调还差 " + ", ".join(m))
    m = g.missing(pid, "paid")
    if m:
        bits.append("真实付费接入还差 " + ", ".join(m))
    return "；".join(bits)


def cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render(g: Graph) -> str:
    total = len(g.phases)
    c = counts(g)
    done = c["done"]
    title = lambda pid: f"{pid} {g.by_id[pid]['title']}"
    parts = [
        "<!-- 由 scripts/roadmap.py 自动生成，不要手改；改 docs/roadmap/phases.json 或运行 `python scripts/roadmap.py set ...` -->",
        "",
        "# Classroom OS 阶段进度",
        "",
        f"**{done} / {total} 完成**　`{bar(done, total)}`　{round(100 * done / total) if total else 0}%",
        "",
        "　".join(f"{ICON[k]} {LABEL[k]} {c[k]}" for k in ["done", "review", "doing", "ready", "todo", "blocked"]),
        "",
        "图例：节点颜色＝状态（🟡 黄色粗框＝前置都完成、可以开工）；实线＝硬依赖，虚线＝完整联调，橙色粗线＝真实付费接入前置；**连线变绿＝这条前置已满足**。",
        "",
        "```mermaid",
        mermaid(g),
        "```",
        "",
    ]
    mods = []
    for p in g.phases:
        k = p["id"][:2]
        if not mods or mods[-1][0] != k:
            mods.append((k, []))
        mods[-1][1].append(p["id"])
    parts += ["## 按模块", "", "| 模块 | 进度 | 阶段 |", "|---|---|---|"]
    for k, ids in mods:
        dn = sum(1 for x in ids if g.status(x) == "done")
        name = g.groups.get(k, "") if isinstance(g.groups, dict) else ""
        parts.append(f"| {k} {cell(name)} | {dn}/{len(ids)} | " + " ".join(f"{ICON[g.shown(x)]} {x}" for x in ids) + " |")
    parts += ["", "## 现在可以开工", ""]
    ready = [p for p in g.phases if g.shown(p["id"]) == "ready"]
    if ready:
        for p in ready:
            extra = extra_gates(g, p["id"])
            parts.append(f"- **{p['id']} {p['title']}**" + (f"（{extra}）" if extra else ""))
    else:
        parts.append("- 暂无")
    parts.append("")

    cp = g.critical_path()
    if cp:
        parts += ["## 关键路径", "", f"剩余最长的硬依赖链有 **{len(cp)}** 个阶段，决定最快什么时候能全部做完：", "",
                  " → ".join(title(x) for x in cp), ""]
    bd = g.blocked_downstream()
    top = sorted([p["id"] for p in g.phases if g.status(p["id"]) != "done" and bd[p["id"]]],
                 key=lambda x: (-bd[x], g.order[x]))[:5]
    if top:
        parts += ["## 瓶颈", "", "未完成阶段里，卡住下游最多的五个：", ""]
        parts += [f"- {title(x)}：卡住 {bd[x]} 个下游（{ICON[g.shown(x)]} {LABEL[g.shown(x)]}）" for x in top]
        parts.append("")
    w = g.waves()
    maxw = max(w.values()) if w else 0
    if maxw:
        parts += ["## 剩余并行波次", "", "同一波里的阶段互不依赖，可以并行；前一波完成后下一波才能全部开工。", ""]
        for k in range(1, maxw + 1):
            parts.append(f"- 第 {k} 波：" + "、".join(p["id"] for p in g.phases if w[p["id"]] == k))
        parts.append("")

    warns = g.warnings()
    if warns:
        parts += ["## ⚠️ 状态与依赖不一致", ""] + [f"- {x}" for x in warns] + [""]

    parts += ["## 全部阶段", "", "| 阶段 | 状态 | 负责人 | PR | 还差的硬依赖 | 其他前置 | 开始 | 完成 | 备注 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for p in g.phases:
        s = g.shown(p["id"])
        prs = " ".join(f"[{pr_label(u)}]({u})" for u in p.get("prs", [])) or "—"
        parts.append(
            f"| {p['id']} {cell(p['title'])} | {ICON[s]} {LABEL[s]} | {cell(p.get('owner', '')) or '—'} | {prs} | "
            f"{', '.join(g.missing(p['id'])) or '—'} | {extra_gates(g, p['id']) or '—'} | "
            f"{Graph.started(p) or ('开始日期未记录' if p.get('status') == 'done' else '—')} | {Graph.finished(p) or '—'} | {cell(p.get('note', ''))} |"
        )
    parts.append("")
    return "\n".join(parts)


# ---------- 终端输出 ----------

def color(s: str, key: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return s
    return f"\033[{ANSI[key]}m{s}\033[0m"


def print_next(g: Graph) -> None:
    total = len(g.phases)
    c = counts(g)
    print(f"进度 {c['done']}/{total}  {color(bar(c['done'], total, 28), 'done')}")
    print("  ".join(color(f"{LABEL[k]} {c[k]}", k) for k in ["done", "review", "doing", "ready", "todo", "blocked"]))
    print()
    for key, title in [("doing", "进行中"), ("review", "待验收"), ("blocked", "受阻"), ("ready", "可开工")]:
        rows = [p for p in g.phases if g.shown(p["id"]) == key]
        if not rows:
            continue
        print(color(f"■ {title}", key))
        for p in rows:
            bits = [extra_gates(g, p["id"]), p.get("owner") and "@" + p["owner"], p.get("note")]
            tail = "".join(f"  · {x}" for x in bits if x)
            print(f"    {p['id']:<4} {p['title']}{tail}")
    cp = g.critical_path()
    if cp:
        print()
        print(color(f"■ 关键路径（{len(cp)} 个阶段）", "bold"))
        print("    " + " → ".join(cp))
    bd = g.blocked_downstream()
    top = sorted([p["id"] for p in g.phases if g.status(p["id"]) != "done" and bd[p["id"]]],
                 key=lambda x: (-bd[x], g.order[x]))[:5]
    if top:
        print(color("■ 瓶颈", "bold"))
        print("    " + "  ".join(f"{x}（卡住 {bd[x]}）" for x in top))
    warns = g.warnings()
    if warns:
        print()
        print(color("■ 状态与依赖不一致", "blocked"))
        for x in warns:
            print("    " + x)


# ---------- 网站 ----------

EMBED_RE = re.compile(r'(<script id="default-data" type="application/json">)(.*?)(</script>)', re.S)


def deploy_site(target: Path, readonly: bool) -> int:
    src_page = SITE_SRC / "index.html"
    if not src_page.exists():
        print(f"找不到页面源文件 {src_page.relative_to(ROOT)}", file=sys.stderr)
        return 1
    target = target.resolve()
    if target == SITE_SRC.resolve():
        print("目标目录不能是页面源目录本身", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)
    for item in SITE_SRC.iterdir():
        if item.name.lower() == "readme.md":
            continue
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    raw = DATA.read_text(encoding="utf-8")
    payload = raw.strip().replace("</", "<\\/")  # 防止数据里的 </script> 提前结束标签
    html = (target / "index.html").read_text(encoding="utf-8")
    if not EMBED_RE.search(html):
        print("页面里找不到内置数据块 <script id=\"default-data\">", file=sys.stderr)
        return 1
    html = EMBED_RE.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), html, count=1)
    if readonly:
        html = html.replace("editable: true", "editable: false", 1)
    (target / "index.html").write_text(html, encoding="utf-8")
    shutil.copy2(DATA, target / "phases.json")
    print(f"已导出到 {target}" + ("（只读）" if readonly else ""))
    print("  index.html   页面（内置了一份当前数据，读不到 phases.json 时用）")
    print("  phases.json  页面优先读取的数据")
    print("  vendor/      渲染库本地副本")
    return 0


# ---------- CLI ----------

def write_out(g: Graph) -> None:
    OUT.write_text(render(g), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Classroom OS 阶段进度工具")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("render", help="重新生成 ROADMAP.md")
    sub.add_parser("next", help="终端查看进度")
    sub.add_parser("check", help="校验数据并确认 ROADMAP.md 是最新的（CI 用）")
    sp = sub.add_parser("set", help="改状态 / 负责人 / PR / 备注")
    sp.add_argument("pairs", nargs="*", help="<id> <status> [<id> <status> ...]")
    sp.add_argument("--note", nargs=2, action="append", metavar=("ID", "TEXT"), help="备注；空字符串清除")
    sp.add_argument("--owner", nargs=2, action="append", metavar=("ID", "NAME"), help="负责人；空字符串清除")
    sp.add_argument("--pr", nargs=2, action="append", metavar=("ID", "URL"), help="挂一个 PR 链接")
    sp.add_argument("--unpr", nargs=2, action="append", metavar=("ID", "URL"), help="去掉一个 PR 链接")
    sp.add_argument("--date", help="状态变更记在哪天，默认今天（YYYY-MM-DD）")
    ss = sub.add_parser("site", help="把可视化页面和最新 phases.json 复制到网站目录")
    ss.add_argument("target", help="网站里存放页面的目录，例如 ../personal-web/public/roadmap")
    ss.add_argument("--readonly", action="store_true", help="访客只能看、不能改")
    args = ap.parse_args(argv)
    cmd = args.cmd or "render"

    data = load()
    g = Graph(data)
    errs = g.errors()
    if errs:
        for e in errs:
            print("错误：" + e, file=sys.stderr)
        return 1

    if cmd == "set":
        date = args.date or dt.date.today().isoformat()
        if not valid_date(date):
            print("--date 需要真实有效的 YYYY-MM-DD 日期", file=sys.stderr)
            return 2
        if len(args.pairs) % 2:
            print("状态需要成对的 <id> <status>", file=sys.stderr)
            return 2

        def phase(pid):
            pid = pid.lower()
            if pid not in g.by_id:
                print(f"没有阶段 {pid}；可选：{', '.join(g.by_id)}", file=sys.stderr)
                sys.exit(2)
            return g.by_id[pid]

        changes = [(phase(pid), st) for pid, st in zip(args.pairs[::2], args.pairs[1::2])]
        for _, st in changes:
            if st not in STATUSES:
                print(f"状态 {st!r} 无效；可选：{', '.join(STATUSES)}", file=sys.stderr)
                return 2
        for p, st in changes:
            if p.get("status", "todo") != st:
                p["status"] = st
                p.setdefault("history", []).append({"date": date, "status": st})
        for field, items in (("note", args.note), ("owner", args.owner)):
            for pid, text in items or []:
                p = phase(pid)
                if text:
                    p[field] = text
                else:
                    p.pop(field, None)
        for pid, url in args.pr or []:
            p = phase(pid)
            if url not in p.setdefault("prs", []):
                p["prs"].append(url)
        for pid, url in args.unpr or []:
            p = phase(pid)
            if url in p.get("prs", []):
                p["prs"].remove(url)
            if not p.get("prs"):
                p.pop("prs", None)
        if not (changes or args.note or args.owner or args.pr or args.unpr):
            print("没有要改的内容", file=sys.stderr)
            return 2
        g = Graph(data)
        errs = g.errors()
        if errs:
            for e in errs:
                print("错误：" + e, file=sys.stderr)
            return 1
        save(data)
        write_out(g)
        print_next(g)
        return 0

    if cmd == "render":
        write_out(g)
        print(f"已生成 {OUT.relative_to(ROOT)}")
        return 0

    if cmd == "next":
        print_next(g)
        return 0

    if cmd == "site":
        return deploy_site(Path(args.target), args.readonly)

    if cmd == "check":
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != render(g):
            print("ROADMAP.md 不是最新的，请运行：python scripts/roadmap.py render", file=sys.stderr)
            return 1
        for w in g.warnings():
            print("警告：" + w)
        print("roadmap OK")
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # 例如 `roadmap.py next | head`
        sys.exit(0)
