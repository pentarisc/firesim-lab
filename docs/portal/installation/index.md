# Installation

This chapter installs firesim-lab on your host and gets you to a running container shell. It assumes you have already worked through {doc}`/setup/index` — in particular {doc}`/setup/host-prerequisites`, which confirms a container runtime, `curl`, supported platforms, and hardware sizing. If `docker run --rm hello-world` (or the equivalent for Podman/nerdctl) succeeds in your target shell, you are ready to install.

Installation is deliberately light. Almost everything firesim-lab needs — Scala/SBT, Verilator, the FireSim Python environment, FPGA tooling — lives inside a single container image, so installing on the host means little more than placing a launcher script on your `PATH` and pulling that image. For the reasoning behind this split, see {doc}`host-vs-container`.

## What the installer actually does

A single `curl`-piped script, `install.sh`, performs the whole host-side install. It does **not** start the container runtime or run any simulation — it only stages files:

1. Picks an install directory — `~/.firesim-lab` by default (override with `INSTALL_DIR`).
2. Downloads the `firesim-lab` launcher plus the files it needs (`docker-compose.yaml`, `Dockerfile`, `entrypoint.sh`, `firesim-lab-shell`) from the repository.
3. Marks the launcher executable and symlinks it onto your `PATH` (via `~/.local/bin`, `~/bin`, or a shell-rc `PATH` line as a last resort).
4. Creates two empty, self-contained directories under the install dir — `.aws` and `.ssh` (mode `700`) — that are later bind-mounted into the container as `~/.aws` and `~/.ssh`. This is why you never install the AWS CLI or manage SSH keys on the host. See {doc}`mountpoints` for the full mount map.

After that, you run the `firesim-lab` launcher from any workspace directory; the **first run** pulls the image, starts the container, and drops you into a shell. That first-run experience is documented separately in {doc}`first-container-start`.

The workflow below is the same on every platform — the only difference is how you reach the Linux shell you run it from. Pick your platform:

