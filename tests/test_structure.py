"""Tests for structural analysis.

Each case follows an argument from Kent Beck's *Tidy First?* rather than a
metrics definition, and the docstrings say which, so a future reader can tell
a requirement from a preference.
"""

from __future__ import annotations

from code_futures import structure as st
from code_futures.structure import Commit, CoupledPair, Hotspot


def _c(*files):
    return Commit("0" * 40, list(files))


class TestParseLog:
    def test_merges_excluded_by_the_log_command(self):
        """A merge's file list is the union of both sides, which fabricates
        coupling between files no person edited together. `--no-merges`
        handles it; this records why the flag is load-bearing."""
        text = "a" * 40 + "\nsrc/one.py\nsrc/two.py\n"
        got = st.parse_log(text)
        assert len(got) == 1 and got[0].files == ["src/one.py", "src/two.py"]

    def test_lockfiles_are_dropped(self):
        """Lockfiles change constantly and couple to everything."""
        text = "b" * 40 + "\npackage-lock.json\nsrc/app.js\n"
        assert st.parse_log(text)[0].files == ["src/app.js"]

    def test_vendored_paths_are_dropped(self):
        text = "c" * 40 + "\n.venv/lib/site-packages/x.py\nsrc/app.py\n"
        assert st.parse_log(text)[0].files == ["src/app.py"]


class TestChangeCoupling:
    def test_pair_below_support_is_not_reported(self):
        """Two files that changed together twice are a coincidence."""
        assert st.change_coupling([_c("a.py", "b.py")] * 2) == []

    def test_strong_pair_is_reported(self):
        pairs = st.change_coupling([_c("a.py", "b.py")] * 6)
        assert len(pairs) == 1 and pairs[0].degree == 1.0

    def test_bulk_commit_does_not_couple_everything(self):
        """A reformat touching 400 files would otherwise couple every pair
        in it, swamping the real signal."""
        bulk = [_c(*[f"f{i}.py" for i in range(40)])] * 10
        assert st.change_coupling(bulk) == []

    def test_promiscuous_file_is_not_coupled_to_everything(self):
        """Jaccard normalisation is what prevents this: a file edited in
        every commit appears coupled to all of them under a raw count."""
        commits = [_c("hub.py", f"leaf{i}.py") for i in range(10)] * 2
        for p in st.change_coupling(commits):
            assert p.degree < 1.0


class TestCohesion:
    def test_same_directory_pair_is_cohesion_working(self):
        p = CoupledPair("api/models/a.py", "api/models/b.py", 10, 10)
        got = st.cohesion([p])
        assert got.within == [p] and got.across == []

    def test_cross_boundary_pair_is_a_leak(self):
        """Beck: cohesion is coupling put in the right place. A pair
        spanning two modules is a boundary that is not holding."""
        p = CoupledPair("api/models/survey.py", "api/routes/upload.py", 10, 12)
        got = st.cohesion([p])
        assert got.across == [p] and got.leak_ratio == 1.0

    def test_module_is_the_immediate_parent(self):
        """The inner boundary is the one worth testing: two files can share
        a top-level directory and sit across a layer boundary."""
        assert st.module_of("api/models/survey.py") == "api/models"
        assert st.module_of("README.md") == ""


class TestBlastRadius:
    def test_tail_is_what_the_cost_argument_points_at(self):
        """cost(software) ~ cost(change) ~ cost(big change). The median
        alone says nothing about what a change costs you."""
        commits = [_c("a.py")] * 90 + [_c(*[f"f{i}.py" for i in range(20)])] * 10
        br = st.blast_radius(commits)
        assert br.median == 1
        assert br.p90 >= 20
        assert br.largest == 20

    def test_worst_files_names_what_makes_changes_big(self):
        commits = [_c("a.py")] * 20 + [_c("hub.py", "x.py", "y.py", "z.py")] * 8
        assert st.blast_radius(commits).worst_files[0][0] in {"hub.py", "x.py", "y.py", "z.py"}

    def test_empty_history_yields_nothing(self):
        assert st.blast_radius([]).median == 0


class TestTidyFirst:
    def test_churn_decides_soonest_rather_than_worst(self):
        """The question mark is the point. This is not the worst file; it is
        the one where tidying pays back soonest, because you are already in
        it."""
        rarely = Hotspot("rare.py", changes=2, complexity=900)
        often = Hotspot("hot.py", changes=60, complexity=200)
        got = st.tidy_first([rarely, often], st.Cohesion(), set())
        assert got[0].path == "hot.py"

    def test_missing_tests_raise_the_cost_of_each_visit(self):
        a = Hotspot("a.py", changes=30, complexity=100)
        b = Hotspot("b.py", changes=30, complexity=100)
        got = st.tidy_first([a, b], st.Cohesion(), untested={"b.py"})
        assert got[0].path == "b.py"

    def test_cross_boundary_coupling_raises_it_too(self):
        a = Hotspot("a.py", changes=30, complexity=100)
        b = Hotspot("b.py", changes=30, complexity=100)
        coh = st.Cohesion(across=[CoupledPair("b.py", "other/x.py", 9, 10)])
        got = st.tidy_first([a, b], coh, set())
        assert got[0].path == "b.py"

    def test_reasons_are_carried_so_the_report_can_say_why(self):
        h = Hotspot("a.py", changes=30, complexity=100)
        got = st.tidy_first([h], st.Cohesion(), untested={"a.py"})[0]
        assert "changed 30 times" in got.reasons
        assert "no test file" in got.reasons

    def test_single_crossing_reads_as_singular(self):
        h = Hotspot("a.py", changes=5, complexity=10)
        coh = st.Cohesion(across=[CoupledPair("a.py", "z/x.py", 9, 10)])
        got = st.tidy_first([h], coh, set())[0]
        assert "1 coupling across a module boundary" in got.reasons


class TestComplexity:
    def test_nesting_costs_more_than_length(self):
        flat = "a = 1\n" * 10
        nested = "".join("    " * 3 + "a = 1\n" for _ in range(10))
        assert st.complexity(nested) > st.complexity(flat)

    def test_blank_lines_and_comments_are_skipped(self):
        assert st.complexity("\n\n# comment\n// comment\n") == 0
