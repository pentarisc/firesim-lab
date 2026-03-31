import sys; sys.path.insert(0, ".")
from fslab.schemas.registry import RegistryFile, MasterRegistry
from fslab.schemas.project import FSLabConfig
from pydantic import ValidationError

raw_reg = {
    "bridges": [{"id": "uart", "label": "UART Bridge", "description": "...",
        "input_ports": ["rx"], "output_ports": ["tx"],
        "cpp_headers": ["uart/uart.h"], "cpp_sources": ["uart/uart.cc"],
        "cpp_template": "t.j2",
        "scala_templates": {"dut_imports": "d.j2", "ports": "p.j2", "wiring": "w.j2"}}],
    "platforms": [{"id": "f1", "label": "AWS F1", "config_package": "midas", "config_class": "F1Config"}],
    "features": [{"id": "verilog-blackbox", "label": "VBB", "description": "D"}],
}
master = MasterRegistry.from_registry_files([RegistryFile.model_validate(raw_reg)])

base_proj = {
    "project": {"name": "my_design_02", "package_name": "com.example",
                "top_module": "MyMonolithicTop", "config_class": "MyConfig"},
    "design": {"type": "blackbox", "parameters": {"AXI_ADDR_WIDTH": 32},
               "sources": ["src/main/verilog/my_top.sv"],
               "blackbox_ports": {"clk": "in clock", "rst": "in reset",
                                  "uart_tx": "out 1", "uart_rx": "in 1",
                                  "mem_addr": "out AXI_ADDR_WIDTH"}},
    "target": {"platform": "f1", "clock_period": "1.0"},
    "host": {"emulator": "verilator", "driver_name": "Drv"},
    "bridges": [{"type": "uart", "name": "serial_0",
                 "port_map": {"tx": "uart_tx", "rx": "uart_rx"}}],
}

# Happy path
cfg = FSLabConfig.model_validate(base_proj, context={"registry": master})
assert cfg.project.name == "my_design_02"
print("✅  Happy path OK")

# PROJ-07
import copy
p2 = copy.deepcopy(base_proj); p2["design"]["blackbox_ports"] = None
try:
    FSLabConfig.model_validate(p2, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-07" in str(e), str(e); print("✅  PROJ-07 OK")

# PROJ-08
p3 = copy.deepcopy(base_proj); p3["design"]["type"] = "chisel"
try:
    FSLabConfig.model_validate(p3, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-08" in str(e), str(e); print("✅  PROJ-08 OK")

# PROJ-09
p4 = copy.deepcopy(base_proj); p4["design"]["blackbox_ports"] = {"clk": "in GHOST_PARAM"}
try:
    FSLabConfig.model_validate(p4, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-09" in str(e), str(e); print("✅  PROJ-09 OK")

# PROJ-10 duplicate bridge name
p5 = copy.deepcopy(base_proj)
p5["bridges"] = [{"type": "uart", "name": "s0", "port_map": {}},
                 {"type": "uart", "name": "s0", "port_map": {}}]
try:
    FSLabConfig.model_validate(p5, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-10" in str(e), str(e); print("✅  PROJ-10 OK")

# PROJ-11
p6 = copy.deepcopy(base_proj); p6["target"]["platform"] = "vu9p"
try:
    FSLabConfig.model_validate(p6, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-11" in str(e), str(e); print("✅  PROJ-11 OK")

# PROJ-12
p7 = copy.deepcopy(base_proj)
p7["bridges"] = [{"type": "nonexistent", "name": "x", "port_map": {}}]
try:
    FSLabConfig.model_validate(p7, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-12" in str(e), str(e); print("✅  PROJ-12 OK")

# PROJ-13 wrong direction (tx is output_port, uart_rx is "in")
p8 = copy.deepcopy(base_proj)
p8["bridges"] = [{"type": "uart", "name": "s0", "port_map": {"tx": "uart_rx"}}]
try:
    FSLabConfig.model_validate(p8, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-13" in str(e), str(e); print("✅  PROJ-13 OK")

# PROJ-13 value not in blackbox_ports
p9 = copy.deepcopy(base_proj)
p9["bridges"] = [{"type": "uart", "name": "s0", "port_map": {"rx": "ghost_port"}}]
try:
    FSLabConfig.model_validate(p9, context={"registry": master}); assert False
except ValidationError as e:
    assert "PROJ-13" in str(e), str(e); print("✅  PROJ-13 (missing bb port) OK")

print("\n🎉  All 13 project requirement checks passed.")