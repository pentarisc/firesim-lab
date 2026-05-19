# Background Build + Monitor — Handoff Notes for Next Conversation

**Date written:** 2026-05-14
**Project:** firesim-lab
**Status:** Design complete and agreed with the user. **No code written yet.**
Next conversation's focus: implement the redesign described below.

---

## Purpose of this document

The existing `fslab build fpga` runs the bitstream build synchronously in
the foreground: the local CLI holds the SSH session for the entire
~90-minute Vivado build, then pulls the DCP back, then runs the publisher
locally (S3 upload + `create-fpga-image` + AFI poll). This makes the
local machine a single point of failure for hours-long builds and tied
the publisher to the user's AWS SSO session lifetime — see the **Why now**
section below.

This document specifies a redesign in which:

  1. The build runs in the **background on the remote host**.
  2. The remote wrapper does build → S3 upload → `create-fpga-image`
     submit → exits.
  3. The local CLI **auto-attaches to monitor** by default; Ctrl+C
     detaches without killing the remote.
  4. Authentication on the remote is via **EC2 instance profile**, which
     auto-rotates and eliminates the SSO-expiry failure mode entirely.
  5. The orchestration is **generic across providers and platforms** —
     each platform supplies its own wrapper script, but stamp-file shape,
     monitor UX, launch/attach/cleanup are shared infrastructure.

Restate your understanding of the goal back to the user before changing
any code (per project [CLAUDE.md](../CLAUDE.md)). Place modified versions
of existing files in `tempwork/` per the workflow described there.

---

## Why now (the incident that motivated this)

A real F2 build failed at the publish step after ~90 minutes of
successful Vivado work. Root cause traced to **AWS IAM Identity Center
SSO session expiry**: the user had run `aws sso login` in the morning
(8 h SSO session), kicked off the build later, and crossed the session
boundary mid-publish. The boto3 refresh-token chain dies hard at that
boundary — the refresh token returned by the IdP becomes `invalid_grant`
the moment the session expires server-side, and there's no programmatic
recovery (a fresh `aws sso login` requires browser interaction).

Diagnostic clues that pinned this down (worth understanding for context):

  * `~/.aws/sso/cache/<token-hash>.json` had `expiresAt` only ~40 min
    after the file's `mtime`. IAM Identity Center clips access-token TTL
    to never exceed remaining SSO session lifetime — so a clipped TTL is
    the silent indicator that the underlying session is about to die.
  * boto3 debug output showed the refresh-token exchange hitting
    `sso-oidc:CreateToken` and getting back
    `{"error": "invalid_grant", "error_description": "Invalid refresh token provided"}`.
  * The `expiresAt` clock is bound to the original `aws sso login`
    timestamp — **the client cannot tell how much SSO session is left**;
    only the IdP knows.

The redesign solves the root cause for cloud builds (instance profile
credentials don't expire from the workload's perspective) and makes
hours-long builds resilient to local network/laptop failure as a bonus.

---

## High-level flow

```
USER                  LOCAL fslab                REMOTE host                 AWS
 │                      │                          │                          │
 │ fslab build fpga     │                          │                          │
 ├─────────────────────►│                          │                          │
 │                      │ render wrapper (Jinja2)  │                          │
 │                      │ rsync inputs + script    │                          │
 │                      │ launch in background     │                          │
 │                      ├─────────────────────────►│ wrapper writes build_id  │
 │                      │ (returns PID + build_id) │ to remote stamp & starts │
 │                      │◄─────────────────────────┤                          │
 │                      │ write LOCAL stamp.yaml   │                          │
 │                      │ enter MONITOR (default)  │                          │
 │                      │ tail log over SSH        │ Vivado runs (~90 min)    │
 │ (closes laptop) ────►│ (Ctrl+C detaches)        │ ...                      │
 │                                                 │ build done               │
 │                                                 │ S3 upload of DCP ───────►│
 │                                                 │ create-fpga-image ──────►│
 │                                                 │ write result.yaml        │
 │                                                 │ wrapper exits            │
 │                                                                            │
 │ fslab monitor build  │                          │                          │
 ├─────────────────────►│ read LOCAL stamp.yaml    │                          │
 │                      │ ssh probe build_id stamp │                          │
 │                      ├─────────────────────────►│ (wrapper already exited) │
 │                      │◄─────────────────────────┤ pull result.yaml + logs  │
 │                      │ display summary          │                          │
 │                      │ run cleanup_remote()     │ terminate / stop / no-op │
```

