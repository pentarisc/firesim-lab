# firesim-lab AI-Accelerated Flow — Skill Requirements / Operating Model

**Date written:** 2026-06-08
**Project:** firesim-lab
**Status:** Requirements **defined**. No skill built yet. This document is the
agreed operating model and the **complete, self-contained** spec to build the
skill from — it folds in the proven metasim flow (§17), the command sequence
(§18), the engineering gotchas (§13), and the repo reference index (§19), so no
other document is needed.

---

## 1. Purpose & scope

A Claude Code **skill** that drives a **fully AI-accelerated firesim-lab flow**,
end to end, for **end users who write only Verilog/SystemVerilog** and never
touch Chisel/Scala:

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

## 2. Vehicle decision (why a skill, packaged as a plugin)

A Claude Code **Skill** is the vehicle, **packaged as a plugin and distributed
via a marketplace**. Alternatives considered and rejected:

| Option | Verdict | Reason |
|---|---|---|
| **Skill (plugin / marketplace)** | **Chosen** | Workflow orchestration with progressive disclosure, runs in the user's main conversation, bundles companion reference/scripts, native install + `/plugin update`. |
| Slash command | Rejected | A bare prompt template — no progressive disclosure, no bundled reference/scripts. Skills are the strict superset. |
| Subagent as the top-level surface | Rejected as top-level | Fresh isolated context is wrong for an interactive, stateful flow. **Used internally** for background build/run monitoring (§9). |
| MCP server / Agent-SDK app | Rejected | Heavyweight; wrong audience — users already run Claude Code. |
| **Project skill** (`<repo>/.claude/skills/`) | Rejected | Only activates when the firesim-lab repo *is* the workspace. End users work in their **own out-of-tree** project folders under `/target`, so they would never see it. |
| install.sh copies skill → `~/.claude/skills/` | Deferred | Viable personal-skill path, but marketplace gives a better first-touch and native updates. Kept as a possible future fallback; not specified now. |

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

---

## 4. Delivery model

- **Source location:** skill authored under a neutral **`skills/`** folder at the
  **repo root** (not `.claude/skills/`), separating *authoring location* from
  *deployment location*. Layout kept **plugin-compatible**
  (`.claude-plugin/plugin.json`, marketplace manifest).
- **Distribution:** **marketplace / plugin** is the front door. README documents
  the two-line install:
  ```
  /plugin marketplace add pentarisc/firesim-lab
  /plugin install firesim-lab-sim
  ```
- **Relationship to `install.sh`:** the host toolchain (the `firesim-lab`
  launcher at `~/.local/bin/firesim-lab` + the Docker image) and the **skill** are
  **independent installs**. `install.sh` remains **as-is** as the toolchain
  installer; the skill does *not* ship through it. The skill's **Stage 0** detects
  the toolchain and, with per-step permission, can **run `install.sh` / pull the
  image / configure** the host itself (§5, §6) — so the skill is the single
  AI-native entry point and can bootstrap a fresh host.
- **Maintainer/dev note:** because the skill can install/configure directly (with
  permission), maintainers can validate the onboarding path without `install.sh`.
  For testing the skill *inside this repo*, optionally symlink `skills/<name>` into
  the repo's `.claude/skills/` (maintainer-only nicety, not part of user delivery).

---

## 5. State machine (gated, idempotent, resumable)

Every node is a **checkpoint**. Because every `fslab` step is hash-aware and
idempotent, the skill can re-enter at any node. The ordering is the intended
happy path, not a rigid lockstep.

