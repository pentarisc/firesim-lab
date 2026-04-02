"""
fslab/codegen/context.py
========================
Builds the Jinja2 rendering context dict from a validated ``FSLabConfig``
and its accompanying ``MasterRegistry``.

Public API
----------
    _build_template_context(config, registry) -> dict

Context keys
------------
Scalar / path keys (CMakeLists.txt.j2, fslab.yaml.j2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
project_name        str        config.project.name
package_name        str        config.project.package_name
fslab_top           str        config.project.fslab_top
config_class        str        config.project.config_class
platform            str        config.target.platform
clock_period        str        config.target.clock_period
driver_name         str        config.host.driver_name
top_module          str        config.design.top_module
gen_dir             str        config.advanced.gen_dir
gen_file_basename   str        config.advanced.gen_file_basename
firesim_root        str        config.advanced.firesim_root  (or "/opt/firesim")
firesim_lab_root    str        config.advanced.firesim_lab_root  (or "/opt/firesim-lab")

Derived from design.blackbox_ports
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
clock_port          str | None  key whose value == "in clock"
reset_port          str | None  key whose value == "in reset"
verilog_files       list[str] design.sources (blackbox only)

C++ build settings (CMakeLists.txt.j2, driver.cc.j2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
cxx_standard        int        config.host.cxx_standard
cxx_flags           list[str]  config.host.cxx_flags split into tokens
link_libs           list[str]  config.host.libs + required system libs (deduped)

Source file lists (CMakeLists.txt.j2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
user_cc_files       list[str]  config.host.sources
user_h_files        list[str]  config.host.includes
bridge_cc_files     list[str]  cpp_sources from every used registry bridge (deduped)
bridge_h_files      list[str]  cpp_headers from every used registry bridge (deduped)

Bridge objects for code-generation templates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
used_bridges        list[BridgeInstance]
    One BridgeInstance per *unique* bridge type used in the project, in project
    declaration order.  Used by driver.cc.j2 (list iteration).

unique_bridges      dict[str, BridgeInstance]
    Same data keyed by bridge type id.  Used by DUT.scala.j2 / Top.scala.j2
    where the template iterates with .items() for imports.

instances           list[BridgeInstance]
    One BridgeInstance per *bridge instance* (config.bridges entry), in
    declaration order.  Used by DUT.scala.j2 / Top.scala.j2 for per-instance
    port and wiring template inclusion.

BridgeInstance attributes
~~~~~~~~~~~~~~~~~~~~~~~~~
Each BridgeInstance is a SimpleNamespace exposing:

  From the registry BridgeEntry:
    id                  str
    label               str
    description         str
    cpp_type            str          C++ class name for get_bridges<T>()
    cpp_template        str          path to per-bridge driver snippet template
    cpp_headers         list[str]
    cpp_sources         list[str]
    input_ports         list[str]
    output_ports        list[str]
    module_macro_prefix str | None
    runtime_plusargs    list | None
    scala_templates     ScalaTemplates (Pydantic model)
      .dut_imports      str          path to Jinja2 sub-template
      .top_imports      str | None   path to Jinja2 sub-template (optional)
      .ports            str          path to Jinja2 sub-template
      .wiring           str          path to Jinja2 sub-template

  From the project BridgeConfig (only on instances, not unique_bridges):
    name                str          instance name, e.g. "serial_0"
    port_map            dict[str, str]
    params              dict[str, Any]
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
import os

if TYPE_CHECKING:
    from fslab.schemas.project import FSLabConfig
    from fslab.schemas.registry import BridgeEntry, MasterRegistry

# Libraries that the FireSim/MIDAS host driver always requires.
# Appended after user-specified libs so user symbols resolve correctly.
_SYSTEM_LIBS: list[str] = ["pthread", "gmp", "rt", "stdc++"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup_ordered(items: list[str]) -> list[str]:
    """Return a de-duplicated list with first-occurrence order preserved."""
    return list(dict.fromkeys(items))


def _make_bridge_instance(
    bridge_entry: "BridgeEntry",
    *,
    name: str | None = None,
    port_map: dict | None = None,
    params: dict | None = None,
) -> SimpleNamespace:
    """
    Wrap a BridgeEntry (and optional project-config data) in a SimpleNamespace
    so that Jinja2 templates can access all fields with dot notation.

    ``scala_templates`` is kept as the original Pydantic model so that
    ``instance.scala_templates.dut_imports`` etc. work without conversion.
    """
    return SimpleNamespace(
        # ── registry fields ───────────────────────────────────────────────
        id=bridge_entry.id,
        label=bridge_entry.label,
        description=bridge_entry.description,
        cpp_type=bridge_entry.cpp_type,
        cpp_template=bridge_entry.cpp_template,
        cpp_headers=bridge_entry.cpp_headers,
        cpp_sources=bridge_entry.cpp_sources,
        input_ports=bridge_entry.input_ports,
        output_ports=bridge_entry.output_ports,
        module_macro_prefix=bridge_entry.module_macro_prefix,
        runtime_plusargs=bridge_entry.runtime_plusargs,
        # ScalaTemplates Pydantic model kept intact so attribute access works:
        # bridge.scala_templates.dut_imports / .top_imports / .ports / .wiring
        scala_templates=bridge_entry.scala_templates,
        # ── project instance fields (None on unique_bridges entries) ──────
        name=name,
        port_map=port_map or {},
        params=params or {},
    )


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def _build_template_context(
    config: "FSLabConfig",
    registry: "MasterRegistry",
) -> dict:
    """
    Assemble the flat context dict consumed by all fslab Jinja2 templates.

    Parameters
    ----------
    config:
        A fully-validated ``FSLabConfig`` from ``load_and_validate``.
    registry:
        The ``MasterRegistry`` used to validate *config*.
    """

    # ── Scalar / path fields ───────────────────────────────────────────────
    project_name      = config.project.name
    package_name      = config.project.package_name
    fslab_top         = config.project.fslab_top
    config_class      = config.project.config_class
    platform          = config.target.platform
    clock_period      = config.target.clock_period
    driver_name       = config.host.driver_name
    gen_dir           = config.advanced.gen_dir
    gen_file_basename = config.advanced.gen_file_basename
    firesim_root      = config.advanced.firesim_root     or "/opt/firesim"
    firesim_lab_root  = config.advanced.firesim_lab_root or "/opt/firesim-lab"
    top_module        = config.design.top_module

    # ── Derived from design.blackbox_ports ────────────────────────────────
    # Scan for the key whose port definition is "in clock" or "in reset".
    clock_port:   str | None = None
    reset_port:   str | None = None
    verilog_files: list[str] | None = None
    verilog_file_names: list[str] | None = None

    if config.design.blackbox_ports:
        for port_name, port_def in config.design.blackbox_ports.items():
            if port_def == "in clock":
                clock_port = port_name
            elif port_def == "in reset":
                reset_port = port_name

    if config.design.sources:
        verilog_files = list(config.design.sources)
        verilog_file_names = [os.path.basename(p) for p in verilog_files]

    # ── C++ build settings ─────────────────────────────────────────────────
    cxx_standard: int    = config.host.cxx_standard
    # Split "-O3 -Wall" → ["-O3", "-Wall"] for per-flag template emission.
    cxx_flags: list[str] = config.host.cxx_flags.split() if config.host.cxx_flags else []
    # User libs first; system libs appended; dedup preserves first-occurrence.
    link_libs: list[str] = _dedup_ordered(list(config.host.libs) + _SYSTEM_LIBS)

    # ── Source file lists (CMakeLists) ────────────────────────────────────
    user_cc_files: list[str] = list(config.host.sources)
    user_h_files:  list[str] = list(config.host.includes)

    # ── Bridge collections ────────────────────────────────────────────────
    # Walk config.bridges in declaration order for deterministic output.

    # unique_bridges: one entry per bridge *type* — for import statements
    # (DUT.scala, Top.scala) and aggregated CMake source lists.
    unique_bridges_map: dict[str, SimpleNamespace] = {}

    # instances: one entry per bridge *instance* — for per-instance port and
    # wiring template inclusion (DUT.scala, Top.scala, driver.cc).
    instances: list[SimpleNamespace] = []

    raw_bridge_cc: list[str] = []
    raw_bridge_h:  list[str] = []

    for bridge_cfg in config.bridges:
        reg_bridge = registry.bridges.get(bridge_cfg.type)
        if reg_bridge is None:
            continue  # unreachable after load_and_validate, but be defensive

        # One unique-type record (no instance-specific fields).
        if bridge_cfg.type not in unique_bridges_map:
            unique_bridges_map[bridge_cfg.type] = _make_bridge_instance(reg_bridge)

        # One per-instance record (includes name, port_map, params).
        instances.append(
            _make_bridge_instance(
                reg_bridge,
                name=bridge_cfg.name,
                port_map=dict(bridge_cfg.port_map),
                params=dict(bridge_cfg.params),
            )
        )

        raw_bridge_cc.extend(reg_bridge.cpp_sources)
        raw_bridge_h.extend(reg_bridge.cpp_headers)

    # used_bridges: the unique-type list form, convenient for driver.cc.j2.
    used_bridges: list[SimpleNamespace] = list(unique_bridges_map.values())

    bridge_cc_files: list[str] = _dedup_ordered(raw_bridge_cc)
    bridge_h_files:  list[str] = _dedup_ordered(raw_bridge_h)

    # ------------------------------------------------------------------
    # Assemble and return the flat context dict
    # ------------------------------------------------------------------
    return {
        "config": config,
        "registry": registry,
        # paths & identifiers
        "project_name":      project_name,
        "package_name":      package_name,
        "fslab_top":        fslab_top,
        "config_class":      config_class,
        "platform":          platform,
        "clock_period":      clock_period,
        "driver_name":       driver_name,
        "gen_dir":           gen_dir,
        "gen_file_basename": gen_file_basename,
        "firesim_root":      firesim_root,
        "firesim_lab_root":  firesim_lab_root,
        # derived design fields
        "clock_port":        clock_port,
        "reset_port":        reset_port,
        "top_module":        top_module,
        "verilog_files":     verilog_files,
        "verilog_file_names": verilog_file_names,
        # C++ build
        "cxx_standard":      cxx_standard,
        "cxx_flags":         cxx_flags,
        "link_libs":         link_libs,
        # source file lists
        "user_cc_files":     user_cc_files,
        "user_h_files":      user_h_files,
        "bridge_cc_files":   bridge_cc_files,
        "bridge_h_files":    bridge_h_files,
        # bridge objects — three shapes for three access patterns
        "unique_bridges":    unique_bridges_map,  # dict[type_id, BridgeInstance]
        "used_bridges":      used_bridges,         # list[BridgeInstance], unique types
        "instances":         instances,            # list[BridgeInstance], per instance
        "target_config":     config_class,
    }