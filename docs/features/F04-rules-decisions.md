# F04 规则与决策登记、静态冲突判定

依赖 F02、F03。属于 Diagnostic Policy 的规则与权威来源部分。所有公共字段以 [公共契约](../contracts.md) 为准；本文补齐 F04 的接口、登记格式、判定顺序、边界条件和验收用例。本文对 `RuleSpan`、`Constraint`、`BehaviorCheck`、`Confirmation` 的扩展字段属于 F04 内部接口/待回写契约项，清单见交接第 6 节；公共契约更新前不得假定其他功能已接受这些字段。

## 用户结果

工具能指出哪条规则已经被当前有效需求替代，哪些范围存在真实矛盾，以及哪些内容只是因为范围、条件或工具不同而合法保留。用户提供一份明确的 JSON 决策登记后，工具只依据其中经确认的原文解释、Decision、Confirmation 和 BehaviorCheck 作确定性判断，并把无法确认的部分列为待确认问题。

判断必须同时说明原文行段、决策来源、生效时间、适用文件范围和受影响的具体义务。程序不根据“文档看起来更新”“文件时间更晚”或“Git 提交更晚”选择权威版本。

## 职责边界

F04 负责：

- 按 Markdown 块结构从 F02 证据中定位规则原文，保留条款、子条款、代码块和标题归属。
- 读取并校验用户显式指定的 JSON 决策登记，绑定原文摘要与行段。
- 解析 Decision 的状态、确认、生效区间、范围和 `supersedes`，得到审计时点 `as_of` 的有效策略。
- 在路径组件和条件交集上比较已确认的结构化约束，输出 `stale_rule`、`rule_conflict` 和必要的 `insufficient_evidence` 候选。
- 校验并输出可参与机械计数的 BehaviorCheck，期望值只从有效 Decision 解析。
- 为 F07 提供确定性候选，为 F08 提供条款级编辑目标，为 F09 提供覆盖状态。

F04 不负责：

- 不解析 ADR 正文里的 `Status: accepted`、`Superseded` 等字样并据此授予权威。此类文本只是候选材料，只有登记与确认才进入已确认集合。
- 不使用文件 mtime、Git 作者时间或提交顺序判断新旧。
- 不执行 `python_assignment_literal` 等 detector，不统计事件违规；由 F05 执行。
- 不调用本地模型，不把模型解释写入确认集合。
- 不生成最终报告、不应用 Diff、不修改项目文件。
- 不推断登记中没有声明的自然语言含义。无法机械比较的内容保持 `unresolved` 或待确认。

## 文件与接口

建议新增：

- `src/context_manager/rules.py`：规则块解析、文本摘要、范围运算、候选构造。
- `src/context_manager/decisions.py`：登记加载与校验、生效策略、替代关系、约束比较。
- `tests/test_f04_decisions.py`。
- `tests/fixtures/f04/`：合成项目和登记变体。

公共入口如下。所有返回对象为冻结 dataclass 或等价只读结构，可稳定序列化为 JSON。

```python
def extract_rule_spans(
    sources: Sequence[RuleSource],
    evidence_store: EvidenceStore,
) -> RuleExtraction: ...

def parse_registry(raw_bytes: bytes, *, project_id: str) -> RegistryInput: ...

def bind_registry(registry_input: RegistryInput, evidence_store: EvidenceStore) -> Registry: ...

def resolve_policy(
    rule_spans: Sequence[RuleSpan],
    registry: Registry,
    evidence_store: EvidenceStore,
    *,
    as_of: datetime,
) -> PolicyState: ...

def compare_constraints(
    rule_spans: Sequence[RuleSpan],
    registry: Registry,
    evidence_store: EvidenceStore,
    *,
    as_of: datetime,
) -> ComparisonResult: ...
```

`compare_constraints` 是策略级入口，不是 F07 的二参数单对比较；单对比较使用 `compare_constraint_pair`。`compare_constraints` 内部调用 `resolve_policy`。`parse_registry` 校验登记结构，错误抛出 `RegistryFormatError`。F10 负责把 F02 预读的字节交给它。`bind_registry` 只调用 F02，核对每个 SourceLocator 的路径、全文件摘要与行段，然后返回含 EvidenceRef 的运行内视图。失败的条目及其授权依赖不参与判定，生成 `registry_binding_failed`、相应缺口与失败覆盖；其他独立条目可继续。重复定位复用本次捕获，原登记不改写。

下游查询入口：

```python
def resolve_applicable_rules(policy, relative_path, *, harness) -> tuple[RuleBinding, ...]: ...
def resolve_constraint(policy, subject, relative_path, *, harness,
                       exception_rule_ids=()) -> ConstraintResolution: ...
def scope_relation(left, right) -> ScopeRelation: ...
def compare_constraint_pair(left, right) -> ConstraintEvaluation: ...
def clauses(policy, rule_id) -> tuple[ClauseBinding, ...]: ...  # 等价于 policy.clauses(rule_id)
```

字段和状态见“下游视图”。这些函数只读 `PolicyState`，不重新读盘或重算决策生效。

### 依赖的 F03 输入

F04 不自行扫描目录。F03 需要为每个候选规则文件提供 `RuleSource`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `evidence_id` | `str` | F02 本次运行捕获的证据 ID |
| `relative_path` | `str` | 根目录相对路径，`/` 分隔 |
| `harness` | `str` | 工具名或 `shared` |
| `source_scope` | `Scope` | F10 从 F03 已解析的 `rule_files[].scope` 投影 |
| `kind` | `str` | `agents`、`claude`、`override`、`claude_rule` |

同一证据被两个工具分别加载时，F03 可以给出两条 `RuleSource`。F04 按 `(evidence_id, harness)` 区分，不合并结论。无法解析的来源范围只进入覆盖和缺口，不用默认根范围参与比较。

### 输出对象

| 对象 | 主要字段 |
| --- | --- |
| `RuleExtraction` | `spans`、`gaps`、`sources`、`coverage` |
| `Registry` | `schema_version`、`project_id`、`rules`、`decisions`、`confirmations`、`behavior_checks`、`sha256` |
| `PolicyState` | `as_of`、`current_rules`、`rule_bindings`、`effective_decisions`、`inactive_decisions`、`supersessions`、`effective_checks`、`suggested_checks`、`editable_files`、`issues` |
| `ComparisonResult` | `as_of`、`candidates`、`evaluations`、`issues`、`coverage`、`policy`、`edit_targets` |
| `RuleBinding` | `rule_id`、`text`、`text_sha256`、`scope`、`harness`、`source_ref`、`interpretation_status`、`constraint_ids` |
| `ConstraintResolution` | `status`、`operator`、`value`、`decision_id`、`constraint_id`、`rule_ids`、`exception_applied`、`gaps` |
| `ConfirmedBehaviorCheck` | `check_id`、`rule_id`、`rule_ids`、`subject`、`detector`、`scope`、`parameters`、`constraint`、`decision_id`、`confirmation_ref`、`expected_value` |
| `StaticCandidate` | F07 信封，见“输出” |
| `ConstraintEvaluation` | `left_id`、`right_id`、`subject`、`status`、`reason`、`scope`、`conditions`、`left_value`、`right_value` |
| `EditTarget` | `rule_id`、`line_start`、`line_end`、`old_text_sha256`、`clause_span`、`clause_text_sha256`、`replacement_value`、`preserved_constraint_ids`、`edit_ready` |

