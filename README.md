# AgentOps Guard

AgentOps Guard 是一个 local-first 的 AI coding agent 使用诊断与成本日报 CLI。它先支持 Codex 和 Claude Code，目标是帮助个人或团队看清楚本地 agent 工作流里的 token 消耗、失败率、重试循环、后台异常、隐私风险、策略风险和诊断信号。

命令入口保持稳定：

```bash
python -m aicg
```

当前版本是 `v0.8`。这个阶段的重点不是云端商业化，也不是接管账单系统，而是把本机日志变成可信、可迁移、可复核、不会泄露 raw prompt/code/output 的本地报告、review 诊断、dashboard、provider 插件接口和 metadata 数据层。

## Why

AI coding agent 的真实成本不只是一张账单。更常见的问题是：

- 同一个项目反复失败，但用户只看到“又跑了一次”。
- 后台任务或恢复失败消耗了大量 token，却没有日报级可见性。
- 模型 fallback、profile 锁定、thread restore 错误散落在不同日志里。
- 本地日志里可能包含 key、敏感路径、完整 payload 或受限服务调用。
- 成本估算如果没有明确价格表，就很容易变成看似精确的幻觉数字。

AgentOps Guard 的第一性原则是：默认离线、只读、可解释、可复核、坏日志不阻塞好日志。

## Current Capabilities

`v0.8` 已实现：

- `python -m aicg init`
- `python -m aicg capture codex -- ...`
- `python -m aicg providers`
- `python -m aicg providers verify <module_or_package> --fixtures fixtures/`
- `python -m aicg scan --since 24h --provider all`
- `python -m aicg rebuild --since all`
- `python -m aicg import usage --provider openrouter --file usage.json`
- `python -m aicg summary --since 24h --out daily.md`
- `python -m aicg summary --since 24h --format html --out daily.html`
- `python -m aicg summary --since 24h --redact-paths --out daily.md`
- `python -m aicg summary --period week --compare --out weekly.md`
- `python -m aicg dashboard --period day --out ~/.aicg/reports/dashboard.html`
- `python -m aicg alerts check --period day`
- `python -m aicg schedule print --scheduler cron|launchd|systemd --time 09:00`
- `python -m aicg review --session <session_id> --format md|json`
- `python -m aicg review --last`
- `python -m aicg review --since 24h --top 5`
- `python -m aicg review --project <path> --since 7d`
- `python -m aicg policy check --since 24h --fail-on violation`
- `python -m aicg policy rules`
- `python -m aicg policy ack <finding_id> --reason "approved"`
- `python -m aicg git link /path/to/repo`
- `python -m aicg git sync --since 7d`
- `python -m aicg doctor`
- `python -m aicg doctor --json`
- `python -m aicg doctor --online --json`
- `python -m aicg doctor --deep --max-files 500`
- `python -m aicg doctor --self-check --json`
- `python -m aicg inspect session <session_id> --format md|json`
- `python -m aicg inspect source <source_file_hash> --format md|json`
- `python -m aicg export --kind sessions|turns|tool-events|issues|scan-errors --format json|csv --out <path>`
- `python -m aicg export --kind sessions --format json --redact-paths --out sessions.json`

当前 reader 支持：

- Codex JSONL rollout/session logs
- Legacy/imported Codex JSONL under `~/.aicg/raw/codex`
- Claude Code project transcript JSONL

当前 analyzer 支持：

- `HIGH_COST_TASK`
- `HIGH_OUTPUT_TOKEN_RATIO`
- `PROJECT_FAILURE_HOTSPOT`
- `RETRY_LOOP`
- `BACKGROUND_CONSUMPTION`
- `MODEL_SWITCH_ANOMALY`
- `TOOL_OUTPUT_BLOAT`
- `MCP_OVERUSE`
- `RAW_PAYLOAD_RISK`
- `POSSIBLE_SECRET`
- `RESTRICTED_SERVICE_CALL`

## Privacy Boundary

AgentOps Guard 默认只处理本地文件：

- No web app
- No cloud service
- No external API calls by default
- No billing page scraping
- No automatic cache deletion or config repair
- No raw prompt storage
- No raw code storage
- No raw command output storage
- No raw environment variable storage

SQLite 只保存 normalized metadata，例如 project path、provider、model、session/turn status、token counts、duration、retry count、tool names、output byte counts、hashed errors、privacy flags 和 policy flags。

