# 视觉观测协议（写 VIS-* 之前必读）

## 一次合格的读是什么

一次读＝**一次独立的模型调用**，输入只有：冻结的目标素材、候选渲染件、以及一个明确的观察问题。它必须返回对**具体对象、位置、关系**的观察，而不是只给一个结论。

合格的回答示例（结构，不是可复制的答案）："图中左上角的蓝色线段位于圆形图标下方，长度为圆直径的两倍；按钮区域没有出现方向箭头。"

不合格的回答："看起来没问题" / "符合预期" / "应该通过" —— 这类回答不算观测，不能关闭任何条目。

## 两次读必须真正分开

同一个 model×rubric 需要两次读，并且：

| 要求 | 原因 |
|---|---|
| `read_index` 分别是 1 和 2 | 校验器按索引配对 |
| `invocation_id` 不同 | 两次独立调用 |
| `session_id` 不同 | 两次独立上下文 |
| 问题与 `prompt_sha256` 不同 | 不能是同一条提示的复制 |
| 第二条读的原文不含第一条的结论 | 防止复述式"独立" |
| `independent_context` 为真 | 明确声明两次读互不可见 |
| 两条的媒体集合完全相同 | 比的是同一对素材 |
| `confidence` 都是 high | 低置信不能下结论 |
| `conflict` 为 false，且两条 `verdict` 相同 | 冲突必须升级人工 |

## 记录字段

每条 `VIS-*` 必须带：`observation_id`、`type=machine_vision`、身份信封（outer/base/源摘要/任务）、`model_id`、`rubric_id`、`round`、`criterion_sha256`、`read_index`、`invocation_id`、`session_id`、`independent_context`、`input_media_ids`、`tool`、`model`、`tool_version`、`question` 与 `prompt_sha256`、`raw_response` 与 `raw_response_sha256`、`fact`、`verdict`、`confidence`、`conflict`、`observed_at`。

`prompt_sha256` 必须是问题的真实摘要，`raw_response_sha256` 必须是原始回答的真实摘要；校验器会重算，对不上直接拒收。

## 什么时候不该读

- 条目是主观的或政策歧义的：不读，直接走人工。
- 素材缺失或无法渲染：不读，记为缺口。
- 只有渲染器收据但没有真实输出文件：不读，先修渲染。

## 独立性是"可审计的近似"

两次不同的调用、不同的会话、不同的提示，只能说明这次记录是分开采集的，**不能**从密码学上证明提供方真的跑了两次互不可见的推理。不要把这条写成"已证明独立"。