`RuleExtraction.gaps` 的每项记录 `code`、`relative_path`、`line_start`、`line_end` 和 `reason`；它不是 `PolicyIssue`，但会降低 `f04.rule_extraction` 的覆盖状态。`PolicyState.inactive_decisions` 的每项记录 `decision_id`、`reason` 和可选的 `issue_code`。

## 登记文件格式

登记文件是 UTF-8 JSON，建议扩展名 `.json`。当前为 `schema_version: 1`。顶层只允许以下键：

| 键 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | `int` | 是 | 必须为 `1` |
| `project_id` | `str` | 否 | 若提供，必须与本次 `AuditRequest.project_id` 相同 |
| `synthetic` | `bool` | 否 | 默认 false；仅合成登记显式设置 true |
| `rules` | `array` | 否 | 结构化规则解释，默认空数组 |
| `decisions` | `array` | 否 | Decision，默认空数组 |
| `confirmations` | `array` | 否 | Confirmation，默认空数组 |
| `behavior_checks` | `array` | 否 | 行为检查配置，默认空数组 |

静态登记中所有 `source_ref` 均为 `SourceLocator`，运行期绑定后使用 `EvidenceRef`。以下示例的摘要仅用于展示形状，验收夹具使用实际字节计算。

```json
{
  "schema_version": 1,
  "project_id": "stale-users-api",
  "rules": [
    {
      "rule_id": "rule-agents-clients-v1",
      "source_ref": {
        "relative_path": "AGENTS.md",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "line_start": 3,
        "line_end": 3
      },
      "text": "- New users clients under `clients/` must call `/api/v1/users`.",
      "text_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "scope": {"include_prefixes": ["clients"], "exclude_prefixes": []},
      "harness": "codex",
      "interpretation_status": "structured_confirmed",
      "confirmation_ref": "conf-rule-v1",
      "constraints": [
        {
          "constraint_id": "c-agents-clients-path",
          "subject": "users_endpoint",
          "operator": "equals",
          "value": "/api/v1/users",
          "conditions": {},
          "span": {"start": 2, "end": 63}
        }
      ]
    }
  ],
  "decisions": [
    {
      "decision_id": "decision-users-v2",
      "status": "accepted",
      "scope": {"include_prefixes": ["clients"], "exclude_prefixes": []},
      "effective_from": "2026-10-01T00:00:00+08:00",
      "effective_until": null,
      "supersedes": ["rule-agents-clients-v1"],
      "source_ref": {
        "relative_path": "docs/adr/0002-users-api.md",
        "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        "line_start": 3,
        "line_end": 6
      },
      "confirmation_ref": "conf-decision-v2",
      "constraints": [
        {
          "constraint_id": "c-decision-clients-v2",
          "subject": "users_endpoint",
          "operator": "equals",
          "value": "/api/v2/users",
          "conditions": {}
        }
      ]
    }
  ],
  "confirmations": [
    {
      "confirmation_id": "conf-rule-v1",
      "kind": "rule_interpretation",
      "source_ref": {
        "relative_path": "AGENTS.md",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "line_start": 3,
        "line_end": 3
      },
      "approved_items": [
        {
          "item_id": "rule-agents-clients-v1",
          "source_ref": {
            "relative_path": "AGENTS.md",
            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "line_start": 3,
            "line_end": 3
          }
        }
      ]
    },
    {
      "confirmation_id": "conf-decision-v2",
      "kind": "decision_approval",
      "source_ref": {
        "relative_path": "docs/adr/0002-users-api.md",
        "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        "line_start": 3,
        "line_end": 6
      },
      "approved_items": [
        {
          "item_id": "decision-users-v2",
          "source_ref": {
            "relative_path": "docs/adr/0002-users-api.md",
            "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
            "line_start": 3,
            "line_end": 6
          }
        }
      ]
    },
    {
      "confirmation_id": "conf-check-users-path",
      "kind": "check_configuration",
      "source_ref": {
        "relative_path": "docs/adr/0002-users-api.md",
        "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        "line_start": 3,
        "line_end": 6
      },
      "approved_items": [
        {
          "item_id": "check-users-path",
          "source_ref": {
            "relative_path": "docs/adr/0002-users-api.md",
            "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
            "line_start": 3,
            "line_end": 6
          }
        }
      ]
    }
  ],
  "behavior_checks": [
    {
      "check_id": "check-users-path",
      "subject": "users_endpoint",
      "detector": "python_assignment_literal",
      "scope": {"include_prefixes": ["clients"], "exclude_prefixes": []},
      "parameters": {
        "symbol": "USERS_PATH",
        "decision_id": "decision-users-v2"
      },
      "confirmation_ref": "conf-check-users-path"
    }
  ]
}
```

### Rule 条目

登记 Rule 在 F01 `RuleSpan` 基础上增加 `text_sha256`、`constraints` 和 `relative_path`，属于 F04 的登记扩展；提取结果本身仍保留 F01 字段。

| 字段 | 类型 | 必填 | 校验 |
| --- | --- | --- | --- |
| `rule_id` | `str` | 是 | 全局唯一，1–128 个可打印字符 |
| `source_ref` | `SourceLocator` | 是 | 路径、摘要和行段经 F02 验证后绑定 |
| `text` | `str` | 是 | 必须与 F02 读取结果的 `text` 精确相等（按 `\n` 连接，不含行终止符） |
| `text_sha256` | `str` | 条件 | 64 位小写十六进制；语义是 F02 的 `span_sha256`，覆盖原始行段字节（含原始行终止符），不是对 `text` 重算；`structured_confirmed` 必有 |
| `scope` | `Scope` | 是 | 必须是 `RuleSource.source_scope` 的子集 |
| `harness` | `str` | 是 | 与 `RuleSource.harness` 相同；源为 `shared` 时可收窄 |
| `interpretation_status` | `str` | 是 | 登记中只允许 `structured_confirmed` 或 `unresolved` |
| `confirmation_ref` | `str \| null` | 条件 | `structured_confirmed` 必须非空 |
| `constraints` | `array` | 条件 | `structured_confirmed` 至少一项；`unresolved` 必须为空 |

`model_candidate` 不允许写入用户登记。模型候选由 F06 的 `submit_candidates` 提交，F04 不读取、不合并，从来源上隔离两者。

### 提取结果 RuleSpan

`extract_rule_spans` 返回的 `RuleSpan` 保留契约字段，并附加块结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | `str` | 程序生成的稳定 ID |
| `relative_path` | `str` | 规则文件根目录相对路径 |
| `source_ref` | `EvidenceRef` | 该块对应的证据行段 |
| `text` | `str` | 精确原文，规则见“读取与摘要” |
| `text_sha256` | `str` | F02 `span_sha256`，含原始行终止符 |
| `scope` | `Scope` | 来自 `RuleSource.source_scope` |
| `harness` | `str` | 来自 `RuleSource.harness` |
| `interpretation_status` | `str` | 提取阶段固定为 `unresolved` |
| `block_kind` | `str` | `list_item` / `paragraph` / `quote` |
| `section_path` | `tuple[str, ...]` | 所属标题路径 |
| `parent_rule_id` | `str \| null` | 嵌套列表的父项 |
| `context` | `str` | `active` / `quoted` / `historical` / `unsupported` |
| `attachments` | `tuple` | 代码块/说明段的行段与摘要，供 F06/F08 读取 |

