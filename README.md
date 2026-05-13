# AI Agent 赛道 Tracker

每日扫描 WorkBuddy / Manus / Coze / QwenPaw / OpenClaw / 红手指 等 10+ 个源，用 Claude 提炼中文新动态日报。重大信号自动推送。

公开页面：https://cherielilili.github.io/workbuddy-tracker/

## 数据流

1. 每日 04:35（Shanghai）通过 LaunchAgent 触发 `runner.sh`
2. `scripts/run_tracker.py` 调用 Claude CLI，让 Claude 用 WebFetch 抓取每个源
3. 与 `data/sources_state.json` 记录的上次摘要做语义 diff
4. 输出 markdown + HTML 报告到 `reports/`
5. `index.html` 始终指向当日最新一份
6. 重大信号自动推送

## 数据源

见 `sources.yaml`。
