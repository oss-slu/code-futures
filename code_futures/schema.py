"""The `code-futures/v1` artifact schema.

This module is the contract. CI writes it, the sweep writes it, the renderer
reads it, and the course orchestration tooling reads it to compose team views.
Everything else in this package is replaceable; changing this is a version bump.

# Why `Outcome` has four values and not two

A boolean destroys the single most actionable finding the tool produces. A
repository with a complete test suite that no workflow invokes is one file away
from having continuous integration; a repository with no test suite is weeks
away. `PRESENT` and `ABSENT` are different states, not degrees of the same one.

`UNREADABLE` exists for a different reason, and it is the one with teeth. These
reports are read by the teams they describe. A check that failed on our side,
rendered as an absence, is a false accusation in a document somebody has to
argue with. The orchestration tooling learned this the expensive way: a sweep
using GitHub's `/community/profile` endpoint 404'd on four of twenty-nine
repositories, the caller read the empty response as an empty file list, and
repositories with a full set of community files were reported as having none.
Every constructor here routes through `Outcome.from_lookup`, so there is no
path from "the request failed" to `ABSENT`.

The middle value is named `PRESENT` rather than `configured`, which is what the
design plan called it. `configured` reads naturally for a linter and badly for
a README, and the same vocabulary has to serve both. The word survives where it
was doing real work: the guidebook's pattern name, "Configured But Never Run".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

SCHEMA = "code-futures/v1"


class Outcome(str, Enum):
    """What a probe established.

    Ordered weakest-evidence-last for display, never for arithmetic. There is
    no comparison operator here on purpose: summing or ranking outcomes is the
    misreading the whole design exists to prevent.
    """

    ENFORCED = "enforced"
    """In place, and CI runs it."""

    PRESENT = "present"
    """In the repository, but nothing runs it automatically."""

    ABSENT = "absent"
    """We looked, and it is not there."""

    UNREADABLE = "unreadable"
    """We could not find out. Describes our tooling, never the repository."""

    @classmethod
    def from_lookup(cls, found: bool | None) -> "Outcome":
        """Build from a lookup that may itself have failed.

        `True` is present, `False` is absent, `None` is unreadable. Routing
        every collector through this is what makes a defaulted `False`
        impossible: a failed request has no path to `ABSENT`.
        """
        if found is None:
            return cls.UNREADABLE
        return cls.PRESENT if found else cls.ABSENT

    @property
    def counts_against(self) -> bool:
        """Whether this is a gap in the repository.

        Only `ABSENT` does. `UNREADABLE` is our gap and must never be counted
        as theirs.
        """
        return self is Outcome.ABSENT


class Section(str, Enum):
    """Report sections, in rendering order.

    Fixed because two tech leads must be able to put two reports side by side
    and talk about the difference. Tailoring a report's shape to its repository
    is tempting and wrong: a section omitted because it found nothing reads as
    a different document, where an explicit "nothing found" is comparable.
    """

    STRUCTURE = "Structure"
    BASELINE = "Open Source Baseline"
    LINT = "Linting and Formatting"
    TEST = "Testing"
    REVIEW = "Review and Integration"
    SECURITY = "Security and Dependencies"
    QUALITY = "Code Quality Analysis"


@dataclass(frozen=True)
class Probe:
    """One question, asked the same way of every repository."""

    id: str
    section: Section
    asks: str
    """The question, phrased for the guidebook's probe table."""

    per_language: bool = False
    """Whether this probe is answered once per detected language.

    A repository that is Python and JavaScript has a `ruff` answer and an
    `eslint` answer, and they can disagree. One merged answer would hide which
    half needs attention, so the probe id stays fixed and the result carries
    the language.
    """

    top_rung: Outcome = Outcome.ENFORCED
    """The strongest outcome this probe can reach.

    A README can never be `ENFORCED`, because there is nothing for CI to run.
    Declaring this makes the renderer's "nothing found" wording honest and
    gives the tests something to assert against.
    """


