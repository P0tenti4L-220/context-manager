# F01 工程入口与公共契约

依赖无。实现前阅读 [总体设计](../design.md)、[公共契约](../contracts.md) 和 [执行计划](../../EXECUTION_PLAN.md)。

本项是 F02–F10 的根节点：它不产出体检结论，只建立后续模块共同使用的对象结构、校验错误、CLI 骨架和最小测试入口。字段、枚举及 JSON 形状以[公共契约 r3](../contracts.md)为准，[跨功能接口矩阵](../interfaces.md)定义业务对接。本文保留 F01 的结构校验细节。

## 用户结果

本机安装后可以运行 `context-manager --help` 与 `--version`，所有模块共享同一套输入输出结构、状态和错误定义。后续功能可以在 IDE 中直接导入测试。

任何模块的输入错误都落到同一个 `FieldError`，用同一套字段路径指回原位置，例如 `incidents[1].occurred_at`。用户先看到“哪个字段错了、期望什么”，而不是一整段输入正文。

## 范围与校验边界

| 主题 | F01 负责 | 交给 |
| --- | --- | --- |
| 对象字段 | 必填键、类型、枚举、格式、单对象内部一致性 | — |
| 跨对象引用 | 不检查存在性 | F02 证据、F04 决策与确认、F05 事件与回执 |
| 作用域重叠、替代关系、有效期语义 | 只检查 `effective_until > effective_from` 等单对象规则 | F04 |
| 证据门槛与结论等级 | 只校验枚举和结构 | F07 |
| Diff 内容与可应用性 | 只校验 `diff_sha256` 格式及 null 规则 | F08 |
| 文件写入与报告 | 不写任何运行产物 | F09 |
| 业务管线 | 解析、构造并校验请求；未接通时返回 `not_implemented` | F10 |

“必填”表示 JSON 键必须存在；“值可 null”表示该键允许显式 `null`，用于保留已知缺口。缺键与 `null` 是两种不同错误，不能互相替代。

## 文件与接口

| 文件 | 责任 |
| --- | --- |
| `pyproject.toml` | 包元数据、`context-manager` 控制台入口、pytest 配置、`local_model` 标记与 `prompts/*.md` 包资源声明 |
| `src/context_manager/__init__.py` | `__version__`、包级导出；不产生导入副作用 |
| `src/context_manager/__main__.py` | `python -m context_manager` 转发到 `cli.main` |
| `src/context_manager/cli.py` | 参数解析、请求构造、退出码、`not_implemented` 分支 |
| `src/context_manager/contracts.py` | 数据类型、标量校验、对象解析与序列化、`ContractError` |
| `tests/conftest.py` | `--local-model-config` 选项、`local_model` 标记、`local_model_config` 夹具 |
| `tests/test_f01_foundation.py` | 本文验收用例 |

包名 `context_manager`，控制台命令 `context-manager`，Python 基线 3.11。运行时只用标准库；`pytest` 仅作为开发依赖。

公开 Python 接口（F02–F10 只使用这些名称，不直接读内部结构）：

| 接口 | 行为 |
| --- | --- |
| `SCHEMA_VERSION: int` | 固定为 `1`，与包版本分别记录 |
| `CONTRACT_OBJECTS: Mapping[str, type]` | 对象名到 dataclass 的映射，供测试和下游按名解析 |
| `parse_object(name, data, *, max_errors=20)` | 校验并返回 dataclass 实例；失败抛 `ContractError` |
| `validate_object(name, data, *, max_errors=20) -> list[FieldError]` | 只校验，返回全部字段错误，不抛异常 |
| `parse_list(name, items, *, path_prefix="")` | 逐项校验容器元素，返回 `(有效对象, 错误列表)`，错误路径带 `path_prefix` 索引；供 F05 隔离坏事件 |
| `dump_object(obj) -> dict` | 序列化为可 JSON 化的 dict，保留全部键与 null |
| `dumps(obj) -> str` | 稳定 JSON 文本；文件输出使用 2 空格缩进 |
| `load_json(path, *, max_bytes=32*1024*1024)` | 读 UTF-8 文件并检查 JSON 语法、重复键、NaN/Infinity |
| `load_document(path, *, max_bytes=...)` | 在 `load_json` 基础上要求顶层对象且 `schema_version == 1` |
| `require_schema_version(data)` | 单独校验版本字段，供自有信封的模块复用 |
| `normalize_relative_path(value)` / `normalize_scope_prefix(value)` | 返回规范形式；不合法时抛 `ValueError`，由对象校验转成 `FieldError` |
| `resolve_cli_path(value, cwd)` | 按传入 cwd 解析一次为绝对路径，不做存在性检查 |
| `build_request(namespace, cwd)` | 从 argparse 结果构造并校验 `AuditRequest`；参数组合错误抛 `ContractError` |
| `ContractError` | `code`（字段校验为 `contract_invalid`，CLI 参数错误为具体码）、`object_name`、`errors: tuple[FieldError, ...]`、`exit_code = 2`、`to_dict()` |
| `FieldError` | `path`、`code`、`message`、`expected`、`actual_type` |

