#!/usr/bin/env python3
"""
worker.py - Learn That Song processing worker (runs on the EC2 worker instance).

Polls the SQS job queue. For each job {"job_id", "video_id"} it:
  1. checks the on-disk cache (stems already processed for this video ID)
  2. validates the video is a song of acceptable length (yt-dlp metadata, no download)
  3. downloads the audio with yt-dlp (cookies from Secrets Manager)
  4. separates it into six stems with Demucs htdemucs_6s
  5. encodes the stems to AAC in .m4a files (one ffmpeg per stem, in parallel)
  6. uploads the stems to s3://<bucket>/stems/<video_id>/<stem>.m4a
and writes progress to s3://<bucket>/progress/<job_id>.json throughout.

After IDLE_SECONDS with an empty queue it exits with EXIT_IDLE (42); run.sh then
shuts the instance down.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REGION = "ap-southeast-2"
BUCKET = "learn-that-song-s3"
QUEUE_URL = "https://sqs.ap-southeast-2.amazonaws.com/884145163779/learn-that-song-jobs"
COOKIE_SECRET = "learn-that-song/yt-cookies"

MIN_SECONDS = 30
MAX_SECONDS = 8 * 60
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "300"))
MAX_RECEIVES = 2                      # must match the queue's maxReceiveCount
VISIBILITY_SECONDS = 900
RETRY_DELAY_SECONDS = 30
CACHE_LIMIT_BYTES = 10 * 1024 ** 3

CODE_KEY = "worker/worker.py"           # where deploys upload new worker code
RELOAD_CHECK_SECONDS = 60

YTDLP_TIMEOUT = 300
DEMUCS_TIMEOUT = 900
FFMPEG_TIMEOUT = 300

HOME = Path(os.environ.get("WORKER_HOME", "/var/lib/worker"))
JOBS_DIR = HOME / "jobs"
CACHE_DIR = HOME / "cache"
CODE_DIR = HOME / "code"                # writable copy that a reloaded worker runs from

STEMS = ["bass", "drums", "guitar", "other", "piano", "vocals"]
# AAC in MP4: every browser's Web Audio can decode it. Ogg Opus fails on Safari/iPad ("Decoding failed").
AUDIO_EXT = "m4a"
AUDIO_FORMAT = "m4a"
AUDIO_BITRATE = "160k"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
PCT_RE = re.compile(rb"(\d{1,3})%\|")
EXIT_IDLE = 42                          # run.sh shuts the instance down on this code


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


class JobError(Exception):
    """A failure that retrying will not fix. `message` is safe to show to the user."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ── Validation ────────────────────────────────────────────────────────────────

def parse_message(body: str):
    """Return (job_id, video_id) from an SQS message body, or None if malformed."""
    try:
        data = json.loads(body)
        job_id, video_id = data["job_id"], data["video_id"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(job_id, str) and JOB_ID_RE.match(job_id)):
        return None
    if not (isinstance(video_id, str) and VIDEO_ID_RE.match(video_id)):
        return None
    return job_id, video_id


def validate_info(info: dict) -> None:
    """Raise JobError unless the yt-dlp metadata describes a reasonable song."""
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise JobError("live", "Live streams and premieres can't be processed.")

    duration = info.get("duration")
    if not duration:
        raise JobError("unavailable", "Couldn't determine the video length.")
    if duration < MIN_SECONDS:
        raise JobError("too_short", f"That video is only {int(duration)} seconds long.")
    if duration > MAX_SECONDS:
        raise JobError(
            "too_long",
            f"That video is {duration / 60:.1f} minutes long; the limit is {MAX_SECONDS // 60} minutes.",
        )

    is_music = (
        "Music" in (info.get("categories") or [])
        or bool(info.get("track"))
        or bool(info.get("artist"))
    )
    if not is_music:
        raise JobError("not_music", "That doesn't look like a song (it isn't in YouTube's Music category).")


