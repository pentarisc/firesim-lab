# firesim-lab — host setup (Windows & macOS)

firesim-lab runs inside a Linux container, so the only host requirement is a
**container runtime**. This page walks through **Docker Desktop** — the
tested, zero-extra-setup path on Windows and macOS. Pick your platform:

- **Linux** — use the one-line installer in the [README](../README.md); Linux
  also supports rootful **Podman** and **nerdctl** as alternatives to Docker —
  see the documentation portal's *Host Prerequisites* page for the one-time
  setup each needs.
- **Windows** — [Windows (WSL2)](#windows-wsl2) below.
- **macOS** — [macOS](#macos) below.

Both Windows and macOS use the *same* Linux image and the *same* `firesim-lab`
workflow once you are inside the container.

> **Alternative runtimes on Windows/macOS.** Since a WSL2 distro is genuinely
> Linux, Podman/nerdctl (installed *inside* WSL2, not a separate Windows app)
> should work the same way they do on native Linux — but this hasn't been
> tested. Desktop wrappers like Podman Desktop, Rancher Desktop, or Finch are
> untested with firesim-lab entirely; Docker Desktop remains the only
> Windows/macOS path covered by this guide.

---

## Windows (WSL2)

On Windows, firesim-lab runs through **WSL2** (Windows Subsystem for Linux).
Docker Desktop already uses WSL2 as its engine, so this adds only a user-facing
Linux distro — and in return you get near-native file performance and correct
file ownership.

### Why the workspace must live in the WSL filesystem

Docker Desktop on Windows runs on a WSL2 Linux VM. File access has two very
different speeds:

| Where the files live | Bind-mount speed | Notes |
|---|---|---|
| Windows drive (`C:\...`, `/mnt/c/...`) | **Slow** — crosses the Win↔Linux boundary (9P/virtiofs) | What Docker Desktop & the VS Code WSL extension warn about |
| Inside a WSL2 distro's ext4 (`~/` in Ubuntu) | **Near-native** | Same VM, no boundary crossing |

The penalty is worst for **many small file operations** — exactly what
SBT/Coursier, Chisel elaboration, and Verilator's C++ codegen + ccache do (tens
of thousands of tiny reads/writes). **Keep your workspace under your WSL2 Linux
home (`~`), not under `/mnt/c/...`.**

> **File ownership.** The container entrypoint derives its runtime user from the
> ownership of the bind-mounted `/target` workspace. A workspace under your WSL2
> Linux home is owned by your Linux user, so you get a proper **non-root** user
> inside the container. A `/mnt/c` (Windows-drive) path appears **root-owned**
> in the Linux VM, so the container would fall back to running as root — another
> reason to keep the workspace under `~`.

> **Where each step runs.** Steps 1–2 run on the **Windows side** (PowerShell /
> Docker Desktop GUI). From **step 3 onward you work inside the WSL shell** —
> commands typed in PowerShell will not reach your Linux home.

<details>
<summary><strong>New to Windows terminals? (first-time setup)</strong></summary>

If you have never used WSL, PowerShell, or Docker before:

- **Open PowerShell as Administrator** (needed for step 1): right-click the
  **Start** button and choose **Terminal (Admin)** (Windows 11) or **Windows
  PowerShell (Admin)** (Windows 10). Or open the Start menu, type *PowerShell*,
  right-click it, and choose **Run as administrator**.
- **Open a WSL / Ubuntu shell** (step 3 onward): after WSL is installed, launch
  the **Ubuntu** app from the Start menu, pick **Ubuntu** from the Windows
  Terminal `⌄` dropdown, or just run `wsl` in any PowerShell / CMD window.
- Microsoft's official walkthrough (with current screenshots) is the best
  visual reference: <https://learn.microsoft.com/windows/wsl/install>.

</details>

### Prerequisites

- Windows 10 version 21H2+ or Windows 11.
- Administrator rights for the initial WSL install.
- Docker Desktop for Windows.

### 1. Install WSL2 *(in PowerShell, as Administrator)*

Open PowerShell **as Administrator** — right-click the **Start** button and pick
**Terminal (Admin)** (Windows 11) or **Windows PowerShell (Admin)** (Windows 10).
Without elevation, `wsl --install` fails with an "access denied" / "requires
administrator" error. Then run:

```powershell
wsl --install
```

This enables WSL2 and installs Ubuntu by default. Reboot if prompted, then set
up your Linux username and password when the Ubuntu window opens.

If WSL is already present, make sure WSL2 is the default and install Ubuntu
explicitly:

```powershell
wsl --set-default-version 2
wsl --install -d Ubuntu
wsl -l -v          # confirm the distro shows VERSION 2
```

### 2. Install Docker Desktop with the WSL2 backend *(Windows side)*

1. Install **Docker Desktop for Windows**.
2. In Docker Desktop: **Settings → General →** enable *"Use the WSL 2 based
   engine"* (this is the default on recent versions).
3. **Settings → Resources → WSL Integration →** enable integration for your
   distro (e.g. *Ubuntu*).
4. **Apply & Restart.**

### 3. Open a WSL (Ubuntu) shell

Everything from here on runs **inside WSL**, not in PowerShell. Open a WSL
shell in any of these ways:

- **Start menu:** launch the **Ubuntu** app.
- **Windows Terminal:** open a new tab from the ⌄ dropdown and pick **Ubuntu**.
- **From PowerShell or CMD:** run `wsl` to drop into your default distro.

Your prompt changes to something like `you@host:~$`. Confirm Docker is wired
through to WSL correctly from this shell:

```bash
docker version
docker run --rm hello-world
```

If `docker` is not found here, re-check **WSL Integration** in step 2.

### 4. Create your workspace in the Linux filesystem *(in WSL)*

Keep projects under your **WSL home** (`~`), **not** under `/mnt/c/...` — this
is what makes the build fast and gives correct non-root UID mapping.

```bash
mkdir -p ~/firesim-workspaces/my-workspace
```

### 5. Install firesim-lab *(in WSL)*

In the WSL Ubuntu shell:

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash
```

Then reload your shell (or `source ~/.bashrc`) so `firesim-lab` is on PATH.

### 6. Run *(in WSL)*

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

First run prompts for settings, starts the container, and opens the shell.
Inside the container the workflow is the usual `fslab new` → `fslab init` →
`fslab generate` → `fslab sim`.

### Accessing your WSL files from Windows

- In File Explorer, browse to `\\wsl$\Ubuntu\home\<user>\firesim-workspaces`.
- Or open the workspace in VS Code with the WSL remote: from the WSL shell run
  `code .` in the project directory.

### AWS credentials and SSH keys

Handled exactly as on Linux: `install.sh` creates `~/.firesim-lab/.aws` and
`~/.firesim-lab/.ssh`, which are bind-mounted into the container as `~/.aws`
and `~/.ssh`. Run `aws configure sso` / `aws sso login` *inside* the container;
credentials persist on the host. Place `.pem` keys under
`~/.firesim-lab/.ssh` (chmod 600).

### Common first-time issues

- **`wsl --install` says "access denied" / "requires administrator".** PowerShell
  wasn't opened as Administrator — see step 1.
- **WSL or Docker won't start; "virtualization" errors.** Hardware
  virtualization is disabled in your laptop's BIOS/UEFI. Reboot into BIOS/UEFI
  setup and enable **Intel VT-x / AMD-V** (often listed as *Virtualization
  Technology* or *SVM*). This is the most common blocker on student laptops.
- **`docker: command not found` inside WSL.** Docker Desktop's **WSL Integration**
  is off for your distro — enable it (step 2) and reopen the WSL shell.
- **`wsl` is not recognized.** Your Windows build is too old or the WSL feature
  isn't enabled; update Windows, then re-run `wsl --install`.

---

## macOS

macOS runs Docker Desktop natively, so the standard Linux installer works
directly in Terminal — no WSL and no platform-specific scripts.

### Prerequisites

- macOS 12 (Monterey) or newer, Intel or Apple Silicon.
- Docker Desktop for Mac, installed and running.
- Terminal (the default **zsh** shell is fine).

> **Apple Silicon (M1/M2/M3/M4).** The firesim-lab image is built for x86-64
> (amd64). On Apple Silicon it runs under emulation, which is noticeably slower
> for SBT/Verilator builds. In Docker Desktop, enable **Settings → General →
> "Use Rosetta for x86/amd64 emulation on Apple Silicon"** for the best
> available speed (install Rosetta first if prompted: `softwareupdate
> --install-rosetta`). Intel Macs run the image natively.

### 1. Install and start Docker Desktop for Mac

Download Docker Desktop for Mac, install it, and launch it. Verify in Terminal:

```bash
docker version
docker run --rm hello-world
```

### 2. Create your workspace *(in Terminal)*

```bash
mkdir -p ~/firesim-workspaces/my-workspace
```

### 3. Install firesim-lab *(in Terminal)*

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash
```

Then reload your shell (`source ~/.zshrc`, or open a new Terminal tab) so
`firesim-lab` is on PATH.

### 4. Run *(in Terminal)*

```bash
cd ~/firesim-workspaces/my-workspace
firesim-lab
```

First run prompts for settings, starts the container, and opens the shell;
inside it the workflow is the usual `fslab new` → `fslab init` →
`fslab generate` → `fslab sim`.

### Notes

- **AWS / SSH:** handled exactly as on Linux — `install.sh` creates
  `~/.firesim-lab/.aws` and `~/.firesim-lab/.ssh`, bind-mounted into the
  container as `~/.aws` and `~/.ssh`.
- **Performance:** Docker Desktop's VirtioFS bind mounts are acceptable, and
  (unlike Windows) there is no faster alternative filesystem location, so no
  special workspace placement is needed beyond the Apple Silicon note above.

---

## Stopping and cleaning up

From your workspace directory (in the WSL shell on Windows, or Terminal on
macOS):

```text
firesim-lab --down          # stop the container for the current workspace
firesim-lab --clean-cache   # remove this workspace's SBT/ccache volumes
firesim-lab --upgrade       # re-pin this workspace to the installed version
```

## Upgrading

firesim-lab pins each workspace to the version that created it. After
reinstalling a newer version, the launcher refuses to start an older workspace
until you migrate it with `firesim-lab --upgrade` (which also recreates the
container on the new image). Your project's `fslab.yaml` and any `registry.yaml`
carry an `fslab_version` field that the in-container `fslab` CLI checks too, and
must be migrated by hand after a minor-or-greater upgrade. The full procedure is
in the documentation portal's *Versioning & Upgrading* page.
