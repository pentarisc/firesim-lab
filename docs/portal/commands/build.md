# fslab build

Run the build chain: generate (if needed), elaborate the Chisel shim through Golden Gate, and compile the C++ driver/simulator. The `fpga` subcommand additionally launches an FPGA bitstream build on a remote host.

`fslab build` implicitly calls {doc}`generate` first, and {doc}`sim` implicitly calls `fslab build`, so you only run `build` directly when you want to compile without simulating.

## Synopsis

```bash
fslab build [metasim|driver|fpgasim|fpga] [options]
```

With no subcommand, `fslab build` runs the **metasim** target.

| Subcommand | Builds |
|---|---|
| `metasim` *(default)* | The software simulator driver (Verilator/VCS/Xcelium per `host.emulator`). |
| `driver` | The C++ host driver for the target platform. |
| `fpgasim` | The FPGA-level simulation driver (e.g. Xilinx XSIM). |
| `fpga` | The platform driver **and** a real FPGA bitstream (remote build). |

## Options

These apply to every subcommand:

| Option | Default | Description |
|---|---|---|
| `--skip-rtl` | off | Skip the sbt + Java RTL steps (sbt package, Chisel generator, Golden Gate). |
| `--skip-driver` | off | Skip the C++ driver build (cmake + make). |
| `--force-gen` | off | Force {doc}`generate` even if the config hash is unchanged. |
| `-c`, `--config <path>` | `fslab.yaml` | Path to the project YAML. |
| `-j`, `--jobs <n>` | `4` | Parallel `make` jobs for the driver build. |
| `-e`, `--extra-args <str>` | `""` | Extra arguments passed to `make`, inserted after the target name (e.g. `VM_PARALLEL_BUILDS=1`). |
| `-d`, `--debug` | off | Enable build debug (currently applied to `make`). |

