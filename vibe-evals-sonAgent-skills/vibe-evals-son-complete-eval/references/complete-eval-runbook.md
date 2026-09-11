# 逐步执行手册（远端完整评测）

这份手册写给不熟悉本流程的执行者。**逐阶段执行，不要跳步、不要并步、不要自己重新排序。** 每个命令都由脚本执行；你负责准备输入、读退出码、按分支决策、报告结果。

## 退出码含义

| 退出码 | 含义 | 你该做什么 |
|---:|---|---|
| 0 | 该阶段完成，可以进入下一阶段 | 记录产物路径，继续下一行 |
| 2 | 合法但未闭合 | 按该阶段的分支继续补，不要跳阶段 |
| 3 | 校验失败 | 读错误码，按对应分支修，不要绕过 |
| 4 | 源变更（终态） | 停止整个运行，报告 RUN_ID 与变更详情，换新 RUN_ID 重跑 |
| 5 | 需要真人 | 停下来找人，把命令交给真人执行；不要代替人确认 |

任何时刻你都不需要、也不允许手改 JSON。所有写入都通过命令。

## 阶段总表

| 阶段 | 输入 | 命令 | 期望退出码 | 产物 | 停止条件 | 失败恢复 | 进度报告 |
|---|---|---|---:|---|---|---|---|
| 1 发现与初始化 | 题目包绝对路径、输出根目录 | `complete_eval.py start --task-root <绝对> --output-root <绝对>` | 2 | `<run>/run-state.json`、`<run>/v1/` | 题目包路径不是绝对路径 | 修正路径后重跑；`RUN_EXISTS` 时换 RUN_ID | RUN_ID、`evidence_initialized`、v1 基线 derived 状态 |
| 2 评审 | 需求候选 JSONL、评审候选 JSON | `complete_eval.py accept-review --run <run> --requirements <r.jsonl> --review-items <i.json>` | 0 | 三个评审产物 | 无 | 退出码 3 时按错误码修正候选，**不要**改产物 | `review_complete`、rubric 条数 |
| 3 取证 | 工作者证据批次 JSONL | `complete_eval.py accept-evidence --run <run> --candidate <batch.jsonl>` | 0 或 2 | `v1/models/<model>/rubric-evidence.jsonl` | 2 表示仍有未覆盖的 model×rubric 对 | 继续提交剩余批次；同一对不要交两次 | 已合并条数、剩余对数 |
| 4 封内层包 | 无 | `complete_eval.py seal-base --run <run>` | 0 | `base/evidence-bundle.zip` 与其 sidecar、`form-ready/` | 退出码 3 说明内层包不完整 | 回到阶段 2/3 补齐 | `base_verified`、ZIP 的 SHA-256 |
| 5 冻结媒体 | 无（读冻结清单） | `complete_eval.py freeze-media --run <run>` | 0 或 2 | `form-ready/observations/source-media/`、媒体索引 | 2 表示仍有 target/feedback 未登记 | 按 `MEDIA_*` 错误码处理，见下表分支 | `media_frozen`、已登记媒体数 |
| 6 渲染 | 被动 SVG（若该题有） | `render_media.py --help` 后按其参数渲染，再 `register_render` 登记 | 0，或 `NO_QUALIFIED_RENDERER` | `observations/renders/`、渲染收据 | `NO_QUALIFIED_RENDERER` | 见"分支 D/E" | 渲染件数、渲染器版本 |
| 7 双读观测 | 每个需要视觉判断的 model×rubric | `complete_eval.py record-vision --run <run> --record <vis.json>` 两次 | 2（正常） | `observations/machine-vision.jsonl` | 读数为 1 条 | 补第二条读；见"分支 F" | 已记录读数条数 |
| 8 人工升级 | 需要真人的条目 | `complete_eval.py record-human --run <run> --record <hum.json>` | 0 | `observations/remote-human.jsonl` | 退出码 5 | 交给真人执行；见"分支 J" | 已记录人工见证条数 |
| 9 决策 | 每个 model×rubric 一行决定 | `complete_eval.py decide --run <run> --pair model/rubric --value-json <v.json>` | 0 或 2 | `decisions/final-decisions.json`、审计 | 2 表示仍有未闭合分数/裁定/缺口 | 按 `DECISION_*` 错误码补齐 | `decisions_complete`、决定条数 |
| 10 演示槽位 | 每个语义槽位一个提案 | `complete_eval.py record-presentation --run <run> --slot <slot> --value-json <v.json>` | 2 | `presentation/presentation-input.json` | 2 表示仍有空槽 | 继续填；见"分支 G/H" | 已完成槽位数 |
| 11 演示确认 | 主观槽位 | `complete_eval.py attest-presentation --run <run> --slot <slot> --observer <alias>` | 0 或 5 | 槽位确认记录 | 5 | 交给真人执行 | `presentation_complete` |
| 12 评分与产物 | 无 | `complete_eval.py build-outputs --run <run>` | 0 | `scored/`、`presentation/反馈报告.md`、热力图、`form-input.json` | 退出码 3 说明上游未闭合 | 回到对应阶段 | `outputs_complete`、计分条数 |
| 13 源复核 | 无 | `complete_eval.py check-source --run <run>` | 0 或 4 | 源复核收据 | 4 即终态 | 立即停止并报告 | `source_verified` 或终态 |
| 14 校验 | 无 | `complete_eval.py validate --run <run>` | 0 | 校验报告 | 非 0 | 按错误码回到对应阶段 | 计数：模型/rubric/证据/未闭合 |
| 15 封外层包 | 无 | `complete_eval.py package --run <run> --output-zip <绝对路径>` | 0 | 外层 ZIP 与 sidecar | 非 0 | 按退出码处理 | ZIP 绝对路径、sidecar 绝对路径、SHA-256 |

