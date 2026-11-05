# F03 指令发现、作用范围与静态引用检查

依赖 F02。所属组件为 Inspector。公共字段、枚举与状态见 [契约](../contracts.md)，产品目标和判定边界见 [总体方案](../design.md)。本功能回答“项目里有哪些指令材料、它们按已知工具配置预计覆盖什么范围、引用的本地文件和命令是否存在”，不判断规则内容对错，也不确认实际加载。

## 1. 用户结果

工具列出项目范围内的指令文件、它们之间的导入关系、失效的本地引用和无法核对的命令，并明确区分三种信息：

- **发现**：文件真实存在并已捕获为本次运行的证据。
- **预计**：版本化的工具配置声明该文件应进入某类任务的上下文。
- **未知**：配置缺失、作用范围无法解析或读取失败，因此不做加载判断。

`CLAUDE.md` 与 `AGENTS.md` 同时存在只生成“并行规则文件”观察项，不据此判定同会话冲突；子目录规则只覆盖其所在目录及以下。实际是否加载由 F05 的回执回答。

## 2. 范围与非目标

| 本功能负责 | 本功能不负责 |
| --- | --- |
| 枚举候选指令文件并交给 F02 捕获 | 拆分规则条款、解释规则含义（F04、F06） |
| 按 profile 计算预计适用与层内优先顺序 | 用回执确认实际加载（F05） |
| 解析导入边、本地链接与命令语法 | 执行任何项目命令、脚本或构建 |
| 记录目录项计数、截断与未覆盖原因 | 全仓库规则盘点（只扫任务范围祖先目录） |
| 输出结构化的引用与命令核对结果 | 生成 Finding、结论等级或修复建议（F07、F08） |

## 3. 文件与接口

建议新增 `src/context_manager/inspector.py`、`tests/test_f03_inspection.py`，合成夹具放在 `tests/fixtures/f03/`。

```python
def inspect_project(
    request: AuditRequest,
    evidence_store: EvidenceStore,
    *,
    options: InspectionOptions | None = None,
) -> InspectionResult
```

`evidence_store` 必须是已绑定项目根目录、`run_id` 和 `project_id` 的 F02 实例。F03 读取规则文件和导入目标的内容时一律调用 F02，不自行 `open()`；存在性核对使用 F02 的 `probe_path`（见 §9）。项目根目录、输出目录分离和符号链接策略由 F02 执行，F03 不重复放宽。

**输入**：`AuditRequest.project_id`、`repo`、`task_scope.include_prefixes`、`task_scope.exclude_prefixes`、`harness_profile`；已绑定的 `EvidenceStore`；可选的 `InspectionOptions`。`as_of` 与 `semantic_mode` 不参与本功能的判定，F03 只沿用请求中的运行标识，不改写它们。

**输出**：一个 `InspectionResult`，是结构化对象而非报告文本。F09 决定把它作为独立产物还是并入 `checks.json`，F03 不自行写文件；除测试与调试外，调用方不应从 `InspectionResult` 反推 Evidence 之外的原文。

### 3.1 InspectionOptions

| 字段 | 默认 | 约束 |
| --- | --- | --- |
| `entry_limit` | `2000` | 单次检查访问的目录项上限，最小 1 |
| `max_import_depth` | `8` | 导入链最大深度，根目录候选为 0 |
| `max_imported_files` | `200` | 单次检查因导入新增的候选文件上限 |
| `rules_dir_names` | `[".claude/rules"]` | 相对每个扫描目录查找的规则目录 |

低于 1 或类型错误按 F01 的契约错误处理，字段路径为 `options.entry_limit`、`options.max_import_depth`、`options.max_imported_files`。验收可通过显式传入较小的 `entry_limit` 触发截断，无需构造 2000 个文件。

