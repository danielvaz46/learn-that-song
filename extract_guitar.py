"""
extract_guitar.py - Download a YouTube song, extract its guitar stem, and apply pitch/tempo adjustments.

Usage:
    python extract_guitar.py <youtube_url> [options]

Options:
    --output-dir, -o    Directory for all output files (default: ./output)
    --pitch, -p         Pitch shift in semitones (e.g. -2 = two semitones down, 1 = one up)
    --tempo, -t         Tempo multiplier (e.g. 0.75 = 75% speed, 1.5 = 150% speed)

Examples:
    python extract_guitar.py "https://youtube.com/watch?v=..." --pitch -2 --tempo 0.8
    python extract_guitar.py "https://youtube.com/watch?v=..." --tempo 0.75

Output:
    output/htdemucs_6s/<track_name>/guitar.wav          (raw stem)
    output/htdemucs_6s/<track_name>/guitar_adjusted.wav (after pitch/tempo, if requested)
"""

import argparse
import subprocess
import sys
from pathlib import Path


def find_deno() -> str | None:
    """Return the deno executable path if found, else None."""
    import shutil

    if shutil.which("deno"):
        return shutil.which("deno")

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            user_path, _ = winreg.QueryValueEx(key, "Path")
        for directory in user_path.split(";"):
            candidate = Path(directory.strip()) / "deno.exe"
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass

    winget_packages = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_packages.exists():
        for exe in winget_packages.rglob("deno.exe"):
            return str(exe)

    return None


def find_ffmpeg() -> str:
    """Return the ffmpeg executable path, trying several locations."""
    import shutil

    # 1. Already on the process PATH
    if shutil.which("ffmpeg"):
        return "ffmpeg"

    # 2. Read the user's PATH from the registry (winget updates this but the
    #    running process won't see it until a new shell is opened)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            user_path, _ = winreg.QueryValueEx(key, "Path")
        for directory in user_path.split(";"):
            candidate = Path(directory.strip()) / "ffmpeg.exe"
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass

    # 3. Search the WinGet packages directory (covers any version/hash)
    winget_packages = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_packages.exists():
        for exe in winget_packages.rglob("ffmpeg.exe"):
            if exe.parent.name == "bin":
                return str(exe)

    raise EnvironmentError(
        "ffmpeg not found. Install it with: winget install Gyan.FFmpeg"
    )


def download_audio(url: str, out_dir: Path, ffmpeg_path: str) -> Path:
    """Download the best audio stream from a YouTube URL as a WAV file."""
    import os
    import yt_dlp

    deno_path = find_deno()
    if deno_path:
        deno_dir = str(Path(deno_path).parent)
        if deno_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = deno_dir + os.pathsep + os.environ.get("PATH", "")

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "%(title)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "ffmpeg_location": str(Path(ffmpeg_path).parent) if ffmpeg_path != "ffmpeg" else None,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
        "quiet": False,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    wav_path = Path(ydl.prepare_filename(info)).with_suffix(".wav")
    if not wav_path.exists():
        raise FileNotFoundError(f"Expected WAV at {wav_path} — download may have failed.")
    return wav_path


def separate_guitar(audio_path: Path, out_dir: Path) -> Path:
    """Run Demucs htdemucs_6s and return the path to the guitar stem."""
    cmd = [
        sys.executable, "-m", "demucs",
        "-n", "htdemucs_6s",
        "--out", str(out_dir),
        str(audio_path),
    ]

    print("\nRunning Demucs — first run downloads the model (~300 MB)...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError("Demucs separation failed.")

    guitar_stem = out_dir / "htdemucs_6s" / audio_path.stem / "guitar.wav"
    if not guitar_stem.exists():
        raise FileNotFoundError(f"Guitar stem not found at: {guitar_stem}")
    return guitar_stem


def convert_to_opus(stem_dir: Path, ffmpeg_path: str) -> list[Path]:
    """Convert all WAV stems in a directory to Opus 128kbps, removing the originals."""
    wav_files = sorted(stem_dir.glob("*.wav"))
    for wav in wav_files:
        opus = wav.with_suffix(".opus")
        cmd = [ffmpeg_path, "-i", str(wav), "-c:a", "libopus", "-b:a", "128k", "-y", str(opus)]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"Opus conversion failed for {wav.name}: {result.stderr.decode()}")
        wav.unlink()
    return sorted(stem_dir.glob("*.opus"))


