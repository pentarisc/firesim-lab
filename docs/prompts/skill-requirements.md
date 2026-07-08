# firesim-lab AI-Accelerated Flow — Skill Requirements / Operating Model

**Date written:** 2026-06-08
**Project:** firesim-lab
**Status:** Requirements **defined**. No skill built yet. This document is the
agreed operating model and the **complete, self-contained** spec to build the
skills from — it folds in the proven metasim flow (§17), the command sequence
(§18), the engineering gotchas (§13), and the repo reference index (§19), so no
other document is needed.

---

## 1. Purpose & scope

A Claude Code **plugin — three cooperating skills (Help, Setup, Simulation)** —
that drives a **fully AI-accelerated firesim-lab flow**, end to end, for **end
users who write only Verilog/SystemVerilog** and never touch Chisel/Scala:

- **Phase 1 — Metasim** (the proven backbone): onboard the host, scaffold a
  project, place user RTL + payload, configure `fslab.yaml` (bridges, clk/reset/
  enable, port maps, params, `mem_base`), build, run, and confirm output.
- **Phase 2 — F2 FPGA** (gated extension): conversationally orchestrate AWS IAM
  Identity Center login (SSO device-code), configure the build/run targets,
  build the DCP/AFI, run on a real F2 host, and tear the cloud resources down.

Phase 2 is **hard-gated** behind a passing Phase 1: a design that builds and
prints correctly in metasim is the precondition for spending FPGA build
time/money.

---

## 2. Vehicle & skill decomposition

### 2.1 Vehicle: skills bundled as one plugin

The vehicle is **Claude Code Skills**, **bundled together as a single plugin and
distributed via a marketplace**. Alternatives considered and rejected:

| Option | Verdict | Reason |
|---|---|---|
| **Skills (plugin / marketplace)** | **Chosen** | Workflow orchestration with progressive disclosure, run in the user's main conversation, bundle companion reference/scripts/agents, native install + `/plugin update`. |
| Slash command | Rejected | A bare prompt template — no progressive disclosure, no bundled reference/scripts. Skills are the strict superset. |
| Subagent as the top-level surface | Rejected as top-level | Fresh isolated context **cannot interact with the user** — wrong for an interactive, stateful flow. **Used internally** for autonomous heavy work (§2.4, §10). |
| MCP server / Agent-SDK app | Rejected | Heavyweight; wrong audience — users already run Claude Code. |
| **Project skill** (`<repo>/.claude/skills/`) | Rejected | Only activates when the firesim-lab repo *is* the workspace. End users work in their **own out-of-tree** project folders under `/target`, so they would never see it. |
| install.sh copies skill → `~/.claude/skills/` | Deferred | Viable personal-skill path, but marketplace gives a better first-touch and native updates. Kept as a possible future fallback; not specified now. |

### 2.2 Decomposition: three skills, by separation of concerns

The flow is **not** one monolithic skill. It splits by *separation of concerns*,
which also maps to *how often each part runs* — keeping each invocation's context
small and letting the user run any part directly:

| Skill | Runs | Owns | Interactive? |
|---|---|---|---|
| **`firesim-lab-help`** | on demand (pull) | the flow overview / map; names the other two | yes (trivial) |
| **`firesim-lab-setup`** | once per host/account | one-time provisioning: host prereqs + workspace init (always); AWS account/IAM/roles/quota + first-time `aws configure sso` (opt-in, F2 users; §9) | yes |
| **`firesim-lab-sim`** | every iteration | the recurring end-to-end flow: metasim (scaffold → configure → compile-fix loop → sim → gate) **and** F2 (verify AWS + recurring SSO login → configure target → build → run → cleanup) | yes |

**No separate router/orchestrator.** `firesim-lab-sim` *self-orchestrates*: on
entry it reads the state stamp (§2.3) and resumes or jumps (e.g. straight to the
F2 stage when the stamp shows metasim already passed). The three skills navigate
by **cross-referencing** — Help names them; Setup ends with "now run the sim
skill"; Sim's preflight says "run Setup first" if the stamp shows it is missing.

### 2.3 The inter-skill state stamp (the contract)

Because the skills are **separate invocations** (possibly separate sessions),
they cannot share in-memory state. Each skill **bootstraps from a persisted
project stamp**: *read stamp → know what is done → do its part → update stamp*.
The stamp records the **active version** (§2.5), setup completion, AWS readiness,
the configured design, whether **metasim passed** (+ its evidence), and any
AGFI/image. The hard metasim→F2 gate is enforced by `firesim-lab-sim` **reading
the stamp**, not by in-memory flow order — which makes the gate robust across
sessions and direct invocation. fslab already single-sources per-project metadata
in `.fslab/meta.json` and generation state in `.fslab/state.json`, and the whole
`.fslab/` dir is gitignored (local-only) — so the stamp lives **alongside** these
as **skill-owned JSON sibling files**, split by scope into a **two-level stamp**
(§2.6): a **workspace-root** file for host/AWS/version facts (written by Setup) and
a **per-project** `.fslab/skill-state.json` for design/metasim/F2 facts (written by
Sim). The stamp is skill-owned — the CLI never reads or writes it — and the
metasim→F2 gate is tied to the CLI's existing `config_hash`, so stale evidence
re-opens it automatically (§2.6).

### 2.4 Skill vs. sub-agent (the dividing rule)

A sub-agent runs **autonomously in isolated context and returns one result — it
cannot pause to ask the user anything.** So:

- **Interactive work → a skill** (main context): all questionnaires, consent,
  clk/reset/port-map veto, spend confirmation, the SSO show-code-and-wait.
- **Verbose or long *autonomous* work → a sub-agent** (isolated context, returns
  a summary): the verbose build execution, and the long background build/run
  monitors (§10). Interaction always stays in the skill; only non-interactive
  execution is delegated.

### 2.5 Version awareness (bind to the installed tool, never "latest")

**Principle: the installed tool is the single source of truth for version.** The
skills are a thin orchestration layer over whatever fslab + image is installed;
they must never assume a version or reference `latest`/`stable`. firesim-lab
already single-sources the version (`fslab-cli/pyproject.toml` → `fslab
--version`) and pins it per workspace (`.firesim-lab.env` `FIRESIM_LAB_VERSION`)
and per project (`.fslab/meta.json` `__version__`); the launcher **hard-fails** on
host↔container↔workspace skew. The skills **read** these — they do not invent a
new version mechanism.

Required behaviors (all three skills):

1. **Detect the active version** at preflight from `fslab --version`,
   cross-checked with `FIRESIM_LAB_VERSION` / `.fslab/meta.json`. This is the
   **active version** — never `latest`.
2. **All three skills bind to that one version — for free.** Because the
   workspace pin is already enforced as a matched set, Help/Setup/Sim reading
   `FIRESIM_LAB_VERSION` are guaranteed consistent. The stamp (§2.3) records it.
3. **Bind every RTD doc link to the active version:** `…/en/v<active>/…` (RTD
   keeps the `v`). If that exact slug isn't published, fall back to the **nearest
   published patch of the same MAJOR.MINOR**; if none, **warn and link the version
   list** — never silently use `latest`/`stable`. (A literal `main` install, whose
   version resolves to `latest`, is the one case that maps to `/en/latest/`, with
   a note.)
4. **Skill↔tool compatibility at MAJOR.MINOR (reusing `is_compatible`):** each
   skill carries an `fslab_version` and is compatible with any installed tool of
   the same **MAJOR.MINOR** (patch always OK) — exactly the rule
   `fslab.yaml`/`registry.yaml` already use (`fslab/utils/versioning.py`). On a
   MINOR mismatch the skill **halts** with the same `firesim-lab --upgrade`
   migration message the tool already gives, rather than operating a tool it does
   not understand. Skill patch-level fixes ship as independent skill releases; a
   new tool MINOR triggers a new skill MINOR.

This makes "all three skills stick to one version, never `latest`" a property of
the existing pins rather than new bookkeeping; the only net-new check is the
skill↔tool MAJOR.MINOR gate in item 4.

### 2.6 State-stamp design (decided)

**Two skill-owned JSON files, split by scope.** The facts differ in *who writes
them* and *what they're scoped to*, so they live in two places; both sit in
already-gitignored, **local-only** locations, so "metasim passed" / "AWS
provisioned" never leak into the user's VCS or a teammate's clone. The skill
reads/writes them directly as plain files — **no new `fslab` command** (keeps the
skills a thin layer over the tool, §2.5). Conventions are borrowed from the
existing build/run stamps: a `schema_version`, a carried `fslab_version`,
ISO8601-UTC `created_at`/`updated_at`, and atomic `*.tmp`→rename writes.

**Workspace-level — `<workspace>/.firesim-lab.skill-state.json`** (next to
`.firesim-lab.env`; written by **Setup**, read by all three). Host/account/version
facts that outlive any single project:

