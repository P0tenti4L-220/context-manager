# F08 最小修复提案与 Diff 预检

依赖 F04、F07，只读使用 F02 的证据快照和 F03 登记的规则文件。所属组件为 Repair Planner。细化版本 `r1`，日期 `2026-11-04`。

## 用户结果

用户看到针对具体条款的最小 Diff、修改依据、原文件摘要和预检结果。每条 hunk 都能回指一个 Finding。范围、解释或需求尚未明确时，报告列出候选改法、待确认问题和未决前提，不输出可应用补丁。

F08 只产出可审阅的提案。它不写入被审计项目、不把补丁应用到真实仓库、不安装 Hook、不改配置、不改业务源码。`preview_ready` 表示“具备审阅条件”，是否应用由用户另行决定。

## 处理范围

首版只允许修改**已有规则文件中的整行段**：

- `replace_span`：替换一条已确认的过时条款。
- `delete_span`：删除完全被替代、且没有独立义务的条款。

以下请求只输出具体建议，不生成 Diff：

- 迁移到新文件、拆分规则文件；
- 修改业务源码、测试或配置；
- 增加或调整 Hook、Lint、CI；
- 修复加载路径、检索版本或工具配置；
- 新增行为检查；
- 只凭“长期未使用”“文字较长”提出的删除；
- 复合条款中还有未确认解释或未参与替代义务的修改。

## 入口与数据流

建议新增 `src/context_manager/repair.py`、`tests/test_f08_repair.py`。入口：

```python
plan_repairs(findings, edit_intents, registry, evidence_store) -> RepairPlan
```

- `findings`：F07 的 `Finding` 列表。F08 只把它们当作索引；授权和证据门槛由 F08 重新核对，不采信 Finding 自述。
- `edit_intents`：已登记或模型候选的编辑意图，结构见下节。模型候选必须由运行器标为 `origin: model`。
- `registry`：F04 的只读登记视图，同时携带 F03 本次实际登记的 `editable_files`。具体协议见“依赖接口”。
- `evidence_store`：F02 的证据存储，只读。

调用顺序为 F07 产出 Finding，F10 汇总编辑意图，F08 预检，F09 渲染产物。F08 不写 `out/<run_id>/`；Diff 字节和检查记录以返回值交给 F09。

## 编辑意图

`EditIntent` 字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `edit_intent_id` | 是 | 本次运行内唯一。 |
| `finding_ids` | 是 | 至少一个。必须存在，且 Finding 的 `related_rule_ids` 覆盖 `rule_ids`。 |
| `target_path` | 是 | 项目根相对路径，使用 `/`。不得是绝对路径。 |
| `operation` | 是 | `replace_span` 或 `delete_span`。 |
| `line_start`, `line_end` | 是 | 基准快照中的行号，从 1 开始，两端包含。只接受整行段。 |
| `base_evidence_id` | 是 | 编辑所依据的 F02 证据 ID。 |
| `base_sha256` | 是 | 该证据完整文件字节的摘要。 |
| `old_text_sha256` | 是 | 行段原始字节（含行终止符）的摘要。 |
| `replacement_text` | `replace_span` 必填 | UTF-8 文本。`delete_span` 时必须为空。空替换请使用 `delete_span`。 |
| `rule_ids` | 是 | 被替换或删除的条款 ID。 |
| `preserved_rule_ids` | 否，默认 `[]` | 行段内或同文件内必须原样保留的独立义务 ID。 |
| `decision_ids` | 是 | 授权该修改的已登记决策。 |
| `basis_refs` | 是 | 决策、规则和确认的 `EvidenceRef`。引用必须属于本次运行。 |
| `confirmation_id` | 生成预览时必填 | 绑定具体改法的 decision_approval。 |
| `interpretation_ref` | 否 | 已确认解释的引用。 |
| `origin` | 是 | `registry`、`user` 或 `model`。未知值拒绝。 |
| `scope_condition` | `narrow_scope` 时必填 | 已确认的结构化范围条件。 |
| `note` | 否 | 仅供报告显示，不参与授权。 |

