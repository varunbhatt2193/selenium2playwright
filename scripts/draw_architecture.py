"""Draw the architecture diagram the README and the How it works page both show.

    uv run python scripts/draw_architecture.py

Writes ui/web/public/architecture.svg. One file for both places, so the picture
cannot drift between them: the README embeds it from the repo, the page serves
it from "/". Hand-placed rather than laid out by a library, because five lanes
with two feedback loops is exactly where automatic layout turns to spaghetti.

Change the labels here, never in the SVG. Every label is a claim about the
code; the names match graph.py and suite_graph.py, and the gate order is the
order validate runs them in.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "ui/web/public/architecture.svg"

W, H = 1240, 1528
LEFT, RIGHT = 56, 1184          # lane edges; the 56px either side carries the loops
X0, X1 = 80, 1160               # content edges inside a lane

# The page's teal theme (ui/web/src/styles.css), so the image sits in the page
# without a seam and carries its own ground on GitHub's light and dark themes.
C = {
    "bg": "#0a2429", "lane": "#0e2c32", "surface": "#12343b", "surface2": "#174048",
    "line": "#2b5258", "line2": "#3d6a70",
    "text": "#eafaf9", "text2": "#b0d5d8", "text3": "#79a7ab",
    "accent": "#2dd4bf", "model": "#38bdf8", "good": "#34d399", "warn": "#fbbf24", "bad": "#fb7185",
    "label": "#f2b84b",
}
TONES = {  # tone -> (stroke, fill)
    "plain": (C["line2"], C["surface"]),
    "model": (C["model"], "#123a48"),
    "good": (C["good"], "#11392f"),
    "warn": (C["warn"], "#3a3a24"),
    "bad": (C["bad"], "#3d2a33"),
}

parts: list[str] = []


def add(svg: str) -> None:
    parts.append(svg)


def text(x: float, y: float, s: str, size: float = 15, fill: str = C["text2"], weight: int = 400,
         anchor: str = "start", extra: str = "") -> None:
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}"{extra}>{escape(s)}</text>')


def lane(y: float, h: float, number: int, title: str, caption: str, stroke: str = C["line"], caption_end: float = X1) -> None:
    add(f'<rect x="{LEFT}" y="{y}" width="{RIGHT - LEFT}" height="{h}" rx="18" fill="{C["lane"]}" stroke="{stroke}"/>')
    add(f'<circle cx="{X0 + 11}" cy="{y + 27}" r="11" fill="none" stroke="{C["label"]}"/>')
    text(X0 + 11, y + 32, str(number), 13, C["label"], 700, "middle")
    text(X0 + 32, y + 32, title.upper(), 13.5, C["label"], 700, extra=' letter-spacing="1.6"')
    text(caption_end, y + 32, caption, 14, C["text3"], anchor="end")


def box(x: float, y: float, w: float, h: float, title: str, lines: tuple[str, ...] = (), tone: str = "plain") -> None:
    stroke, fill = TONES[tone]
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
    text(x + 16, y + 29, title, 17, C["text"], 700)
    for i, line in enumerate(lines):
        text(x + 16, y + 53 + i * 21, line, 14.5)


def node(x: float, y: float, w: float, h: float, title: str, sub: str, tone: str = "plain") -> None:
    stroke, fill = TONES[tone]
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
    text(x + w / 2, y + 28, title, 16, C["text"], 700, "middle")
    text(x + w / 2, y + 50, sub, 13.5, C["text2"], anchor="middle")


def arrow(d: str, colour: str = "muted", dashed: bool = False) -> None:
    stroke = {"muted": C["text3"], "accent": C["accent"], "good": C["good"]}[colour]
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="1.8"{dash} marker-end="url(#head-{colour})"/>')


def defs() -> None:
    add("<defs>")
    for name, colour in (("muted", C["text3"]), ("accent", C["accent"]), ("good", C["good"])):
        add(f'<marker id="head-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{colour}"/></marker>')
    add("</defs>")


def draw() -> str:
    defs()
    add(f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>')
    text(LEFT, 62, "Selenium2Playwright · architecture", 28, C["text"], 800)
    text(LEFT, 92, "How a Selenium file becomes checked Playwright, and how every change is measured before it ships.",
         16, C["text3"])

    # 1 · who calls it ------------------------------------------------------------
    lane(124, 128, 1, "Who calls it", "")
    box(X0, 168, 330, 64, "Browser", ("the playground, varun-s2p.fly.dev",))
    box(830, 168, 330, 64, "CLI · CI pipeline", ("s2p convert · s2p suite · your key",))
    text(620, 206, "Visitors use the hosted page. Teams run the CLI.", 14.5, C["text3"], anchor="middle")

    # 2 · the front door ----------------------------------------------------------
    lane(284, 176, 2, "The front door", "nothing reaches a model before this", caption_end=1096)
    arrow("M 340 232 V 328")
    box(X0, 330, 320, 106, "Playground server", ("FastAPI · streams progress", "holds no model key"))
    arrow("M 400 383 H 430")
    box(432, 330, 320, 106, "Input screen", ("not Selenium, or talks to the model?", "refused before any model call"), "bad")
    arrow("M 752 383 H 782")
    box(784, 330, 280, 106, "Guard, on the API", ("visitor key · screens again", "per-visitor limits · budget"), "warn")
    # The CLI runs the graph in-process with the caller's own key: no front door.
    arrow("M 1120 232 V 490")
    text(1136, 392, "local run", 13, C["text3"], anchor="middle", extra=' transform="rotate(-90 1136 392)"')

    # 3 · the agent ---------------------------------------------------------------
    lane(492, 480, 3, "The agent", "LangGraph · Docker on Fly · s2p.fly.dev")
    arrow("M 924 436 V 490")

    add(f'<rect x="{X0}" y="540" width="{X1 - X0}" height="226" rx="14" fill="{C["bg"]}" fill-opacity="0.45" stroke="{C["line"]}"/>')
    text(X0 + 20, 566, "ONE FILE", 12.5, C["text3"], 700, extra=' letter-spacing="1.4"')
    text(X1 - 20, 566, "every attempt runs all four checks", 13.5, C["text3"], anchor="end")
    steps = [("intake", "classify it", "plain"), ("recall", "your conventions", "plain"),
             ("risk review", "may pause to ask", "warn"), ("convert", "model writes", "model"),
             ("validate", "four checks", "good"), ("critic", "model reviews", "model"),
             ("assemble", "code + report", "plain")]
    nw, first, pitch, ny = 128, 96, 153.33, 616
    for i, (title, sub, tone) in enumerate(steps):
        x = first + i * pitch
        node(x, ny, nw, 66, title, sub, tone)
        if i:
            arrow(f"M {x - pitch + nw:.1f} {ny + 33} H {x - 2:.1f}")
    vx = first + 4 * pitch + nw / 2                     # validate's centre
    gates = ("compile", "residue", "lint", "parity")    # graph.py runs them in this order
    gx = vx - (4 * 74 + 3 * 6) / 2
    for i, gate in enumerate(gates):
        add(f'<rect x="{gx + i * 80:.1f}" y="580" width="74" height="24" rx="12" fill="#11392f" stroke="{C["good"]}"/>')
        text(gx + i * 80 + 37, 597, gate, 12.5, C["text"], 600, "middle")
    add(f'<path d="M {vx:.1f} 604 V 614" stroke="{C["good"]}" stroke-width="1.4"/>')
    ix = first + nw / 2
    arrow(f"M {ix:.1f} 682 V 710")
    add(f'<rect x="{first}" y="712" width="206" height="34" rx="10" fill="{TONES["bad"][1]}" stroke="{C["bad"]}"/>')
    text(first + 103, 734, "refuse, with the reason", 13.5, C["text"], 600, "middle")
    cx, kx = first + 5 * pitch + nw / 2, first + 3 * pitch + nw / 2
    arrow(f"M {cx:.1f} 682 V 722 H {kx:.1f} V 684", "accent")
    text((cx + kx) / 2, 746, "fails: back to convert with the findings, at most 3 attempts", 13.5, C["accent"], 600, "middle")

    add(f'<rect x="{X0}" y="782" width="{X1 - X0}" height="172" rx="14" fill="{C["bg"]}" fill-opacity="0.45" stroke="{C["line"]}"/>')
    text(X0 + 20, 808, "WHOLE SUITE", 12.5, C["text3"], 700, extra=' letter-spacing="1.4"')
    text(X1 - 20, 808, "one zip in, one Playwright folder out", 13.5, C["text3"], anchor="end")
    box(96, 822, 220, 88, "plan", ("scan the folder,", "order it into waves"))
    arrow("M 316 866 H 344")
    box(346, 822, 220, 88, "next wave", ("page objects first,", "then the tests"))
    arrow("M 566 856 H 610")
    for off in (12, 6):
        add(f'<rect x="{612 + off}" y="{822 - off}" width="276" height="88" rx="12" fill="{C["surface"]}" stroke="{C["line2"]}"/>')
    box(612, 822, 276, 88, "convert each file", ("the one-file graph above,", "files in a wave in parallel"), "model")
    arrow("M 750 910 V 928 H 456 V 912", "accent")
    text(603, 948, "next wave", 12.5, C["accent"], 600, "middle")
    arrow("M 888 866 H 922")
    box(924, 822, 220, 88, "finish", ("whole tree compiled once", "parity ledger · zip"), "good")

    # 4 · what it uses ------------------------------------------------------------
    lane(1004, 152, 4, "What it uses", "")
    uses = [("Model", ("any LangChain chat model", "Claude or OpenAI"), "model"),
            ("TypeScript toolchain", ("pinned in the image", "Node · tsc · ESLint"), "good"),
            ("Postgres", ("run checkpoints", "long-term memory, pgvector"), "plain"),
            ("Redis", ("run queue · limit counters", "the daily budget"), "plain")]
    for i, (title, lines, tone) in enumerate(uses):
        x = X0 + i * 276
        box(x, 1050, 252, 90, title, lines, tone)
        arrow(f"M {x + 200} 974 V 1048")

    # 5 · evals -------------------------------------------------------------------
    lane(1188, 262, 5, "Evals", "nothing ships unmeasured", C["accent"])
    loop = [("Every run traced", ("LangSmith, every run", "thumbs up or down", "become dataset rows")),
            ("Fixed datasets", ("12 sample files", "11 hard cases", "golden Playwright answers")),
            ("Scored", ("deterministic evaluators", "calibrated LLM judge", "two judges agree")),
            ("A/B before shipping", ("same data, both arms", "repair loop · playbook", "must not regress"))]
    for i, (title, lines) in enumerate(loop):
        x = X0 + i * 280
        box(x, 1234, 240, 116, title, lines, "good" if i == 3 else "plain")
        if i:
            arrow(f"M {x - 40} 1292 H {x - 2}")
    add(f'<rect x="{X0}" y="1366" width="{X1 - X0}" height="60" rx="12" fill="#11392f" stroke="{C["good"]}" stroke-width="1.4"/>')
    text(X0 + 18, 1402, "CI gate, every push", 17, C["text"], 700)
    text(X0 + 206, 1402, "775 offline tests, no tokens · converted goldens run in a real browser · playground build",
         15, C["text2"])

    # the two loops, in the gutters -----------------------------------------------
    arrow(f"M {LEFT} 650 H 28 V 1292 H {X0 - 2}", "accent", dashed=True)
    text(18, 960, "traces · feedback", 13, C["accent"], 600, "middle", ' transform="rotate(-90 18 960)"')
    arrow(f"M {X1} 1316 H 1212 V 600 H {RIGHT + 2}", "accent", dashed=True)
    text(1226, 950, "a prompt change ships only when green", 13, C["accent"], 600, "middle",
         ' transform="rotate(90 1226 950)"')

    # legend -----------------------------------------------------------------------
    ly = 1486
    for i, (tone, label) in enumerate([("model", "model call"), ("good", "deterministic, no model"),
                                       ("warn", "pauses or limits"), ("bad", "refuses")]):
        x = LEFT + i * 230
        stroke, fill = TONES[tone]
        add(f'<rect x="{x}" y="{ly - 13}" width="18" height="18" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        text(x + 28, ly + 1, label, 14, C["text3"])
    text(RIGHT, ly + 1, "github.com/varunbhatt2193/selenium2playwright", 14, C["text3"], anchor="end")

    body = "\n".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif">\n'
            f"<title>Selenium2Playwright architecture: callers, the front door that screens and meters input, "
            f"the one-file and whole-suite graphs, what they use, and the evals loop that gates every change</title>\n"
            f"{body}\n</svg>\n")


if __name__ == "__main__":
    OUT.write_text(draw(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")
