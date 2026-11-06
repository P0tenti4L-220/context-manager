# F07 证据核验与诊断结论

依赖 F03–F06。所属组件为 Diagnostic Policy。本规格为 F07 设计组细化稿 `r1`（`2026-11-04`），公共字段与枚举以 [公共契约](../contracts.md) 为准；与契约冲突时先改契约，再改本规格。

## 用户结果

工具把静态发现与 Agent 候选统一成可复核的诊断，明确当前规则问题、历史行为观察和可能成因，并给出对应的处理方向。每条诊断都能回答四件事：声明是什么、依据哪段原文、还缺什么、下一步应改规则还是改加载或检索。

## 职责边界

F07 负责：

- 校验候选的结构、来源、作用域、时间和引用，拒绝无效候选并保留原因。
- 用 F04 的规则/决策、F05 的事件/回执/违规统计重新过门槛，生成最终 `Finding`。
- 区分事实声明、因果解释和材料缺口，给出与门槛相称的 `assessment` 和 `suggested_action`。
- 合并重复发现、保留冲突证据，输出逐项校验记录与自身覆盖状态。

F07 不负责：

- 重新扫描项目、重新解析回执原文或重新执行行为检查；这些分别属于 F03、F05 和 F05 的已确认检查。
- 调用本地模型、访问网络或读取本次运行之外的证据。
- 生成或应用修复 Diff（F08）、渲染报告和决定 `RunResult.status`（F09/F10）。
- 修改项目文件、规则登记、事件包或证据存储。`evaluate_findings` 是只读判定入口。
- 把“引用有效”当成“语义正确”，或把模型自述的等级、kind、置信度当成最终结论。

同一输入与同一证据快照必须得到相同 `finding_id`、顺序和结果；不得使用随机 ID 或依赖输入列表顺序。

## 文件与接口

建议新增 `src/context_manager/diagnosis.py`，测试为 `tests/test_f07_diagnosis.py`，合成夹具放在 `tests/fixtures/f07/`。如需拆分内部结构，可增加 `diagnosis_models.py`，但对外入口只保留一个。

```python
def evaluate_findings(
    request: AuditRequest,
    static_candidates: Sequence[StaticCandidate],
    model_candidates: Sequence[ModelCandidate],
    registry: RegistryView,
    incidents: IncidentView,
    receipts: ReceiptView,
    evidence_store: EvidenceStore,
    *,
    inspection=None,
    model_reads=(),
) -> DiagnosisResult: ...
```

`inspection` 是 F03 本次只读视图，用于回查缺失引用等事实；`model_reads` 是 F06 本次调用的实际读取记录。F02 全局读取日志可能含 Inspector 的读取，不能单独证明正文送给了模型。未提供或未覆盖模型引用时拒绝该候选，不继承其他调用的已读状态。

`DiagnosisResult` 至少包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `findings` | `list[Finding]` | 已合并、已排序的最终诊断，可直接交给 F08/F09 |
| `validations` | `list[CandidateValidation]` | 每个输入候选的接收、合并、降级或拒绝记录 |
| `coverage` | `list[CoverageItem]` | F07 自身三类检查的执行状态，供 `checks.json` 使用 |
| `stats` | `dict` | 接收、合并、降级、拒绝数量及按等级计数，便于报告汇总 |

`CandidateValidation` 字段：

```text
candidate_id, origin, status(accepted|merged|downgraded|rejected),
reason_code, finding_ids, checks, model_claimed, notes
```

`model_claimed` 只记录模型自述的 `kind`、`assessment`、`confidence`，用于审计，不参与判定。

### Finding 的附加字段

契约要求的 `Finding` 必填字段全部保留。F07 按契约 §5 输出附加字段，默认值分别为 `fact`、空列表、`false`。

| 字段 | 取值 | 用途 |
| --- | --- | --- |
| `claim_type` | `fact` / `causal_explanation` / `gap` | 区分可支持事实、因果假设和材料缺口 |
| `related_finding_ids` | `list[str]` | 把因果假设或缺口关联到事实发现 |
| `synthetic` | `bool` | 该发现的历史证据是否来自合成回执/事件 |

每个 `Finding.checks` 条目统一为：

```json
{
  "check_id": "load.coverage_complete",
  "status": "passed",
  "reason": "coverage_complete",
  "inputs": ["receipt-demo", "rule-demo"],
  "artifact_refs": []
}
```

`status` 只取 `passed` / `failed` / `not_run`。`inputs` 为证据或登记 ID 数组，其他结构化门槛细节留在验证记录中。

输出示例（事实项与因果假设分别出项）：

```json
{
  "finding_id": "fnd_stale_rule_7c1a2b3d4e5f",
  "kind": "stale_rule",
  "claim": "规则 R1 在 clients/ 内要求 /api/v1/users，已被 D1 在 2026-10-01 起替代。",
  "scope": {"include_prefixes": ["clients"], "exclude_prefixes": ["legacy"], "harnesses": ["shared"]},
  "assessment": "supported",
  "basis": "confirmed_interpretation",
  "support_refs": [{"evidence_id": "ev_agents", "sha256": "…", "line_start": 3, "line_end": 3}],
  "counter_refs": [{"evidence_id": "ev_agents", "sha256": "…", "line_start": 4, "line_end": 4}],
  "missing_evidence": [],
  "suggested_action": "update",
  "checks": [{"check_id": "decision.effective", "status": "passed", "reason": "effective_at_as_of", "inputs": ["D1"], "artifact_refs": []}],
  "related_rule_ids": ["R1"],
  "related_event_ids": [],
  "claim_type": "fact",
  "related_finding_ids": ["fnd_stale_rule_8d2b3c4e5f60"],
  "synthetic": false
}
```

