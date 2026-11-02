"""Render a `RepositoryReport` to Markdown.

Pure: a report in, a string out, no I/O and no clock. That makes the output
diffable against its own past, which is the whole point of a report a team
reads more than once.

Never called from CI. The CI path writes an artifact and stops; reports are
assembled outside it. Keeping the entry point unreachable in that context is
the entire mechanism preventing an internal document being produced in a public
pipeline -- not a policy, just a function nobody calls there.
"""

from __future__ import annotations

import re

from . import strings as S
from .schema import Outcome, ProbeResult, RepositoryReport, Section

_OUTCOME_ORDER = (Outcome.ENFORCED, Outcome.PRESENT, Outcome.ABSENT, Outcome.UNREADABLE)

# Capability probes, where `present` means configured-but-never-run. The
# baseline probes cannot reach `enforced`, so a `present` there is simply the
# artifact existing and carries no such implication.
_CAPABILITY = (
    "lint",
    "format",
    "test.unit",
    "test.integration",
    "test.e2e",
    "test.coverage",
    "test.mutation",
)


def _detail(r: ProbeResult) -> str:
    """Fill the template for one finding.

    Falls back to the generic wording when a collector did not supply every
    slot. A report should never invent specificity it does not have, so an
    under-populated finding says less rather than guessing.
    """
    if r.detail:
        return r.detail
    if r.outcome is Outcome.UNREADABLE:
        reason = r.attributes.get("reason") or "We could not read this."
        return S.UNREADABLE_DETAIL.format(reason=reason.rstrip(".") + ".")
    # A tool can be enforced with no config file to cite. Six repositories in
    # the organisation are in exactly that state, and naming a config path we
    # do not have would be a fabrication.
    #
    # Restricted to capability probes. `ci.present` and the Sonar probes are
    # also enforced-without-a-config, but they have their own templates and
    # routing them here dropped all three to the generic wording.
    template = None
    if r.id in _CAPABILITY and r.outcome is Outcome.ENFORCED and not r.attributes.get("config"):
        template = S.DETAIL_NOCONFIG.get(r.id)
    if template is None:
        template = S.DETAIL.get((r.id, r.outcome))
    if template is None:
        return S.GENERIC[r.outcome]
    slots = {"tool": r.tool or "", **{k: v for k, v in r.attributes.items() if v is not None}}
    # Derive the plural marker rather than requiring every collector to
    # supply it. A missing slot falls back to the generic wording, so an
    # omission here would quietly degrade a finding rather than fail.
    if "{s}" in template and "s" not in slots:
        try:
            slots["s"] = "" if int(slots.get("count", 0)) == 1 else "s"
        except (TypeError, ValueError):
            slots["s"] = "s"
    try:
        return template.format(**slots)
    except KeyError:
        return S.GENERIC[r.outcome]


def _label(r: ProbeResult) -> str:
    return f"`{r.id}`" if not r.language else f"`{r.id}` ({r.language})"


def _table(rows: list[list[str]]) -> str:
    """Pipe table with aligned columns.

    Alignment is cosmetic in rendered Markdown and load-bearing in a diff: an
    unaligned table reflows every row when one cell changes, which buries the
    finding that actually moved.
    """
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    out = ["| " + " | ".join(rows[0][c].ljust(widths[c]) for c in range(len(widths))) + " |"]
    out.append("| " + " | ".join("-" * widths[c] for c in range(len(widths))) + " |")
    for row in rows[1:]:
        out.append("| " + " | ".join(row[c].ljust(widths[c]) for c in range(len(widths))) + " |")
    return "\n".join(out)


def _section_body(report: RepositoryReport, section: Section) -> str:
    results = report.by_section(section)
    if not results:
        return "_Nothing to report in this section._"
    if section in S.TABLE_SECTIONS:
        per_lang = any(r.language for r in results)
        head = ["Probe", "Language", "Outcome", "Evidence"] if per_lang else ["Probe", "Outcome", "Evidence"]
        rows = [head]
        for r in results:
            cells = [f"`{r.id}`"]
            if per_lang:
                cells.append(r.language or "—")
            cells += [f"**{r.outcome.value}**", _detail(r)]
            rows.append(cells)
        return _table(rows)
    lines = []
    for r in results:
        lines.append(f"* **{_label(r)}** — **{r.outcome.value}**. {_detail(r)}")
        # Structural findings are only actionable with the files named, so
        # their evidence is shown rather than kept in the snapshot. A coupled
        # pair a team cannot see is a statistic; one they can see is a
        # conversation about where a boundary belongs.
        if section is Section.STRUCTURE and r.outcome is Outcome.PRESENT and r.evidence:
            for item in r.evidence[:6]:
                lines.append(f"    * `{item}`" if "<->" not in item else f"    * {item}")
    return "\n".join(lines)


