# Nuyoah Skills

Nuyoah 的个人 Agent Skills 仓库，用于存放、版本管理与跨机器同步自研技能包。

## 技能列表

| 技能 | 作用 | 依赖关系 |
| --- | --- | --- |
| [vibe-evals-rubric-review](vibe-evals-rubric-review/SKILL.md) | 读题包 rubrics + prompt，做三源核验（prompt 预期 → 模型效果 → 出题人思考），产出新 rubrics JSON（默认与基线逐字节一致）+《对每条rubric的理解.md》（含裁定口径）。检测废话条目、覆盖缺口与判分口径问题。 | 可独立运行 |
| [vibe-evals-scoring](vibe-evals-scoring/SKILL.md) | Vibe Evals 打分流水线：并行子智能体证据收集 → 机器预打分 0/1 → 人工打分文档 → 人工回填复核 → 转录 `rubrics-{X}.json` → 反馈报告 + 打分热力图。 | 上游可选：vibe-evals-rubric-review |
| [vibe-evals-vibe-form](vibe-evals-vibe-form/SKILL.md) | 按 V2.1 人评标准，从反馈报告 + 已评分 rubrics JSON 生成单文件多模型评分表单（机器预填建议值，全部标注"待人工确认"，最终以人工拍板为准）。 | 上游必需：vibe-evals-scoring 产物 |

## 流水线关系

```
题包（prompt / rubrics / 模型输出）
        │
        ▼
vibe-evals-rubric-review ──► rubrics1..N.json + 对每条rubric的理解.md
        │
        ▼
vibe-evals-scoring ────────► 预打分.md → 人工打分文档.md → rubrics-{X}.json
        │                                      └─► 反馈报告.md + rubrics打分热力图.html
        ▼
vibe-evals-vibe-form ──────► {题}-评分表单.md（V2.1 人评表单）
```

三个技能各自可独立触发，也可以按上述顺序串成一条完整的人评流水线。

## 目录结构

```
.
├── vibe-evals-rubric-review/
│   ├── SKILL.md
│   └── templates/对每条rubric的理解模板.md
├── vibe-evals-scoring/
│   ├── SKILL.md
│   └── templates/
│       ├── 预打分模板.md
│       ├── 人工打分文档模板.md
│       ├── 反馈报告模板.md
│       ├── 证据收集子智能体prompt模板.md
│       └── rubrics打分热力图模板.html
└── vibe-evals-vibe-form/
    ├── SKILL.md
    ├── references/人评标准V2.1-Pointwise多维度评分.md
    └── templates/
        ├── 评分表单模板.md
        └── V2.1标签库.md
```

每个技能遵循通用 Agent Skills 结构：`SKILL.md` 为入口（含 `name` / `description` frontmatter），`templates/` 放产出物模板，`references/` 放标准与背景材料。

## 使用方式

克隆本仓库后，把需要的技能目录拷贝到你的 agent 技能目录即可。

Claude Code：

```bash
git clone https://github.com/Nuyoah-sir/nuyoah-skills.git
cp -r nuyoah-skills/vibe-evals-* ~/.claude/skills/
```

Codex（Windows PowerShell）：

```powershell
git clone https://github.com/Nuyoah-sir/nuyoah-skills.git
Copy-Item .\nuyoah-skills\vibe-evals-* -Destination "$env:USERPROFILE\.codex\skills\" -Recurse -Force
```

也可以只取单个技能：把仓库中对应的技能目录放进技能目录，并保证 `SKILL.md` 处于该目录根下。

## 说明

- 三个技能围绕 Vibe Evals 人评流程设计，产出物里的所有分数与结论默认标注"待人工确认"，机器只给建议值。
- 技能运行时的纪律：证据必须落到 `file:line` 或实测结果；不擅自裁定判分口径；不修改题包原始文件。
- 仓库内容为个人工作资产，未附开源许可证，未经许可请勿转载或用于商业用途。
