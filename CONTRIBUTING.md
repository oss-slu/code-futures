# Contributing to Code Futures

Thanks for looking. This document covers how to set up, how to propose a change, and what review looks like.

## Setting up

Python 3.11 or later.

```bash
git clone https://github.com/oss-slu/code-futures && cd code-futures
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Optional, and only needed for the probes that use them:

- [OpenSSF Scorecard](https://github.com/ossf/scorecard) on your `PATH`
- A SonarQube Cloud token, per [`docs/credentials.md`](docs/credentials.md)

Without either, those probes report `unreadable` and everything else works.

## Before you push

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
```

CI runs the same three. Unlike the reusable workflow this project offers other teams, **these block** — we are the maintainers here and we chose them.

## Proposing a change

Branch from `main`, open a pull request. `main` is protected: one approving review, conversations resolved.

Branch names follow the program convention: `feature-*`, `bugfix-*`, `docs-*`.

## What review looks for

### Does a failure stay a failure?

The rule the whole design rests on: **a source that could not be read produces `unreadable`, never `absent`.** These reports are read by the teams they describe, and rendering our failure as their deficiency is the worst thing this tool can do. Every collector routes through that discipline, and a change that adds a path from "the request failed" to a statement about a repository will be sent back.

### Is a new finding real?

The surest way to be wrong here is a heuristic that looks sensible and misreads real repositories. Some ways that has already happened:

- A repository commits its `.venv`, so counting test files made it the best-tested project in the organisation.
- `npm test` is frequently an `echo`, so matching it manufactured a test suite that does not exist.
- `./gradlew build` runs tests without the word "test" appearing anywhere; `./gradlew assembleRelease` runs none.
- Six repositories configure a linter entirely inside a workflow, with no config file at all.

If you add a detection rule, say which real repository shape it came from.

### Prose changes are behaviour changes

Every sentence a report can contain lives in `code_futures/strings.py`. Nothing is model-generated. Editing a template changes every report that renders it, which is the point and also the risk. Findings are next steps, absences are opportunities, and nothing ranks anybody against anybody.

### Tests

Required on anything that parses, resolves, or reads a boundary, because wrongness there is silent. A resolver that returns the wrong answer does not crash; it quietly misreports somebody's repository.

Every bug gets a test that fails before the fix.

The worked example is renderer output, and a test asserts the committed file still matches. If your change alters rendering, regenerate it:

```bash
python examples/fixture.py
```

## Reporting a wrong finding

**The most useful issue you can file.** If a report says something untrue about a repository, that is a bug in the tooling and not a dispute about the codebase. Include the repository, the probe, what it said, and what is actually the case.

Findings marked `unreadable` that you believe we could have read are worth reporting too.

## Where things live

| Module | Holds |
|---|---|
| `schema.py` | The `code-futures/v1` contract. Changing it is a version bump |
| `strings.py` | Every sentence a report can contain |
| `detect.py` | Capability detection |
| `structure.py` | Git-history analysis |
| `collectors.py` | One function per evidence source |
| `observe.py` | What a CI run watched happen |
| `render.py` | Report to Markdown. **Never called from CI** |

That last line is a constraint rather than a note. CI writes an artifact of public facts; the composed report is internal and assembled elsewhere. Do not add a render call to the CI path.

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
