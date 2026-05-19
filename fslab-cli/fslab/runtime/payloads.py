"""Payload / result-file resolution and hash verification.

Shared between the foreground runner and the detached launcher.

Responsibilities
----------------
1. Resolve user-supplied `PayloadConfig` / `ResultFileConfig` entries
   against the project dir (filesystem-touching checks that pydantic
   intentionally skips so the schema layer stays IO-free).
2. Parse the optional `payloads/SHA256SUMS` manifest and apply the
   `verify_hash` policy: enforce presence on YES, surface coverage gaps,
   harvest per-payload sha256s for result.yaml forensics.
3. Build the artefacts the runtime layer needs: upload pairs
   (`(local_path, remote_target)`), the remote `sha256sum -c` command,
   and per-payload `{remote_name, sha256, size_bytes}` triples for
   result.yaml.

Validation codes raised from here (as `InvalidRunConfig`):
  PAY-01  payload `path` must exist locally
  PAY-03  payload `remote_name` must not collide with the driver basename
          (pydantic already rejects collisions with static reserved names)
  PAY-04  `verify_hash: YES` requires `payloads/SHA256SUMS` to exist
  PAY-05  when a manifest is in use, every payload basename must appear
          in it
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fslab.schemas.runner_args import (
    F2RunnerArgs,
    PayloadConfig,
    ResultFileConfig,
    VerifyHash,
)


# `sha256sum -c` runs from the local `payloads/` dir; the manifest is
# expected at that path by convention. The dir name is fixed — the
# scaffolded project layout puts user payloads here and the SHA256SUMS
# manifest references files relative to this dir.
_LOCAL_PAYLOAD_DIR_NAME = "payloads"
_MANIFEST_BASENAME = "SHA256SUMS"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PayloadResolutionFailed(Exception):
    """Raised for payload-axis filesystem / manifest failures.

    Callers (RunConfig.from_validated) catch this and re-raise as
    `InvalidRunConfig` so the run pipeline surfaces a single failure
    type, but isolating the payload-specific cases here keeps the
    error-message machinery testable.
    """


class HashVerificationFailed(Exception):
    """Raised by `local_verify` when `sha256sum -c` reports a mismatch."""


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPayload:
    """One payload after path resolution + (optional) hash lookup."""

    local_path: Path
    """Absolute path on the local filesystem."""

    remote_name: str
    """Filename inside the per-slot remote dir."""

    size_bytes: int

    sha256: Optional[str] = None
    """Hex digest. Read from the manifest when verification is in play;
    None when there is no manifest or `verify_hash=NO`. The framework
    does not recompute sha256 itself — the manifest is the source of
    truth."""


@dataclass(frozen=True)
class ResolvedResultFile:
    """One driver-produced file the runner pulls back after the run."""

    remote_path: str
    """Path on the remote, relative to the per-slot remote dir."""

    local_name: str
    """Filename written under `run/fpga/results/<ts>/`."""


@dataclass(frozen=True)
class ResolvedPayloads:
    """All payload-axis state needed by the runtime layer.

    Constructed by `resolve_payloads()` once, threaded into the runner
    via RunConfig.
    """

    payloads: tuple[ResolvedPayload, ...] = field(default_factory=tuple)
    result_files: tuple[ResolvedResultFile, ...] = field(default_factory=tuple)
    verify_hash: VerifyHash = VerifyHash.IF_PRESENT
    local_manifest_path: Optional[Path] = None
    """Absolute path to `<project>/payloads/SHA256SUMS` when present and
    in scope per `verify_hash`. None means no verification will run."""

    local_payload_dir: Optional[Path] = None
    """Absolute path to `<project>/payloads`. Set whenever the manifest
    is in scope — `sha256sum -c` is invoked with this as cwd."""

    @property
    def has_verification(self) -> bool:
        return self.local_manifest_path is not None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_payloads(
    project_dir: Path,
    runner_args: F2RunnerArgs,
    driver_basename: str,
) -> ResolvedPayloads:
    """Resolve payload + result-file config into a runtime-ready bundle.

    Runs the filesystem-touching checks that the pydantic layer skipped:
    PAY-01 (existence), PAY-03 (driver-basename collision), PAY-04
    (YES-requires-manifest), PAY-05 (manifest covers payloads).

    Raises `PayloadResolutionFailed` with a leading `[PAY-NN]` code on
    any failure.
    """
    project_dir = project_dir.resolve()

    resolved_payloads: list[ResolvedPayload] = []
    for idx, p in enumerate(runner_args.payloads):
        rp = _resolve_one_payload(idx, p, project_dir, driver_basename)
        resolved_payloads.append(rp)

    resolved_result_files = tuple(
        ResolvedResultFile(
            remote_path=r.remote_path,
            local_name=r.local_name or Path(r.remote_path).name,
        )
        for r in runner_args.result_files
    )

    # Manifest handling: presence, policy, coverage. The manifest is the
    # source of truth for sha256s — when in scope, we read each payload's
    # hash out of it and stamp it onto the ResolvedPayload for forensics.
    manifest_path, payload_dir = _locate_manifest(
        project_dir, runner_args.verify_hash, bool(runner_args.payloads),
    )

    if manifest_path is not None:
        manifest = _parse_manifest(manifest_path)
        _check_manifest_covers(resolved_payloads, manifest, manifest_path)
        resolved_payloads = [
            _stamp_sha256(rp, manifest) for rp in resolved_payloads
        ]

    return ResolvedPayloads(
        payloads=tuple(resolved_payloads),
        result_files=resolved_result_files,
        verify_hash=runner_args.verify_hash,
        local_manifest_path=manifest_path,
        local_payload_dir=payload_dir,
    )


def _resolve_one_payload(
    idx: int,
    p: PayloadConfig,
    project_dir: Path,
    driver_basename: str,
) -> ResolvedPayload:
    """Resolve absolute local path; check PAY-01 + PAY-03."""
    raw = p.path
    local_path = raw if raw.is_absolute() else (project_dir / raw)
    local_path = local_path.resolve()

    if not local_path.is_file():
        raise PayloadResolutionFailed(
            f"[PAY-01] payloads[{idx}].path='{p.path}' does not resolve "
            f"to an existing file. Tried: {local_path}"
        )

    remote_name = p.remote_name or local_path.name

    # [PAY-03] driver-basename collision. The static reserved-name set is
    # rejected by pydantic; the driver basename is project-derived so
    # we catch it here.
    if remote_name == driver_basename:
        raise PayloadResolutionFailed(
            f"[PAY-03] payloads[{idx}].remote_name='{remote_name}' "
            f"collides with the driver binary basename. Choose a "
            f"different remote_name (the driver is uploaded into the "
            f"same per-slot dir)."
        )

    return ResolvedPayload(
        local_path=local_path,
        remote_name=remote_name,
        size_bytes=local_path.stat().st_size,
        sha256=None,
    )


def _locate_manifest(
    project_dir: Path,
    verify_hash: VerifyHash,
    has_payloads: bool,
) -> tuple[Optional[Path], Optional[Path]]:
    """Return (manifest_path, payload_dir) when verification is in scope,
    or (None, None) when it is not.

    Enforces [PAY-04]: YES + missing manifest is fatal.
    """
    if verify_hash is VerifyHash.NO:
        return None, None

    # With no payloads at all, the manifest question is moot — even
    # `verify_hash: YES` has nothing to verify. Skip silently.
    if not has_payloads:
        return None, None

    payload_dir = (project_dir / _LOCAL_PAYLOAD_DIR_NAME).resolve()
    manifest_path = payload_dir / _MANIFEST_BASENAME

    if manifest_path.is_file():
        return manifest_path, payload_dir

    if verify_hash is VerifyHash.YES:
        raise PayloadResolutionFailed(
            f"[PAY-04] verify_hash: YES requires a sha256 manifest at "
            f"{manifest_path}, but the file was not found. Either "
            f"generate the manifest (`cd payloads && sha256sum * > "
            f"SHA256SUMS`) or relax verify_hash to IF_PRESENT / NO."
        )

    # IF_PRESENT + missing manifest → skip verification. (The runtime
    # layer's warn-once is emitted by the runner, not here.)
    return None, None


def _parse_manifest(manifest_path: Path) -> dict[str, str]:
    """Parse a `sha256sum`-compatible manifest into {filename: hex}.

    Tolerates the two standard forms `<hex>  name` (text mode) and
    `<hex> *name` (binary mode); ignores blank lines and `#` comments.
    """
    out: dict[str, str] = {}
    with manifest_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # `<hex><space><space-or-asterisk><name>` is the canonical
            # form; split on the first space and strip the mode marker.
            parts = stripped.split(maxsplit=1)
            if len(parts) != 2 or not parts[1]:
                raise PayloadResolutionFailed(
                    f"[PAY-05] {manifest_path}:{lineno} is not a valid "
                    f"sha256sum line: {stripped!r}"
                )
            digest, name = parts
            name = name.lstrip("*").strip()
            out[name] = digest.lower()
    return out


def _check_manifest_covers(
    resolved: list[ResolvedPayload],
    manifest: dict[str, str],
    manifest_path: Path,
) -> None:
    """[PAY-05] every payload's basename must appear in the manifest."""
    missing = [
        rp.local_path.name
        for rp in resolved
        if rp.local_path.name not in manifest
    ]
    if missing:
        raise PayloadResolutionFailed(
            f"[PAY-05] {manifest_path} is missing entries for: {missing}. "
            f"Add them with `sha256sum {' '.join(missing)} >> "
            f"{manifest_path.name}` (run from the payloads/ dir)."
        )


