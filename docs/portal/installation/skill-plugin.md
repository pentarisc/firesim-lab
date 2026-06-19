# The Claude Code Skill Plugin

firesim-lab ships an optional **[Claude Code](https://claude.com/claude-code) plugin** that drives the whole flow conversationally. This page covers installing and updating it. For what it does and how it behaves, see {doc}`/skill/index`; for a hands-on run, see {doc}`/skill/walkthrough`.

The plugin and the host toolchain (the `firesim-lab` launcher + the Docker image) are **independent installs**. You can install either first — but the plugin's setup skill can detect and even bootstrap the toolchain for you, so installing the plugin is a fine place to start on a fresh host.

## Prerequisites

- **Claude Code** running on your host (the VS Code extension, desktop app, or CLI). The plugin lives in `~/.claude` on the host and drives the container from there.
- **The firesim-lab toolchain** — Docker, the `firesim-lab` launcher, and the image (see {doc}`index`). You do *not* need to install this first: the `firesim-lab-setup` skill detects what's missing and, with per-step confirmation, can run `install.sh` and pull the image for you.

## Install

From any project, add this repository as a plugin marketplace and install the plugin:

```text
/plugin marketplace add pentarisc/firesim-lab
/plugin install firesim-lab@pentarisc
```

Installing the one plugin makes all three skills available: `firesim-lab-help`, `firesim-lab-setup`, and `firesim-lab-sim`. Start by invoking **`firesim-lab-help`** for the overview, or go straight to **`firesim-lab-setup`** on a fresh host.

:::{note}
The install identifier is `firesim-lab@pentarisc` — the plugin named `firesim-lab` from the `pentarisc` marketplace. If you also use the manual CLI, nothing here changes it; the skill is purely additive.
:::

## Updating

The plugin is released from the **same repository at the same git tags as the tool**, so a given plugin version matches the tool at that version. Update through Claude Code:

```text
/plugin marketplace update pentarisc
/plugin update firesim-lab@pentarisc
```

### Keep the plugin and tool in step

The skill binds to the **installed** `fslab` — it reads the active version at preflight and never assumes `latest`. It is compatible with any installed tool of the same **MAJOR.MINOR** (patch differences are always fine). If they drift across a MINOR — for example you upgraded the tool but not the plugin — the skill **halts** with the standard `firesim-lab --upgrade` guidance rather than driving a tool it doesn't understand.

So when you upgrade one, upgrade the other to the matching MAJOR.MINOR:

- Tool: re-run `install.sh` for the new version and `firesim-lab --upgrade` in each workspace (see {doc}`versioning`).
- Plugin: `/plugin update` as above.

## Relationship to `install.sh`

`install.sh` installs the host toolchain only; it does **not** install the skill, and the skill does not ship through it. They are deliberately decoupled so each can be updated independently. The one bridge between them is the `firesim-lab-setup` skill, which can *invoke* `install.sh` (with your confirmation) to bootstrap a host — making the plugin a complete AI-native entry point.

## What's next

- {doc}`/skill/index` — what the plugin does, the metasim gate, guardrails, and version binding.
- {doc}`/skill/walkthrough` — the AXIUARTPrinter example, end to end, via the skill.
- {doc}`/quickstart/index` — the same flow done by hand with the `fslab` CLI.
