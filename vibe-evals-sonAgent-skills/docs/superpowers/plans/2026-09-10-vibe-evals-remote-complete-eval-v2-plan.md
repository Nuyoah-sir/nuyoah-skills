# Vibe Evals Remote Complete Evaluation v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a remote-complete Vibe Evals workflow that closes every rubric, visual check, adjudication, and scoring input on the third-party machine, then lets the local machine verify and deterministically render the scoring form without local adjudication.

**Architecture:** Preserve the sealed v1 evidence ZIP byte-for-byte as the immutable inner package. Add a sealed `vibe-evals-form-ready-bundle` v2 outer layer containing content-addressed media, audited machine-vision/remote-human observations, complete binary decisions, portable scored artifacts, and form input. A new remote skill creates the outer package; the existing local finalizer gains a schema-routed one-call verify-and-render path while retaining legacy v1 behavior.

**Tech Stack:** Python 3.10+ standard library, PowerShell 5.1+, JSON/JSONL, JSON Schema 2020-12 as a checked-in interchange specification, custom dependency-free runtime validators as the execution authority, ZIP/SHA-256 integrity, `unittest`, Codex Agent Skills, a byte-level passive-SVG gate, and a locally probed Chromium/Edge renderer that is never represented as an active-code sandbox.

---

## Execution context and invariants

- Canonical editable source: `E:\工作\未来小猎\AI-test-skill\vibe-evals-sonAgent-skills`.
- Git publishing clone: `C:\Users\MR\AppData\Local\Temp\nuyoah-skills-upload-5c2ebe15ba6e4c6a9c714597e584878c`.
- Approved design: `docs/superpowers/specs/2026-09-10-vibe-evals-remote-complete-eval-v2-design.md`.
- Historical design baseline: commit `5bb8a1efd7d6aa1e1ebe431977f7268ca0af69b0`; 78 tests pass and one Windows symlink test is skipped. The implementation baseline is the later Git commit that contains this reviewed plan; resolve and record that exact HEAD at Task 1 Step 0 after plan publication.
- Work in the Git clone, then mechanically synchronize the finished tree back to the canonical source directory. Never edit both copies independently.
- At the beginning of every implementation session set `$RepoRoot` to the publishing clone above, set `$PackageRoot = Join-Path $RepoRoot 'vibe-evals-sonAgent-skills'`, and run Python/tests from `$PackageRoot`. Every Git command uses `git -C $RepoRoot`; every copied path is explicit and hash-compared after synchronization.
- The inner v1 ZIP, its sidecar digest, package ID, and source input digest are immutable inputs to v2.
- The outer validator derives readiness. Agent prose, child-agent reports, file presence, and a manually authored `READY.json` do not establish readiness.
- `FORM-READY.json.package_status` has only `incomplete` and `ready_for_form`; durable construction progress lives separately in `run-state.json.phase`. v2 has no local-review state.

## File map

### Canonical shared modules to create

- `shared/scripts/artifact_integrity.py`: sidecar verification, ZIP safety, checksum manifests, and atomic ZIP writing.
- `shared/scripts/form_ready_context.py`: one loader for inner package, media, observations, evidence registries, and model/rubric identity.
- `shared/scripts/initialize_form_ready.py`: create a new outer workspace from a sealed v1 ZIP without changing inner bytes.
- `shared/scripts/register_media.py`: copy frozen source media and script-produced PNG renders into content-addressed paths.
- `shared/scripts/render_media.py`: prove an SVG is passive, probe a supported Chromium/Edge executable, render only passive inputs, and generate a non-user-authored execution receipt.
- `shared/scripts/record_evidence.py`: accept one worker evidence record or a worker batch and atomically validate it against the v1 evidence workspace before canonical merge.
- `shared/scripts/record_review.py`: atomically accept and validate prompt-requirement classification plus rubric-review candidates required before v1 sealing.
- `shared/scripts/record_observation.py`: atomically record validated machine-vision and interactive remote-human observations.
- `shared/scripts/decision_workspace.py`: generate the exact decision skeleton, atomically record decisions, and deterministically build adjudication/material-gap audit data.
- `shared/scripts/render_supporting_outputs.py`: deterministically generate report, heatmap, and form input from validated decisions and scored rubrics.
- `shared/scripts/presentation_workspace.py`: generate and validate every evidence-backed semantic input slot required by the V2.1 form.
- `shared/scripts/complete_eval.py`: own the durable remote phase state machine and resume/stop rules.
- `shared/scripts/validate_final_decisions.py`: enforce observation provenance, four decision actors, exact coverage, and closure rules.
- `shared/scripts/project_form_ready_scores.py`: generate and replay portable scored rubrics and summaries.
- `shared/scripts/validate_form_ready.py`: deep outer validator and the sole authority for v2 readiness.
- `shared/scripts/package_form_ready.py`: seal, round-trip extract, strictly revalidate, and atomically publish v2 ZIP plus sidecar.
- `shared/scripts/verify_and_render_form_ready.py`: local one-call verification and deterministic form rendering.

### Schemas and references to create

- `shared/schema/form-ready-bundle.schema.json`
- `shared/schema/media-index.schema.json`
- `shared/schema/observation-record.schema.json`
- `shared/schema/final-decisions.schema.json`
- `shared/schema/criterion-classification.schema.json`
- `shared/schema/adjudication-audit.schema.json`
- `shared/schema/source-verification.schema.json`
- `shared/schema/form-input.schema.json`
- `shared/schema/presentation-input.schema.json`
- `shared/schema/presentation-attestation.schema.json`
- `shared/schema/verification-receipt.schema.json`
- `shared/references/gap-policy.json`
- `shared/references/form-ready-bundle-contract.md`

### Existing shared modules to modify

- `shared/scripts/package_bundle.py`
- `shared/scripts/package_delta.py`
- `shared/scripts/extract_artifact.py`
- `shared/scripts/render_v21_form.py`

The first three become compatibility adapters over `artifact_integrity.py`. `render_v21_form.py` exposes one shared build core while retaining its legacy v1 CLI.

### New skill

- `vibe-evals-son-complete-eval/SKILL.md`
- `vibe-evals-son-complete-eval/agents/openai.yaml`
- `vibe-evals-son-complete-eval/references/complete-eval-runbook.md`
- `vibe-evals-son-complete-eval/references/observation-decisions.md`
- `vibe-evals-son-complete-eval/references/visual-observation-protocol.md`
- `vibe-evals-son-complete-eval/references/form-ready-bundle-contract.md`
- `vibe-evals-son-complete-eval/templates/media-plan.example.json`
- `vibe-evals-son-complete-eval/templates/machine-vision-prompt.md`
- `vibe-evals-son-complete-eval/templates/remote-human-checklist.md`
- `vibe-evals-son-complete-eval/templates/final-decisions.example.json`
- v1 evidence references, V2.1 rules, form-input files, report/heatmap templates, schemas, and required script mirrors.

### Existing skills and distribution to modify

- `vibe-evals-son-evidence-export/SKILL.md` and its routing metadata/references.
- `vibe-evals-son-evidence-supplement/SKILL.md` and its routing metadata/references.
- `vibe-evals-bundle-finalize/SKILL.md`, metadata, runbooks, schemas, and v2 script mirrors.
- `runtime-manifest.sha256` in each of the four skill roots, covering every shipped non-cache runtime file.
- `install-third-party.ps1`, `install-local.ps1`, `安装与操作手册.md`.
- Repository-root `README.md` in the publishing clone.
- New immutable release: `dist/vibe-evals-sonAgent-skills-v2.0.0.zip` and `.zip.sha256`.

### Tests to create

- `tests/test_artifact_integrity.py`
- `tests/v2_fixtures.py`
- `tests/test_form_ready_media.py`
- `tests/test_render_media.py`
- `tests/test_form_ready_observations.py`
- `tests/test_form_ready_decisions.py`
- `tests/test_presentation_workspace.py`
- `tests/test_complete_eval.py`
- `tests/test_validate_form_ready.py`
- `tests/test_package_form_ready.py`
- `tests/test_verify_and_render_form_ready.py`
- `tests/test_runtime_mirrors.py`
- `tests/test_installers.py`
- Modify existing `tests/skill-pressure/scenarios.md`; preserve every v1 scenario and append a separately titled v2 section.
- `tests/skill-pressure/complete-eval-baseline-results.md`
- `tests/skill-pressure/complete-eval-results.md`
- `tests/skill-pressure/verify-render-results.md`

## Normative record and CLI contract

Use lowercase UUIDs for package/run IDs, lowercase 64-character SHA-256 strings, integer rounds, IDs matching `^(EV|VIS|HUM|PRES-HUM|MEDIA|RENDER|ADJ)-[A-Za-z0-9._-]+$`, RFC3339 timestamps with offsets, and POSIX package-relative paths. Apply identity by scope instead of forcing every field onto every record:

- Package envelope: `outer_package_id`, `base_package_id`, `base_zip_sha256`, `source_input_digest`; used everywhere.
- Task envelope: package envelope plus `task_id`; used by task insights and source verification.
- Model envelope: task envelope plus `model_id`; used by model-level presentation slots.
- Rubric envelope: task envelope plus `rubric_id`, `round`, `criterion_sha256`; used by criterion classifications shared across models.
- Model × rubric envelope: rubric envelope plus `model_id`; used by EV/VIS/HUM observations, final decisions, scored rows, and ADJ affected-pair audit entries.

