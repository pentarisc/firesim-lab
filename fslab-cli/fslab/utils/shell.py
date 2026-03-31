"""
fslab/utils/shell.py
====================
[CLI-08] Robust subprocess wrapper around subprocess.Popen.
[CLI-09] Streams stdout AND stderr to the terminal in real-time so users
         are never staring at a frozen screen during long sbt / java / cmake
         invocations.
[CLI-10] Non-zero exit codes are caught and surfaced as styled Rich errors
         followed by a clean SystemExit – no raw Python tracebacks reach the
         user.

Public API
----------
    run(cmd, *, cwd, env, label, log_file, echo_cmd, stderr_style) -> int
        Execute a command, stream output line-by-line, return the exit code.

    run_or_die(cmd, ...)
        Like run(), but raises SystemExit with a Rich error panel on failure.
        [CLI-10] This is the standard way to run any required build step.

    run_with_spinner(cmd, *, cwd, env, spinner_text, log_file) -> int
        [CLI-15] Run a long command behind a Rich Live spinner.
        All output goes to log_file only – no terminal noise.

    stream_lines(cmd, *, cwd, env) -> Iterator[tuple[str, str]]
        Low-level generator yielding (stream_name, line) pairs.
        Exported for callers that need fine-grained control (e.g. tests).

Design: background-thread merge
--------------------------------
Both stdout and stderr from the child process are read by dedicated daemon
threads that push decoded lines into a shared ``SimpleQueue``.  Each thread
pushes an ``_EOF`` sentinel when its stream reaches EOF.  The main thread
drains the queue until it has seen both sentinels.

This is the only reliable cross-platform approach for merging two OS-level
streams without deadlock:
  * ``select()``/``poll()`` are POSIX-only.
  * ``proc.communicate()`` buffers everything and blocks until EOF.
  * Reading stdout in the main thread while stderr runs in a thread risks the
    classic deadlock where one stdout-read blocks while stderr fills its 4 KB
    OS pipe buffer (or vice-versa).

Design: StopIteration.value and generator return codes
-------------------------------------------------------
``stream_lines`` ends with ``return proc.returncode``.  Python raises
``StopIteration(proc.returncode)`` when the generator is exhausted, making
the exit code available as ``StopIteration.value``.

A ``for`` loop silently DISCARDS ``StopIteration.value``.  We therefore drive
``stream_lines`` with a ``while True / next()`` loop inside ``run()`` so the
``except StopIteration as exc: rc = exc.value`` clause fires correctly.
"""

from __future__ import annotations

import os
import queue as q_module
import subprocess
import threading
from pathlib import Path
from typing import Iterator, Optional

from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from fslab.utils.display import cmd_echo, console, error


# ---------------------------------------------------------------------------
# Custom exception – raised by run_or_die, caught at command boundaries
# ---------------------------------------------------------------------------


class SubprocessError(Exception):
    """Raised when a managed subprocess exits with a non-zero return code."""

    def __init__(self, cmd: list[str], returncode: int) -> None:
        self.cmd = cmd
        self.returncode = returncode
        super().__init__(
            f"Command exited with code {returncode}: {' '.join(cmd)}"
        )


# ---------------------------------------------------------------------------
# Sentinel – marks EOF from a reader thread in the shared queue.
# Using a module-level object() avoids any risk of a real data item matching.
# ---------------------------------------------------------------------------
_EOF = object()


# ---------------------------------------------------------------------------
# [CLI-09] Core streaming primitive
# ---------------------------------------------------------------------------


