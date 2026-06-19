# firesim-lab AWS Setup

This page creates the AWS resources fslab expects before the FPGA path works: the F2 service-quota increase, a region choice, an SSH key pair, and the two IAM **instance-profile roles** fslab attaches to the EC2 instances it launches — one for the **build host** (`fslab build fpga`) and one for the **run host** (`fslab sim fpga`). It is a one-time-per-account setup.

It pairs with {doc}`identity-center-sso`, which sets up your login identity and grants it `iam:PassRole` on the build role created here. If you are using Identity Center, do that page first or alongside this one.

Nothing here is needed for desktop metasimulation.

:::{note}
**Solo developer vs org / DevOps admin.** A solo developer on a personal account runs every command here themselves under their own admin identity. In an org, an admin typically creates the roles on a developer's behalf because the developer's `FireSim-Developer` identity intentionally lacks `iam:CreateRole` / `iam:CreateInstanceProfile`. Both paths produce the same two roles; the org path is covered in {ref}`Variant A <fslab-variant-a>` (developer is a traditional IAM role) and in {doc}`identity-center-sso` (developer logs in through a permission set).
:::

## Prerequisites

- **firesim-lab installed and its container running.** Run every command on this page **inside the firesim-lab container** (see {doc}`/installation/index`). The container ships the AWS CLI v2 and a complete, consistent environment; running the commands on your bare host — especially on Windows — risks a missing or differently-configured AWS CLI, absent environment variables, and path differences. The container sidesteps all of that.
- An identity with **IAM-write** permissions (typically your developer-admin profile) authenticated to the AWS CLI *inside the container*. The profile you later launch fslab *builds* with does **not** need IAM-write — only the EC2 launch/describe permissions from the [AWS-FPGA developer guide](https://github.com/aws/aws-fpga). The roles created below are what run *inside* the EC2 instances.
- An account with the root user secured and a billing budget in place — see {doc}`aws-primer`.

## CLI conventions

Run all commands from a shell **inside the firesim-lab container**, where the AWS CLI and environment are already set up. Every `aws ...` command below ends with `--profile $ADMIN_PROFILE`. Set it once:

```bash
ADMIN_PROFILE=<admin-profile>   # e.g. fslab-admin
```

Use the SSO profile name of the admin running the commands — the one you `aws sso login --profile <name>` against. Modern SSO setups typically have no default profile, so omitting `--profile` fails with `Unable to locate credentials`.

A few commands need the 12-digit account ID. Capture it once:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity \
    --query Account --output text --profile $ADMIN_PROFILE)
```

Shell variables live only in the terminal where they were set — re-export them in a new shell. Commands shown **inside an instance** (Troubleshooting) deliberately omit `--profile`: they use the instance profile attached to the EC2 instance.

## Step 1 — Request the F2 service quota

Only the **run host** is an FPGA instance. The bitstream **build host** is an ordinary compute instance (the registry default is `z1d.2xlarge` — 8 vCPU, 64 GB — which runs Vivado synthesis; an FPGA slot is not needed to build). The two hosts therefore draw on different quotas:

- **Run host (F2).** Governed by the **"Running On-Demand F instances"** quota, measured in vCPUs, with a default of **0** — no F2 instance will launch until you raise it.
- **Build host (standard).** Governed by the **"Running On-Demand Standard instances"** quota, which usually has a non-zero default but may still need a bump to cover `z1d.2xlarge` (8 vCPU).

To raise the F2 quota:

1. Open **Service Quotas → AWS services → Amazon Elastic Compute Cloud (Amazon EC2)**.
2. Find **Running On-Demand F instances** and choose *Request increase at account level*.
3. Request enough vCPUs for the size you intend to run: **24** for `f2.6xlarge` (1 FPGA), **48** for `f2.12xlarge` (4 FPGAs), or **192** for `f2.48xlarge` (8 FPGAs). Request per region.

Approval is sometimes automatic and sometimes takes a business day or two; request early.

## Step 2 — Choose a region

F2 instances exist only in a subset of regions. As of this writing: US East (N. Virginia, `us-east-1`), US West (Oregon, `us-west-2`), Canada Central, Europe (Frankfurt, London), and Asia Pacific (Sydney, Tokyo, Seoul). The smallest `f2.6xlarge` size is currently in `us-east-1`, `us-west-2`, and London only.

Strictly speaking only the run host is constrained to an F2 region; the build host could run elsewhere. But keeping both in one region keeps the key pair, the AMI, and the registered FPGA image (AGFI) all in the same place. Pick one region and use it for the quota request, the key pair, the build host, and the run host. The examples below use `us-west-2`.

:::{warning}
FPGA images are region-scoped. An AGFI registered in one region cannot be loaded from an instance in another unless you replicate it — see {ref}`Cross-region AGFIs <fslab-cross-region>`. Keeping build and run in the same region avoids this entirely.
:::

## Step 3 — Create an SSH key pair

fslab connects to the launched instance over SSH. Create an EC2 key pair in your chosen region:

```bash
aws ec2 create-key-pair \
    --key-name firesim-lab \
    --key-type ed25519 \
    --region us-west-2 \
    --query 'KeyMaterial' --output text \
    --profile $ADMIN_PROFILE > ~/.ssh/fslab_ed25519
