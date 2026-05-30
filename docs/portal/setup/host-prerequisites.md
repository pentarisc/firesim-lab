# Host Prerequisites

firesim-lab ships its entire toolchain — Scala/SBT, Verilator, the FireSim Python environment, FPGA tooling — inside a single pinned Docker image. The host machine therefore stays deliberately thin: essentially just **Docker** (plus `curl` on Linux). Everything heavy runs in the container.

This page is the checklist to get your host ready *before* you install firesim-lab. For the actual install and first container start, continue to {doc}`/installation/index`. For why the toolchain is containerized at all and what lives where, see {doc}`/installation/host-vs-container`.

## Supported platforms

Because the entire toolchain lives in the container, the only real platform requirement is a working Docker installation that can run Linux containers.

- **Linux** — any modern distribution with a current Docker Engine. The primary, most-tested platform.
- **Windows 10 / 11** — via [Docker Desktop](https://www.docker.com/products/docker-desktop/) on the WSL2 backend. firesim-lab ships native PowerShell installer and launcher scripts, so you drive it from a normal Windows prompt — no manual WSL gymnastics required.
- **macOS** — on the roadmap; not yet supported with first-class launcher scripts.

The required software differs slightly per platform; both are covered below.

## Required software

firesim-lab runs as a Docker Compose service. What you install to get there depends on your platform.

### On Linux

You need exactly two things:

Docker Engine (with the Compose v2 plugin)
: Docker Engine 20.10 or newer with the bundled `docker compose` (v2) plugin. Verify both:
: ```bash
  docker --version
  docker compose version
  ```
: If `docker compose version` errors, install the Compose plugin (`docker-compose-plugin` on most distributions) — the standalone legacy `docker-compose` binary is not required.

curl
: The installer is fetched and piped to your shell with `curl`, and the launcher uses it to download files. Verify:
: ```bash
  curl --version
  ```

The bootstrap installer checks for both and stops with a clear error if either is missing.

**Run Docker without sudo.** The launcher and the container's bind-mount logic expect to invoke `docker` as your normal user. Add yourself to the `docker` group once (then log out and back in), and confirm it works:

```bash
sudo usermod -aG docker "$USER"
docker run --rm hello-world
```

### On Windows

Docker Desktop for Windows
: [Docker Desktop](https://www.docker.com/products/docker-desktop/) on the **WSL2 backend** (the default). It bundles the Compose v2 plugin, so there is nothing else to install for Compose. Docker Desktop must be **running** before you launch firesim-lab — the launcher checks for it and exits with a clear message if it is not. Verify from any prompt:
: ```powershell
  docker --version
  docker compose version
  ```

Windows PowerShell 5.1
: Built into Windows 10 and 11 — **no separate PowerShell 7 install is required**. The installer and the `firesim-lab` launcher are PowerShell scripts invoked through a `.cmd` shim that runs them with `-ExecutionPolicy Bypass`, so you do not need to change your machine's execution policy. You can run `firesim-lab` from `cmd.exe`, PowerShell, or Windows Terminal interchangeably.

`curl` is **not** required on Windows — the installer uses PowerShell's built-in `Invoke-WebRequest`. There is also no `docker` group step: Docker Desktop manages access for your Windows user.

## Recommended hardware

Chisel elaboration and Verilator compilation are memory- and CPU-intensive. The container defaults to a 16 GB memory ceiling with an 8 GB reservation, so plan accordingly:

- **Memory:** 16 GB of host RAM recommended; 8 GB is a workable floor for small designs. Larger designs benefit from raising the container's memory limit (see the environment-variable reference in {doc}`/installation/index`).
- **CPU:** more physical cores shorten Verilator builds and metasim runs — the container parallelizes Verilator across cores. Four or more is comfortable.
- **Disk:** the image plus the persistent SBT/Ivy/coursier and ccache volumes consume tens of gigabytes. Budget at least ~30 GB of free space to start, more if you keep multiple build artefacts around.

On **Windows**, these limits apply to the resources Docker Desktop's WSL2 backend is allowed to use, not just the container. If builds run short on memory, raise the WSL2 allocation (via a `.wslconfig` file in your user profile) so the container's 16 GB ceiling actually fits.

## Network access

The host needs outbound HTTPS to:

- **`raw.githubusercontent.com`** — to fetch the installer and launcher files.
- **Docker Hub (`docker.io`)** — to pull the `pentarisc/firesim-lab` image.

Behind a corporate proxy, configure Docker's proxy settings so image pulls succeed.

## What you do *not* need on the host

A frequent source of confusion: none of the simulation toolchain belongs on the host. You do **not** install any of the following — they live in the image:

- Java, SBT, Scala, or any JVM tooling
- Verilator or VCS
- Python or the FireSim Python environment
- The AWS CLI — even for FPGA builds and runs. The installer creates a self-contained `.aws` directory under your install location that is bind-mounted into the container, so you run `aws configure sso` / `aws sso login` *inside* the container. This is exactly why the AWS setup pages instruct you to run their commands in the container (see {doc}`aws/index`).
- Xilinx Vivado or any FPGA vendor tooling
- On Windows: `curl` and PowerShell 7 — the built-in `Invoke-WebRequest` and Windows PowerShell 5.1 are sufficient.

### git is optional

`git` is **not** required to install or run firesim-lab — the installer downloads files directly rather than cloning. You only need `git` on the host if you intend to clone the firesim-lab repository for development, or to version-control your own scaffolded projects (recommended, since each `fslab new` project is a standalone repo). If you want it, `git --version` should succeed; install it from your distro package manager (Linux) or [git-scm.com](https://git-scm.com/download/win) (Windows).

## Quick verification

Run this before moving on — every line should succeed.

**Linux:**

```bash
docker --version          # Docker Engine present
docker compose version    # Compose v2 plugin present
curl --version            # curl present
docker run --rm hello-world   # Docker usable without sudo
```

**Windows** (any prompt, with Docker Desktop running):

```powershell
docker --version          # Docker Desktop present
docker compose version    # Compose v2 present
$PSVersionTable.PSVersion # PowerShell 5.1+ (built in)
docker run --rm hello-world   # Docker usable
```

## Next steps

- **Everyone:** continue to {doc}`/installation/index` to install firesim-lab and start the container.
- **FPGA path only:** if you plan to run on AWS F2 hardware, also work through {doc}`aws/index` to prepare your AWS account, login identity, and IAM roles. Metasimulation users can skip the AWS section entirely.
