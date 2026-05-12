"""
fslab/schemas/publish.py
========================
Pydantic V2 models for `target.build.publish` — discriminated union of
post-build artifact handlers.

Discriminator is the `type` field. Closed framework-owned union; same
shape as host_model.py. Adding a new publisher is a framework change.

Currently registered types
--------------------------
  none           No publish step. Build artifacts remain in the local
                 results directory.
  local_tarball  Tar bitstream + metadata into a project-relative subdir
                 and emit a minimal hwdb-style descriptor file.
  aws_afi        F2: S3 upload of DCP, aws ec2 create-fpga-image, optional
                 multi-region AFI replication, optional SNS notification,
                 optional post-build hook.

Validation requirements
-----------------------
  PUB-01  publish.type discriminator must match a registered publisher
  PUB-02  aws_afi.s3_bucket_name must be non-empty and DNS-compliant
  AWS-02  copy_to_regions entries must be valid AWS region codes
  AWS-04  s3_bucket_name must match the S3 naming rules
  AWS-05  sns_topic_arn must be a valid SNS ARN (when set)
  AWS-06  aws_afi.aws_profile matches the named-profile shape (when set)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class PublishConfigBase(BaseModel):
    """Base class for the `target.build.publish` discriminated union.

    Concrete subclasses set `type: Literal[...]` as the discriminator.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# none — no-op
# ---------------------------------------------------------------------------

class NonePublishConfig(PublishConfigBase):
    """No publish step. Build artifacts stay in the local results dir."""
    type: Literal["none"]


# ---------------------------------------------------------------------------
# local_tarball
# ---------------------------------------------------------------------------

class LocalTarballPublishConfig(PublishConfigBase):
    """Tar bitstream + metadata into a project-relative directory and emit
    a minimal hwdb-style descriptor file.

    Used by Alveo / Vitis (when those bitbuilders land) and by F2 builds
    where the user does not want to publish to AWS.
    """

    type: Literal["local_tarball"]

    output_subdir: str = Field(
        "built-artifacts",
        min_length=1,
        description=(
            "Project-relative directory where the tarball + descriptor land. "
            "Created if absent."
        ),
    )
    hwdb_entry_name: Optional[str] = Field(
        None,
        description=(
            "Name used in the hwdb descriptor written alongside the tarball. "
            "Defaults to project.name when omitted."
        ),
    )


# ---------------------------------------------------------------------------
# aws_afi — full F2 feature set
# ---------------------------------------------------------------------------

class AwsAfiPublishConfig(PublishConfigBase):
    """AWS AGFI/AFI publish for F2.

    Mirrors firesim's F2BitBuilder.aws_create_afi() feature set:
      - S3 bucket auto-create + DCP upload
      - aws ec2 create-fpga-image -> AGFI/AFI
      - optional multi-region AFI replication
      - optional SNS topic notification on completion (success/fail)
      - optional post-build hook (script invoked with local results dir)
    """

    type: Literal["aws_afi"]

    s3_bucket_name: str = Field(..., min_length=1)
    """[PUB-02][AWS-04] S3 bucket for DCP upload. Auto-created by the
    publisher if it does not exist."""

    append_userid_region: bool = Field(
        True,
        description=(
            "If true, append '-<aws_userid>-<region>' to the bucket name "
            "(firesim convention). Default true mirrors firesim."
        ),
    )

    aws_profile: Optional[str] = Field(
        None,
        description=(
            "Named AWS profile (~/.aws/config / ~/.aws/credentials) used "
            "when constructing the boto3 session for S3/EC2 publish calls. "
            "Leave null to fall back to the AWS_PROFILE env var or the "
            "[default] profile. Independent of host.aws_profile so the "
            "publish axis stays self-contained — set both to the same "
            "value if you want them aligned."
        ),
    )

    hwdb_entry_name: Optional[str] = Field(
        None,
        description="Name for the generated hwdb entry; defaults to project.name.",
    )

    copy_to_regions: list[str] = Field(
        default_factory=list,
        description="Replicate the AFI to these AWS regions after creation.",
    )

    sns_topic_arn: Optional[str] = Field(
        None,
        description="Optional SNS topic ARN for build-completion notifications.",
    )

    post_build_hook: Optional[str] = Field(
        None,
        description=(
            "Optional path to a script run after a successful publish, "
            "with the local results directory as its first argument."
        ),
    )

    dcp_tar_glob: str = Field(
        "build/checkpoints/*.tar",
        min_length=1,
        description=(
            "Glob (relative to the local results cl_dir) used to locate the "
            "DCP tarball produced by the build. Default matches the F2 "
            "build-bitstream.sh convention. Override only if a custom build "
            "script writes the tar elsewhere."
        ),
    )

    @field_validator("s3_bucket_name", mode="after")
    @classmethod
    def _validate_bucket(cls, v: str) -> str:
        """[AWS-04]"""
        if not rx.S3_BUCKET_NAME_RE.match(v):
            raise ValueError(
                f"[AWS-04] s3_bucket_name '{v}' is invalid. "
                + regex_msg(rx.S3_BUCKET_NAME_RE)
            )
        return v

    @field_validator("copy_to_regions", mode="after")
    @classmethod
    def _validate_regions(cls, v: list[str]) -> list[str]:
        """[AWS-02]"""
        for r in v:
            if not rx.AWS_REGION_RE.match(r):
                raise ValueError(
                    f"[AWS-02] copy_to_regions entry '{r}' is invalid. "
                    + regex_msg(rx.AWS_REGION_RE)
                )
        return v

    @field_validator("sns_topic_arn", mode="after")
    @classmethod
    def _validate_sns_arn(cls, v: Optional[str]) -> Optional[str]:
        """[AWS-05]"""
        if v is None:
            return v
        if not rx.SNS_ARN_RE.match(v):
            raise ValueError(
                f"[AWS-05] sns_topic_arn '{v}' is invalid. "
                + regex_msg(rx.SNS_ARN_RE)
            )
        return v

    @field_validator("aws_profile", mode="before")
    @classmethod
    def _empty_profile_to_none(cls, v: Any) -> Any:
        """An empty/whitespace aws_profile falls back to env / default."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("aws_profile", mode="after")
    @classmethod
    def _validate_profile(cls, v: Optional[str]) -> Optional[str]:
        """[AWS-06]"""
        if v is None:
            return v
        if not rx.AWS_PROFILE_RE.match(v):
            raise ValueError(
                f"[AWS-06] aws_profile '{v}' is invalid. "
                + regex_msg(rx.AWS_PROFILE_RE)
            )
        return v


# ---------------------------------------------------------------------------
# Discriminated union + known-types set
# ---------------------------------------------------------------------------

PublishConfig = Annotated[
    Union[NonePublishConfig, LocalTarballPublishConfig, AwsAfiPublishConfig],
    Field(discriminator="type"),
]
"""Public union type used by TargetBuildConfig.publish."""


KNOWN_PUBLISH_TYPES: frozenset[str] = frozenset({"none", "local_tarball", "aws_afi"})
"""[PUB-01] Set of registered discriminator values. Imported by registry.py
to validate `platforms[].publish` keys at registry-load time. Update this
when adding a new publisher class above."""
