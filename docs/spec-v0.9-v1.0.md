# AgentOps Guard v0.9 – v1.0 实施 Spec

状态：draft（2026-07-07）
适用范围：v0.9（Feature Freeze & External Validation）、v1.0（Stable Release，README Phase 7 兑现）
前置文档：`docs/spec-v0.3-v0.5.md`、`docs/spec-v0.6-v0.8.md`（本文假设 v0.8 RC 已交付：打包、插件 API、冻结清单、threat model、加盐 hash）

**定位**：v0.8 结束时功能面已完整。v0.9 不再造功能，主体是冻结纪律 + 外部真实使用验证，外加三件"关舱门之前必须装上的安全设备"。v1.0 不是功能里程碑，是一份关于未来的契约——发布那一刻真正交付的东西是"以后怎么变"的规则。

---

## 0. 新增第一性推论

**推论 7：稳定性承诺只有经过非作者之手的真实使用才算被验证。**
作者自己的 dogfooding 会系统性地避开自己没想到的用法。v0.9 的验收主体是外部 beta 用户：不同 OS、不同 provider 重度使用者、不同规模的日志库。工具没有遥测（离线原则是绝对的），所以反馈通道必须显式设计——用户主动导出、亲眼审查后再发送的诊断包，而不是任何自动上报。

**推论 8：成本准确性是本产品的可证伪核心命题，1.0 前必须公开证据。**
"看清真实消耗"如果误差不可知，就退化为 README 里批判的"看似精确的幻觉数字"。v0.3 的验收（单日误差 < 5%）是抽查；1.0 需要的是跨用户、跨 provider、30 天窗口的系统性审计，且**方法论和结果随 1.0 公开**——让用户能自己复现审计，而不是相信一句声明。

**推论 9：格式漂移是确定会发生的未来，必须在冻结前装上观测哨。**
Codex 和 Claude Code 的日志格式每隔几周就会变。冻结后的 1.x 不能靠频繁改 reader 追格式；它需要的是在格式漂移发生时**如实报告失真程度**的能力——未知事件类型计数、未知字段计数进 scan 输出和 doctor。"坏日志不阻塞好日志"的成熟形态是"变化的日志不静默污染统计"。

---

## 1. v0.9 — Feature Freeze & External Validation

### 1.1 目标

1. 冻结前最后三件安全设备：**格式漂移观测哨**、**support bundle**、**fixture 脱敏工具**；
2. 进入 feature freeze：此后到 1.0 只收 bugfix / docs / test；
3. 对 v0.8 全部 experimental 项做出裁决（毕业 / 砍掉 / 带着 experimental 标记进 1.x）；
4. 外部 beta 计划与成本准确性审计；
5. 执行弃用清除，定格最终 CLI 面；
6. 定义并执行 1.0 的 bug bar。

### 1.2 冻结前的三件安全设备

**（a）格式漂移观测哨（推论 9）**

- reader contract 增补（对插件是新增可选能力，对内置 reader 是必做）：`ParsedRecords` 增加 `unknown_event_types: Counter[str]`、`unrecognized_field_ratio: float`（采样估算即可）；
- 派生表 `format_observations(provider, observation_key, count, first_seen_at, last_seen_at)`，scan 时累积；
- 出口：`scan` 末尾输出一行 `format drift: codex 2 unknown event type(s)`；doctor 增加 "Format drift" 小节；当未知事件占比超过阈值（默认 5%）时，日报 overview 追加 warning 行 `token统计可能不完整：<provider> 存在未识别事件`——统计失真必须对用户可见，这是"可信"的最后一道防线；
- conformance 套件同步增加第 7 项：向 fixture 注入未知类型事件，reader 必须计数而非崩溃或静默丢弃。

**（b）Support bundle（无遥测前提下的反馈通道）**

```bash
aicg support bundle --out bundle.json [--include-config]
```

