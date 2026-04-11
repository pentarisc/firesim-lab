"""
fslab/utils/display.py
======================
[CLI-02] Centralised Rich formatting helpers.

Every command module imports `console` and the helper functions from here
rather than creating its own Console() instance.  This guarantees consistent
styling (colours, prefix icons) across the entire CLI.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.theme import Theme
from rich.markup import escape

# ---------------------------------------------------------------------------
# [CLI-02] Shared console with an opinionated colour theme.
# ---------------------------------------------------------------------------
_THEME = Theme(
    {
        "info": "bold cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "muted": "dim white",
        "cmd": "bold magenta",
        "path": "italic cyan",
        "section": "bold white",
    }
)

console = Console(theme=_THEME)


# ---------------------------------------------------------------------------
# Styled message helpers
# ---------------------------------------------------------------------------

def info(msg: str, **kwargs: Any) -> None:
    """Print a cyan informational message with an ℹ prefix."""
    console.print(f"[info]ℹ[/]  {msg}", **kwargs)


def success(msg: str, **kwargs: Any) -> None:
    """Print a green success message with a ✓ prefix."""
    console.print(f"[success]✓[/]  {msg}", **kwargs)


def warning(msg: str, **kwargs: Any) -> None:
    """Print a yellow warning with a ⚠ prefix."""
    console.print(f"[warning]⚠[/]  {msg}", **kwargs)


def error(msg: str, **kwargs: Any) -> None:
    """
    [CLI-10] Print a styled red error panel.

    Using a Panel makes errors visually distinct from normal log lines and
    avoids raw Python tracebacks reaching the user.
    """
    console.print(
        Panel(
            f"[error]{msg}[/]",
            title="[error]Error[/]",
            border_style="red",
            expand=False,
        ),
        **kwargs,
    )


def section(title: str) -> None:
    """Print a section divider rule."""
    console.print(Rule(f"[section]{title}[/]", style="dim white"))


def cmd_echo(cmd: list[str]) -> None:
    """Print the command that is about to be executed (for transparency)."""
    console.print(f"  [muted]$[/] [cmd]{' '.join(cmd)}[/]")


def kv_table(rows: dict[str, str], title: str = "") -> None:
    """Render a simple two-column key/value Rich table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="muted", no_wrap=True)
    table.add_column(style="white")
    for k, v in rows.items():
        table.add_row(k, v)
    if title:
        console.print(f"[section]{title}[/]")
    console.print(table)

def regex_msg(regex):
    return f"Must match {escape(regex.pattern)}"