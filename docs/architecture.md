# Architecture Decision Log

Decisions made during development, with reasoning and trade-offs.

---

## Local-first processing

**Decision:** Run everything on the user's machine — no cloud server required.

**Reasoning:** Demucs is a large PyTorch model (~300 MB). Running it on a server requires meaningful compute (minutes of CPU time per song). For a personal learning tool, local processing is free, private, and fast enough. Cloud is deferred to a future version.

---

## Demucs htdemucs_6s (6-stem model)

**Decision:** Use `htdemucs_6s` over the standard 4-stem `htdemucs`.

**Reasoning:** The 6-stem model separates guitar and piano as independent stems, which is the core use case. The 4-stem model lumps guitar into "other", making isolation impossible. The processing time difference is negligible for this use case.

---

## All 6 stems loaded simultaneously

**Decision:** Load all 6 stems into the browser at startup rather than one at a time.

**Reasoning:** Switching stems required stopping and restarting audio, breaking playback continuity. Loading all stems in parallel and routing each through a `Tone.Gain` node into a single `Tone.PitchShift` allows instant mute/unmute toggling with no audio interruption. Memory cost (~480 MB WAV, ~24 MB Opus) is acceptable on a desktop browser.

**Audio graph:**
```
Player[guitar] → Gain → ─┐
Player[bass]   → Gain → ─┤
Player[drums]  → Gain → ─┼─→ PitchShift → Destination
Player[vocals] → Gain → ─┤
Player[piano]  → Gain → ─┤
Player[other]  → Gain → ─┘
```

---

## Opus audio format

**Decision:** Convert Demucs WAV output to Opus 128kbps before storing.

**Reasoning:** WAV stems are ~80 MB each (6 stems = ~480 MB per song). Opus at 128kbps is ~4 MB per stem (~24 MB per song) — a 20× reduction. Opus is lossless to human ears at practice volumes, natively decoded by all modern browsers via Web Audio API, and requires no changes to the player other than the file extension.

---

## Tone.js served locally, not from CDN

**Decision:** Download `tone.js` into the project directory and serve it from the local HTTP server.

**Reasoning:** The unpkg CDN URL for Tone.js was slow or blocked in the development environment. When a `<script>` tag fails to load, all JavaScript on the page silently fails to execute with no visible error. Serving the file locally eliminates this dependency entirely.

---

## Manual fetch bypass for Tone.Player.load()

**Decision:** Load audio via `fetch()` → `arrayBuffer()` → `decodeAudioData()` → `ToneAudioBuffer` rather than using `Tone.Player.load()`.

**Reasoning:** `Tone.Player.load(url)` consistently threw "could not load url" errors even when the server returned HTTP 200. Root cause was not fully diagnosed — likely related to internal URL resolution or response handling in Tone.js. The manual fetch approach is explicit, reliable, and gives control over error handling.

---

## JSON API endpoint for song metadata

**Decision:** Serve song name and stem list via a `/api/song` JSON endpoint rather than URL query parameters.

**Reasoning:** URL query parameters mangled song names containing special characters. `encodeURIComponent` and `URLSearchParams` decoded inconsistently, silently truncating values at characters like `(`. A JSON endpoint sidesteps encoding entirely and is more robust.

---

## ThreadedHTTPServer

**Decision:** Use `ThreadingMixIn + HTTPServer` instead of Python's default `HTTPServer`.

**Reasoning:** The default `HTTPServer` is single-threaded. When the browser loads 6 audio stems simultaneously, each request blocks the server until the previous one completes. `ThreadingMixIn` handles each request in its own thread, allowing parallel stem loading.

---

## Polling for progress updates

**Decision:** Browser polls `/api/progress` every 1.5 seconds during processing rather than using Server-Sent Events (SSE) or WebSockets.

**Reasoning:** Polling is simpler to implement and debug. At 1.5-second intervals the lag is imperceptible for a process that takes ~2 minutes. SSE would require keeping a persistent connection open, which complicates the threaded server model.

---

## isSeeking flag on seek bar

**Decision:** Skip `renderSeek()` updates while the user is dragging the seek bar.

