"""
fslab/schemas/runner_args.py
============================
Pydantic V2 models for the **runner_args** (user-side) and
**runner_params** (registry-side) blocks. Run-side counterpart to
`bitbuilder_args.py`.

runner_args
    Lives at  target.run.runner_args  in fslab.yaml.
    Schema selected at parse time by  registry.runners[<id>].args_schema.

runner_params
    Lives at  registry.runners[<id>].params (per-runner) in registry.yaml.
    Schema selected by  registry.runners[<id>].params_schema. Reserved for
    future runner variants that share a class but differ in some static
    per-recipe fact.

Resolution flow (cross-validation in FSLabConfig)
-------------------------------------------------
  1. Read  target.platform = "<p>"  from validated FSLabConfig.
  2. Look up  registry.platforms[<p>].runner = "<r_id>".
  3. Look up  registry.runners[<r_id>].args_schema = "F2RunnerArgs".
  4. Resolve via  RUNNER_ARGS_REGISTRY["F2RunnerArgs"].
  5. Re-parse the user-supplied dict through the resolved class.

The discriminator is *external* (platform→runner lookup), not an internal
`type:` field, so this axis uses a name-keyed registry rather than a
discriminated union. Same pattern as bitbuilder_args.py.

Validation requirements
-----------------------
  RUNA-01  args_schema name from registry must be present in
           RUNNER_ARGS_REGISTRY
  RUNA-02  user's runner_args block must validate against the resolved
           class (errors surfaced verbatim)
  RUNA-03  params_schema name from registry must be present in
           RUNNER_PARAMS_REGISTRY
  RUNA-04  registry's runner params block must validate against the
           resolved params class

Adding a new runner
-------------------
    @register_runner_args
    class MyRunnerArgs(RunnerArgsBase):
        my_field: str

    @register_runner_params
    class MyRunnerParams(RunnerParamsBase):
        ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Registries (decorator-populated, queried by string class name)
# ---------------------------------------------------------------------------

RUNNER_ARGS_REGISTRY: dict[str, type["RunnerArgsBase"]] = {}
RUNNER_PARAMS_REGISTRY: dict[str, type["RunnerParamsBase"]] = {}


def register_runner_args(cls: type["RunnerArgsBase"]) -> type["RunnerArgsBase"]:
    """Register a per-runner *user args* schema by its class name."""
    RUNNER_ARGS_REGISTRY[cls.__name__] = cls
    return cls


def register_runner_params(cls: type["RunnerParamsBase"]) -> type["RunnerParamsBase"]:
    """Register a per-runner *recipe params* schema by its class name."""
    RUNNER_PARAMS_REGISTRY[cls.__name__] = cls
    return cls


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------

class RunnerArgsBase(BaseModel):
    """Base for per-runner user-tunable args (target.run.runner_args)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunnerParamsBase(BaseModel):
    """Base for per-runner recipe parameters (registry.runners[].params)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Built-in runners — F2
# ---------------------------------------------------------------------------

@register_runner_args
class F2RunnerArgs(RunnerArgsBase):
    """User-tunable args for the F2 runner.

    Minimal initial set — enough to drive an F2 simulation end-to-end.
    Per-feature knobs (tracerv ports, autocounter readrate, +verbose levels,
    blkdev passthrough) will be added on demand once the run pipeline
    starts exercising them.
    """

    max_cycles: Optional[int] = Field(
        None,
        gt=0,
        description=(
            "Cap the simulation at this many target cycles before the "
            "driver exits. None lets the workload run to its natural "
            "termination (target `poweroff`, driver-side fatal error, etc.)."
        ),
    )

    tracing: bool = Field(
        False,
        description=(
            "Enable TracerV output. When true, the runner pulls "
            "trace files back into the run results directory. Per-port "
            "selection and start/end cycle gating are deferred until "
            "the run pipeline lands and we know which knobs users hit."
        ),
    )

    autocounter: bool = Field(
        False,
        description=(
            "Enable autocounter CSV emission. When true, the runner pulls "
            "autocounter files back into the run results directory."
        ),
    )

    workload_bin: Optional[Path] = Field(
        None,
        description=(
            "Path to the target workload binary (e.g. a RISC-V ELF). "
            "Resolved relative to the project dir if not absolute. "
            "Some workloads embed everything in the bitstream and need "
            "no separate binary — leave None in that case."
        ),
    )

    extra_driver_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Verbatim +plusarg / -- flags appended to the driver invocation. "
            "Escape hatch for one-off knobs not yet covered by a typed field. "
            "Each entry is passed through as-is — no validation."
        ),
    )


@register_runner_params
class F2RunnerParams(RunnerParamsBase):
    """Per-recipe parameters for the F2 runner.

    Currently empty. There is one F2 runner today and it needs no params.
    Reserved for future F2 variants that share the F2Runner recipe but
    differ in some platform-static fact.
    """
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_args_schema(name: str) -> type[RunnerArgsBase]:
    """[RUNA-01] Resolve an args_schema class-name string to the registered class.

    Raises ValueError when the name is not registered.
    """
    cls = RUNNER_ARGS_REGISTRY.get(name)
    if cls is None:
        known = sorted(RUNNER_ARGS_REGISTRY.keys())
        raise ValueError(
            f"[RUNA-01] args_schema '{name}' is not registered. Known: {known}"
        )
    return cls


def resolve_params_schema(name: str) -> type[RunnerParamsBase]:
    """[RUNA-03] Resolve a params_schema class-name string to the registered class.

    Raises ValueError when the name is not registered.
    """
    cls = RUNNER_PARAMS_REGISTRY.get(name)
    if cls is None:
        known = sorted(RUNNER_PARAMS_REGISTRY.keys())
        raise ValueError(
            f"[RUNA-03] params_schema '{name}' is not registered. Known: {known}"
        )
    return cls
