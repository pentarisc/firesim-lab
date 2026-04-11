"""
fslab/commands/regexes.py
=========================
"""

import re

# [PROJ-06]
BRIDGE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

VERILOG_MODULE_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_$]*$')

# [PROJ-01]
PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# [PROJ-02] Scala/Java-style qualified identifiers (dots allowed for packages)
MODULE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

# [PROJ-05] Blackbox port definition: "in|out <width_token>"
#   width_token may be: clock, reset, a decimal number, or a Verilog identifier
BB_PORT_RE = re.compile(
    r"^(in|out)\s+(clock|reset|\d+|[a-zA-Z_][a-zA-Z0-9_]*)$"
)

# [REG-01] IDs: alphanumerics, underscores, hyphens.
ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# [REG-08] Verilog port names.
VERILOG_PORT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]*$")

# [REG-09 / REG-09m] POSIX env var names: uppercase, digits, underscore.
ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# [REG-10 / REG-10m] Library name characters.
LIB_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.\+]+$")

# [REG-11] CMake-style path refs: absolute, ${VAR}, or $ENV{VAR}.
CMAKE_PATH_RE = re.compile(r"^(/|\$\{|\$ENV\{)")

# [REG-11m] Makefile-style path refs: absolute, $(VAR), or ${VAR}.
# Note: $(VAR) is Make env/variable expansion. $ENV{VAR} is cmake-only and
# would appear literally in the Makefile (not expanded by Make) — reject it.
MAKEFILE_PATH_RE = re.compile(r"^(/|\$\(|\$\{)")

# [REG-13 / REG-13m / REG-14] Jinja2 expression/statement/comment markers.
JINJA2_EXPR_RE = re.compile(r"\{\{|\}\}|\{%-?|\{#")

# [REG-09x] Extract $(VAR) references from Makefile-style path strings.
MAKEFILE_VAR_RE = re.compile(r"\$\(([^)]+)\)")