提取结果本身不带约束。约束只来自用户登记并与这些块匹配；未登记块保持 `unresolved`，不会被程序自动解释。

### Decision 条目

| 字段 | 类型 | 必填 | 校验 |
| --- | --- | --- | --- |
| `decision_id` | `str` | 是 | 全局唯一 |
| `status` | `str` | 是 | `draft` / `accepted` / `withdrawn` |
| `scope` | `Scope` | 是 | 决定替代关系和作用范围 |
| `effective_from` | ISO 8601 | 是 | 必须带时区 |
| `effective_until` | ISO 8601 \| null | 否 | 必须带时区；晚于 `effective_from` |
| `supersedes` | `array[str]` | 是 | 只列明确 ID，可为空；不得重复 |
| `source_ref` | `SourceLocator` | 是 | 决策原文的项目内位置 |
| `confirmation_ref` | `str \| null` | 是 | `accepted` 应为非空；空值进入待确认，不静默生效 |
| `constraints` | `array` | 是 | 可为空；空数组合法表示“只有退役/撤回，没有新义务” |

### Confirmation 条目

| 字段 | 类型 | 必填 | 校验 |
| --- | --- | --- | --- |
| `confirmation_id` | `str` | 是 | 全局唯一 |
| `kind` | `str` | 是 | `decision_approval` / `rule_interpretation` / `check_configuration` |
| `source_ref` | `SourceLocator` | 是 | 绑定后须覆盖所批准条目的同一证据行段 |
| `approved_items` | `ApprovedItem[]` | 是 | 每项为 `{item_id, source_ref}`；非空；`item_id` 不重复；类型必须与 `kind` 一致 |
| `edit_approvals` | `EditApproval[]` | 否 | 默认空数组；只允许在 decision_approval 中批准同一记录已批准的决策，形状见[公共契约](../contracts.md) |

`ApprovedItem` 只有 `item_id` 和 `source_ref` 两个字段。类型映射固定：

| `kind` | 被批准条目 |
| --- | --- |
| `decision_approval` | `decisions[]` 中的 `decision_id` |
| `rule_interpretation` | `rules[]` 中的 `rule_id` |
| `check_configuration` | `behavior_checks[]` 中的 `check_id` |

未知 `item_id` 记 `unknown_approved_item`，不提升任何条目的权威。

### Constraint 条目

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `constraint_id` | `str` | 否 | 同一 owner 内唯一；缺失时按 owner 和序号生成稳定 ID |
| `subject` | `str` | 是 | 同一主题才比较 |
| `operator` | `str` | 是 | 首版仅 `equals` / `not_equals` |
| `value` | `Scalar` | 是 | 字符串、数字或布尔；精确类型和值比较，不做 Unicode 归一化 |
| `conditions` | `object` | 是 | 已确认键值；值限字符串、数字或布尔；可为空 |
| `span` | `object` | 否 | `{"start": int, "end": int}`，0 基、左闭右开；规则约束指向该规则 `text`，Decision 约束指向其 `source_ref` 的 F02 文本 |

`span` 用于复合条款的子义务定位和删除门槛。规则约束没有 `span` 时仍可作确定性冲突判断，但 F08 只能把编辑标为待确认；Decision 约束通常不需要 `span`。

### BehaviorCheck 条目

| 字段 | 类型 | 必填 | 校验 |
| --- | --- | --- | --- |
| `check_id` | `str` | 是 | 全局唯一 |
| `subject` | `str` | 是 | 必须对应某一有效 Decision 的 `Constraint.subject` |
| `detector` | `str` | 是 | `python_assignment_literal` / `structured_check_result` |
| `scope` | `Scope` | 是 | 必须是来源 Decision `scope` 的子集 |
| `parameters` | `object` | 是 | detector 专属；见下 |
| `confirmation_ref` | `str \| null` | 否 | 非空且有效时才参与机械计数；否则只作为建议 |

`python_assignment_literal` 的 `parameters` 必须含 `symbol`（合法 Python 标识符）和 `decision_id`。F04 从该 Decision 中解析同主题的期望值，输出 `expected_value` 供 F05 使用；配置中不接受第二份期望值，避免两处真值漂移。`structured_check_result` 至少含 `decision_id` 和 `result_check_id`，具体结果格式由 F05 校验。只有 `kind="check_configuration"` 的确认批准该 `check_id` 时才生效。

### 结构错误与限制

以下情况抛出 `RegistryFormatError`，字段路径写到具体位置，例如 `decisions[1].effective_from`：

| code | 条件 |
| --- | --- |
| `invalid_json` | JSON 语法错误、重复键、根不是对象 |
| `missing_field` | 必填字段缺失 |
| `wrong_type` | 字段类型错误 |
| `unknown_enum` | 状态、detector、operator、kind 等枚举非法 |
| `unknown_field` | 出现未声明的字段（便于发现拼写错误） |
| `duplicate_id` | 规则、决策、确认或检查 ID 重复 |
| `invalid_scope` | 绝对路径、`..`、反斜杠、空组件、控制字符 |
| `invalid_time` | 无时区、无法解析、`effective_until <= effective_from` |
| `invalid_span` | 负数、倒置、超出 `text` 范围 |
| `limit_exceeded` | 超过下文限制 |
| `project_id_mismatch` | 登记 `project_id` 与请求不一致 |

限制：登记文件不超过 1 MiB；`rules`、`decisions` 各不超过 1,000 条；`confirmations` 不超过 2,000 条；`behavior_checks` 不超过 500 条；全部 `constraints` 不超过 10,000 条；每个 `Scope` 的 include/exclude 各不超过 100 项；每个 `conditions` 不超过 20 个键。

## 规则定位

### 读取与摘要

F04 只通过 F02 的 `read_evidence` 读取规则文件，不直接访问磁盘。按 F02 的续读字段循环读取，单次最多 80 行、8 KiB；块跨读取边界时拼接完整。若仍被截断或编码无效，记录 `text_truncated` / `encoding_invalid`，该来源不计为完整提取。

- F02 的 `text` 将选中行用 `\n` 连接，行终止符不进入文本；原始 LF/CRLF 保留在快照字节中，由 `span_sha256` 覆盖。
- 登记的 `text` 必须等于 F02 `text`；登记的 `text_sha256` 必须等于 F02 `span_sha256`（含原始行终止符的原始字节摘要）。字段名沿用下游 `RuleBinding.text_sha256`，不另造第二套摘要。
- 规则 ID 由程序生成：`rule_<sha256(relative_path + "\0" + line_start + "\0" + line_end + "\0" + harness + "\0" + span_sha256)[:16]>`。行终止符或字节内容变化会改变 ID，旧确认因此失效，而不是被静默沿用。
- 登记规则通过 `source_ref`、`harness`、`text` 和 `text_sha256` 与提取结果匹配，不依赖登记者知道生成 ID。`source_ref` 必须与某个提取块逐字段相同（同一证据、同一起止行），否则返回 `rule_span_not_found`。匹配成功后，报告的 `related_rule_ids` 使用登记中的 `rule_id`。

