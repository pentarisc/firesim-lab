# Run-Pipeline — Handoff Notes for Next Conversation

> **STATUS: IMPLEMENTED (2026-05-17).** All five implementation phases
> (shared-pipeline refactor → schemas → foreground sim → detached sim +
> monitor/abandon → documentation) have landed. For day-to-day usage
> refer to **[run-pipeline-guide.md](run-pipeline-guide.md)** and
> **[aws-setup-run.md](aws-setup-run.md)**. This document is kept for
> design-rationale history. Where the implementation diverged from the
> design below — e.g. the wrapper is re-rendered into
> `run/fpga/staging/` per detached run rather than emitted by
> `fslab generate`; cross-pipeline stamp/lifecycle helpers were not
> fully extracted (Phase 1 lifted hosts + monitor primitives only) —
> trust the guide as the current source of truth.

**Date written:** 2026-05-16
**Project:** firesim-lab
**Status (original):** Design captured below; **no run-side code written yet.**
**Supersedes:** the prior 2026-05-12 version of this document. The
intervening work on background builds + monitor is reflected directly
in the design below rather than narrated as a delta.

Next conversation's focus: implement `target.run:`, the `fslab sim fpga`
subcommand, and the `monitor` / `abandon` extensions described here.

---

## Purpose of this document

This document specifies the run-side counterpart to `target.build:`. It
reuses the four-axis decomposition from the build pipeline (bitbuilder
→ runner, host-model verbatim, publish → artifact_source) and is
designed to share the same orchestration infrastructure that the
background-build redesign introduced (stamp files, provider-discriminated
cleanup, `monitor`/`abandon` plumbing, Jinja2 wrapper templates).

Restate your understanding of the goal to the user before changing any
code (per project [CLAUDE.md](../CLAUDE.md)). Place modified versions of
existing files in `tempwork/` per the workflow described there.

The run-side is grounded in how upstream FireSim actually executes a
simulation on an F1/F2 host:

  1. `sudo fpga-clear-local-image -S <slot> -A` (idempotent reset).
  2. `sudo fpga-load-local-image -S <slot> -I <agfi> -A` (program AGFI;
     this call requires AWS IAM credentials on the instance — see D4).
  3. rsync driver binary + simulation infrastructure to the remote slot
     dir; driver tarball is extracted; per-slot `sim-run.sh` is
     generated and executed.
  4. Driver runs, emitting `uartlog` (primary artifact), plus optional
     `memory_stats.csv`, TracerV trace files, autocounter CSVs, etc.
     FireSim wraps this in a `screen` session so the user can attach
     manually.
  5. On clean target shutdown (target calls `poweroff`), the driver
     exits and results are rsynced back to a per-run results directory.
  6. Teardown: `fpga-clear-local-image`, `rmmod xdma`, `qemu-nbd -d`
     for any NBD-mounted disks.

The firesim-lab run pipeline mirrors this but uses our own wrapper
script (in `--detach` mode) instead of FireSim's `screen`-based pattern,
because we own the lifecycle and the driver exit code is sufficient as
a completion signal.

---

## What just landed (build side — the foundation the run side builds on)

The run-side design assumes the following build-side machinery is in
place. Most of it is the union of two prior rounds of work:

  1. **ec2_launch host model + auth + provider-owned HDK upload**
     (landed code, 2026-05-12).
  2. **Background build + monitor + abandon + stamp + shared cleanup
     abstraction** (design complete per the 2026-05-14 handoff;
     implementation expected before the run-side conversation begins —
     see D3).

The run-side conversation should treat both rounds as the current
starting point. Headlines:

### Host axis — `Ec2LaunchHostConfig`

Two operating modes selected by the presence of `instance_id`:

  * **Ephemeral** (`instance_id` unset). Provider runs `RunInstances`
    each build, waits for SSH, builds, terminates on release.
    Lifecycle is `spot_one_time` (default) or `on_demand`.
  * **Managed reuse** (`instance_id` set). Provider starts a stopped
    instance, uses it, restores to its original state on release
    (`stopped`→stop, `running`→leave). Captured at launch time.

`HostModelConfig` was **explicitly designed for verbatim reuse on the
run side** — assign the same type to `target.run.host`, no subclassing,
no forking (see D5).

### Auth — `aws_profile` + EC2 instance profile

  * `aws_profile` plumbed end-to-end through host and publish axes.
    Independent fields on each AWS-touching axis (users normally set
    them to the same value).
  * `aws_fpga.py` is fully session-based — every helper takes a
    `boto3.Session` as its first argument. Module-level
    `boto3.client(...)` calls are gone.
  * `check_credentials` translates SSO/credential failures into a
    single `AwsCredsExpired` exception with an actionable remediation
    message. Called once per pipeline phase (build, publish, future
    run) at the entry point.
  * `iam_instance_profile` (new field on `Ec2LaunchHostConfig`) is
    required for background-mode builds. The remote build wrapper
    authenticates via the EC2 instance profile rather than forwarded
    SSO credentials — eliminates the hours-long-build SSO-expiry
    failure mode that motivated the background-build redesign.

### Provider-owned platform-HDK upload decision

  * `BuildHostProvider.ensure_platform(host, cfg, *, builder, force_upload)`
    consults a stamp file (`<remote_platform_path>/.firesim-lab-stamp.yaml`)
    and the host's `_upload_mode` to decide skip / upload / fail.
  * Policy: `reuse_strict` (external host → hard error on mismatch),
    `reuse_soft` (ec2_launch + instance_id → auto-upload + warn),
    `fresh` (ec2_launch ephemeral → always upload).
  * `--upload-platform` is a force override, not a literal "do the
    upload" switch.
  * Bitbuilder owns *how* to upload (platform-specific rsync layout +
    excludes); provider owns *whether* to upload.

