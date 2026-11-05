# Context Manager 开发协作

开始工作先读 `EXECUTION_PLAN.md`，按其中的当前功能进入 `docs/features/` 对应规格。公共数据契约在 `docs/contracts.md`，实际本地验收流程在 `docs/acceptance.md`。

设计组按 `docs/agent-assignments.md` 各自细化一个功能，设计交接写入 `local/design-handoffs/Fxx.md`。实现者完成对应功能后实际运行本地测试，把命令、退出码、日志位置、源文件摘要和偏差写入 `local/handoffs/Fxx.md`。本聊天负责对照代码复核、更新执行记录和最终功能汇总。

测试在本机 IDE 或终端实际执行。语义检查使用配置好的本地模型，机械测试使用合成数据。明确记录 `not_run`、失败和材料缺口。

测试数据放在 `tests/fixtures/`，真实日志及本机模型配置放在 Git 忽略的 `local/`。`examples/` 中的规则文件作为案例数据处理。