**Reasoning:** The `tick()` loop calls `renderSeek()` on every animation frame, which sets `$seekBar.value` to the current playback position. This directly fights the user's drag — the slider snaps back to the playing position faster than the user can move it. Setting `isSeeking = true` on `pointerdown` and `false` on `change` (mouse release) prevents `tick()` from updating the slider during a drag.

---

## Ephemeral stem storage via IndexedDB

**Decision:** S3 acts as a short-lived handoff point only (pre-signed URL, ~1 hour expiry). The browser fetches stems from S3 once and stores them in IndexedDB permanently. The server never stores stems long-term.

**Reasoning:** The original design assumed stems were deleted when the user closed the tab. But browser memory (decoded AudioBuffers in `Tone.Player`) is already ephemeral — page close wipes it. S3 permanent storage would cost ~$0.023/GB/month indefinitely; combined with CloudFront transfer this is negligible per song (~20MB Opus) but grows unboundedly. The better model is: EC2 writes to S3, browser downloads and stores in IndexedDB, EC2 deletes from S3. On revisiting the same song, the browser skips the network entirely and loads from IndexedDB directly.

**Benefit for users:** Tab close and reopen no longer requires reprocessing. The song loads from local storage in seconds. This also eliminates the question of whether to require a login — the browser is the user's persistent identity.

**Trade-off:** IndexedDB is per-browser, per-device. Switching devices or browsers requires reprocessing. A future login system can back IndexedDB with S3 for cross-device sync.

---

## Frontend platform: webapp over browser extension or native iOS

**Decision:** Build the primary frontend as a responsive webapp (PWA). Do not build a browser extension or native iOS app for V1.

**Reasoning:**

- **Webapp:** Works on all devices (desktop, mobile, tablet). No install or store approval. Deployable to S3 + CloudFront. URL paste required, but this is acceptable — the user already navigated to the song.
- **Browser extension (Chrome Side Panel API):** Eliminates URL paste — auto-fills from the active YouTube tab. Significantly more convenient on desktop. But: no iOS support, requires Chrome Web Store approval and review, separate JS context from the page, cannot run on phones. Best reserved as a V2 enhancement for desktop power users.
- **iOS native (Swift/SwiftUI):** Best mobile UX. But: requires Apple Developer account ($99/year), App Store review (days to weeks), separate Swift codebase, yt-dlp cannot run in-browser so the server pipeline is unchanged. React Native or Flutter reduces duplication but adds framework overhead. Not justified until usage validates the need.

**Verdict:** Webapp first. Add Chrome extension later for desktop convenience. iOS only if mobile usage proves significant.

---

## AWS cloud architecture (planned)

**Decision:** Stop/start EC2 rather than always-on or terminate-per-job.

**Reasoning:**

- **Always-on GPU instance** (~$140/month): Achieves ~10s processing. Not justified at low usage volume.
- **Terminate per job**: Full cold boot on every request (~60–120s provisioning + OS boot). Slowest option.
- **Stop/start with pre-built AMI**: Instance disk persists between jobs. Resume takes ~30–60s (OS boot only, no reprovisioning). Demucs model weights cached on EBS. Total latency ~90–150s. Cost when idle: ~$1–2/month EBS only.

The stop/start approach gives acceptable latency at near-zero idle cost and remains the right balance until usage volume justifies an always-on instance.

**Planned stack:** S3 (stem handoff + progress files) + CloudFront (static site + CDN) + API Gateway + Lambda (orchestration) + SQS (job queue) + EC2 (processing) + IAM (permissions).

**Build order:**
1. S3 — upload stems manually, confirm browser loads them
2. CloudFront — HTTPS on custom domain
3. EC2 — run extraction pipeline manually on cloud instance. **Do a yt-dlp spike first** — YouTube often blocks datacenter IPs, which is the biggest unknown in the plan
4. IAM — grant EC2 write access to S3 and read/delete access to SQS
5. SQS + worker — Lambda enqueues jobs; the EC2 worker polls the queue, writes `progress/<job_id>.json` to S3, and stops the instance after the queue has been empty for a few minutes
6. Lambda + API Gateway — `/process` (enqueue + start EC2) and `/progress` endpoints
7. Auto stop/start — Lambda starts the instance on submission; the worker stops it on idle
8. Frontend — point `player.html` at API Gateway URL

---

## SQS job queue

**Decision:** Use SQS to hold song requests rather than passing jobs directly to the instance.

