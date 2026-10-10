#!/bin/bash
# Publish the Learn That Song web app to https://danielvaz.dev/learnthatsong/ and refresh CloudFront.
# Usage (from the project root): bash infra/publish_app.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

BUCKET=learn-that-song-s3-site
DIST=$(aws cloudformation describe-stacks --region us-east-1 --stack-name danielvaz-site \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)

aws s3 cp player.html "s3://$BUCKET/learnthatsong/index.html" \
  --content-type "text/html; charset=utf-8" --cache-control "no-cache" --only-show-errors
aws s3 cp tone.js "s3://$BUCKET/learnthatsong/tone.js" \
  --content-type "application/javascript" --cache-control "public, max-age=86400" --only-show-errors
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/learnthatsong/*" \
  --query "Invalidation.[Id,Status]" --output text
echo "Published to https://danielvaz.dev/learnthatsong/"
