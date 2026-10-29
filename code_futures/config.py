"""Configuration loading.

Order of precedence: environment, then `~/.config/code-futures/config.toml`,
then defaults. Credentials never come from the repository, and the loader has
no path that would read one from there.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "code-futures" / "config.toml"

DEFAULT_CHECKS = (
    "SAST",
    "Security-Policy",
    "Token-Permissions",
    "Dangerous-Workflow",
    "Dependency-Update-Tool",
)


@dataclass
class Config:
    github_token: str | None = None
    github_org: str = "oss-slu"
    sonar_token: str | None = None
    sonar_org: str = "oss-slu"
    sonar_project_key: str = "{owner}_{repo}"
    scorecard_checks: tuple[str, ...] = DEFAULT_CHECKS
    scorecard_timeout: int = 300
    practice_snapshot: Path | None = None
    output_dir: str = "evidence/{term}"
    term: str = "fall-2026"
    warnings: list[str] = field(default_factory=list)

    @property
    def has_sonar(self) -> bool:
        return bool(self.sonar_token)

    def output_path(self) -> Path:
        return Path(self.output_dir.format(term=self.term))

    def sonar_key(self, repository: str) -> str:
        owner, _, repo = repository.partition("/")
        return self.sonar_project_key.format(owner=owner, repo=repo)


def _gh_token() -> str | None:
    """Fall back to the `gh` CLI's token.

    Preferred over asking for a second credential on a workstation where `gh`
    is already logged in and SSO-authorised. `gh` is also how every API call
    in this package is made, so depending on it costs nothing extra: it
    handles auth, pagination and SSO, and keeps an HTTP library out of the
    dependency tree.
    """
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token or None


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    data: dict = {}
    warnings: list[str] = []

    if path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"could not read {path}: {exc}")
        else:
            mode = path.stat().st_mode & 0o077
            if mode:
                warnings.append(f"{path} is group- or world-readable; chmod 600 it")

    gh = data.get("github", {})
    sonar = data.get("sonar", {})
    sc = data.get("scorecard", {})
    practice = data.get("practice", {})
    out = data.get("output", {})

    snapshot = os.environ.get("CODE_FUTURES_PRACTICE") or practice.get("snapshot") or ""

    cfg = Config(
        github_token=os.environ.get("GITHUB_TOKEN") or gh.get("token") or _gh_token(),
        github_org=os.environ.get("GITHUB_ORG") or gh.get("organization") or "oss-slu",
        sonar_token=os.environ.get("SONAR_TOKEN") or sonar.get("token") or None,
        sonar_org=os.environ.get("SONAR_ORGANIZATION") or sonar.get("organization") or "oss-slu",
        sonar_project_key=sonar.get("project_key") or "{owner}_{repo}",
        scorecard_checks=tuple(sc.get("checks") or DEFAULT_CHECKS),
        scorecard_timeout=int(sc.get("timeout") or 300),
        practice_snapshot=Path(snapshot).expanduser() if snapshot else None,
        output_dir=out.get("directory") or "evidence/{term}",
        term=os.environ.get("CODE_FUTURES_TERM") or out.get("term") or "fall-2026",
        warnings=warnings,
    )

    if not cfg.sonar_token:
        cfg.warnings.append(
            "no sonar.token; the Code Quality Analysis section will report unreadable"
        )
    if cfg.practice_snapshot and not cfg.practice_snapshot.exists():
        cfg.warnings.append(f"practice snapshot not found at {cfg.practice_snapshot}")
    return cfg
