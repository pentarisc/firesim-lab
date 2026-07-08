---
name: firesim-lab-setup
description: One-time-per-host/account provisioning for the firesim-lab flow. Use when setting up a new host or workspace, when firesim-lab-sim says setup is missing, or when the user wants to enable AWS F2 or task notifications. Detects host prereqs (container runtime, the firesim-lab launcher, the image) and offers to remediate per-step; inits the workspace; detects + pins the installed fslab version; optionally provisions AWS (solo-admin scripts vs org-admin handoff) + first-time SSO; optionally sets up notifications. Writes the workspace-level skill-state stamp.
metadata:
  fslab_version: "0.9.0rc1"
  skill_version: "0.9.0rc1"
---

# firesim-lab-setup — run-once provisioning

You perform the **one-time** setup that the recurring `firesim-lab-sim` skill
depends on, and you record what you did in the **workspace-level stamp** so other
skills (and later sessions) can read it. You are interactive: detect, explain,
ask consent per step, then act. Never silently install or create cloud resources.

## 0. Preflight — context + version (always first)

1. Run from the **workspace root** (or export `FSLAB_ENV_FILE` to the absolute
   `.firesim-lab.env` path), then source the runtime seam and detect context:
   `source "${CLAUDE_PLUGIN_ROOT}/skills/firesim-lab-setup/scripts/detect-context.sh" && fslab_detect_context`.
   This sets `FSLAB_CONTEXT` (in_container / host), `$RUNTIME`, and
   `$FSLAB_CONTAINER`. **Never** inline a container command — always go through
   `fslab_exec` / `fslab_in_dir` (the `docker` literal lives only in that script).
2. **Detect the active version** and pin to it (§2.5): once a container exists,
   read `fslab --version` via `fslab_exec`, cross-check `FIRESIM_LAB_VERSION` in
   `<workspace>/.firesim-lab.env` and any `.fslab/meta.json`. This **active
   version** is recorded in the stamp and used by all three skills. Never assume
   "latest".
3. **Skill↔tool compatibility (MAJOR.MINOR):** this skill carries
   `fslab_version: 0.9.0rc1`. If the active tool's MAJOR.MINOR differs, **halt** and
   tell the user to align them (`firesim-lab --upgrade` for the workspace, or
   install the matching plugin version) — do not operate a tool the skill does not
   understand. Patch differences are fine.

If the first run shows nothing set up yet, point the user at `firesim-lab-help`
for the overview, then proceed.

Detailed probes and remediation live in
[reference/prereqs.md](reference/prereqs.md) — load it for S1–S3.

## S1–S3. Host prereqs → workspace init → container + version

Walk these with **detect + offer to run (per-step confirm)**:

- **S1 Host prereqs:** is *any* container runtime installed? If not, ask which
  (Docker recommended; Podman/nerdctl on Linux) and offer to run the matching
  install script (`scripts/install-podman-rootful.sh` /
  `scripts/install-nerdctl-rootful.sh`; Docker's official convenience script on
  Linux, explain-and-link only for Docker Desktop on macOS/Windows) — see
  [reference/prereqs.md](reference/prereqs.md) Tier 0. Then: is it running? is
  the `firesim-lab` launcher installed? is the image pulled? Offer to remediate
  each gap — may run `install.sh` / pull the image. The launcher is
  **TTY-guarded**: only `--pull/--status/--down/--clean-cache/--upgrade/--help`
  run non-interactively; bare `firesim-lab` (init/start) needs a TTY — drive it
  by pre-seeding the prompted fields (`VERILATOR_THREADS`,
  `ENABLE_CUSTOM_PLUGINS`, both defaulted) or hand the exact command to the
  user to run.
- **S2 Workspace init:** if `<workspace>/.firesim-lab.env` is absent, run the
  launcher to create it.
- **S3 Container + version:** discover the running container, establish the
  `firesim-lab-shell` path (handled by the seam), then detect + pin the version
  (preflight step 2).

→ Update the stamp: `setup.host_prereqs_ok`, `workspace_initialized`,
`container_discovered`, `container_runtime` (`"docker"`, `"podman"`, or
`"nerdctl"`), and `fslab_version`.

## S4. AWS provisioning — OPT-IN (only if the user wants F2)

**Ask intent first:** does the user plan to use real FPGA (AWS F2)? Metasim-only
users **skip all of S4** (stamp `aws.intent: "metasim_only"`,
`provisioned: "skipped"`).

If F2: ask **solo-developer vs org-developer** and follow
[reference/aws-provisioning.md](reference/aws-provisioning.md). Summary of the
four layers and who runs them:

- **Console / account / quota** (account security, billing, Identity Center,
  permission set, **F2 service quota**) — **explain + link + verify only**, never
  scriptable. **Request the slow F2 quota EARLY** (1–2 day approval) so it can be
  approving while the user iterates in metasim.
- **Admin-CLI** (the two instance-profile roles, the SSH key pair, the
  `iam:PassRole` grant) — **offer to run the bundled scripts, per-step confirm**,
  **solo-developer admin only**. For an **org-developer** (no `iam:CreateRole` by
  design) do **not** attempt creation — verify the admin-provisioned resources and
  produce the exact commands for their admin.
- **First-time `aws configure sso --use-device-code`** — guide + run (creates the
  login profile). The container is headless, so always use `--use-device-code`
  (here and in any login command you show the user). The *recurring*
  `aws sso login --use-device-code` is `firesim-lab-sim`'s job, not yours.
- **Verification** — run the read-only probes freely
  (`scripts/verify-aws.sh <profile> <region>`).

→ Update the stamp `aws` block: `intent`, `developer_kind`, `provisioned`
(`true`/`false`/`"skipped"`), `sso_profile_configured`, `profile_name`, `region`.

## S5. Notifications — OPT-IN

Ask: want a ping when a task finishes or needs attention? `no` is remembered
(`enabled: false`) so you **never re-ask**; inline reporting still always applies.

If yes: reuse an existing channel (record a **reference**) or **scaffold + guide**
a new one (webhook-first). You cannot complete auth — the human pastes the webhook
URL / OAuths / reconnects. For an env-var-backed secret, it must be exported where
**non-interactive** shells read it (for zsh that is `~/.zshenv`, not `~/.zshrc`).
**Never write a secret into the stamp** — only a `type` + env-var name / tool ref.

→ Update the stamp `notifications` block: `enabled`, `events`
(default `["needs_attention","completion"]`), `channel` (type + ref + env names).

## The workspace stamp (you own it)

Write `<workspace>/.firesim-lab.skill-state.json` directly (atomic `*.tmp`→rename),
next to `.firesim-lab.env`. Shape and field meanings are in
[reference/prereqs.md](reference/prereqs.md). Add it to the workspace `.gitignore`
(host-local, like `.firesim-lab.env`). The CLI never reads or writes this file.

## Hand-off

End by telling the user setup is complete and to run **`firesim-lab-sim`** to
scaffold and simulate a design. If F2 was provisioned, note that the quota may
still be approving and that metasim needs none of it.
