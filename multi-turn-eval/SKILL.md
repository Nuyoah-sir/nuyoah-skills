---
name: multi-turn-eval
description: Use when running a multi-turn backend Coding Agent evaluation for multiple model output directories from a spec, including requests such as "evaluate these models", "run multi-turn eval", "评测这些模型", or "跑多轮评测". Do not use for question design or polishing, standalone rubric review, scoring existing artifacts only, Vibe forms, or ordinary project testing.
---

# Multi-Turn Backend Eval Pipeline

## Overview

7-phase evaluation pipeline (quality-context extraction adds an 8th phase when the spec declares quality dimensions):

1. **Parse Spec** — extract tech stack, API paths, data model, state transitions, error codes, invariants, seed data from spec markdown; **if the spec declares quality dimensions (QR task): extract quality context (Phase 1.11)**
2. **Generate Solve Scripts** — produce `solve_<model>.sh` (one per model), each self-contained with 3 parts: env check, host mode (build+start), Docker mode (build image → test → cleanup)
3. **Generate test.sh + Rubric.json** — produce black-box HTTP test suite from spec; simultaneously generate Rubric scoring rubric
4. **Per-Model Dispatch** — for each model, a subagent reads source, generates solve script, runs Docker test, returns structured report
5. **Aggregate + Rubric Scoring** — collect all subagent reports into `反馈报告.md` with comparison tables and D1-D5 5-dimension scores (0-4); dispatch per-model Rubric scoring subagents to produce `Rubric_X打分结果.json`
6. **Manual Test Guide** — write `DOCKER_RUN_GUIDE.md` with reproducible commands and troubleshooting
7. **Deep Analysis** — read raw model conversation files (`*模型.md`), produce per-round "对话分析" subsections
8. **Rubric Enrichment** — after Phase 7 reveals model behavioral differences, the orchestration session enriches Rubric.json with evidence-backed entries, re-scores them, and preserves the complete unpruned set

## Codex execution and write boundary

- Resolve `SKILL_DIR` as the directory containing this `SKILL.md`; bundled resources are under `SKILL_DIR/templates/`.
- Treat `SPEC_FILE`, `MODEL_DIR`, `CONV_DIR`, `SESSION_FILE`, bundled templates, and every model source tree as read-only.
- Author or modify evaluation artifacts only under the user-provided `OUTPUT_DIR`. Generated solve/test scripts may use short-lived OS temporary files and Docker containers at runtime, but no authored deliverable belongs outside `OUTPUT_DIR`.
- Use the file inspection, patching/writing, command execution, planning, and subagent capabilities available in the current Codex session; do not assume platform-specific tool aliases.
- Parallelize independent per-model work when subagents are available. Otherwise execute the same per-model protocols sequentially and state that fallback in the report; behavior and evidence requirements do not change.

### Output Directory Structure

```
$OUTPUT_DIR/
├── 反馈报告.md
├── instruction.md
├── DOCKER_RUN_GUIDE.md
├── Rubric.json
├── Rubric打分结果/
│   ├── Rubric_<model1>打分结果.json
│   ├── Rubric_<model2>打分结果.json
│   └── ...
├── solution/
│   ├── solve_<model1>.sh    ← 自包含：env_check + host + docker
│   ├── solve_<model2>.sh
│   └── ...
└── tests/
    └── test.sh
```

## Entry: Gather Parameters

If user has not provided all paths, ask:

```
请提供：
1. 题目 spec 文件路径（$SPEC_FILE）：
2. 模型输出目录（$MODEL_DIR，内含各模型子文件夹，如 A/、B/ 或任意命名）：
3. 做题输出目录（$OUTPUT_DIR）：
4. 模型对话目录（$CONV_DIR，内含各模型对话文件如 A模型.md、B模型.md 等，可选——不提供则跳过 Phase 7 深度分析）：
5. 会话记录文件（$SESSION_FILE，$CONV_DIR/会话记录/request-usage-*.xlsx，可选——用于填写各轮 Session ID）：
```

Fixed assumptions:
- Model count: **dynamic** — auto-detect all subdirectories under `$MODEL_DIR` (each subdirectory = one model)
- Model identifiers: use the subdirectory names as model labels (e.g., if `$MODEL_DIR` contains `A/`, `B/`, then models are "A" and "B")
- App port: fixed 8080
- Conversation files: auto-detected under `$CONV_DIR` by pattern `*模型.md` (e.g., `A模型.md`, `B模型.md`) — one file per model
- Session ID source: `$SESSION_FILE` (Excel, preferred) or extracted from conversation file `Session ID` markers
- Phase 7 is optional: skipped if `$CONV_DIR` not provided or conversation files absent
- Everything else (tech stack, Docker images, ports, API paths) is derived from spec

**Data source roles for round-level analysis:**

| Source | Role |
|--------|------|
| Spec "多轮脚本" section | **Baseline reference framework** — defines expected prompt intent per round (R0-R6). Used for "人为补充 Prompt 大意" table. |
| Model conversation MD files (`*模型.md`) | **Authoritative for actual rounds** — contains every real user-model exchange. Actual round count may exceed spec rounds (e.g., spec=7 rounds, actual=12). |
| Session records Excel | **Authoritative for Session IDs** — one row per session. Used to backfill Session ID into each round's heading. |

**Round matching logic:** Spec rounds are mapped to actual conversation rounds via semantic matching (content similarity). Actual rounds that don't match any spec round are labeled "补充轮次" and inserted in their chronological position among spec rounds.

## Phase 1: Parse Spec

Read `$SPEC_FILE`. Extract the following into memory (not written to disk):

### 1.1 Tech Stack
Search for versioned component names. Map to solve Part 0 (env check) items and build commands:

| Pattern in spec | env check item | build command |
|-----------------|---------------|---------------|
| Java 17+ / JDK 21 | `java --version` (expect 17+) | `mvn clean package -DskipTests -q` |
| Spring Boot 3.3.x | (no check, Maven handles) | — |
| PostgreSQL 15+ | `docker pull postgres:16-alpine` | — |
| Kafka / Redpanda | `docker pull kafka-local:latest` (or detect image name) | — |
| Node.js / npm | `node --version` | `npm install && npm start` |
| Go | `go version` | `go build -o app .` |
| Python / pip | `python3 --version` | `pip install -r requirements.txt` |
| Maven wrapper | Check for `mvnw` in model source | Use `./mvnw clean package -DskipTests -q` if present |
| Gradle | Check for `gradlew` in model source | Use `./gradlew build -x test -q` if present |

### 1.2 Docker Images & Infrastructure Mode

Extract from spec infrastructure mentions:
- **Database image**: e.g. `postgres:16-alpine` (from "PostgreSQL 15+")
- **Message queue image**: e.g. `kafka-local:latest` (from "Kafka") — check if image exists locally; if not, note in env check (solve Part 0)
- **DB credentials**: user/password/dbname — from spec or default to `postgres/postgres/<dbname>`
- **DB port**: from spec (default 5432)
- **MQ port**: from spec (default 9092)
- **MQ topic**: from spec (e.g. `procurement.po_line.lifecycle.v1`)
- **Consumer group**: from spec (e.g. `po-line-lifecycle-consumer`)

**Lightweight mode detection** — set these flags for template conditional blocks:
- `HAS_DB=true` if spec requires external database (PostgreSQL, MySQL, etc.). Projects using H2/SQLite in-memory → `HAS_DB=false`.
- `HAS_MQ=true` if spec requires message queue (Kafka, Redpanda, RabbitMQ). No MQ mentioned → `HAS_MQ=false`.
- `HAS_DB_OR_MQ` = `HAS_DB || HAS_MQ` (used for infrastructure wait block).
- `CHINA_MIRROR=true` if network type is "domestic" (detected in Part 0.1).

When `HAS_DB=false`, skip: `{{#IF HAS_DB}}` blocks (PG container, entrypoint PG startup, Dockerfile postgresql packages).
When `HAS_MQ=false`, skip: `{{#IF HAS_MQ}}` blocks (Redpanda container, redpanda.yaml, entrypoint Redpanda startup, Dockerfile Redpanda install).
When `HAS_DB_OR_MQ=false`, skip: infrastructure wait entirely.

### 1.3 API Endpoints
Extract every HTTP method + path + auth requirement + response format. Example extraction:

```
GET  /api/health                        → no auth → {"status":"ok"}
GET  /api/po-lines/{id}                 → X-Buyer-Id + X-Admin-Token → 200 {code:0,data:{...}} / 401 / 404
POST /api/admin/events/publish          → X-Admin-Token → 202 / 400 / 401 / 404
GET  /api/admin/events/{event_id}       → X-Admin-Token → 200 {code:0,data:{result,...}} / 404
GET  /api/admin/po-lines/{po_line_id}/processed-events?limit=N → X-Admin-Token → 200 {code:0,data:{items,total}} / 404
```

These drive test.sh API wrappers and assertions.

### 1.4 Data Model & Seed
Extract table definitions and seed data. Key for test.sh T2 assertions:
- Table columns: name, type, constraints
- Seed: buyer entries (id, name), po_line entries (id, buyer_id, sku, quantity, status)
- Note which columns are nullable (last_event_at, last_event_id)
- **种子可用性判定（v1.4 起，MANDATORY）**：模型只收到各轮 prompt——出题人注/终态契约不是 prompt。若 spec 出题人注声明"评测侧数据构造指引（非种子数据）"或"不提供逐行种子"，则**不提取种子值、不写 T2 种子断言**（模型收不到种子，断言必挂——实测教训：exam-monitor v1 四模型 9/123 同分，根因即 test.sh 依赖未传达的种子/预置 token）。该场景下 test.sh 改为"setup 构造 + 读回断言"（见 Phase 3"test.sh 数据构造与场景覆盖约束"）。

### 1.5 State Transition Table
Extract `event_type → old_status → new_status` mapping. Identify terminal states (received, canceled). This drives test.sh T3, T6, T7, T12.

### 1.6 Error Code Table
Extract code/message/trigger mapping with the priority rule (401 > 404 > 400). This drives test.sh T9, T10 with exact message assertions.

### 1.7 Hard Invariants
Extract numbered invariant rules. Key patterns:
1. Idempotency: same event_id → po_line.status changes at most once
2. Single transaction: processed_event + po_line in same DB transaction
3. Stale detection: occurred_at strictly less than last_event_at → do NOT modify
4. Kafka ack: MANUAL, after DB transaction commit
5. Concurrent safety: same event_id → at most one applied

### 1.8 Auth Scheme
Extract:
- Admin token header name and default value
- Buyer ID header name
- Buyer isolation rule (cross-buyer → 404)

### 1.9 Test Coverage Baseline (MANDATORY)

**MUST locate** the `## 多轮脚本` marker in the spec file. The content **before** this marker contains two critical sections that are **required reading** — not optional.

**If either section is missing** from the spec file, **MUST ask the user** to confirm test scenarios — do not guess or fabricate.

**"难点坑点" (Pitfalls) section** — Lists N core pitfalls (e.g., event_id business idempotency, same-transaction + ack ordering, stale rollback prevention, state machine + terminal states, HTTP vs consumer result layering, Spring validation envelope shape).

**MUST extract every pitfall** as a hard constraint and map each to a test.sh verification:
- "event_id business idempotency" → same event_id replay MUST NOT change po_line.status
- "same-transaction + ack ordering" → DB commit MUST happen before Kafka ack
- (continue for every pitfall found in spec)

**"如何写单元测试" (How to Write Unit Tests) section** — Divided into "正向对照" (positive cases) and "错误语义" (error semantics).

**MUST extract every scenario** from both subsections. These form the **minimum test coverage baseline** — no scenario may be skipped:
- Positive scenarios:
  - Health check + seed data readback
  - shipped → applied → in_transit
  - Same event_id → duplicate, status unchanged
  - delayed → transit_resumed → received legal chain
  - List API: {items, total} descending, respects limit truncation
- Error scenarios:
  - Stale: newer event applied first, older event with earlier timestamp → stale
  - Rejected: illegal transition (e.g., delayed on open); event after terminal state (e.g., shipped after received)
  - Auth: 401 > 404 > 400 priority per error table
  - Validation: publish with invalid event_type / event_id / occurred_at / po_line_id → exact 400 message
  - Concurrent: same event_id → at most one applied

**MUST produce a coverage map** before generating test.sh in Phase 3.2: each 1.9 scenario → corresponding T number (T1-T12). No scenario left unmapped. This map is the input to the Phase 3.2 cross-reference checklist.

