# AgentOps Guard v0.3 – v0.5 实施 Spec

状态：draft（2026-07-06）
适用范围：Phase 2（v0.3 Provider abstraction）、Phase 3（v0.4 Policy & privacy guard）、Phase 4（v0.5 Cost & productivity analytics）
前置输入：v0.2 对抗性审查结论（见附录 A 债务清单）

---

## 0. 第一性原则与由此推出的设计决策

README 已有五条原则：默认离线、只读、可解释、可复核、坏日志不阻塞好日志。本 spec 在此之上补充三条推论，它们决定了 v0.3–v0.5 的全部结构性选择：

**推论 1：SQLite 里的派生数据是本地日志的可重建物化视图（materialized view），不是 source of truth。**
源日志始终在磁盘上（`~/.codex`、`~/.claude`、`~/.aicg/raw`）。因此：

- schema 迁移永远不需要复杂的数据搬迁——`aicg rebuild` 从源日志全量重建即可；
- 语义修正（token 口径、状态口径）可以放心地破坏性变更，代价只是一次重扫；
- 任何报表数字必须能通过"重扫 + SQL 查询"复核，这是"可复核"的操作化定义。

该推论有两条明确边界，必须写进实现：

- **DB 分两类表**：派生表（sessions/turns/tool_events/issues/issue_evidence/scan_errors/scan_state/policy_findings/git_activity）可随时 drop + 重扫；**用户态表**（runs、report_snapshots、policy_acks、git_links）记录的是用户输入或历史观点，不可从日志重建，rebuild 与迁移必须原样保留。
- **重建能力受 provider 日志保留期限制**：Claude Code 默认 30 天清理 transcript（`cleanupPeriodDays`），Codex 也会归档/滚动。超过保留期的历史只存在于 DB 中——rebuild 前必须对比 scan_state 与磁盘现存文件，对"已消失的源文件"发出警告并保留 `.bak`；历史趋势的长期载体是 report_snapshots（这正是 v0.5 快照表存在的第一性理由，而不仅是性能优化）。

**推论 2：语义契约必须先于 provider 扩展冻结。**
v0.2 审查证明当前 token/状态/会话身份三个语义层都有错。如果 v0.3 直接在错误语义上抽象 reader contract，每接入一个新 provider 就复制一份错误，且 contract 一旦有第三方实现就再也改不动。所以 **v0.3 的第一交付物是语义规范，第二交付物才是 registry 和新 reader**。

**推论 3：检测器的价值 = 命中时的信息量，永远报警的检测器价值为零。**
v0.4 的 policy guard 不是加更多 pattern，而是把 v0.2 那批"100% 会话命中"的检测器改造成有信噪比的规则系统：区分"调用"与"提到"、区分 violation/warning/needs_review、支持基线排除和确认（ack）机制。

---

## 1. v0.3 — Provider Abstraction（语义契约 + 可扩展 reader 层）

### 1.1 目标

1. 冻结 canonical 语义契约（token、状态、会话身份、turn 身份），修复 v0.2 全部语义级缺陷；
2. 把 codex/claude reader 重构到统一 `ProviderReader` contract 上，附带一套任何 reader 都必须通过的 conformance 测试；
3. 新增 provider 采取"有真实 fixture 才有 reader"的准入制，v0.3 目标是 registry + 2 个存量 reader 重构 + 1 个新 reader + 1 个通用 usage import；
4. 增量扫描（scan_state），解决全量重读和 mtime 窗口漏扫两个问题。

### 1.2 Canonical token 语义（schema v3 的核心）

**规则：所有入库 token 字段必须是互斥桶（disjoint buckets），total 由加法定义，不允许子集字段参与求和。**

| canonical 字段 | 定义 |
| --- | --- |
| `input_uncached_tokens` | 未命中缓存的输入 token |
| `cache_read_input_tokens` | 缓存命中读取的输入 token |
| `cache_creation_input_tokens` | 写入缓存的输入 token（无此概念的 provider 恒为 0） |
| `output_tokens` | 全部输出 token（**含** reasoning） |
| `reasoning_output_tokens` | 信息字段，是 `output_tokens` 的子集，**永不参与 total 求和** |

`total_tokens ≡ input_uncached + cache_read + cache_creation + output`，该公式写入 `docs/semantics.md` 并由 conformance 测试锁定。

