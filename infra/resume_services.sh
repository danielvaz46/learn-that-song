#!/bin/bash
# Undo the budget kill switch: detach the deny policy from the three roles it was attached to.
# The worker starts again by itself the next time someone submits a song.
# Usage (from the project root): bash infra/resume_services.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

POLICY_ARN="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/learn-that-song-kill-switch"
role() {
  aws cloudformation describe-stack-resource --stack-name learn-that-song-api --logical-resource-id "$1" \
    --query StackResourceDetail.PhysicalResourceId --output text
}

for r in "$(role ProcessRole)" "$(role ReconcileRole)" learn-that-song-worker; do
  if aws iam list-attached-role-policies --role-name "$r" --query "AttachedPolicies[].PolicyArn" --output text | grep -q "$POLICY_ARN"; then
    aws iam detach-role-policy --role-name "$r" --policy-arn "$POLICY_ARN" && echo "detached from $r"
  else
    echo "not attached to $r"
  fi
done
echo "Services resumed. If the budget is still over its limit, it will fire again at the next refresh."