约束：

- 行段必须整行覆盖，不支持行内任意字节偏移。修改一条列表项时，替换文本包含列表标记和该行完整内容。
- `replacement_text` 上限 8 KiB；修改后文件不超过 F02 的 1 MiB 上限；否则拒绝。
- 替换文本不得含 NUL；不得只改变编码或换行风格。
- `origin: model` 的意图即使文字完全正确，也不能自行进入 `preview_ready`。它必须命中与最终改法逐字节绑定的确认，见“确认匹配”。
- 意图所依据的始终是 `base_evidence_id` 指向的快照，不是当前工作树。基准变化时重新采集并重新生成意图，不自动 rebase。

## 输出

`RepairPlan`：

| 字段 | 说明 |
| --- | --- |
| `run_id` | 与本次运行一致。 |
| `as_of` | 本次审计固定时间。 |
| `proposals` | `RepairProposal` 列表，字段遵守公共契约 §6。 |
| `diff` | 可审阅 Diff 的原始 UTF-8 字节；没有 `preview_ready` 时为 `None`。 |
| `diff_proposal_id` | `diff` 所属提案；无 Diff 时为 `None`。 |
| `diff_sha256` | Diff 原始字节摘要；无 Diff 时为 `None`。 |
| `apply_check` | 可空的结构化 Git 预检记录，字段见[接口矩阵](../interfaces.md)。 |
| `hunks` | Diff 内部映射，含路径、旧/新行段和关联编辑及发现 ID；F09 用于渲染说明。 |
| `advisories` | 不生成 Diff 的修改建议。 |
| `rejections` | 被拒绝的编辑意图及原因。 |
| `checks` | 修复检查记录，F09 仅写入 `proposals.json.repair_checks`。 |

`RepairProposal` 严格使用公共契约字段，`Edit.relative_path` 和 `Edit.new_text` 由内部意图投影。未进入 Diff 的意图可以在待确认提案中保留安全的契约字段，拒绝原因单独记入 `rejections`。预检及 hunk 到编辑 ID 的映射从 RepairPlan 交给 F09，RepairProposal 保持公共字段。

`status` 只有三种：

| 状态 | 条件 | Diff |
| --- | --- | --- |
| `preview_ready` | 所有纳入编辑均通过授权、基准、路径、保留和 Git 预检；最后复核时目标文件未变化 | 有，且可应用 |
| `needs_confirmation` | 缺解释、决策未生效、确认过期或不匹配、范围不明、编辑重叠等可由用户补齐的前提 | 无 |
| `blocked` | 路径越界、符号链接、非规则文件、基准变化、行段摘要不符、Git 预检失败等不能安全生成补丁 | 无 |

每次运行最多一个 `preview_ready` 提案，聚合所有互不冲突且各自满足门槛的编辑。未就绪的 Finding 各自形成 `needs_confirmation` 或 `blocked` 提案。这样 F09 的 `proposal.diff` 只有一份确定补丁，且每个 Finding 仍保留独立状态。

`RepairAdvisory`：

- `advisory_id`、`finding_ids`、`kind`、`summary`、`rationale`、`suggested_target`、`basis_refs`、`requires_confirmation`。
- `kind` 取 `migrate_clause`、`new_file`、`hook`、`config`、`fix_loading`、`fix_retrieval`、`add_check`、`manual_edit`。
- 建议不得包含可应用 Diff，也不得把模型措辞写成已确认事实。

`EditRejection`：

- `edit_intent_id`、`finding_ids`、`target_path`、`reason`、`detail`、`checks`。
- 只记录相对路径、摘要和原因码，不复制整段原文。

## 依赖接口

以下协议是 F08 的硬依赖。上游字段缺失时 F08 失败关闭，不猜测、不读取项目文件绕过。

### F02 证据存储

```python
evidence_store.get_evidence(evidence_id) -> Evidence
evidence_store.snapshot_bytes(evidence_id) -> bytes        # 本次运行快照的完整原始字节
evidence_store.verify_current_digest(relative_path, expected_sha256) -> DigestCheck
```

