# 旧格式案例的实际运行结果

2026-11-06 通过正式 CLI 运行。两条事件只保留为线索，加载状态为未知，诊断退出 3。完整报告和快照位于本机验收目录。下方展示副本用文件名标明产物。

# Context Manager report

Project: stale-users-api
Status: **partial** · exit code 3
Synthetic basis: synthetic
As of: 2026-11-05T00:00:00Z

## Scope and coverage

{&quot;include\_prefixes&quot;: \[&quot;clients&quot;\], &quot;exclude\_prefixes&quot;: \[\], &quot;harnesses&quot;: \[\]}

Checks: 13 passed, 0 failed, 10 not run.

## Instruction inventory

Only task ancestors, rule directories and explicit imports were inspected.

- AGENTS.md: definite; expected loading: unknown; actual loading is not established by discovery.
- CLAUDE.md: definite; expected loading: unknown; actual loading is not established by discovery.

References missing: 0; commands missing: 0.

Inventory, imports, scope and command details (`inspection.json`)

## Findings

No grounded findings were produced. See check coverage for unperformed or incomplete checks.
## Repair proposals


No preview diff: no_editable_findings.

## Incomplete checks and gaps

- f04.behavior_checks: not_run · registry\_not\_provided
- f04.constraint_comparison: not_run · registry\_not\_provided
- f04.registry: not_run · registry\_not\_provided
- f04.supersession: not_run · registry\_not\_provided
- f05.behaviors: not_run · legacy\_material\_gap
- f05.bundle: not_run · legacy\_material\_gap
- f05.loads: not_run · legacy\_material\_gap
- f05.receipts: not_run · legacy\_material\_gap
- f05.recurrence: not_run · legacy\_material\_gap
- f06.semantic: not_run · disabled\_by\_user
- coverage\_incomplete: coverage\_incomplete

Semantic analysis: not requested (未请求).

Incident counts: {&quot;canonical\_event\_count&quot;: 2, &quot;confirmed\_independent\_tasks&quot;: 0, &quot;confirmed\_violation\_actions&quot;: 0, &quot;conflicted\_event\_count&quot;: 0, &quot;duplicate\_event\_count&quot;: 0, &quot;excluded\_event\_count&quot;: 2, &quot;invalid\_event\_count&quot;: 0, &quot;raw\_event\_count&quot;: 2}

Incident observations, receipts and gaps (`incidents.json`)

Legacy example: claims are unverified; full snapshots and receipts are required.

## Artifacts

Findings (`findings.json`) · Checks (`checks.json`) · Proposals (`proposals.json`) · Evidence index (`evidence/index.json`)
