# F02 证据快照与受限读取

依赖 F01。所属组件为 Evidence Store。公共对象见 [契约](../contracts.md)，验收流程见 [本地验收规范](../acceptance.md)。设计版本 `0.2`，本规格细化版本 `0.3`。

## 用户结果

报告里的每项引用都能回到同一次运行捕获的具体原文。原项目后来变化时，历史报告仍能说明当时检查的是哪版内容。

具体可观察结果：

- 每个 `evidence_id` 绑定一个运行、一个逻辑路径和一份原始字节；同一次运行内，同一路径的同一内容只产生一条证据。
- 捕获之后修改原文件，旧证据仍返回捕获时的字节；再次捕获产生新证据和新摘要，旧证据不被覆盖。
- 任何读取都经过行号、字节上限、运行归属和摘要校验；越界、跨运行、过期和摘要不符返回可定位的结构化错误，不返回残缺内容冒充完整内容。
- 读取被截断时明确标出实际展示的行段和下一段起点，调用方能连续读完。
- 捕获或读取失败进入覆盖记录，带原因，不被计为“已检查且无问题”。
- 输出目录与被审计项目分离，选定外部输入不会把其父目录带进扫描范围。

## 范围与非目标

负责：文件字节捕获、证据标识与快照存储、行段索引、受限读取、引用校验、读取日志、捕获覆盖记录、运行目录内证据索引的持久化与重开。

不负责：目录扫描与候选发现（F03）、规则语义与冲突判定（F04、F07）、事件包格式与回执校验（F05）、模型工具循环（F06）、报告渲染（F09）、CLI 参数与退出码映射（F01、F10）、Git 调用（`source_revision` 由调用方传入，F02 不执行 `git`）。

## 文件与接口

建议新增 `src/context_manager/evidence.py`、`tests/test_f02_evidence.py`、夹具目录 `tests/fixtures/f02/`。实现只使用标准库，不访问网络，不读取项目文件以外的路径。

```python
def prepare_selected_input(path: str, *, max_bytes: int = 10 * 1024 * 1024) -> SelectedInput: ...

class EvidenceStore:
    def __init__(self, project_root, output_root, run_id, *,
                 limits: EvidenceLimits | None = None,
                 clock: Callable[[], datetime] | None = None) -> None: ...

    @classmethod
    def open_existing(cls, run_dir, *,
                      limits: EvidenceLimits | None = None,
                      clock: Callable[[], datetime] | None = None,
                      project_root: str | os.PathLike[str] | None = None) -> "EvidenceStore": ...

    def capture_file(self, relative_path: str, *, kind: str = "document",
                     source_revision: str | None = None,
                     provenance: Provenance = PROJECT_FILE,
                     purpose: str | None = None,
                     expires_at: str | None = None) -> Evidence: ...

    def try_capture_file(self, relative_path: str, **kwargs) -> CaptureOutcome: ...

    def capture_selected_input(self, path_or_prepared: str | SelectedInput, *, input_id: str,
                               kind: str = "event_package",
                               provenance: Provenance = USER_SUPPLIED,
                               source_revision: str | None = None,
                               purpose: str | None = None,
                               expires_at: str | None = None) -> Evidence: ...

    def capture_imported_bytes(self, data: bytes, *, logical_name: str,
                               input_id: str, kind: str = "event_package",
                               provenance: Provenance = SYNTHETIC,
                               purpose: str | None = None,
                               expires_at: str | None = None) -> Evidence: ...

    def list_evidence(self, *, kind: str | None = None,
                      origin: str | None = None,
                      prefix: str | None = None) -> list[Evidence]: ...

    def get(self, evidence_id: str) -> Evidence: ...

    def probe_path(self, relative_path: str) -> PathProbe: ...

    def read_raw(self, evidence_id: str, *, max_bytes: int) -> bytes: ...

    def snapshot_bytes(self, evidence_id: str) -> bytes: ...

    def verify_current_digest(self, relative_path: str, expected_sha256: str) -> DigestCheck: ...

    def location(self, evidence_id: str) -> EvidenceLocation | None: ...

    def all_locations(self) -> Mapping[str, EvidenceLocation]: ...

    def make_ref(self, evidence: Evidence, line_start: int,
                 line_end: int) -> EvidenceRef: ...

    def read_evidence(self, ref: EvidenceRef | Mapping[str, object], *,
                      max_lines: int | None = None,
                      max_bytes: int | None = None,
                      caller: str = "unknown") -> EvidenceRead: ...

    def validate_ref(self, ref: EvidenceRef | Mapping[str, object], *,
                     require_covered: bool = False) -> RefValidation: ...

    def covered_ranges(self, evidence_id: str) -> list[tuple[int, int]]: ...

    def coverage_items(self) -> list[CoverageItem]: ...

    def flush_index(self) -> None: ...

    def close(self) -> None: ...

    # 属性
    run_dir: Path
    evidence_dir: Path
```