`python -m aicg capture codex -- ...` 默认只透传子进程 stdout 并记录 sanitized run metadata；它不会把 stdout 复制到 `raw/codex`。显式使用 `--store-redacted` 时，capture 只写入 redacted JSONL marker 和 redacted stdout lines；`--no-store` 是默认行为。`raw/codex` 仅作为 legacy/imported JSONL 的本地扫描目录保留。

文件解析失败会进入 `scan_errors`，只保存 `error_type` 和 `error_message_hash`，不会保存原始异常全文。
Issue evidence 会进入 `issue_evidence`，只保存 source hash、line range、metric key/value 和 message hash，不保存 raw payload 或 redacted excerpt。
Review evidence 会进入 `review_evidence`，只保存 source hash、line range、metric key/value 和 message hash，不保存 prompt/code/output 原文或 redacted excerpt。

## Local State

默认目录：

```text
~/.aicg/
  config.toml
  aicg.sqlite
  raw/codex/
  raw/claude/
  reports/
  policy.toml
```

临时运行可以设置 `AICG_HOME`：

```bash
AICG_HOME=/tmp/aicg-demo python -m aicg init
```

## Quickstart

初始化：

```bash
python -m aicg init
```

扫描最近 24 小时的 Codex 和 Claude Code 日志：

```bash
python -m aicg scan --since 24h --provider all
```

扫描来源包括：

