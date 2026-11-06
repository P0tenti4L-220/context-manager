# F09 报告、覆盖率与运行产物

依赖 F02、F07、F08。属于 CLI 的报告接口层。本功能只把已核验的 `RunResult`、证据目录和修复预检结果渲染成文件，不重新判定、不重新读取被审计项目、不应用 Diff。公共对象见[契约](../contracts.md)，实际执行与审查流程见[本地验收规范](../acceptance.md)。

## 用户结果

用户打开 `out/<run_id>/report.md`，能回答五个问题：检查了什么、发现了什么、依据在哪个快照的哪几行、建议怎么处理、还缺哪些材料。同一目录里的 JSON 给出机器可读结果，`proposal.diff` 给出可审阅的确定补丁。原项目或本机模型随后变化、甚至被删除，这个目录仍能自行说明当时的证据和状态。

## 职责边界

| 本功能负责 | 本功能不负责 |
| --- | --- |
| 校验 `RunResult` 与依赖对象是否自洽，失败时给出结构化错误 | 生成 Finding、提升或降低 `assessment`、合并或删除 Finding |
| 计算覆盖与发现计数、整体状态展示、退出码建议 | 重新执行 F03–F06 的检查或重新读取原项目文件 |
| 生成 Markdown、JSON、`proposal.diff` 和 `trace.jsonl` | 重新生成或修补 F08 的 Diff，运行 `git apply` 到真实项目 |
| 把证据引用解析为本次运行目录内的相对链接 | 修改项目文件、模型配置或其他运行目录中的内容 |
| 原子写入、产物清单、未完成标记和并发隔离 | 决定 run ID、规定输出根目录位置、决定是否请求语义分析 |

报告层的复核结果只影响“是否还能把这次运行展示为 `complete`”，不会把 `hypothesis` 变成 `supported`，也不会新增诊断结论。

## 文件与接口

建议新增 `src/context_manager/reporting.py`、`tests/test_f09_reporting.py`。

### 入口

```python
def build_artifacts(
    run_result: RunResult,
    evidence_catalog: EvidenceCatalog,
    repair_plan: RepairPlan | None = None,
    *,
    trace: Sequence[TraceEvent] = (),
    incident_analysis: IncidentAnalysis | None = None,
    inspection_result: InspectionResult | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ArtifactBundle: ...

def write_run_artifacts(
    run_result: RunResult,
    output_root: Path,
    evidence_catalog: EvidenceCatalog,
    repair_plan: RepairPlan | None = None,
    *,
    trace: Sequence[TraceEvent] = (),
    incident_analysis: IncidentAnalysis | None = None,
    inspection_result: InspectionResult | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RunArtifacts: ...
```

`build_artifacts` 在内存中完成全部校验、排序和渲染，不写磁盘，供测试检查 Markdown 与 JSON 是否来自同一份数据。`write_run_artifacts` 先调用它并透传 trace 和事件分析，再把结果原子写入 `output_root/<run_id>/`。`clock` 只用于测试和可复现运行，默认取当前 UTC 时间；一次运行内所有时间字段使用同一个采样值。

本轮正式接线增加可选 `inspection_result`：输出 `inspection.json` 并在报告显示规则清单、作用范围与加载预期，使 F03 结果在 CLI 可见。它必须绑定同一 run_id，仍不等于实际加载证明。

原接口 `write_run_artifacts(run_result, output_root)` 增加证据目录参数，原因是报告链接和摘要复核必须绑定本次运行的实际快照，不能依赖模块级状态或重新访问原项目。

### 依赖对象的最小形状

F09 使用以下只读接口，字段形状按公共契约和接口矩阵。

`EvidenceCatalog` 由 F02 的 `Evidence` 记录和本次运行目录构造，只读；重开历史报告时可基于 F02 的 `open_existing(run_dir)` 重建：

| 成员 | 类型 | 含义 |
| --- | --- | --- |
| `run_id` | `str` | 必须与 `RunResult.run_id` 相同 |
| `root` | `Path` | 本次运行的产物目录，即 `output_root/<run_id>` |
| `index_artifact_path` | `str \| None` | F02 索引相对路径，取 `evidence/index.json` |
| `location(evidence_id)` | `EvidenceLocation \| None` | 不存在返回 null，由 F09 记录引用缺口 |
| `all_locations()` | `Mapping[str, EvidenceLocation]` | 报告和完整性检查使用 |

