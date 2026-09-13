# Contributing

Thanks for looking. This project is an AI agent that rewrites TypeScript
Selenium suites in Playwright and proves the result compiles before anyone
sees it. Pull requests are welcome, from a typo to a new gate.

## Set up

You need Python 3.12, [uv](https://docs.astral.sh/uv/), and Node 22.

```sh
git clone https://github.com/varunbhatt2193/selenium2playwright && cd selenium2playwright
uv sync --group ui              # Python deps, including what the playground's tests import
(cd sandbox && npm ci)          # the pinned TypeScript compiler and ESLint the gates run
cp .env.example .env            # only needed for live runs, see below
```

## Run the tests

```sh
uv run --group ui python -m unittest discover -s tests
```

This needs no API key and spends no tokens. Every model reply in the suite is
scripted. The gates are real, though: `tsc`, ESLint and the parity scripts run
against the output, which is why the `sandbox` install above matters. It is the
same command CI runs.

To convert a real file you need a model key in `.env`: `ANTHROPIC_API_KEY`, or
`OPENAI_API_KEY` with `S2P_MODEL=openai:gpt-5.4`. Set `S2P_EMBEDDINGS=off` if
you have no OpenAI key. Everything else is described in
[docs/config.md](docs/config.md).

## Find something to work on

- Issues labelled [good first issue](https://github.com/varunbhatt2193/selenium2playwright/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
  are small and self-contained.
- Issues labelled [help wanted](https://github.com/varunbhatt2193/selenium2playwright/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
  are bigger, and help is welcome.
- [docs/gap-log.md](docs/gap-log.md) lists conversion cases the agent gets
  wrong, with the evidence.
- [docs/prevention-backlog.md](docs/prevention-backlog.md) lists reliability
  work, sized and written to be picked up cold.

If you want to build something not listed, open an issue first so we can agree
on the shape before you spend time on it. Questions go in
[Discussions](https://github.com/varunbhatt2193/selenium2playwright/discussions).

## Send a pull request

1. Branch from `main`. `main` only accepts pull requests, and six checks must
   pass: the offline suite, two browser execution gates, the playground build,
   and CodeQL for Python and TypeScript.
2. Keep it focused. One change per PR is easier to review and easier to revert.
3. Add or update tests with the code. The offline suite is the contract.
4. Fill in the PR template: what changed, why, and how you checked it.
5. A PR from a fork runs the full CI. It needs no secrets, so nothing is held
   back from outside contributors.

## Rules you cannot guess from the code

- **Never commit `.env` or any key.** Secret scanning and push protection are
  on and will block the push.
- **The goldens and dataset fixtures are frozen.** The files under
  `samples/playwright-golden`, `samples/selenium-suite` and
  `samples/selenium-hard-suite` have their SHA-256 recorded inside published
  evaluation datasets. Changing one silently changes what every published
  number means. Add a new fixture instead of editing an old one.
- **`docs/playbook.md` is the model's rulebook, and it is gated by
  evaluation.** A prompt change ships only after an A/B run on the evaluation
  set shows it helps (`scripts/compare_prompt_ab.py`). That needs a model key
  and costs a few dollars. Open an issue with the proposed rule and the failing
  case, and a maintainer can run the comparison.
- **Every gate is deterministic on purpose.** Do not add a model call inside
  `src/selenium2playwright/validators/`.
- **Vendor-specific code lives in `src/selenium2playwright/llm.py` only.**
  Everything else goes through LangChain abstractions, so the model stays
  swappable with `S2P_MODEL`.

## License

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE), the same as the project. There is no contributor
agreement to sign.