### Bootstrap script

  * [fslab-cli/scripts/ec2_f2/bootstrap.sh](../fslab-cli/scripts/ec2_f2/bootstrap.sh) —
    idempotent post-upload sanity probe (HDK sourceable, vivado on
    PATH, advisory disk-space check). Runs after every platform
    upload. Soft-fail by design.
  * Lives outside the python package to keep shell scripts editable
    without touching the package tree.
  * The run side gets a sibling — see "Likely-touching" file list
    below.

### Parser registry-default merge

  * `_merge_target_build_defaults` in
    [parser.py](../fslab-cli/fslab/schemas/parser.py) folds
    `registry.platforms[<id>].host_models[<host.type>]` and
    `…publish[<publish.type>]` defaults into the user's
    `target.build.host` / `target.build.publish` blocks *before*
    pydantic validation runs. Shallow merge, user wins.
  * Run side renames this to `_merge_target_defaults` and extends it
    to cover `target.run.host` and `target.run.artifact_source`.

### Background build + monitor + abandon

  * `fslab build fpga` runs **background-on-remote by default**;
    local CLI auto-attaches to a monitor (`tail -f` over SSH).
    Ctrl+C detaches without killing the remote.
  * `fslab build fpga --detach` launches and exits immediately (CI).
  * `fslab build fpga --abandon` discards local state of an in-flight
    build, cleans up the remote, then starts a new build.
  * `fslab monitor build` reattaches to a project's in-flight build.
  * Local stamp at fixed path `build/fpga/.fslab/build.yaml`
    (single in-flight build per project; no list, no build-id arg).
  * Remote wrapper does **build → S3 upload → `create-fpga-image`
    submit → exit**. No AFI polling on remote (that's free in
    AWS-managed infra). Local CLI optionally polls AFI status if
    interactively asked.
  * Only **logs, reports, and `result.yaml`** are pulled back — never
    the DCP itself (it lives in S3).

### Shared cleanup abstraction (provider-discriminated)

  * `BuildHostProvider` (renamed `HostProvider` when lifted into the
    shared layer per D3) gains:
    * `serialize_cleanup_state(host, cfg) -> dict` — captures
      everything cleanup needs at launch time.
    * `cleanup_from_state(state) -> None` — classmethod, idempotent,
      operates on captured state only (no live cfg or host).
  * `PROVIDER_REGISTRY: dict[str, type[HostProvider]]` mirrors the
    existing `BITBUILDER_CLASS_REGISTRY` pattern.
  * Top-level `cleanup_remote(stamp)` dispatches via the registry.
  * Stamp's `cleanup:` block is a discriminated union keyed by
    `provider`. AWS-specific fields (`aws_profile`, `region`,
    `instance_id`, `lifecycle`, `original_state`) live only inside
    the `ec2_launch` variant. `external` variant is just
    `{provider: external}` — cleanup is a no-op.
  * This abstraction is **what the run side hooks into** to get
    monitor / abandon / cleanup for free.

### Wrapper-script template pattern

  * `fslab-cli/fslab/templates/remote_build/f2.sh.j2` — F2 build
    wrapper. Rendered into the project by `fslab generate` so the
    user can inspect / customize; uploaded fresh on every build.
  * Template responsibilities: write remote stamp first; run build
    script; on success do S3 upload + `create-fpga-image`; trap on
    EXIT to always write `result.yaml`; exit with build-script's exit
    code.
  * Auth on remote is via instance profile only (no SSO).
  * The run side adds a sibling template under
    `templates/remote_run/f2.sh.j2` (see D6).

---

## Decisions locked in

Treat as fixed unless flagging a concrete issue back to the user.

### D1. Foreground default; opt-in `--detach`

`fslab sim fpga` runs **foreground by default**:

  * Direct SSH session to the run host, `pty=True`.
  * Driver streams uartlog to local stdout in real time.
  * stdin forwarded so the user can type into the simulated UART.
  * Ctrl+C tears down the driver and immediately runs host cleanup.
  * No stamp file, no `monitor`, no `abandon` involved.

`fslab sim fpga --detach` runs **background-on-remote** like the build
flow:

  * Renders + uploads a run-side wrapper script (see D6).
  * Wrapper runs the driver in background (`nohup`); CLI returns.
  * A local stamp at `run/fpga/.fslab/run.yaml` carries everything
    `fslab monitor run` and `fslab abandon run` need.

**Why both:** runs are *usually* short and interactive (driver attaches
to UART, user types, observes, Ctrl+Cs). But long workloads (Linux boot
+ SPEC, multi-hour benchmarks) want to survive laptop sleep. One default
covers the common case; an explicit flag covers the rare case.

The build side is `--detach`-only-internally because builds are always
long and never interactive. The asymmetry is intentional.

### D2. Top-level `run/` directory in the project tree

A new top-level `run/` mirrors `build/`. Final layout:

```
<project>/
├── build/
│   └── fpga/
│       ├── .fslab/build.yaml          # build stamp (from background-build design)
│       ├── reports/                   # pulled-back timing/utilization reports
│       └── …
└── run/
    └── fpga/
        ├── .fslab/run.yaml            # run stamp — ONLY in --detach mode
        ├── staging/                   # local rendering of run wrapper script (--detach)
        ├── results/
        │   └── <YYYYMMDD-HHMMSS>/     # one per invocation
        │       ├── uartlog
        │       ├── memory_stats.csv   # if produced
        │       ├── tracerv/           # if tracing enabled
        │       ├── autocounter/       # if autocounter enabled
        │       ├── driver.log         # stdout/stderr of the driver process
        │       └── result.yaml        # see "Run result file" below
        └── logs/                      # wrapper-script logs from --detach runs
```

**`fslab sim fpga` wipes `run/fpga/staging/`** at the start of every
detached run (foreground runs don't stage anything locally beyond
results). `results/<timestamp>/` is **append-only** — every run gets a
new directory, never overwritten, so failed runs are preserved for
forensics. Disk-space management is the user's problem.

