"""Collectors: turn evidence from each source into probe results.

One function per source. Each returns results for the probes it owns and says
nothing about the others, so a source being unavailable costs its own probes
and no more.

Every collector follows the same rule, and it is the only rule that matters
here: **a source that could not be read produces `UNREADABLE`, never
`ABSENT`.** These reports are read by the teams they describe, and rendering
our failure as their deficiency is the worst thing this tool can do.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import detect, github, structure
from .config import Config
from .schema import Outcome, ProbeResult

# --- Practice baseline ----------------------------------------------------

_PRESENCE_PROBES = {
    "readme": "practice.readme",
    "contributing": "practice.contributing",
    "code_of_conduct": "practice.code_of_conduct",
    "issue_template": "practice.issue_template",
    "pull_request_template": "practice.pull_request_template",
}

_DEFAULT_PATHS = {
    "practice.readme": "README.md",
    "practice.contributing": "CONTRIBUTING.md",
    "practice.code_of_conduct": "CODE_OF_CONDUCT.md",
    "practice.issue_template": ".github/ISSUE_TEMPLATE/",
    "practice.pull_request_template": ".github/pull_request_template.md",
}


def from_practice_snapshot(entry: dict) -> list[ProbeResult]:
    """Map one repository's entry in the orchestration practice snapshot.

    Read rather than reimplemented. That collector is tested, current, and
    already distinguishes "we could not ask" from "the answer is no" -- a
    distinction it acquired the expensive way, after an endpoint returning 404
    on four of twenty-nine repositories caused projects with a full set of
    community files to be reported as having none.

    Its `Presence` maps onto our `Outcome` without loss, which is why the
    middle value here is named `present`.
    """
    out: list[ProbeResult] = []

    for field, probe_id in _PRESENCE_PROBES.items():
        raw = entry.get(field)
        outcome = {
            "present": Outcome.PRESENT,
            "absent": Outcome.ABSENT,
        }.get(raw, Outcome.UNREADABLE)
        attrs = {"path": _DEFAULT_PATHS[probe_id]} if outcome is Outcome.PRESENT else {}
        out.append(
            ProbeResult(
                id=probe_id,
                outcome=outcome,
                attributes=attrs,
                source="practice-snapshot",
                **({"evidence": [_DEFAULT_PATHS[probe_id]]} if outcome is Outcome.PRESENT else {}),
            )
        )

    # Licence carries a fourth upstream state. A file that exists but matches
    # no OSI licence is `present` with a null SPDX id: folding it into
    # `absent` would erase the fact that somebody licensed this deliberately,
    # and a bare `present` would imply the licence works.
    lic = entry.get("licence") or {}
    state = lic.get("state")
    if state == "identified":
        out.append(
            ProbeResult(
                id="practice.license",
                outcome=Outcome.PRESENT,
                attributes={"path": "LICENSE", "spdx": lic.get("spdx")},
                evidence=["LICENSE"],
                source="practice-snapshot",
            )
        )
    elif state == "unrecognised":
        out.append(
            ProbeResult(
                id="practice.license",
                outcome=Outcome.PRESENT,
                detail="`LICENSE` exists. Its text matches no OSI-approved licence we recognise, so no SPDX identifier was assigned.",
                attributes={"path": "LICENSE", "spdx": None},
                evidence=["LICENSE"],
                source="practice-snapshot",
            )
        )
    elif state == "absent":
        out.append(ProbeResult(id="practice.license", outcome=Outcome.ABSENT, source="practice-snapshot"))
    else:
        out.append(
            ProbeResult(
                id="practice.license",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "The licence check could not be read."},
                source="practice-snapshot",
            )
        )

    # Branch gate. `present` covers protection that exists and gates nothing,
    # which is the trap in this section: the settings page looks reassuring
    # while an unreviewed pull request can still merge.
    gate = entry.get("branch_gate") or {}
    gstate = gate.get("state")
    branch = entry.get("default_branch", "main")
    if gstate == "protected":
        reviews = int(gate.get("required_reviews", 0) or 0)
        attrs = {
            "branch": branch,
            "reviews": str(reviews),
            "status_checks": "required" if gate.get("status_checks") else "not required",
            "admins": "subject to the rules" if gate.get("enforce_admins") else "exempt",
        }
        out.append(
            ProbeResult(
                id="review.required",
                outcome=Outcome.ENFORCED if reviews > 0 else Outcome.PRESENT,
                attributes=attrs,
                source="practice-snapshot",
            )
        )
    elif gstate == "unprotected":
        out.append(
            ProbeResult(
                id="review.required",
                outcome=Outcome.ABSENT,
                attributes={"branch": branch},
                source="practice-snapshot",
            )
        )
    else:
        out.append(
            ProbeResult(
                id="review.required",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "Branch protection settings could not be read."},
                source="practice-snapshot",
            )
        )

    # `None` means the workflow list could not be read. An empty list means
    # there are none, which is a finding.
    wf = entry.get("workflows")
    if wf is None:
        out.append(
            ProbeResult(
                id="ci.present",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "The workflow list could not be read."},
                source="practice-snapshot",
            )
        )
    elif wf:
        out.append(
            ProbeResult(
                id="ci.present",
                outcome=Outcome.ENFORCED,
                attributes={"count": str(len(wf)), "workflows": ", ".join(f"`{w}`" for w in wf[:6])},
                source="practice-snapshot",
            )
        )
    else:
        out.append(ProbeResult(id="ci.present", outcome=Outcome.ABSENT, source="practice-snapshot"))

    return out


def load_practice_snapshot(path: Path) -> dict[str, dict] | None:
    """Index the orchestration snapshot by repository name."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, Exception):  # noqa: BLE001 - any parse failure is unreadable
        return None
    if not isinstance(data, dict):
        return None
    return {r["repository"]: r for r in data.get("repositories", []) if "repository" in r}