## 决策分支

**分支 A：被动位图（PNG/JPG）**
直接把该文件登记为 `target` 或 `feedback` 媒体。不需要渲染。

**分支 B：安全的静态 SVG**
先过 `render_media.py` 的被动 SVG 白名单；通过后才渲染并登记为 `candidate_full`。渲染器收据必须由脚本生成，不接受任何手工收据。

**分支 C：不安全 SVG 或主动内容**
白名单拒绝（`UNSAFE_SVG`）、或输入是 HTML/应用时，**不要**尝试渲染或执行。记录为未渲染，并在缺口里说明原因；相应条目的视觉判断改走人工见证。

**分支 D：没有合格渲染器**
`NO_QUALIFIED_RENDERER` 时不要伪造渲染收据。把该候选条目标记为"无法渲染"，改走人工见证。

**分支 E：缺目标素材**
冻结清单里没有 `target`/`feedback` 文件时不要凭空造图。保留 `material_gaps`，在决策阶段按"材料缺口"处理。

**分支 F：两次读冲突或低置信**
两条读结论不一致、或任一条不是高置信、或第二条重复了第一条的答案时，**不得**用它下结论。升级为人工见证（阶段 8）。

**分支 G：主观条目或政策歧义**
分类为 `subjective_or_policy` 的条目，或挂了 `ADJ-*` 的条目，一律需要人工见证。机器读图不得关闭这类条目。

**分支 H：证据引不到**
提案引用的证据必须能在本包本模型的证据登记表里解析。解析不到就换引用或补证据，不要改摘要。

**分支 I：缺可选材料**
只有当 `references/gap-policy.json` 的 `known_optional` 里**明确列出**该缺口、且前置条件满足时，才能用机械方式关闭。清单里没有的一律按语义缺口处理。

**分支 J：缺语义材料或没有真人**
语义缺口和未知缺口需要真人决议加一段限制说明。现场没有真人时停在退出码 5，报告"需要真人"，**不要**用机器结论顶替。

## 每次阶段报告格式

一行一件事，写完就继续：

```
阶段 <编号> <名称>：命令 <执行的命令> → 退出码 <码>；产物 <路径>；下一步 <计划>
```

运行结束时额外给出：RUN_ID、ZIP 路径、sidecar 路径、ZIP SHA-256、以及未闭合计数（必须全 0）。

## 硬性禁止

- 不跳阶段、不并阶段、不重新排序。
- 不用 `git checkout`、不删除运行目录、不手改任何 JSON/JSONL。
- 不把机器结论写成人的结论，不伪造确认词。
- 不因为"看起来差不多了"就跳过 `validate` 或 `package`。