def _stamp_sha256(
    rp: ResolvedPayload, manifest: dict[str, str],
) -> ResolvedPayload:
    """Copy the manifest's hex digest onto a payload for forensics."""
    return ResolvedPayload(
        local_path=rp.local_path,
        remote_name=rp.remote_name,
        size_bytes=rp.size_bytes,
        sha256=manifest.get(rp.local_path.name),
    )


# ---------------------------------------------------------------------------
# Upload pairs + remote command
# ---------------------------------------------------------------------------


def upload_pairs(
    resolved: ResolvedPayloads,
    remote_slot_dir: str,
) -> list[tuple[Path, str]]:
    """Return [(local_path, remote_target), ...] for every payload (and
    the manifest, when in scope). The caller `host.put`s each pair.

    The manifest is shipped to `<remote_slot_dir>/SHA256SUMS` rather
    than into a subdir so the remote `sha256sum -c SHA256SUMS` invocation
    can run with the slot dir as cwd, where the payloads also live.
    """
    pairs: list[tuple[Path, str]] = []
    for rp in resolved.payloads:
        pairs.append((rp.local_path, f"{remote_slot_dir}/{rp.remote_name}"))
    if resolved.local_manifest_path is not None:
        pairs.append(
            (resolved.local_manifest_path, f"{remote_slot_dir}/{_MANIFEST_BASENAME}")
        )
    return pairs


