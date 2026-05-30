# Mountpoints

This page is the reference for how the host and container are wired together: which host directories appear where inside the container, which Docker-managed volumes hold the caches, and the environment variables that control it all. The mounts are defined in `docker/docker-compose.yaml` (the production Compose file shipped by `install.sh`); the launcher fills in the host-specific paths through a per-workspace settings file.

:::{note}
This page documents the **production** Compose file that installed users run. Contributors who build the image locally use `docker/docker-compose-dev.yaml`, which adds developer mounts (source trees, the in-repo `fslab` CLI) — that file is covered in the {doc}`/developer/index` chapter, not here.
:::

## The mount map at a glance

When the container starts, the launcher (`firesim-lab`) invokes Docker Compose with three bind mounts and five named volumes:

| Host source | Container target | Type | Purpose |
|---|---|---|---|
| `HOST_WORKSPACE_DIR` (your workspace) | `/target` | bind (rw) | Your projects, sources, and all build outputs |
| `HOST_AWS_DIR` (`<install>/.aws`) | `/home/firesim-lab/.aws` | bind (rw) | AWS credentials/config, persisted to the host |
| `HOST_SSH_DIR` (`<install>/.ssh`) | `/home/firesim-lab/.ssh` | bind (rw) | SSH keys and `known_hosts` |
| `sbt-ivy-cache` | `/home/firesim-lab/.ivy2` | volume | SBT/Ivy artifact cache |
| `sbt-coursier` | `/home/firesim-lab/.cache/coursier` | volume | Coursier resolver cache |
| `sbt-boot` | `/home/firesim-lab/.sbt/boot` | volume | SBT launcher + Scala distributions |
| `sbt-global` | `/home/firesim-lab/.sbt/1.0` | volume | SBT global plugins/settings |
| `verilator-ccache` | `/home/firesim-lab/.cache/ccache` | volume | ccache for Verilator-generated C++ |

Inside the container, `HOME` is fixed to `/home/firesim-lab` for *every* host user. That is deliberate: SBT resolves `~/.ivy2` and `~/.sbt`, ccache resolves its cache, pip resolves its cache, and the AWS CLI resolves `~/.aws` — all from `HOME`. Pinning it means those tools always land on the pre-warmed caches and the bind-mounted credentials, regardless of which host UID is running.

## The workspace bind mount — `/target`

Your workspace directory is bind-mounted read-write at `/target`, and the container's working directory starts there. This is Tier 3 from {doc}`host-vs-container` — the only writable tier and the only place your data lives.

- One workspace maps to one `/target`, and the launcher derives a container name from the workspace's basename, so **each workspace gets its own container**. You can run several side by side.
- Everything under the workspace persists on the host automatically: scaffolded projects, `generated-src/`, `build/`, `run/`. Stopping or removing the container never touches `/target`.
- The container's **runtime user is detected from the ownership of `/target`**. The entrypoint reads the UID/GID that owns the mount and runs as that user via `gosu`, so files you create in the container are owned by *you* on the host. If `/target` is root-owned — most commonly a `/mnt/c/...` path on Windows — the container falls back to running as root. Keep your workspace under your real user's home (and, on Windows, under the WSL2 `~`) so UID mapping works. See {doc}`index` for the Windows specifics.

## AWS and SSH bind mounts

`install.sh` creates two empty, mode-`700` directories under the install location and the launcher bind-mounts them at the conventional in-container paths:

- **`<install>/.aws` → `~/.aws`.** Run `aws configure sso` / `aws sso login` *inside* the container; the resulting credentials and config are written straight back to the host directory, so they survive container restarts and you never install the AWS CLI on the host. Account setup is in {doc}`/setup/aws/index`.
- **`<install>/.ssh` → `~/.ssh`.** Place private keys (e.g. AWS `.pem` files) here and `chmod 600` them so OpenSSH accepts them. The mount is read-write so `ssh` can update `known_hosts` on first connect; individual keys stay protected by their own permissions.

Both directories are anchored to the install dir rather than your host `~`, so they never collide with a host-side `~/.aws` or `~/.ssh`. The default install location is `~/.firesim-lab`, giving `~/.firesim-lab/.aws` and `~/.firesim-lab/.ssh`; a custom `INSTALL_DIR` moves them with it.

## The cache volumes

The five SBT/Coursier/ccache mounts are **Docker named volumes**, not bind mounts. Docker manages them under `/var/lib/docker/volumes/`, they are seeded from the image on first use, and they persist across container restarts so dependency downloads and C++ compilation are not repeated.

