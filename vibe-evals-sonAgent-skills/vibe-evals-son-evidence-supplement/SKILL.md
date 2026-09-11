---
name: vibe-evals-son-evidence-supplement
description: Use when the local Vibe Evals review has returned an evidence_requests.json for a previously exported evidence bundle and the source machine must collect only the requested missing or conflicting evidence. Do not use for a first full export or to overwrite a base bundle.
---

# Vibe Evals 远端定向补证

## 这个技能服务的是哪条链路

补证只服务**证据包**链路（`$vibe-evals-son-evidence-export` 产出的 v1 证据 ZIP）。如果题目已经走 `$vibe-evals-son-complete-eval` 的完整评测流程，缺失或冲突的材料要在那套流程内补齐（回到对应阶段），不要用本技能对一个 form-ready 包做补证——它不认识外层包的封印结构。

## 核心原则

补证是“回答本机提出的具体证据问题”，不是重跑整批，也不是借机改原分。基包不可变；每次补证生成带独立 delta_id 的增量 ZIP。

开始前完整阅读 [references/supplement-decisions.md](references/supplement-decisions.md)、[references/evidence-bundle-contract.md](references/evidence-bundle-contract.md)、[references/minimal-examples.md](references/minimal-examples.md) 和 [templates/evidence-request.schema.json](templates/evidence-request.schema.json)。

## 必需输入

- 原题包根路径，仍可在第三方机器只读访问；
- 已安全解压并保留封印文件的基包目录（本技能不直接接收 ZIP）；
- 本机生成的 `evidence_requests.json`；
- 全新 delta 输出目录。

先验证基包，再验证请求：

```powershell
$SkillRoot = Join-Path $env:USERPROFILE '.codex\skills\vibe-evals-son-evidence-supplement'
$BaseBundle = '<已封印基包目录>'
$RequestFile = '<evidence_requests.json绝对路径>'
$SourceRoot = '<原题包根路径>'
$RunRoot = '<全新补证运行目录>'
$DeltaRoot = Join-Path $RunRoot 'delta'
$DeltaZip = Join-Path $RunRoot '<题目>-evidence-delta.zip'
py "$SkillRoot\scripts\validate_bundle.py" "$BaseBundle"
py "$SkillRoot\scripts\validate_evidence_requests.py" "$RequestFile" "$BaseBundle"
py "$SkillRoot\scripts\verify_source.py" "$SourceRoot" "$BaseBundle\source-freeze.json"
py "$SkillRoot\scripts\initialize_delta.py" "$BaseBundle" "$RequestFile" "$DeltaRoot"
```

任一失败都停止。`base_package_id`、模型 ID、rubric ID 或输入摘要不匹配时禁止靠模糊名称猜配。`verify_source.py` 退出码 4 是批次级终止：不初始化、不生成、不交付 delta；固定源版本后重新完整 export。

## 执行边界

- 一次请求只处理其指定模型、rubric 和 `need`；不要扩展为全模型复评。
- 只允许请求列出的取证方法。需要更换方法时返回 `unresolved` 和原因，由本机决定是否发新请求。
- 新证据使用新 ID，保留基包旧证据；不得复用 ID 写不同内容。
- 不修改基包任何文件，不在源题包写文件，不覆盖已有 delta。
- 若原始输入哈希已变化，以退出码 4 停止整批补证；不要把新源码证据补进旧批次，也不要生成伪装成正常响应的 delta。
- 语义口径歧义不能靠补更多代码自动解决。若请求实质是让 Agent 拍板，返回 `requires_human_ruling`。
- 动态测试继续遵守六项隔离门槛。普通 PowerShell、`venv` 或新目录不算隔离；没有已证明的合格沙箱时默认跳过，如实返回 `status=unresolved, reason_code=no_isolation`。

## 输出

请求文件的 schema、base_package_id、模型或 rubric 外键无效时，整次初始化失败，先回本机修正请求。有效请求每个恰好一条 response：`fulfilled`、`partial` 或 `unresolved`。已满足时列出新增 evidence_id；未满足时列出检查过的范围、失败原因和推荐下一步。

delta 目录包含：

```text
DELTA.json
responses.jsonl
models/{model_id}/supplements/{request_id}.json
evidence/...
logs/...
integrity/files.sha256
```

静态补证必须用绑定轮次目录调用：

```powershell
$Receipt = Join-Path $RunRoot 'receipts\REQ-001.json'
py "$SkillRoot\scripts\extract_excerpt.py" '<绑定轮次目录>' '<相对文件路径>' <起始行> <结束行> "$Receipt" --blob-dir "$DeltaRoot\evidence\source-blobs" --bundle-root "$DeltaRoot"
```

把 receipt 的机械字段原样放进 `new_evidence`，再补 evidence_id、direction、fact。若相同 blob 已存在基包，可以引用基包路径而不在 delta 重复携带；否则 delta 必须携带该 blob。不得加入没有任何新增 evidence 引用的额外 blob。

动态补证的索引不得放在泛化的根 `logs/` 下。固定写为 `models/{model_id}/tests/index.json` 或 `models/{model_id}/probes/index.json`；stdout/stderr 和脚本路径从该模型目录起算。每个已执行 run 必须包含：run_id、status、argv 数组、script_path、script_sha256、exit_code、duration_ms、结构化 assertions、stdout_path、stderr_path、source_state、round、source_inventory_digest，以及六项全 true 的 safety。passed/failed 的 assertion 逐条写 rubric_id、expected、actual、passed。跳过的 run 只能留作审计，不能被 evidence 引用支持分数。

完成 responses 后只使用 delta 打包器；不要使用基包 `package_bundle.py`：

```powershell
py "$SkillRoot\scripts\package_delta.py" "$DeltaRoot" "$BaseBundle" "$RequestFile" "$DeltaZip"
```

`request-copy.json` 必须与本机原始 `$RequestFile` 逐字节相同。`DELTA.json` 必须记录 base_package_id、base digest、delta_id、request file digest、request_ids、source input digest 和 response_count。只有 fulfilled 可给 `proposed_update`；partial/unresolved 不得给。任何状态都不能改 `human_check_needed` 或 `adjudication_ids`，这些门禁只能由本机人类闭环。压缩后输出 ZIP 与 SHA-256。

## 完成回复

汇报请求总数、fulfilled/partial/unresolved 数量、跳过的危险或无隔离测试、delta ZIP、SHA-256。若源输入变化，只汇报退出码 4 与变化清单，不输出 delta。不得把 partial 说成已补齐，也不得宣称基包已被更新。
