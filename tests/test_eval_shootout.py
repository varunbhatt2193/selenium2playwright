"""The actor shootout diagram is drawn only from comparable receipts that share one critic."""

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from selenium2playwright import eval_shootout

ROOT = Path(__file__).resolve().parents[1]
HAIKU, OPUS = ROOT / "docs/phase-6.5-haiku-comparison.json", ROOT / "docs/phase-6.3-comparison.json"


class ShootoutTests(unittest.TestCase):
    def test_renders_svg_and_table_from_committed_receipts(self):
        with TemporaryDirectory() as temporary:
            svg, md = Path(temporary) / "s.svg", Path(temporary) / "t.md"
            eval_shootout.main([f"Haiku={HAIKU}", f"Opus={OPUS}", "--svg", str(svg), "--md", str(md)])
            drawing, table = svg.read_text(), md.read_text()
        self.assertTrue(drawing.startswith("<svg "))
        for label in ("Haiku", "Opus", "Fully passed graph report", ">9<", ">11<", "$0.69", "≥$0.54 (11 rows)"):
            self.assertIn(label, drawing)
        self.assertIn('stroke-dasharray', drawing)  # the partial Opus cost is dashed, not solid
        self.assertIn("| Fully passed graph report | 2/12 | 9/12 | 6/12 | 11/12 |", table)
        self.assertIn("| Rows that used a repair lap | 0 | 8 | 0 | 2 |", table)

    def test_refuses_uncomparable_receipts_and_mixed_critics(self):
        broken = json.loads(HAIKU.read_text())
        broken["comparable"], broken["issues"] = False, ["arm B has provider errors"]
        mixed = json.loads(HAIKU.read_text())
        mixed["arms"]["reflective"]["critic_model"] = "anthropic:claude-sonnet-5"
        with TemporaryDirectory() as temporary:
            for name, receipt, message in (("broken.json", broken, "not comparable"), ("mixed.json", mixed, "share one critic")):
                path = Path(temporary) / name
                path.write_text(json.dumps(receipt))
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                    eval_shootout.main([f"X={path}", f"Opus={OPUS}", "--svg", f"{temporary}/s.svg", "--md", f"{temporary}/t.md"])


if __name__ == "__main__":
    unittest.main()