常量：`EXIT_OK = 0`、`EXIT_FAILED = 1`、`EXIT_USAGE = 2`、`EXIT_PARTIAL = 3`。退出码语义见公共契约第 7 节，F01 只使用 0、1、2。

## 公共类型

用 `@dataclass(frozen=True, slots=True)` 加 `enum.StrEnum` 表达对象和枚举。解析后的对象及嵌套容器不可变，与输入隔离；修改 `dump_object` 输出也不能改变对象。下游通过 `parse_object` 从 dict 构造，避免绕过校验。

### 标量

| 类型 | 规则 |
| --- | --- |
| `Timestamp` | 字符串，`YYYY-MM-DDTHH:MM:SS[.fraction]` 加 `Z` 或 `±HH:MM`。正则 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z\|[+-]\d{2}:\d{2})$`，再用 `datetime.fromisoformat` 复核。拒绝只有日期、无时区、空格分隔和小写 `t`/`z`。 |
| `Sha256` | 64 位小写十六进制，`^[0-9a-f]{64}$`。大写不自动转换，直接拒绝。 |
| `RelativePath` | 非空、≤ 4096 字符、UTF-8、无控制字符、无 NUL、无反斜杠、无盘符；以 `/` 分隔；不允许空组件、`.` 组件、`..` 组件和结尾 `/`。中文组件有效。不做存在性检查。 |
| `ScopePrefix` | 在上述规则上允许且仅允许一个结尾 `/`；允许整串为 `.`；`./clients` 规范为 `clients`。规范化只发生一次。 |
| `PathString` | 非空、≤ 4096 字符、无 NUL、无控制字符；不要求相对路径写法，由调用时工作目录解析。 |
| `IdString` | 去除首尾空白后非空、≤ 128 字符、无控制字符、无 `/`。允许中文与中间空格。 |
| `RunId` | `IdString` 且匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`，保证能作为目录名。由运行入口生成，F02/F09 必须遵守。 |
| `HarnessId` | 开放标识：`shared` 或 `^[a-z][a-z0-9_]{0,31}$`。已知工具集合由 F03 的 profile 决定，F01 不拒绝未登记的工具名。 |
| `OpenToken` | `^[a-z][a-z0-9_]{1,63}$`。用于契约未枚举的字段，见“枚举归属”。 |
| `Scalar` | `str` / `int`（不含 `bool`）/ `float`（有限值）/ `bool` / `null`。不接受数组和对象。 |

所有字符串字段统一要求 UTF-8、无 NUL、无控制字符（`U+0000–U+001F`、`U+007F`）。只有 `RuleSpan.text`、`Finding.claim`、`Edit.new_text` 等正文类字段允许换行（`\n`、`\r\n`），其余字段的换行同样按控制字符拒绝。

### 复合基础类型

| 对象 | 字段 | 规则 |
| --- | --- | --- |
| `Scope` | `include_prefixes`、`exclude_prefixes`、`harnesses` | 前者为非空 `ScopePrefix` 数组，后两项可省略并默认 `[]`。重复前缀允许，不自动去重，保证往返一致。 |
| `EvidenceRef` | `evidence_id`、`sha256`、`line_start`、`line_end` | 全部必填。行号从 1 开始，`line_end >= line_start`。不检查证据是否存在。 |
| `SourceLocator` | `relative_path`、`sha256`、`line_start`、`line_end` | 静态登记必填字段，摘要和行段的结构校验与 EvidenceRef 一致，路径代替本次运行 ID。 |
| `Provenance` | `kind`、`adapter`、`version` | 按公共契约区分项目文件、用户输入、合成与适配器来源。 |
| `RunError` | `code`、`path`、`message`、`severity` | severity 可选，默认 error，也可为 warning。 `path` 可为 `null`；`message` 不得回显输入正文。用于 `RunResult.errors`。 |

`Scope` 的规范化在解析时完成一次：去 `./` 前缀、去单个结尾 `/`、`.` 保留。非规范输入被改写为规范形式属于声明行为，`dump_object` 不还原原字面量。后续模块只能看到规范形式，比较逻辑各自实现（组件级比较，`clients/` 与 `clients-old/` 不重叠）。

### 输入对象

**`AuditRequest`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | `IdString` | 是 | 用户给定的本地别名 |
| `repo` | `PathString` | 是 | 调用时工作目录下的项目根，解析为绝对路径 |
| `task_scope` | `Scope` | 是 | 由 CLI 的 `--scope` 汇总，默认 `["."]` |
| `as_of` | `Timestamp` | 是 | 每次审计固定；CLI 省略时取当前带时区时间 |
| `semantic_mode` | `off` / `local` | 是 | 默认 `off` |
| `decision_registry` | `PathString` 或 `null` | 否 | 注册文件路径；内容由 F04 校验 |
| `harness_profile` | `PathString` 或 `null` | 否 | profile 路径；内容由 F03 校验 |
| `out` | `PathString` 或 `null` | 否 | 输出根；分离性由 CLI 与 F02/F09 检查 |

`PathString` 只要求非空、无 NUL、无控制字符；解析后的绝对路径由 `resolve_cli_path` 提供，不写回原始字符串字段。

