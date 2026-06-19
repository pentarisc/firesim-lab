# Setup reference — AWS provisioning (S4)

Loaded only when the user opts into AWS F2. Source of truth in the portal:
`setup/aws/index`, `aws-primer`, `identity-center-sso`, `firesim-lab-aws-setup`
(link the **version-matched** pages — `…/en/v<active>/…`). All AWS commands run
**inside the container** (it ships AWS CLI v2) with an explicit `--profile`
(modern SSO has no default profile). Go through `fslab_exec`.

## Decide the developer kind first

- **Solo developer** (own admin, personal account): you MAY run the admin-CLI
  scripts under the admin profile, **one confirmed step at a time**.
- **Org developer** (logs in via a `FireSim-Developer` permission set; lacks
  `iam:CreateRole`/`CreateInstanceProfile` by design): do **NOT** attempt
  creation. Verify the admin-provisioned roles/profile/PassRole exist and, on a
  gap, hand the user the exact commands for **their admin** to run. (Reminder: an
  `AWSReservedSSO_*` permission-set role cannot back an EC2 instance profile — the
  instance-profile role is always a separate regular IAM role.)

Before offering the admin-CLI scripts, optionally confirm IAM-write capability
(e.g. `iam:CreateRole` via `simulate-principal-policy`) rather than trusting the
intent answer alone.

## The four layers

### 1. Console / account / quota — explain + link + verify only
Not scriptable. Cover: account + root security and a billing budget (`aws-primer`);
enable IAM Identity Center; create a user/group + the `FireSim-Developer`
permission set; **request the F2 service quota** ("Running On-Demand F instances",
default 0, ~1–2 day approval — **do this EARLY**). Verify the quota and an FPGA
Developer AMI with `scripts/verify-aws.sh`.

### 2. Admin-CLI — offer to run scripts, per-step confirm (solo-admin only)
Each script takes `<admin-profile> <region>` and is idempotent. Run in order,
confirming each:

| Step | Script | Creates |
|---|---|---|
| Key pair (Step 3) | `scripts/aws-create-keypair.sh` | EC2 key pair `firesim-lab` → `~/.ssh/fslab_ed25519` |
| Build role (Step 4) | `scripts/aws-create-build-role.sh` | role + instance profile `fslab-fpga-builder` |
| Run role (Step 5) | `scripts/aws-create-run-role.sh` | role + instance profile `fslab-fpga-runner` |
| PassRole grant | `scripts/aws-grant-passrole.sh <admin-profile> <ic-home-region> [perm-set]` | `iam:PassRole` on `fslab-fpga-builder` for the permission set |

These map 1:1 to `firesim-lab-aws-setup` Steps 3/4/5 and the `identity-center-sso`
PassRole grant. Show the user what each will create before running it.

### 3. Developer login — first-time `aws configure sso` (guide + run)
Create the SSO login profile here (interactive). The container is **headless**, so
run `aws configure sso --use-device-code` and show device-code logins with
`--use-device-code` everywhere — a plain login tries to open an absent browser. The
**recurring** `aws sso login --use-device-code` is `firesim-lab-sim`'s job.
Credentials persist via the `~/.aws` bind mount; there is no AWS CLI on the host.

### 4. Verification — run freely (read-only)
`scripts/verify-aws.sh <profile> <region>` probes: active SSO session, build role +
instance profile, run role, `iam:PassRole` grant, key pair in region, **F2 quota
> 0** (surfacing a pending request), and an FPGA Developer AMI for the region. Each
gap prints its layer + remediation.

## Record in the stamp

Update the `aws` block: `intent: "f2"`, `developer_kind`, `provisioned`
(`true` once roles+keypair+passrole verify, `false` if gaps remain, `"skipped"`
for metasim-only), `sso_profile_configured`, `profile_name`, `region`. Live
readiness is always re-probed by `firesim-lab-sim` — the stamp records intent and
the one-time provisioning outcome, not a credential.
