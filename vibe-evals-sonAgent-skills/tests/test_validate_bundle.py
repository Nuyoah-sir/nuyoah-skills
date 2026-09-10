import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from validate_bundle import validate_bundle


APP_TEXT = "button.onclick = run;\n"
APP_BYTES = APP_TEXT.encode("utf-8")
APP_DIGEST = hashlib.sha256(APP_BYTES).hexdigest()
APP_BLOB = f"evidence/source-blobs/{APP_DIGEST}.txt"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def inventory_digest(items: list[dict]) -> str:
    return hashlib.sha256(
        "\n".join(f"{item['sha256']}  {item['path']}" for item in sorted(items, key=lambda value: value["path"])).encode("utf-8")
    ).hexdigest()


def identity_digest(inventory_hash: str, prompt_source: dict, rubric_sources: list[dict], model_sources: dict, record_source=None) -> str:
    identity = {
        "inventory_digest": inventory_hash,
        "prompt_source": {key: prompt_source.get(key) for key in ("path", "size", "sha256")},
        "record_source": {key: record_source.get(key) for key in ("path", "size", "sha256")} if isinstance(record_source, dict) else None,
        "rubric_sources": [{key: item.get(key) for key in ("round", "path", "size", "sha256")} for item in rubric_sources],
        "model_sources": model_sources,
    }
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def make_bundle(root: Path, status: str = "ready_for_form") -> Path:
    bundle = root / "bundle"
    rubric_value = [{"id": "R1-01", "round": 1, "criterion": "按钮可点击"}]
    rubric_text = json.dumps(rubric_value, ensure_ascii=False, indent=2)
    rubric_path = bundle / "inputs" / "rubrics" / "rubrics1.json"
    rubric_path.parent.mkdir(parents=True, exist_ok=True)
    rubric_path.write_text(rubric_text, encoding="utf-8")
    rubric_digest = hashlib.sha256(rubric_path.read_bytes()).hexdigest()
    prompt_text = "第一轮：实现按钮。\n"
    prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    source_inventory = [
        {"path": "source/prompt.md", "size": len(prompt_text.encode("utf-8")), "sha256": prompt_digest},
        {"path": "source/rubrics1.json", "size": rubric_path.stat().st_size, "sha256": rubric_digest},
        {"path": "source/model-a/app.js", "size": len(APP_BYTES), "sha256": APP_DIGEST},
        {"path": "snapshots/model-a/R1/app.js", "size": len(APP_BYTES), "sha256": APP_DIGEST},
    ]
    source_inventory_hash = inventory_digest(source_inventory)
    prompt_source = {"path": "source/prompt.md", "size": len(prompt_text.encode("utf-8")), "sha256": prompt_digest}
    rubric_sources = [{"round": 1, "path": "source/rubrics1.json", "size": rubric_path.stat().st_size, "sha256": rubric_digest}]
    model_sources = {"model-a": {"display_name": "模型 A", "final_root": "source/model-a", "round_roots": {"1": "snapshots/model-a/R1"}, "conversation_source_path": None}}
    source_digest = identity_digest(source_inventory_hash, prompt_source, rubric_sources, model_sources)
    write_json(
        bundle / "MANIFEST.json",
        {
            "schema": "vibe-evals-evidence-bundle",
            "schema_version": "1.0.0",
            "package_id": "pkg-001",
            "task": {"name": "示例题", "batch_id": "0908", "round_count": 1},
            "generator": {"agent": "test", "skill_version": "1.0.0"},
            "source_input_digest": source_digest,
            "inputs": {"prompt": "inputs/prompt.md", "rubrics": [{"round": 1, "path": "inputs/rubrics/rubrics1.json", "count": 1, "sha256": rubric_digest}]},
            "models": [{"model_id": "model-a", "display_name": "模型 A", "directory": "models/model-a", "output_status": "present"}],
            "package_status": status,
            "missing_materials": [],
            "warnings": [],
        },
    )
    (bundle / "inputs" / "prompt.md").write_bytes(prompt_text.encode("utf-8"))
    write_json(bundle / "inputs" / "rubric-index.json", {"rubrics": [{"id": "R1-01", "round": 1, "criterion": "按钮可点击", "criterion_sha256": hashlib.sha256("按钮可点击".encode("utf-8")).hexdigest(), "source_file": "inputs/rubrics/rubrics1.json", "source_index": 0, "source_basis": [{"type": "prompt", "requirement_id": "P-R1-001"}], "review": {"basis_status": "supported"}}]})
    (bundle / "inputs" / "prompt-requirements.jsonl").write_text(json.dumps({"requirement_id": "P-R1-001", "round": 1, "kind": "explicit", "text": "实现按钮", "source": {"path": "inputs/prompt.md", "line_start": 1, "line_end": 1, "quote": "第一轮：实现按钮。"}, "mapped_rubric_ids": ["R1-01"], "coverage": "mapped"}, ensure_ascii=False) + "\n", encoding="utf-8")
    model = bundle / "models" / "model-a"
    write_json(model / "model.json", {"model_id": "model-a", "display_name": "模型 A", "output_status": "present", "fallback_zero": False, "source_output_root_label": "source/model-a", "round_source_labels": {"1": "snapshots/model-a/R1"}, "conversation_source_path": None})
    write_json(model / "inventory.json", {"final": {"files": [{"path": "app.js", "size": len(APP_BYTES), "sha256": APP_DIGEST}]}, "rounds": [{"round": 1, "source_root_label": "snapshots/model-a/R1", "files": [{"path": "app.js", "size": len(APP_BYTES), "sha256": APP_DIGEST}]}]})
    model.mkdir(parents=True, exist_ok=True)
    evidence = {
        "model_id": "model-a",
        "rubric_id": "R1-01",
        "rubric_round": 1,
        "criterion": "按钮可点击",
        "disposition": "examined",
        "coverage": "complete",
        "suggested_score": 1,
        "confidence": "high",
        "reason_code": "implemented_static_only",
        "fact_summary": "点击处理器已绑定。",
        "evidence": [{"evidence_id": "EV-model-a-R1-01-001", "type": "static_line", "direction": "support", "source_state": "round_end", "round": 1, "path": "app.js", "line_start": 1, "line_end": 1, "file_sha256": APP_DIGEST, "source_blob_path": APP_BLOB, "excerpt": "button.onclick = run;", "fact": "绑定点击处理器。"}],
        "human_check_needed": False,
        "adjudication_ids": [],
        "limitations": [],
    }
    (model / "rubric-evidence.jsonl").write_text(json.dumps(evidence, ensure_ascii=False) + "\n", encoding="utf-8")
    blob_path = bundle / APP_BLOB
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(APP_BYTES)
    write_json(bundle / "review" / "pending-adjudications.json", {"items": []})
    write_json(bundle / "review" / "rubric-review.json", {"status": "reviewed", "items": [{"rubric_id": "R1-01", "finding": "supported", "reason": "直接覆盖 P-R1-001。"}]})
    write_json(bundle / "source-freeze.json", {
        "input_digest": source_digest,
        "source_inventory": source_inventory,
        "source_inventory_digest": source_inventory_hash,
        "prompt_source": prompt_source,
        "record_source": None,
        "rubric_sources": rubric_sources,
        "model_sources": model_sources,
        "snapshot_dirs": ["snapshots/model-a/R1"],
        "conversation_files": [],
    })
    return bundle


