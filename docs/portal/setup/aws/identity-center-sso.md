# AWS Identity Center & SSO

This page sets up the **login identity** you use with fslab — a federated, short-lived-credential identity through AWS IAM Identity Center (formerly AWS SSO) — and grants that identity the one fslab-specific permission it needs to launch FPGA build/run instances (`iam:PassRole`).

It does not create the IAM roles that get passed; those are the instance-profile roles in {doc}`firesim-lab-aws-setup`. The two pages meet at one point: the permission set here is granted `iam:PassRole` *on* the `fslab-fpga-builder` role created there.

## Why Identity Center rather than access keys

fslab's FPGA build runs on a remote EC2 instance and can take ~90 minutes, after which the wrapper uploads to S3 and submits `create-fpga-image`. The credentials those calls run under must outlive the whole build.

If the build host used your personal credentials forwarded over SSH, they would expire at the SSO session boundary (typically 8 hours) and the build could die mid-flight. fslab sidesteps this entirely: your Identity Center session is used only to *launch* the instance, and the instance then assumes a dedicated IAM role through an **instance profile** — credentials that the EC2 metadata service refreshes automatically and that never expire from the workload's perspective. Identity Center gives you secure, short-lived login credentials with no long-lived access keys to leak; the instance-profile role does the long-running work.

:::{note}
**Solo developer on a personal account?** Identity Center is still the recommended way to get a non-root identity with CLI access, and a single-user Identity Center instance is free and quick to set up. If you would rather use a plain IAM user with `aws configure` access keys, you can — the rest of fslab works the same — but you then own the risk of long-lived keys, and you can skip directly to the {ref}`PassRole grant <ic-passrole>` (granting it to your IAM user instead of a permission set).
:::

## Enable IAM Identity Center

In the AWS console, open **IAM Identity Center** and choose *Enable*. Identity Center is a regional service; the region you enable it in becomes its **home region** (you will need this region later, as `$INSTANCE_REGION`). If prompted, let it create the default identity source (the built-in Identity Center directory) — you can connect an external IdP later, but the built-in directory is enough to get going.

## Create a user and a group

1. **IAM Identity Center → Users → Add user.** Provide a username and email; the user receives an invitation to set a password and register MFA.
2. **IAM Identity Center → Groups → Create group**, e.g. `firesim-developers`, and add the user to it. Assigning permission sets to a group rather than an individual user scales better and is the recommended practice.

## Create the `FireSim-Developer` permission set

A **permission set** is a named bundle of permissions that Identity Center provisions into one or more AWS accounts as a federated role. Create one for fslab developers:

