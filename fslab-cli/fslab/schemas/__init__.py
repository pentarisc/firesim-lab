"""
fslab/schemas/__init__.py
=========================
Public surface of the schemas sub-package.
"""

from .registry import (
    BridgeEntry,
    FeatureEntry,
    MasterRegistry,
    PlatformEntry,
    RegistryFile,
    RuntimePlusarg,
    ScalaTemplates,
    MetaSimEntry,
    FpgaSimEntry,
)
from .project import (
    AdvancedConfig,
    DesignConfig,
    FSLabConfig,
    HostConfig,
    ProjectConfig,
    TargetConfig,
)
from .resolvers import (
    BridgeConfig,
    BridgeParam
)

from .parser import load_and_validate

__all__ = [
    # registry
    "BridgeEntry",
    "FeatureEntry",
    "MasterRegistry",
    "PlatformEntry",
    "RegistryFile",
    "RuntimePlusarg",
    "ScalaTemplates",
    "MetaSimEntry",
    "FpgaSimEntry",
    # project
    "AdvancedConfig",
    "DesignConfig",
    "FSLabConfig",
    "HostConfig",
    "ProjectConfig",
    "TargetConfig",
    # resolvers
    "BridgeConfig",
    "BridgeParam"
    # parser
    "load_and_validate",
]