**`RuleSpan`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | `IdString` | 是 | — |
| `source_ref` | `EvidenceRef` | 是 | 引用一段已捕获证据 |
| `text` | `string` | 是 | 非空，允许换行 |
| `scope` | `Scope` | 是 | 规则适用前缀 |
| `harness` | `HarnessId` | 是 | 指定工具或 `shared` |
| `interpretation_status` | `structured_confirmed` / `model_candidate` / `unresolved` | 是 | — |
| `confirmation_ref` | `IdString` 或 `null` | 否 | 默认 `null`；与状态的耦合由 F04 校验 |

**`Constraint`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `subject` | `string` | 是 | 1–128 字符，与 `BehaviorCheck.subject` 对齐 |
| `operator` | `equals` / `not_equals` | 是 | v1 只有这两种机械比较 |
| `value` | `Scalar` | 是 | 多值或复杂条件不属于 v1 的机械比较 |
| `conditions` | `object<string, Scalar>` | 是 | 可为 `{}`；键非空 |
| `constraint_id`、`span` | IdString、Unicode code point 区间 | 否 | 均可空，区间见公共契约 |

**`BehaviorCheck`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `check_id` | `IdString` | 是 | — |
| `subject` | `string` | 是 | 与 `Constraint.subject` 对齐 |
| `detector` | `python_assignment_literal` / `structured_check_result` | 是 | — |
| `scope` | `Scope` | 是 | 检查适用前缀 |
| `parameters` | `object` | 是 | 可为 `{}`；具体形状由 F04/F05 按 detector 校验 |
| `confirmation_ref` | `IdString` 或 null | 否 | 默认 null |

**`Decision`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `decision_id` | `IdString` | 是 | — |
| `status` | `draft` / `accepted` / `withdrawn` | 是 | 有效性还要求来源与用户确认 |
| `scope` | `Scope` | 是 | 替代只在本范围内生效 |
| `effective_from` | `Timestamp` | 是 | 区间起点，包含 |
| `effective_until` | `Timestamp` 或 `null` | 否 | 区间终点，不包含；须晚于 `effective_from` |
| `supersedes` | `IdString[]` | 是 | 可为 `[]`；环与悬空 ID 由 F04 检查 |
| `source_ref` | `EvidenceRef` | 是 | — |
| `confirmation_ref` | `IdString` 或 `null` | 是 | 键必须存在；`accepted` 时的非空要求由 F04 检查 |
| `constraints` | `Constraint[]` | 是 | 可为 `[]` |

区间与可空规则按公共契约第 1、2 节。

**`Confirmation`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `confirmation_id` | `IdString` | 是 | — |
| `kind` | `decision_approval` / `rule_interpretation` / `check_configuration` | 是 | 闭合枚举 |
| `source_ref` | `EvidenceRef` | 是 | 被确认的原文位置 |
| `approved_items` | `ApprovedItem[]` | 是 | 至少 1 项 |
| `edit_approvals` | `EditApproval[]` | 否 | 默认空数组，只允许 decision_approval 携带非空记录；逐字段形状见公共契约 |

`ApprovedItem`：`item_id`（`IdString`）、`source_ref`（`EvidenceRef`）。用 `source_ref` 落实“每个确认绑定具体原文的摘要与行段”。

**`Incident`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `event_id` | `IdString` | 是 | 原始事件 ID |
| `session_id` | `IdString` | 是 | — |
| `task_id` | `IdString` 或 `null` | 是 | 键必填，值可 `null`；未知时保留缺口 |
| `attempt_id` | `IdString` 或 `null` | 是 | 同上 |
| `occurred_at` | `Timestamp` 或 null | 是 | 缺失为 null，保留事件并按 F05 降级 |
| `context_epoch` | 非负整数或 null | 是 | 缺失为 null |
| `scope` | `Scope` 或 null | 否 | 默认 null |
| `harness` | `HarnessId` | 是 | — |
| `snapshot_id` | `IdString` 或 `null` | 是 | 键必填，值可 `null` |
| `action` | `Action` | 是 | 见下 |
| `source_ref` | `EvidenceRef` 或 null | 是 | 事件原文缺失为 null |

