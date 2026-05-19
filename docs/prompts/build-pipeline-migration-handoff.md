# Build-Pipeline Migration — Handoff Notes for Next Conversation

**Date written:** 2026-05-10
**Project:** firesim-lab
**Status:** Schema migration **complete**; runtime/orchestration migration is the work for the next conversation.

---

## Purpose of this document

The previous conversation reworked the build-pipeline configuration architecture
(both schema and YAML). The new schemas are now live under
[fslab-cli/fslab/schemas/](../fslab-cli/fslab/schemas/) but several runtime/
orchestration files still import classes that no longer exist, and a few
schema-level concerns flagged during review were left for follow-up. This
document gives a fresh Claude session enough context to pick up the
implementation work without re-reading the entire previous conversation.

The previous conversation's design rationale is summarised below. Restate your
understanding to the user before changing any code (per project [CLAUDE.md](../CLAUDE.md)).

---

## What just landed (architecture summary)

The build pipeline is now decomposed into **four orthogonal axes**, each
configured under `target.build:` in `fslab.yaml`:

| Axis | What it picks | Configured at | Discriminator |
|---|---|---|---|
| **A. Bitbuilder** | RTL→bitstream recipe (silicon-specific) | Registry catalog (`bitbuilders:`) + per-platform params + per-user args | External lookup (platform → bitbuilder → schema name) |
| **B. Host model** | How the build host is acquired | User (`target.build.host`) | Internal `type:` field, closed pydantic discriminated union |
| **C. Publish** | Post-build artifact handling | User (`target.build.publish`) | Internal `type:` field, closed pydantic discriminated union |
| **× Cross-cutting** | Provenance, notifications, hooks | User (mostly inside `publish.aws_afi`) | n/a |

The same shape is intended to scale to a future `target.run:` block (a sim
*run-host* axis, parallel to build-host) — see "Future / out of scope" below.
**Do not flatten `target.build` into `target` or fuse axes** — the symmetry
with `target.run` is what makes "build on z1d.2xlarge / run on f2.xlarge" cost
optimisation easy to add later.

### What's implemented today

- **Bitbuilder catalog:** `f2` only, in [lib/registry.yaml](../lib/registry.yaml).
- **Host models:** `external` is fully implemented (SSH-reachable pre-provisioned host).
  `ec2_launch` is **schema-only** — schema present so `fslab.yaml` can be authored ahead
  of the provider; the factory must raise `NotImplementedError` if selected.
- **Publishers:** `none`, `local_tarball`, `aws_afi`. The `aws_afi` schema covers
  the full firesim feature set (S3, AGFI, region replication, SNS, post_build_hook),
  but **the publisher implementation does not exist yet** in [bitstream/](../fslab-cli/fslab/bitstream/).

### Three polymorphism patterns (one per axis)

Different parts of the schema layer use deliberately different polymorphism mechanisms.
Don't unify them — each fits its axis:

| Pattern | Where used | When to reach for it |
|---|---|---|
| **Decorator-registered dynamic Union** | `bridges` in [resolvers.py](../fslab-cli/fslab/schemas/resolvers.py) — `BRIDGE_CFG_REGISTRY` + `_get_live_config_model` in [parser.py](../fslab-cli/fslab/schemas/parser.py) | Open extension via custom registries / third-party plugins |
| **Closed pydantic discriminated union** | `host` in [host_model.py](../fslab-cli/fslab/schemas/host_model.py), `publish` in [publish.py](../fslab-cli/fslab/schemas/publish.py) | Framework-owned closed sets where extension means a framework change |
| **Name-keyed registry (external discriminator)** | `bitbuilder_args` / `bitbuilder_params` in [bitbuilder_args.py](../fslab-cli/fslab/schemas/bitbuilder_args.py) | When the schema is selected by an external lookup (here: platform → bitbuilder → schema name) rather than an internal `type:` field |

---

## Implementation tasks for this conversation

These are listed in dependency order. **Start by restating the goal back to the user
and getting confirmation before touching code.**

### 1. Update `fslab.bitstream.buildconfig` ([buildconfig.py](../fslab-cli/fslab/bitstream/buildconfig.py))