### Markdown 块

使用行式块解析，不引入第三方 Markdown 依赖：

| 块 | 处理 |
| --- | --- |
| ATX / Setext 标题 | 更新 `section_path`，标题本身不是义务 |
| 列表项（`-`、`*`、`+`、有序） | 每个项生成一个规则块；缩进续行并入该项 |
| 普通段落 | 生成规则块；连续非空行合并 |
| 围栏代码块 | 不作为规则；附加到同一节中最近的规则块 |
| 引用块 `>` | 生成 `context="quoted"` 的块，不作为当前规则参与比较 |
| 表格 | 首版不解释；记录 `unsupported_table` 提示，不生成规则 |
| HTML 注释、frontmatter | 跳过 |

每个块保留 `line_start`、`line_end`、`section_path`、`block_kind`、`parent_rule_id`（嵌套列表）、`context` 和 `attachments`。`attachments` 记录代码块或说明段的行段与摘要，供 F06/F08 读取上下文；F04 的比较不使用附件内容。

`context` 取值：

- `active`：当前规则，可参与比较。
- `quoted`：引用块，保留为候选材料，不参与确定性冲突。
- `historical`：位于标题含 `History`、`Historical`、`Changelog`、`Deprecated`、`Superseded`、`历史`、`变更记录`、`已废弃` 的小节。
- `unsupported`：表格等未实现结构。

只有 `active` 的登记规则能成为当前规则。登记指向 `quoted` / `historical` 时返回 `rule_not_current`，不生成支持性结论。同一句话同时出现在当前规则和历史引用中时，两处是不同证据块，历史引用不会被当成第二个当前规则。

### 范围

`Scope` 统一为：

```json
{"include_prefixes": ["clients"], "exclude_prefixes": ["clients-old"]}
```

- 前缀使用根目录相对路径，`/` 分隔，无前导 `./` 或尾部 `/`。
- `"."` 表示项目全部文件；内部规范化为根前缀，输出时还原为 `"."`。
- `include_prefixes` 为空数组表示空范围，不表示全部；`["."]` 才是全部。
- 按路径组件比较：`clients` 覆盖 `clients/users.py`，不覆盖 `clients-old/users.py`。
- `exclude_prefixes` 优先于 include；排除值等于或宽于某个 include 时，该 include 消失。
- 输出按字典序排序并去重。

范围运算：

| 运算 | 规则 |
| --- | --- |
| `intersection(a, b)` | 对 include 两两取较窄前缀；若互不覆盖则无交集；再用两边 exclude 过滤 |
| `covers(a, b)` | `b` 的每个 include 都被 `a` 的某个 include 覆盖，且不与 `a` 的任何 exclude 相交 |
| `intersects(a, b)` | `intersection(a, b)` 非空 |

示例：

| A | B | 交集 |
| --- | --- | --- |
| `clients` | `clients-old` | 空 |
| `clients` | `clients/new` | `clients/new` |
| `.` | `clients`，排除 `legacy` | `clients`，排除 `legacy` |
| `clients`，排除 `legacy` | `clients` | `clients`，排除 `legacy` |
| `legacy` | `clients` | 空 |

条件按已确认键值求交：同一键值相等则兼容；同一键不同值则不相交；不同键可同时成立，合并后取更窄条件。任何自然语言条件都不进入 `conditions`，而留在 `unresolved` 解释或 F06 候选中。

## 决策生效与确认

### 生效条件

Decision 在时点 `as_of` 有效，必须同时满足：

1. `status == "accepted"`。
2. `confirmation_ref` 指向有效 Confirmation，且该 Confirmation 批准此 `decision_id`。
3. `effective_from <= as_of`。
4. `effective_until is null or as_of < effective_until`。

时间统一转 UTC 比较，接受任意合法时区偏移。`as_of == effective_from` 时生效；`as_of == effective_until` 时已失效。

### 确认有效性

Confirmation 对某一条目有效，必须同时满足：

- `source_ref` 属于本次运行，摘要、行段和文件内容一致。
- `source_ref` 覆盖每个 `approved_items[].source_ref`，后者又必须与该条目的 `source_ref` 相同。`check_configuration` 没有自己的条目来源，`approved_items[].source_ref` 指向其 `parameters.decision_id` 所指 Decision 的 `source_ref`。
- `approved_items` 中存在 `item_id` 等于该条目 ID 的对象，且 `kind` 与条目类型一致。
- 条目反过来引用该 `confirmation_ref`；单向列出不生效。

摘要不一致返回 `confirmation_stale`，不再使用该确认。原文行段移动但文件摘要未变时，`source_ref` 行段仍按当前证据校验；行段越界返回 `line_out_of_range`。已登记的 `text_sha256` 与当前证据不一致时返回 `rule_text_mismatch`，等同于确认过期。

### 不使用时间戳授予权威

以下内容都不改变权威：

- ADR 中的 `Status: accepted`、`Superseded by ...` 字样。
- 文件 mtime、Git 作者时间、提交顺序。
- `Evidence.source_revision` 的新旧。
- 文本长度、是否最近被引用。

这些只能作为 F06 候选或报告线索。没有登记与确认时，`effective_decisions` 为空，F04 的覆盖状态必须说明确定性判断未执行。

### 未生效原因

`inactive_decisions` 记录 `decision_id`、`reason` 和 `issue_code`：

| reason | 条件 |
| --- | --- |
| `status_draft` | 草稿 |
| `status_withdrawn` | 已撤回 |
| `confirmation_missing` | 无确认引用 |
| `confirmation_invalid` | 确认缺失、未批准、类型不符或摘要过期 |
| `not_yet_effective` | `effective_from > as_of` |
| `expired` | `effective_until <= as_of` |

草稿、未来生效和已撤回是正常未生效状态，不产生 `stale_rule`，也不要求用户回答；只有 `accepted` 但确认无效时输出 `insufficient_evidence` 待确认项。

## 替代关系

### 图与边

- 只从**有效 Decision** 出边。`supersedes` 可指向登记中的规则 ID 或另一个决策 ID。
- 直接边生效，不做传递闭包。A 替代 B、B 替代 C 不自动推出 A 替代 C。
- 目标不存在：`unknown_supersedes`，忽略该边。
- 目标是未登记或非当前规则：`supersedes_non_current_rule`，忽略该边。
- 目标是未生效决策：`supersedes_inactive_decision`，只记提示，不产生替代。
- 决策-决策边形成环（含自环）：`supersession_cycle`，环内所有替代边不用于生成 `stale_rule`；这些决策的约束仍可用于冲突判断。环作为待确认项，不自动选边。
- 悬空边只影响该边，同一 Decision 的其他合法边仍处理。

### 替代计算

对每个有效 Decision `D` 的每条合法边 `D -> R`：

1. 计算 `S = intersection(R.scope, D.scope)`。`S` 为空则 D 在该范围没有替代权。
2. 对 R 的每个约束 `r`，找 D 中 `subject` 相同且条件与 `S` 兼容的约束。
3. 无同主题约束时：
   - 若 `D.constraints` 为空，视为显式退役/撤回；
   - 若 `D.constraints` 非空，视为没有可比替代，返回 `replacement_missing`，不生成确定结论。