辅助对象（结构见下）：`EvidenceLimits`、`CaptureOutcome`、`EvidenceRead`、`RefValidation`、`EvidenceError`。`Evidence`、`EvidenceRef`、`CoverageItem` 由 F01 的 `contracts.py` 提供，F02 只负责产生和校验它们的值。

`kind` 取值：`rule_file`、`document`、`code`、`test`、`data`、`event_package`、`decision_registry`、`other`。`origin` 取值：`project`、`selected_input`、`imported`。`provenance`：项目文件为 `project_file`，选定输入为 `user_supplied`，包内导入为 `synthetic` 或适配器名称。未知枚举按契约错误拒绝。

辅助对象字段：

```python
@dataclass(frozen=True)
class CaptureOutcome:
    evidence: Evidence | None       # 成功时非空
    coverage: CoverageItem          # 每次捕获恰好一条
    error: EvidenceError | None     # 失败时非空，try_capture_file 不抛出

@dataclass(frozen=True)
class EvidenceRead:
    read_id: str
    evidence_id: str
    relative_path: str
    sha256: str
    text: str
    line_start: int
    line_end: int
    requested_line_start: int
    requested_line_end: int | None
    empty: bool
    truncated: bool
    truncation_reason: str | None   # line_limit / byte_limit / None
    next_line: int | None
    raw_bytes: int
    text_bytes: int
    span_sha256: str
    at: str
    caller: str
    def as_receipt(self) -> dict: ...   # read_id、请求与实际范围、raw_bytes、truncated、at、caller

@dataclass(frozen=True)
class RefValidation:
    ok: bool
    code: str | None                # evidence_not_read 等；通过时为 None
    detail: dict                    # 含 evidence_id、请求范围、实际覆盖区间
    covered: bool                   # 引用区间是否被本次运行实际展示过
```

### 证据对象示例

```json
{
  "schema_version": 1,
  "evidence_id": "ev_run-20261104-a1b2c3_0007",
  "run_id": "run-20261104-a1b2c3",
  "kind": "rule_file",
  "origin": "project",
  "relative_path": "AGENTS.md",
  "sha256": "3f0c...9a",
  "snapshot_id": "snap_5c1e...7b",
  "captured_at": "2026-11-04T16:24:12+08:00",
  "source_revision": null,
  "byte_length": 1843,
  "line_count": 42,
  "encoding": "utf-8",
  "provenance": {"kind": "project_file", "adapter": null, "version": null},
  "input_id": null
}
```

`origin` 与 `input_id` 是本规格对 `Evidence` 的增量字段（默认 `"project"` 与 `null`），不改变已有字段含义，见“与其他规格的接口约定”。

### 读取请求与响应示例

```json
{
  "schema_version": 1,
  "read_id": "rd_0003",
  "evidence_id": "ev_run-20261104-a1b2c3_0007",
  "relative_path": "AGENTS.md",
  "sha256": "3f0c...9a",
  "text": "- 新客户端使用 /api/v2/users\n- 兼容目录 legacy/ 仍使用 v1",
  "line_start": 8,
  "line_end": 9,
  "requested_line_start": 8,
  "requested_line_end": 20,
  "empty": false,
  "truncated": true,
  "truncation_reason": "byte_limit",
  "next_line": 10,
  "raw_bytes": 6144,
  "text_bytes": 5980,
  "span_sha256": "b71d...e4",
  "at": "2026-11-04T16:25:03+08:00",
  "caller": "inspector"
}
```

`text` 是选中行用 `\n` 连接的结果，行终止符（`\n` 或 `\r\n`）不进入 `text`；原始终止符保留在快照里，`span_sha256` 覆盖它们。`text.split("\n")` 恰好对应 `line_start..line_end` 各行，最后一行允许为空字符串。

### 最小调用示例

```python
store = EvidenceStore(project_root, out_root, run_id)
ev = store.capture_file("AGENTS.md", kind="rule_file")
ref = store.make_ref(ev, 1, 20)
read = store.read_evidence(ref, caller="inspector")
assert read.truncated is False
store.close()
```

## 证据标识与快照

### 标识规则

- `snapshot_id = "snap_" + sha256(b"context-manager/snapshot/v1\n" + rel.encode("utf-8") + b"\n" + sha256_hex.encode("ascii")).hexdigest()[:32]`。只绑定逻辑路径和内容摘要，跨运行对相同路径与内容稳定。
- `evidence_id = "ev_" + run_id + "_" + 四位以上零填充序号`，序号在本次运行内从 1 递增，不重用、不回退，重开运行目录后从已有最大值继续。
- 同一运行内 `(relative_path, sha256)` 相同则返回既有 `Evidence`，不新分配 ID、不重写快照；不同内容生成新 ID，旧证据保留。
- 选定输入与导入字节按 `(input_id, sha256)` 去重，`relative_path` 固定为 `selected-input/<input_id>/<logical_name>` 或 `imported/<input_id>/<logical_name>`。
- 调用方把 `evidence_id` 当作不透明字符串。F02 解析时从最后一个 `_` 右侧读序号，其余部分为所属运行，用于判定跨运行；其他模块不得解析。