- 内容：版本号、schema 版本、doctor --self-check 报告、format_observations、各表行数、性能计时（最近一次 scan/summary 耗时）、去 salt 的匿名化统计（token 分布分位数、provider 占比）；
- **强制审查步骤**：生成后打印全文摘要并提示"请人工检查后再分享"；`--include-config` 才包含 config（价格表可能被视为敏感），默认不含；
- 硬约束：bundle 生成路径复用 `--redact-paths` + secret pattern 自检，self_check 新增 `bundle.no_raw_leak` 检查项；bundle 里的所有 hash 已经过 v0.8 加盐，跨机器不可关联；
- 明确边界：工具永不发送 bundle，发送动作 100% 由用户完成（邮件/issue 附件）。

**（c）Fixture 脱敏工具（服务 beta 用户与插件作者贡献真实日志）**

```bash
aicg fixture redact <input.jsonl> --out <fixture.jsonl> [--keep-structure]
```

- 对 JSONL 逐事件处理：内容型字段（message/content/output/command/arguments...）替换为等长占位符或保留前 N 字符 + 长度标记（`--keep-structure` 保留字段结构与字节量级，保证 fixture 仍能触发 RAW_PAYLOAD/输出字节类逻辑）；路径做别名化（`/Users/x/proj-a` → `/REDACTED/project-1`，同名同映射保持关联性）；secret pattern 强制替换；
- 输出末尾跑一遍验证：redacted 产物再过全部 secret/sensitive 检测器，有命中则拒绝输出并报告位置；
- 这是把 v0.3 "有真实 fixture 才有 reader" 准入制的贡献成本降到可行的关键工具——没有它，"第三方贡献 provider reader" 在隐私上不成立。

### 1.3 Feature freeze 纪律与 experimental 裁决

freeze 起点：上述三件合入之日。此后到 1.0 的 PR 分类白名单：bugfix（需附回归测试）、docs、test/fixture、性能修复（不改行为）。任何行为变更需要 maintainer 显式豁免并记录原因。

**Experimental 裁决表**（v0.9 必须逐项给出结论，附录 B 为模板）：

| 项 | 来源 | 裁决选项 |
| --- | --- | --- |
| `capture`（流式脱敏后） | v0.4 | 达到 2.7#6 验收 → 毕业 stable；否则从 1.0 移除（不是带病冻结） |
| Windows 支持 | v0.8 | CI 绿 → 毕业；否则 experimental 进 1.x，文档明示 |
| `--allow-unverified` 插件加载 | v0.8 | 保留但永久 experimental（信任模型上它本来就是逃生门） |
| usage import 各 provider 方言 | v0.3 | 有真实用户使用证据的方言毕业，其余 experimental |
| review LLM 辅助 | v0.6 non-goal | 维持不进 1.0 |

裁决原则：**1.0 里不存在"默认开启的 experimental"**；毕业的标准是验收测试 + beta 期间的真实使用证据，二者缺一即不毕业。

### 1.4 外部 Beta 计划

- 目标画像至少覆盖：macOS + Linux；Codex 重度 / Claude Code 重度 / 双栖；日志库规模小（<1k sessions）与大（>20k sessions）；至少一名非作者插件开发者（v0.8 出口 gate 的延续）；建议 5–10 人；
- 入口：`pip install aicg==0.9.*`，beta 指引文档一页（安装 → init → scan → summary → 遇到问题 `support bundle`）；
- 反馈闭环：GitHub issue 模板内嵌 bundle 附件位 + "已人工审查 bundle 内容" 勾选项；
- beta 期间收集三类证据：崩溃/错误（bug bar 输入）、准确性数据（1.5 输入）、experimental 使用证据（1.3 输入）。

### 1.5 成本准确性审计（推论 8）