def _notes(report: RepositoryReport, section: Section) -> list[str]:
    """Conditional notes for a section.

    A condition decides whether a note renders, never what it says. Every note
    is a fixed block in `strings.py`.
    """
    out: list[str] = []
    got = {r.key: r for r in report.by_section(section)}

    if section is Section.STRUCTURE:
        hs = got.get(("structure.hotspots", None))
        if hs and hs.outcome is Outcome.PRESENT:
            if hs.attributes.get("thin") == "true":
                out.append(S.NOTE_THIN_HISTORY.format(commits=hs.attributes.get("commits", "Few")))
            out.append(S.NOTE_HOTSPOT)
        cp = got.get(("structure.change_coupling", None))
        if cp and cp.outcome is Outcome.PRESENT:
            out.append(S.NOTE_COUPLING)
        coh = got.get(("structure.cohesion", None))
        if coh and coh.outcome is Outcome.PRESENT and int(coh.attributes.get("across", "0") or 0):
            out.append(S.NOTE_COHESION)
        br = got.get(("structure.blast_radius", None))
        if br and br.outcome is Outcome.PRESENT:
            out.append(S.NOTE_BLAST_RADIUS)
        tf = got.get(("structure.tidy_first", None))
        if tf and tf.outcome is Outcome.PRESENT:
            out.append(S.NOTE_TIDY_FIRST)
        # Always last in this section, and always present. The limits of
        # automated structural analysis are part of the finding, not a
        # footnote to it.
        out.append(S.NOTE_MANUAL_ANALYSIS)

    if section is Section.REVIEW:
        req = got.get(("review.required", None))
        prac = got.get(("review.practiced", None))
        # The interesting case is disagreement between the rule and what
        # actually happens, in either direction.
        readable = (
            req and prac and req.outcome is not Outcome.UNREADABLE and prac.outcome is not Outcome.UNREADABLE
        )
        if readable and req.outcome != prac.outcome:
            out.append(S.NOTE_POLICY_VS_PRACTICE)

    if section is Section.BASELINE:
        lic = got.get(("practice.license", None))
        if lic and lic.outcome is Outcome.PRESENT and not lic.attributes.get("spdx"):
            out.append(S.NOTE_UNIDENTIFIABLE_LICENCE)

    if section is Section.LINT:
        langs = sorted({r.language for r in report.by_section(section) if r.language})
        outcomes = {r.outcome for r in report.by_section(section)}
        if len(langs) > 1 and len(outcomes) > 1:
            out.append(S.NOTE_LANGUAGES_DISAGREE.format(count=_spell(len(langs)), languages=_join(langs)))
        else:
            stale = [r for r in report.by_section(section) if r.outcome is Outcome.PRESENT]
            if stale:
                tools = _join([f"`{r.tool}`" for r in stale if r.tool]) or "A tool is"
                plural = len(stale) > 1
                out.append(
                    S.NOTE_CONFIGURED_NOT_RUN.format(
                        tools=tools + (" are" if plural else " is"),
                        pronoun="them" if plural else "it",
                        Pronoun_cap="They" if plural else "It",
                    )
                )

    if section in (Section.LINT, Section.TEST):
        loose = [r for r in report.by_section(section) if r.attributes.get("blocking") == "false"]
        if loose:
            tools = _join([f"`{r.tool}`" for r in loose if r.tool])
            out.append(
                S.NOTE_NON_BLOCKING.format(tools=(tools or "A check") + (" are" if len(loose) > 1 else " is"))
            )

    if section is Section.TEST:
        out.append(S.NOTE_COVERAGE_KINDS)
        results = report.by_section(section)
        absent = sum(1 for r in results if r.outcome is Outcome.ABSENT)
        if absent >= 2:
            out.append(
                S.NOTE_ABSENCE_OK.format(absent=_spell(absent).capitalize(), total=_spell(len(results)))
            )

    if section is Section.REVIEW:
        rv = got.get(("review.required", None))
        if rv and rv.outcome is Outcome.PRESENT:
            if rv.attributes.get("reviews") == "0":
                out.append(S.NOTE_HOLLOW_PROTECTION)
            if rv.attributes.get("admins") == "exempt":
                out.append(S.NOTE_ADMINS_EXEMPT)

    if section is Section.QUALITY:
        hs = got.get(("quality.hotspots_reviewed", None))
        if hs and hs.outcome in (Outcome.PRESENT, Outcome.ENFORCED):
            out.append(S.NOTE_HOTSPOTS)
        proj = got.get(("quality.project", None))
        if proj and proj.outcome is not Outcome.ABSENT:
            out.append(S.NOTE_SONAR_PUBLIC)

    if section is Section.SECURITY:
        bad = [r for r in report.by_section(section) if r.outcome is Outcome.UNREADABLE]
        if bad:
            out.append(
                S.NOTE_UNREADABLE.format(
                    probes=_join([f"`{r.id}`" for r in bad]),
                    pronoun="them" if len(bad) > 1 else "it",
                )
            )
    return out