对应的因果项使用同一 `kind`，但 `claim_type=causal_explanation`、`assessment=hypothesis`、`basis=model_candidate`、`suggested_action=keep`，并通过 `related_finding_ids` 指回事实项；不得复制事实项的 `supported`。

## 输入

### 1. AuditRequest

使用契约中的 `AuditRequest`。F07 额外依赖以下语义：

- `as_of` 是当前体检的判定时点；历史事件使用各自的 `occurred_at`。两者不得混用。
- `task_scope` 规范化后是发现范围的硬边界；越界候选一律拒绝。
- `semantic_mode=off` 时不得产生 `cleanup_candidate` 或因果假设；静态候选仍照常判定。若调用方仍传入 `model_candidates`，逐项拒绝并记 `semantic_disabled`，不得静默忽略。
- `run_id` 用于校验所有证据引用属于本次运行。

### 2. Scope 规范形

F03/F04/F05 传入的范围可能是列表或对象。F07 统一规范化为：

```json
{
  "include_prefixes": ["clients"],
  "exclude_prefixes": ["legacy"],
  "harnesses": ["codex", "claude_code"]
}
```

规则：

- 相对 POSIX 路径，按路径组件比较；`clients` 与 `clients-old` 不重叠。
- `["."]` 表示项目全部文件。拒绝绝对路径、`..` 越界和空组件。
- `harnesses` 为空表示未限定；`shared` 匹配任意工具；不同具体工具分别保留。
- 条件不放进 `scope`，留在 Constraint 的 `conditions` 中比较。
- 范围交集取 include 交集并减去 exclude；交集为空则不产生该范围的诊断。

内部判定统一使用上述规范形。运行内和对外序列化均使用公共契约的 Scope。事件包的前缀列表由 F05 先归一化。

### 3. StaticCandidate

由 F03/F04/F05 生成。F07 不信任其 `kind`、`assessment` 或结论，只把 ID、事实和引用作为待核验输入。

```json
{
  "candidate_id": "rules:stale:R1:D1",
  "origin": "inspector|rules|incidents",
  "kind": "stale_rule",
  "claim": "候选声明原文",
  "scope": {"include_prefixes": ["clients"], "exclude_prefixes": ["legacy"]},
  "facts": {},
  "support_refs": [],
  "counter_refs": [],
  "missing_evidence": [],
  "suggested_action": "update"
}
```

`facts` 按 kind 至少提供：

| kind | 必需事实 |
| --- | --- |
| `stale_rule` | `rule_id`、`decision_id`、`subject`；可选 `clause_id`、`covered_clause_ids`、`remaining_obligations` |
| `rule_conflict` | `left_id`、`right_id`、`subject`（ID 可为 rule 或 decision） |
| `policy_mismatch` | `left_rule_id`、`right_rule_id`、`left_harness`、`right_harness`、`subject` |
| `dead_reference` | `reference_kind`（`import`/`markdown_link`/`command`）、`target`、`target_state`、`scan_complete`；命令另附 `command_kind`、`declared_targets` |
| `missing_load` | `event_id`、`rule_id` |
| `loaded_violation` | `event_id`、`rule_id`、`check_id` |
| `stale_code_evidence` | `event_id`、`observed_ref`、`required_ref`、`requirement_source_ref` |
| `recurrence` | `constraint_id`、`group_key`、`event_ids`、`independent_task_ids` |
| `cleanup_candidate` | 无机械事实；只能来自模型或 F04 的语义建议 |
| `insufficient_evidence` | `gap_key`、`missing_items`、`related_rule_ids` / `related_event_ids` 中的至少一项 |

`facts` 只放 ID 和已算好的结构化结果，不放待判定结论。F07 必须复核这些 ID 在 registry/incidents/receipts 中仍存在，并复核可机械重算的门槛；只能引用、不能重算的上游事实（如 F03 的扫描覆盖）必须带来源和覆盖范围，F07 检查其绑定，不接受无来源的布尔值。

### 4. ModelCandidate

使用契约 §4 的候选字段：`candidate_id`、`kind`、`claim`、`scope`、`support_refs`、`counter_refs`、`missing_evidence`、`suggested_action`、可选 `edit_intent`。另可带 `model_confidence`，仅归档。

模型候选没有 `facts`。除 `cleanup_candidate` 和语义型 `stale_rule`/`rule_conflict`/`policy_mismatch` 外，模型不能凭自身声明生成加载、违规、复发或版本结论。

### 5. 上游视图

F07 需要下列只读能力；上游实现名称可以不同，由 F10/适配层接入，但语义不得变：

