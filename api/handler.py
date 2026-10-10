"""
handler.py - Lambda functions behind the Learn That Song HTTP API.

  process_handler    POST /process            body {"url": "<youtube link>"}
  progress_handler   GET  /progress/{job_id}
  reconcile_handler  EventBridge, every minute: start the worker if jobs are waiting

No third-party dependencies (boto3 ships with the Lambda runtime).
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

BUCKET = os.environ.get("BUCKET", "learn-that-song-s3")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
INSTANCE_TAG = os.environ.get("INSTANCE_TAG", "learn-that-song-worker")
API_KEY_PARAM = os.environ.get("API_KEY_PARAM", "/learn-that-song/youtube-api-key")

MIN_SECONDS = 30
MAX_SECONDS = 8 * 60
MAX_QUEUE_DEPTH = 10
MAX_BODY_BYTES = 2048
URL_EXPIRY_SECONDS = 3600

STEMS = ["bass", "drums", "guitar", "other", "piano", "vocals"]
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}

_clients = {}


def client(name):
    """Lazily create boto3 clients (keeps import cheap and tests free of boto3)."""
    if name not in _clients:
        import boto3
        _clients[name] = boto3.client(name)
    return _clients[name]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message = status, code, message


def respond(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def error_response(e: ApiError) -> dict:
    return respond(e.status, {"error": {"code": e.code, "message": e.message}})


def log(msg: str) -> None:
    print(msg, flush=True)


# ── Input parsing ─────────────────────────────────────────────────────────────

def extract_video_id(raw):
    """Return the 11-character video ID from a YouTube link (or a bare ID), else None."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > 200:
        return None
    if VIDEO_ID_RE.match(raw):
        return raw
    try:
        u = urllib.parse.urlparse(raw if "://" in raw else "https://" + raw)
    except ValueError:
        return None
    if u.scheme not in ("http", "https"):
        return None
    host = (u.hostname or "").lower()
    candidate = None
    if host == "youtu.be":
        candidate = u.path.lstrip("/").split("/")[0]
    elif host in YOUTUBE_HOSTS:
        if u.path == "/watch":
            candidate = (urllib.parse.parse_qs(u.query).get("v") or [None])[0]
        else:
            parts = u.path.split("/")
            if len(parts) >= 3 and parts[1] in ("shorts", "embed", "live"):
                candidate = parts[2]
    return candidate if candidate and VIDEO_ID_RE.match(candidate) else None


def parse_iso_duration(value: str):
    """'PT3M45S' -> 225 seconds; None if unparseable."""
    m = DURATION_RE.match(value or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


# ── YouTube Data API check ────────────────────────────────────────────────────

def get_api_key():
    return client("ssm").get_parameter(Name=API_KEY_PARAM, WithDecryption=True)["Parameter"]["Value"]


def fetch_video_item(video_id: str):
    """Return the Data API item for a video, None if it doesn't exist, or raise OSError on API trouble."""
    params = urllib.parse.urlencode({
        "part": "snippet,contentDetails,status,topicDetails", "id": video_id, "key": get_api_key()})
    req = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/videos?{params}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            items = json.load(resp).get("items") or []
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise OSError(f"YouTube Data API unavailable: {type(e).__name__}") from None
    return items[0] if items else None


def validate_item(item) -> str:
    """Raise ApiError unless the Data API item describes a usable song; return its title."""
    if item is None:
        raise ApiError(400, "unavailable", "That video wasn't found, or it's private.")
    snippet = item.get("snippet", {})
    if (item.get("status", {}).get("privacyStatus") == "private"):
        raise ApiError(400, "unavailable", "That video wasn't found, or it's private.")
    if snippet.get("liveBroadcastContent", "none") != "none":
        raise ApiError(400, "live", "Live streams and premieres can't be processed.")

    duration = parse_iso_duration(item.get("contentDetails", {}).get("duration", ""))
    if not duration:
        raise ApiError(400, "unavailable", "Couldn't determine the video length.")
    if duration < MIN_SECONDS:
        raise ApiError(400, "too_short", f"That video is only {duration} seconds long.")
    if duration > MAX_SECONDS:
        raise ApiError(400, "too_long",
                       f"That video is {duration / 60:.1f} minutes long; the limit is {MAX_SECONDS // 60} minutes.")

    topics = item.get("topicDetails", {}).get("topicCategories", [])
    is_music = snippet.get("categoryId") == "10" or any(t.lower().endswith("music") for t in topics)
    if not is_music:
        raise ApiError(400, "not_music", "That doesn't look like a song (it isn't in YouTube's Music category).")
    return snippet.get("title") or ""


def check_video(video_id: str) -> None:
    try:
        validate_item(fetch_video_item(video_id))
    except OSError as e:
        # Fail open: the worker repeats the check with yt-dlp before doing any real work.
        log(f"data api check skipped: {e}")


# ── AWS helpers ───────────────────────────────────────────────────────────────

def find_instance():
    """Return (instance_id, state) of the worker instance, or (None, None)."""
    resp = client("ec2").describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [INSTANCE_TAG]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])
    for r in resp["Reservations"]:
        for i in r["Instances"]:
            return i["InstanceId"], i["State"]["Name"]
    return None, None


