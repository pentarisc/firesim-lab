#!/usr/bin/env bash
# aws-create-build-role.sh — firesim-lab-aws-setup Step 4 (SOLO-ADMIN ONLY).
#
# Creates the build-host instance-profile role `fslab-fpga-builder` (S3 DCP
# staging + EC2 CreateFpgaImage + sts:GetCallerIdentity) and wraps it in an
# instance profile of the same name. Mutating: per-step confirmed, solo-admin
# only. Org developers: direct to your admin (the developer identity lacks
# iam:CreateRole by design).
#
# Usage:  aws-create-build-role.sh <admin-profile> <region>
# Runs inside the firesim-lab container. Idempotent: each create is guarded.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./detect-context.sh
source "$HERE/detect-context.sh"
fslab_detect_context

ADMIN_PROFILE="${1:?usage: aws-create-build-role.sh <admin-profile> <region>}"
REGION="${2:?usage: aws-create-build-role.sh <admin-profile> <region>}"
AWS="aws --profile $ADMIN_PROFILE --region $REGION"
ROLE="fslab-fpga-builder"
POLICY="FiresimLabBuildHostAccess"

# 4a + 4b — write the trust and permissions policy documents into the container.
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

fslab_exec "cat > /tmp/fslab-permissions.json <<'EOF'
{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    { \"Sid\": \"S3DcpStaging\", \"Effect\": \"Allow\",
      \"Action\": [\"s3:CreateBucket\",\"s3:HeadBucket\",\"s3:ListBucket\",\"s3:GetObject\",\"s3:PutObject\"],
      \"Resource\": \"*\" },
    { \"Sid\": \"Ec2Fpga\", \"Effect\": \"Allow\",
      \"Action\": [\"ec2:CreateFpgaImage\",\"ec2:DescribeFpgaImages\"], \"Resource\": \"*\" },
    { \"Sid\": \"StsIdentity\", \"Effect\": \"Allow\",
      \"Action\": [\"sts:GetCallerIdentity\"], \"Resource\": \"*\" }
  ]
}
EOF"

# 4c — role, inline policy, instance profile, attach (each guarded).
if fslab_exec "$AWS iam get-role --role-name $ROLE >/dev/null 2>&1"; then
  echo "role $ROLE exists — skipping create-role."
else
  fslab_exec "$AWS iam create-role --role-name $ROLE \
      --assume-role-policy-document file:///tmp/fslab-trust-policy.json"
fi

fslab_exec "$AWS iam put-role-policy --role-name $ROLE \
    --policy-name $POLICY --policy-document file:///tmp/fslab-permissions.json"

if fslab_exec "$AWS iam get-instance-profile --instance-profile-name $ROLE >/dev/null 2>&1"; then
  echo "instance profile $ROLE exists — skipping create-instance-profile."
else
  fslab_exec "$AWS iam create-instance-profile --instance-profile-name $ROLE"
fi

# add-role-to-instance-profile errors if the role is already attached; tolerate it.
fslab_exec "$AWS iam add-role-to-instance-profile --instance-profile-name $ROLE --role-name $ROLE 2>/dev/null" \
  || echo "role already attached to instance profile $ROLE — ok."

echo "Done. fslab.yaml (build host):  iam_instance_profile: $ROLE"