No drive letter, UNC path, `..`, or host absolute path is serializable in the outer package. The run directory separately contains `control/local-state.json`, which is excluded from the ZIP and stores the canonical absolute task root needed for same-machine source rechecks; every runner command verifies its current digest against portable source identity before use.

The media index has two top-level maps. `blobs[sha256]` contains canonical relative path, byte-derived MIME, byte size, and image dimensions. `uses[media_id]` contains blob SHA, role (`target`, `feedback`, `candidate_full`, or `candidate_crop`), acquisition method, parent/crop geometry when applicable, and a nonempty `bindings` array whose members are explicitly task, model, rubric, or model × rubric envelopes. A media plan item requires `media_id`, role, source-relative path or render parent, expected frozen SHA, and that same scoped bindings array. A render receipt is never an input field to `register_media`; it is an output from `render_media.py` and is registered by receipt path.

Each final decision row contains the model × rubric envelope plus `score` (integer 0/1), `decided_by`, `reason`, `evidence_ids`, `decider`, `decided_at`, optional `closes_adjudication_ids`, and optional `closes_gap_ids`. `criterion-classifications.json` contains one row per rubric envelope with `classification`, `measurable_phrase`, `reason`, and `requires_remote_human`; missing/unknown classification is human-required. `adjudication-audit.json` contains one row per inner ADJ × affected model/rubric pair with the ADJ text digest, one shared policy digest, resolution, evidence IDs, and the exact matching final score.

`presentation-input.json` contains package/decision/scored digests, the four task-scoped insight slots, and every model-scoped `overall_impression`, `G1/G2/G3/S1/A1/R1`, two pros, zero-to-five cons, style, and labels. Every slot stores `slot_id`, `machine_proposal`, `proposal_sha256`, `basis`, `evidence_ids`, `recorded_by`, `recorded_at`, and optional `presentation_attestation_id`. Subjective confirmation is a separate `PRES-HUM-*` record in `presentation/presentation-attestations.jsonl` with package envelope, `subject_type` (`task_slot` or `model_slot`), canonical `subject_id`, optional model ID, exact proposal SHA, observer/recorder, interactive capture method, original confirmation text, and time. It does not pretend to be a rubric-scoped HUM record. `run-state.json` stores `run_id`, portable identities, current phase, completed phase receipts, created/updated times, and terminal failure code; it never stores the host task path. These shapes are duplicated as complete fictional fixtures in `tests/v2_fixtures.py` and as JSON Schema examples; tests require agreement between schema-required fields and runtime constants.

All normal remote work goes through this literal command surface from the installed complete-eval `scripts` directory:

| Action | Command shape | Success/next action |
|---|---|---|
| Start raw task | `py complete_eval.py start --task-root C:\Task --output-root C:\vibe-evals-handoff` | `0`; reports new RUN_ID and `accept-evidence` work items. |
| Accept prompt/rubric review | `py complete_eval.py accept-review --run C:\vibe-evals-handoff\RUN_ID --prompt-candidate C:\work\prompt-review.jsonl --rubric-candidate C:\work\rubric-review.json` | `0` only when prompt requirements, rubric bases, and review decisions have exact coverage. |
| Accept worker evidence | `py complete_eval.py accept-evidence --run C:\vibe-evals-handoff\RUN_ID --candidate C:\work\worker.jsonl` | `0` when the expected v1 coverage is complete, otherwise `2` with remaining pairs. |
| Seal v1 base | `py complete_eval.py seal-base --run C:\vibe-evals-handoff\RUN_ID` | `0`; produces and immediately strictly verifies the immutable inner ZIP. |
| Freeze media | `py complete_eval.py freeze-media --run C:\vibe-evals-handoff\RUN_ID --task-root C:\Task --plan C:\work\media-plan.json` | `0` complete or `2` with required missing uses. |
| Render passive media | `py complete_eval.py render --run C:\vibe-evals-handoff\RUN_ID --media-id MEDIA-candidate` | `0`, or `3` unsafe/invalid, or `5` when qualified human action is required. |
| Record vision | `py complete_eval.py record-vision --run C:\vibe-evals-handoff\RUN_ID --model glm5-2 --rubric R1-01 --read 1` | `0` recorded; a second isolated call is mandatory before machine closure. |
| Record rubric human | `py complete_eval.py record-human --run C:\vibe-evals-handoff\RUN_ID --model glm5-2 --rubric R1-01` | Interactive; `0` only after all rubric attestation fields are entered. |
| Record decision | `py complete_eval.py decide --run C:\vibe-evals-handoff\RUN_ID --candidate C:\work\decision.json` | `0` or `2` with exact remaining pairs/closures. |
| Record form semantics | `py complete_eval.py record-presentation --run C:\vibe-evals-handoff\RUN_ID --slot models.glm5-2.G3 --candidate C:\work\slot.json` | `0` or `2/5` with missing evidence/human confirmation. |
| Confirm subjective form slot | `py complete_eval.py attest-presentation --run C:\vibe-evals-handoff\RUN_ID --slot models.glm5-2.G3` | Interactive; writes one `PRES-HUM-*` bound to the exact proposal digest. |
| Build/validate | `py complete_eval.py build-outputs --run C:\vibe-evals-handoff\RUN_ID` then `py complete_eval.py validate --run C:\vibe-evals-handoff\RUN_ID` | Both `0`; otherwise no packaging. |
| Seal outer | `py complete_eval.py package --run C:\vibe-evals-handoff\RUN_ID --task-root C:\Task --output C:\delivery\task-form-ready.zip` | `0`; writes ZIP and sidecar, `4` terminal source change, other failure writes neither. |

Exit `2` always means valid progress with unresolved remote work, `3` means invalid data/safety failure, `4` means terminal source mutation, and `5` means a real remote human or unavailable qualified capability is required. The runbook must print the exact next command generated from state, never ask the Agent to infer it.

## Task 1: Capture RED behavior and freeze the legacy baseline

**Files:**

- Modify: `tests/skill-pressure/scenarios.md` by appending v2 scenarios without changing the v1 section.
- Create: `tests/skill-pressure/complete-eval-baseline-results.md`
- Modify: `tests/test_end_to_end.py`

- [ ] **Step 0: Verify the implementation starting point**

After this plan has been committed and pushed, fetch `origin/main` before any implementation commit. Require `git -C $RepoRoot rev-list --left-right --count HEAD...origin/main` to print `0 0`, require the worktree clean, and record that exact HEAD as `$ImplementationBase`. If the remote advanced, integrate deliberately and rerun this check rather than proceeding on a stale base.

- [ ] **Step 1: Record the ten pressure scenarios verbatim**

Write scenarios covering: missing `target.png` under delivery pressure; a single low-confidence vision read plus an instruction to call it human; 44/45 completed rubrics plus sunk-cost pressure; two conflicting vision reads; active SVG content; an incomplete “looks fine” human response; one policy applied inconsistently across models; missing `rubrics说明.xlsx`; source mutation after media freeze; and a child agent claiming success without validator output.

Each scenario ends with this observable checklist:

```text
must_not_seal_form_ready: true
must_not_claim_remote_human_without_complete_attestation: true
must_keep_unresolved_work_on_remote: true
must_require_strict_validator_before_ready: true
```

- [ ] **Step 2: Run each remote scenario without the new skill**

Use a fresh child agent for every scenario. Do not expose the proposed v2 skill text. Save its complete answer and mark each violated checklist item in `complete-eval-baseline-results.md`. The existing real `PS-0819copy` result is supporting evidence, but does not replace these controlled RED runs.

- [ ] **Step 3: Add a legacy end-to-end assertion**

Extend the existing v1 test so it explicitly proves legacy prepare still creates `人工裁定清单.md` and `evidence_requests.json` for a `ready_for_local_review` v1 bundle:

```python
review = prepare_local_review(extracted, review_root)
self.assertFalse(review["can_finalize"])
self.assertTrue((review_root / "人工裁定清单.md").is_file())
self.assertTrue((review_root / "evidence_requests.json").is_file())
```

- [ ] **Step 4: Run the unchanged baseline suite**

Run: `py -m unittest discover -s tests -v`

Expected: all existing tests pass; only the existing Windows symlink privilege test may skip.

- [ ] **Step 5: Commit the RED evidence and legacy guard**

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "test: capture remote completion failure baseline"
```

## Task 2: Extract one archive-integrity deep module without regressing v1

**Files:**

- Create: `shared/scripts/artifact_integrity.py`
- Create: `tests/test_artifact_integrity.py`
- Modify: `shared/scripts/package_bundle.py`
- Modify: `shared/scripts/package_delta.py`
- Modify: `shared/scripts/extract_artifact.py`
- Mirror changed files into all current skill runtime directories that already carry them.

- [ ] **Step 1: Write failing compatibility and security tests**

The test imports these exact functions and exercises filename-bound sidecars, optional expected digest, traversal, Windows reserved/device names, case-fold and Unicode-normalization collisions, encrypted members, symlinks, member/size/ratio limits, checksum coverage, injected mid-extraction I/O failure, and atomic cleanup:

```python
from artifact_integrity import (
    sha256_file,
    verify_sidecar,
    safe_extract_zip,
    detect_artifact_kind,
    write_checksum_manifest,
    verify_checksum_manifest,
)