def stream_lines(
    cmd: list[str],
    *,
    cwd: Optional[Path | str] = None,
    env: Optional[dict[str, str]] = None,
) -> Iterator[tuple[str, str]]:
    """
    [CLI-08, CLI-09] Launch *cmd* via Popen and yield ``(stream, line)`` pairs
    where ``stream`` ∈ ``{"stdout", "stderr"}``.

    Both streams are read as data arrives – there is no buffering delay even
    for tools (like sbt/Vivado) that interleave stdout and stderr heavily.

    Generator return value
    ----------------------
    The generator function ends with ``return proc.returncode``.  When the
    generator is exhausted this becomes ``StopIteration.value``.  Callers that
    need the exit code MUST use ``while True / next()`` – NOT a ``for`` loop.

    Yields
    ------
    (stream_name, line)
        ``stream_name`` is ``"stdout"`` or ``"stderr"``.
        ``line`` is a decoded, newline-stripped string.
    """
    merged: q_module.SimpleQueue = q_module.SimpleQueue()

    def _reader(stream, label: str) -> None:
        """Background daemon thread: read *stream* line-by-line, enqueue."""
        try:
            for raw in stream:
                merged.put((label, raw.rstrip("\r\n")))
        finally:
            merged.put(_EOF)  # Always signal EOF, even on exception

    full_env = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        env=full_env,
    )

    t_out = threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True)
    t_out.start()
    t_err.start()

    # Drain the queue until both threads have confirmed EOF
    eofs_seen = 0
    while eofs_seen < 2:
        item = merged.get()
        if item is _EOF:
            eofs_seen += 1
        else:
            yield item  # type: ignore[misc]

    t_out.join()
    t_err.join()
    proc.wait()

    # This value is accessible as StopIteration.value when the generator
    # is exhausted.  It is LOST if the caller uses a plain for-loop.
    return proc.returncode  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# [CLI-08, CLI-09, CLI-10] Primary run function
# ---------------------------------------------------------------------------


def run(
    cmd: list[str],
    *,
    cwd: Optional[Path | str] = None,
    env: Optional[dict[str, str]] = None,
    label: Optional[str] = None,
    log_file: Optional[Path] = None,
    echo_cmd: bool = True,
    stderr_style: str = "dim yellow",
) -> int:
    """
    [CLI-08] Execute *cmd*, stream every output line in real-time, return
    the process exit code.

    Parameters
    ----------
    cmd:
        Argument list passed to Popen (``shell=False`` – never use shell=True
        with user-supplied strings).
    cwd:
        Working directory for the subprocess.
    env:
        Extra environment variables merged on top of the current process env.
    label:
        Optional heading printed before the command, e.g. ``"[sbt package]"``.
    log_file:
        If given, all output lines are *also* written here.
    echo_cmd:
        Print the full argument list before executing (transparency).
    stderr_style:
        Rich markup style applied to stderr lines.  Default ``"dim yellow"``
        distinguishes warnings/errors from normal build output without being
        alarming.

    Returns
    -------
    int
        Subprocess exit code (0 = success, anything else = failure).
    """
    if label:
        console.print(f"\n[bold white]{label}[/]")
    if echo_cmd:
        cmd_echo(cmd)

    log_fh = open(log_file, "a", encoding="utf-8") if log_file else None  # noqa: SIM115
    returncode = 0

    try:
        gen = stream_lines(cmd, cwd=cwd, env=env)

        # ----------------------------------------------------------------
        # IMPORTANT: use while/next() – NOT a for-loop.
        # A for-loop discards StopIteration.value (Python language spec).
        # The except clause below is the only way to recover proc.returncode
        # from the generator after it is exhausted.
        # ----------------------------------------------------------------
        while True:
            try:
                stream, line = next(gen)
            except StopIteration as exc:
                if exc.value is not None:
                    returncode = int(exc.value)
                break

            # [CLI-09] Stream-aware real-time output
            if stream == "stderr":
                console.print(f"[{stderr_style}]{line}[/]")
            else:
                console.print(line)

            # Mirror to log file if requested
            if log_fh:
                log_fh.write(f"[{stream}] {line}\n")
                log_fh.flush()

    finally:
        if log_fh:
            log_fh.close()

    return returncode


