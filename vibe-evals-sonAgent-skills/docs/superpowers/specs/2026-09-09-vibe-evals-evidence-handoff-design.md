# Vibe Evals 跨机器证据移交技能设计

## 目标

让第三方机器承担题包探测、rubric 审查、模型源码取证、安全实测、对话解析、轮次快照比较和机器预打分等高耗时工作；用户只需复制一个经过校验的证据 ZIP 到本机，本机即可进行人工裁定、最终 rubric 转录和 V2.1 评分表单汇总。

最终评分表单的可信度以证据 ZIP 为基础。报告中的任何分数、判断、标签或排序都必须能追溯到包内证据，不能只依赖第三方 Agent 的自然语言结论。

## 架构

交付三个独立 Codex skills：

1. `vibe-evals-son-evidence-export`：第三方机器的常规入口。探测题包、规范化输入、逐模型取证、生成机器预判、校验并打包。
2. `vibe-evals-son-evidence-supplement`：第三方机器的补证入口。读取本机生成的 `evidence_requests.json`，只补指定证据并输出增量包。
3. `vibe-evals-bundle-finalize`：本机入口。验证基础包和增量包，生成人工裁定材料，待裁定完成后生成 scored rubrics、反馈报告、热力图和 V2.1 评分表单。

三个技能以 `vibe-evals-evidence-bundle` schema v1 为稳定接口。JSON/JSONL 是事实源，Markdown 是便于人工阅读的派生产物。

## 证据包状态

- `incomplete`：结构、哈希、覆盖、引用或输入一致性存在错误，不允许进入评分。
- `ready_for_local_review`：证据包结构完整，但存在人工裁定项、主观实测项或明确证据缺口；本机可以接手。
- `ready_for_form`：所有 rubric 的最终 0/1 已由人工确认或有无歧义证据，且报告数字一致；可以生成评分表单建议稿。

状态由校验脚本机械推导，Agent 不得自行宣称就绪。

## 证据判断原则

### 证据不是“是否找到”

- 未搜索到实现不等于确认未实现。只有文件清单、符号检索、入口追踪等形成“确认缺失”证据时，才可倾向 0。
- 证据不足时使用 `suggested_score: null` 和 `insufficient_evidence`，不得为填满表格而猜分。
- 判 1 至少引用一条支持证据；判 0 至少引用一条反驳证据或确认缺失证据。

### 轮次必须匹配

- Rn rubric 的“未经纠正时”证据必须来自第 n 轮结束快照。
- 后续轮最终代码只能说明修复轨迹，不能倒证早期轮次得分。
- 对应轮快照缺失时，相关条目标记 `partial` 或 `missing`，不能用最终产物伪装完整证据。

### 静态、实测与人工记录

- 可操作行为优先使用安全实测；静态代码存在不等于行为达标。
- `记录.txt` 是权威人工观察，但不得篡改原文。
- 静态证据、自测和人工实测冲突时保留各方证据，并创建待裁定项；不得静默覆盖。
- Subjective、视觉、可玩性或交互体验条目可以给机器倾向，但必须标记 `human_check_needed`。

### 不可信代码

- 模型自带测试和会加载模型产物的探针均视为不可信代码。
- 只有真正隔离、禁网、无凭据、可丢弃副本和最小权限全部满足时才执行。
- 无安全沙箱时跳过动态执行，记录 `skipped_no_isolation`；这不是自动判 0，也不能伪称已实测。
- 检测到删除、联网、跨目录访问、凭据读取或不可控外部副作用时停止该动态测试并登记安全事件。

### 人工裁定边界

第三方 Agent 不得最终决定：rubric 增删改、歧义口径、冲突证据采信、Subjective 条目、兜底总分 0、V2.1 总体印象、门控维度、最终 Rank、洞察和标签。它可以给建议，并必须清楚标注建议依据与备选口径。

## 证据包目录

```text
evidence-bundle/
├─ MANIFEST.json
├─ READY.json
├─ inputs/
│  ├─ prompt.md
│  ├─ prompt-requirements.jsonl
│  ├─ rubrics/
│  ├─ rubric-index.json
│  └─ record.txt
├─ review/
│  ├─ rubric-review.json
│  ├─ 对每条rubric的理解.md
│  └─ pending-adjudications.json
├─ models/{model_id}/
│  ├─ model.json
│  ├─ inventory.json
│  ├─ rubric-evidence.jsonl
│  ├─ conversation-summary.json
│  ├─ conversation-excerpts.jsonl
│  ├─ snapshot-diff.json
│  ├─ human-observations.jsonl
│  ├─ tests/
│  └─ probes/
├─ drafts/
│  ├─ machine-prescore.json
│  ├─ 预打分.md
│  └─ 人工打分文档.md
├─ integrity/
│  ├─ files.sha256
│  └─ validation-report.json
└─ logs/
```

完整源码和完整大型对话默认不进入 ZIP。所有用于评分的源码证据必须带相对路径、原文件 SHA-256、行号、连续摘录和摘录哈希；测试必须带原始 stdout/stderr；对话必须带稳定消息索引和必要原文摘录。

## 数据流

1. 远端 export 对关键输入做只读探测和初始哈希。
2. 脚本生成 rubric、prompt requirement、模型和对话的规范化索引。
3. Agent 按模型独立取证，每个模型只写自己的目录。
4. 脚本检查每模型 × 每 rubric 恰好一条记录、引用可解析、总分可重算。
5. 包装器生成临时 ZIP，解压到临时目录重新校验，通过后写正式 ZIP 和 SHA-256。
6. 本机 finalize 重跑校验；有待裁定项时先生成裁定表，不生成最终表单。
7. 证据不足时 finalize 生成 `evidence_requests.json`；远端 supplement 输出不可覆盖基包的 delta ZIP。
8. 本机合并基包与 delta，在所有裁定完成后生成最终产物。

## 失败与恢复

硬停止包括缺 prompt/rubrics/模型输出、rubric ID 重复、模型无法唯一映射、输出可能覆盖金标准、输入运行中变化、证据引用断裂、无证据却给分、危险测试和包校验失败。

允许降级但必须记录的情况包括缺 `record.txt`、缺部分快照、对话局部解析失败、无安全沙箱和可选字段说明缺失。

`run-state.json` 只提供人工可读的阶段记录，不构成自动续跑承诺。不能逐项证明阶段输入/输出摘要未变时，必须换新 run_id 完整重跑；任一源输入变化都会使整包与下游汇总失效。瞬时工具失败最多原命令重试两次，材料缺失、安全事件和语义裁定不自动重试。

## 验证

每个 skill 先做无技能基线压力测试，再做带技能前向测试。确定性脚本使用标准库单元测试覆盖：路径安全、哈希、JSONL、rubric 覆盖、外键、分数域、轮次证据、冲突裁定、就绪状态、ZIP 路径穿越、断点状态和包解压复验。

最终验收要求：三个 skill 均通过 Codex skill 快速校验；Python 测试全绿；合成题包可完成 export → validate → supplement → finalize；损坏包和未决裁定包被正确拒绝；可分发 ZIP 解压后文件与校验和一致。

## 非目标

- 不替用户最终确认任何 V2.1 分数或 Rank。
- 不在第三方宿主机无隔离执行模型代码。
- 不复制完整题包来规避证据契约。
- 不修改原 rubrics、原题包或已有金标准产物。
