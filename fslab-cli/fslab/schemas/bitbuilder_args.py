"""
fslab/schemas/bitbuilder_args.py
================================
Pydantic V2 models for the **bitbuilder_args** (user-side) and
**bitbuilder_params** (registry-side) blocks.

bitbuilder_args
    Lives at  target.build.bitbuilder_args  in fslab.yaml.
    Schema selected at parse time by  registry.bitbuilders[<id>].args_schema.

bitbuilder_params
    Lives at  platforms[<id>].bitbuilder_params  in registry.yaml.
    Schema selected by  registry.bitbuilders[<id>].params_schema.

Resolution flow (cross-validation in FSLabConfig)
-------------------------------------------------
  1. Read  target.platform = "<p>"  from validated FSLabConfig.
  2. Look up  registry.platforms[<p>].bitbuilder = "<bb_id>".
  3. Look up  registry.bitbuilders[<bb_id>].args_schema = "F2BitbuilderArgs".
  4. Resolve via  BITBUILDER_ARGS_REGISTRY["F2BitbuilderArgs"].
  5. Re-parse the user-supplied dict through the resolved class.

The discriminator is *external* (platform→bitbuilder lookup), not an internal
`type:` field, so this axis uses a name-keyed registry rather than a
discriminated union. Compare resolvers.BRIDGE_CFG_REGISTRY which uses
discriminator-based dispatch via dynamic Union.

Validation requirements
-----------------------
  BBA-01  args_schema name from registry must be present in
          BITBUILDER_ARGS_REGISTRY
  BBA-02  user's bitbuilder_args block must validate against the resolved
          class (errors surfaced verbatim)
  BBA-03  params_schema name from registry must be present in
          BITBUILDER_PARAMS_REGISTRY
  BBA-04  registry's bitbuilder_params block must validate against the
          resolved params class

Adding a new bitbuilder
-----------------------
    @register_bitbuilder_args
    class MyBitbuilderArgs(BitbuilderArgsBase):
        my_field: str

    @register_bitbuilder_params
    class MyBitbuilderParams(BitbuilderParamsBase):
        board_name: str
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Registries (decorator-populated, queried by string class name)
# ---------------------------------------------------------------------------

BITBUILDER_ARGS_REGISTRY: dict[str, type["BitbuilderArgsBase"]] = {}
BITBUILDER_PARAMS_REGISTRY: dict[str, type["BitbuilderParamsBase"]] = {}


def register_bitbuilder_args(cls: type["BitbuilderArgsBase"]) -> type["BitbuilderArgsBase"]:
    """Register a per-bitbuilder *user args* schema by its class name."""
    BITBUILDER_ARGS_REGISTRY[cls.__name__] = cls
    return cls


def register_bitbuilder_params(cls: type["BitbuilderParamsBase"]) -> type["BitbuilderParamsBase"]:
    """Register a per-bitbuilder *platform params* schema by its class name."""
    BITBUILDER_PARAMS_REGISTRY[cls.__name__] = cls
    return cls


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------

class BitbuilderArgsBase(BaseModel):
    """Base for per-bitbuilder user-tunable args (target.build.bitbuilder_args)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BitbuilderParamsBase(BaseModel):
    """Base for per-platform bitbuilder parameters (platforms[].bitbuilder_params)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Built-in bitbuilders — F2
# ---------------------------------------------------------------------------

@register_bitbuilder_args
class F2BitbuilderArgs(BitbuilderArgsBase):
    """User-tunable args for the F2 bitbuilder.

    F2's other user-facing knobs (S3 bucket, AGFI replication, SNS) live
    under target.build.publish (aws_afi). EC2-launch knobs live under
    target.build.host (ec2_launch). This class holds bitbuilder-internal
    tunables that don't fit the host or publish axes.

    place/phy_opt/route are optional and passed through as-is to
    `aws_build_dcp_from_cl.py` (via the vendored `build-bitstream.sh`
    patch, docker/patches/f2-build-bitstream.sh) — omit any of them to
    fall back to that script's own built-in default rather than
    duplicating its defaults here.
    """
    place: Optional[str] = Field(
        None,
        description=(
            "Vivado place directive (aws_build_dcp_from_cl.py --place). "
            "Omit to use its built-in default (SSI_SpreadLogic_high)."
        ),
    )
    phy_opt: Optional[str] = Field(
        None,
        description=(
            "Vivado physical-optimization directive (--phy_opt). Omit to "
            "use its built-in default (AggressiveExplore)."
        ),
    )
    route: Optional[str] = Field(
        None,
        description=(
            "Vivado route directive (--route). Omit to use its built-in "
            "default (AggressiveExplore)."
        ),
    )
    extra_args: str = Field(
        "",
        description=(
            "Verbatim extra flags appended as-is to the "
            "aws_build_dcp_from_cl.py invocation (e.g. --clock_recipe_a/b/c, "
            "--tag, --no-encrypt, --mode, --flow)."
        ),
    )


@register_bitbuilder_params
class F2BitbuilderParams(BitbuilderParamsBase):
    """Per-platform parameters for the F2 bitbuilder.

    Currently empty. There is one F2 platform and it needs no params.
    Reserved for future F2 variants that share the F2BitBuilder recipe but
    differ in some platform-static fact.
    """
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_args_schema(name: str) -> type[BitbuilderArgsBase]:
    """[BBA-01] Resolve an args_schema class-name string to the registered class.

    Raises ValueError when the name is not registered.
    """
    cls = BITBUILDER_ARGS_REGISTRY.get(name)
    if cls is None:
        known = sorted(BITBUILDER_ARGS_REGISTRY.keys())
        raise ValueError(
            f"[BBA-01] args_schema '{name}' is not registered. Known: {known}"
        )
    return cls


def resolve_params_schema(name: str) -> type[BitbuilderParamsBase]:
    """[BBA-03] Resolve a params_schema class-name string to the registered class.

    Raises ValueError when the name is not registered.
    """
    cls = BITBUILDER_PARAMS_REGISTRY.get(name)
    if cls is None:
        known = sorted(BITBUILDER_PARAMS_REGISTRY.keys())
        raise ValueError(
            f"[BBA-03] params_schema '{name}' is not registered. Known: {known}"
        )
    return cls
