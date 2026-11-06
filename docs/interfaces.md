# 跨功能接口矩阵 · r2 基线与 r3 澄清

本页与[公共数据契约](contracts.md)一并作为首版实现基线。r3 明确 F01 接受空清单的结构、F05 复核完整覆盖，并补充解析隔离和结构化错误要求。下表明确谁生成材料、谁验证、谁可以写产物。专项交接中的旧提议保留为历史；实现者按本页及已对齐的专项规格对接。

## 联调接口

| 提供方 → 使用方 | 冻结接口 | 交接边界 | 验收定位 |
| --- | --- | --- | --- |
| F01 → F02–F10 | `contracts.py` 的 `parse_object`、`dump_object`、`CoverageItem` 等公共契约形状 | F01 校验结构，引用与授权由业务模块复核。F01 拥有 CLI 骨架与 pytest 标记，F10 接入管线。 | F01-01～12 |
| F02 → F03 | `store.probe_path(relative_path) -> PathProbe` | 只读元数据，返回 `status/is_symlink/is_dir/byte_length`；状态 `file/directory/missing/symlink/outside_project/unsafe_path/not_regular_file/unreadable`。F03 先判断自己的忽略目录，再调用探测。 | F02-05/08、F03-05～07 |
| F02 → F04/F05/F08/F10 | `prepare_selected_input(path, *, max_bytes=10*1024*1024) -> SelectedInput`；`store.capture_selected_input(path_or_prepared, ...)`；`store.read_raw(evidence_id, *, max_bytes) -> bytes`；`store.snapshot_bytes(evidence_id) -> bytes`；`store.verify_current_digest(relative_path, expected_sha256) -> DigestCheck` | 预检安全读取一次，保留不可变字节、逻辑文件名和摘要，无运行目录副作用；捕获已准备输入时不重新打开原文件。原始读取限于本次运行，重核全文件摘要，超限整体拒绝。snapshot_bytes 只接收当前项目文件来源的捕获快照；摘要复核只安全读取当前项目文件并返回结果，不捕获新证据。三个内部读取入口均不增加模型覆盖。 | F02-08/10/11、F05-01/15、F08-05/08、F10-07/09 |
| F02 → F06/F07/F09 | `make_ref`、`read_evidence`、`validate_ref`、`covered_ranges`、`location/all_locations` | 模型仍限行数、字节数和实际展示范围。F02 为 F09 提供当前运行的快照相对路径与元数据；F10 构造 F09 `EvidenceCatalog`，F09 不访问原项目。每次捕获的覆盖 ID 绑定逻辑身份和用途，不以用途单独分组。 | F02-02/03/06/11、F06-05/07、F09-01/07 |
| F03 → F04/F05/F08 | `InspectionResult.rule_files[].scope` 投影为 F04 `RuleSource.source_scope`；`registered_instruction_files[] = {relative_path, evidence_id}`；profile 的 `harnesses[].context_channels` 可选 | 范围无法解析的规则仍报告，不能退回项目根进行确定性比较或编辑。白名单取成功捕获且明确可编辑的规则文件；任意导入文本不因被读取而可编辑。静态 profile 的渠道用于合成测试前须明确声明完整集合。 | F03-03/08/12/14、F08-06、F10-03 |
| F04 → F05/F07/F08/F10 | `parse_registry(raw_bytes, *, project_id) -> RegistryInput`；`bind_registry(input, store) -> Registry`；`compare_constraints(...)`；`DecisionResolver.policy_at(as_of)`；`confirmation_approves(confirmation_id, approval_key, *, as_of)` | 预检拒绝坏结构；运行内绑定验证全文件摘要与行段，隔离失败条目并记录覆盖。F05 用事件时点的 PolicyState 查询规则和约束；F07 验证候选；F08 通过 F04 验证逐决策的改法批准。F04 保存 `candidate_id → edit_targets`。 | F04-01/05/09/10/11、F05-08、F08-01/03/11、F10-01/03 |
| F05 → F06/F07/F09/F10 | `parse_incident_input(raw_bytes, *, project_id)`；`IncidentAnalysis.coverage_items: CoverageItem[]`；既有 `loads/behaviors/recurrence` 等视图 | 预检含包级结构和旧格式识别。捕获后用 `read_raw` 验证并解析同一份字节，按 F05 规格导入。F05 拥有覆盖和证据门槛，F10 不补算。事件范围优先于包声明、再回退 CLI，最终受 CLI 范围约束。 | F05-01～15、F10-02/03/07/13 |
| F06 → F07/F09/F10 | `analyze_context(...)` 返回 `AnalystResult`。F10 将候选和 `model_reads=analyst.reads` 交 F07，并用 `trace=` 交 F09 | 模型引用须由本次 F06 调用的实际返回行段覆盖。无决策体检的进度和规则比较共用预算，提交前的文档与区块编号转换为公共引用。F07 将迁移理由写入声明供报告和建议使用。trace 保留读取元数据、筛选与拒绝原因。 | F06-01～13、规则清理与本机语义案例、F09-02/08/09、F10-04/05/06 |
| F03 → F07 | `evaluate_findings(..., inspection=inspection_result, model_reads=())` | dead_reference 回查真实 F03 观察与 F02 当前探测，不能只相信候选 facts；模型引用再次核对本轮 model_reads。Finding ID 使用逻辑路径/摘要/行段，避免运行 ID 改变同一结论身份。 | F07 证据门槛、F10 确定性 |
| F07 → F08/F10 | `DiagnosisResult.validations[].{candidate_id,finding_ids}`；`findings: Finding[]` | 适配层只回连确定性 F04 `stale_rule` 的可编辑目标。空候选是完成的检查；越权、模型自述或加载推测不能变成证实事实。 | F07-01～18、F10-01/02/13 |
| F08 → F09/F10 | `RepairPlan`；`apply_check = {exit_code, checked_at, applied_in_temp_copy, checked_files}` 可空 | `checked_files` 每项是 `{path, sha256}`，记录临时预检的基准文件摘要。失败仍记录实际退出码与原因；没启动 Git 时退出码为空。F09 原样写 Diff，修复检查留在提案产物。 | F08-01～12、F09-04/12 |
| F09 → F10 | `build_artifacts(..., *, trace=(), incident_analysis=None, inspection_result=None, clock=None)`；`write_run_artifacts(..., *, trace=(), incident_analysis=None, inspection_result=None, clock=None) -> RunArtifacts` | F09 返回最终状态和退出码，保留报告降级原因。diagnose 有分析结果时写 incidents.json；inspection_result 写 inspection.json 并展示静态清单。F10 排他建立运行目录，F09 在目录内排他建立 run.json。 | F09-01～12、F10-08/11/12 |