_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _spell(n: int) -> str:
    """Spell small numbers. Prose reads badly with digits in it."""
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _anchor(heading: str) -> str:
    """The fragment GitHub will generate for a heading.

    Mirrors github-slugger: strip markup, lowercase, drop anything that is
    not a word character, space or hyphen, then replace **each** space with a
    hyphen. Runs of whitespace are not collapsed, so `1. Structure` becomes
    `1-structure` while an em-dash heading yields a double hyphen. Getting
    that wrong silently produces links that scroll nowhere.
    """
    s = re.sub(r"^\s*#+\s*", "", heading)  # tolerate a full heading line
    s = re.sub(r"\*\*|`", "", s).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return s.replace(" ", "-")


def _contents(report: RepositoryReport) -> str:
    """A table of contents with a finding count per section."""
    rows = [["Section", "Findings"]]
    for i, section in enumerate(Section, 1):
        heading = f"{i}. {section.value}"
        n = len(report.by_section(section))
        rows.append([f"[{heading}](#{_anchor(heading)})", str(n) if n else "—"])
    return "\n".join([S.CONTENTS_HEADING, "", _table(rows), "", S.CONTENTS_NOTE])


def _structure_sentences(report: RepositoryReport) -> str:
    """One or two clauses on what the structural analysis found.

    Assembled from findings that already exist below, so it cannot disagree
    with them, and omits anything that was not established rather than
    reporting an absence as a result.
    """
    got = {r.id: r for r in report.by_section(Section.STRUCTURE)}
    out: list[str] = []

    hs = got.get("structure.hotspots")
    if hs and hs.outcome is Outcome.PRESENT:
        n = hs.attributes.get("count", "Some")
        out.append(f"{n} hotspot{'' if n == '1' else 's'}")

    cp = got.get("structure.change_coupling")
    coh = got.get("structure.cohesion")
    if cp and cp.outcome is Outcome.PRESENT:
        n = cp.attributes.get("count", "several")
        clause = f"{n} coupled file pair{'' if n == '1' else 's'}"
        if coh and coh.outcome is Outcome.PRESENT:
            across = coh.attributes.get("across")
            if across and across != "0":
                clause += f" ({across} crossing a module boundary)"
        out.append(clause)

    ut = got.get("structure.untested_modules")
    if ut and ut.outcome is Outcome.PRESENT:
        out.append(f"{ut.attributes.get('count', 'some')} churning files with no test")

    if not out:
        return ""

    tf = got.get("structure.tidy_first")
    tail = ""
    if tf and tf.outcome is Outcome.PRESENT and tf.attributes.get("top"):
        tail = f" `{tf.attributes['top']}` would repay tidying soonest."
    return "; ".join(out).capitalize() + "." + tail