1. **IAM Identity Center → Permission sets → Create permission set.** Choose *Custom permission set*, name it `FireSim-Developer`, and set a session duration (the 8-hour maximum is fine).
2. Attach the permissions a developer needs to launch and manage EC2 FPGA instances. The baseline is the [AWS-FPGA developer guide](https://github.com/aws/aws-fpga) permission set: `ec2:RunInstances`, the supporting `ec2:Describe*` / network / key-pair read actions, and instance lifecycle actions (`ec2:StartInstances`, `ec2:StopInstances`, `ec2:TerminateInstances`). You can attach the AWS managed policy `AmazonEC2FullAccess` to start, then tighten later.
3. The fslab-specific `iam:PassRole` grant is added separately, below — keep it out of the base permission set so it stays easy to audit.

## Assign the permission set to your account

**IAM Identity Center → AWS accounts**, select your account, choose *Assign users or groups*, pick the `firesim-developers` group, and attach the `FireSim-Developer` permission set. Identity Center provisions a federated role named `AWSReservedSSO_FireSim-Developer_<suffix>` into the account.

## Configure the AWS CLI for SSO

Run this **inside the firesim-lab container** (see {doc}`/installation/index`), which ships the AWS CLI v2 and a consistent environment — running on the bare host, especially on Windows, risks a missing or differently-configured AWS CLI, absent environment variables, and path differences. Configure a CLI profile backed by SSO:

```bash
aws configure sso
```

Answer the prompts with your Identity Center **start URL** (shown on the Identity Center dashboard), its **home region**, and select the account and the `FireSim-Developer` permission set. Give the profile a memorable name, e.g. `fslab-dev`. From then on:

```bash
aws sso login --profile fslab-dev
aws sts get-caller-identity --profile fslab-dev   # confirm you are logged in
```

:::{note}
Modern SSO setups usually do not configure a *default* profile, so omitting `--profile` fails with `Unable to locate credentials`. Always pass `--profile fslab-dev` (or `export AWS_PROFILE=fslab-dev`). This is the profile name you put in `aws_profile:` in `fslab.yaml`.
:::

(ic-passrole)=
## Grant the permission set rights to launch with the fslab role

To launch an EC2 instance with an IAM role attached, the launching identity needs `iam:PassRole` permission on that role. fslab's build host runs under the `fslab-fpga-builder` instance-profile role (created in {doc}`firesim-lab-aws-setup`); your `FireSim-Developer` permission set must be allowed to pass it.

:::{warning}
Do **not** edit the `AWSReservedSSO_FireSim-Developer_*` role directly in the account console. Identity Center owns that role and overwrites manual edits on the next sync. Always apply changes through the permission set, as shown below. Likewise, that reserved SSO role cannot itself be used as an EC2 instance-profile role — it is federation-only by design. The instance-profile role is a separate, regular IAM role.
:::

Run these commands **inside the firesim-lab container** as well, for the same reason — the AWS CLI and environment are already set up there. This assumes the admin running them has an Identity Center admin profile; export it once (see {doc}`firesim-lab-aws-setup` for the same convention):

```bash
ADMIN_PROFILE=<admin-profile>   # e.g. fslab-admin
```

### Helper: capture the account ID and Identity Center ARNs

The commands below reference the 12-digit account ID, the SSO **instance ARN**, the `FireSim-Developer` **permission set ARN**, and the Identity Center home region. Capture them once:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity \
    --query Account --output text --profile $ADMIN_PROFILE)

INSTANCE_REGION=<instance-region>   # Identity Center home region, e.g. us-east-1

SSO_INSTANCE_ARN=$(aws sso-admin list-instances \
    --query 'Instances[0].InstanceArn' --output text \
    --profile $ADMIN_PROFILE --region $INSTANCE_REGION)

# Identity Center has no list-by-name API for permission sets,
# so enumerate the ARNs and match on the Name field.
PERMISSION_SET_ARN=$(
    for arn in $(aws sso-admin list-permission-sets \
            --instance-arn "$SSO_INSTANCE_ARN" \
            --query 'PermissionSets[]' --output text \
            --profile $ADMIN_PROFILE --region $INSTANCE_REGION); do
        name=$(aws sso-admin describe-permission-set \
            --instance-arn "$SSO_INSTANCE_ARN" \
            --permission-set-arn "$arn" \
            --query 'PermissionSet.Name' --output text \
            --profile $ADMIN_PROFILE --region $INSTANCE_REGION)
        [ "$name" = "FireSim-Developer" ] && echo "$arn" && break
    done)

echo "Account:        $ACCOUNT_ID"
echo "SSO instance:   $SSO_INSTANCE_ARN"
echo "Permission set: $PERMISSION_SET_ARN"
```

These shell variables live only in the current terminal — re-run the block if you open a new shell. If `$PERMISSION_SET_ARN` prints empty, the permission set either does not exist or is not visible to your admin profile; resolve that first.

`sso-admin` is regional: every command targets `--region $INSTANCE_REGION`, and omitting it fails with a "must specify region" error if your CLI default region differs from the Identity Center home region.

### The PassRole policy

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
        "StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}
      }
    },
    {
      "Sid": "DescribeInstanceProfileForValidation",
      "Effect": "Allow",
      "Action": ["iam:GetInstanceProfile", "iam:ListInstanceProfiles"],
      "Resource": "*"
    }
  ]
}
EOF
```

