# Vibe Evals 远端闭环评测与 Form-Ready 交付 v2 设计

## 1. 背景与问题

Evidence Bundle v1 成功解决了“题包不复制回本机”的传输问题，但把 `null`、视觉检查、政策歧义和 material gap 留给本机。真实批次 `PS-0819copy` 因而在远端成功封包后，仍需把目标图从第三方机器取回、在本机渲染检查并处理两个裁定项。这违背了“第三方机器完成脏活累活，本机拿到文件即可生成评分表单”的目标。

v2 将评分闭环整体移到第三方机器。第三方交付物只有在所有 rubric 已有最终 0/1、所有视觉与政策问题已闭环、所有评分表单输入已生成并通过机械校验时，才允许封包。本机不再补证或判分，只进行密码学验证、结构复验和确定性渲染。

## 2. 已确认的权威边界

采用混合裁定模式：

- 机械事实和可复现静态事实由 Agent 判定。
- 客观视觉事实可由机器视觉判定，但必须满足双读一致、证据完备和无口径歧义等门槛。
- 主观视觉、低置信判断、证据冲突和政策歧义必须由第三方机器旁的真人确认。
- Agent 不得把自身或视觉模型的输出标记为真人决定。
- 若需要真人而无人确认，任务停留在第三方机器，不产生 form-ready ZIP。

## 3. 目标与完成定义

### 3.1 用户可见目标

第三方机器最终只交付：

1. `<题目>-form-ready-evaluation.zip`
2. `<题目>-form-ready-evaluation.zip.sha256`

本机收到两文件后，可以在不读取原题包、不请求额外图片、不做人工 rubric 裁定、不返回第三方补证的情况下，直接生成并复验最终评分表单。

### 3.2 `ready_for_form` 的机械条件

同时满足以下条件才允许生成正式 ZIP：

- 原题包在整个运行期间保持只读且源摘要不变。
- 每模型 × 每 rubric 恰好一条最终 0/1，无 `null`。
- `human_check_needed=0`，无 pending adjudication。
- 每个 material gap 均已补齐，或按明确规则留下具名真人的 limitation 决定；声明为可选且不影响语义的材料可由契约规则机械关闭。
- 每个最终分数引用同模型、同 rubric、合法轮次内的有效证据。
- 每条机器视觉决定具备可重放的输入、渲染和观察链。
- 每条真人决定具备真人身份代号、时间、步骤、看到的版本、原始确认文本和证据引用。
- scored rubrics、总分、反馈报告、热力图与 `form-input.json` 相互一致。
- 内层 evidence bundle 与外层 form-ready 包均通过严格 validator 和完整性封印。

## 4. 技能架构

### 4.1 新增远端入口

新增 `vibe-evals-son-complete-eval`。这是第三方机器的默认入口，负责编排完整闭环：

1. 只读 discovery 与 source freeze。
2. prompt/rubric 审查、逐模型证据收集和机器预评分。
3. 收集 rubric 相关源媒体与反馈媒体。
4. 安全检查、机器视觉和真人观察闭环。
5. 统一政策裁定并生成全部最终 0/1。
6. 生成 scored rubrics、报告、热力图和 form input。
7. 双层验证并输出 form-ready ZIP。

该技能复用现有确定性脚本，但不要求 Agent 手工拼接关键 JSON。新脚本负责状态派生、引用校验、封印和渲染输入生成。

### 4.2 保留现有技能

- `vibe-evals-son-evidence-export` 保留为诊断/中间证据模式，不再作为正常跨机器交付终点；其说明必须明确 exit 2 不满足“直接生成评分表单”的目标。
- `vibe-evals-son-evidence-supplement` 保留用于同一第三方机器内部的定向补证，也可兼容旧 v1 流程。
- `vibe-evals-bundle-finalize` 增加 v2 `verify-and-render` 模式。本机面对 v2 包时只验证和渲染；不得生成裁定清单或要求用户补评分事实。v1 包仍可进入 legacy prepare 模式，以保持向后兼容。

### 4.3 安装职责

第三方安装器安装三个远端技能：export、supplement、complete-eval。本机安装器只安装 bundle-finalize。分发包和 GitHub README 默认提示词改用 complete-eval。

## 5. 数据与封包设计

### 5.1 双层不可变结构

form-ready ZIP 使用外层 schema `vibe-evals-form-ready-bundle`，版本 `2.0.0`：

```text
form-ready-bundle/
├─ FORM-READY.json
├─ READY.json
├─ base/
│  ├─ evidence-bundle.zip
│  └─ evidence-bundle.zip.sha256
├─ observations/
│  ├─ media-index.json
│  ├─ source-media/{sha256}.{ext}
│  ├─ renders/{sha256}.png
│  ├─ machine-vision.jsonl
│  └─ remote-human.jsonl
├─ decisions/
│  ├─ final-decisions.json
│  └─ adjudication-audit.json
├─ scored/
│  ├─ rubrics-{model_id}.json
│  ├─ scoring-summary.json
│  └─ decision-audit.json
├─ presentation/
│  ├─ 反馈报告.md
│  ├─ rubrics打分热力图.html
│  └─ form-input.json
└─ integrity/
   ├─ files.sha256
   └─ validation-report.json
```