These scenarios are the **authoritative** end-to-end semantics source. Phase 1.3–1.7 provides precise API signatures, status tables, and error code tables (the "what"); Phase 1.9 provides the "how to verify" (the scenarios that prove correctness).

### 1.10 抽取多轮脚本

从 spec 中提取 `## 多轮脚本` section 的全部内容，写入 `$OUTPUT_DIR/instruction.md`。

**提取规则：**
- 定位 spec 中 `## 多轮脚本` 标记
- 提取从该标记到文件末尾的所有内容（含各轮 prompt 原文、技术规格、数据模型等 prompt 中向模型传达的全部信息）
- 如 spec 无 `## 多轮脚本` 标记，回退为复制整个 spec
- 轮次数不固定，spec 有几轮就记录几轮

**用途：** instruction.md 记录评测方通过 prompt 向模型传达的全部指令，作为评测过程的输入证据。

### 1.11 质量维度上下文提取（Quality Context Extraction）

**触发条件**：spec 题目元信息/设计文档中存在 `质量维度` 字段且非空（快速题 QR 题声明 1-3 个质量维度；常规题无此字段）。

**提取逻辑**：
1. 读 `质量维度` → 解析质量维度列表（如 幂等 / 并发安全 / 金额精度 / 权限边界）
2. 读 `难点坑点` → 提取与各质量维度对应的"不达标表现/出色表现"描述（来自出题技能 Step Q1 质量维度池的语义）
3. 读 `多轮脚本` R-final → 提取自述机制要求（"请说明你如何保证 XX"）
4. 组装为 `QUALITY_CONTEXT` 文本块

**QUALITY_CONTEXT 格式**：

```
## QUALITY_CONTEXT
- 质量维度: {幂等 / 并发安全 / ...（1-3 个）}
- 不达标表现: {弱模型会怎么做（朴素实现）}
- 出色表现: {强模型应该怎么做}
- 代码证据位置: {在源码中查找什么——表名/函数名/SQL 模式}
- 验证方法: {如何从源码/测试中判定模型表现}
- R-final 自述机制: {模型是否说清如何保证该维度——自述 vs 现实的差距是重要区分信号}
```

**用途**：QUALITY_CONTEXT 注入 Phase 2 子智能体（代码审查追加质量维度专项）、Phase 3.3（Rubric 来源 E）、Phase 5.2（反馈报告新增"质量维度结果"section）、Phase 7（对话分析新增"质量维度察觉"维度）。

**如果 `质量维度` 字段不存在**：跳过质量维度感知分析，后续 Phase 按常规模式执行。QUALITY_CONTEXT 不存在时，Phase 2/3.3/5.2/7 中的质量维度专项检查全部静默跳过。

**注意**：快速题使用 QR 质量维度语义；不得生成旧模式的分阶段诱导分析。

## Phase 2: Dispatch Per-Model Subagents (Generate Solve + Test)

**CRITICAL：有子代理能力时，主协调会话不读模型源码；每个模型由独立子代理全量读取和审查。若当前 Codex 环境没有可用的多代理能力，则主协调会话按模型逐个顺序执行同一附录 A 协议，不得跳过全量源码读取、Docker 测试、代码审查或结构化回报。**

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
- Admin header: X-Admin-Token
- Admin token value: dev-admin-token (default)
- Buyer header: X-Buyer-Id
- Priority: 401 > 404 > 400
- Health excluded: true

## INVARIANTS
(逐条列出，从 Phase 1.7 提取)

## PITFALLS
(逐条列出，从 Phase 1.9「难点坑点」提取)

## TEST_SCENARIOS
(逐条列出 T1-T12 对应的测试场景和断言，从 Phase 1.9 coverage map 提取)

## BUILD_CMD
{build_command}

## START_CMD
{start_command}

## MIRROR_CONFIG (if China network)
{mirror_config_text}
```

### 2.2 检测模型列表

```bash
find "$MODEL_DIR" -maxdepth 1 -mindepth 1 -type d | sort
```

每个子目录 = 一个模型，子目录名 = 模型标识。

### 2.3 并行派发子智能体

为每个检测到的模型，使用 Codex 当前可用的子代理或多代理能力并行派发一个独立任务。Prompt 模板来自下方附录 A，替换以下占位符：

| Placeholder | Value |
|-------------|-------|
| `{MODEL_LABEL}` | 子目录名 (e.g., A, B, actor-graph) |
| `{EXTRACTED_SPEC_TEXT}` | 2.1 生成的文本块 |
| `{SOLVE_TEMPLATE_PATH}` | `{SKILL_DIR}/templates/solve_template.sh` |
| `{TEST_SH_PATH}` | `$OUTPUT_DIR/tests/test.sh` |
| `{MODEL_SOURCE_DIR}` | `$MODEL_DIR/{MODEL_LABEL}` |
| `{OUTPUT_DIR}` | 用户提供的输出目录 |

**并行派发 = 所有模型同时启动子智能体。** 子智能体互相独立，无共享状态。

若当前环境没有可用子代理，则按模型标识排序顺序执行附录 A；每个模型完成并固化结构化回报后再处理下一个，避免交叉污染。

### 2.4 等待全部完成

子智能体完成后返回结构化回报（格式见附录 A）。主会话收集所有回报，转 Phase 5 聚合。

## Phase 3: Generate test.sh

### test.sh 数据构造与场景覆盖约束（v1.4 起，MANDATORY）

**原则：test.sh 用已知 API 按顺序组合去创造场景**——不是从 spec 复制预置数据断言，而是"构造 + 断言"两步走。模型只收到各轮 prompt，spec 出题人注中的种子/预置 token 模型不可见，断言它们必然全灭。

1. **禁止依赖模型不可见的预置数据**：spec 中任何种子数据、预置 token 的具体值，模型均不可见（实测教训：exam-monitor v1 四模型 9/123 同分，根因即 test.sh 依赖未传达的种子/预置 token）。
2. **自带 setup（用 API 组合创造场景）**：test.sh 必须包含数据构造段——login 生成各角色 token → 建考试 → 加题 → 发布 → start-exam → answers → submit → grade → flags/confirm/regrade，按 API 顺序组合创造每个场景所需数据；所有断言使用 setup 阶段拿到的真实 id，不依赖固定 id。
3. **场景覆盖完全（多组合场景都要复现）**：单功能点之外，多组合场景必须覆盖——窗口×幂等、标记×确认×判分时序、排名×变化×原子性、身份×权限（token 身份/资源归属/角色矩阵）、错误优先级（401>404>409>403>400 的组合条件，如"无 token 调不存在考试 → 401 而非 404"）、并发（恰一次成功）、状态机非法流转、删除/重置语义。组合场景能产出远超单点的断言数。
4. **环境重置（组间零共享状态）**：每个互不关联的测试场景组，测完必须重置环境。题目无 DELETE 接口时，"重置"= 每组从新建的独立数据开始（新建考试/独立学生）；有 DELETE 接口时可用 DELETE 清理。**组间禁止共享可变数据**——某组失败不级联、任一组可单独重跑。组内强相关断言可组成"场景流"（一个考试的自然生命周期故事），顺序依赖是故事的一部分并显式注释前提。
5. **可控序列**：排名/列表等断言用脚本控制的构造顺序推导可预测结果（如同分学生按脚本提交顺序排，脚本先提交者名次靠前）。
6. **宽容断言**：prompt 未定义的语义（如 409 内部优先级、空考试边界、无效 id）只断言状态码/服务不崩溃，不断言精确消息。

Read [templates/test_template.sh](templates/test_template.sh), resolved relative to this `SKILL.md`, and fill its placeholders with spec-extracted values.

**Generate `{{API_WRAPPERS}}`:**

```bash
publish() {
    local eid="$1" plid="$2" typ="$3" oca="$4"
    http POST "<publish_path>" \
        -H "Content-Type: application/json" \
        -H "<admin_header>: <admin_token>" \
        -d "{\"event_id\":\"$eid\",\"po_line_id\":$plid,\"event_type\":\"$typ\",\"occurred_at\":\"$oca\"}"
}

get_po_line() {
    local bid="$1" lid="$2"
    http GET "<po_line_path>" -H "<buyer_header>: $bid" -H "<admin_header>: <admin_token>"
}

get_event() {
    http GET "<event_path>" -H "<admin_header>: <admin_token>"
}

list_events() {
    local lid="$1" lim="${2:-50}"
    http GET "<list_path>?limit=$lim" -H "<admin_header>: <admin_token>"
}
```

Substitute `<publish_path>`, `<po_line_path>`, `<event_path>`, `<list_path>`, `<admin_header>`, `<buyer_header>`, `<admin_token>` from Phase 1.3 and 1.8.

**jq safety rules** (MUST follow when writing TEST_CASES):

1. **`json_get` always safe** — the template function already has `|| true` after the jq pipeline. No additional `|| true` needed at call sites.
2. **Boolean comparisons use lowercase** — jq returns JSON `true`/`false`, NOT Python `True`/`False`. Use `"true"` in assert_eq.
3. **Array indexing uses bracket notation** — `.data.items[0].name` works; `.data.items.0.name` is a jq syntax error.
4. **Service wait accepts 200/401/403/404** — the template pre-check loop already handles auth-required endpoints.

**Auth fallback strategy (MANDATORY):**

The template provides `http_or_auth` — a drop-in replacement for `http` that implements "try public first, fallback to authenticated":

- `http_or_auth <method> <path> [curl args...]` — same signature as `http`, sets `CURL_CODE`/`HTTP_BODY` identically
- If the endpoint returns 401/403 and `bootstrap_auth()` has obtained a token, it automatically retries with `Authorization: Bearer <token>`
- Sets `USED_AUTH=1` if fallback was triggered (test code can branch on this)
- `bootstrap_auth()` is called automatically after pre-check — registers a test user + logs in to get a token

**When to use `http_or_auth` vs `http`:**

| Endpoint type | Use | Rationale |
|---------------|-----|-----------|
| Spec says **public** (no auth required) | `http_or_auth` | Detect auth compliance failure AND validate business logic behind auth wall |
| Spec says **auth required** (admin-only) | `http` + explicit auth header | Auth is expected — test should pass auth deliberately, not via fallback |
| Auth endpoints (register/login) | `http` | Self-referential — can't use fallback to bootstrap itself |

**Test case pattern for public endpoints:**

```bash
# Using http_or_auth for a spec-public endpoint
http_or_auth GET "/api/actors?page=1&size=10"
if [ "$USED_AUTH" -eq 1 ]; then
    FAIL "T2.0: GET /api/actors requires auth (spec says public)"
