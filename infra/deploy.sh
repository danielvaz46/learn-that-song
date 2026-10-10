#!/bin/bash
# Package api/handler.py, upload it to S3, and deploy the CloudFront-independent API stack.
# Usage (from the project root): bash infra/deploy.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

BUCKET=learn-that-song-s3
STACK=learn-that-song-api
mkdir -p build
ZIP=build/handler.zip

python - "$ZIP" <<'EOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    z.write("api/handler.py", "handler.py")
EOF

HASH=$(python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" "$ZIP")
KEY="lambda/handler-$HASH.zip"
aws s3 cp "$ZIP" "s3://$BUCKET/$KEY" --only-show-errors
echo "uploaded s3://$BUCKET/$KEY"

aws cloudformation deploy \
  --stack-name "$STACK" \
  --template-file infra/api.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides CodeBucket="$BUCKET" CodeKey="$KEY" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs" --output table