### 3.2 InspectionResult

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 固定 `1` |
| `run_id`、`project_id` | 取自本次运行，用于和证据、报告对齐 |
| `status` | `complete` 或 `partial`；契约错误以异常返回，不产生 `failed` 结果 |
| `rule_files` | `RuleFileCandidate` 列表，按 `relative_path` 升序 |
| `registered_instruction_files` | 当前实际可编辑规则文件的 `{relative_path, evidence_id}` 列表，按路径排序。只纳入成功捕获、范围可解析的已识别规则文件。普通导入文档保留证据但不加入。 |
| `imports` | `ImportEdge` 列表，按 `from_relative_path`、`line`、`column` 升序 |
| `references` | `ReferenceCheck` 列表，按路径、行、列升序 |
| `commands` | `CommandCheck` 列表，按路径、行、列升序 |
| `observations` | `ScopeObservation` 列表，按 `observation_id` 升序 |
| `expected_load_order` | `HarnessOrder` 列表；profile 未知时为空 |
| `coverage` | `CoverageItem` 列表，使用契约字段 `check_id`、`status`、`inputs`、`reason`、`artifact_refs` |
| `counts` | 计数摘要，见 §10 |
| `errors` | `InspectionError` 列表 |

`status` 在任一覆盖率检查为 `failed`、扫描被截断或导入上限触发时为 `partial`，否则为 `complete`。F09 按契约把 `partial` 映射为退出码 3，由 F10 汇总。

### 3.3 结果对象

`RuleFileCandidate`

| 字段 | 取值 |
| --- | --- |
| `candidate_id` | `rf_` + `sha256("rule_file\0" + relative_path)` 前 12 位十六进制 |
| `relative_path` | 根目录相对路径，`/` 分隔，不含前导 `./` |
| `kind` | `agents_md`、`claude_md`、`agents_override_md`、`claude_rule`、`imported_text` |
| `harness` | `codex`、`claude_code`、`shared` |
| `discovery` | `scan_ancestor`、`rules_dir`、`import` |
| `depth` | 扫描目录中的文件为 `0`，导入层级依次加一 |
| `evidence_id`、`sha256`、`byte_length`、`line_count`、`encoding` | 取自 F02 的 `Evidence`；未捕获时为 `null` |
| `readable` | 布尔；失败原因见 `error` |
| `scope` | `RuleScope`，见下 |
| `applies_to_task` | `true`、`false` 或 `null`（作用范围无法解析） |
| `load_expectation` | `expected`、`expected_if_parent_loaded`、`not_expected`、`unknown` |
| `load_basis` | `{profile_id, profile_version, rule}` 或 `null` |
| `precedence` | `selected`、`shadowed`、`unknown`；含 `shadowed_by` |
| `reason` | 机器可读原因码，例如 `excluded_scope`、`outside_task_scope`、`profile_size_limit`、`unreadable`；无特殊原因时为 `null` |
| `error` | `InspectionError` 或 `null` |

`RuleScope`：`include_prefixes`、`exclude_prefixes` 使用契约的前缀语义；`declared_by` 为 `directory`、`frontmatter_paths` 或 `profile_default`；`scope_match` 为 `definite`、`partial`、`none`、`unresolved`。

`ImportEdge`

| 字段 | 取值 |
| --- | --- |
| `edge_id` | `ie_` + `sha256("import\0" + from_path + "\0" + line + "\0" + raw_text)` 前 12 位 |
| `from_candidate_id`、`from_relative_path`、`line`、`column`、`raw_text` | 导入声明的位置与原文 |
| `syntax` | `at_path` 或 `markdown_link` |
| `target_text`、`target_path` | 原样目标与规范化相对路径（无法规范化时为 `null`） |
| `target_state` | `resolved`、`missing`、`outside_project`、`absolute_or_rooted`、`ignored_path`、`rejected_symlink`、`encoded_target`、`directory`、`unsupported_syntax`、`unreadable`、`binary` |
| `traversal` | `followed`、`cycle`、`duplicate`、`skipped`、`limit_reached` |
| `target_candidate_id` | 已解析为目标候选时填写 |
| `reason` | 机器可读原因码，供报告渲染 |

`ReferenceCheck`

| 字段 | 取值 |
| --- | --- |
| `ref_id` | `rc_` + 内容哈希前 12 位，算法同上 |
| `source_candidate_id`、`relative_path`、`line`、`column`、`raw_text` | 位置与原文 |
| `form` | `markdown_link`、`markdown_image`、`inline_code`、`fenced_code`、`html_link` |
| `classification` | `local_file`、`api_literal`、`rooted_path`、`external_url`、`anchor_only`、`code_example`、`not_a_path` |
| `status` | `resolved`、`missing`、`skipped`、`unverified` |
| `target_path`、`reason` | 规范化路径或 `null`；原因码 |