chmod 600 ~/.ssh/fslab_ed25519
```

`--key-type ed25519` makes the key match the `fslab_ed25519` filename (the
`create-key-pair` default is RSA). The `--key-name` value (`firesim-lab`) maps to `key_name:` in `fslab.yaml`; the saved private-key path maps to `ssh_key:`. The account's **default VPC** is sufficient for fslab — you only need the instance reachable over inbound SSH (port 22), which the default security group permits from within the VPC; widen it to your workstation's IP if you launch into a security group that does not already allow SSH.

### Alternative — generate the key locally and import it

If you would rather hold the private key from the outset (it never transits AWS) or already have an SSH key you use, generate one locally and import only the **public** half:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/fslab_ed25519        # if you don't already have a key
aws ec2 import-key-pair \
    --key-name firesim-lab \
    --public-key-material fileb://~/.ssh/fslab_ed25519.pub \
    --region us-west-2 \
    --profile $ADMIN_PROFILE
```

Either path leaves you with the same two `fslab.yaml` values: the EC2 key-pair name (`key_name:`) and the local private-key path (`ssh_key:`).

:::{note}
This key pair is for the `ec2_launch` host model, where AWS installs the public key on the instance at launch. For `host.type: external` — a host you provision yourself — there is **no** `key_name`; you install your public key in the host's `~/.ssh/authorized_keys` directly and point `ssh_key:` at the matching private key. See {doc}`/setup/external-host`.
:::

## Step 4 — Build-host instance-profile role

The build host needs S3 (to stage the design checkpoint) and EC2 FPGA (to register the AFI). These permissions are carried by an IAM role, attached to the instance through an instance profile.

### 4a — Trust policy

This declares that the EC2 service may assume the role on behalf of an instance:

```bash
cat > /tmp/fslab-trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
```

### 4b — Permissions policy

The minimum the F2 build wrapper needs: **S3** to create the DCP-staging bucket (if missing), upload the tarball, and let `create-fpga-image` read it back; **EC2 FPGA** to submit `create-fpga-image` and inspect AFI state; and `sts:GetCallerIdentity` for the optional userid-region bucket-naming convention.

```bash
cat > /tmp/fslab-permissions.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3DcpStaging",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:HeadBucket",
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Ec2Fpga",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateFpgaImage",
        "ec2:DescribeFpgaImages"
      ],
      "Resource": "*"
    },
    {
      "Sid": "StsIdentity",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
EOF
```

:::{note}
**On least privilege.** Every policy in this guide uses `"Resource": "*"` for readability — it keeps the JSON short and lets you get a first build working without resource-naming friction. That is broader than a production deployment should run with. The principle of least privilege says an identity should hold only the permissions it actually uses, scoped to the specific resources it touches: in a hardened setup you would constrain the S3 actions to your build bucket's ARN (e.g. `arn:aws:s3:::my-firesim-builds/*`) and, where the service supports it, the EC2 FPGA actions to specific image ARNs. We keep full access here for simplicity; tighten it once your pipeline is stable.
:::

### 4c — Create the role and instance profile

