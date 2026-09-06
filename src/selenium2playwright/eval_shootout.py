"""Draw one picture across actors: one attempt vs. reflection, per actor, one shared critic.

Reads the comparison.json receipts written by scripts/run_reflection_ab.py and
writes a dependency-free SVG (renders on GitHub) plus a markdown table. It
never calls a model and never changes a number; every value is copied from
the receipts. Receipts whose arms are not comparable (see eval_compare) are
refused, so a broken run can never be drawn as a result.
CLI wrapper: scripts/render_actor_shootout.py.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

ARMS = (("one_attempt", "1 attempt"), ("reflective", "reflection"))
COLORS = {"one_attempt": "#b9c4d0", "reflective": "#2f6fdb"}


def load(spec: str) -> tuple[str, dict]:
    label, path = spec.split("=", 1)
    comparison = json.loads(Path(path).read_text())
    if not comparison.get("comparable"):
        raise ValueError(f"{path}: arms are not comparable ({comparison.get('issues')}); not drawing it")
    return label, comparison["arms"]


def count(measure: dict) -> str:
    return "n/a" if measure["total"] is None else f"{int(Decimal(measure['total'])):,}"


def money(measure: dict) -> tuple[str, float, bool]:
    """Label, bar height, and whether the number is a full total.

    A partial cost (LangSmith reported tokens for only some rows) is drawn as
    a dashed outline at the known subtotal and labelled with the row count,
    so it is never mistaken for a complete figure."""
    if measure["total"] is not None:
        return f"${Decimal(measure['total']):.2f}", float(measure["total"]), True
    return f"≥${Decimal(measure['known_subtotal']):.2f} ({measure['known_rows']} rows)", float(measure["known_subtotal"]), False


def panel(x: int, y: int, w: int, h: int, title: str, actors: list, value, ymax: float, fmt, complete=lambda a: True) -> list[str]:
    """One grouped bar chart: actors along the x axis, the two arms side by side."""
    out = [f'<text x="{x}" y="{y - 14}" class="title">{title}</text>',
           f'<line x1="{x}" y1="{y + h}" x2="{x + w}" y2="{y + h}" class="axis"/>']
    group = w / len(actors)
    bar = group * 0.32
    for i, (label, arms) in enumerate(actors):
        gx = x + i * group
        out.append(f'<text x="{gx + group / 2}" y="{y + h + 20}" class="label">{label}</text>')
        for j, (arm, _) in enumerate(ARMS):
            v = value(arms[arm])
            bh = 0 if v is None else h * v / ymax
            bx = gx + group / 2 - bar + j * bar
            style = (f'fill="{COLORS[arm]}"' if complete(arms[arm])
                     else f'fill="none" stroke="{COLORS[arm]}" stroke-width="2" stroke-dasharray="5,4"')
            out.append(f'<rect x="{bx:.1f}" y="{y + h - bh:.1f}" width="{bar - 4:.1f}" height="{bh:.1f}" {style} rx="3"/>')
            out.append(f'<text x="{bx + (bar - 4) / 2:.1f}" y="{y + h - bh - 5:.1f}" class="value">{fmt(arms[arm])}</text>')
    return out


def render_svg(actors: list) -> str:
    n = 13.5  # headroom so a 12/12 bar and its label sit below the panel title
    panels = [
        ("Fully passed graph report (of 12)", lambda a: a["passes"]["graph_report_passed"], n,
         lambda a: str(a["passes"]["graph_report_passed"])),
        ("All four static gates passed (of 12)", lambda a: a["passes"]["all_static_passed"], n,
         lambda a: str(a["passes"]["all_static_passed"])),
        ("Rows that used a repair lap (of 12)", lambda a: a["with_draft"]["repaired_rows"], n,
         lambda a: str(a["with_draft"]["repaired_rows"])),
        ("LangSmith cost, USD (Opus critic in every bar; dashed = partial total)",
         lambda a: money(a["langsmith_root_cost_usd"])[1],
         max(0.8, max(money(a["langsmith_root_cost_usd"])[1] for _, arms in actors for a in arms.values()) * 1.3),
         lambda a: money(a["langsmith_root_cost_usd"])[0], lambda a: money(a["langsmith_root_cost_usd"])[2]),
    ]
    width, pw, ph, top = 1000, 440, 190, 90
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{top + 2 * (ph + 80) + 10}" viewBox="0 0 {width} {top + 2 * (ph + 80) + 10}">',
             '<style>text{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;fill:#222}.h{font-size:20px;font-weight:600}'
             '.sub{font-size:13px;fill:#555}.title{font-size:14px;font-weight:600}.label{font-size:13px;text-anchor:middle}'
             '.value{font-size:12px;text-anchor:middle;fill:#333}.axis{stroke:#999}.legend{font-size:13px}</style>',
             f'<rect width="{width}" height="100%" fill="#fff"/>',
             '<text x="30" y="34" class="h">Does the repair loop earn its extra calls?</text>',
             '<text x="30" y="56" class="sub">Same 12 pinned files, same Opus critic, one actor per group. Grey = one conversion attempt. Blue = up to three attempts (draft + two repairs).</text>',
             f'<rect x="{width - 330}" y="22" width="14" height="14" fill="{COLORS["one_attempt"]}" rx="2"/><text x="{width - 310}" y="34" class="legend">1 attempt</text>',
             f'<rect x="{width - 220}" y="22" width="14" height="14" fill="{COLORS["reflective"]}" rx="2"/><text x="{width - 200}" y="34" class="legend">reflection (≤3)</text>']
    for k, (title, value, ymax, fmt, *complete) in enumerate(panels):
        x, y = 40 + (k % 2) * (pw + 60), top + (k // 2) * (ph + 80)
        lines += panel(x, y, pw, ph, title, actors, value, ymax, fmt, *complete)
    lines.append("</svg>")
    return "\n".join(lines)


def render_table(actors: list) -> str:
    head = ["| Metric |" + "".join(f" {label}, {arm} |" for label, _ in actors for _, arm in ARMS),
            "| --- |" + " --- |" * (2 * len(actors))]
    rows = [
        ("Fully passed graph report", lambda a: f"{a['passes']['graph_report_passed']}/{a['scheduled']}"),
        ("All four static gates", lambda a: f"{a['passes']['all_static_passed']}/{a['scheduled']}"),
        ("Rows that used a repair lap", lambda a: str(a["with_draft"]["repaired_rows"])),
        ("Actor model calls", lambda a: str(a["actor_calls"]["total"])),
        ("Actor tokens", lambda a: count(a["actor_total_tokens"])),
        ("Critic tokens (Opus)", lambda a: count(a["critic_total_tokens"])),
        ("Wall-clock (sum)", lambda a: f"{float(a['target_seconds']['total']):.0f} s" if a["target_seconds"]["total"] is not None else "n/a"),
        ("LangSmith cost", lambda a: money(a["langsmith_root_cost_usd"])[0]),
        ("LangSmith experiment", lambda a: f"[open]({a['experiment']['url']})"),
    ]
    return "\n".join(head + [f"| {name} |" + "".join(f" {f(arms[arm])} |" for _, arms in actors for arm, _ in ARMS) + "" for name, f in rows]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("specs", nargs="+", metavar="LABEL=comparison.json")
    parser.add_argument("--svg", type=Path, required=True)
    parser.add_argument("--md", type=Path, required=True)
    args = parser.parse_args(argv)
    actors = [load(spec) for spec in args.specs]
    critics = {arms[arm].get("critic_model", arms[arm]["model"]) for _, arms in actors for arm, _ in ARMS}
    if len(critics) != 1:
        raise ValueError(f"Every arm must share one critic to be drawn together; found {sorted(critics)}")
    args.svg.write_text(render_svg(actors), encoding="utf-8")
    args.md.write_text(render_table(actors), encoding="utf-8")
    print(f"wrote {args.svg} and {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
