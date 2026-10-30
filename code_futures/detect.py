"""Capability detection: which tools a repository configures, and what runs them.

No off-the-shelf tool answers "does this project lint, and which kinds of
testing does its CI actually run", and for a team that has neither those are
the two most useful findings available.

# What a survey of the organisation changed about this design

The original design read configuration from manifests and enforcement from
workflows, treating `enforced` as `present` plus a CI step. A sweep of all 96
repositories in the organisation refuted that, and the corrections below are
the reason this module looks the way it does.

**A tool can be enforced without being configured.** Six repositories set up
pylint entirely by writing `pip install pylint` in a workflow and passing every
rule as a command-line flag. Detecting from config files alone finds pylint in
three repositories; detecting from run lines finds it in seven. So the two
sources are a union rather than a hierarchy, and `_CONFIG` and `_RUN` are
consulted independently.

**Vendored trees poison every count.** One repository commits its `.venv`:
12,600 of its 12,639 files sit under `site-packages`, including 612 files
named `test_*.py`. A detector counting test files would report it as the
best-tested project in the organisation, and it has no first-party tests at
all. Another vendors an upstream ROS package carrying the most sophisticated
lint configuration anywhere in the organisation, none of it written by the
team and none of it running. `VENDORED` exists because of these two.

**A green lint step frequently enforces nothing.** `pylint *.py || true`,
`flake8 --exit-zero`, and `--fail-under=8.5` all run, all pass, and none can
fail a build. That is recorded as an attribute rather than a different
outcome: the tool runs and produces feedback, which is genuinely different
from not running, and advisory-by-default is the posture this project
recommends anyway. Whether a check should block is the subject of the
guidebook's section 7, and the report's job is to say which state you are in.

**A workflow file can be a no-op.** One repository's `pylint.yml` is twenty
lines of comments. It lists in the Actions UI and does nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Path fragments whose contents belong to somebody else.
#
# Checked as path segments rather than substrings, so a legitimate directory
# named `vendor_api` is not swallowed.
VENDORED: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "env",
        ".env",
        "site-packages",
        "node_modules",
        "vendor",
        "third_party",
        "thirdparty",
        "external",
        "dist",
        "build",
        ".tox",
        ".nox",
        "__pypackages__",
        "bower_components",
        "target",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        # Code generators emit manifests. One repository here has a
        # Prisma-generated `package.json` under `server/src/generated/prisma/`,
        # which is nobody's configuration.
        "generated",
    }
)

# --- Script indirection ---------------------------------------------------
#
# `npm run lint` is the most reliable lint signal in this organisation, present
# in 23 of 39 Node repositories. `npm test` is the least reliable thing that
# looks reliable, and resolving it is the reason this section exists.
#
# Observed `test` scripts that run no tests:
#   "test": "echo \"No tests specified\" && exit 0"
#   "test": "echo \"Error: no test specified\" && exit 1"
#   "test": "next lint"
#   "test": "jest"            (in a package with zero test files)
#
# Matching `npm test` in a workflow and recording a passing suite would
# manufacture a test suite out of an `echo`. So a script is resolved to its
# command before the command is classified.

_HOLLOW_SCRIPT = re.compile(r"^\s*echo\b|--passWithNoTests\b|\bexit\s+[01]\s*$")

# A formatter run in write mode rewrites files on a throwaway runner and
# always passes. Three repositories share a workflow doing exactly this; only
# one of them commits the result, so for the other two the job is decorative.
_WRITE_MODE = re.compile(r"\b(prettier|black|isort|ruff\s+format)\b(?!.*--check)(?=.*(--write|-w)\b)")


def resolve_script(scripts: dict[str, str], command: str) -> tuple[str, bool]:
    """Expand `npm run X` to the command X actually runs.

    Returns the resolved command and whether it does real work. A script
    resolving to `echo` or `exit 0` is hollow: the step is green and nothing
    ran.
    """
    m = re.search(r"\b(?:npm|yarn|pnpm)\s+(?:run\s+)?([\w:.-]+)", command)
    if not m:
        return command, True
    name = m.group(1)
    if name in ("install", "ci", "i", "exec", "dlx"):
        return command, True
    # `--passWithNoTests` appears on the workflow line rather than in the
    # script, so the command has to be checked as well as the body.
    if _HOLLOW_SCRIPT.search(command):
        return command, False
    body = scripts.get(name)
    if body is None:
        # `npm test` with no `test` script is a failure, not a test run.
        return command, name != "test"
    return body, not bool(_HOLLOW_SCRIPT.search(body))


def is_vendored(path: str) -> bool:
    """Whether a path sits inside somebody else's tree.

    The single most important filter in this module. Without it one
    repository's committed virtualenv makes it the best-tested project in the
    organisation.
    """
    return any(seg in VENDORED for seg in path.split("/"))


@dataclass(frozen=True)
class Signal:
    """One way of recognising that a tool is configured."""

    tool: str
    language: str
    kind: str  # lint | format | test.unit | test.e2e | test.coverage | test.mutation
    filename: str | None = None
    """Exact basename, matched anywhere outside a vendored path."""

    content_key: str | None = None
    """Literal string that must appear in the named file."""

    in_file: str | None = None
    """File the `content_key` is sought in, when it differs from `filename`."""


# --- Configuration signals ------------------------------------------------
#
# Every marker here was observed in a real repository in this organisation.
# Markers that turned up nowhere are recorded as absent at the bottom rather
# than speculatively supported, because an unexercised branch is a liability.

_CONFIG: tuple[Signal, ...] = (
    # Python — lint
    Signal("pylint", "Python", "lint", filename=".pylintrc"),
    Signal("flake8", "Python", "lint", filename=".flake8"),
    Signal("flake8", "Python", "lint", content_key="[tool.flake8]", in_file="pyproject.toml"),
    Signal("ruff", "Python", "lint", content_key="[tool.ruff]", in_file="pyproject.toml"),
    Signal("mypy", "Python", "lint", content_key="[tool.mypy]", in_file="pyproject.toml"),
    Signal("codespell", "Python", "lint", content_key="[tool.codespell]", in_file="pyproject.toml"),
    # Python — format
    Signal("black", "Python", "format", content_key="[tool.black]", in_file="pyproject.toml"),
    Signal("isort", "Python", "format", content_key="[tool.isort]", in_file="pyproject.toml"),
    Signal("ruff format", "Python", "format", content_key="[tool.ruff.format]", in_file="pyproject.toml"),
    # Python — test
    Signal("pytest", "Python", "test.unit", filename="pytest.ini"),
    Signal(
        "pytest", "Python", "test.unit", content_key="[tool.pytest.ini_options]", in_file="pyproject.toml"
    ),
    Signal("pytest", "Python", "test.unit", filename="conftest.py"),
    # Python — coverage
    Signal("coverage", "Python", "test.coverage", filename=".coveragerc"),
    Signal(
        "coverage", "Python", "test.coverage", content_key="[tool.coverage.run]", in_file="pyproject.toml"
    ),
    Signal("pytest-cov", "Python", "test.coverage", content_key="pytest-cov", in_file="requirements.txt"),
    # Node — lint
    Signal("eslint", "JavaScript", "lint", filename=".eslintrc"),
    Signal("eslint", "JavaScript", "lint", filename=".eslintrc.json"),
    Signal("eslint", "JavaScript", "lint", filename=".eslintrc.js"),
    Signal("eslint", "JavaScript", "lint", filename=".eslintrc.cjs"),
    Signal("eslint", "JavaScript", "lint", filename="eslint.config.js"),
    Signal("eslint", "JavaScript", "lint", filename="eslint.config.mjs"),
    Signal("eslint", "JavaScript", "lint", content_key='"eslintConfig"', in_file="package.json"),
    Signal("biome", "JavaScript", "lint", filename="biome.json"),
    # Node — format
    Signal("prettier", "JavaScript", "format", filename=".prettierrc"),
    Signal("prettier", "JavaScript", "format", filename=".prettierrc.json"),
    Signal("prettier", "JavaScript", "format", content_key='"prettier"', in_file="package.json"),
    # Node — test
    Signal("jest", "JavaScript", "test.unit", filename="jest.config.js"),
    Signal("jest", "JavaScript", "test.unit", filename="jest.config.ts"),
    Signal("jest", "JavaScript", "test.unit", content_key='"jest"', in_file="package.json"),
    Signal("vitest", "JavaScript", "test.unit", filename="vitest.config.ts"),
    Signal("vitest", "JavaScript", "test.unit", filename="vitest.config.js"),
    Signal("mocha", "JavaScript", "test.unit", filename=".mocharc.json"),
    # Node — e2e
    Signal("playwright", "JavaScript", "test.e2e", filename="playwright.config.ts"),
    Signal("playwright", "JavaScript", "test.e2e", filename="playwright.config.js"),
    Signal("cypress", "JavaScript", "test.e2e", filename="cypress.config.js"),
    Signal("cypress", "JavaScript", "test.e2e", filename="cypress.config.ts"),
    Signal("cypress", "JavaScript", "test.e2e", filename="cypress.json"),
    # Node — mutation
    Signal("stryker", "JavaScript", "test.mutation", filename="stryker.conf.json"),
    # Java
    Signal("checkstyle", "Java", "lint", filename="checkstyle.xml"),
    Signal("checkstyle", "Java", "lint", content_key="checkstyle", in_file="build.gradle"),
    Signal("pmd", "Java", "lint", content_key="pmd", in_file="build.gradle"),
    Signal("spotbugs", "Java", "lint", content_key="spotbugs", in_file="build.gradle"),
    Signal("spotless", "Java", "format", content_key="spotless", in_file="build.gradle"),
    Signal("junit", "Java", "test.unit", content_key="junit", in_file="pom.xml"),
    Signal("junit", "Java", "test.unit", content_key="junit", in_file="build.gradle"),
    Signal("jacoco", "Java", "test.coverage", content_key="jacoco", in_file="build.gradle"),
    Signal("jacoco", "Java", "test.coverage", content_key="jacoco", in_file="pom.xml"),
    # Rust
    Signal("clippy", "Rust", "lint", filename="clippy.toml"),
    Signal("clippy", "Rust", "lint", content_key="[lints", in_file="Cargo.toml"),
    Signal("clippy", "Rust", "lint", content_key="clippy", in_file="rust-toolchain.toml"),
    Signal("rustfmt", "Rust", "format", filename="rustfmt.toml"),
    Signal("rustfmt", "Rust", "format", filename=".rustfmt.toml"),
    Signal("rustfmt", "Rust", "format", content_key="rustfmt", in_file="rust-toolchain.toml"),
    Signal("cargo-mutants", "Rust", "test.mutation", filename=".cargo/mutants.toml"),
    # Language-agnostic: pre-commit is configuration rather than enforcement,
    # because it only runs for a developer who installed it.
    Signal("pre-commit", "Python", "lint", filename=".pre-commit-config.yaml"),
)

# Markers deliberately kept despite matching nothing in this organisation:
# checkstyle, PMD, SpotBugs, spotless, JaCoCo, TestNG, tarpaulin and
# cargo-mutants appear in zero build files here -- their only occurrences
# anywhere in the org are in the handbooks that recommend them. They stay
# because this tool is meant to be usable against any repository, not only
# these. They are untested branches until a repository exercises them, which
# is worth knowing when one of them first fires.

# --- Run-line patterns ----------------------------------------------------
#
# Matched against the literal `run:` strings in workflow files. Every pattern
# below was written against commands actually observed in this organisation,
# which is why several look oddly specific: `uv run pytest` does not match a
# naive `^pytest`, and `cd server && PYTHONPATH=$(pwd) pytest` matches neither.
#
# Order matters only in that the first match supplies the reported tool name.

_RUN: tuple[tuple[str, str, str, str], ...] = (
    # (regex, tool, language, kind)
    # Python — lint
    (r"\bpylint\b", "pylint", "Python", "lint"),
    (r"\bflake8\b", "flake8", "Python", "lint"),
    (r"\bruff\s+check\b", "ruff", "Python", "lint"),
    (r"\bmypy\b", "mypy", "Python", "lint"),
    (r"\bcodespell\b", "codespell", "Python", "lint"),
    (r"\bbandit\b", "bandit", "Python", "lint"),
    # `pre-commit run` is an aggregate: it invokes whatever the config lists,
    # which in practice here is a formatter and a linter at once.
    (r"\bpre-commit\s+run\b", "pre-commit", "Python", "lint"),
    # Python — format
    (r"\bruff\s+format\b", "ruff format", "Python", "format"),
    (r"\bblack\b", "black", "Python", "format"),
    (r"\bisort\b", "isort", "Python", "format"),
    (r"\bpre-commit\s+run\b", "pre-commit", "Python", "format"),
    # Python — test. Covers bare, `python -m`, `uv run`, and `cd X && ...`.
    (r"\bpytest\b", "pytest", "Python", "test.unit"),
    (r"\bunittest\s+discover\b", "unittest", "Python", "test.unit"),
    (r"\bcoverage\s+run\b", "coverage", "Python", "test.coverage"),
    (r"--cov\b", "pytest-cov", "Python", "test.coverage"),
    # Node
    (r"\b(npm|yarn|pnpm)\s+run\s+(check:)?lint\b", "eslint", "JavaScript", "lint"),
    (r"\bnpx?\s+eslint\b", "eslint", "JavaScript", "lint"),
    (r"\beslint\b", "eslint", "JavaScript", "lint"),
    (r"\bbiome\s+(check|lint)\b", "biome", "JavaScript", "lint"),
    (r"\bnext\s+lint\b", "next lint", "JavaScript", "lint"),
    # Type checking is a distinct discipline that reads as linting to a team,
    # and five repositories run it as their only static check.
    (r"\b(npm|yarn|pnpm)\s+run\s+(check:)?type(check|s|script)\b", "tsc", "JavaScript", "lint"),
    (r"\btsc\s+--noEmit\b", "tsc", "JavaScript", "lint"),
    # The format script is spelled five different ways here.
    (
        r"\b(npm|yarn|pnpm)\s+run\s+(format|fmt|prettier:check|format:check)\b",
        "prettier",
        "JavaScript",
        "format",
    ),
    (r"\bprettier\b", "prettier", "JavaScript", "format"),
    (r"\bbiome\s+format\b", "biome", "JavaScript", "format"),
    (r"\b(npm|yarn|pnpm)\s+(run\s+)?test\b", "npm test", "JavaScript", "test.unit"),
    (r"\bjest\b", "jest", "JavaScript", "test.unit"),
    (r"\bvitest\b", "vitest", "JavaScript", "test.unit"),
    (r"\bmocha\b", "mocha", "JavaScript", "test.unit"),
    # Create React App and craco run Jest invisibly: no jest config, no jest
    # dependency, nothing a file-based detector can see.
    (r"\b(react-scripts|craco)\s+test\b", "react-scripts test", "JavaScript", "test.unit"),
    # The Node built-in runner. One repository uses it with a tsx loader and
    # has no test framework in its dependencies at all.
    (r"\bnode\s+.*--test\b", "node --test", "JavaScript", "test.unit"),
    (r"\bplaywright\s+test\b", "playwright", "JavaScript", "test.e2e"),
    (r"\bcypress\s+run\b", "cypress", "JavaScript", "test.e2e"),
    (r"--coverage\b", "coverage", "JavaScript", "test.coverage"),
    # Java. The aggregate-task problem lives here: `./gradlew check` runs
    # tests and checkstyle and spotless without naming any of them, and
    # `mvn verify` triggers whatever plugins are bound. Matching the aggregate
    # as a test invocation is the least-wrong reading, and the report says
    # which command it saw so a reader can judge.
    (r"\bgradlew?\s+.*\bspotlessCheck\b", "spotless", "Java", "format"),
    (r"\bgradlew?\s+.*\bcheckstyle", "checkstyle", "Java", "lint"),
    (r"\bgradlew?\s+.*\b(check|build)\b", "gradle check", "Java", "test.unit"),
    (r"\bmvn\b.*\b(test|verify|package)\b", "maven", "Java", "test.unit"),
    (r"\bgradlew?\s+.*\bjacoco", "jacoco", "Java", "test.coverage"),
    # `assembleRelease`, `shadowJar`, `javadoc` and `publish` are deliberately
    # absent above. `assemble` and `check` are siblings under `build`, so
    # `./gradlew assembleRelease` runs no tests and no verification. Treating
    # every Gradle aggregate as a test run produces a false positive on the
    # one Android repository here, which is why the pattern names `build` and
    # `check` rather than matching `gradlew` generally.
    # Rust
    (r"\bcargo\s+clippy\b", "clippy", "Rust", "lint"),
    (r"\bcargo\s+fmt\b", "rustfmt", "Rust", "format"),
    (r"\bcargo\s+(test|nextest)\b", "cargo test", "Rust", "test.unit"),
    (r"\bcargo\s+(tarpaulin|llvm-cov)\b", "tarpaulin", "Rust", "test.coverage"),
    (r"\bcargo\s+mutants\b", "cargo-mutants", "Rust", "test.mutation"),
)

_COMPILED = tuple((re.compile(rx), tool, lang, kind) for rx, tool, lang, kind in _RUN)

# A step that cannot fail the build. All four forms were observed here.
_NON_BLOCKING = re.compile(r"\|\|\s*true\b|--exit-zero\b|--fail-under=|\|\|\s*echo\b")

# Integration suites are named rather than structurally distinct, so this is
# the weakest inference in the module and is treated as a hint.
_INTEGRATION = re.compile(r"\bintegration\b|-m\s+[\"']?integration|\bit\b:", re.I)


@dataclass
class Finding:
    """One detected capability, before it becomes a ProbeResult."""

    kind: str
    language: str
    tool: str
    config: str | None = None
    invocation: str | None = None
    command: str | None = None
    blocking: bool = True
    evidence: list[str] = field(default_factory=list)

    @property
    def enforced(self) -> bool:
        return self.invocation is not None


def detect_config(paths: dict[str, str]) -> list[Finding]:
    """Findings from manifests and config files.

    `paths` maps repository-relative path to file contents. Vendored paths are
    dropped before anything is matched.
    """
    live = {p: c for p, c in paths.items() if not is_vendored(p)}
    out: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()

    for sig in _CONFIG:
        for path, content in live.items():
            base = path.rsplit("/", 1)[-1]
            if sig.filename and base != sig.filename:
                continue
            if sig.in_file and base != sig.in_file:
                continue
            if sig.content_key and sig.content_key not in content:
                continue
            key = (sig.kind, sig.language, sig.tool)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Finding(
                    kind=sig.kind,
                    language=sig.language,
                    tool=sig.tool,
                    config=path,
                    evidence=[path],
                )
            )
            break
    return out


def _strip_comments(text: str) -> str:
    """Remove whole-line comments.

    One repository's `pylint.yml` is twenty lines of `#`. It lists in the
    Actions UI and runs nothing, and without this it would read as a lint
    workflow.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def run_lines(workflow: str) -> list[str]:
    """Every shell command a workflow runs.

    Deliberately textual rather than a YAML parse. Workflow `run:` blocks are
    scalars containing shell, multi-line continuations are common, and several
    files here are not valid YAML once comments are stripped. Reading the lines
    is more robust than modelling the document.
    """
    body = _strip_comments(workflow)
    out: list[str] = []
    in_block = False
    indent = 0
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(\s*)-?\s*run:\s*(\|[-+]?|>[-+]?)?\s*(.*)$", line)
        if m:
            lead, block, rest = m.group(1), m.group(2), m.group(3)
            if rest:
                out.append(rest)
            in_block = bool(block)
            indent = len(lead)
            continue
        if in_block:
            cur = len(line) - len(line.lstrip())
            if cur > indent:
                out.append(line.strip())
            else:
                in_block = False
    # Join shell continuations so a multi-line pytest invocation reads as one.
    joined: list[str] = []
    for cmd in out:
        if joined and joined[-1].endswith("\\"):
            joined[-1] = joined[-1][:-1].rstrip() + " " + cmd
        else:
            joined.append(cmd)
    return joined