Reader 映射责任（业务判断留在 reader 的唯一例外，因为只有 reader 知道 provider 原始语义）：

- **Codex/OpenAI 系**：原始 `input_tokens` 含 cached → `input_uncached = input − cached_input`，`cached_input → cache_read`；原始 `output_tokens` 含 reasoning → 直接映射，reasoning 只作子集记录。
- **Claude**：原始 `input_tokens` 本就不含 cache 桶 → 直接映射为 `input_uncached`。
- **Codex `token_count` 事件**：只接受 `last_token_usage`；`total_token_usage` 是会话累计值，**禁止**落到单个 turn。若只有累计值，reader 必须做差分（记录上一次累计值，差分为本 turn 用量），差分不可得时该 turn token 记 0 并打 `TOKEN_USAGE_UNRELIABLE` 标记，宁可少算不可虚构。

**定价公式随之修正**：五个可计价桶各自乘各自单价（`input_uncached × input价 + cache_read × cache_read价 + ...`），reasoning 不单独计价（已含在 output 内）；price 表里的 `reasoning_output_per_mtok_usd` 字段废弃，config 解析时遇到则输出 deprecation warning。

### 1.3 状态与重试语义

| 状态 | 判定 |
| --- | --- |
| `completed` | 有显式完成证据（turn.completed / 明确的收尾事件） |
| `failed` | 有显式失败证据 |
| `interrupted` | 文件读到 EOF 但最后状态仍是 running（v0.2 错误地记成 completed） |
| `unknown` | 无任何可判定事件 |

- 报表层将 `interrupted` 单列，不计入成功也不计入失败，失败率分母 = completed + failed；
- `retry_count` 语义改为"同一 turn 观察到的失败→重试事件对数"，同一 turn 收到多个 error 事件只计一次失败；session 级 `failed_turns` 与 `retry_count` 拆成两个字段，不再混用。

### 1.4 会话与 turn 身份（解决 resume 双计与跨文件碰撞）

- `sessions` 新增 `native_session_id`（provider 原生 id）、`lineage_id`、`parent_session_id`。Claude resume 场景：新文件复制了历史消息 → reader 通过消息 uuid 前缀重叠识别 lineage，resume 会话记 `parent_session_id`；
- `turns` 新增 `native_turn_key`（provider + 原生消息/turn id），建**唯一索引**。跨文件出现相同 `native_turn_key` 时保留最早归属（首次入库的 session），后来者只允许补全字段不允许重复计数——这一条直接消灭 Claude resume 双计；
- Codex 无原生 turn id 时，`native_turn_key = 文件hash + 文件内序号`，保证跨文件不碰撞（修复 v0.2 的跨文件覆盖 bug）；
- Claude sessionId 缺失于首行时：reader 必须扫到第一个含 sessionId 的事件再定 session 身份，而不是用首行兜底 hash。

### 1.5 ProviderReader contract 与 registry

```python
class ProviderReader(Protocol):
    provider: str                                   # "codex" | "claude" | ...
    def discover(self, since: datetime) -> Iterator[Path]: ...
    def read(self, path: Path) -> ParsedRecords: ...
    def capabilities(self) -> Capabilities: ...
```

`Capabilities` 是声明式矩阵，报表层据此解释缺失数据（"该 provider 无 token 数据"而不是显示 0）：

```python
@dataclass(frozen=True)
class Capabilities:
    token_usage: bool
    cache_semantics: Literal["disjoint", "subset", "none"]
    tool_events: bool
    turn_status: bool
    session_resume: bool
    background_flag: bool
    native_cost: bool          # provider 日志自带成本数字（如 OpenRouter）
```

**Conformance 测试套件**（`tests/conformance/`，任何 reader 必须全绿）：

1. 幂等性：同一文件读两次，ParsedRecords 逐字段相等；
2. token 互斥性：任意 fixture 下 `reasoning ≤ output`，且各桶非负；
3. 身份稳定性：文件追加 N 行后重读，已有 turn 的 id 不变；
4. 中断语义：截断 fixture 尾部 → session 状态必须是 `interrupted`；
5. 坏行韧性：注入任意二进制/半截 JSON 行，不抛异常、malformed 计数正确；
6. 无 raw 泄漏：ParsedRecords 所有字符串字段跑一遍 secret pattern，fixture 中植入的假密钥不得出现在任何输出字段（只允许出现其 hash）。