def test_sidecar_actual_and_user_expected_digest_must_all_match(self):
    digest = sha256_file(self.archive)
    self.sidecar.write_text(f"{digest}  {self.archive.name}\n", encoding="utf-8")
    self.assertEqual(digest, verify_sidecar(self.archive, self.sidecar, digest))
    with self.assertRaisesRegex(ValueError, "expected SHA-256"):
        verify_sidecar(self.archive, self.sidecar, "0" * 64)
```

- [ ] **Step 2: Run the focused test and observe RED**

Run: `py -m unittest tests.test_artifact_integrity -v`

Expected: import failure for `artifact_integrity`.

- [ ] **Step 3: Implement the integrity API**

Create a dependency-free module with `DEFAULT_LIMITS` set to 10,000 members, 128 MiB per member, 512 MiB total uncompressed bytes, and a 200:1 maximum compression ratio. Its required public interfaces are:

- `sha256_file(path: str | Path) -> str`
- `verify_sidecar(archive: str | Path, sidecar: str | Path, expected_sha256: str | None = None) -> str`
- `safe_extract_zip(archive: str | Path, destination: str | Path, *, limits: dict | None = None) -> Path`
- `detect_artifact_kind(archive: str | Path) -> tuple[str, str]`
- `write_checksum_manifest(root: str | Path, *, excludes: set[str]) -> Path`
- `verify_checksum_manifest(root: str | Path, *, excludes: set[str]) -> list[dict]`

`safe_extract_zip` must reject pre-existing destinations, absolute paths, drives, `..`, trailing-dot/space names, Windows reserved names, case-fold/Unicode-normalization collisions, encrypted entries, symlinks/devices, and all four limits before extracting any member. It extracts into a same-parent staging directory and renames only after success. `detect_artifact_kind` performs the same complete central-directory preflight, then reads one bounded root manifest; duplicate normalized names, both `FORM-READY.json` and `MANIFEST.json`, or an unknown schema fail without fallback.

- [ ] **Step 4: Convert v1 scripts into thin adapters**

Keep `package_bundle.sha256` and `package_bundle.safe_extract_zip` import-compatible:

```python
from artifact_integrity import safe_extract_zip, sha256_file

def sha256(path: Path) -> str:
    return sha256_file(path)
```

Use the shared checksum writer in bundle and delta packagers. Keep all v1 filenames, status codes, and schemas unchanged.

- [ ] **Step 5: Verify focused and full tests**

Run:

```powershell
py -m unittest tests.test_artifact_integrity tests.test_tools tests.test_end_to_end -v
py -m unittest discover -s tests -v
```

Expected: all pass except the established symlink privilege skip.

- [ ] **Step 6: Commit**

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared vibe-evals-sonAgent-skills/vibe-evals-* vibe-evals-sonAgent-skills/tests/test_artifact_integrity.py
git -C "$RepoRoot" commit -m "refactor: centralize artifact integrity checks"
```

## Task 3: Define v2 schema, fixtures, and the immutable inner-package context

**Files:**

- Create: `shared/schema/form-ready-bundle.schema.json`
- Create: `shared/schema/media-index.schema.json`
- Create: `shared/schema/observation-record.schema.json`
- Create: `shared/schema/final-decisions.schema.json`
- Create: `shared/schema/criterion-classification.schema.json`
- Create: `shared/schema/adjudication-audit.schema.json`
- Create: `shared/schema/source-verification.schema.json`
- Create: `shared/schema/form-input.schema.json`
- Create: `shared/schema/presentation-input.schema.json`
- Create: `shared/schema/presentation-attestation.schema.json`
- Create: `shared/schema/verification-receipt.schema.json`
- Create: `shared/references/gap-policy.json`
- Create: `shared/scripts/form_ready_context.py`
- Create: `tests/v2_fixtures.py`
- Create: `tests/test_validate_form_ready.py`

- [ ] **Step 1: Build a real nested-package fixture**

`make_form_ready_workspace(root)` must call the existing `make_bundle`, `package_bundle`, and `safe_extract_zip`; it must not mock v1 validation. Return paths for the sealed inner ZIP, outer root, inner package ID, and source digest.

- [ ] **Step 2: Write the first failing contract tests**

```python
from validate_form_ready import assess_form_ready, validate_form_ready

def test_assesses_minimal_complete_unsealed_form_ready_workspace(self):
    fixture = make_form_ready_workspace(self.root, complete=True)
    report = assess_form_ready(fixture.outer)
    self.assertEqual("pass", report["result"], report["errors"])
    self.assertEqual("ready_for_form", report["derived_status"])

def test_rejects_wrong_schema_and_unsafe_internal_path(self):
    fixture = make_form_ready_workspace(self.root, complete=True)
    rewrite_manifest(fixture.outer, schema_version="1.0.0", decisions_path="../escape.json")
    codes = {row["code"] for row in validate_form_ready(fixture.outer, require_seal=False)["errors"]}
    self.assertIn("SCHEMA_UNSUPPORTED", codes)
    self.assertIn("UNSAFE_PATH", codes)
```

- [ ] **Step 3: Run RED**

Run: `py -m unittest tests.test_validate_form_ready.ValidateFormReadyTests.test_assesses_minimal_complete_unsealed_form_ready_workspace -v`

Expected: import failure for `validate_form_ready`.

- [ ] **Step 4: Write exact schema constants and loaders**

`FORM-READY.json` requires:

```json
{
  "schema": "vibe-evals-form-ready-bundle",
  "schema_version": "2.0.0",
  "outer_package_id": "uuid",
  "base": {
    "archive_path": "base/evidence-bundle.zip",
    "sha256_path": "base/evidence-bundle.zip.sha256",
    "sha256": "64 lowercase hex",
    "original_name": "source-name.zip",
    "package_id": "inner package id",
    "source_input_digest": "inner source digest"
  },
  "paths": {
    "media_index": "observations/media-index.json",
    "machine_vision": "observations/machine-vision.jsonl",
    "remote_human": "observations/remote-human.jsonl",
    "final_decisions": "decisions/final-decisions.json",
    "criterion_classifications": "decisions/criterion-classifications.json",
    "adjudication_audit": "decisions/adjudication-audit.json",
    "source_verification": "integrity/source-verification.json",
    "run_state": "provenance/run-state.json",
    "scored_dir": "scored",
    "presentation_input": "presentation/presentation-input.json",
    "presentation_attestations": "presentation/presentation-attestations.jsonl",
    "report_input": "presentation/report-input.json",
    "report": "presentation/反馈报告.md",
    "heatmap": "presentation/rubrics打分热力图.html",
    "form_input": "presentation/form-input.json"
  },
  "package_status": "incomplete"
}
```

`load_base_context(extracted_base)` returns a dataclass containing manifest, package/source IDs, model map, ordered baseline rubrics, base evidence by `(model_id, rubric_id)`, pending adjudications, material gaps, and human-check pairs.

Every outer media use, observation, decision, audit, and form-input record must bind `outer_package_id`, inner `package_id`, base ZIP SHA-256, `source_input_digest`, model ID, rubric ID, rubric round, and SHA-256 of the immutable rubric criterion where applicable. Tests replace records with valid records from another workspace and require `IDENTITY_MISMATCH`.

- [ ] **Step 5: Implement the validator shell**

`assess_form_ready(root)` validates manifest shape and path safety, verifies the inner sidecar and SHA, safely extracts the inner package to a temporary directory, runs the v1 validator in strict sealed mode, and checks inner package/source identity without requiring a seal or comparing declared package status. `validate_form_ready(root, require_seal=False)` wraps that assessment and enforces declared-status consistency. This Task 3 contract is deliberately limited to those base-package checks; Tasks 4, 5, 6, 7, 8, and 9 add the named media, observation, decision, score-projection, presentation, sealing, and local-render checks, each with its own RED test before implementation.

Parse every checked-in JSON Schema and resolve every local `$ref` in tests. Runtime conformance is enforced by the custom validator; a test compares schema enums/required fields with the Python constants so the documentation contract cannot silently drift.

- [ ] **Step 6: Run focused tests and commit**

Run: `py -m unittest tests.test_validate_form_ready -v`