def adjust_audio(input_path: Path, pitch_semitones: float, tempo_rate: float) -> Path:
    """
    Apply pitch shift and/or tempo change to a WAV file.
    pitch_semitones: positive = higher, negative = lower
    tempo_rate: < 1.0 = slower, > 1.0 = faster (pitch is preserved)
    """
    import librosa
    import soundfile as sf

    print(f"\nLoading audio for adjustment...")
    y, sr = librosa.load(str(input_path), sr=None, mono=False)

    # librosa.load returns mono by default; preserve stereo if present
    if y.ndim == 1:
        if pitch_semitones != 0:
            print(f"  Shifting pitch by {pitch_semitones:+.1f} semitones...")
            y = librosa.effects.pitch_shift(y, sr=sr, n_steps=pitch_semitones)
        if tempo_rate != 1.0:
            print(f"  Adjusting tempo to {tempo_rate * 100:.0f}%...")
            y = librosa.effects.time_stretch(y, rate=tempo_rate)
    else:
        # Process each channel independently to preserve stereo
        channels = []
        for ch in range(y.shape[0]):
            ch_audio = y[ch]
            if pitch_semitones != 0:
                ch_audio = librosa.effects.pitch_shift(ch_audio, sr=sr, n_steps=pitch_semitones)
            if tempo_rate != 1.0:
                ch_audio = librosa.effects.time_stretch(ch_audio, rate=tempo_rate)
            channels.append(ch_audio)
        import numpy as np
        y = np.stack(channels, axis=0)

    output_path = input_path.parent / "guitar_adjusted.wav"
    # soundfile expects (samples, channels) for stereo
    if y.ndim > 1:
        sf.write(str(output_path), y.T, sr)
    else:
        sf.write(str(output_path), y, sr)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Extract and adjust guitar stem from a YouTube URL.")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--output-dir", "-o", default="output", help="Output directory (default: ./output)")
    parser.add_argument("--pitch", "-p", type=float, default=0.0,
                        help="Pitch shift in semitones (e.g. -2, 0, 1.5)")
    parser.add_argument("--tempo", "-t", type=float, default=1.0,
                        help="Tempo multiplier (e.g. 0.75 = 75%% speed, default: 1.0)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    ffmpeg_path = find_ffmpeg()

    needs_adjust = args.pitch != 0.0 or args.tempo != 1.0
    total_steps = 4 if needs_adjust else 3

    print(f"[1/{total_steps}] Downloading audio...")
    audio_path = download_audio(args.url, out_dir / "downloads", ffmpeg_path)
    print(f"      Saved: {audio_path}")

    print(f"\n[2/{total_steps}] Separating stems with Demucs...")
    guitar_path = separate_guitar(audio_path, out_dir)
    print(f"      Guitar stem: {guitar_path}")

    if needs_adjust:
        print(f"\n[3/{total_steps}] Adjusting guitar stem...")
        adjust_audio(guitar_path, args.pitch, args.tempo)

    print(f"\n[{total_steps}/{total_steps}] Converting stems to Opus...")
    opus_stems = convert_to_opus(guitar_path.parent, ffmpeg_path)
    total_mb = sum(p.stat().st_size for p in opus_stems) / 1_048_576
    print(f"      {len(opus_stems)} stems → {total_mb:.1f} MB total")
    for p in opus_stems:
        print(f"        {p.name}  ({p.stat().st_size / 1_048_576:.1f} MB)")

    print(f"\nDone! Stems saved to: {guitar_path.parent}")


if __name__ == "__main__":
    main()
