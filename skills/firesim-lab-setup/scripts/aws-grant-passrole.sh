#!/usr/bin/env bash
# aws-grant-passrole.sh — identity-center-sso PassRole grant (SOLO-ADMIN ONLY).
#
# Grants the FireSim-Developer permission set iam:PassRole on fslab-fpga-builder
# (constrained to ec2.amazonaws.com), via a customer-managed policy attached to
# the permission set, then re-provisions it. Mirrors the "Primary: customer-
# managed policy" path in docs/portal/setup/aws/identity-center-sso.md.
#
# Mutating: per-step confirmed, solo-admin only. Note the doc warning — an
# AWSReservedSSO_* permission-set role cannot itself back an EC2 instance
# profile; this only grants PassRole, it does not create the role.
#
# Usage:  aws-grant-passrole.sh <admin-profile> <instance-region> [permission-set-name]
#   <instance-region>      = the IAM Identity Center HOME region (e.g. us-east-1),
#                            which may differ from the F2 region.
#   [permission-set-name]  = defaults to FireSim-Developer.
# Runs inside the firesim-lab container.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./detect-context.sh
source "$HERE/detect-context.sh"
fslab_detect_context

ADMIN_PROFILE="${1:?usage: aws-grant-passrole.sh <admin-profile> <instance-region> [permission-set-name]}"
INSTANCE_REGION="${2:?usage: aws-grant-passrole.sh <admin-profile> <instance-region> [permission-set-name]}"
PS_NAME="${3:-FireSim-Developer}"
POLICY_NAME="FiresimLabDeveloperAccess"
A="aws --profile $ADMIN_PROFILE"
AR="$A --region $INSTANCE_REGION"

ACCOUNT_ID="$(fslab_exec "$A sts get-caller-identity --query Account --output text")"
SSO_INSTANCE_ARN="$(fslab_exec "$AR sso-admin list-instances --query 'Instances[0].InstanceArn' --output text")"

# Identity Center has no list-by-name API — enumerate and match on Name.
PERMISSION_SET_ARN="$(fslab_exec "
  for arn in \$($AR sso-admin list-permission-sets --instance-arn '$SSO_INSTANCE_ARN' --query 'PermissionSets[]' --output text); do
    name=\$($AR sso-admin describe-permission-set --instance-arn '$SSO_INSTANCE_ARN' --permission-set-arn \"\$arn\" --query 'PermissionSet.Name' --output text)
    [ \"\$name\" = '$PS_NAME' ] && echo \"\$arn\" && break
  done")"

echo "Account:        $ACCOUNT_ID"
echo "SSO instance:   $SSO_INSTANCE_ARN"
echo "Permission set: ${PERMISSION_SET_ARN:-<not found>}"
if [ -z "${PERMISSION_SET_ARN:-}" ]; then
  echo "Permission set '$PS_NAME' not found or not visible to $ADMIN_PROFILE — resolve that first." >&2
  exit 1
fi

# The PassRole policy document (ec2-constrained) + validation read grants.
fslab_exec "cat > /tmp/firesim-lab-developer-launch.json <<EOF
{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    { \"Sid\": \"PassFslabFpgaBuilderRole\", \"Effect\": \"Allow\", \"Action\": \"iam:PassRole\",
      \"Resource\": \"arn:aws:iam::${ACCOUNT_ID}:role/fslab-fpga-builder\",
      \"Condition\": { \"StringEquals\": {\"iam:PassedToService\": \"ec2.amazonaws.com\"} } },
    { \"Sid\": \"DescribeInstanceProfileForValidation\", \"Effect\": \"Allow\",
      \"Action\": [\"iam:GetInstanceProfile\",\"iam:ListInstanceProfiles\"], \"Resource\": \"*\" }
  ]
}
EOF"

# 1. Create (or reuse) the customer-managed policy.
if fslab_exec "$A iam get-policy --policy-arn arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME} >/dev/null 2>&1"; then
  echo "policy $POLICY_NAME exists — to extend it, add statements (see doc); skipping create."
else
  fslab_exec "$A iam create-policy --policy-name $POLICY_NAME \
      --policy-document file:///tmp/firesim-lab-developer-launch.json"
fi

# 2. Attach to the permission set by name.
fslab_exec "$AR sso-admin attach-customer-managed-policy-reference-to-permission-set \
    --instance-arn $SSO_INSTANCE_ARN --permission-set-arn $PERMISSION_SET_ARN \
    --customer-managed-policy-reference Name=$POLICY_NAME,Path=/ 2>/dev/null" \
  || echo "policy reference already attached — ok."

# 3. Re-provision so the change takes effect in the account.
fslab_exec "$AR sso-admin provision-permission-set \
    --instance-arn $SSO_INSTANCE_ARN --permission-set-arn $PERMISSION_SET_ARN \
    --target-type AWS_ACCOUNT --target-id ${ACCOUNT_ID}"

echo "Done. PassRole granted to '$PS_NAME' on fslab-fpga-builder (re-provisioned)."
