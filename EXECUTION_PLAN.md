# 执行计划与当前状态

本文件是实现与审查的统一记录。新模型接手时先读取本文件，再阅读当前功能规格。总体设计版本 `0.2`，跨功能契约修订 `r3`，最后更新 `2026-11-05`。

## 当前状态

| 项目 | 当前值 |
| --- | --- |
| 当前阶段 | F02-H02 已复核 accepted，F03 可领取实现 |
| 专项设计交接 | 十份交接已收齐，保存在 `local/design-handoffs/` |
| 下个实现验收项 | F03，完成规则文件发现、作用范围、失效引用和命令检查并复跑 F01/F02 回归 |
| 运行时架构 | 1 个只读 Context Analyst + Inspector、Evidence Store、Diagnostic Policy、Repair Planner |
| 专项设计负责人 | 每个功能一个设计组，均已交接 |
| 实现负责人 | F01-H02、F02-H02 已验收；F03 待领取，F04–F10 待依赖齐备 |
| 设计与代码复核 | 本聊天，收到功能交接后对照记录和实际代码审查 |
| 当前实现 | F01 工程、公共契约、CLI 骨架及测试已落地；F02 证据存储、受限读取与测试已验收；实际 audit/diagnose 管线由 F10 接入 |
| 当前 Git | 发布已验收的 F01/F02 与统一检查计划到 `main`，阶段提交以 Git 历史为准；完整工作流交付待 F10 |
| 公开仓库 | [P0tenti4L-220/context-manager](https://github.com/P0tenti4L-220/context-manager) |
| 功能测试 | F01 为 12 passed、1 skipped，7 个审查探针 passed；F02 为 30 passed（含 F01）、1 skipped，5 个审查探针 passed；F03–F10 not_run |
| 真实本地模型测试 | `not_run`；本机 Ollama 0.40.14 + Qwen2.5-7B-Instruct Q4_K_M 已就绪，配置 `local/model.toml` 并完成回环冒烟，真实语义案例留待 F06 |
| 文档检查 | 基线与 r2 检查见 D-001/D-003；r3 澄清与 F01 审查见 R-002/R-003；F02 审查与整改复核见 R-004/R-005 |
| 提交与交付 | 当前发布 F01/F02 开发快照及 F03–F10 统一检查计划；完整工作流交付 pending |

## 设计入口

- [总体方案](docs/design.md) 说明产品目标、架构和功能顺序。
- [公共契约 r3](docs/contracts.md) 约定所有组件的字段、状态、证据和输出。
- [跨功能接口矩阵及变更影响](docs/interfaces.md) 固定调用方式、责任和验收映射。
- [本地验收规范](docs/acceptance.md) 约定实际测试、源摘要和审查方式。
- [F03–F10 统一实现与检查计划](docs/implementation-checklist.md) 汇总后续交付、命令、交接和完整工作流发布门槛。
- [功能设计子 Agent 分工](docs/agent-assignments.md) 列出十个可同时派发的设计任务和交接方式。
- [同类项目源码分析](docs/landscape.md) 保存架构依据。

## 功能计划

每个设计组负责一份规格，设计工作可以并行。实现模型按确认后的规格实现并运行本地测试，依赖齐备后联调与正式验收。表中的实现状态和测试结果以实际交接为准。

| ID | 功能规格 | 直接依赖 | 专项设计 | 实现状态 | 实施者 | 本地测试 | 审查 | 最近运行记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F01 | [工程入口与公共契约](docs/features/F01-foundation.md) | 无 | 已确认 | accepted | F01-H02 | passed | accepted | R-003 |
| F02 | [证据快照与受限读取](docs/features/F02-evidence.md) | F01 | 已确认 | accepted | F02-H02 | passed | accepted | R-005 |
| F03 | [发现、范围与引用检查](docs/features/F03-inspection.md) | F02 | 已确认 | ready | 待领取 | not_run | pending | — |
| F04 | [规则与决策](docs/features/F04-rules-decisions.md) | F02、F03 | 已确认 | planned | 无交接 | not_run | pending | — |
| F05 | [事件与加载回执](docs/features/F05-incidents.md) | F02、F04 | 已确认 | planned | 无交接 | not_run | pending | — |
| F06 | [本地语义 Agent](docs/features/F06-analyst.md) | F02、F04、F05 | 已确认 | planned | 无交接 | not_run | pending | — |
| F07 | [证据核验与诊断](docs/features/F07-diagnosis.md) | F03–F06 | 已确认 | planned | 无交接 | not_run | pending | — |
| F08 | [修复 Diff 预检](docs/features/F08-repair.md) | F04、F07 | 已确认 | planned | 无交接 | not_run | pending | — |
| F09 | [报告与运行产物](docs/features/F09-reporting.md) | F02、F07、F08 | 已确认 | planned | 无交接 | not_run | pending | — |
| F10 | [CLI 集成与最终交付](docs/features/F10-integration.md) | F01–F09 | 已确认 | planned | 无交接 | not_run | pending | — |

## 状态更新规则

专项设计按 `待领取 → 设计中 → 待复核 → 已确认` 记录。设计者提交 `local/design-handoffs/Fxx.md`，由本聊天核对并更新该列。下面的状态用于实现与测试。

| 状态 | 进入条件 | 允许的下一步 |
| --- | --- | --- |
| planned | 专项设计待确认或依赖尚未审查通过 | 准备夹具和环境 |
| ready | 专项设计已确认，依赖均 accepted | 登记实施者后开始 |
| in_progress | 正在实现或修复自测失败 | 写代码并执行本地测试 |
| ready_for_review | 本项测试与必要回归通过，实际记录和源摘要完整 | 本聊天复核 |
| changes_requested | 审查发现偏差、缺测试或记录与代码不符 | 修改并复跑 |
| accepted | 审查者检查实际代码并完成针对性复跑，符合规格 | 开启下一项 |
| blocked | 实现缺少环境或必须由用户决定的信息，已记录具体问题 | 补齐后恢复实施 |

实现者在独立交接文件内填写测试结果和状态建议，由本聊天统一登记执行计划并填写 `accepted`。任何影响功能的共享代码、提示词、配置或契约变更，都要把相关审查列更新为 `needs_recheck`，复核前不满足最终提交条件。

本地测试状态用 `not_run`、`passed`、`failed`。跳过的必需测试单独列出，不能计入全部通过。F06 另记录 `runner_tests` 和 `local_model_tests`，两者均通过才进入审查。

## 实现者交接模板

实现者按功能写入 `local/handoffs/Fxx.md`，同一功能的多次交接追加记录。审查者在下方登记交接编号、文件位置和审查结论。测试原始日志保存在 Git 忽略的 `local/verification/`。

```text
功能 ID / 交接编号
实施者与完成时间
依据的设计版本与规格路径
实现文件及关键入口
测试文件与新增合成夹具
行为变化与验收条目对应关系
设计偏差及原因（无偏差也写明）
本机环境（Python、pytest、OS，本地模型任务另附运行器与模型）
实际执行命令、开始时间、耗时、退出码
passed / failed / skipped 数量及跳过原因
原始日志相对路径
源文件摘要清单路径及清单 SHA-256
本地模型各案例输出路径（F06）
对已 accepted 功能的影响及回归结果
剩余问题、需要审查者重点核对的内容
状态建议 ready_for_review 或 in_progress
```

## 审查者记录模板

```text
功能 ID / 对应交接编号
审查日期与审查者
实际读取的代码、测试与规格
当前文件与测试摘要是否匹配
复跑命令、退出码、日志位置
逐条验收结果
是否符合用户目标（规则清理、重复错误溯源、正确修复方向）
发现的问题（严重性、文件与行号、影响、所需修复）
设计偏差接受或驳回的理由
审查结论 accepted 或 changes_requested
需要重新检查的已完成功能
下一项可领取功能
```

## 执行与审查记录

### D-001 详细设计交付

- 日期为 `2026-11-04`，设计版本为 `0.2`。
- 已拆分 F01–F10，定义输入输出、实现边界、正负例、本地测试命令与审查标准。
- 已建立公共契约、执行状态和交接流程。
- 现有示例回执只是 ID 引用，F05 将补足真实可校验的合成回执并保留旧格式缺口。
- 产品实现和功能测试均待开始。
- 文档检查实际在本机执行，退出码 0，覆盖 18 份 Markdown、49 个本地链接/锚点、10 份功能规格和初始状态一致性。记录在 `local/verification/design-v0.2/checks.txt`。
- 既有手写样例 Diff 使用 `git apply --check --verbose --directory=examples/stale-users-api/project examples/stale-users-api/proposal.diff` 验证通过，退出码 0。这个结果只说明样例补丁匹配文件。
- 实际检查确认产品 `src/` 与 `tests/` 目录尚未创建，所有功能测试和本地模型测试仍为 `not_run`。

### D-002 功能设计分组

- F01–F10 各设一个设计组，每组只细化一份规格，所有组均可同时开始。
- 实现和测试按功能记录。全部功能通过本地验收后，由本聊天复核并汇总最终功能结果。

### R-001 组装前检查

- `2026-11-04` 核对十份专项规格和设计交接。当前工作区没有 `pyproject.toml`、`src/context_manager/`、`tests/`、`local/handoffs/` 或 `local/model.toml`。本地 `main` 无提交，未注册其他工作树，`git ls-remote --symref origin` 退出 `0` 且无输出。
- 本机执行 `rtk proxy python3 -B -m context_manager --help`，退出 `1`，`No module named context_manager`。手写示例补丁的 `git apply --check` 退出 `0`。原始检查记录位于 `local/verification/assembly-review-2026-11-04/checks.txt`。
- F01–F10 产品测试、F06 的 `runner_tests` 和 `local_model_tests`、F10 的 CLI 集成验收和 wheel 构建安装均为 `not_run`。当前缺少可复核的实现快照与测试证据，组装待定。
- [F10 联调规格](docs/features/F10-integration.md) 与专项交接中待对齐的接口，优先复核以下四处。
  - F01/F04/F10 明确静态登记的路径、行段和摘要如何绑定为本次运行的 `EvidenceRef`。
  - F02 明确供 F03/F05/F08 使用的路径探测、限量读取完整快照字节以及复核当前文件摘要的接口。
  - F04/F08 统一确认记录如何绑定旧文本与替换文本的摘要，明确何时允许 `preview_ready`。
  - F06/F09/F10 统一 trace 的传递入口，并在 F09 的覆盖规则中明确处理 `--semantic off` 对应的 `not_run:disabled_by_user`。
- 手写示例报告把旧格式中的回执 ID 当成了加载证据。F10 应以正式 CLI 复跑旧样例，核对加载 `unknown` 和复发结论的降级。

### D-003 跨功能契约 r2

- `2026-11-04` 按十项设计交接合并裁决，更新公共契约、十份专项规格、接口矩阵和 F01/F02 交接附录。设计修订号为 `r2`，对外 JSON 继续使用 `schema_version: 1`。
- F01 可领取；F02–F10 仍按表中的依赖验收推进。F10-01 的正例使用精确改法批准，预览只改目标行；缺失或不匹配时待确认。旧示意报告由 F10 依据正式 CLI 结果更新。
- 本机确认 Python 3.11.9 与 Git 可用；项目 `.venv`、pytest 开发依赖和本机模型服务仍需准备。模型环境影响 F06/F10 的实际语义验收，F01 可以先实施。
- r2 本机文档核对退出 0，覆盖 27 份 Markdown（含两份本机交接）、104 个本地链接/锚点、21 个 JSON 示例及 131 条机械用例和 7 个模型案例的编号。记录位于 `local/verification/contracts-r2/checks.txt`。F01–F10 产品测试、F06 真实模型和 F10 打包仍为 `not_run`。

### R-002 F01-H01 实现审查与 r3 澄清

- 审查日期为 `2026-11-05`，对照 F01 规格、公共契约、接口矩阵、全部 F01 源码和测试，以及 `local/handoffs/F01.md`。原清单 SHA-256 为 `df7236722c3d9de1ad090a07b1021f1d3cbaa84a12c52670fb7668ca86090bc3`，7 个文件的摘要与字节数均匹配。
- 本机复跑 compileall、F01 pytest、排除 local_model 的全部测试、模块帮助/版本与控制台版本，退出码均为 0。既有 F01 测试为 `12 passed, 1 skipped`，排除模型探针为 `12 passed, 1 deselected`。跳过项只证明配置门控未请求模型，真实模型仍为 `not_run`。
- 定向审查探针实际复现 7 个失败，按四组整改。P1 为空的完整回执被结构校验拒绝，以及嵌套容器可变且参数与输入共享。P2 为 JSON 深度、整数长度和数值转换错误未统一处理，以及 argparse 回显非法参数值。命令、耗时与原始日志在 `local/verification/F01/review-20261105/`。
- r3 澄清 F01 只校验空回执的结构，由 F05 校验有效完整覆盖；修正 F01 旧边界表和错误路径示例。接受 `BaseFile.evidence_id` 省略后补 null、`dumps` 末尾换行及 F09 从顶层补 `as_of` 的实现决定。
- 审查结论为 `changes_requested`。本行的测试 `failed` 表示审查探针失败，既有 12 项自测通过记录保留。修复后须补回归、复跑并生成新的源码清单，不能沿用 F01-H01 原清单。
- F02 维持 planned。F01 达到 accepted 后可领取 F02，实现证据捕获、受限读取、预读与全量字节入口，并运行 F02-01～11 和 F01 回归。

### R-003 F01-H02 整改复核

- 审查日期为 `2026-11-05`。读取 R1–R4 的实现、回归断言与 F01-H02 交接，确认空回执结构通过、嵌套容器隔离及冻结、解析边界错误转换、CLI 受限消息均进入正式入口。接受 audit 的隐藏 `--case` 参数安排，其正常调用仍经 build_request 以退出码 2 拒绝，帮助中不展示。
- H02 清单 SHA-256 为 `afb2fe1039f12d984cce1ad8b809ce42303808242503c5890bf5fc03e77d51b5`。7 个文件的摘要与字节数在复跑前后均匹配，原审查探针源码也与 R-002 清单一致。
- 实际复跑 compileall、F01 pytest、全部非模型测试、7 个原审查探针、顶层及 audit 帮助、模块版本、控制台版本，退出码均为 0。F01 为 `12 passed, 1 skipped`，非模型为 `12 passed, 1 deselected`，探针为 `7 passed`。唯一 skip 是未提供配置的 local_model 门控，真实模型仍为 `not_run`。
- 日志与命令记录位于 `local/verification/F01/review-h02-20261105/`。审查结论为 `accepted`，R1–R4 已关闭，F02 调整为 `ready`。F03–F10 的实现与测试仍待推进。
- GitHub 只读查询确认公开仓库已存在，仓库 size 为 0，main 分支查询为空。按最终统一交付约定，首版提交和发布在 F10 验收后执行。

### R-004 F02-H01 实现审查

- 审查日期为 `2026-11-05`。读取 F02 规格、契约 r3、接口矩阵、F09 证据目录约定、`src/context_manager/evidence.py` 全部实现、`tests/test_f02_evidence.py` 与 16 个夹具；对照 F01-H02 清单确认 `contracts.py`、`cli.py`、`conftest.py`、`pyproject.toml` 和 F01 测试未变。
- F02-H01 清单 SHA-256 为 `0227aeabb8574aae0b5cb28a8fc6ebc94d0c36140390d1c5b834b87e4dda1601`，16 个文件的摘要与字节数均匹配。本机复跑 compileall、F02+F01、全量、非模型和 F01 单独测试，退出码均为 0：F02+F01 与全量为 `28 passed, 1 skipped`，非模型为 `28 passed, 1 deselected`，F01 为 `12 passed, 1 skipped`。日志在 `local/verification/F02/review-20261105/`。
- F02-01～11 均有实际断言的通过记录。独立审查探针 3 failed、2 passed：R1 为 `evidence_input_id_conflict` 错误作用于 imported，同一 `input_id` 的第二份历史内容被拒绝，会阻塞 F05（P1）；R2 为 `_walker.check` 后按完整路径重新打开，父目录被替换为符号链接时可读到项目外字节（P2，已用探针复现 `OUTSIDE-SECRET`）。
- 审查结论为 `changes_requested`，F02 保持 `changes_requested`。要求 R1 只约束选定输入的重复 `input_id`、imported 按 `(input_id, sha256)` 去重；R2 改为 dirfd 链打开并保留 `_read_once` 钩子。修复后补回归、追加 F02-H02 并重新生成清单。
- 接受范围错误码裁决、`open_existing` 不依赖原项目、`index.json` 信封、`snapshot_bytes` 非项目来源错误码与非法 `caller` 归一化等偏差。同时把 F09 规格第 77 行外部输入逻辑名从 `inputs/...` 修正为契约 r3 的 `selected-input/...`。
- 第二轮复核通过前，F03 不领取；真实本地模型仍为 `not_run`。

### R-005 F02-H02 整改复核

- 审查日期为 `2026-11-05`。读取整改后的 `evidence.py`、两个新增回归和 H02 交接；H02 清单 SHA-256 `37699e01d695f3953ec4464ae35bf7f936cebf885397cd79fdf302ed69e3b7c2` 的 16 个文件摘要/字节数与工作树一致，F01 文件摘要未变。
- R1 已按来源隔离：`evidence_input_id_conflict` 只约束 selected 输入的重复使用，imported 按 `(input_id, sha256)` 去重并允许多份不同内容，F05 的“事件包 + 包内历史文件”场景有回归。R2 已改为项目根 fd 起逐级 `openat`、末级 `O_NOFOLLOW`，读取与复核共用 fd 链，保留 `_read_once` 钩子；父目录竞态回归实际拒绝并确认无证据/快照、覆盖 `not_run`、源摘要不变。
- 本机独立复跑 compileall、F02+F01、全量、非模型和 F01 均退出 0：F02+F01 与全量为 `30 passed, 1 skipped`，非模型 `30 passed, 1 deselected`，F01 `12 passed, 1 skipped`；审查探针 `5 passed, 0 failed`。日志在 `local/verification/F02/review-20261105/run2/`。
- 审查结论为 `accepted`。F02 标记 `accepted`，F03 因依赖满足调整为 `ready`。已接受偏差与 P3 记录项保留在 F02 交接中；真实本地模型仍为 `not_run`，留待 F06/F10。

### R-006 阶段发布前复核

- 日期为 `2026-11-05`。核对 F02-H02 的来源冲突隔离、逐级目录 fd 读取、两个回归以及原审查探针。16 个 F02 清单文件与 7 个 F01 清单文件在本轮复跑前后均匹配；F02 清单 SHA-256 为 `37699e01d695f3953ec4464ae35bf7f936cebf885397cd79fdf302ed69e3b7c2`。
- 实际复跑 F02+F01 为 `30 passed, 1 skipped`，非模型为 `30 passed, 1 deselected`；F02 原 5 个探针与 F01 原 7 个探针全部通过。compileall、模块帮助/版本、控制台版本均退出 0。本机记录在 `local/verification/F02/publication-review-20261105/`。
- F02 维持 accepted，F03 维持 ready。F06 的配置和独立冒烟记录可读取，真实语义用例仍为 not_run。F09 逻辑路径修正与公共契约一致。

### P-001 F01/F02 开发快照与后续检查计划

- 本轮明确要求尽快提交 GitHub，发布范围调整为当前已验收的 F01/F02、合成测试、设计文档和后续检查计划。README 已标明开发快照及 audit/diagnose 当前的入口状态。
- 后续交付统一从 `docs/implementation-checklist.md` 进入，覆盖 F03–F10 的 108 个规格验收编号、F06 的 7 个语义案例各 3 次、模块回归、交接材料与完整工作流发布门槛。
- 阶段提交包括公开源码、测试、合成夹具、文档和项目元数据。`local/`、虚拟环境、模型权重/配置、原始验收日志和构建缓存按忽略规则保留在本机。
- 本次阶段提交和远端一致性由发布日志记录；完整工作流最终交付表继续保持未完成项。

## 下一阶段与完整工作流排期

以下为规划估计。假设同一实现模型持续推进、交接及时复核、本机模型环境顺利，剩余完整工作流按约 `5–10 个工作日` 安排。模型适配和联调返工可能延长，F06 验收后再依据实际速度更新估计。

| 阶段 | 工作与完成条件 |
| --- | --- |
| 下一项 F03 | 实现规则文件发现、作用范围、失效引用与命令检查；运行 F03-01～14 及 F01/F02 回归并交接 |
| F04–F05 | 接通登记绑定与生效决策、合成事件与加载回执、去重和复发计数，逐项验收 |
| F06–F07 | 准备并登记本机模型；真实完成 7 个语义案例各 3 次及人工复核；接通证据门槛与最终诊断 |
| F08–F10 | 完成受批准的最小 Diff、临时预检、自包含报告、audit/diagnose 串联、wheel 离线构建和安装、端到端验收 |
| 完整工作流发布 | F01–F10 均 accepted，基础测试、真实模型、CLI 和打包验收有有效记录，最终摘要与提交文件一致，公开内容检查通过，更新实际示例和 README，在已发布的开发快照上继续提交 |

## 最终交付记录

| 条件 | 当前结果 |
| --- | --- |
| F01–F09 全部 accepted | not_run |
| F10 CLI 和本地打包验收 | not_run |
| 全部基础测试 | not_run |
| 实际本地模型语义案例 | not_run |
| 最终代码和测试摘要一致 | not_run |
| 所有需改进审查项已关闭 | not_run |
| 最终代码公开内容检查 | not_run |
| 最终提交 SHA | pending |
| 远端发布/合并结果 | pending |

## 后续阶段

原生会话适配、后台触发、实际 Agent 行为重放、自动应用和同步需另立功能规格。当前任务按首版范围完成后，再决定这些工作。
