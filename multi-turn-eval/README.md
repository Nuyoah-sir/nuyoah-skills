# Multi-Turn Eval

Multi-turn backend evaluation pipeline skill. Parses a multi-round spec, generates solve scripts and test.sh for each model output detected in the input directory, runs them with Docker isolation, and produces a scored feedback report.

## Quickstart

Invoke the Codex skill by asking for a multi-turn backend evaluation, for example:

```
Please evaluate these models:
- Spec: path/to/题目文件/po-line-fulfill.md
- Models: path/to/模型输出/po-line-fulfill
- Output: path/to/做题输出/po-line-fulfill
```

The skill will ask for any missing paths.

## Inputs

| Parameter | Description |
|-----------|-------------|
| `$SPEC_FILE` | Multi-round spec with round-by-round requirements, API definitions, data models, error codes, invariants |
| `$MODEL_DIR` | Parent directory containing model subdirectories (e.g., `A/`, `B/` or any names), each a complete project |
| `$OUTPUT_DIR` | Where all generated artifacts and reports go |

## Outputs

```
$OUTPUT_DIR/
├── 反馈报告.md               # Aggregated comparison report
├── instruction.md            # Archived spec copy
├── DOCKER_RUN_GUIDE.md       # Standalone manual test guide
├── solution/
│   └── solve_<model>.sh      # Self-contained per-model solve scripts (Part 0-2)
└── tests/
    └── test.sh               # Black-box HTTP test suite (auto-generated from spec)
```

## How It Works

1. **Parse Spec** — Extract tech stack, APIs, data model, state transitions, error codes, invariants
2. **Generate** — Per-model solve_<model>.sh (3-part: env check + host mode + Docker mode), test.sh (spec-driven assertions)
3. **Per-Model Test** — One Codex subagent per model builds and tests via `solve_<model>.sh --docker` when multi-agent execution is available; otherwise run the same protocol per model in isolated sequence.
4. **Aggregate** — Comparison table, 5-dimension scores, key findings → feedback report

## Spec Requirements

The spec must contain:
- Round-by-round requirements (R1-R7 or similar)
- Tech stack declaration
- API endpoint definitions with response formats
- Data model (tables + columns)
- State transition rules
- Error code table (code / message / trigger)
- Hard invariants (numbered rules)
- Seed data

## Limitations

- Dynamic model count — auto-detected from subdirectories under $MODEL_DIR
- App port fixed to 8080
- Docker-based infrastructure only
- HTTP black-box testing (not unit tests)
