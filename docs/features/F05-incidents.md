# F05 合成事件、加载回执与复发统计

依赖 F02、F04。属于 Diagnostic Policy 的历史证据部分。公共对象见 [数据契约](../contracts.md)，上游见 [F02 证据快照](F02-evidence.md) 与 [F04 规则与决策](F04-rules-decisions.md)。本规格在设计版本 `0.2` 基础上细化，日期 `2026-11-04`。

## 用户结果

工具区分同一失败被记录多次与真正跨任务复发，并说明每个动作之前能确认哪些规则进入了上下文。报告里的每次计数都能回到具体动作、规则版本和回执条目；无法确认时显示缺口，而不是把“没看到”说成“没加载”。

F05 只产出规范事件、回执校验、加载观察、行为观察、去重与复发分组。Finding 的等级、根因和修复方向由 F07 生成；报告结构由 F09 决定。

## 1. 文件与入口

建议新增：

```text
src/context_manager/incidents.py     # 包加载、归一化、去重、复发分组
src/context_manager/receipts.py      # 回执校验、加载状态、条目摘要核对
src/context_manager/behavior.py      # 两个确定性检测器（允许并入 incidents.py）
tests/test_f05_incidents.py
tests/fixtures/f05/                  # 合成事件包、负例和旧格式副本
```

公开入口：

```python
def parse_incident_input(raw_bytes: bytes, *, project_id: str) -> IncidentPackageInput: ...

def load_incident_package(
    path_or_prepared: str | SelectedInput,
    *,
    project_id: str,
    evidence_store: EvidenceStore,
    input_id: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
) -> IncidentPackage: ...

def analyze_incidents(
    package: IncidentPackage,
    *,
    evidence_store: EvidenceStore,
    rules: Sequence[RuleBinding],
    checks: Sequence[ConfirmedBehaviorCheck],
    decisions: DecisionResolver,
    channel_profile: ChannelProfile | None,
    task_scope: Sequence[str] = (),
) -> IncidentAnalysis: ...

def prepare_incidents(
    case_path: str | SelectedInput,
    *,
    project_id: str,
    task_scope: Sequence[str],
    evidence_store: EvidenceStore,
    rules: Sequence[RuleBinding],
    checks: Sequence[ConfirmedBehaviorCheck],
    decisions: DecisionResolver,
    channel_profile: ChannelProfile | None,
    input_id: str | None = None,
) -> IncidentAnalysis: ...
```

`parse_incident_input` 在内存识别旧格式并验证包级结构，不导入证据。F10 预检时调用它；运行内 `load_incident_package` 捕获预读对象，经 F02 `read_raw` 校验同一份字节后解析并导入。

`prepare_incidents` 是 F10 使用的便捷入口，等价于 `load_incident_package` 后调用 `analyze_incidents`。`input_id` 缺省时由 F05 生成 `case-<sha256(规范路径)[:8]>`；同一路径内容变化会触发 F02 的 `input_id` 冲突，调用方应显式传入新 ID。测试可以直接调用拆分后的函数：

- `normalize_incident`：单个事件转成 `NormalizedIncident`。
- `deduplicate_incidents`：返回 `DedupResult`。
- `validate_receipts`：返回 `ReceiptValidation` 列表。
- `resolve_load_observations`：返回 `LoadObservation` 列表。
- `evaluate_behavior`：返回 `BehaviorObservation`。
- `group_recurrence`：返回 `RecurrenceGroup` 列表。

所有公开结果对象提供 `to_dict()`，字段只使用契约和本规格给出的名称，不输出宿主绝对路径。

## 2. 输入边界

### 2.1 选定文件

- 事件包是用户显式选定的单个文件，由 `--case FILE` 指定；一次 `diagnose` 只接收一个包，不合并多个包。
- 用 F02 的 `capture_selected_input` 捕获，单文件上限与 F02 的 `max_selected_input_bytes` 一致：默认 `10 MiB`；F05 的 `max_bytes` 只允许降低。输入的路径与大小检查统一由 F02 完成；超过上限返回 `input_too_large`，退出码 `2`。恰好等于上限时允许。
- 解析必须读取 F02 已捕获的原始字节，不能重新打开原文件。F05 依赖 F02 提供的 `read_raw(evidence_id, *, max_bytes)`；该内部接口不受模型工具的 8 KiB 展示上限影响。
- 只接受常规文件。最终路径组件是符号链接、目录、设备文件或不可读时，分别返回 `not_regular_file`、`symlink_rejected`、`file_not_found`，退出码 `2`。
- 文件按 UTF-8 严格解码；允许并剥离 UTF-8 BOM，记录 `bom_stripped: true`。非法 UTF-8、JSON 语法错误、重复 JSON 键分别返回 `invalid_utf8`、`invalid_json`、`duplicate_json_key`。
- JSON 顶层必须是对象。未知顶层字段保留到 `package.extra_fields` 并写入 `warnings`，不因前向兼容字段失败。未知枚举值按契约错误拒绝。
- 条目数量上限：`5,000` 个事件、`5,000` 份回执、`1,000` 个包内文件。超过返回 `max_items_exceeded`，退出码 `2`。

### 2.2 包结构

```json
{
  "format": "context-manager-incidents",
  "schema_version": 1,
  "synthetic": true,
  "project_id": "demo",
  "task_scope": ["clients/"],
  "channel_profile": {
    "profile_id": "synthetic-codex",
    "profile_version": "1",
    "source": "synthetic",
    "channels": {
      "codex": ["project_instructions", "imported_instructions", "rule_directories", "session_injection"]
    }
  },
  "files": [],
  "incidents": [],
  "receipts": [],
  "check_results": []
}
```

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `format` | 是 | 必须精确等于 `context-manager-incidents`；不匹配按旧格式探测，仍不匹配则 `unsupported_format` |
| `schema_version` | 是 | 整数 `1`；其他整数 `unsupported_schema_version`，退出码 `2` |
| `synthetic` | 是 | 必须为 `true`。首版不接收真实会话；`false` 返回 `not_synthetic`，退出码 `2` |
| `project_id` | 是 | 与调用参数一致；不一致返回 `project_id_mismatch`，退出码 `2` |
| `task_scope` | 否 | 前缀数组，元素按 §2.3 的 `path` 规则校验；非法前缀返回 `unsafe_path`。缺省时用调用参数的 `task_scope` |
| `channel_profile` | 否 | 见 §5.3；与调用参数二选一，参数优先 |
| `files` | 是 | 包内自包含的历史材料；可为空数组 |
| `incidents` | 是 | 事件数组；可为空数组，输出 `empty_package` 警告 |
| `receipts` | 否 | 回执数组，默认 `[]` |
| `check_results` | 否 | 结构化检查结果，默认 `[]` |

`schema_version` 只表示 F05 包格式，不代表产品版本。

`load_incident_package` 返回的 `IncidentPackage` 保存：`package_format`、`project_id`、`input_id`、`package_sha256`、`raw`（解析后的 JSON）、`files`（`file_ref -> Evidence` 映射）、`incidents`、`receipts`、`check_results`、`channel_profile`、`task_scope`、`legacy`、`warnings`、`extra_fields`。后续函数只读该对象，不再访问原文件。

### 2.3 文件条目与引用

`files[]` 条目：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `file_ref` | 是 | 包内唯一逻辑 ID，区分大小写；只用于包内引用，不是磁盘路径 |
| `path` | 是 | 项目根相对路径，`/` 分隔；拒绝绝对路径、`..`、`.`、空段、反斜杠和结尾 `/` |
| `revision` | 是 | 版本标签，例如 `2026-10-01`、`after-edit-001`；同一 `path` 可以有多份 |
| `kind` | 是 | `rule`、`code`、`log`、`config`、`diff`、`event_source` |
| `encoding` | 否 | 只支持 `utf-8`，默认 `utf-8` |
| `content` | 是 | UTF-8 文本；换行原样保留。首版不收二进制 |
| `sha256` | 否 | 用于交叉核对；写入时按实际 `content` 字节重新计算，声明值不参与判定 |
| `snapshot_id` | 否 | 逻辑快照标签；可被事件的 `snapshot_id` 引用 |