- 方法论文档 `docs/accuracy-audit.md`：审计窗口 30 天；对照对象 = provider 官方用量页（Codex/OpenAI usage、Claude 用量账单）；对齐口径 = canonical token 桶 + 本地价格表；差异分解模板（保留期截断 / 未识别事件 / provider 端未入账 / 定价表滞后）；
- 执行：作者 + ≥3 名 beta 用户各自跑一轮，`aicg export` 汇总（脱敏），结果写入审计报告；
- **发布标准**：30 天窗口 token 总量偏差中位数 ≤ 5%，且每个超差案例都有归因（归因到上表四类之一）。达不到 → 1.0 延期，这是硬 gate；
- 审计报告随 1.0 发布（`docs/accuracy-report-1.0.md`），含复现步骤——用户可用同样方法审计自己的安装。

### 1.6 弃用清除与最终 CLI 面

- v0.8 标记 deprecated 的项（如 `reasoning_output_per_mtok_usd` 价格字段、旧 flag 别名）在 v0.9 移除（满足"警告一个 minor 版本"政策）；
- 定格 `docs/stability.md` 的 CLI 清单为 1.0 候选终稿；stability 快照测试从"变更报警"升格为"变更即 CI 失败，豁免需修改测试并在 PR 说明"；
- `aicg --version` 输出增加 schema 版本与 ruleset 版本（支持排障时一眼定位）。

### 1.7 文档完成度

1.0 文档集终稿（v0.8 已有骨架，v0.9 补完并全部纳入 docs-as-tests）：

- User guide（安装 → 日常工作流 → 报表解读逐板块说明——特别是"estimated 与 unavailable 的含义"、"interrupted 不计入失败率"这类口径说明，报表可信的前提是读者理解口径）；
- FAQ / Troubleshooting（database locked、rebuild 时机、格式漂移警告怎么处理、准确性偏差自查流程）；
- 全部文档中的 CLI 示例进冒烟测试；README 精简为入口 + 指向 docs/（当前 README 的 roadmap 章节替换为指向三份 spec）。

### 1.8 Schema v9

- 新表 `format_observations`（派生表，rebuild 可重建）；
- 无其他 schema 变更——freeze 期间 schema 冻结在 v9 直到 1.0（1.0 不引入 schema 变更，v9 即 1.0 schema）。

### 1.9 Bug bar 与 v0.9 验收标准

**Bug bar（1.0 发布阻塞标准）**：

- P0（阻塞）：崩溃、数据错误（统计数字错）、隐私泄漏（任何 raw 内容出现在任何输出面）、DB 损坏；
- P1（阻塞）：错误的诊断结论（review 规则在真实日志上系统性误报）、性能超预算 2×、迁移失败；
- P2（不阻塞，进 1.x）：显示瑕疵、非核心路径的边缘 case。

**验收**：

1. 格式漂移 fixture：注入 10% 未知事件 → scan 正常完成、日报出现失真警告、format_observations 正确计数；
2. support bundle 在植入密钥/敏感路径的环境生成 → 自检拦截或产物零泄漏；
3. `fixture redact` 对 3 个真实（含敏感内容的）日志样本：产物过全部检测器零命中，且仍能驱动对应 reader 的 conformance 用例；
4. beta：≥5 名外部用户完成 ≥2 周使用，P0/P1 清零；
5. 准确性审计达标（1.5 的硬 gate）；
6. experimental 裁决表无空行，每项结论有证据链接；
7. stability 快照测试连续 4 周无非豁免变更。

### 1.10 v0.9 Non-goals

- 不新增任何用户可见功能（三件安全设备合入后立即 freeze）；
- 报表 i18n（`--lang zh` 等）推迟到 1.x——它是纯增量、不破坏冻结面，不值得为它推迟 1.0；
- 不做自动更新检查（联网，永久 non-goal）；
- 不为追新 provider 格式打破 freeze——格式漂移由观测哨如实报告，reader 适配进 1.x。

---

## 2. v1.0 — Stable Release（契约的签署）

### 2.1 目标

1.0 交付四样东西：**semver 语义契约**、**支持与安全响应政策**、**治理规则**、**发布本身**。没有新代码（除 bugfix），发布的实质是把 v0.8–v0.9 验证过的东西盖上"以后怎么变"的规则。

### 2.2 Semver 语义映射