`CommandCheck`

| 字段 | 取值 |
| --- | --- |
| `check_id` | `cc_` + 内容哈希前 12 位 |
| `source_candidate_id`、`relative_path`、`line`、`column`、`raw_text` | 命令出现位置 |
| `family` | `npm`、`pnpm`、`yarn`、`make` |
| `subject` | 脚本名或 Make 目标名 |
| `manifest_path`、`manifest_kind` | 实际使用的清单；`package_json` 或 `makefile` |
| `status` | `resolved`、`missing`、`unsupported_syntax`、`builtin_command`、`dynamic_manifest`、`unresolved_manifest`、`workspace_scope_unknown` |
| `reason` | 原因码 |
| `executed` | 恒为 `false`，作为“只解析不执行”的显式记录 |

`ScopeObservation`：`observation_id`、`kind`（`parallel_harness_rules`、`shadowed_candidate`）、`relative_paths`、`detail`。

`InspectionError`：`code`、`relative_path`、`line`、`detail`。错误码见 §10。

`HarnessOrder`：`harness`、`parent_child`、`one_per_level`、`entries`；每个条目含 `candidate_id`、`directory`、`rank`（无法排序时为 `null`）。

## 4. 扫描范围与目录项计数

扫描根为项目根目录与每个 `include_prefix` 的祖先目录（含自身，去重）。`include_prefixes: ["."]` 只产生项目根。目录深度优先遍历，同级按名称的码点升序，`entry_limit` 对每个被检视的目录项计一次：

- 被忽略目录只计一次、不下降，其内部文件不产生候选。
- 符号链接本身计一次，不跟随，也不作为规则文件候选。
- 二进制判定由 F02 在捕获时完成；F03 不自行解码，命中的二进制文件记为 `readable: false` 并附原因。

默认忽略目录：`.git`、`node_modules`、`.venv`、`venv`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`dist`、`build`、`target`、`.next`、`.cache`、`vendor`、`.context-manager`，以及位于项目内的本次输出目录。忽略清单不随 profile 变化。

每个扫描目录中直接匹配候选名称的文件成为候选；`rules_dir_names` 指向的目录递归查找 `.md` 文件并计为 `claude_rule`。候选名称匹配区分大小写，避免大小写不敏感文件系统把 `agents.md` 误认成 `AGENTS.md`。

`include_prefix` 指向不存在的目录时产生 `E_SCOPE_MISSING`，该前缀不产生候选，覆盖率记为 `failed`；其余前缀继续处理。

达到 `entry_limit` 时停止遍历，记录 `E_SCAN_TRUNCATED`，`counts.truncated` 为 `true`，`f03.scan` 覆盖率 `failed`。这是“不完整”而非“没有规则”的唯一表达方式。

## 5. harness_profile 与预计加载

profile 是版本化的工具配置声明。每个 harness 可选 `context_channels`。只有显式标记为合成的 profile 才可把完整渠道声明提供给 F05 的合成事件核验；省略时按未知渠道处理。结构如下（合成测试夹具使用同一形状）：

```json
{
  "schema_version": 1,
  "profile_id": "synthetic-codex-claude",
  "profile_version": "1",
  "source": "synthetic",
  "harnesses": [
    {
      "id": "codex",
      "display_name": "Codex",
      "known_versions": ["0.0-synthetic"],
      "context_channels": ["project_instructions", "imported_instructions", "rule_directories", "session_injection"],
      "candidate_files": [
        {"filename": "AGENTS.override.md", "role": "override"},
        {"filename": "AGENTS.md", "role": "root"}
      ],
      "candidate_order": ["AGENTS.override.md", "AGENTS.md"],
      "one_per_level": true,
      "parent_child": "nearest_first",
      "import_syntax": ["at_path"],
      "rules_dirs": [],
      "max_file_bytes": 1048576
    },
    {
      "id": "claude_code",
      "display_name": "Claude Code",
      "known_versions": ["0.0-synthetic"],
      "context_channels": ["project_instructions", "imported_instructions", "rule_directories", "session_injection"],
      "candidate_files": [{"filename": "CLAUDE.md", "role": "root"}],
      "candidate_order": ["CLAUDE.md"],
      "one_per_level": true,
      "parent_child": "nearest_first",
      "import_syntax": ["at_path", "markdown_link"],
      "rules_dirs": [".claude/rules"],
      "max_file_bytes": 1048576
    }
  ]
}
```

