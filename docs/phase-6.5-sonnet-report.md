# Phase 6.5 (part 2) — Sonnet writes, Opus reviews: arm A valid, arm B invalid

Same A/B as the [Haiku run](phase-6.5-haiku-report.md) with
`anthropic:claude-sonnet-5` as the actor and `anthropic:claude-opus-5` as the
critic, revision `c48e5f6`, clean worktree, pinned dataset version
`2026-09-06T03:05:09.476354+00:00`.

## What happened

Arm A (one attempt) completed and verified. Arm B (up to three attempts) ran
all twelve rows, but during the last two the Anthropic account ran out of
credits. Two actor calls returned:

```
Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error',
'message': 'Your credit balance is too low to access the Anthropic API. ...'}}
```

`windows-page` lost its third attempt (its attempt-2 draft was kept, still
needs-review) and `windows-test` got no draft at all on attempt 1. LangSmith
still verified the experiment, and the original comparison called it
"comparable" with the headline "all-static 12/12 → 11/12, graph 9/12 → 8/12".
That headline is wrong: it is a billing failure, not a model result. This is
gap **T10** in [gap-log.md](gap-log.md); `eval_compare` now refuses an arm
that contains a provider error, and the regenerated receipt says so:

> Not comparable: arm B has provider errors (infrastructure, not model
> quality) on windows-page, windows-test; rerun that arm.

**Arm B must be rerun once credits are restored.** Nothing below uses it.

## Arm A: Sonnet, one attempt (valid)

| Metric | Value |
| --- | --- |
| All four static gates passed | 12/12 |
| Graph report `passed` | 9/12 (needs-review: iframe-page, login-page, windows-page, all critic `revise`) |
| Actor tokens / critic tokens | 50,933 / 56,831 |
| Target wall-clock (sum) | 155.9 s |
| LangSmith root cost | $0.316863 (12/12 rows) |
| Experiment | [`s2p-6.5-claude-sonnet-5-critic-claude-opus-5-attempts1-fc66b840`](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/33c80b1e-96bd-4b5b-a9c1-ca49d215828f/compare?selectedSessions=facf5bdd-5725-4b1c-8764-9dba41d9ae0f), ID `facf5bdd-5725-4b1c-8764-9dba41d9ae0f` |
| Configuration SHA-256 | `efdc32455f94eb4ac23f3bc634929aa6adf4320942658b84ca2885a08f46ea42` |

For scale: Opus alone was 12/12 static and 6/12 graph-passed; Haiku alone was
9/12 and 2/12. Sonnet's first drafts are as clean as Opus's on the static
gates and the critic accepted more of them. One run; treat as indicative.

## Arm B: Sonnet, reflection (invalid, kept for the record)

Experiment `s2p-6.5-claude-sonnet-5-critic-claude-opus-5-attempts3-1f892a3c`,
ID `fc93d48b-91ad-4efc-9b1b-6b918d797fa5`, configuration `93190431c422…`.
Before the credit failure, the ten unaffected rows matched arm A row for row
(nine passed on attempt 1; iframe-page and login-page used all three
attempts and stayed needs-review with one TODO each). Receipt with the
refusal recorded: [phase-6.5-sonnet-comparison-invalid.json](phase-6.5-sonnet-comparison-invalid.json).
Local artifacts (ignored): `out/6.5/ab-20260906T153717Z-45002cfe/`.

## To finish this step

```bash
# after topping up credits, on a clean worktree
.venv/bin/python scripts/run_reflection_ab.py --run --phase 6.5 \
    --model anthropic:claude-sonnet-5 --critic-model anthropic:claude-opus-5
# then redraw the diagram with all three actors
.venv/bin/python scripts/render_actor_shootout.py \
    Haiku=docs/phase-6.5-haiku-comparison.json \
    Sonnet=docs/phase-6.5-sonnet-comparison.json \
    Opus=docs/phase-6.3-comparison.json \
    --svg docs/reflection-shootout.svg --md docs/reflection-shootout-table.md
```

The rerun repeats arm A as well, so both arms cite one revision.
