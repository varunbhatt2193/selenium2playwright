# Does the repair loop actually help? (Phase 6.3, explained simply)

This page explains step 6.3 in plain words: what question we asked, how we
set up a fair test, what code changed, how to run it, and how to read the
answer. The measured numbers live in [phase-6.3-report.md](phase-6.3-report.md).

## 1. The question

Our converter does not stop after one try. It drafts Playwright code, runs four
checks on it (compile, residue, lint, parity), asks a second model call (the
critic) to review it, and if something is wrong it tries again with that
feedback. It can do this up to three times. We call that loop **reflection**.

Reflection costs money and time: every extra lap is one more conversion call
and one more critic call. So the honest question is:

> If we allowed only **one** attempt, how much worse would the results be?

Until now we could only say "the loop helps, we saw it fix a missing `await`
once". That is a story, not a number. Step 6.3 replaces the story with a
number, and the roadmap says that number replaces the word "perfect" everywhere.

## 2. What an A/B test is, and why only one thing may change

An A/B test runs the same job twice with exactly one difference, then compares.

- **Arm A**: every file gets one conversion attempt. The checks and the critic
  still run, so we still get a scorecard, but the graph never repairs.
- **Arm B**: every file gets up to three attempts (draft + two repairs). This
  is what the product does today.

Everything else is held fixed: the same 12 dataset examples at the same pinned
version, the same model, the same prompts, the same evaluators, the same git
revision. If two things changed at once we could not say which one caused the
difference. The comparison code checks this and refuses to compare arms that
differ in anything besides the attempt cap.

One thing we cannot hold fixed: the model itself is not deterministic. Running
arm B twice would not give identical output. So a difference of one file could
be luck. Treat small deltas as "about the same", not as proof.

## 3. What changed in the code

Before this step the number three was hard-coded. There was no way to ask the
graph for fewer laps. These are the changes, file by file.

**`reflection.py`** — a tiny helper, `resolve_attempt_cap(value)`. It turns an
optional input into a checked number: `None` means "use the default (3)";
anything else must be a whole number from 1 to 3. A wrong value raises an
error right away instead of quietly changing how many model calls we make.

**`graph.py`** — the graph state gets one new optional input key,
`max_attempts`. The `intake` node fills it in (default 3). The routing function
after the critic, which used to compare against the constant, now compares
against `state["max_attempts"]`. That single comparison is the whole feature:
with a cap of 1 the critic still writes its review, but the edge goes to
`assemble` instead of back to `convert`. The CLI also gets `--max-attempts`.

In LangGraph terms: **state** is a dict that every node reads and adds to;
a **conditional edge** is a plain function that looks at the state and returns
the name of the next node. We did not add a node or an edge. We made the
existing conditional edge read its budget from state instead of from a
constant. This is the normal way to make a graph configurable.

**`eval_target.py`** — the evaluation target (the function LangSmith calls once
per dataset example) now accepts `max_attempts`, passes it into the graph
inputs, tags the trace with `attempts:1` or `attempts:3`, and records the cap in
its output so a row can never be misread later.

**`eval_plan.py`** — the frozen configuration now includes `max_attempts`. The
configuration is hashed, and that hash is stored with the experiment. Two plans
that differ only in the cap now have different hashes. This is what lets the
comparison prove "only the cap changed". Plans also carry a `phase` label used
in the experiment name and report title.

**`eval_experiment.py`** — the runner takes the cap from the plan, not from the
caller, and binds it to the real target with `functools.partial`. So the
recorded configuration and the graph that actually ran can never disagree.
The experiment name becomes `s2p-6.3-<model>-attempts<N>-<id>`.

**`eval_compare.py`** (new) — takes two finished experiment reports, checks they
are a fair pair, and computes B minus A for every metric: the four gates, the
"all four passed" count, the "graph report passed" count, wall-clock seconds,
actor tokens, critic tokens, cost where LangSmith reports it, and the number of
model calls. It also labels each of the 12 cases `same`, `improved`,
`regressed`, or `no draft in A/B` (the model's reply never parsed into code,
so that row says nothing about the repair loop), and writes a short
markdown scorecard.

