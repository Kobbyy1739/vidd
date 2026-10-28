#!/usr/bin/env python3
"""
Video Downloader - web version (Flask + yt-dlp).

Run locally:
    pip install -r requirements.txt
    python app.py            ->  http://localhost:8000

Environment variables (all optional):
    PORT              port to listen on (default 8000)
    APP_PASSWORD      if set, the whole site asks for this password (user: anything)
    MAX_CONCURRENT    simultaneous downloads allowed (default 2)
    MAX_FILESIZE_MB   per-file size cap (default 2000)
    FILE_TTL_MIN      minutes to keep finished files before deletion (default 30)
    DATA_DIR          where temp files go (default ./downloads)

Only download content you own, that is public domain / Creative Commons,
or that the owner has given you permission to download.
"""
import hmac
import ipaddress
import os
import shutil
import socket
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

try:
    import yt_dlp
except ImportError:
    raise SystemExit("yt-dlp is not installed. Run:  pip install -r requirements.txt")

BASE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "downloads")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
MAX_FILESIZE = int(os.environ.get("MAX_FILESIZE_MB", "2000")) * 1024 * 1024
FILE_TTL = int(os.environ.get("FILE_TTL_MIN", "30")) * 60

app = Flask(__name__, static_folder=None)

FORMATS = {
    "best": {"format": "bv*+ba/b", "merge_output_format": "mp4"},
    "1080": {"format": "bv*[height<=1080]+ba/b[height<=1080]", "merge_output_format": "mp4"},
    "720": {"format": "bv*[height<=720]+ba/b[height<=720]", "merge_output_format": "mp4"},
    "480": {"format": "bv*[height<=480]+ba/b[height<=480]", "merge_output_format": "mp4"},
    "mp3": {
        "format": "bestaudio/best",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    },
}

JOBS = {}
LOCK = threading.Lock()


class Cancelled(Exception):
    pass


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


FFMPEG = find_ffmpeg()


# ---------- safety ----------
@app.before_request
def check_password():
    if not APP_PASSWORD:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", APP_PASSWORD):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Video Downloader"'})


def url_is_safe(url):
    """Only public http(s) hosts - stops the server being used to reach internal networks."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def active_jobs():
    return sum(1 for j in JOBS.values() if j["status"] in ("queued", "downloading", "processing"))


# ---------- worker ----------
def run_job(job_id, url, fmt, playlist):
    job = JOBS[job_id]
    folder = DATA_DIR / job_id

    def hook(d):
        if job["cancel"].is_set():
            raise Cancelled()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            job["pct"] = (done / total * 100) if total else 0
            info = d.get("info_dict") or {}
            item = ""
            if info.get("playlist_index") and info.get("n_entries"):
                item = f"[{info['playlist_index']}/{info['n_entries']}] "
            speed = (d.get("_speed_str") or "").strip()
            eta = (d.get("_eta_str") or "").strip()
            job["message"] = f"{item}Downloading... {job['pct']:.1f}%  {speed}  ETA {eta}"
        elif d["status"] == "finished":
            job["pct"] = 100
            job["status"] = "processing"
            job["message"] = "Download finished, processing..."

    opts = {
        "outtmpl": str(folder / "%(title).150B [%(id)s].%(ext)s"),
        "noplaylist": not playlist,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "windowsfilenames": True,
        "max_filesize": MAX_FILESIZE,
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    opts.update(FORMATS[fmt])

    try:
        folder.mkdir(parents=True, exist_ok=True)
        job["status"] = "downloading"
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        files = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix not in (".part", ".ytdl", ".temp")
        )
        if not files:
            raise RuntimeError("Nothing was downloaded (file may exceed the size limit).")
        if len(files) == 1:
            job["file"], job["name"] = files[0], files[0].name
        else:
            zpath = folder / "playlist.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
                for f in files:
                    z.write(f, f.name)
                    f.unlink()
            job["file"], job["name"] = zpath, "playlist.zip"
        job["status"], job["pct"], job["message"] = "done", 100, "Ready to download."
    except Cancelled:
        job["status"], job["message"] = "cancelled", "Cancelled."
        shutil.rmtree(folder, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        if job["cancel"].is_set():
            job["status"], job["message"] = "cancelled", "Cancelled."
        else:
            job["status"], job["message"] = "error", str(e)
        shutil.rmtree(folder, ignore_errors=True)


def cleaner():
    while True:
        time.sleep(60)
        now = time.time()
        with LOCK:
            old = [k for k, j in JOBS.items()
                   if j["status"] in ("done", "error", "cancelled") and now - j["created"] > FILE_TTL]
            for k in old:
                shutil.rmtree(DATA_DIR / k, ignore_errors=True)
                JOBS.pop(k, None)


threading.Thread(target=cleaner, daemon=True).start()


# ---------- routes ----------
@app.get("/")
def index():
    return send_from_directory(BASE / "static", "index.html")


@app.post("/api/download")
def start():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = data.get("format", "best")
    playlist = bool(data.get("playlist"))
    if not url:
        return jsonify(error="Please paste a video URL first."), 400
    if fmt not in FORMATS:
        return jsonify(error="Unknown quality option."), 400
    if not url_is_safe(url):
        return jsonify(error="That URL isn't allowed (must be a public http/https link)."), 400
    with LOCK:
        if active_jobs() >= MAX_CONCURRENT:
            return jsonify(error="Server is busy. Try again in a minute."), 429
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            "status": "queued", "pct": 0, "message": "Starting...",
            "file": None, "name": None, "created": time.time(), "cancel": threading.Event(),
        }
    threading.Thread(target=run_job, args=(job_id, url, fmt, playlist), daemon=True).start()
    return jsonify(id=job_id)


@app.get("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Job not found (it may have expired)."), 404
    return jsonify(status=job["status"], pct=round(job["pct"], 1),
                   message=job["message"], name=job["name"])


@app.post("/api/cancel/<job_id>")
def cancel(job_id):
    job = JOBS.get(job_id)
    if job:
        job["cancel"].set()
    return jsonify(ok=True)


@app.get("/api/file/<job_id>")
def get_file(job_id):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done" or not job["file"] or not Path(job["file"]).exists():
        abort(404)
    return send_file(job["file"], as_attachment=True, download_name=job["name"])


@app.get("/healthz")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), threaded=True)
