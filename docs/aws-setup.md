# AWS Setup for fslab F2 Background Builds

This is a one-time-per-AWS-account setup. Once the IAM role and
instance profile below exist, every `fslab build fpga` against an F2
target inherits AWS credentials on the build host via the instance
profile — no SSO session, no expiring tokens from the workload's
perspective.

> **Why this matters.** The remote bitstream build can take ~90 minutes,
> after which the wrapper script does an S3 upload and submits
> `create-fpga-image`. If those calls used the user's local SSO
> credentials (forwarded over SSH), they would die at the 8-hour SSO
> session boundary — even when the build started well within the
> session. Instance-profile auth is the supported pattern for
> EC2-hosted workloads and eliminates that failure mode entirely.

## Prerequisites

  * AWS CLI installed and authenticated as a user with IAM-write
    permissions (typically your developer-admin profile).
  * The profile you launch fslab builds with does NOT need IAM-write
    permissions — only the launch + describe permissions covered by the
    [AWS-FPGA developer guide](https://github.com/aws/aws-fpga). The
    role created below is what runs *inside* the build instance.

## CLI conventions

All `aws ...` commands in this guide end with `--profile $ADMIN_PROFILE`.
Set `$ADMIN_PROFILE` once before running anything else:

```bash
ADMIN_PROFILE=<admin-profile>   # e.g. fslab-admin
```

Use the SSO profile name of the admin running the commands — the one
you `aws sso login --profile <name>` against. Modern SSO setups
typically do not configure a default profile, so omitting `--profile`
would otherwise fail with `Unable to locate credentials`.

A handful of additional shell variables (`$ACCOUNT_ID`,
`$INSTANCE_REGION`, `$SSO_INSTANCE_ARN`, `$PERMISSION_SET_ARN`) are
introduced in dedicated *Helper* subsections at the points where
they are first needed. All shell variables in this guide live only
in the terminal where they were exported — re-run the relevant
capture block if you open a new shell.

Commands shown **inside the build instance** (Troubleshooting
section) deliberately omit `--profile` — they use the instance
profile attached to the EC2 instance.

## Step 1 — Trust policy

This declares that the EC2 service is allowed to assume the role on
behalf of an instance.

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

These are the minimum permissions the F2 wrapper script needs:

  * **S3** to create the DCP-staging bucket (if missing), upload the
    DCP tarball, and let `create-fpga-image` read it back.
  * **EC2 FPGA** to submit `create-fpga-image` and inspect AFI state.

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

If you use the optional `publish.append_userid_region` convention
(bucket name suffixed with `-<userid>-<region>`), the
`sts:GetCallerIdentity` permission above lets the wrapper resolve the
account id from inside the instance.

## Admin-assisted setup: granting fslab capability to a pre-provisioned developer identity

Many sites operate with a pre-provisioned developer identity (e.g. a
role or permission set named `FireSim-Developer`) that intentionally
lacks `iam:CreatePolicy`, `iam:CreateRole`, and
`iam:CreateInstanceProfile`. In those environments the developer
cannot self-serve [Step 3](#step-3--create-role--instance-profile);
an admin must do it on their behalf.

How the admin actually does this depends on **what kind of identity
`FireSim-Developer` is**:

  * **Variant A — Traditional IAM role.** A regular IAM role that the
    developer assumes (e.g. via `sts:AssumeRole`, a SAML federation
    you control yourself, or a long-lived workload identity).
  * **Variant B — AWS Identity Center (formerly AWS SSO) permission
    set.** The role surfaced in the account as
    `AWSReservedSSO_FireSim-Developer_<suffix>` and managed by
    Identity Center.

Pick the variant that matches your environment, then return to
[Step 4](#step-4--reference-it-in-your-project). The variant you
choose replaces — or, for Variant B, supplements —
[Step 3](#step-3--create-role--instance-profile).

> Throughout this section, the example name `FireSim-Developer` is
> illustrative; substitute your site's actual role / permission set
> name.

### Helper: capture the AWS account ID

A few of the commands below need the 12-digit AWS account ID (in
policy ARNs and `provision-permission-set` targets). Capture it once
into a shell variable and the rest of the section can reference
`$ACCOUNT_ID`:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity \
    --query Account \
    --output text \
    --profile $ADMIN_PROFILE)
```

> If you open a new shell partway through this section, re-run the
> line above before continuing — `$ACCOUNT_ID` only lives in the
> shell where it was set.

### Variant A — Traditional IAM role

This variant attaches the Step 2 permissions directly to the existing
`FireSim-Developer` IAM role and creates an instance profile that
wraps it. It is the in-place equivalent of
[Step 3](#step-3--create-role--instance-profile).

#### A1 — Verify (and if needed update) the trust policy

The role must allow `ec2.amazonaws.com` to assume it; otherwise an
EC2 instance launched with the instance profile cannot pick up
credentials. Inspect the current trust policy first:

```bash
aws iam get-role --role-name FireSim-Developer \
    --query 'Role.AssumeRolePolicyDocument' \
    --profile $ADMIN_PROFILE
```

If `ec2.amazonaws.com` is **not** already a trusted principal, add
it. `update-assume-role-policy` **replaces** the trust policy in
full, so the admin must merge with the current document — never apply
the bare Step 1 trust policy by itself, or pre-existing trust
relationships (developer SSO, federated identities, cross-account
trust) will be wiped out.

```bash
# Edit /tmp/firesim-developer-trust-policy.json so that its
# Statement array contains BOTH the existing statements AND the
# EC2 service principal block below, then run update-assume-role-policy.
cat > /tmp/firesim-developer-trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2InstanceAssumeRole",
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
    /* merge any pre-existing statements here, taken from
       `aws iam get-role` output above */
  ]
}
EOF

aws iam update-assume-role-policy \
    --role-name FireSim-Developer \
    --policy-document file:///tmp/firesim-developer-trust-policy.json \
    --profile $ADMIN_PROFILE
```

#### A2 — Attach the Step 2 permissions to the role

Two equivalent options — pick one based on your organization's
policy-governance practice:

**Option 1 — Inline policy** (simplest, consistent with the rest of
this guide):

```bash
aws iam put-role-policy \
    --role-name FireSim-Developer \
    --policy-name FiresimLabBuildHostAccess \
    --policy-document file:///tmp/fslab-permissions.json \
    --profile $ADMIN_PROFILE
```

**Option 2 — Customer-managed policy** (reusable across multiple
roles; better fit if you have policy-governance / audit requirements):

```bash
aws iam create-policy \
    --policy-name FiresimLabBuildHostAccess \
    --policy-document file:///tmp/fslab-permissions.json \
    --profile $ADMIN_PROFILE

# create-policy returns the policy ARN — feed it into attach-role-policy:
aws iam attach-role-policy \
    --role-name FireSim-Developer \
    --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/FiresimLabBuildHostAccess \
    --profile $ADMIN_PROFILE
```

#### A3 — Create the instance profile and attach the role

The instance profile is a distinct AWS object from the role; EC2
references the *profile* at launch, and the profile contains a
reference to the role. The names do not have to match:

```bash
aws iam create-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE

aws iam add-role-to-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --role-name FireSim-Developer \
    --profile $ADMIN_PROFILE
```

> The instance-profile name is what fslab references via
> `iam_instance_profile:` in `fslab.yaml`. Pick any name you like; the
> example above keeps `fslab-fpga-builder` for consistency with the
> rest of this guide.

#### A4 — Hand off to the developer

The developer now sets the instance-profile name in their
`fslab.yaml` and proceeds from
[Step 4](#step-4--reference-it-in-your-project). No further IAM-write
permission is needed from them.

### Variant B — AWS Identity Center permission set

If `FireSim-Developer` is an Identity Center permission set, the
flow above does **not** apply, and attempting it will fail or be
silently reverted. Specifically:

  * Identity Center provisions the role into the account as
    `AWSReservedSSO_FireSim-Developer_<hex-suffix>`. The suffix is
    not stable across re-provisioning, and the role's trust policy
    is owned by Identity Center — manual edits via
    `update-assume-role-policy` are reverted on the next sync.
  * `AWSReservedSSO_*` roles are federation-only by design; AWS does
    not support using them as the role behind an EC2 instance
    profile.

The correct pattern is therefore: the **EC2 instance profile role is
still a regular IAM role** that the admin creates exactly as in
[Step 3](#step-3--create-role--instance-profile) (i.e.
`fslab-fpga-builder`). The Identity Center permission set is *not*
the runtime credential carrier — it is only the launchpad. To make
the developer able to launch an EC2 instance and attach
`fslab-fpga-builder` to it, the admin adds a narrow PassRole policy
to the permission set.

#### Helper: capture the Identity Center region and ARNs

The CLI commands in B2 below need three Identity Center identifiers:
the home region of your Identity Center instance, the SSO
**instance ARN**, and the `FireSim-Developer` **permission set ARN**.
Capture them into shell variables once and the rest of Variant B can
reference `$INSTANCE_REGION`, `$SSO_INSTANCE_ARN`, and
`$PERMISSION_SET_ARN`. `$ADMIN_PROFILE` from
[CLI conventions](#cli-conventions) is also assumed to be set.

`sso-admin` is a regional service: every command targets a specific
region, and if your AWS CLI default region isn't already set to your
Identity Center's home region the command fails with a "must specify
region" error. The `$INSTANCE_REGION` variable makes the region
explicit on each call:

```bash
INSTANCE_REGION=<instance-region>   # e.g. us-east-1

SSO_INSTANCE_ARN=$(aws sso-admin list-instances \
    --query 'Instances[0].InstanceArn' \
    --output text \
    --profile $ADMIN_PROFILE \
    --region $INSTANCE_REGION)

# Identity Center exposes no list-by-name API for permission sets,
# so enumerate the ARNs and match on the Name field.
PERMISSION_SET_ARN=$(
    for arn in $(aws sso-admin list-permission-sets \
            --instance-arn "$SSO_INSTANCE_ARN" \
            --query 'PermissionSets[]' \
            --output text \
            --profile $ADMIN_PROFILE \
            --region $INSTANCE_REGION); do
        name=$(aws sso-admin describe-permission-set \
            --instance-arn "$SSO_INSTANCE_ARN" \
            --permission-set-arn "$arn" \
            --query 'PermissionSet.Name' \
            --output text \
            --profile $ADMIN_PROFILE \
            --region $INSTANCE_REGION)
        [ "$name" = "FireSim-Developer" ] && echo "$arn" && break
    done)

echo "Instance region: $INSTANCE_REGION"
echo "SSO instance:    $SSO_INSTANCE_ARN"
echo "Permission set:  $PERMISSION_SET_ARN"
```

If `$PERMISSION_SET_ARN` prints empty, the `FireSim-Developer`
permission set either doesn't exist or isn't visible to your admin
profile — resolve that before proceeding.

> Same lifetime caveat as `$ACCOUNT_ID`: these variables only live
> in the current shell. Re-run this block if you open a new
> terminal.

**Console alternative.** If you'd rather look them up by hand:
Identity Center → *Permission sets* → `FireSim-Developer` → copy
the *Permission set ARN* from the details panel; Identity Center →
*Settings* → copy the *Instance ARN*. Substitute the literal ARNs
into the B2 commands instead of `$SSO_INSTANCE_ARN` /
`$PERMISSION_SET_ARN`.

#### B1 — Complete Step 3 as written

Have the admin run [Step 3](#step-3--create-role--instance-profile)
unmodified. This produces:

  * IAM role `fslab-fpga-builder` (trusted by `ec2.amazonaws.com`,
    carrying the Step 2 permissions).
  * Instance profile `fslab-fpga-builder`.

#### B2 — Grant the permission set rights to launch with the role

This grants the minimum needed to launch an EC2 instance with the
`fslab-fpga-builder` instance profile attached at launch time
(`type: ec2_launch` host mode). Two approaches are available;
**customer-managed is recommended for fslab** because it groups all
fslab developer grants under one named, version-controlled policy
that can grow as new fslab roles or permissions are added — and is
reusable if you later add a second permission set (e.g. an admin
variant).

The JSON below is referenced by both approaches:

```bash
cat > /tmp/firesim-lab-developer-launch.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PassFslabFpgaBuilderRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/fslab-fpga-builder",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "ec2.amazonaws.com"
        }
      }
    },
    {
      "Sid": "DescribeInstanceProfileForValidation",
      "Effect": "Allow",
      "Action": [
        "iam:GetInstanceProfile",
        "iam:ListInstanceProfiles"
      ],
      "Resource": "*"
    }
  ]
}
EOF
```

The condition key `iam:PassedToService` constrains PassRole to EC2
specifically — without it, the permission set could pass
`fslab-fpga-builder` to any service, which is broader than needed.
The `iam:Get*`/`iam:List*` block is optional but lets fslab
pre-validate the instance profile before the RunInstances call.

##### Primary: customer-managed policy

Step 1 — create the policy in the AWS account:

```bash
aws iam create-policy \
    --policy-name FiresimLabDeveloperAccess \
    --policy-document file:///tmp/firesim-lab-developer-launch.json \
    --profile $ADMIN_PROFILE
