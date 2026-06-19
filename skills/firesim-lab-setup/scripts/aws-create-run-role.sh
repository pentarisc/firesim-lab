#!/usr/bin/env bash
# aws-create-run-role.sh — firesim-lab-aws-setup Step 5 (SOLO-ADMIN ONLY).
#
# Creates the narrower run-host instance-profile role `fslab-fpga-runner`
# (ec2:DescribeFpgaImages + ec2:AssociateFpgaImage only — no S3, no
# CreateFpgaImage) and wraps it in an instance profile. Reuses the Step 4 trust
# policy (same EC2 service principal). Mutating: per-step confirmed, solo-admin
# only.
#
# Usage:  aws-create-run-role.sh <admin-profile> <region>
# Runs inside the firesim-lab container. Idempotent: each create is guarded.
# Run aws-create-build-role.sh first (it writes /tmp/fslab-trust-policy.json),
# or this script re-writes the trust policy itself below.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./detect-context.sh
source "$HERE/detect-context.sh"
fslab_detect_context

ADMIN_PROFILE="${1:?usage: aws-create-run-role.sh <admin-profile> <region>}"
REGION="${2:?usage: aws-create-run-role.sh <admin-profile> <region>}"
AWS="aws --profile $ADMIN_PROFILE --region $REGION"
ROLE="fslab-fpga-runner"
POLICY="FiresimLabRunHostAccess"

# Trust policy (idempotent re-write — identical to Step 4a).
fslab_exec "cat > /tmp/fslab-trust-policy.json <<'EOF'
{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Effect\": \"Allow\",
    \"Principal\": {\"Service\": \"ec2.amazonaws.com\"},
    \"Action\": \"sts:AssumeRole\"
  }]
}
EOF"

fslab_exec "cat > /tmp/fslab-run-permissions.json <<'EOF'
{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    { \"Sid\": \"Ec2FpgaLoad\", \"Effect\": \"Allow\",
      \"Action\": [\"ec2:DescribeFpgaImages\",\"ec2:AssociateFpgaImage\"], \"Resource\": \"*\" }
  ]
}
EOF"

if fslab_exec "$AWS iam get-role --role-name $ROLE >/dev/null 2>&1"; then
  echo "role $ROLE exists — skipping create-role."
else
  fslab_exec "$AWS iam create-role --role-name $ROLE \
      --assume-role-policy-document file:///tmp/fslab-trust-policy.json"
fi

fslab_exec "$AWS iam put-role-policy --role-name $ROLE \
    --policy-name $POLICY --policy-document file:///tmp/fslab-run-permissions.json"

if fslab_exec "$AWS iam get-instance-profile --instance-profile-name $ROLE >/dev/null 2>&1"; then
  echo "instance profile $ROLE exists — skipping create-instance-profile."
else
  fslab_exec "$AWS iam create-instance-profile --instance-profile-name $ROLE"
fi

fslab_exec "$AWS iam add-role-to-instance-profile --instance-profile-name $ROLE --role-name $ROLE 2>/dev/null" \
  || echo "role already attached to instance profile $ROLE — ok."

echo "Done. fslab.yaml (run host):  iam_instance_profile: $ROLE"
