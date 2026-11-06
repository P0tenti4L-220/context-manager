# F10 CLI 串联、本地集成与最终交付

依赖 F01–F09 全部审查通过。本项把各组件接入正常用户入口，并完成统一提交前验收。本规格采用[公共契约 r2](../contracts.md)和[跨功能接口矩阵](../interfaces.md)。早期交接中的提议保留为历史记录。

## 用户结果

用户在虚拟环境中安装项目后，可以执行完整体检和事件诊断，得到报告与可审阅的修复提案。全部验收在本机完成。用户能从退出码判断结果是否完整，从 `out/<run_id>/` 目录回查每项结论的证据，并能在原项目或本机模型随后变化后继续复核该次运行。

`audit` 检查当前规则，`diagnose` 在体检基础上关联选定事件。两者共用同一套参数校验、证据捕获、覆盖率、状态和产物布局。

## 职责边界

| 本功能负责 | 本功能不负责 |
| --- | --- |
| 唯一 CLI 入口、参数到 `AuditRequest` 的映射、输入预检 | 重新定义公共对象、枚举或字段语义 |
| 生成 `run_id`、创建运行目录、选择输出位置 | 重新实现证据捕获、规则比较、诊断或 Diff |
| 按依赖顺序调用 F02–F09，在各模块之间做形状适配 | 直接打开被审计项目文件绕过 F02 |
| 汇总覆盖项、计算请求状态、映射退出码 | 提升或降低 Finding 的 `assessment` / `basis` |
| 把 F04 的编辑目标与 F06 的模型意图拼成 F08 输入 | 自行生成语义改写、猜测行段或授权删除 |
| 打印结果位置、错误摘要和必要警告 | 重新渲染报告或改写 F09 的产物 |
| 本地打包、安装后入口检查、最终验收记录 | 自动应用 Diff、联网、安装 Hook |

F10 只做机械编排和形状转换。任何需要语义判断的内容留在 F06/F07，任何会改变规则文本的内容留在 F08。

## 文件与入口

建议新增或补齐：

| 文件 | 责任 |
| --- | --- |
| `src/context_manager/cli.py` | 保留 F01 的参数面；F10 替换 `not_implemented` 分支为正式管线，提供 `main(argv) -> int` |
| `src/context_manager/pipeline.py` | `run_audit`、`run_diagnose`、阶段顺序、覆盖汇总、状态计算 |
| `src/context_manager/adapters.py` | F03/F04/F05 候选转 F07 视图、F06 覆盖项、F09 证据目录、F08 registry 视图 |
| `src/context_manager/intents.py` | 从 F04 `edit_targets` 与 F06 `edit_intent` 机械拼装 F08 `EditIntent` |
| `tests/test_f10_cli.py` | 入口、管线、状态、错误、夹具、不可变性 |
| `tests/test_f10_packaging.py` | wheel 构建、安装、资源、入口一致性 |
| `tests/fixtures/stale_users_api/` | 完整合成项目、决策登记、事件包及变体 |
| `README.md` | 安装、两个命令示例、产物布局、退出码、模型模式与限制 |

入口约定：

```python
def main(argv: Sequence[str] | None = None) -> int: ...

def run_audit(request: AuditRequest, *, run_id: str | None = None) -> PipelineResult: ...
def run_diagnose(request: AuditRequest, case_input: IncidentInput, *,
                 run_id: str | None = None) -> PipelineResult: ...
```

- `main` 是唯一参数解析和退出码出口，不直接 `sys.exit`；控制台脚本和 `__main__.py` 包装其返回值。
- `run_id` 参数只供测试注入确定性 ID；CLI 不暴露该参数。
- `PipelineResult` 是 F10 内部对象，至少携带 `run_result`、`evidence_catalog`、`repair_plan`、`analyst_result`、`incident_analysis`、`run_id`；不得被写成新的公共契约。
- 导入 `context_manager` 不产生副作用；模型传输只在 `--semantic local` 时延迟导入。
- `python -m context_manager` 与安装后的 `context-manager` 使用同一 `main`，行为一致。

## 命令行契约

```text
context-manager audit --repo PATH --project-id ID --scope PREFIX
  --as-of ISO_TIME --decisions FILE --semantic off|local
  [--model-config FILE] --out PATH

context-manager diagnose --repo PATH --project-id ID --scope PREFIX
  --as-of ISO_TIME --decisions FILE --case FILE --semantic off|local
  [--model-config FILE] --out PATH
```

