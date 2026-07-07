# AgentOps Guard v0.6 – v0.8 实施 Spec

状态：draft（2026-07-07）
适用范围：Phase 5（v0.6 Review module）、Phase 6（v0.7 Local dashboard & automation）、Phase 7 前置（v0.8 = 1.0 Release Candidate）
前置文档：`docs/spec-v0.3-v0.5.md`（本文假设其全部交付物已落地：canonical 语义、conformance 套件、policy 引擎、浪费归因、report_snapshots）

**版本与 README 路线图的对应关系**：README 把 Phase 7 直接映射到 v1.0。本 spec 把 Phase 7 的全部实质工作（打包、插件 API、threat model、稳定性冻结）前置到 v0.8 作为 Release Candidate，v1.0 只做"冻结确认 + 发布"——稳定性承诺不应该和大量新代码在同一个版本里出生。

---

## 0. 新增第一性推论

延续 v0.3–v0.5 spec 的推论 1–3，本阶段再补三条：

**推论 4：报表面（report surface）是攻击面。**
日报、dashboard、issue template 的内容全部派生自**不可信输入**（agent 日志：模型输出、工具输出、文件路径都可能被第三方内容污染）。v0.3 已处理 Markdown 转义；v0.7 引入 HTML 后攻击面扩大一个数量级。规则：所有日志派生字符串在进入任何渲染面之前必须过对应的转义层，且用注入 fixture 做回归——这不是加固项，是渲染层的准入条件。

**推论 5：诊断必须是确定性的、可复现的。**
review 模块回答"为什么失败"。如果诊断本身不可复现（比如引入 LLM 判断），工具就失去了"可解释、可复核"的立身之本。v0.6 的 review 是纯规则引擎：同一个 DB 跑两次 review，输出逐字节一致。LLM 辅助诊断永久列为可选扩展、默认关闭、且不进 1.0 范围。

**推论 6：稳定性是一个交付物，不是一个声明。**
1.0 的承诺（CLI/schema/插件 API 不破坏）只有在冻结对象被完整枚举、有弃用政策、有兼容性测试兜底时才成立。所以 v0.8 的核心交付物是三份"冻结清单"和对应的回归测试，而不是新功能。

---

## 1. v0.6 — Review Module（从"浪费在哪"到"为什么失败"）

### 1.1 目标

1. 基于 normalized metadata 的**确定性诊断规则引擎**，产出带证据指针和置信度的诊断结论；
2. `aicg review` 命令族：单 session 深查、按浪费排序批查、按 project 聚合复发模式；
3. "下次运行前建议"（pre-run checklist）：把诊断转成可执行动作清单；
4. 高风险 session 导出为 issue template（Markdown，零 raw 内容）。

### 1.2 核心约束：不读 raw 内容如何诊断"为什么失败"

这是 v0.6 的第一性难题。答案：review 诊断的是失败的**形状**（shape），不是失败的内容：

- 相同 `error_message_hash` 重复 N 次 = "同一个错误被原样重试了 N 次"——不需要知道错误内容是什么，就能断定 agent 在打转；
- 逐 turn 的 `input_uncached_tokens` 单调增长 + `cache_read` 占比坍塌 = 上下文膨胀；
- 前 K 个 shell tool_event 的 exit_code ∈ {127, 126, 1} 密集出现 = 环境未就绪；
- 单次 tool 输出字节暴涨后紧跟失败 turn = 输出洪泛冲垮上下文。

当用户需要看内容时，review 输出**源指针**（source_file + 行号区间）——用户打开的是自己磁盘上本来就有的日志，工具从头到尾不复制、不引用原文。这与"可解释"不矛盾：解释的对象是模式，证据的载体是指针。

### 1.3 Session 时间线特征提取

新增内部模块 `aicg/review/features.py`：对单个 session 把 turns + tool_events 按时间排序，提取派生特征（全部由现有 schema 计算，不新增采集）：