## 正常入口的关键顺序

1. F01 解析参数；F02 安全预读用户选定文件；F04/F05 校验登记和事件包结构；local 模式校验 TOML。拒绝与项目互相包含或相等的输出根。参数和输入结构错误在建目录前退出 2。
2. F10 排他创建运行目录。F02 捕获预检时已读的原始字节；F03 发现规则；F04 绑定逻辑来源，作静态比较。来源不可读、摘要不符或行段越界时隔离相关条目，按覆盖降为 partial。
3. diagnose 由 F05 解析事件和回执，保留去重、时间、来源、缺口。F05 有独立完整渠道声明时才允许完整排除。渠道声明优先级为显式传入的合成适配器 profile、事件包显式合成 profile、F03 显式合成 profile；无法确认完整渠道时，只能凭匹配条目支持已加载。
4. local 模式调用 F06。F10 分别传递静态事实和模型候选给 F07，合并上游覆盖。F08 对支持的编辑方向核对批准、字节和预检。F09 写报告，CLI 使用最终退出码。
5. F10-01 用两份登记做正反验证。精确批准时只预览旧规则的目标行；省略、过期或替换摘要不匹配时显示 `needs_confirmation` 且无 Diff。两种情况下兼容例外与测试义务均保留。
6. F10-02 用默认 off 复核受支持的复发和加载事实，且不产生因果 Finding。因果等级由 F07 的专项用例验证；F10-06 用真实模型验证候选进入 F07 后仍受等级约束。F10 负责用旧样例实际运行结果更新示例报告。

## 变更影响与实施交接

| 裁决 | 主要改动 | 复核范围 |
| --- | --- | --- |
| D01 公共形状与安全绑定 | F01 结构类型、F04 登记解析及有效性、F10 预检与捕获顺序 | F01/F02/F04/F05/F10 |
| D02 证据查询与只读字节 | F02 查询、预读、完整快照、摘要及报告位置；F03 scope/白名单 | F02/F03/F05/F06/F08/F09 |
| D03 具体改法与提案完整性 | F04 授权、F07 关联、F08 摘要及预检、F09 产物 | F04/F07/F08/F09/F10 |
| D04 运行覆盖与语义记录 | F05 覆盖和事件产物、F06 trace、F07 空候选、F09/F10 降级 | F05/F06/F07/F09/F10 |
| D05 本机验收环境 | F01 虚拟环境与 pytest 标记；F06 本机配置；F10 安装后入口 | F01 基础门槛、F06 真实模型、F10 全流程与 wheel |

## F03–F10 代码补强说明（2026-11-05）

- 公开组件结果通过 `views.py` 深层封存，Mapping 和序列不共享可变容器。`dump_object` 返回独立 JSON 投影；内部更新用新视图替换，不解冻既有策略或缓存。新增组件视图是内部接口，对外 JSON 仍为 schema_version 1。
- F01 的开放 payload 同步封存底层映射；选定文件预检不跟随末级链接、不阻塞 FIFO，并保留末级路径交 F02 再校验。F01 因这些行为及 CLI 变化需重新复核，旧 accepted 只代表历史快照。
- profile 由 F03 的 `validate_profile` 统一校验，CLI 和组件调用使用同一门槛。任务排除条件不会写入规则固有作用范围。
- F08 首行旧字节摘要包含原始 BOM；BOM 作为不变前缀保留，replacement_sha256 只覆盖实际批准的替换正文。具体换行与删除说明见 F08。F09 在写入前复核快照字节与提案；新产物排他落盘，失败留下 incomplete 清单。

实际范围、运行结果和未完成验收见[详细执行方案](f03-f10-execution-plan.md)与[功能状态](feature-status.md)。

[本地验收规范](acceptance.md)约定源码摘要和实际日志。实现顺序按[执行计划](../EXECUTION_PLAN.md)中的依赖门槛推进。F01 先建立 Python 3.11+ 工程、开发依赖和两个入口；F02 接入后，依赖齐备的模块可按表实施。本机模型服务的接口已经确定为无认证的本机回环 OpenAI 兼容聊天接口，运行器、模型权重与量化由本机实际环境确认。配置保存在 Git 忽略的 `local/model.toml`，按 F06 固定参数记录运行器、模型、版本、温度、超时和提示词摘要。环境未就绪时，真实语义用例和最终交付仍为 `not_run`。