```json
{
  "schema_version": 1,
  "fslab_version": "0.9.0rc1",
  "skill_version": "0.9.0rc1",
  "created_at": "2026-06-11T12:00:00Z",
  "updated_at": "2026-06-11T12:00:00Z",
  "setup": { "host_prereqs_ok": true, "workspace_initialized": true, "container_discovered": true },
  "aws": {
    "intent": "f2",                 // "f2" | "metasim_only"
    "developer_kind": "solo",       // "solo" | "org" | null
    "provisioned": true,            // true | false | "skipped"
    "sso_profile_configured": true,
    "profile_name": "firesim-lab",
    "region": "us-east-1"
  },
  "notifications": {                // §20; written by Setup, read by Sim
    "enabled": true,                // false is remembered → Setup never re-asks
    "events": ["needs_attention", "completion"],   // which report kinds push (§20)
    "channel": {
      "type": "webhook",            // "webhook" | "mcp" | "local"
      "ref": "$FSLAB_NOTIFY_WEBHOOK",   // env-var name / tool ref — NEVER a secret value
      "env": ["FSLAB_NOTIFY_WEBHOOK"]   // names only; secrets stay in env / MCP config
    }
  }
}
```

(Setup adds this file to the workspace `.gitignore` — it is host-local, like
`.firesim-lab.env`. fslab only gitignores the per-project `.fslab/` dir, not the
workspace root.)

**Project-level — `<project>/.fslab/skill-state.json`** (written by **Sim**, per
project). Design + gate + F2 pointers:

```json
{
  "schema_version": 1,
  "fslab_version": "0.9.0rc1",
  "skill_version": "0.9.0rc1",
  "created_at": "...",
  "updated_at": "...",
  "design": {
    "project_name": "uart-print-test",
    "top_module": "AXIUARTPrinter",
    "rtl_paths": ["user_rtl/AXIUARTPrinter.v"],
    "bridges": ["fased", "uart"]
  },
  "metasim": {
    "passed": true,
    "config_hash": "<sha256 copied from .fslab/state.json at pass time>",
    "criterion": { "type": "expected_output", "value": "Hello fr" },
    "evidence": { "matched": true, "captured_excerpt": "Hello frfom FiReim!…", "max_cycles": 100000 },
    "passed_at": "..."
  },
  "f2": { "last_build_id": null, "last_run_id": null, "agfi": null }
}
```

**Gate rule (§7).** F2 is unlocked **iff** `metasim.passed === true` **and**
`metasim.config_hash` equals the *current* `config_hash` in `.fslab/state.json`.
Editing RTL or `fslab.yaml` changes that hash and **re-opens the gate
automatically** — the skill never trusts stale evidence. This reuses the CLI's
existing hash mechanism (`state.json` `config_hash` / build-stamp `quintuplet`)
rather than inventing a new freshness check.

**Live F2 state is read, not copied.** AGFI, build status, and run status come
from the existing `build/fpga/.fslab/build.yaml` and `run/fpga/.fslab/run.yaml`
stamps; `f2.last_build_id` / `last_run_id` are only **pointers** so the skill can
find the right stamp. One source of truth per fact — the skill-state file never
duplicates lifecycle state the CLI already owns.

---

## 3. Audience, host, and runtime context

- **Audience:** end users of firesim-lab (no Chisel knowledge assumed). The skill
  must be **legible** — explain what it is doing and never silently guess on
  anything affecting correctness or cost.
- **Skill host:** Claude Code runs **on the user's host** (VSCode extension).
  `~/.claude/` and the skill live on the host. The skill **drives the container**
  via `docker exec <container> firesim-lab-shell bash -lc '…'` (see §13 #1).
- **Context detection (first action of any fslab call):** detect whether the
  skill is running *in-container* (`fslab` on `PATH`) vs *host-driving-container*.
  In-container → call `fslab` directly. Host → go through `firesim-lab-shell`
  (never bare `docker exec`, which runs as root and breaks SBT/ccache writes).

### 3.1 Multi-runtime (Phase 1 — rootful — landed in v0.9.0)

Multi–container-runtime support (rootful **Podman**, **nerdctl**/containerd;
**Finch** detection is wired but untested) shipped in **v0.9.0**, alongside the
SKILL update below. The SKILL's Setup S1 only ever installs/configures the
**rootful** path (below); correct behavior when a rootless runtime is already
present some other way is covered separately in §3.2.

The four seams this section originally asked the SKILL to leave (so this change
would be a near one-file edit) held exactly as designed:

1. **Single container-CLI seam.** `scripts/detect-context.sh` is still the only
   place the literal string `docker` appears; `SKILL.md` and the `reference/`
   files still never inline `docker exec …` — confirmed unchanged, **no code
   edit needed here**.
2. **`CONTAINER_RUNTIME` with a `docker` fallback.** The launcher
   (`docker/firesim-lab`) now genuinely writes `CONTAINER_RUNTIME=` into
   `.firesim-lab.env` (auto-detected as the first of `docker`/`podman`/
   `nerdctl`/`finch` found on `PATH`, overridable with `--runtime=<name>` or
   `FIRESIM_RUNTIME=<name>`, and persisted per-workspace). `detect-context.sh`
   already read this field with a `docker` fallback — **no code edit needed**.
3. **Stamp field.** `setup.container_runtime` in the workspace skill-state can
   now hold `"podman"` / `"nerdctl"` / `"finch"` in addition to `"docker"` —
   **no `schema_version` bump needed**, as designed.
4. **Runtime-neutral prose.** `SKILL.md` and `reference/prereqs.md` already say
   "the container runtime" generically. This turned out to need more than
   wording, though: S1 previously only checked whether an *already-installed*
   runtime was *running* — there was no path for "no runtime is installed at
   all, which one do you want?" (unlike the launcher/image checks, which
   already offer to run `install.sh` / pull). `reference/prereqs.md` S1 is now
   split into **Tier 0** (is any runtime installed? if not, ask Docker/Podman/
   nerdctl and offer to run the matching install script) and **Tier 1** (is it
   running? — the original check, unchanged). Two new bundled scripts do the
   actual work, `skills/firesim-lab-setup/scripts/install-podman-rootful.sh`
   and `install-nerdctl-rootful.sh` — host-side scripts (they run before any
   firesim-lab container exists, so they don't source `detect-context.sh` /
   use `fslab_exec` the way the AWS provisioning scripts do). Both are
   idempotent and require per-step confirm like every other mutating action
   this skill takes. Neither configures passwordless `sudo` for nerdctl, and
   neither can complete Podman's "log out and back in" step for the user —
   both are called out explicitly so the SKILL doesn't overreach.

Three real host-setup facts surfaced by AWS validation, worth the SKILL knowing
about: Podman defaults to **rootless** for any non-root invocation (needs
`CONTAINER_HOST` + a socket-group setup, or `sudo`, to reach the *rootful*
backend this SKILL targets); nerdctl's rootful mode requires the invoking
process to actually be UID 0 (no non-root socket-permission equivalent —
`sudo` is the only path); and **nerdctl-compose requires a real console/tty
for `tty: true` services, even for `up -d` / `down`** — unlike Docker/Podman,
which just warn and continue on a non-tty stdin. This directly affects the
SKILL, which drives `firesim-lab` non-interactively via a Bash tool: if
`CONTAINER_RUNTIME=nerdctl`, a plain non-interactive `firesim-lab --pull` (or
`--down`) can fail with `provided file is not a console` even though the
launcher's own TTY-guard considers those flags safe to run headless. If S1
hits this under nerdctl, tell the user to run the command themselves from a
real terminal rather than retrying it non-interactively.

Finch was not exercised end-to-end (native-Linux Finch runs are a smaller lift
than its usual macOS/Windows Lima-VM mode, but neither was tested); treat its
detection as present-but-unverified.

### 3.2 Rootless detection (Phase 2 — landed in v0.9.0)

Scope check first: this is **detection and correct behavior when a rootless
runtime is already present**, not SKILL-driven rootless *setup* — Setup S1
(§3.1) only ever installs/configures the **rootful** path. If a user already
has rootless Podman/nerdctl configured some other way, the container still
needs to behave correctly under it, which is what this section covers.

`entrypoint.sh` and `firesim-lab-shell` both used to treat "`/target` owned by
UID 0" as a single case — a warning, then run as root. That conflated two
different situations: a rootless user namespace (container-UID-0 mapped to the
real host user by the kernel — files written land back on the host correctly
owned; running as-is is already correct) and a genuinely root-owned workspace
under a rootful runtime (e.g. a Windows `/mnt/c` path — a real
misconfiguration, where running as root really does write root-owned files
back to the host). Both scripts now distinguish them via `/proc/self/uid_map`
(non-identity first entry = rootless userns; identity = genuine root) — a
kernel-level signal, not runtime-specific. The `exec "$@"` behavior is
unchanged in both branches; only the message (info vs. warning) differs.
`--userns=keep-id` inverts this mapping and is explicitly not supported.