| 特征 | 来源 |
| --- | --- |
| `error_hash_repetitions` | 相同 error_message_hash 的连续/总计出现次数 |
| `token_growth_curve` | 逐 turn input_uncached 序列的斜率与单调段长度 |
| `cache_ratio_curve` | 逐 turn cache_read / (uncached + cache_read) |
| `tool_failure_runs` | 相同 (tool_name, status=failed) 的连续段 |
| `early_shell_failures` | 前 K 个 shell 事件中非零 exit_code 计数与码值分布 |
| `edit_success_ratio` | file 类 tool 成功数 / 总数 |
| `output_spikes` | 单事件 output_bytes 超过 session P95×N 的位置 |
| `model_switch_points` | model 变更的 turn 位置及其后失败密度 |
| `idle_gaps` | 相邻事件时间差超阈值的区段 |

特征提取与规则判定分层（对应 reader/analyzer 分层原则）：features 只算数，rules 只做判断。

### 1.4 诊断规则（v0.6 初始集）

每条规则输出：`code`、`confidence`（high/medium/low，按证据强度的固定映射，不是概率）、`detail`、`recommendation`、evidence pointers。

| Code | 触发（默认阈值，全部进 config `[review]` 节） | 建议方向 |
| --- | --- | --- |
| `REPEATED_IDENTICAL_FAILURE` | 同 error_hash 相邻重复 ≥ 3 次；confidence 随相邻重复次数升级 | 同一错误在被原样重试；先人工解决根因再继续 |
| `EDIT_RETRY_CHURN` | 同一 tool_name 的 file 类失败连续段 ≥ 3 | 检查目标文件状态/冲突，考虑更小的编辑粒度 |
| `CONTEXT_OVERLOAD` | reliable input 单调增长段 ≥ 5 turns 且末端 > 起点 3×，cache 占比同期下降；零起点和 `TOKEN_USAGE_UNRELIABLE` 不参与 | 拆分任务或重开会话；把长产物移出上下文 |
| `TOOL_OUTPUT_FLOOD` | output spike 后 2 turns 内出现失败或 token 跳变 | 给命令加过滤/分页；限制单次输出 |
| `MISSING_SETUP` | 前 5 个 shell 事件中强 setup 信号累计达阈值；普通 exit 1 只作弱证据 | 运行前先手动验证环境（依赖/权限/路径） |
| `NO_PROGRESS_THRASHING` | turn 数 ≥ P90 且 mutating edit_success_ratio < 0.3 且无 completed 收尾；零编辑/只读会话不触发 | 任务对 agent 过大或缺关键信息，改变任务表述 |
| `MODEL_FALLBACK_DEGRADATION` | model 切换点之后满足最小样本、绝对失败数和失败率增量 | 检查 profile/fallback 配置，锁定模型重试 |
| `INTERRUPTED_TAIL` | status=interrupted 且最后一个事件是运行中 tool | 检查是否人为中断/崩溃；恢复前先 review 本报告 |

规则集版本化：`REVIEW_RULESET_VERSION`，review 输出携带该版本号（同一 DB + 同一规则版本 = 逐字节一致输出，推论 5 的验收锚点）。

### 1.5 CLI

```bash
aicg review --session <id> [--format md|json]
aicg review --last                         # 最近一个 session
aicg review --since 24h --top 5            # 按 wasted_cost 排序批量 review
aicg review --project <path> --since 7d    # 跨 session 复发模式聚合
aicg review --session <id> --export-issue <path.md>
```

- `--project` 聚合视图回答"这个项目反复死在哪一步"：按 code 聚合出现次数、涉及 session 数、首次/最近出现时间；默认仅 review 最近 200 个匹配 session，`--limit 0` 才全量；
- exit code 沿用全局约定：发现 high confidence 诊断返回 3（可选 `--fail-on`），供脚本编排。

**Pre-run checklist**：md 输出末尾固定为 "Before the next run" 段，规则按 confidence 降序生成动作项，每项带 `[code]` 前缀，可直接粘给 agent 或人。

**Issue template 导出**：包含 session 元数据表、诊断列表、evidence 指针（source hash + 行区间 + metric）、复现命令（`aicg inspect session <id>`）。模板顶部固定声明："本文件不含任何 prompt/代码/输出原文"。导出前跑一遍 secret pattern 自检（复用 conformance 第 6 项），防止未来字段扩展意外引入原文。

