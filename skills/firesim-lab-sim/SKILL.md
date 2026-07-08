---
name: firesim-lab-sim
description: The recurring end-to-end firesim-lab flow — use every time the user wants to simulate a Verilog/SystemVerilog design with firesim-lab, or to take a passing design to AWS F2. Self-orchestrates from the saved state stamp: scaffold a project, place RTL + payload, configure fslab.yaml (clk/reset/enable, bridges, port_map, ref: params, mem_base), build (compile-fix loop via the build-runner sub-agent), run the metasim, and evaluate a user-defined success criterion. F2 (build/run on a real FPGA, costs money) is HARD-GATED behind a passing metasim. Delegates verbose/long autonomous work to sub-agents; all interaction stays here.
metadata:
  fslab_version: "0.9.0rc1"
  skill_version: "0.9.0rc1"
---

# firesim-lab-sim — the recurring flow (self-orchestrating)

You run the iteration loop end to end. You **self-orchestrate from the stamp**:
on entry, read state, then resume or jump to the right step. All user interaction
(questions, vetoes, spend confirmation, the SSO code) stays here; verbose/long
autonomous work goes to sub-agents (§ below). Be **legible** — explain what you
are doing; never silently guess on anything affecting correctness or cost.

## 0. Preflight (always first)

1. **Context + seam:** run from the **workspace root** (or export `FSLAB_ENV_FILE`
   to the absolute `.firesim-lab.env` path) so runtime/version resolution reads the
   right env file, then
   `source "${CLAUDE_PLUGIN_ROOT}/skills/firesim-lab-setup/scripts/detect-context.sh" && fslab_detect_context`.
   Use `fslab_exec` / `fslab_in_dir` only — never inline a container command.
2. **Version detect + bind (§2.5):** read `fslab --version`; cross-check
   `FIRESIM_LAB_VERSION`. Bind every doc link to `…/en/v<active>/…`. This skill is
   `fslab_version 0.9.0rc1` — on a MAJOR.MINOR mismatch, **halt** with the
   `firesim-lab --upgrade` guidance.
3. **Read both stamps:**
   - workspace `<workspace>/.firesim-lab.skill-state.json` (written by Setup).
     If it shows setup incomplete → tell the user to run **`firesim-lab-setup`**
     first and stop.
   - project `<project>/.fslab/skill-state.json` (you own this).
4. **Resume/jump:** if the project stamp shows `metasim.passed` *and*
   `metasim.config_hash` equals the current `config_hash` in `.fslab/state.json`,
   the **gate is open** — you may go straight to F2 (step 7). Otherwise (or if RTL
   / `fslab.yaml` changed, which changes the hash) start/continue in metasim.

The three input tiers govern every question: **ASK** (never guess), **INFER +
SHOW** (propose, user vetoes), **VERIFY silently**. Inline per-question help is
always on; `help` re-shows the overview ([../firesim-lab-help/reference/overview.md]).

---

## METASIM (steps 1–6) — load [reference/metasim.md](reference/metasim.md)

1. **Inputs [ASK]:** RTL path(s) + top module (propose from the open VSCode file
   when possible).
2. **Project:** ask name → `fslab new <proj>` → copy RTL into
   `/target/<proj>/user_rtl/` and payload into `/target/<proj>/payloads/` (plural).
3. **Bridges [ASK]:** which bridges → `fslab init -t <Top> -f <file>` →
   check DUT ports vs `lib/registry.yaml` required ports. **Missing required port =
   HARD STOP** (`needs_decision` report; needs a user RTL change).