The `iam:PassedToService` condition constrains PassRole to EC2 specifically — without it the permission set could pass `fslab-fpga-builder` to any service, which is broader than needed. The `iam:Get*` / `iam:List*` block is optional but lets fslab pre-validate the instance profile before the `RunInstances` call.

### Primary: customer-managed policy

A customer-managed policy is recommended for fslab — it groups all fslab developer grants under one named, version-controlled object you can extend as fslab adds roles, and it is reusable across permission sets (e.g. if you later add an admin variant).

```bash
# 1. Create the policy in the account.
aws iam create-policy \
    --policy-name FiresimLabDeveloperAccess \
    --policy-document file:///tmp/firesim-lab-developer-launch.json \
    --profile $ADMIN_PROFILE

# 2. Attach it to the permission set BY NAME (Identity Center looks up a
#    same-named policy in each target account at provisioning time).
aws sso-admin attach-customer-managed-policy-reference-to-permission-set \
    --instance-arn $SSO_INSTANCE_ARN \
    --permission-set-arn $PERMISSION_SET_ARN \
    --customer-managed-policy-reference Name=FiresimLabDeveloperAccess,Path=/ \
    --profile $ADMIN_PROFILE --region $INSTANCE_REGION

# 3. Re-provision so the change takes effect in the account.
aws sso-admin provision-permission-set \
    --instance-arn $SSO_INSTANCE_ARN \
    --permission-set-arn $PERMISSION_SET_ARN \
    --target-type AWS_ACCOUNT --target-id ${ACCOUNT_ID} \
    --profile $ADMIN_PROFILE --region $INSTANCE_REGION
```

As fslab evolves, extend **this same policy** with additional statements rather than attaching a second one — that keeps all fslab permission-set grants under one named object.

### Alternative: inline policy on the permission set

If you would rather embed the JSON directly into the permission set and skip managing a separate policy object:

```bash
aws sso-admin put-inline-policy-to-permission-set \
    --instance-arn $SSO_INSTANCE_ARN \
    --permission-set-arn $PERMISSION_SET_ARN \
    --inline-policy file:///tmp/firesim-lab-developer-launch.json \
    --profile $ADMIN_PROFILE --region $INSTANCE_REGION

aws sso-admin provision-permission-set \
    --instance-arn $SSO_INSTANCE_ARN \
    --permission-set-arn $PERMISSION_SET_ARN \
    --target-type AWS_ACCOUNT --target-id ${ACCOUNT_ID} \
    --profile $ADMIN_PROFILE --region $INSTANCE_REGION
```

The inline policy travels with the permission set automatically and has no per-account lifecycle, but it is not reusable across permission sets, and adding more fslab grants later means re-editing this blob rather than versioning a named policy.

:::{note}
**Multi-account.** If `FireSim-Developer` is assigned to additional accounts, a same-named `FiresimLabDeveloperAccess` policy must exist in each account *before* you re-provision there, or provisioning fails with `PolicyNotFound`. Typical tooling: CloudFormation StackSets or Terraform. For a single-account setup this is a non-issue.
:::

## Troubleshooting

`RunInstances` returns `UnauthorizedOperation` mentioning PassRole
: The permission set is missing the `iam:PassRole` grant on the `fslab-fpga-builder` role ARN, or the change has not been provisioned into the target account. Re-apply the policy above and re-run `provision-permission-set`, then retry.

`Unable to locate credentials`
: No active SSO session, or you omitted `--profile`. Run `aws sso login --profile fslab-dev` and pass the profile on every command.

The `iam:PassRole` resource ARN must match the role name you create in {doc}`firesim-lab-aws-setup`
: If you name the build role something other than `fslab-fpga-builder`, update the `Resource` ARN in the PassRole policy to match.

## Next

Continue to {doc}`firesim-lab-aws-setup` to create the `fslab-fpga-builder` (and run-host) instance-profile roles this permission set is now allowed to pass, request the F2 quota, and wire the names into `fslab.yaml`.
