# Payload / Workload Management — Handoff Notes for Next Conversation

**Date written:** 2026-05-18
**Date updated:** 2026-05-19 (open questions resolved; design locked)
**Project:** firesim-lab
**Status:** Design locked; **no payload code written yet.**
**Supersedes:** nothing.

The `fslab sim fpga` run pipeline (foreground + detached) is functional
end-to-end on AWS F2 as of [run-pipeline-handoff.md](run-pipeline-handoff.md).
Today the only thing it ships to the remote slot dir is the driver
binary. That's enough for self-contained simulations (workload baked
into the bitstream's loadmem hex), but not for the common case where
the user wants to pass a file via a driver `+plusarg` — most notably
the loadmem-bin bridge:

```yaml
runner_args:
  extra_driver_flags:
    - "+loadmembin=dhrystone.bin"   # the file is local — but never reaches the remote
    - "+baseaddress=0x80000000"
```

Today, that flag references a filename that doesn't exist on the
remote. The user needs a way to declare "this file must travel with
the driver" inside `fslab.yaml`.

This document captures the design problem, the upstream comparison, the
locked-in design decisions, and the implementation plan.

---

## Goal of the next conversation

Add a payload/workload upload axis to `target.run` so that user-
supplied files (loadmem binaries, RISC-V ELFs / kernels, ROM images,
NIC trace files, …) are staged onto the run host alongside the driver
and are addressable from `extra_driver_flags`. Symmetric output-pull
mechanics — extracting files produced *by* the driver back into
`run/fpga/results/<ts>/` — are part of the same axis.

Restate your understanding of the goal to the user before changing any
code (per project [CLAUDE.md](../CLAUDE.md)). Place modified versions of
existing files in `tempwork/`.

---

## Upstream reference — how FireSim handles this

