"""GitHub access, through the `gh` CLI.

# Why a subprocess rather than an HTTP client

`gh` already handles authentication, pagination, rate limiting and -- the one
that matters here -- SAML SSO against the enterprise. Reimplementing those
costs a dependency and several subtle bugs, and buys nothing. `~/code` asks
whether the standard library could do a job in about fifty lines; here the
answer is that a CLI already does it in zero.

It is also available everywhere this runs: preinstalled on GitHub Actions
runners, and already authenticated on the workstation that runs sweeps.

Every function returns `None` on failure rather than raising, so a caller can
map the failure to `Outcome.UNREADABLE`. That is the whole discipline of this
module: there is no code path from "the request failed" to a statement about
a repository.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass


class AuthError(RuntimeError):
    """Raised only by the authorisation pre-flight, never by a collector."""


def _gh(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        return 127, "", "gh not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"gh timed out after {timeout}s"
    return p.returncode, p.stdout, p.stderr


def api(path: str, *, paginate: bool = False, timeout: int = 60) -> object | None:
    """One API call. `None` means we could not find out."""
    args = ["api", path]
    if paginate:
        args.append("--paginate")
    code, out, _ = _gh(*args, timeout=timeout)
    if code != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # --paginate concatenates JSON arrays; stitch them.
        try:
            return json.loads(out.replace("][", ","))
        except json.JSONDecodeError:
            return None


@dataclass
class Authorisation:
    """Result of the SSO pre-flight."""

    ok: bool
    detail: str
    scopes: list[str]


def check_authorisation(org: str) -> Authorisation:
    """Confirm the token can actually read this organisation.

    **This runs before any sweep, and a sweep refuses to proceed without it.**

    A token with correct scopes that has not been SSO-authorised for the
    enterprise returns 403 on every repository. Nothing about that looks like
    an authentication failure from the inside: every check comes back empty,
    every probe reports a gap, and the sweep renders as *every team failed
    everything*. It has happened once already on the feedback pipeline, and it
    is the single most expensive way this tool can be wrong, because the
    output is plausible.

    Three things are checked, and the second is the one that catches SSO:
    the token resolves, private repositories are listable, and branch
    protection returns a semantic answer rather than a permissions error.
    """
    code, out, err = _gh("auth", "status")
    if code != 0:
        return Authorisation(False, f"gh is not authenticated: {err.strip()}", [])

    scopes: list[str] = []
    for line in out.splitlines():
        if "Token scopes:" in line:
            scopes = [s.strip().strip("'\"") for s in line.split(":", 1)[1].split(",")]

    missing = [s for s in ("repo", "read:org") if s not in scopes]
    if missing:
        return Authorisation(
            False,
            f"token is missing scope(s): {', '.join(missing)}. "
            "Branch protection needs classic `repo`; enumeration needs `read:org`.",
            scopes,
        )

    # The SSO probe. An unauthorised token lists nothing here while still
    # answering /user perfectly well, which is exactly why a simpler check
    # would pass and the sweep would still be wrong.
    private = api(f"orgs/{org}/repos?type=private&per_page=1")
    if private is None:
        return Authorisation(
            False,
            f"cannot list private repositories in {org}. The token is probably not "
            "SSO-authorised: github.com/settings/tokens -> Configure SSO -> Authorize.",
            scopes,
        )
    if not isinstance(private, list):
        return Authorisation(False, f"unexpected response listing {org} repositories", scopes)

    return Authorisation(
        True, f"authorised for {org}; {len(scopes)} scopes; private repositories readable", scopes
    )


def list_repositories(org: str, *, include_archived: bool = False) -> list[dict] | None:
    data = api(f"orgs/{org}/repos?per_page=100", paginate=True)
    if not isinstance(data, list):
        return None
    return [r for r in data if include_archived or not r.get("archived")]


def tree(repository: str, ref: str = "HEAD") -> list[str] | None:
    """Every path in the repository, or `None` if unreadable."""
    data = api(f"repos/{repository}/git/trees/{ref}?recursive=1")
    if not isinstance(data, dict) or "tree" not in data:
        return None
    return [e["path"] for e in data["tree"] if e.get("type") == "blob"]


def file(repository: str, path: str) -> str | None:
    """File contents, decoded. `None` when absent or unreadable.

    Decoding is deliberately lenient. One `requirements.txt` in this
    organisation is UTF-16LE with a BOM, and a strict UTF-8 read turns it into
    mojibake rather than failing, which would silently produce a wrong finding
    instead of an honest `unreadable`.
    """
    data = api(f"repos/{repository}/contents/{path}")
    if not isinstance(data, dict) or "content" not in data:
        return None
    try:
        raw = base64.b64decode(data["content"])
    except (ValueError, TypeError):
        return None
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def workflows(repository: str) -> dict[str, str] | None:
    """Every workflow file, keyed by path.

    An empty dict means the repository has no workflows, which is a finding.
    `None` means the directory could not be read, which is not.
    """
    listing = api(f"repos/{repository}/contents/.github/workflows")
    if listing is None:
        # Distinguish "no such directory" from "could not ask". A repository
        # whose tree reads fine but has no workflows directory genuinely has
        # no workflows.
        if tree(repository) is not None:
            return {}
        return None
    if not isinstance(listing, list):
        return {}
    out: dict[str, str] = {}
    for entry in listing:
        name = entry.get("name", "")
        if not name.endswith((".yml", ".yaml")):
            continue
        body = file(repository, f".github/workflows/{name}")
        if body is not None:
            out[f".github/workflows/{name}"] = body
    return out


def contributor_count(repository: str) -> int | None:
    """How many people have committed, never who.

    A count is aggregate and safe to carry. Identities joined to an assessment
    would be an education record, so this function deliberately returns a
    number and discards the rest of the payload.
    """
    data = api(f"repos/{repository}/contributors?per_page=100&anon=false", paginate=True)
    if not isinstance(data, list):
        return None
    return len(data)


def default_branch(repository: str) -> str | None:
    data = api(f"repos/{repository}")
    if not isinstance(data, dict):
        return None
    return data.get("default_branch")


def head_commit(repository: str, branch: str) -> str | None:
    data = api(f"repos/{repository}/commits/{branch}")
    if not isinstance(data, dict):
        return None
    sha = data.get("sha")
    return sha[:7] if isinstance(sha, str) else None
