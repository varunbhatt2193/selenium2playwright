"""Snapshot the repository's GitHub traffic, and keep the history GitHub throws away.

    uv run python scripts/github_traffic.py
    uv run python scripts/github_traffic.py --out out/traffic/github-traffic.json

GitHub's traffic API is a **rolling 14-day window**: the day a visit falls out
of it, it is gone, and the Insights page cannot show it again. Anyone who wants
to know whether a launch post moved the needle three weeks later has to have
been writing the numbers down. This writes them down.

Each run merges today's window into a local JSON file, keyed by date, so days
already recorded keep their numbers and only the window's dates are refreshed.
Referrers and paths have no per-day breakdown in the API — they are a snapshot
of the same 14 days — so those are stored under the date they were fetched.

Reads only. Authentication is `gh`'s, which means no token is handled here; the
traffic endpoints need push access, so run it as the repository owner. The
output file lives under `out/`, which is gitignored: traffic numbers are the
author's business, not the repository's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_OUT = Path("out/traffic/github-traffic.json")


def gh_api(path: str) -> Any:
    """One `gh api` call, returned as parsed JSON."""
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"gh api {path} failed"
        raise RuntimeError(message)
    return json.loads(result.stdout)


def current_repo() -> str:
    """owner/name for the checkout this script is run from."""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not determine the repository")
    return result.stdout.strip()


def day_key(timestamp: str) -> str:
    """GitHub returns '2026-09-16T00:00:00Z'; the date is the useful part."""
    return timestamp.split("T")[0]


def merge(history: dict, fetched: dict, today: str) -> dict:
    """Fold one fetch into the stored history.

    Pure, so the interesting half of this script is testable without a network:
    days inside the window are refreshed (GitHub is authoritative for those),
    days outside it are left exactly as they were recorded.
    """
    merged = {
        "repo": fetched.get("repo") or history.get("repo"),
        "views": dict(history.get("views", {})),
        "clones": dict(history.get("clones", {})),
        "referrers": dict(history.get("referrers", {})),
        "paths": dict(history.get("paths", {})),
    }
    for kind in ("views", "clones"):
        for row in fetched.get(kind, {}).get(kind, []):
            merged[kind][day_key(row["timestamp"])] = {
                "count": row["count"],
                "uniques": row["uniques"],
            }
    merged["referrers"][today] = fetched.get("referrers", [])
    merged["paths"][today] = fetched.get("paths", [])
    return merged


def fetch(repo: str) -> dict:
    return {
        "repo": repo,
        "views": gh_api(f"repos/{repo}/traffic/views?per=day"),
        "clones": gh_api(f"repos/{repo}/traffic/clones?per=day"),
        "referrers": gh_api(f"repos/{repo}/traffic/popular/referrers"),
        "paths": gh_api(f"repos/{repo}/traffic/popular/paths"),
    }


def report(history: dict, today: str, days: int = 14) -> str:
    """The last `days` recorded days, plus this fetch's referrers and pages."""
    views, clones = history.get("views", {}), history.get("clones", {})
    recent = sorted(views)[-days:]
    lines = [f"{history.get('repo', '?')} — {len(views)} days recorded", ""]
    lines.append(f"{'date':<12}{'views':>7}{'uniques':>9}{'clones':>8}")
    for day in recent:
        v = views.get(day, {})
        c = clones.get(day, {})
        lines.append(
            f"{day:<12}{v.get('count', 0):>7}{v.get('uniques', 0):>9}{c.get('count', 0):>8}"
        )
    if recent:
        total_views = sum(views[d]["count"] for d in recent)
        total_uniques = sum(views[d]["uniques"] for d in recent)
        lines.append(f"{'total':<12}{total_views:>7}{total_uniques:>9}")

    for label, key in (("Where they came from", "referrers"), ("What they opened", "paths")):
        rows = history.get(key, {}).get(today, [])
        if not rows:
            continue
        lines += ["", f"{label} (last 14 days)"]
        for row in rows[:10]:
            name = row.get("referrer") or row.get("path", "")
            lines.append(f"  {name:<44}{row.get('count', 0):>6}{row.get('uniques', 0):>7}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=None, help="owner/name; default: this checkout's")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    try:
        repo = args.repo or current_repo()
        fetched = fetch(repo)
    except FileNotFoundError:
        print("gh is not installed: brew install gh", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"{exc}\n\nThe traffic API needs push access to the repository.", file=sys.stderr)
        return 1

    today = date.today().isoformat()
    history = json.loads(args.out.read_text()) if args.out.exists() else {}
    merged = merge(history, fetched, today)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")

    print(report(merged, today))
    print(f"\nhistory: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
