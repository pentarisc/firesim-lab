"""
fslab/schemas/runner_args.py
============================
Pydantic V2 models for the **runner_args** (user-side) and
**runner_params** (registry-side) blocks. Run-side counterpart to
`bitbuilder_args.py`.

runner_args
    Lives at  target.run.host.fpga_slot.runner_args  in fslab.yaml.
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

Payload-axis validation (F2RunnerArgs)
--------------------------------------
  PAY-02   `remote_name` must be unique within the payloads list.
  PAY-03   `remote_name` must not collide with a framework-reserved
           name. The driver-basename collision check is deferred to
           `RunConfig.from_validated` (the driver basename is project-
           derived and not known at pydantic-validation time).
  PAY-06   `result_files[*].remote_path` must not collide with a
           framework-reserved name.

  Filesystem-touching payload checks (PAY-01 path-exists,
  PAY-04 SHA256SUMS-required-when-YES, PAY-05 manifest covers payloads)
  live in `RunConfig.from_validated` so pydantic stays free of IO.

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

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    """Base for per-runner user-tunable args (target.run.host.fpga_slot.runner_args)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunnerParamsBase(BaseModel):
    """Base for per-runner recipe parameters (registry.runners[].params)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Payload axis — shared types
# ---------------------------------------------------------------------------

# Names the framework writes into the per-slot remote dir during a run.
# Any user-supplied remote_name / remote_path that collides with one of
# these would be silently overwritten or would shadow framework files —
# rejected up front. The driver binary basename is project-derived and
# checked separately in RunConfig.from_validated.
_RESERVED_REMOTE_NAMES: frozenset[str] = frozenset({
    "driver.log",
    "result.yaml",
    "remote_run_f2.sh",
    "SHA256SUMS",
    "pid",
    "run.yaml",
    ".fslab",
})


class VerifyHash(str, Enum):
    """Tri-state policy for `payloads/SHA256SUMS` verification.

    Verification runs in two places: locally before upload and on the
    remote before driver exec. Both honour the same policy.

        YES         Verification required. Missing SHA256SUMS is fatal
                    at config-load time.
        NO          Never verify. SHA256SUMS, if present, is ignored.
        IF_PRESENT  Verify when SHA256SUMS is present; warn-once and
                    skip otherwise. This is the default — least friction
                    while still catching corruption when the manifest is
                    provided.
    """
    YES = "YES"
    NO = "NO"
    IF_PRESENT = "IF_PRESENT"


class PayloadConfig(BaseModel):
    """One payload file uploaded alongside the driver.

    `path` is the local source (project-relative or absolute; resolved
    to absolute in RunConfig.from_validated). `remote_name` is the
    filename inside the per-slot remote dir; defaults to the basename
    of `path`. The driver references it via relative path (e.g.
    `+loadmembin=dhrystone.bin`) since the driver `cd`s into the slot
    dir before exec.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: Path = Field(
        ...,
        description=(
            "Local source path. Project-relative paths resolve against "
            "the project dir at RunConfig construction time; absolute "
            "paths are kept as-is. Existence is checked then, not here."
        ),
    )

    remote_name: Optional[str] = Field(
        None,
        description=(
            "Filename inside the per-slot remote dir. Defaults to "
            "basename(path) when unset. The driver references payloads "
            "via this name, so keep it stable across runs if your "
            "driver flags hard-code it."
        ),
    )

    @model_validator(mode="after")
    def _default_remote_name(self) -> "PayloadConfig":
        if self.remote_name is None or self.remote_name == "":
            object.__setattr__(self, "remote_name", self.path.name)
        return self


class ResultFileConfig(BaseModel):
    """One file produced by the driver and pulled back into the local
    results dir after the run.

    `remote_path` is interpreted relative to the per-slot remote dir
    (where the driver runs). `local_name` is the filename inside
    `run/fpga/results/<ts>/`; defaults to basename(remote_path).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    remote_path: str = Field(
        ...,
        description=(
            "Path of the produced file relative to the per-slot remote "
            "dir. The driver `cd`s into that dir before exec, so a bare "
            "filename is the common case."
        ),
    )

    local_name: Optional[str] = Field(
        None,
        description=(
            "Filename written under `run/fpga/results/<ts>/`. Defaults "
            "to basename(remote_path) when unset."
        ),
    )

    @model_validator(mode="after")
    def _default_local_name(self) -> "ResultFileConfig":
        if self.local_name is None or self.local_name == "":
            object.__setattr__(self, "local_name", Path(self.remote_path).name)
        return self


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

    payloads: list[PayloadConfig] = Field(
        default_factory=list,
        description=(
            "Files staged into the per-slot remote dir alongside the "
            "driver. Addressable from `extra_driver_flags` by "
            "`remote_name` (which defaults to basename(path))."
        ),
    )

    result_files: list[ResultFileConfig] = Field(
        default_factory=list,
        description=(
            "Files produced by the driver to pull back into the local "
            "results dir after the run. Missing files at pull time "
            "produce a warning, not a fatal error."
        ),
    )

    verify_hash: VerifyHash = Field(
        VerifyHash.IF_PRESENT,
        description=(
            "Policy for verifying `payloads/SHA256SUMS` locally before "
            "upload and on the remote before driver exec. Default "
            "`IF_PRESENT` verifies only when the manifest exists."
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

    @model_validator(mode="after")
    def _validate_payload_axis(self) -> "F2RunnerArgs":
        """[PAY-02] / [PAY-03] / [PAY-06] payload + result_file shape checks.

        Filesystem-touching checks (path exists, manifest exists, manifest
        covers payloads) are intentionally deferred to RunConfig.from_validated
        so the schema layer stays IO-free.
        """
        seen: dict[str, int] = {}
        for idx, p in enumerate(self.payloads):
            rn = p.remote_name or p.path.name

            # [PAY-02] unique remote_name across the payloads list.
            if rn in seen:
                raise ValueError(
                    f"[PAY-02] payloads[{idx}].remote_name='{rn}' duplicates "
                    f"payloads[{seen[rn]}]. Each remote_name must be unique."
                )
            seen[rn] = idx

            # [PAY-03] no collision with framework-reserved remote names.
            # (Driver-basename collision is checked in RunConfig.from_validated
            #  since the basename is project-derived.)
            if rn in _RESERVED_REMOTE_NAMES:
                raise ValueError(
                    f"[PAY-03] payloads[{idx}].remote_name='{rn}' collides "
                    f"with a framework-reserved name "
                    f"({sorted(_RESERVED_REMOTE_NAMES)}). Choose a different "
                    f"remote_name."
                )

        for idx, r in enumerate(self.result_files):
            # [PAY-06] result_files remote_path must not collide with reserved
            # names — those are framework outputs (driver.log, result.yaml,
            # etc.) which we generate and pull back unconditionally.
            if r.remote_path in _RESERVED_REMOTE_NAMES:
                raise ValueError(
                    f"[PAY-06] result_files[{idx}].remote_path='{r.remote_path}' "
                    f"collides with a framework-reserved name "
                    f"({sorted(_RESERVED_REMOTE_NAMES)}). Choose a different "
                    f"remote_path; framework artifacts are pulled "
                    f"automatically."
                )

        return self


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
