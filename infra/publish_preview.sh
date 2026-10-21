#!/bin/bash
# Publish the working copy of the app to an unlisted preview address for testing before it goes live:
#   https://daniel-vaz.com/preview/learnthatsong/
# Same domain, API and browser cache as the live app; nothing links to it. Publish for real with publish_app.sh.
# Usage (from the project root): bash infra/publish_preview.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

BUCKET=learn-that-song-s3-site
DIST=$(aws cloudformation describe-stacks --region us-east-1 --stack-name daniel-vaz-site \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)

aws s3 cp player.html "s3://$BUCKET/preview/learnthatsong/index.html" \
  --content-type "text/html; charset=utf-8" --cache-control "no-cache" --only-show-errors
aws s3 cp tone.js "s3://$BUCKET/preview/learnthatsong/tone.js" \
  --content-type "application/javascript" --cache-control "public, max-age=86400" --only-show-errors
aws s3 cp icons/ "s3://$BUCKET/preview/learnthatsong/" --recursive \
  --cache-control "public, max-age=86400" --only-show-errors
# Demo songs: copy the live ones so the preview shows the same buttons (server-side copy, nothing downloaded).
aws s3 sync "s3://$BUCKET/learnthatsong/demos/" "s3://$BUCKET/preview/learnthatsong/demos/" --delete --only-show-errors
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/preview/*" \
  --query "Invalidation.[Id,Status]" --output text
echo "Preview at https://daniel-vaz.com/preview/learnthatsong/"