def detect_runs(workflows: dict[str, str], scripts: dict[str, str] | None = None) -> list[Finding]:
    """Findings from workflow invocations.

    Consulted independently of `detect_config`, not as a second layer over it.
    Six repositories in this organisation install and run a linter purely from
    a workflow, with no configuration file anywhere.

    `scripts` is the merged `scripts` block from the repository's
    `package.json` files. Supplying it is what stops `npm test` being recorded
    as a test run when the script behind it is an `echo`.
    """
    scripts = scripts or {}
    out: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()

    for path, text in workflows.items():
        if is_vendored(path):
            continue
        for cmd in run_lines(text):
            resolved, real = resolve_script(scripts, cmd)
            if not real:
                # The step is green and nothing ran. Recording it would
                # manufacture a capability out of an `echo`.
                continue
            # Classify against the expansion when a script resolved, and the
            # raw command otherwise. Searching both would misread the wrapper:
            # one repository maps `"test": "next lint"`, and a union haystack
            # containing `npm test` records a unit-test run that never
            # happened. What the script does wins over what it is called.
            haystack = resolved if resolved != cmd else cmd
            for rx, tool, lang, kind in _COMPILED:
                if not rx.search(haystack):
                    continue
                if kind == "test.unit" and _INTEGRATION.search(haystack):
                    kind = "test.integration"
                key = (kind, lang, tool)
                if key in seen:
                    continue
                seen.add(key)
                # A formatter in write mode rewrites files on a throwaway
                # runner and always passes, so it gates nothing.
                blocking = not (_NON_BLOCKING.search(haystack) or _WRITE_MODE.search(resolved))
                out.append(
                    Finding(
                        kind=kind,
                        language=lang,
                        tool=tool,
                        invocation=path,
                        command=cmd,
                        blocking=blocking,
                        evidence=[path],
                    )
                )
    return out


