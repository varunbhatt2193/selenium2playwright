"""Draw the architecture diagram the README and the How it works page both show.

    uv run python scripts/draw_architecture.py

Writes ui/web/public/architecture.svg. One file for both places, so the picture
cannot drift between them: the README embeds it from the repo, the page serves
it from "/". Hand-placed rather than laid out by a library, because boundaries
nested three deep with labelled connections are where automatic layout fails.

It is a deployment view: what runs where, what each part holds, and what talks
to what over which wire. The step-by-step loop inside a graph is the other
drawing on How it works. Change the labels here, never in the SVG; each one is
a claim about the code, deploy/fly/*.toml or .github/workflows.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "ui/web/public/architecture.svg"

W, H = 1280, 1052

# The page's teal theme (ui/web/src/styles.css), so the image sits in the page
# without a seam and carries its own ground on GitHub's light and dark themes.
C = {
    "bg": "#0a2429", "zone": "#0d2a30", "box": "#12343b", "inner": "#174048",
    "line": "#2b5258", "line2": "#3d6a70",
    "text": "#eafaf9", "text2": "#b0d5d8", "text3": "#79a7ab",
    "accent": "#2dd4bf", "model": "#38bdf8", "good": "#34d399", "warn": "#fbbf24", "bad": "#fb7185",
    "label": "#f2b84b",
}
TONES = {  # tone -> (stroke, fill)
    "plain": (C["line2"], C["inner"]),
    "model": (C["model"], "#123a48"),
    "good": (C["good"], "#11392f"),
    "warn": (C["warn"], "#37381f"),
    "bad": (C["bad"], "#3d2a33"),
}
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

parts: list[str] = []


def add(svg: str) -> None:
    parts.append(svg)


def text(x: float, y: float, s: str, size: float = 14, fill: str = C["text2"], weight: int = 400,
         anchor: str = "start", mono: bool = False, spacing: float = 0) -> None:
    family = f' font-family="{MONO}"' if mono else ""
    track = f' letter-spacing="{spacing}"' if spacing else ""
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}"{family}{track}>{escape(s)}</text>')


def zone(x: float, y: float, w: float, h: float, title: str, caption: str = "", dashed: bool = True,
         stroke: str = C["line2"]) -> None:
    """A boundary: a machine, a cloud, an organisation. Dashed, because it holds things rather than doing them."""
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{C["zone"]}" stroke="{stroke}" stroke-width="1.5"{dash}/>')
    text(x + 18, y + 28, title.upper(), 13, C["label"], 700, spacing=1.5)
    if caption:
        text(x + w - 18, y + 28, caption, 13, C["text3"], anchor="end")


def container(x: float, y: float, w: float, h: float, name: str, title: str, caption: str = "") -> None:
    """A deployed unit: one Fly app, one image."""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{C["box"]}" stroke="{C["accent"]}" stroke-width="1.6"/>')
    text(x + 16, y + 24, name, 12.5, C["accent"], 600, mono=True)
    text(x + 16, y + 46, title, 17, C["text"], 700)
    if caption:
        text(x + w - 16, y + 24, caption, 12, C["text3"], anchor="end", mono=True)


def part(x: float, y: float, w: float, h: float, title: str, lines: tuple[str, ...] = (), tone: str = "plain") -> None:
    """A component inside a container, or a service inside a zone."""
    stroke, fill = TONES[tone]
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')
    text(x + 14, y + 25, title, 15.5, C["text"], 700)
    for i, line in enumerate(lines):
        text(x + 14, y + 46 + i * 19, line, 13.5)


def cylinder(x: float, y: float, w: float, h: float, stroke: str = C["text2"]) -> None:
    ry = h * 0.16
    add(f'<path d="M {x} {y + ry} V {y + h - ry} A {w / 2} {ry} 0 0 0 {x + w} {y + h - ry} V {y + ry}" '
        f'fill="{C["inner"]}" stroke="{stroke}" stroke-width="1.5"/>')
    add(f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" fill="{C["box"]}" stroke="{stroke}" stroke-width="1.5"/>')


def store(x: float, y: float, w: float, name: str, title: str, lines: tuple[str, ...]) -> None:
    cylinder(x, y + 4, 34, 46)
    text(x + 50, y + 14, name, 12, C["accent"], 600, mono=True)
    text(x + 50, y + 34, title, 15.5, C["text"], 700)
    for i, line in enumerate(lines):
        text(x + 50, y + 54 + i * 19, line, 13.5)


def wire(d: str, label: str = "", at: tuple[float, float] | None = None, colour: str = "muted",
         both: bool = False, dashed: bool = False) -> None:
    """A connection. The label says what crosses it, pinned on a chip so it reads over any line."""
    stroke = {"muted": C["text3"], "accent": C["accent"], "model": C["model"], "good": C["good"]}[colour]
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    start = f' marker-start="url(#head-{colour})"' if both else ""
    add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="1.7"{dash}{start} marker-end="url(#head-{colour})"/>')
    if label and at:
        chip(at[0], at[1], label, stroke)


def chip(cx: float, cy: float, label: str, stroke: str = C["text3"]) -> None:
    lines = label.split("\n")
    w = max(len(line) for line in lines) * 7.6 + 18
    h = 8 + 15 * len(lines)
    add(f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h}" rx="{min(h / 2, 9)}" '
        f'fill="{C["bg"]}" stroke="{stroke}" stroke-opacity="0.7"/>')
    for i, line in enumerate(lines):
        text(cx, cy - h / 2 + 16 + i * 15, line, 12, C["text2"], 600, "middle", mono=True)


def glyph_browser(x: float, y: float) -> None:
    add(f'<rect x="{x}" y="{y}" width="30" height="24" rx="4" fill="none" stroke="{C["text2"]}" stroke-width="1.5"/>')
    add(f'<path d="M {x} {y + 7} H {x + 30}" stroke="{C["text2"]}" stroke-width="1.5"/>')
    for i in range(3):
        add(f'<circle cx="{x + 5 + i * 5}" cy="{y + 3.6}" r="1.3" fill="{C["text2"]}"/>')


def glyph_terminal(x: float, y: float) -> None:
    add(f'<rect x="{x}" y="{y}" width="30" height="24" rx="4" fill="none" stroke="{C["text2"]}" stroke-width="1.5"/>')
    add(f'<path d="M {x + 7} {y + 8} L {x + 12} {y + 12} L {x + 7} {y + 16} M {x + 15} {y + 17} H {x + 23}" '
        f'fill="none" stroke="{C["text2"]}" stroke-width="1.5"/>')


def glyph_cloud(x: float, y: float) -> None:
    add(f'<path d="M {x + 8} {y + 22} H {x + 26} A 6 6 0 0 0 {x + 25} {y + 10} A 9 9 0 0 0 {x + 8} {y + 11} '
        f'A 5.5 5.5 0 0 0 {x + 8} {y + 22} Z" fill="none" stroke="{C["text2"]}" stroke-width="1.5"/>')


def defs() -> None:
    add("<defs>")
    for name, colour in (("muted", C["text3"]), ("accent", C["accent"]), ("model", C["model"]), ("good", C["good"])):
        add(f'<marker id="head-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{colour}"/></marker>')
    add("</defs>")


def draw() -> str:
    defs()
    add(f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>')
    text(32, 52, "Selenium2Playwright · architecture", 27, C["text"], 800)
    text(32, 80, "What runs where, what each part holds, and what crosses each wire. Evals and CI decide what gets deployed.",
         15, C["text3"])

    # --- the visitor ------------------------------------------------------------
    zone(32, 110, 180, 500, "Visitor")
    glyph_browser(50, 262)
    text(92, 280, "Browser", 16, C["text"], 700)
    add(f'<rect x="46" y="250" width="152" height="96" rx="10" fill="none" stroke="{C["line2"]}"/>')
    text(62, 312, "any device,", 13.5)
    text(62, 331, "no signup", 13.5)

    # --- Fly.io -------------------------------------------------------------------
    zone(236, 110, 708, 500, "Fly.io · region iad", "each app its own image", stroke=C["accent"])

    container(256, 150, 196, 318, "varun-s2p", "Playground")
    part(270, 212, 168, 58, "React SPA", ("built by Vite, static",))
    part(270, 280, 168, 58, "FastAPI server", ("the page's only backend",))
    part(270, 348, 168, 58, "Input screen", ("refuses, no model call",), "bad")
    text(270, 446, "holds no model key", 13, C["text3"])

    container(508, 150, 418, 318, "s2p", "LangGraph API server", "langgraph-api 0.14")
    part(524, 212, 186, 76, "Guard", ("auth · visitor key", "screen · limits · budget"), "warn")
    part(726, 212, 186, 76, "Convert graph", ("convert → 4 checks →", "critic, at most 3 laps"), "model")
    part(524, 300, 186, 76, "HTTP routes", ("/limits · /feedback", "for the page"))
    part(726, 300, 186, 76, "Suite graph", ("waves, one convert", "graph per file"), "model")
    part(524, 388, 388, 64, "Node toolchain, pinned in the image", ("tsc · ESLint · residue · parity, run as a subprocess",), "good")
    wire("M 710 250 H 724", colour="muted")
    wire("M 819 300 V 290", colour="muted")

    zone(256, 488, 670, 114, "Private network", dashed=False, stroke=C["line"])
    text(274, 540, "no public IP, reached", 13.5, C["text3"])
    text(274, 559, "only from inside Fly", 13.5, C["text3"])
    store(543, 518, 200, "s2p-postgres", "Postgres + pgvector",
          ("checkpoints · memory", "limits · budget"))
    store(760, 518, 160, "s2p-redis", "Redis", ("run queue", "live run streams"))

    # --- external services -------------------------------------------------------
    zone(968, 110, 280, 500, "External services")
    add(f'<rect x="984" y="150" width="248" height="150" rx="12" fill="{TONES["model"][1]}" stroke="{C["model"]}" stroke-width="1.4"/>')
    glyph_cloud(998, 162)
    text(1040, 182, "Model providers", 16, C["text"], 700)
    for i, line in enumerate(("Anthropic · Claude", "OpenAI · GPT", "any LangChain chat model,", "chosen per run")):
        text(1000, 214 + i * 20, line, 13.5)
    add(f'<rect x="984" y="330" width="248" height="162" rx="12" fill="{C["inner"]}" stroke="{C["line2"]}" stroke-width="1.4"/>')
    glyph_cloud(998, 342)
    text(1040, 362, "LangSmith", 16, C["text"], 700)
    for i, line in enumerate(("a trace for every run", "👍 / 👎 from the page", "eval datasets", "experiments and A/B runs")):
        text(1000, 394 + i * 20, line, 13.5)

    # --- wires on the request path ------------------------------------------------
    wire("M 198 298 H 254", "HTTPS", (226, 272))
    wire("M 452 250 H 506", "SDK +\nvisitor key", (479, 212))
    wire("M 926 196 H 982", "HTTPS", (954, 172), "model")
    wire("M 926 410 H 982", "traces ·\nfeedback", (954, 372))
    wire("M 560 468 V 520", ":5432", (602, 490))
    wire("M 777 468 V 520", ":6379", (820, 490))

    # --- the developer machine ---------------------------------------------------
    zone(32, 650, 596, 300, "Developer machine", "your own keys")
    add(f'<rect x="48" y="696" width="180" height="150" rx="10" fill="{C["inner"]}" stroke="{C["line2"]}" stroke-width="1.3"/>')
    glyph_terminal(62, 708)
    text(102, 726, "s2p CLI", 15.5, C["text"], 700)
    for i, line in enumerate(("convert · suite", "same graphs,", "in-process", "runs in any CI job")):
        text(62, 758 + i * 19, line, 13.5)
    part(240, 696, 180, 150, "Eval harness", ("fixed datasets:", "12 files, 11 hard cases", "evaluators + LLM judge", "A/B before a prompt", "change ships"), "good")
    part(432, 696, 180, 150, "Deploy scripts", ("flyctl, both apps", "refuses while runs", "are in flight", "pinned API image"))

    # --- GitHub -------------------------------------------------------------------
    zone(652, 650, 596, 300, "GitHub", "varunbhatt2193/selenium2playwright")
    part(668, 696, 170, 150, "Repository", ("main", "goldens immutable", "CodeQL scanning"))
    part(858, 696, 374, 150, "Actions: CI gate, every push", ("790+ offline tests, no tokens", "golden fixtures run in Chromium", "playground build", "red blocks the merge"), "good")
    wire("M 838 770 H 856", colour="good")

    # --- wires from the build side ----------------------------------------------------
    wire("M 470 696 V 612", "flyctl deploy", (470, 632), "accent")
    wire("M 612 896 H 753 V 848", "git push", (640, 896))
    # The CLI and the eval harness call the same outside services the server does.
    bus = f'fill="none" stroke="{C["model"]}" stroke-width="1.7" stroke-dasharray="6 5"'
    add(f'<path d="M 138 846 V 978 H 1262 V 240" {bus}/>')
    add(f'<path d="M 330 846 V 978" {bus}/>')
    wire("M 1262 240 H 1234", colour="model", dashed=True)
    wire("M 1262 430 H 1234", colour="model", dashed=True)
    chip(700, 978, "CLI and eval harness → model providers · traces, datasets, experiments", C["model"])

    # --- legend ---------------------------------------------------------------------------
    ly = 1022
    items = [("zone", "boundary"), ("container", "deployed app"), ("part", "component"), ("store", "data store"),
             ("model", "model call"), ("good", "gate, no model"), ("bad", "refuses")]
    x = 32
    for kind, label in items:
        if kind == "zone":
            add(f'<rect x="{x}" y="{ly - 12}" width="20" height="16" rx="4" fill="none" stroke="{C["line2"]}" stroke-dasharray="4 3"/>')
        elif kind == "container":
            add(f'<rect x="{x}" y="{ly - 12}" width="20" height="16" rx="4" fill="{C["box"]}" stroke="{C["accent"]}"/>')
        elif kind == "part":
            add(f'<rect x="{x}" y="{ly - 12}" width="20" height="16" rx="4" fill="{C["inner"]}" stroke="{C["line2"]}"/>')
        elif kind == "store":
            cylinder(x + 3, ly - 14, 14, 20)
        else:
            stroke, fill = TONES[kind]
            add(f'<rect x="{x}" y="{ly - 12}" width="20" height="16" rx="4" fill="{fill}" stroke="{stroke}"/>')
        text(x + 28, ly + 1, label, 13.5, C["text3"])
        x += 36 + len(label) * 7.4 + 22

    body = "\n".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'font-family="Inter, -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif">\n'
            f"<title>Selenium2Playwright architecture: a browser calls the playground app on Fly, which screens input and "
            f"calls the LangGraph API server with a visitor key; the server holds the guard, the convert and suite graphs "
            f"and a pinned Node toolchain, keeps state in Postgres and Redis on a private network, and calls model "
            f"providers and LangSmith. A developer machine runs the CLI, the eval harness and the deploy scripts; GitHub "
            f"runs the CI gate on every push.</title>\n"
            f"{body}\n</svg>\n")


if __name__ == "__main__":
    OUT.write_text(draw(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")