| 参数 | 必填 | 默认 | 解析与校验 | 去向 |
| --- | --- | --- | --- | --- |
| `--repo PATH` | 是 | — | 按调用时工作目录解析一次；必须是已存在目录；根目录符号链接解析后绑定 | `AuditRequest.repo` |
| `--project-id ID` | 是 | — | 非空本地别名；不做路径拼接；不得含控制字符 | `AuditRequest.project_id` |
| `--scope PREFIX` | 否 | `["."]` | 可重复，顺序保留并去重；相对路径；拒绝绝对路径与 `..`；不存在的目录由 F03 记 `E_SCOPE_MISSING` 并令覆盖失败 | `task_scope.include_prefixes` |
| `--as-of ISO_TIME` | 否 | 当前时间 | 必须带时区；解析后统一换算为 UTC；省略时采样一次并同时写入请求与产物 | `AuditRequest.as_of` |
| `--decisions FILE` | 否 | null | 指定时必须存在、可读、UTF-8 JSON；省略时登记为 null | `decision_registry` |
| `--case FILE` | diagnose 是 | — | audit 不定义该参数，出现即参数错误；单文件不超过 10 MiB | `IncidentInput` |
| `--semantic` | 否 | `off` | 只接受 `off`、`local` | `semantic_mode` |
| `--model-config FILE` | local 时是 | null | 仅 local 时读取；TOML、普通文件、不超过 64 KiB；只允许 `127.0.0.1` / `[::1]` 回环地址 | 传输配置 |
| `--out PATH` | 是 | — | 解析后不得与 `repo` 相等或互相包含；已存在时必须是目录；不得把运行目录写到符号链接目标之外 | 输出根目录 |

补充规则：

1. `--model-config` 在 `--semantic off` 时按 F01 的约定忽略，不读取该文件，只记录 `model_config_ignored` 警告；不因此失败。
2. `--semantic local` 缺 `--model-config`、文件不存在、TOML 或字段非法、非回环地址，都在创建运行目录前退出 2。
3. `--scope` 为空字符串、绝对路径、包含 `..` 或无法规范化的写法退出 2；前缀存在但无文件时由 F03 给出覆盖结果。
4. `--repo` 内部路径组件经过符号链接、`..` 越界或项目外导入由 F02/F03 拒绝并记覆盖，不在 F10 放宽。
5. `--out` 已存在且是普通文件、输出根与项目经符号链接互相包含，均退出 2；运行 ID 冲突按运行冲突处理；不得覆盖旧运行。
6. `--help`、`--version` 退出 0，stdout 输出，不读文件、不建目录、不联网。
7. 未知参数、缺必填、未知枚举由 argparse 或 F01 `build_request` 退出 2；stdout 为空，错误只走 stderr。
8. 其余参数不得新增；`--harness-profile`、`--exclude-scope` 等未列入首版，F03 使用其内置 profile，F04 的范围来自登记与任务范围。

## 输入预检与登记绑定

### 预检顺序

在创建输出目录和证据之前完成：

1. F01 解析参数并调用 `build_request`，得到已解析路径、`as_of` 和 `scope`。
2. `--decisions` 指定时，F02 预读选定输入，F04 `parse_registry` 校验结构；`RegistryFormatError` → 退出 2。此时只验证结构，不绑定引用。
3. `--case` 指定时，F02 按 10 MiB 上限预读，F05 `parse_incident_input` 校验包级结构及旧格式。事件证据的跨对象核验留在运行内。
4. `--semantic local` 时，F06 的配置加载器读取并校验模型配置；失败退出 2。配置内容不进入证据、不复制到产物、不打印。
5. 解析 `--out` 的真实路径：不得与项目相等或互相包含，已存在时必须是目录；不通过则退出 2。
6. 以上全部通过后才创建 `<out>/<run_id>/`。预检失败不得创建任何输出目录。

预检成功后，F02 捕获预读的同一份字节。F10 不重新打开用户提供的登记或事件包。

### 登记来源绑定

F04 `bind_registry` 把静态登记的 `SourceLocator` 绑定为本次运行的 `EvidenceRef`。定位必须提供项目相对路径、完整文件 SHA-256 和行段。F04 经 F02 捕获项目文件并核对定位，再按规则原文与行段摘要匹配；只改内存视图。路径、摘要或行段无法绑定时记 `registry_binding_failed` 和失败覆盖，隔离该条目及依赖其授权的结果。保留可用结果和报告，按 partial/3 退出。

### 请求映射

| `AuditRequest` 字段 | 来源 |
| --- | --- |
| `project_id` | `--project-id` |
| `repo` | 解析、复核后的项目根 |
| `task_scope` | `{include_prefixes: scopes, exclude_prefixes: []}` |
| `as_of` | 参数值或采样值，UTC |
| `semantic_mode` | `--semantic` |
| `decision_registry` | 预检时解析的本地输入路径；省略时为 null。路径不写入运行产物 |
| `harness_profile` | null；F03 有内置 profile 时使用并记录名称/版本，否则按 F03-02 以 `shared` / `unknown` 运行并记录缺口 |
| `out` | 解析后的输出根 |
| `mode` | 由 `cli.main` 传入；`{command, semantic}` 写入 `RunResult` |

## 运行目录与输出

每次运行创建 `<out>/<run_id>/`，其中 `run_id` 匹配 F09 的 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。建议格式：

```text
YYYYMMDDTHHMMSSZ-<8位小写十六进制>
```

- 时间取本次运行采样，随机后缀用于并发区分；同一个 `run_id` 已存在时重新采样，最多 5 次，仍冲突则退出 1。
- 先排他创建运行目录，再创建 F02 证据存储；F09 最后在同一目录写入产物，不另建目录。
- F10 不预创建 `run.json`、`report.md` 等 F09 目标文件，避免与 F09 的排他创建冲突。
- 证据目录由 F02 维护；F10 不复制、不改写证据字节。
- `out/` 根目录可在预检后按需创建；创建失败退出 1。