4. 有同主题约束时，按 `equals` / `not_equals` 判断是否冲突。相同值或兼容值不产生过时候选，只记录评估结果。
5. 冲突产生时，候选只覆盖 `S` 和合并后的条件，不扩大到 D 未覆盖的范围。

### 动作选择

| 情况 | 输出 | `suggested_action` |
| --- | --- | --- |
| 有替代值，D 覆盖 R 全部范围 | `stale_rule`，替换具体约束 | `update` |
| 有替代值，D 只覆盖 R 部分范围 | `stale_rule`，scope 为交集 | `narrow_scope` |
| 显式退役，D 覆盖全部范围，规则解释覆盖全部义务 | `stale_rule`，可删除该条 | `delete` |
| 显式退役，覆盖全部但解释不完整 | `insufficient_evidence`，保留原文 | `ask_user` |
| 显式退役，只覆盖部分范围，解释完整 | `stale_rule`，scope 为交集 | `narrow_scope` |
| 显式退役，只覆盖部分范围，解释不完整 | `insufficient_evidence`，保留原文 | `ask_user` |
| 同主题但无条件交集，或替代值相同 | 不生成候选 | — |
| 多个有效决策给出不同替代值且范围重叠 | `rule_conflict` + 规则待确认，不选边 | `ask_user` |

`update` 不表示删除。F04 只说明哪个约束被哪个值替代，并把保留义务写入 `edit_targets[].preserved_constraint_ids` 和 `facts.remaining_obligations`。复合条款只替换命中的子条款，未命中的测试要求、兼容例外和说明必须原样保留。

规则“解释完整”指：去掉列表标记和首尾空白后，`text` 的每个非空白字符都至少被一个 `constraint.span` 覆盖，且所有 span 与 `text_sha256` 校验通过。没有 `span` 的规则只能 `update`，不能 `delete`。

### 删留边界

- 只有全部结构化义务被明确退役、范围完整覆盖且解释完整时，F04 才给出 `delete`。
- 复合条款只替换命中的子条款；测试要求、兼容例外、说明和代码示例保留。需要删除整条时必须由 F07/F08 再次核对保留义务。
- 内容属于阶段进度、任务待办或迁移计划时，F04 不根据关键词删除，也不建议迁移；这属于 F06 的语义候选，由 F07 输出 `cleanup_candidate`，F08 仍要求确认。
- 长期未被引用、文本较长、文件较旧只构成审阅线索，不产生 `delete`，也不提升另一版本的权威。
- 已接受决策彼此冲突且替代关系未明时，输出 `rule_conflict` 和待确认项，不自动选边。

### 保留范围与例外

D 的范围不覆盖的区域仍保留 R 的原约束。报告用以下方式表达：

- 候选的 `scope` 只写交集，不写 R 的完整范围。
- `details.preserved_scope_note` 明确“未覆盖部分仍按原规则执行”，不伪造无法精确表示的差集。
- `edit_targets[].preserved_constraint_ids` 列出同一条规则中未被替代的约束。
- `legacy/` 等兼容例外如果本身是独立规则，且与 D 范围不相交，则不出现在 `facts.rule_id`，也不进入 Diff 目标。

## 冲突判定

### 参与对象

只比较以下已确认约束：

- 当前 `structured_confirmed` 规则中的约束。
- 有效 Decision 中的约束。

同一 owner 内部的多条约束也参与比较，例如同一规则同时要求 v1 和 v2。未确认规则、历史引用、模型候选和未生效 Decision 不参与确定性冲突。

### 跳过条件

- 两条约束的 scope 交集为空。
- 条件在同一键上取值不同，交集为空。
- 工具上下文不兼容：两个不同的具体 `harness` 不互相比较，只记录 `cross_harness_not_compared`。`shared` 与任意具体工具兼容。
- 一对约束已被一条直接替代边覆盖其交集。被替代的关系输出 `stale_rule`，不再重复报冲突。
- 同一约束对已合并，不重复输出。

### 冲突真值

| A | B | 冲突 |
| --- | --- | --- |
| `equals v1` | `equals v2` | 是 |
| `equals v1` | `not_equals v1` | 是 |
| `equals v1` | `not_equals v2` | 否 |
| `not_equals v1` | `not_equals v2` | 否 |
| 相同 operator 和相同 value | — | 否 |

冲突候选的 `scope` 为交集，`facts.conditions` 为合并条件，`suggested_action="ask_user"`。F04 只确认“两条已确认约束在该范围内不能同时满足”，不判断哪条是权威，也不自动选边；最终 `assessment` 由 F07 判断。

两份 accepted Decision 互相矛盾且没有替代关系时，同样输出一个 `rule_conflict`，`facts.left_id` 和 `facts.right_id` 列出两者，`facts.authority="unresolved"`。这属于成功完成的审计发现，不是运行失败。

## 输出

### ComparisonResult

```text
as_of
candidates: tuple[StaticCandidate, ...]
evaluations: tuple[ConstraintEvaluation, ...]
issues: tuple[PolicyIssue, ...]
coverage: tuple[CoverageItem, ...]
policy: PolicyState
edit_targets: Mapping[candidate_id, EditTarget]
```

`candidates` 是 F07 的正式输入，不冒充最终 `Finding`。F04 不写 `assessment` 或 `basis`；F07 重新核对事实、引用和门槛后生成结论。F04 可以声明哪个候选属于确定性事实，但这只通过 `facts` 和 `suggested_action` 表达，不预先填最终等级。

`evaluations` 记录每一对已比较约束的结果，供 F07 复核替代与冲突，不要求 F07 重算范围：

```text
left_id, right_id, subject, status(compatible|incompatible|unresolved),
reason, scope, conditions, left_value, right_value
```

- `incompatible`：范围与条件相交，operator/value 不能同时满足。
- `compatible`：范围或条件不相交，或值相同/兼容。
- `unresolved`：登记解释不足、跨 harness、替代关系未明等，不能下确定结论。

### StaticCandidate

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `candidate_id` | `str` | F04 稳定 ID，见下 |
| `origin` | `str` | 固定 `rules` |
| `kind` | `str` | `stale_rule` / `rule_conflict` / `insufficient_evidence` |
| `claim` | `str` | 面向报告的短句，含 ID 和值；不作为判定依据 |
| `scope` | `Scope` | 候选成立的交集范围 |
| `facts` | `object` | 已算好的结构化事实，见下 |
| `support_refs` | `EvidenceRef[]` | 规则和决策原文 |
| `counter_refs` | `EvidenceRef[]` | 反证；没有时为空 |
| `missing_evidence` | `string[]` | 缺口代码 |
| `suggested_action` | `str` | 契约动作枚举，不表示 F08 已获授权 |
| `details` | `object` | F04 扩展，可为空；F07 不依赖 |

`facts` 按 kind 至少提供：

| kind | 必需事实 |
| --- | --- |
| `stale_rule` | `rule_id`、`decision_id`、`decision_ids`（多决策时）、`subject`、`clause_id`、`covered_clause_ids`、`remaining_obligations`（约束 ID 数组） |
| `rule_conflict` | `left_id`、`right_id`、`subject`、`authority="unresolved"` |
| `insufficient_evidence` | `gap_key`、`missing_items`、`related_rule_ids` 或 `related_decision_ids` |

