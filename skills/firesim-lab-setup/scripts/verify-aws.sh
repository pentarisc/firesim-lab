#!/usr/bin/env bash
# verify-aws.sh — AWS F2 readiness probes (spec §9.3), least-privilege + graceful.
#
# Runs ONLY non-mutating queries. Designed to work for a normal **org developer**
# (the documented FireSim-Developer permission set), not just an admin:
#   - roles are checked via their INSTANCE PROFILE (iam:GetInstanceProfile is
#     granted by the PassRole policy; iam:GetRole is NOT) ;
#   - the F2 service quota is discovered BY NAME (no hardcoded, possibly-wrong
#     quota code) ;
#   - the FPGA Developer AMI is owned by aws-marketplace, not amazon.
#
# A probe that is DENIED is reported as **unknown**, never a gap — e.g. a developer
# cannot read servicequotas, so the F2 quota is "unknown, assume available" rather
# than a false "quota is 0". Only a genuinely missing resource is a gap. Admins
# (with broader read) get full verification through the same script.
#
# All AWS CLI calls run inside the container (AWS CLI v2) with an explicit
# --profile (modern SSO has no default profile).
#
# Usage:  verify-aws.sh <profile> <region>
# Exit code: non-zero only if there is at least one hard GAP (unknowns do not fail).

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./detect-context.sh
source "$HERE/detect-context.sh"
fslab_detect_context

PROFILE="${1:?usage: verify-aws.sh <profile> <region>}"
REGION="${2:?usage: verify-aws.sh <profile> <region>}"
AWS="aws --profile $PROFILE --region $REGION"

pass=0; gaps=0; unknown=0
ok()      { echo "  [ ok   ] $1"; pass=$((pass+1)); }
gap()     { echo "  [ GAP  ] ($1) $2"; gaps=$((gaps+1)); }      # missing resource — remediate
unkn()    { echo "  [ ???? ] ($1) $2"; unknown=$((unknown+1)); } # denied/not checkable — informational

# probe '<command>' -> sets REPLY (combined output), PROBE_RC, PROBE_DENIED
probe() {
  REPLY="$(fslab_exec "$1" 2>&1)"; PROBE_RC=$?
  if printf '%s' "$REPLY" | grep -qiE 'AccessDenied|UnauthorizedOperation|not authorized to perform'; then
    PROBE_DENIED=1; else PROBE_DENIED=0; fi
}
empty_val() { [ -z "$1" ] || [ "$1" = "None" ] || [ "$1" = "null" ]; }

echo "AWS F2 readiness — profile=$PROFILE region=$REGION"

# 1. Active SSO session ---------------------------------------------------------
probe "$AWS sts get-caller-identity --query Arn --output text"
if [ "$PROBE_RC" -eq 0 ] && ! empty_val "$REPLY"; then
  ok "active SSO session ($REPLY)"
else
  gap "developer-login" "no valid SSO session — run 'aws sso login --use-device-code --profile $PROFILE' (headless container; firesim-lab-sim does this)"
fi

# 2. Build instance profile (least-priv: get-instance-profile, not get-role) ----
probe "$AWS iam get-instance-profile --instance-profile-name fslab-fpga-builder --query InstanceProfile.InstanceProfileName --output text"
if   [ "$PROBE_DENIED" -eq 1 ]; then unkn "iam-read" "can't read instance profiles — ask admin/console to confirm fslab-fpga-builder"
elif [ "$PROBE_RC" -eq 0 ] && ! empty_val "$REPLY"; then ok "build instance profile (fslab-fpga-builder)"
else gap "admin-cli" "fslab-fpga-builder instance profile missing — Setup S4 / firesim-lab-aws-setup Step 4"; fi