`fslab build fpga` adds three more — see [Building an FPGA bitstream](#building-an-fpga-bitstream).

## The build pipeline

For `metasim`, `driver`, and `fpgasim`, `fslab build` runs:

1. **generate** (hash-aware; implicit) — renders the project from `fslab.yaml`.
2. **`sbt package`** — assembles the Chisel design JAR.
3. **`java midas.chiselstage.Generator`** — emits FIRRTL from the shim.
4. **`java midas.stage.GoldenGateMain`** — runs Golden Gate (MIDAS) elaboration, applying the FAME-1 transform and wiring in the bridges.
5. **`cmake` + `make`** — builds the C++ driver/simulator for the chosen target.

Steps 2–4 are the "RTL steps" gated by `--skip-rtl`; step 5 is gated by `--skip-driver`. Each step streams to a log under `.fslab/logs/`. On success the result is a runnable binary under `build/` (consumed by {doc}`sim`).

:::{tip}
Use `--skip-rtl` when you have only changed C++ driver code, and `--skip-driver` when you only want the elaborated RTL. Skipping the unchanged half makes iteration much faster.
:::

## Example

```bash
fslab build                 # metasim, the default
fslab build metasim -j8     # 8 parallel make jobs
fslab build driver --skip-rtl   # recompile only the driver
```

## Building an FPGA bitstream

`fslab build fpga` first runs the full compile pipeline above (producing the FPGA-target driver), then hands off to a **background bitstream build on a remote host**. Bitstream builds take hours, so the launch is always backgrounded on the remote; by default the local CLI attaches a monitor (like `docker run` without `--detach`). `Ctrl+C` detaches cleanly without killing the remote build.

:::{warning}
`fslab build fpga` launches billable AWS resources and creates an Amazon FPGA Image. Costs accrue while build instances run. Make sure your AWS account, IAM roles, and quotas are set up first, and tear down anything you no longer need with {doc}`abandon`.
:::

### Additional options

In addition to the shared build options above, `fslab build fpga` accepts:

| Option | Default | Description |
|---|---|---|
| `-u`, `--upload-platform` | off | Upload the platform HDK / board-support files to the remote host (needed on first build, or after the platform changes). |
| `--detach` | off | Launch the build and exit immediately without attaching the monitor. CI-friendly; reattach later with `fslab monitor build`. |
| `--skip-compile` | off | Reuse compile artefacts from a prior successful `fslab build fpga` and jump straight to the bitstream build. |

`--skip-compile` requires (1) a prior successful `fslab build fpga` for this project and (2) a clean remote-build slate. If a previous remote build's state is still present, the command refuses to start and points you at {doc}`monitor` (to attach) or {doc}`abandon` (to tear it down).

### Prerequisites for an F2 build

Before the first `fslab build fpga`, configure the F2 build environment:

- **AWS account set up for F2** — account, region, IAM roles, and service quotas. Walk through {doc}`/setup/aws/index`, in particular {doc}`/setup/aws/firesim-lab-aws-setup`.
- **Credentials inside the container.** The `fslab` CLI runs in the container, so credentials must be available there. Run `aws sso login` (or `aws configure`) once per container session.
- **A build host with the F2 platform HDK.** With `host.type: ec2_launch` the framework launches the build instance for you from your AMI; with `host.type: external` you point at a pre-provisioned host and give its `remote_platform_path` (where the HDK lives). Pass `--upload-platform` on the first build (or whenever the platform changes) to push the HDK / board-support files up.

### Configuring the F2 build environment

The `target.build` field reference below is exhaustive, but a typical F2 build uses a framework-managed EC2 host (`ec2_launch`) and publishes the result as an AGFI (`aws_afi`). A known-good shape — substitute your own account-specific values (AMI, key pair, S3 bucket):

```yaml
target:
  platform:     "f2"
  clock_period: "1.0"
  fpga_sim: "xsim"

  build:
    fpga_frequency: 100.0         # target FPGA clock in MHz (0 < f <= 300)
    build_strategy: "TIMING"      # Vivado strategy
    bitbuilder_args: {}           # F2 has no tunables today

    host:
      type: ec2_launch                          # framework-managed EC2 build host
      region: us-west-2                          # AWS region to launch in
      aws_profile: fslab-build                   # named profile; omit for AWS_PROFILE/default
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
      hwdb_entry_name: "uart-print-test_v1"      # published image name; defaults to project name
      aws_profile: fslab-build                   # profile for publish; independent of host.aws_profile
```

The parts you are most likely to change:

- `host.type` — `ec2_launch` launches and tears down the instance per build. Set `instance_id:` to reuse a long-lived instance (start-if-stopped, stop on release), or use `external` for a pre-provisioned host.
- `iam_instance_profile` — **required**. The remote build authenticates to AWS through this instance profile, so hours-long builds survive local SSO expiry. Create it once per account ({doc}`/setup/aws/firesim-lab-aws-setup`).
- `lifecycle` — `spot_one_time` is cheapest; use `on_demand` if you cannot tolerate spot interruption.
- `ami_id`, `key_name`, `instance_type` — your build AMI, EC2 key pair, and machine size.
- `publish.type` — `aws_afi` registers an AGFI you can run on F2; `none` leaves artifacts local; `local_tarball` targets off-AWS boards.
- `publish.s3_bucket_name` — where the design checkpoint is staged for AFI creation.

When the build finishes it prints the **AGFI id** — paste it into `target.run.artifact_source.agfi` to run it (see {doc}`sim-fpga`). For the same configuration in the context of the full walkthrough, see {doc}`/quickstart/fpga`.

```bash
fslab build fpga                 # compile + bitstream, attach monitor
fslab build fpga --detach        # launch and return; monitor later
fslab build fpga --skip-compile  # reuse compile output, rebuild bitstream
```

---

## The `target.build` reference

`target.build` configures the FPGA bitstream build. It is read only by `fslab build fpga`; metasim and driver builds ignore it. It has four orthogonal axes. Defaults are layered — framework registry, then any custom registry, then your value in `fslab.yaml` wins — so you only specify what you want to override.

```yaml
target:
  build:
    fpga_frequency: 100.0
    build_strategy: "TIMING"
    bitbuilder_args: {}
    host:
      type: external
      host: "10.0.0.5"
      user: "centos"
      ssh_key: "~/.ssh/firesim.pem"
      remote_platform_path: "/opt/aws-fpga-firesim-f2"
    publish:
      type: none
```

### Build parameters

`fpga_frequency`
: Build frequency in MHz; must be in `(0, 300]`.

`build_strategy`
: Vivado strategy: `BASIC`, `AREA`, `TIMING`, `EXPLORE`, `CONGESTION`, `NORETIMING`, or `DEFAULT`. Default `TIMING`.

`bitbuilder_args`
: Per-bitbuilder tunables, validated against the platform's bitbuilder schema `[BBA-01]`. The F2 bitbuilder has no user-tunables today, so `{}` is correct.

### `host:` — build-host acquisition

A discriminated union keyed on `type`. The build host is where Vivado runs.

#### `type: external` — a pre-provisioned SSH host

`host`
: IP or hostname. Must not contain `@` or `://` `[HMOD-03]` — set the username separately.

`user`
: SSH username.

`ssh_key`
: Path to a private key (supports `~`). Omit/empty to fall back to ssh-agent or `~/.ssh/config` `[HMOD-02]`.

`remote_platform_path`
: Absolute path to the platform HDK on the build host `[HMOD-04]`. Required for `external` — the framework ships no default because layout varies per install.

See {doc}`/setup/external-host` for what a `type: external` build host must provide: SSH access, the platform HDK, the FPGA toolchain, and AWS credentials.

#### `type: ec2_launch` — a framework-managed EC2 build host

Two sub-modes selected by `instance_id`:

- **ephemeral** (`instance_id` unset) — launch a fresh instance per build, terminate on release;
- **managed reuse** (`instance_id` set) — start the named instance if stopped, stop it on release.

Key fields:

`region`
: AWS region (e.g. `us-west-2`) `[AWS-02]`. Required.

`iam_instance_profile`
: IAM instance profile name attached to the build host `[HMOD-07]`. **Required.** The remote wrapper authenticates to AWS through this profile, so hours-long builds survive local SSO expiry.

`aws_profile`
: Named AWS profile for the local boto3 session `[AWS-06]`. Omit to fall back to `AWS_PROFILE` or `[default]`.

`lifecycle`
: `spot_one_time` (cheapest) or `on_demand` (safest). Ephemeral mode only.

`ami_id`, `instance_type`, `aws_fpga_version`
: AMI, instance type, and HDK version tag. Registry supplies defaults for `instance_type` / `aws_fpga_version`; `ami_id` has no shipped default — supply one or use managed reuse `[AWS-01]` `[AWS-03]`.

`instance_id`
: Existing instance id (`i-…`) to enable managed reuse `[AWS-07]`.

`key_name`, `subnet_id`, `ssh_key`, `ssh_user`
: EC2 key-pair name installed at launch; subnet; local private key path; and SSH username (default `centos`).

:::{note}
`fpga_slot` is a **run-side** concept and must not appear under `target.build.host` `[FSLOT-02]`. It belongs in `target.run` — see {doc}`sim-fpga`.
:::

### `publish:` — post-build artifact handling

A discriminated union keyed on `type`.

#### `type: none`

No publish step; build artifacts stay in the local results directory.

#### `type: local_tarball`

Tar the bitstream + metadata into a project-relative directory and emit an hwdb-style descriptor.

`output_subdir`
: Project-relative output directory. Default `built-artifacts`.

`hwdb_entry_name`
: Descriptor name; defaults to `project.name`.

#### `type: aws_afi` — publish an AGFI (F2)

Uploads the DCP to S3 and runs `create-fpga-image` to produce an AGFI.

`s3_bucket_name`
: S3 bucket for the DCP, DNS-compliant `[AWS-04]`. **Required.** Auto-created if absent.

`append_userid_region`
: Append `-<userid>-<region>` to the bucket name (firesim convention). Default `true`.

`aws_profile`
: Named AWS profile for publish calls `[AWS-06]`; independent of `host.aws_profile`.

`hwdb_entry_name`
: hwdb entry name; defaults to `project.name`.

`copy_to_regions`
: Replicate the AFI to these regions `[AWS-02]`.

`sns_topic_arn`
: Optional SNS topic for completion notifications `[AWS-05]`.

`post_build_hook`
: Optional script run after a successful publish, with the local results dir as `$1`.

The AGFI emitted here is what you reference in `target.run.artifact_source.agfi` — see {doc}`sim-fpga`.

## Related

- {doc}`init` — the `project` / `design` / `host` field references.
- {doc}`sim-fpga` — the `target.run` reference and running the bitstream.
- {doc}`monitor`, {doc}`abandon` — managing an in-flight FPGA build.
- {doc}`/setup/aws/firesim-lab-aws-setup` — AWS account setup for F2.
- {doc}`/concepts/metasim-vs-fpga` — when to build which target.
