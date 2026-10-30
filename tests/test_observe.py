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
