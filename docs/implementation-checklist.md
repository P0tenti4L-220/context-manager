# F03–F10 实现与检查计划

本计划是后续实现模型和审查者的统一入口，更新日期为 `2026-11-05`。公共形状以[契约 r3](contracts.md)为准，组件调用以[接口矩阵](interfaces.md)为准，实际进度由[执行计划](../EXECUTION_PLAN.md)登记。

## 当前基线与交付范围

F01–F10 已接入正式工作流。这里保留专项复跑命令和发布门槛。当前测试和待办以 [功能状态](feature-status.md) 与 [执行记录](../EXECUTION_PLAN.md) 为准。

## 实现顺序与检查门槛

F03–F10 的规格列出 108 个编号。其中 F10-06 使用真实模型，F06 另有七组案例，每组执行三次。历史实施过程记录于 [详细执行方案](f03-f10-execution-plan.md)。

| 功能与直接依赖 | 实现交付 | 必须验证的行为 | 验收编号与规格 |
| --- | --- | --- | --- |
| F03，依赖 F02 | inspector、内置 harness profile、可编辑规则文件清单与覆盖项 | 祖先规则和导入发现；组件级 scope；`.claude/rules` 受限 paths 解析；静态引用/命令检查；截断和不可读降级；空扫描 passed/none_found；目标树字节不变 | F03-01～14，[F03](features/F03-inspection.md) |
| F04，依赖 F02/F03 | 规则块提取、登记解析/绑定、PolicyState、比较候选和 edit_targets | SourceLocator 绑定本次快照；确认、有效期和 scope；草稿/未来/撤回；直接替代、环与冲突；复合义务保留；具体改法批准；空登记及坏条目隔离 | F04-01～11，[F04](features/F04-rules-decisions.md) |
| F05，依赖 F02/F04 | 事件包解析、归一化、去重、回执/行为观察、复发分组和 coverage_items | 同包多份历史材料；独立任务计数；重试/提及/合法兼容例外；完整与部分回执；空清单须有完整覆盖证明；缺材料 unknown；旧格式保留线索与迁移提示 | F05-01～15，[F05](features/F05-incidents.md) |
| F06，依赖 F02/F04/F05 | 本地传输、只读工具循环、提示词包资源、候选与 trace | 单层 JSON 围栏按规格解析；坏 JSON 有界重试；越权动作拒绝；四类预算；引用只能来自已展示行段；传输失败保留 partial；关闭语义时零调用 | F06-01～13 及真实案例，[F06](features/F06-analyst.md) |
| F07，依赖 F03–F06 | 证据门槛、CandidateValidation、最终 Finding 和诊断覆盖 | 逐 kind 门槛；模型候选等级上限；事实与因果独立；加载证明与违规检查绑定；独立任务复发；反证和合法例外；空候选 passed/no_candidates | F07-01～18，[F07](features/F07-diagnosis.md) |
| F08，依赖 F04/F07 | RepairPlan、批准匹配、最小 Diff 与临时预检 | 白名单和行段摘要；完整批准才 preview_ready；测试/例外保留；删除门槛；漂移、重叠、过期与摘要不匹配；Git 预检与实应用；目标树不变 | F08-01～12，[F08](features/F08-repair.md) |
| F09，依赖 F02/F07/F08 | 自包含报告、JSON、原始 Diff、trace 和 incidents 产物 | 同次快照链接可复核；移走原项目仍可阅读；off 豁免；缺材料和 trace 降级；原子写与并发；修复检查只影响提案；安全渲染和来源标记 | F09-01～12，[F09](features/F09-reporting.md) |
| F10，依赖 F01–F09 accepted | 正式 audit/diagnose 管线、CLI 状态、安装包、实际示例 | 正式入口使用真实组件；预检零副作用；完整/缺回执/草稿/兼容/旧格式；模型关闭与不可用；并发和确定性；源树只读；wheel 安装后的行为一致 | F10-01～13，[F10](features/F10-integration.md) |

实现者先领取单项，阅读其直接依赖的接口。可以提前准备下游合成夹具与用例，正式联调和 accepted 按依赖门槛推进。共享源码、提示词、配置或契约发生行为变化时，相关 accepted 功能记 needs_recheck，并复跑受影响回归。

## 命令清单

以下命令用于复跑专项测试。实际执行、退出码、日志和源码清单由执行记录关联。