# The probe spine. Seven sections, fixed order, Structure first.
#
# Deliberately not configurable. A per-repository probe set would make reports
# incomparable, which is the one property the delivery design depends on.
PROBES: tuple[Probe, ...] = (
    # --- Structure -------------------------------------------------------
    # First, because it is the only section about the code rather than the
    # process around it, and because a team that reads one section should
    # read this one.
    #
    # These probes break the `enforced`/`present` pattern: nothing in CI
    # "runs" change coupling, so their top rung is `present`, meaning the
    # analysis produced a finding. `absent` means it ran and found nothing
    # worth reporting, which is a genuinely good result rather than a gap.
    Probe("structure.hotspots", Section.STRUCTURE,
          "Where do complexity and change frequency concentrate?", top_rung=Outcome.PRESENT),
    Probe("structure.change_coupling", Section.STRUCTURE,
          "Which files keep changing together?", top_rung=Outcome.PRESENT),
    Probe("structure.untested_modules", Section.STRUCTURE,
          "Which frequently-changed modules have no tests?", top_rung=Outcome.PRESENT),
    Probe("structure.duplication", Section.STRUCTURE,
          "How much logic is duplicated?", top_rung=Outcome.PRESENT),
    Probe("structure.complexity", Section.STRUCTURE,
          "Which files carry the most cognitive complexity?", top_rung=Outcome.PRESENT),
    # --- Open Source Baseline -------------------------------------------
    # Sourced from the orchestration tooling's practice snapshot rather than
    # recollected. Its collector is tested, current, and already handles the
    # unreadable case; reimplementing it here would be a second thing to be
    # wrong. Ownership should move once the probe set settles.
    Probe("practice.readme", Section.BASELINE, "Is there a README?", top_rung=Outcome.PRESENT),
    # The program's conventions require a README covering Overview, Tech
    # Stack, Installation and Project Structure. Checking only that the file
    # exists tests nothing a team cares about; opening it is nearly free.
    Probe("practice.readme_sections", Section.BASELINE,
          "Does the README cover the four expected sections?", top_rung=Outcome.PRESENT),
    Probe("practice.contributing", Section.BASELINE, "Is there a contributing guide?", top_rung=Outcome.PRESENT),
    Probe("practice.code_of_conduct", Section.BASELINE, "Is there a code of conduct?", top_rung=Outcome.PRESENT),
    Probe("practice.license", Section.BASELINE, "Is there a licence, and is it identifiable?", top_rung=Outcome.PRESENT),
    Probe("practice.issue_template", Section.BASELINE, "Is there an issue template?", top_rung=Outcome.PRESENT),
    Probe("practice.pull_request_template", Section.BASELINE, "Is there a pull request template?", top_rung=Outcome.PRESENT),
    Probe("practice.security_policy", Section.BASELINE, "Is there a security policy?", top_rung=Outcome.PRESENT),
    # --- Linting and Formatting -----------------------------------------
    Probe("lint", Section.LINT, "Is a linter configured, and does CI invoke it?", per_language=True),
    Probe("format", Section.LINT, "Is a formatter configured, and does CI check formatting?", per_language=True),
    # --- Testing ---------------------------------------------------------
    Probe("test.unit", Section.TEST, "Is a unit test runner configured, and invoked in CI?"),
    Probe("test.integration", Section.TEST, "Is there a separate integration suite?"),
    Probe("test.e2e", Section.TEST, "Is there end-to-end testing?"),
    Probe("test.coverage", Section.TEST, "Is a coverage tool wired into CI?"),
    Probe("test.mutation", Section.TEST, "Is there mutation testing?"),
    # --- Review and Integration ------------------------------------------
    Probe("review.required", Section.REVIEW, "Does the default branch require an approving review?"),
    # The companion to the probe above, and the more interesting of the two.
    # `review.required` says whether the rule exists; this says whether review
    # actually happens. A repository can require review and merge everything
    # through an approval nobody read, and it can require nothing while
    # reviewing every change carefully. The gap between policy and practice is
    # the teachable finding, and dropping this as "scalar" lost it.
    Probe("review.practiced", Section.REVIEW, "Do recent changes actually arrive through review?"),
    Probe("ci.present", Section.REVIEW, "Does any CI workflow run?"),
    # --- Security and Dependencies ----------------------------------------
    Probe("security.sast", Section.SECURITY, "Does static application security testing run?"),
    Probe("security.token_permissions", Section.SECURITY, "Do workflows declare minimal permissions?"),
    Probe("security.workflow_safety", Section.SECURITY, "Are workflows free of dangerous patterns?"),
    Probe("deps.update_tool", Section.SECURITY, "Is a dependency update tool configured and working?"),
    # Recovered after being dropped as "inverted". A count of dependencies
    # with known advisories is data a team can act on this afternoon, and
    # withholding it because the probe reads backwards was the wrong trade.
    Probe("deps.known_vulnerabilities", Section.SECURITY,
          "Do any dependencies carry known advisories?", top_rung=Outcome.PRESENT),
    # --- Code Quality Analysis (SonarQube Cloud) --------------------------
    # Its own section rather than folded into security, because what Sonar
    # contributes here is mostly not SAST. CodeQL does the heavier semantic
    # work: dataflow and taint tracking, which is what finds an injection path
    # running from a request parameter through three calls into a query.
    #
    # Sonar's distinctive contribution is the Security Hotspot -- a category
    # meaning "security-sensitive code a human must review", as opposed to a
    # finding the tool is confident about. That category is the best available
    # illustration of the guidebook's thesis: automation got you to the
    # question, and the answer is still yours. Burying it among the Scorecard
    # probes would lose the lesson.
    #
    # Counts and ratings Sonar produces are carried in `attributes` and never
    # become outcomes. A maintainability letter is a vendor's model rendered
    # as a grade, and this tool reports which practices are in place.
    Probe("quality.project", Section.QUALITY, "Is the project analysed by SonarQube Cloud?"),
    Probe("quality.gate", Section.QUALITY, "Is a quality gate defined, and does CI apply it?"),
    Probe("quality.hotspots_reviewed", Section.QUALITY, "Have flagged security hotspots been reviewed?"),
)