- `snapshot_bytes` 返回捕获时的不可变字节，并重新校验 `sha256`。只有行级 `read_evidence` 不足以生成 Diff。
- `verify_current_digest` 只读比对当前项目文件与基准摘要，不产生新证据、不改变运行状态。F08 不自行打开项目文件。
- 没有上述接口时，只能返回 `blocked`，原因 `evidence_interface_incomplete`。

### F03 发现结果

- 提供本次实际登记的 `registered_instruction_files`，包含规范相对路径和对应证据 ID。
- 只登记实际发现并捕获、范围可解析的 `AGENTS.md`、`CLAUDE.md`、`AGENTS.override.md` 和已识别的 `.claude/rules/` 指令文件。导入目标还须自身属于这些规则类型。
- 未发现、未捕获或位于项目根之外的文件不得进入白名单。空列表表示“不可编辑”，不是“全部允许”。

### F04 登记视图

```python
registry.project_root: Path
registry.run_id: str
registry.as_of: datetime
registry.editable_files: Sequence[str]
registry.get_rule(rule_id) -> RuleRecord | None
registry.get_decision(decision_id) -> Decision | None
registry.decision_applies(decision_id, rule, task_scope, as_of) -> DecisionEval
registry.get_confirmation(confirmation_id) -> Confirmation | None
registry.confirmation_approves(confirmation_id, approval_key, *, as_of) -> ConfirmationEval
```

- `RuleRecord`：`rule_id`、`relative_path`、`line_start`、`line_end`、`text_sha256`、`interpretation_status`、`evidence_id`。
- `DecisionEval`：状态 `accepted` / `inactive` / `out_of_scope` / `not_superseding` / `missing` / `conflict`，附原因。
- `ConfirmationEval`：状态 `valid` / `missing` / `stale` / `expired` / `mismatch`，并说明是否覆盖决策、规则和具体改法。
- `registry.editable_files` 来自 F03 的本次登记结果，是唯一目标白名单。
- 语义解释与决策有效性由 F04 判定；F08 不重新解释自然语言。

### F07 诊断

F08 使用 `finding_id`、`kind`、`assessment`、`basis`、`suggested_action`、`related_rule_ids`、`support_refs`、`checks`。用于生成 Diff 的 Finding 必须有 `related_rule_ids`；缺失时返回 `needs_confirmation`，原因 `finding_rule_link_missing`。

### F09 报告与 F10 编排

- F09 原样写入 `plan.diff` 字节为 `proposal.diff`，不得重新编码或重新生成；`diff_sha256` 对同一字节计算。
- F09 读取 `proposals`、`advisories`、`rejections`、`checks` 和 `hunks`。无 Diff 时明确记录原因，`run.json` 的 Diff 路径为 null。
- F10 传入同一 `run_id`、`as_of` 和证据存储，不把当前工作树路径传给 F08。

## 授权门槛

### Finding 与操作

| Finding 情况 | F08 行为 |
| --- | --- |
| `stale_rule` 且 `update` / `delete` / `narrow_scope` | 可进入编辑流程，仍须通过下列全部检查。 |
| `stale_rule` 且 `keep` / `ask_user` | 只生成问题或建议，不编辑。 |
| `rule_conflict` 且 `narrow_scope` | 只在解释和范围已确认时编辑；否则 `needs_confirmation`。 |
| `cleanup_candidate` | 只生成 `migrate_clause` 等建议。删除必须另有 `stale_rule` 依据。 |
| `policy_mismatch`、`missing_load`、`loaded_violation`、`stale_code_evidence`、`recurrence`、`insufficient_evidence` | 不修改规则文件；按建议类型输出 advisory 或问题。复发本身不构成删改依据。 |

编辑操作必须与 Finding 的 `suggested_action` 一致：`delete_span` 只对应 `delete`；`replace_span` 对应 `update` 或 `narrow_scope`；`narrow_scope` 还必须提供 `scope_condition` 并由确认覆盖。每个 `basis_ref` 都必须属于本次运行、能解析出行段且摘要匹配，否则 `needs_confirmation`（`basis_ref_invalid`）。`decision_ids` 中的每个决策都必须通过下列检查；任一失败即整体未就绪。

