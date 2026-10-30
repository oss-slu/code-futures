"""The fabricated worked example, as data.

`tidewater-atlas` does not exist. The findings are composite: chosen so every
outcome and every conditional note appears at least once, which no single real
repository would be likely to do.

It is built as a `RepositoryReport` and rendered by the real renderer, so the
example in the guidebook is output rather than prose about output. A test
asserts the committed markdown still matches what this produces, which is what
stops the guidebook drifting away from the tool it documents.

Naming a real repository here would make the guidebook a public statement
about the team holding it, and the guidebook outlives any one cohort.
"""

from __future__ import annotations

from code_futures.schema import Outcome as O
from code_futures.schema import ProbeResult as P
from code_futures.schema import RepositoryReport

REPOSITORY = "oss-slu/tidewater-atlas"


def build() -> RepositoryReport:
    results = [
        # Structure. The section the report leads with, and the only one about
        # the code rather than the process around it.
        P("structure.hotspots", O.PRESENT,
          attributes={"count": "4", "top": "api/services/sync.py", "top_changes": "67",
                      "top_complexity": "412", "thin": "false", "commits": "214"},
          evidence=["api/services/sync.py", "api/models/survey.py", "web/src/Dashboard.jsx",
                    "api/routes/upload.py"],
          source="structure"),
        P("structure.change_coupling", O.PRESENT,
          attributes={"count": "3", "threshold": "60", "a": "api/models/survey.py",
                      "b": "api/routes/upload.py", "degree": "83", "together": "24"},
          evidence=["api/models/survey.py <-> api/routes/upload.py (83%)",
                    "api/services/sync.py <-> web/src/Dashboard.jsx (71%)",
                    "api/models/survey.py <-> web/src/SurveyForm.jsx (64%)"],
          source="structure"),
        P("structure.untested_modules", O.PRESENT,
          attributes={"count": "3"},
          evidence=["api/services/sync.py", "api/routes/upload.py", "web/src/Dashboard.jsx"],
          source="structure"),
        P("structure.duplication", O.PRESENT,
          attributes={"density": "7.4", "blocks": "18"}, source="sonar"),
        P("structure.complexity", O.PRESENT,
          attributes={"total": "1840", "files": "63", "top": "api/services/sync.py",
                      "top_value": "412"},
          source="sonar"),
        # Cohesion: two of three coupled pairs cross a module boundary.
        P("structure.cohesion", O.PRESENT,
          attributes={"across": "2", "total": "3", "ratio": "67"},
          evidence=["api/services/sync.py <-> web/src/Dashboard.jsx (71%)",
                    "api/models/survey.py <-> web/src/SurveyForm.jsx (64%)"],
          source="structure"),
        P("structure.blast_radius", O.PRESENT,
          attributes={"median": "2", "p90": "19", "largest": "41"},
          evidence=["api/models/survey.py (in 14 large commits)",
                    "api/services/sync.py (in 11 large commits)",
                    "web/src/Dashboard.jsx (in 9 large commits)"],
          source="structure"),
        P("structure.tidy_first", O.PRESENT,
          attributes={"top": "api/services/sync.py",
                      "reasons": "changed 67 times, complexity 412, no test file, 1 coupling across a module boundary"},
          evidence=["api/services/sync.py — changed 67 times, complexity 412, no test file, 1 coupling across a module boundary",
                    "api/models/survey.py — changed 52 times, complexity 180, 1 coupling across a module boundary",
                    "web/src/Dashboard.jsx — changed 44 times, complexity 210, no test file"],
          source="structure"),

        # Baseline. Licence is `present` with a null SPDX id, which is the
        # upstream `unrecognised` state and triggers its own note.
        P("practice.readme", O.PRESENT, attributes={"path": "README.md"}, source="practice-snapshot"),
        P("practice.readme_sections", O.PRESENT,
          attributes={"found": "two", "missing": "Installation, Project Structure"},
          source="capability"),
        P("practice.contributing", O.PRESENT, attributes={"path": "CONTRIBUTING.md"}, source="practice-snapshot"),
        P("practice.code_of_conduct", O.ABSENT, source="practice-snapshot"),
        P("practice.license", O.PRESENT, attributes={"path": "LICENSE", "spdx": None},
          detail="`LICENSE` exists. Its text matches no OSI-approved licence we recognise, so no SPDX identifier was assigned.",
          source="practice-snapshot"),
        P("practice.issue_template", O.PRESENT,
          attributes={"path": ".github/ISSUE_TEMPLATE/bug_report.md"}, source="practice-snapshot"),
        P("practice.pull_request_template", O.ABSENT, source="practice-snapshot"),
        P("practice.security_policy", O.ABSENT, source="scorecard"),

        # Two languages that disagree, which is the commonest real shape here
        # and the one a single merged answer would hide.
        P("lint", O.ENFORCED, language="Python", tool="ruff",
          attributes={"config": "pyproject.toml", "invocation": ".github/workflows/ci.yml", "blocking": "true"},
          evidence=["pyproject.toml", ".github/workflows/ci.yml"], source="capability"),
        P("lint", O.PRESENT, language="JavaScript", tool="eslint",
          attributes={"config": "frontend/.eslintrc.json", "blocking": "true"},
          evidence=["frontend/.eslintrc.json"], source="capability"),
        P("format", O.ENFORCED, language="Python", tool="ruff format",
          attributes={"config": "pyproject.toml", "invocation": ".github/workflows/ci.yml", "blocking": "true"},
          evidence=[".github/workflows/ci.yml"], source="capability"),
        P("format", O.ABSENT, language="JavaScript", source="capability"),

        P("test.unit", O.ENFORCED, tool="pytest",
          attributes={"config": "pyproject.toml", "invocation": ".github/workflows/ci.yml", "blocking": "true"},
          evidence=["pyproject.toml", ".github/workflows/ci.yml"], source="capability"),
        P("test.integration", O.ABSENT, source="capability"),
        P("test.e2e", O.ABSENT, source="capability"),
        P("test.coverage", O.PRESENT, tool="pytest-cov",
          attributes={"config": "pyproject.toml", "blocking": "true"},
          evidence=["pyproject.toml"], source="capability"),
        P("test.mutation", O.ABSENT, source="capability"),

        # Protection that exists and gates nothing: the trap in this section.
        P("review.required", O.PRESENT,
          attributes={"branch": "main", "reviews": "0", "status_checks": "not required", "admins": "exempt"},
          source="practice-snapshot"),
        # Policy says nothing is required; practice shows most changes were
        # reviewed anyway. The disagreement is the finding.
        P("review.practiced", O.ENFORCED,
          attributes={"reviewed": "18", "total": "20"}, source="scorecard"),
        P("ci.present", O.ENFORCED,
          attributes={"count": "3", "workflows": "`CI`, `CodeQL`, `Dependabot Updates`"},
          source="practice-snapshot"),

        # One unreadable, so the example shows a failure reported as ours.
        P("security.sast", O.UNREADABLE,
          attributes={"reason": "The Scorecard SAST check exceeded its timeout."}, source="scorecard"),
        P("security.token_permissions", O.ABSENT, source="scorecard"),
        P("security.workflow_safety", O.ENFORCED, source="scorecard"),
        P("deps.update_tool", O.ENFORCED, tool="Dependabot",
          attributes={"config": ".github/dependabot.yml"}, source="scorecard"),
        P("deps.known_vulnerabilities", O.PRESENT,
          attributes={"count": "3"}, source="scorecard"),

        # Sonar, with hotspots outstanding so the teaching note appears.
        P("quality.project", O.ENFORCED,
          attributes={"project_key": "oss-slu_tidewater-atlas", "last_analysis": "2026-10-30"}, source="sonar"),
        P("quality.gate", O.ENFORCED,
          attributes={"gate": "default", "status": "Passed"}, source="sonar"),
        P("quality.hotspots_reviewed", O.PRESENT,
          attributes={"total": "7", "reviewed": "2"}, source="sonar"),
    ]
    return RepositoryReport(
        repository=REPOSITORY,
        collected_at="2026-11-01 14:00 UTC",
        commit="a0408fc",
        languages=["Python", "JavaScript"],
        results=results,
        contributors=5,
    )


if __name__ == "__main__":
    from pathlib import Path

    from code_futures.render import render

    out = Path(__file__).parent / "tidewater-atlas.md"
    out.write_text(render(build(), fabricated=True))
    print(f"wrote {out}")
