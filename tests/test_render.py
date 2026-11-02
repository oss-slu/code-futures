"""Tests for the renderer.

The last test here is the important one: it asserts that the committed worked
example is still exactly what the renderer produces. That example is what the
guidebook's "Reading Your Report" section walks a reader through, so if the
two drift, the guidebook starts describing a document nobody receives.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import fixture  # noqa: E402

from code_futures import strings as S  # noqa: E402
from code_futures.render import render  # noqa: E402
from code_futures.schema import PROBES, Outcome, ProbeResult, RepositoryReport, Section  # noqa: E402


def _report(results, **kw):
    return RepositoryReport(
        repository="oss-slu/example", collected_at="2026-11-01 14:00 UTC", results=results, **kw
    )


class TestSchemaGuards:
    def test_unknown_probe_id_is_rejected(self):
        with pytest.raises(ValueError, match="unknown probe"):
            ProbeResult("lint.nonexistent", Outcome.ABSENT)

    def test_probe_cannot_exceed_its_top_rung(self):
        """A README cannot be `enforced`; nothing runs it.

        Declaring the ceiling turns a category error into a crash at the
        collector rather than a nonsensical line in somebody's report.
        """
        with pytest.raises(ValueError, match="cannot be enforced"):
            ProbeResult("practice.readme", Outcome.ENFORCED)

    def test_language_rejected_on_single_answer_probe(self):
        with pytest.raises(ValueError, match="not per-language"):
            ProbeResult("test.unit", Outcome.ABSENT, language="Python")

    def test_from_lookup_never_maps_failure_to_absent(self):
        """The rule the whole four-valued type exists to enforce."""
        assert Outcome.from_lookup(True) is Outcome.PRESENT
        assert Outcome.from_lookup(False) is Outcome.ABSENT
        assert Outcome.from_lookup(None) is Outcome.UNREADABLE

    def test_only_absent_counts_against_a_repository(self):
        assert Outcome.ABSENT.counts_against
        assert not Outcome.UNREADABLE.counts_against
        assert not Outcome.PRESENT.counts_against


class TestSections:
    def test_every_section_renders_even_when_empty(self):
        """Comparability depends on this.

        A section omitted because it found nothing reads as a different
        document; an explicit "nothing found" is comparable side by side.
        """
        out = render(_report([ProbeResult("practice.readme", Outcome.ABSENT)]))
        for i, section in enumerate(Section, 1):
            assert f"## **{i}. {section.value}**" in out

    def test_wholly_unreadable_report_says_so_first(self):
        results = [ProbeResult(p.id, Outcome.UNREADABLE, attributes={"reason": "x"}) for p in PROBES[:5]]
        out = render(_report(results))
        assert "This report is empty, and that is our fault" in out
        assert out.index("that is our fault") < out.index("## **1.")

    def test_single_contributor_is_flagged(self):
        out = render(_report([ProbeResult("practice.readme", Outcome.ABSENT)], contributors=1))
        assert "One contributor" in out

    def test_multiple_contributors_not_flagged(self):
        out = render(_report([ProbeResult("practice.readme", Outcome.ABSENT)], contributors=4))
        assert "One contributor" not in out


class TestDetail:
    def test_unreadable_never_reads_as_the_repository_s_fault(self):
        r = ProbeResult("security.sast", Outcome.UNREADABLE, attributes={"reason": "Timed out"})
        out = render(_report([r]))
        assert "Timed out. We established nothing here." in out
        assert "An unreadable probe describes us" in out

    def test_enforced_without_config_does_not_invent_a_path(self):
        """Six repositories run a linter with no config file anywhere."""
        r = ProbeResult(
            "lint",
            Outcome.ENFORCED,
            language="Python",
            tool="pylint",
            attributes={"invocation": ".github/workflows/pylint.yml", "config": ""},
        )
        out = render(_report([r]))
        assert "Configured by command-line flags rather than a config file" in out

    def test_probe_with_its_own_template_is_not_routed_to_noconfig(self):
        """`ci.present` has no config and its own wording.

        A first version sent every config-less enforced probe to the generic
        fallback, which dropped `ci.present` and both Sonar probes to
        "Configured, and invoked by CI."
        """
        r = ProbeResult(
            "ci.present",
            Outcome.ENFORCED,
            attributes={"count": "3", "workflows": "`CI`, `CodeQL`"},
        )
        assert "3 workflows: `CI`, `CodeQL`." in render(_report([r]))

    def test_missing_slot_falls_back_rather_than_crashing(self):
        r = ProbeResult("ci.present", Outcome.ENFORCED, attributes={})
        assert "Configured, and invoked by CI." in render(_report([r]))


class TestNotes:
    def test_unidentifiable_licence_note(self):
        r = ProbeResult("practice.license", Outcome.PRESENT, attributes={"path": "LICENSE", "spdx": None})
        assert "Unidentifiable licence" in render(_report([r]))

    def test_identified_licence_has_no_note(self):
        r = ProbeResult("practice.license", Outcome.PRESENT, attributes={"path": "LICENSE", "spdx": "MIT"})
        assert "Unidentifiable licence" not in render(_report([r]))

    def test_protection_with_zero_reviews_is_called_out(self):
        r = ProbeResult(
            "review.required",
            Outcome.PRESENT,
            attributes={
                "branch": "main",
                "reviews": "0",
                "status_checks": "not required",
                "admins": "exempt",
            },
        )
        out = render(_report([r]))
        assert "Protection that permits unreviewed merges" in out
        assert "Administrators are exempt" in out

    def test_non_blocking_check_is_called_out(self):
        r = ProbeResult(
            "lint",
            Outcome.ENFORCED,
            language="Python",
            tool="pylint",
            attributes={"invocation": "ci.yml", "config": "", "blocking": "false"},
        )
        assert "Runs, but cannot fail" in render(_report([r]))


class TestWhereToStart:
    def test_unreadable_findings_are_never_actionable(self):
        """Asking a team to act on our own collection failure would be the
        worst instance of the mistake the fourth outcome exists to prevent."""
        r = ProbeResult("security.sast", Outcome.UNREADABLE, attributes={"reason": "Timed out"})
        out = render(_report([r]))
        assert S.WHERE_TO_START_NOTHING in out

    def test_unidentifiable_licence_leads(self):
        """It is the only finding with consequences outside the team."""
        results = [
            ProbeResult("practice.license", Outcome.PRESENT, attributes={"path": "LICENSE", "spdx": None}),
            ProbeResult("practice.code_of_conduct", Outcome.ABSENT),
        ]
        out = render(_report(results))
        assert "Our suggestion here is **the licence**" in out

    def test_clean_repository_says_so(self):
        r = ProbeResult("practice.license", Outcome.PRESENT, attributes={"path": "LICENSE", "spdx": "MIT"})
        assert S.WHERE_TO_START_NOTHING in render(_report([r]))


class TestDeterminism:
    def test_same_report_renders_identically(self):
        """Templates rather than a model, so a report can be diffed against
        its own past. That is the continuous-improvement story."""
        assert render(fixture.build()) == render(fixture.build())


class TestWorkedExample:
    def test_committed_example_matches_the_renderer(self):
        """The guidebook's worked example must be real renderer output.

        Section 3 of the guidebook walks a reader through this exact
        document. If the two drift, the guidebook describes a report nobody
        receives. Regenerate with `python examples/fixture.py`.
        """
        committed = (Path(__file__).resolve().parents[1] / "examples" / "tidewater-atlas.md").read_text()
        assert committed == render(fixture.build(), fabricated=True)

    def test_example_exercises_every_outcome(self):
        counts = fixture.build().counts()
        for outcome in Outcome:
            assert counts[outcome] > 0, f"example never produces {outcome.value}"

    def test_example_names_no_real_repository(self):
        assert fixture.REPOSITORY == "oss-slu/tidewater-atlas"


class TestStructure:
    """The section about the code rather than the process around it."""

    def test_coupling_names_the_files(self):
        """A coupled pair a team cannot see is a statistic. One they can see
        is a conversation about where a boundary belongs."""
        r = ProbeResult(
            "structure.change_coupling",
            Outcome.PRESENT,
            attributes={
                "count": "2",
                "threshold": "60",
                "a": "models.py",
                "b": "routes.py",
                "degree": "83",
                "together": "24",
            },
            evidence=["models.py <-> routes.py (83%)"],
        )
        out = render(_report([r]))
        assert "models.py <-> routes.py (83%)" in out
        assert "Reading change coupling" in out

    def test_thin_history_is_flagged(self):
        """A young repository reports that recently-written files were
        recently written, and the report should say so."""
        r = ProbeResult(
            "structure.hotspots",
            Outcome.PRESENT,
            attributes={
                "count": "2",
                "top": "a.py",
                "top_changes": "4",
                "top_complexity": "90",
                "thin": "true",
                "commits": "18",
            },
        )
        out = render(_report([r]))
        assert "This repository is young" in out
        assert "18 commits" in out

    def test_long_history_is_not_flagged(self):
        r = ProbeResult(
            "structure.hotspots",
            Outcome.PRESENT,
            attributes={
                "count": "2",
                "top": "a.py",
                "top_changes": "60",
                "top_complexity": "400",
                "thin": "false",
                "commits": "300",
            },
        )
        assert "This repository is young" not in render(_report([r]))

    def test_structure_probes_cannot_be_enforced(self):
        """Nothing in CI runs change coupling."""
        with pytest.raises(ValueError, match="cannot be enforced"):
            ProbeResult("structure.change_coupling", Outcome.ENFORCED)


class TestRemediationTriggers:
    """`when` replaced a hard-coded whitelist that dropped findings twice."""

    def test_every_remediation_declares_reachable_outcomes(self):
        from code_futures.schema import PROBES_BY_ID

        for pid, spec in S.REMEDIATION.items():
            assert pid in PROBES_BY_ID, f"{pid} is not a probe"
            top = PROBES_BY_ID[pid].top_rung
            for value in spec.get("when", ("absent",)):
                outcome = Outcome(value)
                assert outcome is not Outcome.UNREADABLE, (
                    f"{pid}: acting on an unreadable finding means acting on our own failure"
                )
                if outcome is Outcome.ENFORCED:
                    assert top is Outcome.ENFORCED, f"{pid} can never be enforced"

    def test_known_vulnerabilities_are_actionable_while_present(self):
        r = ProbeResult("deps.known_vulnerabilities", Outcome.PRESENT, attributes={"count": "3"})
        assert "dependencies with known advisories" in render(_report([r]))

    def test_untested_modules_are_actionable_while_present(self):
        """As the only finding it becomes the lead, which uses `label`.
        Ranked below something else it becomes a numbered `step`."""
        r = ProbeResult("structure.untested_modules", Outcome.PRESENT, attributes={"count": "3"})
        assert "a test for the module that changes most often" in render(_report([r]))

    def test_ranked_below_another_finding_it_becomes_a_step(self):
        results = [
            ProbeResult("structure.untested_modules", Outcome.PRESENT, attributes={"count": "3"}),
            ProbeResult("deps.known_vulnerabilities", Outcome.PRESENT, attributes={"count": "2"}),
        ]
        out = render(_report(results))
        assert "Our suggestion here is **the dependencies with known advisories**" in out
        assert "most frequently changed untested file" in out

    def test_identified_licence_is_not_actionable(self):
        r = ProbeResult("practice.license", Outcome.PRESENT, attributes={"path": "LICENSE", "spdx": "MIT"})
        assert S.WHERE_TO_START_NOTHING in render(_report([r]))


class TestAnchors:
    def test_each_space_becomes_a_hyphen(self):
        """github-slugger does not collapse whitespace runs, so an em-dash
        heading yields a double hyphen. Collapsing them produces links that
        silently scroll nowhere."""
        from code_futures.render import _anchor

        assert _anchor("## **1. Structure**") == "1-structure"
        assert _anchor("Section 1 — Structure") == "section-1--structure"
        assert _anchor("Where to start") == "where-to-start"

    def test_every_generated_link_resolves(self):
        """A table of contents with a broken link is worse than none."""
        import re

        out = render(fixture.build())
        seen, real = {}, set()
        for line in out.split("\n"):
            m = re.match(r"^#{2,4} (.+)$", line)
            if m:
                from code_futures.render import _anchor

                a = _anchor(m.group(1))
                n = seen.get(a, 0)
                seen[a] = n + 1
                real.add(a if n == 0 else f"{a}-{n}")
        links = re.findall(r"\]\(#([^)]+)\)", out)
        assert links, "expected internal links"
        assert [x for x in links if x in real] == links


class TestSummary:
    def test_it_names_the_same_item_the_recommendations_lead_with(self):
        """Two independent rankings would eventually disagree, and a reader
        would have no way to tell which to trust."""
        out = render(fixture.build())
        summary = out.split("## **How to read this**")[0]
        lead = out.split("Our suggestion here is **")[1].split("**")[0]
        assert lead.lower() in summary.lower()

    def test_counts_appear_once_rather_than_twice(self):
        """The counts used to have a section of their own carrying the same
        table, which pushed the first real finding down the page."""
        out = render(fixture.build())
        assert "## **Outcomes**" not in out
        assert out.count("| enforced | present | absent | unreadable |") == 1

    def test_unreadable_is_attributed_to_us(self):
        out = render(fixture.build())
        assert "our gap rather than this repository's" in out

    def test_clean_report_says_nothing_needs_action(self):
        r = ProbeResult("practice.license", Outcome.PRESENT, attributes={"path": "LICENSE", "spdx": "MIT"})
        assert S.SUMMARY_NOTHING_TO_DO in render(_report([r]))

    def test_structure_clauses_are_omitted_not_invented(self):
        """A summary must not report an absence as a result."""
        r = ProbeResult("structure.hotspots", Outcome.ABSENT)
        out = render(_report([r]))
        assert S.SUMMARY_NO_STRUCTURE in out


class TestNoLocalDefensiveness:
    def test_no_section_intro_claims_it_alone_carries_no_grade(self):
        """Stating a global property locally implies it does not hold
        elsewhere: a reviewer read "this section carries no grade" as meaning
        the other six do."""
        for section, intro in S.SECTION_INTRO.items():
            lowered = intro.lower()
            assert "no grade" not in lowered, section
            assert "this section carries" not in lowered, section

    def test_the_claim_is_made_once_globally(self):
        assert "score" in S.NO_SCORE.lower()
        assert S.NO_SCORE in render(fixture.build())


class TestGlossary:
    def test_it_is_short(self):
        """The report carries only what you cannot read it without. The
        expanded vocabulary belongs in the guidebook."""
        assert len(S.GLOSSARY.split()) < 200

    def test_it_distinguishes_the_two_hotspots(self):
        """`hotspot` means two different things in one report: a structural
        one and a Sonar security one. Leaving that implicit is a trap."""
        assert "Security hotspot" in S.GLOSSARY
        assert "Unrelated to the above" in S.GLOSSARY

    def test_it_defers_the_outcome_vocabulary_rather_than_repeating_it(self):
        assert "How to read this" in S.GLOSSARY