- 包内所有引用只能写 `{"file_ref": "...", "line_start": 1, "line_end": 3}`，不允许写 `evidence_id` 或绝对路径。`evidence_id` 是本次运行生成的，跨包引用无效。
- 未找到 `file_ref` 的引用是缺口，不是包级失败：动作引用缺失使该事件行为未知；回执条目缺失使该回执 `rejected`。
- 行号从 1 开始、两端包含；`line_start > line_end`、越界或 `0` 都是 `entry_unresolved` / `ref_unresolved`，不得截断后继续。
- 包内历史内容按 F02 的 `capture_imported_bytes` 导入，`input_id` 取本次选定的 `input_id`，`logical_name` 使用 `case/<revision>/<path>`，`provenance.kind = "synthetic"`。F02 重新计算 `sha256` 和 `span_sha256`，生成新的 `evidence_id`。包里的 `sha256` 只用于 `embedded_digest_mismatch` 警告，不能替代 F02 的计算结果。
- 同一 `path` 的当前文件与历史文件分别保存：当前项目文件由 F02 常规捕获，包内历史内容走导入入口；两者的 `evidence_id`、`snapshot_id` 和 `origin` 不同，报告分别展示。

### 2.4 事件与动作

`incidents[]` 是 `IncidentInput`，不是契约中字段全部必填的 `Incident`。这样设计是为了让缺失材料保留为缺口，而不是让整包在 F01 校验阶段被拒绝。

| 字段 | 必填 | 缺失时 |
| --- | --- | --- |
| `event_id` | 是 | 包级错误 `invalid_field`，位置 `incidents[i].event_id` |
| `session_id` | 是 | 包级错误 |
| `harness` | 是 | 包级错误；必须是 `shared` 或工具名 |
| `action` | 是 | 包级错误 |
| `task_id` | 否 | 保留事件，`missing_task_id`，降低任务独立性结论 |
| `attempt_id` | 否 | 保留事件，`missing_attempt_id` 警告；不影响同一任务内的去重 |
| `occurred_at` | 否 | 保留事件，`missing_occurred_at`；该事件的行为与加载状态为 `unknown` |
| `context_epoch` | 否 | 保留事件，`missing_context_epoch`；不能确认加载或未加载 |
| `snapshot_id` | 否 | 保留事件，`missing_snapshot` 警告；不影响已解析的动作引用 |
| `source_ref` | 否 | 保留事件，`missing_source_ref` 警告；报告标注事件原文不可回查 |
| `scope` | 否 | 前缀数组，元素按 §2.3 的 `path` 规则校验；缺省使用包的 `task_scope`，再缺省使用 CLI 范围；实际分析取与 CLI 范围的交集 |
| `exception_rule_ids` | 否 | 显式例外规则 ID 数组，默认空；只在 F04 确认这些规则适用且约束解析为 resolved 时输出 exception，不从路径名或文本猜测 |

`action` 字段：

| `kind` | 必填 | 可选 | 说明 |
| --- | --- | --- | --- |
| `edit` | `target_path`、`after_ref` 或 `before_ref` 至少一个 | `before_ref`、`after_ref`、`diff_ref`、`evidence_ref`、`change` | 实际编辑。`after_ref` 缺失且不是删除时，行为未知 |
| `tool_result` | `target_path`、`result_ref` | `tool`、`command`、`exit_code`、`evidence_ref` | 命令或工具结果。只有配套的已确认 `structured_check_result` 才能确认违规 |
| `mention` | `target_path` | `quote_ref`、`text`、`evidence_ref` | 文档提及、注释、搜索命中说明。永不进入违规计数 |

- `action.evidence_ref` 可由 `after_ref`、`result_ref` 或 `quote_ref` 补全；都没有时 `missing_action_evidence`，该动作只能作为线索。
- `change` 只允许 `create`、`modify`、`delete`；缺省时按有无 `before_ref` / `after_ref` 推断，推断结果写入 `NormalizedIncident.action.change`。
- `before_ref`、`after_ref` 指向的 `files[].path` 必须等于动作的 `target_path`；不同为 `ref_path_mismatch`，该动作的行为为 `unknown`。`result_ref`、`diff_ref` 和 `quote_ref` 不要求路径相同。
- `target_path` 必须是项目根相对路径，规则同 §2.3 的 `path`；越界、绝对路径、符号链接路径不解析，事件记为 `invalid_action`。
- `action.claims`（例如 `added_literal`、`claimed_violation`、`diff_text`）只作为线索保存，不能用于计数。`diff_ref` 指向的差异文本同样只作为线索。
- 动作时间、顺序和 `snapshot_id` 不做推断。事件缺少 `occurred_at` 时，不能通过数组下标补时间。
- `snapshot_id` 存在但不对应任何 `files[].snapshot_id` 时记 `snapshot_unresolved` 警告，仍保留事件；不因标签缺失把动作当成已证明。
- `source_ref` 指向包内原始事件摘录（`kind = "event_source"`），用于报告回查；它不证明动作已执行。存在但无法解析时记 `ref_unresolved`。

### 2.5 回执

`receipts[]` 对应 `LoadReceipt`，字段如下。包内 provenance 保留以下输入形状；输出映射为公共契约的 `Provenance {kind, adapter, version}`。

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `receipt_id` | 是 | 包内唯一；重复且内容完全相同去重并警告，重复但内容不同全部 `rejected`，记 `duplicate_receipt_id` |
| `session_id` | 是 | 必须与事件一致 |
| `harness` | 是 | 必须与事件一致 |
| `context_epoch` | 是 | 必须与事件一致；缺失或不同不能证明加载 |
| `before_event_id` | 是 | 目标事件或同一 session、同一 epoch 中较早的事件；表示“在该动作之前捕获” |
| `captured_at` | 否 | 带时区 ISO 8601；有值时不得晚于事件时间 |
| `coverage` | 是 | `complete`、`partial`、`unknown`；只是声明值，是否有效由 §5.3 计算 |
| `channels_covered` | `coverage = complete` 时必填 | 渠道名数组；必须覆盖 `channel_profile` 给该 harness 的全部渠道 |
| `entries` | 是 | 实际覆盖的规则片段；允许空数组 |
| `provenance` | 是 | `{"synthetic": true, "adapter": "...", "protocol_version": "..."}` |

`entries[]`：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `rule_id` | 是 | 被覆盖规则的 ID；允许与目标规则 ID 不同，但会记录警告 |
| `source_ref` | 是 | 包内文件引用，指向实际规则文本 |
| `text_sha256` | 是 | 该行段原始字节的 SHA-256，必须等于 F02 `EvidenceRead.span_sha256` |
| `channel` | 是 | `project_instructions`、`imported_instructions`、`rule_directories`、`session_injection` 之一 |
| `scope` | 否 | 条目适用的路径前缀；缺省表示该动作的完整上下文 |

- 回执只能由事件通过 `before_event_id` 关联；事件本身没有 `receipt_id` 字段。多份回执可以指向同一事件。
- 回执的 `coverage` 完整性和 `channels_covered` 必须来自已知输入协议或测试夹具适配器，不能由分析模型补写。`provenance.adapter` 为空时，即使写了 `complete` 也只能按 `partial` 使用，并记 `coverage_unattested`。
- 只有 `receipt_id`、没有回执内容的旧格式记录，标为 `missing_receipt` / `legacy_receipt_only`，不生成 `observed_loaded`。

### 2.6 结构化检查结果

`check_results[]` 只在 `detector = "structured_check_result"` 时使用：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `check_id` | 是 | 必须对应已确认的 `ConfirmedBehaviorCheck` |
| `event_id` | 是 | 必须对应该规范事件 |
| `input_ref` | 否 | 包内文件引用；缺省时取 `edit.after_ref` 或 `tool_result.result_ref` |
| `input_sha256` | 是 | 输入文件**完整原始字节**的 SHA-256，不是行段摘要 |
| `status` | 是 | `passed`、`failed`、`error`、`not_run` |
| `observed` / `expected` | 否 | 仅供展示，不参与计数 |
| `evidence_ref` | 否 | 检查结果原文在包内的位置 |

- 检查结果必须由已确认检查产生，且输入摘要、事件和检查 ID 三者都匹配。任何一项不匹配都不能计数。
- `error` 和 `not_run` 是未完成，不是“通过”，也不是“违规”。
- 同一 `(check_id, event_id)` 的多条结果若状态和输入摘要一致则去重；不一致返回 `conflicting_check_results`，该检查状态为 `unknown`。

### 2.7 旧示意格式

`examples/stale-users-api/case.json` 是早期格式。探测顺序：

