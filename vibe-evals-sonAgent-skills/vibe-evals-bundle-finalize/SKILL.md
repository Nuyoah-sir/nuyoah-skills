---
name: vibe-evals-bundle-finalize
description: Use when a Vibe Evals ZIP from another machine must be routed by schema — verify and render a form-ready bundle without any local adjudication, or finalize a v1 evidence bundle into scored rubric JSONs, feedback artifacts, and the V2.1 multi-model scoring form. Do not use on an unvalidated raw report alone.
---

# Vibe Evals 证据包本机汇总

## 第一步永远是按 schema 分流

拿到任何 ZIP 之前先做这三步，顺序不可变：

1. `verify_sidecar(archive, sidecar, expected_sha256)`。**在读取任何 ZIP 成员或中央目录之前**完成 sidecar 与期望摘要校验；这一步失败就停止，不要"先看看里面有什么"。
2. 只有第 1 步通过后，才调用 `detect_artifact_kind`：它会先完整预检中央目录（路径穿越、大小写碰撞、符号链接、加密成员、压缩炸弹），再做有界的根清单解析，并且只认三种根清单：`FORM-READY.json`、`MANIFEST.json`、`DELTA.json`。
3. 按检测结果分流：

   - `vibe-evals-form-ready-bundle/2.0.0` → 走 [references/verify-and-render-runbook.md](references/verify-and-render-runbook.md) 的校验与渲染路径（本机**不做任何裁定**）。
   - `vibe-evals-evidence-bundle/1.0.0` → 走既有的 prepare/finalize 路径。
   - `vibe-evals-evidence-delta/1.0.0` → 走既有的补证合并路径。
   - 其他版本或未知 schema → 停止，不要回退到旧路径。

同时出现两个根清单、归一化重名、未知 schema、或任何 v2 错误，一律停止。**不允许**"看不懂就当 v1 处理"。

## v2 包在本地被禁止的动作

处理 form-ready 包时，本机**不得**创建或修改：人工裁定清单、补证请求（`evidence_requests.json`）、人工决定文件（`human-decisions.json`）、本地人工证据（`local-human-evidence.jsonl`）、任何替换分数、任何替补图片。

远端给出的分数与裁定就是最终值；本机只校验、只渲染、只出收据。若包没闭合，正确动作是**停下并要求远端补齐**，不是本机替它判。

唯一允许的成功产物：确定性渲染出的表单、远端报告与热力图的副本、以及 `verification-receipt.json`。

## 核心原则

先证明证据包可用，再判断分数；先逐模型独立给总体印象，再生成 Rank。远端机器建议不是人工裁定，远端 `READY.json` 不是本机验证的替代品。

按顺序完整阅读：

1. [references/finalization-runbook.md](references/finalization-runbook.md)：prepare、补证循环和 finalize 的手把手流程。
2. [references/finalization-decisions.md](references/finalization-decisions.md)：证据可信度、冲突、人工裁定和停止条件。
3. [references/v21-form-rules.md](references/v21-form-rules.md)：总体印象、Rank、维度门控、文字反馈和标签规则。
4. [references/evidence-bundle-contract.md](references/evidence-bundle-contract.md)：数据契约。
5. [references/minimal-examples.md](references/minimal-examples.md)：人工证据与决定文件示例；只学结构，不复制示例事实。

## 强制门槛

- 只收到 `反馈报告.md`、截图或散落 JSON 时停止，要求 evidence bundle；不得降级硬填。
- 校验 ZIP SHA-256，安全解压，再在本机重跑 validator。远端 validation report 只作记录。
- `incomplete` 不进入评分；生成错误清单并要求重新导出。
- `ready_for_local_review` 先生成裁定/补证材料。基包保持不可变；所有 pending、null、human_check 和 material gap 都必须在 `human-decisions.json` 中逐项闭环，任何一项缺失都禁止生成最终评分表单。
- 人工裁定写入独立 `human-decisions.json`，不修改证据包。
- 每个最终 0/1 都有非空 reason，并引用封印基包内 evidence_id 或经校验的同模型同 rubric `LOCAL-*` 人工证据。scored rubric 字段必须严格等于基线字段 + `score` + `reason`。
- 原题包、基包、远端草稿和金标准只读；输出写入全新目录。
- `vibe-evals-scoring` 的 Rubric 表现排序只是证据，不是 V2.1 最终 Rank。

