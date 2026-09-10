# Vibe Evals Evidence Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three installable Codex skills and a validated evidence-bundle v1 toolchain so a third-party machine can collect trustworthy evaluation evidence and the local machine can generate the final V2.1 scoring form without copying the full task package.

**Architecture:** The remote export skill produces an immutable, hash-addressed evidence bundle; the supplement skill produces non-overwriting delta bundles; the local finalize skill verifies, adjudicates, merges, and renders final artifacts. Python standard-library scripts enforce structure and integrity while skill instructions teach context-sensitive evidence judgments.

**Tech Stack:** Codex Agent Skills, Markdown/YAML, Python 3 standard library, JSON/JSONL, SHA-256, ZIP, `unittest`.

---

### Task 1: Record baseline skill failures

**Files:**
- Create: `tests/skill-pressure/baseline-results.md`
- Create: `tests/skill-pressure/scenarios.md`

- [ ] **Step 1: Run three pressure scenarios without the new skills**

Use fresh subagents for urgency, missing-material/no-sandbox, and conflicting-evidence/round-drift scenarios. Record their exact proposed decisions.

- [ ] **Step 2: Classify the failures**

Map each unsafe or ambiguous behavior to one of: fabricated evidence, unsafe execution, missing stop condition, round leakage, silent conflict resolution, unverifiable path, or premature readiness.

- [ ] **Step 3: Save the observed baseline**

Write the scenarios and observed failure patterns to the two files. Expected result: every later skill rule traces to an observed failure or an invariant from the source Vibe Evals skills.

### Task 2: Define schema and failing validator tests

**Files:**
- Create: `shared/schema/evidence-bundle.schema.json`
- Create: `shared/references/evidence-bundle-contract.md`
- Create: `shared/scripts/validate_bundle.py`
- Create: `tests/test_validate_bundle.py`
- Create: `tests/fixtures/minimal-valid/`

- [ ] **Step 1: Write failing tests for the public validator API**

Tests import `validate_bundle.validate_bundle(path)` and assert rejection of absolute paths, `..`, duplicate IDs, missing model-rubric pairs, invalid score values, unresolved evidence references, unsafe executed tests, round leakage, pending adjudication marked ready, and hash mismatch.

- [ ] **Step 2: Run the validator tests and confirm RED**

Run: `py -m unittest tests.test_validate_bundle -v`

Expected: failures because `validate_bundle.py` and schema fixtures do not yet exist.

- [ ] **Step 3: Implement the minimum validator**

Implement structured errors, deterministic status derivation, JSON/JSONL parsing, path normalization, key/reference checks, score/evidence invariants, readiness checks, and SHA-256 verification using only the Python standard library.

- [ ] **Step 4: Run the validator tests and confirm GREEN**

Run: `py -m unittest tests.test_validate_bundle -v`

Expected: all validator tests pass with no warnings from the test runner.

### Task 3: Build deterministic discovery, state, and packaging helpers

**Files:**
- Create: `shared/scripts/discover_task_package.py`
- Create: `shared/scripts/package_bundle.py`
- Create: `shared/scripts/merge_delta.py`
- Create: `tests/test_discover_task_package.py`
- Create: `tests/test_package_bundle.py`
- Create: `tests/test_merge_delta.py`

- [ ] **Step 1: Write failing tests**

Cover rubric filename priority, model ID normalization/collision, missing mandatory inputs, optional warnings, input digests, ZIP path traversal, `.partial` behavior, extraction revalidation, delta non-overwrite, and base package ID matching.

- [ ] **Step 2: Run helper tests and confirm RED**

Run: `py -m unittest tests.test_discover_task_package tests.test_package_bundle tests.test_merge_delta -v`

Expected: failures because helpers are not implemented.

- [ ] **Step 3: Implement helpers**

Use `pathlib`, `hashlib`, `json`, `zipfile`, `tempfile`, and atomic `Path.replace`. Never execute model code. Emit machine-readable errors and stable exit codes 0, 2, 3, and 4.

- [ ] **Step 4: Run helper tests and confirm GREEN**

Run: `py -m unittest tests.test_discover_task_package tests.test_package_bundle tests.test_merge_delta -v`

Expected: all helper tests pass.

### Task 4: Author and forward-test the remote export skill

**Files:**
- Create: `vibe-evals-son-evidence-export/SKILL.md`
- Create: `vibe-evals-son-evidence-export/agents/openai.yaml`
- Create: `vibe-evals-son-evidence-export/references/evidence-decisions.md`
- Create: `vibe-evals-son-evidence-export/references/step-by-step-runbook.md`
- Create: `vibe-evals-son-evidence-export/templates/model-evidence-agent-prompt.md`
- Copy generated runtime resources into `vibe-evals-son-evidence-export/scripts/` and `schemas/`
- Create: `tests/skill-pressure/export-results.md`

- [ ] **Step 1: Use baseline failures as the RED evidence**

