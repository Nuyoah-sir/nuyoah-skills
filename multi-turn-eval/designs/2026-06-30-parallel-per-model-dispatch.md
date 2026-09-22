# Multi-Turn Eval Skill — 分模型并行调度重构

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Phase 2-4-7 从「主会话暴力全读」重构为「每模型一个子智能体，并行独立处理」，消除因文件量过大导致的偷懒漏读。

**Architecture:** 主会话只做无模型依赖的协调工作（Phase 1 spec 解析、Phase 3 test.sh 生成、Phase 5 结果聚合、Phase 6 指南生成）。任何需要读模型源码或对话的工作（Phase 2 solve 生成、Phase 4 Docker 测试、Phase 7 对话分析）全部派给每模型一个的独立子智能体。子智能体并行执行，彼此零耦合。

**Tech Stack:** Codex multi-agent dispatch, bash heredoc embedding, Docker

---

## 文件变更总览

| 文件 | 动作 | 说明 |
|------|------|------|
| `skill.md` | 重写 Phase 2/4/5/7, 新增内置 prompt 附录 | 主变更 |
| `templates/solve_template.sh` | 不变 | 子智能体仍需此模板 |
| `templates/test_template.sh` | 不变 | 主会话用 |
| `templates/results_template.md` | 可能微调 | 子智能体回报格式 |

---

## 新架构流程图

```
用户提供: SPEC_FILE, MODEL_DIR, OUTPUT_DIR, CONV_DIR(可选)

┌─ 主会话 Phase 1 ─────────────────────────────┐
│  read SPEC_FILE (204行, 可控)                  │
│  → 提取 TECH_STACK, API_SPEC, DATA_MODEL,     │
│     ERROR_CODES, AUTH_SCHEME, INVARIANTS       │
│  → 写 instruction.md                           │
│  → 输出: EXTRACTED_SPEC (结构化提取结果)        │
└───────────────────────────────────────────────┘
                    ↓
┌─ 主会话 Phase 3 ─────────────────────────────┐
│  用 EXTRACTED_SPEC 生成 tests/test.sh          │
│  (不读任何模型代码)                             │
│  → 输出: TEST_SH_PATH                          │
└───────────────────────────────────────────────┘
                    ↓
┌─ 并行子智能体 (每模型一个) ────────────────────┐
│                                                │
│  ┌─ Agent[模型A] ──────────────────────────┐  │
│  │ 输入: EXTRACTED_SPEC + MODEL_DIR/A/     │  │
│  │       + solve_template + test.sh         │  │
│  │ 1. 读 A/ 源文件 (~30个)                 │  │
│  │ 2. 生成 solve_A.sh                       │  │
│  │ 3. docker build → run → exec test.sh     │  │
│  │ 4. 回报: 构建/测试/代码审查 结果          │  │
│  └──────────────────────────────────────────┘  │
│                                                │
│  ┌─ Agent[模型B] ──(同上, 并行)────────────┐  │
│  └─ Agent[模型C] ──(同上, 并行)────────────┘  │
│                                                │
│  等待全部完成                                   │
└───────────────────────────────────────────────┘
                    ↓
┌─ 主会话 Phase 5 ─────────────────────────────┐
│  收集各子智能体回报                             │
│  → 横向对比表 + 五维度打分                     │
│  → 写 反馈报告.md                              │
└───────────────────────────────────────────────┘
                    ↓
┌─ 主会话 Phase 6 ─────────────────────────────┐
│  写 DOCKER_RUN_GUIDE.md                       │
│  写 environment/ 文件                          │
└───────────────────────────────────────────────┘
                    ↓
┌─ Phase 7 (可选, 并行子智能体) ────────────────┐
│  每模型派一个子智能体读对话文件 → 写对话分析     │
│  主会话将它们插入 反馈报告.md                   │
└───────────────────────────────────────────────┘
```

---

### Task 1: 新增内置 subagent prompt 附录

**文件:** `skill.md` (新增附录 section)

在 skill.md 末尾（完工自检清单之后）新增 `## 附录：子智能体 Prompt 模板` section，包含三个 prompt 模板。

