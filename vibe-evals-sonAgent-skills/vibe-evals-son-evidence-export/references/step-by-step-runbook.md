# 远端证据导出手把手运行手册

## 0. 接收参数

向用户确认两个绝对路径：题包根路径、输出根路径。输出根路径下为本次创建唯一 `run_id`，格式建议 `YYYYMMDD-HHMMSS-随机6位`。固定为 `<输出根>\<run_id>\bundle` 存证据包目录，ZIP 放在 `<输出根>\<run_id>`。输出目录不得位于题包内。除非用户明确给出上一批次理解文件，否则不猜上一批次路径。

先完整定义公共变量；不要在后文临时发明路径：

```powershell
$SkillRoot = Join-Path $env:USERPROFILE '.codex\skills\vibe-evals-son-evidence-export'
$TaskRoot = '<题包绝对路径>'
$OutputRoot = '<输出根绝对路径>'
$RunId = Get-Date -Format 'yyyyMMdd-HHmmss-ffffff'
$RunRoot = Join-Path $OutputRoot $RunId
$BundleRoot = Join-Path $RunRoot 'bundle'
$DiscoveryFile = Join-Path $RunRoot 'discovery.json'
$BundleZip = Join-Path $RunRoot '<题目>-evidence-bundle.zip'
New-Item -ItemType Directory -Path $RunRoot -ErrorAction Stop | Out-Null
py "$SkillRoot\scripts\discover_task_package.py" "$TaskRoot" --output "$DiscoveryFile"
if ($LASTEXITCODE -ne 0) { throw 'discovery 失败；停止本批次并换新 run_id' }
py "$SkillRoot\scripts\initialize_bundle.py" "$TaskRoot" "$DiscoveryFile" "$BundleRoot"
if ($LASTEXITCODE -ne 0) { throw '初始化失败；停止本批次' }
```

如果输出目录已存在以下任一文件，换新运行目录：`MANIFEST.json`、`READY.json`、`反馈报告.md`、`rubrics-*.json`、`*-评分表单.md`。不覆盖金标准，也不把旧文件当本批结果。

## 1. 只读探测

运行 `discover_task_package.py`，逐项向用户报告：

| 材料 | 必需性 | 缺失处理 |
|---|---|---|
| 权威 `prompt.md` | 必需 | 自动忽略模型最终输出目录与严格 `Rn` 快照目录中的副本；过滤后仍为 0 或多份才整批停止 |
| rubrics 基线 | 必需 | 整批停止；优先 `合并rubrics*`，其次 `rubrics*`，再 `原rubrics*` |
| `*-模型输出/` 与至少一个模型 | 必需 | 整批停止 |
| 对话记录 | 条件必需 | 可继续静态取证；过程、G3、R1 门控标证据缺口 |
| R1/R2…快照 | 条件必需 | 可继续最终产物取证；对应轮“未经纠正时”条目不得宣称完整 |
| `记录.txt` | 可选但权威 | 缺失写 warning；不得伪造人工实测 |
| `rubrics说明.xlsx` | 可选 | 缺失时沿用实际 JSON 字段，不猜字段语义 |

探测器要求权威 prompt、每轮 rubric、模型最终目录、轮次目录和对话文件都能唯一绑定。模型最终输出目录及严格命名为 `R1`、`R2`…的快照目录中的 `prompt.md` 视为运行副本，会从权威 prompt 候选中排除，但仍进入源清单和哈希冻结。对话文件按 `*模型对话*.json` 收集；非标准名字会告警而不是漏掉。多个 `记录.txt`、同一模型多个候选最终目录、同轮多个快照或多份无法归属的对话都属于歧义，必须停止并让人整理目录，不能选排序第一项。探测成功后 `initialize_bundle.py` 会重新探测并比对摘要，随后保存完整源清单、绑定、大小、mtime 和 SHA-256。后续每个阶段前运行 `verify_source.py`；变化则退出码 4，整包及下游汇总失效。

## 2. 冻结规范化输入

只复制以下原始字节到包：`prompt.md`、选定 rubrics JSON、存在时的 `记录.txt`。不要重新排版或重存原文件。

原 rubric 项里的 `round` 可写成正整数、`"R1"`/`"r1"` 或纯数字字符串。初始化器只在生成的 index、evidence 和 manifest 中规范化为正整数，复制的 rubric JSON 保持原字节；格式无法识别或与文件轮次冲突才停止。不要为迎合工具修改题包中的 `round` 字段。

生成以下规范化文件：

- `prompt-requirements.jsonl`：每轮每条显式要求、必要隐含要求或上下文一行。
- `rubric-index.json`：rubric 原文、轮次、源文件、三源依据、criterion 哈希。
- `review/rubric-review.json`：废话、不足、冗余、覆盖缺口、修改建议。