### 1.6 存储与 Schema v6

- 派生表 `review_findings(id, session_id, ruleset_version, code, confidence, detail, recommendation, created_at)` + 复用 `issue_evidence` 结构的 `review_evidence` 表；同 session 重跑 review 采用 replace 语义；
- 存储的目的不是缓存（review 很快），而是让 `--project` 聚合和 v0.7 dashboard 的"复发诊断"板块可查询；
- rebuild 时 drop（纯派生）；self_check 增加两表的结构检查。

### 1.7 与 analyzer 的边界

analyzer（scan 时自动跑）保持轻量阈值检测不变；review 是按需深查。两者共享 Issue/Evidence 数据模型但表分开、code 空间分开（analyzer 是 `HIGH_COST_TASK` 类资源型，review 是 `REPEATED_IDENTICAL_FAILURE` 类行为型）。日报的 High-risk issues 板块 v0.6 起追加一行引导：`run aicg review --session <id> for diagnosis`。

### 1.8 v0.6 验收标准

1. 每条规则至少一个触发 fixture + 一个恰好不触发的边界 fixture；
2. 确定性：同 DB 同规则版本连跑两次 review，输出逐字节一致；
3. 无原文：对植入假密钥/长 payload 的 fixture，review 全部输出（md/json/issue template）过 secret pattern 与长度检查，只出现 hash 和指针；
4. `--project` 聚合在 20 个 session 的 fixture 上正确合并复发 code；
5. 1000 turns 的 session review < 2s；
6. 阈值全部可在 config `[review]` 覆盖，非法配置走 exit 2。

### 1.9 v0.6 Non-goals

- 不做 LLM 辅助诊断（推论 5，永久默认关闭的可选项，不进 1.0）；
- 不做自动修复/自动重跑；
- 不读、不摘录、不"脱敏后引用"任何 raw 内容——指针是唯一出口。

---

## 2. v0.7 — Local Dashboard & Automation（Phase 6）

### 2.1 目标

1. 单文件、零网络请求的静态 HTML dashboard；
2. 趋势图表（数据源 = report_snapshots，v0.5 已铺好）；
3. `summary --format md|json|html`；
4. 调度接入：生成 cron/launchd/systemd 配置**文本**，不写系统目录；
5. alert 阈值：本地文件 + exit code，不做任何网络通知。

### 2.2 Dashboard 技术形态（第一性选型）

**选型：Python 端生成内联 SVG 图表 + 单 HTML 文件，不引入 JS 图表库。**

推导：dashboard 的约束是零网络（离线原则）、零供应链新增（可审计原则）、内容不可信（推论 4）。CDN 引用直接违反离线；vendor 一个 minified chart 库 = 引入一个无法逐行审计的二进制等价物。趋势图是折线/柱状/表格，Python 生成 SVG 完全够用，且输出是可 diff、可测试的文本。

- 产物：`aicg dashboard --out ~/.aicg/reports/dashboard.html`（同时 `summary --format html` 输出单周期 HTML 报表，共享渲染层）；
- 单文件自包含：内联 CSS、内联 SVG、少量内联 vanilla JS（仅 tab 切换/表格排序，JS 禁用时全部内容仍可读——SVG 是静态的）；
- `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">` 作为纵深防御；HTML 层新增 `escape_html()` 转义函数，与 v0.3 的 `escape_md_cell()` 并列，所有日志派生字符串进 HTML 前强制经过；
- 板块：总览卡片（周期对比 delta）、成本/token 趋势线（快照序列）、浪费率趋势、project 排行、provider/model 分布、policy findings 三段式、review 复发诊断 top、alert 历史。

### 2.3 数据流

```
report_snapshots (+当前周期实时 report + review_findings 聚合 + alert_events)
  -> dashboard model (JSON, 版本化: DASHBOARD_SCHEMA_VERSION = 1)
  -> SVG/HTML 渲染
```

dashboard model 与渲染分离（与 daily_report 先 JSON 后 Markdown 同构），model 可用 `--format json` 单独导出供第三方自行渲染。

### 2.4 调度接入

```bash
aicg schedule print --scheduler cron|launchd|systemd [--time 09:00] [--commands "scan,summary,dashboard"]
```

