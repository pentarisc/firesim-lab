# Versioning & Upgrading

firesim-lab is versioned with **SemVer** (`vMAJOR.MINOR.PATCH`). It is pre-1.0,
so per SemVer's `0.y.z` rule a **minor** bump (`0.7.x` → `0.8.0`) may contain
breaking changes — to the `fslab` CLI, the `fslab.yaml` schema, the registry
format, or the generated project layout.

To keep things consistent, the version is pinned in three coordinated places,
and firesim-lab actively refuses to run mismatched combinations rather than
failing in confusing ways later:

1. **The host install** — `install.sh` pins the launcher scripts, the
   `docker-compose.yaml`, and the Docker image tag to the version you install.
2. **The workspace** — each `.firesim-lab.env` records the version it was
   created against, so the host launcher and the container image stay a matched
   set.
3. **The project** — every `fslab.yaml` and `registry.yaml` carries an
   `fslab_version` field, and the in-container `fslab` CLI refuses to operate on
   files whose version is incompatible with itself.

The single source of truth for the version number is the `fslab` package
(`fslab-cli/pyproject.toml`); the git tag and the Docker image tag are derived
from it.

```{contents}
:local:
:depth: 1
```

## Compatibility rule

A version is **compatible** with another when they share the same
`MAJOR.MINOR`. Patch differences are always compatible (a patch release never
changes a schema); a differing `MAJOR` or `MINOR` is treated as incompatible.

- `0.7.0` and `0.7.3` → compatible.
- `0.7.x` and `0.8.0` → **incompatible** (minor may break, pre-1.0).
- A file with **no** `fslab_version` field → **incompatible** (it predates
  version stamping and must be migrated explicitly).

## How a version is installed and pinned

When you install firesim-lab, the install ref maps to a Docker image tag and a
contract version:

| Install ref            | Image tag | Recorded version |
|------------------------|-----------|------------------|
| `v0.7.0` (release tag) | `0.7.0`   | `0.7.0`          |
| `main` (moving dev)    | `latest`  | `main`           |
| any branch / sha       | `<ref>`   | `<ref>`          |

```bash
# Install a specific release:
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash -s -- v0.7.0
```

`install.sh` records what it set up in `~/.firesim-lab/.firesim-lab-installed`
and writes `FIRESIM_IMAGE` + `FIRESIM_LAB_VERSION` into each workspace's
`.firesim-lab.env`. New workspaces inherit the installed version; you are not
prompted for an image tag.

## Upgrading the host install

Re-run the installer with the version you want. This replaces the launcher
scripts, the compose file, and the recorded image/version:

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash -s -- v0.8.0
```

Existing workspaces are **not** silently changed — they stay pinned to the
version that created them (see below).

## Upgrading a workspace

A workspace is pinned to the firesim-lab version that created it. After you
upgrade the host install, running the launcher in an older workspace **fails
on purpose**:

```text
✗ firesim-lab version mismatch.
     This workspace is pinned to : 0.7.0
     Installed version is        : 0.8.0
```

The host scripts and the container image are a matched set, so firesim-lab will
not run them against each other. You have two choices:

```bash
firesim-lab --upgrade     # migrate this workspace to the installed version
```

`--upgrade` re-pins the workspace's image and version, preserves your other
settings (Verilator threads, memory limits, plugins), and recreates the
container on the new image. If the SBT/Scala toolchain changed between versions,
follow up with `firesim-lab --clean-cache` to re-seed the build caches.

Or, to keep the workspace exactly as it is, reinstall the matching version:

```bash
curl -sSL https://raw.githubusercontent.com/pentarisc/firesim-lab/main/docker/install.sh | bash -s -- v0.7.0
```

## Upgrading a project (`fslab.yaml`)

Upgrading the workspace gets you the new container, but the **project files
inside it are not touched** — your `fslab.yaml` still declares the old version.
The `fslab` CLI refuses to operate on it until you migrate:

```text
Incompatible project file (fslab.yaml):
  declared fslab_version : 0.7.0
  this fslab version     : 0.8.0

firesim-lab requires the project file to match this CLI's MAJOR.MINOR version.
```

Migration is **manual** and deliberate — the schema may have changed in ways no
automatic rewrite can safely infer. The procedure:

1. Read the release notes / changelog for everything between your project's
   version and the installed version, noting any `fslab.yaml` schema changes.
2. Apply those changes to your `fslab.yaml` (rename/add/remove fields as
   documented).
3. Update the top-level `fslab_version` field to the installed version.
4. Re-run your command (`fslab generate`, `fslab build`, …); the gate now
   passes.

```{tip}
The fastest way to see the current expected shape is to scaffold a throwaway
project with the new CLI (`fslab new _scratch && fslab init …`) and diff its
generated `fslab.yaml` against yours.
```

## Upgrading a registry (`registry.yaml`)

The same rule applies to the built-in registry and to any **user-defined**
registry files referenced from `advanced.custom_registries`. Each must carry an
`fslab_version` matching the CLI's `MAJOR.MINOR`:

```yaml
# my-registry.yaml
fslab_version: "0.8.0"
bridges:
  - id: my_bridge
    # ...
```

The built-in `lib/registry.yaml` is stamped to ship with each release, so it
always matches the container's CLI. For your own registries, migrate the entries
to any new schema and bump `fslab_version` exactly as you would for a project.

## See also

- {doc}`first-container-start` — the launcher lifecycle commands, including
  `--upgrade`.
- {doc}`host-vs-container` — why the host and container are split, and which
  pieces are version-pinned.
