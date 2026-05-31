# Schemas

The `schemas/` package is the semantic core of `fslab`. It defines the Pydantic
v2 models behind `fslab.yaml` and `registry.yaml`, runs every validation rule,
and produces the two validated objects — `FSLabConfig` and `MasterRegistry` —
that the rest of the CLI consumes. If a config is structurally or semantically
wrong, it is rejected *here*, before any template is rendered or any tool runs.

Read {doc}`index` for the architecture and {doc}`extending` for the
copy-paste recipes. This page explains how the layer is built so you can add
fields, rules, and whole new schema axes confidently.

## The two-pass parser

The single public entry point is:

```python
from fslab.schemas.parser import load_and_validate
config, registry = load_and_validate("fslab.yaml")
```

`load_and_validate` is a cached, locked wrapper around
`_internal_load_and_validate`. The cache means the **first** call in a process
fixes the result; later calls for the same path return it, and a call for a
*different* path raises `RuntimeError`. This is why commands can call
`load_and_validate` repeatedly (generate, then compile, then render) without
re-parsing — and why tests that need a fresh parse must use a fresh path or
reset the module globals.

Internally it runs two passes:

**Pass 1 — build the `MasterRegistry`.**

1. Read `advanced.default_registry` from the raw project YAML (falling back to
   `/opt/firesim-lab/lib/registry.yaml`).
2. Read each `advanced.custom_registries[]` entry in order. An entry may carry a
   `plugin:` path; if so, `_load_user_plugin()` dynamically imports that Python
   module — which runs its `@register_*` decorators and thereby registers new
   types. **This is gated**: it raises `PermissionError` unless
   `ENABLE_CUSTOM_PLUGINS=1`, because executing project-supplied Python is a
   trust decision.
3. Parse each file into a `RegistryFile`, then merge them into one
   `MasterRegistry` with **last-definition-wins** semantics (`REG-07`).

**Between the passes — `_merge_target_defaults`.** Before the project is
validated, registry-supplied defaults are folded into the user's `target.*`
blocks (shallow merge, **user wins on every key**):

- `platforms[<id>].host_models[<type>]` → `target.build.host` and
  `target.run.host`
- `platforms[<id>].publish[<type>]` → `target.build.publish`
- `platforms[<id>].run_artifact_sources[<type>]` → `target.run.artifact_source`

This is why a user can write a three-line `host:` block and still pass
validation — the platform registry supplied the rest. If you add a new
registry-defaulted axis, extend this function alongside the schema.

