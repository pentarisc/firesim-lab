# Quickstart: FPGA Acceleration

This page takes the `uart-print-test` project you ran in {doc}`metasim` and runs it on a real **AWS F2** FPGA. The design, bridges, and port maps are unchanged — FPGA acceleration adds only the `target` configuration that says *where to build the bitstream* and *where to run it*.

:::{warning}
This flow launches billable AWS resources (build instances and F2 FPGA instances) and builds an Amazon FPGA Image. Costs accrue while instances run. Make sure your AWS account, IAM roles, and quotas are set up first — see {doc}`/setup/aws/index` — and shut instances down when you are done.
:::

## Prerequisites

- A **working metasim project** — finish {doc}`metasim` first. FPGA acceleration reuses the same `fslab.yaml`.
- **AWS configured for F2.** Account, region, IAM roles, and the F2 platform HDK on a build host. Walk through {doc}`/setup/aws/index`, in particular {doc}`/setup/aws/firesim-lab-aws-setup`. Credentials live inside the container — run `aws sso login` (or `aws configure`) once per container session.

## How the FPGA flow is shaped

There are two phases, configured by two blocks under `target:`:

1. **Build** (`target.build`) — synthesise the design into a bitstream on a build host, then publish it as an AGFI (Amazon FPGA Image global ID). Run with `fslab build fpga`.
2. **Run** (`target.run`) — acquire an F2 instance, load the AGFI onto the FPGA, and execute the driver. Run with `fslab sim fpga`.

Each phase independently describes a **host** to acquire (today via `ec2_launch`, framework-managed EC2) and what to do with artifacts. The blocks below are a known-good shape; substitute your own account-specific values (AMI, key pair, S3 bucket, AGFI).

## Configure the build

Replace the generated `target.build` with an `ec2_launch` build host and an `aws_afi` publish step:

```yaml
target:
  platform:     "f2"
  clock_period: "1.0"
  fpga_sim: "xsim"

  build:
    fpga_frequency: 100.0         # target FPGA clock in MHz (0 < f <= 300)
    build_strategy: "TIMING"      # Vivado strategy: BASIC|AREA|TIMING|EXPLORE|CONGESTION|NORETIMING|DEFAULT
    bitbuilder_args: {}           # per-bitbuilder tunables; F2 has none today

    host:
      type: ec2_launch                          # framework-managed EC2 build host
      region: us-west-2                          # AWS region to launch in
      aws_profile: fslab-build                   # named AWS profile; omit to use AWS_PROFILE/default
      iam_instance_profile: fslab-fpga-builder   # REQUIRED — long builds authenticate via this role
      ssh_key: ~/.ssh/fslab_ed25519              # local private key (or omit for ssh-agent)
      ssh_user: ubuntu                           # SSH user for the AMI
      lifecycle: spot_one_time                   # spot_one_time | on_demand
      ami_id: ami-0c0b7a80d5725c332              # build AMI (no vetted default ships)
      key_name: firesim-lab                      # EC2 key-pair name installed at launch
      instance_type: z1d.2xlarge                 # build box (8 vCPU, 64 GB)

    publish:
      type: aws_afi                              # register the bitstream as an AGFI
      s3_bucket_name: my-firesim-builds          # S3 bucket for the staged DCP (required)
      hwdb_entry_name: "uart-print-test_v1"      # name for the published image; defaults to project name
      aws_profile: fslab-build                   # profile for publish; independent of host.aws_profile
```

Field-by-field, the parts you are most likely to change:

- `host.type: ec2_launch` — the framework launches and tears down the build instance. Set `instance_id:` instead to reuse a long-lived instance (start-if-stopped, stop on release).
- `iam_instance_profile` — **required**. The remote build authenticates to AWS through this instance profile, so hours-long builds survive local SSO expiry. Create it once per account ({doc}`/setup/aws/firesim-lab-aws-setup`).
- `lifecycle` — `spot_one_time` is cheapest; use `on_demand` if you cannot tolerate spot interruption.
- `ami_id`, `key_name`, `instance_type` — your build AMI, EC2 key pair, and machine size.
- `publish.type: aws_afi` — registers the result as an AGFI you can run on F2. Use `none` to leave artifacts local, or `local_tarball` for off-AWS targets.
- `publish.s3_bucket_name` — where the design checkpoint is staged for AFI creation.