**What's broken:** imports `BuildHostConfig` from `fslab.schemas.project` and
reads `platform_entry.remote_build` from `RemoteBuildConfig`. Neither exists
anymore.

**What to change:**

- Replace the `BuildHostConfig` import with `ExternalHostConfig` (and possibly
  `Ec2LaunchHostConfig`) from `fslab.schemas.host_model`.
- The `BuildConfig.from_validated()` method currently extracts paths from
  `platform_entry.remote_build`. The new shape moves these:
  - `local_platform_path`, `local_build_script`, `local_project_staging_subdir`,
    `local_results_subdir` → directly on `PlatformEntry`.
  - `template_cl_name`, `remote_cl_parent_subdir`, `build_script` (now
    `build_script_basename`) → on `BitbuilderEntry`. Resolve via
    `registry.bitbuilders[platform_entry.bitbuilder]`.
  - `instance_type`, `ami_id`, `aws_fpga_version`, `platform_path` (remote)
    → on `platform_entry.host_models["ec2_launch"]` dict (currently unused;
    needed when `ec2_launch` provider lands).
  - For `external`, the remote path comes from
    `project.target.build.host.remote_platform_path` (user-supplied).
- `BuildConfig` field `build_host` should be replaced with
  `host: HostModelConfig` (or specifically `ExternalHostConfig` if you only
  support `external` today and want to type-narrow).
- The factory should reject platforms with `bitbuilder is None` with a clear
  error message ("platform 'X' has no bitbuilder configured for fpga build").

### 2. Update `fslab.bitstream.buildhost` ([buildhost.py](../fslab-cli/fslab/bitstream/buildhost.py))

**What's broken:** imports `BuildHostConfig`. `ExternalBuildHost.__init__`
takes a `BuildHostConfig`.

**What to change:**

- Replace `BuildHostConfig` imports with `ExternalHostConfig`.
- The host/user/ssh_key fields on `ExternalHostConfig` are the same as the
  old `BuildHostConfig`, plus `remote_platform_path` (a new required field).
  No behavioural change to the SSH/rsync logic itself.
- `make_build_host_provider` should branch on the discriminator:
  - `host.type == "external"` → `ExternalBuildHostProvider` (existing).
  - `host.type == "ec2_launch"` → raise
    `NotImplementedError("ec2_launch provider not yet implemented")`.

### 3. Update `fslab.bitstream.bitbuilder` ([bitbuilder.py](../fslab-cli/fslab/bitstream/bitbuilder.py))

**What's stale, not broken:** `make_bitbuilder` currently hard-codes
`if cfg.platform_id == "f2": return F2BitBuilder(cfg)`. It works because
F2 is the only platform with a bitbuilder, but it doesn't use the new catalog.

**What to change:**

- `make_bitbuilder` should look up `registry.bitbuilders[platform_entry.bitbuilder].python_class`
  (a string class name like `"F2BitBuilder"`) and resolve it to the actual
  class via a small `BITBUILDER_CLASS_REGISTRY: dict[str, type[BitBuilder]]`.
- `F2BitBuilder` should register itself: `BITBUILDER_CLASS_REGISTRY["F2BitBuilder"] = F2BitBuilder`
  (or use a decorator `@register_bitbuilder_class`).
- `F2BitBuilder.build_bitstream` reads paths off `cfg`. After task 1, `cfg`
  will carry the recipe paths (template_cl_name, build_script_basename,
  remote_cl_parent_subdir, build_script_flags) sourced from the
  `BitbuilderEntry`. Make sure these are passed through.
- Reorder the orchestration in `build_bitstream()` (the module-level entry
  point at the bottom): **release the host before publishing**, not after.
  Today host release happens in the `finally` after the bitbuilder returns;
  publishing currently doesn't exist so this is a forward-looking note. When
  the publisher runs (next phase), it should run after `provider.release()`
  so that long S3 uploads don't keep an EC2 instance billing.

### 4. Update `fslab.schemas.parser` ([parser.py](../fslab-cli/fslab/schemas/parser.py))

Two related concerns:

#### 4a. Smoke-test that nested discriminated unions work through `LiveFSLabConfig`