### 存储布局

```text
<out>/<run_id>/
  _store.json                     # run_id、项目根、创建时间、限制、工具版本
  evidence/
    index.jsonl                   # 捕获记录，追加写，权威索引
    index.json                    # 关闭或 flush 时物化的检查点副本
    coverage.jsonl                # 捕获覆盖记录，追加写
    reads.jsonl                   # 读取日志，追加写
    snapshots/<snapshot_id>/
      content.bin                 # 原始字节，写入后不可变
      meta.json                   # 摘要、编码、换行、行数、行索引导出摘要
```

`meta.json` 记录 `snapshot_id`、`relative_path`、`sha256`、`byte_length`、`encoding`、`newline_style`、`line_count`、`line_starts_sha256`、`captured_at`、`capture_count`、`expires_at`。`index.jsonl`、`coverage.jsonl`、`reads.jsonl` 的每一行都是独立 JSON 对象，带 `schema_version: 1`。行段偏移不落盘大数组：读取时由 `content.bin` 确定性重建，并用 `line_starts_sha256` 核对与捕获时一致。

### 写入与不可变

- 快照先写 `content.bin.tmp`，`fsync` 后 `os.replace` 到位；随后写 `meta.json`；成功后追加索引行并 `fsync`。
- 索引行和读取日志在文件锁（`fcntl.flock`）下整行追加；`index.json` 用临时文件加替换物化。
- 若目标快照目录已存在且内容摘要与本次字节不符，返回 `evidence_snapshot_corrupt`，不覆盖。捕获失败不留下可读快照目录。
- 已有证据的 `content.bin` 永不重写。重复捕获相同内容只递增 `meta.json` 里的 `capture_count`。
- `close()` 幂等。它写出 `index.json` 并关闭句柄。`open_existing(run_dir)` 从 `_store.json` 恢复限制和创建时间，加载索引、读取与覆盖记录。要继续捕获项目文件，调用方显式提供匹配本次运行的 `project_root`，序号从最大值继续。已归档快照可以在原项目移走后读取。

## 捕获规则

### 路径校验顺序

1. 字符串非空、无 NUL、长度不超过 1024、以 `/` 分隔、不以 `/` 结尾、无空段、无 `.` 或 `..` 段、无盘符。绝对路径直接 `evidence_path_invalid`；调用方应改用 `capture_selected_input`。
2. 对外路径统一做 Unicode NFC 归一化后存储；访问文件系统时使用调用方给出的原始字符串。
3. 从已解析的项目根开始逐段检查。实际读项目文件时用目录描述符逐级打开，每级禁止跟随符号链接，再相对最后一级目录打开普通文件，避免探测后父目录被替换。任一段为符号链接即 `evidence_symlink_rejected`。
4. 目标必须是常规文件：目录、设备、管道、套接字返回 `evidence_not_regular_file`；不存在返回 `evidence_not_found`。
5. 解析后的真实路径必须仍在项目根之内，否则 `evidence_path_escape`（防御性检查，正常情况下已被第 3 步拦截）。

项目根在构造时 `Path.resolve(strict=True)` 一次，必须是已存在目录；判定以解析后的根为边界，根自身经过的符号链接不视为越界。这条例外是必要的：macOS 的 `/tmp`、`/var` 本身是符号链接，若把根路径上的解析也算违规，所有临时目录测试都会失败。根以下任何组件仍严格拒绝符号链接。

### 运行目录

- `run_id` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`，否则 `evidence_run_id_invalid`。
- 输出根按 `Path.resolve()` 解析，允许尚不存在；与解析后的项目根互相不得包含，也不得相等，对应错误见上表。通过后按需创建 `<out>/<run_id>/evidence/`。
- 运行目录已有 `_store.json` 时，若 `run_id`、项目根一致则继续使用并追加记录；不一致返回 `evidence_run_conflict`，不覆盖已有产物。
- 新运行的 `_store.json` 记录 `schema_version`、`run_id`、`project_root_sha256`、`created_at`、`limits` 和工具版本，`project_root` 为 `null`。根目录摘要用于校验显式重绑，避免把机器上的绝对项目路径带入报告包。旧元数据仍可重开。

### 上限

```python
@dataclass(frozen=True)
class EvidenceLimits:
    max_project_file_bytes: int = 1_048_576      # 项目文件 1 MiB
    max_selected_input_bytes: int = 10_485_760   # 选定输入 10 MiB，对齐 F05
    max_read_lines: int = 80
    max_read_bytes: int = 8_192
    capture_retry_limit: int = 1
