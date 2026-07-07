# Setup reference — prereqs, version, and the workspace stamp

Loaded for S1–S3 and for writing the stamp. Everything here runs from the host
(or in-container); container commands go through `fslab_exec` from
`scripts/detect-context.sh`.

## S1 — host prereq probes (detect, then offer to remediate)

Use the **container runtime** generically (docker, podman, or nerdctl; the seam
resolves it via `CONTAINER_RUNTIME` — see `scripts/detect-context.sh`).

| Prereq | Probe | Remediation (per-step confirm) |
|---|---|---|
| Runtime running | `"$RUNTIME" info >/dev/null 2>&1` | Docker: start the engine (Docker Desktop / `systemctl start docker`) and confirm the user is in the `docker` group. Podman/nerdctl: these need one-time rootful setup this skill should not improvise inline — point the user at the documentation portal's Host Prerequisites page (Podman needs a socket-group + `CONTAINER_HOST`; nerdctl's rootful mode requires actual root, i.e. `sudo`). |
| Launcher installed | `command -v firesim-lab` | run `install.sh` (the curl-pipe from the README/installation guide), then re-check `firesim-lab --help` |
| Image pulled | `"$RUNTIME" image inspect <FIRESIM_IMAGE>` (read tag from `.firesim-lab.env`, default `docker.io/pentarisc/firesim-lab:latest`) | `firesim-lab --pull` (non-interactive, TTY-safe) |

The launcher is **TTY-guarded**. Non-interactive (safe to run for the user):
`--pull`, `--status`, `--down`, `--clean-cache`, `--upgrade`, `--help`. The bare
`firesim-lab` (init/start) **needs a TTY** — either pre-seed the two prompted
fields and run it, or hand the user the exact command:

- `VERILATOR_THREADS` — defaults to host nproc.
- `ENABLE_CUSTOM_PLUGINS` — defaults to `0` (security-sensitive; leave off unless
  the user asks).

## S2 — workspace init

If `<workspace>/.firesim-lab.env` is absent, the workspace is not initialized.
Running the launcher from the workspace creates it and pins
`FIRESIM_LAB_VERSION`. Confirm the file exists afterward.

## S3 — container discovery + version pin

`fslab_detect_context` discovers the container (Compose names it
`firesim-lab-firesim-lab-<workspace>`; matched on the `firesim-lab` prefix).
Then detect the active version:

```bash
fslab_exec 'fslab --version'                 # authoritative active version
grep FIRESIM_LAB_VERSION .firesim-lab.env    # workspace pin (must agree)
```

The launcher already hard-fails on host↔container↔workspace skew, so these are
guaranteed a matched set; record the version in the stamp.

## The workspace-level stamp

Write `<workspace>/.firesim-lab.skill-state.json` (atomic `*.tmp`→rename), and add
it to the workspace `.gitignore`. Schema (`schema_version: 1`):

```json
{
  "schema_version": 1,
  "fslab_version": "0.9.0-rc",
  "skill_version": "0.9.0-rc",
  "created_at": "2026-06-19T00:00:00Z",
  "updated_at": "2026-06-19T00:00:00Z",
  "setup": {
    "host_prereqs_ok": true,
    "workspace_initialized": true,
    "container_discovered": true,
    "container_runtime": "docker"
  },
  "aws": {
    "intent": "metasim_only",
    "developer_kind": null,
    "provisioned": "skipped",
    "sso_profile_configured": false,
    "profile_name": null,
    "region": null
  },
  "notifications": {
    "enabled": false,
    "events": ["needs_attention", "completion"],
    "channel": { "type": "local", "ref": null, "env": [] }
  }
}
```

Field notes:

- `aws.intent`: `"f2"` | `"metasim_only"`. `aws.provisioned`: `true` | `false` |
  `"skipped"`. `developer_kind`: `"solo"` | `"org"` | `null`.
- `container_runtime`: `"docker"` | `"podman"` | `"nerdctl"` | `"finch"` (spec
  §3.1 seam 3 reserved this field ahead of time, so multi-runtime support
  needed no `schema_version` bump when it landed).
- `notifications.channel.ref` / `.env` carry **names only** — never a secret
  value. `type`: `"webhook"` | `"mcp"` | `"local"`.

Always bump `updated_at` on every write; set `created_at` once.

## Skill↔tool version gate

This skill is `fslab_version 0.9.0-rc` → compatible with any installed tool of the
same MAJOR.MINOR (patch always OK), matching the rule `fslab.yaml`/`registry.yaml`
use. On a MINOR mismatch, halt with the tool's standard `firesim-lab --upgrade`
guidance rather than proceeding.