- [ ] **Step 1: 新增「源码→solve→测试」子智能体 prompt 模板**

在 skill.md 末尾追加：

```markdown
## 附录：子智能体 Prompt 模板

### A. 模型源码处理子智能体 (Phase 2+4)

使用此模板为每个模型派发一个子智能体。`{PLACEHOLDERS}` 由主会话填入后发送。

```
你正在执行多轮后端评测流水线的「模型 {MODEL_LABEL}」处理环节。

**你的职责：**
1. 读懂该模型的源码
2. 生成自包含 solve 脚本
3. Docker 构建并运行测试
4. 回报结果

**禁止：**
- 修改模型源码（编译错误只记录，不修复）
- 修改 test.sh（测试失败只记录，不调整）
- 修改 solve 模板结构

---

## 输入材料

### A. Spec 提取结果

{EXTRACTED_SPEC_TEXT}

### B. Solve 脚本模板

读取 `{SOLVE_TEMPLATE_PATH}` 获取完整模板。

### C. test.sh

位于 `{TEST_SH_PATH}`，已由主会话生成，你的 solve 脚本在 Docker 模式下自动调用它。

### D. 模型源码

目录: `{MODEL_SOURCE_DIR}`

**必须读取该目录下每一个源文件**（跳过 target/、node_modules/、.git/）。记录文件清单。

---

## 执行步骤

### Step 1: 通读源码

递归列出所有文件（跳过编译产物/依赖）：
```
find {MODEL_SOURCE_DIR} -type f -not -path '*/target/*' -not -path '*/node_modules/*' -not -path '*/.git/*' | sort
```

逐个读取每个源文件。**即使文件数超过 20 个也必须全部读完。不允许跳过。**

### Step 2: 生成 solve 脚本

写入 `{OUTPUT_DIR}/solution/solve_{MODEL_LABEL}.sh`。

按 solve 模板规则填充：
- Part 0 (env check): 从 spec 提取结果填占位符
- Part 1 (host mode): 将每份源码嵌入 heredoc。**文件数 ≥ 20 时用 base64-encoded tar.gz。**
- Part 2 (docker mode): 从 spec 提取结果填 Docker 相关占位符

占位符映射参考 spec 提取结果中的 TECH_STACK / INFRA / API 等字段。

### Step 3: Docker E2E 测试

```bash
cd {OUTPUT_DIR}
MSYS_NO_PATHCONV=1 bash solution/solve_{MODEL_LABEL}.sh --docker
```

脚本内部自动完成：env 检查 → 写 Docker 构件 → docker build → docker run → exec build → exec start → health check → cp test.sh → exec test.sh → cleanup

**捕获全部输出并保留在回报中。**

### Step 4: 代码审查

对比 spec 提取结果审查该模型实现：
- API 路径/方法是否匹配
- 数据模型是否匹配（表名、字段、类型）
- 状态机转换规则是否正确
- 错误码/message 是否精确匹配
- 鉴权逻辑是否正确（401/404/400 优先级）
- 硬性不变量是否满足

### Step 5: 回报

按以下格式输出最终结果：

```
## 模型 {MODEL_LABEL} 回报

### 1. 源码清单
(列出所有读取的源文件路径，一个一行)

### 2. 构建结果
- 编译: PASS / FAIL
- 如 FAIL: (粘贴编译错误全文)

### 3. 黑盒测试结果
- 通过: X / 失败: Y / 断言总数: Z
- 失败详情: (逐项列出)

### 4. 代码审查
- API 合规: (逐 API 端点说明匹配/不匹配)
- 数据模型合规: (逐表/字段说明)
- 状态机合规: (检查迁移规则)
- 错误码合规: (检查 message 精确匹配)
- 鉴权合规: (检查 401/404/400 优先级)
- 硬性不变量: (逐条检查 #1-#5)

### 5. 五维度预打分 (0-4)
| 维度 | 分数 | 说明 |
|------|------|------|
| 指令与约束遵循 | X | ... |
| 功能交付完整性 | X | ... |
| 任务完成效率 | X | ... |
| 架构合理性 | X | ... |
| 上下文理解 | X | ... |

### 6. 发现的模型 Bug
(逐条列出，含文件路径和行号)
```
```
```