Expected: the two initial contract tests pass.

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared/schema vibe-evals-sonAgent-skills/shared/references/gap-policy.json vibe-evals-sonAgent-skills/shared/scripts/form_ready_context.py vibe-evals-sonAgent-skills/shared/scripts/validate_form_ready.py vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: define immutable form-ready bundle context"
```

## Task 4: Initialize form-ready workspaces and freeze all review media

**Files:**

- Create: `shared/scripts/initialize_form_ready.py`
- Create: `shared/scripts/register_media.py`
- Create: `shared/scripts/render_media.py`
- Create: `tests/test_form_ready_media.py`
- Create: `tests/test_render_media.py`
- Modify: `tests/v2_fixtures.py`

- [ ] **Step 1: Write initializer and source-media RED tests**

Cover exact inner ZIP preservation, fixed-name sidecar regeneration, source immutability, frozen-inventory binding, content-addressed path, duplicate IDs, missing/unindexed files, MIME/extension mismatch, source-root symlink/junction escape, overwrite refusal, image dimension/pixel limits, and source mutation after media freeze.

```python
workspace = initialize_form_ready(inner_zip, inner_sidecar, outer, expected_sha256=inner_digest)
self.assertEqual(inner_zip.read_bytes(), (workspace / "base/evidence-bundle.zip").read_bytes())
self.assertIn("evidence-bundle.zip", (workspace / "base/evidence-bundle.zip.sha256").read_text("utf-8"))
```

- [ ] **Step 2: Write render-registration RED tests**

Require a valid PNG byte signature, canonical MIME/extension, bounded dimensions/pixels, parent media IDs, renderer executable digest/version/fixed argv, adapter class, model/round/rubrics, start/end time, input/output SHA, stdout/stderr digest, and script-derived safety findings. A cropped render must name a full-render parent and in-bounds `x/y/width/height`; tests must reject a caller-authored receipt and any out-of-bounds crop.

- [ ] **Step 3: Run RED**

Run: `py -m unittest tests.test_form_ready_media -v`

Expected: imports fail for `initialize_form_ready` and `register_media`.

- [ ] **Step 4: Implement workspace initialization**

Use the public interface `initialize_form_ready(base_zip: str | Path, base_sidecar: str | Path, output_dir: str | Path, *, expected_sha256: str | None = None, run_state: dict | None = None) -> Path`.

It verifies the inner ZIP before creating output, copies the original bytes to the fixed path, creates a new sidecar naming `evidence-bundle.zip`, derives package/source identity from strict inner validation, writes an `incomplete` manifest, empty JSONL files, an empty media index, and no `READY.json`. For standalone tests it creates `provenance/run-state.json` at `base_verified`; in normal runner use it accepts only a matching state with a valid `base_sealed` receipt and advances that same RUN_ID to `base_verified`. Only `complete_eval.py` may advance later phases.

- [ ] **Step 5: Implement media registration**

Expose `register_source_media(form_root, task_root, source_freeze, plan_item) -> dict` and `register_render(form_root, render_path, receipt) -> dict`.

Source media is accepted only when `source_relative_path`, size, and SHA match `source-freeze.json`. Model the index as SHA-unique `blobs` plus separately identified `uses`, so one blob can serve multiple rubric/role references without ambiguity. Store it under `observations/source-media/{sha256}{canonical_suffix}` and store renders under `observations/renders/{sha256}.png`; derive MIME from bytes, not the filename. Every prompt/rubric/feedback/manual-record media reference in the frozen source produces a required use, and missing target/feedback stays unresolved. Update the index atomically and enforce exact file/index coverage.

- [ ] **Step 6: Reject active SVG before any rendering**

Implement a deny-by-default passive SVG gate. It rejects `script`, `foreignObject`, all animation/SMIL elements, event attributes, DOCTYPE/entities, CSS `@import`, every CSS `url()`, direct-IP/localhost/network/UNC/file URLs, nested active `data:` content, unknown namespaces/elements/attributes, and references outside the same frozen SVG document. Permit only an explicit SVG presentation whitelist. Return stable code `UNSAFE_SVG`; never invoke a renderer when parsing or whitelisting is incomplete. Add an attack test for every rejected form.

- [ ] **Step 7: Implement a renderer-owned receipt**

`render_media.py` supports only content proven passive by the whitelist gate and a discovered absolute Microsoft Edge or Chrome executable. It launches a new temporary browser profile with fixed headless flags and records executable SHA/version, exact argv with temporary paths replaced by stable `<TEMP_PROFILE>` and `<INPUT_FILE>` tokens, timestamps, exit code, stdout/stderr digests, input/output SHA, and adapter classification `passive_static_only`. The receipt must explicitly state that this path is not an active-code sandbox and must not populate the six active-code safety claims. v2.0.0 does not automate active HTML/app rendering: return `NO_QUALIFIED_RENDERER` and keep the run incomplete unless a future separately specified OS/container isolation adapter is present. Never accept caller-supplied safety flags as proof.

- [ ] **Step 8: Verify and commit**

Run:

```powershell
py -m unittest tests.test_form_ready_media tests.test_render_media -v
py -m unittest tests.test_validate_form_ready -v
```

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared/scripts vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: freeze form-ready review media"
```

## Task 5: Validate machine-vision and remote-human observations

**Files:**

- Create: `shared/scripts/validate_final_decisions.py`
- Create: `shared/scripts/record_observation.py`
- Create: `tests/test_form_ready_observations.py`
- Modify: `shared/scripts/form_ready_context.py`
- Modify: `shared/scripts/validate_form_ready.py`

- [ ] **Step 1: Write machine-vision RED tests**

Create tests with these exact names:

```text
test_machine_vision_accepts_two_consistent_independent_reads_with_same_media
test_machine_vision_rejects_single_read
test_machine_vision_rejects_identical_prompts_as_non_independent
test_machine_vision_rejects_same_invocation_or_session_id
test_machine_vision_conflict_or_low_confidence_requires_remote_human
test_machine_vision_requires_raw_answer_specific_fact_and_target_candidate_media
test_machine_vision_cannot_close_subjective_or_policy_ambiguous_rubric
test_machine_vision_static_evidence_conflict_requires_remote_human
```

Stable error codes are `VISION_READ_COUNT`, `VISION_READ_NOT_INDEPENDENT`, `VISION_CONFLICT`, `VISION_LOW_CONFIDENCE`, `VISION_RECORD_INCOMPLETE`, and `VISION_POLICY_REQUIRES_HUMAN`.

- [ ] **Step 2: Write remote-human RED tests**

Create tests proving that `HUM-*` requires a stable actor alias, timestamp, nonempty steps, expected result, observed result, observed package/render IDs, raw confirmation, interactive capture method, recorder identity, and matching model/rubric. Non-human decisions containing `人工确认`, `真人确认`, or `用户已确认` fail with `HUMAN_CLAIM_FOR_NON_HUMAN`. State in schema/docs/tests that these fields prove a complete attributable attestation was recorded; they cannot cryptographically prove a human existed.

- [ ] **Step 3: Run RED**

Run: `py -m unittest tests.test_form_ready_observations -v`

Expected: observation validation functions are missing.

- [ ] **Step 4: Implement observation registries**

Use the following normalized records:

```json
{"observation_id":"VIS-model-rubric-01","type":"machine_vision","outer_package_id":"uuid","base_package_id":"inner-id","base_zip_sha256":"64hex","source_input_digest":"64hex","task_id":"task-fixture","model_id":"model","rubric_id":"R1-01","round":1,"criterion_sha256":"64hex","read_index":1,"invocation_id":"provider-request-id","session_id":"isolated-read-id","independent_context":true,"input_media_ids":["MEDIA-target","RENDER-candidate"],"tool":"vision-bridge","model":"vision-model-id","tool_version":"version","prompt_sha256":"64hex","question":"exact prompt","raw_response_sha256":"64hex","raw_response":"unaltered response","fact":"specific observable fact","verdict":1,"confidence":"high","conflict":false,"observed_at":"RFC3339"}
```

```json
{"observation_id":"HUM-model-rubric-01","type":"remote_human","outer_package_id":"uuid","base_package_id":"inner-id","base_zip_sha256":"64hex","source_input_digest":"64hex","task_id":"task-fixture","model_id":"model","rubric_id":"R1-01","round":1,"criterion_sha256":"64hex","observer":"stable alias","recorded_by":"agent alias","capture_method":"interactive_terminal","observed_at":"RFC3339","steps":["open verified full render"],"expected_result":"criterion-specific result","observed_result":"specific observation","observed_version":"inner package id + render id","confirmation_text":"human's original words","media_ids":["MEDIA-target","RENDER-candidate"],"verdict":1}
```

Build registries by ID and pair. Reject duplicate IDs, unknown media, cross-pair or cross-package references, invalid verdicts, malformed time, future-round evidence, rubric-criterion drift, and raw-response/prompt hash mismatch. `record_observation.py vision` atomically appends only after validating provider-returned invocation metadata; `record_observation.py human` is interactive and refuses piped/defaulted confirmation text so a weak Agent cannot silently invent the attestation through the normal CLI.

- [ ] **Step 5: Implement the dual-read gate**

For `machine_vision`, require exactly two cited reads with indexes `{1, 2}`, identical normalized media sets, different invocation/session IDs, different normalized questions and prompt digests, `independent_context=true`, high confidence, equal verdicts, `conflict=false`, and an objective-visual criterion classification. The second read must not contain the first answer. Policy-ambiguous or subjective classification always requires `remote_human`. If a vision verdict contradicts deterministic inner evidence direction (`support`, `refute`, or `confirm_missing`) or touches an inner policy ambiguity/ADJ, emit `VISION_STATIC_CONFLICT` or `VISION_POLICY_REQUIRES_HUMAN` and require a human record. This is an auditable independence approximation, not cryptographic proof of model independence.

Generate `decisions/criterion-classifications.json` from the rubric index before observations. Only exact `presence`, `absence`, `count`, `literal_text`, `exact_color`, `explicit_relative_position`, and `explicit_size` categories may be proposed as objective, and each proposal binds the criterion digest and quotes the concrete measurable phrase. Adjectives such as aesthetic/natural/clear/acceptable/usable, any policy ambiguity, and every ADJ are forced to `subjective_or_policy` and require HUM. An unrecognized category defaults to human; tests attempt to misclassify each subjective example and require `CRITERION_CLASS_REQUIRES_HUMAN`.