逐条分类，不能保留初始化骨架中的 `candidate/needs_classification`：

- `explicit`：prompt 原文明确要求；`source.quote` 必须等于对应连续行原文，coverage 为 `mapped` 或 `gap`。
- `necessary_implicit`：完成显性任务不可缺少但原文未直接写出的要求；必须写具体 `rationale`，coverage 为 `mapped` 或 `gap`。
- `context`：背景、示例或非验收信息；coverage 必须为 `not_applicable`，不得硬配 rubric。

每个 mapped requirement 与 `rubric-index.json` 的 prompt basis 必须双向引用。每条 rubric review 必须恰好出现一次，形如 `{"rubric_id":"R1-01","finding":"supported","reason":"直接覆盖 P-R1-001。"}`，finding 与 rubric-index 的 `review.basis_status` 一致。每条 rubric 必须回落到 prompt、模型效果或明确的出题人思考；出题人思考只能来自用户或已提供材料。

默认新 rubrics 与基线逐字节一致。增删改仅写建议，不改 JSON，除非用户明确批准并留下批准记录。

## 3. 建立模型任务

对每个模型创建独立目录和独立证据任务。使用 [templates/model-evidence-agent-prompt.md](../templates/model-evidence-agent-prompt.md)，替换全部占位符后再派发。

每个工作者只接收一个模型的最终输出、对应对话、对应轮次快照、全量 rubric、对应人工记录和独立输出目录。禁止不同模型共写 `预打分.md`、`反馈报告.md` 或公共 JSON。

并发槽位不足时分批处理。并发不是完成条件；模型隔离和引用正确优先。

如果当前 Codex 没有子代理能力，就按模型目录排序逐个执行同一模板：先把当前模型绝对路径和公共只读文件路径写进任务记录；本阶段只读取该模型路径，输出后核对所有写入都位于它自己的 bundle 模型目录，再开始下一个模型。禁止在同一取证步骤里同时读取多个模型后凭记忆拆结果。所有模型完成后，由主 Agent 一次性运行全包 validator。

优先使用随技能提供的确定性工具，不自行发明字段格式。对 discovery 列出的每个模型重复下面完整模板；`$ModelId` 只取 `MANIFEST.json` 中的精确 ID：

```powershell
$ModelId = '<MANIFEST中的model_id>'
$ModelDir = Join-Path $BundleRoot "models\$ModelId"
$ModelMeta = Join-Path $ModelDir 'model.json'
$Meta = Get-Content -LiteralPath $ModelMeta -Raw | ConvertFrom-Json
$ModelOutput = Join-Path $TaskRoot ($Meta.source_output_root_label -replace '/', '\')
$InventoryArgs = @($ModelOutput, (Join-Path $ModelDir 'inventory.json'), '--task-root', $TaskRoot, '--model-metadata', $ModelMeta)
foreach ($Property in $Meta.round_source_labels.PSObject.Properties) {
  $RoundPath = Join-Path $TaskRoot ($Property.Value -replace '/', '\')
  $InventoryArgs += @('--round', "$($Property.Name)=$RoundPath")
}
& py "$SkillRoot\scripts\collect_model_inventory.py" @InventoryArgs
if ($LASTEXITCODE -ne 0) { throw "inventory 失败：$ModelId" }

# 每个静态证据都调用一次；下面路径和行号必须换成真实值。
$EvidenceReceipt = Join-Path $RunRoot "receipts\$ModelId-R1-01.json"
$Round1Path = if ($Meta.round_source_labels.'1') { Join-Path $TaskRoot ($Meta.round_source_labels.'1' -replace '/', '\') } else { $null }
if ($Round1Path) {
  py "$SkillRoot\scripts\extract_excerpt.py" "$Round1Path" 'src/app.js' 18 30 "$EvidenceReceipt" --blob-dir "$BundleRoot\evidence\source-blobs" --bundle-root "$BundleRoot"
  if ($LASTEXITCODE -ne 0) { throw "静态摘录失败：$ModelId/R1-01" }
}

# 只有 model.json 已绑定对话源时才运行；不得拿另一模型的对话代替。
if ($Meta.conversation_source_path) {
  $ConversationFile = Join-Path $TaskRoot ($Meta.conversation_source_path -replace '/', '\')
  py "$SkillRoot\scripts\summarize_conversation.py" "$ConversationFile" (Join-Path $ModelDir 'conversation-summary.json') --task-root "$TaskRoot" --model-metadata "$ModelMeta" --blob-dir "$BundleRoot\evidence\source-blobs" --bundle-root "$BundleRoot"
  if ($LASTEXITCODE -ne 0) { throw "对话摘要失败：$ModelId" }
}
```