The list of files rsynced back is **runner-driven** (the F2 runner
declares its result-file glob set). At a minimum every runner pulls:

  * `uartlog`
  * `driver.log`
  * `result.yaml`

Optional extras are gated by features the user enabled in `runner_args`
(tracing, autocounter, etc.).

### D3. Shared orchestration layer

The background-build design introduced (or will introduce):

  * `BuildHostProvider.serialize_cleanup_state` + `cleanup_from_state`.
  * `PROVIDER_REGISTRY` + `register_provider`.
  * Top-level `cleanup_remote(stamp)`.
  * Stamp read/write helpers.
  * `build_id` generation.
  * Monitor: `tail -f` over SSH + Ctrl+C detach.
  * Abandon: idempotent remote cleanup + local-state wipe.

**None of this is build-specific.** For the run side it gets factored
into a pipeline-agnostic module. Suggested location:
`fslab-cli/fslab/pipeline/` with submodules `host.py` (provider base +
registry), `stamp.py`, `monitor.py`, `lifecycle.py`. `BuildHostProvider`
is renamed `HostProvider`; `cleanup_remote` and `monitor_remote` are
parametrised by which stamp file to read.

CLI dispatch:

```
fslab monitor <build|run>     # routes to the right stamp + module
fslab abandon <build|run>     # routes to the right cleanup
```

**Implementation reality check:** the background-build work hasn't
landed yet (per the 2026-05-14 handoff). The implementer for the run
side has two viable orderings:

  1. Land the background-build work first (its own session), then come
     back and build the run pipeline against the now-shared layer.
  2. Build the run pipeline as a sibling first, then refactor both into
     a shared layer in a follow-up.

Recommendation: **(1)**. The shared layer is best designed when only
the build side exists, then validated by adding the run side as a
consumer. Building two parallel implementations and merging them
afterwards is the slower path. Flag this ordering to the user when
starting the run-side conversation.

### D4. AWS auth on the run host: instance profile, minimal IAM

`Ec2LaunchHostConfig` on the run side requires `iam_instance_profile`,
same field name and shape as the build side. The IAM policy attached
to that role is **smaller** than the build role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ec2:DescribeFpgaImages",
      "ec2:AssociateFpgaImage"
    ],
    "Resource": "*"
  }]
}
```

No S3 write, no `CreateFpgaImage`. `fpga-load-local-image` calls into
AWS to fetch the AGFI manifest and verify entitlement — that's the only
reason the run host needs credentials at all. Confirmed against
FireSim's actual remote command sequence (which uses
`sudo fpga-load-local-image -S <slot> -I <agfi> -A` with the EC2
instance role).

If a future feature adds "auto-upload run results to S3 from the run
host", extend this policy at that point — not now.

`external` run hosts inherit whatever IAM their AMI/instance-profile
already has. fslab does not try to manage it. If the user's external
host can't program AGFIs, that's a user-side configuration problem.

### D5. `HostModelConfig` reused verbatim

`HostModelConfig` from
[host_model.py](../fslab-cli/fslab/schemas/host_model.py) is assigned
**unmodified** to `TargetRunConfig.host`. The provider classes
(`ExternalBuildHostProvider`, `Ec2LaunchBuildHostProvider`) are renamed
when lifted to the shared layer (D3) — drop the `Build` prefix:
`ExternalHostProvider`, `Ec2LaunchHostProvider`. They serve both
pipelines.

Build-side `host` and run-side `host` then point to different concrete
instance configs naturally — `z1d.2xlarge` for synthesis,
`f2.6xlarge` for runtime — without any schema gymnastics.

The build side already follows "leave as found" lifecycle semantics
(launched→terminate, started-from-stopped→stop, found-running→leave).
Run side inherits this verbatim. No `--keep-instance` flag is needed
because the `lifecycle` field on the host config and the
`original_state` capture already cover the relevant cases.

### D6. Per-platform run wrapper template (detached mode only)

The current build template lives at
`fslab-cli/fslab/templates/remote_build/f2.sh.j2` (per the
background-build handoff). The run side adds:

```
fslab-cli/fslab/templates/
├── remote_build/
│   └── f2.sh.j2                # existing
└── remote_run/
    └── f2.sh.j2                # NEW
```

Both rendered by `fslab generate` into the project tree so the user
can inspect / customize. Both re-uploaded fresh on every CLI invocation
that uses them.

The run wrapper is **only used in `--detach` mode**. Foreground mode
runs the driver directly over SSH; no wrapper script is rendered or
uploaded.

Run wrapper responsibilities (F2):

  1. Write remote stamp with `run_id`, `started_at`, `hostname`.
  2. `sudo fpga-clear-local-image -S <slot> -A`.
  3. `sudo fpga-load-local-image -S <slot> -I <agfi> -A`.
  4. Exec the driver with the configured flags
     (`+uartlog=…`, `+tracefile=…`, `+autocounter-filename-base=…`),
     redirecting stdout+stderr to `driver.log`.
  5. On driver exit, write `result.yaml` with exit code, status,
     finished_at, and produced-artifact paths.
  6. Teardown: `sudo fpga-clear-local-image`, `sudo rmmod xdma` (if
     applicable), `sudo qemu-nbd -d` for any NBD-mounted disks.

The wrapper exits with the driver's exit code. Trap on EXIT ensures
`result.yaml` is always written (mirrors the build wrapper's pattern).

### D7. `artifact_source` axis on `target.run`

Closed pydantic discriminated union, parallel to the build side's
`publish`:

```yaml
target:
  run:
    artifact_source:
      type: aws_afi           # for F2 today
      agfi: agfi-xxxxxxxxxxxx
