"""Measure the recall threshold instead of guessing it (step 7.3).

    caffeinate -i -s uv run python scripts/calibrate_recall.py

Semantic recall contains one number that cannot be reasoned out from first
principles: how close is close enough? Cosine similarity between an embedded
memory and an embedded file is not a probability and has no universal cut-off.
It moves with the embeddings model, with how the query is built, and — most of
all — with how the user happened to word the memory.

So measure it, on our own data. Every memory below is labelled with the files it
genuinely applies to; three are about something else entirely, the noise that
must never reach a prompt; and one pair says the same thing twice, once vaguely
and once naming the kind of file it applies to, because the gap between those
two is larger than any tuning.

What the run reports is the thing that actually matters — the shortlist each
file would be sent at the configured MIN_SCORE and RECALL_LIMIT:

  hard requirement   no unrelated memory ever reaches a shortlist. That is the
                     gate's real job, and the script fails if it is broken.
  soft measure       how many genuinely applicable memories reached one. Misses
                     are reported by name; they are usually a wording problem,
                     not a threshold problem.

It also scores the same pairs with the naive query (paste the file in) to show
why store.recall_query builds a profile instead.

Writes out/7.3/recall-calibration.json. Costs a few embedding calls (a fraction
of a cent) and no chat-model calls at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from selenium2playwright import env, llm, store
from selenium2playwright.classify import classify

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "7.3"
SUITE = ROOT / "samples/selenium-suite"

FILES = ["pages/LoginPage.ts", "pages/AlertsPage.ts", "pages/UploadPage.ts",
         "tests/login.spec.ts", "tests/alerts.spec.ts", "tests/upload.spec.ts"]

# Preferences in a user's own words, not phrased to match the files. `applies`
# is where each one is genuinely actionable; the empty ones are noise that must
# never be recalled for any of these files.
MEMORIES: list[tuple[str, str, set[str]]] = [
    ("testids", "Use getByTestId for form fields; our app ships data-testid on every input",
     {"pages/LoginPage.ts"}),
    ("fixtures", "Tests should use a shared test.extend fixture that logs in, not a beforeEach",
     {"tests/login.spec.ts", "tests/alerts.spec.ts", "tests/upload.spec.ts"}),
    ("naming", "Name page object classes <Feature>Page and their file <Feature>Page.ts",
     {"pages/LoginPage.ts", "pages/AlertsPage.ts", "pages/UploadPage.ts"}),
    ("uploads", "For file uploads prefer setInputFiles over clicking through the OS dialog",
     {"pages/UploadPage.ts", "tests/upload.spec.ts"}),
    # The same rule twice. The second one names the kind of file it is about,
    # and that single phrase is worth more than any threshold change.
    ("steps-vague", "Wrap the actions inside every test in test.step() calls named after what "
                    "the step does, so the HTML report reads like a scenario",
     {"tests/login.spec.ts", "tests/alerts.spec.ts", "tests/upload.spec.ts"}),
    ("steps-named", "In test specs, wrap each action in a named test.step() block",
     {"tests/login.spec.ts", "tests/alerts.spec.ts", "tests/upload.spec.ts"}),
    ("ci", "Our CI publishes the HTML report to S3 after every nightly run", set()),
    ("release", "We freeze releases on Fridays and cut the branch on Monday morning", set()),
    ("support", "Ask the platform team in #qa-help before adding a new npm dependency", set()),
]


def naive_query(path: str, classification, source: str) -> str:
    """The obvious query this project started with: the head of the file itself."""
    kind = "page object" if classification.runner == "none" else f"{classification.runner} test spec"
    return f"{kind} {Path(path).name}\n{source[:1200]}"


def score_all(embeddings, dims, query_builder) -> tuple[list[dict], list, list]:
    """Every memory scored against every file, plus the two labelled groups."""
    rows, applies, unrelated = [], [], []
    with store.open_store(store.IN_MEMORY, embeddings, dims) as memory:
        tags = {}
        for tag, text, _ in MEMORIES:
            tags[store.key_for(text)] = tag
            store.remember(memory, text, "calibration")
        for relative in FILES:
            path = SUITE / relative
            source = path.read_text(encoding="utf-8")
            query = query_builder(str(path), classify(source, str(path)), source)
            # min_score=0 and no cap: we want every score, not the shortlist.
            found = store.recall(memory, query, "calibration", limit=len(MEMORIES), min_score=0.0)
            scores = {tags[item.key]: round(item.score, 4) for item in found}
            rows.append({"file": relative, "scores": scores})
            for tag, _, where in MEMORIES:
                score = scores.get(tag, 0.0)
                if relative in where:
                    applies.append((score, tag, relative))
                elif not where:
                    unrelated.append((score, tag, relative))
    return rows, applies, unrelated


def render(title: str, rows: list[dict], applies: list, unrelated: list) -> dict:
    width = max(len(f) for f in FILES)
    print(f"\n=== {title} ===")
    print("file".ljust(width) + "  " + "  ".join(tag.rjust(11) for tag, _, _ in MEMORIES))
    for row in rows:
        print(row["file"].ljust(width) + "  "
              + "  ".join(f"{row['scores'].get(tag, 0.0):11.4f}" for tag, _, _ in MEMORIES))
    lowest, loudest = min(applies), max(unrelated)
    print(f"lowest score that genuinely applies : {lowest[0]:.4f}  ({lowest[1]} / {lowest[2]})")
    print(f"loudest unrelated memory            : {loudest[0]:.4f}  ({loudest[1]} / {loudest[2]})")
    if lowest[0] <= loudest[0]:
        print("                                      the two bands overlap: no single "
              "threshold separates them")
    return {"rows": rows, "lowest_applies": lowest, "loudest_unrelated": loudest,
            "window": lowest[0] - loudest[0]}


def shortlists(rows: list[dict]) -> dict:
    """What each file would actually be sent, at the configured gate and cap."""
    applicable = {tag: where for tag, _, where in MEMORIES}
    picked, hits, noise, missed = {}, 0, [], []
    for row in rows:
        ranked = sorted(row["scores"].items(), key=lambda pair: pair[1], reverse=True)
        chosen = [(tag, score) for tag, score in ranked if score >= store.MIN_SCORE][:store.RECALL_LIMIT]
        picked[row["file"]] = chosen
        names = {tag for tag, _ in chosen}
        for tag, where in applicable.items():
            if row["file"] in where and tag in names:
                hits += 1
            elif row["file"] in where:
                missed.append(f"{tag} / {row['file']} ({row['scores'].get(tag, 0.0):.4f})")
            elif not where and tag in names:
                noise.append(f"{tag} / {row['file']} ({row['scores'].get(tag, 0.0):.4f})")
    total = sum(len(where) for where in applicable.values())
    print(f"\nshortlists at MIN_SCORE {store.MIN_SCORE}, limit {store.RECALL_LIMIT}:")
    for name, chosen in picked.items():
        print(f"  {name.ljust(max(len(f) for f in FILES))}  "
              + ", ".join(f"{tag}@{score:.3f}" for tag, score in chosen))
    print(f"applicable memories recalled : {hits} of {total}")
    for miss in missed:
        print(f"  missed: {miss}")
    print(f"unrelated memories recalled  : {len(noise)}  (must be 0)")
    for hit in noise:
        print(f"  LEAKED: {hit}")
    return {"picked": picked, "applicable_recalled": hits, "applicable_total": total,
            "missed": missed, "unrelated_recalled": noise}


def main() -> int:
    name = env.embeddings_name()
    if not name:
        print("S2P_EMBEDDINGS=off — nothing to calibrate.")
        return 2
    embeddings = llm.make_embeddings()
    dims = llm.embedding_dims(embeddings)
    print(f"embeddings: {name} ({dims} dims)")

    naive = render("naive query: the head of the file", *score_all(embeddings, dims, naive_query))
    profile = render("store.recall_query: a profile of the file",
                     *score_all(embeddings, dims, store.recall_query))
    selection = shortlists(profile["rows"])

    clean = not selection["unrelated_recalled"]
    print("\nthe gate holds: no unrelated memory reached a prompt" if clean
          else "\nthe gate LEAKED — raise MIN_SCORE or fix the query")

    OUT.mkdir(parents=True, exist_ok=True)
    receipt = {"embeddings": name, "dims": dims, "naive": naive, "profile": profile,
               "selection": selection, "configured_min_score": store.MIN_SCORE,
               "recall_limit": store.RECALL_LIMIT, "gate_holds": clean}
    (OUT / "recall-calibration.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"wrote {OUT / 'recall-calibration.json'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
