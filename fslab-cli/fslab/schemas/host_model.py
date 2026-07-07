"""
fslab/schemas/host_model.py
===========================
Pydantic V2 models for `target.build.host` / `target.run.host` —
discriminated union of host-acquisition strategies, plus the per-FPGA
slot configuration that hangs off the run side.

Discriminator is the `type` field. The union is a *closed* set of
framework-owned subclasses; the discriminated-union dispatch is wired in
at import time, so adding a new host model is a framework change (add a
class, append to KNOWN_HOST_MODELS, extend HostModelConfig).

This is intentionally simpler than the bridges plugin pattern in
resolvers.py: bridges accept third-party IP descriptors, but host
acquisition strategies are tied to fslab-internal provider implementations.

Currently registered types
--------------------------
  external      Pre-provisioned host reachable via SSH. Implementation:
                ExternalHost in fslab.pipeline.host.
  ec2_launch    Framework-managed EC2 build host. Two sub-modes selected
                by the presence of `instance_id`:

                  * instance_id unset → ephemeral. Provider launches a
                    fresh instance per build and terminates on release.
                  * instance_id set   → managed reuse. Provider starts the
                    named instance if stopped, uses it, then stops it on
                    release. If found running, connection only — no
                    state change on release (preserves user-controlled
                    instances that another process is already using).

FPGA slot
---------
`fpga_slot` is an optional sub-block carried on every host variant
(declared on `HostModelConfigBase` so all concrete subclasses inherit
it). It is meaningful only on the run side: a build host compiles a
bitstream but never loads one onto an FPGA. Cross-validation in
`FSLabConfig.cross_validate_with_registry` enforces:

  * `target.build.host.fpga_slot` must be absent  [FSLOT-02]
  * `target.run.host.fpga_slot`   must be present [FSLOT-03]

The block is single-instance today (one host, one slot, id 0); the
nesting under `host` is the forward-compatible scaffold for multi-host
multi-slot, which will refactor to `target.run.hosts: [{ ..., slots:
[...] }]` without restructuring the inner shape.

Validation requirements
-----------------------
  HMOD-01  type discriminator must match a registered host model
  HMOD-02  ssh_key whitespace-only treated as None (BHOST-01 reuse)
  HMOD-03  external.host must not contain '@' or '://' (BHOST-02 reuse)
  HMOD-04  remote_platform_path must be Unix-absolute when set
  HMOD-06  ec2_launch.lifecycle ∈ {spot_one_time, on_demand}
  HMOD-07  ec2_launch.iam_instance_profile is required and non-empty
  AWS-01   ec2_launch.ami_id matches `ami-XXXX...` format (when set)
  AWS-02   ec2_launch.region is a valid AWS region code
  AWS-03   ec2_launch.instance_type matches AWS naming (when set)
  AWS-06   ec2_launch.aws_profile matches the named-profile shape (when set)
  AWS-07   ec2_launch.instance_id matches `i-XXXX...` format (when set)
  FSLOT-01 fpga_slot.id must be a non-negative integer (today: 0)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import fslab.utils.regexes as rx
from fslab.utils.display import regex_msg


# ---------------------------------------------------------------------------
# FpgaSlotConfig — per-FPGA-slot configuration carried on run hosts
# ---------------------------------------------------------------------------

class FpgaSlotConfig(BaseModel):
    """Per-FPGA-slot block under `target.run.host.fpga_slot:`.

    Today's framework is single-host, single-slot (id 0); the nesting
    inside `host:` is the forward-compatible scaffold for multi-host /
    multi-slot — `target.run.hosts: [{ ..., slots: [...] }]` is the
    eventual shape, but the inner per-slot fields (runner_args) stay
    the same.

    Lives here rather than under schemas/project.py so the host union
    can import it without a circular dependency.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(
        ...,
        description=(
            "Slot identifier within the host. Single-slot today, so "
            "must be 0. Multi-slot support will relax this to any "
            "non-negative integer."
        ),
    )

    runner_args: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-runner user-tunable args. The pydantic schema for this "
            "block is selected via the platform's runner.args_schema and "
            "validated cross-field in FSLabConfig.cross_validate_with_registry "
            "[RUNA-01]."
        ),
    )

    @field_validator("id", mode="after")
    @classmethod
    def _validate_slot_id(cls, v: int) -> int:
        """[FSLOT-01] fpga_slot.id must be non-negative and (today) must be 0."""
        if v < 0:
            raise ValueError(
                f"[FSLOT-01] target.run.host.fpga_slot.id must be a "
                f"non-negative integer; got {v}."
            )
        if v != 0:
            raise ValueError(
                f"[FSLOT-01] target.run.host.fpga_slot.id must be 0 "
                f"(multi-slot is not yet supported); got {v}."
            )
        return v


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class HostModelConfigBase(BaseModel):
    """Base class for the `target.build.host` / `target.run.host`
    discriminated union.

    All concrete subclasses set `type: Literal[...]` as the discriminator.
    `fpga_slot` is declared here so it transparently flows through every
    host variant; cross-validation in FSLabConfig gates its presence on
    the run side and its absence on the build side.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fpga_slot: Optional[FpgaSlotConfig] = Field(
        None,
        description=(
            "Per-FPGA-slot configuration. Only valid under "
            "`target.run.host` — must be omitted under `target.build.host` "
            "[FSLOT-02], must be present under `target.run.host` "
            "[FSLOT-03]."
        ),
    )


# ---------------------------------------------------------------------------
# external
# ---------------------------------------------------------------------------

class ExternalHostConfig(HostModelConfigBase):
    """Pre-provisioned SSH-reachable build host.

    Replaces the previous BuildHostConfig (BHOST-XX rules preserved as
    HMOD-XX).  `remote_platform_path` is required on the user side because
    it varies per lab/install — the framework registry deliberately leaves
    no default for `external` (see registry.yaml `host_models.external: {}`).
    """

    type: Literal["external"]

    host: str = Field(..., min_length=1, description="IP or hostname.")
    user: str = Field(..., min_length=1, description="SSH username.")
    ssh_key: Optional[str] = Field(
        None,
        description=(
            "Path to SSH private key (supports `~`). "
            "Leave null/omit to fall back to ssh-agent or ~/.ssh/config."
        ),
    )
    remote_platform_path: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute path to the platform HDK on the remote host. The "
            "framework registry does NOT default this for `external` "
            "because layout varies per lab/install."
        ),
    )

    @field_validator("ssh_key", mode="before")
    @classmethod
    def _empty_ssh_key_to_none(cls, v: Any) -> Any:
        """[HMOD-02] An empty/whitespace ssh_key falls back to ssh-agent."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _validate_host_shape(self) -> "ExternalHostConfig":
        """[HMOD-03] Catch the common 'user@host' mistake and pasted URLs."""
        if "@" in self.host:
            raise ValueError(
                f"[HMOD-03] host '{self.host}' contains '@'. "
                f"Specify the SSH user via .user instead."
            )
        if "://" in self.host:
            raise ValueError(
                f"[HMOD-03] host '{self.host}' looks like a URL. "
                f"Use only the hostname or IP."
            )
        return self

    @field_validator("remote_platform_path", mode="after")
    @classmethod
    def _absolute_remote_path(cls, v: str) -> str:
        """[HMOD-04] Remote paths must be Unix-absolute."""
        if not v.startswith("/"):
            raise ValueError(
                f"[HMOD-04] remote_platform_path must be an absolute Unix path, "
                f"got: {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# ec2_launch
# ---------------------------------------------------------------------------

# Allowed values for the ephemeral-launch lifecycle. `spot_persistent` was
# considered but cut from scope: the only value it adds is multi-build
# reuse via stop/start, which the `instance_id` opt-in covers more
# explicitly. Keep the enum narrow; expand if a real need surfaces.
LIFECYCLE_VALUES = ("spot_one_time", "on_demand")


class Ec2LaunchHostConfig(HostModelConfigBase):
    """Framework-managed EC2 build host.

    Two sub-modes, selected by `instance_id`:

      * `instance_id` unset → **ephemeral**. Provider runs `RunInstances`
        with the lifecycle market options below, waits for SSH, builds, then
        `TerminateInstances` on release.

      * `instance_id` set   → **managed reuse**. Provider looks up the named
        instance and starts it if `stopped`. On release it stops the
        instance (only if it started it). If the instance is found already
        `running`, the provider connects without changing state and leaves
        it running on release — preserving instances another process or
        user has up.

    Defaults for ephemeral-mode fields (`instance_type`, `ami_id`,
    `aws_fpga_version`, `remote_platform_path`) come from the platform's
    registry entry (`platforms.<id>.host_models.ec2_launch`); the parser
    merges those defaults into the user dict before pydantic validation.
    The user only needs to supply fields they wish to override.

    Required-ness of ephemeral-mode fields is enforced at request-time by
    the provider rather than pydantic, because the registry merge step
    populates them and not every user-supplied YAML will carry them
    explicitly.
    """

    type: Literal["ec2_launch"]

    # --- auth + region (always relevant) ----------------------------------

    region: str = Field(..., min_length=1, description="AWS region (e.g. us-west-2).")

    aws_profile: Optional[str] = Field(
        None,
        description=(
            "Named AWS profile (~/.aws/config / ~/.aws/credentials) used "
            "when constructing the boto3 session for EC2 lifecycle calls. "
            "Leave null to fall back to the AWS_PROFILE env var or the "
            "[default] profile."
        ),
    )

    remote_platform_path: Optional[str] = Field(
        None,
        description="Absolute path to the platform HDK inside the instance.",
    )

    # --- managed-reuse mode (set to opt in) -------------------------------

    instance_id: Optional[str] = Field(
        None,
        description=(
            "If set, the provider operates in managed-reuse mode: looks up "
            "this instance, starts it if stopped, stops it on release. "
            "Mutually exclusive with the ephemeral-launch fields below "
            "(they are ignored when instance_id is set)."
        ),
    )

    # --- ephemeral-launch mode (used when instance_id is unset) -----------

    lifecycle: Literal["spot_one_time", "on_demand"] = Field(
        "spot_one_time",
        description=(
            "Market behaviour for newly-launched instances. Ignored when "
            "instance_id is set. `spot_one_time` is cheapest and terminates "
            "on interrupt; `on_demand` is safest."
        ),
    )

    subnet_id: Optional[str] = Field(None, description="Subnet for the launched instance.")
    key_name: Optional[str] = Field(
        None,
        description=(
            "EC2 key-pair *name* installed on the launched instance "
            "(passed to RunInstances). The matching local private key path "
            "is supplied via `ssh_key` below."
        ),
    )
    iam_instance_profile: str = Field(
        ...,
        min_length=1,
        description=(
            "[HMOD-07] Name of the IAM instance profile attached to the "
            "build host. The remote build wrapper authenticates to AWS "
            "(S3 upload + create-fpga-image) via this profile, which "
            "eliminates the local SSO-expiry failure mode that bit long-"
            "running builds. Required even for managed-reuse mode so the "
            "expected profile name is recorded in the project. See "
            "docs/aws-setup.md for one-time IAM role + instance profile "
            "creation steps."
        ),
    )

    ssh_key: Optional[str] = Field(
        None,
        description=(
            "Path to the SSH private key (supports `~`) the provider uses "
            "to connect to the instance. Same semantics as "
            "ExternalHostConfig.ssh_key — leave null/omit to fall back to "
            "ssh-agent or ~/.ssh/config. Independent of `key_name`, which "
            "is the EC2 key-pair name installed at launch."
        ),
    )

    ssh_user: str = Field(
        "centos",
        min_length=1,
        description=(
            "SSH username on the instance. AWS FPGA Developer AMIs default "
            "to 'centos'; user can override for custom AMIs."
        ),
    )

    instance_type: Optional[str] = Field(
        None, description="EC2 instance type (e.g. f2.2xlarge). Registry default applies."
    )
    ami_id: Optional[str] = Field(
        None,
        description=(
            "AMI ID. Registry supplies a framework-vetted base AMI; user may "
            "override with a derived AMI built on top of the base."
        ),
    )
    aws_fpga_version: Optional[str] = Field(
        None, description="HDK version tag for stamp comparison (e.g. v1.4.0-firesim)."
    )

    # --- volume overrides (ephemeral-launch mode) -------------------------
    # Omitting all three reproduces today's behaviour exactly: the instance
    # inherits the AMI's baked volumes untouched. The provider resizes by
    # role (root vs the single data volume) via AMI introspection, so the
    # user never has to know the AMI's /dev/... layout. Ignored in
    # managed-reuse mode (instance_id set), like the other launch fields.

    root_volume_gb: Optional[int] = Field(
        None,
        description=(
            "Resize the launched instance's ROOT EBS volume to this many GiB. "
            "Omit to inherit the AMI's baked root size. EBS can only grow, so "
            "a value smaller than the AMI's size is rejected at launch."
        ),
    )
    data_volume_gb: Optional[int] = Field(
        None,
        description=(
            "Resize the AMI's secondary/data EBS volume to this many GiB. "
            "Omit to inherit the AMI's baked size (the default that overflows "
            "for large designs). Requires the AMI to expose exactly one "
            "non-root EBS volume; grow-only."
        ),
    )
    volume_type: Optional[
        Literal["gp3", "gp2", "io1", "io2", "st1", "sc1", "standard"]
    ] = Field(
        None,
        description=(
            "Override the EBS volume type for whichever volumes are being "
            "resized (root_volume_gb / data_volume_gb). Applies only to the "
            "overridden volume(s), so it requires at least one of them set."
        ),
    )

    # ----------------------------------------------------------------------
    # Validators
    # ----------------------------------------------------------------------

    @field_validator("region", mode="after")
    @classmethod
    def _validate_region(cls, v: str) -> str:
        """[AWS-02]"""
        if not rx.AWS_REGION_RE.match(v):
            raise ValueError(
                f"[AWS-02] region '{v}' is invalid. " + regex_msg(rx.AWS_REGION_RE)
            )
        return v

    @field_validator("instance_type", mode="after")
    @classmethod
    def _validate_instance_type(cls, v: Optional[str]) -> Optional[str]:
        """[AWS-03]"""
        if v is None:
            return v
        if not rx.AWS_INSTANCE_TYPE_RE.match(v):
            raise ValueError(
                f"[AWS-03] instance_type '{v}' is invalid. "
                + regex_msg(rx.AWS_INSTANCE_TYPE_RE)
            )
        return v

    @field_validator("root_volume_gb", "data_volume_gb", mode="after")
    @classmethod
    def _validate_volume_gb(cls, v: Optional[int], info) -> Optional[int]:
        """[AWS-04] Volume sizes are GiB in the EBS-valid range (1..65536)."""
        if v is None:
            return v
        if v < 1 or v > 65536:
            raise ValueError(
                f"[AWS-04] {info.field_name}={v} is out of range. "
                f"Expected an EBS size in GiB between 1 and 65536."
            )
        return v

    @model_validator(mode="after")
    def _validate_volume_type_requires_size(self) -> "Ec2LaunchHostConfig":
        """[AWS-04] volume_type only makes sense alongside a resize; it is
        applied to the overridden volume(s), so at least one of
        root_volume_gb / data_volume_gb must be set."""
        if self.volume_type is not None and (
            self.root_volume_gb is None and self.data_volume_gb is None
        ):
            raise ValueError(
                "[AWS-04] volume_type requires root_volume_gb and/or "
                "data_volume_gb to be set (it applies to the resized volume)."
            )
        return self

    @field_validator("ami_id", mode="after")
    @classmethod
    def _validate_ami_id(cls, v: Optional[str]) -> Optional[str]:
        """[AWS-01]"""
        if v is None:
            return v
        if not rx.AMI_ID_RE.match(v):
            raise ValueError(
                f"[AWS-01] ami_id '{v}' is invalid. " + regex_msg(rx.AMI_ID_RE)
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

    @field_validator("ssh_key", mode="before")
    @classmethod
    def _empty_ssh_key_to_none(cls, v: Any) -> Any:
        """An empty/whitespace ssh_key falls back to ssh-agent."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("instance_id", mode="after")
    @classmethod
    def _validate_instance_id(cls, v: Optional[str]) -> Optional[str]:
        """[AWS-07]"""
        if v is None:
            return v
        if not rx.EC2_INSTANCE_ID_RE.match(v):
            raise ValueError(
                f"[AWS-07] instance_id '{v}' is invalid. "
                + regex_msg(rx.EC2_INSTANCE_ID_RE)
            )
        return v

    @field_validator("remote_platform_path", mode="after")
    @classmethod
    def _absolute_remote_path(cls, v: Optional[str]) -> Optional[str]:
        """[HMOD-04]"""
        if v is None:
            return v
        if not v.startswith("/"):
            raise ValueError(
                f"[HMOD-04] remote_platform_path must be an absolute Unix path, "
                f"got: {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Discriminated union + known-types set
# ---------------------------------------------------------------------------

HostModelConfig = Annotated[
    Union[ExternalHostConfig, Ec2LaunchHostConfig],
    Field(discriminator="type"),
]
"""Public union type used by TargetBuildConfig.host and TargetRunConfig.host."""


KNOWN_HOST_MODELS: frozenset[str] = frozenset({"external", "ec2_launch"})
"""[HMOD-01] Set of registered discriminator values. Imported by registry.py
to validate `platforms[].host_models` keys at registry-load time. Update
this when adding a new host model class above."""
