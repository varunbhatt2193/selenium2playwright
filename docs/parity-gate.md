# Step 4.4 — parity gate walkthrough

The compiler accepts a test whose assertion was deleted. This gate compares the
original and converted files to make that loss visible before the critic runs.
Implementation, automated checks, and the learning review are complete.
Connecting the four gates to the graph is step 4.5.

## Interface

```python
parity_check(
    source_files: dict[str, str],
    converted_files: dict[str, str],
) -> ValidationReport
```

Keys are matching relative paths, such as `tests/login.spec.ts`. Values are file
contents. This lets companion files travel together, as in the other gates.

## Part 1: inventory the syntax

[`sandbox/parity.cjs`](../sandbox/parity.cjs) uses the TypeScript version already
pinned for compilation. `createSourceFile` parses text; `forEachChild` visits the
syntax tree. Neither submitted file is imported or executed. The official
[TypeScript compiler API guide](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API#traversing-the-ast-with-a-little-linter)
explains these operations.

1. `inventory` creates a tree and collects source locations and parsing issues.
2. `callee` recognizes common test/assertion names and import aliases.
3. `visit` carries enclosing suite names and the current test down the tree.
   Assertions in nested callbacks belong to that test. Comments, strings, and
   regular-expression literals cannot impersonate calls.
4. The script reads both file dictionaries as JSON on stdin and returns JSON
   inventories. It needs no temporary files or new dependency.

We tried regex first. It counted a deleted assertion preserved in a comment and
mistook a nested callback's closing brace for the end of its test. These concrete
failures triggered the roadmap's AST fallback.

## Part 2: compare the inventories

[`validators/parity.py`](../src/selenium2playwright/validators/parity.py) runs the
inventory helper once, then compares each file:

- A missing counterpart produces `missing-file`.
- Tests match by suite path and title. A queue retains duplicate occurrences;
  a set would hide the deletion of one duplicate. Loss or renaming produces
  `missing-test`; changing an active test to skipped/pending produces `disabled-test`.
- `compare_assertions` checks each matched test separately, plus one file-level
  pool for assertions outside tests. A drop produces `missing-assertion`.
- Syntax errors, dynamic titles, `.each`, and callbacks supplied by reference
  produce `unverified-parity` so unsupported shapes do not receive a clean pass.

Loss findings point into the **source**, because the missing code has no output
location. An assertion-count drop names the affected test and lists its original
assertions for review. It does not guess which expression corresponds to which
rewritten Playwright assertion.

## Run and review

```sh
uv run python -m selenium2playwright.validators.parity samples/selenium-suite samples/playwright-golden
uv run python -m unittest discover -s tests -v
```

The CLI also accepts two individual files, even if their filenames differ.
It exits 0 on pass, 1 on findings, and 2 for invalid CLI arguments.

Verified: the golden passes all four standalone gates. Deleting its invalid-login
assertion still passes compilation, but parity reports `missing-assertion` for
`Login > rejects invalid credentials`, pointing to source line 33. The 12 offline
regression tests also cover duplicate titles, import aliases, misleading comments,
skips, nested callbacks, missing files, and unverifiable shapes.

## Limits

These are static declaration and assertion counts. They do not prove runtime
case counts, callback execution, or equivalent expected values. Loops are not
expanded, and custom test wrappers or locally shadowed API names require review;
this visitor does not resolve symbols or follow helper calls. Assertions outside
tests share a per-file pool, so moving them between helpers is not distinguished.
Moving them into tests can conservatively flag a drop in that pool.

POM method removal is allowed here: the public-member kept/renamed/removed ledger
belongs to step 9.3. The critic and execution evals still own semantic correctness.
