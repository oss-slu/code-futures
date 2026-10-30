"""Structural analysis from git history.

# Why this exists, and why it nearly did not

The rest of this package reports on *practice*: does a linter run, is review
required, is there a licence. All useful, all about process, and none of it
says anything about the code. A team can pass every other probe and still be
working in a Big Ball of Mud.

This module is the part that looks at structure, and the distinction it keeps
is the one the design plan blurred. **A structural fact is not a quality
grade.** "These two files changed together in 80% of commits that touched
either" is an observation a team can verify and act on. "Your maintainability
is a C" is a vendor's model wearing a letter. The first belongs in a report;
the second does not.

# The method, and why it needs no service

Both findings come from `git log` alone. The technique is Adam Tornhill's,
from *Your Code as a Crime Scene* -- CodeScene is his hosted implementation of
it, which is why the design plan reached for CodeScene and then deferred it as
too expensive to onboard. The analysis itself is not expensive. A blobless
clone and a log parse give you both findings with no account, no per-project
setup, and nothing to switch on.

**Change coupling is the empirical form of Beck's definition.** Coupling is
the probability that changing one element forces you to change another, and
that is a quantity you can measure directly from history rather than infer
from imports. Two files with no import relationship that always change
together are coupled, and a static analyser will never see it.

**Hotspots are complexity times change frequency.** Complicated code nobody
touches is cheap; simple code that changes constantly is cheap. The expensive
intersection is where both are high, and that is where a team's next bug is
going to come from.

# The honest caveats, which belong in the report rather than here

Hotspots need history to mean anything. A project started in August mostly
reports that recently-written files were recently written, and the report says
so rather than presenting a weak signal as a strong one. Change coupling is
the more durable finding on a young repository: which files keep changing
together is visible after one semester.

Complexity is approximated by indentation rather than parsed per language.
That is deliberate -- it is Tornhill's own proxy, it works identically across
every language in the portfolio, and a real parser for six languages would be
a project rather than a module. It is an approximation and the guidebook says
so.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .detect import is_vendored

# Commits touching more than this many files are excluded from coupling.
#
# A bulk reformat, a dependency bump, or an initial import couples every file
# it touches to every other, which swamps the real signal. Thirty is generous
# enough to keep genuine cross-cutting changes and tight enough to drop the
# "added 400 files" commit that would otherwise dominate every result.
BULK_COMMIT_FILES = 30

# A pair must co-change at least this often before it is reported. Two files
# that changed together twice are a coincidence.
MIN_SUPPORT = 4

# Degree above which a pair is worth a team's attention.
COUPLING_THRESHOLD = 0.6

# Below this, hotspot ranking is noise rather than signal.
MIN_COMMITS_FOR_HOTSPOTS = 40

# Paths that are committed but not authored: lockfiles, generated clients,
# minified bundles. They change constantly and couple to everything.
_NOISE = re.compile(
    r"(^|/)("
    r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Cargo\.lock|"
    r"go\.sum|Gemfile\.lock|composer\.lock|"
    r".*\.min\.(js|css)|.*\.bundle\.js|.*\.map|.*\.svg|.*\.png|.*\.jpg|.*\.pdf|.*\.ipynb"
    r")$"
)


def is_noise(path: str) -> bool:
    return bool(_NOISE.search(path)) or is_vendored(path)


@dataclass
class Commit:
    sha: str
    files: list[str]


@dataclass
class CoupledPair:
    a: str
    b: str
    together: int
    either: int

    @property
    def degree(self) -> float:
        return self.together / self.either if self.either else 0.0


@dataclass
class Hotspot:
    path: str
    changes: int
    complexity: int
    """Indentation-weighted line count. A proxy, not a measurement."""

    @property
    def score(self) -> float:
        return self.changes * self.complexity


@dataclass
class StructureReport:
    commits: int = 0
    files: int = 0
    coupled: list[CoupledPair] = field(default_factory=list)
    hotspots: list[Hotspot] = field(default_factory=list)
    churn: Counter = field(default_factory=Counter)
    blast: BlastRadius | None = None
    unreadable: str | None = None
    """Set when history could not be read. Never a finding about the code."""

    @property
    def thin_history(self) -> bool:
        """Whether hotspot ranking should be read sceptically.

        A repository with one semester of commits mostly reports that
        recently-written files were recently written.
        """
        return self.commits < MIN_COMMITS_FOR_HOTSPOTS


def parse_log(text: str) -> list[Commit]:
    """Parse `git log --name-only --no-merges --format=%H`.

    Merges are excluded. A merge commit's file list is the union of both
    sides, which fabricates coupling between files that were never edited
    together by a person.
    """
    commits: list[Commit] = []
    sha: str | None = None
    files: list[str] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if re.fullmatch(r"[0-9a-f]{40}", line):
            if sha:
                commits.append(Commit(sha, files))
            sha, files = line, []
        elif sha and not is_noise(line):
            files.append(line)
    if sha:
        commits.append(Commit(sha, files))
    return commits


def change_coupling(commits: list[Commit]) -> list[CoupledPair]:
    """Which files keep changing together.

    Degree is the Jaccard ratio: commits touching both, over commits touching
    either. That normalisation matters -- a file edited in every commit would
    otherwise appear coupled to everything, when in fact it is coupled to
    nothing in particular.
    """
    appears: Counter = Counter()
    together: Counter = Counter()

    for c in commits:
        files = sorted(set(c.files))
        # A bulk change couples everything it touches to everything else.
        if not (2 <= len(files) <= BULK_COMMIT_FILES):
            appears.update(files)
            continue
        appears.update(files)
        for i, a in enumerate(files):
            for b in files[i + 1 :]:
                together[(a, b)] += 1

    pairs = []
    for (a, b), n in together.items():
        if n < MIN_SUPPORT:
            continue
        either = appears[a] + appears[b] - n
        pair = CoupledPair(a, b, n, either)
        if pair.degree >= COUPLING_THRESHOLD:
            pairs.append(pair)
    pairs.sort(key=lambda p: (-p.degree, -p.together))
    return pairs


def complexity(source: str) -> int:
    """Indentation-weighted line count, as a complexity proxy.

    Tornhill's proxy, and chosen for the same reason he chose it: it works
    identically across every language in this portfolio, where a real parser
    for six languages would be its own project. Deeply nested code scores
    higher than flat code of the same length, which is the property that
    matters.

    Blank lines and whole-line comments are skipped. Tabs count as four.
    """
    total = 0
    for raw in source.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "*", "/*")):
            continue
        indent = len(line) - len(line.lstrip())
        indent += raw[:indent].count("\t") * 3
        total += 1 + indent // 4
    return total


def clone_history(repository: str, workdir: Path) -> str | None:
    """Fetch history without file contents.

    `--filter=blob:none --bare` downloads commit and tree metadata only, which
    is all `--name-only` needs. On a large repository this is seconds rather
    than minutes, and it is why this analysis is cheap enough to run across a
    whole portfolio.
    """
    target = workdir / "repo.git"
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--bare",
            "--filter=blob:none",
            "--quiet",
            f"https://github.com/{repository}.git",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if clone.returncode != 0:
        return None
    log = subprocess.run(
        ["git", "-C", str(target), "log", "--name-only", "--no-merges", "--format=%H"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return log.stdout if log.returncode == 0 else None


def analyse(repository: str, fetch_file, log_text: str | None = None) -> StructureReport:
    """Full structural analysis for one repository.

    `fetch_file(path) -> str | None` supplies file contents for the hotspot
    candidates. Injected rather than imported so this module stays testable
    without a network.

    `log_text` supplies history directly, skipping the clone. That is how
    self-analysis works in CI: the checkout already has the history, so the
    whole structural analysis runs with no network and no token.
    """
    if log_text is not None:
        text: str | None = log_text
    else:
        with tempfile.TemporaryDirectory() as tmp:
            text = clone_history(repository, Path(tmp))

    if text is None:
        return StructureReport(unreadable="The repository history could not be read.")

    commits = parse_log(text)
    if not commits:
        return StructureReport(unreadable="No commit history was returned.")

    churn: Counter = Counter()
    for c in commits:
        churn.update(set(c.files))

    report = StructureReport(
        commits=len(commits),
        files=len(churn),
        coupled=change_coupling(commits)[:8],
        churn=churn,
        blast=blast_radius(commits),
    )

    # Complexity is only fetched for files that already churn, because a
    # complicated file nobody touches is not a hotspot and costs a request to
    # discover.
    for path, changes in churn.most_common(25):
        source = fetch_file(path)
        if source is None:
            continue
        report.hotspots.append(Hotspot(path, changes, complexity(source)))
    report.hotspots.sort(key=lambda h: -h.score)
    report.hotspots = report.hotspots[:6]
    return report


# --- Cohesion, blast radius, and the tidy-first intersection ---------------
#
# Three further readings of the same history, each following an argument from
# Kent Beck's *Tidy First?* rather than from a metrics catalogue.


def module_of(path: str) -> str:
    """The directory a file lives in, used as its module.

    The immediate parent rather than the top-level directory, because the
    boundary worth testing is usually the inner one. `api/models/survey.py`
    and `api/routes/upload.py` share a top-level directory and sit on
    opposite sides of a layer boundary, which is the case this is for.
    """
    return path.rsplit("/", 1)[0] if "/" in path else ""


@dataclass
class Cohesion:
    """How much of a repository's coupling crosses a module boundary.

    Beck's framing is that cohesion is coupling put in the right place:
    things that change together should be together. So the same co-change
    data answers two questions depending on where the pair sits. A pair
    inside one module is cohesion working. A pair spanning two modules is a
    boundary that is not holding, and it is the more actionable of the two
    because it names where a refactor would pay.
    """

    within: list[CoupledPair] = field(default_factory=list)
    across: list[CoupledPair] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.within) + len(self.across)

    @property
    def leak_ratio(self) -> float:
        """Share of strong coupling that crosses a boundary."""
        return len(self.across) / self.total if self.total else 0.0


def cohesion(pairs: list[CoupledPair]) -> Cohesion:
    out = Cohesion()
    for p in pairs:
        (out.within if module_of(p.a) == module_of(p.b) else out.across).append(p)
    return out


@dataclass
class BlastRadius:
    """The distribution of how many files a change touches.

    Beck's cost argument runs cost(software) ~ cost(change) ~ cost(big
    change): the total is dominated by the expensive changes rather than the
    average one, and coupling is what makes a change expensive. So the shape
    of this distribution, and especially its tail, is the economic
    measurement that argument points at. A median of two with a ninetieth
    percentile of twenty says the costly changes are costly for a reason, and
    `worst_files` names it.
    """

    median: int = 0
    p90: int = 0
    largest: int = 0
    worst_files: list[tuple[str, int]] = field(default_factory=list)
    """Files appearing most often in the largest decile of commits."""


def blast_radius(commits: list[Commit]) -> BlastRadius:
    sizes = sorted(len(c.files) for c in commits if c.files)
    if not sizes:
        return BlastRadius()
    median = sizes[len(sizes) // 2]
    p90 = sizes[int(len(sizes) * 0.9)] if len(sizes) > 9 else sizes[-1]

    big = [c for c in commits if len(c.files) >= max(p90, 2)]
    appears: Counter = Counter()
    for c in big:
        appears.update(set(c.files))
    return BlastRadius(
        median=median,
        p90=p90,
        largest=sizes[-1],
        worst_files=appears.most_common(5),
    )


@dataclass
class TidyCandidate:
    """A file where tidying would pay off soon.

    The question mark in *Tidy First?* is the whole argument: tidying pays
    when it makes an imminent change easier, and tidying you will not cash in
    soon is cost without return. A report that lists every structural
    weakness invites exactly the reading Beck argues against.

    This answers the question with evidence rather than taste. A file is a
    candidate when you are already in it often, it is hard to work in, and
    nothing protects you while you change it. Reasons are carried so the
    report can say why rather than just rank.
    """

    path: str
    changes: int
    complexity: int
    untested: bool
    crossing: int
    """Strong couplings this file has across a module boundary."""

    @property
    def reasons(self) -> list[str]:
        out = [f"changed {self.changes} times"]
        if self.complexity:
            out.append(f"complexity {self.complexity}")
        if self.untested:
            out.append("no test file")
        if self.crossing:
            plural = "" if self.crossing == 1 else "s"
            out.append(f"{self.crossing} coupling{plural} across a module boundary")
        return out

    @property
    def score(self) -> float:
        # Churn is the term that makes this "soon" rather than "someday", so
        # it multiplies rather than adds. The rest raise the cost of each
        # visit.
        weight = 1.0 + (0.5 if self.untested else 0.0) + 0.25 * self.crossing
        return self.changes * max(self.complexity, 1) * weight


def tidy_first(hotspots: list[Hotspot], coh: Cohesion, untested: set[str]) -> list[TidyCandidate]:
    """Rank files by how soon tidying them would pay off."""
    crossing: Counter = Counter()
    for p in coh.across:
        crossing[p.a] += 1
        crossing[p.b] += 1

    out = [
        TidyCandidate(
            path=h.path,
            changes=h.changes,
            complexity=h.complexity,
            untested=h.path in untested,
            crossing=crossing.get(h.path, 0),
        )
        for h in hotspots
    ]
    out.sort(key=lambda c: -c.score)
    return out