`EvidenceLocation` 来自 F02 的 `Evidence`，路径按 F02 规格的固定布局 `evidence/snapshots/<snapshot_id>/content.bin` 派生：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evidence_id` | `str` | 本次运行内的不透明证据 ID，F09 不解析其格式 |
| `artifact_path` | `str` | 相对 `root` 的 `/` 分隔路径，指向 `content.bin` |
| `sha256` | `str` | 原始捕获字节的摘要 |
| `byte_length` | `int` | 原始字节数 |
| `line_count` | `int` | 可引用行数，空文件为 0 |
| `logical_name` | `str` | `Evidence.relative_path`；外部输入为 `selected-input/<input_id>/<basename>` |
| `encoding` | `str` | `utf-8`、`utf-8-bom`、`invalid-utf8` 或 `binary` |
| `provenance_kind` | `str` | 归一化后的来源类型：`synthetic`、`project_file`、`user_supplied`、`adapter`；从 F02 的 `Evidence.provenance.kind` 取得 |

`content.bin` 必须是原始字节的完整副本，不能是加了行号、围栏或 JSON 包装的内容；否则 Markdown 行锚点和摘要复核都不成立。只有 `utf-8`、`utf-8-bom` 文本生成行锚点；二进制或非法编码只给文件链接并注明不能按行引用。

`RepairPlan` 由 F08 提供，对应其规格中的 `plan`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `run_id`、`as_of` | `str` | 必须与 `RunResult` 相同 |
| `proposals` | `list[RepairProposal]` | 与 `RunResult.proposals` 一致；至多一个 `preview_ready` |
| `diff` | `bytes \| None` | 所有可预览编辑合并后的唯一确定补丁 |
| `diff_proposal_id` | `str \| None` | `diff` 所属提案；无补丁时为 null |
| `advisories` | `list[RepairAdvisory]` | 迁移、源码、Hook、配置、加载、检索、新增检查等仅建议项 |
| `rejections` | `list[EditRejection]` | 路径越界、符号链接、非规则文件等拒绝记录 |
| `checks` | `list` | F08 的门槛、基准、保留、Diff 范围和 Git 预检记录 |
| `apply_check` | `ApplyCheckResult \| None` | 临时副本上的 `git apply --check` 与实应用摘要；由 F08 直接提供，形状见[接口矩阵](../interfaces.md) |

`ApplyCheckResult` 含 `exit_code: int 或 null`、`checked_at: str`、`checked_files: list[{path, sha256}]`、`applied_in_temp_copy: bool`。`advisories` 按 F08 的 `RepairAdvisory` 渲染，至少含 `advisory_id`、`kind`、`summary`，可带 `finding_ids`、`suggested_target`、`basis_refs`、`requires_confirmation`；`rejections` 按 F08 的 `EditRejection` 渲染，含 `edit_intent_id`、`reason`、`detail`，可带 `target_path`、`finding_ids`、`checks`。

F09 从 `diff_proposal_id` 对应提案读取 `diff_sha256`，并核对 `sha256(diff)` 与之一致；`repair_plan` 不需要另设 Diff 摘要字段。F08 的 `checks` 不并入顶层覆盖，也不参与整体状态判定。Git 预检失败只把相关提案标为 `blocked`，不能把已完成的审计降级为 `partial`；报告在第 3 节展示这些预检结果和退出码。

`RunArtifacts` 是 `write_run_artifacts` 的返回值：`run_dir`、`files`（名称到 `{path, sha256, byte_length, status}` 的映射）、`diff_path`、`effective_status`、`exit_code`、`incomplete`、`errors`。F10 用它打印报告位置并决定进程退出码，不需要重新扫描目录。

## 输入

`RunResult` 必须提供公共契约第 7 节约定的字段。F01 已把 `mode` 定为 `{command, semantic}`（F01 交接第 5 节第 4 条），F09 采用该形状，不再单设 `semantic_mode`。F09 还需要以下字段，需在 F01 确认后补入：

| 字段 | 必需 | 用途 |
| --- | --- | --- |
| `mode.semantic` | 是 | 展示“未请求 / 已请求 / 已执行”，并核对 trace |
| `synthetic` | 是 | F01 已增加的可选汇总标记；默认 `false`，用于合成材料标识 |
| `parameters` | 是 | 按公共契约含 `scope: Scope` 与可空的模型摘要；F09 只写出 `scope`、`model` 摘要和 `as_of` |
| `inputs` | 否 | 输入摘要列表；缺省为空列表，报告写“未记录外部输入” |
| `project_revision` | 否 | 写入 `run.json`，可为 null |
| `errors[].severity` | 否 | 区分 `error` 与 `warning`；缺失按 `error` 处理 |

`inputs` 每项为 `{input_id, kind, logical_name, sha256, byte_length, synthetic}`，`kind` 取 `decisions`、`incidents`、`harness_profile`。模型配置只出现在经过筛选的模型摘要中。`logical_name` 必须是逻辑名称或项目相对路径；绝对路径只保留文件名，并记 `input_path_sanitized` 警告，不把原路径写入任何产物。`parameters.scope` 缺失或非法时按结构错误退出 2。

`coverage` 必须覆盖本次请求的每一项检查，包括 `not_run`；F09 不会根据 `findings` 反推检查是否执行。语义检查的 `check_id` 统一为 `f06.semantic`，F09 用它核对 `mode.semantic`、覆盖状态与 trace 是否一致。`findings`、`proposals` 使用 F01 的统一序列化，字段不得增删；`repair_plan.proposals` 必须与 `RunResult.proposals` 的 ID 和状态逐一相同。

输入校验在渲染前一次完成，失败不创建目录、不写文件：

| 条件 | 结果 |
| --- | --- |
| `schema_version != 1`、必填字段缺失、未知枚举、`run_id` 非法 | `ReportInputError`，错误码 `invalid_input`，退出码 2 |
| `mode` 不是 `{command, semantic}` 或枚举未知 | `ReportInputError`，`invalid_mode`，退出码 2 |
| `run_id` 不是 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`，含斜杠、反斜杠、`..`、控制字符 | `ReportInputError`，`invalid_run_id`，退出码 2 |
| `evidence_catalog.run_id` 与 `run_result.run_id` 不同 | `ReportInputError`，`catalog_run_mismatch`，退出码 2 |
| `evidence_catalog.root` 与 `output_root/<run_id>` 解析后不是同一目录 | `ReportInputError`，`catalog_root_mismatch`，退出码 2 |
| `coverage` 中 `check_id` 重复 | `ReportInputError`，`duplicate_check_id`，退出码 2 |
| `coverage` 中 `failed` / `not_run` 项缺少原因 | `ReportInputError`，`coverage_reason_missing`，退出码 2 |
| `repair_plan.proposals` 与 `RunResult.proposals` 的 ID 或状态不一致 | `ReportInputError`，`proposal_set_mismatch`，退出码 2 |
| 有 `preview_ready` 但 `repair_plan`、`diff`、`diff_proposal_id` 为空，或 Diff 字节摘要与所属提案的 `diff_sha256` 不符 | `ReportInputError`，`diff_contract_violation`，退出码 2 |
| 有 `preview_ready` 但 `apply_check.exit_code != 0`、未在临时副本实应用或缺少预检摘要 | `ReportInputError`，`diff_not_prechecked`，退出码 2 |
| 无 `preview_ready` 却提供 Diff | `ReportInputError`，`diff_contract_violation`，退出码 2 |
| `proposals` 中任一 `run_id` 与本次运行不同 | `ReportInputError`，`cross_run_reference`，退出码 2 |
| 证据路径越界、经符号链接离开运行目录、引用行段倒置或越界、摘要不符 | 不拒绝整次运行；降级并记入错误，见“完整性与降级” |

