# Documentation Portal — Chapter Handoff Prompt

Paste this prompt verbatim as the opening message of a new conversation focused on drafting or updating a documentation chapter for the firesim-lab portal. On receiving it, the LLM must ask the user to supply values for the slots marked `{{...}}` (listed under "This conversation's assignment") and wait for them before doing any work.

---

## Project orientation

`firesim-lab` is a framework that builds on UCB-BAR's FireSim to provide no-Chisel, Verilog/SystemVerilog blackbox design simulation — both metasimulation and FPGA-accelerated (AWS F2). End users write only Verilog/SV; the framework generates the Chisel/Scala shim that interfaces with FireSim's Golden Gate (MIDAS). The `fslab` CLI orchestrates the entire project lifecycle.

For full project context, read `CLAUDE.md` and `README.md` at the repo root before starting.

## Documentation portal facts

- Portal location: `docs/portal/`
- Markup: **MyST Markdown** (Sphinx + `myst-parser`)
- Theme: `sphinx-book-theme`
- Build config: `docs/portal/conf.py`, `docs/portal/requirements.txt`, `docs/portal/Makefile`, and `.readthedocs.yaml` at the repo root. Published at `https://firesim-lab.readthedocs.io`. Author chapters in vanilla MyST — no RTD-specific features, no theme-specific extensions beyond what `sphinx-book-theme` and the MyST extensions enabled in `conf.py` ship with. **Do not modify the build config from a chapter conversation.**
- Table of contents: see `docs/portal/index.md` and per-section `index.md` files for the agreed structure. **Do not restructure the ToC** without the user's confirmation.

## Working rules (from `CLAUDE.md`)

- Restate the goal in one sentence and confirm before editing anything.
- For material changes to *existing* files, place edits in `tempwork/` using the naming convention `<name>--<YYYY-MM-DD>--<HH-MM>.<ext>`. The `tempwork/` directory is flat — no subdirectories. Show both paths and wait for confirmation before replacing the original. (Creating *new* content in a placeholder file is not considered a material change to an existing file, since placeholders contain no real content.)
- Stay strictly in scope. Do not draft adjacent chapters or refactor unrelated sections, even if they look incomplete.
- Read only the files needed for the assigned chapter. Ask before scanning the broader project.
- Do not add comments like `CHANGED`, `FIXED`, `NEW`. Do not delete existing relevant comments.

## This conversation's assignment

Before drafting, ask the user for each of these and wait for answers:

- **Chapter / page:** `{{CHAPTER_PATH}}` (e.g. `docs/portal/concepts/bridges-overview.md`)
- **Scope:** `{{SCOPE_NOTES}}` — what this chapter covers and what it explicitly does NOT cover (cross-link to sibling chapters instead).
- **Audience:** `{{AUDIENCE}}` — e.g. "first-time user with HDL background, no FireSim familiarity" or "framework contributor adding a new bridge".
- **Source references:** `{{SOURCE_REFERENCES}}` — list of files, code paths, or external links the author should consult. Read these before drafting.
- **Length target:** `{{LENGTH_HINT}}` — e.g. "600–1500 words" or "reference page, as long as needed".

## Style guide

- Voice: direct, second-person ("you run", "the CLI emits"), present tense.
- Default length: 600–1500 words per leaf page unless the assignment overrides. If a section is growing past ~2000 words, propose a split to the user before continuing.
- Code blocks: triple-backtick fenced and language-tagged (` ```bash `, ` ```yaml `, ` ```scala `, ` ```verilog `, ` ```python `).
- Admonitions: use MyST `:::{note}` / `:::{warning}` / `:::{tip}` sparingly — at most one or two per page.
- Cross-references: prefer MyST `{doc}` and `{ref}` roles over hardcoded `.md` URLs. Example: `` {doc}`/concepts/bridges-overview` ``.
- Diagrams: source `.drawio` lives at `docs/`; export PNG/SVG to `docs/portal/_static/images/` and reference from there.
- No emoji unless the user explicitly requests it.
- Do not leave `TODO` / `TBD` inside finished content — either complete the section or call it out explicitly to the user at the end of the conversation.

## Output expectations

- A drafted chapter file (or a `tempwork/` copy if editing an existing non-placeholder file).
- If new pages were created, update the appropriate `index.md` toctree to include them.
- A short summary at the end: what was drafted, any open questions, any sources that could not be resolved.

## Out of scope for this conversation

- Modifying build configuration (`docs/portal/conf.py`, `docs/portal/requirements.txt`, `docs/portal/Makefile`, `.readthedocs.yaml`). These are managed centrally.
- Restructuring the portal ToC or moving/renaming sibling chapters.
- Drafting chapters other than the assigned one.

## Notes for future splits

Some placeholder pages may need to be split as content is drafted. Known candidates (decide at drafting time, in coordination with the user):

- `docs/portal/commands/build.md` — `fslab build` does many distinct things internally (CMake generation, Chisel/FIRRTL elaboration, Golden Gate, metasim vs FPGA paths). If the draft grows past ~2000 words, propose splitting into a `commands/build/` subfolder.
- `docs/portal/commands/sim-fpga.md` — foreground vs detached flows plus F2 instance lifecycle may justify a split.
- `docs/portal/developer/docker-architecture.md` — could grow to cover image layout, mount strategy, cache mounts, devcontainer wiring.
