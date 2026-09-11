# form-ready bundle 契约（外层产物）

外层包（`FORM-READY.json` 所在的那一层）是交给本机的唯一交付物。它必须自证：内层 v1 包逐字节未被改动，媒体与观测可复算，分数与裁定全部闭合。

## 目录与文件

| 路径 | 谁写 | 说明 |
|---|---|---|
| `FORM-READY.json` | `initialize_form_ready`（状态由 `package_form_ready` 置为 `ready_for_form`） | 外层清单与固定路径表 |
| `base/evidence-bundle.zip` + `.sha256` | `initialize_form_ready` | 内层 v1 包，**逐字节不可变** |
| `observations/media-index.json` | `register_media` | blob（按内容寻址）＋ use（按 id）分离 |
| `observations/source-media/`、`observations/renders/` | `register_media` / `register_render` | 真实字节，索引必须与磁盘一一对应 |
| `observations/machine-vision.jsonl`、`remote-human.jsonl` | `record-vision` / `record-human` | 逐行追加，不改写历史 |
| `decisions/criterion-classifications.json` | 决策阶段 | 每条 rubric 一条分类记录 |
| `decisions/final-decisions.json` | `decision_workspace` | 恰好覆盖每个 model×rubric |
| `decisions/adjudication-audit.json` | `decision_workspace` | 待裁定项与材料缺口的闭合记录 |
| `scored/` | `project_form_ready_scores` | `rubrics-<model>.json`＋`scoring-summary.json`＋`decision-audit.json` |
| `presentation/presentation-input.json` | `presentation_workspace` | 语义槽位＝机器提案＋依据＋证据（＋主观项的人工确认） |
| `presentation/反馈报告.md`、`rubrics打分热力图.html`、`form-input.json`、`report-input.json` | `render_supporting_outputs` | 由校验过的输入渲染，可逐字节复算 |
| `integrity/source-verification.json` | `package_form_ready` | 脚本生成的源核对收据；手工写的会被覆盖 |
| `integrity/files.sha256`、`validation-report.json`、`READY.json` | `package_form_ready` | 外层封印三件套 |

## 身份绑定

每条外层记录都要带：`outer_package_id`、`base_package_id`、`base_zip_sha256`、`source_input_digest`、`task_id`；涉及具体条目的还要带 `model_id`、`rubric_id`、`round`、`criterion_sha256`。任何一项与内层对不上就是 `IDENTITY_MISMATCH`。

## 分数纪律

- 每个 model×rubric 恰好一条决定，`score` 只能是 0 或 1，绝不为空。
- `decided_by` 只有四种：`mechanical`、`accepted_machine`、`machine_vision`、`remote_human`，各自的可引用证据不同。
- 机器读图不能关闭主观条目；受裁定影响的条目必须由真人决议关闭。
- `null`／未知永远不等于 0。

## 封印的含义与边界

封印检查能发现：内层包被改动、媒体字节与索引不符、观测记录不完整、分数或裁定未闭合、外层产物被改过、校验清单与控制文件不一致。

封印**不能**发现：一个有能力重写外层的全部字节、并且同时改掉外部 sidecar 与期望摘要的攻击者。包内自洽检查没有任何东西保护它自己；被信任的锚点始终是外部 sidecar 或你手里的期望 SHA-256。不要把这条写成"密码学密封"。
