# fslab monitor

Attach to an in-flight background **build** or detached **run** and follow its progress. Both subcommands read the local stamp the launch wrote, connect to the remote, and either tail the live log or summarise an already-finished job.

`fslab monitor` is how you reconnect after `fslab build fpga --detach` or `fslab sim fpga --detach`, after a `Ctrl+C` detach, or simply from another shell.

## Synopsis

```bash
fslab monitor build [-c <path>]
fslab monitor run   [-c <path>]
```

| Option | Default | Description |
|---|---|---|
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML. |

## `fslab monitor build`

Attaches to this project's bitstream build, using the stamp at `build/fpga/.fslab/build.yaml`. Depending on the build's phase it will:

- tail the remote wrapper's log while the build is still running;
- poll AFI status if the wrapper has exited but the AGFI is still being created (the post-wrapper phase);
- print a summary if the build already reached a terminal state (succeeded / failed / abandoned).

## `fslab monitor run`

Attaches to this project's detached FPGA run, using the stamp at `run/fpga/.fslab/run.yaml`. It will:

- tail the wrapper's `driver.log` while the run is in progress;
- pull results and release the host if the wrapper has exited (writing a terminal stamp);
- print a summary if the run already reached a terminal state.

## Detaching

Press `Ctrl+C` to detach. The remote build or run is `nohup`'d and keeps going — detaching never kills it. Reattach any time by running the same `fslab monitor` command again.

```bash
fslab build fpga --detach     # launch, return immediately
fslab monitor build           # attach and watch
# ...Ctrl+C to detach; the build keeps running...
fslab monitor build           # reattach later
```

## Related

- {doc}`build` — launching a background FPGA build (`fslab build fpga`).
- {doc}`sim-fpga` — launching a detached run (`fslab sim fpga --detach`).
- {doc}`abandon` — tear a build/run down instead of watching it.
