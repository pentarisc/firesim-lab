"""
fslab/schemas/resolvers.py
=========================
"""

from pydantic import BaseModel, Field, field_validator, model_validator, computed_field
from fslab.utils.display import console, error, info, section, success, warning, regex_msg
from typing import Literal, Any, Optional
import fslab.utils.regexes as rx

# 1. The central registry for Brige configuration classes
BRIDGE_CFG_REGISTRY = []

# 2. The decorator for anyone to use
def register_bridge_cfg(cls):
    BRIDGE_CFG_REGISTRY.append(cls)
    return cls

class BridgeParam(BaseModel):
    """One parameter in the project's bridge parameters list."""
    value: Optional[Any] = None
    ref: Optional[str] = None

    @model_validator(mode="before")
    def normalize(cls, v):
        # Case 1: literal value
        if not isinstance(v, dict):
            return {"value": v}

        # Case 2: dict → must be a ref
        if "ref" in v and len(v) == 1:
            return {"ref": v["ref"]}

        raise ValueError(f"bridge.parameters '{v}' is invalid.")

    @model_validator(mode="after")
    def check_exclusive(self):
        if (self.value is None) == (self.ref is None):
            raise ValueError(f"Either value or 'ref' must be set in bridge parameters")
        return self

# 3. The Base class everyone inherits from
class BridgeConfig(BaseModel):
    """One entry in the project's bridges list."""

    name: str
    port_map: dict[str, str] = Field(default_factory=dict)
    params: dict[str, BridgeParam] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """[PROJ-06] bridge.name must match ^[a-zA-Z_][a-zA-Z0-9_]*$"""
        if not rx.BRIDGE_NAME_RE.match(v):
            raise ValueError(
                f"[PROJ-06] bridge.name '{v}' is invalid. " +
                regex_msg(rx.BRIDGE_NAME_RE)
            )
        return v

    def resolve_refs(self, design_params: dict[str, Any]) -> "BridgeConfig":
        """Resolve 'ref' by sourcing value from the design.parameters"""
        
        print(f"Calling bridge configuration validations.. (before if)")

        # First time info and fslab_config will be None.
        if design_params is None:
            return self

        print(f"Calling bridge configuration validations. Design params is: {design_params}")

        if self.params:
            print(f"Params are: {self.params}..")
            for param_name, param in self.params.items():
                print(f"Param is {param_name} and ref is: {param.ref}..")
                if param.ref is not None:
                    # parameter must be a declared blackbox port
                    if param.ref not in design_params:
                        raise ValueError(
                            f"Parameter reference '{param.ref}' "
                            f"for bridge '{self.name}', parameter '{param_name}' "
                            f"does not exist in design.parameters."
                        )
                    param.value = design_params[param.ref]
        return self

# 4. Built-in types that come with firesim-lab
@register_bridge_cfg
class UartBridgeConfig(BridgeConfig):
    type: Literal['uart']
    # No uart bridge specific calculation here.

@register_bridge_cfg
class FasedBridgeConfig(BridgeConfig):
    type: Literal['fased']
    # No fased bridge specific calculation here.

@register_bridge_cfg
class BlockdevBridgeConfig(BridgeConfig):
    type: Literal['iceblk']

    def resolve_refs(self, design_params: dict[str, Any]) -> "BridgeConfig":
        """Resolve 'ref' by sourcing value from the design.parameters"""
        
        print(f"Calling super class's method first:")
        super().resolve_refs(design_params)

        # First time info and fslab_config will be None.
        if design_params is None:
            return self

        print(f"Calling blockdev bridge configuration validations. Design params is: {design_params}")

        n = self.params["n_trackers"].value
        t = self.params["tag_bits"].value

        # Normalize missing values
        if n is None and t is None:
            n, t = 1, 0

        elif n is not None and t is None:
            n = int(n)
            assert n >= 1, "n_trackers must be >= 1"
            t = (n - 1).bit_length()

        elif n is None and t is not None:
            t = int(t)
            assert t >= 0, "tag_bits must be >= 0"
            n = 1 << t

        else:
            n = int(n)
            t = int(t)
            assert n >= 1
            assert t >= 0
            required = (n - 1).bit_length()
            assert t >= required, f"tag_bits ({t}) too small for n_trackers ({n}). At least ({required}) required."

        # Add derived fields
        self.params["n_trackers"].value = n
        self.params["tag_bits"].value = max(1, t)
        return self