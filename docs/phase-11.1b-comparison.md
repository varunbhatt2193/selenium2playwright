# Phase 11.1b — playbook A/B

Comparable: **True**

**all-static pass 11/11 -> 10/11; graph passed 6/11 -> 8/11**

Edited between arms: `docs/playbook.md`.

| Held fixed | Value |
| --- | --- |
| dataset_id | `897199c5-f591-42d4-938e-9e6ec64fc8ed` |
| dataset_version | `2026-09-08T00:13:23.005331+00:00` |
| collection_sha256 | `b233d4c101fde8464611264aa8732f1fff2ef98700fd920da264cc2262b385ba` |
| model | `openai:gpt-5.4` |
| critic_model | `openai:gpt-5.4` |
| max_attempts | `3` |
| evaluator_version | `deterministic-v1` |

| Arm | Experiment | Git | Local complete | Cloud |
| --- | --- | --- | --- | --- |
| A: before | [s2p-11.1b-gpt-5.4-attempts3-a7d0e95c](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/897199c5-f591-42d4-938e-9e6ec64fc8ed/compare?selectedSessions=65031644-01a2-4fd7-b7af-413db0b7d552) | `56db849f8` | True | verified |
| B: after | [s2p-11.1b-gpt-5.4-attempts3-cc5468a0](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/897199c5-f591-42d4-938e-9e6ec64fc8ed/compare?selectedSessions=f285f3b8-189d-4e20-939c-b1154365fb1f) | `a2e398d15` | True | verified |

## Quality

| Metric | A: before | B: after | Delta (B − A) |
| --- | --- | --- | --- |
| compiles | 11/11 (100.0%) | 10/11 (90.91%) | -1 (-9.09 pts) |
| residue_free | 11/11 (100.0%) | 11/11 (100.0%) | +0 (+0.0 pts) |
| typed_lint_pass | 11/11 (100.0%) | 11/11 (100.0%) | +0 (+0.0 pts) |
| parity_pass | 11/11 (100.0%) | 11/11 (100.0%) | +0 (+0.0 pts) |
| all_static_passed | 11/11 (100.0%) | 10/11 (90.91%) | -1 (-9.09 pts) |
| graph_report_passed | 6/11 (54.55%) | 8/11 (72.73%) | +2 (+18.18 pts) |

## Cost

| Measure | A: before | B: after | Delta (B − A) |
| --- | --- | --- | --- |
| target_seconds | 138.3473210399970377 | 113.8232803740102085 | -24.5240406659868292 (×0.823) |
| actor_total_tokens | 58590 | 60568 | 1978 (×1.034) |
| critic_total_tokens | 63503 | 66191 | 2688 (×1.042) |
| langsmith_root_cost_usd | 0.3940640 | 0.3442940 | -0.0497700 (×0.874) |

## Per hard case

Groups overlap: one row exercises several patterns, so these counts do not sum to the experiment total and one row moving shows up in every group it belongs to.

| # | Pattern | Rows | All static A → B | Tuned for? |
| --- | --- | --- | --- | --- |
| 1 | Custom driver.wait(async predicate) polling → web-first assertion or expect(...).toPass() | 3 | 3 → 2 | yes |
| 3 | until.stalenessOf / stale-element retry loops → wait for the new state instead | 2 | 2 → 1 | yes |
| 4 | Stateful switchTo().frame()/defaultContent() across methods → stateless frameLocator() scoping | 2 | 2 → 2 | yes |
| 6 | Implicit-wait config + findElements().length presence/absence → toHaveCount() with the same patience | 2 | 2 → 1 | yes |
| 7 | findElements loops that mutate the DOM (delete-all) → re-query loop, not a stale snapshot | 2 | 2 → 2 | yes |
| 8 | Action chains: hover menus, modifier clicks, drag-and-drop | 2 | 2 → 2 | yes |
| 9 | executeScript workarounds (JS click, scrollIntoView) → delete, or locator.evaluate() | 2 | 2 → 2 | yes |
| 10 | BasePage wait helpers → inline or delete with a ledger entry, not mechanical preservation | 2 | 2 → 2 | reserved |
| 11 | beforeAll shared driver + login → serial/fixtures/storageState; surface the order dependence | 2 | 2 → 2 | reserved |
| 12 | Promise-chained legacy code with no await → insert awaits without reordering side effects | 1 | 1 → 1 | yes |

## Each example

| Case | A status (attempts) | A all-static | B status (attempts) | B all-static | Change |
| --- | --- | --- | --- | --- | --- |
| add-remove-page | needs-review (2) | True | passed (1) | True | improved |
| add-remove-test | passed (1) | True | passed (1) | True | same |
| base-page | needs-review (3) | True | needs-review (3) | True | same |
| dynamic-controls-page | needs-review (3) | True | needs-review (3) | False | regressed |
| dynamic-controls-test | passed (1) | True | passed (1) | True | same |
| hovers-page | passed (1) | True | passed (2) | True | same |
| hovers-test | passed (1) | True | passed (1) | True | same |
| nested-frames-page | needs-review (3) | True | needs-review (2) | True | same |
| nested-frames-test | passed (1) | True | passed (1) | True | same |
| shared-session-page | passed (1) | True | passed (1) | True | same |
| shared-session-test | needs-review (2) | True | passed (1) | True | improved |

Changes: {'improved': 2, 'same': 8, 'regressed': 1}.

One run per arm. These models are not deterministic and temperature is not set, so a small delta can be run-to-run variance rather than the edit. Static gates do not establish browser correctness.