| 来源 | 需要的接口 | 用途 |
| --- | --- | --- |
| F02 `EvidenceStore` | `run_id`；`validate_ref(ref, require_covered=...)`、`get(evidence_id)`；确需文本时用受限 `read_evidence` | 校验引用属于本次运行、摘要和行段可读 |
| F03 Inspector | `InspectionResult.references/imports/commands` 和扫描覆盖，由 F10 适配候选 | `dead_reference` 的事实来源 |
| F04 Registry | 规则、决策、确认、约束、行为检查；`rule(id)`、`decision(id)`、`effective_decisions(subject, scope, at)`、`supersession(rule_or_clause, at)`、`compare_constraint_pair(a, b)`、`scope_relation(a, b)`、`clauses(rule_id)` | 过时、冲突、兼容例外与替代链 |
| F05 Incidents | `IncidentView.by_id`、`violations`、`recurrence_groups`、`load_observation(event_id, rule_id)`、`requested_event_gaps` | 加载证明、实际违规、复发与缺口 |
| F05 Receipts | `receipt(id)`、`observed_entries`、`coverage`、`channels`、`order_verified`、`provenance` | 回执绑定与排除证明 |
| F06 Analyst | `AnalysisResult.candidates`、`termination`、`reads` | 固定候选注入；F07 不关心传输层 |

F04 的 `compare_constraint_pair` 返回：`compatible` / `incompatible` / `unresolved`，并附冲突的 subject、值和原因。F05 的 `load_observation` 由 F10 按 F05 的证明回执和 `proof_checks` 机械投影，包含：`state`（`discovered`/`expected`/`observed_loaded`/`observed_not_loaded`/`unknown`）、`receipt_id`、`coverage`、`channels_complete`、`entry_present`、`text_sha_matches`、`same_session`、`same_harness`、`same_epoch`、`before_action`、`order_verified`、`provenance`。

## 校验顺序

1. **结构**：必填字段、类型、枚举、非空 `claim`、合法 scope、引用列表元素形状。字段错误只拒绝该候选，不抛到调用方。
2. **来源**：`origin` 与 `kind` 是否匹配。`dead_reference` 只能来自 inspector；`missing_load`、`loaded_violation`、`recurrence`、`stale_code_evidence` 的加载/违规/版本事实只能来自 incidents；模型来源的加载类候选直接拒绝。
3. **范围**：规范化 scope，检查是否与 `request.task_scope` 相交；`include_prefixes` 为空、绝对路径、`..` 越界均拒绝。
4. **引用**：逐个解析 `support_refs` / `counter_refs`，校验 `evidence_id` 属于当前 `run_id`、`sha256` 匹配、行号从 1 开始且两端包含、行段可读。任一 support ref 失败则拒绝；counter ref 失败则拒绝并保留原因，避免把无法核对的“反证”当成反证。`supported` / `hypothesis` 至少需要一条可解析的 support ref；`insufficient_evidence` 可无引用，但必须带相关规则/事件 ID 和具体缺口。
5. **对象与时间**：规则、决策、约束、事件、回执、行为检查 ID 必须存在；时间必须带时区；按判定时点取有效决策；历史事件不得套用当前规则版本。
6. **解释与条件**：规则解释必须是 `structured_confirmed` 或带有效 `confirmation_ref` 的确认解释；`unresolved` 条件不得进入确定性判定；草稿、未来生效、已撤回决策不得支撑替代或冲突结论。
7. **门槛**：按下方判定表逐项检查，记录每个 check 的结果。
8. **动作与断言**：按动作守则降级不允许的动作；生成规范 claim 和 finding。
9. **合并与排序**：去重、合并引用、保留冲突，分配确定性 ID 和顺序。

### 拒绝、降级与缺口

| 情形 | 处理 | 是否产生 Finding |
| --- | --- | --- |
| 结构错误、未知 kind、非法 action、重复 candidate_id、越界 scope | 拒绝，记 `rejected` | 否 |
| support ref 跨运行、摘要不符、行号越界、无法读取 | 拒绝，记 `rejected` | 否 |
| 决策为 draft/future/withdrawn，或替代链有环/悬空 | 拒绝该确定性结论，记原因；不生成 supported | 否 |
| 解释未确认、条件不可机械比较 | 降级为 `hypothesis`，动作 `ask_user` | 是 |
| 加载覆盖不完整、顺序不明、回执缺失 | 保留该 kind，`assessment=unknown`，列缺口 | 是 |
| 核心锚点（事件时间、规则版本、任务范围、动作）缺失 | 生成 `insufficient_evidence`，`assessment=unknown` | 是 |
| 模型给出自信但无有效引用的结论 | 拒绝；有有效引用但缺确认则 `hypothesis` | 视情况 |
| 模型声明加载/未加载或实际违规 | 拒绝，`model_cannot_establish_load` / `model_cannot_establish_violation` | 否 |

拒绝记录必须写入 `validations`，不得静默丢弃。拒绝候选的 checks 仍保留，供审查者查看检查在哪一步失败。

## 判定表

下表是最终门槛。`at` 表示当前体检的 `as_of` 或历史事件的 `occurred_at`；历史判定使用事件时点的规则版本。