**`scripts/run_reflection_ab.py`** (new) — runs arm A, then arm B, then the
comparison, into one folder under `out/6.3/`. Without `--run` it only writes
the two plans so you can inspect them. `--compare-only A B` rebuilds the
comparison from saved reports without calling any model.

## 4. How to run it

```bash
# preview: write both plans, no model calls, no uploads
.venv/bin/python scripts/run_reflection_ab.py

# live: two experiments (12 conversions each), then the comparison
.venv/bin/python scripts/run_reflection_ab.py --run

# recompute the scorecard from saved evidence
.venv/bin/python scripts/run_reflection_ab.py --compare-only out/6.3/ab-<stamp>/attempts-1 out/6.3/ab-<stamp>/attempts-3

# try the cap on the CLI for one file
uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts --max-attempts 1
```

Commit first. The plan records whether the worktree was dirty, and a clean
revision is what makes the result reviewable by someone else.

## 5. How to read `comparison.md`

- **Comparable** must be `True`. If it is `False`, the listed issues explain
  what differed and the numbers below it should not be quoted.
- **Quality** table: pass counts for each arm and the delta. `all_static_passed`
  is "all four checks passed"; `graph_report_passed` is stricter, it also needs
  the critic to say pass and zero `TODO(review)` items.
- **Cost of reflection** table: time, tokens, and cost. A total is shown only
  when every row reported it; otherwise you see the known subtotal and how many
  rows are missing. Missing is reported as missing, never as zero.
- **Each example** table: what happened to every file in both arms, and how
  many repairs arm B actually used. Most files pass on the first try, so the
  loop only costs extra on the files that needed it.

## 6. What we measured (2026-09-06, Opus, 12 files)

| | A: one attempt | B: reflection |
| --- | --- | --- |
| All four checks passed | 11 of 12 | 9 of 12 |
| Graph report passed | 9 of 12 | 8 of 12 |
| Actor model calls | 12 | 14 |
| Actor tokens | 51,327 | 62,347 |
| Wall-clock | 174.5 s | 174.2 s |

At first glance reflection looks worse. The per-file table explains it. Four
files across the two arms (one in A, three in B) never produced code at all:
the model wrote `notes` as a sentence instead of a list, the reply failed to
parse, and the graph went straight to assembly. Reflection cannot fix a file it
never sees. That failure is random from run to run, and it is the biggest
quality problem in the pipeline right now.

Looking only at files that did produce a draft: every one passed all four
checks on the first try in both arms. Reflection fired on two files in arm B
and turned one of them from "needs review" into "passed". The price was two
extra model calls and about 11,000 extra tokens.

So the number that replaces "perfect" is: **9 to 11 files out of 12 pass all
four static checks per run, and 1 to 3 first drafts out of 12 fail to parse.**
Full evidence and links: [phase-6.3-report.md](phase-6.3-report.md).

## 7. What this does not prove

- The checks are static. A file can pass all four and still behave wrongly in a
  browser. Browser-based evals are a later phase.
- One run per arm. The model varies between runs, so a one-file difference is
  within noise. A bigger dataset and repeated runs would tighten this.
- Arm A still runs the critic. That is deliberate so both arms produce the same
  report shape, but it means arm A is not the cheapest possible pipeline.

## 8. Check yourself

1. In arm A the critic says "revise". What does the graph do next, and which
   function decides that?
2. Why is `max_attempts` part of the hashed configuration instead of just a
   command-line flag?
3. The comparison says `comparable: False` with "configuration.model differs".
   Is the quality delta still meaningful? Why not?
4. Arm B used two attempts on two files and one attempt on the other ten.
   How many extra actor calls did reflection cost on this dataset?
5. Three files in arm B produced no code because the reply did not parse.
   Which edge in the graph decided that, and why did the repair loop not run?

Answers are in the code comments and in [phase-6.3-report.md](phase-6.3-report.md).

## Related pages

- [reflection-loop.md](reflection-loop.md) — how the loop was built in phase 5.
- [evaluation-primer.md](evaluation-primer.md) — datasets, targets, evaluators.
- [evaluation-runner.md](evaluation-runner.md) — how one experiment is run and verified.
- [phase-6.2-report.md](phase-6.2-report.md) — the baseline this step builds on.