1. 有 `format` 字段：按 §2.2 校验；`format` 值不认识时 `unsupported_format`，不再猜旧格式。
2. 无 `format`，但有 `case_id`，且 `incidents[]` 至少一项含 `rule_observation`：识别为 `legacy-demo-v0`；此时 `schema_version` 必须缺失或为 `1`，否则按 `unsupported_schema_version` 拒绝。
3. 其他情况：`unsupported_format`。

旧格式导入规则：

- 包级 `synthetic` 必须为 `true`；`case_id`、`symptom`、`scope`、`snapshot`、`decisions` 保留到 `legacy.metadata`。
- 旧格式没有 `project_id` / `task_scope`：`project_id` 取调用参数并记 `project_id_source = "cli"`；`task_scope` 取顶层 `scope`，没有则用调用参数。
- `incidents[].action.path` 转成 `NormalizedIncident.action.kind = "edit"`、`target_path`；`added_literal` 只放入 `action.claims`，`claim_only = true`。
- `rule_observation.receipt_id` 只生成 `legacy.claims`，`verified_state` 固定为 `unknown`，原因 `legacy_receipt_only`；`rule_observation.state` 原文保留为 `claimed_state`，不得升级为 `observed_loaded` 或 `observed_not_loaded`。
- 旧格式没有 `before_ref`、`after_ref`、`source_ref`、`context_epoch`，全部记对应缺口；事件仍然保留，用于展示线索和缺口。
- `loads`、`behaviors` 为空数组；`exclusions` 中每个事件记 `legacy_claim`。这是有意的降级，不是解析失败。
- 输出 `package_format = "legacy-demo-v0"`、`legacy.requires_migration = true`，并给出迁移提示：补 `format`、`schema_version`、完整文件条目、动作前后引用和可校验回执。
- 旧格式的 `snapshot` 只是标签，不是 F02 快照；记录 `legacy_snapshot_label` 警告，不参与版本对照。
- 现有示例的 `expected-report.md` 目前把 `added_literal` 和只有 ID 的回执写成 `supported` / `observed_loaded`，与本节冲突。F05 不直接修改示例；F10 必须按真实 CLI 输出重写该报告并保留“旧格式已降级”的说明。

## 3. 处理流程

1. 读取并校验包外壳；计算 `package_sha256`，登记 `input_id`，识别 `v1` 或 `legacy-demo-v0`。
2. 用 F02 的 `capture_imported_bytes` 导入 `files[]`，重新计算摘要，建立 `file_ref -> Evidence` 映射。导入失败的文件保留条目和缺口，不终止整包。
3. 归一化每个事件：解析动作引用、时间、范围、上下文 epoch，生成 `NormalizedIncident` 和逐事件缺口。
4. 去重并确定规范事件；冲突事件不参与后续确认。
5. 校验回执，按“事件 × 适用规则”生成 `LoadObservation`。
6. 对每个规范事件，按已确认检查和有效决策生成 `BehaviorObservation`；只有 `violation` 计数。
7. 按规则、范围、违规类型和任务分组，计算复发候选、独立任务数和缺口。
8. 汇总覆盖、排除项和警告，按固定顺序输出。全程不写项目文件。

处理顺序固定，保证同一输入在不同机器上产生相同语义结果；不读取当前时间，不把数组下标当作事件顺序。

## 4. 归一化与去重

### 4.1 `NormalizedIncident`

```text
event_id, canonical_event_id, session_id, task_id?, attempt_id?,
occurred_at?, harness, context_epoch?, snapshot_id?,
action{kind, target_path, change, before_ref?, after_ref?, result_ref?,
       quote_ref?, diff_ref?, tool?, command?, exit_code?, claims{}, claim_only},
scope[], source_evidence_id?, status, duplicate_of?, gaps[], synthetic
```

- `status`：`canonical`、`duplicate`、`conflicted`、`invalid`。`invalid` 事件保留在 `events` 和原始计数中，但不进入规范计数、回执或行为评估。
- `canonical_event_id` 在去重后指向保留事件；`duplicate` 事件保留 `duplicate_of`。
- `scope` 规范化成排序后的前缀数组；`["."]` 表示项目根，是唯一允许的 `.` 值。
- 事件按 `(occurred_at is None, occurred_at, event_id)` 排序；时间缺失的事件排在有时间事件之后，不能据此推断“之后发生”。
- 归一化不修改包内原文摘要；引用只新增 F02 的 `EvidenceRef`。

### 4.2 稳定内容签名

去重签名 `signature_v1` 对以下规范 JSON 求 SHA-256：

```json
{
  "kind": "edit",
  "target_path": "clients/a.py",
  "before_sha256": "…或 null",
  "after_sha256": "…或 null",
  "result_sha256": "…或 null",
  "tool": "shell 或 null",
  "command": "规范化后的命令或 null"
}
```

- 编辑事件的 `before_sha256` / `after_sha256` 取自 F02 导入后的证据摘要；缺失时为 `null`。
- `tool_result` 的 `result_sha256` 取自 `result_ref`；`mention` 使用引用摘要或规范化后的 `text` 摘要。
- **不把** `action.claims`、`added_literal`、`claimed_violation`、`diff_text` 放进签名。复制日志时附加的说明不能让同一动作变成两次。
- 签名中的 `command` 只做空白折叠和大小写保留的规范化，不执行、不解析 shell。
- 签名强弱由引用完整度决定：有实际前后快照为 `strong`，只有 `claim_only` 为 `weak`；`weak` 签名只用于旧格式去重，不能确认违规。

### 4.3 去重与冲突

1. 先按 `event_id` 去重。内容签名相同：保留 `occurred_at` 最早的一条，其余标 `duplicate`，原因 `same_event_id`。内容签名不同：全部标 `conflicted`，原因 `duplicate_event_conflict`，不进入确认。
2. 再按 `(project_id, task_id, signature_v1)` 去重。`task_id` 缺失时退化为同一 `session_id` 内按签名合并，跨 session 不合并，并记 `task_identity_incomplete`；不把同一 session 的重复记录当成多个独立任务。
3. 同一 `task_id` 的重试、跨 session 的同任务记录、复制日志和重复导入合并为一条规范动作。不同 `task_id` 的相同签名不合并。一次 `diagnose` 只加载一个包；包内重复记录模拟“重复导入”，F05 不跨运行合并事件。
4. `attempt_id` 不参与去重键；同一任务的不同尝试仍属于同一任务。
5. 去重结果保留全部原始 `event_ids`，报告可以显示“原始 4 条，规范 1 条，重复 3 条”。

## 5. 回执校验与加载状态

### 5.1 身份、时序与内容

回执必须同时满足：

| 检查 | 不满足时的结果 |
| --- | --- |
| `receipt_id` 非空且可识别 | 字段缺失是包级 `invalid_field`；旧格式只有 ID 时为 `dangling_receipt`，状态 `unknown` |
| 重复 `receipt_id` 且内容完全相同 | 去重并记 `duplicate_receipt_id` 警告，不改变状态 |
| 重复 `receipt_id` 但内容不同 | 两份都 `rejected`，记 `duplicate_receipt_id` |
| `session_id` 与事件一致 | `receipt_session_mismatch`；`rejected` |
| `harness` 与事件一致 | `receipt_harness_mismatch`；`rejected` |
| 事件 `context_epoch` 存在且与回执一致 | `missing_context_epoch` / `receipt_epoch_missing` / `receipt_epoch_mismatch`；`unknown` |
| `before_event_id` 解析到目标事件，或同一 session、同一 epoch 中较早的事件 | `receipt_anchor_mismatch` / `receipt_after_action`；`rejected` |
| `captured_at` 不晚于事件 `occurred_at` | `receipt_after_action`；`rejected` |
| 事件 `occurred_at` 存在 | `missing_occurred_at`；`unknown` |
| `provenance.synthetic == true` 且 `adapter` 非空 | `provenance_missing`；`rejected` |

- `before_event_id` 先经事件 ID 去重映射到规范事件。重复事件的回执归入同一个规范事件；事件 ID 冲突（同 ID 不同内容）时无法锚定，按 `conflicted_event` 保持未知。
- `before_event_id` 指向不存在的事件：`dangling_receipt`（兼容别名 `receipt_anchor_missing`），`rejected`。
- `before_event_id` 指向本事件之后的事件：`receipt_after_action`，`rejected`。
- `before_event_id` 指向本事件之前的另一个事件：只有当它属于同一 session、同一 `context_epoch`，且 `captured_at` 缺失或早于目标动作时才有效；记 `receipt_anchor_offset` 警告。不同 epoch 的回执按 `receipt_epoch_mismatch` 保持 `unknown`，不推测中间没有压缩。
- `before_event_id` 指向另一 session 的事件：先按 `receipt_session_mismatch` 拒绝，不进入锚点偏移判断。
- 锚点等于目标事件时表示“动作前即时捕获”，不记偏移警告。
- `context_epoch` 不同表示回执可能来自另一次压缩上下文；即使内容摘要匹配，也保持 `unknown`，原因 `receipt_epoch_mismatch`。
- 回执没有 `captured_at` 但锚点等于目标事件时，顺序由锚点确定，允许继续，记 `captured_at_missing` 警告。

