# Bug Log

Issues encountered during development, with root cause and fix.

---

## Tone.js CDN blocked page execution

**Symptom:** Page loaded but showed only initial HTML. No JavaScript executed. No console errors visible.

**Root cause:** The `<script src="https://unpkg.com/tone">` tag failed silently — the CDN was slow or blocked in the development environment. When a script tag fails to load, the browser skips all subsequent JavaScript without throwing a visible error.

**Fix:** Downloaded `tone.js` (v14.8.49) locally and served it from the project's own HTTP server. Replaced CDN `<script>` with `<script src="tone.js">`.

---

## Tone.Player.load() threw "could not load url"

**Symptom:** `Init error: Error: could not load url: output/htdemucs_6s/.../guitar.wav` even though the server returned HTTP 200 for the file.

**Root cause:** Tone.js internal URL loading is unreliable — likely related to how it resolves relative URLs or handles the fetch response internally. Root cause not fully confirmed.

**Fix:** Bypassed `Tone.Player.load()` entirely. Load audio manually:
```javascript
fetch(url)
  → response.arrayBuffer()
  → rawCtx.decodeAudioData(arrayBuffer)
  → new Tone.ToneAudioBuffer(audioBuffer)
  → new Tone.Player(toneBuffer)
```

---

## await Tone.start() hung forever at page load

**Symptom:** Page initialisation froze indefinitely. `init()` never completed.

**Root cause:** `await Tone.start()` internally tries to resume the Web Audio `AudioContext`. Chrome's autoplay policy blocks AudioContext resume until a user gesture has occurred. Calling it at page load — before any click — causes it to wait forever for a gesture that never comes in that context.

**Fix:** Moved `await Tone.start()` out of `init()` and into `startAudio()`, which is called only from the play button's click handler.

---

## loadStem() silently failed — all JavaScript stopped working

**Symptom:** After adding `loadStem`, the entire page stopped functioning. No stems loaded, no buttons responded, no errors shown.

**Root cause:** `loadStem` used `await` inside a non-`async` function. This is a syntax error in strict mode and causes the browser to abort parsing the entire script block, silently dropping all subsequent JavaScript.

**Fix:** Added the `async` keyword to the `loadStem` function declaration.

---

## URL parameters mangled song names with special characters

**Symptom:** Stems failed to load for songs with titles containing parentheses. "Slipknot - Three Nil (Audio)" was truncated to "Slipknot - Three Nil " — everything from `(` onwards was lost.

**Root cause:** `encodeURIComponent` and `URLSearchParams` encoded and decoded the string inconsistently across the Python server and JavaScript client, silently dropping characters at `(`.

**Fix:** Replaced URL parameter passing with a `/api/song` JSON endpoint. The server returns `{"name": "...", "stems": [...]}` and the browser reads it via `fetch('/api/song')`. No encoding required.

---

## ffmpeg not found in subprocess PATH

**Symptom:** `extract_guitar.py` ran successfully from the terminal but failed with "ffmpeg not found" when triggered as a subprocess from `player.py`.

**Root cause:** ffmpeg was installed via winget, which adds it to the user's PATH in the Windows registry. The running Python process inherited its PATH at launch time — before winget updated it — so `shutil.which('ffmpeg')` returned `None` in the subprocess environment.

**Fix:** Added registry-based PATH lookup to `find_ffmpeg()`. It reads `HKEY_CURRENT_USER\Environment\Path` via `winreg` and searches for `ffmpeg.exe`. Falls back to searching `%LOCALAPPDATA%\Microsoft\WinGet\Packages\` recursively.

---

## Unicode encode error (UnicodeEncodeError: 'cp1252')

**Symptom:** Processing crashed with `UnicodeEncodeError: 'charmap' codec can't encode character '｜'` for a song title containing the fullwidth vertical bar `｜` (U+FF5C).

**Root cause:** Windows default console encoding is cp1252 (Windows-1252), which cannot represent characters outside the Latin-1 range. `subprocess.Popen` inherited this encoding for stdout/stderr pipes.