错误对象采用 F01 的 `RunError` 形状 `{code, path, message}`，可带 `severity`；`path` 使用字段路径如 `findings[1].support_refs[0]`，不复述字段值。`ReportInputError` 携带退出码 2；`ArtifactCollisionError` 和 `ArtifactWriteError` 携带退出码 1。F09 不直接调用 `sys.exit`，由 F10 统一打印并映射进程退出码。

## 输出

```text
out/<run_id>/
├── run.json
├── report.md
├── findings.json
├── checks.json
├── proposals.json
├── proposal.diff          # 仅当存在 preview_ready 提案
├── incidents.json         # 仅 diagnose 且有事件分析结果
├── trace.jsonl            # 仅当 mode.semantic = local
└── evidence/              # F02 写入，本功能只链接，不复制、不改写
```

写入顺序固定为 `run.json`（占位清单）→ `findings.json` → `checks.json` → `proposals.json` → `proposal.diff` → `incidents.json` → `trace.jsonl` → `report.md` → `run.json`（最终清单）。JSON 统一 UTF-8、`ensure_ascii=False`、2 空格缩进、LF、末尾换行；`proposal.diff` 按原始字节写入，不转码、不补换行。

### run.json

| 字段 | 取值与来源 |
| --- | --- |
| `schema_version` | `1` |
| `kind` | `"run"` |
| `run_id`、`project_id`、`as_of` | 来自 `RunResult` |
| `mode` | F01 对象 `{command: "audit" \| "diagnose", semantic: "off" \| "local"}` |
| `created_at` | 单次 `clock` 采样，UTC ISO 8601 |
| `status` | 最终有效状态，见“完整性与降级” |
| `requested_status` | 原始 `RunResult.status`；未降级时为 null |
| `status_reason` | 降级或失败的代码，如 `evidence_ref_unresolved`；否则 null |
| `exit_code` | `complete` 0、`partial` 3、`failed` 1 |
| `synthetic` | 汇总标记：仅在 `synthetic_basis = synthetic` 时为 true |
| `synthetic_basis` | `synthetic`、`user_supplied` 或 `unknown`，见“合成标记” |
| `project_revision` | 可空 |
| `parameters` | `{scope: Scope, as_of, model: ModelSummary 或 null}`，按公共契约保留提示词版本和摘要 |
| `inputs` | 外部输入摘要 `{input_id, kind, logical_name, sha256, byte_length, synthetic}`；不含本机绝对路径 |
| `coverage_summary` | 程序计数的 `{total, passed, failed, not_run}` |
| `findings_summary` | `{total, supported, hypothesis, unknown}` |
| `proposals_summary` | `{total, preview_ready, needs_confirmation, blocked, advisories, rejections}`，全部由程序计数 |
| `artifacts` | `{run, report, findings, checks, proposals, diff, evidence_index, trace, incidents}`；不可用项为 null |
| `artifact_manifest` | F09 写入的文件清单 `[{path, sha256, byte_length, status}]`；不含 `run.json` 自身和 `evidence/` |
| `artifact_manifest_status` | 写入中为 `incomplete`，全部完成并校验后为 `final` |
| `integrity` | `{evidence_links: "passed" \| "failed", unresolved_refs: [{finding_id, evidence_id, path, reason}], downgraded: bool}` |
| `errors` | 输入错误和报告层完整性错误，按 `code` 排序，同码按 `path`；含严重性 |

