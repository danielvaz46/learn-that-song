#!/bin/bash
# Deploy the danielvaz.dev CloudFront/certificate/DNS stack and lock the site bucket to this distribution.
# Usage (from the project root): bash infra/deploy_site.sh <Route53 hosted zone ID>
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

ZONE="${1:?usage: bash infra/deploy_site.sh <hosted zone id>}"
STACK=danielvaz-site
BUCKET=learn-that-song-s3-site

# CloudFront certificates must be in us-east-1, so this stack lives there.
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name "$STACK" \
  --template-file infra/site.yaml \
  --parameter-overrides HostedZoneId="$ZONE" \
  --no-fail-on-empty-changeset

DIST_ARN=$(aws cloudformation describe-stacks --region us-east-1 --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionArn'].OutputValue" --output text)

# The bucket is private: only this CloudFront distribution may read it.
mkdir -p build
python - "$DIST_ARN" "$BUCKET" build/site-bucket-policy.json <<'EOF'
import json, sys
arn, bucket, out = sys.argv[1:4]
json.dump({"Version": "2012-10-17", "Statement": [{
    "Sid": "AllowCloudFrontOAC", "Effect": "Allow",
    "Principal": {"Service": "cloudfront.amazonaws.com"},
    "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*",
    "Condition": {"StringEquals": {"AWS:SourceArn": arn}}}]}, open(out, "w"))
EOF
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "file://build/site-bucket-policy.json"
echo "Bucket policy now allows only: $DIST_ARN"

aws cloudformation describe-stacks --region us-east-1 --stack-name "$STACK" --query "Stacks[0].Outputs" --output table
