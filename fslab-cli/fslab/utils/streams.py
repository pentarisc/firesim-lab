"""I/O stream utilities."""

from __future__ import annotations

from typing import Any, TextIO


class Tee:
    """Write-only stream that fans out to multiple underlying streams.

    Use this to send a single stream of writes (e.g. the stdout/stderr of a
    remote command) to both the console and a log file simultaneously.

    Each underlying stream needs `.write(str)` and `.flush()`.

    Example:
        with open("build.log", "a") as f:
            out = Tee(sys.stdout, f)
            err = Tee(sys.stderr, f)
            conn.run(cmd, out_stream=out, err_stream=err)

    Notes:
      * Each write triggers a flush on every stream — slightly slower, but
        means the log file is up-to-date if the build crashes mid-command.
      * If one underlying stream is closed mid-run (e.g. a file rotated),
        writes to other streams continue rather than aborting the whole call.
    """

    def __init__(self, *streams: TextIO) -> None:
        self.streams = streams

    def write(self, data: Any) -> int:
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except (AttributeError, ValueError):
                # closed / non-flushable stream — keep going for the others
                pass
        try:
            return len(data)
        except TypeError:
            return 0

    def flush(self) -> None:
        for stream in self.streams:
            try:
                stream.flush()
            except (AttributeError, ValueError):
                pass

    # Some libraries probe these before writing; declare them explicitly
    # so we don't fall back to default object behaviour (which raises).

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False