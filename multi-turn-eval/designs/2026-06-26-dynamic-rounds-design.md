# Dynamic Rounds in Feedback Report

> 2026-06-26 | multi-turn-eval skill

## Problem

Spec file defines N rounds in "多轮脚本", but actual model conversations may exceed N rounds (e.g., spec=7 rounds, model B actually had 12 user messages). The feedback report must reflect ALL actual conversation rounds, not just spec-defined ones.

## Data Sources

| Source | Role |
|--------|------|
| Spec "多轮脚本" | Baseline reference framework — defines expected prompt intent per round |
| Model conversation MD files | Authoritative for actual round content — all real user prompts |
| Session records Excel | Authoritative for Session ID per round |

## Design

1. Extract baseline rounds from spec "多轮脚本" (R1-R7 etc.)
2. Extract all user prompts from model conversation MD files
3. Semantic match: each spec prompt → best-matching actual prompt
4. Unmatched actual prompts → labeled "补充轮次"
5. All rounds ordered by actual occurrence time
6. Session IDs backfilled from Excel records

### Report Structure

```
### 人为补充 Prompt 大意 (from spec, fixed reference)
| 轮次 | Prompt 大意 | 来源 |

### 逐轮结果反馈 (from actual conversations, dynamic)
**第1轮** (spec R1) | Session ID: xxx
[analysis]

**补充轮次** | Session ID: xxx
[analysis — extra round between R2 and R3]

**第2轮** (spec R2) | Session ID: xxx
[analysis]
...
```

### SKILL.md Changes Needed

- Entry section: clarify data source roles
- Phase 5.2: report structure reflects dynamic rounds
- Phase 7 Step 1: add "semantic matching spec rounds to actual rounds"
- Phase 7 Step 2: handle supplement rounds