**Registry**：`aicg/readers/registry.py`，内置 reader 显式注册；`aicg providers` 命令列出 provider + capabilities 矩阵；`aicg scan --provider codex|claude|all`（默认 all）。v0.3 不做 entry-point 动态插件（那是 v1.0 的事），只保证"新 reader = 新文件 + 注册一行 + conformance 全绿"。

### 1.6 新 provider 准入与范围

**准入规则：没有真实（脱敏后）日志 fixture 的 provider 不写 reader。** README 里 Cursor/Zed/OpenCode/Qwen/Ollama 是候选池而非承诺清单。

v0.3 范围锁定：

1. codex、claude 重构到 contract（必做）；
2. **一个新 reader**：优先 OpenCode 或 Gemini CLI（本地 JSONL、格式公开、易取 fixture），选型以"两周内能拿到真实 fixture"为准；
3. **通用 usage import**（不是 reader）：`aicg import usage --provider openrouter --file usage.json`，把 OpenRouter 导出文件/OpenAI-compatible usage JSONL 映射为无 turn 明细的 session 级记录，capabilities 全 false 除 token_usage/native_cost。满足"OpenRouter/local model usage import，但不做云端 API 拉取"。

Cursor（SQLite 内部库，格式不稳定）明确推迟，写入 Non-goals。Qwen/DeepSeek/Kimi/Ollama 类本地模型日志同样进入候选池：无统一日志格式，v0.3 不写专用 reader；其中 OpenAI-compatible 的 usage 输出可直接走 `aicg import usage`，其余等真实 fixture。

usage import 的两条补充规则：

- **幂等**：导入记录 id = `stable_id("import", 文件hash, 行号)`，同一文件重复导入不产生重复 session；
- **native cost 优先**：capabilities 声明 `native_cost=true` 的记录直接使用日志自带成本，本地价格表不再对其计价（避免双重估算）；报表标注成本来源（`native` / `local_pricing` / `unavailable`）。

### 1.7 增量扫描

新表 `scan_state(source_file PK, file_hash, file_size, mtime, last_line, last_scanned_at)`：

- 文件 hash/size/mtime 均未变 → 跳过（消灭全量重读）；
- 仅追加（size 增大且旧区间 hash 前缀一致的近似判断：size 增大 + mtime 更新即可，重读全文件但只是单文件级）→ 重读该文件（v0.3 不做行级断点续读，保持简单）；
- **发现机制与 mtime 窗口解耦**：discover 返回"scan_state 里没有的文件 ∪ mtime 在窗口内的文件"，修复"mtime 早于窗口但从未扫过"的漏扫。

### 1.8 `aicg rebuild` 与 schema v3 迁移

- `CURRENT_SCHEMA_VERSION = 3`（后续每个引入 schema 变更的版本 +1：v0.4 = 4，v0.5 = 5）；
- 打开 version < CURRENT 的 DB：提示 `run: aicg rebuild`，scan/summary 拒绝在旧语义 DB 上继续写入（防止新旧口径混存）；打开 version > CURRENT 的 DB：拒绝并提示升级 aicg（修复"未来版本盖章"问题）；
- `aicg rebuild [--since all]`：备份旧 DB 为 `aicg.sqlite.bak-<ts>`，保留用户态表（见第 0 节），drop 派生表后重建 schema、全量重扫；重扫前对比 scan_state 与磁盘，对已消失的源文件打印警告（历史缺口如实呈现，不静默）。这是推论 1 的直接兑现：迁移 = 重建，`tests/test_schema_migration.py` 的重心从"补列"转为"v2 DB → rebuild → self-check 全绿 + 用户态表原样保留"。

**报表与导出的版本随动**：canonical 语义改变了 `daily_report` 的字段含义（token 桶、interrupted 计数、计价覆盖率显示），`DAILY_REPORT_SCHEMA_VERSION` 升为 2，`docs/report-schema.md` 记录逐字段变更；`export` 的列集合同步更新并在 JSON 输出顶层加 `formatVersion`，CSV 在文档中声明列变更（消费方需按版本适配）。self_check 的 required keys / REQUIRED_COLUMNS / REQUIRED_INDEXES 同步扩展到新表和 `native_turn_key` 唯一索引。

### 1.9 v0.3 一并清偿的工程债