**Fix:** Set `PYTHONIOENCODING=utf-8` in the subprocess environment and `encoding='utf-8'` on the `Popen` call.

---

## HTTP 403 Forbidden from YouTube

**Symptom:** yt-dlp failed with `ERROR: unable to download video data: HTTP Error 403: Forbidden`. Log also showed: `No supported JavaScript runtime could be found. Only deno is enabled by default`.

**Root cause:** yt-dlp's modern YouTube extractor requires a JavaScript runtime (Deno by default) to resolve certain video formats. Without it, some format extraction paths fail and YouTube returns 403.

**Fix:** Installed Deno via `winget install DenoLand.Deno`. Added `find_deno()` to `extract_guitar.py` (same registry + WinGet search pattern as `find_ffmpeg()`). Prepends the Deno directory to `os.environ["PATH"]` before yt-dlp runs, so the subprocess can find `deno.exe` without requiring a shell restart.

---

## Tone.Player.buffer undefined after loadAllStems

**Symptom:** `Failed to load stems: Cannot read properties of undefined (reading 'buffer')` on initial load.

**Root cause:** `duration = Object.values(players)[0].buffer.duration` relied on `Tone.Player.buffer` returning a valid `ToneAudioBuffer`. In Tone.js 14.x, `.buffer` can be undefined when a `Player` is constructed directly from a `ToneAudioBuffer` instance rather than a URL, making this accessor unreliable.

**Fix:** Capture `audioBuffer.duration` directly from the raw `AudioBuffer` returned by `decodeAudioData()` inside the `Promise.all` callback, before wrapping it in `ToneAudioBuffer`. All 6 stems have identical duration so the last write wins safely.

---

## Orphaned player.py process served stale responses

**Symptom:** After switching stems from WAV to Opus, `/api/song` returned `"stems": []` even after restarting player.py and confirming the correct code was saved. New debug fields added to `_song_payload` did not appear in responses despite the file being updated.

**Root cause:** Claude had launched a `player.py` process internally via a tool call in a previous session. That process was still running in the background and handling most HTTP requests. When the user "restarted" the server, a second process started — but the old one kept answering the majority of requests. The old process still looked for `.wav` files, which no longer existed after Opus conversion.

**Fix:** `taskkill /f /im python.exe` to kill all Python processes, then restart manually. To detect this in future: the startup line `Serving from: <path>` now prints on startup — if two processes are running, one will silently absorb requests with no output visible in the active terminal.

---

## Seek bar fought user drag during playback

**Symptom:** Clicking or dragging the seek bar during playback was sluggish and unresponsive — the thumb would snap back toward the playing position while being dragged.

**Root cause:** The `tick()` loop runs on every animation frame (~60 fps) and calls `renderSeek()`, which sets `$seekBar.value` to the current playback position. This directly overwrote the native browser drag value faster than the user could move the slider.

**Fix:** Added an `isSeeking` flag. Set to `true` on `pointerdown`, cleared to `false` on `change` (mouse release). `renderSeek()` returns immediately when `isSeeking` is true, leaving the browser in full control of the slider during a drag.

---

## Detached ArrayBuffer when caching stems in IndexedDB

**Symptom:** `Init error: TypeError: Cannot perform ArrayBuffer.prototype.slice on a detached ArrayBuffer` after loading a new song.

**Root cause:** `decodeAudioData(arrayBuffer)` and `arrayBuffer.slice(0)` were evaluated in the same `Promise.all([...])` array. `decodeAudioData` detaches (transfers) its input buffer synchronously, so the `slice` ran on an already-detached buffer.

**Fix:** Copy the buffer for IndexedDB before calling `decodeAudioData`. Also made cache failures non-fatal (IndexedDB unavailable, read or write error falls back to plain fetch) and merged the duplicated cache-hit/miss player setup into one path.

---

## AMI snapshot captured empty files