class ValidateBundleTests(unittest.TestCase):
    def test_accepts_minimal_valid_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_bundle(make_bundle(Path(tmp)), require_seal=False)
            self.assertEqual("pass", report["result"])
            self.assertEqual("ready_for_form", report["derived_status"])

    def test_rejects_absolute_and_parent_paths(self):
        for bad in ("E:/secret.txt", "../escape.txt"):
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as tmp:
                bundle = make_bundle(Path(tmp))
                manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
                manifest["inputs"]["prompt"] = bad
                write_json(bundle / "MANIFEST.json", manifest)
                report = validate_bundle(bundle, require_seal=False)
                self.assertIn("UNSAFE_PATH", {e["code"] for e in report["errors"]})

    def test_rejects_missing_model_rubric_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            (bundle / "models" / "model-a" / "rubric-evidence.jsonl").write_text("", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("EVIDENCE_COVERAGE", {e["code"] for e in report["errors"]})

    def test_rejects_invalid_score_and_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["suggested_score"] = 2
            row["evidence"] = []
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            codes = {e["code"] for e in report["errors"]}
            self.assertIn("INVALID_SCORE", codes)
            self.assertIn("SCORE_WITHOUT_EVIDENCE", codes)

    def test_verified_reason_requires_a_passed_rubric_bound_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["reason_code"] = "implemented_and_verified"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("VERIFIED_REASON_UNBACKED", {e["code"] for e in report["errors"]})

    def test_rejects_round_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"][0]["round"] = 2
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("ROUND_LEAKAGE", {e["code"] for e in report["errors"]})

    def test_pending_adjudication_derives_local_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp), "ready_for_form")
            write_json(bundle / "review" / "pending-adjudications.json", {"items": [{"adjudication_id": "ADJ-001", "status": "pending", "rubric_ids": ["R1-01"], "question": "按行为还是代码判？", "ambiguity": "两种验收解释都可能成立。", "evidence_ids": [], "applies_to_all_models": True, "recommended_policy": "按真实行为。", "alternative_policy": "按静态代码。", "impact": "影响 R1-01。", "resolution": None}]})
            evidence_path = bundle / "models/model-a/rubric-evidence.jsonl"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["adjudication_ids"] = ["ADJ-001"]
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertEqual("ready_for_local_review", report["derived_status"])
            self.assertIn("STATUS_MISMATCH", {e["code"] for e in report["errors"]})

    def test_rejects_unsafe_test_marked_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            write_json(bundle / "models" / "model-a" / "tests" / "index.json", {"runs": [{"run_id": "T-1", "status": "passed", "safety": {"model_code_treated_as_untrusted": True, "isolated": False, "network_disabled": True, "credentials_absent": True, "disposable_copy": True, "minimal_permissions": True}}]})
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("UNSAFE_TEST_EXECUTION", {e["code"] for e in report["errors"]})

    def test_skipped_run_cannot_support_a_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            write_json(bundle / "models/model-a/tests/index.json", {"runs": [{"run_id": "T-SKIPPED", "status": "skipped_no_isolation", "skip_reason": "no_isolation", "safety": {"model_code_treated_as_untrusted": True, "isolated": False, "network_disabled": False, "credentials_absent": False, "disposable_copy": False, "minimal_permissions": False}}]})
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"] = [{"evidence_id": "EV-SKIPPED", "type": "self_test_run", "direction": "support", "run_id": "T-SKIPPED", "fact": "测试未执行"}]
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("RUN_REFERENCE_MISSING", {item["code"] for item in report["errors"]})

    def test_rejects_missing_required_evidence_files(self):
        targets = ["inputs/prompt.md", "inputs/rubric-index.json", "models/model-a/model.json", "models/model-a/inventory.json"]
        for relative in targets:
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as tmp:
                bundle = make_bundle(Path(tmp))
                (bundle / relative).unlink()
                report = validate_bundle(bundle, require_seal=False)
                self.assertEqual("fail", report["result"])

    def test_rejects_unknown_evidence_type_and_missing_run_reference(self):
        for evidence in (
            {"evidence_id": "EV-X", "type": "invented", "direction": "support"},
            {"evidence_id": "EV-X", "type": "probe_run", "direction": "support", "run_id": "PR-missing", "fact": "claimed"},
        ):
            with self.subTest(kind=evidence["type"]), tempfile.TemporaryDirectory() as tmp:
                bundle = make_bundle(Path(tmp))
                path = bundle / "models/model-a/rubric-evidence.jsonl"
                row = json.loads(path.read_text(encoding="utf-8"))
                row["evidence"] = [evidence]
                path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
                report = validate_bundle(bundle, require_seal=False)
                self.assertEqual("fail", report["result"])

    def test_rejects_fallback_zero_without_human_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row.update({"disposition": "fallback_zero", "suggested_score": 0, "evidence": []})
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("FALLBACK_REQUIRES_HUMAN", {e["code"] for e in report["errors"]})

    def test_rejects_rubric_round_drift_even_when_evidence_matches_fake_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["rubric_round"] = 2
            row["evidence"][0]["round"] = 2
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("RUBRIC_ROUND_MISMATCH", {e["code"] for e in report["errors"]})

    def test_sealed_validation_requires_ready_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_bundle(make_bundle(Path(tmp)), require_seal=True)
            codes = {e["code"] for e in report["errors"]}
            self.assertTrue("FILE_MISSING" in codes or "CHECKSUM_MISSING" in codes)

    def test_rejects_unclassified_initializer_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "inputs/prompt-requirements.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row.update({"kind": "candidate", "coverage": "needs_classification", "mapped_rubric_ids": []})
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            index_path = bundle / "inputs/rubric-index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["rubrics"][0].update({"source_basis": [], "review": {"basis_status": "unreviewed"}})
            write_json(index_path, index)
            write_json(bundle / "review/rubric-review.json", {"status": "unreviewed", "items": []})
            report = validate_bundle(bundle, require_seal=False)
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("PROMPT_REQUIREMENT_UNCLASSIFIED", codes)
            self.assertIn("RUBRIC_BASIS_MISSING", codes)
            self.assertIn("RUBRIC_REVIEW_INCOMPLETE", codes)

    def test_malformed_static_line_numbers_fail_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"][0]["line_start"] = "first"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("STATIC_EVIDENCE_INCOMPLETE", {item["code"] for item in report["errors"]})

    def test_rejects_final_output_as_pre_final_round_scoring_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            manifest_path = bundle / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["task"]["round_count"] = 2
            write_json(manifest_path, manifest)
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"][0].update({"source_state": "final", "round": 2})
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("ROUND_LEAKAGE", {item["code"] for item in report["errors"]})

    def test_inventory_cannot_drift_from_source_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            inventory_path = bundle / "models/model-a/inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["final"]["files"][0]["sha256"] = "c" * 64
            inventory["rounds"][0]["files"][0]["sha256"] = "c" * 64
            write_json(inventory_path, inventory)
            evidence_path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(evidence_path.read_text(encoding="utf-8"))
            row["evidence"][0]["file_sha256"] = "c" * 64
            evidence_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("FINAL_INVENTORY_SOURCE_MISMATCH", codes)
            self.assertIn("ROUND_INVENTORY_SOURCE_MISMATCH", codes)

    def test_snapshot_diff_hashes_must_match_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"] = [{"evidence_id": "EV-DIFF", "type": "snapshot_diff", "direction": "support", "from_round": 1, "to_round": 1, "path": "app.js", "before_sha256": "c" * 64, "after_sha256": "d" * 64, "fact": "声称发生变化"}]
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("SNAPSHOT_DIFF_RANGE_INVALID", {item["code"] for item in report["errors"]})

    def test_human_observation_needs_actor_steps_time_result_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            model = bundle / "models/model-a"
            (model / "human-observations.jsonl").write_text(json.dumps({"observation_id": "OBS-1"}) + "\n", encoding="utf-8")
            path = model / "rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"] = [{"evidence_id": "EV-HUMAN", "type": "human_note", "direction": "support", "observation_id": "OBS-1", "fact": "声称人工通过"}]
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_bundle(bundle, require_seal=False)
            self.assertIn("HUMAN_OBSERVATION_INCOMPLETE", {item["code"] for item in report["errors"]})

    def test_rejects_tampered_source_freeze_digest_and_prompt_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            freeze_path = bundle / "source-freeze.json"
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
            freeze["source_inventory"][2]["sha256"] = "c" * 64
            write_json(freeze_path, freeze)
            codes = {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]}
            self.assertIn("SOURCE_DIGEST_MISMATCH", codes)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            (bundle / "inputs/prompt.md").write_text("伪造题面\n", encoding="utf-8")
            codes = {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]}
            self.assertIn("PROMPT_SOURCE_MISMATCH", codes)

    def test_rejects_model_and_round_source_rebinding(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            meta_path = bundle / "models/model-a/model.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["source_output_root_label"] = "source/other"
            write_json(meta_path, meta)
            codes = {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]}
            self.assertIn("MODEL_SOURCE_BINDING_MISMATCH", codes)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            inventory_path = bundle / "models/model-a/inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["rounds"][0]["source_root_label"] = "snapshots/other/R9"
            write_json(inventory_path, inventory)
            codes = {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]}
            self.assertIn("ROUND_SOURCE_BINDING_MISMATCH", codes)

    def test_rejects_fake_prompt_mapping_and_empty_rubric_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            requirement_path = bundle / "inputs/prompt-requirements.jsonl"
            requirement = json.loads(requirement_path.read_text(encoding="utf-8"))
            requirement.update({"text": "完全不存在的要求", "source": {"path": "inputs/prompt.md", "line_start": 1, "line_end": 999}})
            requirement_path.write_text(json.dumps(requirement, ensure_ascii=False) + "\n", encoding="utf-8")
            write_json(bundle / "review/rubric-review.json", {"status": "reviewed", "items": []})
            codes = {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]}
            self.assertIn("PROMPT_SOURCE_RANGE_INVALID", codes)
            self.assertIn("RUBRIC_REVIEW_COVERAGE", codes)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            requirement_path = bundle / "inputs/prompt-requirements.jsonl"
            requirement = json.loads(requirement_path.read_text(encoding="utf-8"))
            requirement["text"] = "完全不存在的要求"
            requirement_path.write_text(json.dumps(requirement, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertIn("PROMPT_TEXT_MISMATCH", {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]})

    def test_rejects_cross_state_score_matrix_and_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row.update({"coverage": "missing", "reason_code": "implemented_and_verified", "fact_summary": ""})
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            codes = {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]}
            self.assertIn("EVIDENCE_STATE_CONFLICT", codes)
            self.assertIn("FACT_SUMMARY_MISSING", codes)

    def test_rejects_unknown_static_state_and_future_snapshot_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"][0]["source_state"] = "bogus"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertIn("STATIC_SOURCE_STATE_INVALID", {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]})

        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["evidence"] = [{"evidence_id": "EV-DIFF", "type": "snapshot_diff", "direction": "support", "from_round": 2, "to_round": 1, "path": "app.js", "before_sha256": APP_DIGEST, "after_sha256": APP_DIGEST, "fact": "倒序快照"}]
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertIn("SNAPSHOT_DIFF_RANGE_INVALID", {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]})

    def test_rejects_round_count_downgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            manifest_path = bundle / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["task"]["round_count"] = 2
            write_json(manifest_path, manifest)
            self.assertIn("ROUND_COUNT_MISMATCH", {item["code"] for item in validate_bundle(bundle, require_seal=False)["errors"]})


if __name__ == "__main__":
    unittest.main()