`stale_rule` 的 `decision_id` 是直接出边并给出替代值的决策；同一事实涉及多个决策时 `decision_ids` 列全，主决策仍写入 `decision_id`。`stale_rule` 的 `facts` 可附 `old_value`、`new_value`、`preserved_scope_note`；`rule_conflict` 可附两边 operator/value。`details` 可带 `edit_target` 的引用，但 F08 应优先使用 `ComparisonResult.edit_targets`。

### EditTarget

`edit_targets` 只给 `stale_rule` 候选，字段如下：

```json
{
  "rule_id": "rule-agents-clients-v1",
  "source_ref": {"evidence_id": "ev-0001", "sha256": "<64-hex>", "line_start": 3, "line_end": 3},
  "line_start": 3,
  "line_end": 3,
  "old_text_sha256": "<64-hex>",
  "clause_span": {"start": 2, "end": 63},
  "clause_text_sha256": "<64-hex>",
  "replacement_value": "/api/v2/users",
  "preserved_constraint_ids": ["c-agents-tests"],
  "edit_ready": true
}
```

- `old_text_sha256` 是 F02 对整行段原始字节的 `span_sha256`，与 F08 的 `old_text_sha256` 对齐。
- `clause_span` 是 `constraint.span`，0 基、左闭右开，指向 F02 `text`（`\n` 连接后的文本）。
- `clause_text_sha256` 是子串 UTF-8 摘要，用于 F08 组合整行替换文本。
- F08 用 `clause_span` 和 `replacement_value` 组合整行 `replacement_text`；F04 不生成整行文本，避免误改同一行的其他义务。
- `clause_span` 缺失时 `edit_ready=false`，`missing_evidence` 加 `constraint_span`。F08 必须要求确认，不能模糊匹配或猜测位置。
- `delete` 还要求 `old_text_sha256` 对应整条规则行段，且 `preserved_constraint_ids` 为空、解释完整。

### Candidate ID

同一 `as_of`、同一输入下 ID 稳定。按 kind、相关规则/决策 ID、规范 scope、条件、`facts.gap_key` 和 `as_of` 计算：

```text
candidate_id = "rules:" + kind + ":" + sha256(canonical_json(payload)).hexdigest()[:16]
```

不使用随机数或运行序号。F07 可以使用该 ID 合并重复候选，但必须自行验证 ID 对应的事实。

### PolicyIssue

```text
code, severity(error|warning|info), message, field_path|null,
related_ids, evidence_refs, blocking: bool
```

`blocking=true` 表示依赖该条目的确定性结论没有生成；F09 据此把对应覆盖项记为未完成，而不是“无问题”。

| code | 严重性 | 影响 |
| --- | --- | --- |
| `evidence_missing` | error | 引用不在本次运行，条目不可用 |
| `evidence_digest_mismatch` | error | 证据摘要不符，条目不可用 |
| `line_out_of_range` | error | 行段越界，条目不可用 |
| `registry_binding_failed` | error | 逻辑来源的路径、摘要或行段不能绑定 |
| `rule_text_mismatch` | error | 登记文本或 `text_sha256` 与证据不符 |
| `rule_span_not_found` | error | 登记规则无法匹配当前提取块 |
| `rule_not_current` | error | 规则位于引用/历史小节，不能作为当前规则 |
| `source_scope_mismatch` | error | 登记范围超出规则文件默认范围 |
| `harness_mismatch` | error | 登记工具与来源不一致 |
| `duplicate_rule_binding` | error | 两条登记规则绑定同一证据块 |
| `confirmation_missing` | error | accepted 决策没有确认 |
| `confirmation_not_found` | error | 确认 ID 不存在 |
| `confirmation_stale` | error | 确认引用的证据摘要已变化 |
| `confirmation_not_approved` | error | `approved_items` 中没有 `item_id` 等于条目 ID 的对象 |
| `confirmation_kind_mismatch` | error | 确认类型与条目类型不符 |
| `confirmation_source_not_covered` | error | 确认原文行段未覆盖条目来源 |
| `confirmation_backlink_missing` | error | 条目没有反向引用确认 |
| `unknown_approved_item` | warning | 确认为未知条目背书，忽略该项 |
| `unknown_supersedes` | error | 替代目标不存在 |
| `supersedes_non_current_rule` | error | 目标是历史/未找到规则 |
| `supersedes_inactive_decision` | info | 目标是未生效决策，无替代效果 |
| `supersession_cycle` | error | 替代环，相关边不用于结论 |
| `replacement_missing` | error | 声明替代但没有同主题可比约束 |
| `retirement_incomplete` | error | 退役但解释不覆盖全部义务，禁止删除 |
| `unresolved_interpretation` | warning | 规则只有未决解释，不能机械比较 |
| `behavior_check_unconfirmed` | warning | 检查配置未确认，仅作建议 |
| `behavior_check_scope_mismatch` | error | 检查范围超出决策范围 |
| `behavior_check_subject_mismatch` | error | 检查主题没有对应决策约束 |
| `behavior_check_decision_mismatch` | error | 检查引用的决策无效或不存在 |
| `cross_harness_not_compared` | info | 不同工具规则未做冲突比较 |
| `text_truncated` | error | 规则块跨读取边界后仍不完整 |
| `encoding_invalid` | error | 规则文件解码失败 |
| `unsupported_table` | info | 表格内容未解释 |
| `source_scope_unresolved` | error | F03 范围无法解析，规则只保留为线索 |

### 覆盖项

F04 输出五个 `CoverageItem`，`check_id` 固定：

| check_id | `passed` 条件 | `not_run` 条件 |
| --- | --- | --- |
| `f04.registry` | 登记结构已校验且所有使用的定位已绑定 | 未提供登记为 not_run；无法绑定的来源为 failed |
| `f04.rule_extraction` | 所有 F03 规则来源完整读取并解析；无规则为 `passed/none_found` | 存在截断、编码、读取或范围缺口 |
| `f04.supersession` | 所有有效决策和当前规则的替代关系已处理 | 登记缺失，或存在 blocking 条目使部分关系未判定 |
| `f04.constraint_comparison` | 所有可比较的已确认约束已比较 | 登记缺失，或存在 blocking 条目使部分约束未比较 |
| `f04.behavior_checks` | 所有登记检查已校验并解析期望值 | 无登记文件，或存在未确认/无效检查使机械计数未就绪 |

`f04.registry` 在空登记（数组全为空）时仍为 `passed`；没有登记文件则是 `not_run`。`passed` 不等于没有问题，只表示该检查确实执行完毕。

## 下游视图

F04 除候选外，还提供只读策略视图，避免 F05/F07/F08 各自重算有效期和范围。

### F05：规则绑定、约束解析与行为检查

`RuleBinding`：

| 字段 | 说明 |
| --- | --- |
| `rule_id` | 登记规则 ID |
| `text` | F02 `text`，`\n` 连接 |
| `text_sha256` | F02 `span_sha256`，含原始行终止符 |
| `scope` | 规则范围 |
| `harness` | 规则工具 |
| `source_ref` | 原文证据 |
| `interpretation_status` | 当前为 `structured_confirmed` |
| `constraint_ids` | 该规则登记的约束 ID |