def package_scripts(paths: dict[str, str]) -> dict[str, str]:
    """Merge every non-vendored `package.json` scripts block.

    Fails soft on malformed JSON. One `package.json` in this organisation is
    missing a comma and will not parse; a detector that raised there would
    lose the whole repository over a typo somebody will fix next week.
    """
    import json

    out: dict[str, str] = {}
    for path, content in paths.items():
        if is_vendored(path) or not path.endswith("package.json"):
            continue
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            continue
        scripts = data.get("scripts")
        if isinstance(scripts, dict):
            for k, v in scripts.items():
                if isinstance(v, str):
                    out.setdefault(k, v)
    return out


def merge(config: list[Finding], runs: list[Finding]) -> list[Finding]:
    """Combine both sources into one finding per (kind, language, tool).

    A run-line match wins on invocation, a config match wins on config path,
    and either alone is sufficient. That union is the correction the
    organisation survey forced: neither source is complete, and treating
    configuration as a prerequisite for enforcement loses four of the seven
    repositories that lint.
    """
    by_key: dict[tuple[str, str, str], Finding] = {}
    for f in config + runs:
        key = (f.kind, f.language, f.tool)
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = Finding(**{**f.__dict__, "evidence": list(f.evidence)})
            continue
        cur.config = cur.config or f.config
        cur.invocation = cur.invocation or f.invocation
        cur.command = cur.command or f.command
        cur.blocking = cur.blocking and f.blocking
        for e in f.evidence:
            if e not in cur.evidence:
                cur.evidence.append(e)
    return list(by_key.values())
