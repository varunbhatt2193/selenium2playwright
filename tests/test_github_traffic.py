"""The traffic snapshot exists to outlive GitHub's 14-day window.

So the one thing worth testing is that a day already recorded survives a later
fetch that no longer mentions it. Everything else in the script is `gh api` and
printing; the merge is the part that can silently lose history.
"""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_traffic.py"
spec = importlib.util.spec_from_file_location("github_traffic", SCRIPT)
traffic = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(traffic)


def window(kind: str, rows: list[tuple[str, int, int]]) -> dict:
    """The shape GitHub returns: a list under a key named after itself."""
    return {kind: [{"timestamp": f"{d}T00:00:00Z", "count": c, "uniques": u} for d, c, u in rows]}


class MergeTests(unittest.TestCase):
    def fetched(self, views, clones=(), referrers=(), paths=()):
        return {
            "repo": "owner/repo",
            "views": window("views", views),
            "clones": window("clones", clones),
            "referrers": list(referrers),
            "paths": list(paths),
        }

    def test_a_day_that_left_the_window_is_still_there(self):
        first = traffic.merge({}, self.fetched([("2026-09-01", 9, 4)]), "2026-09-01")
        later = traffic.merge(first, self.fetched([("2026-09-20", 3, 2)]), "2026-09-20")
        self.assertEqual(later["views"]["2026-09-01"], {"count": 9, "uniques": 4})
        self.assertEqual(later["views"]["2026-09-20"], {"count": 3, "uniques": 2})

    def test_a_day_inside_the_window_is_refreshed_not_added_up(self):
        morning = traffic.merge({}, self.fetched([("2026-09-16", 2, 1)]), "2026-09-16")
        evening = traffic.merge(morning, self.fetched([("2026-09-16", 7, 5)]), "2026-09-16")
        self.assertEqual(evening["views"]["2026-09-16"], {"count": 7, "uniques": 5})

    def test_clones_are_kept_alongside_views(self):
        merged = traffic.merge(
            {}, self.fetched([("2026-09-16", 4, 3)], clones=[("2026-09-16", 2, 2)]), "2026-09-16"
        )
        self.assertEqual(merged["clones"]["2026-09-16"], {"count": 2, "uniques": 2})

    def test_referrers_are_stored_per_fetch_because_the_api_has_no_per_day_breakdown(self):
        one = [{"referrer": "linkedin.com", "count": 12, "uniques": 9}]
        two = [{"referrer": "google.com", "count": 3, "uniques": 3}]
        first = traffic.merge({}, self.fetched([], referrers=one), "2026-09-16")
        later = traffic.merge(first, self.fetched([], referrers=two), "2026-09-30")
        self.assertEqual(later["referrers"]["2026-09-16"], one)
        self.assertEqual(later["referrers"]["2026-09-30"], two)

    def test_the_report_names_every_recorded_day_and_totals_them(self):
        history = traffic.merge(
            {}, self.fetched([("2026-09-15", 5, 4), ("2026-09-16", 6, 5)]), "2026-09-16"
        )
        text = traffic.report(history, "2026-09-16")
        self.assertIn("2026-09-15", text)
        self.assertIn("2026-09-16", text)
        self.assertIn("11", text)  # 5 + 6 views
        self.assertIn("9", text)  # 4 + 5 uniques


if __name__ == "__main__":
    unittest.main()