以下任一情况不得进入 `preview_ready`：

- `assessment` 不是 `supported`；
- `basis` 为 `model_candidate`，且没有针对最终改法的确认；
- Finding 的规则 ID 与 `edit_intent.rule_ids` 不一致；
- Finding 的关键检查是 `not_run`、`failed` 或缺少来源。

### 决策

F08 通过 F04 逐项确认，不根据日期新旧自动补全授权：

- `status` 为 `accepted`；
- `as_of` 落在 `[effective_from, effective_until)` 内；
- 决策范围覆盖该规则路径和任务范围；范围外或兼容目录不生成修改；
- `supersedes` 直接列出目标 `rule_id`。只有更晚时间戳、只有文字相似、只有间接链都不建立替代；
- `confirmation_ref` 存在，来源摘要与当前证据一致，且覆盖该决策/规则；
- 出现冲突的 accepted 决策、环或悬空 ID 时返回 `needs_confirmation`，原因 `authority_conflict`，不自动选边。

### 确认匹配

F08 按[公共契约](../contracts.md)计算 `approval_key`，包含决策 ID、路径、操作、起止行、旧行段摘要、最终替换字节摘要和排序后的规则 ID。逐个授权决策调用 F04 的 `confirmation_approves`。F04 校验批准条目、来源与有效期。

- 计算替换摘要时采用即将写入临时副本的字节，保留原编码、换行和末尾约定。删除对应空字节。
- 缺批准、批准过期、来源摘要漂移或改法不匹配均返回 `needs_confirmation`。只有规则解释或决策批准但无 `edit_approvals` 时，也按缺具体改法处理。
- 具体改法确认不能提升 Finding 等级。模型意图仍须与可编辑的已验证事实和批准的精确字节匹配。

### 语义复杂条款

以下任一条件要求用户确认解释或改法：

- Finding 为 `hypothesis` 或 `basis: model_candidate`；
- 规则的 `interpretation_status` 为 `model_candidate` 或 `unresolved`；
- 修改会改变适用范围或新增 `scope_condition`；
- 删除跨多条规则或复合义务的行段；
- 同一行内还有必须保留的义务；
- 替换文字由模型改写，无法从结构化决策值直接推出；
- 决策范围含无法结构化比较的条件。

## 目标与基准

### 路径白名单

1. `target_path` 必须是 `registry.editable_files` 中的精确规范路径；先做 Unicode NFC 规范化再比较。
2. 拒绝绝对路径、`..`、空段、反斜杠、NUL、控制字符和 Windows 盘符。
3. 用 `realpath` 确认文件位于项目根内，且路径每一级都不是符号链接；文件本身必须是普通文件，不是符号链接、FIFO、设备或目录。
4. 拒绝 `.git/`、运行输出目录、缓存目录、业务源码、测试、配置、ADR、`examples/` 案例数据和文档。
5. 文件必须是 UTF-8 或 UTF-8 with BOM，可严格解码，大小不超过 1 MiB。
6. 白名单为空、路径大小写或 Unicode 规范化产生歧义时拒绝，原因 `edit_not_whitelisted` 或 `ambiguous_path`。

### 基准快照

- `base_evidence_id` 必须属于本次运行，路径等于 `target_path`，且 `sha256 == base_sha256`。
- `line_start..line_end` 必须落在该证据的 `line_count` 内；F08 自行从原始字节建立行索引，并与 `Evidence.line_count` 核对，不一致时 `blocked`。
- `old_text_sha256` 必须等于该行段原始字节的摘要，包含行终止符。
- 摘要不符时直接 `blocked`。**禁止**模糊匹配、向上/向下搜索相同文字、按上下文猜位置或自动改行号。行号漂移也视为摘要不符。

### 行、编码与换行

