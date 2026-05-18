"""
fslab/schemas/artifact_source.py
================================
Pydantic V2 models for `target.run.artifact_source` — the run-side
counterpart to `target.build.publish`. Tells the runner *where* the
bitstream to run came from.

Today the only registered type is `aws_afi` (AGFI by id, fetched on
the run host via `fpga-load-local-image`). Two deferred types are
documented but not implemented:

  * `local_tarball`  — DCP tarball + driver tarball uploaded from local.
                       Paired with the build-side `local_tarball`
                       publisher; lands when both are concrete.
  * `hwdb_entry`     — by-name lookup once a hwdb registry exists.

Discriminator is the `type` field. Closed framework-owned union; same
shape as publish.py.

Validation requirements
-----------------------
  ARTSRC-01  target.run.artifact_source.type must be in
             platform.run_artifact_sources (cross-checked in
             FSLabConfig.cross_validate_with_registry)
  AWS-08     aws_afi.agfi must match the AGFI format
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class ArtifactSourceConfigBase(BaseModel):
    """Base class for the `target.run.artifact_source` discriminated union.

    Concrete subclasses set `type: Literal[...]` as the discriminator.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# aws_afi — F2 AGFI by id
# ---------------------------------------------------------------------------

class AwsAfiArtifactSourceConfig(ArtifactSourceConfigBase):
    """Resolve the run-side bitstream from an AWS AGFI id.

    The AGFI is loaded on the run host with `sudo fpga-load-local-image
    -S <slot> -I <agfi> -A`, which uses the instance profile attached to
    the EC2 instance to call DescribeFpgaImages / AssociateFpgaImage.
    """

    type: Literal["aws_afi"]

    agfi: str = Field(
        ...,
        description=(
            "AGFI id (Amazon FPGA Global Image). 20-char form: "
            "'agfi-' + 17 lowercase hex. Cross-region replication is "
            "the publisher's responsibility — set host.region to a "
            "region the AFI was replicated to."
        ),
    )

    @field_validator("agfi", mode="after")
    @classmethod
    def _validate_agfi(cls, v: str) -> str:
        """[AWS-08]"""
        if not rx.AGFI_RE.match(v):
            raise ValueError(
                f"[AWS-08] agfi '{v}' is invalid. " + regex_msg(rx.AGFI_RE)
            )
        return v


# ---------------------------------------------------------------------------
# Discriminated union + known-types set
# ---------------------------------------------------------------------------

# Currently a single-arm "union". When `local_tarball` and `hwdb_entry`
# land, switch to Annotated[Union[AwsAfi…, LocalTarball…, …],
# Field(discriminator="type")] in the publish.py style. The
# KNOWN_ARTIFACT_SOURCE_TYPES set drives the [ARTSRC-01] cross-check
# against PlatformEntry.run_artifact_sources.
ArtifactSourceConfig = AwsAfiArtifactSourceConfig
"""Public type used by TargetRunConfig.artifact_source."""


KNOWN_ARTIFACT_SOURCE_TYPES: frozenset[str] = frozenset({"aws_afi"})
"""[ARTSRC-01] Set of registered discriminator values. Imported by
registry.py to validate `platforms[].run_artifact_sources` keys at
registry-load time. Update this when adding a new artifact-source class."""
