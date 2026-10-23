# AutoTube AI — flat GitHub + Docker edition

All project files live in the repository root. No subdirectories need to be uploaded.

## GitHub
Upload every file in this directory to the **root** of your GitHub repository, including `main.py`, `index.html`, `Dockerfile`, `compose.yaml`, and `requirements.txt`.

## Docker
Create a local `.env` file based on `.env.example` (never commit `.env`). Configure at minimum `APP_SECRET` (32+ characters) and `ADMIN_PASSWORD` (12+ characters).

```bash
docker compose up -d --build
```

Visit http://localhost:8080.

## Hosting
For Dockerfile-based deployment, use port 8080 and set environment variables in the hosting dashboard. Mount persistent storage at `/data` for SQLite and videos. Set `PUBLIC_URL` to your real HTTPS site URL. For YouTube OAuth, register the callback URI `<PUBLIC_URL>/api/google/callback` in Google Cloud and configure OAuth credentials. Ensure your host supports a persistent background process and volume.

## Limitations
The project includes backend routes and video-job processing, but actual provider functionality requires valid credentials and correct deAPI endpoints compatible with the implemented payloads. Provider API contracts and Hostless deployment have not been verified here. Video character/environment continuity is best-effort, not guaranteed. Do not upload API keys or `.env` to GitHub.
