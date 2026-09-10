---
name: vibe-evals-son-evidence-export
description: Use when a third-party machine has a complete Vibe Evals Coding Agent task package and must export trustworthy, portable scoring evidence for review on another machine. Do not use to make final human rulings or the final V2.1 Rank.
---

# Vibe Evals 远端证据导出

## 核心原则

证据 ZIP 是下游评分的事实数据库，不是报告附件。远端只做“发现、取证、实测、摘录、机器建议和校验”；本机或人类做最终口径裁定、最终 0/1、总体印象和 Rank。

先读：

1. [references/step-by-step-runbook.md](references/step-by-step-runbook.md)：每次完整运行都必须按此执行。
2. [references/evidence-decisions.md](references/evidence-decisions.md)：遇到缺材料、证据冲突、轮次问题、测试限制或主观条目时必须查此表。
3. [references/evidence-bundle-contract.md](references/evidence-bundle-contract.md)：写任何 JSON/JSONL 前阅读字段与引用规则。
4. [references/minimal-examples.md](references/minimal-examples.md)：第一次运行或字段拿不准时照例子核对，但不要把示例事实复制进真实包。

## 不可突破的边界

- 原题包只读；所有输出写入全新运行目录。
- `没找到` 不等于 `确认没有`。不充分或歧义证据使用 `suggested_score: null`，绝不为填满表格而写 0。
- Rn rubric 只用第 n 轮结束状态判断“未经纠正时”；后续修复不能倒证前轮。
- 模型测试和会加载模型代码的探针是不可信代码。没有真正隔离、禁网、无凭据、可丢弃副本、最小权限时禁止执行。
- 静态证据、自测与 `记录.txt` 冲突时全部保留并建立 `ADJ-*`；不得选择顺眼的一方覆盖另一方。
- 不把完整源码树或完整大对话默认塞进 ZIP。携带评分所需的连续摘录、稳定消息索引、原文件哈希和原始测试日志。
- 第三方 Agent 不得把自己的判断写成 `decided_by: human`，不得生成最终 V2.1 Rank。
- 多个模型必须独立取证。一个证据工作者一次只负责一个模型，只写该模型目录。

## 入口和退出

用户至少提供题包根路径与全新输出根目录。所有命令从技能绝对路径调用；不要假设当前目录正好是技能目录：

```powershell
$SkillRoot = Join-Path $env:USERPROFILE '.codex\skills\vibe-evals-son-evidence-export'
$RunRoot = '<输出根目录>\<全新run_id>'
$BundleRoot = Join-Path $RunRoot 'bundle'
py "$SkillRoot\scripts\discover_task_package.py" "<题包根路径>" --output "$RunRoot\discovery.json"
py "$SkillRoot\scripts\initialize_bundle.py" "<题包根路径>" "$RunRoot\discovery.json" "$BundleRoot"
```

发现报告 `ok=false` 时停止，修好源材料后必须使用新 run_id；不得在失败运行目录中混合续跑。初始化只生成待填写骨架，不能直接交付。完成规范映射和所有模型证据后，先确认源未变化，再封包：

```powershell
py "$SkillRoot\scripts\verify_source.py" "<题包根路径>" "$BundleRoot\source-freeze.json"
py "$SkillRoot\scripts\package_bundle.py" "$BundleRoot" "$RunRoot\<题目>-evidence-bundle.zip"
```

固定目录含义：`RunRoot` 是一次运行目录；`BundleRoot` 只放包内容；ZIP 和 `.sha256` 放在 `RunRoot`，不得放进 `BundleRoot` 形成自包含递归。

退出码：`0=ready_for_form`，`2=ready_for_local_review`，`3=结构或证据错误`，`4=输入运行中变化`。退出码 2 是可交接状态，不代表失败；必须同时列出待裁定项、主观检查项和证据缺口。

## 完成回复

只汇报：运行目录、模型数、rubric 数、证据条数、动态测试状态、待裁定项、证据缺口、就绪级别、ZIP 绝对路径、ZIP SHA-256、验证退出码。不要用“全部真实”“完全正确”等无法由结构校验证明的措辞。

## 红线自检

出现以下想法立即停下并查判断表：

- “格式只收 0/1，未知先填 0。”
- “最终代码已经修好，可以给 R1 得分。”
- “测试文件在这里，所以应该通过。”
- “用户说不要停，因此可以在宿主机直接跑。”
- “普通 PowerShell 就算隔离环境。”
- “人工记录与代码冲突，我挑更可信的一边。”
- “把整个题包压缩进去最省事。”
- “子代理说完成，所以可以生成 READY。”

只有校验器可以决定包是否就绪。