PROBES_BY_ID: dict[str, Probe] = {p.id: p for p in PROBES}


@dataclass
class ProbeResult:
    """One outcome for one probe in one repository, with its evidence."""

    id: str
    outcome: Outcome
    language: str | None = None
    """Set only for `per_language` probes."""

    tool: str | None = None
    detail: str = ""
    """Rendered prose. Produced by a template in `strings.py`, never free text.

    Deterministic because these documents are team-facing and comparable: the
    same finding must read identically in two reports, and a reviewer should
    have to check the wording once rather than twenty-nine times.
    """

    evidence: list[str] = field(default_factory=list)
    """File paths, config keys, and `file:line` references backing the outcome."""

    source: str = ""
    """Which collector produced this, so a disputed finding can be traced."""

    attributes: dict[str, str | None] = field(default_factory=dict)
    """Probe-specific machine-readable values, such as a licence SPDX id.

    Kept open rather than modelled per probe. The probe set will move for
    several weeks yet, and a typed field per variant is the part that is
    expensive to reshape.
    """

    def __post_init__(self) -> None:
        if self.id not in PROBES_BY_ID:
            raise ValueError(f"unknown probe id: {self.id}")
        probe = PROBES_BY_ID[self.id]
        if self.language and not probe.per_language:
            raise ValueError(f"probe {self.id} is not per-language")
        if self.outcome is Outcome.ENFORCED and probe.top_rung is Outcome.PRESENT:
            raise ValueError(f"probe {self.id} cannot be enforced; nothing runs it")

    @property
    def key(self) -> tuple[str, str | None]:
        """Identity of a finding: the probe, plus the language where relevant."""
        return (self.id, self.language)

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "outcome": self.outcome.value}
        for k in ("language", "tool", "source"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.detail:
            d["detail"] = self.detail
        if self.evidence:
            d["evidence"] = self.evidence
        if self.attributes:
            d["attributes"] = self.attributes
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProbeResult":
        return cls(
            id=d["id"],
            outcome=Outcome(d["outcome"]),
            language=d.get("language"),
            tool=d.get("tool"),
            detail=d.get("detail", ""),
            evidence=list(d.get("evidence", [])),
            source=d.get("source", ""),
            attributes=dict(d.get("attributes", {})),
        )


@dataclass
class RepositoryReport:
    """Every probe result for one repository.

    The repository is the unit of generation and of delivery. A team holding
    three repositories receives three of these plus a covering view, because
    "linting is missing" is useless without naming which codebase.

    Nothing here knows what a team is. Grouping and delivery need a roster, a
    team-to-repository mapping and chat channels, none of which belong in a
    tool whose subject is codebases.
    """

    repository: str
    collected_at: str
    commit: str | None = None
    languages: list[str] = field(default_factory=list)
    results: list[ProbeResult] = field(default_factory=list)
    contributors: int | None = None
    """Contributor count, never identities.

    Carried so a report can say plainly that its findings read as one person's
    work where that is true. A count is aggregate; who wrote what, joined to an
    assessment, would be an education record.
    """

    def by_section(self, section: Section) -> list[ProbeResult]:
        """Results for one section, in spine order, language rows grouped."""
        order = {p.id: i for i, p in enumerate(PROBES)}
        got = [r for r in self.results if PROBES_BY_ID[r.id].section is section]
        return sorted(got, key=lambda r: (order[r.id], r.language or ""))

    def counts(self) -> dict[Outcome, int]:
        """Findings per outcome. An index for the reader, never a score."""
        return {o: sum(1 for r in self.results if r.outcome is o) for o in Outcome}

    def unreadable(self) -> list[ProbeResult]:
        return [r for r in self.results if r.outcome is Outcome.UNREADABLE]

    @property
    def wholly_unreadable(self) -> bool:
        """Whether nothing at all could be established.

        A report where every probe is unreadable is a collection failure
        wearing a report's clothes, and must be said rather than rendered as a
        page of unknowns.
        """
        return bool(self.results) and all(r.outcome is Outcome.UNREADABLE for r in self.results)

    def to_dict(self) -> dict:
        d: dict = {
            "schema": SCHEMA,
            "repository": self.repository,
            "collected_at": self.collected_at,
        }
        if self.commit:
            d["commit"] = self.commit
        if self.languages:
            d["languages"] = self.languages
        if self.contributors is not None:
            d["contributors"] = self.contributors
        d["probes"] = [r.to_dict() for r in self.results]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RepositoryReport":
        got = d.get("schema")
        if got != SCHEMA:
            raise ValueError(f"expected schema {SCHEMA}, got {got!r}")
        return cls(
            repository=d["repository"],
            collected_at=d["collected_at"],
            commit=d.get("commit"),
            languages=list(d.get("languages", [])),
            results=[ProbeResult.from_dict(p) for p in d.get("probes", [])],
            contributors=d.get("contributors"),
        )


@dataclass
class Snapshot:
    """Every repository report from one sweep.

    This is the whole interface to anything downstream. A consumer that wants
    team views reads a snapshot and composes; it does not reach into this
    package's types, and this package never learns what a team is.
    """

    collected_at: str
    reports: list[RepositoryReport] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    """Repositories that could not be reached at all, and why.

    Run-wide failures rather than per-repository gaps. A sweep that could not
    read half the organisation is not a sweep that found half the organisation
    wanting, and keeping these out of `reports` is what stops them rendering as
    findings.
    """

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "collected_at": self.collected_at,
            "reports": [r.to_dict() for r in self.reports],
            "failures": self.failures,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        got = d.get("schema")
        if got != SCHEMA:
            raise ValueError(f"expected schema {SCHEMA}, got {got!r}")
        return cls(
            collected_at=d["collected_at"],
            reports=[RepositoryReport.from_dict(r) for r in d.get("reports", [])],
            failures=dict(d.get("failures", {})),
        )

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    @classmethod
    def loads(cls, text: str) -> "Snapshot":
        return cls.from_dict(json.loads(text))