`Action`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kind` | `edit` / `tool_result` / `mention` | 是 | 首版范围来自 F05 |
| `target_path` | `RelativePath` | 是 | 动作对象 |
| `evidence_ref` | `EvidenceRef` 或 null | 是 | 动作观察材料，缺失为 null |
| `before_ref` | `EvidenceRef` 或 `null` | 否 | 编辑前版本，默认 `null` |
| `after_ref` | `EvidenceRef` 或 `null` | 否 | 编辑后版本，默认 `null` |
| `payload` | `object` | 否 | 默认 `{}`；detector 专用字段，形状由 F05 校验 |

`payload` 承载 F05 校验的检测器输入。

**`LoadReceipt`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `receipt_id` | `IdString` | 是 | — |
| `session_id` | `IdString` | 是 | — |
| `context_epoch` | `int >= 0` | 是 | 压缩上下文序号；比较语义由 F05 负责 |
| `before_event_id` | `IdString` | 是 | 动作前时点 |
| `harness` | `HarnessId` | 是 | 回执工具 |
| `channels_covered` | 渠道数组 | 是 | 可为空，有效覆盖由 F05 判断 |
| `captured_at` | `Timestamp` 或 null | 否 | 默认 null |
| `entries` | `LoadReceiptEntry[]` | 是 | 字段见公共契约，可为空 |
| `coverage` | `complete` / `partial` / `unknown` | 是 | — |
| `provenance` | `Provenance` | 是 | 合成标记或适配器版本 |

回执与事件的 session、harness、顺序、摘要一致性由 F05 校验，F01 只保证结构。

### 证据对象

**`Evidence`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `evidence_id` | `IdString` | 是 | — |
| `run_id` | `RunId` | 是 | 一次运行内唯一 |
| `kind` | `OpenToken` | 是 | 已知值由 F02 决定 |
| `relative_path` | `RelativePath` | 是 | 项目内路径；外部输入和导入材料使用公共契约的逻辑路径 |
| `sha256` | `Sha256` | 是 | 原始字节摘要 |
| `snapshot_id` | `IdString` | 是 | 绑定路径与摘要 |
| `captured_at` | `Timestamp` | 是 | — |
| `source_revision` | `string` 或 `null` | 是 | 键必填；Git HEAD 可为 `null` |
| `byte_length` | `int >= 0` | 是 | 原始字节数 |
| `line_count` | `int >= 0` 或 `null` | 是 | 非文本为 `null` |
| `encoding` | `string` 或 `null` | 是 | 小写编码名（如 `utf-8`）；非文本或编码未知为 `null` |
| `provenance` | `Provenance` | 是 | — |
| `origin` | `project` / `selected_input` / `imported` | 否 | 默认 project |
| `input_id` | `IdString` 或 null | 否 | 默认 null，外部来源必有值 |
| `expires_at` | `Timestamp` 或 null | 否 | 默认 null |

**`CoverageItem`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `check_id` | `IdString` | 是 | — |
| `status` | `passed` / `failed` / `not_run` | 是 | — |
| `inputs` | `IdString[]` | 是 | 可为 `[]` |
| `reason` | `string` 或 `null` | 是 | `status != passed` 时必须为非空字符串 |
| `artifact_refs` | `RelativePath[]` | 是 | 可为 `[]` |

### 诊断、修复与运行结果

**`Finding`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `finding_id` | `IdString` | 是 | — |
| `kind` | 见枚举 | 是 | 10 个值 |
| `claim` | `string` | 是 | 非空，允许换行 |
| `scope` | `Scope` | 是 | — |
| `assessment` | `supported` / `hypothesis` / `unknown` | 是 | 门槛由 F07 判定 |
| `basis` | `deterministic` / `confirmed_interpretation` / `model_candidate` | 是 | — |
| `support_refs` | `EvidenceRef[]` | 是 | 可为 `[]` |
| `counter_refs` | `EvidenceRef[]` | 是 | 可为 `[]` |
| `missing_evidence` | `string[]` | 是 | 每项非空 |
| `suggested_action` | 见枚举 | 是 | 9 个值 |
| `checks` | `CoverageItem[]` | 是 | 可为 `[]` |
| `related_event_ids` | `IdString[]` | 否 | 默认 `[]` |
| `related_rule_ids` | `IdString[]` | 否 | 默认 `[]` |
| `related_finding_ids` | `IdString[]` | 否 | 默认 `[]` |
| `claim_type` | `fact` / `causal_explanation` / `gap` | 否 | 默认 fact |
| `synthetic` | bool | 否 | 默认 false |

**`RepairProposal`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `proposal_id` | `IdString` | 是 | — |
| `run_id` | `RunId` | 是 | — |
| `finding_ids` | `IdString[]` | 是 | 至少 1 项 |
| `base_files` | `BaseFile[]` | 是 | preview_ready 时至少 1 项 |
| `edits` | `Edit[]` | 是 | preview_ready 时至少 1 项 |
| `diff_sha256` | `Sha256` 或 `null` | 是 | 无可应用 Diff 时必须是 `null` |
| `status` | `preview_ready` / `needs_confirmation` / `blocked` | 是 | — |
| `checks` | `CoverageItem[]` | 是 | 可为 `[]` |
| `unresolved_questions` | 结构化问题数组 | 是 | 字段见公共契约，可为 `[]` |

`BaseFile` 含 `relative_path`、`sha256` 及可省略且默认 null 的 `evidence_id`。`Edit`：`edit_id`、`finding_id`、`relative_path`、`operation`、`line_start`、`line_end`、`old_text_sha256`、`new_text`。`operation` 为 `replace_span` 时 `new_text` 必须为非空字符串；为 `delete_span` 时必须为 `null`。`diff_sha256` 与 `status` 必须一致：`preview_ready` 必有摘要，`needs_confirmation` 与 `blocked` 必须为 `null`。

**`RunResult`**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `run_id` | `RunId` | 是 | — |
| `project_id` | `IdString` | 是 | — |
| `mode` | `Mode` | 是 | `{"command": "audit"\|"diagnose", "semantic": "off"\|"local"}` |
| `as_of` | `Timestamp` | 是 | — |
| `status` | `complete` / `partial` / `failed` | 是 | 退出码语义见契约 |
| `coverage` | `CoverageItem[]` | 是 | — |
| `findings` | `Finding[]` | 是 | — |
| `proposals` | `RepairProposal[]` | 是 | — |
| `artifacts` | `RelativePath[]` | 是 | — |
| `errors` | `RunError[]` | 是 | — |
| `synthetic` | `bool` | 否 | 默认 `false`；本次运行的合成声明 |
| `parameters` | `{scope, model}` | 是 | 使用公共契约的 Scope 和模型摘要 |
| `inputs` | 输入摘要数组 | 否 | 默认 `[]` |
| `project_revision` | string 或 null | 否 | 默认 null |

模型 trace 通过 F09 写入入口传递。

### 枚举归属

闭合枚举（F01 拒绝未知值，新增值必须先改契约）：

| 字段 | 取值 |
| --- | --- |
| `AuditRequest.semantic_mode`、`Mode.semantic` | `off`、`local` |
| `RuleSpan.interpretation_status` | `structured_confirmed`、`model_candidate`、`unresolved` |
| `Constraint.operator` | `equals`、`not_equals` |
| `BehaviorCheck.detector` | `python_assignment_literal`、`structured_check_result` |
| `Decision.status` | `draft`、`accepted`、`withdrawn` |
| `Action.kind` | `edit`、`tool_result`、`mention` |
| `LoadReceipt.coverage` | `complete`、`partial`、`unknown` |
| `CoverageItem.status` | `passed`、`failed`、`not_run` |
| `Finding.kind` | `stale_rule`、`rule_conflict`、`policy_mismatch`、`dead_reference`、`missing_load`、`loaded_violation`、`stale_code_evidence`、`recurrence`、`cleanup_candidate`、`insufficient_evidence` |
| `Finding.assessment` | `supported`、`hypothesis`、`unknown` |
| `Finding.basis` | `deterministic`、`confirmed_interpretation`、`model_candidate` |
| `Finding.suggested_action` | `keep`、`update`、`delete`、`narrow_scope`、`relocate`、`fix_loading`、`add_check`、`fix_retrieval`、`ask_user` |
| `Edit.operation` | `replace_span`、`delete_span` |
| `RepairProposal.status` | `preview_ready`、`needs_confirmation`、`blocked` |
| `RunResult.status`、`Mode.command` | `complete`/`partial`/`failed`；`audit`/`diagnose` |
| `Provenance.kind` | `project_file`、`synthetic`、`user_supplied`、`adapter` |
| `Confirmation.kind` | `decision_approval`、`rule_interpretation`、`check_configuration` |

开放标识（契约未枚举，F01 只校验形态，由标注模块收窄）：

| 字段 | 当前已知值 | 收窄责任 |
| --- | --- | --- |
| `HarnessId` | `shared`、`codex`、`claude_code` | F03 profile |
| `Evidence.kind` | `file`、`selected_input`、`incident_package`（建议值） | F02 |

## 序列化

- JSON 字段名与契约一致，使用 `snake_case`，不做驼峰转换。
- 文件输出 UTF-8、无 BOM、2 空格缩进、`ensure_ascii=False`、结尾换行。读取时接受 BOM（`utf-8-sig`）。
- 所有对象输出全部键，包括 `null` 和空数组；不因值为 `null` 而省略。
- 键顺序固定为字段声明顺序，保证同一对象两次 `dumps` 字节一致。
- 非 CLI 来源时间可以保留原始偏移；CLI 的 `as_of` 统一为 UTC，序列化 UTC 写成 `Z`。往返比较按时间点，不按字面量。
- `schema_version` 只出现在对外 JSON 顶层，嵌套对象不带。顶层必须恰为整数 `1`。
- 未知字段直接拒绝（`unknown_field`）。r2 属于首版发布前的设计修订，外部 JSON 仍为版本 1。发布后的破坏性字段变化须更新 schema 版本。
- 重复键、`NaN`、`Infinity` 一律拒绝；`json.loads` 使用 `object_pairs_hook` 与 `parse_constant` 拦截。解析器的深度或整数长度限制转换为 `invalid_json`；字段数值转换失败记录 `invalid_value`，CLI 通过统一错误入口返回 2。

## 校验与错误模型

### 错误码

| 码 | 场景 |
| --- | --- |
| `missing_required` | 必填键不存在 |
| `wrong_type` | JSON 类型不符，包括 `bool` 冒充 `int`、数字冒充字符串 |
| `unknown_enum` | 闭合枚举取到未列出的值 |
| `unknown_field` | 对象出现未声明字段 |
| `invalid_format` | 时间戳、摘要、标识、编码名格式错误 |
| `empty_path` | 路径或前缀为空 |
| `invalid_path` | 绝对路径、`..`、空组件、反斜杠、盘符；`RelativePath` 另拒绝结尾斜杠 |
| `invalid_value` | 单对象内部关系不成立，如 `line_end < line_start`、`effective_until <= effective_from`、`not_run` 缺原因 |
| `duplicate_key` | JSON 对象重复键 |
| `invalid_json` | 语法错误、`NaN`/`Infinity` |
| `invalid_encoding` | 输入文件不是 UTF-8 |
| `input_too_large` | 输入超过大小上限 |
| `input_not_found` | 指定文件不存在 |
| `unsupported_schema_version` | 顶层版本不是 `1` |
| `too_many_errors` | 错误数超过 `max_errors`，其余被截断 |
| `out_inside_repo` | `--out` 与项目路径相等或互相包含 |
| `invalid_argument` | CLI 参数组合无效，如 `--semantic local` 缺 `--model-config` |
| `not_implemented` | 业务管线尚未接通 |

### 字段路径

- 根对象字段直接写字段名：`as_of`。
- 数组元素写索引：`incidents[1]`。
- 嵌套组合：`receipts[0].entries[2].line_end`。
- 整个对象类型错误（例如顶层是数组）路径为 `$`。
- 路径只描述位置，不拼接输入内容。

### 聚合与顺序

一次校验收集全部字段错误，默认上限 20 条，按文档顺序深度优先输出。同一位置同一错误码只保留一条。超过上限时保留前 20 条，追加一条 `path="$"`、`code="too_many_errors"` 的记录，不再继续遍历。

校验顺序：键集合（未知字段、缺失键）→ 类型 → 格式与枚举 → 单对象内部关系。父对象先报结构错误，子对象仅在父类型正确时继续解析。

### 隐私

- 错误 `message` 只包含字段路径、期望类型或允许值集合、实际 JSON 类型，以及固定说明。
- 不输出字段值，不输出文件正文，不输出整份输入。`claim`、`text`、`payload` 等正文尤其不得回显。
- CLI 只向 stderr 输出错误，stdout 留空；退出码 2。该规则同时适用于 argparse 的未知枚举和未知参数错误，不回显原始参数值。
- 文件路径可以出现在错误中，但使用相对路径或调用时给出的原文，不展开用户主目录。

CLI 文本格式：首行是 `ContractError.code`；每行是 `<路径>: <错误码>: <说明>`。只有对象级校验使用 `contract_invalid` 作为首行，参数错误首行直接是具体码。

```text
context-manager: contract_invalid
  incidents[1].occurred_at: invalid_format: expected timezone-aware ISO 8601 timestamp; got string
  incidents[1].action.kind: unknown_enum: expected one of edit, tool_result, mention; got string
