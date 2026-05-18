"""
fslab/utils/regexes.py
======================
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
    r"^(in|out)\s+(clock|reset|\d+|[a-zA-Z_][a-zA-Z0-9_\[\]:]*)$"
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

# [BB-02] Python class name in CamelCase (used for python_class / args_schema /
# params_schema fields on BitbuilderEntry).
PY_CLASS_NAME_RE = re.compile(r"^[A-Z][a-zA-Z0-9_]*$")

# [AWS-01] AMI ID. Modern AMIs are 17 hex chars; older ones are 8.
AMI_ID_RE = re.compile(r"^ami-[0-9a-f]{8,17}$")

# [AWS-02] AWS region code (e.g. us-west-2, ap-southeast-1).
AWS_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")

# [AWS-03] EC2 instance type (e.g. f2.2xlarge, c5n.18xlarge).
AWS_INSTANCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9]*\.[a-z0-9]+$")

# [AWS-04] S3 bucket name (DNS-compliant subset of the AWS rules).
# Full AWS rules also forbid IP-shaped names and consecutive periods, but the
# regex captures the structural shape; the SDK rejects the rest at runtime.
S3_BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")

# [AWS-05] SNS topic ARN. Topic name uses [A-Za-z0-9_-]; FIFO topics end in
# '.fifo' but we don't enforce that here.
SNS_ARN_RE = re.compile(
    r"^arn:aws[\w-]*:sns:[a-z]{2}-[a-z]+-\d+:\d{12}:[A-Za-z0-9_\-]+(?:\.fifo)?$"
)

# [AWS-06] Named AWS profile (~/.aws/config / ~/.aws/credentials section name).
# AWS itself is loose; this captures the practical shape and blocks whitespace
# and obvious typos. First char is alphanum/underscore; remainder may also
# include dot and dash (common for sso/role profile naming).
AWS_PROFILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")

# [AWS-07] EC2 instance id (e.g. i-0abc1234, i-0abcdef0123456789).
# Modern ids are 17 hex chars; older 8-char ids still resolve, so accept both.
EC2_INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")

# [AWS-08] AGFI id (Amazon FPGA Global Image). Always 17 lowercase hex
# characters; AWS has not used short-form AGFIs.
AGFI_RE = re.compile(r"^agfi-[0-9a-f]{17}$")
