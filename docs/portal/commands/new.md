# fslab new

Scaffold a new, empty project workspace. This is the first command you run for any design — it creates the directory skeleton that every later command expects.

## Synopsis

```bash
fslab new <project_name>
```

## Arguments

| Argument | Required | Description |
|---|---|---|
| `project_name` | yes | Name of the new project folder to create, relative to the current directory. |

`fslab new` takes no options. It refuses to run if a directory named `project_name` already exists, so it never overwrites your work.

## What it does

`fslab new my-design` creates the following tree under the current directory (typically `/target`, your bind-mounted workspace):

```text
my-design/
├── .fslab/
│   └── meta.json          # workspace marker + project name (do not edit)
├── .gitignore             # excludes generated-src/, build/, payloads/, etc.
├── src/main/scala/        # generated Chisel shim lands here
├── src/main/cc/           # generated C++ driver lands here
├── generated-src/         # FIRRTL / Verilog / Golden Gate output
├── user_rtl/              # YOUR Verilog/SystemVerilog goes here
└── payloads/              # run-time inputs (hex images, ELFs, disk images)
```

Two folders are yours to fill:

- **`user_rtl/`** — copy or write your `.v` / `.sv` sources here. `fslab init` resolves a bare top-module filename against this directory.
- **`payloads/`** — run-time inputs that are uploaded per-run, not baked into the build. The generated `.gitignore` excludes `payloads/*` (keeping only an optional `payloads/SHA256SUMS` manifest), so payloads are not version-controlled by default.

The hidden `.fslab/meta.json` marks the folder as an fslab workspace and records the project name. `fslab init` reads it to confirm you are inside a project; do not edit or delete it.

## Project name rules

The folder name becomes `project.name` in `fslab.yaml` and is later validated against `^[a-zA-Z0-9_-]+$` (letters, digits, `_`, `-`). The framework derives a Chisel top-module name from it — for example `uart-print-test` becomes `UartPrintTestTop`. Stick to that character set to avoid a validation error at {doc}`generate` time.

## Example

```bash
cd /target
fslab new uart-print-test
cd uart-print-test
cp /path/to/AXIUARTPrinter.v user_rtl/
```

You are now ready to run {doc}`init`.

## Related

- {doc}`/quickstart/index` — the full scaffold-to-simulation walkthrough.
- {doc}`/installation/mountpoints` — how `/target` maps to your host workspace.
- {doc}`init` — the next step.
