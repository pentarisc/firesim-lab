# External Build & Run Hosts

fslab acquires the machine that builds your bitstream (`fslab build fpga`) and the machine that runs it (`fslab sim fpga`) through a pluggable **host model**, selected by `host.type` in `fslab.yaml`:

- `ec2_launch` — the framework launches an EC2 instance for the job and tears it down afterwards.
- `external` — you point fslab at a machine **you have already provisioned and manage yourself**; fslab simply connects over SSH and uses it.

This page covers what an `external` host must provide. For the exact `fslab.yaml` field reference see {doc}`/commands/build` (build host) and {doc}`/commands/sim-fpga` (run host); for the AWS account resources an F2 host needs, see {doc}`/setup/aws/firesim-lab-aws-setup`.

Nothing here is needed for desktop metasimulation, nor if you use the framework-managed `ec2_launch` model.

## When to use an external host

`external` fits when the machine already exists and you want to keep control of its lifecycle:

- a long-lived or shared EC2 instance you manage yourself, so you are not waiting on a launch on every build;
- a shared lab / build server;
- any SSH-reachable host that already carries the F2 toolchain and AWS access.

With `external` you own provisioning, the toolchain, SSH reachability, and — for the F2 FPGA path — AWS credentials on the box. fslab never creates, starts, stops, or terminates an external host.

:::{note}
`external` describes *who manages the machine*, not an escape from AWS. The F2 toolchain is still AWS-tied: a build publishes its bitstream as an AGFI (`publish.type: aws_afi`), and a run loads that AGFI with `fpga-load-local-image`. Both need AWS access from the host — see the credential sections below.
:::

## How fslab uses an external host

Acquiring an external host is simply "open an SSH session" — there is no cloud API call involved. fslab connects with your `ssh_key` (or ssh-agent), verifies prerequisites, runs the job, and disconnects. A bitstream build is launched in the background on the host under `nohup`, so it survives the disconnect; the SSH session fslab opens does not keep the box occupied, and you reattach with {doc}`/commands/monitor`.

## SSH access

The connection is configured by three fields (`remote_platform_path`, below, is the fourth):

`host`
: Bare hostname or IP — **no** `user@` prefix and **no** `scheme://`.

`user`
: SSH username on the host.

`ssh_key`
: Path to a private key (supports `~`). Omit it to fall back to ssh-agent or `~/.ssh/config`.

