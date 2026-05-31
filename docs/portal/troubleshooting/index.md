# Troubleshooting & FAQ

```{note}
Mostly placeholder — content grows organically with reported issues. Initial
seed: common build failures, AWS quota issues, bitstream errors, container/host
mount confusion. See `docs/prompts/docs-portal-handoff.md` for the drafting
workflow.
```

## Version mismatches

### `firesim-lab version mismatch` when starting a workspace

```text
✗ firesim-lab version mismatch.
     This workspace is pinned to : 0.7.0
     Installed version is        : 0.8.0
```

You reinstalled a different firesim-lab version than this workspace was created
against. The host scripts and the container image are a matched set, so the
launcher refuses to mix them. Either migrate the workspace to the installed
version:

```bash
firesim-lab --upgrade
```

or reinstall the version the workspace expects. Full details — including what
`--upgrade` preserves and when to follow up with `--clean-cache` — are in
{doc}`/installation/versioning`.

### `Incompatible project file (fslab.yaml)` / `Incompatible registry file`

```text
Incompatible project file (fslab.yaml):
  declared fslab_version : 0.7.0
  this fslab version     : 0.8.0
```

The `fslab` CLI inside the container refuses to operate on a `fslab.yaml` or
`registry.yaml` whose `fslab_version` does not match its own `MAJOR.MINOR`
(files with no `fslab_version` at all are also refused — they predate version
stamping). Migration is manual: apply any schema changes for the new version and
bump the file's `fslab_version`. Step-by-step instructions are in
{doc}`/installation/versioning`.
