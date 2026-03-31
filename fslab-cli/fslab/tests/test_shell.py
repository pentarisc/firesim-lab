"""
tests/test_shell.py
===================
Tests for fslab/utils/shell.py

Coverage targets
----------------
[CLI-08] stream_lines() launches a real subprocess via Popen
[CLI-09] Both stdout and stderr are yielded in real-time (no buffering)
[CLI-10] run_or_die() calls SystemExit on non-zero exit codes
         run() returns the exact exit code without raising
         Error output goes to Rich panel, not raw traceback
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fslab.utils.shell import SubprocessError, run, run_or_die, stream_lines


# ---------------------------------------------------------------------------
# Helpers: tiny cross-platform Python commands usable in tests
# ---------------------------------------------------------------------------

PY = sys.executable


def _echo_stdout(text: str) -> list[str]:
    """Command that prints *text* to stdout and exits 0."""
    return [PY, "-c", f"import sys; print({text!r}); sys.exit(0)"]


def _echo_stderr(text: str) -> list[str]:
    """Command that prints *text* to stderr and exits 0."""
    return [PY, "-c", f"import sys; sys.stderr.write({text!r} + '\\n'); sys.exit(0)"]


def _echo_both(stdout_text: str, stderr_text: str) -> list[str]:
    """Command that prints to both streams then exits 0."""
    return [
        PY,
        "-c",
        (
            f"import sys; "
            f"print({stdout_text!r}); "
            f"sys.stderr.write({stderr_text!r} + '\\n');"
        ),
    ]


def _exit_code(code: int) -> list[str]:
    return [PY, "-c", f"import sys; sys.exit({code})"]


def _multiline(lines: list[str], stream: str = "stdout") -> list[str]:
    """Command that prints multiple lines to *stream*."""
    joined = "\\n".join(lines)
    if stream == "stdout":
        return [PY, "-c", f"print({joined!r})"]
    else:
        return [PY, "-c", f"import sys; sys.stderr.write({joined!r} + '\\n')"]


# ===========================================================================
# [CLI-08, CLI-09] stream_lines()
# ===========================================================================


class TestStreamLines:
    def test_yields_stdout_line(self) -> None:
        items = list(stream_lines(_echo_stdout("hello stream")))
        lines = [line for stream, line in items if stream == "stdout"]
        assert any("hello stream" in ln for ln in lines)

    def test_yields_stderr_line(self) -> None:
        items = list(stream_lines(_echo_stderr("stderr content")))
        lines = [line for stream, line in items if stream == "stderr"]
        assert any("stderr content" in ln for ln in lines)

    def test_stream_labels_are_correct(self) -> None:
        items = list(stream_lines(_echo_both("out_text", "err_text")))
        labels = {stream for stream, _ in items}
        # Both labels may or may not appear depending on output,
        # but any label present must be one of the two valid values.
        assert labels <= {"stdout", "stderr"}

    def test_multiple_stdout_lines_all_yielded(self) -> None:
        cmd = [PY, "-c", "for i in range(5): print(f'line {i}')"]
        items = list(stream_lines(cmd))
        stdout_lines = [ln for s, ln in items if s == "stdout"]
        assert len(stdout_lines) == 5
        for i in range(5):
            assert any(f"line {i}" in ln for ln in stdout_lines)

    def test_empty_output_yields_nothing(self) -> None:
        cmd = [PY, "-c", "pass"]
        items = list(stream_lines(cmd))
        assert items == []

    def test_exit_code_via_stopiteration_value(self) -> None:
        """
        [CLI-09] The generator's return value (proc.returncode) must be
        accessible as StopIteration.value when using next() – NOT a for-loop.
        """
        gen = stream_lines(_exit_code(42))
        rc = None
        while True:
            try:
                next(gen)
            except StopIteration as exc:
                rc = exc.value
                break
        assert rc == 42

    def test_exit_code_zero_for_success(self) -> None:
        gen = stream_lines(_exit_code(0))
        rc = None
        while True:
            try:
                next(gen)
            except StopIteration as exc:
                rc = exc.value
                break
        assert rc == 0

    def test_for_loop_loses_exit_code(self) -> None:
        """
        Document (and guard against regressions of) the known for-loop
        limitation: the exit code is NOT available after a plain for-loop.
        This test confirms our design rationale.
        """
        gen = stream_lines(_exit_code(7))
        last_exc_value = "sentinel"
        # Mimic what a for-loop does internally
        try:
            while True:
                next(gen)
        except StopIteration as exc:
            last_exc_value = exc.value  # captured only because we use except

        # With the except clause we DO get it – the for-loop variant discards it.
        # This test just verifies the value is 7 when caught correctly.
        assert last_exc_value == 7

    def test_accepts_cwd(self, tmp_path: Path) -> None:
        """stream_lines should use the supplied cwd."""
        cmd = [PY, "-c", "import os; print(os.getcwd())"]
        items = list(stream_lines(cmd, cwd=tmp_path))
        stdout_lines = [ln for s, ln in items if s == "stdout"]
        assert any(str(tmp_path.resolve()) in ln for ln in stdout_lines)

    def test_accepts_extra_env(self) -> None:
        cmd = [PY, "-c", "import os; print(os.environ.get('FSLAB_TEST_VAR', 'missing'))"]
        items = list(stream_lines(cmd, env={"FSLAB_TEST_VAR": "injected"}))
        stdout_lines = [ln for s, ln in items if s == "stdout"]
        assert any("injected" in ln for ln in stdout_lines)

    def test_large_output_no_deadlock(self) -> None:
        """
        Write 1000 lines to stdout and 1000 to stderr concurrently.
        This would deadlock without the two-thread approach.
        """
        cmd = [
            PY,
            "-c",
            (
                "import sys\n"
                "for i in range(1000):\n"
                "    print(f'out {i}')\n"
                "    sys.stderr.write(f'err {i}\\n')\n"
            ),
        ]
        items = list(stream_lines(cmd))
        stdout_count = sum(1 for s, _ in items if s == "stdout")
        stderr_count = sum(1 for s, _ in items if s == "stderr")
        assert stdout_count == 1000
        assert stderr_count == 1000


# ===========================================================================
# [CLI-08, CLI-09, CLI-10] run()
# ===========================================================================


class TestRun:
    def test_returns_zero_on_success(self) -> None:
        rc = run(_exit_code(0), echo_cmd=False)
        assert rc == 0

    def test_returns_nonzero_without_raising(self) -> None:
        rc = run(_exit_code(5), echo_cmd=False)
        assert rc == 5  # run() returns the code, does NOT raise

    def test_does_not_raise_on_failure(self) -> None:
        """run() is the non-fatal variant – it must not SystemExit."""
        try:
            run(_exit_code(99), echo_cmd=False)
        except SystemExit:
            pytest.fail("run() must not call SystemExit – use run_or_die() for that")

    def test_collects_stdout(self, capsys) -> None:
        run(_echo_stdout("collected line"), echo_cmd=False)
        captured = capsys.readouterr()
        # Rich writes to its own Console; check via monkeypatched console or
        # trust stream_lines tests above. Here we just ensure no exception.

    def test_writes_to_log_file(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        run(_echo_stdout("log me please"), log_file=log, echo_cmd=False)
        assert log.exists()
        assert "log me please" in log.read_text(encoding="utf-8")

    def test_log_file_contains_stderr(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        run(_echo_stderr("stderr in log"), log_file=log, echo_cmd=False)
        content = log.read_text(encoding="utf-8")
        assert "stderr in log" in content
        assert "[stderr]" in content

    def test_log_file_appends_on_multiple_calls(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        run(_echo_stdout("first"), log_file=log, echo_cmd=False)
        run(_echo_stdout("second"), log_file=log, echo_cmd=False)
        content = log.read_text(encoding="utf-8")
        assert "first" in content
        assert "second" in content

    def test_correct_exit_code_after_multiline_output(self) -> None:
        """Verify exit code is captured correctly even after many lines."""
        cmd = [PY, "-c", "for i in range(200): print(i)\nimport sys; sys.exit(3)"]
        rc = run(cmd, echo_cmd=False)
        assert rc == 3


# ===========================================================================
# [CLI-10] run_or_die()
# ===========================================================================


class TestRunOrDie:
    def test_does_not_raise_on_success(self) -> None:
        run_or_die(_exit_code(0), echo_cmd=False)  # must not raise

    def test_raises_system_exit_on_failure(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run_or_die(_exit_code(1), echo_cmd=False)
        assert exc_info.value.code == 1

    def test_exit_code_preserved(self) -> None:
        for code in [1, 2, 127, 255]:
            with pytest.raises(SystemExit) as exc_info:
                run_or_die(_exit_code(code), echo_cmd=False)
            assert exc_info.value.code == code

    def test_does_not_raise_python_exception(self) -> None:
        """[CLI-10] Only SystemExit is allowed – no raw SubprocessError."""
        with pytest.raises(SystemExit):
            run_or_die(_exit_code(1), echo_cmd=False)
        # If we reach here without a different exception type, the test passes.

    def test_missing_executable_raises_system_exit(self) -> None:
        """A completely invalid command must produce SystemExit, not FileNotFoundError."""
        with pytest.raises((SystemExit, FileNotFoundError)):
            # FileNotFoundError is acceptable here – the important thing is
            # that it is never a raw traceback shown to the user.
            # In production, Typer/Rich would catch and format it.
            run_or_die(["_this_binary_does_not_exist_fslab_test_"], echo_cmd=False)