`run.json` 不写绝对路径、输出根目录以外路径、模型服务地址、凭据或环境变量。`run.json.artifacts` 是“产物角色 → 相对路径”的映射，与 F01 `RunResult.artifacts` 的 `RelativePath[]` 含义不同；报告层以本表为准，不从输入列表反推。

### findings.json

```json
{
  "schema_version": 1,
  "kind": "findings",
  "run_id": "…",
  "generated_at": "…",
  "summary": {"total": 4, "supported": 2, "hypothesis": 1, "unknown": 1},
  "findings": []
}
```

`findings` 与 Markdown 使用同一份排序结果：先按 `supported`、`hypothesis`、`unknown`，再按 `finding_id` 的字节序。每项保留 F01 序列化的全部契约字段；报告层不添加或改写字段。

### checks.json

```json
{
  "schema_version": 1,
  "kind": "checks",
  "run_id": "…",
  "generated_at": "…",
  "summary": {"total": 7, "passed": 4, "failed": 1, "not_run": 2},
  "checks": []
}
```

`checks` 按 `check_id` 字节序排列，逐项含 `check_id`、`status`、`inputs`、`reason`、`artifact_refs`。`reason` 为空只允许出现在 `passed`；`failed` 和 `not_run` 必须有原因。

### proposals.json

```json
{
  "schema_version": 1,
  "kind": "proposals",
  "run_id": "…",
  "generated_at": "…",
  "summary": {"total": 2, "preview_ready": 1, "needs_confirmation": 1, "blocked": 0, "advisories": 2, "rejections": 1},
  "no_preview_reason": null,
  "diff": {
    "path": "proposal.diff",
    "sha256": "…",
    "byte_length": 412,
    "apply_check": {
      "exit_code": 0,
      "checked_at": "…",
      "applied_in_temp_copy": true,
      "checked_files": [{"path": "AGENTS.md", "sha256": "…"}]
    }
  },
  "proposals": [],
  "advisories": [],
  "rejections": [],
  "repair_checks": [],
  "apply_check": null
}
```

无 `preview_ready` 时 `diff` 为 null，`no_preview_reason` 必须为非空字符串，来源依次为：待确认问题汇总、`blocked` 原因、状态计数解释。`proposal.diff` 的字节长度和摘要与 `run.json.artifact_manifest`、`proposals.json.diff` 三处一致。

`advisories` 原样收录 F08 的仅建议项，按 `advisory_id` 排序；`rejections` 收录被拒绝的修改请求，按 `edit_intent_id` 排序；`repair_checks` 收录 F08 的门槛、基准、保留和 Git 预检记录，保留 `not_run`、失败原因和退出码。这三类都只描述“提案层”的事实，不进入 `checks.json` 的请求覆盖，也不改变整体运行状态。

### proposal.diff

只写 F08 提供的合并补丁字节。F08 至多产出一个 `preview_ready` 提案，并把其全部编辑合入同一份 Diff；`diff_proposal_id` 指向该提案，`proposals.json.diff.sha256` 取该提案的 `diff_sha256`，并与 `sha256(proposal.diff)` 一致。F09 不拼接、不重新生成、不修改头部。无 `preview_ready` 时不创建该文件，`run.json.artifacts.diff` 为 null。

预检失败且无 Diff 时，`proposals.json.apply_check` 保存实际的失败记录；成功预检记录留在 `diff.apply_check`，未执行 Git 时均为 null。

### incidents.json

仅在 diagnose 且提供 F05 `IncidentAnalysis` 时写入。信封为 `{schema_version, kind: "incidents", run_id, generated_at, analysis}`。F09 按 F05 的结果字段投影 `counts`、`dedup`、`coverage`、`loads`、`behaviors`、`recurrence`、`receipts`、`exclusions`、`gaps` 与 legacy 摘要。保留事件、独立任务和回执 ID、证据引用、合成标记、分类与稳定原因码，不写原始事件包、正文、自由文本声明或宿主路径。旧格式保留迁移提示与未知状态。文件纳入 `run.json` 角色映射和产物清单。

