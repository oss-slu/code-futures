"""What a CI run observed, as opposed to what a file inspection inferred.

A workflow can say things no amount of reading can establish. "A linter is
configured and a step invokes it" is inference from two files; "the linter ran
and exited zero" is observation. Where both have an opinion about the same
probe, the observation wins, because it watched the thing happen.

# The shape

Each job writes one small JSON file into a shared directory and the summarize
job merges them. A fragment is deliberately minimal:

    {"probe": "test.unit", "outcome": "enforced", "tool": "pytest",
     "detail": "41 tests ran", "blocking": false}

Jobs are `continue-on-error`, so a fragment can record a failing check. That
is the point: a repository whose lint fails must still appear in its own
report, and a run that produced no artifact would make failure
indistinguishable from never having adopted the workflow.

# What must not end up in here

**Nothing that is not already derivable from the public repository.** CI
output on a public repository is public: logs and job summaries are readable
without an account, and artifacts by anyone signed in. Under that rule the
artifact is facts about public code, and its login-walled visibility is a
convenience rather than a control.

The composed report is a different thing and is assembled elsewhere. This
module exists partly to keep that line visible in the code.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import PROBES_BY_ID, Outcome, ProbeResult


def load(directory: Path) -> list[ProbeResult]:
    """Read every observation fragment in a directory.

    Malformed and unknown fragments are skipped rather than raising. A job
    that wrote nonsense should cost its own probe and not the whole run.
    """
    out: list[ProbeResult] = []
    if not directory.is_dir():
        return out

    for path in sorted(directory.rglob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for frag in data if isinstance(data, list) else [data]:
            result = _one(frag)
            if result is not None:
                out.append(result)
    return out


def _scalar(v: object) -> str:
    """Stringify an attribute value to the artifact's conventions.

    JSON booleans need lowercasing: `str(False)` is `"False"`, the contract
    is `"false"`, and `render.py` compares against the lowercase form. A
    fragment using the documented `"blocking": false` therefore matched
    nothing and its advisory note was silently dropped.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _one(frag: object) -> ProbeResult | None:
    """One fragment, or `None` if it is not usable.

    Field *types* are checked before anything is looked up or constructed.
    Fragments are written by shell scripts, so a field can be any JSON type,
    and an unhashable one raised `TypeError` at the membership check below --
    which escaped `load()` and discarded every healthy fragment alongside it.
    """
    if not isinstance(frag, dict):
        return None
    probe_id = frag.get("probe")
    if not isinstance(probe_id, str) or probe_id not in PROBES_BY_ID:
        return None
    outcome_raw = frag.get("outcome")
    if not isinstance(outcome_raw, str):
        return None
    try:
        outcome = Outcome(outcome_raw)
    except ValueError:
        return None

    language = frag.get("language")
    if language is not None and not isinstance(language, str):
        return None
    tool = frag.get("tool")
    if tool is not None and not isinstance(tool, str):
        return None
    evidence_raw = frag.get("evidence", [])
    if not isinstance(evidence_raw, list):
        evidence_raw = []

    probe = PROBES_BY_ID[probe_id]
    # A job cannot claim a rung the probe does not have. Without this a
    # malformed fragment would raise inside the constructor and take the
    # summarize job down with it.
    if outcome is Outcome.ENFORCED and probe.top_rung is not Outcome.ENFORCED:
        outcome = Outcome.PRESENT

    attributes = {
        k: _scalar(v)
        for k, v in frag.items()
        if k not in ("probe", "outcome", "tool", "detail", "language", "evidence")
        and isinstance(v, (str, int, float, bool))
    }
    try:
        return ProbeResult(
            id=probe_id,
            outcome=outcome,
            language=language if probe.per_language else None,
            tool=tool,
            detail=str(frag.get("detail", "")),
            evidence=[str(e) for e in evidence_raw if e],
            attributes=attributes,
            source="ci",
        )
    except (ValueError, TypeError):
        return None