`_get_live_config_model` builds a dynamic `LiveFSLabConfig` that overrides
`bridges` with a discriminated union via `create_model(__base__=FSLabConfig, ...)`.
The new `target.build.host` and `target.build.publish` are nested closed
discriminated unions defined statically in [project.py](../fslab-cli/fslab/schemas/project.py).
**They should validate without parser intervention** — pydantic v2 dispatches
discriminated unions natively for nested fields. But this hasn't been
exercised yet. Test path:

```python
from fslab.schemas.parser import load_and_validate
cfg, registry = load_and_validate("path/to/test/fslab.yaml")
assert isinstance(cfg.target.build.host, ExternalHostConfig)  # not the base class
assert isinstance(cfg.target.build.publish, NonePublishConfig)  # or whatever the test config picks
```

If pydantic complains that the union isn't dispatched, the fix is to extend
`_get_live_config_model` to override the nested fields too. Pydantic v2's
`create_model` with `__base__` should propagate the static discriminated
unions, but if not, you'll need to walk through `target → build → host/publish`
overrides.

#### 4b. Add registry-default merge step (only matters once `ec2_launch` is implemented)

The schema design assumes parser.py merges
`registry.platforms[<id>].host_models[<type>]` defaults into the user's
`target.build.host` dict *before* pydantic validation runs. For
`external` + `none` (today's only implemented combo), the registry has no
defaults (the F2 entry is `external: {}`), so no merge is needed and the
existing parser works.

When implementing `ec2_launch`:

```python
# Pseudocode for the merge step, inserted in _internal_load_and_validate
# AFTER reading raw_project but BEFORE LiveConfig.model_validate.
platform_id = raw_project["target"]["platform"]
platform_entry = master_registry.platforms.get(platform_id)
if platform_entry:
    # Merge host defaults
    user_host = raw_project["target"]["build"].get("host", {})
    host_type = user_host.get("type")
    if host_type and host_type in platform_entry.host_models:
        defaults = platform_entry.host_models[host_type]
        merged = {**defaults, **user_host}  # user wins
        raw_project["target"]["build"]["host"] = merged
    # Same for publish
    ...
```

The merge is destructive on the raw dict — that's fine because the dict has
already been read from disk and is local.

### 5. Trivial cleanup: regex module-path docstring

[fslab/utils/regexes.py](../fslab-cli/fslab/utils/regexes.py)'s top docstring
says `fslab/commands/regexes.py` but the file lives at `fslab/utils/regexes.py`.
One-line fix.

---

## Things flagged in review (concerns/caveats)

These were called out at the end of the previous conversation. Most are
addressed by the task list above, but worth re-flagging:

1. **`bitbuilder_args` is `dict[str, Any]` at the schema level.** The
   cross-validator in `FSLabConfig.cross_validate_with_registry` *parses* it
   through the resolved class but doesn't *replace* the field with the typed
   object. Downstream consumers (especially `BuildConfig.from_validated`)
   should call `resolve_args_schema(bb_entry.args_schema).model_validate(...)`
   themselves to get the typed object. This avoids mutating fields after
   pydantic validation. Helper: `fslab.schemas.bitbuilder_args.resolve_args_schema`.

2. **Decorator import order.** `BITBUILDER_ARGS_REGISTRY` and
   `BITBUILDER_PARAMS_REGISTRY` are populated at import time of
   `bitbuilder_args.py`. registry.py imports it directly, so by the time
   `MasterRegistry._cross_validate_bitbuilders` runs, F2 classes are
   registered. For custom plugins: the existing two-pass flow in
   parser.py loads plugins (which can `@register_bitbuilder_args`) before
   `MasterRegistry.from_registry_files` runs. No changes needed.

3. **`fpga_sim` cross-check typo.** The previous registry.py had a
   `[PROJ-11]` code on a `[PROJ-16]` check (and referenced the non-existent
   field `target.fpgasimulators`). Already fixed in the new project.py.

4. **Pydantic v2 nested discriminator dispatch.** See task 4a — needs a smoke
   test once buildconfig.py is updated and an end-to-end parse can be
   exercised.

---

## Future / out of scope for this conversation

These were discussed during the architecture work but deliberately deferred.
**Do not implement them now.** They inform why the schema is shaped the way
it is — the point is *not to break them* prematurely.

### `target.run:` (run-side pipeline, parallel to `target.build`)

The same four-axis decomposition (axes A–C + cross-cutting) is intended to
extend to a run-side pipeline:

```yaml
target:
  platform: f2
  build:                          # build pipeline (today)
    bitbuilder_args: {}
    host: { type: external, ... }
    publish: { type: aws_afi, hwdb_entry_name: my_design_v1 }
  run:                            # run pipeline (future)
    runner_args: { ... }          # parallel to bitbuilder_args
    host: { type: external, ... } # SAME HostModelConfig type, different IP
    artifact_source: { type: aws_afi, hwdb_entry_name: my_design_v1 }
```

The host axis (`HostModelConfig`) is **explicitly designed to be reusable
verbatim** under `target.run.host` — that's why it carries no build-specific
fields. When `target.run` lands, the schema can add it as a peer of `build:`
without changing `host_model.py`.

Per-platform support of `runner` will be declared like `bitbuilder` is today
(with a parallel `runners:` catalog at the registry top level). The platform
entry will gain `runner: <id>` and `supported_artifact_sources: [...]`.

Cost optimisation works for free: the user picks two different `host:` blocks
for `build` vs `run` (e.g. z1d.2xlarge for synthesis, f2.xlarge for execution).

### hwdb (hardware database)

firesim's `config_hwdb.yaml` is the registry of *built bitstreams* that the
runtime references when launching a simulation. firesim-lab does not have
this concept yet. The new schema includes `hwdb_entry_name` fields on
`LocalTarballPublishConfig` and `AwsAfiPublishConfig` so the publisher can
write a small descriptor file (built-artifacts/<name>.yaml with AGFI ID,
git hash, timestamp, etc.). The full registry concept gets revisited when
`target.run` lands. **For now: just emit a small descriptor YAML file alongside
build artifacts; don't design a full registry.**

### Provenance / git metadata embedding

firesim's `BitBuilder.get_metadata_string()` packs build/deploy quintuplets,
makefrag id, and `git rev-parse HEAD --dirty` into AGFI tags. firesim-lab
does not capture this yet. When the publisher implementation lands, it
should auto-capture the git hash + dirty flag + quintuplet and embed in
the descriptor.

### Additional host_model implementations

Reasonable future additions: `slurm` (HPC clusters), `docker_local`
(containerised CI builds). Each adds:
- A new class in [host_model.py](../fslab-cli/fslab/schemas/host_model.py)
- An entry in `KNOWN_HOST_MODELS`
- A new branch in the `make_build_host_provider` factory
- Optional per-host-model defaults on relevant `PlatformEntry` entries

### `local` host model — explicitly NOT planned

The user (project owner) decided against a `local` host model. Rationale:
they don't want to build a Docker image with Vivado in it (image size + EDA
licensing on Docker Hub). Users with local Vivado workstations should use
`external` with their workstation's IP. **Do not add `local` host model.**

