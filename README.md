# Klipped Studio

An independent AI video editor at `klippdstudio.com`. It runs as a React frontend plus a FastAPI/FFmpeg backend. The MVP can use its built-in single-user file store; MongoDB is optional.

## Local setup

1. Copy `backend/.env.example` to `backend/.env`. MongoDB and a custom Fernet key are optional; Klipped creates a local store and stable key automatically.
2. Copy `frontend/.env.example` to `frontend/.env` and set the backend URL.
3. Install FFmpeg, then run the backend with `uvicorn server:app --reload` from `backend/`.
4. Run `yarn start` from `frontend/`.

The Dockerfiles are production-ready: the backend image installs FFmpeg and includes the editing knowledge base, while the frontend image builds a static SPA. Build the backend from the repository root so both folders are in context:

```powershell
docker build -f backend/Dockerfile -t klipped-backend .
```

Set `REACT_APP_BACKEND_URL` to the public backend URL at build time, and set `CORS_ORIGINS=https://klippdstudio.com,https://www.klippdstudio.com` on the backend. For an ongoing paid deployment, mount a persistent volume at `/app/data`; it stores uploads, renders, the local single-user database, and its generated encryption key. MongoDB is optional.

Video files are stored on the backend volume, not in MongoDB. Uploads over 25 MB are automatically re-encoded as compact H.264 working files before editing; the original is discarded only when the compressed version saves at least 5%. Temporary transcription audio is deleted after analysis, final videos use compact H.264/AAC settings, and deleting a project removes its source, captions, main render, and generated clip renders.

## Plans and retention

The server assigns a project retention policy when it is uploaded and deletes the entire project plus its stored media after expiry. The current MVP is single-user, so `SUBSCRIPTION_PLAN` must be set by the billing/admin system rather than accepted from the browser.

| Plan | Price | Storage retention | Shipped value |
| --- | --- | --- | --- |
| Basic | $19/month | 7 days | AI transcript, filler removal, captions, B-roll selection, SFX, and watermark-free MP4 export. |
| Pro | $49/month | 30 days | Everything in Basic plus Creator DNA, edit-chat controls, and viral clip generation. |
| Elite | $149/month | No automatic deletion | Everything in Pro plus no automatic project deletion. Do not sell this tier until its planned premium workflow features ship. |
| Enterprise | Starts at $120/seat/month | No automatic deletion | Qualified organizations only. Do not sell until team controls, SSO, API/integrations, audit requirements, and dedicated support exist. |

Do not market an unimplemented item as available. The current code enforces retention only; Stripe billing, accounts, team roles, SSO, API access, brand templates, and priority queues require their own implementation before they can be sold.

The MVP API is single-user and has no accounts. Do not expose it publicly without a gate. `APP_ACCESS_TOKEN` enables an optional request token, but a static frontend cannot keep that token secret; Cloudflare Access or equivalent edge authentication is the recommended temporary protection for tomorrow's private test. Groq is the only required provider key for transcription and analysis. Configure it on the backend with `SEED_GROQ_KEY`; it is never entered or exposed in the frontend. Cerebras is an optional server-side text fallback and Pixabay is optional stock search.

`render.yaml` is the no-charge tomorrow-test blueprint. It requests Render's free plan and does not create a disk. Add the provider secrets in Render when prompted; no MongoDB service is required. Free-service storage is ephemeral, so uploads, renders, saved keys, and project history can disappear whenever the instance restarts or redeploys. Download the test result before then.

`render.persistent.example.yaml` is a separate, explicitly optional paid example with a starter service and 20 GB disk. Do not apply it unless ongoing persistence is approved.
