"""Gap T9: a string in a list field is a usable reply, not a parse failure."""

import unittest

from pydantic import ValidationError

from selenium2playwright.schemas import ConversionResult


class ConversionResultToleranceTests(unittest.TestCase):
    def test_single_string_notes_become_a_list(self):
        result = ConversionResult(code="export {};\n", notes="Dialog handling: Playwright auto-dismisses dialogs.")
        self.assertEqual(result.notes, ["Dialog handling: Playwright auto-dismisses dialogs."])

    def test_item_tagged_lines_become_items_and_blank_lines_are_dropped(self):
        raw = "\n<item>Rule 4/5: Builder replaced by fixtures.</item>\n\n<item>Rule 20: same POM API.</item>\n"
        result = ConversionResult(code="export {};\n", notes=raw, todos="TODO(review): check baseURL")
        self.assertEqual(result.notes, ["Rule 4/5: Builder replaced by fixtures.", "Rule 20: same POM API."])
        self.assertEqual(result.todos, ["TODO(review): check baseURL"])

    def test_lists_and_defaults_are_untouched(self):
        result = ConversionResult(code="export {};\n", notes=["a", "b"])
        self.assertEqual((result.notes, result.todos), (["a", "b"], []))
        self.assertEqual(ConversionResult(code="x", notes="").notes, [])

    def test_other_wrong_shapes_still_fail(self):
        for bad in (7, {"note": "x"}, [1, 2]):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                ConversionResult(code="x", notes=bad)


if __name__ == "__main__":
    unittest.main()