fi
assert_status "T2.1: Actor list responds" "200" "$CURL_CODE"
# ... business logic assertions on $HTTP_BODY work regardless of auth path
```

This ensures each test produces two independent signals:
1. **Auth compliance**: did the endpoint require auth when spec said public? (FAIL if yes)
2. **Business logic**: does the endpoint return correct data? (PASS/FAIL based on actual response)

The `AUTH_FALLBACK_COUNT` in the summary tells you how many tests needed fallback — this number IS the auth compliance score.

**Cross-reference checklist (MANDATORY — execute before writing TEST_CASES):**

1. **List all scenarios** extracted in Phase 1.9 as a flat checklist.

2. **Assign T numbers.** For each scenario, assign a T number (T1-T12). Verify:
   - No scenario without a corresponding T
   - No T without a driving scenario from 1.9 or 1.3–1.7

3. **Boundary condition audit.** Check: `occurred_at` equal to `last_event_at` → NOT stale; Same `event_id` concurrent → at most one `applied`; Terminal states → reject all subsequent events; Cross-buyer access → 404.

4. **Error message assertions.** Every error message MUST use the **exact string** from the Phase 1.6 error code table. Do not rewrite, translate, or paraphrase.

5. **After generating TEST_CASES**, re-read Phase 1.9 extraction and confirm every scenario has a corresponding assertion block.

**Generate `{{TEST_CASES}}`** with these sections:

- **T1: Health** — `http GET <health_path>` → assert 200, assert body contains expected status
- **T2: Seed Data** — For each seed po_line: GET with correct buyer → assert 200, assert sku/status/quantity/buyer_id match. Cross-buyer → 404.
- **T3: First Event → Applied** — `publish` with gen_uuid + utc_now + first legal event_type → assert 202. wait_consume. get_event → assert 200, result=applied. get_po_line → assert status changed per transition table.
- **T4: Replay → Idempotent** — Publish SAME event_id again → assert 202. get_event → assert result unchanged. get_po_line → assert status unchanged.
- **T5: Stale Detection** — Publish event with utc_offset(+3600) → applied. Then publish event with utc_offset(-3600) → stale. get_po_line → assert status from later event preserved.
- **T6: Terminal → Rejected** — Drive a po_line to terminal. Then publish newer event → assert result=rejected.
- **T7: Legal Chain** — Cycle through full non-terminal chain. Assert each step.
- **T8: Equal Timestamp** — PASS by reasoning (strict isBefore, equal falls through to state machine).
- **T9: Auth Priority** — Test 401 > 404 > 400 per error table. Cross-buyer → 404. Missing token on non-existent resource → 401.
- **T10: Parameter Validation** — Each 400 error from Phase 1.6 with exact message assertion.
- **T11: List Processed Events** — list_events → assert 200, total is integer, limit truncation, 404 for non-existent po_line.
- **T12: Canceled Terminal → Rejected** — PASS (same mechanism as T6).

**Error message assertions MUST use the exact message strings from Phase 1.6 error table.**

### 3.3 Generate Rubric.json

**与 test.sh 同步生成** `$OUTPUT_DIR/Rubric.json`。

#### 什么是 Rubric

可以用 **是/否** 回答、反映模型真实能力差距的评分细则。"是"代表表现好（得1分），"否"代表表现差（得0分）。

Rubric 的作用是反映五维度打分未能反映的模型真实差距。后端多轮题的 rubric 不应只写"功能完整"。

#### Rubric 自检规则

每条 rubric 必须满足：

| 规则 | 说明 |
|------|------|
| **可验证** | 评审人能指出接口、测试、代码证据 |
| **原子化** | 一条 rubric 只判断一个点 |
| **有区分度** | 不同模型可能得到不同结论 |
| **避免空泛词** | 少写"合理、优雅、稳定"，改为具体证据 |

#### Rubric 条目属性

每条 rubric 包含以下属性：

| 属性 | 取值 | 说明 |
|------|------|------|
| `round` | `"R0"` ~ `"RN"` / `"All"` | Rx = 该 criterion 特指第 x 轮的行为（**R0 起**，如 `"R0"`、`"R1"`）；`"All"`（大写 A）= 该 criterion 涉及整个项目/跨轮评估 |
| `priority` | `Must have` / `Nice to have` | Must have = 不满足即为不合格；Nice to have = 满足则额外加分 |
| `dimension` | `Instruction Following` / `Feature Delivery` / `Task Efficiency` / `Architecture Quality` / `Context Understanding` | 对应 D1-D5 五维度（**注意 D4 的英文是 Architecture Quality，不是 Architecture**） |
| `necessity` | `Explicit` / `Implicit` | Explicit = spec 明确要求；Implicit = spec 隐含或业界默认预期 |
| `type` | `Objective` / `Subjective` | Objective = 有明确证据可判定；Subjective = 需评审人综合判断 |

#### 条目来源推导

**Rubric 覆盖两类考察点，产出后不剪枝**：① 能力差距考察点（实现方式/过程行为/合规点/质量维度——test.sh 测不了、必须人评或审源码的点）；② test.sh 测试要点考察点（黑盒断言对应的业务语义，MANDATORY 25%-50%）。**不因"所有模型都会做到"或"test.sh 已断言"而删除任何条目**（不剪枝，见下）；**不得因"test.sh 已断言"而省略 test.sh 要点条目**。

**候选池（5+1 个来源）：**

| 优先级 | 来源 | 说明 | 示例 |
|--------|------|------|------|
| **A（最高）** | Phase 1.9「单元测试难以覆盖、需要人类评估的点」 | 这是 rubric 的**天然来源**——test.sh 测不了、必须靠人审源码或文档的点 | "BFS是否用了ArrayDeque"、"图热重载是否原子引用替换"、"路径电影选择策略是否在文档中说明" |
| **B** | Phase 1.9「难点坑点」中 test.sh 无法黑盒验证的点 | 坑点分两类：能黑盒验证的→test.sh覆盖；不能的（如"用了什么数据结构""并发方案是什么"）→rubric覆盖 | "是否用AtomicReference而非原地修改"、"JSON校验是在启动时还是首次查询时执行" |
| **C** | 过程行为（从 Phase 7 对话分析提取） | 不只评最终交付物，也评"怎么做的"——模型是否自诊断、修复是最小化还是全量重写、是否在实现前做技术分析 | "R2 是否在实现前给出了技术分析"、"R3 修复是否是minimal fix而非大面积重写"、"是否在收到spec测试期望值后主动对照验证自己的输出" |
| **D** | Phase 1.3/1.6/1.8 中 test.sh 无法覆盖的合规点 | API合规中test.sh能测的（路径/方法/状态码）不纳入；test.sh测不了的（多余端点、错误码message精确匹配、鉴权策略选择）纳入 | "是否恰好实现9个端点（无多余端点）"、"错误码message是否与spec报错表原文一致" |
| **E（质量维度过程）** | Phase 1.11 QUALITY_CONTEXT（如存在） | 快速题 QR专有：模型在声明质量维度上是否自发做到（未被告知）、自述机制与代码现实是否一致、是"一开始就对"还是"补丁式修复"。即使test.sh全通过，来源E也能区分"正确但巧合"和"正确因为理解" | "模型在R1是否使用了实时JOIN而非物化ACL"、"模型在R2是否自主意识到成员变动影响共享权限"、"模型在实现前是否主动讨论过朴素 vs 正确两种方案" |
| **F（MANDATORY）** | **test.sh 测试要点**（Phase 3.2 生成的 test.sh 断言场景，源自 Phase 1.9 素材清单/出题人注测试清单素材） | 从 test.sh 覆盖的测试要点中选取 **25%-50%** 写成 rubric 条目（如"并发注册同一新序列号是否恰 1 个 201 其余 409"、"重复流水号上报是否恰入库一条"）。**不得因"test.sh 已断言"而省略，也不得全部照搬**；优先选与质量维度/核心不变量相关的场景；同一测试要点只写一条 | "并发 10 玩家抢 daily_limit=3 → 恰 3 个 201 + 7 个 409"、"同玩家连续两次 claim → 第二次 409 且 message 区分" |

**划分粒度（拆分/合并规则，MANDATORY）：**

- 划分依据是考察点所属维度（判断证据/可观察能力面是否不同）：需要**不同证据**（不同代码位置/不同断言/不同行为面）才能回答 → 拆成 2 条；能用**同一处代码、同一条断言**回答 → 合并为 1 条（"且"连接）
- 关系紧密的考察点完全可以不拆。例：机制类条目（"R1 限量是否原子实现"——证据是 SQL/事务代码）与行为类条目（"并发 10 抢 3 是否恰 3+7"——证据是黑盒计数）证据不同 → 拆 2 条；同属一个场景的多条行为断言（如"批量限量不超发"与"批量跳过已领成员"）→ 可合并
- 自测：去掉某考察点后另一条是否仍能覆盖？能 → 重复，合并或删重

**区分度检查（替代剪枝——不是事后删条目，而是每条产出时对照材料验证，MANDATORY）：**

产出**每一条** rubric 时，结合输入材料完成两项检查：

1. **区分度真实性检查**：对照 spec 多轮脚本契约、出题人注终态契约、Phase 1.9 素材清单、历史实测记录——该条描述的区分点是否**真实存在于材料中**（弱模型可能做不到 / 强模型可能做到 / 至少模型间可能不同）？材料支持 → 保留；**纯凭印象、无任何材料支撑的假设性区分点 → 修改 criterion 直到有材料支撑，仍无支撑才允许删除**（这是唯一允许的删除情形，发生在产出时；产出后的最终 rubrics 集不做删除——不剪枝）
2. **字段值真实性检查**：每个字段值对照材料得出，不得拍脑袋——`round`（该条在哪轮可观察，对照该轮契约/测试要求；跨轮填 All）、`priority`（对照 MoSCoW 映射或重要性）、`dimension`（对照实际考察能力面）、`necessity`（Explicit=prompt 明示 / Implicit=prompt 隐含或业界默认）、`type`（Objective=可进黑盒断言——对照 test.sh 能否验证 / Subjective=仅人评）
3. **来源标注**：每条 criterion 末尾括注材料来源（如"（素材 #9）"、"（终态契约规则 1）"、"（R2 prompt 结算段落）"）——供 Phase 5.5 打分与报告核验真实性
4. **依据边界（质检员视角，MANDATORY）**：criterion 中出现的**具体名称**（表名/字段名/参数名/接口路径/具体值/机制名）必须以「多轮脚本」（instruction.md：各轮 prompt 原文 + 出题人注契约）为出处——**质检员核验时不读模型源码**，仅凭多轮脚本 + 黑盒测试结果即可定位每条 rubric 的依据。规则：
   - 从多轮脚本能找到出处的具体名（如 R2 prompt 明确表名 guild_members、R1 prompt 明确奖励 id daily-login-gold、出题人注 DATA_MODEL/API 契约明确的字段名）→ 可写，来源标注指向该位置（"R1 prompt 规则 2"、"出题人注测试清单素材 #3"）
   - **模型输出代码/对话只是辅助梳理考察点的素材，不是 rubric 的依据**——从代码中发现的考察点必须回落到多轮脚本找依据，找不到就改写为 prompt 语义描述（如"同一玩家同一天对同一奖励只能领一次"而非"claims 表 UNIQUE 约束"）；语义也无法落回多轮脚本的条目 → 删除（无材料支撑，允许的删除情形）
   - 出题人注 DATA_MODEL 为"建议契约"时：criterion 优先用 prompt 语义描述；写建议名（如 UNIQUE(reward_id, player_id, claim_date)）时标注"语义等价"
   - 单测写法/难点坑点/人评焦点中的名称（如 WAL 建议、IntegrityError、INSERT..SELECT..WHERE 形态）若不出现在多轮脚本中，不得作为 criterion 具体名称的出处——机制描述改写为行为/语义描述，考察点用黑盒可核验的结果表达

**不剪枝（MANDATORY）**：Rubric.json 产出后**不做条目删除**——不因"所有模型都会做到"或"test.sh 已覆盖"而删掉任何条目。条目数预期**几十条**（来源 A-E 条目 + test.sh 要点条目 25%-50%），数量是覆盖要求的正常结果，不是缺陷；Phase 5.5 打分后同样不做剪枝（见 5.6 区分度检查）。全模型同分条目如实保留（反映共性能力），报告中照实呈现。

#### 每轮覆盖要求（MANDATORY）

**Rubric.json 必须覆盖每一轮和全局维度，不可出现某轮无 rubric 的缺口。**

规则：

1. **每轮至少一条**：spec 多轮脚本有 N 轮（R0-R(N-1)），则 Rubric.json 中必须存在至少一条 `"round": "Rx"` 的条目对应每一轮。不要求每轮条目等量，但任何一轮都不能为空。
2. **All 至少一条**：至少存在一条 `"round": "All"` 的条目（大写 A），覆盖跨轮/全局维度的评估点。
3. **一条 rubric 一个 round**：每条 rubric 的 `round` 字段只填一个值（`"R0"` / `"R1"` / ... / `"RN"` / `"All"`）。

**生成后自检（逐项强制）**：
1. 逐轮统计 rubric 条数，若某轮为 0 条，必须从来源 A-F 中为该轮补充至少一条 rubric
2. 若 `"All"` 为 0 条，必须补充至少一条跨轮 rubric
3. **每条 rubric 都必须有 `round` 字段**——缺 round 即违规（这是既往真实产物最常见的错误：11/14 文件缺 round）
4. 逐条核对字段值域：`dimension` 必须是五个英文值之一（含 **Architecture Quality**）；`priority` 必须为 Must have/Nice to have；`necessity` 为 Explicit/Implicit；`type` 为 Objective/Subjective
5. 逐条核对字段值与 criterion 语义匹配（如"是否使用 ArrayDeque"→ dimension 应为 Task Efficiency 或 Architecture Quality 而非 Feature Delivery）

**示例**（4 轮题）：

```json
[
    {"criterion": "R0初始交付...", "round": "R0", ...},
    {"criterion": "R1...", "round": "R1", ...},
    {"criterion": "R2...", "round": "R2", ...},
    {"criterion": "R3全量重写...", "round": "R3", ...},
    {"criterion": "stale判定...", "round": "All", ...}
]
```

#### JSON 格式

```json
[
    {
        "criterion": "模型是否正确地使用了Python 3.11，而非系统自带Python？",
        "round": "R0",
        "priority": "Must have",
        "dimension": "Instruction Following",
        "necessity": "Explicit",
        "type": "Objective"
    }
]
```

#### 输出

写入 `$OUTPUT_DIR/Rubric.json`。条目数量由 spec 复杂度决定，预期**几十条**（来源 A-E 条目 + test.sh 要点条目 25%-50%），**不剪枝**。

**重要**：Phase 3.3 生成的 Rubric.json 是**初始种子集**——此时主会话尚未看过任何模型源码或对话记录，无法知道哪些维度真正区分这批模型。种子集覆盖可预测的区分点（来源 A 人评重点 + 来源 C 过程行为预期 + 来源 F test.sh 要点）。完整集在 **Phase 5.7（Rubric 补遗）** 追加补遗条目后确定；**全程不剪枝**（种子 + 补遗 + test.sh 要点全部保留）。

## Phase 4: Docker E2E Test

> **已合并入 Phase 2：每模型子智能体在执行 solve 脚本时自动完成 Docker 构建、启动、测试和清理。** 此处保留 section 编号以维护 Phase 编号连续性，原 4.1-4.4 内容不再适用。

## Phase 5: Aggregate Report

### 5.1 收集子智能体回报

每个 Phase 2 子智能体返回的结构化回报包含：
- 源码清单（已读文件完整列表）
- 构建结果（编译 PASS/FAIL + 错误全文）
- 黑盒测试结果（通过/失败/断言总数 + 失败详情表）
- 代码审查发现（API/数据模型/状态机/错误码/鉴权/不变量逐项检查）
- 五维度预打分 + 说明
- 发现的模型 Bug 列表（含文件路径和行号）

**验证规则**：如果子智能体回报中「源码清单」缺少模型目录下任何非 target/node_modules/.git 源文件，该回报标记为不完整，对应的模型 section 中注明「子智能体未完成全量源码审查」。

### 5.2 生成反馈报告

报告结构与原模板相同。生成时：

1. 每个模型 section 的「正面亮点」「负面缺陷」标签和额外说明基于子智能体回报（测试结果、代码审查发现、五维度预打分）撰写
2. 「风格指纹」标签和额外说明基于子智能体的代码审查发现 + Phase 7 对话分析（如执行）撰写，不可填"无"
3. 「总体评价」由主会话基于子智能体的代码审查发现和测试结果撰写
4. 「五维度打分」使用子智能体预打分，主会话根据跨模型一致性做 ±1 调整
5. 额外说明中应融入五维度评分和 Rubric 的关键发现，以及三者（标签/五维度/Rubric）无法覆盖的模型特性
6. 横向对比表使用子智能体回报的测试通过/失败数据
7. 「关键发现」由主会话做跨模型对比后撰写
8. **逐轮 Session ID**：从会话记录文件（$SESSION_FILE）或对话文件中提取，填入每轮；无法获取时如实标注"对话中未发现 Session ID 字段"，**禁止使用"[待填写]"占位**
9. **内容可验证性**：模板中"会话信息（traceId）"不可为占位；所有数字（测试成绩/得分/Rubric 条目数）必须与产物文件一致（见"反馈报告写作逻辑"章节）

### 反馈报告写作逻辑（核心原则：每个字都要经得起验证）

反馈报告是**证据链的载体**——读报告的人应能拿着报告去源码/测试/对话里复现每一个判断。每句话必须属于以下三类之一：

| 类别 | 定义 | 写法要求 |
|------|------|---------|
| **事实陈述** | 可复现的证据（测试输出/源码 file:line/对话原文） | 必须标注证据位置 |
| **证据支撑的判断** | 基于事实的推理（如"publish 400 → 参数校验后存在未分类异常"） | 推理链可复现 |
| **有界定的推断** | 无法直接验证的推测（如"最可能的原因是…"） | **必须标注"推断"**及依据 |

**禁止**：无证据的主张（"总体表现优秀"而无证据）、编造细节（不存在的行号/未核对过的数字/对话中没说过的话）、模板填充（照搬句式、换词不换意、复制其他模型的段落）、未核对的引用（引对话原文必须先回原文核对逐字一致）。

**五维打分：先列证据，再定分数。** 每个维度的说明 = 证据（测试/代码/对话引用）+ 判定（对照 0-4 分标准哪一档、为什么）+ 反证（有无相反证据）。自洽铁律：4 分却列不出亮点证据 = 违规；1 分却没有缺陷证据 = 违规。

**数据一致性铁律**：报告中的所有数字必须与产物文件逐一对齐——黑盒测试成绩 ↔ DOCKER_RUN_GUIDE 汇总表；Rubric 汇总条目数与条目内容 ↔ Rubric.json + Rubric打分结果/*.json；五维度总分 ↔ 各维度分数求和；引用的代码行号/对话行号 ↔ 实际文件。**不一致先修正再交付，不允许带矛盾发布。**

**写任何一段内容前，按此顺序思考：**
1. 这段要传达什么结论？（一句话说清）
2. 支撑结论的证据是什么？（测试/源码/对话，具体到位置）
3. 证据是否已亲自核对？（行号数字是否与实际文件一致）
4. 结论是否超出证据？（超出 → 标注推断；无证据 → 删除该段）
5. 这段与报告其他部分是否矛盾？（分数、成绩、条目数）

**QR 视角**：快速题 QR 报告**质量维度结果**：模型在声明质量维度上的表现——是否**自发**做到（未被告知的情况下）、是否"以为做到但实际没做到"（R-final 自述机制 vs 代码现实）。常规题无质量维度声明则省略该节。

### 反馈报告模板 (Report Template)

Write `$OUTPUT_DIR/反馈报告.md` with this structure.

**报告定位**：对标签、五维度评分和 Rubric 的深度说明，以及三者无法覆盖的模型特性。

```markdown
# [Project Name] 多轮后端评测题反馈报告

