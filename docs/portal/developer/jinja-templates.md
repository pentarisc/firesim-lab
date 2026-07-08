# Jinja2 Template Reference

This is the **catalogue** of every template under `fslab-cli/fslab/templates/`:
what each one is, the context variables it consumes, and where its rendered
output lands. It is the companion to {doc}`/developer/fslab-python/templates`,
which explains the *rendering mechanism* (the loader, the context builder, the
render plan, sub-template inclusion, the hash gate). Read that page for *how*
rendering works; read this page to find *which* template produces a given file.

The context variables named below are produced by `_build_template_context()`
and the validated `FSLabConfig` / `MasterRegistry` models — see
{doc}`/developer/fslab-python/schemas`. To add or change a template, see the
checklist in {doc}`/developer/fslab-python/templates` and the recipes in
{doc}`/developer/fslab-python/extending`.

## Conventions common to all templates

- **Output is code, not HTML.** The Jinja2 environment has autoescape **off**
  and does not enable `trim_blocks`/`lstrip_blocks`, so templates control
  whitespace explicitly with `{%- … -%}` / `{{- … -}}` markers. The dense `-`
  markers in the `.scala.j2` files are deliberate.
- **Generated files are marked.** Most rendered files carry a
  `@GENERATED_BY_FSLAB` banner and a "do not edit by hand" note. Hand-edits to a
  generated file are detected by hash and block the next `generate` unless
  `--force` (see the hash gate in {doc}`/developer/fslab-python/templates`).
- **Three render triggers.** A template is rendered by one of three paths:
  the main `generate` pipeline (`_render_templates` in `commands/build.py`),
  the `fslab init` scaffolder (`fslab.yaml.j2` only), or just-in-time at run
  launch (`remote_run/f2.sh.j2`).

## Project files — rendered by `fslab generate`

These eight templates make up the `render_plan` in `_render_templates`. Output
paths are relative to the project root; names in `{braces}` are filled from the
context.

| Template | Output | Purpose |
|---|---|---|
| `build.sbt.j2` | `build.sbt` | SBT root build for the Chisel shim; depends on the vendored `fslabBridges` project. |
| `plugins.sbt.j2` | `project/plugins.sbt` | Static SBT plugin list (scalafix, assembly, bloop, …). No variables. |
| `Config.scala.j2` | `src/main/scala/Config.scala` | The `Parameters` config class (`NoConfig` by default). |
| `Top.scala.j2` | `src/main/scala/{fslab_top}.scala` | The top `RawModule`: clock/reset bridges, instantiates the DUT, wires each bridge instance. |
| `DUT.scala.j2` | `src/main/scala/{fslab_top}BlackBox.scala` | The Chisel `BlackBox` wrapping the user's Verilog: IO bundle + per-bridge port declarations + `addPath`. |
| `driver.cc.j2` | `src/main/cc/{driver_name}.cc` | The C++ simulation driver class + `create_simulation` factory. |
| `CMakeLists.txt.j2` | `CMakeLists.txt` | The entire host-driver / metasim / fpgasim / fpga build, generated from registry entries. |
| `user_rtl_readme.md.j2` | `user_rtl/README.md` | Reminder listing the expected RTL source filenames. |

### `build.sbt.j2`

Context: `firesim_lab_root` (required to exist on disk), `package_name`,
`project_name`, `fslab_top`. Pins Scala 2.13.10 and Chisel 3.6.1 to match
FireSim, and binds the project to the `fslabBridges` `ProjectRef` in the
firesim-lab tree. The packaged jar is named `{project_name}.jar`.

### `Top.scala.j2`

Context: `package_name`, `fslab_top`, `top_module`, the derived
`clock_port` / `reset_port` / `enable_port`, plus the two bridge collections.
It iterates `unique_bridges` (dict, by type) to emit each type's
`scala_templates.top_imports`, and `instances` (list, per instance) to
`{% include %}` each instance's `scala_templates.wiring` snippet (captured and
re-indented). Always emits the `RationalClockBridge`, `ResetPulseBridge`, and
`PeekPokeBridge` scaffolding.

### `DUT.scala.j2`

Context: `top_module`, `package_name`, the clock/reset/enable ports,
`verilog_files`, and the bridge collections. Iterates `unique_bridges` for
`scala_templates.dut_imports` (optional — none of the built-in bridges set it)
and `instances` for each `scala_templates.ports` snippet inside the IO bundle.
`addPath(...)` is emitted once per entry in `verilog_files`.