---

## Validation requirement codes added (quick reference)

| Code | Where defined | Checks |
|---|---|---|
| `BB-01..BB-09` | [registry.py](../fslab-cli/fslab/schemas/registry.py) | BitbuilderEntry validation + PlatformEntry build-pipeline field rules |
| `BB-10..BB-12` | registry.py (MasterRegistry-level) | Cross-checks: bitbuilder lookup, args/params schemas resolvable, bitbuilder_params validates |
| `BBA-01..BBA-04` | [bitbuilder_args.py](../fslab-cli/fslab/schemas/bitbuilder_args.py) + [project.py](../fslab-cli/fslab/schemas/project.py) | bitbuilder_args / bitbuilder_params schema resolution + validation |
| `HMOD-01..HMOD-05` | [host_model.py](../fslab-cli/fslab/schemas/host_model.py) + registry.py + project.py | Host-model dispatch + path absoluteness + cross-check `host.type ∈ platform.host_models` |
| `PUB-01..PUB-03` | [publish.py](../fslab-cli/fslab/schemas/publish.py) + project.py | Publisher dispatch + cross-check `publish.type ∈ platform.publish` |
| `AWS-01..AWS-05` | [regexes.py](../fslab-cli/fslab/utils/regexes.py) + host_model.py + publish.py | AMI / region / instance type / S3 bucket / SNS ARN format |

