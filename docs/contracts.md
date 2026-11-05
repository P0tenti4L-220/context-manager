# 公共数据契约 v1 · 设计修订 r3

本文件冻结 F01–F10 共用的数据形状和状态语义。[跨功能接口矩阵](interfaces.md) 冻结调用签名、职责和验收映射。专项规格补充模块行为，设计交接保留讨论历史。F01 负责结构校验，拥有业务数据的模块负责引用、授权和证据门槛的校验。

`r3` 是 F01 实现审查后的设计澄清，日期为 `2026-11-05`。首版对外 JSON 文档和 JSONL 记录仍用整数 `schema_version: 1`，嵌套对象不重复带版本。必填键可以明确允许 `null`；省略可选键时按字段表补默认值。未知字段、重复键、非有限数和未知闭合枚举按 F01 拒绝。F05 事件包信封有专项规定的未知顶层字段保留和旧格式兼容。F06 的模型动作信封由 F06 校验。

F01 解析得到的对象与调用方输入隔离，嵌套映射和数组也须不可变。`dump_object` 返回可独立修改的 JSON 容器，不能通过修改输入或输出容器改变已解析对象。文件 JSON 的解析深度和整数长度限制统一返回结构化输入错误，数值字段校验中的转换问题统一返回字段错误。CLI 参数校验错误使用受限消息，不能原样回显非法枚举值。

## 1. 路径、时间与身份

- `project_id` 是本地别名。运行绑定规范化的项目根，项目证据使用根目录相对路径和 `/` 分隔。外部输入只发布逻辑名称。
- `run_id` 绑定本次运行的证据和产物。跨运行材料必须重新导入并复核。快照身份绑定来源、逻辑路径和原始字节摘要，Git 版本标签可空。
- 文件摘要 `sha256` 对完整原始字节计算。行号从 1 开始且两端包含；按 LF 分行，CRLF 一起作为行终止符，末尾换行不额外产生一行。行段摘要 `span_sha256` 覆盖该行段的原始字节及原行终止符。展示用文本不能用于重算此摘要。
- 时间使用带时区的 ISO 8601。CLI 固定单次运行的 `as_of` 并统一输出 UTC；历史事件用各自 `occurred_at`。决策有效区间为 `[effective_from, effective_until)`，结束时间可空。
- `Scope = {include_prefixes, exclude_prefixes, harnesses}`。include 非空，后两项默认 `[]`。路径按组件比较，`clients` 与 `clients-old` 无交集，`.` 表示项目根。工具列表为空或含 `shared` 时不限工具。交集为空时，保留范围外或无交集状态，无需构造空 include 的 Scope。复杂条件保留原文并标记无法机械判定。
- `HarnessId` 是开放的小写标识，形态为 `^[a-z][a-z0-9_]{0,31}$`。内置标识为 `codex`、`claude_code`、`shared`。显示名称可以使用 Claude Code。未知标识的预计加载为未知。

## 2. 输入与登记