```
0. Onboarding / preflight
   0a. First-run help: check the per-user ~/.firesim-lab onboarding marker;
       if absent, ask "new to firesim-lab? want a 60-second tour?" → show the
       overview (§16) → offer "don't show again" (writes the marker).
       Inline per-question help stays on regardless. (See §16.)
   0b. Host prereqs: Docker running? firesim-lab launcher? image pulled?
       └─ mode: DETECT + OFFER TO RUN (per-step confirmation) — may run
          install.sh / pull image with the user's permission
   0c. Workspace init: is .firesim-lab.env present in the current folder?
       └─ if absent, run the firesim-lab launcher to initialize it
   0d. Container running? discover it; establish firesim-lab-shell path
   0e. AWS intent (lightweight): plan to use F2? AWS already set up?
       solo-developer (own admin) or org-developer (admin provisions)?
       └─ informational only; nudge: request the slow F2 quota NOW if F2 is
          intended (approval can take a day or two). No heavy AWS work here.
1. Inputs: RTL path(s) + top module     [ASK / propose from open VSCode file]
2. Project: ask name → fslab new → docker cp RTL + payload into /target/<proj>
3. Bridges: ask which → check DUT ports vs registry required ports
   └─ missing required ports = HARD STOP (report; needs user RTL change)
4. Configure fslab.yaml: clk/reset/enable, port_map, ref: params, mem_base
                                          [INFER + SHOW; user vetoes]
5. fslab generate → fslab build metasim
   ├─ logic/semantic/elaboration error = REPORT, do not fix, wait, rebuild
   └─ Verilator -Wall width-lint        = skill MAY apply minimal sized-literal
                                           fix, SHOW the diff; never logic
6. Success criterion: ask what counts as pass → fslab sim → evaluate
   ════════════════ HARD GATE: metasim must pass ════════════════
7. AWS preflight & guided setup (Phase 2 entry; re-checks even if deferred at 0d)
   ├─ verify readiness via read-only probes (roles, key pair, PassRole, quota,
   │  SSO session) → report each gap
   ├─ console/quota/account gaps = EXPLAIN + LINK + VERIFY (cannot script)
   ├─ admin-CLI gaps (roles, key pair, PassRole) = OFFER TO RUN bundled scripts,
   │  per-step confirm — ONLY for solo-developer with an admin profile;
   │  org-developer path = direct to their admin, verify-only
   └─ developer login = aws configure sso (first run) + device-code login (§9.4)
8. F2 questionnaire: AWS profile/region, SSO mode, build/run host models,
   fpga_slot, publish mode, spend ack  → patch fslab.yaml target.*
9. fslab build fpga   (EC2 launch / AFI create = HARD SPEND CONFIRM)
   └─ background sub-agent: fslab monitor build → on image ready:
      pull logs/artifacts → TERMINATE build EC2
10. Patch fslab.yaml with AGFI/image → fslab sim fpga (detached) →
    sub-agent monitors → on completion: pull output → STOP F2 host → report
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

The user interacts through **staged questionnaires**, because some answers only
become meaningful after a prior step runs:

- **Stage-0 questionnaire** — prereq remediation consent (per step) + workspace
  init + **AWS intent** (plan to use F2? already set up? **solo-developer vs
  org-developer**). The AWS answers are recorded and drive the Phase 2 preflight
  (§9); they impose no AWS work during Phase 1.
- **Project/RTL questionnaire** — RTL path(s), top module (propose from the
  open VSCode file when possible), project name.
- **Bridge questionnaire** — which bridges; presented before the port check.
- **Post-`init` configuration** — clk/reset/enable + `port_map` are presented
  **after** `fslab init` parses the real ports, **pre-filled with the skill's
  INFER proposals** for the user to veto. (This is the "edit post-init" strategy:
  the skill patches `fslab.yaml`; it does not author it from scratch.)
- **Success-criterion question** — what constitutes a passing metasim (the gate
  contract; see §7).
- **F2 questionnaire** — appears **only after the gate passes**: AWS profile/
  region, SSO mode, build/run host models, publish mode, `fpga_slot`, spend
  acknowledgement.

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
     re-runs `fslab build`.
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

## 9. AWS readiness, guided setup & SSO (Phase 2 preflight)

AWS is needed **only** for Phase 2; the AWS setup docs themselves say everything
here can be deferred until the design works in metasim. The skill therefore does
**no AWS work in Phase 1** beyond the Stage-0 intent question (§5 0e). All real
AWS verification and setup happens in the **Phase 2 preflight** (state-machine
step 7), which runs even when the user deferred at Stage 0. Source of truth:
[docs/portal/setup/aws/](../portal/setup/aws/) —
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

### 9.2 Solo-developer vs org-developer (from the Stage-0 answer)

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

Active SSO session (`get-caller-identity`); build role + instance profile +
policy (`fslab-fpga-builder`); run role (`fslab-fpga-runner`); `iam:PassRole`
grant present; SSH key pair exists in the chosen region; **F2 quota > 0**
(else no instance launches); region is F2-capable and an FPGA Developer **AMI**
id is available for it. Each gap is reported with its layer and remediation.

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

First-run-with-no-profile falls back to guiding `aws configure sso`. Credentials
persist via the `~/.aws` bind mount; there is no AWS CLI on the host.

---

## 10. Background sub-agents

Long F2 build/run phases survive laptop sleep via **detached + monitor**. The
skill spawns **background sub-agents** that:

- **Build monitor:** poll `fslab monitor build`; on image-ready, pull
  logs/artifacts, then **terminate the build EC2** (§8.4).
- **Run monitor:** after `fslab sim fpga --detach`, poll `fslab monitor run`; on
  completion pull the output, **stop the F2 host**, and report back to the user.

Sub-agents are the appropriate use of the subagent primitive here (isolated,
long-lived polling), distinct from the top-level interactive skill.

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

## 12. Skill file layout (progressive disclosure)

```
skills/firesim-lab-sim/
  .claude-plugin/plugin.json   # plugin manifest (for marketplace packaging)
  SKILL.md                     # trigger + state machine + tier-1 interaction rules
  reference/
    help.md                    # §16: first-run overview copy + on-demand help text
    onboarding.md              # Stage 0: prereq detection, install.sh guidance, .firesim-lab.env
    metasim.md                 # Parts B/C distilled: port_map, ref:, mem_base, lint, max-cycles
    fpga-f2.md                 # F2 build/run pipelines, target.build/target.run, cleanup
    aws-setup-sso.md           # §9 layered model: verify probes, solo/org paths, device-code SSO
  scripts/                     # deterministic helpers, run only with confirmation
    detect-context.sh          #   in-container vs host-driving-container; container discovery
    verify-aws.sh              #   §9.3 read-only readiness probes (roles/keypair/PassRole/quota)
    aws-create-build-role.sh   #   firesim-lab-aws-setup Step 4 (solo-admin only)
    aws-create-run-role.sh     #   firesim-lab-aws-setup Step 5 (solo-admin only)
    aws-create-keypair.sh      #   firesim-lab-aws-setup Step 3 (solo-admin only)
    aws-grant-passrole.sh      #   identity-center-sso PassRole grant (solo-admin only)
    scrape-sso-code.sh         #   §9.4 extract verification URL + code from backgrounded login