### `driver.cc.j2`

Context: `driver_name`, `used_bridges` (unique types). Emits `#include`s from
each bridge's `cpp_headers`, a `get_bridges<cpp_type>()` member per type, the
`simulation_run()` loop that ticks all bridges, and — per unique type —
`{% include bridge.cpp_template %}` for custom per-tick logic (empty for the
built-in bridges today; see below). Ends with the `create_simulation` factory.

### `CMakeLists.txt.j2`

The largest template. Context: the core scalars (`project_name`, `platform`,
`driver_name`, `gen_dir`, `gen_file_basename`, `clock_period`, the toolchain
roots, `quintuplet`), the C++ lists (`cxx_standard`, `cxx_flags`, `link_libs`,
the `*_cc_files` / `*_h_files` source lists, `verilog_files`), and the three
resolved registry entries `platform_cfg`, `metasim_cfg`, `fpgasim_cfg`. It
renders four independent build sub-trees (`driver/`, `metasim/`, `fpgasim/`,
`fpga/`) entirely from those registry objects — no platform or tool name is
hard-coded. It also writes `Makefile.<id>.sim` and `Makefile.fpga.mk` via
`file(WRITE ...)`, which imposes the CMake-vs-Make two-level escaping discipline
documented in {doc}`/developer/fslab-python/templates`.

### `plugins.sbt.j2` / `Config.scala.j2` / `user_rtl_readme.md.j2`

Small templates. `plugins.sbt.j2` is static. `Config.scala.j2` consumes only
`package_name`. `user_rtl_readme.md.j2` consumes `verilog_file_names`.

## Scaffolding — rendered by `fslab init`

### `fslab.yaml.j2`

Output: `fslab.yaml` in the new project root. **Not** part of the `generate`
render plan — it is rendered by `init.py` through its own Jinja2 environment,
because at `init` time there is no validated config yet. Context comes from CLI
flags and the parsed top module: `project_name`, `platform`, `project_dir`, and
(when `fslab init -t/-f` parsed a module) `top_module`, `ports`, `params`,
`sources`. It produces a complete, valid `fslab.yaml` with the `design` block
populated from the parsed ports, plus extensive commented examples for `host`,
`bridges` (uart/fased/iceblk), the `target.build` / `target.run` axes, and
`advanced`. The inline comments cite the validation codes
(`[PROJ-01]`, `[BBA-XX]`, …) so the generated file doubles as a reference.

## Bridge sub-templates — included, never in the render plan

The files under `templates/bridges/<id>/` are pulled into `Top.scala.j2`,
`DUT.scala.j2`, and `driver.cc.j2` via `{% include %}`, using the paths declared
in each bridge's registry entry (`scala_templates.*` and `cpp_template`). They
render in the `instances` / `used_bridges` loop, so the per-instance
`BridgeInstance` namespace — `instance.name`, `instance.port_map.<port>`,
`instance.params.<key>.value` — is in scope.

The registry slot → file mapping for the three built-in bridges:

| Bridge | `top_imports` | `ports` | `wiring` | `cpp_template` |
|---|---|---|---|---|
| `uart` | `bridges/uart/top_imports.scala.j2` | `bridges/uart/ports.scala.j2` | `bridges/uart/wiring.scala.j2` | `bridges/uart/sim_loop.cc.j2` (empty) |
| `fased` | `bridges/fased/top_imports.scala.j2` | `bridges/fased/ports.scala.j2` | `bridges/fased/wiring.scala.j2` | `bridges/uart/sim_loop.cc.j2` (empty) |
| `iceblk` | `bridges/iceblk/top_imports.scala.j2` | `bridges/iceblk/ports.scala.j2` | `bridges/iceblk/wiring.scala.j2` | `bridges/iceblk/sim_loop.cc.j2` (empty) |

`dut_imports` is unset (commented out) for all three. The `sim_loop.cc.j2`
files are intentionally **empty** — the built-in bridges need no custom per-tick
logic beyond the standard `bridge->tick()` loop in `driver.cc.j2`, so the
`{% include %}` contributes nothing. They exist as the hook where a future
bridge (or your own) would add driver-side behaviour. fased reuses uart's empty
`sim_loop.cc.j2` because it likewise has none.

