"""FPGA bitstream build orchestration.

Public entry point: `build_bitstream(project, registry, *, upload_platform=False)`.
"""

from .bitbuilder import (
    BitBuilder,
    BitstreamBuildFailed,
    F2BitBuilder,
    build_bitstream,
    make_bitbuilder,
)
from .buildconfig import BuildConfig, InvalidBuildConfig
from .buildhost import (
    BuildHost,
    BuildHostProvider,
    ExternalBuildHost,
    ExternalBuildHostProvider,
    RemoteCommandFailed,
    RsyncFailed,
    make_build_host_provider,
)

__all__ = [
    # config
    "BuildConfig",
    "InvalidBuildConfig",
    # host
    "BuildHost",
    "BuildHostProvider",
    "ExternalBuildHost",
    "ExternalBuildHostProvider",
    "RemoteCommandFailed",
    "RsyncFailed",
    "make_build_host_provider",
    # builder
    "BitBuilder",
    "F2BitBuilder",
    "BitstreamBuildFailed",
    "build_bitstream",
    "make_bitbuilder",
]