```

As fslab evolves, extend **this same policy** with additional
statements (for example, PassRole for a future fslab-* role, or
narrower RunInstances conditions) rather than attaching a second
policy. That keeps all fslab permission-set grants under one named
object.

Step 2 — reference the policy from the permission set **by name**.
Identity Center attaches customer-managed policies by name (not by
ARN); at provisioning time it looks up a policy with this name in
each target AWS account. Two options:

  * **Console.** Identity Center → Permission sets →
    `FireSim-Developer` → *AWS managed and customer-managed
    policies* → *Attach customer-managed policies* → enter the name
    `FiresimLabDeveloperAccess`.
  * **CLI.**

    ```bash
    aws sso-admin attach-customer-managed-policy-reference-to-permission-set \
        --instance-arn $SSO_INSTANCE_ARN \
        --permission-set-arn $PERMISSION_SET_ARN \
        --customer-managed-policy-reference Name=FiresimLabDeveloperAccess,Path=/ \
        --profile $ADMIN_PROFILE \
        --region $INSTANCE_REGION
    ```

Step 3 — re-provision the permission set so the new reference takes
effect in the target account:

```bash
aws sso-admin provision-permission-set \
    --instance-arn $SSO_INSTANCE_ARN \
    --permission-set-arn $PERMISSION_SET_ARN \
    --target-type AWS_ACCOUNT \
    --target-id ${ACCOUNT_ID} \
    --profile $ADMIN_PROFILE \
    --region $INSTANCE_REGION
