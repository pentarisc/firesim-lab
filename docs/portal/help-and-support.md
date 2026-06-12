# Help & Support

Stuck on something, unsure whether a behaviour is a bug, or want to share what
you are building? firesim-lab has two support channels, both on GitHub:

- **[GitHub Discussions](https://github.com/pentarisc/firesim-lab/discussions)** —
  the place for questions, usage help, ideas and feature proposals, and showing
  off what you have built. If you are not sure whether something is a bug, start
  here; it can always be promoted to an issue.
- **[GitHub Issues](https://github.com/pentarisc/firesim-lab/issues)** — for
  reproducible bugs and concrete defects in the framework, the CLI, the bridges,
  or the documentation.

## Before you ask

A quick pass through the existing material often answers the question directly:

- {doc}`/troubleshooting/index` — known failure modes and their fixes.
- {doc}`/commands/index` — the full `fslab` command reference; every command
  also has its own `--help`.
- {doc}`/concepts/index` — the mental model behind metasim, bridges, and the
  target/host split.
- Search [existing discussions](https://github.com/pentarisc/firesim-lab/discussions)
  and [issues](https://github.com/pentarisc/firesim-lab/issues) — someone may
  have hit the same thing.

## Asking a good question

The easier your situation is to reproduce, the faster it gets answered. When
you post, include:

- the firesim-lab version you are running (the pinned image tag in your
  workspace's `.firesim-lab.env`),
- your host platform (Linux distro, macOS, or Windows/WSL2),
- the exact `fslab` command you ran and the relevant output or log excerpt,
- the relevant portion of your `fslab.yaml` (the bridge and port mappings
  usually matter most).

:::{note}
firesim-lab is an independent project — it is not affiliated with, endorsed
by, or supported by the FireSim or Chipyard projects. Please bring
firesim-lab questions and bug reports here, not to the upstream projects'
channels.
:::