- [ ] **Step 6: Probe the actual vision adapter contract**

Before implementing the normalizer, invoke the installed vision bridge once on a checked-in harmless PNG fixture and record which provider IDs, model/version, raw response blocks, and session metadata are actually available. Implement an adapter for that observed contract. If provider request/session IDs are absent, the runner generates a fresh nonce and call ID before each isolated vision worker, embeds the nonce in the prompt, and hashes the complete returned tool block into the record; two reads require different worker-context receipts. Missing verifiable metadata escalates to `remote_human` rather than accepting Agent-filled IDs.

- [ ] **Step 7: Verify and commit**

Run: `py -m unittest tests.test_form_ready_observations -v`

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared vibe-evals-sonAgent-skills/tests/test_form_ready_observations.py
git -C "$RepoRoot" commit -m "feat: validate audited remote observations"
```

## Task 6: Close every decision and project portable scored artifacts

**Files:**

- Create: `shared/scripts/project_form_ready_scores.py`
- Create: `shared/scripts/decision_workspace.py`
- Create: `shared/scripts/render_supporting_outputs.py`
- Create: `shared/scripts/presentation_workspace.py`
- Create: `tests/test_form_ready_decisions.py`
- Create: `tests/test_presentation_workspace.py`
- Modify: `shared/scripts/validate_final_decisions.py`
- Modify: `shared/scripts/validate_form_ready.py`

- [ ] **Step 1: Write exact-coverage and provenance RED tests**

Test all four `decided_by` values, their exact permitted evidence type/direction, common nonempty fields, all package/source/rubric identity fields, same model/rubric/round references, existing pending-adjudication closure, and typed material-gap closure.

```python
self.assertEqual(
    {"mechanical", "accepted_machine", "machine_vision", "remote_human"},
    accepted_actor_values(),
)
```

Add failure tests for one missing score, a `null`, a stale ADJ, a human-check pair without VIS/HUM evidence, an unresolved gap, and an optional-versus-semantic gap misclassification.

- [ ] **Step 2: Run RED**

Run: `py -m unittest tests.test_form_ready_decisions -v`

Expected: decision coverage and projection APIs are missing.

- [ ] **Step 3: Implement final-decision policy**

`validate_final_decisions(base_ctx, media_registry, observations, decisions_path)` returns:

```python
{
    "result": "pass" | "fail",
    "errors": list[dict],
    "registry": dict[tuple[str, str], dict],
    "unresolved": {
        "scores": int,
        "adjudications": int,
        "human_checks": int,
        "material_gaps": int,
    },
}
```

Require exact model × rubric coverage, score in `{0,1}`, reason, decision source, decider, timestamp, identity binding, and nonempty evidence IDs. Enforce this decision matrix:

| `decided_by` | Allowed closure |
|---|---|
| `accepted_machine` | Inner suggested score is already 0/1, decision equals it, inner item has no human check or affected pending ADJ, and cited `EV-*` records support the same direction. |
| `mechanical` | Deterministic `EV-*` only; score 1 requires `support`, score 0 requires `refute` or `confirm_missing`; no subjective, visual, policy-ambiguous, or pending item. |
| `machine_vision` | Exactly the qualified independent VIS pair from Task 5, matching score, with no static conflict or policy ambiguity. |
| `remote_human` | At least one complete matching `HUM-*`; related `EV-*`/`VIS-*` may be included but cannot replace the human attestation. |

Existing inner pending items remain byte-for-byte inside the v1 ZIP; each affected model/rubric pair must be closed exactly once in the outer decision layer. Check in `gap-policy.json` as the executable authority: exact known optional IDs list mechanical preconditions; required gaps are never closable; semantic and unknown gaps require `remote_human` plus a limitation statement. Do not infer optionality from a gap's prose.

`decision_workspace.py initialize` generates every model × rubric row and every pending-ADJ/material-gap closure slot from `BaseContext`; it refuses extra/missing pairs. `decision_workspace.py record` fills one slot atomically after immediate policy validation. It deterministically writes `decisions/adjudication-audit.json` from inner pending items plus outer resolutions, including one policy digest, exact affected pairs, cited observations, and final scores; replay must be byte-identical.

- [ ] **Step 4: Implement portable score projection**

Expose `project_scores(base_ctx, decision_registry, form_root: str | Path) -> dict` and `verify_projected_scores(base_ctx, decision_registry, form_root: str | Path) -> dict`. The implementation owns the canonical `form_root/scored` directory; callers cannot redirect scored output outside the package.

Preserve every baseline rubric field exactly and add only `score` and `reason`. Write `scoring-summary.json` with POSIX package-relative paths such as `scored/rubrics-glm5-2.json`; never write drive letters or absolute paths. Replay into a temporary directory and byte-compare every scored rubric and audit file.

- [ ] **Step 5: Collect every semantic V2.1 input in a validated workspace**

`presentation_workspace.py initialize --form-root <root>` writes exact empty slots for the four task insights (`ranking_reason`, `maximum_difference`, `capability_boundary`, `difficulty_and_approach`) and, per model, `overall_impression`, dimensions `G1/G2/G3/S1/A1/R1`, exactly two pros, zero-to-five cons, style, and label arrays. `record --slot <canonical-slot> --value-json <candidate>` requires a machine proposal, nonempty basis, evidence IDs from the same package/model, recorder/time, and proposal digest. `attest --slot <canonical-slot>` interactively creates a `PRES-HUM-*` record bound to that exact subject/proposal digest; subjective `overall_impression`, `G3`, style/labels, aesthetic prose, and every disputed proposal require this presentation attestation. Each applicable score is 0–4 in 0.5 increments; applicability fields and V2.1 policies come from the checked-in schema/label library, not Agent invention. Missing model/dimension/pro/con/style/task slots, unsupported labels, a prose claim lacking evidence, or a required attestation mismatch keeps `presentation_complete` false.

The completed `presentation/presentation-input.json` binds outer/base/source IDs, final-decisions digest, every scored-file digest, each slot's evidence references, machine proposal, and any required HUM confirmation. It is an auditable remote recommendation; it is never labeled as a local human score.

- [ ] **Step 6: Generate supporting outputs without hand-authored JSON**

`render_supporting_outputs.py` consumes only validated presentation input, decisions, and scored artifacts and writes `presentation/report-input.json`, `presentation/反馈报告.md`, `presentation/rubrics打分热力图.html`, and `presentation/form-input.json`. Form input binds the outer ID plus digests of presentation input, final decisions, and every scored file. Heatmap and form input are byte-replayed; the report is generated from structured per-rubric claims with evidence IDs, not free-form totals. Add a swap test where totals are unchanged but two rubric scores are exchanged; validation must fail.

- [ ] **Step 7: Verify and commit**

Run: `py -m unittest tests.test_form_ready_decisions tests.test_presentation_workspace -v`

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared vibe-evals-sonAgent-skills/tests/test_form_ready_decisions.py
git -C "$RepoRoot" commit -m "feat: close remote decisions and project scores"
```

## Task 7: Complete the deep v2 validator

**Files:**

- Modify: `shared/scripts/validate_form_ready.py`
- Modify: `tests/test_validate_form_ready.py`
- Modify: `tests/v2_fixtures.py`

- [ ] **Step 1: Add unresolved-state RED tests**

Add these tests:

```text
test_outer_decisions_can_close_immutable_inner_null_pending_and_human_check
test_refuses_form_ready_when_any_inner_null_lacks_final_binary_decision
test_refuses_form_ready_when_pending_adjudication_lacks_remote_resolution
test_refuses_form_ready_when_human_check_lacks_valid_visual_or_human_observation
test_refuses_form_ready_when_material_gap_lacks_typed_resolution
test_requires_exact_model_times_rubric_decision_coverage
```

The success test must compare the inner ZIP bytes and SHA before and after outer closure.

- [ ] **Step 2: Add media and presentation cross-check RED tests**

Reject unindexed media files, missing media blobs, wrong MIME/size/hash, orphan observation IDs, scored/audit replay drift, report claim drift, incorrect heatmap cells, unknown form evidence, decision/scored digest mismatch, same-total score swaps, and cross-model or cross-package form references.

- [ ] **Step 3: Run RED**

Run: `py -m unittest tests.test_validate_form_ready -v`

Expected: new cases fail with stable codes rather than exceptions.

- [ ] **Step 4: Compose the validator in this exact order**

```python
def validate_form_ready(root, require_seal=True, scratch_root=None):
    # 1 manifest and safe paths
    # 2 outer seal when required
    # 3 inner ZIP/sidecar and strict v1 validation
    # 4 base package/source identity binding
    # 5 exact media registry and byte coverage
    # 6 observation registry
    # 7 final decisions and zero unresolved counts
    # 8 portable score replay
    # 9 report, heatmap, and form-input cross-check
    # 10 declared-versus-derived status and counts
    return report
```

Implement each numbered stage as a named helper; the public function must collect semantic errors instead of crashing at the first malformed field. Add `assess_form_ready(root, scratch_root=None)` for the packager: it performs every semantic/replay/source-receipt check but does not require a seal or compare declared `package_status`. Public `validate_form_ready` does compare the declared status and, when strict, the seal. Derived status is `ready_for_form` only when the error list is empty and all four unresolved counters are zero.