- 行号 1 起，两端包含。第 N 行从该行首字节到第 N+1 行首字节；最后一行到 EOF。空文件 `line_count = 0`。
- 行段以 `\n` 分行；`\r\n` 整体属于行终止符，`\r` 不计为独立换行。
- 行段摘要覆盖完整原始字节，包含其行终止符（若有）；BOM 不属于任何行段，修改后保留。
- 替换文本先统一为 `\n`，去掉末尾的换行符（不删除中间空行），再按目标文件的主换行风格编码。主风格为出现次数最多的 `\r\n` 或 `\n`；并列或混合时使用目标行段前一行的换行符，没有则用 `\n`。
- 如果原行段以终止符结束，替换结果也以一个同风格终止符结束；如果原行段是无末尾换行的最后一行，替换结果也不补末尾换行。`delete_span` 删除整个行段字节。
- 其余字节、BOM、行尾空白、编码和换行风格保持原样。不得顺手格式化、重排列表或统一全文换行。
- 修改后文件为空、替换后与原文完全相同、或替换超出大小限制时，分别 `blocked`（`file_would_be_empty`、`no_effect`、`file_too_large`）。

## 合并、保留与冲突

1. 按 Finding 分组。一个 Finding 的多个编辑意图作为整体处理；其中任一未就绪，该 Finding 整体不进入可应用 Diff。
2. 同一路径上的编辑按行段字节偏移排序后合并。相邻但不重叠的编辑可以同批生成 Diff。
3. 完全相同（路径、操作、行段摘要、替换摘要、保留集合一致）的意图去重，合并 `finding_ids` 与依据；不得重复计费或重复产出 hunk。
4. 行段重叠且替换不同、一个删除一个替换、或保留集合不同的意图，全部转为 `needs_confirmation`，原因 `overlap_conflict`。禁止按输入顺序后写覆盖先写。
5. 行段内出现其他 `RuleSpan` 时，这些条款 ID 必须全部列入 `preserved_rule_ids`，否则 `needs_confirmation`（`missing_preserved_rule`）。首版只支持同一目标文件内的保留条款；跨文件的保留 ID 返回 `needs_confirmation`（`preserved_rule_scope_unsupported`），不假装已核对。
6. `delete_span` 必须恰好覆盖目标规则的完整行段，不得连带头部、空行或其他条款。删除复合条款的一部分时改用 `replace_span`。
7. 删除或替换后，用行偏移差把每个保留条款映射到最终字节，并逐字节比较。重叠或无法映射时，要求替换文本原样包含该条款并已有改法确认，否则 `needs_confirmation`（`preserved_rule_changed`）。
8. 最多重算一次“可应用编辑集合”。仍不满足时保留未就绪状态，不无限重试。

## Diff 生成与 Git 预检

1. 从证据快照复制基准字节到独立临时目录，按合并结果生成每个目标文件的最终字节；非目标文件不复制、不输出。
2. 在临时仓库中初始化 Git，写入基准文件，`git add` 后写入最终文件，用程序构造的参数生成统一 Diff：

   ```text
   git -C <temp> -c core.autocrlf=false -c core.safecrlf=false \
       -c core.quotePath=false diff --no-ext-diff --no-color --unified=3 \
       --src-prefix=a/ --dst-prefix=b/ -- <sorted relpaths>
   ```

   环境固定 `GIT_CONFIG_NOSYSTEM=1`、临时 `HOME`、`LC_ALL=C`、`TZ=UTC`，不使用 shell 拼接。路径排序后生成，保证同样输入得到同样字节。

3. 检查 Diff 只包含 `base_files` 中的路径；解析 hunk 并与 `edits` 的行段对应；确认未修改文件没有出现在 Diff 中。
4. 另建干净临时目录（初始化为空仓库），复制同一基准字节，执行：

   ```text
   git -C <check> apply --check --verbose --whitespace=nowarn <patch>
   git -C <apply> apply --verbose --whitespace=nowarn <patch>
   ```

   分别记录 argv、退出码、标准输出和错误输出尾部（各最多 4 KiB）、耗时。`apply` 后逐文件比较最终字节，必须与预期完全一致。
