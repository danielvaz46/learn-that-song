"""
player.py - Serve the guitar practice web player.

Usage:
    python player.py [--song SONG_TITLE] [--port 8080] [--no-browser]

If --song is omitted, the most recently processed song is loaded automatically.
If no songs exist yet, the server still starts — use the URL input in the player to process one.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn

OUTPUT_DIR = Path("output") / "htdemucs_6s"
_song_dir: Path = None  # currently loaded song; updated when a new song finishes processing

# ── Job state (one job at a time) ─────────────────────────────────────────────
_job = {
    "running": False,
    "messages": [],
    "error": None,
    "done_song": None,
}
_job_lock = threading.Lock()

ANSI_RE = re.compile(r"\x1B[@-Z\\-_]|\x1B\[[\d;]*[ -/]*[@-~]")


def run_extraction(url: str):
    global _song_dir
    with _job_lock:
        _job["running"] = True
        _job["messages"] = []
        _job["error"] = None
        _job["done_song"] = None

    def log(msg: str):
        msg = ANSI_RE.sub("", msg).strip()
        if msg:
            with _job_lock:
                _job["messages"].append(msg)

    try:
        cmd = [sys.executable, "-u", "extract_guitar.py", url]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        for line in proc.stdout:
            log(line)
        proc.wait()

        if proc.returncode == 0:
            songs = find_songs()
            if songs:
                _song_dir = songs[0]
                with _job_lock:
                    _job["done_song"] = songs[0].name
            else:
                with _job_lock:
                    _job["error"] = "Processing finished but no output found."
        else:
            with _job_lock:
                _job["error"] = "Processing failed — check the terminal for details."
    except Exception as e:
        with _job_lock:
            _job["error"] = str(e)
    finally:
        with _job_lock:
            _job["running"] = False


class CORSHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/song":
            self._send_json(self._song_payload())

        elif self.path == "/api/progress":
            with _job_lock:
                payload = dict(_job)
            self._send_json(payload)

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/process":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            url = (body.get("url") or "").strip()

            if not url:
                self._send_json({"error": "Missing url"}, status=400)
                return

            with _job_lock:
                if _job["running"]:
                    self._send_json({"error": "Already processing"}, status=409)
                    return

            threading.Thread(target=run_extraction, args=(url,), daemon=True).start()
            self._send_json({"status": "started"})

        else:
            self.send_response(404)
            self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _send_json(self, data: dict, status: int = 200):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _song_payload(self) -> dict:
        if _song_dir is None or not _song_dir.exists():
            return {"name": None, "stems": []}
        stems = sorted(p.stem for p in _song_dir.iterdir() if p.suffix == ".opus")
        return {"name": _song_dir.name, "stems": stems}


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def find_songs():
    if not OUTPUT_DIR.exists():
        return []
    return sorted(
        [d for d in OUTPUT_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )


def main():
    global _song_dir

    parser = argparse.ArgumentParser(description="Serve the guitar practice player.")
    parser.add_argument("--song", help="Song folder name to load (default: most recent)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    songs = find_songs()

    if args.song:
        _song_dir = OUTPUT_DIR / args.song
        if not _song_dir.exists():
            print(f"Song '{args.song}' not found. Available songs:")
            for s in songs:
                print(f"  {s.name}")
            sys.exit(1)
    elif songs:
        _song_dir = songs[0]
        print(f"Loading: {_song_dir.name}")
    else:
        print("No processed songs found — paste a YouTube URL in the player to get started.")

    os.chdir(Path(__file__).parent)

    url = f"http://localhost:{args.port}/player.html"
    if not args.no_browser:
        webbrowser.open(url)

    print(f"Player: {url}")
    print(f"Serving from: {Path(__file__).resolve()}")
    print("Press Ctrl+C to stop.")

    server = ThreadedHTTPServer(("localhost", args.port), CORSHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