| kind | 支持条件 | assessment / basis | 建议动作 |
| --- | --- | --- | --- |
| `stale_rule` | 规则解释已确认；决策 `accepted` 且在 `at` 生效；`supersedes` 明确覆盖该规则或子条款；范围相交且替代范围覆盖冲突部分；约束机械冲突或明确退役；替代链完整；例外不覆盖冲突部分 | `supported` / `confirmed_interpretation` | 按残余义务选 `update` / `delete` / `narrow_scope` |
| `rule_conflict` | 两条已确认约束同 subject；范围相交；条件可同时满足；值不可同时满足；无替代关系或权威决策；二者在 `at` 均有效 | `supported` / `confirmed_interpretation` | `ask_user`；有明确可分离范围时 `narrow_scope` |
| `policy_mismatch` | 两个不同 harness（或 shared 与具体工具）的已确认规则在相同任务范围对同一 subject 要求不同；无证据证明二者在同一动作前共同加载；共享决策未明确按工具分别授权 | `supported` / `confirmed_interpretation` | `ask_user`；可分离范围时 `narrow_scope` |
| `dead_reference` | 实际导入、Markdown 本地链接或静态命令引用；目标确实缺失且扫描覆盖完整；不是代码围栏示例、API 字面量或动态命令 | `supported` / `deterministic` | `ask_user`；有已验证替代目标时 `update` |
| `missing_load` | 规则在 `at` 应适用且解释已确认；回执绑定同一 session/harness/epoch、在动作前、顺序已验证；coverage 为 `complete`；覆盖全部可能携带规则的渠道；清单中无该规则文本；规则快照摘要匹配 | `supported` / `deterministic` | `fix_loading` |
| `loaded_violation` | 正确版本规则在动作前已加载且文本/适用上下文匹配；已确认 BehaviorCheck 对事件求值为失败；输入摘要匹配；事件在规则范围内且不在例外内 | `supported` / `deterministic` | `add_check` |
| `stale_code_evidence` | 事件引用的代码快照与任务要求的版本均可核对且不同；任务要求来自确认材料而非模型推断；差异影响该动作 | `supported` / `deterministic` | `fix_retrieval` |
| `recurrence` | 同一 `project_id + constraint_id + task_scope + violation_kind` 分组；至少两个独立 `task_id`；每个计入事件都有已确认且适用的实际违规；去重后无例外/提及/搜索/修复删除 | `supported` / `deterministic` | `add_check`；无检查配置时 `ask_user` |
| `cleanup_candidate` | 语义建议有可解析的规则/行段引用；不把语义建议当删除依据；保留独立义务和兼容例外 | 最高 `hypothesis` / `model_candidate` | `keep` / `relocate` / `ask_user` |
| `insufficient_evidence` | 候选结构有效且属于请求范围；至少一项关键材料缺失且缺口可具体列出；不是引用无效或越界 | `unknown` / `deterministic` 或 `model_candidate` | `ask_user` |

### stale_rule 细节

- 决策必须由 `confirmation_ref` 或用户显式登记确认；仅文本里写了 `accepted` 的候选不满足门槛。
- `Decision.supersedes` 必须显式包含规则 ID 或子条款 ID；日期更晚不建立替代。
- 到 `at` 为止应使用替代链的终端有效决策；若存在多个互不隶属的终端决策且结论不同，不产生 supported `stale_rule`，改记冲突或缺口。
- 替代只在新决策自身范围内生效。范围外的旧规则、`legacy/` 兼容目录和独立测试义务必须保留。
- 动作选择：
  - `delete`：旧规则的全部义务已被完整替代，且没有独立义务、兼容例外或未覆盖范围；必须由 F04 的 `covered_clause_ids` / `remaining_obligations` 支撑。
  - `update`：只替代部分义务，仍需改写保留部分；或旧规则含测试、兼容等独立义务。
  - `narrow_scope`：冲突只发生在旧规则范围的一部分，且可安全缩小到不冲突范围。
- 残余义务未知时只能给 `update` 或 `narrow_scope`，不得给 `delete`。
- 若同一规则被多个决策部分替代，按范围/子条款分别出项；不得把多个部分替代拼成“全量删除”。

### rule_conflict 与 policy_mismatch 的区分

- 同一 harness、同一动作上下文内两条约束不能同时满足：`rule_conflict`。
- 不同 harness 各自适用、共同加载未确认：`policy_mismatch`。它只声明“规则要求不同且共同加载未确认”，不声明互相干扰，也不选边。
- 若确认两个不同 harness 的规则在同一 session 动作前都已加载，按同一动作冲突处理：进入 `rule_conflict`，或在该动作确有违规时进入 `loaded_violation`。
- 若共享决策已明确按工具分别规定行为，则不是冲突；记 `resolved_by_shared_decision`，不产生 `policy_mismatch`。
- 条件不可同时满足不是冲突；应记录为条件分离，不产生 finding。
- 已接受决策彼此矛盾且无替代关系时输出 `rule_conflict` 和 `ask_user`，禁止自动选权威。

### 加载与违规细节

- `missing_load` 的“完整排除证明”必须同时满足：覆盖渠道完整、同一 session/harness/epoch、动作前、顺序已验证、规则快照摘要匹配。缺一项即为 `unknown`，不得降级为“未加载”。
- `observed_loaded` 只证明规则文本进入了上下文，不证明模型理解、遵循或因果。因果解释单独记为 `hypothesis`。
- `loaded_violation` 要求已确认 `BehaviorCheck` 的失败结果；`not_run`、未知、输入摘要不匹配或例外适用时不得支持。
- 只有 `python_assignment_literal` 与 `structured_check_result` 的已确认检查参与机械判定；diff 中出现字样、动作自述、模型正则都只是线索。
- 回执来源不是 synthetic 的首版合成案例：加载类结论保持 `unknown`，原因 `unsupported_provenance`；不得把真实来源混入合成验收。