成功或部分完成时，stdout 只输出三行：

```text
run_id: <run_id>
status: <complete|partial|failed>
artifacts: <运行目录绝对路径>
```

- `status` 使用 F09 返回的 `RunArtifacts.effective_status`，不是请求状态。
- stderr 用于警告和错误；警告格式 `context-manager: <code>` 加缩进说明，错误格式沿用 F01 的 `context-manager: <code>`，不打印证据正文、输入全文、模型配置或凭据。
- 参数或契约错误退出 2 时 stdout 为空；运行失败退出 1 时仍尽量打印已生成的运行目录（若存在）。
- 不输出颜色控制符；不输出 traceback，异常类型和受限消息写入 stderr 与 `run.json.errors`（若 F09 可写）。
- `--help`、`--version` 保持 F01 的输出格式。

## 管线与依赖接口

阶段顺序固定；`audit` 跳过事件阶段，`diagnose` 必选事件包。

| 阶段 | 调用 | 必要产物 | 失败处理 |
| --- | --- | --- | --- |
| 0 预检 | F01 `build_request`、F02 预读、F04/F05 解析入口、F06 配置加载器 | 已校验请求、原始登记、模型配置 | 退出 2，零写入 |
| 1 运行建立 | F10 生成 `run_id`、目录；F02 `EvidenceStore(project_root, output_root, run_id)` | 运行目录、证据存储 | 退出 1 |
| 2 选定输入 | F02 `capture_selected_input` 捕获已预读的登记；事件包由 F05 在事件阶段捕获 | 输入证据、`input_id`、逻辑名 | 捕获预读字节，写入失败按运行错误处理 |
| 3 发现 | F03 `inspect_project(request, store, options)` | `InspectionResult`、规则文件证据、引用与命令检查 | 截断/不可读记覆盖，继续 |
| 4 规则与决策 | F04 `extract_rule_spans`、绑定登记、`resolve_policy`、`compare_constraints` | `RuleExtraction`、`ComparisonResult`、候选与覆盖 | 绑定和比较缺口记覆盖 |
| 5 事件 | F05 `prepare_incidents`（仅 diagnose） | `IncidentAnalysis`、回执/加载/行为观察 | 包级结构错误已在预检处理；证据导入或分析缺口记覆盖 |
| 6 语义 | F06 `analyze_context`（仅 local） | `AnalystResult`、候选、trace、覆盖 | 不可用/未完成记覆盖，静态结果保留 |
| 7 适配 | F10 `adapters.py` | F07 的 `StaticCandidate` / `ModelCandidate` / 视图 | 形状错误退出 1 |
| 8 诊断 | F07 `evaluate_findings(...)` | `DiagnosisResult`、Findings、覆盖 | 输入错误退出 1；单候选拒绝不中断 |
| 9 编辑意图 | F10 `intents.py` | `EditIntent[]` | 无依据的意图不生成，留待确认 |
| 10 修复 | F08 `plan_repairs(findings, edit_intents, registry, store)` | `RepairPlan`、唯一 Diff、预检 | 提案 `blocked`/`needs_confirmation` 不降级审计 |
| 11 汇总 | F10 构造 `RunResult` | 模式、状态、覆盖、发现、提案、参数、输入 | 形状错误退出 1 |
| 12 产物 | F09 `write_run_artifacts(...)` | `RunArtifacts` | 输入错误 2、写入/冲突 1 |
| 13 退出 | F10 打印并返回 | stdout 三行、stderr 警告 | 用 `RunArtifacts.exit_code` |

约束：

1. 所有阶段使用同一个 `run_id`、`as_of` 和证据存储；禁止跨运行引用。
2. 阶段失败不能吞掉：每个阶段必须产生覆盖项或结构化错误，不能以空结果表示“没发现问题”。
3. 预期材料缺失（截断、缺回执、草稿决策、模型不可用）不抛异常，转为覆盖和缺口；输入结构错误才抛。
4. F10 不重新读取项目文件；所有原文和字节来自 F02 快照。登记和事件预检也通过 F02 预读，后续使用同一份字节。
5. F10 不改变上游 Finding 的等级、引用或缺口；只合并和排序。
6. 模型阶段在静态阶段之后运行，保证语义失败时静态结果仍可写报告。
7. `--semantic off` 时不构造传输、不导入模型传输模块、不发起连接；只写一条 `f06.semantic` 的 `not_run` 覆盖项，原因 `disabled_by_user`。

### 候选与视图适配