`docs/stability.md` 终稿，对三个冻结面分别定义版本语义：

| 面 | MAJOR（破坏） | MINOR（兼容新增） | PATCH |
| --- | --- | --- | --- |
| CLI | 移除/改义 stable 命令、flag、exit code、机器可读输出结构 | 新命令/新 flag/新输出字段 | 行为修复 |
| 数据 | DB schema 需 rebuild 的变更、report/export JSON schema 字段移除或改义 | 新表/新列/新 JSON 字段（追加式） | 无结构变化 |
| 插件 | ProviderReader/Capabilities/ParsedRecords 破坏性变更、conformance 语义收紧导致既有合格插件不合格 | 新可选 protocol 方法、新 capabilities 字段（默认值向后兼容） | — |

补充规则：

- **数据面的特殊性**：即使 MINOR 的追加式 schema 变更也走 rebuild（v0.3 定下的机制不变），但 rebuild 在 MINOR 内必须无损（用户态表 + 派生数据语义不变）；MAJOR 才允许语义变化；
- deprecation 政策延续：stable 项移除前至少一个 MINOR 版本的 DeprecationWarning；
- 版本号载体：CLI `--version`、report JSON `schemaVersion` 族、schema_meta，三处一致性进 self_check。

### 2.3 支持与安全响应政策

- `SECURITY.md`：私密披露渠道（安全邮箱）、响应承诺（72h 确认、90 天协调披露）、范围声明（本工具无网络面，安全问题主要类别 = 隐私泄漏 / 解析崩溃 / 报表注入——引用 threat model）；隐私泄漏类比照 P0 处理并出 patch 版本；
- 支持窗口：1.x 主线持续维护；出现 2.0 后，1.x 安全修复维持 12 个月；
- Python 版本政策：跟随上游 EOL，弃支持某 Python 版本 = MINOR 版本事件并提前一个版本公告。

### 2.4 治理与贡献规则

- `CONTRIBUTING.md`：PR 分类要求（行为变更必须先有 issue 讨论 + stability 面影响声明）、fixture 贡献规则（必须真实日志 + 必须经 `aicg fixture redact` + CI 自动跑泄漏检测拒绝未脱敏 fixture）、新 reader 准入流程（引用 plugin guide + conformance）；
- 决策记录：`docs/adr/` 目录收录既有关键决策（canonical token 语义、rebuild 迁移模型、零依赖政策、无遥测原则、SVG over JS 图表），此后破坏性提议必须先提 ADR——把三份 spec 里的第一性推论沉淀为可引用的治理文件；
- 发布节奏声明：无固定节奏，按语义发版；每个 MINOR 附迁移说明（哪怕内容是"无需操作"）。

### 2.5 发布工程

- `aicg==1.0.0` 发布 PyPI：wheel + sdist + SHA256SUMS；git tag 签名；
- Release notes：v0.1→1.0 演进摘要（各版本一句话）+ 1.0 契约要点 + 准确性审计结果链接；
- `docs/migration-guide.md` 终稿：任意 0.x → 1.0 的统一路径（`pip install -U aicg && aicg rebuild`），各版本行为变化速查表；
- README 终稿：Current Capabilities 更新为 1.0 全量、roadmap 章节替换为"1.x 主题 + 指向 specs/ADR"。

### 2.6 1.x 主题种子（非承诺，防止 1.0 后失焦）

按需求证据排序的候选池，不承诺顺序：

1. 新 provider reader（Cursor/Zed/本地模型——继续 fixture 准入制，多数应以第三方插件形态存在）；
2. 报表 i18n（zh 优先）；
3. policy 规则包（可分享的 policy.toml 预设，纯文件分发，无市场）；
4. dashboard 增强（多周期钻取、review 诊断下钻）；
5. `accepted_lines` / `human_review_time` 的本地可测来源探索（编辑器插件日志？仍需满足离线 + 只读）。

### 2.7 v1.0 验收标准

