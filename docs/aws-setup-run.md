# AWS Setup for fslab F2 Runs

Companion to [aws-setup.md](aws-setup.md), which covers the build host.
This is a one-time-per-AWS-account setup for the **run host** — the
EC2 instance `fslab sim fpga` launches (or attaches to) so the driver
can program the FPGA and execute the simulation.

> **Why a separate role.** The build host needs S3 write +
> `CreateFpgaImage` (it builds the bitstream and registers the AFI).
> The run host needs neither — it only needs to *load* an already-
> registered AGFI. The narrower role limits blast radius and makes it
> safer to attach to long-running spot instances.

## What the run host actually does with AWS

`sudo fpga-load-local-image -S <slot> -I <agfi> -A` calls into the AWS
EC2 FPGA service from inside the instance to fetch the AGFI manifest
and verify entitlement. That's the only reason the run host touches
AWS at all. No S3, no `CreateFpgaImage`, no AFI registration.

## Prerequisites

Same as [aws-setup.md](aws-setup.md): a profile with IAM-write
permissions for the admin doing the setup. The profile you launch
`fslab sim fpga` with does NOT need any of these IAM permissions —
the role created below runs *inside* the run instance.

## CLI conventions

Same as [aws-setup.md](aws-setup.md). Export `$ADMIN_PROFILE` once:

```bash
ADMIN_PROFILE=<admin-profile>   # e.g. fslab-admin
```

## Step 1 — Trust policy

Identical to the build-side trust policy — EC2 service principal
assumes the role on behalf of an instance. If you already created
`/tmp/fslab-trust-policy.json` while following aws-setup.md you can
reuse that file.

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

## Step 2 — Permissions policy

The minimum permissions the run wrapper needs:

  * **EC2 FPGA describe + associate** so `fpga-load-local-image` can
    fetch the AGFI manifest and verify entitlement.

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
```

That's the entire policy. Notably absent compared to the build role:

  * No S3 actions.
  * No `CreateFpgaImage`.
  * No `sts:GetCallerIdentity` — the run wrapper does not need to
    resolve the account id (no userid-region bucket suffix on the run
    side).

If a future feature uploads results to S3 from the run host, extend
the policy at that point.

## Step 3 — Create the role and instance profile

```bash
aws iam create-role \
    --role-name fslab-fpga-runner \
    --assume-role-policy-document file:///tmp/fslab-trust-policy.json \
    --profile "$ADMIN_PROFILE"

aws iam put-role-policy \
    --role-name fslab-fpga-runner \
    --policy-name FiresimLabRunHostAccess \
    --policy-document file:///tmp/fslab-run-permissions.json \
    --profile "$ADMIN_PROFILE"

aws iam create-instance-profile \
    --instance-profile-name fslab-fpga-runner \
    --profile "$ADMIN_PROFILE"

aws iam add-role-to-instance-profile \
    --instance-profile-name fslab-fpga-runner \
    --role-name fslab-fpga-runner \
    --profile "$ADMIN_PROFILE"
```

You can pick a different name — the instance profile name is what
goes in `target.run.host.iam_instance_profile` in fslab.yaml.

## Step 4 — Reference the profile in fslab.yaml

```yaml
target:
  run:
    host:
      type: ec2_launch
      region: us-west-2
      aws_profile: fslab-run                  # your local SSO profile
      iam_instance_profile: fslab-fpga-runner # the profile created above
      lifecycle: spot_one_time
      ami_id: ami-082c5db2375456e1a           # FPGA Developer AMI
      instance_type: f2.4xlarge               # FPGA-attached instance
      ssh_key: ~/.ssh/fslab_ed25519
      ssh_user: ubuntu
      key_name: firesim-lab
    artifact_source:
      type: aws_afi
      agfi: agfi-0123456789abcdef0
```

## Cross-region AGFIs

`fpga-load-local-image` can only load an AGFI that has been registered
in (or replicated to) the instance's region. If your build registered
the AFI in `us-east-1` and your run host is in `us-west-2`, set
`target.build.publish.copy_to_regions: [us-west-2]` and rebuild, or
pin the run host to the original build region.

## Troubleshooting

  * **`AccessDeniedException` on `fpga-load-local-image`** — the
    instance profile is missing from the run instance, or the role
    lacks `ec2:AssociateFpgaImage`. Verify with (from inside the run
    instance):
    ```bash
    aws sts get-caller-identity
    aws ec2 describe-fpga-images --owners self --region "$REGION"
    ```
    The first should return the run role's caller arn; the second
    should not error.

  * **`InvalidFpgaImageID.NotFound`** — the AGFI exists but not in
    this region. Replicate it (see "Cross-region AGFIs" above) or run
    in the region where the AFI was originally registered.

  * **`fpga-load-local-image: command not found`** — the AMI is not an
    FPGA Developer AMI. Switch to the official AWS-FPGA AMI or install
    `aws-fpga` on a custom AMI.

## See also

  * [aws-setup.md](aws-setup.md) — build-host role.
  * [run-pipeline-guide.md](run-pipeline-guide.md) — end-to-end
    `fslab sim fpga` workflow.