```python
def resolve_applicable_rules(
    policy: PolicyState,
    relative_path: str,
    *,
    harness: str,
) -> tuple[RuleBinding, ...]: ...

def resolve_constraint(
    policy: PolicyState,
    subject: str,
    relative_path: str,
    *,
    harness: str,
    exception_rule_ids: Sequence[str] = (),
) -> ConstraintResolution: ...
```

历史时点由 `DecisionResolver` 提供：

```python
class DecisionResolver:
    def __init__(self, rule_spans, registry, evidence_store): ...
    def policy_at(self, as_of: datetime) -> PolicyState: ...
    def resolve_constraint(self, subject, relative_path, *, as_of, harness,
                           exception_rule_ids=()) -> ConstraintResolution: ...
```

`policy_at` 复用同一份规则提取和登记，只重算生效/替代视图；F05 不需要为每个事件重新读取登记。`resolve_constraint(policy, ...)` 是 `policy.as_of` 时点的便利入口。

解析算法（两个入口共用）：在 `as_of` 时点收集适用的已确认规则约束和有效决策约束，移除已被直接替代的旧约束，再合并：

| `status` | 条件 |
| --- | --- |
| `resolved` | 只剩一个可满足的值，或所有适用值相同 |
| `conflict` | 两个及以上不可同时满足的值 |
| `none` | 没有适用约束 |
| `unknown` | 解释、确认、范围或替代关系存在缺口 |

`ConstraintResolution` 字段：`status`、`operator`、`value`、`decision_id`、`constraint_id`、`rule_ids`、`exception_applied`、`gaps`。`exception_applied` 只由调用方显式传入的 `exception_rule_ids` 触发，F04 不从文字推断“兼容例外”；无法确定时保持 `false` 并写缺口。

`ConfirmedBehaviorCheck`：

| 字段 | 说明 |
| --- | --- |
| `check_id`、`subject`、`detector`、`scope`、`parameters` | 登记原值 |
| `rule_id` | 该主题被替代的主要规则；无法唯一确定时为 `null` |
| `rule_ids` | 该主题被替代的全部规则；可为空 |
| `constraint` | 解析出的约束（operator/value） |
| `decision_id`、`confirmation_ref` | 期望值与确认来源 |
| `expected_value` | 从有效 Decision 解析的期望值 |

只有 `confirmation_ref` 有效、Decision 有效且主题匹配的检查才进入 `effective_checks`；其余进入 `suggested_checks`，不参与机械计数。

### F07：候选、约束比较与范围关系

- `ComparisonResult.candidates` 使用 F07 的 `StaticCandidate` 信封；`facts` 字段按上文提供。
- `ComparisonResult.evaluations` 的 `status` 为 `compatible` / `incompatible` / `unresolved`，附 `subject`、`left_value`、`right_value`、`scope`、`conditions` 和 `reason`。
- `compare_constraint_pair(left, right)` 返回单个 `ConstraintEvaluation`；F04 的完整入口 `compare_constraints(...)` 返回全部候选和评估，不重载为二参数函数。
- `scope_relation(left, right)` 返回 `relation`（`equal` / `contains` / `contained_by` / `overlaps` / `disjoint`）和 `intersection`。
- `clauses(rule_id)` 返回 `ClauseBinding{clause_id, span, text, text_sha256, constraints}`，用于 F07/F08 检查 `covered_clause_ids` 和 `remaining_obligations`；`PolicyState.clauses(rule_id)` 与模块级 `clauses(policy, rule_id)` 等价。
- F07 不得把 `unresolved` 或 `insufficient_evidence` 候选升级为 `supported`；`incompatible` 也只表示冲突事实，不表示哪条权威。

### F08：规则记录、决策适用与确认

```python
policy.get_rule(rule_id) -> RuleRecord | None
policy.get_decision(decision_id) -> Decision | None
policy.get_confirmation(confirmation_id) -> Confirmation | None
policy.decision_applies(decision_id, rule_id_or_record, task_scope, *, as_of=None) -> DecisionEval
policy.confirmation_approves(confirmation_id, approval_key, *, as_of) -> ConfirmationEval
policy.editable_files: tuple[str, ...]
```

F08 交接中的 `registry` 指解析后的 `PolicyState`；原始 `Registry` 只保存登记内容，不能单独完成证据和确认校验。`as_of=None` 表示使用 `PolicyState.as_of`；传入其他时点须用 `DecisionResolver.policy_at(as_of)` 重新解析，避免在旧视图上误判。

`RuleRecord` 至少含 `rule_id`、`relative_path`、`line_start`、`line_end`、`text_sha256`、`interpretation_status`、`evidence_id`、`constraints`。`editable_files` 由 F10 从 F03 的 `registered_instruction_files` 投影。F08 仍须检查目标是当前有效规则，F04 的历史规则或登记不能扩大白名单。

`DecisionEval.status`：`accepted`、`inactive`、`out_of_scope`、`not_superseding`、`missing`、`conflict`。`ConfirmationEval.status`：`valid`、`missing`、`stale`、`expired`、`mismatch`、`unsupported`；`expired` 和 `mismatch` 只有在 `ApprovedItem` 增加 `valid_until`/`replacement_sha256` 后才可能返回，当前实现按 `unsupported` 处理。

F04 按公共契约计算已登记 `edit_approvals` 的键并查找完全匹配项，同时校验被批准决策及确认来源。只确认决策本身时，对具体改法返回 `missing`；过期返回 `expired`，有改法记录但不匹配返回 `mismatch`。F08 将这些情况转成待确认。

## 边界条件

| 场景 | 行为 |
| --- | --- |
| 未提供 `decision_registry` | 不生成任何确定性 stale/conflict；`f04.registry`、`f04.supersession`、`f04.constraint_comparison`、`f04.behavior_checks` 为 `not_run`；`f04.rule_extraction` 仍可按 F03 独立执行，不能报告“规则无问题” |
| 登记为空数组 | 登记检查 `passed`，其余检查按零条目执行；不产生候选 |
| `schema_version != 1` | `RegistryFormatError`，CLI 退出码 2 |
| accepted 决策缺确认 | 决策不生效，输出 `insufficient_evidence` 和 `confirmation_missing` |
| draft / withdrawn / 未来生效 / 已过期 | 决策不生效，不生成依赖它的 stale；不要求用户回答 |
| `as_of == effective_from` | 生效 |
| `as_of == effective_until` | 不生效 |
| `clients` 与 `clients-old` | 组件边界不同，无交集 |
| include 与 exclude 重叠 | exclude 优先 |
| 条件同键不同值 | 无交集，合法差异 |
| 条件键不同 | 可同时成立，交集为更窄条件 |
| `equals` 与 `not_equals` 同值 | 冲突 |
| 两个 `not_equals` | 不冲突 |
| 复合条款含测试/兼容义务 | 只替换命中子条款，保留其余义务 |
| 多个 accepted 决策矛盾且无替代 | `rule_conflict`，`ask_user`，不选边 |
| 替代环、悬空 ID | 记录错误/缺口，相关边不用于结论 |
| 同一语句出现在历史引用 | 标为 `quoted`/`historical`，不作为第二个当前规则 |
| 登记指向历史引用 | `rule_not_current`，不生成支持性 stale |
| 不同具体 harness 的规则 | 不做冲突比较，交 F07 判断 `policy_mismatch` |
| 表格、代码块 | 表格不解释；代码块作为附件，不作为规则 |
| CRLF、中文、无末尾换行 | `text` 按 F02 用 `\n` 连接；`text_sha256`/`span_sha256` 覆盖原始字节含终止符，不归一化 |
| 登记文件超过 1 MiB | `limit_exceeded`，退出码 2 |
| 登记或规则文件缺失/不可读 | 对应覆盖 `not_run`，不输出空成功报告 |
| 任何处理 | 不写入项目、不执行项目命令、不修改证据 |

