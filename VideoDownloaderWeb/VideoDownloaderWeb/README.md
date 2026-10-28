# Video Downloader (web)

Flask + yt-dlp backend with a single-page front end.

## Run locally
    pip install -r requirements.txt
    python app.py
Open http://localhost:8000 (ffmpeg is picked up from your system, or from the
bundled imageio-ffmpeg package).

## Run with Docker
    docker build -t video-downloader .
    docker run -p 8000:8000 -e APP_PASSWORD=choose-a-password video-downloader

## Deploy
Any host that runs Docker works: Render, Railway, Fly.io, a VPS.
1. Push this folder to GitHub.
2. Create a new "Web Service" from the repo, choose the Dockerfile.
3. Set environment variables (below), deploy.

## Settings (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| APP_PASSWORD | (none) | Password-protect the whole site. Strongly recommended. |
| MAX_CONCURRENT | 2 | Simultaneous downloads |
| MAX_FILESIZE_MB | 2000 | Per-file cap |
| FILE_TTL_MIN | 30 | Minutes before finished files are deleted |
| PORT | 8000 | Listen port |

## Notes
- Files are stored temporarily on the server, then deleted.
- Job state is in memory, so run a single instance (the Dockerfile does).
- Some sites (YouTube especially) block requests from cloud/datacenter IPs,
  so downloads that work at home may fail on a hosted server.
- Keep yt-dlp current: rebuild/redeploy regularly.
- Hosting a public downloader can breach site terms and copyright law. Use a
  password and only for content you have the right to download.