| 来源 | 适配为 | 规则 |
| --- | --- | --- |
| F03 `InspectionResult` 的失效引用 | `StaticCandidate(kind=dead_reference)` | `facts` 带引用类型、目标、目标状态、扫描完整性；扫描不完整不得报确定缺失 |
| F04 `ComparisonResult.candidates` | 原样作为 `StaticCandidate` | 保留 `candidate_id`、`origin=rules`、`facts`、`details`、`edit_targets` 映射 |
| F05 `recurrence` 达到阈值 | `StaticCandidate(kind=recurrence)` | `facts` 带 `constraint_id`、`group_key`、`event_ids`、`independent_task_ids` |
| F05 完整排除的 `observed_not_loaded` | `StaticCandidate(kind=missing_load)` | 仅当加载证明完整；部分覆盖不生成 |
| F05 `observed_loaded` 加已确认违规 | `StaticCandidate(kind=loaded_violation)` | 必须带 `event_id`、`rule_id`、`check_id` |
| F06 `AnalystResult.candidates` | `ModelCandidate` | 保留 `candidate_id`、引用、缺口、`edit_intent`；来源固定 model |
| F04 `Registry` + F03 登记文件 | F07 `RegistryView` / F08 `registry` | 补 `editable_files`；不改变决策有效性 |

F10 只按上表从观察事实机械构造；不把 `added_literal`、diff 文本、模型自述或“疑似”升级为违规。构造规则必须有对应事实，否则不生成候选。

### 编辑意图拼装

F10 只拼装已有依据的 `EditIntent`，不写新规则文本：

1. 只处理 F07 结果中 `assessment=supported`、`suggested_action` 属于 `update` / `delete`、且来源为 F04 `stale_rule` 的 Finding。
2. 通过 F07 `CandidateValidation.candidate_id` 与 `finding_ids` 回到对应的 F04 `edit_targets[candidate_id]`；找不到绑定则不生成意图，F08 以 `needs_confirmation` 展示。
3. 要求 `edit_ready=true`、`source_ref` 属于本次运行、`line_start/line_end` 与 `old_text_sha256` 和 F02 快照一致；否则不生成。
4. `update` 的整行替换文本：取 F02 的该行文本（不含行终止符），用 `edit_target.clause_span` 定位子串并替换为 `replacement_value`，其余字符原样保留；替换后不得含 NUL。行终止符、BOM 和文件编码由 F08 保持。
5. `delete` 仅在 F04 明确给出 `delete`、`edit_ready=true`、`preserved_constraint_ids` 为空且解释完整时生成；否则留待确认。
6. `narrow_scope` 没有结构化替换文本时不生成意图；F08 输出 `needs_confirmation`，由用户提供确认后的完整改法。F10 不写“建议措辞”。
7. `preserved_rule_ids` 由 F04 的 `preserved_constraint_ids` 映射；映射不到独立义务时不省略，转为待确认，避免静默删除。
8. `decision_ids`、`basis_refs`、`confirmation_id` 来自 F04 候选、决策和确认。F08 核验是否批准实际替换字节，缺失或不匹配时保留待确认。
9. F06 模型 `edit_intent` 只保留字段完整、引用有效、`origin=model` 的意图；缺少基准证据或摘要的意图只保留为拒绝记录。结构合格但缺确认的意图交给 F08 按待确认处理。
10. `edit_intent_id` 由 `finding_id + target_path + line_start + operation` 稳定派生；同一目标重复意图去重，冲突意图都保留给 F08 判定。
11. 所有基准字节取自本次 F02 快照，禁止读取当前工作树作为基准。

## 覆盖、状态与退出码

### 覆盖汇总

F10 收集并合并：

- F02 `coverage_items`（`evidence.capture:<purpose>`）；
- F03 的 `f03.*`；
- F04 的 `f04.*`；
- F07 的 `diagnosis.*`；
- F05 的 `IncidentAnalysis.coverage_items` 原样传递；
- F06 的 `f06.semantic`：`status=complete` → `passed`；`partial` → `failed` 并带结束原因；`off` → `not_run`，原因 `disabled_by_user`；local 下的防御性 `not_run` 按 `failed` 处理。

合并规则：

1. 同一 `check_id` 的重复项按保守状态合并：任一 `failed` → `failed`；否则任一 `not_run` → `not_run`；否则 `passed`。合并 `inputs`、`artifact_refs`；`reason` 去重后按稳定顺序拼接。
2. 最终 `check_id` 必须唯一；按 `check_id` 字节序排序。
3. `not_run` 只表示“请求了但未执行”；没有东西可查（如无规则文件、无候选）必须是 `passed` 加原因 `none_found` / `no_candidates`，不能写成 `not_run`。F03 与 F07 均按此处理。
4. `failed` 和 `not_run` 必须有非空原因；`passed` 可以带 `none_found` 等原因。
5. F08 的修复预检不进入顶层覆盖，也不影响整体状态；由 `proposals.json.repair_checks` 展示。

### 请求状态

在调用 F09 前计算 `RunResult.status`：

```text
if 致命错误（运行目录、证据存储、无法继续）: failed
elif 任一覆盖项 status=failed: partial
elif 覆盖为空或存在请求后未执行的 not_run: partial
else: complete
```