5. 任何一步失败都返回 `blocked`，原因 `git_unavailable`、`git_apply_check_failed` 或 `temp_apply_mismatch`；不得忽略退出码、不得用 `--unsafe-paths`、不得在真实项目执行 Git。
6. 临时目录在 `finally` 中清理。原始日志由测试或 F09 保存到 `local/verification/`，不写入被审计项目。

## 输出前复核与状态

1. 生成 Diff 后，再对每个 `base_files` 调用 `verify_current_digest`。任一摘要变化，整个 `preview_ready` 提案降级为 `blocked`，原因 `target_changed_after_preview`，`diff_sha256` 和 `plan.diff` 置空。不能输出过期可应用补丁。
2. 复核 Diff 摘要：`diff_sha256` 对最终 Diff 字节计算；F09 写入的 `proposal.diff` 必须具有同一摘要。
3. 生成稳定 ID：`proposal_id = "rp_" + sha256(run_id + sorted(finding_ids) + sorted(edit_keys))[:16]`；重复运行相同输入得到相同 ID 和 Diff 摘要。
4. 输出顺序固定为路径、行号、`finding_id`、`edit_id` 排序，保证报告可复核。
5. 每个 hunk 必须至少关联一个 `edit_id` 和 `finding_id`；无法关联时不得进入 `preview_ready`。

## 错误与边界

- `EditIntent` 的结构错误（缺字段、类型错误、未知枚举、摘要格式错误、行号倒置）由 F01/F08 的契约校验抛出定位到字段的错误，F10 映射退出码 2。
- 跨对象问题（Finding 不存在、路径越界、证据不属于本运行、决策缺失、确认过期、摘要不符）不抛异常，写入 `blocked`、`needs_confirmation` 或 `rejections`，保证一次运行能同时报告多个问题。
- 错误与日志只使用项目相对路径和摘要，不回显完整原文、模型提示或本机敏感配置。
- 同一运行最多生成一个 `preview_ready` 提案；某文件复核失败时整批不输出，避免半套补丁。
- 没有 Findings 时返回空 `proposals`、`plan.diff = None`。存在已支持、建议更新/删除/收窄范围的 `stale_rule`，但没有合格意图时，按该 Finding 输出 `needs_confirmation` 并写明缺失材料。两种情况都不启动 Git 预检。
- 文件在预检期间被替换、删除或从普通文件变成链接时，按 `target_changed_after_preview` 或 `path_escape` 处理，不重试模糊定位。

## 本地测试

```sh
.venv/bin/python -m pytest tests/test_f08_repair.py -q
.venv/bin/python -m pytest tests/test_f08_repair.py tests/test_f07_diagnosis.py -q
.venv/bin/python -m compileall -q src/context_manager
```

本机使用 RTK 时给命令加 `rtk proxy`。原始输出、退出码和耗时保存到 `local/verification/F08/<run>/`。

测试夹具放在 `tests/fixtures/repair/`，每个用例复制到 `tmp_path` 后使用，不修改夹具原件。建议目录：

```text
tests/fixtures/repair/base/AGENTS.md              # LF，过时 v1 条款 + 兼容例外 + 测试义务
tests/fixtures/repair/base/CLAUDE.md              # v2 对照
tests/fixtures/repair/base/.claude/rules/api.md   # 显式纳入的规则
tests/fixtures/repair/crlf/AGENTS.md              # CRLF，中文，无末尾换行
tests/fixtures/repair/dupes/AGENTS.md             # 重复文本、两处可编辑行
tests/fixtures/repair/compound/AGENTS.md          # 复合条款与同一行义务
tests/fixtures/repair/outside/notes.md            # 项目外文件
tests/fixtures/repair/nonrule/clients.py          # 业务源码
```

