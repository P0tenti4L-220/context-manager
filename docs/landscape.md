# 同类工具与项目定位

2026-11-04 查阅官方文档、仓库说明和四个项目的工作流源码。能力概览来自上游公开材料，具体分工以代码和 Skill 入口为准。本次采用静态检查。

| 参考 | 公开材料中与本项目最相关的能力 | 对设计的启发 |
| --- | --- | --- |
| [claude-md-doctor](https://github.com/agent-clinic/claude-md-doctor) | 规则静态体检、历史会话回测、根因分流及约束建议，报告提供证据 | 和「重复错误溯源」重叠最大。借鉴从可检查的违规动作回查规则的方式；分别标注规则确实过时、疑似参与历史行为、真正复现三类结论。 |
| [context-doctor](https://github.com/DheerG/context-doctor) | 以异常行为为入口，只读检查冲突、过期和作用范围；区分文件发现与加载证据；提供 Claude Code 与 Codex 入口 | 跨工具只读诊断已有先例。借鉴范围图与隔离实验，重点设计如何确认正式决策、约束修改权限以及留存修复证据。 |
| [aacc-substrate-audit](https://github.com/andrewjpyle/aacc-substrate-audit) | 检查项目记忆、任务、指令等材料中的冲突和过期信息；只读分析并标注覆盖缺口 | 检查失败或材料缺失时要报未覆盖，避免出具虚假的「无问题」结论。 |
| [claude-md-audit](https://github.com/vr1e/claude-md-audit) | 核对规则文件中的代码事实，精简可从仓库推导的描述，迁移局部规则 | 借鉴整理建议，但要区分可核对的实现事实与经确认的规范要求。 |
| [rulesync](https://github.com/dyoshikawa/rulesync) | 为 Codex、Claude Code 等工具生成配置，管理共享规则 | 可作为后续发布层。同步之前需要先确定该同步什么、适用于哪一类任务。 |
| [claude-mem](https://github.com/thedotmack/claude-mem) / [agentlint](https://github.com/agentlint/agentlint) | 分别偏向会话上下文持久化和项目就绪度体检 | 可参考会话采集和静态检查；首版不增加常驻记忆服务或就绪度打分。 |
| [Claude Code `/doctor prompt-audit`](https://code.claude.com/docs/en/memory#audit-your-instruction-files) | 文档称自 v2.1.283 起可查找过时引用和矛盾指令、提出编辑建议 | 作为静态体检的官方基线。[Issue #99288](https://github.com/anthropics/claude-code/issues/99288) 报告一次未获确认就写入及判错权威来源的情况；这是单次公开报告，未独立复现。项目的写入边界由程序强制。 |

[Codex 的指令发现说明](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)和 [Claude Code 的项目指令说明](https://code.claude.com/docs/en/memory)分别作为适配器的起点。静态规则用来推断预期加载；实际加载结论另需与会话和版本关联的证据。

## 源码里的分工

阶段是工作流中的步骤。Agent 数按实现显式调用的模型执行角色来计，宿主 Agent 单独注明。一次角色调用可能包含多轮工具使用。

| 项目 | 阶段与 Agent | 模型负责 | 代码执行或约束 |
| --- | --- | --- | --- |
| `claude-md-doctor` | 常规体检是 Stage 0–6 共 7 阶段，另有无规则文件时的起草模式。由 1 个宿主 Agent 运行 Skill，主流程里没有单独调度子 Agent。 | 拆解规则，定义触发/违规模板，抽查回测命中，核实复杂声明并做诊断。 | Python 查路径、导入、`package.json` 脚本和 Make 目标，整理历史记录，按匹配器计算规则命中，生成报告并标出缺跑阶段。可生成 Hook 与配置供人审阅安装。规则回显、压缩和会话深度属于回测中的代理信号。 [Skill 工作流](https://github.com/agent-clinic/claude-md-doctor/blob/main/skills/claude-md-doctor/SKILL.md#L96) · [引用检查](https://github.com/agent-clinic/claude-md-doctor/blob/main/skills/claude-md-doctor/scripts/refcheck.py#L71) · [回测归类](https://github.com/agent-clinic/claude-md-doctor/blob/main/skills/claude-md-doctor/scripts/backtest.py#L83) · [约束生成](https://github.com/agent-clinic/claude-md-doctor/blob/main/skills/claude-md-doctor/scripts/compile.py#L147) · [报告覆盖检查](https://github.com/agent-clinic/claude-md-doctor/blob/main/skills/claude-md-doctor/scripts/report.py#L592) |
| `aacc-substrate-audit` | 4 阶段。子 Agent 数是 `N + 2` 或 `N + 3`，其中 `N` 是并行阅读组数。演示报告的 8 个是一次运行的规模，宿主 Skill 另计。 | Haiku 做盘点，Sonnet 分组查冲突与过期条目，Haiku 按需通过 Bash 核对剩余路径，Opus 汇总提案。 | JS 分组、过滤、去重，预先解析确定的记忆链接，记录失败和覆盖率。子 Agent 统一使用 `Explore` 类型；外层 Skill 有 `Write` 工具用于保存报告，改原文件须按 Skill 流程单独批准。 [盘点与并行流程](https://github.com/andrewjpyle/aacc-substrate-audit/blob/main/substrate_audit_workflow.js#L205) · [引用检查与覆盖报告](https://github.com/andrewjpyle/aacc-substrate-audit/blob/main/substrate_audit_workflow.js#L276) · [外层 Skill](https://github.com/andrewjpyle/aacc-substrate-audit/blob/main/SKILL.md#L44) · [上游演示规模](https://github.com/andrewjpyle/aacc-substrate-audit#faq) |
| `context-doctor` | 2 个主要执行部分。Node 盘点命令和由 1 个宿主 Agent 执行的诊断 Skill，流程中没有独立子 Agent 调度。 | 从异常行为出发，有选择地读材料，判断语义冲突、作用范围和可能的干扰，提出隔离实验。 | Node 枚举文件和配置元数据，限制遍历范围，查找同名 Skill 和命令，统一把发现的文件标为 `candidate`。诊断阶段的只读约定写在 Skill 里；盘点脚本的扫描边界与宿主自身的权限是两层机制。 [盘点实现](https://github.com/DheerG/context-doctor/blob/main/plugins/context-doctor/skills/diagnose-agent-context/scripts/context-inventory.mjs#L177) · [诊断 Skill](https://github.com/DheerG/context-doctor/blob/main/plugins/context-doctor/skills/diagnose-agent-context/SKILL.md#L10) |
| `claude-md-audit` | 5 阶段，分别发现、核验、分类、报告和批准后应用。1 个宿主 Agent 执行 Skill。 | 逐条核验项目事实，判断删、留、改、迁移及范围，生成报告并在批准后编辑。 | `find`、`grep` 等命令提供可核查结果。Skill 同时声明 `Edit`、`Write` 工具，先审批后应用由 Skill 流程和宿主权限处理。可机械检查的规则建议迁移到 Lint、Hook 或 CI。 [五阶段与工具声明](https://github.com/vr1e/claude-md-audit/blob/main/skills/agents-md-audit/SKILL.md#L50) · [应用步骤](https://github.com/vr1e/claude-md-audit/blob/main/skills/agents-md-audit/SKILL.md#L160) |

## 对 Context Manager 的取舍

语义分析保留一个角色，负责找出自然语言的矛盾和例外。路径、已记录的加载状态、版本、覆盖率和补丁边界由程序直接检查。借鉴 `claude-md-doctor` 的分工和 `context-doctor` 的候选状态，模型可以提议「这条规则可能影响了行为」，程序按实际记录决定结论能否升级。仅发现同名文件或观察到规则被提及，只能作为线索。

借鉴 AACC 的程序生成覆盖记录，扫描缺项、模型未运行、加载证据缺失都会在报告中可见。相同的失效引用检查交给 Python，省去单独调用路径检查 Agent。把 `claude-md-audit` 的逐条删留判断纳入语义候选，补丁由独立代码检查路径、版本和改动范围。

项目以开发者已确认的需求和决策为依据，分别回答规则当前是否过时、历史错误可能如何发生、修复应落在哪里。具体设计见[模块分工](design.md#3-运行架构)。