只向 stdout 打印配置文本（crontab 行 / plist / timer+service unit），由用户自行安装。理由：工具承诺只读，不碰系统配置目录；打印出的配置本身也是可审计文本。文档给出三平台完整示例与卸载方法。**不做** `--install` 选项——写 `~/Library/LaunchAgents` 是修改系统行为，越过只读边界。

### 2.5 Alerts

config 新增：

```toml
[alerts]
daily_cost_usd_max = 5.0
daily_waste_rate_max = 0.3
new_policy_violations_max = 0
interrupted_sessions_max = 3
```

- 评估时机：`summary` 落快照后 + 独立命令 `aicg alerts check --period day`；
- 输出三通道，全部本地：终端 summary 行、追加 `alert_events` 表（append-only，记录阈值/实测值/周期/config hash）、exit 3。用户要通知就拿 exit code 自己接（cron 的 MAILTO、自建脚本），工具不内置任何 notifier；
- `alert_events` 属于历史记录：append-only、rebuild 保留（记录的是"当时报过什么警"，与快照同类）。

### 2.6 Schema v7

- 新表 `alert_events`（用户态，rebuild 保留）；
- `report_snapshots` 增加 `dashboard_model_json` 缓存列（可空，派生）；
- `DASHBOARD_SCHEMA_VERSION = 1` 起始。

### 2.7 v0.7 验收标准

1. dashboard HTML 静态解析零外部引用（测试：解析产物中不存在 http(s)://、//、data: 之外的资源 URL）；用 file:// 打开完整可用；
2. 注入 fixture（tool_name/project_path 含 `<script>`、`</td>`、`![x](`）在 md 与 html 两个渲染面都惰性呈现；
3. JS 禁用时所有数字与图表仍可读；
4. 90 天快照生成 dashboard < 3s，产物 < 2MB；
5. `schedule print` 三平台输出可直接安装运行（CI 至少验证 cron 行与 systemd unit 语法）；
6. alerts：阈值命中 → exit 3 + alert_events 落表；未配置 `[alerts]` 时 `alerts check` 输出说明并 exit 0。

### 2.8 v0.7 Non-goals

- 不做常驻 daemon / watch 模式（调度交给系统调度器）；
- 不做 Web server（哪怕 localhost）——HTML 是文件，不是服务；
- 不内置邮件/Slack/webhook 通知；
- 不引入任何第三方前端依赖。

---

## 3. v0.8 — 1.0 Release Candidate（Phase 7 前置）

### 3.1 目标

把 README Phase 7 的全部实质工作在 v0.8 完成并接受真实使用检验，v1.0 只做冻结确认。交付物分四组：**打包与插件 API**、**冻结清单与弃用政策**、**安全加固与 threat model**、**发布工程**。

### 3.2 打包

- PyPI 包 `aicg`，console script `aicg`（`python -m aicg` 永久保留等价入口）；
- **零运行时依赖政策**成为书面契约（当前事实如此，v0.8 起写进 `docs/stability.md`）：stdlib-only 使供应链审计成本为零，任何未来想引入依赖的 PR 需要在文档中论证；
- requires-python >= 3.11，CI 矩阵 3.11/3.12/3.13 × macOS/Linux；Windows 进 CI 为 best-effort（路径处理用 pathlib 已基本兼容，标记 experimental，不阻塞发布）。

### 3.3 Provider 插件 API（v0.3 registry 的对外化）

- entry point group `aicg.providers`：第三方包注册 `ProviderReader` 实现；
- **conformance 即准入**：`aicg providers verify <module_or_package>` 对插件跑完整 conformance 套件（含插件自带 fixtures），未通过的插件 registry 拒绝加载（可 `--allow-unverified` 显式放行，标注 experimental）；conformance 套件作为公共 API 随包发布（`aicg.conformance`）；
- 信任模型写明：安装插件 = 执行第三方代码，与 pip 安装任何包同级；`aicg providers` 列表标注 origin（builtin / third-party）与 verified 状态；doctor 输出加载的插件清单及其版本；
- Provider plugin guide（`docs/provider-plugin-guide.md`）：contract 逐字段语义（直接引用 `docs/semantics.md`）、capabilities 声明规范、fixture 要求（必须真实脱敏日志）、conformance 本地运行方式、一个完整示例插件仓库。