def classify_ytdlp_error(stderr: str) -> JobError:
    if "Sign in to confirm" in stderr:
        log("COOKIES_EXPIRED: yt-dlp hit the bot check; refresh the learn-that-song/yt-cookies secret")
        return JobError("service_unavailable", "The service can't fetch videos right now. Please try again later.")
    if re.search(r"Video unavailable|Private video|removed|This video is not available", stderr):
        return JobError("unavailable", "That video is unavailable.")
    return JobError("extract_failed", "Couldn't download that video.")


# ── Progress reporting ────────────────────────────────────────────────────────

class Progress:
    """Writes progress/<job_id>.json, throttled so frequent updates don't spam S3."""

    def __init__(self, s3, job_id: str, video_id: str):
        self.s3 = s3
        self.job_id = job_id
        self.state = {
            "job_id": job_id, "video_id": video_id, "status": "starting", "percent": 0,
            "message": "Starting", "name": None, "stems": None, "ext": None, "error": None, "updated": None,
        }
        self._last_write = 0.0

    def update(self, status=None, percent=None, message=None, force=False, **extra) -> None:
        changed = status is not None and status != self.state["status"]
        if status is not None:
            self.state["status"] = status
        if percent is not None:
            self.state["percent"] = int(percent)
        if message is not None:
            self.state["message"] = message
        self.state.update(extra)
        if force or changed or time.time() - self._last_write >= 3:
            self._write()

    def _write(self) -> None:
        self.state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self.s3.put_object(
                Bucket=BUCKET, Key=f"progress/{self.job_id}.json",
                Body=json.dumps(self.state).encode(),
                ContentType="application/json", CacheControl="no-store",
            )
            self._last_write = time.time()
        except Exception as e:  # progress is best-effort; never fail the job over it
            log(f"progress write failed: {e}")


# ── Cache ─────────────────────────────────────────────────────────────────────

def cache_get(video_id: str):
    d = CACHE_DIR / video_id
    meta = d / "meta.json"
    if meta.exists() and all((d / f"{s}.{AUDIO_EXT}").exists() for s in STEMS):
        d.touch()
        return d, json.loads(meta.read_text())
    return None


def cache_put(video_id: str, src_dir: Path, meta: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f".{video_id}.tmp"
    final = CACHE_DIR / video_id
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    for s in STEMS:
        shutil.move(str(src_dir / f"{s}.{AUDIO_EXT}"), tmp / f"{s}.{AUDIO_EXT}")
    (tmp / "meta.json").write_text(json.dumps(meta))
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    cache_evict()
    return final


def cache_evict() -> None:
    entries = [p for p in CACHE_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")]
    sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file()) for p in entries}
    total = sum(sizes.values())
    for p in sorted(entries, key=lambda p: p.stat().st_mtime):
        if total <= CACHE_LIMIT_BYTES:
            break
        log(f"cache full, evicting {p.name}")
        shutil.rmtree(p, ignore_errors=True)
        total -= sizes[p]


# ── Pipeline steps ────────────────────────────────────────────────────────────

def write_cookies(sm, path: Path) -> None:
    secret = sm.get_secret_value(SecretId=COOKIE_SECRET)["SecretString"]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret)


def ytdlp_cmd(cookies: Path, *args: str) -> list:
    return ["yt-dlp", "--cookies", str(cookies), "--no-playlist", "--no-warnings", *args]


def fetch_info(video_id: str, cookies: Path) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    r = subprocess.run(ytdlp_cmd(cookies, "--skip-download", "--dump-single-json", url),
                       capture_output=True, text=True, timeout=YTDLP_TIMEOUT)
    if r.returncode != 0:
        raise classify_ytdlp_error(r.stderr)
    return json.loads(r.stdout)


