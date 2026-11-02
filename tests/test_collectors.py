"""Tests for the collectors that parse somebody else's JSON.

These are boundary functions, and their failure mode is silence: a parser
that mishandles a missing field returns a plausible answer about the wrong
thing, and nothing crashes. The rule under test throughout is the one the
schema exists to enforce -- **a measurement we could not read is
`UNREADABLE`, never `ABSENT`**. Reporting "no duplication" because Sonar
declined to answer would be our failure printed as a team's clean result.
"""

from __future__ import annotations

import pytest

from code_futures import collectors
from code_futures.schema import Outcome

# Which URL each keyword argument stands for. `measures/component?` is
# distinct from `measures/component_tree?` only by that question mark, so the
# fragments are spelled out here rather than inferred from the argument name.
_ENDPOINTS = {"measures": "measures/component?", "tree": "measures/component_tree"}


class FakeSonar:
    """Stands in for `_sonar_get`, answering by endpoint."""

    def __init__(self, **responses: object) -> None:
        self.responses = {_ENDPOINTS[k]: v for k, v in responses.items()}
        self.asked: list[str] = []

    def __call__(self, url: str, cfg: object) -> object:
        self.asked.append(url)
        for fragment, response in self.responses.items():
            if fragment in url:
                return response
        return None


def measures(**metrics: str) -> dict:
    return {"component": {"measures": [{"metric": k, "value": v} for k, v in metrics.items()]}}


def tree(path: str, value: str) -> dict:
    return {"components": [{"path": path, "measures": [{"metric": "cognitive_complexity", "value": value}]}]}


@pytest.fixture
def sonar(monkeypatch):
    def install(fake):
        monkeypatch.setattr(collectors, "_sonar_get", fake)
        return fake

    return install


def results(cfg=None) -> dict[str, collectors.ProbeResult]:
    return {r.id: r for r in collectors._structure_from_sonar("key", cfg)}


# --- Duplication -----------------------------------------------------------


def test_duplication_above_threshold_is_present_with_its_numbers(sonar):
    sonar(FakeSonar(measures=measures(duplicated_lines_density="7.4", duplicated_blocks="12")))
    dup = results()["structure.duplication"]
    assert dup.outcome is Outcome.PRESENT
    assert dup.attributes["density"] == "7.4"
    assert dup.attributes["blocks"] == "12"


def test_duplication_below_sonars_own_gate_is_absent(sonar):
    sonar(FakeSonar(measures=measures(duplicated_lines_density="0.4")))
    assert results()["structure.duplication"].outcome is Outcome.ABSENT


def test_duplication_exactly_at_the_threshold_is_reported(sonar):
    sonar(FakeSonar(measures=measures(duplicated_lines_density="3.0")))
    assert results()["structure.duplication"].outcome is Outcome.PRESENT


def test_missing_duplication_metric_is_unreadable_not_absent(sonar):
    """The distinction the whole schema turns on."""
    sonar(FakeSonar(measures=measures(files="40")))
    dup = results()["structure.duplication"]
    assert dup.outcome is Outcome.UNREADABLE
    assert "did not report" in dup.attributes["reason"]


def test_unparseable_density_is_unreadable(sonar):
    sonar(FakeSonar(measures=measures(duplicated_lines_density="n/a")))
    assert results()["structure.duplication"].outcome is Outcome.UNREADABLE


def test_sonar_unreachable_leaves_both_probes_unreadable(sonar):
    sonar(FakeSonar())
    out = results()
    assert out["structure.duplication"].outcome is Outcome.UNREADABLE
    assert out["structure.complexity"].outcome is Outcome.UNREADABLE


# --- Cognitive complexity --------------------------------------------------


def test_complexity_reports_the_total_and_the_worst_file(sonar):
    sonar(
        FakeSonar(
            measures=measures(cognitive_complexity="318", files="40"),
            tree=tree("src/importer.py", "71"),
        )
    )
    cx = results()["structure.complexity"]
    assert cx.outcome is Outcome.PRESENT
    assert cx.attributes["total"] == "318"
    assert cx.attributes["files"] == "40"
    assert cx.attributes["top"] == "src/importer.py"
    assert cx.attributes["top_value"] == "71"


def test_zero_complexity_is_absent(sonar):
    sonar(FakeSonar(measures=measures(cognitive_complexity="0", files="3")))
    assert results()["structure.complexity"].outcome is Outcome.ABSENT


def test_total_without_a_readable_file_breakdown_is_unreadable(sonar):
    """A total we cannot attribute is not a finding we can act on.

    Reporting `PRESENT` here would render a sentence naming the worst file
    with no file to name.
    """
    sonar(FakeSonar(measures=measures(cognitive_complexity="318", files="40")))
    assert results()["structure.complexity"].outcome is Outcome.UNREADABLE


def test_complexity_in_an_unanalysed_language_is_unreadable(sonar):
    """Sonar computes cognitive complexity for some languages and not others.

    No metric means it did not measure, which is not the same as measuring
    nothing.
    """
    sonar(FakeSonar(measures=measures(duplicated_lines_density="1.0", files="40")))
    assert results()["structure.complexity"].outcome is Outcome.UNREADABLE


def test_the_file_breakdown_is_not_requested_without_a_total(sonar):
    """One fewer HTTP call per repository, across a whole-organisation sweep."""
    fake = sonar(FakeSonar(measures=measures(files="40")))
    results()
    assert not any("component_tree" in url for url in fake.asked)


# --- `_most_complex_file` --------------------------------------------------


def test_most_complex_file_falls_back_to_the_component_key(sonar):
    sonar(
        FakeSonar(
            tree={
                "components": [
                    {"key": "proj:src/a.py", "measures": [{"metric": "cognitive_complexity", "value": "9"}]}
                ]
            }
        )
    )
    assert collectors._most_complex_file("key", None) == ("proj:src/a.py", 9)


def test_most_complex_file_tolerates_a_component_with_no_measures(sonar):
    sonar(FakeSonar(tree={"components": [{"path": "src/a.py"}]}))
    assert collectors._most_complex_file("key", None) is None


def test_most_complex_file_tolerates_a_float(sonar):
    sonar(FakeSonar(tree=tree("src/a.py", "9.0")))
    assert collectors._most_complex_file("key", None) == ("src/a.py", 9)


def test_measures_ignores_entries_with_no_value(sonar):
    """Sonar omits `value` on metrics it has a period for but no datum."""
    sonar(
        FakeSonar(
            measures={
                "component": {
                    "measures": [
                        {"metric": "files", "value": "40"},
                        {"metric": "coverage", "periods": [{"index": 1}]},
                    ]
                }
            }
        )
    )
    assert collectors._measures("key", None) == {"files": "40"}