# 3. Run instance profile (also via instance profile) ---------------------------
probe "$AWS iam get-instance-profile --instance-profile-name fslab-fpga-runner --query InstanceProfile.Roles[0].RoleName --output text"
if   [ "$PROBE_DENIED" -eq 1 ]; then unkn "iam-read" "can't read instance profiles — ask admin/console to confirm fslab-fpga-runner"
elif [ "$PROBE_RC" -eq 0 ] && ! empty_val "$REPLY"; then ok "run instance profile (fslab-fpga-runner -> role $REPLY)"
else gap "admin-cli" "fslab-fpga-runner instance profile missing — Setup S4 / firesim-lab-aws-setup Step 5"; fi

# 4. iam:PassRole — NOT self-verifiable by a developer (simulate needs an IAM
#    principal ARN + iam:SimulatePrincipalPolicy). Treat as an info note, not a
#    gap; the real proof is a successful build launch. Admins can confirm via the
#    permission-set policy.
unkn "passrole" "iam:PassRole on fslab-fpga-builder is granted at setup and not self-checkable here — ensure your permission set has it (identity-center-sso)"

# 5. SSH key pair in region -----------------------------------------------------
probe "$AWS ec2 describe-key-pairs --key-names firesim-lab --query KeyPairs[0].KeyName --output text"
if   [ "$PROBE_DENIED" -eq 1 ]; then unkn "ec2-read" "can't read key pairs — confirm 'firesim-lab' exists in $REGION"
elif [ "$PROBE_RC" -eq 0 ] && ! empty_val "$REPLY"; then ok "EC2 key pair (firesim-lab) in $REGION"
else gap "admin-cli" "key pair 'firesim-lab' missing in $REGION — firesim-lab-aws-setup Step 3"; fi

# 6. F2 service quota > 0 — discovered BY NAME, graceful on AccessDenied ---------
# Applied value first (if the quota was ever changed from default), else default.
QNAME="On-Demand F instances"   # matches "Running On-Demand F instances"
probe "$AWS service-quotas list-service-quotas --service-code ec2 --query \"Quotas[?contains(QuotaName, '$QNAME')].Value\" --output text"
if [ "$PROBE_DENIED" -eq 1 ]; then
  unkn "console-quota" "servicequotas read denied (normal for developers) — ASSUMING F2 quota is available; verify in the console or ask your admin"
else
  QVAL="$(printf '%s' "$REPLY" | tr '\t' '\n' | grep -E '^[0-9.]+$' | head -n1)"
  if empty_val "${QVAL:-}"; then
    probe "$AWS service-quotas list-aws-default-service-quotas --service-code ec2 --query \"Quotas[?contains(QuotaName, '$QNAME')].Value\" --output text"
    QVAL="$(printf '%s' "$REPLY" | tr '\t' '\n' | grep -E '^[0-9.]+$' | head -n1)"
  fi
  if [ -n "${QVAL:-}" ] && awk "BEGIN{exit !($QVAL>0)}" 2>/dev/null; then
    ok "F2 on-demand quota = $QVAL vCPU (>0)"
  else
    gap "console-quota" "F2 on-demand quota is ${QVAL:-0} — request 'Running On-Demand F instances' early (console; ~1-2 day approval)"
  fi
fi

# 7. FPGA Developer AMI available (owner = aws-marketplace, not amazon) ----------
probe "$AWS ec2 describe-images --owners aws-marketplace amazon --filters 'Name=name,Values=FPGA Developer AMI*' --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text"
if   [ "$PROBE_DENIED" -eq 1 ]; then unkn "ec2-read" "can't read images — confirm an FPGA Developer AMI exists in $REGION"
elif [ "$PROBE_RC" -eq 0 ] && ! empty_val "$REPLY"; then ok "FPGA Developer AMI available in $REGION ($REPLY)"
else gap "console-quota" "no FPGA Developer AMI found in $REGION — confirm the region is F2-capable"; fi

echo "readiness: $pass ok, $gaps gap(s), $unknown unknown"
[ "$gaps" -eq 0 ]