内层 v1 evidence bundle 保持封印且不被改写。外层决定引用内层 evidence ID 或外层 `VIS-*`/`HUM-*` observation ID，并绑定内层 package ID、内层 ZIP SHA-256 与 source input digest。这样既能复用 v1 校验器，又能明确区分取证事实与后续裁定。

### 5.2 媒体证据

仅冻结哈希不足以完成跨机器视觉复核。v2 必须携带所有实际参与判分的媒体字节：

- prompt、rubric、反馈或人工记录直接引用的目标图、参考图和反馈图；
- 用于视觉判断的模型产物原文件；
- 对 HTML/SVG/应用产物生成的实际观察截图；
- 必要时的局部裁剪，但必须同时保留完整图并记录裁剪坐标。

`media-index.json` 为每个媒体记录源相对路径、角色、轮次、模型、rubric、大小、MIME、SHA-256、内容寻址路径和采集方式。媒体默认按原字节保存；不能可靠确认来源或哈希的媒体不得用于评分。

### 5.3 决定来源枚举

最终决定允许以下 `decided_by`：

- `mechanical`：由清单、精确值、测试断言等确定性事实直接决定。
- `accepted_machine`：远端 Agent 根据已验证非视觉证据接受机器建议。
- `machine_vision`：满足机器视觉门槛的客观视觉判断。
- `remote_human`：第三方机器旁真人明确确认。

每项还必须包含 `decider`、`decided_at`、`reason`、`evidence_ids` 和 `decision_source`。`remote_human` 的 `decision_source` 保存真人原话或忠实短述；其他类型不得使用“人工确认”等措辞。

## 6. 视觉检查协议

### 6.1 可安全观察的产物

- PNG/JPEG/WebP 等被动位图可直接检查。
- SVG 必须先拒绝脚本、事件处理器、外部资源、`foreignObject`、网络 URL 和越界文件引用，再由固定版本渲染器在无网、无凭据、可丢弃环境中栅格化。
- HTML、前端应用或任何会执行模型代码的产物，只有六项安全门槛全部满足时才能运行：不可信代码标记、隔离、禁网、无凭据、可丢弃副本、最小权限。
- 不满足安全条件时不得在宿主机直接打开。相关视觉 rubric 必须转给第三方真人使用合规环境检查；仍无合规环境则不能形成 form-ready 包。

### 6.2 机器视觉可终局判分门槛

仅当以下条件全部成立，客观视觉 rubric 才可使用 `machine_vision`：

1. criterion 可转成明确可观察事实，不依赖审美、自然度或整体感受。
2. 目标图和候选渲染图均已入包并绑定哈希。
3. 使用相同证据、不同措辞进行两次独立视觉读取；两次结论一致。
4. 视觉结论与源码、几何、颜色等静态证据不冲突。
5. 不存在 rubric 语义或评分政策歧义。
6. 两次读取都给出具体位置、对象和可核对事实，而非只有“看起来正确”。

任一条件不满足即升级为 `remote_human`。主观视觉、遮挡是否可接受、布局是否自然、游戏手感和政策歧义默认要求真人。

### 6.3 机器视觉记录

每次读取保存：视觉工具/模型、时间、输入媒体 ID、完整问题、原始回答、结构化观察、结论、置信度、对应 rubric、读取序号和冲突状态。最终决定引用两次读取的 observation ID；只保存 Agent 的总结不合格。

### 6.4 真人观察记录

真人观察必须保存：稳定身份代号、观察时间、操作步骤、预期、实际观察、被观察 package/render ID、原始确认文本和受影响 rubric。Agent 只能转录，不得代填身份或替真人选择。

## 7. 远端闭环状态机

```text
DISCOVERED
  → EVIDENCE_COLLECTED
  → MEDIA_FROZEN
  → OBSERVATIONS_COMPLETE
  → DECISIONS_COMPLETE
  → SCORED_ARTIFACTS_VERIFIED
  → FORM_INPUT_VERIFIED
  → FORM_READY_SEALED
```

任一阶段失败保留诊断目录，但不得跨阶段伪造完成标志。源摘要变化时整批失效并使用新 RUN_ID。低置信或待真人项停在 `OBSERVATIONS_COMPLETE` 之前；有任何 null、pending、未解决 gap 或引用错误时不得进入 `FORM_READY_SEALED`。

material gap 分三类：

- `required_source_missing`：必须补齐，否则停止。
- `optional_contract_material_missing`：契约明确可选且不影响解释时，机械记录 limitation 后关闭。
- `semantic_material_missing`：可能改变 rubric 含义或分数，必须由第三方真人决定是否以 limitation 继续，并说明排除的评价能力。

## 8. 本机流程

本机运行：

