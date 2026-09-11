import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from v21_labels import LABEL_SECTIONS, LABEL_SOURCE, allowed_labels, build_label_document, load_label_document


class V21LabelLibraryTests(unittest.TestCase):
    def test_checked_in_label_library_is_exactly_reproducible_from_the_markdown(self):
        rebuilt = build_label_document(ROOT / LABEL_SOURCE)
        self.assertEqual(rebuilt, load_label_document(ROOT / "shared"))

    def test_label_library_is_non_trivial_and_has_no_duplicate_tags(self):
        allowed = allowed_labels(ROOT / "shared")
        self.assertEqual(set(LABEL_SECTIONS), set(allowed))
        for section, tags in allowed.items():
            with self.subTest(section=section):
                self.assertGreaterEqual(len(tags), 10)
        flattened = [tag for tags in allowed.values() for tag in tags]
        self.assertEqual(len(flattened), len(set(flattened)), "a tag must not appear in two sections")

    def test_every_legal_tag_appears_verbatim_in_the_source_markdown(self):
        markdown = (ROOT / LABEL_SOURCE).read_text(encoding="utf-8")
        allowed = allowed_labels(ROOT / "shared")
        missing = sorted(tag for tags in allowed.values() for tag in tags if tag not in markdown)
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