1. v0.9 bug bar 清零且保持两周；
2. 三份契约文档（stability / SECURITY / CONTRIBUTING）齐备，semver 映射表覆盖三个冻结面无遗漏；
3. 准确性审计报告随发布公开且含复现步骤；
4. 从 v0.5 与 v0.9 两个起点的真实 DB 各完成一次升级演练（rebuild + 用户态表保留验证）；
5. 一名外部贡献者按 CONTRIBUTING 完整走通一次 PR（fixture 或 bugfix）；
6. `pip install aicg==1.0.0` 在干净 macOS/Linux 环境冒烟全绿；
7. 发布后 48h 内无 P0 报告（发布检查点，出现则 1.0.1 流程演练随即启动）。

### 2.8 v1.0 Non-goals（永久性声明随 1.0 固化）

以下不是"还没做"，是随 1.0 写入文档的永久边界（改变它们需要 2.0 级别的重新论证）：

- 云端 SaaS / 任何默认联网行为 / 遥测；
- 保存或转述 raw prompt/code/output；
- 自动修改 agent 配置、自动删除缓存；
- 内置通知网络通道（邮件/webhook）；
- LLM 参与的默认诊断路径。

---

## 3. 交付顺序

```
v0.9  三件安全设备（drift 观测哨 → fixture redact → support bundle）
      → FREEZE 生效 → beta 招募启动 → 弃用清除 + CLI 定格
      → 30 天准确性审计（与 beta 并行）→ experimental 裁决
      → 文档终稿 + docs-as-tests → bug bar 清零
v1.0  semver 映射 + SECURITY + CONTRIBUTING + ADR 沉淀
      → 升级演练（v0.5/v0.9 起点）→ release notes / migration 终稿
      → 1.0.0 发布 → 48h 观察 + 1.0.1 流程就绪
```

关键路径是 **30 天准确性审计**：它决定 v0.9 的最短历时（freeze 后至少 30 天 + 归因分析时间），也是唯一可能使 1.0 延期的硬 gate。beta 招募应在三件安全设备合入后立即启动，让审计窗口与 beta 期完全重叠。

### 里程碑 gate

- v0.9 出口 gate = 1.9 验收全绿（其中 #4 beta、#5 审计为硬项）；
- v1.0 出口 gate = 2.7 全绿；任何 P0 在发布窗口出现，重置 48h 观察期。

---

## 附录 A：README Phase 7 承诺 → 最终落点

| README 承诺 | 落点 |
| --- | --- |
| 发布 pip package | v0.8 构建 / v1.0 正式发布（2.5） |
| 稳定 CLI flags | v0.8 清单 / v0.9 定格（1.6）/ v1.0 semver 契约（2.2） |
| 稳定 normalized event schema | v0.3 语义 / v0.8 schemas 文件 / v1.0 契约（2.2） |
| 稳定 report JSON schema | 同上 |
| provider plugin guide | v0.8（spec v0.6-v0.8 §3.3）/ v1.0 治理接入（2.4） |
| example configs | v0.8 examples/ / v0.9 docs-as-tests 覆盖 |
| threat model 文档 | v0.8 / v1.0 SECURITY.md 引用（2.3） |
| migration guide | v0.8 初稿 / v1.0 终稿（2.5） |
| 用户放心放进本地工作流 | 准确性审计公开（1.5/2.7#3）+ 永久 non-goals 固化（2.8） |
| 第三方贡献 reader 无需理解全部内部实现 | conformance + plugin guide + fixture redact 工具（1.2c）+ CONTRIBUTING（2.4） |

## 附录 B：Experimental 裁决表模板

| 项 | 引入版本 | beta 使用证据 | 验收状态 | 裁决（毕业/移除/experimental 进 1.x） | 决策记录链接 |
| --- | --- | --- | --- | --- | --- |
| capture | v0.2（v0.4 脱敏改造） | | | | |
| Windows 支持 | v0.8 | | | | |
| --allow-unverified | v0.8 | | | | |
| usage import 方言：openrouter | v0.3 | | | | |
| （补充至无遗漏） | | | | | |
