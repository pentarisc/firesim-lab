"""Publishers for `target.build.publish`.

Implements the `target.build.publish` axis: a closed discriminated union
of post-build artifact handlers.

Currently active variants
-------------------------
  none           NonePublisher — no-op (default for projects that just
                 want a local DCP).
  local_tarball  LocalTarballPublisher — schema-live but not yet
                 implemented (NotImplementedError); reserved for Alveo /
                 Vitis / on-prem F2 workflows.
  aws_afi        Removed from this module: the S3 upload +
                 create-fpga-image submit + AFI poll have moved into the
                 remote wrapper script (templates/remote_build/f2.sh.j2)
                 as part of the background-build redesign. The local CLI
                 must NOT call `make_publisher` for an aws_afi build —
                 that path raises NotImplementedError to surface misuse.

See docs/background-build-monitor-handoff.md for the redesign rationale.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path

from fslab.schemas.publish import (
    AwsAfiPublishConfig,
    LocalTarballPublishConfig,
    NonePublishConfig,
)
from fslab.utils.display import info

from .buildconfig import BuildConfig


# ---------------------------------------------------------------------------
# Inputs from the build phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishInputs:
    """Hand-off from the bitbuilder to the publisher.

    The bitbuilder produces a timestamped local results directory
    (`<results>/<ts>-<project>-PASS/cl_<quintuplet>/`); the publisher
    needs that exact path to locate the DCP tar.
    """

    local_results_dir: Path
    """Absolute path to the cl_dir-shaped results directory the
    bitbuilder rsynced back from the build host."""


# ---------------------------------------------------------------------------
# Publisher base + factory
# ---------------------------------------------------------------------------


class Publisher(abc.ABC):
    """Abstract base for post-build artifact handlers."""

    def __init__(self, cfg: BuildConfig) -> None:
        self.cfg = cfg

    @abc.abstractmethod
    def publish(self, inputs: PublishInputs) -> None:
        """Run the publish step. Raises on failure (per design choice 6a:
        publish errors propagate to the CLI rather than being swallowed)."""


def make_publisher(cfg: BuildConfig) -> Publisher:
    """Pick the right Publisher subclass for this build's publish config.

    Note: `publish.type=aws_afi` is no longer dispatched here. As of the
    background-build redesign, the S3 upload + create-fpga-image submit
    + AFI poll all run inside the remote wrapper script
    (`templates/remote_build/f2.sh.j2`), under instance-profile auth on
    the build host. Calling `make_publisher` on an aws_afi build is a
    bug — the orchestrator skips the publish step entirely for that
    case. We raise here so misuse surfaces immediately rather than
    silently double-running the publish work.
    """
    pub = cfg.publish
    if isinstance(pub, NonePublishConfig):
        return NonePublisher(cfg)
    if isinstance(pub, LocalTarballPublishConfig):
        return LocalTarballPublisher(cfg)
    if isinstance(pub, AwsAfiPublishConfig):
        raise NotImplementedError(
            "publish.type=aws_afi is now handled by the remote wrapper "
            "script (see fslab/bitstream/bitbuilder.py:build_bitstream and "
            "templates/remote_build/f2.sh.j2). The local CLI must not "
            "construct an AwsAfiPublisher — skip the publish step instead."
        )
    raise NotImplementedError(
        f"No publisher implementation for publish.type={type(pub).__name__}"
    )


# ---------------------------------------------------------------------------
# none
# ---------------------------------------------------------------------------


class NonePublisher(Publisher):
    """No publish step. Build artifacts stay where the bitbuilder pulled them."""

    def publish(self, inputs: PublishInputs) -> None:
        info(f"publish.type=none — leaving artifacts at {inputs.local_results_dir}")


# ---------------------------------------------------------------------------
# local_tarball (Tier 2/3 — not implemented)
# ---------------------------------------------------------------------------


class LocalTarballPublisher(Publisher):
    """Tar bitstream + metadata into a project-relative directory.

    Schema is live but the implementation is deferred — primarily relevant
    for future Alveo/Vitis bitbuilders. Raises NotImplementedError so
    misconfigured F2 projects fail loudly rather than silently no-op.
    """

    def publish(self, inputs: PublishInputs) -> None:
        raise NotImplementedError(
            "publish.type=local_tarball is not yet implemented "
            "(scheduled with Alveo/Vitis bitbuilder support)."
        )


# ---------------------------------------------------------------------------
# aws_afi — REMOVED in the background-build redesign.
# ---------------------------------------------------------------------------
#
# The S3 upload + create-fpga-image submit + AFI poll have all been moved
# into the remote wrapper script (templates/remote_build/f2.sh.j2). The
# wrapper authenticates via the EC2 instance profile attached at launch,
# which avoids the local SSO-session expiry failure mode that bit
# hours-long builds.
#
# AFI build status (the AWS-managed "pending → available" phase that
# follows create-fpga-image) is polled from local during `fslab monitor
# build` via `F2BitBuilder.check_post_wrapper_status` — no remote
# resource is held during that polling.
#
# See docs/background-build-monitor-handoff.md for the full design.
# ---------------------------------------------------------------------------