| 对象 | 字段与约束 |
| --- | --- |
| `AuditRequest` | 必填 `project_id`、`repo`、`task_scope: Scope`、`as_of`、`semantic_mode: off/local`。可选 `decision_registry`、`harness_profile`、`out`，默认 null。路径字段 `repo`、`decision_registry`、`harness_profile`、`out` 只在本机请求中使用。|
| `EvidenceRef` | 必填 `evidence_id`、`sha256`、`line_start`、`line_end`。指向本次运行的快照，摘要是完整文件摘要。 |
| `SourceLocator` | 必填 `relative_path`、`sha256`、`line_start`、`line_end`。用于可跨运行复用的用户登记，摘要是完整文件摘要。 |
| `RuleSpan` | 必填 `rule_id`、`source_ref: EvidenceRef`、`text`、`scope: Scope`、`harness`、`interpretation_status`。可选 `confirmation_ref` 默认 null。解释状态为 `structured_confirmed`、`model_candidate` 或 `unresolved`。提取块结构和登记扩展由 F04 管理。 |
| `Constraint` | 必填 `subject`、`operator: equals/not_equals`、`value: Scalar`、`conditions: dict[str, Scalar]`。可选 `constraint_id`、`span` 默认 null。span 在 F02 展示文本上用 0 基、左闭右开的 Unicode code point 偏移。F08 编辑仍绑定原始整行字节。 |
| `BehaviorCheck` | 必填 `check_id`、`subject`、`detector`、`scope: Scope`、`parameters`。可选 `confirmation_ref` 默认 null。检测器为 `python_assignment_literal`、`structured_check_result`，参数按 F04/F05 规格校验。 |
| `Decision` | 必填 `decision_id`、`status: draft/accepted/withdrawn`、`scope: Scope`、`effective_from`、`supersedes`、`source_ref: EvidenceRef`、`confirmation_ref`、`constraints`。可选 `effective_until` 默认 null，有值须晚于起点。空的 `confirmation_ref` 保留为缺口；accepted 决策须有有效确认才生效。 |
| `Confirmation` | 必填 `confirmation_id`、`kind: decision_approval/rule_interpretation/check_configuration`、`source_ref: EvidenceRef`、非空 `approved_items: ApprovedItem[]`。可选 `edit_approvals: EditApproval[]` 默认 `[]`，只允许 `decision_approval` 携带非空改法批准。 |
| `ApprovedItem` | 必填 `item_id`、`source_ref: EvidenceRef`。按确认 kind 分别引用登记决策、规则解释或检查配置。 |

`Scalar` 为字符串、整数、有限浮点数、布尔值或 null。结构化比较区分类型，避免把布尔值当成整数。F04 只根据生效的确认、时间和范围比较约束。环、悬空 ID、冲突和历史引用按 F04 的门槛处理。生效决策的空约束列表加直接 `supersedes` 表示退役；仍须验证范围、解释和 F08 的删除批准。

CLI 决策登记中的所有 `source_ref`（含确认与批准条目）使用 `SourceLocator`。F04 预检结构，F02 按受限规则捕获定位文件，F04 将摘要和行段匹配的条目绑定为 `EvidenceRef`。绑定使用内存对象，原登记保持原样。绑定失败记录 `registry_binding_failed`，相关条目不参与确定性结论或预览，保留其他独立结果并将覆盖标为未完成。项目文本中自称已接受的决定不能代替用户显式登记。

### 具体改法确认

`EditApproval` 必填 `decision_id`、`target_path`、`operation: replace_span/delete_span`、`line_start`、`line_end`、`old_text_sha256`、`replacement_sha256`、非空 `rule_ids`。可选 `valid_until` 默认 null。该记录只能批准同一 `Confirmation.approved_items` 中已确认的决策。校验时 `as_of >= valid_until` 为过期。

- `old_text_sha256` 取基准快照目标行段的原始字节摘要。`replacement_sha256` 取 F08 按目标编码、换行和文件末尾规则处理后的实际替换字节。删除使用空字节的 SHA-256。
- `approval_key` 对上述八个必填字段的 JSON 对象计算 SHA-256。使用 UTF-8、`ensure_ascii=False`、键排序、无空格分隔符、无末尾换行，`rule_ids` 按 UTF-8 字节序排序且不重复。有效期单独校验。
- 每个授权决策都要匹配改法。F04 同时验证确认来源、条目和决策有效性。缺失、过期或不匹配返回 `needs_confirmation`。解释确认只确认解释；改法批准仍不能替代 F07 证据门槛、F08 路径白名单、义务保留及预检。

### 事件与回执

