"""Tests for capability detection.

Every case here is drawn from a real repository in `oss-slu`, found by a
survey of all 96. They are regression tests against specific ways this
detector was going to be wrong, and the docstrings say which repository shape
each one came from so a future reader can tell a real requirement from a
guess.
"""

from __future__ import annotations

from code_futures import detect


class TestVendored:
    def test_committed_virtualenv_is_excluded(self):
        """One repository commits its `.venv`.

        12,600 of its 12,639 files sit under `site-packages`, including 612
        named `test_*.py`. Without this filter it is the best-tested project
        in the organisation, and it has no first-party tests at all.
        """
        assert detect.is_vendored(".venv/Lib/site-packages/numpy/conftest.py")
        assert detect.is_vendored("montis/.venv/lib/python3.11/site-packages/scipy/conftest.py")

    def test_vendored_upstream_is_a_known_gap(self):
        """Vendored upstream source is NOT detected, and this records that.

        One repository vendors an upstream ROS package at
        `ros2_ws/src/rosbridge_suite/`, carrying the most sophisticated lint
        configuration in the organisation. None of it is the team's and
        nothing runs it, so crediting the team with it is wrong.

        No path convention distinguishes it: `ros2_ws`, `src` and the package
        name are all ordinary. Detecting this properly needs either a
        per-repository ignore file or a heuristic over nested package
        metadata, and guessing would risk excluding real source — a worse
        error, because it would under-report a team's actual work.

        The test asserts the current behaviour so that a future fix shows up
        as a deliberate change rather than an accident.
        """
        assert not detect.is_vendored("ros2_ws/src/rosbridge_suite/pyproject.toml")

    def test_generated_manifests_are_excluded(self):
        """A Prisma-generated `package.json` is nobody's configuration."""
        assert detect.is_vendored("server/src/generated/prisma/package.json")

    def test_node_modules_excluded(self):
        assert detect.is_vendored("frontend/node_modules/eslint/package.json")

    def test_real_source_is_not_vendored(self):
        for path in (
            "pyproject.toml",
            "backend/pyproject.toml",
            "Frontend/eslint.config.js",
            "client_app/.eslintrc.json",
            "src/main.rs",
        ):
            assert not detect.is_vendored(path), path

    def test_segment_match_not_substring(self):
        """A directory named `vendor_api` is not a vendored tree."""
        assert not detect.is_vendored("vendor_api/config.toml")
        assert not detect.is_vendored("src/environment/settings.py")


class TestRunLines:
    def test_bare_run(self):
        assert detect.run_lines("      - run: pytest\n") == ["pytest"]

    def test_block_scalar(self):
        wf = """
jobs:
  x:
    steps:
      - name: lint
        run: |
          cd frontend
          npm run lint
      - name: next
        uses: actions/checkout@v4
"""
        assert detect.run_lines(wf) == ["cd frontend", "npm run lint"]

    def test_commented_out_workflow_yields_nothing(self):
        """One repository's `pylint.yml` is twenty lines of `#`.

        It lists in the Actions UI and runs nothing. Reading it as a lint
        workflow would credit a repository with a check it does not have.
        """
        wf = "\n".join("# " + l for l in ["name: Pylint", "jobs:", "  run: pylint ."])
        assert detect.run_lines(wf) == []

    def test_shell_continuation_is_joined(self):
        """A multi-line pytest invocation must read as one command, or the
        `--cov` flags land on a line of their own and lose their context."""
        wf = """
      - run: |
          pytest --ignore=tests/slow.py \\
            --cov=app \\
            --cov-fail-under=80
"""
        got = detect.run_lines(wf)
        assert len(got) == 1
        assert "--cov-fail-under=80" in got[0]


class TestResolveScript:
    def test_echo_test_script_is_hollow(self):
        """`"test": "echo \\"No tests specified\\" && exit 0"` is real, and
        recording it as a test run manufactures a suite out of an `echo`."""
        scripts = {"test": 'echo "No tests specified" && exit 0'}
        _, real = detect.resolve_script(scripts, "npm test")
        assert real is False

    def test_exit_one_test_script_is_hollow(self):
        scripts = {"test": 'echo "Error: no test specified" && exit 1'}
        _, real = detect.resolve_script(scripts, "npm test")
        assert real is False

    def test_pass_with_no_tests_is_hollow(self):
        scripts = {"test": "react-scripts test"}
        _, real = detect.resolve_script(scripts, "npm test -- --passWithNoTests")
        assert real is False

    def test_test_script_that_runs_the_linter(self):
        """One repository maps `"test": "next lint"`. Running "the tests"
        there runs the linter, and the report should not claim a test ran."""
        scripts = {"test": "next lint"}
        resolved, real = detect.resolve_script(scripts, "npm test")
        assert real is True
        assert resolved == "next lint"
        findings = detect.detect_runs({".github/workflows/ci.yml": "      - run: npm test\n"}, scripts)
        kinds = {f.kind for f in findings}
        assert "lint" in kinds
        assert "test.unit" not in kinds

    def test_missing_test_script_is_hollow(self):
        _, real = detect.resolve_script({}, "npm test")
        assert real is False

    def test_real_script_resolves_to_its_command(self):
        scripts = {"lint": "eslint . --max-warnings 0"}
        resolved, real = detect.resolve_script(scripts, "npm run lint")
        assert real is True
        assert resolved.startswith("eslint")

    def test_install_is_not_a_script_lookup(self):
        resolved, real = detect.resolve_script({}, "npm ci")
        assert real is True
        assert resolved == "npm ci"