字段用途：`known_versions` 用于说明“按哪个已知版本判断”，实际版本未核实时把 `source` 和 `profile_version` 写进 `load_basis` 并在报告标注为合成声明；`candidate_order` 与 `one_per_level` 决定层内谁被选中；`parent_child` 为 `nearest_first` 或 `root_first`，决定跨层排序，未知时排序为 `null`；`import_syntax` 限定哪些导入语法视为受支持；`max_file_bytes` 小于 F02 限制时由 F03 额外拒绝。`load_basis.rule` 取 `filename_match`、`ancestor_order`、`import_from:<edge_id>`、`shadowed_by:<candidate_id>` 或 `profile_size_limit`，说明该判断来自哪条声明。

预计加载的判定：

| 条件 | `load_expectation` |
| --- | --- |
| profile 缺失、工具未知或版本未声明 | `unknown`，`load_basis` 为 `null` |
| 候选由扫描发现、`applies_to_task` 为 `true`、`precedence` 不是 `shadowed` | `expected` |
| 候选由导入发现，父候选为 `expected`，且边语法在 `import_syntax` 内 | `expected_if_parent_loaded` |
| 候选由导入发现，父候选不是 `expected` 或语法不受支持 | `not_expected` |
| 候选的层级未被任何任务前缀覆盖 | `not_expected` |
| 候选超过 profile 声明的大小上限 | `not_expected`（配置明确排除） |
| 候选因不可读、坏编码或 F02 大小上限未能捕获 | `unknown`（材料缺失，不能断言不加载） |
| `applies_to_task` 为 `null` | `unknown` |

映射到契约的加载观察：`expected` 与 `expected_if_parent_loaded` 记为 `expected`；`unknown` 记为 `unknown`；`not_expected` 只记为 `discovered`。报告保留 `expected_if_parent_loaded` 这个条件标签，不把它写成无条件预期。F03 永不输出 `observed_loaded` 或 `observed_not_loaded`。

`expected_load_order` 按 harness 输出本层与祖先层中 `expected` 候选的排序；`nearest_first` 由深到浅，`root_first` 由浅到深，同层按 `candidate_order`。`shadowed` 候选仍保留在 `rule_files` 中并写入 `shadowed_by`，不删除、不改写原文。

发现范围不因 profile 收窄：候选文件名集合是内置名称（`AGENTS.md`、`CLAUDE.md`、`AGENTS.override.md`）与所有 harness 已声明文件名的并集，规则目录同样取选项与 profile 的并集。profile 只决定 harness 归属、层内顺序与预计加载，不决定“是否发现”。因此未声明的 `CLAUDE.md` 仍会出现在清单里，只是 `harness` 为 `shared`、`load_expectation` 为 `unknown`。profile 缺失时全部走这一分支，即“项目候选盘点”的起点。

## 6. 作用范围判定

候选目录 `D` 为 `relative_path` 的父目录（根目录文件为 `.`）。`include_prefix` 已按 F01 规范化：无前导 `/`、无 `..`、`/` 结尾表示目录。

| 情形 | `scope_match` | `applies_to_task` |
| --- | --- | --- |
| `D` 等于某前缀 | `definite` | `true` |
| `D` 是某前缀的祖先（含 `D` 为 `.`） | `definite` | `true` |
| 某前缀是 `D` 的祖先（候选位于任务子树内部） | `partial` | `true` |
| 无前缀关系 | `none` | `false` |
| 前缀解析失败或 glob 不受支持 | `unresolved` | `null` |

排除前缀相对任务根解析，与候选区域的交集取 `D` 与 `p` 中较深者。该交集若被某个排除前缀等于或覆盖，这个任务前缀就不提供适用性；全部前缀都不提供时 `applies_to_task` 为 `false`、`reason` 记为 `excluded_scope`。排除前缀只切掉交集的一部分时仍为适用，`scope_match` 保持 `definite` 或 `partial`。被排除的候选与证据照样保留，只标注不适用。

