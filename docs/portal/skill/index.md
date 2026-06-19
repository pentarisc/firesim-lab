# AI-Accelerated Flow (Claude Code Skill)

firesim-lab ships an optional **[Claude Code](https://claude.com/claude-code) plugin** that drives the whole flow conversationally — from a blank host to a passing metasimulation, and on to an AWS F2 FPGA run. You write only Verilog/SystemVerilog; the skill scaffolds the project, fills in `fslab.yaml`, runs the build with a compile-fix loop, evaluates the result against a success criterion you define, and (only after that passes) orchestrates the F2 build and run — including AWS SSO, optional notifications when a long task finishes or needs you, and automatic, cost-safe cloud cleanup.

It is a thin orchestration layer over the **installed** `fslab` and Docker image — it never assumes a version, and it never does anything that costs money without explicit confirmation.

:::{note}
The skill is **optional**. Everything it does, you can do by hand with the `fslab` CLI — see {doc}`/quickstart/index`. The skill is for users who would rather drive the flow through Claude Code than type the commands themselves.
:::

## Install

See {doc}`/installation/skill-plugin` for the two-line install and update mechanics. In short:

```text
/plugin marketplace add pentarisc/firesim-lab
/plugin install firesim-lab@pentarisc
```

Then jump to the {doc}`walkthrough` to take the AXIUARTPrinter example end to end.

## What's in the plugin

The plugin is **three cooperating skills** plus three background **sub-agents**, split by how often each part runs so every invocation stays small and you can run any part directly.

| Skill | Runs | What it does |
|---|---|---|
| `firesim-lab-help` | on demand | Shows the flow overview and points you at the other two. Read-only. |
| `firesim-lab-setup` | once per host/account | Host prerequisites (container runtime, the `firesim-lab` launcher, the image), workspace init, version pinning, and — only if you want F2 — opt-in AWS provisioning + first-time SSO, and opt-in notifications. |
| `firesim-lab-sim` | every iteration | The recurring end-to-end flow: scaffold → configure → build → metasim → **gate** → F2 build/run. Self-orchestrates from saved state. |

The sub-agents (`build-runner`, `build-monitor`, `run-monitor`) do the long or verbose autonomous work — running the build, and watching detached F2 build/run jobs — in isolated context, so the hundreds of lines of build output never clutter the conversation. All questions and confirmations stay in the interactive skill.

## The flow, and the metasim gate

```text
Setup (once) ─▶ Metasim ─▶ ┃ GATE ┃ ─▶ AWS preflight + SSO ─▶ F2 build ─▶ F2 run
                            ┗━━━━━━┛
              prove the design in software first
```

The defining rule: **the F2 path (a real FPGA — it costs money and takes time) is only offered after the design passes the metasim gate** — a fast desktop simulation that produces the output *you* defined as success. A design that builds and prints correctly in software is the precondition for spending FPGA build time.

The gate is robust across sessions: it is tied to the same configuration hash `fslab` already tracks, so editing your RTL or `fslab.yaml` re-opens it automatically — the skill never trusts stale evidence.

## How it interacts with you — three input tiers

Every input the flow needs falls into exactly one tier, so you always know whether the skill will ask, propose, or just check:

- **Ask (never guess):** your top module + RTL path(s); which bridges; port-map overrides when bridge and design names don't line up; `mem_base`; the metasim **success criterion**; and (for F2) AWS profile/region, SSO mode, host models, and any spend confirmation.
- **Infer + show (you can veto):** which ports are clock/reset/enable; `ref:`-style parameter mappings; the `+max-cycles` budget from payload size; width-lint fix proposals.
- **Verify silently:** container/shell context, the `payloads/` path, that the build/elaboration actually succeeded.

Questions are **staged**, not one giant form — clock/reset and port maps, for instance, are only asked *after* `fslab init` parses your real ports. Every question carries plain-language help, and typing `help` at any point re-shows the overview and explains the current step.

## Guardrails

- **Your RTL is read-only**, with one narrow exception: trivial Verilator width-lint fixes (mechanical sized-literal changes), and only with the diff shown. Logic, elaboration, and semantic errors are **reported, never silently fixed** — the skill surfaces the diagnostics and waits for you.
- **No silent spending.** EC2 launch and AFI creation are hard confirm-gated; the skill shows the action, instance type, and region first.
- **No silent cloud resource creation.** The skill verifies first (read-only probes) and, for solo admins only, *offers* to run the documented setup scripts one confirmed step at a time. Org developers are directed to their admin.
- **Automatic, evidence-preserving cleanup.** On completion the build EC2 is terminated and the F2 host stopped — but only *after* logs/artifacts are pulled back, so even a failed build leaves its diagnostics behind.

## AWS, SSO, and notifications

- **AWS work is split by frequency.** One-time provisioning (account/IAM/roles/quota + first-time `aws configure sso`) lives in `firesim-lab-setup`; the recurring login + a verify-only readiness probe live in `firesim-lab-sim`. Metasim needs no AWS at all, and the slow F2 service-quota approval is requested early so it can clear while you iterate in software.
- **SSO is device-code** (the container is headless): the skill surfaces the verification URL and user code immediately, then polls to completion. Readiness probes degrade gracefully — a permission a normal developer lacks is reported as "unknown," never a false failure.
- **Notifications are opt-in.** If enabled, the skill can ping you (webhook, an MCP "send" tool, or the local terminal bell) when a task finishes or needs your attention. Inline reporting in the conversation is always on regardless; turning push on only changes the reach, not the message.

## Version binding

The installed tool is the single source of truth for the version. The skills detect it at preflight (`fslab --version`, cross-checked with the workspace pin) and bind everything to it — including documentation links — and never reference `latest`. Each skill is compatible with any installed tool of the same MAJOR.MINOR; on a mismatch it halts with the standard `firesim-lab --upgrade` guidance rather than driving a tool it doesn't understand. The plugin is released from this repository at the same tags as the tool, so the two always match. See {doc}`/installation/versioning`.

```{toctree}
:maxdepth: 1
:hidden:

walkthrough
```