# How strongly an outcome speaks, for combining two observations of one probe.
# `UNREADABLE` is weakest: a job that could not tell must never displace one
# that could.
_STRENGTH = {Outcome.ENFORCED: 3, Outcome.PRESENT: 2, Outcome.ABSENT: 1, Outcome.UNREADABLE: 0}


def _combine(a: ProbeResult, b: ProbeResult) -> ProbeResult:
    """Two observations of one probe, resolved by strength of evidence.

    Needed because four language jobs all report `test.unit`, which is a
    single-answer probe. Without this the last fragment read wins, and
    fragments are read in filename order -- so `java-test.json` sorting
    before `node-test.json` let Node's `absent` placeholder overwrite a real
    Java test run. Filename order was deciding what a repository is.

    Strongest wins, because the claims are not symmetric: a job that watched
    tests run establishes that tests run, while a job finding none in *its*
    language establishes nothing about the others.
    """
    strong, weak = (a, b) if _STRENGTH[a.outcome] >= _STRENGTH[b.outcome] else (b, a)

    # `passed` is a conjunction across every run combined here. Keeping only
    # the stronger finding's value let a passing Python suite hide a failing
    # Rust one, and a green summary is the worst thing to be wrong about.
    verdicts = [r.attributes.get("passed") for r in (a, b) if "passed" in r.attributes]
    if verdicts:
        strong.attributes["passed"] = "true" if all(v == "true" for v in verdicts) else "false"
    # Likewise advisory-ness: if any combined run could not fail, say so.
    blocking = [r.attributes.get("blocking") for r in (a, b) if "blocking" in r.attributes]
    if blocking:
        strong.attributes["blocking"] = "true" if all(v == "true" for v in blocking) else "false"

    for e in weak.evidence:
        if e not in strong.evidence:
            strong.evidence.append(e)
    # Keep the weaker finding visible rather than discarding it: "pytest ran,
    # and the Node test script is an echo" is two facts, not a contradiction.
    if weak.detail and weak.detail not in strong.detail:
        strong.detail = f"{strong.detail} ({weak.tool or 'elsewhere'}: {weak.detail})".strip()
    return strong


def merge(inferred: list[ProbeResult], observed: list[ProbeResult]) -> list[ProbeResult]:
    """Combine inference with observation, observation winning.

    Keyed by `(probe, language)` so a polyglot repository keeps both answers
    for a per-language probe.

    The direction is not arbitrary. A CI run can report that a test command
    ran and what it did; nothing read from outside the run can establish
    that. Where the two disagree, the one that watched is the better
    evidence.

    Two *observations* of the same probe are combined by strength rather than
    by order -- see `_combine`.
    """
    merged: dict[tuple[str, str | None], ProbeResult] = {}
    # Inference can collide with itself: `capabilities()` may return an
    # enforced pytest and a configured-but-unrun Jest, both keyed
    # `test.unit`. A plain comprehension kept whichever the collector
    # happened to emit last, downgrading an enforced suite and discarding
    # its evidence.
    for r in inferred:
        merged[r.key] = _combine(merged[r.key], r) if r.key in merged else r

    seen_observed: set[tuple[str, str | None]] = set()
    for r in observed:
        if r.key in seen_observed:
            merged[r.key] = _combine(merged[r.key], r)
        else:
            merged[r.key] = r
            seen_observed.add(r.key)

    # A language-unspecified placeholder and a language-specific finding for
    # the same probe have different keys, so both survived -- leaving the
    # artifact asserting that linting is absent and that Python linting is
    # enforced. Once any language has an answer, the unspecified one is noise.
    languaged = {pid for (pid, lang) in merged if lang is not None}
    return [
        r
        for key, r in merged.items()
        if not (key[1] is None and key[0] in languaged and r.outcome in (Outcome.ABSENT, Outcome.UNREADABLE))
    ]
