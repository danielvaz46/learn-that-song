# Backlog

What is done and what is next. Decisions and measurements are in `architecture.md`; problems and fixes are in `bugs.md`.

Live at https://daniel-vaz.com/learnthatsong/

---

## Done

| Area | What | Where to read more |
|---|---|---|
| Hosting | Private S3 stems bucket (`learn-that-song-s3`, 1-day expiry on `stems/` and `progress/`) and private site bucket behind CloudFront | architecture.md |
| Domain | `daniel-vaz.com` on Route 53, wildcard HTTPS certificate, CloudFront, security headers; app at `/learnthatsong/` | "Custom domain" |
| Worker | `c6i.xlarge` from AMI v2, SQS queue + dead-letter queue, yt-dlp (cookies from Secrets Manager) -> Demucs -> AAC `.m4a` -> S3; stops itself when idle; reloads new code from S3 on its own | "Queue worker", "Stems are AAC", "Worker reloads" |
| API | `POST /process`, `GET /progress/{id}`, reconciler that wakes the worker; YouTube Data API v3 pre-check (song, 30 s-8 min, not live) | "Public API" |
| IAM | Scoped roles per Lambda and for the worker; no keys on the instance | architecture.md |
| Frontend | Player talks to the API, IndexedDB cache (instant and offline for songs you have loaded), progress bar, expired-link refresh, iOS playback-session fix | "Frontend swap" |
| Spending guard | $5 monthly budget over this project's services, email alerts, automatic kill switch (deny policy + stop worker); `infra/resume_services.sh` undoes it | "Spending guard" |
| Tests | API (24), worker (21), CloudFront router (7), live browser end-to-end (22) | README |

---

## Before sharing publicly (LinkedIn)

1. **Demo songs** — pre-process a handful of popular songs and keep them permanently in S3 (separate prefix, no 1-day expiry) with "Try a demo" buttons, so a first-time visitor never waits on the pipeline or YouTube. This is the most important protection against a traffic spike.
2. **Per-visitor limits** — the throttle is global, so one visitor can fill the 10-job queue and lock everyone else out. Add a per-IP counter (for example DynamoDB with a TTL, free tier) in `/process`: a few new songs per hour per IP.
3. **Queue position and wait estimate** in the progress UI, and a clearer "busy" message.
4. **Confirm the mobile fixes on real phones** — AAC stems on iPad (the earlier "Decoding failed"), and sound on iPhone (the silent-switch fix). If an iPad still fails to load, memory is the next suspect (see "iOS memory headroom").

## Keeping it running

5. **Cookie maintenance** — write refreshed cookies back to Secrets Manager after each job (needs `PutSecretValue`), and email an alert when YouTube starts rejecting them. Re-exporting cookies from the burner account is still manual: private window, open `youtube.com/robots.txt`, export, close the window immediately.
6. **Prove the budget actions end to end** — a throwaway stack with a $0.01 limit pointing at dummy roles and a spare instance would show AWS really runs both actions. Also check the domain charge under Billing, and raise the limit (`bash infra/deploy_budget.sh <email> 8`) if October trips it because of build-time costs.
7. **YouTube rate limits** — one cookie account and one datacenter IP are the realistic failure point under load, not AWS cost.

## Security review

- Replace the full-access IAM user keys on the development laptop with scoped credentials; rotate or delete the long-lived keys in `~/.aws/credentials`.
- Pre-signed URLs: expiry length and leak surface (logs, history, referrers).
- Add a Content-Security-Policy header (the page uses an inline script, so this needs thought).
- The worker executes whatever is in `s3://learn-that-song-s3/worker/worker.py`; confirm only the owner can write that prefix.
- `/process` abuse beyond the per-IP limit above; confirm URL parsing and yt-dlp invocation cannot be injected (unit-tested, review again).
- EC2: no inbound security group (done), IMDSv2 (done), patching cadence, yt-dlp/Demucs supply chain.
- Copyright and YouTube terms-of-service exposure of downloading and hosting separated audio, and what happens if the burner account is banned.
- Decide whether the GitHub repository stays public (it contains the AWS account ID in two files, which is not a secret).

## Cleanup

- Delete the raw cookie exports in `Downloads` (`cookies.txt`, `www.youtube.com_cookies.txt`) and the API key file in `Projects\Credentials` if the copy is not needed.
- Delete the unused v1 AMI (`ami-02a2a74958cd1698b`) and its snapshot once v2 has proven stable.
- Remove the legacy local app (`player.py`; `extract_guitar.py` still works standalone) if it is no longer wanted.
- Remove the `/` -> `/learnthatsong/` redirect in `infra/site.yaml` when the portfolio home page exists.
- Add the planned tribute to the user's grandmother somewhere on the site (see memory notes; do not add unprompted).

## Open technical loose ends

- **iOS memory headroom** — six decoded stems are about 600 MB for a 5-minute song, which can exceed a tab's limit on smaller iPads. Options if it bites: decode fewer stems at once, lower the sample rate, or stream from media elements.
- The 8-minute cap's memory use has only been measured at 5 minutes (3.0 GB peak on an 8 GB instance); test a 7-8 minute song.
- Demucs with `-j` on a 2xlarge was never tried; the current 150 s separation is acceptable.
- Pre-signed URLs are signed with the Lambda's temporary credentials and may expire in under their advertised hour; the player refreshes links on a 403, which covers it.
- The reconciler's "start the instance while it is stopping" race has not been reproduced.
- One browser end-to-end run in five failed once for an unidentified reason and has not recurred in four further runs; re-run if the suite looks flaky.

---

## Client features (later)

- **Per-song settings memory** — remember pitch and tempo for each song (localStorage), restored on load.
- **Song library** — list the songs cached in IndexedDB, switch between them without re-pasting a link, delete individual songs.
- **PWA / service worker** — cache the app shell so the UI loads offline and can be installed to the home screen.
- **Waveform visualisation** — render a waveform from the guitar stem instead of the plain seek bar.
- **Web Worker for audio decoding** — only if loading six stems causes visible jank.
- **Stale-cache invalidation** — cached stems are keyed by video ID only; re-processing a song never refreshes a browser's copy.

## Later / speculative

- **Chrome side panel extension** that opens the player on YouTube pages and fills in the link.
- **User login** (Google) with stems synced across devices.
- **iOS app** if mobile use justifies it.
- **WebAssembly/WebGPU Demucs** to remove the server, once browser support matures.
- **Collaborative annotations** (shared loop markers and notes; needs login).
- **CI/CD** — deploy on push from GitHub Actions (needs AWS credentials stored in GitHub, so only after the security review).