```

### 与下游的分工

F01 会拒绝的错误：字段缺失、类型不符、枚举非法、时间无时区、摘要格式错误、行号倒置、路径越界写法、`not_run` 无原因、`preview_ready` 无 Diff 摘要、`effective_until` 不晚于 `effective_from`。

F01 不拒绝、由下游判定的情形：引用 ID 不存在、`supersedes` 成环或悬空、`Decision.accepted` 缺确认、作用域重叠、回执与事件顺序不符、证据门槛不足、Diff 不可应用、`out` 经过符号链接落入项目内。

## CLI 行为

### 入口

```text
context-manager --help
context-manager --version
context-manager audit ...
context-manager diagnose ...
```

`python -m context_manager` 与安装后的 `context-manager` 必须等价。`--help` 与 `--version` 不依赖子命令、不读取任何文件、不创建任何目录、不导入重依赖。

`--version` 输出一行：`context-manager <包版本> (schema_version 1)`。包版本来自 `__init__.__version__`，`pyproject.toml` 通过 `[tool.setuptools.dynamic]` 引用同一处，避免两处漂移。

### 参数

| 参数 | 适用 | 必填 | 说明 |
| --- | --- | --- | --- |
| `--repo PATH` | audit、diagnose | 是 | 项目根；解析后必须是已存在目录 |
| `--project-id ID` | audit、diagnose | 是 | — |
| `--scope PREFIX` | audit、diagnose | 否 | 可重复，默认 `["."]`；顺序保留 |
| `--as-of ISO_TIME` | audit、diagnose | 否 | 省略时取当前带时区时间 |
| `--decisions FILE` | audit、diagnose | 否 | 注册文件；省略时 `decision_registry` 为 `null` |
| `--case FILE` | diagnose | 是 | 合成事件包 |
| `--semantic off\|local` | audit、diagnose | 否 | 默认 `off` |
| `--model-config FILE` | audit、diagnose | 条件 | `--semantic local` 时必填 |
| `--out PATH` | audit、diagnose | 是 | 必须位于项目外 |

F01 完整解析以上参数并构造 `AuditRequest`，使 F10 只替换管线，不改参数面。

### 参数解析与校验顺序

1. argparse 解析；未知参数、缺必填、枚举取值错误由 argparse 报错，退出 2。
2. 按调用时工作目录把 `repo`、`out`、`decisions`、`model_config`、`case` 解析为绝对路径，只解析一次。之后任何 `chdir` 不影响结果。
3. `repo` 必须存在且是目录，否则 `input_not_found` / `invalid_value`，退出 2。
4. `--as-of` 按 `Timestamp` 校验，失败路径 `as_of`。
5. `--semantic local` 缺 `--model-config` 报 `invalid_argument`，路径 `model_config`；仅 `off` 时忽略该参数。
6. `--out` 经 `resolve(strict=False)` 后不得与 `repo` 相等或互相包含，否则 `out_inside_repo`，退出 2。该检查是词法与解析级预检，F02/F09 仍须用真实路径复核。
7. `--decisions`、`--case` 指定时必须存在且可读，否则 `input_not_found`。`--decisions` 的 JSON 语法与 `schema_version` 由 F01 检查，字段由 F04 检查；`--case` 的字段由 F05 检查。
8. 全部通过后构造 `AuditRequest`，再进入管线。

### F01 单独交付时的未就绪分支

F01 阶段 `audit`、`diagnose` 在参数与请求校验通过后，向 stderr 输出：

```text
context-manager: not_implemented
  audit pipeline is not available in this build