- 所有 `IN (...)` 查询按 500 个变量分块（`db.py` 提供 `chunked_in()` helper）；
- `connect()` 设 `PRAGMA busy_timeout=5000` + WAL；
- Markdown 渲染统一走 `escape_md_cell()`（转义 `|`、反引号、换行），日志内容不再能破坏/注入日报；
- CSV 空导出写 header；
- issues 的报表窗口口径统一为"session 窗口"（export --kind issues 改为 join sessions 过滤）；
- 成功扫描后清除该文件的 scan_errors 行；
- `_sanitize_command` 泛化：所有 capture 命令（不限 `codex exec` 形态）的位置参数一律 `[redacted-arg]`，只保留可执行名与 flag 名入 `runs.command`；
- PROJECT_FAILURE_HOTSPOT 的 evidence pointer 不再用 MIN/MAX 跨文件混合 hash 与行号，改为指向失败率最高的单个样本 session；
- **全局 CLI 错误处理约定**：exit 0 = 成功；1 = 目标不存在（如 inspect 未命中）；2 = 用法/配置错误；3 = 检查类命令发现问题（v0.4 policy check 复用）；4 = 运行时错误（DB locked、IO 失败），输出一行人类可读错误而非裸 traceback，`--debug` 才显示完整堆栈；
- doctor 随 provider 抽象演进：provider 相关小节（binary/版本/日志目录/文件计数）由 registry 驱动生成，新 reader 注册后自动出现在 doctor 报告中；`_recent_jsonl_errors` 先按 mtime 排序再截断 max_files，并增加单文件读取字节上限。

### 1.10 v0.3 验收标准

1. conformance 套件对 codex/claude/新 reader 全绿；
2. resume fixture（真实 Claude resume 日志脱敏）token 只计一次；同 thread 双 rollout fixture 不再互相覆盖；
3. 人工核对：对一天真实日志，`total_tokens` 与 provider 官方用量页数字偏差 < 5%（这是"成本可信"的最终裁判）；
4. 截断 fixture 产出 `interrupted`，日报失败率分母正确；
5. 5000 个 session 的 DB 上 `summary --since 30d` 不崩、< 10s；
6. 二次 `scan` 在无新日志时 0 文件重读，报告字节级一致。

---

## 2. v0.4 — Policy & Privacy Guard（把检测器变成规则系统）

### 2.1 目标

1. `policy.toml`：用户可审计的规则文件，支持 per-project override；
2. 检测语义修正：区分**调用面**（tool 的 command/url 参数）与**内容面**（消息正文提到），三级结论 violation / warning / needs_review；
3. 噪音治理：基线排除 + finding 去重 + `ack` 确认流；
4. `aicg policy check` 独立命令，exit code 可用于 CI/pre-commit 门禁（仍然完全离线）；
5. 解决 capture 的隐私矛盾（流式脱敏）。

### 2.2 policy.toml

位置 `~/.aicg/policy.toml`，`init` 生成带注释的默认文件：

```toml
[policy]
version = 1

[policy.secrets]
enabled = true
builtin = ["openai_key", "anthropic_key", "github_token", "bearer_header", "generic_env_assignment"]
# 自定义规则：命名 + 正则，finding 只记录规则名和 hash，永不记录匹配原文
[[policy.secrets.custom]]
name = "internal_token"
pattern = "itk_[A-Za-z0-9]{32}"

[policy.sensitive_paths]
enabled = true
builtin = ["dot_env", "ssh_keys", "cloud_credentials"]
# v0.2 的 /Users/... 全路径匹配移除；home 路径本身不是敏感信号
[[policy.sensitive_paths.custom]]
name = "company_vault"
pattern = "(^|/)vault/"

[policy.services]
# 只对"调用面"生效：shell command、tool 参数里的 URL、MCP server 地址
mode = "denylist"            # denylist | allowlist
deny = ["api.openai.com"]
allow = []
# 内容面提到受限域名默认只产生 needs_review，且默认关闭
flag_mentions = false

[policy.raw_payload]
# 从固定 2048B 提高，且只对 tool 输出/输入生效，不对助手正文生效
threshold_bytes = 65536

[policy.overrides."/Users/me/work/oss-project"]
services.mode = "allowlist"
services.allow = ["api.openai.com"]
```

规则文件本身就是审计对象：`policy check` 输出包含 policy 文件的 hash 和 version，报告可复核"当时用的是哪套规则"。

### 2.3 检测语义：调用面 vs 内容面

