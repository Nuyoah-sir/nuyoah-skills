# Nuyoah Skills

Nuyoah 的个人 Agent Skills 仓库，用于存放、版本管理与跨机器同步自研技能包。

## 技能列表

| 技能 | 作用 | 依赖关系 |
| --- | --- | --- |
| [multi-turn-eval](multi-turn-eval/SKILL.md) | 多轮后端评测流水线：解析 spec → 生成各模型 solve 脚本 + 黑盒 `test.sh` → Docker 逐模型评测 → 聚合出反馈报告与 Rubric 打分结果。 | 被 vibe-evals-rubric-review / vibe-evals-scoring 前置引用（只读对照） |
| [vibe-evals-rubric-review](vibe-evals-rubric-review/SKILL.md) | 读题包 rubrics + prompt，做三源核验（prompt 预期 → 模型效果 → 出题人思考），产出新 rubrics JSON（默认与基线逐字节一致）+《对每条rubric的理解.md》（含裁定口径）。检测废话条目、覆盖缺口与判分口径问题。 | 可独立运行 |
| [vibe-evals-scoring](vibe-evals-scoring/SKILL.md) | Vibe Evals 打分流水线：并行子智能体证据收集 → 机器预打分 0/1 → 人工打分文档 → 人工回填复核 → 转录 `rubrics-{X}.json` → 反馈报告 + 打分热力图。 | 上游可选：vibe-evals-rubric-review |
| [vibe-evals-vibe-form](vibe-evals-vibe-form/SKILL.md) | 按 V2.1 人评标准，从反馈报告 + 已评分 rubrics JSON 生成单文件多模型评分表单（机器预填建议值，全部标注"待人工确认"，最终以人工拍板为准）。 | 上游必需：vibe-evals-scoring 产物 |

## 共享前置材料（`references/` 与 `multi-turn-eval/`）

vibe-evals-rubric-review 的「前置条件（MANDATORY）」要求开跑前先读两份材料（多轮评测技能、《Coding Agent 人评标准 V 2.1》全文）。这两份材料以前只写在本机技能目录里，第三方机器取不到就会卡在开跑前。现在它们随仓库一起分发，**查找顺序的第一步就是仓库/技能目录内的相对路径**：

| 材料 | 仓库内路径 | 用途（被谁引用） |
| --- | --- | --- |
| multi-turn-eval 技能全文 | [multi-turn-eval/](multi-turn-eval/SKILL.md) | 对照多轮后端评测流水线的输入（spec、模型输出、输出目录、对话记录）与产出（反馈报告、Rubric.json、solve/test 脚本），与人评路线区分。被 vibe-evals-rubric-review、vibe-evals-scoring 前置引用。 |
| 《Coding Agent 人评标准 V 2.1》完整文档（Markdown 全文） | [references/Coding Agent 人评标准 V2.1.md](references/Coding%20Agent%20人评标准%20V2.1.md) | 打分流程指南、多模型 Rank（排序判断原则 / 排序理由 / 洞察）、Pointwise 多维度评分、Vibe Tags 与备注的权威依据。被 vibe-evals-rubric-review、vibe-evals-scoring、vibe-evals-vibe-form 引用。 |
| 同一文档的原始 Word 版本 | `references/Coding Agent 人评标准 V 2.1.docx` | 需要原始排版、平台截图或对外引用来源时读；与上游发布文件逐字节一致。 |
| 文档内图片资源 | `references/assets/人评标准V2.1/` | Markdown 全文按原位置内嵌引用的截图。 |

取用顺序：① 仓库内副本（相对各 `SKILL.md` 的 `../references/...`、`../multi-turn-eval/...`）→ ② 拷贝安装后技能目录同级（`~/.claude/skills/references/...`、`~/.agents/skills/...`）→ ③ 用户题包随附材料或用户指定路径。**命中即用，不必找齐**。

三处都取不到时：先把「已查找路径清单」报给用户并索取路径——不猜、不跳过、不凭记忆复述标准原文。用户明确同意后，可以按「前置缺失」记录在案继续，但必须在交付物开头写明缺哪一项、缺失影响到哪些判定口径（Rank 排序、洞察写法、Pointwise 维度分档等）。

## 跨机器证据套件

[vibe-evals-sonAgent-skills](vibe-evals-sonAgent-skills/安装与操作手册.md) 用于“题包留在第三方机器，本机只接收可信证据 ZIP”的工作方式，包含：

| 技能 | 安装位置 | 作用 |
| --- | --- | --- |
| `vibe-evals-son-complete-eval` | 第三方机器（**默认**） | 从原始题包一路做完：取证、渲染、观测、裁定、评分、演示产物，最后封出一个 form-ready ZIP。 |
| `vibe-evals-son-evidence-export` | 第三方机器（legacy） | 只导出证据，由本机另行评分。 |
| `vibe-evals-son-evidence-supplement` | 第三方机器（legacy） | 按 `evidence_requests.json` 对证据包补证。 |
| `vibe-evals-bundle-finalize` | 本机 | 先按 schema 分流：form-ready 包做校验与渲染（**不做任何本地裁定**）；v1 证据包走既有汇总流程。 |

