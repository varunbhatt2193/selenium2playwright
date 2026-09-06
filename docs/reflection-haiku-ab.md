# Does reflection help more when the writer is weaker? (Haiku actor, Opus critic, explained simply)

Phase 6.3 measured the repair loop with Opus writing the code. The answer was
"a little": every first draft already passed the four static checks, so the
loop had almost nothing to fix. That raises an obvious follow-up:

> Is reflection worth little because the loop is weak, or because Opus rarely
> needs a second try?

The clean way to find out is to give the loop a weaker writer and keep the
reviewer strong. This page explains what changed, why, how to run it, and what
we measured. The evidence with links lives in
[phase-6.5-haiku-report.md](phase-6.5-haiku-report.md).

## 1. Actor and critic are two jobs

Inside the graph there are two model calls per lap:

- the **actor** reads the Selenium file and writes the Playwright draft;
- the **critic** reads the draft plus the four check results and says
  `pass` or `revise`, with a list of fixes.

Until now one setting, `S2P_MODEL`, named the model for both jobs. That is
fine for production, but it makes "weak writer, strong reviewer" impossible
to express. So this step adds a second, optional setting:

| Setting | Names | If empty |
| --- | --- | --- |
| `S2P_MODEL` | the actor | `anthropic:claude-sonnet-5` |
| `S2P_CRITIC_MODEL` | the critic | the same as the actor |

Nothing changes for anyone who never sets the second one.

## 2. What changed in the code

**`env.py`** — `critic_model_name()` reads `S2P_CRITIC_MODEL` and falls back to
the actor. `model_names()` returns both by role. `required()` now asks for an
API key for *every* provider in use, so an OpenAI critic with an Anthropic
actor would demand both keys.

**`llm.py`** — `make_model(for_critic=True)` resolves the critic's name from
the environment. `prepare_messages(for_critic=True)` does the same, so the
Anthropic prompt-cache marker is applied only when the model that will
receive the messages is Anthropic. The provider knowledge stays in this one
file, as the model-agnostic rule requires.

**`graph.py`** — one word: the critic chain calls
`prepare_messages(for_critic=True)`.

**`eval_plan.py`** — the hashed configuration gets a `critic_model` key.
This is the important part for fairness. In 6.3 the comparison refuses two
arms whose configuration differs in anything but `max_attempts`. Because the
critic is now inside the hash, a "Haiku + Opus critic" arm can never be
accidentally compared with a "Haiku + Haiku critic" arm.

**`eval_experiment.py`** — before any model call the runner checks that both
environment variables equal what the plan recorded, and the experiment name
adds `-critic-<model>` only when the critic differs from the actor.

**`scripts/run_reflection_ab.py`** — two new flags, `--critic-model` and
`--phase`. The output folder is `out/<phase>/`.

## 3. How to run it

```bash
# preview: write both plans (note the two different configuration hashes)
.venv/bin/python scripts/run_reflection_ab.py --phase 6.5 \
    --model anthropic:claude-haiku-4-5-20251001 --critic-model anthropic:claude-opus-5

# live: arm A (one attempt), arm B (up to three), then the comparison
.venv/bin/python scripts/run_reflection_ab.py --run --phase 6.5 \
    --model anthropic:claude-haiku-4-5-20251001 --critic-model anthropic:claude-opus-5
```

Both arms use the same 12 pinned examples, the same pinned dataset version,
the same prompts, the same Opus critic, and the same git revision. Only the
attempt cap differs.

## 4. Why Haiku as the weak actor

Haiku 4.5 is the smallest current Claude model. It is fast and cheap, so the
question "can a strong reviewer make a cheap writer good enough?" is the one
with a real cost pay-off. If reflection lifts Haiku close to Opus-alone
quality, the loop has earned its extra calls. If it does not, the honest
conclusion is that the actor's quality matters more than the loop.

## 5. What we measured (2026-09-06, 12 files, Haiku actor, Opus critic)

Both arms ran at revision `1c8edad` on a clean worktree, and both were
verified in LangSmith. The receipt with every number is
[phase-6.5-haiku-comparison.json](phase-6.5-haiku-comparison.json).

