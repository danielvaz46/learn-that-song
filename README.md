# Learn That Song

A local guitar learning tool. Paste a YouTube URL, and the app downloads the song, splits it into 6 instrument stems using AI source separation, and plays them in a browser with live pitch and tempo controls. Toggle any stem on/off, set loop regions, and slow down or transpose the song — all in real time.

## What it does

- Downloads audio from YouTube via yt-dlp
- Separates into 6 stems (guitar, bass, drums, vocals, piano, other) using Meta's Demucs `htdemucs_6s` model
- Stores stems as Opus files (~24 MB per song vs ~480 MB as WAV)
- Plays in a browser with:
  - Per-stem mute toggles
  - Tempo control (25–150%)
  - Pitch control (±12 semitones)
  - A/B loop regions
  - Seek bar

## Prerequisites

**ffmpeg**
```
winget install Gyan.FFmpeg
```

**Deno** (required by yt-dlp for modern YouTube extraction)
```
winget install DenoLand.Deno
```

**Python packages**
```
pip install yt-dlp demucs librosa soundfile
```

> The first time you process a song, Demucs downloads its model weights (~300 MB). Subsequent runs use the cached model.

## Usage

### Start the player

```
python player.py
```

Opens `http://localhost:8080/player.html` in your browser. Paste a YouTube URL into the input box and click Load. Processing takes roughly 2 minutes for a typical song on a modern laptop CPU.

### Process from the command line

```
python extract_guitar.py "https://youtube.com/watch?v=..."
```

Optional pitch/tempo adjustment (applied to the guitar stem before Opus conversion):
```
python extract_guitar.py "https://youtube.com/watch?v=..." --pitch -2 --tempo 0.8
```

### Load a specific song

```
python player.py --song "Song Title"
python player.py --port 9090
python player.py --no-browser
```

## Output

```
output/
  htdemucs_6s/
    Song Title/
      guitar.opus
      bass.opus
      drums.opus
      vocals.opus
      piano.opus
      other.opus
  downloads/
    Song Title.wav    (source audio, kept for reference)
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for decisions made during development.

## Bug log

See [docs/bugs.md](docs/bugs.md) for issues encountered and how they were fixed.
