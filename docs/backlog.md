# Backlog

Future improvements, ordered roughly by priority within each section.

---

## Client

- **IndexedDB stem caching** *(done; stale-cache invalidation on re-process still open)* — store decoded Opus bytes in the browser after first fetch. Subsequent loads skip the network entirely. Key: `songName/stem`. See architecture.md for full decision rationale.
- **PWA / Service Worker** — cache static assets (HTML, JS, tone.js) so the app shell loads offline. Stems already handled by IndexedDB; this covers the player UI itself. Makes the app installable to home screen on both Android and iOS.
- **Per-song settings memory** — remember the last-used pitch and tempo for each song name in localStorage. Restored on load.
- **Song library** — list all IndexedDB-cached songs in the player so users can switch without re-pasting a URL. Delete individual songs from cache.
- **Web Worker for audio decoding** — push `fetch → arrayBuffer → decodeAudioData` into a Web Worker to keep the UI thread free during loading. Only worth it if 6-stem parallel loading causes visible jank.
- **Demo mode (pre-processed songs)** — ship a small set of already-processed songs that load instantly with no YouTube dependency, so a first-time visitor (e.g. a recruiter) never hits a failed download. "Process a new YouTube URL" becomes the advanced option, with a clear message if extraction fails. Stems for the demo set live permanently in S3 (not subject to the 1-day expiry; use a separate `demo/` prefix) and are served via CloudFront or pre-signed URLs. Motivation: portfolio showcase; see the yt-dlp datacenter-IP findings in architecture.md.
- **Waveform visualisation** — render a static waveform from the guitar stem's AudioBuffer. Replaces the plain seek bar with a scrollable waveform view.

---

## Backend (AWS)

See architecture.md for the full decision log and build order.

1. S3 — bucket + CORS config; upload one song manually; confirm browser loads stems from S3 URL *(done: `learn-that-song-s3`, ap-southeast-2; test song `3deDNMr12rQ`; browser playback test pending)*
2. CloudFront — HTTPS on custom domain; serve `player.html` via CDN *(part 1 done: distribution `E11KOV68D9DUS5` at `db3ggl735zwq2.cloudfront.net`, private site bucket `learn-that-song-s3-site` via OAC; stems stay on pre-signed S3 URLs. Custom domain + ACM cert still to do. Site updates: re-upload then `aws cloudfront create-invalidation`)*
3. EC2 — launch instance; install Python + yt-dlp + Demucs + ffmpeg; run extraction pipeline manually; confirm stems land in S3. Spike yt-dlp from the datacenter IP first (YouTube blocking risk) *(done: full pipeline verified, 182 s for a 5-minute song; see architecture.md. Remaining: bake AMI with deps + model, parallel Opus, record video ID, cap song length)*
4. IAM — role for EC2 with S3 write + SQS receive/delete; no hardcoded keys
5. SQS + worker — queue for song requests; worker polls it, writes `progress/<job_id>.json` to S3 (replaces in-memory `_job` dict, no DynamoDB), skips Demucs if the song is already cached on EBS, and stops the instance after the queue is idle
6. Lambda + API Gateway — `/process` (enqueue job + start EC2) and `/progress/:job_id` (read S3 progress file; report "starting" from EC2 state if absent)
7. Auto stop/start — Lambda starts the instance on submission; SQS absorbs jobs while it boots; worker self-stops on idle
8. Frontend swap — point `player.html` at API Gateway URL; remove local `player.py` dependency

---

## Security review (after hosting is complete)

Detailed review once steps 1-8 are live. Known items to cover:

- Replace the full-access IAM user keys with scoped roles (EC2 instance role, Lambda execution roles); rotate or delete the long-lived keys in `~/.aws/credentials`
- Pre-signed URL exposure: expiry length, leak surface (logs, browser history, referrers)
- S3 bucket policy and CORS: tighten origins to the final domain; confirm Block Public Access stays on
- `/process` abuse: auth or rate limiting, input validation on the YouTube URL (SSRF, command injection into yt-dlp), cost caps on EC2/SQS
- API Gateway/Lambda: CORS, throttling, least-privilege policies
- EC2: security group (no inbound), IMDSv2, patching, yt-dlp/Demucs supply chain
- Billing alarms and budget limits
- Copyright/ToS exposure of downloading and hosting separated YouTube audio

---

## Desktop extension

- **Chrome Side Panel** — extension that opens the player in Chrome's side panel on YouTube pages; auto-fills the URL from the active tab; eliminates copy-paste for desktop users. Requires Chrome Web Store submission.

---

## Future / speculative

- **User login** — authenticate with Google; back IndexedDB with S3 for cross-device stem sync; show library across devices.
- **iOS app** — native Swift/SwiftUI client once mobile usage justifies it. Same AWS backend; no change to processing pipeline.
- **WebAssembly Demucs** — run source separation in-browser via WebGPU. Eliminates the server entirely. Not production-ready yet; worth revisiting as WebGPU matures.
- **Collaborative annotations** — shared loop markers and notes on a song (requires login).