### 5.2 条目与摘要

对每个 `entries[]`：

1. `source_ref` 必须解析到本次导入的文件和合法行段；否则 `entry_unresolved` / `entry_range_invalid`。
2. 用 F02 `read_evidence` 读取该行段，取 `span_sha256`。读取结果 `truncated == true` 时条目为 `entry_unresolved`，不能拿截断文本算摘要；完整读取的摘要必须等于条目 `text_sha256`，不等为 `entry_digest_mismatch`，该条目不参与匹配。
3. `channel` 必须是 §5.3 的渠道枚举。`channel_profile` 可用时还必须属于该 harness 的 `required_channels`，否则 `entry_channel_mismatch`；profile 缺失时保留条目并记 `channel_set_unknown` 警告，该条目只能用于 `observed_loaded`，不能用于 `observed_not_loaded`。
4. `rule_id` 允许与目标规则不同，但匹配成功后记 `entry_rule_id_mismatch` 警告，报告保留实际条目 ID。
5. 条目 `scope` 若存在，必须覆盖动作 `target_path`；否则 `entry_scope_mismatch`。
6. 任一条目无效时，该回执仍可凭其他有效匹配条目证明 `observed_loaded`，但不能作为 `observed_not_loaded` 的完整排除证明；报告保留该条目的缺口。

只有条目本身通过 1–3 才可用于加载证明。任何无效条目都不能被“忽略后当作完整清单”；`observed_not_loaded` 要求该回执的全部条目都有效。

### 5.3 覆盖完整性

`channel_profile` 是已知输入协议或合成适配器声明的渠道集合：

```python
@dataclass(frozen=True)
class ChannelProfile:
    profile_id: str
    profile_version: str
    source: str                       # "f03" | "synthetic"
    channels: Mapping[str, tuple[str, ...]]  # harness -> 渠道名
```

- 渠道名为 `project_instructions`、`imported_instructions`、`rule_directories`、`session_injection`。优先使用显式传入的合成适配器 profile，其次事件包的显式合成 profile，再次 F03 已明确声明完整渠道的合成 profile。不从静态发现字段推导完整渠道。
- `required_channels = channel_profile.channels[event.harness]`。profile 缺失、harness 未知或列表为空时，`not_loaded` 不能成立，原因 `channel_set_unknown`。
- `coverage_effective` 计算：
  - 声明 `complete` + `provenance.adapter` 非空 + `channels_covered` 覆盖 `required_channels` → `complete`。
  - 声明 `complete` 但缺渠道 → `partial`，原因 `coverage_channel_gap`。
  - 声明 `complete` 但无适配器 → `partial`，原因 `coverage_unattested`。
  - 声明 `partial` / `unknown` → 原值。
- 完整性不能由条目数量、条目为空、模型输出或“没有找到”反推。`complete` 是输入协议的事实，不是分析结论。

### 5.4 加载状态判定

对每个 `(规范事件, 适用规则)`，按以下顺序取首个匹配项；冲突判定先于单份回执结论：

| 条件 | `state` | `load_proof` |
| --- | --- | --- |
| 没有任何锚定该事件的回执 | `unknown` | `none`，`missing_receipt` |
| 只有旧格式的 `receipt_id` | `unknown` | `none`，`legacy_receipt_only` |
| 所有回执都被拒绝 | `unknown` | `none`，合并拒绝原因 |
| 规则绑定缺少 `text_sha256` 或摘要格式无效 | `unknown` | `none`，`rule_binding_digest_missing` |
| 一份有效完整回执排除规则，另一份有效回执又包含规则 | `unknown` | `none`，`conflicting_receipts` |
| 至少一份 `verified` 回执含有效匹配条目 | `observed_loaded` | `matched_entry` |
| 无匹配条目，且至少一份 `verified` 回执 `coverage_effective = complete`、条目全部有效 | `observed_not_loaded` | `complete_exclusion` |
| 无匹配条目，仅 `partial` / `unknown` 覆盖 | `unknown` | `none`，`coverage_incomplete` |

匹配条目的条件：条目有效 + `span_sha256 == rule.text_sha256` + `channel` 在 `required_channels` 内（profile 缺失时只要求渠道属于已知枚举）+ 条目 `scope` 覆盖目标路径。`rule_id` 不同只记警告，不阻止加载事实。

- 用于 `observed_not_loaded` 的回执还必须没有 `entry_unresolved`、`entry_range_invalid`、`entry_digest_mismatch`、`entry_channel_mismatch` 或 `entry_scope_mismatch` 条目；否则只能判 `unknown` 并保留对应缺口，不能把无效条目当作“清单里没有”。
- 完整清单中存在同一 `rule_id`、但摘要不同的旧版本条目时，仍可判 `observed_not_loaded`，并记录 `different_version_in_context`：当前版本不在上下文，旧版本在。
- 完整清单中完全没有相关规则，才是普通的 `observed_not_loaded`。
- `observed_loaded` 不要求 `coverage = complete`；只要条目本身被明确覆盖即可。`observed_not_loaded` 才要求完整渠道清单。
- 回执因条目失败而不能用于证明时，`LoadObservation.reasons` 除覆盖原因外还保留对应的条目码（如 `entry_digest_mismatch`），`gaps` 保留完整列表；这样报告能区分“清单不全”和“摘要不对”。
- 所有 `LoadObservation` 都带 `synthetic: true`、`proof_scope: "synthetic_case"`，并保留 `receipt_ids`、`matched_entry_index` 和 `coverage_effective`。

### 5.5 多份回执

- 同一事件的多份回执先各自校验；加载证明取“最强且不矛盾”的结果。
- 若一份 `observed_loaded`、另一份 `observed_not_loaded`，且两者都有效，结果为 `unknown`，原因 `conflicting_receipts`；报告同时保留两份回执，不选边。
- 多份都证明加载时，选 `receipt_id` 字典序最小的一份作为 `matched_receipt_id`，保证输出稳定。
- 未被任何事件锚定的回执仍输出 `ReceiptValidation`，`referenced_by_event_ids = []`，状态按自身字段校验，不影响加载结论。

## 6. 行为判定

### 6.1 输入：已确认的行为检查

```python
@dataclass(frozen=True)
class RuleBinding:
    rule_id: str
    text: str
    text_sha256: str          # F02 span_sha256，原始字节
    scope: Scope
    harness: str
    source_ref: EvidenceRef
    interpretation_status: str  # 只接受 structured_confirmed

@dataclass(frozen=True)
class ConfirmedBehaviorCheck:
    check_id: str
    rule_id: str
    subject: str
    detector: str             # python_assignment_literal | structured_check_result
    scope: Scope
    parameters: Mapping[str, object]
    constraint: Constraint    # subject/operator/value/conditions
    decision_id: str | None
    confirmation_ref: str
```

- 只处理 `interpretation_status == "structured_confirmed"` 的规则；其他规则不进入确定性结论，记 `rule_not_confirmed`，可作为 F06/F07 的语义线索。
- 只处理带 `confirmation_ref` 的检查；模型建议的检查不进入 F05，记 `check_not_confirmed`。
- 检查的 `rule_id` 必须在 `rules` 中；否则 `check_rule_not_in_scope`，不生成观察。
- `parameters.path` 是可选的精确路径过滤器；存在且不等于 `action.target_path` 时跳过该检查（不生成观察）。

`DecisionResolver` 由 F04 实现。F05 用 `policy_at(occurred_at)` 取得历史策略，再调用 F04 的 `resolve_applicable_rules(policy, relative_path, *, harness)` 和 `resolve_constraint(policy, subject, relative_path, *, harness, exception_rule_ids=())`。约束结果状态为 `resolved/none/conflict/unknown`。范围先由 F05 按任务和检查声明判定，显式兼容例外 ID 才进入 `exception_rule_ids`。

F05 的处理：