## 本地测试

```sh
.venv/bin/python -m pytest tests/test_f04_decisions.py tests/test_f03_inspection.py tests/test_f02_evidence.py -q
```

本地测试依赖 F02/F03 的真实实现。测试只通过 `capture_file` / `inspect_project` / `read_evidence` 取得证据和范围，不得直接读项目文件伪造 `EvidenceRef`，也不得复制预期报告替代判定。

### 合成夹具

`tests/fixtures/f04/project/` 建议包含：

| 路径 | 内容 |
| --- | --- |
| `AGENTS.md` | 新客户端 v1、`legacy/` 兼容、更新测试三条规则 |
| `CLAUDE.md` | 同任务 v2，用于跨工具差异 |
| `clients/users.py` | `USERS_PATH = "/api/v2/users"` |
| `clients-old/users.py` | v1，用于相似目录负例 |
| `legacy/users.py` | v1，用于兼容例外 |
| `composite/AGENTS.md` | 同一行含 API 与测试两项义务 |
| `history/AGENTS.md` | 当前规则加 `## History` 引用块 |
| `docs/adr/0001-users-api.md` | 历史 v1 决策，`Status: superseded` |
| `docs/adr/0002-users-api.md` | accepted v2，含 `Supersedes` 与 `legacy/` 例外 |
| `docs/adr/0003-users-api.md`、`0004-*.md` | 用于冲突与替代环变体 |

测试 helper 从夹具真实字节填静态 `SourceLocator` 和行段摘要，再经 `parse_registry`、`bind_registry` 验证两次运行产生不同的 EvidenceRef。另验证摘要漂移只隔离受影响的条目。每个用例把项目复制到 `tmp_path`，记录修改前后文件摘要，断言审计没有改动项目。

### 验收用例

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| F04-01 | v2 决策已确认、生效且替代新客户端 v1 | 只生成一个 `stale_rule`；`scope` 为 `clients`；`facts.rule_id` 为旧规则 ID；`suggested_action="update"`；`facts.new_value="/api/v2/users"`；`legacy/` 兼容规则和测试规则不在 Diff 目标 |
| F04-02 | v2 是 draft、未来生效、已 withdrawn 或 `effective_until == as_of` | 四种情况均不生成 `stale_rule`；决策进入 `inactive_decisions`，原因正确；不产生要求用户选边的候选；覆盖仍说明检查已执行 |
| F04-03 | `legacy/` 例外、`clients-old/` 目录、部分范围和排除前缀 | `clients` 与 `clients-old` 无交集；`legacy` 例外不被替代；只覆盖 `clients/new` 时候选 scope 为 `clients/new` 且动作 `narrow_scope`；exclude 正确过滤 |
| F04-04 | 两份 accepted 决策矛盾且无替代关系 | 恰好一个 `rule_conflict`；`facts.left_id` 和 `facts.right_id` 同时列出两者；`suggested_action="ask_user"`；没有 `stale_rule`，没有自动选边 |
| F04-05 | 替代环、悬空 ID、旧摘要确认、确认未批准 | 分别出现 `supersession_cycle`、`unknown_supersedes`、`confirmation_stale`、`confirmation_not_approved`；相关条目进入 `insufficient_evidence`；坏条目不影响无关决策的合法比较 |
| F04-06 | 规则含 API 与测试两项义务 | 只对 API 子条款生成 `stale_rule`；`edit_targets[candidate_id].preserved_constraint_ids` 含测试约束；动作不是 `delete`；原文件中测试文本未被改动 |
| F04-07 | 更晚 mtime / Git 时间但无确认 | 不因时间更新提升权威；没有登记确认的 ADR 不产生决策；旧规则仍按已确认 v2 决策判定；结果与设置 mtime 前一致 |
| F04-08 | 相同语句出现在历史引用或条件不明处 | 历史块标为 `quoted`/`historical` 且不参与比较；登记指向历史块返回 `rule_not_current`；未决解释返回 `unresolved_interpretation`；不生成支持性 `stale_rule`/`rule_conflict` |
| F04-09 | BehaviorCheck 已确认、未确认和越界 | 已确认检查进入 `effective_checks`，`expected_value` 来自有效决策；未确认检查只进入 `suggested_checks`；越界检查报 `behavior_check_scope_mismatch`；未知 detector 抛结构错误 |
| F04-10 | 删除门槛 | 单条义务、span 覆盖全文、显式退役且解释完整时可 `delete`；复合条款或 span 不完整时只给 `ask_user`/`insufficient_evidence`，不生成可删除候选 |
| F04-11 | 登记结构错误与上限 | 非法 schema、缺字段、重复 ID、`../` 范围、无时区时间、重复 JSON 键均抛 `RegistryFormatError`，字段路径可定位；超限同样失败；项目文件无变化 |

## 通过与交接

审查者重点核对：

- 用户确认与模型候选是否从数据来源上分离，模型不能通过登记文件提升权威。
- Decision 是否只在状态、确认、时间和范围同时满足时生效。
- 范围是否按路径组件比较，`clients` 与 `clients-old` 是否被正确区分，exclude 是否优先。
- 复合条款是否只命中具体子条款，测试、兼容例外和说明是否保留。
- 冲突是否只在范围和条件交集内成立，是否坚持不选边。
- 同一语句的历史引用、草稿决策和更晚时间戳是否被正确降级。
- `ComparisonResult.coverage` 在缺少登记或材料时是否为 `not_run`，而不是“无问题”。
- 测试是否在实现被破坏后失败，是否实际调用了 F02/F03，而非复制预期结果。

下游接口：

- F05 使用 `PolicyState.effective_checks` 的 `expected_value`、`resolve_constraint` 和 `effective_decisions` 评估行为；历史事件可按 `occurred_at` 再次调用 F04，获取当时有效的决策。`RuleBinding.text_sha256` 与 F05 的绑定字段同义，均为 F02 `span_sha256`。
- F07 直接消费 `ComparisonResult.candidates` 和 `evaluations`，可因新证据降级，但不得把 `unresolved`/缺口候选提升为支持结论，也不得覆盖 F04 已算好的范围与替代事实。
- F08 使用 `ComparisonResult.edit_targets[candidate_id]`；`edit_ready=false` 时必须要求确认，不能模糊匹配整行。
- F09 汇总 `coverage` 和 `issues`，保留 `not_run`、合成标记和证据引用。
- F10 将 `RegistryFormatError` 映射为退出码 2；F04 覆盖为 `not_run` 时，请求过的确定性检查不得计为通过。