```

退出码 1。必须满足：不创建 `--out`、不创建 `run_id` 目录、stdout 为空、不输出任何“成功”“0 个问题”一类结果。F10 把该分支替换为正式管线，参数与退出码约定不变。

### 副作用

除读取显式指定的输入文件外，`--help`、`--version`、参数错误和 `not_implemented` 分支都不得写文件、不得创建目录、不得访问网络。

## 测试入口

### pytest 配置

`pyproject.toml` 的 `[tool.pytest.ini_options]`：

```toml
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra --strict-markers"
markers = [
  "local_model: needs a local model runner and --local-model-config",
]
```

`pythonpath` 让源码布局下的测试无需安装即可导入；子进程调用 CLI 的用例显式设置 `PYTHONPATH=<repo>/src`，不依赖当前 shell。

### conftest 契约

`tests/conftest.py` 提供：

- `pytest_addoption` 注册 `--local-model-config PATH`，默认 `None`。
- `pytest_configure` 注册 `local_model` 标记。
- `local_model_config` 夹具：
  - 未提供选项时 `pytest.skip("local_model_test: not_run (pass --local-model-config <file>)")`；
  - 提供但文件不存在、不可读或 TOML 解析失败时 `pytest.fail(...)`，不能跳过；
  - 提供且合法时返回解析后的 dict。
- `repo_root` 夹具返回仓库根目录，供子进程用例拼 `PYTHONPATH`。

标记只有 `local_model` 一类。机械测试不加标记，默认执行；`-m 'not local_model'` 排除模型用例。`--strict-markers` 保证拼错标记名直接报错。

默认 `pytest tests` 会收集模型用例并显示 skip 原因，但不会发起任何模型调用。

## 依赖接口

上游：无。

下游可用的 F01 产物：

| 使用方 | 使用的接口 |
| --- | --- |
| F02 | `Evidence`、`EvidenceRef`、`CoverageItem`、`Provenance`、`Sha256`、`normalize_relative_path` |
| F03 | `AuditRequest.harness_profile`、`Scope`、`ScopePrefix`、`CoverageItem` |
| F04 | `RuleSpan`、`Constraint`、`Decision`、`Confirmation`、`BehaviorCheck`、`parse_object` |
| F05 | `Incident`、`Action`、`LoadReceipt`、`Provenance`、`parse_list`（坏项隔离） |
| F06 | `EvidenceRef`、`Finding`（候选不含最终等级） |
| F07 | `Finding`、`CoverageItem`、`RunError` |
| F08 | `RepairProposal`、`BaseFile`、`Edit` |
| F09 | `RunResult`、`RunError`、`dump_object`、`dumps` |
| F10 | `cli.build_request`、`EXIT_*`、`ContractError.to_dict()` |

后续模块新增字段或枚举前，先改 `docs/contracts.md` 与本文件，再改实现；不得在模块内私自定义同名字段。

## 边界条件

| 场景 | 期望 |
| --- | --- |
| 输入 0 字节或只有空白 | `invalid_json`，退出 2 |
| 顶层是数组、字符串、`null` | `wrong_type`，`path="$"` |
| `schema_version` 缺失、`"1"`、`2` | 分别是 `missing_required`、`wrong_type`、`unsupported_schema_version` |
| 文件带 UTF-8 BOM | 正常读取 |
| JSON 重复键 | `duplicate_key`，不静默取最后一个 |
| `NaN` / `Infinity` | `invalid_json` |
| 文件超过 32 MiB | `input_too_large`，不读入内存 |
| 未知字段 | `unknown_field`，路径指向该字段 |
| 路径含中文 | 合法 |
| 路径为 `..`、`/x`、`C:\x`、`a\\b`、`a//b`、`a/` | `invalid_path`；`a/` 作为 `ScopePrefix` 合法，作为 `RelativePath` 不合法 |
| 作用域前缀 `""` | `empty_path` |
| 作用域前缀 `"."` | 合法，表示项目全部 |
| 时间 `2026-11-04`、`2026-11-04T00:00:00` | `invalid_format`，必须带时区 |
| `line_start=0` | `invalid_value`；`line_end<line_start` 同样为 `invalid_value` |
| 整数位置写 `true` 或 `1.0` | `wrong_type` |
| 必填键写成 `null` | 有 null 语义的字段（如 `task_id`）合法；否则 `wrong_type` |
| `coverage=complete` 且 `entries=[]` | 结构合法时通过；F05 独立核验完整渠道和时序，缺证明时 unknown |
| `not_run` 且 `reason=null` | `invalid_value` |
| `preview_ready` 且 `diff_sha256=null` | `invalid_value` |
| 未知枚举值 | `unknown_enum`，列出允许值 |
| `--out` 与 `--repo` 相等或互相包含 | `out_inside_repo`，退出 2 |
| `--semantic local` 无 `--model-config` | `invalid_argument`，退出 2 |
| `diagnose` 无 `--case` | argparse 退出 2 |
| `audit` 参数合法但管线未接通 | `not_implemented`，退出 1，无文件写入 |

