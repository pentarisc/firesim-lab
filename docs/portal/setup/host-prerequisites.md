# Host Prerequisites

firesim-lab ships its entire toolchain — Scala/SBT, Verilator, the FireSim Python environment, FPGA tooling — inside a single pinned container image. The host machine therefore stays deliberately thin: essentially just a **container runtime**. Everything heavy runs in the container.

The `firesim-lab` launcher auto-detects whichever runtime is on your `PATH` — **Docker**, **Podman**, or **nerdctl** (containerd) — so this page covers all three. Docker remains the best-tested, zero-extra-setup path; Podman and nerdctl are supported in **rootful** mode (see below for the one-time setup each needs).

This page is the checklist to get your host ready *before* you install firesim-lab. For the actual install and first container start, continue to {doc}`/installation/index`. For why the toolchain is containerized at all and what lives where, see {doc}`/installation/host-vs-container`.

## Supported platforms

Because the entire toolchain lives in a Linux container, every supported platform converges on the same thing: a Linux shell with a container runtime. Windows reaches that through WSL2; Linux and macOS get there directly. Once you are at that shell, the install and the `firesim-lab` workflow are **identical on all three** — the same `install.sh`, the same launcher.

- **Linux** — any modern distribution with a current Docker Engine, or rootful Podman/nerdctl (see below). The primary, most-tested platform, including for the Podman/nerdctl paths.
- **Windows 10 (21H2+) / 11** — via **WSL2** (Windows Subsystem for Linux) with Docker Desktop's WSL2 backend. You install firesim-lab and run it *inside* a WSL2 Linux distro (Ubuntu), so from the container's point of view it is just Linux. There are no Windows-native firesim-lab scripts. Since a WSL2 distro is genuinely Linux, the Podman/nerdctl setup below applies there too, but only Docker Desktop's WSL2 backend has been tested end-to-end on Windows.
- **macOS 12 (Monterey) or newer** — Intel or Apple Silicon, with Docker Desktop for Mac. The standard Linux installer runs directly in Terminal. See {doc}`/setup-options` for alternative macOS/Windows container runtimes (Podman Desktop, Rancher Desktop, Finch) — untested with firesim-lab so far.

## Required software

firesim-lab runs as a Compose service launched from a Linux shell. The prerequisites below get you to that shell on each platform. The detailed, step-by-step setup lives in {doc}`/installation/index` — this page only lists what must be in place.

### Linux and macOS

Two things, in the shell you already have (bash on Linux, zsh on macOS):

