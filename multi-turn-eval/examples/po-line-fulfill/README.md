# PO Line Fulfill — Reference Example

This directory contains actual outputs from evaluating three LLM-generated Spring Boot + PostgreSQL + Kafka projects against the `po-line-fulfill.md` spec.

- **solve_A.sh / solve_B.sh / solve_C.sh**: build-and-run scripts with heredoc-embedded source
- **test.sh**: 60-assertion black-box test suite
- **反馈报告.md**: aggregated report with per-round analysis, 5-dimension scores, comparison table

## Results Summary

| Model | Pass | Fail | Rate |
|-------|------|------|------|
| A | N/A | N/A | 0% (API incompatible) |
| B | 60 | 0 | 100% |
| C | 58 | 2 | 96.7% |