> 评测日期：{today} | 评测人：{evaluator}
> **交付三件套（SOP V1.9 硬性要求；缺一不可）**：① 主观排序（见横向对比总结）；② traceId（各模型会话信息 JSON，从 CodeBuddy 对话导出复制）；③ Session ID（逐轮填入）。如果用户提供当前 SOP，则同时核对其“交付口径”和 traceId 获取规则；未提供时使用本 Skill 的内置约束且不得猜测标识符。
>
> 该 SOP 是外部可选读取依赖：路径不可用时仍执行本 Skill 内嵌的三件套规则，不因缺少 SOP 文件跳过、猜测或伪造 traceId / Session ID。

---

## 模型 [X]   ← 为每个检测到的模型重复此 section

### 基本信息

- **模型名称**：模型 [X]
- **技术栈**：{从 spec 提取}
- **用户风格**：{从 spec/对话提取}
- **会话信息（traceId）**：{"traceId":"...","conversationRequestId":"...","conversationId":"..."}（从 CodeBuddy 对话导出复制，**不可为占位**）

### 正面亮点

#### 标签

{从标签列表中选择或自定义，同一行内列举，空格隔开不同标签}

#### 额外说明

{简短解释标签、五维度评分和 Rubric 中的亮点，并说明前面三者未能说明的模型亮点。可以自定义标签。如果没有额外说明，填"无"，但保留"#### 标签"和"#### 额外说明"的 tag}

### 负面缺陷

#### 标签

{从标签列表中选择或自定义，同一行内列举，空格隔开不同标签}

#### 额外说明

{简短解释标签、五维度评分和 Rubric 中的缺陷，并说明前面三者未能说明的模型不足。可以自定义标签。如果没有额外说明，填"无"，但保留"#### 标签"和"#### 额外说明"的 tag}

### 风格指纹

#### 标签

{从标签列表中选择或自定义，同一行内列举，空格隔开不同标签}

#### 额外说明

{简短说明给出相应标签的理由，并反馈该模型的总体风格，可以自定义标签。可以与其他模型比较。**本栏不可填"无"**}

### 质量维度结果（如 QUALITY_CONTEXT 存在）

> 快速题 QR 专用：报告模型在出题声明的质量维度上的实际表现。常规题无质量维度声明则省略本 section；不要生成旧模式分析。

- **质量维度**：{从 QUALITY_CONTEXT 提取，如 幂等 / 并发安全 / 金额精度}
- **自发做到与否**：{模型是否在未被提示的情况下自发做到——源码证据}
- **自述 vs 现实**：{模型 R-final 自述的保证机制 vs 代码实际实现——是否"以为做到但实际没做到"}
- **关键证据**：{源码位置 + 对话分析中的决策过程引用}
- **质量评价**：{达标 / 部分达标 / 未达标 — 一段话总结，必须附证据}

### 总体评价

{2-4 句总结性评价，覆盖整体表现、最突出的优缺点、与 spec 的对齐程度}

**优势**：
- {bullet list}

**不足**：
- {bullet list}

### 五维度打分

| 维度 | 分数 (0-4) | 说明 |
|------|-----------|------|
| 指令与约束遵循 | X | {具体说明——哪里做得好/不好，引用具体 spec 偏差} |
| 功能交付完整性 | X | {具体说明} |
| 任务完成效率 | X | {具体说明} |
| 架构合理性 | X | {具体说明} |
| 上下文理解 | X | {具体说明} |

---

## 模型横向对比总结

### 黑盒测试成绩

| 模型 | 通过 | 失败 | 通过率 | 备注 |
|------|------|------|--------|------|
| [X1] | ...  | ...  | ...    | {关键失败原因一句话} |
| [X2] | ...  | ...  | ...    | ... |

### 五维度对比

| 维度 | 模型 [X1] | 模型 [X2] |
|------|-----------|-----------|
| 指令与约束遵循 | X | X |
| 功能交付完整性 | X | X |
| 任务完成效率 | X | X |
| 架构合理性 | X | X |
| 上下文理解 | X | X |
| **总分** | **X/15** | **X/15** |

### 主观排序（SOP V1.9 交付要求，sop_last20260810.md 第 1 节"交付口径"）

{反映评测人对模型能力的主观印象，格式 A>B>C，可含等号如 C>B=A}

- 排序：{A>B>C}
- 理由（一句话）：{基于五维度与 Rubric 的关键差异}

### 关键发现

1. **{发现 1}**：{具体对比——哪个模型在哪方面更好，为什么}
2. **{发现 2}**：...
3. {共同优点}
4. {共同盲点}

---

## 附录 A：五维度打分标准

| 评估维度 | 0分（完全不可用） | 1分（非常残缺） | 2分（明显瑕疵） | 3分（无问题） | 4分（无问题且有亮点） |
|----------|------------------|----------------|----------------|--------------|---------------------|
| **D1 指令与约束遵循** | 完全无视核心指令或硬性约束，对模糊需求装作清楚并按错误假设硬干，结果与真实意图严重偏离。 | 仅遵循极少部分宏观指令，丢失大量关键约束；即使意识到可能没理解，也缺少有效澄清，需大规模重构。 | 核心约束基本执行到位，但次要规则有明显违背；能识别少量模糊点但澄清不到位，仍需人工排查修正。 | 严格遵守显性指令和硬性约束；遇到关键模糊点能提出必要澄清或明确合理假设，不越界、不漏项。 | 100%遵守所有约束，在复杂复合指令下展现极强控制力；能主动识别隐性需求、给出权衡建议，并避开潜在冲突点。 |
| **D2 功能交付完整性** | 代码存在严重语法错误，无法编译或运行；前端白屏报错或页面极其错乱；核心功能一项都未实现。 | 勉强能跑，但主干链路断裂/存在阻断性Bug；仅实现极少部分核心需求，界面布局或视觉还原严重失控，完全无法投入使用。 | 主业务跑通，核心功能可用，但存在非关键Bug或缺乏边界条件处理；前端视觉细节粗糙、对齐/比例/还原度有明显偏差，必须人工修补。 | 核心流程完全闭环，运行流畅无明显Bug，边界处理得当；前端排版规整、色彩协调，符合现代UI规范或参考图要求。 | 功能完美运行无Bug，且在鲁棒性、性能优化、扩展性或交互体验上有突出表现；前端设计感强，视觉完成度或参考图还原度超预期。 |
| **D3 任务完成效率** | 沟通灾难级。多次（≥5次）纠正重跑仍无法理解需求，反复犯错，导致放弃或需人工推翻重写。 | 需频繁提示、纠正或补充说明（3-4次）。AI理解碎片化，修东墙漏西墙，效率低下。 | 需少量追问和微调（1-2次）。初次理解有偏差或遗漏隐性约束，经引导能较快拉回正轨。 | 精准理解需求，基本做到一次成型（One-shot），最多只需1次极轻微确认或代码片段小修补。 | 完美One-shot交付。一次性听懂所有显隐性需求，甚至能举一反三自动补齐未考虑到但必要的逻辑分支。 |
| **D4 架构合理性** | 完全没有架构思维。所有逻辑塞进单文件/单函数，无任何分层；业务、数据访问、配置混在一起；命名混乱、强耦合，二次维护需推翻重写。 | 勉强分文件但边界混乱。controller/service/dao概念缺失或错位，业务逻辑写在路由里；公共代码反复复制；接口与数据契约不一致，新增同类需求改动面极大。 | 基本分层存在但不彻底。能看出routes/handlers/db的划分，但部分职责越界（如DB访问混在handler里）；命名不统一；新增同类需求需修改2-3处。 | 分层清晰、职责单一。controller/service/repo边界明确，接口与实现解耦；数据模型与业务模型分离；新增同类需求只需在对应层添加，老代码基本不动。 | 架构具有前瞻性。在"无问题"基础上主动做了合理抽象（策略/中间件/插件等）支撑后续扩展；错误模型/事务边界/幂等设计清晰；目录与命名让其他工程师5分钟即可上手。 |
| **D5 上下文理解** | 完全没有上下文。每一轮都像第一次对话，前面定的命名/技术栈/风格全部漂移；用户重申过的约束依然失忆。 | 仅记得最近一轮。能接住上一句指令但忘记第1-2轮的约束（命名规范、项目结构等）；多轮后的产物与最初版本完全脱节。 | 上下文部分保留但有遗漏。能记得大方向（技术栈、整体架构），但早前细节（函数命名、数据格式、边界规则）出现遗忘或冲突，需用户提醒。 | 跨轮上下文稳定保持。命名、技术栈、结构、风格在多轮中一致；每轮的修改意图都被准确捕获并落实；不会重复犯之前已修复的错误。 | 主动利用历史上下文做增益。除完整保持约束外，能把早前轮次的偏好、踩坑点主动应用到新一轮（如"上次约定不引入X库，本轮继续遵守"），形成累计的协作记忆。 |