### trace.jsonl

`mode.semantic = local` 时创建，即使模型启动失败也保留错误记录；`off` 时不创建。投影 F06 事件：`run_start`、`model_call`、`tool_call` 对应同名类型，协议错误、拒绝和传输失败映射为 `error`，`stop` 映射为 `run_end`。每行一个 JSON 对象，允许的 `type` 为 `run_start`、`model_call`、`model_response`、`tool_call`、`tool_result`、`budget`、`error`、`run_end`。

每行只允许以下字段：`schema_version`、`seq`、`ts`、`run_id`、`type`、`status`、`tool`、`evidence_ids`、`line_start`、`line_end`、`args_digest`、`result_digest`、`error_code`、`elapsed_ms`、`reason`、`discarded_fields_count`（仅 run_end）。未知字段丢弃，丢弃数量写入最后一条 `run_end` 记录；不写提示词正文、模型完整回复、证据正文、服务地址或凭据。单行超过 16 KiB 截断并标记；文件超过 2 MiB 时写入一条 `{"type": "trace_truncated", "reason": "size_limit", ...}` 后停止。F06 未提供任何记录而 `mode.semantic = local` 时，F09 写入占位 `run_start` 和带 `error_code = semantic_trace_missing` 的 `error` 记录，并触发降级；占位记录不能当作语义会话已建立。

## report.md

固定五个部分，顺序不变，标题文本不包含用户数据：

```text
# 上下文体检报告
## 1. 范围与结果概览
## 2. 逐项发现
## 3. 修复提案
## 4. 未覆盖检查与缺口
## 5. 产物清单与复核
```

标题下先放一行程序生成的标记，不写用户文本：

```text
> 合成材料：是 / 否（含用户提供材料） / 未确认 · 运行 `run-…` · 状态 `partial` · 退出码 3
```

### 概览

概览由程序计算，不写死数字。至少包含：

- 运行、项目别名、`mode.command`、`mode.semantic`、`as_of`、最终状态与退出码。
- 范围前缀和输入逻辑名称。
- 覆盖计数、本次请求的检查 ID 与状态清单（`passed` / `failed` / `not_run` 全部列出）、语义分析状态（`未请求（--semantic off）`、`已执行`、`已请求但未建立会话`）。
- 发现计数、提案计数、建议与拒绝计数、Diff 路径与摘要。
- 一段确定性结论，按以下规则生成：
  - `failed`：说明未得到可用结果，后续内容不能作为项目结论。
  - 覆盖为空：说明没有可用检查记录，不能判断项目整体无问题。
  - 存在 `failed` 或 `not_run`：说明完成多少项、未完成多少项，结论只覆盖已完成部分。
  - 全部完成且有发现：说明发现数量；全部完成且无发现：说明本次范围内未发现需要处理的问题。
  - 有 `preview_ready`：提示存在可预览 Diff；没有：说明原因见第 3 节。

### 逐项发现

每项包含声明、等级、依据类型、范围、证据链接、反证、缺口、建议、检查来源、关联事件或规则。等级和依据使用“代码值（中文说明）”格式，例如 `` `supported`（有支持） ``、`` `model_candidate`（模型候选） ``，避免把假设读成已证实。

证据链接格式为：

```text
E-0007（`AGENTS.md` 第 3 行，sha256 `3f9a1c2d…`）[打开快照](evidence/snapshots/snap-7f3a9c/content.bin#L3)
```

链接目标只用 `EvidenceCatalog` 返回的 `artifact_path` 和校验过的行号，路径经百分号编码，片段为 `L3` 或 `L3-L5`。链接文本同时写明逻辑名称和行号；渲染器不支持行锚点时仍可按行号在快照中定位。二进制或非法编码证据只给文件链接并注明不能按行引用。引用无法解析时渲染为纯文本 `E-0099（引用不可解析）`，并在第 4 节和 `run.json.errors` 列出具体原因，不生成看似有效的链接。`supported` 发现只要有一个引用未通过复核，就在等级后标注“引用未通过报告复核”，不能继续显示为完全支持。

### 修复提案

第 3 节按以下四小节展开：

1. **可预览 Diff**：提案 ID、对应 Finding、基准文件及摘要、修改行段与旧文本摘要、预检结果（`git apply --check` 退出码、临时副本实应用）、Diff 路径与摘要。只展示摘要，不内联 Diff 正文。
2. **待确认或受阻**：提案状态、待确认问题、阻塞原因和缺少的材料。
3. **仅建议（advisories）**：迁移、源码修复、Hook、配置、加载路径和新增检查等不进入 Diff 的建议，回指 Finding。
4. **拒绝的修改请求（rejections）**：路径越界、符号链接、非规则文件等拒绝原因和原目标路径。

