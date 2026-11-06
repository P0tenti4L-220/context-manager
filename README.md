# Context Manager

给长期使用 Coding Agent 的项目做上下文体检：发现过时或冲突的项目规则，追查重复错误，并给出有证据、可审阅的修复建议。

例如，Agent 反复在新代码中使用 `/api/v1/users`。如果适用于当前任务的旧指令仍要求用 v1，就建议更新那条指令；如果 v2 规则正确却未加载，就检查发现路径或导入；如果能证明 v2 规则已加载且行为违背了它，就考虑加一条回归检查。现有代码可能还没迁移完，所以会结合已确认的项目决策判断，而不从代码现状反推需求。

| 项目材料 | 应回答的问题 |
| --- | --- |
| `AGENTS.md` / `CLAUDE.md` | Agent 长期应该遵守什么？如模块边界、代码规范和验收要求。 |
| 任务记录 | 现在做到哪里？下一步是什么？ |
| ADR / 决策记录 | 为什么这样做？哪些旧决定已被替代，何时生效？ |
| 代码、测试与 Git | 当前或当时实际实现了什么？变更发生在哪里？ |
| 会话证据 | Agent 实际做了什么？出错前，哪些指令有证据表明已送入上下文？ |

**正式 `audit` / `diagnose` 可运行。** 静态检查定位规则范围、失效引用和已确认的替代关系。诊断将实际动作、历史规则版本和加载回执关联起来，按独立任务识别复发。批准的最小 Diff 在临时副本中预检和应用。报告可以脱离原项目复核。

F01–F10 首版本机验收已完成。同一份 v28 源码通过 **283 项机械检查、5 组真实模型规则审计、7 组语义案例各 3 次**，F01 模型配置探针、F10-06 和离线安装双入口也已通过。验收使用合成输入和本机模型。[功能状态](docs/feature-status.md)

- [执行计划与当前状态](EXECUTION_PLAN.md)
- [功能实现与收尾缺口](docs/feature-status.md)
- [F03–F10 详细执行方案与完成台账](docs/f03-f10-execution-plan.md)
- [F03–F10 统一实现与检查计划](docs/implementation-checklist.md)
- [详细方案与 10 项功能规格](docs/design.md)
- [公共数据契约](docs/contracts.md)
- [跨功能接口矩阵](docs/interfaces.md)
- [本地测试与审查规范](docs/acceptance.md)
- [GitHub 同类工具与官方基线](docs/landscape.md)
- [合成案例与手写预期：重复使用旧 API](examples/stale-users-api/README.md)
- [可运行的合成验收案例](tests/fixtures/stale_users_api/README.md)

首版以本地 CLI 为入口。静态体检和证据校验由程序执行，语义审计可开启一个只读分析 Agent，其候选保持 `hypothesis` 或 `unknown`，不能自行确认决策或批准修改。事件诊断当前接收明确标记为合成的自包含日志包，真实会话适配另行规划。

## 安装和当前入口

使用 Python 3.11+，在仓库根目录执行。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/context-manager --help
.venv/bin/python -m context_manager --version
.venv/bin/python -m pytest tests -m 'not local_model' -q
```

## 正式使用

静态体检；输出根必须在被审计项目之外。`--scope` 可重复，检查任务范围的祖先规则、规则目录及显式导入，并不递归盘点整个仓库。

```sh
.venv/bin/context-manager audit --repo /path/to/project --project-id my-project \
  --scope clients --decisions /path/to/decisions.json --out /path/to/reports

.venv/bin/context-manager diagnose --repo /path/to/project --project-id my-project \
  --scope clients --decisions /path/to/decisions.json \
  --case /path/to/synthetic-case.json --out /path/to/reports
```

`--decisions` 的格式见 [F04](docs/features/F04-rules-decisions.md)，事件包见 [F05](docs/features/F05-incidents.md)。可以省略决策登记；这时确定性决策检查记录为未执行，整体返回 `partial/3`。有效空登记 `{"schema_version": 1}` 表示明确没有登记项，相关检查可以正常完成。

`--harness-profile FILE` 接收显式的版本化工具配置；省略时仍发现文件，但预计加载为未知。静态发现与加载预期均不等于实际加载。

启用本地模型时增加 `--semantic local --model-config local/model.toml`。只允许显式回环地址的本机兼容聊天接口，默认关闭时不建立模型连接。配置格式见 [F06](docs/features/F06-analyst.md)。

每次运行建立独立目录，包含 `report.md`、`run.json`、`findings.json`、`checks.json`、`inspection.json`、`proposals.json` 和原始证据快照；诊断另有 `incidents.json`，本地语义模式另有 `trace.jsonl`。只有批准、基准和临时应用检查全部满足时才生成 `proposal.diff`。报告可以脱离原项目阅读。

| 退出码 | 含义 |
| --- | --- |
| 0 | 请求的检查完成；仍可能有发现或待确认提案 |
| 3 | 至少一项请求检查因材料、读取、模型或预算问题未完成 |
| 2 | 参数或输入结构错误，建运行目录前拒绝 |
| 1 | 致命运行、写入或运行目录冲突 |

## 证据存储示例

下面的例子在临时目录创建合成规则，捕获并读取快照，展示当前已实现的 F02 入口。

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from context_manager.evidence import EvidenceStore

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    project = root / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Use the v2 API.\n", encoding="utf-8")

    store = EvidenceStore(project, root / "out", "readme-demo")
    try:
        evidence = store.capture_file("AGENTS.md", kind="rule_file")
        reference = store.make_ref(evidence, 1, 1)
        excerpt = store.read_evidence(reference)
        print(excerpt.text)
    finally:
        store.close()
```

## 验收材料

测试材料均为合成数据。真实日志、模型配置、权重和本机验收原始输出保存于 Git 忽略的 `local/`。