### 版本与时间细节

- 当前体检：规则取 registry 当前确认版本，决策按 `[effective_from, effective_until)` 在 `as_of` 求值。
- 历史诊断：规则取事件时点可核对的历史快照；若只有当前规则且摘要不同，加载/过时结论为 `unknown`，不得用当前文本反推当时加载。
- 事件早于决策生效时间：该事件不产生 `stale_rule`；当前 `as_of` 的体检仍可单独产生当前规则过时项。
- `stale_code_evidence` 必须同时拿到实际使用的代码快照和任务要求版本；只有一侧时输出 `unknown` 和 `ask_user`。
- 合成事件的可选 `code_version` 采用 F05 绑定后的完整快照。实际快照须与动作的编辑前快照或工具结果相同。已确认且在事件时点生效的 `code_snapshot_sha256` 约束须绑定同一路径与要求摘要。两个摘要不一致时报告 `fix_retrieval`，没有可用或一致的权威版本时列出缺口。
- 文件修改时间、Git 提交时间只用于追踪，不用于建立替代或权威。

### 复发细节

- 先按原始事件 ID 和稳定内容签名去重，再按 F05 的分组键统计。
- 同一 `task_id` 的重试、复制日志、重复导入只算一个任务；不同 session 可以是同一任务。
- 提及、搜索旧 API、撤回说明、修复时删除旧行、`legacy/` 中的合法调用都不增加违规计数。
- 只有一个独立任务时，不输出 supported `recurrence`；可输出 `unknown` 的缺口说明已确认任务数和动作数。
- 复发事实与“规则导致复发”的因果解释分开出项；前者可 supported，后者只能是 `hypothesis`。

### cleanup_candidate 细节

- 只来自语义模式或 F04 的语义建议；引用必须指向具体行段。
- 冗余、进度材料、规则迁移都属于语义判断，v1 不因模型自信而升级为 supported。
- `relocate` 将 F06 的迁移理由附到候选声明后，供报告和 F08 的 `migrate_clause` 建议引用。无决策体检的声明保留快照中的任务名称或编号、实际状态和一次性下一步。
- 建议动作只允许 `keep`、`relocate`、`ask_user`；不得直接 `delete`/`update`，删除与改写交给 F04/F08 的确认流程。
- 必须保留独立义务、兼容例外和来源；反证可见。

## 声明拆分与动作守则

一个“问题”可以产生多个 Finding，每个只声明一件事：

| 声明类型 | 示例 | 允许等级 |
| --- | --- | --- |
| 事实 | 规则 R1 已被 D1 替代；R1 在事件 e1 前未加载；检查 BC1 判定违规 | `supported` / `unknown` |
| 因果解释 | 旧规则 R1 可能影响 e1 的行为 | 只能 `hypothesis` |
| 缺口 | 缺少 e2 的动作前回执，无法判断根因 | `unknown` |

- `supported` 只给该声明自身达到门槛的事实；不得把“规则当前过时”的 supported 继承给“旧规则导致错误”的声明。
- 即使规则已加载且行为违规，也只能支持“已加载”和“违规”两个事实；因果关系仍为 `hypothesis`，除非将来有隔离重放证据。
- 动作守则：`hypothesis` / `unknown` 只能配 `keep` 或 `ask_user`；除 `cleanup_candidate` 的 `relocate` 外，不得给 `update`、`delete`、`narrow_scope`、`fix_loading`、`add_check`、`fix_retrieval`。
- 模型给出的 `suggested_action` 仅作参考；与门槛不符时由 F07 覆盖并记 `action_guard` 检查。

## 去重、ID 与排序

- **合并键**：`kind + assessment + 规范化 scope + 事实键 + 规范化动作`。事实键由规则/决策/事件/检查 ID 构成，不使用自由文本。
- 相同合并键：保留最早候选为规范项，合并 `support_refs`、`counter_refs`、`missing_evidence`、`related_*` 并去重；合并后的 `checks` 按 `check_id + inputs` 去重。`basis` 取证据强度最高者：`deterministic` > `confirmed_interpretation` > `model_candidate`。
- 不同 assessment 的同一问题不合并；分别保留并互相写入 `related_finding_ids`（F07-08）。
- 同一事实键但动作相反：保留两项，记 `conflicting_actions`，不静默选择；进入 F08 前必须由用户确认。
- 模型候选之间只有 claim 文本规范化后完全相同、quote/引用一致且 scope 相同才合并；否则分开。
- `finding_id = "fnd_" + kind + "_" + sha256(canonical_json(合并键))[:12]`；碰撞时追加 `_2`、`_3`。ID 在一次运行内稳定，不依赖随机数。
- 排序：`assessment` 等级（`supported` < `hypothesis` < `unknown`），再按契约 kind 顺序（`stale_rule`、`rule_conflict`、`policy_mismatch`、`dead_reference`、`missing_load`、`loaded_violation`、`stale_code_evidence`、`recurrence`、`cleanup_candidate`、`insufficient_evidence`），最后按 `finding_id`。
- `validations` 按输入候选的确定性顺序输出；重复 ID 先按来源优先级 `inspector < rules < incidents < analyst`，再按规范化内容摘要选择保留项。