What each snippet emits and the params it reads:

- **`uart`** — `top_imports`: `UARTBridge` / `UARTPortIO` imports. `ports`: a
  `txd` Output and `rxd` Input. `wiring`: a `UARTPortIO` wire connected to
  `dut.io`, then `UARTBridge(...)` parameterised by `params.freq_mhz.value` and
  `params.baud_rate.value`.
- **`fased`** — `top_imports`: Nasti / `FASEDBridge` / AXI4 diplomacy imports.
  `ports`: the full AXI4 master/slave port set, widths from
  `params.id_bits` / `addr_bits` / `data_bits` / `user_bits`. `wiring`: builds a
  `NastiIO`, maps every AXI4 channel to `dut.io`, and instantiates
  `FASEDBridge(...)` with `mem_base` / `mem_size` / `memory_region_name`.
- **`iceblk`** — `top_imports`: `BlockDevBridge` / `BlockDeviceConfig` /
  `BlockDeviceIO` imports. `ports`: the block-device request/data/response/info
  channels, tag width from `params.tag_bits`. `wiring`: builds a `BlockDeviceIO`
  from `params.n_trackers`, maps the channels, and branches the tag wiring on
  whether `n_trackers <= 1` (the only Jinja2 conditional in the bridge wiring).

For the C++ models and Scala bridge stubs these snippets reference, see
{doc}`/developer/bridges/index`.

## Remote wrapper scripts

These render into Bash that runs on the F2 host. They are platform-specific
(F2 only today) and never edited by hand.

### `remote_build/f2.sh.j2`

Output: `scripts/remote_build_f2.sh`, rendered by `fslab generate` (it is the
one render-plan entry added conditionally for `platform == "f2"`). It lives under
`scripts/` so it survives `fslab clean`, and `fslab build fpga` re-uploads it
each run. Jinja2 bakes in **project-static** config — `project_name`,
`quintuplet`, `dcp_glob`, `fpga_frequency`, `place`/`phy_opt`/`route`/`extra_args`, `s3_bucket_base`,
`append_userid_region`, `aws_region`. **Per-build** values (`BUILD_ID`, `S3_KEY`,
`AFI_NAME`, `CL_DIR`, `REMOTE_BUILD_SCRIPT`, log/result/stamp paths) arrive as
environment variables at launch, not in the rendered body. Once launched it
writes the remote stamp, ensures the S3 bucket, runs `build-bitstream.sh`,
uploads the DCP tar, submits `create-fpga-image`, and always writes
`result.yaml` via an EXIT trap. See {doc}`/developer/fslab-python/orchestration`
for how the monitor consumes the stamp and result.

### `remote_run/f2.sh.j2`

Output: `run/fpga/staging/remote_run_f2.sh`, rendered **just-in-time** by
`fslab sim fpga --detach` (not by `generate`), so per-run params can be baked
into env vars per launch. Jinja2 renders project-static config; per-run values
(`RUN_ID`, `AGFI`, `SLOT_DIR`, `DRIVER_BASENAME`, `MAX_CYCLES`, `EXTRA_FLAGS`,
`VERIFY_HASH`, `HAS_SHA256SUMS`, log/result/stamp paths) arrive as env vars.
Once launched under `nohup` it writes the run stamp, optionally verifies
`SHA256SUMS`, clears and loads the AGFI onto the FPGA slot (busy-waiting on
`fpga-describe-local-image`), execs the driver as root, and writes `result.yaml`
via an EXIT trap. Unlike the build wrapper there is no post-wrapper polling — the
driver's exit is terminal.

## Adding or changing a template

- New generated project file → add the `.j2` here **and** a `render_plan` entry
  in `_render_templates`, plus any new context fields in
  `_build_template_context`.
- New per-bridge snippet → add it under `templates/bridges/<id>/` and point the
  registry entry's `scala_templates.*` / `cpp_template` at it; it is included,
  not render-planned.
- New platform wrapper → add it under `remote_build/<id>/` (or `remote_run/`)
  and extend the per-platform conditional in the render plan / run launcher.
- Keep verbatim-emit registry fields (`cmake_fragment`, `makefile_fragment`)
  free of Jinja2 markers — the schema layer rejects them
  (`REG-13`/`REG-14`).
- Update this catalogue when you add or rename a template file.