---

## 附录 B：标签列表

### 正面亮点 (Pros)

| 类别 | 可用标签 |
|------|---------|
| 架构与组织 | 模块拆分清晰 命名一致 错误处理完备 配置简洁 编译运行简单 |
| 功能与实现 | 功能完备 测试完备 边界条件处理好 延迟低 幂等处理好 并发处理好 安全处理佳 |
| 指令与约束遵循 | 严格遵循指令 积极自检 理解隐式约束 |
| 任务完成效率 | One-shot通过 交付迅速 质量超预期 |
| 上下文理解 | 细节记忆清楚 目标清晰 重构能力强 |

### 负面缺陷 (Cons)

| 类别 | 可用标签 |
|------|---------|
| 架构与组织 | 过度抽象 抽象不足 过度防御 命名散乱 临时文件散乱 配置混乱 编译运行复杂 |
| 功能与实现 | 核心功能缺失 行为不正确 边界处理缺失 状态不持久 提权漏洞 数据覆盖 死锁 不变量破坏 测试缺失 运行缓慢 默认态不合理 |
| 指令与约束遵循 | 假装实现 实现缩水 实现偏离要求 忽略隐式约束 执行危险命令 违背常识 |
| 任务完成效率 | 交付缓慢 难以debug 主动放弃 |
| 上下文理解 | 遗忘上下文 幻觉 目标漂移 拆东补西 |

### 风格指纹（中性）

| 类别 | 可用标签 |
|------|---------|
| 起手式与流程习惯 | 先列计划 直接动手 |
| 多轮交互习惯 | 偏全量重写 偏patch修改 自检后再交付 等用户喂报错 积极提问 自己拿主意 |
| 项目组织习惯 | 轻量架构 重量级架构 简洁逻辑 积极防御 最小产品 全面交付 |
| 临时文件管理 | 无临时文件 临时文件写入tmp 临时文件写入项目文件夹 |
| 本机环境探查 | 积极探查项目目录上一层 积极探查活跃端口 积极探查可用软件 积极探查测试相关组件 |
| 副作用 | 全局安装Python包 持续占用端口 持久化修改环境变量 安装需求外软件 |
| 拟人风格 | 横冲直撞 小心翼翼 我行我素 |

> 标签可以从上表中选择，也可以自定义。在反馈报告中列举标签时，请在同一行内列举，用空格隔开不同标签。

---

## 测试方法与基础设施说明

### 测试流程
1. 解析 spec 文件提取 API 规范、数据模型、错误码、不变量
2. 为每个模型生成 solve 脚本（env check + host mode + Docker mode）
3. 生成黑盒 test.sh
4. Docker 构建 → 容器运行 → 测试执行 → 清理
5. 收集结果并生成反馈报告
6. 分析模型对话记录（如提供）

### 基础设施
- Docker Desktop (Windows)
- Docker 镜像：{base image} + {runtime packages}
- 应用端口：8080
- {数据库/消息队列，如为 lightweight 模式则写"无外部依赖（H2 内存数据库）"}

### 已识别的非模型问题

- **{问题分类}**：{具体问题和修复方案}
- 始终区分：模型代码问题 vs 基础设施/测试脚本问题
```

### 5.3 Scoring Guidelines (D1-D5 五维度评分标准)

每条维度 0-4 分制。评分反映模型的综合表现。

#### D1 指令与约束遵循
评估 AI 是否"听话"，能否理解需求、澄清关键模糊点，并把规则落到实处。

| 分数 | 含义 |
|------|------|
| 0 | 完全无视核心指令或硬性约束，对模糊需求装作清楚并按错误假设硬干，结果与真实意图严重偏离。 |
| 1 | 仅遵循极少部分宏观指令，丢失大量关键约束；即使意识到可能没理解，也缺少有效澄清，需大规模重构。 |
| 2 | 核心约束基本执行到位，但次要规则有明显违背；能识别少量模糊点但澄清不到位，仍需人工排查修正。 |
| 3 | 严格遵守显性指令和硬性约束；遇到关键模糊点能提出必要澄清或明确合理假设，不越界、不漏项。 |
| 4 | 100%遵守所有约束，在复杂复合指令下展现极强控制力；能主动识别隐性需求、给出权衡建议，并避开潜在冲突点。 |

#### D2 功能交付完整性
评估代码是否"真能跑"，体验是否完整，是成品还是半成品。

| 分数 | 含义 |
|------|------|
| 0 | 代码存在严重语法错误，无法编译或运行；前端白屏报错或页面极其错乱；核心功能一项都未实现。 |
| 1 | 勉强能跑，但主干链路断裂/存在阻断性 Bug；仅实现极少部分核心需求，界面布局或视觉还原严重失控，完全无法投入使用。 |
| 2 | 主业务跑通，核心功能可用，但存在非关键 Bug 或缺乏边界条件处理；前端视觉细节粗糙、对齐/比例/还原度有明显偏差，必须人工修补。 |
| 3 | 核心流程完全闭环，运行流畅无明显 Bug，边界处理得当；前端排版规整、色彩协调，符合现代 UI 规范或参考图要求。 |
| 4 | 功能完美运行无 Bug，且在鲁棒性、性能优化、扩展性或交互体验上有突出表现；前端设计感强，视觉完成度或参考图还原度超预期。 |

#### D3 任务完成效率
达成目标所需轮数，评估理解复杂约束的能力。

| 分数 | 含义 |
|------|------|
| 0 | 沟通灾难级。多次（≥5次）纠正重跑仍无法理解需求，反复犯错，导致放弃或需人工推翻重写。 |
| 1 | 需频繁提示、纠正或补充说明（3-4次）。AI 理解碎片化，修东墙漏西墙，效率低下。 |
| 2 | 需少量追问和微调（1-2次）。初次理解有偏差或遗漏隐性约束，经引导能较快拉回正轨。 |
| 3 | 精准理解需求，基本做到一次成型（One-shot），最多只需 1 次极轻微确认或代码片段小修补。 |
| 4 | 完美 One-shot 交付。一次性听懂所有显隐性需求，甚至能举一反三自动补齐未考虑到但必要的逻辑分支。 |

#### D4 架构合理性
评估后端代码的组织、模块边界、数据契约与可扩展性。

| 分数 | 含义 |
|------|------|
| 0 | 完全没有架构思维。所有逻辑塞进单文件/单函数，无任何分层；业务、数据访问、配置混在一起；命名混乱、强耦合，二次维护需推翻重写。 |
| 1 | 勉强分文件但边界混乱。controller/service/dao 概念缺失或错位，业务逻辑写在路由里；公共代码反复复制；接口与数据契约不一致，新增同类需求改动面极大。 |
| 2 | 基本分层存在但不彻底。能看出 routes/handlers/db 的划分，但部分职责越界（如 DB 访问混在 handler 里）；命名不统一；新增同类需求需修改 2-3 处。 |
| 3 | 分层清晰、职责单一。controller/service/repo 边界明确，接口与实现解耦；数据模型与业务模型分离；新增同类需求只需在对应层添加，老代码基本不动。 |
| 4 | 架构具有前瞻性。在"无问题"基础上主动做了合理抽象（策略/中间件/插件等）支撑后续扩展；错误模型/事务边界/幂等设计清晰；目录与命名让其他工程师 5 分钟即可上手。 |

#### D5 上下文理解
评估多轮交互中对历史约束、技术栈、修改意图的保持与利用。

| 分数 | 含义 |
|------|------|
| 0 | 完全没有上下文。每一轮都像第一次对话，前面定的命名/技术栈/风格全部漂移；用户重申过的约束依然失忆。 |
| 1 | 仅记得最近一轮。能接住上一句指令但忘记第 1-2 轮的约束（命名规范、项目结构等）；多轮后的产物与最初版本完全脱节。 |
| 2 | 上下文部分保留但有遗漏。能记得大方向（技术栈、整体架构），但早前细节（函数命名、数据格式、边界规则）出现遗忘或冲突，需用户提醒。 |
| 3 | 跨轮上下文稳定保持。命名、技术栈、结构、风格在多轮中一致；每轮的修改意图都被准确捕获并落实；不会重复犯之前已修复的错误。 |
| 4 | 主动利用历史上下文做增益。除完整保持约束外，能把早前轮次的偏好、踩坑点主动应用到新一轮（如"上次约定不引入 X 库，本轮继续遵守"），形成累计的协作记忆。 |

### 5.4 Important Distinction

Always separate:
- **Model code issues**: bugs, missing features, spec deviations, compilation errors
- **Infrastructure issues**: Docker image config problems, network limitations, test script tool dependencies

### 5.5 Rubric 打分子智能体派发

**触发时机**：Phase 5.4 反馈报告完成后、Phase 7 对话分析开始前。

**目的**：为每个模型生成 Rubric 二值化打分结果（0-1 制），作为五维度打分的细粒度佐证。

#### 5.5.1 准备

确认以下文件已就绪：
- `$OUTPUT_DIR/Rubric.json` — Phase 3.3 生成
- `$OUTPUT_DIR/反馈报告.md` — Phase 5.4 生成
- 各模型源码目录、测试结果、对话文件

#### 5.5.2 并行派发子智能体

为每个检测到的模型，使用 Codex 当前可用的子代理或多代理能力并行派发一个 Rubric 打分任务。Prompt 模板来自下方附录 C，替换以下占位符；没有多代理能力时按模型顺序执行同一协议。

| Placeholder | Value |
|-------------|-------|
| `{MODEL_LABEL}` | 模型标识 (e.g., A, B, actor-graph) |
| `{RUBRIC_JSON_PATH}` | `$OUTPUT_DIR/Rubric.json` |
| `{MODEL_SOURCE_DIR}` | `$MODEL_DIR/{MODEL_LABEL}` |
| `{OUTPUT_DIR}` | 用户提供的输出目录 |
| `{FEEDBACK_REPORT_PATH}` | `$OUTPUT_DIR/反馈报告.md` |
| `{CONV_FILE_PATH}` | `$CONV_DIR/{MODEL_LABEL}模型.md`（如 CONV_DIR 提供） |

**并行派发 = 所有模型同时启动子智能体。** 子智能体互相独立，无共享状态。

#### 5.5.3 子智能体输出

每个子智能体输出 `$OUTPUT_DIR/Rubric打分结果/Rubric_{MODEL_LABEL}打分结果.json`：

```json
[
    {
        "criterion": "模型是否正确地使用了Python 3.11，而非系统自带Python？",
        "round": "R0",
        "priority": "Must have",
        "dimension": "Instruction Following",
        "necessity": "Explicit",
        "type": "Objective",
        "score": 1
    },
    {
        "criterion": "最后一轮，若无额外提醒，模型是否正确处理id计数？",
        "round": "R3",
        "priority": "Must have",
        "dimension": "Feature Delivery",
        "necessity": "Implicit",
        "type": "Objective",
        "score": 0
    }
]
```

**打分依据**：子智能体需综合审阅以下材料后逐条判定：
1. **Rubric.json** — 评分细目定义
2. **模型源码** — 代码实现是否符合细则要求
3. **黑盒测试结果**（来自反馈报告）— 功能是否通过实际验证
4. **对话记录**（如提供）— 多轮交互中的约束保持与修复行为

**打分规则**：0-1 二值化。`"score": 1` = 满足细则（"是"），`"score": 0` = 不满足细则（"否"）。

#### 5.5.4 收集与验证

主会话收集所有 `Rubric_X打分结果.json`，验证：
- 文件数 = 模型数
- 每条 rubric 都有 `score` 字段且值为 0 或 1
- 无遗漏条目

### 5.6 Rubric 区分度检查（替代剪枝——不删条目，只验证真实性）

**触发时机**：Phase 5.5 所有模型打分完成后。

**目的**：**不删除任何条目**。rubric 集的区分度通过每条产出时的"材料对照检查"（Phase 3.3 区分度检查）保证；本节在打分完成后做最终真实性核验——对照输入材料确认每条得分的判定依据真实存在，防止打分偏差。全模型同分的条目（全 1 或全 0）**保留**——它们如实反映共性能力（全 1 = 该题所有模型达标，全 0 = 该题所有模型未达标），是覆盖要求（来源 A-E + test.sh 要点 25%-50%）的正常结果，不是缺陷。

**检查规则**：

1. 读取所有 `Rubric_X打分结果.json`，构建得分矩阵（行=rubric条目, 列=模型）
2. 对每条 rubric，检查得分是否全同（全 1 或全 0）——全同条目**保留**，在报告中备注"全模型同分（共性能力，未剪枝）"
3. **真实性抽查**：对得分异常或全同的条目，对照材料（模型源码/黑盒测试结果/对话记录）复核打分依据——发现打分与材料不符 → 修正该条 score（**只改分，不删条目**）
4. 文件保持原样（`Rubric.json` 和所有 `Rubric_X打分结果.json` 条目一致，不做删除）

**检查后验证**：
- 更新 `反馈报告.md` 中的 Rubric 得分汇总，反映**完整**条目数和得分（含全同条目）
- 全同条目在汇总表中照实列出，备注"全模型同分（共性能力，未剪枝）"
- 条目数量不设上限——几十条是覆盖要求（来源 A-E + test.sh 要点 25%-50%）的正常结果

## Phase 6: Manual Test Guide

### 6.1 Generate DOCKER_RUN_GUIDE.md

After all models are tested, write `$OUTPUT_DIR/DOCKER_RUN_GUIDE.md`. This is a standalone manual that allows anyone to reproduce the Docker-based E2E tests without the eval pipeline.

**Path handling for Windows (MANDATORY):**

When generating the guide, follow these rules for all host-side path references:
- **容器内部路径**（如 `/tmp/test.sh`, `/workspace`, `/tmp/app.log`）：保持 POSIX 格式，这些在容器内部执行
- **宿主机手动步骤中的临时目录**：使用项目本地路径（如 `$PWD/.docker_build_A`），**禁止使用** `/tmp/...`，因为 Docker Desktop for Windows 无法访问 Git Bash 的 `/tmp`
- **所有 `docker build` / `docker cp` 命令**：需要 `MSYS_NO_PATHCONV=1` 前缀
- **在指南中显式提示**：Windows Git Bash 用户必须将 `/tmp/` 路径替换为 `$PWD/.docker_build_X` 或 Windows 绝对路径

**Required sections:**

```markdown
# [Project Name] 多轮评测 — Docker 手动测试指南