## 本地测试

```sh
.venv/bin/python -m pytest tests/test_f01_foundation.py -q
.venv/bin/python -m context_manager --help
.venv/bin/python -m context_manager --version
.venv/bin/python -m compileall -q src/context_manager
```

| 编号 | 输入或场景 | 必须断言 |
| --- | --- | --- |
| F01-01 | 契约第 2、3、5–7 节的 14 类对象各一份合法样本 | `dump_object` → `parse_object` 字段、枚举、`null`、Unicode 与顺序一致；`dumps` 两次字节相同；解析不修改入参 |
| F01-02 | 版本错误、缺字段、错类型、未知枚举、未知字段 | 错误码与路径准确；`ContractError.exit_code == 2`；CLI 用坏 `--as-of` 或坏 `--decisions` 运行退出 2 |
| F01-03 | 中文、空路径、无时区时间、倒置行号 | 中文 `claim`/路径通过；`""` 报 `empty_path`；无时区报 `invalid_format`；`line_end<line_start` 报 `invalid_value` |
| F01-04 | `--help`、`--version`、无参数调用 | 前两者退出 0；帮助列出两个子命令，版本行含包版本与 `schema_version 1`；在临时 cwd 中不产生任何文件；无参数退出 2 |
| F01-05 | 合法参数的 `audit`、`diagnose` | F01 单独交付时检查 `not_implemented` 和零写入。接入 F10 后核对合法请求传给正式管线，以及 F10 对完整、部分和失败结果的退出码约定。 |
| F01-06 | pytest 收集与标记 | `-m 'not local_model'` 不收集模型用例；默认收集时该用例 skip 原因含 `--local-model-config`；`request.config.getini("markers")` 含 `local_model`，`addopts` 含 `--strict-markers` |
| F01-07 | 嵌套错误与多错误 | 路径为 `incidents[1].occurred_at`、`receipts[0].entries[2].source_ref.line_end`；一次返回多条且顺序稳定；超过 20 条时保留 20 条并追加 `too_many_errors` |
| F01-08 | 输入含哨兵字符串 | 哨兵不出现在 `message`、stderr、stdout；错误只含路径、期望、实际类型 |
| F01-09 | 路径规范化与拒绝 | `./clients/` 规范为 `clients`；`clients//x`、`..`、绝对路径、反斜杠、盘符被拒；中文路径保留 |
| F01-10 | 相对路径解析、参数组合 | `build_request` 返回绝对路径且只解析一次；`--out` 在项目内、`local` 缺配置、`diagnose` 缺 `--case` 均退出 2；重复 `--scope` 顺序保留 |
| F01-11 | JSON 输入规范 | 重复键、`NaN`、非对象顶层、超限、未知字段分别报对应错误；BOM 可读；错误不包含正文 |
| F01-12 | 包入口与版本 | 有安装元数据时 `__version__` 与 `importlib.metadata.version("context-manager")` 一致，否则为合法版本号；`python -m context_manager --version` 输出含版本与 schema 版本；`compileall` 退出 0；导入包无文件或网络副作用 |

