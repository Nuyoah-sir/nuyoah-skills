# Evidence Bundle v1 最小字段示例

> **这些片段只用于认字段，不是可运行、可校验或可交付的真实证据包。** 示例中的路径、文件摘要、包摘要、请求摘要和 ID 都是虚构值；片段之间也没有提供完整目录、原文件、inventory、引用目标、校验报告、封印文件和 ZIP。实际工作必须从真实题目包取证，并以脚本校验结果为准。

## 1 分：存在直接支持证据

下面是一条 `models/<model_id>/rubric-evidence.jsonl` 记录。它只能说明“静态源码中存在绑定”；如果 rubric 要求真实交互效果，还应增加安全执行的测试或保留相应限制，不能夸大成已验证运行效果。

```json
{"model_id":"model-a","rubric_id":"R1-01","rubric_round":1,"criterion":"点击开始按钮后进入游戏","disposition":"examined","coverage":"complete","suggested_score":1,"confidence":"medium","reason_code":"implemented_static_only","fact_summary":"第 1 轮快照中开始按钮绑定了 startGame 处理器。","evidence":[{"evidence_id":"EV-model-a-R1-01-001","type":"static_line","direction":"support","source_state":"round_end","round":1,"path":"src/game.js","line_start":18,"line_end":18,"file_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_blob_path":"evidence/source-blobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt","excerpt":"startButton.addEventListener('click', startGame);","excerpt_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","fact":"开始按钮绑定到 startGame。"}],"human_check_needed":false,"adjudication_ids":[],"limitations":["未执行浏览器交互，仅验证静态绑定。"]}
```

判断要点：`suggested_score=1` 必须有 `direction=support` 的有效证据；第 1 轮 rubric 只能使用第 1 轮结束时可见的证据，不能引用后续轮次补上的实现。

## 0 分：明确缺失，而不是“没找到所以算 0”

这里使用清单事实说明交付目录中不存在题目明确要求的文件。只有在 inventory 确实完整、目标名称或位置没有歧义时，这种 `confirm_missing` 才足以支持 0 分。

```json
{"model_id":"model-a","rubric_id":"R2-03","rubric_round":2,"criterion":"提交 levels/hard.json 关卡文件","disposition":"examined","coverage":"complete","suggested_score":0,"confidence":"high","reason_code":"missing_implementation","fact_summary":"第 2 轮交付清单中没有 levels/hard.json。","evidence":[{"evidence_id":"EV-model-a-R2-03-001","type":"inventory_fact","direction":"confirm_missing","source_state":"round_end","round":2,"path":"levels/hard.json","metric":"path_exists","value":false,"basis":"第 2 轮完整文件清单按规范化相对路径精确匹配 levels/hard.json。","fact":"第 2 轮交付中不存在要求的关卡文件。"}],"human_check_needed":false,"adjudication_ids":[],"limitations":[]}
```

判断要点：搜索失败、工具报错、文件无法读取或执行环境不足，都不是实现缺失的直接证据；这些情况应使用 `null`。

## null：证据不足，等待补证或人工确认

`null` 是诚实状态，不是漏打分。下面的视觉效果需要人工观察，而远端机器只有源码、没有可用的隔离浏览器，因此不能强行填 0 或 1。

```json
{"model_id":"model-a","rubric_id":"R3-02","rubric_round":3,"criterion":"箱子移动动画自然且无明显跳变","disposition":"examined","coverage":"unsafe_to_test","suggested_score":null,"confidence":"low","reason_code":"subjective_needs_human","fact_summary":"当前材料无法可靠判断动画观感，需要人工运行并观察。","evidence":[],"human_check_needed":true,"adjudication_ids":["ADJ-001"],"limitations":["没有满足六项安全条件的浏览器隔离环境。","动画自然度属于主观体验判断。"]}
```

判断要点：`null` 必须保留具体原因和下一步；不要为了让总分看起来完整而把未知转成 0。

## ADJ：远端只登记歧义，不自行定政策

文件：`review/pending-adjudications.json`。远端输出必须保留 `status=pending` 和 `resolution=null`；最终口径由本机人工裁定，并对所有受影响模型一致应用。

```json
{
  "items": [
    {
      "adjudication_id": "ADJ-001",
      "status": "pending",
      "rubric_ids": ["R3-02"],
      "question": "“动画自然”应仅凭人工观感判断，还是允许稳定帧率指标作为充分证据？",
      "ambiguity": "rubric 没有给出可机械验证的阈值。",
      "evidence_ids": [],
      "applies_to_all_models": true,
      "recommended_policy": "由同一名评审按同一观察步骤逐模型人工确认。",
      "alternative_policy": "若题目作者补充帧率阈值，则按统一阈值执行探针。",
      "impact": "影响 R3-02 的最终 0/1；不影响其他 rubric。",
      "resolution": null
    }
  ]
}
```

## 跳过动态测试：普通 PowerShell 不是沙箱

文件：`models/<model_id>/tests/index.json`。六项安全条件任一不满足，就记录 `skipped`，不要运行模型提供的脚本，也不要伪造 exit code、stdout 或断言结果。

```json
{
  "runs": [
    {
      "run_id": "T-model-a-001",
      "kind": "self_test",
      "argv": ["python", "tests/test_game.py"],
      "status": "skipped",
      "skip_reason": "no_isolation",
      "exit_code": null,
      "duration_ms": null,
      "assertions": null,
      "stdout_path": null,
      "stderr_path": null,
      "safety": {
        "model_code_treated_as_untrusted": true,
        "isolated": false,
        "network_disabled": false,
        "credentials_absent": false,
        "disposable_copy": false,
        "minimal_permissions": false
      }
    }
  ]
}
```

