"""Read a repository from the local filesystem.

Mirrors the interface of `github.py` so a collector does not know or care
which it is talking to. That symmetry is what lets Code Futures analyse the
checkout it is running inside, with no token, no network, and no clone.

It exists for the self-analysis job in this repository's own CI, which is both
a test of the collectors against a real repository and the most honest
demonstration available: a tool that reports on engineering practice should be
willing to say what its own looks like.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .detect import is_vendored


def tree(root: Path) -> list[str] | None:
    """Every tracked path, repository-relative.

    Uses `git ls-files` rather than walking the filesystem, so build output
    and anything gitignored is excluded for free.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.splitlines() if p]


def file(root: Path, path: str) -> str | None:
    """File contents, or `None` when absent or unreadable.

    Decoding is lenient for the same reason it is in the GitHub reader: a
    strict UTF-8 read of a UTF-16 file produces mojibake rather than an error,
    which would turn an honest `unreadable` into a wrong finding.
    """
    target = root / path
    try:
        raw = target.read_bytes()
    except OSError:
        return None
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def workflows(root: Path) -> dict[str, str] | None:
    """Every workflow file, keyed by repository-relative path.

    An empty dict means there are none, which is a finding. `None` means we
    could not look, which is not.
    """
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return {} if root.is_dir() else None
    out: dict[str, str] = {}
    for p in sorted(directory.iterdir()):
        if p.suffix not in (".yml", ".yaml"):
            continue
        body = file(root, f".github/workflows/{p.name}")
        if body is not None:
            out[f".github/workflows/{p.name}"] = body
    return out


def log(root: Path) -> str | None:
    """Commit history in the format `structure.parse_log` expects.

    Requires full history. A CI checkout defaults to depth 1, which yields
    exactly one commit and would make every structural finding vacuous, so
    the workflow sets `fetch-depth: 0` and the caller checks `shallow`.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "log", "--name-only", "--no-merges", "--format=%H"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return out.stdout if out.returncode == 0 else None


def is_shallow(root: Path) -> bool:
    """Whether the checkout has truncated history.

    Worth checking explicitly: a shallow clone does not fail, it silently
    reports one commit, and every structural finding computed from it would
    be wrong rather than missing.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return out.stdout.strip() == "true"


def head(root: Path) -> tuple[str | None, str | None]:
    """Short SHA and branch name of the current checkout."""

    def _run(*args: str) -> str | None:
        r = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return r.stdout.strip() or None if r.returncode == 0 else None

    return _run("rev-parse", "--short", "HEAD"), _run("rev-parse", "--abbrev-ref", "HEAD")


def contributor_count(root: Path) -> int | None:
    """How many distinct authors, never who.

    A count is aggregate. Identities joined to an assessment would be an
    education record, so this returns a number and discards the names.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%ae"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if out.returncode != 0:
        return None
    return len({line.strip() for line in out.stdout.splitlines() if line.strip()}) or None


def config_paths(root: Path, wanted: set[str]) -> dict[str, str]:
    """Contents of every non-vendored file whose basename a signal matches."""
    paths = tree(root) or []
    out: dict[str, str] = {}
    for p in paths:
        if is_vendored(p):
            continue
        if p.rsplit("/", 1)[-1] in wanted:
            body = file(root, p)
            if body is not None:
                out[p] = body
    return out