```

`SKILL.md` stays lean; `reference/` files load only when the relevant
phase/stage is entered.

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
   the docker layer — detect this first. **→ §3; §5 node 0d.**

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
   (`templates/bridges/fased/wiring.scala.j2`). **→ §8.5.**

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
   `resolve_refs`); the ref dict is `{ref: NAME}`. **→ §6.1 INFER+SHOW.**

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

---

## 14. Open items deferred to build time

1. **SSO completion/expiry detection robustness** — reliably distinguishing
   success vs expiry vs denial when polling a backgrounded login; exact poll
   command and timeout values (§9).
2. **Prereq detection specifics** — the exact probes for "Docker running",
   "launcher installed", "image pulled" on the host (§5 node 0b).
3. **Port-check semantics** — how strictly to match DUT ports against registry
   `input_ports`/`output_ports`, and how to present a partial mismatch (§5 node 3).
4. **Plugin manifest + marketplace plumbing** — `plugin.json` fields, marketplace
   manifest location (this repo vs a dedicated marketplace repo), and the
   versioning relationship to the existing `install.sh`/manifest machinery (§4).
5. **Where the skill persists per-project decisions** — rely on `fslab.yaml`
   alone, or keep a side stamp.
6. **Solo-vs-org capability detection** — whether to trust the Stage-0 answer
   alone or also probe IAM-write capability (e.g. `iam:CreateRole` via a dry-run
   / `simulate-principal-policy`) before offering the admin-CLI scripts (§9.2).
7. **AWS verification probe specifics** — exact CLI for the F2 **quota** check
   ("Running On-Demand F instances" > 0) and the per-region FPGA Developer **AMI**
   lookup, plus how to surface a *pending* (not-yet-approved) quota request (§9.3).

---

## 15. Decisions ledger (this conversation)

| # | Decision | Rationale |
|---|---|---|
| 1 | Vehicle = Skill, packaged as plugin, marketplace-distributed | Progressive disclosure, bundling, native updates; project skill rejected (out-of-tree audience) |
| 2 | Audience = end users; skill host = host driving the container | Users work in their own folders; Claude Code runs in host VSCode |
| 3 | Delivery = marketplace front door; `install.sh` unchanged, driven by Stage 0 | Single AI-native entry point; toolchain install stays decoupled |
| 4 | Stage 0 = detect + offer to run (per-step consent) | Skill can bootstrap a fresh host without being reckless |
| 5 | Scope = full two-phase, F2 hard-gated behind metasim | Don't spend FPGA time/money on an unproven design |
| 6 | `fslab.yaml` = edit post-`init` (patch, not author) | Matches the validated flow + the `init` contract |
| 7 | Questionnaires = staged (Option A) | Port-map/clk-reset only meaningful after `init` parses ports |
| 8 | Gate criterion = user-defined via questionnaire | Different DUTs define "success" differently |
| 9 | RTL = read-only except narrow width-lint sized-literal fixes (diff-shown) | Never alter user logic; mechanical lint fixes are safe and shown |
| 10 | AWS = hard spend-confirm; verify-first; never *silently* create IAM, but may run setup scripts per-step-confirmed (solo-admin); console/quota = explain-only | End-user cost/safety; admin-CLI layer is scriptable per the setup docs |
| 10a | AWS gated behind Phase 2: Stage-0 intent question only; real verify+setup in Phase 2 preflight | Metasim needs no AWS; quota approval is slow so nudge early |
| 11 | Cloud cleanup = fully automatic, evidence-preserving | Cost safety without destroying diagnostics |
| 12 | Long F2 build/run = background sub-agents (detached + monitor) | Survive sleep; isolated polling |
| 13 | Help = 3 tiers (suppressible first-run tour + always-on inline per-question help + on-demand keyword); tour flag per-user (~/.firesim-lab) | Newcomers find the questions confusing; tour is about the *person*, not the project |

---

## 16. User help & onboarding

Skills have **no native help UI** — "help" is skill-authored behavior. Because the
skill is a conversation (not a rigid wizard), it provides **three tiers**, all
specified as requirements:

1. **First-run overview (suppressible).** On entry (§5 0a) the skill checks a
   **per-user** marker (`~/.firesim-lab/skill-onboarded` or equivalent). If
   absent, it asks *"New to firesim-lab? Want a 60-second tour?"*; on yes, shows
   the overview below; then offers **"don't show this again"**, which writes the
   marker. Per-user (not per-workspace) because it reflects the *person's*
   familiarity across all their projects. There is no native "remember this
   choice" control — the marker file **is** the mechanism.
2. **Inline per-question help (always on).** Every staged question carries
   plain-language `description` text per option plus a one-line *why we're asking*
   — so a newcomer is never stranded on a confusing question **even if they
   skipped or suppressed the tour**. This tier is never suppressed.
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

This copy lives in `reference/help.md` (loaded for the tour and on-demand
re-show), keeping `SKILL.md` lean.

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
