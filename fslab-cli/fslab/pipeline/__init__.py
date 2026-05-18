"""Pipeline-agnostic orchestration layer.

Shared between the build pipeline (`fslab.bitstream`) and the upcoming
run pipeline (`fslab.runtime`, to land in a later phase). Modules here
know nothing about bitstreams, drivers, AGFIs, or platform-specific
recipes — they provide the SSH host abstraction, provider registry,
generic monitor primitives, and (Phase 4+) lifecycle helpers.

Submodules:
  host       — Host + ExternalHost + Ec2LaunchHost + HostProvider base
               + PROVIDER_REGISTRY + cleanup_remote
  stamp      — small stamp-touching utilities (utc_now_iso, …)
  monitor    — connect / verify-id / tail-until-result / interruptible-sleep
  lifecycle  — placeholder; populated when the run pipeline arrives
"""
