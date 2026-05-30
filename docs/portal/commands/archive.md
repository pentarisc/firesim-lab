# fslab archive

Create a `.tar.gz` snapshot of the project, excluding large build artefacts. Useful for sharing a project, attaching it to a bug report, or capturing a milestone.

## Synopsis

```bash
fslab archive -t <tag> [-c <path>] [-o <dir>]
```

## Options

| Option | Default | Description |
|---|---|---|
| `-t`, `--tag <label>` | *(prompted)* | Label for this snapshot (e.g. `milestone-v1`). If omitted, you are prompted for it. |
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML; its directory is the archive root. |
| `-o`, `--output <dir>` | `archives` | Directory to write the archive into (created if needed). |

## What it does

`fslab archive` walks the project directory and writes a gzip tarball named `<project>-<tag>-<timestamp>.tar.gz` into the output directory. To keep snapshots small, it skips files matching ignore patterns:

- It reads `.fslabignore` if present, otherwise falls back to `.gitignore`.
- The `archives/` directory itself and `.fslab/logs/` are always excluded.

So with the default scaffold's `.gitignore`, `generated-src/`, `build/`, `target/`, and `payloads/*` are left out — keeping a 50 GB FPGA build tree out of your snapshot — while your `fslab.yaml`, `user_rtl/`, and driver sources are included. On completion it reports the file count and size.

```bash
fslab archive -t milestone-v1
# → archives/uart-print-test-milestone-v1-20260530T120000.tar.gz
```

## Related

- {doc}`clean` — delete artefacts instead of snapshotting around them.
- {doc}`new` — the `.gitignore` that drives the default exclusions.