> 生成时间：{date} | 工具：multi-turn-eval + Docker E2E

## 前置条件
- Docker Desktop 运行中
- 当前目录：{OUTPUT_DIR}
- Windows Git Bash: 所有 docker build/docker cp 命令需要 `MSYS_NO_PATHCONV=1` 前缀
- Windows Git Bash: 手动步骤的临时目录使用 `$PWD/.docker_build_X`（非 `/tmp/`——Docker Desktop 无法访问 Git Bash 的 `/tmp`）

## 目录结构 (tree view)

## 架构 (ASCII diagram: solve_X.sh --docker → build image with source baked in → container with PG+Redpanda → exec build+start → test from host → cleanup)

## 快速开始
### 1. 运行模型
#### 模型 [X]: `bash solution/solve_X.sh --docker`  ← 为每个模型重复（脚本内含 build+test+cleanup）
### 2. 查看结果 (test output + 反馈报告.md)

## 测试结果汇总
| 测试项 | 模型 [X1] | 模型 [X2] | ... |
(12-row table with pass/fail per test item + total row, one column per model)

## 已知问题
(Bullet list of model code bugs found, NOT infrastructure issues)

## 环境说明
| 组件 | 版本 | 端口 |
(Docker image components)

## 修改记录
| 日期 | 修改 |
(Chronological fixes during this eval cycle)
```

**Fill each section with actual values** from the completed test run:
- Docker commands use the real image name, container name, and paths
- Results table filled from actual Phase 5 test data
- Known issues list model-specific bugs (e.g., "模型 A — ApiEnvelope<Void> type mismatch")
- Modification log records every fix made during the eval cycle (infrastructure + solve script fixes)

### 6.2 Health Check Validation Standard

All solve scripts validate that the service is alive, not that it returns a specific JSON shape:

```bash
# ✅ CORRECT: validates HTTP status — accepts 200 (open), 401/403 (auth required), 404 (not found but listening)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/api/health")
if echo "$HTTP_CODE" | grep -qE "^(200|401|403|404)$"; then
    echo "Service ready"
fi