---

## Test plan (suggested, for whoever does the implementation)

After updating `buildconfig.py` and `buildhost.py`:

1. **Schema-only sanity:** load
   [lib/registry.yaml](../lib/registry.yaml) through `MasterRegistry`
   and confirm:
   - `mr.bitbuilders["f2"]` resolves
   - `mr.platforms["f2"].bitbuilder == "f2"`
   - `mr.platforms["xilinx_alveo_u250"].bitbuilder is None`
2. **Project sanity:** generate a fresh `fslab.yaml` via `fslab init`,
   confirm it has the new `target.build` shape (host with type, publish
   with type), confirm it parses through `parser.load_and_validate`
   without error.
3. **Cross-validation negatives:**
   - User picks `host.type: ec2_launch` for a platform whose `host_models`
     omits ec2_launch → expect `[HMOD-05]`.
   - User picks `publish.type: aws_afi` for a platform whose `publish` omits
     it → expect `[PUB-03]`.
   - User picks platform with `bitbuilder is None` and runs `fslab build fpga`
     → expect a clear "no bitbuilder configured" error from the BuildConfig
     factory (not from pydantic).
4. **End-to-end build:** point at an external F2 box, run `fslab build fpga`
   for an existing test project. The recipe should run identically to
   pre-migration (no behavioural change — only schema/orchestration).
5. **Smoke test for nested discriminator dispatch** (per task 4a):
   confirm `cfg.target.build.host` is the concrete subclass
   (`ExternalHostConfig`), not the base `HostModelConfigBase`.

---

## Files inventory (post-migration)

**Schemas (live now):**
- [fslab-cli/fslab/schemas/bitbuilder_args.py](../fslab-cli/fslab/schemas/bitbuilder_args.py) (new)
- [fslab-cli/fslab/schemas/host_model.py](../fslab-cli/fslab/schemas/host_model.py) (new)
- [fslab-cli/fslab/schemas/publish.py](../fslab-cli/fslab/schemas/publish.py) (new)
- [fslab-cli/fslab/schemas/registry.py](../fslab-cli/fslab/schemas/registry.py) (modified — adds BitbuilderEntry, reworks PlatformEntry, removes RemoteBuildConfig)
- [fslab-cli/fslab/schemas/project.py](../fslab-cli/fslab/schemas/project.py) (modified — reworks TargetBuildConfig, removes BuildHostConfig)
- [fslab-cli/fslab/utils/regexes.py](../fslab-cli/fslab/utils/regexes.py) (modified — adds 6 regexes)

**Configuration (live now):**
- [lib/registry.yaml](../lib/registry.yaml) (reworked — F2 fully migrated; other platforms have stub `remote_build:` removed and bitbuilder fields omitted)
- [fslab-cli/fslab/templates/fslab.yaml.j2](../fslab-cli/fslab/templates/fslab.yaml.j2) (reworked `target.build` block)

**Runtime (NEEDS UPDATING this conversation):**
- [fslab-cli/fslab/bitstream/buildconfig.py](../fslab-cli/fslab/bitstream/buildconfig.py)
- [fslab-cli/fslab/bitstream/buildhost.py](../fslab-cli/fslab/bitstream/buildhost.py)
- [fslab-cli/fslab/bitstream/bitbuilder.py](../fslab-cli/fslab/bitstream/bitbuilder.py)

**Possibly needs touching:**
- [fslab-cli/fslab/schemas/parser.py](../fslab-cli/fslab/schemas/parser.py) (smoke test discriminator dispatch; merge step deferred)

---

## Project working rules (reminder for the next Claude)

From [CLAUDE.md](../CLAUDE.md):

- Restate goal before doing anything; ask clarifying questions before code.
- For material changes that touch multiple files, list them upfront and get confirmation.
- Use `tempwork/` for material edits to existing files (naming:
  `<original_filename_without_ext>--<YYYY-MM-DD>--<HH-MM>.<ext>`, flat dir).
- Don't read files unless asked or relevant to a specified task.
- No opportunistic refactoring outside scope.
