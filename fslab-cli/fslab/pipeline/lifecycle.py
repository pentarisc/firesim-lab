"""Pipeline-agnostic lifecycle helpers (placeholder).

Reserved for the in-flight guard, --detach launch helper, and --abandon
plumbing once the run pipeline lands and the build-side equivalents
(currently in `fslab.commands.build` / `fslab.commands.abandon` and the
bitbuilder's `check_no_existing_build`) are factored down.

Phase 1 leaves this module empty so future imports can target a stable
path; Phase 4/5 will fill it in.
"""