| 领取功能 | 专项与依赖回归命令 |
| --- | --- |
| F03 | `rtk proxy .venv/bin/python -m pytest tests/test_f03_inspection.py tests/test_f02_evidence.py tests/test_f01_foundation.py -q` |
| F04 | `rtk proxy .venv/bin/python -m pytest tests/test_f04_decisions.py tests/test_f03_inspection.py tests/test_f02_evidence.py tests/test_f01_foundation.py -q` |
| F05 | `rtk proxy .venv/bin/python -m pytest tests/test_f05_incidents.py tests/test_f04_decisions.py tests/test_f02_evidence.py tests/test_f01_foundation.py -q` |
| F06 运行器 | `rtk proxy .venv/bin/python -m pytest tests/test_f06_analyst.py tests/test_f02_evidence.py tests/test_f01_foundation.py -q` |
| F06 真实模型 | `rtk proxy .venv/bin/python -m pytest tests/test_f06_local_model.py -m local_model --local-model-config local/model.toml -q` |
| F07 | `rtk proxy .venv/bin/python -m pytest tests/test_f07_diagnosis.py tests/test_f04_decisions.py tests/test_f05_incidents.py tests/test_f06_analyst.py -q` |
| F08 | `rtk proxy .venv/bin/python -m pytest tests/test_f08_repair.py tests/test_f07_diagnosis.py -q` |
| F09 | `rtk proxy .venv/bin/python -m pytest tests/test_f09_reporting.py tests/test_f08_repair.py -q` |
| F10 | `rtk proxy .venv/bin/python -m pytest tests/test_f10_cli.py tests/test_f10_packaging.py -q` |

每项交接前还要运行语法检查。进入联调后，运行全部已存在的非模型测试。F06 和 F10 保留各自的真实模型门槛。

```sh
rtk proxy .venv/bin/python -m compileall -q src/context_manager
rtk proxy .venv/bin/python -m pytest tests -m 'not local_model' -q
rtk proxy git diff --check
```

## F06 真实语义检查

目前准备的是 Ollama 与 `qwen2.5:7b-instruct` Q4_K_M。模型服务、配置、日志和权重保存在 Git 忽略的 `local/`。独立分类冒烟只说明该次服务请求可用。进入 F06 时，实施者先确认服务与配置，再通过正式运行器执行以下案例。

| 案例 | 要验证的行为 |
| --- | --- |
| api-wording | 关联不同措辞的旧规则和新决策，引用与范围正确 |
| compound-rule | 提出修改时保留测试义务和兼容例外 |
| doc-example | 将文档示例与实际动作区分，避免虚构违规 |
| draft-decision | 保留未决需求，明确缺少确认 |
| log-wording | 关联同类行为线索，独立任务计数交由 F05 |
| retry-mention | 保留重试与提及区别，避免夸大复发 |
| missing-receipt | 回执仅有 ID 时说明缺口，加载保持未知 |

每例固定配置运行 3 次，保存全部输出并逐次人工复核，采用 F06 规格中的 2/3 正例与 3/3 负例门槛。运行器测试、真实模型案例、人工复核分别记录。模型响应出现 JSON 围栏时走正式解析分支，禁止在测试中预先替模型清理响应。记录模型、量化、运行器版本、温度、超时与提示词摘要。

## 交接与独立检查

每项交接写入 `local/handoffs/Fxx.md`，原始结果保存到 `local/verification/Fxx/<run>/`。真实日志、模型配置及绝对本机路径只保留在本机。以下内容必须齐备。

- 实现入口和规格编号的对应关系、实际运行的正负例、设计偏差与待裁决问题。
- 原始日志、命令、退出码、passed/failed/skipped/deselected 数量以及跳过原因。
- 实现、测试、合成夹具、提示词和配置的 tested-files.json，与清单自身的 SHA-256。
- 对目标项目的前后文件集合和摘要检查，失败、截断、未知和 not_run 的机器可读记录。
- 与已 accepted 功能的影响和回归范围。修复后的新源码使用新清单，旧交接和失败日志保留。

审查者核对清单与当前字节，复跑功能与依赖用例，再选取影响用户目标的独立负例。F02 的整改模式需要在后续接口中持续回归，尤其检查同一事件包多份历史文件，以及路径检查后发生父目录替换。通过后登记 accepted；有偏差时登记 changes_requested 并给出具体位置和复现。

## 完整工作流发布门槛

- [ ] F03–F10 完成最终本地复核。F06 的规则清理语义质量仍待整改，F10 待整体验收。
- [x] 当前基础与专项机械测试通过。
- [ ] 规则清理的正负例通过，并用最终源码复跑 F06 七组各三次和 F10-06。v11 快照的正式套件通过已留档。
- [x] audit/diagnose 的 complete、partial、failed 和参数错误分别对应 0、3、1、2，JSON、报告及进程结果一致。
- [x] Diff 覆盖批准范围，实际预检前后原项目摘要一致。
- [x] wheel 离线构建、资源检查、临时 venv 安装与两个入口通过，安装后结果与源码运行一致。
- [x] README 和旧 API 示例依据真实 CLI 结果更新，保留旧格式与合成标记。
- [ ] 发布文件与最后一次测试清单一致，公开内容检查通过，记录最终提交与远端结果。

本轮不处理发布。完整工作流达到上述验收条件后，再按届时用户要求决定提交与发布。