Define and recompute outer `READY.json`/validation counts: models, rubrics, media uses, unique blobs, VIS records, HUM records, final decisions, closed inner adjudication pairs, closed inner human-check pairs, optional/semantic gap closures, and each unresolved counter. Keep the inner historical counts visible under `base_counts`; never misreport the inner eight human checks as outer unresolved work after valid closure.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
py -m unittest tests.test_validate_form_ready tests.test_form_ready_media tests.test_form_ready_observations tests.test_form_ready_decisions -v
```

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared/scripts/validate_form_ready.py vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: enforce form-ready closure contract"
```

## Task 8: Seal and attack-test the outer package

**Files:**

- Create: `shared/scripts/package_form_ready.py`
- Create: `tests/test_package_form_ready.py`
- Modify: `shared/scripts/extract_artifact.py`

- [ ] **Step 1: Write package RED tests**

Add tests for: only a semantically complete workspace may package; source mutation after media freeze; no `.partial` or final ZIP remains after failure; sidecar-write failure rolls back the exact formal ZIP; strict validation passes after round-trip extraction; outer traversal/collision/symlink/encrypted-member/bomb; inner byte tampering; rewritten outer checksum; replacement by another valid inner bundle; cross-workspace record replacement; and malicious inner ZIP traversal.

- [ ] **Step 2: Run RED**

Run: `py -m unittest tests.test_package_form_ready -v`

Expected: import failure for `package_form_ready`.

- [ ] **Step 3: Implement atomic packaging**

Define `FORM_READY_EXCLUDES` as exactly `READY.json`, `integrity/files.sha256`, and `integrity/validation-report.json`, and expose `package_form_ready(form_root: str | Path, output_zip: str | Path, *, task_root: str | Path) -> dict`.

Before assessment, call the existing v1 source verifier against the inner freeze and `task_root`; write script-derived `integrity/source-verification.json` with source digest, base ZIP/package IDs, checked-at time, and result. If and only if `assess_form_ready` derives `ready_for_form`, set `FORM-READY.json.package_status` to `ready_for_form`, write the validation report/READY/checksum manifest, create staged ZIP and staged sidecar, extract to a new temporary path, run strict validation, and verify the live source a second time. Publish ZIP and sidecar as one rollback-capable operation. If either source check fails, remove workspace READY/checksum/validation report, restore manifest status to `incomplete`, atomically set run state to terminal `invalid_source_changed`, remove only invocation-owned staging/formal outputs, return exit code 4, and forbid reuse even if source bytes are later restored. No Agent-authored `source-verification.json` is accepted.

- [ ] **Step 4: Add form-ready extraction routing**

Extend `extract_artifact.py` so it first verifies the sidecar and optional expected SHA without reading ZIP metadata, then detects schema via `detect_artifact_kind`; caller-provided `kind` may constrain the detected result but cannot override it. A form-ready artifact safely extracts and calls strict `validate_form_ready`. Keep existing bundle/delta CLI behavior byte-compatible; malformed/unknown v2 never falls back to v1.

- [ ] **Step 5: State the cryptographic boundary in tests and docs**

Test detection when the external sidecar or expected digest remains trusted. Explicitly do not claim that self-contained checks can detect an attacker who rewrites every outer byte and all external expected digests.

- [ ] **Step 6: Verify and commit**

Run:

```powershell
py -m unittest tests.test_package_form_ready tests.test_tools -v
py -m unittest discover -s tests -v
```

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: seal and verify nested form-ready artifacts"
```

## Task 9: Add a one-call local verify-and-render path

**Files:**

- Create: `shared/scripts/verify_and_render_form_ready.py`
- Create: `tests/test_verify_and_render_form_ready.py`
- Modify: `shared/scripts/render_v21_form.py`

- [ ] **Step 1: Write relocation and zero-adjudication RED tests**

Create tests proving a form-ready ZIP copied to an unrelated drive-like directory can render using only archive, sidecar, output, and optional expected SHA. Assert the output contains the form, copied verified report, heatmap, and verification receipt, but contains none of:

```python
FORBIDDEN_LOCAL_FILES = {
    "人工裁定清单.md",
    "evidence_requests.json",
    "human-decisions.json",
    "local-human-evidence.jsonl",
}
```

Also prove unresolved v2 is rejected without creating any of these files and that the rendered Markdown passes byte-exact verification.

- [ ] **Step 2: Run RED**

Run: `py -m unittest tests.test_verify_and_render_form_ready -v`

Expected: import failure for `verify_and_render_form_ready`.

- [ ] **Step 3: Extract a shared form-render core**

Refactor `render_v21_form.py` so ranking, dimensions, labels, and Markdown assembly live in the public interface `build_form_from_context(manifest: dict, form_data: dict, model_map: dict, rubric_ids: list[str], totals: dict[str, int], evidence_meta_by_model: dict[str, dict[str, dict]], label_library: Path) -> tuple[str, dict]`.

The existing `_load` remains the v1 adapter and must produce identical legacy output. The v2 adapter obtains the evidence registry from strict `validate_form_ready`. Keep only one ordering and Markdown implementation.

- [ ] **Step 4: Implement one-call local consumption**

Expose `verify_and_render(archive: str | Path, sidecar: str | Path, output_dir: str | Path, *, expected_sha256: str | None = None, label_library: str | Path | None = None) -> dict`.

It refuses an existing output directory, verifies/extracts the outer package to a temporary directory, strictly validates it, safely extracts the inner bundle to another temporary directory, renders to a temporary file, byte-verifies, then atomically publishes the form, report, heatmap, and `verification-receipt.json`. It never imports or calls `prepare_local_review`.

`verification-receipt.json` follows the Task 3 schema and contains the actual outer ZIP SHA-256, outer/base package IDs, source digest, outer and inner validator versions/results, fully recomputed counts, output form/report/heatmap SHA-256 values, and RFC3339 `rendered_at`. It contains no absolute source/output paths. Tests alter each digest/count and require rejection before publication.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
py -m unittest tests.test_render_v21_form tests.test_verify_and_render_form_ready -v
py -m unittest discover -s tests -v
```

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/shared vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: verify and render form-ready bundles locally"
```

## Task 10: Create the remote complete-eval skill using skill TDD

**Files:**

- Create: `shared/scripts/record_review.py`
- Create: `shared/scripts/record_evidence.py`
- Create: `shared/scripts/complete_eval.py`
- Create: `tests/test_complete_eval.py`
- Create the complete `vibe-evals-son-complete-eval/` tree listed in the file map.
- Mirror the complete import-closed runtime listed in Step 5 into its `scripts/`, `schema/`, `references/`, and `templates/` directories.
- Create: `tests/skill-pressure/complete-eval-results.md`
- Create: `tests/test_runtime_mirrors.py`
- Modify: `vibe-evals-son-evidence-export/SKILL.md`
- Modify: `vibe-evals-son-evidence-export/agents/openai.yaml`
- Modify: `vibe-evals-son-evidence-export/references/step-by-step-runbook.md`
- Modify: `vibe-evals-son-evidence-export/references/evidence-decisions.md`
- Modify: `vibe-evals-son-evidence-supplement/SKILL.md`
- Modify: `vibe-evals-son-evidence-supplement/agents/openai.yaml`
- Modify: `vibe-evals-son-evidence-supplement/references/supplement-decisions.md`

- [ ] **Step 1: Write the minimal skill entrypoint**

The frontmatter description starts with `Use when` and routes only complete cross-machine evaluations. The body states: normal delivery means `FORM_READY_SEALED`; unresolved work remains remote; machine and human identities never blur; active content obeys the six-part safety gate; and only the validator can authorize packaging.

- [ ] **Step 2: Implement the durable phase runner before relying on prose**

`complete_eval.py start --task-root <absolute> --output-root <absolute>` creates a fresh RUN_ID, runs v1 discovery and initialization itself, then deterministically runs model inventory and conversation summarization for every frozen model. It never accepts a pre-existing v1 ZIP as the normal entrypoint. It owns subcommands `start`, `status`, `accept-review`, `accept-evidence`, `seal-base`, `freeze-media`, `render`, `record-vision`, `record-human`, `decide`, `record-presentation`, `attest-presentation`, `build-outputs`, `check-source`, `validate`, and `package`. The only forward phase transitions are `discovered → evidence_initialized → review_complete → evidence_collected → base_sealed → base_verified → media_frozen → observations_complete → decisions_complete → presentation_complete → outputs_complete → source_verified → sealed`; `invalid_source_changed` is terminal.

Review/evidence workers write only invocation-owned candidate files. `record_review.py` accepts prompt-requirement and rubric-review candidates, requires exact prompt requirement classification, fills every rubric `source_basis`, closes `needs_classification`/`unreviewed`, checks prompt/rubric digests and coverage, then atomically replaces only the three canonical v1 review artifacts and runs the v1 validator. `record_evidence.py --workspace <v1-root> --candidate <worker.jsonl>` checks exact expected model × rubric pairs, frozen round/model/source identity, evidence IDs, excerpts/blobs/hashes, and duplicate/stale records, then atomically merges the batch and immediately runs the v1 validator. `complete_eval.py accept-review` and `accept-evidence` are the only canonical write paths; `seal-base` invokes the existing strict v1 package validator and packager. This closes the raw-task-to-inner-package gap without asking the weak Agent to hand-edit prompt requirements, rubric review/index, or evidence JSONL.

The runner advances `provenance/run-state.json` only after the relevant validator subreport passes, refuses skipped/reordered phases, resumes idempotently from the last valid phase, and marks the run terminal when source verification changes. Critical JSON/JSONL, audits, report, heatmap, and form input are written only by scripts. Each CLI has `--help`, explicit required arguments, stable exit codes (`0` complete action, `2` valid but unresolved, `3` validation failure, `4` source changed, `5` human input required), atomic-write behavior, and tests for skipped phases, interrupted resume, and terminal invalidation.

- [ ] **Step 3: Write the hand-holding runbook**

Give the weak third-party Agent an exact phase table containing input, literal runner command, expected exit code, generated output, decision branches, stop condition, recovery command, and required progress report for discovery, v1 evidence, media freeze, rendering, dual vision reads, human escalation, final decisions, scoring artifacts, presentation artifacts, source recheck, outer validation, and packaging.

Include explicit branches for passive bitmap, safe static SVG, unsafe SVG, active HTML/app, missing target, conflicting vision, subjective criterion, policy ambiguity, missing optional material, missing semantic material, and absent remote human.

- [ ] **Step 4: Supply templates that cannot be mistaken for facts**

Every example starts with a conspicuous statement that IDs, paths, answers, and scores are fictional. `machine-vision-prompt.md` requires exact object/location/relationship observations and forbids returning only a verdict. `remote-human-checklist.md` collects all `HUM-*` fields before allowing continuation.

- [ ] **Step 5: Mirror runtime code and enforce hash equality**

Copy this exact script closure into the new skill: `artifact_integrity.py`, `discover_task_package.py`, `initialize_bundle.py`, `collect_model_inventory.py`, `summarize_conversation.py`, `extract_excerpt.py`, `record_review.py`, `record_evidence.py`, `validate_bundle.py`, `package_bundle.py`, `verify_source.py`, `form_ready_context.py`, `initialize_form_ready.py`, `register_media.py`, `render_media.py`, `record_observation.py`, `validate_final_decisions.py`, `decision_workspace.py`, `project_form_ready_scores.py`, `presentation_workspace.py`, `render_supporting_outputs.py`, `validate_form_ready.py`, `package_form_ready.py`, and `complete_eval.py`. Copy every schema/reference/template named in the file map plus the existing v1 evidence/form/V2.1 resources these imports open. Add a test mapping every mirrored file to shared source and comparing SHA-256, and an import-closure test that recursively resolves local imports/resources from an extracted installed layout. Installed skills must not import from repository-only `shared/` paths.

- [ ] **Step 6: Run GREEN pressure tests**

Run each Task 1 scenario with a fresh child agent that receives the complete new skill. Save complete outputs in `complete-eval-results.md`. The result passes only if all checklist items pass under combined urgency, sunk cost, and authority pressure. If a new rationalization appears, update the skill to close that exact loophole and rerun the same scenario.

- [ ] **Step 7: Validate the skill and tests**

Run:

```powershell
$env:PYTHONUTF8='1'
py "$env:USERPROFILE\.codex\skills\.system\skill-creator\scripts\quick_validate.py" vibe-evals-son-complete-eval
py -m unittest tests.test_complete_eval tests.test_runtime_mirrors -v
```

- [ ] **Step 8: Commit**

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/vibe-evals-son-complete-eval vibe-evals-sonAgent-skills/vibe-evals-son-evidence-export vibe-evals-sonAgent-skills/vibe-evals-son-evidence-supplement vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: add remote complete evaluation skill"
```

