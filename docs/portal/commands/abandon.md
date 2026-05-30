# fslab abandon

Tear down a build or run: clean up the remote resource it launched and clear the local state so you can start fresh. This is the escape hatch when a build/run aborted partway, when you want to walk away from a long job, or when a corrupt stamp is blocking a new launch.

`fslab abandon` does **not** start anything new — run {doc}`build` or {doc}`sim-fpga` afterward when you are ready.

## Synopsis

```bash
fslab abandon build [-c <path>]
fslab abandon run   [-c <path>]
```

| Option | Default | Description |
|---|---|---|
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML. |

## `fslab abandon build`

Runs cleanup against the remote resource recorded in the build stamp (terminate/stop the EC2 instance), deletes the local stamp, and removes the **remote-build-layer** artefacts:

- `build/fpga/.fslab/` (stamp + monitor-pulled wrapper output)
- `build/fpga/reports/` (pulled Vivado reports)
- `build/fpga/results-build/`
- `.fslab/logs/fpga-build-*.log`

The **compile layer** is intentionally preserved (`generated-src/`, the rest of `build/`, the `build/fpga/cl_<…>/` staging tree, and `.fslab/state.json`) so a subsequent `fslab build fpga --skip-compile` can reuse it.

## `fslab abandon run`

Runs cleanup against the remote run host recorded in the run stamp, then wipes the local stamp and the just-in-time staging directory. Prior results under `run/fpga/results/` are **preserved** — they are append-only forensic records. Remove them with {doc}`clean` or by hand if you want them gone.

## Safety

Cleanup is idempotent — terminating an already-terminated instance is a no-op — so re-running `fslab abandon` after a partial failure is safe. If remote cleanup **fails** (for example, an expired SSO session), the local stamp is deliberately left in place so you can retry rather than orphan a possibly-still-billing remote instance. Resolve the error (e.g. `aws sso login`) and re-run.

```bash
fslab abandon build      # kill the remote build, clear remote-build state
fslab abandon run        # kill the remote run, clear stamp + staging
```

## Related

- {doc}`monitor` — attach and watch instead of tearing down.
- {doc}`build` — `--skip-compile` reuses the preserved compile layer after `abandon build`.
- {doc}`clean` — the broader local wipe (compile layer included).
