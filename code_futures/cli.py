"""Command line interface.

    code-futures doctor                 verify credentials before trusting output
    code-futures probe OWNER/REPO       one repository, to stdout
    code-futures sweep                  every active repository, to the snapshot
    code-futures render SNAPSHOT        snapshot to reports on disk

`doctor` is not optional politeness. A sweep refuses to run until it passes,
because an unauthorised token produces a complete, plausible, entirely wrong
report rather than an error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import collectors, config, github, render
from .schema import PROBES, RepositoryReport, Snapshot


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def cmd_doctor(args, cfg) -> int:
    print(f"code-futures doctor — organisation {cfg.github_org}\n")
    ok = True

    auth = github.check_authorisation(cfg.github_org)
    print(f"  [{'ok' if auth.ok else 'FAIL'}] GitHub authorisation")
    print(f"         {auth.detail}")
    if auth.scopes:
        print(f"         scopes: {', '.join(auth.scopes)}")
    ok = ok and auth.ok

    import shutil

    have_sc = shutil.which("scorecard") is not None
    print(f"  [{'ok' if have_sc else 'warn'}] OpenSSF Scorecard")
    print("         " + ("installed" if have_sc else "not installed; those probes report unreadable"))

    print(f"  [{'ok' if cfg.has_sonar else 'warn'}] SonarQube Cloud")
    print("         " + (f"token present, organisation {cfg.sonar_org}" if cfg.has_sonar
                         else "no token; the Code Quality Analysis section reports unreadable"))

    if cfg.practice_snapshot:
        exists = cfg.practice_snapshot.exists()
        print(f"  [{'ok' if exists else 'warn'}] practice baseline")
        print(f"         {cfg.practice_snapshot}" + ("" if exists else " (not found)"))

    for w in cfg.warnings:
        print(f"  [warn] {w}")

    print()
    if ok:
        print("Authorisation confirmed. A zero from here is a finding, not a permissions error.")
    else:
        print("REFUSING to sweep. An unauthorised token returns 403 on every repository,")
        print("which renders as every team failing everything rather than as an error.")
        print("See docs/credentials.md.")
    return 0 if ok else 1


def _build(repository: str, cfg, practice: dict | None) -> RepositoryReport:
    results = []
    languages: list[str] = []

    entry = (practice or {}).get(repository.split("/")[-1])
    if entry:
        results += collectors.from_practice_snapshot(entry)

    caps, languages = collectors.capabilities(repository)
    results += caps
    results += collectors.scorecard(repository, cfg)
    results += collectors.sonar(repository, cfg)

    # Any probe no collector spoke to is unreadable, never absent. A silently
    # missing probe would render as a gap in somebody's repository.
    seen = {r.id for r in results}
    from .schema import Outcome, ProbeResult

    for probe in PROBES:
        if probe.id not in seen:
            results.append(
                ProbeResult(
                    id=probe.id, outcome=Outcome.UNREADABLE,
                    attributes={"reason": "No collector reported on this probe."},
                    source="none",
                )
            )

    branch = github.default_branch(repository)
    return RepositoryReport(
        repository=repository,
        collected_at=_now(),
        commit=github.head_commit(repository, branch) if branch else None,
        languages=languages,
        results=results,
        contributors=github.contributor_count(repository),
    )


def cmd_probe(args, cfg) -> int:
    auth = github.check_authorisation(cfg.github_org)
    if not auth.ok:
        print(f"authorisation failed: {auth.detail}", file=sys.stderr)
        return 1
    practice = collectors.load_practice_snapshot(cfg.practice_snapshot) if cfg.practice_snapshot else None
    report = _build(args.repository, cfg, practice)
    print(render.render(report))
    return 0


def cmd_sweep(args, cfg) -> int:
    auth = github.check_authorisation(cfg.github_org)
    if not auth.ok:
        print(f"REFUSING to sweep: {auth.detail}", file=sys.stderr)
        return 1

    names = args.repositories or []
    if not names:
        repos = github.list_repositories(cfg.github_org)
        if repos is None:
            print("could not list repositories", file=sys.stderr)
            return 1
        names = [r["full_name"] for r in repos]

    practice = collectors.load_practice_snapshot(cfg.practice_snapshot) if cfg.practice_snapshot else None
    snap = Snapshot(collected_at=_now())
    for i, name in enumerate(names, 1):
        print(f"  [{i}/{len(names)}] {name}", file=sys.stderr)
        try:
            snap.reports.append(_build(name, cfg, practice))
        except Exception as exc:  # noqa: BLE001 - one repo must not end the sweep
            snap.failures[name] = str(exc)

    out = cfg.output_path()
    out.mkdir(parents=True, exist_ok=True)
    path = out / "code-futures.json"
    path.write_text(snap.dumps())
    print(f"\n{len(snap.reports)} reports, {len(snap.failures)} failures -> {path}", file=sys.stderr)
    return 0


def cmd_render(args, cfg) -> int:
    snap = Snapshot.loads(Path(args.snapshot).read_text())
    out = Path(args.output or cfg.output_path() / "reports")
    out.mkdir(parents=True, exist_ok=True)
    for report in snap.reports:
        name = report.repository.replace("/", "_")
        (out / f"{name}.md").write_text(render.render(report))
    print(f"{len(snap.reports)} reports -> {out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="code-futures", description=__doc__)
    p.add_argument("--config", type=Path, help="config file (default ~/.config/code-futures/config.toml)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="verify credentials before trusting any output")

    sp = sub.add_parser("probe", help="report on one repository")
    sp.add_argument("repository", help="OWNER/REPO")

    ss = sub.add_parser("sweep", help="report on every repository, writing a snapshot")
    ss.add_argument("repositories", nargs="*", help="OWNER/REPO, or empty for the whole organisation")

    sr = sub.add_parser("render", help="render a snapshot to markdown reports")
    sr.add_argument("snapshot")
    sr.add_argument("-o", "--output")

    args = p.parse_args(argv)
    cfg = config.load(args.config)
    return {
        "doctor": cmd_doctor, "probe": cmd_probe, "sweep": cmd_sweep, "render": cmd_render,
    }[args.command](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