- [ ] **Step 2: 新增「对话分析」子智能体 prompt 模板**

在附录中追加 B 模板：

```markdown
### B. 对话分析子智能体 (Phase 7)

```
你正在执行多轮后端评测的「模型 {MODEL_LABEL}」对话分析。

## 输入

1. Spec 文件: {SPEC_FILE} (重点读「多轮脚本」section)
2. 模型对话: {CONV_FILE_PATH} (完整 MD 对话记录)
3. 已完成报告: {OUTPUT_DIR}/反馈报告.md (已有该模型的测试结果与解决概况)

## 职责

为此模型在 反馈报告.md 中已完成的结果基础上，写入逐轮「对话分析」subsection。

## 规则

- **直接读取对话源文件**——不要依赖任何 summary 或缓存中的轮次数。
- 提取每一个 `<user_query>` 标记作为新轮次。
- `<previous_user_message>` 标记是历史回放，不是新轮次。
- 每轮与 spec 轮次做 semantic match。
- 补充轮次按时间顺序穿插在 spec 轮次之间。
- 每个 spec-matched 轮次 ≥ 3 段主题分析。补充轮次 ≥ 2 段。
- 所有标签来自标准集合：规划方式 / 自诊断能力 / 修复方法论 / 架构决策 / 盲点与遗漏 / 跨模型对比
- 按 Phase 5.2 模板格式写入。

## 输出

直接编辑 {OUTPUT_DIR}/反馈报告.md，在模型 {MODEL_LABEL} 的 section 中每个轮次的「结果」与「优点」之间插入「对话分析」subsection。
```
```
```

- [ ] **Step 3: 新增「结果聚合」指导段落**

在附录中追加 C 段落（此为主会话 Phase 5 的聚合逻辑，非子智能体）：

```markdown
### C. 主会话结果聚合指导 (Phase 5)

主会话完成以下聚合工作：

1. 读取所有子智能体回报，提取：构建状态 / 测试成绩 / 代码审查发现 / 五维度预打分
2. 按 Phase 5.2 模板生成 反馈报告.md，每个模型一个 `## 模型 X 评测` section
3. 填写横向对比表（黑盒成绩 + 五维度对比 + 关键发现）
4. 区分「模型代码问题」vs「基础设施/测试脚本问题」
5. 填写「已识别的非模型问题」section
```
```

---

### Task 2: 重写 Phase 2 — 从主会话读码改为子智能体调度

**文件:** `skill.md` Phase 2 section

- [ ] **Step 1: 定位 Phase 2 当前内容**

Phase 2 目前包含：
- 2.1 Part 0: Env Check (填占位符)
- 2.2 Part 1: Host Mode (读源码 → heredoc 嵌入)
- 2.3 Part 2: Docker Mode (填 Docker 占位符)
- 各种占位符映射表

全部替换。

- [ ] **Step 2: 写入新 Phase 2 内容**

```markdown
## Phase 2: Dispatch Per-Model Subagents (Generate Solve + Test)

**CRITICAL: 主会话不读任何模型源码。所有需要读模型文件的工作由子智能体完成。**

### 2.1 准备 Spec 提取结果

将 Phase 1 提取结果整理为子智能体可消费的文本块。格式如下，用真实提取值替换所有 `{}`：

```
## TECH_STACK
- Language: {java_version} / {build_tool}
- Framework: {spring_boot_version}
- DB: {db_image} (HAS_DB={true|false})
- MQ: {mq_image} (HAS_MQ={true|false})
- China Network: {true|false}

## INFRA
- DB_IMAGE: {image}
- DB_PORT: {port}
- DB_USER/PASS/NAME: {user}/{pass}/{db}
- MQ_IMAGE: {image}
- MQ_PORT: {port}
- MQ_TOPIC: {topic}
- MQ_CONSUMER_GROUP: {group}
- APP_PORT: 8080
- HEALTH_PATH: {health_path}
- BASE_URL: http://127.0.0.1:8080

## API_SPEC
(逐 API 列出 method + path + headers + response，从 Phase 1.3 提取)

