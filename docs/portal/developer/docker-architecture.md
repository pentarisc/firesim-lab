# Container Architecture

The entire firesim-lab toolchain — Scala/SBT, Verilator, the FireSim and
firesim-lab source trees, the Python `fslab` CLI, and the FPGA tooling — ships
inside a single container image (a standard OCI image, published to Docker Hub).
You never install any of it on the host. This page is a reference for how that
image is structured, how the two compose files differ, and — the part that
matters most if you are extending the framework — **how to iterate on the CLI
and on bridges locally without pushing anything to the firesim-lab repository.**

```{note}
"Docker" appears throughout this page because the image is built and published
via Docker, and local development (below) is a Docker-only workflow. The
production runtime — the `firesim-lab` launcher and `docker-compose.yaml` — also
runs under Podman, nerdctl, and Finch; see {ref}`container-runtime-support` below.
```

This page assumes you already know how to *use* the container as an end user. If
you do not, read {doc}`/installation/host-vs-container` and
{doc}`/installation/mountpoints` first; they cover the runtime user model and the
bind mounts from a user's perspective. Here we look at the same machinery from a
contributor's side.

## The three tiers

At runtime the container always has three source tiers present:

| Tier | Path | Origin | Ownership |
|---|---|---|---|
| 1 | `/opt/firesim` | Baked into the image (cloned at build time) | root, world-readable |
| 2 | `/opt/firesim-lab` | Baked into the image (cloned at build time) | root, world-readable |
| 3 | `/target` | Bind-mounted from the host (your project) | host user |

Tier 1 and Tier 2 are *baked in* — they are part of the published image and the
end user never edits them. Tier 3 is *your project*, mounted read-write so
generated sources and build artifacts persist on the host. The whole point of
dev mode (covered below) is to turn Tier 2 into a fourth, mutable thing: your
local clone, bind-mounted over `/opt/firesim-lab` so you can edit framework code
without rebuilding the image.