```

所有值必须为不小于 0 的整数，其中 `capture_retry_limit` 为 0 表示不重试、其余四项必须大于 0；高于默认值返回 `evidence_limit_invalid`，只允许降低。`capture_file` 用 `max_project_file_bytes`，`capture_selected_input` 与 `capture_imported_bytes` 用 `max_selected_input_bytes`。

`list_evidence` 的 `prefix` 按路径组件比较（`clients` 命中 `clients/a`，不命中 `clients-old/a`）；省略或 `.` 表示全部。结果按 `relative_path` 排序，不返回正文。

### 稳定读取

1. `lstat` 得到初始签名（`st_dev`、`st_ino`、`st_size`、`st_mtime_ns`）。
2. `os.open(..., O_RDONLY | O_NOFOLLOW)` 后 `fstat`，设备与 inode 必须与初始签名一致，否则视为变化。
3. 循环读满，单文件不超过上限；读取字节数与 `st_size` 不一致视为变化。
4. 再次 `fstat` 与 `lstat`，签名必须与打开时一致。
5. 变化则有限重试一次：从第 1 步完整重读，不拼接两次结果。两次读取内容完全相同则接受（内容稳定），否则返回 `evidence_changed_during_read`，覆盖记 `not_run`，不写快照。
6. `sha256` 只对最终保留的那一次字节计算；`byte_length` 取实际字节数。

实现把单次读取封装为内部方法 `EvidenceStore._read_once(path, limit)`，返回 `(bytes, signature)`。测试用 monkeypatch 替换它来模拟读取期间变化；公开 API 不暴露该参数。

### 选定输入与导入字节

- `prepare_selected_input` 在运行目录外完成只读预检，返回冻结的 `SelectedInput`（`data: bytes`、`basename`、`sha256`）。使用本节同样的路径、文件类型、稳定读取和大小限制。`capture_selected_input` 接受已预检对象时复核摘要和本次存储上限，捕获同一份字节，不重新打开原文件。`USER_SUPPLIED` 是默认的 `Provenance`。
- `capture_selected_input` 接受绝对路径或相对调用时工作目录的路径，只解析一次；只允许常规文件。最终组件是符号链接即 `evidence_symlink_rejected`；父目录可能位于符号链接环境（如 `/tmp`），不逐段拒绝。
- 选定输入不进入项目扫描范围，父目录不枚举。`input_id` 必须匹配 `^[a-z0-9][a-z0-9._-]{0,63}$`，同一运行内重复使用时内容必须相同。
- `capture_imported_bytes` 只接收调用方已经读入内存的字节（F05 解析事件包时使用），不访问磁盘；`logical_name` 按相对路径规则校验（禁止绝对路径、`..`、空段），并拼入 `imported/<input_id>/` 前缀。
- 两类输入的 `relative_path` 都是逻辑名，不写入原始绝对路径；`source_revision` 仍由调用方传入。

### 仅供内部组件使用的读取接口

- `probe_path` 按项目路径逐段检查，只返回元数据。`PathProbe` 的字段和状态见[接口矩阵](../interfaces.md)。不创建快照或读取覆盖。忽略目录由 F03 判断。
- `read_raw` 读取同一运行的完整捕获字节，验证归属、过期与原始摘要，使用调用上限和来源上限中的较小值。超限返回 `evidence_too_large`，不返回截断数据。`snapshot_bytes` 仅接受已捕获的项目文件，使用项目文件上限复用同一快照校验。
- `verify_current_digest` 沿项目路径安全读取当前常规文件，返回 `DigestCheck {ok, code, actual_sha256}`。不捕获、不改变覆盖。缺失、越界、符号链接、读取不稳定或摘要不符均返回失败，F08 据此阻止过期预览。
- `location` 和 `all_locations` 返回 F09 `EvidenceLocation` 所需的元数据与相对于运行目录的快照路径。报告链接仍由 F09 验证。所有内部接口均不增加模型读取覆盖。

### 编码与行映射

- 前 8 KiB 含 NUL 字节 → `encoding = "binary"`。
- 以 `EF BB BF` 开头且能整体按 UTF-8 解码 → `encoding = "utf-8-bom"`，首行文本去掉 BOM。
- 其余能整体按 UTF-8 解码 → `encoding = "utf-8"`；解码失败 → `encoding = "invalid-utf8"`。
- 非 UTF-8 文件仍捕获原始字节和摘要，但文本读取返回 `evidence_not_text`。
- 行以 `0x0A` 切分；`\r\n` 的 `\r` 属于终止符，不进入行文本。`newline_style` 为 `lf`、`crlf`、`mixed` 或 `none`。
- `line_count`：空文件为 0；否则为换行符个数加上“末尾无换行时为 1”。`line_starts_sha256` 对逗号连接的字节偏移列表求摘要。

## 读取与引用追溯

### 校验顺序

`read_evidence` 依次检查，命中即返回结构化错误，不降级返回内容：

1. `max_lines`、`max_bytes` 参数合法且不高于默认值。
2. 引用结构：`evidence_id`、`sha256`、`line_start`、`line_end` 齐全、类型正确，`sha256` 为 64 位十六进制，`line_start >= 1`、`line_end >= line_start`。
3. 引用 ID 无法解析为证据 ID 为 `evidence_ref_invalid`；可解析但 `run_id` 不是本次运行为 `evidence_cross_run`；属于本次运行但索引无记录为 `evidence_not_found`。
4. 过期：`expires_at` 非空且 `now >= expires_at` 为 `evidence_expired`（`reason = "retention_elapsed"`）；快照文件缺失同为 `evidence_expired`（`reason = "snapshot_missing"`）。
5. 摘要：`ref.sha256` 与记录不一致为 `evidence_digest_mismatch`。
6. 快照完整性：`content.bin` 重新求摘要，与记录不符为 `evidence_snapshot_corrupt`；行索引摘要不符同为该错误。
7. 范围：`line_end` 超过 `line_count` 或 `line_start` 超过 `line_count` 为 `evidence_range_invalid`，`details` 写出行数与请求范围。空文件允许 `line_start = 1`、`line_end` 省略或为 1。
8. 文本：`binary`、`invalid-utf8` 返回 `evidence_not_text`。
9. 截断：命中行数或字节上限时返回部分内容，不报错。

### 截断算法

```python
# end 取 line_end，省略时取记录的 line_count；两个上限都先与默认值取小
end = line_count if line_end is None else line_end
max_lines = min(max_lines or limits.max_read_lines, limits.max_read_lines)
byte_limit = min(max_bytes or limits.max_read_bytes, limits.max_read_bytes)
selected, used = [], 0
for line_no in range(line_start, end + 1):
    span = line_span_bytes(line_no)          # 含该行终止符，最后一行可能不含
    if span > byte_limit and not selected:
        raise evidence_line_too_long(line_no, span, byte_limit)
    if selected and (len(selected) >= max_lines or used + span > byte_limit):
        break
    selected.append(line_no); used += span