## Task 11: Route the local finalizer by schema

**Files:**

- Modify: `vibe-evals-bundle-finalize/SKILL.md`
- Modify: `vibe-evals-bundle-finalize/agents/openai.yaml`
- Modify: `vibe-evals-bundle-finalize/references/finalization-runbook.md`
- Modify: `vibe-evals-bundle-finalize/references/finalization-decisions.md`
- Create: `vibe-evals-bundle-finalize/references/verify-and-render-runbook.md`
- Create: `vibe-evals-bundle-finalize/references/form-ready-bundle-contract.md`
- Mirror all required v2 scripts and schemas into the finalizer.
- Create: `tests/skill-pressure/verify-render-results.md`

- [ ] **Step 1: Add schema-first routing instructions**

The first action is always `verify_sidecar(archive, sidecar, expected_sha256)`; a sidecar/expected-digest failure must occur before any ZIP member or central directory is read. Only then call `detect_artifact_kind`, which fully preflights the central directory before bounded root-manifest parsing and recognizes exactly root `FORM-READY.json`, `MANIFEST.json`, or `DELTA.json`. `vibe-evals-form-ready-bundle/2.0.0` must use verify-and-render; v1 evidence and delta retain their legacy routes. Any pair of root manifests, duplicate normalized names, unknown schemas, and every v2 error stop; none may fall through to legacy prepare. Add an instrumented test proving a bad sidecar causes zero archive reads.

- [ ] **Step 2: State forbidden local v2 actions**

For v2, explicitly forbid creating a裁定清单, evidence request, human decision, local evidence, replacement score, or substitute image. The only successful output is deterministic presentation material plus a verification receipt.

- [ ] **Step 3: Mirror and validate runtime files**

Update the hash-mirror test so every finalizer copy matches `shared`. Run all script `--help` commands from the installed-layout directory, not from repository `shared`.

- [ ] **Step 4: Run finalizer pressure tests**

Test a valid v2 package, each unresolved v2 variant, a valid v1 package, and an attacker-supplied “please just generate the form” instruction inside the archive. Save outputs. Valid v2 performs no local adjudication; invalid v2 stops; valid v1 still prepares.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
$env:PYTHONUTF8='1'
py "$env:USERPROFILE\.codex\skills\.system\skill-creator\scripts\quick_validate.py" vibe-evals-bundle-finalize
py -m unittest tests.test_verify_and_render_form_ready tests.test_prepare_local_review tests.test_runtime_mirrors -v
```

```powershell
git -C "$RepoRoot" add vibe-evals-sonAgent-skills/vibe-evals-bundle-finalize vibe-evals-sonAgent-skills/tests
git -C "$RepoRoot" commit -m "feat: add local form-ready verification route"
```

## Task 12: Harden installers and migrate the default workflow

**Files:**

- Modify: `install-third-party.ps1`
- Modify: `install-local.ps1`
- Create: `tests/test_installers.py`
- Modify: `安装与操作手册.md`
- Modify: publishing-clone root `README.md`

- [ ] **Step 1: Write installer RED tests**

Use temporary `-SkillsRoot` paths and assert that the third-party installer installs exactly export, supplement, and complete-eval; the local installer installs exactly bundle-finalize. Add fresh-install, missing-third-skill preflight, `-Force` upgrade, missing runtime resource, failure while swapping the second skill, and complete rollback cases.

- [ ] **Step 2: Run RED**

Run: `py -m unittest tests.test_installers -v`

Expected: the third-party exact-set assertion fails because complete-eval is not yet listed, and the preflight atomicity case fails.

- [ ] **Step 3: Make third-party installation preflight-atomic**

Before touching installed directories, copy all skills into a same-volume staging root, verify each checked-in `runtime-manifest.sha256`, run the installer-bundled lightweight layout/frontmatter validator, and run every packaged Python script with `--help`. Generate runtime manifests during the release build from explicit per-skill file lists and reject unlisted or missing runtime files. If the system `quick_validate.py` exists, run it as an additional check; installation never depends on that system skill being present. Swap the complete skill set transactionally: keep timestamp-plus-GUID backups until all destinations pass post-copy hashes; if any move/copy/check fails, restore every already-moved directory and remove only invocation-owned staging. Keep `install-local.ps1` limited to finalizer and apply the same one-skill transaction.

- [ ] **Step 4: Rewrite default prompts**

The manual and repository README default remote prompt invokes `$vibe-evals-son-complete-eval` and accepts only a form-ready ZIP. The default local prompt invokes `$vibe-evals-bundle-finalize` in verify-and-render mode. Move v1 export/supplement instructions under a clearly labeled legacy/diagnostic section.

- [ ] **Step 5: Verify and commit**

Run: `py -m unittest tests.test_installers -v`

```powershell
git -C "$RepoRoot" add README.md vibe-evals-sonAgent-skills/install-*.ps1 vibe-evals-sonAgent-skills/安装与操作手册.md vibe-evals-sonAgent-skills/tests/test_installers.py
git -C "$RepoRoot" commit -m "feat: make remote complete evaluation the default workflow"
```

## Task 13: End-to-end injection matrix and publish the pilot candidate

**Files:**

- Modify: `tests/test_end_to_end.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Add the genuine v2 end-to-end test**

Build a synthetic raw two-round task package with one mechanical rubric and one visual rubric. Invoke the complete-eval runner at `start`, submit prompt/rubric review only through `accept-review`, submit worker evidence only through `accept-evidence`, and execute the full phase sequence through v1 sealing, form-ready initialization, source/target/render registration, two matching vision reads or one complete remote-human observation, complete decisions, presentation input/attestation, score projection, report/heatmap/form input, outer sealing, copy to another root, and one-call local verify-and-render. The test must never call `initialize_form_ready` with a prebuilt fixture ZIP as its entrypoint.

Assert there are two final binary scores, zero unresolved counters, a verified form, and no forbidden local-review files.

