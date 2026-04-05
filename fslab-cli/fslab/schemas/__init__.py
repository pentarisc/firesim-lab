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
    BridgeConfig,
    DesignConfig,
    FSLabConfig,
    HostConfig,
    ProjectConfig,
    TargetConfig,
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
    "BridgeConfig",
    "DesignConfig",
    "FSLabConfig",
    "HostConfig",
    "ProjectConfig",
    "TargetConfig",
    # parser
    "load_and_validate",
]