- `f06.semantic` 的 `disabled_by_user` 是明确不请求语义检查，是 F09/F10 唯一共享的 not_run 豁免。
- `unknown` 的 Finding、待确认决策、缺回执、草稿决策本身不自动改变状态；只有对应请求检查未完成才改变。
- F10 用关键字 `trace=` 和 `incident_analysis=` 传递相应视图。F09 只能向更保守方向降级（如证据引用无法解析、覆盖为空、trace 缺失），不能把 `partial` 升为 `complete`。
- 最终退出码使用 `RunArtifacts.exit_code`，不自行重算。

| 情况 | 请求状态 | 退出码 |
| --- | --- | --- |
| 所有请求检查 `passed`，语义明确关闭 | `complete` | 0 |
| 语义关闭但 `f06.semantic` 为 `not_run:disabled_by_user` | `complete` | 0 |
| 省略 `--decisions`，`f04.registry` 为 `not_run` | `partial` | 3 |
| 决策为 draft/未来/withdrawn，检查已执行且无 stale 结论 | `complete` | 0 |
| 缺回执导致加载 `unknown`，`f05.loads` 未完成 | `partial` | 3 |
| 请求本地模型但连接失败或预算结束 | `partial` | 3 |
| 扫描截断、规则文件不可读、证据捕获失败 | `partial` | 3 |
| 提供有效空登记，且无规则文件、无候选，检查正常完成 | `complete` | 0 |
| F08 Git 预检失败或提案 `blocked` | 不改变审计状态 | 0/3 按覆盖 |
| 输出写入失败或运行目录冲突 | `failed` | 1 |
| 参数、路径、时间、JSON 结构、模型配置错误 | 未产生结果 | 2 |
| 未预期内部异常，无法得到可用结果 | `failed` | 1 |

参数错误必须在创建运行目录前发生；退出 2 时不得留下 `report.md` 或“成功”摘要。运行中失败时，尽量让 F09 写出 `failed` 报告；写不出就只打印结构化错误，并保留不完整目录。

### 合成标记

`RunResult.synthetic` 只在同时满足以下条件时为 `true`：

1. 每个外部输入都显式声明合成：事件包 `synthetic: true`，决策登记若有对应的可选标记也须为 true。
2. 本次证据的 `provenance.kind` 全部为 `synthetic` 或 `project_file`，且证据非空。
3. 没有任何输入或证据表明用户提供材料。

否则为 `false`。CLI 不提供 `--synthetic`，也不根据 `tests/fixtures/` 路径推断；静态决策登记省略合成标记时，CLI 审计记为 `user_supplied`。事件包内的事件、回执和 Finding 各自的 `synthetic` 标记仍保留，不能被整体标记覆盖。合成夹具显式设置 F04 登记的 `synthetic: true`。

## 边界条件

1. **项目根符号链接**：解析后绑定真实根；根以下的符号链接、`..`、绝对路径跳转按 F02/F03 拒绝并记缺口。
2. **`--out` 边界**：`out` 与项目根相等或互相包含（含经符号链接解析）、或已存在为普通文件，均退出 2；`out` 的父目录可创建。
3. **运行目录**：同一 `run_id` 不允许覆盖；并发运行必须得到不同目录；F09 目标文件已存在按冲突退出 1。
4. **作用范围**：`clients/` 与 `clients-old/` 按路径组件区分；重复 `--scope` 去重但保持首次顺序；不存在的前缀由 F03 记失败，整体 partial。
5. **时间**：`as_of` 必须带时区；内部按 UTC 比较；决策区间 `[effective_from, effective_until)`；历史事件使用各自 `occurred_at`。
6. **决策省略**：没有登记时不生成确定性 stale/conflict，`f04.registry` 为 `not_run`，整体 partial；不得用 ADR 文本或模型自述补出确认。
7. **草稿/未来/撤回决策**：正常评估为未生效，不产生 stale 结论，也不因此 partial；报告可说明没有生效决策。
8. **缺回执**：保留事件、复发和静态结论；加载状态 `unknown`，对应覆盖 `not_run`，整体 partial；不得生成 `missing_load` 或 `loaded_violation`。
9. **旧格式事件包**：F05 识别为 `legacy-demo-v0`，只保留线索，load/behavior 为 unknown；整体 partial，报告明确旧格式，不伪造回执。
10. **语义关闭**：不读取 `--model-config`（若提供则警告）、不建连接、不产生 trace、不导入传输模块；`f06.semantic` 记 `not_run:disabled_by_user`。
11. **语义不可用**：配置合法但连接失败、超时或预算结束，静态发现、覆盖和提案照常保留；整体 partial，trace 保留失败记录，报告不声称语义完成。
12. **模型配置**：只允许回环 HTTP、无认证、无代理、无重定向；配置内容不进入证据、报告、trace、`run.json` 或 stderr；只记录公共契约规定的模型和提示词摘要。
13. **目标项目只读**：`audit`、`diagnose`、F08 预检、语义读取前后，项目文件内容和文件集合不变；不得在项目内创建 `.context-manager/`、`out/`、`.git/` 或缓存。
14. **空项目**：登记有效、扫描完整且没有规则文件时检查完成、候选为空，报告明确“没有可检查的规则”，不得写“没有发现问题”。
15. **CRLF、BOM、无末尾换行、中文路径**：F02/F08 保持字节；F10 不重写证据或 Diff；测试夹具覆盖这些形式。
16. **输出隐私**：产物不含绝对项目路径、输出根外路径、模型地址或凭据；`report.md` 显示项目相对路径。stdout 的运行目录是本机终端提示，不写入产物。
17. **错误输出**：不打印输入正文、证据片段、完整异常栈；错误消息只含路径、字段、稳定错误码和受限说明。
18. **中断**：首版不定义 `SIGINT` 的退出码；已写入目录保留，不标记为成功，不能复用为新的 `run_id`。