# --- Capability detection -------------------------------------------------

_CAPABILITY_PROBES = (
    "lint",
    "format",
    "test.unit",
    "test.integration",
    "test.e2e",
    "test.coverage",
    "test.mutation",
)


def capabilities(
    repository: str,
    *,
    paths: list[str] | None = None,
    wfs: dict[str, str] | None = None,
    read_file=None,
    _local: bool = False,
) -> tuple[list[ProbeResult], list[str]]:
    """Detect lint, format and test capability. Returns results and languages.

    Sources are injectable so the same detection runs against a GitHub
    repository or a local checkout. `_local` says the caller supplied both,
    which matters because `None` means unreadable and an absent argument does
    not.
    """
    if not _local:
        paths = github.tree(repository)
        wfs = github.workflows(repository)
    reader = read_file or (lambda p: github.file(repository, p))

    if paths is None and wfs is None:
        unreadable = [
            ProbeResult(
                id=pid,
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "The repository tree could not be read."},
                source="capability",
            )
            for pid in _CAPABILITY_PROBES
        ]
        return unreadable, []

    # Fetch only files a signal could match, and never from a vendored tree.
    wanted = {s.filename for s in detect._CONFIG if s.filename} | {
        s.in_file for s in detect._CONFIG if s.in_file
    }
    contents: dict[str, str] = {}
    for p in paths or []:
        if detect.is_vendored(p):
            continue
        if p.rsplit("/", 1)[-1] in wanted:
            body = reader(p)
            if body is not None:
                contents[p] = body

    scripts = detect.package_scripts(contents)
    findings = detect.merge(detect.detect_config(contents), detect.detect_runs(wfs or {}, scripts))
    languages = sorted({f.language for f in findings})

    out: list[ProbeResult] = []
    for pid in _CAPABILITY_PROBES:
        matching = [f for f in findings if f.kind == pid]
        per_language = pid in ("lint", "format")
        if not matching:
            if per_language:
                for lang in languages or [None]:
                    out.append(
                        ProbeResult(id=pid, outcome=Outcome.ABSENT, language=lang, source="capability")
                    )
            else:
                out.append(ProbeResult(id=pid, outcome=Outcome.ABSENT, source="capability"))
            continue
        for f in matching:
            out.append(
                ProbeResult(
                    id=pid,
                    outcome=Outcome.ENFORCED if f.enforced else Outcome.PRESENT,
                    language=f.language if per_language else None,
                    tool=f.tool,
                    attributes={
                        "config": f.config or "",
                        "invocation": f.invocation or "",
                        "blocking": "true" if f.blocking else "false",
                    },
                    evidence=f.evidence,
                    source="capability",
                )
            )
    return out, languages


# --- Scorecard -------------------------------------------------------------