初始化器已创建 inventory 骨架；collector 只会在模型和轮次绑定完全一致时替换这份骨架，其他已存在文件一律拒绝覆盖。没有某轮快照时 `model.json` 中不会有该绑定，不得用最终目录代替。摘录工具会同时产生 receipt 和内容寻址源码 blob；把 receipt 的字段原样写入 evidence，再补 evidence_id、direction 和 fact。没有 blob 的 static_line 不能交付。

## 4. 逐 rubric 取证

每条 rubric 恰好生成一条 `rubric-evidence.jsonl` 记录：

1. 固定 rubric 轮次、criterion 和判分对象。
2. 从该轮结束快照开始找证据；缺快照时标局限。
3. 搜索入口、调用链、状态读写和相关测试，避免只找到同名变量就判实现。
4. 对支持和反驳证据分别记录；不要只收集支持原先猜测的一面。
5. 可动态验证时先检查安全隔离，再运行。
6. `fact_summary` 只写观察事实，不写整体评价。
7. 根据判断表产生 `0/1/null` 倾向、置信度、局限和待裁定引用。

静态摘录必须连续且足以理解事实，通常 3–30 行；不得只摘一个标识符。摘录较长时缩小到最小充分上下文，不把整文件带回。

## 5. 对话和快照

大对话文件不得整份塞进模型上下文。先用 `summarize_conversation.py` 遍历 `data.conversations[].requests[].messages[]`，生成稳定 msg_ref 和机械统计；再基于这些引用标注首轮提问、自测/妥协/决策/修复事件。debug 次数无法机械确认时保持 null。

统计不可得填 `null` 并写原因，不能用 0 冒充。快照比较按相对路径和 SHA-256 生成 added/removed/changed；关键修改仍需具体摘录。

## 6. 测试和探针

先审阅脚本及调用链。普通 PowerShell、普通终端、新建文件夹、`venv` 和“我不会访问网络”的提示都不算隔离。只有平台已预先配置且能逐项证明：视为不可信、隔离、禁网、无凭据、可丢弃副本、最小权限，六项全部满足才执行。默认判断是“没有合格沙箱”，跳过所有会执行或加载模型代码的脚本/探针。

命令保存为 argv 数组。保存退出码、耗时、结构化断言、原始 stdout/stderr、实际执行脚本副本及哈希。每个 passed/failed 断言都写 `{rubric_id, expected, actual, passed}`；`script_path` 从模型目录起算，`script_sha256` 必须能重算。只看到测试文件但没有执行记录，不能写 passed。

无法隔离时：`status=skipped_no_isolation`；可继续静态取证；不能把未运行转成 0；必须实测的条目写 null 或人工检查。

探针不超过 100 行，自包含，文件名 `_verify_{model_id}_{rubric_id}.js`。会加载模型代码的探针遵守同一安全门槛。

## 7. 机器预判

- 1：至少一条支持证据，且无未处理关键反证或口径歧义。
- 0：至少一条反驳证据或“确认缺失”证据，不能只因没有搜到。
- null：证据不足、口径歧义、冲突未裁定、关键轮快照缺失或必须人工体验。

兜底总分 0 只标记 `fallback_zero_candidate`，附文件数、总字节、入口和核心链路事实，留给本机/人工确认。不要逐条伪造 0。

## 8. 汇总和校验

所有模型工作者完成后，主 Agent 从 JSON 生成草稿 Markdown，并检查每模型 × 每 rubric 覆盖、criterion、ID/外键、分数证据方向、轮次、人工冲突、安全声明、待人工状态和相对路径。只有主 Agent 运行全包 validator。

修复所有结构错误。语义歧义不能通过“选一个分数”修复。

## 9. 打包

运行 `package_bundle.py`。工具会生成校验报告、READY、文件哈希，创建 `.zip.partial`，解压复验后才改名为正式 ZIP，并生成 ZIP 的 `.sha256`。

没有 READY 的 ZIP 不可交付。`ready_for_local_review` 可以交付，但回复必须列出本机待处理事项。

## 10. 中断与重试

本技能没有自动续跑器，也不承诺仅凭 `run-state.json` 安全恢复。为了避免弱模型把两批源混在一起：

- discovery、初始化或源校验失败：保留旧目录作诊断，使用新 run_id 从头完整 export。
- 取证中断但题包未变：可以人工检查现有输出；只要不能逐项证明阶段输入/输出未变，就使用新 run_id 从头跑。
- 工具瞬时错误最多原命令重试 2 次；仍失败则停止并报告完整错误。
- 缺材料、危险脚本、语义裁定不自动重试。

任何时候都不要在失败目录中加入新源文件后继续封包。