`load_expectation` 与 `applies_to_task` 解耦：不适用只影响“预计进入上下文”，不影响文件被发现和引用被检查。

`.claude/rules` 中的文件默认 `declared_by: "profile_default"`、作用范围为项目根。带 `paths:` 前置字段的规则文件按受限子集解析：

- 纯字面路径或 `**` 结尾的字面前缀参与与上表相同的比较，得到 `definite` 或 `partial`。
- 含其他通配符（`*`、`?`、`[]`、`{}`）或无法解析的写法记为 `unresolved`：保留候选、`applies_to_task` 为 `null`、`load_expectation` 为 `unknown`，并写 `E_SCOPE_UNRESOLVED`。
- 前置字段只读取首个 YAML 块中的 `paths:` 列表；块内其他键忽略，不以规则正文推断范围。

F03 不展开 glob 到具体文件，也不遍历任务子树之外的目录，因此范围判断是“与任务前缀是否有交集”，不是逐文件匹配。

## 7. 导入与本地引用

### 7.1 导入语法

| 语法 | 识别方式 | 说明 |
| --- | --- | --- |
| `at_path` | 整行去除列表标记与空白后为 `@<目标>`，或列表项内容以 `@<目标>` 开始 | 目标不含空白；含空白或反斜杠转义为 `unsupported_syntax` |
| `markdown_link` | 行内链接写法：方括号文本后紧跟圆括号目标，目标可带尖括号与可选标题 | 只支持行内写法，不接受引用式定义（冒号加路径） |

图像语法（`!` 开头的同类行内链接）、行内 HTML 链接、自动链接、引用式定义、HTML 注释内的内容都不作为导入，只按 §7.3 记录为 `skipped` 或 `unverified`。同一条目在一行内重复出现只记一条边。

### 7.2 目标解析

1. 去掉 `#fragment`；含 `?`、百分号编码或 `~` 前缀时记为 `encoded_target` 或 `absolute_or_rooted`，不读取。
2. 相对导入文件所在目录解析，`posixpath.normpath` 规范化；结果为空、以 `/` 开头、含驱动器或落在项目外记 `absolute_or_rooted` 或 `outside_project`。
3. 路径任一组件的符号链接、命中忽略目录、指向目录用 `probe_path` 判定，分别记 `rejected_symlink`、`ignored_path`、`directory`，不读取内容；二进制与非文本由 F02 的捕获结果判定，记 `binary` 或 `unreadable`。
4. 不存在记 `missing`，并生成同位置的 `ReferenceCheck`（`classification: local_file`、`status: missing`），供 F07 生成 `dead_reference`。
5. 存在且为文本时经 F02 捕获为 `imported_text` 候选，记 `traversal: followed`；再次到达同一路径记 `duplicate`，栈上回边记 `cycle`，不重复捕获。
6. 深度超过 `max_import_depth` 或新增文件超过 `max_imported_files` 时记 `limit_reached`，`errors` 追加 `E_IMPORT_DEPTH` 或 `E_IMPORT_LIMIT`，`f03.imports` 覆盖率 `failed`。

同一路径在一次检查中只捕获一次；若 F02 未提供幂等捕获，F03 以路径到 `evidence_id` 的映射去重。

### 7.3 分类顺序

`form` 只记录出现位置；`classification` 决定“为什么检查或不检查”，按下列固定顺序判定，先命中者生效：

| 顺序 | 条件 | `classification` | `status` |
| --- | --- | --- | --- |
| 1 | 带 `scheme://`、`mailto:`、`file:` | `external_url` | `skipped` |
| 2 | 仅 `#anchor` | `anchor_only` | `skipped` |
| 3 | 以 `/` 开头且无文本扩展名 | `api_literal` | `skipped` |
| 4 | 以 `/` 开头且有文本扩展名（如 `/docs/x.md`） | `rooted_path` | `unverified` |
| 5 | 不含 `/` 且无扩展名 | `not_a_path` | 不产生记录 |
| 6 | 位于围栏代码块或行内代码中的相对路径 | `code_example` | `skipped` |
| 7 | 其余相对目标 | `local_file` | `resolved` 或 `missing` |