- `~/.aicg/raw/codex/**/*.jsonl`（legacy/imported local JSONL）
- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/**/*.jsonl`
- `~/.claude/projects/**/*.jsonl`

生成 Markdown 日报：

```bash
python -m aicg summary --since 24h --out ~/.aicg/reports/daily.md
```

生成 JSON 日报：

```bash
python -m aicg summary --since 24h --format json --out ~/.aicg/reports/daily.json
```

生成单周期 HTML 报告：

```bash
python -m aicg summary --since 24h --format html --out ~/.aicg/reports/daily.html
```

生成周期快照并对比上一周期：

```bash
python -m aicg summary --period week --compare --out ~/.aicg/reports/weekly.md
```

生成本地 dashboard：

```bash
python -m aicg dashboard --period day --out ~/.aicg/reports/dashboard.html
```

检查本地 alert 阈值：

```bash
python -m aicg alerts check --period day
```

打印系统调度器配置文本：

```bash
python -m aicg schedule print --scheduler cron --time 09:00
python -m aicg schedule print --scheduler launchd --time 09:00
python -m aicg schedule print --scheduler systemd --time 09:00
```

Review 单个高风险 session：

```bash
python -m aicg review --session <session_id> --format md
python -m aicg review --session <session_id> --export-issue review.md
```

按浪费/失败风险排序批量 review 或按项目聚合复发模式：

```bash
python -m aicg review --since 24h --top 5
python -m aicg review --project /path/to/project --since 7d --format json
```

查看 provider capability：

```bash
python -m aicg providers
python -m aicg providers verify my_provider_plugin --fixtures fixtures/
```

重建派生表：

```bash
python -m aicg rebuild --since all
```

检查本地 policy：

```bash
python -m aicg policy check --since 24h --fail-on violation
```

运行默认离线诊断：

```bash
python -m aicg doctor
```

默认 `doctor` 是 offline/shallow：只读本地文件，不调用 `codex doctor --json`，并用 `--max-files` 限制目录扫描。

显式启用 Codex 自带 doctor：

```bash
python -m aicg doctor --online --json
```

显式启用深度扫描：

```bash
python -m aicg doctor --deep --max-files 500 --out ~/.aicg/reports/doctor-deep.md
```

运行 AICG 自检：

```bash
python -m aicg doctor --self-check --json
```

Inspect 单个 session：

```bash
python -m aicg inspect session <session_id> --format json
python -m aicg inspect source <source_file_hash> --format json
```

导出 normalized metadata：

```bash
python -m aicg export --kind issues --format json --since 24h --out ~/.aicg/reports/issues.json
python -m aicg export --kind sessions --format csv --since 24h --out ~/.aicg/reports/sessions.csv
python -m aicg export --kind sessions --format json --redact-paths --out ~/.aicg/reports/sessions-redacted.json
```

## Daily Report

日报包含：

- Overview
- Project ranking
- Provider/model breakdown
- Top expensive tasks as `session/model/task_type` rollups
- Failure and retry table
- Background anomalies
- Tool usage summary
- Privacy and policy findings
- High-risk issues
- Recommended fixes
- Consistency checks in JSON output

`v0.5` 的日报先生成 `daily_report` JSON model，再从同一个 model 渲染 Markdown。session 计数按 `session_id` 去重；即使一个 session 被拆成多个 `model/task_type` rollup，privacy/policy、failure/retry、background anomalies、waste 和 git activity 也不会被重复放大。

`v0.6` 起，High-risk issues 会追加 `run aicg review --session <id> for diagnosis` 引导。Review 是按需深查，不会读取 raw prompt/code/output；它诊断的是失败形状，例如重复 error hash、上下文膨胀、早期 shell 失败、输出洪泛和 interrupted tail。Review 默认只展示 source hash 和行号；需要在本机解析 source hash 时，显式运行 `python -m aicg inspect source <source_file_hash>`。

`v0.7` 起，`summary --format html` 会从同一个 report model 生成单文件 HTML 报告。HTML 输出不加载网络资源，不引用前端依赖，并对日志派生字符串做 HTML 转义。

## Local Dashboard And Alerts

`python -m aicg dashboard` 生成静态 HTML 文件，不启动 Web server。dashboard 数据来自当前周期 report、`report_snapshots` 历史快照、`review_findings` 聚合和 `alert_events` 历史记录；趋势图使用内联 SVG，禁用 JavaScript 时数字和图表仍然可读。

`[alerts]` 默认不启用。需要本地阈值时，在 `~/.aicg/config.toml` 中显式配置：

```toml
[alerts]
daily_cost_usd_max = 5.0
daily_waste_rate_max = 0.3
new_policy_violations_max = 0
interrupted_sessions_max = 3
```

`python -m aicg alerts check --period day` 命中阈值时返回 exit 3，并把事件追加到 `alert_events`。`summary --period day|week|month` 写入快照后也会评估 alerts。AgentOps Guard 不内置邮件、Slack、webhook 或 daemon；需要通知时，可以让 cron/launchd/systemd 根据 exit code 处理。

`python -m aicg schedule print` 只向 stdout 打印 cron/launchd/systemd 配置文本，不写 `crontab`、`~/Library/LaunchAgents` 或 systemd 目录。生成的命令使用当前 Python 解释器运行 `-m aicg`，并在当前环境存在 `AICG_HOME` 时保留该值。

## v0.8 Stability And Plugins

`v0.8` 引入 schema v8：`schema_meta.hash_salt` 在 `init` 时生成，所有日志/报表派生 hash 使用本机 salt 做 HMAC-SHA256。同一份日志在两台机器上不会产生可关联 hash；同一机器 rebuild 后 hash 稳定。旧 DB 需要 `python -m aicg rebuild --since all`。

Provider 插件通过 `aicg.providers` entry point 暴露 `ProviderReader`。第三方插件默认必须先通过：

```bash
python -m aicg providers verify <module_or_package> --fixtures <fixtures_dir>
```

通过后会写入本地 `provider-verifications.json`，记录 provider、entry point、distribution version、module file hash 和 fixture hashes；之后 `scan`、`providers`、`doctor` 会把匹配记录的插件标为 `verified=true`。插件代码或安装版本变化后需要重新 verify。`--allow-unverified` 只用于显式本地实验；默认 `doctor` 不会 import 未验证 provider。

稳定性、插件、迁移与安全边界见：

- `docs/stability.md`
- `docs/provider-plugin-guide.md`
- `docs/migration-guide.md`
- `docs/threat-model.md`
- `schemas/*.schema.json`

## Local Pricing

成本估算必须来自本地显式价格表。AgentOps Guard 不抓 billing 页面，也不根据模型名猜价格。

lookup key 必须精确匹配 `provider:model`：

```toml
[prices."codex:gpt-5.5"]
input_per_mtok_usd = 0
cached_input_per_mtok_usd = 0
output_per_mtok_usd = 0
reasoning_output_per_mtok_usd = 0
cache_creation_input_per_mtok_usd = 0
cache_read_input_per_mtok_usd = 0
credit_per_usd = 1
```

`reasoning_output_per_mtok_usd` 已废弃。`reasoning_output_tokens` 是
`output_tokens` 的子集，不参与 total，也不单独计价。

无匹配价格时，`estimated_cost_usd` 和 `credit_estimate` 保持 unavailable。

Provider token 语义按来源处理，避免重复计算：

- Codex/OpenAI: raw `input_tokens` 可包含 cached input；canonical total 使用 `input_uncached_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens`。
- Claude: raw `input_tokens` 本身是不含 cache bucket 的 input；canonical total 同样使用上述互斥 bucket。
- Markdown 日报只有在所有 turn 都匹配本地价格时才把成本显示为完整估算；部分匹配时会标明 priced turn coverage，并明确 unpriced turns excluded。

## Doctor

默认 `doctor` 收集：

- Python version
- AICG local state paths
- Codex binary/version
- `CODEX_HOME`
- Codex config/profile/MCP summary
- Codex models cache
- Codex state DB/log DB summary
- Codex session file counts
- Claude binary/version
- Claude config/cache/transcript summary
- Recent error-like JSONL signals with hashed messages
- Large cache/history file signals from bounded shallow scan
- Read-only repair suggestions

`doctor --online` 会额外调用本机 `codex doctor --json`，并解析 warning/fail checks。这个行为必须显式 opt-in。

`doctor --deep` 会递归扫描大目录，但仍受 `--max-files` 限制。报告会输出 `mode.offline`、`mode.deep`、`limits.maxFiles` 和 `scanCapReached`，方便解释诊断覆盖范围。

`doctor --self-check` 会验证 DB schema version、required tables/columns/indexes、config parse、price table numeric fields、report JSON consistency、overview counts，以及 `scan_errors`、`review_evidence` 是否只保存 hash/指针。

Doctor 永远不自动删除缓存、不修改 Codex/Claude 配置、不重建 state DB。

## Architecture

当前代码结构：

```text
aicg/
  cli.py
  config.py
  db.py
  alerts.py
  dashboard.py
  doctor.py
  analyzer.py
  pricing.py
  reporter.py
  schedule.py
  self_check.py
  readers/
    codex_jsonl.py
    claude_jsonl.py
tests/
  fixtures/
```

核心数据流：

```text
local JSONL logs
  -> provider reader
  -> normalized sessions/turns/tool_events
  -> SQLite
  -> pricing
  -> analyzer issues
  -> Markdown/JSON reports
```

设计约束：

- Provider reader 只负责把日志归一化，不做业务判断。
- Analyzer 只基于 normalized metadata 和 thresholds 产出 issues。
- Reporter 只做聚合和展示，不重新解释 raw logs。
- Pricing 只使用本地 config，不做网络请求。
- Doctor 只读，默认 offline，所有大目录扫描都必须有边界。

## Long-Term Roadmap

### Phase 0: Trustworthy local core

状态：完成，对应 `v0.1.1`。

目标：

- Codex + Claude Code local logs
- SQLite schema
- idempotent scan
- file-level parser resilience
- offline bounded doctor
- Markdown daily summary
- privacy/policy flags
- local exact-match pricing
- regression tests for reader, reporter, doctor, pricing and scan resilience

下一步重点是继续扩充真实日志 fixtures，让统计结果在更多边缘格式下保持可信。

### Phase 1: Schema and evidence hardening

目标版本：`v0.2`

状态：完成。

计划：

- 增加 schema versioning 和 migration tests。
- 固化 `daily_report` JSON schema，Markdown 从 JSON schema 渲染。
- 为每个 issue 增加 evidence pointer，但仍不保存 raw payload。
- 增加 `aicg inspect session <id>`，只展示 metadata 和 hashed evidence。
- 增加 `aicg export --format json|csv`。
- 增加更多 malformed JSONL、partial transcript、missing fields、rotated logs fixtures。
- 增加 `aicg doctor --self-check`，验证 DB schema、config 和 report consistency。

成功标准：

- 任何坏日志都不能阻塞日报。
- Overview、project ranking、issue counts 可由 SQLite 查询复核。
- SQLite schema 可从旧版本平滑迁移。

### Phase 2: Provider abstraction

目标版本：`v0.3`

计划：

- 抽象 provider registry。
- 支持 Cursor/Zed/OpenCode 日志 reader。
- 支持 OpenRouter/local model usage import，但不做云端 API 拉取。
- 支持本地 Qwen/DeepSeek/Kimi/Ollama style logs。
- 增加 provider-specific capabilities matrix。
- 增加 `aicg scan --provider codex|claude|all`。

成功标准：

- 新 provider 只需要实现 reader contract。
- 不同 provider 的 token、model、tool、status 语义能映射到统一 schema。
- 未识别字段不会破坏现有 provider。

### Phase 3: Policy and privacy guard

目标版本：`v0.4`

计划：

- 增加 `policy.toml`。
- 支持 restricted service allowlist/denylist。
- 支持 sensitive path rules。
- 支持 secret pattern rules。
- 支持 per-project policy overrides。
- 增加 `aicg policy check --since 24h`。
- 增加 privacy risk trend section。
- 增加 raw payload risk classifier 的阈值配置。

成功标准：

- 用户可以把“哪些服务不能被 agent 调用”“哪些路径不能进入日志”写成可审计规则。
- 报告可以区分 warning、violation 和 needs review。
- 仍然不保存 raw sensitive data。

### Phase 4: Cost and productivity analytics

目标版本：`v0.5`

计划：

- 增加 weekly/monthly reports。
- 增加 per-project cost trend。
- 增加 failed cost 和 retry cost。
- 增加 model switch/fallback trend。
- 增加 estimated cost per successful task。
- 预留 `accepted_lines`、`merged_pr_count`、`human_review_time` 字段。
- 支持 Git metadata optional import，用于估算 `estimated_cost_per_merged_pr`。

成功标准：

- 用户能看到“哪个项目最烧 token”“哪个模型最容易失败”“哪类任务最适合拆分”。
- cost 不再只是总量，而能解释浪费来自哪里。

### Phase 5: Review module

目标版本：`v0.6`

状态：已实现。

- 增加 `review` module。
- 分析任务是否存在 repeated failure、tool output bloat、context overload、missing setup。
- 增加 `aicg review --session <id>`、`--last`、`--since --top`、`--project`。
- 输出“下次运行前建议”。
- 支持把高风险 session 导出成 issue template。

成功标准：

- 工具不仅告诉用户“花了多少”，还能告诉用户“为什么失败”和“下一次怎么降低失败概率”。

### Phase 6: Local dashboard and automation

目标版本：`v0.7`

状态：已实现。

- 增加 local-only static HTML dashboard。
- 增加 trend charts。
- 增加 `aicg summary --format md|json|html`。
- 增加 cron/launchd/systemd 示例。
- 增加 alert thresholds，但只输出本地文件或 terminal summary。

成功标准：

- 用户不用打开 SQLite，也能查看趋势。
- 自动日报不需要云服务。

### Phase 7: Public package and stable API

目标版本：`v1.0`

计划：

- 发布 pip package。
- 稳定 CLI flags。
- 稳定 normalized event schema。
- 稳定 report JSON schema。
- 提供 provider plugin guide。
- 提供 example configs。
- 提供 threat model 文档。
- 提供 migration guide。

成功标准：

- 用户可以放心把它放进自己的本地 agent 工作流。
- 第三方可以贡献 provider reader，而不用理解全部内部实现。

## Non-Goals

短期不做：

- 云端 SaaS。
- 自动读取 billing 页面。
- 自动删除 Codex/Claude 缓存。
- 自动修改 agent 配置。
- 自动重建 Codex state DB。
- 保存 raw prompt/code/output。
- 默认联网诊断。

这些限制不是功能缺失，而是为了先保证工具可信、可审计、可公开。

## Development

运行测试：

```bash
python -m pytest -q
```

建议开发流程：

```bash
AICG_HOME=/tmp/aicg-dev python -m aicg init
AICG_HOME=/tmp/aicg-dev python -m aicg scan --since 24h
AICG_HOME=/tmp/aicg-dev python -m aicg summary --since 24h --out daily.md
AICG_HOME=/tmp/aicg-dev python -m aicg doctor --json
```

提交新功能时，优先补：

- reader fixture
- schema/import test
- analyzer test
- reporter test
- CLI smoke test

## Project Direction

AgentOps Guard 的长期方向不是“又一个账单统计器”，而是一个本地 agent operations layer：

- cost: 看到真实消耗和浪费来源。
- reliability: 看到失败率、重试和恢复问题。
- privacy: 看到敏感 payload、路径和 secret 风险。
- policy: 看到受限服务调用和模型 fallback 风险。
- review: 给出下一次 agent run 前的修复建议。

先把本地可信内核做扎实，再逐步扩展 provider 和 report surface。