## 边界条件

| 编号 | 条件 | 期望 |
| --- | --- | --- |
| B-01 | 空候选列表 | 返回空 findings，不抛异常；覆盖记录说明候选数为 0，不能据此宣称项目无问题 |
| B-02 | 缺字段、未知枚举、非法 action、claim 为空 | 拒绝该候选并给出字段路径；其他候选继续 |
| B-03 | 同一 `candidate_id` 重复 | 保留确定性优先项，其余 `duplicate_candidate_id` |
| B-04 | 证据 ID 跨运行、摘要不符、行号越界、不可读 | 拒绝；不得用内容相近的片段替代 |
| B-05 | 绝对路径、`..`、空 prefix、与任务范围无交集 | 拒绝，`scope_invalid` / `out_of_scope` |
| B-06 | 无时区时间、倒置区间、事件时间缺失 | 请求级错误抛 `DiagnosisInputError`；单候选时间缺失则 `unknown` |
| B-07 | 决策 draft/future/withdrawn | 不产生 supported `stale_rule`；记 `decision_not_effective` |
| B-08 | 替代环、悬空 ID、摘要过期的确认 | 不产生确定性替代结论；记缺口 |
| B-09 | 条件含 `unresolved` 或自然语言 | 不进入确定性冲突/替代；降级 `hypothesis`，`ask_user` |
| B-10 | 两条规则仅 harness 不同 | `policy_mismatch`，不得判 `rule_conflict` 或假定共同干扰 |
| B-11 | `legacy/` 例外、`clients-old/` 相似目录 | 例外保留；只有冲突子条款出项；不得建议删除例外或测试义务 |
| B-12 | 回执 partial/unknown、顺序不明、跨 session | 加载结论 `unknown`，列具体缺口；不得 `fix_loading` |
| B-13 | 回执完整、动作前、清单无规则 | 才可 `missing_load` |
| B-14 | 正确规则已加载但行为检查 unknown/未运行 | 不得 `loaded_violation`；保留 `unknown` 与缺口 |
| B-15 | 模型声明加载、未加载、实际违规 | 拒绝，加载/违规只能由 F05 事实支持 |
| B-16 | 模型置信度 0.99 但引用无效或缺少确认 | 拒绝或 hypothesis；置信度不提升等级 |
| B-17 | 同一问题一个 supported、一个 unknown | 两个 Finding 分别保留状态和检查来源，不合并 |
| B-18 | 历史事件早于决策生效、当前 `as_of` 晚于生效 | 历史项不标过时；当前体检项可独立判定；不得混用时间 |
| B-19 | 规则/事件/检查 ID 存在但版本不匹配 | `unknown` 或拒绝，不按 ID 同名放行 |
| B-20 | 非 synthetic 回执用于首版合成案例 | 加载类结论 `unknown`，保留 `unsupported_provenance` |
| B-21 | 候选或引用数量超过上限 | 超限候选 `candidate_limit`，覆盖标记失败，不静默截断 |
| B-22 | 证据存储系统性读取失败 | 抛 `DiagnosisUnavailable`；单条引用失败只降级该候选 |
| B-23 | 重复运行相同输入 | finding_id、顺序、validations 一致 |
| B-24 | 中文、CRLF、无末尾换行 | 行号与引用由 F02 处理；F07 只按 EvidenceRef 定位，不改写原文 |

默认上限：候选总数 2,000，单候选引用 32 条，单条 claim 4,000 字符；可按配置降低。超过上限必须显式记录，不得只处理前缀后当作完整结果。

## 错误与覆盖

F07 的异常边界：

- `DiagnosisInputError`：`request`、registry/incidents 视图或公共对象结构无效，调用方按契约退出码 2 处理。
- `DiagnosisUnavailable`：证据存储整体不可用或运行绑定失效，调用方可将本次运行标为 `partial`/`failed`。
- 单个候选的结构、引用、门槛错误不抛出；写入 `validations` 后继续处理其他候选。
- 不捕获 `KeyboardInterrupt`、`MemoryError` 等进程级异常。

`DiagnosisResult.coverage` 至少包含：

| check_id | passed 条件 | failed/not_run 条件 |
| --- | --- | --- |
| `diagnosis.candidate_validation` | 所有候选已按上限处理，拒绝均有原因 | 超过上限或内部无法继续 |
| `diagnosis.reference_validation` | 所有引用均已完成校验并记录结果（无效引用作为候选拒绝原因保留） | 证据存储系统性失败，或未执行 |
| `diagnosis.gate_evaluation` | 所有有效候选已完成门槛检查；无有效候选时 `passed/no_candidates` | 材料或运行错误导致门槛检查未完成 |

`unknown` 的 Finding 不等于整次运行 `partial`；运行状态由 F09/F10 根据请求的检查是否实际完成决定。F07 只报告自身检查的覆盖，不把“没有 Finding”写成“没有发现问题”。

## 本地测试