- F05 输入事件包中的 `IncidentInput` 和回执使用包内 `file_ref`。归一化后解析为本次运行 `EvidenceRef`，无法解析时保留缺口。
- 归一化 `Incident` 必填 `event_id`、`session_id`、`harness`、`action`。`task_id`、`attempt_id`、`occurred_at`、`context_epoch`、`snapshot_id`、`source_ref` 在输出中保留键，缺失时为 null。可选 `scope: Scope` 默认为 null。时间、顺序和独立任务身份不猜测。
- `Action` 必填 `kind: edit/tool_result/mention`、`target_path`、可空 `evidence_ref`，可选 `before_ref`、`after_ref` 默认 null，`payload` 默认 `{}`。`Incident.source_ref` 指向事件原文；`Action.evidence_ref` 指向动作观察材料。F05 的检测器专用引用和输入字段归入其事件视图。
- 归一化 `LoadReceipt` 含 `receipt_id`、`session_id`、`harness`、`context_epoch`、`before_event_id`、`coverage`、`channels_covered`、`entries`、`provenance`，`captured_at` 可空。每条 `entries[]` 含 `rule_id`、`source_ref: EvidenceRef`、`text_sha256`、`channel`、可选 `scope`。F01 允许结构正确的 `entries=[]`，包括声明 `coverage=complete` 的空清单；F05 再核验来源、身份、时序和完整渠道证明，证明不足时保留 unknown。合法空清单须有完整覆盖证明才可能支持排除。
- 首版 F05 接收合成事件包。旧示意格式保留 `clue_only`、加载 `unknown` 和迁移提示。仅有回执 ID 时记录缺口。加载观察保留来源与合成标记。`discovered`、`expected` 是静态发现及预期；`observed_loaded`、`observed_not_loaded`、`unknown` 由 F05 按回执门槛裁定。

## 3. 证据与运行覆盖

| 对象 | 字段与约束 |
| --- | --- |
| `Provenance` | `{kind, adapter, version}`。kind 取 `project_file`、`synthetic`、`user_supplied`、`adapter`。project_file/user_supplied 的 adapter、version 都为 null；synthetic 可同时填适配器名和版本，也可都为 null；adapter 两项必须非空。F05 将包内 `synthetic/adapter/protocol_version` 映射到此形状。 |
| `Evidence` | `evidence_id`、`run_id`、`kind`（开放标识）、`relative_path`、`sha256`、`snapshot_id`、`captured_at`、`source_revision`、`byte_length`、`line_count`、`encoding`、`provenance: Provenance`。附 `origin: project/selected_input/imported`（默认 project）、`input_id`（默认 null，外部输入及导入材料须有值）与 `expires_at`（默认 null，有值时为带时区时间）。外部逻辑名称沿用 F02 的 `selected-input/<input_id>/<basename>` 与 `imported/<input_id>/<logical_name>`。 |
| `CoverageItem` | `{check_id, status, inputs: IdString[], reason: string/null, artifact_refs: RelativePath[]}`。状态为 `passed/failed/not_run`。failed/not_run 必须有原因。检查已完成但无材料可查时，使用 `passed` 加 `none_found` 或 `no_candidates`。 |

F02 分开提供受限文本读取和内部快照读取。只有实际展示给 Analyst 的内容计入模型引用覆盖。原始字节读取、快照复核和报告链接检查均不增加此覆盖。

## 4. 模型交互契约

Agent 动作限于 `list_evidence`、`read_evidence`、`submit_candidates`。F06 校验后调度。候选含 `candidate_id`、`kind`、`claim`、`scope: Scope`、`support_refs`、`counter_refs`、`missing_evidence`、`suggested_action`，可选 `edit_intent` 和 `model_confidence`。补齐引用摘要后验证本次运行和实际读取覆盖。

模型候选、归档置信度和改法草图保持模型来源。模型候选经 F07 核验，结论最高为 `hypothesis/model_candidate`。静态候选用 F07 的 `StaticCandidate` 信封，F10 只机械适配事实，不升级结论。F06 的 `TraceEvent[]` 通过 F09 写入入口的关键字参数 `trace` 传递，不进入 `RunResult`。

## 5. 诊断输出

`Finding` 必填 `finding_id`、`kind`、`claim`、`scope: Scope`、`assessment`、`basis`、`support_refs`、`counter_refs`、`missing_evidence`、`suggested_action`、`checks: CoverageItem[]`。可选 `related_event_ids`、`related_rule_ids`、`related_finding_ids` 默认 `[]`，`claim_type: fact/causal_explanation/gap` 默认 fact，`synthetic` 默认 false。进入 F08 编辑路径的发现必须带规则 ID。

| 字段 | 取值 |
| --- | --- |
| `kind` | `stale_rule`、`rule_conflict`、`policy_mismatch`、`dead_reference`、`missing_load`、`loaded_violation`、`stale_code_evidence`、`recurrence`、`cleanup_candidate`、`insufficient_evidence` |
| `assessment` | `supported`、`hypothesis`、`unknown` |
| `basis` | `deterministic`、`confirmed_interpretation`、`model_candidate` |
| `suggested_action` | `keep`、`update`、`delete`、`narrow_scope`、`relocate`、`fix_loading`、`add_check`、`fix_retrieval`、`ask_user` |

