#!/usr/bin/env bash
# aws-create-keypair.sh — firesim-lab-aws-setup Step 3 (SOLO-ADMIN ONLY).
#
# Creates the EC2 key pair fslab launches instances with, saving the private key
# to ~/.ssh/fslab_ed25519. Mutating: the skill runs this only with explicit,
# per-step confirmation and only when a solo-developer admin profile with
# IAM/EC2-write is detected. Org developers: direct to your admin instead.
#
# Usage:  aws-create-keypair.sh <admin-profile> <region>
# Runs inside the firesim-lab container (AWS CLI v2). Idempotent: skips if the
# key pair already exists in the region.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./detect-context.sh
source "$HERE/detect-context.sh"
fslab_detect_context

ADMIN_PROFILE="${1:?usage: aws-create-keypair.sh <admin-profile> <region>}"
REGION="${2:?usage: aws-create-keypair.sh <admin-profile> <region>}"
AWS="aws --profile $ADMIN_PROFILE --region $REGION"
KEY_NAME="firesim-lab"
KEY_PATH="~/.ssh/fslab_ed25519"

if fslab_exec "$AWS ec2 describe-key-pairs --key-names $KEY_NAME >/dev/null 2>&1"; then
  echo "key pair '$KEY_NAME' already exists in $REGION — nothing to do."
  echo "fslab.yaml: key_name: $KEY_NAME   ssh_key: $KEY_PATH"
  exit 0
fi

echo "Creating EC2 key pair '$KEY_NAME' in $REGION (private key -> $KEY_PATH)…"
# --key-type ed25519 so the key matches its filename (the create-key-pair default
# is RSA); mkdir -p ~/.ssh in case the dir isn't present in the container.
fslab_exec "mkdir -p ~/.ssh && $AWS ec2 create-key-pair --key-name $KEY_NAME --key-type ed25519 \
    --query 'KeyMaterial' --output text > $KEY_PATH && chmod 600 $KEY_PATH"

echo "Done. In fslab.yaml use:  key_name: $KEY_NAME   ssh_key: $KEY_PATH"
