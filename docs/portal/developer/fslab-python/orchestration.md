# Orchestration Layers

This page covers the FPGA-acceleration machinery: the `bitstream/`, `runtime/`,
`pipeline/`, and `cloudutils/` packages. Metasimulation never reaches here —
`fslab build metasim` / `fslab sim metasim` finish at `cmake`/`make` and the
local simulation binary. Everything below begins when a command targets a real
F2 host: `fslab build fpga`, `fslab sim fpga`, `fslab monitor`, `fslab abandon`.

This is the layer a `debug`/`trace`-style feature most often extends, because
those flows usually mean "run the driver on the FPGA host with extra plusargs
and pull more files back" — which is exactly what the run pipeline already
does. Read this page before adding remote behaviour so you reuse the host,
stamp, and monitor primitives instead of re-implementing SSH.

## Why three packages

The split is by *concern*, not by command:

- **`pipeline/`** — pipeline-agnostic. Knows about SSH hosts, cloud providers,
  and "tail a remote log until a result file appears". Knows **nothing** about
  bitstreams, drivers, AGFIs, or platform recipes.
- **`bitstream/`** — the build pipeline. Builds an FPGA image from the staged
  project on a remote host, using `pipeline/` for the host plumbing.
- **`runtime/`** — the run pipeline. Loads an image onto an FPGA slot and execs
  the driver, again using `pipeline/` for the host plumbing.
- **`cloudutils/`** — thin provider-specific helpers (AWS EC2 lifecycle, FPGA
  image queries) the providers call into.

`bitstream/` and `runtime/` are deliberately parallel. If you understand one,
the other reads as its mirror image — the same stamp/monitor/abandon shape,
specialised for "build an image" versus "run an image".

## The shared layer: `pipeline/`

`pipeline/` is the foundation both pipelines import. Its public pieces:

- **Host abstraction** (`pipeline.host`): a `Host` base with `ExternalHost` and
  `Ec2LaunchHost` concrete types, a `HostProvider` base, a `PROVIDER_REGISTRY`,
  and `cleanup_remote()`. A *provider* knows how to acquire a host (connect to
  a pre-provisioned box, or launch/start an EC2 instance), hand back a
  connected `Host`, and release it (terminate/stop, or leave running). The host
  object exposes the SSH/rsync operations the pipelines need.
- **Monitor primitives** (`pipeline.monitor`): `connect`, `verify_remote_id`,
  tail-until-result, interruptible-sleep, and the two control-flow exceptions
  `MonitorAborted` and `MonitorDetached`. Both pipelines' monitors are thin
  state machines built on these.
- **Stamp helpers** (`pipeline.stamp`): small utilities such as
  `utc_now_iso()`.

The host model the user selects in `fslab.yaml` (`target.build.host.type` /
`target.run.host.type`, an `external` or `ec2_launch` discriminated-union
variant — see {doc}`schemas`) is mapped to a provider here. Adding a new way to
acquire a host is a `pipeline/` + `schemas/host_model.py` change; see
{doc}`extending`.

## The build pipeline: `bitstream/`

Public entry point:

```python
from fslab.bitstream import build_bitstream, check_no_existing_build

build_id = build_bitstream(
    project=config, registry=registry,
    upload_platform=upload_platform, log_file=log,
)
```

`commands/build.py::build_fpga` is the caller. The sequence:

1. **Guard.** `check_no_existing_build()` refuses to start if a stamp for an
   in-flight build already exists (defended again inside `build_bitstream`).
2. **Resolve a `BuildConfig`.** `bitstream.buildconfig.BuildConfig` derives all
   remote paths, the staging directory `build/fpga/cl_<quintuplet>/`, the build
   script, and log locations from the validated config + the platform registry
   entry. Invalid combinations raise `InvalidBuildConfig`.
3. **Acquire a build host.** `bitstream.buildhost` provides
   `make_build_host_provider()` returning an `ExternalBuildHostProvider` or
   `Ec2LaunchBuildHostProvider`. It checks for platform-version mismatches
   (`PlatformVersionMismatch`) and registry/default path conflicts
   (`RegistryDefaultPathConflict`).
4. **Run the bitbuilder.** `bitstream.bitbuilder` selects a `BitBuilder`
   subclass via `make_bitbuilder()` (today `F2BitBuilder`), uploads the staged
   project and the rendered `scripts/remote_build_f2.sh` wrapper, and launches
   the build under `nohup` on the remote. A `BitstreamBuildFailed` is raised on
   setup failure.