def _actionable(report: RepositoryReport) -> list[tuple[int, ProbeResult, dict]]:
    """Findings worth acting on, ranked.

    Shared by the summary and by "Where to start", so the item the summary
    names is by construction the item the recommendations lead with. Two
    independent rankings would eventually disagree, and a reader would have
    no way to tell which to trust.
    """
    out: list[tuple[int, ProbeResult, dict]] = []
    for r in report.results:
        spec = S.REMEDIATION.get(r.id)
        if not spec:
            continue
        if r.outcome.value not in spec.get("when", ("absent",)):
            continue
        # An identified licence is `present` and needs nothing. Only the
        # unidentifiable case is actionable.
        if r.id == "practice.license" and r.attributes.get("spdx"):
            continue
        out.append((spec["rank"], r, spec))
    out.sort(key=lambda x: (x[0], x[1].language or ""))
    return out


def _lead_item(report: RepositoryReport) -> dict | None:
    got = _actionable(report)
    return got[0][2] if got else None


def _probe_list(bad: list[ProbeResult], show: int = 4) -> str:
    """Name the unreadable probes, saying so when the list is truncated.

    An earlier version said "Eight probes unreadable" and then named four,
    which reads as a complete list and is not one.
    """
    if not bad:
        return ""
    names = [f"`{r.id}`" for r in bad[:show]]
    rest = len(bad) - len(names)
    joined = _join(names)
    if rest:
        return f"{joined}, and {_spell(rest) if rest <= 10 else rest} more"
    return joined


def _summary(report: RepositoryReport) -> str:
    """The precis at the top.

    Everything here is already stated below. That is what a summary is, and
    it is also why this is cheap: no new measurement, no new judgement, and
    nothing that can drift out of step with the sections.
    """
    counts = report.counts()
    n_findings = len(report.results)
    n_probes = len({r.id for r in report.results})
    langs = f" in {_join(report.languages)}" if report.languages else ""

    scope = (S.SUMMARY_SCOPE if report.commit else S.SUMMARY_SCOPE_NO_COMMIT).format(
        probes=_spell(n_probes) if n_probes <= 10 else n_probes,
        findings=n_findings,
        languages=langs,
        commit=report.commit or "",
    )
    parts = [S.SUMMARY_HEADING, "", scope.capitalize() if scope[0].isalpha() else scope, ""]
    parts.append(_table([[o.value for o in _OUTCOME_ORDER], [str(counts[o]) for o in _OUTCOME_ORDER]]))
    parts.append("")

    sentences = _structure_sentences(report)
    if sentences:
        parts.append(S.SUMMARY_STRUCTURE.format(sentences=sentences))
    else:
        # An empty structural result means one of two things, and they are not
        # interchangeable: the analysis ran and found nothing worth reporting,
        # or it could not run. Claiming the first when it was the second
        # renders our own failure as a clean bill of health.
        structural = report.by_section(Section.STRUCTURE)
        unreadable = [x for x in structural if x.outcome is Outcome.UNREADABLE]
        if structural and len(unreadable) == len(structural):
            reason = unreadable[0].attributes.get("reason", "").strip()
            parts.append(
                S.SUMMARY_STRUCTURE_UNREADABLE.format(reason=reason + (" " if reason else "")).replace(
                    "  ", " "
                )
            )
        else:
            parts.append(S.SUMMARY_NO_STRUCTURE)
    parts.append("")

    lead = _lead_item(report)
    if lead is None:
        # No ranked recommendation is not the same as nothing to do. It also
        # happens when every probe was unreadable, and when the only gap is a
        # probe the remediation table does not rank.
        gaps = [
            x
            for x in report.results
            if x.outcome is Outcome.ABSENT or (x.outcome is Outcome.PRESENT and x.id in _CAPABILITY)
        ]
        if gaps or report.unreadable():
            parts.append(S.SUMMARY_NO_RECOMMENDATION)
        else:
            parts.append(S.SUMMARY_ALL_CLEAR)
    else:
        label = lead["label"]
        parts.append(
            S.SUMMARY_FIRST_STEP.format(
                item=label[0].upper() + label[1:],
                where=_anchor("Where to start"),
            )
        )
    bad = report.unreadable()
    if bad:
        parts += [
            "",
            S.SUMMARY_UNREADABLE.format(
                count=_spell(len(bad)).capitalize(),
                s="" if len(bad) == 1 else "s",
                probes=_probe_list(bad),
            ),
        ]
    return "\n".join(parts)