def download_audio(video_id: str, cookies: Path, job_dir: Path) -> Path:
    url = f"https://www.youtube.com/watch?v={video_id}"
    r = subprocess.run(
        ytdlp_cmd(cookies, "-f", "bestaudio/best", "-x", "--audio-format", "wav",
                  "--ffmpeg-location", "/usr/local/bin", "-o", str(job_dir / "audio.%(ext)s"), url),
        capture_output=True, text=True, timeout=YTDLP_TIMEOUT)
    wav = job_dir / "audio.wav"
    if r.returncode != 0 or not wav.exists():
        raise classify_ytdlp_error(r.stderr)
    return wav


def separate(progress: Progress, audio: Path, out_dir: Path) -> Path:
    cmd = [sys.executable, "-m", "demucs", "-n", "htdemucs_6s", "--out", str(out_dir), str(audio)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    timer = threading.Timer(DEMUCS_TIMEOUT, proc.kill)
    timer.start()
    tail = deque(maxlen=20)
    buf = b""
    try:
        while True:
            chunk = proc.stderr.read1(4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for line in lines:
                m = PCT_RE.search(line)
                if m:
                    progress.update(percent=15 + int(m.group(1)) * 0.70)
                elif line.strip():
                    tail.append(line.decode(errors="replace"))
        rc = proc.wait()
    finally:
        timer.cancel()
    if rc != 0:
        raise RuntimeError(f"demucs exited {rc}: {' | '.join(tail)}")
    stem_dir = out_dir / "htdemucs_6s" / audio.stem
    if not all((stem_dir / f"{s}.wav").exists() for s in STEMS):
        raise RuntimeError("demucs produced no/incomplete stems")
    return stem_dir


def encode_audio(stem_dir: Path) -> None:
    def convert(stem: str) -> None:
        wav = stem_dir / f"{stem}.wav"
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                            "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart",
                            "-y", str(stem_dir / f"{stem}.{AUDIO_EXT}")],
                           capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {stem}: {r.stderr[-300:]}")
        wav.unlink()

    with ThreadPoolExecutor(max_workers=len(STEMS)) as pool:
        list(pool.map(convert, STEMS))


def upload_stems(s3, video_id: str, src_dir: Path, meta: dict) -> None:
    def put(stem: str) -> None:
        s3.upload_file(str(src_dir / f"{stem}.{AUDIO_EXT}"), BUCKET, f"stems/{video_id}/{stem}.{AUDIO_EXT}",
                       ExtraArgs={"ContentType": "audio/mp4"})

    with ThreadPoolExecutor(max_workers=len(STEMS)) as pool:
        list(pool.map(put, STEMS))
    # Written last: its presence tells the API that the whole set is available.
    meta = {**meta, "format": AUDIO_FORMAT}
    s3.put_object(Bucket=BUCKET, Key=f"stems/{video_id}/meta.json", Body=json.dumps(meta).encode(),
                  ContentType="application/json")


def process(s3, sm, progress: Progress, video_id: str) -> None:
    cached = cache_get(video_id)
    if cached:
        src_dir, meta = cached
        log(f"{video_id}: cache hit")
        progress.update("uploading", 90, "Found previously processed stems", force=True)
    else:
        job_dir = JOBS_DIR / progress.job_id
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, mode=0o700)
        try:
            cookies = job_dir / "cookies.txt"
            write_cookies(sm, cookies)

            progress.update("validating", 3, "Checking the video", force=True)
            info = fetch_info(video_id, cookies)
            validate_info(info)
            meta = {"name": info.get("title") or video_id, "duration": info.get("duration")}

            progress.update("downloading", 8, "Downloading audio", force=True)
            audio = download_audio(video_id, cookies, job_dir)
            cookies.unlink(missing_ok=True)

            progress.update("separating", 15, "Separating instruments", force=True)
            stem_dir = separate(progress, audio, job_dir / "out")

            progress.update("encoding", 86, "Compressing stems", force=True)
            encode_audio(stem_dir)
            src_dir = cache_put(video_id, stem_dir, meta)

            progress.update("uploading", 94, "Uploading", force=True)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    upload_stems(s3, video_id, src_dir, meta)
    progress.update("done", 100, "Ready", force=True, name=meta["name"], stems=STEMS, ext=AUDIO_EXT)
    log(f"{video_id}: done ({meta['name']})")