FireSim uses a **declarative workload-JSON manifest**, not blanket
rsync. The full schema is documented at
[Defining Custom Workloads](https://docs.fires.im/en/1.17.1/Advanced-Usage/Workloads/Defining-Custom-Workloads.html);
the salient fields:

| Field | Direction | Scope |
|---|---|---|
| `common_bootbinary` | in | all nodes (per-node copy) |
| `common_rootfs` | in | all nodes (per-node copy) |
| `common_simulation_inputs` | in | extra files supplied to the simulator |
| `simulation_inputs` (per-job) | in | per-node |
| `common_outputs` / `outputs` | out | files extracted from the rootfs after run |
| `common_simulation_outputs` / `simulation_outputs` | out | files from the sim host |
| `post_run_hook` | hook | local script after results land |

Implementation: `FireSimServerNode.get_required_files_local_paths()`
in [firesim_topology_elements.py](https://github.com/firesim/firesim/blob/main/deploy/runtools/firesim_topology_elements.py)
returns an explicit `[(local, remote), ...]` list that the deploy
manager `put()`s onto the remote. The manager does NOT walk a directory
or rsync the build tree.

### Why upstream landed on a declarative manifest

1. **Reproducibility.** The manifest is the contract. Two users with
   the same workload JSON get the same files on the remote, regardless
   of what loose files happen to be lying around the build tree.
2. **Decoupling of build outputs from run inputs.** The build artifact
   dir holds the driver and only the driver. User-authored payloads
   live in a separate location.
3. **Forensics.** A declarative list surfaces in `result.yaml` —
   "this run consumed `payloads/dhrystone.bin` (sha256: …)". Blanket
   rsync loses this entirely.
4. **Bandwidth control.** F2 hosts cost real money. An accidental
   4 GiB file in the wrong dir would be shipped every run under
   blanket rsync; an explicit list catches that at config time.
5. **Multi-node future.** Per-node payloads (different ELFs per slot)
   are a hard requirement upstream. Adding the structure later is
   harder than adding it now.

---

## Locked-in design (2026-05-19)

### YAML shape

A nested `slot:` block under `target.run` groups all per-slot
configuration (today: just one slot, id 0). Within the slot,
`runner_args` carries the new `payloads:`, `result_files:`, and
`verify_hash:` fields:

```yaml
target:
  run:
    host: ...
    artifact_source: ...
    slot:
      id: 0
      runner_args:
        max_cycles: 1000000
        verify_hash: IF_PRESENT          # YES | NO | IF_PRESENT (default)
        payloads:
          - path: payloads/dhrystone.bin   # local, project-relative (or absolute)
            remote_name: dhrystone.bin     # optional; defaults to basename(path)
          - path: payloads/initrd.cpio
        result_files:
          - remote_path: dump.bin          # written by driver into $SLOT_DIR
            local_name: dump.bin           # under run/fpga/results/<ts>/
        extra_driver_flags:
          - "+loadmembin=dhrystone.bin"    # references remote_name, not local path
          - "+initrd=initrd.cpio"
```

**Why nested under `slot:`** — when multi-slot support lands, the
shape refactors cleanly to `slots: [{id, runner_args}, ...]`, with
the inner structure unchanged. Slot is a portable concept (PCIe
multi-FPGA hosts exist on-prem too), even though slot **addressing**
(`fpga-load-local-image -S <n>`) is AWS-specific. This is a
**breaking change** for existing `fslab.yaml` files — `runner_args`
moves from directly under `run:` into `run.slot.runner_args`. Worth
the breakage now while the API has one or two users; not worth it
once it ships widely.

### Mechanics

- **Validation at config-load time.** Each `path` must exist; each
  `remote_name` must be unique within the list and must not collide
  with framework-reserved names (`driver.log`, `result.yaml`,
  `<driver_basename>`, the wrapper script, `SHA256SUMS`).
- **Upload site.** Both the foreground runner ([runner.py](../fslab-cli/fslab/runtime/runner.py))
  and the detached launcher ([launch.py](../fslab-cli/fslab/runtime/launch.py))
  iterate the list and `host.put()` each entry into
  `<remote_slot_dir>/<remote_name>` immediately after uploading the
  driver. Same primitive, just more files. If a `SHA256SUMS` file is
  present in the local payload dir (and `verify_hash` is not `NO`),
  it ships alongside.
- **Driver invocation.** The driver `cd`s into `remote_slot_dir`
  before exec (already true in both paths), so flags like
  `+loadmembin=dhrystone.bin` resolve as relative paths without the
  user having to know the absolute remote path.
- **Result pull.** After driver exit, the runner / wrapper rsyncs
  each `result_files` entry from `<remote_slot_dir>/<remote_path>`
  into `run/fpga/results/<ts>/<local_name>`. Missing files are warned,
  not fatal (the driver may legitimately skip writing them on early
  exit).
- **Forensics.** `result.yaml` gains a `payloads:` block listing
  each `(remote_name, sha256, size_bytes)` consumed.
- **`workload_bin` deprecation path.** The existing
  `workload_bin: Path?` field in
  [F2RunnerArgs](../fslab-cli/fslab/schemas/runner_args.py) becomes
  sugar that prepends a single-entry payload at parse time. Keep it
  working for one release with a deprecation warning, then remove.

### Hash verification (`verify_hash`)

Format: a single `payloads/SHA256SUMS` manifest at the project root,
one line per file in the standard `sha256sum`-compatible form:

```
b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9  dhrystone.bin
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  initrd.cpio
```

`verify_hash` semantics:

| Value | If SHA256SUMS present | If SHA256SUMS absent |
|---|---|---|
| `YES` | verify; fail on mismatch | fail at config-load time |
| `IF_PRESENT` (default) | verify; fail on mismatch | skip, warn once |
| `NO` | skip | skip |

Verification runs in **two places** for both foreground and detached
modes:

1. **Locally**, before upload — `sha256sum -c SHA256SUMS` in the
   local `payloads/` dir. Catches local corruption / forgotten
   regeneration.
2. **On the remote**, after upload, before driver exec — same
   command in `<remote_slot_dir>`. Catches in-flight corruption and
   guarantees the bytes the FPGA host runs are the bytes the user
   intended.

For detached runs, remote verification lives in the wrapper script
([f2.sh.j2](../fslab-cli/fslab/templates/remote_run/f2.sh.j2)) so it
runs even with no CLI attached. For foreground runs, the CLI invokes
`sha256sum -c` via SSH before kicking off the driver.

Failure is **fatal** in either location — the run aborts before the
driver starts.

### Project scaffolding

- `fslab new` creates an empty `payloads/` directory at the project
  root, sibling to `user_rtl/`.
- The generated `.gitignore` excludes `payloads/` wholesale.
  Reproducibility-via-SHA256SUMS is the contract; the binaries
  themselves are not version-controlled.

---

## Rejected alternatives (briefly, with reasons)

| Option | Why rejected |
|---|---|
| Blanket rsync of `build/fpga/cl_*/driver/` | Pollutes the build tree; ships unrelated build outputs; no forensics; surprises the user when staleness creeps in. |
| User-supplied `payload_dir:` rsynced wholesale | Lighter than full-tree rsync, but still no manifest — `result.yaml` can't record what was actually consumed; sha256-pinning awkward; ambiguous when the dir contains output files from a prior run. |
| Per-file `payloads:` list (chosen) | Slightly more upfront work but matches a model that has survived years of upstream FireSim's evolution; aligns with the framework's existing declarative philosophy (everything else in `fslab.yaml` is explicit). |
| Flat `slot: 0` scalar (rejected) | Simpler today but breaks worse later — multi-slot would have to remove the scalar AND restructure `runner_args` placement at the same time. Nested `slot:` block is one breaking change now instead of two later. |
| Single `files:` axis with `direction:` field (rejected) | Conflates two operations that have different validation needs (inputs must exist locally; outputs may or may not exist remotely) and different forensics requirements. Two axes are clearer. |
| Driver-flag synthesis sugar (deferred) | Auto-emitting `+flag=<remote_name>` from a payload's `flag:` field would shave duplication, but the duplication is two strings — not enough to justify the magic. Revisit if a real use case shows up. |

A `payload_dir:` *hybrid* could be added later as sugar for "include
every file under this dir" — but only after the explicit list is
proven. Don't ship both up front.

---

## Resolved design decisions (Q1–Q8)

These were open in the 2026-05-18 draft; resolutions captured 2026-05-19.

### Q1. Inputs and outputs — one axis or two? → **Two**

Separate `payloads:` and `result_files:`. Same reasoning as upstream
FireSim's split — different validation, different forensics, different
direction.

### Q2. Payload location → `payloads/` at project root

- `fslab new` scaffolds an empty `payloads/` dir.
- Generated `.gitignore` excludes `payloads/` wholesale.
- Schema does not *require* paths to be under `payloads/` — absolute
  paths and paths outside the project root remain legal — but the
  scaffolded layout and templates use `payloads/` by convention.

### Q3. sha256-pinning → manifest-based, `verify_hash` enum

- Single `payloads/SHA256SUMS` manifest (one line per file,
  `sha256sum -c` compatible).
- New `verify_hash: YES | NO | IF_PRESENT` field under
  `runner_args`. Default `IF_PRESENT`.
- Verified **both locally before upload and on the remote before
  driver exec**, in both foreground and detached modes.
- Fail-on-mismatch, not warn.
- See "Hash verification" subsection above for the full table.

### Q4. Workload provisioning / build integration → deferred

firesim-lab does not ship a workload builder. Users continue to use
FireMarshal (or any other toolchain) to produce payload binaries
out-of-band. Reconsider if user demand surfaces — design should not
preclude a future `fslab workload build` command, but does not
anticipate it either.

### Q5. Per-node / per-slot payloads → **add `slot:` block now**

- Single `slot:` block under `target.run`, with `id` and
  `runner_args` inside it. See YAML shape above.
- Concept is portable across cloud and on-prem multi-FPGA hosts;
  only the addressing layer (PCIe BDF, `fpga-load-local-image -S`)
  is AWS-specific.
- Multi-slot remains out of scope for this pass — the schema is
  closed enough that `slot: { ... }` → `slots: [ { ... }, ... ]`
  is the only future refactor needed.
- **Breaking change:** existing `fslab.yaml` files with
  `target.run.runner_args` at run-level must move it under
  `target.run.slot.runner_args`. Acceptable given the framework's
  current user base.

### Q6. Post-run hooks → deferred

Once results are pulled into `run/fpga/results/<ts>/`, the user can
run any local script manually. Automate with a `post_run_hook:`
field once there is concrete demand and a clear use case.

### Q7. Driver-flag synthesis sugar → **keep explicit**

`payloads:` and `extra_driver_flags:` stay independent. The
user-visible duplication (filename in two places) is small enough
that explicit > magic. Revisit if a real workload's `extra_driver_flags`
becomes painfully long.

### Q8. Maximum payload size / bandwidth cap → deferred

No size guardrail in the first cut. Add once real workload sizes
surface a footgun.

---

## Out of scope for the next conversation

- **Workload building** (FireMarshal-equivalent). Users supply
  pre-built artifacts; firesim-lab does not yet rebuild target
  software. (Q4.)
- **Multi-node / multi-slot payloads.** Single-slot only; the
  `slot:` block is single-instance for now. (Q5.)
- **Post-run hooks.** Manual scripts against `run/fpga/results/<ts>/`
  for now. (Q6.)
- **Maximum-size guardrail.** Defer until a footgun appears. (Q8.)
- **Streaming payloads / S3-backed payload source.** Idea: host
  payloads in an S3 bucket in the same region as the F2 instance,
  and have the remote pull directly from S3 instead of via SSH from
  the laptop. Likely marginal for small workloads but potentially
  significant for multi-GB ones (S3-to-EC2 same-region bandwidth
  vastly exceeds typical home-office uplink). Defer until real
  workload sizes justify the added complexity — at that point the
  decision is between (a) S3-backed source as a new
  `artifact_source`-style discriminated variant, or (b) a cross-run
  content-addressed cache on the remote keyed by sha256.
- **Cross-run payload caching on the remote.** Re-uploading the
  same 100 MiB payload each run is wasteful; a sha256-keyed cache
  on the remote would skip uploads of unchanged files. Naturally
  pairs with the S3 idea above.

---

## File-by-file change preview

This is what the **first-cut implementation** will touch.

### Modified

- [fslab-cli/fslab/schemas/runner_args.py](../fslab-cli/fslab/schemas/runner_args.py)
  — add `PayloadConfig` + `ResultFileConfig` + `VerifyHash` enum;
  extend `F2RunnerArgs` with `payloads`, `result_files`,
  `verify_hash` fields. `workload_bin` keeps working as sugar with
  a deprecation warning.
- [fslab-cli/fslab/schemas/project.py](../fslab-cli/fslab/schemas/project.py)
  — introduce `SlotConfig` (id + runner_args). Restructure
  `TargetRunConfig`: `runner_args` moves from direct field to
  nested under `slot.runner_args`. Add `slot: SlotConfig` field on
  `TargetRunConfig`. Add new validation codes `PAY-*`
  (path-exists, remote-name uniqueness, reserved-name collision,
  SHA256SUMS-required-when-YES, etc.).
- [fslab-cli/fslab/runtime/runconfig.py](../fslab-cli/fslab/runtime/runconfig.py)
  — surface resolved (absolute) payload paths and the optional
  SHA256SUMS path; thread `verify_hash` through.
- [fslab-cli/fslab/runtime/runner.py](../fslab-cli/fslab/runtime/runner.py)
  — local sha256 verify before upload; upload payloads + SHA256SUMS;
  remote sha256 verify before driver exec; pull `result_files` in
  `F2Runner.run_foreground`.
- [fslab-cli/fslab/runtime/launch.py](../fslab-cli/fslab/runtime/launch.py)
  — local sha256 verify before upload; upload payloads + SHA256SUMS
  alongside the driver+wrapper in the detached path. Remote verify
  lives in the wrapper.
- [fslab-cli/fslab/runtime/monitor_run.py](../fslab-cli/fslab/runtime/monitor_run.py)
  — pull `result_files` into the timestamped results dir on
  wrapper-exit transition. Surface any sha256-mismatch failure
  emitted by the wrapper.
- [fslab-cli/fslab/templates/remote_run/f2.sh.j2](../fslab-cli/fslab/templates/remote_run/f2.sh.j2)
  — add a `sha256sum -c SHA256SUMS` step (gated on
  `verify_hash != NO` and the file being present) before launching
  the driver. Exit non-zero with a clear marker so `monitor_run`
  can report the abort.
- [fslab-cli/fslab/templates/fslab.yaml.j2](../fslab-cli/fslab/templates/fslab.yaml.j2)
  — restructure `target.run` to use the nested `slot:` block.
  Commented `payloads:` + `result_files:` + `verify_hash:` example.
- `fslab-cli/fslab/commands/new.py` (or equivalent — confirm path
  during design) — scaffold empty `payloads/` dir; append
  `payloads/` to the generated `.gitignore`.
- [docs/run-pipeline-guide.md](run-pipeline-guide.md) — user docs:
  document `payloads:`, `result_files:`, `verify_hash:`, the
  nested `slot:` shape, and the migration note for existing
  projects.

### New

- New validation codes `PAY-*` under
  [schemas/project.py](../fslab-cli/fslab/schemas/project.py):
  - `PAY-01` payload path must exist
  - `PAY-02` remote_name must be unique within payloads list
  - `PAY-03` remote_name must not collide with framework-reserved names
  - `PAY-04` `verify_hash: YES` requires `payloads/SHA256SUMS` to exist
  - `PAY-05` SHA256SUMS must list every payload (when verifying)
  - `PAY-06` result_files remote_path must not collide with reserved names
- New validation code `SLOT-01` — `slot.id` must be a non-negative
  integer (today: must be 0 until multi-slot lands).

---

## Migration note (for existing fslab.yaml files)

Any project that currently has:

```yaml
target:
  run:
    host: ...
    artifact_source: ...
    runner_args:
      max_cycles: 1000000
      extra_driver_flags: [...]
```

must restructure to:

```yaml
target:
  run:
    host: ...
    artifact_source: ...
    slot:
      id: 0
      runner_args:
        max_cycles: 1000000
        extra_driver_flags: [...]
```

A clear `[CONFIG]` validation error at parse time should call out
the move, e.g.:

> `target.run.runner_args` was removed in favour of
> `target.run.slot.runner_args`. Move the block under a new
> `slot:` parent with `id: 0`. See docs/run-pipeline-guide.md.

---

## Cross-references

- [run-pipeline-handoff.md](run-pipeline-handoff.md) — the
  surrounding run-pipeline design.
- [run-pipeline-guide.md](run-pipeline-guide.md) — current
  user-facing behaviour; payload story slots in here once landed.
- Upstream FireSim references:
  - [Defining Custom Workloads](https://docs.fires.im/en/1.17.1/Advanced-Usage/Workloads/Defining-Custom-Workloads.html)
  - [run_farm_deploy_managers.py](https://github.com/firesim/firesim/blob/main/deploy/runtools/run_farm_deploy_managers.py)
    — `copy_sim_slot_infrastructure`, `copy_back_job_results_from_run`.
  - [firesim_topology_elements.py](https://github.com/firesim/firesim/blob/main/deploy/runtools/firesim_topology_elements.py)
    — `get_required_files_local_paths`.
