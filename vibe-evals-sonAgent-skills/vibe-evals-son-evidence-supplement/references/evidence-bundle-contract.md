# Evidence Bundle v1 数据契约

`MANIFEST.json` 声明 schema=`vibe-evals-evidence-bundle`、schema_version=`1.0.0`、唯一 package_id 和机械状态。原始 prompt、rubrics、record.txt 按字节复制；JSON/JSONL 是事实源。JSON Schema 负责单文件形状，`validate_bundle.py` 负责目录、外键、轮次、哈希、证据方向和封印的跨文件约束；两者不能互相替代。

## Manifest

包含 task、generator、source_input_digest、inputs.prompt、inputs.rubrics[]、models[]、package_status、missing_materials、warnings。rubric 条目包含 round、path、count、sha256。所有包内路径使用 `/` 相对路径，禁止盘符、绝对路径、`..` 和符号链接。

`source-freeze.json` 的 input_digest 必须等于 manifest，source_inventory 覆盖探测到的 prompt、rubrics、记录、对话、所有模型产物和轮次快照。它还冻结 prompt、记录、各轮 rubric、模型最终目录、各轮快照和对话文件之间的唯一绑定；路径有多个合理候选时 discovery 必须失败，不能选“第一个”。补证前用它检测源变化。

## Prompt requirements

`inputs/prompt-requirements.jsonl` 每条包含 requirement_id、round、kind、text、source、mapped_rubric_ids、coverage。kind 只能是 `explicit`、`necessary_implicit` 或 `context`；coverage 只能是 `mapped`、`gap` 或 `not_applicable`。显性要求的 `source.quote` 必须与 prompt 连续行逐字一致；必要隐含要求还必须给出 `rationale`；上下文只能用 `not_applicable`。初始化器产生的 `candidate/needs_classification` 只是空骨架，validator 必须拒绝封包。

## Rubric index

每条包含 id、round、criterion、criterion_sha256、source_file、source_index、source_basis、review。source_basis 仅为 `prompt`、`model_effect`、`author_thinking`；后者必须引用用户或材料。未知字段保留在原 rubric JSON，本机仅追加 score/reason。

## Rubric evidence

每模型 × 每 rubric 恰好一条，字段为 model_id、rubric_id、rubric_round、criterion、disposition、coverage、suggested_score、confidence、reason_code、fact_summary、evidence、human_check_needed、adjudication_ids、limitations。

score 只允许 0、1、null。reason_code 使用：implemented_and_verified、implemented_static_only、behavior_failed、missing_implementation、violates_explicit_constraint、insufficient_evidence、subjective_needs_human、unsafe_to_test、policy_ambiguous、model_fallback_zero。

## Evidence

- static_line：ID、direction、source_state、round、path、line_start/end、file_sha256、source_blob_path、excerpt、excerpt_sha256、fact。`source_blob_path` 指向包内按文件 SHA-256 命名的原字节副本；validator 用它重算文件哈希并复现精确行段。
- self_test_run/probe_run：ID、direction、run_id、fact。
- conversation：ID、direction、msg_ref、flat_msg_index、role、excerpt、event_type、fact。
- human_note：ID、direction、observation_id、quote、fact。
- snapshot_diff：ID、direction、from/to_round、path、before/after_sha256、fact。
- inventory_fact：ID、direction、metric、value、basis、fact；用于 `confirm_missing` 时还必须有 source_state=`round_end`、round、path，并由对应轮完整 inventory 证明该路径确实不存在。

direction 只允许 support、refute、confirm_missing、context。所有 ID 全局唯一。

## 测试、对话、裁定

测试命令保存 argv、状态、退出码、耗时、结构化断言、stdout/stderr、`script_path`、`script_sha256` 与六项安全布尔值。执行状态时六项必须全真；脚本原文必须保留在模型目录内且哈希相符。passed/failed 的每个断言包含 rubric_id、expected、actual、passed，评分证据只能引用同 rubric 的断言。

对话单列首轮是否提问、问题摘录、debug 次数和修复轨迹。不可得统计用 null 与原因。

裁定项含 ADJ ID、状态、rubric、问题、歧义、证据、全模型适用性、建议/备选口径、影响和 resolution。远端只能 pending + null resolution。裁定项与受影响模型的 rubric evidence 必须双向引用，不能留下无上下文的空壳 ADJ。

## 状态

- 结构、哈希、引用或覆盖错误：incomplete。
- pending、null、human_check_needed：ready_for_local_review。
- 无错误、无 pending/null/人工检查：ready_for_form。
- 声明状态与推导不一致：校验失败。

## Delta

增量 `DELTA.json` 使用 schema=`vibe-evals-evidence-delta`、schema_version=`1.0.0`，包含 delta_id、base_package_id、base_digest、request_sha256、source_input_digest、request_ids、response_count。`responses.jsonl` 对 request_ids 每项恰好一条。状态只允许 fulfilled、partial、unresolved；无效模型/rubric 请求在 delta 初始化前整批拒绝。源输入变化由 `verify_source.py` 以退出码 4 在 delta 初始化前终止。

delta 必须逐字节携带 `request-copy.json`，并由本机保留的原始 `evidence_requests.json` 复核；远端不可改写允许的方法。只有 fulfilled 可带 `proposed_update`，且远端不能清除 human/adjudication 门禁。delta 中的源码 blob 必须恰好等于新增证据引用集合。

delta 通过 `package_delta.py <delta> <base> <原请求> <zip>` 封印，不能使用 `package_bundle.py`。本机用 `merge_delta.py <base> <delta> <原请求> <新目录>` 生成新的未封印 bundle；新证据必须进入对应规范 `rubric-evidence.jsonl`，原 response 同时保存在 supplements 审计目录。随后重新运行 `package_bundle.py` 封印。