| `status` / 标志 | 行为观察 |
| --- | --- |
| `resolved`，`exception_applied = false` | 执行检测器 |
| `resolved`，`exception_applied = true` | `exception`，`exception_applied` |
| 目标路径越出已验证的适用范围 | `not_applicable`，`out_of_scope` |
| `none` | `unknown`，按已记录的原因区分 `decision_not_found` 或 `decision_not_effective` |
| `conflict` | `unknown`，`decision_conflict` |
| `unknown` | `unknown`，`decision_unresolved` 并保留 F04 缺口 |
| 其他 | `unknown`，`decision_unresolved` |

兼容例外须由明确登记的例外规则 ID 与范围证实；命中时返回 `exception_applied = true`。既不在适用范围也无已证实例外时为 `out_of_scope`。这样 `legacy/` 的合法 v1 调用会保留为 `exception`，而不是被当成普通不适用。

事件缺少 `occurred_at` 时不调用 `resolve_constraint`；所有相关检查直接 `unknown` + `missing_occurred_at`，不补当前时间，也不按数组顺序推断事件时间。

### 6.2 `python_assignment_literal`

参数：`{"symbol": "USERS_PATH"}`；可选 `path` 精确过滤。只解析 Python 源码文本，不导入、不执行、不调用项目代码。`python_assignment_literal` 只处理 `edit` 动作；`tool_result` / `mention` 返回 `not_applicable` + `action_not_executed`，不读取结果文本。`target_path` 不是 `.py` 时返回 `unknown` + `unsupported_language`，不退回文本搜索。

1. 取动作的 `after_ref`。若事件是删除（`change = delete`）且 `before_ref` 存在，读 before；否则 after 缺失为 `missing_after_snapshot`，状态 `unknown`。
2. 用 `ast.parse` 解析源码。语法错误为 `parse_error`，状态 `unknown`。
3. 只接受模块顶层的 `Assign` 或 `AnnAssign`，目标为单个 `Name`，值为 `ast.Constant` 字符串。函数内、类内、条件分支、循环、`try` 内的赋值不作确定值；多次顶层赋值时取最后一条无条件赋值，并在 `reasons` 记 `multiple_assignments`（若值不同）。
4. 符号不存在：
   - after 中没有该符号，但 before 存在且符号存在 → `no_violation`，原因 `symbol_removed`（这是修复删除，不是违规）。
   - after 和 before 中都没有该符号 → `unknown`，`symbol_not_found`。
   - after 缺失但 before 存在且 `change = delete` → `no_violation`，`symbol_removed`。
5. 值不是字符串常量 → `unknown`，`symbol_not_string`。
6. 期望值来自 `ConstraintResolution.value`。比较运算符优先取 `ConstraintResolution.operator`，缺失时取 `constraint.operator`；两者都存在且不同为 `operator_mismatch`，状态 `unknown`：
   - `equals`：`observed != expected` → `violation`，`violation_kind = literal_mismatch`。
   - `equals`：`observed == expected` → `no_violation`；若 before 值曾经违反同一约束，追加原因 `value_corrected`（这是修复）。
   - `not_equals`：`observed == expected` → `violation`，`violation_kind = literal_mismatch`。
   - `not_equals`：`observed != expected` → `no_violation`；若 before 值曾经违反同一约束，追加原因 `value_corrected`。
   - 其他运算符 → `unknown`，`unsupported_operator`。
7. 如果 before 也能解析，输出 `change.before_value` / `after_value`；before 解析失败只记警告，不影响 after 的确定值。
8. 注释、文档字符串、其他变量中的 `v1` 字样不参与比较；AST 只读取指定符号的常量。diff 文本声称新增 `v1`、但 after 常量为 `v2` 时，以 after 字节为准，状态 `no_violation`，原因 `claim_contradicted_by_source`。

示例：`USERS_PATH = "/api/v1/users"  # v1` 在 after 中，期望 `/api/v2/users` → `violation`；`USERS_PATH = "/api/v2/users"  # v1` → `no_violation`；`# USERS_PATH = "/api/v1/users"` → `symbol_not_found`，不是违规。

### 6.3 `structured_check_result`

1. 找到 `check_results[]` 中 `check_id` 和 `event_id` 都匹配的记录。`event_id` 先经去重映射到规范事件；指向 `duplicate` 的记录归入规范事件，指向 `conflicted` 或 `invalid` 的记录不能确认。`mention` 动作返回 `not_applicable` + `action_not_executed`，不把正文当检查结果。
2. 确定输入：`input_ref`，缺省取 `edit.after_ref` 或 `tool_result.result_ref`。没有输入 → `check_input_missing`。`input_ref` 只能指向完整文件；带 `line_start` / `line_end` 时 `check_input_invalid`，不能只验部分片段。
3. 用 F02 读取输入文件的完整原始字节，计算 SHA-256，必须等于 `input_sha256`；不等为 `check_input_digest_mismatch`。
4. `status = failed` → `violation`，`violation_kind = check_failed`；`passed` → `no_violation`；`error` / `not_run` → `unknown`，`check_result_inconclusive`。
5. 没有结果记录 → `unknown`，`check_result_missing`；`check_id` 不在已确认检查中 → `unknown`，`unconfirmed_check`；`event_id` 指向别的事件 → `unknown`，`check_action_mismatch`。
6. 同一检查、同一事件出现冲突结果 → `unknown`，`conflicting_check_results`。`observed` / `expected` 只展示，即使它们与 `status` 矛盾，也不改变由已确认状态得到的结论，但要写 `result_text_mismatch` 警告。

### 6.4 明确不计数的情况

| 情况 | 观察状态 / 原因 | 排除原因 |
| --- | --- | --- |
| `mention` 文档、注释、搜索命中说明、撤回说明 | `not_applicable` / `mention_not_execution` | `mention` |
| `tool_result` 只是搜索、`cat`、列目录、读取规则文件，没有结构化检查结果 | `clue_only` / `tool_result_without_check` | `tool_result_only` |
| 修复编辑删除旧调用、删除旧符号或改正旧值 | `no_violation` / `symbol_removed` 或 `value_corrected` | `fix_removal` |
| 兼容目录中的合法旧值 | `exception` / `exception_applied` | `exception` |
| 路径不在适用范围 | `not_applicable` / `out_of_scope` | `out_of_scope` |

### 6.5 代码版本对照

事件可带 `code_version: {observed_ref, required_ref, decision_id}`。`observed_ref` 必填，另外两项可省略。两个引用使用包内 `file_ref`，由 F05 绑定到 `action.target_path` 的完整代码快照。F07 核对 `observed_ref` 与编辑的 `before_ref` 或工具结果的 `result_ref` 相同，再检查 `decision_id` 对目标路径的版本要求。该对照不会增加行为违规或复发计数。

版本要求复用已确认的登记决策。约束的 `subject` 为 `code_snapshot_sha256`，`operator` 为 `equals`，`value` 为要求版本的全文件 SHA-256，`conditions` 为 `{"relative_path": "clients/app.py"}` 这类精确路径绑定。版本材料缺失、决定未生效或相互矛盾时保留未知。
| 只在 diff / 注释中出现 v1，实际常量为 v2 | `no_violation` / `claim_contradicted_by_source` | `text_claim_only` |
| 动作只有 `added_literal` 等自述 | `clue_only` / `claim_only` | `unverified_claim` |
| 未确认检查或模型建议的检查 | 不生成观察，记缺口 | `unconfirmed_check` |
| 未支持的检测器或语言 | `unknown` / `unsupported_detector` | `unsupported_detector` |

- `exclusions` 是按事件或事件×检查记录的“为什么没有计数”，不是 Finding。重复事件也记 `duplicate`。
- 一次 `cat`、`read`、搜索或普通转录里出现规则文件名，都不等于规则已进入上下文；只有 §5 的回执条目能支持 `observed_loaded`。后续真实会话适配器必须单独证明记录覆盖。
- 合法兼容调用保留事件和观察，`status = exception`，`counts_toward_recurrence = false`；F07 可以据此说明“这是例外，不应生成删除建议”。
- `clients/` 与 `clients-old/` 按路径组件比较；`clients-old/` 不在 `clients/` 范围内，不能用字符串前缀误判为适用。

### 6.5 行为观察

```text
observation_id, event_id, rule_id, constraint_id?, check_id?, detector?,
status, violation_kind?, operator?, observed_value?, expected_value?,
decision_id?, decision_status?, change{before_value?, after_value?, symbol?, path?},
evidence_refs[], reasons[], gaps[], counts_toward_recurrence, synthetic
```

