#!/bin/bash
# Deploy the spending guard. Looks up the API roles and the worker instance, then deploys infra/budget.yaml.
# Usage (from the project root): bash infra/deploy_budget.sh <alert email> [limit in USD, default 5]
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

EMAIL="${1:?usage: bash infra/deploy_budget.sh <alert email> [limit]}"
LIMIT="${2:-5}"

role() {  # physical role name of a resource in the API stack
  aws cloudformation describe-stack-resource --stack-name learn-that-song-api --logical-resource-id "$1" \
    --query StackResourceDetail.PhysicalResourceId --output text
}
PROCESS_ROLE=$(role ProcessRole)
RECONCILE_ROLE=$(role ReconcileRole)
INSTANCE=$(aws ec2 describe-instances --filters Name=tag:Name,Values=learn-that-song-worker \
  Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query "Reservations[0].Instances[0].InstanceId" --output text)
echo "process role: $PROCESS_ROLE"; echo "reconcile role: $RECONCILE_ROLE"; echo "worker instance: $INSTANCE"

aws cloudformation deploy \
  --stack-name learn-that-song-budget \
  --template-file infra/budget.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides AlertEmail="$EMAIL" LimitUsd="$LIMIT" \
    ProcessRoleName="$PROCESS_ROLE" ReconcileRoleName="$RECONCILE_ROLE" WorkerInstanceId="$INSTANCE"

aws cloudformation describe-stacks --stack-name learn-that-song-budget --query "Stacks[0].Outputs" --output table