**Reasoning:** The instance is usually stopped, so a request often arrives while it is still booting. An SQS message survives the boot window with no custom retry logic. It also gives the worker a natural idle signal for self-shutdown: an empty queue for N minutes means stop. Failed jobs return to the queue after the visibility timeout (a dead-letter queue can catch repeated failures).

---

## No DynamoDB for job progress

**Decision:** Drop DynamoDB. The worker writes progress as a small JSON object in S3 (`progress/<job_id>.json`), and the browser polls it via `/progress`.

**Reasoning:** Progress is a single short-lived record per job with no query needs, so a database is unnecessary. S3 is already in the stack. Before the worker has written anything, `/progress` reports "starting" by checking the EC2 instance state. If multi-user job history or per-user rate limiting is added later, DynamoDB can be introduced then.

---

## S3 for handoff, EBS as processing cache

**Decision:** Keep S3 as the handoff to the browser. Keep processed stems on the EBS volume as a cache, not as the serving location.

**Reasoning:** The instance is stopped most of the time, so serving stems from EBS directly would require booting it for every download. It would also need a stable public IP (Elastic IP cost) and TLS on the instance, because the HTTPS player page cannot fetch from a plain-HTTP origin. S3 pre-signed URLs avoid all of this at negligible cost (~20 MB per song, deleted after download).

EBS as a cache is still worthwhile: if a requested song (keyed by video ID) is already on disk, skip Demucs and re-upload to S3. This saves compute and costs ~$0.08/GB/month with no extra service.

---

## yt-dlp from EC2: spike findings (2026-10-11)

**Finding:** YouTube blocks yt-dlp from the EC2 datacenter IP. Every attempt on a `t3.micro`/`t3.small` in `ap-southeast-2` failed with `Sign in to confirm you're not a bot`, for three different videos, using yt-dlp 2026.08.19 and Deno 2.9.7. Search and metadata lookups resolved, so connectivity was fine; the block is on video extraction.

**Tried and ruled out:**

- **Installing Deno** (the earlier 403 fix): Deno was present and working; not the cause.
- **PO token provider** (`bgutil-ytdlp-pot-provider` 2.0.2, Deno script mode, source reviewed before running): loaded correctly, but yt-dlp never requested a token. It used the `visionos` client, which does not use PO tokens, and was rejected by the bot check first. The block is IP reputation, not a missing token.
- **Cloudflare WARP:** not tested. Cloudflare's package repo has no Amazon Linux 2023 build. Judged a long shot (WARP addresses are widely known and often challenged too).

**Verified (spike 3):** with a cookies file from a throwaway Google account, yt-dlp resolved all three test songs and downloaded a full audio stream from the EC2 datacenter IP. **Decision:** use yt-dlp cookies from a throwaway Google account, stored in AWS Secrets Manager and read by the worker role. Cookies expire and the account may be flagged, so they need occasional refreshing; the worker must report a clear error when extraction fails. Fallback if this proves too fragile: a pay-per-GB residential proxy.

**Rejected:** routing through the user's laptop (as downloader or proxy) and client-side download/upload, because the tool should not depend on a personal machine being on.

**Resources created during the spike (kept; free):** IAM role and instance profile `learn-that-song-worker` (currently only `AmazonSSMManagedInstanceCore`), security group `sg-05e9c05fecb8015ff` (no inbound rules) in the default VPC, AMI used: Amazon Linux 2023 (`ami-083a7457bdb2d0548`). Test instances were reachable via SSM Session/Run Command with no open ports and have been terminated.

**Cookie storage:** secret `learn-that-song/yt-cookies` in Secrets Manager (`ap-southeast-2`). Only the `.youtube.com`, `.google.com` and `accounts.google.com` cookies are stored, not the Gmail/Contacts/Workspace ones in a raw browser export. The `learn-that-song-worker` role has an inline policy `read-yt-cookies` allowing `secretsmanager:GetSecretValue` on that one secret only. The worker should write the cookies to a `0600` temp file, run yt-dlp with `--cookies`, and delete the file after use. Refresh: export again from a private window (open `youtube.com/robots.txt`, export, close the window immediately) and `put-secret-value`.

---

## Full pipeline run on EC2 (2026-10-11)

