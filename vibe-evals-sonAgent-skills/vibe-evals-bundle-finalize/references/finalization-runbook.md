# 本机汇总手把手运行手册

## 1. 接收与解压

要求同时拿到 `*.zip` 和 `*.zip.sha256`。使用技能目录的 `extract_artifact.py bundle`；它先核对文件名与哈希，再拒绝绝对路径、盘符、`..`、重复 ZIP 成员、符号链接和超大解压。

解压到全新临时目录。确认存在 `MANIFEST.json`、`READY.json`、`integrity/files.sha256` 和 `integrity/validation-report.json`。文件存在不等于可信，下一步仍须重验。

## 2. 本机重验

运行 validator。按结果处理：

| 结果 | 处理 |
|---|---|
| incomplete / exit 3 | 停止评分；列出错误码、路径、修复建议，要求远端重导出或补正确包 |
| ready_for_local_review / exit 2 | 进入 prepare；不得生成最终表单 |
| ready_for_form / exit 0 | 仍检查人工策略与最终 rubric 决定，然后可 finalize |

哈希只能证明包内内容未变，不能证明第三方 Agent 的语义判断正确。关键争议按证据可信度抽查。

## 3. Prepare

运行 `prepare_local_review.py`。它会生成：

- `local-review.json`：待裁定、人工检查、补证请求和能否 finalize；
- `人工裁定清单.md`：给用户看的问题；
- `evidence_requests.json`：可复制回远端的小文件。

清单还会列出 `material_gaps`，例如某模型缺对话或某轮快照。此类缺口不是普通 warning：若无法补回，必须由人类在 `material_gap_resolutions` 中逐项选择 `proceed_with_limitation`，说明不评价哪些能力及原因，不能静默略过。

逐项向用户呈现问题、证据、建议口径、备选口径和各模型分数影响。不要只问“判 0 还是 1”，要让用户看懂两种口径的含义。

主观/视觉/手感项需用户或明确的人工作者按步骤实测。记录操作者、步骤、预期、观察、时间和对应版本。不得把 Agent 看代码的想象写成“人工实测”。

## 4. 补证循环

若问题是事实缺失而非政策裁定，把 `evidence_requests.json` 交远端 supplement。收到 delta 后：

1. 保留当初发给远端的原始 `evidence_requests.json`，不得用 delta 内副本覆盖；
2. 运行 `extract_artifact.py delta <delta.zip> <delta.zip.sha256> <全新delta目录> --base <基包目录> --requests <原始evidence_requests.json>`；
3. 运行 `merge_delta.py <基包目录> <delta目录> <原始evidence_requests.json> <全新合并包目录>`；
4. 运行 `package_bundle.py <合并包目录> <全新合并包.zip>` 重新封印；
5. 对重新解压的合并包再次运行 prepare。

合并器把新证据写入该模型该 rubric 的规范 `rubric-evidence.jsonl`，同时保留 supplement 审计文件；不是只把附件放在旁边。

远端 `verify_source.py` 以退出码 4 报告 `source_changed` 时，表示旧包与当前源不再是同一批次。此时远端必须停止，不能生成合法 delta；本机也不能合并任何来自该次尝试的文件，必须重新完整 export。

## 5. 人工决定文件

`human-decisions.json` 必须覆盖每模型每 rubric。用户明确接受机器建议时写 `decided_by=accepted_machine`；用户改判或裁定时写 `decided_by=human`。两者都必须引用 evidence_id。

reason 模仿用户记录风格：短、事实化、证据在括号内。不要写“综合考虑后认为较好”。

待裁定项 resolution 记录用户原话或忠实短述、decider、decided_at、decision_source、统一口径和各模型最终分，并精确覆盖受影响的模型/rubric。material gap resolution 同样记录 gap_id、`proceed_with_limitation`、reason、decider、decided_at。Agent 不得自行写 `decided_by=human`。

## 6. 转录 scored rubrics

运行 `finalize_scores.py`。工具检查 package ID、裁定覆盖、模型/rubric 覆盖、0/1 值、reason、evidence 引用和字段集合。

输出每模型 `rubrics-{model_id}.json`、`decision-audit.json` 和 `scoring-summary.json`。紧接着运行 `verify_final_artifacts.py`，交叉核对基线字段、决定、同 rubric 证据引用和三路总分。失败时修正决定文件或补证，不直接手改输出 JSON 绕过检查。

## 7. 反馈报告与热力图

按 `反馈报告模板.md` 生成：Rubric 表现汇总、硬失败/分水岭、4W1H、差异或单模型表现、打分明细、最终口径、出题启示和关键实测证据。

N=1 时明确单模型无同场比较，删除分差、并列和比较措辞。N≥2 的表现顺序只按可比 Rubric 口径，名称始终是“Rubric 表现排序参考”。

热力图只读 scored rubrics 与报告表现汇总。绿=1、红=0；行数等于 rubric 总数；N=1 不生成虚构比较列。

每条数据行使用 `<tr data-rubric-id="...">`，每个得分格使用 `<td data-model-id="..." data-score="0|1" ...>`。生成后运行 `verify_supporting_outputs.py <bundle> <human-decisions> <scored目录> <报告> <热力图>`；它会先重放最终决定，再拒绝遗留模板占位符、错配模型总分、缺 rubric 行、孤儿行/格或错位得分格。

## 8. V2.1 表单

先逐模型独立给总体印象建议分，再把 G1/G2/G3、门控维度、Pros/Cons/Style、标签和题目级文字写入符合 `form-input.schema.json` 的 `form-input.json`。所有模型级判断都填写 `evidence_refs`，引用同一模型真实存在的远端或 `LOCAL-*` 证据；题目级结论也填写证据引用。不要在这个阶段手工排序，也不要用无关 rubric 的证据装饰文字。

运行 `render_v21_form.py render <bundle> <human-decisions> <scored目录> <form-input> <输出>` 生成评分表单，再把首参数改为 `verify` 做逐字节复验。渲染器负责重验决定链、总体印象第一排序键、同分 Rubric 决胜、双同分并列、N=1、N/A、待人工确认和标签白名单。verify 未通过时不得手改 Markdown 绕过；修正 `form-input.json` 后输出到新路径重渲染。

评分表单是建议稿，不把机器建议伪装成人工最终确认；但它依赖的 rubric 0/1 与裁定必须已经闭环。

## 9. 交稿复核

重新从 scored rubrics 计算每模型总分和分率，对比报告、热力图和经确定性渲染的表单。检查所有证据 ID、总条数、模型名、轮次、门控、Rank 键和标签合法性。

最后输出文件清单和仍需用户确认的 pointwise 建议值。若存在任何未决 rubric 证据或口径，退回 prepare 状态。
