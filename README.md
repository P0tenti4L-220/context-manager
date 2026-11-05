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

**开发快照，F01/F02 已实现并验收，F03 可领取。** 当前包含工程入口、公共契约和证据存储。`audit`、`diagnose` 的业务调用当前返回 `not_implemented`（退出码 1），由 F10 接入完整管线。F03–F10 的实现与验收进度见下列计划。

本机复核为 **30 passed、1 skipped**，跳过项是未请求本机模型的配置门控；排除模型标记为 **30 passed、1 deselected**。F01 的 7 个和 F02 的 5 个独立探针通过。本机模型已完成独立分类冒烟，正式运行器与真实语义案例在 F06 验收。

- [执行计划与当前状态](EXECUTION_PLAN.md)
- [F03–F10 统一实现与检查计划](docs/implementation-checklist.md)
- [详细方案与 10 项功能规格](docs/design.md)
- [公共数据契约](docs/contracts.md)
- [跨功能接口矩阵](docs/interfaces.md)
- [本地测试与审查规范](docs/acceptance.md)
- [GitHub 同类工具与官方基线](docs/landscape.md)
- [合成案例与手写预期：重复使用旧 API](examples/stale-users-api/README.md)

首版以本地 CLI 为入口。静态体检和证据校验由程序执行，语义审计可开启一个只读分析 Agent，识别自然语言里的冲突和例外。先用合成日志开发，真实会话以后由用户明确选定并仅在本机读取。首版交付独立 Diff 供人审阅。

## 安装和当前入口

使用 Python 3.11+，在仓库根目录执行。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/context-manager --help
.venv/bin/python -m context_manager --version
.venv/bin/python -m pytest tests -m 'not local_model' -q
```

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

## 后续交付

F03–F05 完成发现、规则决策和事件材料，F06–F07 完成语义分析与证据诊断，F08–F10 完成 Diff、报告和正式 CLI。每项以实际本机测试、源码摘要和独立复核进入 accepted。完整工作流还须通过真实本机模型、端到端和 wheel 安装检查。

测试材料均为合成数据。真实日志、模型配置、权重和本机验收原始输出保存于 Git 忽略的 `local/`。