测试通过正式构造器生成 Finding、Decision、Confirmation、RuleSpan 和 EditIntent；只允许对 Git 子进程、时钟和证据存储做协议级替身，不得替身 F08 的授权、合并、保留或状态判定。断言以实际字节、实际 Diff 和实际退出码为准，不复制文档里的手写报告。

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| F08-01 | 新客户端 v1 规则已被生效决策替代，登记含精确替换批准 | 仅一个 `preview_ready`；Diff 只改目标条款，hunk 可回指 Finding；`git apply --check` 退出 0；临时应用后该行为 v2，其他行逐字节未变；目标仓库摘要不变。 |
| F08-02 | 同文件含兼容例外和测试义务 | 例外与测试文本原样保留，摘要一致；Diff 不触碰这些行；若移除保留记录则转为 `needs_confirmation`，不得静默删除。 |
| F08-03 | 规则仅剩被完整替代的单条义务，登记含精确删除批准 | `delete_span` 只删除该条款整行；其他字节保留；临时应用后文件非空且与预期字节一致；连带头部或空行的意图被拒绝。 |
| F08-04 | 复合条款解释未知、决策为草稿 | `needs_confirmation`；`diff_sha256` 和 `plan.diff` 为 null；问题指出草稿决策或缺失解释；替换文字未写入任何文件。 |
| F08-05 | 目标摘要变化、行号漂移 | 目标文件变化得到 `blocked`（`target_changed_after_preview`）；行号漂移得到 `blocked`（`span_digest_mismatch`）；不模糊搜索、不猜位置、无 Diff；原文件不变。 |
| F08-06 | 路径越界、符号链接、非规则文件 | `../`、绝对路径、符号链接、业务源码、ADR、缺失文件和 FIFO 全部拒绝；无 `preview_ready`；目标树摘要不变；不执行 Git 预检。 |
| F08-07 | 重复文本、多处编辑、重叠编辑 | 只改指定出现位置；两处不重叠编辑同批生成一个 Diff；重叠或不同替换转为 `needs_confirmation`，不静默覆盖；完全重复的意图去重。 |
| F08-08 | CRLF、中文、无末尾换行 | Diff 保持 CRLF；中文正确解码和编码；无末尾换行的最后一行修改后仍无末尾换行；`apply --check` 退出 0，实际应用字节完全一致；中文或带空格路径可应用。 |
| F08-09 | 预览前后目标仓库 | 全部目标文件及项目树摘要不变；不在目标项目创建 `.git`、不写 `out/`、不留下临时补丁；两次调用得到相同 Diff 摘要。 |
| F08-10 | Git 预检失败或 Git 不可用 | `blocked`，记录实际退出码和错误尾部；`plan.diff` 为 null；项目不变；不得忽略失败继续输出。 |
| F08-11 | 确认过期或改法摘要不匹配 | `needs_confirmation`（`confirmation_expired` / `confirmation_mismatch`）；不得退回使用模型原文；无 Diff。 |
| F08-12 | 迁移、源码、Hook、加载或检查修复 | 只输出对应 advisory；不生成 Diff、不修改规则；报告能回指 Finding 和依据。 |

补充检查：

- 用变异测试确认断言有效：放开行号漂移模糊匹配、允许越界路径、跳过保留检查、忽略 `git apply --check` 退出码时，对应用例必须失败。
- 同一夹具重复运行，`proposal_id`、`diff_sha256`、hunk 顺序保持一致。
- 测试结束后清理临时目录，并断言夹具原件摘要未变化。

## 通过与交接

审查者打开实际生成的 Diff、临时应用结果和检查记录，逐条确认：

- Diff 只覆盖已确认的条款，没有扩展到其他文件或其他义务；
- 兼容例外、测试要求和未参与替代的独立义务逐字节保留；
- 每个 hunk 对应具体 Finding，授权来自登记决策或用户确认，不来自模型自述；
- 目标摘要变化、确认过期、路径越界和 Git 失败均按失败关闭处理；
- 原项目文件在预览前后摘要不变；
- 测试确实运行 F08 的正式逻辑，不是复制预期报告；
- 实现交接写入 `local/handoffs/F08.md`，附代码路径、测试命令、退出码、日志和源摘要。

设计交接记录在 `local/design-handoffs/F08.md`，跨功能接口需求集中列在该文件，不在本规格中单方面修改公共契约。