def ensure_started() -> str:
    """Start the worker if it is stopped. Returns the state seen before acting."""
    instance_id, state = find_instance()
    if state == "stopped":
        client("ec2").start_instances(InstanceIds=[instance_id])
        log(f"started {instance_id}")
    elif state is None:
        log("no worker instance found")
    return state


def queue_counts():
    a = client("sqs").get_queue_attributes(
        QueueUrl=QUEUE_URL,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def put_progress(job_id: str, state: dict) -> None:
    state["updated"] = now()
    client("s3").put_object(Bucket=BUCKET, Key=f"progress/{job_id}.json", Body=json.dumps(state).encode(),
                            ContentType="application/json", CacheControl="no-store")


def read_json(key: str):
    from botocore.exceptions import ClientError
    try:
        return json.load(client("s3").get_object(Bucket=BUCKET, Key=key)["Body"])
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


# ── Handlers ──────────────────────────────────────────────────────────────────

def process_handler(event, context):
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded") or len(raw) > MAX_BODY_BYTES:
            raise ApiError(400, "bad_request", "Invalid request.")
        try:
            body = json.loads(raw)
        except ValueError:
            raise ApiError(400, "bad_request", "Request body must be JSON.") from None
        video_id = extract_video_id(body.get("url") if isinstance(body, dict) else None)
        if not video_id:
            raise ApiError(400, "invalid_url", "Please paste a YouTube video link.")

        check_video(video_id)

        job_id = str(uuid.uuid4())
        meta = read_json(f"stems/{video_id}/meta.json")
        if meta:  # stems are still in S3: no need to wake the worker
            put_progress(job_id, {"job_id": job_id, "video_id": video_id, "status": "done", "percent": 100,
                                  "message": "Ready", "name": meta.get("name"), "stems": STEMS, "error": None})
            return respond(200, {"job_id": job_id, "video_id": video_id, "status": "done"})

        waiting, in_flight = queue_counts()
        if waiting + in_flight >= MAX_QUEUE_DEPTH:
            raise ApiError(429, "busy", "The service is busy right now. Please try again in a few minutes.")

        put_progress(job_id, {"job_id": job_id, "video_id": video_id, "status": "queued", "percent": 0,
                              "message": "Queued", "name": None, "stems": None, "error": None})
        client("sqs").send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps({"job_id": job_id, "video_id": video_id}))
        try:
            ensure_started()
        except Exception as e:  # the reconciler will retry within a minute
            log(f"start failed, reconciler will retry: {e}")
        return respond(202, {"job_id": job_id, "video_id": video_id, "status": "queued"})
    except ApiError as e:
        return error_response(e)
    except Exception as e:
        log(f"process failed: {type(e).__name__}: {e}")
        return error_response(ApiError(500, "internal_error", "Something went wrong. Please try again."))


def progress_handler(event, context):
    try:
        job_id = (event.get("pathParameters") or {}).get("job_id", "")
        if not JOB_ID_RE.match(job_id):
            raise ApiError(400, "bad_request", "Invalid job ID.")
        state = read_json(f"progress/{job_id}.json")
        if state is None:
            raise ApiError(404, "not_found", "Unknown or expired job.")

        if state.get("status") == "done":
            s3 = client("s3")
            video_id = state["video_id"]
            state["urls"] = {
                stem: s3.generate_presigned_url(
                    "get_object", Params={"Bucket": BUCKET, "Key": f"stems/{video_id}/{stem}.opus"},
                    ExpiresIn=URL_EXPIRY_SECONDS)
                for stem in STEMS}
            state["expires_in"] = URL_EXPIRY_SECONDS
        elif state.get("status") == "queued":
            _, server = find_instance()
            if server in ("stopped", "pending", "stopping"):
                state["message"] = "Waking up the processing server"
        return respond(200, state)
    except ApiError as e:
        return error_response(e)
    except Exception as e:
        log(f"progress failed: {type(e).__name__}: {e}")
        return error_response(ApiError(500, "internal_error", "Something went wrong. Please try again."))


def reconcile_handler(event, context):
    waiting, _ = queue_counts()
    state = ensure_started() if waiting > 0 else None
    log(f"reconcile: waiting={waiting} instance_state_seen={state}")
    return {"waiting": waiting, "state": state}
