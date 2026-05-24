# docs/

This directory contains the firesim-lab documentation portal and related working files.

## Authoritative documentation

Published documentation lives in [`portal/`](portal/). This is the **only** source of truth for user- and developer-facing docs. New documentation chapters must be added under `portal/` and wired into the appropriate `index.md` toctree.

## Other contents

- `prompts/` — self-contained prompts used to drive split conversations that draft individual chapters.
- `firesim-lab.drawio` — editable source for architecture diagrams. Exported PNG/SVG goes into `portal/_static/images/`.
- `verify_remote_setup.sh` — utility script (not portal content).

## What does NOT belong here

Scratch notes, draft documents, or ad-hoc markdown files at this level. If it is worth keeping, it belongs as a chapter inside `portal/`. Loose markdown at `docs/` level will rot.
