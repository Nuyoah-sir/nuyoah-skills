import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from collect_model_inventory import collect_model_inventory
from extract_excerpt import extract_excerpt
from summarize_conversation import summarize_conversation


class ModelInventoryTests(unittest.TestCase):
    def test_collects_final_and_rounds_in_stable_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "final"
            round_one = root / "R1"
            final.mkdir()
            round_one.mkdir()
            (final / "z.txt").write_text("z", encoding="utf-8")
            (final / "a.txt").write_text("alpha", encoding="utf-8")
            (round_one / "old.txt").write_text("old", encoding="utf-8")
            output = root / "bundle" / "inventory.json"

            result = collect_model_inventory(final, [(1, round_one)], output)

            self.assertEqual(["a.txt", "z.txt"], [item["path"] for item in result["final"]["files"]])
            self.assertEqual([1], [item["round"] for item in result["rounds"]])
            self.assertEqual(hashlib.sha256(b"alpha").hexdigest(), result["final"]["files"][0]["sha256"])
            self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))

    def test_rejects_output_overwrite_duplicate_round_and_output_inside_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "final"
            final.mkdir()
            (final / "a.txt").write_text("a", encoding="utf-8")
            existing = root / "inventory.json"
            existing.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                collect_model_inventory(final, [], existing)
            self.assertEqual("keep", existing.read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                collect_model_inventory(final, [(1, final), (1, final)], root / "other.json")
            with self.assertRaises(ValueError):
                collect_model_inventory(final, [], final / "inventory.json")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_rejects_source_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "final"
            final.mkdir()
            target = root / "outside.txt"
            target.write_text("secret", encoding="utf-8")
            try:
                os.symlink(target, final / "link.txt")
            except OSError as exc:
                self.skipTest(f"cannot create symlink: {exc}")

            with self.assertRaises(ValueError):
                collect_model_inventory(final, [], root / "inventory.json")


class ExcerptTests(unittest.TestCase):
    def test_extracts_exact_contiguous_lines_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            file_path = source / "src" / "app.py"
            file_path.parent.mkdir()
            raw = "one\r\ntwo\r\nthree\r\nfour\r\n"
            file_path.write_bytes(raw.encode("utf-8"))
            output = root / "evidence" / "excerpt.json"

            result = extract_excerpt(source, "src/app.py", 2, 3, output)

            self.assertEqual("src/app.py", result["path"])
            self.assertEqual(2, result["line_start"])
            self.assertEqual(3, result["line_end"])
            self.assertEqual("two\nthree", result["excerpt"])
            self.assertEqual(hashlib.sha256(raw.encode("utf-8")).hexdigest(), result["file_sha256"])
            self.assertEqual(hashlib.sha256(b"two\nthree").hexdigest(), result["excerpt_sha256"])

    def test_rejects_escape_bounds_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")

            with self.assertRaises(ValueError):
                extract_excerpt(source, "../outside.txt", 1, 1, root / "one.json")
            with self.assertRaises(ValueError):
                extract_excerpt(source, "a.txt", 0, 1, root / "two.json")
            with self.assertRaises(ValueError):
                extract_excerpt(source, "a.txt", 1, 3, root / "three.json")
            output = root / "exists.json"
            output.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                extract_excerpt(source, "a.txt", 1, 1, output)
            self.assertEqual("keep", output.read_text(encoding="utf-8"))


class ConversationSummaryTests(unittest.TestCase):
    def test_stably_flattens_codebuddy_messages_without_guessing_debug(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "conversation.json"
            source.write_text(
                json.dumps(
                    {
                        "schema": "codebuddy.conversation",
                        "data": {
                            "conversations": [
                                {
                                    "id": "c1",
                                    "requests": [
                                        {
                                            "id": "req1",
                                            "messages": [
                                                {
                                                    "id": "outer-user",
                                                    "role": "user",
                                                    "message": json.dumps({"role": "user", "content": [{"type": "text", "text": "build it"}]}),
                                                },
                                                {
                                                    "id": "outer-assistant",
                                                    "role": "assistant",
                                                    "message": json.dumps(
                                                        {
                                                            "role": "assistant",
                                                            "content": [
                                                                {"type": "text", "text": "I will inspect."},
                                                                {"type": "tool-call", "toolName": "list_dir", "args": {"path": "."}},
                                                            ],
                                                        }
                                                    ),
                                                },
                                            ],
                                        },
                                        {
                                            "id": "req2",
                                            "messages": [
                                                {"role": "tool", "message": "not-json tool output"},
                                            ],
                                        },
                                    ],
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "conversation-summary.json"

            result = summarize_conversation(source, output)

            events = result["key_events"]
            self.assertEqual([0, 1, 2], [event["flat_msg_index"] for event in events])
            self.assertEqual([1, 1, 2], [event["round"] for event in events])
            self.assertEqual(["MSG-000000", "MSG-000001", "MSG-000002"], [event["msg_ref"] for event in events])
            self.assertEqual("assistant_tool_call", events[1]["event_type"])
            self.assertIn("I will inspect.", events[1]["excerpt"])
            self.assertIn("list_dir", events[1]["excerpt"])
            self.assertEqual(3, result["structural_counts"]["message_count"])
            self.assertIsNone(result["statistics"]["debug_count"]["value"])
            self.assertIn("semantic", result["statistics"]["debug_count"]["unavailable_reason"])

    def test_rejects_bad_shape_and_output_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad.json"
            bad.write_text('{"data":{"conversations":{}}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                summarize_conversation(bad, root / "summary.json")

            valid = root / "valid.json"
            valid.write_text('{"data":{"conversations":[]}}', encoding="utf-8")
            output = root / "exists.json"
            output.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                summarize_conversation(valid, output)
            self.assertEqual("keep", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
