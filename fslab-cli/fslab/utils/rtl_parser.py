"""
fslab/codegen/rtl_parser.py
========================
"""

import pyslang
from rich.console import Console
from fslab.utils.display import console, error, info, section, success, warning

def is_struct(port_name, port_type, direction)-> bool:
    """
    Check if port is a struct.
    """
    # 1. Resolve typedefs/aliases to get the underlying type
    # Based on your debug, 'canonicalType' is a property
    t = port_type.canonicalType if hasattr(port_type, 'canonicalType') else port_type
    
    # 2. Check if it's a struct (packed or unpacked)
    # Your debug shows 'isStruct' and 'isUnpackedStruct' properties exist
    return getattr(t, 'isStruct', False) or getattr(t, 'isUnpackedStruct', False)

def extract_module_info(file_path, module_name)->[dict[str,str], dict[str,str]]:
    sm = pyslang.SourceManager()
    tree = pyslang.SyntaxTree.fromFile(file_path, sm)
    compilation = pyslang.Compilation()
    compilation.addSyntaxTree(tree)

    root = compilation.getRoot()
    target_inst = next((inst for inst in root.topInstances if inst.name == module_name), None)

    if not target_inst:
        error(f"Module '{module_name}' not found.")
        return [None, None]

    # 1. Parameters
    params: dict[str, str] = {}
    for sym in target_inst.body:
        if sym.kind == pyslang.SymbolKind.Parameter and not sym.isLocalParam:
            val = sym.value
            if val is None or str(val) == "<BAD>":
                syntax_str = str(sym.syntax)
                val = syntax_str.split('=')[-1].strip() if '=' in syntax_str else "n/a"
            params[str(sym.name)] = str(val)

    # 2. Ports
    ports: dict[str, str] = {}
    for sym in target_inst.body:
        if sym.kind == pyslang.SymbolKind.Port:
            # Get direction string (e.g., 'In', 'Out')
            direction = str(sym.direction).split('.')[-1]
            # Run recursive flattener
            isStruct = is_struct(str(sym.name), sym.type, direction)
            if isStruct:
                error(
                    f"Module '{module_name}' contains struct port '{sym.name}'.\n"
                    f"SystemVerilog structs not supported."
                )
                return [None, None]
            ports[str(sym.name)] = f"{direction.lower()} {sym.type}"

    return [params, ports]