**Pass 2 — validate the project.** `_get_live_config_model()` builds a
*specialised* subclass of `FSLabConfig` whose `bridges` field is a discriminated
`Union` of every currently-registered bridge config class (see
[Bridges](#bridge-configs-the-plugin-axis) below). The raw project dict is then
validated with the `MasterRegistry` injected as Pydantic **validation context**:

```python
config = LiveConfig.model_validate(raw_project, context={"registry": master_registry})
```

That context is the mechanism that lets project models cross-check against the
registry *during construction* — covered next.

## Project models (`project.py`)

`FSLabConfig` is the root model for `fslab.yaml`. Its direct fields map to the
top-level YAML blocks:

| Field | Model | Block |
|---|---|---|
| `project` | `ProjectConfig` | identity (`name`, `package_name`, `config_class`, `project_dir`) |
| `design` | `DesignConfig` | `type`, `top_module`, `parameters`, `sources`, `blackbox_ports` |
| `target` | `TargetConfig` | `platform`, `clock_period`, `fpga_sim`, `build`, optional `run` |
| `host` | `HostConfig` | emulator + C++ build settings |
| `bridges` | `List[Any]` → discriminated union | the bridge instances |
| `advanced` | `AdvancedConfig` | registry paths, toolchain roots, gen params |

Validation happens at three levels, and knowing which to use is most of the job:

- **`@field_validator(..., mode="before")`** — format checks on a single field,
  run on the raw value. Most regex checks live here (e.g.
  `ProjectConfig.validate_name` → `PROJECT_NAME_RE`, `PROJ-01`).
- **`@model_validator(mode="after")`** — cross-field checks within one model
  (e.g. `DesignConfig.validate_blackbox_rules` enforces that a `blackbox` design
  has ports incl. `in clock`/`in reset` — `PROJ-07`, and that a `chisel` design
  has none — `PROJ-08`).
- **`@computed_field`** — derived, read-only fields. `ProjectConfig.fslab_top`
  turns `my-design` into `MyDesignTop`; it is not in the YAML.

### Cross-registry validation

The big `FSLabConfig.cross_validate_with_registry` model-validator is where
project-vs-registry checks run. It pulls the registry out of the validation
context (`info.context["registry"]`) and, if present, enforces:

| Code | Rule |
|---|---|
| `PROJ-10` | bridge names unique within the project |
| `PROJ-11` | `target.platform` exists in the registry |
| `PROJ-12` | each `bridge.type` exists in the registry |
| `PROJ-13` | every `port_map` value is a declared blackbox port, and each key is in the bridge's `input_ports`/`output_ports` **in the matching direction** |
| `PROJ-16` | `target.fpga_sim` exists in `fpgasimulators` |
| `HMOD-05/06`, `FSLOT-02/03` | host-model type supported by platform; `fpga_slot` absent on build / present on run |
| `PUB-03`, `ARTSRC-01` | publish / artifact-source type supported by platform |
| `BBA-01`, `RUNA-01` | `bitbuilder_args` / `runner_args` validate against the resolved schema class |
| `RUN-20` | `target.run` requires the platform to declare a `runner` |

A second model-validator, `validate_design_sources` (`PROJ-14`), resolves each
declared source path against `project_dir` and fails if a file is missing.

:::{note}
Cross-registry checks are guarded by `if info.context is None: return self`. If
you ever build an `FSLabConfig` *without* injecting the registry context, those
checks silently skip — which is correct for unit tests of pure structure but
means **the registry context is required for a real validation**. Always go
through `load_and_validate`.
:::

## Registry models (`registry.py`)

`registry.yaml` is parsed in two tiers:

- **`RegistryFile`** — one parsed file. Holds lists of `BridgeEntry`,
  `BitbuilderEntry`, `RunnerEntry`, `PlatformEntry`, `FeatureEntry`,
  `MetaSimEntry`, `FpgaSimEntry`. Its model-validator enforces intra-file id
  uniqueness per category (`REG-06`).
- **`MasterRegistry`** — the merged, dict-keyed view (`bridges: dict[id, …]`,
  etc.). Built by `from_registry_files()` with last-wins merge (`REG-07`), then
  runs the cross-file checks `_cross_validate_bitbuilders()` and
  `_cross_validate_runners()`.

The entries carry the bulk of the framework's rules. A few patterns recur:

- **Path syntax differs by consumer.** `PlatformEntry` path fields use
  **CMake** syntax (`/abs`, `${VAR}`, `$ENV{VAR}` — `REG-11`, validated by
  `CMAKE_PATH_RE`) because they land in `CMakeLists.txt`. `MetaSimEntry` /
  `FpgaSimEntry` path fields use **Makefile** syntax (`/abs`, `$(VAR)`,
  `${VAR}` — `REG-11m`, `MAKEFILE_PATH_RE`) because they land in a generated
  `Makefile.<id>.sim`. Mixing them is a validation error on purpose — `$ENV{}`
  is meaningless to Make.
- **Verbatim-emit fields reject Jinja2.** `cmake_fragment` / `makefile_fragment`
  are escape hatches copied through untouched, so `_validate_no_jinja2`
  (`REG-13`/`REG-13m`/`REG-14`) rejects `{{`/`}}`/`{%`/`{#` — an unresolved
  marker would corrupt the output.
- **Env-var cross-checks.** Any `$ENV{VAR}` / `$(VAR)` referenced in a path must
  be declared in `required_env_vars` (`REG-09`/`REG-09x`), so CMake/Make emit a
  clear "missing env var" error at configure time instead of silently producing
  a broken path.
- **Bitbuilder/runner consistency.** `BB-05` requires all four `local_*` paths
  when a platform sets `bitbuilder`; `BB-10`/`RUN-10` require the referenced
  bitbuilder/runner to exist; `BB-11`/`RUN-11` require their `args_schema` /
  `params_schema` names to resolve; `BB-12` validates each platform's
  `bitbuilder_params` against the resolved class.

`schemas/__init__.py` re-exports the commonly used models (`FSLabConfig`,
`MasterRegistry`, `BridgeEntry`, `PlatformEntry`, …) and `load_and_validate`, so
importers use `from fslab.schemas import …`.

(bridge-configs-the-plugin-axis)=
## Bridge configs: the plugin axis (`resolvers.py`)

Bridges are the one project-side axis that is **open to extension** rather than a
closed framework union, because third-party IP descriptors need to register
without editing the repo. The mechanism:

```python
BRIDGE_CFG_REGISTRY = []                 # module-level list

def register_bridge_cfg(cls):            # decorator appends to it
    BRIDGE_CFG_REGISTRY.append(cls)
    return cls

@register_bridge_cfg
class UartBridgeConfig(BridgeConfig):
    type: Literal['uart']
```

At parse time `_get_live_config_model()` reduces every registered class into a
`Union[...]` with `Field(discriminator='type')` and substitutes it for
`FSLabConfig.bridges`. So adding a class (in-tree in `resolvers.py`, or via a
`plugin:` module) makes a new `type:` selectable with zero changes to the
parser.

The base `BridgeConfig` enforces the instance `name` format (`PROJ-06`) and
gives every bridge a `resolve_refs(design_params)` hook (run by the parser's
after-validator) that resolves `ref:`-style parameters against
`design.parameters`. A subclass overrides it for derived parameters —
`BlockdevBridgeConfig` computes `tag_bits` from `n_trackers` there.
`BridgeParam` models the `value:` / `ref:` exclusivity of a single parameter.

## The name-keyed schema axes: bitbuilder / runner args

Two axes use a different dispatch from bridges. Instead of an internal `type:`
discriminator, the schema class is selected by an **external name lookup**
(platform → bitbuilder/runner → `args_schema` string). Each has a paired
user-side and registry-side schema, populated by decorators into name-keyed
dicts:

| File | User args | Registry params | Lives at (user) |
|---|---|---|---|
| `bitbuilder_args.py` | `@register_bitbuilder_args` | `@register_bitbuilder_params` | `target.build.bitbuilder_args` |
| `runner_args.py` | `@register_runner_args` | `@register_runner_params` | `target.run.host.fpga_slot.runner_args` |

`resolve_args_schema(name)` / `resolve_params_schema(name)` turn the registry's
class-name string into the class; `FSLabConfig` re-parses the user dict through
it (`BBA-01`/`RUNA-01`) and `MasterRegistry` validates the registry params
(`BB-12`). All these bases set `extra="forbid"`, so an unknown key is a hard
error — surface new knobs as typed fields. `F2RunnerArgs` is the worked example,
including the payload / result-file axis (`PayloadConfig`, `ResultFileConfig`,
the `VerifyHash` policy, and the `PAY-*` reserved-name checks).

## Host models: a closed discriminated union (`host_model.py`)

Host acquisition (`target.build.host` / `target.run.host`) is a **closed**
union — `Union[ExternalHostConfig, Ec2LaunchHostConfig]` discriminated on
`type`, with a `KNOWN_HOST_MODELS` frozenset that `registry.py` imports to
validate platform `host_models` keys (`HMOD-01`/`BB-08`). It is deliberately
*not* a plugin registry: each variant binds to an fslab-internal provider, so
adding one is a framework change (schema + provider + union + known-set — see
{doc}`extending`). The shared `fpga_slot` sub-block is declared on the base so it
flows through every variant; cross-validation gates it to the run side.

## Where validation belongs — a decision guide

When you add a rule, put it at the narrowest level that can express it:

| The rule depends on… | Put it in… | Example |
|---|---|---|
| one field's format | `@field_validator(mode="before")` | `PROJ-01` name regex |
| several fields of one model | `@model_validator(mode="after")` | `PROJ-07` blackbox needs ports |
| the registry | `FSLabConfig.cross_validate_with_registry` | `PROJ-11` platform exists |
| two registry files merged | `MasterRegistry._cross_validate_*` | `BB-10` bitbuilder exists |
| the filesystem | a dedicated after-validator / `from_validated` | `PROJ-14` source exists |

:::{tip}
Keep filesystem I/O out of the schema layer where you can — the payload checks
in `runner_args.py` deliberately defer path-existence and SHA256SUMS checks to
`RunConfig.from_validated` so the pydantic models stay pure. Pure models are
trivially unit-testable with a dict.
:::

## Conventions for adding to the schema layer

- **Every rule gets a code.** Use the existing families (`PROJ-`, `REG-`,
  `BB-`/`BBA-`, `RUN-`/`RUNA-`, `HMOD-`, `FSLOT-`, `PUB-`, `ARTSRC-`, `AWS-`,
  `PAY-`). Put the code in both the raised message and the docstring — it makes
  failures greppable and the model's docstring a live spec.
- **Regexes live in `utils/regexes.py`**, referenced by name; error text comes
  from `regex_msg(pattern)` so the user sees the actual pattern.
- **`extra="forbid"`** on user-facing config models, so typos surface instead of
  being silently ignored.
- **Touching how a model feeds rendering?** The flattening into the Jinja2
  context happens in `commands/context.py`; see {doc}`templates`. A new field
  is invisible to templates until you thread it through there.