## 夹具

`tests/fixtures/stale_users_api/` 与现有 `examples/stale-users-api/` 语义一致，但作为正式 CLI 夹具，必须包含完整回执和可绑定的登记定位。测试先把夹具复制到 `tmp_path`，不得修改源夹具。

```text
tests/fixtures/stale_users_api/
├── README.md
├── project/
│   ├── AGENTS.md                 # clients/ 要求 v1；legacy/ 例外；测试义务
│   ├── CLAUDE.md                 # clients/ 要求 v2；同范围说明
│   ├── clients/app.py            # USERS_PATH = "/api/v2/users"
│   ├── clients-old/legacy.py     # 名称相似目录，scope 边界
│   ├── legacy/legacy_client.py   # 合法 v1 兼容
│   ├── docs/adr/0001-users-api.md
│   ├── docs/adr/0002-users-api.md
│   └── tests/test_users.py
├── decisions.json                # 逻辑定位 source_ref；accepted v2 + confirmations + BehaviorCheck
├── incidents.json                # 两个独立任务、同任务重试、完整回执、合成历史文件
└── variants/
    ├── decisions_draft.json      # v2 为 draft
    ├── decisions_future.json     # v2 未来生效
    ├── decisions_invalid.json    # 缺字段/未知枚举
    ├── incidents_missing_receipt.json
    ├── incidents_compatible.json # legacy 任务使用 v1
    ├── incidents_invalid.json    # 缺字段/未知格式
    └── project_compatible/       # clients-old/ 与 legacy/ 的合法变体
```

夹具要求：

1. `decisions.json` 顶层为 `schema_version: 1`、`synthetic: true`、`project_id`、`rules`、`decisions`、`confirmations`、`behavior_checks`；`source_ref` 使用逻辑定位并带全文件摘要。预览正例增加精确 `edit_approvals`，负例覆盖缺失、过期和不匹配。
2. `incidents.json` 使用 F05 的 `context-manager-incidents` 包格式，`synthetic: true`，包含包内历史文件、完整回执、`before_event_id`、`context_epoch`、`channels_covered` 和 `provenance`。
3. 两个独立任务都实际修改了 `USERS_PATH` 为 v1；同任务重试事件不能增加独立任务数；`legacy/` 任务为合法例外。
4. 完整回执覆盖 F05 要求的全部渠道，加载旧版 `AGENTS.md` 的条目摘要与包内快照一致；缺失回执变体从 v1 的 receipts 数组移除对应回执；仅旧示意格式保留只有 ID 的线索。
5. 大文件、非法 UTF-8、符号链接、只读目录和超限事件包在 `tmp_path` 现场生成，不提交大二进制。
6. `README.md` 写明每个夹具的预期状态和用途，不能把预期报告当作测试断言来源。

| 变体 | 命令要点 | 预期 |
| --- | --- | --- |
| 完整 audit | scope `clients/`、accepted v2 | complete/0；`stale_rule` supported；兼容例外和测试义务保留；一个 `preview_ready` Diff |
| 完整 diagnose | 完整事件包 | complete/0；两个独立任务复发；每次加载状态有完整回执；off 模式无因果解释 |
| 缺回执 | event 2 无可用回执 | partial/3；静态结论保留；加载 unknown；不生成 missing_load/loaded_violation |
| 草稿决策 | `decisions_draft.json` | complete/0；无 stale 结论、无 preview Diff；报告说明未生效 |
| 兼容变体 | `project_compatible/`、legacy 事件 | complete/0；legacy 不计违规、不进入 Diff |
| 旧样例 | `examples/stale-users-api/case.json` | partial/3；legacy 提示、加载 unknown、不升级为 supported |

## 本地测试

```sh
.venv/bin/python -m pytest tests/test_f10_cli.py tests/test_f10_packaging.py -q
.venv/bin/python -m pytest tests -m 'not local_model' -q
.venv/bin/python -m pytest tests/test_f06_local_model.py -m local_model --local-model-config local/model.toml -q
.venv/bin/python -m compileall -q src/context_manager
git diff --check
```