`supported` 只支持该项具体声明。跨工具规则差异可以是已支持的事实，共同加载和行为因果需要各自证据。因果解释用独立 Finding 关联事实，保持 hypothesis；语义关闭时不生成模型来源或因果解释项。首版没有可信的任务要求版本材料时，`stale_code_evidence` 保持 `unknown/ask_user`。失效引用默认 `ask_user`，已核验替代目标时可建议 `update`，仍不进入 F08 的规则编辑入口。

## 6. 修复输出

`RepairProposal` 必填 `proposal_id`、`run_id`、`finding_ids`、`base_files`、`edits`、`diff_sha256`、`status`、`checks`、`unresolved_questions`。状态为 `preview_ready`、`needs_confirmation`、`blocked`。每次运行最多一个 `preview_ready`，只有该状态携带 `diff_sha256` 和对应 Diff。未就绪提案可为空编辑和基准；可预览提案必须至少有一项编辑和基准。

- `BaseFile` 为 `{relative_path, sha256, evidence_id}`，evidence_id 可省略且默认 null，序列化时保留该键。`Edit` 为 `{edit_id, finding_id, relative_path, operation, line_start, line_end, old_text_sha256, new_text}`。替换的 `new_text` 非空，删除为 null。F08 内部的编辑意图、义务记录和 hunk 映射属于修复视图。
- `unresolved_questions[]` 每项为 `{question_id, prompt, finding_ids, needed}`，指出具体缺少的确认或材料。
- `RepairPlan` 由 F08 交给 F09，包含 `proposals`、`diff` 原始字节、`diff_sha256`、`diff_proposal_id`、`advisories`、`rejections`、`checks`、`hunks` 和可空 `apply_check`。Git 预检只在临时副本运行，报告写入 `proposals.json`，审计覆盖与退出码独立。

## 7. 运行结果与退出码

`RunResult` 必填 `run_id`、`project_id`、`mode`、`as_of`、`status`、`parameters`、`coverage`、`findings`、`proposals`、`artifacts`、`errors`。可选 `inputs` 默认 `[]`，`project_revision` 默认 null，`synthetic` 默认 false。

- `mode = {command: audit/diagnose, semantic: off/local}`。`parameters = {scope: Scope, model: ModelSummary/null}`。F09 将 `as_of` 加入对外参数摘要。`ModelSummary` 含 `name`、`transport`、`temperature`、`timeout_s`、`prompt_version`、`prompt_sha256`，未获取的提示词信息可空；不记录服务地址、路径、正文或凭据。
- `inputs[]` 为 `{input_id, kind, logical_name, sha256, byte_length, synthetic}`。kind 取 `decisions`、`incidents`、`harness_profile`。模型配置仅记录上面的模型摘要，不进入输入证据。决策登记可显式声明 `synthetic: true`；汇总标记由 F09 结合输入和证据来源判定。
- `RunError` 为 `{code, path, message, severity}`，path 可空，severity 默认为 `error`，也可为 `warning`。错误不回显输入正文。`RunResult.artifacts` 是相对路径列表，`run.json.artifacts` 是角色到相对路径的映射。

| 状态 | 退出码 | 含义 |
| --- | --- | --- |
| `complete` | 0 | 已完成请求的检查，报告可以有发现和待确认项 |
| `partial` | 3 | 至少一个已请求的检查因材料、预算、读取或模型问题未完成 |
| `failed` | 1 | 致命运行或写入错误，含运行 ID 写入冲突 |
| 参数或契约错误 | 2 | 参数或外部输入结构无效 |

F10 计算请求状态，F09 只按产物完整性保守降级。覆盖为空不能标 complete；存在 failed 或已请求的 not_run 时为 partial。唯一的 not_run 豁免是 off 模式下 `f06.semantic: not_run / disabled_by_user`。空扫描与空候选已完成时分别使用 `passed / none_found`、`passed / no_candidates`。修复预检失败只影响提案，单个 unknown 发现本身也不降级运行。