内容判定先于上下文判定：在行内代码里出现的 `/api/v1/users` 是 `api_literal`，不会被降级成泛化的示例；围栏里出现的 `clients/new_user.py` 是 `code_example`，不做存在性判断。因此 API 字面量与代码示例都不会被报成失效文件，而代码外的确切失效链接保留原文、行、列与规范化目标，供报告直接定位。

`.md` 和 `.markdown` 的本地链接同时产生导入边；其他扩展名的本地链接只做存在性核对，不捕获、不遍历。存在性核对不得读取文件内容。

## 8. 命令检查

从候选文件的以下位置提取命令：行内代码、信息串为空的围栏块，以及 `sh`、`bash`、`shell`、`zsh`、`console` 的围栏块。每个代码行单独解析；整行注释与空行忽略。

支持的形式：

- `npm run <script>`、`pnpm run <script>`、`yarn run <script>`，允许尾部 `--` 及之后的参数。
- `make <target>`；`make -C`、`-f`、变量赋值、多目标、续行、`&&`、`|`、`$(...)`、反引号、重定向均记 `unsupported_syntax`，不猜测。
- 单独的 `yarn <script>` 只在清单中存在同名脚本时记 `resolved`；否则记 `unsupported_syntax`，因为可能是包管理器内建命令而不是脚本。
- `npm test`、`npm start` 等内建脚本名在清单中不存在时记 `builtin_command`，不报缺失。

清单查找：从命令所在规则的目录向上找到项目根，取第一个 `package.json`（npm、pnpm、yarn）或 `Makefile`、`makefile`、`GNUmakefile`（make）。找不到记 `unresolved_manifest`。`package.json` 非法 JSON 记 `dynamic_manifest` 并附 `E_MANIFEST_INVALID`，覆盖率 `f03.commands` 为 `failed`。

存在性判定：

| 情形 | `status` |
| --- | --- |
| 清单中存在该脚本或静态目标 | `resolved` |
| 清单可解析、脚本或目标不存在、无动态生成迹象 | `missing` |
| 清单声明 `workspaces` 且根清单无该脚本 | `workspace_scope_unknown` |
| Makefile 含 `include`、`define`、`$(eval` 等动态生成迹象且目标未找到 | `dynamic_manifest` |
| 语法不受支持、多命令或需要执行 shell 展开 | `unsupported_syntax` |

Make 静态目标的识别限于 `^[A-Za-z0-9][A-Za-z0-9_.-]*\s*:` 且冒号后不是 `=`；`.PHONY` 等特殊目标、后缀规则和含 `%` 的模式规则不参与匹配。

命令检查只读清单文本，不运行 `npm`、`pnpm`、`yarn`、`make`，也不调用 `shutil.which` 推断可执行性。`executed` 恒为 `false`。

## 9. 依赖接口

**上游 F01**

- `AuditRequest`：读取 `project_id`、`repo`、`task_scope.include_prefixes`、`task_scope.exclude_prefixes`、`harness_profile`。
- 前缀与时区的规范化、契约错误类型由 F01 提供；F03 对非法输入抛出同一异常族，字段路径写到具体键。

**上游 F02**（必须确认后实现）

| 需要 | 用途 |
| --- | --- |
| `EvidenceStore(project_root, output_root, run_id)` | 绑定本次运行 |
| `capture_file(relative_path, *, purpose="rule_file") -> Evidence` | 捕获规则文件与导入目标；同一路径重复调用返回同一 `evidence_id` |
| `probe_path(relative_path) -> PathProbe` | 存在性、是否符号链接、是否目录、字节数；`PathProbe` 至少含 `status`、`is_symlink`、`is_dir`、`byte_length`，不返回内容 |
| 读取错误类型可区分 `unreadable`、`encoding`、`too_large`、`symlink`、`outside_project` | 映射为 F03 错误码 |

按[接口矩阵](../interfaces.md)调用 F02。忽略目录由 F03 先过滤，剩余路径交 F02 做探测或捕获。

**下游**：F10 用已解析的 `rule_files[].scope` 投影出 F04 的 `source_scope`，未解析的范围只进入覆盖缺口；F07 用 `references` 中 `status: missing` 的条目生成 `dead_reference`；F09 用 `coverage`、`counts`、`errors` 渲染覆盖情况；F10 在证据采集之后调用本入口。

## 10. 覆盖率、计数与错误码