_SCORECARD_PROBES = {
    "SAST": "security.sast",
    "Security-Policy": "practice.security_policy",
    "Token-Permissions": "security.token_permissions",
    "Dangerous-Workflow": "security.workflow_safety",
    "Dependency-Update-Tool": "deps.update_tool",
}


def _scorecard_outcome(score: int, reason: str) -> tuple[Outcome, dict]:
    """Map one Scorecard check onto an outcome.

    **`-1` is not a low score.** Scorecard uses it for a check that could not
    reach a conclusion -- "no workflows found", "No tokens found" -- which is
    categorically different from `0`, meaning evaluated and nothing found.
    Conflating the two manufactures findings, so `-1` becomes `UNREADABLE`
    carrying Scorecard's own reason string rather than a paraphrase.

    The 0-10 composite is never read. It is calibrated for widely-depended-on
    libraries, and a composite score encodes the population it was built for.
    """
    if score < 0:
        return Outcome.UNREADABLE, {"reason": reason.rstrip(".").capitalize() + "."}
    # A practice-shaped check: anything above the floor means it is in place.
    return (Outcome.ENFORCED if score >= 7 else Outcome.ABSENT), {"reason": reason}


def scorecard(repository: str, cfg: Config) -> list[ProbeResult]:
    """Run selected Scorecard checks against one repository."""
    env_token = cfg.github_token or ""
    cmd = [
        "scorecard",
        f"--repo=github.com/{repository}",
        f"--checks={','.join(cfg.scorecard_checks)}",
        "--format=json",
    ]
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=cfg.scorecard_timeout,
            check=False,
            env={"GITHUB_AUTH_TOKEN": env_token, "PATH": _path()},
        )
    except FileNotFoundError:
        return _all_unreadable("Scorecard is not installed.")
    except subprocess.TimeoutExpired:
        return _all_unreadable(f"Scorecard exceeded its {cfg.scorecard_timeout}s timeout.")

    if p.returncode != 0:
        return _all_unreadable("The Scorecard run failed.")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return _all_unreadable("Scorecard output could not be parsed.")

    out: list[ProbeResult] = []
    for check in data.get("checks", []):
        probe_id = _SCORECARD_PROBES.get(check.get("name", ""))
        if not probe_id:
            continue
        outcome, attrs = _scorecard_outcome(int(check.get("score", -1)), check.get("reason", ""))
        if probe_id == "practice.security_policy" and outcome is Outcome.ENFORCED:
            outcome = Outcome.PRESENT  # nothing runs a SECURITY.md
            attrs = {"path": "SECURITY.md"}
        out.append(ProbeResult(id=probe_id, outcome=outcome, attributes=attrs, source="scorecard"))
    return out


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin")


def _all_unreadable(reason: str) -> list[ProbeResult]:
    return [
        ProbeResult(id=pid, outcome=Outcome.UNREADABLE, attributes={"reason": reason}, source="scorecard")
        for pid in _SCORECARD_PROBES.values()
    ]


# --- SonarQube Cloud --------------------------------------------------------

_SONAR_PROBES = ("quality.project", "quality.gate", "quality.hotspots_reviewed")