**Validated end-to-end on real AWS spot instances** (Ubuntu 24.04), for both
Podman and nerdctl: pulled the image, started a container in rootless mode,
confirmed a file written from inside the container lands on the host owned by
the real user (not root), patched the running container with the new
`entrypoint.sh`/`firesim-lab-shell` and restarted it, and confirmed the
informational message fires instead of the warning. `firesim-lab-shell` was
also confirmed to correctly reach `fslab --version` in both cases.

Two setup-only findings surfaced while *manufacturing* a rootless nerdctl test
environment (irrelevant to firesim-lab's own code, but worth recording since
they'd otherwise look like this project's bugs if hit during troubleshooting):
on Ubuntu 23.10+, `apparmor_restrict_unprivileged_userns` blocks
`containerd-rootless-setuptool.sh install` outright (fixed by the AppArmor
profile the tool's own error message suggests); and rootless containerd's
default config fails fatally on `/var/run/nri/nri.sock: permission denied`
unless the CRI plugin is disabled (`disabled_plugins = ["io.containerd.grpc.v1.cri"]`
in `~/.config/containerd/config.toml`) — firesim-lab doesn't use CRI, so this
is a safe deviation from containerd's defaults. Neither of these is something
firesim-lab's install scripts need to handle, since Setup S1 doesn't install
rootless mode.

---

## 4. Delivery model

- **Location — this repo doubles as the marketplace *and* the plugin.** Skills
  live under **`skills/`** at the repo root (not `.claude/skills/`, which would
  only activate inside this repo); sub-agents under **`agents/`**. The marketplace
  manifest sits at the fixed **`.claude-plugin/marketplace.json`** (the file
  `/plugin marketplace add` reads), and the plugin manifest at
  **`.claude-plugin/plugin.json`** with `source: "."`. Full tree + the
  `marketplace.json` contents are in §12.
- **Why same repo:** the skills are **tagged and released with the tool** (one set
  of git tags, the one `release.yml`), so the plugin content at tag `vX.Y.Z`
  matches the tool at that tag — making the §2.5 MAJOR.MINOR binding *structural*
  rather than a manual cross-repo sync. A separate repo would reintroduce exactly
  that skew.
- **Distribution:** **marketplace / plugin** is the front door; installing the one
  plugin makes all three skills available. README documents the two-line install:
  ```
  /plugin marketplace add pentarisc/firesim-lab
  /plugin install firesim-lab@firesim-lab
  ```
- **Portability:** the `skills/<name>/SKILL.md` layout is the portable Agent-Skills
  convention — other tools can consume `skills/` directly; the `.claude-plugin/`
  wrapper is Claude Code's installer layer and is inert elsewhere.
- **Relationship to `install.sh`:** the host toolchain (the `firesim-lab`
  launcher at `~/.local/bin/firesim-lab` + the Docker image) and the **skills** are
  **independent installs**. `install.sh` remains **as-is** as the toolchain
  installer; the skills do *not* ship through it. The **`firesim-lab-setup`** skill
  detects the toolchain and, with per-step permission, can **run `install.sh` /
  pull the image / configure** the host itself (§5) — so the plugin is the single
  AI-native entry point and can bootstrap a fresh host.
- **Maintainer/dev note:** because Setup can install/configure directly (with
  permission), maintainers can validate the onboarding path without `install.sh`.
  For testing the skills *inside this repo*, optionally symlink them into the
  repo's `.claude/skills/` (maintainer-only nicety, not part of user delivery).

---

## 5. State machine (gated, idempotent, resumable)

The flow spans the three skills. Every node is a **checkpoint**; because every
`fslab` step is hash-aware and idempotent, any skill can re-enter at any node by
reading the state stamp (§2.3). The ordering is the intended happy path, not a
rigid lockstep.

```
HELP skill — on demand (pull) ────────────────────────────────────────────────
 H. Show the flow overview (§16) and name the other skills. No marker, no state.

SETUP skill — run once per host/account; writes the stamp ─────────────────────
 S1. Host prereqs: container runtime running? firesim-lab launcher? image pulled?
     └─ DETECT + OFFER TO RUN (per-step confirm) — may run install.sh / pull image
     └─ launcher is TTY-guarded: only --pull/--status/--down/--clean-cache/--upgrade/
        --help run non-interactively; bare `firesim-lab` (init/start) needs a TTY —
        drive it by pre-seeding the prompted fields (VERILATOR_THREADS,
        ENABLE_CUSTOM_PLUGINS; both have defaults) or hand the command to the user
 S2. Workspace init: is .firesim-lab.env present? if absent, run the launcher
 S3. Container running? discover it; establish firesim-lab-shell path (§3)
     └─ detect the active tool version (fslab --version / FIRESIM_LAB_VERSION;
        §2.5) and pin it in the stamp — all skills bind to it; never "latest"
 S4. AWS provisioning — OPT-IN, only if the user wants F2 (ask intent first; §9):
     ├─ console/quota/account = EXPLAIN + LINK + VERIFY — incl. request the slow
     │  F2 quota EARLY (approval can take a day or two); metasim-only users skip
     ├─ admin-CLI (roles, key pair, PassRole) = OFFER TO RUN scripts, per-step
     │  confirm — solo-developer admin only; org-developer = direct to their admin
     └─ first-time `aws configure sso` (create the login profile)
     → stamp: setup done; AWS provisioned (or skipped)
 S5. Notifications — OPT-IN (ask intent; §20): want a ping when a task finishes or
     needs attention? → existing channel (reuse) OR scaffold + guide a new one
     (webhook-first; agent can't complete auth — human pastes URL / OAuths)
     → stamp: notifications block (enabled + events + channel ref; never a secret)

SIMULATION skill — every iteration; self-orchestrates; binds to stamp version §2.5
  metasim ─────────────────────────────────────────────────────────────────────
 1. Inputs: RTL path(s) + top module        [ASK / propose from open VSCode file]
 2. Project: ask name → fslab new → docker cp RTL + payload into /target/<proj>
 3. Bridges: ask which → check DUT ports vs registry required ports
    └─ missing required ports = HARD STOP (report; needs user RTL change)
 4. Configure fslab.yaml: top-level host (emulator+driver_name+sources §13#11/#12),
    clk/reset/enable, port_map, ref: params, mem_base   [INFER + SHOW; user vetoes]
 5. fslab generate → build (compile-fix loop; build via build-runner sub-agent §10)
    ├─ logic/semantic/elaboration error = REPORT to user, do not fix, wait, rebuild
    └─ Verilator -Wall width-lint        = MAY apply minimal sized-literal fix,
                                           SHOW the diff; never logic
 6. Success criterion: ask what counts as pass → fslab sim → evaluate
    → stamp: metasim PASSED (+ evidence)
    ═══════════ HARD GATE: stamp must show metasim passed ═══════════
  F2 ──────────────────────────────────────────────────────────────────────────
 7. AWS preflight (VERIFY-ONLY; §9): probe roles/key/PassRole/quota/SSO session
    └─ gap = point back to `firesim-lab-setup`; do NOT provision here
 8. Recurring SSO login (device-code; §9.4) — show code, user approves, poll
 9. F2 questionnaire: profile/region, SSO mode, build/run host models, fpga_slot,
    publish mode, spend ack  → patch fslab.yaml target.*
10. fslab build fpga (EC2 launch / AFI create = HARD SPEND CONFIRM)
    └─ background build-monitor sub-agent → on image: pull artifacts → TERMINATE EC2
       → RETURNS report to skill; the skill (foreground) sends the notification (§20)
11. Patch fslab.yaml with AGFI/image → fslab sim fpga (detached) →
    background run-monitor sub-agent → on completion: pull output → STOP F2 →
    RETURNS report to skill; the skill (foreground) sends the notification (§20)

Notifications: only the foreground skill ever sends; sub-agents return reports (§20).
```

---

## 6. Interaction model — three tiers + staged questionnaires

### 6.1 The three input tiers

Every input the flow needs falls in exactly one tier:

- **ASK (never guess):** top module + RTL path(s); which bridges; `port_map`
  overrides *when bridge↔DUT names don't auto-align*; `mem_base`; the metasim
  **success criterion**; and (Phase 2) AWS profile/region, **SSO mode**, build/run
  host models, and any spend confirmation.
- **INFER + SHOW (propose, user vetoes):** clk/reset/enable port designation;
  `ref:` param mappings (e.g. `ADDR_W↔addr_bits`); `+max-cycles` from payload
  size × baud; width-lint sized-literal fix proposals.
- **VERIFY silently (the skill checks the result, doesn't ask):** container/shell
  context; `payloads/` (plural) path; that build/elaboration actually succeeded.

### 6.2 Staged questionnaires (Option A — staged, not one upfront form)

The user interacts through **staged questionnaires** across the Setup and
Simulation skills, because some answers only become meaningful after a prior step
runs:

- **Setup questionnaire** (`firesim-lab-setup`) — prereq remediation consent (per
  step) + workspace init + **AWS intent** (plan to use F2? **solo-developer vs
  org-developer**) + **notification intent** (want pings when a task finishes /
  needs attention? already have a channel vs scaffold a new one; §20). On "F2 yes"
  it drives the opt-in AWS provisioning (S4, §9); metasim-only users skip all AWS.
- **Project/RTL questionnaire** (`firesim-lab-sim`) — RTL path(s), top module
  (propose from the open VSCode file when possible), project name.
- **Bridge questionnaire** (`firesim-lab-sim`) — which bridges; before the port
  check.
- **Post-`init` configuration** (`firesim-lab-sim`) — clk/reset/enable +
  `port_map` are presented **after** `fslab init` parses the real ports,
  **pre-filled with the skill's INFER proposals** for the user to veto. (The
  "edit post-init" strategy: the skill patches `fslab.yaml`, it does not author it
  from scratch.) It must also author the **mandatory top-level `host:` emulator
  block** (`emulator` + `driver_name`) and list the generated driver in
  `host.sources` — these are **required** for `generate`/link to succeed, not
  optional (§13 #11, #12).
- **Success-criterion question** (`firesim-lab-sim`) — what constitutes a passing
  metasim (the gate contract; see §7).
- **F2 questionnaire** (`firesim-lab-sim`) — appears **only after the gate
  passes**: AWS profile/region, SSO mode, build/run host models, publish mode,
  `fpga_slot`, spend acknowledgement.

---

## 7. The metasim GATE

The gate contract is **user-defined via the success-criterion question** rather
than hardcoded. Supported criterion shapes the skill should offer:

- **Expected-output match** — a substring/regex the UART output must contain
  (e.g. a known greeting). Strongest signal.
- **Clean-exit + non-empty UART** — the sim completed without error and produced
  output.
- **Manual confirm** — the skill shows the captured output and the user confirms
  pass/fail.

Notes the skill must encode when evaluating:

- UART output goes to **stdout, interleaved** with FireSim's banner (no `uartlog`
  in metasim) — parse accordingly (§13 #8).
- `+max-cycles` must be sized to the bytes you expect to see (≈11 bytes / 100k
  cycles at 115200 baud / 100 MHz). Compute from payload size × baud, or warn
  about truncation (§13 #9).

**Phase 2 is not offered until the gate passes.**

---

## 8. Guardrails

1. **User RTL is read-only — with one narrow exception.**
   - **Logic / semantic / elaboration errors:** the skill **reports and never
     fixes**; it surfaces the diagnostics, waits for the user to fix the RTL, then
     re-runs `fslab build`. The report is structured per §20 — `error_diagnosed`
     (summary + suggested fix) when the skill can root-cause it from the log,
     `error_opaque` (summary + the relevant log excerpt, no invented fix) when it
     cannot.
   - **Verilator `-Wall` width-lint warnings** (the trivial sized-`$clog2`-cast /
     sized-comparison class made fatal by the FireSim Makefrag, §13 #4): the
     skill **may auto-apply a minimal sized-literal fix**, but **shows the diff**
     and never makes a logic change. Fixes are limited to mechanical width
     sizing.

2. **Never auto-spend on AWS.** EC2 launch and AFI create are **hard
   confirm-gated** — the skill surfaces the action (and instance type/region)
   and waits for explicit approval.

3. **Never *silently* create AWS/IAM resources — but the skill may run
   documented setup scripts with explicit per-step confirmation.** It **always
   verifies first** (read-only probes) and reports gaps. For the **admin-CLI
   layer** (the two instance-profile roles, the SSH key pair, the `iam:PassRole`
   grant — all with exact commands in the setup docs) the skill **offers to run
   bundled scripts**, one confirmed step at a time, and **only when a
   solo-developer admin profile with IAM-write is detected**. For an
   **org-developer** (intentionally lacks `iam:CreateRole`) it does **not**
   attempt creation — it directs the user to their admin and verifies the
   admin-provisioned resources. **Console / account / quota** actions (account
   creation, billing, enabling Identity Center, permission sets, the F2 service
   quota) are **explain + link + verify only** — they cannot be scripted. See §9.

4. **Fully-automatic cloud cleanup — but evidence-preserving.** On completion the
   monitoring sub-agent **terminates the build EC2** and **stops the F2 run host**
   with no prompt (cost safety), **but first pulls back logs/artifacts via
   `fslab monitor`** so even a failed build leaves its diagnostics behind.

5. **`mem_base` sanity.** FASED `mem_base` must contain the addresses the DUT
   actually drives (`+loadmem` writes at offset 0). The skill validates/sanity-
   checks this rather than accepting a mismatched base that hangs the sim
   (§13 #5).

---

## 9. AWS readiness, provisioning & SSO (Setup provisions, Simulation verifies)

AWS is needed **only** for the F2 path; the AWS setup docs themselves say
everything here can be deferred until the design works in metasim. AWS work is
**split across two skills by frequency**:

- **Provisioning (once) → `firesim-lab-setup`, step S4** (opt-in): account/IAM/
  roles/key pair/quota + first-time `aws configure sso`. Done early so the slow F2
  quota can be approving while the user iterates in metasim.
- **Login + verify (every F2 run) → `firesim-lab-sim`, steps 7–8**: a verify-only
  readiness probe and the recurring `aws sso login`. It **does not provision** —
  on a gap it points the user back to Setup.

Source of truth: [docs/portal/setup/aws/](../portal/setup/aws/) —
[index](../portal/setup/aws/index.md),
[aws-primer](../portal/setup/aws/aws-primer.md),
[identity-center-sso](../portal/setup/aws/identity-center-sso.md),
[firesim-lab-aws-setup](../portal/setup/aws/firesim-lab-aws-setup.md).

All AWS commands run **inside the container** (it ships AWS CLI v2; the host may
not). Every command uses an explicit `--profile` (modern SSO has no default
profile).

### 9.1 The four layers (different automation profiles)

| Layer | Examples | Skill behavior |
|---|---|---|
| **Console / account / quota** | account + root security, billing budget, enable Identity Center, users/groups/**permission set**, **F2 service quota** (default 0, ~1–2 day approval) | **Explain + link + verify** only — not scriptable |
| **Admin-CLI** | `fslab-fpga-builder` + `fslab-fpga-runner` instance-profile roles, SSH key pair (`firesim-lab` / `~/.ssh/fslab_ed25519`), `iam:PassRole` grant on the permission set | **Offer to run bundled scripts**, per-step confirm — **solo-developer admin profile only** |
| **Developer login** | `aws configure sso` (first run), `aws sso login` device-code, `aws_profile:` in `fslab.yaml` | **Guide + run** (device-code, §9.4) |
| **Verification** | `get-caller-identity`, `get-instance-profile`, `get-role-policy`, quota query, F2 region/AMI check | **Run freely** (read-only) |

**Which skill runs which layer:** the **Console/quota**, **Admin-CLI**, and
*first-time* `aws configure sso` layers run in **`firesim-lab-setup`** (S4,
once). The **Verification** probes and the *recurring* `aws sso login` run in
**`firesim-lab-sim`** (steps 7–8, every F2 run).

### 9.2 Solo-developer vs org-developer (from the Setup intent answer)

- **Solo developer** (own admin, personal account): the skill may run the
  admin-CLI scripts (role/key-pair/PassRole creation) under the admin profile,
  per-step confirmed.
- **Org developer** (logs in via a `FireSim-Developer` permission set, lacks
  `iam:CreateRole`/`CreateInstanceProfile` by design): the skill **does not**
  attempt creation — it verifies the admin-provisioned roles/profile/PassRole
  exist and, on a gap, produces the exact commands for the user's **admin** to
  run. (Note the doc's warning: an `AWSReservedSSO_*` permission-set role cannot
  back an EC2 instance profile — that is always a separate regular IAM role.)

### 9.3 Readiness probes the preflight runs

Active SSO session (`get-caller-identity`); build role + instance profile
(`fslab-fpga-builder`); run role (`fslab-fpga-runner`); `iam:PassRole` grant
present; SSH key pair exists in the chosen region; **F2 quota > 0** (else no
instance launches); region is F2-capable and an FPGA Developer **AMI** id is
available for it.

**Least-privilege + graceful, not admin-assuming (validated live 2026-06-19
against the `FireSim-Developer` permission set).** The probes must succeed for a
*normal org developer*, not only an admin, and must distinguish a missing resource
(GAP) from a probe the identity is not allowed to run (UNKNOWN — informational,
never a false gap):

- **Roles are checked via their INSTANCE PROFILE** (`iam:GetInstanceProfile`,
  granted by the PassRole policy), **not `iam:GetRole`** (which the developer lacks
  — `get-role` returns AccessDenied even when the role exists).
- **The F2 quota is discovered BY NAME** (`QuotaName` contains
  `"On-Demand F instances"`) via `service-quotas list-service-quotas` then
  `list-aws-default-service-quotas` — **no hardcoded, possibly-wrong quota code**.
  Developers cannot read `servicequotas` at all → report **UNKNOWN and assume the
  quota is available** (tell the user to verify in the console / ask their admin);
  never a "quota is 0" gap. (Admin path returns the real value, e.g. 24 vCPU.)
- **The FPGA Developer AMI is owned by `aws-marketplace`** (owner `679593333241`),
  **not `amazon`** — `--owners amazon` silently returns nothing. Query
  `--owners aws-marketplace amazon`, name `FPGA Developer AMI*`.
- **`iam:PassRole` is NOT self-verifiable** by a developer (`simulate-principal-
  policy` needs an IAM principal ARN — an SSO assumed-role session ARN is rejected
  — and the permission is usually denied). Treat it as an info note, not a gap; the
  real proof is a successful build launch.

Every AccessDenied → UNKNOWN with guidance; only a genuinely absent resource is a
GAP (reported with its layer + remediation). Readiness fails only on a hard gap.

### 9.4 SSO device-code UX

The container is headless, so **device-code** is the correct mode. **SSO mode is
a questionnaire field**, offering:

- **`skill-driven`** (default): the skill launches
  `aws sso login --use-device-code --profile <name>` **backgrounded inside the
  container**, **scrapes the verification URL + user code from stdout**, surfaces
  them to the user **immediately**, then **polls for completion** (e.g.
  `aws sts get-caller-identity` / login process exit) with an **expiry/timeout
  fallback** that re-prompts.
- **`user-paste`**: the user runs the login themselves and pastes the
  URL/code/result back; the skill stays hands-off on the login itself.
- **`already-logged-in`**: skip login; verify credentials are valid.

The first-time `aws configure sso` (creating the login profile) is part of
**Setup** (S4); `firesim-lab-sim` performs only the **recurring** `aws sso login`
here. Credentials persist via the `~/.aws` bind mount; there is no AWS CLI on the
host.

---

## 10. Sub-agents (inside `firesim-lab-sim`)

Per the §2.4 rule, `firesim-lab-sim` delegates **verbose or long autonomous**
work to sub-agents, keeping all user interaction in the skill itself:

- **`build-runner`** (metasim, step 5): runs the verbose `fslab build` (sbt /
  Golden Gate / Verilator) in isolated context and returns a **distilled verdict**
  — pass, a width-lint diff it applied, or a concise logic-error diagnostic. The
  hundreds of lines of build output never enter the skill's context; the
  compile-fix loop control and the user hand-off stay in the skill (§8.1). The
  verdict maps onto the §20 report object (`auto_fixed` / `error_diagnosed` /
  `error_opaque`). When the failure is a **schema/config validation error**, the
  diagnosis must be attributed to the **actual schema field path that raised it**
  (resolve against the model), not inferred from a nearby YAML key — a confident
  wrong attribution is worse than `error_opaque`. (In validation testing the runner
  mis-blamed a missing **top-level** `host` on `target.build.host`; §13 #11.)
- **`build-monitor`** (F2, step 10, background): poll `fslab monitor build`; on
  image-ready, pull logs/artifacts, then **terminate the build EC2** (§8.4).
- **`run-monitor`** (F2, step 11, background): after `fslab sim fpga --detach`,
  poll `fslab monitor run`; on completion pull the output, **stop the F2 host**,
  and report back.

These are the right use of the sub-agent primitive (isolated, non-interactive,
context-absorbing). Anything needing user input stays in the skill.

**Sub-agents never send notifications (§20).** Each returns its report (verdict /
diagnostic / completion) to the foreground skill; cleanup (terminate/stop) still
happens autonomously for cost safety, but the **notification is sent by the skill**
when the harness re-invokes it on the background task's completion. This keeps a
single notifier, avoids the background-context MCP limitation, and ensures any
follow-up decision is taken in the interactive skill, not the isolated agent.

---

## 11. Charter & responsibility model

The §13 gotchas are **not a CLI backlog** — they are either **already fixed** or
the **intended manual-configuration surface** that `fslab init` deliberately
leaves to the user (it parses RTL verbatim with pyslang and does not infer
intent). The skill's core job is to **be the AI standing in for the human at that
intended post-`init` configuration step.** Two columns:

| **CLI already owns** (skill *verifies* the result) | **Skill assists the user** (the intended-manual surface) |
|---|---|
| Scaffolding (`fslab new`), hash-aware idempotency | clk / reset / **enable** port designation |
| The actual build / Golden Gate elaboration / Verilator | Bridge selection + port check vs registry |
| `firesim-lab-shell` privilege-drop plumbing | `port_map` overrides when names don't align |
| `+loadmem` / payload path mechanics | `ref:` param mapping (avoid duplication) |
| Detached build/run + `fslab monitor` | `mem_base`, `+max-cycles` sizing |
|  | Width-lint sized-literal fixes (narrow, diff-shown) |
|  | Conversational AWS SSO + cloud lifecycle/cleanup |

---

## 12. Plugin & marketplace layout (three skills + sub-agents)

```
firesim-lab/                       # repo root = marketplace AND plugin
  .claude-plugin/
    marketplace.json               # read by `/plugin marketplace add pentarisc/firesim-lab`
    plugin.json                    # plugin manifest (source "."); bundles all skills + agents
  skills/
    firesim-lab-help/
      SKILL.md                     # the overview/map; names the other skills (§16)
      reference/overview.md        # §16.1 canonical overview copy
    firesim-lab-setup/
      SKILL.md                     # run-once provisioning; reads/writes the stamp
      reference/
        prereqs.md                 # host prereq detection, install.sh guidance, .firesim-lab.env
        aws-provisioning.md        # §9 console/quota + admin-CLI + first-time configure-sso
      scripts/
        detect-context.sh          # in-container vs host-driving-container; container discovery
        verify-aws.sh              # §9.3 read-only readiness probes
        aws-create-build-role.sh   # firesim-lab-aws-setup Step 4 (solo-admin only)
        aws-create-run-role.sh     # firesim-lab-aws-setup Step 5 (solo-admin only)
        aws-create-keypair.sh      # firesim-lab-aws-setup Step 3 (solo-admin only)
        aws-grant-passrole.sh      # identity-center-sso PassRole grant (solo-admin only)
    firesim-lab-sim/
      SKILL.md                     # the recurring flow; self-orchestrates from the stamp
      reference/
        metasim.md                 # port_map, ref:, mem_base, lint, max-cycles (§13)
        fpga.md                    # F2 build/run pipelines, target.build/target.run, cleanup
        aws-login.md               # §9.4 recurring device-code login + verify-only preflight
      scripts/
        # detect-context.sh / verify-aws.sh are NOT mirrored here — they are
        # single-sourced in firesim-lab-setup/scripts/ and referenced via
        # $CLAUDE_PLUGIN_ROOT, so the `docker` literal stays in exactly one file
        # (seam 1, §3.1). Only sim-specific scripts live here.
        scrape-sso-code.sh         # §9.4 device-code login: --launch (scrape URL+code) / --poll / --verify-only
  agents/
    build-runner.md                # §10 metasim build executor (distilled verdict)
    build-monitor.md               # §10 background F2 build monitor + cleanup
    run-monitor.md                 # §10 background F2 run monitor + cleanup
```

Each `SKILL.md` stays lean; `reference/` files load only when that stage is
entered (a metasim-only run never loads `fpga.md`). Shared scripts
(`detect-context.sh`, `verify-aws.sh`) are **single-sourced** in
`firesim-lab-setup/scripts/` and referenced from `firesim-lab-sim` via
`$CLAUDE_PLUGIN_ROOT` rather than mirrored/symlinked — this keeps the `docker`
literal in exactly one place (seam 1, §3.1), which a mirrored copy would violate.
(Decided at build time, validated against a live account 2026-06-19.)

`marketplace.json` (the repo root doubles as a single-plugin marketplace):

```json
{
  "name": "firesim-lab",
  "owner": { "name": "pentarisc" },
  "plugins": [
    { "name": "firesim-lab", "source": ".",
      "description": "AI-accelerated firesim-lab: Help, Setup, Simulation skills",
      "version": "0.9.0rc1" }
  ]
}
```

Install from any project:

```
/plugin marketplace add pentarisc/firesim-lab
/plugin install firesim-lab@firesim-lab
```

The plugin `version` tracks the repo tag (§2.5): the plugin content at tag
`vX.Y.Z` matches the tool at that tag. (If `firesim-lab@firesim-lab` reads
awkwardly, set `marketplace.json` `name: "pentarisc"` → `firesim-lab@pentarisc`.)

---

## 13. Engineering gotchas the skill must encode

These cost real time when discovered the hard way; every one must be baked into
the skill's logic or its checklist. Each ends with **→** where it is handled in
this spec.

1. **Always run `fslab` via `firesim-lab-shell`, never bare `docker exec`.** The
   image's default exec user is **root**; bare `docker exec` creates root-owned
   project files and misses the `firesim-lab-cache` group, breaking SBT/ccache
   writes. `firesim-lab-shell` uses `gosu` to drop to the host UID (detected from
   `/target` ownership) *with* full supplementary groups. If the skill itself runs
   *inside* the container with `fslab` on `PATH`, call `fslab` directly and skip
   the docker layer — detect this first. **→ §3; §5 step S3.**

2. **`fslab init` does NOT infer clock/reset.** It parses RTL verbatim (pyslang)
   and emits ports as-is (e.g. `clk: "in logic"`). The Chisel shim templates pick
   `clock_port`/`reset_port` by matching the literal strings **`"in clock"` /
   `"in reset"`** — so the skill must rewrite the clock, reset (and enable)
   entries in `blackbox_ports` to those values, or the DUT clock/reset stay
   unconnected and nothing toggles. This is **intended user-config, not a bug**
   (`DUT.scala.j2` / `Top.scala.j2`). **→ §6.2 post-`init` INFER+SHOW; §11.**

3. **Payload directory is `payloads/` (plural).** `fslab new` scaffolds
   `payloads/`; older docs say `payload/`. `+loadmem` takes a full path — be
   consistent with the real path. **→ §6.1 VERIFY tier.**

4. **User RTL must be Verilator `-Wall`-clean.** The FireSim Verilator Makefrag
   compiles with `-Wall` (only `UNUSEDSIGNAL/DECLFILENAME/VARHIDDEN/UNDRIVEN`
   waived), making **width warnings fatal**. Expect width-lint failures (e.g. an
   unsized `$clog2` cast, an unsized comparison). Flag file in image:
   `/opt/firesim/sim/midas/src/main/cc/rtlsim/Makefrag-verilator`.
   **→ §8.1 (narrow sized-literal fix, diff-shown).**

5. **FASED `mem_base` must contain the addresses the DUT drives.** `+loadmem`
   writes the payload at offset 0 of the model; a mismatched base (e.g.
   `0x80000000` when the DUT reads from `0x0`) puts the read outside the modeled
   region → no response → the sim **hangs**
   (`templates/bridges/fased/wiring.scala.j2`). The value's *encoding* matters too
   — see #14. **→ §8.5.**

6. **Bridge `port_map` direction convention.** Keys are *bridge* port names;
   values are *DUT blackbox* port names. For FASED: `m_*` keys = DUT **master
   outputs** (aw/w/ar valid+payload, b/r ready); `s_*` keys = DUT **slave inputs**
   (aw/w ready, b/r valid+payload). The full key list per bridge is in
   `lib/registry.yaml` (`input_ports`/`output_ports`); a worked example is in the
   `fslab.yaml.j2` template comments. **→ §6.1 ASK (overrides).**

7. **Map design params into bridge params with `ref:` to avoid duplication.**
   Instead of hardcoding `addr_bits: 32`, write `addr_bits: { ref: ADDR_W }` to
   source from `design.parameters`. Validated mappings: `ADDR_W↔addr_bits`,
   `DATA_W↔data_bits`, `ID_W↔id_bits`, `USER_W↔user_bits`, `BAUD↔baud_rate`.
   Params with no 1:1 design param (`mem_base`, `mem_size`, `memory_region_name`,
   `freq_mhz` — different units from `CLK_HZ`) stay literal. Mechanism:
   `fslab-cli/fslab/schemas/resolvers.py` (`BridgeParam.normalize` /
   `resolve_refs`); the ref dict is `{ref: NAME}`. Literal params must also match
   the bridge stub's Scala type (see #13). **→ §6.1 INFER+SHOW.**

8. **UART output goes to stdout, interleaved.** The host UART model
   (`lib/bridges/src/main/cc/bridges/uart.cc`) prints received bytes to stdout
   (no `uartlog` file in metasim), so payload text can land on the same line as
   FireSim's startup banner (`FireSim fingerprint: 0x…`). Parse accordingly.
   **→ §7 gate evaluation.**

9. **Cycle-budget math for UART.** At 115200 baud / 100 MHz, each byte ≈ 8680
   cycles (10 bits × 868 cycles/bit) plus AXI latency (≈11 bytes per 100k
   cycles). Size `+max-cycles` from payload bytes × baud, or warn that output will
   be truncated. **→ §6.1 INFER+SHOW; §7.**

10. **`fslab sim`'s implicit-recompile path** had a bug (`_run_cmake_make()`
    missing `extra_args`/`debug`), **fixed 2026-05-30** in
    `fslab-cli/fslab/commands/sim.py`. If a stale image still has it, work around
    with `fslab sim --skip-rtl --skip-driver` after a completed build; prefer
    those flags whenever the binary is already current (faster).
    **→ already fixed; skill prefers `--skip-rtl --skip-driver` when current.**

11. **The top-level `host:` (emulator) block is mandatory, but `fslab init` emits
    it commented out.** `init` writes the top-level `host:` block (`emulator`,
    `driver_name`) **commented out**, yet `FSLabConfig.host` is a **required**
    field — so `fslab generate` aborts with `1 validation error for
    LiveFSLabConfig / host / Field required`. Note the bare `host` path is the
    **top-level** host, **not** `target.build.host` (a real trap: the active
    `target.build.host` block is present and unrelated, so the error misleads).
    The skill must author the block during post-`init` config: minimally
    `emulator: "verilator"` plus a `driver_name`. **→ §6.2 post-`init` config.**

12. **`host.sources` must list the generated driver, or the metasim link fails.**
    `USER_CC` in the generated `CMakeLists.txt` comes from `config.host.sources`
    (`fslab-cli/fslab/commands/context.py`: `user_cc_files =
    list(config.host.sources)`). The fslab-**generated** driver
    `src/main/cc/<driver_name>.cc` — which defines `create_simulation()` — is
    **not** auto-added; with an empty `host.sources` the Verilator link fails with
    `undefined reference to create_simulation`. The skill must add the generated
    driver to `host.sources`. **→ §6.2 post-`init` config.**

13. **UART `freq_mhz` must be an integer literal.** `UARTBridge.apply` types
    `freqMHz: Int`. A float in `fslab.yaml` (`freq_mhz: 100.0`) is rendered
    verbatim into the generated Scala (`UARTBridge(..., 100.0, ...)`) and sbt fails
    with `type mismatch; found Double(100.0) required Int`. Write `freq_mhz: 100`.
    (Sharpens #7: a literal bridge param must also match the bridge stub's Scala
    type.) **→ §6.1 INFER+SHOW.**

14. **FASED `mem_base`/`mem_size` are rendered as `BigInt("<value>", 16)` — write
    them as bare hex-digit strings.** `templates/bridges/fased/wiring.scala.j2`
    emits `BigInt("{{ mem_base.value }}", 16)` / `BigInt("{{ mem_size.value }}",
    16)`, so the value is parsed as **base-16 digits**. Writing `0x40000000` (YAML
    parses it to decimal `1073741824`) becomes `BigInt("1073741824", 16)` — a
    non-power-of-two address mask → Golden Gate elaboration fails with
    `AXI4SlaveParameters: minAlignment (N) must be >= maxTransfer (M)`. Write bare
    hex, **no `0x`, not decimal**: `mem_base: "0"`, `mem_size: "40000000"` (= 0x0 /
    0x40000000). `mem_base: 0x0` only survives by luck ("0" is base-agnostic).
    (Extends #5 with the encoding format.) **→ §8.5; §6.1 ASK.**

---

## 14. Open items deferred to build time

1. **SSO completion/expiry detection robustness** — reliably distinguishing
   success vs expiry vs denial when polling a backgrounded login; exact poll
   command and timeout values (§9).
2. **Prereq detection specifics** — the exact probes for "Docker running",
   "launcher installed", "image pulled" on the host (§5 step S1).
3. **Port-check semantics** — how strictly to match DUT ports against registry
   `input_ports`/`output_ports`, and how to present a partial mismatch (§5 node 3).
4. **Plugin manifest + marketplace plumbing** — `plugin.json` fields, marketplace
   manifest location (this repo vs a dedicated marketplace repo), and the
   versioning relationship to the existing `install.sh`/manifest machinery (§4).
5. **State-stamp location & schema** — **DECIDED, see §2.6.** Two skill-owned JSON
   sibling files split by scope: a **workspace-root** `.firesim-lab.skill-state.json`
   (host/AWS/version, written by Setup) and a **per-project**
   `.fslab/skill-state.json` (design/metasim/F2, written by Sim). The metasim→F2
   gate is tied to the CLI's existing `config_hash` (stale evidence re-opens it),
   and live F2 state is read from the existing build/run stamps, not copied.
   (Rejected: extending the CLI-owned, immutable `.fslab/meta.json`.)
6. **Solo-vs-org capability detection** — whether to trust the Setup intent answer
   alone or also probe IAM-write capability (e.g. `iam:CreateRole` via a dry-run
   / `simulate-principal-policy`) before offering the admin-CLI scripts (§9.2).
7. **AWS verification probe specifics** — exact CLI for the F2 **quota** check
   ("Running On-Demand F instances" > 0) and the per-region FPGA Developer **AMI**
   lookup, plus how to surface a *pending* (not-yet-approved) quota request (§9.3).
8. **RTD slug availability & fallback** — how to determine whether `/en/v<active>/`
   is published (RTD versions API vs an HTTP probe) and implement the
   nearest-published-patch fallback before warning (§2.5).
9. **Skill `fslab_version` declaration** — where each skill records its compatible
   MAJOR.MINOR (a `plugin.json` field vs a skill metadata file) and how it invokes
   the tool's `is_compatible` and surfaces the standard `--upgrade` message (§2.5).
10. **Notification channel wiring** — the concrete send mechanism per `channel.type`
    (webhook `curl` payload shape; which MCP "send" tools to support; the `local`
    `preferredNotifChannel` fallback), the exact scaffold-and-guide flow for a new
    channel (what the agent writes vs the human-only auth/reconnect steps), and how
    the foreground skill picks up a background sub-agent's report to send on (§20).

---

## 15. Decisions ledger (this conversation)

| # | Decision | Rationale |
|---|---|---|
| 1 | Vehicle = Skill, packaged as plugin, marketplace-distributed | Progressive disclosure, bundling, native updates; project skill rejected (out-of-tree audience) |
| 2 | Audience = end users; skill host = host driving the container | Users work in their own folders; Claude Code runs in host VSCode |
| 3 | Delivery = marketplace front door; `install.sh` unchanged, driven by the Setup skill | Single AI-native entry point; toolchain install stays decoupled |
| 4 | Setup prereqs = detect + offer to run (per-step consent) | Setup can bootstrap a fresh host without being reckless |
| 5 | Scope = full two-phase, F2 hard-gated behind metasim | Don't spend FPGA time/money on an unproven design |
| 6 | `fslab.yaml` = edit post-`init` (patch, not author) | Matches the validated flow + the `init` contract |
| 7 | Questionnaires = staged (Option A) | Port-map/clk-reset only meaningful after `init` parses ports |
| 8 | Gate criterion = user-defined via questionnaire | Different DUTs define "success" differently |
| 9 | RTL = read-only except narrow width-lint sized-literal fixes (diff-shown) | Never alter user logic; mechanical lint fixes are safe and shown |
| 10 | AWS = hard spend-confirm; verify-first; never *silently* create IAM, but may run setup scripts per-step-confirmed (solo-admin); console/quota = explain-only | End-user cost/safety; admin-CLI layer is scriptable per the setup docs |
| 10a | AWS gated behind F2: intent + opt-in provisioning in Setup (S4); verify-only + recurring login in Simulation | Metasim needs no AWS; quota approval is slow so provision early in Setup |
| 11 | Cloud cleanup = fully automatic, evidence-preserving | Cost safety without destroying diagnostics |
| 12 | Long F2 build/run = background sub-agents (detached + monitor) | Survive sleep; isolated polling |
| 13 | Help = always-on inline per-question help + on-demand keyword + a pull-only Help skill for the overview (no marker) | Newcomers find questions confusing; decomposition makes the overview pull-based, eliminating the suppression marker |
| 14 | Decompose into 3 skills by separation-of-concerns / run-frequency: Help (pull), Setup (once), Simulation (each iteration); bundled as one plugin | Smaller per-invocation context; direct invocation of any part; no marker; self-orchestrating Sim |
| 15 | AWS seam: provisioning + first-time configure-sso + quota nudge in Setup; recurring login + verify-only in Simulation | One-time vs recurring; the slow F2 quota must be requestable early |
| 16 | Heavy autonomous work → sub-agents inside Simulation (build-runner, build/run monitors); interaction stays in the skill | Sub-agents are non-interactive + isolate context; absorbs verbose build/monitor output |
| 17 | Inter-skill state stamp is the contract; metasim→F2 gate enforced by reading the stamp | Separate invocations can't share memory; gate robust across sessions / direct invocation |
| 18 | Version awareness = detect-and-bind to the installed tool (single source of truth); never reference `latest`/`stable` | Skills are a thin layer over whatever tool/image is installed |
| 19 | Skill↔tool compatibility at MAJOR.MINOR, reusing the tool's `is_compatible`; skill patches ship independently | Consistent with fslab.yaml/registry.yaml gating; same `--upgrade` UX |
| 20 | RTD links pinned to `/en/v<active>/`; fallback = nearest published patch, else warn | Guidance must match the installed version; never silently drift to latest |
| 21 | All three skills bind to the one workspace-pinned version (`FIRESIM_LAB_VERSION`) | The existing matched-set pin makes "one version" free; reuse, don't reinvent |
| 22 | State stamp = **two skill-owned JSON files** (workspace-root host/AWS/version + per-project design/metasim/F2), JSON format; gate tied to the CLI `config_hash`; live F2 state read from build/run stamps; no new `fslab` command (§2.6) | Facts split by scope/writer; `.fslab/` already gitignored/local; reusing the hash mechanism makes stale evidence re-open the gate for free; keeps skills a thin layer. Rejected extending the immutable CLI-owned `.fslab/meta.json` |
| 23 | Errors/notifications = one **report object** (`auto_fixed`/`error_diagnosed`/`error_opaque`/`needs_decision`/`completed`), inline always-on, push optional; **diagnosable→summary+fix, opaque→summary+excerpt (never invent a fix)**; channel **webhook-first** (MCP/local alts), push set = attention+completion, setup = **scaffold+guide** (auth is human-only); **only the foreground skill notifies** — sub-agents return reports (§20) | One taxonomy governs inline + push so the "what" holds even with push off; hooks can't carry composed content; webhook works without OAuth; single notifier dodges the background-MCP limit and keeps decisions interactive |

---

## 16. User help & onboarding

Skills have **no native help UI** — "help" is skill-authored behavior. The
decomposition (§2.2) makes help **pull-based**, which removes the need for any
"don't show again" marker. Three tiers:

1. **The `firesim-lab-help` skill (pull).** The user invokes it deliberately to
   get the flow overview (§16.1) and a map of the other two skills. Because it is
   pull-not-push, there is **nothing to suppress** — the per-user onboarding
   marker from the earlier monolithic design is **gone**. (`firesim-lab-setup` and
   `firesim-lab-sim` also point the user at it on entry when the stamp shows a
   first run.)
2. **Inline per-question help (always on).** Every staged question carries
   plain-language `description` text per option plus a one-line *why we're asking*
   — so a newcomer is never stranded on a confusing question. Never suppressed.
3. **On-demand help (any time).** The user can type `help` / *"what does this
   do?"* / *"why are you asking this?"* at any stage; the skill explains the
   current stage and, on request, re-shows the overview. No special command is
   required (it's a conversation), but `help` is advertised as a keyword.

### 16.1 The first-run overview (canonical copy)

> **What's about to happen — the firesim-lab flow**
> 1. **Setup (one-time):** check Docker + firesim-lab are installed and your
>    workspace is initialized. Free.
> 2. **Metasim — prove your design in software:** scaffold a project, add your
>    Verilog/SV + a test payload, pick bridges (UART, FASED memory, BlockDev),
>    and run a fast *desktop* simulation. No FPGA, no cost. We confirm it behaves
>    correctly here **first**.
> 3. **AWS setup (only if you want real FPGA):** help you log in (SSO) and check
>    your AWS roles/quota. Nothing here costs money by itself.
> 4. **F2 — run on a real FPGA:** build a bitstream and run it on an AWS F2
>    instance. This **costs money and takes time**, so we always confirm before
>    spending and shut the cloud machines down for you when done.

This copy lives in `firesim-lab-help/reference/overview.md`, loaded on demand and
re-shown by the `help` keyword in the other skills.

---

## 17. Proven metasim flow (the validated backbone)

The metasim path was validated end-to-end in a real container run (2026-05-30) —
this is the reference design the skill should be validated against before any
Phase 2 wiring:

- **Project:** `fslab new uart-print-test` under `/target`.
- **DUT:** an AXI4 read-master that streams FASED memory bytes over UART 8N1 —
  [examples/axi-uart/AXIUARTPrinter.v](../../examples/axi-uart/AXIUARTPrinter.v)
  (the tested artifact).
- **Payload:** [examples/axi-uart/sample.hex](../../examples/axi-uart/sample.hex).
- **Result:** clean Golden Gate / FASED port-map elaboration, clean Verilator
  build, UART output `Hello frfom FiReim! Hell` followed by FASED fill bytes —
  exactly as expected.
- **Walkthrough:** [examples/axi-uart/README.md](../../examples/axi-uart/README.md).

---

## 18. Metasim command sequence (what the skill automates)

All `fslab` commands run **inside the container** (§3). Discover and invoke:

```bash
# Compose names it firesim-lab-firesim-lab-<workspace>
docker ps --filter name=firesim-lab --format '{{.Names}}'
docker exec <container> firesim-lab-shell bash -lc 'cd /target/<proj> && fslab <...>'
```

Lifecycle (each step is hash-aware — effectively a no-op when nothing changed):

```bash
fslab new <proj>                          # scaffold under /target
#   copy user RTL -> /target/<proj>/user_rtl/<top>.v   (docker cp from host)
#   copy payload  -> /target/<proj>/payloads/<file>    (NOTE: "payloads", plural)
fslab init -t <TopModule> -f <top>.v      # parse ports/params -> fslab.yaml
#   EDIT fslab.yaml -> clk/reset/enable, bridges, port_map, ref:, mem_base (§6, §13)
fslab generate                            # render Chisel shims, CMake, driver
fslab build metasim                       # sbt + Golden Gate + Verilator/VCS
fslab sim --args '+loadmem=/target/<proj>/payloads/<file> +max-cycles=<N>'
```

---

## 19. Reference index (firesim-lab repo)

**CLI / commands**
- Project overview + command table: [CLAUDE.md](../../CLAUDE.md)
- Command implementations: [fslab-cli/fslab/commands/](../../fslab-cli/fslab/commands/)
  — `init.py`, `build.py`, `sim.py`, `fpga.py`, `monitor.py`, `abandon.py`

**Config schema / validation**
- Canonical, fully-commented `fslab.yaml` template (FASED/UART/iceblk `port_map`
  examples, `target.build`/`target.run`):
  [fslab.yaml.j2](../../fslab-cli/fslab/templates/fslab.yaml.j2)
- Bridge registry (ports, params, platforms, bitbuilders, runners):
  [lib/registry.yaml](../../lib/registry.yaml)
- Schemas: [fslab-cli/fslab/schemas/](../../fslab-cli/fslab/schemas/) —
  `parser.py`, `resolvers.py` (bridge params + `ref:`), `project.py`,
  `registry.py`, `host_model.py`

**Code-generation templates**
- Top-level: [fslab-cli/fslab/templates/](../../fslab-cli/fslab/templates/) —
  `DUT.scala.j2`, `Top.scala.j2`, `Config.scala.j2`, `driver.cc.j2`,
  `CMakeLists.txt.j2`, `build.sbt.j2`
- Per-bridge: [templates/bridges/](../../fslab-cli/fslab/templates/bridges/)
  (`fased/`, `uart/`, `iceblk/`)
- Remote wrappers: `templates/remote_build/f2.sh.j2`, `templates/remote_run/f2.sh.j2`

**F2 pipelines / AWS**
- Pipelines guide: [docs/run-pipeline-guide.md](../run-pipeline-guide.md)
- Build side: [fslab-cli/fslab/bitstream/](../../fslab-cli/fslab/bitstream/)
  (`bitbuilder.py`, `buildhost.py`, `publisher.py`, `build_stamp.py`, `monitor.py`)
- Run side: [fslab-cli/fslab/runtime/](../../fslab-cli/fslab/runtime/)
  (`launch.py`, `runner.py`, `runconfig.py`, `payloads.py`, `monitor_run.py`,
  `run_stamp.py`)
- F2 platform entry (instance types, `host_models`, `publish`, `runner`): the
  `f2` block in [lib/registry.yaml](../../lib/registry.yaml)
- AWS setup: [docs/portal/setup/aws/](../portal/setup/aws/)
  ([index](../portal/setup/aws/index.md),
  [aws-primer](../portal/setup/aws/aws-primer.md),
  [identity-center-sso](../portal/setup/aws/identity-center-sso.md),
  [firesim-lab-aws-setup](../portal/setup/aws/firesim-lab-aws-setup.md)); also
  [docs/aws-setup.md](../aws-setup.md), [docs/aws-setup-run.md](../aws-setup-run.md)

**Container / environment**
- Host launcher (start/enter/`--down`/`--pull`/`--status`):
  `~/.local/bin/firesim-lab` (installed per-user, not in repo)
- In-image privilege-drop shell: `/usr/local/bin/firesim-lab-shell` (gosu)
- Compose / mounts (incl. `/target` bind, SBT/ccache volumes, `~/.aws`, `~/.ssh`):
  [docker/docker-compose-dev.yaml](../../docker/docker-compose-dev.yaml)

**Worked example**
- [examples/axi-uart/](../../examples/axi-uart/) — `AXIUARTPrinter.v` (tested),
  `sample.hex`, `README.md`

---

## 20. Error reporting & notifications

Two halves: **what** the skill says when something fails or finishes, and **how**
it optionally pushes that to the user's messaging channel. The unifying principle:
**there is one report, two transports.** Inline rendering in the conversation is
**always on**; a push notification is **optional transport of the same content**.
So the *what* governs how every error/outcome is presented **even when the user
has notifications off** — turning push on never changes the message, only its
reach.

### 20.1 The report object (the "what")

Every notify-worthy event produces one structured report. Five `kind`s:

| `kind` | When | Content | Pushes by default? |
|---|---|---|---|
| `auto_fixed` | a width-lint sized-literal fix was applied (§8.1) | summary + **the diff** | no — informational, inline-only |
| `error_diagnosed` | the skill root-causes the failure from the log | summary **+ suggested fix** (the user edits RTL/config) | yes (attention) |
| `error_opaque` | the skill **cannot** root-cause it | summary of the problem + the relevant **log excerpt / `file:line`** — **never a fabricated fix** | yes (attention) |
| `needs_decision` | spend gate, SSO code ready, port-check hard stop, success-criterion confirm | summary + exactly what is being asked | yes (attention) |
| `completed` | metasim passed; F2 image ready; F2 run output ready | summary + where the output/artifact is | yes (completion) |

Common shape (the `build-runner` verdict maps onto this, §10):

```json
{
  "kind": "error_diagnosed",
  "stage": "build:verilator",        // generate | build:sbt | build:golden_gate | build:verilator | sim | f2_build | f2_run | aws_sso | ...
  "title": "Verilator width-lint failed in AXIUARTPrinter.v",
  "summary": "…plain-language paragraph…",
  "suggested_fix": "…concrete next step (error_diagnosed / needs_decision only)…",
  "log_excerpt": "…relevant lines + file:line (error_* only)…",
  "needs_user_action": true
}
```

**Diagnosable vs opaque is the heart of the "what".** The skill (or the
`build-runner` sub-agent) **attempts** root-cause from the log: confident →
`error_diagnosed` with a fix; not confident → `error_opaque`, handing over the raw
material so the user has what they need, **without inventing a fix**. This
formalizes the §8.1 "report, never fix" rule for user RTL.

### 20.2 Delivery (the "how")

- **Single sender: the foreground skill.** Only `firesim-lab-sim` (the interactive
  skill) sends notifications. **Sub-agents never notify** — `build-runner`,
  `build-monitor`, `run-monitor` return their report to the skill, which sends it
  when the harness re-invokes the skill on the background task's completion (§10).
  One notifier; decisions stay interactive; and the channel only ever runs in the
  main agent's context (sidestepping the known background-context MCP limitation).
- **Canonical channel = webhook-first.** The skill sends via a Bash `curl` to a
  webhook URL (token in an env var). Alternatives: an MCP "send message" tool
  (`channel.type: "mcp"`), or the built-in `preferredNotifChannel` terminal/OS bell
  (`channel.type: "local"`, zero-setup but generic and local-only).
- **Hooks are not used for the message body.** A Claude Code `Notification` hook
  only carries the harness's generic text — it **cannot** carry our composed,
  classified message. So the skill sends directly; hooks are not the mechanism.
- **Default push set:** `error_diagnosed` + `error_opaque` + `needs_decision`
  (attention) **and** `completed` (completion). `auto_fixed` stays inline-only.
  The user can narrow this; `notifications.events` records the choice (§2.6).

### 20.3 Setup & consent (Setup step S5, §5)

1. **Ask intent:** want a ping when a task finishes or needs attention? `no` is
   stored (`enabled: false`) so Setup **never re-asks**. Inline reporting still
   applies regardless.
2. **Existing vs new channel:** if the user already has a channel (a webhook, an
   MCP "send" server, a CLI), the skill records a **reference** to it (consented
   for this workspace). If not, the skill **scaffolds + guides**: it writes the
   config/env scaffold and walks the user through the **human-only** steps — pasting
   a webhook URL, OAuth approval, or a session reconnect. **The agent cannot
   complete auth itself** (no OAuth, no SMTP creds); it is honest about this. For
   an **env-var-backed** channel, the secret must be exported where
   **non-interactive** shells read it — for zsh that is `~/.zshenv`, **not**
   `~/.zshrc` (which only interactive shells source) — because the skill sends from
   a non-interactive Bash/zsh shell; a value only in `~/.zshrc` is invisible to the
   sender.
3. **Store in the workspace stamp** (§2.6 `notifications` block): `enabled`,
   `events`, and a `channel` **reference** — `type` + an env-var name / tool ref.
   **Secrets are never written to the stamp**; they live in env or the MCP server
   config. (The stamp is gitignored, but the principle holds regardless.)
