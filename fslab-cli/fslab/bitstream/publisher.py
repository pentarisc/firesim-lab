"""Publishers for `target.build.publish`.

The publisher runs after a successful bitstream build (and AFTER the
build host has been released, so long S3 uploads don't hold an EC2
instance billing). It implements the `target.build.publish` axis from
the schema migration: a closed discriminated union of post-build
artifact handlers.

Tier 1 (this round) implements only `aws_afi`:
  * S3 upload of the DCP tarball
  * `ec2 create-fpga-image` -> AFI/AGFI
  * indefinite poll until state leaves 'pending'

The `aws_afi` schema also accepts `copy_to_regions`, `sns_topic_arn`,
`post_build_hook`, `hwdb_entry_name` — Tier 2/3 features. We accept them
to keep `fslab.yaml` files forward-compatible, but log a warning and
skip them.

`local_tarball` is schema-valid but not yet implemented (raises).

Auth context
------------
The publisher builds a single `boto3.Session` from
`cfg.publish.aws_profile` at the top of `publish()` and threads it
through every aws_fpga helper. `check_credentials` runs first so SSO /
missing-creds failures surface with a friendly message before any
upload begins.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fslab.schemas.publish import (
    AwsAfiPublishConfig,
    LocalTarballPublishConfig,
    NonePublishConfig,
)
from fslab.utils.display import info, success, warning

from . import aws_fpga
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
    """Pick the right Publisher subclass for this build's publish config."""
    pub = cfg.publish
    if isinstance(pub, NonePublishConfig):
        return NonePublisher(cfg)
    if isinstance(pub, LocalTarballPublishConfig):
        return LocalTarballPublisher(cfg)
    if isinstance(pub, AwsAfiPublishConfig):
        return AwsAfiPublisher(cfg)
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
# aws_afi (Tier 1 MVP)
# ---------------------------------------------------------------------------


class AwsAfiPublisher(Publisher):
    """Publish a built DCP as an AWS FPGA Image (AFI/AGFI).

    Pipeline:
      1. build a boto3 session pinned to cfg.publish.aws_profile
      2. probe credentials (SSO-expiry detection)
      3. resolve effective bucket name (firesim convention: optional
         '-<userid>-<region>' suffix for collision avoidance)
      4. ensure bucket exists
      5. locate DCP tar via the configured glob (default
         'build/checkpoints/*.tar') under the local results dir
      6. upload to s3://<bucket>/dcp/<project>-<quintuplet>-<utc-ts>-<rand>.tar
      7. ec2.create_fpga_image -> AFI/AGFI
      8. poll describe_fpga_images until state leaves 'pending'

    Tier 2/3 fields on AwsAfiPublishConfig (copy_to_regions, sns_topic_arn,
    post_build_hook, hwdb_entry_name) are warned-and-ignored in this
    iteration.
    """

    def publish(self, inputs: PublishInputs) -> None:
        cfg = self.cfg
        pub = cfg.publish
        assert isinstance(pub, AwsAfiPublishConfig)  # type-narrow for mypy/readers

        self._warn_on_unsupported_fields(pub)

        # ------ session + credentials -------------------------------------
        # Region is left implicit on the publisher: the user's profile (or
        # AWS_DEFAULT_REGION) supplies it. The publisher does not pin a
        # region from cfg because the publish axis is decoupled from the
        # build host's region (S3/AGFI may be in a different region than
        # the build farm). aws_region() raises a clear error if neither
        # source has one.
        session = aws_fpga.make_session(profile=pub.aws_profile)
        aws_fpga.check_credentials(session, pub.aws_profile)

        # ------ bucket name ------------------------------------------------
        bucket = pub.s3_bucket_name
        if pub.append_userid_region:
            userid = aws_fpga.aws_userid(session)
            region = aws_fpga.aws_region(session)
            bucket = f"{bucket}-{userid}-{region}"
            info(f"Effective bucket name (with userid/region): {bucket}")

        # ------ DCP tar discovery ------------------------------------------
        tar_path = self._locate_dcp_tar(inputs.local_results_dir, pub.dcp_tar_glob)

        # ------ S3 upload --------------------------------------------------
        aws_fpga.ensure_bucket(session, bucket)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d--%H-%M-%S")
        rand = aws_fpga.random_suffix(4)
        dcp_key = f"dcp/{cfg.project_name}-{cfg.quintuplet}-{ts}-{rand}.tar"
        aws_fpga.upload_dcp_to_s3(session, tar_path, bucket, dcp_key)

        # ------ create FPGA image ------------------------------------------
        afi_name = pub.hwdb_entry_name or cfg.project_name
        description = (
            f"firesim-lab build: project={cfg.project_name} "
            f"quintuplet={cfg.quintuplet} ts={ts}"
        )
        ids = aws_fpga.create_fpga_image(
            session,
            bucket=bucket,
            dcp_key=dcp_key,
            logs_prefix="logs/",
            name=afi_name,
            description=description,
        )
        afi = ids["FpgaImageId"]
        agfi = ids["FpgaImageGlobalId"]
        info(f"Submitted create-fpga-image: AFI={afi}, AGFI={agfi}")

        # ------ poll until done --------------------------------------------
        aws_fpga.wait_for_fpga_image(session, afi)
        success(f"AFI build complete. AFI={afi}  AGFI={agfi}")

    # ----------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------

    def _warn_on_unsupported_fields(self, pub: AwsAfiPublishConfig) -> None:
        """Tier 1 ignores the cross-cutting / extra-feature fields."""
        if pub.copy_to_regions:
            warning(
                f"publish.copy_to_regions={pub.copy_to_regions} is not yet "
                f"implemented; AFI will not be replicated to other regions."
            )
        if pub.sns_topic_arn:
            warning(
                f"publish.sns_topic_arn={pub.sns_topic_arn} is not yet "
                f"implemented; no notifications will be sent."
            )
        if pub.post_build_hook:
            warning(
                f"publish.post_build_hook={pub.post_build_hook} is not yet "
                f"implemented; hook will not be invoked."
            )
        if pub.hwdb_entry_name and pub.hwdb_entry_name != self.cfg.project_name:
            # The hwdb-entry file write is Tier 2; we still honor the name
            # for the AFI's `Name` field so the user's intent isn't lost.
            info(
                f"hwdb_entry_name='{pub.hwdb_entry_name}' will be used as the "
                f"AFI Name. The hwdb descriptor file is not yet emitted."
            )

    def _locate_dcp_tar(self, results_dir: Path, glob_pat: str) -> Path:
        """Resolve `glob_pat` (relative to results_dir) to exactly one tar.

        Multiple matches indicate a stale prior-build artifact in the
        results dir — refusing to guess is safer than picking 'newest'.
        """
        matches = sorted(results_dir.glob(glob_pat))
        if not matches:
            raise FileNotFoundError(
                f"No DCP tar matched '{glob_pat}' under {results_dir}. "
                f"Check that the build script produced a tarball at that "
                f"location, or override `publish.dcp_tar_glob`."
            )
        if len(matches) > 1:
            joined = "\n  ".join(str(m) for m in matches)
            raise RuntimeError(
                f"DCP glob '{glob_pat}' matched multiple files under "
                f"{results_dir}:\n  {joined}\n"
                f"Remove stale artifacts or tighten the glob."
            )
        return matches[0]