覆盖率检查 ID 固定如下，`artifact_refs` 为空时由 F09 决定展示方式。

| check_id | 通过条件 | 失败条件 |
| --- | --- | --- |
| `f03.scan` | 未截断，所有扫描根可读 | 达到 `entry_limit`、扫描根缺失或不可读 |
| `f03.rule_files` | 所有匹配候选均已捕获，或仅因 profile 声明的大小上限被排除 | 存在被列入检查但无法读取的候选，或无法归因的捕获失败 |
| `f03.imports` | 所有导入边到达终止状态 | 深度或数量上限触发、父文件未捕获 |
| `f03.references` | 所有引用完成分类与核对 | 文件不可读导致无法核对 |
| `f03.commands` | 所有命令完成核对 | 清单不可解析 |
| `f03.scope` | 所有范围声明可解析 | 存在 `E_SCOPE_UNRESOLVED` |

未找到任何规则文件、引用或命令时，对应检查仍为 `passed`，`reason` 记 `none_found`，`counts` 中为 0；不能用 `not_run` 表示“没有东西可查”。

profile 声明的大小上限属于配置决定的排除：记 `E_PROFILE_SIZE_LIMIT`、`not_expected`，`f03.rule_files` 保持 `passed`，不因此把整体置为 `partial`。F02 侧的大小上限、坏编码与不可读属于材料缺失，使 `f03.rule_files` 为 `failed`。

`counts` 字段：`directories_visited`、`entries_visited`、`truncated`、`rule_files`、`imports`、`references_checked`、`references_missing`、`commands_checked`、`commands_missing`。

错误码：`E_SCOPE_MISSING`、`E_SCAN_TRUNCATED`、`E_IMPORT_DEPTH`、`E_IMPORT_LIMIT`、`E_SCOPE_UNRESOLVED`、`E_FILE_UNREADABLE`、`E_ENCODING`、`E_TOO_LARGE`、`E_PROFILE_SIZE_LIMIT`、`E_MANIFEST_INVALID`。

每条 `InspectionError` 同时体现在相关对象的 `error` 字段和结果级 `errors` 中，两者内容一致，避免报告与 JSON 不一致。

## 11. 确定性与安全副作用

- 相同输入、相同 profile、相同选项产生相同对象与 ID；只有 F02 的 `captured_at` 和运行级 `run_id` 随运行变化。
- ID 由内容与路径推导，不依赖遍历顺序；所有列表排序规则写在 §3.2。
- 不写入被审计项目，不修改任何源文件；只读通过 F02 完成。
- 不跟随符号链接、不读取忽略目录、不解析项目外路径；这些拒绝先在 F03 判定，再由 F02 兜底。
- 不启动子进程、不执行命令、不访问网络、不读取环境变量里的凭据。
- 遍历只覆盖任务范围祖先目录、规则目录与导入链，不因扫描范围小而声称已盘点全项目。

## 12. 本地测试

```sh
.venv/bin/python -m pytest tests/test_f03_inspection.py tests/test_f02_evidence.py -q
```

夹具放在 `tests/fixtures/f03/`：合成 profile（形如 §5）、规则文件片段与清单片段；项目树在 `tmp_path` 中由辅助函数按字典构造，便于每个用例独立设置符号链接、坏编码和不可读文件。证据存储使用 F02 的真实实现，需要观察捕获行为时套一层记录代理，不写替代实现。