Full semantics of every build/publish field are in {doc}`/commands/build`.

### Build the bitstream

```bash
fslab build fpga
```

This stages the project to the build host, runs Vivado synthesis and implementation, and (with `aws_afi`) creates the AGFI. The build takes a long time — when it finishes, it prints the **AGFI id** you need for the run block. Track an in-flight build with `fslab monitor build`; discard a stuck one with `fslab abandon build`.

## Configure the run

Add a `target.run` block (a sibling of `target.build`). It acquires an F2 instance, loads the AGFI, and runs the driver with the same kind of plusargs you used in metasim:

```yaml
  run:
    host:
      type: ec2_launch
      region: us-west-2
      aws_profile: fslab-build
      iam_instance_profile: fslab-fpga-runner    # smaller IAM than the build role
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: ubuntu
      lifecycle: on_demand                       # spot_one_time | on_demand
      ami_id: ami-0c0b7a80d5725c332              # FPGA Developer AMI (or your own)
      instance_type: f2.6xlarge                  # FPGA-attached instance, 1 slot
      key_name: firesim-lab
      fpga_slot:
        id: 0                                    # single slot today; must be 0
        runner_args:
          max_cycles: 100000                     # bound the run (AXIUARTPrinter loops forever)
          tracing: false                         # waveform capture
          autocounter: false                     # performance counters
          verify_hash: IF_PRESENT                # YES | NO | IF_PRESENT — sha256 vs payloads/SHA256SUMS
          payloads:
            - path: payloads/sample.hex          # staged into the remote slot dir
              remote_name: sample.hex            # defaults to basename(path)
          result_files: []                       # files to pull back into run/fpga/results/<ts>/
          extra_driver_flags:
            - "+loadmem=sample.hex"              # same plusarg as metasim; references the staged name

    artifact_source:
      type: aws_afi
      agfi: agfi-0123456789abcdef0               # the AGFI id printed by `fslab build fpga`
```

The fields that matter most:

- `host.iam_instance_profile` — the run role is **smaller** than the build role (it only needs to fetch and associate the FPGA image). See {doc}`/setup/aws/firesim-lab-aws-setup` for the policy.
- `instance_type` — an `f2.*` (FPGA-attached) machine. `f2.6xlarge` has one FPGA slot, which is all the single-node framework uses today.
- `fpga_slot.runner_args` — the driver's runtime knobs. `payloads` are uploaded next to the driver; `extra_driver_flags` are passed verbatim, so `+loadmem=sample.hex` works exactly as in metasim (the driver runs from the slot directory, so reference the staged `remote_name`).
- `max_cycles` — bounds the run, just like in software simulation.
- `artifact_source.agfi` — paste the AGFI id from the build step here.

Every run field is documented in {doc}`/commands/sim-fpga`.

### Run on the FPGA

```bash
fslab sim fpga
```

Foreground, this acquires the run host, loads the AGFI, and execs the driver over a pty — your terminal becomes the simulated UART, so the bytes from `sample.hex` print just as they did in metasim, now at FPGA speed. Results land in `run/fpga/results/<timestamp>/`.

For long workloads, detach so the run survives a laptop sleep, then reattach later:

```bash
fslab sim fpga --detach
fslab monitor run
```

`fslab abandon run` discards local state and cleans up the remote for a run you no longer want.

## Where to go next

- {doc}`/setup/aws/index` — the AWS account, IAM, and HDK setup this page depends on.
- {doc}`/commands/build` and {doc}`/commands/sim-fpga` — the complete build and run references.
- {doc}`/concepts/metasim-vs-fpga` — what actually differs between the software and FPGA paths, and when to use each.
