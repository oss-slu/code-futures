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
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import collectors, config, github, local, observe, render
from .schema import PROBES, Outcome, ProbeResult, RepositoryReport, Section, Snapshot


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


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
    print(
        "         "
        + (
            f"token present, organisation {cfg.sonar_org}"
            if cfg.has_sonar
            else "no token; the Code Quality Analysis section reports unreadable"
        )
    )

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
    results += collectors.structural(repository, github.tree(repository))
    results += collectors.scorecard(repository, cfg)
    results += collectors.sonar(repository, cfg)

    # Any probe no collector spoke to is unreadable, never absent. A silently
    # missing probe would render as a gap in somebody's repository.
    seen = {r.id for r in results}
    for probe in PROBES:
        if probe.id not in seen:
            results.append(
                ProbeResult(
                    id=probe.id,
                    outcome=Outcome.UNREADABLE,
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


def cmd_self(args, cfg) -> int:
    """Analyse the checkout this is running inside.

    A smoke test and a demonstration at once: a tool that reports on
    engineering practice should be willing to say what its own looks like,
    and running the collectors against a real repository catches breakage
    that unit tests with hand-built fixtures do not.

    Entirely local. Structure comes from the checkout's git history,
    capability detection from its files, and the baseline from the
    filesystem. No token, no network, nothing to configure -- which is what
    makes it usable as a CI step.
    """
    root = Path(args.path).resolve()

    if local.is_shallow(root):
        print(
            "refusing: this checkout is shallow, so the history is truncated.\n"
            "Structural findings computed from it would be wrong rather than missing.\n"
            "Use actions/checkout with fetch-depth: 0.",
            file=sys.stderr,
        )
        return 1

    paths = local.tree(root)
    if paths is None:
        print(f"not a git repository: {root}", file=sys.stderr)
        return 1

    wfs = local.workflows(root) or {}
    reader = lambda p: local.file(root, p)  # noqa: E731

    results = collectors.local_practice(paths, reader)
    caps, languages = collectors.capabilities(
        args.repository, paths=paths, wfs=wfs, read_file=reader, _local=True
    )
    results += caps
    results += collectors.structural(args.repository, paths, log_text=local.log(root), read_file=reader)

    # Scorecard and Sonar need a network and a token. Absent either, their
    # probes report unreadable, which is the designed degradation rather than
    # a failure of the run.
    if args.remote:
        results += collectors.scorecard(args.repository, cfg)
        results += collectors.sonar(args.repository, cfg)

    seen = {r.id for r in results}
    for probe in PROBES:
        if probe.id not in seen:
            results.append(
                ProbeResult(
                    id=probe.id,
                    outcome=Outcome.UNREADABLE,
                    attributes={"reason": "Not collected in a local run; this needs a network."},
                    source="none",
                )
            )

    sha, _branch = local.head(root)
    report = RepositoryReport(
        repository=args.repository,
        collected_at=_now(),
        commit=sha,
        languages=languages,
        results=results,
        contributors=local.contributor_count(root),
    )

    text = render.render(report)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)

    counts = report.counts()
    print(
        "  ".join(f"{o.value}={counts[o]}" for o in Outcome),
        file=sys.stderr,
    )
    # The smoke test proper: a run where nothing could be established means
    # the collectors are broken, not that the repository is bare.
    if report.wholly_unreadable:
        print("FAILED: every probe was unreadable", file=sys.stderr)
        return 1
    return 0


def _analyse_local(root: Path, repository: str):
    """Local inference shared by `self` and `ci`."""
    paths = local.tree(root)
    if paths is None:
        return None, []
    wfs = local.workflows(root) or {}

    def reader(p):
        return local.file(root, p)

    results = collectors.local_practice(paths, reader)
    caps, languages = collectors.capabilities(repository, paths=paths, wfs=wfs, read_file=reader, _local=True)
    results += caps
    results += collectors.structural(repository, paths, log_text=local.log(root), read_file=reader)
    return results, languages


def cmd_ci(args, cfg) -> int:
    """Write a Code Futures artifact from inside a CI run.

    **This never renders a report, and the absence is the mechanism rather
    than a policy.** CI output on a public repository is public; the composed
    report is internal and is assembled elsewhere. Keeping `render` out of
    this function is what stops an internal document being produced in a
    public pipeline, and it is why the workflow job is called `summarize`.

    What it writes is the artifact: facts about public code, every one of
    them already derivable by anyone who can clone the repository.
    """
    root = Path(args.path).resolve()
    repository = args.repository or os.environ.get("GITHUB_REPOSITORY", "unknown/unknown")

    # A shallow checkout has one commit. Every structural finding computed
    # from it would be wrong rather than missing, which is the worse failure,
    # so this refuses rather than reporting from truncated history.
    if local.is_shallow(root):
        print(
            "refusing: shallow checkout. Structural findings from truncated history "
            "would be wrong rather than missing. Use actions/checkout with fetch-depth: 0.",
            file=sys.stderr,
        )
        return 1

    inferred, languages = _analyse_local(root, repository)
    if inferred is None:
        print(f"not a git repository: {root}", file=sys.stderr)
        return 1

    observed = observe.load(Path(args.observations)) if args.observations else []
    results = observe.merge(inferred, observed)

    seen = {r.id for r in results}
    for probe in PROBES:
        if probe.id not in seen:
            results.append(
                ProbeResult(
                    id=probe.id,
                    outcome=Outcome.UNREADABLE,
                    attributes={"reason": "Not collected in this run."},
                    source="none",
                )
            )

    sha, _ = local.head(root)
    report = RepositoryReport(
        repository=repository,
        collected_at=_now(),
        commit=os.environ.get("GITHUB_SHA", sha or "")[:7] or None,
        languages=languages,
        results=results,
        contributors=local.contributor_count(root),
    )

    out = Path(args.output)
    out.write_text(Snapshot(collected_at=report.collected_at, reports=[report]).dumps())
    print(f"wrote {out}", file=sys.stderr)

    summary = _ci_summary(report, len(observed))
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(step_summary).write_text(summary)
    print(summary)
    return 0


def _ci_summary(report: RepositoryReport, observations: int) -> str:
    """A terse table for the job summary.

    Not the report. Job summaries on a public repository are readable by
    anyone with no account, so this carries outcomes and nothing that frames
    them -- no guidance, no recommendations, no prose about what a team
    should do next. Those belong in the document their tech lead receives.
    """
    counts = report.counts()
    lines = [
        "## Code Futures",
        "",
        f"`{report.repository}` · schema `code-futures/v1` · {observations} observation(s) from this run",
        "",
        "| Outcome | Count |",
        "| --- | --- |",
    ]
    lines += [f"| {o.value} | {counts[o]} |" for o in Outcome]
    lines += ["", "| Probe | Outcome |", "| --- | --- |"]
    for section in Section:
        for r in report.by_section(section):
            label = f"`{r.id}`" + (f" ({r.language})" if r.language else "")
            lines.append(f"| {label} | {r.outcome.value} |")
    lines += [
        "",
        "These are facts about this repository, all of them derivable from the "
        "code itself. The composed report, with what the findings mean and what "
        "to do about them, is assembled outside CI and reaches your tech lead.",
        "",
    ]
    return "\n".join(lines)


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

    sf = sub.add_parser("self", help="analyse the checkout this is running inside")
    sf.add_argument("--path", default=".", help="repository root (default: cwd)")
    sf.add_argument("--repository", default="oss-slu/code-futures", help="name to label the report with")
    sf.add_argument("-o", "--output", help="write markdown here instead of stdout")
    sf.add_argument("--remote", action="store_true", help="also run Scorecard and Sonar")

    sc = sub.add_parser("ci", help="write an artifact from inside a CI run (never a report)")
    sc.add_argument("--path", default=".", help="repository root (default: cwd)")
    sc.add_argument("--repository", default=None, help="defaults to $GITHUB_REPOSITORY")
    sc.add_argument("--observations", default=None, help="directory of observation fragments")
    sc.add_argument("-o", "--output", default="code-futures.json")

    sr = sub.add_parser("render", help="render a snapshot to markdown reports")
    sr.add_argument("snapshot")
    sr.add_argument("-o", "--output")

    args = p.parse_args(argv)
    cfg = config.load(args.config)
    return {
        "doctor": cmd_doctor,
        "self": cmd_self,
        "ci": cmd_ci,
        "probe": cmd_probe,
        "sweep": cmd_sweep,
        "render": cmd_render,
    }[args.command](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