对应证据行应写 `reason_code=unsafe_to_test`、`suggested_score=null`（除非已有独立的充分静态证据能够直接判断该 rubric），并在 limitations 里说明缺哪项安全条件。

## Evidence request：本机只请求缺失的具体证据

文件：`evidence_requests.json`。`model_id`、`rubric_id` 和当前证据 ID 必须来自基包，`allowed_methods` 是远端的硬边界。

```json
{
  "schema_version": "1.0.0",
  "base_package_id": "pkg-example-001",
  "requests": [
    {
      "request_id": "REQ-001",
      "model_id": "model-a",
      "rubric_id": "R1-01",
      "round": 1,
      "need": "补充第 1 轮快照中开始按钮处理器的连续源码行，并绑定到 inventory 中的文件摘要。",
      "why_needed": "当前只有文件存在性，不能证明按钮调用 startGame。",
      "current_evidence_ids": ["EV-model-a-R1-01-OLD"],
      "allowed_methods": ["static_line"],
      "acceptance": "证据包含相对路径、行号、原文、round=1、file_sha256，且摘要与第 1 轮 inventory 一致。"
    }
  ]
}
```

## Evidence delta：只回答请求，不改写基包

增量目录至少包含 `DELTA.json` 和 `responses.jsonl`。摘要必须由真实文件机械计算；下面的重复字符仅为占位符。

`DELTA.json`：

```json
{
  "schema": "vibe-evals-evidence-delta",
  "schema_version": "1.0.0",
  "delta_id": "delta-example-001",
  "base_package_id": "pkg-example-001",
  "base_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "request_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "source_input_digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "request_ids": ["REQ-001"],
  "response_count": 1,
  "new_files": []
}
```

`responses.jsonl` 的一行：

```json
{"request_id":"REQ-001","model_id":"model-a","rubric_id":"R1-01","status":"fulfilled","reason_code":"evidence_collected","new_evidence":[{"evidence_id":"EV-model-a-R1-01-NEW","type":"static_line","direction":"support","source_state":"round_end","round":1,"path":"src/game.js","line_start":18,"line_end":18,"file_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_blob_path":"evidence/source-blobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt","excerpt":"startButton.addEventListener('click', startGame);","excerpt_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","fact":"开始按钮绑定到 startGame。"}],"proposed_update":{"suggested_score":1,"reason_code":"implemented_static_only","coverage":"complete","confidence":"medium","fact_summary":"补证确认第 1 轮已有开始按钮绑定。","limitations":["未执行浏览器交互。"]},"limitations":[]}
```

真实 delta 还必须逐字节复制原请求为 `request-copy.json`，其 SHA-256 等于 `request_sha256`。如果找不到证据，应返回 `status=unresolved`、`new_evidence=[]` 和准确的 reason_code；不得把新证据写进旧 ZIP，也不得顺手回答未列出的 model/rubric。

## Human decisions：接受机器建议、人工实测与裁定

`LOCAL-*` 必须记录操作者、时间、步骤、预期、观察和已验证包版本；`per_model_final_scores` 按模型再按 rubric 两级映射：

```json
{
  "schema_version": "1.0.0",
  "base_package_id": "pkg-example-001",
  "material_gap_resolutions": [{"gap_id":"model:model-a:conversation_summary_missing","decision":"proceed_with_limitation","reason":"原始对话不可取得；不据此评价提问与收敛表现。","decided_by":"human","decider":"评测人稳定代号","decided_at":"2026-09-10T10:00:00+08:00"}],
  "adjudication_resolutions": [{"adjudication_id":"ADJ-001","decided_by":"human","decider":"评测人稳定代号","decided_at":"2026-09-10T10:00:00+08:00","decision_source":"用户确认：按统一人工观察步骤判定。","final_policy":"动画自然度由同一评审按统一步骤实测。","per_model_final_scores":{"model-a":{"R3-02":1}}}],
  "local_evidence": [{"evidence_id":"LOCAL-model-a-R3-02-001","model_id":"model-a","rubric_id":"R3-02","type":"human_note","observer":"评测人稳定代号","observed_at":"2026-09-10T10:00:00+08:00","steps":["打开已校验产物","连续推动箱子三次"],"expected_result":"移动动画连续且无明显跳变","observed_result":"三次移动均连续，无明显跳变","observed_version":"pkg-example-001"}],
  "rubric_scores": {"model-a":{"R1-01":{"score":1,"reason":"接受静态绑定事实。","evidence_ids":["EV-model-a-R1-01-001"],"decided_by":"accepted_machine"},"R2-03":{"score":0,"reason":"人工确认采用完整清单缺失口径。","evidence_ids":["EV-model-a-R2-03-001"],"decided_by":"human"},"R3-02":{"score":1,"reason":"统一步骤实测动画无跳变。","evidence_ids":["LOCAL-model-a-R3-02-001"],"decided_by":"human"}}}
}
```

真实文件必须覆盖基包中的每个模型和每条 rubric；上例为字段教学片段，不代表完整覆盖。

## 使用这些示例时的最后检查

1. 复制的是字段形状和判断原则，不是示例中的事实、ID、路径或摘要。
2. 每个 score 都能追溯到同一模型、同一 rubric、允许轮次内的证据。
3. 动态测试没有完整隔离就跳过；未知保留 `null`。
4. ADJ 在远端保持 pending；delta 只响应 request。
5. 真实交付必须通过 `validate_bundle.py` 或 `validate_delta.py`，并由打包脚本生成封印与校验和。