这是 v0.4 最重要的一刀。v0.2 把子串匹配跑在所有 content 上，导致"聊到 openai 文档 = 违规"。新模型：

| 信号来源 | 例子 | 最高可产生 |
| --- | --- | --- |
| **调用面** | Bash tool 的 `command` 参数、WebFetch 的 `url`、MCP server 配置的 `url`、HTTP 客户端调用参数 | `violation` |
| **内容面** | 助手回复正文、日志消息、README 内容 | `needs_review`（默认关闭） |

实现要求：reader 在 tool_events 上新增 `call_target` 字段（从 tool 参数中抽取的域名/host 列表，只存域名不存完整 URL 参数），policy 引擎只对 `call_target` 做 allowlist/denylist 判定。域名抽取规则（URL parse + 常见命令 curl/wget/http 的参数识别）写进 `docs/semantics.md`。

secrets/sensitive_path 同理分级：出现在 tool 调用参数里（正在被 agent 主动使用）→ violation；出现在 tool 输出里（被读到了）→ warning；出现在正文提及 → needs_review。

自身豁免：provider 调用自家 API（claude 日志出现 anthropic 域名、codex 出现 openai 域名）内置豁免，除非用户显式列入 deny。

### 2.4 Finding 生命周期与 ack

新表：

```sql
CREATE TABLE policy_findings (
    id TEXT PRIMARY KEY,          -- stable_id(rule, session, call_target/hash) → 天然去重
    session_id TEXT, tool_event_id TEXT,
    rule_id TEXT NOT NULL,        -- "services.denylist:api.openai.com"
    level TEXT NOT NULL,          -- violation | warning | needs_review
    surface TEXT NOT NULL,        -- call | output | mention
    detail_hash TEXT,             -- 匹配内容的 hash，永不存原文
    first_seen_at TEXT, last_seen_at TEXT
);
CREATE TABLE policy_acks (
    finding_id TEXT PRIMARY KEY,
    reason TEXT,                  -- 用户自己写的备注
    acked_at TEXT
);
```

- 同一 finding 重复出现只更新 `last_seen_at`，日报只报**新增**和**未 ack** 的 finding；
- `aicg policy ack <finding_id> --reason "approved oss usage"`：确认后从默认报表消失，`--all` 视图仍可见（审计不删数据）；`policy_acks` 是用户态表，rebuild/迁移原样保留；
- privacy/policy 板块从"扁平 flag 计数"改为"新增 N / 未处理 M / 已确认 K"三段式；README Phase 3 承诺的 **privacy risk trend section** 在 v0.4 基于 `first_seen_at`/`last_seen_at` 给出窗口内新增趋势，跨周期长趋势在 v0.5 由 report_snapshots 接管。

**Schema v4（v0.4）**：新增 `policy_findings`、`policy_acks` 两表 + `tool_events.call_target` 列；版本升级走统一的 rebuild 流程（policy_acks 保留）。

### 2.5 CLI 与 CI 门禁

```bash
aicg policy check --since 24h [--format md|json] [--fail-on violation|warning]
```

exit code：0 = 干净或仅有已 ack 项；3 = 存在达到 `--fail-on` 级别的未 ack finding；2 = 用法/配置错误。这让"哪些服务不能被 agent 调用"变成可以放进 pre-commit / CI 的硬规则，且全程离线。

`aicg policy rules`：打印当前生效规则（合并 override 后），带来源标注（builtin/custom/override），满足"可审计"。

### 2.6 capture 隐私矛盾的最终解

`capture` 改为**流式脱敏落盘**：写入 `raw/` 之前逐行过 `redact_secrets` + policy secret 规则；文件头写入 marker `{"aicg_capture": true, "redacted": true, ...}`。同时提供 `aicg capture --no-store`（只统计不落盘，管道透传）。README Privacy Boundary 更新为："capture 落盘内容经过脱敏，且可用 --no-store 完全关闭"。scan 端识别 capture marker，不再把非 JSONL stdout 误判成 session（capture 文件解析失败时静默按 run 记录，不产生垃圾 session）。

### 2.7 v0.4 验收标准

1. 在真实的一天日志上，默认规则下 finding 数量 ≤ 10（信噪比验收，而不是功能验收）；
2. "对话里提到 api.openai.com" 的 fixture 不产生 violation；"Bash curl api.openai.com" 的 fixture 产生 violation；
3. ack 后再次 scan，该 finding 不再出现在日报，`--all` 可见；
4. `policy check --fail-on violation` 在干净 repo 返回 0，在违规 fixture 返回 3；
5. policy_findings 表全表扫描无任何字段能还原原始 payload（self-check 新增该检查）；
6. capture 植入假密钥 → raw 文件里只有 `[REDACTED]`。