```bash
aws iam create-role \
    --role-name fslab-fpga-builder \
    --assume-role-policy-document file:///tmp/fslab-trust-policy.json \
    --profile $ADMIN_PROFILE

aws iam put-role-policy \
    --role-name fslab-fpga-builder \
    --policy-name FiresimLabBuildHostAccess \
    --policy-document file:///tmp/fslab-permissions.json \
    --profile $ADMIN_PROFILE

aws iam create-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE

aws iam add-role-to-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --role-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE
```

The instance profile is a distinct object from the role: EC2 references the *profile* at launch, and the profile wraps the role. The names need not match, but keeping both `fslab-fpga-builder` is simplest. The instance-profile name is what goes in `iam_instance_profile:` in `fslab.yaml`.

(fslab-variant-a)=
### 4a' — Variant: attach to an existing IAM role

If your site issues developers a pre-provisioned **traditional IAM role** (e.g. `FireSim-Developer`) rather than creating a fresh role, attach the Step 4b permissions to it and wrap it in an instance profile instead of creating `fslab-fpga-builder`:

```bash
# Ensure ec2.amazonaws.com is a trusted principal. update-assume-role-policy
# REPLACES the trust policy in full — first inspect the current one and merge,
# never apply the bare Step 4a policy alone or you wipe existing trust.
aws iam get-role --role-name FireSim-Developer \
    --query 'Role.AssumeRolePolicyDocument' --profile $ADMIN_PROFILE

aws iam put-role-policy \
    --role-name FireSim-Developer \
    --policy-name FiresimLabBuildHostAccess \
    --policy-document file:///tmp/fslab-permissions.json \
    --profile $ADMIN_PROFILE

aws iam create-instance-profile \
    --instance-profile-name fslab-fpga-builder --profile $ADMIN_PROFILE
aws iam add-role-to-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --role-name FireSim-Developer --profile $ADMIN_PROFILE
```

:::{warning}
If `FireSim-Developer` is an **Identity Center permission set** (an `AWSReservedSSO_*` role), this variant does **not** apply — those roles cannot back an EC2 instance profile and their trust policy is overwritten on sync. Create `fslab-fpga-builder` as in Step 4c and grant the permission set `iam:PassRole` instead — see {doc}`identity-center-sso`.
:::

## Step 5 — Run-host instance-profile role

The run host does far less with AWS: `fpga-load-local-image` fetches the AGFI manifest and verifies entitlement, and that is all. No S3, no `CreateFpgaImage`. Give it a narrower role — the smaller blast radius is safer for long-running spot instances.

```bash
cat > /tmp/fslab-run-permissions.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Ec2FpgaLoad",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeFpgaImages",
        "ec2:AssociateFpgaImage"
      ],
      "Resource": "*"
    }
  ]
}
EOF

# Reuse the Step 4a trust policy — the EC2 service principal is identical.
aws iam create-role \
    --role-name fslab-fpga-runner \
    --assume-role-policy-document file:///tmp/fslab-trust-policy.json \
    --profile $ADMIN_PROFILE

aws iam put-role-policy \
    --role-name fslab-fpga-runner \
    --policy-name FiresimLabRunHostAccess \
    --policy-document file:///tmp/fslab-run-permissions.json \
    --profile $ADMIN_PROFILE

aws iam create-instance-profile \
    --instance-profile-name fslab-fpga-runner --profile $ADMIN_PROFILE
aws iam add-role-to-instance-profile \
    --instance-profile-name fslab-fpga-runner \
    --role-name fslab-fpga-runner --profile $ADMIN_PROFILE
```

## Step 6 — Reference the roles in `fslab.yaml`

In your project's `fslab.yaml`, set the instance-profile names. The build host:

```yaml
target:
  build:
    host:
      type: ec2_launch
      region: us-west-2
      iam_instance_profile: fslab-fpga-builder    # REQUIRED — Step 4
      instance_type: z1d.2xlarge                  # compute host, no FPGA slot
      ami_id: ami-0123456789abcdef0               # FPGA Developer AMI in this region
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: centos
      key_name: firesim-lab
```

The run host (see {doc}`/commands/sim-fpga` for the full run block):

