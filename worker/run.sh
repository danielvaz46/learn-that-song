#!/bin/bash
# Boot wrapper for the worker instance (runs as root via learn-worker.service).
# Fetches the latest worker.py from S3, runs it as the unprivileged `worker` user,
# and powers the instance off when the worker reports idle (exit 42), crashes repeatedly,
# or has been up longer than MAX_UPTIME seconds (safety net against runaway billing).
set -u

REGION=ap-southeast-2
CODE_URI=s3://learn-that-song-s3/worker/worker.py
DIR=/opt/worker/current
MAX_UPTIME=10800
export PATH=/usr/local/bin:/opt/venv/bin:/usr/sbin:/usr/bin:/sbin:/bin

mkdir -p "$DIR"

( sleep "$MAX_UPTIME"; echo "watchdog: max uptime reached"; shutdown -h now ) &

if aws s3 cp "$CODE_URI" "$DIR/worker.py.new" --region "$REGION" --only-show-errors; then
  mv -f "$DIR/worker.py.new" "$DIR/worker.py"
else
  echo "code fetch failed; using existing copy if any"
fi
if [ ! -f "$DIR/worker.py" ]; then
  echo "no worker code available, shutting down"
  shutdown -h now
  exit 1
fi
chmod 644 "$DIR/worker.py"

crashes=0
while true; do
  runuser -u worker -- env HOME=/var/lib/worker HF_HOME=/opt/hf TORCH_HOME=/opt/torch \
    HF_HUB_OFFLINE=1 PATH="$PATH" /opt/venv/bin/python "$DIR/worker.py"
  rc=$?
  if [ "$rc" -eq 42 ]; then
    echo "idle, shutting down"
    shutdown -h now
    exit 0
  fi
  crashes=$((crashes + 1))
  echo "worker exited rc=$rc (crash $crashes)"
  if [ "$crashes" -ge 5 ]; then
    echo "too many crashes, shutting down"
    shutdown -h now
    exit 1
  fi
  sleep 10
done
