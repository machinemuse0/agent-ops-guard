# AgentOps Guard

AgentOps Guard 是一个 local-first 的 AI coding agent 使用诊断与成本日报 CLI。它先支持 Codex 和 Claude Code，目标是帮助个人或团队看清楚本地 agent 工作流里的 token 消耗、失败率、重试循环、后台异常、隐私风险、策略风险和诊断信号。

命令入口保持稳定：

```bash
python -m aicg
```

当前版本是 `v0.9` release prep。这个阶段的重点不是云端商业化，也不是接管账单系统，而是把本机日志变成可信、可迁移、可复核、不会泄露 raw prompt/code/output 的本地报告、review 诊断、dashboard、provider 插件接口、格式漂移观测和 metadata 数据层，并补齐 `v1.0` 的稳定契约与发布闸门。

## Install And First Check

AgentOps Guard 需要 Python 3.11+。如果你在源码目录里试用，可以直接运行 `python -m aicg`；如果要从任意目录使用，先安装到当前 Python 环境：

```bash
python -m pip install .
python -m aicg --version
```

建议首次使用先用独立目录验证，不影响已有 `~/.aicg`：

```bash
AICG_HOME=/tmp/aicg-demo python -m aicg init
AICG_HOME=/tmp/aicg-demo python -m aicg doctor --self-check --json
```

默认日志位置是 `~/.codex` 和 `~/.claude`。如果你使用了自定义目录，可以在运行 `scan` 前设置：

```bash
CODEX_HOME=/path/to/codex-home CLAUDE_CONFIG_DIR=/path/to/claude-config python -m aicg scan --since 24h
```

## Why

AI coding agent 的真实成本不只是一张账单。更常见的问题是：

- 同一个项目反复失败，但用户只看到“又跑了一次”。
- 后台任务或恢复失败消耗了大量 token，却没有日报级可见性。
- 模型 fallback、profile 锁定、thread restore 错误散落在不同日志里。
- 本地日志里可能包含 key、敏感路径、完整 payload 或受限服务调用。
- 成本估算如果没有明确价格表，就很容易变成看似精确的幻觉数字。

AgentOps Guard 的第一性原则是：默认离线、只读、可解释、可复核、坏日志不阻塞好日志。

## Current Capabilities

`v0.9` 已实现：

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
- `python -m aicg schedule print --scheduler cron --time 09:00`
- `python -m aicg review --session <session_id> --format md`
- `python -m aicg review --last`
- `python -m aicg review --since 24h --top 5`
- `python -m aicg review --project <path> --since 7d`
- `python -m aicg security audit --since 24h --out security.md`
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
- `python -m aicg inspect session <session_id> --format json`
- `python -m aicg inspect source <source_file_hash> --format json`
- `python -m aicg export --kind sessions --format json --out sessions.json`
- `python -m aicg export --kind sessions --format json --redact-paths --out sessions.json`
- `python -m aicg fixture redact input.jsonl --out fixture.jsonl`
- `python -m aicg support bundle --out bundle.json`

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
- `LOW_CACHE_HIT`
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
  raw/codex/      # legacy/imported Codex JSONL
  raw/claude/     # reserved local staging; built-in Claude reader scans ~/.claude/projects
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

如果输出 `files scanned: 0`，通常表示这个时间窗口内没有可读的新日志，或者本机使用了自定义 `CODEX_HOME` / `CLAUDE_CONFIG_DIR`。这不是初始化失败；可以先跑 `python -m aicg doctor --json` 看本机路径识别结果。

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

输出本地 agent 命令安全审计报告：