5. **Stamp.** A build stamp is written to `build/fpga/.fslab/build.yaml`
   (`bitstream.build_stamp`) recording the `build_id`, status, and a `cleanup:`
   block describing the remote resource to tear down.
6. **Publish.** On success `bitstream.publisher` handles the platform's
   post-build artifact step (for F2 + `aws_afi`, the S3 upload +
   `create-fpga-image`).

The launch is **always background-on-the-remote**. By default the local CLI
then attaches a monitor (mirroring `docker run` semantics); `--detach` returns
immediately. `--skip-compile` skips steps 1–5 of the local compile and reuses a
prior FPGA compile's artefacts, with strict preconditions enforced in
`build_fpga` (a recorded successful FPGA compile and a clean remote-build
slate).

## The run pipeline: `runtime/`

Public entry points:

```python
from fslab.runtime import (
    run_simulation_foreground,   # fslab sim fpga
    launch_detached,             # fslab sim fpga --detach
    monitor_run,                 # fslab monitor run
)
```

- **Foreground** (`run_simulation_foreground`): resolves `target.run` into a
  `RunConfig` (`runtime.runconfig`, raising `InvalidRunConfig` on bad input),
  acquires a run host, loads the AGFI onto the FPGA slot, and execs the driver
  over SSH with a pty so the local terminal *becomes* the simulated UART.
  Results are pulled into `run/fpga/results/<timestamp>/` on exit or Ctrl+C.
- **Detached** (`launch_detached`): same staging and AGFI-load, but the driver
  is launched under `nohup` and a run stamp is written to
  `run/fpga/.fslab/run.yaml`. Returns immediately.
- **Runner selection** (`runtime.runner`): `make_runner()` resolves a `Runner`
  subclass (today `F2Runner`) via the `RUNNER_CLASS_REGISTRY`, populated by
  `@register_runner_class`. `make_run_id()` mints run ids.
- **Payloads** (`runtime.payloads`, `runtime.runconfig`): the `runner_args`
  payload/result-file axis (uploaded inputs, pulled-back outputs, SHA256SUMS
  verification policy) is validated in `schemas/runner_args.py` and realised
  here. This is the natural hook for trace/autocounter output: declare extra
  `result_files`, and the runner pulls them back automatically.

## Stamps, monitor, and abandon

A **stamp** is the single source of truth for an in-flight remote job. It
records the job id, a status enum (with a notion of *terminal* vs *non-terminal*
states), timestamps, and a `cleanup:` block naming the remote resource. Because
the stamp lives locally and the job runs remotely under `nohup`, the laptop can
disconnect, sleep, or be killed without affecting the job.

This enables three operations that share one model across both pipelines:

- **`fslab monitor build` / `fslab monitor run`** (`commands/monitor.py`): read
  the stamp, connect, and either tail the wrapper log, poll post-wrapper status
  (e.g. AFI creation), or print a terminal summary. Ctrl+C raises
  `MonitorDetached` → clean exit; the remote keeps running.
- **`fslab abandon build` / `fslab abandon run`** (`commands/abandon.py`): the
  escape hatch. Runs `cleanup_remote()` against the stamp's `cleanup:` block
  (terminate/stop the instance), then wipes local remote-build/run artefacts.
  Cleanup is idempotent, and on cleanup failure the stamp is **preserved** so
  the user can retry rather than orphan a billing remote resource. Abandon can
  even recover a `cleanup:` block from a corrupt stamp via a raw YAML read.

:::{note}
The build pipeline's `abandon` preserves the *compile layer*
(`generated-src/`, `build/`, `build/fpga/cl_<quintuplet>/`) so a later
`fslab build fpga --skip-compile` can reuse it. The run pipeline's `abandon`
preserves `run/fpga/results/` (prior runs are append-only forensic records).
Keep these scopes in sync if you change either — the precondition checks in
`commands/build.py` and the cleanup scope in `commands/abandon.py` are mirrored
on purpose.
:::

## Where new remote behaviour belongs

| Change | Package |
|---|---|
| New plusargs / extra pulled-back files for a run | `schemas/runner_args.py` (typed fields) + `runtime/` |
| New FPGA platform's build recipe | `bitstream/bitbuilder.py` (`BitBuilder` subclass) + `lib/registry.yaml` |
| New FPGA platform's run recipe | `runtime/runner.py` (`Runner` subclass via `@register_runner_class`) |
| New way to acquire a host | `pipeline/host.py` (provider) + `schemas/host_model.py` |
| New cloud provider helper | `cloudutils/` |

For the schema-side of these (the `*_args` / `*_params` registries and the host
discriminated union) and worked examples, continue to {doc}`extending`.