**Result:** the whole pipeline works on a fresh instance with no manual fixes. Song: Slipknot - Three Nil (289 s). Instance: `c6i.xlarge` (4 vCPU, 8 GB), 30 GB gp3 root volume, Amazon Linux 2023, Python 3.11, PyTorch 2.5.1 (CPU) + Demucs, static ffmpeg 7.0.2 (AL2023 has no ffmpeg package; the johnvansickle static build includes libopus), yt-dlp standalone binary + Deno, cookies from Secrets Manager.

| Stage | Time |
|---|---|
| yt-dlp download + WAV | ~9 s |
| Demucs `htdemucs_6s` | ~121 s (peak RSS 3.0 GB) |
| Opus conversion, sequential | ~53 s |
| Opus conversion, 4 in parallel | ~23 s |
| Upload 6 stems (24 MB) to S3 | ~1 s |
| **Total, sequential Opus** | **182 s** |

Output sizes matched the local Windows run (4.6/4.2/4.3/4.0/4.2/3.8 MB). Add ~30-60 s for instance boot until SSM/worker is ready.

**Sizing notes:**

- Demucs gave identical wall time with all 4 vCPUs and with the process pinned to 2 (`taskset`), so it does not scale past ~2 threads on this hardware. A 2 vCPU `c6i.large` (4 GB, ~$0.085/h) is therefore plausible, but pinning on a 4 vCPU box is not proof. Re-test on a real `c6i.large` before choosing it.
- Peak memory is 3.0 GB for a ~5 minute song and grows with song length. 4 GB leaves little headroom for long songs; either use 8 GB or cap song length (e.g. reject videos over ~7 minutes).
- Cost is negligible either way: ~3 minutes of `c6i.xlarge` is about 1 cent per song. A stopped instance costs only its EBS volume, regardless of instance type.
- Worker should convert Opus in parallel (`xargs -P`), saving ~30 s.
- Model weights (~300 MB) download on first run; baking them into the AMI or keeping them on the EBS volume avoids that.

**IAM state of `learn-that-song-worker`:** `AmazonSSMManagedInstanceCore`; inline `read-yt-cookies` (GetSecretValue on the one secret); inline `write-stems` (PutObject on `learn-that-song-s3/stems/*` only). Still to add for the queue worker: SQS receive/delete, and S3 write on `progress/*`.

---

## Worker AMI `learn-that-song-worker-v1` (2026-10-11)

**AMI:** `ami-02a2a74958cd1698b` (`ap-southeast-2`, snapshot `snap-0ebef0f848c05414c`, 30 GB volume, ~3 GB used, so snapshot cost is roughly $0.15/month). Built from Amazon Linux 2023 on a `c6i.xlarge`.

**Contents (versions recorded in `/opt/worker/VERSIONS.txt`, pip freeze in `/opt/worker/pip-freeze.txt`):**

- Python 3.11 venv at `/opt/venv` (PyTorch 2.5.1 CPU, Demucs, soundfile)
- Demucs `htdemucs_6s` weights (only ~53 MB with the current Demucs, fetched via the Hugging Face hub) pre-downloaded to `/opt/hf`; set `HF_HOME=/opt/hf TORCH_HOME=/opt/torch HF_HUB_OFFLINE=1` so nothing is fetched at runtime
- `/usr/local/bin`: static ffmpeg 7.0.2 (with libopus), yt-dlp standalone binary, Deno
- AWS CLI v2 (preinstalled on AL2023); SSM agent
- Unprivileged system user `worker` (home `/var/lib/worker`) that should run the job; the instance role supplies AWS credentials via IMDSv2
- Not baked in: the worker script (to be fetched from S3 at boot so code changes do not need a re-bake) and cookies (read from Secrets Manager per job)

**Verified from a cold launch of the AMI (`c6i.xlarge`), running as `worker`:**

| Step | Time |
|---|---|
| Launch to SSM ready | 23 s |
| yt-dlp download + WAV (with cookies) | 11 s |
| Demucs | 142 s |
| Opus, 6 parallel on 4 vCPU | 22 s |
| Upload to S3 | 1 s |
| **Job total** | **176 s** |