本机使用 RTK 时按验收规范加 `rtk proxy`。F10 的测试必须通过正式 CLI 或正式管线调用真实实现；不得 monkeypatch 诊断门槛、复制预期报告或用固定响应替代 F06 的真实语义结果。F09、F08、F07 的专用用例仍需各自通过。

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| F10-01 | 静态 audit 正常入口 | 退出 0、`run.json.status=complete`；找到已确认的 `stale_rule`（`assessment=supported`，`basis` 为确定性或已确认解释）；保留 `legacy/` 例外和测试义务；恰好一个 `preview_ready`，Diff 只改 AGENTS.md 目标行且新值为 v2；兼容行、测试行逐字节不变；报告链接可打开；语义覆盖为 `not_run:disabled_by_user`；缺失、过期或摘要不匹配的改法批准只产出 `needs_confirmation` 且无 Diff |
| F10-02 | 完整 diagnose | 退出 0、complete；复发为 supported，`related_event_ids` 覆盖两个独立任务，同任务重试未增加计数（进程内核对 F05 `IncidentAnalysis`；同时核对 `incidents.json`）；每个事件有加载观察和合成回执来源；默认 off 不生成因果 Finding；F07 专项用例核对因果 hypothesis；提案只指向目标规则 |
| F10-03 | 缺回执、草稿决策和合法兼容变体 | 缺回执：partial/3，加载 unknown，静态结论保留，不生成需要完整排除的结论；草稿：complete/0，无 stale、无 preview Diff，报告说明未生效；兼容：legacy 不计违规、不进入 Diff；三种情况目标文件摘要不变 |
| F10-04 | 默认关闭模型 | 不提供 `--semantic` 时等价 `off`；不建立 socket 连接（进程内监视）；不生成 `trace.jsonl`；`f06.semantic` 为 `not_run:disabled_by_user`；基础审计 complete/0 |
| F10-05 | 请求本地模型但不可用 | 合法回环配置指向未监听端口；退出 3、partial；静态 findings/proposals 与 F10-01 等价；`trace.jsonl` 有失败记录；`f06.semantic` 未通过；stderr 有受限警告；不声称语义完成 |
| F10-06 | 实际本地模型完整流程 | 使用 `--model-config local/model.toml`，经正式 CLI 运行；至少一次退出 0、complete；模型候选进入 F07 后被独立核验；所有模型来源 Finding 的引用可在本次快照解析，等级不高于 hypothesis/model_candidate；报告、`findings.json`、`trace.jsonl` 一致；记录模型名、温度、超时和提示词 SHA-256；配置缺失时记 `not_run`，不能通过 |
| F10-07 | 输出路径非法、参数错误和写失败 | 缺 `--case`、坏 `--as-of`、`--out` 在项目内（含 `..`/符号链接）、local 缺配置、非法 decisions/case、超 10 MiB 事件包、`--repo` 不存在均退出 2；预检错误前不创建输出目录；哨兵字符串不出现在 stdout/stderr；只读输出根写入失败退出 1、无报告；所有情况目标项目不变 |
| F10-08 | 从 wheel 安装后的入口 | 在临时源码副本上离线构建 wheel；wheel 含 `context_manager/prompts/analyst.md` 和控制台入口，不含 `tests/`、`local/`、`examples/`、缓存；新建临时 venv 后从本地 wheel 安装；`context-manager --help/--version` 与 `python -m context_manager` 可用；同一夹具的 audit 结果与源目录运行在规范化后一致；模型配置和密钥不进入 wheel |
| F10-09 | 任一审计与预览运行前后 | 对目标项目全部普通文件做前后摘要和路径集合比较，完全一致；没有新建 `.context-manager/`、`out/`、`.git/`、缓存或临时补丁；F08 的临时副本不残留 |
| F10-10 | 入口、路径与参数一致性 | 控制台入口、`python -m` 和源码运行使用同一 `main`；从不同工作目录、用相对/绝对 `--repo` 得到同一结果；默认 scope 为根，重复 scope 顺序稳定；显式 `as_of` 原样换算并以 UTC 记录；默认 `as_of` 带时区且落在运行前后之间；`--help`/`--version` 零写入 |
| F10-11 | 状态、覆盖与退出码矩阵 | complete→0、partial→3、failed→1、参数/契约→2；`report.md`、`run.json`、`checks.json` 与进程退出码一致；F09 的降级原因写回 `requested_status`/`status_reason`；`--semantic off` 不被覆盖项误降级；`not_run` 都有原因 |
| F10-12 | 确定性与并发 | 相同输入、相同 `as_of` 两次运行，除 `run_id`、时间和路径外，findings、proposals、coverage、Diff 摘要一致；两个并发进程写同一 `--out` 根目录都成功且目录不同；没有覆盖或混合产物；同一 `run_id` 冲突时只有一个成功 |
| F10-13 | 诚实的缺口与旧格式 | 提供有效空登记且无规则文件：complete 但明确“没有可检查规则”，不写“无问题”；省略 `--decisions`：partial/3，`f04.registry` 未完成；旧样例：partial/3、legacy 提示、加载 unknown、不伪造 supported；扫描截断/不可读：partial 且原因可见；以上均不生成空 Diff |

测试组织建议：

- `tests/test_f10_cli.py` 使用 `subprocess` 运行 `sys.executable -m context_manager`，同时提供进程内 `main(argv)` 用例用于 socket/写入监视。
- 每个用例复制夹具到 `tmp_path`；`--out` 指向 `tmp_path/out`，项目源码只读。
- `local_model` 标记只用于 F10-06；默认 `-m 'not local_model'` 不触发模型调用。
- 断言以 `run.json`、`findings.json`、`checks.json`、`proposals.json`、`proposal.diff` 和实际文件摘要为准，不以日志文字为准。
- F10-08 需要本地构建后端；不可用时记 `not_run`，不能标为通过。

