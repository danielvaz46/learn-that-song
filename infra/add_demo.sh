#!/bin/bash
# Add a demo song to https://daniel-vaz.com/learnthatsong/ (a "Try a demo" button).
# Runs the song through the normal pipeline, then copies its stems from the stems bucket (1-day expiry) to
# learnthatsong/demos/<video_id>/ in the site bucket (no expiry), where CloudFront serves them as static files.
#
# Usage (from the project root):
#   bash infra/add_demo.sh <youtube-url> ["Button label"]   add or update a demo (label defaults to the video title)
#   bash infra/add_demo.sh --remove <video_id>              remove a demo
#   bash infra/add_demo.sh --cover <video_id>               (re)copy a demo's cover image only
#   bash infra/add_demo.sh --label <video_id> "Artist - Song"   rename a demo (the page shows the song, artist below)
# The cover is the video's YouTube thumbnail, copied next to the stems so the page never loads images from YouTube.
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

API=https://x3g1l5yblk.execute-api.ap-southeast-2.amazonaws.com
STEMS_BUCKET=learn-that-song-s3
SITE_BUCKET=learn-that-song-s3-site
PREFIX=learnthatsong/demos
STEMS="bass drums guitar other piano vocals"
DIST=$(aws cloudformation describe-stacks --region us-east-1 --stack-name daniel-vaz-site \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)

TMP=$(mktemp -d)
# Windows builds of curl, python and aws can't read Git Bash paths like /tmp/... (path conversion is off above)
command -v cygpath >/dev/null && TMP=$(cygpath -m "$TMP")
trap 'rm -rf "$TMP"' EXIT

json_get() { python -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get(sys.argv[2]) or '')" "$1" "$2"; }

# Rewrite demos.json with an entry added/replaced ($1=id, $2=name) or removed ($1=id, no name).
update_manifest() {
  aws s3 cp "s3://$SITE_BUCKET/$PREFIX/demos.json" "$TMP/demos.json" --only-show-errors 2>/dev/null || echo '[]' > "$TMP/demos.json"
  python - "$TMP/demos.json" "$@" <<'EOF'
import json, sys
path, vid = sys.argv[1], sys.argv[2]
name = sys.argv[3] if len(sys.argv) > 3 else None
demos = json.load(open(path))
if name is None:
    demos = [d for d in demos if d.get("id") != vid]
elif any(d.get("id") == vid for d in demos):
    demos = [dict(d, name=name) if d.get("id") == vid else d for d in demos]   # rename in place, keep the order
else:
    demos.append({"id": vid, "name": name})
json.dump(demos, open(path, "w"), ensure_ascii=False, indent=2)
print(f"{len(demos)} demo(s): " + ", ".join(d["name"] for d in demos))
EOF
  aws s3 cp "$TMP/demos.json" "s3://$SITE_BUCKET/$PREFIX/demos.json" \
    --content-type "application/json; charset=utf-8" --cache-control "no-cache" --only-show-errors
}

invalidate() {
  aws cloudfront create-invalidation --distribution-id "$DIST" --paths "$@" --query "Invalidation.Id" --output text >/dev/null
}

# Copy the video's thumbnail (320x180, no letterbox bars; the page crops it square) to demos/<id>/cover.jpg.
copy_cover() {
  curl -sSf "https://i.ytimg.com/vi/$1/mqdefault.jpg" -o "$TMP/cover.jpg"
  aws s3 cp "$TMP/cover.jpg" "s3://$SITE_BUCKET/$PREFIX/$1/cover.jpg" \
    --content-type "image/jpeg" --cache-control "public, max-age=86400" --only-show-errors
}

if [ "${1:-}" = "--cover" ]; then
  VID=${2:?video ID required}
  copy_cover "$VID"
  invalidate "/$PREFIX/$VID/cover.jpg"
  echo "Copied cover for $VID"
  exit 0
fi

if [ "${1:-}" = "--label" ]; then
  VID=${2:?video ID required}
  update_manifest "$VID" "${3:?label required}"
  invalidate "/$PREFIX/demos.json"
  exit 0
fi

if [ "${1:-}" = "--remove" ]; then
  VID=${2:?video ID required}
  update_manifest "$VID"
  aws s3 rm "s3://$SITE_BUCKET/$PREFIX/$VID/" --recursive --only-show-errors
  invalidate "/$PREFIX/demos.json" "/$PREFIX/$VID/*"
  echo "Removed demo $VID"
  exit 0
fi

URL=${1:?usage: bash infra/add_demo.sh <youtube-url> [\"Button label\"]}
LABEL=${2:-}

python -c "import json,sys; print(json.dumps({'url': sys.argv[1]}))" "$URL" > "$TMP/body.json"
curl -sS -X POST "$API/process" -H "Content-Type: application/json" --data-binary "@$TMP/body.json" -o "$TMP/job.json"
JOB=$(json_get "$TMP/job.json" job_id)
VID=$(json_get "$TMP/job.json" video_id)
if [ -z "$JOB" ]; then echo "Request rejected: $(cat "$TMP/job.json")" >&2; exit 1; fi
echo "Job $JOB for video $VID"

DEADLINE=$((SECONDS + 15 * 60))
while :; do
  curl -sS "$API/progress/$JOB" -o "$TMP/progress.json"
  STATUS=$(json_get "$TMP/progress.json" status)
  echo "  $STATUS $(json_get "$TMP/progress.json" percent)% $(json_get "$TMP/progress.json" message)"
  [ "$STATUS" = "done" ] && break
  if [ "$STATUS" = "error" ]; then echo "Processing failed: $(cat "$TMP/progress.json")" >&2; exit 1; fi
  if [ $SECONDS -gt $DEADLINE ]; then echo "Timed out" >&2; exit 1; fi
  sleep 10
done
NAME=${LABEL:-$(json_get "$TMP/progress.json" name)}
NAME=${NAME:-$VID}

for STEM in $STEMS; do
  aws s3 cp "s3://$STEMS_BUCKET/stems/$VID/$STEM.m4a" "s3://$SITE_BUCKET/$PREFIX/$VID/$STEM.m4a" \
    --content-type "audio/mp4" --cache-control "public, max-age=31536000, immutable" \
    --metadata-directive REPLACE --only-show-errors
done
copy_cover "$VID"
update_manifest "$VID" "$NAME"
invalidate "/$PREFIX/demos.json" "/$PREFIX/$VID/*"
echo "Added demo \"$NAME\" ($VID)"
