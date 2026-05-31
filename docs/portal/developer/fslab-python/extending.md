# Extending the CLI

This page is a set of worked recipes for the most common ways to extend
`fslab`. Each recipe names the files you touch, gives the minimal code, and ties
the result to the validation codes that enforce it. Read
{doc}`index` first for the architecture; read {doc}`schemas` and
{doc}`orchestration` for the layers the later recipes touch.

Two extension styles run through all of these:

- **In-tree** — you are working on firesim-lab itself. You edit the framework
  modules directly and the new behaviour ships with the image.
- **Plugin** — you are a third party who wants to register types without
  forking the repo. The two-pass parser can load a user Python module named
  from a `custom_registries[].plugin` entry in `fslab.yaml`. Importing that
  module runs its `@register_*` decorators, so any registry-based extension
  (bridge configs, bitbuilder/runner schemas) can be added from outside the
  tree. Plugins are gated: the parser refuses to load one unless
  `ENABLE_CUSTOM_PLUGINS=1` is set, because executing project-supplied Python is
  a trust decision.

Recipes 1 and 4 are framework changes by nature (a subcommand and a closed
union); recipes 2 and 3 work either in-tree or as a plugin.

## Recipe 1 — Add a subcommand (e.g. `fslab debug`)

A subcommand group is a self-contained module under `commands/` that creates
its own `typer.Typer()` router and is mounted in `cli.py`. Business logic lives
in the module; `cli.py` only wires it up.

**1. Create `fslab/commands/debug.py`:**

```python
from __future__ import annotations

from pathlib import Path

import typer

from fslab.utils.display import section, success, error
from fslab.schemas.parser import load_and_validate

app = typer.Typer(rich_markup_mode="rich")
debug_app = typer.Typer()
app.add_typer(debug_app, name="debug", help="Inspect a project's resolved config.")

_YamlPathOpt = typer.Option(Path("fslab.yaml"), "--config", "-c",
                            help="Path to the project YAML.")


@debug_app.command("config")
def cmd_debug_config(yaml_path: Path = _YamlPathOpt) -> None:
    """Print the validated config + resolved registry for this project."""
    section("fslab debug config")
    try:
        config, registry = load_and_validate(str(yaml_path.resolve()))
    except Exception as exc:  # noqa: BLE001
        error(f"Configuration error:\n  {exc}")
        raise typer.Exit(code=1) from exc

    success(f"Project: {config.project.name}  platform: {config.target.platform}")
    success(f"Bridges: {[b.name for b in config.bridges]}")
```

**2. Register it in `fslab/cli.py`**, alongside the other `app.add_typer` calls:

```python
from fslab.commands.debug import app as debug_top_app  # noqa: E402
app.add_typer(debug_top_app)
```

That is the whole wiring. Conventions to follow so your command matches the
rest of the CLI:

- Load config through `load_and_validate` and translate any exception into a
  styled `error(...)` + `raise typer.Exit(code=1)`. Never let a raw traceback
  reach the user.
- Run external tools through `fslab.utils.shell.run_or_die` (fatal) or `run`
  (when you inspect the exit code yourself).
- Use the shared `fslab.utils.display` helpers — never a fresh `Console()`.
- If your command produces remote work, build on the {doc}`orchestration` host
  + stamp + monitor primitives rather than opening your own SSH session. A
  `trace` flow, for example, is usually a thin wrapper over the run pipeline
  that sets extra `runner_args` and declares extra `result_files`.

## Recipe 2 — Register a new bridge

A bridge is the bigger change because it spans four artifacts: a Python config
class, a registry entry, the Scala wiring templates, and (usually) a C++ model.
The Python config class is what makes the bridge selectable in `fslab.yaml`.

**1. Python config — `fslab/schemas/resolvers.py` (or a plugin module):**

```python
@register_bridge_cfg
class GpioBridgeConfig(BridgeConfig):
    type: Literal['gpio']
    # Override resolve_refs() only if the bridge needs derived params,
    # the way BlockdevBridgeConfig computes tag_bits from n_trackers.
```

`@register_bridge_cfg` appends the class to `BRIDGE_CFG_REGISTRY`. At parse time
`_get_live_config_model()` folds every registered class into a discriminated
`Union` keyed on `type`, so a `bridges:` entry with `type: gpio` now validates
against `GpioBridgeConfig`. The base `BridgeConfig` already enforces the bridge
`name` format (`PROJ-06`) and the `value`/`ref` parameter shape.

**2. Registry entry — `lib/registry.yaml`** under `bridges:` (a `BridgeEntry`,
see {doc}`schemas`). Required fields include `id`, `label`, `description`,
`origin` (`firesim` / `fslab` / `custom`), `input_ports`, `output_ports`,
`cpp_type`, `cpp_headers`, `cpp_sources`, `cpp_template`, and a
`scala_templates` block (`ports` + `wiring` required, `dut_imports` /
`top_imports` optional). Port names are validated as Verilog identifiers and
must be unique (`REG-08`). The `id` here is the string the user puts in
`type:` and must match your `Literal` above.

**3. Scala templates — `fslab/templates/bridges/gpio/`** with at least
`ports.scala.j2` and `wiring.scala.j2` (and `top_imports.scala.j2` if needed),
mirroring the `uart/` and `iceblk/` directories. These are included per-instance
by `Top.scala.j2` / `DUT.scala.j2`. Inside them you have the per-instance
`BridgeInstance` namespace — `instance.name`, `instance.port_map`,
`instance.params` — built in `commands/context.py`.

