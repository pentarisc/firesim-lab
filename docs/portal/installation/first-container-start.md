# First Container Start

You have installed firesim-lab (see {doc}`index`) and `firesim-lab --help` works. This page walks through what happens the first time you actually launch the container in a workspace, so nothing the launcher prints is a surprise — and so you know how to verify the toolchain is wired up before you start building designs.

Run everything here from your **workspace directory** — your terminal on Linux/macOS, or your WSL2 shell on Windows.

## Launch

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

The launcher resolves its own install directory, ensures the `.aws` and `.ssh` directories exist, and treats your current directory as the workspace that will be bind-mounted to `/target`. From here, the first run differs from later runs only in that it *prompts* you and writes a settings file; subsequent runs read that file silently.

## Step 1 — Workspace project scan

The launcher first scans the workspace for an existing firesim-lab project (it looks for a `build.sbt` containing `FIRESIM_LAB_ROOT` in any immediate subdirectory):

- **No project found** — expected on a brand-new workspace. The launcher notes that you can scaffold one with `fslab new` once you are inside.
- **Exactly one project** — it asks `Use this project? [Y/n]` and, if you confirm, marks it active.
- **Multiple projects** — only one project per workspace is supported, so it lists them and asks you to pick one.

The selected project is used only for the on-entry banner and the post-start hints; it does not lock you into anything.

## Step 2 — First-run prompts

On a fresh workspace (no `.firesim-lab.env` yet, or when you pass `--reconfigure`) the launcher asks a short series of questions. Press Enter to accept the bracketed default:

- **`Docker image name:tag`** — default `pentarisc/firesim-lab:latest`. The pinned image to run.
- **`Verilator parallel jobs`** — default is your host's logical core count. Controls Verilator's build parallelism.
- **`Enable custom Python plugins (Security Risk)?`** — default `n`. Leave this off unless you specifically need user plugins; enabling it lets the framework load arbitrary local Python.

Memory limits and the cache GID are *not* prompted — they take their defaults (`16g` ceiling / `8g` reserve, GID `2543`) and can be overridden via environment variables if you ever need to.

The launcher then writes your answers, your host UID/GID, and the per-UID cache volume names into `.firesim-lab.env` in the workspace, and prints a configuration summary showing the host↔container mappings (workspace → `/target`, AWS dir → `~/.aws`, SSH dir → `~/.ssh`). That file is reused on every later run — see {doc}`mountpoints` for its contents.

## Step 3 — Image check and pull

The launcher checks whether the chosen image is present locally:

- **Found locally** — it proceeds straight to starting the container.
- **Not found** — it offers to pull it (`Pull it now? [Y/n]`). The first pull downloads the full toolchain image and can take a while on a slow connection; later runs skip this. If a pull is unavailable, Compose will attempt a local build from the bundled `Dockerfile` as a fallback.

You can force a fresh pull at any time with `firesim-lab --pull`.

## Step 4 — Container start and UID mapping

With the image in place, the launcher brings the container up with Docker Compose (`docker compose up -d`) and waits for it to report ready. As it starts, the container's entrypoint — running briefly as root — performs the UID/GID setup that makes file ownership "just work":

1. It reads the UID/GID that **owns `/target`** and creates matching `/etc/passwd` / `/etc/group` entries for that user.
2. It adds that user to the `firesim-lab-cache` group so the pre-warmed cache volumes are writable.
3. It drops privileges with `gosu` and hands control to your shell.

The practical upshot: inside the container you are a normal non-root user whose UID matches yours on the host, so everything written to `/target` is owned by you back on the host. (If `/target` is root-owned — typically a `/mnt/c/...` workspace on Windows — the container runs as root instead; {doc}`index` explains how to avoid that.)

## Step 5 — You're in the shell

The launcher drops you into an interactive shell with a firesim-lab banner, a quick-reference of the core `fslab` commands, and AWS/SSH reminders. Your prompt looks like:

```text
(firesim-lab /target) $
```

You are now on Linux with the full toolchain on `PATH`. The container keeps running in the background; typing `exit` leaves the shell but **does not stop the container** — re-run `firesim-lab` from the same workspace to re-enter instantly.

## Step 6 — Verify the toolchain

Before scaffolding a design, confirm the environment is wired up. Inside the container shell:

```bash
fslab --help          # the firesim-lab CLI is on PATH
echo "$FIRESIM_ROOT $FIRESIM_LAB_ROOT $TARGET_ROOT"
                      # → /opt/firesim /opt/firesim-lab /target
whoami                 # a non-root user (e.g. firesim-lab-user), not root
ls /target             # your workspace contents, as on the host
```

If `fslab --help` prints usage, the two read-only tiers resolve, and `whoami` is not `root`, the container is healthy and you can move on to {doc}`/quickstart/index`.

:::{tip}
If `whoami` reports `root`, your workspace is on a root-owned path (most often `/mnt/c/...` on Windows). Exit, move the workspace under your user's home — under the WSL2 `~` on Windows — and relaunch. See {doc}`index`.
:::

## Launcher lifecycle commands

The first run is interactive; day-to-day you use the launcher's subcommands from the host. All are run from the workspace directory:

| Command | What it does |
|---|---|
| `firesim-lab` | Start the container, or enter it if already running |
| `firesim-lab --down` | Stop and remove the container for this workspace |
| `firesim-lab --pull` | Pull the latest image and restart |
| `firesim-lab --reconfigure` | Re-prompt the first-run settings and rewrite `.firesim-lab.env` |
| `firesim-lab --upgrade` | Re-pin this workspace to the installed firesim-lab version (see {doc}`versioning`) |
| `firesim-lab --status` | Show the container's status for this workspace |
| `firesim-lab --clean-cache` | Remove this workspace's SBT/ccache volumes (forces re-seed) |
| `firesim-lab --help` | Show usage |

Because each workspace has its own container and its own `.firesim-lab.env`, these commands always act on the workspace you run them from — multiple workspaces stay independent.

## What's next

- {doc}`/quickstart/index` — scaffold and simulate your first Verilog/SystemVerilog design.
- {doc}`mountpoints` — the full host↔container mapping and environment-variable reference.
- {doc}`/setup/aws/index` — only if you intend to run on AWS F2 hardware.