### 3.4 冻结清单（推论 6 的落地）

三份清单进 `docs/stability.md`，每项标 `stable | experimental | deprecated`：

1. **CLI 面**：命令、flag、exit code、stdout 结构（机器可读部分）。v0.8 全面盘点：`capture` 若仍未达到 v0.4 脱敏验收标准则标 experimental 或移除；
2. **数据面**：DB schema（含用户态/派生表分类）、daily_report / dashboard model / export format 的 JSON schema——正式落成 `schemas/*.schema.json` 文件随包分发，self_check 用 schema 文件校验替代手写 required keys；
3. **插件面**：`ProviderReader` protocol、`Capabilities` 字段、`ParsedRecords` 模型、conformance 套件语义。

**弃用政策**：stable 项的破坏性变更需先在一个 minor 版本打 DeprecationWarning 再移除；DB schema 变更永远走 rebuild 且用户态表跨版本保留。兼容性由测试兜底：`tests/test_stability.py` 快照锁定 CLI --help 输出结构、report JSON schema、export 列集合。

### 3.5 安全加固与 Threat Model

`docs/threat-model.md` 结构：资产（本地日志、metadata DB、报表产物）、信任边界（本机文件系统内，无网络面）、攻击者模型与对策：

| 威胁 | 对策 | 版本 |
| --- | --- | --- |
| 日志内容注入报表/dashboard（间接 prompt injection、HTML/MD 注入） | 双渲染面强制转义 + CSP + 注入 fixture 回归 | v0.3/v0.7 已做，v0.8 补 fuzz |
| 恶意 provider 插件 | conformance 准入 + origin 标注 + 文档化信任模型 | 3.3 |
| 低熵字符串的 hash 可被字典逆推（error message、短 secret 的 sha256 可穷举） | **全库加盐**：`init` 时生成随机 salt 存 schema_meta，所有 message/error hash 改为 HMAC-SHA256(salt)；跨机器分享报表时 hash 不可关联、不可字典测试 | v0.8（schema v8，走 rebuild） |
| 报表/DB 被分享到机器外泄露路径与用户名 | `export/summary --redact-paths`：home 前缀替换为 `~`，project 路径可选替换为稳定别名 | v0.8 |
| reader 解析崩溃/资源耗尽（超长行、深嵌套 JSON、zip-bomb 型日志） | 结构感知 fuzz（从 fixtures 变异生成语料，CI 每日跑）+ 单行/单文件字节上限 | v0.8 |

残余风险如实列出（例：工具无法阻止 agent 本身泄露数据，只能事后发现；salt 泄露则 hash 保护降级）。

### 3.6 Schema v8

- schema_meta 增加 `hash_salt`（init 生成，rebuild 保留——salt 属于用户态）；
- 全部 `*_hash` 字段切换 HMAC 口径：旧 DB 升级必须 rebuild（hash 不可迁移，如实提示）；
- `schemas/` 目录随包分发，self_check 改为按 JSON schema 文件校验。

### 3.7 发布工程

- GitHub Actions：测试矩阵（含 conformance、fuzz smoke、stability 快照测试）、构建 sdist/wheel、产物 SHA256SUMS 随 release 发布；
- 测试覆盖率地板：语句覆盖 ≥ 80%，readers/analyzer/review 核心 ≥ 90%，低于地板 CI 失败；
- 性能预算进 CI（用生成的合成大库）：5 万 session DB 上 `summary --period month` < 30s、`dashboard` < 10s、增量 `scan`（无新文件）< 1s；
- `docs/migration-guide.md`：v0.2→v0.8 每一跳的操作（统一为 rebuild）与行为变化摘要；
- 示例配置集 `examples/`：常见 provider 价格表**示例**（文件头注明日期与"价格会过时，工具不猜价"的免责声明——与"不根据模型名猜价格"原则一致）、policy.toml 场景示例（个人宽松/团队严格）、三平台调度配置。

### 3.8 v0.8 验收标准