```text
$vibe-evals-bundle-finalize verify-and-render <form-ready.zip> <sha256>
```

工具依次执行：

1. sidecar、用户期望值与独立 SHA-256 三方比对。
2. 外层 ZIP 安全检查与全新目录解压。
3. 外层文件封印校验。
4. 内层 evidence ZIP SHA-256、安全解压和 v1 严格 validator。
5. v2 observation、decision、scored artifacts、报告、热力图和 form input 交叉校验。
6. 确定性渲染评分表单并逐字节复验。

本机 v2 流程没有 prepare、补证和人工裁定分支。发现 unresolved 项时直接判定远端包不合格，并生成错误报告；不会让本机用户继续完成评分。

## 9. 当前 PS-0819copy 批次的预期闭环

v2 重跑必须在第三方机器完成：

- 将 `target.png`、`round2_feedback.png`、相关 SVG 原文件和安全栅格化结果装入媒体证据。
- R1-12、R1-17：目标图与第一轮渲染双读；若仍不能可靠判断则第三方真人确认。
- R2-01、R2-15：渲染检查重叠/遮挡；主观可接受性由第三方真人确认。
- R2-23：机器检查实际 `4 4` 虚线，真人处理“清楚分离”与政策口径。
- R2-09、R2-12、R2-26：第三方真人统一解决两项 ADJ，且对受影响 rubric 一致应用。
- `rubrics说明.xlsx`：先按契约判断它是可选材料还是语义材料；不得因为文件名缺失机械阻断。如果实际 JSON 已完整定义字段且无外部语义依赖，记录 optional limitation 后关闭。

产物必须包含 45 条最终 0/1、0 个 pending、0 个 null、0 个 human check，以及已验证的 `form-input.json`。

## 10. 错误处理与真实性规则

- 没有媒体字节只有哈希：不能完成视觉判断。
- 单次视觉模型回答：不能形成终局机器视觉分数。
- 视觉模型与静态证据冲突：升级真人，不得挑选有利结论。
- 需要真人但无人回应：留在远端，不能封包。
- 真人观察缺身份、时间、步骤、版本或原话：结构不合格。
- 不安全的动态渲染：禁止执行，也不能用静态想象冒充观察。
- 评分全为 0/1但证据引用断裂：仍是 incomplete。
- 报告或 form input 与 scored rubrics 不一致：不封包。
- 外层包不得声称密码学证明“确有真人”；它只能证明具名声明和所见材料被完整记录且未在传输中改变。

## 11. 测试与验收

### 11.1 技能行为 RED 测试

在修改技能前记录至少三类无 v2 指令的失败基线：

1. 面对缺 target.png 的视觉 null，Agent仍把问题留给本机。
2. 面对机器视觉低置信结果，Agent把自身判断伪装成人工确认。
3. 面对 44/45 已评分和赶时间压力，Agent仍错误封包。

修改后以同样压力场景验证：问题必须留在第三方闭环、身份不冒充、未全闭环不封包。

### 11.2 脚本测试

单元与集成测试至少覆盖：

- v2 schema 和路径安全。
- 内外层 SHA-256、文件清单和 package/source 绑定。
- 媒体原字节、MIME、哈希和内容寻址覆盖。
- 双读一致与冲突升级。
- 四种 decided_by 的必填字段和禁用措辞。
- 真人观察字段完整性与同模型同 rubric 外键。
- null、pending、human check、unresolved gap 任一存在时拒绝 form-ready。
- scored rubrics、总分、报告、热力图和 form input 一致性。
- v1 legacy prepare 不回归。
- v2 本机 verify-and-render 不产生裁定清单或补证请求。
- ZIP 路径穿越、重复成员、符号链接、压缩炸弹和篡改检测。

### 11.3 端到端验收

使用合成多轮视觉题完成：远端完整闭环 → v2 封包 → 本机无裁定渲染评分表单。再分别注入缺媒体、单次视觉读取、伪造真人、一个 null、未决 ADJ、篡改内层包和错配 form input，确认全部被拒绝。

PS-0819copy 作为真实验收批次重新从 discovery 运行；本机最终只能收到 form-ready ZIP 与 sidecar，并在一次调用中生成通过 verify 的评分表单。

## 12. 兼容性与迁移

- v1 evidence bundle 与现有脚本保持可读，旧包继续使用 legacy prepare。
- v2 不允许把旧 `ready_for_local_review` 包简单改状态；必须在原第三方机器基于同一冻结源补充媒体和决定，或使用新 RUN_ID 完整重跑。
- 正常提示词、安装手册和 README 迁移到 complete-eval；export/supplement 标为高级诊断入口。
- 分发包使用新版本号并保留旧 ZIP，避免破坏已有安装。

## 13. 非目标

- 不用结构校验冒充真人真实性证明。
- 不允许本机替远端补完任何 rubric 分数或视觉事实。
- 不在不合格环境执行模型代码或主动内容。
- 不修改原题包、原 rubrics、旧封印包或金标准。
- 不要求把完整题包复制回本机。