`status` 只取 `violation`、`no_violation`、`exception`、`not_applicable`、`unknown`、`clue_only`。只有 `violation` 且 `counts_toward_recurrence = true` 进入计数。`unknown` 不是“未违规”，不能用于排除规则或生成修复。

## 7. 复发统计

### 7.1 分组键

```text
project_id + rule_id + (constraint_id 或 check_id) + task_scope + violation_kind
```

- `task_scope` 使用事件的有效范围，规范化成排序后的前缀数组；相同集合产生相同键。
- 范围未知时组键中记 `"<unknown>"`，`scope_unknown = true`，不产生复发候选。
- 不同违规类型（`literal_mismatch`、`check_failed`）分开统计。
- 同一规则的不同检查若没有 `constraint_id`，用 `check_id` 区分，避免把两个不同约束合并。

### 7.2 独立任务

- 独立任务由 `task_id` 精确区分，不按 session 拆分，也不做大小写或相似字符串合并。
- 同一 `task_id` 的多次尝试、重试、跨 session 记录、重复导入只算一个任务。
- 缺少 `task_id` 的事件不能人为补 ID；它们保留在动作数和缺口里，但不增加 `confirmed_independent_task_count`。
- 两个事件 `task_id` 不同即视为独立任务；首版没有更强的任务谱系输入，不猜测任务别名。

### 7.3 阈值与状态

首版至少两个独立任务都有已确认违规动作，才输出复发候选。

| 条件 | `status` | `meets_recurrence_threshold` |
| --- | --- | --- |
| 没有已确认违规动作 | `no_confirmed_violation` | false |
| 有违规，但范围未知 | `insufficient_scope` | false |
| 已确认独立任务数 ≥ 2 | `recurrence` | true |
| 已确认独立任务数 < 2，且存在缺失 `task_id` 的违规事件 | `insufficient_task_identity` | false |
| 已确认独立任务数 = 1，任务 ID 完整 | `single_task` | false |

- 已确认独立任务数 ≥ 2 时，即使还有缺失任务 ID 的事件，也满足阈值，但 `task_identity_complete = false`，报告注明“这是下界”。
- 同一任务里两次不同文件、不同签名的违规是两个动作，但任务数仍为 1，状态 `single_task`。
- 重复事件不计入动作数；例外事件、`unknown`、`clue_only` 不计入。
- `confirmed_violation_count` 是去重后的规范事件数；`related_event_ids` 保留原始 ID 列表，方便回查“哪些记录被合并”。

## 8. 输出对象

### 8.1 `IncidentAnalysis`

| 字段 | 说明 |
| --- | --- |
| `format` | `context-manager-incidents` |
| `schema_version` | `1` |
| `synthetic` | 恒为 `true` |
| `proof_scope` | `synthetic_case` |
| `package_format` | `v1` 或 `legacy-demo-v0` |
| `project_id`、`input_id`、`package_sha256` | 本次输入标识 |
| `status` | `complete` 或 `partial`，见 §10 |
| `events` | `NormalizedIncident[]` |
| `dedup` | `DedupSummary` |
| `receipts` | `ReceiptValidation[]` |
| `loads` | `LoadObservation[]` |
| `behaviors` | `BehaviorObservation[]` |
| `recurrence` | `RecurrenceGroup[]` |
| `counts` | 计数摘要 |
| `coverage` | 覆盖摘要 |
| `exclusions` | 未计数原因 |
| `gaps` | 全局缺口 |
| `warnings` | 非阻断警告 |
| `legacy` | 旧格式提示或 `null` |
| `extra_fields` | 包内未识别字段 |

`counts`：

```text
raw_event_count, canonical_event_count, duplicate_event_count,
conflicted_event_count, invalid_event_count, confirmed_violation_actions,
confirmed_independent_tasks, excluded_event_count
```

`raw_event_count = canonical_event_count + duplicate_event_count + conflicted_event_count + invalid_event_count`；只有 `canonical` 事件进入回执、行为和复发评估。

`confirmed_independent_tasks` 是所有复发分组中去重后的已知任务 ID 并集；`excluded_event_count` 是至少有一条 `Exclusion` 的规范事件数，同一事件有多个排除原因只计一次。

`coverage`：

```text
events{total, normalized, evaluated, unknown}
receipts{total, verified, rejected, unknown}
loads{observed_loaded, observed_not_loaded, unknown}
behaviors{violation, no_violation, exception, not_applicable, unknown, clue_only}
mechanical{detectors_supported, checks_run, checks_unsupported,
           checks_unconfirmed, languages}
```

`mechanical` 明确写出 F05 只机械支持 Python 字符串常量和结构化检查结果；其他语言和复杂业务条件留在 F06/F07 的语义候选，不能算作机械覆盖。

F05 另输出 `coverage_items: CoverageItem[]`，供 F10 透传，保留上面的细粒度计数。每项按已请求检查计算，失败或未执行时带稳定原因。

| check_id | 已完成 | 存在缺口 |
| --- | --- | --- |
| `f05.bundle` | 包级结构已验证并完成事件整理 | 局部材料无效或旧格式缺材料时 `failed` |
| `f05.receipts` | 所有需要的回执已核验；没有适用事件为 `passed/none_found` | 缺失、悬空、不可用或冲突时 `not_run` |
| `f05.loads` | 适用加载观察均有证据；无适用观察为 `passed/none_found` | 存在 unknown 时 `not_run` |
| `f05.behaviors` | 适用检查已得到确定结果，合法例外和不适用也算已完成 | 检查因缺材料无法完成时 `not_run` |
| `f05.recurrence` | 已按可用观察完成分组和阈值计算，低于阈值也可通过 | 分组无法执行时 `failed` |

### 8.2 `DedupSummary`

```text
signature_version, raw_event_count, canonical_event_count,
duplicate_event_count, conflicted_event_count,
groups[]{canonical_event_id, event_ids[], reason, signature_strength}
```

### 8.3 `ReceiptValidation`

```text
receipt_id, status, usable, session_id, harness, context_epoch,
before_event_id, captured_at?, coverage_declared, coverage_effective,
channels_covered[], required_channels[], entries[], referenced_by_event_ids[],
provenance{}, issues[], synthetic
```

`ReceiptValidation.status` 是 `verified`、`rejected`、`unknown`；`usable` 只在 `verified` 时为 `true`。身份检查通过、但非空条目全部无效或无法解析时，状态为 `unknown`，不能用该回执证明加载或未加载；身份不匹配时为 `rejected`。声明空清单且有效完整覆盖时可为 `verified` 并用于完整排除。条目 `status` 是 `valid`、`invalid` 或 `unresolved`，并输出 `rule_id`、`evidence_ref`、`span_sha256`、`channel` 和 `issues`。

### 8.4 `LoadObservation`

见 §5.4。所有观察都带 `rule_id`、`event_id`、`state`、`load_proof`、`receipt_ids`、`matched_receipt_id`、`matched_entry_index`、`coverage`（即 `coverage_effective`）、`reasons`、`gaps`、`synthetic`、`proof_scope`。附 `proof_receipt_id`（证实加载或完整排除的回执 ID，未知时为空）与 `proof_checks`（基于该回执的逐门槛结构）。`proof_checks` 包含 `same_session/same_harness/same_epoch/before_action/order_verified/channels_complete/entry_present/text_sha_matches`，每项为布尔值或 null。完整排除时 `entry_present=false`，`text_sha_matches` 仅表示被检查的规则快照摘要已验证；已加载时它表示匹配条目的行段摘要一致。没有有效证明时未知字段保留 null。F07 以证明回执和对应的验证记录核对这些字段。

### 8.5 `RecurrenceGroup`

```text
group_id, project_id, rule_id, constraint_id?, check_ids[],
task_scope[], scope_unknown, violation_kind, status,
meets_recurrence_threshold, confirmed_violation_event_ids[],
related_event_ids[], confirmed_violation_count,
independent_task_ids[], confirmed_independent_task_count,
task_identity_complete, duplicate_event_ids[], exception_event_ids[],
unknown_event_ids[], gaps[], evidence_refs[], synthetic
```

`group_id = "rec-" + sha256(规范键)[:16]`，规范键字段顺序固定，保证重排输入后不变。

### 8.6 `Exclusion` 与 `Gap`

```text
Exclusion{event_id, reason, detail, rule_id?, check_id?}
Gap{code, field, message, effect, event_id?, receipt_id?}
LegacyNotice{detected_format, requires_migration, message, metadata{},
             claims[]{event_id, receipt_id?, claimed_state, verified_state, reason},
             warnings[]}
```