无 `preview_ready` 时写出 `no_preview_reason`，明确“没有可应用补丁，未生成 Diff”。`repair_checks` 中的失败或未执行项在本节展示，不算作第 4 节的请求覆盖缺口。

### 未覆盖检查与缺口

列出所有 `not_run`、`failed` 的覆盖项，以及证据引用失败、基线无法核对、语义会话缺失等报告层缺口。每项写检查 ID、状态、原因和产物引用。所有请求检查均为 `passed` 且无缺口时，才允许写“所有请求的检查均已完成”。

### 产物清单与复核

表格列出除 `run.json` 和 `report.md` 自身之外的 F09 产物、字节数、sha256 和状态；`report.md` 的摘要写入 `run.json` 最终清单，`run.json` 不自列摘要。说明 `evidence/` 由证据模块维护。注明本目录自包含：原项目或模型配置变化后，第 2–4 节的链接仍指向本次快照；机器可读结果见 `findings.json`、`checks.json`、`proposals.json`。

## 完整性与降级

整体状态由 `RunResult.status` 决定，报告层只做更保守的修正，不向更乐观方向调整：

| 条件 | 处理 |
| --- | --- |
| 任一 `support_refs` 或 `counter_refs` 的 ID 不存在、摘要不符、行段越界、路径不安全 | 该链接变为纯文本；记 `evidence_ref_unresolved`（含 `finding_id` 和 `evidence_id`）；`complete` 降为 `partial`，`integrity.evidence_links = failed` |
| `preview_ready` 提案的基准文件无法在证据目录中核对摘要 | 记 `base_file_unverifiable`；`complete` 降为 `partial`；第 3 节标注基准未复核 |
| `mode.semantic = local` 但 F06 未提供任何执行记录，或 `trace.jsonl` 缺少 `run_start` | 记 `semantic_trace_missing`；占位记录不算会话已建立；`complete` 降为 `partial` |
| `mode.semantic = off` 但覆盖或 trace 声称语义检查已执行 | `ReportInputError`，`semantic_mode_conflict`，退出码 2 |
| `coverage` 为空且 `status = complete` | 记 `coverage_missing`；降为 `partial` |
| `status = complete` 但有失败或请求后未执行的覆盖项 | 记 `coverage_incomplete` 并降为 `partial`；唯一豁免是 off 模式的 `f06.semantic: not_run/disabled_by_user` |
| `status = partial` 但覆盖全部 `passed` 且无错误 | 不升级；保留 `partial`，记 `status_unexplained` 警告 |
| `status = failed` | 保持 `failed`，仍写出报告与 JSON，退出码 1 |

降级后 `requested_status` 保留原值，`status_reason` 取第一个原因码，所有原因进入 `errors`。报告概览显示“原始状态 → 最终状态”和原因。`unknown` 发现、材料缺口、待确认决策本身不改变运行状态，只有请求的检查未完成才改变。F08 的 Git 预检失败或提案被拒只影响该提案，不把已完成的审计降为 `partial`。

基准核对优先使用 `base_files[].evidence_id` 查找目录；没有该字段时按 `relative_path` 与 `sha256` 匹配证据逻辑名称。两者都无法对应，才记 `base_file_unverifiable`。

## 合成标记

`run.json.synthetic`、`synthetic_basis` 和报告顶部标记由程序按以下顺序计算：

| 条件 | `synthetic_basis` | 报告显示 |
| --- | --- | --- |
| `RunResult.synthetic = false`，且存在证据或输入 | `user_supplied` | `否（含用户提供材料）` |
| 任一证据 `provenance_kind` 为 `user_supplied` / `adapter`，或任一输入 `synthetic = false` | `user_supplied`；若 `RunResult.synthetic = true` 另记 `synthetic_flag_conflict` 警告 | `否（合成标记与证据不一致）` |
| `RunResult.synthetic = true`，证据非空且 `provenance_kind` 全部为 `synthetic` 或 `project_file`，输入未标非合成 | `synthetic` | `是` |
| `RunResult.synthetic = true`，但证据为空、`provenance_kind` 缺失或未知 | `unknown` | `未确认` |
| `RunResult.synthetic = false`，且证据和输入都为空 | `unknown` | `未确认（无材料）` |

`run.json.synthetic` 只在 `synthetic_basis = synthetic` 时为 true。`project_file` 表示由项目路径捕获；只有在 `RunResult.synthetic = true` 时，它才随整体标记视为合成材料。合成标记不影响结论等级，只说明材料来源；不得把未确认材料写成合成。

## 渲染安全

模型输出、规则原文、路径、错误消息都按不可信文本处理：