**Symptom:** A worker instance launched from a freshly built AMI never started its service: `systemctl` reported `Unit learn-worker.service is masked`, and the SQS queue was never consumed. Nothing was logged.

**Root cause:** The AMI was created with `aws ec2 create-image --no-reboot` from a running builder moments after writing files. The snapshot caught the filesystem before file contents were flushed, so `run.sh` and the unit file existed but were 0 bytes (systemd treats an empty unit file as masked), and the last line written to `VERSIONS.txt` was missing.

**Fix:** Discarded the corrupt image and snapshot, rebuilt, ran `sync`, verified SHA-256 checksums against the local files, stopped the builder, and created the image from the stopped instance.

---

## CloudFormation stack for the domain failed twice

**Symptom 1:** `Certificate CREATE_FAILED ... invalid set of changes for a resource record set 'CNAME _dc95...daniel-vaz.com.'` and a rollback.

**Root cause 1:** The certificate covered the apex and the wildcard (`*.daniel-vaz.com`). ACM validates both with the same DNS record, but `DomainValidationOptions` listed both names with a hosted zone, so CloudFormation tried to create that record twice.

**Fix 1:** List only the apex in `DomainValidationOptions`.

**Symptom 2:** `Distribution CREATE_FAILED: The specified response headers policy does not exist.`

**Root cause 2:** The managed `SecurityHeadersPolicy` ID in the template had been recalled from memory and was wrong (`...5512b698d30f` instead of `...5512b31e9d03`).

**Fix 2:** Look managed policy IDs up with `aws cloudfront list-response-headers-policies --type managed` instead of typing them from memory.

**Related:** the first failed deploy looked successful because its output was piped through `tail`, which returns the exit code of `tail`, not of the script. Redirect output to a log file and check the script's own exit status.

---

## API CORS did not pick up a changed template default

**Symptom:** After changing the default of the `AllowedOrigins` parameter and redeploying, the API still answered preflight requests only for the old origin.

**Root cause:** `aws cloudformation deploy` keeps the previous value of any parameter that is not passed in `--parameter-overrides`; a new default in the template is ignored for an existing stack.

**Fix:** `infra/deploy.sh` passes `AllowedOrigins` explicitly.

---

## "Decoding failed" on iPad Safari

**Symptom:** after the "Done!" message the player showed a red decode error on an iPad; desktop Chrome worked.

**Root cause:** stems were Opus in Ogg, which Safari's `decodeAudioData` does not reliably support (see architecture.md, "Stems are AAC").

**Fix:** switched all stems to AAC in `.m4a`. Also fixed a second bug found while debugging: a stem was saved to IndexedDB before decoding, so an undecodable file stayed cached and every retry failed the same way. It is now saved only after a successful decode, and a bad cached copy is deleted.

---

## Deploying new worker code did not reach a running worker

**Symptom:** after uploading a worker that produced `.m4a`, requests still returned Opus stems.

**Root cause:** the worker fetched its code only at boot, and the instance had been running continuously, so it kept executing the old code in memory.

**Fix:** the worker now checks S3 for a newer `worker.py` between jobs and restarts into it (see architecture.md). Lesson: a deploy that only takes effect at boot is not a deploy; verify what is actually running (here, `grep` on the instance and the journal) before trusting a test result.

---

## Budget kill switch test: first attempt "failed"

**Symptom:** with the deny policy attached to the `/process` role, requests still returned 202 for over a minute, and a retry loop queued about ten duplicate jobs for one song before the queue cap stopped it.

**Root cause:** IAM changes are eventually consistent and the running Lambda kept succeeding until the change propagated (it took longer than about 70 s). The IAM simulator already showed an explicit deny, so the configuration was right; my test did not wait long enough. A second, self-inflicted problem was the test design: a loop that submits a real job on every pass produces real work.

**Fix / lesson:** retest with a single request after a few minutes (it returned the expected `503 paused`). When testing a block, do not use a retry loop that does the thing being blocked; poll a read-only signal (the IAM simulator's live decision) and send one request.