## DATA_MODEL
(逐表列出 columns + types + constraints，从 Phase 1.4 提取)

## STATE_TRANSITIONS
(逐条 event_type → old_status → new_status，从 Phase 1.5 提取)

## ERROR_CODES
(逐条 code + message + trigger，从 Phase 1.6 提取)

## AUTH
- Admin header: {X-Admin-Token}
- Admin token value: {dev-admin-token}
- Buyer header: {X-Buyer-Id}
- Priority: 401 > 404 > 400
- Health excluded: true

## INVARIANTS
(逐条列出，从 Phase 1.7 提取)

## PITFALLS
(逐条列出，从 Phase 1.9 提取)

## TEST_SCENARIOS
(逐条列出 T1-T12 对应的测试场景和断言，从 Phase 1.9 coverage map 提取)

## BUILD_CMD
{build_command}

## START_CMD
{start_command}

## MIRROR_CONFIG (if China)
{mirror_config_text}
```

### 2.2 检测模型列表

```bash
find "$MODEL_DIR" -maxdepth 1 -mindepth 1 -type d | sort
```

每个子目录 = 一个模型，子目录名 = 模型标识。

### 2.3 并行派发子智能体

为每个检测到的模型，使用 Codex 当前可用的多代理能力并行派发子智能体。Prompt 来自附录 A 模板，替换以下占位符：

| Placeholder | Value |
|-------------|-------|
| `{MODEL_LABEL}` | 子目录名 (e.g., A, B, actor-graph) |
| `{EXTRACTED_SPEC_TEXT}` | 2.1 生成的文本块 |
| `{SOLVE_TEMPLATE_PATH}` | `templates/solve_template.sh`（相对当前 Skill 根目录解析） |
| `{TEST_SH_PATH}` | `$OUTPUT_DIR/tests/test.sh` |
| `{MODEL_SOURCE_DIR}` | `$MODEL_DIR/{MODEL_LABEL}` |
| `{OUTPUT_DIR}` | 用户提供的输出目录 |

**并行派发 = 所有模型同时启动子智能体。** 子智能体互相独立，无共享状态。

### 2.4 等待全部完成

子智能体完成后返回结构化回报（见附录 A 格式）。主会话收集所有回报，转 Phase 5 聚合。
```

---

### Task 3: 删除独立的 Phase 4 section

**文件:** `skill.md`

- [ ] **Step 1: 删除或标记 Phase 4 section 为废弃**

Phase 4 (Docker E2E Test) 已合并到每模型子智能体中。将 Phase 4 section 内容替换为：

```markdown
## Phase 4: Docker E2E Test

> **已合并入 Phase 2: 每模型子智能体在执行 solve 脚本时自动完成 Docker 构建和测试。** 此处保留 section 编号以维护 Phase 编号连续性。
```

---

### Task 4: 重写 Phase 5 — 从子智能体回报聚合

**文件:** `skill.md` Phase 5 section

- [ ] **Step 1: 修改 Phase 5 开头**

将 Phase 5 的 "5.1 Collect Per-Model Findings" 修改为从子智能体回报收集：

```markdown
### 5.1 收集子智能体回报

每个子智能体返回的结构化回报包含：
- 源码清单
- 构建结果（编译 PASS/FAIL + 错误全文）
- 黑盒测试结果（通过/失败/断言数 + 失败详情）
- 代码审查发现（API/数据模型/状态机/错误码/鉴权/不变量逐项检查）
- 五维度预打分 + 说明
- 发现的模型 Bug 列表

**验证规则**：如果子智能体回报中「源码清单」缺少模型目录下任何源文件，该回报标记为不完整，对应的模型 section 中注明「子智能体未完成全量源码审查」。
```

- [ ] **Step 2: 修改 5.2「生成反馈报告」**

追加聚合指引：

