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


def _one(frag: object) -> ProbeResult | None:
    if not isinstance(frag, dict):
        return None
    probe_id = frag.get("probe")
    if probe_id not in PROBES_BY_ID:
        return None
    try:
        outcome = Outcome(frag.get("outcome", ""))
    except ValueError:
        return None

    probe = PROBES_BY_ID[probe_id]
    # A job cannot claim a rung the probe does not have. Without this a
    # malformed fragment would raise inside the constructor and take the
    # summarize job down with it.
    if outcome is Outcome.ENFORCED and probe.top_rung is not Outcome.ENFORCED:
        outcome = Outcome.PRESENT

    attributes = {
        k: str(v)
        for k, v in frag.items()
        if k not in ("probe", "outcome", "tool", "detail", "language", "evidence") and v is not None
    }
    try:
        return ProbeResult(
            id=probe_id,
            outcome=outcome,
            language=frag.get("language") if probe.per_language else None,
            tool=frag.get("tool"),
            detail=str(frag.get("detail", "")),
            evidence=[str(e) for e in frag.get("evidence", []) if e],
            attributes=attributes,
            source="ci",
        )
    except ValueError:
        return None


def merge(inferred: list[ProbeResult], observed: list[ProbeResult]) -> list[ProbeResult]:
    """Combine inference with observation, observation winning.

    Keyed by `(probe, language)` so a polyglot repository keeps both answers.

    The direction is not arbitrary. A CI run can report that a test command
    ran and what it did; nothing read from outside the run can establish
    that. Where the two disagree, the one that watched is the better
    evidence.
    """
    merged = {r.key: r for r in inferred}
    for r in observed:
        merged[r.key] = r
    return list(merged.values())
