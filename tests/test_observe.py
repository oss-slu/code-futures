"""Tests for CI observations.

A fragment arrives from a shell script in a workflow, which means it arrives
from the least trustworthy place in the system. Every test here is about a
malformed fragment costing its own probe and nothing else.
"""

from __future__ import annotations

import json

from code_futures import observe
from code_futures.schema import Outcome, ProbeResult


def _write(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return tmp_path


class TestLoad:
    def test_reads_a_fragment(self, tmp_path):
        d = _write(
            tmp_path,
            "a.json",
            {
                "probe": "test.unit",
                "outcome": "enforced",
                "tool": "pytest",
                "detail": "41 passed",
            },
        )
        got = observe.load(d)
        assert len(got) == 1
        assert got[0].id == "test.unit"
        assert got[0].detail == "41 passed"
        assert got[0].source == "ci"

    def test_malformed_json_costs_only_itself(self, tmp_path):
        """A job that wrote nonsense should not take the run down."""
        _write(tmp_path, "bad.json", "{not json")
        _write(tmp_path, "good.json", {"probe": "lint", "outcome": "enforced", "language": "Python"})
        assert len(observe.load(tmp_path)) == 1

    def test_unknown_probe_is_skipped(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": "lint.invented", "outcome": "enforced"})
        assert observe.load(tmp_path) == []

    def test_unknown_outcome_is_skipped(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": "test.unit", "outcome": "excellent"})
        assert observe.load(tmp_path) == []

    def test_impossible_rung_is_demoted_not_raised(self, tmp_path):
        """A README cannot be enforced. A fragment claiming otherwise must
        not raise inside the constructor and kill the summarize job."""
        _write(tmp_path, "a.json", {"probe": "practice.readme", "outcome": "enforced"})
        got = observe.load(tmp_path)
        assert len(got) == 1 and got[0].outcome is Outcome.PRESENT

    def test_language_dropped_on_single_answer_probe(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": "test.unit", "outcome": "enforced", "language": "Python"})
        assert observe.load(tmp_path)[0].language is None

    def test_missing_directory_is_empty_not_an_error(self, tmp_path):
        assert observe.load(tmp_path / "nope") == []

    def test_extra_keys_become_attributes(self, tmp_path):
        _write(
            tmp_path,
            "a.json",
            {
                "probe": "lint",
                "outcome": "enforced",
                "language": "Python",
                "blocking": "false",
                "passed": "true",
            },
        )
        got = observe.load(tmp_path)[0]
        assert got.attributes["blocking"] == "false"
        assert got.attributes["passed"] == "true"

    def test_a_list_of_fragments_in_one_file(self, tmp_path):
        _write(
            tmp_path,
            "a.json",
            [
                {"probe": "lint", "outcome": "enforced", "language": "Rust"},
                {"probe": "format", "outcome": "enforced", "language": "Rust"},
            ],
        )
        assert len(observe.load(tmp_path)) == 2


class TestMerge:
    def test_observation_beats_inference(self):
        """CI watched the thing happen; file inspection guessed from two
        files. Where they disagree, the one that watched wins."""
        inferred = [ProbeResult("test.unit", Outcome.PRESENT, tool="pytest")]
        observed = [ProbeResult("test.unit", Outcome.ENFORCED, tool="pytest", detail="41 passed")]
        got = observe.merge(inferred, observed)
        assert len(got) == 1
        assert got[0].outcome is Outcome.ENFORCED
        assert got[0].detail == "41 passed"

    def test_languages_are_kept_apart(self):
        """A polyglot repository keeps both answers; a merge keyed on probe
        id alone would silently drop one."""
        inferred = [
            ProbeResult("lint", Outcome.PRESENT, language="Python"),
            ProbeResult("lint", Outcome.PRESENT, language="JavaScript"),
        ]
        observed = [ProbeResult("lint", Outcome.ENFORCED, language="Python")]
        got = observe.merge(inferred, observed)
        by_lang = {r.language: r.outcome for r in got}
        assert by_lang == {"Python": Outcome.ENFORCED, "JavaScript": Outcome.PRESENT}

    def test_inference_survives_where_nothing_was_observed(self):
        inferred = [ProbeResult("practice.readme", Outcome.PRESENT)]
        assert observe.merge(inferred, [])[0].outcome is Outcome.PRESENT

    def test_a_failing_check_still_produces_a_finding(self):
        """Jobs are continue-on-error so a failing check still reports. A
        run that produced nothing would make failure look like
        non-adoption."""
        observed = [ProbeResult("lint", Outcome.ENFORCED, language="Python", attributes={"passed": "false"})]
        got = observe.merge([], observed)
        assert got[0].attributes["passed"] == "false"


class TestMalformedFieldTypes:
    """Fragments come from shell scripts, so a field can be any JSON type.

    The module docstring promises a malformed fragment costs its own probe.
    It did not: an unhashable value raised at the membership check and took
    every healthy fragment in the run with it.
    """

    def test_unhashable_probe_does_not_raise(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": [], "outcome": "enforced"})
        _write(tmp_path, "b.json", {"probe": "test.unit", "outcome": "enforced"})
        got = observe.load(tmp_path)
        assert [r.id for r in got] == ["test.unit"]

    def test_non_string_outcome_is_skipped(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": "test.unit", "outcome": 3})
        assert observe.load(tmp_path) == []

    def test_null_evidence_does_not_raise(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": "test.unit", "outcome": "enforced", "evidence": None})
        assert observe.load(tmp_path)[0].evidence == []

    def test_list_valued_language_is_skipped(self, tmp_path):
        _write(tmp_path, "a.json", {"probe": "lint", "outcome": "enforced", "language": ["Python"]})
        assert observe.load(tmp_path) == []

    def test_structured_attribute_is_dropped_not_stringified(self, tmp_path):
        _write(
            tmp_path,
            "a.json",
            {
                "probe": "test.unit",
                "outcome": "enforced",
                "blocking": "false",
                "junk": {"a": 1},
            },
        )
        got = observe.load(tmp_path)[0]
        assert got.attributes == {"blocking": "false"}

    def test_one_bad_fragment_in_a_list_keeps_the_rest(self, tmp_path):
        _write(
            tmp_path,
            "a.json",
            [
                {"probe": [], "outcome": "enforced"},
                {"probe": "test.e2e", "outcome": "absent"},
            ],
        )
        assert [r.id for r in observe.load(tmp_path)] == ["test.e2e"]


class TestSingleAnswerCollision:
    """All four language jobs emit `test.unit`, which is not per-language.

    Fragments are read in filename order, so `java-test.json` sorted before
    `node-test.json` and Node's `absent` placeholder overwrote a real Java
    test run. Filename order was deciding what a repository is.
    """

    def test_a_real_run_is_not_overwritten_by_an_absent_placeholder(self):
        java = ProbeResult("test.unit", Outcome.ENFORCED, tool="gradle build", detail="tests ran")
        node = ProbeResult("test.unit", Outcome.ABSENT, detail="the test script runs no tests")
        for order in ([java, node], [node, java]):
            got = observe.merge([], list(order))
            assert len(got) == 1
            assert got[0].outcome is Outcome.ENFORCED, order

    def test_the_weaker_finding_stays_visible(self):
        """Two facts, not a contradiction: pytest ran, and the Node test
        script is an echo."""
        strong = ProbeResult("test.unit", Outcome.ENFORCED, tool="pytest", detail="41 passed")
        weak = ProbeResult("test.unit", Outcome.ABSENT, detail="the test script runs no tests")
        got = observe.merge([], [strong, weak])[0]
        assert "41 passed" in got.detail
        assert "runs no tests" in got.detail

    def test_unreadable_never_displaces_a_real_answer(self):
        real = ProbeResult("test.unit", Outcome.ABSENT, detail="no runner configured")
        unk = ProbeResult("test.unit", Outcome.UNREADABLE, attributes={"reason": "timed out"})
        for order in ([real, unk], [unk, real]):
            assert observe.merge([], list(order))[0].outcome is Outcome.ABSENT, order

    def test_per_language_probes_are_still_kept_apart(self):
        got = observe.merge(
            [],
            [
                ProbeResult("lint", Outcome.ENFORCED, language="Python", tool="ruff"),
                ProbeResult("lint", Outcome.ABSENT, language="JavaScript"),
            ],
        )
        assert {r.language: r.outcome for r in got} == {
            "Python": Outcome.ENFORCED,
            "JavaScript": Outcome.ABSENT,
        }

    def test_evidence_from_both_jobs_survives(self):
        a = ProbeResult("test.unit", Outcome.ENFORCED, evidence=["ci.yml"])
        b = ProbeResult("test.unit", Outcome.PRESENT, evidence=["package.json"])
        got = observe.merge([], [a, b])[0]
        assert set(got.evidence) == {"ci.yml", "package.json"}


class TestAttributeConventions:
    def test_json_booleans_become_lowercase(self, tmp_path):
        """`str(False)` is `"False"`; the contract and `render.py` both use
        `"false"`. The documented `"blocking": false` form matched nothing,
        so the advisory note was silently omitted."""
        _write(
            tmp_path,
            "a.json",
            {
                "probe": "lint",
                "outcome": "enforced",
                "language": "Python",
                "blocking": False,
                "passed": True,
            },
        )
        got = observe.load(tmp_path)[0]
        assert got.attributes["blocking"] == "false"
        assert got.attributes["passed"] == "true"


class TestCombinedVerdicts:
    def test_a_failing_run_is_not_hidden_by_a_passing_one(self):
        """`passed` is a conjunction. A green summary is the worst thing to
        be wrong about."""
        ok = ProbeResult("test.unit", Outcome.ENFORCED, tool="pytest", attributes={"passed": "true"})
        bad = ProbeResult("test.unit", Outcome.ENFORCED, tool="cargo test", attributes={"passed": "false"})
        for order in ([ok, bad], [bad, ok]):
            assert observe.merge([], list(order))[0].attributes["passed"] == "false", order

    def test_all_passing_stays_passing(self):
        a = ProbeResult("test.unit", Outcome.ENFORCED, attributes={"passed": "true"})
        b = ProbeResult("test.unit", Outcome.ENFORCED, attributes={"passed": "true"})
        assert observe.merge([], [a, b])[0].attributes["passed"] == "true"

    def test_a_non_blocking_run_makes_the_combination_non_blocking(self):
        a = ProbeResult("lint", Outcome.ENFORCED, language="Python", attributes={"blocking": "true"})
        b = ProbeResult("lint", Outcome.ENFORCED, language="Python", attributes={"blocking": "false"})
        assert observe.merge([], [a, b])[0].attributes["blocking"] == "false"


class TestInferredCollisions:
    def test_inference_colliding_with_itself_is_combined_not_dropped(self):
        """`capabilities()` can return an enforced pytest and a
        configured-but-unrun Jest, both keyed `test.unit`. Keeping whichever
        came last downgraded an enforced suite and lost its evidence."""
        strong = ProbeResult("test.unit", Outcome.ENFORCED, tool="pytest", evidence=["ci.yml"])
        weak = ProbeResult("test.unit", Outcome.PRESENT, tool="jest", evidence=["package.json"])
        for order in ([strong, weak], [weak, strong]):
            got = observe.merge(list(order), [])
            assert len(got) == 1
            assert got[0].outcome is Outcome.ENFORCED, order
            assert set(got[0].evidence) == {"ci.yml", "package.json"}

    def test_a_languageless_absence_yields_to_a_language_specific_answer(self):
        """Different keys meant both survived, so one artifact asserted that
        linting is absent and that Python linting is enforced."""
        got = observe.merge(
            [ProbeResult("lint", Outcome.ABSENT)],
            [ProbeResult("lint", Outcome.ENFORCED, language="Python", tool="ruff")],
        )
        assert [(r.language, r.outcome) for r in got] == [("Python", Outcome.ENFORCED)]

    def test_a_languageless_answer_survives_when_nothing_else_speaks(self):
        got = observe.merge([ProbeResult("lint", Outcome.ABSENT)], [])
        assert [(r.language, r.outcome) for r in got] == [(None, Outcome.ABSENT)]
