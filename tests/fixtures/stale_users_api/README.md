# 旧 API 的合成验收案例

当前项目的客户端已用 v2。根规则仍要求新客户端用 v1，同时保留测试义务和旧客户端的兼容规则。登记中有一份生效决策和对单行改法的精确批准。

事件包记录了两个独立任务的 v1 编辑和一次同任务重试，包含动作快照和合成回执。诊断会把重试归到原任务。当前规则需要更新，回执仅证明各事件中实际出现的旧规则文本。

```sh
rtk proxy .venv/bin/python -m context_manager diagnose \
  --repo tests/fixtures/stale_users_api/project --project-id synthetic \
  --scope clients --as-of 2026-11-05T00:00:00Z \
  --decisions tests/fixtures/stale_users_api/decisions.json \
  --case tests/fixtures/stale_users_api/incidents.json \
  --out /tmp/context-manager-demo
```

预期退出 0。报告中有一项过时规则和一项复发事实，Diff 仅更新新客户端路由。兼容规则与测试义务原样保留。缺失材料、草稿和不匹配批准由专项测试在临时副本中构造。
