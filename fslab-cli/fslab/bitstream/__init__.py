"""FPGA bitstream build orchestration.

Public entry point: `build_bitstream(project, registry, *, upload_platform=False)`.

The pipeline-agnostic host abstraction (`Host`, `ExternalHost`,
`Ec2LaunchHost`, `HostProvider`, the provider registry, and
`cleanup_remote`) and generic monitor primitives this package builds
on now live in [fslab.pipeline](../pipeline/). Callers needing those
should import them from there directly. This package keeps the
build-specific layer (bitbuilders, build stamp, build-side providers,
build-monitor state machine, publisher).
"""

from .bitbuilder import (
    BitBuilder,
    BitstreamBuildFailed,
    F2BitBuilder,
    build_bitstream,
    check_no_existing_build,
    make_bitbuilder,
)
from .buildconfig import BuildConfig, InvalidBuildConfig
from .buildhost import (
    BuildHostProvider,
    ExternalBuildHostProvider,
    Ec2LaunchBuildHostProvider,
    PlatformVersionMismatch,
    RegistryDefaultPathConflict,
    make_build_host_provider,
)

__all__ = [
    # config
    "BuildConfig",
    "InvalidBuildConfig",
    # build-side host providers
    "BuildHostProvider",
    "ExternalBuildHostProvider",
    "Ec2LaunchBuildHostProvider",
    "PlatformVersionMismatch",
    "RegistryDefaultPathConflict",
    "make_build_host_provider",
    # builder
    "BitBuilder",
    "F2BitBuilder",
    "BitstreamBuildFailed",
    "build_bitstream",
    "check_no_existing_build",
    "make_bitbuilder",
]