## 打包与安装检查

1. 将 `pyproject.toml`、`src/`、必需的 README 复制到临时目录构建，避免在真实工作树产生 `build/`、`dist/`、`*.egg-info`；记录源文件摘要。
2. 使用已安装的本地构建工具离线构建：
   - 优先 `python -m build --no-isolation --wheel --outdir <tmp>`；
   - 不可用时 `python -m pip wheel --no-deps --no-build-isolation -w <tmp> .`；
   - 构建后端缺失或不支持离线构建时记 `not_run`，不得联网安装。
3. 检查 wheel 包含 `context_manager/`、四份分析提示词、`METADATA` 与 `entry_points.txt`。包内容限于运行所需源码、资源和元数据。
4. 新建临时 venv，使用 `pip install --no-index --no-deps <wheel>` 安装；验证：
   - `context-manager --version` 等于包版本并带 `schema_version 1`；
   - `context-manager --help` 列出 audit、diagnose；
   - `python -m context_manager` 等价；
   - `importlib.resources.files("context_manager").joinpath("prompts/analyst.md")` 可读，SHA-256 与源码一致。
5. 从工作目录之外运行完整 audit，与源码树运行结果做规范化比较：比较 findings 的 kind/claim/scope/assessment/basis、support/counter 逻辑路径与行段、proposals 状态、`diff_sha256`、coverage 的 check_id/status/reason；忽略 run_id、时间、绝对路径和包版本。
6. 记录 wheel 文件名、SHA-256、字节数、构建命令、退出码、venv 的 Python 版本和安装日志。

## 最终审查与提交

前置条件：F01–F09 全部 `accepted`，没有 `changes_requested` 或未关闭的跨功能接口问题；F06 的 `local_model_tests` 不是 `not_run`；本规格中的 F10-01–F10-13 全部有实际执行记录。

执行并记录：

```sh
.venv/bin/python -m compileall -q src/context_manager
.venv/bin/python -m pytest tests -m 'not local_model' -q
.venv/bin/python -m pytest tests/test_f06_local_model.py -m local_model --local-model-config local/model.toml -q
git diff --check
```

1. 生成实现、测试、配置、提示词、夹具和 `pyproject.toml` 的 SHA-256 清单，保存在 `local/verification/f10/<run>/tested-files.json`；清单自身也要记录 SHA-256。
2. 准备提交后，用暂存区文件与清单逐项比对；任何实现变化都让相关结果过期并重跑。
3. 公开内容检查：不提交 `local/`、`.venv/`、真实日志、模型配置或权重、绝对用户路径、密钥、缓存和构建产物；夹具均为合成数据并带标记；README 命令可直接复制运行。
4. 更新 README 的安装、audit/diagnose、产物、退出码和模型模式说明。用真实 CLI 输出复核 `examples/stale-users-api/expected-report.md`：旧格式样例不得继续写成 supported 的复发/加载结论；由 F10 重写为实际结果，并保留“旧示意格式”的说明。
5. 核对远端当前状态：远端为空时完成一次初始提交并发布 `main`，记录 `not_applicable_initial_publication`；已有目标分支时先对照基线，再统一合并。记录最终提交 SHA、远端 URL、推送/合并结果。
6. 合并冲突或提交前任何代码变化，都要重跑受影响测试并更新清单；不得用旧日志代替。
7. 在执行计划中登记 F10 的测试命令、退出码、耗时、日志、源摘要和最终状态；未运行项、跳过项和失败项必须显式保留，不能计入全部通过。

交付前逐项核对用户目标：

| 目标 | F10 验收证据 |
| --- | --- |
| 找出过时规则 | F10-01、F10-03、F10-13 |
| 追查重复错误 | F10-02 |
| 正确修复方向 | F10-01、F10-02、F10-03，F08 用例 |
| 结果可复核 | F10-01、F10-05、F10-09、F10-11 |
| 本地可安装 | F10-08 |
| 不伪造证据或成功 | F10-03、F10-05、F10-11、F10-13 |

## 通过与交接

F10 通过的条件：

1. 上述 F10-01–F10-13 全部实际执行并通过；F10-06 使用真实本机模型，`not_run` 不能计入通过。
2. 全部基础测试（`not local_model`）、语法检查、打包与安装检查通过；跳过项单独列出。
3. 目标项目在全部运行前后摘要一致；没有任何自动应用或项目内写入。
4. 报告、JSON、Diff 与退出码一致；未完成、失败和未知项在报告中可见。
5. 最终源摘要清单与暂存区一致；公开内容不含本机敏感材料。

设计与实现交接分开记录：本设计组只更新本规格并写入 `local/design-handoffs/F10.md`；代码和测试完成后，实现者按 `docs/acceptance.md` 写入 `local/handoffs/F10.md`，由审查者复跑并登记 `accepted`。F10 在 F01–F09 达到依赖验收门槛后进入实现。