# ✅ CORRECT for Docker mode: use docker exec to bypass Windows port forwarding
HTTP_CODE=$(docker exec <container> curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/api/health")
if echo "$HTTP_CODE" | grep -qE "^(200|401|403|404)$"; then
    echo "Service ready"
fi
```

If models produce different health response formats (e.g., flat `{"status":"ok"}` vs envelope `{"code":0,"data":{"status":"ok"}}`), test.sh T1 uses `assert_contains` rather than exact string match.

## Phase 7: Deep Conversation Analysis

### Trigger

After Phase 5 completes and `反馈报告.md` exists, check if `$CONV_DIR` is provided and contains model conversation files (auto-detect by pattern `*模型.md`, e.g., `A模型.md`, `B模型.md`). If present, enter Phase 7. If absent or `$CONV_DIR` not set, skip Phase 7 — the report from Phase 5 is the final output.

### Input

- `$CONV_DIR/`下各模型对话文件（如 `A模型.md`、`B模型.md` 等） — raw conversation logs between user and each model across all rounds
- `$OUTPUT_DIR/反馈报告.md` — Phase 5 output (test scores, per-round pros/cons, dimension scores, comparison tables)
- Optional: a round-boundary file (e.g., `r2-r7-analysis-plan.md`) with line ranges per model per round

### Output

Same `反馈报告.md`, enriched with deep conversation analysis that feeds into each model's「风格指纹」and「额外说明」sections. The子智能体 produces a per-model analysis summary covering behavioral signatures, cross-model comparisons, and key round-level findings. The main session uses this analysis to refine the 风格指纹 labels and 额外说明 text — ensuring the report captures not just what the model delivered but how it worked across rounds.

### Process

**每模型派一个子智能体**进行对话分析。Prompt 模板来自下方附录 B。

| Placeholder | Value |
|-------------|-------|
| `{MODEL_LABEL}` | 模型标识 |
| `{SPEC_FILE}` | `$SPEC_FILE` 路径 |
| `{CONV_FILE_PATH}` | `$CONV_DIR/{MODEL_LABEL}模型.md` |
| `{OUTPUT_DIR}` | 用户提供的输出目录 |

子智能体读取对话文件并输出结构化的对话分析摘要，主会话将其整合到反馈报告的「风格指纹」和「额外说明」中。

**子智能体互相独立，可并行派发。没有子代理能力时，按模型顺序执行附录 B，并保持每个模型的输入与分析上下文隔离。**

主会话在全部子智能体完成后，做最终检查：
- 每模型的风格指纹是否基于对话分析得出（不可填"无"）
- 正面亮点/负面缺陷的额外说明是否融入了对话分析中的关键发现
- 跨模型对比是否引用了对话分析中的具体差异
- Session ID 是否已记录

### Five Analysis Dimensions

Each round's analysis MUST cover these five dimensions. Dimensions can merge or split across paragraphs as the round's content demands — the requirement is coverage, not exactly five paragraphs.

**1. Self-Diagnosis (问题诊断)**
- What bugs/issues did the model discover on its own?
- How did it discover them? (compile check, runtime test, logical reasoning, user prompt)
- What did it find vs. what did it miss? How thorough was the diagnosis?

**2. Fix Methodology (修复方案)**
- How did it approach the fix? (one-shot rewrite, iterative correction, heavy-plan-then-minimal-change)
- How many files changed? Was the scope appropriate?
- Did it compare alternative approaches? Did it justify its choice?
- Was the fix minimal or over-engineered?

**3. Architectural Decisions (架构决策)**
- What design choices did it make for this round's challenge?
- How do those choices differ from the other models?
- Why the differences? (early choice lock-in, different trade-off analysis, understanding gap)

**4. Blind Spots (盲点与遗漏)**
- What did the model miss? Why?
- Categories: requires external knowledge (e.g., framework version API), requires actual data comparison (e.g., string matching between code and JSON payload), scope too large for single-round verification
- Is there a systematic pattern across rounds? (same model missing same type of issue)

**5. Cross-Model Comparison (跨模型对比)**
- Rank the models on this round's challenge
- Root cause of the ranking (methodology difference, accumulated early-choice effect, scaling behavior)
- Note any role-reversals from previous rounds

### Writing Guidelines

**Purpose — feed into 反馈报告 "风格指纹" and "额外说明":**

Phase 7 conversation analysis now produces structured analysis summaries (see Appendix B output format) rather than inserting per-round subsections into the report. The main session uses these summaries to:

1. Refine each model's **风格指纹** labels and 额外说明 (this is where behavioral patterns are surfaced)
2. Enrich **正面亮点/负面缺陷** 额外说明 with process-level evidence (not just test results)
3. Populate the **跨模型对比** in 横向对比总结 with concrete behavioral differences

**Analysis dimensions to cover (feed into the 5-dimension framework):**

| Dimension | What to analyze | Feeds into |
|-----------|----------------|------------|
| 问题诊断 | What bugs did the model discover on its own? How? What did it miss? | 风格指纹 (自检能力) |
| 修复方案 | How did it fix? One-shot rewrite vs iterative vs targeted edits? | 风格指纹 (修复风格) |
| 架构决策 | Design choices across rounds; how do they differ from other models? | 正面亮点/负面缺陷 |
| 盲点与遗漏 | What did the model miss and why? Systematic patterns? | 负面缺陷 |
| 跨模型对比 | Rank models per round; root cause of differences; role reversals | 横向对比总结 |

**Model behavioral signatures:**

Track and note patterns across rounds. When a model deviates from its established signature, flag it:

| Pattern | Signature | Strengths | Weaknesses |
|---------|-----------|-----------|------------|
| Fast parallel batch | Quick to find and fix bugs in small scope; changes many files in one burst | Efficient when scope is small | Quality collapses when change scope exceeds single-round verification capacity |
| Iterative self-correction | Each round builds correctly on previous; small precise fixes | Minimal R6 work; cumulative correctness | Requires early correct choices; framework-version issues need external prompting |
| Heavy planning, minimal changes | Long reasoning before any code; architecture naturally correct from R0 | Zero compile errors; robust foundations | Analysis-to-change ratio can be extreme; misses issues requiring runtime data comparison |

A model may shift between patterns across rounds or exhibit a hybrid. Note the pattern, don't force-fit. These behavioral signatures directly inform the 风格指纹 labels.

**Additional rules:**

- Lead with the most insightful finding, not chronological narrative
- Cross-model comparisons must reference specific differences (not vague "A did better than B")
- Track each model's behavioral signature across rounds and note when a model behaves against its established pattern
- **User style field**: Extract from spec/conversation tone — e.g., "采购中台/集成负责人口吻；前期口语给方向，R3 假设情境，R6 契约收口"

### Cross-Check: Model Self-Test vs Phase 1.9 Coverage

In the dialogue analysis summary, include a finding comparing the model's self-test coverage against the Phase 1.9 extracted scenarios: did the model's own test.sh cover the same scenarios the pipeline required? This closes the quality loop: spec requirements → pipeline test → model's own verification. If the model wrote self-tests, this comparison feeds into the「正面亮点」额外说明.

### 5.7 Rubric 补遗（Phase 7 之后执行）

**触发时机**：Phase 7 对话分析全部完成后。此时主会话已掌握：
- Phase 2 子智能体的代码审查发现（每模型的 API 合规/数据模型/错误码/鉴权/不变量/Bug 列表）
- Phase 5.4 反馈报告中的五维度打分和横向对比
- Phase 5.5 种子 rubric 打分结果
- Phase 7 深度对话分析（每模型每轮的行为模式、自诊断能力、修复风格）

**目的**：Phase 3.3 的种子 rubric 集是在"没见过模型"的情况下写的，无法捕捉事后才暴露的模型差异。补遗利用已有的全部信息，写出真正区分这批模型的 rubric。

**补遗规则**：

1. **审视差异**：主会话对比 Phase 2 代码审查发现 + Phase 7 对话分析，列出两个模型表现不同的维度。这些差异来自：
   - 代码审查中的对比发现（如"模型 A 多了未定义端点 vs B 恰好 spec 端点"、"A 的鉴权策略 vs B 的鉴权策略"）
   - 对话分析中的过程行为差异（如"A 从 R0 就用了 ArrayDeque vs B 到 R3 才从 LinkedList 替换"、"A 自创种子数据到补充轮次才修正 vs B 从 R0 就用正确数据"）
   - 安全/架构维度中 test.sh 覆盖不到的差异（如"登录失败消息是否泄露用户存在信息"）

2. **写 rubric**：对每个有区分度的维度，写一条可以用 0/1 回答的 rubric。标准同上（可验证/原子化/有区分度/无空泛词）。不设条目数限制，但**不为凑数而写**——每条都必须在当前评估的两模型上有明确的 A≠B 预期。

3. **追加到 Rubric.json**：新条目追加到 `$OUTPUT_DIR/Rubric.json`（保留 Phase 3.3 种子条目）。

4. **重新打分**：为每个模型派发轻量 Rubric 打分子智能体，**仅对新增条目打分**（种子条目的得分已在 Phase 5.5 完成，不需重打）。子智能体 Prompt 同附录 C。

5. **合并结果**：将新增条目的得分追加到各 `Rubric_X打分结果.json`。

6. **执行 Phase 5.6 区分度检查**：对合并后的完整 rubric 集执行区分度真实性核验——**不删除任何条目**（全模型同分条目保留，反映共性能力）；对得分与材料不符的条目修正 score（只改分不删条目）。

**补遗后自检**：补遗完成后，重新统计每轮 rubric 条数。若某轮仍为 0 条，必须为该轮补充至少一条 rubric（即使该轮所有模型表现一致——可为"All"维度或相邻轮共享维度）。

**为什么放在 Phase 7 之后而不是 Phase 5 里面**：

```
Phase 3.3 种子集        → 只能从 spec 推测，不知道模型差异
Phase 5.5 种子打分      → 给种子集打分
Phase 7 对话分析        → 深入理解每模型的 R0-R5 行为模式
Phase 5.7 补遗          → 利用全部信息写出真正区分模型的 rubric
Phase 5.6 区分度检查   → 打分后核验真实性，不删条目（全同保留，只修正与材料不符的 score）
```

**补遗示例**（以 actor-graph 题目为例）：

```
Phase 3.3 种子集（2 条）:
  - "BFS 队列是否使用 ArrayDeque"（来源 A：人评重点）
  - "登录失败是否使用统一消息不泄露信息"（来源 A：人评重点）

Phase 7 后发现：
  - A 自创种子数据 → 补充轮次修正；B 从 R0 就用正确数据
    → 补遗 #3: "模型在 R0 首次交付中是否使用了 spec 定义的种子数据？"
  - A 多了一个 /api/graph/status 端点；B 恰好 9 个
    → 补遗 #4: "模型是否恰好实现了 spec 定义的 9 个端点？"
  - A 查询端点需 JWT 认证（符合 spec）；B 设为 public
    → 补遗 #5: "查询端点是否要求用户认证而非 public？"
  - A 写了 7 个测试文件；B 写了 13 个
    → 补遗 #6: "是否编写了 ≥10 个测试文件？"
  - A 从 R0 就用 ArrayDeque（种子 #1 已覆盖，但加过程维度）→ 种子 #1 保留
  - A 登录泄露 "User not found"(种子 #2 已覆盖) → 种子 #2 保留

补遗后共 6 条 → 区分度检查：6 条全部有材料支撑 → 保留全部 6 条（不剪枝）
```

## Templates Reference

Resolve all bundled templates relative to the directory containing this `SKILL.md`:

**Phase 2-3 (scripts):**
- [templates/solve_template.sh](templates/solve_template.sh) — self-contained solve script skeleton. Dockerfile, entrypoint.sh, and redpanda.yaml content are embedded as conditional heredocs in Part 2. Infrastructure (PG, Redpanda) is controlled by `{{#IF HAS_DB}}`/`{{#IF HAS_MQ}}` blocks.
- [templates/test_template.sh](templates/test_template.sh) — black-box test skeleton with pipefail-safe `json_get`, jq boolean/array guidance, 401-aware service wait, and intentionally short-lived `mktemp` response files

**Phase 5 (report):**
- [templates/results_template.md](templates/results_template.md) — per-model result skeleton using the 0-4 D1-D5 scale

Note: `env_check_template.sh`, `Dockerfile.template`, `entrypoint_template.sh`, `redpanda_template.yaml` were removed — their content is embedded in `solve_template.sh`.

## Historical material

`README.md`, `designs/`, and `examples/` are retained only as migration history. Do not load them as the current behavior contract and do not copy their placeholder values or legacy pruning/trap semantics into new outputs.

## Appendix: 完工自检清单

全部 Phase 完成后，逐项勾选。任一 ❌ 即为未合规，必须修复后再交付。

### Phase 1: Parse Spec

- [ ] 1.10 — `$OUTPUT_DIR/instruction.md` 已生成（仅含 spec 中 `## 多轮脚本` section 内容）

### Phase 2: Per-Model Dispatch

- [ ] Spec 提取结果已整理为子智能体输入文本（含 TECH_STACK/INFRA/API_SPEC/DATA_MODEL/STATE_TRANSITIONS/ERROR_CODES/AUTH/INVARIANTS/PITFALLS/TEST_SCENARIOS/BUILD_CMD/START_CMD）
- [ ] 模型列表已检测（find $MODEL_DIR -maxdepth 1 -mindepth 1 -type d）
- [ ] 有多代理能力时，所有模型任务已并行派发；无多代理能力时，已按同一协议逐模型顺序执行并完整收集结果
- [ ] 所有子智能体回报已收集（含源码清单/构建结果/测试结果/代码审查/预打分/Bug 列表）
- [ ] 子智能体回报中「源码清单」已验证完整性（与源目录 find 结果文件数一致）

### Phase 3: test.sh + Rubric.json

- [ ] Phase 1.9 场景全覆盖（不可跳过任一场景）
- [ ] **test.sh 自带数据构造 setup**：login+建考试+加题+发布+答题+判分按 API 顺序组合，断言用真实 id，无预置数据/固定 id 依赖
- [ ] **test.sh 场景覆盖**：单点 + 多组合场景（窗口×幂等/状态×权限/错误优先级/并发/状态机非法流转）
- [ ] **test.sh 环境重置**：不相关场景组各自独立数据（无 DELETE 时每组新建），失败不级联；组内场景流顺序依赖有显式注释
- [ ] `http()` / `http_auth()` / `http_or_auth()` 沿用模板的 `mktemp` 响应捕获并在每次请求后立即删除；不要把 Windows 宿主手工步骤的 `/tmp` 禁令误用于模板内部临时响应文件
- [ ] set -e 安全：`[ -n ] ||` 而非 `[ -z ] &&`
- [ ] 认证在 http() 内部用 bash 数组（非字符串拼接）
- [ ] json_get 内置 `|| true`
- [ ] jq boolean 比较用小写 `"true"` / `"false"`
- [ ] 服务等待接受 `200/401/403/404`
- [ ] 错误消息断言使用 Phase 1.6 报错表的原文
- [ ] Rubric.json 已生成到 `$OUTPUT_DIR/Rubric.json`（从 Phase 3.3 候选池推导，含 test.sh 要点条目，**未剪枝**）
- [ ] 每条 rubric 包含 `round` 字段（`"Rx"` 或 `"All"`）
- [ ] 每轮至少一条 rubric（逐轮统计，无缺口）；`"All"` 至少一条
- [ ] 每条 rubric 的字段值域合规：`dimension` ∈ {Instruction Following / Feature Delivery / Task Efficiency / **Architecture Quality** / Context Understanding}；`round` ∈ {R0...RN, **All 大写**}；`priority` ∈ {Must have / Nice to have}；`necessity` ∈ {Explicit / Implicit}；`type` ∈ {Objective / Subjective}
- [ ] **test.sh 测试要点已纳入 25%-50%**（来源 F）：从 test.sh 断言场景选取，不得因"test.sh 已断言"而省略、也不得全部照搬；同一测试要点只写一条
- [ ] 每条 rubric 通过自检：可验证 / 原子化 / 有区分度 / 无空泛词 / **划分粒度合规**（按考察点维度拆分合并：不同证据→拆、同证据紧密考察点→合并；自测"去掉某考察点另一条是否仍覆盖"）
- [ ] **区分度检查已完成（替代剪枝）**：每条产出时对照材料（多轮脚本契约/终态契约/Phase 1.9 素材/历史实测）验证区分度真实性 + 字段值真实性；无材料支撑的条目已修改 criterion 或删除（唯一允许的删除情形）；criterion 已标注材料来源
- [ ] Rubric 条目覆盖 6 个来源：A(难以自动化测试的点) + B(无法黑盒验证的难点坑点) + C(过程行为) + D(test.sh无法覆盖的合规点) + E(质量维度过程) + F(test.sh 测试要点 25%-50%)

### Phase 4: Docker E2E Test

> 已合并入 Phase 2 — 每模型子智能体自动完成。无需单独检查。

### Phase 5: 反馈报告.md + Rubric 打分

- [ ] 标题格式：`# [Project] 多轮后端评测题反馈报告`
- [ ] 评测日期 + 评测人
- [ ] 每个模型独立 `## 模型 X` section
- [ ] 每模型含：正面亮点（#### 标签 + #### 额外说明；额外说明可填"无"但保留 tag）
- [ ] 每模型含：负面缺陷（#### 标签 + #### 额外说明；额外说明可填"无"但保留 tag）
- [ ] 每模型含：风格指纹（#### 标签 + #### 额外说明；**本栏不可填"无"**）
- [ ] 每模型含：总体评价（2-4 句总结 + 优势/不足 bullet list）
- [ ] **每模型含：质量维度结果 section**（如 QUALITY_CONTEXT 存在；含质量维度、自发做到与否、自述 vs 现实、关键证据、质量评价）
- [ ] 每模型含：五维度打分（0-4）+ 说明（使用 D1-D5 分维度评分标准）
- [ ] **主观排序**：横向对比总结含 A>B>C 格式的主观排序 + 一句话理由（SOP V1.9 交付要求，sop_last20260810.md 第 1 节）
- [ ] **traceId**：每模型"基本信息"含会话信息（traceId JSON），**不可为占位**
- [ ] **Session ID**：逐轮已填（从会话记录提取），无法获取时如实标注，**禁止 [待填写] 占位**
- [ ] 标签从标准列表中选择或自定义，同一行内用空格隔开
- [ ] 额外说明覆盖：标签解释 + 五维度评分说明 + Rubric 说明 + 三者无法覆盖的模型特性
- [ ] 横向对比总结：黑盒成绩表 + 五维对比表 + 关键发现
- [ ] 附录 A：五维度打分标准（D1-D5 分维度 0-4 制）
- [ ] 附录 B：标签列表（正面亮点 / 负面缺陷 / 风格指纹）
- [ ] 测试方法与基础设施说明
- [ ] 已识别的非模型问题（区分模型问题 vs infra 问题）
- [ ] 修改记录（从本次 eval 开始累积）
- [ ] Rubric 打分子智能体已并行派发（每模型一个）— **仅对种子集打分**
- [ ] 所有 `Rubric_X打分结果.json` 已收集（文件数 = 模型数，每条含 0/1 得分）
- [ ] Phase 5.7 Rubric 补遗已完成：基于 Phase 2 代码审查 + Phase 7 对话分析，补写区分度驱动的 rubric
- [ ] 补遗条目已追加到 Rubric.json + 已重新派发轻量打分 + 得分已合并到打分结果
- [ ] **Rubric 区分度检查已完成（替代剪枝）**：未删除任何条目；全模型同分条目保留（共性能力）并如实呈现；得分与材料不符的条目已修正 score（只改分不删条目）
- [ ] 反馈报告中的 Rubric 得分汇总反映**完整**条目数（含全同条目，备注"全模型同分（共性能力，未剪枝）"）
- [ ] Rubric 打分结果目录结构正确（`Rubric打分结果/` 目录下 N 个 JSON 文件）
- [ ] **Rubric 汇总一致性**：反馈报告中的 Rubric 汇总条目数与内容与 Rubric.json + 各 Rubric_X打分结果.json **逐条核对一致**（既往真实产物多次出现汇总表与文件矛盾）
- [ ] **五维总分自洽**：总分 = 各维度分数求和（0-4 制，满分 20）
- [ ] **写作逻辑自检**：逐段核对——每句话有证据（测试/源码 file:line/对话原文）、无无证据的主张、无模板填充、无占位残留、数字与产物文件一致（见"反馈报告写作逻辑"章节）

### Phase 6: DOCKER_RUN_GUIDE.md

- [ ] 前置条件（含 Windows `/tmp` 警告 + `$PWD/.docker_build_X` 替代方案）
- [ ] 目录结构（tree view）
- [ ] 架构（ASCII diagram：solve → build → container → test → cleanup）
- [ ] 快速开始（含一键命令 + 退出码说明）
- [ ] 测试结果汇总（per-model 逐测试项表）
- [ ] 已知问题（模型代码问题，非 infra 问题）
- [ ] 环境说明（组件/版本/端口表）
- [ ] 修改记录

### Phase 7: 深度分析

- [ ] 对话源文件已直接读取（非依赖 summary/缓存中的轮次数）
- [ ] 所有 `<user_query>` / `user_query` 标记已提取并去重
- [ ] `<previous_user_message>` 标记已识别为历史回放（非新轮次）
- [ ] 每轮与 spec 轮次已完成 semantic match
- [ ] 每模型对话分析摘要已生成（覆盖五维度：问题诊断 / 修复方案 / 架构决策 / 盲点与遗漏 / 跨模型对比）
- [ ] 模型行为特征表已填写
- [ ] 风格指纹标签和额外说明已基于对话分析更新
- [ ] 正面亮点/负面缺陷的额外说明已融入对话分析关键发现
- [ ] session 数矛盾（如 spec 6 轮但模型仅 2 轮）已触发源文件重验证
- [ ] **质量维度察觉分析**（如 QUALITY_CONTEXT 存在）：模型在声明质量维度上的表现——是否自发做到（未被告知）、自述机制 vs 代码现实的差距、是"一开始就对"还是"补丁式修复"

### Phase 1.X: 质量维度上下文提取

- [ ] spec 题目元信息/设计文档中 `质量维度` 字段已读取
- [ ] 如非空：QUALITY_CONTEXT 已组装（质量维度/不达标表现/出色表现/代码证据位置/验证方法/R-final 自述机制）
- [ ] QUALITY_CONTEXT 已注入 Phase 2/3.3/5.2/7 子智能体 prompt

---

## 附录：子智能体 Prompt 模板

### A. 模型源码处理子智能体 (Phase 2+4)

使用此模板为每个模型派发一个子智能体。`{PLACEHOLDERS}` 由主会话填入后发送。

```
你正在执行多轮后端评测流水线的「模型 {MODEL_LABEL}」处理环节。

**你的职责：**
1. 读懂该模型的源码
2. 生成自包含 solve 脚本
3. Docker 构建并运行测试
4. 回报结构化结果

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

按 solve 模板规则填充占位符：
- Part 0 (env check): 从 spec 提取结果填语言/Docker/端口检查
- Part 1 (host mode): 将每份源码嵌入 heredoc。**文件数 ≥ 20 时用 base64-encoded tar.gz。**
- Part 2 (docker mode): 从 spec 提取结果填 Dockerfile / entrypoint 占位符

占位符映射参考 spec 提取结果中的 TECH_STACK / INFRA / API 等字段。

### Step 3: Docker E2E 测试

```bash
cd {OUTPUT_DIR}
MSYS_NO_PATHCONV=1 bash solution/solve_{MODEL_LABEL}.sh --docker
```

脚本内部自动完成：env 检查 → 写 Docker 构件 → docker build → docker run → exec build → exec start → health check → docker cp test.sh → docker exec test.sh → cleanup

**捕获全部输出并保留在回报中。**

### Step 4: 代码审查

对比 spec 提取结果审查该模型实现：
- API 路径/方法是否匹配
- 数据模型是否匹配（表名、字段、类型）
- 状态机转换规则是否正确
- 错误码/message 是否精确匹配（必须用 Phase 1.6 报错表原文）
- 鉴权逻辑是否正确（401 > 404 > 400 优先级）
- 硬性不变量是否满足（逐条 #1-#5）
- **质量维度专项审查**（如 QUALITY_CONTEXT 存在）：
  - 逐维度（幂等/并发安全/金额精度/权限边界等）检查模型实现：是"不达标表现（朴素实现）"还是"出色表现"？
  - 代码证据：找到每个维度的实现位置，判断属于哪种方案（参考 QUALITY_CONTEXT.代码证据位置）
  - R-final 自述机制核对：模型自述的保证机制 vs 代码实际实现——是否"以为做到但实际没做到"？

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
- 失败详情: (逐项列出失败测试名称和实际 vs 期望值)

### 4. 代码审查
- API 合规: (逐 API 端点说明匹配/不匹配)
- 数据模型合规: (逐表/字段说明)
- 状态机合规: (检查迁移规则)
- 错误码合规: (检查 message 精确匹配)
- 鉴权合规: (检查 401/404/400 优先级)
- 硬性不变量: (逐条检查 #1-#5)
- 质量维度专项审查: (如 QUALITY_CONTEXT 存在；说明模型在声明质量维度上是否自发做到、R-final 自述机制与代码现实是否一致、代码证据)

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

### B. 对话分析子智能体 (Phase 7)

```
你正在执行多轮后端评测的「模型 {MODEL_LABEL}」对话分析。

## 输入

1. Spec 文件: {SPEC_FILE} (重点读「多轮脚本」section，提取基线轮次 R0-RN)
2. 模型对话: {CONV_FILE_PATH} (完整 MD 对话记录)
3. 已完成报告: {OUTPUT_DIR}/反馈报告.md (已有该模型的测试结果、五维度打分、正面亮点/负面缺陷/风格指纹初稿)

## 职责

为该模型生成结构化的对话分析摘要，覆盖多轮交互中的行为模式、自诊断能力、修复风格和跨模型差异。分析结果将用于完善反馈报告中的「风格指纹」和「额外说明」。

## 规则

- **直接读取对话源文件**——不要依赖任何 summary 或缓存中的轮次数。
- 提取每一个 user 消息标记作为新轮次（`<user_query>`、`**User**`、`## 第X轮` 等）。
- `<previous_user_message>` 标记是历史回放，不是新轮次。
- 每轮与 spec「多轮脚本」轮次做 semantic match（内容相似度）。
- 未匹配 spec 轮次的实际轮次 → 标记为「补充轮次」。
- 覆盖五维度分析：**问题诊断** / **修复方案** / **架构决策** / **盲点与遗漏** / **跨模型对比**。
- **质量维度察觉（如 QUALITY_CONTEXT 存在）**：模型在逐轮演进中是否自发选择正确方案、后续轮加新功能时旧质量是否保持、R-final 自述机制与代码现实是否一致。是"一开始就对"还是"补丁式修复"？
- 跨模型对比必须引用具体差异（如 "B 的排序用 Collections.sort 确定性更强 vs A 的 min-movieId 比较"）。
- 识别模型行为特征签名（如"快速并行批处理""迭代自纠""重规划轻修改"等）。

## 输出

输出一段结构化的分析摘要（纯文本，不要编辑反馈报告），包含：

1. **模型行为特征签名**：该模型属于哪种多轮交互模式（从 Phase 7 行为特征表中选择或自定义）
2. **每轮关键发现**：逐轮的亮点和缺陷，聚焦过程行为（怎么做的）而非最终结果。快速题 QR需额外标注模型在质量维度上的逐轮行为（首次自发做到的轮次、后续轮旧质量是否保持）
3. **质量维度察觉分析**（如 QUALITY_CONTEXT 存在）：模型在声明质量维度上的表现——是否自发做到（未被告知）、自述机制 vs 代码现实的差距、是"一开始就对"还是"补丁式修复"
4. **风格指纹建议**：推荐 2-4 个风格指纹标签 + 简要理由
5. **跨模型差异**：该模型与其他模型在本轮题上的关键差异点
6. **额外说明素材**：正面亮点/负面缺陷中可补充的具体事例（引对话原文或代码位置）
```

### C. Rubric 打分子智能体 (Phase 5.5)

使用此模板为每个模型派发一个 Rubric 打分子智能体。`{PLACEHOLDERS}` 由主会话填入后发送。

```
你正在执行多轮后端评测的「模型 {MODEL_LABEL}」Rubric 打分。

**你的职责：**
1. 阅读 Rubric.json 中的每一条评分细则
2. 审查模型源码、测试结果、对话记录
3. 逐条判定：满足（score=1）或不满足（score=0）
4. 输出 Rubric_X打分结果.json

---

## 输入材料

### A. Rubric.json

读取 `{RUBRIC_JSON_PATH}` 获取全部评分细则。

### B. 反馈报告

读取 `{FEEDBACK_REPORT_PATH}` 获取该模型的：
- 黑盒测试结果（通过/失败明细）
- 五维度打分及说明
- 代码审查发现
- 逐轮分析（如有）

### C. 模型源码

目录: `{MODEL_SOURCE_DIR}`

### D. 对话记录（如提供）

文件: `{CONV_FILE_PATH}`

---

## 打分规则

1. **逐条判定**：按 Rubric.json 中的顺序，逐条给出 0 或 1 分
2. **证据驱动**：每条判定需基于具体证据（源码位置、测试输出、对话片段），不可凭感觉打分
3. **独立判定**：每条 rubric 独立评估，不因其他条目的结果而受影响
4. **综合审阅**：不只依赖单一材料。例如，某条 rubric 关于"是否实现幂等"，需同时看：
   - 源码中幂等逻辑的实现（如 event_id 去重）
   - 测试结果中 T4 幂等性测试是否通过
   - 对话记录中是否在该问题上反复修正

## 输出

写入 `{OUTPUT_DIR}/Rubric打分结果/Rubric_{MODEL_LABEL}打分结果.json`。

格式：Rubric.json 的每条条目 + `"score": 0` 或 `"score": 1`。

```json
[
    {
        "criterion": "模型是否正确地使用了Python 3.11，而非系统自带Python？",
        "round": "R0",
        "priority": "Must have",
        "dimension": "Instruction Following",
        "necessity": "Explicit",
        "type": "Objective",
        "score": 1
    }
]
```

**注意**：
- 不得新增或删除 Rubric.json 中的条目
- 不得修改原条目的任何字段（含 `round`），只能追加 `"score"` 字段
- 如果某条 rubric 无法判定（缺证据），score 默认为 0 并在输出末尾附上无法判定的条目列表
```

### D. 主会话结果聚合指导 (Phase 5)

主会话完成以下聚合工作：

1. 读取所有子智能体回报，提取：构建状态 / 测试成绩 / 代码审查发现 / 五维度预打分
2. 按 Phase 5.2 模板生成 反馈报告.md，每个模型一个 `## 模型 X 评测` section
3. 填写横向对比表（黑盒成绩表 + 五维度对比表 + 关键发现）
4. 严格区分「模型代码问题」vs「基础设施/测试脚本问题」
5. 填写「已识别的非模型问题」section
6. 填写「测试方法与基础设施说明」section
7. **写作逻辑执行**：按"反馈报告写作逻辑"章节逐段自检——每句话有证据、数字与产物文件一致、无模板填充、无占位残留
8. **QR 视角**：QUALITY_CONTEXT 存在时，只报告"质量维度结果"，不生成旧模式表述