- Volume **names embed the host UID** (e.g. `firesim-lab-sbt-ivy-1000`), so multiple users on the same Docker host keep independent caches.
- The cache directories inside the image are `2775` (setgid + group-writable) and owned by the `firesim-lab-cache` group (GID `2543` by default). The entrypoint adds your container user to that group at start-up, and the setgid bit makes new files inherit the group — so the caches stay writable across runs **without any per-start `chown`**.
- To wipe and re-seed the caches for the current workspace, run `firesim-lab --clean-cache` on the host. This is the fix for stale-cache problems, e.g. after a major SBT version bump.

:::{warning}
`firesim-lab --clean-cache` only removes the **cache volumes**. It never touches `/target`, so your projects and build outputs are safe. Conversely, deleting these volumes by hand forces a full re-download of SBT/Ivy/Coursier artifacts on the next build.
:::

## The settings file — `.firesim-lab.env`

The first time you run `firesim-lab` in a workspace, it writes a `.firesim-lab.env` file *into that workspace*. It records your answers to the first-run prompts plus the derived host identity and volume names, and it is read silently on every later run. It is **workspace-specific — do not copy it between workspaces**. Re-run `firesim-lab --reconfigure` to regenerate it. The first-run flow that produces it is described in {doc}`first-container-start`.

## Environment-variable reference

The launcher sets these for you; you should not normally need to touch them. They are split into the variables visible *inside* the container and the host-side variables the launcher writes into `.firesim-lab.env`.

### Inside the container

| Variable | Default | Description |
|---|---|---|
| `HOME` | `/home/firesim-lab` | Fixed in-container home, so SBT/ccache/pip caches and `~/.aws` resolve consistently for any UID |
| `FIRESIM_ROOT` | `/opt/firesim` | Tier 1 — pinned FireSim checkout (read-only) |
| `FIRESIM_LAB_ROOT` | `/opt/firesim-lab` | Tier 2 — this repo, baked into the image (read-only) |
| `TARGET_ROOT` | `/target` | Tier 3 — bind-mounted workspace from the host |
| `SBT_OPTS` | `-Xmx8g -Xss8m …` | JVM options for SBT (memory + non-interactive shell) |
| `VERILATOR_THREADS` | host core count | Verilator parallel-job count; prompted on first run, saved in the env file |
| `ENABLE_CUSTOM_PLUGINS` | `0` | Opt-in for loading user Python plugins (security-sensitive) |
| `CACHE_GID` | `2543` | GID of the in-image `firesim-lab-cache` group that owns the cache volumes |

### Host-side (written to `.firesim-lab.env`)

| Variable | Description |
|---|---|
| `FIRESIM_IMAGE` | Pinned image tag (default `pentarisc/firesim-lab:latest`) |
| `CONTAINER_NAME` | Derived from the workspace basename; one container per workspace |
| `HOST_WORKSPACE_DIR` | Workspace directory on the host, bind-mounted as `/target` |
| `HOST_AWS_DIR` | Host AWS directory, bind-mounted at `~/.aws` |
| `HOST_SSH_DIR` | Host SSH directory, bind-mounted at `~/.ssh` |
| `HOST_UID`, `HOST_GID` | Your host user's UID/GID, recorded for the cache volume names |
| `CONTAINER_MEMORY_LIMIT` | Docker memory ceiling for the container (default `16g`) |
| `CONTAINER_MEMORY_RESERVE` | Docker memory reservation (default `8g`) |
| `VOLUME_SBT_IVY`, `VOLUME_SBT_COURSIER`, `VOLUME_SBT_BOOT`, `VOLUME_SBT_GLOBAL`, `VOLUME_CCACHE` | Per-UID cache volume names referenced by Compose |

:::{note}
Although the entrypoint detects the runtime UID/GID from `/target` ownership at container start, the launcher also records `HOST_UID`/`HOST_GID` in `.firesim-lab.env` — these drive the per-user cache **volume names**, not the privilege drop.
:::

Most of these can be overridden by exporting the variable before running `firesim-lab` (handy in CI), but the defaults are correct for normal use. To raise the container's memory ceiling for large designs, set `CONTAINER_MEMORY_LIMIT` (and, on Windows, the WSL2 allocation — see {doc}`/setup/host-prerequisites`).

## Where to go next

- {doc}`first-container-start` — the first-run walkthrough that writes `.firesim-lab.env` and starts the container.
- {doc}`host-vs-container` — the conceptual host/container split behind these mounts.