def sonar(repository: str, cfg: Config) -> list[ProbeResult]:
    """Read SonarQube Cloud analysis for one repository.

    Skipped rather than failed when no token is configured: a missing
    credential is our gap, so the probes report `unreadable` and the rest of
    the report is unaffected.
    """
    if not cfg.has_sonar:
        return [
            ProbeResult(
                id=pid,
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "No SonarQube Cloud token is configured."},
                source="sonar",
            )
            for pid in _SONAR_PROBES
        ]

    key = cfg.sonar_key(repository)
    base = "https://sonarcloud.io/api"
    project = _sonar_get(f"{base}/components/show?component={key}", cfg)
    if project is None:
        return [
            ProbeResult(
                id=pid,
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "SonarQube Cloud could not be reached."},
                source="sonar",
            )
            for pid in _SONAR_PROBES
        ]
    if not project.get("component"):
        return [
            ProbeResult(id="quality.project", outcome=Outcome.ABSENT, source="sonar"),
            ProbeResult(id="quality.gate", outcome=Outcome.ABSENT, source="sonar"),
            ProbeResult(id="quality.hotspots_reviewed", outcome=Outcome.ABSENT, source="sonar"),
        ]

    out = [
        ProbeResult(
            id="quality.project",
            outcome=Outcome.ENFORCED,
            attributes={
                "project_key": key,
                "last_analysis": (project.get("component", {}).get("analysisDate") or "unknown")[:10],
            },
            source="sonar",
        )
    ]

    gate = _sonar_get(f"{base}/qualitygates/project_status?projectKey={key}", cfg) or {}
    status = (gate.get("projectStatus") or {}).get("status")
    if status:
        out.append(
            ProbeResult(
                id="quality.gate",
                outcome=Outcome.ENFORCED,
                attributes={"gate": "default", "status": status.title()},
                source="sonar",
            )
        )
    else:
        out.append(
            ProbeResult(
                id="quality.gate",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "The quality gate status could not be read."},
                source="sonar",
            )
        )

    hs = _sonar_get(f"{base}/hotspots/search?projectKey={key}&ps=1", cfg)
    if hs is None:
        out.append(
            ProbeResult(
                id="quality.hotspots_reviewed",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "Security hotspots could not be read."},
                source="sonar",
            )
        )
    else:
        total = int((hs.get("paging") or {}).get("total", 0))
        reviewed_data = _sonar_get(f"{base}/hotspots/search?projectKey={key}&status=REVIEWED&ps=1", cfg) or {}
        reviewed = int((reviewed_data.get("paging") or {}).get("total", 0))
        if total == 0:
            out.append(ProbeResult(id="quality.hotspots_reviewed", outcome=Outcome.ABSENT, source="sonar"))
        else:
            out.append(
                ProbeResult(
                    id="quality.hotspots_reviewed",
                    outcome=Outcome.ENFORCED if reviewed == total else Outcome.PRESENT,
                    attributes={"total": str(total), "reviewed": str(reviewed)},
                    source="sonar",
                )
            )
    return out