def remote_verify_command(
    resolved: ResolvedPayloads,
    remote_slot_dir: str,
) -> Optional[str]:
    """Return the shell command to verify hashes on the remote, or None
    when no verification is in scope.

    Runs `sha256sum -c SHA256SUMS` with the slot dir as cwd. The
    manifest references files by basename (per the local layout); the
    runner uploads payloads to those same basenames in the slot dir, so
    the same manifest verifies remotely without rewriting paths.

    The returned command is suitable for `host.run(...)` — non-zero exit
    signals a mismatch that the caller should treat as fatal
    (`failure.stage = "hash_verify"`).
    """
    if not resolved.has_verification:
        return None
    cwd = shlex.quote(remote_slot_dir)
    return f"cd {cwd} && sha256sum -c {_MANIFEST_BASENAME}"


# ---------------------------------------------------------------------------
# Local verification
# ---------------------------------------------------------------------------


def local_verify(resolved: ResolvedPayloads) -> None:
    """Run `sha256sum -c SHA256SUMS` locally before any upload happens.

    No-op when verification is out of scope (NO, or IF_PRESENT with no
    manifest). Raises `HashVerificationFailed` on mismatch so the caller
    can abort the run before network I/O.

    The caller (foreground runner / detached launcher) decides what to
    do with the failure — typically: emit a clear error and exit with a
    non-zero code. This helper does no orchestration on its own.
    """
    if not resolved.has_verification:
        return

    assert resolved.local_payload_dir is not None
    assert resolved.local_manifest_path is not None

    proc = subprocess.run(
        ["sha256sum", "-c", _MANIFEST_BASENAME],
        cwd=str(resolved.local_payload_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise HashVerificationFailed(
            "Local sha256 verification failed against "
            f"{resolved.local_manifest_path}.\n"
            f"  sha256sum stdout:\n{proc.stdout}\n"
            f"  sha256sum stderr:\n{proc.stderr}"
        )


# ---------------------------------------------------------------------------
# Forensics
# ---------------------------------------------------------------------------


def forensics_block(resolved: ResolvedPayloads) -> list[dict]:
    """Build the `payloads:` block for result.yaml.

    Each entry: `{remote_name, sha256, size_bytes}`. `sha256` is None
    when no manifest was in scope — call sites can serialise as YAML
    null or drop the key, either is acceptable.
    """
    return [
        {
            "remote_name": rp.remote_name,
            "sha256": rp.sha256,
            "size_bytes": rp.size_bytes,
        }
        for rp in resolved.payloads
    ]


def compute_sha256(path: Path, _chunk: int = 1 << 20) -> str:
    """Stream a file through sha256 and return the hex digest.

    Reserved for the (rare) case where we need a hash outside the
    manifest flow — e.g. a future "hash even without a manifest" mode.
    Not used by `resolve_payloads`; manifest entries are the source of
    truth there.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(_chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