---

## 3. v0.5 — Cost & Productivity Analytics（从总量到浪费归因）

### 3.1 目标

前提是 v0.3 语义正确（否则是在垃圾数据上做分析）。v0.5 回答三个问题：**浪费在哪**（wasted cost 归因）、**趋势如何**（周/月对比）、**值不值**（cost per outcome，git 关联）。

### 3.2 报表周期与快照

- `summary --period day|week|month`（默认 day，等价现有 --since 24h）+ `--compare`（自动对比上一周期，输出 delta）；
- **周期边界用本地时区**（用户对账的对象是自己的一天），周期起点以带 offset 的 ISO 串存储；`--utc` 可切换为 UTC 边界，同一 DB 内混用两种边界的快照按 `(period_type, period_start)` 自然区分；
- 新表 `report_snapshots(period_type, period_start, report_json, report_hash, created_at, PRIMARY KEY(period_type, period_start))`：同一周期重复运行 summary 做 upsert（最新覆盖），保证"每个周期恰好一份权威快照"，趋势查询无二义。趋势 = 查快照，不重算历史；快照 hash 保证"当天报表说了什么"可审计。快照是用户态表（见第 0 节），`rebuild` 时保留——它同时是超出 provider 日志保留期后历史趋势的唯一载体。

### 3.3 浪费归因模型（wasted cost）

在 canonical 语义上定义四类浪费，全部可由 SQL 复核：

| 类别 | 定义 |
| --- | --- |
| `failed_cost` | status=failed 的 session 全部成本 |
| `retry_cost` | 非 failed session 中，失败 turn 及其后同 task 重试 turn 的成本 |
| `interrupted_cost` | interrupted session 的成本 |
| `bloat_cost_estimate` | TOOL_OUTPUT_BLOAT 会话中超阈值字节折算的输入 token 估算（标注为估算值） |

日报新增 "Waste breakdown" 板块：总成本、浪费成本、浪费率，按 project × model 展开 top 10。`cost_per_successful_session = 总成本 / completed session 数`（含浪费分摊——失败的钱也是花掉的钱，这是"真实成本"的第一性定义）。

### 3.4 效率指标

- **缓存效率**：`cache_read / (input_uncached + cache_read)`，实现 config 里已预留但 v0.2 未实现的 `low_cache_hit_ratio` 检测器（LOW_CACHE_HIT issue：大输入 + 低缓存命中 → 建议检查会话组织方式）；
- **模型切换/fallback 趋势**：基于修正后的 MODEL_SWITCH（排除 unknown 假阳性：只统计非 NULL model 的 distinct 数），按周聚合；
- **任务拆分信号**：token 分布 P50/P95 per task_type，P95/P50 比值过大提示"该类任务适合拆分"。

### 3.5 Git 关联（可选、只读、本地）

```bash
aicg git link /path/to/repo        # 登记 repo ↔ project_path 映射
aicg git sync --since 7d           # 只读本地 git log，不触网
```

- `git link` 写入用户态表 `git_links(repo_path, project_path, linked_at)`；`git sync` 产出派生表 `git_activity(project_path, period_start, commits, merge_commits, insertions, deletions, synced_at)`，数据全部来自本地 `git log --numstat --merges`；
- 报表新增 `estimated_cost_per_merge`（分母 = merge commits，作为 merged PR 的本地代理指标——不调 GitHub API，保持离线；README 预留的 `merged_pr_count` 字段由它填充，字段名如实叫 `merge_commit_count`，不谎称 PR）；
- `accepted_lines`、`human_review_time` 继续留空预留，v0.5 不虚构无法本地测量的指标（第一性原则：测不到就标 unavailable，不给幻觉数字）。

### 3.6 Schema v5（v0.5）

- `report_snapshots`、`git_activity`、`git_links`（repo ↔ project_path 映射，用户态表）三张新表；
- `sessions` 增加 `wasted_cost_usd`（派生缓存列，rebuild 可重建）；
- 迁移策略同 v0.3：版本不匹配 → 提示 rebuild；用户态表（report_snapshots、git_links、policy_acks、runs）原样保留，git_activity 为派生表可由 `git sync` 重建；
- `DAILY_REPORT_SCHEMA_VERSION` 升为 3（新增 waste breakdown、compare、cost source 字段），report-schema.md 同步。