```

> **Multi-account note (informational).** If `FireSim-Developer` is
> later assigned to additional AWS accounts, the same-named
> `FiresimLabDeveloperAccess` policy must exist in each new
> account *before* you re-provision the permission set there,
> otherwise provisioning fails with `PolicyNotFound`. Typical
> tooling: CloudFormation StackSets or Terraform. For a
> single-account Identity Center setup this is a non-issue.

##### Alternative: inline policy on the permission set

If you'd rather embed the JSON directly into the permission set and
skip having a separate IAM policy object to manage, attach it as an
inline policy on `FireSim-Developer`:

  * **Console.** Identity Center → Permission sets →
    `FireSim-Developer` → *Inline policy* → paste the JSON.
  * **CLI.**

    ```bash
    aws sso-admin put-inline-policy-to-permission-set \
        --instance-arn $SSO_INSTANCE_ARN \
        --permission-set-arn $PERMISSION_SET_ARN \
        --inline-policy file:///tmp/firesim-lab-developer-launch.json \
        --profile $ADMIN_PROFILE \
        --region $INSTANCE_REGION

    aws sso-admin provision-permission-set \
        --instance-arn $SSO_INSTANCE_ARN \
        --permission-set-arn $PERMISSION_SET_ARN \
        --target-type AWS_ACCOUNT \
        --target-id ${ACCOUNT_ID} \
        --profile $ADMIN_PROFILE \
        --region $INSTANCE_REGION
    ```

Tradeoffs vs. customer-managed: the inline policy travels with the
permission set automatically and has no per-account policy lifecycle
to worry about, but it is not reusable across permission sets, and
adding more fslab-related grants over time means re-editing this
blob rather than evolving a separately-versioned named policy.

##### Closing notes (apply to both approaches)

The `ec2:RunInstances` (and supporting describe / network /
key-pair) permissions needed to actually launch the build host are
assumed to already be on the permission set per the
[Prerequisites](#prerequisites) — the AWS-FPGA developer guide
permission baseline covers them. If your `FireSim-Developer`
permission set was provisioned without those, grant them now via
your normal Identity Center permission-set workflow.

Manual edits to the `AWSReservedSSO_FireSim-Developer_*` role in the
account console will be overwritten on the next Identity Center
sync and should be avoided — always apply changes through the
permission set as shown above.

#### B3 — Hand off to the developer

The developer sets `iam_instance_profile: fslab-fpga-builder` in
their `fslab.yaml` and proceeds from
[Step 4](#step-4--reference-it-in-your-project). At
`fslab build fpga` time, the developer's Identity Center session is
used to call `ec2:RunInstances` with the instance profile attached;
once the instance boots, it picks up credentials for the
`fslab-fpga-builder` role from the instance metadata service. The
developer's Identity Center session is no longer in the credential
path for the long-running wrapper-script calls — sidestepping the
8-hour SSO boundary, as intended.

## Step 3 — Create role + instance profile

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

> The instance-profile name is what fslab references in `fslab.yaml`.
> You can pick any name you like, as long as it matches the
> `iam_instance_profile:` field. The example above uses
> `fslab-fpga-builder` consistently.

## Step 4 — Reference it in your project

In your project's `fslab.yaml`, under `target.build.host` (when
`type: ec2_launch`), set:

```yaml
target:
  build:
    host:
      type: ec2_launch
      region: us-west-2
      iam_instance_profile: fslab-fpga-builder    # REQUIRED
      # ...other host fields...
