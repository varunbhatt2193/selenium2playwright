# Phase 11.4-rule29 — playbook A/B

Comparable: **True**

**all-static pass 10/11 -> 11/11; graph passed 8/11 -> 9/11**

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
| A: before | [s2p-11.4-rule29-gpt-5.4-attempts3-140ee3da](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/897199c5-f591-42d4-938e-9e6ec64fc8ed/compare?selectedSessions=e21cb5c3-6d08-4c65-9500-35c31dcd8694) | `424cdbeef` | True | verified |
| B: after | [s2p-11.4-rule29-gpt-5.4-attempts3-dfc4af53](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/897199c5-f591-42d4-938e-9e6ec64fc8ed/compare?selectedSessions=625f0cf4-94ef-49c5-9c62-02f48aed4971) | `424cdbeef` | True | verified |

## Quality

| Metric | A: before | B: after | Delta (B − A) |
| --- | --- | --- | --- |
| compiles | 10/11 (90.91%) | 11/11 (100.0%) | +1 (+9.09 pts) |
| residue_free | 11/11 (100.0%) | 11/11 (100.0%) | +0 (+0.0 pts) |
| typed_lint_pass | 11/11 (100.0%) | 11/11 (100.0%) | +0 (+0.0 pts) |
| parity_pass | 11/11 (100.0%) | 11/11 (100.0%) | +0 (+0.0 pts) |
| all_static_passed | 10/11 (90.91%) | 11/11 (100.0%) | +1 (+9.09 pts) |
| graph_report_passed | 8/11 (72.73%) | 9/11 (81.82%) | +1 (+9.09 pts) |

## Cost

| Measure | A: before | B: after | Delta (B − A) |
| --- | --- | --- | --- |
| target_seconds | 107.1959805829974318 | 106.4871527929972217 | -0.7088277900002101 (×0.993) |
| actor_total_tokens | 71671 | 80108 | 8437 (×1.118) |
| critic_total_tokens | 76541 | 84729 | 8188 (×1.107) |
| langsmith_root_cost_usd | 0.2936410 | 0.3234995 | 0.0298585 (×1.102) |

## Per hard case

Groups overlap: one row exercises several patterns, so these counts do not sum to the experiment total and one row moving shows up in every group it belongs to.

| # | Pattern | Rows | All static A → B | Tuned for? |
| --- | --- | --- | --- | --- |
| 1 | Custom driver.wait(async predicate) polling → web-first assertion or expect(...).toPass() | 3 | 2 → 3 | yes |
| 3 | until.stalenessOf / stale-element retry loops → wait for the new state instead | 2 | 1 → 2 | yes |
| 4 | Stateful switchTo().frame()/defaultContent() across methods → stateless frameLocator() scoping | 2 | 2 → 2 | yes |
| 6 | Implicit-wait config + findElements().length presence/absence → toHaveCount() with the same patience | 2 | 1 → 2 | yes |
| 7 | findElements loops that mutate the DOM (delete-all) → re-query loop, not a stale snapshot | 2 | 2 → 2 | yes |
| 8 | Action chains: hover menus, modifier clicks, drag-and-drop | 2 | 2 → 2 | yes |
| 9 | executeScript workarounds (JS click, scrollIntoView) → delete, or locator.evaluate() | 2 | 2 → 2 | yes |
| 10 | BasePage wait helpers → inline or delete with a ledger entry, not mechanical preservation | 2 | 2 → 2 | reserved |
| 11 | beforeAll shared driver + login → serial/fixtures/storageState; surface the order dependence | 2 | 2 → 2 | reserved |
| 12 | Promise-chained legacy code with no await → insert awaits without reordering side effects | 1 | 1 → 1 | yes |

## Each example

| Case | A status (attempts) | A all-static | B status (attempts) | B all-static | Change |
| --- | --- | --- | --- | --- | --- |
| add-remove-page | passed (1) | True | passed (1) | True | same |
| add-remove-test | passed (1) | True | passed (1) | True | same |
| base-page | needs-review (3) | True | needs-review (3) | True | same |
| dynamic-controls-page | needs-review (3) | False | passed (2) | True | improved |
| dynamic-controls-test | passed (1) | True | passed (1) | True | same |
| hovers-page | passed (1) | True | passed (1) | True | same |
| hovers-test | passed (1) | True | passed (1) | True | same |
| nested-frames-page | passed (1) | True | passed (1) | True | same |
| nested-frames-test | passed (1) | True | passed (1) | True | same |
| shared-session-page | passed (1) | True | needs-review (2) | True | regressed |
| shared-session-test | needs-review (2) | True | passed (3) | True | improved |

Changes: {'same': 8, 'improved': 2, 'regressed': 1}.

One run per arm. These models are not deterministic and temperature is not set, so a small delta can be run-to-run variance rather than the edit. Static gates do not establish browser correctness.
