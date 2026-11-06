# 执行计划与当前状态

本文件是实现与审查的统一记录。接手后先阅读本文件，再核对当前功能规格。总体设计版本 `0.2`，公共契约维持 JSON v1，最后更新 `2026-11-06`。

## 当前状态

| 项目 | 当前值 |
| --- | --- |
| 当前阶段 | F01–F10 首版在 v28 源码上完成本机验收 |
| 专项设计交接 | 十份交接已收齐，保存在 `local/design-handoffs/` |
| 复核材料 | `local/handoffs/NEXT.md`、各功能交接、当前源码摘要、原始测试日志和模型输出 |
| 运行时架构 | 1 个只读 Context Analyst + Inspector、Evidence Store、Diagnostic Policy、Repair Planner |
| 专项设计负责人 | 每个功能一个设计组，均已交接 |
| 实现负责人 | 本聊天复核当前源码并补齐缺口，保留各功能历史交接 |
| 设计与代码复核 | 本聊天，收到功能交接后对照记录和实际代码审查 |
| 当前实现 | audit/diagnose 包含发现、确认决策、合成事件、语义循环、加载与版本核验、批准 Diff 和报告。见 [功能状态](docs/feature-status.md) |
| 当前 Git | v28 首版发布目标为 GitHub `main`。本机交接记录提交和推送结果 |
| 公开仓库 | [P0tenti4L-220/context-manager](https://github.com/P0tenti4L-220/context-manager) |
| 功能测试 | v28 全机械 283 passed、14 deselected，模型用例另行执行 |
| 真实本地模型测试 | v28 清理审计 5/5、七组语义案例 21/21、F01 模型配置与 F10-06 通过 |
| 文档检查 | F06/F07 规格和接口已对齐，当前验收结论见 R-012 |
| 交付 | 本地工作区、验收与复跑报告、可运行合成案例和 v28 离线 wheel |

## 设计入口

- [总体方案](docs/design.md) 说明产品目标、架构和功能顺序。
- [公共契约 r3](docs/contracts.md) 约定所有组件的字段、状态、证据和输出。
- [跨功能接口矩阵及变更影响](docs/interfaces.md) 固定调用方式、责任和验收映射。
- [本地验收规范](docs/acceptance.md) 约定实际测试、源摘要和审查方式。
- [F03–F10 统一实现与检查计划](docs/implementation-checklist.md) 汇总后续交付、命令、交接和完整工作流发布门槛。
- [F03–F10 本轮详细执行方案](docs/f03-f10-execution-plan.md) 列出 P01–P10 的实际代码改动、完成条件和结果。
- [功能实现与收尾缺口](docs/feature-status.md) 区分此前未实现、本轮补齐、效果待改进与尚未运行的检查。
- [功能设计子 Agent 分工](docs/agent-assignments.md) 列出十个可同时派发的设计任务和交接方式。
- [同类项目源码分析](docs/landscape.md) 保存架构依据。

## 功能计划

每个设计组负责一份规格，设计工作可以并行。实现模型按确认后的规格实现并运行本地测试，依赖齐备后联调与正式验收。表中的实现状态和测试结果以实际交接为准。

| ID | 功能规格 | 直接依赖 | 专项设计 | 实现状态 | 实施者 | 本地测试 | 审查 | 最近运行记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F01 | [工程入口与公共契约](docs/features/F01-foundation.md) | 无 | 已确认 | accepted | 本聊天 / H03 | F01-01～12 与模型配置探针 passed | v28 入口与契约复验通过 | R-012 |
| F02 | [证据快照与受限读取](docs/features/F02-evidence.md) | F01 | 已确认 | accepted | 本聊天 / H03 | F02-01～11 passed | v28 机械回归和快照重开通过 | R-012 |
| F03 | [发现、范围与引用检查](docs/features/F03-inspection.md) | F02 | 已确认 | accepted | 本聊天 / H03 | F03-01～14 passed | v28 机械回归通过 | R-012 |
| F04 | [规则与决策](docs/features/F04-rules-decisions.md) | F02、F03 | 已确认 | accepted | 本聊天 / H03 | F04-01～11 passed | v28 机械回归和决策范围通过 | R-012 |
| F05 | [事件与加载回执](docs/features/F05-incidents.md) | F02、F04 | 已确认 | accepted | 本聊天 / H03 | F05-01～15 passed | v28 机械回归通过 | R-012 |
| F06 | [本地语义 Agent](docs/features/F06-analyst.md) | F02、F04、F05 | 已确认 | accepted | 本聊天 / H06 | F06-01～13、五组审计、七组各三次 passed | v28 逐份复核引用、清理与候选范围 | R-012 |
| F07 | [证据核验与诊断](docs/features/F07-diagnosis.md) | F03–F06 | 已确认 | accepted | 本聊天 / H06 | F07-01～18 与真实 CLI 联调 passed | 已核对迁移理由、假设等级与确定性证据 | R-012 |
| F08 | [修复 Diff 预检](docs/features/F08-repair.md) | F04、F07 | 已确认 | accepted | 本聊天 / H05 | F08-01～12、迁移建议及批准 Diff passed | 迁移需确认，批准预览保留独立义务 | R-012 |
| F09 | [报告与运行产物](docs/features/F09-reporting.md) | F02、F07、F08 | 已确认 | accepted | 本聊天 / H04 | F09-01～12、六份正式模型报告 passed | 引用、追踪、清单与重开复核通过 | R-012 |
| F10 | [CLI 集成与最终交付](docs/features/F10-integration.md) | F01–F09 | 已确认 | accepted | 本聊天 / H06 | F10-01～13、离线双入口 passed | v28 正式模型、同源安装与完整合成诊断通过 | R-012 |

## 状态更新规则

专项设计按 `待领取 → 设计中 → 待复核 → 已确认` 记录。设计者提交 `local/design-handoffs/Fxx.md`，由本聊天核对并更新该列。下面的状态用于实现与测试。

| 状态 | 进入条件 | 允许的下一步 |
| --- | --- | --- |
| planned | 专项设计待确认或依赖尚未审查通过 | 准备夹具和环境 |
| ready | 专项设计已确认，依赖均 accepted | 登记实施者后开始 |
| in_progress | 正在实现或修复自测失败 | 写代码并执行本地测试 |
| implemented_pending_review | 按用户功能优先指令，主流程代码已接通并做必要运行检查；专项验收后补 | 改进已观察问题，补专项检查，之后 ready_for_review；不等同 accepted |
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

### R-007 功能优先补齐与正式主链检查

- 日期为 `2026-11-05`。按用户最新要求，优先补齐现有正式功能，不另建体验版，完整专项测试留待后续，并停止 GitHub 操作。实施阶段提前联调，未改变 accepted 的含义。
- 实际检查此前仅有 F01/F02 源码，合法 audit/diagnose 均直接返回 not_implemented。F03–F10 的主要问题是未实现，而非单纯测试未跑。
- 已新增 Inspector、规则块与登记绑定、生效策略、合成事件与回执、AST/结构化行为检查、独立任务计数、本地传输与只读循环、证据诊断、精确批准与临时 Diff、报告和正式管线。CLI 已替换占位分支；默认 off 不构造传输；出现运行阶段异常时写 failed 报告。
- 本机现有非模型检查为 `39 passed, 1 deselected`，含 9 项工作流检查；批准 Diff 实际在临时仓库检查并应用，源项目字节不变。wheel 离线构建、包内提示词、独立环境安装、安装后的模块 audit 与控制台 diagnose 均有实际退出 0 记录。最初构建缺少 wheel，已补齐构建依赖后重跑通过。
- 正式本机模型冒烟完成 3 次调用（list/read/submit），最新一次循环 complete/0、约 31 秒；保留义务仍被列为 cleanup_candidate/keep，引用有效但属于候选噪声。语义质量未验收通过。最早的传输失败和候选拒绝均保留记录，不能隐去后只报成功。
- 原始材料位于 `local/verification/feature-completion-20261105/`，包含命令、退出码、日志、源码摘要、wheel 与模型运行目录。F03–F10 交接分别位于 `local/handoffs/Fxx.md`。
- 增加 `inspection.json` 与报告规则清单，使 F03 的结构化结果在正式 CLI 可见；F05 额外接受显式 `exception_rule_ids`，仅通过已确认规则范围解析例外，不从目录名猜测。
- F03–F10 标记 implemented_pending_review。108 个专项验收编号、7 个语义案例各 3 次和完整独立复核仍未完成，不能全部标 accepted。详见 [功能状态](docs/feature-status.md)。

### R-008 F03–F10 详细方案与代码补强

- 日期为 `2026-11-05`。已写入 [P01–P10 执行方案](docs/f03-f10-execution-plan.md)，按用户指令继续完善现有正式模块，不处理 GitHub。
- 深层封存公开组件结果、F04 缓存和开放 payload；分离规则固有范围与任务排除，统一 profile 校验，修正 frontmatter、导入深度及引用。登记绑定按条目原子完成，主体、条件、标量类型、时点及已被替代的决策均进入权威门槛。
- F05 检测结果绑定实际动作 after 快照与 path；F07 复核真实发现、历史策略、加载回执、行为与分组，并用逻辑证据生成稳定 ID；F08 重新核对未替代义务、BOM/换行与多编辑整体状态；F09 验证快照、Diff 和 trace，排他逐文件落盘，写失败保留 incomplete。
- F10 预检保留末级输入路径供 F02 拒绝链接，JSON 预读不阻塞 FIFO。当前 F01 源码发生共享行为变化，基础回归虽通过，历史 accepted 不能代替当前字节的重新复核。F02 源码未改，相关回归通过。
- 实际新增 24 个缺陷回归；全体现有非模型结果 `63 passed, 1 deselected`。compileall、diff --check、离线 wheel、独立 venv、两个安装入口、安装与源码 audit 等价、报告清单和目标项目只读均通过。最终命令及摘要在 `local/verification/f03-f10-hardening-20261105/delivery-recorded/`。
- 真实本机复跑还发现未读空提交，以及全局已读覆盖可能混入 Inspector 读取；已补本次模型读取门槛和回归。提示词 v3 下临时进度正例完成读取并形成 relocate/hypothesis；有效义务负例实际读取后因候选 evidence_id 契约错误返回 partial，无最终问题。这是已观察到的输出稳定性缺口，不能只列“测试没跑”。语义材料绑定同轮 delivery-final 清单，之后代码仅补候选拒绝 trace。
- 先前沙箱传输失败、提示词 v2 的未读空结果/keep 正例与 v3 的负例 partial 均保留本机记录；完整 108 项矩阵、7×3 语义案例和独立符合性复核未完成。F03–F10 仍为 implemented_pending_review，详见[功能状态](docs/feature-status.md)。H02 追加到各功能交接，不覆写 H01。

### R-009 F01–F10 规格检查与上一版模型验收

- 日期为 `2026-11-06`。复核正式源码、功能规格与合成输入，补齐专项测试。十个 H03 交接已写入 `local/handoffs/`，保留原交接。机械覆盖对应 130 个规格编号，剩余编号 F10-06 由真实模型测试执行。
- F01/F02 复跑入口、公共契约、受限路径、快照破坏和重开。新存储用项目根摘要绑定。报告中的快照可独立重开，继续捕获时显式提供原项目根。
- F03/F04 修正静态链接与 profile 顺序，隔离有环或悬空的替代授权。F05 核对独立任务、重复、合法例外及回执。F07 补齐由已确认任务版本约束支持的代码快照对照，区分加载未知、事实与因果假设。
- F06 保持单个只读 Agent。体检与诊断选择相应提示词。修正严格 JSON 的纠正消息和读取字节预算。历史示例以及缺少独立编辑证据的复发候选进入筛除记录。模型候选的事实等级仍由 F07 控制。
- F08/F09 核对原样保留义务、换行、重叠编辑、并发和完整性降级。正式合成案例 `complete/0`，报告支持当前过时规则和两个独立任务的复发。三条事件归为两条规范事件与一次重试，Diff 仅改已批准的路由行。旧示例实际 `partial/3`。
- 当时机械测试为 `280 passed, 9 deselected`，退出 0。9 个本地模型标记项单独执行。日志和 60 个文件的摘要清单保存在 `local/verification/f01-f10-final-20261105/`，清单 SHA-256 为 `2b706d4d0e11bd951ea96b9811123f7765eca3884bbbb50bf730775663459008`。`mechanical-matrix.json` 关联测试编号和实际断言。
- wheel 已离线构建并安装。源码、安装后的模块入口与控制台入口均运行同一合成 diagnose，退出 0。以逻辑路径核对证据后，发现、覆盖、Diff 字节和事件计数相同。详细命令在同目录的 `commands.jsonl`，wheel 在 `packaging/`。
- v11 下七组固定合成案例和 F10-06 的 pytest 子命令为 `8 passed, 18 deselected`。21 次达到最终候选断言，引用和源项目字节复核通过。正式 CLI 实跑 `complete/0`，模型候选为 `hypothesis`。F01 模型配置探针另行通过。组合命令继续执行规则清理探针，两项失败，因此组合退出 1。
- 早期连接中断、非 JSON 动作及错误分类保留原始日志。v11 模型将历史示例和同任务重试分别误标三次，机械门槛拦截全部六项。`semantic-review.md` 记录逐份复核及各输出目录。复合规则模型候选建议更新且引用完整，精确改法由 F08 的授权正例验证。

### R-010 当前工作区接手记录

- 日期为 `2026-11-06`。按用户要求在当前实现收尾，v14 接手顺序、复现命令、源码和原始证据见 `local/handoffs/NEXT-v14.md`。
- 当前 v14 机械回归 `280 passed, 9 deselected`，退出 0。61 个文件摘要清单的 SHA-256 为 `f80a58d418e763f87986dc295ebd7dee21fc059b4974e2eee41e4232e4a5d5ec`。原始日志为 `local/verification/f01-f10-final-20261105/20261106T064205993968Z-mechanical-v14.log`，对应 `-tested-files.json` 记录全部条目。
- 当前离线 wheel 已重新构建和独立安装。源代码、安装后模块和控制台执行同一合成诊断均得到 `complete/0`，发现、覆盖、Diff 字节和事件计数一致。记录见同目录 `20261106T064606585317Z-handoff-installed-final.log`，语法编译退出 0。
- F06 无决策的体检使用单独的 `instruction_review.md`，与有决策体检和诊断共用一个只读 Agent。v14 的两个清理探针均完成工具循环，但负例错误建议迁移两条有效义务，正例的临时进度候选逐字复述且没有理由，被质量门槛筛除。组合退出 1，原始输出见 `20261106T064206791458Z-cleanup-v14.log` 及 `cleanup-20261106T064207Z/`。F06 保持 `changes_requested`。
- F07 显式记录关闭语义时拒绝模型候选，F08 把有效迁移建议映射为 `migrate_clause`。两项机械回归已通过。修好 F06 后用正式 CLI 验证 `relocate`、迁移建议和原项目只读，再按最终源码复跑 7×3 和 F10-06。F10 机械与安装验收已有证据，完整交付仍待复核。

### R-011 F06 整改与当前交接

- 日期为 `2026-11-06`。本条记录 v23，原交接见 `local/handoffs/NEXT-v23.md`。F06 将无决策的审阅材料限到规则文件和文档快照，以绝对行号展示已读正文。模型先分类，程序映射到既有候选种类与动作。迁移候选需提供理由，F07 将理由并入报告和建议摘要。
- v23 定向机械 `63 passed, 3 deselected`，退出 0。五组正式本机模型审计 `1 passed, 4 failed`，退出 1。五次 CLI 都为 `complete/0`，快照引用检查通过，项目字节不变。真冲突通过，有效义务和历史示例误报，重复误给迁移动作，临时进度已产生 `relocate/hypothesis` 和 `migrate_clause`，claim 缺少具体任务编号。
- 63 个源码、提示词、测试和夹具的摘要与当前工作树匹配。清单 SHA-256 为 `c46f0521cd58666b18b7b254a63d0ac20d21af242d97a97217ee3a5ea533a35d`。原始记录位于 `local/verification/f01-f10-final-20261105/`，分别为 `20261106T134947870952Z-handoff-v23-runner.log` 和 `20261106T072837986589Z-cleanup-audit-v23.log`。逐份报告、原始回复和复跑命令由接手报告索引。
- F06 仍为 `changes_requested`。F07 的迁移理由投影及 F08/F09 下游产物列为 `needs_recheck`。v23 全机械、完整 7×3、F01 模型配置探针、F10-06 和重新打包安装为 `not_run`。历史通过和失败记录继续保留。

### R-012 v28 首版本机验收

- 日期为 `2026-11-06`。无决策体检由同一个只读 Analyst 分别提取任务记录与比较现行规则。文档及区块编号都回绑到已读快照。迁移声明保留 `TASK-42`、已关闭和待发审查链接，附归档理由。重复和冲突引用两条规则并待确认。有效义务和历史示例通过保留断言。
- 有决策体检以规则和决策原文为审阅对象，登记提供结构化状态和范围。复合规则三次候选均写明只替换路由，保留测试和 `legacy/`。公共契约维持 JSON v1，F06/F07 规格、接口和对应验收已对齐。
- 全机械 **283 passed、14 deselected**，130 项规格机械编号有本次断言。真实模型清理审计 **5 passed**，七组语义各运行三次，**21/21** 通过并逐份复核。F01 配置探针和 F10-06 **2 passed**。重试负例三次原始误标由独立编辑证据门槛筛除。
- F10-06 以 `complete/0` 结束。模型假设引用实际读取的旧规则，确定性发现同时引用旧规则与批准决策。已批准 Diff 仅替换路由并在临时副本应用，保留测试。离线 wheel 的模块、控制台和源码入口生成等价发现、覆盖、Diff 和事件计数。
- 上述验收和语法编译均以 0 退出，绑定同一 64 文件清单，SHA-256 `595adcc27e11f9806728fc21bd16f6ccbe6325f8bcb8354d4f99a7d664840669`。复核者逐项核对当前字节、命令与原始产物。v23–v27 的失败及探针保留在原始记录。
- 审查结论为 F06–F10 `accepted`，F01–F05 复验通过并维持 `accepted`。交付范围为本地 CLI、合成输入和当前本机模型。具体命令、输出、源码清单和 wheel 见 `local/handoffs/NEXT.md` 与 `local/verification/f01-f10-final-20261105/review-v28.md`。

## 最终交付记录

| 条件 | 当前结果 |
| --- | --- |
| F01–F09 功能复核 | v28 `accepted`，专项与受影响链路均已复验 |
| F10 CLI 和本地打包 | F10-06、离线构建安装、源码及安装后双入口等价检查通过 |
| 基础、专项与工作流检查 | v28 283 passed、14 deselected。130 个机械编号有实际断言 |
| 实际本地模型语义案例 | 五组清理审计、七组各三次、F01 配置和 F10-06 均通过 |
| 测试与源码摘要 | 原始日志、编号映射、逐份复核与 64 文件摘要已保存 |
| 交接位置 | 本地工作区，入口为 `local/handoffs/NEXT.md` |

## 后续阶段

原生会话适配、后台触发、实际 Agent 行为重放、自动应用和同步需另立功能规格。当前任务按首版范围完成后，再决定这些工作。