```markdown
### 5.2 生成反馈报告

报告结构与原来相同（见 5.2 模板）。生成时：

1. 每个模型 section 的「黑盒测试结果」直接使用子智能体回报的测试数据
2. 五维度打分基于子智能体预打分，主会话可根据跨模型一致性做 ±1 调整
3. 「总体评价」由主会话基于子智能体的代码审查发现撰写
4. 「逐轮结果反馈」先留空，待 Phase 7 填写对话分析
5. 横向对比表使用子智能体回报的测试通过/失败数据
6. 「关键发现」由主会话做跨模型对比后撰写
```

---

### Task 5: 重写 Phase 7 — 对话分析也走子智能体

**文件:** `skill.md` Phase 7 section

- [ ] **Step 1: 修改 Phase 7 为子智能体调度模式**

```markdown
## Phase 7: Deep Conversation Analysis

### Trigger

Phase 5 完成且用户提供了 `$CONV_DIR`。检测对话文件：
```bash
find "$CONV_DIR" -name "*模型.md" | sort
```

### Process

**每模型派一个子智能体**，Prompt 来自附录 B 模板，替换占位符：

| Placeholder | Value |
|-------------|-------|
| `{MODEL_LABEL}` | 模型标识 |
| `{SPEC_FILE}` | `$SPEC_FILE` 路径 |
| `{CONV_FILE_PATH}` | `$CONV_DIR/{MODEL_LABEL}模型.md` |
| `{OUTPUT_DIR}` | 用户提供的输出目录 |

子智能体直接编辑 `{OUTPUT_DIR}/反馈报告.md`，在该模型 section 的每轮中插入「对话分析」subsection。

**子智能体互相独立，可并行派发。**

主会话在全部子智能体完成后，做最终检查：
- 每轮是否有 ≥ 3 段主题分析
- 是否使用了标准标签集合
- 补充轮次是否按时间顺序穿插
```

---

### Task 6: 更新 Overview 描述

**文件:** `skill.md` Overview section

- [ ] **Step 1: 修改 Overview 中 pipeline 描述**

将：
```
4. **Docker E2E Test** — for each model, execute...
5. **Report** — aggregate per-model findings...
```

改为：
```
4. **Per-Model Dispatch** — for each model, a subagent reads source, generates solve script, runs Docker test, returns structured report
5. **Aggregate** — collect all subagent reports into `反馈报告.md`
```

---

### Task 7: 更新完工自检清单

**文件:** `skill.md` Appendix 完工自检清单

- [ ] **Step 1: 更新 Phase 2 清单项**

将：
```markdown
### Phase 2: Solve Scripts（每个模型一个）
- [ ] solve 脚本包含 Part 0...
```

改为：
```markdown
### Phase 2: Per-Model Dispatch
- [ ] Spec 提取结果已整理为子智能体输入文本
- [ ] 所有模型子智能体已并行派发
- [ ] 所有子智能体回报已收集
- [ ] 子智能体回报中源码清单已验证完整性（与源目录文件数一致）
```

- [ ] **Step 2: 更新 Phase 4 清单项**

删除原独立测试项，标记为已在 Phase 2 子智能体中完成。

- [ ] **Step 3: 更新 Phase 5 清单项**

追加：
```markdown
- [ ] 各模型数据来自子智能体回报（非主会话直接读取）
```

---

### Task 8: 自检

- [ ] **Step 1: Placeholder scan**

逐 section 检查新内容中是否有 TBD / TODO / 「待定」等占位符。如有，用具体内容替换。

- [ ] **Step 2: 引用一致性检查**

确认：
- Phase 2 引用的「附录 A」确实存在于 Task 1 创建的内容中
- Phase 7 引用的「附录 B」确实存在
- 所有占位符名称在各处一致

- [ ] **Step 3: 与现有模板的兼容性**

确认：
- `solve_template.sh` 格式不变，子智能体可以继续使用
- `test_template.sh` 格式不变，主会话 Phase 3 继续使用
- Phase 1 提取格式唯一一处定义（避免 Phase 1 和 Phase 2.1 有不一致版本）

- [ ] **Step 4: 回退兼容**

如果 `$MODEL_DIR` 只有一个模型，不并行（只派 1 个子智能体）。无行为退化。

- [ ] **Step 5: 边界检查**

确认当模型数 > 10 时，当前 Codex 多代理能力仍可并行派发或按批次安全执行。
```