4. **Configure `fslab.yaml` [INFER + SHOW]:** patch (don't author) — the
   **mandatory top-level `host:` block** (`emulator` + `driver_name`) and the
   generated driver in `host.sources`; clk/reset/enable designation; `port_map`;
   `ref:` params; `mem_base`. The gotchas in reference/metasim.md (§13) are
   load-bearing — read them.
5. **Generate + build:** `fslab generate`, then run the build through the
   **`build-runner`** sub-agent so the verbose log stays out of this context. Apply
   its verdict (compile-fix loop):
   - **logic / semantic / elaboration error** → REPORT (`error_diagnosed` /
     `error_opaque`), **do not fix user logic**, wait for the user, rebuild.
   - **Verilator `-Wall` width-lint** → the sub-agent may apply a **minimal
     sized-literal fix** and **show the diff** (`auto_fixed`); never logic.
6. **Success criterion [ASK]:** what counts as pass (expected-output match /
   clean-exit+non-empty UART / manual confirm). `fslab sim` → evaluate (UART is on
   stdout, interleaved with the banner; size `+max-cycles` to expected bytes).
   On pass → write the **project stamp** `metasim` block with evidence and the
   `config_hash` copied from `.fslab/state.json`. Send a `completed` report.

```
═══════════ HARD GATE: F2 is offered only when the stamp shows metasim passed
            AND metasim.config_hash == current .fslab/state.json config_hash ═══════════
```

---

## F2 (steps 7–11) — load [reference/fpga.md](reference/fpga.md) + [reference/aws-login.md](reference/aws-login.md)

Only after the gate is open.

7. **AWS preflight (VERIFY-ONLY):** `scripts/verify-aws.sh <profile> <region>`
   (via the shared seam). Any gap → point back to **`firesim-lab-setup`**; do NOT
   provision here.
8. **Recurring SSO login (device-code):** `scripts/scrape-sso-code.sh <profile>` —
   surface the verification URL + user code immediately (`needs_decision`), poll to
   completion with a timeout fallback. Modes: `skill-driven` (default) /
   `user-paste` / `already-logged-in`.
9. **F2 questionnaire [ASK]:** profile/region, SSO mode, build/run host models,
   `fpga_slot`, publish mode, **spend acknowledgement** → patch `fslab.yaml`
   `target.*`.
10. **Build:** `fslab build fpga` — EC2 launch / AFI create is a **HARD SPEND
    CONFIRM** (`needs_decision`). Then launch the **`build-monitor`** sub-agent in
    the background; on image-ready it pulls artifacts and **terminates the build
    EC2**, returning a report. When the harness re-invokes you on completion, **you**
    send the notification.
11. **Run:** patch `fslab.yaml` with the AGFI/image → `fslab sim fpga --detach` →
    launch the **`run-monitor`** sub-agent; on completion it pulls output and
    **stops the F2 host**, returning a report. You send the notification.
    → update the project stamp `f2` pointers (`last_build_id`, `last_run_id`).

---

## Sub-agents (delegate; never interactive)

- **`build-runner`** (step 5): runs the verbose build, returns one distilled
  verdict (`completed`/`auto_fixed`/`error_diagnosed`/`error_opaque`). Schema/config
  validation errors must be attributed to the **actual** field path — a missing
  top-level `host` is NOT `target.build.host`.
- **`build-monitor`** (step 10, background): poll → pull → terminate build EC2.
- **`run-monitor`** (step 11, background): poll → pull → stop F2 host.

**Sub-agents never notify.** They return reports; **you** (the foreground skill)
send the notification when re-invoked on their completion.

## Guardrails (always)

User RTL is read-only except the narrow width-lint sized-literal fix (diff-shown).
Never auto-spend on AWS. Never silently create AWS/IAM resources. Cloud cleanup is
automatic but evidence-preserving (pull logs before teardown). Validate `mem_base`
against the addresses the DUT drives (see reference/metasim.md).

## Reporting & notifications (§20)

Every notify-worthy event is one **report object** (`auto_fixed` / `error_diagnosed`
/ `error_opaque` / `needs_decision` / `completed`). Inline rendering is **always
on**; push is optional and only sent by you, per the workspace stamp
`notifications` block (default push: attention + completion; `auto_fixed` is
inline-only). Diagnosable → summary + fix; opaque → summary + log excerpt, **never
a fabricated fix**. See reference/fpga.md for channel send mechanics.

## The project stamp (you own it)

Write `<project>/.fslab/skill-state.json` (atomic `*.tmp`→rename). The `.fslab/`
dir is already gitignored. Live F2 state (AGFI, build/run status) is **read** from
`build/fpga/.fslab/build.yaml` / `run/fpga/.fslab/run.yaml` — the stamp only keeps
pointers, never a second copy. The CLI never reads or writes this file. Full schema
in [reference/metasim.md](reference/metasim.md).
