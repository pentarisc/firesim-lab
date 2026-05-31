# Contributing

This page is for people contributing to **firesim-lab itself** — fixing a bug,
adding a bridge, extending the CLI, or changing the toolchain image. It covers
the *process*: how to set up a development environment, where each kind of change
lives, and the conventions for branches, commits, pull requests, code, and docs.

For the *mechanics* of each change — the files you write and the code to write in
them — the {doc}`/developer/index` section is the reference. This page tells you
how to work; the developer section tells you what to build.

```{note}
"No-Chisel" applies to firesim-lab's *users*, not its *contributors*. A user
wires a Verilog/SystemVerilog blackbox to existing bridges through `fslab.yaml`
and never sees Scala. Working on the framework involves Python, Jinja2, YAML,
C++, and Chisel/Scala in varying combinations depending on what you touch.
```

## Where your change lives

The framework has a few distinct surfaces. Find the row that matches your change,
then follow the link for the in-depth recipe. The {doc}`/developer/index` "Find
your task" table is the fuller version of this map.

| You want to… | Primary surface | Languages | Start at |
|---|---|---|---|
| Add or change a **bridge** | `lib/bridges/`, `lib/registry.yaml`, `templates/bridges/` | Chisel/Scala, C++, Jinja2, YAML | {doc}`/developer/bridges/index` |
| Add a **subcommand** or build/run flow to the **CLI** | `fslab-cli/fslab/` | Python | {doc}`/developer/fslab-python/extending` |
| Change a **generated file** (the shim, CMake, driver) | `fslab-cli/fslab/templates/` | Jinja2 | {doc}`/developer/jinja-templates` |
| Support a new **platform / simulator** | `lib/registry.yaml` | YAML | {doc}`/developer/fslab-python/schemas` |
| Change the **toolchain image** | `docker/` | Dockerfile, shell | {doc}`/developer/docker-architecture` |
| Improve the **documentation** | `docs/portal/` | MyST Markdown | [Documentation](#documentation) |

Most contributions are data- or template-driven and never touch the core
pipeline: you add a registry entry, a template, or a Pydantic model and the
existing machinery picks it up. You only edit Python control flow when adding a
genuinely new *kind* of behaviour. See {doc}`/developer/fslab-python/extending`
for that boundary.

## Development environment

All framework work happens **inside the toolchain container**, the same image
end users run. Your checkout is bind-mounted into the container, so edits on the
host are live inside it — the firesim-lab repo baked into the image is not the
one you work in. Day-to-day CLI and bridge changes never require an image
rebuild.

```bash
# from docker/, on the host
docker compose -f docker-compose-dev.yaml up -d
docker exec --user firesim-lab -it firesim-lab-dev bash
```

The CLI is installed editable inside the image, so changes under `fslab-cli/` —
along with the Jinja2 templates and the registry YAML — take effect on the next
`fslab` invocation with no reinstall. Scala and C++ changes are recompiled by the
next `fslab build`, still inside the running container. You only rebuild the image
itself when a change must exist at image-build time, such as a new system or
Python dependency.

For the full picture of the image stages, the bind mounts, the cache volumes,
and the local-iteration workflow, see {doc}`/developer/docker-architecture`.

:::{tip}
Validate most changes end-to-end against a throwaway project:

```bash
fslab new scratch && cd scratch
# edit fslab.yaml, then:
fslab generate && fslab build && fslab sim
```
:::

## Contribution workflow

1. **Branch from `main`.** Use a short, descriptive branch name, e.g.
   `bridge/spi`, `cli/sim-fpga-detach`, `docker/bump-verilator`.
2. **Keep the change focused.** One logical change per pull request. If you spot
   an unrelated bug or rough edge, note it in the PR description or open an issue
   rather than folding it in.
3. **Validate before pushing.** At minimum, run `fslab generate && fslab build &&
   fslab sim` on a project that exercises your change. For bridge or FPGA-path
   work, also run `fslab sim fpga` if you have an F2 host.
4. **Update the docs in the same PR** as the behaviour change — the relevant
   {doc}`/developer/index` page, and any user-facing command or concept page.
5. **Open the PR** with a clear description of what changed, why, and how you
   tested it. Link any related issue.

```{note}
The test suite under `fslab-cli/fslab/tests/` is partial and some of it is
outdated; framework-level testing and CI are being reworked. Until that lands,
manual end-to-end validation against a scratch project is the expected bar for a
contribution. Call out in your PR what you ran and what you could not.
```

## Code conventions

Match the style of the file you are editing rather than introducing a new one.
A few cross-cutting points:

- **Python (the CLI).** Built on Typer and Pydantic. Each subcommand is its own
  `typer.Typer()` registered in `fslab/cli.py`; validation belongs in a Pydantic
  schema, not in ad-hoc command code. Keep business logic out of `cli.py`.
- **Jinja2 templates.** Templates under `fslab-cli/fslab/templates/` are the
  single source of truth for everything `fslab generate` emits. Fix the template,
  never the generated output in a user project — it is overwritten on the next
  `fslab generate`. Regenerate a scratch project and inspect the rendered file
  before building.
- **Registry (`lib/registry.yaml`).** New bridges, platforms, simulators,
  bitbuilders, and runners are registered here against a fixed schema. Copy the
  nearest existing entry (each section has a commented `TEMPLATE` block) and keep
  the field set complete.
- **Comments.** Keep them about the code, not the change. Do not leave markers
  like `CHANGED`, `FIXED`, or `NEW`, and do not delete existing relevant comments
  — improve them if they are unclear.

## Documentation

This portal is Sphinx with `myst-parser`, under `docs/portal/`. Author pages in
MyST Markdown. Preview locally:

```bash
cd docs/portal
pip install -r requirements.txt
make html        # output in _build/html
```

When you write or edit a page, follow the conventions already in use across the
portal: a direct second-person voice in the present tense; fenced, language-tagged
code blocks; MyST `{doc}` and `{ref}` cross-references rather than hardcoded `.md`
links; and `:::{note}` / `:::{tip}` / `:::{warning}` admonitions used sparingly.
Keep terminology consistent — *firesim-lab*, *metasim*, *Golden Gate*, *bridge*,
*the shim*, *user RTL*, and *F2* (never *F1*). Do not modify the Sphinx build
configuration (`conf.py`, `requirements.txt`, `Makefile`, `.readthedocs.yaml`)
or restructure the table of contents without agreeing it first.

## Upstream boundaries

firesim-lab sits on top of FireSim and vendors pieces of Chipyard. Some things
are deliberately used as-shipped:

- **Golden Gate (a.k.a. MIDAS)** is used unmodified — no changes to the FAME-1
  transform, decoupling, or multi-clock handling.
- **Chipyard bridges** are vendored under `lib/`. Changes to a vendored bridge
  should stay close to upstream; if a change belongs upstream, raise it there
  before building on a local fork.
- **Orchestration is firesim-lab's own.** The FireSim *manager* is not used —
  new lifecycle behaviour belongs in the `fslab` CLI here, not in an upstream
  shim.

If a change you are planning crosses one of these boundaries, raise it as an
issue first so the approach can be agreed before you invest in it.
