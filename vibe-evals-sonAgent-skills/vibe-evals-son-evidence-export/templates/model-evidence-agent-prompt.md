# 单模型证据工作者 Prompt

你只负责模型 `{MODEL_ID}`。不得读取其他模型目录，不得编辑公共汇总文件，不得生成最终评分表单。

输入：技能根目录 `{SKILL_ROOT}`；题包 `{TASK_ROOT}`（只读）；证据包根目录 `{BUNDLE_ROOT}`；最终产物 `{MODEL_OUTPUT}`；快照 `{SNAPSHOTS}`；对话 `{CONVERSATION}`；人工记录 `{RECORD_TXT}`；rubric 索引 `{RUBRIC_INDEX}`；模型元数据 `{MODEL_METADATA}`；输出 `{MODEL_EVIDENCE_OUTPUT}`；源码 blob 目录 `{SOURCE_BLOB_DIR}`；receipt 目录 `{RECEIPT_DIR}`。

开始前完整阅读 `{SKILL_ROOT}\references\evidence-decisions.md` 与 `{SKILL_ROOT}\references\evidence-bundle-contract.md`。发现未替换的 `{...}` 占位符立即返回错误，不猜路径。以下脚本全部从 `{SKILL_ROOT}\scripts\` 调用，不使用裸文件名。

1. 使用 `{SKILL_ROOT}\scripts\collect_model_inventory.py` 生成 final 与各轮 inventory，并传 `--task-root`、`--model-metadata` 和所有已绑定 `--round`；使用相对路径、大小和 SHA-256，不手抄清单。初始化骨架只能在绑定完全一致时由工具替换。
2. 每条 rubric 只用对应轮结束状态判断自主产出；最终产物只作后续状态或修复轨迹。
3. 每条 rubric 写一条 `rubric-evidence.jsonl`。0/1 必须有方向匹配证据；不确定写 null。
4. 使用 `{SKILL_ROOT}\scripts\extract_excerpt.py` 并传 `--blob-dir {SOURCE_BLOB_DIR} --bundle-root {BUNDLE_ROOT}`，取得连续摘录、行号、文件哈希和 `source_blob_path`；你只补 evidence_id、方向和事实，不手算摘要。没有 blob 的静态证据无效。
5. 仅在六项安全条件全部满足时运行脚本或探针；否则记录跳过。
6. 仅当 `{MODEL_METADATA}` 已绑定对话源时，使用 `{SKILL_ROOT}\scripts\summarize_conversation.py`，并传 `--task-root`、`--model-metadata`、`--blob-dir`、`--bundle-root` 生成稳定对话索引与原 JSON blob；再基于 msg_ref 标注首轮提问、自测、妥协、决策和修复事件。脚本明确为 null 的 debug 统计不得改成猜测值。
7. 从 `记录.txt` 逐字摘录；冲突建立裁定建议，不自行消解。
8. 只校验你负责的文件字段、JSONL 可解析性和引用；不得运行或解释全包 validator 的就绪状态。全包 validator 由主 Agent 在所有模型完成后统一运行。不得用猜分修复语义未知。

返回模型 ID、rubric 记录数、0/1/null 数、证据数、测试执行/跳过、裁定项、证据缺口和验证错误。不得只说“已完成”。