`effect` 取 `unknown`、`not_counted`、`warning`、`rejected`。`rejected` 只用于回执或包内引用级失败，不表示整包失败。

### 8.7 缺口代码

以下代码是验收断言的稳定标识；实现可以增加，不得改动含义。

| 类别 | 代码 |
| --- | --- |
| 包外壳 | `unsupported_format`、`unsupported_schema_version`、`not_synthetic`、`project_id_mismatch`、`input_too_large`、`invalid_utf8`、`invalid_json`、`duplicate_json_key`、`max_items_exceeded`、`empty_package` |
| 路径与引用 | `unsafe_path`、`unknown_file_ref`、`ref_path_mismatch`、`snapshot_unresolved`、`embedded_digest_mismatch`、`ref_unresolved`、`entry_unresolved`、`entry_range_invalid` |
| 事件 | `invalid_action`、`missing_action_evidence`、`missing_after_snapshot`、`missing_task_id`、`missing_attempt_id`、`missing_occurred_at`、`missing_context_epoch`、`missing_snapshot`、`missing_source_ref`、`duplicate_event_conflict`、`conflicted_event` |
| 回执 | `missing_receipt`、`dangling_receipt`、`duplicate_receipt_id`、`receipt_session_mismatch`、`receipt_harness_mismatch`、`receipt_epoch_missing`、`receipt_epoch_mismatch`、`receipt_anchor_missing`、`receipt_anchor_mismatch`、`receipt_anchor_offset`、`receipt_after_action`、`captured_at_missing`、`entry_digest_mismatch`、`entry_channel_mismatch`、`entry_scope_mismatch`、`entry_rule_id_mismatch`、`coverage_incomplete`、`coverage_channel_gap`、`coverage_unattested`、`channel_set_unknown`、`conflicting_receipts`、`provenance_missing` |
| 规则与检查 | `rule_not_confirmed`、`rule_binding_digest_missing`、`check_not_confirmed`、`check_rule_not_in_scope`、`unsupported_detector`、`unsupported_operator`、`operator_mismatch`、`unsupported_language` |
| 决策 | `decision_not_found`、`decision_not_effective`、`decision_conflict`、`decision_unresolved` |
| 行为 | `action_not_executed`、`parse_error`、`symbol_not_found`、`symbol_not_string`、`multiple_assignments`、`missing_after_snapshot`、`check_result_missing`、`unconfirmed_check`、`check_action_mismatch`、`check_input_missing`、`check_input_invalid`、`check_input_digest_mismatch`、`check_result_inconclusive`、`conflicting_check_results`、`result_text_mismatch` |
| 复发 | `scope_unknown`、`task_identity_incomplete` |
| 旧格式 | `legacy_format`、`legacy_receipt_only`、`legacy_claim_downgraded`、`legacy_snapshot_label`、`legacy_claim` |

## 9. 依赖接口

### 9.1 F02

| 需要 | 用途 |
| --- | --- |
| `capture_selected_input(path, input_id=..., kind="event_package")` | 捕获事件包，10 MiB 上限，`origin = selected_input` |
| `read_raw(evidence_id, *, max_bytes)`（新增请求） | 读取已捕获的完整包字节用于 JSON 解析，不重新访问原文件 |
| `capture_imported_bytes(data, logical_name=..., input_id=..., kind=..., provenance="synthetic")` | 导入包内历史文件，重新计算摘要 |
| `make_ref(evidence, line_start, line_end)` | 生成规则、动作、条目引用 |
| `read_evidence(ref)` | 读取行段，返回 `text`、`span_sha256`、行号 |
| `Evidence.sha256` / `EvidenceRead.span_sha256` | 文件摘要与行段摘要的唯一来源 |

F02 的错误要能映射成 F05 的 `input_too_large`、`symlink_rejected`、`file_not_found`、`invalid_utf8`、`unsafe_path`。包解析使用 F02 已校验的完整快照字节。

### 9.2 F04

| 需要 | 说明 |
| --- | --- |
| `RuleBinding` | `rule_id`、`text`、`text_sha256`（F02 `span_sha256`）、`scope`、`harness`、`source_ref`、`interpretation_status` |
| `resolve_applicable_rules(path, at, harness)` | 返回该动作时点适用的已确认规则；范围与例外由 F04 判定 |
| `ConfirmedBehaviorCheck` | 已确认的检查配置，含 `confirmation_ref` 和约束 |
| `resolve_constraint(subject, path, at, harness)` | 返回 `ConstraintResolution`，包含有效值、例外标志和缺口 |
| `ConstraintResolution` | 字段见 §6.1；`exception_applied` 必须由决策区间和范围计算，不能由 F05 猜 |

F05 不读取决策登记 JSON，也不自行解释 `Decision`；它只消费 F04 的解析结果。这样历史时点的决策有效性只有一处实现。

### 9.3 F03 / F01

- F03 仅透传显式合成渠道声明，F10 不补造完整渠道集合；缺失时完整排除保持未知。
- F01 的 `contracts.py` 需要登记 F05 的输出对象，或允许 F05 在自己的模块内定义并由 F09 以 JSON 消费。建议在契约中增加 `IncidentInput`、`LoadObservation`、`BehaviorObservation`、`RecurrenceGroup`、`Exclusion`、`Gap` 的字段说明，并明确 `Incident.task_id` / `attempt_id` / `snapshot_id` / `source_ref` 在输入包中可为空、在归一化结果中为 `null`。这避免 F01 的结构校验把 F05-08 的缺材料用例直接拒绝。

## 10. 错误与状态

### 10.1 包级错误

以下错误抛出 `IncidentInputError(code, field, message)`，CLI 退出码 `2`，不产生部分分析：

| 代码 | 触发 |
| --- | --- |
| `file_not_found` | 路径不存在 |
| `not_regular_file` | 目录、设备、管道等 |
| `symlink_rejected` | 最终路径组件是符号链接 |
| `input_too_large` | 超过 `max_bytes`，默认 10 MiB |
| `invalid_utf8` | 非 UTF-8 |
| `invalid_json` | JSON 语法错误 |
| `duplicate_json_key` | 同一对象重复键 |
| `unsupported_schema_version` | 不是整数 `1` |
| `unsupported_format` | 既不是 v1 也不是可识别的旧格式 |
| `not_synthetic` | `synthetic != true` |
| `project_id_mismatch` | 包与调用参数不一致 |
| `invalid_field` | 必填字段缺失、类型错误、时间无时区、枚举非法 |
| `unsafe_path` | 绝对路径、`..`、空段、反斜杠、结尾 `/` |
| `max_items_exceeded` | 事件、回执或文件数量超限 |

错误消息使用字段路径，例如 `incidents[1].occurred_at`、`receipts[0].entries[2].text_sha256`；不打印完整正文。

### 10.2 分析状态

- `complete`：包解析成功，所有规范事件完成归一化；所有适用检查都得到 `violation` / `no_violation` / `exception` / `not_applicable` / `unknown` 之一；所有回执完成校验。存在 `unknown` 不改变 `complete`。
- `partial`：至少一个事件、回执或检查因结构问题无法处理（例如动作引用无法解析、冲突事件、冲突回执），但其余结果可用。`gaps[].effect = rejected` 至少一条。
- 包级错误不返回 `IncidentAnalysis`，直接抛异常。
- CLI 依据 `coverage_items` 汇总。缺回执导致请求的加载判断未完成时，F05 写入 `f05.loads: not_run` 和原因，CLI 报 partial/3。

## 11. 安全、确定性与边界

- 不执行包内代码，不 `import` 被检查文件，不调用 shell；Python 检查只用 `ast.parse` 读文本。
- 不用正则或字面量搜索判定违规；`v1` 出现在注释、diff、日志或 `added_literal` 中只作为线索。
- 所有引用只允许包内 `file_ref`；解析前完成路径字符串校验，拒绝绝对路径、`..`、符号链接和越界。F05 不读取包外文件。
- 包内文本按 UTF-8 严格解码；不猜测编码，不把二进制当文本。
- 输出排序固定：事件按 `(occurred_at is None, occurred_at, event_id)`，回执按 `receipt_id`，条目按数组下标，加载观察按 `(event_id, rule_id)`，行为观察按 `(event_id, check_id)`，排除项按 `(event_id, reason, check_id)`，分组按 `group_id`，缺口按 `(code, event_id, field)`。
- 输出不含当前时间、随机数、宿主绝对路径或运行目录。相同输入和相同依赖输出逐字段一致（`evidence_id` 由同一运行内 F02 决定）。
- 所有输出保留 `synthetic: true` 和 `proof_scope: "synthetic_case"`；不得因用户请求、报告渲染或模型候选去掉该标记。
- F05 不修改项目文件，不写 `examples/`；测试读取旧样例前后要断言文件摘要不变。

