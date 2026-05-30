# fslab sim fpga

Run a built bitstream on a real AWS F2 host. The command requests a run host, uploads your driver and payloads, loads the AGFI onto the FPGA, and runs the simulation — in the foreground (your terminal becomes the simulated console) or detached (background on the remote).

This requires a published AGFI from {doc}`build` (`fslab build fpga` with an `aws_afi` publish) and a configured AWS account — see {doc}`/setup/aws/firesim-lab-aws-setup`. The `target.run` block in `fslab.yaml`, documented below, must be present.

## Synopsis

```bash
fslab sim fpga [-c <path>] [--detach]
```

## Options

| Option | Default | Description |
|---|---|---|
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML. |
| `--detach` | off | Launch the run in the background on the remote and exit immediately. Reattach with `fslab monitor run`. |

## Foreground run (default)

`fslab sim fpga` resolves `target.run` from `fslab.yaml` and then:

1. Requests a run host per `target.run.host` (typically an `f2.*` instance).
2. Uploads the local driver binary and the configured `payloads` into the per-slot remote directory.
3. Loads the AGFI onto the FPGA slot.
4. Execs the driver over SSH with a pty, so **your local terminal becomes the simulated UART** — you interact with the design directly.
5. On exit (driver finishes, or you `Ctrl+C`), pulls results back into `run/fpga/results/<timestamp>/`.

## Detached run (`--detach`)

The same staging and AGFI-load flow, but the driver is launched under `nohup` in the background. A local stamp at `run/fpga/.fslab/run.yaml` records what {doc}`monitor` and {doc}`abandon` need to reattach or tear the run down later. Use this for long workloads that must survive a laptop sleep or disconnect.

```bash
fslab sim fpga              # foreground; terminal is the console
fslab sim fpga --detach     # background; reattach with: fslab monitor run
```

After a detached launch, manage the run with {doc}`monitor` (`fslab monitor run`) and {doc}`abandon` (`fslab abandon run`).

---

## The `target.run` reference

`target.run` is optional: a project that only builds bitstreams leaves it unset, and the run-side validation is skipped. When present, it has two axes — `host` (with a nested `fpga_slot`) and `artifact_source`. Defaults are layered the same way as `target.build`.

```yaml
target:
  run:
    host:
      type: ec2_launch
      region: us-west-2
      iam_instance_profile: fslab-fpga-runner
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: ubuntu
      lifecycle: spot_one_time
      ami_id: ami-0c0b7a80d5725c332
      instance_type: f2.6xlarge
      key_name: firesim-lab
      fpga_slot:
        id: 0
        runner_args:
          verify_hash: IF_PRESENT
          payloads:
            - path: payloads/sample.hex
          extra_driver_flags:
            - "+loadmem=sample.hex"
    artifact_source:
      type: aws_afi
      agfi: agfi-0123456789abcdef0
```

### `host:` — run-host acquisition

Same discriminated union as `target.build.host` (`external` / `ec2_launch`) and the same fields — see the `host:` reference under `target.build` in {doc}`build`. The differences on the run side:

- The host is typically FPGA-attached (e.g. `instance_type: f2.6xlarge`).
- A run host needs a **smaller IAM policy** than a build host — only `DescribeFpgaImages` + `AssociateFpgaImage`, so `fpga-load-local-image` can fetch and verify the AGFI. No S3 write, no `CreateFpgaImage`.
- The run host **must** carry an `fpga_slot` block `[FSLOT-03]`.

### `host.fpga_slot:` — the FPGA slot

```yaml
fpga_slot:
  id: 0
  runner_args: { ... }
```

`id`
: Slot identifier. Single-slot today, so must be `0` `[FSLOT-01]`.

`runner_args`
: Per-runner tunables, validated against the platform's runner schema `[RUNA-01]`. The F2 runner's fields:

`max_cycles`
: Cap the simulation at this many target cycles. Omit to run to natural termination.

`tracing`
: Enable TracerV output; trace files are pulled into the results directory. Default `false`.

`autocounter`
: Enable autocounter CSV emission; files are pulled into the results directory. Default `false`.

`payloads`
: Files staged into the per-slot remote directory alongside the driver. Each entry is `{ path, remote_name? }`; `remote_name` defaults to the basename of `path`. The driver references payloads by `remote_name` (it `cd`s into the slot dir before exec). `remote_name` must be unique `[PAY-02]` and must not collide with framework-reserved names `[PAY-03]`.

`result_files`
: Files the driver produces to pull back into `run/fpga/results/<ts>/`. Each entry is `{ remote_path, local_name? }`. Missing files at pull time warn rather than fail `[PAY-06]`.

`verify_hash`
: `payloads/SHA256SUMS` verification policy: `YES` (required; missing manifest is fatal), `NO` (never), or `IF_PRESENT` (verify when present — the default). Checked locally before upload and on the remote before exec.

`extra_driver_flags`
: Verbatim `+plusarg` / `--` flags appended to the driver invocation. The escape hatch for knobs without a typed field; passed through unvalidated.

### `artifact_source:` — where the bitstream comes from

```yaml
artifact_source:
  type: aws_afi
  agfi: agfi-0123456789abcdef0
```

`type`
: Must be a source the platform supports `[ARTSRC-01]`. `aws_afi` is the only one today.

`agfi`
: The AGFI id (`agfi-` + 17 hex chars) `[AWS-08]` — the value `fslab build fpga`'s `aws_afi` publish printed. Cross-region replication is the publisher's job; set `host.region` to a region the AFI was replicated to.

## Related

- {doc}`build` — produce the AGFI (`target.build` reference).
- {doc}`monitor`, {doc}`abandon` — manage a detached run.
- {doc}`/setup/aws/firesim-lab-aws-setup` — AWS account + IAM setup.
- {doc}`/quickstart/fpga` — the F2 quickstart.
- {doc}`/concepts/metasim-vs-fpga` — metasim vs hardware trade-offs.
