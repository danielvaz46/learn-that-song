# Learn That Song

Paste a YouTube link and get the song split into six instrument stems (guitar, bass, drums, vocals, piano, other) that you can mute individually, slow down, transpose, and loop, all in the browser. Built for learning songs by ear: mute the guitar to play along, or isolate it to hear exactly what's being played.

**Live:** https://db3ggl735zwq2.cloudfront.net

## How it works

```
Browser (CloudFront + S3)                 AWS (ap-southeast-2)
  player.html  ──POST /process──►  API Gateway ─► Lambda: validate link, YouTube Data API check
       │                                              │ queue job, wake the worker
       │                                              ▼
       │                                         SQS queue ─────────► EC2 worker (stopped when idle)
       │                                                               yt-dlp ─► Demucs htdemucs_6s
       ├──GET /progress/{job}──► Lambda ◄── progress/<job>.json (S3)    ─► Opus ─► S3 stems/<video>/
       │                                                               shuts itself down after 5 idle minutes
       └──signed S3 links──► stems downloaded once, kept in IndexedDB (instant and offline afterwards)
```

- **Player** (`player.html`): Tone.js audio engine with per-stem mute, tempo 25-150%, pitch ±12 semitones, A/B loop, and a seek bar. A song you have loaded before opens straight from the browser's IndexedDB with no network.
- **API** (`api/`, `infra/api.yaml`): `POST /process` and `GET /progress/{job_id}` on API Gateway + Lambda. Validates links, checks the video is a song of 30 s to 8 min through the YouTube Data API, answers instantly if the stems are still in S3, otherwise queues a job and starts the worker.
- **Worker** (`worker/`): runs on a `c6i.xlarge` from a prebuilt AMI. A job takes about 3 minutes for a 5-minute song and about 1 cent of compute. The instance stops itself when the queue is empty, so idle cost is only its 30 GB disk.
- **Stems are temporary on the server**: S3 keeps them for a day as a handoff; the browser keeps them permanently.

## Repo layout

| Path | What |
|---|---|
| `player.html`, `tone.js` | The web app (deployed to S3 + CloudFront) |
| `api/handler.py` | Lambda functions: process, progress, reconcile |
| `infra/api.yaml`, `infra/deploy.sh` | CloudFormation template and deploy script for the API |
| `worker/worker.py`, `run.sh`, `learn-worker.service` | Queue worker, boot wrapper, systemd unit (AMI contents are described in the architecture doc) |
| `tests/e2e_player.mjs` | Browser end-to-end test against the live site (headless Chrome) |
| `docs/architecture.md` | Decision log with reasoning, measurements, and the AWS resources in use |
| `docs/bugs.md` | Problems hit along the way, with root causes |
| `docs/backlog.md` | What's next, including the security review |
| `extract_guitar.py`, `player.py` | The original local-only version (see below) |

## Development

```
python api/test_handler.py          # API unit tests
python worker/test_worker.py        # worker unit tests
node tests/e2e_player.mjs           # live browser test (Node 22+, Chrome)
bash infra/deploy.sh                # package and deploy the API stack
```

Update the site: upload `player.html` to the site bucket and invalidate CloudFront. Update the worker: upload `worker/worker.py` to `s3://learn-that-song-s3/worker/`; the next boot picks it up. To run the page locally, serve this folder on port 8080 (`python -m http.server 8080`); that origin is allowed by the API's CORS rules.

## Original local version

`extract_guitar.py` still works on its own: it downloads a song with yt-dlp, runs Demucs, and writes Opus stems to `output/`. Requirements: ffmpeg, Deno, and `pip install yt-dlp demucs librosa soundfile`.

```
python extract_guitar.py "https://youtube.com/watch?v=..." [--pitch -2] [--tempo 0.8]
```

`player.py` served the old local app and is no longer used by `player.html`, which now talks to the hosted API.
