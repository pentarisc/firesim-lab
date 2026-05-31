# Developer Documentation

This section is for people who want to change what firesim-lab *does*, not just
use it: contributors to the framework itself, and advanced users extending it
with new bridges, new generated code, new CLI flows, or container changes. It is
the counterpart to the end-user material — {doc}`/commands/index`,
{doc}`/concepts/index`, and the {doc}`/quickstart/index` — which assume the
framework is fixed and you are driving it.

```{note}
"No-Chisel" applies to firesim-lab's *users*, not its *developers*. A user wires
a Verilog/SystemVerilog blackbox to existing bridges through `fslab.yaml` and
never sees Scala. Extending the framework — a new bridge, a new generated file, a
new subcommand — is a framework-level task and does involve Chisel/Scala, C++,
Python, Jinja2, and YAML, in varying combinations depending on what you touch.
```

## How this section is organised

The framework has four moving parts a contributor works on: the **Python CLI**
that orchestrates everything, the **Jinja2 templates** it renders into Chisel and
C++, the **bridges** that connect a simulated target to the host, and the
**Docker image** the whole toolchain runs inside. The pages below map onto those
parts.

- {doc}`fslab-python/index` — the contributor's reference for the `fslab` CLI
  under `fslab-cli/`: the package map, the request lifecycle (load → validate →
  generate → compile → run), the cross-cutting patterns (decorator registries,
  Pydantic schemas, the `.fslab/` state model), and the extension points for new
  commands, bridges, host models, and tunables. **Start here** if you are new to
  the internals — it routes to everything else.
- {doc}`jinja-templates` — the catalogue of every template under
  `templates/`: what each one consumes and where its rendered output lands. The
  companion to the CLI section's rendering-mechanism pages.
- {doc}`bridges/index` — the conceptual and how-to half of the bridge
  documentation: what a bridge is made of (target interface, stub, host model,
  C++ driver, wiring templates, registry entry), the target/host split, and the
  end-to-end recipe for adding one.
- {doc}`bridge-reference/index` — the reference half: per-bridge spec sheets
  (exact ports, parameters, registry fields, driver hooks) for the UART,
  BlockDevice, and FASED bridges that ship today.
- {doc}`docker-architecture` — how the toolchain image is built and run, and —
  crucially for contributors — how to iterate on the CLI and on bridges in a
  local dev container without rebuilding the image or pushing to the repository.

## Find your task

| You want to… | Start at |
|---|---|
| Understand the CLI internals end to end | {doc}`fslab-python/index` |
| Add a new bridge | {doc}`bridges/index` → {doc}`bridges/adding-new-bridges` |
| Look up an existing bridge's ports/params | {doc}`bridge-reference/index` |
| Add or change a generated file | {doc}`jinja-templates` + {doc}`fslab-python/templates` |
| Add a new subcommand or flow | {doc}`fslab-python/extending` |
| Add a validation rule | {doc}`fslab-python/schemas` |
| Change the FPGA remote build/run | {doc}`fslab-python/orchestration` |
| Iterate locally without rebuilding the image | {doc}`docker-architecture` |

Almost every contribution starts by reading {doc}`fslab-python/index` and tracing
one `fslab` invocation through its lifecycle; the other pages fill in the area you
are changing. Before writing any Chisel for a bridge, also read
{doc}`/concepts/index` so the target/host vocabulary is in place.

```{toctree}
:maxdepth: 2

bridges/index
bridge-reference/index
docker-architecture
fslab-python/index
jinja-templates
```
