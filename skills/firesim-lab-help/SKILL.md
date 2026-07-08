---
name: firesim-lab-help
description: Explains the AI-accelerated firesim-lab flow and points to the other two skills. Use when the user asks "what is firesim-lab", "how does the firesim-lab skill work", "where do I start", or invokes /firesim-lab-help. Pull-only and read-only — it never changes anything; it shows the flow overview and a map of firesim-lab-setup and firesim-lab-sim.
metadata:
  fslab_version: "0.9.0rc1"
  skill_version: "0.9.0rc1"
---

# firesim-lab-help — the flow overview & map

You are the **pull-only Help skill** for the firesim-lab plugin. You are invoked
deliberately by the user. You **change nothing** and write no state — you orient
the user and hand off to the other two skills.

## What to do

1. Show the **first-run overview** verbatim from
   [reference/overview.md](reference/overview.md) (load it now).
2. Name the other two skills and when each runs:
   - **`firesim-lab-setup`** — run **once per host/account**. Checks host
     prereqs (container runtime, the `firesim-lab` launcher, the image), inits the
     workspace, and (only if the user wants AWS F2) provisions AWS + first-time
     SSO, and optionally sets up notifications.
   - **`firesim-lab-sim`** — run **every iteration**. The recurring end-to-end
     flow: scaffold a project, place RTL + payload, configure `fslab.yaml`, build,
     run the metasim, and (after the metasim gate passes) build/run on AWS F2.
3. Recommend the entry point based on what the user says:
   - Fresh host / never run setup → **start with `firesim-lab-setup`**.
   - Already set up, wants to simulate a design → **go straight to `firesim-lab-sim`**
     (it self-orchestrates from the saved state stamp and will tell them to run
     Setup first if the stamp shows it is missing).
4. Mention that `help` works as a keyword inside the other skills at any stage —
   they re-show this overview and explain the current question.

## Version note

This plugin binds to the **installed** fslab version — never "latest". The other
skills detect it at preflight (`fslab --version` / `FIRESIM_LAB_VERSION`) and use
it for everything, including version-pinned doc links. If you mention docs, say
the skills link the version-matched pages; do not hardcode a version here.

Keep it short. You are a signpost, not the flow.