```

For `type: external` (user-managed EC2 instance), attach the same
instance profile to the EC2 instance directly via the AWS console or
`aws ec2 associate-iam-instance-profile --profile $ADMIN_PROFILE`.
fslab will surface a clear error at `validate_remote_auth` time if
the remote can't reach `aws sts get-caller-identity`.

## Step 5 — Sanity-check

```bash
# Verify the instance profile exists
aws iam get-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE

# Verify the role has the expected policy
aws iam list-role-policies \
    --role-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE
aws iam get-role-policy \
    --role-name fslab-fpga-builder \
    --policy-name FiresimLabBuildHostAccess \
    --profile $ADMIN_PROFILE
```

## Troubleshooting

### `validate_remote_auth` fails immediately after launch

The build host can't reach AWS credentials. Possible causes:

  * **`iam_instance_profile` typo in `fslab.yaml`** — does the name
    match what
    `aws iam list-instance-profiles --profile $ADMIN_PROFILE` reports?
  * **Stock AMI without the AWS CLI** — fslab's F2 wrapper invokes
    `aws sts get-caller-identity` and later `aws s3 cp`, `aws ec2 ...`.
    Use the AWS FPGA Developer AMI (which ships AWS CLI v2) or install
    `awscli` on your custom AMI.
  * **Imported AMI scrubbed the metadata-service hook** — the
    `imds-v2` requirement on the instance metadata service must allow
    the role token endpoint. The FPGA Developer AMI is configured
    correctly out of the box.

### `create-fpga-image` returns AccessDenied

The role's permissions don't include `ec2:CreateFpgaImage`, or the
role attached to the instance is not the one you expected. Verify:

```bash
# Inside the build instance (or via fslab monitor build).
# These run under the instance profile, NOT an admin SSO profile —
# do not add --profile here.
aws sts get-caller-identity
aws ec2 describe-fpga-images --max-results 1
```

If `get-caller-identity` returns a role ARN that doesn't end in
`fslab-fpga-builder/...`, the wrong profile was attached. Re-launch
with the correct name in `fslab.yaml`.

### S3 upload returns AccessDenied

Usually `s3:PutObject` is missing or scoped too narrowly. The permissions
policy above uses `"Resource": "*"` for simplicity; you can tighten this
to your specific bucket if you prefer, e.g.

```json
{
  "Effect": "Allow",
  "Action": ["s3:PutObject", "s3:GetObject"],
  "Resource": [
    "arn:aws:s3:::my-firesim-builds/*",
    "arn:aws:s3:::my-firesim-builds-*"
  ]
}
```

The `-*` suffix covers `append_userid_region: true` bucket names.

### `RunInstances` returns `UnauthorizedOperation` mentioning PassRole (Identity Center setup)

The `FireSim-Developer` permission set is missing the `iam:PassRole`
grant on the `fslab-fpga-builder` role ARN. Re-apply the policy
from Variant B step
[B2](#b2--grant-the-permission-set-rights-to-launch-with-the-role)
and re-provision the permission set into the target account, then
retry.

## Tearing it down

```bash
aws iam remove-role-from-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --role-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE

aws iam delete-instance-profile \
    --instance-profile-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE

aws iam delete-role-policy \
    --role-name fslab-fpga-builder \
    --policy-name FiresimLabBuildHostAccess \
    --profile $ADMIN_PROFILE

aws iam delete-role \
    --role-name fslab-fpga-builder \
    --profile $ADMIN_PROFILE
```

## Related

  * [background-build-monitor-handoff.md](background-build-monitor-handoff.md)
    — original design doc for the background-build flow that this
    instance-profile setup enables.