测试函数名建议带编号前缀（`test_f01_01_roundtrip` 等），便于审查对照。

## 实现顺序

1. `pyproject.toml`、`__init__.py`、`__main__.py`，先让 `--version`、`--help` 可运行。
2. `contracts.py` 的标量校验与 `FieldError`/`ContractError`。
3. 对象字段表与 `parse_object`、`dump_object`、`load_json`、`load_document`。
4. `cli.py` 参数面、路径解析、`not_implemented` 分支与退出码。
5. `tests/conftest.py` 与 `tests/test_f01_foundation.py`，接着补 `local_model` 门控用例。

r2 扩展同一组验收用例。F01-01 往返覆盖 SourceLocator、带 EditApproval 的确认、含可空材料的事件及新增运行参数。F01-02 拒绝把运行内 evidence_id 当作静态定位、非法批准类型和倒置批准行段。F01-07 校验错误路径能够定位到嵌套批准条目。

## 通过与交接

测试全部通过，包可从虚拟环境调用。审查者核对对象和枚举与契约一致，确认业务尚未实现时未被标成成功。记录实际文件、测试日志、源摘要和状态后等待复核。

F01 的实现交接写入 `local/handoffs/F01.md`，附测试命令、退出码、日志位置、源文件摘要和偏差。本设计交接见 `local/design-handoffs/F01.md`。
