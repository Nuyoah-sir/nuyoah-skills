# form-ready bundle 契约（本机视角）

远端（`$vibe-evals-son-complete-eval`）负责按字段写这个包；本机只负责**验**和**渲染**。字段级细节以远端技能里的 `references/form-ready-bundle-contract.md` 为准，本文件只讲本机必须知道的约束。

## 本机信任的锚点

1. sidecar 或你手里的期望 SHA-256；
2. 外层 `FORM-READY.json` 里的 `base.sha256` 与内层 ZIP 的实际字节；
3. 外层封印三件套：`READY.json`、`integrity/files.sha256`、`integrity/validation-report.json`。

这三样对不上任何一件，立即停止。

## 本机必须重算的东西

- **总分**：用内层冻结的 rubric 集，不用外层声称的数字。
- **每个模型的得分**：取 `scored/rubrics-<model>.json` 的真实值。
- **三个输出摘要**：自己算 `form_sha256` / `report_sha256` / `heatmap_sha256`，并与收据里的值比较。
- **计数**：模型数、rubric 数、证据条数、四个未闭合计数（必须全 0）。

## 本机不能做的事

不得创建或修改人工裁定清单、`evidence_requests.json`、`human-decisions.json`、`local-human-evidence.jsonl`，不得替换任何分数或图片，不得"顺手修正"远端结论。

## 收据

`verification-receipt.json` 是给下游和审计看的凭证：归档 SHA-256、外层/内层包 id、源摘要、两层校验器版本与结果、重算计数、三个产物摘要、`rendered_at`。它**不含**任何绝对路径。

## 说明一处已知粒度

远端演示槽位对 pros/cons 是"整块共享一份证据"，V2.1 表单要求每条 claim 一组引用；本机渲染时让每条 claim 继承该槽位的证据列表，并在收据里写明 `evidence_granularity.pros_cons = "slot-level"`。不要把这些引用当成逐条证据。