“未执行命令”的断言方式：临时替换 `subprocess.run`、`subprocess.Popen`、`os.system`、`os.spawnv` 为抛 `AssertionError` 的替身，并断言用例结束后调用次数为 0；同时断言 `commands` 中 `executed` 全为 `false`。

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| F03-01 | 根与子目录各有规则，任务位于其中一个子树，并声明该子树内的排除前缀 | 候选齐全；根候选对 `clients/` 为 `definite`、`clients/api/` 候选为 `partial`；被排除前缀完全覆盖的 `clients/legacy/` 候选为 `applies_to_task: false`、`reason: excluded_scope`；无关的 `legacy/` 候选为 `none`；来源路径与证据 ID 对应 |
| F03-02 | 未知或缺失 harness profile | 候选照常捕获并记为 `shared`；`load_expectation` 全为 `unknown`；`expected_load_order` 为空；`f03.rule_files` 仍 `passed` |
| F03-03 | 合成 profile 的覆盖文件、层内顺序与大小上限 | 同目录 `AGENTS.override.md` 为 `selected`、`AGENTS.md` 为 `shadowed` 且 `shadowed_by` 指向覆盖文件；超 profile 上限的文件 `readable: false`、`E_PROFILE_SIZE_LIMIT`、`not_expected` |
| F03-04 | 相对导入、重复导入、循环、缺失及越界 | 相对路径按导入文件目录解析；重复边为 `duplicate` 且不新增候选；循环为 `cycle` 无死循环；缺失为 `missing` 并有引用位置；`..` 越界为 `outside_project` 且未捕获 |
| F03-05 | API 字面量、围栏内路径、行内代码、普通本地链接 | `/api/v1/users` 为 `api_literal`；围栏与行内示例为 `code_example`；确切失效链接为 `local_file`/`missing` 且行列准确；四类都不进入导入边 |
| F03-06 | 存在与缺失的脚本、静态 Make 目标、复合命令 | `resolved`、`missing`、`builtin_command`、`unsupported_syntax` 分别正确；命令监视器计数为 0；`executed` 全为 `false` |
| F03-07 | 扫描达到上限、规则文件不可读 | `truncated` 为 `true`、`E_SCAN_TRUNCATED`、`f03.scan` 为 `failed`；不可读文件 `readable: false`、带 `E_FILE_UNREADABLE` 或 `E_ENCODING`；结果 `status` 为 `partial`，不能表达成“项目无问题” |
| F03-08 | 同一目录同时存在 `AGENTS.md` 与 `CLAUDE.md` | 两个候选各自的 `harness`、作用范围和预计加载独立；不产生合并后的单一候选；只有 `parallel_harness_rules` 观察项，没有冲突结论 |
| F03-09 | 同一输入连续运行两次 | 除 `run_id`、`captured_at` 外对象逐字段相等；ID 相同；`rule_files`、`imports`、`references`、`commands` 顺序稳定 |
| F03-10 | 导入命中 `node_modules`、超过深度、超过文件数 | 忽略目录为 `ignored_path` 且未捕获；深度/数量上限分别触发 `E_IMPORT_DEPTH`、`E_IMPORT_LIMIT`，`f03.imports` 为 `failed`，不再继续展开 |
| F03-11 | 文件符号链接、目录符号链接、`..`、绝对路径、`~`、百分号编码 | 分别得到 `rejected_symlink`、`outside_project`、`absolute_or_rooted`、`encoded_target`；F02 捕获记录中不存在这些路径；项目文件摘要前后一致 |
| F03-12 | 空项目、无规则文件、超大文件、坏编码 | 空项目为 `complete`、候选为空、相关检查 `passed` 且 `reason: none_found`；超大与坏编码按 `E_TOO_LARGE`、`E_ENCODING` 记录并令整体 `partial` |
| F03-13 | `workspaces` 根清单无脚本、非法 `package.json`、无清单、尾部参数 | 分别为 `workspace_scope_unknown`、`dynamic_manifest` 加 `E_MANIFEST_INVALID`、`unresolved_manifest`；`npm run test -- --watch` 正常解析为 `resolved` |
| F03-14 | 规则目录文件带 `paths:` 字面路径、`**` 前缀、其他通配符 | 前两者为 `definite` 或 `partial`；不受支持的通配符为 `unresolved`、`applies_to_task: null`、`E_SCOPE_UNRESOLVED`、`f03.scope` 为 `failed` |

依赖 F02 的用例先跑；F03-11 中依赖符号链接的断言在 macOS 与 Linux 执行，Windows 跳过并记录 `skipped`。

## 13. 通过与交接

审查者检查：扫描与证据读取确实共用 F02；候选状态没有从 `discovered` 升级成实际加载；受支持的命令语法、profile 覆盖范围、忽略目录和限制值写入报告；截断、不可读与未解析范围都以 `partial` 或 `failed` 表达，而不是被当作干净结果。

实现交接写入 `local/handoffs/F03.md`，附实现文件、测试命令与退出码、夹具、源摘要、偏差和 `ready_for_review` 或 `in_progress` 建议。