The matching **public** key must be present in the host's `~/.ssh/authorized_keys` for `user`. If you generate the key locally:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/fslab_ed25519
ssh-copy-id -i ~/.ssh/fslab_ed25519.pub user@host   # or append the .pub to authorized_keys yourself
```

If the host is an EC2 instance you launched from an EC2 key pair, that key already works — see {doc}`/setup/aws/firesim-lab-aws-setup` (Step 3). Unlike `ec2_launch`, the `external` host model has **no** `key_name` field: there is no launch step at which to install a key, so you authenticate with a key the host already trusts.

fslab runs inside the container, so `ssh_key` must point at a path visible **inside the container** — see {doc}`/installation/mountpoints`. Verify connectivity from a container shell before running fslab:

```bash
ssh -i ~/.ssh/fslab_ed25519 user@host 'echo ok'
```

## Build host requirements (F2)

A `type: external` build host runs Vivado synthesis and then publishes the result. It must provide:

1. **The FPGA toolchain.** Vivado plus the `aws-fpga` / `aws-fpga-firesim-f2` tooling — exactly what the AWS **FPGA Developer AMI** ships. A login shell must put `vivado` on `PATH` (fslab runs the build under `bash -lc`).
2. **The platform HDK at `remote_platform_path`.** An absolute Unix path where the HDK lives. The framework cannot guess it, so it is required. fslab can push it for you: run `fslab build fpga --upload-platform` on the first build (or whenever the platform changes). Without the HDK pre-staged and without the flag, the build fails with *"Remote cl template not found."*
3. **AWS credentials.** F2 builds require `publish.type: aws_afi` today, so the build uploads the design checkpoint to S3 and runs `create-fpga-image` **on the host**. Before staging anything, fslab runs `aws sts get-caller-identity` on the host and fails fast if it cannot authenticate. The credentials need S3 access plus `ec2:CreateFpgaImage` / `ec2:DescribeFpgaImages`, and they must outlive the build (~90 min). Provide them one of two ways:
   - **Host is EC2** — attach the build instance profile from {doc}`/setup/aws/firesim-lab-aws-setup` (Step 4):
     ```bash
     aws ec2 associate-iam-instance-profile \
         --instance-id i-0123456789abcdef0 \
         --iam-instance-profile Name=fslab-fpga-builder
     ```
     Instance-profile credentials auto-refresh and never expire mid-build — the preferred option.
   - **Host is not EC2 (or you prefer)** — `aws configure` a profile on the host, or export `AWS_*` env vars, with the same permissions. Make sure they will not expire before the build finishes (SSO sessions can; long-lived keys or an instance profile avoid that).

## Run host requirements (F2)

A `type: external` run host is an FPGA-attached machine (an F2 instance / F2-class card). It must provide:

1. **The FPGA runtime tooling** — `fpga-load-local-image` and friends on `PATH`, again provided by the FPGA Developer AMI. The AGFI is loaded with `sudo fpga-load-local-image`.
2. **`remote_platform_path`** — required on the run side too; it roots the per-slot working directory fslab stages the driver and payloads into.
3. **AWS credentials** with `ec2:DescribeFpgaImages` + `ec2:AssociateFpgaImage` — the smaller run-host role from {doc}`/setup/aws/firesim-lab-aws-setup` (Step 5). No S3, no `CreateFpgaImage`. Provide them the same way as the build host (instance profile preferred on EC2).
4. **An AGFI loadable in the host's region.** The `artifact_source.agfi` must have been registered in, or replicated to, this host's region — see {ref}`Cross-region AGFIs <fslab-cross-region>`.

## fslab.yaml example

Both the build and the run host configured as `external`:

```yaml
target:
  platform:     "f2"
  clock_period: "1.0"
  fpga_sim:     "xsim"

  build:
    fpga_frequency: 100.0
    bitbuilder_args: {}
    host:
      type: external
      host: "10.0.0.5"                   # bare IP/hostname, no user@ or scheme
      user: "ubuntu"
      ssh_key: "~/.ssh/fslab_ed25519"    # omit to use ssh-agent / ~/.ssh/config
      remote_platform_path: "/opt/aws-fpga-firesim-f2"
    publish:
      type: aws_afi                       # required for F2
      s3_bucket_name: my-firesim-builds

  run:
    host:
      type: external
      host: "10.0.0.9"
      user: "ubuntu"
      ssh_key: "~/.ssh/fslab_ed25519"
      remote_platform_path: "/opt/aws-fpga-firesim-f2"
      fpga_slot:
        id: 0
        runner_args:
          verify_hash: IF_PRESENT
    artifact_source:
      type: aws_afi
      agfi: agfi-0123456789abcdef0
```

## Pre-flight checklist

From a container shell, before running the FPGA path:

- `ssh -i <key> user@host 'echo ok'` connects.
- On the host: `aws sts get-caller-identity` succeeds.
- On a build host: `vivado -version` works; the HDK exists at `remote_platform_path` (or you will pass `--upload-platform`).
- On a run host: `fpga-load-local-image` is on `PATH`.

## Related

- {doc}`/commands/build` — the `target.build.host` field reference.
- {doc}`/commands/sim-fpga` — the `target.run.host` field reference.
- {doc}`/setup/aws/firesim-lab-aws-setup` — the AWS account resources (key pair, IAM roles) an F2 host needs.
- {doc}`/installation/mountpoints` — which host paths are visible inside the container.