**4. C++ model — under `lib/bridges/`**, referenced by `cpp_type` /
`cpp_sources` / `cpp_headers` / `cpp_template`. The driver template iterates the
unique bridge types to emit `get_bridges<cpp_type>()` calls.

Validation that now applies for free: `PROJ-12` (bridge `type` must exist in the
registry), `PROJ-13` (every `port_map` key must be in the bridge's
`input_ports`/`output_ports` in the correct direction, and every value must be a
declared blackbox port), and any `required_params` the registry entry lists.

See {doc}`/developer/bridges/index` for the bridge model details and
{doc}`/developer/jinja-templates` for the template catalogue.

## Recipe 3 — Add a bitbuilder or runner tunable

The build and run pipelines each have two parallel schema axes resolved by
*class name* (not by a `type:` discriminator): a **user-args** schema for the
`fslab.yaml` block and a **params** schema for the registry block. Both use
decorator-populated, name-keyed registries.

For a **bitbuilder** (`fslab/schemas/bitbuilder_args.py`):

```python
@register_bitbuilder_args
class MyBoardBitbuilderArgs(BitbuilderArgsBase):
    overclock_pct: int = 0          # validates target.build.bitbuilder_args

@register_bitbuilder_params
class MyBoardBitbuilderParams(BitbuilderParamsBase):
    board_name: str                 # validates platforms[].bitbuilder_params
```

Then in `lib/registry.yaml`, the `bitbuilders:` entry names them by class
string:

```yaml
bitbuilders:
  - id: my_board
    python_class: MyBoardBitBuilder    # resolved in fslab.bitstream.bitbuilder
    args_schema: MyBoardBitbuilderArgs
    params_schema: MyBoardBitbuilderParams
    build_script_basename: build-bitstream.sh
```

Runners follow the identical pattern in `fslab/schemas/runner_args.py`
(`@register_runner_args` / `@register_runner_params`,
`RUNNER_ARGS_REGISTRY`). The run-side user args live at
`target.run.host.fpga_slot.runner_args`; `F2RunnerArgs` is the worked example,
including the payload / result-file axis.

What the registries buy you: at registry-merge time `MasterRegistry`
cross-checks that every `args_schema` / `params_schema` string resolves
(`BB-11` / `RUN-11`) and that each platform's `bitbuilder_params` validates
against the resolved params class (`BB-12`). At project-parse time
`FSLabConfig` re-parses the user's `bitbuilder_args` / `runner_args` through the
resolved class (`BBA-01` / `RUNA-01`). All schemas use `extra="forbid"`, so an
unknown key in the user's block is a hard error — surface new tunables as typed
fields, not free-form dicts.

:::{tip}
The `args_schema` / `params_schema` names are bare class-name strings validated
as CamelCase (`BB-02` / `RUN-02`). Keep the class name and the registry string
identical — the registry decorator keys on `cls.__name__`.
:::

## Recipe 4 — Add a host model

Host acquisition is a **closed** discriminated union, not a plugin registry —
adding one is intentionally a framework change, because each variant is bound to
an fslab-internal provider implementation. There are four edits, all in-tree:

**1. The schema — `fslab/schemas/host_model.py`:**

```python
class K8sHostConfig(HostModelConfigBase):
    type: Literal["k8s"]
    namespace: str = Field(..., min_length=1)
    # fpga_slot is inherited from HostModelConfigBase.
```

**2. Add it to the union and the known-set, same file:**

```python
HostModelConfig = Annotated[
    Union[ExternalHostConfig, Ec2LaunchHostConfig, K8sHostConfig],
    Field(discriminator="type"),
]

KNOWN_HOST_MODELS: frozenset[str] = frozenset({"external", "ec2_launch", "k8s"})
```

`KNOWN_HOST_MODELS` is imported by `registry.py` to validate the
`platforms[].host_models` keys (`BB-08`). A platform only offers a host model if
its registry entry lists that key, and the per-key default dict is merged into
the user's `host:` block at parse time (`_merge_target_defaults` in
`schemas/parser.py`).

**3. The provider — `fslab/pipeline/host.py`:** implement a `HostProvider`
subclass that acquires, connects, and releases the host, and register it in
`PROVIDER_REGISTRY` so the build/run pipelines can map `host.type` to it.

**4. Wire it into the build/run provider factories** in `bitstream/buildhost.py`
and (if it supports runs) the runtime equivalent.

Cross-validation already in place once the union knows your `type`: `HMOD-05`
(build host type must be a platform-supported host model), `HMOD-06` (same on
the run side), `FSLOT-02` / `FSLOT-03` (build hosts must omit `fpga_slot`, run
hosts must carry it). Add field-level validators (regex, ranges) in your config
class the way `Ec2LaunchHostConfig` does for AWS ids and regions.

## Checklist before you ship an extension

- New rule? Give it a code and put that code in both the error message and the
  model docstring (the codebase convention — makes failures greppable).
- New user-facing config key? It is `extra="forbid"` territory — add it as a
  typed field on the right schema, never a loose dict.
- New subprocess? Route it through `utils.shell` and write its log under
  `.fslab/logs/` via `StateManager.log_file()`.
- Touches generation? Update both the template under `templates/` **and** the
  context builder in `commands/context.py`, and remember the config-hash gate —
  test with `fslab generate --force` while iterating.
- Touches a remote job? Reuse the {doc}`orchestration` stamp + monitor +
  abandon model so your feature is detachable and cleanly abandonable.