```bash
python -m aicg security audit --since 24h --format md --out ~/.aicg/reports/security.md --fail-on high
python -m aicg security audit --since 24h --format json --out ~/.aicg/reports/security.json --fail-on none
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

## v0.8-v0.9 Stability, Plugins, And Support

`v0.8` 引入 schema v8：`schema_meta.hash_salt` 在 `init` 时生成，所有日志/报表派生 hash 使用本机 salt 做 HMAC-SHA256。同一份日志在两台机器上不会产生可关联 hash；同一机器 rebuild 后 hash 稳定。旧 DB 需要 `python -m aicg rebuild --since all`。

`v0.9` 引入 schema v9：`format_observations` 记录 reader 看到的未知事件类型和顶层字段漂移。`scan` 会打印 `format drift: ...` 摘要，`doctor` 展示 Format drift 小节，日报在未知事件比例超过默认 5% 时提示 token 统计可能不完整。

`v0.9` release prep 进一步引入 schema v10：`tool_events` 增加 `security_flags`、`security_detail` 和 `command_hash`，用于 `security audit` 的 no-raw 命令风险报告。

冻结前新增两个本地支持命令：

```bash
python -m aicg fixture redact input.jsonl --out fixture.jsonl --keep-structure
python -m aicg support bundle --out bundle.json
```

`fixture redact` 用于把真实 JSONL 脱敏成可贡献 fixture；`support bundle` 只生成本地 JSON 文件，不上传、不联网，生成后会提示人工检查再手动分享。

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
- `SECURITY.md`
- `CONTRIBUTING.md`
- `docs/accuracy-audit.md`
- `docs/release-checklist-1.0.md`
- `docs/adr/`
- `aicg/schemas/*.schema.json`（repo 根目录保留匹配的 `schemas/*.schema.json` 便于 review）

`v1.0` release readiness 是本地可检查的：

```bash
python scripts/check_release_ready.py
python scripts/check_release_ready.py --target 1.0.0
```

在 30 天 accuracy audit、外部 beta 和 P0/P1 bug bar 完成前，`--target
1.0.0` 必须显示 `blocked`，不能被误判成 ready。

## Local Pricing

成本估算必须来自本地显式价格表。AgentOps Guard 不抓 billing 页面，也不根据模型名猜价格。

运行 `python -m aicg init` 后，在 `~/.aicg/config.toml` 里添加价格表。lookup key 必须精确匹配 `provider:model`：

```toml
[prices."codex:gpt-5.5"]
input_per_mtok_usd = 0
cached_input_per_mtok_usd = 0
output_per_mtok_usd = 0
cache_creation_input_per_mtok_usd = 0
cache_read_input_per_mtok_usd = 0
credit_per_usd = 1
```

`reasoning_output_per_mtok_usd` 已在 v0.9 移除。`reasoning_output_tokens`
是 `output_tokens` 的子集，不参与 total，也不单独计价；self-check 会拒绝仍包含该字段的价格表。

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

`doctor --self-check` 会验证 DB schema version、required tables/columns/indexes、config parse、price table numeric fields、包内 JSON schema 文件、report JSON consistency、overview counts，以及 `scan_errors`、`review_evidence` 是否只保存 hash/指针。

Doctor 永远不自动删除缓存、不修改 Codex/Claude 配置、不重建 state DB。

## Architecture

当前代码结构：

```text
aicg/
  cli.py
  config.py
  db.py
  alerts.py
  analyzer.py
  conformance.py
  dashboard.py
  doctor.py
  fixture_redact.py
  policy.py
  pricing.py
  reporter.py
  schedule.py
  self_check.py
  support.py
  schemas/
  readers/
    codex_jsonl.py
    claude_jsonl.py
  review/
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

## Roadmap And Release Contracts

历史阶段和设计推导保存在：

- `docs/spec-v0.3-v0.5.md`
- `docs/spec-v0.6-v0.8.md`
- `docs/spec-v0.9-v1.0.md`

`v1.0` 不再是新功能里程碑，而是稳定契约的签署：

- CLI/Data/Plugin semver 语义：`docs/stability.md`
- 安全响应政策：`SECURITY.md`
- 贡献与 fixture 准入：`CONTRIBUTING.md`
- 30 天成本准确性审计：`docs/accuracy-audit.md`
- 发布清单与 release notes 草稿：`docs/release-checklist-1.0.md`、`docs/release-notes-1.0.md`
- 关键架构决策：`docs/adr/`

1.x 主题种子只作为候选池，不代表承诺顺序：

- 新 provider reader 继续走 fixture 准入制，多数应以第三方插件形态存在。
- 报表 i18n。
- policy 规则包。
- dashboard drill-down。
- `accepted_lines` / `human_review_time` 的本地可测来源探索。

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