1. 换行统一为 LF；替换 NUL、除 `\n`、`\t` 外的 C0/C1 控制字符和 Unicode 双向控制字符为 `U+FFFD`。
2. 行内文本先转义 `&`、`<`、`>` 为 HTML 实体，再反斜杠转义 Markdown 结构字符 `` ` ``、`*`、`_`、`[`、`]`、`(`、`)`、`#`、`|`、`!`。换行折叠为空格。
3. 多行文本用动态长度的围栏代码块显示，围栏反引号数比内容中最长连续反引号多一，最少三个，语言标记为 `text`。
4. 用户文本不进入标题、链接目标、表格结构或 HTML 属性；表格只放程序生成的计数、ID、状态和路径摘要。
5. 每个展示字段最多 4 KiB，超出时截断并注明“完整内容见 findings.json / proposals.json”；JSON 不截断。
6. 证据链接只由目录返回值和校验过的行号构造，目标先做百分号编码；禁止 `javascript:`、`data:` 和绝对路径目标。
7. 项目内路径显示为项目相对路径，分隔符为 `/`；项目外或含用户主目录的路径只显示文件名，并在 JSON 中同样不出现绝对路径。
8. 模型名、温度、超时以外的模型配置不写；错误消息若含运行根目录前缀，替换为项目别名。

## 写入、原子性与并发

1. `output_root` 必须已存在或可创建，解析后不能是文件；`output_root/<run_id>` 不能是符号链接，解析后必须仍位于 `output_root` 内。
2. 任何 F09 目标文件已存在时判定为重复运行，抛 `ArtifactCollisionError`，退出码 1，不改动任何既有字节；不提供覆盖或续写开关。
3. 单个文件先写同目录临时文件 `.<name>.tmp-<pid>-<随机>`，`fsync` 后用 `os.replace` 替换，权限 0644，目录 0755；异常时清理临时文件。所有文件写入经过同一个内部钩子，测试可在指定文件处注入失败，不能只 mock 上层函数。
4. 首次创建 `run.json` 使用 `O_CREAT|O_EXCL` 作为本次写入的占位，内容为 `artifact_manifest_status = "incomplete"` 和当前已完成清单；每完成一个文件后原子更新清单，最终再写一次 `final`。
5. 写入失败时抛 `ArtifactWriteError`，携带失败路径、已完成文件清单和错误码 `artifact_write_failed`；保留 `incomplete` 的 `run.json`，删除本文件临时残留，不删除已完成产物。F10 打印单行结构化错误并退出 1。
6. 不同 `run_id` 的并发写入各自使用独立目录和临时文件，不共享可变状态；同一 `run_id` 并发时由 `run.json` 的排他创建决定唯一胜者，失败者退出 1 且不影响胜者产物。
7. 报告在其他产物全部写完后生成，避免出现“报告完整、机器结果缺失”的目录；`run.json` 最终清单最后写入。
8. 重建同一输入的字节结果只依赖输入、固定 `clock` 和固定 run ID；文件遍历顺序不影响输出。

## 依赖接口

| 上游 | 本功能需要 | 不满足时 |
| --- | --- | --- |
| F01 | `RunResult` 及公共对象、枚举、序列化、`schema_version`、退出码常量、时间格式，以及 r2 的运行参数、输入摘要、合成标记和错误严重性 | 结构错误退出 2 |
| F02 | 固定布局 `evidence/snapshots/<snapshot_id>/content.bin`、原始字节摘要、`line_count`、`encoding`、`relative_path`、`Provenance` 对象（kind 投影为 `provenance_kind`）、`evidence/index.json`、`open_existing`；运行目录与 F09 相同 | 运行目录不一致退出 2；单个引用路径不安全、摘要不符或无法解析时降级 |
| F03 | 扫描截断、不可读文件的覆盖项与原因 | 截断或不可读必须带原因；整张覆盖表为空才按 `coverage_missing` 降级，不能输出“无问题” |
| F05、F06 | 覆盖项、`IncidentAnalysis`、`trace` 记录、语义结束原因；语义检查使用 `f06.semantic` 作为 `check_id` 并记录 `not_run` 原因；模型失败时仍保留记录 | 缺 trace 记为 `semantic_trace_missing` 并降级到 `partial` |
| F07 | 最终 `assessment`、`basis`、引用与 `checks`；不把假设写成支持 | F09 只复核引用和状态，不改等级 |
| F08 | `RepairPlan` 的 `proposals`、`diff`、`diff_proposal_id`、`advisories`、`rejections`、`checks` 及 `apply_check` 摘要 | `preview_ready` 缺预检按契约错误退出 2；仅建议、拒绝和预检失败照常展示，且不改变整体状态 |
| F10 | 唯一 `run_id`、项目外可写的 `output_root`、按依赖顺序调用、填写 `RunResult` 的输入与参数摘要、异常到退出码映射 | 目录重叠时拒绝；F09 不自行决定 run ID |

## 本地测试

```sh
.venv/bin/python -m pytest tests/test_f09_reporting.py tests/test_f08_repair.py -q
.venv/bin/python -m compileall -q src/context_manager
```