```

Initial discriminator set:

  * `aws_afi` — AGFI by id, fetched on the run host via
    `fpga-load-local-image`. **Implemented in first cut.**
  * `local_tarball` — DCP tarball + driver tarball uploaded from local.
    Mirrors build-side `local_tarball` publisher status. **Deferred**
    until the build-side equivalent lands.
  * `hwdb_entry` — by-name lookup once a hwdb registry exists.
    **Deferred** (see "Out of scope").

Cross-validation `[ARTSRC-01]`: `target.run.artifact_source.type` ∈
`platform.run_artifact_sources` (parallel to the build-side `[PUB-03]`
check).

### D8. Single in-flight run per project (detached mode only)

In `--detach` mode, only one run per project may be in flight. The
stamp at `run/fpga/.fslab/run.yaml` is the in-flight indicator.

`fslab sim fpga --detach` behaviour when a stamp exists:

  * Remote still running (PID alive + run_id matches): refuse with
    "run in progress; use `fslab monitor run` to attach, or
    `fslab abandon run` to discard".
  * Remote completed but cleanup not done: run `cleanup_remote(stamp)`,
    wipe stamp, proceed with new run.
  * Stamp corrupt / unreachable remote / run_id mismatch: warn and
    require explicit `fslab abandon run` to proceed.

Foreground runs **do not** write a stamp and **do not** participate in
the in-flight guard. A foreground run that crashes mid-Ctrl+C cleanup
is the user's problem to detect and clean up manually (the host's
lifecycle field still drives the auto-cleanup attempt in the SSH
session's `finally`).

### D9. Run wrapper does not poll for "simulation done from target side"

FireSim detects simulation completion by polling `screen` session
liveness and treating clean shutdown as "target called `poweroff`".
firesim-lab's wrapper is simpler:

  * **The driver process's exit is the completion signal.** Whatever
    causes the driver to exit (target poweroff, fatal error, signal,
    explicit `--max-cycles`) ends the run.
  * The wrapper records the driver's exit code in `result.yaml`.
  * Interpretation of exit code is the user's responsibility today;
    runner-specific exit-code conventions can be added later.

No `screen` dependency. No 10-second polling loop. Simpler than upstream.

### D10. Run-side cleanup uses the same lifecycle/`original_state` machinery

Identical to the build side:

| `lifecycle` | cleanup action |
|---|---|
| `spot_one_time` | terminate (idempotent) |
| `on_demand` | terminate (idempotent) |
| `reuse` (or whatever the existing schema calls the managed case) | restore to `original_state`: `stopped`→stop, `running`→leave |

Spot for run hosts is supported but should warn loudly the same way
the build side does. Spot reclamation of a multi-hour benchmark is the
same foot-gun.

---

## High-level flow

### Foreground (default)

```
USER                  LOCAL fslab                REMOTE host (F2)            AWS
 │                      │                          │                          │
 │ fslab sim fpga       │                          │                          │
 ├─────────────────────►│                          │                          │
 │                      │ resolve RunConfig        │                          │
 │                      │ provider.request()       │                          │
 │                      ├─────────────────────────►│ (boot, ssh ready)        │
 │                      │ rsync driver + scripts   │                          │
 │                      ├─────────────────────────►│                          │
 │                      │ ssh -tt (pty):           │                          │
 │                      │   fpga-clear-local-image │                          │
 │                      │   fpga-load-local-image  ├──► describe + associate ►│
 │                      │   ./driver +uartlog=...  │                          │
 │ uartlog live ◄───────┤ stdout                   │                          │
 │ keystrokes ─────────►│ stdin                    │                          │
 │                      │                          │                          │
 │ target poweroff ─OR─ Ctrl+C ────────────────────►│ driver exits             │
 │                      │ rsync results back       │                          │
 │                      │◄─────────────────────────┤                          │
 │                      │ provider.release()       │                          │
 │                      ├─────────────────────────►│ stop/terminate per cfg   │
 │                      │ summary + results path   │                          │
 │◄─────────────────────┤                          │                          │
```

### Detached

```
USER                  LOCAL fslab                REMOTE host                  AWS
 │ fslab sim fpga       │                          │                          │
 │   --detach           │                          │                          │
 ├─────────────────────►│ resolve, request host    │                          │
 │                      │ rsync driver + wrapper   │                          │
 │                      │ nohup wrapper &          │                          │
 │                      ├─────────────────────────►│ wrapper writes stamp,    │
 │                      │                          │ loads AGFI, runs driver  │
 │                      │ poll remote stamp (~10s) │                          │
 │                      │ write LOCAL run.yaml     │                          │
 │                      │ exit 0 with run_id       │                          │
 │◄─────────────────────┤                          │                          │
 │                                                 │ driver running...        │
 │                                                 │ driver exits             │
 │                                                 │ wrapper writes result.yaml│
 │ fslab monitor run    │                          │                          │
 ├─────────────────────►│ read run.yaml            │                          │
 │                      │ ssh probe stamp+pid      │                          │
 │                      ├─────────────────────────►│ wrapper exited           │
 │                      │ rsync results back       │                          │
 │                      │◄─────────────────────────┤                          │
 │                      │ cleanup_remote(stamp)    │                          │
 │                      │ wipe stamp; print summary│                          │
