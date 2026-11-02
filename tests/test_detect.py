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
        wf = "\n".join("# " + x for x in ["name: Pylint", "jobs:", "  run: pylint ."])
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


class TestCppDetection:
    """Drawn from the two C/C++ projects in this portfolio, which share
    almost no toolchain. One is Unreal Engine -- `.uproject`, `.uplugin`,
    `*.Build.cs`, no CMake anywhere -- and the other is meson with a CMake
    test subdirectory. A detector assuming CMake would have found neither.
    """

    def test_clang_format_config_is_a_format_signal(self):
        got = detect.detect_config({".clang-format": "BasedOnStyle: LLVM\n"})
        assert [(f.kind, f.language, f.tool) for f in got] == [("format", "C/C++", "clang-format")]

    def test_clang_tidy_config_is_a_lint_signal(self):
        got = detect.detect_config({"Plugins/MCPUnreal/.clang-tidy": "Checks: '*'\n"})
        assert [(f.kind, f.tool) for f in got] == [("lint", "clang-tidy")]

    def test_meson_without_tests_is_not_a_test_signal(self):
        """A build file declares a build. The CMake signals already required
        test evidence, and this one was inconsistent with them."""
        assert detect.detect_config({"xplane_plugin/meson.build": "project('x', 'cpp')\n"}) == []

    def test_meson_declaring_a_test_is(self):
        src = "project('x', 'cpp')\ntest('suite', executable('t', 't.cpp'))\n"
        got = detect.detect_config({"xplane_plugin/meson.build": src})
        assert [(f.kind, f.tool) for f in got] == [("test.unit", "meson")]

    def test_cmake_without_tests_is_not_a_test_signal(self):
        """A CMakeLists that declares no tests declares no test capability."""
        assert detect.detect_config({"CMakeLists.txt": "project(x)\nadd_executable(a a.c)\n"}) == []

    def test_cmake_with_add_test_is(self):
        got = detect.detect_config({"CMakeLists.txt": "enable_testing()\nadd_test(NAME t COMMAND t)\n"})
        assert got and got[0].kind == "test.unit"

    def test_clang_format_run_line(self):
        wf = {".github/workflows/cpp-format.yml": "      - run: clang-format --dry-run -Werror\n"}
        got = detect.detect_runs(wf)
        assert [(f.kind, f.language, f.tool) for f in got] == [("format", "C/C++", "clang-format")]

    def test_meson_test_counts_but_meson_compile_does_not(self):
        """A build is not a test run, the lesson `assembleRelease` taught."""
        build = detect.detect_runs({"w.yml": "      - run: meson setup build && meson compile -C build\n"})
        assert "test.unit" not in {f.kind for f in build}
        tested = detect.detect_runs({"w.yml": "      - run: meson test -C build\n"})
        assert "test.unit" in {f.kind for f in tested}

    def test_ctest_counts(self):
        assert "test.unit" in {
            f.kind for f in detect.detect_runs({"w.yml": "      - run: ctest --output-on-failure\n"})
        }


class TestSuperLinter:
    """super-linter declares what it checks in an `env:` block, so no `run:`
    line reveals it. In one repository here it is the only C++ format check
    that exists."""

    WF = """
jobs:
  lint:
    steps:
      - uses: super-linter/super-linter@v8.3.1
        env:
          VALIDATE_ALL_CODEBASE: true
          VALIDATE_CLANG_FORMAT: true
          VALIDATE_GITHUB_ACTIONS: true
          VALIDATE_YAML: true
"""

    def test_enabled_validator_is_detected(self):
        got = detect.detect_runs({".github/workflows/super-linter.yml": self.WF})
        assert ("format", "C/C++", "clang-format") in {(f.kind, f.language, f.tool) for f in got}

    def test_validators_not_enabled_are_not_credited(self):
        """The action defaults most validators off. One repository enables
        only clang-format, Actions and YAML, and crediting it with Python
        linting would be exactly wrong."""
        got = detect.detect_runs({".github/workflows/super-linter.yml": self.WF})
        assert "Python" not in {f.language for f in got}

    def test_a_commented_out_validator_is_not_credited(self):
        wf = self.WF.replace("VALIDATE_CLANG_FORMAT: true", "# VALIDATE_CLANG_FORMAT: true")
        assert detect.detect_runs({".github/workflows/super-linter.yml": wf}) == []


class TestCalledOnlyWorkflows:
    """A `workflow_call`-only workflow describes what it does for callers,
    not what its host repository does.

    Found by running Code Futures against Code Futures. Its own reusable
    workflow contains `clang-tidy`, `eslint` and `cargo clippy` as real
    `run:` lines -- conditionally, for whichever stack a caller has -- and
    the report credited a Python-only repository with enforced linting in
    three languages it does not contain.
    """

    REUSABLE = """
on:
  workflow_call:
jobs:
  rust:
    steps:
      - run: cargo clippy --all-targets -- -D warnings
  cpp:
    steps:
      - run: clang-tidy src/a.cpp
"""
    OWN = """
on:
  pull_request:
  push:
    branches: [main]
jobs:
  lint:
    steps:
      - run: ruff check .
"""

    def test_a_reusable_workflow_is_not_the_host_s_practice(self):
        assert detect.is_called_only(self.REUSABLE)
        assert detect.detect_runs({".github/workflows/reusable.yml": self.REUSABLE}) == []

    def test_an_ordinary_workflow_still_counts(self):
        assert not detect.is_called_only(self.OWN)
        got = detect.detect_runs({".github/workflows/ci.yml": self.OWN})
        assert [(f.language, f.tool) for f in got] == [("Python", "ruff")]

    def test_a_workflow_both_called_and_triggered_still_counts(self):
        """Dual-purpose workflows exist, and their push trigger means they
        really do run here."""
        both = "on:\n  workflow_call:\n  push:\n" + "jobs:\n  x:\n    steps:\n      - run: ruff check .\n"
        assert not detect.is_called_only(both)
        assert detect.detect_runs({"w.yml": both})

    def test_a_commented_out_trigger_does_not_rescue_it(self):
        wf = self.REUSABLE.replace("on:\n  workflow_call:", "on:\n  workflow_call:\n  # push:")
        assert detect.is_called_only(wf)

    def test_the_caller_side_is_unaffected(self):
        """What a team adds is a `uses:` line, which names no tool, so their
        own workflows are read exactly as before."""
        caller = """
on:
  pull_request:
jobs:
  code-futures:
    uses: oss-slu/code-futures/.github/workflows/code-futures.yml@v1.0.0
"""
        assert not detect.is_called_only(caller)
        assert detect.detect_runs({".github/workflows/code-futures.yml": caller}) == []