class TestDetectRuns:
    def test_tool_enforced_without_any_config_file(self):
        """Six repositories configure a linter entirely in a workflow.

        This is the case that refuted the original two-layer design, where
        `enforced` required `present` first.
        """
        wf = {".github/workflows/pylint.yml": "      - run: pylint $(git ls-files '*.py')\n"}
        got = detect.detect_runs(wf)
        assert [f.tool for f in got] == ["pylint"]
        assert got[0].enforced
        assert got[0].config is None

    def test_non_blocking_lint_is_recorded(self):
        for cmd in ("pylint *.py || true", "flake8 . --exit-zero", "pylint --fail-under=8.5 x.py"):
            got = detect.detect_runs({".github/workflows/ci.yml": f"      - run: {cmd}\n"})
            assert got, cmd
            assert got[0].blocking is False, cmd

    def test_blocking_lint_is_the_default(self):
        got = detect.detect_runs({".github/workflows/ci.yml": "      - run: ruff check .\n"})
        assert got[0].blocking is True

    def test_prettier_write_cannot_fail(self):
        """Three repositories share a format workflow running `prettier
        --write` on a throwaway runner. Only one commits the result; for the
        other two the job always passes and gates nothing."""
        scripts = {"fmt": "prettier --write ."}
        got = detect.detect_runs({".github/workflows/format.yml": "      - run: npm run fmt\n"}, scripts)
        fmt = [f for f in got if f.kind == "format"]
        assert fmt and fmt[0].blocking is False

    def test_uv_run_pytest_is_found(self):
        """`uv run pytest` matches neither `^pytest` nor `python -m pytest`."""
        got = detect.detect_runs({".github/workflows/ci.yml": "      - run: uv run pytest --cov\n"})
        assert "test.unit" in {f.kind for f in got}

    def test_cd_prefixed_pytest_is_found(self):
        wf = {".github/workflows/ci.yml": "      - run: cd server && PYTHONPATH=$(pwd) pytest\n"}
        assert "test.unit" in {f.kind for f in detect.detect_runs(wf)}

    def test_gradle_build_implies_tests(self):
        """`./gradlew build` triggers `check`, which runs the test task.

        The word "test" appears nowhere in the workflow, and the repository's
        27 JUnit classes do execute.
        """
        wf = {".github/workflows/java.yml": "      - run: ./gradlew build\n"}
        assert "test.unit" in {f.kind for f in detect.detect_runs(wf)}

    def test_gradle_assemble_does_not_imply_tests(self):
        """`assemble` and `check` are siblings under `build`, so
        `assembleRelease` runs no tests. Treating every Gradle aggregate as a
        test run produces a false positive on the one Android repo here."""
        wf = {".github/workflows/release.yml": "      - run: ./gradlew assembleRelease\n"}
        assert "test.unit" not in {f.kind for f in detect.detect_runs(wf)}

    def test_cargo_clippy_and_fmt(self):
        wf = {
            ".github/workflows/ci.yml": (
                "      - run: cargo fmt --all -- --check\n"
                "      - run: cargo clippy --all-targets --all-features -- -D warnings\n"
                "      - run: cargo test --verbose\n"
            )
        }
        kinds = {(f.kind, f.tool) for f in detect.detect_runs(wf)}
        assert ("lint", "clippy") in kinds
        assert ("format", "rustfmt") in kinds
        assert ("test.unit", "cargo test") in kinds

    def test_react_scripts_test_is_a_test_runner(self):
        """CRA runs Jest with no jest config and no jest dependency."""
        scripts = {"test": "react-scripts test --watchAll=false"}
        got = detect.detect_runs({".github/workflows/ci.yml": "      - run: npm test\n"}, scripts)
        assert "test.unit" in {f.kind for f in got}

    def test_vendored_workflow_path_ignored(self):
        wf = {"node_modules/pkg/.github/workflows/ci.yml": "      - run: pytest\n"}
        assert detect.detect_runs(wf) == []


class TestPackageScripts:
    def test_malformed_package_json_fails_soft(self):
        """One `package.json` in the organisation is missing a comma.

        Raising there would lose the whole repository over a typo.
        """
        bad = '{"scripts": {"test": "jest" "lint": "eslint ."}}'
        assert detect.package_scripts({"package.json": bad}) == {}

    def test_vendored_package_json_ignored(self):
        good = '{"scripts": {"lint": "eslint ."}}'
        assert detect.package_scripts({"node_modules/x/package.json": good}) == {}

    def test_subdirectory_manifests_are_merged(self):
        """Only 13 of 39 Node repositories keep their only manifest at the
        root. `frontend/`, `Frontend/`, `client_app/`, `client-app/`, `api/`
        and `app/` all occur."""
        paths = {
            "Frontend/package.json": '{"scripts": {"lint": "eslint ."}}',
            "api/package.json": '{"scripts": {"test": "jest"}}',
        }
        got = detect.package_scripts(paths)
        assert got == {"lint": "eslint .", "test": "jest"}


class TestMerge:
    def test_config_and_run_combine_into_one_finding(self):
        cfg = detect.detect_config({"pyproject.toml": "[tool.ruff]\n"})
        runs = detect.detect_runs({".github/workflows/ci.yml": "      - run: ruff check .\n"})
        merged = detect.merge(cfg, runs)
        lint = [f for f in merged if f.kind == "lint"]
        assert len(lint) == 1
        assert lint[0].config == "pyproject.toml"
        assert lint[0].invocation == ".github/workflows/ci.yml"
        assert lint[0].enforced

    def test_config_alone_is_present_not_enforced(self):
        merged = detect.merge(detect.detect_config({".eslintrc.json": "{}"}), [])
        assert merged[0].enforced is False