def _where_to_start(report: RepositoryReport) -> str:
    """Rank the actionable findings and name one.

    Ranking is a fixed table in `strings.py` rather than a severity score,
    because the guidebook's argument is about sequencing rather than danger:
    anything with consequences outside the team first, then anything that makes
    the next fix cheaper, then the cheapest real improvement.

    `unreadable` findings are excluded. Asking a team to act on our own
    collection failure would be the worst instance of the mistake the fourth
    outcome exists to prevent.
    """
    actionable = _actionable(report)

    parts = [S.WHERE_TO_START_INTRO, ""]
    if not actionable:
        parts.append(S.WHERE_TO_START_NOTHING)
        return "\n".join(parts)

    _, lead_r, lead_spec = actionable[0]
    parts.append(S.WHERE_TO_START_LEAD.format(item=lead_spec["label"]))
    parts.append("")
    parts += [f"* {w}" for w in lead_spec["why"]]

    rest = actionable[1:4]
    if rest:
        parts += ["", S.WHERE_TO_START_THEN, ""]
        for i, (_, r, spec) in enumerate(rest, 1):
            slots = {"tool": r.tool or "the tool", **{k: v or "" for k, v in r.attributes.items()}}
            try:
                step = spec["step"].format(**slots)
            except KeyError:
                step = spec["step"]
            parts.append(f"{i}. {step}")
    parts += ["", S.WHERE_TO_START_CLOSE]
    return "\n".join(parts)


def render(
    report: RepositoryReport,
    *,
    fabricated: bool = False,
    classification: str = "Internal — shareable within the program, not published",
) -> str:
    """Render one repository report to Markdown."""
    p: list[str] = [f"# {S.TITLE.format(repository=report.repository)}", ""]

    if fabricated:
        p += [S.FABRICATED_BANNER.format(repository=report.repository), ""]

    meta = [["", ""], ["**Repository**", f"`{report.repository}`"]]
    if report.commit:
        meta.append(["**Commit**", f"`{report.commit}`"])
    meta.append(["**Collected**", report.collected_at])
    meta.append(["**Schema**", "`code-futures/v1`"])
    if report.languages:
        meta.append(["**Languages**", ", ".join(report.languages)])
    meta.append(["**Classification**", classification])
    p += [_table(meta), "", "---", ""]

    if report.wholly_unreadable:
        p += [S.WHOLLY_UNREADABLE, "", "---", ""]

    # Summary first. The counts live here rather than in a section of their
    # own: an "Outcomes" heading carrying the same table was duplication, and
    # it pushed the first real finding further down the page.
    p += [_summary(report), "", "---", ""]

    p += [S.HOW_TO_READ, ""]

    n_findings = len(report.results)
    n_probes = len({r.id for r in report.results})
    if n_findings != n_probes:
        p += [S.MULTI_LANGUAGE_COUNT.format(findings=n_findings, probes=n_probes), ""]

    p += [S.NO_SCORE, ""]

    if report.contributors == 1:
        p += [S.SINGLE_CONTRIBUTOR, ""]

    p += [_contents(report), ""]

    for i, section in enumerate(Section, 1):
        p += ["---", "", f"## **{i}. {section.value}**", "", S.SECTION_INTRO[section], ""]
        p += [_section_body(report, section), ""]
        for note in _notes(report, section):
            p += [note, ""]

    p += ["---", "", "## **What we could not read**", ""]
    bad = report.unreadable()
    if bad:
        rows = [["Probe", "Reason"]]
        for r in bad:
            rows.append([f"`{r.id}`", (r.attributes.get("reason") or "unknown").rstrip(".")])
        p.append(_table(rows))
        p += [
            "",
            f"{len(bad)} of {n_probes} probes. A long list here, or a complete one, would mean the sweep failed rather than the repository being empty.",
            "",
        ]
    else:
        p += ["Every probe returned an answer.", ""]

    p += ["---", "", _where_to_start(report), ""]
    p += ["---", "", S.GLOSSARY, ""]
    p += ["---", "", S.FOOTER, ""]
    return "\n".join(p)