## 12. 本地测试

```sh
.venv/bin/python -m pytest tests/test_f05_incidents.py tests/test_f04_decisions.py tests/test_f02_evidence.py -q
.venv/bin/python -m compileall -q src/context_manager
```

测试使用 F02 的真实 `EvidenceStore` 和 F04 的真实解析器；只有模型不存在，本功能不需要模型。夹具放在 `tests/fixtures/f05/`：

```text
recurrence_two_tasks/package.json     # 两个独立任务，均引入 v1
retry_duplicates/package.json         # 同任务重试、同 ID、复制日志、跨 session
non_actions/package.json              # 提及、搜索、修复删除、兼容目录、相似前缀
literal_and_diff/package.json         # 注释含 v1 但常量 v2、diff 自述 v1
load_ok/package.json                  # 完整回执，匹配规则文本
load_bad/package.json                 # 悬空、错摘要、跨 session、动作后、错 epoch
coverage/package.json                 # 完整排除、部分覆盖、缺渠道、旧版本在上下文
missing_metadata/package.json         # 缺 task_id、occurred_at、after_ref
structured_results/package.json        # 通过、失败、摘要错误、未确认、冲突结果
invalid/                              # 版本、非 synthetic、project_id、路径、JSON 负例
```

测试辅助函数在 `tmp_path` 中生成超过 10 MiB 的文件和非法 UTF-8，不提交大文件；旧格式用例直接读取 `examples/stale-users-api/case.json`，只读不改。

| 编号 | 输入或场景 | 必须断言 |
| --- | --- | --- |
| F05-01 | `recurrence_two_tasks`：两个独立任务都在新客户端引入 v1 | `confirmed_violation_actions = 2`、`confirmed_independent_task_count = 2`、组 `status = recurrence`、`meets_recurrence_threshold = true`；两个事件都保留证据引用 |
| F05-02 | `retry_duplicates`：同一任务的重试、复制日志、重复导入、跨 session 同任务 | `raw_event_count` 大于 `canonical_event_count`；重复数准确；`confirmed_independent_task_count = 1`；组 `status = single_task`，不产生复发候选 |
| F05-03 | `non_actions`：文档提及、搜索命令、`cat` 规则文件、修复删除旧调用、相似前缀 `clients-old/` | `confirmed_violation_actions = 0`；`exclusions` 分别为 `mention`、`tool_result_only`、`fix_removal`、`out_of_scope`；`clients-old/` 不被当成 `clients/` |
| F05-10 | `literal_and_diff`：Python 注释含 v1、实际常量 v2；diff 自述 v1；结构化结果输入摘要错误 | 前者 `no_violation`；diff 自述不改变 AST 结论；后者 `unknown` + `check_input_digest_mismatch`；没有任何一条因字面量计数 |
| F05-04 | `non_actions` 的合法 v1 兼容目录 | 事件保留，行为 `status = exception`、`exception_applied`，`counts_toward_recurrence = false`；`exclusions` 含 `exception` |
| F05-05 | `load_ok`：回执覆盖相关规则实际文本、摘要、epoch、动作前时点都匹配；另有一条同 epoch 较早锚点的回执 | 直接锚点与较早锚点都得到 `observed_loaded`、`load_proof = matched_entry`；较早锚点额外带 `receipt_anchor_offset` 警告；`matched_entry_index` 指向条目；`synthetic` 和 `proof_scope` 保留 |
| F05-06 | `load_bad`：悬空 receipt ID、错摘要、跨 session、动作后回执、错 epoch | 每个事件的加载状态为 `unknown`；原因分别为 `dangling_receipt`、`entry_digest_mismatch`、`receipt_session_mismatch`、`receipt_after_action`、`receipt_epoch_mismatch`；没有 `observed_loaded` |
| F05-07 | `coverage`：完整清单排除规则；部分清单缺规则；声明 complete 但缺渠道；完整清单含旧版本 | 完整排除 → `observed_not_loaded` + `complete_exclusion`；部分 → `unknown` + `coverage_incomplete`；缺渠道 → `unknown` + `coverage_channel_gap`；旧版本 → `observed_not_loaded` + `different_version_in_context` |
| F05-08 | `missing_metadata`：缺 `task_id`、`occurred_at`、`after_ref` 和 `snapshot_id` | 事件全部保留；缺时间/证据的事件加载与行为为 `unknown`；`task_identity_complete = false`；组 `status = insufficient_task_identity`；缺口代码可见，不补造 ID 或时间 |
| F05-09 | 直接导入 `examples/stale-users-api/case.json` | `package_format = legacy-demo-v0`；`legacy.requires_migration = true`；`receipts = []`；旧 `observed_loaded` 只出现在 `legacy.claims.claimed_state`，`verified_state = unknown`；`loads = []`、`behaviors = []`；原文件摘要不变 |
| F05-11 | 超过 10 MiB 的包、恰好 10 MiB 的合法包、目录、符号链接 | 超限 `input_too_large`、退出码 `2`；恰好上限可解析；目录 `not_regular_file`；符号链接 `symlink_rejected`；失败前不解析 JSON |
| F05-12 | 非 synthetic、`schema_version = 2`、`project_id` 不一致、绝对路径、`..`、重复 JSON 键、坏 UTF-8 | 分别返回 `not_synthetic`、`unsupported_schema_version`、`project_id_mismatch`、`unsafe_path`、`duplicate_json_key`、`invalid_utf8`；字段路径可见；包外文件未被读取 |
| F05-13 | 重复 receipt ID（相同/不同内容）、同 event ID 不同内容、冲突回执 | 相同回执去重并警告；不同内容全部 `rejected`；冲突事件标 `conflicted`，不产生违规；一 loaded 一 not_loaded 的冲突回执 → `unknown` + `conflicting_receipts` |
| F05-14 | `structured_results`：passed、failed、`error`、未确认 check、错误 event、冲突结果、未支持 detector | `failed → violation`、`passed → no_violation`、`error → unknown`；未确认/错误 event/冲突分别得到对应缺口；未支持 detector 不执行正则，计入 `checks_unsupported` |
| F05-15 | 同一包重复分析、打乱 `incidents`/`receipts` 数组顺序 | 语义字段、`group_id`、计数和排序一致；输出不含当前时间或随机 ID；`synthetic` 始终为 `true` |

## 13. 通过与交接

审查者重点核对：

- 去重是否只合并同任务、同内容的记录，没有把两个独立任务或不同文件的两个动作合并掉。
- `observed_loaded` / `observed_not_loaded` 是否都由程序校验身份、时序、条目摘要和渠道完整性；是否存在“只有 receipt ID”或“模型说加载了”就被升级的路径。
- 合规例外、提及、搜索、修复删除是否保留事件但不计数；`clients-old/` 与 `clients/` 是否按路径组件区分。
- 旧样例是否被明确降级，示例报告中的 `supported` / `observed_loaded` 是否已改为真实可得的输出。
- 所有输出是否保留 `synthetic` 和 `proof_scope`；是否存在把 `unknown` 写成 `observed_not_loaded` 的路径。

下游：

- **F06** 可以读取 `events`、`exclusions`、`loads` 的只读摘要，但不得用模型补写 `channels_covered` 或 `coverage = complete`。
- **F07** 使用 `loads` 生成 `missing_load` / `loaded_violation`，使用 `recurrence` 生成 `recurrence`；只有 `load_proof = complete_exclusion` 和 `meets_recurrence_threshold = true` 才达到对应门槛。
- **F09** 建议把 `IncidentAnalysis.to_dict()` 写入 `out/<run_id>/incidents.json`，并在报告中展示原始事件数、规范事件数、重复数、独立任务数和未被证明的缺口。
- **F10** 的 `diagnose --case FILE` 调用 `prepare_incidents`；包级错误退出 `2`，`partial` 按 F09 的覆盖结论处理。

实现交接写入 `local/handoffs/F05.md`，必须附实际命令、退出码、日志、源文件摘要清单，以及 F05-01 至 F05-15 的逐条结果。设计交接写入 `local/design-handoffs/F05.md`。