## 两种模式

### Prepare

```powershell
$SkillRoot = Join-Path $env:USERPROFILE '.codex\skills\vibe-evals-bundle-finalize'
$BundleRoot = '<全新解压目录>'
$ReviewRoot = '<全新review输出目录>'
py "$SkillRoot\scripts\extract_artifact.py" bundle "<证据ZIP>" "<证据ZIP.sha256>" "$BundleRoot"
py "$SkillRoot\scripts\validate_bundle.py" "$BundleRoot"
py "$SkillRoot\scripts\prepare_local_review.py" "$BundleRoot" "$ReviewRoot"
```

退出码 2 时，把《人工裁定清单.md》交给用户。可以把 `evidence_requests.json` 复制回第三方机器运行 `vibe-evals-son-evidence-supplement`。未收到人类决定前不要替人填写 resolution。

### Finalize

所有 pending、人工检查和 null 已处理后，根据 [templates/human-decisions.schema.json](templates/human-decisions.schema.json) 写 `human-decisions.json`，再运行：

```powershell
$SkillRoot = Join-Path $env:USERPROFILE '.codex\skills\vibe-evals-bundle-finalize'
$BundleRoot = '<已安全解压并重验的bundle目录>'
$FinalRoot = '<全新final输出目录>'
$DecisionFile = '<human-decisions.json绝对路径>'
py "$SkillRoot\scripts\finalize_scores.py" "$BundleRoot" "$DecisionFile" "$FinalRoot"
py "$SkillRoot\scripts\verify_final_artifacts.py" "$BundleRoot" "$DecisionFile" "$FinalRoot"
```

`verify_final_artifacts.py` 先机械确认 scored rubrics、决定、审计、证据外键和总分一致。通过后按模板生成 `反馈报告.md` 与 `rubrics打分热力图.html`；热力图每条 rubric 行和模型分数格必须保留模板规定的 `data-rubric-id`、`data-model-id`、`data-score`。先运行 `verify_supporting_outputs.py` 检查占位符、模型总分、热力图行与格，再参照 [templates/form-input.example.json](templates/form-input.example.json) 写出满足 [templates/form-input.schema.json](templates/form-input.schema.json) 的 `form-input.json`。示例中的模型 ID 和文字全是占位内容，必须替换。评分表单禁止手抄模板，必须确定性渲染并复验：

```powershell
$FormInput = '<form-input.json绝对路径>'
$FormOutput = Join-Path $FinalRoot 'Vibe Evals评测材料\<题目>-评分表单.md'
py "$SkillRoot\scripts\verify_supporting_outputs.py" "$BundleRoot" "$DecisionFile" "$FinalRoot" "$FinalRoot\反馈报告.md" "$FinalRoot\rubrics打分热力图.html"
py "$SkillRoot\scripts\render_v21_form.py" render "$BundleRoot" "$DecisionFile" "$FinalRoot" "$FormInput" "$FormOutput"
py "$SkillRoot\scripts\render_v21_form.py" verify "$BundleRoot" "$DecisionFile" "$FinalRoot" "$FormInput" "$FormOutput"
```

渲染器先重放 `finalize_scores` 并核对决定链，再从 scored rubrics 机械计算总分，以总体印象为第一排序键、同分时 Rubric 总分决胜，自动处理并列和 N=1，强制每个数值建议带“待人工确认”、N/A 不带数字，并拒绝标签库外标签。`form-input.json` 的总体印象、适用维度、Pros/Cons/Style 都必须提供本模型 evidence ID；题目级结论也要给证据引用。Pros 至少有一条 support，Cons 至少有一条 refute/confirm_missing。机器只能校验引用归属、方向与结构，语义相关性仍须逐项人工核对。

## 最终检查

- 每模型 × 每 rubric 得分齐全，证据引用存在。
- reason 为简洁事实句，含 file:line、实测、人工记录或消息索引。
- 报告、JSON、人工文档、热力图总分一致。
- 无未处理裁定；热力图行数等于 rubric 数。
- 所有模型先独立评总体印象，再按总体印象、可比 Rubric 表现得分生成 Rank。
- 维度 N/A 有依据；所有数字建议值写“待人工确认”。
- Pros/Cons/Style 符合速记风格；标签只来自标签库且有证据。

任何一项失败都不得把产物称为最终稿。