Confirm the baseline scenarios exhibit at least one missing or unsafe decision without the skill.

- [ ] **Step 2: Write the skill**

Keep `SKILL.md` as a routing and invariant layer. Put the hand-held runbook and full judgment matrix in references. Require one model per evidence worker and prohibit shared report writes.

- [ ] **Step 3: Validate static skill structure**

Run: `py C:\Users\MR\.codex\skills\.system\skill-creator\scripts\quick_validate.py <skill-path>`

Expected: `Skill is valid!`

- [ ] **Step 4: Run the same pressure scenarios with the skill**

Expected: agents preserve unknowns, stop unsafe tests, separate R1/R2 evidence, create adjudications for conflicts, and refuse readiness when references are incomplete.

### Task 5: Author and forward-test the remote supplement skill

**Files:**
- Create: `vibe-evals-son-evidence-supplement/SKILL.md`
- Create: `vibe-evals-son-evidence-supplement/agents/openai.yaml`
- Create: `vibe-evals-son-evidence-supplement/references/supplement-decisions.md`
- Create: `vibe-evals-son-evidence-supplement/templates/evidence-request.schema.json`
- Copy required runtime resources into `scripts/` and `schemas/`
- Create: `tests/skill-pressure/supplement-results.md`

- [ ] **Step 1: Write a failing forward scenario**

Provide a request with one missing line excerpt, one unsafe dynamic test, and one rubric-policy ambiguity. Expected baseline failure: overwrite or unsupported score fabrication.

- [ ] **Step 2: Write the skill and non-overwrite rules**

Require base package ID/digest matching, request-scoped collection, stable evidence IDs, explicit unresolved responses, and a new delta package.

- [ ] **Step 3: Validate and forward-test**

Run quick validation and repeat the scenario. Expected: only requested evidence is added; base artifacts remain unchanged; unresolved semantic policy returns to the human.

### Task 6: Author and forward-test the local finalize skill

**Files:**
- Create: `vibe-evals-bundle-finalize/SKILL.md`
- Create: `vibe-evals-bundle-finalize/agents/openai.yaml`
- Create: `vibe-evals-bundle-finalize/references/finalization-decisions.md`
- Create: `vibe-evals-bundle-finalize/references/v21-form-rules.md`
- Create: `vibe-evals-bundle-finalize/templates/`
- Copy required runtime resources into `scripts/` and `schemas/`
- Create: `tests/skill-pressure/finalize-results.md`

- [ ] **Step 1: Write failing forward scenarios**

Cover pending adjudication, conflicting evidence, fallback-zero candidate, subjective evidence, N=1, tied overall impression, and incomparable rubric sets.

- [ ] **Step 2: Write the skill**

Require local revalidation, human resolution before finalization, scored rubric fields equal baseline fields plus `score` and `reason`, independent overall-impression scoring before Rank, and all suggestion values marked pending human confirmation.

- [ ] **Step 3: Validate and forward-test**

Expected: incomplete packages are rejected; review-ready packages generate adjudication material only; form-ready packages generate internally consistent final artifacts.

### Task 7: Package, document, and verify end to end

**Files:**
- Create: `安装与操作手册.md`
- Create: `install-third-party.ps1`
- Create: `install-local.ps1`
- Create: `dist/vibe-evals-sonAgent-skills-v1.zip`
- Create: `dist/vibe-evals-sonAgent-skills-v1.zip.sha256`
- Create: `tests/test_end_to_end.py`

- [ ] **Step 1: Write the failing end-to-end test**

The test creates a synthetic multi-round package, runs discovery, materializes valid evidence, packages and revalidates the ZIP, applies a delta, and verifies finalize readiness. It also corrupts one excerpt hash and confirms rejection.

- [ ] **Step 2: Run the end-to-end test and confirm RED**

Run: `py -m unittest tests.test_end_to_end -v`

Expected: failure until installation and packaging integration exists.

- [ ] **Step 3: Implement documentation and packaging**

Write copy-paste prompts for first run, safe restart after interruption, supplement, local prepare, and local finalize. Install scripts copy only the appropriate skill folders and preserve existing installations unless `-Force` is explicitly supplied.

- [ ] **Step 4: Run all verification**

Run: `py -m unittest discover -s tests -v`

Run quick validation for each skill, then unpack the distribution ZIP into a temporary directory and rerun hash and skill validation.

Expected: all tests pass, all three skills report valid, and the distribution checksum matches.

### Task 8: Independent final reviews

**Files:**
- Review all created files without modification during the first pass.

- [ ] **Step 1: Spec compliance review**

Use a fresh reviewer to compare every design requirement with an observable file, test, or documented decision.

- [ ] **Step 2: Code and skill quality review**

Use a second fresh reviewer to inspect security, portability, weak-model clarity, schema consistency, and accidental reliance on third-party absolute paths.

- [ ] **Step 3: Resolve findings and rerun verification**

Apply only supported fixes, rerun affected tests, then rerun the complete verification suite before claiming completion.