- **Linux** — run directly in your terminal.
- **Windows** — run inside a **WSL2** Ubuntu shell (see [Windows (WSL2)](#windows-wsl2)).
- **macOS** — run directly in Terminal (see [macOS](#macos)).

(install-linux)=
## Linux

Linux is the primary, most-tested platform. You need a running container runtime and `curl` — verified in {doc}`/setup/host-prerequisites`. The steps below show Docker; if you're using Podman or nerdctl instead, complete the rootful setup in {doc}`/setup/host-prerequisites` first, then swap `docker` for `podman`/`nerdctl` in the verify command below (the `firesim-lab` launcher itself auto-detects whichever runtime is active — no flag needed). If you have not already added yourself to the `docker` group, do so now so the launcher can drive Docker as your normal user:

```bash
sudo usermod -aG docker "$USER"   # then log out and back in
docker run --rm hello-world
```

### 1. Create your workspace

A *workspace* is just a directory on the host. The launcher bind-mounts it into the container as `/target`, and every project you scaffold lives inside it.

```bash
mkdir -p ~/firesim-workspaces/my-workspace
```

### 2. Install firesim-lab

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash
```

Then reload your shell (or `source ~/.bashrc`) so the `firesim-lab` launcher is on your `PATH`. Confirm it resolves:

```bash
firesim-lab --help
```

To install a specific tag or branch instead of `main`:

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash -s -- v1.2.0
```

### 3. Run

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

The first run prompts for a few settings, pulls the image, starts the container, and opens a shell. Walk through exactly what happens in {doc}`first-container-start`. Inside the container the workflow is the usual `fslab new` → `fslab init` → `fslab generate` → `fslab build` → `fslab sim` — see {doc}`/quickstart/index`.

(windows-wsl2)=
## Windows (WSL2)

On Windows, firesim-lab runs inside **WSL2** (Windows Subsystem for Linux). Docker Desktop already uses WSL2 as its engine, so this adds only a user-facing Linux distro — and in return you get near-native file performance and correct file ownership. There are no Windows-native firesim-lab scripts; from the container's point of view, you are simply on Linux.

:::{warning}
**Keep your workspace inside the WSL2 Linux filesystem** (under your Linux home, `~`), **not** on a Windows drive (`C:\...` / `/mnt/c/...`). Two reasons:

- **Speed.** Files on a Windows drive cross the Windows↔Linux boundary (9P/virtiofs) on every access. The penalty is worst for the *many small file operations* SBT/Coursier, Chisel elaboration, and Verilator's C++ codegen perform — tens of thousands of tiny reads and writes. A workspace under `~` lives on the same VM with no boundary crossing.
- **File ownership.** The container derives its runtime user from the ownership of the bind-mounted `/target`. A workspace under your WSL home is owned by your Linux user, so you get a proper **non-root** user in the container. A `/mnt/c` path appears **root-owned** inside the Linux VM, so the container falls back to running as root.
:::

:::{note}
**Where each step runs.** Steps 1–2 run on the **Windows side** (PowerShell / Docker Desktop). From **step 3 onward you work inside the WSL shell** — commands typed in PowerShell will not reach your Linux home.
:::

### 1. Install WSL2 *(PowerShell, as Administrator)*

Open PowerShell **as Administrator** — right-click the **Start** button and pick **Terminal (Admin)** (Windows 11) or **Windows PowerShell (Admin)** (Windows 10). Without elevation, `wsl --install` fails with an "access denied" / "requires administrator" error.

```powershell
wsl --install
```

This enables WSL2 and installs Ubuntu by default. Reboot if prompted, then set your Linux username and password when the Ubuntu window opens.

If WSL is already present, make sure WSL2 is the default and install Ubuntu explicitly:

```powershell
wsl --set-default-version 2
wsl --install -d Ubuntu
wsl -l -v          # confirm the distro shows VERSION 2
```

### 2. Install Docker Desktop with the WSL2 backend *(Windows side)*

1. Install **Docker Desktop for Windows**.
2. **Settings → General →** enable *"Use the WSL 2 based engine"* (the default on recent versions).
3. **Settings → Resources → WSL Integration →** enable integration for your distro (e.g. *Ubuntu*).
4. **Apply & Restart.**

### 3. Open a WSL (Ubuntu) shell

Everything from here runs **inside WSL**, not PowerShell. Open a WSL shell from the **Ubuntu** Start-menu app, the Windows Terminal `⌄` dropdown, or by running `wsl` in any PowerShell/CMD window. Your prompt changes to something like `you@host:~$`. Confirm Docker is wired through:

```bash
docker version
docker run --rm hello-world
```

If `docker` is not found here, re-check **WSL Integration** in step 2.

### 4. Create your workspace *(in WSL)*

Keep projects under your **WSL home** (`~`), not under `/mnt/c/...`:

```bash
mkdir -p ~/firesim-workspaces/my-workspace
```

### 5. Install firesim-lab *(in WSL)*

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash
```

Reload your shell (or `source ~/.bashrc`) so `firesim-lab` is on `PATH`, then verify with `firesim-lab --help`.

### 6. Run *(in WSL)*

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

See {doc}`first-container-start` for what the first run does.

### Accessing your WSL files from Windows

- In File Explorer, browse to `\\wsl$\Ubuntu\home\<user>\firesim-workspaces`.
- Or open the workspace in VS Code with the WSL remote: run `code .` in the project directory from the WSL shell.

### Common first-time issues

- **`wsl --install` says "access denied" / "requires administrator".** PowerShell was not opened as Administrator — see step 1.
- **WSL or Docker won't start; "virtualization" errors.** Hardware virtualization is disabled in BIOS/UEFI. Reboot into BIOS/UEFI setup and enable **Intel VT-x / AMD-V** (often labelled *Virtualization Technology* or *SVM*). This is the most common blocker.
- **`docker: command not found` inside WSL.** Docker Desktop's **WSL Integration** is off for your distro — enable it (step 2) and reopen the WSL shell.
- **`wsl` is not recognized.** Your Windows build is too old or the WSL feature is not enabled; update Windows, then re-run `wsl --install`.

(macos)=
## macOS

macOS runs Docker Desktop natively, so the standard Linux installer works directly in Terminal — no WSL, no platform-specific scripts.

:::{note}
**Apple Silicon (M-series).** The firesim-lab image is built for x86-64 (amd64) and runs under emulation on Apple Silicon, which is noticeably slower for SBT/Verilator builds. In Docker Desktop, enable **Settings → General → "Use Rosetta for x86/amd64 emulation on Apple Silicon"** for the best available speed (install Rosetta first if prompted: `softwareupdate --install-rosetta`). Intel Macs run the image natively.
:::

### 1. Start Docker Desktop and verify

Install and launch Docker Desktop for Mac, then verify in Terminal:

```bash
docker version
docker run --rm hello-world
```

### 2. Create your workspace

```bash
mkdir -p ~/firesim-workspaces/my-workspace
```

### 3. Install firesim-lab

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash
```

Reload your shell (`source ~/.zshrc`, or open a new Terminal tab) so `firesim-lab` is on `PATH`, then verify with `firesim-lab --help`.

### 4. Run

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

Unlike Windows, there is no faster alternative filesystem location on macOS — Docker Desktop's VirtioFS bind mounts are fine, so no special workspace placement is needed beyond the Apple Silicon note above.

## AWS credentials and SSH keys

On every platform, `install.sh` creates `~/.firesim-lab/.aws` and `~/.firesim-lab/.ssh` on the host, bind-mounted into the container as `~/.aws` and `~/.ssh`. You run `aws configure sso` / `aws sso login` *inside* the container, and credentials persist on the host — no AWS CLI on the host. Place `.pem` keys under `~/.firesim-lab/.ssh` and `chmod 600` them so OpenSSH accepts them. The AWS account setup itself is covered in {doc}`/setup/aws/index`; the mount mechanics are in {doc}`mountpoints`.

## Stopping and cleaning up

From your workspace directory (the WSL shell on Windows, Terminal on macOS, or your shell on Linux):

```bash
firesim-lab --down          # stop the container for the current workspace
firesim-lab --clean-cache   # remove this workspace's SBT/ccache volumes
```

The full set of launcher subcommands is documented in {doc}`first-container-start`. For upgrading to a newer firesim-lab release — and migrating an existing workspace and its `fslab.yaml` — see {doc}`versioning`.

## What's next

- {doc}`host-vs-container` — what runs where, and why the toolchain is containerized.
- {doc}`mountpoints` — how host paths map into the container, plus the environment-variable reference.
- {doc}`first-container-start` — a step-by-step of your first `firesim-lab` run.
- {doc}`versioning` — the versioning scheme, and how to upgrade the install, a workspace, and a project.
- {doc}`skill-plugin` — install the optional Claude Code skill that drives the whole flow conversationally.
- {doc}`/quickstart/index` — scaffold and simulate your first design.

```{toctree}
:maxdepth: 2

host-vs-container
mountpoints
first-container-start
versioning
skill-plugin
```