```

---

## CLI surface

```
fslab sim fpga                # foreground, auto-clean on exit
fslab sim fpga --detach       # background; writes stamp; exits immediately
fslab monitor run             # attach to project's in-flight detached run
fslab abandon run             # cleanup remote + wipe local state for in-flight detached run
```

Mirrors the build side:

```
fslab build fpga                # background-internally, auto-attached monitor
fslab build fpga --detach       # background; exits immediately
fslab monitor build
fslab abandon build
```

The `fslab monitor` and `fslab abandon` subcommands dispatch by pipeline:

```
fslab monitor build → reads build/fpga/.fslab/build.yaml
fslab monitor run   → reads run/fpga/.fslab/run.yaml
fslab abandon build → cleanup_remote(build stamp)
fslab abandon run   → cleanup_remote(run stamp)
```

### `fslab sim fpga` (foreground) behaviour

1. Resolve `RunConfig` from validated project + registry.
2. `provider.request(run_cfg)` → run host with F2 attached. Wait for SSH.
3. rsync driver binary + result-collection script to remote.
4. Open SSH session with pty:
   * `fpga-clear-local-image`, `fpga-load-local-image`.
   * Exec driver with configured flags. stdin/stdout pass-through.
5. On driver exit OR Ctrl+C: rsync results back into
   `run/fpga/results/<timestamp>/`.
6. `provider.release()` honouring lifecycle.
7. Print summary + path to results dir.

### `fslab sim fpga --detach` behaviour

1. Resolve `RunConfig`.
2. Read `run/fpga/.fslab/run.yaml` if present: same in-flight guard
   logic as the build side's `--detach` flow (refuse / auto-recover /
   require abandon, per D8).
3. Wipe `run/fpga/staging/`. Render run wrapper template.
4. `provider.request(run_cfg)`. Wait for SSH.
5. rsync driver binary + rendered wrapper to remote.
6. Launch wrapper in background (`nohup ./wrapper.sh > driver.log 2>&1 &`).
7. Poll remote stamp for ~10 s; if absent, abort and **do not write
   local stamp**.
8. Write `run/fpga/.fslab/run.yaml`.
9. Print run_id and remote host; exit 0.

### `fslab monitor run` behaviour

Only meaningful for detached runs. Mirrors `fslab monitor build`:

1. Read `run/fpga/.fslab/run.yaml`. If absent: error.
2. SSH; verify remote stamp's `run_id` matches local.
3. Determine state:
   * Wrapper running: `tail -f driver.log` over SSH. Ctrl+C detaches
     cleanly (does NOT kill remote).
   * Wrapper exited: rsync `run/fpga/results/<timestamp>/*` back; update
     local stamp; `cleanup_remote(stamp)`; print summary.

### `fslab abandon run` behaviour

Mirrors `fslab abandon build`:

1. Read local stamp.
2. `cleanup_remote(stamp)` regardless of remote state. Idempotent.
3. If cleanup fails (e.g. expired AWS creds): preserve local stamp,
   surface error with retry instruction. **Do not** wipe local state
   until cleanup succeeds — otherwise a still-running EC2 instance
   becomes orphaned.
4. After cleanup succeeds: set `status: abandoned`, `cleanup_done: true`,
   wipe stamp.

---

## Local stamp file: `run/fpga/.fslab/run.yaml`

Schema (parallel to build stamp, detached mode only):

```yaml
run_id: <opaque unique stamp>          # same format as build_id (see open Q1)
started_at: <iso8601-utc>
finished_at: <iso8601-utc>             # null until wrapper exits

remote:
  host: <ip-or-dns>
  user: <ssh-user>
  ssh_key_path: <path>
  remote_results_dir: <abs path on remote>
  remote_driver_log_path: <abs path on remote>
  remote_result_yaml_path: <abs path on remote>
  remote_pid_path: <abs path on remote>
  remote_stamp_path: <abs path on remote>

run:
  platform: f2
  project_name: <name>
  agfi: <agfi-id>                      # from artifact_source.aws_afi
  runner_args: <serialized>            # for forensics

# Identical shape to the build stamp's cleanup section.
cleanup:
  provider: ec2_launch
  aws_profile: <name>
  region: <aws-region>
  instance_id: <i-xxxxx>
  lifecycle: spot_one_time | on_demand | reuse
  original_state: stopped | running    # ONLY when lifecycle=reuse

status: launching | running | succeeded | failed | abandoned
exit_code: <int or null>
cleanup_done: false

# Populated from result.yaml after wrapper exits.
result:
  uartlog_path: run/fpga/results/<ts>/uartlog
  results_dir: run/fpga/results/<ts>/
```

---

## Remote `result.yaml` (run wrapper writes this)

```yaml
run_id: <opaque>                       # cross-check
status: succeeded | failed
exit_code: <int>
started_at: <iso8601-utc>
finished_at: <iso8601-utc>

# F2-specific produced artifacts. Other platforms write a different
# shape; the orchestrator does not interpret these — it surfaces the
# files listed under `artifacts` to the user.
artifacts:
  uartlog: <abs path on remote>
  driver_log: <abs path on remote>
  tracerv:                              # optional, present if tracing enabled
    - <abs path on remote>
  autocounter:                          # optional, present if autocounter enabled
    - <abs path on remote>

# On failure, populated with whatever the wrapper could discover.
failure:
  stage: agfi_load | driver | teardown
  message: <human-readable>
```

---

## Schema additions

### `target.run:` block

New file `fslab-cli/fslab/schemas/runner_args.py`, parallel to
`bitbuilder_args.py`:

```python
RUNNER_ARGS_REGISTRY: dict[str, type[BaseModel]] = {}
RUNNER_PARAMS_REGISTRY: dict[str, type[BaseModel]] = {}

def register_runner_args(name: str): ...
def register_runner_params(name: str): ...
```

For F2, `runner_args` carries the simulation knobs (max cycles, tracing
on/off and ports, autocounter on/off, +verbose flags, workload binary
path on the target, etc.). Initial F2 runner_args schema is whatever
maps cleanly onto driver flags — list them out in the implementation
conversation.

New file `fslab-cli/fslab/schemas/artifact_source.py`, parallel to
`publish.py`:

```python
class AwsAfiArtifactSourceConfig(BaseModel):
    type: Literal["aws_afi"]
    agfi: str                          # AWS-08 regex (agfi-[0-9a-f]{17})

class LocalTarballArtifactSourceConfig(BaseModel):
    type: Literal["local_tarball"]
    dcp_tar_path: Path                 # deferred — paired with build-side local_tarball publisher

ArtifactSourceConfig = Annotated[
    Union[AwsAfiArtifactSourceConfig, LocalTarballArtifactSourceConfig],
    Field(discriminator="type"),
]
```

New regex `AGFI_RE` in `fslab-cli/fslab/utils/regexes.py`.

### `TargetRunConfig` in `project.py`

```python
class TargetRunConfig(BaseModel):
    runner_args: dict[str, Any]              # cross-validated; see BBA pattern
    host: HostModelConfig                    # reuse verbatim
    artifact_source: ArtifactSourceConfig
```

`TargetConfig` gains an optional `run: TargetRunConfig | None`. Optional
because a project that only builds (no `sim fpga`) shouldn't be required
to populate it.

### `RunnerEntry` in `registry.py`

Parallel to `BitbuilderEntry`:

```python
class RunnerEntry(BaseModel):
    python_class: str                        # e.g. "F2Runner"
    args_schema: str                         # name keyed into RUNNER_ARGS_REGISTRY
    params_schema: str | None = None
    params: dict[str, Any] = {}
    # F2-specific recipe paths (driver tarball glob, remote slot dir layout, …)
    remote_slot_parent_subdir: str
    driver_basename: str
```

`MasterRegistry` gains a top-level `runners: dict[str, RunnerEntry]`.

`PlatformEntry` gains:

  * `runner: str | None` — name keyed into `runners:`. `None` means
    "this platform does not support FPGA run".
  * `run_artifact_sources: dict[str, dict]` — allowed artifact source
    types and their per-platform defaults. Validated like the existing
    `publish:` field.

New validation codes:

| Code | Checks |
|---|---|
| `RUN-01..RUN-04` | RunnerEntry well-formed + lookups resolve |
| `RUN-05..RUN-07` | Cross-checks: runner exists, args/params schemas resolvable |
| `RUNA-01..RUNA-04` | runner_args / runner_params validation |
| `ARTSRC-01` | `target.run.artifact_source.type` ∈ `platform.run_artifact_sources` |
| `AWS-08` | AGFI format |

### Parser merge step

`_merge_target_build_defaults` in
[parser.py](../fslab-cli/fslab/schemas/parser.py) is renamed
`_merge_target_defaults` and extended to also fold
`registry.platforms[<id>].host_models[<host.type>]` and
`registry.platforms[<id>].run_artifact_sources[<artifact_source.type>]`
into `target.run.host` / `target.run.artifact_source`. Same shallow
merge; user wins per-key.

---

## Shared-layer refactor scope

The background-build handoff schedules new abstract methods on
`BuildHostProvider` (`serialize_cleanup_state`, `cleanup_from_state`)
and a `PROVIDER_REGISTRY`. The run side promotes that work to a
pipeline-agnostic module.

Proposed new package layout:

```
fslab-cli/fslab/pipeline/
├── __init__.py
├── host.py            # HostProvider base + PROVIDER_REGISTRY + cleanup_remote()
├── stamp.py           # read_stamp / write_stamp / wipe_stamp / build_id|run_id gen
├── monitor.py         # tail-f-over-ssh, Ctrl+C detach, result rsync
└── lifecycle.py       # in-flight guard, --abandon, --detach helpers
```

`fslab-cli/fslab/bitstream/buildhost.py` becomes a thin shim that
imports from `fslab.pipeline.host` and registers the providers under
their build-pipeline names (or simply renames them — see open Q4).

`fslab-cli/fslab/bitstream/bitbuilder.py` and the new
`fslab-cli/fslab/runtime/runner.py` both consume the pipeline package.

**Build files this refactor touches:**

  * `bitstream/buildhost.py` — class renames + import-site moves.
  * `bitstream/bitbuilder.py` — `monitor_build` / `abandon_build` thin
    wrappers now call into `pipeline.monitor` / `pipeline.lifecycle`.
  * `bitstream/buildconfig.py` — no functional change, possibly import
    moves.
  * CLI entry — `fslab monitor` / `fslab abandon` gain the build|run
    dispatcher.

Per CLAUDE.md scope discipline: this refactor is in scope **because**
the run side needs the abstraction; do not opportunistically refactor
build internals beyond what reuse requires.

---

## Wrapper script template — `templates/remote_run/f2.sh.j2`

Skeleton (final paths and flag names per implementer):

```bash
#!/usr/bin/env bash
# Generated by fslab — do not edit manually.
set -uo pipefail

RUN_ID="{{ run_id }}"
PROJECT_NAME="{{ project_name }}"
SLOT="{{ fpga_slot }}"                # default 0
AGFI="{{ agfi }}"
REMOTE_RESULTS_DIR="{{ remote_results_dir }}"
DRIVER_BIN="{{ remote_driver_path }}"
DRIVER_LOG="$REMOTE_RESULTS_DIR/driver.log"
UART_LOG="$REMOTE_RESULTS_DIR/uartlog"
RESULT_PATH="$REMOTE_RESULTS_DIR/result.yaml"
STAMP_PATH="{{ remote_stamp_path }}"

mkdir -p "$REMOTE_RESULTS_DIR" "$(dirname "$STAMP_PATH")"

cat > "$STAMP_PATH" <<EOF
run_id: $RUN_ID
started_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
hostname: $(hostname)
project_name: $PROJECT_NAME
platform: f2
EOF

CURRENT_STAGE=agfi_load
write_result() {
  local rc=$?
  local status="failed"; [[ $rc -eq 0 ]] && status="succeeded"
  cat > "$RESULT_PATH" <<EOF
run_id: $RUN_ID
status: $status
exit_code: $rc
finished_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
artifacts:
  uartlog: $UART_LOG
  driver_log: $DRIVER_LOG
failure:
  stage: $CURRENT_STAGE
  message: ${FAILURE_MESSAGE:-}
EOF
  # Teardown — best effort, never aborts the trap
  sudo fpga-clear-local-image -S "$SLOT" -A 2>/dev/null || true
  sudo rmmod xdma 2>/dev/null || true
}
trap write_result EXIT

# 1. Clear + program AGFI
sudo fpga-clear-local-image -S "$SLOT" -A
sudo fpga-load-local-image -S "$SLOT" -I "$AGFI" -A

# 2. Run driver
CURRENT_STAGE=driver
"$DRIVER_BIN" \
    +uartlog="$UART_LOG" \
    {% for flag in driver_flags %}{{ flag }} \
    {% endfor %}\
    > "$DRIVER_LOG" 2>&1
```

Notes:

  * Uses `sudo` for FPGA commands (FireSim convention; the run AMI must
    have passwordless sudo for these specific commands, or the run user
    must be in `fpga-users`).
  * AWS CLI / boto3 is NOT needed by this wrapper — AGFI fetching
    happens inside `fpga-load-local-image`, which uses the instance
    profile transparently.
  * `driver_flags` is computed from `runner_args` at render time.

---

## File-by-file change plan

### New

| File | Purpose |
|---|---|
| `fslab-cli/fslab/schemas/runner_args.py` | RUNNER_ARGS_REGISTRY + RUNNER_PARAMS_REGISTRY, parallel to bitbuilder_args.py |
| `fslab-cli/fslab/schemas/artifact_source.py` | ArtifactSourceConfig discriminated union |
| `fslab-cli/fslab/runtime/__init__.py` | New package for the run side |
| `fslab-cli/fslab/runtime/runconfig.py` | `RunConfig.from_validated`, parallel to BuildConfig |
| `fslab-cli/fslab/runtime/runner.py` | `Runner` base + `F2Runner` + RUNNER_CLASS_REGISTRY |
| `fslab-cli/fslab/pipeline/__init__.py` | Shared pipeline-agnostic layer (D3) |
| `fslab-cli/fslab/pipeline/host.py` | HostProvider base + PROVIDER_REGISTRY + cleanup_remote |
| `fslab-cli/fslab/pipeline/stamp.py` | Stamp helpers + opaque-id generation |
| `fslab-cli/fslab/pipeline/monitor.py` | tail-f-over-ssh + Ctrl+C detach + result rsync |
| `fslab-cli/fslab/pipeline/lifecycle.py` | In-flight guard, --detach launch, --abandon |
| `fslab-cli/fslab/templates/remote_run/f2.sh.j2` | Run wrapper for F2 (see above) |
| (optional) `fslab-cli/scripts/ec2_f2/run_bootstrap.sh` | Sanity probe for run hosts (fpga-tools present, xdma loadable, sudo works). Mirrors the build-side bootstrap.sh under a different name. |

### Modified

| File | Change |
|---|---|
| `fslab-cli/fslab/schemas/registry.py` | Add `RunnerEntry`, `runners:` top-level, `runner:` + `run_artifact_sources:` on `PlatformEntry`. New `RUN-*` cross-validation codes |
| `fslab-cli/fslab/schemas/project.py` | Add `TargetRunConfig` and `target.run` field (optional); new `RUN-*` / `ARTSRC-*` codes |
| `fslab-cli/fslab/schemas/parser.py` | Rename `_merge_target_build_defaults` → `_merge_target_defaults`; extend to merge run-side defaults |
| `fslab-cli/fslab/schemas/host_model.py` | Add `iam_instance_profile` validator note that it applies to both build and run pipelines (no separate field — same axis) |
| `fslab-cli/fslab/utils/regexes.py` | Add `AGFI_RE` (AWS-08) |
| `fslab-cli/fslab/templates/fslab.yaml.j2` | Add commented `target.run:` example block; document run-side IAM expectations |
| `lib/registry.yaml` | F2 gains `runner: f2` + `run_artifact_sources: { aws_afi: {} }`; new top-level `runners:` catalog with `f2` entry |
| `fslab-cli/fslab/bitstream/buildhost.py` | Lift provider classes into `fslab.pipeline.host`; thin shim left here |
| `fslab-cli/fslab/bitstream/bitbuilder.py` | Wrap `monitor_build` / `abandon_build` as thin callers into `pipeline.monitor` / `pipeline.lifecycle` |
| CLI entry (wherever `fslab sim` / `fslab build` / new `fslab monitor` / `fslab abandon` are wired) | Add `fslab sim fpga` (foreground + `--detach`), `fslab monitor run`, `fslab abandon run`, and the `build|run` dispatcher on monitor/abandon |

---

## Implementation phases

Each phase is independently mergeable. Prerequisite: the background-build
work from the 2026-05-14 handoff has landed (D3 rationale).

### Phase 1: Shared pipeline package (refactor only)

  * Create `fslab.pipeline.*`. Move provider classes, stamp helpers,
    monitor logic from `bitstream/buildhost.py` and `bitstream/bitbuilder.py`.
  * Leave `bitstream/*` as thin shims that re-export for backward
    compatibility within the codebase (no public API today).
  * Build pipeline still works identically. Regression-test against the
    background-build flow.

### Phase 2: Run-side schemas

  * `runner_args.py`, `artifact_source.py`.
  * `RunnerEntry`, `runners:`, `runner:`, `run_artifact_sources:` on
    registry. New validation codes.
  * `TargetRunConfig` on project. Parser merge extension.
  * Update `lib/registry.yaml` and `fslab.yaml.j2` example.
  * Unit-test cross-validation negatives (run-side counterparts to the
    existing build-side negatives).

### Phase 3: Foreground `fslab sim fpga`

  * `RunConfig.from_validated`, `F2Runner`, `make_runner` factory.
  * Reuse `make_host_provider` (lifted to shared layer in phase 1).
  * Foreground SSH+pty flow. rsync results back. Provider release.
  * No stamp, no monitor. **This is the smallest viable run-side
    feature** and should be the first user-visible deliverable.

### Phase 4: Detached `fslab sim fpga --detach`

  * Render + upload run wrapper.
  * Background launch (`nohup`), verify-started via remote stamp.
  * Write local run.yaml.
  * Drop into auto-attach monitor mode? Or just exit?
    **Decision:** exit immediately (the build side's auto-attach default
    doesn't apply here — the user already chose foreground vs detached
    via the flag).

### Phase 5: `fslab monitor run` + `fslab abandon run`

  * Wire into the shared pipeline-agnostic dispatcher.
  * Verify the build|run dispatcher works for both stamp types.
  * Spot-interruption handling (see open Q5 below).

### Phase 6: Documentation

  * IAM setup for run host (the smaller-than-build policy).
  * `fslab sim fpga` user guide.
  * Update CLAUDE.md project overview to mention `target.run` and
    `fslab sim fpga`.

---

## Open questions for the implementer

### Q1. run_id vs build_id format

The background-build handoff recommended `<utc-ts>-<short-rand>` for
build_id. **Use the same format for run_id**, and generate via a
shared helper in `pipeline/stamp.py`. The two IDs need to be
distinguishable in logs — prefix them, e.g. `b-20260516T154100Z-a3f2`
vs `r-20260516T154100Z-b7e1`.

### Q2. Foreground mode: handling Ctrl+C during AGFI load

If the user Ctrl+Cs during `fpga-load-local-image` (which can take
~30 s), the FPGA may be left in an indeterminate state. Options:

  * **Trap Ctrl+C during AGFI-load and finish that operation before
    tearing down.** Safer; brief delay.
  * **Hard-kill and rely on next run's `fpga-clear-local-image` to
    reset.** Simpler; slight risk on the very next run.

Recommendation: trap and let AGFI-load finish, then tear down.

### Q3. Foreground mode: stdin behaviour

Driver typically expects stdin to be the UART input. Forwarding the
local terminal's stdin verbatim works (line-buffered) for most use
cases. For exotic inputs (control chars, escape sequences), the user
may need to use detached mode + `screen` manually. Document the
limitation; do not over-engineer the foreground path.

### Q4. Provider class naming on lift

`ExternalBuildHostProvider` and `Ec2LaunchBuildHostProvider` become
shared. Three naming options:

  * **Drop `Build` prefix:** `ExternalHostProvider`, `Ec2LaunchHostProvider`.
    Cleanest; small renaming pain.
  * **Keep `Build` prefix everywhere:** confusing for run-side readers.
  * **Add an alias module that re-exports under the old names:** keeps
    callers stable; clutter.

Recommendation: drop the prefix in one go and update call sites.

### Q5. Spot interruption mid-run

Same foot-gun as build but worse — a long Linux+benchmark run can lose
hours. Monitor should detect connection-refused → query instance state
→ mark as `failed` with reason `spot_interruption` → require explicit
`fslab abandon run` to clean up. Do **not** auto-relaunch in the first
cut.

### Q6. Result tarball vs per-file rsync

FireSim rsyncs individual files. With autocounter / TracerV enabled,
there can be many small files. Options:

  * Per-file rsync (FireSim parity). Network-efficient with delta-rsync
    but many round-trips.
  * Tar on remote → single rsync → untar locally. Simpler, slightly
    slower for incremental but fine for one-shot.

Recommendation: tar+rsync. Hidden behind the orchestrator; can switch
later if needed.

### Q7. AGFI replication across regions

If the build registered the AFI in `us-east-1` and the run host is in
`us-west-2`, `fpga-load-local-image` will fail unless the AFI was
replicated. The build-side publisher already supports region
replication in its schema; verify the run-side `target.run.host.region`
is validated against `artifact_source` replication targets, OR error
clearly at runtime. First cut: error clearly at runtime; cross-check
later.

---

## Out of scope for the next conversation

  * **Full hwdb registry.** `artifact_source.type=aws_afi` carries the
    AGFI directly. A descriptor-file emit on the build publisher is the
    natural precursor; design when the run side is concrete enough to
    know what fields it needs.
  * **`hwdb_entry` artifact_source type.** Deferred with hwdb.
  * **`local_tarball` artifact_source.** Deferred until the build-side
    `local_tarball` publisher implements the tarball format.
  * **Networked / supernode topologies.** Single-node only. Multi-node
    is a future schema extension on `target.run` (likely a `nodes:`
    list, but the shape will be informed by user needs).
  * **`local`, `slurm`, `docker_local` host models.** Same status as
    build: not planned.
  * **Auto-relaunch on spot interruption.** Surface, don't recover.
  * **Cross-machine monitoring.** `fslab monitor run` reads the local
    project's stamp. Monitoring from a different machine is a future
    enhancement.
  * **Workload provisioning (RISC-V Linux image generation, etc.).**
    The user is expected to supply a workload binary path; firesim-lab
    does not (yet) rebuild target software the way FireSim's manager
    does. If this becomes a feature, it's a separate axis on
    `target.run` (likely `workload:`) and a separate design discussion.

---

## Project working rules (reminder for the next Claude)

From [CLAUDE.md](../CLAUDE.md):

  * Restate goal before doing anything; ask clarifying questions before code.
  * For material changes that touch multiple files, list them upfront and get confirmation.
  * Use `tempwork/` for material edits to existing files (naming:
    `<original_filename_without_ext>--<YYYY-MM-DD>--<HH-MM>.<ext>`, flat dir).
  * Don't read files unless asked or relevant to a specified task.
  * No opportunistic refactoring outside scope.
  * Don't add comments like CHANGED / FIXED / MODIFIED / NEW; write
    comments that survive the change without dating themselves.

---

## Cross-references

  * [build-pipeline-migration-handoff.md](build-pipeline-migration-handoff.md) — original four-axis architecture.
  * [background-build-monitor-handoff.md](background-build-monitor-handoff.md) — background build, monitor, abandon, stamp file, provider-discriminated cleanup. The shared-layer factoring (D3) consumes this work directly.
  * Upstream FireSim references that informed the run flow:
    * Manager Tasks doc — runworkload lifecycle and result collection.
    * `deploy/runtools/run_farm_deploy_managers.py` — actual remote command sequence (`fpga-clear-local-image`, `fpga-load-local-image`, driver launch, teardown).
    * Single-node tutorial — `results-workload/<TIMESTAMP>-<WORKLOAD>/<node>/{uartlog, memory_stats.csv, os-release}` layout that informs our `run/fpga/results/<timestamp>/` layout.