def run_or_die(
    cmd: list[str],
    *,
    cwd: Optional[Path | str] = None,
    env: Optional[dict[str, str]] = None,
    label: Optional[str] = None,
    log_file: Optional[Path] = None,
    echo_cmd: bool = True,
) -> None:
    """
    [CLI-10] Execute *cmd* and abort the CLI on failure.

    Converts a non-zero exit code into:
      1. A styled Rich error Panel – never a raw Python traceback.
      2. ``SystemExit(returncode)`` – propagated cleanly by Typer.

    Use this for any build step where failure is fatal: sbt compile,
    java elaboration, cmake, make.  Steps that are optional (e.g. a
    lint check) should call ``run()`` and inspect the return code themselves.
    """
    returncode = run(
        cmd,
        cwd=cwd,
        env=env,
        label=label,
        log_file=log_file,
        echo_cmd=echo_cmd,
    )
    if returncode != 0:
        # [CLI-10] Styled error panel instead of raw traceback
        error(
            f"Command failed with exit code [bold]{returncode}[/]:\n"
            f"  [italic]{' '.join(cmd)}[/]"
        )
        raise SystemExit(returncode)


# ---------------------------------------------------------------------------
# [CLI-15] Spinner runner – used by `fslab build` for long synthesis jobs
# ---------------------------------------------------------------------------


def run_with_spinner(
    cmd: list[str],
    *,
    cwd: Optional[Path | str] = None,
    env: Optional[dict[str, str]] = None,
    spinner_text: str = "Running…",
    log_file: Optional[Path] = None,
) -> int:
    """
    [CLI-15] Run *cmd* while showing a Rich Live spinner.

    All stdout/stderr lines are written to *log_file* only – they are NOT
    echoed to the terminal.  This keeps the spinner animation stable even
    when the child process emits thousands of lines per second (e.g. Vivado).

    The spinner subtitle shows a truncated preview of the most recently seen
    line so the user gets a coarse sense of progress.

    Parameters
    ----------
    cmd:
        Command argument list.
    cwd:
        Working directory for the subprocess.
    env:
        Extra environment variables.
    spinner_text:
        Static prefix shown in the spinner line, e.g. ``"Vivado synthesis…"``.
    log_file:
        Required.  Full path to the log file.

    Returns
    -------
    int
        Subprocess exit code.

    Implementation note
    -------------------
    We cannot use ``stream_lines()`` here because its exit code is only
    available via ``StopIteration.value``, and correctly extracting that value
    from inside a ``with Live(...)`` block adds complexity.  Instead we
    replicate the same two-thread / SimpleQueue pattern and capture the return
    code in a mutable ``rc_box`` list that is written by the main thread after
    ``proc.wait()`` completes.
    """
    if log_file is None:
        raise ValueError("run_with_spinner() requires a log_file path.")

    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    full_env = {**os.environ, **(env or {})}
    merged: q_module.SimpleQueue = q_module.SimpleQueue()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        env=full_env,
    )

    def _reader(stream, label: str) -> None:
        try:
            for raw in stream:
                merged.put((label, raw.rstrip("\r\n")))
        finally:
            merged.put(_EOF)

    threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True).start()

    def _make_spinner(preview: str) -> Spinner:
        return Spinner(
            "dots",
            text=Text.assemble(
                (f"{spinner_text}  ", "bold green"),
                (preview, "dim white"),
            ),
            style="green",
        )

    with Live(_make_spinner(""), console=console, refresh_per_second=12) as live:
        with open(log_file, "a", encoding="utf-8") as fh:
            eofs_seen = 0
            while eofs_seen < 2:
                item = merged.get()
                if item is _EOF:
                    eofs_seen += 1
                    continue
                stream_name, line = item  # type: ignore[misc]
                fh.write(f"[{stream_name}] {line}\n")
                fh.flush()
                preview = (line[:88] + "…") if len(line) > 88 else line
                live.update(_make_spinner(preview))

    proc.wait()
    return proc.returncode