```sh
.venv/bin/python -m compileall -q src/context_manager
.venv/bin/python -m pytest tests/test_f07_diagnosis.py tests/test_f04_decisions.py tests/test_f05_incidents.py tests/test_f06_analyst.py -q
```

### 测试构造要求

- 用真实的 F02 临时证据存储、F04 registry、F05 事件/回执/违规结果构造输入；F07 的门槛必须执行真实逻辑。
- 模型候选通过 F06 的正式候选对象或等价 JSON 注入；不得 monkeypatch `evaluate_findings` 内部门槛，也不得用传输替身替代判定。
- 测试读返回的 `Finding` / `validations` / `checks` 字段，不靠日志文本或异常消息判断。
- 每个门槛至少有一个负例翻转：例如把 `coverage=complete` 改为 `partial`，必须从 supported 变为 unknown；把确认删掉，必须拒绝或降级。
- 夹具全部为合成材料，放在 `tests/fixtures/f07/`，附简短 README 说明场景；不使用真实日志，不需要本地模型标记。

### 验收用例

| 编号 | 场景 | 关键输入 | 必须断言 |
| --- | --- | --- | --- |
| F07-01 | 过时规则、有效决策、错误复发 | AGENTS v1 + `legacy/` 例外 + 测试义务；ADR-002 accepted 且 supersedes；两个独立任务各一次 v1 编辑，两次均由已确认 `BehaviorCheck` 判为违规；e1 有已加载回执，e2 回执缺失；模型给出因果解释 | `stale_rule` 为 supported，动作为 `update`/`narrow_scope` 而非 `delete`；`recurrence` 为 supported，`related_event_ids` 含两个任务；因果解释独立成项且只能是 `hypothesis`，不得继承 supported；证据引用全部可解析 |
| F07-02 | 正确规则加完整未加载证明 | 正确 v2 规则适用；e1 前回执同 session/harness/epoch、顺序已验证、coverage complete、渠道完整、无 v2 条目；v2 快照摘要匹配 | `missing_load` 为 supported，动作 `fix_loading`；`load.coverage_complete` 与 `load.rule_absent` 均 passed；保留 synthetic；不产生 `loaded_violation` |
| F07-03 | 正确规则已加载且实际违规 | 同一 e1，回执含 v2 实际文本；已确认 `BehaviorCheck` 对编辑后值判失败，输入摘要匹配；不在例外内 | `loaded_violation` 为 supported，动作 `add_check`；`behavior.check_result` passed；不产生 `missing_load`；不得把因果写成 supported |
| F07-04 | 加载不明 | 回执悬空、coverage partial 或动作后生成；正确规则应适用 | 加载类 Finding 为 `unknown`，动作 `ask_user`；`missing_evidence` 具体到回执/覆盖/顺序；规则不被建议删除；不得出现 supported `missing_load` |
| F07-05 | Agent 自信但缺引用/范围/确认 | 模型候选 confidence 0.99，claim 为 stale/loaded_violation；缺 support_refs、scope 或确认；或无确认决策 | 无引用或引用无效时直接拒绝，记 `rejected`；引用有效但门槛不足时最多 `hypothesis`；任何路径都不得 supported；`model_confidence` 只出现在 validation，不改变等级 |
| F07-06 | 不同 Agent 规则各自适用 | AGENTS 为 codex/v1，CLAUDE 为 claude_code/v2，范围相同；无共同 session 回执；共享决策未分别授权 | `policy_mismatch` 为 supported（只声明要求不同且共同加载未确认），动作 `ask_user`/`narrow_scope`；不得产出 `rule_conflict`；不得声明共同干扰；若补上共同加载证明，则改走 `rule_conflict` 或 `loaded_violation` |
| F07-07 | 兼容目录、历史引用和反证 | 旧规则含 `legacy/` 例外、测试义务；ADR-001 是历史引用；候选附反证引用 | 合法例外和测试义务完整保留，反证在 `counter_refs` 可见；`stale_rule` 只覆盖冲突子条款，动作不得 `delete`；历史引用不被当成当前有效规则；不得生成会误伤 `legacy/` 的修复方向 |
| F07-08 | 同一问题一项已证实、另一项未知 | 规则过时事实有确认决策；同一规则的模型候选缺当前范围/确认；或 e2 根因缺回执 | 两个 Finding 使用不同 `finding_id`，分别保留 `supported` 与 `unknown`/`hypothesis`；不合并、不互相覆盖；`related_finding_ids` 可互链；检查来源分别可见 |
| F07-09 | 旧代码快照与任务指定版本不符 | 事件引用 rev A 的代码快照；任务要求 rev B；两者证据均可核对且不同；规则本身正确 | `stale_code_evidence` 为 supported，动作 `fix_retrieval`；不得输出修改正确规则的 supported；`version.compare` passed；若缺任务要求版本，则为 `unknown` + `ask_user` |
| F07-10 | 引用校验负例 | support ref 跨运行、sha 错误、行号越界、行段不可读；另一候选引用正常 | 每条无效引用被拒绝并给出具体 `reason_code`；无效候选不产生 Finding；正常候选仍处理；`diagnosis.reference_validation` 记录结果 |
| F07-11 | 冲突与替代优先 | 两条 accepted 约束同范围矛盾且无替代；变体为 D2 明确 supersedes D1 | 无替代时 `rule_conflict` supported + `ask_user`，不自动选边；有替代时只产生 `stale_rule`，不再把已解决的一对标为冲突；替代链不完整时降级/拒绝 |
| F07-12 | 时间与决策状态 | 决策为 draft、未来生效、withdrawn；历史事件早于 effective_from；`as_of` 晚于 effective_from | 均不产生 supported `stale_rule`；validation 记 `decision_not_effective`；历史项与当前体检项分开，不混用 `occurred_at`/`as_of`；当前项可独立判定 |
| F07-13 | 条件或解释未决 | 规则含 `unresolved` 自然语言条件；模型主张冲突；无确认解释 | 不产生确定性 `stale_rule`/`rule_conflict`；最多 `hypothesis` + `ask_user`；`missing_evidence` 指向解释/条件；不得给破坏性动作 |
| F07-14 | 去重、冲突声明与确定性 | 同一 stale 事实被 rules 以不同 candidate_id 重复提交；模型提出相反动作；重复 candidate_id；连续运行两次 | 相同键合并且引用并集；相反动作分开并记 `conflicting_actions`；重复 ID 按来源优先级保留；两次运行 finding_id、排序、validations 完全一致 |
| F07-15 | 模型伪造加载/违规 | 模型候选 kind=`missing_load`/`loaded_violation`，引用看似有效，但 F05 无 observation；或模型自述 supported | 拒绝，`model_cannot_establish_load`/`model_cannot_establish_violation`；不产生加载/违规 Finding；不因模型文本升级任何事实 |
| F07-16 | 空输入、全无效与上限 | 无候选；全部候选结构错误；候选数超过上限；引用数超限 | 空输入不崩溃且说明未据此宣称无问题；空输入或全无效时 findings 为空、validations 完整、gates 为 `passed/no_candidates`；超限候选记 `candidate_limit`，覆盖标失败，不静默截断 |
| F07-17 | 失效引用与 API 字面量 | 真实缺失 Markdown 链接、缺失 npm script、围栏内路径、行内 `/api/v1/users`、动态命令 | 只有真实链接/静态命令可 `dead_reference` supported，动作 `ask_user`/`update`；API 字面量和代码围栏不报失效文件；动态命令为 `unknown` + `ask_user`；源引用可解析 |
| F07-18 | synthetic 来源传播 | 加载/违规/复发均来自 synthetic 回执与事件；另造非 synthetic 回执 | 相关 Finding 的 `synthetic=true`，`synthetic.provenance` passed；非 synthetic 回执不升级加载结论，记 `unsupported_provenance`；报告不会把合成结果呈现为真实会话证明 |

