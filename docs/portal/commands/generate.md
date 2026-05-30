# fslab generate

Validate `fslab.yaml` and render it into framework code: the Chisel shim, the C++ driver, the build files, and the platform scripts. You rarely run this directly — {doc}`build` and {doc}`sim` call it for you — but it is the step that turns your config into a buildable project.

## Synopsis

```bash
fslab generate [-f] [--dry-run] [-c <path>]
```

## Options

| Option | Default | Description |
|---|---|---|
| `-f`, `--force` | off | Regenerate even if the config hash is unchanged, and overwrite framework files you have edited by hand. |
| `--dry-run` | off | Report what *would* be generated without writing anything. |
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML. |

## What it does

1. **Load and validate.** Parses `fslab.yaml` and validates it against the registries. Every rule in the {doc}`init` field reference (the `[PROJ-*]`, `[HMOD-*]`, `[PUB-*]`, … codes) is enforced here; a violation aborts with the offending code and a message.
2. **Hash check.** Computes a hash over `fslab.yaml` plus the registry files. If nothing has changed since the last successful generate, generation is **skipped** — this is why repeated `fslab build` / `fslab sim` runs are fast. Pass `--force` to regenerate anyway.
3. **Render templates.** Writes the generated files (below) from the Jinja2 templates bundled with the framework.

### Files it generates

| Output | Role |
|---|---|
| `build.sbt`, `project/plugins.sbt` | sbt build definition for the Chisel shim. |
| `src/main/scala/<Top>.scala` | Chisel top that emits the FIRRTL annotations Golden Gate needs. |
| `src/main/scala/<Top>BlackBox.scala` | Chisel `BlackBox` wrapping your Verilog. |
| `src/main/scala/Config.scala` | Generated target config. |
| `src/main/cc/<driver_name>.cc` | The C++ host driver, named after `host.driver_name`. |
| `CMakeLists.txt` | Drives the C++ driver / simulator build. |
| `user_rtl/README.md` | Generated note for the RTL folder. |
| `scripts/remote_build_f2.sh` | F2 background-build wrapper (rendered only when `target.platform` is `f2`). |

`<Top>` is derived from `project.name` (e.g. `uart-print-test` → `UartPrintTestTop`). These files are framework-owned — regenerated from `fslab.yaml` — so you do not edit them by hand.

## Protecting hand edits

`fslab generate` tracks the files it writes. If it detects that you have modified one of those generated files, it **refuses to overwrite** and lists the changed paths rather than silently discarding your edits:

```text
Detected changes to bootstrapped files.
Refusing to regenerate to prevent accidental data loss:
  • modified: build.sbt
Please review these changes or run with --force to overwrite.
```

Resolve it by reverting the file, or by re-running with `--force` to accept the regenerated version. The `scripts/remote_build_f2.sh` wrapper lives under `scripts/` (outside `build/`) so it survives {doc}`clean`; `fslab build fpga` re-uploads it every time, so template updates take effect without an extra step.

## Example

```bash
fslab generate              # render if fslab.yaml changed
fslab generate --dry-run    # preview only
fslab generate --force      # rebuild everything from the templates
```

## Related

- {doc}`/quickstart/metasim` — `generate` in the metasim flow.
- {doc}`/developer/jinja-templates` — how the templates are structured (for contributors).
- {doc}`build` — runs `generate` then compiles.