```yaml
target:
  run:
    host:
      type: ec2_launch
      region: us-west-2
      aws_profile: fslab-dev                    # your SSO login profile
      iam_instance_profile: fslab-fpga-runner   # created in Step 5
      lifecycle: spot_one_time
      ami_id: ami-0123456789abcdef0             # FPGA Developer AMI in this region
      instance_type: f2.6xlarge                 # 1 FPGA slot
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: centos
      key_name: firesim-lab
    artifact_source:
      type: aws_afi
      agfi: agfi-0123456789abcdef0
```

For `type: external` (you manage the EC2 instance yourself), attach the instance profile to the instance directly:

```bash
aws ec2 associate-iam-instance-profile \
    --instance-id i-0123456789abcdef0 \
    --iam-instance-profile Name=fslab-fpga-builder \
    --profile $ADMIN_PROFILE
```

:::{note}
AMI IDs are region-specific. Look up the current **FPGA Developer AMI** in your region from the AWS Marketplace rather than copying an ID from another region. The FPGA Developer AMI ships Vivado, AWS CLI v2, and the `aws-fpga` tooling (`fpga-load-local-image`); a stock AMI lacks all three.
:::

(fslab-cross-region)=
## Cross-region AGFIs

`fpga-load-local-image` can only load an AGFI registered in (or replicated to) the run instance's region. If your build registered the AFI in `us-east-1` and your run host is in `us-west-2`, either set `target.build.publish.copy_to_regions: [us-west-2]` and rebuild, or pin the run host to the build region.

## Step 7 — Sanity-check

```bash
aws iam get-instance-profile \
    --instance-profile-name fslab-fpga-builder --profile $ADMIN_PROFILE
aws iam list-role-policies \
    --role-name fslab-fpga-builder --profile $ADMIN_PROFILE
aws iam get-role-policy \
    --role-name fslab-fpga-builder \
    --policy-name FiresimLabBuildHostAccess --profile $ADMIN_PROFILE
```

## Troubleshooting

`validate_remote_auth` fails immediately after launch
: The build host cannot reach AWS credentials. Common causes: an `iam_instance_profile` typo in `fslab.yaml` (does it match `aws iam list-instance-profiles --profile $ADMIN_PROFILE`?); a stock AMI without the AWS CLI (use the FPGA Developer AMI or install `awscli`); or an imported AMI whose metadata-service hook was scrubbed (the FPGA Developer AMI is configured correctly out of the box).

`create-fpga-image` returns `AccessDenied`
: The role lacks `ec2:CreateFpgaImage`, or the wrong role is attached. From inside the build instance (these run under the instance profile — **no** `--profile`):
: ```bash
  aws sts get-caller-identity
  aws ec2 describe-fpga-images --max-results 1
  ```
: If `get-caller-identity` returns a role ARN that does not end in `fslab-fpga-builder/...`, the wrong profile was attached — re-launch with the correct `fslab.yaml` name.

S3 upload returns `AccessDenied`
: Usually `s3:PutObject` is missing or scoped too narrowly. The policy above uses `"Resource": "*"` for simplicity; tighten to your bucket if you prefer, e.g. `arn:aws:s3:::my-firesim-builds/*` (and `-*` to cover `append_userid_region` bucket names).

`fpga-load-local-image: command not found` (run host)
: The AMI is not an FPGA Developer AMI. Switch to the official AWS-FPGA AMI or install `aws-fpga` on a custom AMI.

`InvalidFpgaImageID.NotFound` (run host)
: The AGFI exists but not in this region. Replicate it (see {ref}`Cross-region AGFIs <fslab-cross-region>`) or run in the region where the AFI was registered.

## Tearing down

```bash
# Build role (repeat with the -runner names for the run role).
aws iam remove-role-from-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --role-name fslab-fpga-builder --profile $ADMIN_PROFILE
aws iam delete-instance-profile \
    --instance-profile-name fslab-fpga-builder --profile $ADMIN_PROFILE
aws iam delete-role-policy \
    --role-name fslab-fpga-builder \
    --policy-name FiresimLabBuildHostAccess --profile $ADMIN_PROFILE
aws iam delete-role \
    --role-name fslab-fpga-builder --profile $ADMIN_PROFILE
```

## Next

With the quota approved and both roles in place, head to {doc}`/commands/build` to build a bitstream on F2 and {doc}`/commands/sim-fpga` to run it.