```

- `truncated` 表示未覆盖到 `end`；`next_line` 为下一段起点，未截断时为 `null`。
- `truncation_reason` 取 `line_limit`、`byte_limit` 或 `null`。
- 单行原始字节超过 `max_read_bytes` 时整行不可读，返回 `evidence_line_too_long`，`details` 写出 `line_no`、`line_bytes`、`limit`；读取相邻行不受影响。不把超限行拆成片段，避免引用无法用行段表达。
- `raw_bytes` 为实际展示范围的原始字节数，范围与 `span_sha256` 完全一致（含最后一行终止符，若有）；`text_bytes` 为 `text` 的 UTF-8 字节数。
- 空文件返回 `text = ""`、`line_start = 1`、`line_end = 1`、`empty = true`、`raw_bytes = 0`、`span_sha256 = sha256(b"")`。

### 读取日志与覆盖

- 每次成功读取追加 `reads.jsonl`：`read_id`、`evidence_id`、请求范围、实际范围、`raw_bytes`、`truncated`、`at`、`caller`。`caller` 限 `inspector`、`analyst`、`report`、`test`、`unknown`。
- 被拒绝的读取也追加一条记录：`read_id` 为 `null`、带 `code` 与请求范围，不含正文，便于回溯“读过但被拒”的调用。只有成功读取进入 `covered_ranges`。
- 读取不重新访问原项目文件，只读快照。
- `covered_ranges(evidence_id)` 返回本次运行已实际展示的行段（合并重叠区间），供 F06 判定候选引用是否在被读过的范围内。
- `validate_ref(ref, require_covered=True)` 在结构、归属、摘要、范围之外再检查引用区间是否被展示过；未展示返回 `evidence_not_read`，`ok = false` 但不抛异常，由调用方记录拒绝原因。

### 覆盖记录

捕获每次产生一条 `CoverageItem`：

| 情况 | status | reason | inputs / artifact_refs |
| --- | --- | --- | --- |
| 文本捕获成功 | `passed` | null | `[evidence_id]` / `["evidence/snapshots/<snapshot_id>/content.bin"]` |
| 二进制或无效编码 | `not_run` | `binary` 或 `invalid_utf8` | `[evidence_id]` / 同上 |
| 越界、符号链接、不存在、权限、超限、读取中变化 | `not_run` | 对应错误码 | `[]` / `[]` |

`check_id` 为 `evidence.capture:<key>`。`key` 取规范 JSON 数组 `[origin, logical_path, purpose_or_null]` 的 UTF-8 SHA-256，编码按公共契约的确定性规则。相同用途的不同文件拥有不同 ID。捕获失败也先写入一条 `not_run` 覆盖记录再抛出；读取失败只写拒绝日志，由调用方把校验结果并入自己的覆盖。

`try_capture_file` 只把 `EvidenceError` 转成 `CaptureOutcome.error`，其余异常照常抛出，避免把程序缺陷记成材料缺口。

## 错误

`EvidenceError(code, message, details)` 带 `to_dict()`，输出 `{"schema_version": 1, "error": {"code", "message", "details"}}`，`message` 不包含文件正文。

| code | 触发 | CLI 归类 |
| --- | --- | --- |
| `evidence_path_invalid` | 空、绝对、含 NUL、含 `..`、格式不符 | 2 |
| `evidence_path_escape` | 解析后离开项目根 | 2 |
| `evidence_symlink_rejected` | 任一路径组件是符号链接 | 3 |
| `evidence_not_found` | 文件或证据记录不存在 | 3 |
| `evidence_not_regular_file` | 目录、设备、管道等 | 3 |
| `evidence_unreadable` | 权限不足或 IO 错误 | 3 |
| `evidence_too_large` | 超过对应单文件上限 | 3 |
| `evidence_changed_during_read` | 重试后内容或元数据仍变化 | 3 |
| `evidence_limit_invalid` | 限制非正或高于默认 | 2 |
| `evidence_output_inside_project` | 输出目录位于项目根内 | 2 |
| `evidence_output_contains_project` | 项目根位于输出目录内 | 2 |
| `evidence_run_conflict` | 运行目录已有不同 `_store.json` | 2 |
| `evidence_run_id_invalid` | `run_id` 不匹配命名规则 | 2 |
| `evidence_input_id_invalid` | `input_id` 不匹配 `^[a-z0-9][a-z0-9._-]{0,63}$` | 2 |
| `evidence_input_id_conflict` | 同一 `input_id` 出现不同内容 | 2 |
| `evidence_ref_invalid` | 引用字段缺失、类型错误、摘要格式错误 | 2 |
| `evidence_cross_run` | 证据 ID 属于其他运行 | 3 |
| `evidence_expired` | 超过保留期或快照文件缺失 | 3 |
| `evidence_digest_mismatch` | 引用摘要与记录不符 | 3 |
| `evidence_range_invalid` | 行号非法或超过总行数 | 2 |
| `evidence_line_too_long` | 单行超过读取字节上限 | 3 |
| `evidence_not_text` | 对二进制或无效编码做文本读取 | 3 |
| `evidence_not_read` | `validate_ref(require_covered=True)` 时引用区间未被实际展示 | 3 |
| `evidence_snapshot_corrupt` | 快照字节或行索引与记录不符 | 1 |
| `evidence_index_corrupt` | 索引无法解析或版本未知 | 1 |

退出码由 F10 按契约第 7 节映射：`2` 为参数或契约错误；`3` 表示本次请求的读取未完成，整次运行相应为 `partial`；`1` 表示证据存储损坏，本次无法得到可用结果。`evidence_not_read` 只作为 `RefValidation.code` 出现，`validate_ref` 不抛异常。

## 边界条件

| 条件 | 行为 |
| --- | --- |
| 空文件 | `line_count = 0`，读取返回 `empty = true`、`text = ""`；`line_start = 2` 报 `evidence_range_invalid` |
| 仅 BOM 的文件 | `encoding = "utf-8-bom"`，`line_count = 1`，首行文本为空 |
| 无末尾换行 | 最后一行延续到 EOF，`line_count` 正确 |
| CRLF 与混合换行 | 原始终止符保留，`text` 中统一为 `\n`，`span_sha256` 覆盖原始终止符 |
| 单行超过 8 KiB | `evidence_line_too_long`，相邻行仍可读 |
| 文件恰好等于上限 | 允许；上限加 1 字节拒绝 |
| 同名相似目录（`clients/`、`clients-old/`） | 按路径组件独立记录，不合并、不互相越界 |
| 输出目录为项目兄弟目录（如 `project-out/`） | 允许；按组件比较，不做字符串前缀判断 |
| 输出目录等于或位于项目内 | `evidence_output_inside_project` |
| 项目根位于输出目录内 | `evidence_output_contains_project` |
| 符号链接文件或目录组件 | `evidence_symlink_rejected`，原文件不变 |
| 项目根自身经符号链接解析（如 `/tmp`） | 允许，根以下仍严格检查 |
| 读取中文件被替换 | 有限重试一次；仍变化则不产生快照 |
| 捕获后原文件被修改或删除 | 旧证据仍可读，新捕获生成新 ID |
| 同一内容重复捕获 | 返回同一 `evidence_id`，`capture_count` 递增 |
| 快照被外部篡改 | 读取报 `evidence_snapshot_corrupt`，不返回被改内容 |
| 运行目录已存在但 `_store.json` 不同 | `evidence_run_conflict` |
| 选定输入位于项目内 | 允许，仍按 `selected-input/<input_id>/...` 记录，不把父目录纳入扫描 |
| 选定输入的父目录 | 不枚举、不隐式捕获，只能按文件逐个显式传入 |
| 选定输入重复 `input_id` | 内容相同返回既有证据；内容不同 `evidence_input_id_conflict` |
| 外部绝对路径出现在对外产物 | 不出现；只保留 `input_id` 与逻辑名 |

## 依赖接口

### 上游 F01 需要提供

- `contracts.py` 中的 `Evidence`、`EvidenceRef`、`CoverageItem` 与 `SCHEMA_VERSION = 1`；`Evidence` 需接受 `origin`、`input_id`、`expires_at` 三个可选字段（默认 `project`、`null`、`null`）。
- 统一的错误基类或 `code`/`message`/`details` 约定；若 F01 未提供，F02 在 `evidence.py` 内定义 `EvidenceError`，字段保持一致，F10 再统一包装。
- 时间戳与摘要工具：带时区的 ISO 8601 解析/格式化、`sha256` 十六进制小写。
- 枚举校验：`kind`、`origin`、`provenance` 的允许值。

### 下游使用约定

| 功能 | 使用方式 |
| --- | --- |
| F03 | 对候选规则文件调用 `try_capture_file`，按 `CaptureOutcome` 记录跳过原因；不得自行 `open` 项目文件；二进制和符号链接在捕获前跳过或按 `not_run` 记录 |
| F04 | 用 `make_ref` 生成行段引用，经 `read_evidence` 取原文；条款行号直接使用读取结果，不重新解析磁盘 |
| F05 | 事件包用 `capture_selected_input`（10 MiB 上限）；包内历史内容用 `capture_imported_bytes` 重新计算摘要并作为本次运行的新证据；回执引用这些新 ID。F05 可以自行解析用户显式选定的事件包，但引用、摘要与回执必须来自 F02 证据；模型读取不得绕过 F02 |
| F06 | 工具 `list_evidence`/`read_evidence` 直接映射 `list_evidence`/`read_evidence`，`caller="analyst"`；候选引用用 `validate_ref(require_covered=True)` 校验；不得绕过 F02 读盘 |
| F07 | 对 Finding 的 `support_refs`/`counter_refs` 调 `validate_ref`，按 `covered_ranges` 记录模型是否真读过 |
| F09 | 证据链接指向 `evidence/snapshots/<snapshot_id>/content.bin`；元数据取 `index.json`；对外只显示逻辑路径 |

## 与其他规格的接口约定

1. **选定输入上限**：本规格把项目文件默认上限保持 1 MiB，把选定输入与导入字节的上限设为 10 MiB，与 F05 的事件包 10 MiB 对齐；只允许向下配置。
2. **`provenance` 取值**：证据输出使用契约 `Provenance` 对象，项目捕获默认 `project_file`，包内合成材料为 `synthetic`。
3. **`Evidence` 增量字段**：`origin`、`input_id`、`expires_at` 为可选字段，带默认值，不影响既有对象往返。
4. **行段索引**：不落盘偏移数组，改为读取时确定性重建并核对 `line_starts_sha256`，保留“行段索引”的可验证性，避免 1 MiB 短行文件产生数 MB 元数据。
5. **单行超限**：整行拒绝而不拆片段，保持引用只以行段表达。
6. **跨运行识别**：`evidence_id` 内嵌 `run_id`，这是实现细节；其他模块只当作不透明 ID。
7. **不持久化外部绝对路径**：选定输入只记录 `input_id` 和逻辑名，避免本机路径进入产物。

## 本地测试

```sh
.venv/bin/python -m pytest tests/test_f02_evidence.py tests/test_f01_foundation.py -q
```

夹具放在 `tests/fixtures/f02/`，测试把 `project/` 复制到 `tmp_path/project`，把 `outside/` 复制到同级 `tmp_path/outside`，符号链接、大文件和权限异常在临时目录内现场创建，不提交大文件。

| 夹具 | 内容 | 用途 |
| --- | --- | --- |
| `project/AGENTS.md` | LF、含中文和列表、末尾有换行 | 基本捕获、行段 |
| `project/docs/crlf.md` | CRLF、末尾有换行 | 换行与 `span_sha256` |
| `project/docs/zh.md` | 中文、无末尾换行 | 最后一行与无终止符 |
| `project/docs/lines-200.md` | 200 行编号文本 | 截断与续读 |
| `project/clients/users.py`、`project/clients-old/users.py` | 相似目录 | 路径组件比较 |
| `project/docs/empty.md` | 空文件 | 空文件读取 |
| `project/docs/bom.md` | UTF-8 BOM | BOM 处理 |
| `project/docs/binary.bin`、`project/docs/bad-utf8.txt` | NUL 字节 / 非法 UTF-8 | 非文本标记 |
| `selected/case.json` | 小型合成事件包 | 选定输入 |
| `outside/secret.md` | 位于临时项目根之外 | 越界与逃逸 |

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| F02-01 | LF、CRLF、中文、无末尾换行 | 三个文件的 `sha256` 等于原始字节摘要；`line_count` 正确；CRLF 读取的 `text` 为 `a\nb`、`newline_style = "crlf"`、`span_sha256` 等于含 `\r\n` 的原始字节摘要；中文文件最后一行完整 |
| F02-02 | 捕获后修改原文件 | 旧证据读取仍等于捕获版本且摘要不变；再次捕获得到新 `evidence_id` 与新摘要；`list_evidence` 同时包含两条；旧快照字节未变 |
| F02-03 | 越界、符号链接、路径相似目录 | `../outside/secret.md` 与绝对路径被拒；文件符号链接和目录符号链接均 `evidence_symlink_rejected`；失败后源文件摘要不变；`clients/` 与 `clients-old/` 各自捕获且 `snapshot_id` 不同；兄弟输出目录可用、项目内输出目录被拒 |
| F02-04 | 超限、坏编码、读异常 | 降低上限后超限文件报 `evidence_too_large` 且无快照；二进制与非法 UTF-8 捕获成功但读取报 `evidence_not_text`；覆盖项为 `not_run` 并带原因；权限不足（非 root 时）报 `evidence_unreadable`；上述项均不出现在 `passed` 计数中 |
| F02-05 | 跨运行 ID、错摘要、越界行段、过期 | 另一运行的 ID 报 `evidence_cross_run`；错误摘要报 `evidence_digest_mismatch`；`line_start = 0`、`line_end = line_count + 1`、`line_start > line_end` 均报 `evidence_range_invalid`；本运行不存在的合法 ID 报 `evidence_not_found`；`expires_at` 已过报 `evidence_expired`；删除 `content.bin` 后报 `evidence_expired`（`snapshot_missing`） |
| F02-06 | 单次读取截断与续读 | 200 行文件按默认上限读到 1–80，`truncated = true`、`reason = "line_limit"`、`next_line = 81`；续读至 160、再至 200 时 `truncated = false`、`next_line = null`；每行 200 字节的文件在第 40 行截断，`reason = "byte_limit"`；`max_lines = 10` 生效，`max_lines = 200` 报 `evidence_limit_invalid` |
| F02-07 | 模拟读取时文件变化 | 替换 `_read_once` 返回两个不同版本时，重试一次后报 `evidence_changed_during_read`，不产生证据与快照，覆盖为 `not_run`；两次返回相同字节时捕获成功且摘要为该版本；不存在由两次读取拼接出的摘要 |
| F02-08 | 选定外部输入及项目内输出路径 | 外部文件经 `capture_selected_input` 捕获，`origin = "selected_input"`、`input_id` 正确、`relative_path = "selected-input/<input_id>/case.json"`；同目录其他文件不被列出；`capture_file` 访问项目外路径被拒；项目内输出目录报 `evidence_output_inside_project`，输出目录包含项目时报 `evidence_output_contains_project`；重复 `input_id` 内容不同报 `evidence_input_id_conflict` |
| F02-09 | 超长单行 | 含 9 KiB 单行的文件，读取该行报 `evidence_line_too_long`，`details.line_no` 正确；读取相邻正常行成功；`reads.jsonl` 留下带 `code` 的拒绝记录且该行未进入 `covered_ranges` |
| F02-10 | 空文件、BOM、上限边界 | 空文件 `line_count = 0`、读取 `empty = true`、`text = ""`、`line_start = 2` 报 `evidence_range_invalid`；BOM 文件首行不含 `\ufeff` 且 `span_sha256` 含 BOM；恰好等于上限的文件被接受，多 1 字节被拒 |
| F02-11 | 幂等、损坏与重开 | 相同内容重复捕获返回同一 ID 且索引只有一条记录；篡改 `content.bin` 后读取报 `evidence_snapshot_corrupt`；`open_existing(run_dir)` 在删除原项目文件后仍能列出并读取证据；同一运行目录上两个 store 实例交替捕获不同文件时序号递增、索引无丢失 |

测试不得只断言异常类型：每个错误用例同时断言 `code`、`details` 关键字段，以及源文件摘要未变。测试不读取 `tests/fixtures/f02` 以外的真实项目文件，不联网，不调用外部命令。

r2 增补 F02-08 的预读后源文件变化与路径探测，确认捕获字节与预检一致。F02-10/11 核对原始读取上限、跨运行与损坏拒绝、读取覆盖隔离、证据目录和当前文件摘要复核。

## 通过与交接

审查重点：

- 引用能回到同一次运行的实际字节，`sha256` 与 `span_sha256` 由捕获内容计算，不是从预期文本反推。
- 读取边界在实现层执行，`read_evidence` 之外没有读取快照或原文件的旁路。
- 捕获后修改、读取中变化、损坏快照三种情况下不会返回混合版本或冒充完整内容。
- 越界、符号链接、跨运行、过期、错摘要都返回带定位信息的结构化错误，覆盖记录如实反映未完成。
- 产物中不出现项目外绝对路径；运行目录可重开且不依赖原项目。
- 与 F03/F04/F05/F06/F07/F09 的接口按上文约定实现，未私自扩展公共契约。