# ── Code reload ───────────────────────────────────────────────────────────────

def exec_worker(path: Path) -> None:
    """Replace this process with the worker at `path` (same PID, so systemd is none the wiser)."""
    os.execv(sys.executable, [sys.executable, str(path)])


def maybe_reload(s3) -> None:
    """If a different worker.py has been uploaded to S3, run that instead.

    Without this, a worker that stays up for hours keeps running the code it booted with, so a deploy
    only reached instances that happened to restart. Only called between jobs. For a single-part upload the
    S3 ETag is the file's MD5, which is compared with this file's own MD5.
    """
    try:
        remote = s3.head_object(Bucket=BUCKET, Key=CODE_KEY)["ETag"].strip('"')
        if "-" in remote or remote == hashlib.md5(Path(__file__).read_bytes()).hexdigest():
            return
        body = s3.get_object(Bucket=BUCKET, Key=CODE_KEY)["Body"].read()
        if hashlib.md5(body).hexdigest() != remote:
            return  # the object changed again while downloading; try next time
        CODE_DIR.mkdir(parents=True, exist_ok=True)
        target = CODE_DIR / "worker.py"
        tmp = target.with_suffix(".new")
        tmp.write_bytes(body)
        os.replace(tmp, target)
        log(f"new worker code detected ({remote[:8]}); restarting into it")
        exec_worker(target)
    except Exception as e:
        log(f"code reload check failed: {e}")


# ── Queue loop ────────────────────────────────────────────────────────────────

def handle(sqs, s3, sm, msg: dict) -> None:
    receipt = msg["ReceiptHandle"]
    parsed = parse_message(msg["Body"])
    if not parsed:
        log(f"dropping malformed message: {msg['Body'][:200]!r}")
        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
        return

    job_id, video_id = parsed
    receives = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
    progress = Progress(s3, job_id, video_id)
    log(f"job {job_id}: {video_id} (attempt {receives})")
    try:
        progress.update("starting", 1, "Starting", force=True)
        process(s3, sm, progress, video_id)
        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
    except JobError as e:
        log(f"job {job_id}: rejected: {e}")
        progress.update("error", 0, e.message, force=True, error={"code": e.code, "message": e.message})
        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
    except Exception:
        log(f"job {job_id}: failed\n{traceback.format_exc()}")
        if receives >= MAX_RECEIVES:
            text = "Processing failed. Please try again later."
            progress.update("error", 0, text, force=True, error={"code": "internal_error", "message": text})
            # Leave the message: the next receive redrives it to the dead-letter queue.
            sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=receipt, VisibilityTimeout=0)
        else:
            progress.update("retrying", 0, "Something went wrong; retrying", force=True)
            sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=receipt,
                                          VisibilityTimeout=RETRY_DELAY_SECONDS)


def main() -> int:
    import boto3

    sqs = boto3.client("sqs", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    sm = boto3.client("secretsmanager", region_name=REGION)

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for leftover in JOBS_DIR.iterdir():
        shutil.rmtree(leftover, ignore_errors=True)

    log(f"worker started; idle timeout {IDLE_SECONDS}s")
    last_activity = time.time()
    last_reload_check = 0.0
    while True:
        if time.time() - last_activity > IDLE_SECONDS:
            log("idle, exiting")
            return EXIT_IDLE
        if time.time() - last_reload_check > RELOAD_CHECK_SECONDS:
            maybe_reload(s3)
            last_reload_check = time.time()
        resp = sqs.receive_message(
            QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20,
            VisibilityTimeout=VISIBILITY_SECONDS, AttributeNames=["ApproximateReceiveCount"],
        )
        for msg in resp.get("Messages", []):
            handle(sqs, s3, sm, msg)
            last_activity = time.time()


if __name__ == "__main__":
    sys.exit(main())