---

## Decisions locked in (with rationale)

These were settled during the design conversation. Implementer should
treat them as fixed unless flagging a concrete issue back to the user.

### D1. Background-only on remote, auto-attach on local

There is no separate "foreground" mode. Every build runs in the
background on the remote. Local CLI auto-attaches to a monitor by
default, so the user-facing experience for a quick interactive run is
identical to today's foreground flow.

**Why:** one code path, no "which mode am I in today?" decision. The
disconnect-and-reconnect use case becomes "Ctrl+C, then `fslab monitor
build` later". Mirrors `docker run` (attached by default, `-d` to
detach) and `kubectl attach`.

### D2. Single in-flight build per project

A project has at most one in-flight build. The local stamp lives at a
**fixed path**: `build/fpga/.fslab/build.yaml`. No multi-build directory,
no `fslab build list`, no build-id argument on `fslab monitor build`.

`fslab build fpga` wipes `build/fpga/` at the start (this is **not** done
today — staging accumulates). Wipe is gated by an in-flight check.

### D3. Remote scope: build → S3 upload → create-fpga-image → exit

The wrapper script, on F2:

  1. Run `build-bitstream.sh`.
  2. On success, upload the DCP tarball directly to S3 (no DCP transit
     through the local machine).
  3. Submit `create-fpga-image`.
  4. Write `result.yaml` with status, AFI, AGFI, S3 key, etc.
  5. Exit with appropriate exit code.

**No AFI polling on remote.** Once `create-fpga-image` returns, no EC2
build host is needed for the polling phase — the AFI build runs in
AWS-managed infra. The local monitor (or the user manually) can poll
`describe-fpga-images` from anywhere, cheaply.

### D4. Pull back: logs + reports + result.yaml. NOT DCP.

Always pull back: the wrapper's `build.log`, the `build/reports/*` dir
(timing / utilization — small, useful for tuning `fpga_frequency` and
`build_strategy`), and `result.yaml`.

DCP tarball is **not** pulled back. It lives in S3. If the user ever
needs it locally for debugging, `aws s3 cp` is one command away.

### D5. AWS auth on remote: EC2 instance profile only

The remote wrapper authenticates via the EC2 instance profile attached
at launch — no SSO, no forwarded credentials, no expiring tokens from
the workload's perspective.

This requires a new field in the host config, e.g.
`build_host.iam_instance_profile: <name>` for ec2_launch. Build aborts
pre-launch if unset. See **AWS setup** section for the IAM role + policy
the user must pre-create.

External (user-managed) hosts have whatever auth the user has set up out
of band — not fslab's problem.

### D6. Generic orchestration, per-platform wrapper script

The "background launch + stamp + attach + cleanup" framework lives in
the platform-agnostic layer. Per-platform specifics (which build script
to run, what fields go in `result.yaml`) live in per-platform wrappers
owned by the corresponding `BitBuilder`.

For now we define the F2 wrapper. Future platforms (Alveo on-prem,
other clouds) plug in by supplying a different wrapper template; the
orchestration code does not change.

### D7. Wrapper script: Jinja2 template under `templates/remote_build/`

Templates live at `fslab-cli/fslab/templates/remote_build/<platform>.sh.j2`.
Rendered into the project tree by `fslab generate` (so the user can
inspect / customize), and uploaded fresh on every `fslab build` (so
template updates always take effect).

### D8. Spot is still the default; warn loudly

`launch_instance` keeps `spot_one_time` as the default. When launching
a background build with spot, surface a clear warning: spot interruption
mid-build wastes hours of work. User can opt into `on_demand` per
project. Do **not** auto-upgrade silently.

### D9. Stamp file's cleanup block is provider-discriminated

The `cleanup` section of the local stamp is a discriminated union keyed
by `provider`. AWS-specific fields (`aws_profile`, `region`,
`instance_id`, `lifecycle`) live ONLY inside the `ec2_launch` variant.
Non-AWS providers (external, future on-prem) define their own variant.

This mirrors the registry-based dispatch already used for
`BITBUILDER_CLASS_REGISTRY`
([bitbuilder.py:104-115](../fslab-cli/fslab/bitstream/bitbuilder.py#L104-L115))
and `make_publisher`
([publisher.py:85-96](../fslab-cli/fslab/bitstream/publisher.py#L85-L96)).

### D10. Cleanup uses `lifecycle` (existing values verbatim) + `original_state` for reuse

| `lifecycle` value | Cleanup action |
|---|---|
| `spot_one_time` | terminate (idempotent) |
| `on_demand` | terminate (idempotent) |
| `reuse` (or whatever the existing schema calls it) | restore to `original_state` (`stopped` → stop, `running` → leave running) |

The `original_state` is captured at launch time before fslab touches the
instance, and stored in the stamp. This implements the user's stated
"if down then down" semantics for managed-reuse instances.

If the existing schema already uses a different word than `reuse` for
the managed case, use whatever's already there — this is a naming
detail, not a design choice.

---

## Local stamp file: schema

Path: `build/fpga/.fslab/build.yaml`

```yaml
build_id: <opaque unique stamp>          # see "build_id format" open question
started_at: <iso8601-utc>
finished_at: <iso8601-utc>               # null until wrapper exits

remote:
  host: <ip-or-dns>
  user: <ssh-user>
  ssh_key_path: <path>                   # so monitor on a fresh shell can reconnect
  remote_log_path: <abs path on remote>  # what monitor tails
  remote_result_yaml_path: <abs path on remote>
  remote_pid_path: <abs path on remote>  # contains the wrapper script's PID
  remote_stamp_path: <abs path on remote># wrapper writes build_id here for verification

build:
  platform: f2
  project_name: <name>
  quintuplet: <q>
  fpga_frequency: <mhz>
  build_strategy: <name>

# Provider-discriminated. Captured at launch, never re-derived from cfg.
cleanup:
  provider: ec2_launch                    # or "external" or future
  # --- ec2_launch fields below ---
  aws_profile: <name>
  region: <aws-region>
  instance_id: <i-xxxxx>
  lifecycle: spot_one_time | on_demand | reuse
  original_state: stopped | running       # ONLY when lifecycle=reuse

# Lifecycle status of the build itself.
status: launching | running | succeeded | failed | abandoned
exit_code: <int or null>                  # populated when wrapper exits
cleanup_done: false                       # flipped true after cleanup_remote() succeeds

# Populated by pulling result.yaml from remote after wrapper exits.
result:
  s3_bucket: <name>
  s3_key: <key>
  afi: <afi-id>
  agfi: <agfi-id>
```

`external` provider variant is just `{provider: external}` — no other
fields, cleanup is a no-op.

---

## Remote stamp file: schema

Path on remote: under the project's remote build dir, e.g.
`<remote_cl_dir>/.fslab/remote_stamp.yaml` (final path TBD by implementer).

```yaml
build_id: <same opaque value as local stamp>
started_at: <iso8601-utc>
hostname: <remote hostname>               # sanity for "is this even the right host"
project_name: <name>
platform: f2
```

The remote stamp is written by the wrapper script as its first action.
`fslab monitor build` SSHes in and verifies that the remote stamp's
`build_id` matches the local stamp's — this guards against "wrong host",
"build dir reused for a different project", etc. If they don't match,
abort with a clear error rather than displaying garbage.

---

## Remote `result.yaml`: contract

Written by the wrapper script just before it exits. Pulled back to local
at end of monitor or end of build.

```yaml
build_id: <opaque>                        # for cross-check vs stamp
status: succeeded | failed
exit_code: <int>
finished_at: <iso8601-utc>

# F2-specific fields. Other platforms write a different shape; the
# orchestrator does not interpret these — it just reads `status` and
# `exit_code` and surfaces the rest to the user.
artifacts:
  dcp_s3_bucket: <name>
  dcp_s3_key: <key>
  afi: <afi-id>
  agfi: <agfi-id>

# On failure, populated with whatever the wrapper could discover.
failure:
  stage: build | s3_upload | create_fpga_image
  message: <human-readable>
```

---

## CLI surface

```
fslab build fpga                # launch build on remote, auto-attach to monitor
fslab build fpga --detach       # launch, exit immediately (CI-friendly)
fslab build fpga --abandon      # discard local state of in-flight build, clean up remote, then start new build
fslab monitor build             # reattach to project's in-flight build
```

### `fslab build fpga` behavior

1. Resolve `BuildConfig` (existing path).
2. Read `build/fpga/.fslab/build.yaml` if present:
   * If present and remote build is **still running** (PID alive on
     remote AND build_id stamp matches): refuse with "build in progress;
     use `fslab monitor build` to attach, or `--abandon` to discard".
   * If present and remote build is **complete but cleanup not done**:
     run `cleanup_remote(stamp)`, wipe local state, proceed.
   * If present and corrupt / unreachable remote / stamp mismatch: warn
     and require explicit `--abandon` to proceed.
3. Wipe `build/fpga/` (the entire dir, not just `.fslab/`).
4. Render wrapper template, rsync inputs + script to remote (existing
   bitbuilder steps for staging).
5. Launch wrapper in background on remote (see **Background launch
   mechanism** open question). Capture PID.
6. Verify wrapper actually started: poll for the remote stamp file with
   matching `build_id` for ~10 s. If absent, error out — do NOT write a
   local stamp for a build that didn't start.
7. Write local `build/fpga/.fslab/build.yaml`.
8. If `--detach`: print build_id and remote host info, exit 0.
9. Otherwise: enter monitor mode (same code path as `fslab monitor
   build`).

### `fslab monitor build` behavior

1. Read `build/fpga/.fslab/build.yaml`. If absent: error "no in-flight
   or recently-completed build for this project."
2. SSH to remote, verify remote stamp's `build_id` matches local. On
   mismatch: error.
3. Determine state:
   * **Wrapper still running** (PID alive): tail `build.log` over SSH.
     Update local stamp's `status` if needed. Ctrl+C detaches cleanly
     (does NOT kill remote).
   * **Wrapper exited**: pull `result.yaml`, `build.log`, `build/reports/*`.
     Update local stamp with `status`, `exit_code`, `result.*`. Run
     `cleanup_remote(stamp)`. Set `cleanup_done: true`. Print summary.

### `--abandon` behavior

1. Read local stamp.
2. Run `cleanup_remote(stamp)` regardless of remote build state.
   Idempotent — terminating an already-terminated instance is fine.
3. If cleanup fails (e.g., AWS creds expired): leave local stamp in
   place, surface error with retry instruction. Do **not** wipe local
   state until cleanup succeeds — otherwise we lose the only handle on
   a possibly-still-running EC2 instance.
4. After cleanup succeeds: set `status: abandoned`, `cleanup_done: true`,
   wipe local state (or move to a `.fslab/abandoned-<ts>/` for
   forensics — implementer's call).

---

## Provider abstraction: the two new methods

`BuildHostProvider`
([buildhost.py](../fslab-cli/fslab/bitstream/buildhost.py)) gains:

```python
class BuildHostProvider(abc.ABC):
    @abc.abstractmethod
    def serialize_cleanup_state(
        self, host: BuildHost, cfg: BuildConfig
    ) -> dict:
        """Capture everything cleanup_from_state will need.
        Called once at launch time; never re-derived from cfg afterward."""

    @classmethod
    @abc.abstractmethod
    def cleanup_from_state(cls, state: dict) -> None:
        """Execute provider-appropriate cleanup using ONLY the captured
        state. No live cfg, no live host. Idempotent."""
```

A new module-level registry mirrors the existing
`BITBUILDER_CLASS_REGISTRY` pattern:

```python
PROVIDER_REGISTRY: dict[str, type["BuildHostProvider"]] = {}

def register_provider(name: str):
    def decorator(cls):
        PROVIDER_REGISTRY[name] = cls
        return cls
    return decorator
```

Top-level helper:

```python
def cleanup_remote(stamp: dict) -> None:
    state = stamp["cleanup"]
    cls = PROVIDER_REGISTRY[state["provider"]]
    cls.cleanup_from_state(state)
```

### `Ec2LaunchProvider.cleanup_from_state`

```python
@classmethod
def cleanup_from_state(cls, state: dict) -> None:
    session = aws_fpga.make_session(
        region=state["region"], profile=state["aws_profile"]
    )
    aws_fpga.check_credentials(session, state["aws_profile"])
    iid = state["instance_id"]
    lc = state["lifecycle"]
    if lc in ("spot_one_time", "on_demand"):
        aws_fpga.terminate_instance(session, iid)
    elif lc == "reuse":
        if state["original_state"] == "stopped":
            aws_fpga.stop_instance(session, iid)
        # original_state == "running" → leave alone
    else:
        raise ValueError(f"unknown lifecycle: {lc}")
```

### `ExternalProvider.cleanup_from_state`

```python
@classmethod
def cleanup_from_state(cls, state: dict) -> None:
    info(f"external host '{state.get('host', '?')}' is user-managed; "
         f"nothing to clean up.")
```

---

## Wrapper script template

Path: `fslab-cli/fslab/templates/remote_build/f2.sh.j2`

Rendered by `fslab generate` into the project at e.g.
`build/fpga/remote_build_f2.sh`. Uploaded to remote on every build.

Template responsibilities (F2 specifics, other platforms differ):

```bash
#!/usr/bin/env bash
# Generated by fslab — do not edit manually.
set -uo pipefail

BUILD_ID="{{ build_id }}"
PROJECT_NAME="{{ project_name }}"
QUINTUPLET="{{ quintuplet }}"
CL_DIR="{{ remote_cl_dir }}"
S3_BUCKET="{{ s3_bucket }}"
S3_KEY="{{ s3_key }}"
LOG_PATH="{{ remote_log_path }}"
RESULT_PATH="{{ remote_result_yaml_path }}"
STAMP_PATH="{{ remote_stamp_path }}"
DCP_GLOB="{{ dcp_tar_glob }}"
AFI_NAME="{{ afi_name }}"
AFI_DESCRIPTION="{{ afi_description }}"

# 1. Write remote stamp first thing so monitor can verify build_id
mkdir -p "$(dirname "$STAMP_PATH")"
cat > "$STAMP_PATH" <<EOF
build_id: $BUILD_ID
started_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
hostname: $(hostname)
project_name: $PROJECT_NAME
platform: f2
EOF

# Trap so we always write a result.yaml on exit
write_result() {
  local rc=$?
  local stage="${CURRENT_STAGE:-unknown}"
  local status="failed"
  [[ $rc -eq 0 ]] && status="succeeded"
  cat > "$RESULT_PATH" <<EOF
build_id: $BUILD_ID
status: $status
exit_code: $rc
finished_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
artifacts:
  dcp_s3_bucket: ${UPLOADED_BUCKET:-}
  dcp_s3_key: ${UPLOADED_KEY:-}
  afi: ${AFI_ID:-}
  agfi: ${AGFI_ID:-}
failure:
  stage: $stage
  message: ${FAILURE_MESSAGE:-}
EOF
}
trap write_result EXIT

# 2. Build
CURRENT_STAGE=build
"{{ remote_build_script }}" --cl_dir "$CL_DIR" \
    --frequency "{{ fpga_frequency }}" \
    --strategy "{{ build_strategy }}"

# 3. Locate DCP tar
CURRENT_STAGE=s3_upload
DCP_TAR=$(ls $CL_DIR/$DCP_GLOB | head -1)
[[ -f "$DCP_TAR" ]] || { FAILURE_MESSAGE="DCP tar not found"; exit 2; }

# 4. S3 upload (instance profile auth, no SSO)
aws s3 cp "$DCP_TAR" "s3://$S3_BUCKET/$S3_KEY"
UPLOADED_BUCKET=$S3_BUCKET
UPLOADED_KEY=$S3_KEY

# 5. create-fpga-image
CURRENT_STAGE=create_fpga_image
RESP=$(aws ec2 create-fpga-image \
    --input-storage-location "Bucket=$S3_BUCKET,Key=$S3_KEY" \
    --logs-storage-location "Bucket=$S3_BUCKET,Key=logs/" \
    --name "$AFI_NAME" \
    --description "$AFI_DESCRIPTION")
AFI_ID=$(echo "$RESP" | jq -r .FpgaImageId)
AGFI_ID=$(echo "$RESP" | jq -r .FpgaImageGlobalId)
```

The wrapper's stdout/stderr is redirected to `$LOG_PATH` by the launch
mechanism (see open question on launch mechanism), not by the wrapper
itself.

**Implementer note:** the F2 wrapper above uses `aws cli` rather than
boto3 because the remote AMI ships AWS CLI and we want to keep the
wrapper free of Python dependencies. If the AMI has reliable boto3,
implementer can rewrite the S3 upload + create-fpga-image steps in
Python — same logic, just less shell.

---

## AWS setup the user must do once

Document this in the user-facing docs (separate from this handoff).
Implementation should fail with a clear pointer to these instructions
when `iam_instance_profile` is missing.

### IAM role + instance profile

```bash
# 1. Trust policy: allow EC2 to assume this role
cat > /tmp/trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

# 2. Permissions: S3 + EC2 FPGA
cat > /tmp/permissions.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:HeadBucket",
        "s3:ListBucket",
        "s3:PutObject"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateFpgaImage",
        "ec2:DescribeFpgaImages"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam create-role \
    --role-name fslab-fpga-builder \
    --assume-role-policy-document file:///tmp/trust-policy.json

aws iam put-role-policy \
    --role-name fslab-fpga-builder \
    --policy-name fslab-fpga-builder-permissions \
    --policy-document file:///tmp/permissions.json

aws iam create-instance-profile \
    --instance-profile-name fslab-fpga-builder

aws iam add-role-to-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --role-name fslab-fpga-builder
```

### `fslab.yaml`

```yaml
build_host:
  provider: ec2_launch
  iam_instance_profile: fslab-fpga-builder   # NEW field
  # existing fields: ami_id, instance_type, key_name, subnet_id, region, ...
```

`launch_instance`
([aws_fpga.py:464](../fslab-cli/fslab/bitstream/aws_fpga.py#L464))
already accepts `iam_instance_profile` — only the schema/wiring is new.

---

## Schema changes (`fslab-cli/fslab/schemas/host_model.py`)

Add `iam_instance_profile: str` (required) to the `Ec2LaunchHostConfig`.
For `external` host configs no change is needed — they don't use
instance profiles.

Cross-validation: error if `provider == ec2_launch` and
`iam_instance_profile` is unset (with the AWS-setup pointer in the
error message).

No change to publish schema — instance profile only matters on the
build host. The publisher still uses the user's local SSO profile for
the (rare) cases the local CLI itself needs to talk to AWS (e.g., AFI
status check from monitor — but even that could go through SSH and use
the remote's instance profile; implementer's call, see open question).

---

## File-by-file change plan

| File | Change |
|---|---|
| `fslab-cli/fslab/bitstream/buildhost.py` | Add `serialize_cleanup_state` + `cleanup_from_state` abstract methods. Add `PROVIDER_REGISTRY` and `register_provider` decorator. Implement on existing providers. |
| `fslab-cli/fslab/bitstream/bitbuilder.py` | Replace synchronous `build_bitstream()` with the new launch+attach flow. Add the `--detach` / `--abandon` paths. Add `monitor_build()` entry point. |
| `fslab-cli/fslab/bitstream/aws_fpga.py` | No new functions needed — `terminate_instance` / `stop_instance` already exist. May add a small helper for capturing `original_state` at launch. |
| `fslab-cli/fslab/bitstream/publisher.py` | Largely **gutted** for ec2_launch + F2: the publisher's logic moves into the remote wrapper script. Keep `LocalTarballPublisher` / `NonePublisher` paths as-is (they don't depend on remote auth). Consider whether `AwsAfiPublisher` should remain as an "AFI-status checker" used by monitor, or be removed entirely. |
| `fslab-cli/fslab/bitstream/buildconfig.py` | Add `iam_instance_profile` plumbing into `BuildConfig`. Add `build_id` generation. |
| `fslab-cli/fslab/schemas/host_model.py` | Add `iam_instance_profile` to `Ec2LaunchHostConfig` + validator. |
| `fslab-cli/fslab/templates/remote_build/f2.sh.j2` | NEW — the wrapper script template (see above). |
| `fslab-cli/fslab/templates/fslab.yaml.j2` | Add `iam_instance_profile` example/comment. |
| CLI module (wherever `fslab build` / `fslab sim` is wired) | Add `fslab monitor build`, add `--detach` and `--abandon` to `fslab build fpga`. |
| `fslab-cli/fslab/templates/CMakeLists.txt.j2` | Probably no change. |

Per project [CLAUDE.md](../CLAUDE.md): place modified versions in
`tempwork/` first, get user confirmation before replacing originals.

---

## Implementation phases (suggested order)

Each phase is independently mergeable.

### Phase 1: provider abstraction + cleanup helper

  * Add `serialize_cleanup_state` / `cleanup_from_state` / `PROVIDER_REGISTRY`.
  * Implement on `Ec2LaunchProvider` and `ExternalProvider`.
  * Add the `cleanup_remote(stamp)` top-level helper.
  * Unit-test cleanup in isolation (mock boto3) for each lifecycle.

### Phase 2: stamp file read/write + build_id

  * Define stamp schema (pydantic or dataclass — match codebase
    convention, probably dataclass given the existing style).
  * Helpers: `read_stamp(project)`, `write_stamp(project, stamp)`,
    `wipe_stamp(project)`.
  * `build_id` generation (see open question).

### Phase 3: schema + iam_instance_profile

  * Add field, validator, fslab.yaml example.
  * Verify it gets passed to `launch_instance`.

### Phase 4: F2 wrapper template

  * Render under `fslab generate` into the project tree.
  * Verify the rendered script runs end-to-end on a remote (manually).

### Phase 5: background launch + verify-started

  * Replace synchronous build with background launch.
  * The "verify wrapper started" step (poll remote stamp for ~10 s) is
    the single most error-prone bit — give it explicit attention.

### Phase 6: monitor mode + Ctrl+C detach

  * `fslab monitor build` reads stamp, SSHes in, tails log.
  * Ctrl+C handler: detach, do NOT kill remote.
  * On wrapper-exited: pull artifacts, run cleanup, update stamp.

### Phase 7: --detach, --abandon, in-flight guard on `fslab build fpga`

  * Wire flags.
  * Implement the in-flight check + wipe gating.

### Phase 8: documentation

  * User-facing setup guide (IAM role creation).
  * Update CLAUDE.md project overview if needed.

---

## Open questions for the implementer

These were left open intentionally during the design conversation —
they're judgment calls that should be made with the actual code in
front of you.

### Q1. `build_id` format

Options:

  * **UUID4** — boring, opaque, no semantic meaning. Easy to mistake
    one for another in logs.
  * **`<utc-ts>-<short-rand>`** — e.g. `20260514T091234Z-a3f2`.
    Human-scannable, sorts chronologically, unique enough. Reads well
    in log lines.
  * **ULID** — sorts chronologically and is opaque. Slight dependency
    cost.

Recommendation: option 2. Discuss with user if you disagree.

### Q2. Background launch mechanism

Options:

  * **`nohup ... > log 2>&1 &`** — simple, ubiquitous, captures PID
    cleanly. Survives SSH disconnect. Default recommendation.
  * **`setsid nohup ...`** — even more detached. Probably overkill.
  * **`systemd-run --user --scope ...`** — clean process isolation
    and journal integration, but requires user lingering enabled and
    is fiddlier to script.
  * **`screen -dmS ...` or `tmux new-session -d ...`** — extra
    dependency on the AMI; not all builders have it.

Recommendation: nohup. Anything more is gold-plating for a niche
benefit.

### Q3. Log streaming: SSH `tail -f` vs periodic pull

For the auto-attach default, the user wants to see Vivado output in
real time. Options:

  * `ssh <host> tail -f <logfile>` — real-time, simple, but the SSH
    connection is held open the whole time. Detach on Ctrl+C just
    closes the SSH; remote keeps running.
  * Periodic `scp` of the growing logfile — robust to network
    flakiness, but laggy and wasteful.

Recommendation: `tail -f` for live attach. The whole point of the
detach pattern is the SSH connection isn't load-bearing.

### Q4. AFI status from local

After the wrapper exits, the AFI is in `pending` for ~30-60 min. The
existing `AwsAfiPublisher.publish` polled this from local. In the new
flow, the user can:

  * Just check later with `aws ec2 describe-fpga-images --fpga-image-ids <afi>`.
  * Have `fslab monitor build` poll AFI status (using the user's
    LOCAL aws_profile — same SSO thing as before, but the polling is
    short and the user is interactively present, so it's not the same
    failure mode).

Decision: support the second option but make it explicit (`fslab monitor
build --wait-afi`?) rather than implicit. The 90-min foot-gun is gone
— polling is just a convenience.

### Q5. Spot interruption mid-build

Spot can be reclaimed any time. When that happens, the remote
disappears. Monitor will see SSH connection refused. Suggested
behavior:

  * Detect connection refused → query EC2 for instance state.
  * If terminated → mark build as `failed`, reason `spot_interruption`,
    surface clearly.
  * `fslab build fpga --abandon` then proceeds normally.

Out of scope for first cut: automatic re-launch on different instance.

### Q6. Where should the AFI status check go for non-cloud platforms?

For F2 there's an AFI; for hypothetical on-prem Alveo there isn't.
The monitor's "wait for final artifact" semantics are platform-specific.
This probably belongs as a method on `BitBuilder` (or a sibling class)
rather than hardcoded in the orchestrator. Implementer should sketch
this when adding the second platform; no need to over-engineer it now.

### Q7. Reuse-mode lifecycle name

Existing schema may already have a name for "managed reuse" (probably
something distinguishing presence/absence of `instance_id`). Use
whatever's already there rather than inventing `reuse`.

---

## Test plan

### Unit tests

  * `cleanup_from_state` for each lifecycle (mock boto3): terminate /
    stop / no-op paths. Idempotency (run twice on already-terminated
    instance, expect success).
  * Stamp file read/write round-trip.
  * In-flight guard logic on `fslab build fpga` (mock remote probe):
    each of "no stamp" / "stamp + alive" / "stamp + completed-not-cleaned"
    / "stamp + corrupt".
  * Schema validator: missing `iam_instance_profile` for ec2_launch.

### Integration tests (manual, on a real F2 instance)

  * Happy path: `fslab build fpga` → auto-attach → see logs → wait
    until done → verify cleanup ran → verify AFI exists in AWS.
  * Detach + reattach: `fslab build fpga` → Ctrl+C after 10 s →
    verify remote still running → `fslab monitor build` → verify
    reattach + final summary.
  * `--detach`: `fslab build fpga --detach` → exits immediately →
    `fslab monitor build` later → verify same outcome.
  * `--abandon` on still-running build: verify EC2 instance is
    terminated, local state wiped, next `fslab build fpga` proceeds.
  * `--abandon` with expired AWS creds: verify error surfaces, local
    state preserved, retry after `aws sso login` works.
  * Reuse mode (`instance_id` set): verify `original_state` captured,
    cleanup leaves stopped-was-stopped and running-was-running.

### Failure-mode tests

  * Wrapper fails to start (e.g. permission denied on script): verify
    no local stamp written, clear error.
  * Wrapper writes remote stamp but build fails: verify `result.yaml`
    captured `failure.stage`, monitor surfaces it cleanly, cleanup runs.
  * Spot interruption mid-build: verify monitor surfaces it as
    `spot_interruption`, `--abandon` cleans up gracefully.
  * Network blip during attached monitor: verify the SSH `tail -f`
    dies, fslab surfaces "connection lost; remote build continues; use
    `fslab monitor build` to reattach", remote build is unaffected.

---

## What is explicitly NOT in scope

  * **Multi-platform**: the framework is generic, but only F2 wrapper
    template is delivered in this round. Alveo / on-prem / other-cloud
    wrappers come later, plugging into the same orchestration.
  * **Cross-machine monitoring**: `fslab monitor build` reads the local
    project's stamp file. Monitoring an in-flight build from a
    completely different machine (no shared filesystem, no copied
    stamp) is a future enhancement.
  * **Auto re-launch on spot interruption**: surface the failure, let
    the user decide.
  * **AFI-poll-on-remote**: the remote stops at `create-fpga-image`
    submit. AFI poll is local convenience.
  * **`local_tarball` publish path**: still NotImplementedError as
    today; remote-publisher work doesn't change that.
  * **Refactor of unrelated bits in `bitbuilder.py`**: per CLAUDE.md
    scope discipline, only touch what this redesign needs.

---

## References

  * Original SSO incident discussion: see conversation transcript that
    produced this handoff (the cache-file forensics + IAM Identity
    Center session model are the key context).
  * Prior handoffs that establish the four-axis architecture this builds
    on:
    * [build-pipeline-migration-handoff.md](build-pipeline-migration-handoff.md)
    * [run-pipeline-handoff.md](run-pipeline-handoff.md)
  * Current code entry points (read these first):
    * [bitbuilder.py](../fslab-cli/fslab/bitstream/bitbuilder.py) —
      `build_bitstream()` is the function being redesigned.
    * [buildhost.py](../fslab-cli/fslab/bitstream/buildhost.py) —
      `BuildHostProvider` base class gains the new methods.
    * [publisher.py](../fslab-cli/fslab/bitstream/publisher.py) —
      `AwsAfiPublisher` logic largely moves into the wrapper script.
    * [aws_fpga.py](../fslab-cli/fslab/bitstream/aws_fpga.py) —
      already has terminate/stop/SSO helpers; no new functions needed.