测试用真实 F02 捕获证据、真实 F07 生成结论、真实 F08 生成 Diff，不能直接手写 `report.md` 或预期的 `run.json`。固定候选通过 F07 的正式输入注入，恶意文本作为候选声明进入 Finding 后再验证转义。夹具优先复用 F10 的 `tests/fixtures/stale_users_api/`，边界场景用 `tmp_path` 构造临时项目和损坏输入。

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| F09-01 | `supported`、`hypothesis`、`unknown` 混合结果 | Markdown 与 JSON 的 ID、等级、计数一致；顶部合成标记与证据、`RunResult.synthetic` 一致；每个支持与反证引用都能在运行目录解析且摘要相符；`unknown` 保留缺口且不给出确定性处理 |
| F09-02 | 明确关闭模型；模型启动失败 | 前者即使覆盖含 `f06.semantic: not_run/disabled_by_user` 也为 `complete`、退出 0、语义行写“未请求”、无 `trace.jsonl`；后者 `partial`、退出 3、trace 含错误记录、报告写明失败且不声称语义完成 |
| F09-03 | 材料缺失、扫描截断、无规则文件 | 材料缺口和截断逐项可见，相关覆盖未通过；完整扫描无规则时对应项为 `passed/none_found`，报告明确没有可检查规则 |
| F09-04 | 有、无可用补丁 | 有时 Diff 文件字节摘要与 `proposals.json`、`run.json`、清单一致且预检退出 0；无时文件不存在、`artifacts.diff` 为 null、`no_preview_reason` 非空；清单集合等于实际文件集合（除 `run.json` 和 `evidence/`） |
| F09-05 | 控制字符、Markdown、HTML 和超长文本 | 报告标题顺序不变；不存在原始 `<script>`、NUL、ESC、双向控制字符或可执行的 `javascript:` 链接；围栏配对；JSON 保留原文；截断处指向 JSON |
| F09-06 | 写入失败、已有运行目录、两次并发写 | 注入失败后退出 1、`run.json` 为 `incomplete`、失败文件不在清单、无临时残留；已存在的 `run.json` 字节不变；不同 run ID 并发都成功且互不污染；同一 run ID 仅一个成功，另一个退出 1 |
| F09-07 | 输出目录下重开报告 | 删除或移走原项目后可解析每个链接，行段文本与当时快照一致（含 CRLF、中文、无末尾换行）；不访问运行目录外的文件 |
| F09-08 | `complete` 但引用失败、覆盖为空或语义 trace 缺失 | 降为 `partial`、退出 3，`requested_status` 保留，原因码同时出现在 `run.json.errors` 和报告；受影响的 `supported` 发现标注引用未通过复核 |
| F09-09 | 语义 trace、固定时钟重复构建 | trace 只含允许字段，不含提示词、回复正文、配置或凭据；超限有截断记录；`off` 时不生成；同一输入和时钟两次构建字节一致 |
| F09-10 | run ID 路径注入、符号链接输出、不安全证据路径 | run ID 的 `../x`、绝对路径、反斜杠、空值、超长值均退出 2，且不写出 `output_root` 之外；符号链接目录被拒绝或解析后仍受限；证据路径含 `../` 时不生成链接并降级 |
| F09-11 | 状态与退出码映射、JSON 格式 | `complete` 0、`partial` 3、`failed` 1 且三种状态都写出可读报告；JSON 为 UTF-8、LF、末尾换行；数组排序稳定 |
| F09-12 | F08 仅有建议、拒绝记录、Git 预检失败或 `blocked` 提案 | 第 3 节分别展示建议与拒绝；`proposals.json` 的 `advisories`、`rejections`、`repair_checks` 与报告一致；`checks.json` 不混入修复预检；审计检查通过时整体仍为 `complete`，无 Diff，原项目摘要不变 |

## 通过与交接

审查者打开实际生成的 `report.md`、`run.json`、`findings.json`、`checks.json`、`proposals.json` 和 Diff，逐项检查：

- 概览计数与各 JSON 一致，降级原因可见，退出码与状态匹配。
- 每个证据链接都能在本次目录打开，行段和摘要对应当次快照；原项目删除后仍可复核。
- `supported` 没有因为模型措辞被升级，`hypothesis` 和 `unknown` 的缺口、反证未被省略。
- 无可用补丁时没有生成空 Diff，有补丁时摘要与预检结果一致。
- 建议、拒绝和预检失败只出现在第 3 节和 `proposals.json`，没有改变请求覆盖或整体状态。
- 合成标记与证据来源一致；未确认材料没有被写成合成。
- 报告没有把未执行或失败的检查写成通过，没有泄露绝对路径、模型配置或凭据。
- 测试由正式模块生成输入，移除任一关键校验后测试会失败；不存在复制预期报告充当运行结果。