def _sonar_get(url: str, cfg: Config) -> dict | None:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url)
    token = cfg.sonar_token or ""
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https host
            return json.loads(r.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


# --- Structure -------------------------------------------------------------

_STRUCTURE_PROBES = (
    "structure.hotspots",
    "structure.change_coupling",
    "structure.untested_modules",
    "structure.duplication",
    "structure.complexity",
    "structure.cohesion",
    "structure.blast_radius",
    "structure.tidy_first",
)

# A source file is considered covered when a file with a matching stem exists
# under a test-looking path. Crude, and deliberately so: a precise mapping
# needs per-framework knowledge, and the finding here is "nothing anywhere
# names this module", which the crude version gets right.
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_|_test\.|\.test\.|\.spec\.", re.I)

# Strip the conventional affixes so a test file maps to the module it tests.
# Without this, `tests/test_render.py` has the stem `test_render`, never
# matches `render.py`, and every tested module is reported as untested --
# which is what the first self-run of this tool reported about itself.
_TEST_AFFIX = re.compile(r"^test[_.-]|[_.-]test$|^spec[_.-]|[_.-]spec$", re.I)


def _subject_of(path: str) -> str:
    """The module name a test file is about."""
    stem = path.rsplit("/", 1)[-1].split(".")[0]
    return _TEST_AFFIX.sub("", stem).lower()


def structural(
    repository: str,
    paths: list[str] | None,
    *,
    log_text: str | None = None,
    read_file=None,
) -> list[ProbeResult]:
    """Hotspots, change coupling, and untested modules, from git history.

    The only collector that looks at the code rather than the process around
    it. Everything here is computed from `git log` plus the files it names,
    with no external service and nothing to onboard.
    """
    reader = read_file or (lambda p: github.file(repository, p))
    report = structure.analyse(repository, reader, log_text=log_text)

    if report.unreadable:
        return [
            ProbeResult(
                id=pid,
                outcome=Outcome.UNREADABLE,
                attributes={"reason": report.unreadable},
                source="structure",
            )
            for pid in _STRUCTURE_PROBES
        ]

    out: list[ProbeResult] = []

    if report.hotspots:
        top = report.hotspots[0]
        out.append(
            ProbeResult(
                id="structure.hotspots",
                outcome=Outcome.PRESENT,
                attributes={
                    "count": str(len(report.hotspots)),
                    "top": top.path,
                    "top_changes": str(top.changes),
                    "top_complexity": str(top.complexity),
                    "thin": "true" if report.thin_history else "false",
                    "commits": str(report.commits),
                },
                evidence=[h.path for h in report.hotspots],
                source="structure",
            )
        )
    else:
        out.append(ProbeResult(id="structure.hotspots", outcome=Outcome.ABSENT, source="structure"))

    if report.coupled:
        best = report.coupled[0]
        out.append(
            ProbeResult(
                id="structure.change_coupling",
                outcome=Outcome.PRESENT,
                attributes={
                    "count": str(len(report.coupled)),
                    "threshold": str(int(structure.COUPLING_THRESHOLD * 100)),
                    "a": best.a,
                    "b": best.b,
                    "degree": str(round(best.degree * 100)),
                    "together": str(best.together),
                },
                evidence=[f"{p.a} <-> {p.b} ({round(p.degree * 100)}%)" for p in report.coupled],
                source="structure",
            )
        )
    else:
        out.append(ProbeResult(id="structure.change_coupling", outcome=Outcome.ABSENT, source="structure"))

    # Untested modules, restricted to files that actually churn. A stable file
    # with no test is a much weaker finding than a file changing every week
    # with no test, and reporting both flattens the one worth acting on.
    if paths is None:
        out.append(
            ProbeResult(
                id="structure.untested_modules",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "The repository tree could not be read."},
                source="structure",
            )
        )
    else:
        stems = {s for p in paths if _TEST_PATH.search(p) for s in (_subject_of(p),)}
        bare = []
        for path, _changes in report.churn.most_common(40):
            if _TEST_PATH.search(path) or detect.is_vendored(path):
                continue
            if not path.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".rs", ".kt")):
                continue
            stem = path.rsplit("/", 1)[-1].split(".")[0].lower()
            if stem not in stems:
                bare.append(path)
        if bare:
            out.append(
                ProbeResult(
                    id="structure.untested_modules",
                    outcome=Outcome.PRESENT,
                    attributes={"count": str(len(bare))},
                    evidence=bare[:10],
                    source="structure",
                )
            )
        else:
            out.append(
                ProbeResult(id="structure.untested_modules", outcome=Outcome.ABSENT, source="structure")
            )

    # Cohesion: the same coupling data read against module boundaries.
    coh = structure.cohesion(report.coupled)
    if coh.total:
        out.append(
            ProbeResult(
                id="structure.cohesion",
                outcome=Outcome.PRESENT if coh.across else Outcome.ABSENT,
                attributes={
                    "across": str(len(coh.across)),
                    "total": str(coh.total),
                    "ratio": str(round(coh.leak_ratio * 100)),
                },
                evidence=[f"{p.a} <-> {p.b} ({round(p.degree * 100)}%)" for p in coh.across[:6]],
                source="structure",
            )
        )
    else:
        out.append(ProbeResult(id="structure.cohesion", outcome=Outcome.ABSENT, source="structure"))

    # Blast radius: the distribution Beck's cost argument points at.
    br = report.blast
    if br and br.median:
        out.append(
            ProbeResult(
                id="structure.blast_radius",
                outcome=Outcome.PRESENT,
                attributes={"median": str(br.median), "p90": str(br.p90), "largest": str(br.largest)},
                evidence=[
                    f"{path} (in {n} large commit{'' if n == 1 else 's'})" for path, n in br.worst_files
                ],
                source="structure",
            )
        )
    else:
        out.append(ProbeResult(id="structure.blast_radius", outcome=Outcome.ABSENT, source="structure"))

    # Tidy first: where tidying pays back soonest, rather than the worst file.
    untested_set = set()
    for r in out:
        if r.id == "structure.untested_modules" and r.outcome is Outcome.PRESENT:
            untested_set = set(r.evidence)
    candidates = structure.tidy_first(report.hotspots, coh, untested_set)
    if candidates:
        top = candidates[0]
        out.append(
            ProbeResult(
                id="structure.tidy_first",
                outcome=Outcome.PRESENT,
                attributes={"top": top.path, "reasons": ", ".join(top.reasons)},
                evidence=[f"{c.path} — {', '.join(c.reasons)}" for c in candidates[:4]],
                source="structure",
            )
        )
    else:
        out.append(ProbeResult(id="structure.tidy_first", outcome=Outcome.ABSENT, source="structure"))

    # Duplication and cognitive complexity come from SonarQube Cloud, which
    # measures both properly. Reported as unreadable rather than absent when
    # no Sonar project exists, because we did not establish them.
    for pid in ("structure.duplication", "structure.complexity"):
        out.append(
            ProbeResult(
                id=pid,
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "SonarQube Cloud supplies this measurement, and no analysis was read."},
                source="structure",
            )
        )
    return out


