// Every number on the Evaluation page, copied from the reports in docs/ and
// named for where it came from. Change the report first, then this file.
// Nothing here is computed on the page; the page draws what it is given.

export const REPORTS = {
  judge: 'docs/phase-6.4-report.md',
  agreement: 'docs/phase-6.4-judge-agreement.md',
  ab: 'docs/phase-6.3-report.md',
  shootout: 'docs/reflection-shootout.md',
  hardCases: 'docs/hard-cases.md',
  hardCasesScored: 'docs/phase-11.1b-report.md',
  execution: 'docs/phase-11.2-report.md',
  gapLog: 'docs/gap-log.md',
  primer: 'docs/evaluation-primer.md',
}

// --- the shootout: one attempt vs reflection, per actor, same Opus critic -----
// docs/reflection-shootout-table.md. "Passed" is the graph report, out of 12.
export type ShootoutRow = { actor: string; size: string; one: number; reflection: number; repaired: number; costOne: string; costReflection: string }

export const SHOOTOUT_TOTAL = 12
export const SHOOTOUT: ShootoutRow[] = [
  { actor: 'Haiku', size: 'small', one: 2, reflection: 9, repaired: 8, costOne: '$0.36', costReflection: '$0.69' },
  { actor: 'Sonnet', size: 'mid', one: 11, reflection: 10, repaired: 3, costOne: '$0.29', costReflection: '$0.40' },
  { actor: 'Opus', size: 'large', one: 6, reflection: 11, repaired: 2, costOne: '$0.52', costReflection: '≥$0.54' },
]

// --- the A/B behind the production default (Opus actor) ----------------------
// docs/phase-6.3-report.md, run 2, code revision c9459f21.
export const AB = {
  gatesOne: '12/12',
  gatesReflection: '12/12',
  passedOne: '6/12',
  passedReflection: '11/12',
  repairLaps: 2,
  actorTokens: '×1.24',
  criticTokens: '×1.16',
  wallClock: '×1.15',
}

// --- judge calibration, before the judge scored anything that mattered -------
// docs/phase-6.4-report.md §1 and docs/phase-6.4-judge-agreement.md.
export const JUDGE = {
  rubric: 'idiomatic-v1',
  goldens: 12,
  goldenScore: 'all 12 scored 5 of 5, both judges',
  brokenVariants: 24,
  // GPT-5.4 scored all 24; Opus returned a verdict on 18 (6 replies cut short
  // by the provider), and 18 of 18 scored lower. Both numbers, not the better one.
  brokenLowerGpt: '24/24',
  brokenLowerOpus: '18/18 scored',
  repeatPairs: '24 of 24 repeat pairs agreed',
  twoJudges: 'claude-opus-5 and gpt-5.4',
  rowsBothScored: 70,
  exact: 57,
  withinOne: 70,
  exactPct: '81%',
  withinOnePct: '100%',
}

// --- the hard-case benchmark and the rules it changed --------------------------
// docs/phase-11.1b-report.md. Eleven rows, because two of the twelve patterns
// already live in the ordinary set. Graph-passed, arm A → arm C. The report's
// own first baseline scored 7/11; arm A is a repeat of that setup and scored
// 6/11 — the gap between two identical runs is the report's headline finding.
// Ran on gpt-5.4 throughout. Rule 28 came from a different run (Phase 9.3) and
// is not part of this measurement.
export const HARD = {
  rows: 11,
  patterns: 12,
  model: 'gpt-5.4',
  firstBaseline: '7/11',
  baselinePassed: '6/11',
  finalPassed: '9/11',
  rulesAdded: ['26', '27'],
  reserved: 'hard cases 10 and 11 were held out of the tuning loop',
  unsolved: 'hard case 10, the BasePage wait helpers, failed in all four runs',
}

// --- execution: the converted code run in a real browser, no model spend -----
// docs/phase-11.2-report.md.
export const EXECUTION = {
  baseSet: '9/12',
  // The probe run; the three tuning arms scored 9, 9 and 8 of 11. Best-of-four
  // alone would flatter, so the page shows the range.
  hardProbe: '10/11',
  hardArms: '9, 9 and 8 of 11',
  testRows: '20/20 test rows across four runs',
  pageObjectRows: '20/30 page-object rows',
  caught: 'a dropped 10 s wait budget, so a 5 s default lost the race — found twice, months apart',
  spend: 'zero tokens, about four minutes of browser time',
}

// --- the gap taxonomy, as the eval work grew it -------------------------------
// docs/gap-log.md.
export const GAPS: [string, string][] = [
  ['T4', 'async/await slips: compiles, passes wrongly'],
  ['T5', 'parity loss: tests or assertions quietly vanish'],
  ['T6', 'semantic drift: compiles, runs, does the wrong thing'],
  ['T9', 'structured-output shape failure: no code at all'],
  ['T11', 'judge reply cut short by the provider mid-rubric'],
  ['T12', 'an explicit wait longer than the default is thrown away'],
  ['T13', 'a page object cannot guess the name its caller will use'],
]
