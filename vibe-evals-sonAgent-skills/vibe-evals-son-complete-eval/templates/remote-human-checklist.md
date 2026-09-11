# 人工见证检查清单

> **本文件里的 id、路径、答案、分数全部是虚构示例。** 不得把这里的任何具体内容复制进真实记录。

## 缺任何一项就不允许继续

- [ ] 观察者别名 `observer`（稳定、可追溯，不写"某人"）
- [ ] 记录者别名 `recorded_by`
- [ ] 采集方式 `capture_method`（交互式终端）
- [ ] 观察时间 `observed_at`（RFC3339）
- [ ] 步骤 `steps`（至少一步可复现的动作）
- [ ] 预期 `expected_result`（与条目相关，不是复述条目）
- [ ] 实际 `observed_result`（具体事实，允许与预期不同）
- [ ] 被观察版本 `observed_version`（必须同时写内层包 id 与渲染/媒体 id）
- [ ] 媒体 `media_ids`（目标与候选渲染都写）
- [ ] 原始确认词 `confirmation_text`（真人当场输入的原话，不允许"y"、"确认"这类占位）
- [ ] 结论 `verdict`（0 或 1）
- [ ] 身份信封：`outer_package_id` / `base_package_id` / `base_zip_sha256` / `source_input_digest` / `task_id` / `model_id` / `rubric_id` / `round` / `criterion_sha256`

## 执行方式

真人必须在**交互式终端**里执行 `complete_eval.py record-human --run <run> --record <file>` 并当场输入确认词。管道输入、脚本代填、从模板复制的确认词都会被拒绝（退出码 5）。

## 这些字段能证明什么

它们能证明"一条完整的、可追溯到某个观察者的见证被记录下来了"。它们**不能**从密码学上证明某个人真的存在过。不要把它写成"已证实真人存在"。