1. `pip install aicg && aicg init && aicg scan` 在干净环境（macOS/Linux）全绿；
2. 示例第三方插件（独立仓库）通过 `providers verify` 并出现在 scan/doctor/dashboard 全链路；
3. stability 快照测试锁定三份冻结清单，任何 stable 项变动导致 CI 失败；
4. 加盐后：同一日志在两台机器（不同 salt）产生的 DB，hash 字段无一相同；同机 rebuild 前后 hash 稳定；
5. fuzz 语料 24h 运行无 crash、无 hang、无内存超限（单文件解析峰值内存 < 500MB）；
6. `--redact-paths` 输出中不出现 `/Users/<name>` 或 `/home/<name>` 明文；
7. threat model、plugin guide、migration guide、stability 文档齐备且与实现互查一致（文档中的每个 CLI 示例进 doctest 级冒烟测试）。

### 3.9 v0.8 Non-goals

- 不做云端/网络功能（永久）；
- 不做插件市场或远程插件分发；
- 不做 GUI 应用（dashboard 是文件）；
- 不承诺 Windows stable（best-effort experimental）；
- 不做历史 DB 的 hash 原位迁移（rebuild 是唯一路径）。

---

## 4. 交付顺序与依赖

```
v0.6  features 提取层 → 规则引擎 + 规则 fixture → review CLI →
      --project 聚合 → issue template 导出（复用 conformance 无原文自检）
v0.7  escape_html 转义层 + 注入 fixture → dashboard model → SVG 渲染 →
      summary --format html → schedule print → alerts + alert_events
v0.8  加盐 hash（schema v8, 尽早——影响所有后续 fixture）→ 打包/entry point →
      conformance 对外化 + providers verify → 冻结清单 + stability 测试 →
      threat model / fuzz / redact-paths → 发布工程 → 文档集
```

跨版本依赖：v0.7 dashboard 的"复发诊断"板块依赖 v0.6 的 review_findings 表；v0.8 的加盐变更会使 v0.6/v0.7 所有含 hash 的 fixture 需要按 salt 注入方式重生成——**因此 fixture 从 v0.6 起就应把 hash 断言写成"经同一 hash 函数计算"而非硬编码字面量**，这是给正在开发 v0.3–v0.5 的同事的提前量提示（v0.3 conformance 套件同样适用）。

### 里程碑 gate

- v0.6 出口 gate：review 在团队自身真实日志上跑一周，至少一半诊断被人工确认"有用"（诊断质量是主观验收，需要 dogfooding，不能只靠 fixture）；
- v0.7 出口 gate：dashboard 被真实用作每日查看入口 ≥ 1 周，没有回到"手动开 SQLite"的场景；
- v0.8 出口 gate：一个非作者按 plugin guide 独立写出通过 verify 的插件；migration guide 被用于一次真实 v0.5→v0.8 升级。

---

## 附录 A：README Phase 承诺 → 本 spec 章节映射

| README 承诺 | 章节 |
| --- | --- |
| Phase 5: repeated failure / tool output bloat / context overload / missing setup 分析 | 1.4 |
| Phase 5: `aicg review --session <id>` | 1.5 |
| Phase 5: 下次运行前建议 | 1.5 pre-run checklist |
| Phase 5: 高风险 session 导出 issue template | 1.5 |
| Phase 6: local-only static HTML dashboard | 2.2 |
| Phase 6: trend charts | 2.2/2.3（数据源 = v0.5 快照） |
| Phase 6: `summary --format md\|json\|html` | 2.2 |
| Phase 6: cron/launchd/systemd 示例 | 2.4（print-only，不安装） |
| Phase 6: alert thresholds，只输出本地文件/终端 | 2.5 |
| Phase 7: pip package | 3.2 |
| Phase 7: 稳定 CLI flags / event schema / report schema | 3.4 |
| Phase 7: provider plugin guide | 3.3 |
| Phase 7: example configs | 3.7 |
| Phase 7: threat model 文档 | 3.5 |
| Phase 7: migration guide | 3.7 |
| Phase 7: 第三方可贡献 provider reader | 3.3（conformance 准入 + verify 命令） |