Notes: Demucs was slower than the 121 s earlier (variance or cold caches; re-measure). Parallel Opus took 22 s, not the ~13 s a perfect 4x split would give: the 4 vCPUs are hyperthreads on 2 physical cores, so 4-wide and 6-wide gave about the same result. Expect roughly 3.3 minutes from request to stems on a stopped-then-started instance (about 25-60 s of boot plus the job). Stems matched earlier output sizes. A stray test upload was cleaned up afterwards.

**Rebuilding:** launch AL2023 with a 30 GB gp3 root, install the above, run a Demucs smoke test as `worker`, clean caches, `create-image`. The model must be pre-downloaded with `HF_HOME=/opt/hf`, not the default home directory (a first attempt cached it under `/root` where `worker` could not read it).

---

## Queue worker (step 5) (2026-10-11)

**Components:** SQS queue `learn-that-song-jobs` (15 min visibility, 20 s long polling, 1 day retention, SSE on, `maxReceiveCount` 2) with dead-letter queue `learn-that-song-jobs-dlq` (14 day retention). Worker code lives in `worker/` in this repo: `worker.py` (the job loop), `run.sh` (boot wrapper), `learn-worker.service` (systemd unit), `test_worker.py` (unit tests, `python worker/test_worker.py`). `worker.py` is uploaded to `s3://learn-that-song-s3/worker/` and fetched on every boot by `run.sh`, so code changes do not need an AMI rebuild: edit, upload, and the next boot runs it. `run.sh` and the unit file are baked into the AMI.

**Job message:** `{"job_id": "<8-64 chars [A-Za-z0-9-]>", "video_id": "<11 chars [A-Za-z0-9_-]>"}`. The worker builds the canonical `watch?v=` URL itself; user-supplied URLs never reach yt-dlp. Malformed messages are logged and dropped.

**Flow per job:** cache check (EBS, keyed by video ID) -> yt-dlp metadata check (`validate_info`) -> download -> Demucs (progress parsed from its progress bar) -> parallel Opus -> cache -> upload to `stems/<video_id>/` -> progress `done`. Progress is written to `progress/<job_id>.json` (statuses: starting, validating, downloading, separating, encoding, uploading, done, error, retrying; fields: percent, message, name, stems, error{code,message}).

**Validation rules (second layer; the first is URL/ID parsing in the future Lambda):** reject live/upcoming videos, under 30 s, over 8 minutes, or not in YouTube's Music category unless artist/track metadata is present. Error codes: `live`, `too_short`, `too_long`, `not_music`, `unavailable`, `extract_failed`, `service_unavailable` (bot check; the worker logs `COOKIES_EXPIRED` for alerting; the user message does not mention cookies), `internal_error`.

**Failure handling:** rejections (`JobError`) write an error progress file and delete the message. Unexpected failures retry once (visibility 30 s); on the second failure the worker writes `internal_error` and sets visibility 0 so the next receive redrives the message to the DLQ. Job directories (and the cookie file, deleted right after download) are removed in a `finally`.

**Idle and safety shutdown:** after 5 minutes with an empty queue the worker exits 42 and `run.sh` runs `shutdown -h now` (the instance stops; no `ec2:StopInstances` permission is needed). Also shut down after 5 consecutive crashes, if worker code cannot be fetched, or after 3 hours of uptime (watchdog).

**EBS cache:** `/var/lib/worker/cache/<video_id>/` holds the six Opus files plus `meta.json`; oldest entries are evicted beyond 10 GB.

**IAM (`learn-that-song-worker`):** inline policies `read-yt-cookies`, `write-stems` (`PutObject` on `stems/*`), and `worker-queue-progress-code` (SQS receive/delete/change-visibility/get-attributes on the jobs queue only; `PutObject` on `progress/*`; `GetObject` on `worker/*`). Not yet granted: `PutSecretValue` for cookie write-back, SNS publish for alerts.

**AMI `learn-that-song-worker-v2` (`ami-0ab17f231d0df0a7f`):** v1 plus boto3, `/opt/worker/run.sh`, and `learn-worker.service` (enabled). v1 (`ami-02a2a74958cd1698b`) is kept as a fallback.