默认远程提示词：

```text
使用 $vibe-evals-son-complete-eval 完成这整套评测。
题目包根目录：<绝对路径>
输出根目录：<绝对路径>
```

默认本机提示词：

```text
使用 $vibe-evals-bundle-finalize 校验并渲染这个 form-ready 包。
ZIP：<绝对路径>
sidecar：<绝对路径>
期望 SHA-256：<可选，建议填写>
输出目录：<绝对路径，必须不存在>
```

套件同时提供 PowerShell 安装脚本、完整测试、协议说明和[最新可分发 ZIP v1.0.2](vibe-evals-sonAgent-skills/dist/vibe-evals-sonAgent-skills-v1.0.2.zip)。第三方机器无需把完整题包复制回本机；证据包保留源码原文字节、内容哈希、轮次绑定、测试脚本/日志、对话绑定和人工裁定审计链。v1.0.2 兼容题包 rubric 中数字轮次与 `"R1"/"R2"` 字符串轮次，并在不改原始 rubric 字节的前提下生成统一数字元数据。

```powershell
# 第三方机器
.\vibe-evals-sonAgent-skills\install-third-party.ps1

# 本机
.\vibe-evals-sonAgent-skills\install-local.ps1
```

详细流程、状态含义和可复制提示词见[安装与操作手册](vibe-evals-sonAgent-skills/安装与操作手册.md)。

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

原有三个技能各自可独立触发，也可以按上述顺序串成一条完整的人评流水线；跨机器场景优先使用上面的证据套件。上方的 `multi-turn-eval` 是另一条路线（自动跑模型 + Docker 测试），不参与人评流水线，只作为 vibe-evals-rubric-review 判分口径的前置对照材料随仓库分发。

## 目录结构

```
.
├── multi-turn-eval/                 # 前置材料：多轮后端评测技能
│   ├── SKILL.md
│   ├── README.md
│   ├── designs/ · templates/ · examples/
├── references/                      # 前置材料：人评标准全文与截图
│   ├── Coding Agent 人评标准 V2.1.md
│   ├── Coding Agent 人评标准 V 2.1.docx
│   └── assets/人评标准V2.1/
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

克隆本仓库后，把需要的技能目录**连同 `multi-turn-eval/` 与 `references/`** 一起拷贝到你的 agent 技能目录即可（后两者是 vibe-evals-rubric-review 的前置材料，缺了会在开跑前卡住）。

Claude Code：

```bash
git clone https://github.com/Nuyoah-sir/nuyoah-skills.git
cp -r nuyoah-skills/vibe-evals-* nuyoah-skills/multi-turn-eval nuyoah-skills/references ~/.claude/skills/

# 装完自查：下面两个路径都存在，rubric-review 的前置条件即可离线满足
ls ~/.claude/skills/multi-turn-eval/SKILL.md \
   ~/.claude/skills/references/"Coding Agent 人评标准 V2.1.md"
```

Codex（Windows PowerShell）：

```powershell
git clone https://github.com/Nuyoah-sir/nuyoah-skills.git
Copy-Item .\nuyoah-skills\vibe-evals-*, .\nuyoah-skills\multi-turn-eval, .\nuyoah-skills\references `
  -Destination "$env:USERPROFILE\.codex\skills\" -Recurse -Force

# 装完自查：两个路径都应输出 True
Test-Path "$env:USERPROFILE\.codex\skills\multi-turn-eval\SKILL.md",
          "$env:USERPROFILE\.codex\skills\references\Coding Agent 人评标准 V2.1.md"
```

也可以只取单个技能：把仓库中对应的技能目录放进技能目录，并保证 `SKILL.md` 处于该目录根下；若只取 `vibe-evals-*` 而不带 `references/`、`multi-turn-eval/`，则技能里写明的「仓库内副本」路径会落空，需要在本机技能目录（`~/.claude/skills/references/...`、`~/.agents/skills/...`）另放一份前置材料，或按技能内的提示向用户索取路径。

## 说明

- 三个技能围绕 Vibe Evals 人评流程设计，产出物里的所有分数与结论默认标注"待人工确认"，机器只给建议值。
- 技能运行时的纪律：证据必须落到 `file:line` 或实测结果；不擅自裁定判分口径；不修改题包原始文件。
- `references/Coding Agent 人评标准 V 2.1.docx` 内含公司内部平台地址与平台截图，仓库访问范围请按同样口径控制，不要把该文档分发到授权范围之外。
- 仓库内容为个人工作资产，未附开源许可证，未经许可请勿转载或用于商业用途。
