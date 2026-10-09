# Backlog

Future improvements, ordered roughly by priority within each section.

---

## Client

- **IndexedDB stem caching** *(done; stale-cache invalidation on re-process still open)* — store decoded Opus bytes in the browser after first fetch. Subsequent loads skip the network entirely. Key: `songName/stem`. See architecture.md for full decision rationale.
- **PWA / Service Worker** — cache static assets (HTML, JS, tone.js) so the app shell loads offline. Stems already handled by IndexedDB; this covers the player UI itself. Makes the app installable to home screen on both Android and iOS.
- **Per-song settings memory** — remember the last-used pitch and tempo for each song name in localStorage. Restored on load.
- **Song library** — list all IndexedDB-cached songs in the player so users can switch without re-pasting a URL. Delete individual songs from cache.
- **Web Worker for audio decoding** — push `fetch → arrayBuffer → decodeAudioData` into a Web Worker to keep the UI thread free during loading. Only worth it if 6-stem parallel loading causes visible jank.
- **Waveform visualisation** — render a static waveform from the guitar stem's AudioBuffer. Replaces the plain seek bar with a scrollable waveform view.

---

## Backend (AWS)

See architecture.md for the full decision log and build order.

1. S3 — bucket + CORS config; upload one song manually; confirm browser loads stems from S3 URL
2. CloudFront — HTTPS on custom domain; serve `player.html` and stems via CDN
3. EC2 — launch instance; install Python + yt-dlp + Demucs + ffmpeg; run extraction pipeline manually; confirm stems land in S3
4. IAM — role for EC2 with S3 write + DynamoDB write; no hardcoded keys
5. DynamoDB — `jobs` table; replace in-memory `_job` dict with DynamoDB item; `job_id` as partition key
6. Lambda + API Gateway — `/process` (start job) and `/progress/:job_id` endpoints; Lambda polls DynamoDB
7. Auto stop/start — EC2 stops itself after job completes; Lambda starts it when a new job is submitted; SQS queue absorbs submissions while instance is starting
8. Frontend swap — point `player.html` at API Gateway URL; remove local `player.py` dependency

---

## Desktop extension

- **Chrome Side Panel** — extension that opens the player in Chrome's side panel on YouTube pages; auto-fills the URL from the active tab; eliminates copy-paste for desktop users. Requires Chrome Web Store submission.

---

## Future / speculative

- **User login** — authenticate with Google; back IndexedDB with S3 for cross-device stem sync; show library across devices.
- **iOS app** — native Swift/SwiftUI client once mobile usage justifies it. Same AWS backend; no change to processing pipeline.
- **WebAssembly Demucs** — run source separation in-browser via WebGPU. Eliminates the server entirely. Not production-ready yet; worth revisiting as WebGPU matures.
- **Collaborative annotations** — shared loop markers and notes on a song (requires login).
