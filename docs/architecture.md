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