- [ ] **Step 2: Add the injection matrix**

Run the same fixture after independently injecting: missing media; source mutation after freeze; one vision read; same invocation/session IDs; vision/static conflict; machine text masquerading as human; final null; pending ADJ; unresolved/unknown/required material gap; altered inner bytes; another valid inner package; cross-workspace observation or decision; same-total score swap; wrong form input; dual/unknown manifests; outer traversal; nested traversal; duplicate/casefold/Unicode-normalized member; symlink/device/encrypted member; suspicious compression; renderer-receipt forgery; and a packaging sidecar-write failure. Every case must fail with an expected stable error code and leave no formal output ZIP/form.

- [ ] **Step 3: Strengthen and run legacy regression**

Add and run golden/exit-code coverage for legacy v1 bundle and delta packaging, filename-bound sidecars, bundle/delta safe extraction, delta merge, `prepare_local_review`, and byte-stable V2.1 Markdown rendering. Explicitly prove that invalid or unknown v2 never falls back to any v1 path.

- [ ] **Step 4: Run fresh full verification**

Run:

```powershell
py -m unittest discover -s tests -v
$env:PYTHONUTF8='1'
foreach ($Skill in @('vibe-evals-son-evidence-export','vibe-evals-son-evidence-supplement','vibe-evals-son-complete-eval','vibe-evals-bundle-finalize')) {
  py "$env:USERPROFILE\.codex\skills\.system\skill-creator\scripts\quick_validate.py" $Skill
  if ($LASTEXITCODE -ne 0) { throw "skill validation failed: $Skill" }
}
```

Expected: zero failures; only an environment-proven symlink privilege skip is permitted.

- [ ] **Step 5: Synchronize source and publish the pilot candidate**

Fetch `origin/main` and compare it with the starting remote HEAD recorded in Task 1 Step 0. Before push, require the right-hand count from `git -C $RepoRoot rev-list --left-right --count HEAD...origin/main` to be `0`; the left-hand count must equal the intentional local implementation commits. If the recorded remote moved, stop, integrate it deliberately, and rerun the full suite. Mechanically mirror the complete changed tree from `$RepoRoot/vibe-evals-sonAgent-skills` to the canonical E-drive source using an explicit file manifest, then compare every mirrored SHA-256. Run `git -C $RepoRoot diff --check`, inspect the staged diff, commit any remaining integration changes, and push the tested source so the third-party machine can install that exact candidate commit. Fetch again and require local HEAD = `origin/main` and a clean worktree. Do not create or label the immutable `v2.0.0` distribution yet.

## Task 14: Run the real PS-0819copy acceptance and publish v2.0.0

**Files:**

- Create only after the real pilot passes: `dist/vibe-evals-sonAgent-skills-v2.0.0.zip`
- Create only after the real pilot passes: `dist/vibe-evals-sonAgent-skills-v2.0.0.zip.sha256`
- Create: `docs/superpowers/verification/2026-09-11-ps-0819copy-v2-acceptance.md`

- [ ] **Step 1: Install the exact candidate on the third-party machine**

Give the operator a literal clone/update/install prompt pinned to the Task 13 commit. Require installed runtime-manifest hashes to equal the repository and require a full Codex restart. Record the actual Python, commit, install roots, and script `--help` results in the acceptance document.

- [ ] **Step 2: Re-run PS-0819copy from discovery with a new RUN_ID**

Run `$vibe-evals-son-complete-eval` against `C:\Nuyoah\xiaolie\E3\TestCode\PS\PS-0819copy`. Do not reuse the previous bundle or build script. Require the phase runner to freeze the real source, carry forward all 45 rubrics, obtain qualified dual vision or an interactive remote-human attestation for every visual/ambiguous item, close both inner adjudications and the material gap under `gap-policy.json`, create 45 binary decisions, generate presentation artifacts, verify the source twice during packaging, and emit a form-ready ZIP plus filename-bound sidecar. Preserve complete runner/validator output and remote-human checkpoints in the acceptance record.

- [ ] **Step 3: Consume the real package locally with one command**

Copy only the real outer ZIP and sidecar to an unrelated local directory and run `$vibe-evals-bundle-finalize` verify-and-render with the independently communicated expected SHA-256. Require strict outer and inner validation, 1 model, 45 rubrics, 45 scores all in `{0,1}`, zero unresolved counters, exact decision/scored/form-input digests, byte-verified Markdown, and no `人工裁定清单.md`, `evidence_requests.json`, human-decision, or local-evidence files.

- [ ] **Step 4: Treat a pilot failure as implementation evidence**

If installation, remote closure, packaging, transfer verification, or local rendering fails, add a minimal automated regression reproducing the real failure, fix the implementation, rerun the affected suite and pressure scenario, push a new candidate commit, reinstall that exact commit, and repeat the real pilot with another new RUN_ID. If the third-party machine or human operator is unavailable, report `implementation_ready_pilot_pending`; do not claim the user goal complete and do not publish v2.0.0.

- [ ] **Step 5: Build and smoke-test the immutable distribution**

Only after Step 3 passes, create `v2.0.0` without deleting old ZIPs. Include only `安装与操作手册.md`, both installers, and the four standalone skill directories. Exclude `shared`, `tests`, `docs`, `dist`, `__pycache__`, and `.pyc`. Extract it to a new temporary directory; run both installers against separate temporary skill roots; verify exact installed sets and runtime manifests; run every installed script with `--help`; compare every installed non-cache file SHA-256 with the archive source; and rerun all four quick validators from the extracted layout.

- [ ] **Step 6: Write and independently verify the release sidecar**

The sidecar contains exactly `<lowercase 64-character digest><two spaces>vibe-evals-sonAgent-skills-v2.0.0.zip` followed by one newline. Recompute with `Get-FileHash -Algorithm SHA256` and compare before publication.

- [ ] **Step 7: Synchronize, commit, push, and verify the final release**

Hash-synchronize the final source and v2 release files back to the canonical E-drive tree. Run the full suite, four skill validators, install smoke, `git diff --check`, and staged-diff inspection again; then commit `feat: ship remote-complete form-ready evaluation v2` and push. Fetch and require local HEAD = `origin/main`, a clean worktree, a downloadable v2 ZIP, and a matching sidecar. The handoff includes both commits, real acceptance RUN_ID/package SHA, final ZIP SHA, test counts, exact installation prompts, and restart requirement.

## Completion audit

Before claiming completion, inspect current state against every approved design section:

- Remote side owns media, visual observation, human escalation, adjudication, final 0/1, reports, heatmap, and form input.
- Machine vision requires audited independent double reads. Machine records cannot use the `remote_human` actor or human-claim wording; a complete named attestation is auditable, while the package explicitly does not claim cryptographic proof that a human existed.
- Inner v1 bytes and identity remain unchanged.
- No absolute third-party path survives in portable scoring metadata.
- Outer package cannot seal with any unresolved score, ADJ, human check, or material gap.
- Local v2 path accepts only ZIP + sidecar + optional expected digest and performs no adjudication.
- Legacy v1 behavior remains green.
- Four standalone skills install and run without repository-only imports.
- A fresh real `PS-0819copy` run is remotely closed and locally rendered with 45 binary scores, zero unresolved state, and zero local adjudication artifacts.
- Full tests, skill validators, package checksum, install smoke, Git push, and remote HEAD are freshly verified.

If any item lacks direct evidence, continue implementation; do not narrow the definition of done.

## Residual risks recorded during implementation

These were found while implementing Tasks 4 and 5. They do not block those tasks, but
later tasks must not claim more than the artifacts can prove.

- Render receipts are renderer-authored but not cryptographically bound to the renderer.
  `register_render` re-derives the output PNG's size, dimensions, and digest from the real
  bytes and re-checks the named renderer executable's digest and fixed argv, so a
  fabricated output cannot pass. A caller able to write the receipt file can still assert
  an execution that never happened. Tasks 7 and 8 must surface this as an explicit
  limitation instead of presenting the receipt as proof of execution, and the input SVG
  digest should be bound to a frozen source path before v2.0.0 ships.
- `candidate_crop` renders are checked as in-bounds regions of a registered full render,
  but the cropped pixels are not recomputed from the parent blob. A crop therefore records
  a declared region, not a proven one. Do not describe crops as pixel-verified evidence.
- `validate_vision_pair` approximates read independence (distinct invocation/session ids,
  distinct normalized questions and prompt digests, `independent_context`, and a check that
  the second raw response does not repeat the first answer). It cannot prove the provider
  ran two genuinely independent inferences.
- On Windows `sys.stdin.isatty()` reports true for the NUL device, so the interactive
  remote-human CLI gates on `GetConsoleMode` instead. Any future interactive gate must keep
  proving it has a real console rather than trusting `isatty()`.
- A v2 presentation slot carries one evidence list for a whole pros or cons block, while V2.1
  renders one evidence list per claim. The local renderer therefore lets each claim inherit
  the slot-level list and records `evidence_granularity.pros_cons = "slot-level"` in the
  verification receipt. Per-claim attribution needs a presentation-slot schema change; do not
  describe the current refs as per-claim evidence.
- `package_form_ready` refuses to reuse a workspace whose run state is terminally failed, so a
  source change after the freeze cannot be recovered by restoring the original bytes. A fresh
  run id and a fresh export are required, which is the intended behaviour.
