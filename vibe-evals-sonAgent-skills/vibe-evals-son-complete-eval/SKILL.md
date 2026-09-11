---
name: vibe-evals-son-complete-eval
description: Use when a third-party machine must complete an entire Vibe Evals cross-machine evaluation from the raw task package and hand back one sealed form-ready bundle. Do not use for rubric design, question polishing, local form filling, or the legacy v1 evidence-only export.
---

# Vibe Evals 远端完整评测

这个技能让远端机器把整件事做完：从原始题目包开始，取证、渲染、观测、裁定、评分、演示产物，直到封出一个 `FORM_READY_SEALED` 包交给本机。本机只做校验与渲染，不再做任何裁定。

## 正常交付的定义

正常交付只有一种：命令 `package` 退出码 **0**，并产出一个 ZIP 与其 sidecar，包内 `READY.json` 的 `status` 是 `ready_for_form`。

以下都**不是**交付：

- 退出码 2（合法但未闭合）、3（校验失败）、4（源变更终态）、5（需要真人）；
- 任何未闭合的分数、待裁定项、人工复核项或材料缺口；
- 你口头说明"完成了"，但 `validate` 或 `package` 没有给出退出码 0。

未完成的部分留在远端继续处理，不要把它包装成已完成。

## 不可突破的边界

- 机器身份与人的身份永不混同。机器读图只能写 `VIS-*`，真人见证只能写 `HUM-*`，你不得代写 `decided_by: human`、不得伪造真人确认词、不得把机器的结论记成人的结论。
- 需要真人时返回退出码 5 并停下等人，不允许"代替人确认"继续。
- 主动内容（HTML/JS/应用）必须过六条安全闸门（真实隔离、禁网、无凭据、可丢弃副本、最小权限、不执行模型代码），任何一条不满足就不能执行，只能记录为跳过并说明原因。
- 只有校验器能授权封包。你的判断、你的总结、你写的任何 JSON 都不算授权。
- 所有产物只能由脚本写入。不要手改 `MANIFEST.json`、`rubric-index.json`、`prompt-requirements.jsonl`、`rubric-evidence.jsonl`、`final-decisions.json`、`presentation-input.json`、`READY.json` 或任何校验清单。
- 原题包只读。所有输出写入全新的运行目录。

## 入口和退出

入口是 `complete_eval.py start`，一次调用同时做发现、内层包初始化、模型清单与对话摘要。它**不接受**一个现成的 v1 ZIP 作为正常入口。

按顺序读：

1. [references/complete-eval-runbook.md](references/complete-eval-runbook.md)：每次运行都必须照它逐阶段执行，含每个阶段的命令、期望退出码、决策分支、停止条件与恢复方式。
2. [references/observation-decisions.md](references/observation-decisions.md)：遇到缺素材、双读冲突、主观条目、政策歧义、缺人工时查这张表。
3. [references/visual-observation-protocol.md](references/visual-observation-protocol.md)：写任何 `VIS-*` 记录前必读。
4. [references/form-ready-bundle-contract.md](references/form-ready-bundle-contract.md)：写任何外层产物前阅读字段与引用规则。

## 每次运行的开场与收尾

开场：确认题目包绝对路径与输出根目录，然后执行 `complete_eval.py start --task-root <绝对路径> --output-root <绝对路径>`，把返回的 RUN_ID 记下来。

收尾：执行 `complete_eval.py package --run <运行目录> --output-zip <绝对路径>`。只有退出码 0 才向用户报告完成，并同时给出：RUN_ID、ZIP 绝对路径、sidecar 绝对路径、ZIP 的 SHA-256、以及 `validate` 报告的计数（模型数、rubric 数、证据条数、未闭合计数必须全为 0）。

每完成一个阶段，用一句话报告：阶段名、执行的命令、退出码、生成的产物路径。不要复述本文件。
