# fslab clean

Delete generated artefacts and build directories to reclaim disk space or force a clean rebuild.

## Synopsis

```bash
fslab clean [--all]
```

## Options

| Option | Default | Description |
|---|---|---|
| `--all` | off | Also remove the `.fslab/` state directory (config hash, logs). |

## What it does

By default, `fslab clean` removes from the current project:

- `generated-src/` — FIRRTL / Verilog / Golden Gate output
- `build/` — CMake build tree and the compiled simulator/driver

With `--all`, it additionally removes `.fslab/`, which holds the configuration hash and the logs. Because the next {doc}`generate` decides whether to re-render by comparing hashes, removing `.fslab/` forces a full regeneration on the next build.

`fslab clean` does not touch your inputs — `user_rtl/`, `payloads/`, and `fslab.yaml` are left alone. It also reports when there is nothing to remove.

```bash
fslab clean          # remove generated-src/ and build/
fslab clean --all    # also remove .fslab/ (forces full regenerate next time)
```

:::{note}
For FPGA work, `fslab clean` is broader than {doc}`abandon`. `fslab abandon build` clears only the remote-build layer and deliberately keeps the compile layer for `--skip-compile`; `fslab clean` removes the whole `build/` tree. Note also that `fslab clean` does not run any remote cleanup — terminate remote instances with {doc}`abandon` first if a build or run is still in flight.
:::

## Related

- {doc}`abandon` — remote-aware teardown of an in-flight build/run.
- {doc}`generate` — regenerates after a clean.
- {doc}`archive` — snapshot the project (excludes build artefacts).