The files that define all of this live under [docker/](https://github.com/pentarisc/firesim-lab/tree/main/docker):

| File | Role |
|---|---|
| `Dockerfile` | Four-stage image build |
| `docker-compose.yaml` | Production runtime — driven by the `firesim-lab` launcher |
| `docker-compose-dev.yaml` | Local-development runtime — bind-mounts your clone |
| `entrypoint.sh` | Runtime UID/GID remapping; runs as root, drops privileges |
| `firesim-lab-shell` | Privilege-dropping shell for exec sessions (`docker exec`, `podman exec`, ...) |
| `firesim-lab` | Host-side launcher script (detects the container runtime, writes `.firesim-lab.env`, runs compose) |
| `install.sh` | Host-side installer that lays down the launcher and config dirs |
| `firesim-requirements.txt` | Python deps installed into the image's venv |

## Image build: the four stages

The `Dockerfile` is a multi-stage build. Each stage produces the input for the
next; only the final stage is published.

### Stage 1 — `base`

Starts from `ubuntu:24.04` and installs the OS-level toolchain: Java (OpenJDK 17
by default), the SBT launcher, Verilator, `ccache`, `g++`/`make`/`cmake`, Python
3 with `venv`, the AWS CLI v2, and `gosu` (the privilege-dropping helper used by
the entrypoint). It also establishes the **user and cache-group model** that the
rest of the image depends on:

- A fixed build-time user `firesim-lab` (UID/GID 1000), used only to pre-warm
  caches during the build. It is *not* the user you run as.
- A shared group `firesim-lab-cache` (GID `CACHE_GID`, default `2543`) that owns
  every cache directory `2775` (setgid + group-writable). The setgid bit means
  files created at runtime — by whatever host UID you happen to be — inherit the
  cache group automatically, so no per-start `chown` is ever needed.

The cache directories (`~/.ivy2`, `~/.cache/coursier`, `~/.cache/ccache`,
`~/.sbt/boot`, `~/.sbt/1.0`) all live under the fixed home `/home/firesim-lab`.
`HOME` is pinned to that path so SBT, `ccache`, and `pip` resolve `~` to the
pre-warmed locations regardless of the running UID.

### Stage 2 — `firesim`

Clones FireSim at the pinned `FIRESIM_COMMIT` into `/opt/firesim`, initializes
submodules, and swaps the upstream `aws-fpga-firesim-f2` submodule for the
pentarisc fork (needed for Ubuntu 24.04 / AWS F2 support). It stubs out
`env.sh` so the heavy `build-setup.sh` is skipped, then runs the F2 SDK/HDK
setup. This tree is root-owned and world-readable; end users only read it.

### Stage 3 — `firesim-lab`

Clones firesim-lab at the pinned `FIRESIM_LAB_COMMIT` into `/opt/firesim-lab`,
creates the Python virtual environment at `/opt/firesim-venv`, and installs the
Python dependencies in two passes:

1. `firesim-requirements.txt` (FireSim's build/runtime Python deps).
2. `fslab-cli/requirements.txt`, followed by an **editable install** of the CLI
   itself: `pip install -e fslab-cli`.

That editable install is the hinge for CLI development. Because the package is
installed in editable mode, the `fslab` entry point resolves to the source under
`/opt/firesim-lab/fslab-cli` — so when dev mode bind-mounts your clone over
`/opt/firesim-lab`, your CLI edits take effect immediately, with no reinstall.

This stage also generates the `fslab` bash-completion script into
`/etc/bash_completion.d/`, then `chown`s the whole `/opt/firesim-lab` and
`/opt/firesim` trees to `firesim-lab:firesim-lab-cache` with the setgid bit, so
the next stage's `sbt assembly` can create `target/` directories anywhere in the
tree.

### Stage 4 — `final`

Switches to the build-time `firesim-lab` user and runs `sbt assembly` once. This
does the expensive work up front — resolving every Ivy/Coursier dependency and
producing the assembly JAR — so that the resulting artifacts and resolver caches
are **baked into the image layer**. A post-build pass fixes group ownership and
permissions on all generated `target/` content (some tools, like zinc, write
`600` files that bypass the umask), so any runtime UID in the cache group can
reuse the incremental-compile caches. Finally it installs `entrypoint.sh` and
`firesim-lab-shell` and sets `ENTRYPOINT`.

### Build arguments

All four stages are parameterized:

| Arg | Default | Purpose |
|---|---|---|
| `FIRESIM_COMMIT` | `main` | Git ref for FireSim |
| `FIRESIM_LAB_COMMIT` | `main` | Git ref for firesim-lab |
| `AWS_FPGA_F2_REF` | `main` | Git ref for the F2 aws-fpga fork |
| `SBT_VERSION` | `1.10.1` | SBT launcher version |
| `JAVA_VERSION` | `17` | OpenJDK major version |
| `CACHE_GID` | `2543` | GID of the `firesim-lab-cache` group |

`CACHE_GID` is the only one you might need to override, and only when rebuilding
locally on a host where GID 2543 already collides with an existing group. If you
pull the published image, the runtime `CACHE_GID` must match the value baked in.

## What is baked in vs mounted

Read this distinction carefully — it explains both why end users never rebuild
and why bridge changes sometimes do require a rebuild.

**Baked into the image** (immutable, shipped):

- `/opt/firesim` and `/opt/firesim-lab` source trees at their pinned commits.
- The Python venv with `fslab` installed editable.
- The *seed* contents of the cache directories (Ivy, Coursier, ccache, sbt boot)
  produced by Stage 4's `sbt assembly`.

**Mounted at runtime** (mutable, per host/user):

- `/target` — your project (always).
- The named volumes — `sbt-ivy-cache`, `sbt-coursier`, `sbt-boot`, `sbt-global`,
  `verilator-ccache` — which are *seeded* from the image's cache layers on first
  use and then grow incrementally and persist across restarts.
- `~/.aws` and `~/.ssh` — bind-mounted so AWS and SSH state persist on the host.
- In **dev mode only**: your local firesim-lab clone over `/opt/firesim-lab`.

```{note}
Named volumes are seeded from the image only when they are *empty*. After a major
SBT version bump, a stale `sbt-boot` / `sbt-global` volume can cause version
conflicts; clear and re-seed them with `firesim-lab --clean-cache`.
```

## Runtime user model

The image is generic: built once, pushed to Docker Hub, and shared by every host
and every UID. There is no `user:` override in either compose file. Instead
`entrypoint.sh` runs as root and:

1. Detects `HOST_UID`/`HOST_GID` from the ownership of the `/target` bind mount
   (not from environment variables — `/target` ownership is always correct).
2. Appends transient `/etc/passwd` and `/etc/group` entries for that UID/GID,
   with `HOME=/home/firesim-lab`.
3. Adds the pseudo-user to `firesim-lab-cache` so it can write the cache dirs.
4. Sets `umask 002` and drops privileges with `gosu`.

`firesim-lab-shell` exists for the same reason but for exec sessions: `docker
exec -u <uid>` (and the Podman/nerdctl equivalents) skip `initgroups()`, so the
`firesim-lab-cache` supplementary group would be missing; `gosu` calls
`initgroups()` correctly. This is why interactive shells go through
`firesim-lab-shell` (or, in raw dev mode, `docker exec --user firesim-lab`).

## The two compose files

Both files describe the same service and the same image. The difference is what
gets mounted and how the container is launched.

| | `docker-compose.yaml` (production) | `docker-compose-dev.yaml` (dev) |
|---|---|---|
| Launched by | The `firesim-lab` host launcher | You, manually with `docker compose -f ...` |
| Container name | `firesim-lab` | `firesim-lab-dev` |
| `/opt/firesim-lab` | Image-baked Tier 2 | **Bind-mounted from your local clone** |
| `HOST_WORKSPACE_DIR` | Required (from `.firesim-lab.env`) | Defaults to a local test project |
| AWS / SSH dirs | From `HOST_AWS_DIR` / `HOST_SSH_DIR` | Fall back to `~/.firesim-lab/.{aws,ssh}` |

Everything else — the named volumes, the environment block, the resource limits,
the entrypoint behavior — is identical. The dev file is deliberately a thin
overlay: it changes *who provides Tier 2* and nothing structural.

(container-runtime-support)=
## Container runtime support

`docker-compose.yaml` — the production file, driven by the `firesim-lab`
launcher — runs under **Docker, Podman, and nerdctl** (rootful); the launcher
auto-detects whichever is on `PATH` (override with `--runtime=<name>` or
`FIRESIM_RUNTIME=<name>`) and pins the choice per-workspace as
`CONTAINER_RUNTIME` in `.firesim-lab.env`. See
{doc}`/setup/host-prerequisites` for per-runtime setup notes.

`docker-compose-dev.yaml` — the local-development workflow below — is
**Docker-only** by design: it is always invoked directly (`docker compose -f
docker-compose-dev.yaml ...`), never through the launcher, and its AWS/SSH
directory fallbacks rely on `docker compose`'s variable interpolation. There is
no need to run local development under another runtime, since the point is to
iterate on source that later ships in the one published image regardless of
which runtime end users run it under.

The single meaningful addition in the dev file is this mount:

```yaml
# Tier 3: override /opt/firesim-lab with your own for development purposes
- type: bind
  source: ${HOME}/pentarisc/projects/firesim-lab
  target: /opt/firesim-lab
```

This bind mount *shadows* the image-baked Tier 2. Everything under your clone —
`fslab-cli/` (the CLI), `lib/bridges/` (the Chisel/Scala + C++ bridge sources),
the Jinja2 templates, the registry — is now what the container compiles and runs.

```{warning}
The `source:` path is hard-coded to `${HOME}/pentarisc/projects/firesim-lab`.
If your clone lives elsewhere, edit that line (and the `HOST_WORKSPACE_DIR`
default) before launching dev mode. These paths are developer conveniences, not
part of the published configuration.
```

## Local development workflow

This is the section to internalize if you are extending firesim-lab. There are
two distinct activities — CLI work and bridge work — and they have different
iteration costs.

Launch the dev container once:

```bash
cd ~/pentarisc/projects/firesim-lab/docker
docker compose -f docker-compose-dev.yaml up -d
docker exec --user firesim-lab -it firesim-lab-dev bash
```

You are now inside the container with your local clone mounted at
`/opt/firesim-lab`. The `/target` workspace defaults to a local test project; set
`HOST_WORKSPACE_DIR` before `up -d` to point at a different project.

### CLI development — zero-rebuild

Because the CLI is installed editable (Stage 3) and your clone is mounted over
`/opt/firesim-lab`, **changes to any Python under `fslab-cli/fslab/` take effect
on the next `fslab` invocation.** Edit on the host in your editor, rerun `fslab`
in the container shell, done. No image rebuild, no `pip install`, no container
restart. The same applies to the Jinja2 templates and the registry YAML — they
are read from your mounted clone at run time.

The only time you restart the container is if you change something compose reads
at launch (environment variables, the workspace path, mounts).

### Bridge development — rebuild the Scala/C++, not the image

A bridge is not pure Python. Adding or changing one touches Chisel/Scala
(target interface, stub, host model), C++ (the driver), Jinja2 templates, and
the registry — see {doc}`/developer/bridges/adding-new-bridges` for the full
recipe. The Python/template/registry parts behave exactly like CLI work above:
edit-and-rerun, no rebuild. The Scala and C++ parts must be **recompiled**, but
still inside the running dev container — *not* by rebuilding the image:

- **Scala/Chisel** changes are picked up by the next `fslab build` (or a direct
  `sbt` invocation), which compiles from your mounted source tree. The first
  build in dev mode is slower because your clone's `target/` shadows the
  image-baked one and zinc recompiles; subsequent incremental builds are fast and
  reuse the named-volume Ivy/Coursier caches.
- **C++ driver** changes are recompiled by the metasim/driver build step, reusing
  the `verilator-ccache` volume.

So the loop for a new bridge is: edit Scala/C++/templates/registry in your clone
on the host → `fslab build` inside the dev container → `fslab sim`. Nothing is
pushed and the image is never rebuilt.

### When you must edit `docker-compose-dev.yaml`

The dev compose only mounts the firesim-lab repo root. You edit it when your work
needs paths or settings *outside* that mount:

- Your clone lives somewhere other than `${HOME}/pentarisc/projects/firesim-lab`
  (change the `source:` of the Tier 2 bind mount).
- A bridge needs an additional host directory mounted into the container (extra
  RTL, firmware images, test vectors) — add a bind mount.
- You need a different default workspace, AWS/SSH directory, Verilator thread
  count, or memory limit for your dev sessions.

### When you must edit the `Dockerfile`

Bind-mounting handles *source* iteration. You only fall back to a real image
rebuild when the change must exist at **image-build time** or below the source
layer:

- **New system or Python dependencies.** A bridge that needs an extra apt package
  or Python library has to have it installed in the image. Add it to the relevant
  `apt-get`/`pip` step (or to `firesim-requirements.txt`) and rebuild. A bind
  mount cannot add packages.
- **Validating the production build path before pushing.** The published image
  *clones* firesim-lab from GitHub at `FIRESIM_LAB_COMMIT` (Stage 3) and runs
  `sbt assembly` once to pre-warm caches (Stage 4). Dev mode bypasses both by
  mounting your clone, so it never exercises the clone-and-assemble path your
  bridge will actually ship through. To test that path with *local, unpushed*
  code, temporarily replace the `git clone` in Stage 3 with a `COPY` of your
  working tree (or rebuild with `FIRESIM_LAB_COMMIT` pointed at a local branch
  you have pushed to a throwaway remote), then `docker build` and run the
  *production* `docker-compose.yaml` against it. This confirms your bridge
  survives a clean assembly and that its dependencies are all declared — the
  things dev mode's bind mount silently papers over.

```{tip}
Rule of thumb: if your change is **source code** (Python, Scala, C++, templates,
YAML), dev-mode bind-mounting is enough and you never rebuild. If your change is
to the **environment** (packages, the build sequence, what gets baked in), edit
the `Dockerfile` and rebuild the image.
```

### Promoting a change

Once a bridge or CLI change works in dev mode, ship it the normal way: push the
firesim-lab changes to the repository, then rebuild and publish the image so the
new commit is baked in at `FIRESIM_LAB_COMMIT`. End users pull the new image and
get your work with no dev-mode setup. The dev compose file's bind mount is purely
a local convenience and is never part of what users run.

## See also

- {doc}`/installation/host-vs-container` — the runtime user/tier model from a
  user's perspective.
- {doc}`/installation/mountpoints` — what each mount is for at runtime.
- {doc}`/developer/bridges/adding-new-bridges` — the end-to-end bridge recipe
  that this dev loop supports.
- {doc}`/developer/fslab-python/index` — the CLI internals you iterate on in dev
  mode.