### 3.7 v0.5 验收标准

1. `summary --period week --compare` 输出上周 delta，数字可由两份快照 JSON 手工相减复核；
2. 浪费四类成本之和 ≤ 总成本（self-check 新增不变式检查）；
3. 在无价格配置时，所有 cost 类指标显示 unavailable，token 类指标正常（不因缺价格阻塞分析）；
4. git sync 在无网络环境全功能可用；未 link 的 project 各指标显示 unavailable；
5. LOW_CACHE_HIT 在高缓存命中 fixture 上不触发、低命中大输入 fixture 上触发。

---

## 4. 交付顺序与依赖

```
v0.3  语义契约 → conformance 套件 → codex/claude 重构 → scan_state/rebuild
      → 工程债（IN 分块/WAL/MD转义） → 新 reader + usage import
v0.4  call_target 抽取（依赖 v0.3 contract） → policy.toml + 引擎
      → findings/ack → policy check CLI → capture 脱敏
v0.5  快照表 → 浪费归因 → 周期对比 → 缓存效率/切换趋势 → git 关联
```

每个版本内部，排序原则一致：**先修正确性，再加表面积**。任何新报表板块的前提是它引用的字段语义已被 conformance 测试锁定。

### 各版本 Non-goals（防 scope 蔓延）

- v0.3：不做 entry-point 插件系统；不做 Cursor reader；不做行级断点续扫；
- v0.4：不做自动修复/自动删日志（只报告）；不做网络侧真实流量检测（只有日志证据）；不做正则之外的 secret 检测（无 ML/熵检测）；
- v0.5：不调 GitHub/GitLab API；不做 HTML dashboard（那是 v0.7）；不虚构 accepted_lines / review time。

---

## 附录 A：v0.2 审查债务 → 版本映射

| 审查发现 | 修复版本 | 对应章节 |
| --- | --- | --- |
| token 子集混算、定价高估 | v0.3 | 1.2 |
| total_token_usage 累计值落单 turn | v0.3 | 1.2 |
| 部分计价显示为完整成本 | v0.3（报表显示 pricedTurns 覆盖率）/ v0.5（板块化） | 1.2 / 3.3 |
| Claude resume 双计 | v0.3 | 1.4 |
| Codex 跨文件 turn id 碰撞 | v0.3 | 1.4 |
| 中断会话记 completed | v0.3 | 1.3 |
| retry 语义混乱（failed 计入 retry、重复 +1） | v0.3 | 1.3 |
| Claude sessionId 取自首行、缺失时回落文件 hash | v0.3 | 1.4 |
| isSidechain 只看首事件 | v0.3（reader 重构时按事件级采集） | 1.5 conformance |
| task_type 垃圾值（"user"/"summary"） | v0.3 reader 重构 | 1.5 |
| MODEL_SWITCH unknown 假阳性 | v0.3（排除 NULL）/ v0.5（趋势化） | 3.4 |
| SENSITIVE_PATH / RAW_PAYLOAD 全量命中 | v0.4 | 2.2 / 2.3 |
| RESTRICTED_SERVICE 子串匹配 | v0.4 | 2.3 |
| capture 违反隐私边界 | v0.3（标记+声明）→ v0.4（流式脱敏） | 1.9 / 2.6 |
| IN 子句变量上限 | v0.3 | 1.9 |
| 无 busy_timeout / WAL | v0.3 | 1.9 |
| schema 版本无条件盖章 / 未来版本不拒绝 | v0.3 | 1.8 |
| Markdown 注入 | v0.3 | 1.9 |
| 全量重读无增量 | v0.3 | 1.7 |
| doctor bounded 只限文件数 | v0.3（顺带：先按 mtime 排序再截断，加单文件字节上限） | 1.9 |
| scan_errors 不清除 | v0.3 | 1.9 |
| 非 codex-exec 命令参数原样入 runs.command | v0.3 | 1.9 |
| main() 只捕 ValueError，DB locked 裸 traceback | v0.3 | 1.9 |
| hotspot evidence 跨文件混合 hash/行号 | v0.3 | 1.9 |
| fixture 理想化 | 持续：conformance 准入制 | 1.5 / 1.6 |