Container runtime
: A running **Docker** Engine (Linux) or Docker Desktop (macOS), **or** rootful **Podman**/**nerdctl** (Linux only, see below). The `firesim-lab` launcher auto-detects whichever is on `PATH`; override with `--runtime=<name>` or `FIRESIM_RUNTIME=<name>` if more than one is installed.

**Docker** — the default, zero-extra-setup path:
: ```bash
  docker --version
  docker compose version
  ```
: On Linux, install the Compose plugin (`docker-compose-plugin`) if `docker compose version` errors — the legacy standalone `docker-compose` binary is not required. Add yourself to the `docker` group once so the launcher can invoke it as your normal user (Docker Desktop on macOS handles this for you):
  ```bash
  sudo usermod -aG docker "$USER"   # then log out and back in
  docker run --rm hello-world
  ```

**Podman** (Linux only, rootful) — Podman defaults to a **rootless** backend for any non-root invocation, which firesim-lab does not yet support; you need either `sudo` per invocation, or a one-time setup so your normal user reaches the *rootful* backend without `sudo`:
: ```bash
  sudo systemctl enable --now podman.socket
  sudo groupadd -f podman && sudo usermod -aG podman "$USER"
  sudo mkdir -p /etc/systemd/system/podman.socket.d
  printf '[Socket]\nSocketGroup=podman\nSocketMode=0660\n' | sudo tee /etc/systemd/system/podman.socket.d/override.conf
  sudo systemctl daemon-reload && sudo systemctl restart podman.socket
  echo 'export CONTAINER_HOST=unix:///run/podman/podman.sock' >> ~/.bashrc   # then log out and back in
  ```
: Verify with `podman info --format '{{.Host.Security.Rootless}}'` — it should print `false`.

**nerdctl** (Linux only, rootful, via containerd) — nerdctl's rootful mode requires the invoking process to actually be UID 0; unlike Podman there is no socket-group equivalent for non-root access. Run `firesim-lab` with `sudo`, or configure passwordless `sudo` for your user.

curl
: Used to fetch the installer and download files. Present by default on macOS; install via your package manager on Linux if missing. Verify with `curl --version`.

:::{note}
**Apple Silicon (M-series).** The firesim-lab image is x86-64 (amd64) and runs under emulation on Apple Silicon, which is slower for SBT/Verilator builds. Enable Docker Desktop's **Use Rosetta for x86/amd64 emulation** for the best available speed (Intel Macs run the image natively). This is a performance caveat, not a blocker.
:::

### Windows (WSL2)

On Windows you do not run firesim-lab natively — you run it inside a WSL2 Linux distro, where the Linux prerequisites above apply. What Windows itself must provide:

- **Windows 10 version 21H2 or later, or Windows 11.**
- **Hardware virtualization enabled** in BIOS/UEFI (Intel VT-x / AMD-V, sometimes labelled *Virtualization Technology* or *SVM*). This is the single most common blocker — WSL2 and Docker Desktop will not start without it.
- **Administrator rights** for the one-time `wsl --install`.
- **WSL2 with a Linux distro** (Ubuntu is installed by default by `wsl --install`).
- **Docker Desktop for Windows** with the **WSL2 backend** enabled and **WSL Integration** turned on for your distro, so `docker` is available *inside* the WSL shell.

:::{warning}
Keep your workspace inside the **WSL2 Linux filesystem** (under your Linux home, `~`), **not** on a Windows drive (`/mnt/c/...`). The Windows↔Linux filesystem boundary is slow for the many-small-file operations SBT and Verilator perform, and a `/mnt/c` workspace appears root-owned inside the VM, which forces the container to run as root. A workspace under `~` is both fast and correctly owned. See {doc}`/installation/index` for the full walkthrough.
:::

## Recommended hardware

Chisel elaboration and Verilator compilation are memory- and CPU-intensive. The container defaults to a 16 GB memory ceiling with an 8 GB reservation, so plan accordingly:

- **Memory:** 16 GB of host RAM recommended; 8 GB is a workable floor for small designs. Larger designs benefit from raising the container's memory limit (see the environment-variable reference in {doc}`/installation/index`).
- **CPU:** more physical cores shorten Verilator builds and metasim runs — the container parallelizes Verilator across cores. Four or more is comfortable.
- **Disk:** the image plus the persistent SBT/Ivy/coursier and ccache volumes consume tens of gigabytes. Budget at least ~30 GB of free space to start, more if you keep multiple build artefacts around.

On **Windows**, these limits apply to the resources Docker Desktop's WSL2 backend is allowed to use, not just the container. If builds run short on memory, raise the WSL2 allocation (via a `.wslconfig` file in your user profile) so the container's 16 GB ceiling actually fits.

## Network access

The host needs outbound HTTPS to:

- **`raw.githubusercontent.com`** — to fetch the installer and launcher files.
- **Docker Hub (`docker.io`)** — to pull the `pentarisc/firesim-lab` image (Docker, Podman, and nerdctl all pull from the same registry).

Behind a corporate proxy, configure your container runtime's proxy settings so image pulls succeed.

## What you do *not* need on the host

A frequent source of confusion: none of the simulation toolchain belongs on the host. You do **not** install any of the following — they live in the image:

- Java, SBT, Scala, or any JVM tooling
- Verilator or VCS
- Python or the FireSim Python environment
- The AWS CLI — even for FPGA builds and runs. The installer creates a self-contained `.aws` directory under your install location that is bind-mounted into the container, so you run `aws configure sso` / `aws sso login` *inside* the container. This is exactly why the AWS setup pages instruct you to run their commands in the container (see {doc}`aws/index`).
- Xilinx Vivado or any FPGA vendor tooling

### git is optional

`git` is **not** required to install or run firesim-lab — the installer downloads files directly rather than cloning. You only need `git` if you intend to clone the firesim-lab repository for development, or to version-control your own scaffolded projects (recommended, since each `fslab new` project is a standalone repo). If you want it, `git --version` should succeed; install it via your Linux/WSL package manager or, on macOS, with Homebrew or the Xcode command-line tools.

## Quick verification

Run this in your target shell before moving on — bash on Linux, Terminal on macOS, or the **WSL Ubuntu shell** on Windows. Every line should succeed (shown for Docker; substitute `podman`/`nerdctl` and the rootful-access check from above if you're using one of those instead):

```bash
docker --version          # Docker present
docker compose version    # Compose v2 present
curl --version            # curl present
docker run --rm hello-world   # Docker usable as your user
```

On Windows, run these from *inside* WSL (not PowerShell). If `docker` is not found there, enable Docker Desktop's **WSL Integration** for your distro.

## Next steps

- **Everyone:** continue to {doc}`/installation/index` to install firesim-lab and start the container.
- **FPGA path only:** if you plan to run on AWS F2 hardware, also work through {doc}`aws/index` to prepare your AWS account, login identity, and IAM roles. Metasimulation users can skip the AWS section entirely.
