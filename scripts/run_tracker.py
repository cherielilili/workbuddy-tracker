#!/usr/bin/env python3
"""WorkBuddy / AI Agent 赛道 tracker — daily run.

Pipeline:
  1. Load sources.yaml + data/sources_state.json (last-seen content hashes per URL)
  2. Build a Claude CLI prompt that lists URLs + last-seen summaries
  3. Invoke `claude -p` — Claude uses WebFetch to pull each URL, diffs vs last-seen,
     outputs a JSON envelope:
       {
         "report_md": "<full markdown report text>",
         "signals": [{"title": "...", "vendor": "...", "severity": "major|minor",
                      "summary": "...", "source": "..."}],
         "new_state": {"<source_id>": {"last_seen_summary": "...", "fetched_at": "..."}}
       }
  4. Persist markdown + HTML + new state
  5. signals[severity=major] → discord_send("tracking", ...)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources.yaml"
STATE = ROOT / "data" / "sources_state.json"
REPORTS = ROOT / "reports"
INDEX_HTML = ROOT / "index.html"
TEMPLATE = ROOT / "templates" / "report.html"

CLAUDE_BIN = "/Users/cherie/.local/bin/claude"
REPORT_URL = "https://cherielilili.github.io/workbuddy-tracker/"
DISCORD_PUSH_PATH = "/Users/cherie/projects/Antigravity/ag-worker"


def load_sources() -> list[dict]:
    with SOURCES.open() as f:
        data = yaml.safe_load(f)
    return data["sources"]


def load_state() -> dict:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text())


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def build_prompt(sources: list[dict], state: dict, today: str) -> str:
    lines = [
        "你是 AI Agent 赛道分析师。任务：扫描以下源 → 提炼过去 24h 新动态 → 输出 JSON。",
        "",
        f"今日日期：{today}",
        "",
        "## 任务流程",
        "",
        "1. 对每个源用 WebFetch 抓取页面内容",
        "2. 对比 last_seen_summary（上次访问时记录的关键内容摘要）",
        "3. 提取**新增/变化**的关键内容（如：新版本/新功能/价格变化/官方公告/重要横评）",
        "4. 按厂商分组生成中文 markdown 日报",
        "5. 识别**重大信号**（severity=major）：新产品发布、重大版本更新、价格策略变化、官方公告、季度横评长文；",
        "   Stanford HAI 类重大信号：新课程/研讨班发布（如 MS&E 435 此类有从业者阵容）、年度 AI Index 报告、政策/监管简报、重要公开讲座",
        "6. 普通增量（教程/小版本/用户实测）= severity=minor，写日报但不推 Discord",
        "7. **抓不到内容的源不要进 new_state，也不要进 report_md**",
        "8. 更新 new_state（每个源的最新摘要）",
        "",
        "## 输出格式（严格 JSON，用 ```json 包裹）",
        "",
        "```json",
        "{",
        '  "report_md": "# AI Agent 赛道日报 YYYY-MM-DD\\n\\n## 摘要\\n...\\n\\n## 腾讯 WorkBuddy\\n...",',
        '  "signals": [',
        '    {"title": "WorkBuddy 接入飞书", "vendor": "Tencent", "severity": "major", "summary": "今日官网发布 v2.x...", "source": "https://..."}',
        '  ],',
        '  "new_state": {',
        '    "workbuddy_official": {"last_seen_summary": "页面当前主推 v2.x，强调...", "fetched_at": "2026-05-13T04:35:00Z"}',
        '  }',
        "}",
        "```",
        "",
        "## 源列表",
        "",
    ]
    for s in sources:
        sid = s["id"]
        last = state.get(sid, {}).get("last_seen_summary", "(首次抓取，无 baseline)")
        lines.append(f"### {sid} [{s['priority']}] - {s['vendor']}")
        lines.append(f"URL: {s['url']}")
        lines.append(f"备注: {s['note']}")
        lines.append(f"上次摘要: {last}")
        lines.append("")

    lines.extend([
        "## 写作要求",
        "",
        "- markdown 报告：按厂商分组（## 腾讯 WorkBuddy / ## 字节 Coze / ## Stanford HAI / ...），每组下用 bullet 列新增条目",
        "- 顶部加一个 '## 摘要' 节，2-3 句概括今日赛道动态。**摘要里不要提及抓不到的源**（不要说 XX 因 SPA 渲染失败或类似话）。全无更新时写'今日赛道无新动态'即可。",
        "- **重大信号** 节单独列在摘要之后，每条带 [vendor] 标签",
        "- 引用必须带 URL",
        "- **抓不到内容的源（404、SPA 渲染失败、超时、需登录、被反爬）完全省略，不要出现在报告里**",
        "- 抓到但无新动态的源也省略",
        "- 报告只列实际有可读新动态的厂商，没有就没有",
        "- 全文中文、专业、不口语化",
        "- **JSON 必须可被 json.loads 解析**：",
        "  - 字符串里的换行用 \\n",
        "  - 字符串里**禁止使用双引号 \"**；若引用页面标题或专有名词，用中文书名号《》或单引号 ' 替代",
        "  - last_seen_summary 限制在 100 字以内，纯描述，不要引号",
    ])

    return "\n".join(lines)


def run_claude(prompt: str) -> str:
    env = os.environ.copy()
    # Keep OAuth token, clear other CLAUDE* vars
    keep = {"CLAUDE_CODE_OAUTH_TOKEN"}
    for k in list(env.keys()):
        if k.startswith("CLAUDE") and k not in keep:
            env.pop(k, None)

    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--output-format", "text",
         "--allowedTools", "WebFetch"],
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        print(f"[run_tracker] claude failed rc={result.returncode}", file=sys.stderr)
        print(result.stderr[:2000], file=sys.stderr)
        sys.exit(1)
    return result.stdout


def extract_json(text: str) -> dict:
    """Find first ```json ... ``` block; fallback to first { ... } block."""
    start = text.find("```json")
    if start >= 0:
        start = text.find("{", start)
        # find matching closing brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i+1])
    # fallback
    first_brace = text.find("{")
    if first_brace >= 0:
        depth = 0
        for i in range(first_brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[first_brace:i+1])
    raise ValueError("no JSON envelope found in claude output")


def render_html(report_md: str, today: str, signals: list[dict]) -> str:
    tmpl = TEMPLATE.read_text()
    # very simple md → html (we'll inject via <pre> + JS markdown or pre-render)
    # For now use markdown-it via python if available, else just <pre>
    try:
        import markdown as md_lib
        body_html = md_lib.markdown(report_md, extensions=["tables", "fenced_code"])
    except ImportError:
        body_html = "<pre>" + report_md.replace("<", "&lt;") + "</pre>"

    major_count = sum(1 for s in signals if s.get("severity") == "major")
    minor_count = sum(1 for s in signals if s.get("severity") == "minor")

    signal_pills = ""
    for s in signals:
        sev = s.get("severity", "minor")
        cls = "pill-major" if sev == "major" else "pill-minor"
        signal_pills += (
            f'<div class="signal {cls}">'
            f'<span class="vendor">{s.get("vendor","")}</span>'
            f'<span class="title">{s.get("title","")}</span>'
            f'<div class="summary">{s.get("summary","")}</div>'
            f'<a class="src" href="{s.get("source","")}">来源 →</a>'
            f'</div>'
        )

    return (tmpl
        .replace("{{DATE}}", today)
        .replace("{{BODY}}", body_html)
        .replace("{{SIGNALS}}", signal_pills)
        .replace("{{MAJOR_COUNT}}", str(major_count))
        .replace("{{MINOR_COUNT}}", str(minor_count)))


def push_signals(signals: list[dict], today: str) -> None:
    sys.path.insert(0, DISCORD_PUSH_PATH)
    try:
        from discord_push import discord_send
    except ImportError:
        print("[run_tracker] discord_push not importable, skipping push", file=sys.stderr)
        return

    majors = [s for s in signals if s.get("severity") == "major"]
    if not majors:
        print("[run_tracker] no major signals", file=sys.stderr)
        return

    lines = [f"🤖 **AI Agent 赛道动态** {today}"]
    lines.append(f"重大信号 × {len(majors)}")
    lines.append("")
    for s in majors[:10]:
        lines.append(f"**[{s.get('vendor','')}]** {s.get('title','')}")
        if s.get("summary"):
            lines.append(f"  {s['summary'][:160]}")
        if s.get("source"):
            lines.append(f"  → {s['source']}")
        lines.append("")
    lines.append(f"完整日报 → {REPORT_URL}")
    msg = "\n".join(lines)
    if len(msg) > 1900:
        msg = msg[:1900] + "\n…(truncated)"
    discord_send("tracking", msg)


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sources = load_sources()
    state = load_state()
    prompt = build_prompt(sources, state, today)

    print(f"[run_tracker] invoking claude with {len(sources)} sources …", file=sys.stderr)
    raw = run_claude(prompt)

    try:
        env = extract_json(raw)
    except Exception as e:
        debug_path = REPORTS / f"{today}_raw_claude_output.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw)
        print(f"[run_tracker] JSON parse failed: {e}; raw saved to {debug_path}", file=sys.stderr)
        return 1

    report_md = env.get("report_md", "")
    signals = env.get("signals", []) or []
    new_state = env.get("new_state", {}) or {}

    # write markdown
    md_path = REPORTS / f"{today}_ai_agents.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report_md)

    # render HTML
    if TEMPLATE.exists():
        html = render_html(report_md, today, signals)
        html_path = REPORTS / f"{today}_ai_agents.html"
        html_path.write_text(html)
        INDEX_HTML.write_text(html)
        print(f"[run_tracker] HTML → {html_path}", file=sys.stderr)

    # merge state
    merged = dict(state)
    for sid, info in new_state.items():
        merged[sid] = info
    save_state(merged)

    # push major signals
    push_signals(signals, today)

    print(f"[run_tracker] done — {len(signals)} signals ({sum(1 for s in signals if s.get('severity')=='major')} major)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