## 与上下游接口

### 上游必须提供

| 上游 | 本规格要求的产物 | 缺少时的处理 |
| --- | --- | --- |
| F02 | 本次运行的证据解析、sha/行段校验、`run_id` 绑定 | 引用无法核对则拒绝；系统性失败抛 `DiagnosisUnavailable` |
| F03 | 规则清单、适用范围、引用类型/目标状态、扫描覆盖 | `dead_reference` 降级或拒绝；不得猜缺失目标 |
| F04 | 已确认规则/决策/确认、约束比较、范围关系、替代链、残余义务、行为检查 | 缺少比较或残余义务时不得 `delete`；缺少替代链则拒绝 stale |
| F05 | 去重事件、已确认违规、复发分组、回执绑定/覆盖/顺序/来源、缺口 | 加载类结论 `unknown`；不得用模型补写 |
| F06 | 符合契约的候选、读取记录、结束原因 | 候选结构无效则拒绝；`semantic_mode=off` 时不得有模型候选 |

### 下游可依赖

| 下游 | F07 提供 |
| --- | --- |
| F08 | `findings`、`assessment`、`suggested_action`、`support_refs`/`counter_refs`、`related_rule_ids`、`related_event_ids`；只有 supported 且动作可执行时进入 Diff 预检 |
| F09 | `findings`、`validations`、`coverage`、`stats`；逐项 checks 与 synthetic 标记 |
| F10 | 稳定入口 `evaluate_findings`、结构化异常、确定性的 finding 顺序 |

F08 不得把 `hypothesis` / `unknown` 当作修改依据；F07 也不得输出没有证据门槛支撑的破坏性动作。

## 通过与交接

审查者重点检查：

1. 单一证据是否被不当扩展为完整因果结论；supported 是否只落在可观察事实上。
2. 每类根因的修复方向是否与用户目标相符：规则过时改规则，加载缺失修发现/注入，已加载后违背加检查，检索版本错误修检索。
3. 草稿/未来/撤回决策、未确认解释、partial 回执、legacy 例外是否都被正确降级或拒绝。
4. 模型是否只能通过 F05 事实建立加载/违规结论；置信度是否完全不影响等级。
5. 引用是否真的回到本次运行快照；冲突与反证是否保留；synthetic 是否传播。
6. 去重是否没有丢掉独立问题，冲突声明是否没有被静默合并。

实现交接写入 `local/handoffs/F07.md`，至少包含：实现文件、测试命令、退出码、日志位置、源文件摘要、F07-01–F07-18 逐项结果、与本文的偏差、对 F08/F09 的实际输出样例。