| | Haiku alone | Haiku + reflection | Opus alone | Opus + reflection |
| --- | --- | --- | --- | --- |
| All four checks passed | 9 of 12 | **12 of 12** | 12 of 12 | 12 of 12 |
| Graph report passed | 2 of 12 | **9 of 12** | 6 of 12 | 11 of 12 |
| Files that used a repair | 0 | 8 | 0 | 2 |
| Actor model calls | 12 | 23 | 12 | 14 |
| Actor tokens | 42,510 | 95,328 | 50,860 | 63,139 |
| Critic tokens (Opus in all four) | 62,374 | 119,489 | 57,831 | 66,795 |
| Wall-clock | 199 s | 391 s | 184 s | 211 s |
| LangSmith cost | $0.36 | $0.69 | $0.52 | $0.54 (11 of 12 rows) |

The Opus columns are the phase 6.3 run-2 numbers, on the same dataset
version and evaluators, at revision `c9459f2`.

Four things to take from that table.

1. **With a weak writer, reflection is the whole story.** Three of Haiku's
   twelve first drafts did not compile, and one also failed lint. Every one
   of them was fixed by a repair lap. Eight of twelve files needed at least
   one repair; three needed both. Seven files went from "needs review" to
   better with a repair, and the comparison credits reflection for each.
   Only two rows improved without a repair (critic variance).
2. **Haiku with the loop beats Opus without it.** Nine fully passed reports
   versus six. The strong critic plus the retry budget turns a cheap first
   draft into something close to the Opus-alone result.
3. **But it does not beat Opus with the loop, and it is not cheaper.**
   Opus with reflection still leads (11 of 12) and costs less ($0.54 known
   over 11 rows vs $0.69). The reason is in the critic row: the critic is
   Opus in every column, and it runs once per lap. With Haiku the loop ran
   23 laps instead of 14, so the critic bill nearly doubled. The cheap actor
   saved little because the expensive reviewer did most of the work.
4. **Time doubles.** Each repair is another actor call, another compile,
   lint, and parity run, and another critic call. 199 s became 391 s.

So the honest verdict on the roadmap question "does reflection earn its
extra calls?" is: **yes, when the first draft is often wrong.** With Opus
the loop fired twice and fixed one file. With Haiku it fired eight times and
fixed all three compile failures plus four review problems. The loop is not
weak; Opus rarely needs it.

## 6. What went wrong on the way

The first live attempt was thrown away. While arm A was running I wrote this
page into `docs/`, which made the git worktree dirty. The runner hashes
"is the worktree dirty?" into the experiment configuration and re-checks it
after the last row, so it correctly refused the result: "Configuration
changed during execution". Arm A's twelve rows had actually run (the orphan
experiment `e931ca7e` is still in LangSmith) but the evidence is not clean,
so both arms were rerun on a clean tree with nothing touched until the
comparison was written. The rule is simple: **do not edit anything in the
repo while a live experiment is running.**

## 7. What this does not prove

- Static gates plus the critic's opinion, not a browser run.
- One run per arm. The two "variance" rows show how much the critic's
  verdict moves between runs.
- Haiku's cost advantage would look different with a Haiku critic. That
  arm was not run; it would change two things at once (actor and critic),
  which is exactly what the A/B rules forbid.

## 8. Check yourself

1. Why is the critic's model part of the hashed configuration, and what
   would the comparison say if arm A used a Haiku critic and arm B an Opus
   critic?
2. Haiku with reflection made 23 actor calls. How many critic calls did it
   make, and why is that the number that drives the cost?
3. `windows-page` failed to compile in arm A and passed everything in arm B
   after one repair. Which node produced the feedback the actor used on
   attempt 2?
4. Why was the first live run discarded even though all twelve rows of arm A
   finished?

## Related pages

- [reflection-ab.md](reflection-ab.md) — the Opus A/B this builds on.
- [phase-6.5-haiku-report.md](phase-6.5-haiku-report.md) — evidence, links, screenshot.
- [reflection-loop.md](reflection-loop.md) — how the loop was built.
