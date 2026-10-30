# Code Futures

Given a repository, say what state its engineering practice is in.

Code Futures reads a codebase and reports on its **structure** — where complexity and churn concentrate, which files keep changing together, where a module boundary is leaking — and on the **practice** around it: linting, testing, review gates, dependency hygiene, open-source baseline.

It produces a report per repository and a machine-readable snapshot. It knows nothing about courses, teams, or people.

## On the name

After Kent Beck's distinction in *Tidy First?* between the two kinds of value in software. **Features** are what it does now. **Futures** are what remains possible later.

Not one probe here is a feature. A test suite, a branch gate, a linter, a licence — none of them do anything for a user today, and every one of them decides what you can still change next semester. That is also why they are so easy to postpone: nothing bad happens the week you skip them.

**Companion guidebook**: [`oss-slu/guidebook_code_futures`](https://github.com/oss-slu/guidebook_code_futures) explains what each finding means, what it would be wrong to conclude, and what to do about it. Read that before acting on a report.

---

## Installation

Python 3.11 or later.

```bash
pip install git+https://github.com/oss-slu/code-futures
```

Then, on any repository you have checked out:

```bash
cd /path/to/your/repo
code-futures self
```

That runs entirely locally. No token, no network, nothing to set up — structure comes from your git history, capability detection from your files.

Optional: [OpenSSF Scorecard](https://github.com/ossf/scorecard) on your `PATH` enables the security probes. Without it they report `unreadable`.

## Tech Stack

| | |
|---|---|
| **Language** | Python 3.11+ |
| **Dependencies** | PyYAML. Everything else is standard library |
| **GitHub access** | The `gh` CLI as a subprocess — it already handles auth, pagination and SAML SSO |
| **Git analysis** | `git log` via subprocess; a blobless clone for remote repositories |
| **External tools** | OpenSSF Scorecard (optional), SonarQube Cloud (optional) |
| **CI** | GitHub Actions |
| **Tests** | pytest |
| **Lint and format** | ruff |

---

## The four outcomes

Every finding is one of four values. This is a vocabulary, not a scale, and there is no arithmetic you can do on it.

| Outcome | Means | Who it is about |
|---|---|---|
| `enforced` | In place, and CI runs it | the repository |
| `present` | In the repository, but nothing runs it automatically | the repository |
| `absent` | We looked, and it is not there | the repository |
| `unreadable` | We could not find out | **us** |

`present` is not a worse `enforced`. A repository with a complete test suite that no workflow invokes is one file away from having CI; one with no tests is weeks away. Collapsing those loses the most actionable finding the tool produces.

`unreadable` is a statement about the tooling. A check that timed out is reported as our failure, never as your gap.

---

## Running it

### On a repository you have locally

```bash
code-futures self                      # report to stdout
code-futures self -o report.md         # to a file
code-futures self --remote             # also run Scorecard and SonarQube
```

`--remote` needs credentials; everything else does not.

### On a repository you do not have locally

```bash
code-futures doctor                    # always run this first
code-futures probe oss-slu/some-repo
```

### Across a whole organisation

```bash
code-futures doctor
code-futures sweep                     # writes evidence/<term>/code-futures.json
code-futures render evidence/fall-2026/code-futures.json -o reports/
```

Budget real time. Scorecard takes one to two minutes per repository and the structural analysis clones history for each, so thirty repositories is plausibly an hour.

### Why `doctor` is not optional

`sweep` refuses to run until `doctor` passes, and the reason is specific.

`oss-slu` enforces SAML SSO. A token with correct scopes that has **not** been SSO-authorised returns 403 on every repository. Nothing about that looks like an authentication failure from the inside: every check comes back empty, every probe reports a gap, and the sweep renders as *every team failed everything*. The output is complete, plausible, and entirely wrong.

This has happened once already on a different pipeline. `doctor` exists so it cannot happen again silently.

---

## Configuration

Only needed for Scorecard and SonarQube. Structure, capability detection and the baseline need nothing.

```bash
mkdir -p ~/.config/code-futures
cp config.example.toml ~/.config/code-futures/config.toml
chmod 600 ~/.config/code-futures/config.toml
```

Credentials live in `~/.config` or the environment, never beside source. `config.toml` is gitignored here as a second line of defence.

| Config key | Environment variable | Needed for |
|---|---|---|
| `github.token` | `GITHUB_TOKEN` | everything; falls back to `gh auth token` |
| `sonar.token` | `SONAR_TOKEN` | the Code Quality Analysis section |
| `sonar.organization` | `SONAR_ORGANIZATION` | same |

**[`docs/credentials.md`](docs/credentials.md) has the full walkthrough** — generating each token, the SSO authorisation step, and setting up the SonarQube Cloud organisation.

Without a Sonar token, those probes report `unreadable` and every other section is unaffected. That is the designed degradation, not a failure.

---

## Adding it to your repository's CI

Add **one file**. This is what a team pastes:

```yaml
# .github/workflows/code-futures.yml
name: Code Futures

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  security-events: write

jobs:
  code-futures:
    uses: oss-slu/code-futures/.github/workflows/code-futures.yml@v1.0.0
    secrets: inherit
```

### The four things to know about that block

**Copy the `permissions:` block exactly.** Omitting it is the most likely reason a first attempt fails. A reusable workflow cannot grant itself more permission than the workflow calling it, so `security-events: write` must be declared *in your repository*. Without it the security scanning job fails, and the error does not obviously point back here.

**Nothing will fail your build.** Every job is advisory. A team whose build breaks the day they adopt a quality tool learns that quality tools are an obstacle, and they would not be wrong to conclude that. Making a check blocking is your decision — section 7 of the guidebook covers what it costs.

**There is no stack to declare.** Each job looks for its own manifest and skips when it finds none. A repository that is Python *and* React gets both toolchains rather than having to pick one.

**Pin the exact version.** `@v1.0.0` is immutable and will never change under you. The common convention of pinning a moving major tag like `@v1` is avoided here deliberately: a moving tag silently changes the CI of every repository using it, and means nobody can say what ran last Tuesday. The cost is that fixes need you to bump the tag, and you will be told when there is one.

### What the workflow does

| Job | Runs |
|---|---|
| `detect` | Looks for a manifest of each kind, anywhere in the tree |
| `python` | `ruff` lint, format check, `pytest` — skipped with no Python manifest |
| `node` | Your own `lint` and `test` scripts — skipped with no `package.json` |
| `java` | `./gradlew build` or `mvn test` |
| `rust` | `cargo clippy`, `cargo fmt --check`, `cargo test` |
| `sast` | CodeQL, for the languages it finds |
| `summarize` | Writes `code-futures.json` and a summary table |

### What it does *not* do

**It does not produce a report in CI.** It writes an artifact of facts, every one already derivable by anyone who can clone your repository. The composed report — with what findings mean and what to do about them — is assembled outside CI and reaches your tech lead.

That distinction is why the last job is called `summarize` rather than `report`. CI output on a public repository is public: logs and job summaries are readable with no account. Keeping the renderer out of that path is the mechanism, not a policy.

---

## How it works

### Structure, from git history

Hotspots and change coupling come from `git log` alone. The technique is Adam Tornhill's, from *Your Code as a Crime Scene*.

**Change coupling** is Beck's definition of coupling made measurable: the probability that changing one thing forces you to change another, counted from history rather than inferred from imports. Two files in different languages with no import between them, that change together four commits in five, are coupled — and no static analyser will see it.

**Cohesion** is the same measurement read against module boundaries. A coupled pair inside one module is cohesion working; a pair spanning two is a boundary that is not holding.

**Blast radius** is the distribution of files per commit. Beck's cost argument runs `cost(software) ≈ cost(change) ≈ cost(big change)`, and coupling is what makes a change big, so the gap between your median change and your largest tenth is the measurement that matters.

**Tidy first** answers the question mark in Beck's title. It is not the worst file in your repository; it is the file where tidying would pay back soonest, because you are already in it regularly.

Complexity is approximated by indentation depth rather than parsed per language. An approximation, and it works identically across every language.

### Capability detection, in two sources

Manifests say what is *configured*. Workflow files say what is *enforced*. These are consulted as a **union, not a hierarchy** — a tool can be enforced without being configured, and six repositories in this organisation set up a linter entirely inside a workflow with no config file anywhere.

### Measurements, not scores

The reports carry numbers — coverage, complexity, duplication, coupling degrees — each with its units and population. A measurement is data. A score is data collapsed against somebody's threshold with the reasoning hidden.

Two numbers are deliberately absent. The OpenSSF Scorecard's 0–10 aggregate is calibrated for widely-depended-upon libraries and measures a population these projects are not in — a validity problem rather than an objection to numbers. And a maintainability letter grade is the collapse itself.

Repositories are never ranked against each other.

---

## Development

```bash
git clone https://github.com/oss-slu/code-futures && cd code-futures
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

| Module | Holds |
|---|---|
| `schema.py` | The `code-futures/v1` contract. Changing it is a version bump |
| `strings.py` | Every sentence a report can contain |
| `detect.py` | Capability detection |
| `structure.py` | Git-history analysis |
| `collectors.py` | One function per evidence source |
| `observe.py` | What a CI run watched happen |
| `render.py` | Report to Markdown. Never called from CI |

**Reports are rendered from deterministic templates, not generated by a model.** Four reasons: a false statement in a team-facing document is something a team has to argue with, and a template is reviewed once rather than once per repository; comparability needs identical phrasing; the CI path has no model access; and the same snapshot produces the same bytes, so a report diffs against its own past.

Changing wording in `strings.py` changes every report. That is the point.

**`examples/tidewater-atlas.md` is renderer output**, generated by `examples/fixture.py`, with a test asserting the committed file still matches. The guidebook walks a reader through that exact document. Regenerate with `python examples/fixture.py`.

### One dependency

PyYAML, to read the orchestration tooling's practice snapshot. Everything else is standard library. GitHub access goes through the `gh` CLI, which already handles auth, pagination and SAML SSO, so no HTTP client is needed.

### Known gaps

- **Vendored upstream source is not detected.** No path convention distinguishes it, and guessing risks excluding real source, which is the worse error.
- **Several config markers are unexercised.** Checkstyle, PMD, SpotBugs, Spotless, JaCoCo, tarpaulin and cargo-mutants match nothing in this organisation. Kept for portability.
- **`structure.duplication` and `structure.complexity` need SonarQube.** They report `unreadable` without it.

---

## Licence

MIT. See [`LICENSE`](LICENSE).