**End-to-end test (c6i.xlarge from the AMI, no manual setup):** three queued messages (one malformed, one 19 s video, Three Nil). Result: Three Nil done 198 s after launch (about 23 s boot, 150 s separation, then encode and upload); the 19 s video rejected with `too_short`; malformed message dropped; both queues empty; instance stopped itself about 5 minutes after the last job. A later `start-instances` of the stopped instance with a repeat request for the same song finished in **14 s** (service restarted by itself, cache hit, re-upload).

**Lesson:** an AMI taken with `--no-reboot` from a running instance captured files with zero length (data not yet flushed), producing a corrupt image. Always `stop` the builder before `create-image` (or reboot), and verify with checksums.

---

## Public API (step 6) (2026-10-11)

**Endpoint:** `https://x3g1l5yblk.execute-api.ap-southeast-2.amazonaws.com` (HTTP API, `$default` stage, stack `learn-that-song-api`). Deploy/update with `bash infra/deploy.sh` (zips `api/handler.py`, uploads it to `s3://learn-that-song-s3/lambda/`, runs `aws cloudformation deploy` on `infra/api.yaml`). Remove with `aws cloudformation delete-stack --stack-name learn-that-song-api`. Unit tests: `python api/test_handler.py`.

**Routes**

- `POST /process` body `{"url": "<youtube link>"}`. Returns `202 {job_id, video_id, status:"queued"}`, or `200 {..., status:"done"}` if the stems are still in S3 (`stems/<id>/meta.json` exists), or an error `{"error": {"code", "message"}}`: 400 `invalid_url`, `bad_request`, `unavailable`, `live`, `too_short`, `too_long`, `not_music`; 429 `busy`; 500 `internal_error`.
- `GET /progress/{job_id}` returns the worker's progress JSON. When `status` is `done` it adds `urls` (pre-signed GET URL per stem, 1 hour) and `expires_in`. While `queued` and the instance is not running, the message reads "Waking up the processing server". 404 for unknown/expired jobs.

**`/process` order of checks:** body size/JSON -> `extract_video_id` (only youtube.com, m., music., youtu.be, shorts/embed/live paths; or a bare 11-char ID; the canonical URL is rebuilt by the worker) -> YouTube Data API v3 lookup (`videos.list`, 1 quota unit; category Music or a Music topic, not live, 30 s to 8 min, not private; fails open on API outage because the worker re-checks) -> S3 `meta.json` shortcut -> queue depth cap (10) -> write `queued` progress, enqueue, start instance if stopped.

**Reconciler:** a Lambda runs every minute; if the queue has waiting messages and the worker is `stopped`, it starts it. This covers a request arriving while the instance is `stopping` (a start call fails then).

**Security properties:** each Lambda has its own role. `ec2:StartInstances` is limited to instances tagged `Name=learn-that-song-worker`; S3 access is limited to `progress/*` (write), `stems/*/meta.json` (read), `stems/*` (read, for signing); the Data API key lives in SSM Parameter Store as a SecureString (`/learn-that-song/youtube-api-key`), restricted to the YouTube Data API v3 in Google Cloud (no application restriction possible from Lambda). API Gateway throttles at 5 req/s (burst 10). CORS allows only the CloudFront origin and `http://localhost:8080`. Error responses never include internal details. Because S3 returns AccessDenied instead of NoSuchKey for missing keys without `s3:ListBucket`, the roles hold list permission scoped to the `stems/` and `progress/` prefixes.

**Worker instance:** persistent, launched from AMI v2, tagged `Name=learn-that-song-worker`, `Project=learn-that-song`; it sits stopped between jobs (only its 30 GB gp3 disk bills, about $2.40/month).

**Verified end to end through the public API:** rejections (non-YouTube link, non-JSON body, 19 s video, nonexistent video, unknown job, CORS preflight allowed for the CloudFront origin and denied for another). A real request for Three Nil woke the stopped instance and was ready in **200 s** (queued -> validating -> downloading -> separating with live percent -> encoding -> done). The pre-signed stem URL downloaded the full 4.3 MB file with correct CORS headers; changing the object name in a signed URL returned 403. A repeat request returned `done` immediately without waking the worker. Lambda logs showed no Data API fail-open events.

**Not yet verified / caveats:** pre-signed URLs are signed with the Lambda role's temporary credentials and cannot outlive them, so some may expire in under an hour; check this when wiring the frontend. The reconciler's start path has only run in unit tests and with an empty queue.