# --- Local practice baseline ------------------------------------------------

_LOCAL_FILES = {
    "practice.readme": ("README.md", "README.rst", "README"),
    "practice.contributing": ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md"),
    "practice.code_of_conduct": ("CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md"),
    "practice.license": ("LICENSE", "LICENSE.md", "LICENCE", "COPYING"),
    "practice.pull_request_template": (
        ".github/pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    ),
    "practice.security_policy": ("SECURITY.md", ".github/SECURITY.md"),
}

# The program's README convention. Matched case-insensitively against heading
# text rather than exact strings, because "## Installation" and "## Getting
# Started / Installation" both satisfy the intent.
README_SECTIONS = {
    "Overview": ("overview", "about", "what is", "introduction"),
    "Tech Stack": ("tech stack", "technology", "built with", "stack"),
    "Installation": ("installation", "install", "getting started", "setup"),
    "Project Structure": ("project structure", "structure", "layout", "architecture"),
}


def readme_sections(source: str | None) -> tuple[list[str], list[str]]:
    """Which expected README sections are present, and which are missing.

    Checking that a README exists tests nothing a reader cares about. This
    opens it, which is nearly free and answers the question the program's
    conventions actually ask.
    """
    if source is None:
        return [], list(README_SECTIONS)
    headings = [
        line.lstrip("#").strip().lower() for line in source.splitlines() if line.lstrip().startswith("#")
    ]
    found, missing = [], []
    for name, aliases in README_SECTIONS.items():
        if any(any(a in h for a in aliases) for h in headings):
            found.append(name)
        else:
            missing.append(name)
    return found, missing


def local_practice(paths: list[str], read_file) -> list[ProbeResult]:
    """Baseline presence from a local checkout.

    A simpler reader than the orchestration snapshot: it answers presence and
    nothing about licence identification or branch protection, both of which
    need more than the filesystem. Those probes are left to other collectors
    rather than guessed at here.
    """
    have = set(paths)
    out: list[ProbeResult] = []

    for probe_id, candidates in _LOCAL_FILES.items():
        hit = next((c for c in candidates if c in have), None)
        out.append(
            ProbeResult(
                id=probe_id,
                outcome=Outcome.PRESENT if hit else Outcome.ABSENT,
                attributes={"path": hit} if hit else {},
                evidence=[hit] if hit else [],
                source="local",
            )
        )

    issue_dir = any(p.startswith(".github/ISSUE_TEMPLATE") for p in paths)
    out.append(
        ProbeResult(
            id="practice.issue_template",
            outcome=Outcome.PRESENT if issue_dir else Outcome.ABSENT,
            attributes={"path": ".github/ISSUE_TEMPLATE/"} if issue_dir else {},
            source="local",
        )
    )

    readme = next((c for c in _LOCAL_FILES["practice.readme"] if c in have), None)
    found, missing = readme_sections(read_file(readme) if readme else None)
    if not readme:
        out.append(
            ProbeResult(
                id="practice.readme_sections",
                outcome=Outcome.UNREADABLE,
                attributes={"reason": "There is no README to read."},
                source="local",
            )
        )
    elif missing:
        out.append(
            ProbeResult(
                id="practice.readme_sections",
                outcome=Outcome.PRESENT,
                attributes={"found": str(len(found)), "missing": ", ".join(missing)},
                source="local",
            )
        )
    else:
        out.append(
            ProbeResult(
                id="practice.readme_sections",
                outcome=Outcome.PRESENT,
                detail="Covers all four expected sections.",
                source="local",
            )
        )

    wf = [p for p in paths if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))]
    out.append(
        ProbeResult(
            id="ci.present",
            outcome=Outcome.ENFORCED if wf else Outcome.ABSENT,
            attributes={
                "count": str(len(wf)),
                "workflows": ", ".join(f"`{p.rsplit('/', 1)[-1]}`" for p in wf[:6]),
            },
            source="local",
        )
    )
    return out
