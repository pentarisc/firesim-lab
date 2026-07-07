# Sim reference — F2 build & run (steps 9–11) + notifications

Loaded only after the metasim gate is open. Never reached for a metasim-only run.
All AWS/`fslab` calls go through the shared seam (`fslab_exec` / `fslab_in_dir`).

## F2 questionnaire → patch `fslab.yaml` `target.*` (step 9)

ASK (never guess): AWS `profile`/`region`, **SSO mode**, build host model, run host
model, `fpga_slot`, publish mode, and an explicit **spend acknowledgement**. Patch
into `target.build` and `target.run`. Reference the version-matched
`commands/build`, `commands/sim-fpga`, and `setup/aws/firesim-lab-aws-setup` portal
pages. Key fields (see `lib/registry.yaml` `f2` block + `fslab.yaml.j2`):

```yaml
target:
  build:
    host:
      type: ec2_launch
      region: <region>
      iam_instance_profile: fslab-fpga-builder    # from Setup S4
      instance_type: z1d.2xlarge                  # compute host, no FPGA slot
      ami_id: <FPGA Developer AMI in region>
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: centos
      key_name: firesim-lab
      # data_volume_gb: 100                       # optional; grow the AMI's secondary volume (GiB)
      # root_volume_gb: 60                        # optional; grow the root volume (GiB)
      # volume_type: gp3                          # optional; needs a *_volume_gb set
    publish: { ... }                              # publish mode
  run:
    host:
      type: ec2_launch
      region: <region>
      aws_profile: <sso-profile>
      iam_instance_profile: fslab-fpga-runner     # from Setup S4
      lifecycle: spot_one_time
      ami_id: <FPGA Developer AMI in region>
      instance_type: f2.6xlarge                   # 1 FPGA slot
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: centos
      key_name: firesim-lab
      # data_volume_gb: 100                       # optional; grow the AMI's secondary volume (GiB)
    artifact_source: { type: aws_afi, agfi: <agfi from build> }
```

**Volume overrides (optional, `ec2_launch` only):** `data_volume_gb` /
`root_volume_gb` grow the launched host's EBS volumes by role (via AMI
introspection — no device names needed); omit them to inherit the AMI's baked
sizes. Grow-only. Reach for `data_volume_gb` when a large design overflows the
AMI's default secondary volume during build. `volume_type` (gp3/gp2/io1/io2/
st1/sc1/standard) applies only to a volume you're resizing. `data_volume_gb`
yields *usable* space, not just a bigger block device: a cloud-init grow runs at
first boot to expand the data filesystem to fill the enlarged volume (logged to
`/var/log/firesim-lab-growfs.log` on the host). The root filesystem is grown by
the AMI's own growpart.

## Build (step 10)

- `fslab build fpga` — the **EC2 launch / AFI create is a HARD SPEND CONFIRM**.
  Surface the instance type + region and wait for explicit approval
  (`needs_decision`). Never auto-spend.
- Launch the **`build-monitor`** sub-agent in the background. It polls
  `fslab monitor build`, on image-ready pulls logs/artifacts, then **terminates the
  build EC2** (cost safety, evidence-preserving — logs are pulled even on failure).
  It returns a report; **you** send the notification when re-invoked on completion.
- Live AGFI/build status is read from `build/fpga/.fslab/build.yaml` — record only
  `f2.last_build_id` in the project stamp.

## Run (step 11)

- Patch `fslab.yaml` with the AGFI/image, then `fslab sim fpga --detach`.
- Launch the **`run-monitor`** sub-agent. It polls `fslab monitor run`, on
  completion pulls the output, then **stops the F2 host**. Returns a report; you
  send the notification.
- Live run status is read from `run/fpga/.fslab/run.yaml`; record only
  `f2.last_run_id`.

## Cleanup contract

Teardown (terminate build EC2 / stop F2 host) is **automatic, no prompt** for cost
safety — but **always after** evidence is pulled via `fslab monitor`, so even a
failed build/run leaves diagnostics behind. If a sub-agent cannot confirm teardown,
report it loudly (`needs_decision`) so the user can intervene.

## Notification send mechanics (§20.2)

**Only this foreground skill sends.** Sub-agents return reports; you send when the
harness re-invokes you on a background task's completion. Read the channel from the
workspace stamp `notifications` block. Push set by default: `error_diagnosed`,
`error_opaque`, `needs_decision` (attention) + `completed` (completion);
`auto_fixed` is inline-only.

- **`channel.type: "webhook"` (canonical):** `curl` the webhook URL; the token is
  read from the env var named by `channel.env` (never stored in the stamp). Send a
  compact JSON body built from the report object (title + summary + a link/where).
- **`channel.type: "mcp"`:** call the configured MCP "send message" tool with the
  same composed content.
- **`channel.type: "local"`:** fall back to the built-in `preferredNotifChannel`
  (terminal/OS bell) — zero-setup, generic, local-only.

Hooks are **not** the mechanism — a `Notification` hook can only carry the
harness's generic text, not the composed/classified message. Send directly.
